"""Shared process gateway; never patches Python's global subprocess module.

All project launches use the same evidence lifecycle. Long-lived applications,
serial monitors, browsers and AI sessions have no arbitrary short deadline.
Only autonomous invocations are refused after repeated identical failures;
explicit human retries are allowed and diagnosed.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import re
import subprocess as _stdlib
import threading
import time

from modules.execution_lessons import ExecutionLessons, _command, _sanitize
from modules.execution_policy import context_fingerprint
from modules.project_test_runner import _stop_process_tree
from modules.workspace_paths import resolved_path


_scope = ContextVar("merotec_process_scope", default={})
_active_workspace = None
_POPEN_CLASS = _stdlib.Popen


class RepeatedExecutionError(RuntimeError):
    pass


def set_active_workspace(workspace):
    global _active_workspace
    candidate = resolved_path(workspace)
    if candidate.is_dir():
        _active_workspace = candidate


@contextmanager
def process_scope(**options):
    token = _scope.set({**_scope.get(), **options})
    try:
        yield
    finally:
        _scope.reset(token)


def operation_profile(command):
    text = _command(command).lower().replace("\\", "/")
    if re.search(r"\b(?:app-server|browser_runtime|web_chat_runtime|http\.server|serve|serial-monitor|monitor)\b", text):
        return "service", None
    if re.search(r"\bcodex(?:\.exe|\.cmd)?[\"']?\s+exec\b", text):
        return "ai-session", 1800
    if re.search(r"\b(?:deploy|publish|push|release|upload|flash)\b", text):
        return "deploy", 900
    if re.search(r"\b(?:install|download|update|upgrade|fetch|clone|pull)\b", text):
        return "dependencies", 900
    if re.search(r"\b(?:unittest|pytest|test|ctest)\b", text):
        return "test", 120
    if re.search(r"\b(?:git(?:\.exe)?[\"']?\s+(?:status|diff|rev-parse|ls-remote)|get-command|test-path|which|where|ping)\b|(?:^|\s)--(?:version|help)\b", text):
        return "diagnostic", 60
    if re.search(r"\bgit(?:\.exe)?[\"']?(?=\s|$)", text):
        return "git", 180
    if re.search(r"\b(?:build|compile|compileall|check|avr-gcc|avr-g\+\+|cmake|gcc|g\+\+|javac|tsc|msbuild)\b", text):
        return "build", 600
    if re.search(r"\b(?:mkdir|new-item|init|create|generate|scaffold)\b", text):
        return "create", 300
    # GUI apps, scripts and persistent programs can intentionally run indefinitely.
    return "run", None


def execution_context(workspace, cwd=None):
    root = resolved_path(workspace)
    fingerprint = context_fingerprint(root)
    working = resolved_path(cwd or root)
    if working != root:
        fingerprint += ":cwd=" + hashlib.sha256(str(working).encode()).hexdigest()[:12]
    return fingerprint


def failure_diagnostic(output, *, timed_out=False, cancelled=False, launch_error=False):
    """Suggestions from observed symptoms, never a claim of a confirmed root cause."""
    if timed_out:
        return "Timeout observado; causa não confirmada. Localize a etapa, confira processos filhos, ambiente, permissões e rede antes de repetir."
    if cancelled:
        return "Cancelado; execução não aprovada. Não retome automaticamente uma operação com efeitos externos."
    if launch_error:
        return "Falha ao iniciar. Confira executável, PATH, cwd, interpretador e permissões."
    text = str(output or "").lower()
    rules = (
        (r"permission denied|access.*denied|acesso.*negado|eacces|eperm|sandbox", "Indício de restrição de acesso: confira caminho e permissões antes de alterar o código."),
        (r"401|403|unauthorized|authentication failed|could not read username|invalid.*token", "Indício de autenticação/autorização: confira sessão, conta e destino; não repita deploy/push às cegas."),
        (r"non-fast-forward|merge conflict|conflict.*merge|unmerged", "Indício de divergência Git: inspecione status e diferenças antes de tentar novamente."),
        (r"modulenotfound|no module named|not recognized|não.*reconhecido|command not found|enoent", "Indício de ferramenta/dependência ausente: confira interpretador, ambiente e PATH."),
        (r"syntaxerror|indentationerror|taberror|error c\d+|compilation failed|error:.*expected", "Indício de falha de código/compilação: leia o arquivo e a linha apontados; valide a correção antes de repetir."),
        (r"could not resolve|connection refused|network.*unreachable|timed out|timeout|econn", "Indício de rede/serviço indisponível: confira destino e conectividade antes de repetir."),
        (r"address already in use|eaddrinuse|port.*busy", "Indício de porta ocupada: identifique o processo dono antes de alterar a configuração."),
    )
    for pattern, recommendation in rules:
        if re.search(pattern, text):
            return recommendation
    return "Falha observada; confira saída, cwd, ferramenta e etapa antes de atribuir uma causa."


class ProcessObservation:
    def __init__(self, command, workspace=None, *, cwd=None, origin="process", autonomous=False, operation=None, timeout="auto", environment=None):
        options = _scope.get()
        inherited = os.environ.get("MEROTEC_EXECUTION_WORKSPACE")
        selected = workspace or options.get("workspace")
        if selected is None:
            candidates = [inherited]
            if cwd and _active_workspace:
                try:
                    resolved_path(cwd).relative_to(resolved_path(_active_workspace))
                except ValueError:
                    candidates.append(cwd)
            candidates.extend((_active_workspace, cwd, Path.cwd()))
            selected = next((candidate for candidate in candidates if candidate and Path(candidate).is_dir()), Path.cwd())
        self.workspace = resolved_path(selected)
        self.cwd = resolved_path(cwd or self.workspace)
        self._secrets = [str(value) for key, value in {**os.environ, **(environment or {})}.items()
                         if value and len(str(value)) >= 6 and re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", str(key), re.I)]
        def scrub(value):
            value = str(value)
            for secret in self._secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        self.command = [scrub(arg) for arg in command] if isinstance(command, (list, tuple)) else scrub(command)
        profile, deadline = operation_profile(command)
        self.operation = operation or options.get("operation") or profile
        self.timeout = options.get("timeout", deadline) if timeout == "auto" else timeout
        self.origin = options.get("origin", origin)
        self.autonomous = options.get("autonomous", autonomous)
        self.context = execution_context(self.workspace, self.cwd)
        self.started_at = time.monotonic()
        self.output = ""
        self.timed_out = self.cancelled = self.finished = False
        self.result = None
        self._lock = threading.RLock()

    def assess(self):
        try:
            return ExecutionLessons(self.workspace).assess_repeat(self.command, context=self.context)
        except (OSError, ValueError):
            return {"should_pause": False, "recommendation": "Memória indisponível; use o resultado observado."}

    def check(self):
        assessment = self.assess()
        if self.autonomous and assessment["should_pause"]:
            raise RepeatedExecutionError(assessment["recommendation"])
        return assessment

    def feed(self, output):
        if output:
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            with self._lock:
                self.output = (self.output + str(output))[-12000:]

    def finish(self, code=None, *, crashed=False, cancelled=False, timed_out=False, launch_error=False, outcome=None):
        with self._lock:
            if self.finished:
                return self.result
            if type(code) is not int and not (launch_error or cancelled or timed_out) and outcome not in {"applied", "rejected", "cancelled"}:
                return None
            self.finished = True
            self.cancelled |= cancelled
            self.timed_out |= timed_out
            if self.cancelled:
                outcome = "cancelled"
            elif launch_error:
                outcome = "launch_error"
            if crashed and code == 0:
                code = None
                outcome = outcome or "cancelled"
            output = self.output
            # Do not retain environment credential values even without a prefix.
            for value in self._secrets:
                output = output.replace(value, "[REDACTED]")
            diagnosis = "" if outcome == "applied" or (code == 0 and not outcome and not self.timed_out) else failure_diagnostic(
                output, timed_out=self.timed_out, cancelled=self.cancelled, launch_error=launch_error)
            try:
                self.result = ExecutionLessons(self.workspace).record(
                    self.command, exit_code=code, timed_out=self.timed_out, outcome=outcome,
                    strategy=getattr(self, "strategy", "process:" + self.operation), evidence=output, context=self.context,
                    operation=self.operation, origin=self.origin, cwd=str(self.cwd),
                    duration_seconds=time.monotonic() - self.started_at, diagnosis=diagnosis)
            except (OSError, ValueError):
                self.result = None
            return self.result


class _OutputTap:
    def __init__(self, stream, observation):
        self._stream, self._observation = stream, observation
    def __getattr__(self, name): return getattr(self._stream, name)
    def read(self, *args):
        value = self._stream.read(*args); self._observation.feed(value); return value
    def readline(self, *args):
        value = self._stream.readline(*args); self._observation.feed(value); return value
    def __iter__(self): return self
    def __next__(self):
        value = next(self._stream); self._observation.feed(value); return value
    def __enter__(self): return self
    def __exit__(self, *args): return self._stream.__exit__(*args)


class ObservedProcess:
    def __init__(self, process, observation):
        self._process, self.observation = process, observation
        self.stdout = _OutputTap(process.stdout, observation) if process.stdout is not None else None
        self.stderr = _OutputTap(process.stderr, observation) if process.stderr is not None else None
        if isinstance(process, _POPEN_CLASS):
            threading.Thread(target=self._watch, daemon=True).start()
    def __getattr__(self, name): return getattr(self._process, name)
    def __enter__(self): return self
    def __exit__(self, *args):
        try: return self._process.__exit__(*args)
        finally: self.observation.finish(self._process.returncode)
    def _watch(self):
        try:
            code = self._process.wait(timeout=self.observation.timeout)
        except _stdlib.TimeoutExpired:
            self.observation.timed_out = True
            _stop_process_tree(self._process)
            code = self._process.returncode
        except (OSError, ValueError):
            return
        # Give the owner's stdout reader a chance to provide the final diagnostic.
        threading.Event().wait(.05)
        self.observation.finish(code)
    def wait(self, *args, **kwargs):
        code = self._process.wait(*args, **kwargs)
        self.observation.finish(code)
        if self.observation.timed_out and self.observation.timeout is not None:
            raise _stdlib.TimeoutExpired(self.args, self.observation.timeout, output=self.observation.output)
        return code
    def communicate(self, *args, **kwargs):
        output = self._process.communicate(*args, **kwargs)
        for value in output: self.observation.feed(value)
        self.observation.finish(self._process.returncode)
        if self.observation.timed_out and self.observation.timeout is not None:
            raise _stdlib.TimeoutExpired(self.args, self.observation.timeout, output=output[0], stderr=output[1])
        return output
    def terminate(self):
        self.observation.cancelled = True
        _stop_process_tree(self._process)
    def kill(self): self.terminate()
    def send_signal(self, sig):
        self.observation.cancelled = True
        return self._process.send_signal(sig)


class ProcessGateway:
    """subprocess-compatible facade with evidence and owned-process deadlines."""
    def __getattr__(self, name): return getattr(_stdlib, name)

    def Popen(self, args, *positional, **kwargs):
        observation = ProcessObservation(args, cwd=kwargs.get("cwd"), environment=kwargs.get("env"))
        observation.check()
        env = dict(kwargs.get("env") if kwargs.get("env") is not None else os.environ)
        env["MEROTEC_EXECUTION_WORKSPACE"] = str(observation.workspace)
        kwargs["env"] = env
        if os.name == "nt" and not positional:
            kwargs.setdefault("creationflags", _stdlib.CREATE_NO_WINDOW)
        elif not positional:
            kwargs.setdefault("start_new_session", True)
        try:
            process = _stdlib.Popen(args, *positional, **kwargs)
        except OSError as exc:
            observation.feed(type(exc).__name__)
            observation.finish(launch_error=True)
            raise
        return ObservedProcess(process, observation)

    def run(self, *popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
        if timeout is None:
            command = popenargs[0] if popenargs else kwargs.get("args", [])
            timeout = _scope.get().get("timeout", operation_profile(command)[1])
        if input is not None:
            if kwargs.get("stdin") is not None:
                raise ValueError("stdin and input arguments may not both be used.")
            kwargs["stdin"] = _stdlib.PIPE
        if capture_output:
            if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
                raise ValueError("stdout and stderr arguments may not be used with capture_output.")
            kwargs.update(stdout=_stdlib.PIPE, stderr=_stdlib.PIPE)
        # The caller's explicit deadline takes precedence over operation defaults.
        with process_scope(timeout=None):
            with self.Popen(*popenargs, **kwargs) as process:
                try:
                    stdout, stderr = process.communicate(input, timeout=timeout)
                except _stdlib.TimeoutExpired as exc:
                    process.observation.timed_out = True
                    _stop_process_tree(process._process)
                    stdout, stderr = process.communicate()
                    raise _stdlib.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr) from exc
                except BaseException:
                    process.kill()
                    raise
                code = process.poll()
                if check and code:
                    raise _stdlib.CalledProcessError(code, process.args, output=stdout, stderr=stderr)
                return _stdlib.CompletedProcess(process.args, code, stdout, stderr)

    def call(self, *args, timeout=None, **kwargs):
        return self.run(*args, timeout=timeout, **kwargs).returncode

    def check_call(self, *args, **kwargs):
        self.run(*args, check=True, **kwargs)
        return 0

    def check_output(self, *args, **kwargs):
        if "stdout" in kwargs:
            raise ValueError("stdout argument not allowed; it will be overridden.")
        return self.run(*args, stdout=_stdlib.PIPE, check=True, **kwargs).stdout


subprocess = ProcessGateway()

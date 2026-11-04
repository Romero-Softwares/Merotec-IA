"""Evidence and child-process preparation shared by the IDE's agent backends."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

from modules.execution_lessons import ExecutionLessons
from modules.project_test_runner import DEFAULT_TIMEOUT, RUNTIME_RELATIVE


STRATEGY = "workspace_test_runtime"
_IGNORED = {".git", ".merotec_system_ai", "venv", ".venv", "env", "node_modules",
            "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"}
_SOURCES = {".py", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml", ".txt",
            ".js", ".ts", ".c", ".cpp", ".h", ".ino", ".rs", ".go", ".java", ".cs",
            ".dart", ".kt", ".kts", ".gradle", ".sh", ".bat", ".ps1", ".html", ".css",
            ".xml", ".vue", ".svelte", ".lock", ".md", ".sql", ".rb", ".php", ".swift",
            ".cc", ".cxx", ".hpp", ".cmake", ".v", ".sv", ".vhd", ".vhdl", ".kicad_pcb", ".kicad_sch", ".merotec_pcb"}


def simple_argv(command):
    """Parse a literal invocation only; shell expressions must keep shell semantics.

    In particular do not reinterpret pipelines, substitutions, environment
    assignments or PowerShell expressions as arguments to a test runner.
    """
    value = str(command or "").strip()
    if value.startswith("& "):
        value = value[2:].lstrip()
    if not value or any(char in value for char in ("\n", "\r", "\0", "`", "$", "%")):
        return None
    if os.name != "nt":
        try:
            lexer = shlex.shlex(value, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            lexer.commenters = ""
            parts = list(lexer)
            return None if any(re.fullmatch(r"[;&|<>()]+", part) for part in parts) else parts
        except ValueError:
            return None
    parts, current, quote, started = [], [], "", False
    for char in value:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            started = True
        elif char in "\"'":
            quote, started = char, True
        elif char in ";&|<>(){}#":
            return None
        elif char.isspace():
            if started:
                parts.append("".join(current))
                current, started = [], False
        else:
            current.append(char)
            started = True
    if quote:
        return None
    if started:
        parts.append("".join(current))
    return parts or None


def _is_python_test(argv):
    if not argv:
        return False
    name = Path(argv[0]).name.lower()
    if name in {"pytest", "pytest.exe", "py.test", "py.test.exe"}:
        return True
    if not re.fullmatch(r"(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?", name):
        return False
    args = argv[1:]
    # Only interpreter switches before -m; never convert python -c or scripts.
    while args and (args[0] in {"-u", "-B", "-E", "-I", "-s", "-S"}
                    or (name in {"py", "py.exe"} and re.fullmatch(r"-\d+(?:\.\d+)*", args[0]))):
        args = args[1:]
    return len(args) >= 2 and args[0] == "-m" and args[1] in {"unittest", "pytest"}


def project_python(workspace):
    root = Path(workspace).resolve()
    for relative in (".venv/Scripts/python.exe", "venv/Scripts/python.exe", ".venv/bin/python", "venv/bin/python"):
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return str(sys.executable) if not getattr(sys, "frozen", False) else (shutil.which("python") or "python")


def prepare_test_launch(command, workspace, timeout=DEFAULT_TIMEOUT):
    """Return a direct, isolated launch for recognized *simple* Python tests."""
    argv = simple_argv(command)
    if not _is_python_test(argv):
        return None
    root = Path(workspace).resolve()
    executable = Path(argv[0])
    if not executable.is_absolute() and ("/" in argv[0] or "\\" in argv[0]):
        candidate = (root / executable).resolve()
        if candidate.is_file():
            argv[0] = str(candidate)
    # A bare python means the project's environment, when one is available.
    elif argv[0].lower() in {"python", "python.exe"}:
        argv[0] = project_python(root)
    runner_python = project_python(root) if getattr(sys, "frozen", False) else sys.executable
    return {"program": str(runner_python), "arguments": [str(Path(__file__).with_name("project_test_runner.py")),
            "--workspace", str(root), "--timeout", str(timeout), "--", *argv],
            "strategy": STRATEGY, "test_argv": argv}


def context_fingerprint(workspace):
    """Bounded source/config/toolchain metadata; runtime logs do not reset retries."""
    root = Path(workspace).resolve()
    digest = hashlib.sha256(str(root).encode())
    count = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in _IGNORED
                                and not name.startswith(".merotec_") and not (Path(folder) / name).is_symlink())
        for name in sorted(files):
            path = Path(folder) / name
            if (path.suffix.lower() not in _SOURCES and name not in {"Dockerfile", "Makefile", "CMakeLists.txt"}) or path.is_symlink():
                continue
            try:
                stat = path.stat()
                digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
            except OSError:
                continue
            count += 1
            if count >= 4000:
                break
        if count >= 4000:
            break
    interpreter = Path(project_python(root))
    for path in (interpreter, interpreter.parent.parent / "pyvenv.cfg"):
        try:
            stat = path.stat()
            digest.update(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        except OSError:
            pass
    digest.update(f"{sys.platform}:{sys.version_info[:3]}".encode())
    git_dir = root / ".git"
    try:
        if git_dir.is_file():
            reference = git_dir.read_text(encoding="utf-8").strip()
            if reference.startswith("gitdir:"):
                git_dir = (root / reference[7:].strip()).resolve()
        for name in ("HEAD", "index", "config", "packed-refs", "FETCH_HEAD"):
            path = git_dir / name
            if path.is_file():
                stat = path.stat()
                digest.update(f"git/{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        digest.update(head.encode())
        if head.startswith("ref: "):
            ref = (git_dir / head[5:]).resolve()
            ref.relative_to(git_dir.resolve())
            digest.update(ref.read_bytes()[:256])
    except (OSError, ValueError):
        pass
    for key in ("PATH", "VIRTUAL_ENV", "PYTHONPATH", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"):
        digest.update(f"{key}={os.environ.get(key, '')}\n".encode())
    return digest.hexdigest()[:24]


def shell_command(argv):
    if os.name == "nt":
        return "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in argv)
    return shlex.join([str(arg) for arg in argv])


def execution_guidance(workspace):
    root = Path(workspace).resolve()
    recipe = "Confira o framework/configuração do projeto antes de escolher a suíte."
    if (root / "tests").is_dir():
        # This is an example, not a claim that every project uses unittest.
        launch = prepare_test_launch("python -m unittest discover -s tests -v", root)
        recipe += " Exemplo para unittest (adapte os argumentos ao projeto):\n" + shell_command([launch["program"], *launch["arguments"]])
    return (
        "EXECUÇÃO E DIAGNÓSTICO DO PROJETO:\n"
        "Antes de executar, confira cwd, interpretador, dependências e diretórios graváveis. "
        "Para testes Python use o executor isolado abaixo: AppData e temporários ficam no workspace apenas no processo de testes; "
        "o ambiente da IDE e a autenticação permanecem preservados.\n"
        "Demora/timeout não comprova defeito no cálculo, SciPy ou interface. Primeiro localize a etapa (import, setUp, teste, persistência) "
        "e verifique caminhos/permissões. Não altere código de produção sem evidência da causa. "
        "Após timeout, faça um diagnóstico limitado antes de repetir; não prolongue sucessivamente a mesma tentativa. "
        "Após duas falhas iguais no mesmo contexto, mude a estratégia ou obtenha evidência nova. "
        "Reutilize estratégias verificadas apenas após conferir contexto, ambiente e alterações.\n"
        "Esta política vale para deploy, Git, compilação, execução, instalação, criação e edição de código, em qualquer linguagem. "
        "Preserve credenciais, perfis e diretórios de autenticação em operações normais; isolamento de AppData é específico dos testes. "
        "Depois de falha/timeout de deploy, push, upload ou criação, confira o estado efetivo no destino antes de retentar; "
        "a operação pode ter sido aplicada parcialmente. Um processo iniciado não significa tarefa concluída, "
        "e uma edição aplicada não comprova funcionamento.\n"
        + recipe + "\nContexto atual: " + context_fingerprint(root) + "\n"
        + ExecutionLessons(root).format_for_agent()
    )


def record_observed_result(workspace, command, exit_code, *, succeeded=None, timed_out=False,
                           strategy="", output="", context=None):
    """Persist process evidence without allowing cancellation/crash to become success."""
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return None
    if succeeded is False and exit_code == 0:
        exit_code = None
    memory = ExecutionLessons(workspace)
    from modules.process_runtime import operation_profile, failure_diagnostic
    operation, _ = operation_profile(command)
    fingerprint = context if context is not None else context_fingerprint(workspace)
    entry = memory.record(command, exit_code=exit_code, timed_out=timed_out, strategy=strategy,
                          evidence=output, context=fingerprint, operation=operation, origin="observed-command",
                          diagnosis=failure_diagnostic(output, timed_out=timed_out) if exit_code != 0 or timed_out else "")
    return {"entry": entry, "assessment": memory.assess_repeat(command, context=fingerprint)}


def runner_report(workspace, argv, exit_code, started_at):
    """Accept only a fresh matching runner report, never an earlier successful run."""
    path = Path(workspace).resolve() / RUNTIME_RELATIVE / "latest_run.json"
    try:
        path.resolve().relative_to(Path(workspace).resolve())
        if path.stat().st_size > 64_000:
            return None
        report = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(report["started_at"]).astimezone(timezone.utc).timestamp()
        if (report.get("schema_version") == 1 and report.get("command") == argv
                and report.get("command_redacted") is False and stamp >= started_at
                and report.get("exit_code") == exit_code
                and Path(report.get("workspace", "")).resolve() == Path(workspace).resolve()):
            return report
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def record_codex_command_event(method, params, workspace, seen, started=None):
    """App-server command completion is evidence; text/deltas/final claims are not."""
    if method != "item/completed" or not isinstance(params, dict):
        return None
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "commandExecution":
        return None
    code, command, identifier = item.get("exitCode"), item.get("command"), item.get("id")
    if not isinstance(code, int) or isinstance(code, bool) or not isinstance(command, str) or not command.strip() or not isinstance(identifier, str) or not identifier:
        return None
    if identifier in seen:
        return None
    root = Path(workspace).resolve()
    if item.get("cwd") is not None and not isinstance(item["cwd"], str):
        return None
    cwd = Path(item.get("cwd") or root)
    cwd = (root / cwd).resolve() if not cwd.is_absolute() else cwd.resolve()
    try:
        cwd.relative_to(root)
    except ValueError:
        return None
    # A runner's explicit timeout marker is evidence of timeout, not of its cause.
    argv = simple_argv(command) or []
    strategy = "codex_process"
    report = None
    if str(Path(__file__).with_name("project_test_runner.py")) in argv and "--" in argv and started is not None:
        test_argv = argv[argv.index("--") + 1:]
        report = runner_report(root, test_argv, code, started)
        if report:
            strategy = STRATEGY
    from modules.process_runtime import execution_context
    fingerprint = execution_context(root, cwd)
    result = record_observed_result(root, command, code, output=item.get("aggregatedOutput", ""),
                                    succeeded=item.get("status") not in {"declined", "cancelled", "interrupted", "failed"},
                                    timed_out=bool(report and report.get("timed_out")), strategy=strategy, context=fingerprint)
    seen.add(identifier)
    return result


def record_codex_action_event(method, params, workspace, seen):
    if method != "item/completed" or not isinstance(params, dict):
        return None
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "fileChange" or item.get("status") not in {"completed", "failed", "declined"}:
        return None
    identifier, changes = item.get("id"), item.get("changes")
    if not isinstance(identifier, str) or identifier in seen or not isinstance(changes, list) or not changes:
        return None
    root = Path(workspace).resolve()
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), str):
            return None
        try:
            (root / change["path"]).resolve().relative_to(root)
        except ValueError:
            return None
    from modules.process_runtime import ProcessObservation
    digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
    observation = ProcessObservation("coding fileChange sha256=" + digest, root, origin="codex", operation="coding", timeout=None)
    observation.feed("Evento estruturado de alteração de arquivo; funcionamento ainda exige validação do projeto.")
    entry = observation.finish(outcome="applied" if item["status"] == "completed" else "rejected")
    seen.add(identifier)
    return {"entry": entry, "assessment": observation.assess()} if entry else None


def record_codex_cli_event(event, workspace, seen):
    """Translate Codex exec --json completion events into the common lifecycle."""
    if not isinstance(event, dict) or event.get("type") != "item.completed" or not isinstance(event.get("item"), dict):
        return None
    original = event["item"]
    if original.get("type") == "command_execution":
        item = {"id": original.get("id"), "type": "commandExecution", "command": original.get("command"),
                "cwd": original.get("cwd") or str(workspace), "status": original.get("status"),
                "exitCode": original.get("exit_code"), "aggregatedOutput": original.get("aggregated_output", "")}
        return record_codex_command_event("item/completed", {"item": item}, workspace, seen)
    if original.get("type") == "file_change":
        item = {**original, "type": "fileChange"}
        return record_codex_action_event("item/completed", {"item": item}, workspace, seen)
    return None

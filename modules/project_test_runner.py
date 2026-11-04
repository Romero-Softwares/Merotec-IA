"""Run project tests with workspace-local writable application/temp directories.

The environment overlay belongs only to the launched child. This module neither
changes the IDE/Codex environment nor treats a timeout as proof of a code defect.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

if __package__:
    from modules.json_store import atomic_write_json
    from modules.workspace_paths import resolved_path
else:
    from json_store import atomic_write_json
    from workspace_paths import resolved_path


RUNTIME_RELATIVE = Path(".merotec_system_ai") / "test_runtime"
DEFAULT_TIMEOUT = 120
ENVIRONMENT_DIRS = {
    "LOCALAPPDATA": "localappdata",
    "APPDATA": "appdata",
    "TEMP": "temp",
    "TMP": "temp",
    "TMPDIR": "temp",
    "XDG_CONFIG_HOME": "xdg_config",
    "XDG_CACHE_HOME": "xdg_cache",
    "XDG_DATA_HOME": "xdg_data",
}


def _workspace(value):
    root = resolved_path(value)
    if not root.is_dir():
        raise ValueError("A pasta do workspace não existe.")
    return root


def _contained(root, relative):
    path = root / relative
    try:
        resolved_path(path).relative_to(root)
    except ValueError as exc:
        raise ValueError("O diretório de testes aponta para fora do workspace.") from exc
    return path


def prepare_test_environment(workspace, parent_env=None):
    """Return a copied child environment and its non-secret writable overlay."""
    root = _workspace(workspace)
    env = dict(os.environ if parent_env is None else parent_env)
    overlay = {}
    prepared = set()
    for key, directory in ENVIRONMENT_DIRS.items():
        path = _contained(root, RUNTIME_RELATIVE / directory)
        if directory not in prepared:
            path.mkdir(parents=True, exist_ok=True)
            _contained(root, RUNTIME_RELATIVE / directory)
            # Probe the actual target rather than the global tempfile directory.
            descriptor, probe = tempfile.mkstemp(prefix=".write-probe-", dir=path)
            try:
                os.write(descriptor, b"ok")
            finally:
                os.close(descriptor)
                Path(probe).unlink(missing_ok=True)
            prepared.add(directory)
        overlay[key] = str(path.resolve())
    overlay.update({"PYTHONUNBUFFERED": "1", "PYTHONFAULTHANDLER": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(overlay)
    return env, overlay


def _redacted_command(command, environment):
    """Keep useful argv while avoiding common explicit credential forms."""
    secrets = [str(value) for name, value in environment.items()
               if value and len(str(value)) >= 6 and re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", name, re.I)]
    sensitive = re.compile(r"(?:api[_-]?key|token|secret|password|authorization|credential)", re.I)
    result, redact_next = [], False
    for arg in command:
        value = str(arg)
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        if sensitive.search(value) and value.startswith("-"):
            if "=" in value:
                value = value.split("=", 1)[0] + "=[REDACTED]"
            else:
                redact_next = True
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(Bearer\s+)[^\s'\"]+", r"\1[REDACTED]", value)
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,})\b", "[REDACTED]", value)
        result.append(value)
    if "-c" in command and Path(command[0]).name.lower().startswith(("python", "py.exe")):
        index = command.index("-c") + 1
        if index < len(result):
            digest = hashlib.sha256(command[index].encode()).hexdigest()[:12]
            result[index] = f"[inline code omitted; sha256={digest}]"
    return result


def _stop_process_tree(process):
    """Stop only the tree/group created for this runner, never unrelated jobs."""
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        # The leader may exit before children which ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def run_test_command(workspace, command_argv, timeout=DEFAULT_TIMEOUT, parent_env=None):
    """Execute argv without a shell, stream output, and return a bounded report.

    A zero exit code means this command succeeded; it does not independently
    establish test coverage or physical/UI behavior.
    """
    root = _workspace(workspace)
    if not isinstance(command_argv, (list, tuple)) or not command_argv or any(
            not isinstance(item, str) or "\0" in item for item in command_argv):
        raise ValueError("Forneça um argv não vazio, sem shell ou caracteres NUL.")
    if not command_argv[0].strip():
        raise ValueError("Informe o executável do teste.")
    command_argv = list(command_argv)
    # Windows executable lookup may occur before cwd is applied to the child.
    executable = Path(command_argv[0])
    if not executable.is_absolute() and ("/" in command_argv[0] or "\\" in command_argv[0]):
        candidate = (root / executable).resolve()
        if candidate.is_file():
            command_argv[0] = str(candidate)
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("O timeout deve ser um número finito positivo.") from exc
    env, overlay = prepare_test_environment(root, parent_env)
    report_path = _contained(root, RUNTIME_RELATIVE / "latest_run.json")
    start = time.monotonic()
    safe_command = _redacted_command(command_argv, env)
    report = {"schema_version": 1, "workspace": str(root),
              "started_at": datetime.now(timezone.utc).isoformat(),
              "command": safe_command, "command_redacted": safe_command != list(command_argv),
              "environment_overlay": overlay, "timeout_seconds": timeout,
              "timed_out": False, "exit_code": None, "status": "starting"}
    process = None
    try:
        process = subprocess.Popen(command_argv, cwd=root, env=env, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        report["pid"] = process.pid
        report["exit_code"] = process.wait(timeout=timeout)
        report["status"] = "passed" if process.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        report.update(status="timeout", timed_out=True, exit_code=124)
        _stop_process_tree(process)
    except KeyboardInterrupt:
        report.update(status="interrupted", exit_code=130)
        if process is not None:
            _stop_process_tree(process)
    except OSError as exc:
        if process is not None and process.poll() is None:
            _stop_process_tree(process)
        report.update(status="launch_error", exit_code=127,
                      error=f"Não foi possível iniciar o processo ({type(exc).__name__}).")
    finally:
        report["duration_seconds"] = round(time.monotonic() - start, 6)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _contained(root, RUNTIME_RELATIVE / "latest_run.json")
        if not atomic_write_json(report_path, report):
            raise OSError("Não foi possível gravar o relatório de execução dos testes.")
    report["report_path"] = str(report_path)
    return report


def main(argv=None):
    # QProcess reads UTF-8; the wrapper's own messages also need that encoding.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        report = run_test_command(args.workspace, command, args.timeout)
    except (ValueError, OSError) as exc:
        print(f"[Testes] Preparação não concluída: {exc}", flush=True)
        return 2
    if report["timed_out"]:
        print(f"\n[Testes] Limite de {args.timeout:g}s excedido. Processo interrompido; "
              "a causa permanece não confirmada. Consulte o relatório e o ambiente antes de repetir.", flush=True)
    else:
        print(f"\n[Testes] {report['status']}; código {report['exit_code']}; "
              f"{report['duration_seconds']:.2f}s.", flush=True)
    print(f"[Testes] Relatório: {report['report_path']}", flush=True)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

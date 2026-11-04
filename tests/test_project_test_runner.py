import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from modules import project_test_runner as runner


class ProjectTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_overlay_only_changes_child_environment_and_preserves_identity_paths(self):
        parent = dict(os.environ)
        parent.update({"HOME": "original-home", "USERPROFILE": "original-profile",
                       "CODEX_HOME": "original-codex", "OPENAI_API_KEY": "secret-example-key"})
        snapshot = dict(parent)
        env, overlay = runner.prepare_test_environment(self.root, parent)
        self.assertEqual(snapshot, parent)
        for key in ("HOME", "USERPROFILE", "CODEX_HOME", "PATH", "OPENAI_API_KEY"):
            self.assertEqual(parent.get(key), env.get(key))
            self.assertNotIn(key, overlay)
        for key in runner.ENVIRONMENT_DIRS:
            Path(env[key]).resolve().relative_to(self.root.resolve())
            self.assertTrue(Path(env[key]).is_dir())
        self.assertEqual("1", env["PYTHONFAULTHANDLER"])
        self.assertEqual("1", env["PYTHONUNBUFFERED"])

    def test_real_child_writes_appdata_and_temp_inside_project(self):
        before = {key: os.environ.get(key) for key in runner.ENVIRONMENT_DIRS}
        script = (
            "import os,tempfile,json,pathlib; "
            "p=pathlib.Path(os.environ['LOCALAPPDATA'])/'configuracoes.json'; "
            "p.write_text('ok',encoding='utf-8'); "
            "f=tempfile.NamedTemporaryFile(delete=False); f.write(b'probe'); f.close(); "
            "pathlib.Path('observed.json').write_text(json.dumps({'config':str(p),'temp':f.name}),encoding='utf-8')"
        )
        report = runner.run_test_command(self.root, [sys.executable, "-c", script], timeout=5)
        self.assertEqual(0, report["exit_code"])
        self.assertEqual("passed", report["status"])
        observed = json.loads((self.root / "observed.json").read_text(encoding="utf-8"))
        for value in observed.values():
            Path(value).resolve().relative_to(self.root.resolve())
        self.assertEqual("ok", Path(observed["config"]).read_text(encoding="utf-8"))
        self.assertEqual(before, {key: os.environ.get(key) for key in runner.ENVIRONMENT_DIRS})
        saved = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report["environment_overlay"], saved["environment_overlay"])
        self.assertFalse(saved["timed_out"])

    def test_cli_streams_stdout_stderr_and_preserves_child_exit_code(self):
        command = [sys.executable, str(Path(runner.__file__).resolve()), "--workspace", str(self.root),
                   "--timeout", "5", "--", sys.executable, "-c",
                   "import sys; print('stdout-test'); print('stderr-test',file=sys.stderr); raise SystemExit(7)"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
        self.assertEqual(7, result.returncode, result.stderr)
        self.assertIn("stdout-test", result.stdout)
        self.assertIn("stderr-test", result.stdout)
        saved = json.loads((self.root / runner.RUNTIME_RELATIVE / "latest_run.json").read_text(encoding="utf-8"))
        self.assertEqual(7, saved["exit_code"])
        self.assertEqual("failed", saved["status"])

    def test_timeout_is_bounded_and_does_not_claim_code_or_environment_cause(self):
        start = time.monotonic()
        report = runner.run_test_command(self.root, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=.15)
        self.assertEqual(124, report["exit_code"])
        self.assertEqual("timeout", report["status"])
        self.assertTrue(report["timed_out"])
        self.assertLess(time.monotonic() - start, 8)
        self.assertNotIn("cause", report)

    def test_launch_error_is_reported_without_raw_environment(self):
        report = runner.run_test_command(self.root, [str(self.root / "missing-executable")], timeout=1)
        self.assertEqual("launch_error", report["status"])
        self.assertEqual(127, report["exit_code"])
        self.assertNotIn("environment", report)

    def test_command_report_redacts_explicit_and_environment_credentials(self):
        env = dict(os.environ, OPENAI_API_KEY="a-very-private-example-key")
        report = runner.run_test_command(self.root, [sys.executable, "-c", "pass", "--api-key", "explicit-secret",
                                                   "--token=embedded-secret", "a-very-private-example-key"], parent_env=env)
        saved = Path(report["report_path"]).read_text(encoding="utf-8")
        for secret in ("a-very-private-example-key", "explicit-secret", "embedded-secret"):
            self.assertNotIn(secret, saved)
        self.assertIn("[REDACTED]", saved)
        self.assertTrue(report["command_redacted"])

    def test_timeout_stops_grandchild_in_the_owned_process_tree(self):
        started = self.root / "child-started"
        marker = self.root / "must-not-be-written"
        child = f"import pathlib,time; pathlib.Path({str(started)!r}).write_text('started'); time.sleep(2); pathlib.Path({str(marker)!r}).write_text('orphan')"
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        report = runner.run_test_command(self.root, [sys.executable, "-c", parent], timeout=1)
        self.assertTrue(started.exists(), "O processo filho de teste não chegou a iniciar")
        self.assertEqual(124, report["exit_code"])
        time.sleep(1.5)
        self.assertFalse(marker.exists(), "O processo neto continuou após interromper os testes")

    def test_invalid_commands_timeouts_and_workspace_are_rejected(self):
        for value in ("python -m unittest", [], [""], ["bad\0program"], [3]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run_test_command(self.root, value)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run_test_command(self.root, [sys.executable], timeout=value)
        with self.assertRaises(ValueError):
            runner.prepare_test_environment(self.root / "missing")

    def test_escaping_runtime_link_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / ".merotec_system_ai").mkdir()
            link = self.root / runner.RUNTIME_RELATIVE
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Criação de symlink não permitida nesta instalação")
            with self.assertRaises(ValueError):
                runner.prepare_test_environment(self.root)
            self.assertEqual([], list(Path(outside).iterdir()))

    def test_report_escape_link_is_rejected_before_launch(self):
        runner.prepare_test_environment(self.root)
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "must-not-touch.json"
            target.write_text("original", encoding="utf-8")
            report = self.root / runner.RUNTIME_RELATIVE / "latest_run.json"
            try:
                report.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Criação de symlink não permitida nesta instalação")
            with patch.object(runner.subprocess, "Popen") as popen:
                with self.assertRaises(ValueError):
                    runner.run_test_command(self.root, [sys.executable, "-c", "pass"])
                popen.assert_not_called()
            self.assertEqual("original", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

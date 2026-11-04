import importlib.util
import json
import os
from pathlib import Path
import subprocess as raw_subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from modules.execution_lessons import ExecutionLessons
from modules.execution_policy import context_fingerprint, record_codex_action_event, record_codex_cli_event
from modules.process_runtime import (ProcessObservation, RepeatedExecutionError, execution_context,
                                     failure_diagnostic, operation_profile, process_scope, subprocess)


class ProcessRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def entries(self):
        return json.loads((self.root / ".merotec_system_ai/execution_lessons.json").read_text(encoding="utf-8"))["entries"]

    def test_profiles_cover_workflows_without_killing_persistent_apps(self):
        for command, expected in (("git push", ("deploy", 900)), ("git commit -m x", ("git", 180)),
                                  ("git status", ("diagnostic", 60)), ("dotnet build", ("build", 600)),
                                  ("cargo check", ("build", 600)), ("npm install", ("dependencies", 900)),
                                  ("mkdir app", ("create", 300)), ("ctest", ("test", 120)),
                                  ("python app.py", ("run", None)), ("python -m http.server", ("service", None)),
                                  ("arduino-cli monitor", ("service", None)), ("codex app-server", ("service", None))):
            with self.subTest(command=command):
                self.assertEqual(expected, operation_profile(command))

    def test_run_preserves_output_cwd_exit_code_and_check_semantics(self):
        command = [sys.executable, "-c", "import os,sys; print(os.getcwd()); print('err',file=sys.stderr); sys.exit(7)", "compile"]
        with process_scope(workspace=self.root):
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=5)
            self.assertEqual(7, result.returncode)
            self.assertIn(str(self.root), result.stdout)
            self.assertIn("err", result.stderr)
            with self.assertRaises(raw_subprocess.CalledProcessError) as error:
                subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(7, error.exception.returncode)
        self.assertEqual("failed", self.entries()[0]["status"])
        self.assertEqual("build", self.entries()[0]["operation"])
        self.assertEqual(2, self.entries()[0]["runs"])

    def test_deploy_and_git_keep_authentication_and_environment(self):
        parent = dict(os.environ, HOME="preserved-home", USERPROFILE="preserved-profile", CODEX_HOME="preserved-codex",
                      LOCALAPPDATA="preserved-appdata", OPENAI_API_KEY="private-credential-value")
        command = [sys.executable, "-c", "import os,json; print(json.dumps({k:os.environ[k] for k in ['HOME','USERPROFILE','CODEX_HOME','LOCALAPPDATA','OPENAI_API_KEY']}))", "deploy"]
        with process_scope(workspace=self.root):
            result = subprocess.run(command, cwd=self.root, env=parent, capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode)
        observed = json.loads(result.stdout)
        for key in observed:
            self.assertEqual(parent[key], observed[key])
        self.assertEqual("preserved-appdata", parent["LOCALAPPDATA"])
        self.assertNotIn("MEROTEC_EXECUTION_WORKSPACE", os.environ)
        with process_scope(workspace=self.root):
            subprocess.run([sys.executable, "-c", "import os; print(os.environ['OPENAI_API_KEY'])"],
                           cwd=self.root, env=parent, capture_output=True, text=True, timeout=5)
        self.assertNotIn("private-credential-value", str(self.entries()))

    def test_two_failed_autonomous_deploys_are_not_launched_a_third_time(self):
        command = [sys.executable, "-c", "raise SystemExit(3)", "deploy"]
        with process_scope(workspace=self.root, autonomous=True):
            for _ in range(2):
                self.assertEqual(3, subprocess.run(command, cwd=self.root, timeout=5).returncode)
            with patch("modules.process_runtime._stdlib.Popen") as launch:
                with self.assertRaises(RepeatedExecutionError):
                    subprocess.run(command, cwd=self.root, timeout=5)
                launch.assert_not_called()
        # A human can deliberately retry an external condition that changed.
        with process_scope(workspace=self.root, autonomous=False):
            self.assertEqual(3, subprocess.run(command, cwd=self.root, timeout=5).returncode)
        (self.root / "deploy.yaml").write_text("repaired: true", encoding="utf-8")
        with process_scope(workspace=self.root, autonomous=True):
            self.assertEqual(3, subprocess.run(command, cwd=self.root, timeout=5).returncode)

    def test_new_diagnostic_evidence_permits_one_attempt_after_failure(self):
        command = "git push"
        context = context_fingerprint(self.root)
        memory = ExecutionLessons(self.root)
        for _ in range(2):
            memory.record(command, 1, context=context, strategy="process:deploy")
        self.assertTrue(memory.assess_repeat(command, context=context)["should_pause"])
        memory.record("git ls-remote origin", 0, context=context, operation="diagnostic")
        self.assertFalse(memory.assess_repeat(command, context=context)["should_pause"])
        memory.record(command, 1, context=context, strategy="process:deploy")
        self.assertTrue(memory.assess_repeat(command, context=context)["should_pause"])

    def test_timeout_raises_and_stops_owned_grandchild(self):
        marker = self.root / "orphan-marker"
        child = f"import pathlib,time; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('orphan')"
        command = [sys.executable, "-c", f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"]
        with process_scope(workspace=self.root):
            with self.assertRaises(raw_subprocess.TimeoutExpired):
                subprocess.run(command, cwd=self.root, capture_output=True, timeout=.5)
        self.assertEqual("timeout", self.entries()[0]["status"])
        self.assertIn("causa não confirmada", self.entries()[0]["diagnosis"])
        time.sleep(1.8)
        self.assertFalse(marker.exists())

    def test_popen_watchdog_stops_process_even_while_owner_reads_stdout(self):
        with process_scope(workspace=self.root, timeout=.15):
            process = subprocess.Popen([sys.executable, "-c", "import time; print('started',flush=True); time.sleep(30)"],
                                       cwd=self.root, stdout=subprocess.PIPE, text=True)
        output = process.stdout.read()
        with self.assertRaises(raw_subprocess.TimeoutExpired):
            process.wait(timeout=5)
        process.stdout.close()
        self.assertEqual("timeout", process.observation.result["status"])
        self.assertIn("started", output)

    def test_cancellation_and_launch_error_cannot_become_verified(self):
        observation = ProcessObservation("app", self.root)
        self.assertEqual("cancelled", observation.finish(0, cancelled=True)["status"])
        with process_scope(workspace=self.root):
            with self.assertRaises(OSError):
                subprocess.Popen([str(self.root / "missing-tool")], cwd=self.root)
        self.assertEqual("launch_error", self.entries()[-1]["status"])

    def test_success_is_recorded_once_and_does_not_patch_stdlib(self):
        original = raw_subprocess.Popen
        with process_scope(workspace=self.root):
            process = subprocess.Popen([sys.executable, "-c", "print('done')"], cwd=self.root, stdout=subprocess.PIPE, text=True)
            output, _ = process.communicate(timeout=5)
            process.wait()
        self.assertEqual("done", output.strip())
        self.assertIs(raw_subprocess.Popen, original)
        self.assertEqual(1, self.entries()[0]["runs"])
        self.assertEqual("verified_success", self.entries()[0]["status"])

    def test_git_branch_and_other_language_changes_invalidate_context(self):
        git = self.root / ".git"
        git.mkdir()
        (git / "HEAD").write_text("commit-one", encoding="utf-8")
        old = execution_context(self.root)
        (git / "HEAD").write_text("commit-two", encoding="utf-8")
        new = execution_context(self.root)
        self.assertNotEqual(old, new)
        for filename in ("app.rs", "app.go", "app.dart", "app.java", "Dockerfile", "app.html"):
            (self.root / filename).write_text("source", encoding="utf-8")
            fresh = execution_context(self.root)
            self.assertNotEqual(new, fresh)
            new = fresh

    def test_diagnostics_are_symptoms_not_claimed_root_causes(self):
        for text, fragment in (("Permission denied", "acesso"), ("401 Unauthorized", "autenticação"),
                               ("non-fast-forward", "Git"), ("ModuleNotFoundError", "dependência"),
                               ("SyntaxError", "compilação"), ("connection refused", "rede")):
            self.assertIn(fragment, failure_diagnostic(text))
        self.assertIn("causa não confirmada", failure_diagnostic("SciPy slow", timed_out=True))

    def test_native_codex_records_edits_separately_and_cli_process_results(self):
        item = {"id": "edit", "type": "fileChange", "status": "completed", "changes": [
            {"path": str(self.root / "app.py"), "kind": {"type": "add"}, "diff": "private source"}]}
        seen = set()
        result = record_codex_action_event("item/completed", {"item": item}, self.root, seen)
        self.assertEqual("applied", result["entry"]["status"])
        self.assertIsNone(result["entry"]["exit_code"])
        self.assertNotIn("private source", str(self.entries()))
        self.assertIsNone(record_codex_action_event("item/completed", {"item": item}, self.root, seen))
        event = {"type": "item.completed", "item": {"id": "cmd", "type": "command_execution", "status": "completed",
                                                     "command": "git status", "aggregated_output": "clean", "exit_code": 0}}
        result = record_codex_cli_event(event, self.root, seen)
        self.assertEqual("verified_success", result["entry"]["status"])
        self.assertEqual("diagnostic", result["entry"]["operation"])
        self.assertIsNone(record_codex_cli_event({"type": "item.completed", "item": {"type": "agent_message", "text": "deploy approved"}}, self.root, seen))

    def test_memory_merges_results_from_concurrent_processes(self):
        script = ("from modules.execution_lessons import ExecutionLessons; import sys; "
                  "m=ExecutionLessons(sys.argv[1]); "
                  "[m.record(sys.argv[2]+str(i),0) for i in range(8)]")
        children = [raw_subprocess.Popen([sys.executable, "-c", script, str(self.root), f"worker-{index}-"],
                                        cwd=Path(__file__).resolve().parents[1], stdout=raw_subprocess.PIPE, stderr=raw_subprocess.PIPE)
                    for index in range(3)]
        results = [child.communicate(timeout=15) for child in children]
        for child, (stdout, stderr) in zip(children, results):
            self.assertEqual(0, child.returncode, (stdout, stderr))
        self.assertEqual(24, len(self.entries()))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 indisponível")
class CodingObservationTests(unittest.TestCase):
    def test_rejected_code_is_remembered_without_source_and_new_code_is_allowed(self):
        from modules.qt_agent_actions import QtAgentActions
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            messages = []
            action = QtAgentActions(root, lambda *args: messages.append(args), lambda _: True, lambda _: None)
            invalid = "[WRITE: app.py]\ndef broken(:\n[/WRITE]"
            for _ in range(3):
                action.apply(invalid)
            self.assertTrue(any("repetida recusada" in text for _, text in messages))
            self.assertFalse((root / "app.py").exists())
            self.assertEqual([root / "app.py"], action.apply("[WRITE: app.py]\nprint('repaired')\n[/WRITE]"))
            entries = json.loads((root / ".merotec_system_ai/execution_lessons.json").read_text(encoding="utf-8"))["entries"]
            self.assertEqual("applied", entries[-1]["status"])
            self.assertNotIn("print('repaired')", json.dumps(entries))


if __name__ == "__main__":
    unittest.main()

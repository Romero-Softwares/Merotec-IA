import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from modules.execution_lessons import ExecutionLessons
from modules.execution_policy import (STRATEGY, context_fingerprint, execution_guidance, prepare_test_launch,
                                      record_codex_command_event, record_observed_result, runner_report, shell_command)
from modules.project_test_runner import RUNTIME_RELATIVE


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_test_commands_are_wrapped_with_literal_argv(self):
        command = shell_command([sys.executable, "-u", "-m", "unittest", "discover", "-s", "tests with spaces", "-v"])
        launch = prepare_test_launch(command, self.root)
        self.assertEqual(STRATEGY, launch["strategy"])
        self.assertEqual([sys.executable, "-u", "-m", "unittest", "discover", "-s", "tests with spaces", "-v"], launch["test_argv"])
        self.assertIn(str(self.root.resolve()), launch["arguments"])
        self.assertIsNotNone(prepare_test_launch("pytest -q", self.root))
        self.assertIsNotNone(prepare_test_launch("python -m pytest -q", self.root))

    def test_compounds_shell_expressions_and_non_tests_are_not_rewritten(self):
        for command in ("python -m unittest; echo ok", "python -m unittest && echo ok", "python -m pytest | tee log",
                        "python -m pytest > result.txt", "$env:LOCALAPPDATA='x'; python -m unittest",
                        "python -c 'print(42)'", "python app.py", "python -m compileall .", "npm test",
                        "python -m unittest $(whoami)", "python -m unittest\nother"):
            with self.subTest(command=command):
                self.assertIsNone(prepare_test_launch(command, self.root))

    def test_fingerprint_changes_for_source_or_environment_but_not_runtime_history(self):
        source = self.root / "app.py"
        source.write_text("pass", encoding="utf-8")
        before = context_fingerprint(self.root)
        ExecutionLessons(self.root).record("python -m unittest", 1)
        self.assertEqual(before, context_fingerprint(self.root))
        source.write_text("print('repaired')", encoding="utf-8")
        after = context_fingerprint(self.root)
        self.assertNotEqual(before, after)
        with patch.dict(os.environ, {"PYTHONPATH": "different-environment"}):
            self.assertNotEqual(after, context_fingerprint(self.root))

    def test_real_unittest_setup_writes_only_to_isolated_appdata_and_temp(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_persistence.py").write_text(
            "import os,pathlib,tempfile,unittest\n"
            "class Persistence(unittest.TestCase):\n"
            " def setUp(self):\n"
            "  root=pathlib.Path.cwd()\n"
            "  app=pathlib.Path(os.environ['LOCALAPPDATA']); app.relative_to(root)\n"
            "  (app/'configuracoes.json').write_text('ok')\n"
            "  fd,name=tempfile.mkstemp(); os.close(fd); pathlib.Path(name).relative_to(root)\n"
            " def test_ok(self): self.assertTrue(True)\n", encoding="utf-8")
        command = shell_command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        launch = prepare_test_launch(command, self.root)
        started = time.time()
        completed = subprocess.run([launch["program"], *launch["arguments"]], cwd=self.root,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("test_ok", completed.stdout)
        report = runner_report(self.root, launch["test_argv"], 0, started)
        self.assertEqual("passed", report["status"])
        record_observed_result(self.root, command, 0, strategy=STRATEGY, output=completed.stdout)
        context = execution_guidance(self.root)
        self.assertIn("[VERIFICADO exit=0]", context)
        self.assertIn(STRATEGY, context)
        self.assertIn("project_test_runner.py", context)

    def test_stale_or_mismatched_runner_report_is_not_evidence(self):
        path = self.root / RUNTIME_RELATIVE / "latest_run.json"
        path.parent.mkdir(parents=True)
        payload = {"schema_version": 1, "started_at": "2000-01-01T00:00:00+00:00", "command": ["tool"],
                   "command_redacted": False, "exit_code": 0, "workspace": str(self.root)}
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(runner_report(self.root, ["tool"], 0, time.time()))
        self.assertIsNone(runner_report(self.root, ["different"], 0, 0))
        self.assertIsNone(runner_report(self.root, ["tool"], 1, 0))
        self.assertIsNotNone(runner_report(self.root, ["tool"], 0, 0))

    def test_cancellation_or_crash_with_zero_code_does_not_become_success(self):
        result = record_observed_result(self.root, "tool", 0, succeeded=False)
        self.assertEqual("unconfirmed", result["entry"]["status"])
        self.assertIsNone(record_observed_result(self.root, "tool", False))

    def test_codex_only_completed_structured_command_results_are_recorded_once(self):
        seen = set()
        item = {"id": "cmd1", "type": "commandExecution", "command": "python -m unittest", "cwd": str(self.root),
                "exitCode": 0, "aggregatedOutput": "OK", "status": "completed"}
        self.assertIsNone(record_codex_command_event("agentMessage/delta", {"delta": "72 tests passed"}, self.root, seen))
        self.assertIsNone(record_codex_command_event("item/started", {"item": item}, self.root, seen))
        result = record_codex_command_event("item/completed", {"item": item}, self.root, seen)
        self.assertEqual("verified_success", result["entry"]["status"])
        self.assertIsNone(record_codex_command_event("item/completed", {"item": item}, self.root, seen))
        self.assertEqual(1, ExecutionLessons(self.root).assess_repeat(item["command"], context=context_fingerprint(self.root))["runs"])

    def test_codex_outside_workspace_and_invalid_exit_codes_are_ignored(self):
        for code in (None, "0", False):
            item = {"id": "cmd", "type": "commandExecution", "command": "tool", "exitCode": code}
            self.assertIsNone(record_codex_command_event("item/completed", {"item": item}, self.root, set()))
        item.update(exitCode=0, cwd=str(self.root.parent))
        self.assertIsNone(record_codex_command_event("item/completed", {"item": item}, self.root, set()))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 indisponível")
class AgentCommandIntegrationTests(unittest.TestCase):
    def test_ide_wraps_tests_pauses_repeated_failures_and_allows_repaired_source(self):
        from pyside_app import MerotecIDE

        class IDE:
            terminal_process = None
            run_agent_command = MerotecIDE.run_agent_command
            _record_agent_command_result = MerotecIDE._record_agent_command_result

            def __init__(self, root):
                self.workspace = root
                self.log = []
                self.launches = []

            def append_terminal(self, message): self.log.append(message)
            def _append_chat_activity(self, message): self.log.append(message)
            def start_terminal_process(self, program, arguments, label, **kwargs):
                self.launches.append((program, arguments))
                return True
            def _start_shell_command(self, command, **kwargs):
                self.launches.append(("shell", command))
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("pass", encoding="utf-8")
            ide = IDE(root)
            for _ in range(2):
                self.assertTrue(ide.run_agent_command("python -m unittest"))
                self.assertIn("project_test_runner.py", str(ide.launches[-1]))
                ide._record_agent_command_result("python -m unittest", False, 1, "FAILED")
            self.assertFalse(ide.run_agent_command("python -m unittest"))
            self.assertEqual(2, len(ide.launches))
            self.assertIn("falhas repetidas", ide._agent_command_rejection)
            (root / "app.py").write_text("print('fixed')", encoding="utf-8")
            self.assertTrue(ide.run_agent_command("python -m unittest"))
            self.assertEqual(3, len(ide.launches))
            ide._record_agent_command_result("python -m unittest", True, 0, "OK")
            self.assertIn("[VERIFICADO exit=0]", ExecutionLessons(root).format_for_agent())
            self.assertTrue(ide.run_agent_command("python app.py"))
            self.assertEqual("shell", ide.launches[-1][0])


class CodexExecutionIntegrationTests(unittest.TestCase):
    def test_codex_does_not_replay_task_in_another_executor_after_partial_activity(self):
        from modules.engine import UniversalEngine
        from unittest.mock import Mock
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._find_codex_executable = lambda: "codex"
        engine._codex_is_logged_in = lambda _: True
        engine._codex_reasoning_efforts = lambda: ["high"]
        engine._generate_codex_exec_solution = Mock(return_value="unexpected replay")
        engine._try_codex_fallback_models = Mock(return_value="unexpected model replay")
        for response in (None, "Codex ficou sem enviar progresso", "Codex app-server retornou erro: alta demanda"):
            def partial(*args, **kwargs):
                engine._codex_attempt_had_activity = True
                return response
            engine._generate_codex_app_server_solution = partial
            result = engine._generate_codex_solution("faça deploy")
            self.assertIn("não reiniciou", result)
        engine._generate_codex_exec_solution.assert_not_called()
        engine._try_codex_fallback_models.assert_not_called()

    def test_codex_startup_failure_without_activity_can_use_existing_fallback(self):
        from modules.engine import UniversalEngine
        from unittest.mock import Mock
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._find_codex_executable = lambda: "codex"
        engine._codex_is_logged_in = lambda _: True
        engine._codex_reasoning_efforts = lambda: ["high"]
        engine._codex_attempt_had_activity = False
        engine._generate_codex_app_server_solution = lambda *args, **kwargs: None
        engine._generate_codex_exec_solution = Mock(return_value="Fallback autorizado sem ações anteriores")
        self.assertIn("Fallback autorizado", engine._generate_codex_solution("compile"))
        engine._generate_codex_exec_solution.assert_called_once()

    def test_successful_codex_turn_is_not_replayed_for_words_in_final_prose(self):
        from modules.engine import UniversalEngine
        from unittest.mock import Mock
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._find_codex_executable = lambda: "codex"
        engine._codex_is_logged_in = lambda _: True
        engine._codex_reasoning_efforts = lambda: ["high"]
        engine._codex_attempt_succeeded = True
        engine._codex_attempt_had_activity = True
        final = "Corrigido e validado. A mensagem de alta demanda era da execução anterior."
        engine._generate_codex_app_server_solution = lambda *args, **kwargs: final
        engine._try_codex_fallback_models = Mock()
        self.assertEqual(final, engine._generate_codex_solution("compile"))
        engine._try_codex_fallback_models.assert_not_called()

    def _run_server(self, root, notifications):
        from modules.engine import UniversalEngine

        class Input:
            def __init__(self): self.sent = []
            def write(self, value): self.sent.append(json.loads(value))
            def flush(self): pass
            def close(self): pass

        class Process:
            def __init__(self):
                self.stdin = Input()
                messages = [{"id": 0, "result": {}}, {"id": 1, "result": {}},
                            {"id": 2, "result": {"thread": {"id": "thread-test"}}}, {"id": 3, "result": {"turn": {"id": "turn-test"}}},
                            *notifications, {"method": "turn/completed", "params": {"turn": {
                                "status": "completed", "items": [{"type": "agentMessage", "text": "Concluído"}]}}}]
                self.stdout = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
            def poll(self): return None
            def terminate(self): pass

        process = Process()
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "codex"
        engine.codex_model_name = "model-test"
        engine.codex_reasoning_effort = "high"
        engine.system_instruction = "test"
        engine.cancel_requested = False
        engine._remember_quota_message_handler = lambda *args: None
        engine._remember_rate_limits = lambda *args: None
        engine._remember_codex_response_model = lambda *args, **kwargs: None
        with patch("modules.engine.subprocess.Popen", return_value=process):
            result = engine._generate_codex_app_server_solution("codex", "teste", workspace_path=root)
        self.assertEqual("Concluído", result)
        return process.stdin.sent

    def test_real_engine_loop_records_completed_commands_without_trusting_final_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {"id": "cmd-test", "type": "commandExecution", "command": "python -m unittest",
                    "cwd": str(root), "exitCode": 0, "status": "completed", "aggregatedOutput": "OK"}
            event = {"method": "item/completed", "params": {"item": item}}
            sent = self._run_server(root, [event, event])
            result = ExecutionLessons(root).assess_repeat(item["command"], context=context_fingerprint(root))
            self.assertEqual(1, result["runs"])
            self.assertEqual("verified_success", result["status"])
            start = next(message for message in sent if message.get("method") == "turn/start")
            self.assertIn("EXECUÇÃO E DIAGNÓSTICO", start["params"]["input"][0]["text"])

    def test_engine_rejects_approval_of_command_that_repeatedly_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = "python -m unittest"
            memory = ExecutionLessons(root)
            for _ in range(2):
                memory.record(command, 1, context=context_fingerprint(root), strategy="codex_process")
            sent = self._run_server(root, [{"id": 10, "method": "item/commandExecution/requestApproval",
                                           "params": {"command": command, "cwd": str(root)}}])
            response = next(message for message in sent if message.get("id") == 10)
            self.assertEqual("reject", response["result"]["decision"])

    def test_execution_memory_survives_web_and_local_context_compaction(self):
        from modules.engine import UniversalEngine
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.lm_studio_max_input_chars = 2500
        evidence = "EXECUÇÃO E DIAGNÓSTICO DO PROJETO:\n[VERIFICADO exit=0] workspace_test_runtime"
        context = evidence + "\n\nMAPA PERMANENTE DO PROJETO PARA A IA:\n" + "componente " * 2000
        self.assertIn("workspace_test_runtime", engine._compact_web_chat_context(context))
        self.assertIn("workspace_test_runtime", engine._compact_lm_studio_context(context))

    def test_failed_native_command_delivers_diagnostic_in_same_turn_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {"id": "deploy", "type": "commandExecution", "command": "git push", "cwd": str(root),
                    "exitCode": 1, "status": "failed", "aggregatedOutput": "401 Unauthorized"}
            sent = self._run_server(root, [{"method": "item/completed", "params": {"item": item}},
                                           {"id": 4, "error": {"message": "steering unavailable"}}])
            steers = [message for message in sent if message.get("method") == "turn/steer"]
            self.assertEqual(1, len(steers))
            self.assertEqual("turn-test", steers[0]["params"]["expectedTurnId"])
            self.assertIn("autenticação", steers[0]["params"]["input"][0]["text"])
            self.assertIn("parcial", steers[0]["params"]["input"][0]["text"])
            self.assertEqual(1, ExecutionLessons(root).assess_repeat("git push", context=context_fingerprint(root))["runs"])

    def test_cli_fallback_uses_json_results_and_returns_final_message(self):
        from modules.engine import UniversalEngine
        class Input:
            def write(self, value): pass
            def close(self): pass
        class Process:
            returncode = 0
            stdin = Input()
            def __init__(self):
                self.stdout = io.StringIO("\n".join(json.dumps(event) for event in [
                    {"type": "item.completed", "item": {"type": "command_execution", "id": "cli-command", "command": "git status", "status": "completed", "exit_code": 0, "aggregated_output": "clean"}},
                    {"type": "item.completed", "item": {"type": "agent_message", "id": "final", "text": "Feito"}},
                ]) + "\n")
            def wait(self, **kwargs): return 0
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "codex"
        engine.codex_model_name = "model-test"
        engine.codex_reasoning_effort = "high"
        engine.system_instruction = "test"
        engine.cancel_requested = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("modules.engine.subprocess.Popen", return_value=Process()) as launch:
                result = engine._generate_codex_exec_solution("codex", "teste", workspace_path=root)
            self.assertEqual("Feito", result)
            self.assertIn("--json", launch.call_args.args[0])
            memory = ExecutionLessons(root).assess_repeat("git status", context=context_fingerprint(root))
            self.assertEqual("verified_success", memory["status"])


if __name__ == "__main__":
    unittest.main()

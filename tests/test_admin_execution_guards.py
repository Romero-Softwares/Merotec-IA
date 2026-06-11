import os
import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.agent_actions import AgentActionsMixin
from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES
from modules.engine import UniversalEngine
from modules.workspace_intelligence import WorkspaceIntelligenceMixin
from main import UniversalApp

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DummyApp(AgentActionsMixin, WorkspaceIntelligenceMixin):
    build_command_failure_diagnostic = UniversalApp.build_command_failure_diagnostic
    command_failure_signature = UniversalApp.command_failure_signature
    extract_workspace_paths_from_output = UniversalApp.extract_workspace_paths_from_output
    unique_existing_relative_paths = UniversalApp.unique_existing_relative_paths
    read_diagnostic_file_snippets = UniversalApp.read_diagnostic_file_snippets

    def __init__(self):
        self.current_workspace = str(Path.cwd())
        self.active_ai_objective = ""
        self.ai_task_metrics = {}
        self.current_task_id = 0
        self.command_failure_signatures = {}
        self.messages = []
        self.logs = []
        self.executed_commands = []

    def is_task_cancelled(self, task_id=None):
        return False

    def add_chat_message(self, sender, message):
        self.messages.append((sender, message))

    def log_agent(self, message):
        self.logs.append(message)

    def set_status(self, message, kind=None):
        self.status = (message, kind)

    def _agent_execute(self, command, **kwargs):
        self.executed_commands.append(("EXECUTE", command))

    def _agent_execute_admin(self, command, **kwargs):
        self.executed_commands.append(("EXECUTE_ADMIN", command))

    def iter_workspace_files(self, limit=500):
        workspace = Path(self.current_workspace)
        count = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [name for name in sorted(dirs) if name not in IGNORED_DIRS and not name.startswith(".")]
            root_path = Path(root)
            for filename in sorted(files):
                path = root_path / filename
                if filename.startswith(".") or path.suffix.lower() in IGNORED_SUFFIXES:
                    continue
                try:
                    rel = path.relative_to(workspace)
                except ValueError:
                    continue
                yield path, rel
                count += 1
                if count >= limit:
                    return


class DummyCodexApprovalApp(DummyApp):
    ask_codex_app_server_approval = UniversalApp.ask_codex_app_server_approval
    is_codex_command_approval_method = UniversalApp.is_codex_command_approval_method
    describe_codex_app_server_approval = UniversalApp.describe_codex_app_server_approval
    format_codex_app_server_approval = UniversalApp.format_codex_app_server_approval
    extract_codex_app_server_command = UniversalApp.extract_codex_app_server_command
    compact_codex_approval_value = UniversalApp.compact_codex_approval_value


class AdminExecutionGuardsTest(unittest.TestCase):
    def setUp(self):
        self.app = DummyApp()

    def test_placeholder_commands_are_rejected(self):
        placeholders = [
            "...",
            "`",
            "``",
            "` e termine com `",
            "e termine com",
            "comece com ` e termine com `",
            "comando",
            "command",
            "comando real",
            "comando concreto",
            "comando_real",
            "o comando real",
            "o comando concreto",
            "EXECUTE: comando real",
            "EXECUTE: comando concreto",
            "EXECUTE_ADMIN: comando real",
            "EXECUTE_ADMIN: comando concreto",
            "[" + "EXECUTE: ...]",
            "[" + "EXECUTE: ` e termine com `]",
            "[" + "EXECUTE_ADMIN: ...]",
            "[" + "EXECUTE_ADMIN: ` e termine com `]",
            "[" + "EXECUTE: comando real]",
            "[" + "EXECUTE: comando concreto]",
            "[" + "EXECUTE_ADMIN: comando real]",
            "[" + "EXECUTE_ADMIN: comando concreto]",
            "<comando completo>",
            "<comando concreto>",
            "cmd /c comando",
            "cmd /c ...",
            "cmd /c comando real",
            "cmd /c comando concreto",
            "cmd.exe /c \"comando real\"",
            "cmd.exe /c \"comando concreto\"",
            "cmd /c <comando completo>",
            "powershell -Command comando",
            "powershell -Command ...",
            "powershell -Command comando real",
            "powershell -Command comando concreto",
            "powershell -Command \"comando real\"",
            "powershell -Command \"comando concreto\"",
            "pwsh -c command here",
            "pwsh -c 'comando concreto aqui'",
        ]

        for command in placeholders:
            with self.subTest(command=command):
                self.assertTrue(self.app.is_placeholder_command(command))

    def test_real_commands_with_placeholder_words_are_allowed(self):
        commands = [
            "cmd /c echo comando real",
            "powershell -Command \"Write-Output command here\"",
            "python -m unittest tests.test_admin_execution_guards",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.app.is_placeholder_command(command))

    def test_placeholder_terminal_errors_are_detected(self):
        outputs = [
            "'...' nao e reconhecido como um comando interno",
            "'comando' nao e reconhecido como um comando interno",
            "'comando real' nao e reconhecido como um comando interno",
            "'comando concreto' nao e reconhecido como um comando interno",
            "'`' nao e reconhecido como um comando interno",
            "'command here' is not recognized as an internal or external command",
        ]

        for output in outputs:
            with self.subTest(output=output):
                self.assertTrue(
                    self.app.command_output_is_placeholder_error("npm test", output)
                )

    def test_admin_requests_are_cleaned_and_detected(self):
        command = "net stop spooler como administrador"

        self.assertTrue(self.app.is_admin_execute_request(command))
        self.assertEqual("net stop spooler", self.app.clean_admin_command(command))
        self.assertTrue(
            self.app.command_output_requires_admin(
                "A operacao solicitada requer elevacao."
            )
        )

    def test_system_instruction_uses_concrete_examples_without_copyable_placeholders(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.language = "pt-BR"

        instruction = UniversalEngine._build_system_instruction(engine)

        self.assertIn("[EXECUTE: python -m unittest]", instruction)
        self.assertIn("[EXECUTE_ADMIN: whoami /groups]", instruction)
        self.assertNotIn("[EXECUTE: comando concreto]", instruction)
        self.assertNotIn("[EXECUTE_ADMIN: comando concreto]", instruction)

    def test_app_server_placeholder_commands_are_detected(self):
        engine = UniversalEngine.__new__(UniversalEngine)

        placeholder_messages = [
            ("item/commandExecution/requestApproval", {"command": "..."}),
            ("execCommandApproval", {"argv": ["cmd", "/c", "..."]}),
            ("execCommandApproval", {"program": "powershell", "args": ["-Command", "comando real"]}),
        ]

        for method, params in placeholder_messages:
            with self.subTest(method=method, params=params):
                self.assertTrue(
                    engine._app_server_message_has_placeholder_command(method, params)
                )

        self.assertFalse(
            engine._app_server_message_has_placeholder_command(
                "execCommandApproval",
                {"command": "python -m unittest tests.test_admin_execution_guards"},
            )
        )
        self.assertTrue(
            engine._app_server_output_is_placeholder_error(
                "rawOutput",
                {"output": "'...' nao e reconhecido como um comando interno"},
            )
        )

    def test_placeholder_execute_actions_do_not_reach_executor(self):
        placeholders = ("comando real", "comando concreto", "...")

        for action in ("EXECUTE", "EXECUTE_ADMIN"):
            for placeholder in placeholders:
                with self.subTest(action=action, placeholder=placeholder):
                    self.app.executed_commands.clear()
                    self.app.logs.clear()
                    self.app.parse_and_execute_agent_actions(
                        f"[{action}: {placeholder}]",
                        task_objective="rodar validacao",
                        task_id=f"{action}-{placeholder}",
                    )

                    self.assertEqual([], self.app.executed_commands)
                    self.assertTrue(
                        any("placeholder" in message.lower() for message in self.app.logs)
                    )

    def test_repeated_concrete_placeholder_failure_is_detected(self):
        self.assertTrue(
            self.app.command_output_is_placeholder_error(
                "comando concreto",
                "'comando' não é reconhecido como um comando interno\n"
                "ou externo, um programa operável ou um arquivo em lotes.",
            )
        )

    def test_real_admin_actions_reach_admin_executor(self):
        self.app.parse_and_execute_agent_actions(
            "[EXECUTE_ADMIN: net stop spooler]",
            task_objective="parar spooler",
            task_id="admin-tag",
        )

        self.assertEqual([("EXECUTE_ADMIN", "net stop spooler")], self.app.executed_commands)

    def test_execute_with_admin_marker_is_redirected_to_admin_executor(self):
        AgentActionsMixin._agent_execute(
            self.app,
            "net stop spooler como administrador",
            task_objective="parar spooler",
            task_id="admin-marker",
        )

        self.assertEqual(
            [("EXECUTE_ADMIN", "net stop spooler")],
            self.app.executed_commands,
        )

    def test_human_test_plan_finds_nested_html_target(self):
        temp_root = Path(os.environ.get("MEROTEC_TEST_TMP", "C:/tmp" if os.name == "nt" else tempfile.gettempdir()))
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            root = Path(temp_dir)
            playable = root / "unity-mini-csharp-game" / "PlayableWeb"
            playable.mkdir(parents=True)
            (playable / "index.html").write_text("<!doctype html><canvas></canvas>", encoding="utf-8")
            self.app.current_workspace = str(root)
            self.app.active_ai_objective = "Execute um teste visual do sistema"

            plan = self.app.build_human_test_plan("auto", task_objective=self.app.active_ai_objective)

            self.assertIsNotNone(plan)
            self.assertEqual(playable.resolve(), Path(plan["cwd"]).resolve())
            self.assertFalse(plan["shell"])
            self.assertIn("-m http.server", plan["display"])
            self.assertRegex(plan["url"], r"http://127\.0\.0\.1:\d+/index\.html$")

    def test_codex_app_server_placeholder_approval_is_rejected(self):
        app = DummyCodexApprovalApp()

        approved = app.ask_codex_app_server_approval(
            "item/commandExecution/requestApproval",
            {"command": "..."},
            str(Path.cwd()),
        )

        self.assertFalse(approved)
        self.assertTrue(
            any("demonstrativo" in message.lower() for _sender, message in app.messages)
        )
        self.assertTrue(any("placeholder" in message.lower() for message in app.logs))

    def test_codex_app_server_placeholder_argv_is_rejected(self):
        app = DummyCodexApprovalApp()

        approved = app.ask_codex_app_server_approval(
            "execCommandApproval",
            {"argv": ["cmd", "/c", "..."]},
            str(Path.cwd()),
        )

        self.assertFalse(approved)
        self.assertTrue(any("placeholder" in message.lower() for message in app.logs))

    def test_codex_app_server_extracts_common_command_shapes(self):
        app = DummyCodexApprovalApp()
        cases = [
            ({"command": "python -m unittest"}, "python -m unittest"),
            ({"cmd": ["python", "-m", "unittest"]}, "python -m unittest"),
            ({"program": "cmd.exe", "args": ["/c", "echo", "ok"]}, "cmd.exe /c echo ok"),
            ({"request": {"commandLine": "npm test"}}, "npm test"),
        ]

        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(expected, app.extract_codex_app_server_command(params))


if __name__ == "__main__":
    unittest.main()

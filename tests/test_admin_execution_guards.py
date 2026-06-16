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
from main import UniversalApp, _single_instance_bypass_requested

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DummyApp(AgentActionsMixin, WorkspaceIntelligenceMixin):
    build_command_failure_diagnostic = UniversalApp.build_command_failure_diagnostic
    build_ai_display_response = UniversalApp.build_ai_display_response
    response_has_agent_action = UniversalApp.response_has_agent_action
    strip_agent_action_markup = UniversalApp.strip_agent_action_markup
    action_execution_message = UniversalApp.action_execution_message
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
        self.human_tests = []
        self.queued_ai_tasks = []
        self.ai_passive_action_count = 0
        self.max_ai_passive_actions = 20

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

    def _agent_human_test(self, request, **kwargs):
        self.human_tests.append((request, kwargs))

    def _run_ai_task(self, command, **kwargs):
        self.queued_ai_tasks.append((command, kwargs))

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
        self.assertIn("[WEB_SEARCH: consulta objetiva]", instruction)
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

    def test_unrestricted_mode_allows_real_mutation_execute_action(self):
        self.app.settings = {"autonomous_unrestricted_mode": True}
        command = 'powershell -NoProfile -Command "Set-Content -Path sample.txt -Value ok"'

        self.app.parse_and_execute_agent_actions(
            f"[EXECUTE: {command}]",
            task_objective="atualizar arquivo pelo caminho direto",
            task_id="unrestricted-execute",
        )

        self.assertEqual([("EXECUTE", command)], self.app.executed_commands)

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

    def test_adjacent_human_test_tags_execute_visual_validation_once(self):
        self.app.parse_and_execute_agent_actions(
            "[HUMAN_TEST: auto][HUMAN_TEST: auto]",
            task_objective="faca um teste visual desse sistema",
            task_id="visual-tag",
        )

        self.assertEqual(1, len(self.app.human_tests))
        self.assertEqual("auto", self.app.human_tests[0][0])

    def test_adjacent_action_tags_are_not_shown_as_chat_text(self):
        response = "[HUMAN_TEST: auto][HUMAN_TEST: auto]"

        self.assertTrue(self.app.response_has_agent_action(response))
        self.assertEqual(
            "A IDE recebeu uma execucao real e iniciou a validacao.",
            self.app.build_ai_display_response(response),
        )
        self.assertEqual("", self.app.strip_agent_action_markup(response))

    def test_web_search_tag_is_hidden_as_context_action(self):
        response = "[WEB_SEARCH: Python urllib timeout]"

        self.assertTrue(self.app.response_has_agent_action(response))
        self.assertEqual(
            "A IDE esta coletando contexto objetivo para executar o proximo passo.",
            self.app.build_ai_display_response(response),
        )
        self.assertEqual("", self.app.strip_agent_action_markup(response))

    def test_web_search_action_parses_results_and_requeues_agent(self):
        html = """
        <html><body>
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Furllib.request.html">urllib.request docs</a>
          <a class="result__snippet">Official Python documentation for urllib.request.</a>
        </body></html>
        """
        self.app.fetch_web_search_html = lambda query: html

        self.app.parse_and_execute_agent_actions(
            "[WEB_SEARCH: Python urllib request timeout]",
            task_objective="resolver erro de rede usando documentacao atual",
            task_id="web-search",
        )

        self.assertTrue(
            any("Busquei na internet" in message for _sender, message in self.app.messages)
        )
        self.assertEqual(1, len(self.app.queued_ai_tasks))
        _command, kwargs = self.app.queued_ai_tasks[0]
        context = kwargs["extra_context"]
        self.assertIn("WEB_SEARCH: Python urllib request timeout", context)
        self.assertIn("urllib.request docs", context)
        self.assertIn("https://docs.python.org/3/library/urllib.request.html", context)
        self.assertIn("Official Python documentation", context)

    def test_inline_action_examples_are_not_executed(self):
        self.app.parse_and_execute_agent_actions(
            "Para validar, use [HUMAN_TEST: auto].",
            task_objective="explique como validar",
            task_id="visual-example",
        )

        self.assertEqual([], self.app.human_tests)

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

    def test_human_test_plan_relaunches_self_in_separate_test_instance(self):
        self.app.current_workspace = str(PROJECT_ROOT)
        self.app.active_ai_objective = "Analise o print do teste visual real da IDE"

        plan = self.app.build_human_test_plan("auto", task_objective=self.app.active_ai_objective)

        self.assertIsNotNone(plan)
        self.assertEqual(PROJECT_ROOT.resolve(), Path(plan["cwd"]).resolve())
        self.assertFalse(plan["shell"])
        self.assertIn("nova instancia de teste", plan["display"])
        self.assertIsInstance(plan["command"], list)
        self.assertIn("main.py", " ".join(str(part) for part in plan["command"]))
        self.assertIn("MEROTEC_INSTANCE_TITLE_SUFFIX", plan.get("env", {}))
        self.assertEqual("1", plan["env"].get("MEROTEC_FORCE_NEW_INSTANCE"))
        self.assertEqual("1", plan["env"].get("MEROTEC_HUMAN_TEST_INSTANCE"))
        self.assertEqual("1", plan["env"].get("MEROTEC_VISUAL_TEST_INSTANCE"))
        self.assertIn("teste visual", plan["env"]["MEROTEC_INSTANCE_TITLE_SUFFIX"])
        self.assertNotIn("capture_window_title", plan)
        self.assertTrue(plan.get("require_target_window"))
        self.assertGreater(plan["ready_timeout"], 2)

    def test_human_test_self_plans_use_unique_instance_markers(self):
        self.app.current_workspace = str(PROJECT_ROOT)
        self.app.active_ai_objective = "Analise o print do teste visual real da IDE"

        first_plan = self.app.build_human_test_plan("auto", task_objective=self.app.active_ai_objective)
        second_plan = self.app.build_human_test_plan("auto", task_objective=self.app.active_ai_objective)

        self.assertNotEqual(
            first_plan["env"]["MEROTEC_INSTANCE_TITLE_SUFFIX"],
            second_plan["env"]["MEROTEC_INSTANCE_TITLE_SUFFIX"],
        )

    def test_human_test_capture_prefers_process_over_title(self):
        image = object()

        class CaptureApp(DummyApp):
            def __init__(self):
                super().__init__()
                self.capture_calls = []

            def grab_window_image_by_pid(self, pid, timeout=8.0):
                self.capture_calls.append(("pid", pid, timeout))
                return image

            def grab_window_image_by_title(self, title, timeout=8.0):
                self.capture_calls.append(("title", title, timeout))
                return None

        app = CaptureApp()

        result = app.grab_human_test_image(
            {
                "capture_process_pid": 1234,
                "capture_window_title": "titulo antigo",
                "window_capture_timeout": 9.0,
                "require_target_window": True,
            }
        )

        self.assertIs(image, result)
        self.assertEqual([("pid", 1234, 9.0)], app.capture_calls)

    def test_collect_process_tree_pids_includes_descendants(self):
        parent_map = {
            101: 100,
            102: 101,
            103: 102,
            200: 999,
        }

        self.assertEqual(
            {100, 101, 102, 103},
            self.app.collect_process_tree_pids(100, parent_map=parent_map),
        )

    def test_single_instance_bypass_accepts_visual_test_markers(self):
        keys = (
            "MEROTEC_FORCE_NEW_INSTANCE",
            "MEROTEC_HUMAN_TEST_INSTANCE",
            "MEROTEC_VISUAL_TEST_INSTANCE",
            "MEROTEC_INSTANCE_TITLE_SUFFIX",
        )
        previous = {key: os.environ.get(key) for key in keys}
        try:
            for key in keys:
                os.environ.pop(key, None)

            self.assertFalse(_single_instance_bypass_requested())

            os.environ["MEROTEC_HUMAN_TEST_INSTANCE"] = "1"
            self.assertTrue(_single_instance_bypass_requested())
            os.environ.pop("MEROTEC_HUMAN_TEST_INSTANCE", None)

            os.environ["MEROTEC_VISUAL_TEST_INSTANCE"] = "1"
            self.assertTrue(_single_instance_bypass_requested())
            os.environ.pop("MEROTEC_VISUAL_TEST_INSTANCE", None)

            os.environ["MEROTEC_INSTANCE_TITLE_SUFFIX"] = " - teste visual unitario"
            self.assertTrue(_single_instance_bypass_requested())
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_human_test_plan_wraps_explicit_self_command_in_test_instance(self):
        self.app.current_workspace = str(PROJECT_ROOT)
        self.app.active_ai_objective = "Faca um teste visual real"
        requested = f'"{sys.executable}" "main.py"'

        plan = self.app.build_human_test_plan(
            requested,
            task_objective=self.app.active_ai_objective,
            requested_command=requested,
        )

        self.assertIsNotNone(plan)
        self.assertFalse(plan["shell"])
        self.assertIn("nova instancia de teste", plan["display"])
        self.assertEqual("1", plan["env"].get("MEROTEC_FORCE_NEW_INSTANCE"))
        self.assertNotIn("capture_window_title", plan)
        self.assertTrue(plan.get("require_target_window"))

    def test_human_test_plan_wraps_absolute_python_self_command(self):
        self.app.current_workspace = str(PROJECT_ROOT)
        self.app.active_ai_objective = "Execute o teste visual"
        requested = f'"{sys.executable}" "{PROJECT_ROOT / "main.py"}"'

        plan = self.app.build_human_test_plan(
            requested,
            task_objective=self.app.active_ai_objective,
            requested_command=requested,
        )

        self.assertFalse(plan["shell"])
        self.assertIn("nova instancia de teste", plan["display"])
        self.assertEqual("1", plan["env"].get("MEROTEC_FORCE_NEW_INSTANCE"))
        self.assertTrue(plan.get("require_target_window"))
        self.assertNotIn("capture_window_title", plan)

    def test_execute_python_main_visual_request_routes_to_human_test(self):
        self.app.active_ai_objective = "Faca um teste visual real"

        self.assertTrue(
            self.app.should_route_execute_to_human_test(
                f'"{sys.executable}" "main.py"',
                self.app.active_ai_objective,
            )
        )

        self.assertTrue(
            self.app.should_route_execute_to_human_test(
                "py main.py",
                self.app.active_ai_objective,
            )
        )

        self.assertTrue(
            self.app.should_route_execute_to_human_test(
                "pythonw.exe .\\main.py",
                self.app.active_ai_objective,
            )
        )

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

    def test_codex_app_server_real_request_is_autoapproved_when_enabled(self):
        app = DummyCodexApprovalApp()
        app.settings = {"codex_auto_approve_app_server_requests": True}

        approved = app.ask_codex_app_server_approval(
            "execCommandApproval",
            {"command": "python -m unittest tests.test_admin_execution_guards"},
            str(Path.cwd()),
        )

        self.assertTrue(approved)
        self.assertTrue(any("autoaprovada" in message.lower() for message in app.logs))

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

    def test_direct_workspace_changes_keep_codex_final_message_visible(self):
        response = (
            "Corrigi a inicializacao da janela e preservei a configuracao atual.\n\n"
            "Verificado com python -m unittest."
        )

        display = self.app.build_ai_display_response(
            response,
            direct_changes=[("alterado", "ide_settings.json")],
            direct_change_total=1,
        )

        self.assertIn("Corrigi a inicializacao da janela", display)
        self.assertIn("Verificado com python -m unittest", display)
        self.assertIn("Arquivos afetados", display)
        self.assertIn("ide_settings.json (alterado)", display)
        self.assertNotEqual(
            self.app.format_direct_workspace_changes([("alterado", "ide_settings.json")], 1),
            display,
        )


if __name__ == "__main__":
    unittest.main()

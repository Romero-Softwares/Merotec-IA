from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None
if PYSIDE_AVAILABLE:
    from pyside_app import MerotecIDE
else:
    MerotecIDE = None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 nao esta instalado neste interpretador")
class PySideTerminalTests(unittest.TestCase):
    def test_chat_context_is_preserved_for_ephemeral_provider_threads(self):
        ide = MerotecIDE.__new__(MerotecIDE)
        ide.active_ai_objective = "Corrigir a memoria da conversa"
        ide.ai_context_memory = []
        ide.last_response = "Vou verificar o estado atual do projeto."

        ide._remember_ai_context_message("Voce", "Investigue por que a IA esquece a conversa.")
        ide._remember_ai_context_message("Merotec IA", ide.last_response)
        context = ide.build_chat_continuity_context("continue de onde parou")

        self.assertIn("Missao ativa mais recente: Corrigir a memoria da conversa", context)
        self.assertIn("Investigue por que a IA esquece a conversa.", context)
        self.assertIn("Nunca alegue que nao ha conversa anterior", context)

    def test_short_continuation_does_not_replace_active_objective(self):
        self.assertTrue(MerotecIDE._is_chat_continuation_request("continue de onde parou"))
        self.assertTrue(MerotecIDE._is_chat_continuation_request("faça isso"))
        self.assertFalse(MerotecIDE._is_chat_continuation_request("crie um projeto novo"))

    def test_image_generation_request_is_detected_without_hijacking_regular_chat(self):
        self.assertTrue(MerotecIDE._is_image_generation_request("Gere uma imagem de pessoas ficticias"))
        self.assertTrue(MerotecIDE._is_image_generation_request("crie imagem: uma cidade futurista"))
        self.assertFalse(MerotecIDE._is_image_generation_request("consegue criar imagem de pessoas?"))

    def test_agent_image_message_is_forwarded_to_chat_as_attachment(self):
        class ImmediateBridge:
            @staticmethod
            def call_soon(callback):
                callback()

        class FakeIDE:
            _is_image_attachment = MerotecIDE._is_image_attachment

            def __init__(self):
                self.ui_bridge = ImmediateBridge()
                self.remembered = []
                self.messages = []

            def _remember_ai_context_message(self, sender, text):
                self.remembered.append((sender, text))

            def add_chat(self, sender, text, outgoing=False, attachments=None):
                self.messages.append((sender, text, outgoing, attachments))

        ide = FakeIDE()
        MerotecIDE.add_chat_image_message(ide, "Merotec IA", Path("imagem_teste.png"), "Imagem pronta")

        self.assertEqual(ide.messages, [("Merotec IA", "Imagem pronta", False, [Path("imagem_teste.png")])])
        self.assertIn("[imagem anexada: imagem_teste.png]", ide.remembered[0][1])

    def test_codex_generated_artifact_is_delivered_to_chat(self):
        class FakeIDE:
            _is_image_attachment = MerotecIDE._is_image_attachment

            def __init__(self):
                self.delivered = []

            def add_chat_image_message(self, sender, path, text):
                self.delivered.append((sender, path, text))

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "gerada.png"
            image.write_bytes(b"png")
            ide = FakeIDE()

            MerotecIDE._deliver_generated_chat_image(ide, image)

        self.assertEqual(
            ide.delivered,
            [("Merotec IA", image, "Imagem gerada pelo Codex.")],
        )

    def test_all_codex_generated_artifacts_are_delivered_once(self):
        class FakeIDE:
            _is_image_attachment = MerotecIDE._is_image_attachment

            def __init__(self):
                self.delivered = []

            def add_chat_image_message(self, sender, path, text):
                self.delivered.append((sender, path, text))

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "gerada.png"
            other = Path(temp_dir) / "variacao.webp"
            image.write_bytes(b"png")
            other.write_bytes(b"webp")
            ide = FakeIDE()

            MerotecIDE._deliver_generated_chat_image(ide, [image, other, image])

        self.assertEqual(
            ide.delivered,
            [
                ("Merotec IA", image, "Imagem gerada pelo Codex."),
                ("Merotec IA", other, "Imagem gerada pelo Codex."),
            ],
        )

    def test_webengine_json_result_is_accepted_for_chat_actions(self):
        result = MerotecIDE._decode_web_javascript_result(
            '{"ok": true, "before": "resposta anterior"}'
        )
        self.assertEqual(result, {"ok": True, "before": "resposta anterior"})

    def test_webengine_invalid_result_is_rejected_for_chat_actions(self):
        self.assertIsNone(MerotecIDE._decode_web_javascript_result("resposta livre"))

    def test_terminal_output_accepts_utf8(self):
        self.assertEqual(MerotecIDE._decode_process_output("configuração pronta".encode("utf-8")), "configuração pronta")

    def test_terminal_output_accepts_windows_code_pages(self):
        self.assertEqual(MerotecIDE._decode_process_output("configuração pronta".encode("cp1252")), "configuração pronta")

    def test_python_command_is_unbuffered_only_once(self):
        self.assertEqual(MerotecIDE._make_python_output_unbuffered("python main.py"), "python -u main.py")
        self.assertEqual(MerotecIDE._make_python_output_unbuffered("python -u main.py"), "python -u main.py")
        self.assertEqual(
            MerotecIDE._make_python_output_unbuffered('& "C:\\venv\\Scripts\\pythonw.exe" -m compileall'),
            '& "C:\\venv\\Scripts\\pythonw.exe" -u -m compileall',
        )

    def test_quoted_executable_is_invoked_by_powershell(self):
        command = '"C:\\venv\\Scripts\\pythonw.exe" -m compileall -q main.py'
        self.assertEqual(
            MerotecIDE._normalize_powershell_command(command),
            '& "C:\\venv\\Scripts\\pythonw.exe" -m compileall -q main.py',
        )

    def test_jarsigner_and_keytool_use_interactive_terminal_mode(self):
        self.assertTrue(MerotecIDE._command_requires_interactive_input("jarsigner -verify app.aab"))
        self.assertTrue(MerotecIDE._command_requires_interactive_input("keytool -genkeypair -alias app"))
        self.assertFalse(MerotecIDE._command_requires_interactive_input("flutter build appbundle"))

    def test_opening_external_file_keeps_current_workspace(self):
        class FakeIDE:
            def __init__(self, workspace):
                self.workspace = workspace
                self.opened_file = None

            def open_file(self, path):
                self.opened_file = path

            def open_workspace(self, _path):
                raise AssertionError("Abrir um arquivo externo nao pode trocar o projeto ativo")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "projeto-ativo"
            external_file = Path(temp_dir) / "outro-projeto" / "arquivo.py"
            workspace.mkdir()
            external_file.parent.mkdir()
            external_file.write_text("print('ok')\n", encoding="utf-8")
            ide = FakeIDE(workspace)

            with patch("pyside_app.QFileDialog.getOpenFileName", return_value=(str(external_file), "")):
                MerotecIDE.open_external_file(ide)

            self.assertEqual(external_file.resolve(), ide.opened_file)
            self.assertEqual(workspace, ide.workspace)

    def test_browser_result_resumes_the_active_agent_task(self):
        class FakeIDE:
            _decode_web_javascript_result = staticmethod(MerotecIDE._decode_web_javascript_result)

            def __init__(self):
                self._agent_browser_action_pending = {
                    "action": "inspect",
                    "payload": {},
                }
                self.chat_busy = True
                self._chat_waiting_for_browser = True
                self.continuation = None

            def _continue_agent_after_browser_action(self, action, payload, result):
                self.continuation = (action, payload, result)

        ide = FakeIDE()

        MerotecIDE._finish_agent_browser_action(
            ide,
            {"result": '{"ok": true, "text": "Pagina carregada"}'},
        )

        self.assertEqual(
            ide.continuation,
            ("inspect", {}, {"ok": True, "text": "Pagina carregada"}),
        )
        self.assertFalse(ide._chat_waiting_for_browser)

    def test_browser_request_is_queued_without_worker_thread_webengine_probe(self):
        class SignalProbe:
            def __init__(self):
                self.args = None

            def emit(self, *args):
                self.args = args

        class FakeIDE:
            def __init__(self):
                self.browser_action_requested = SignalProbe()

            def _internal_browser_is_usable(self):
                raise AssertionError("A thread do agente não deve consultar QWebEngineView.")

        ide = FakeIDE()
        callback = lambda _event: None

        request_id = MerotecIDE.request_internal_browser_action(
            ide,
            "inspect",
            {"scope": "pagina"},
            callback,
        )

        self.assertTrue(request_id.startswith("qt-browser-"))
        self.assertEqual(ide.browser_action_requested.args, ("inspect", {"scope": "pagina"}, callback))

    def test_browser_actions_are_explicitly_queued_to_the_qt_event_loop(self):
        source = (ROOT / "pyside_app.py").read_text(encoding="utf-8")
        self.assertIn("Qt.ConnectionType.QueuedConnection", source)
        self.assertIn("JSON.stringify({ok:true,url: location.href", source)

    def test_browser_fallback_preamble_is_recovered_with_local_visual_test(self):
        reply = (
            "Vou usar o recurso de controle do navegador para executar e inspecionar "
            "visualmente o primeiro subprojeto. O navegador interno não está disponível "
            "nesta sessão. Vou validar a renderização com o navegador instalado localmente."
        )

        self.assertTrue(MerotecIDE._is_unactionable_browser_preamble(reply))
        self.assertFalse(MerotecIDE._is_unactionable_browser_preamble("[HUMAN_TEST: auto]"))

    def test_non_browser_reply_is_not_converted_to_visual_test(self):
        self.assertFalse(
            MerotecIDE._is_unactionable_browser_preamble(
                "A validação está pendente porque o servidor local não iniciou."
            )
        )

    def test_visual_test_uses_flask_server_before_template_as_static_html(self):
        class FakeIDE:
            _requested_local_visual_url = staticmethod(MerotecIDE._requested_local_visual_url)
            _find_visual_server_target = staticmethod(MerotecIDE._find_visual_server_target)
            _allocate_visual_test_port = staticmethod(MerotecIDE._allocate_visual_test_port)

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self._chat_task_prompt = "Teste visual completo"

            def _build_visual_server_plan(self, workspace, request):
                return MerotecIDE._build_visual_server_plan(self, workspace, request)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "templates").mkdir()
            (root / "templates" / "index.html").write_text("<h1>{{ title }}</h1>", encoding="utf-8")
            (root / "app.py").write_text(
                "from flask import Flask\napp = Flask(__name__)\n",
                encoding="utf-8",
            )
            plan = MerotecIDE._build_visual_test_plan(FakeIDE(root), "auto")

        self.assertEqual("browser", plan["kind"])
        self.assertIn("flask", plan["display"])
        self.assertIn("-m", plan["command"])
        self.assertIn("flask", plan["command"])
        self.assertTrue(plan["url"].endswith("/"))

    def test_visual_test_reuses_explicit_local_server_without_starting_another(self):
        class FakeIDE:
            _requested_local_visual_url = staticmethod(MerotecIDE._requested_local_visual_url)

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self._chat_task_prompt = ""

            def _build_visual_server_plan(self, workspace, request):
                return MerotecIDE._build_visual_server_plan(self, workspace, request)

        with tempfile.TemporaryDirectory() as temp_dir:
            plan = MerotecIDE._build_visual_server_plan(
                FakeIDE(temp_dir),
                Path(temp_dir),
                "Abra http://127.0.0.1:4173/dashboard para validar a tela",
            )

        self.assertEqual("http://127.0.0.1:4173/dashboard", plan["url"])
        self.assertIsNone(plan["command"])

    def test_changed_files_start_first_automatic_validation(self):
        class FakeIDE:
            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self._chat_task_prompt = "corrija o arquivo"
                self._chat_auto_validation_paths = set()
                self._chat_waiting_for_command = False
                self.command = ""
                self.activity = []
                self.messages = []

            def validation_command_for_changed_paths(self, paths, objective):
                self.validation_paths = paths
                self.validation_objective = objective
                return "python -m compileall -q app.py"

            def run_agent_command(self, command):
                self.command = command
                return True

            def _append_chat_activity(self, text):
                self.activity.append(text)

            def add_chat(self, author, text):
                self.messages.append((author, text))

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "app.py"
            target.write_text("print('ok')\n", encoding="utf-8")
            ide = FakeIDE(temp_dir)

            started = MerotecIDE._start_automatic_validation(ide, [target])

            self.assertTrue(started)
            self.assertTrue(ide._chat_waiting_for_command)
            self.assertEqual(ide.command, "python -m compileall -q app.py")
            self.assertEqual(ide._chat_auto_validation_paths, {target.resolve()})

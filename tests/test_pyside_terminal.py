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
    from PySide6.QtWidgets import QApplication
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from pyside_app import AudioPlayerWidget, MerotecIDE, QMessageBox, _is_audio_source, _is_video_source, _media_source_name
else:
    QApplication = None
    QVideoWidget = None
    AudioPlayerWidget = None
    MerotecIDE = None
    QMessageBox = None
    _is_audio_source = None
    _is_video_source = None
    _media_source_name = None


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

    def test_short_request_to_preserve_image_theme_is_detected(self):
        self.assertTrue(MerotecIDE._is_video_theme_revision_request("Deve ter o tema da imagem"))
        self.assertTrue(MerotecIDE._is_video_theme_revision_request("Use a paleta da foto"))
        self.assertFalse(MerotecIDE._is_video_theme_revision_request("Gere um video de uma cidade"))

    def test_video_generation_request_remains_distinct_from_video_narration(self):
        self.assertTrue(MerotecIDE._is_video_generation_request("Gere um vídeo: uma cidade futurista"))
        self.assertTrue(MerotecIDE._is_video_generation_request("renderize vídeo de uma floresta"))
        self.assertFalse(MerotecIDE._is_video_generation_request("analise este vídeo e crie uma narração"))

    def test_requested_video_duration_is_extracted_and_duration_revision_is_detected(self):
        self.assertEqual(30, MerotecIDE._requested_video_duration_seconds("Gere um video de 30s"))
        self.assertEqual(45, MerotecIDE._requested_video_duration_seconds("duração: 45 segundos"))
        self.assertIsNone(MerotecIDE._requested_video_duration_seconds("gere um video moderno"))
        self.assertTrue(MerotecIDE._is_video_duration_revision_request("O tempo de video deve ser 30s"))
        self.assertFalse(MerotecIDE._is_video_duration_revision_request("O tempo de renderizacao e longo"))

    def test_video_revision_is_detected_for_explicit_visual_corrections(self):
        self.assertTrue(MerotecIDE._is_video_revision_request(
            "Gere o video novamente mas sem as bolhas e com textos 3d"
        ))
        self.assertFalse(MerotecIDE._is_video_revision_request("Gere um video de uma cidade"))

    def test_last_video_prompt_is_recovered_from_persisted_chat_context(self):
        ide = MerotecIDE.__new__(MerotecIDE)
        ide.ai_context_memory = [
            {"author": "Você", "text": "Gere um vídeo: uma cidade futurista"},
            {"author": "Merotec IA", "text": "Vídeo gerado."},
        ]

        self.assertEqual(
            "Gere um vídeo: uma cidade futurista",
            ide._last_video_generation_prompt_from_context(),
        )

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

    def test_audio_sources_are_recognized_for_the_embedded_player(self):
        self.assertTrue(_is_audio_source("C:/audios/narracao.mp3"))
        self.assertTrue(_is_audio_source("https://example.test/voice.ogg?download=1"))
        self.assertTrue(_is_audio_source({"url": "https://cdn.example.test/content?id=1", "media_type": "audio"}))
        self.assertFalse(_is_audio_source("https://example.test/preview.png"))
        self.assertEqual(_media_source_name("https://example.test/audio/narracao.mp3"), "narracao.mp3")

    def test_open_file_routes_audio_to_the_embedded_player_tab(self):
        class FakeIDE:
            open_file = MerotecIDE.open_file

            def __init__(self):
                self.paths_by_tab = {}
                self.opened_audio = None

            def open_audio(self, path):
                self.opened_audio = path

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fala.wav"
            path.write_bytes(b"RIFF" + b"x" * 48)
            ide = FakeIDE()
            ide.open_file(path)

        self.assertEqual(ide.opened_audio, path.resolve())

    def test_open_file_routes_video_to_the_embedded_player_tab(self):
        class FakeIDE:
            open_file = MerotecIDE.open_file

            def __init__(self):
                self.paths_by_tab = {}
                self.opened_video = None

            def open_video(self, path):
                self.opened_video = path

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demonstracao.mp4"
            path.write_bytes(b"video")
            ide = FakeIDE()
            ide.open_file(path)

        self.assertEqual(ide.opened_video, path.resolve())

    def test_audio_tab_uses_the_same_player_widget_as_the_chat(self):
        app = QApplication.instance() or QApplication([])

        class Tabs:
            def __init__(self):
                self.widget = None
                self.current_index = None

            def addTab(self, widget, _title):
                self.widget = widget
                return 3

            def setCurrentIndex(self, index):
                self.current_index = index

        class Language:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        ide = type("FakeIDE", (), {})()
        ide.tabs = Tabs()
        ide.paths_by_tab = {}
        ide.language = Language()

        MerotecIDE.open_audio(ide, Path("C:/audios/fala.wav"))

        self.assertIsInstance(ide.tabs.widget.findChild(AudioPlayerWidget), AudioPlayerWidget)
        self.assertEqual(ide.paths_by_tab[3], Path("C:/audios/fala.wav"))
        self.assertEqual(ide.language.text, "Áudio")
        self.assertIsNotNone(app)
        ide.tabs.widget.deleteLater()

    def test_video_tab_uses_an_embedded_video_widget(self):
        app = QApplication.instance() or QApplication([])

        class Tabs:
            def __init__(self):
                self.widget = None
                self.current_index = None

            def addTab(self, widget, _title):
                self.widget = widget
                return 4

            def setCurrentIndex(self, index):
                self.current_index = index

        class Language:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        ide = type("FakeIDE", (), {})()
        ide.tabs = Tabs()
        ide.paths_by_tab = {}
        ide.language = Language()

        MerotecIDE.open_video(ide, Path("C:/videos/demonstracao.mp4"))

        self.assertIsInstance(ide.tabs.widget.findChild(QVideoWidget), QVideoWidget)
        self.assertEqual(ide.paths_by_tab[4], Path("C:/videos/demonstracao.mp4"))
        self.assertEqual(ide.language.text, "Vídeo")
        self.assertIsNotNone(app)
        ide.tabs.widget.deleteLater()

    def test_local_audio_generation_requests_do_not_require_chat_web(self):
        self.assertTrue(MerotecIDE._is_audio_generation_request("Gere um áudio: Olá mundo"))
        self.assertTrue(MerotecIDE._is_audio_generation_request("criar narração: teste"))
        self.assertTrue(MerotecIDE._is_audio_generation_request("Produza uma voz: teste"))
        self.assertFalse(MerotecIDE._is_audio_generation_request("Gere uma imagem: cidade"))

    def test_local_audio_generation_strips_the_request_prefix(self):
        class Signal:
            def __init__(self):
                self.values = []

            def emit(self, *values):
                self.values.append(values)

        class Voice:
            def __init__(self):
                self.text = ""

            def generate_audio_file(self, text, output_dir):
                self.text = text
                path = Path(output_dir) / "narracao.mp3"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"audio")
                return path

        with tempfile.TemporaryDirectory() as temp_dir:
            ide = type("FakeIDE", (), {})()
            ide.workspace = Path(temp_dir)
            ide.voice = Voice()
            ide.audio_generation_finished = Signal()
            MerotecIDE._generate_audio(ide, "Gere um áudio Esta é uma narração local.")

        self.assertEqual(ide.voice.text, "Esta é uma narração local.")
        self.assertEqual(ide.audio_generation_finished.values[0][1], "")

    def test_generated_audio_artifacts_are_delivered_to_the_chat_player(self):
        class FakeIDE:
            _deliver_generated_chat_audio = MerotecIDE._deliver_generated_chat_audio

            def __init__(self):
                self.delivered = []

            def add_chat_audio_message(self, sender, source, text):
                self.delivered.append((sender, source, text))

        ide = FakeIDE()
        signed_audio = {"url": "https://cdn.example.test/content?id=1", "media_type": "audio", "name": "Narracao da resposta"}
        ide._deliver_generated_chat_audio([
            "https://example.test/narracao.mp3",
            "https://example.test/narracao.mp3",
            signed_audio,
            "https://example.test/arquivo.txt",
        ])

        self.assertEqual(len(ide.delivered), 2)
        self.assertEqual(ide.delivered[0][1], "https://example.test/narracao.mp3")
        self.assertEqual(ide.delivered[1][1], signed_audio)

    def test_generated_video_artifacts_are_delivered_to_the_chat_player(self):
        class ImmediateBridge:
            @staticmethod
            def call_soon(callback):
                callback()

        class FakeIDE:
            _deliver_generated_chat_video = MerotecIDE._deliver_generated_chat_video

            def __init__(self):
                self.ui_bridge = ImmediateBridge()
                self.remembered = []
                self.messages = []

            def _remember_ai_context_message(self, sender, text):
                self.remembered.append((sender, text))

            def add_chat(self, sender, text, outgoing=False, attachments=None):
                self.messages.append((sender, text, outgoing, attachments))

        ide = FakeIDE()
        ide._deliver_generated_chat_video([
            "https://example.test/anuncio.mp4",
            "https://example.test/anuncio.mp4",
            {"url": "https://cdn.example.test/media?id=1", "media_type": "video", "name": "Anúncio"},
            "https://example.test/arquivo.txt",
        ])

        self.assertEqual(len(ide.messages), 2)
        self.assertEqual(ide.messages[0][3], ["https://example.test/anuncio.mp4"])
        self.assertEqual(ide.messages[1][3][0]["name"], "Anúncio")
        self.assertTrue(_is_video_source({"url": "https://example.test/stream", "media_type": "video"}))

    def test_completed_narration_reply_publishes_the_latest_local_audio(self):
        class FakeIDE:
            _latest_generated_audio = MerotecIDE._latest_generated_audio
            _publish_unattached_narration = MerotecIDE._publish_unattached_narration

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self._published_chat_audio = set()
                self.delivered = []

            def add_chat_audio_message(self, sender, source, text, immediate=False):
                self.delivered.append((sender, Path(source), text, immediate))

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / ".merotec_system_ai" / "generated_audio"
            audio_dir.mkdir(parents=True)
            audio_path = audio_dir / "narracao.wav"
            audio_path.write_bytes(b"RIFF" + b"x" * 48)
            ide = FakeIDE(temp_dir)

            published = ide._publish_unattached_narration("Narração criada e validada.")

        self.assertTrue(published)
        self.assertEqual(ide.delivered[0][1].name, "narracao.wav")
        self.assertTrue(ide.delivered[0][3])

    def test_embedded_audio_player_has_playback_controls(self):
        app = QApplication.instance() or QApplication([])
        player = AudioPlayerWidget("C:/audios/narracao.mp3")

        self.assertEqual(player.play_button.text(), "Reproduzir")
        self.assertEqual(player.stop_button.text(), "Parar")
        self.assertEqual(player.position.text(), "0:00 / 0:00")
        self.assertEqual(player.progress.minimum(), 0)
        self.assertIsNotNone(app)
        player.deleteLater()

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

    def test_explorer_moves_a_file_when_dropped_on_a_folder(self):
        class FakeIDE:
            _is_editable_workspace_path = MerotecIDE._is_editable_workspace_path
            _remap_open_tabs = MerotecIDE._remap_open_tabs
            move_explorer_items = MerotecIDE.move_explorer_items

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self.paths_by_tab = {}
                self.refreshed = False
                self.status = ""

            def refresh_tree(self):
                self.refreshed = True

            def set_status(self, text):
                self.status = text

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            origin = workspace / "origem"
            destination = workspace / "destino"
            origin.mkdir()
            destination.mkdir()
            source = origin / "arquivo.py"
            source.write_text("print('ok')\n", encoding="utf-8")
            ide = FakeIDE(workspace)

            ide.move_explorer_items([str(source)], str(destination))

            self.assertFalse(source.exists())
            self.assertTrue((destination / "arquivo.py").is_file())
            self.assertTrue(ide.refreshed)
            self.assertIn("movido", ide.status)

    def test_explorer_renames_a_workspace_file(self):
        class FakeIDE:
            _is_editable_workspace_path = MerotecIDE._is_editable_workspace_path
            _remap_open_tabs = MerotecIDE._remap_open_tabs
            rename_explorer_item = MerotecIDE.rename_explorer_item

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self.paths_by_tab = {}
                self.refreshed = False
                self.status = ""

            def refresh_tree(self):
                self.refreshed = True

            def set_status(self, text):
                self.status = text

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source = workspace / "antes.txt"
            source.write_text("conteudo", encoding="utf-8")
            ide = FakeIDE(workspace)

            with patch("pyside_app.QInputDialog.getText", return_value=("depois.txt", True)):
                ide.rename_explorer_item(source)

            self.assertFalse(source.exists())
            self.assertTrue((workspace / "depois.txt").is_file())
            self.assertTrue(ide.refreshed)

    def test_explorer_deletes_only_after_confirmation(self):
        class FakeIDE:
            _is_editable_workspace_path = MerotecIDE._is_editable_workspace_path
            _discard_tabs_for_path = MerotecIDE._discard_tabs_for_path
            delete_explorer_item = MerotecIDE.delete_explorer_item

            def __init__(self, workspace):
                self.workspace = Path(workspace)
                self.paths_by_tab = {}
                self.refreshed = False
                self.status = ""

            def refresh_tree(self):
                self.refreshed = True

            def set_status(self, text):
                self.status = text

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source = workspace / "remover.txt"
            source.write_text("conteudo", encoding="utf-8")
            ide = FakeIDE(workspace)

            with patch(
                "pyside_app.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                ide.delete_explorer_item(source)

            self.assertFalse(source.exists())
            self.assertTrue(ide.refreshed)
            self.assertIn("Excluido", ide.status)

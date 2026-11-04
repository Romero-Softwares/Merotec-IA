import importlib.util
import unittest
from unittest.mock import Mock, patch


HAS_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_QT, "PySide6 indisponível")
class ModelIdentityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def bare_ide(self, engine):
        from PySide6.QtWidgets import QLabel, QMainWindow
        from pyside_app import MerotecIDE
        ide = MerotecIDE.__new__(MerotecIDE)
        # Initialize only Qt ownership; do not start the IDE, providers or timers.
        QMainWindow.__init__(ide)
        ide.engine = engine
        ide.model_identity = QLabel("badge inicial", ide)
        ide.provider_label = QLabel("cabeçalho inicial", ide)
        self.addCleanup(ide.deleteLater)
        return ide

    def codex_engine(self):
        from modules.engine import UniversalEngine
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "codex"
        engine.codex_model_name = ""
        engine.active_codex_model_name = ""
        engine.codex_default_model_name = ""
        engine.model_id = "modelo não confirmado"
        engine.codex_executable = None
        return engine

    def test_badge_and_header_use_the_exact_identity_reported_by_engine(self):
        engine = Mock(provider="openai")
        engine.assistant_display_name.return_value = "ft:gpt-snapshot:org:custom-id"
        ide = self.bare_ide(engine)
        ide._refresh_model_identity()
        self.assertEqual("Modelo: ft:gpt-snapshot:org:custom-id", ide.model_identity.text())
        self.assertEqual("openai · ft:gpt-snapshot:org:custom-id", ide.provider_label.text())
        self.assertIn("ft:gpt-snapshot:org:custom-id", ide.provider_label.toolTip())

    def test_frequent_identity_refresh_does_no_discovery_or_network_io(self):
        engine = self.codex_engine()
        engine._find_codex_executable = Mock(side_effect=AssertionError("No executable search during refresh"))
        ide = self.bare_ide(engine)
        with patch("pyside_app.discover_codex_models", side_effect=AssertionError("No catalogue query during refresh")) as discover, patch("pyside_app.QThreadPool.globalInstance") as pool, patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP during refresh")) as http:
            for _ in range(20):
                ide._refresh_model_identity()
            engine.active_codex_model_name = "reported-runtime-id"
            ide._refresh_model_identity()
            discover.assert_not_called()
            pool.assert_not_called()
            http.assert_not_called()
        engine._find_codex_executable.assert_not_called()
        self.assertEqual("Modelo: reported-runtime-id", ide.model_identity.text())
        self.assertEqual("codex · reported-runtime-id", ide.provider_label.text())

    def test_catalog_default_is_applied_through_engine_without_pinning_selection(self):
        engine = self.codex_engine()
        ide = self.bare_ide(engine)
        ide._model_identity_token = 3
        ide._model_identity_target = engine
        records = [{"id": "first-not-default", "default": False},
                   {"id": "exact-account-default", "name": "Friendly Name", "default": True}]
        with patch.object(engine, "remember_codex_default_model", wraps=engine.remember_codex_default_model) as remember:
            ide._codex_model_identity_discovered(3, records, "")
            remember.assert_called_once_with("exact-account-default")
        self.assertEqual("", engine.codex_model_name)
        self.assertEqual("Modelo: exact-account-default", ide.model_identity.text())
        self.assertEqual("codex · exact-account-default", ide.provider_label.text())

    def test_result_from_an_old_request_token_is_ignored(self):
        engine = self.codex_engine()
        ide = self.bare_ide(engine)
        ide._model_identity_token = 9
        ide._model_identity_target = engine
        with patch.object(engine, "remember_codex_default_model") as remember:
            ide._codex_model_identity_discovered(8, [{"id": "obsolete-id", "default": True}], "")
            remember.assert_not_called()
        self.assertEqual("badge inicial", ide.model_identity.text())
        self.assertEqual("cabeçalho inicial", ide.provider_label.text())

    def test_result_for_a_replaced_engine_is_ignored_even_with_current_token(self):
        old_engine = self.codex_engine()
        new_engine = self.codex_engine()
        ide = self.bare_ide(new_engine)
        ide._model_identity_token = 9
        ide._model_identity_target = old_engine
        with patch.object(old_engine, "remember_codex_default_model") as old_remember, patch.object(new_engine, "remember_codex_default_model") as new_remember:
            ide._codex_model_identity_discovered(9, [{"id": "old-account-default", "default": True}], "")
            old_remember.assert_not_called()
            new_remember.assert_not_called()
        self.assertEqual("badge inicial", ide.model_identity.text())

    def test_failed_or_defaultless_discovery_does_not_invent_an_id(self):
        engine = self.codex_engine()
        ide = self.bare_ide(engine)
        ide._model_identity_token = 2
        ide._model_identity_target = engine
        ide._refresh_model_identity()
        with patch.object(engine, "remember_codex_default_model") as remember:
            ide._codex_model_identity_discovered(2, [], "Tempo esgotado")
            self.assertEqual("Modelo: modelo não confirmado", ide.model_identity.text())
            self.assertIn("Nenhum ID foi presumido", ide.model_identity.toolTip())
            ide._codex_model_identity_discovered(2, [{"id": "available-but-not-default"}], "")
            remember.assert_not_called()
        self.assertEqual("Modelo: modelo não confirmado", ide.model_identity.text())
        self.assertNotIn("available-but-not-default", ide.provider_label.text())

    def test_discovery_is_queued_and_uses_the_captured_engine(self):
        engine = self.codex_engine()
        engine.codex_executable = "fake-codex.exe"
        ide = self.bare_ide(engine)
        with patch("pyside_app.QThreadPool.globalInstance") as pool, patch("pyside_app.discover_codex_models", return_value=[{"id": "discovered-exact-id", "default": True}]) as discover:
            ide._discover_model_identity()
            discover.assert_not_called()
            job = pool.return_value.start.call_args.args[0]
            job.run()
            discover.assert_called_once_with("fake-codex.exe", timeout=12)
        self.assertIs(engine, ide._model_identity_target)
        self.assertEqual("Modelo: discovered-exact-id", ide.model_identity.text())

    def test_explicit_model_or_non_codex_provider_does_not_launch_discovery(self):
        for provider, selected in (("codex", "configured-exact-id"), ("openai", "")):
            engine = Mock(provider=provider, codex_model_name=selected)
            engine.assistant_display_name.return_value = selected or "api-exact-id"
            ide = self.bare_ide(engine)
            with self.subTest(provider=provider), patch("pyside_app.QThreadPool.globalInstance") as pool:
                ide._discover_model_identity()
                pool.assert_not_called()
            self.assertEqual(f"Modelo: {selected or 'api-exact-id'}", ide.model_identity.text())

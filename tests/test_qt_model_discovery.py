import copy
import importlib.util
import unittest
from unittest.mock import Mock, patch

from modules.ai_profiles import ensure_ai_profiles, profile_for


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 indisponível")
class ModelSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        for name in ("_schedule_discovery",):
            patcher = patch("modules.qt_settings_dialog.QtSettingsDialog." + name)
            patcher.start()
            self.addCleanup(patcher.stop)
        voices = patch("modules.qt_settings_dialog.list_tts_voices", return_value=[])
        voices.start()
        self.addCleanup(voices.stop)

    def dialog(self, provider="openai", model="old-model"):
        from modules.qt_settings_dialog import QtSettingsDialog
        settings = ensure_ai_profiles({"ai_provider": provider, "active_ai_profile": provider,
            "openai_model_name": model, "openai_api_key": "sk-fake-test", "codex_model_name": model})
        dialog = QtSettingsDialog(settings)
        self.addCleanup(dialog.reject)
        return dialog, settings

    def inject(self, dialog, records, token=None, signature=None, error=""):
        token = dialog._model_token if token is None else token
        dialog._model_requests[token] = signature or (dialog._current_provider, "test")
        dialog._models_discovered(token, records, error)

    def test_discovered_models_preserve_current_selection_and_save_exact_id(self):
        dialog, settings = self.dialog()
        self.inject(dialog, [{"id": "model-one"}, {"id": "model-two"}])
        control = dialog.fields["openai_model_name"]
        self.assertEqual("old-model", control.currentText())
        self.assertGreaterEqual(control.findData("model-two"), 0)
        control.setCurrentIndex(control.findData("model-two"))
        self.assertNotIn("seleção atual não", dialog.model_status.text())
        dialog.accept()
        self.assertEqual("model-two", settings["openai_model_name"])
        self.assertEqual("model-two", profile_for(settings, "openai")["openai_model_name"])

    def test_drafts_survive_provider_switch_and_cancel_does_not_save_edits(self):
        dialog, settings = self.dialog()
        baseline = copy.deepcopy(settings)
        dialog.fields["openai_model_name"].setEditText("typed-model")
        dialog.fields["openai_base_url"].setText("https://example.test/api/v1")
        dialog.provider.setCurrentIndex(dialog.provider.findData("codex"))
        dialog.provider.setCurrentIndex(dialog.provider.findData("openai"))
        self.assertEqual("typed-model", dialog.fields["openai_model_name"].currentText())
        self.assertEqual("https://example.test/api/v1", dialog.fields["openai_base_url"].text())
        dialog.reject()
        self.assertEqual(baseline, settings)

    def test_invalid_inactive_profile_draft_is_not_persisted(self):
        for field, bad in (("openai_model_name", ""), ("openai_base_url", "not a URL")):
            with self.subTest(field=field):
                dialog, settings = self.dialog()
                baseline = copy.deepcopy(settings)
                control = dialog.fields[field]
                if hasattr(control, "setEditText"):
                    control.setEditText(bad)
                else:
                    control.setText(bad)
                dialog.provider.setCurrentIndex(dialog.provider.findData("codex"))
                with patch("modules.qt_settings_dialog.QMessageBox.warning") as warning:
                    dialog.accept()
                    warning.assert_called_once()
                self.assertEqual(baseline, settings)
                self.assertEqual("openai", dialog.provider.currentData())
                self.assertFalse(dialog._closed)

    def test_stale_discovery_cannot_replace_new_profile_or_credential_catalog(self):
        dialog, _ = self.dialog()
        old = dialog._model_token
        dialog._model_requests[old] = ("openai", "old")
        dialog._invalidate_models()
        dialog._models_discovered(old, [{"id": "stale"}], "")
        self.assertEqual(-1, dialog.fields["openai_model_name"].findData("stale"))
        current = dialog._model_token
        dialog._model_requests[current] = ("openai", "old")
        dialog.provider.setCurrentIndex(dialog.provider.findData("codex"))
        dialog._models_discovered(current, [{"id": "wrong-provider"}], "")
        self.assertEqual(-1, dialog.fields["codex_model_name"].findData("wrong-provider"))

    def test_discovery_error_keeps_manual_model_and_reenables_refresh(self):
        dialog, _ = self.dialog()
        dialog.refresh_models_button.setEnabled(False)
        self.inject(dialog, [], error="Chave inválida")
        self.assertTrue(dialog.refresh_models_button.isEnabled())
        self.assertEqual("old-model", dialog.fields["openai_model_name"].currentText())
        self.assertIn("Chave inválida", dialog.model_status.text())

    def test_codex_uses_exact_id_reasoning_choices_and_account_default(self):
        dialog, settings = self.dialog("codex", "model-one")
        self.inject(dialog, [{"id": "model-one", "name": "Model One", "reasoning": ["low", "medium"], "default_reasoning": "medium"}])
        self.assertEqual("medium", dialog.fields["codex_reasoning_effort"].currentText())
        control = dialog.fields["codex_model_name"]
        control.setCurrentIndex(control.findData(""))
        dialog.accept()
        self.assertEqual("", settings["codex_model_name"])

    def test_automatic_codex_choice_displays_discovered_exact_id(self):
        dialog, settings = self.dialog("codex", "")
        self.assertNotIn("Padrão da conta", dialog.fields["codex_model_name"].currentText())
        self.inject(dialog, [{"id": "exact-default", "name": "Default label", "default": True,
                             "reasoning": ["medium"], "default_reasoning": "medium"}])
        control = dialog.fields["codex_model_name"]
        self.assertEqual("exact-default (automático)", control.currentText())
        self.assertEqual("", dialog._control_value(control))
        self.assertEqual("medium", dialog.fields["codex_reasoning_effort"].currentText())
        dialog.accept()
        self.assertEqual("", settings["codex_model_name"])

    def test_codex_display_label_is_resolved_only_against_fetched_catalog(self):
        dialog, settings = self.dialog("codex", "Model One")
        self.inject(dialog, [{"id": "exact-id", "name": "Model One", "reasoning": ["high"]}])
        dialog.accept()
        self.assertEqual("exact-id", settings["codex_model_name"])

    def test_unknown_codex_label_with_spaces_is_not_saved_as_model_id(self):
        dialog, settings = self.dialog("codex", "old-model")
        dialog.fields["codex_model_name"].setEditText("Unknown display label")
        with patch("modules.qt_settings_dialog.QMessageBox.warning") as warning:
            dialog.accept()
            warning.assert_called_once()
        self.assertEqual("old-model", profile_for(settings, "codex")["codex_model_name"])
        self.assertFalse(dialog._closed)

    def test_async_worker_updates_catalog_without_blocking_widget(self):
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtTest import QTest
        dialog, _ = self.dialog()
        with patch("modules.qt_settings_dialog.discover_openai_models", return_value=["async-model"]) as discover:
            dialog.refresh_models(force=True)
            timer = QElapsedTimer()
            timer.start()
            while dialog.fields["openai_model_name"].findData("async-model") < 0 and timer.elapsed() < 3000:
                QTest.qWait(10)
            self.assertGreaterEqual(dialog.fields["openai_model_name"].findData("async-model"), 0)
            discover.assert_called_once_with("https://api.openai.com/v1", "sk-fake-test", timeout=8)

    def test_discovery_uses_values_currently_edited_in_dialog(self):
        dialog, _ = self.dialog()
        dialog.fields["openai_base_url"].setText("https://example.test/api/v1/models")
        dialog.fields["openai_api_key"].setText("new-fake-key")
        with patch("modules.qt_settings_dialog.QThreadPool.globalInstance") as pool:
            dialog.refresh_models(force=True)
            job = pool.return_value.start.call_args.args[0]
            with patch("modules.qt_settings_dialog.discover_openai_models", return_value=["new-model"]) as discover:
                job.run()
            discover.assert_called_once_with("https://example.test/api/v1", "new-fake-key", timeout=8)
        self.assertGreaterEqual(dialog.fields["openai_model_name"].findData("new-model"), 0)

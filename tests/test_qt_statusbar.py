import importlib.util
import unittest
from unittest.mock import Mock


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 indisponível")
class StatusBarLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        from pathlib import Path
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        # Windows offscreen Qt does not always discover the platform fonts.
        font = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/segoeui.ttf"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))

    def test_long_dynamic_fields_never_overlap_at_narrow_and_wide_widths(self):
        from PySide6.QtWidgets import QWidget
        from modules.qt_statusbar import AdaptiveStatusBar
        from pyside_app import STYLE
        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet(STYLE)
        bar = AdaptiveStatusBar(root)
        bar.status.setText("🤖 Compilando e validando todos os arquivos do projeto em execução (180s)")
        bar.model_identity.setText("Modelo: ft:gpt-5.6-terra:organization:custom-snapshot-2026-11-02")
        bar.quota_status.setText("Cota: janela 5h 100% | semanal 92% | entrada 123456 tokens | saída 76543 tokens")
        root.show()
        bar.show()
        for width in (320, 500, 744, 1000, 1440, 744):
            with self.subTest(width=width):
                root.resize(width, 42)
                bar.resize(width, 42)
                self.app.processEvents()
                visible = [bar.layout().itemAt(i).widget() for i in range(bar.layout().count())
                           if bar.layout().itemAt(i).widget().isVisible()]
                for label in visible:
                    self.assertTrue(bar.rect().contains(label.geometry()))
                for left, right in zip(visible, visible[1:]):
                    self.assertFalse(left.geometry().intersects(right.geometry()))
                self.assertIn("custom-snapshot-2026-11-02", bar.model_identity.text())
                self.assertIn("custom-snapshot-2026-11-02", bar.model_identity.toolTip())
                self.assertFalse(bar.grab().isNull())  # Exercise actual text painting.
        root.close()
        root.deleteLater()

    def test_quota_shows_usage_without_duplicating_the_exact_model(self):
        from modules.qt_statusbar import AdaptiveStatusBar
        from pyside_app import MerotecIDE
        bar = AdaptiveStatusBar()
        ide = Mock()
        ide.engine.quota_status_text.return_value = "CODEX gpt-5.6-terra/high | uso aguardando"
        ide.quota_status = bar.quota_status
        MerotecIDE.refresh_quota_status(ide)
        self.assertEqual("Cota: uso aguardando", bar.quota_status.text())
        self.assertEqual("CODEX gpt-5.6-terra/high | uso aguardando", bar.quota_status.toolTip())
        bar.deleteLater()

    def test_full_text_and_tooltip_survive_repeated_resizes_and_updates(self):
        from modules.qt_statusbar import ElidedLabel
        label = ElidedLabel()
        for text in ("Modelo: gpt-5.6-terra", "Modelo: " + "custom-model-" * 40):
            label.setText(text)
            for width in (1, 50, 1000):
                label.resize(width, 30)
                label.show()
                self.app.processEvents()
                self.assertEqual(text, label.text())
                self.assertEqual(text, label.toolTip())
                self.assertFalse(label.grab().isNull())
        label.close()
        label.deleteLater()

    def test_chat_header_keeps_space_for_the_exact_model(self):
        from PySide6.QtWidgets import QMainWindow
        from pyside_app import MerotecIDE, STYLE
        ide = MerotecIDE.__new__(MerotecIDE)
        QMainWindow.__init__(ide)
        ide.engine = Mock(provider="codex")
        ide.engine.assistant_display_name.return_value = "gpt-5.6-terra"
        ide.setStyleSheet(STYLE)
        panel = ide._build_chat()
        ide.setCentralWidget(panel)
        ide.resize(420, 600)
        ide._refresh_model_identity()
        ide.show()
        self.app.processEvents()
        self.assertGreater(ide.provider_label.width(), 50)
        self.assertEqual("codex · gpt-5.6-terra", ide.provider_label.text())
        self.assertTrue(panel.rect().contains(ide.provider_label.geometry()))
        # Only Qt/chat ownership was initialized; skip the full IDE close flow.
        ide.hide()
        ide.deleteLater()


if __name__ == "__main__":
    unittest.main()

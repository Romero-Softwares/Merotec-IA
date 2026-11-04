import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 indisponível")
class ArduinoEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_create_sketch_templates_and_never_replace_existing_directory(self):
        from modules.qt_arduino_editor import ArduinoEditor
        with tempfile.TemporaryDirectory() as root:
            widget = ArduinoEditor(root)
            widget.template.setCurrentText("LED sem bloqueio")
            with patch("modules.qt_arduino_editor.QFileDialog.getExistingDirectory", return_value=root), patch("modules.qt_arduino_editor.QInputDialog.getText", return_value=("Blink", True)):
                widget.create_sketch()
            path = Path(root) / "Blink/Blink.ino"
            original = path.read_text(encoding="utf-8")
            self.assertIn("millis()", original)
            self.assertEqual(path.resolve(), widget.path)
            self.assertFalse(widget.code.document().isModified())
            with patch("modules.qt_arduino_editor.QFileDialog.getExistingDirectory", return_value=root), patch("modules.qt_arduino_editor.QInputDialog.getText", return_value=("Blink", True)), patch("modules.qt_arduino_editor.QMessageBox.warning") as warning:
                widget.create_sketch()
                warning.assert_called_once()
            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_paste_save_open_and_cancel_unsaved_replacement(self):
        from modules.qt_arduino_editor import ArduinoEditor
        from PySide6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as root:
            widget = ArduinoEditor(root)
            self.app.clipboard().setText("void setup() {}\nvoid loop() {}\n")
            widget.code.paste()
            with patch("modules.qt_arduino_editor.QFileDialog.getSaveFileName", return_value=(str(Path(root) / "source"), "")):
                self.assertTrue(widget.save())
            path = Path(root) / "source.ino"
            self.assertEqual(widget.code.toPlainText(), path.read_text(encoding="utf-8"))
            widget.code.insertPlainText("// edit\n")
            before = widget.code.toPlainText()
            another = Path(root) / "other.ino"
            another.write_text("void loop() {}", encoding="utf-8")
            with patch("modules.qt_arduino_editor.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
                self.assertFalse(widget.load_file(another))
            self.assertEqual(before, widget.code.toPlainText())

    def test_changed_disk_file_is_not_silently_overwritten(self):
        from modules.qt_arduino_editor import ArduinoEditor
        from PySide6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "code.ino"
            path.write_text("// old", encoding="utf-8")
            widget = ArduinoEditor(root)
            widget.load_file(path)
            widget.code.setPlainText("// editor")
            path.write_text("// external", encoding="utf-8")
            with patch("modules.qt_arduino_editor.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                self.assertFalse(widget.save())
            self.assertEqual("// external", path.read_text(encoding="utf-8"))

    def test_browse_external_firmware_and_save_before_upload(self):
        from modules.qt_electronics import ElectronicsDialog
        from PySide6.QtWidgets import QMainWindow, QMessageBox
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as external:
            folder = Path(external) / "Blink"
            folder.mkdir()
            path = folder / "Blink.ino"
            path.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            ide = QMainWindow()
            ide.workspace = Path(workspace)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            with patch("modules.qt_electronics.QFileDialog.getExistingDirectory", return_value=str(folder)):
                dialog.browse_firmware()
            self.assertEqual(folder.resolve(), Path(dialog.folder.text()))
            self.assertIs(dialog.tabs.currentWidget(), dialog.arduino_editor)
            dialog.arduino_editor.code.setPlainText("void setup() {}\nvoid loop() { delay(5); }\n")
            dialog.port.setCurrentText("COM3")
            with patch("modules.qt_electronics.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                dialog.run_editor_action("upload")
            self.assertIn("delay(5)", path.read_text(encoding="utf-8"))
            self.assertEqual(folder.resolve(), dialog.start_process.call_args.args[3])
            args = dialog.start_process.call_args.args[1]
            self.assertIn("upload", args)
            self.assertIn("COM3", args)
            ide.close()

    def test_cancelled_save_or_busy_process_prevents_editor_upload(self):
        from modules.qt_electronics import ElectronicsDialog
        from PySide6.QtWidgets import QMainWindow
        with tempfile.TemporaryDirectory() as root:
            ide = QMainWindow()
            ide.workspace = Path(root)
            dialog = ElectronicsDialog(ide)
            dialog.run_tool = Mock()
            with patch.object(dialog.arduino_editor, "save", return_value=False):
                dialog.run_editor_action("upload")
            dialog.run_tool.assert_not_called()
            dialog.process = Mock()
            with patch.object(dialog.arduino_editor, "save") as save:
                dialog.run_editor_action("build")
                save.assert_not_called()
            dialog.process = None
            ide.close()

    def test_new_template_cancel_and_close_keep_unsaved_code(self):
        from modules.qt_electronics import ElectronicsDialog
        from PySide6.QtWidgets import QMainWindow, QMessageBox
        with tempfile.TemporaryDirectory() as root:
            ide = QMainWindow()
            ide.workspace = Path(root)
            dialog = ElectronicsDialog(ide)
            editor = dialog.arduino_editor
            editor.new_code()
            self.assertTrue(editor.code.document().isModified())
            original = editor.code.toPlainText()
            editor.template.setCurrentText("Eco serial")
            with patch("modules.qt_arduino_editor.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
                editor.new_code()
                with patch.object(dialog, "stop_process") as stop:
                    dialog.reject()
                    stop.assert_not_called()
            self.assertEqual(original, editor.code.toPlainText())
            editor.code.document().setModified(False)
            ide.close()

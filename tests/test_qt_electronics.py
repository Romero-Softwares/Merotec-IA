import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 indisponível")
class ElectronicsDialogTests(unittest.TestCase):
    def wait_until(self, predicate, timeout=6000):
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtTest import QTest
        timer = QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < timeout:
            QTest.qWait(10)
        self.assertTrue(predicate(), "A operação assíncrona não terminou no prazo")

    def test_process_streams_stdout_stderr_in_window_and_keeps_it_open(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            ide.start_terminal_process = Mock()
            dialog = ElectronicsDialog(ide)
            callback = Mock()
            with patch.object(dialog, "accept") as accept:
                dialog.start_process(sys.executable, ["-u", "-c", "import sys; print('saída local'); sys.stderr.write('erro local\\n')"], "Teste", temp, after=callback)
                self.wait_until(lambda: dialog.process is None)
                accept.assert_not_called()
            text = dialog.output.toPlainText()
            self.assertIn("saída local", text)
            self.assertIn("erro local", text)
            self.assertIn("Concluído", dialog.process_status.text())
            self.assertFalse(dialog.stop_button.isEnabled())
            ide.start_terminal_process.assert_not_called()
            callback.assert_called_once()
            ide.close()

    def test_busy_stop_and_close_do_not_leave_process_running(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process(sys.executable, ["-u", "-c", "import time; print('pronto'); time.sleep(30)"], "Monitor", temp)
            self.wait_until(lambda: "pronto" in dialog.output.toPlainText())
            self.assertFalse(dialog.start_process(sys.executable, [], "Duplicado", temp))
            dialog.stop_button.click()
            self.wait_until(lambda: dialog.process is None and dialog._stopper is None)
            self.assertEqual("Interrompido", dialog.process_status.text())
            dialog.start_process(sys.executable, ["-u", "-c", "import time; print('aberto'); time.sleep(30)"], "Monitor", temp)
            self.wait_until(lambda: "aberto" in dialog.output.toPlainText())
            dialog.reject()
            self.wait_until(lambda: dialog.process is None and dialog._stopper is None and not dialog._close_pending)
            ide.close()

    def test_failed_start_and_nonzero_exit_are_visible_in_output(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process(str(Path(temp) / "missing-executable"), [], "Ausente", temp)
            self.wait_until(lambda: dialog.process is None)
            self.assertIn("Falha ao iniciar", dialog.output.toPlainText())
            dialog.start_process(sys.executable, ["-c", "raise SystemExit(7)"], "Falha", temp)
            self.wait_until(lambda: dialog.process is None)
            self.assertIn("código 7", dialog.process_status.text())
            ide.close()

    def test_detected_ports_refresh_preserves_choice_and_manual_entry(self):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            with patch("modules.qt_electronics.ports", return_value=["COM3", "COM7"]):
                dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            self.assertEqual("", dialog.port.currentText())
            self.assertGreaterEqual(dialog.port.findText("COM7"), 0)
            dialog.port.setCurrentText("COM7")
            with patch("modules.qt_electronics.ports", return_value=["COM3"]):
                dialog.refresh_ports_button.click()
            self.assertEqual("COM7", dialog.port.currentText())
            self.assertIn("não foi detectada", dialog.port_status.text())
            dialog.port.setCurrentText("COM99")
            with patch("modules.qt_electronics.ports", return_value=[]):
                dialog.refresh_ports()
            self.assertEqual("COM99", dialog.port.currentText())
            self.assertIn("Nenhuma porta", dialog.port_status.text())
            with patch("modules.qt_electronics.ports", side_effect=OSError("denied")):
                dialog.refresh_ports()
            self.assertEqual("COM99", dialog.port.currentText())
            self.assertIn("Falha na detecção", dialog.port_status.text())
            ide.close()

    def test_selected_port_and_baud_are_passed_to_monitor(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            with patch("modules.qt_electronics.ports", return_value=["COM3"]):
                dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            self.assertEqual("115200", dialog.baud.currentText())
            for rate in ("9600", "57600", "115200", "921600"):
                self.assertGreaterEqual(dialog.baud.findText(rate), 0)
            dialog.port.setCurrentText("COM3")
            dialog.baud.setCurrentText("57600")
            with patch("modules.qt_electronics.shutil.which", return_value=sys.executable):
                dialog.run_tool("monitor")
            args = dialog.start_process.call_args.args[1]
            self.assertEqual("COM3", args[args.index("--port") + 1])
            self.assertEqual("57600", args[args.index("--baud") + 1])
            ide.close()

    def test_default_firmware_backend_is_python_and_install_uses_worker(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            self.assertEqual(0, dialog.backend.currentIndex())
            with patch("modules.qt_electronics.shutil.which", return_value=sys.executable):
                dialog.run_tool("install")
            call = dialog.start_process.call_args
            self.assertEqual(sys.executable, call.args[0])
            self.assertTrue(call.args[1][1].endswith("native_firmware.py"))
            self.assertIn("install", call.args[1])
            ide.close()

    def test_diagnostics_launch_in_workbench_and_load_report(self):
        import sys
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        from modules.pcb_diagnostics import diagnose, save_report
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            dialog.run_diagnostics(True)
            args = dialog.start_process.call_args.args
            self.assertEqual(sys.executable, args[0])
            self.assertEqual("--kicad", args[1][-1])
            self.assertIn(str(ide.workspace.resolve()), args[1])
            from PySide6.QtWidgets import QDialog
            self.assertEqual(QDialog.DialogCode.Rejected, QDialog.result(dialog))
            save_report(ide.workspace, diagnose(ide.workspace))
            dialog.load_diagnostic_report()
            self.assertIn("PENDING", dialog.diagnostic_report.toPlainText())
            ide.close()

    def test_calculator_and_tool_execution_use_workbench_process(self):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            dialog.backend.setCurrentText("arduino")
            dialog.compute()
            self.assertIn("0.005 A", dialog.result.text())
            dialog.operation.setCurrentIndex(2)
            self.assertIn("µF", dialog.second_label.text())
            with patch("modules.qt_electronics.shutil.which", return_value="arduino-cli"):
                dialog.run_tool("build")
            dialog.start_process.assert_called_once_with(
                "arduino-cli", ["compile", "--fqbn", "arduino:avr:uno", "."],
                "arduino-cli compile --fqbn arduino:avr:uno .", Path(temp).resolve())
            ide.close()

    def test_upload_cancellation_does_not_start_a_process(self):
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
        from modules.qt_electronics import ElectronicsDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            ide = QMainWindow()
            ide.workspace = Path(temp)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            dialog.backend.setCurrentText("arduino")
            dialog.port.setCurrentText("COM3")
            with patch("modules.qt_electronics.shutil.which", return_value="arduino-cli"), patch("modules.qt_electronics.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                dialog.run_tool("upload")
            dialog.start_process.assert_not_called()
            ide.close()

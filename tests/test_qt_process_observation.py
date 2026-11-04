import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from modules.process_runtime import ProcessObservation

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None
if QT_AVAILABLE:
    from PySide6.QtCore import QObject, QEventLoop, QTimer, QProcess
    from PySide6.QtWidgets import QApplication
    from pyside_app import MerotecIDE


@unittest.skipUnless(QT_AVAILABLE, "PySide6 indisponível")
class QtProcessObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_persistent_shell_keeps_environment_and_cwd_and_reports_real_failure(self):
        class Harness(QObject):
            _send_to_terminal_session = MerotecIDE._send_to_terminal_session
            _read_terminal_stdout = MerotecIDE._read_terminal_stdout
            _append_terminal_session_output = MerotecIDE._append_terminal_session_output
            _terminal_session_finished = MerotecIDE._terminal_session_finished
            _terminal_session_error = MerotecIDE._terminal_session_error
            _terminal_session_deadline = MerotecIDE._terminal_session_deadline
            _decode_process_output = staticmethod(MerotecIDE._decode_process_output)
            _command_requires_interactive_input = staticmethod(MerotecIDE._command_requires_interactive_input)

            def __init__(self, root):
                super().__init__()
                self.workspace = self.terminal_working_directory = root
                self.terminal_session = self.terminal_process = None
                self._terminal_session_marker = ""
                self._terminal_session_output_buffer = ""
                self._terminal_session_queue = []
                self._terminal_session_command_number = 0
                self._terminal_session_interactive = False
                self.logs = []
            def append_terminal(self, text): self.logs.append(text)
            def set_status(self, text): pass
            def _start_terminal_progress(self, *args): pass
            def _stop_terminal_progress(self, *args): pass
            def _observe_terminal_progress_output(self, *args): pass
            def _run_next_terminal_session_command(self): self.loop.quit()
            def execute(self, command):
                self.loop = QEventLoop()
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self.loop.quit)
                timer.start(8000)
                self._send_to_terminal_session(command)
                self.loop.exec()
                timer.stop()
                timer.deleteLater()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subfolder = root / "subfolder"
            subfolder.mkdir()
            ide = Harness(root)
            try:
                ide.execute("set MEROTEC_TEST_STATE=preserved" if os.name == "nt" else "export MEROTEC_TEST_STATE=preserved")
                self.assertEqual("", ide._terminal_session_marker)
                process = ide.terminal_session
                ide.execute(f'cd /d "{subfolder}"' if os.name == "nt" else f'cd "{subfolder}"')
                self.assertEqual(subfolder.resolve(), ide.terminal_working_directory)
                command = f'"{sys.executable}" -c "import os,sys; print(os.environ.get(\'MEROTEC_TEST_STATE\')); print(os.getcwd()); sys.exit(7)"'
                ide.execute(command)
                self.assertEqual("", ide._terminal_session_marker, "A sessão não entregou o resultado do comando")
                self.assertIs(process, ide.terminal_session)
                self.assertIn("preserved", "".join(ide.logs))
                self.assertIn(str(subfolder), "".join(ide.logs))
                entries = json.loads((root / ".merotec_system_ai/execution_lessons.json").read_text(encoding="utf-8"))["entries"]
                self.assertEqual(7, entries[-1]["exit_code"])
                self.assertEqual("failed", entries[-1]["status"])
            finally:
                process = ide.terminal_session
                if process is not None:
                    process.write(b"exit\r\n")
                    if not process.waitForFinished(1000):
                        process.kill()
                        self.assertTrue(process.waitForFinished(3000), "O shell de teste não encerrou")
                ide.deleteLater()

    def test_split_completion_marker_does_not_publish_success_before_exit_code_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ide = Mock()
            ide._terminal_session_marker = "__MEROTEC_TEST_DONE__"
            ide._terminal_session_output_buffer = ""
            ide._terminal_session_observation = ProcessObservation("compile fixture", root)
            MerotecIDE._append_terminal_session_output(ide, "failed output\n__MEROTEC_TEST_DONE__7|")
            self.assertFalse(ide._terminal_session_observation.finished)
            MerotecIDE._append_terminal_session_output(ide, str(root) + "\r\n")
            entries = json.loads((root / ".merotec_system_ai/execution_lessons.json").read_text(encoding="utf-8"))["entries"]
            self.assertEqual("failed", entries[-1]["status"])
            self.assertEqual(7, entries[-1]["exit_code"])
            self.assertEqual("", ide._terminal_session_marker)

    def test_qprocess_launch_uses_common_observer_and_actual_exit_status(self):
        class Harness(QObject):
            start_terminal_process = MerotecIDE.start_terminal_process
            _terminal_finished = MerotecIDE._terminal_finished
            _terminal_error = MerotecIDE._terminal_error
            _terminal_deadline = MerotecIDE._terminal_deadline
            _read_terminal_stdout = MerotecIDE._read_terminal_stdout
            _read_terminal_stderr = MerotecIDE._read_terminal_stderr
            _decode_process_output = staticmethod(MerotecIDE._decode_process_output)
            def __init__(self, root):
                super().__init__()
                self.workspace = self.terminal_working_directory = root
                self.terminal_process = self.terminal_session = None
                self.terminal_input = Mock()
                self.logs = []
            def append_terminal(self, text): self.logs.append(text)
            def _start_terminal_progress(self, *args): pass
            def _stop_terminal_progress(self, *args): pass
            def _observe_terminal_progress_output(self, *args): pass
            def _terminal_prompt(self): pass
            def set_status(self, *args): pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ide = Harness(root)
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            self.assertTrue(ide.start_terminal_process(sys.executable, ["-c", "print('compiled'); raise SystemExit(4)"], "compile fixture"))
            process = ide.terminal_process
            observation = process._merotec_observation
            process.finished.connect(loop.quit)
            timer.start(8000)
            loop.exec()
            timer.stop()
            self.assertTrue(observation.finished)
            self.assertEqual(4, observation.result["exit_code"])
            self.assertEqual("failed", observation.result["status"])
            self.assertIn("compiled", "".join(ide.logs))
            ide.deleteLater()


if __name__ == "__main__":
    unittest.main()

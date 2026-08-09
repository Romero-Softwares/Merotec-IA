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
    def test_terminal_output_accepts_utf8(self):
        self.assertEqual(MerotecIDE._decode_process_output("configuração pronta".encode("utf-8")), "configuração pronta")

    def test_terminal_output_accepts_windows_code_pages(self):
        self.assertEqual(MerotecIDE._decode_process_output("configuração pronta".encode("cp1252")), "configuração pronta")

    def test_python_command_is_unbuffered_only_once(self):
        self.assertEqual(MerotecIDE._make_python_output_unbuffered("python main.py"), "python -u main.py")
        self.assertEqual(MerotecIDE._make_python_output_unbuffered("python -u main.py"), "python -u main.py")

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

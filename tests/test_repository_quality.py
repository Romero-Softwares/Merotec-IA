import ast
import importlib
import pathlib
import re
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RepositoryQualityTests(unittest.TestCase):
    def test_engine_has_no_shadowed_methods(self):
        tree = ast.parse((ROOT / "modules" / "engine.py").read_text(encoding="utf-8"))
        engine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UniversalEngine")
        methods = [node.name for node in engine.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        duplicates = sorted({name for name in methods if methods.count(name) > 1})
        self.assertEqual(duplicates, [])

    def test_windows_launcher_is_portable(self):
        launcher = (ROOT / "init_System.cmd").read_text(encoding="utf-8").lower()
        self.assertIn('cd /d "%~dp0"', launcher)
        self.assertIn('"venv\\scripts\\python.exe" main.py', launcher)
        self.assertNotIn("c:\\users\\", launcher)
        self.assertNotIn("set \"tcl_library=", launcher)

    def test_chat_and_terminal_cancel_commands_are_separate(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UniversalApp")
        methods = {node.name: node for node in app.body if isinstance(node, ast.FunctionDef)}

        cancel_ai_calls = {
            node.func.attr
            for node in ast.walk(methods["cancel_ai_task"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        interrupt_calls = {
            node.func.attr
            for node in ast.walk(methods["interrupt_terminal_from_keyboard"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertNotIn("cancel_active_terminal_processes", cancel_ai_calls)
        self.assertIn("cancel_generation", cancel_ai_calls)
        self.assertIn("cancel_terminal_command", interrupt_calls)
        self.assertNotIn("cancel_ai_task", interrupt_calls)
        self.assertIn("Cancelar terminal", source)

    def test_code_transport_exports_working_validators(self):
        transport = importlib.import_module("modules.code_transport")

        self.assertTrue(callable(transport.unwrap_transport_code))
        self.assertTrue(callable(transport.validate_source_text))
        self.assertTrue(callable(transport.validate_source))
        self.assertTrue(callable(transport.fenced_transport_instruction))
        self.assertIsNone(transport.validate_source("app.py", "def main():\n    return 1\n"))

        issue = transport.validate_source("app.py", "def main():\n\treturn 1\n")
        self.assertIsNotNone(issue)
        self.assertEqual(issue["kind"], "TabIndentationRejected")

        instruction = transport.fenced_transport_instruction("app.py")
        self.assertIn("PROTOCOLO INCREMENTAL V9", instruction)
        self.assertIn("[REPLACE: caminho/arquivo]", instruction)

    def test_settings_dialog_expands_scrollable_body(self):
        source = (ROOT / "modules" / "ai_config.py").read_text(encoding="utf-8")

        self.assertIn("dialog.grid_rowconfigure(3, weight=1)", source)
        self.assertNotIn("dialog.grid_rowconfigure(2, weight=1)", source)
        self.assertIn('body.grid(row=3, column=0, sticky="nsew"', source)

    def test_plugin_manager_is_connected_to_app_startup(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UniversalApp")
        methods = {node.name: node for node in app.body if isinstance(node, ast.FunctionDef)}
        init_calls = {
            node.func.attr
            for node in ast.walk(methods["__init__"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertIn("load_plugins", init_calls)
        self.assertIn("report_plugin_status", init_calls)
        self.assertIn("initialize_plugins", source)
        self.assertIn('"engine": self.engine', source)
        self.assertIn('"executor": self.executor', source)
        plugin_manager = (ROOT / "modules" / "plugin_manager.py").read_text(encoding="utf-8")
        self.assertIn("def load_installed_plugins", plugin_manager)
        self.assertIn("def initialize_plugins", plugin_manager)

    def test_requirements_do_not_pin_stdlib_modules(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        packages = {
            line.split(";", 1)[0].split("==", 1)[0].strip().lower()
            for line in requirements
            if line.strip() and not line.lstrip().startswith("#")
        }
        stdlib = {name.lower() for name in getattr(sys, "stdlib_module_names", set())}

        self.assertFalse(packages & stdlib)

    def test_sensitive_recovery_codes_are_ignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("PyPI-Recovery-Codes-*.txt", gitignore)

    def test_local_state_and_operational_artifacts_are_ignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for pattern in (
            "history.json",
            "change_history.json",
            "ide_settings.json",
            ".merotec_attachments/",
            ".merotec_backups/",
            ".merotec_local_ai/",
            ".merotec_patch_backups/",
            ".merotec_system_ai/",
            "*.bak",
            "*.tmp",
        ):
            self.assertIn(pattern, gitignore)

    def test_sensitive_recovery_codes_are_not_left_in_workspace(self):
        leaked_files = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.glob("PyPI-Recovery-Codes-*.txt")
            if path.is_file()
        ]

        self.assertEqual([], leaked_files)

    def test_critical_ai_dependencies_are_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

        for package in ("openai", "llama-cpp-python"):
            self.assertRegex(requirements, rf"(?m)^{re.escape(package)}==[0-9][^\s;]*")

    def test_random_temp_directories_are_ignored(self):
        constants = importlib.import_module("modules.app_constants")

        self.assertTrue(constants.is_ignored_dir_name("tmpy10392w4"))
        self.assertTrue(constants.is_ignored_dir_name("TMPABC_123"))
        self.assertFalse(constants.is_ignored_dir_name("templates"))
        self.assertFalse(constants.is_ignored_dir_name("tmp"))

    def test_architecture_document_exists(self):
        architecture = ROOT / "docs" / "architecture.md"

        self.assertTrue(architecture.is_file())
        text = architecture.read_text(encoding="utf-8")
        for section in (
            "## Visao geral",
            "## Fluxo de IA",
            "## Execucao e validacao",
            "## Navegador interno",
            "## Plugins",
            "## Memoria local e RAG",
            "## Dados locais e seguranca",
        ):
            self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main()

import ast
import pathlib
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


if __name__ == "__main__":
    unittest.main()

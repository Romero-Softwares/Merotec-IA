"""Guard against adding a new unmanaged workspace process entry point."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProcessGatewayCoverageTests(unittest.TestCase):
    def test_process_launches_use_gateway_or_documented_owned_runner(self):
        exceptions = {ROOT / "modules/process_runtime.py", ROOT / "modules/project_test_runner.py"}
        violations = []
        paths = [ROOT / "main.py", ROOT / "pyside_app.py", *sorted((ROOT / "modules").glob("*.py"))]
        for path in paths:
            if path in exceptions:
                continue
            source = ast.parse(path.read_text(encoding="utf-8-sig"))
            raw_names = set()
            for node in ast.walk(source):
                if isinstance(node, ast.Import):
                    raw_names.update(alias.asname or alias.name for alias in node.names if alias.name == "subprocess")
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    violations.append(f"{path.name}:{node.lineno}: import direto de executor subprocess")
            for node in ast.walk(source):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in raw_names and node.func.attr in {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}):
                    violations.append(f"{path.name}:{node.lineno}: {node.func.attr} sem observação")
        self.assertEqual([], violations)

    def test_qprocess_entry_points_are_centralized(self):
        allowed = {("pyside_app.py", "_send_to_terminal_session"), ("pyside_app.py", "start_terminal_process"),
                   ("qt_electronics.py", "start_process"), ("qt_electronics.py", "stop_process")}
        found = set()
        paths = [ROOT / "pyside_app.py", *sorted((ROOT / "modules").glob("*.py"))]
        for path in paths:
            for function in (node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))) if isinstance(node, ast.FunctionDef)):
                if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "QProcess" for node in ast.walk(function)):
                    found.add((path.name, function.name))
        self.assertEqual(allowed, found)


if __name__ == "__main__":
    unittest.main()

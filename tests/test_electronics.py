import tempfile
import unittest
from pathlib import Path

from modules.electronics import calculate, tool_arguments, detect_embedded, EMBEDDED_KINDS
from modules.project_manager import ProjectManager
from modules.project_loader import ProjectLoader
from modules.language_guard import detect_workspace_language, workspace_contract
from modules.agent_skills import AgentSkillRegistry
from modules.editor_intelligence import completion_items


class ElectronicsTests(unittest.TestCase):
    def test_calculations_and_invalid_inputs(self):
        self.assertIn("0.005 A", calculate("ohm", 5, 1000))
        self.assertIn("150 Ω", calculate("led", 3, 20))
        self.assertIn("0.001 s", calculate("rc", 1000, 1))
        self.assertIn("24 W", calculate("power", 12, 2))
        for value in (0, -1, "nan", "inf", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate("ohm", value, 100)

    def test_upload_compiles_and_requires_explicit_port(self):
        for backend in ("arduino", "platformio"):
            with self.assertRaises(ValueError):
                tool_arguments(backend, "upload")
        args = tool_arguments("arduino", "upload", port="COM3")
        self.assertEqual(args[:2], ["arduino-cli", "compile"])
        self.assertIn("--upload", args)
        self.assertIn("COM3", args)
        self.assertEqual(tool_arguments("platformio", "build"), ["pio", "run"])

    def test_arguments_are_not_interpreted_as_shell(self):
        port = "COM3; echo injected"
        self.assertIn(port, tool_arguments("arduino", "monitor", port=port))

    def test_all_starters_have_engineering_artifacts_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ProjectManager(temp)
            for kind in EMBEDDED_KINDS:
                root = manager.create_project(temp, kind + " project", kind)
                self.assertTrue((root / "hardware/bom.csv").is_file())
                self.assertTrue((root / "docs/engineering.md").is_file())
                self.assertTrue((root / "tests/bench-plan.csv").is_file())
                expected = "platformio" if kind in {"esp32", "stm32"} else kind
                self.assertEqual(expected, detect_embedded(root))
                self.assertEqual(expected, ProjectLoader.detect_project_type(root))
                self.assertEqual(expected, detect_workspace_language(root))
                self.assertTrue(workspace_contract(root))
                with self.assertRaises(FileExistsError):
                    manager.create_project(temp, root.name, kind)
            self.assertTrue((Path(temp) / "arduino project/firmware/firmware.ino").exists())

    def test_specialist_routes_and_arduino_completions(self):
        registry = AgentSkillRegistry()
        for prompt in ("desenvolva circuito eletrônico", "firmware Arduino", "automação Modbus", "PCB KiCad"):
            self.assertEqual("electrical-electronics-engineer", registry.resolve(prompt).skill.id)
        self.assertIn("digitalWrite", completion_items("digitalW", "firmware.ino"))


if __name__ == "__main__":
    unittest.main()

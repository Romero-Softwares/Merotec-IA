import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES
from modules.workspace_intelligence import WorkspaceIntelligenceMixin


class DummyWorkspace(WorkspaceIntelligenceMixin):
    def __init__(self):
        self.current_workspace = str(PROJECT_ROOT)

    def iter_workspace_files(self, limit=500):
        workspace = Path(self.current_workspace)
        count = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [name for name in sorted(dirs) if name not in IGNORED_DIRS and not name.startswith(".")]
            root_path = Path(root)
            for filename in sorted(files):
                path = root_path / filename
                if filename.startswith(".") or path.suffix.lower() in IGNORED_SUFFIXES:
                    continue
                try:
                    rel = path.relative_to(workspace)
                except ValueError:
                    continue
                yield path, rel
                count += 1
                if count >= limit:
                    return


class AutonomousDiscoveryTriggersTest(unittest.TestCase):
    def setUp(self):
        self.app = DummyWorkspace()

    def test_discovery_mission_returns_three_trigger_categories_without_question(self):
        command = (
            "a meta e retirar completamente o ser humano da etapa de interacao e descoberta "
            "com testes de estresse autonomos; existem exatamente tres categorias de gatilhos"
        )

        reply = self.app.local_autonomous_task(command, self.app.normalize_plain_text(command))

        self.assertIsNotNone(reply)
        self.assertIn("Tres categorias de gatilho", reply)
        self.assertIn("Gatilho de intencao", reply)
        self.assertIn("Gatilho de evidencia", reply)
        self.assertIn("Gatilho de restricao", reply)
        self.assertIn("Acao autonoma escolhida", reply)
        self.assertNotIn("?", reply)
        self.assertEqual(3, len(self.app.autonomous_discovery_trigger_categories()))

    def test_trigger_classifier_prefers_strongest_signal(self):
        cases = [
            ("meta do usuario com requisito de produto", "intencao"),
            ("erro de build apareceu no teste de estresse", "evidencia"),
            ("comando bloqueado por permissao de administrador e UAC", "restricao"),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    expected,
                    self.app.classify_autonomous_discovery_trigger(text),
                )


if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES
from modules.workspace_intelligence import WorkspaceIntelligenceMixin


class DummySmartWorkspace(WorkspaceIntelligenceMixin):
    def __init__(self, workspace):
        self.current_workspace = str(workspace)
        self.open_editors = {}

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

    def resolve_workspace_path(self, raw_path):
        return (Path(self.current_workspace) / raw_path).resolve()


class SmartTaskBriefTest(unittest.TestCase):
    def test_classifies_common_intents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummySmartWorkspace(Path(temp_dir))

            self.assertEqual("corrigir", app.classify_smart_task_intent("corrija erro atual"))
            self.assertEqual("implementar", app.classify_smart_task_intent("torne a IDE mais inteligente"))
            self.assertEqual("validar", app.classify_smart_task_intent("rode os testes"))
            self.assertEqual("configurar", app.classify_smart_task_intent("configure OpenRouter"))

    def test_smart_brief_points_to_candidate_files_and_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (workspace / "modules").mkdir()
            (workspace / "modules" / "engine.py").write_text("class Engine: pass\n", encoding="utf-8")
            (workspace / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            app = DummySmartWorkspace(workspace)

            brief = app.build_smart_task_brief("corrija erro em modules/engine.py")

            self.assertIn("BRIEFING INTELIGENTE DA IDE", brief)
            self.assertIn("Intencao detectada: corrigir", brief)
            self.assertIn("modules/engine.py", brief)
            self.assertIn("-m compileall", brief)
            self.assertIn("Leia o trecho exato", brief)

    def test_capability_question_replies_without_starting_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummySmartWorkspace(Path(temp_dir))

            reply = app.local_quick_reply("vc consegue fazer o deploy para o repositorio do github agora?")

            self.assertIsNotNone(reply)
            self.assertIn("consigo ajudar com o deploy", reply)
            self.assertIn("pedido direto", reply)
            self.assertTrue(app.is_answer_only_question("vc consegue fazer o deploy para o repositorio do github agora?"))

    def test_direct_deploy_task_is_not_answer_only_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummySmartWorkspace(Path(temp_dir))

            self.assertFalse(app.is_answer_only_question("faca o deploy para GitHub Pages"))
            self.assertIsNone(app.local_quick_reply("faca o deploy para GitHub Pages"))


if __name__ == "__main__":
    unittest.main()

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
            self.assertIn("-q main.py modules", brief)
            self.assertIn("venv", brief)
            self.assertIn("Leia o trecho exato", brief)

    def test_smart_brief_distinguishes_flutter_dart_and_flet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            flutter_workspace = Path(temp_dir) / "flutter_app"
            flutter_workspace.mkdir()
            (flutter_workspace / "pubspec.yaml").write_text(
                "name: flutter_app\ndependencies:\n  flutter:\n    sdk: flutter\n",
                encoding="utf-8",
            )
            (flutter_workspace / "lib").mkdir()
            (flutter_workspace / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
            flutter_app = DummySmartWorkspace(flutter_workspace)

            flutter_brief = flutter_app.build_smart_task_brief("implemente a tela em lib/main.dart")

            self.assertIn("Tipo de projeto detectado: flutter", flutter_brief)
            self.assertIn("flutter analyze", flutter_brief)
            self.assertIn("lib/main.dart", flutter_brief)

            dart_workspace = Path(temp_dir) / "dart_cli"
            dart_workspace.mkdir()
            (dart_workspace / "pubspec.yaml").write_text(
                "name: dart_cli\nenvironment:\n  sdk: ^3.0.0\n",
                encoding="utf-8",
            )
            (dart_workspace / "bin").mkdir()
            (dart_workspace / "bin" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
            dart_app = DummySmartWorkspace(dart_workspace)

            dart_brief = dart_app.build_smart_task_brief("corrija bin/main.dart")

            self.assertIn("Tipo de projeto detectado: dart", dart_brief)
            self.assertIn("dart analyze", dart_brief)

            flet_workspace = Path(temp_dir) / "flet_app"
            flet_workspace.mkdir()
            (flet_workspace / "requirements.txt").write_text("flet\n", encoding="utf-8")
            (flet_workspace / "main.py").write_text("import flet as ft\n", encoding="utf-8")
            flet_app = DummySmartWorkspace(flet_workspace)

            self.assertEqual("flet", flet_app.detect_run_kind(flet_workspace))
            self.assertIn("Tipo detectado: Flet/Python", flet_app.local_project_summary())

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

    def test_zoom_mobile_check_does_not_treat_scale_and_fov_as_zoom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "game.js").write_text(
                "\n".join(
                    [
                        "const isMobile = window.innerWidth < 700;",
                        "const fov = Math.PI / 3;",
                        "const scale = canvas.width / 320;",
                        "function renderFrame() { return fov * scale; }",
                    ]
                ),
                encoding="utf-8",
            )
            app = DummySmartWorkspace(workspace)

            reply = app.verify_zoom_mobile_locally("verifique se existe zoom mobile em game.js")

            self.assertIn("nao encontrei funcao clara de zoom", reply)
            self.assertNotIn("Sim, encontrei sinais de logica de zoom", reply)
            self.assertIn("nao confirmam zoom mobile", reply)


if __name__ == "__main__":
    unittest.main()

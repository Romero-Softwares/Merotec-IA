import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.ai_profiles import ensure_ai_profiles
from modules.app_state import AppStateMixin
from modules.language_guard import detect_workspace_language
from modules.project_loader import ProjectLoader
from modules.project_manager import ProjectManager


class ProjectManagerTests(unittest.TestCase):
    def test_create_python_project_with_starter_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            project = manager.create_project(temp_dir, "meu-app", "python")

            self.assertTrue((project / "main.py").is_file())
            self.assertTrue((project / "README.md").is_file())
            self.assertTrue((project / "tests").is_dir())

    def test_create_web_project_with_starter_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            project = manager.create_project(temp_dir, "site", "web")

            self.assertTrue((project / "index.html").is_file())
            self.assertTrue((project / "style.css").is_file())
            self.assertTrue((project / "app.js").is_file())

    def test_create_flet_project_with_starter_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            project = manager.create_project(temp_dir, "painel", "flet")

            self.assertTrue((project / "main.py").is_file())
            self.assertIn("flet", (project / "requirements.txt").read_text(encoding="utf-8"))
            self.assertEqual("flet_python", detect_workspace_language(project))
            self.assertEqual("flet", ProjectLoader.detect_project_type(project))

    def test_create_dart_and_flutter_projects_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            dart_project = manager.create_project(temp_dir, "cli-dart", "dart")
            flutter_project = manager.create_project(temp_dir, "app-flutter", "flutter")

            self.assertTrue((dart_project / "bin" / "main.dart").is_file())
            self.assertTrue((flutter_project / "lib" / "main.dart").is_file())
            self.assertEqual("dart", detect_workspace_language(dart_project))
            self.assertEqual("flutter_dart", detect_workspace_language(flutter_project))
            self.assertEqual("dart", ProjectLoader.detect_project_type(dart_project))
            self.assertEqual("flutter", ProjectLoader.detect_project_type(flutter_project))

    def test_existing_project_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            existing = Path(temp_dir) / "existente"
            existing.mkdir()

            with self.assertRaises(FileExistsError):
                manager.create_project(temp_dir, "existente", "python")

    def test_invalid_project_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(temp_dir)
            with self.assertRaises(ValueError):
                manager.create_project(temp_dir, "pasta/projeto", "empty")


class WorkspaceRestoreTests(unittest.TestCase):
    class DummyState(AppStateMixin):
        def __init__(self, settings, workspace=None):
            self.settings = ensure_ai_profiles(settings)
            self.current_workspace = str(workspace or "")
            self.internal_browser_url = "about:blank"
            self.web_chat_restore_url = "stale"
            self.web_chat_workspace_key = "stale"
            self.opened = []

        def _save_settings(self):
            pass

        def open_internal_browser(self, url, source=""):
            self.opened.append((url, source))
            self.internal_browser_url = url

        def log_agent(self, _message):
            pass

    def test_last_external_workspace_is_restored_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ide"
            projects = root / "projects"
            last_project = Path(temp_dir) / "ultimo-projeto"
            projects.mkdir(parents=True)
            last_project.mkdir()
            state = self.DummyState({"last_workspace": str(last_project), "recent_projects": []})

            with patch("modules.app_state.PROJECT_ROOT", root), patch("modules.app_state.DEFAULT_WORKSPACE", projects):
                selected = state._initial_workspace()

            self.assertEqual(last_project.resolve(), selected)

    def test_ide_root_can_be_restored_as_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ide"
            projects = root / "projects"
            recent = Path(temp_dir) / "projeto-recente"
            projects.mkdir(parents=True)
            recent.mkdir()
            state = self.DummyState(
                {"last_workspace": str(root), "recent_projects": [str(root), str(recent)]}
            )

            with patch("modules.app_state.PROJECT_ROOT", root), patch("modules.app_state.DEFAULT_WORKSPACE", projects):
                selected = state._initial_workspace()

            self.assertEqual(root.resolve(), selected)

    def test_codex_profile_does_not_associate_workspace_with_web_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            workspace.mkdir()
            state = self.DummyState(
                {
                    "ai_provider": "codex",
                    "active_ai_profile": "codex",
                    "ai_profiles": {
                        "web_chat": {
                            "web_chat_url": "https://chatgpt.com/",
                            "web_chat_restore_project_session": True,
                        },
                    },
                },
                workspace=workspace,
            )

            target = state.activate_workspace_web_chat_session()

            self.assertEqual("", target)
            self.assertEqual("", state.web_chat_restore_url)
            self.assertEqual("", state.web_chat_workspace_key)
            self.assertEqual([], state.opened)


if __name__ == "__main__":
    unittest.main()

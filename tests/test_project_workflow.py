import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.app_state import AppStateMixin
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
        def __init__(self, settings):
            self.settings = settings

        def _save_settings(self):
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


if __name__ == "__main__":
    unittest.main()

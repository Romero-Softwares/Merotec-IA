import tempfile
import unittest
from pathlib import Path

from modules.workspace_change_tracking import capture_workspace_snapshot, changed_workspace_paths


class WorkspaceChangeTrackingTests(unittest.TestCase):
    def test_detects_created_modified_and_deleted_workspace_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            changed = workspace / "changed.py"
            removed = workspace / "removed.py"
            changed.write_text("before\n", encoding="utf-8")
            removed.write_text("remove\n", encoding="utf-8")
            before = capture_workspace_snapshot(workspace)

            changed.write_text("after and longer\n", encoding="utf-8")
            removed.unlink()
            created = workspace / "created.py"
            created.write_text("new\n", encoding="utf-8")
            after = capture_workspace_snapshot(workspace)

            paths = {path.relative_to(workspace).as_posix() for path in changed_workspace_paths(before, after)}
            self.assertEqual({"changed.py", "removed.py", "created.py"}, paths)

    def test_ignores_internal_agent_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            before = capture_workspace_snapshot(workspace)
            artifact = workspace / ".merotec_system_ai" / "output.txt"
            artifact.parent.mkdir()
            artifact.write_text("internal", encoding="utf-8")
            after = capture_workspace_snapshot(workspace)

            self.assertEqual([], changed_workspace_paths(before, after))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.agent_actions import _merotec_v7_metric, _merotec_v7_workspace_signature


class WorkspaceSignatureTests(unittest.TestCase):
    def test_ignored_directories_are_pruned_before_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'main.py'
            source.write_text('print(1)', encoding='utf-8')
            app = SimpleNamespace(current_workspace=directory)
            visited = []

            def walk(*args, **kwargs):
                directories = ['venv', '.git']
                yield str(root), directories, ['main.py']
                visited.extend(directories)
                for name in directories:
                    yield str(root / name), [], ['ignored.py']

            with patch('modules.agent_actions.os.walk', side_effect=walk):
                signature = _merotec_v7_workspace_signature(app)
            self.assertEqual([], visited)
            source.write_text('print(123)', encoding='utf-8')
            self.assertNotEqual(signature, _merotec_v7_workspace_signature(app))

    def test_cached_metric_does_not_rescan_workspace(self):
        metrics = {'v7_workspace_signature': 'cached'}
        app = SimpleNamespace(get_ai_task_metrics=lambda task_id: metrics)
        with patch('modules.agent_actions._merotec_v7_workspace_signature') as signature:
            self.assertIs(metrics, _merotec_v7_metric(app, 42))
        signature.assert_not_called()


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from modules.json_store import atomic_write_json, load_json_file


class JsonStoreTests(unittest.TestCase):
    def test_atomic_write_json_replaces_file_with_complete_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text('{"old": true}', encoding="utf-8")

            self.assertTrue(atomic_write_json(path, {"novo": "valor"}))

            self.assertEqual({"novo": "valor"}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(list(path.parent.glob(".settings.json.*.tmp")))

    def test_load_json_file_backs_up_invalid_json_and_returns_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ide_settings.json"
            path.write_text('{"incompleto": ', encoding="utf-8")

            loaded = load_json_file(path, {"ok": True}, dict)

            self.assertEqual({"ok": True}, loaded)
            backups = list(path.parent.glob("ide_settings.json.corrupt-*.bak"))
            self.assertEqual(1, len(backups))
            self.assertEqual('{"incompleto": ', backups[0].read_text(encoding="utf-8"))

    def test_load_json_file_rejects_unexpected_root_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.json"
            path.write_text('{"not": "a list"}', encoding="utf-8")

            self.assertEqual([], load_json_file(path, [], list))

    def test_atomic_write_json_cleans_temp_file_when_payload_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text('{"old": true}', encoding="utf-8")

            self.assertFalse(atomic_write_json(path, {"bad": object()}))

            self.assertEqual({"old": True}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(list(path.parent.glob(".settings.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()

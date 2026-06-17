import inspect
import unittest
from types import SimpleNamespace

from main import UniversalApp


class EditorInteractionTests(unittest.TestCase):
    def _event(self, keysym, char="", state=0):
        return SimpleNamespace(keysym=keysym, char=char, state=state)

    def test_navigation_keys_do_not_count_as_text_changes(self):
        for keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
            with self.subTest(keysym=keysym):
                event = self._event(keysym)
                self.assertFalse(UniversalApp._is_editor_text_change_event(None, event))

    def test_text_editing_keys_count_as_text_changes(self):
        cases = [
            self._event("a", "a"),
            self._event("space", " "),
            self._event("BackSpace"),
            self._event("Delete"),
            self._event("Return", "\r"),
            self._event("v", state=0x4),
            self._event("x", state=0x4),
        ]
        for event in cases:
            with self.subTest(keysym=event.keysym, char=event.char, state=event.state):
                self.assertTrue(UniversalApp._is_editor_text_change_event(None, event))

    def test_create_editor_configures_horizontal_scroll(self):
        source = inspect.getsource(UniversalApp._create_editor)
        self.assertIn('orientation="horizontal"', source)
        self.assertIn("command=editor.xview", source)
        self.assertIn("xscrollcommand=sync_horizontal_scroll", source)
        self.assertIn("wrap=\"none\"", source)
        self.assertNotIn("show_editor_completion(event, name)", source)


if __name__ == "__main__":
    unittest.main()

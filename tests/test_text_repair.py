import inspect
import unittest

from modules.agent_actions import AgentActionsMixin


class TextRepairTest(unittest.TestCase):
    def setUp(self):
        self.app = AgentActionsMixin()

    def test_common_mojibake_is_detected_and_repaired(self):
        broken = "A memoria da IA estÃ¡ pronta para a prÃ³xima aÃ§Ã£o."

        self.assertGreater(self.app.mojibake_score(broken), 0)
        self.assertEqual(
            "A memoria da IA está pronta para a próxima ação.",
            self.app.repair_common_mojibake(broken),
        )

    def test_text_repair_methods_are_not_duplicated_in_source(self):
        source = inspect.getsource(AgentActionsMixin)

        self.assertEqual(1, source.count("def mojibake_score("))
        self.assertEqual(1, source.count("def apply_mojibake_map("))


if __name__ == "__main__":
    unittest.main()

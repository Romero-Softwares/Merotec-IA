import unittest

from modules.agent_skills import (
    AgentSkillRegistry,
    READ_ONLY_AGENT_ACTIONS,
    SUPPORTED_AGENT_ACTIONS,
)


class AgentSkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentSkillRegistry()

    def test_auto_selects_merotec_maintainer_for_internal_agent_work(self):
        resolution = self.registry.resolve("corrija o agente interno PySide da IDE Merotec", "python")

        self.assertEqual("merotec-ide-maintainer", resolution.skill.id)
        self.assertEqual("auto", resolution.source)

    def test_auto_selects_read_only_security_review(self):
        resolution = self.registry.resolve("faça uma auditoria de segurança procurando segredos", "python")

        self.assertEqual("security-review-readonly", resolution.skill.id)
        self.assertEqual(READ_ONLY_AGENT_ACTIONS, resolution.skill.allowed_actions)

    def test_manual_selection_consumes_prefix_and_has_priority(self):
        resolution = self.registry.resolve("/skill web-ui-visual corrija o traceback Python", "python")

        self.assertEqual("web-ui-visual", resolution.skill.id)
        self.assertEqual("manual", resolution.source)
        self.assertEqual("corrija o traceback Python", resolution.clean_prompt)

    def test_unknown_manual_selection_falls_back_to_deterministic_routing(self):
        resolution = self.registry.resolve("/skill inexistente corrija este bug", "python")

        self.assertEqual("debug-test-repair", resolution.skill.id)
        self.assertEqual("auto", resolution.source)

    def test_every_skill_only_exposes_actions_implemented_by_qt_executor(self):
        for skill in self.registry.list():
            with self.subTest(skill=skill.id):
                self.assertTrue(skill.allowed_actions)
                self.assertTrue(skill.allowed_actions <= SUPPORTED_AGENT_ACTIONS)

    def test_rendered_context_lists_only_selected_contract(self):
        resolution = self.registry.resolve("/skill architecture-review-readonly avalie o projeto")

        context = self.registry.render_context(resolution)

        self.assertIn("Revisão de arquitetura", context)
        self.assertIn("READ, SEARCH_TEXT", context)
        self.assertNotIn("EXECUTE", context)


if __name__ == "__main__":
    unittest.main()

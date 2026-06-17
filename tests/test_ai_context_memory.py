import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import UniversalApp


def bare_app():
    app = UniversalApp.__new__(UniversalApp)
    app.current_task_id = 7
    app.active_ai_objective = "Corrigir a IDE para preservar contexto entre rodadas"
    app.last_response = "Vou continuar com [READ: main.py]"
    app.ai_context_memory = []
    app.format_recent_changes_for_agent = lambda limit=10: "- main.py alterado"
    return app


class AiContextMemoryTest(unittest.TestCase):
    def test_continuation_request_uses_active_objective(self):
        app = bare_app()

        self.assertTrue(app.should_continue_active_ai_task("continue de onde parou"))
        self.assertTrue(app.should_continue_active_ai_task("faca isso"))
        self.assertFalse(app.should_continue_active_ai_task("crie um projeto novo"))

    def test_recent_context_memory_is_compact_and_ordered(self):
        app = bare_app()

        app.remember_ai_context_message("Voce", "Verifique por que a IA perde contexto.")
        app.remember_ai_context_message("Merotec IA", "Achei que a thread do Codex e efemera.")

        context = app.build_recent_ai_context_memory(limit=4)

        self.assertIn("Voce: Verifique por que a IA perde contexto.", context)
        self.assertIn("Merotec IA: Achei que a thread do Codex e efemera.", context)

    def test_continuation_context_includes_objective_last_response_and_chat(self):
        app = bare_app()
        app.remember_ai_context_message("Voce", "continue")
        app.remember_ai_context_message("Merotec IA", "A ultima acao foi revisar main.py.")

        context = app.build_active_task_continuation_context("continue")

        self.assertIn("Missao ativa anterior", context)
        self.assertIn(app.active_ai_objective, context)
        self.assertIn("Ultima resposta visivel da IA", context)
        self.assertIn("A ultima acao foi revisar main.py.", context)
        self.assertIn("nao trate o pedido atual como tarefa isolada", context)


if __name__ == "__main__":
    unittest.main()

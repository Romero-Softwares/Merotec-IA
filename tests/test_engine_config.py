import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules import config
from modules.engine import UniversalEngine


class EngineConfigTest(unittest.TestCase):
    def test_config_exports_legacy_constants_used_by_engine(self):
        for name in (
            "AI_PROVIDER",
            "CODEX_MODEL_NAME",
            "CODEX_REASONING_EFFORT",
            "OPENAI_API_KEY",
            "OPENAI_MODEL_NAME",
            "GOOGLE_API_KEY",
            "MODEL_NAME",
            "LANGUAGE",
        ):
            self.assertTrue(hasattr(config, name), name)

    def test_resolve_model_id_uses_provider_specific_model(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._model_cache = {}
        engine.provider = "openai"
        engine.codex_model_name = "gpt-5.5"
        engine.openai_model_name = "deepseek/deepseek-chat"
        engine.google_model_name = "gemini-2.5-flash"

        self.assertEqual("deepseek/deepseek-chat", engine._resolve_model_id())

        engine.provider = "google"
        self.assertEqual("gemini-2.5-flash", engine._resolve_model_id())

    def test_generate_solution_uses_external_fallback_when_provider_fails(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.external_ai_fallback_enabled = True
        engine.cancel_requested = False
        engine.configured_external_ai_fallback_providers = lambda: ["google"]
        engine._snapshot_provider_state = lambda: {}
        engine._restore_provider_state = lambda _state: None
        engine._activate_provider = lambda _provider: None

        calls = []

        def fake_generate(provider, *args, **kwargs):
            calls.append(provider)
            if provider == "openai":
                return "Sua chave foi aceita, mas a conta/projeto esta sem cota disponivel."
            return "Resposta vinda do Gemini."

        engine._generate_solution_with_provider = fake_generate

        response = engine.generate_solution("pergunta complexa")

        self.assertEqual(["openai", "google"], calls)
        self.assertIn("Fallback externo: Gemini", response)
        self.assertIn("Resposta vinda do Gemini.", response)

    def test_generate_solution_does_not_fallback_when_answer_is_valid(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.external_ai_fallback_enabled = True
        engine.cancel_requested = False
        engine.configured_external_ai_fallback_providers = lambda: ["google"]

        calls = []

        def fake_generate(provider, *args, **kwargs):
            calls.append(provider)
            return "Resposta direta e suficiente."

        engine._generate_solution_with_provider = fake_generate

        response = engine.generate_solution("pergunta simples")

        self.assertEqual(["openai"], calls)
        self.assertEqual("Resposta direta e suficiente.", response)


if __name__ == "__main__":
    unittest.main()

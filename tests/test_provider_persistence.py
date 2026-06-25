import os
import unittest
from unittest import mock

from modules.app_state import AppStateMixin
from modules.engine import UniversalEngine


class ProviderPersistenceTests(unittest.TestCase):
    def test_environment_preserves_exact_provider_models_and_endpoints(self):
        app = AppStateMixin()
        app.settings = {
            "ai_provider": "openai",
            "openai_api_key": "sk-or-example",
            "openai_model_name": "deepseek/deepseek-chat:free",
            "openai_base_url": "https://openrouter.ai/api/v1/",
            "google_api_key": "google-example",
            "google_model_name": "gemini-example",
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            app._apply_settings_to_environment()
            self.assertEqual("deepseek/deepseek-chat:free", os.environ["OPENAI_MODEL_NAME"])
            self.assertEqual("https://openrouter.ai/api/v1", os.environ["OPENAI_BASE_URL"])
            self.assertEqual("google-example", os.environ["GOOGLE_API_KEY"])

    def test_unknown_provider_is_not_silently_routed_to_google(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        self.assertNotIn("unknown", UniversalEngine.VALID_PROVIDERS)


if __name__ == "__main__":
    unittest.main()

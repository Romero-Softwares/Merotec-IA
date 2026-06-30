import os
import unittest
from unittest import mock

from modules.app_state import AppStateMixin
from modules.ai_profiles import activate_profile, ensure_ai_profiles, profile_for
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

    def test_codex_profile_does_not_enable_browser_fallback_by_default(self):
        settings = ensure_ai_profiles({"ai_provider": "codex", "active_ai_profile": "codex"})

        self.assertFalse(profile_for(settings, "codex")["browser_ai_fallback_enabled"])

        activate_profile(settings, "codex")

        self.assertFalse(settings["browser_ai_fallback_enabled"])


if __name__ == "__main__":
    unittest.main()

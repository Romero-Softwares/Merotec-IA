import os
from modules.app_constants import APP_SETTINGS_FILE
from modules.ai_profiles import activate_profile, ensure_ai_profiles
from modules.json_store import atomic_write_json, load_json_file


def _load_settings():
    return load_json_file(APP_SETTINGS_FILE, {}, dict)



_SETTINGS = ensure_ai_profiles(_load_settings())
activate_profile(_SETTINGS, _SETTINGS.get("active_ai_profile") or _SETTINGS.get("ai_provider"))

LOCAL_GGUF_PATH = os.getenv("LOCAL_GGUF_PATH", _SETTINGS.get("local_gguf_path", "")).strip()
AI_PROVIDER = os.getenv("AI_PROVIDER", _SETTINGS.get("ai_provider", "web_chat")).strip().lower()
CODEX_MODEL_NAME = os.getenv("CODEX_MODEL_NAME", _SETTINGS.get("codex_model_name", "")).strip()
CODEX_REASONING_EFFORT = os.getenv(
    "CODEX_REASONING_EFFORT",
    _SETTINGS.get("codex_reasoning_effort", "xhigh"),
).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", _SETTINGS.get("openai_api_key", "")).strip()
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", _SETTINGS.get("openai_model_name", "gpt-5.5")).strip()
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    _SETTINGS.get("openai_base_url", "https://api.openai.com/v1"),
).strip()
LM_STUDIO_BASE_URL = os.getenv(
    "LM_STUDIO_BASE_URL",
    _SETTINGS.get("lm_studio_base_url", "http://127.0.0.1:1234/v1"),
).strip()
LM_STUDIO_MODEL_NAME = os.getenv(
    "LM_STUDIO_MODEL_NAME",
    _SETTINGS.get("lm_studio_model_name", ""),
).strip()
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", _SETTINGS.get("lm_studio_api_key", "")).strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", _SETTINGS.get("google_api_key", "")).strip()
MODEL_NAME = os.getenv("GOOGLE_MODEL_NAME", _SETTINGS.get("google_model_name", "gemini-3.1-flash-lite")).strip()
LANGUAGE = os.getenv("APP_LANGUAGE", _SETTINGS.get("language", "pt-BR")).strip()
WEB_CHAT_URL = os.getenv("WEB_CHAT_URL", _SETTINGS.get("web_chat_url", "https://chatgpt.com/")).strip()
WEB_CHAT_TIMEOUT_SECONDS = int(os.getenv("WEB_CHAT_TIMEOUT_SECONDS", _SETTINGS.get("web_chat_timeout_seconds", 300)) or 300)
WEB_CHAT_MESSAGE_CHARS = int(os.getenv("WEB_CHAT_MESSAGE_CHARS", _SETTINGS.get("web_chat_message_chars", 28000)) or 28000)
WEB_CHAT_AUTO_ATTACH_MEDIA = str(
    os.getenv("WEB_CHAT_AUTO_ATTACH_MEDIA", _SETTINGS.get("web_chat_auto_attach_media", True))
).strip().lower() in {"1", "true", "sim", "yes", "on"}

class ConfigManager:
    def __init__(self):
        self.settings = self._load_settings()
        
    def _load_settings(self):
        return _load_settings()
            
    def save_settings(self):
        return atomic_write_json(APP_SETTINGS_FILE, self.settings, indent=2, ensure_ascii=False)

    def get(self, key, default=None):
        return self.settings.get(key, default)
        
    def set(self, key, value):
        if self.settings.get(key) == value:
            return True  # Skip save if value unchanged
        self.settings[key] = value
        return self.save_settings()
        
    def update_many(self, new_settings):
        """Update multiple settings at once with single save"""
        needs_save = False
        for key, value in new_settings.items():
            if self.settings.get(key) != value:
                self.settings[key] = value
                needs_save = True
        return self.save_settings() if needs_save else True

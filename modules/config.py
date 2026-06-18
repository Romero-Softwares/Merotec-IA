import json
import os
from pathlib import Path
from modules.app_constants import APP_SETTINGS_FILE


def _load_settings():
    try:
        if APP_SETTINGS_FILE.exists():
            with APP_SETTINGS_FILE.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


_SETTINGS = _load_settings()


AI_PROVIDER = os.getenv("AI_PROVIDER", _SETTINGS.get("ai_provider", "codex")).strip().lower()
CODEX_MODEL_NAME = os.getenv("CODEX_MODEL_NAME", _SETTINGS.get("codex_model_name", "")).strip()
CODEX_REASONING_EFFORT = os.getenv(
    "CODEX_REASONING_EFFORT",
    _SETTINGS.get("codex_reasoning_effort", "xhigh"),
).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", _SETTINGS.get("openai_api_key", "")).strip()
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", _SETTINGS.get("openai_model_name", "gpt-5.5")).strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", _SETTINGS.get("google_api_key", "")).strip()
MODEL_NAME = os.getenv("GOOGLE_MODEL_NAME", _SETTINGS.get("google_model_name", "gemini-3.1-flash-lite")).strip()
LANGUAGE = os.getenv("APP_LANGUAGE", _SETTINGS.get("language", "pt-BR")).strip()

class ConfigManager:
    def __init__(self):
        self.settings = self._load_settings()
        
    def _load_settings(self):
        return _load_settings()
            
    def save_settings(self):
        try:
            with open(APP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)
            return True
        except Exception:
            return False

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

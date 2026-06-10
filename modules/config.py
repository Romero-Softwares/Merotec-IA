import os

AI_PROVIDER = os.getenv("AI_PROVIDER", "codex").strip().lower()
CODEX_MODEL_NAME = os.getenv("CODEX_MODEL_NAME", "").strip()
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", "xhigh").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-5.5").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
MODEL_NAME = os.getenv("GOOGLE_MODEL_NAME", "gemini-3.1-flash-lite")
LANGUAGE = os.getenv("APP_LANGUAGE", "pt-BR")

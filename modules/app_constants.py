from pathlib import Path


APP_NAME = "Merotec IA IDE"
CHAT_TAB_NAME = "Chat AI"
CORE_TABS = {CHAT_TAB_NAME, "Chat IA", "Scratchpad", "Terminal Local", "Log do Agente"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "projects"
APP_SETTINGS_FILE = PROJECT_ROOT / "ide_settings.json"
APP_HISTORY_FILE = PROJECT_ROOT / "history.json"
APP_CHANGE_HISTORY_FILE = PROJECT_ROOT / "change_history.json"

DEFAULT_APP_SETTINGS = {
    "last_workspace": "",
    "recent_projects": [],
    "ai_provider": "codex",
    "codex_model_name": "",
    "codex_reasoning_effort": "xhigh",
}

SCRATCHPAD_DEFAULT_TEXT = """# Como configurar um modelo de IA nesta IDE
#
# Motor principal:
# - Provedor: codex
# - Usa o Codex local ja logado no Windows.
# - A IDE usa apenas o Codex como agente principal.
#
# Opcao OpenAI:
# 1. Crie uma chave em: https://platform.openai.com/api-keys
# 2. Configure as variaveis no PowerShell:
#    setx AI_PROVIDER "openai"
#    setx OPENAI_API_KEY "cole_sua_chave_aqui"
#    setx OPENAI_MODEL_NAME "gpt-5.2"
# 3. Feche e abra a IDE novamente.
#
# Opcao Google:
# 1. Configure sua chave do Google GenAI:
#    setx AI_PROVIDER "google"
#    setx GOOGLE_API_KEY "cole_sua_chave_aqui"
#    setx GOOGLE_MODEL_NAME "gemini-3.1-flash-lite"
# 2. Feche e abra a IDE novamente.
#
# Observacoes:
# - Nao cole sua chave no chat.
# - Se aparecer invalid_api_key, gere uma nova chave e copie completa.
# - Se aparecer insufficient_quota, verifique Billing, Usage e Limits da plataforma.
# - Depois de configurar, use a aba Chat AI para conversar com o modelo.

"""

IGNORED_DIRS = {
    ".git",
    ".gradle",
    ".gemini",
    ".idea",
    ".dart_tool",
    ".merotec_attachments",
    ".merotec_backups",
    ".tool_appdata",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "codex_schema_probe",
    "coverage",
    "dist",
    "ephemeral",
    "node_modules",
    "out",
    "tcl_runtime",
    "venv",
}

IGNORED_SUFFIXES = {
    ".bin",
    ".dll",
    ".exe",
    ".ico",
    ".jpg",
    ".jpeg",
    ".pyd",
    ".pyc",
    ".png",
    ".webp",
    ".zip",
}

FILE_ICON_COLORS = {
    ".py": ("#3776ab", "#ffd343"),
    ".js": ("#f0db4f", "#1f2328"),
    ".ts": ("#3178c6", "#ffffff"),
    ".tsx": ("#3178c6", "#61dafb"),
    ".jsx": ("#1f2937", "#61dafb"),
    ".html": ("#e44d26", "#f7f7f7"),
    ".css": ("#264de4", "#f7f7f7"),
    ".json": ("#d6b656", "#1f2328"),
    ".md": ("#6f7785", "#ffffff"),
    ".txt": ("#8ea0b8", "#ffffff"),
    ".cmd": ("#2fbf71", "#06120c"),
    ".ps1": ("#3a7bd5", "#ffffff"),
    ".bat": ("#2fbf71", "#06120c"),
}

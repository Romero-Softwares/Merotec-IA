from pathlib import Path


APP_NAME = "Merotec IA IDE"
CHAT_TAB_NAME = "Chat IA"
CORE_TABS = {CHAT_TAB_NAME, "Chat IA", "Scratchpad", "Terminal Local", "Log do Agente", "Navegador"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = PROJECT_ROOT / "projects"
# A pasta da propria IDE nunca deve ser tratada como projeto do usuario.
DEFAULT_WORKSPACE = PROJECTS_DIR
APP_SETTINGS_FILE = PROJECT_ROOT / "ide_settings.json"
APP_HISTORY_FILE = PROJECT_ROOT / "history.json"
APP_CHANGE_HISTORY_FILE = PROJECT_ROOT / "change_history.json"
MEROTEC_SYSTEM_AI_DIR = PROJECT_ROOT / ".merotec_system_ai"

DEFAULT_APP_SETTINGS = {
    "last_workspace": "",
    "recent_projects": [],
    "ai_provider": "web_chat",
    "active_ai_profile": "web_chat",
    "ai_profiles": {},
    "web_chat_sessions": {},
    "lm_studio_base_url": "http://127.0.0.1:1234/v1",
    "lm_studio_model_name": "",
    "lm_studio_api_key": "",
    "lm_studio_allow_external_fallback": False,
    "lm_studio_timeout_seconds": 300,
    "lm_studio_max_input_chars": 14000,
    "lm_studio_max_tokens": 1024,
    "local_gguf_path": "",
    "local_gguf_allow_external_fallback": False,
    "local_gguf_n_ctx": 4096,
    "local_gguf_n_threads": 0,
    "local_gguf_n_gpu_layers": 0,
    "local_gguf_n_batch": 256,
    "local_gguf_max_tokens": 160,
    "local_gguf_max_input_tokens": 900,
    "local_gguf_timeout_seconds": 12,
    "codex_model_name": "",
    "codex_reasoning_effort": "high",
    "autonomous_unrestricted_mode": True,
    "autonomous_delivery_enabled": True,
    "autonomous_visual_validation_enabled": True,
    # O loop de desenvolvimento permanece ativo até validação, cancelamento ou bloqueio real.
    # 0 = sem limite artificial de ciclos; o usuário pode definir um limite positivo em ide_settings.json.
    "continuous_development_loop_enabled": True,
    "continuous_development_max_cycles": 0,
    "autonomous_max_repair_cycles": 4,  # compatibilidade com instalações antigas
    "codex_auto_approve_app_server_requests": True,
    "codex_app_server_approval_policy": "on-request",
    "codex_app_server_idle_timeout_seconds": 900,
    "codex_task_timeout_seconds": 3600,
    "external_ai_fallback_enabled": False,
    "browser_ai_fallback_enabled": False,
    "browser_ai_fallback_url": "https://chatgpt.com/",
    "browser_ai_fallback_timeout_seconds": 240,
    "browser_ai_fallback_max_context_chars": 60000,
    # Chat Web é um provedor de navegador: qualquer URL HTTPS/HTTP pode ser usada.
    "web_chat_url": "https://chatgpt.com/",
    "web_chat_timeout_seconds": 300,
    "web_chat_message_chars": 28000,
    "web_chat_auto_attach_media": True,
    "web_chat_allow_remote_actions": False,
    "web_chat_restore_project_session": True,
    "web_chat_include_project_context": True,
    "web_chat_auto_apply_imported_actions": True,
    "web_chat_fallback_enabled": False,
}

SCRATCHPAD_DEFAULT_TEXT = """# Como configurar um modelo de IA nesta IDE
#
# Perfis de IA:
# - Abra Configurar IA e escolha no seletor o perfil ativo.
# - Cada perfil mantém suas próprias chaves, modelo, URL e limites.
# - Chat Web aceita qualquer URL HTTP(S), por exemplo https://gemini.google.com/.
# - A conversa Web é restaurada por projeto; a IDE não cria Nova conversa ao reenviar tarefas.
#
# Opcao OpenAI:
# 1. Crie uma chave em: https://platform.openai.com/api-keys
# 2. Configure as variaveis no PowerShell:
#    setx AI_PROVIDER "openai"
#    setx OPENAI_API_KEY "cole_sua_chave_aqui"
#    setx OPENAI_MODEL_NAME "gpt-5.5"
# 3. Feche e abra a IDE novamente.
#
# Opcao LM Studio (modelo servido localmente):
# 1. Inicie o servidor local no LM Studio.
# 2. Abra Configurar IA e escolha lm_studio.
# 3. A IDE detecta os modelos em http://127.0.0.1:1234/v1.
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
    ".merotec_local_ai",
    ".merotec_system_ai",
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

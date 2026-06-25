"""Perfis persistentes de IA e sessões de chat web da Merotec IA IDE.

Este módulo mantém a configuração de cada tipo de IA isolada. Ele também
mantém um vínculo entre projeto + perfil de Chat Web e a URL da conversa,
para que a IDE retorne à conversa correta sem clicar em "Nova conversa".
"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PROVIDER_ORDER = ("web_chat", "codex", "openai", "google", "lm_studio", "local_gguf")

PROVIDER_LABELS = {
    "web_chat": "Chat Web (URL livre)",
    "codex": "Codex local",
    "openai": "API compatível com OpenAI",
    "google": "Google Gemini API",
    "lm_studio": "LM Studio local",
    "local_gguf": "Modelo GGUF local",
}

PROVIDER_ALIASES = {
    "web": "web_chat",
    "browser": "web_chat",
    "chat web": "web_chat",
    "chat_web": "web_chat",
    "url": "web_chat",
    "local": "local_gguf",
    "gguf": "local_gguf",
    "local gguf": "local_gguf",
    "lmstudio": "lm_studio",
    "lm studio": "lm_studio",
    "servidor lm studio": "lm_studio",
}

GLOBAL_SETTING_KEYS = {
    "last_workspace",
    "recent_projects",
    "ai_provider",
    "active_ai_profile",
    "ai_profiles",
    "web_chat_sessions",
    "autonomous_unrestricted_mode",
    "codex_auto_approve_app_server_requests",
    "codex_app_server_approval_policy",
    "codex_app_server_idle_timeout_seconds",
    "codex_task_timeout_seconds",
    "external_ai_fallback_enabled",
}

PROFILE_DEFAULTS = {
    "web_chat": {
        "web_chat_url": "https://chatgpt.com/",
        "web_chat_timeout_seconds": 300,
        "web_chat_message_chars": 28000,
        "web_chat_auto_attach_media": True,
        "web_chat_allow_remote_actions": False,
        "web_chat_restore_project_session": True,
        "web_chat_include_project_context": True,
        "web_chat_auto_apply_imported_actions": True,
        "web_chat_fallback_enabled": True,
    },
    "codex": {
        "codex_model_name": "",
        "codex_reasoning_effort": "high",
        "browser_ai_fallback_enabled": True,
        "browser_ai_fallback_url": "https://chatgpt.com/",
        "browser_ai_fallback_timeout_seconds": 300,
        "browser_ai_fallback_max_context_chars": 60000,
    },
    "openai": {
        "openai_api_key": "",
        "openai_model_name": "gpt-5.5",
        "openai_base_url": "https://api.openai.com/v1",
    },
    "google": {
        "google_api_key": "",
        "google_model_name": "gemini-2.5-flash",
    },
    "lm_studio": {
        "lm_studio_base_url": "http://127.0.0.1:1234/v1",
        "lm_studio_model_name": "",
        "lm_studio_api_key": "",
        "lm_studio_allow_external_fallback": False,
        "lm_studio_timeout_seconds": 300,
        "lm_studio_max_input_chars": 60000,
        "lm_studio_max_tokens": 2048,
    },
    "local_gguf": {
        "local_gguf_path": "",
        "local_gguf_allow_external_fallback": False,
        "local_gguf_n_ctx": 8192,
        "local_gguf_n_threads": 0,
        "local_gguf_n_gpu_layers": 0,
        "local_gguf_n_batch": 256,
        "local_gguf_max_tokens": 512,
        "local_gguf_max_input_tokens": 6000,
        "local_gguf_timeout_seconds": 90,
    },
}

# Configuração legada que deve ser migrada para cada perfil na primeira abertura.
LEGACY_KEYS_BY_PROVIDER = {
    key: tuple(values)
    for key, values in PROFILE_DEFAULTS.items()
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_provider(value: object, default: str = "web_chat") -> str:
    text = str(value or "").strip().lower()
    text = PROVIDER_ALIASES.get(text, text)
    return text if text in PROVIDER_ORDER else default


def provider_label(provider: object) -> str:
    return PROVIDER_LABELS.get(normalize_provider(provider), "IA")


def provider_from_label(value: object) -> str:
    text = str(value or "").strip()
    for provider, label in PROVIDER_LABELS.items():
        if text == label:
            return provider
    return normalize_provider(text)


def normalize_web_url(value: object, default: str = "https://chatgpt.com/") -> str:
    url = str(value or "").strip().strip("\"'")
    if not url:
        return default
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Informe uma URL HTTP(S) válida para o Chat Web.")
    return url


def _profile_defaults(provider: str) -> dict:
    return copy.deepcopy(PROFILE_DEFAULTS[provider])


def ensure_ai_profiles(settings: dict | None) -> dict:
    """Normaliza perfis e migra preferências antigas sem apagar dados."""
    settings = settings if isinstance(settings, dict) else {}
    raw_profiles = settings.get("ai_profiles")
    profiles = raw_profiles if isinstance(raw_profiles, dict) else {}

    for provider in PROVIDER_ORDER:
        profile = profiles.get(provider)
        if not isinstance(profile, dict):
            profile = {}
        merged = _profile_defaults(provider)

        # Migra configurações antigas somente quando o perfil ainda não possui o valor.
        for key in LEGACY_KEYS_BY_PROVIDER[provider]:
            if key not in profile and key in settings and settings.get(key) not in (None, ""):
                merged[key] = settings.get(key)
        merged.update(profile)
        profiles[provider] = merged

    active = normalize_provider(
        settings.get("active_ai_profile") or settings.get("ai_provider") or "web_chat",
        default="web_chat",
    )
    settings["ai_profiles"] = profiles
    settings["active_ai_profile"] = active

    sessions = settings.get("web_chat_sessions")
    settings["web_chat_sessions"] = sessions if isinstance(sessions, dict) else {}
    return settings


def profile_for(settings: dict, provider: object | None = None) -> dict:
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(provider or settings.get("active_ai_profile"), default="web_chat")
    profile = settings["ai_profiles"][selected]
    return copy.deepcopy(profile)


def update_profile(settings: dict, provider: object, values: dict) -> dict:
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(provider, default="web_chat")
    current = settings["ai_profiles"][selected]
    for key, value in (values or {}).items():
        if key in PROFILE_DEFAULTS[selected]:
            current[key] = value
    settings["ai_profiles"][selected] = current
    return settings


def activate_profile(settings: dict, provider: object) -> dict:
    """Ativa um perfil e espelha seus valores para as chaves legadas do motor."""
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(provider, default="web_chat")
    settings["active_ai_profile"] = selected
    settings["ai_provider"] = selected
    profile = settings["ai_profiles"][selected]
    for key, value in profile.items():
        settings[key] = value
    return settings


def active_profile(settings: dict) -> tuple[str, dict]:
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(settings.get("active_ai_profile"), default="web_chat")
    return selected, profile_for(settings, selected)

# MEROTEC_CHAT_URL_SESSIONS_V2
def web_chat_origin(value: object) -> str:
    """Identidade estável do site de chat, sem caminho de conversa ou query."""
    try:
        parsed = urlparse(normalize_web_url(value))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme.lower()}://{host}{suffix}"


def _legacy_workspace_session_key(workspace: str | Path, provider: object = "web_chat") -> str:
    try:
        canonical = str(Path(workspace).resolve())
    except OSError:
        canonical = str(workspace)
    value = f"{normalize_provider(provider, 'web_chat')}|{canonical.lower()}"
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def workspace_session_key(
    workspace: str | Path,
    provider: object = "web_chat",
    entry_url: object = "",
) -> str:
    """Chave por projeto e origem do Chat Web.

    Assim um projeto pode manter uma conversa no ChatGPT e outra no Gemini sem
    uma URL antiga substituir o chat selecionado atualmente.
    """
    selected = normalize_provider(provider, "web_chat")
    try:
        canonical = str(Path(workspace).resolve())
    except OSError:
        canonical = str(workspace)
    origin = web_chat_origin(entry_url) if selected == "web_chat" and entry_url else ""
    value = f"{selected}|{origin or 'legacy'}|{canonical.lower()}"
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _same_web_chat_origin(left: object, right: object) -> bool:
    first = web_chat_origin(left)
    second = web_chat_origin(right)
    return bool(first and second and first == second)


def get_web_chat_session(
    settings: dict,
    workspace: str | Path,
    provider: object = "web_chat",
    *,
    entry_url: object = "",
) -> dict:
    """Busca apenas a sessão pertencente ao site de chat selecionado.

    Registros antigos sem origem são migrados sob demanda somente se apontarem
    para o mesmo site. Um registro do ChatGPT nunca é reutilizado no Gemini.
    """
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(provider, "web_chat")
    sessions = settings["web_chat_sessions"]
    if entry_url:
        key = workspace_session_key(workspace, selected, entry_url)
        item = sessions.get(key)
        if isinstance(item, dict):
            return copy.deepcopy(item)

        legacy = sessions.get(_legacy_workspace_session_key(workspace, selected))
        if isinstance(legacy, dict):
            legacy_entry = legacy.get("entry_url") or legacy.get("url")
            if _same_web_chat_origin(legacy_entry, entry_url):
                migrated = copy.deepcopy(legacy)
                migrated["entry_url"] = normalize_web_url(entry_url)
                sessions[key] = copy.deepcopy(migrated)
                return migrated
        return {}

    item = sessions.get(_legacy_workspace_session_key(workspace, selected))
    return copy.deepcopy(item) if isinstance(item, dict) else {}


def remember_web_chat_session(
    settings: dict,
    workspace: str | Path,
    provider: object,
    url: str,
    *,
    entry_url: str = "",
    title: str = "",
) -> dict:
    settings = ensure_ai_profiles(settings)
    try:
        normalized_url = normalize_web_url(url)
        normalized_entry = normalize_web_url(entry_url or normalized_url)
    except ValueError:
        return settings
    selected = normalize_provider(provider, "web_chat")
    key = workspace_session_key(workspace, selected, normalized_entry)
    try:
        workspace_label = Path(workspace).resolve().name
    except OSError:
        workspace_label = str(workspace)
    settings["web_chat_sessions"][key] = {
        "url": normalized_url,
        "entry_url": normalized_entry,
        "title": str(title or "").strip()[:240],
        "workspace_label": workspace_label,
        "provider": selected,
        "updated_at": utc_now(),
    }
    # Limite defensivo: mantém somente as 120 conversas mais recentes.
    entries = settings["web_chat_sessions"]
    if len(entries) > 120:
        ordered = sorted(
            entries.items(),
            key=lambda item: str((item[1] or {}).get("updated_at", "")),
            reverse=True,
        )[:120]
        settings["web_chat_sessions"] = dict(ordered)
    return settings


def clear_web_chat_session(
    settings: dict,
    workspace: str | Path,
    provider: object = "web_chat",
    *,
    entry_url: object = "",
) -> dict:
    settings = ensure_ai_profiles(settings)
    selected = normalize_provider(provider, "web_chat")
    key = (
        workspace_session_key(workspace, selected, entry_url)
        if entry_url
        else _legacy_workspace_session_key(workspace, selected)
    )
    settings["web_chat_sessions"].pop(key, None)
    return settings


def web_chat_url_for_workspace(
    settings: dict,
    workspace: str | Path,
    provider: object = "web_chat",
) -> str:
    """Retorna a conversa do projeto apenas quando ela pertence à URL atual."""
    settings = ensure_ai_profiles(settings)
    profile = profile_for(settings, provider)
    entry_url = normalize_web_url(profile.get("web_chat_url"), "https://chatgpt.com/")
    saved = get_web_chat_session(settings, workspace, provider, entry_url=entry_url)
    if saved.get("url") and _same_web_chat_origin(saved.get("url"), entry_url):
        return str(saved["url"])
    return entry_url

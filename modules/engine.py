import base64
import atexit
import importlib.util
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import PIL.Image
try:
    from google.genai import Client as GoogleClient
    from google.genai import types
except ModuleNotFoundError:
    GoogleClient = None
    types = None

from modules import config as app_config
from modules.ai_profiles import (
    activate_profile,
    ensure_ai_profiles,
    normalize_web_url,
    profile_for,
)
from modules.web_chat_bridge import WebChatBridge


class UniversalEngine:
    VALID_PROVIDERS = {"web_chat", "codex", "openai", "lm_studio", "google", "local_gguf"}

    def __init__(self):
        self.configuration_warnings = []
        # Estado da última entrega pelo navegador. A interface usa isto para
        # não fingir que um print de teste chegou ao chat quando o site recusou
        # o upload ou o botão de envio ficou bloqueado.
        self.latest_web_chat_delivery = {}
        self.latest_web_chat_artifacts = {}
        settings_path = Path(app_config.APP_SETTINGS_FILE)
        settings = {}
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                if not isinstance(settings, dict):
                    settings = {}
                    self.configuration_warnings.append("arquivo de configuracao nao contem um objeto JSON")
            except (OSError, json.JSONDecodeError) as exc:
                self.configuration_warnings.append(f"nao foi possivel ler a configuracao: {exc}")

        ensure_ai_profiles(settings)
        requested_provider = str(
            os.getenv(
                "AI_PROVIDER",
                settings.get("active_ai_profile", settings.get("ai_provider", app_config.AI_PROVIDER)),
            )
        ).strip().lower()
        activate_profile(settings, requested_provider)
        if requested_provider not in self.VALID_PROVIDERS:
            self.configuration_warnings.append(f"provedor desconhecido '{requested_provider}' corrigido para web_chat")
            requested_provider = "web_chat"
            activate_profile(settings, requested_provider)
        self.settings = settings
        self.provider = requested_provider
        
        self.codex_model_name = str(settings.get("codex_model_name", os.getenv("CODEX_MODEL_NAME", app_config.CODEX_MODEL_NAME))).strip()
        self.codex_reasoning_effort = str(settings.get("codex_reasoning_effort", os.getenv("CODEX_REASONING_EFFORT", app_config.CODEX_REASONING_EFFORT))).strip().lower() or "high"
        
        # 3. Carrega as chaves de API salvas na configuração do JSON ou do ambiente
        self.openai_api_key = str(settings.get("openai_api_key", os.getenv("OPENAI_API_KEY", app_config.OPENAI_API_KEY))).strip()
        self.openai_model_name = str(settings.get("openai_model_name", os.getenv("OPENAI_MODEL_NAME", app_config.OPENAI_MODEL_NAME))).strip()
        self.active_openai_model_name = ""
        self.openai_base_url = str(
            settings.get("openai_base_url", os.getenv("OPENAI_BASE_URL", app_config.OPENAI_BASE_URL))
        ).strip().rstrip("/") or "https://api.openai.com/v1"
        self.lm_studio_base_url = self.normalize_lm_studio_base_url(
            settings.get("lm_studio_base_url", os.getenv("LM_STUDIO_BASE_URL", app_config.LM_STUDIO_BASE_URL))
        )
        self.lm_studio_model_name = str(
            settings.get("lm_studio_model_name", os.getenv("LM_STUDIO_MODEL_NAME", app_config.LM_STUDIO_MODEL_NAME))
        ).strip()
        self.active_lm_studio_model_name = ""
        self.lm_studio_api_key = str(
            settings.get("lm_studio_api_key", os.getenv("LM_STUDIO_API_KEY", app_config.LM_STUDIO_API_KEY))
        ).strip()
        self.lm_studio_allow_external_fallback = self._truthy_setting(
            settings,
            "lm_studio_allow_external_fallback",
            "LM_STUDIO_ALLOW_EXTERNAL_FALLBACK",
            False,
        )
        self.lm_studio_timeout_seconds = self._positive_int_setting(
            settings,
            "lm_studio_timeout_seconds",
            "LM_STUDIO_TIMEOUT_SECONDS",
            300,
            minimum=30,
        )
        self.lm_studio_max_input_chars = self._positive_int_setting(
            settings,
            "lm_studio_max_input_chars",
            "LM_STUDIO_MAX_INPUT_CHARS",
            14000,
            minimum=2000,
        )
        self.lm_studio_max_tokens = self._positive_int_setting(
            settings,
            "lm_studio_max_tokens",
            "LM_STUDIO_MAX_TOKENS",
            1024,
            minimum=32,
        )
        
        self.google_api_key = str(settings.get("google_api_key", os.getenv("GOOGLE_API_KEY", app_config.GOOGLE_API_KEY))).strip()
        self.google_model_name = str(settings.get("google_model_name", os.getenv("GOOGLE_MODEL_NAME", app_config.MODEL_NAME))).strip()
        self.local_gguf_path = str(settings.get("local_gguf_path", os.getenv("LOCAL_GGUF_PATH", app_config.LOCAL_GGUF_PATH))).strip()
        self.local_gguf_n_ctx = self._positive_int_setting(settings, "local_gguf_n_ctx", "LOCAL_GGUF_N_CTX", 4096, minimum=512)
        self.local_gguf_n_threads = self._thread_setting(settings, "local_gguf_n_threads", "LOCAL_GGUF_N_THREADS")
        self.local_gguf_n_gpu_layers = self._int_setting(settings, "local_gguf_n_gpu_layers", "LOCAL_GGUF_N_GPU_LAYERS", 0)
        self.local_gguf_n_batch = self._positive_int_setting(settings, "local_gguf_n_batch", "LOCAL_GGUF_N_BATCH", 256, minimum=32)
        self.local_gguf_max_tokens = self._positive_int_setting(settings, "local_gguf_max_tokens", "LOCAL_GGUF_MAX_TOKENS", 160, minimum=32)
        self.local_gguf_max_input_tokens = self._positive_int_setting(
            settings,
            "local_gguf_max_input_tokens",
            "LOCAL_GGUF_MAX_INPUT_TOKENS",
            900,
            minimum=256,
        )
        self.local_gguf_timeout_seconds = self._nonnegative_int_setting(
            settings,
            "local_gguf_timeout_seconds",
            "LOCAL_GGUF_TIMEOUT_SECONDS",
            12,
        )
        self.local_gguf_allow_external_fallback = self._truthy_setting(
            settings,
            "local_gguf_allow_external_fallback",
            "LOCAL_GGUF_ALLOW_EXTERNAL_FALLBACK",
            False,
        )
        self.language = os.getenv("APP_LANGUAGE", app_config.LANGUAGE).strip()
        # Perfil Web: a URL é livre e a conversa é restaurada por projeto.
        self.web_chat_profile = profile_for(settings, "web_chat")
        self.web_chat_url = normalize_web_url(
            os.getenv("WEB_CHAT_URL", settings.get("web_chat_url", self.web_chat_profile.get("web_chat_url", ""))),
            "https://chatgpt.com/",
        )
        self.web_chat_timeout_seconds = self._positive_int_setting(
            settings, "web_chat_timeout_seconds", "WEB_CHAT_TIMEOUT_SECONDS", 300, minimum=30
        )
        self.web_chat_message_chars = self._positive_int_setting(
            settings, "web_chat_message_chars", "WEB_CHAT_MESSAGE_CHARS", 28000, minimum=4000
        )
        self.web_chat_auto_attach_media = self._truthy_setting(
            settings, "web_chat_auto_attach_media", "WEB_CHAT_AUTO_ATTACH_MEDIA", True
        )
        self.web_chat_bridge = None
        self.latest_web_chat_artifacts = {}
        external_fallback_raw = settings.get(
            "external_ai_fallback_enabled",
            os.getenv("EXTERNAL_AI_FALLBACK_ENABLED", "1"),
        )
        self.external_ai_fallback_enabled = str(external_fallback_raw).strip().lower() not in {
            "0",
            "false",
            "nao",
            "não",
            "off",
        }

        self.client = None
        self.chat_session = None
        self.local_llm = None
        self.local_llm_lock = threading.Lock()
        self.local_worker_process = None
        self.local_worker_events = queue.Queue()
        self.local_worker_reader_thread = None
        self.local_worker_ready = False
        self.local_worker_model_key = None
        self.local_worker_next_request_id = 0
        self.active_process = None
        self.cancel_requested = False
        atexit.register(self.terminate_local_gguf_worker)
        
        # 4. Resolve o ID do modelo baseado no que foi carregado dinamicamente
        self.model_id = self._resolve_model_id()

        self.latest_rate_limits = None
        self.latest_token_usage = None
        self.latest_quota_problem = ""
        self.latest_quota_updated_at = 0
        self.system_instruction = self._build_system_instruction()
        self.generation_config = self._build_google_generation_config()

        if self.provider == "web_chat":
            self.client = "webview2-chat" if self.web_chat_url else None
            return
        if self.provider == "codex":
            self.codex_executable = self._find_codex_executable()
            self.client = "codex-cli" if self.codex_executable and self._codex_is_logged_in(self.codex_executable) else None
            return

        if self.provider == "openai":
            self.client = "openai-http" if self.openai_api_key else None
            return

        if self.provider == "lm_studio":
            self.client = "lm-studio-http" if self.lm_studio_base_url and self.lm_studio_model_name else None
            return

        if self.provider == "google" and self.google_api_key and GoogleClient:
            self.client = GoogleClient(api_key=self.google_api_key)
            self.reset_session()
            return

        if self.provider == "local_gguf":
            self.client = "local-gguf" if self.local_gguf_file_configured() else None
            return
    _model_cache = {}

    @staticmethod
    def _int_setting(settings, key, env_name, default):
        raw = settings.get(key, os.getenv(env_name, default))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(default)

    @classmethod
    def _positive_int_setting(cls, settings, key, env_name, default, minimum=1):
        return max(int(minimum), cls._int_setting(settings, key, env_name, default))

    @classmethod
    def _nonnegative_int_setting(cls, settings, key, env_name, default):
        return max(0, cls._int_setting(settings, key, env_name, default))

    @staticmethod
    def default_local_gguf_threads():
        cpu_count = os.cpu_count() or 4
        return max(1, min(8, cpu_count - 1 if cpu_count > 2 else cpu_count))

    @classmethod
    def _thread_setting(cls, settings, key, env_name):
        raw = settings.get(key, os.getenv(env_name, "0"))
        try:
            configured = int(raw)
        except (TypeError, ValueError):
            configured = 0
        if configured <= 0:
            return cls.default_local_gguf_threads()
        return max(1, configured)

    @staticmethod
    def _truthy_setting(settings, key, env_name, default=False):
        raw = settings.get(key, os.getenv(env_name, "1" if default else "0"))
        return str(raw).strip().lower() in {"1", "true", "sim", "yes", "on"}

    @staticmethod
    def normalize_lm_studio_base_url(value):
        base_url = str(value or "http://127.0.0.1:1234/v1").strip().rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if base_url.lower().endswith(suffix):
                base_url = base_url[: -len(suffix)].rstrip("/")
        if not base_url.lower().endswith("/v1"):
            base_url += "/v1"
        return base_url

    @classmethod
    def discover_lm_studio_models(cls, base_url, api_key="", timeout=5):
        endpoint = f"{cls.normalize_lm_studio_base_url(base_url)}/models"
        headers = {"Accept": "application/json"}
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {str(api_key).strip()}"
        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        model_ids = [
            str(item.get("id") or "").strip()
            for item in payload.get("data", [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        chat_models = [
            model_id
            for model_id in model_ids
            if not any(marker in model_id.lower() for marker in ("embedding", "embed-text", "reranker"))
        ]
        return chat_models or model_ids

    def _ensure_local_gguf_lock(self):
        lock = getattr(self, "local_llm_lock", None)
        if lock is None:
            lock = threading.Lock()
            self.local_llm_lock = lock
        return lock

    def resolve_local_gguf_path(self, raw_path=None):
        raw = str(raw_path if raw_path is not None else self.local_gguf_path or "").strip().strip("\"'")
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            from modules.app_constants import PROJECT_ROOT

            path = PROJECT_ROOT / path
        return path.resolve()

    def local_gguf_is_ready(self):
        path = self.resolve_local_gguf_path()
        return bool(path and path.is_file() and path.suffix.lower() == ".gguf" and self.llama_cpp_available())

    def local_gguf_file_configured(self):
        if not getattr(self, "local_gguf_path", ""):
            return False
        path = self.resolve_local_gguf_path()
        return bool(path and path.is_file() and path.suffix.lower() == ".gguf")

    @staticmethod
    def llama_cpp_available():
        return importlib.util.find_spec("llama_cpp") is not None

    def load_local_gguf_model(self):
        if self.local_llm is not None:
            return ""
        with self._ensure_local_gguf_lock():
            if self.local_llm is not None:
                return ""
            path = self.resolve_local_gguf_path()
            if not path:
                return "Modelo local GGUF nao configurado. Abra Configurar IA e selecione um arquivo .gguf."
            if not path.exists():
                return f"Modelo local GGUF nao encontrado: {path}"
            if path.suffix.lower() != ".gguf":
                return f"O arquivo selecionado nao e um modelo .gguf: {path}"
            if not self.llama_cpp_available():
                return "llama-cpp-python nao esta instalado neste Python. Instale as dependencias do requirements.txt."
            try:
                from llama_cpp import Llama

                self.local_llm = Llama(
                    model_path=str(path),
                    n_ctx=self.local_gguf_n_ctx,
                    n_threads=self.local_gguf_n_threads,
                    n_batch=self.local_gguf_n_batch,
                    n_gpu_layers=self.local_gguf_n_gpu_layers,
                    verbose=False,
                )
                self.client = "local-gguf"
                return ""
            except Exception as exc:
                self.local_llm = None
                return f"Erro ao carregar modelo local GGUF: {exc}"

    def _resolve_model_id(self):
        self.provider = getattr(self, "provider", "codex")
        self.web_chat_url = getattr(self, "web_chat_url", "")
        self.codex_model_name = getattr(self, "codex_model_name", "")
        self.openai_model_name = getattr(self, "openai_model_name", "")
        self.lm_studio_model_name = getattr(self, "lm_studio_model_name", "")
        self.google_model_name = getattr(self, "google_model_name", "")
        self.local_gguf_path = getattr(self, "local_gguf_path", "")
        self._model_cache = getattr(self, "_model_cache", {})
        configured_model = {
            "web_chat": self.web_chat_url,
            "codex": self.codex_model_name,
            "openai": self.openai_model_name,
            "lm_studio": getattr(self, "lm_studio_model_name", ""),
            "google": self.google_model_name,
            "local_gguf": getattr(self, "local_gguf_path", ""),
        }.get(self.provider, self.codex_model_name)
        cache_key = f"{self.provider}:{configured_model}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        if self.provider == "web_chat":
            try:
                host = urllib.parse.urlparse(self.web_chat_url).netloc
            except Exception:
                host = self.web_chat_url
            model_id = f"Chat Web · {host or 'URL livre'}"
        elif self.provider == "codex":
            model_id = self.codex_model_name or "gpt-5.5"
        elif self.provider == "openai":
            model_id = self.openai_model_name or "gpt-5.5"
        elif self.provider == "lm_studio":
            model_id = self.lm_studio_model_name or "modelo carregado no LM Studio"
        elif self.provider == "google":
            model_id = self.google_model_name or "gemini-3.1-flash-lite"
        elif self.provider == "local_gguf":
            path = self.resolve_local_gguf_path()
            model_id = path.name if path else "modelo local GGUF"
        else:
            model_id = self.codex_model_name or "gpt-5.5"

        self._model_cache[cache_key] = model_id
        return model_id

    def assistant_display_name(self):
        """Return the actual model identity instead of inventing an assistant name."""
        if self.provider == "web_chat":
            return self.model_id or "Chat Web"
        if self.provider == "lm_studio":
            return (
                self.active_lm_studio_model_name
                or self.lm_studio_model_name
                or self.model_id
                or "Modelo do servidor LLM"
            )
        if self.provider == "openai":
            return self.active_openai_model_name or self.openai_model_name or self.model_id or "Modelo OpenAI"
        if self.provider == "google":
            return self.google_model_name or self.model_id or "Modelo Google"
        if self.provider == "local_gguf":
            return self.model_id or "Modelo local GGUF"
        if self.provider == "codex":
            return self.codex_model_name or "Codex"
        return self.model_id or "IA"

    def _remember_lm_studio_response_model(self, payload):
        if not isinstance(payload, dict):
            return
        reported_model = str(payload.get("model") or "").strip()
        if reported_model:
            self.active_lm_studio_model_name = reported_model

    def _remember_openai_response_model(self, payload):
        if not isinstance(payload, dict):
            return
        reported_model = str(payload.get("model") or "").strip()
        if reported_model:
            self.active_openai_model_name = reported_model

    def status_text(self):
        if self.provider == "web_chat":
            key_state = "URL configurada" if self.web_chat_url else "sem URL"
        elif self.provider == "codex":
            if self.client:
                key_state = "logado"
            elif getattr(self, "codex_executable", None):
                key_state = "sem login"
            else:
                key_state = "nao encontrado"
        elif self.provider == "local_gguf":
            path = self.resolve_local_gguf_path()
            if not self.local_gguf_path:
                key_state = "sem modelo"
            elif not path or not path.exists():
                key_state = "arquivo ausente"
            elif self.local_worker_ready:
                key_state = "rodando em segundo plano"
            elif self.local_llm is not None:
                key_state = "carregado"
            else:
                key_state = "modelo pronto para carregar"
        elif self.provider == "lm_studio":
            key_state = "servidor configurado" if self.client else "sem modelo"
        else:
            key_state = "chave ok" if self.client else "sem chave"
        effort = f" | raciocinio {self.codex_reasoning_effort}" if self.provider == "codex" else ""
        displayed_model = self.assistant_display_name() if self.provider == "lm_studio" else self.model_id
        warnings = getattr(self, "configuration_warnings", [])
        warning = f" | aviso: {warnings[-1]}" if warnings else ""
        return f"{self.provider.upper()} | {displayed_model}{effort} | {key_state}{warning}"

    def quota_status_text(self):
        model = self.assistant_display_name() if self.provider == "lm_studio" else (self.model_id or "modelo atual")
        if self.provider == "web_chat":
            return (
                f"CHAT WEB {model} | conversa por projeto | URL livre | "
                "limites pertencem ao serviço selecionado"
            )
        if self.provider == "local_gguf":
            return (
                f"LOCAL_GGUF {model} | offline/local | ctx {self.local_gguf_n_ctx} | "
                f"threads {self.local_gguf_n_threads} | batch {self.local_gguf_n_batch} | "
                f"entrada {self.local_gguf_max_input_tokens}"
            )
        if self.provider == "lm_studio":
            return f"LM STUDIO {model} | servidor local | {self.lm_studio_base_url}"
        if self.provider == "codex" and self.codex_reasoning_effort:
            model = f"{model}/{self.codex_reasoning_effort}"
        prefix = f"{self.provider.upper()} {model}"

        if self.latest_quota_problem:
            return f"{prefix} | {self.latest_quota_problem}"

        rate_text = self._format_rate_limit_status(self.latest_rate_limits)
        token_text = self._format_token_usage_status(self.latest_token_usage)
        if rate_text and token_text:
            return f"{prefix} | {rate_text} | {token_text}"
        if rate_text:
            return f"{prefix} | {rate_text}"
        if token_text:
            return f"{prefix} | {token_text}"
        return f"{prefix} | uso aguardando"

    def _remember_rate_limits(self, payload):
        if not isinstance(payload, dict):
            return
        rate_limits = payload.get("rateLimits") if "rateLimits" in payload else payload
        by_limit = payload.get("rateLimitsByLimitId")
        if isinstance(by_limit, dict):
            rate_limits = by_limit.get("codex") or rate_limits or next(iter(by_limit.values()), None)
        if isinstance(rate_limits, dict):
            self.latest_rate_limits = rate_limits
            self.latest_quota_updated_at = time.time()
            reached = str(rate_limits.get("rateLimitReachedType") or "").strip()
            credits = rate_limits.get("credits") or {}
            if reached:
                self.latest_quota_problem = self._human_rate_limit_reached(reached)
            elif isinstance(credits, dict) and credits.get("hasCredits") is False and not credits.get("unlimited"):
                self.latest_quota_problem = "creditos esgotados"
            else:
                self.latest_quota_problem = ""

    def _remember_token_usage(self, payload):
        if not isinstance(payload, dict):
            return
        token_usage = payload.get("tokenUsage") if "tokenUsage" in payload else payload
        if isinstance(token_usage, dict):
            self.latest_token_usage = token_usage
            self.latest_quota_updated_at = time.time()

    def _remember_openai_usage(self, usage):
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("input_tokens", usage.get("inputTokens", 0)) or 0
        output_tokens = usage.get("output_tokens", usage.get("outputTokens", 0)) or 0
        total_tokens = usage.get("total_tokens", usage.get("totalTokens"))
        if total_tokens is None:
            try:
                total_tokens = int(input_tokens) + int(output_tokens)
            except (TypeError, ValueError):
                total_tokens = 0
        self._remember_token_usage(
            {
                "last": {
                    "cachedInputTokens": usage.get("cached_input_tokens", usage.get("cachedInputTokens", 0)) or 0,
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "reasoningOutputTokens": usage.get(
                        "reasoning_output_tokens",
                        usage.get("reasoningOutputTokens", 0),
                    ) or 0,
                    "totalTokens": total_tokens,
                },
                "total": {
                    "cachedInputTokens": usage.get("cached_input_tokens", usage.get("cachedInputTokens", 0)) or 0,
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "reasoningOutputTokens": usage.get(
                        "reasoning_output_tokens",
                        usage.get("reasoningOutputTokens", 0),
                    ) or 0,
                    "totalTokens": total_tokens,
                },
            }
        )

    def _remember_quota_error(self, payload):
        if not isinstance(payload, dict):
            return
        error = payload.get("error") or payload
        if not isinstance(error, dict):
            return
        text = " ".join(
            str(value)
            for value in (
                error.get("message"),
                error.get("additionalDetails"),
                error.get("codexErrorInfo"),
            )
            if value
        ).lower()
        if "usagelimitexceeded" in text or "usage limit" in text or "rate limit" in text:
            self.latest_quota_problem = "sem cota/limite atingido"
            self.latest_quota_updated_at = time.time()

    def _remember_quota_message_handler(self, method, params):
        lower_method = (method or "").lower()
        if "ratelimits" in lower_method:
            self._remember_rate_limits(params)
        if "tokenusage" in lower_method or "token_usage" in lower_method:
            self._remember_token_usage(params)
        if lower_method in {"error", "warning"} or lower_method.endswith("/error"):
            self._remember_quota_error(params)

    def _remember_app_server_quota_message(self, method, params):
        self._remember_quota_message_handler(method, params)

    def _human_rate_limit_reached(self, value):
        labels = {
            "rate_limit_reached": "limite de taxa atingido",
            "workspace_owner_credits_depleted": "creditos do workspace esgotados",
            "workspace_member_credits_depleted": "creditos do membro esgotados",
            "workspace_owner_usage_limit_reached": "limite do workspace atingido",
            "workspace_member_usage_limit_reached": "limite do membro atingido",
        }
        return labels.get(value, value.replace("_", " "))

    def _format_rate_limit_status(self, rate_limits):
        if not isinstance(rate_limits, dict):
            return ""
        pieces = []
        plan = rate_limits.get("planType")
        if plan and plan != "unknown":
            pieces.append(str(plan))
        limit_name = rate_limits.get("limitName") or rate_limits.get("limitId")
        if limit_name:
            pieces.append(str(limit_name))
        for label, key in (("janela", "primary"), ("extra", "secondary")):
            window = rate_limits.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if used is None:
                continue
            window_text = f"{label} {used}%"
            reset_text = self._format_reset_time(window.get("resetsAt"))
            if reset_text:
                window_text += f" reset {reset_text}"
            pieces.append(window_text)
        credits = rate_limits.get("credits")
        if isinstance(credits, dict):
            if credits.get("unlimited"):
                pieces.append("creditos ilimitados")
            elif credits.get("balance"):
                pieces.append(f"creditos {credits.get('balance')}")
        return " | ".join(pieces)

    def _format_token_usage_status(self, token_usage):
        if not isinstance(token_usage, dict):
            return ""
        total = token_usage.get("total") or {}
        last = token_usage.get("last") or {}
        total_tokens = total.get("totalTokens") if isinstance(total, dict) else None
        last_tokens = last.get("totalTokens") if isinstance(last, dict) else None
        context_window = token_usage.get("modelContextWindow")
        if total_tokens is not None and context_window:
            return f"ctx {self._compact_number(total_tokens)}/{self._compact_number(context_window)}"
        if total_tokens is not None:
            return f"tokens {self._compact_number(total_tokens)}"
        if last_tokens is not None:
            return f"ultima {self._compact_number(last_tokens)} tokens"
        return ""

    def _format_reset_time(self, value):
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return ""
        if timestamp <= 0:
            return ""
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        seconds = max(0, int(timestamp - time.time()))
        if seconds <= 0:
            return "agora"
        minutes = max(1, round(seconds / 60))
        if minutes < 60:
            return f"{minutes}min"
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h{minutes:02d}"

    def _compact_number(self, value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return str(value)
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}k"
        return str(number)

    def _find_codex_executable(self):
        candidates = []
        roots = [
            Path(os.getenv("ProgramFiles", "")) / "WindowsApps",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
        ]
        patterns = [
            "OpenAI.Codex_*\\app\\resources\\codex.exe",
            "OpenAI.Codex_*\\app\\resources\\codex",
            "codex.exe",
        ]
        for root in roots:
            if not root.exists():
                continue
            for pattern in patterns:
                try:
                    for candidate in sorted(root.glob(pattern), reverse=True):
                        if candidate.exists():
                            candidates.append(str(candidate))
                except OSError:
                    continue

        for executable in (shutil.which("codex.exe"), shutil.which("codex")):
            if executable:
                candidates.append(executable)

        for candidate in dict.fromkeys(candidates):
            if self._can_run_codex(candidate):
                return candidate
        return None

    def _can_run_codex(self, executable):
        try:
            process = subprocess.Popen(
                [executable, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=5)
            return process.returncode == 0 and "codex" in (output or "").lower()
        except Exception:
            return False

    def _codex_is_logged_in(self, executable=None):
        executable = executable or self._find_codex_executable()
        if not executable:
            return False
        try:
            process = subprocess.Popen(
                [executable, "login", "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=12)
            lower_output = (output or "").lower()
            return process.returncode == 0 and "not logged in" not in lower_output
        except Exception:
            return False

    def _build_system_instruction(self):
        return f"""Voce e um agente autonomo de engenharia de software integrado a IDE Merotec AI.
Idioma preferido: {self.language}.

Voce pode solicitar acoes da IDE usando tags especiais:
[READ: caminho/arquivo.py] para ler arquivo antes de alterar.
[READ: caminho/arquivo.py | linhas 120-260] para ler um intervalo especifico de arquivo grande.
[WRITE: caminho/arquivo.py] ... [/WRITE] para criar ou sobrescrever arquivo com backup automatico.
[REPLACE: caminho/arquivo.py] [OLD] trecho atual exato [/OLD] [NEW] trecho novo [/NEW] [/REPLACE] para trocar um trecho pequeno com backup automatico.
[SEARCH_TEXT: padrao | caminho/arquivo.py] para a IDE buscar termos ou regex em arquivo sem usar terminal.
[WEB_SEARCH: consulta objetiva] para a IDE buscar na internet quando a solucao depender de documentacao externa, fatos atuais ou erro desconhecido.
[SCAN_TEXT: caminho/arquivo.py] para a IDE localizar caracteres corrompidos/mojibake e problemas de texto sem usar terminal.
[FIX_MOJIBAKE: caminho/arquivo.py] para a IDE corrigir mojibake comum com backup automatico.
Para rodar terminal, envie uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest].
Para administrador no Windows, envie uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].
[OPEN_URL: http://127.0.0.1:porta/] para abrir uma URL no navegador interno da IDE.
[BROWSER_INSPECT: pagina] para ler texto, URL e elementos interativos reais da pagina aberta.
[BROWSER_CLICK: e3] para clicar no elemento numerado recebido por BROWSER_INSPECT.
[BROWSER_TYPE: e4 | texto] para preencher um campo sem enviar o formulario.
[BROWSER_SCROLL: down] ou [BROWSER_SCROLL: up] para rolar a pagina.
[SCREENSHOT: tela] para capturar a tela atual e devolver a imagem para analise.
[HUMAN_TEST: auto] para a IDE executar/abrir o app ou jogo, esperar a tela, capturar print real e devolver para analise visual.
[UNDO: caminho/arquivo.py] para restaurar o backup .bak.

Regras:
- Modo Codex: comporte-se como um agente de engenharia integrado, nao como chatbot comum.
- Use raciocinio altissimo: antes de responder, escolha o proximo passo que realmente muda, executa, valida ou conclui.
- Ciclo obrigatorio: entender a missao, escolher poucos arquivos relevantes, aplicar alteracao quando pedida, validar com comando/print quando possivel e fechar com resumo objetivo.\n- ENTREGA AUTÔNOMA DA IDE: em tarefas de criar, implementar ou corrigir, não devolva a tarefa ao usuário. Aplique a alteração; a IDE valida e testa automaticamente. Se a validação falhar, use a saída para corrigir e continue até aprovar ou atingir o limite técnico.
- Se a pergunta for simples e nao exigir projeto, responda diretamente sem tags.
- Se a missao for analise/planejamento, entregue diagnostico completo em texto; nao transforme analise em execucao ou edicao.
- Se a missao for implementacao/correcao, nao pare em "vou fazer"; use [READ], [REPLACE], [WRITE] e uma tag EXECUTE com comando real ate haver resultado verificavel.
- Se voce nao tiver resposta exata, se depender de fato atual, documentacao, versao de biblioteca/servico ou comportamento externo, use [WEB_SEARCH: consulta objetiva] antes de concluir.
- Se a resposta vier de contexto externo, entregue uma conclusao resumida e aplicavel dentro da IDE, citando o essencial em vez de despejar pesquisa bruta.
- Se for usar uma tag, responda com a tag diretamente. Nao escreva "vou", "irei" ou "preciso" antes da tag.
- Texto de intencao sem acao sera ignorado pela IDE. Acao real ou conclusao final sao as unicas saidas validas.
- Nunca diga que corrigiu, aplicou, alterou, rodou, testou ou validou sem enviar a tag real que faz isso.
- Correcao so conta com [REPLACE], [WRITE], [FIX_MOJIBAKE] ou [UNDO]; validacao so conta com uma tag EXECUTE/EXECUTE_ADMIN ja preenchida, [OPEN_URL] no navegador interno, [SCREENSHOT] ou [HUMAN_TEST].
- Para projeto grande, use o mapa do workspace, arquivos-chave e buscas pontuais. Nao tente ler tudo em sequencia.
- Depois de 2 ou 3 leituras estrategicas, tome decisao: editar, testar, abrir, capturar print ou concluir.
- Se a IDE avisar que substituiu leitura em massa por mapa do projeto, use o mapa e entregue resultado; nao peca nova lista de arquivos.
- Preserve o trabalho existente: prefira patches pequenos; nao recrie um projeto funcional se o usuario pediu corrigir uma parte.
- Ao corrigir bug, busque a causa no arquivo/camada provavel antes de alterar arquivos aleatorios.
- Ao testar, leia a saida, corrija a causa e teste de novo quando fizer sentido.
- Ao terminar uma missao, diga o que mudou, o que foi verificado e qualquer risco restante.
- Trabalhe com raciocinio alto e autonomia de agente senior: entenda a missao, leia o que faltar, altere, execute e corrija sem pedir o objetivo de novo.
- Nao revele raciocinio interno detalhado; mostre apenas a decisao, a acao e o resultado.
- Use apenas caminhos relativos ao workspace, como `app.py`, `src/main.py` ou `style.css`.
- Nunca use caminhos absolutos como `C:/...`.
- Use tags da IDE ou ferramentas diretas do app-server; escolha o caminho real mais confiavel para concluir a tarefa.
- Terminal e app-server podem ser usados para inspecao, validacao e comandos reais dentro do workspace.
- Para edicoes pequenas, prefira [WRITE]/[REPLACE]; se usar ferramenta direta do app-server, mantenha tudo dentro do workspace e relate o que mudou.
- Para alterar um trecho pequeno de arquivo grande, prefira [REPLACE] com o trecho OLD exatamente como foi lido pela IDE.
- Para procurar caracteres corrompidos, mojibake ou texto quebrado, use [SCAN_TEXT] ou uma inspecao direta equivalente.
- Para verificar se existe uma funcao, recurso, variavel, termo ou logica no arquivo, use [SEARCH_TEXT: padrao | arquivo] ou busca direta equivalente.
- Depois de receber resultado de SEARCH_TEXT para uma pergunta simples de verificacao, responda a conclusao; nao faca novas buscas parecidas.
- Para resolver problema que dependa de informacao atual, documentacao externa, erro desconhecido ou comportamento de biblioteca/servico que pode ter mudado, use [WEB_SEARCH: consulta objetiva] antes de concluir.
- Depois de receber resultado de WEB_SEARCH, use as fontes encontradas para decidir a proxima acao; nao repita a mesma busca sem motivo.
- Quando a varredura indicar mojibake comum, use [FIX_MOJIBAKE] antes de validar.
- Para build, run, testes, iniciar servidor ou abrir app sem necessidade visual, responda com uma tag EXECUTE contendo o comando real.
- Para comandos que realmente exigem administrador no Windows, responda com uma tag EXECUTE_ADMIN contendo o comando real. Nao escreva "como administrador" dentro de [EXECUTE].
- Nunca use reticencias, "comando", "comando real", texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real.
- Nunca copie literalmente `comando concreto` nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto.
- Nunca chame terminal, ferramenta de shell ou app-server com comando `...`, `comando`, `como administrador`, `--admin` ou outro placeholder; se nao houver comando real, entregue uma conclusao final.
- Para testar como usuario, validar tela, jogo, layout, print, fluxo visual ou "teste real", responda com [HUMAN_TEST: auto] em vez de ficar lendo arquivos.
- Para controlar uma pagina ja aberta, comece por [BROWSER_INSPECT: pagina] e use exatamente um BROWSER_CLICK, BROWSER_TYPE, BROWSER_SCROLL ou BROWSER_INSPECT por resposta.
- No modo irrestrito, use o navegador autonomamente tambem em sites externos para interacoes comuns; somente dados sensiveis e acoes destrutivas/financeiras podem pedir autorizacao.
- Para apps Flutter, `flutter run -d windows` ja faz build antes de executar no Windows.
- Para comandos que podem ficar rodando, como `flutter run`, `npm run dev` ou servidores locais, use uma tag EXECUTE com o comando real e finalize sua resposta; a IDE mantem o terminal aberto.
- Se um comando falhar, nao repita o mesmo comando antes de aplicar [WRITE] em pelo menos um arquivo suspeito ou ler um arquivo novo que explique a falha.
- Antes de alterar arquivo que voce ainda nao leu, prefira ler o trecho relevante por [READ] ou ferramenta direta equivalente.
- Se a IDE informar que o arquivo e grande, use o indice recebido e peca intervalos com [READ: arquivo | linhas inicio-fim] ate ter contexto suficiente.
- Evite reescrever um arquivo grande inteiro usando apenas o resumo; primeiro leia os intervalos exatos quando isso reduzir risco.
- Se a IDE informar "Leitura bloqueada para evitar ciclo infinito", pare de pedir READ desse arquivo e avance com [WRITE], uma tag EXECUTE com comando real ou uma conclusao objetiva.
- Para arquivos grandes em uma unica pagina, como HTML com CSS/JS embutidos, apos mapear cabecalho, estilos, estado principal e loop/renderizacao, pare de ler e execute a alteracao pedida.
- Prefira alteracoes pequenas, claras e verificaveis.
- Explique o resultado em portugues direto, sem enrolar.
- Quando executar algo, analise a saida e continue somente se necessario.
- Quando o usuario pedir para construir, reconstruir, corrigir ou alterar, nao pergunte "qual o proximo passo"; execute a acao.
- Quando receber contexto com "MISSAO ATIVA DA IA" ou "MISSAO ORIGINAL", trate essa missao como o objetivo principal ate concluir.
- Quando receber "DIAGNOSTICO DE FALHA GERADO PELA IDE", siga a camada provavel, leia/altere os arquivos suspeitos e nao repita o mesmo comando antes de corrigir a causa indicada.
- Em erros de build, identifique a camada antes de agir: Dart, Flutter dependencias, Android/Gradle, Windows CMake, C++ compile ou linker.
- Se a falha for Windows CMake/C++/linker, nao altere `lib/main.dart` nem `pubspec.yaml` sem evidencia direta.
- Para mudar arquivos, responda com tags [WRITE] ou [REPLACE] completas, sempre fechando as tags.
- REGRA PYTHON: antes de alterar um arquivo .py existente, use [READ] e espere o conteúdo real.
- REGRA PYTHON: use somente espaços, com 4 espaços por nível; nunca use tab para indentação.
- REGRA PYTHON: após def, class, if, for, while, try, except, with ou match, preserve o bloco indentado.
- REGRA PYTHON: se receber IndentationError, TabError ou SyntaxError, leia o arquivo indicado e corrija a estrutura; não repita o comando.
- Se estiver criando um app novo, escreva os arquivos diretamente; nao peca confirmacao.
- Seja objetivo. Evite introducao longa.
"""

    def _build_google_generation_config(self):
        if not types:
            return None
        return types.GenerateContentConfig(
            system_instruction=self._agent_protocol_system_instruction(),
            temperature=0.1,
            max_output_tokens=8192,
        )

    def _message_payload(self, prompt, code_context=None):
        text_content = f"Instrucao do usuario/sistema: {prompt}"
        if code_context:
            text_content += f"\n\n--- CONTEXTO PARA ANALISAR ---\n{code_context}"
        return text_content

    def _agent_protocol_system_instruction(self):
        language = getattr(self, "language", "portugues") or "portugues"
        return f"""Voce e o modelo de engenharia ativo dentro de uma IDE com ferramentas.
Responda em {language}, de forma direta. Para uma pergunta simples, responda em texto curto.

PROTOCOLO MEROTEC V4: esta instrução substitui regras anteriores desta conversa.
"Uma ação por vez" não significa "uma linha" para todo conteúdo: READ, SEARCH_TEXT e EXECUTE são linhas curtas; WRITE, REPLACE e PATCH são blocos multilinha fechados pelas respectivas tags. Nunca coloque código de edição apenas em texto explicativo.

Quando precisar agir no projeto, emita uma unica acao real por vez usando uma destas tags:
[READ: caminho/arquivo.py]
[SEARCH_TEXT: padrao | caminho]
[WRITE: caminho/arquivo.py] conteudo completo [/WRITE]
[REPLACE: caminho/arquivo.py] [OLD] trecho exato [/OLD] [NEW] novo trecho [/NEW] [/REPLACE]
[EXECUTE: comando real]
[OPEN_URL: http://127.0.0.1:porta/]
[BROWSER_INSPECT: pagina]
[BROWSER_CLICK: e3]
[BROWSER_TYPE: e4 | texto]
[BROWSER_SCROLL: down]
[SCREENSHOT: tela]
[HUMAN_TEST: auto]
[WEB_SEARCH: consulta]

Regras essenciais:
- Use somente caminhos relativos ao workspace.
- Nao invente resultado de leitura, edicao ou teste.
- Se faltar conteudo de arquivo, use READ ou SEARCH_TEXT.
- Em implementacao, nao entregue apenas explicacao ou plano: emita imediatamente a proxima tag de acao real.
- Apos cada resultado da IDE, decida a proxima acao ate editar e validar a solucao.
- Em analise solicitada pelo usuario, entregue o diagnostico final completo; nao narre o plano da resposta.
- Nunca mostre "vou fazer", "meu objetivo" ou uma lista do que pretende cobrir sem executar ou concluir.
- Nunca use reticencias ou placeholders dentro de tags.
- Preserve o trabalho existente e prefira mudancas pequenas.
- REGRA PYTHON: antes de alterar um arquivo .py existente, use [READ] e espere o conteúdo real.
- REGRA PYTHON: use somente espaços, com 4 espaços por nível; nunca use tab para indentação.
- REGRA PYTHON: após def, class, if, for, while, try, except, with ou match, preserve o bloco indentado.
- REGRA PYTHON: se receber IndentationError, TabError ou SyntaxError, leia o arquivo indicado e corrija a estrutura; não repita o comando.
- Se nenhuma acao for necessaria, entregue a resposta final sem tags.
"""

    def _lm_studio_system_instruction(self):
        """Compatibilidade para configuracoes e testes antigos."""
        return self._agent_protocol_system_instruction()

    def _lm_studio_message_payload(self, prompt, code_context=None):
        return self._agent_message_payload(prompt, code_context)

    def _agent_message_payload(self, prompt, code_context=None):
        text = f"Pedido do usuario: {prompt}"
        simple_checks = {
            "teste",
            "test",
            "oi",
            "ola",
            "olá",
            "bom dia",
            "boa tarde",
            "boa noite",
            "voce esta funcionando",
            "você está funcionando",
        }
        normalized_prompt = re.sub(r"[^a-z0-9áàâãéêíóôõúç ]", "", str(prompt or "").lower()).strip()
        if self._lm_studio_is_analysis_prompt(normalized_prompt):
            text += (
                "\n\nInstrucao obrigatoria: entregue agora a analise final detalhada do projeto. "
                "Nao descreva o que pretende analisar e nao termine em um esboco incompleto. "
                "Cubra arquitetura, componentes, fluxo, tecnologias, riscos e proximas melhorias com evidencias do contexto. "
                "Use no maximo 850 tokens, com secoes objetivas e sem pre-planejamento."
            )
        elif any(
            marker in normalized_prompt
            for marker in ("crie", "construa", "implemente", "corrija", "altere", "adicione", "remova")
        ):
            text += (
                "\n\nInstrucao obrigatoria: esta e uma tarefa de implementacao. "
                "Nao responda com plano ou tutorial; emita a proxima tag real para ler, alterar ou validar o projeto."
            )
        if code_context and normalized_prompt not in simple_checks:
            compacted = self._compact_lm_studio_context(code_context)
            text += f"\n\nContexto essencial da IDE:\n{compacted}"
        return text

    @staticmethod
    def _lm_studio_is_analysis_prompt(prompt):
        normalized = str(prompt or "").lower()
        return "projeto" in normalized and any(
            marker in normalized
            for marker in ("analis", "avali", "revis", "diagnost", "arquitetura")
        )

    @staticmethod
    def _clean_lm_studio_analysis_answer(answer):
        text = str(answer or "").strip()
        final_markers = (
            "**Análise Detalhada",
            "**Analise Detalhada",
            "# Análise Detalhada",
            "# Analise Detalhada",
        )
        positions = [text.find(marker) for marker in final_markers if text.find(marker) >= 0]
        if positions:
            return text[min(positions):].strip()
        return text

    def _compact_lm_studio_context(self, code_context):
        context = str(code_context or "").strip()
        context = re.sub(
            r"\n*MODO CODEX DA IDE:.*?(?=\n\nAlteracoes recentes feitas pela IDE)",
            "\n",
            context,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        limit = max(2000, int(getattr(self, "lm_studio_max_input_chars", 14000) or 14000))
        if len(context) <= limit:
            return context

        sections = self.split_context_sections(context)
        ranked = []
        priorities = (
            ("contexto adicional", 120),
            ("missao ativa", 110),
            ("briefing inteligente", 100),
            ("mapa inteligente", 95),
            ("conversa recente", 80),
            ("arquivos do workspace", 70),
            ("alteracoes recentes", 60),
            ("projeto atual", 55),
            ("sub-rede", 30),
        )
        for index, section in enumerate(sections):
            lower = section.lower()
            if "modo codex da ide" in lower:
                continue
            score = next((value for marker, value in priorities if marker in lower), 20)
            ranked.append((score, index, section))

        chosen = []
        remaining = limit - 120
        for score, index, section in sorted(ranked, key=lambda item: (-item[0], item[1])):
            if remaining <= 300:
                break
            allowance = min(len(section), 4000 if score >= 100 else 2400, remaining)
            if len(section) > allowance:
                head = max(200, int(allowance * 0.72))
                tail = max(100, allowance - head - 45)
                section = section[:head].rstrip() + "\n[...contexto reduzido...]\n" + section[-tail:].lstrip()
            chosen.append((index, section))
            remaining -= len(section) + 2

        compacted = "\n\n".join(section for _, section in sorted(chosen))
        return "[Contexto compactado para o modelo local]\n" + compacted[:limit]

    def _openai_input(self, prompt, code_context=None, image_path=None):
        content = [{"type": "input_text", "text": self._agent_message_payload(prompt, code_context)}]

        if image_path and os.path.exists(image_path):
            path = Path(image_path)
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"})

        return [{"role": "user", "content": content}]

    def generate_stream(self, prompt, code_context=None):
        if self.provider in {"web_chat", "codex", "openai", "lm_studio"} or not self.chat_session:
            return None
        try:
            return self.chat_session.send_message_stream(self._agent_message_payload(prompt, code_context))
        except Exception:
            return None

    def generate_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
    ):
        self.cancel_requested = False
        response = self._generate_solution_with_provider(
            self.provider,
            prompt,
            image_path=image_path,
            code_context=code_context,
            stream_callback=stream_callback,
            workspace_path=workspace_path,
            approval_callback=approval_callback,
        )
        if self.provider == "local_gguf" and not self.local_gguf_allow_external_fallback:
            return response
        if self.provider == "lm_studio" and not self.lm_studio_allow_external_fallback:
            return response
        if not self.should_try_external_ai_fallback(response):
            return response

        fallback_response = self.try_external_ai_fallback(
            prompt,
            image_path=image_path,
            code_context=code_context,
            stream_callback=stream_callback,
            workspace_path=workspace_path,
            approval_callback=approval_callback,
            failed_response=response,
        )
        return fallback_response or response

    def _generate_solution_with_provider(
        self,
        provider,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
    ):
        provider = (provider or self.provider or "codex").strip().lower()

        if provider == "web_chat":
            return self._generate_web_chat_solution(
                prompt,
                image_path=image_path,
                code_context=code_context,
                stream_callback=stream_callback,
                workspace_path=workspace_path,
            )

        if provider == "local_gguf":
            return self._generate_local_gguf_solution(
                prompt,
                code_context=code_context,
                stream_callback=stream_callback,
            )

        if provider == "codex":
            return self._generate_codex_solution(
                prompt,
                image_path,
                code_context,
                stream_callback=stream_callback,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
        if provider == "openai":
            return self._generate_openai_solution(prompt, image_path, code_context)
        if provider == "lm_studio":
            return self._generate_lm_studio_solution(
                prompt,
                image_path,
                code_context,
                stream_callback=stream_callback,
            )
        return self._generate_google_solution(prompt, image_path, code_context)

    def _web_chat_attachment_payload(self, image_path=None):
        """Codifica um anexo visual para o drop programático do Chat Web."""
        if not image_path or not self.web_chat_auto_attach_media:
            return []
        try:
            path = Path(image_path)
            if not path.is_file():
                return []
            size = path.stat().st_size
            # O limite evita congelar o WebView ao inserir um data URL enorme.
            if size > 10 * 1024 * 1024:
                return []
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return [{
                "name": path.name,
                "mime_type": mime_type,
                "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }]
        except OSError:
            return []

    def _web_chat_bridge_for(self, workspace_path=None):
        bridge = getattr(self, "web_chat_bridge", None)
        if bridge is not None:
            bridge.profile.update(
                {
                    "web_chat_url": self.web_chat_url,
                    "web_chat_timeout_seconds": self.web_chat_timeout_seconds,
                    "web_chat_message_chars": self.web_chat_message_chars,
                    "web_chat_auto_attach_media": self.web_chat_auto_attach_media,
                }
            )
            return bridge
        runtime_path = Path(__file__).with_name("browser_runtime.py")
        bridge = WebChatBridge(
            runtime_path=runtime_path,
            settings_path=app_config.APP_SETTINGS_FILE,
            profile={
                **getattr(self, "web_chat_profile", {}),
                "web_chat_url": self.web_chat_url,
                "web_chat_timeout_seconds": self.web_chat_timeout_seconds,
                "web_chat_message_chars": self.web_chat_message_chars,
                "web_chat_auto_attach_media": self.web_chat_auto_attach_media,
            },
            workspace_path=workspace_path,
            log=lambda message: None,
        )
        self.web_chat_bridge = bridge
        return bridge

    def _generate_web_chat_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
    ):
        try:
            if stream_callback:
                stream_callback("Chat Web: preparando sessão do projeto no navegador interno.\n")
            bridge = self._web_chat_bridge_for(workspace_path)
            full_prompt = (
                self._agent_protocol_system_instruction()
                + "\n\n"
                + self._agent_message_payload(prompt, code_context)
            )
            attachments = self._web_chat_attachment_payload(image_path)
            # Limpa o estado antes de cada rodada. Sem isso, uma entrega antiga
            # podia ser usada para avaliar o print da rodada atual.
            self.latest_web_chat_delivery = {
                "ok": False,
                "attachments_requested": bool(attachments),
                "attachment_count": 0,
                "attachment_error": "",
                "attachment_delivery": "pending" if attachments else "none",
                "visual_receipt": "not_requested" if not attachments else "pending",
                "response_received": False,
                "error": "",
            }
            result = bridge.chat(
                full_prompt,
                workspace_path=workspace_path,
                attachments=attachments,
                timeout=self.web_chat_timeout_seconds,
                stream_callback=stream_callback,
            )
            if not isinstance(result, dict):
                result = {"ok": False, "error": "Resposta inválida do Chat Web."}
            self.latest_web_chat_artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
            response = str(result.get("response") or "").strip()

            # O recibo visual só pode ser decidido a partir da resposta desta
            # mesma rodada. O campo existia na UI, mas nunca era preenchido;
            # por isso toda análise com screenshot era tratada como falha.
            visual_receipt = "not_requested"
            if attachments:
                visual_receipt = "unknown"
                if re.search(r"^\s*\[VISUAL_EVIDENCE_RECEIVED\]", response, re.IGNORECASE):
                    visual_receipt = "received"
                elif re.search(r"^\s*\[VISUAL_EVIDENCE_MISSING\]", response, re.IGNORECASE):
                    visual_receipt = "missing"

            self.latest_web_chat_delivery = {
                "ok": bool(result.get("ok")),
                "attachments_requested": bool(attachments),
                "attachment_count": int(result.get("attachment_count") or 0),
                "attachment_error": str(result.get("attachment_error") or ""),
                "attachment_delivery": str(result.get("attachment_delivery") or ("none" if not attachments else "unknown")),
                "visual_receipt": visual_receipt,
                "response_received": bool(response),
                "error": str(result.get("error") or ""),
            }
            if not result.get("ok"):
                details = str(result.get("error") or "erro desconhecido")
                attachment_detail = str(result.get("attachment_error") or "")
                if attachment_detail:
                    details += " | anexo: " + attachment_detail
                return "Chat Web não concluiu a resposta: " + details
            if not response:
                artifacts = self.latest_web_chat_artifacts or {}
                if artifacts.get("images") or artifacts.get("audio"):
                    return "O Chat Web gerou mídia, mas não retornou texto. Abra a conversa restaurada no navegador para revisar o resultado."
                return "O Chat Web terminou sem texto de resposta."
            if attachments and result.get("attachment_error") and stream_callback:
                stream_callback(
                    "Chat Web: o site não aceitou o print automaticamente; a IDE marcou a entrega como incompleta e não deve concluir a validação visual.\n"
                )
            return response
        except Exception as exc:
            delivery = self.latest_web_chat_delivery if isinstance(self.latest_web_chat_delivery, dict) else {}
            delivery.update({
                "ok": False,
                "response_received": False,
                "error": str(exc),
            })
            self.latest_web_chat_delivery = delivery
            return f"Erro no Chat Web: {exc}"

    def _generate_local_gguf_solution(self, prompt, code_context=None, stream_callback=None):
        if stream_callback:
            stream_callback("Modelo local: preparando contexto...\n")
        config_error = self.validate_local_gguf_for_generation()
        if config_error:
            return config_error
        try:
            max_output_tokens = self.local_gguf_safe_output_tokens()
            input_token_budget = self.local_gguf_input_token_budget(max_output_tokens)
            user_text = (
                self._agent_protocol_system_instruction()
                + "\n\n"
                + self._agent_message_payload(prompt, code_context)
            )
            original_tokens = self.estimate_local_gguf_tokens(user_text)
            user_text = self.compact_local_gguf_prompt(user_text, input_token_budget)
            compacted_tokens = self.estimate_local_gguf_tokens(user_text)
            if stream_callback and original_tokens > compacted_tokens:
                stream_callback(
                    f"Modelo local: contexto reduzido de ~{original_tokens} para ~{compacted_tokens} tokens.\n"
                )
            if stream_callback:
                stream_callback("Modelo local: gerando resposta offline...\n")
            return self._run_local_gguf_completion_subprocess(user_text, max_output_tokens)
        except Exception as exc:
            return f"Erro na execucao do modelo local GGUF: {exc}"

    def validate_local_gguf_for_generation(self):
        path = self.resolve_local_gguf_path()
        if not path:
            return "Modelo local GGUF nao configurado. Abra Configurar IA e selecione um arquivo .gguf."
        if not path.exists():
            return f"Modelo local GGUF nao encontrado: {path}"
        if path.suffix.lower() != ".gguf":
            return f"O arquivo selecionado nao e um modelo .gguf: {path}"
        if int(getattr(self, "local_gguf_timeout_seconds", 0) or 0) <= 0:
            return ""
        if not self.llama_cpp_available():
            return "llama-cpp-python nao esta instalado neste Python. Instale as dependencias do requirements.txt."
        return ""

    def _run_local_gguf_completion_subprocess(self, user_text, max_output_tokens):
        if int(self.local_gguf_timeout_seconds) <= 0:
            return (
                "Tempo esgotado no modelo local GGUF. "
                "A tentativa GGUF esta desativada nesta configuracao; usando fallback local extrativo quando disponivel."
            )
        path = self.resolve_local_gguf_path()
        if not path:
            return "Modelo local GGUF nao configurado. Abra Configurar IA e selecione um arquivo .gguf."
        lock = self._ensure_local_gguf_lock()
        with lock:
            return self._run_local_gguf_completion_worker(path, user_text, max_output_tokens)

    def _run_local_gguf_completion_worker(self, path, user_text, max_output_tokens):
        start_error = self.ensure_local_gguf_worker(path)
        if start_error:
            return start_error

        self.local_worker_next_request_id = getattr(self, "local_worker_next_request_id", 0) + 1
        request_id = self.local_worker_next_request_id
        request = {
            "id": request_id,
            "prompt": user_text,
            "max_tokens": max_output_tokens,
        }
        process = self.local_worker_process
        if not process or process.poll() is not None or not process.stdin:
            self.reset_local_gguf_worker_state()
            return "Erro na execucao do modelo local GGUF: worker local nao esta ativo."

        previous_active = self.active_process
        self.active_process = process
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            timeout_at = time.time() + max(1, int(self.local_gguf_timeout_seconds))
            while time.time() < timeout_at:
                if self.cancel_requested:
                    self.terminate_local_gguf_worker()
                    return "Tarefa cancelada."
                remaining = max(0.05, min(0.25, timeout_at - time.time()))
                try:
                    event = self.local_worker_events.get(timeout=remaining)
                except queue.Empty:
                    continue
                if event.get("event") == "exit":
                    self.reset_local_gguf_worker_state()
                    return "Erro na execucao do modelo local GGUF: worker local encerrou."
                if event.get("id") != request_id:
                    continue
                if event.get("error"):
                    return f"Erro na execucao do modelo local GGUF: {event.get('error')}"
                response = str(event.get("response") or "").strip()
                if response:
                    return response
                return "O modelo local GGUF terminou sem devolver texto."

            self.terminate_local_gguf_worker()
            return (
                "Tempo esgotado no modelo local GGUF. "
                f"Ele nao respondeu em {self.local_gguf_timeout_seconds}s; reiniciei o worker local."
            )
        except (BrokenPipeError, OSError) as exc:
            self.reset_local_gguf_worker_state()
            return f"Erro na execucao do modelo local GGUF: {exc}"
        finally:
            if self.active_process is process:
                self.active_process = previous_active

    def ensure_local_gguf_worker(self, path):
        model_key = (
            str(path),
            int(self.local_gguf_n_ctx),
            int(self.local_gguf_n_threads),
            int(self.local_gguf_n_batch),
            int(self.local_gguf_n_gpu_layers),
        )
        process = getattr(self, "local_worker_process", None)
        if (
            process
            and process.poll() is None
            and self.local_worker_ready
            and self.local_worker_model_key == model_key
        ):
            return ""

        self.terminate_local_gguf_worker()
        self.local_worker_events = queue.Queue()
        self.local_worker_ready = False
        self.local_worker_model_key = model_key

        payload = {
            "model_path": str(path),
            "n_ctx": self.local_gguf_n_ctx,
            "n_threads": self.local_gguf_n_threads,
            "n_batch": self.local_gguf_n_batch,
            "n_gpu_layers": self.local_gguf_n_gpu_layers,
        }
        worker_code = r'''
import json
import sys

def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)

config_line = sys.stdin.readline()
payload = json.loads(config_line or "{}")
try:
    from llama_cpp import Llama

    llm = Llama(
        model_path=payload["model_path"],
        n_ctx=int(payload.get("n_ctx") or 4096),
        n_threads=int(payload.get("n_threads") or 4),
        n_batch=int(payload.get("n_batch") or 256),
        n_gpu_layers=int(payload.get("n_gpu_layers") or 0),
        verbose=False,
    )
    system_prompt = (
        "Voce e o modelo de engenharia ativo dentro de uma IDE com ferramentas. "
        "Em implementacao, responda com exatamente uma tag real do protocolo recebido, sem plano. "
        "Nao invente leituras, edicoes ou testes; aguarde o resultado da IDE antes da proxima acao. "
        "Nao repita o prompt."
    )
    emit({"event": "ready"})
except Exception as exc:
    emit({"event": "error", "error": str(exc)})
    sys.exit(1)

for line in sys.stdin:
    try:
        request = json.loads(line or "{}")
        if request.get("cmd") == "shutdown":
            break
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.get("prompt") or ""},
            ],
            max_tokens=int(request.get("max_tokens") or 160),
            temperature=0.2,
            stop=["<|im_end|>", "Usuario:", "User:", "System:", "###"],
        )
        choices = response.get("choices") if isinstance(response, dict) else None
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content") or choices[0].get("text") or "")
        emit({"id": request.get("id"), "response": str(content).strip()})
    except Exception as exc:
        emit({"id": request.get("id") if isinstance(request, dict) else None, "error": str(exc)})
'''
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", worker_code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.local_worker_process = process
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            event_queue = self.local_worker_events
            self.local_worker_reader_thread = threading.Thread(
                target=self._read_local_gguf_worker_events,
                args=(process, event_queue),
                daemon=True,
            )
            self.local_worker_reader_thread.start()
        except Exception as exc:
            self.reset_local_gguf_worker_state()
            return f"Erro na execucao do modelo local GGUF: {exc}"

        timeout_at = time.time() + max(1, int(self.local_gguf_timeout_seconds))
        while time.time() < timeout_at:
            if self.cancel_requested:
                self.terminate_local_gguf_worker()
                return "Tarefa cancelada."
            remaining = max(0.05, min(0.25, timeout_at - time.time()))
            try:
                event = self.local_worker_events.get(timeout=remaining)
            except queue.Empty:
                continue
            if event.get("event") == "ready":
                self.local_worker_ready = True
                self.client = "local-gguf-worker"
                return ""
            if event.get("event") == "error":
                error = event.get("error") or "falha desconhecida"
                self.reset_local_gguf_worker_state()
                return f"Erro na execucao do modelo local GGUF: {error}"
            if event.get("event") == "exit":
                self.reset_local_gguf_worker_state()
                return "Erro na execucao do modelo local GGUF: worker local encerrou ao carregar."

        self.terminate_local_gguf_worker()
        return (
            "Tempo esgotado no modelo local GGUF. "
            f"Ele nao carregou em {self.local_gguf_timeout_seconds}s; ajuste o timeout ou use um modelo menor."
        )

    def _read_local_gguf_worker_events(self, process, event_queue):
        try:
            for line in process.stdout or []:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    event_queue.put(event)
        finally:
            event_queue.put({"event": "exit"})

    def terminate_local_gguf_worker(self):
        process = getattr(self, "local_worker_process", None)
        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    process.stdin.flush()
            except OSError:
                pass
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
        self.reset_local_gguf_worker_state()

    def reset_local_gguf_worker_state(self):
        self.local_worker_process = None
        self.local_worker_ready = False
        self.local_worker_model_key = None
        self.client = "local-gguf" if self.local_gguf_file_configured() else None

    def _run_local_gguf_completion_oneshot_subprocess(self, user_text, max_output_tokens):
        if int(self.local_gguf_timeout_seconds) <= 0:
            return (
                "Tempo esgotado no modelo local GGUF. "
                "A tentativa GGUF esta desativada nesta configuracao; usando fallback local extrativo quando disponivel."
            )
        path = self.resolve_local_gguf_path()
        if not path:
            return "Modelo local GGUF nao configurado. Abra Configurar IA e selecione um arquivo .gguf."
        payload = {
            "model_path": str(path),
            "n_ctx": self.local_gguf_n_ctx,
            "n_threads": self.local_gguf_n_threads,
            "n_batch": self.local_gguf_n_batch,
            "n_gpu_layers": self.local_gguf_n_gpu_layers,
            "max_tokens": max_output_tokens,
            "prompt": user_text,
        }
        worker_code = r'''
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
try:
    from llama_cpp import Llama

    llm = Llama(
        model_path=payload["model_path"],
        n_ctx=int(payload.get("n_ctx") or 4096),
        n_threads=int(payload.get("n_threads") or 4),
        n_batch=int(payload.get("n_batch") or 256),
        n_gpu_layers=int(payload.get("n_gpu_layers") or 0),
        verbose=False,
    )
    system_prompt = (
        "Voce e o modelo de engenharia ativo dentro de uma IDE com ferramentas. "
        "Em implementacao, responda com exatamente uma tag real do protocolo recebido, sem plano. "
        "Nao invente leituras, edicoes ou testes; aguarde o resultado da IDE antes da proxima acao. "
        "Nao repita o prompt."
    )
    response = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload.get("prompt") or ""},
        ],
        max_tokens=int(payload.get("max_tokens") or 160),
        temperature=0.2,
        stop=["<|im_end|>", "Usuario:", "User:", "System:", "###"],
    )
    choices = response.get("choices") if isinstance(response, dict) else None
    content = ""
    if choices:
        content = ((choices[0].get("message") or {}).get("content") or choices[0].get("text") or "")
    print(json.dumps({"response": str(content).strip()}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
'''
        try:
            process = subprocess.run(
                [sys.executable, "-c", worker_code],
                input=json.dumps(payload, ensure_ascii=False),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.local_gguf_timeout_seconds,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            return (
                "Tempo esgotado no modelo local GGUF. "
                f"Ele nao respondeu em {self.local_gguf_timeout_seconds}s; usando fallback local extrativo quando disponivel."
            )

        output = (process.stdout or "").strip()
        try:
            data = json.loads(output.splitlines()[-1]) if output else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        if process.returncode != 0:
            error = data.get("error") or (process.stderr or "").strip() or "falha desconhecida"
            return f"Erro na execucao do modelo local GGUF: {error}"
        response = str(data.get("response") or "").strip()
        if not response:
            return "O modelo local GGUF terminou sem devolver texto."
        return response

    def _run_local_gguf_completion(self, user_text, max_output_tokens):
        if hasattr(self.local_llm, "create_chat_completion"):
            response = self.local_llm.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Voce e o modelo de engenharia ativo dentro de uma IDE com ferramentas. "
                            "Em implementacao, responda com exatamente uma tag real do protocolo recebido, sem plano. "
                            "Nao invente leituras, edicoes ou testes; aguarde o resultado da IDE antes da proxima acao. "
                            "Nao repita o prompt."
                        ),
                    },
                    {"role": "user", "content": user_text},
                ],
                max_tokens=max_output_tokens,
                temperature=0.2,
                stop=["<|im_end|>", "Usuario:", "User:", "System:", "###"],
            )
            choices = response.get("choices") if isinstance(response, dict) else None
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if content:
                    return str(content).strip()

        prompt_text = (
            "Sistema: Voce e o modelo de engenharia ativo dentro de uma IDE com ferramentas. "
            "Em implementacao, responda com exatamente uma tag real do protocolo recebido, sem plano. "
            "Nao invente leituras, edicoes ou testes; aguarde o resultado da IDE antes da proxima acao. "
            "Nao repita o prompt.\n\n"
            f"Usuario:\n{user_text}\n\nResposta:"
        )
        output = self.local_llm(
            prompt_text,
            max_tokens=max_output_tokens,
            temperature=0.2,
            stop=["<|im_end|>", "Usuario:", "User:", "System:", "###"],
        )
        choices = output.get("choices") if isinstance(output, dict) else None
        if choices:
            return str(choices[0].get("text", "")).strip()
        return str(output or "").strip()

    def local_gguf_safe_output_tokens(self):
        return min(
            max(32, int(self.local_gguf_max_tokens)),
            max(32, int(self.local_gguf_n_ctx) // 8),
        )

    def local_gguf_input_token_budget(self, max_output_tokens):
        context_budget = max(256, int(self.local_gguf_n_ctx) - int(max_output_tokens) - 256)
        return min(context_budget, max(256, int(self.local_gguf_max_input_tokens)))

    def estimate_local_gguf_tokens(self, text):
        text = str(text or "")
        tokenizer = getattr(self.local_llm, "tokenize", None)
        if callable(tokenizer):
            try:
                return len(tokenizer(text.encode("utf-8", errors="ignore"), add_bos=False))
            except Exception:
                pass
        return max(1, len(text) // 4)

    def compact_local_gguf_prompt(self, text, token_budget):
        text = str(text or "")
        if self.estimate_local_gguf_tokens(text) <= token_budget:
            return text

        sections = self.split_context_sections(text)
        if not sections:
            return self.trim_text_to_local_token_budget(text, token_budget)

        priority = (
            "Instrucao do usuario/sistema",
            "MISSAO ATIVA DA IA",
            "BRIEFING INTELIGENTE DA IDE",
            "Arquivos do workspace",
            "MAPA INTELIGENTE DO PROJETO",
            "Projeto atual",
            "Alteracoes recentes",
            "Conversa recente",
        )
        selected = []
        remaining = max(128, token_budget)
        for title in priority:
            match = next((section for section in sections if title.lower() in section.lower()), "")
            if not match:
                continue
            portion_budget = max(96, min(remaining, token_budget // 3 if selected else token_budget // 2))
            compact = self.trim_text_to_local_token_budget(match, portion_budget)
            selected.append(compact)
            remaining -= self.estimate_local_gguf_tokens(compact)
            if remaining <= 160:
                break

        if not selected:
            selected.append(self.trim_text_to_local_token_budget(text, token_budget))

        compacted = (
            "CONTEXTO COMPACTADO PARA MODELO LOCAL GGUF:\n"
            "O contexto original excedeu a janela do modelo; responda com base nos trechos essenciais abaixo.\n\n"
            + "\n\n".join(selected)
        )
        return self.trim_text_to_local_token_budget(compacted, token_budget)

    def split_context_sections(self, text):
        chunks = re.split(r"\n(?=(?:--- |[A-Z0-9 _/.-]{8,}:|Arquivos do workspace:|Alteracoes recentes|Conversa recente))", text or "")
        return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]

    def trim_text_to_local_token_budget(self, text, token_budget):
        text = str(text or "")
        if self.estimate_local_gguf_tokens(text) <= token_budget:
            return text
        token_count = max(1, self.estimate_local_gguf_tokens(text))
        char_budget = max(800, int(len(text) * (float(token_budget) / token_count) * 0.82))
        if len(text) <= char_budget:
            return text
        head = max(400, int(char_budget * 0.68))
        tail = max(240, char_budget - head)
        return (
            text[:head].rstrip()
            + "\n\n[... contexto compactado para caber no modelo local ...]\n\n"
            + text[-tail:].lstrip()
        )

    def should_try_external_ai_fallback(self, response):
        text = str(response or "").strip()
        if not text:
            return True
        normalized = text.lower()
        failure_markers = (
            "alta demanda",
            "capacity",
            "codex nao conseguiu iniciar",
            "codex nao foi encontrado",
            "nao esta logado",
            "sem login",
            "insufficient_quota",
            "sem cota",
            "cota disponivel",
            "creditos esgotados",
            "limite atingido",
            "rate limit",
            "usage limit",
            "configure openai_api_key",
            "configure google_api_key",
            "pacote google genai nao esta instalado",
            "erro no chat web",
            "chat web não concluiu",
            "chat web nao concluiu",
            "navegador interno",
            "erro no motor openai",
            "erro no motor genai",
            "erro no lm studio",
            "nao consegui conectar ao lm studio",
            "lm studio sem modelo configurado",
            "o lm studio demorou mais",
            "erro na requisi",
            "chave inserida foi rejeitada",
            "modelo `",
            "nao foi localizado",
            "modelo local gguf nao",
            "erro ao carregar modelo local gguf",
            "erro na execucao do modelo local gguf",
            "tempo esgotado no modelo local gguf",
            "llama-cpp-python nao esta instalado",
            "terminou sem devolver texto",
            "app-server retornou erro",
            "service unavailable",
            "temporar",
            "overloaded",
        )
        return any(marker in normalized for marker in failure_markers)

    def configured_external_ai_fallback_providers(self):
        providers = []
        current = (self.provider or "").strip().lower()
        if current == "local_gguf" and not self.local_gguf_allow_external_fallback:
            return []
        if current == "lm_studio" and not self.lm_studio_allow_external_fallback:
            return []

        if current != "web_chat" and self.web_chat_url:
            providers.append("web_chat")
        if current != "codex":
            providers.append("codex")
        if current != "openai" and self.openai_api_key:
            providers.append("openai")
        if current != "google" and self.google_api_key and GoogleClient:
            providers.append("google")
        if (
            current != "lm_studio"
            and getattr(self, "lm_studio_base_url", "")
            and getattr(self, "lm_studio_model_name", "")
        ):
            providers.append("lm_studio")
        try:
            local_ready = bool(self.local_gguf_is_ready())
        except Exception:
            local_ready = False
        if current != "local_gguf" and local_ready:
            providers.append("local_gguf")

        return [
            provider
            for provider in providers
            if provider in {"web_chat", "codex", "openai", "google", "lm_studio", "local_gguf"}
        ]

    def try_external_ai_fallback(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
        failed_response="",
    ):
        if not getattr(self, "external_ai_fallback_enabled", True):
            return None

        providers = self.configured_external_ai_fallback_providers()
        if not providers:
            return None

        original_state = self._snapshot_provider_state()
        try:
            for provider in providers:
                if self.cancel_requested:
                    return None
                label = self.external_provider_label(provider)
                if stream_callback:
                    stream_callback(f"\nFallback externo: tentando {label}...\n")
                self._activate_provider(provider)
                response = self._generate_solution_with_provider(
                    provider,
                    prompt,
                    image_path=image_path,
                    code_context=code_context,
                    stream_callback=stream_callback,
                    workspace_path=workspace_path,
                    approval_callback=approval_callback,
                )
                if response and not self.should_try_external_ai_fallback(response):
                    return f"[Fallback externo: {label}]\n\n{response}"
            return None
        finally:
            self._restore_provider_state(original_state)

    def external_provider_label(self, provider):
        labels = {
            "web_chat": "Chat Web",
            "codex": "Codex/ChatGPT",
            "openai": "OpenAI/OpenRouter",
            "lm_studio": "LM Studio Local",
            "google": "Gemini",
            "local_gguf": "Modelo Local GGUF",
        }
        return labels.get(provider, provider.upper())

    def _snapshot_provider_state(self):
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "client": self.client,
            "chat_session": self.chat_session,
            "local_llm": self.local_llm,
            "generation_config": self.generation_config,
        }

    def _restore_provider_state(self, state):
        self.provider = state["provider"]
        self.model_id = state["model_id"]
        self.client = state["client"]
        self.chat_session = state["chat_session"]
        self.local_llm = state["local_llm"]
        self.generation_config = state["generation_config"]

    def _activate_provider(self, provider):
        self.provider = (provider or "codex").strip().lower()
        self.model_id = self._resolve_model_id()
        self.chat_session = None
        if self.provider == "codex":
            self.codex_executable = self._find_codex_executable()
            self.client = "codex-cli" if self.codex_executable and self._codex_is_logged_in(self.codex_executable) else None
            return
        if self.provider == "openai":
            self.client = "openai-http" if self.openai_api_key else None
            return
        if self.provider == "lm_studio":
            self.client = "lm-studio-http" if self.lm_studio_base_url and self.lm_studio_model_name else None
            return
        if self.provider == "google" and self.google_api_key and GoogleClient:
            self.client = GoogleClient(api_key=self.google_api_key)
            self.generation_config = self._build_google_generation_config()
            self.reset_session()
            return
        if self.provider == "local_gguf":
            self.client = "local-gguf" if self.local_gguf_file_configured() else None
            return
        self.client = None

    def _generate_codex_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
    ):
        executable = self._find_codex_executable()
        if not executable:
            return (
                "Codex nao foi encontrado no Windows. A IDE tentara abrir o instalador automaticamente. "
                "Depois de instalar, faca login no Codex e tente novamente."
            )
        if not self._codex_is_logged_in(executable):
            return (
                "O Codex que a IDE chama ainda nao esta logado no Windows. "
                "Clique em `Entrar Codex`, conclua o login do Codex CLI e tente novamente."
            )

        for effort in self._codex_reasoning_efforts():
            app_server_response = self._generate_codex_app_server_solution(
                executable,
                prompt,
                image_path=image_path,
                code_context=code_context,
                stream_callback=stream_callback,
                reasoning_effort=effort,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
            if app_server_response:
                if self._is_capacity_message(app_server_response):
                    fallback_response = self._try_codex_fallback_models(
                        executable,
                        prompt,
                        image_path=image_path,
                        code_context=code_context,
                        stream_callback=stream_callback,
                        reasoning_effort=effort,
                        workspace_path=workspace_path,
                        approval_callback=approval_callback,
                    )
                    if fallback_response:
                        return fallback_response
                if self._is_codex_progress_timeout(app_server_response):
                    if stream_callback:
                        stream_callback(
                            "\nCodex app-server ficou silencioso; continuando pelo executor direto...\n"
                        )
                    break
                return app_server_response

        last_exec_response = ""
        for effort in self._codex_reasoning_efforts():
            last_exec_response = self._generate_codex_exec_solution(
                executable,
                prompt,
                image_path,
                code_context,
                stream_callback=stream_callback,
                reasoning_effort=effort,
                workspace_path=workspace_path,
            )
            if last_exec_response and not self._is_reasoning_effort_error(last_exec_response):
                return last_exec_response
        return last_exec_response or "Codex nao conseguiu iniciar a tarefa."

    def _codex_reasoning_efforts(self):
        configured = (self.codex_reasoning_effort or "xhigh").strip().lower()
        efforts = []
        for effort in (configured, "xhigh", "high"):
            if effort and effort not in efforts:
                efforts.append(effort)
        return efforts

    def _try_codex_fallback_models(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
        approval_callback=None,
    ):
        fallback_models = ["gpt-5.4-mini", "gpt-5.3-codex-spark"]
        requested = self.codex_model_name.strip()
        for model in fallback_models:
            if requested and requested == model:
                continue
            response = self._generate_codex_app_server_solution(
                executable,
                prompt,
                image_path=image_path,
                code_context=code_context,
                model_override=model,
                stream_callback=stream_callback,
                reasoning_effort=reasoning_effort,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
            if response and not self._is_capacity_message(response) and not self._is_codex_error_message(response):
                return f"[Codex alternativo: {model}]\n\n{response}"
        return None

    def _is_capacity_message(self, text):
        lower = (text or "").lower()
        return "alta demanda" in lower or self._is_codex_capacity_error(lower)

    def _is_codex_progress_timeout(self, text):
        lower = (text or "").lower()
        return "sem enviar progresso" in lower or "ficou sem enviar progresso" in lower

    def _positive_int_env(self, name, default, minimum=1, maximum=None):
        try:
            value = int(os.getenv(name, "").strip())
        except (TypeError, ValueError):
            value = int(default)
        value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    def _is_codex_error_message(self, text):
        lower = (text or "").lower()
        error_markers = [
            "codex app-server retornou erro",
            "codex retornou erro",
            "not available",
            "not found",
            "unknown model",
            "invalid model",
            "nao esta logado",
        ]
        return any(marker in lower for marker in error_markers)

    def _is_reasoning_effort_error(self, text):
        lower = (text or "").lower()
        return (
            "model_reasoning_effort" in lower
            and any(marker in lower for marker in ("invalid", "unknown", "unexpected", "supported", "value"))
        )

    def _generate_codex_app_server_solution(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        model_override=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
        approval_callback=None,
    ):
        workspace = Path(workspace_path).resolve() if workspace_path else Path.cwd()
        selected_model = model_override or self.codex_model_name or "gpt-5.5"
        selected_effort = (reasoning_effort or self.codex_reasoning_effort or "xhigh").strip().lower()
        approval_policy = (
            os.getenv("MEROTEC_CODEX_APP_SERVER_APPROVAL_POLICY", "on-request").strip()
            or "on-request"
        )
        if approval_policy not in {"on-request", "on-failure", "never", "untrusted"}:
            approval_policy = "on-request"
        prompt_text = (
            "Voce esta respondendo dentro da Merotec IA IDE via Codex app-server.\n"
            f"Use raciocinio altissimo nesta tarefa: effort={selected_effort}.\n"
            "Use o workspace atual como projeto ativo.\n"
            "Responda com resultado final ou acao real. Nao escreva promessas como 'vou fazer' antes de executar.\n"
            "Se for usar uma tag da IDE, envie a tag diretamente, sem texto narrando intencao antes dela.\n"
            "Use tags da IDE ou ferramentas diretas do app-server; escolha o caminho real mais confiavel para concluir a tarefa.\n"
            "Use [READ], [WRITE], [REPLACE], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], [FIX_MOJIBAKE], tags EXECUTE/EXECUTE_ADMIN ja preenchidas, [OPEN_URL], [BROWSER_INSPECT], [BROWSER_CLICK], [BROWSER_TYPE], [BROWSER_SCROLL], [SCREENSHOT], [HUMAN_TEST] e [UNDO].\n"
            "Se as tags da IDE estiverem limitando a conclusao da tarefa e o app-server disponibilizar ferramentas diretas, aja diretamente no workspace como Codex, mantendo as mudancas dentro da pasta do projeto e relatando o que fez.\n"
            "Quando a tarefa for correcao/alteracao, use [READ], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], [FIX_MOJIBAKE], [REPLACE], [WRITE] ou ferramentas diretas equivalentes. "
            "Quando a solucao depender de documentacao externa, informacao atual ou erro desconhecido, use [WEB_SEARCH: consulta objetiva] para a IDE buscar na internet.\n"
            "Use uma tag EXECUTE com comando real para validar depois da correcao ou quando o pedido for apenas rodar/iniciar; use EXECUTE_ADMIN com comando real somente quando precisar UAC/administrador no Windows; use [HUMAN_TEST: auto] quando precisar abrir, capturar print e avaliar a tela como usuario.\n"
            "Nunca use reticencias, 'comando', 'comando real', texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real.\n"
            "Nunca copie literalmente 'comando concreto' nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto.\n"
            "Nunca chame terminal, ferramenta de shell ou app-server com comando `...`, `comando`, `como administrador`, `--admin` ou outro placeholder; se nao houver comando real, entregue uma conclusao final.\n"
            "Nunca diga que corrigiu, aplicou, alterou, rodou, testou ou validou sem uma acao real: "
            "[REPLACE]/[WRITE] para mudar arquivo, EXECUTE com comando real para rodar, EXECUTE_ADMIN com comando real para pedir permissao de administrador, [HUMAN_TEST] quando precisar testar visualmente como usuario, [OPEN_URL]/[SCREENSHOT] quando a tela ja estiver aberta.\n\n"
            f"{self._message_payload(prompt, code_context)}"
        )

        try:
            process = subprocess.Popen(
                [
                    executable,
                    "app-server",
                    "-c",
                    'model_provider="openai"',
                    "-c",
                    f'model="{selected_model}"',
                    "-c",
                    f'model_reasoning_effort="{selected_effort}"',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(workspace),
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.active_process = process
        except Exception:
            return None

        messages = queue.Queue()

        def reader():
            try:
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.put(json.loads(line))
                    except json.JSONDecodeError:
                        messages.put({"method": "rawOutput", "params": {"text": line}})
            finally:
                messages.put({"method": "processClosed", "params": {}})

        threading.Thread(target=reader, daemon=True).start()

        next_id = 0

        def send(method, params=None, request_id=None):
            payload = {"method": method}
            if params is not None:
                payload["params"] = params
            if request_id is not None:
                payload["id"] = request_id
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def respond(request_id, result):
            payload = {"id": request_id, "result": result}
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def ask_approval(method, params):
            if approval_callback is None:
                return True
            try:
                return bool(approval_callback(method, params or {}, str(workspace)))
            except Exception:
                return False

        def handle_server_request(message):
            request_id = message.get("id")
            method = message.get("method", "")
            params = message.get("params") or {}
            if request_id is None or not method:
                return False
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                approved = False if self._app_server_message_has_placeholder_command(method, params) else ask_approval(method, params)
                decision = "acceptForSession" if approved else "reject"
                respond(request_id, {"decision": decision})
                return True
            if method in {"execCommandApproval", "applyPatchApproval"}:
                approved = False if self._app_server_message_has_placeholder_command(method, params) else ask_approval(method, params)
                decision = "approved_for_session" if approved else "denied"
                respond(request_id, {"decision": decision})
                return True
            if method == "item/permissions/requestApproval":
                if not ask_approval(method, params):
                    respond(
                        request_id,
                        {
                            "permissions": {
                                "fileSystem": {"entries": []},
                                "network": {"enabled": False},
                            },
                            "scope": "turn",
                            "strictAutoReview": True,
                        },
                    )
                    return True
                respond(
                    request_id,
                    {
                        "permissions": {
                            "fileSystem": {
                                "entries": [
                                    {
                                        "access": "write",
                                        "path": {"type": "path", "path": str(workspace)},
                                    }
                                ]
                            },
                            "network": {"enabled": True},
                        },
                        "scope": "turn",
                        "strictAutoReview": False,
                    },
                )
                return True
            return False

        def wait_for_response(request_id, timeout=20):
            deadline = time.time() + timeout
            buffered_notifications = []
            while time.time() < deadline:
                try:
                    message = messages.get(timeout=0.5)
                except queue.Empty:
                    continue
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message", str(message["error"])))
                    return message.get("result") or {}
                if handle_server_request(message):
                    continue
                buffered_notifications.append(message)
            raise TimeoutError("Codex app-server nao respondeu a tempo.")

        try:
            send(
                "initialize",
                {
                    "clientInfo": {
                        "name": "merotec_ide",
                        "title": "Merotec IA IDE",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                next_id,
            )
            wait_for_response(next_id, timeout=20)
            next_id += 1
            send("initialized", {})
            try:
                send("account/rateLimits/read", {}, next_id)
                rate_limit_result = wait_for_response(next_id, timeout=8)
                self._remember_rate_limits(rate_limit_result)
                next_id += 1
            except Exception:
                next_id += 1

            thread_params = {
                "cwd": str(workspace),
                "developerInstructions": self.system_instruction,
                "sandbox": "workspace-write",
                "approvalsReviewer": "user",
                "personality": "friendly",
                "threadSource": "user",
                "ephemeral": True,
                "modelReasoningEffort": selected_effort,
                "approvalPolicy": approval_policy,
            }
            if selected_model:
                thread_params["model"] = selected_model

            send("thread/start", thread_params, next_id)
            thread_result = wait_for_response(next_id, timeout=30)
            next_id += 1
            thread_id = (thread_result.get("thread") or {}).get("id")
            if not thread_id:
                return None

            input_items = [{"type": "text", "text": prompt_text}]
            if image_path and os.path.exists(image_path):
                input_items.append({"type": "localImage", "path": str(Path(image_path).resolve())})

            turn_params = {
                "threadId": thread_id,
                "input": input_items,
                "cwd": str(workspace),
                "approvalPolicy": approval_policy,
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": True,
                    "writableRoots": [str(workspace)],
                },
                "summary": "concise",
                "personality": "friendly",
                "modelReasoningEffort": selected_effort,
            }
            if selected_model:
                turn_params["model"] = selected_model

            send("turn/start", turn_params, next_id)
            turn_response_id = next_id
            next_id += 1

            chunks = []
            final_from_items = ""
            last_error = ""
            completed = False
            task_timeout = self._positive_int_env(
                "MEROTEC_CODEX_TASK_TIMEOUT_SECONDS",
                3600,
                minimum=300,
                maximum=14400,
            )
            idle_timeout = self._positive_int_env(
                "MEROTEC_CODEX_APP_SERVER_IDLE_TIMEOUT_SECONDS",
                900,
                minimum=120,
                maximum=7200,
            )
            deadline = time.time() + task_timeout
            last_activity = time.time()

            while time.time() < deadline:
                if self.cancel_requested:
                    last_error = "Tarefa cancelada pelo usuario."
                    break
                try:
                    message = messages.get(timeout=1)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    if time.time() - last_activity > idle_timeout:
                        last_error = (
                            f"Codex ficou sem enviar progresso por mais de {idle_timeout} segundos. "
                            "A IDE vai tentar continuar pelo executor direto do Codex."
                        )
                        break
                    continue
                last_activity = time.time()

                if message.get("id") == turn_response_id and "error" in message:
                    last_error = message["error"].get("message", str(message["error"]))
                    break

                method = message.get("method", "")
                params = message.get("params") or {}
                self._remember_quota_message_handler(method, params)

                if handle_server_request(message):
                    continue

                if self._app_server_message_has_placeholder_command(method, params):
                    last_error = (
                        "Codex app-server tentou executar um comando placeholder. "
                        "A IDE bloqueou a rodada para nao repetir reticencias no terminal."
                    )
                    break

                if self._app_server_output_is_placeholder_error(method, params):
                    last_error = (
                        "O app-server recebeu um placeholder como comando. "
                        "A IDE interrompeu a repeticao; use um comando real ou conclua em texto."
                    )
                    break

                if method.endswith("agentMessage/delta") or method == "agentMessageDelta":
                    delta = params.get("delta", "")
                    chunks.append(delta)
                    if delta and stream_callback:
                        stream_callback(delta)
                    continue

                progress = self._extract_app_server_progress(method, params)
                if progress and stream_callback:
                    stream_callback(progress)
                    continue

                if method in {"error", "warning", "rawOutput"}:
                    last_error = params.get("message") or params.get("text") or last_error
                    continue

                if method.endswith("turn/completed") or method == "turnCompleted":
                    completed = True
                    turn = params.get("turn") or {}
                    if turn.get("status") == "failed":
                        error = turn.get("error") or {}
                        self._remember_quota_error({"error": error})
                        last_error = error.get("message") or str(error)
                    else:
                        final_from_items = self._extract_app_server_final_message(turn)
                    break

                if method == "processClosed":
                    break

            final_message = "".join(chunks).strip() or final_from_items.strip()
            if last_error and self._is_codex_progress_timeout(last_error) and not completed:
                return self._format_codex_app_server_error(last_error)
            if final_message:
                return final_message
            if last_error:
                return self._format_codex_app_server_error(last_error)
            if completed:
                return "Codex terminou a tarefa sem mensagem final."
            return None
        except Exception:
            return None
        finally:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            if self.active_process is process:
                self.active_process = None

    def _extract_app_server_progress(self, method, params):
        lower_method = (method or "").lower()
        progress_methods = ("command", "exec", "patch", "filechange")
        if not any(marker in lower_method for marker in progress_methods):
            return ""

        text = (
            params.get("delta")
            or params.get("text")
            or params.get("output")
            or params.get("message")
            or ""
        )
        if text:
            return text

        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if command and any(marker in lower_method for marker in ("start", "begin", "created")):
            return f"\nExecutando: {command}\n"

        status = params.get("status")
        if status:
            return f"\nStatus: {status}\n"

        return ""

    def _app_server_message_has_placeholder_command(self, method, params):
        lower_method = (method or "").lower()
        if not any(marker in lower_method for marker in ("command", "exec", "shell")):
            return False
        command = self._extract_app_server_command_text(params)
        return bool(command and self._is_placeholder_command_text(command))

    def _app_server_output_is_placeholder_error(self, method, params):
        lower_method = (method or "").lower()
        if not any(marker in lower_method for marker in ("raw", "output", "error", "warning", "command", "exec")):
            return False
        text = (
            params.get("output")
            or params.get("text")
            or params.get("message")
            or params.get("delta")
            or ""
        )
        normalized = str(text or "").lower()
        if not normalized:
            return False
        return bool(
            re.search(r"'\s*(?:\.{3}|\u2026|`+)\s*'.*(?:reconhecido|recognized)", normalized)
            or re.search(r"'\s*(?:comando|command)(?:\s+(?:real|concreto|concrete|here))?\s*'.*(?:reconhecido|recognized)", normalized)
        )

    def _extract_app_server_command_text(self, value, depth=0):
        if depth > 5:
            return ""
        if isinstance(value, dict):
            executable = self._first_present_app_server_value(
                value,
                ("program", "executable", "filePath", "file_path", "binary", "shell"),
            )
            arguments = self._first_present_app_server_value(
                value,
                ("argv", "args", "arguments", "argList", "argumentList", "argument_list"),
            )
            if executable is not None and arguments is not None:
                parts = [
                    self._compact_app_server_command_value(executable),
                    self._compact_app_server_command_value(arguments),
                ]
                return " ".join(part for part in parts if part).strip()

            preferred_keys = (
                "command",
                "commandLine",
                "command_line",
                "cmdLine",
                "cmdline",
                "cmd",
                "shellCommand",
                "shell_command",
                "script",
                "argv",
                "args",
                "arguments",
                "argList",
                "argumentList",
                "argument_list",
            )
            for key in preferred_keys:
                if key not in value:
                    continue
                nested = value[key]
                if isinstance(nested, dict):
                    found = self._extract_app_server_command_text(nested, depth + 1)
                    if found:
                        return found
                return self._compact_app_server_command_value(nested)
            for nested in value.values():
                found = self._extract_app_server_command_text(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            return self._compact_app_server_command_value(value)
        return ""

    def _first_present_app_server_value(self, values, keys):
        for key in keys:
            if key in values and values[key] not in (None, "", [], False):
                return values[key]
        return None

    def _compact_app_server_command_value(self, value):
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def _is_placeholder_command_text(self, command):
        raw = str(command or "").strip().strip("`\"'").strip()
        lowered = raw.lower().strip()
        if not lowered:
            return True

        placeholders = {
            "...",
            "\u2026",
            "?",
            "`",
            "``",
            "```",
            "e termine com",
            "termine com",
            "comece com",
            "comece com e termine com",
            "start with",
            "start with and end with",
            "end with",
            "comando",
            "comando real",
            "comando concreto",
            "comando completo",
            "comando aqui",
            "comando concreto aqui",
            "command",
            "command here",
            "concrete command",
            "complete command",
            "your command",
            "your command here",
            "como administrador",
            "run as administrator",
            "--admin",
            "/admin",
        }
        lowered_without_ticks = lowered.replace("`", "").strip()
        core = re.sub(r"[\s.<>\[\]{}()_`\-./\\:;|&=\u2026?]+", "", lowered_without_ticks)
        placeholder_cores = {
            re.sub(r"[\s.<>\[\]{}()_`\-./\\:;|&=\u2026?]+", "", item)
            for item in placeholders
        }
        if lowered in placeholders or lowered_without_ticks in placeholders or core in placeholder_cores:
            return True

        shell_payload_patterns = (
            r"^(?:/[ck]\s+)(.+)$",
            r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)(.+)$",
            r"^(?:(?:powershell|pwsh)(?:\.exe)?\b.*?(?:-command|-c)\s+)(.+)$",
        )
        for pattern in shell_payload_patterns:
            match = re.match(pattern, lowered)
            if not match:
                continue
            payload = match.group(1).strip().strip("`\"'").strip()
            if payload and payload != lowered and self._is_placeholder_command_text(payload):
                return True

        patterns = (
            r"^(?:o\s+|um\s+)?comando(?:\s+(?:real|completo|concreto|aqui|do\s+projeto|preenchido))+$",
            r"^(?:seu|your)\s+comando(?:\s+aqui)?$",
            r"^<[^>]*(?:comando|command)[^>]*>$",
            r"^(?:e\s+)?termine\s+com$",
            r"^(?:comece|comeca)\s+com(?:\s+e\s+termine\s+com)?$",
            r"^start\s+with(?:\s+and\s+end\s+with)?$",
            r"^end\s+with$",
            r"^(?:cmd(?:\.exe)?\s+)?/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
            r"^cmd(?:\.exe)?\s+/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
            r"^(?:powershell|pwsh)(?:\.exe)?\s+.*(?:-command|-c)\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
        )
        if any(
            re.fullmatch(pattern, candidate)
            for candidate in (lowered, lowered_without_ticks)
            for pattern in patterns
        ):
            return True

        return False

    def _extract_app_server_final_message(self, turn):
        messages = []
        for item in turn.get("items", []):
            if item.get("type") == "agentMessage":
                text = item.get("text")
                if text:
                    messages.append(text)
        return "\n\n".join(messages)

    def _format_codex_app_server_error(self, message):
        lower = (message or "").lower()
        if "cancelada pelo usuario" in lower:
            return "Tarefa cancelada."
        if "sem enviar progresso" in lower:
            return message
        if self._is_codex_capacity_error(lower):
            return (
                "O Codex esta com alta demanda no momento e nao conseguiu responder agora. "
                "A IDE esta funcionando; esse erro vem do servico Codex. "
                "Tente novamente em alguns minutos."
            )
        if "not logged in" in lower or "unauthorized" in lower:
            return (
                "O Codex que a IDE chama ainda nao esta logado no Windows. "
                "Clique em `Entrar Codex`, conclua o login e tente novamente."
            )
        return f"Codex app-server retornou erro:\n\n{message}"

    def _generate_codex_exec_solution(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
    ):
        workspace = Path(workspace_path).resolve() if workspace_path else Path.cwd()
        selected_effort = (reasoning_effort or self.codex_reasoning_effort or "xhigh").strip().lower()
        prompt_text = (
            f"{self.system_instruction}\n\n"
            "Voce esta sendo chamado diretamente pela Merotec IA IDE via Codex CLI.\n"
            f"Use raciocinio alto nesta tarefa: effort={selected_effort}.\n"
            "Pode editar arquivos no workspace quando a tarefa pedir implementacao.\n"
            "Nao responda com promessa. Execute a tarefa e devolva apenas resultado final ou a acao real necessaria.\n"
            "Ao terminar, responda em portugues com um resumo curto do que fez.\n\n"
            f"{self._message_payload(prompt, code_context)}"
        )

        output_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as file:
                output_path = Path(file.name)

            command = [
                executable,
                "exec",
                "-c",
                'model_provider="openai"',
                "-c",
                f'model="{self.codex_model_name or "gpt-5.5"}"',
                "-c",
                f'model_reasoning_effort="{selected_effort}"',
                "-c",
                "sandbox_workspace_write.network_access=true",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "-o",
                str(output_path),
            ]
            if self.codex_model_name:
                command.extend(["--model", self.codex_model_name])
            if image_path and os.path.exists(image_path):
                command.extend(["--image", image_path])
            command.append("-")

            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(workspace),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.active_process = process
            process.stdin.write(prompt_text)
            process.stdin.close()

            if stream_callback:
                stream_callback("Codex iniciou a tarefa...\n")

            output_chunks = []
            buffer = []
            started_at = time.time()
            while True:
                if self.cancel_requested:
                    self.cancel_generation()
                    return "Tarefa cancelada."
                chunk = process.stdout.read(1)
                if not chunk:
                    break
                output_chunks.append(chunk)
                buffer.append(chunk)
                if stream_callback and (chunk in {"\n", "\r"} or len(buffer) >= 160):
                    stream_callback("".join(buffer))
                    buffer.clear()
                if time.time() - started_at > 1800:
                    self.cancel_generation()
                    return "Codex demorou mais de 30 minutos nessa tarefa. Divida em passos menores e tente novamente."

            if stream_callback and buffer:
                stream_callback("".join(buffer))
            process.wait(timeout=5)
            output = "".join(output_chunks)
            final_message = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""

            if process.returncode != 0:
                tail = self._compact_command_error(output)
                lower_tail = tail.lower()
                if "401 unauthorized" in lower_tail or "missing bearer" in lower_tail or "not logged in" in lower_tail:
                    return (
                        "Codex esta instalado, mas a sessao nao esta autenticada para executar tarefas. "
                        "Clique em `Entrar Codex`, conclua o login e tente novamente."
                    )
                if self._is_codex_capacity_error(lower_tail):
                    return (
                        "O Codex esta com alta demanda no momento e nao conseguiu responder agora. "
                        "A IDE esta funcionando; esse erro vem do servico Codex. "
                        "Tente novamente em alguns minutos."
                    )
                return f"Codex retornou erro {process.returncode}.\n\n{tail}"

            return final_message or (output or "").strip() or "Codex terminou sem mensagem final."
        except subprocess.TimeoutExpired:
            self.cancel_generation()
            return "Codex demorou mais de 30 minutos nessa tarefa. Divida em passos menores e tente novamente."
        except Exception as exc:
            return f"Erro ao executar Codex local: {exc}"
        finally:
            if output_path:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if self.active_process is locals().get("process"):
                self.active_process = None

    def _compact_command_error(self, output):
        lines = (output or "").strip().splitlines()
        interesting = []
        for line in lines:
            clean = line.strip()
            lower = clean.lower()
            if not clean:
                continue
            if (
                "error" in lower
                or "unauthorized" in lower
                or "not logged in" in lower
                or "missing bearer" in lower
                or "usage:" in lower
                or "unexpected argument" in lower
                or clean.startswith("ERROR:")
            ):
                interesting.append(clean)

        selected = interesting[-12:] if interesting else lines[-20:]
        text = "\n".join(selected).strip()
        return text[-2400:] if len(text) > 2400 else text

    def _is_codex_capacity_error(self, text):
        capacity_markers = [
            "high demand",
            "temporar",
            "reconnecting",
            "service unavailable",
            "rate limit",
            "overloaded",
        ]
        return any(marker in text for marker in capacity_markers)

    def _generate_openai_solution(self, prompt, image_path=None, code_context=None):
        if not self.openai_api_key:
            return (
                "Para usar GPT na IDE, abra Configurar IA e cole uma OPENAI_API_KEY. "
                "Login no ChatGPT/Codex nao pode ser usado diretamente como chave de API."
            )

        try:
            # Detecta se a chave pertence ao OpenRouter
            base_url = str(getattr(self, "openai_base_url", "") or "https://api.openai.com/v1").rstrip("/")
            openrouter_key = self.openai_api_key.startswith("sk-or-")
            if openrouter_key and "api.openai.com" in base_url.lower():
                # Migra configuracoes antigas que guardavam apenas a chave e o modelo.
                base_url = "https://openrouter.ai/api/v1"
                self.openai_base_url = base_url
            is_openrouter = openrouter_key or "openrouter.ai" in base_url.lower()
            
            if is_openrouter:
                endpoint = f"{base_url}/chat/completions"
                current_model = str(self.openai_model_name or self.model_id or "").strip()
                if not current_model:
                    return "Configure o ID exato do modelo OpenRouter em Configurar IA."
                    
                payload = {
                    "model": current_model,
                    "messages": [
                        {"role": "system", "content": self._agent_protocol_system_instruction()},
                        {"role": "user", "content": self._agent_message_payload(prompt, code_context)}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                }
                headers = {
                    "Authorization": f"Bearer {self.openai_api_key.strip()}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/merotec/ai-ide",
                    "X-Title": "Merotec AI IDE",
                }
            else:
                endpoint = f"{base_url}/chat/completions"
                payload = {
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": self._agent_protocol_system_instruction()},
                        {"role": "user", "content": self._agent_message_payload(prompt, code_context)}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                }
                headers = {
                    "Authorization": f"Bearer {self.openai_api_key.strip()}",
                    "Content-Type": "application/json",
                }

            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))

            self._remember_openai_response_model(data)
            if "usage" in data:
                self._remember_openai_usage(data.get("usage"))
                
            return self._extract_openai_text(data)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return self._format_openai_http_error(exc.code, body)
        except Exception as exc:
            return f"Erro no motor OpenAI usando modelo `{self.model_id}`: {exc}"

    def _generate_lm_studio_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        _analysis_continuation=False,
    ):
        if not self.lm_studio_model_name:
            return "LM Studio sem modelo configurado. Abra Configurar IA e selecione lm_studio."

        direct_action = self._lm_studio_direct_protocol_action(prompt)
        if direct_action:
            return direct_action

        endpoint = f"{self.normalize_lm_studio_base_url(self.lm_studio_base_url)}/chat/completions"
        user_content = self._lm_studio_message_payload(prompt, code_context)
        if image_path and os.path.exists(image_path):
            path = Path(image_path)
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            user_content = [
                {"type": "text", "text": user_content},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                },
            ]
        payload = {
            "model": self.lm_studio_model_name,
            "messages": [
                {"role": "system", "content": self._lm_studio_system_instruction()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": self.lm_studio_max_tokens,
            "reasoning_effort": "none",
            "stream": True,
        }
        headers = {"Content-Type": "application/json"}
        if self.lm_studio_api_key:
            headers["Authorization"] = f"Bearer {self.lm_studio_api_key}"

        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            chunks = []
            reasoning_chunks = []
            plain_lines = []
            finish_reason = ""
            with urllib.request.urlopen(request, timeout=self.lm_studio_timeout_seconds) as response:
                for raw_line in response:
                    if self.cancel_requested:
                        return "Geracao local cancelada."
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        plain_lines.append(line)
                        continue
                    event_text = line[5:].strip()
                    if event_text == "[DONE]":
                        break
                    try:
                        event = json.loads(event_text)
                    except json.JSONDecodeError:
                        continue
                    self._remember_lm_studio_response_model(event)
                    self._remember_openai_usage(event.get("usage"))
                    choices = event.get("choices") or []
                    choice = choices[0] if choices else {}
                    finish_reason = str(choice.get("finish_reason") or finish_reason)
                    delta = choice.get("delta")
                    content = delta.get("content") if isinstance(delta, dict) else ""
                    reasoning = delta.get("reasoning_content") if isinstance(delta, dict) else ""
                    if reasoning:
                        reasoning_chunks.append(str(reasoning))
                    if content:
                        content = str(content)
                        chunks.append(content)
                        if stream_callback:
                            stream_callback(content)

            answer = "".join(chunks).strip()
            if not answer and reasoning_chunks:
                answer = self._extract_lm_studio_action_from_reasoning(
                    "".join(reasoning_chunks)
                )
            if not answer and plain_lines:
                data = json.loads("\n".join(plain_lines))
                self._remember_lm_studio_response_model(data)
                self._remember_openai_usage(data.get("usage"))
                answer = self._extract_openai_text(data)
            is_analysis = self._lm_studio_is_analysis_prompt(prompt)
            if answer and is_analysis:
                answer = self._clean_lm_studio_analysis_answer(answer)
            if answer and finish_reason == "length" and is_analysis and not _analysis_continuation:
                if stream_callback:
                    stream_callback("\n\n[Continuando analise local...]\n\n")
                continuation_context = (
                    f"CONTEXTO ORIGINAL:\n{code_context or ''}\n\n"
                    f"ANALISE PARCIAL JA ESCRITA:\n{answer}"
                )
                continuation = self._generate_lm_studio_solution(
                    "Continue a analise detalhada do projeto exatamente do ponto interrompido. "
                    "Nao repita introducao nem secoes concluidas; finalize riscos, melhorias e conclusao.",
                    code_context=continuation_context,
                    stream_callback=stream_callback,
                    _analysis_continuation=True,
                )
                if continuation:
                    continuation = self._clean_lm_studio_analysis_answer(continuation)
                    return f"{answer}\n\n{continuation}".strip()
            if answer:
                return answer
            if finish_reason == "length":
                return (
                    "O LM Studio consumiu o limite de tokens antes da resposta final. "
                    "A IDE ja solicitou raciocinio reduzido; tente novamente."
                )
            return "O LM Studio respondeu sem texto utilizavel."
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("error", {}).get("message", body)
            except json.JSONDecodeError:
                message = body
            return f"Erro no LM Studio: HTTP {exc.code} - {message}"
        except urllib.error.URLError as exc:
            return (
                f"Nao consegui conectar ao LM Studio em {self.lm_studio_base_url}. "
                f"Confirme se o servidor local esta ligado. Detalhe: {exc.reason}"
            )
        except TimeoutError:
            return (
                f"O LM Studio demorou mais de {self.lm_studio_timeout_seconds}s para iniciar a resposta. "
                "Reduza o contexto/modelo ou aumente o timeout nas configuracoes."
            )
        except Exception as exc:
            return f"Erro no LM Studio usando modelo `{self.lm_studio_model_name}`: {exc}"

    @staticmethod
    def _extract_lm_studio_action_from_reasoning(reasoning_text):
        text = str(reasoning_text or "")
        candidates = []
        block_pattern = re.compile(
            r"\[(WRITE|REPLACE)\s*:[^\]\r\n]+\].*?\[/\1\]",
            re.IGNORECASE | re.DOTALL,
        )
        simple_pattern = re.compile(
            r"\[(?:READ|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|"
            r"EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|SCREENSHOT|HUMAN_TEST)\s*:[^\]\r\n]+\]",
            re.IGNORECASE,
        )
        for pattern in (block_pattern, simple_pattern):
            candidates.extend((match.start(), match.group(0).strip()) for match in pattern.finditer(text))
        return max(candidates, default=(-1, ""), key=lambda item: item[0])[1]

    @staticmethod
    def _lm_studio_direct_protocol_action(prompt):
        normalized = str(prompt or "").lower()
        visual_test_markers = (
            "teste visual",
            "testes visuais",
            "testar visualmente",
            "teste de interface",
            "testar a interface",
        )
        if any(marker in normalized for marker in visual_test_markers):
            return "[HUMAN_TEST: auto]"
        return ""

    def _format_openai_http_error(self, status_code, body):
        try:
            payload = json.loads(body)
            message = payload.get("error", {}).get("message", body)
            code = payload.get("error", {}).get("code", "")
        except json.JSONDecodeError:
            message = body
            code = ""

        if status_code == 401 or code == "invalid_api_key":
            return (
                f"A chave inserida foi rejeitada pela API (HTTP {status_code}). Verifique se "
                "copiou o token completo do OpenRouter corretamente."
            )

        if status_code == 429 and code == "insufficient_quota":
            self.latest_quota_problem = "sem cota disponivel"
            self.latest_quota_updated_at = time.time()
            return (
                "Sua chave foi aceita, mas a conta/projeto esta sem cota disponivel. "
                "Verifique seu saldo na plataforma."
            )

        if status_code in {400, 404} and "model" in message.lower():
            return (
                f"O modelo `{self.model_id}` nao foi localizado. Certifique-se de que digitou o ID "
                "exato do OpenRouter (Exemplo: deepseek/deepseek-chat:free)."
            )

        return f"Erro na requisição: HTTP {status_code} - {message}"

    def _extract_openai_text(self, data):
        if data.get("output_text"):
            return data["output_text"]

        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            if isinstance(choice, dict) and "message" in choice:
                message = choice["message"] or {}
                content = message.get("content")
                if content:
                    if isinstance(content, list):
                        blocks = [
                            str(item.get("text") or "")
                            for item in content
                            if isinstance(item, dict) and item.get("text")
                        ]
                        if blocks:
                            return "\n".join(blocks)
                    return str(content)
                recovered = self._extract_lm_studio_action_from_reasoning(
                    message.get("reasoning_content") or message.get("reasoning") or ""
                )
                if recovered:
                    return recovered

        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if text:
                    chunks.append(text)
        return "\n".join(chunks)

    def _generate_google_solution(self, prompt, image_path=None, code_context=None):
        if not GoogleClient:
            return "O pacote Google GenAI nao esta instalado nesse Python. Use Codex ou OpenAI, ou instale google-genai."
        if not self.chat_session:
            return (
                "Configure GOOGLE_API_KEY ou altere AI_PROVIDER=openai com OPENAI_API_KEY. "
                "Depois reinicie a IDE."
            )

        try:
            parts = [self._agent_message_payload(prompt, code_context)]

            if image_path and os.path.exists(image_path):
                parts.append(PIL.Image.open(image_path))

            response = self.chat_session.send_message(parts)
            return response.text or ""
        except Exception as exc:
            if "429" in str(exc):
                self.reset_session()
                return "A sessao ficou pesada ou limitada pela API. Reiniciei o chat; tente novamente."
            return f"Erro no motor GenAI: {exc}"

    def reset_session(self):
        if self.provider in {"web_chat", "codex", "openai", "lm_studio", "local_gguf"} or not self.client:
            return
        self.chat_session = self.client.chats.create(
            model=self.model_id,
            config=self.generation_config,
        )

    def cancel_generation(self):
        """Cancela a tarefa sem destruir a conversa WebView2 da IDE.

        A ponte ``InternalBrowserWebChatBridge`` pertence à janela principal.
        Fechá-la durante um cancelamento fazia a próxima tarefa criar uma segunda
        ponte/processo e perder a conversa vinculada ao projeto. Pontes de
        fallback independentes continuam sendo encerradas normalmente.
        """
        self.cancel_requested = True
        bridge = getattr(self, "web_chat_bridge", None)
        if bridge is not None and self.provider == "web_chat":
            if not getattr(bridge, "managed_by_ide", False):
                try:
                    bridge.close()
                except Exception:
                    pass
                self.web_chat_bridge = None
        process = self.active_process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass


# MEROTEC_CODE_PROTOCOL_V5
_merotec_v5_base_agent_protocol = UniversalEngine._agent_protocol_system_instruction

_merotec_v5_transport_protocol = r"""
PROTOCOLO DE ENTREGA DE CÓDIGO:
- Para código multiline, prefira blocos Markdown com ```linguagem para preservar visualmente todos os espaços.
- A IDE aceita uma alteração sem cerca Markdown quando ela passa na validação da linguagem; não interrompa uma correção simples apenas por falta de cerca.
- Para Python, use quatro espaços por nível e nunca tab. Antes de alterar arquivo existente, leia o arquivo real.
- Se receber erro de sintaxe/indentação, faça uma única correção baseada no trecho real. Não entre em loop e não repita a mesma alteração.
- Para WRITE e REPLACE, preserve a estrutura e imports já existentes. Para PATCH, envie contexto exato.
"""

def _merotec_v5_agent_protocol(self):
    return _merotec_v5_base_agent_protocol(self).rstrip() + "\n\n" + _merotec_v5_transport_protocol

UniversalEngine._agent_protocol_system_instruction = _merotec_v5_agent_protocol


# MEROTEC_WEB_CHAT_CONVERSATION_PIPELINE_V1
# Restaura uma conversa contínua: não recarrega o chat e não reenvia um briefing
# gigante a cada chamada.

_merotec_chat_base_cancel_generation = UniversalEngine.cancel_generation


def _merotec_chat_compact_context(self, code_context, limit=9000):
    text = str(code_context or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    # Seções que são úteis localmente, mas repetidas e pesadas para uma conversa
    # já aberta no navegador.
    text = re.sub(
        r"\n*CONTEXTO DA SUB-REDE LOCAL DO SISTEMA:.*?(?=\n\nContexto adicional:|\n\nANALISE CONSOLIDADA|\Z)",
        "\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"\n*(?:ORDEM PARA A IA:|MAPA PERMANENTE DO PROJETO PARA A IA:|Historico recente que deve ser lembrado:).*",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    # Não reenvia repetidamente blocos de protocolo já incorporados no motor.
    text = re.sub(
        r"\n*MODO CODEX DA IDE:.*?(?=\n\n(?:Alteracoes recentes feitas pela IDE|Conversa recente que deve ser preservada:|BRIEFING INTELIGENTE DA IDE:|Contexto adicional:)|\Z)",
        "\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    if len(text) <= limit:
        return text

    head = max(2600, int(limit * 0.48))
    tail = max(2200, limit - head - 120)
    return (
        text[:head].rstrip()
        + "\n\n[Contexto intermediário omitido; use o arquivo/resultado mais recente abaixo.]\n\n"
        + text[-tail:].lstrip()
    )


def _merotec_chat_instruction(self):
    return (
        "PROTOCOLO ATIVO DA MEROTEC IA IDE — esta instrução substitui orientações anteriores desta conversa que peçam texto livre.\n"
        "A missão de desenvolvimento permanece ativa até existir evidência de mudança, leitura, execução, validação, bloqueio externo explícito ou cancelamento.\n"
        "NÃO responda com resumo, promessa ou afirmação de que corrigiu/testou sem antes emitir uma ação que a IDE possa executar.\n"
        "Em cada resposta, emita EXATAMENTE UMA ação executável: [READ: caminho], [SEARCH_TEXT: padrão | caminho], "
        "[EXECUTE: comando], [HUMAN_TEST: auto], [WRITE: caminho]...[/WRITE], "
        "[REPLACE: caminho] [OLD]...[/OLD] [NEW]...[/NEW] [/REPLACE] ou [PATCH: caminho]...[/PATCH].\n"
        "READ, SEARCH_TEXT, EXECUTE, HUMAN_TEST, WEB_SEARCH e ações de navegador ficam em uma linha. "
        "WRITE, REPLACE e PATCH são blocos multilinha; não resuma, não use reticências e feche cada marcador.\n"
        "Antes de editar arquivo existente, leia o trecho real. Depois de editar, execute uma validação compatível com a linguagem. "
        "Use caminhos relativos ao workspace. Nunca invente leitura, edição, teste ou resultado."
    )

def _merotec_chat_generate_web(
    self,
    prompt,
    image_path=None,
    code_context=None,
    stream_callback=None,
    workspace_path=None,
):
    """Envia a mensagem ao Chat Web e confirma anexos pelo transporte do navegador.

    A confirmação não depende de uma frase especial do modelo. Quando o
    navegador confirma que o print foi anexado/enviado para a conversa, essa é
    a evidência de transporte usada pela IDE. O texto do modelo continua livre
    para devolver a próxima ação da IDE, sem conflito com o protocolo.
    """
    attachments = []
    requested_visual = bool(image_path)
    self.latest_web_chat_delivery = {
        "ok": False,
        "attachments_requested": requested_visual,
        "attachment_count": 0,
        "attachment_error": "",
        "attachment_delivery": "pending" if requested_visual else "none",
        "visual_receipt": "pending" if requested_visual else "not_requested",
        "response_received": False,
        "error": "",
    }
    self.latest_web_chat_artifacts = {}

    try:
        self.cancel_requested = False
        if stream_callback:
            stream_callback("Chat Web: continuando a conversa do projeto.\n")

        bridge = self._web_chat_bridge_for(workspace_path)
        if bridge is None:
            raise RuntimeError("ponte do Chat Web indisponível")
        bridge.managed_by_ide = True

        include_project_context = bool(
            getattr(self, "web_chat_profile", {}).get(
                "web_chat_include_project_context", True
            )
        )
        compacted = _merotec_chat_compact_context(self, code_context) if include_project_context else ""
        message = (
            _merotec_chat_instruction(self)
            + "\n\nTAREFA ATUAL:\n"
            + str(prompt or "").strip()
        )
        if compacted:
            message += "\n\nCONTEXTO OBJETIVO:\n" + compacted

        attachments = self._web_chat_attachment_payload(image_path)
        if requested_visual and not attachments:
            self.latest_web_chat_delivery["attachment_error"] = (
                "A IDE capturou o print, mas não conseguiu prepará-lo para envio "
                "(arquivo ausente, ilegível ou acima do limite local)."
            )
            self.latest_web_chat_delivery["attachment_delivery"] = "local_prepare_failed"
            self.latest_web_chat_delivery["visual_receipt"] = "unavailable"
        elif attachments:
            message += (
                "\n\nEVIDÊNCIA VISUAL ANEXADA:\n"
                "Há um print do teste visual nesta mensagem. Analise a imagem junto com o contexto técnico. "
                "Não responda apenas confirmando o anexo: devolva diretamente a próxima ação da IDE ou uma "
                "conclusão baseada na imagem. Se a imagem realmente não estiver visível, informe isso de forma objetiva."
            )

        result = bridge.chat(
            message,
            workspace_path=workspace_path,
            attachments=attachments,
            timeout=self.web_chat_timeout_seconds,
            stream_callback=stream_callback,
        )
        if not isinstance(result, dict):
            result = {"ok": False, "error": "Resposta inválida do navegador interno."}

        response = str(result.get("response") or "").strip()
        normalized_response = re.sub(r"\s+", " ", response).strip().upper()
        raw_attachment_error = str(result.get("attachment_error") or "").strip()
        prior_attachment_error = str(self.latest_web_chat_delivery.get("attachment_error") or "").strip()
        attachment_error = raw_attachment_error or prior_attachment_error
        attachment_count = int(result.get("attachment_count") or 0)
        attachment_verified = bool(result.get("attachment_verified")) if requested_visual else True
        attachment_in_conversation = bool(result.get("attachment_in_conversation")) if requested_visual else False
        expected_count = max(1, len(attachments)) if requested_visual else 0
        transport_confirmed = bool(
            requested_visual
            and attachment_verified
            and attachment_count >= expected_count
            and not attachment_error
        )

        if requested_visual:
            if "[VISUAL_EVIDENCE_MISSING]" in normalized_response:
                receipt = "missing"
            elif "[VISUAL_EVIDENCE_RECEIVED]" in normalized_response:
                receipt = "received"
            elif attachment_in_conversation:
                receipt = "conversation_confirmed"
            elif transport_confirmed:
                receipt = "transport_confirmed"
            else:
                receipt = "unconfirmed"
        else:
            receipt = "not_requested"

        if not requested_visual:
            delivery_state = "none"
        elif attachment_error:
            delivery_state = "unavailable"
        elif not attachment_verified:
            delivery_state = "unavailable"
        elif attachment_count < expected_count:
            delivery_state = "not_selected"
        elif attachment_in_conversation:
            delivery_state = "conversation_confirmed"
        elif receipt == "received":
            delivery_state = "provider_acknowledged"
        else:
            delivery_state = "transport_confirmed"

        self.latest_web_chat_artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
        self.latest_web_chat_delivery = {
            "ok": bool(result.get("ok")),
            "attachments_requested": requested_visual,
            "attachment_count": attachment_count,
            "attachment_error": attachment_error,
            "attachment_verified": attachment_verified,
            "attachment_in_conversation": attachment_in_conversation,
            "attachment_delivery": delivery_state,
            "visual_receipt": receipt,
            "response_received": bool(response),
            "error": str(result.get("error") or ""),
        }

        if not result.get("ok"):
            detail = str(result.get("error") or "erro desconhecido")
            if attachment_error:
                detail += " | anexo: " + attachment_error
            return "Chat Web não concluiu a resposta: " + detail

        if not response:
            if self.latest_web_chat_artifacts.get("images") or self.latest_web_chat_artifacts.get("audio"):
                return "O Chat Web gerou mídia, mas não retornou texto. Abra a conversa no navegador para revisar o resultado."
            return "O Chat Web terminou sem texto de resposta."

        if requested_visual and stream_callback:
            if receipt == "missing":
                stream_callback("Chat Web: o modelo informou que não recebeu o print; a IDE seguirá sem alegar validação visual.\n")
            elif attachment_in_conversation:
                stream_callback("Chat Web: print confirmado na conversa pelo navegador; seguindo com a análise.\n")
            elif transport_confirmed:
                stream_callback("Chat Web: print confirmado pelo transporte do navegador; seguindo com a análise.\n")
            elif attachment_error:
                stream_callback("Chat Web: o anexo não foi aceito automaticamente; a IDE não tratará o teste visual como validado.\n")
            else:
                stream_callback("Chat Web: resposta recebida, mas o navegador não confirmou o anexo.\n")
        return response
    except Exception as exc:
        self.latest_web_chat_delivery = {
            "ok": False,
            "attachments_requested": requested_visual,
            "attachment_count": 0,
            "attachment_error": str(exc) if requested_visual else "",
            "attachment_delivery": "failed",
            "visual_receipt": "unavailable" if requested_visual else "not_requested",
            "response_received": False,
            "error": str(exc),
        }
        return f"Erro no Chat Web: {exc}"

def _merotec_chat_cancel_generation(self):
    # Nunca desanexa a ponte interna ao cancelar. O próximo pedido deve reutilizar
    # a mesma conversa e o mesmo WebView2.
    self.cancel_requested = True
    bridge = getattr(self, "web_chat_bridge", None)
    if bridge is not None and self.provider == "web_chat":
        try:
            bridge.managed_by_ide = True
        except Exception:
            pass
    process = getattr(self, "active_process", None)
    if process and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


UniversalEngine._compact_web_chat_context = _merotec_chat_compact_context
UniversalEngine._web_chat_conversation_instruction = _merotec_chat_instruction
UniversalEngine._generate_web_chat_solution = _merotec_chat_generate_web
UniversalEngine.cancel_generation = _merotec_chat_cancel_generation


# MEROTEC_CONFIGURED_PROVIDER_LOCK_V1
# A missão deve usar exclusivamente o provedor selecionado em Configurar IA.
# Fallbacks automáticos só devem existir quando o usuário os habilitar
# explicitamente em uma versão futura da interface.

def _merotec_locked_generate_solution(
    self,
    prompt,
    image_path=None,
    code_context=None,
    stream_callback=None,
    workspace_path=None,
    approval_callback=None,
):
    self.cancel_requested = False
    response = self._generate_solution_with_provider(
        self.provider,
        prompt,
        image_path=image_path,
        code_context=code_context,
        stream_callback=stream_callback,
        workspace_path=workspace_path,
        approval_callback=approval_callback,
    )
    if self.provider == "local_gguf" and not getattr(self, "local_gguf_allow_external_fallback", False):
        return response
    if self.provider == "lm_studio" and not getattr(self, "lm_studio_allow_external_fallback", False):
        return response
    if not self.should_try_external_ai_fallback(response):
        return response
    fallback_response = self.try_external_ai_fallback(
        prompt,
        image_path=image_path,
        code_context=code_context,
        stream_callback=stream_callback,
        workspace_path=workspace_path,
        approval_callback=approval_callback,
        failed_response=response,
    )
    return fallback_response or response


def _merotec_locked_should_try_external_ai_fallback(self, response):
    if not getattr(self, "external_ai_fallback_enabled", False):
        return False
    normalizer = getattr(self, "normalize_plain_text", None)
    normalized = normalizer(response or "") if callable(normalizer) else str(response or "").lower()
    failure_markers = (
        "sem cota",
        "cota disponivel",
        "insufficient_quota",
        "quota",
        "rate limit",
        "limite atingido",
        "modelo local gguf nao",
        "erro ao carregar modelo local gguf",
        "llama-cpp-python nao esta instalado",
        "lm studio sem modelo configurado",
        "nao consegui conectar ao lm studio",
        "erro no lm studio",
        "erro no motor openai",
        "erro no motor genai",
        "service unavailable",
        "overloaded",
        "temporar",
    )
    return any(marker in normalized for marker in failure_markers)


def _merotec_locked_external_providers(self):
    providers = []
    current = (getattr(self, "provider", "") or "").strip().lower()
    if current == "local_gguf" and not getattr(self, "local_gguf_allow_external_fallback", False):
        return []
    if current == "lm_studio" and not getattr(self, "lm_studio_allow_external_fallback", False):
        return []

    if current != "web_chat" and getattr(self, "web_chat_url", ""):
        providers.append("web_chat")
    if current != "codex":
        providers.append("codex")
    if current != "openai" and getattr(self, "openai_api_key", ""):
        providers.append("openai")
    if current != "google" and getattr(self, "google_api_key", "") and GoogleClient:
        providers.append("google")
    if (
        current != "lm_studio"
        and getattr(self, "lm_studio_base_url", "")
        and getattr(self, "lm_studio_model_name", "")
    ):
        providers.append("lm_studio")
    try:
        local_ready = bool(self.local_gguf_is_ready())
    except Exception:
        local_ready = False
    if current != "local_gguf" and local_ready:
        providers.append("local_gguf")
    return [
        provider
        for provider in providers
        if provider in {"web_chat", "codex", "openai", "google", "lm_studio", "local_gguf"}
    ]


def _merotec_locked_try_external_ai_fallback(
    self,
    prompt,
    image_path=None,
    code_context=None,
    stream_callback=None,
    workspace_path=None,
    approval_callback=None,
    failed_response="",
):
    if not getattr(self, "external_ai_fallback_enabled", False):
        return None
    providers = self.configured_external_ai_fallback_providers()
    if not providers:
        return None
    original_state = self._snapshot_provider_state()
    try:
        for provider in providers:
            if getattr(self, "cancel_requested", False):
                return None
            label = self.external_provider_label(provider)
            if stream_callback:
                stream_callback(f"\nFallback externo: tentando {label}...\n")
            self._activate_provider(provider)
            response = self._generate_solution_with_provider(
                provider,
                prompt,
                image_path=image_path,
                code_context=code_context,
                stream_callback=stream_callback,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
            if response and not self.should_try_external_ai_fallback(response):
                return f"[Fallback externo: {label}]\n\n{response}"
        return None
    finally:
        self._restore_provider_state(original_state)


UniversalEngine.generate_solution = _merotec_locked_generate_solution
UniversalEngine.should_try_external_ai_fallback = _merotec_locked_should_try_external_ai_fallback
UniversalEngine.configured_external_ai_fallback_providers = _merotec_locked_external_providers
UniversalEngine.try_external_ai_fallback = _merotec_locked_try_external_ai_fallback


# MEROTEC_WEB_CHAT_EDIT_CONTRACT_V6
# Chat sites render unified diffs as regular prose in some responses.  The
# executable protocol therefore uses fenced WRITE/REPLACE blocks as the primary
# format.  Legacy PATCH replies remain supported by agent_actions.py only so a
# task can recover; the chat must not generate new PATCH messages.

def _merotec_v6_chat_instruction(self):
    return (
        "PROTOCOLO EXECUTÁVEL DA MEROTEC IA IDE — substitui instruções anteriores sobre formato de edição.\n"
        "A missão permanece ativa até uma mudança aplicada e uma validação, bloqueio externo explícito ou cancelamento.\n"
        "Responda com EXATAMENTE UMA ação da IDE e nenhum texto explicativo antes ou depois dela.\n"
        "Ações de uma linha: [READ: caminho], [SEARCH_TEXT: padrão | caminho], [EXECUTE: comando de teste real], "
        "[HUMAN_TEST: auto], [WEB_SEARCH: consulta], [OPEN_URL: url], [SCREENSHOT: tela], [FINAL: resumo].\n"
        "NÃO use [PATCH], unified diff, '*** Begin Patch', '@@', '*** Update File' ou diff. "
        "Esse formato é incompatível com o Chat Web e gera edições sem arquivo ou sem indentação.\n"
        "Para mudar parte de um arquivo existente, primeiro leia o arquivo e use exatamente:\n"
        "[REPLACE: caminho/arquivo]\n"
        "[OLD]\n"
        "```linguagem\n"
        "trecho EXATO copiado do arquivo atual\n"
        "```\n"
        "[/OLD]\n"
        "[NEW]\n"
        "```linguagem\n"
        "trecho novo com todos os espaços preservados\n"
        "```\n"
        "[/NEW]\n"
        "[/REPLACE]\n"
        "Para reescrever um arquivo inteiro, use exatamente:\n"
        "[WRITE: caminho/arquivo]\n"
        "```linguagem\n"
        "conteúdo completo válido\n"
        "```\n"
        "[/WRITE]\n"
        "Nunca use reticências. Para Python, use quatro espaços por nível, nunca tab, e mantenha o código dentro da cerca Markdown. "
        "Nunca use EXECUTE para echo, Write-Host, printf, true, exit 0 ou para declarar que a missão terminou; esses comandos são recusados. "
        "Use [FINAL: resumo] somente depois de a IDE aplicar uma alteração e aprovar uma validação real. "
        "Se uma edição for recusada, use o arquivo atual devolvido pela IDE; não repita a mesma edição."
    )


# _merotec_chat_generate_web resolves this global at call time, so replacing it
# changes the active message contract without replacing the working browser flow.
_merotec_chat_instruction = _merotec_v6_chat_instruction
UniversalEngine._web_chat_conversation_instruction = _merotec_v6_chat_instruction

# MEROTEC_WRITE_ONLY_WEB_CHAT_CONTRACT_V8
# The browser chat has no tool-call transaction layer.  A single full-file WRITE
# contract is more reliable than line-exact REPLACE or unified diff transport.

def _merotec_v8_chat_instruction(self):
    return (
        "PROTOCOLO ATIVO DA MEROTEC IA IDE V8 — esta instrução substitui formatos anteriores.\n"
        "A missão continua até uma mudança aplicada e uma validação real, bloqueio externo explícito, cancelamento ou [FINAL] aceito.\n"
        "Responda com EXATAMENTE UMA ação da IDE, sem texto antes ou depois.\n"
        "Ações curtas: [READ: caminho], [SEARCH_TEXT: padrão | caminho], [EXECUTE: comando de teste real], "
        "[HUMAN_TEST: auto], [OPEN_URL: url], [SCREENSHOT: tela], [FINAL: resumo].\n"
        "Exemplos canonicos: [READ: main.py], [SEARCH_TEXT: def main | main.py], [EXECUTE: python -m unittest]. "
        "Não use [READ] arquivo; use sempre [READ: arquivo].\n"
        "PARA CRIAR OU MODIFICAR QUALQUER ARQUIVO, use SOMENTE WRITE completo. WRITE cria arquivos inexistentes "
        "e sobrescreve arquivos existentes com backup automático. Não use PATCH, unified diff, *** Begin Patch, @@, REPLACE, [OLD] ou [NEW].\n"
        "Formato obrigatório de edição:\n"
        "[WRITE: caminho/arquivo.ext]\n"
        "```linguagem\n"
        "conteúdo COMPLETO, válido e sem reticências\n"
        "```\n"
        "[/WRITE]\n"
        "Antes de alterar um arquivo existente, use [READ: caminho] quando o conteúdo atual não estiver no contexto. "
        "Para Python, use quatro espaços por nível, nunca tab. Preserve todos os espaços dentro da cerca Markdown.\n"
        "Nunca use EXECUTE para echo, Write-Host, printf, true, exit 0 ou para declarar conclusão. "
        "Use [FINAL: resumo] somente depois de uma alteração aplicada e validação real aprovada pela IDE.\n"
        "Se a IDE recusar uma edição, use o arquivo canônico devolvido e envie um novo [WRITE] completo; não repita a resposta inválida."
    )


_merotec_chat_instruction = _merotec_v8_chat_instruction
UniversalEngine._web_chat_conversation_instruction = _merotec_v8_chat_instruction

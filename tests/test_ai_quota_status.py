import sys
import time
import unittest
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import UniversalApp
from modules.ai_config import AiConfigMixin
from modules.app_state import AppStateMixin
from modules.engine import UniversalEngine


def bare_engine():
    engine = UniversalEngine.__new__(UniversalEngine)
    engine.provider = "codex"
    engine.model_id = "gpt-5.5"
    engine.codex_reasoning_effort = "high"
    engine.client = "codex-cli"
    engine.latest_rate_limits = None
    engine.latest_token_usage = None
    engine.latest_quota_problem = ""
    engine.latest_quota_updated_at = 0
    return engine


class AiQuotaStatusTest(unittest.TestCase):
    def test_quota_status_combines_codex_rate_limit_and_context_tokens(self):
        engine = bare_engine()
        engine.latest_rate_limits = {
            "planType": "plus",
            "limitId": "codex",
            "primary": {
                "usedPercent": 42,
                "resetsAt": int(time.time() + 3600),
            },
        }
        engine.latest_token_usage = {
            "modelContextWindow": 128000,
            "last": {
                "cachedInputTokens": 0,
                "inputTokens": 1000,
                "outputTokens": 250,
                "reasoningOutputTokens": 100,
                "totalTokens": 1350,
            },
            "total": {
                "cachedInputTokens": 0,
                "inputTokens": 12000,
                "outputTokens": 2000,
                "reasoningOutputTokens": 500,
                "totalTokens": 14500,
            },
        }

        text = engine.quota_status_text()

        self.assertIn("CODEX", text)
        self.assertIn("plus", text)
        self.assertIn("janela 42%", text)
        self.assertIn("ctx 14.5k/128.0k", text)

    def test_quota_status_reports_limit_problem(self):
        engine = bare_engine()
        engine._remember_rate_limits(
            {
                "rateLimits": {
                    "rateLimitReachedType": "workspace_member_usage_limit_reached",
                    "primary": {"usedPercent": 100},
                }
            }
        )

        self.assertIn("limite do membro atingido", engine.quota_status_text())

    def test_status_bar_appends_quota_once(self):
        engine = bare_engine()
        app = type("DummyApp", (), {"engine": engine})()

        first = UniversalApp.status_with_ai_quota(app, "IA trabalhando...")
        second = UniversalApp.status_with_ai_quota(app, first)

        self.assertIn("IA trabalhando...", first)
        self.assertIn("Cota: CODEX gpt-5.5/high", first)
        self.assertEqual(first, second)

    def test_ai_sidebar_status_includes_current_quota(self):
        engine = bare_engine()
        engine.latest_rate_limits = {
            "planType": "plus",
            "limitId": "codex",
            "primary": {"usedPercent": 17},
        }

        class DummyLabel:
            def __init__(self):
                self.text = ""

            def configure(self, **kwargs):
                self.text = kwargs.get("text", self.text)

        class DummyApp(AiConfigMixin):
            def __init__(self, engine):
                self.engine = engine
                self.ai_status_label = DummyLabel()

        app = DummyApp(engine)
        app.refresh_ai_status()

        self.assertIn("Cota atual:", app.ai_status_label.text)
        self.assertIn("janela 17%", app.ai_status_label.text)

    def test_ai_sidebar_status_includes_fallback_policy(self):
        engine = bare_engine()
        engine.external_ai_fallback_enabled = True
        engine.configured_external_ai_fallback_providers = lambda: ["web_chat", "openai"]

        class DummyApp(AiConfigMixin):
            def __init__(self, engine):
                self.engine = engine

        text = DummyApp(engine).ai_status_text()

        self.assertIn("Fallback externo: WEB_CHAT, OPENAI", text)
        self.assertIn("Fallback local: RAG offline extrativo", text)
        self.assertIn("limitado ao corpus da sub-rede", text)

    def test_local_provider_status_reports_external_fallback_disabled(self):
        engine = bare_engine()
        engine.provider = "local_gguf"
        engine.model_id = "modelo.gguf"
        engine.local_gguf_allow_external_fallback = False
        engine.external_ai_fallback_enabled = True
        engine.configured_external_ai_fallback_providers = lambda: ["codex"]
        engine.status_text = lambda: "LOCAL_GGUF | modelo.gguf | modelo pronto"
        engine.quota_status_text = lambda: ""

        class DummyApp(AiConfigMixin):
            def __init__(self, engine):
                self.engine = engine

        text = DummyApp(engine).ai_status_text()

        self.assertIn("Fallback externo: desligado", text)
        self.assertIn("Fallback local: RAG offline extrativo", text)

    def test_codex_progress_timeout_is_recoverable(self):
        engine = bare_engine()

        self.assertTrue(
            engine._is_codex_progress_timeout(
                "Codex ficou sem enviar progresso por mais de 900 segundos."
            )
        )
        self.assertFalse(engine._is_codex_progress_timeout("Codex retornou erro 1."))

    def test_codex_app_server_progress_timeout_falls_back_to_exec(self):
        engine = bare_engine()
        engine.codex_model_name = "gpt-5.5"
        engine.codex_reasoning_effort = "high"
        engine._find_codex_executable = lambda: "codex"
        engine._codex_is_logged_in = lambda executable: True
        engine._generate_codex_app_server_solution = lambda *args, **kwargs: (
            "Codex ficou sem enviar progresso por mais de 900 segundos."
        )
        engine._generate_codex_exec_solution = lambda *args, **kwargs: "fallback exec ok"

        response = engine._generate_codex_solution("corrigir tarefa longa")

        self.assertEqual("fallback exec ok", response)

    def test_codex_timeout_settings_are_exported_to_environment(self):
        class DummyState(AppStateMixin):
            def __init__(self):
                self.settings = {
                    "ai_provider": "codex",
                    "codex_model_name": "gpt-5.5",
                    "codex_reasoning_effort": "high",
                    "autonomous_unrestricted_mode": True,
                    "codex_auto_approve_app_server_requests": True,
                    "codex_app_server_approval_policy": "on-request",
                    "codex_app_server_idle_timeout_seconds": 1200,
                    "codex_task_timeout_seconds": 5400,
                }

        previous = {
            key: os.environ.get(key)
            for key in (
                "MEROTEC_CODEX_APP_SERVER_IDLE_TIMEOUT_SECONDS",
                "MEROTEC_CODEX_TASK_TIMEOUT_SECONDS",
            )
        }
        try:
            app = DummyState()
            app._apply_settings_to_environment()

            self.assertEqual("1200", os.environ["MEROTEC_CODEX_APP_SERVER_IDLE_TIMEOUT_SECONDS"])
            self.assertEqual("5400", os.environ["MEROTEC_CODEX_TASK_TIMEOUT_SECONDS"])
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()

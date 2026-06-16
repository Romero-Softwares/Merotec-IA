import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import UniversalApp
from modules.ai_config import AiConfigMixin
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

        self.assertIn("IA", text)
        self.assertIn("plus", text)
        self.assertIn("janela 45%", text)
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


if __name__ == "__main__":
    unittest.main()

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from modules.engine import UniversalEngine


def engine_for_api(model="selected-model", base_url="https://api.openai.com/v1"):
    engine = UniversalEngine.__new__(UniversalEngine)
    engine.openai_model_name = model
    engine.model_id = "stale-display-model"
    engine.openai_api_key = "sk-test-placeholder"
    engine.openai_base_url = base_url
    engine.active_openai_model_name = "old-server-model"
    engine._openai_chat_message_content = Mock(return_value="prompt")
    engine._agent_protocol_system_instruction = Mock(return_value="instructions")
    return engine


def response(payload=None):
    payload = payload or {"model": "selected-model", "output": [{"content": [{"type": "output_text", "text": "ok"}]}]}
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = json.dumps(payload).encode()
    return result


class OpenAIModelSwitchTests(unittest.TestCase):
    def test_current_model_is_used_instead_of_stale_display_identity(self):
        engine = engine_for_api()
        with patch("modules.engine.urllib.request.urlopen", return_value=response()) as request:
            self.assertEqual("ok", engine._generate_openai_solution("test"))
        req = request.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual("selected-model", payload["model"])
        self.assertEqual("selected-model", engine.model_id)
        self.assertEqual("https://api.openai.com/v1/responses", req.full_url)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(4096, payload["max_output_tokens"])
        self.assertFalse(payload["store"])

    def test_dynamic_second_selection_reaches_the_next_request(self):
        engine = engine_for_api()
        with patch("modules.engine.urllib.request.urlopen", return_value=response()) as request:
            engine._generate_openai_solution("first")
            engine.openai_model_name = "another-exact-id"
            engine._generate_openai_solution("second")
        models = [json.loads(call.args[0].data)["model"] for call in request.call_args_list]
        self.assertEqual(["selected-model", "another-exact-id"], models)

    def test_blank_selection_never_silently_uses_old_or_hardcoded_model(self):
        engine = engine_for_api(model="")
        with patch("modules.engine.urllib.request.urlopen") as request:
            text = engine._generate_openai_solution("test")
        self.assertIn("ID exato", text)
        request.assert_not_called()

    def test_compatible_server_preserves_chat_completions_contract(self):
        engine = engine_for_api(base_url="http://localhost:9000/custom/v1/chat/completions")
        with patch("modules.engine.urllib.request.urlopen", return_value=response({"choices": [{"message": {"content": "ok"}}]})) as request:
            self.assertEqual("ok", engine._generate_openai_solution("test"))
        req = request.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual("http://localhost:9000/custom/v1/chat/completions", req.full_url)
        self.assertEqual("selected-model", payload["model"])
        self.assertIn("messages", payload)
        self.assertEqual(4096, payload["max_tokens"])

    def test_responses_multimodal_input_is_converted_without_losing_image(self):
        engine = engine_for_api()
        engine._openai_chat_message_content.return_value = [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "high"}},
        ]
        with patch("modules.engine.urllib.request.urlopen", return_value=response()) as request:
            engine._generate_openai_solution("test")
        content = json.loads(request.call_args.args[0].data)["input"][0]["content"]
        self.assertEqual({"type": "input_text", "text": "describe"}, content[0])
        self.assertEqual("input_image", content[1]["type"])
        self.assertEqual("data:image/png;base64,AA==", content[1]["image_url"])

    def test_model_rejection_keeps_selection_and_does_not_retry_other_model(self):
        engine = engine_for_api()
        failure = urllib.error.HTTPError("https://api.openai.com/v1/responses", 404, "Not Found", {},
            io.BytesIO(b'{"error":{"code":"model_not_found","message":"model unavailable"}}'))
        with patch("modules.engine.urllib.request.urlopen", side_effect=failure) as request:
            text = engine._generate_openai_solution("test")
        self.assertIn("selected-model", text)
        self.assertIn("Atualize a lista", text)
        self.assertNotIn("OpenRouter", text)
        self.assertEqual("selected-model", engine.openai_model_name)
        self.assertEqual(1, request.call_count)

    def test_incomplete_response_does_not_apply_partial_agent_tags(self):
        engine = engine_for_api()
        payload = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                   "output": [{"content": [{"text": "[WRITE: partial.py]broken"}]}]}
        with patch("modules.engine.urllib.request.urlopen", return_value=response(payload)):
            text = engine._generate_openai_solution("test")
        self.assertIn("não foi aplicada", text)
        self.assertNotIn("[WRITE:", text)

    def test_key_and_parameter_errors_are_provider_neutral(self):
        engine = engine_for_api()
        key = engine._format_openai_http_error(401, '{"error":{"code":"invalid_api_key"}}')
        self.assertNotIn("OpenRouter", key)
        parameter = engine._format_openai_http_error(400,
            '{"error":{"param":"temperature","message":"temperature not supported with this model"}}')
        self.assertIn("temperature", parameter)
        self.assertNotIn("não está disponível", parameter)


class CodexDefaultModelTests(unittest.TestCase):
    def codex_engine(self, model=""):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.codex_model_name = model
        engine.codex_reasoning_effort = "high"
        engine.system_instruction = "test"
        engine._message_payload = Mock(return_value="test")
        engine.active_process = None
        return engine

    def test_blank_app_server_model_uses_account_default(self):
        engine = self.codex_engine()
        with patch("modules.engine.subprocess.Popen", side_effect=OSError("stop")) as popen:
            self.assertIsNone(engine._generate_codex_app_server_solution("codex", "test"))
        command = popen.call_args.args[0]
        self.assertFalse(any(arg.startswith("model=") for arg in command))

    def test_explicit_app_server_model_is_preserved_exactly(self):
        engine = self.codex_engine("exact-account-model")
        with patch("modules.engine.subprocess.Popen", side_effect=OSError("stop")) as popen:
            engine._generate_codex_app_server_solution("codex", "test")
        self.assertIn('model="exact-account-model"', popen.call_args.args[0])

    def test_blank_exec_model_uses_account_default(self):
        engine = self.codex_engine()
        with tempfile.TemporaryDirectory() as folder, patch("modules.engine.subprocess.Popen", side_effect=OSError("stop")) as popen:
            engine._generate_codex_exec_solution("codex", "test", workspace_path=Path(folder))
        command = popen.call_args.args[0]
        self.assertFalse(any(arg.startswith("model=") for arg in command))
        self.assertNotIn("--model", command)

    def test_default_model_label_does_not_invent_model_identity(self):
        engine = self.codex_engine()
        engine.provider = "codex"
        engine._model_cache = {"codex:": "Padrão da conta"}
        self.assertEqual("modelo não confirmado", engine._resolve_model_id())

    def test_discovered_default_id_is_displayed_without_pinning_selection(self):
        engine = self.codex_engine()
        engine.provider = "codex"
        engine.model_id = engine._resolve_model_id()
        self.assertTrue(engine.remember_codex_default_model("exact-default-id"))
        self.assertEqual("", engine.codex_model_name)
        self.assertEqual("exact-default-id", engine.model_id)
        self.assertEqual("exact-default-id", engine.assistant_display_name())
        self.assertEqual("exact-default-id", engine._resolve_model_id())

    def test_discovery_does_not_leak_between_engine_instances(self):
        first, second = self.codex_engine(), self.codex_engine()
        first.provider = second.provider = "codex"
        first.remember_codex_default_model("first-account-default")
        self.assertEqual("modelo não confirmado", second._resolve_model_id())

    def test_runtime_reported_id_wins_and_fallback_model_is_visible(self):
        engine = self.codex_engine("configured-id")
        engine.provider = "codex"
        engine.remember_codex_default_model("default-id")
        self.assertEqual("configured-id", engine.assistant_display_name())
        self.assertTrue(engine._remember_codex_response_model({"thread": {"model": "actual-thread-model"}}))
        self.assertEqual("actual-thread-model", engine.assistant_display_name())
        engine._remember_codex_response_model({"turn": {"modelId": "actual-turn-model"}})
        self.assertEqual("actual-turn-model", engine.assistant_display_name())
        engine._remember_codex_response_model({}, requested_model="capacity-fallback-id")
        self.assertEqual("capacity-fallback-id", engine.assistant_display_name())
        self.assertEqual("configured-id", engine.codex_model_name)

    def test_status_and_quota_use_exact_effective_identity(self):
        engine = self.codex_engine()
        engine.provider = "codex"
        engine.client = "codex-cli"
        engine.latest_rate_limits = engine.latest_token_usage = None
        engine.latest_quota_problem = ""
        engine.model_id = "old-model"
        engine.remember_codex_default_model("discovered-model")
        self.assertIn("discovered-model", engine.status_text())
        self.assertIn("discovered-model", engine.quota_status_text())
        engine._remember_codex_response_model({"model": "effective-model"})
        self.assertIn("effective-model", engine.status_text())
        self.assertIn("effective-model", engine.quota_status_text())
        self.assertNotIn("discovered-model", engine.status_text())

    def test_invalid_labels_and_non_model_payloads_do_not_replace_exact_ids(self):
        engine = self.codex_engine()
        engine.provider = "codex"
        engine.remember_codex_default_model("exact-default")
        self.assertFalse(engine.remember_codex_default_model("Padrão da conta"))
        self.assertFalse(engine.remember_codex_default_model({"id": "not-a-string"}))
        self.assertFalse(engine._remember_codex_response_model({"item": {"text": "model: invented"}}))
        self.assertEqual("exact-default", engine.assistant_display_name())
        engine.codex_model_name = "rótulo com espaços"
        self.assertEqual("modelo não confirmado", engine.assistant_display_name())

    def test_cli_metadata_can_supply_default_identity_without_parsing_answer_text(self):
        engine = self.codex_engine()
        engine.provider = "codex"
        self.assertFalse(engine._remember_codex_cli_model("model: assistant-prose"))
        self.assertTrue(engine._remember_codex_cli_model("OpenAI Codex v1\n--------\nworkdir: project\nmodel: exact-cli-model\nprovider: openai\n"))
        self.assertEqual("exact-cli-model", engine.assistant_display_name())

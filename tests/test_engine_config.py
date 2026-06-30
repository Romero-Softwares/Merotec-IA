import json
import sys
import unittest
from unittest import mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules import config
from modules.engine import UniversalEngine


class EngineConfigTest(unittest.TestCase):
    def test_config_exports_legacy_constants_used_by_engine(self):
        for name in (
            "AI_PROVIDER",
            "CODEX_MODEL_NAME",
            "CODEX_REASONING_EFFORT",
            "OPENAI_API_KEY",
            "OPENAI_MODEL_NAME",
            "LM_STUDIO_BASE_URL",
            "LM_STUDIO_MODEL_NAME",
            "LM_STUDIO_API_KEY",
            "GOOGLE_API_KEY",
            "MODEL_NAME",
            "LOCAL_GGUF_PATH",
            "LANGUAGE",
        ):
            self.assertTrue(hasattr(config, name), name)

    def test_resolve_model_id_uses_provider_specific_model(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._model_cache = {}
        engine.provider = "openai"
        engine.codex_model_name = "gpt-5.5"
        engine.openai_model_name = "deepseek/deepseek-chat"
        engine.google_model_name = "gemini-2.5-flash"

        self.assertEqual("deepseek/deepseek-chat", engine._resolve_model_id())

        engine.provider = "google"
        self.assertEqual("gemini-2.5-flash", engine._resolve_model_id())

    def test_resolve_model_id_uses_local_gguf_filename(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._model_cache = {}
        engine.provider = "local_gguf"
        engine.codex_model_name = ""
        engine.openai_model_name = ""
        engine.google_model_name = ""
        engine.local_gguf_path = str(PROJECT_ROOT / "models" / "teste.gguf")

        self.assertEqual("teste.gguf", engine._resolve_model_id())

    def test_resolve_model_id_uses_lm_studio_model(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine._model_cache = {}
        engine.provider = "lm_studio"
        engine.codex_model_name = ""
        engine.openai_model_name = ""
        engine.google_model_name = ""
        engine.lm_studio_model_name = "google/gemma-4-e4b"

        self.assertEqual("google/gemma-4-e4b", engine._resolve_model_id())

    def test_lm_studio_display_name_uses_model_reported_by_server(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "lm_studio"
        engine.lm_studio_model_name = "configured/model"
        engine.active_lm_studio_model_name = ""
        engine.model_id = "configured/model"
        self.assertEqual("configured/model", engine.assistant_display_name())

        engine._remember_lm_studio_response_model({"model": "runtime/model-from-server"})
        self.assertEqual("runtime/model-from-server", engine.assistant_display_name())

    def test_openai_display_name_uses_model_reported_by_server(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.openai_model_name = "configured/model"
        engine.active_openai_model_name = ""
        engine.model_id = "configured/model"
        self.assertEqual("configured/model", engine.assistant_display_name())

        engine._remember_openai_response_model({"model": "runtime/provider-model"})
        self.assertEqual("runtime/provider-model", engine.assistant_display_name())

    def test_lm_studio_url_is_normalized_to_openai_v1(self):
        self.assertEqual(
            "http://127.0.0.1:1234/v1",
            UniversalEngine.normalize_lm_studio_base_url("http://127.0.0.1:1234"),
        )
        self.assertEqual(
            "http://127.0.0.1:1234/v1",
            UniversalEngine.normalize_lm_studio_base_url(
                "http://127.0.0.1:1234/v1/chat/completions"
            ),
        )

    def test_lm_studio_discovery_prefers_chat_models(self):
        response = mock.MagicMock()
        response.read.return_value = (
            b'{"data":[{"id":"google/gemma-4-e4b"},'
            b'{"id":"text-embedding-nomic-embed-text-v1.5"}]}'
        )
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response):
            models = UniversalEngine.discover_lm_studio_models("http://127.0.0.1:1234")

        self.assertEqual(["google/gemma-4-e4b"], models)

    def test_lm_studio_simple_check_does_not_send_entire_workspace(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.lm_studio_max_input_chars = 10000

        payload = engine._lm_studio_message_payload("teste", "mapa do workspace " * 5000)

        self.assertEqual("Pedido do usuario: teste", payload)

    def test_lm_studio_compacts_large_engineering_context(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.lm_studio_max_input_chars = 4000
        context = (
            "MISSAO ATIVA DA IA:\ncorrigir o projeto\n\n"
            "MODO CODEX DA IDE:\n" + ("regra duplicada " * 1000) + "\n\n"
            "Alteracoes recentes feitas pela IDE:\nnenhuma\n\n"
            "BRIEFING INTELIGENTE DA IDE:\n" + ("detalhe tecnico " * 1000)
        )

        compacted = engine._compact_lm_studio_context(context)

        self.assertLessEqual(len(compacted), 4100)
        self.assertIn("MISSAO ATIVA", compacted)
        self.assertNotIn("regra duplicada", compacted)

    def test_lm_studio_recovers_action_tag_without_exposing_reasoning(self):
        reasoning = (
            "Thinking Process: analisar detalhes internos que nao devem aparecer. "
            "A acao correta e [HUMAN_TEST: auto]"
        )

        action = UniversalEngine._extract_lm_studio_action_from_reasoning(reasoning)

        self.assertEqual("[HUMAN_TEST: auto]", action)

    def test_lm_studio_routes_explicit_visual_test_without_model_wait(self):
        action = UniversalEngine._lm_studio_direct_protocol_action(
            "realize testes visuais do projeto"
        )

        self.assertEqual("[HUMAN_TEST: auto]", action)

    def test_lm_studio_engineering_prompt_requires_action_instead_of_plan(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.lm_studio_max_input_chars = 14000

        payload = engine._lm_studio_message_payload(
            "corrija o sistema do projeto",
            "MISSAO ATIVA DA IA:\ncorrigir o sistema",
        )

        self.assertIn("tarefa de implementacao", payload)
        self.assertIn("proxima tag real", payload)

    def test_non_codex_agent_payload_is_compact_and_requires_real_actions(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.language = "portugues"
        engine.lm_studio_max_input_chars = 4000
        context = "MISSAO ATIVA DA IA:\nCorrigir o sistema\n\n" + ("mapa do workspace " * 3000)

        payload = engine._agent_message_payload("Implemente a correcao", context)
        instruction = engine._agent_protocol_system_instruction()

        self.assertLessEqual(len(payload), 4300)
        self.assertIn("proxima tag real", payload)
        self.assertIn("uma unica acao real por vez", instruction)
        self.assertIn("[REPLACE:", instruction)

    def test_openai_text_recovers_action_hidden_in_reasoning(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        data = {
            "choices": [
                {"message": {"content": "", "reasoning_content": "Vou agir. [READ: src/app.py]"}}
            ]
        }
        self.assertEqual("[READ: src/app.py]", engine._extract_openai_text(data))

    def test_openrouter_keeps_exact_configured_model(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.openai_api_key = "sk-or-test"
        engine.openai_base_url = "https://openrouter.ai/api/v1"
        engine.openai_model_name = "vendor/custom-coder:free"
        engine.model_id = engine.openai_model_name
        engine.language = "portugues"
        engine.lm_studio_max_input_chars = 4000
        engine.latest_token_usage = {}

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual("ok", engine._generate_openai_solution("teste"))

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("vendor/custom-coder:free", payload["model"])
        self.assertEqual("https://openrouter.ai/api/v1/chat/completions", request.full_url)

    def test_legacy_openrouter_key_migrates_default_openai_endpoint(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.openai_api_key = "sk-or-legacy"
        engine.openai_base_url = "https://api.openai.com/v1"
        engine.openai_model_name = "nvidia/test-model:free"
        engine.model_id = engine.openai_model_name
        engine.language = "portugues"
        engine.lm_studio_max_input_chars = 4000
        engine.latest_token_usage = {}

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual("ok", engine._generate_openai_solution("teste"))

        self.assertEqual(
            "https://openrouter.ai/api/v1/chat/completions",
            urlopen.call_args.args[0].full_url,
        )

    def test_reasoning_recovery_preserves_complete_write_action(self):
        reasoning = "Decisao interna: [WRITE: app.py]print('ok')\n[/WRITE]"
        self.assertEqual(
            "[WRITE: app.py]print('ok')\n[/WRITE]",
            UniversalEngine._extract_lm_studio_action_from_reasoning(reasoning),
        )

    def test_lm_studio_analysis_prompt_demands_complete_final_report(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.lm_studio_max_input_chars = 14000

        payload = engine._lm_studio_message_payload(
            "faca uma analise detalhada do projeto",
            "MAPA PERMANENTE DO PROJETO PARA A IA:\ncomponentes",
        )

        self.assertIn("analise final detalhada", payload)
        self.assertIn("nao termine em um esboco incompleto", payload.lower())

    def test_lm_studio_removes_preplanning_from_analysis_display(self):
        raw = (
            "Vou estruturar a resposta em seis partes.\n\n"
            "**Análise Detalhada do Projeto**\n\nArquitetura real."
        )

        cleaned = UniversalEngine._clean_lm_studio_analysis_answer(raw)

        self.assertTrue(cleaned.startswith("**Análise Detalhada"))
        self.assertNotIn("Vou estruturar", cleaned)

    def test_local_gguf_can_join_fallback_providers_when_ready(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.openai_api_key = ""
        engine.google_api_key = ""
        engine.local_gguf_allow_external_fallback = True
        engine.local_gguf_is_ready = lambda: True

        self.assertIn("local_gguf", engine.configured_external_ai_fallback_providers())

    def test_local_gguf_does_not_fallback_to_external_by_default(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "local_gguf"
        engine.local_gguf_allow_external_fallback = False
        engine.cancel_requested = False
        engine._generate_solution_with_provider = lambda *args, **kwargs: "Modelo local GGUF nao configurado."

        response = engine.generate_solution("teste local")

        self.assertEqual("Modelo local GGUF nao configurado.", response)
        self.assertEqual([], engine.configured_external_ai_fallback_providers())

    def test_local_gguf_can_fallback_when_explicitly_enabled(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "local_gguf"
        engine.local_gguf_allow_external_fallback = True
        engine.external_ai_fallback_enabled = True
        engine.cancel_requested = False
        engine.openai_api_key = ""
        engine.google_api_key = ""
        engine.local_gguf_is_ready = lambda: False
        engine._snapshot_provider_state = lambda: {}
        engine._restore_provider_state = lambda _state: None
        engine._activate_provider = lambda _provider: None
        engine.configured_external_ai_fallback_providers = lambda: ["codex"]

        calls = []

        def fake_generate(provider, *args, **kwargs):
            calls.append(provider)
            if provider == "local_gguf":
                return "Modelo local GGUF nao configurado."
            return "Resposta do Codex autorizada."

        engine._generate_solution_with_provider = fake_generate

        response = engine.generate_solution("teste local")

        self.assertEqual(["local_gguf", "codex"], calls)
        self.assertIn("Fallback externo: Codex/ChatGPT", response)

    def test_local_gguf_provider_uses_loader_pipeline(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        calls = []

        def fake_local(prompt, code_context=None, stream_callback=None):
            calls.append((prompt, code_context, stream_callback))
            return "Resposta local carregada."

        engine._generate_local_gguf_solution = fake_local

        response = engine._generate_solution_with_provider(
            "local_gguf",
            "pergunta",
            code_context="contexto",
        )

        self.assertEqual("Resposta local carregada.", response)
        self.assertEqual([("pergunta", "contexto", None)], calls)

    def test_local_gguf_compacts_prompt_to_context_budget(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.local_llm = None
        huge_context = (
            "Instrucao do usuario/sistema: analise completa\n\n"
            "MISSAO ATIVA DA IA:\nanalise o projeto\n\n"
            "BRIEFING INTELIGENTE DA IDE:\n" + ("brief " * 1200) + "\n\n"
            "Arquivos do workspace:\n" + ("arquivo.py\n" * 1200)
        )
        engine.estimate_local_gguf_tokens = lambda text: max(1, len(str(text)) // 4)

        compacted = engine.compact_local_gguf_prompt(huge_context, 700)

        self.assertLessEqual(engine.estimate_local_gguf_tokens(compacted), 700)
        self.assertIn("CONTEXTO COMPACTADO", compacted)
        self.assertIn("MISSAO ATIVA DA IA", compacted)

    def test_local_gguf_input_budget_caps_large_context_for_cpu(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.local_gguf_n_ctx = 4096
        engine.local_gguf_max_input_tokens = 1800

        self.assertEqual(1800, engine.local_gguf_input_token_budget(512))

    def test_local_gguf_thread_setting_auto_uses_cpu_bound_default(self):
        auto_threads = UniversalEngine._thread_setting({}, "local_gguf_n_threads", "LOCAL_GGUF_N_THREADS")

        self.assertGreaterEqual(auto_threads, 1)
        self.assertLessEqual(auto_threads, 8)

    def test_local_gguf_completion_uses_timeout_protected_runner(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.local_gguf_n_ctx = 4096
        engine.local_gguf_max_tokens = 512
        engine.local_gguf_max_input_tokens = 1800
        engine.local_gguf_path = str(PROJECT_ROOT / "models" / "teste.gguf")
        engine.validate_local_gguf_for_generation = lambda: ""
        engine.load_local_gguf_model = lambda: ""
        engine.estimate_local_gguf_tokens = lambda text: max(1, len(str(text)) // 4)
        engine.compact_local_gguf_prompt = lambda text, budget: text[: budget * 4]
        calls = []

        def fake_run(user_text, max_output_tokens):
            calls.append((user_text, max_output_tokens))
            return "Resposta local."

        engine._run_local_gguf_completion_subprocess = fake_run

        response = engine._generate_local_gguf_solution("pergunta", code_context="contexto")

        self.assertEqual("Resposta local.", response)
        self.assertEqual(1, len(calls))
        self.assertEqual(512, calls[0][1])

    def test_generate_solution_uses_external_fallback_when_provider_fails(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.external_ai_fallback_enabled = True
        engine.cancel_requested = False
        engine.configured_external_ai_fallback_providers = lambda: ["google"]
        engine._snapshot_provider_state = lambda: {}
        engine._restore_provider_state = lambda _state: None
        engine._activate_provider = lambda _provider: None

        calls = []

        def fake_generate(provider, *args, **kwargs):
            calls.append(provider)
            if provider == "openai":
                return "Sua chave foi aceita, mas a conta/projeto esta sem cota disponivel."
            return "Resposta vinda do Gemini."

        engine._generate_solution_with_provider = fake_generate

        response = engine.generate_solution("pergunta complexa")

        self.assertEqual(["openai", "google"], calls)
        self.assertIn("Fallback externo: Gemini", response)
        self.assertIn("Resposta vinda do Gemini.", response)

    def test_generate_solution_does_not_fallback_when_answer_is_valid(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.external_ai_fallback_enabled = True
        engine.cancel_requested = False
        engine.configured_external_ai_fallback_providers = lambda: ["google"]

        calls = []

        def fake_generate(provider, *args, **kwargs):
            calls.append(provider)
            return "Resposta direta e suficiente."

        engine._generate_solution_with_provider = fake_generate

        response = engine.generate_solution("pergunta simples")

        self.assertEqual(["openai"], calls)
        self.assertEqual("Resposta direta e suficiente.", response)

    def test_valid_answer_that_mentions_quota_does_not_trigger_fallback(self):
        engine = UniversalEngine.__new__(UniversalEngine)
        engine.provider = "openai"
        engine.external_ai_fallback_enabled = True

        response = (
            "A quota mensal do plano pode ser modelada como uma regra de negocio "
            "e exibida no painel financeiro."
        )

        self.assertFalse(engine.should_try_external_ai_fallback(response))


if __name__ == "__main__":
    unittest.main()

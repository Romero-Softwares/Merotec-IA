from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.ai_profiles import (
    ensure_ai_profiles,
    is_restorable_web_chat_url,
    remember_web_chat_session,
    web_chat_url_for_workspace,
    workspace_session_key,
)
from modules.ui_web_chat_bridge import InternalBrowserWebChatBridge
from modules.web_chat_bridge import WebChatBridge
from modules.agent_actions import AgentActionsMixin
from modules.engine import UniversalEngine


class _RunningProcess:
    def poll(self):
        return None


class _FakeApp:
    def __init__(self, workspace: Path, target: str):
        self.current_workspace = str(workspace)
        self.internal_browser_process = _RunningProcess()
        self.internal_browser_url = "https://gemini.google.com/"
        self.target = target
        self.opened = []

    def web_chat_target_for_workspace(self, _workspace):
        return self.target

    def after(self, _delay, callback):
        callback()

    def open_internal_browser(self, url, source=""):
        self.opened.append((url, source))
        self.internal_browser_url = url


class _FakeProcessWebChatBridge(WebChatBridge):
    def __init__(self, *, settings_path: Path, profile: dict, workspace_path: Path):
        super().__init__(
            runtime_path=Path("browser_runtime.py"),
            settings_path=settings_path,
            profile=profile,
            workspace_path=workspace_path,
        )
        self.requests = []

    def request(self, action: str, payload: dict | None = None, timeout: float = 60) -> dict:
        payload = dict(payload or {})
        self.requests.append((action, payload, timeout))
        return {"url": payload.get("url"), "title": "Chat"}


class ProfileAndSessionTests(unittest.TestCase):
    def test_sessions_are_isolated_by_chat_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            settings = ensure_ai_profiles({})
            settings["ai_profiles"]["web_chat"]["web_chat_url"] = "https://gemini.google.com/"
            remember_web_chat_session(
                settings,
                workspace,
                "web_chat",
                "https://gemini.google.com/app/conversa-projeto",
                entry_url="https://gemini.google.com/",
            )
            self.assertEqual(
                web_chat_url_for_workspace(settings, workspace),
                "https://gemini.google.com/app/conversa-projeto",
            )
            settings["ai_profiles"]["web_chat"]["web_chat_url"] = "https://chatgpt.com/"
            self.assertEqual(
                web_chat_url_for_workspace(settings, workspace),
                "https://chatgpt.com/",
            )

    def test_bridge_uses_origin_aware_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            target = "https://gemini.google.com/app/conversa-projeto"
            app = _FakeApp(workspace, target)
            bridge = InternalBrowserWebChatBridge(
                app,
                {"web_chat_url": "https://gemini.google.com/", "web_chat_restore_project_session": True},
            )
            bridge.ensure_workspace_session(workspace)
            self.assertEqual(
                bridge.current_session_key,
                workspace_session_key(workspace, "web_chat", "https://gemini.google.com/"),
            )
            self.assertEqual(app.opened, [(target, "Chat Web")])

    def test_restore_disabled_uses_entry_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            app = _FakeApp(workspace, "https://gemini.google.com/app/conversa-antiga")
            bridge = InternalBrowserWebChatBridge(
                app,
                {"web_chat_url": "https://gemini.google.com/", "web_chat_restore_project_session": False},
            )
            target = bridge.ensure_workspace_session(workspace)
            self.assertEqual(target, "https://gemini.google.com/")
            self.assertEqual(app.opened, [])

    def test_process_bridge_restore_disabled_uses_entry_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "Projeto"
            settings_path = root / "settings.json"
            settings = ensure_ai_profiles({})
            remember_web_chat_session(
                settings,
                workspace,
                "web_chat",
                "https://chatgpt.com/c/conversa-antiga",
                entry_url="https://chatgpt.com/",
            )
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            bridge = _FakeProcessWebChatBridge(
                settings_path=settings_path,
                profile={
                    "web_chat_url": "https://chatgpt.com/",
                    "web_chat_restore_project_session": False,
                },
                workspace_path=workspace,
            )

            target = bridge.ensure_workspace_session(workspace)

            self.assertEqual(target, "https://chatgpt.com/")
            self.assertEqual(bridge.requests[0][1]["url"], "https://chatgpt.com/")
            self.assertFalse(bridge.requests[0][1]["restore_session"])

    def test_process_bridge_restore_enabled_uses_saved_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "Projeto"
            settings_path = root / "settings.json"
            settings = ensure_ai_profiles({})
            saved_url = "https://chatgpt.com/c/conversa-salva"
            remember_web_chat_session(
                settings,
                workspace,
                "web_chat",
                saved_url,
                entry_url="https://chatgpt.com/",
            )
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            bridge = _FakeProcessWebChatBridge(
                settings_path=settings_path,
                profile={
                    "web_chat_url": "https://chatgpt.com/",
                    "web_chat_restore_project_session": True,
                },
                workspace_path=workspace,
            )

            target = bridge.ensure_workspace_session(workspace)

            self.assertEqual(target, saved_url)
            self.assertEqual(bridge.requests[0][1]["url"], saved_url)
            self.assertTrue(bridge.requests[0][1]["restore_session"])

    def test_saved_chat_session_rejects_unstable_internal_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            settings = ensure_ai_profiles({})
            settings["ai_profiles"]["web_chat"]["web_chat_url"] = "https://chatgpt.com/"

            remember_web_chat_session(
                settings,
                workspace,
                "web_chat",
                "https://chatgpt.com/backend-api/conversation",
                entry_url="https://chatgpt.com/",
            )

            self.assertFalse(
                is_restorable_web_chat_url(
                    "https://chatgpt.com/backend-api/conversation",
                    "https://chatgpt.com/",
                )
            )
            self.assertEqual(web_chat_url_for_workspace(settings, workspace), "https://chatgpt.com/")

    def test_saved_chat_session_rejects_oversized_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            settings = ensure_ai_profiles({})
            settings["ai_profiles"]["web_chat"]["web_chat_url"] = "https://chatgpt.com/"
            oversized = "https://chatgpt.com/c/" + ("x" * 2100)

            remember_web_chat_session(
                settings,
                workspace,
                "web_chat",
                oversized,
                entry_url="https://chatgpt.com/",
            )

            self.assertEqual(web_chat_url_for_workspace(settings, workspace), "https://chatgpt.com/")

    def test_bridge_compacts_message_with_provider_margin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Projeto"
            app = _FakeApp(workspace, "https://chatgpt.com/")
            bridge = InternalBrowserWebChatBridge(
                app,
                {"web_chat_url": "https://chatgpt.com/", "web_chat_message_chars": 10000},
            )

            compacted, changed = bridge._compact_single_message("x" * 12000, bridge._message_limit())

            self.assertTrue(changed)
            self.assertLessEqual(len(compacted), 8800)
            self.assertIn("Contexto intermediario omitido", compacted)


class BrowserRuntimeTests(unittest.TestCase):
    def test_visual_runtime_arguments_reach_run(self):
        runtime = importlib.import_module("modules.browser_runtime")
        with patch.object(runtime, "run", return_value=23) as run:
            with patch.object(
                sys,
                "argv",
                [
                    "browser_runtime.py",
                    "--url", "http://127.0.0.1:8000",
                    "--title", "Teste Visual",
                    "--storage-scope", "visual-tests",
                ],
            ):
                self.assertEqual(runtime.main(), 23)
        run.assert_called_once_with(
            "http://127.0.0.1:8000",
            title="Teste Visual",
            storage_scope="visual-tests",
        )

    def test_runtime_contains_attachment_and_artifact_pipeline(self):
        source = (ROOT / "modules" / "browser_runtime.py").read_text(encoding="utf-8")
        self.assertIn("DataTransfer", source)
        self.assertIn("filesSetter", source)
        self.assertIn("send_wait_seconds", source)
        self.assertIn('"attachment_error"', source)
        self.assertIn('"attachment_count"', source)
        self.assertIn('"artifacts"', source)

    def test_visual_delivery_never_silently_claims_attachment_success(self):
        engine_source = (ROOT / "modules" / "engine.py").read_text(encoding="utf-8")
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("latest_web_chat_delivery", engine_source)
        self.assertIn("web_chat_visual_delivery_problem", main_source)
        self.assertIn("retry_web_chat_visual_delivery", main_source)
        self.assertIn("não confirmou o recebimento do print", main_source)

    def test_web_chat_attachment_payload_has_stable_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "print.png"
            image_path.write_bytes(b"fake-png-for-transport")
            engine = UniversalEngine.__new__(UniversalEngine)
            engine.web_chat_auto_attach_media = True

            payload = engine._web_chat_attachment_payload(image_path)

            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["name"], "print.png")
            self.assertEqual(len(payload[0]["sha256"]), 64)
            self.assertTrue(payload[0]["data_base64"])

    def test_runtime_deduplicates_visual_attachment_paths(self):
        source = (ROOT / "modules" / "browser_runtime.py").read_text(encoding="utf-8")
        self.assertIn("seen_attachments = set()", source)
        self.assertIn('item.get("sha256")', source)
        self.assertIn("attachmentCount < transfer.files.length", source)
        self.assertNotIn("if (transfer.files.length) {{\n                          for (const target of dropTargets)", source)


class StartupOrderTests(unittest.TestCase):
    def test_all_application_patches_are_registered_before_mainloop(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        guard = source.rfind('if __name__ == "__main__":')
        self.assertGreater(guard, source.find("MEROTEC_CONFIGURED_PROVIDER_LOCK_V1"))
        self.assertGreater(guard, source.find("UniversalApp.local_llm_fallback_reply"))
        self.assertTrue(source.rstrip().endswith("app.mainloop()"))



class _ActionHarness(AgentActionsMixin):
    def __init__(self):
        self.settings = {}
        self.active_ai_objective = ""
        self.read_calls = []

    def is_task_cancelled(self, _task_id=None):
        return False

    def autonomous_unrestricted_mode_enabled(self):
        return False

    def claims_concrete_result_without_real_action(self, *_args, **_kwargs):
        return False

    def task_has_real_action(self, _task_id=None):
        return False

    def should_use_project_map_instead_of_mass_read(self, *_args, **_kwargs):
        return False

    def should_block_passive_ai_action(self, *_args, **_kwargs):
        return False

    def _agent_read_many(self, raw_paths, **kwargs):
        self.read_calls.append((list(raw_paths), kwargs))


class WebChatActionProtocolTests(unittest.TestCase):
    def test_gemini_bracket_then_path_is_executed(self):
        app = _ActionHarness()
        app.parse_and_execute_agent_actions("[READ] main.py", task_objective="corrigir o projeto")
        self.assertEqual(app.read_calls[0][0], ["main.py"])

    def test_parser_accepts_canonical_and_common_single_line_variants(self):
        parser = AgentActionsMixin()
        self.assertEqual(list(parser.iter_agent_action_lines("[READ: main.py]")), [("READ", "main.py")])
        self.assertEqual(list(parser.iter_agent_action_lines("[READ] main.py")), [("READ", "main.py")])
        self.assertEqual(list(parser.iter_agent_action_lines("READ main.py")), [("READ", "main.py")])
        self.assertEqual(list(parser.iter_agent_action_lines("READ: main.py")), [("READ", "main.py")])

    def test_parser_does_not_turn_explanation_into_action(self):
        parser = AgentActionsMixin()
        self.assertEqual(
            list(parser.iter_agent_action_lines("O próximo passo é READ main.py antes de editar.")),
            [],
        )

    def test_web_chat_prompt_demands_the_canonical_action_format(self):
        instruction = UniversalEngine._web_chat_conversation_instruction(None)
        self.assertIn("[READ: main.py]", instruction)
        self.assertIn("[REPLACE: caminho/arquivo.ext]", instruction)
        self.assertIn("prefira REPLACE pequeno", instruction)
        self.assertIn("WRITE completo apenas para arquivo novo", instruction)
        self.assertIn("Não use [READ] arquivo", instruction)


    def test_web_chat_prompt_prioritizes_new_user_request(self):
        instruction = UniversalEngine._web_chat_conversation_instruction(None)
        self.assertIn("A TAREFA ATUAL tem prioridade", instruction)
        self.assertIn("pare o ciclo antigo", instruction)
        self.assertIn("claramente de continuidade", instruction)

    def test_codex_prompt_prioritizes_latest_user_request(self):
        engine_source = (ROOT / "modules" / "engine.py").read_text(encoding="utf-8")
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("PRIORIDADE DO PEDIDO ATUAL", engine_source)
        self.assertIn("mensagem mais recente do usuario tem prioridade", main_source)
        self.assertIn("substitua a missao anterior pelo pedido atual", main_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

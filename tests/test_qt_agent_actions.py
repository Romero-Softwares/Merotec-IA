from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modules.code_transport import validate_file
from modules.qt_agent_actions import QtAgentActions


class QtAgentActionsTests(unittest.TestCase):
    def test_read_action_keeps_observation_for_agent_continuation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app.py").write_text("print('olá')\n", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[READ: app.py]")

            self.assertEqual(actions.last_action_count, 1)
            self.assertEqual(actions.last_followup_required, 1)
            self.assertTrue(actions.last_observations)
            self.assertIn("print('olá')", actions.last_observations[0])

    def test_execute_does_not_trigger_early_agent_continuation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            commands = []
            messages = []
            actions = QtAgentActions(
                temp_dir,
                lambda author, text: messages.append((author, text)),
                commands.append,
                lambda _paths: None,
            )

            command = "python -c \"items=[1, 2]; print(items[0])\""
            actions.apply(f"[EXECUTE: {command}]")

            self.assertEqual(actions.last_action_count, 1)
            self.assertEqual(actions.last_followup_required, 0)
            self.assertTrue(actions.last_command_requested)
            self.assertEqual(commands, [command])
            self.assertTrue(any("EXECUTE encaminhado ao terminal" in text for _author, text in messages))

    def test_human_test_is_forwarded_to_the_pyside_visual_runner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            requests = []
            actions = QtAgentActions(
                temp_dir,
                lambda *_args: None,
                lambda _command: None,
                lambda _paths: None,
                on_human_test=requests.append,
            )

            actions.apply("[HUMAN_TEST: auto]")

            self.assertEqual(requests, ["auto"])
            self.assertTrue(actions.last_visual_test_requested)
            self.assertEqual(actions.last_action_count, 1)

    def test_browser_action_is_forwarded_one_step_at_a_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            requests = []
            actions = QtAgentActions(
                temp_dir,
                lambda *_args: None,
                lambda _command: None,
                lambda _paths: None,
                on_browser_action=lambda action, payload: requests.append((action, payload)),
            )

            actions.apply(
                "[OPEN_URL: http://127.0.0.1:8765/]\n"
                "[BROWSER_INSPECT: pagina]"
            )

            self.assertEqual(requests, [("open", {"url": "http://127.0.0.1:8765/"})])
            self.assertTrue(actions.last_browser_action_requested)
            self.assertEqual(actions.last_action_count, 1)

    def test_browser_type_requires_element_and_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            messages = []
            actions = QtAgentActions(
                temp_dir,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[BROWSER_TYPE: e1]")

            self.assertFalse(actions.last_browser_action_requested)
            self.assertTrue(any("BROWSER_TYPE precisa" in text for _author, text in messages))

    def test_write_unwraps_markdown_code_fence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply(
                "[WRITE: apresentacao.html]\n"
                "```html\n<!DOCTYPE html>\n<html><body>Pronto</body></html>\n```\n"
                "[/WRITE]"
            )

            self.assertEqual(
                (workspace / "apresentacao.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html>\n<html><body>Pronto</body></html>\n",
            )

    def test_write_discards_browser_prose_outside_code_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply(
                "[WRITE: pagina.html]\nAqui está o arquivo:\n```html\n<!DOCTYPE html><html></html>\n```\nPronto.\n[/WRITE]"
            )

            self.assertEqual((workspace / "pagina.html").read_text(encoding="utf-8"), "<!DOCTYPE html><html></html>\n")

    def test_write_reassembles_source_split_across_browser_code_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply(
                "[WRITE: pagina.html]\n```html\n<!DOCTYPE html>\n<html><head><style>\n```\n"
                "```\nbody { color: white; }\n</style></head>\n```\n"
                "```\n<body>Pronta</body></html>\n```\n[/WRITE]"
            )

            self.assertEqual(
                (workspace / "pagina.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html>\n<html><head><style>\nbody { color: white; }\n</style></head>\n<body>Pronta</body></html>\n",
            )

    def test_write_preserves_text_between_split_browser_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply(
                "[WRITE: pagina.html]\n<!DOCTYPE html><html><style>\n```\nbody { color: white; }\n```\n"
                ".card { padding: 1rem; }\n```\n</style><body>Pronta</body></html>\n```\n[/WRITE]"
            )

            source = (workspace / "pagina.html").read_text(encoding="utf-8")
            self.assertNotIn("```", source)
            self.assertIn(".card { padding: 1rem; }", source)

    def test_validate_rejects_markdown_fence_left_in_html_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "pagina.html"
            target.write_text("<!DOCTYPE html>\n```\n<html></html>", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace, lambda author, text: messages.append((author, text)), lambda _command: None, lambda _paths: None
            )

            actions.apply("[VALIDATE: pagina.html]")

            self.assertFalse(any("TransportArtifact" in text for _author, text in messages))
            self.assertNotIn("```", target.read_text(encoding="utf-8"))
            self.assertIn(target.resolve(), actions.last_validated_paths)

    def test_write_parts_only_commit_after_all_parts_arrive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            staging = {}
            actions = QtAgentActions(
                workspace, lambda *_args: None, lambda _command: None, lambda _paths: None, write_staging=staging
            )

            actions.apply("[WRITE_PART: pagina.html | 1/2]\n<!DOCTYPE html><html><body>\n[/WRITE_PART]")
            self.assertFalse((workspace / "pagina.html").exists())
            actions.apply("[WRITE_PART: pagina.html | 2/2]\nPronta</body></html>\n[/WRITE_PART]")

            self.assertEqual(
                (workspace / "pagina.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html><html><body>\nPronta</body></html>\n",
            )

    def test_write_removes_html_label_joined_by_web_chat_dom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply("[WRITE: apresentacao.html]\n```\nHTML<!DOCTYPE html><html></html>\n```\n[/WRITE]")

            self.assertEqual(
                (workspace / "apresentacao.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html><html></html>\n",
            )

    def test_write_preserves_arbitrary_text_file_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply("[WRITE: config/tema.custom]\n```\nchave=valor\n# texto livre\n```\n[/WRITE]")

            self.assertEqual(
                (workspace / "config" / "tema.custom").read_text(encoding="utf-8"),
                "chave=valor\n# texto livre\n",
            )

    def test_replace_unwraps_fenced_json_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "package.json"
            target.write_text('{"name": "antes"}\n', encoding="utf-8")
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply(
                "[REPLACE: package.json]\n[OLD]\n```json\n{\"name\": \"antes\"}\n```\n[/OLD]\n"
                "[NEW]\n```\nJSON{\"name\": \"depois\"}\n```\n[/NEW]\n[/REPLACE]"
            )

            self.assertEqual(target.read_text(encoding="utf-8"), '{"name": "depois"}\n')

    def test_insert_before_adds_html_without_replace_old_fragment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "pagina.html"
            target.write_text("<!DOCTYPE html><html><body><main>Base</main></body></html>\n", encoding="utf-8")
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply("[INSERT_BEFORE: pagina.html | </body>]\n<section>Detalhe novo</section>\n[/INSERT_BEFORE]")

            source = target.read_text(encoding="utf-8")
            self.assertIn("<section>Detalhe novo</section>\n</body>", source)
            self.assertIsNone(validate_file(target))

    def test_validate_reports_structured_file_result_to_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "pagina.html").write_text("<html><body>incompleto", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[VALIDATE: pagina.html]")

            self.assertEqual(actions.last_followup_required, 1)
            self.assertEqual(actions.last_validated_paths, [])
            self.assertTrue(any(author == "Erro" and "HTMLStructureError" in text for author, text in messages))

    def test_validate_records_successful_file_for_completion_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "pagina.html"
            target.write_text("<!DOCTYPE html><html><body>ok</body></html>", encoding="utf-8")
            actions = QtAgentActions(workspace, lambda *_args: None, lambda _command: None, lambda _paths: None)

            actions.apply("[VALIDATE: pagina.html]")

            self.assertEqual(actions.last_validated_paths, [target.resolve()])

    def test_rejected_replace_returns_current_small_file_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "apresentacao.html"
            target.write_text("<html><body>truncado", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[REPLACE: apresentacao.html]\n[OLD]ausente[/OLD]\n[NEW]novo[/NEW]\n[/REPLACE]")

            recovery = next(text for author, text in messages if author == "Recuperação da IDE")
            self.assertIn("[WRITE: apresentacao.html]", recovery)
            self.assertIn("<html><body>truncado", recovery)

    @unittest.skip("Substituído por teste com comparação Unicode independente da codificação do arquivo.")
    def test_rejected_replace_returns_valid_file_for_incremental_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "pagina.html"
            target.write_text("<!DOCTYPE html><html><body>ok</body></html>", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[REPLACE: pagina.html]\n[OLD]ausente[/OLD]\n[NEW]novo[/NEW]\n[/REPLACE]")

            recovery = next(text for author, text in messages if author == "RecuperaÃ§Ã£o da IDE")
            self.assertIn("[REPLACE: pagina.html]", recovery)
            self.assertNotIn("[WRITE:", recovery)
            self.assertIn("<!DOCTYPE html>", recovery)

    def test_rejected_replace_returns_valid_file_for_incremental_repair_utf8_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "pagina.html"
            target.write_text("<!DOCTYPE html><html><body>ok</body></html>", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[REPLACE: pagina.html]\n[OLD]ausente[/OLD]\n[NEW]novo[/NEW]\n[/REPLACE]")

            recovery = next(text for author, text in messages if author.startswith("Recuper"))
            self.assertIn("[REPLACE: pagina.html]", recovery)
            self.assertNotIn("[WRITE:", recovery)
            self.assertIn("<!DOCTYPE html>", recovery)

    def test_explicit_overwrite_recovery_requests_full_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "pagina.html").write_text("<!DOCTYPE html><html><body>ok</body></html>", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
                task_objective="Sobrescreva o arquivo pagina.html com uma nova página gerada.",
            )

            actions.apply("[REPLACE: pagina.html]\n[OLD]ausente[/OLD]\n[NEW]novo[/NEW]\n[/REPLACE]")

            recovery = next(text for author, text in messages if author.startswith("Recuper"))
            self.assertIn("[WRITE: pagina.html]", recovery)
            self.assertIn("não use REPLACE", recovery)

    def test_write_rejects_invalid_html_without_overwriting_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "apresentacao.html"
            target.write_text("<!DOCTYPE html>\n<html></html>\n", encoding="utf-8")
            messages = []
            actions = QtAgentActions(
                workspace,
                lambda author, text: messages.append((author, text)),
                lambda _command: None,
                lambda _paths: None,
            )

            actions.apply("[WRITE: apresentacao.html]\n```html\n<html><body>incompleto\n```\n[/WRITE]")

            self.assertEqual(target.read_text(encoding="utf-8"), "<!DOCTYPE html>\n<html></html>\n")
            self.assertTrue(any(author == "Erro" and "Conteúdo inválido" in text for author, text in messages))


if __name__ == "__main__":
    unittest.main()

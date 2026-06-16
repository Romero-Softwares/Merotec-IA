import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES
from modules.workspace_intelligence import WorkspaceIntelligenceMixin


class DummyWorkspace(WorkspaceIntelligenceMixin):
    def __init__(self):
        self.current_workspace = str(PROJECT_ROOT)
        self.system_ai_dir = PROJECT_ROOT / ".test_merotec_system_ai"

    def iter_workspace_files(self, limit=500):
        workspace = Path(self.current_workspace)
        count = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [name for name in sorted(dirs) if name not in IGNORED_DIRS and not name.startswith(".")]
            root_path = Path(root)
            for filename in sorted(files):
                path = root_path / filename
                if filename.startswith(".") or path.suffix.lower() in IGNORED_SUFFIXES:
                    continue
                try:
                    rel = path.relative_to(workspace)
                except ValueError:
                    continue
                yield path, rel
                count += 1
                if count >= limit:
                    return


class AutonomousDiscoveryTriggersTest(unittest.TestCase):
    def setUp(self):
        self.app = DummyWorkspace()

    def test_discovery_mission_returns_three_trigger_categories_without_question(self):
        command = (
            "a meta e retirar completamente o ser humano da etapa de interacao e descoberta "
            "com testes de estresse autonomos; existem exatamente tres categorias de gatilhos"
        )

        reply = self.app.local_autonomous_task(command, self.app.normalize_plain_text(command))

        self.assertIsNotNone(reply)
        self.assertIn("Tres categorias de gatilho", reply)
        self.assertIn("Gatilho de intencao", reply)
        self.assertIn("Gatilho de evidencia", reply)
        self.assertIn("Gatilho de restricao", reply)
        self.assertIn("Acao autonoma escolhida", reply)
        self.assertNotIn("?", reply)
        self.assertEqual(3, len(self.app.autonomous_discovery_trigger_categories()))

    def test_trigger_classifier_prefers_strongest_signal(self):
        cases = [
            ("meta do usuario com requisito de produto", "intencao"),
            ("erro de build apareceu no teste de estresse", "evidencia"),
            ("comando bloqueado por permissao de administrador e UAC", "restricao"),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    expected,
                    self.app.classify_autonomous_discovery_trigger(text),
                )

    def test_local_training_subnet_request_exports_redacted_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "README.md").write_text("# Projeto local\n", encoding="utf-8")
            (workspace / "main.py").write_text(
                "print('ok')\nOPENAI_API_KEY = 'nao_deve_entrar_no_corpus'\n",
                encoding="utf-8",
            )
            system_dir = workspace.parent / "merotec-system-ai"
            self.app.current_workspace = str(workspace)
            self.app.system_ai_dir = system_dir
            command = (
                "continue a varredura da sub rede para um modelo treinavel local "
                "quando a ia conectada ficar sem cota"
            )

            reply = self.app.local_autonomous_task(command, self.app.normalize_plain_text(command))

            self.assertIsNotNone(reply)
            self.assertIn("Artefatos locais atualizados na rede do sistema", reply)
            output_dir = self.app.local_training_output_dir(workspace)
            corpus = output_dir / "training_corpus.jsonl"
            subnet = output_dir / "memory_subnet.json"
            self.assertTrue(corpus.exists())
            self.assertTrue(subnet.exists())
            self.assertFalse((workspace / ".merotec_local_ai").exists())
            corpus_text = corpus.read_text(encoding="utf-8")
            self.assertIn("[REDACTED_SECRET_LINE]", corpus_text)
            self.assertNotIn("nao_deve_entrar_no_corpus", corpus_text)
            status = self.app.local_training_subnet_status()
            self.assertIn("Sub-rede local: pronta", status)
            self.assertIn("Pasta da rede do sistema:", status)
            self.assertIn("Projeto alimentador:", status)
            self.assertIn("Registros no corpus", status)
            self.assertIn("nao e, sozinha, um LLM", status)

            context = self.app.build_local_training_context("continue a modificacao da rede")

            self.assertIn("CONTEXTO DA SUB-REDE LOCAL DO SISTEMA", context)
            self.assertIn("Registros mais relevantes", context)
            self.assertIn("Sub-rede local: pronta", context)
            self.assertIn("main.py", context)
            self.assertIn("[REDACTED_SECRET_LINE]", context)
            self.assertNotIn("nao_deve_entrar_no_corpus", context)

    def test_local_training_subnet_status_reports_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "projeto"
            workspace.mkdir()
            system_dir = Path(temp_dir) / "merotec-system-ai"
            self.app.current_workspace = str(workspace)
            self.app.system_ai_dir = system_dir

            status = self.app.local_training_subnet_status()

            self.assertIn("Sub-rede local: ainda nao preparada na pasta do sistema", status)
            self.assertIn("Faltando:", status)
            self.assertIn("workspaces/", status)
            self.assertIn("memory_subnet.json", status)
            self.assertIn("training_corpus.jsonl", status)
            self.assertIn("README.md", status)
            self.assertNotIn("Faltando: README.md", status)

    def test_local_llm_reply_uses_system_corpus_without_leaking_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "projeto"
            workspace.mkdir()
            (workspace / "README.md").write_text("# Projeto local\n", encoding="utf-8")
            (workspace / "main.py").write_text(
                "print('ok')\nOPENAI_API_KEY = 'nao_deve_entrar_no_corpus'\n",
                encoding="utf-8",
            )
            self.app.current_workspace = str(workspace)
            self.app.system_ai_dir = Path(temp_dir) / "merotec-system-ai"
            self.app.export_local_training_subnet_artifacts()

            reply = self.app.local_llm_reply("transformar a sub-rede em uma LLM local usando main.py")

            self.assertIn("LLM Local (RAG offline)", reply)
            self.assertIn("fallback local estilo LLM/RAG", reply)
            self.assertIn("main.py", reply)
            self.assertIn("[REDACTED_SECRET_LINE]", reply)
            self.assertNotIn("nao_deve_entrar_no_corpus", reply)

    def test_local_llm_fallback_answers_when_external_model_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "projeto"
            workspace.mkdir()
            (workspace / "README.md").write_text("# Projeto local\n", encoding="utf-8")
            self.app.current_workspace = str(workspace)
            self.app.system_ai_dir = Path(temp_dir) / "merotec-system-ai"
            self.app.export_local_training_subnet_artifacts()

            reply = self.app.local_llm_fallback_reply(
                "continue a tarefa de transformar a rede em uma LLM",
                "Codex esta com alta demanda no momento.",
            )

            self.assertIsNotNone(reply)
            self.assertIn("O modelo externo falhou", reply)
            self.assertIn("fallback local estilo LLM/RAG", reply)


if __name__ == "__main__":
    unittest.main()

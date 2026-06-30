import json
import hashlib
import os
import re
import subprocess
import sys
import threading
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES, MEROTEC_SYSTEM_AI_DIR
from modules.memory import MemorySubnet


class WorkspaceIntelligenceMixin:
    def model_directed_autonomy_enabled(self):
        settings = getattr(self, "settings", None)
        return isinstance(settings, dict) and bool(settings.get("autonomous_unrestricted_mode", False))

    def local_quick_reply(self, command, image_path=None):
        if image_path:
            return None

        normalized = self.normalize_plain_text(command)
        undo_reply = self.local_undo_reply(normalized)
        if undo_reply:
            return undo_reply

        calculation_reply = self.local_calculation_reply(command, normalized)
        if calculation_reply:
            return calculation_reply

        if not self.model_directed_autonomy_enabled():
            capability_reply = self.local_capability_question_reply(command, normalized)
            if capability_reply:
                return capability_reply

        if self.is_local_llm_request(normalized):
            return self.local_llm_reply(command)

        greetings = {
            "ola",
            "oi",
            "opa",
            "e ai",
            "bom dia",
            "boa tarde",
            "boa noite",
        }
        if normalized in greetings:
            return "Ola! Estou pronto. Me diga o que voce quer construir, corrigir ou analisar."

        return None

    def local_capability_question_reply(self, command, normalized=None):
        normalized = normalized or self.normalize_plain_text(command)
        if not self.is_capability_question(command, normalized):
            return None

        words = set(re.findall(r"[a-z0-9_]+", normalized or ""))
        deploy_terms = {"deploy", "publicar", "subir", "hospedar", "github", "repositorio", "repository"}
        run_terms = {"rodar", "rode", "executar", "execute", "testar", "teste", "validar", "valide"}
        edit_terms = {"corrigir", "corrija", "consertar", "conserte", "alterar", "altere", "editar", "edite", "implementar", "implemente"}

        if words & deploy_terms:
            return (
                "Sim, consigo ajudar com o deploy, mas primeiro preciso responder e alinhar o alvo. "
                "Para um deploy real eu preciso saber o destino, por exemplo GitHub Pages, Vercel, Netlify, servidor proprio ou outro ambiente, "
                "e tambem preciso das credenciais/permissoes quando forem necessarias. "
                "Quando voce quiser que eu execute de fato, envie um pedido direto como: `faca o deploy para GitHub Pages` e informe o repositorio/destino."
            )

        if words & run_terms:
            return (
                "Sim, consigo executar validacoes ou comandos do projeto quando voce pedir de forma direta. "
                "Como isso esta em formato de pergunta, vou responder antes de agir: diga qual comando, teste ou app devo rodar, "
                "ou envie `rode os testes` para eu iniciar a execucao."
            )

        if words & edit_terms:
            return (
                "Sim, consigo alterar o projeto, corrigir erros e implementar melhorias. "
                "Como voce perguntou sobre capacidade, nao vou mexer nos arquivos agora. "
                "Para executar, mande o pedido direto com o que deve mudar."
            )

        return (
            "Sim, consigo ajudar com isso. Como a mensagem veio como pergunta, respondi sem iniciar execucao automatica. "
            "Quando quiser acao real na IDE, envie o pedido em forma de tarefa direta."
        )

    def is_capability_question(self, command, normalized=None):
        raw = str(command or "").strip()
        normalized = normalized or self.normalize_plain_text(raw)
        if not normalized:
            return False

        starters = (
            "vc consegue",
            "voce consegue",
            "consegue",
            "conseguiria",
            "vc pode",
            "voce pode",
            "poderia",
            "e possivel",
            "eh possivel",
            "tem como",
            "da para",
            "da pra",
        )
        has_capability_marker = normalized.startswith(starters) or any(f" {marker} " in f" {normalized} " for marker in starters)
        if not has_capability_marker:
            return False
        return "?" in raw or normalized.startswith(("vc consegue", "voce consegue", "consegue", "conseguiria"))

    def is_answer_only_question(self, command, normalized=None):
        raw = str(command or "").strip()
        normalized = normalized or self.normalize_plain_text(raw)
        if not normalized:
            return False
        if self.is_capability_question(raw, normalized):
            return True

        question_starters = (
            "como ",
            "o que ",
            "oque ",
            "qual ",
            "quais ",
            "quando ",
            "onde ",
            "por que ",
            "porque ",
            "explique ",
            "me explique ",
        )
        if not ("?" in raw or normalized.startswith(question_starters)):
            return False

        words = set(re.findall(r"[a-z0-9_]+", normalized))
        direct_action_terms = {
            "adicione",
            "altere",
            "apague",
            "aplique",
            "conserte",
            "corrija",
            "crie",
            "edite",
            "execute",
            "faca",
            "fazer",
            "implemente",
            "remova",
            "rode",
            "suba",
            "teste",
        }
        if words & direct_action_terms and not normalized.startswith(question_starters):
            return False
        return True

    def local_undo_reply(self, normalized):
        undo_terms = {"desfazer", "desfaca", "reverter", "reverta", "voltar", "restaurar", "restaure", "recuperar", "recupere"}
        words = set(re.findall(r"[a-z0-9_]+", normalized or ""))
        if not words & undo_terms:
            return None
        if not any(term in normalized for term in ("alteracao", "mudanca", "arquivo", "ultima", "nuvem", "nuvens", "remocao", "removeu", "desfazer", "reverter", "restaurar")):
            return None
        reply = self.undo_last_change()
        if "Nao encontrei alteracao recente" in reply:
            fallback = self.restore_main_backup()
            if fallback:
                return fallback
        return reply

    def local_calculation_reply(self, command, normalized=None):
        normalized = normalized or self.normalize_plain_text(command)
        if not self.looks_like_simple_calculation(normalized):
            return None

        expression = self.extract_calculation_expression(normalized)
        if not expression:
            return None

        try:
            result = self.evaluate_decimal_expression(expression)
        except (ValueError, InvalidOperation, DivisionByZero, ZeroDivisionError):
            return None

        if self.is_currency_question(normalized):
            formatted = self.format_brl(result)
            return f"12 x 4,70 = {formatted}" if "12" in normalized and "4,70" in normalized else f"Resultado: {formatted}"

        return f"Resultado: {self.format_decimal_result(result)}"

    def looks_like_simple_calculation(self, text):
        if not re.search(r"\d", text):
            return False
        intent_terms = {
            "quanto",
            "calcule",
            "calcula",
            "calcular",
            "resultado",
            "conta",
        }
        if any(term in text for term in intent_terms):
            return True
        return bool(re.search(r"\d+\s*(?:x|\*|/|\+|-|,|\.)\s*\d+", text))

    def extract_calculation_expression(self, text):
        expr = f" {text} "
        replacements = [
            (r"\bdividido\s+por\b", "/"),
            (r"\bmultiplicado\s+por\b", "*"),
            (r"\bvezes\b", "*"),
            (r"\bmais\b", "+"),
            (r"\bmenos\b", "-"),
        ]
        for pattern, replacement in replacements:
            expr = re.sub(pattern, replacement, expr)

        expr = re.sub(r"\bquanto\s+(?:e|eh|é)\b", " ", expr)
        expr = re.sub(r"\b(?:calcule|calcula|calcular|resultado|conta|qual|o|de|da|do|em|reais|real)\b", " ", expr)
        expr = expr.replace("r$", " ")
        expr = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
        expr = re.sub(r"(?<=\d),(?=\d)", ".", expr)
        expr = expr.replace("÷", "/").replace("×", "*")

        tokens = re.findall(r"\d+(?:\.\d+)?|[()+\-*/]", expr)
        if not tokens or not any(token in {"+", "-", "*", "/"} for token in tokens):
            return ""
        return " ".join(tokens)

    def evaluate_decimal_expression(self, expression):
        tokens = re.findall(r"\d+(?:\.\d+)?|[()+\-*/]", expression)
        if not tokens:
            raise ValueError("expressao vazia")

        output = []
        operators = []
        precedence = {"+": 1, "-": 1, "*": 2, "/": 2}
        previous = None

        for token in tokens:
            if re.fullmatch(r"\d+(?:\.\d+)?", token):
                output.append(Decimal(token))
                previous = "number"
            elif token in "+-*/":
                if token == "-" and previous in {None, "operator", "("}:
                    output.append(Decimal("0"))
                while operators and operators[-1] in precedence and precedence[operators[-1]] >= precedence[token]:
                    output.append(operators.pop())
                operators.append(token)
                previous = "operator"
            elif token == "(":
                operators.append(token)
                previous = "("
            elif token == ")":
                while operators and operators[-1] != "(":
                    output.append(operators.pop())
                if not operators:
                    raise ValueError("parenteses invalidos")
                operators.pop()
                previous = "number"

        while operators:
            operator = operators.pop()
            if operator in {"(", ")"}:
                raise ValueError("parenteses invalidos")
            output.append(operator)

        stack = []
        for item in output:
            if isinstance(item, Decimal):
                stack.append(item)
                continue
            if len(stack) < 2:
                raise ValueError("operacao invalida")
            right = stack.pop()
            left = stack.pop()
            if item == "+":
                stack.append(left + right)
            elif item == "-":
                stack.append(left - right)
            elif item == "*":
                stack.append(left * right)
            elif item == "/":
                stack.append(left / right)

        if len(stack) != 1:
            raise ValueError("expressao invalida")
        return stack[0]

    def is_currency_question(self, text):
        return "real" in text or "reais" in text or "r$" in text

    def format_brl(self, value):
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        integer, cents = f"{rounded:.2f}".split(".")
        groups = []
        while integer:
            groups.append(integer[-3:])
            integer = integer[:-3]
        return f"R$ {'.'.join(reversed(groups))},{cents}"

    def format_decimal_result(self, value):
        rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()
        text = format(rounded, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text.replace(".", ",")

    def local_autonomous_task(self, command, normalized=None, image_path=None):
        normalized = normalized or self.normalize_plain_text(command)

        if self.is_project_run_request(normalized):
            return self.start_project_run_task(command, normalized)

        if image_path:
            return None

        # No modo irrestrito, pedidos gerais seguem para o modelo configurado.
        # Mantemos o atalho local apenas para a operacao especializada que
        # realmente exporta os artefatos da sub-rede offline.
        if not self.model_directed_autonomy_enabled() or self.is_local_training_subnet_request(normalized):
            discovery_reply = self.local_autonomous_discovery_reply(command, normalized)
            if discovery_reply:
                return discovery_reply

        if self.is_zoom_mobile_verification_request(normalized):
            return self.verify_zoom_mobile_locally(command)

        return None

    def local_autonomous_discovery_reply(self, command, normalized=None):
        normalized = normalized or self.normalize_plain_text(command)
        if not self.is_autonomous_discovery_request(normalized):
            return None

        categories = self.autonomous_discovery_trigger_categories()
        selected_key = self.classify_autonomous_discovery_trigger(normalized)
        selected = categories[selected_key]
        validation_command = self.autonomous_discovery_validation_command()
        project_signal = self.autonomous_discovery_project_signal()
        local_training_artifact = ""
        if self.is_local_training_subnet_request(normalized):
            local_training_artifact = self.export_local_training_subnet_artifacts()
        local_training_section = f"{local_training_artifact}\n\n" if local_training_artifact else ""

        category_lines = []
        for category in categories.values():
            category_lines.append(
                f"- {category['label']}: {category['definition']} "
                f"Acao: {category['action']}"
            )

        return (
            "**Descoberta Autonoma**\n\n"
            "A etapa de descoberta fica sem pergunta inicial ao usuario: a IDE classifica o gatilho, "
            "usa o mapa local do workspace e escolhe a proxima acao segura.\n\n"
            "Tres categorias de gatilho:\n"
            + "\n".join(category_lines)
            + "\n\n"
            f"Gatilho dominante nesta missao: {selected['label']}.\n"
            f"Acao autonoma escolhida: {selected['action']}\n"
            f"Validacao padrao sugerida: `{validation_command or 'sem comando automatico seguro'}`.\n\n"
            "Sinal atual do projeto:\n"
            f"{project_signal}\n\n"
            f"{local_training_section}"
            "Regra operacional: se o pedido vier incompleto, a IDE nao devolve pergunta aberta; "
            "ela transforma a ambiguidade em uma leitura minima, uma validacao real ou um diagnostico fechado."
        )

    def is_autonomous_discovery_request(self, normalized):
        words = set(re.findall(r"[a-z0-9_]+", normalized or ""))
        autonomy_terms = {
            "autonomo",
            "autonoma",
            "autonomamente",
            "sozinho",
            "sem",
            "retirar",
            "remover",
        }
        discovery_terms = {
            "descoberta",
            "descobrir",
            "gatilho",
            "gatilhos",
        }
        human_terms = {"humano", "humanos", "usuario", "pessoa", "interacao", "interacoes"}
        has_autonomy = bool(words & autonomy_terms) or "sem humano" in normalized or "sem o humano" in normalized
        has_discovery = bool(words & discovery_terms)
        asks_three_triggers = (
            ("tres categorias" in normalized or "3 categorias" in normalized)
            and ("gatilho" in normalized or "gatilhos" in normalized)
        )
        removes_human = bool(words & {"retirar", "remover"}) and bool(words & human_terms)
        return (
            (has_autonomy and has_discovery)
            or asks_three_triggers
            or (removes_human and has_discovery)
            or self.is_local_training_subnet_request(normalized)
        )

    def is_local_training_subnet_request(self, normalized):
        normalized = self.normalize_plain_text(normalized or "")
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        training_terms = {
            "alimentado",
            "alimentar",
            "corpus",
            "dataset",
            "modelo",
            "rag",
            "treinada",
            "treinado",
            "treinamento",
            "treinar",
            "treinavel",
        }
        local_terms = {"cota", "local", "offline", "conectada", "conectado", "quota"}
        subnet_terms = {"subrede", "sub", "rede", "varredura", "varrer"}
        has_training_goal = bool(words & training_terms)
        has_local_reason = bool(words & local_terms) or "sem cota" in normalized or "nao dependa" in normalized
        has_subnet_scan = bool(words & subnet_terms) or "sub rede" in normalized or "sub-rede" in normalized
        return has_training_goal and (has_local_reason or has_subnet_scan)

    def local_training_system_dir(self):
        return Path(getattr(self, "system_ai_dir", MEROTEC_SYSTEM_AI_DIR)).resolve()

    def local_training_workspace_key(self, workspace=None):
        workspace = Path(workspace or self.current_workspace).resolve()
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", workspace.name).strip("._") or "workspace"
        digest = hashlib.sha256(str(workspace).encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{slug}-{digest}"

    def local_training_output_dir(self, workspace=None):
        return self.local_training_system_dir() / "workspaces" / self.local_training_workspace_key(workspace)

    def local_training_corpus_path(self, workspace=None):
        return self.local_training_output_dir(workspace) / "training_corpus.jsonl"

    def local_training_artifact_label(self, path):
        try:
            return path.relative_to(self.local_training_system_dir()).as_posix()
        except ValueError:
            return path.as_posix()

    def export_local_training_subnet_artifacts(self, max_files=180, max_chars_per_file=8000, chunk_chars=1800):
        workspace = Path(self.current_workspace).resolve()
        files = list(self.iter_workspace_files(limit=1400))
        summary = self.local_project_summary()
        recent_changes = self.recent_change_records(limit=20) if hasattr(self, "recent_change_records") else []

        subnet = MemorySubnet(workspace, max_nodes=240)
        subnet.ingest_project_signals(files, summary=summary, recent_changes=recent_changes)

        output_dir = self.local_training_output_dir(workspace)
        output_dir.mkdir(parents=True, exist_ok=True)
        subnet_path = output_dir / "memory_subnet.json"
        corpus_path = output_dir / "training_corpus.jsonl"
        manifest_path = output_dir / "README.md"

        subnet_path.write_text(subnet.export_json(), encoding="utf-8")
        records = self.build_local_training_records(
            files,
            summary,
            subnet,
            recent_changes=recent_changes,
            max_files=max_files,
            max_chars_per_file=max_chars_per_file,
            chunk_chars=chunk_chars,
        )
        with corpus_path.open("w", encoding="utf-8", newline="\n") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

        manifest = (
            "# Merotec Local AI Corpus\n\n"
            "Artefatos gerados pela varredura local do workspace para uso offline/RAG.\n\n"
            f"- Workspace analisado: `{workspace}`\n"
            f"- Chave do projeto: `{self.local_training_workspace_key(workspace)}`\n"
            "- `memory_subnet.json`: grafo leve de sinais do projeto.\n"
            "- `training_corpus.jsonl`: registros JSONL com resumo, nos da sub-rede e trechos textuais redigidos.\n\n"
            "Linhas com possiveis segredos sao redigidas antes de entrar no corpus.\n"
        )
        manifest_path.write_text(manifest, encoding="utf-8")

        return (
            "Artefatos locais atualizados na rede do sistema para modelo/RAG offline:\n"
            f"- {self.local_training_artifact_label(subnet_path)}\n"
            f"- {self.local_training_artifact_label(corpus_path)} ({len(records)} registros)\n"
            f"- {self.local_training_artifact_label(manifest_path)}\n"
            f"\n{self.local_training_subnet_status()}"
        )

    def local_training_subnet_status(self):
        workspace = Path(self.current_workspace).resolve()
        output_dir = self.local_training_output_dir(workspace)
        subnet_path = output_dir / "memory_subnet.json"
        corpus_path = output_dir / "training_corpus.jsonl"
        manifest_path = output_dir / "README.md"

        missing = [
            self.local_training_artifact_label(path)
            for path in (subnet_path, corpus_path, manifest_path)
            if not path.exists()
        ]
        if missing:
            return (
                "Sub-rede local: ainda nao preparada na pasta do sistema.\n"
                f"Faltando: {', '.join(missing)}.\n"
                "Peca uma varredura/treinamento local para gerar o corpus offline sem gravar no diretorio do projeto."
            )

        node_count = 0
        edge_count = 0
        try:
            subnet = json.loads(subnet_path.read_text(encoding="utf-8"))
            node_count = len(subnet.get("nodes") or {})
            edge_count = len(subnet.get("edges") or [])
        except (OSError, json.JSONDecodeError, TypeError):
            pass

        record_count = 0
        try:
            with corpus_path.open("r", encoding="utf-8") as file:
                record_count = sum(1 for line in file if line.strip())
        except OSError:
            pass

        try:
            updated_at = corpus_path.stat().st_mtime
            updated_text = datetime.fromtimestamp(updated_at).strftime("%d/%m/%Y %H:%M")
        except OSError:
            updated_text = "data indisponivel"

        ready = node_count > 0 and record_count > 0
        status = "pronta" if ready else "incompleta"
        return (
            f"Sub-rede local: {status} para memoria/RAG offline.\n"
            f"- Pasta da rede do sistema: {self.local_training_artifact_label(output_dir)}\n"
            f"- Projeto alimentador: {workspace.name}\n"
            f"- Nos de memoria: {node_count}\n"
            f"- Ligacoes: {edge_count}\n"
            f"- Registros no corpus: {record_count}\n"
            f"- Ultima atualizacao: {updated_text}\n"
            "Uso real: ela fornece contexto local e corpus redigido quando o Codex/modelo externo falhar.\n"
            "Limite atual: ela nao e, sozinha, um LLM treinado capaz de gerar respostas novas sem um motor local conectado."
        )

    def ensure_local_training_subnet_ready(self):
        status = self.local_training_subnet_status()
        if "Sub-rede local: pronta" in status:
            return status

        try:
            return self.export_local_training_subnet_artifacts()
        except Exception as exc:
            return (
                f"{status}\n\n"
                "Tentei preparar a sub-rede local automaticamente, mas a geracao falhou.\n"
                f"Erro: {exc}"
            )

    def build_local_training_records(
        self,
        files,
        summary,
        subnet,
        recent_changes=None,
        max_files=180,
        max_chars_per_file=8000,
        chunk_chars=1800,
    ):
        records = [
            {
                "kind": "project_summary",
                "instruction": "Resuma o workspace local para um agente offline.",
                "input": subnet.root_path.name,
                "output": summary,
                "source": "workspace_summary",
            }
        ]

        for node in sorted(subnet.nodes.values(), key=lambda item: item.get("weight", 0), reverse=True):
            records.append(
                {
                    "kind": "memory_node",
                    "instruction": "Use este sinal da sub-rede para orientar a proxima acao local.",
                    "input": node.get("key", ""),
                    "output": node,
                    "source": "memory_subnet",
                }
            )

        for change in recent_changes or []:
            records.append(
                {
                    "kind": "recent_change",
                    "instruction": "Considere esta mudanca recente ao continuar a tarefa.",
                    "input": change.get("rel") or change.get("path") or "",
                    "output": {
                        "action": change.get("action", ""),
                        "summary": change.get("summary", ""),
                        "objective": change.get("objective", ""),
                        "timestamp": change.get("timestamp", ""),
                    },
                    "source": "change_history",
                }
            )

        chunked_files = 0
        for path, rel in files:
            if chunked_files >= max_files:
                break
            if not self.should_include_file_in_local_training(path):
                continue
            text = self.read_local_training_text(path, max_chars=max_chars_per_file)
            if not text:
                continue
            chunked_files += 1
            rel_text = rel.as_posix()
            for index, chunk in enumerate(self.chunk_local_training_text(text, chunk_chars=chunk_chars), start=1):
                records.append(
                    {
                        "kind": "file_chunk",
                        "instruction": "Use este trecho do workspace como contexto local redigido.",
                        "input": {"path": rel_text, "chunk": index},
                        "output": chunk,
                        "source": rel_text,
                    }
                )
        return records

    def should_include_file_in_local_training(self, path):
        suffix = path.suffix.lower()
        if suffix in {".bak", ".tmp"} or suffix in IGNORED_SUFFIXES:
            return False
        allowed_suffixes = {
            ".cmd",
            ".cs",
            ".css",
            ".dart",
            ".html",
            ".java",
            ".js",
            ".json",
            ".jsx",
            ".kt",
            ".md",
            ".py",
            ".rs",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".xml",
            ".yaml",
            ".yml",
        }
        if suffix not in allowed_suffixes:
            return False
        try:
            return path.stat().st_size <= 350_000
        except OSError:
            return False

    def read_local_training_text(self, path, max_chars=8000):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        text = self.redact_local_training_text(text)
        return text[:max_chars].strip()

    def redact_local_training_text(self, text):
        secret_pattern = re.compile(
            r"(api[_-]?key|token|secret|password|passwd|private[_-]?key|client[_-]?secret|authorization)",
            re.IGNORECASE,
        )
        redacted = []
        for line in text.splitlines():
            redacted.append("[REDACTED_SECRET_LINE]" if secret_pattern.search(line) else line)
        return "\n".join(redacted)

    def chunk_local_training_text(self, text, chunk_chars=1800):
        if not text:
            return []
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            if end < len(text):
                newline = text.rfind("\n", start, end)
                if newline > start + 300:
                    end = newline
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
        return chunks

    def load_local_training_records(self, max_records=700):
        corpus_path = self.local_training_corpus_path(self.current_workspace)
        if not corpus_path.exists():
            return []

        records = []
        try:
            with corpus_path.open("r", encoding="utf-8") as file:
                for line in file:
                    if len(records) >= max_records:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        records.append(record)
        except OSError:
            return []
        return records

    def local_training_query_terms(self, query):
        normalized = self.normalize_plain_text(query or "")
        stopwords = {
            "ainda",
            "como",
            "com",
            "continue",
            "da",
            "das",
            "de",
            "do",
            "dos",
            "essa",
            "esse",
            "esta",
            "este",
            "para",
            "por",
            "que",
            "rede",
            "sem",
            "uma",
            "vou",
        }
        return {
            word
            for word in re.findall(r"[a-z0-9_]+", normalized)
            if len(word) >= 3 and word not in stopwords
        }

    def local_training_record_search_text(self, record):
        pieces = [
            str(record.get("kind", "")),
            str(record.get("source", "")),
            json.dumps(record.get("input", ""), ensure_ascii=False, sort_keys=True),
            json.dumps(record.get("output", ""), ensure_ascii=False, sort_keys=True),
        ]
        return self.normalize_plain_text(" ".join(pieces))

    def score_local_training_record(self, record, terms):
        kind = record.get("kind", "")
        base_scores = {
            "project_summary": 9,
            "recent_change": 8,
            "memory_node": 5,
            "file_chunk": 2,
        }
        score = base_scores.get(kind, 1)
        text = self.local_training_record_search_text(record)
        for term in terms:
            if term in text:
                score += 6
            if text.count(term) > 1:
                score += min(6, text.count(term))
        source = str(record.get("source", ""))
        if source in {"main.py", "modules/workspace_intelligence.py", "modules/app_constants.py"}:
            score += 3
        return score

    def format_local_training_context_record(self, record, max_output_chars=900):
        kind = str(record.get("kind", "registro"))
        source = str(record.get("source", "desconhecido"))
        input_value = record.get("input", "")
        output_value = record.get("output", "")
        if not isinstance(output_value, str):
            output_text = json.dumps(output_value, ensure_ascii=False, sort_keys=True)
        else:
            output_text = output_value
        output_text = output_text.strip()
        if len(output_text) > max_output_chars:
            output_text = output_text[:max_output_chars].rstrip() + "\n[trecho reduzido]"
        input_text = ""
        if input_value:
            input_text = json.dumps(input_value, ensure_ascii=False, sort_keys=True)
            if len(input_text) > 180:
                input_text = input_text[:180].rstrip() + "..."
        header = f"- {kind} | fonte: {source}"
        if input_text:
            header += f" | entrada: {input_text}"
        return f"{header}\n{output_text}"

    def build_local_training_context(self, command, max_records=9, max_chars=6000):
        status = self.local_training_subnet_status()
        if "Sub-rede local: pronta" not in status:
            status = self.ensure_local_training_subnet_ready()
        if "Sub-rede local: pronta" not in status and "Artefatos locais atualizados" not in status:
            return ""

        records = self.load_local_training_records()
        if not records:
            return ""

        terms = self.local_training_query_terms(command)
        ranked = sorted(
            records,
            key=lambda record: self.score_local_training_record(record, terms),
            reverse=True,
        )
        selected = ranked[:max_records]
        sections = []
        total_chars = 0
        for record in selected:
            text = self.format_local_training_context_record(record)
            if total_chars + len(text) > max_chars:
                remaining = max_chars - total_chars
                if remaining < 400:
                    break
                text = text[:remaining].rstrip() + "\n[contexto reduzido]"
            sections.append(text)
            total_chars += len(text)

        if not sections:
            return ""
        return (
            "CONTEXTO DA SUB-REDE LOCAL DO SISTEMA:\n"
            "A rede abaixo vem do corpus offline pronto em `.merotec_system_ai` e deve orientar a proxima acao.\n\n"
            f"{status}\n\n"
            "Registros mais relevantes:\n"
            + "\n\n".join(sections)
        )

    def is_local_llm_request(self, normalized):
        text = normalized or ""
        local_terms = {
            "llm",
            "modelo local",
            "ia local",
            "offline",
            "sem cota",
            "sem codex",
            "sub rede",
            "sub-rede",
            "rag",
        }
        action_terms = {
            "responda",
            "responder",
            "use",
            "usar",
            "fallback",
            "falhar",
            "falha",
            "cota",
            "treinavel",
            "transformar",
            "transformara",
            "modelo",
            "llm",
        }
        return any(term in text for term in local_terms) and any(term in text for term in action_terms)

    def is_external_model_failure_response(self, text):
        normalized = self.normalize_plain_text(text or "")
        failure_markers = {
            "alta demanda",
            "capacity",
            "insufficient_quota",
            "sem cota",
            "creditos esgotados",
            "limite atingido",
            "rate limit",
            "nao foi encontrado",
            "nao esta logado",
            "nao conseguiu iniciar",
            "terminou sem devolver texto",
            "tempo esgotado no modelo local gguf",
            "erro no lm studio",
            "nao consegui conectar ao lm studio",
            "o lm studio demorou mais",
            "app-server retornou erro",
            "retornou erro",
        }
        return any(marker in normalized for marker in failure_markers)

    def local_llm_fallback_reply(self, command, external_response="", image_path=None):
        if image_path or not self.is_external_model_failure_response(external_response):
            return None
        return self.local_llm_reply(command, failure_reason=external_response)

    def local_llm_reply(self, command, failure_reason="", max_records=8, max_chars=5200):
        status = self.local_training_subnet_status()
        if "Sub-rede local: pronta" not in status:
            status = self.ensure_local_training_subnet_ready()
        if "Sub-rede local: pronta" not in status and "Artefatos locais atualizados" not in status:
            return (
                "**LLM Local (RAG offline)**\n\n"
                "A rede local ainda nao tem corpus suficiente para responder como fallback.\n\n"
                f"{status}"
            )

        records = self.load_local_training_records()
        if not records:
            return (
                "**LLM Local (RAG offline)**\n\n"
                "A sub-rede existe, mas o corpus local esta vazio. Rode a varredura local novamente."
            )

        terms = self.local_training_query_terms(command)
        ranked = sorted(
            records,
            key=lambda record: self.score_local_training_record(record, terms),
            reverse=True,
        )
        selected = ranked[:max_records]
        if selected and not any(record.get("kind") == "file_chunk" for record in selected):
            first_chunk = next((record for record in ranked if record.get("kind") == "file_chunk"), None)
            if first_chunk:
                insert_at = min(2, len(selected))
                selected = selected[:insert_at] + [first_chunk] + selected[insert_at : max_records - 1]
        signals = []
        for record in selected:
            summary = self.summarize_local_llm_record(record)
            if summary:
                signals.append(summary)
            if len(signals) >= 6:
                break

        answer_lines = [
            "**LLM Local (RAG offline)**",
            "",
        ]
        if failure_reason:
            answer_lines.extend(
                [
                    "O modelo externo falhou; a resposta abaixo foi montada pelo motor local extrativo usando a sub-rede do sistema.",
                    "",
                ]
            )
        else:
            answer_lines.extend(
                [
                    "Resposta gerada pelo motor local extrativo usando a sub-rede e o corpus offline do sistema.",
                    "",
                ]
            )

        answer_lines.extend(
            [
                "Estado da rede:",
                self.compact_local_llm_status(status),
                "",
                "Resposta:",
                self.compose_local_llm_answer(command, selected),
                "",
                "Evidencias locais usadas:",
            ]
        )
        answer_lines.extend(f"- {signal}" for signal in signals)

        result = "\n".join(answer_lines).strip()
        if len(result) > max_chars:
            return result[:max_chars].rstrip() + "\n[resposta local reduzida]"
        return result

    def compact_local_llm_status(self, status):
        lines = []
        for line in (status or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("Sub-rede local:") or stripped.startswith("- Pasta") or stripped.startswith("- Registros"):
                lines.append(stripped)
        return "\n".join(lines) or "Sub-rede local preparada."

    def summarize_local_llm_record(self, record):
        kind = str(record.get("kind", "registro"))
        source = str(record.get("source", "desconhecido"))
        input_value = record.get("input", "")
        output_value = record.get("output", "")
        if isinstance(output_value, dict):
            output_text = " ".join(
                str(output_value.get(key, ""))
                for key in ("summary", "objective", "action", "timestamp")
                if output_value.get(key)
            )
        else:
            output_text = str(output_value or "")
        output_text = self.first_local_llm_sentence(output_text)
        if not output_text:
            output_text = json.dumps(input_value, ensure_ascii=False, sort_keys=True)[:160]
        if len(output_text) > 180:
            output_text = output_text[:180].rstrip() + "..."
        return f"{kind} em {source}: {output_text}"

    def first_local_llm_sentence(self, text):
        clean_lines = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "[REDACTED_SECRET_LINE]" in stripped:
                clean_lines.append("[REDACTED_SECRET_LINE]")
                continue
            clean_lines.append(stripped)
            if len(" ".join(clean_lines)) >= 180:
                break
        clean = " ".join(clean_lines)
        match = re.search(r"^(.{40,220}?[.!?])(?:\s|$)", clean)
        if match:
            return match.group(1).strip()
        return clean[:220].strip()

    def compose_local_llm_answer(self, command, records):
        normalized = self.normalize_plain_text(command or "")
        sources = []
        for record in records:
            source = str(record.get("source", "")).strip()
            if source and source not in sources:
                sources.append(source)
            if len(sources) >= 5:
                break

        if "transform" in normalized and "llm" in normalized:
            return (
                "A rede ja foi promovida de simples memoria para um fallback local estilo LLM/RAG: "
                "ela prepara corpus redigido, ranqueia registros por similaridade textual e monta uma resposta "
                "extrativa quando o modelo externo falha ou quando o usuario pede IA local. "
                "Ela ainda nao treina pesos neurais; o ganho real agora e autonomia offline baseada no proprio corpus."
            )

        if "cota" in normalized or "falha" in normalized or "falhar" in normalized:
            return (
                "Quando Codex/OpenAI/Gemini estiver sem cota, nao logado ou indisponivel, a IDE pode responder "
                "com a sub-rede local. A resposta fica limitada ao que existe no corpus, mas preserva continuidade "
                "da tarefa e evita expor segredos porque o corpus e redigido antes de ser salvo."
            )

        if sources:
            return (
                "Encontrei contexto local relevante em "
                + ", ".join(sources)
                + ". A resposta abaixo usa esses registros como base e evita inventar detalhes fora do corpus."
            )
        return "Encontrei registros na sub-rede local e montei uma resposta limitada ao corpus offline disponivel."

    def autonomous_discovery_trigger_categories(self):
        return {
            "intencao": {
                "label": "Gatilho de intencao",
                "definition": "pedido, meta, requisito ou demanda de produto que inicia trabalho sem precisar de pergunta extra.",
                "action": "converter o objetivo em arquivos provaveis, menor mudanca e criterio de aceite.",
                "terms": {
                    "pedido",
                    "meta",
                    "objetivo",
                    "requisito",
                    "demanda",
                    "feature",
                    "funcionalidade",
                    "usuario",
                    "cliente",
                    "brief",
                    "historia",
                    "interacao",
                },
            },
            "evidencia": {
                "label": "Gatilho de evidencia",
                "definition": "erro, log, teste, build, screenshot ou stress test que revela o proximo ponto de investigacao.",
                "action": "rodar a validacao mais barata, ler o diagnostico e corrigir a causa provavel.",
                "terms": {
                    "erro",
                    "erros",
                    "falha",
                    "falhas",
                    "log",
                    "logs",
                    "teste",
                    "testes",
                    "stress",
                    "estresse",
                    "build",
                    "validacao",
                    "resultado",
                    "screenshot",
                    "print",
                    "diagnostico",
                    "descoberta",
                    "descobrir",
                    "mapear",
                },
            },
            "restricao": {
                "label": "Gatilho de restricao",
                "definition": "permissao, administrador, login, sandbox, limite, dependencia ou politica que bloqueia a execucao normal.",
                "action": "rotear para alternativa segura, comando elevado real ou conclusao objetiva quando nao houver acao possivel.",
                "terms": {
                    "admin",
                    "administrador",
                    "permissao",
                    "permissoes",
                    "privilegio",
                    "privilegios",
                    "uac",
                    "sandbox",
                    "limite",
                    "capacidade",
                    "login",
                    "chave",
                    "api",
                    "dependencia",
                    "bloqueio",
                    "bloqueado",
                    "seguranca",
                },
            },
        }

    def classify_autonomous_discovery_trigger(self, normalized):
        normalized = self.normalize_plain_text(normalized or "")
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        categories = self.autonomous_discovery_trigger_categories()
        scores = {}
        for key, category in categories.items():
            score = 0
            for term in category["terms"]:
                if " " in term:
                    score += 1 if term in normalized else 0
                else:
                    score += 1 if term in words else 0
            scores[key] = score

        if not any(scores.values()):
            return "intencao"
        return max(categories.keys(), key=lambda key: (scores[key], -list(categories.keys()).index(key)))

    def autonomous_discovery_validation_command(self):
        workspace = Path(self.current_workspace)
        if (workspace / "pubspec.yaml").exists():
            pubspec = (workspace / "pubspec.yaml").read_text(encoding="utf-8", errors="replace").lower()
            return "flutter analyze" if "flutter:" in pubspec or "sdk: flutter" in pubspec else "dart analyze"
        package_json = workspace / "package.json"
        if package_json.exists():
            text = package_json.read_text(encoding="utf-8", errors="replace")
            if '"test"' in text:
                return "npm test"
            if '"build"' in text:
                return "npm run build"
            return "npm install --dry-run"
        if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists() or any(workspace.glob("*.py")):
            targets = [
                name
                for name in ("main.py", "app.py", "modules", "tests")
                if (workspace / name).exists()
            ]
            if targets:
                return f'"{sys.executable}" -m compileall -q ' + " ".join(targets)
            excluded = r"(^|[\\/])(\.git|\.venv|venv|env|node_modules|__pycache__)([\\/]|$)"
            return f'"{sys.executable}" -m compileall -q -x "{excluded}" .'
        if (workspace / "index.html").exists() or any(workspace.glob("*.html")):
            return "python -m http.server 8000"
        return ""

    def autonomous_discovery_project_signal(self):
        workspace = Path(self.current_workspace)
        try:
            summary = self.local_project_summary()
        except Exception:
            kind = self.detect_run_kind(workspace) or "generico"
            return f"Projeto atual: {workspace.name}\nTipo detectado: {kind}"

        lines = summary.splitlines()
        compact = []
        for line in lines:
            compact.append(line)
            if len(compact) >= 12:
                break
        return "\n".join(compact)

    def is_zoom_mobile_verification_request(self, normalized):
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        verify_terms = {"verificar", "verifique", "veja", "existe", "exista", "tem", "possui", "confira"}
        return bool(words & verify_terms) and "zoom" in normalized and "mobile" in normalized

    def verify_zoom_mobile_locally(self, command):
        files = self.find_likely_search_targets(command, suffixes={".html", ".js", ".ts", ".tsx", ".jsx", ".dart"})
        if not files:
            return "Nao encontrei arquivo de codigo adequado no projeto atual para verificar zoom mobile."

        pattern = r"zoom|pinch|wheel|touchstart|touchmove|gesture|mobile|isMobile|scale|cameraZoom|fov"
        summaries = []
        zoom_hits = []
        mobile_hits = []
        touch_hits = []
        render_hits = []

        for path in files[:8]:
            rel = path.relative_to(self.current_workspace).as_posix()
            matches = self.search_file_lines(path, pattern, limit=40)
            if not matches:
                summaries.append(f"- {rel}: nenhuma ocorrencia.")
                continue
            summaries.append(f"- {rel}: {len(matches)} ocorrencia(s).")
            for number, line in matches:
                lower = line.lower()
                entry = f"{rel}:{number}: {line.strip()[:180]}"
                if any(term in lower for term in ("zoom", "camerazoom", "pinch")):
                    zoom_hits.append(entry)
                if any(term in lower for term in ("mobile", "ismobile", "modo mobile")):
                    mobile_hits.append(entry)
                if any(term in lower for term in ("pinch", "touchstart", "touchmove", "gesture", "wheel")):
                    touch_hits.append(entry)
                if any(term in lower for term in ("scale", "fov")):
                    render_hits.append(entry)

        if zoom_hits and (mobile_hits or touch_hits):
            verdict = "Sim, encontrei sinais de logica de zoom relacionada a mobile/toque."
        elif zoom_hits:
            verdict = "Encontrei logica de zoom, mas nao encontrei evidencia clara de que ela esteja limitada ao modo mobile."
        elif mobile_hits or touch_hits:
            verdict = "Encontrei logica de mobile/toque, mas nao encontrei funcao clara de zoom."
        else:
            verdict = "Nao encontrei funcao de zoom para modo mobile nos arquivos analisados."

        evidence = zoom_hits[:6] + mobile_hits[:4] + touch_hits[:4]
        evidence_text = "\n".join(f"- {item}" for item in evidence) if evidence else "- Sem linhas relevantes."
        render_text = ""
        if render_hits:
            render_text = (
                "\n\nObservacoes de renderizacao/camera que nao confirmam zoom mobile por si so:\n"
                + "\n".join(f"- {item}" for item in render_hits[:4])
            )
        return (
            f"{verdict}\n\n"
            "Arquivos verificados:\n"
            + "\n".join(summaries[:8])
            + "\n\nEvidencias:\n"
            + evidence_text
            + render_text
        )

    def find_likely_search_targets(self, command, suffixes=None, limit=12):
        suffixes = suffixes or {".py", ".js", ".ts", ".html", ".css", ".dart"}
        workspace = Path(self.current_workspace).resolve()
        mentioned = self.extract_mentioned_file_paths(command)
        targets = []
        seen = set()

        for raw in mentioned:
            try:
                path = self.resolve_workspace_path(raw)
            except Exception:
                continue
            if path.is_file() and path.suffix.lower() in suffixes and path not in seen:
                targets.append(path)
                seen.add(path)

        for info in getattr(self, "open_editors", {}).values():
            raw_path = info.get("path")
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.is_file() and path.suffix.lower() in suffixes and path not in seen:
                targets.append(path)
                seen.add(path)

        for path, _rel in self.iter_workspace_files(limit=800):
            if path.suffix.lower() not in suffixes or path in seen:
                continue
            targets.append(path)
            seen.add(path)
            if len(targets) >= limit:
                break

        return [path for path in targets if str(path.resolve()).startswith(str(workspace))]

    def classify_smart_task_intent(self, command):
        normalized = self.normalize_plain_text(command or "")
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        intent_terms = {
            "corrigir": {
                "corrigir",
                "corrija",
                "erro",
                "bug",
                "falha",
                "quebrou",
                "problema",
                "conserte",
                "arrume",
                "ajuste",
            },
            "implementar": {
                "implementar",
                "implemente",
                "criar",
                "crie",
                "adicionar",
                "adicione",
                "fazer",
                "faca",
                "melhorar",
                "melhore",
                "tornar",
                "torne",
            },
            "validar": {
                "testar",
                "teste",
                "validar",
                "valide",
                "verificar",
                "verifique",
                "rodar",
                "rode",
                "executar",
                "execute",
                "visual",
            },
            "analisar": {
                "analisar",
                "analise",
                "avaliar",
                "avalie",
                "revisar",
                "revise",
                "diagnostico",
                "mapear",
                "explique",
            },
            "configurar": {
                "configurar",
                "configure",
                "chave",
                "api",
                "modelo",
                "provider",
                "provedor",
                "openai",
                "codex",
                "google",
                "openrouter",
            },
        }
        scores = {
            intent: sum(1 for term in terms if term in words or term in normalized)
            for intent, terms in intent_terms.items()
        }
        if not any(scores.values()):
            return "responder"
        return max(scores, key=lambda intent: scores[intent])

    def validation_command_for_smart_intent(self, intent):
        workspace = Path(self.current_workspace)
        if intent in {"responder", "analisar"}:
            return ""
        try:
            return self.autonomous_discovery_validation_command()
        except Exception:
            pass
        if (workspace / "requirements.txt").exists() or any(workspace.glob("*.py")):
            return f'"{sys.executable}" -m unittest discover'
        if (workspace / "package.json").exists():
            return "npm test"
        if (workspace / "index.html").exists() or any(workspace.glob("*.html")):
            return "python -m http.server 8000"
        return ""

    def build_smart_task_brief(self, command, objective=None, max_files=8):
        objective = objective or command or ""
        normalized = self.normalize_plain_text(objective)
        intent = self.classify_smart_task_intent(objective)
        workspace = Path(self.current_workspace).resolve()
        kind = self.detect_run_kind(workspace) or "generico"

        suffixes_by_intent = {
            "corrigir": {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".dart", ".json", ".cmd", ".yaml", ".yml"},
            "implementar": {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".dart", ".json", ".md", ".yaml", ".yml"},
            "validar": {".py", ".js", ".ts", ".html", ".json", ".yaml", ".yml", ".cmd", ".md", ".dart"},
            "analisar": {".py", ".js", ".ts", ".html", ".json", ".yaml", ".yml", ".md"},
            "configurar": {".py", ".json", ".env", ".cmd", ".md", ".yaml", ".yml"},
        }
        candidate_files = self.find_likely_search_targets(
            objective,
            suffixes=suffixes_by_intent.get(intent, {".py", ".js", ".html", ".md", ".json"}),
            limit=max_files,
        )
        file_lines = []
        for path in candidate_files[:max_files]:
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                continue
            file_lines.append(f"- {rel}")
        if not file_lines:
            key_files = self.local_key_files(list(self.iter_workspace_files(limit=300)), limit=max_files)
            file_lines = key_files.splitlines() if key_files else ["- Nenhum arquivo candidato detectado."]

        validation = self.validation_command_for_smart_intent(intent)
        risk_notes = []
        if intent in {"corrigir", "implementar"}:
            risk_notes.append("Leia o trecho exato antes de alterar e prefira patch pequeno.")
        if "config" in normalized or intent == "configurar":
            risk_notes.append("Preserve chaves e configuracoes existentes; nao exponha segredos no chat.")
        if kind == "python":
            risk_notes.append("Validacao barata: compile ou unittest antes de teste visual.")
        elif kind in {"html", "node"}:
            risk_notes.append("Quando houver interface, use validacao visual ou servidor local.")
        elif kind == "flutter":
            risk_notes.append("Erros Windows/CMake devem ser corrigidos na camada nativa, nao no Dart sem evidencia.")
        if not risk_notes:
            risk_notes.append("Mantenha foco na menor acao verificavel.")

        next_step = {
            "corrigir": "Localizar causa, aplicar correcao pequena e validar.",
            "implementar": "Escolher arquivos alvo, implementar o minimo completo e validar.",
            "validar": "Executar validacao apropriada e interpretar a saida antes de concluir.",
            "analisar": "Entregar diagnostico objetivo; leia arquivos especificos apenas se faltar evidencia.",
            "configurar": "Conferir fluxo de configuracao, preservar valores atuais e validar inicializacao.",
            "responder": "Responder diretamente se nao houver necessidade de editar ou executar.",
        }.get(intent, "Avancar com a menor acao verificavel.")

        return (
            "BRIEFING INTELIGENTE DA IDE:\n"
            f"- Intencao detectada: {intent}\n"
            f"- Tipo de projeto detectado: {kind}\n"
            f"- Proximo passo recomendado: {next_step}\n"
            f"- Validacao sugerida: {validation or 'sem comando automatico; conclua em texto ou escolha validacao pontual'}\n"
            "Arquivos candidatos:\n"
            + "\n".join(file_lines[:max_files])
            + "\nRiscos e cuidados:\n"
            + "\n".join(f"- {note}" for note in risk_notes)
        )

    def extract_mentioned_file_paths(self, text):
        extensions = r"(?:html|css|js|ts|tsx|jsx|py|dart|json|md|yaml|yml|txt|cpp|h|cs)"
        return re.findall(r"(?<![\w.-])([A-Za-z0-9_./\\-]+\." + extensions + r")", text or "", re.IGNORECASE)

    def search_file_lines(self, path, pattern, limit=80):
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        matches = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for number, line in enumerate(file, start=1):
                    if regex.search(line):
                        matches.append((number, line.rstrip("\n\r")))
                    if len(matches) >= limit:
                        break
        except OSError:
            return []
        return matches

    def is_project_run_request(self, normalized):
        if any(
            marker in normalized
            for marker in (
                "teste visual",
                "testes visuais",
                "testar visualmente",
                "teste de interface",
                "testar a interface",
            )
        ):
            return False
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        run_terms = {"execute", "executa", "executar", "rode", "roda", "rodar", "abrir", "inicie", "iniciar"}
        build_terms = {"build", "builde", "compila", "compile", "compilar"}
        target_terms = {"app", "projeto", "aplicativo", "programa"}
        fix_terms = {"corrija", "corrige", "corrigir", "arrume", "arruma", "arrumar", "conserte", "consertar"}
        if words & fix_terms and not words & run_terms:
            return False
        if words and words <= (run_terms | build_terms) and self.has_default_run_target():
            return True
        return bool(words & target_terms) and (bool(words & run_terms) or bool(words & build_terms))

    def has_default_run_target(self):
        workspace = Path(self.current_workspace)
        if (workspace / "pubspec.yaml").exists():
            return True
        if (workspace / "app.py").exists() or (workspace / "main.py").exists():
            return True
        if (workspace / "index.html").exists():
            return True
        return any(workspace.glob("*.html"))

    def normalize_match_key(self, text):
        normalized = self.normalize_plain_text(str(text or ""))
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def run_request_tokens(self, command, normalized=None):
        text = self.normalize_match_key(normalized or command)
        stop_words = {
            "a",
            "abra",
            "abrir",
            "app",
            "aplicativo",
            "build",
            "builde",
            "compila",
            "compile",
            "de",
            "do",
            "e",
            "execute",
            "executa",
            "executar",
            "inicie",
            "iniciar",
            "o",
            "os",
            "programa",
            "projeto",
            "roda",
            "rodar",
            "rode",
            "um",
            "uma",
        }
        return [token for token in text.split() if len(token) >= 3 and token not in stop_words]

    def is_flet_workspace(self, workspace):
        workspace = Path(workspace)
        for relative in ("requirements.txt", "pyproject.toml"):
            try:
                text = (workspace / relative).read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if re.search(r"(^|\n)\s*flet(?:[<>=~! ]|$)", text) or " flet" in text:
                return True
        for relative in ("main.py", "app.py"):
            try:
                text = (workspace / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"\b(import\s+flet|from\s+flet\s+import)\b", text):
                return True
        return False

    def detect_run_kind(self, workspace):
        workspace = Path(workspace)
        if (workspace / "pubspec.yaml").exists():
            pubspec = (workspace / "pubspec.yaml").read_text(encoding="utf-8", errors="replace").lower()
            return "flutter" if "flutter:" in pubspec or "sdk: flutter" in pubspec else "dart"
        if (workspace / "package.json").exists():
            return "node"
        if self.is_flet_workspace(workspace):
            return "flet"
        if (workspace / "app.py").exists() or (workspace / "main.py").exists():
            return "python"
        if (workspace / "index.html").exists() or any(workspace.glob("*.html")):
            return "html"
        return ""

    def runnable_workspace_score(self, workspace):
        workspace = Path(workspace)
        score = 0
        kind = self.detect_run_kind(workspace)
        if kind == "flutter":
            score += 60
            if (workspace / "lib" / "main.dart").exists():
                score += 45
            if (workspace / "windows").exists():
                score += 15
            if (workspace / "android").exists() or (workspace / "ios").exists():
                score += 8
        elif kind == "dart":
            score += 58
            if (workspace / "bin" / "main.dart").exists():
                score += 25
        elif kind == "flet":
            score += 62
            if (workspace / "main.py").exists():
                score += 20
        elif kind == "node":
            score += 70
            if (workspace / "src").exists():
                score += 10
        elif kind == "python":
            score += 55
        elif kind == "html":
            score += 45
            if (workspace / "index.html").exists():
                score += 15
        try:
            rel_parts = workspace.resolve().relative_to(Path(self.current_workspace).resolve()).parts
            score += min(len(rel_parts), 4)
        except ValueError:
            pass
        return score

    def find_runnable_workspaces(self, limit=80):
        workspace = Path(self.current_workspace).resolve()
        markers = {
            "package.json",
            "pubspec.yaml",
            "pyproject.toml",
            "requirements.txt",
            "index.html",
            "main.py",
            "app.py",
        }
        candidates = {workspace}
        for path, _rel in self.iter_workspace_files(limit=3000):
            if path.name in markers:
                candidates.add(path.parent.resolve())
            if len(candidates) >= limit:
                break
        return sorted(
            (path for path in candidates if self.detect_run_kind(path)),
            key=lambda path: (self.runnable_workspace_score(path), str(path).lower()),
            reverse=True,
        )

    def resolve_requested_run_workspace(self, command, normalized=None):
        workspace = Path(self.current_workspace).resolve()
        candidates = self.find_runnable_workspaces()
        if not candidates:
            return workspace

        tokens = self.run_request_tokens(command, normalized)
        mentioned_dirs = []
        for candidate in candidates:
            try:
                rel = candidate.relative_to(workspace)
            except ValueError:
                continue
            rel_key = self.normalize_match_key(rel.as_posix())
            name_key = self.normalize_match_key(candidate.name)
            if any(token in rel_key.split() or token == name_key for token in tokens):
                mentioned_dirs.append(candidate)

        if mentioned_dirs:
            bases = []
            for mentioned in mentioned_dirs:
                bases.append(mentioned)
                try:
                    parent = mentioned.parent
                    if parent != workspace and parent.is_relative_to(workspace):
                        bases.append(parent)
                except AttributeError:
                    try:
                        mentioned.parent.relative_to(workspace)
                        bases.append(mentioned.parent)
                    except ValueError:
                        pass

            scoped = []
            for candidate in candidates:
                for base in bases:
                    try:
                        candidate.relative_to(base)
                    except ValueError:
                        continue
                    scoped.append(candidate)
                    break
            if scoped:
                return max(scoped, key=self.runnable_workspace_score)

        root_kind = self.detect_run_kind(workspace)
        if root_kind:
            return workspace
        return candidates[0]

    def relative_workspace_label(self, path):
        workspace = Path(self.current_workspace).resolve()
        path = Path(path).resolve()
        try:
            rel = path.relative_to(workspace)
            return "." if rel.as_posix() == "." else rel.as_posix()
        except ValueError:
            return str(path)

    def start_project_run_task(self, command, normalized=None):
        workspace = self.resolve_requested_run_workspace(command, normalized)
        rel_label = self.relative_workspace_label(workspace)
        kind = self.detect_run_kind(workspace)
        if (workspace / "pubspec.yaml").exists():
            if kind == "flutter":
                has_platform = any((workspace / folder).exists() for folder in ("android", "ios", "windows", "macos", "linux", "web"))
                command = "flutter pub get && flutter run -d windows" if has_platform else "flutter create . && flutter run -d windows"
                self.run_workspace_command(command, f"Executando app Flutter: {rel_label}", cwd=workspace)
                return (
                    f"Execucao iniciada para `{rel_label}` como app Flutter pelo Terminal Local. "
                    "O `flutter run -d windows` ja faz o build antes de abrir o app."
                )
            command = "dart pub get && dart run"
            self.run_workspace_command(command, f"Executando app Dart: {rel_label}", cwd=workspace)
            return f"Execucao iniciada para `{rel_label}` como projeto Dart pelo Terminal Local."

        app_py = workspace / "app.py"
        main_py = workspace / "main.py"
        if app_py.exists() or main_py.exists():
            target = app_py if app_py.exists() else main_py
            command = f'"{sys.executable}" "{target.name}"'
            self.run_workspace_command(command, f"Executando {rel_label}/{target.name}", cwd=workspace)
            return f"Execucao iniciada para `{rel_label}/{target.name}` pelo Terminal Local."

        html_target = workspace / "index.html"
        if not html_target.exists():
            html_files = sorted(workspace.glob("*.html"))
            html_target = html_files[0] if html_files else None
        if html_target and html_target.exists():
            if os.name == "nt":
                command = f'cmd /c start "" "{html_target.name}"'
            else:
                command = f'python -m webbrowser "{html_target.name}"'
            self.run_workspace_command(command, f"Abrindo {rel_label}/{html_target.name}", cwd=workspace)
            return f"Abertura iniciada para `{rel_label}/{html_target.name}` no navegador padrao."

        return (
            f"Nao encontrei um comando automatico seguro para executar `{rel_label}`. "
            "Abra o Terminal Local e rode o comando especifico do framework."
        )

    def run_workspace_command(self, command, title=None, cwd=None):
        cwd_path = str(Path(cwd or self.current_workspace).resolve())
        self.tabview.set("Terminal Local")
        self.append_to_term(f"\n> {title or command}\n{cwd_path}> {command}\n")
        self.log_agent(f"Comando local iniciado: {command}")
        self.set_status(title or "Comando local em execucao...", "busy")
        self.set_terminal_busy(True, title or f"Executando: {command[:70]}")

        def execute():
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd_path,
                )
                self.register_terminal_process(process, title or command)
                self.stream_process_output(process)
                process.wait()
                self.append_to_term(f"\n[processo finalizado com codigo {process.returncode}]\n")
                self.set_status("Comando local finalizado.", "ready" if process.returncode == 0 else "error")
                self.load_workspace_files()
            except Exception as exc:
                self.append_to_term(f"[erro] {exc}\n")
                self.set_status("Falha no comando local.", "error")
            finally:
                if "process" in locals():
                    self.unregister_terminal_process(process)
                self.set_terminal_busy(False)

        threading.Thread(target=execute, daemon=True).start()

    def is_vague_project_update_request(self, normalized):
        if "projeto" not in normalized:
            return False

        words = set(re.findall(r"[a-z0-9_]+", normalized))
        update_terms = {
            "atualiza",
            "atualize",
            "atualizar",
            "melhora",
            "melhore",
            "melhorar",
            "arruma",
            "arrume",
            "arrumar",
            "corrige",
            "corrija",
            "corrigir",
        }
        if not words & update_terms:
            return False

        specific_targets = {
            "botao",
            "botoes",
            "layout",
            "tela",
            "erro",
            "build",
            "login",
            "codex",
            "explorer",
            "arquivo",
            "pubspec",
            "readme",
            "menu",
            "chat",
            "terminal",
        }
        return not bool(words & specific_targets)

    def is_project_analysis_request(self, normalized):
        if "projeto" not in normalized:
            return False

        analysis_terms = {
            "analise",
            "analisa",
            "analisar",
            "analize",
            "analiza",
            "analizar",
            "avaliar",
            "avalie",
            "revisar",
            "revise",
            "verificar",
            "verifique",
        }
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        if words & analysis_terms:
            return True

        relaxed_patterns = [
            r"\bfaca\b.*\bprojeto\b",
            r"\bveja\b.*\bprojeto\b",
            r"\bolhe\b.*\bprojeto\b",
        ]
        return any(re.search(pattern, normalized) for pattern in relaxed_patterns)

    def normalize_plain_text(self, text):
        normalized = unicodedata.normalize("NFKD", text.strip().lower())
        without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", without_accents).strip()

    def local_project_summary(self):
        workspace = Path(self.current_workspace)
        files = list(self.iter_workspace_files(limit=1200))
        if not files:
            return (
                f"Projeto atual: {workspace.name}\n\n"
                "Nao encontrei arquivos analisaveis nesse projeto."
            )

        suffix_counts = Counter(path.suffix.lower() or "[sem extensao]" for path, _rel in files)
        top_extensions = ", ".join(
            f"{suffix}: {count}" for suffix, count in suffix_counts.most_common(8)
        )
        total_size = sum(path.stat().st_size for path, _rel in files if path.exists())
        key_files = self.local_key_files(files)
        project_type = self.detect_project_type(workspace, suffix_counts)
        folders = self.local_top_folders(files)

        notes = []
        if (workspace / "pubspec.yaml").exists():
            stack = "Flutter" if self.detect_run_kind(workspace) == "flutter" else "Dart"
            notes.append(f"Parece ser um projeto {stack}.")
        if self.is_flet_workspace(workspace):
            notes.append("Parece ser um app Flet/Python.")
        if (workspace / "build").exists():
            notes.append("A pasta build existe, mas fica ignorada pela IDE para nao poluir o contexto.")
        if any(rel.as_posix().endswith(".bak") for _path, rel in files):
            notes.append("Ha arquivos .bak no projeto; eles parecem backups criados pela IDE.")
        if not notes:
            notes.append("A estrutura esta limpa para leitura inicial.")

        return (
            f"Projeto atual: {workspace.name}\n"
            f"Tipo detectado: {project_type}\n"
            f"Arquivos analisaveis: {len(files)}\n"
            f"Tamanho aproximado: {self.format_bytes(total_size)}\n"
            f"Extensoes principais: {top_extensions}\n\n"
            f"Pastas principais:\n{folders}\n\n"
            f"Arquivos-chave:\n{key_files}\n\n"
            f"Observacoes:\n- " + "\n- ".join(notes)
        )

    def build_project_analysis_context(self):
        return (
            "CONTEXTO INICIAL DE ANALISE DO PROJETO GERADO PELA IDE:\n\n"
            f"{self.build_project_intelligence_context(deep=True)}\n\n"
            + "\n\n"
            "Ordem para a IA: esta e uma analise arquitetural de projeto grande. "
            "Nao leia dezenas de arquivos. Nao peca [READ] em massa. "
            "Use o mapa de subprojetos, arquivos-chave e comandos provaveis para entregar uma visao geral util. "
            "Leia no maximo 1 ou 2 arquivos especificos apenas se forem indispensaveis para confirmar uma conclusao."
        )

    def build_project_intelligence_context(self, deep=False):
        workspace = Path(self.current_workspace).resolve()
        files = list(self.iter_workspace_files(limit=1800 if deep else 900))
        summary = self.local_project_summary()
        key_files = self.local_key_files(files, limit=14)
        manifest = self.build_project_manifest(files, limit=130 if deep else 90)
        subprojects = self.detect_subprojects(files, limit=40 if deep else 20)
        recent_changes = []
        if hasattr(self, "recent_change_records"):
            recent_changes = self.recent_change_records(limit=10)
        self.memory_subnet = MemorySubnet(workspace)
        self.memory_subnet.ingest_project_signals(files, summary=summary, recent_changes=recent_changes)
        memory_subnet = self.memory_subnet.format_for_agent(limit=14 if deep else 10)
        run_hints = []
        if (workspace / "pubspec.yaml").exists():
            run_hints.extend([
                "- Flutter detectado: valide com `flutter test`, `flutter run -d windows` ou build alvo pedido.",
                "- Erros Windows/CMake/C++ costumam estar em `windows/runner/*` e nao em `lib/main.dart`.",
            ])
        if (workspace / "package.json").exists():
            run_hints.append("- Node/Web detectado: confira scripts em package.json antes de executar.")
        if (workspace / "index.html").exists():
            run_hints.append("- HTML unico detectado: evite reescrever o arquivo inteiro; preserve funcoes, controles e cena existentes.")
        if not run_hints:
            run_hints.append("- Use os arquivos-chave e o explorer para escolher a menor mudanca verificavel.")

        return (
            "MAPA PERMANENTE DO PROJETO PARA A IA:\n"
            f"{summary}\n\n"
            "Arquivos-chave do projeto:\n"
            f"{key_files}\n\n"
            "Manifesto compacto do projeto:\n"
            f"{manifest}\n\n"
            "Subprojetos detectados:\n"
            f"{subprojects}\n\n"
            "Historico recente que deve ser lembrado:\n"
            f"{self.format_recent_changes_for_agent(limit=10)}\n\n"
            "Sub-rede de memoria do projeto:\n"
            f"{memory_subnet}\n\n"
            "Direcionamento de trabalho:\n"
            + "\n".join(run_hints)
            + "\n- Entenda o projeto como um todo antes de editar, mas nao repita leitura do mesmo arquivo em loop.\n"
            "- Prefira mudancas cirurgicas em arquivos existentes; use reescrita completa so quando o usuario pedir recriar/refazer do zero.\n"
            "- Se o usuario pedir desfazer/restaurar algo destruido, use o historico e backups antes de criar uma nova versao."
        )

    def build_project_manifest(self, files, limit=90):
        if not files:
            return "- Nenhum arquivo textual encontrado."
        folder_counts = Counter()
        entry_candidates = []
        for path, rel in files:
            parts = rel.parts
            folder = parts[0] if len(parts) > 1 else "."
            folder_counts[folder] += 1
            rel_text = rel.as_posix()
            name = path.name.lower()
            if name in {
                "index.html",
                "main.py",
                "app.py",
                "package.json",
                "pubspec.yaml",
                "lib/main.dart",
                "readme.md",
            } or rel_text in {
                "lib/main.dart",
                "src/main.js",
                "src/App.jsx",
                "src/App.tsx",
                "windows/runner/CMakeLists.txt",
            }:
                entry_candidates.append(rel_text)

        lines = ["Pastas:"]
        for folder, count in folder_counts.most_common(18):
            lines.append(f"- {folder}: {count} arquivo(s)")
        lines.append("")
        lines.append("Entradas/configuracoes provaveis:")
        for item in entry_candidates[:24]:
            lines.append(f"- {item}")
        if len(lines) < limit:
            lines.append("")
            lines.append("Arquivos visiveis principais:")
            for _path, rel in files[: max(0, limit - len(lines))]:
                lines.append(f"- {rel.as_posix()}")
        return "\n".join(lines[:limit])

    def detect_subprojects(self, files, limit=24):
        if not files:
            return "- Nenhum subprojeto detectado."
        markers = {
            "package.json": "Node/Web",
            "pubspec.yaml": "Flutter/Dart",
            "pyproject.toml": "Python",
            "requirements.txt": "Python",
            "Cargo.toml": "Rust",
            "go.mod": "Go",
            "pom.xml": "Java/Maven",
            "build.gradle": "Java/Gradle",
            "index.html": "Web/HTML",
            "main.py": "Python",
            "app.py": "Python",
        }
        projects = {}
        for path, rel in files:
            marker_type = markers.get(path.name)
            if not marker_type:
                continue
            folder = rel.parent.as_posix() if rel.parent.as_posix() != "." else "."
            entry = projects.setdefault(
                folder,
                {
                    "types": set(),
                    "markers": [],
                    "files": 0,
                    "size": 0,
                },
            )
            entry["types"].add(marker_type)
            entry["markers"].append(rel.as_posix())

        for path, rel in files:
            rel_text = rel.as_posix()
            matched_folder = "."
            for folder in projects:
                if folder != "." and rel_text.startswith(folder.rstrip("/") + "/"):
                    if len(folder) > len(matched_folder):
                        matched_folder = folder
            if matched_folder in projects:
                projects[matched_folder]["files"] += 1
                try:
                    projects[matched_folder]["size"] += path.stat().st_size
                except OSError:
                    pass

        if not projects:
            return "- Nenhum subprojeto com marcadores conhecidos foi detectado."

        lines = []
        for folder, info in sorted(projects.items(), key=lambda item: (item[0] != ".", item[0].lower()))[:limit]:
            types = ", ".join(sorted(info["types"]))
            markers_text = ", ".join(info["markers"][:5])
            lines.append(
                f"- {folder}: {types}; {info['files']} arquivo(s); {self.format_bytes(info['size'])}; marcadores: {markers_text}"
            )
        if len(projects) > limit:
            lines.append(f"- ... {len(projects) - limit} subprojeto(s) omitido(s).")
        return "\n".join(lines)

    def detect_project_type(self, workspace, suffix_counts):
        if (workspace / "pubspec.yaml").exists():
            return "Flutter" if self.detect_run_kind(workspace) == "flutter" else "Dart"
        if (workspace / "package.json").exists():
            return "JavaScript/Node"
        if self.is_flet_workspace(workspace):
            return "Flet/Python"
        if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists():
            return "Python"
        if suffix_counts.get(".py", 0) >= 2:
            return "Python"
        if suffix_counts.get(".html", 0) and suffix_counts.get(".css", 0):
            return "Web"
        return "Projeto generico"

    def local_key_files(self, files, limit=12):
        priority_names = {
            "README.md",
            "pubspec.yaml",
            "analysis_options.yaml",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "main.py",
            "app.py",
        }
        ordered = []
        seen = set()
        for path, rel in files:
            if path.name in priority_names:
                ordered.append(rel.as_posix())
                seen.add(rel.as_posix())
        for _path, rel in files:
            text = rel.as_posix()
            if text not in seen:
                ordered.append(text)
                seen.add(text)
            if len(ordered) >= limit:
                break
        return "\n".join(f"- {item}" for item in ordered[:limit])

    def local_top_folders(self, files, limit=10):
        counts = Counter()
        for _path, rel in files:
            parts = rel.parts
            counts[parts[0] if len(parts) > 1 else "."] += 1
        return "\n".join(f"- {name}: {count} arquivo(s)" for name, count in counts.most_common(limit))

    def format_bytes(self, size):
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024

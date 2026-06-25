import json
import html
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import difflib
import ctypes
import ctypes.wintypes
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tkinter import messagebox

from PIL import ImageGrab

from modules.app_constants import APP_NAME, IGNORED_DIRS, PROJECT_ROOT


class DuckDuckGoHtmlResultParser(HTMLParser):
    def __init__(self, max_results=5):
        super().__init__(convert_charrefs=True)
        self.max_results = max_results
        self.results = []
        self._capture_link = False
        self._capture_snippet = False
        self._current_href = ""
        self._current_text = []
        self._current_snippet = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs or [])
        css_class = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in css_class and len(self.results) < self.max_results:
            self._capture_link = True
            self._current_href = attrs_dict.get("href", "")
            self._current_text = []
            return
        if "result__snippet" in css_class and self.results:
            self._capture_snippet = True
            self._current_snippet = []

    def handle_data(self, data):
        if self._capture_link:
            self._current_text.append(data)
        elif self._capture_snippet:
            self._current_snippet.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._capture_link:
            title = " ".join("".join(self._current_text).split())
            url = AgentActionsMixin.normalize_duckduckgo_result_url(self._current_href)
            if title and url:
                self.results.append({"title": title, "url": url, "snippet": ""})
            self._capture_link = False
            self._current_href = ""
            self._current_text = []
            return
        if self._capture_snippet and tag in {"a", "td", "div"}:
            snippet = " ".join("".join(self._current_snippet).split())
            if snippet and self.results and not self.results[-1].get("snippet"):
                self.results[-1]["snippet"] = snippet
            self._capture_snippet = False
            self._current_snippet = []


class AgentActionsMixin:
    def snapshot_workspace_for_direct_actions(self):
        root_text = self.current_workspace or ""
        if not root_text:
            return {}
        try:
            root = Path(root_text).resolve()
        except OSError:
            return {}
        if not root.exists() or not root.is_dir():
            return {}

        snapshot = {}
        ignored_suffixes = {".bin", ".dll", ".exe", ".pyd", ".pyc", ".zip"}
        max_files = 20000
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    name for name in dirnames
                    if name not in IGNORED_DIRS and not name.startswith(".merotec_")
                ]
                for filename in filenames:
                    path = Path(dirpath) / filename
                    if path.suffix.lower() in ignored_suffixes:
                        continue
                    try:
                        stat = path.stat()
                        rel = path.relative_to(root).as_posix()
                    except (OSError, ValueError):
                        continue
                    snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
                    if len(snapshot) >= max_files:
                        return snapshot
        except OSError:
            return snapshot
        return snapshot

    def detect_direct_workspace_changes(self, before_snapshot, max_items=24):
        if before_snapshot is None:
            return [], 0
        after_snapshot = self.snapshot_workspace_for_direct_actions()
        changes = []

        for rel, state in after_snapshot.items():
            previous = before_snapshot.get(rel)
            if previous is None:
                changes.append(("criado", rel))
            elif previous != state:
                changes.append(("alterado", rel))

        for rel in before_snapshot:
            if rel not in after_snapshot:
                changes.append(("removido", rel))

        changes.sort(key=lambda item: item[1])
        return changes[:max_items], len(changes)

    def register_direct_workspace_changes(self, changes, total, task_id=None):
        if not changes:
            return
        metrics = self.get_ai_task_metrics(task_id)
        metrics["real_actions"] = metrics.get("real_actions", 0) + 1
        metrics["direct_actions"] = metrics.get("direct_actions", 0) + 1

        workspace = str(Path(self.current_workspace).resolve())
        timestamp = datetime.now().isoformat(timespec="seconds")
        for kind, rel in changes[:12]:
            record = {
                "timestamp": timestamp,
                "workspace": workspace,
                "path": str((Path(workspace) / rel).resolve()),
                "rel": rel,
                "action": "CODEX_DIRECT",
                "summary": f"Arquivo {kind} diretamente pelo Codex.",
                "objective": self.active_ai_objective or "",
                "backup": "",
                "existed": kind != "criado",
                "undone": False,
            }
            self.change_history.append(record)

        self.change_history = self.change_history[-240:]
        self._save_change_history()
        self.log_agent(f"Codex alterou {total} arquivo(s) diretamente no workspace.")

    def format_direct_workspace_changes(self, changes, total):
        if not changes:
            return "Codex executou uma acao diretamente no workspace."
        lines = [f"- {rel} ({kind})" for kind, rel in changes[:10]]
        if total > len(lines):
            lines.append(f"- ... mais {total - len(lines)} arquivo(s)")
        return "Codex executou a tarefa diretamente no workspace.\n\nArquivos afetados:\n" + "\n".join(lines)

    def bool_setting_enabled(self, key, env_name=None, default=False):
        settings = getattr(self, "settings", None)
        if isinstance(settings, dict) and key in settings:
            value = settings.get(key)
        elif env_name:
            value = os.getenv(env_name, "")
            if value == "":
                return bool(default)
        else:
            return bool(default)

        if isinstance(value, bool):
            return value
        normalized = self.normalize_plain_text(str(value))
        if normalized in {"1", "true", "yes", "sim", "on", "enabled", "habilitado"}:
            return True
        if normalized in {"0", "false", "no", "nao", "off", "disabled", "desabilitado"}:
            return False
        return bool(default)

    def autonomous_unrestricted_mode_enabled(self):
        return self.bool_setting_enabled(
            "autonomous_unrestricted_mode",
            env_name="MEROTEC_AUTONOMOUS_UNRESTRICTED",
            default=False,
        )

    def codex_auto_approve_app_server_enabled(self):
        if self.bool_setting_enabled(
            "codex_auto_approve_app_server_requests",
            env_name="MEROTEC_CODEX_AUTO_APPROVE_APP_SERVER",
            default=False,
        ):
            return True
        return self.autonomous_unrestricted_mode_enabled()

    def show_retry_available(self):
        if not self.last_failed_ai_task:
            return
        self.btn_send.configure(state="normal", text="Reenviar")
        model_name = self.ai_assistant_display_name() if hasattr(self, "ai_assistant_display_name") else "IA"
        self.set_status(f"{model_name} indisponivel. Clique Reenviar para tentar de novo.", "busy")

    def resolve_workspace_path(self, requested_path):
        clean = requested_path.strip().strip("\"'")
        clean = clean.replace("\\", os.sep).replace("/", os.sep)

        workspace = Path(self.current_workspace).resolve()
        workspace_name = workspace.name
        if clean.startswith(workspace_name + os.sep):
            clean = clean[len(workspace_name) + 1 :]

        candidate = Path(clean)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            try:
                if os.path.commonpath([str(workspace), str(resolved)]) == str(workspace):
                    return resolved
            except ValueError:
                pass

            rebased = self._rebase_agent_path(candidate, workspace)
            if rebased:
                return rebased
        else:
            candidate = workspace / candidate
            resolved = candidate.resolve()

        if os.path.commonpath([str(workspace), str(resolved)]) != str(workspace):
            raise ValueError("Caminho fora do workspace bloqueado.")
        return resolved

    def _rebase_agent_path(self, candidate, workspace):
        parts = [part for part in candidate.parts if part not in {"\\", "/"}]
        workspace_name = workspace.name.lower()

        for index, part in enumerate(parts):
            if part.lower() == workspace_name:
                tail = parts[index + 1 :]
                return (workspace / Path(*tail)).resolve() if tail else workspace

        for index, part in enumerate(parts):
            if part.lower() == "ai_software_engineering":
                tail = parts[index + 1 :]
                if tail and tail[0].lower() == workspace_name:
                    tail = tail[1 :]
                return (workspace / Path(*tail)).resolve() if tail else workspace

        if parts and parts[-1].lower() == workspace_name:
            return workspace

        return None


    def iter_agent_action_lines(self, response_text, action_names=None):
        """Extrai ações curtas do protocolo, inclusive variantes comuns de chat web.

        A IDE sempre pede o formato ``[READ: arquivo]``, mas alguns chats —
        em diferentes chats web — retornam ``[READ] arquivo`` ou ``READ arquivo``.
        Antes da correção essas respostas eram exibidas como se a IDE estivesse
        trabalhando, porém não chegavam ao executor e a missão parava. O parser
        aceita as três formas somente quando a linha inteira é um comando, sem
        interpretar parágrafos comuns como ação.
        """
        if not response_text:
            return
        if action_names is None:
            action_names = (
                "READ", "SEARCH_TEXT", "WEB_SEARCH", "SCAN_TEXT",
                "FIX_MOJIBAKE", "UNDO", "EXECUTE", "EXECUTE_ADMIN",
                "OPEN_URL", "BROWSER_INSPECT", "BROWSER_CLICK",
                "BROWSER_TYPE", "BROWSER_SCROLL", "BROWSER_CHAT", "SCREENSHOT", "HUMAN_TEST",
            )
        allowed = {str(name).upper() for name in action_names}
        action_pattern = "|".join(re.escape(name) for name in sorted(allowed, key=len, reverse=True))
        lines = str(response_text).splitlines()
        in_fenced_block = False

        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if line.startswith("```"):
                in_fenced_block = not in_fenced_block
                continue
            if in_fenced_block or not line:
                continue

            adjacent_actions = re.fullmatch(
                rf"(?:\[(?:{action_pattern})(?:[ \t]*:[^\]\r\n]*)?\]){{2,}}",
                line,
                re.IGNORECASE,
            )
            if adjacent_actions:
                for match in re.finditer(
                    rf"\[({action_pattern})(?:[ \t]*:[ \t]*([^\]\r\n]*))?\]",
                    line,
                    re.IGNORECASE,
                ):
                    action = match.group(1).upper()
                    value = (match.group(2) or "").strip()
                    if action in allowed and value:
                        yield action, value
                continue

            # Formatos aceitos:
            #   [READ: main.py]      (canônico da IDE)
            #   [READ] main.py       (variante recorrente de chats web)
            #   READ: main.py / READ main.py
            bracketed = re.fullmatch(
                r"\[([A-Za-z_]+)(?:[ \t]*:[ \t]*(.*))?\][ \t]*(.*)",
                line,
                re.DOTALL,
            )
            if bracketed:
                action = bracketed.group(1).upper()
                if action not in allowed:
                    continue
                value = (bracketed.group(2) or bracketed.group(3) or "").strip()
                # Alguns provedores quebram a tag e o parâmetro em duas linhas.
                if not value and index + 1 < len(lines):
                    next_line = lines[index + 1].strip()
                    if next_line and not next_line.startswith("[") and not next_line.startswith("```"):
                        value = next_line
                if value:
                    yield action, value
                continue

            # Formas sem colchetes são limitadas a um nome de ação inteiro no
            # início da linha para evitar transformar explicações em comandos.
            plain = re.fullmatch(
                r"([A-Z][A-Z_]{1,})[ \t]*(?::|[ \t]+)[ \t]*(.+)",
                line,
            )
            if not plain:
                continue
            action = plain.group(1).upper()
            value = plain.group(2).strip()
            if action in allowed and value:
                yield action, value

    def extract_agent_action_values(self, response_text, action_name):
        values = []
        seen = set()
        for found_action, value in self.iter_agent_action_lines(response_text, (action_name,)):
            if found_action != action_name.upper():
                continue
            if value in seen:
                continue
            values.append(value)
            seen.add(value)
        return values

    def _extract_fenced_code_payload(self, content):
        """Retorna o código de um bloco Markdown sem perder indentação.

        Chats web às vezes envelopam um WRITE em explicações e um único bloco
        de código. Para a escrita, somente o bloco é conteúdo do arquivo.
        """
        raw = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
        match = re.search(r"```[^\n`]*\n(.*?)\n```", raw, re.DOTALL)
        if match:
            return match.group(1).strip("\n") + "\n"
        return raw.strip("\n") + ("\n" if raw.strip("\n") else "")

    def _looks_like_write_payload(self, raw_path, content):
        """Evita converter texto explicativo em sobrescrita de arquivo."""
        payload = str(content or "").strip()
        if not payload:
            return False
        if "```" in payload:
            return True

        suffix = Path(str(raw_path or "").strip()).suffix.lower()
        code_prefix = re.compile(
            r"(?m)^\s*(?:from\s+\w+|import\s+\w+|def\s+\w+|class\s+\w+|"
            r"async\s+def\s+\w+|[A-Za-z_]\w*\s*=|if\s+__name__\s*==|"
            r"<!(?:doctype)|<html|<\?xml|function\s+\w+|const\s+\w+|let\s+\w+|"
            r"var\s+\w+|#include|package\s+|public\s+(?:class|static)|"
            r"{\s*[\"']|\[\s*[\"'{])"
        )
        if code_prefix.search(payload):
            return True
        if suffix in {".md", ".txt", ".csv", ".ini", ".env", ".yml", ".yaml"}:
            return "\n" in payload or len(payload) >= 24
        return False

    def extract_write_blocks(self, response_text):
        """Extrai WRITEs fechados e recupera um WRITE cujo fechamento sumiu.

        O WebView pode entregar uma resposta completa em Markdown, mas alguns
        chats removem a linha ``[/WRITE]`` ao renderizar/copiar. O cabeçalho
        ainda identifica com segurança o arquivo; o conteúdo só é aceito se
        parecer código ou estiver dentro de um bloco Markdown. A validação por
        linguagem acontece antes de qualquer gravação em disco.
        """
        payload = str(response_text or "")
        closed_pattern = re.compile(
            r"\[WRITE\s*:\s*([^\]\r\n]+)\]\s*(.*?)\s*\[/WRITE\]",
            re.DOTALL | re.IGNORECASE,
        )
        blocks = []
        closed_spans = []
        for match in closed_pattern.finditer(payload):
            raw_path = match.group(1).strip()
            content = match.group(2)
            if raw_path and content.strip():
                blocks.append((raw_path, content, False))
                closed_spans.append(match.span())

        header_pattern = re.compile(
            r"(?im)^\s*\[WRITE\s*:\s*([^\]\r\n]+)\]\s*(?:\r?\n)?"
        )
        next_action_pattern = re.compile(
            r"(?im)^\s*\[(?:PATCH|WRITE|REPLACE|READ|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|"
            r"FIX_MOJIBAKE|UNDO|EXECUTE(?:_ADMIN)?|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|"
            r"BROWSER_TYPE|BROWSER_SCROLL|BROWSER_CHAT|SCREENSHOT|HUMAN_TEST|FINAL)\s*:",
        )
        seen = {(item[0].strip().lower(), item[1].strip()) for item in blocks}
        for header in header_pattern.finditer(payload):
            if any(start <= header.start() < end for start, end in closed_spans):
                continue
            raw_path = header.group(1).strip()
            body_start = header.end()
            next_action = next_action_pattern.search(payload, body_start)
            body_end = next_action.start() if next_action else len(payload)
            content = payload[body_start:body_end].strip("\r\n")
            if not raw_path or not self._looks_like_write_payload(raw_path, content):
                continue
            key = (raw_path.lower(), content.strip())
            if key in seen:
                continue
            seen.add(key)
            blocks.append((raw_path, content, True))
        return blocks

    def validate_write_content(self, path, content):
        """Valida formatos estruturados antes de substituir um arquivo existente."""
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            try:
                compile(content, str(path), "exec")
            except (SyntaxError, ValueError, TypeError) as exc:
                line = getattr(exc, "lineno", None)
                position = f" na linha {line}" if line else ""
                return False, f"Código Python inválido antes de salvar{position}: {getattr(exc, 'msg', str(exc))}"
        elif suffix == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return False, f"JSON inválido antes de salvar na linha {exc.lineno}: {exc.msg}"
        elif suffix in {".toml", ".tml"}:
            try:
                import tomllib
                tomllib.loads(content)
            except (ModuleNotFoundError, ValueError, TypeError) as exc:
                return False, f"TOML inválido antes de salvar: {exc}"
        return True, ""

    def extract_agent_action_names(self, response_text):
        """Identifica ações de linha e blocos multi-linha do protocolo da IDE."""
        names = {action for action, _value in self.iter_agent_action_lines(response_text)}
        payload = response_text or ""
        if self.extract_write_blocks(payload):
            names.add("WRITE")
        if re.search(r"\[REPLACE:\s*.+?\].*?\[/REPLACE\]", payload, re.DOTALL | re.IGNORECASE):
            names.add("REPLACE")
        if re.search(r"\[PATCH(?:\s*:\s*[^\]\r\n]+)?\].*?\[/PATCH\]", payload, re.DOTALL | re.IGNORECASE):
            names.add("PATCH")
        return names


    # MEROTEC_AUTONOMOUS_DELIVERY_V2
    def autonomous_delivery_enabled(self):
        return self.bool_setting_enabled(
            "autonomous_delivery_enabled",
            env_name="MEROTEC_AUTONOMOUS_DELIVERY",
            default=True,
        )

    def autonomous_visual_validation_enabled(self):
        return self.bool_setting_enabled(
            "autonomous_visual_validation_enabled",
            env_name="MEROTEC_AUTONOMOUS_VISUAL_VALIDATION",
            default=True,
        )

    def autonomous_development_loop_enabled(self):
        """Mantém a missão ativa até validação, cancelamento ou bloqueio real."""
        return self.bool_setting_enabled(
            "continuous_development_loop_enabled",
            env_name="MEROTEC_CONTINUOUS_DEVELOPMENT_LOOP",
            default=True,
        )

    def autonomous_max_repair_cycles(self):
        """0 significa ciclo contínuo; um limite só existe se o usuário configurar."""
        raw = getattr(self, "settings", {}).get("continuous_development_max_cycles", 0)
        try:
            return max(0, min(200, int(raw)))
        except (TypeError, ValueError):
            return 0

    def should_continue_development_loop(self, action_depth=0, task_id=None):
        if not self.autonomous_development_loop_enabled():
            return int(action_depth or 0) < 12
        limit = self.autonomous_max_repair_cycles()
        return limit == 0 or int(action_depth or 0) < limit

    def _autonomous_metrics(self, task_id=None):
        metrics = self.get_ai_task_metrics(task_id)
        metrics.setdefault("autonomous_delivery_active", False)
        metrics.setdefault("autonomous_validation_command", "")
        metrics.setdefault("autonomous_visual_pending", False)
        metrics.setdefault("autonomous_repair_cycles", 0)
        metrics.setdefault("autonomous_changed_files", [])
        return metrics

    def workspace_requires_visual_validation(self):
        if not self.autonomous_visual_validation_enabled():
            return False
        workspace = Path(self.current_workspace).resolve()
        if (workspace / "index.html").exists():
            return True
        package_json = workspace / "package.json"
        if package_json.exists():
            try:
                package = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
                if any(name in scripts for name in ("dev", "start", "preview")):
                    return True
            except (OSError, json.JSONDecodeError):
                pass
        for name in ("main.py", "app.py"):
            candidate = workspace / name
            if not candidate.exists():
                continue
            try:
                source = candidate.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if any(marker in source for marker in (
                "tkinter", "customtkinter", "pyqt", "pyside", "kivy",
                "pygame", "flet", "flask", "fastapi", "streamlit",
            )):
                return True
        return False

    def infer_autonomous_validation_command(self, objective=None):
        workspace = Path(self.current_workspace).resolve()
        if (workspace / "index.html").exists():
            # Validação estática curta que termina sozinha. O teste visual,
            # quando aplicável, é iniciado depois dela.
            check = (
                "from pathlib import Path; import sys; "
                "s=Path('index.html').read_text(encoding='utf-8',errors='replace').lower(); "
                "need=('!doctype','html','body'); bad=[x for x in need if x not in s]; "
                "print('HTML static validation OK' if not bad else 'HTML static validation FAILED: '+', '.join(bad)); "
                "sys.exit(0 if not bad else 1)"
            )
            return f'"{sys.executable}" -c "{check}"'
        return self.infer_default_validation_command(objective or self.active_ai_objective or "")

    def arm_autonomous_delivery(self, task_objective=None, task_id=None, changed_paths=None):
        if not self.autonomous_delivery_enabled():
            return False
        metrics = self._autonomous_metrics(task_id)
        metrics["autonomous_delivery_active"] = True
        metrics["autonomous_validation_command"] = self.infer_autonomous_validation_command(task_objective)
        metrics["autonomous_visual_pending"] = self.workspace_requires_visual_validation()
        metrics["autonomous_changed_files"] = list(changed_paths or [])
        return True

    def start_autonomous_delivery_validation(self, task_objective=None, action_depth=0, task_id=None):
        metrics = self._autonomous_metrics(task_id)
        if not metrics.get("autonomous_delivery_active"):
            return False
        command = str(metrics.get("autonomous_validation_command") or "").strip()
        if command:
            self.add_chat_message(
                "Sistema",
                "Ciclo autônomo: alteração aplicada. Iniciando validação automática.",
            )
            self.log_agent(f"Ciclo autônomo iniciou validação: {command}")
            self.mark_ai_active_action("execute", task_id=task_id)
            self._agent_execute(
                command,
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return True
        if metrics.get("autonomous_visual_pending"):
            metrics["autonomous_visual_pending"] = False
            self.add_chat_message("Sistema", "Ciclo autônomo: iniciando teste visual automático.")
            self.mark_ai_active_action("human_test", task_id=task_id)
            self._agent_human_test(
                "auto",
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return True
        metrics["autonomous_delivery_active"] = False
        return False

    def advance_autonomous_delivery_after_validation(self, command, task_objective=None, action_depth=0, task_id=None):
        metrics = self._autonomous_metrics(task_id)
        if not metrics.get("autonomous_delivery_active"):
            return False
        expected = str(metrics.get("autonomous_validation_command") or "").strip()
        if expected and command.strip() != expected:
            return False
        if metrics.get("autonomous_visual_pending"):
            metrics["autonomous_visual_pending"] = False
            self.add_chat_message(
                "Sistema",
                "Validação automática aprovada. Iniciando teste visual do projeto.",
            )
            self.log_agent("Ciclo autônomo avançou para HUMAN_TEST.")
            self.mark_ai_active_action("human_test", task_id=task_id)
            self._agent_human_test(
                "auto",
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )
            return True
        metrics["autonomous_delivery_active"] = False
        self.add_chat_message(
            "Sistema",
            "Ciclo autônomo concluído: alteração criada e validação automática aprovada.",
        )
        self.set_status("Alteração validada automaticamente.", "ready")
        return True

    def can_continue_autonomous_repair(self, command, output, returncode, task_objective=None, task_id=None):
        """Decide se o erro de validação deve voltar ao mesmo agente.

        O padrão é contínuo. A IDE só para por cancelamento, indisponibilidade do
        provedor ou por um limite explicitamente configurado pelo usuário.
        """
        metrics = self._autonomous_metrics(task_id)
        if not metrics.get("autonomous_delivery_active"):
            return True
        metrics["autonomous_repair_cycles"] += 1
        current = metrics["autonomous_repair_cycles"]
        limit = self.autonomous_max_repair_cycles()
        if limit and current > limit:
            metrics["autonomous_delivery_active"] = False
            self.add_chat_message(
                "Erro",
                "O ciclo contínuo foi pausado porque atingiu o limite configurado pelo usuário "
                f"({limit} ciclos). O último erro permanece no Terminal Local.",
            )
            self.set_status("Limite configurado do ciclo atingido.", "warning")
            self.log_agent("Ciclo de correção pausado pelo limite configurado.")
            return False
        cadence = f"{current}/{limit}" if limit else f"{current}/contínuo"
        self.add_chat_message(
            "Sistema",
            f"Validação falhou. A IDE vai devolver o diagnóstico ao mesmo chat e continuar a correção ({cadence}).",
        )
        self.log_agent(f"Ciclo contínuo de correção {cadence}: código {returncode}.")
        return True

    def continue_after_mutation_failure(
        self, response_text, target_paths, task_objective=None, action_depth=0, task_id=None
    ):
        """Não encerra REPLACE/WRITE inválido: devolve o estado atual e continua."""
        if not self.autonomous_development_loop_enabled() or not self.should_continue_development_loop(action_depth, task_id):
            return False
        metrics = self._autonomous_metrics(task_id)
        metrics["autonomous_repair_cycles"] += 1
        current = metrics["autonomous_repair_cycles"]
        limit = self.autonomous_max_repair_cycles()
        if limit and current > limit:
            self.add_chat_message("Erro", "O ciclo foi pausado pelo limite de tentativas configurado pelo usuário.")
            self.set_status("Limite configurado do ciclo atingido.", "warning")
            return False

        contexts = []
        seen = set()
        for raw_path in target_paths or []:
            raw_path = str(raw_path or "").strip()
            if not raw_path or raw_path in seen:
                continue
            seen.add(raw_path)
            try:
                candidate = self.resolve_workspace_path(raw_path)
                if candidate.exists() and candidate.is_file():
                    body = candidate.read_text(encoding="utf-8", errors="replace")
                    try:
                        relative = candidate.relative_to(Path(self.current_workspace).resolve()).as_posix()
                    except ValueError:
                        relative = candidate.name
                    if len(body) > 56000:
                        total_lines = body.count("\n") + 1
                        contexts.append(
                            f"ARQUIVO GRANDE ATUAL: {relative} ({total_lines} linhas).\n"
                            "Nao reescreva o arquivo inteiro. Localize o alvo com [SEARCH_TEXT] ou leia somente o intervalo necessario, por exemplo "
                            f"[READ: {relative} | linhas 120-260], e aplique [REPLACE] ou [PATCH] incremental."
                        )
                    else:
                        contexts.append(f"ARQUIVO ATUAL: {relative}\n```\n{body}\n```")
            except Exception as exc:
                contexts.append(f"Não foi possível reler `{raw_path}`: {exc}")

        cadence = f"{current}/{limit}" if limit else f"{current}/contínuo"
        self.add_chat_message(
            "Sistema",
            "A alteração não foi aplicada, mas a missão continua. A IDE devolveu o estado atual ao mesmo chat "
            f"para uma nova correção ({cadence}).",
        )
        context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or ''}\n\n"
            "A ÚLTIMA EDIÇÃO NÃO FOI APLICADA. Não declare conclusão e não abra uma conversa nova. "
            "Use o conteúdo atual abaixo, corrija o contexto desatualizado e responda com a próxima ação necessária da IDE. "
            "PROTOCOLO INCREMENTAL V9: para arquivo grande, nao exija reescrita completa; use [READ: caminho | linhas inicio-fim] ou [SEARCH_TEXT] e aplique [REPLACE] ou [PATCH] local. "
            "Para reescrever de propósito, use [WRITE: caminho] ... [/WRITE].\n\n"
            f"RESPOSTA ANTERIOR QUE FALHOU:\n{response_text[:6000]}\n\n"
            + "\n\n".join(contexts or ["A IDE não identificou o arquivo alvo; leia o arquivo antes de editar."])
        )
        self._run_ai_task(
            "A edição anterior falhou. Continue a mesma missão aplicando uma correção compatível com o arquivo atual.",
            extra_context=context,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )
        return True


    # MEROTEC_VISUAL_AUTONOMY_V3
    def _agent_apply_patch(self, patch_text, task_id=None, task_objective=None):
        # Formato: [PATCH] *** Begin Patch ... *** End Patch [/PATCH]
        raw = str(patch_text or "").strip()
        raw = re.sub(r"^```(?:diff|patch)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
        if "*** Begin Patch" not in raw or "*** End Patch" not in raw:
            self.add_chat_message(
                "Erro",
                "PATCH recusado: use *** Begin Patch / *** Update File / *** End Patch dentro de [PATCH].",
            )
            return []

        body = raw.split("*** Begin Patch", 1)[1].split("*** End Patch", 1)[0]
        sections = []
        current_kind = ""
        current_path = ""
        current_lines = []

        def flush_section():
            if current_kind and current_path:
                sections.append((current_kind, current_path, list(current_lines)))

        for line in body.splitlines():
            match = re.match(r"^\*\*\* (Update File|Add File|Delete File):\s*(.+?)\s*$", line)
            if match:
                flush_section()
                current_kind = match.group(1).lower().replace(" ", "_")
                current_path = match.group(2).strip()
                current_lines = []
            elif current_kind:
                current_lines.append(line)
        flush_section()
        if not sections:
            self.add_chat_message("Erro", "PATCH recusado: nenhum arquivo foi identificado.")
            return []

        changed = []
        try:
            for kind, raw_path, lines in sections:
                path = self.resolve_workspace_path(raw_path)
                if kind == "add_file":
                    if path.exists():
                        raise ValueError(f"O arquivo novo já existe: {raw_path}")
                    content = "\n".join(line[1:] for line in lines if line.startswith("+"))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.record_file_change_snapshot(path, "PATCH", f"Patch criou {raw_path}.")
                    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
                elif kind == "delete_file":
                    if not path.exists():
                        raise ValueError(f"O arquivo para remover não existe: {raw_path}")
                    self.record_file_change_snapshot(path, "PATCH", f"Patch removeu {raw_path}.")
                    path.unlink()
                else:
                    if not path.exists():
                        raise ValueError(f"Arquivo para atualizar não existe: {raw_path}")
                    source = path.read_text(encoding="utf-8", errors="replace")
                    updated = self._apply_openai_style_patch_hunks(source, lines, raw_path)
                    if updated == source:
                        raise ValueError(f"PATCH não alterou conteúdo em {raw_path}.")
                    self.record_file_change_snapshot(path, "PATCH", f"Patch atualizou {raw_path}.")
                    path.write_text(updated, encoding="utf-8")
                changed.append(raw_path)
        except Exception as exc:
            self.add_chat_message("Erro", f"PATCH não aplicado: {exc}")
            self.log_agent(f"Falha ao aplicar PATCH: {exc}")
            return []

        self.load_workspace_files()
        self.add_chat_message("Sistema", "PATCH aplicado: " + ", ".join(changed))
        self.log_agent("PATCH aplicado no workspace: " + ", ".join(changed))
        return changed

    def _apply_openai_style_patch_hunks(self, source, lines, raw_path):
        source_lines = source.splitlines()
        has_final_newline = source.endswith("\n")
        hunks = []
        current = []
        seen_hunk = False
        for line in lines:
            if line.startswith("@@"):
                if current:
                    hunks.append(current)
                current = []
                seen_hunk = True
                continue
            if seen_hunk:
                current.append(line)
        if current:
            hunks.append(current)
        if not hunks:
            raise ValueError(f"PATCH de {raw_path} não contém @@ com trecho de troca.")

        for hunk in hunks:
            old_lines = [line[1:] for line in hunk if line.startswith((" ", "-"))]
            new_lines = [line[1:] for line in hunk if line.startswith((" ", "+"))]
            if not old_lines:
                raise ValueError(f"PATCH de {raw_path} não possui contexto para inserir com segurança.")
            found_at = -1
            for index in range(0, len(source_lines) - len(old_lines) + 1):
                if source_lines[index:index + len(old_lines)] == old_lines:
                    found_at = index
                    break
            if found_at < 0:
                preview = "\n".join(old_lines[:4])
                raise ValueError(f"Contexto do PATCH não encontrado em {raw_path}: {preview[:180]}")
            source_lines[found_at:found_at + len(old_lines)] = new_lines

        result = "\n".join(source_lines)
        return result + "\n" if has_final_newline else result

    def parse_and_execute_agent_actions(self, response_text, task_objective=None, action_depth=0, task_id=None, direct_action_happened=False):
        if not response_text:
            return
        if self.is_task_cancelled(task_id):
            self.log_agent("Acao da IA ignorada porque a tarefa foi cancelada.")
            return

        mutation_attempted = False
        mutation_succeeded = False
        mutation_paths = []
        mutation_targets = []
        patch_blocks = re.findall(r"\[PATCH(?:\s*:\s*[^\]\r\n]+)?\](.*?)\[/PATCH\]", response_text, re.DOTALL | re.IGNORECASE)
        has_action = bool(patch_blocks)
        if "[PATCH" in response_text.upper() and not patch_blocks:
            self.add_chat_message("Erro", "A IA enviou um PATCH incompleto. Use [PATCH] ... [/PATCH].")
        for patch_text in patch_blocks:
            mutation_attempted = True
            mutation_targets.extend(
                item.strip()
                for item in re.findall(r"^\*\*\* (?:Update File|Add File|Delete File):\s*(.+?)\s*$", patch_text, re.MULTILINE)
                if item.strip()
            )
            self.mark_ai_active_action("patch", task_id=task_id)
            patched_paths = self._agent_apply_patch(patch_text, task_id=task_id, task_objective=task_objective)
            if patched_paths:
                mutation_succeeded = True
                mutation_paths.extend(patched_paths)
        write_blocks = self.extract_write_blocks(response_text)
        has_action = has_action or bool(write_blocks)
        if "[WRITE:" in response_text.upper() and not write_blocks:
            self.add_chat_message(
                "Erro",
                "A IA iniciou um WRITE, mas não trouxe conteúdo de código utilizável. O arquivo atual foi preservado.",
            )
        for raw_path, content, recovered_without_closing_tag in write_blocks:
            mutation_attempted = True
            mutation_targets.append(raw_path.strip())
            self.mark_ai_active_action("write", task_id=task_id)
            if recovered_without_closing_tag:
                self.log_agent(
                    f"WRITE de {raw_path.strip()} recuperado sem a linha final [/WRITE]; validando antes de salvar."
                )
            if self._agent_write(raw_path, content, task_id=task_id, task_objective=task_objective):
                mutation_succeeded = True
                mutation_paths.append(raw_path.strip())

        replace_blocks = re.findall(r"\[REPLACE:\s*(.+?)\](.*?)\[/REPLACE\]", response_text, re.DOTALL | re.IGNORECASE)
        has_action = has_action or bool(replace_blocks)
        if "[REPLACE:" in response_text.upper() and not replace_blocks:
            self.add_chat_message(
                "Erro",
                "A IA enviou um REPLACE incompleto. Ela precisa mandar [REPLACE: arquivo] [OLD]...[/OLD] [NEW]...[/NEW] [/REPLACE].",
            )
        for raw_path, block in replace_blocks:
            mutation_attempted = True
            mutation_targets.append(raw_path.strip())
            old_match = re.search(r"\[OLD\](.*?)\[/OLD\]", block, re.DOTALL | re.IGNORECASE)
            new_match = re.search(r"\[NEW\](.*?)\[/NEW\]", block, re.DOTALL | re.IGNORECASE)
            if not old_match or not new_match:
                self.add_chat_message(
                    "Erro",
                    "REPLACE precisa conter [OLD] trecho atual [/OLD] e [NEW] trecho novo [/NEW].",
                )
                continue
            self.mark_ai_active_action("replace", task_id=task_id)
            if self._agent_replace(
                raw_path,
                old_match.group(1),
                new_match.group(1),
                task_id=task_id,
                task_objective=task_objective,
            ):
                mutation_succeeded = True
                mutation_paths.append(raw_path.strip())

        if mutation_succeeded and hasattr(self, "settings"):
            self.arm_autonomous_delivery(
                task_objective=task_objective or self.active_ai_objective,
                task_id=task_id,
                changed_paths=mutation_paths,
            )

        unrestricted_mode = self.autonomous_unrestricted_mode_enabled()

        fix_paths = self.extract_agent_action_values(response_text, "FIX_MOJIBAKE")
        if (
            fix_paths
            and not unrestricted_mode
            and not self.objective_allows_text_repair(task_objective or self.active_ai_objective or "")
        ):
            self.redirect_unrelated_text_repair(
                "FIX_MOJIBAKE",
                response_text,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return
        has_action = has_action or bool(fix_paths)
        for raw_path in fix_paths:
            self.mark_ai_active_action("write", task_id=task_id)
            self._agent_fix_mojibake(raw_path, task_id=task_id)

        read_paths = self.extract_agent_action_values(response_text, "READ")
        has_action = has_action or bool(read_paths)
        if (
            not direct_action_happened
            and not self.task_has_real_action(task_id)
            and self.claims_concrete_result_without_real_action(response_text, task_objective=task_objective)
        ):
            self.warn_claimed_action_without_real_action(
                response_text,
                task_objective=task_objective,
                task_id=task_id,
            )
        if read_paths:
            if self.should_use_project_map_instead_of_mass_read(read_paths, task_objective):
                self.redirect_mass_read_to_project_map(
                    read_paths,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                return
            if self.should_block_passive_ai_action("READ", read_paths, task_objective, action_depth, task_id):
                return
            self._agent_read_many(read_paths, task_objective=task_objective, action_depth=action_depth, task_id=task_id)
            if (
                self.extract_agent_action_values(response_text, "EXECUTE")
                or self.extract_agent_action_values(response_text, "EXECUTE_ADMIN")
            ):
                self.add_chat_message(
                    "Merotec IA",
                    "Leitura priorizada antes da execucao, para agir com base no arquivo correto.",
                )
            if self.extract_agent_action_values(response_text, "SEARCH_TEXT"):
                self.add_chat_message(
                    "Merotec IA",
                    "Leitura priorizada antes da busca, para editar com base concreta.",
                )
            return

        search_requests = self.extract_agent_action_values(response_text, "SEARCH_TEXT")
        has_action = has_action or bool(search_requests)
        if search_requests:
            if self.should_block_passive_ai_action("SEARCH_TEXT", search_requests, task_objective, action_depth, task_id):
                return
            self._agent_search_text_many(
                search_requests,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return

        web_search_requests = self.extract_agent_action_values(response_text, "WEB_SEARCH")
        has_action = has_action or bool(web_search_requests)
        if web_search_requests:
            if self.should_block_passive_ai_action("WEB_SEARCH", web_search_requests, task_objective, action_depth, task_id):
                return
            self.mark_ai_active_action("web_search", task_id=task_id)
            self._agent_web_search_many(
                web_search_requests,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return

        scan_paths = self.extract_agent_action_values(response_text, "SCAN_TEXT")
        if (
            scan_paths
            and not unrestricted_mode
            and not self.objective_allows_text_repair(task_objective or self.active_ai_objective or "")
        ):
            self.redirect_unrelated_text_repair(
                "SCAN_TEXT",
                response_text,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return
        has_action = has_action or bool(scan_paths)
        if scan_paths:
            if self.should_block_passive_ai_action("SCAN_TEXT", scan_paths, task_objective, action_depth, task_id):
                return
            self._agent_scan_text_many(
                scan_paths,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return

        undo_paths = self.extract_agent_action_values(response_text, "UNDO")
        has_action = has_action or bool(undo_paths)
        for raw_path in undo_paths:
            self.mark_ai_active_action("write", task_id=task_id)
            self._agent_undo(raw_path)

        open_urls = self.extract_agent_action_values(response_text, "OPEN_URL")
        has_action = has_action or bool(open_urls)
        for raw_url in open_urls:
            self.mark_ai_active_action("open_url", task_id=task_id)
            self._agent_open_url(raw_url)

        browser_actions = (
            ("BROWSER_INSPECT", "inspect"),
            ("BROWSER_CLICK", "click"),
            ("BROWSER_TYPE", "type"),
            ("BROWSER_SCROLL", "scroll"),
        )
        for tag_name, action_name in browser_actions:
            requests = self.extract_agent_action_values(response_text, tag_name)
            has_action = has_action or bool(requests)
            if requests:
                self.mark_ai_active_action("browser", task_id=task_id)
                self._agent_browser_action(
                    action_name,
                    requests[0],
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                return

        browser_chat_requests = self.extract_agent_action_values(response_text, "BROWSER_CHAT")
        has_action = has_action or bool(browser_chat_requests)
        if browser_chat_requests:
            self.mark_ai_active_action("browser", task_id=task_id)
            self._agent_browser_chat(
                browser_chat_requests[0],
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return

        screenshot_requests = self.extract_agent_action_values(response_text, "SCREENSHOT")
        has_action = has_action or bool(screenshot_requests)
        for request in screenshot_requests:
            self.mark_ai_active_action("screenshot", task_id=task_id)
            self._agent_screenshot(
                request,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )

        human_test_requests = self.extract_agent_action_values(response_text, "HUMAN_TEST")
        has_action = has_action or bool(human_test_requests)
        for request in human_test_requests:
            self.mark_ai_active_action("human_test", task_id=task_id)
            self._agent_human_test(
                request,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )

        admin_execute_commands = self.extract_agent_action_values(response_text, "EXECUTE_ADMIN")
        has_action = has_action or bool(admin_execute_commands)
        for command in admin_execute_commands:
            if self.reject_placeholder_execute_action("EXECUTE_ADMIN", command):
                continue
            self.mark_ai_active_action("execute", task_id=task_id)
            self._agent_execute_admin(command, task_objective=task_objective, action_depth=action_depth, task_id=task_id)

        execute_commands = self.extract_agent_action_values(response_text, "EXECUTE")
        has_action = has_action or bool(execute_commands)
        for command in execute_commands:
            if self.reject_placeholder_execute_action("EXECUTE", command):
                continue
            # Mesmo no modo irrestrito, usar EXECUTE apenas para imprimir o
            # conteúdo de um arquivo quebra o ciclo: o shell retorna 0 e o
            # Chat Web não recebe o código. Transforme em READ, que envia o
            # contexto de volta ao mesmo chat e exige a próxima ação concreta.
            if self.is_source_read_command(command):
                if self.should_block_passive_ai_action(
                    "EXECUTE_SOURCE_READ", [command], task_objective, action_depth, task_id
                ):
                    continue
                self.redirect_source_read_command_to_read(
                    command,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                return
            if self.should_route_execute_to_human_test(command, task_objective):
                self.mark_ai_active_action("human_test", task_id=task_id)
                self._agent_human_test(
                    command,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                    requested_command=command,
                )
                continue
            if not unrestricted_mode and self.is_file_mutation_command(command):
                self.mark_ai_active_action("redirect", task_id=task_id)
                self.redirect_mutation_command_to_write(
                    command,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                continue
            if not unrestricted_mode and self.is_file_inspection_command(command):
                if self.should_block_passive_ai_action("EXECUTE_INSPECTION", [command], task_objective, action_depth, task_id):
                    continue
                self.redirect_inspection_command_to_scan(
                    command,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                continue
            self.mark_ai_active_action("execute", task_id=task_id)
            self._agent_execute(command, task_objective=task_objective, action_depth=action_depth, task_id=task_id)

        followup_action_requested = any(
            (
                fix_paths,
                undo_paths,
                open_urls,
                screenshot_requests,
                human_test_requests,
                admin_execute_commands,
                execute_commands,
            )
        )
        explicit_validation = bool(
            execute_commands or admin_execute_commands or human_test_requests
        )
        if mutation_succeeded and not explicit_validation and self.should_continue_development_loop(action_depth, task_id):
            if self.start_autonomous_delivery_validation(
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth,
                task_id=task_id,
            ):
                return

        if (
            mutation_attempted
            and mutation_succeeded
            and not followup_action_requested
            and hasattr(self, "settings")
            and self.should_continue_development_loop(action_depth, task_id)
            and self.autonomous_visual_test_required(task_objective)
        ):
            changed = ", ".join(mutation_paths) if mutation_paths else "arquivo alterado"
            self.add_chat_message(
                "Sistema",
                "Alteração aplicada. A IDE abriu automaticamente o teste visual no navegador dedicado antes de concluir.",
            )
            self.log_agent(f"Teste visual automático após alteração: {changed}")
            self.mark_ai_active_action("human_test", task_id=task_id)
            self._agent_human_test(
                "auto",
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )
            return

        if mutation_attempted and not mutation_succeeded and not followup_action_requested:
            if self.continue_after_mutation_failure(
                response_text,
                mutation_targets,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            ):
                return
            self.add_chat_message(
                "Sistema",
                "Alteração não aplicada. Os arquivos foram preservados e o ciclo foi pausado por cancelamento ou limite configurado.",
            )
            self.set_status("Alteração recusada; ciclo pausado.", "warning")
            return

        if mutation_attempted and mutation_succeeded and not followup_action_requested and self.should_continue_development_loop(action_depth, task_id):
            changed = ", ".join(mutation_paths) if mutation_paths else "arquivo"
            self.add_chat_message(
                "Sistema",
                "Alteração aplicada. A IDE continuará somente com a validação necessária.",
            )
            self._run_ai_task(
                "Valide o projeto apos a alteracao aplicada com uma acao real e conclua objetivamente.",
                extra_context=(
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or ''}\n\n"
                    f"Arquivos alterados: {changed}."
                ),
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )
            return

        if direct_action_happened and not has_action:
            self.load_workspace_files()
            return

        if not has_action and self.looks_like_unexecuted_intention(response_text) and self.is_analysis_only_objective(task_objective or self.active_ai_objective or ""):
            self.redirect_unexecuted_analysis_to_report(
                response_text,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return

        if not has_action and self.looks_like_unexecuted_intention(response_text):
            if self.try_execute_implied_validation(
                response_text,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            ):
                return
            if not self.should_continue_development_loop(action_depth, task_id):
                self.add_chat_message(
                    "Erro",
                    "A IA continuou respondendo sem ação real e o ciclo atingiu o limite configurado.",
                )
                self.set_status("Sem ação real; ciclo pausado.", "warning")
                return
            self.add_chat_message("Sistema", "A IA respondeu com intencao, mas nao executou uma acao. Reforcando a tarefa.")
            self._run_ai_task(
                "A resposta anterior nao executou nenhuma acao. Continue a missao agora usando uma tag real da IDE "
                "([READ], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], [FIX_MOJIBAKE], [REPLACE], [WRITE], EXECUTE/EXECUTE_ADMIN ja preenchido, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]) "
                "usando comando real quando houver execucao, ou entregue uma conclusao final direta se a tarefa ja estiver respondida.",
                extra_context=(
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or ''}\n\n"
                    f"Resposta anterior sem acao:\n{response_text}"
                ),
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )

    def mark_ai_active_action(self, action_name=None, task_id=None):
        self.ai_passive_action_count = 0
        if not action_name:
            return
        metrics = self.get_ai_task_metrics(task_id)
        metrics["real_actions"] = metrics.get("real_actions", 0) + 1
        key = f"{action_name.lower()}_actions"
        metrics[key] = metrics.get(key, 0) + 1

    def task_has_real_action(self, task_id=None):
        metrics = self.get_ai_task_metrics(task_id)
        action_keys = (
            "real_actions",
            "direct_actions",
            "write_actions",
            "replace_actions",
            "execute_actions",
            "human_test_actions",
            "screenshot_actions",
            "open_url_actions",
        )
        return any(metrics.get(key, 0) > 0 for key in action_keys)

    def try_execute_implied_validation(self, response_text, task_objective=None, action_depth=0, task_id=None):
        normalized = self.normalize_plain_text(response_text or "")
        if not any(term in normalized for term in ("validar", "teste", "testar", "executar", "rodar", "erro real", "analise estatica")):
            return False

        metrics = self.get_ai_task_metrics(task_id)
        file_actions = metrics.get("write_actions", 0) + metrics.get("replace_actions", 0)
        objective = task_objective or self.active_ai_objective or ""
        if self.objective_requests_visual_human_test(response_text + "\n" + objective):
            already_visual_tested_same_state = (
                metrics.get("visual_test_actions", 0) >= 1
                and file_actions <= metrics.get("visual_test_file_actions", 0)
            )
            if not already_visual_tested_same_state:
                metrics["visual_test_actions"] = metrics.get("visual_test_actions", 0) + 1
                metrics["visual_test_file_actions"] = file_actions
                self.add_chat_message(
                    "Sistema",
                    "A IDE converteu a intencao em teste visual real com print.",
                )
                self.log_agent("Intencao convertida em HUMAN_TEST.")
                self.mark_ai_active_action("human_test", task_id=task_id)
                self._agent_human_test(
                    "auto",
                    task_objective=objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                return True

        command = self.infer_default_validation_command(objective)
        if not command:
            return False

        already_validated_same_state = (
            metrics.get("auto_validation_actions", 0) >= 1
            and file_actions <= metrics.get("auto_validation_file_actions", 0)
        )
        if already_validated_same_state:
            return False

        metrics["auto_validation_actions"] = metrics.get("auto_validation_actions", 0) + 1
        metrics["auto_validation_file_actions"] = file_actions
        self.add_chat_message(
            "Sistema",
            f"A IDE converteu a intencao sem acao em validacao real: {command}",
        )
        self.log_agent(f"Intencao convertida em EXECUTE: {command}")
        self.mark_ai_active_action("execute", task_id=task_id)
        self._agent_execute(
            command,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth,
            task_id=task_id,
        )
        return True

    def infer_default_validation_command(self, objective):
        workspace = Path(self.current_workspace).resolve()
        normalized = self.normalize_plain_text(objective or "")
        if (workspace / "pubspec.yaml").exists():
            if any(term in normalized for term in ("executar", "rodar", "abrir app", "run")):
                return "flutter run -d windows"
            return "flutter analyze"

        package_json = workspace / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                scripts = {}
            if "test" in scripts:
                return "npm test"
            if "build" in scripts:
                return "npm run build"
            return "npm install --dry-run"

        if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists() or list(workspace.glob("*.py")):
            return f'"{sys.executable}" -m compileall .'

        if (workspace / "index.html").exists():
            return "python -m http.server 8000"

        return ""

    def objective_requests_visual_human_test(self, text):
        normalized = self.normalize_plain_text(text or "")
        visual_terms = (
            "teste real",
            "testar real",
            "como usuario",
            "humano",
            "jogo",
            "game",
            "print",
            "screenshot",
            "capturar tela",
            "tirar print",
            "visual",
            "tela",
            "interface",
            "usar",
            "utilizar",
        )
        return any(term in normalized for term in visual_terms)

    def should_route_execute_to_human_test(self, command, task_objective=None):
        objective = task_objective or self.active_ai_objective or ""
        if not self.objective_requests_visual_human_test(objective):
            return False
        if self.command_launches_python_main(command):
            return True
        normalized_command = self.normalize_plain_text(command or "")
        visual_commands = (
            "flutter run",
            "npm run dev",
            "npm start",
            "http.server",
            "python -m webbrowser",
            "cmd /c start",
        )
        return any(item in normalized_command for item in visual_commands)

    def command_launches_python_main(self, command):
        command_text = str(command or "")
        normalized = self.normalize_plain_text(command_text)
        has_python_runner = re.search(r"\b(?:python|pythonw|py)(?:\.exe)?\b", normalized)
        has_main_py = re.search(r"(?:^|[\\/\\s\"'])main\.py\b", command_text, re.IGNORECASE) or re.search(
            r"\bmain\.py\b",
            normalized,
        )
        return bool(has_python_runner and has_main_py)

    def _agent_human_test(self, request, task_objective=None, action_depth=0, task_id=None, requested_command=None):
        if self.is_task_cancelled(task_id):
            return
        plan = self.build_human_test_plan(request, task_objective, requested_command=requested_command)
        if not plan:
            self.add_chat_message("Erro", "Nao encontrei um alvo visual seguro para testar.")
            return

        command_display = plan["display"]
        self.log_agent(f"Teste visual real iniciado: {command_display}")
        self.add_chat_message(
            "Sistema",
            f"Teste visual real iniciado. A IDE vai abrir, esperar a tela e capturar um print: {command_display}",
        )
        self.append_to_term(f"\n> teste visual real via IA\n{plan['cwd']}> {command_display}\n")
        self.tabview.set("Terminal Local")
        self.set_ai_busy(True)
        self.set_ai_activity("IA testando como usuario")
        self.set_terminal_busy(True, f"Teste visual: {command_display[:70]}")

        def run():
            process = None
            output_lines = []
            line_queue = queue.Queue()

            try:
                popen_kwargs = {
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT,
                    "cwd": str(plan["cwd"]),
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                }
                if plan.get("env"):
                    popen_kwargs["env"] = {
                        **os.environ,
                        **{str(key): str(value) for key, value in plan["env"].items()},
                    }
                if plan["shell"]:
                    process = subprocess.Popen(plan["command"], shell=True, **popen_kwargs)
                else:
                    process = subprocess.Popen(
                        plan["command"],
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        **popen_kwargs,
                    )
                plan["capture_process_pid"] = process.pid
                self.register_terminal_process(process, f"Teste visual IA: {command_display}")

                def read_output():
                    try:
                        for line in process.stdout:
                            line_queue.put(line)
                    finally:
                        line_queue.put(None)

                threading.Thread(target=read_output, daemon=True).start()
                url = plan.get("url", "")
                started_at = time.time()
                ready = False
                while time.time() - started_at < plan["ready_timeout"]:
                    if self.is_task_cancelled(task_id):
                        return
                    if process.poll() is not None:
                        break
                    try:
                        line = line_queue.get(timeout=0.5)
                    except queue.Empty:
                        line = ""
                    if line is None:
                        break
                    if line:
                        output_lines.append(line)
                        self.append_to_term(line)
                        found_url = self.extract_first_local_url(line)
                        if found_url:
                            url = found_url
                    if url and self.is_url_ready(url):
                        ready = True
                        break
                    if self.human_test_window_is_ready(plan):
                        ready = True
                        break
                    if self.human_test_output_is_ready("".join(output_lines[-20:])):
                        ready = True
                        break

                if process.poll() is not None and process.returncode not in (0, None) and not ready:
                    # O processo pode encerrar antes da thread leitora terminar de
                    # esvaziar stderr. Drene a fila por um curto periodo para que
                    # o Chat Web receba o traceback real, e nao apenas a primeira
                    # linha ou um diagnostico generico.
                    drain_deadline = time.monotonic() + 1.5
                    while time.monotonic() < drain_deadline:
                        try:
                            line = line_queue.get(timeout=0.12)
                        except queue.Empty:
                            if process.poll() is not None:
                                continue
                            break
                        if line is None:
                            break
                        if line:
                            output_lines.append(line)
                            self.append_to_term(line)
                    output = "".join(output_lines)
                    self.append_to_term(f"\n[teste visual falhou com codigo {process.returncode}]\n")
                    diagnostic = self.build_command_failure_diagnostic(command_display, output, process.returncode)
                    metrics = self.get_ai_task_metrics(task_id)
                    metrics["requires_error_correction"] = {
                        "kind": "human_test",
                        "command": command_display,
                        "returncode": int(process.returncode),
                        "output_tail": output[-12000:],
                        "created_at": time.time(),
                    }
                    self.add_chat_message(
                        "Sistema",
                        "Teste visual falhou. A IDE vai enviar o traceback real e o codigo de saida ao mesmo Chat Web para a proxima correcao.",
                    )
                    context = (
                        "DIAGNOSTICO DE FALHA GERADO PELA IDE — PRIORIDADE MAXIMA:\n"
                        f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Testar visualmente'}\n\n"
                        f"Teste visual tentou executar: {command_display}\n"
                        f"Codigo de saida: {process.returncode}\n"
                        f"{diagnostic}\n"
                        f"SAIDA REAL COMPLETA DISPONIVEL (trecho final):\n```\n{output[-12000:]}\n```\n\n"
                        "O erro acima ocorreu de verdade no processo local. Nao peca para o usuario copiar o log; "
                        "use-o como fonte de verdade, leia o arquivo indicado e emita a proxima tag da IDE para corrigir a causa antes de repetir o teste."
                    )
                    self.set_ai_busy(False)
                    self._run_ai_task(
                        "O teste visual falhou com diagnostico real. Continue a mesma missao corrigindo a causa concreta.",
                        extra_context=context,
                        task_objective=task_objective or self.active_ai_objective,
                        action_depth=action_depth + 1,
                        task_id=task_id,
                    )
                    return

                if url:
                    self._agent_open_url(url)
                    time.sleep(plan["screenshot_delay"])
                else:
                    time.sleep(max(2.0, plan["screenshot_delay"]))

                if self.is_task_cancelled(task_id):
                    return

                image = self.grab_human_test_image(plan)
                screenshot_path = self.save_agent_screenshot(image)
                self.get_ai_task_metrics(task_id).pop("requires_error_correction", None)
                self.log_agent(f"Print do teste visual capturado: {screenshot_path.name}")
                self.add_chat_image_message("Merotec AI", screenshot_path, "")
                output = "".join(output_lines)
                context = (
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Testar visualmente'}\n\n"
                    "A IDE executou um teste visual real, abriu/esperou a interface e capturou um print.\n"
                    f"Comando/alvo: {command_display}\n"
                    f"URL: {url or 'sem URL; tela capturada do desktop'}\n"
                    f"Print: {screenshot_path.name}\n"
                    f"Saida relevante:\n```\n{output[-7000:]}\n```\n\n"
                    "O print e a evidencia primaria deste teste e precisa ser analisado antes de qualquer conclusao. "
                    "Procure primeiro por banners, dialogs, tracebacks, mensagens de erro, tela em branco, layout quebrado, botao fora do lugar, "
                    "fluxo confuso, jogo injogavel, controle invertido, asset faltando ou comportamento incoerente. "
                    "Nao declare que a tela esta boa se nao conseguir enxergar o anexo; nesse caso use [READ] para investigar o codigo em vez de inventar uma validacao. "
                    "Se houver problema, transcreva a mensagem visivel/diagnostico, corrija com [READ], [REPLACE] ou [WRITE] e depois rode novo [HUMAN_TEST: auto]. "
                    "Se estiver bom, entregue uma conclusao objetiva com o que foi validado visualmente."
                )
                self.set_ai_busy(False)
                self._run_ai_task(
                    "Analise o print do teste visual real e corrija se encontrar problema.",
                    image_path=str(screenshot_path),
                    extra_context=context,
                    task_objective=task_objective or self.active_ai_objective,
                    action_depth=action_depth + 1,
                    task_id=task_id,
                )

                while process.poll() is None:
                    if self.is_task_cancelled(task_id):
                        return
                    try:
                        line = line_queue.get(timeout=0.7)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    if line:
                        self.append_to_term(line)
            except Exception as exc:
                self.add_chat_message("Erro", f"Falha no teste visual real: {exc}")
            finally:
                if process and process.poll() is not None:
                    self.unregister_terminal_process(process)
                if not self.has_terminal_processes():
                    self.set_terminal_busy(False)
                self.set_ai_busy(False)

        threading.Thread(target=run, daemon=True).start()

    def resolve_human_test_workspace(self, workspace, objective):
        workspace = Path(workspace).resolve()
        current_kind = self.detect_run_kind(workspace)
        browser_terms = ("browser", "canvas", "game", "html", "jogo", "navegador", "pagina", "site", "web")
        wants_browser_target = any(term in objective for term in browser_terms)

        try:
            candidates = []
            for candidate in self.find_runnable_workspaces():
                resolved = Path(candidate).resolve()
                try:
                    if os.path.commonpath([str(workspace), str(resolved)]) == str(workspace):
                        candidates.append(resolved)
                except ValueError:
                    continue
        except Exception:
            candidates = []

        if current_kind and not (current_kind == "python" and wants_browser_target):
            return workspace

        if wants_browser_target:
            for candidate in candidates:
                if candidate != workspace and self.detect_run_kind(candidate) in {"html", "node", "flutter"}:
                    return candidate

        if not current_kind and candidates:
            return candidates[0]

        return workspace

    def build_human_test_plan(self, request, task_objective=None, requested_command=None):
        workspace = Path(self.current_workspace).resolve()
        objective = self.normalize_plain_text((request or "") + "\n" + (task_objective or self.active_ai_objective or ""))
        if requested_command:
            self_target = self.resolve_requested_self_test_target(requested_command, workspace)
            if self_target:
                return self.build_merotec_self_human_test_plan(workspace, self_target)
            if self.workspace_looks_like_merotec_self(workspace) and self.command_launches_python_main(requested_command):
                return self.build_merotec_self_human_test_plan(workspace, workspace / "main.py")
            url = self.extract_first_local_url(requested_command)
            if not url and "web-port" in requested_command:
                match = re.search(r"--web-port[=\s]+(\d+)", requested_command)
                if match:
                    url = f"http://127.0.0.1:{match.group(1)}/"
            return {
                "command": requested_command,
                "display": requested_command,
                "cwd": workspace,
                "shell": True,
                "url": url,
                "ready_timeout": 110,
                "screenshot_delay": 5.0,
            }

        workspace = self.resolve_human_test_workspace(workspace, objective)
        kind = self.detect_run_kind(workspace)

        if kind == "flutter":
            if (workspace / "web").exists() and any(term in objective for term in ("web", "chrome", "print", "visual", "tela", "jogo", "game", "teste real")):
                port = self.find_available_port(8000) or 8000
                command = f"flutter run -d chrome --web-port={port}"
                return {
                    "command": command,
                    "display": command,
                    "cwd": workspace,
                    "shell": True,
                    "url": f"http://127.0.0.1:{port}/",
                    "ready_timeout": 130,
                    "screenshot_delay": 5.0,
                }
            command = "flutter run -d windows"
            return {
                "command": command,
                "display": command,
                "cwd": workspace,
                "shell": True,
                "url": "",
                "ready_timeout": 120,
                "screenshot_delay": 6.0,
            }

        if kind == "html":
            port = self.find_available_port(8000) or 8000
            command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
            url = self.pick_http_server_test_url(workspace, f"http://127.0.0.1:{port}/")
            return {
                "command": command,
                "display": f"{Path(sys.executable).name} -m http.server {port} --bind 127.0.0.1",
                "cwd": workspace,
                "shell": False,
                "url": url,
                "ready_timeout": 25,
                "screenshot_delay": 2.5,
            }

        if kind == "node":
            package_json = workspace / "package.json"
            scripts = {}
            try:
                data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
            if "dev" in scripts:
                command = "npm run dev"
            elif "start" in scripts:
                command = "npm start"
            else:
                command = "npm test"
            return {
                "command": command,
                "display": command,
                "cwd": workspace,
                "shell": True,
                "url": "",
                "ready_timeout": 80,
                "screenshot_delay": 4.0,
            }

        if kind == "python":
            target = workspace / "app.py" if (workspace / "app.py").exists() else workspace / "main.py"
            is_merotec_self_target = (
                target.name == "main.py"
                and (workspace / "modules" / "agent_actions.py").exists()
                and (workspace / "modules" / "app_constants.py").exists()
            )
            if target.resolve() == (PROJECT_ROOT / "main.py").resolve() or is_merotec_self_target:
                return self.build_merotec_self_human_test_plan(workspace, target)
            command = f'"{sys.executable}" "{target.name}"'
            return {
                "command": command,
                "display": command,
                "cwd": workspace,
                "shell": True,
                "url": "",
                "ready_timeout": 35,
                "screenshot_delay": 4.0,
            }

        return None

    def build_merotec_self_human_test_plan(self, workspace, target):
        workspace = Path(workspace).resolve()
        target = Path(target)
        title_suffix = f" - teste visual {os.getpid()}-{time.time_ns()}"
        return {
            "command": [sys.executable, target.name],
            "display": f"{Path(sys.executable).name} {target.name} (nova instancia de teste)",
            "cwd": workspace,
            "shell": False,
            "url": "",
            "ready_timeout": 35,
            "screenshot_delay": 4.0,
            "window_capture_timeout": 12.0,
            "require_target_window": True,
            "env": {
                "MEROTEC_FORCE_NEW_INSTANCE": "1",
                "MEROTEC_HUMAN_TEST_INSTANCE": "1",
                "MEROTEC_VISUAL_TEST_INSTANCE": "1",
                "MEROTEC_INSTANCE_TITLE_SUFFIX": title_suffix,
            },
        }

    def resolve_requested_self_test_target(self, command, workspace):
        workspace = Path(workspace).resolve()
        if not self.workspace_looks_like_merotec_self(workspace):
            return None

        command_text = str(command or "")
        py_tokens = re.findall(r'"([^"]+\.py)"|\'([^\']+\.py)\'|([^\s"\']+\.py)', command_text, re.IGNORECASE)
        for token_group in py_tokens:
            token = next((item for item in token_group if item), "")
            if not token:
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                if candidate.resolve() == (workspace / "main.py").resolve():
                    return workspace / "main.py"
            except OSError:
                continue
        normalized_command = self.normalize_plain_text(command_text)
        if (
            (workspace / "main.py").exists()
            and self.command_launches_python_main(command_text)
        ):
            return workspace / "main.py"
        return None

    def workspace_looks_like_merotec_self(self, workspace):
        workspace = Path(workspace)
        return (
            (workspace / "main.py").exists()
            and (workspace / "modules" / "agent_actions.py").exists()
            and (workspace / "modules" / "app_constants.py").exists()
        )

    def grab_human_test_image(self, plan):
        plan = plan or {}
        capture_pid = plan.get("capture_process_pid")
        if capture_pid:
            image = self.grab_window_image_by_pid(
                capture_pid,
                timeout=plan.get("window_capture_timeout", 8.0),
            )
            if image is not None:
                return image
            self.log_agent(f"Janela alvo do teste visual nao encontrada pelo PID: {capture_pid}")
        capture_title = plan.get("capture_window_title", "")
        if capture_title:
            image = self.grab_window_image_by_title(
                capture_title,
                timeout=plan.get("window_capture_timeout", 8.0),
            )
            if image is not None:
                return image
            self.log_agent(f"Janela alvo do teste visual nao encontrada para captura: {capture_title}")
        if plan.get("require_target_window"):
            raise RuntimeError("Janela alvo do teste visual nao foi encontrada; captura do desktop foi bloqueada.")
        return ImageGrab.grab()

    def grab_window_image_by_title(self, title, timeout=8.0):
        if os.name != "nt" or not title:
            return None

        deadline = time.time() + max(0.0, float(timeout or 0.0))
        while time.time() <= deadline:
            hwnd = self.find_window_handle_by_title(title)
            if hwnd:
                bbox = self.window_bbox_from_handle(hwnd)
                if bbox:
                    return ImageGrab.grab(bbox=bbox)
            time.sleep(0.25)
        return None

    def human_test_window_is_ready(self, plan):
        if os.name != "nt" or not plan:
            return False
        capture_pid = plan.get("capture_process_pid")
        if capture_pid and self.find_window_handle_by_pid(capture_pid):
            return True
        capture_title = plan.get("capture_window_title", "")
        if capture_title and self.find_window_handle_by_title(capture_title):
            return True
        return False

    def find_window_handle_by_title(self, title):
        try:
            user32 = ctypes.windll.user32
            exact = user32.FindWindowW(None, str(title))
            if exact and user32.IsWindowVisible(exact):
                return exact

            matches = []

            def enum_callback(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                window_title = buffer.value
                if window_title == title or title in window_title:
                    matches.append(hwnd)
                    return False
                return True

            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(callback_type(enum_callback), 0)
            return matches[0] if matches else None
        except Exception:
            return None

    def grab_window_image_by_pid(self, pid, timeout=8.0):
        if os.name != "nt" or not pid:
            return None

        deadline = time.time() + max(0.0, float(timeout or 0.0))
        while time.time() <= deadline:
            hwnd = self.find_window_handle_by_pid(pid)
            if hwnd:
                bbox = self.window_bbox_from_handle(hwnd)
                if bbox:
                    return ImageGrab.grab(bbox=bbox)
            time.sleep(0.25)
        return None

    def find_window_handle_by_pid(self, pid):
        try:
            pid = int(pid)
            user32 = ctypes.windll.user32
            target_pids = self.collect_process_tree_pids(pid)
            matches = []

            def enum_callback(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                process_id = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                if process_id.value not in target_pids:
                    return True
                rect = ctypes.wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width < 80 or height < 80:
                    return True
                matches.append((width * height, hwnd))
                return True

            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(callback_type(enum_callback), 0)
            if not matches:
                return None
            matches.sort(reverse=True)
            return matches[0][1]
        except Exception:
            return None

    def collect_process_tree_pids(self, root_pid, parent_map=None):
        try:
            root_pid = int(root_pid)
        except (TypeError, ValueError):
            return set()

        if parent_map is None:
            parent_map = self.snapshot_process_parent_map()

        tree = {root_pid}
        changed = True
        while changed:
            changed = False
            for child_pid, parent_pid in dict(parent_map).items():
                try:
                    child_pid = int(child_pid)
                    parent_pid = int(parent_pid)
                except (TypeError, ValueError):
                    continue
                if parent_pid in tree and child_pid not in tree:
                    tree.add(child_pid)
                    changed = True
        return tree

    def snapshot_process_parent_map(self):
        if os.name != "nt":
            return {}
        try:
            kernel32 = ctypes.windll.kernel32
            th32cs_snapprocess = 0x00000002
            invalid_handle = ctypes.c_void_p(-1).value

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", ctypes.wintypes.DWORD),
                    ("cntUsage", ctypes.wintypes.DWORD),
                    ("th32ProcessID", ctypes.wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", ctypes.wintypes.DWORD),
                    ("cntThreads", ctypes.wintypes.DWORD),
                    ("th32ParentProcessID", ctypes.wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
            if snapshot in (0, invalid_handle):
                return {}
            try:
                parent_map = {}
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    return {}
                while True:
                    parent_map[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
                return parent_map
            finally:
                kernel32.CloseHandle(snapshot)
        except Exception:
            return {}

    def window_bbox_from_handle(self, hwnd):
        try:
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.35)

            rect = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            bbox = (rect.left, rect.top, rect.right, rect.bottom)
            if bbox[2] - bbox[0] < 80 or bbox[3] - bbox[1] < 80:
                return None
            return bbox
        except Exception:
            return None

    def extract_first_local_url(self, text):
        match = re.search(r"https?://(?:localhost|127\.0\.0\.1|\[?::1\]?)(?::\d+)?/[^\s\"')\]]*", text or "", re.IGNORECASE)
        return match.group(0) if match else ""

    def is_url_ready(self, url):
        if not url:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                status = getattr(response, "status", 200)
                return 200 <= status < 500
        except Exception:
            return False

    def human_test_output_is_ready(self, output):
        normalized = self.normalize_plain_text(output or "")
        ready_terms = (
            "flutter run key commands",
            "a dart vm service",
            "compiled successfully",
            "built build",
            "local:",
            "ready in",
            "serving",
            "listening",
            "http://localhost",
            "http://127.0.0.1",
        )
        return any(term in normalized for term in ready_terms)

    def redirect_unexecuted_analysis_to_report(self, response_text, task_objective=None, action_depth=0, task_id=None):
        objective = task_objective or self.active_ai_objective or "Analisar projeto"
        metrics = self.get_ai_task_metrics(task_id)
        metrics["forced_decisions"] += 1

        if metrics["forced_decisions"] > 1 or action_depth >= 3:
            self.add_chat_message(
                "Merotec AI",
                self.build_project_analysis_fallback_report(objective),
            )
            self.set_status("Analise concluida com mapa local.", "ready")
            self.log_agent("Analise finalizada por fallback local para evitar ciclo de promessa.")
            return

        self.add_chat_message(
            "Sistema",
            "A IA comecou a prometer uma analise em vez de entregar o resultado. A IDE vai fornecer o mapa consolidado e pedir o relatorio final agora.",
        )
        context = (
            f"MISSAO ORIGINAL:\n{objective}\n\n"
            "A resposta anterior foi apenas promessa/planejamento, sem resultado final:\n"
            f"{response_text}\n\n"
            "MAPA CONSOLIDADO DO PROJETO GERADO PELA IDE:\n"
            f"{self.build_project_intelligence_context(deep=True)}\n\n"
            "PROXIMA RESPOSTA OBRIGATORIA:\n"
            "- Entregue a analise detalhada agora.\n"
            "- Nao diga que vai mapear, ler, validar ou verificar.\n"
            "- Nao use tags de acao nesta resposta.\n"
            "- Organize em: resumo, arquitetura, fluxo principal, arquivos importantes, riscos, oportunidades e proximas implementacoes."
        )
        self._run_ai_task(
            "Entregue a analise detalhada agora, sem novas acoes internas.",
            extra_context=context,
            task_objective=objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def build_project_analysis_fallback_report(self, objective):
        return (
            "**Analise Do Projeto**\n\n"
            "A IA entrou em ciclo de promessa/leitura, entao a IDE fechou a analise com o mapa local consolidado para nao deixar voce sem resultado.\n\n"
            f"Objetivo: {objective}\n\n"
            f"{self.build_project_intelligence_context(deep=True)}\n\n"
            "**Proximos Passos Recomendados**\n"
            "- Escolher uma implementacao por vez e pedir para aplicar diretamente.\n"
            "- Depois de cada mudanca, executar teste/build pelo Terminal Local.\n"
            "- Para mudancas em jogo/app existente, preferir alteracoes pequenas para preservar a logica atual."
        )

    def get_ai_task_metrics(self, task_id=None):
        task_key = task_id if task_id is not None else self.current_task_id
        return self.ai_task_metrics.setdefault(
            task_key,
            {
                "read_rounds": 0,
                "read_files": 0,
                "read_paths": {},
                "search_rounds": 0,
                "searches": 0,
                "forced_decisions": 0,
                "passive_actions": 0,
                "real_actions": 0,
                "direct_actions": 0,
                "write_actions": 0,
                "replace_actions": 0,
                "execute_actions": 0,
                "protocol_violations": 0,
                "auto_validation_actions": 0,
                "auto_validation_file_actions": 0,
                "visual_test_actions": 0,
                "visual_test_file_actions": 0,
            },
        )

    def objective_requires_concrete_change(self, objective):
        normalized = self.normalize_plain_text(objective or self.active_ai_objective or "")
        if self.is_analysis_only_objective(normalized):
            return False
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        change_terms = {
            "adicione",
            "adicionar",
            "ajuste",
            "ajustar",
            "altere",
            "alterar",
            "corrija",
            "corrigir",
            "crie",
            "criar",
            "desenvolva",
            "desenvolver",
            "execute",
            "executar",
            "faca",
            "fazer",
            "implemente",
            "implementar",
            "integre",
            "integrar",
            "melhore",
            "melhorar",
            "remova",
            "remover",
            "resolva",
            "resolver",
            "rode",
            "rodar",
            "teste",
            "testar",
        }
        return bool(words & change_terms)

    def is_analysis_only_objective(self, objective):
        normalized = self.normalize_plain_text(objective or self.active_ai_objective or "")
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        analysis_terms = {
            "analise",
            "analisa",
            "analisar",
            "analize",
            "analizar",
            "avaliacao",
            "avaliar",
            "diagnostico",
            "entenda",
            "entender",
            "mapeie",
            "mapear",
            "planejar",
            "planejamento",
            "revise",
            "revisar",
        }
        future_terms = {"depois", "futuro", "futuramente", "posterior", "proximas", "proximos"}
        immediate_change_terms = {
            "agora",
            "aplique",
            "aplicar",
            "corrija",
            "corrigir",
            "edite",
            "editar",
            "implemente",
            "implementar",
            "integre",
            "integrar",
            "modifique",
            "modificar",
        }
        if not (words & analysis_terms):
            return False
        planning_markers = (
            "para que possamos",
            "para depois",
            "depois",
            "antes de",
            "para continuar",
            "futuramente",
            "proximas implementacoes",
            "proximos passos",
        )
        if any(marker in normalized for marker in planning_markers):
            return True
        if words & immediate_change_terms:
            return False
        return True if words & future_terms else any(term in normalized for term in ("analise detalhada", "analise completa", "analisar o aplicativo", "analisar projeto"))

    def passive_limits_for_objective(self, objective):
        if self.is_analysis_only_objective(objective):
            return {"rounds": 8, "files": 24, "passive": 40, "search_rounds": 8}
        if self.objective_requires_concrete_change(objective):
            return {"rounds": 10, "files": 30, "passive": 50, "search_rounds": 10}
        normalized = self.normalize_plain_text(objective or "")
        if any(scope in normalized for scope in ("projeto", "aplicativo", "app", "sistema")) and any(term in normalized for term in ("analise", "analisa", "analisar", "analize", "analizar")):
            return {"rounds": 8, "files": 24, "passive": 40, "search_rounds": 8}
        return {"rounds": 12, "files": 36, "passive": 60, "search_rounds": 12}

    def should_force_concrete_action(self, action_name, requests, task_objective=None, action_depth=0, task_id=None):
        metrics = self.get_ai_task_metrics(task_id)
        limits = self.passive_limits_for_objective(task_objective or self.active_ai_objective or "")
        request_count = max(1, len(requests or []))
        metrics["passive_actions"] += request_count

        normalized_action = (action_name or "").upper()
        if normalized_action == "READ":
            metrics["read_rounds"] += 1
            metrics["read_files"] += request_count
            for raw in requests or []:
                clean, _line_range = self.parse_agent_read_request(raw)
                key = clean.strip().replace("\\", "/").lower()
                metrics["read_paths"][key] = metrics["read_paths"].get(key, 0) + 1
        elif normalized_action in {"SEARCH_TEXT", "WEB_SEARCH", "SCAN_TEXT", "EXECUTE_INSPECTION"}:
            metrics["search_rounds"] += 1
            metrics["searches"] += request_count

        repeated_reads = [
            path for path, count in metrics.get("read_paths", {}).items()
            if path and count >= 8
        ]
        too_many_reads = metrics["read_rounds"] > limits["rounds"] or metrics["read_files"] > limits["files"]
        too_many_searches = metrics["search_rounds"] > limits["search_rounds"]
        too_many_passive = metrics["passive_actions"] > limits["passive"]
        too_deep = action_depth >= 18 and normalized_action in {"READ", "SEARCH_TEXT", "WEB_SEARCH", "SCAN_TEXT", "EXECUTE_INSPECTION"}
        if repeated_reads or too_many_reads or too_many_searches or too_many_passive or too_deep:
            self.log_agent(
                f"Aviso: alto volume de contexto permitido ({normalized_action}); "
                f"leituras={metrics.get('read_files', 0)}, buscas={metrics.get('searches', 0)}"
            )
        return False

    def force_concrete_action_after_context(self, action_name, requests, task_objective=None, action_depth=0, task_id=None):
        metrics = self.get_ai_task_metrics(task_id)
        metrics["forced_decisions"] += 1
        objective = task_objective or self.active_ai_objective or "Continuar tarefa atual"
        requested_text = ", ".join(str(item).strip() for item in (requests or [])[:6])
        if metrics["forced_decisions"] > 2:
            self.add_chat_message(
                "Sistema",
                "A IDE interrompeu novas leituras repetidas nesta missao. Envie um comando mais especifico ou clique Reenviar para continuar com uma nova tentativa.",
            )
            self.set_status("Leitura repetida interrompida.", "ready")
            self.log_agent(f"Leitura repetida interrompida definitivamente: {action_name}")
            return

        self.log_agent(
            f"Acao passiva convertida em decisao concreta: {action_name}; "
            f"leituras={metrics.get('read_files', 0)}, rodadas={metrics.get('read_rounds', 0)}"
        )
        if self.is_analysis_only_objective(objective):
            self.add_chat_message(
                "Sistema",
                "A IDE substituiu a leitura em massa por um mapa do projeto. Agora a IA deve entregar a analise, sem executar nem editar.",
            )
            context = (
                f"MISSAO ORIGINAL:\n{objective}\n\n"
                "CONTROLE DA IDE:\n"
                f"A IA tentou ler contexto demais via {action_name}: {requested_text or 'sem detalhes'}.\n"
                "Como esta missao e de analise/planejamento, a IDE gerou um mapa consolidado do projeto para evitar loop de leitura.\n\n"
                f"{self.build_project_intelligence_context(deep=True)}\n\n"
                "PROXIMA RESPOSTA OBRIGATORIA:\n"
                "- Entregue a analise completa em texto agora.\n"
                "- Inclua arquitetura, fluxo principal, arquivos importantes, riscos, pontos fortes e proximas implementacoes recomendadas.\n"
                "- Nao use [READ], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], EXECUTE, [REPLACE] ou [WRITE] nesta resposta.\n"
                "- Nao diga que vai analisar: apresente o resultado."
            )
            self._run_ai_task(
                "Entregue agora a analise detalhada do projeto usando o mapa consolidado.",
                extra_context=context,
                task_objective=objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )
            return

        self.add_chat_message(
            "Sistema",
            "A IDE ja entregou contexto suficiente e bloqueou novas leituras repetidas. Agora a IA deve aplicar uma acao concreta.",
        )
        context = (
            f"MISSAO ORIGINAL:\n{objective}\n\n"
            "CONTROLE DA IDE:\n"
            f"A IA pediu mais contexto via {action_name}: {requested_text or 'sem detalhes'}.\n"
            f"Ja houve {metrics.get('read_rounds', 0)} rodada(s) de leitura, "
            f"{metrics.get('read_files', 0)} arquivo(s) solicitados e "
            f"{metrics.get('search_rounds', 0)} rodada(s) de busca nesta missao.\n"
            "A partir de agora, a IDE nao vai aceitar nova leitura/busca como proxima acao desta mesma missao.\n\n"
            "PROXIMA RESPOSTA OBRIGATORIA:\n"
            "- Se a missao pede implementar/corrigir, responda com [REPLACE] pequeno e exato ou [WRITE] apenas para arquivo novo/reescrita pedida.\n"
            "- Se a missao pede executar/testar, responda com uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: comando de teste da stack detectada].\n"
            "- Se precisa validar visualmente, responda com [HUMAN_TEST: auto].\n"
            "- Se ja sabe que nao da para fazer com seguranca, entregue conclusao curta dizendo exatamente o bloqueio.\n"
            "- Nao use [READ], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT] ou comando de inspecao na proxima resposta."
        )
        self._run_ai_task(
            "Pare de ler e execute a proxima acao concreta da missao.",
            extra_context=context,
            task_objective=objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def should_block_passive_ai_action(self, action_name, requests, task_objective=None, action_depth=0, task_id=None):
        request_count = len(requests or [])
        self.ai_passive_action_count += max(1, request_count)
        if self.should_force_concrete_action(action_name, requests, task_objective, action_depth, task_id):
            self.force_concrete_action_after_context(action_name, requests, task_objective, action_depth, task_id)
            return True
        if self.ai_passive_action_count > self.max_ai_passive_actions:
            self.log_agent(
                f"Aviso: muitas acoes de contexto seguidas ({action_name}, {self.ai_passive_action_count}), sem bloquear."
            )
        return False

    def should_use_project_map_instead_of_mass_read(self, read_paths, task_objective=None):
        if self.autonomous_unrestricted_mode_enabled():
            return False
        objective = self.normalize_plain_text(task_objective or self.active_ai_objective or "")
        if not any(scope in objective for scope in ("projeto", "aplicativo", "app", "sistema", "arquitetura")):
            return False
        analysis_words = {"analise", "analisa", "analisar", "analize", "analizar", "avaliar", "revise", "revisar", "mapeie", "mapear", "diagnostico"}
        words = set(re.findall(r"[a-z0-9_]+", objective))
        if not (words & analysis_words):
            return False
        unique_files = set()
        for raw in read_paths or []:
            clean, _line_range = self.parse_agent_read_request(raw)
            unique_files.add(clean.strip().replace("\\", "/"))
        return len(unique_files) > 6

    def redirect_mass_read_to_project_map(self, read_paths, task_objective=None, action_depth=0, task_id=None):
        objective = task_objective or self.active_ai_objective or "Analisar projeto"
        count = len(set((item or "").strip() for item in read_paths or []))
        self.log_agent(f"Leitura em massa substituida por mapa de projeto: {count} arquivo(s)")
        self.add_chat_message(
            "Merotec AI",
            f"A IDE trocou {count} leituras por um mapa arquitetural do projeto para evitar travamento.",
        )
        context = (
            f"MISSAO ORIGINAL:\n{objective}\n\n"
            f"A IA pediu {count} leituras de arquivos. Para projeto grande, isso trava a analise.\n"
            "A IDE gerou um mapa consolidado com subprojetos, marcadores e arquivos-chave.\n\n"
            f"{self.build_project_intelligence_context(deep=True)}\n\n"
            "PROXIMA RESPOSTA:\n"
            "- Entregue uma analise arquitetural objetiva do projeto.\n"
            "- Liste subprojetos detectados e suas funcoes provaveis.\n"
            "- Aponte riscos, pontos fortes e proximos passos.\n"
            "- Leia no maximo 1 ou 2 arquivos especificos se realmente indispensavel.\n"
            "- Nao faca nova lista grande de [READ].\n"
            "- Se a missao for so analise/planejamento, nao execute testes nem edite arquivos; entregue o relatorio agora."
        )
        self._run_ai_task(
            "Entregue a analise usando o mapa arquitetural consolidado, sem leitura em massa.",
            extra_context=context,
            task_objective=objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def _agent_open_url(self, raw_url):
        url = (raw_url or "").strip().strip("\"'")
        if not url:
            self.add_chat_message("Erro", "OPEN_URL veio sem URL.")
            return
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = "http://" + url
        try:
            opener = getattr(self, "open_internal_browser", None)
            opened_url = opener(url, source="IA") if callable(opener) else ""
            if not opened_url:
                webbrowser.open(url, new=1)
                opened_url = url
                self.add_chat_message("Merotec AI", f"Abri a pagina no navegador externo: {opened_url}")
            else:
                self.add_chat_message("Merotec AI", f"Abri a pagina no navegador interno da IDE: {opened_url}")
            remember = getattr(self, "remember_internal_browser_chat_url", None)
            if callable(remember) and ("chat" in opened_url.lower() or "gemini" in opened_url.lower()):
                remember(opened_url)
            self.log_agent(f"URL aberta pela IA: {opened_url}")
        except Exception as exc:
            self.add_chat_message("Erro", f"Nao consegui abrir a URL: {exc}")

    def _browser_url_is_local(self):
        url = str(getattr(self, "internal_browser_url", "") or "")
        try:
            host = (urllib.parse.urlparse(url).hostname or "").lower()
        except ValueError:
            host = ""
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or url.startswith("file:")

    def web_chat_remote_actions_enabled(self):
        """Lê a permissão do perfil Chat Web sem depender do perfil de IA ativo."""
        settings = getattr(self, "settings", {})
        profile = (
            settings.get("ai_profiles", {}).get("web_chat", {})
            if isinstance(settings, dict)
            else {}
        )
        value = profile.get(
            "web_chat_allow_remote_actions",
            settings.get("web_chat_allow_remote_actions", False) if isinstance(settings, dict) else False,
        )
        if isinstance(value, bool):
            return value
        return self.normalize_plain_text(str(value)) in {
            "1", "true", "yes", "sim", "on", "enabled", "habilitado"
        }

    def _approve_remote_browser_action(self, action, target, value=""):
        if self._browser_url_is_local() or action in {"inspect", "scroll"}:
            return True
        element = (getattr(self, "browser_element_catalog", None) or {}).get(target, {})
        label = self.normalize_plain_text(
            " ".join(
                str(element.get(key) or "")
                for key in ("label", "type", "role", "tag")
            )
        )
        sensitive_markers = {
            "senha", "password", "passcode", "otp", "codigo de verificacao",
            "token", "api key", "chave api", "secret", "segredo", "cvv",
            "cartao", "card number", "pagamento", "payment", "billing",
            "comprar", "purchase", "excluir conta", "delete account",
            "remover conta", "permissao", "permission",
        }
        sensitive = any(marker in label for marker in sensitive_markers)
        if self.autonomous_unrestricted_mode_enabled() and not sensitive:
            self.log_agent(f"Interacao web autoaprovada no modo irrestrito: {action} {target}")
            return True
        title = "Autorizar interacao no site?"
        if action == "type":
            detail = f"A IA quer digitar no elemento {target}:\n\n{value[:600]}"
        else:
            detail = f"A IA quer clicar no elemento {target}."
        message = (
            f"{detail}\n\nDestino: {getattr(self, 'internal_browser_url', '')}\n\n"
            "Sites podem enviar dados ou produzir efeitos externos. Autorizar esta interacao?"
        )
        if threading.current_thread() is threading.main_thread():
            return bool(messagebox.askyesno(title, message))
        result_queue = queue.Queue(maxsize=1)

        def ask():
            try:
                result_queue.put(bool(messagebox.askyesno(title, message)))
            except Exception as exc:
                result_queue.put(exc)

        self.after(0, ask)
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return bool(result)

    def _agent_browser_action(self, action, raw_request, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            return
        requester = getattr(self, "request_internal_browser_action", None)
        if not callable(requester):
            self.add_chat_message("Erro", "O navegador atual nao oferece automacao.")
            return

        raw = str(raw_request or "").strip()
        parts = [part.strip() for part in raw.split("|", 1)]
        target = parts[0] if parts else ""
        value = parts[1] if len(parts) > 1 else ""
        if action == "inspect":
            target = "page"
        elif action == "scroll":
            target = target.lower() if target.lower() in {"up", "down"} else "down"
        elif not target:
            self.add_chat_message("Erro", f"BROWSER_{action.upper()} veio sem elemento alvo.")
            return
        if action == "type" and len(parts) < 2:
            self.add_chat_message("Erro", "BROWSER_TYPE precisa usar: elemento | texto.")
            return
        if not self._approve_remote_browser_action(action, target, value):
            self.add_chat_message("Sistema", "Interacao remota do navegador cancelada pelo usuario.")
            return

        payload = {"target": target}
        if action == "type":
            payload["value"] = value

        def completed(event):
            if self.is_task_cancelled(task_id):
                return
            result = event.get("result", "")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    pass
            if isinstance(result, dict) and result.get("url"):
                self.internal_browser_url = str(result["url"])
                remember = getattr(self, "remember_internal_browser_chat_url", None)
                if callable(remember):
                    remember(self.internal_browser_url, str(result.get("title") or ""))
            if action == "inspect" and isinstance(result, dict):
                elements = result.get("elements") or []
                self.browser_element_catalog = {
                    str(item.get("ref")): item
                    for item in elements
                    if item.get("ref")
                }
                element_lines = [
                    f"{item.get('ref')}: <{item.get('tag')}> {item.get('label') or '(sem rotulo)'}"
                    + (f" -> {item.get('href')}" if item.get("href") else "")
                    for item in elements[:120]
                ]
                evidence = (
                    f"URL: {result.get('url', '')}\nTitulo: {result.get('title', '')}\n\n"
                    f"TEXTO VISIVEL:\n{str(result.get('text') or '')[:12000]}\n\n"
                    "ELEMENTOS INTERATIVOS:\n" + "\n".join(element_lines)
                )
            else:
                evidence = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (dict, list)) else str(result)
            context = (
                f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Usar o navegador'}\n\n"
                f"RESULTADO DA ACAO BROWSER_{action.upper()}:\n{evidence[:18000]}\n\n"
                "Continue autonomamente. Inspecione novamente se o DOM mudou; use apenas uma acao de navegador por resposta. "
                "Em localhost, teste o fluxo ate obter evidencia suficiente. Se a missao estiver concluida, entregue a conclusao."
            )
            self._run_ai_task(
                "Continue a tarefa usando o resultado real do navegador.",
                extra_context=context,
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )

        request_id = requester(action, payload=payload, callback=completed)
        if not request_id:
            self.add_chat_message("Erro", "Abra uma pagina no navegador antes de pedir interacao autonoma.")
            return
        activity = getattr(self, "set_ai_activity", None)
        if callable(activity):
            activity(f"IA usando navegador: {action}")
        self.log_agent(f"Automacao do navegador enviada: {action} {target}")

    def _agent_browser_chat(self, prompt, task_objective=None, action_depth=0, task_id=None):
        """Envia uma mensagem ao chat aberto no WebView sem criar conversa nova."""
        if self.is_task_cancelled(task_id):
            return
        requester = getattr(self, "request_internal_browser_action", None)
        if not callable(requester):
            self.add_chat_message("Erro", "O navegador atual não oferece envio de chat.")
            return
        prompt = str(prompt or "").strip()
        if not prompt:
            self.add_chat_message("Erro", "BROWSER_CHAT veio sem mensagem.")
            return
        if not self._approve_remote_browser_action("type", "chat", prompt):
            self.add_chat_message("Sistema", "Envio ao Chat Web cancelado pelo usuário.")
            return

        def completed(event):
            if self.is_task_cancelled(task_id):
                return
            result = event.get("result", "")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"response": result}
            result = result if isinstance(result, dict) else {"response": str(result)}
            if result.get("url"):
                self.internal_browser_url = str(result["url"])
                remember = getattr(self, "remember_internal_browser_chat_url", None)
                if callable(remember):
                    remember(self.internal_browser_url, str(result.get("title") or ""))
            response = str(result.get("response") or "").strip()
            if not response:
                self.add_chat_message("Erro", str(result.get("error") or "O Chat Web não devolveu texto."))
                return
            context = (
                f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Usar Chat Web'}\n\n"
                f"RESPOSTA REAL DO CHAT WEB:\n{response[:22000]}\n\n"
                "Continue a missão com uma ação concreta da IDE ou conclua objetivamente."
            )
            self._run_ai_task(
                "Continue a tarefa com a resposta do Chat Web.",
                extra_context=context,
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )

        request_id = requester(
            "chat",
            payload={"prompt": prompt, "timeout": 300},
            callback=completed,
        )
        if not request_id:
            self.add_chat_message("Erro", "Abra o Chat Web antes de usar BROWSER_CHAT.")
            return
        self.set_ai_activity("IA conversando pelo Chat Web")
        self.log_agent("Mensagem enviada ao Chat Web pela automação.")

    def _agent_screenshot(self, request, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            return
        delay = self.parse_screenshot_delay(request)
        self.set_ai_activity("IA capturando tela")

        def run():
            try:
                if delay:
                    time.sleep(delay)
                if self.is_task_cancelled(task_id):
                    return
                image = ImageGrab.grab()
                path = self.save_agent_screenshot(image)
                self.log_agent(f"Screenshot capturado pela IA: {path.name}")
                self.add_chat_image_message("Merotec AI", path, "")
                context = (
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
                    f"A IDE capturou a tela para validacao visual: {path.name}\n"
                    "Analise o print como evidência do estado atual do app. "
                "Se houver erro visual, comportamento quebrado ou tela vazia, corrija autonomamente com [READ], [REPLACE], [WRITE] ou uma tag EXECUTE ja preenchida. "
                    "Se estiver correto, entregue uma conclusao objetiva."
                )
                self._run_ai_task(
                    "Analise o screenshot capturado e continue a validacao/correcao autonomamente.",
                    image_path=str(path),
                    extra_context=context,
                    task_objective=task_objective or self.active_ai_objective,
                    action_depth=action_depth + 1,
                    task_id=task_id,
                )
            except Exception as exc:
                self.add_chat_message("Erro", f"Falha ao capturar screenshot: {exc}")

        threading.Thread(target=run, daemon=True).start()

    def parse_screenshot_delay(self, request):
        text = (request or "").strip()
        match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not match:
            return 1.0
        value = float(match.group(1).replace(",", "."))
        return max(0.0, min(10.0, value))

    def save_agent_screenshot(self, image):
        attachments = Path(self.current_workspace) / ".merotec_attachments"
        attachments.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = attachments / f"screenshot_{timestamp}.png"
        image.save(path, "PNG")
        return path

    def looks_like_unexecuted_intention(self, text):
        normalized = self.normalize_plain_text(text or "")
        intention_patterns = [
            r"\bvou\b.*\b(verificar|corrigir|analisar|procurar|buscar|localizar|mapear|validar|extrair|levantar|diagnosticar|aplicar|executar|ler|ver)\b",
            r"\birei\b.*\b(verificar|corrigir|analisar|procurar|buscar|localizar|mapear|validar|extrair|levantar|diagnosticar|aplicar|executar|ler|ver)\b",
            r"\bpreciso\b.*\b(ler|verificar|analisar|procurar|buscar|localizar|mapear|validar|extrair|levantar|diagnosticar)\b",
            r"\baguardando\b.*\b(leitura|arquivo|resultado)\b",
        ]
        return any(re.search(pattern, normalized) for pattern in intention_patterns)

    def response_has_real_action_tag(self, text):
        if not text:
            return False
        return bool(
            re.search(
                r"\[(WRITE|REPLACE|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|BROWSER_CHAT|SCREENSHOT|HUMAN_TEST)\s*:",
                text,
                re.IGNORECASE,
            )
        )

    def looks_like_claimed_concrete_result(self, text):
        normalized = self.normalize_plain_text(text or "")
        claimed_patterns = [
            r"\b(correcao|ajuste|alteracao|implementacao)\b.*\b(aplicad[ao]s?|feita|feito|concluid[ao]s?)\b",
            r"\b(corrigi|ajustei|alterei|atualizei|implementei|adicionei|removi|substitui|restaurei|forcei|liguei|apliquei)\b",
            r"\b(rodei|executei|testei|validei|verifiquei)\b",
            r"\bagora\b.*\b(aceita|funciona|aponta|usa|renderiza|executa|roda)\b",
        ]
        return any(re.search(pattern, normalized) for pattern in claimed_patterns)

    def claims_concrete_result_without_real_action(self, text, task_objective=None):
        if self.response_has_real_action_tag(text):
            return False
        objective = task_objective or self.active_ai_objective or ""
        if not self.objective_requires_concrete_change(objective):
            return False
        return self.looks_like_claimed_concrete_result(text)

    def redirect_claimed_action_to_real_action(self, response_text, task_objective=None, action_depth=0, task_id=None):
        self.warn_claimed_action_without_real_action(
            response_text,
            task_objective=task_objective,
            task_id=task_id,
        )

    def warn_claimed_action_without_real_action(self, response_text, task_objective=None, task_id=None):
        metrics = self.get_ai_task_metrics(task_id)
        metrics["soft_protocol_warnings"] = metrics.get("soft_protocol_warnings", 0) + 1
        preview = self.normalize_plain_text(response_text or "")[:180]
        objective = self.normalize_plain_text(task_objective or self.active_ai_objective or "")[:180]
        self.log_agent(
            "Aviso nao bloqueante: resposta parece afirmar acao sem tag executavel. "
            f"objetivo='{objective}' resposta='{preview}'"
        )
        if metrics["soft_protocol_warnings"] == 1:
            self.set_status("Aviso: resposta sem acao real detectavel.", "warning")

    def objective_allows_text_repair(self, objective):
        normalized = self.normalize_plain_text(objective or "")
        explicit_terms = {
            "mojibake",
            "codificacao",
            "encoding",
            "acentuacao",
            "acentos",
            "acento",
            "caractere",
            "caracteres",
            "corrompido",
            "corrompidos",
            "texto corrompido",
            "texto quebrado",
        }
        if "\ufffd" in (objective or "") or "Ã" in (objective or ""):
            return True
        return any(term in normalized for term in explicit_terms)

    def redirect_unrelated_text_repair(self, action_name, response_text, task_objective=None, action_depth=0, task_id=None):
        objective = task_objective or self.active_ai_objective or "Continuar tarefa atual"
        self.add_chat_message(
            "Sistema",
            f"A IDE ignorou {action_name} porque a missao atual nao e correcao de texto/codificacao.",
        )
        self.log_agent(f"{action_name} ignorado por desvio de missao.")
        self._run_ai_task(
            "A resposta anterior desviou para mojibake. Continue a missao real agora.",
            extra_context=(
                f"MISSAO ORIGINAL:\n{objective}\n\n"
                "A IDE bloqueou uma acao de mojibake/codificacao porque ela nao corresponde ao pedido atual.\n"
                "Nao use [SCAN_TEXT] nem [FIX_MOJIBAKE] nesta missao, a menos que o usuario peça especificamente texto corrompido.\n"
                "Proxima resposta obrigatoria:\n"
                "- Se precisa entender codigo, use [READ: arquivo | linhas inicio-fim].\n"
                "- Se ja sabe a mudanca, use [REPLACE] ou [WRITE].\n"
                "- Se precisa validar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: comando de teste da stack detectada].\n\n"
                f"Resposta desviada:\n{response_text}"
            ),
            task_objective=objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def is_file_mutation_command(self, command):
        lower = (command or "").lower()
        mutation_markers = [
            "set-content",
            "add-content",
            "out-file",
            "new-item",
            "remove-item",
            "move-item",
            "copy-item",
            " -replace ",
            ">>",
            ">",
            "sed -i",
            "perl -pi",
        ]
        return any(marker in lower for marker in mutation_markers)

    def is_file_inspection_command(self, command):
        lower = (command or "").lower()
        inspection_markers = [
            "select-string",
            "get-content",
            "rg ",
            "grep ",
            "findstr",
            "python -c",
            "py -c",
        ]
        if not any(marker in lower for marker in inspection_markers):
            return False
        return bool(self.extract_mutation_target_path(command) or self.extract_inspection_target_path(command))

    def is_source_read_command(self, command):
        """Reconhece comandos que apenas imprimem um arquivo-fonte.

        Chats web às vezes tentam ler código usando ``python -c`` em vez de
        [READ]. Isso não é uma validação nem uma alteração: o resultado precisa
        voltar ao modelo como contexto de arquivo. A regra é independente do
        modo irrestrito para que a IDE não pare após um comando com código 0.
        """
        text = str(command or "")
        lower = text.lower()
        target = self.extract_inspection_target_path(text)
        if not target:
            return False

        terminal_read = bool(re.search(
            r"\b(?:get-content|cat|type|more)\b|\bhead\s+-n\s+\d+|\bsed\s+-n",
            lower,
        ))
        python_read = (
            ("open(" in lower or "read_text(" in lower or ".read()" in lower)
            and any(marker in lower for marker in ("splitlines", "enumerate(", "print("))
            and not any(marker in lower for marker in ("sys.exit", "compile(", "py_compile", "compileall"))
        )
        return terminal_read or python_read

    def source_read_request_from_command(self, command):
        """Converte a listagem textual do terminal em uma solicitação READ."""
        text = str(command or "")
        target = self.extract_inspection_target_path(text)
        if not target:
            return ""

        start = None
        end = None
        slice_match = re.search(
            r"splitlines\(\)\s*\[\s*(\d*)\s*:\s*(\d*)\s*\]",
            text,
            re.IGNORECASE,
        )
        if slice_match:
            start = int(slice_match.group(1) or 0) + 1
            end = int(slice_match.group(2)) if slice_match.group(2) else None
        if start is None:
            total_match = re.search(r"(?:-totalcount|head\s+-n)\s+(\d+)", text, re.IGNORECASE)
            if total_match:
                start, end = 1, int(total_match.group(1))
        if start is None:
            sed_match = re.search(r"sed\s+-n\s+['\"]?(\d+)\s*,\s*(\d+)p", text, re.IGNORECASE)
            if sed_match:
                start, end = int(sed_match.group(1)), int(sed_match.group(2))

        if start and end and end >= start:
            return f"{target} | linhas {start}-{end}"
        return target

    def redirect_source_read_command_to_read(self, command, task_objective=None, action_depth=0, task_id=None):
        request = self.source_read_request_from_command(command)
        if not request:
            self._agent_execute(command, task_objective=task_objective, action_depth=action_depth, task_id=task_id)
            return
        self.add_chat_message(
            "Sistema",
            "A IA pediu leitura de código pelo terminal. A IDE converteu a ação em READ para devolver o conteúdo ao Chat Web e continuar a missão.",
        )
        self.log_agent(f"Comando de leitura convertido em READ: {command}")
        self._agent_read_many(
            [request],
            task_objective=task_objective,
            action_depth=action_depth,
            task_id=task_id,
        )

    def redirect_inspection_command_to_scan(self, command, task_objective=None, action_depth=0, task_id=None):
        target = self.extract_mutation_target_path(command) or self.extract_inspection_target_path(command)
        if not target:
            self._agent_execute(command, task_objective=task_objective, action_depth=action_depth, task_id=task_id)
            return
        pattern = self.extract_search_pattern_from_command(command)
        if pattern:
            self.add_chat_message(
                "Sistema",
                "A IA tentou buscar texto pelo terminal. A IDE vai fazer a busca internamente.",
            )
            self.log_agent(f"Comando de busca redirecionado para SEARCH_TEXT: {command}")
            self._agent_search_text_many(
                [f"{pattern} | {target}"],
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return
        self.add_chat_message(
            "Sistema",
            "A IA tentou inspecionar arquivo pelo terminal. A IDE vai fazer a varredura internamente.",
        )
        self.log_agent(f"Comando de inspecao redirecionado para SCAN_TEXT: {command}")
        self._agent_scan_text_many(
            [target],
            task_objective=task_objective,
            action_depth=action_depth,
            task_id=task_id,
        )

    def redirect_mutation_command_to_write(self, command, task_objective=None, action_depth=0, task_id=None):
        target = self.extract_mutation_target_path(command)
        target_context = ""
        if target:
            try:
                path = self.resolve_workspace_path(target)
                rel = path.relative_to(self.current_workspace).as_posix()
                if path.exists() and path.is_file():
                    target_context = self.build_file_context_for_agent(path, rel)
            except Exception:
                target_context = ""

        self.add_chat_message(
            "Sistema",
            "A IA tentou alterar arquivo pelo terminal. A IDE vai pedir a alteracao por WRITE para aplicar de forma confiavel.",
        )
        self.log_agent(f"Comando de mutacao redirecionado para WRITE: {command}")
        context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
            "A IA tentou modificar arquivo usando EXECUTE, o que pode nao alterar nada na IDE.\n"
            f"Comando recusado como edicao:\n```\n{command}\n```\n\n"
            "Proxima resposta obrigatoria:\n"
            "- Se for arquivo pequeno ou criacao nova, use [WRITE: caminho] conteudo completo [/WRITE].\n"
            "- Se for arquivo grande e voce conhece o trecho exato, use [REPLACE: caminho] [OLD] trecho atual [/OLD] [NEW] trecho novo [/NEW] [/REPLACE].\n"
            "- Se ainda nao conhece o OLD exato, leia o intervalo com [READ: arquivo | linhas inicio-fim].\n"
            "- Nao use PowerShell, Set-Content, -replace, redirecionamento ou sed para editar arquivo.\n"
        )
        if target_context:
            context += f"\n\nArquivo alvo detectado pela IDE:\n{target_context}"
        self._run_ai_task(
            "Converta a tentativa de edicao em WRITE ou REPLACE confiavel pela IDE.",
            extra_context=context,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def extract_mutation_target_path(self, command):
        text = command or ""
        assignments = re.findall(
            r"\$[A-Za-z_][\w]*\s*=\s*['\"]([^'\"]+\.(?:html|css|js|ts|py|dart|json|md|yaml|yml|txt|cpp|h|cs))['\"]",
            text,
            flags=re.IGNORECASE,
        )
        if assignments:
            return assignments[-1]

        quoted = re.findall(
            r"['\"]([^'\"]+\.(?:html|css|js|ts|py|dart|json|md|yaml|yml|txt|cpp|h|cs))['\"]",
            text,
            flags=re.IGNORECASE,
        )
        if quoted:
            return quoted[-1]
        return self.extract_inspection_target_path(text)

    def extract_inspection_target_path(self, command):
        text = command or ""
        extensions = r"(?:html|css|js|ts|py|dart|json|md|yaml|yml|txt|cpp|h|cs)"
        path_arg = re.search(r"(?:-Path|--path)\s+['\"]?([^'\"\s]+\." + extensions + r")['\"]?", text, re.IGNORECASE)
        if path_arg:
            return path_arg.group(1)
        candidates = re.findall(r"(?<![\w.-])([A-Za-z0-9_./\\-]+\." + extensions + r")", text, re.IGNORECASE)
        return candidates[-1] if candidates else ""

    def extract_search_pattern_from_command(self, command):
        text = command or ""
        select_pattern = re.search(r"-Pattern\s+['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
        if select_pattern:
            return select_pattern.group(1)

        rg_pattern = re.search(r"\brg(?:\.exe)?\s+(?:-[A-Za-z0-9]+\s+)*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
        if rg_pattern:
            return rg_pattern.group(1)

        grep_pattern = re.search(r"\b(?:grep|findstr)\s+(?:-[A-Za-z0-9]+\s+)*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
        if grep_pattern:
            return grep_pattern.group(1)

        return ""

    def parse_search_text_request(self, request):
        text = request.strip().strip("\"'")
        if "|" in text:
            pattern, path = text.rsplit("|", 1)
            return pattern.strip().strip("\"'"), path.strip().strip("\"'")
        return "", text

    def _agent_web_search_many(self, requests, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            return

        blocks = []
        for request in requests:
            query = self.sanitize_web_search_query(request)
            if not query:
                blocks.append("WEB_SEARCH ignorado: consulta vazia.")
                continue
            try:
                html_text = self.fetch_web_search_html(query)
                results = self.parse_web_search_results(html_text)
                blocks.append(self.build_web_search_context(query, results))
                self.log_agent(f"Busca web feita pela IDE: {query}")
                self.add_chat_message("Merotec AI", f"Busquei na internet: `{query}`.")
            except Exception as exc:
                blocks.append(self.build_web_search_context(query, [], error=str(exc)))
                self.add_chat_message("Erro", f"Falha na busca web `{query}`: {exc}")

        context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
            + "\n\n".join(blocks)
            + "\n\n"
            "Continue a missao usando esses resultados da internet como contexto externo. "
            "Se encontrou informacao suficiente, conclua ou aplique a correcao. "
            "Se precisar mudar arquivo, use [REPLACE] ou [WRITE]; se precisar validar, use uma tag EXECUTE real. "
            "Nao repita a mesma busca web sem necessidade."
        )
        self._run_ai_task(
            "Continue a missao original com base na busca web feita pela IDE.",
            extra_context=context,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def sanitize_web_search_query(self, request):
        query = re.sub(r"\s+", " ", str(request or "").strip().strip("\"'"))
        return query[:220]

    def fetch_web_search_html(self, query, timeout=12):
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36 MerotecAI/1.0"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    @staticmethod
    def normalize_duckduckgo_result_url(raw_url):
        url = html.unescape(str(raw_url or "").strip())
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url)
        if parsed.path == "/l/" or parsed.path.endswith("/l/"):
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get("uddg", [""])[0]
            if target:
                return urllib.parse.unquote(target)
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return ""

    def parse_web_search_results(self, html_text, max_results=5):
        parser = DuckDuckGoHtmlResultParser(max_results=max_results)
        parser.feed(html_text or "")
        return parser.results[:max_results]

    def build_web_search_context(self, query, results, error=""):
        header = f"WEB_SEARCH: {query}"
        if error:
            return f"{header}\nResultado: falha ao buscar na internet.\nErro: {error}"
        if not results:
            return f"{header}\nResultado: nenhuma resposta encontrada pelo buscador."

        lines = [header, f"Resultados encontrados: {len(results)}"]
        for index, result in enumerate(results, start=1):
            title = result.get("title", "").strip()
            url = result.get("url", "").strip()
            snippet = result.get("snippet", "").strip()
            if len(snippet) > 320:
                snippet = snippet[:320].rstrip() + "..."
            lines.append(f"{index}. {title}\nURL: {url}")
            if snippet:
                lines.append(f"Resumo: {snippet}")
        return "\n".join(lines)

    def normalize_search_pattern(self, pattern):
        terms = re.findall(r"[a-zA-Z0-9_]+", pattern or "")
        if not terms:
            return (pattern or "").strip().lower()
        return "|".join(sorted({term.lower() for term in terms}))

    def _agent_search_text_many(self, requests, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            return

        blocks = []
        stopped = False
        for request in requests:
            try:
                pattern, raw_path = self.parse_search_text_request(request)
                path = self.resolve_workspace_path(raw_path)
                rel = path.relative_to(self.current_workspace).as_posix()
                if not pattern:
                    pattern = self.default_search_pattern_for_objective(task_objective or self.active_ai_objective or "")

                block = self.build_search_text_context(path, rel, pattern)
                blocks.append(block)
                self.log_agent(f"Busca de texto feita pela IDE: {rel} :: {pattern}")
                self.add_chat_message("Merotec AI", f"Busquei no arquivo: `{rel}`.")

                if self.should_stop_repeated_search(rel, pattern, task_id):
                    stopped = True
            except Exception as exc:
                blocks.append(f"Falha ao buscar texto em {request}: {exc}")

        context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
            + "\n\n".join(blocks)
        )

        simple_verification = self.is_simple_search_verification(task_objective or self.active_ai_objective or "")
        if simple_verification:
            self.add_chat_message(
                "Merotec AI",
                self.local_search_conclusion(task_objective or self.active_ai_objective or "", "\n\n".join(blocks)),
            )
            self.set_status("Busca concluida.", "ready")
            return

        if stopped:
            context += (
                "\n\nCONTROLE DA IDE:\n"
                "A mesma busca ja foi feita nesta missao. Nao conclua dizendo apenas que a busca acabou. "
                "Use as linhas encontradas acima para decidir a proxima acao real.\n"
                "- Se a missao pede alterar/corrigir/remover/adicionar, responda agora com [READ] de um intervalo exato ainda necessario, [REPLACE] ou [WRITE].\n"
                "- Se ja souber o trecho a mudar, prefira [REPLACE].\n"
                "- Se a missao pede executar/testar, responda com uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: comando de teste da stack detectada].\n"
                "- Nao repita [SEARCH_TEXT] para o mesmo arquivo/padrao.\n"
            )
        else:
            context += (
                "\n\nResponda a pergunta do usuario com base nesses resultados. "
                "Se ja encontrou evidencias suficientes, de a conclusao agora. "
                "Nao repita a mesma busca."
            )
        self._run_ai_task(
            "Continue a missao original com base na busca interna da IDE.",
            extra_context=context,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def is_simple_search_verification(self, objective):
        normalized = self.normalize_plain_text(objective or "")
        verify_terms = {"verificar", "verifique", "veja", "existe", "exista", "tem", "possui"}
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        return bool(words & verify_terms) and ("zoom" in normalized or "mobile" in normalized)

    def default_search_pattern_for_objective(self, objective):
        normalized = self.normalize_plain_text(objective or "")
        if "zoom" in normalized or "mobile" in normalized:
            return "zoom|pinch|wheel|touchstart|touchmove|gesture|mobile|isMobile|scale|cameraZoom|fov"
        return "|".join(re.findall(r"[a-zA-Z0-9_]{3,}", objective or "")[:12])

    def should_stop_repeated_search(self, rel, pattern, task_id):
        if self.autonomous_unrestricted_mode_enabled():
            return False
        task_key = task_id if task_id is not None else self.current_task_id
        task_history = self.ai_search_history.setdefault(task_key, {"keys": {}, "files": {}})
        normalized = self.normalize_search_pattern(pattern)
        key = f"{rel}:{normalized}"
        task_history["keys"][key] = task_history["keys"].get(key, 0) + 1
        task_history["files"][rel] = task_history["files"].get(rel, 0) + 1
        return task_history["keys"][key] >= 2 or task_history["files"][rel] >= 4

    def build_search_text_context(self, path, rel, pattern, limit=80):
        if path.is_dir():
            return f"SEARCH_TEXT: {rel}\nAlvo e uma pasta; busca em arquivo ignorada."

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            safe_terms = [re.escape(term) for term in re.findall(r"[a-zA-Z0-9_]+", pattern)]
            regex = re.compile("|".join(safe_terms) or re.escape(pattern), re.IGNORECASE)

        content = path.read_text(encoding="utf-8", errors="replace")
        matches = []
        for number, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                snippet = line.strip()
                if len(snippet) > 240:
                    snippet = snippet[:240] + "..."
                matches.append(f"{number}: {snippet}")
            if len(matches) >= limit:
                matches.append("... busca truncada; ha mais ocorrencias.")
                break

        if not matches:
            return (
                f"SEARCH_TEXT: {rel}\n"
                f"Padrao: {pattern}\n"
                "Resultado: nenhuma ocorrencia encontrada."
            )

        return (
            f"SEARCH_TEXT: {rel}\n"
            f"Padrao: {pattern}\n"
            f"Ocorrencias: {len(matches)}\n"
            "Linhas encontradas:\n```\n"
            + "\n".join(matches)
            + "\n```"
        )

    def local_search_conclusion(self, objective, search_context):
        normalized = self.normalize_plain_text(objective or "")
        has_results = "Resultado: nenhuma ocorrencia encontrada." not in search_context
        if "zoom" in normalized and "mobile" in normalized:
            if has_results:
                return (
                    "A IDE ja buscou os termos de zoom/mobile no arquivo e encontrou ocorrencias relacionadas. "
                    "Isso indica que existe alguma logica ligada a zoom/mobile, mas e preciso olhar as linhas encontradas para confirmar se e zoom funcional no modo mobile."
                )
            return "Nao encontrei ocorrencias de zoom/mobile/pinch/wheel/scale/cameraZoom no arquivo analisado."
        return "A busca interna foi concluida. Use as linhas encontradas acima como base; a IDE interrompeu novas buscas repetidas para evitar ciclo."

    def _agent_scan_text_many(self, raw_paths, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            return
        blocks = []
        for raw_path in raw_paths:
            try:
                path = self.resolve_workspace_path(raw_path)
                rel = path.relative_to(self.current_workspace).as_posix()
                if path.is_dir():
                    blocks.append(f"SCAN_TEXT ignorado: {rel} e uma pasta.")
                    continue
                block = self.build_text_scan_context(path, rel)
                self.log_agent(f"Varredura de texto feita pela IDE: {rel}")
                self.add_chat_message("Merotec AI", f"Varri o arquivo: `{rel}`.")
                blocks.append(block)
            except Exception as exc:
                blocks.append(f"Falha ao varrer {raw_path}: {exc}")

        context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
            + "\n\n".join(blocks)
            + "\n\n"
            "Continue a missao usando a varredura da IDE. "
            "Use [FIX_MOJIBAKE: arquivo] somente se a missao original for corrigir texto/codificacao. "
            "Se for outro erro, use [REPLACE] ou [WRITE]. "
            "Nao use terminal para repetir essa mesma busca."
        )
        self._run_ai_task(
            "Continue a missao apos a varredura de texto da IDE.",
            extra_context=context,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    def build_text_scan_context(self, path, rel, limit=120):
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        issues = []
        for number, line in enumerate(lines, start=1):
            score = self.mojibake_score(line)
            if score or self.has_suspicious_text_chars(line):
                snippet = line.strip()
                if len(snippet) > 220:
                    snippet = snippet[:220] + "..."
                issues.append(f"{number}: score={score} | {snippet}")
            if len(issues) >= limit:
                issues.append("... varredura truncada; ha mais ocorrencias.")
                break

        if not issues:
            return (
                f"SCAN_TEXT: {rel}\n"
                f"Linhas analisadas: {len(lines)}\n"
                "Nenhum mojibake obvio encontrado pela varredura automatica."
            )
        return (
            f"SCAN_TEXT: {rel}\n"
            f"Linhas analisadas: {len(lines)}\n"
            f"Ocorrencias suspeitas: {len(issues)}\n"
            "Trechos suspeitos:\n```\n"
            + "\n".join(issues)
            + "\n```"
        )

    def has_suspicious_text_chars(self, text):
        return any(char in text for char in ("\ufffd", "\ufeff"))

    def _agent_fix_mojibake(self, raw_path, task_id=None):
        try:
            path = self.resolve_workspace_path(raw_path)
            if path.is_dir():
                raise ValueError("FIX_MOJIBAKE precisa apontar para um arquivo.")
            current = path.read_text(encoding="utf-8", errors="replace")
            repaired = self.repair_common_mojibake(current)
            if repaired == current:
                rel = path.relative_to(self.current_workspace).as_posix()
                self.add_chat_message("Merotec AI", f"Nao encontrei texto corrompido corrigivel automaticamente em `{rel}`.")
                return

            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            self.record_file_change_snapshot(path, "FIX_MOJIBAKE", "Caracteres corrompidos corrigidos")
            path.write_text(repaired, encoding="utf-8")
            rel = path.relative_to(self.current_workspace).as_posix()
            before = self.mojibake_score(current)
            after = self.mojibake_score(repaired)
            self.log_agent(f"Mojibake corrigido pela IDE: {rel} ({before} -> {after})")
            self.add_chat_message("Merotec AI", f"Corrigi caracteres corrompidos em `{rel}`. Backup criado.")
            self.load_workspace_files()
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao corrigir mojibake: {exc}")

    def repair_common_mojibake(self, text):
        repaired_lines = []
        for line in text.splitlines(keepends=True):
            repaired_lines.append(self.repair_mojibake_line(line))
        repaired = "".join(repaired_lines)
        return self.apply_mojibake_map(repaired)

    def repair_mojibake_line(self, line):
        original_score = self.mojibake_score(line)
        if not original_score:
            return line
        mapped = self.apply_mojibake_map(line)
        if "\ufffd" in line and "\ufffd" not in mapped:
            return mapped
        candidates = [mapped]
        for encoding in ("cp1252", "latin1"):
            try:
                candidates.append(line.encode(encoding, errors="ignore").decode("utf-8", errors="replace"))
            except UnicodeError:
                continue
        return min(candidates, key=lambda item: (self.mojibake_score(item), len(item))) if candidates else line

    def mojibake_score(self, text):
        markers = [
            "\ufffd",
            "\u00c3",
            "\u00c2",
            "\u00e2\u20ac",
            "\u00ef\u00bb\u00bf",
        ]
        return sum(text.count(marker) for marker in markers)

    def apply_mojibake_map(self, text):
        replacements = {
            "\u00c3\u00a1": "\u00e1",
            "\u00c3\u00a0": "\u00e0",
            "\u00c3\u00a2": "\u00e2",
            "\u00c3\u00a3": "\u00e3",
            "\u00c3\u00a4": "\u00e4",
            "\u00c3\u00a9": "\u00e9",
            "\u00c3\u00aa": "\u00ea",
            "\u00c3\u00a8": "\u00e8",
            "\u00c3\u00ab": "\u00eb",
            "\u00c3\u00ad": "\u00ed",
            "\u00c3\u00ae": "\u00ee",
            "\u00c3\u00ac": "\u00ec",
            "\u00c3\u00af": "\u00ef",
            "\u00c3\u00b3": "\u00f3",
            "\u00c3\u00b4": "\u00f4",
            "\u00c3\u00b5": "\u00f5",
            "\u00c3\u00b2": "\u00f2",
            "\u00c3\u00b6": "\u00f6",
            "\u00c3\u00ba": "\u00fa",
            "\u00c3\u00bb": "\u00fb",
            "\u00c3\u00b9": "\u00f9",
            "\u00c3\u00bc": "\u00fc",
            "\u00c3\u00a7": "\u00e7",
            "\u00c3\u00b1": "\u00f1",
            "\u00c3\u0081": "\u00c1",
            "\u00c3\u0089": "\u00c9",
            "\u00c3\u0093": "\u00d3",
            "\u00c3\u009a": "\u00da",
            "\u00c3\u0087": "\u00c7",
            "\u00c2\u00ba": "\u00ba",
            "\u00c2\u00aa": "\u00aa",
            "\u00c2\u00b0": "\u00b0",
            "\u00c2\u00b7": "\u00b7",
            "\u00c2\u00a0": " ",
            "\u00e2\u20ac\u2122": "'",
            "\u00e2\u20ac\u02dc": "'",
            "\u00e2\u20ac\u0153": '"',
            "\u00e2\u20ac\u009d": '"',
            "\u00e2\u20ac\u201c": "-",
            "\u00e2\u20ac\u201d": "-",
            "\u00e2\u20ac\u00a6": "...",
            "\u00ef\u00bb\u00bf": "",
            "Diagn\ufffdstico": "Diagnostico",
            "diagn\ufffdstico": "diagnostico",
            "aplica\ufffd\ufffdo": "aplicacao",
            "Aplica\ufffd\ufffdo": "Aplicacao",
            "mem\ufffdria": "memoria",
            "Mem\ufffdria": "Memoria",
            "m\ufffddulos": "modulos",
            "M\ufffddulos": "Modulos",
            "orquestra\ufffd\ufffdo": "orquestracao",
            "Orquestra\ufffd\ufffdo": "Orquestracao",
            "implementa\ufffd\ufffdo": "implementacao",
            "Implementa\ufffd\ufffdo": "Implementacao",
            "resili\ufffdncia": "resiliencia",
            "Resili\ufffdncia": "Resiliencia",
            "pr\ufffdxima": "proxima",
            "Pr\ufffdxima": "Proxima",
        }
        repaired = text
        for bad, good in replacements.items():
            repaired = repaired.replace(bad, good)
        return repaired

    def _agent_write(self, raw_path, content, task_id=None, task_objective=None):
        try:
            path = self.resolve_workspace_path(raw_path)
            if path.is_dir():
                raise ValueError("WRITE precisa apontar para um arquivo, nao para uma pasta.")
            if not content.strip():
                raise ValueError("WRITE veio sem conteudo.")
            path.parent.mkdir(parents=True, exist_ok=True)

            cleaned = self._extract_fenced_code_payload(content)
            valid, validation_error = self.validate_write_content(path, cleaned)
            if not valid:
                raise ValueError(validation_error)
            if path.exists():
                current = path.read_text(encoding="utf-8", errors="replace")
                objective = task_objective or self.active_ai_objective or ""
                if self.is_risky_full_rewrite(path, current, cleaned, objective):
                    rel = path.relative_to(self.current_workspace).as_posix()
                    self.log_agent(f"WRITE grande liberado com backup obrigatorio: {rel}")

            if path.exists():
                backup = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup)
                self.log_agent(f"Backup criado: {backup.name}")
            self.record_file_change_snapshot(path, "WRITE", "Arquivo escrito pela IA")

            path.write_text(cleaned, encoding="utf-8")
            self.log_agent(f"Arquivo escrito pela IA: {path.relative_to(self.current_workspace).as_posix()}")
            self.add_chat_message("Merotec AI", f"Atualizei o arquivo: `{path.relative_to(self.current_workspace).as_posix()}`.")
            self.load_workspace_files()
            return True
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao escrever arquivo: {exc}")
            return False

    def objective_allows_full_rewrite(self, objective):
        normalized = self.normalize_plain_text(objective or "")
        negated_terms = [
            "sem recriar",
            "nao recriar",
            "nao recrie",
            "nao reconstruir",
            "nao reconstrua",
            "nao reescrever",
            "nao reescreva",
            "nao refazer",
            "nao refaca",
            "sem refazer",
            "sem reescrever",
            "sem reconstruir",
        ]
        if any(term in normalized for term in negated_terms):
            return False
        allow_terms = {
            "recriar",
            "recrie",
            "reconstruir",
            "reconstrua",
            "reescrever",
            "reescreva",
            "refazer",
            "refaca",
            "do zero",
            "arquivo completo",
            "versao nova",
            "novo app",
            "novo jogo",
        }
        return any(term in normalized for term in allow_terms)

    def is_risky_full_rewrite(self, path, current, proposed, objective):
        if self.objective_allows_full_rewrite(objective):
            return False
        if path.suffix.lower() not in {".html", ".js", ".ts", ".tsx", ".jsx", ".py", ".dart", ".css"}:
            return False
        if len(current) < 12000:
            return False

        current_lines = max(1, current.count("\n") + 1)
        proposed_lines = max(1, proposed.count("\n") + 1)
        line_ratio = proposed_lines / current_lines
        char_ratio = len(proposed) / max(1, len(current))

        if line_ratio < 0.72 or line_ratio > 1.35 or char_ratio < 0.72 or char_ratio > 1.35:
            return True

        current_signals = self.code_identity_signals(current)
        proposed_signals = self.code_identity_signals(proposed)
        if len(current_signals) >= 8:
            preserved = len(current_signals & proposed_signals) / len(current_signals)
            if preserved < 0.55:
                return True
        return False

    def code_identity_signals(self, text, limit=120):
        signals = set()
        patterns = [
            r"\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
            r"\bid\s*=\s*['\"]([^'\"]+)['\"]",
            r"\bclass\s*=\s*['\"]([^'\"]+)['\"]",
            r"\b(?:addEventListener|querySelector|getElementById)\s*\(([^)]{1,80})\)",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text):
                value = match if isinstance(match, str) else "|".join(match)
                value = value.strip()
                if value:
                    signals.add(value[:120])
                if len(signals) >= limit:
                    return signals
        return signals

    def redirect_risky_write_to_patch(self, path, current, proposed, objective, task_id=None):
        rel = path.relative_to(self.current_workspace).as_posix()
        self.add_chat_message(
            "Merotec AI",
            f"A reescrita grande de `{rel}` foi liberada com backup. Se o resultado nao agradar, use desfazer.",
        )
        self.log_agent(f"WRITE grande nao bloqueado: {rel}")

    def _agent_replace(self, raw_path, old_content, new_content, task_id=None, task_objective=None):
        try:
            path = self.resolve_workspace_path(raw_path)
            if path.is_dir():
                raise ValueError("REPLACE precisa apontar para um arquivo, nao para uma pasta.")
            if not path.exists():
                raise ValueError("Arquivo alvo nao existe.")

            old_text = self._clean_action_block(old_content)
            new_text = self._clean_action_block(new_content)
            if not old_text:
                raise ValueError("OLD veio vazio.")

            current = path.read_text(encoding="utf-8", errors="replace")
            objective = task_objective or self.active_ai_objective or ""
            if self.is_risky_replace(path, current, old_text, new_text, objective):
                rel = path.relative_to(self.current_workspace).as_posix()
                self.log_agent(f"REPLACE grande liberado com backup obrigatorio: {rel}")

            updated = self.replace_exact_or_line_ending_variant(current, old_text, new_text)
            if updated is None:
                rel = path.relative_to(self.current_workspace).as_posix()
                self.add_chat_message(
                    "Erro",
                    f"REPLACE nao encontrou o trecho exato em {rel}. A IDE vai devolver o arquivo atual ao mesmo chat e continuar a correção.",
                )
                self.log_agent(f"REPLACE falhou porque OLD nao foi encontrado: {rel}")
                return False

            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            self.record_file_change_snapshot(path, "REPLACE", "Trecho substituido pela IA")
            path.write_text(updated, encoding="utf-8")
            rel = path.relative_to(self.current_workspace).as_posix()
            self.log_agent(f"Trecho substituido pela IA: {rel}")
            self.add_chat_message("Merotec AI", f"Substitui o trecho em `{rel}`.")
            self.load_workspace_files()
            return True
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao substituir trecho: {exc}")
            return False

    def is_risky_replace(self, path, current, old_text, new_text, objective):
        if self.objective_allows_full_rewrite(objective):
            return False
        if path.suffix.lower() not in {".html", ".js", ".ts", ".tsx", ".jsx", ".py", ".dart", ".css"}:
            return False
        current_lines = max(1, current.count("\n") + 1)
        old_lines = max(1, old_text.count("\n") + 1)
        new_lines = max(1, new_text.count("\n") + 1)
        old_ratio = len(old_text) / max(1, len(current))
        if current_lines >= 500 and (old_lines > 220 or old_ratio > 0.35):
            return True
        if current_lines >= 120 and (old_lines > 360 or new_lines > old_lines * 2.6):
            return True
        return False

    def redirect_risky_replace_to_smaller_patch(self, path, current, old_text, new_text, objective, task_id=None):
        rel = path.relative_to(self.current_workspace).as_posix()
        self.add_chat_message(
            "Merotec AI",
            f"A substituicao grande em `{rel}` foi liberada com backup. Se o resultado nao agradar, use desfazer.",
        )
        self.log_agent(f"REPLACE grande nao bloqueado: {rel}")

    def replace_exact_or_line_ending_variant(self, current, old_text, new_text):
        if old_text in current:
            return current.replace(old_text, new_text, 1)

        old_lf = old_text.replace("\r\n", "\n")
        current_lf = current.replace("\r\n", "\n")
        if old_lf not in current_lf:
            return None

        updated_lf = current_lf.replace(old_lf, new_text.replace("\r\n", "\n"), 1)
        return updated_lf.replace("\n", "\r\n") if "\r\n" in current else updated_lf

    def _clean_action_block(self, content):
        cleaned = content.strip("\r\n")
        if cleaned.strip().startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        return cleaned.strip("\r\n")

    def _agent_read(self, raw_path, task_objective=None, action_depth=0, task_id=None):
        self._agent_read_many([raw_path], task_objective=task_objective, action_depth=action_depth, task_id=task_id)

    def read_files_limit_for_objective(self, task_objective=None):
        """Permite contexto suficiente no modo autonomo sem confundir arquivo grande com bloqueio.

        O limite por lote continua protegendo a interface, mas o modo irrestrito nao reduz
        uma tarefa de implementacao a duas leituras apenas porque o objetivo menciona projeto.
        """
        if self.autonomous_unrestricted_mode_enabled():
            return max(1, int(getattr(self, "max_read_requests_per_batch", self.max_read_files_per_turn)))
        objective = self.normalize_plain_text(task_objective or self.active_ai_objective or "")
        words = set(re.findall(r"[a-z0-9_]+", objective))
        if "projeto" in objective and words & {
            "analise",
            "analisa",
            "analisar",
            "analize",
            "analizar",
            "avaliar",
            "revise",
            "revisar",
        }:
            return 2
        return self.max_read_files_per_turn

    def _agent_read_many(self, raw_paths, task_objective=None, action_depth=0, task_id=None):
        try:
            if self.is_task_cancelled(task_id):
                return
            self.set_ai_activity("IA lendo arquivos")
            blocks = []
            seen = set()
            requested = []
            grouped = []
            by_rel = {}
            for raw_path in list(raw_paths)[: self.max_read_requests_per_batch]:
                clean_path, line_range = self.parse_agent_read_request(raw_path)
                path = self.resolve_workspace_path(clean_path)
                rel = path.relative_to(self.current_workspace).as_posix()
                requested.append((path, rel, line_range))
                if rel not in by_rel:
                    by_rel[rel] = {"path": path, "ranges": [], "full": False}
                    grouped.append(rel)
                if line_range:
                    by_rel[rel]["ranges"].append(line_range)
                else:
                    by_rel[rel]["full"] = True

            files_limit = self.read_files_limit_for_objective(task_objective)
            for rel in grouped[: files_limit]:
                info = by_rel[rel]
                path = info["path"]
                ranges = info["ranges"]
                read_key = rel
                if read_key in seen:
                    continue
                seen.add(read_key)
                if path.is_dir():
                    content = self.describe_directory_for_agent(path)
                    block = f"Diretorio lido pela IDE: {rel}\nConteudo:\n```\n{content}\n```"
                    self.add_chat_message("Merotec AI", f"Mapeando pasta `{rel}`...")
                else:
                    total_lines = self.count_text_file_lines(path)
                    # Um intervalo explicitamente solicitado deve voltar exatamente como foi pedido,
                    # mesmo em arquivos com milhares de linhas. Antes, qualquer arquivo acima de
                    # 420 linhas era convertido em mapa geral e o modelo acabava pedindo WRITE
                    # completo por nao receber o trecho que precisava para um REPLACE/PATCH local.
                    should_consolidate = info["full"] or len(ranges) > 1
                    if should_consolidate:
                        block = self.build_file_intelligence_context(
                            path,
                            rel,
                            objective=task_objective or self.active_ai_objective or "",
                            requested_ranges=ranges,
                        )
                        self.register_file_read_coverage(rel, total_lines, ranges)
                        self.add_chat_message("Merotec AI", f"Analisando `{rel}` inteiro uma vez, com foco na missao...")
                    else:
                        line_range = ranges[0] if ranges else None
                        block = self.build_guarded_file_context(path, rel, line_range=line_range)
                        if line_range:
                            self.add_chat_message("Merotec AI", f"Lendo `{rel}`, linhas {line_range[0]}-{line_range[1]}...")
                        else:
                            self.add_chat_message("Merotec AI", f"Lendo `{rel}`...")
                self.log_agent(f"Arquivo lido para IA: {rel}")
                blocks.append(block)

            omitted = max(0, len(grouped) - files_limit)
            if omitted:
                blocks.append(
                    "CONTROLE DE CONTEXTO DA IDE:\n"
                    f"{omitted} arquivo(s) pedido(s) foram omitidos nesta rodada para manter foco.\n"
                    "Nao peca uma nova lista de READ agora. Use os arquivos recebidos para aplicar a proxima acao concreta."
                )

            diff_block = self.build_requested_backup_diff(requested)
            if diff_block:
                blocks.append(diff_block)
                self.add_chat_message("Merotec AI", "Comparei o arquivo atual com o backup para recuperar o que mudou.")

            context = (
                f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Continuar tarefa atual'}\n\n"
                + "\n\n".join(blocks)
                + "\n\n"
                "Continue a missao original usando esse conteudo como sua memoria de trabalho. "
                "Nao pergunte novamente qual e o objetivo. "
                "Nao diga apenas que vai ler/comparar; agora decida a acao concreta. "
                "A IDE consolidou leituras repetidas por arquivo; nao peca novamente os mesmos trechos. "
                "Se a tarefa for corrigir, preservar ou restaurar comportamento, use [REPLACE] pequeno e exato. "
                "Se a tarefa for implementar algo, aplique [REPLACE] ou [WRITE] agora e depois use uma tag EXECUTE ja preenchida para validar. "
                "Se ja tiver informacao suficiente, responda com [REPLACE], [WRITE] ou uma tag EXECUTE com comando real. "
                "Nao use nova rodada de [READ] como proxima acao, exceto para um unico intervalo exato indispensavel."
            )
            self.set_ai_activity("IA analisando leitura")
            self._run_ai_task(
                "Continue a missao original apos a leitura dos arquivos",
                extra_context=context,
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao ler arquivo: {exc}")

    def register_file_read_coverage(self, rel, total_lines, ranges):
        history = self.ai_read_history.setdefault(
            rel,
            {
                "overview_count": 0,
                "ranges": [],
                "range_keys": set(),
                "requests": 0,
            },
        )
        history["requests"] += 1
        history["overview_count"] += 1
        if ranges:
            for line_range in ranges:
                start, end = self.normalize_line_range(line_range, total_lines)
                key = f"{start}-{end}"
                if key not in history["range_keys"]:
                    history["ranges"].append((start, end))
                    history["range_keys"].add(key)
        elif total_lines:
            history["ranges"] = [(1, total_lines)]
            history["range_keys"] = {"1-" + str(total_lines)}

    def build_file_intelligence_context(self, path, rel, objective="", requested_ranges=None):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            content = path.read_text(errors="replace")

        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)
        requested_ranges = requested_ranges or []
        terms = self.extract_objective_terms_for_file(objective)
        snippets = self.collect_relevant_snippets(lines, terms, limit=90)
        requested_snippets = []
        for line_range in requested_ranges[:8]:
            start, end = self.normalize_line_range(line_range, total_lines)
            selected = lines[start - 1 : end]
            requested_snippets.append(
                f"Intervalo pedido pela IA: linhas {start}-{min(end, total_lines)}\n"
                f"```\n{self.number_lines(selected, start)}\n```"
            )

        index = self.build_large_file_index(lines, limit=260)
        backup_diff = self.build_backup_diff_for_file(path, rel, limit=160)

        if total_chars <= 160000:
            body = (
                "Conteudo completo numerado:\n"
                f"```\n{self.number_lines(lines, 1)}\n```"
            )
        else:
            head_count = 260
            tail_count = 180
            tail_start = max(head_count + 1, total_lines - tail_count + 1)
            body = (
                "Arquivo grande: a IDE leu tudo localmente e enviou um mapa amplo para a IA.\n"
                "Use os trechos relevantes e o indice; peca novo READ quando faltar contexto especifico para decidir ou aplicar mudanca.\n\n"
                f"Indice estrutural:\n```\n{index}\n```\n\n"
                f"Trechos relevantes para a missao:\n```\n{snippets or 'Nenhum termo direto encontrado; use o indice estrutural.'}\n```\n\n"
                f"Inicio do arquivo:\n```\n{self.number_lines(lines[:head_count], 1)}\n```\n\n"
                f"Final do arquivo:\n```\n{self.number_lines(lines[tail_start - 1:], tail_start)}\n```"
            )

        requested_text = "\n\n".join(requested_snippets)
        return (
            f"ANALISE CONSOLIDADA DE ARQUIVO PELA IDE: {rel}\n"
            f"Tamanho: {total_lines} linhas, {total_chars} caracteres\n"
            f"Foco da missao: {objective or 'continuar tarefa atual'}\n"
            f"Termos usados para localizar contexto: {', '.join(terms[:24]) or 'estrutura geral'}\n\n"
            + (requested_text + "\n\n" if requested_text else "")
            + body
            + (f"\n\n{backup_diff}" if backup_diff else "")
            + "\n\nORDEM PARA A IA:\n"
            "- Trate esta analise como entendimento do arquivo inteiro.\n"
            "- Evite loop de READ, mas busque contexto adicional quando isso realmente aumentar a qualidade da solucao.\n"
            "- Para preservar o projeto, prefira [REPLACE] pequeno e exato.\n"
            "- Use [WRITE] completo quando for arquivo novo, reescrita solicitada ou alteracao ampla inevitavel."
        )

    def extract_objective_terms_for_file(self, objective):
        normalized = self.normalize_plain_text(objective or "")
        words = re.findall(r"[a-zA-Z_][\w-]{2,}", normalized)
        stop = {
            "para", "como", "que", "uma", "por", "com", "dos", "das", "esse", "essa",
            "isso", "projeto", "arquivo", "atual", "corrigir", "verificar", "fazer",
            "executar", "implementar", "melhorar", "precisa", "deve", "deveria",
        }
        terms = []
        for word in words:
            if word not in stop and word not in terms:
                terms.append(word)
        domain_terms = [
            "camera", "controls", "control", "keydown", "keyup", "touchstart", "touchmove",
            "mobile", "zoom", "pinch", "wheel", "scale", "moveForward", "moveBackward",
            "ArrowUp", "ArrowDown", "KeyW", "KeyS", "cloud", "clouds", "nuvem", "nuvens",
            "flight", "fly", "player", "terrain", "runway", "update", "animate",
            "build", "error", "exception", "function", "class",
        ]
        for term in domain_terms:
            if term.lower() not in [item.lower() for item in terms]:
                terms.append(term)
        return terms[:40]

    def collect_relevant_snippets(self, lines, terms, limit=90, radius=2):
        if not terms:
            return ""
        lower_terms = [term.lower() for term in terms if term]
        selected = set()
        for index, line in enumerate(lines):
            lowered = line.lower()
            if any(term.lower() in lowered for term in lower_terms):
                for pos in range(max(0, index - radius), min(len(lines), index + radius + 1)):
                    selected.add(pos)
            if len(selected) >= limit:
                break
        if not selected:
            return ""
        ordered = sorted(selected)[:limit]
        output = []
        previous = None
        for pos in ordered:
            if previous is not None and pos > previous + 1:
                output.append("  ...")
            output.append(f"{pos + 1:>5}: {lines[pos][:220]}")
            previous = pos
        if len(selected) > limit:
            output.append("  ... trechos relevantes truncados.")
        return "\n".join(output)

    def build_backup_diff_for_file(self, path, rel, limit=160):
        backup = Path(str(path) + ".bak")
        if not backup.exists() or not path.exists():
            return ""
        try:
            current_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            backup_lines = backup.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        diff = list(
            difflib.unified_diff(
                backup_lines,
                current_lines,
                fromfile=rel + ".bak",
                tofile=rel,
                lineterm="",
                n=3,
            )
        )
        if not diff:
            return ""
        if len(diff) > limit:
            diff = diff[:limit] + ["... diff truncado; use somente os trechos relevantes."]
        return (
            f"Comparacao com backup automatico de `{rel}`:\n"
            "Linhas com '-' existiam no backup; linhas com '+' estao no arquivo atual.\n"
            f"```diff\n{chr(10).join(diff)}\n```"
        )

    def describe_directory_for_agent(self, path, limit=160):
        lines = []
        for index, child in enumerate(sorted(path.rglob("*"))):
            if index >= limit:
                lines.append(f"... mais itens omitidos em {path.name}")
                break
            if any(part in IGNORED_DIRS for part in child.parts):
                continue
            try:
                rel = child.relative_to(self.current_workspace).as_posix()
            except ValueError:
                continue
            kind = "dir " if child.is_dir() else "file"
            lines.append(f"{kind}: {rel}")
        return "\n".join(lines) if lines else "Diretorio vazio."

    def build_requested_backup_diff(self, requested, limit=220):
        pairs = []
        by_rel = {rel: path for path, rel, _line_range in requested}
        for rel, path in by_rel.items():
            if rel.endswith(".bak"):
                original_rel = rel[:-4]
                current = by_rel.get(original_rel) or (Path(self.current_workspace) / original_rel)
                if current.exists():
                    pairs.append((current, path, original_rel, rel))
            else:
                backup = Path(str(path) + ".bak")
                backup_rel = rel + ".bak"
                if backup.exists() and backup_rel in by_rel:
                    pairs.append((path, backup, rel, backup_rel))

        if not pairs:
            return ""

        blocks = []
        for current, backup, current_rel, backup_rel in pairs[:3]:
            try:
                current_lines = current.read_text(encoding="utf-8", errors="replace").splitlines()
                backup_lines = backup.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            diff = list(
                difflib.unified_diff(
                    backup_lines,
                    current_lines,
                    fromfile=backup_rel,
                    tofile=current_rel,
                    lineterm="",
                    n=3,
                )
            )
            if len(diff) > limit:
                diff = diff[:limit] + ["... diff truncado; use [READ] em intervalo especifico se precisar."]
            blocks.append(
                f"Comparacao automatica de backup: {backup_rel} -> {current_rel}\n"
                "Linhas com '-' existiam no backup; linhas com '+' estao no arquivo atual.\n"
                "Use isso para restaurar recursos removidos ou corrigir inversoes.\n"
                "```diff\n"
                + "\n".join(diff)
                + "\n```"
            )
        return "\n\n".join(blocks)

    def build_guarded_file_context(self, path, rel, line_range=None):
        total_lines = self.count_text_file_lines(path)
        history = self.ai_read_history.setdefault(
            rel,
            {
                "overview_count": 0,
                "ranges": [],
                "range_keys": set(),
                "requests": 0,
            },
        )
        history["requests"] += 1

        if line_range:
            start, end = self.normalize_line_range(line_range, total_lines)
            range_key = f"{start}-{end}"
            coverage_before = self.read_coverage_ratio(history["ranges"], total_lines)
            repeated = range_key in history["range_keys"]
            repeated_too_much = repeated and history["requests"] > 20
            if (
                not self.autonomous_unrestricted_mode_enabled()
                and (repeated_too_much or coverage_before >= 0.99 or history["requests"] > 40)
            ):
                reason = "intervalo repetido em excesso" if repeated_too_much else "arquivo ja foi mapeado quase inteiro"
                return self.build_read_stop_context(rel, total_lines, history, reason)
            history["ranges"].append((start, end))
            history["range_keys"].add(range_key)
            block = self.build_file_context_for_agent(path, rel, line_range=(start, end))
        else:
            history["overview_count"] += 1
            coverage_before = self.read_coverage_ratio(history["ranges"], total_lines)
            if (
                not self.autonomous_unrestricted_mode_enabled()
                and history["overview_count"] > 6
                and coverage_before >= 0.90
            ):
                return self.build_read_stop_context(rel, total_lines, history, "visao geral repetida")
            block = self.build_file_context_for_agent(path, rel)

        coverage_after = self.read_coverage_ratio(history["ranges"], total_lines)
        if coverage_after >= 0.99 or len(history["ranges"]) >= 24:
            block += (
                "\n\nCONTROLE DE LEITURA DA IDE:\n"
                f"O arquivo {rel} ja tem cobertura suficiente para continuar: "
                f"{coverage_after:.0%} das linhas cobertas em {len(history['ranges'])} intervalo(s).\n"
                "Voce ja tem bastante contexto deste arquivo; priorize agir ou concluir se a informacao for suficiente."
            )
        return block

    def count_text_file_lines(self, path):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                return sum(1 for _line in file)
        except OSError:
            return 0

    def normalize_line_range(self, line_range, total_lines):
        start, end = line_range
        start = max(1, start)
        if total_lines:
            end = min(max(start, end), total_lines)
        else:
            end = max(start, end)
        return start, end

    def read_coverage_ratio(self, ranges, total_lines):
        if not ranges or total_lines <= 0:
            return 0.0
        merged = []
        for start, end in sorted(ranges):
            if not merged or start > merged[-1][1] + 1:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        covered = sum(end - start + 1 for start, end in merged)
        return min(1.0, covered / total_lines)

    def build_read_stop_context(self, rel, total_lines, history, reason):
        coverage = self.read_coverage_ratio(history["ranges"], total_lines)
        ranges = ", ".join(f"{start}-{end}" for start, end in history["ranges"]) or "nenhum intervalo especifico"
        return (
            f"Leitura bloqueada pela IDE para evitar ciclo infinito: {rel}\n"
            f"Motivo: {reason}.\n"
            f"Total de linhas: {total_lines}\n"
            f"Intervalos ja lidos nesta missao: {ranges}\n"
            f"Cobertura aproximada: {coverage:.0%}\n\n"
            "ORIENTACAO PARA A IA:\n"
            "- Use o contexto ja lido para tomar uma decisao produtiva.\n"
            "- Se a tarefa for modificar, use [REPLACE] ou [WRITE].\n"
            "- Se a tarefa for validar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: comando de teste da stack detectada], ou [HUMAN_TEST].\n"
            "- Se realmente faltar informacao essencial, leia outro ponto especifico e siga trabalhando."
        )

    def parse_agent_read_request(self, raw_path):
        text = raw_path.strip().strip("\"'")
        line_range = None
        patterns = [
            r"^(?P<path>.+?)\s*\|\s*linhas?\s+(?P<start>\d+)\s*(?:[-:]|at[eé]|a)\s*(?P<end>\d+)\s*$",
            r"^(?P<path>.+?)\s*\|\s*lines?\s+(?P<start>\d+)\s*(?:[-:]|to)\s*(?P<end>\d+)\s*$",
            r"^(?P<path>.+?)\s*\|\s*(?P<start>\d+)\s*[-:]\s*(?P<end>\d+)\s*$",
            r"^(?P<path>.+?)#L(?P<start>\d+)(?:-L?(?P<end>\d+))?\s*$",
        ]
        for pattern in patterns:
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                start = int(match.group("start"))
                end = int(match.group("end") or start)
                line_range = (max(1, start), max(start, end))
                text = match.group("path").strip()
                break
        return text, line_range

    def build_file_context_for_agent(self, path, rel, line_range=None):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            content = path.read_text(errors="replace")

        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)

        if line_range:
            start, end = line_range
            selected = lines[start - 1 : end]
            numbered = self.number_lines(selected, start)
            return (
                f"Arquivo lido pela IDE: {rel}\n"
                f"Intervalo solicitado: linhas {start}-{min(end, total_lines)} de {total_lines}\n"
                f"Conteudo:\n```\n{numbered}\n```"
            )

        if total_chars <= 120000:
            return (
                f"Arquivo lido pela IDE: {rel}\n"
                f"Tamanho: {total_lines} linhas, {total_chars} caracteres\n"
                f"Conteudo completo:\n```\n{self.number_lines(lines, 1)}\n```"
            )

        head_count = 260
        tail_count = 180
        head = self.number_lines(lines[:head_count], 1)
        tail_start = max(head_count + 1, total_lines - tail_count + 1)
        tail = self.number_lines(lines[tail_start - 1 :], tail_start)
        index = self.build_large_file_index(lines)

        return (
            f"Arquivo grande lido pela IDE: {rel}\n"
            f"Tamanho: {total_lines} linhas, {total_chars} caracteres\n"
            "A IDE enviou um mapa amplo para preservar desempenho.\n"
            "Para ler uma parte especifica, use: "
            f"[READ: {rel} | linhas inicio-fim]\n\n"
            f"Indice de linhas importantes:\n```\n{index}\n```\n\n"
            f"Inicio do arquivo:\n```\n{head}\n```\n\n"
            f"Final do arquivo:\n```\n{tail}\n```"
        )

    def number_lines(self, lines, start_line=1):
        return "\n".join(f"{start_line + index:>5}: {line}" for index, line in enumerate(lines))

    def build_large_file_index(self, lines, limit=220):
        interesting = []
        patterns = [
            r"^\s*(class|def|async\s+def|function|const|let|var|final|void|Widget|Future<|Stream<)\b",
            r"^\s*(import|from|include|#include|target_|add_|set\(|project\(|dependencies:|dev_dependencies:)\b",
            r"^\s*(if|for|while|switch|try|catch)\b",
            r"(TODO|FIXME|ERROR|Exception|throw|raise|return\s+)",
        ]
        combined = re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.IGNORECASE)
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if combined.search(stripped):
                interesting.append(f"{index:>5}: {line[:180]}")
            if len(interesting) >= limit:
                interesting.append("... indice truncado; peca um intervalo de linhas para detalhes.")
                break
        if not interesting:
            interesting = [
                "Nenhuma estrutura obvia detectada.",
                "Peca um intervalo especifico com [READ: arquivo | linhas inicio-fim].",
            ]
        return "\n".join(interesting)

    def undo_last_change(self, raw_path=None):
        try:
            workspace = str(Path(self.current_workspace).resolve())
            target_rel = ""
            if raw_path:
                raw_normalized = self.normalize_plain_text(raw_path)
                if raw_normalized in {"ultima", "ultimo", "last", "alteracao", "mudanca"}:
                    raw_path = None
                else:
                    try:
                        target = self.resolve_workspace_path(raw_path)
                        target_rel = target.relative_to(self.current_workspace).as_posix()
                    except Exception:
                        target_rel = raw_path.strip().replace("\\", "/")
            if raw_path and target_rel:
                pass
            elif not raw_path:
                target_rel = ""

            for index in range(len(self.change_history) - 1, -1, -1):
                record = self.change_history[index]
                if record.get("workspace") != workspace or record.get("undone"):
                    continue
                if target_rel and record.get("rel") != target_rel and Path(record.get("path", "")).name != Path(target_rel).name:
                    continue

                path = Path(record.get("path", ""))
                rel = record.get("rel") or path.name
                if record.get("existed"):
                    backup = Path(record.get("backup", ""))
                    if not backup.exists():
                        return f"Nao encontrei o backup historico para restaurar `{rel}`."
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, path)
                    action = "restaurado"
                else:
                    if path.exists():
                        path.unlink()
                    action = "removido porque foi criado pela alteracao desfeita"

                record["undone"] = True
                record["undone_at"] = datetime.now().isoformat(timespec="seconds")
                self._save_change_history()
                self.log_agent(f"Alteracao desfeita: {rel}")
                self.load_workspace_files()
                return f"Desfiz a ultima alteracao em `{rel}`. O arquivo foi {action} a partir do historico da IDE."

            return "Nao encontrei alteracao recente para desfazer neste projeto."
        except Exception as exc:
            return f"Falha ao desfazer alteracao: {exc}"

    def restore_main_backup(self):
        try:
            workspace = Path(self.current_workspace).resolve()
            candidates = []
            for name in ("index.html.bak", "app.py.bak", "main.py.bak"):
                backup = workspace / name
                if backup.exists():
                    candidates.append(backup)
            if not candidates:
                candidates = sorted(
                    workspace.glob("*.bak"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            if not candidates:
                return None

            backup = candidates[0]
            target = backup.with_suffix("")
            if not target.name:
                return None
            if target.exists():
                self.record_file_change_snapshot(target, "RESTORE_BACKUP", f"Restauracao de {backup.name}")
            shutil.copy2(backup, target)
            rel = target.relative_to(workspace).as_posix()
            self.log_agent(f"Backup principal restaurado: {backup.name} -> {rel}")
            self.load_workspace_files()
            return f"Restaurei `{rel}` usando o backup `{backup.name}`."
        except Exception as exc:
            return f"Falha ao restaurar backup: {exc}"

    def _agent_undo(self, raw_path):
        try:
            history_reply = self.undo_last_change(raw_path)
            if history_reply and "Nao encontrei alteracao recente" not in history_reply:
                self.add_chat_message("Sistema", history_reply)
                return
            if self.normalize_plain_text(raw_path) in {"ultima", "ultimo", "last", "alteracao", "mudanca"}:
                fallback = self.restore_main_backup()
                if fallback:
                    self.add_chat_message("Sistema", fallback)
                    return

            path = self.resolve_workspace_path(raw_path)
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.exists():
                self.add_chat_message("Sistema", f"Nenhum backup encontrado para {path.name}.")
                return
            shutil.copy2(backup, path)
            self.log_agent(f"Backup restaurado: {path.name}")
            self.load_workspace_files()
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao desfazer: {exc}")

    def is_admin_execute_request(self, command):
        normalized = self.normalize_plain_text(command or "")
        admin_markers = (
            "como administrador",
            "modo administrador",
            "permissao de administrador",
            "permissoes de administrador",
            "privilegio de administrador",
            "privilegios de administrador",
            "permissao elevada",
            "permissoes elevadas",
            "privilegio elevado",
            "privilegios elevados",
            "as administrator",
            "run as administrator",
            "elevated",
            "elevado",
            "-verb runas",
        )
        admin_switch = re.search(r"(?:^|\s)(?:--admin|/admin)(?:\s|$)", normalized)
        admin_fuzzy = re.search(
            r"(?:permiss.o|permiss.es|privilegi(?:o|os)|privil.gi(?:o|os))\s+(?:de\s+administrador|elevad[oa]s?)",
            normalized,
        )
        return (
            any(marker in normalized for marker in admin_markers)
            or bool(admin_switch)
            or bool(admin_fuzzy)
            or normalized.startswith(("admin:", "elevated:"))
        )

    def clean_admin_command(self, command):
        cleaned = (command or "").strip()
        cleaned = re.sub(r"^\s*(?:admin|elevated)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:como|modo)\s+administrador\s*[:,-]?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:as|run\s+as)\s+administrator\s*[:,-]?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:elevado|elevated)\s*[:,-]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:^|\s+)(?:com\s+)?(?:permiss(?:a|ã|\?)o|permiss(?:o|õ|\?)es)\s+de\s+administrador\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        trailing_admin_patterns = (
            r"(?:^|\s+)(?:com\s+)?permiss(?:ao|oes|aoes|aos|\?o|\?es)\s+de\s+administrador\s*$",
            r"(?:^|\s+)(?:com\s+)?(?:privilegi|privil\?gi)(?:o|os)\s+de\s+administrador\s*$",
            r"(?:^|\s+)(?:com\s+)?(?:privilegi|privil\?gi)(?:o|os)\s+elevad(?:o|os)\s*$",
            r"(?:^|\s+)(?:com\s+)?permiss(?:ao|oes|aoes|aos|\?o|\?es)\s+elevad(?:a|as)\s*$",
        )
        for pattern in trailing_admin_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:^|\s+)(?:com\s+)?(?:permiss(?:a|ã|\?)o|permiss(?:o|õ|\?)es)\s+de\s+administrador\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"(?:^|\s+)(?:como|modo)\s+administrador\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:^|\s+)(?:as|run\s+as)\s+administrator\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:^|\s+)(?:elevado|elevated)\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:^|\s+)(?:--admin|/admin)\s*$", "", cleaned, flags=re.IGNORECASE)
        admin_suffix_patterns = (
            r"(?:^|\s+)(?:com\s+)?permiss(?:ao|oes|\?o|\?es|\u00e3o|\u00f5es)\s+de\s+administrador\s*$",
            r"(?:^|\s+)(?:com\s+)?privil(?:e|\u00e9)gi(?:o|os)\s+de\s+administrador\s*$",
            r"(?:^|\s+)(?:com\s+)?privil(?:e|\u00e9)gi(?:o|os)\s+elevad(?:o|os)\s*$",
            r"(?:^|\s+)(?:com\s+)?permiss(?:ao|oes|\u00e3o|\u00f5es)\s+elevad(?:a|as)\s*$",
            r"(?:^|\s+)(?:como|modo)\s+administrador\s*$",
            r"(?:^|\s+)(?:as|run\s+as)\s+administrator\s*$",
            r"(?:^|\s+)(?:elevado|elevated)\s*$",
            r"(?:^|\s+)-verb\s+runas\s*$",
            r"(?:^|\s+)(?:--admin|/admin)\s*$",
        )
        for pattern in admin_suffix_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        normalized_suffixes = (
            " com permissao de administrador",
            " permissao de administrador",
            " permissoes de administrador",
            " com privilegio de administrador",
            " privilegio de administrador",
            " privilegios de administrador",
            " com privilegio elevado",
            " privilegio elevado",
            " privilegios elevados",
            " com permissao elevada",
            " permissao elevada",
            " permissoes elevadas",
        )
        normalized = self.normalize_plain_text(cleaned)
        for suffix in normalized_suffixes:
            if normalized.endswith(suffix):
                cleaned = cleaned[: max(0, len(cleaned) - len(suffix))].rstrip()
                break
        return cleaned.strip()

    def is_placeholder_command(self, command):
        candidates = [command, self.clean_admin_command(command)]
        raw_text = str(command or "").strip().strip("`\"'").strip()
        tag_match = re.fullmatch(
            r"\[(?:EXECUTE|EXECUTE_ADMIN)\s*:\s*(.*?)\]",
            raw_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if tag_match:
            candidates.append(tag_match.group(1))
        inline_tag_match = re.fullmatch(
            r"(?:EXECUTE|EXECUTE_ADMIN)\s*:\s*(.*?)",
            raw_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if inline_tag_match:
            candidates.append(inline_tag_match.group(1))
        placeholders = {
            "...",
            "\u2026",
            "?",
            "`",
            "``",
            "```",
            "admin:",
            "administrator",
            "as administrator",
            "como administrador",
            "e termine com",
            "termine com",
            "comece com",
            "comece com e termine com",
            "start with and end with",
            "start with",
            "end with",
            "comando",
            "<comando>",
            "o comando",
            "comando real",
            "o comando real",
            "<comando real>",
            "comando completo",
            "o comando completo",
            "<comando completo>",
            "comando concreto",
            "o comando concreto",
            "<comando concreto>",
            "comando real do projeto",
            "comando completo do projeto",
            "comando concreto do projeto",
            "comando completo aqui",
            "comando concreto aqui",
            "comando aqui",
            "um comando real",
            "um comando concreto",
            "command",
            "<command>",
            "command here",
            "complete command",
            "<complete command>",
            "concrete command",
            "<concrete command>",
            "elevated:",
            "modo administrador",
            "run as administrator",
            "seu comando",
            "seu comando aqui",
            "your command",
            "your command here",
            "--admin",
            "/admin",
        }
        placeholder_cores = {
            re.sub(r"[\s.<>\[\]{}()_\-\u2026?]+", "", item)
            for item in placeholders
        }
        placeholder_patterns = (
            r"^(?:o\s+|um\s+)?comando(?:\s+(?:real|completo|concreto|aqui|do\s+projeto|ja\s+preenchido|preenchido))+$",
            r"^(?:seu|your)\s+comando(?:\s+aqui)?$",
            r"^<[^>]*(?:comando|command)[^>]*>$",
            r"^\[[^\]]*(?:\.\.\.|\u2026|comando|command)[^\]]*\]$",
            r"^(?:cmd(?:\.exe)?\s+)?/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)['\"]?$",
            r"^cmd(?:\.exe)?\s+/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)['\"]?$",
            r"^(?:powershell|pwsh)(?:\.exe)?\s+.*(?:-command|-c)\s+['\"]?(?:\.{3}|\u2026|comando|command)['\"]?$",
            r"^(?:shell|terminal|exec|execute|executar|rodar)\s*[:=-]?\s*(?:\.{3}|\u2026|comando|command)$",
            r"^(?:e\s+)?termine\s+com$",
            r"^(?:comece|comeca)\s+com(?:\s+e\s+termine\s+com)?$",
            r"^start\s+with(?:\s+and\s+end\s+with)?$",
            r"^end\s+with$",
        )
        for candidate in candidates:
            raw_candidate = str(candidate or "").strip()
            for variant in {raw_candidate, raw_candidate.replace("`", "")}:
                text = variant.strip().strip("`\"'").strip()
                lowered = re.sub(r"\s+", " ", self.normalize_plain_text(text)).strip()
                shell_core = re.sub(r"[\s.<>\[\]{}()_`\-\u2026?]+", "", lowered)
                if not shell_core or lowered in placeholders or shell_core in placeholder_cores:
                    return True
                if any(re.fullmatch(pattern, lowered) for pattern in placeholder_patterns):
                    return True
                if self.shell_payload_is_placeholder(lowered):
                    return True
        return False

    def shell_payload_is_placeholder(self, normalized_command):
        text = (normalized_command or "").strip()
        if not text:
            return True
        shell_payload_patterns = (
            r"^(?:/[ck]\s+)(.+)$",
            r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)(.+)$",
            r"^(?:(?:powershell|pwsh)(?:\.exe)?\b.*?(?:-command|-c)\s+)(.+)$",
        )
        for pattern in shell_payload_patterns:
            match = re.match(pattern, text)
            if not match:
                continue
            payload = match.group(1).strip().strip("`\"'").strip()
            payload_placeholders = {
                "...",
                "\u2026",
                "?",
                "comando",
                "comando real",
                "comando completo",
                "comando concreto",
                "comando aqui",
                "comando completo aqui",
                "comando concreto aqui",
                "um comando real",
                "um comando concreto",
                "seu comando",
                "seu comando aqui",
                "command",
                "command here",
                "complete command",
                "concrete command",
                "your command",
                "your command here",
            }
            payload_placeholder_cores = {
                re.sub(r"[\s.<>\[\]{}()_\-./\\:;|&=\u2026?]+", "", item)
                for item in payload_placeholders
            }
            payload_patterns = (
                r"^(?:o\s+|um\s+)?comando(?:\s+(?:real|completo|concreto|aqui|do\s+projeto|ja\s+preenchido|preenchido))+$",
                r"^(?:seu|your)\s+comando(?:\s+aqui)?$",
                r"^<[^>]*(?:comando|command)[^>]*>$",
                r"^command(?:\s+(?:real|complete|concrete|here))?$",
            )
            payload_core = re.sub(r"[\s.<>\[\]{}()_\-./\\:;|&=\u2026?]+", "", payload)
            if (
                not payload_core
                or payload in payload_placeholders
                or payload_core in payload_placeholder_cores
                or any(re.fullmatch(pattern, payload) for pattern in payload_patterns)
            ):
                return True
        return False

    def reject_placeholder_execute_action(self, action_name, command):
        if not self.is_placeholder_command(command):
            return False
        action_name = (action_name or "EXECUTE").upper()
        if action_name == "EXECUTE_ADMIN":
            self.add_chat_message(
                "Erro",
                "EXECUTE_ADMIN precisa receber um comando real. A IDE recusou reticencias ou texto demonstrativo antes de abrir UAC.",
            )
            self.add_chat_message(
                "Sistema",
                "Para pedir administrador, envie uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].",
            )
            self.log_agent("Execucao elevada recusada no parser: comando placeholder.")
            return True
        self.add_chat_message(
            "Erro",
            "EXECUTE precisa receber um comando real. A IDE recusou reticencias ou texto demonstrativo antes do terminal.",
        )
        self.add_chat_message(
            "Sistema",
            "A IDE bloqueou um comando sem conteudo real. Isso evita repetir placeholders no terminal.",
        )
        self.log_agent("Execucao recusada no parser: comando placeholder.")
        return True

    def command_output_is_placeholder_error(self, command, output):
        if self.is_placeholder_command(command):
            return True
        normalized = self.normalize_plain_text(output or "")
        if (
            any(token in normalized for token in ("...", "\u2026", "'comando'", "'command'"))
            and any(marker in normalized for marker in ("reconhecido", "recognized"))
        ):
            return True
        placeholder_error_patterns = (
            r"'\s*(?:\.\.\.|\u2026)\s*'.*nao e reconhecido",
            r"'\s*(?:\.\.\.|\u2026)\s*'.*not recognized",
            r"'\s*comando(?:\s+(?:real|completo|concreto|aqui))?\s*'.*nao e reconhecido",
            r"'\s*command(?:\s+(?:real|complete|concrete|here))?\s*'.*not recognized",
            r"'\s*(?:comando|command)\s*'.*nao e reconhecido",
            r"'\s*(?:comando|command)\s*'.*not recognized",
            r"'\s*`+\s*'.*nao e reconhecido",
            r"'\s*`+\s*'.*not recognized",
            r"(?:^|\s)(?:\.{3}|\u2026)(?:\s|$).*nao e reconhecido",
            r"(?:^|\s)(?:\.{3}|\u2026)(?:\s|$).*not recognized",
        )
        return any(re.search(pattern, normalized) for pattern in placeholder_error_patterns)

    def command_output_requires_admin(self, output):
        normalized = self.normalize_plain_text(output or "")
        markers = (
            "requires elevation",
            "requested operation requires elevation",
            "operation requires elevation",
            "error 740",
            "erro 740",
            "winerror 5",
            "access is denied",
            "access denied",
            "acesso negado",
            "permission denied",
            "privilegios de administrador",
            "privilegio de administrador",
            "administrator privileges",
            "administrative privileges",
            "run as administrator",
            "execute como administrador",
            "requer eleva",
            "requer elevacao",
            "requer privilegios elevados",
            "permissao elevada",
            "permissoes elevadas",
        )
        return any(marker in normalized for marker in markers)

    def powershell_quote(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def ask_admin_command_approval(self, command, requester="A IA"):
        requester = requester or "A IA"
        title = "Autorizar comando como administrador?"
        message = (
            f"{requester} pediu para executar um comando com privilegios de administrador.\n\n"
            f"Comando:\n{command}\n\n"
            "Se voce aceitar, o Windows ainda vai mostrar o prompt UAC."
        )
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(title, message)

        result_queue = queue.Queue(maxsize=1)

        def ask():
            try:
                result_queue.put(bool(messagebox.askyesno(title, message)))
            except Exception as exc:
                result_queue.put(exc)

        self.after(0, ask)
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return bool(result)

    def _agent_execute_admin(
        self,
        command,
        task_objective=None,
        action_depth=0,
        task_id=None,
        requester="A IA",
        terminal_source="via IA como administrador",
    ):
        if self.is_task_cancelled(task_id):
            self.log_agent(f"Comando elevado ignorado apos cancelamento: {command}")
            return
        command = self.clean_admin_command(command)
        if self.is_placeholder_command(command):
            self.add_chat_message(
                "Erro",
                "EXECUTE_ADMIN precisa receber um comando real. Nao use reticencias nem texto como 'como administrador'.",
            )
            self.log_agent("Execucao elevada recusada: comando vazio ou placeholder.")
            self.add_chat_message(
                "Sistema",
                "A IDE bloqueou um pedido de administrador sem comando real. Para elevar, envie uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].",
            )
            return
        if os.name != "nt":
            self.add_chat_message("Erro", "EXECUTE_ADMIN esta implementado para Windows/UAC neste momento.")
            return

        try:
            approved = self.ask_admin_command_approval(command, requester=requester)
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao pedir autorizacao de administrador: {exc}")
            return

        if not approved:
            self.add_chat_message("Sistema", "Execucao elevada cancelada pelo usuario.")
            self.log_agent(f"Execucao elevada negada pelo usuario: {command}")
            return

        self.log_agent(f"Solicitando UAC para comando: {command}")
        self.append_to_term(f"\n> {command} ({terminal_source})\n")
        self.tabview.set("Terminal Local")
        self.set_ai_busy(True)
        self.set_ai_activity("IA aguardando autorizacao do Windows")
        self.set_terminal_busy(True, f"Admin: {command[:70]}")

        def run():
            try:
                workspace = str(Path(self.current_workspace).resolve())
                elevated_command = (
                    f'cd /d "{workspace}" && {command} '
                    "& echo. & echo [Merotec IA] Comando elevado finalizado. "
                    "& echo Feche esta janela quando terminar."
                )
                script = (
                    "$argList = @('/k', "
                    f"{self.powershell_quote(elevated_command)}"
                    "); "
                    "Start-Process -FilePath 'cmd.exe' -ArgumentList $argList -Verb RunAs"
                )
                process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.current_workspace,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                output, _ = process.communicate(timeout=30)
                if output:
                    self.append_to_term(output)
                if process.returncode == 0:
                    self.append_to_term("\n[pedido UAC enviado ao Windows]\n")
                    self.add_chat_message(
                        "Sistema",
                        "Pedido de administrador enviado. Confirme o UAC do Windows para o comando rodar em uma janela elevada.",
                    )
                else:
                    self.append_to_term(f"\n[falha ao solicitar UAC: codigo {process.returncode}]\n")
                    self.add_chat_message(
                        "Erro",
                        f"Nao consegui abrir o prompt de administrador.\n\n{(output or '').strip()}",
                    )
            except subprocess.TimeoutExpired:
                self.append_to_term("\n[timeout ao solicitar UAC]\n")
                self.add_chat_message("Erro", "O pedido de administrador demorou demais para responder.")
            except Exception as exc:
                self.add_chat_message("Erro", f"Falha na execucao elevada: {exc}")
            finally:
                self.set_terminal_busy(False)
                self.set_ai_busy(False)

        threading.Thread(target=run, daemon=True).start()

    def _agent_execute(self, command, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            self.log_agent(f"Comando ignorado apos cancelamento: {command}")
            return
        command = command.strip()
        if self.is_placeholder_command(command):
            self.add_chat_message(
                "Erro",
                "EXECUTE precisa receber um comando real. A IDE recusou reticencias ou placeholder.",
            )
            self.log_agent("Execucao recusada: comando vazio ou placeholder.")
            self.add_chat_message(
                "Sistema",
                "A IDE bloqueou um comando sem conteudo real. Isso evita repetir reticencias no terminal.",
            )
            return
        if self.is_admin_execute_request(command):
            admin_command = self.clean_admin_command(command)
            self._agent_execute_admin(
                admin_command,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return
        if self.is_http_server_command(command):
            self._agent_start_http_server(command, task_objective=task_objective, action_depth=action_depth, task_id=task_id)
            return

        self.log_agent(f"Executando comando da IA: {command}")
        self.append_to_term(f"\n> {command} (via IA)\n")
        self.tabview.set("Terminal Local")
        self.set_ai_busy(True)
        self.set_ai_activity("IA executando comando")
        self.set_terminal_busy(True, f"IA executando: {command[:70]}")

        def run():
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.current_workspace,
                )
                self.register_terminal_process(process, f"IA: {command}")
                output = self.stream_process_output(process, collect=True)
                process.wait()
                if self.is_task_cancelled(task_id):
                    return
                if not output:
                    self.append_to_term(f"[sem saida] codigo {process.returncode}\n")
                self.append_to_term(f"\n[processo da IA finalizado com codigo {process.returncode}]\n")
                diagnostic = ""
                if process.returncode == 0:
                    self.get_ai_task_metrics(task_id).pop("requires_error_correction", None)
                    if self.advance_autonomous_delivery_after_validation(
                        command,
                        task_objective=task_objective,
                        action_depth=action_depth,
                        task_id=task_id,
                    ):
                        return
                    # Um comando concluído com sucesso também é evidência para
                    # a IA. Sem este retorno, EXECUTE finito terminava em
                    # silêncio e a missão ficava parada mesmo sem correção.
                    if self.should_continue_development_loop(action_depth, task_id):
                        context = (
                            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or command}\n\n"
                            f"Comando executado com sucesso: {command}\n"
                            f"Codigo de saida: 0\n"
                            f"Saida:\n```\n{(output or '')[:6000]}\n```\n\n"
                            "ORDEM DA IDE:\n"
                            "- O comando terminou; use a saída como evidência.\n"
                            "- Não repita o mesmo EXECUTE sem uma mudança ou novo motivo.\n"
                            "- Continue a missão: corrija com [REPLACE]/[WRITE], faça [HUMAN_TEST: auto] quando necessário, ou entregue conclusão objetiva somente se a missão estiver realmente concluída.\n"
                        )
                        self._run_ai_task(
                            "O comando terminou com sucesso. Continue a missão original usando a evidência retornada.",
                            extra_context=context,
                            task_objective=task_objective or self.active_ai_objective or command,
                            action_depth=action_depth + 1,
                            task_id=task_id,
                        )
                    else:
                        self.add_chat_message(
                            "Erro",
                            "O ciclo contínuo foi pausado pelo limite configurado. O último resultado foi mantido no Terminal Local.",
                        )
                        self.set_status("Limite configurado do ciclo atingido.", "warning")
                    return

                if process.returncode != 0:
                    diagnostic = self.build_command_failure_diagnostic(command, output, process.returncode)
                    metrics = self.get_ai_task_metrics(task_id)
                    metrics["requires_error_correction"] = {
                        "kind": "execute",
                        "command": command,
                        "returncode": int(process.returncode),
                        "output_tail": (output or "")[-12000:],
                        "created_at": time.time(),
                    }
                    self.add_chat_message(
                        "Sistema",
                        "Comando falhou. A IDE vai devolver a saida real do terminal ao mesmo Chat Web antes da proxima acao.",
                    )
                    if not self.can_continue_autonomous_repair(
                        command,
                        output,
                        process.returncode,
                        task_objective=task_objective,
                        task_id=task_id,
                    ):
                        return
                    if self.command_output_is_placeholder_error(command, output):
                        self.add_chat_message(
                            "Sistema",
                            "A IDE interrompeu a repeticao: o terminal recebeu um placeholder em vez de um comando real.",
                        )
                        self.add_chat_message(
                            "Merotec AI",
                            "Para pedir administrador, use EXECUTE_ADMIN somente com comando real ja preenchido, por exemplo [EXECUTE_ADMIN: whoami /groups]. Sem comando real, a resposta correta e concluir em texto.",
                        )
                        return
                    if self.command_output_requires_admin(output):
                        self.add_chat_message(
                            "Sistema",
                            "O comando falhou por permissao/elevacao. A IDE vai pedir autorizacao de administrador ao usuario.",
                        )
                        self._agent_execute_admin(
                            command,
                            task_objective=task_objective,
                            action_depth=action_depth,
                            task_id=task_id,
                        )
                        return
                context = (
                    "DIAGNOSTICO DE FALHA GERADO PELA IDE — PRIORIDADE MAXIMA:\n"
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or command}\n\n"
                    f"Comando executado: {command}\n"
                    f"Codigo de saida: {process.returncode}\n"
                    f"{diagnostic}\n"
                    f"SAIDA REAL DO TERMINAL (trecho final):\n```\n{(output or '')[-12000:]}\n```\n\n"
                    "ORDEM DA IDE:\n"
                    "- Se o comando falhou, nao repita o mesmo comando agora.\n"
                    "- Leia ou altere os arquivos suspeitos primeiro.\n"
                    "- A proxima acao deve ser [READ], [SCAN_TEXT], [FIX_MOJIBAKE], [REPLACE] ou [WRITE], exceto se a saida provar que nao ha arquivo a corrigir.\n"
                )
                if process.returncode != 0:
                    self._run_ai_task(
                        "Analise o erro do comando e continue a missao original aplicando a correcao.",
                        extra_context=context,
                        task_objective=task_objective or self.active_ai_objective or command,
                        action_depth=action_depth + 1,
                        task_id=task_id,
                    )
            except Exception as exc:
                self.add_chat_message("Erro", f"Falha na execucao autonoma: {exc}")
            finally:
                if "process" in locals():
                    self.unregister_terminal_process(process)
                self.set_terminal_busy(False)
                self.set_ai_busy(False)

        threading.Thread(target=run, daemon=True).start()

    def is_http_server_command(self, command):
        normalized = self.normalize_plain_text(command or "")
        return bool(re.search(r"\b(python|py|python3)\b\s+-m\s+http\.server\b", normalized))

    def parse_http_server_command(self, command):
        port_match = re.search(r"\bhttp\.server\b\s+(\d{2,5})", command or "", re.IGNORECASE)
        port = int(port_match.group(1)) if port_match else 8000
        directory = Path(self.current_workspace).resolve()
        dir_match = re.search(r"--directory\s+([^\r\n]+?)(?:\s+--|\s*$)", command or "", re.IGNORECASE)
        if dir_match:
            raw_dir = dir_match.group(1).strip().strip("\"'")
            try:
                directory = self.resolve_workspace_path(raw_dir)
            except Exception:
                directory = Path(self.current_workspace).resolve()
        return max(1024, min(65535, port)), directory

    def find_available_port(self, preferred_port, attempts=40):
        for offset in range(attempts):
            port = preferred_port + offset
            if port > 65535:
                break
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.25)
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return None

    def _agent_start_http_server(self, command, task_objective=None, action_depth=0, task_id=None):
        preferred_port, directory = self.parse_http_server_command(command)
        port = self.find_available_port(preferred_port)
        if port is None:
            self.add_chat_message("Erro", "Nao encontrei uma porta local livre para iniciar o servidor.")
            return

        server_command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
        self.log_agent(f"Iniciando servidor local da IA: {directory} porta {port}")
        self.append_to_term(
            f"\n> servidor local via IA: {Path(sys.executable).name} -m http.server {port} --bind 127.0.0.1\n"
        )
        self.tabview.set("Terminal Local")
        self.set_ai_busy(True)
        self.set_ai_activity("IA iniciando servidor")
        self.set_terminal_busy(True, f"Servidor local: http://127.0.0.1:{port}")

        def run():
            process = None
            try:
                process = subprocess.Popen(
                    server_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(directory),
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self.register_terminal_process(process, f"Servidor IA: {port}")
                threading.Thread(
                    target=self.stream_managed_server_output,
                    args=(process,),
                    daemon=True,
                ).start()
                url = f"http://127.0.0.1:{port}/"
                test_url = self.pick_http_server_test_url(directory, url)
                ok, detail = self.wait_for_http_server(test_url)
                if ok:
                    self.append_to_term(f"\n[servidor pronto] {test_url}\n")
                    self._agent_open_url(test_url)
                    self.add_chat_message(
                        "Merotec AI",
                        f"Servidor local iniciado e testado com sucesso.\n\nURL: {test_url}\n\nUse Cancelar para encerrar o servidor quando terminar.",
                    )
                    self.set_status(f"Servidor rodando: {test_url}", "busy")
                else:
                    self.append_to_term(f"\n[servidor iniciou, mas o teste falhou] {detail}\n")
                    self.add_chat_message(
                        "Erro",
                        f"O servidor foi iniciado, mas a IDE nao conseguiu validar a URL.\n\n{detail}",
                    )
            except Exception as exc:
                self.add_chat_message("Erro", f"Falha ao iniciar servidor local: {exc}")
                self.append_to_term(f"\n[erro ao iniciar servidor] {exc}\n")
            finally:
                self.set_ai_busy(False)
                if not process or process.poll() is not None:
                    self.set_terminal_busy(False)

        threading.Thread(target=run, daemon=True).start()

    def pick_http_server_test_url(self, directory, base_url):
        index = Path(directory) / "index.html"
        if index.exists():
            return base_url.rstrip("/") + "/index.html"
        return base_url

    def stream_managed_server_output(self, process):
        try:
            self.stream_process_output(process)
            process.wait()
            self.append_to_term(f"\n[servidor finalizado com codigo {process.returncode}]\n")
        finally:
            self.unregister_terminal_process(process)
            if not self.has_terminal_processes():
                self.set_terminal_busy(False)

    def wait_for_http_server(self, url, attempts=25, delay=0.2):
        last_error = ""
        for _attempt in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    status = getattr(response, "status", 200)
                    if 200 <= status < 500:
                        return True, f"HTTP {status}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
                time.sleep(delay)
        return False, last_error or "sem resposta do servidor"


# MEROTEC_VISUAL_AUTONOMY_V3
def _merotec_workspace_has_visual_target(instance):
    try:
        workspace = Path(instance.current_workspace).resolve()
    except Exception:
        return False
    if (workspace / "index.html").exists() or any(workspace.glob("*.html")):
        return True
    if (workspace / "package.json").exists():
        return True
    for candidate in (workspace / "main.py", workspace / "app.py"):
        if not candidate.exists():
            continue
        try:
            source = candidate.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if any(token in source for token in ("tkinter", "customtkinter", "pygame", "pyqt", "pyside", "kivy", "flet", "flask", "fastapi", "streamlit")):
            return True
    return False


def _merotec_autonomous_visual_test_required(self, task_objective=None):
    if not self.bool_setting_enabled("autonomous_visual_validation_enabled", env_name="MEROTEC_AUTONOMOUS_VISUAL_VALIDATION", default=True):
        return False
    return _merotec_workspace_has_visual_target(self)


def _merotec_is_local_visual_url(raw_url):
    value = str(raw_url or "").strip().strip("\"'")
    if not value:
        return False
    if value.startswith("file:"):
        return True
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "http://" + value
    try:
        host = (urllib.parse.urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


_original_merotec_agent_open_url = AgentActionsMixin._agent_open_url
_original_merotec_grab_human_test_image = AgentActionsMixin.grab_human_test_image
_original_merotec_human_test_ready = AgentActionsMixin.human_test_window_is_ready
_original_merotec_should_route_execute = AgentActionsMixin.should_route_execute_to_human_test


def _merotec_agent_open_url(self, raw_url):
    if _merotec_is_local_visual_url(raw_url):
        self._merotec_visual_test_open_error = ""
        opener = getattr(self, "open_visual_test_browser", None)
        if callable(opener):
            result = opener(raw_url)
            if result.get("opened"):
                self.add_chat_message(
                    "Merotec AI",
                    f"Teste visual aberto no navegador dedicado: {result.get('url', raw_url)}",
                )
                self.log_agent(f"Navegador visual dedicado abriu: {result.get('url', raw_url)}")
                return result.get("url", raw_url)
            detail = result.get('error') or 'sem confirmação do WebView2'
            self._merotec_visual_test_open_error = detail
            self.add_chat_message(
                "Erro",
                "A página local respondeu, mas o navegador visual não abriu. "
                f"Detalhe: {detail}",
            )
            return ""
    return _original_merotec_agent_open_url(self, raw_url)


def _merotec_grab_human_test_image(self, plan):
    open_error = str(getattr(self, "_merotec_visual_test_open_error", "") or "")
    if open_error:
        self._merotec_visual_test_open_error = ""
        raise RuntimeError(f"Teste visual bloqueado porque o navegador dedicado não abriu: {open_error}")
    info_getter = getattr(self, "get_visual_test_browser_info", None)
    info = info_getter() if callable(info_getter) else {}
    if info and info.get("title") and _merotec_is_local_visual_url(info.get("url")):
        image = self.grab_window_image_by_title(info["title"], timeout=12.0)
        if image is not None:
            return image
        raise RuntimeError("O navegador de teste abriu, mas a janela visual não pôde ser capturada.")
    return _original_merotec_grab_human_test_image(self, plan)


def _merotec_human_test_window_is_ready(self, plan):
    info_getter = getattr(self, "get_visual_test_browser_info", None)
    info = info_getter() if callable(info_getter) else {}
    if info and info.get("ready"):
        return True
    return _original_merotec_human_test_ready(self, plan)


def _merotec_should_route_execute_to_human_test(self, command, task_objective=None):
    if _original_merotec_should_route_execute(self, command, task_objective):
        return True
    normalized = self.normalize_plain_text(command or "")
    if "http.server" in normalized and self.autonomous_visual_test_required(task_objective):
        return True
    if any(marker in normalized for marker in ("npm run dev", "npm start", "flutter run")) and self.autonomous_visual_test_required(task_objective):
        return True
    return False


AgentActionsMixin.autonomous_visual_test_required = _merotec_autonomous_visual_test_required
AgentActionsMixin._agent_open_url = _merotec_agent_open_url
AgentActionsMixin.grab_human_test_image = _merotec_grab_human_test_image
AgentActionsMixin.human_test_window_is_ready = _merotec_human_test_window_is_ready
AgentActionsMixin.should_route_execute_to_human_test = _merotec_should_route_execute_to_human_test


# MEROTEC_CODE_PIPELINE_V5
# Camada única: sem wrappers recursivos e sem rejeitar alteração simples só
# porque o Chat Web não usou cerca Markdown.

from modules.code_integrity import (
    unwrap_outer_fence as _merotec_v5_unwrap,
    validate_source as _merotec_v5_validate,
)

_merotec_v5_base_write = AgentActionsMixin._agent_write
_merotec_v5_base_replace = AgentActionsMixin._agent_replace
_merotec_v5_base_patch = AgentActionsMixin._agent_apply_patch
_merotec_v5_base_validation = AgentActionsMixin.infer_default_validation_command


def _merotec_v5_report_rejection(self, path, issue, task_id=None):
    try:
        relative = Path(path).resolve().relative_to(Path(self.current_workspace).resolve()).as_posix()
    except Exception:
        relative = Path(path).name or str(path)

    line = int(issue.get("line") or 0)
    location = f" (linha {line})" if line else ""
    self.add_chat_message(
        "Erro",
        f"Alteração recusada antes de salvar em `{relative}`{location}: "
        f"{issue.get('kind')}: {issue.get('message')}. O arquivo válido foi preservado.",
    )
    self.log_agent(
        f"Validação de código recusou {relative}{location}: "
        f"{issue.get('kind')}: {issue.get('message')}"
    )
    metrics = self.get_ai_task_metrics(task_id)
    metrics["code_pipeline_last_rejection"] = {
        "path": relative,
        "kind": issue.get("kind"),
        "line": line,
        "message": issue.get("message"),
    }


def _merotec_v5_write(self, raw_path, content, task_id=None, task_objective=None):
    try:
        path = self.resolve_workspace_path(raw_path)
        candidate = _merotec_v5_unwrap(content)
        issue = _merotec_v5_validate(path, candidate)
        if issue:
            _merotec_v5_report_rejection(self, path, issue, task_id)
            return False
    except Exception as exc:
        self.log_agent(f"Pré-validação WRITE indisponível: {exc}")
    return _merotec_v5_base_write(
        self,
        raw_path,
        content,
        task_id=task_id,
        task_objective=task_objective,
    )


def _merotec_v5_replace(self, raw_path, old_content, new_content, task_id=None, task_objective=None):
    try:
        path = self.resolve_workspace_path(raw_path)
        if path.exists() and path.is_file():
            current = path.read_text(encoding="utf-8", errors="replace")
            old_text = _merotec_v5_unwrap(old_content)
            new_text = _merotec_v5_unwrap(new_content)
            proposed = self.replace_exact_or_line_ending_variant(current, old_text, new_text)
            if proposed is not None:
                issue = _merotec_v5_validate(path, proposed)
                if issue:
                    _merotec_v5_report_rejection(self, path, issue, task_id)
                    return False
    except Exception as exc:
        self.log_agent(f"Pré-validação REPLACE indisponível: {exc}")
    return _merotec_v5_base_replace(
        self,
        raw_path,
        old_content,
        new_content,
        task_id=task_id,
        task_objective=task_objective,
    )


def _merotec_v5_snapshots(self, patch_text):
    snapshots = {}
    for raw_path in re.findall(
        r"^\*\*\* (?:Update File|Add File|Delete File):\s*(.+?)\s*$",
        str(patch_text or ""),
        re.MULTILINE,
    ):
        try:
            path = self.resolve_workspace_path(raw_path.strip())
            if path not in snapshots:
                snapshots[path] = (
                    path.exists(),
                    path.read_bytes() if path.exists() and path.is_file() else b"",
                )
        except Exception:
            pass
    return snapshots


def _merotec_v5_restore(self, snapshots):
    for path, state in snapshots.items():
        existed, payload = state
        try:
            if existed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            elif path.exists():
                path.unlink()
        except Exception as exc:
            self.log_agent(f"Falha ao restaurar PATCH inválido: {exc}")


def _merotec_v5_patch(self, patch_text, task_id=None, task_objective=None):
    snapshots = _merotec_v5_snapshots(self, patch_text)
    changed = _merotec_v5_base_patch(
        self,
        patch_text,
        task_id=task_id,
        task_objective=task_objective,
    )
    if not changed:
        return changed

    invalid_path = None
    invalid_issue = None
    for raw_path in changed:
        try:
            path = self.resolve_workspace_path(raw_path)
            if path.exists() and path.is_file():
                issue = _merotec_v5_validate(
                    path,
                    path.read_text(encoding="utf-8", errors="replace"),
                )
                if issue:
                    invalid_path = path
                    invalid_issue = issue
                    break
        except Exception:
            pass

    if invalid_issue:
        _merotec_v5_restore(self, snapshots)
        self.load_workspace_files()
        _merotec_v5_report_rejection(self, invalid_path, invalid_issue, task_id)
        return []
    return changed


def _merotec_v5_validation_command(self, objective):
    """Usa a barreira multilinguagem para qualquer workspace, não só Python."""
    from modules.language_guard import validation_command, detect_workspace_language
    workspace = Path(self.current_workspace).resolve()
    language = detect_workspace_language(workspace)
    self.log_agent(f"Validação automática selecionada para a stack: {language}.")
    return validation_command(workspace)


AgentActionsMixin._agent_write = _merotec_v5_write
AgentActionsMixin._agent_replace = _merotec_v5_replace
AgentActionsMixin._agent_apply_patch = _merotec_v5_patch
AgentActionsMixin.infer_default_validation_command = _merotec_v5_validation_command


# MEROTEC_WEB_CHAT_EDIT_PROTOCOL_V6
# The web chat protocol must use a single, deterministic grammar.  Older
# versions advertised "[PATCH: file]" but discarded the file name before the
# patch parser ran, producing the recurring "nenhum arquivo identificado"
# loop.  Keep compatibility with those replies, while guiding new replies to
# the safer WRITE/REPLACE envelopes.

import hashlib as _merotec_v6_hashlib

_merotec_v6_base_parse_actions = AgentActionsMixin.parse_and_execute_agent_actions
_merotec_v6_base_continue_after_mutation_failure = AgentActionsMixin.continue_after_mutation_failure


def _merotec_v6_safe_patch_path(value):
    """Return a plain relative path from a [PATCH: path] header, or empty."""
    raw = str(value or "").strip().strip("`").replace("\\", "/")
    raw = raw.splitlines()[0].strip() if raw else ""
    if not raw or raw in {".", "/"} or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        return ""
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        return ""
    return raw


def _merotec_v6_patch_body_is_hunk(body):
    return bool(re.search(r"(?m)^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", body or ""))


def _merotec_v6_normalize_patch_envelopes(response_text):
    """Attach a declared [PATCH: path] to a hunk without touching code bytes.

    Only marker lines are added/repaired.  Hunk lines, including their leading
    spaces and the one-character unified-diff prefix, remain verbatim.
    """
    text = str(response_text or "")
    block_pattern = re.compile(
        r"(\[PATCH\s*:\s*([^\]\r\n]+)\]\s*)(.*?)(\[/PATCH\])",
        re.IGNORECASE | re.DOTALL,
    )

    def normalize(match):
        prefix, declared_raw, body, closing = match.groups()
        declared = _merotec_v6_safe_patch_path(declared_raw)
        if not declared:
            return match.group(0)

        normalized = body.replace("\r\n", "\n").replace("\r", "\n")
        # A chat occasionally returns just a unified hunk after [PATCH:path].
        # The declared header is enough to build the safe OpenAI patch envelope.
        if "*** Begin Patch" not in normalized and _merotec_v6_patch_body_is_hunk(normalized):
            normalized = (
                "\n*** Begin Patch\n"
                f"*** Update File: {declared}\n"
                + normalized.strip("\n")
                + "\n*** End Patch\n"
            )
        elif "*** Begin Patch" in normalized:
            has_file_marker = bool(
                re.search(
                    r"(?m)^\*\*\* (?:Update File|Add File|Delete File):\s*\S+",
                    normalized,
                )
            )
            if not has_file_marker:
                normalized = re.sub(
                    r"(?m)^(\*\*\* Begin Patch[^\n]*\n)",
                    lambda marker: marker.group(1) + f"*** Update File: {declared}\n",
                    normalized,
                    count=1,
                )
            # Gemini sometimes literalizes the documentation placeholder
            # "Update File /".  A real header path is authoritative.
            normalized = re.sub(
                r"(?m)^(\*\*\* (?:Update File|Add File|Delete File):)\s*/\s*$",
                lambda marker: marker.group(1) + f" {declared}",
                normalized,
            )
            # If the streamed answer was complete enough to contain a hunk but
            # omitted the final marker, finish the envelope.  It still goes
            # through exact-context matching and language validation.
            if "*** End Patch" not in normalized and _merotec_v6_patch_body_is_hunk(normalized):
                normalized = normalized.rstrip("\n") + "\n*** End Patch\n"

        return prefix + normalized + closing

    return block_pattern.sub(normalize, text)


def _merotec_v6_parse_actions(
    self,
    response_text,
    task_objective=None,
    action_depth=0,
    task_id=None,
    direct_action_happened=False,
):
    # The normalizer is deliberately limited to PATCH metadata.  It never calls
    # strip() on a source line, so Python indentation and diff prefixes survive.
    normalized = _merotec_v6_normalize_patch_envelopes(response_text)
    return _merotec_v6_base_parse_actions(
        self,
        normalized,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
        direct_action_happened=direct_action_happened,
    )


def _merotec_v6_continue_after_mutation_failure(
    self,
    response_text,
    target_paths,
    task_objective=None,
    action_depth=0,
    task_id=None,
):
    """Keep the mission alive, but break repeated malformed PATCH cycles."""
    raw = str(response_text or "")
    upper = raw.upper()
    if "[PATCH" in upper:
        metrics = self.get_ai_task_metrics(task_id)
        fingerprints = metrics.setdefault("web_chat_patch_failures", {})
        fingerprint = _merotec_v6_hashlib.sha256(
            re.sub(r"\s+", " ", raw).strip().encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        repeats = int(fingerprints.get(fingerprint, 0)) + 1
        fingerprints[fingerprint] = repeats

        targets = [str(item).strip() for item in (target_paths or []) if str(item).strip()]
        declared = ""
        header = re.search(r"\[PATCH\s*:\s*([^\]\r\n]+)\]", raw, re.IGNORECASE)
        if header:
            declared = _merotec_v6_safe_patch_path(header.group(1))
        if declared and declared not in targets:
            targets.append(declared)

        repair = (
            "\n\nPROTOCOLO DE RECUPERAÇÃO DO CHAT WEB:\n"
            "O formato PATCH/unified diff não é confiável nesta conversa e não deve ser repetido. "
            "Não use [PATCH], `*** Begin Patch`, `@@` nem diff.\n"
            "Escolha somente uma das formas abaixo, sem texto fora dos marcadores:\n"
            "1) [READ: caminho] para obter novamente o trecho atual; ou\n"
            "2) [REPLACE: caminho]\\n[OLD]\\n```linguagem\\nTRECHO EXATO ATUAL\\n```\\n[/OLD]\\n"
            "[NEW]\\n```linguagem\\nTRECHO NOVO COM INDENTAÇÃO PRESERVADA\\n```\\n[/NEW]\\n[/REPLACE]; ou\n"
            "3) [WRITE: caminho]\\n```linguagem\\nARQUIVO COMPLETO VÁLIDO\\n```\\n[/WRITE].\n"
            "Para Python use somente quatro espaços por nível e não use tab. "
            "Não declare conclusão antes de uma edição aplicada e validação real."
        )
        if repeats >= 2:
            repair += (
                "\nA mesma tentativa PATCH já falhou mais de uma vez. A próxima resposta deve ser [READ: caminho] "
                "ou um bloco [WRITE]/[REPLACE] no formato acima; PATCH será ignorado para evitar loop."
            )
        raw += repair
        target_paths = targets

    return _merotec_v6_base_continue_after_mutation_failure(
        self,
        raw,
        target_paths,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
    )


AgentActionsMixin.parse_and_execute_agent_actions = _merotec_v6_parse_actions
AgentActionsMixin.continue_after_mutation_failure = _merotec_v6_continue_after_mutation_failure


# MEROTEC_WEB_CHAT_PATCH_COMPATIBILITY_V6
# When a provider renders a unified diff outside a <pre>, a context-only blank
# line can arrive as an empty string instead of the normal single-space diff
# line.  Treat it as a blank context line; never trim source indentation.

_merotec_v6_base_apply_hunks = AgentActionsMixin._apply_openai_style_patch_hunks


def _merotec_v6_apply_hunks(self, source, lines, raw_path):
    source_lines = source.splitlines()
    has_final_newline = source.endswith("\n")
    hunks = []
    current = None
    for line in lines:
        if line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            current = []
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        hunks.append(current)
    if not hunks:
        raise ValueError(f"PATCH de {raw_path} não contém @@ com trecho de troca.")

    for hunk in hunks:
        old_lines = []
        new_lines = []
        for line in hunk:
            if line.startswith(" "):
                # The first character is the unified-diff context marker.  All
                # remaining spaces are actual source indentation and are kept.
                old_lines.append(line[1:])
                new_lines.append(line[1:])
            elif line.startswith("-"):
                old_lines.append(line[1:])
            elif line.startswith("+"):
                new_lines.append(line[1:])
            elif line == "":
                # Some web-chat DOMs drop the single context-marker space on a
                # blank line.  A blank is unambiguous source context here.
                old_lines.append("")
                new_lines.append("")
            elif line.startswith(r"\ No newline at end of file"):
                continue
            else:
                # Do not silently reinterpret non-diff text as code.  It would
                # risk altering indentation or applying a prose explanation.
                raise ValueError(
                    f"PATCH de {raw_path} contém linha sem prefixo diff: {line[:120]!r}. "
                    "Use WRITE/REPLACE em bloco Markdown; PATCH não é seguro nessa resposta."
                )

        if not old_lines:
            raise ValueError(f"PATCH de {raw_path} não possui contexto para inserir com segurança.")
        found_at = -1
        for index in range(0, len(source_lines) - len(old_lines) + 1):
            if source_lines[index:index + len(old_lines)] == old_lines:
                found_at = index
                break
        if found_at < 0:
            preview = "\n".join(old_lines[:5])
            raise ValueError(
                f"Contexto do PATCH não encontrado em {raw_path}: {preview[:220]}. "
                "Use READ e depois REPLACE com [OLD] exato."
            )
        source_lines[found_at:found_at + len(old_lines)] = new_lines

    result = "\n".join(source_lines)
    return result + "\n" if has_final_newline else result


AgentActionsMixin._apply_openai_style_patch_hunks = _merotec_v6_apply_hunks

# MEROTEC_EXECUTION_STATE_MACHINE_V7
# A continuous development loop must be driven by verified progress.  Earlier
# versions treated any successful shell exit code as progress, including
# `[EXECUTE: echo "Missão concluída"]`.  That let a chat create a self-confirming
# loop: print a conclusion -> return code 0 -> ask the chat again -> print a
# conclusion.  V7 blocks output-only commands, records workspace state, accepts
# an explicit final state only after evidence, and pauses repeated failed edits
# that do not change the workspace.

import hashlib as _merotec_v7_hashlib

_merotec_v7_base_execute = AgentActionsMixin._agent_execute
_merotec_v7_base_parse_actions = AgentActionsMixin.parse_and_execute_agent_actions
_merotec_v7_base_continue_mutation_failure = AgentActionsMixin.continue_after_mutation_failure
_merotec_v7_base_action_names = AgentActionsMixin.extract_agent_action_names
_merotec_v7_base_advance_delivery = AgentActionsMixin.advance_autonomous_delivery_after_validation


def _merotec_v7_workspace_signature(instance):
    """Return a lightweight deterministic signature of the current workspace.

    It is intentionally based on relative path, size and mtime.  It is used
    only to detect repeated agent actions without any intervening filesystem
    progress; it is not a security hash.
    """
    try:
        root = Path(instance.current_workspace).resolve()
    except Exception:
        return "workspace-indisponivel"
    digest = _merotec_v7_hashlib.sha256()
    count = 0
    ignored_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    try:
        for path in sorted(root.rglob("*")):
            try:
                if not path.is_file() or any(part in ignored_dirs for part in path.parts):
                    continue
                relative = path.relative_to(root).as_posix()
                # Backups and databases change as a side effect of valid edits;
                # they must not reset the no-progress guard by themselves.
                if relative.endswith((".bak", ".pyc")) or relative in {"tasks.db", "tasks.db-journal"}:
                    continue
                stat = path.stat()
                digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8", "replace"))
                count += 1
                if count >= 2500:
                    break
            except OSError:
                continue
    except OSError:
        return "workspace-indisponivel"
    return digest.hexdigest()[:24]


def _merotec_v7_normalize_shell(command):
    value = str(command or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def _merotec_v7_is_output_only_command(command):
    """Recognize commands that only narrate a conclusion and do no validation.

    The IDE never needs an AI-generated `echo`, `Write-Host`, `printf`, `true`
    or `exit 0` to develop a project.  User-entered Terminal Local commands do
    not pass through `_agent_execute`, so this affects only model actions.
    """
    value = _merotec_v7_normalize_shell(command)
    if not value:
        return True
    # Redirects, pipes and chained commands could have real effects.  Do not
    # classify those automatically; dedicated mutation guards handle them.
    if any(token in value for token in (">", "|", "&&", ";")):
        return False
    value = re.sub(r"^(?:cmd(?:\.exe)?\s+/c\s+)", "", value)
    value = re.sub(r"^(?:powershell(?:\.exe)?\s+(?:-noprofile\s+)?(?:-command\s+)?)", "", value)
    value = value.strip().strip('"\'')
    if re.match(r"^(?:echo(?:\.|\s)|write-host\b|write-output\b|printf\b)", value):
        return True
    if value in {"true", "ver", "exit 0", "exit /b 0", "exit /b"}:
        return True
    return False


def _merotec_v7_metric(instance, task_id):
    metrics = instance.get_ai_task_metrics(task_id)
    metrics.setdefault("v7_workspace_signature", _merotec_v7_workspace_signature(instance))
    return metrics


def _merotec_v7_decrement_rejected_execute(metrics):
    # The legacy parser marks EXECUTE before calling _agent_execute.  Undo that
    # accounting for a command we refuse before it reaches the shell, so a
    # rejected echo cannot become proof of a real task action.
    for key in ("real_actions", "execute_actions"):
        try:
            metrics[key] = max(0, int(metrics.get(key, 0) or 0) - 1)
        except (TypeError, ValueError):
            metrics[key] = 0


def _merotec_v7_resume_after_rejected_execute(self, command, reason, task_objective, action_depth, task_id):
    metrics = _merotec_v7_metric(self, task_id)
    signature = _merotec_v7_workspace_signature(self)
    if metrics.get("v7_workspace_signature") != signature:
        metrics["v7_workspace_signature"] = signature
        metrics["v7_rejected_execute_count"] = 0
    metrics["v7_rejected_execute_count"] = int(metrics.get("v7_rejected_execute_count", 0) or 0) + 1
    count = metrics["v7_rejected_execute_count"]

    try:
        rejected_limit = max(1, min(8, int(getattr(self, "settings", {}).get("web_chat_nonproductive_execute_limit", 2))))
    except (TypeError, ValueError):
        rejected_limit = 2
    if count > rejected_limit:
        self.add_chat_message(
            "Erro",
            "A missão foi pausada para evitar ciclo sem progresso: o Chat Web repetiu comandos de terminal "
            "que não alteram nem validam o projeto. Nenhum arquivo foi modificado.",
        )
        self.log_agent("Ciclo bloqueado por EXECUTE não produtivo repetido.")
        self.set_status("Pausado: Chat Web repetiu comando sem progresso.", "warning")
        return

    self.add_chat_message(
        "Sistema",
        "A IDE recusou um EXECUTE que apenas imprime/declara resultado. A mesma missão continuará uma única vez "
        "com uma ação de desenvolvimento real.",
    )
    recovery = (
        f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or ''}\n\n"
        "RECUPERAÇÃO DO LOOP:\n"
        f"A IDE recusou o comando `{command}` porque {reason}.\n"
        "Não use echo, Write-Host, printf, true, exit 0 ou comandos que apenas dizem que a missão terminou. "
        "Escolha agora UMA ação que gere progresso verificável: [READ: arquivo], [SEARCH_TEXT: padrão | arquivo], "
        "[WRITE: arquivo] com código em cerca Markdown, [REPLACE: arquivo] com OLD/NEW exatos, "
        "[EXECUTE: teste real] ou [HUMAN_TEST: auto]. "
        "Use [FINAL: resumo] somente depois de uma alteração/teste real já aprovado pela IDE."
    )

    def resume():
        if self.is_task_cancelled(task_id):
            return
        self._run_ai_task(
            "Retome a missão com uma única ação real; não use comando de eco ou auto-conclusão.",
            extra_context=recovery,
            task_objective=task_objective or self.active_ai_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

    timer = threading.Timer(0.35, resume)
    timer.daemon = True
    timer.start()


def _merotec_v7_execute(self, command, task_objective=None, action_depth=0, task_id=None):
    normalized = _merotec_v7_normalize_shell(command)
    metrics = _merotec_v7_metric(self, task_id)
    if _merotec_v7_is_output_only_command(command):
        _merotec_v7_decrement_rejected_execute(metrics)
        self.add_chat_message(
            "Erro",
            "EXECUTE recusado: o comando só imprime ou declara uma conclusão; ele não testa nem modifica o projeto.",
        )
        self.log_agent(f"EXECUTE não produtivo recusado: {command}")
        _merotec_v7_resume_after_rejected_execute(
            self, command, "é apenas saída de texto/auto-conclusão", task_objective, action_depth, task_id
        )
        return

    signature = _merotec_v7_workspace_signature(self)
    command_key = _merotec_v7_hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:20]
    executed = metrics.setdefault("v7_executed_commands", {})
    if executed.get(command_key) == signature:
        _merotec_v7_decrement_rejected_execute(metrics)
        self.add_chat_message(
            "Erro",
            "EXECUTE recusado: o mesmo comando já foi executado neste mesmo estado do workspace. "
            "Faça uma leitura, alteração ou use outro teste antes de repetir.",
        )
        self.log_agent(f"EXECUTE repetido sem mudança recusado: {command}")
        _merotec_v7_resume_after_rejected_execute(
            self, command, "repete o mesmo comando sem alteração de arquivos", task_objective, action_depth, task_id
        )
        return

    executed[command_key] = signature
    metrics["v7_workspace_signature"] = signature
    return _merotec_v7_base_execute(
        self,
        command,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
    )


def _merotec_v7_final_summary(response_text):
    text = str(response_text or "").strip()
    match = re.fullmatch(r"\[FINAL\s*:\s*(.+?)\]", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _merotec_v7_has_final_evidence(self, task_objective, task_id):
    objective = str(task_objective or self.active_ai_objective or "")
    if not self.objective_requires_concrete_change(objective):
        return True
    metrics = self.get_ai_task_metrics(task_id)
    changed = sum(int(metrics.get(key, 0) or 0) for key in ("write_actions", "replace_actions", "direct_actions"))
    verified = sum(int(metrics.get(key, 0) or 0) for key in (
        "execute_actions", "human_test_actions", "screenshot_actions", "auto_validation_actions"
    ))
    return changed > 0 and verified > 0


def _merotec_v7_parse_actions(
    self,
    response_text,
    task_objective=None,
    action_depth=0,
    task_id=None,
    direct_action_happened=False,
):
    final = _merotec_v7_final_summary(response_text)
    if final:
        if _merotec_v7_has_final_evidence(self, task_objective, task_id):
            metrics = self.get_ai_task_metrics(task_id)
            metrics["mission_finalized"] = True
            metrics["mission_final_summary"] = final
            self.add_chat_message("Merotec IA", final)
            self.log_agent("Missão encerrada por [FINAL] após evidência verificável.")
            self.set_status("Missão concluída com evidências verificáveis.", "ready")
            return
        self.add_chat_message(
            "Erro",
            "FINAL recusado: ainda não há uma alteração e validação reais para concluir esta missão.",
        )
        _merotec_v7_resume_after_rejected_execute(
            self,
            "[FINAL]",
            "tentou concluir sem alteração e validação verificáveis",
            task_objective,
            action_depth,
            task_id,
        )
        return
    return _merotec_v7_base_parse_actions(
        self,
        response_text,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
        direct_action_happened=direct_action_happened,
    )


def _merotec_v7_action_names(self, response_text):
    names = _merotec_v7_base_action_names(self, response_text)
    if _merotec_v7_final_summary(response_text):
        names.add("FINAL")
    return names


def _merotec_v7_advance_delivery(self, command, task_objective=None, action_depth=0, task_id=None):
    advanced = _merotec_v7_base_advance_delivery(
        self,
        command,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
    )
    if advanced:
        metrics = self.get_ai_task_metrics(task_id)
        metrics["v7_last_validation_signature"] = _merotec_v7_workspace_signature(self)
        metrics["v7_rejected_execute_count"] = 0
    return advanced


def _merotec_v7_continue_mutation_failure(
    self,
    response_text,
    target_paths,
    task_objective=None,
    action_depth=0,
    task_id=None,
):
    """Permit recovery only while the workspace or error is making progress.

    Continuous means the IDE keeps trying valid next steps, not that it sends
    malformed edits to a provider forever.  A repeated failed mutation against
    an unchanged workspace is paused after three attempts with the file intact.
    """
    metrics = _merotec_v7_metric(self, task_id)
    signature = _merotec_v7_workspace_signature(self)
    previous_signature = metrics.get("v7_mutation_failure_signature")
    if previous_signature != signature:
        metrics["v7_mutation_failure_signature"] = signature
        metrics["v7_mutation_failures_without_progress"] = 0
        metrics["v7_mutation_failure_fingerprints"] = {}

    normalized = re.sub(r"\s+", " ", str(response_text or "")).strip()
    fingerprint = _merotec_v7_hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:20]
    fingerprints = metrics.setdefault("v7_mutation_failure_fingerprints", {})
    fingerprints[fingerprint] = int(fingerprints.get(fingerprint, 0) or 0) + 1
    metrics["v7_mutation_failures_without_progress"] = int(
        metrics.get("v7_mutation_failures_without_progress", 0) or 0
    ) + 1
    repeats = fingerprints[fingerprint]
    total = metrics["v7_mutation_failures_without_progress"]

    try:
        no_progress_limit = max(2, min(12, int(getattr(self, "settings", {}).get("web_chat_no_progress_failure_limit", 3))))
    except (TypeError, ValueError):
        no_progress_limit = 3
    if repeats >= 2 or total >= no_progress_limit:
        targets = ", ".join(dict.fromkeys(str(item) for item in (target_paths or []) if str(item).strip())) or "arquivo alvo"
        self.add_chat_message(
            "Erro",
            "Ciclo pausado sem perder arquivos: a mesma edição falhou repetidamente no mesmo estado do workspace. "
            f"Arquivos preservados: {targets}. A próxima tentativa deve começar por [READ: arquivo] e usar "
            "[WRITE]/[REPLACE] com bloco Markdown; PATCH/diff não será aceito nesta conversa.",
        )
        self.log_agent("Loop de mutação bloqueado por falta de progresso verificável.")
        self.set_status("Pausado: edição repetida sem progresso.", "warning")
        return False

    return _merotec_v7_base_continue_mutation_failure(
        self,
        response_text,
        target_paths,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
    )


AgentActionsMixin._agent_execute = _merotec_v7_execute
AgentActionsMixin.parse_and_execute_agent_actions = _merotec_v7_parse_actions
AgentActionsMixin.extract_agent_action_names = _merotec_v7_action_names
AgentActionsMixin.advance_autonomous_delivery_after_validation = _merotec_v7_advance_delivery
AgentActionsMixin.continue_after_mutation_failure = _merotec_v7_continue_mutation_failure


# MEROTEC_FILE_APPLICATION_RECOVERY_V8
# O Chat Web pode colocar explicações antes/depois do bloco de código, omitir a
# cerca final Markdown ou devolver uma versão incompleta.  A V5 validava a
# resposta crua antes de a rotina de escrita extrair o código, fazendo uma
# edição válida ser recusada por `````python```` na linha 1.  Esta camada
# normaliza o payload primeiro e, quando o código realmente é inválido, volta
# ao mesmo chat com o arquivo atual e o diagnóstico — sem tentar salvar nada
# quebrado e sem pausar a missão na terceira tentativa.

_merotec_v8_base_write = AgentActionsMixin._agent_write
_merotec_v8_base_continue_mutation_failure = AgentActionsMixin.continue_after_mutation_failure


def _merotec_v8_clean_write_payload(self, content):
    """Extrai somente o fonte de um WRITE, preservando a indentação.

    Aceita explicação fora da cerca, cerca sem fechamento e a forma que alguns
    chats copiam como ``main.py`` antes do bloco.  O resultado continua sendo
    validado pela barreira multilinguagem antes de qualquer escrita.
    """
    raw = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    # Remova apenas marcadores de transporte; nunca use strip() por linha,
    # pois a indentação de Python é parte do código.
    raw = re.sub(r"(?im)^\s*\[/WRITE\]\s*$", "", raw)
    raw = re.sub(r"(?im)^\s*\[WRITE\s*:\s*[^\]\r\n]+\]\s*$", "", raw)

    fence = re.search(r"```[^\n`]*\n(.*?)(?:\n```|\Z)", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        # Quando só a abertura da cerca chegou pelo WebView, descarte a linha
        # de linguagem e mantenha todo o restante exatamente como foi emitido.
        lines = raw.split("\n")
        if lines and lines[0].lstrip().startswith("```"):
            candidate = "\n".join(lines[1:])
        else:
            candidate = raw

    candidate = candidate.strip("\n")
    return candidate + ("\n" if candidate else "")


def _merotec_v8_write(self, raw_path, content, task_id=None, task_objective=None):
    cleaned = _merotec_v8_clean_write_payload(self, content)
    # Passe o mesmo conteúdo normalizado à V5 e à implementação original.
    # Assim a pré-validação e a gravação usam bytes equivalentes.
    result = _merotec_v8_base_write(
        self,
        raw_path,
        cleaned,
        task_id=task_id,
        task_objective=task_objective,
    )
    if result:
        metrics = self.get_ai_task_metrics(task_id)
        metrics["v8_invalid_write_recoveries"] = 0
        metrics.pop("v8_last_invalid_write_key", None)
    return result


def _merotec_v8_compact_invalid_write_context(self, path, relative, issue, objective):
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Não foi possível reler `{relative}`: {exc}"

    error_kind = str((issue or {}).get("kind") or "ValidationError")
    error_message = str((issue or {}).get("message") or "A validação recusou o conteúdo.")
    line = int((issue or {}).get("line") or 0)
    location = f" na linha {line}" if line else ""
    excerpt = str((issue or {}).get("excerpt") or "").strip()
    if len(source) <= 24000:
        source_block = f"ARQUIVO ATUAL VÁLIDO — {relative}:\n```\n{source}\n```"
    else:
        source_block = (
            f"O arquivo `{relative}` é grande. Antes de editar, responda somente "
            f"[READ: {relative}] para a IDE entregar o trecho atual correto."
        )

    diagnosis = (
        f"MISSÃO ORIGINAL:\n{objective or self.active_ai_objective or ''}\n\n"
        "RECUPERAÇÃO DE ESCRITA DA IDE:\n"
        f"A última proposta para `{relative}` foi recusada antes de salvar por {error_kind}{location}: "
        f"{error_message}\n"
    )
    if excerpt:
        diagnosis += f"Trecho do diagnóstico:\n```\n{excerpt}\n```\n"
    diagnosis += (
        "O arquivo no disco NÃO foi alterado. Ignore totalmente a proposta inválida anterior. "
        "Use o arquivo atual abaixo como fonte de verdade. Responda com UMA ação aplicável: "
        f"prefira [REPLACE: {relative}] com [OLD] idêntico ao conteúdo atual e [NEW] válido; "
        "use [WRITE] somente se for intencionalmente reescrever o arquivo completo. "
        "Não envie código solto, não use PATCH/diff e não explique antes da tag.\n\n"
        + source_block
    )
    return diagnosis


def _merotec_v8_continue_mutation_failure(
    self,
    response_text,
    target_paths,
    task_objective=None,
    action_depth=0,
    task_id=None,
):
    """Recupera falhas de sintaxe com contexto correto, sem bloquear o agente.

    A V7 pausava após poucas tentativas no mesmo estado. Isso é adequado para
    comandos vazios, mas não para fonte inválido: a ação útil seguinte é reler
    o arquivo preservado e fazer uma substituição pequena. Esta rotina entrega
    esse contexto diretamente ao mesmo ciclo automático.
    """
    if self.is_task_cancelled(task_id) or not self.should_continue_development_loop(action_depth, task_id):
        return False

    metrics = self.get_ai_task_metrics(task_id)
    issue = metrics.get("code_pipeline_last_rejection")
    candidate_path = ""
    for raw in target_paths or ():
        raw = str(raw or "").strip()
        if raw:
            candidate_path = raw
            break
    if not candidate_path and isinstance(issue, dict):
        candidate_path = str(issue.get("path") or "").strip()

    if isinstance(issue, dict) and candidate_path:
        try:
            path = self.resolve_workspace_path(candidate_path)
            if path.exists() and path.is_file():
                relative = path.relative_to(Path(self.current_workspace).resolve()).as_posix()
                signature = _merotec_v7_workspace_signature(self)
                response_key = _merotec_v7_hashlib.sha256(
                    (signature + "\0" + re.sub(r"\s+", " ", str(response_text or "")).strip()).encode(
                        "utf-8", "replace"
                    )
                ).hexdigest()[:20]
                previous = metrics.get("v8_last_invalid_write_key")
                if previous != response_key:
                    metrics["v8_invalid_write_recoveries"] = int(metrics.get("v8_invalid_write_recoveries", 0) or 0) + 1
                    metrics["v8_last_invalid_write_key"] = response_key
                recovery_count = int(metrics.get("v8_invalid_write_recoveries", 0) or 0)
                metrics["autonomous_repair_cycles"] = int(metrics.get("autonomous_repair_cycles", 0) or 0) + 1

                self.add_chat_message(
                    "Sistema",
                    "A alteração não foi salva porque o código proposto é inválido. "
                    "A IDE preservou o arquivo e continuará a mesma missão com o conteúdo atual e o diagnóstico "
                    f"({recovery_count}/contínuo).",
                )
                self.log_agent(
                    f"Recuperação V8 de escrita inválida: {relative}; tentativa {recovery_count}/contínuo."
                )
                self.set_ai_activity("IA corrigindo escrita inválida")
                context = _merotec_v8_compact_invalid_write_context(
                    self,
                    path,
                    relative,
                    issue,
                    task_objective or self.active_ai_objective or "",
                )
                self._run_ai_task(
                    "Corrija a edição rejeitada usando o arquivo atual e emita uma única ação válida da IDE.",
                    extra_context=context,
                    task_objective=task_objective or self.active_ai_objective,
                    action_depth=action_depth + 1,
                    task_id=task_id,
                )
                return True
        except Exception as exc:
            self.log_agent(f"Recuperação V8 não conseguiu preparar o arquivo rejeitado: {exc}")

    return _merotec_v8_base_continue_mutation_failure(
        self,
        response_text,
        target_paths,
        task_objective=task_objective,
        action_depth=action_depth,
        task_id=task_id,
    )


AgentActionsMixin._agent_write = _merotec_v8_write
AgentActionsMixin.continue_after_mutation_failure = _merotec_v8_continue_mutation_failure

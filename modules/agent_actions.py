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
        self.set_status("Codex ocupado. Clique Reenviar para tentar de novo.", "busy")

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
        if not response_text:
            return
        if action_names is None:
            action_names = (
                "READ",
                "SEARCH_TEXT",
                "WEB_SEARCH",
                "SCAN_TEXT",
                "FIX_MOJIBAKE",
                "UNDO",
                "EXECUTE",
                "EXECUTE_ADMIN",
                "OPEN_URL",
                "SCREENSHOT",
                "HUMAN_TEST",
            )
        action_names = tuple(str(name).upper() for name in action_names)
        action_pattern = "|".join(re.escape(name) for name in action_names)
        tag_pattern = re.compile(
            rf"\[({action_pattern})[ \t]*:[ \t]*([^\]\r\n]+?)[ \t]*\]",
            re.IGNORECASE,
        )
        in_fenced_block = False
        for line in str(response_text).splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fenced_block = not in_fenced_block
                continue
            if in_fenced_block:
                continue
            matches = list(tag_pattern.finditer(line))
            if not matches:
                continue

            without_tags = tag_pattern.sub("", line).strip()
            if without_tags:
                continue

            for match in matches:
                yield match.group(1).upper(), match.group(2).strip()

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

    def extract_agent_action_names(self, response_text):
        names = {action for action, _value in self.iter_agent_action_lines(response_text)}
        if re.search(r"\[WRITE:\s*.+?\].*?\[/WRITE\]", response_text or "", re.DOTALL | re.IGNORECASE):
            names.add("WRITE")
        if re.search(r"\[REPLACE:\s*.+?\].*?\[/REPLACE\]", response_text or "", re.DOTALL | re.IGNORECASE):
            names.add("REPLACE")
        return names

    def parse_and_execute_agent_actions(self, response_text, task_objective=None, action_depth=0, task_id=None, direct_action_happened=False):
        if not response_text:
            return
        if self.is_task_cancelled(task_id):
            self.log_agent("Acao da IA ignorada porque a tarefa foi cancelada.")
            return

        write_blocks = re.findall(r"\[WRITE:\s*(.+?)\](.*?)\[/WRITE\]", response_text, re.DOTALL | re.IGNORECASE)
        has_action = bool(write_blocks)
        if "[WRITE:" in response_text.upper() and not write_blocks:
            self.add_chat_message(
                "Erro",
                "A IA enviou um WRITE incompleto. Ela precisa mandar [WRITE: arquivo] conteudo [/WRITE].",
            )
        for raw_path, content in write_blocks:
            self.mark_ai_active_action("write", task_id=task_id)
            self._agent_write(raw_path, content, task_id=task_id, task_objective=task_objective)

        replace_blocks = re.findall(r"\[REPLACE:\s*(.+?)\](.*?)\[/REPLACE\]", response_text, re.DOTALL | re.IGNORECASE)
        has_action = has_action or bool(replace_blocks)
        if "[REPLACE:" in response_text.upper() and not replace_blocks:
            self.add_chat_message(
                "Erro",
                "A IA enviou um REPLACE incompleto. Ela precisa mandar [REPLACE: arquivo] [OLD]...[/OLD] [NEW]...[/NEW] [/REPLACE].",
            )
        for raw_path, block in replace_blocks:
            old_match = re.search(r"\[OLD\](.*?)\[/OLD\]", block, re.DOTALL | re.IGNORECASE)
            new_match = re.search(r"\[NEW\](.*?)\[/NEW\]", block, re.DOTALL | re.IGNORECASE)
            if not old_match or not new_match:
                self.add_chat_message(
                    "Erro",
                    "REPLACE precisa conter [OLD] trecho atual [/OLD] e [NEW] trecho novo [/NEW].",
                )
                continue
            self.mark_ai_active_action("replace", task_id=task_id)
            self._agent_replace(
                raw_path,
                old_match.group(1),
                new_match.group(1),
                task_id=task_id,
                task_objective=task_objective,
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
            if action_depth >= 4:
                self.add_chat_message(
                    "Erro",
                    "A IA continuou respondendo com intencao sem executar. A IDE interrompeu o ciclo; envie o pedido novamente de forma direta.",
                )
                self.set_status("Sem acao real.", "warning")
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
                    output = "".join(output_lines)
                    self.append_to_term(f"\n[teste visual falhou com codigo {process.returncode}]\n")
                    diagnostic = self.build_command_failure_diagnostic(command_display, output, process.returncode)
                    context = (
                        f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or 'Testar visualmente'}\n\n"
                        f"Teste visual tentou executar: {command_display}\n"
                        f"Codigo de saida: {process.returncode}\n"
                        f"{diagnostic}\n"
                        f"Saida:\n```\n{output[-7000:]}\n```\n\n"
                        "Corrija a causa antes de tentar o mesmo teste de novo."
                    )
                    self.set_ai_busy(False)
                    self._run_ai_task(
                        "O teste visual falhou antes de abrir a tela. Analise e corrija.",
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
                    "Analise o print como um usuario humano: tela vazia, layout quebrado, botao fora do lugar, erro visual, "
                    "fluxo confuso, jogo injogavel, controle invertido, asset faltando ou comportamento incoerente. "
                    "Se houver problema, corrija com [READ], [REPLACE] ou [WRITE] e depois rode novo [HUMAN_TEST: auto]. "
                    "Se estiver bom, entregue uma conclusao objetiva com o que foi validado."
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
            candidates = [
                Path(candidate).resolve()
                for candidate in self.find_runnable_workspaces()
                if str(Path(candidate).resolve()).startswith(str(workspace))
            ]
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
            "- Se a missao pede executar/testar, responda com uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest].\n"
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
            webbrowser.open(url, new=1)
            self.log_agent(f"URL aberta pela IA: {url}")
            self.add_chat_message("Merotec AI", f"Abri a pagina para validacao visual: {url}")
        except Exception as exc:
            self.add_chat_message("Erro", f"Nao consegui abrir a URL: {exc}")

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
                r"\[(WRITE|REPLACE|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:",
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
                "- Se precisa validar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest].\n\n"
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
                "- Se a missao pede executar/testar, responda com uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest].\n"
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
        candidates = [self.apply_mojibake_map(line)]
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

            cleaned = self._strip_markdown_code(content)
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
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao escrever arquivo: {exc}")

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
                    f"REPLACE nao encontrou o trecho exato em {rel}. A IDE precisa reler o intervalo exato antes de tentar trocar.",
                )
                self.log_agent(f"REPLACE falhou porque OLD nao foi encontrado: {rel}")
                return

            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            self.record_file_change_snapshot(path, "REPLACE", "Trecho substituido pela IA")
            path.write_text(updated, encoding="utf-8")
            rel = path.relative_to(self.current_workspace).as_posix()
            self.log_agent(f"Trecho substituido pela IA: {rel}")
            self.add_chat_message("Merotec AI", f"Substitui o trecho em `{rel}`.")
            self.load_workspace_files()
        except Exception as exc:
            self.add_chat_message("Erro", f"Falha ao substituir trecho: {exc}")

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
                    should_consolidate = info["full"] or len(ranges) > 1 or total_lines > 420
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
            "- Se a tarefa for validar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest], ou [HUMAN_TEST].\n"
            "- Se realmente faltar informacao essencial, leia outro ponto especifico e siga trabalhando."
        )

    def parse_agent_read_request(self, raw_path):
        text = raw_path.strip().strip("\"'")
        line_range = None
        patterns = [
            r"^(?P<path>.+?)\s*\|\s*linhas?\s+(?P<start>\d+)\s*[-:]\s*(?P<end>\d+)\s*$",
            r"^(?P<path>.+?)\s*\|\s*lines?\s+(?P<start>\d+)\s*[-:]\s*(?P<end>\d+)\s*$",
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
                if process.returncode != 0:
                    diagnostic = self.build_command_failure_diagnostic(command, output, process.returncode)
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
                    f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or command}\n\n"
                    f"Comando executado: {command}\n"
                    f"Codigo de saida: {process.returncode}\n"
                    f"{diagnostic}\n"
                    f"Saida:\n```\n{(output or '')[:6000]}\n```\n\n"
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

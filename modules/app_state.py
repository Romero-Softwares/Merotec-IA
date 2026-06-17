import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from modules.app_constants import (
    APP_NAME,
    DEFAULT_APP_SETTINGS,
    DEFAULT_WORKSPACE,
    PROJECT_ROOT,
)


class AppStateMixin:
    def _load_settings(self):
        settings = DEFAULT_APP_SETTINGS.copy()
        if self.settings_file.exists():
            try:
                with self.settings_file.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass

        if not settings.get("recent_projects") and self.history_file.exists():
            try:
                with self.history_file.open("r", encoding="utf-8") as file:
                    history = json.load(file)
                if isinstance(history, list):
                    settings["recent_projects"] = [path for path in history if Path(path).exists()]
                    if not settings.get("last_workspace") and settings["recent_projects"]:
                        settings["last_workspace"] = settings["recent_projects"][0]
            except (OSError, json.JSONDecodeError):
                pass

        return settings

    def _save_settings(self):
        try:
            with self.settings_file.open("w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=2, ensure_ascii=False)
        except OSError as exc:
            self.set_status(f"Nao consegui salvar preferencias: {exc}", "error")

    def _load_change_history(self):
        if not self.change_history_file.exists():
            return []
        try:
            with self.change_history_file.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            return loaded if isinstance(loaded, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_change_history(self):
        try:
            with self.change_history_file.open("w", encoding="utf-8") as file:
                json.dump(self.change_history[-240:], file, indent=2, ensure_ascii=False)
        except OSError as exc:
            self.log_agent(f"Nao consegui salvar historico de alteracoes: {exc}")

    def record_file_change_snapshot(self, path, action, summary=""):
        workspace = Path(self.current_workspace).resolve()
        path = Path(path).resolve()
        rel = path.relative_to(workspace).as_posix()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = workspace / ".merotec_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        safe_rel = re.sub(r"[^A-Za-z0-9_.-]+", "__", rel)
        backup_path = backup_dir / f"{timestamp}__{safe_rel}.bak"
        existed = path.exists()
        if existed:
            shutil.copy2(path, backup_path)

        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(workspace),
            "path": str(path),
            "rel": rel,
            "action": action,
            "summary": summary,
            "objective": self.active_ai_objective or "",
            "backup": str(backup_path) if existed else "",
            "existed": existed,
            "undone": False,
        }
        self.change_history.append(record)
        self.change_history = self.change_history[-240:]
        self._save_change_history()
        self.log_agent(f"Snapshot registrado: {action} em {rel}")
        return record

    def recent_change_records(self, limit=8, include_undone=False):
        workspace = str(Path(self.current_workspace).resolve())
        records = [
            item for item in self.change_history
            if item.get("workspace") == workspace and (include_undone or not item.get("undone"))
        ]
        return records[-limit:]

    def format_recent_changes_for_agent(self, limit=8):
        records = self.recent_change_records(limit=limit)
        if not records:
            return "Nenhuma alteracao recente registrada pela IDE."
        lines = []
        for item in reversed(records):
            objective = item.get("objective") or "sem missao registrada"
            if len(objective) > 120:
                objective = objective[:117] + "..."
            lines.append(
                f"- {item.get('timestamp', '')}: {item.get('action', '')} em {item.get('rel', '')}; "
                f"missao: {objective}"
            )
        return "\n".join(lines)

    def _apply_settings_to_environment(self):
        os.environ["AI_PROVIDER"] = self.settings.get("ai_provider", "codex")
        os.environ["CODEX_MODEL_NAME"] = self.settings.get("codex_model_name", "")
        os.environ["CODEX_REASONING_EFFORT"] = self.settings.get("codex_reasoning_effort", "xhigh") or "xhigh"
        os.environ["MEROTEC_AUTONOMOUS_UNRESTRICTED"] = (
            "1" if self.settings.get("autonomous_unrestricted_mode", False) else "0"
        )
        os.environ["MEROTEC_CODEX_AUTO_APPROVE_APP_SERVER"] = (
            "1" if self.settings.get("codex_auto_approve_app_server_requests", False) else "0"
        )
        os.environ["MEROTEC_CODEX_APP_SERVER_APPROVAL_POLICY"] = (
            self.settings.get("codex_app_server_approval_policy", "on-request") or "on-request"
        )
        os.environ["MEROTEC_CODEX_APP_SERVER_IDLE_TIMEOUT_SECONDS"] = str(
            self.settings.get("codex_app_server_idle_timeout_seconds", 900) or 900
        )
        os.environ["MEROTEC_CODEX_TASK_TIMEOUT_SECONDS"] = str(
            self.settings.get("codex_task_timeout_seconds", 3600) or 3600
        )
        if self.settings.get("openai_model_name"):
            os.environ["OPENAI_MODEL_NAME"] = self.settings["openai_model_name"]
        if self.settings.get("google_model_name"):
            os.environ["GOOGLE_MODEL_NAME"] = self.settings["google_model_name"]

    def _initial_workspace(self):
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        self.settings["last_workspace"] = str(DEFAULT_WORKSPACE)
        recent_projects = [
            path for path in self.settings.get("recent_projects", [])
            if Path(path).resolve() != PROJECT_ROOT.resolve()
        ]
        if str(DEFAULT_WORKSPACE) not in recent_projects:
            recent_projects.insert(0, str(DEFAULT_WORKSPACE))
        self.settings["recent_projects"] = recent_projects[:10]
        self._save_settings()

        candidates = [
            str(DEFAULT_WORKSPACE),
            *recent_projects,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists() and path.is_dir():
                return path.resolve()
        return PROJECT_ROOT

    def update_recent_menu(self):
        self.recent_menu.delete(0, "end")
        history = self._read_history()
        if not history:
            self.recent_menu.add_command(label="Nenhum projeto recente", state="disabled")
            return
        for path in history:
            self.recent_menu.add_command(label=path, command=lambda p=path: self.set_workspace(p))

    def _read_history(self):
        history = self.settings.get("recent_projects", [])
        return [path for path in history if Path(path).exists()]

    def _write_history(self, selected_path):
        history = [path for path in self._read_history() if path != selected_path]
        history.insert(0, selected_path)
        self.settings["last_workspace"] = selected_path
        self.settings["recent_projects"] = history[:10]
        self._save_settings()
        self.update_recent_menu()

    def open_project(self):
        folder = filedialog.askdirectory(initialdir=self.current_workspace)
        if folder:
            self.set_workspace(folder)

    def set_workspace(self, path):
        resolved = Path(path).resolve()
        if not resolved.exists() or not resolved.is_dir():
            messagebox.showerror(APP_NAME, "Pasta invalida.")
            return

        self.current_workspace = str(resolved)
        os.chdir(self.current_workspace)
        if hasattr(self, "memory_subnet"):
            self.memory_subnet.reset_workspace(resolved)
        self.workspace_label.configure(text=self._workspace_title())
        self.load_workspace_files()
        self._write_history(self.current_workspace)
        self.add_chat_message("Sistema", f"Projeto aberto: {self.current_workspace}")
        self.log_agent(f"Workspace alterado para {self.current_workspace}")

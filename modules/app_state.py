import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from modules.app_constants import (
    APP_NAME,
    DEFAULT_APP_SETTINGS,
    DEFAULT_WORKSPACE,
    PROJECT_ROOT,
)
from modules.ai_profiles import (
    activate_profile,
    active_profile,
    ensure_ai_profiles,
    get_web_chat_session,
    normalize_web_url,
    profile_for,
    remember_web_chat_session,
    web_chat_url_for_workspace,
)
from modules.json_store import atomic_write_json, load_json_file


class AppStateMixin:
    def _load_settings(self):
        settings = DEFAULT_APP_SETTINGS.copy()
        loaded = load_json_file(self.settings_file, {}, dict)
        settings.update(loaded)

        if not settings.get("recent_projects") and self.history_file.exists():
            history = load_json_file(self.history_file, [], list)
            settings["recent_projects"] = [path for path in history if Path(path).exists()]
            if not settings.get("last_workspace") and settings["recent_projects"]:
                settings["last_workspace"] = settings["recent_projects"][0]

        ensure_ai_profiles(settings)
        activate_profile(settings, settings.get("active_ai_profile") or settings.get("ai_provider"))
        return settings

    def _save_settings(self):
        ensure_ai_profiles(self.settings)
        activate_profile(self.settings, self.settings.get("active_ai_profile") or self.settings.get("ai_provider"))
        if not atomic_write_json(self.settings_file, self.settings, indent=2, ensure_ascii=False):
            self.set_status("Nao consegui salvar preferencias.", "error")

    def _load_change_history(self):
        return load_json_file(self.change_history_file, [], list)

    def _save_change_history(self):
        if not atomic_write_json(self.change_history_file, self.change_history[-240:], indent=2, ensure_ascii=False):
            self.log_agent("Nao consegui salvar historico de alteracoes.")

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
        ensure_ai_profiles(self.settings)
        activate_profile(self.settings, self.settings.get("active_ai_profile") or self.settings.get("ai_provider"))
        os.environ["AI_PROVIDER"] = self.settings.get("ai_provider", "web_chat")
        os.environ["LM_STUDIO_BASE_URL"] = self.settings.get(
            "lm_studio_base_url",
            "http://127.0.0.1:1234/v1",
        )
        os.environ["LM_STUDIO_MODEL_NAME"] = self.settings.get("lm_studio_model_name", "")
        os.environ["LM_STUDIO_API_KEY"] = self.settings.get("lm_studio_api_key", "")
        os.environ["LM_STUDIO_ALLOW_EXTERNAL_FALLBACK"] = (
            "1" if self.settings.get("lm_studio_allow_external_fallback", False) else "0"
        )
        os.environ["LM_STUDIO_TIMEOUT_SECONDS"] = str(
            self.settings.get("lm_studio_timeout_seconds", 300) or 300
        )
        os.environ["LM_STUDIO_MAX_INPUT_CHARS"] = str(
            self.settings.get("lm_studio_max_input_chars", 14000) or 14000
        )
        os.environ["LM_STUDIO_MAX_TOKENS"] = str(
            self.settings.get("lm_studio_max_tokens", 1024) or 1024
        )
        os.environ["LOCAL_GGUF_PATH"] = self.settings.get("local_gguf_path", "")
        os.environ["LOCAL_GGUF_ALLOW_EXTERNAL_FALLBACK"] = (
            "1" if self.settings.get("local_gguf_allow_external_fallback", False) else "0"
        )
        for key in (
            "local_gguf_n_ctx",
            "local_gguf_n_threads",
            "local_gguf_n_gpu_layers",
            "local_gguf_n_batch",
            "local_gguf_max_tokens",
            "local_gguf_max_input_tokens",
            "local_gguf_timeout_seconds",
        ):
            if key in self.settings:
                os.environ[key.upper()] = str(self.settings.get(key) or "")
        os.environ["CODEX_MODEL_NAME"] = self.settings.get("codex_model_name", "")
        os.environ["CODEX_REASONING_EFFORT"] = self.settings.get("codex_reasoning_effort", "high") or "high"
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
        
        if self.settings.get("openai_api_key"):
            os.environ["OPENAI_API_KEY"] = str(self.settings["openai_api_key"]).strip()
        if self.settings.get("openai_model_name"):
            os.environ["OPENAI_MODEL_NAME"] = str(self.settings["openai_model_name"]).strip()
        if self.settings.get("openai_base_url"):
            os.environ["OPENAI_BASE_URL"] = str(self.settings["openai_base_url"]).strip().rstrip("/")

        if self.settings.get("google_model_name"):
            os.environ["GOOGLE_MODEL_NAME"] = self.settings["google_model_name"]
        if self.settings.get("google_api_key"):
            os.environ["GOOGLE_API_KEY"] = str(self.settings["google_api_key"]).strip()

    def active_ai_profile(self):
        """Retorna o tipo e a configuração isolada atualmente selecionados."""
        ensure_ai_profiles(self.settings)
        return active_profile(self.settings)

    def web_chat_target_for_workspace(self, workspace=None):
        workspace = workspace or self.current_workspace
        profile = profile_for(self.settings, "web_chat")
        entry_url = normalize_web_url(profile.get("web_chat_url"), "https://chatgpt.com/")
        if not bool(profile.get("web_chat_restore_project_session", True)):
            return entry_url
        return web_chat_url_for_workspace(self.settings, workspace, "web_chat")

    def remember_internal_browser_chat_url(self, url, title=""):
        """Persiste a conversa real exibida pelo WebView para o projeto atual."""
        if not url or not getattr(self, "current_workspace", ""):
            return
        remember_web_chat_session(
            self.settings,
            self.current_workspace,
            "web_chat",
            str(url),
            entry_url=str(
                self.settings.get("ai_profiles", {}).get("web_chat", {}).get(
                    "web_chat_url", self.settings.get("web_chat_url", "")
                )
            ),
            title=title,
        )
        self._save_settings()

    def activate_workspace_web_chat_session(self):
        """Prepara/restaura uma conversa pelo projeto sem abrir uma conversa nova."""
        if not getattr(self, "current_workspace", ""):
            return ""
        target = self.web_chat_target_for_workspace(self.current_workspace)
        self.web_chat_restore_url = target
        self.web_chat_workspace_key = str(Path(self.current_workspace).resolve())

        # Caso o navegador interno já esteja aberto, retorna imediatamente à
        # conversa do projeto. Se estiver fechado, apenas registra o destino
        # para a próxima abertura, evitando janelas inesperadas.
        current_url = str(getattr(self, "internal_browser_url", "") or "")
        opener = getattr(self, "open_internal_browser", None)
        if current_url and callable(opener) and current_url != target:
            try:
                opener(target, source="Sessão do projeto")
            except Exception as exc:
                self.log_agent(f"Não consegui restaurar a sessão Web do projeto: {exc}")
        return target

    def _initial_workspace(self):
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        last_workspace = self.settings.get("last_workspace", "")
        recent_projects = [
            path for path in self.settings.get("recent_projects", [])
            if Path(path).exists()
        ]
        self.settings["recent_projects"] = recent_projects[:10]

        candidates = [
            last_workspace,
            *recent_projects,
            str(DEFAULT_WORKSPACE),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists() and path.is_dir():
                resolved = path.resolve()
                self.settings["last_workspace"] = str(resolved)
                self._save_settings()
                return resolved
        return DEFAULT_WORKSPACE.resolve()

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
        folder = filedialog.askdirectory(title="Abrir projeto ou pasta", initialdir=self.current_workspace)
        if folder:
            self.set_workspace(folder)

    def create_new_project(self):
        project_name = simpledialog.askstring("Novo projeto", "Nome do projeto:", parent=self)
        if not project_name:
            return
        project_type = simpledialog.askstring(
            "Tipo do projeto",
            "Tipo: vazio, python ou web",
            initialvalue="python",
            parent=self,
        )
        if not project_type:
            return
        parent_dir = filedialog.askdirectory(
            title="Escolha onde criar o projeto",
            initialdir=str(DEFAULT_WORKSPACE),
            mustexist=True,
        )
        if not parent_dir:
            return
        try:
            project_path = self.pm.create_project(parent_dir, project_name, project_type)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.set_workspace(project_path)
        self.add_chat_message("Sistema", f"Projeto criado e aberto: {project_path}. A IA ja pode desenvolver nele.")

    def open_external_file(self):
        file_path = filedialog.askopenfilename(
            title="Abrir arquivo externo para edicao",
            initialdir=self.current_workspace,
            filetypes=[
                ("Codigo e texto", "*.py *.js *.ts *.tsx *.jsx *.html *.css *.json *.md *.txt *.cs *.java *.c *.cpp *.h *.dart *.yaml *.yml *.toml"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if file_path:
            self.open_file_in_editor(file_path)

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
        target_chat = self.activate_workspace_web_chat_session()
        self.add_chat_message(
            "Sistema",
            f"Projeto aberto: {self.current_workspace}"
            + (f"\nChat Web associado: {target_chat}" if target_chat else ""),
        )
        if resolved == PROJECT_ROOT.resolve():
            self.add_chat_message(
                "Sistema",
                "A propria IDE esta aberta como projeto. Pastas internas pesadas continuam filtradas para manter desempenho e seguranca.",
            )
        self.log_agent(f"Workspace alterado para {self.current_workspace}")

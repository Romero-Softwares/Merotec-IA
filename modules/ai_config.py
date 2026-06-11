import os
import shutil
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES, PROJECT_ROOT
from modules.engine import UniversalEngine
from modules.ui_theme import THEME


class AiConfigMixin:
    def refresh_ai_status(self):
        if hasattr(self, "ai_status_label"):
            self.ai_status_label.configure(text=self.engine.status_text())

    def find_codex_executable(self):
        candidates = []
        roots = [
            Path(os.getenv("ProgramFiles", "")) / "WindowsApps",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
        ]
        patterns = [
            "OpenAI.Codex_*\\app\\resources\\codex.exe",
            "OpenAI.Codex_*\\app\\resources\\codex",
            "codex.exe",
        ]
        for root in roots:
            if not root.exists():
                continue
            for pattern in patterns:
                try:
                    for candidate in sorted(root.glob(pattern), reverse=True):
                        if candidate.exists():
                            candidates.append(str(candidate))
                except OSError:
                    continue

        for executable in (shutil.which("codex.exe"), shutil.which("codex")):
            if executable:
                candidates.append(executable)

        for candidate in dict.fromkeys(candidates):
            if self.can_run_codex(candidate):
                return candidate
        return None

    def can_run_codex(self, executable):
        try:
            process = subprocess.Popen(
                [executable, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.current_workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=5)
            return process.returncode == 0 and "codex" in (output or "").lower()
        except Exception:
            return False

    def ensure_codex_ready(self):
        if self.engine.provider != "codex":
            return

        executable = self.find_codex_executable()
        if not executable:
            self.add_chat_message("Sistema", "Codex nao encontrado. Abrindo a instalacao automaticamente.")
            self.log_agent("Codex nao encontrado. Iniciando instalador.")
            self.install_codex()
            return

        self._add_codex_to_path(executable)
        if not self.codex_is_logged_in(executable):
            self.add_chat_message("Sistema", "Codex encontrado, mas ainda sem login. Abrindo login do Codex.")
            self.log_agent("Codex encontrado sem login. Abrindo autenticacao.")
            self.launch_codex_login()
            return

        self.engine = UniversalEngine()
        self.refresh_ai_status()
        self.codex_login_started = False
        self.set_status("Codex pronto.", "ready")

    def _add_codex_to_path(self, executable):
        folder = str(Path(executable).parent)
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if not any(entry.lower() == folder.lower() for entry in path_entries):
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")

    def codex_is_logged_in(self, executable=None):
        executable = executable or self.find_codex_executable()
        if not executable:
            return False
        try:
            process = subprocess.Popen(
                [executable, "login", "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.current_workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=12)
            return process.returncode == 0 and "not logged in" not in (output or "").lower()
        except Exception:
            return False

    def launch_codex_login(self):
        if self.codex_login_started:
            self.set_status("Login do Codex ja esta aberto.", "busy")
            return

        executable = self.find_codex_executable()
        if not executable:
            self.install_codex()
            return

        self._add_codex_to_path(executable)
        self.codex_login_started = True
        command = (
            f"& '{executable}' login; "
            f"& '{executable}' login status; "
            "Write-Host ''; "
            "Write-Host 'Quando o login terminar, feche esta janela e volte para a Merotec IA IDE.'"
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NoExit", "-Command", command],
                cwd=self.current_workspace,
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            self.set_status("Login do Codex aberto.", "busy")
            self.after(15000, self.ensure_codex_ready)
        except Exception as exc:
            self.codex_login_started = False
            self.add_chat_message("Erro", f"Nao consegui abrir o login do Codex: {exc}")

    def install_codex(self):
        if self.codex_setup_started:
            self.add_chat_message("Sistema", "Instalacao/login do Codex ja esta em andamento.")
            return

        self.codex_setup_started = True
        self.tabview.set("Terminal Local")
        self.append_to_term("\n> Instalando Codex automaticamente...\n")
        self.set_status("Instalando Codex...", "busy")

        script = (
            "$ErrorActionPreference='Continue'; "
            "Write-Host 'Instalando OpenAI Codex...'; "
            "$winget = Get-Command winget -ErrorAction SilentlyContinue; "
            "if ($winget) { "
            "  & $winget.Source install --id OpenAI.Codex -e --source msstore "
            "  --accept-package-agreements --accept-source-agreements; "
            "} "
            "if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { "
            "  Write-Host 'Abrindo Microsoft Store para concluir a instalacao...'; "
            "  Start-Process 'ms-windows-store://pdp/?PFN=OpenAI.Codex_2p2nqsd0c76g0'; "
            "} "
            "Write-Host ''; "
            "Write-Host 'Depois de instalar, volte para a IDE. Ela tentara abrir o login automaticamente.'"
        )

        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NoExit", "-Command", script],
                cwd=str(PROJECT_ROOT),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            threading.Thread(target=self._monitor_codex_install, daemon=True).start()
        except Exception as exc:
            self.codex_setup_started = False
            self.add_chat_message("Erro", f"Nao consegui iniciar a instalacao do Codex: {exc}")

    def _monitor_codex_install(self):
        for _ in range(90):
            threading.Event().wait(4)
            executable = self.find_codex_executable()
            if executable:
                self._add_codex_to_path(executable)
                self.codex_setup_started = False
                self.after(0, self.launch_codex_login)
                self.after(0, lambda: self.add_chat_message("Sistema", "Codex instalado/encontrado. Abrindo login."))
                return
        self.codex_setup_started = False
        self.after(0, lambda: self.add_chat_message("Sistema", "Quando terminar a instalacao do Codex, clique em Entrar Codex."))
        self.after(0, lambda: self.set_status("Aguardando Codex.", "busy"))

    def configure_ai(self):
        provider = self.prompt_value(
            "Provedor da IA",
            "Escolha: codex, openai ou google.\nUse codex para sua conta ja logada no Windows.",
            initial_value=self.engine.provider,
        )
        provider = (provider or "").strip().lower()
        if not provider:
            return
        if provider not in {"codex", "openai", "google"}:
            self.add_chat_message("Erro", "Provedor invalido. Use codex, openai ou google.")
            return

        os.environ["AI_PROVIDER"] = provider

        if provider == "codex":
            model = self.prompt_value(
                "Modelo Codex",
                "Opcional. Deixe vazio para usar o modelo padrao da sua conta Codex.",
                initial_value=os.getenv("CODEX_MODEL_NAME", ""),
            )
            os.environ["CODEX_MODEL_NAME"] = model or ""
            effort = self.prompt_value(
                "Raciocinio Codex",
                "Use xhigh para raciocinio altissimo. Se sua versao do Codex nao aceitar, a IDE tenta high automaticamente.",
                initial_value=os.getenv("CODEX_REASONING_EFFORT", self.settings.get("codex_reasoning_effort", "xhigh") or "xhigh"),
            )
            os.environ["CODEX_REASONING_EFFORT"] = (effort or "xhigh").strip().lower()
            self.settings.update(
                {
                    "ai_provider": "codex",
                    "codex_model_name": os.getenv("CODEX_MODEL_NAME", ""),
                    "codex_reasoning_effort": os.getenv("CODEX_REASONING_EFFORT", "xhigh"),
                }
            )
            self._save_settings()
            self.engine = UniversalEngine()
            self.refresh_ai_status()
            self.add_chat_message("Sistema", f"Codex configurado: {self.engine.status_text()}")
            self.log_agent("Configuracao Codex atualizada na sessao.")
            return

        if provider == "google":
            model = self.prompt_value(
                "Modelo Google",
                "Modelo Gemini atual.",
                initial_value=os.getenv("GOOGLE_MODEL_NAME", self.engine.model_id),
            )
            if model:
                os.environ["GOOGLE_MODEL_NAME"] = model

            api_key = self.prompt_value(
                "Chave Google",
                "Cole a GOOGLE_API_KEY. Ela fica apenas nesta sessao do app.",
                secret=True,
            )
            if api_key:
                os.environ["GOOGLE_API_KEY"] = api_key

            self.settings.update(
                {
                    "ai_provider": "google",
                    "google_model_name": os.getenv("GOOGLE_MODEL_NAME", self.engine.model_id),
                }
            )
            self._save_settings()
            self.engine = UniversalEngine()
            self.refresh_ai_status()
            self.add_chat_message("Sistema", f"IA Google configurada: {self.engine.status_text()}")
            self.log_agent("Configuracao Google atualizada na sessao.")
            return

        model = self.prompt_value(
            "Modelo da IA",
            f"Modelo OpenAI atual: {self.engine.model_id}\nExemplos: gpt-5.2, gpt-5.5",
            initial_value=self.engine.model_id,
        )
        if model:
            os.environ["OPENAI_MODEL_NAME"] = model

        api_key = self.prompt_value(
            "Chave OpenAI",
            "Cole a OPENAI_API_KEY. Ela fica apenas nesta sessao do app.",
            secret=True,
        )
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key

        os.environ["AI_PROVIDER"] = "openai"
        self.settings.update(
            {
                "ai_provider": "openai",
                "openai_model_name": os.getenv("OPENAI_MODEL_NAME", self.engine.model_id),
            }
        )
        self._save_settings()
        self.engine = UniversalEngine()
        self.refresh_ai_status()
        self.add_chat_message("Sistema", f"IA configurada: {self.engine.status_text()}")
        self.log_agent("Configuracao da IA atualizada na sessao.")

    def prompt_value(self, title, text, initial_value="", secret=False):
        result = {"value": None}
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("420x180")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=THEME["panel"])
        dialog.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            dialog,
            text=text,
            text_color=THEME["text"],
            font=("Segoe UI", 13),
            wraplength=370,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))

        entry = ctk.CTkEntry(
            dialog,
            text_color=THEME["text"],
            fg_color=THEME["panel_alt"],
            border_color=THEME["border"],
            show="*" if secret else "",
            height=34,
        )
        entry.grid(row=1, column=0, sticky="ew", padx=18, pady=6)
        if initial_value:
            entry.insert(0, initial_value)
            entry.select_range(0, "end")
        entry.focus_set()

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e", padx=18, pady=(12, 16))

        def accept():
            result["value"] = entry.get().strip()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        cancel_button = self._elevated_button(buttons, text="Cancelar", width=90, height=30, command=cancel)
        save_button = self._elevated_button(
            buttons,
            text="Salvar",
            width=90,
            height=30,
            fg_color=THEME["accent"],
            hover_color=THEME["accent_dark"],
            border_color="#7cc7ff",
            text_color="#06111d",
            command=accept,
        )
        cancel_button.elevation_shadow.pack(side="left", padx=4)
        save_button.elevation_shadow.pack(side="left", padx=4)

        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: cancel())
        self.wait_window(dialog)
        return result["value"]

    def iter_workspace_files(self, limit=500):
        workspace = Path(self.current_workspace)
        count = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in sorted(dirs) if d not in IGNORED_DIRS and not d.startswith(".")]
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

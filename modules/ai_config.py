import os
import shutil
import subprocess
import threading
from pathlib import Path
from tkinter import StringVar, filedialog

import customtkinter as ctk

from modules.app_constants import DEFAULT_APP_SETTINGS, IGNORED_SUFFIXES, PROJECT_ROOT, is_ignored_dir_name
from modules.ai_profiles import (
    PROVIDER_LABELS,
    PROVIDER_ORDER,
    activate_profile,
    ensure_ai_profiles,
    normalize_provider,
    normalize_web_url,
    profile_for,
    provider_from_label,
    provider_label,
    update_profile,
)
from modules.engine import UniversalEngine
from modules.ui_theme import THEME
from modules.voice import DEFAULT_EDGE_VOICE_ID, TTS_ENGINE_EDGE, list_tts_voices


# MEROTEC_CONFIG_CONTRAST_V2
# Paleta exclusiva da janela de configuração. Mantém o modo escuro da IDE,
# mas oferece contraste maior para leitura, campos e ações principais.
CONFIG_UI = {
    "bg": "#16243A",
    "surface": "#20324D",
    "field": "#2A3E5C",
    "field_hover": "#344E70",
    "border": "#5AAEE8",
    "text": "#F2F7FC",
    "label": "#D5E5F4",
    "muted": "#B6C9DB",
    "accent": "#39D5FF",
    "accent_hover": "#1B93C7",
}


class AiConfigMixin:
    def ai_status_text(self):
        status = self.engine.status_text()
        quota = self.engine.quota_status_text()
        fallback = self.ai_fallback_status_text()
        if fallback:
            status = f"{status}\n{fallback}"
        if not quota or quota == status:
            return status
        return f"{status}\nCota atual: {quota}"

    def ai_fallback_status_text(self):
        engine = getattr(self, "engine", None)
        if engine is None:
            return ""
        local_status = "Fallback local: RAG offline extrativo, limitado ao corpus da sub-rede"
        provider = str(getattr(engine, "provider", "") or "").strip().lower()
        if provider in {"local_gguf", "lm_studio"}:
            local_enabled = bool(
                getattr(engine, f"{provider}_allow_external_fallback", False)
            )
            if not local_enabled:
                return f"Fallback externo: desligado\n{local_status}"

        enabled = bool(getattr(engine, "external_ai_fallback_enabled", False))
        providers_fn = getattr(engine, "configured_external_ai_fallback_providers", None)
        providers = []
        if enabled and callable(providers_fn):
            try:
                providers = providers_fn()
            except Exception:
                providers = []

        if not enabled or not providers:
            return f"Fallback externo: indisponivel\n{local_status}"
        external_status = "Fallback externo: " + ", ".join(str(item).upper() for item in providers)
        return f"{external_status}\n{local_status}"

    def refresh_ai_status(self):
        if hasattr(self, "ai_status_label"):
            self.ai_status_label.configure(text=self.ai_status_text())

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

    def _ai_profile_fields(self, provider):
        """Metadados dos campos visíveis de cada perfil, isolados entre si."""
        common_web = [
            ("web_chat_url", "URL do chat web", "url"),
            ("web_chat_timeout_seconds", "Tempo máximo por resposta (s)", "int"),
            ("web_chat_message_chars", "Caracteres por mensagem (sem truncar o contexto)", "int"),
            ("web_chat_auto_attach_media", "Tentar anexar prints/imagens automaticamente", "bool"),
            ("web_chat_restore_project_session", "Restaurar conversa ao retornar ao projeto", "bool"),
        ]
        definitions = {
            "web_chat": common_web + [
                ("web_chat_allow_remote_actions", "Permitir ações remotas fora de localhost", "bool"),
                ("web_chat_include_project_context", "Enviar contexto do projeto", "bool"),
                ("web_chat_auto_apply_imported_actions", "Aplicar respostas importadas automaticamente", "bool"),
            ],
            "codex": [
                ("codex_model_name", "Modelo Codex (vazio = padrão da conta)", "text"),
                ("codex_reasoning_effort", "Esforço de raciocínio: low, medium, high ou xhigh", "text"),
                ("browser_ai_fallback_enabled", "Permitir fallback por Chat Web", "bool"),
                ("browser_ai_fallback_url", "URL do Chat Web de fallback", "url"),
                ("browser_ai_fallback_timeout_seconds", "Tempo de fallback (s)", "int"),
                ("browser_ai_fallback_max_context_chars", "Contexto do fallback (caracteres)", "int"),
            ],
            "openai": [
                ("openai_base_url", "URL base compatível com OpenAI", "url"),
                ("openai_model_name", "ID exato do modelo", "text"),
                ("openai_api_key", "Chave de API", "secret"),
            ],
            "google": [
                ("google_model_name", "Modelo Gemini", "text"),
                ("google_api_key", "Chave Google GenAI", "secret"),
            ],
            "lm_studio": [
                ("lm_studio_base_url", "URL do servidor LM Studio", "url"),
                ("lm_studio_model_name", "Modelo carregado no servidor", "text"),
                ("lm_studio_api_key", "Chave do servidor (opcional)", "secret"),
                ("lm_studio_timeout_seconds", "Tempo máximo (s)", "int"),
                ("lm_studio_max_input_chars", "Contexto máximo (caracteres)", "int"),
                ("lm_studio_max_tokens", "Tokens de saída", "int"),
                ("lm_studio_allow_external_fallback", "Permitir fallback quando o local falhar", "bool"),
            ],
            "local_gguf": [
                ("local_gguf_path", "Arquivo .gguf", "file"),
                ("local_gguf_n_ctx", "Janela de contexto", "int"),
                ("local_gguf_n_threads", "Threads (0 = automático)", "int"),
                ("local_gguf_n_gpu_layers", "Camadas GPU", "int"),
                ("local_gguf_n_batch", "Batch", "int"),
                ("local_gguf_max_tokens", "Tokens de saída", "int"),
                ("local_gguf_max_input_tokens", "Tokens de entrada", "int"),
                ("local_gguf_timeout_seconds", "Tempo máximo (s)", "int"),
                ("local_gguf_allow_external_fallback", "Permitir fallback externo", "bool"),
            ],
        }
        return definitions[normalize_provider(provider)]

    def configure_ai(self):
        """Janela única com select de tipos de IA e configurações independentes."""
        ensure_ai_profiles(self.settings)
        selected = normalize_provider(
            getattr(self.engine, "provider", None) or self.settings.get("active_ai_profile"),
            default="web_chat",
        )
        drafts = {provider: profile_for(self.settings, provider) for provider in PROVIDER_ORDER}
        dialog = ctk.CTkToplevel(self)
        dialog.title("Configurações")
        dialog.geometry("720x740")
        dialog.minsize(620, 560)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=CONFIG_UI["bg"])
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            dialog,
            text="Configurações",
            text_color=CONFIG_UI["text"],
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 2))

        description = ctk.CTkLabel(
            dialog,
            text=(
                "Cada perfil salva sua própria configuração. Chat Web aceita qualquer URL, "
                "mantém uma conversa por projeto e restaura a URL quando você voltar ao projeto."
            ),
            text_color=CONFIG_UI["muted"],
            font=("Segoe UI", 12),
            wraplength=650,
            justify="left",
        )
        description.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))

        selected_label = StringVar(value=provider_label(selected))
        selector = ctk.CTkOptionMenu(
            dialog,
            variable=selected_label,
            values=[PROVIDER_LABELS[key] for key in PROVIDER_ORDER],
            fg_color=CONFIG_UI["field"],
            button_color=CONFIG_UI["accent"],
            button_hover_color=CONFIG_UI["accent_hover"],
            text_color=CONFIG_UI["text"],
            dropdown_fg_color=CONFIG_UI["surface"],
            dropdown_hover_color=CONFIG_UI["field_hover"],
            dropdown_text_color=CONFIG_UI["text"],
            height=38,
        )
        selector.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))

        body = ctk.CTkScrollableFrame(
            dialog,
            fg_color=CONFIG_UI["surface"],
            border_color=CONFIG_UI["border"],
            border_width=1,
            corner_radius=10,
        )
        body.grid(row=3, column=0, sticky="nsew", padx=18, pady=(0, 8))
        body.grid_columnconfigure(1, weight=1)
        current = {"provider": selected, "widgets": {}, "types": {}}
        system_options = {}

        def title_for(provider):
            lines = {
                "web_chat": "Chat Web: cole ou digite a URL do chat que preferir. Ex.: https://gemini.google.com/",
                "codex": "Codex local: usa a sessão autenticada no Windows e mantém a configuração deste perfil.",
                "openai": "API compatível: OpenAI, OpenRouter ou outro endpoint com o formato OpenAI.",
                "google": "Google Gemini API: use uma chave de projeto Google GenAI.",
                "lm_studio": "LM Studio: use o servidor local já iniciado e o modelo carregado nele.",
                "local_gguf": "GGUF local: execução offline por llama.cpp, com perfil próprio de contexto e hardware.",
            }
            return lines[provider]

        def collect_current():
            provider = current["provider"]
            values = drafts[provider]
            for key, widget in current["widgets"].items():
                kind = current["types"][key]
                if kind == "bool":
                    values[key] = bool(widget.get())
                else:
                    values[key] = widget.get().strip()
            drafts[provider] = values

        def browse_gguf(entry):
            selected_path = filedialog.askopenfilename(
                title="Selecionar modelo local GGUF",
                initialdir=self.initial_local_model_dir(entry.get().strip()),
                filetypes=(("Modelos GGUF", "*.gguf"), ("Todos os arquivos", "*.*")),
                parent=dialog,
            )
            if selected_path:
                entry.delete(0, "end")
                entry.insert(0, selected_path)

        def render(provider):
            provider = normalize_provider(provider)
            for child in body.winfo_children():
                child.destroy()
            current["provider"] = provider
            current["widgets"] = {}
            current["types"] = {}

            ctk.CTkLabel(
                body,
                text=title_for(provider),
                text_color=CONFIG_UI["accent"],
                font=("Segoe UI", 14, "bold"),
                wraplength=620,
                justify="left",
            ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 14))

            system_frame = ctk.CTkFrame(
                body,
                fg_color=CONFIG_UI["bg"],
                border_color=CONFIG_UI["border"],
                border_width=1,
                corner_radius=8,
            )
            system_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 14))
            system_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                system_frame,
                text="Sistema",
                text_color=CONFIG_UI["accent"],
                font=("Segoe UI", 13, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
            ctk.CTkLabel(
                system_frame,
                text="Escuta automatica do microfone",
                text_color=CONFIG_UI["label"],
                font=("Segoe UI", 12),
                wraplength=240,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=(12, 10), pady=(6, 10))
            voice_switch = ctk.CTkSwitch(
                system_frame,
                text="Ativado",
                onvalue=True,
                offvalue=False,
                fg_color=CONFIG_UI["field"],
                progress_color=CONFIG_UI["accent"],
                button_color=CONFIG_UI["text"],
                button_hover_color="#FFFFFF",
                text_color=CONFIG_UI["text"],
            )
            if bool(self.settings.get("voice_keyword_listener_enabled", DEFAULT_APP_SETTINGS["voice_keyword_listener_enabled"])):
                voice_switch.select()
            else:
                voice_switch.deselect()
            voice_switch.grid(row=1, column=1, sticky="w", padx=4, pady=(6, 10))
            tts_voices = list_tts_voices()
            default_label = "Microsoft Antonio Neural - Portugues Brasil (EDGE)"
            voice_label_to_config = {
                default_label: {
                    "id": DEFAULT_EDGE_VOICE_ID,
                    "engine": TTS_ENGINE_EDGE,
                }
            }
            for voice in tts_voices:
                label = str(voice.get("label") or voice.get("name") or voice.get("id"))
                voice_label_to_config[label] = {
                    "id": str(voice.get("id") or DEFAULT_EDGE_VOICE_ID),
                    "engine": str(voice.get("engine") or TTS_ENGINE_EDGE),
                }
            current_voice_id = str(self.settings.get("tts_voice_id") or DEFAULT_APP_SETTINGS["tts_voice_id"])
            selected_voice_label = default_label
            for label, config in voice_label_to_config.items():
                if config["id"] == current_voice_id:
                    selected_voice_label = label
                    break
            ctk.CTkLabel(
                system_frame,
                text="Voz da leitura",
                text_color=CONFIG_UI["label"],
                font=("Segoe UI", 12),
                wraplength=240,
                justify="left",
            ).grid(row=2, column=0, sticky="w", padx=(12, 10), pady=(4, 10))
            voice_choice = StringVar(value=selected_voice_label)
            voice_menu = ctk.CTkOptionMenu(
                system_frame,
                variable=voice_choice,
                values=list(voice_label_to_config.keys()),
                fg_color=CONFIG_UI["field"],
                button_color=CONFIG_UI["accent"],
                button_hover_color=CONFIG_UI["accent_hover"],
                text_color=CONFIG_UI["text"],
                dropdown_fg_color=CONFIG_UI["surface"],
                dropdown_hover_color=CONFIG_UI["field_hover"],
                dropdown_text_color=CONFIG_UI["text"],
                height=34,
            )
            voice_menu.grid(row=2, column=1, sticky="ew", padx=4, pady=(4, 10))
            ctk.CTkLabel(
                system_frame,
                text=(
                    "Desligue para economizar memoria e evitar manter o microfone ativo. "
                    "Quando quiser usar comando por palavra-chave, habilite novamente aqui."
                ),
                text_color=CONFIG_UI["muted"],
                font=("Segoe UI", 11),
                wraplength=600,
                justify="left",
            ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
            system_options["voice_keyword_listener_enabled"] = voice_switch
            system_options["tts_voice_choice"] = voice_choice
            system_options["tts_voice_configs"] = voice_label_to_config

            row = 2
            for key, label, kind in self._ai_profile_fields(provider):
                ctk.CTkLabel(
                    body,
                    text=label,
                    text_color=CONFIG_UI["label"],
                    font=("Segoe UI", 12),
                    wraplength=240,
                    justify="left",
                ).grid(row=row, column=0, sticky="w", padx=(4, 10), pady=7)
                value = drafts[provider].get(key, "")
                if kind == "bool":
                    control = ctk.CTkSwitch(
                        body,
                        text="Ativado",
                        onvalue=True,
                        offvalue=False,
                        fg_color=CONFIG_UI["field"],
                        progress_color=CONFIG_UI["accent"],
                        button_color=CONFIG_UI["text"],
                        button_hover_color="#FFFFFF",
                        text_color=CONFIG_UI["text"],
                    )
                    if bool(value):
                        control.select()
                    else:
                        control.deselect()
                    control.grid(row=row, column=1, sticky="w", padx=4, pady=7)
                else:
                    field = ctk.CTkFrame(body, fg_color="transparent")
                    field.grid(row=row, column=1, sticky="ew", padx=4, pady=7)
                    field.grid_columnconfigure(0, weight=1)
                    control = ctk.CTkEntry(
                        field,
                        fg_color=CONFIG_UI["field"],
                        border_color=CONFIG_UI["border"],
                        text_color=CONFIG_UI["text"],
                        placeholder_text_color=CONFIG_UI["muted"],
                        show="*" if kind == "secret" else "",
                        height=34,
                    )
                    control.grid(row=0, column=0, sticky="ew")
                    control.insert(0, str(value or ""))
                    if kind == "file":
                        browse = ctk.CTkButton(
                            field,
                            text="Procurar",
                            width=90,
                            height=34,
                            fg_color=CONFIG_UI["field_hover"],
                            hover_color=CONFIG_UI["accent_hover"],
                            text_color=CONFIG_UI["text"],
                            command=lambda target=control: browse_gguf(target),
                        )
                        browse.grid(row=0, column=1, padx=(8, 0))
                current["widgets"][key] = control
                current["types"][key] = kind
                row += 1

            if provider == "web_chat":
                ctk.CTkLabel(
                    body,
                    text=(
                        "Mídia: a IDE tenta inserir prints/imagens por drop programático. "
                        "Sites que bloquearem anexos automáticos continuam recebendo o texto e a conversa não é perdida."
                    ),
                    text_color=CONFIG_UI["muted"],
                    font=("Segoe UI", 11),
                    wraplength=620,
                    justify="left",
                ).grid(row=row, column=0, columnspan=2, sticky="ew", padx=4, pady=(12, 4))

        def change_provider(choice):
            collect_current()
            render(provider_from_label(choice))

        selector.configure(command=change_provider)
        render(selected)

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="e", padx=18, pady=(0, 12))

        def normalize_values(provider, values, *, validate_active=False):
            """Normaliza perfis sem deixar configuração inativa bloquear o salvamento.

            Um caminho GGUF legado pode deixar de existir depois que o usuário move
            arquivos. Esse dado deve continuar visível para correção futura, mas só
            deve impedir o salvamento quando o perfil GGUF for o perfil ativo.
            """
            cleaned = dict(values)
            for key, _label, kind in self._ai_profile_fields(provider):
                raw = cleaned.get(key)
                if kind == "int":
                    try:
                        cleaned[key] = max(0, int(str(raw or "0").strip()))
                    except ValueError:
                        if validate_active:
                            raise ValueError(f"'{key}' precisa ser um número inteiro.")
                        cleaned[key] = str(raw or "").strip()
                elif kind == "url":
                    try:
                        cleaned[key] = normalize_web_url(raw)
                    except ValueError:
                        if validate_active:
                            raise
                        cleaned[key] = str(raw or "").strip()
                elif kind in {"text", "secret", "file"}:
                    cleaned[key] = str(raw or "").strip()
            if provider == "lm_studio" and cleaned.get("lm_studio_base_url"):
                cleaned["lm_studio_base_url"] = UniversalEngine.normalize_lm_studio_base_url(
                    cleaned["lm_studio_base_url"]
                )
            if provider == "local_gguf" and cleaned.get("local_gguf_path"):
                selected_path = self.resolve_local_gguf_selection(cleaned["local_gguf_path"])
                if selected_path:
                    cleaned["local_gguf_path"] = str(selected_path)
                elif validate_active:
                    raise ValueError("O caminho GGUF não contém um arquivo .gguf válido.")
            return cleaned

        def save_and_activate(open_chat=False):
            try:
                collect_current()
                active = current["provider"]
                self.settings["voice_keyword_listener_enabled"] = bool(
                    system_options["voice_keyword_listener_enabled"].get()
                )
                selected_voice = system_options["tts_voice_choice"].get()
                selected_voice_config = system_options["tts_voice_configs"].get(selected_voice, {})
                self.settings["tts_engine"] = selected_voice_config.get("engine", TTS_ENGINE_EDGE)
                self.settings["tts_voice_id"] = selected_voice_config.get("id", DEFAULT_EDGE_VOICE_ID)
                for provider in PROVIDER_ORDER:
                    update_profile(
                        self.settings,
                        provider,
                        normalize_values(
                            provider,
                            drafts[provider],
                            validate_active=(provider == active),
                        ),
                    )
                activate_profile(self.settings, active)
                self._save_settings()
                apply_environment = getattr(self, "_apply_settings_to_environment", None)
                if callable(apply_environment):
                    apply_environment()
                previous_engine = getattr(self, "engine", None)
                if previous_engine and getattr(previous_engine, "web_chat_bridge", None):
                    previous_engine.web_chat_bridge.close()
                self.engine = UniversalEngine()
                attach_bridge = getattr(self, "attach_internal_web_chat_bridge", None)
                if callable(attach_bridge):
                    attach_bridge()
                self.refresh_ai_status()
                self.log_agent(f"Perfil de IA ativado: {provider_label(active)}")
                self.add_chat_message(
                    "Sistema",
                    f"Perfil ativo: {provider_label(active)}. As configurações dos outros perfis foram preservadas.",
                )
                if active == "codex":
                    self.ensure_codex_ready()
                apply_voice_setting = getattr(self, "apply_voice_keyword_listener_setting", None)
                if callable(apply_voice_setting):
                    apply_voice_setting()
                if active == "web_chat":
                    target = ""
                    session_fn = getattr(self, "activate_workspace_web_chat_session", None)
                    if callable(session_fn):
                        target = session_fn()
                    if open_chat:
                        opener = getattr(self, "open_internal_browser", None)
                        if callable(opener):
                            opener(target or self.engine.web_chat_url, source="Chat Web")
                dialog.destroy()
            except ValueError as exc:
                self.add_chat_message("Erro", f"Configuração inválida: {exc}")
            except Exception as exc:
                self.add_chat_message("Erro", f"Não consegui salvar a configuração de IA: {exc}")

        cancel_button = self._elevated_button(
            buttons, text="Cancelar", width=98, height=32, command=dialog.destroy
        )
        web_button = self._elevated_button(
            buttons,
            text="Salvar e abrir Chat",
            width=154,
            height=32,
            fg_color=CONFIG_UI["field_hover"],
            hover_color=CONFIG_UI["accent_hover"],
            border_color=CONFIG_UI["border"],
            text_color=CONFIG_UI["text"],
            command=lambda: save_and_activate(open_chat=True),
        )
        save_button = self._elevated_button(
            buttons,
            text="Salvar e ativar",
            width=132,
            height=32,
            fg_color=CONFIG_UI["accent"],
            hover_color=CONFIG_UI["accent_hover"],
            border_color="#7cc7ff",
            text_color="#06111d",
            command=save_and_activate,
        )
        cancel_button.elevation_shadow.pack(side="left", padx=4)
        web_button.elevation_shadow.pack(side="left", padx=4)
        save_button.elevation_shadow.pack(side="left", padx=4)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()

    def configure_lm_studio(self):
        current_url = (
            self.settings.get("lm_studio_base_url")
            or os.getenv("LM_STUDIO_BASE_URL")
            or "http://127.0.0.1:1234/v1"
        )
        base_url = self.prompt_value(
            "Servidor do LM Studio",
            "URL da API local compativel com OpenAI. O padrao do LM Studio e http://127.0.0.1:1234/v1.",
            initial_value=current_url,
        )
        if base_url is None:
            return
        base_url = UniversalEngine.normalize_lm_studio_base_url(base_url)
        api_key = self.prompt_value(
            "Chave do LM Studio (opcional)",
            "Deixe vazio se a autenticacao do servidor local estiver desativada.",
            initial_value=str(self.settings.get("lm_studio_api_key") or ""),
            secret=True,
        )
        if api_key is None:
            return
        api_key = api_key.strip()

        try:
            models = UniversalEngine.discover_lm_studio_models(base_url, api_key=api_key, timeout=5)
        except Exception as exc:
            self.add_chat_message(
                "Erro",
                f"Nao consegui conectar ao LM Studio em {base_url}. Confirme se o servidor esta ligado.\n\n{exc}",
            )
            return

        if not models:
            self.add_chat_message(
                "Erro",
                "O servidor do LM Studio respondeu, mas nao informou nenhum modelo carregado.",
            )
            return

        current_model = str(self.settings.get("lm_studio_model_name") or "").strip()
        initial_model = current_model if current_model in models else models[0]
        model = self.prompt_value(
            "Modelo do LM Studio",
            "Modelos de chat detectados no servidor:\n- " + "\n- ".join(models),
            initial_value=initial_model,
        )
        if model is None:
            return
        model = model.strip() or initial_model
        if model not in models:
            self.add_chat_message(
                "Erro",
                f"O modelo '{model}' nao foi informado pelo servidor. Selecione um dos modelos detectados.",
            )
            return

        os.environ["AI_PROVIDER"] = "lm_studio"
        os.environ["LM_STUDIO_BASE_URL"] = base_url
        os.environ["LM_STUDIO_MODEL_NAME"] = model
        os.environ["LM_STUDIO_API_KEY"] = api_key
        self.settings.update(
            {
                "ai_provider": "lm_studio",
                "lm_studio_base_url": base_url,
                "lm_studio_model_name": model,
                "lm_studio_api_key": api_key,
                "lm_studio_allow_external_fallback": bool(
                    self.settings.get("lm_studio_allow_external_fallback", False)
                ),
                "lm_studio_timeout_seconds": self.int_setting_value(
                    "lm_studio_timeout_seconds", 300
                ),
                "lm_studio_max_input_chars": self.int_setting_value(
                    "lm_studio_max_input_chars", 14000
                ),
                "lm_studio_max_tokens": self.int_setting_value("lm_studio_max_tokens", 1024),
            }
        )
        self._save_settings()
        self.engine = UniversalEngine()
        self.refresh_ai_status()
        self.add_chat_message("Sistema", f"LM Studio conectado: {self.engine.status_text()}")
        self.log_agent(f"LM Studio configurado em {base_url} com o modelo {model}.")

    def select_local_gguf_model_path(self):
        current = (
            os.getenv("LOCAL_GGUF_PATH")
            or self.settings.get("local_gguf_path")
            or getattr(self.engine, "local_gguf_path", "")
            or ""
        )
        typed = self.prompt_value(
            "Modelo local GGUF",
            "Cole o caminho de um arquivo .gguf ou de uma pasta com modelos. Deixe vazio para procurar no computador.",
            initial_value=current,
        )
        if typed is None:
            return None
        typed = typed.strip()
        if not typed:
            initial_dir = self.initial_local_model_dir(current)
            typed = filedialog.askopenfilename(
                title="Selecionar modelo local GGUF",
                initialdir=initial_dir,
                filetypes=(("Modelos GGUF", "*.gguf"), ("Todos os arquivos", "*.*")),
            )
            if not typed:
                return None

        selected = self.resolve_local_gguf_selection(typed)
        if not selected:
            self.add_chat_message("Erro", "Nao encontrei nenhum arquivo .gguf nesse caminho ou pasta.")
            return None
        return str(selected)

    def initial_local_model_dir(self, current=""):
        candidates = []
        if current:
            path = Path(current)
            candidates.append(path.parent if path.suffix else path)
        candidates.extend([PROJECT_ROOT / "models", PROJECT_ROOT])
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_dir():
                    return str(candidate)
            except OSError:
                continue
        return str(PROJECT_ROOT)

    def resolve_local_gguf_selection(self, raw_path):
        path = Path(str(raw_path or "").strip().strip("\"'"))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        try:
            path = path.resolve()
        except OSError:
            return None
        if path.is_file() and path.suffix.lower() == ".gguf":
            return path
        if path.is_dir():
            models = sorted(
                (candidate for candidate in path.rglob("*.gguf") if candidate.is_file()),
                key=lambda candidate: (candidate.stat().st_mtime, candidate.stat().st_size),
                reverse=True,
            )
            return models[0] if models else None
        return None

    def local_gguf_settings_payload(self, model_path):
        return {
            "ai_provider": "local_gguf",
            "local_gguf_path": model_path,
            "local_gguf_n_ctx": self.int_setting_value("local_gguf_n_ctx", 4096),
            "local_gguf_n_threads": self.int_setting_value(
                "local_gguf_n_threads",
                UniversalEngine.default_local_gguf_threads(),
            ),
            "local_gguf_n_gpu_layers": self.int_setting_value("local_gguf_n_gpu_layers", 0),
            "local_gguf_n_batch": self.int_setting_value("local_gguf_n_batch", 256),
            "local_gguf_max_tokens": self.int_setting_value("local_gguf_max_tokens", 160),
            "local_gguf_max_input_tokens": self.int_setting_value("local_gguf_max_input_tokens", 900),
            "local_gguf_timeout_seconds": self.int_setting_value("local_gguf_timeout_seconds", 12),
            "local_gguf_allow_external_fallback": bool(
                self.settings.get("local_gguf_allow_external_fallback", False)
            ),
        }

    def int_setting_value(self, key, default):
        try:
            return int(self.settings.get(key, default) or default)
        except (TypeError, ValueError):
            return int(default)

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
            dirs[:] = [d for d in sorted(dirs) if not is_ignored_dir_name(d) and not d.startswith(".")]
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

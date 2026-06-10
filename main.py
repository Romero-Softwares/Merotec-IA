import json
import locale
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import queue
import time
import textwrap
import urllib.error
import urllib.request
import webbrowser
import ctypes
import unicodedata
import difflib
from collections import Counter
from datetime import datetime
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


def _bootstrap_tcl_paths():
    python_home = Path(sys.base_prefix)
    dlls_dir = python_home / "DLLs"
    tcl_root = python_home / "tcl"
    project_root = Path(__file__).resolve().parent
    local_tcl_root = project_root / "tcl_runtime"

    if sys.platform == "win32" and tcl_root.exists():
        try:
            local_init = local_tcl_root / "tcl8.6" / "init.tcl"
            if not local_init.exists():
                shutil.copytree(tcl_root, local_tcl_root, dirs_exist_ok=True)
        except OSError:
            pass

    if (local_tcl_root / "tcl8.6" / "init.tcl").exists():
        try:
            os.chdir(project_root)
        except OSError:
            pass
        tcl_library = local_tcl_root / "tcl8.6"
        tk_library = local_tcl_root / "tk8.6"
        os.environ["TCL_LIBRARY"] = "tcl_runtime/tcl8.6"
        os.environ["TK_LIBRARY"] = "tcl_runtime/tk8.6"
    else:
        tcl_library = tcl_root / "tcl8.6"
        tk_library = tcl_root / "tk8.6"

    if tcl_library.exists():
        os.environ.setdefault("TCL_LIBRARY", str(tcl_library))
    if tk_library.exists():
        os.environ.setdefault("TK_LIBRARY", str(tk_library))

    if dlls_dir.exists() and hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(dlls_dir))
        except OSError:
            pass

    tcl_dll = dlls_dir / "tcl86t.dll"
    if tcl_dll.exists():
        try:
            tcl = ctypes.WinDLL(str(tcl_dll))
            tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
            tcl.Tcl_FindExecutable(str(Path(sys.executable)).encode())
        except Exception:
            pass


_bootstrap_tcl_paths()

import customtkinter as ctk
import pygments
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageGrab, ImageTk
from pygments.lexers import get_lexer_for_filename
from pygments.styles import get_style_by_name

from modules.engine import UniversalEngine
from modules.executor import CodeExecutor
from modules.project_manager import ProjectManager
from modules.voice import VoiceModule


APP_NAME = "Merotec IA IDE"
CHAT_TAB_NAME = "Chat AI"
CORE_TABS = {CHAT_TAB_NAME, "Chat IA", "Scratchpad", "Terminal Local", "Log do Agente"}
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "projects"
APP_SETTINGS_FILE = PROJECT_ROOT / "ide_settings.json"
APP_HISTORY_FILE = PROJECT_ROOT / "history.json"
APP_CHANGE_HISTORY_FILE = PROJECT_ROOT / "change_history.json"
DEFAULT_APP_SETTINGS = {
    "last_workspace": "",
    "recent_projects": [],
    "ai_provider": "codex",
    "codex_model_name": "",
    "codex_reasoning_effort": "xhigh",
}
SCRATCHPAD_DEFAULT_TEXT = """# Como configurar um modelo de IA nesta IDE
#
# Motor principal:
# - Provedor: codex
# - Usa o Codex local ja logado no Windows.
# - A IDE usa apenas o Codex como agente principal.
#
# Opcao OpenAI:
# 1. Crie uma chave em: https://platform.openai.com/api-keys
# 2. Configure as variaveis no PowerShell:
#    setx AI_PROVIDER "openai"
#    setx OPENAI_API_KEY "cole_sua_chave_aqui"
#    setx OPENAI_MODEL_NAME "gpt-5.2"
# 3. Feche e abra a IDE novamente.
#
# Opcao Google:
# 1. Configure sua chave do Google GenAI:
#    setx AI_PROVIDER "google"
#    setx GOOGLE_API_KEY "cole_sua_chave_aqui"
#    setx GOOGLE_MODEL_NAME "gemini-3.1-flash-lite"
# 2. Feche e abra a IDE novamente.
#
# Observacoes:
# - Nao cole sua chave no chat.
# - Se aparecer invalid_api_key, gere uma nova chave e copie completa.
# - Se aparecer insufficient_quota, verifique Billing, Usage e Limits da plataforma.
# - Depois de configurar, use a aba Chat AI para conversar com o modelo.

"""
IGNORED_DIRS = {
    ".git",
    ".gradle",
    ".gemini",
    ".idea",
    ".dart_tool",
    ".merotec_attachments",
    ".merotec_backups",
    ".tool_appdata",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "codex_schema_probe",
    "coverage",
    "dist",
    "ephemeral",
    "node_modules",
    "out",
    "venv",
}
IGNORED_SUFFIXES = {
    ".bin",
    ".dll",
    ".exe",
    ".ico",
    ".jpg",
    ".jpeg",
    ".pyd",
    ".pyc",
    ".png",
    ".webp",
    ".zip",
}

FILE_ICON_COLORS = {
    ".py": ("#3776ab", "#ffd343"),
    ".js": ("#f0db4f", "#1f2328"),
    ".ts": ("#3178c6", "#ffffff"),
    ".tsx": ("#3178c6", "#61dafb"),
    ".jsx": ("#1f2937", "#61dafb"),
    ".html": ("#e44d26", "#f7f7f7"),
    ".css": ("#264de4", "#f7f7f7"),
    ".json": ("#d6b656", "#1f2328"),
    ".md": ("#6f7785", "#ffffff"),
    ".txt": ("#8ea0b8", "#ffffff"),
    ".cmd": ("#2fbf71", "#06120c"),
    ".ps1": ("#3a7bd5", "#ffffff"),
    ".bat": ("#2fbf71", "#06120c"),
}

THEME = {
    "bg": "#070a12",
    "panel": "#0d1422",
    "panel_alt": "#111a2b",
    "panel_soft": "#172338",
    "menu_bg": "#101b2d",
    "menu_top": "#182944",
    "menu_bottom": "#0b1424",
    "menu_active": "#1f5f86",
    "menu_border": "#2fbbff",
    "button_top": "#16253c",
    "button_shadow": "#02040a",
    "border": "#243653",
    "border_lift": "#2fbbff",
    "text": "#b9c8d8",
    "muted": "#70839e",
    "button_text": "#c2cfdd",
    "explorer_text": "#aebdd0",
    "accent": "#24d7ff",
    "accent_dark": "#137ca7",
    "success": "#35f6a2",
    "danger": "#ff5f7e",
    "warning": "#ffd166",
    "terminal": "#030711",
}


class UniversalApp(ctk.CTk):
    def __init__(self):
        try:
            os.chdir(PROJECT_ROOT)
        except OSError:
            pass
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME} - IA Engineering Workspace")
        self.geometry("1280x780")
        self.minsize(1050, 660)
        self.configure(fg_color=THEME["bg"])
        self.protocol("WM_DELETE_WINDOW", self.request_app_close)

        self.settings_file = APP_SETTINGS_FILE
        self.history_file = APP_HISTORY_FILE
        self.change_history_file = APP_CHANGE_HISTORY_FILE
        self.settings = self._load_settings()
        self.change_history = self._load_change_history()
        self._apply_settings_to_environment()
        self.current_workspace = str(self._initial_workspace())
        os.chdir(self.current_workspace)
        self.open_editors = {}
        self.path_to_tab = {}
        self.last_response = ""
        self.last_failed_ai_task = None
        self.active_ai_objective = None
        self.command_failure_signatures = {}
        self.ai_read_history = {}
        self.ai_search_history = {}
        self.ai_task_metrics = {}
        self.ai_passive_action_count = 0
        self.max_ai_passive_actions = 18
        self.max_read_requests_per_batch = 16
        self.max_read_files_per_turn = 4
        self.chat_message_frames = []
        self.max_chat_messages = 80
        self.max_textbox_lines = 1200
        self.tk_callback_error_count = 0
        self.last_tk_callback_error = ""
        self.reporting_callback_error = False
        self.streaming_textbox = None
        self.streaming_text = ""
        self.ai_live_trace = ""
        self.ai_live_trace_task_id = None
        self.ai_live_trace_started_at = 0
        self.ai_live_trace_last_log_at = 0
        self.ai_live_trace_last_length = 0
        self.ai_live_trace_lock = threading.Lock()
        self.agent_busy = False
        self.ai_work_count = 0
        self.ai_work_lock = threading.Lock()
        self.ai_busy_started_at = None
        self.ai_heartbeat_running = False
        self.ai_activity_text = "IA trabalhando"
        self.ai_activity_step = 0
        self.current_task_id = 0
        self.cancelled_task_ids = set()
        self.codex_setup_started = False
        self.codex_login_started = False
        self.pending_image_path = None
        self.pending_image_preview = None
        self.chat_background_path = None
        self.chat_background_source = None
        self.chat_background_image = None
        self.chat_background_photo = None
        self.chat_background_canvas_item = None
        self.chat_background_label = None
        self.chat_background_window_item = None
        self.chat_background_size = None
        self.chat_background_refresh_pending = False
        self.voice_capture_active = False
        self.voice_capture_started_at = None
        self.voice_keyword_capture_active = False
        self.voice_keyword_start = "merotec"
        self.voice_keyword_end = "ok"
        self.voice_keyword_listener_enabled = True
        self.terminal_progress_active = False
        self.terminal_work_count = 0
        self.terminal_work_lock = threading.Lock()
        self.terminal_activity_generation = 0
        self.active_terminal_processes = {}
        self.active_process_lock = threading.Lock()
        self.explorer_visible = True
        self.sidebar_width = 228
        self.explorer_width = 270

        self.engine = UniversalEngine()
        self.voice = VoiceModule()
        self.pm = ProjectManager(str(DEFAULT_WORKSPACE))
        self.executor = CodeExecutor()

        self.style = get_style_by_name("monokai")

        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self.load_workspace_files()
        self.set_status("Pronto para trabalhar.", "ready")
        self.after(900, self.ensure_codex_ready)
        self.after(1400, self.start_voice_keyword_listener)

    def _build_menu(self):
        self._configure_native_menu_style()
        self.menu = self._native_menu()

        file_menu = self._native_menu()
        file_menu.add_command(label="Abrir projeto/pasta...", command=self.open_project)
        self.recent_menu = self._native_menu()
        file_menu.add_cascade(label="Projetos recentes", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Salvar arquivo atual", accelerator="Ctrl+S", command=self.save_current_tab)
        file_menu.add_command(label="Anexar arquivo...", command=self.upload_and_update_code)
        file_menu.add_separator()
        file_menu.add_command(label="Sair", command=self.request_app_close)
        self.menu.add_cascade(label="Arquivo", menu=file_menu)

        view_menu = self._native_menu()
        view_menu.add_command(label="Mostrar/ocultar explorer", accelerator="Ctrl+B", command=self.toggle_explorer)
        view_menu.add_command(label="Atualizar explorer", accelerator="F5", command=self.load_workspace_files)
        self.menu.add_cascade(label="Visualizar", menu=view_menu)

        editor_menu = self._native_menu()
        editor_menu.add_command(label="Fechar aba atual", accelerator="Ctrl+W", command=self.close_current_tab)
        editor_menu.add_command(label="Executar Python atual", accelerator="Ctrl+R", command=self.run_current_python_file)
        self.menu.add_cascade(label="Editor", menu=editor_menu)

        self.visual_menus = [file_menu, view_menu, editor_menu]

        self.update_recent_menu()
        self._build_visual_menu_bar()

    def _build_visual_menu_bar(self):
        self.visual_menu_bar = ctk.CTkFrame(
            self,
            height=30,
            fg_color=THEME["menu_bottom"],
            border_color=THEME["menu_border"],
            border_width=1,
            corner_radius=0,
        )
        self.visual_menu_bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.visual_menu_bar.grid_columnconfigure(3, weight=1)

        menu_items = [
            ("Arquivo", 0),
            ("Visualizar", 1),
            ("Editor", 2),
        ]
        for label, index in menu_items:
            button = ctk.CTkButton(
                self.visual_menu_bar,
                text=label,
                width=82,
                height=22,
                fg_color=THEME["menu_top"],
                hover_color=THEME["menu_active"],
                border_color=THEME["menu_border"],
                border_width=1,
                corner_radius=5,
                text_color=THEME["text"],
                font=("Segoe UI", 11, "bold"),
                command=lambda menu_index=index: self._show_visual_menu(menu_index),
            )
            button.grid(row=0, column=index, padx=(8 if index == 0 else 3, 3), pady=3)

        ctk.CTkLabel(
            self.visual_menu_bar,
            text="Merotec IA",
            text_color=THEME["muted"],
            font=("Segoe UI", 10),
            height=18,
        ).grid(row=0, column=3, sticky="e", padx=(8, 14), pady=(4, 5))

    def _show_visual_menu(self, index, widget=None):
        menus = getattr(self, "visual_menus", [])
        if index >= len(menus):
            return
        if widget is None:
            widget = self.visual_menu_bar.grid_slaves(row=0, column=index)[0]
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height()
        menu = menus[index]
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _native_menu(self):
        import tkinter as tk

        return tk.Menu(
            self,
            tearoff=0,
            background=THEME["menu_bg"],
            foreground=THEME["text"],
            activebackground=THEME["menu_active"],
            activeforeground=THEME["accent"],
            disabledforeground=THEME["muted"],
            borderwidth=0,
            relief="flat",
            activeborderwidth=0,
            font=("Segoe UI", 10),
        )

    def _configure_native_menu_style(self):
        menu_options = {
            "*Menu.background": THEME["menu_bg"],
            "*Menu.foreground": THEME["text"],
            "*Menu.activeBackground": THEME["menu_active"],
            "*Menu.activeForeground": THEME["accent"],
            "*Menu.disabledForeground": THEME["muted"],
            "*Menu.borderWidth": 0,
            "*Menu.activeBorderWidth": 0,
            "*Menu.relief": "flat",
        }
        for option, value in menu_options.items():
            self.option_add(option, value)

    def _section_bar(self, parent, *, height=42, corner_radius=6):
        return ctk.CTkFrame(
            parent,
            fg_color=THEME["panel_alt"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=corner_radius,
            height=height,
        )

    def _elevated_button(self, parent, **kwargs):
        shadow = ctk.CTkFrame(parent, fg_color=THEME["button_shadow"], corner_radius=8)
        shadow.grid_columnconfigure(0, weight=1)
        shadow.grid_rowconfigure(0, weight=1)

        defaults = {
            "fg_color": THEME["button_top"],
            "hover_color": "#343c4b",
            "border_color": THEME["border_lift"],
            "border_width": 1,
            "corner_radius": 7,
            "text_color": THEME["button_text"],
        }
        defaults.update(kwargs)
        button = ctk.CTkButton(shadow, **defaults)
        button.grid(row=0, column=0, sticky="nsew", padx=(0, 1), pady=(0, 4))
        button.elevation_shadow = shadow
        return button

    def _safe_ui_command(self, label, command):
        def run():
            try:
                command()
            except Exception as exc:
                self.set_status(f"Erro em {label}.", "error")
                self.add_chat_message("Erro", f"Falha ao executar {label}: {exc}")
                self.log_agent(f"Erro no botao {label}: {exc}")

        return run

    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=self.sidebar_width)
        self.grid_columnconfigure(1, minsize=self.explorer_width)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, minsize=38)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, minsize=96)
        self.grid_rowconfigure(3, minsize=30)

        self._build_sidebar()
        self._build_explorer()
        self._build_main_tabs()
        self._build_input_bar()
        self._build_status_bar()

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self,
            width=228,
            fg_color=THEME["panel"],
            border_color="#173a5c",
            border_width=1,
            corner_radius=0,
        )
        self.sidebar.grid(row=1, column=0, rowspan=3, sticky="nsew")
        self.sidebar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.sidebar,
            text="MEROTEC IA",
            font=("Segoe UI", 23, "bold"),
            text_color=THEME["accent"],
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 2))

        ctk.CTkLabel(
            self.sidebar,
            text="IDE autônoma",
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))

        self.workspace_label = ctk.CTkLabel(
            self.sidebar,
            text=self._workspace_title(),
            font=("Segoe UI", 12, "bold"),
            text_color=THEME["accent"],
            wraplength=180,
            justify="left",
        )
        self.workspace_label.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))

        self.ai_status_label = ctk.CTkLabel(
            self.sidebar,
            text=self.engine.status_text(),
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
            wraplength=180,
            justify="left",
        )
        self.ai_status_label.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))

        buttons = [
            ("Abrir Projeto", self.open_project),
            ("Configurar IA", self.configure_ai),
            ("Entrar Codex", self.launch_codex_login),
            ("Atualizar Explorer", self.load_workspace_files),
            ("Anexar Arquivo", self.upload_and_update_code),
            ("Comando por Voz", self.voice_command),
        ]

        self.sidebar_buttons = {}
        for row, (label, command) in enumerate(buttons, start=4):
            button = self._elevated_button(
                self.sidebar,
                text=label,
                command=self._safe_ui_command(label, command),
                height=38,
                anchor="w",
                font=("Segoe UI", 13, "bold"),
            )
            button.elevation_shadow.grid(row=row, column=0, sticky="ew", padx=18, pady=5)
            self.sidebar_buttons[label] = button

        ctk.CTkLabel(
            self.sidebar,
            text="Audio",
            font=("Segoe UI", 12, "bold"),
            text_color=THEME["muted"],
        ).grid(row=10, column=0, sticky="w", padx=18, pady=(14, 6))

        audio = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        audio.grid(row=11, column=0, sticky="ew", padx=16)
        audio.grid_columnconfigure((0, 1, 2), weight=1)

        listen_button = self._elevated_button(audio, text="Ouvir", width=58, height=32, command=self.play_last_response)
        pause_button = self._elevated_button(audio, text="Pausar", width=58, height=32, command=self.voice.pause)
        resume_button = self._elevated_button(audio, text="Voltar", width=58, height=32, command=self.voice.resume)
        listen_button.elevation_shadow.grid(row=0, column=0, sticky="ew", padx=2)
        pause_button.elevation_shadow.grid(row=0, column=1, sticky="ew", padx=2)
        resume_button.elevation_shadow.grid(row=0, column=2, sticky="ew", padx=2)

        self.sidebar.grid_rowconfigure(12, weight=1)
        self.agent_summary = ctk.CTkTextbox(
            self.sidebar,
            fg_color=THEME["panel_alt"],
            text_color=THEME["muted"],
            font=("Consolas", 11),
            corner_radius=6,
            height=170,
            wrap="word",
        )
        self.agent_summary.grid(row=13, column=0, sticky="ew", padx=18, pady=(10, 18))
        self._show_ctk_textbox_scrollbar(self.agent_summary)
        self._replace_text(self.agent_summary, "Acoes do agente aparecem aqui.")

    def _build_explorer(self):
        self.explorer = ctk.CTkFrame(self, fg_color=THEME["panel_alt"], corner_radius=0)
        self.explorer.grid(row=1, column=1, rowspan=3, sticky="nsew", padx=(1, 0))
        self.explorer.grid_columnconfigure(0, weight=1)
        self.explorer.grid_rowconfigure(2, weight=1)
        self._configure_explorer_style()
        self._build_file_icons()

        ctk.CTkLabel(
            self.explorer,
            text="EXPLORER",
            font=("Segoe UI", 12, "bold"),
            text_color="#687b96",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))

        self.explorer_filter = ctk.CTkEntry(
            self.explorer,
            placeholder_text="Filtrar arquivos...",
            fg_color=THEME["panel"],
            border_color=THEME["border"],
            text_color=THEME["explorer_text"],
            height=34,
        )
        self.explorer_filter.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.explorer_filter.bind("<KeyRelease>", lambda _event: self.load_workspace_files())

        self.explorer_tree_frame = ctk.CTkFrame(self.explorer, fg_color=THEME["panel_alt"], corner_radius=0)
        self.explorer_tree_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.explorer_tree_frame.grid_columnconfigure(0, weight=1)
        self.explorer_tree_frame.grid_rowconfigure(0, weight=1)

        self.file_tree = ttk.Treeview(
            self.explorer_tree_frame,
            show="tree",
            selectmode="browse",
            style="Explorer.Treeview",
            columns=("path", "kind"),
        )
        self.file_tree.column("#0", width=self.explorer_width - 42, minwidth=160, stretch=True)
        self.file_tree.column("path", width=0, stretch=False)
        self.file_tree.column("kind", width=0, stretch=False)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        self.file_tree.bind("<Double-1>", self._open_selected_tree_item)
        self.file_tree.bind("<Return>", self._open_selected_tree_item)

        tree_scroll = ctk.CTkScrollbar(self.explorer_tree_frame, command=self.file_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.file_tree.configure(yscrollcommand=lambda first, last: self._sync_autohide_scrollbar(tree_scroll, first, last))
        self.after(1, lambda: self._sync_autohide_scrollbar(tree_scroll, *self.file_tree.yview()))

    def _sync_autohide_scrollbar(self, scrollbar, first, last):
        try:
            scrollbar.set(first, last)
            needs_scroll = float(first) > 0.0 or float(last) < 1.0
            if needs_scroll:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
        except (tk.TclError, ValueError):
            pass

    def _autohide_ctk_textbox_scrollbar(self, textbox):
        scrollbar = getattr(textbox, "_y_scrollbar", None)
        if scrollbar is None:
            return
        textbox.configure(yscrollcommand=lambda first, last: self._sync_autohide_scrollbar(scrollbar, first, last))
        self.after(1, lambda: self._sync_autohide_scrollbar(scrollbar, *textbox.yview()))

    def _show_ctk_textbox_scrollbar(self, textbox):
        scrollbar = getattr(textbox, "_y_scrollbar", None)
        if scrollbar is None:
            return
        try:
            scrollbar.grid()
        except tk.TclError:
            pass

    def _autohide_ctk_scrollable_frame_scrollbar(self, scrollable_frame):
        scrollbar = getattr(scrollable_frame, "_scrollbar", None)
        canvas = getattr(scrollable_frame, "_parent_canvas", None)
        if scrollbar is None or canvas is None:
            return
        canvas.configure(yscrollcommand=lambda _first, _last: scrollbar.grid_remove())
        try:
            scrollbar.configure(width=0, fg_color="transparent")
            scrollbar.grid_remove()
        except tk.TclError:
            pass

    def _position_chat_background(self, event=None):
        canvas = getattr(self.chat_history, "_parent_canvas", None)
        item = getattr(self, "chat_background_canvas_item", None)
        if canvas is None or item is None:
            return
        try:
            canvas.tag_lower(item)
        except tk.TclError:
            pass

    def _schedule_chat_background_refresh(self, event=None):
        if self.chat_background_refresh_pending:
            return
        self.chat_background_refresh_pending = True
        self.after(120, self._refresh_chat_background)

    def _build_file_icons(self):
        self.file_icons = {
            "dir": self._make_folder_icon(),
            "file": self._make_file_icon("#5e697a", "#d7dee9"),
        }
        for suffix, (fill, accent) in FILE_ICON_COLORS.items():
            self.file_icons[suffix] = self._make_file_icon(fill, accent)

    def _make_file_icon(self, fill, accent):
        bg = THEME["panel_alt"]
        image = tk.PhotoImage(width=16, height=16)
        image.put(bg, to=(0, 0, 16, 16))
        image.put("#0b0d11", to=(4, 2, 13, 15))
        image.put(fill, to=(3, 1, 12, 14))
        image.put("#ffffff", to=(5, 3, 10, 4))
        image.put("#ffffff", to=(5, 6, 11, 7))
        image.put(accent, to=(5, 10, 10, 12))
        image.put("#18202b", to=(10, 1, 12, 3))
        return image

    def _make_folder_icon(self):
        bg = THEME["panel_alt"]
        image = tk.PhotoImage(width=16, height=16)
        image.put(bg, to=(0, 0, 16, 16))
        image.put("#0b0d11", to=(2, 5, 15, 14))
        image.put("#b98520", to=(2, 4, 7, 6))
        image.put("#d7a13a", to=(1, 6, 14, 13))
        image.put("#f0bd55", to=(1, 7, 14, 12))
        return image

    def _file_tree_icon(self, path, kind):
        if kind == "dir":
            return self.file_icons.get("dir")
        suffix = Path(path).suffix.lower()
        return self.file_icons.get(suffix, self.file_icons.get("file"))

    def _configure_explorer_style(self):
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "Explorer.Treeview",
            background=THEME["panel_alt"],
            fieldbackground=THEME["panel_alt"],
            foreground=THEME["explorer_text"],
            borderwidth=0,
            rowheight=24,
            font=("Segoe UI", 10),
        )
        style.map(
            "Explorer.Treeview",
            background=[("selected", THEME["accent_dark"])],
            foreground=[("selected", THEME["button_text"])],
        )

    def _find_chat_background_image(self):
        image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        preferred_dirs = [PROJECT_ROOT / "access", PROJECT_ROOT / "assets"]
        for folder in preferred_dirs:
            if not folder.exists():
                continue
            images = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in image_extensions]
            if images:
                return max(images, key=lambda path: path.stat().st_mtime)
        return None

    def _make_chat_effect_background(self, width, height):
        width = max(240, int(width))
        height = max(180, int(height))
        base = Image.new("RGB", (1, height), THEME["bg"])
        draw_base = ImageDraw.Draw(base)

        for y in range(height):
            vertical = y / max(1, height - 1)
            draw_base.point(
                (0, y),
                fill=(
                    int(7 + 12 * vertical),
                    int(12 + 18 * vertical),
                    int(24 + 34 * vertical),
                ),
            )

        background = base.resize((width, height))
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        cyan = (47, 187, 255, 42)
        blue = (66, 120, 255, 24)
        soft = (120, 220, 255, 16)

        draw.ellipse((int(width * 0.58), -height // 4, int(width * 1.18), int(height * 0.72)), fill=(24, 90, 150, 38))
        draw.ellipse((-width // 4, int(height * 0.66), int(width * 0.62), int(height * 1.28)), fill=(20, 70, 115, 28))

        for offset in range(-height, width, 96):
            draw.line((offset, height, offset + height, 0), fill=blue, width=1)
        for offset in range(28, width + height, 128):
            draw.line((offset, 0, offset - height, height), fill=(31, 95, 134, 18), width=1)

        nodes = [
            (0.18, 0.28), (0.31, 0.18), (0.45, 0.35), (0.62, 0.22), (0.79, 0.31),
            (0.24, 0.57), (0.40, 0.70), (0.58, 0.58), (0.74, 0.74), (0.88, 0.52),
        ]
        points = [(int(width * x), int(height * y)) for x, y in nodes]
        for start, end in zip(points, points[1:]):
            draw.line((*start, *end), fill=cyan, width=1)
        for x, y in points:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(47, 187, 255, 82), outline=(180, 238, 255, 110))
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline=soft, width=1)

        for radius, alpha in ((180, 18), (260, 12), (360, 7)):
            draw.ellipse((width - radius, 18, width + radius // 2, 18 + radius * 2), outline=(47, 187, 255, alpha), width=1)

        return Image.alpha_composite(background.convert("RGBA"), layer).convert("RGB")

    def _refresh_chat_background(self, event=None):
        self.chat_background_refresh_pending = False
        try:
            canvas = getattr(self.chat_history, "_parent_canvas", None)
            target_widget = canvas if canvas is not None else self.tab_chat
            width = max(240, target_widget.winfo_width())
            height = max(180, target_widget.winfo_height())
            width = (width // 16) * 16
            height = (height // 16) * 16
            if self.chat_background_size == (width, height):
                self._position_chat_background()
                return

            self.chat_background_size = (width, height)
            background = self._make_chat_effect_background(width, height)

            if canvas is not None:
                self.chat_background_photo = ImageTk.PhotoImage(background)
                if self.chat_background_canvas_item is None:
                    self.chat_background_canvas_item = canvas.create_image(
                        0,
                        0,
                        image=self.chat_background_photo,
                        anchor="nw",
                    )
                else:
                    canvas.itemconfigure(self.chat_background_canvas_item, image=self.chat_background_photo)
                canvas.coords(self.chat_background_canvas_item, 0, 0)
                canvas.tag_lower(self.chat_background_canvas_item)
                canvas.configure(bg=THEME["bg"], highlightthickness=0, bd=0)

            self.chat_background_image = ctk.CTkImage(
                light_image=background,
                dark_image=background,
                size=(width, height),
            )
            if self.chat_background_label is not None:
                self.chat_background_label.configure(image=self.chat_background_image)
                self.chat_background_label.place_configure(relx=0, rely=0, relwidth=1, relheight=1)
                self.chat_background_label.lower()
        except Exception as exc:
            self.log_agent(f"Nao consegui aplicar fundo do chat: {exc}")

    def _style_chat_background_layers(self):
        try:
            self.chat_history.configure(fg_color="transparent")
            canvas = getattr(self.chat_history, "_parent_canvas", None)
            if canvas is not None:
                canvas.configure(bg=THEME["bg"], highlightthickness=0, bd=0)
                canvas.bind("<Configure>", self._position_chat_background, add="+")
            parent_frame = getattr(self.chat_history, "_parent_frame", None)
            if parent_frame is not None:
                parent_frame.configure(fg_color="transparent")
            scrollbar = getattr(self.chat_history, "_scrollbar", None)
            if scrollbar is not None:
                scrollbar.configure(width=0, fg_color="transparent")
                scrollbar.grid_remove()
        except tk.TclError:
            pass

    def _build_main_tabs(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=THEME["panel"],
            text_color=THEME["text"],
            border_width=1,
            border_color=THEME["border"],
            segmented_button_fg_color=THEME["panel_alt"],
            segmented_button_selected_color=THEME["accent_dark"],
            segmented_button_selected_hover_color=THEME["accent"],
            segmented_button_unselected_color=THEME["panel_alt"],
            segmented_button_unselected_hover_color=THEME["panel_soft"],
        )
        self.tabview.grid(row=1, column=2, sticky="nsew", padx=12, pady=(12, 8))

        self.tab_chat = self.tabview.add("Chat AI")
        self.tab_editor = self.tabview.add("Scratchpad")
        self.tab_terminal = self.tabview.add("Terminal Local")
        self.tab_agent_log = self.tabview.add("Log do Agente")

        for tab in (self.tab_chat, self.tab_editor, self.tab_terminal, self.tab_agent_log):
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)

        self.chat_background_label = ctk.CTkLabel(self.tab_chat, text="", fg_color=THEME["panel"])
        self.chat_background_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.chat_background_label.lower()

        self.chat_history = ctk.CTkScrollableFrame(self.tab_chat, fg_color="transparent")
        self.chat_history.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.chat_history.tkraise()
        self._style_chat_background_layers()
        self._autohide_ctk_scrollable_frame_scrollbar(self.chat_history)
        self.chat_history.bind("<Configure>", self._schedule_chat_background_refresh, add="+")
        self.tab_chat.bind("<Configure>", self._schedule_chat_background_refresh, add="+")
        self._refresh_chat_background()
        self.after(120, self._schedule_chat_background_refresh)

        self.code_editor_frame, self.code_editor = self._create_editor(self.tab_editor, "Scratchpad")
        self.code_editor_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.code_editor.insert("1.0", SCRATCHPAD_DEFAULT_TEXT)
        self.open_editors["Scratchpad"] = {"widget": self.code_editor, "path": None, "dirty": False}
        self.code_editor.bind("<KeyRelease>", lambda event: self._on_editor_key(event, "Scratchpad"), add="+")
        self.update_editor_markers("Scratchpad")

        self.local_term_out = ctk.CTkTextbox(
            self.tab_terminal,
            fg_color=THEME["terminal"],
            text_color="#62f28f",
            font=("Consolas", 13),
            wrap="word",
        )
        self.local_term_out.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._autohide_ctk_textbox_scrollbar(self.local_term_out)
        self._bind_terminal_interrupt_shortcuts(self.local_term_out)

        self.terminal_activity_frame = ctk.CTkFrame(
            self.tab_terminal,
            fg_color=THEME["panel_alt"],
            corner_radius=6,
        )
        self.terminal_activity_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 4))
        self.terminal_activity_frame.grid_columnconfigure(0, weight=1)
        self.terminal_activity_label = ctk.CTkLabel(
            self.terminal_activity_frame,
            text="Terminal executando...",
            font=("Segoe UI", 11, "bold"),
            text_color=THEME["warning"],
            anchor="w",
        )
        self.terminal_activity_label.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        self.terminal_activity_bar = ctk.CTkProgressBar(
            self.terminal_activity_frame,
            mode="indeterminate",
            height=4,
            progress_color=THEME["warning"],
            fg_color=THEME["border"],
        )
        self.terminal_activity_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(3, 6))
        self.terminal_activity_frame.grid_remove()

        self.local_term_in = ctk.CTkEntry(
            self.tab_terminal,
            placeholder_text="Digite um comando local...",
            font=("Consolas", 13),
            fg_color=THEME["panel_alt"],
            border_color=THEME["border"],
            text_color=THEME["text"],
            height=38,
        )
        self.local_term_in.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        self.local_term_in.bind("<Return>", self.run_local_command)
        self._bind_terminal_interrupt_shortcuts(self.local_term_in)

        self.agent_log = ctk.CTkTextbox(
            self.tab_agent_log,
            fg_color=THEME["terminal"],
            text_color="#d7dee9",
            font=("Consolas", 12),
            wrap="word",
        )
        self.agent_log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._show_ctk_textbox_scrollbar(self.agent_log)
        self._replace_text(self.agent_log, "Log iniciado.\n")

    def _build_input_bar(self):
        self.input_frame = ctk.CTkFrame(self, fg_color=THEME["panel"], corner_radius=0)
        self.input_frame.grid(row=2, column=2, sticky="ew", padx=12, pady=(0, 8))
        self.input_frame.grid_columnconfigure(0, weight=1)

        self.attachment_frame = ctk.CTkFrame(self.input_frame, fg_color=THEME["panel_alt"], corner_radius=6)
        self.attachment_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))
        self.attachment_frame.grid_columnconfigure(1, weight=1)
        self.attachment_frame.grid_remove()

        self.attachment_preview = ctk.CTkLabel(self.attachment_frame, text="", width=54, height=42)
        self.attachment_preview.grid(row=0, column=0, padx=(8, 6), pady=6)
        self.attachment_label = ctk.CTkLabel(
            self.attachment_frame,
            text="",
            text_color=THEME["text"],
            anchor="w",
        )
        self.attachment_label.grid(row=0, column=1, sticky="ew", padx=6)
        remove_button = self._elevated_button(
            self.attachment_frame,
            text="Remover",
            width=82,
            height=28,
            command=self.clear_pending_image,
        )
        remove_button.elevation_shadow.grid(row=0, column=2, padx=8, pady=6)

        self.text_input = ctk.CTkTextbox(
            self.input_frame,
            height=64,
            fg_color=THEME["panel_alt"],
            border_color=THEME["border"],
            border_width=1,
            text_color=THEME["text"],
            font=("Segoe UI", 14),
            wrap="word",
        )
        self.text_input.grid(row=1, column=0, sticky="ew", padx=(10, 8), pady=10)
        self._autohide_ctk_textbox_scrollbar(self.text_input)
        self.text_input.insert("1.0", "")
        self.text_input.bind("<Control-Return>", lambda _event: self.text_command())
        self.text_input.bind("<Control-v>", self.paste_into_chat)
        self.text_input.bind("<Control-V>", self.paste_into_chat)

        actions = ctk.CTkFrame(self.input_frame, fg_color="transparent")
        actions.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=10)

        self.btn_send = self._elevated_button(
            actions,
            text="Enviar",
            width=110,
            height=30,
            command=self.text_command,
            font=("Segoe UI", 13, "bold"),
            fg_color=THEME["accent"],
            hover_color=THEME["accent_dark"],
            border_color="#7cc7ff",
            text_color="#06111d",
        )
        self.btn_send.elevation_shadow.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        clear_button = self._elevated_button(
            actions,
            text="Limpar",
            width=110,
            height=30,
            command=lambda: self._replace_text(self.text_input, ""),
        )
        clear_button.elevation_shadow.grid(row=1, column=0, sticky="ew")

        self.ai_progress = ctk.CTkProgressBar(
            self.input_frame,
            orientation="horizontal",
            height=4,
            mode="indeterminate",
            progress_color=THEME["accent"],
        )
        self.ai_progress.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.ai_progress.grid_remove()

    def _build_status_bar(self):
        self.status_bar = ctk.CTkFrame(self, fg_color=THEME["panel_alt"], height=30, corner_radius=0)
        self.status_bar.grid(row=3, column=2, sticky="ew", padx=12, pady=(0, 12))
        self.status_bar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="",
            font=("Segoe UI", 12),
            text_color=THEME["muted"],
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=10)

    def _bind_shortcuts(self):
        self.bind_all("<Control-s>", self.save_current_tab)
        self.bind_all("<Control-S>", self.save_current_tab)
        self.bind_all("<Control-w>", self.close_current_tab)
        self.bind_all("<Control-W>", self.close_current_tab)
        self.bind_all("<Control-b>", lambda _event: self.toggle_explorer())
        self.bind_all("<Control-B>", lambda _event: self.toggle_explorer())
        self.bind_all("<Control-r>", self.run_current_python_file)
        self.bind_all("<Control-R>", self.run_current_python_file)
        self.bind_all("<F5>", lambda _event: self.load_workspace_files())

    def _bind_terminal_interrupt_shortcuts(self, widget):
        for sequence in ("<Control-c>", "<Control-C>", "<Control-Break>", "<Break>", "<Cancel>"):
            try:
                widget.bind(sequence, self.interrupt_terminal_from_keyboard, add="+")
            except tk.TclError:
                pass

    def interrupt_terminal_from_keyboard(self, event=None):
        if not self.agent_busy and not self.has_terminal_processes():
            return None

        self.append_to_term("\n^C\n")
        self.cancel_ai_task()
        self.set_status("Interrompido pelo Ctrl+C.", "warning")
        return "break"

    def _create_editor(self, parent, tab_name):
        editor_frame = ctk.CTkFrame(parent, fg_color="#16181d", border_width=0, corner_radius=0)
        editor_frame.grid_columnconfigure(1, weight=1)
        editor_frame.grid_rowconfigure(0, weight=1)

        line_numbers = tk.Text(
            editor_frame,
            width=5,
            padx=6,
            pady=8,
            bd=0,
            highlightthickness=0,
            bg="#111318",
            fg=THEME["muted"],
            insertwidth=0,
            takefocus=False,
            font=("Consolas", 14),
            wrap="none",
            state="disabled",
        )
        line_numbers.grid(row=0, column=0, sticky="ns")
        line_numbers.tag_configure("right", justify="right")
        line_numbers.tag_configure("current", background="#242832", foreground=THEME["text"])

        editor = ctk.CTkTextbox(
            editor_frame,
            fg_color="#16181d",
            text_color=THEME["text"],
            font=("Consolas", 14),
            border_width=0,
            wrap="none",
            undo=True,
        )
        editor.grid(row=0, column=1, sticky="nsew")
        self._autohide_ctk_textbox_scrollbar(editor)
        editor._line_numbers = line_numbers
        editor.tag_config("current_line", background="#20242c")

        def sync_scroll(first, last):
            line_numbers.yview_moveto(first)
            try:
                self._sync_autohide_scrollbar(editor._y_scrollbar, first, last)
            except AttributeError:
                pass

        editor.configure(yscrollcommand=sync_scroll)
        editor.bind("<KeyRelease>", lambda _event, name=tab_name: self._on_editor_content_changed(name), add="+")
        editor.bind("<ButtonRelease-1>", lambda _event, name=tab_name: self.update_editor_markers(name), add="+")
        editor.bind("<MouseWheel>", lambda _event, name=tab_name: self.after(1, lambda: self.update_editor_markers(name)), add="+")
        editor.bind("<Configure>", lambda _event, name=tab_name: self.after(1, lambda: self.update_editor_markers(name)), add="+")

        self.after(1, lambda name=tab_name: self.update_editor_markers(name))
        return editor_frame, editor

    def _workspace_title(self):
        return Path(self.current_workspace).name or self.current_workspace

    def _replace_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled" if widget in [getattr(self, "agent_summary", None)] else "normal")

    def paste_into_chat(self, event=None):
        image = self._clipboard_image()
        if image is None:
            return None

        path = None
        try:
            path = self.save_clipboard_image(image)
            self.set_pending_image(path)
            self.set_status("Imagem pronta.", "ready")
        except Exception as exc:
            if path is not None and "pyimage" in str(exc).lower():
                try:
                    self.recreate_attachment_preview()
                    self.set_pending_image(path)
                    self.set_status("Imagem pronta.", "ready")
                    return "break"
                except Exception as retry_exc:
                    exc = retry_exc
            self.add_chat_message("Erro", f"Nao consegui anexar o print: {exc}")
        return "break"

    def _clipboard_image(self):
        try:
            content = ImageGrab.grabclipboard()
        except Exception:
            return None

        if hasattr(content, "save"):
            return content

        if isinstance(content, list):
            for item in content:
                path = Path(item)
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and path.exists():
                    return path
        if isinstance(content, str):
            path = Path(content)
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and path.exists():
                return path
        return None

    def save_clipboard_image(self, image):
        attachments = Path(self.current_workspace) / ".merotec_attachments"
        attachments.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = attachments / f"print_{timestamp}.png"

        if isinstance(image, Path):
            if image.suffix.lower() == ".png":
                shutil.copy2(image, path)
            else:
                Image.open(image).save(path, "PNG")
        else:
            image.save(path, "PNG")
        return path

    def set_pending_image(self, path):
        self.pending_image_path = str(path)
        with Image.open(path) as preview_source:
            preview_source.thumbnail((54, 42))
            preview_image = preview_source.copy()
        self.pending_image_preview = ctk.CTkImage(
            light_image=preview_image,
            dark_image=preview_image,
            size=preview_image.size,
        )
        try:
            self.attachment_preview.configure(image=self.pending_image_preview, text="")
        except tk.TclError:
            self.recreate_attachment_preview()
            self.attachment_preview.configure(image=self.pending_image_preview, text="")
        self.attachment_label.configure(text="")
        self.attachment_frame.grid()

    def recreate_attachment_preview(self):
        try:
            self.attachment_preview.destroy()
        except (AttributeError, tk.TclError):
            pass
        self.attachment_preview = ctk.CTkLabel(self.attachment_frame, text="", width=54, height=42)
        self.attachment_preview.grid(row=0, column=0, padx=(8, 6), pady=6)

    def clear_pending_image(self):
        self.pending_image_path = None
        try:
            self.attachment_preview.configure(image=None, text="")
        except tk.TclError:
            self.recreate_attachment_preview()
        self.pending_image_preview = None
        self.attachment_label.configure(text="")
        self.attachment_frame.grid_remove()

    def report_callback_exception(self, exc, val, tb):
        if self.reporting_callback_error:
            return
        self.tk_callback_error_count += 1
        message = f"{getattr(exc, '__name__', str(exc))}: {val}"
        if message == self.last_tk_callback_error and self.tk_callback_error_count > 3:
            return
        self.last_tk_callback_error = message
        try:
            self.reporting_callback_error = True
            if hasattr(self, "agent_log"):
                self._append_text(self.agent_log, f"[UI] Callback ignorado: {message}\n")
        except Exception:
            pass
        finally:
            self.reporting_callback_error = False

    def safe_chat_scroll_bottom(self):
        try:
            self.chat_history._parent_canvas.yview_moveto(1.0)
            self._position_chat_background()
        except (RecursionError, tk.TclError):
            self.report_callback_exception(RecursionError, "scroll do chat excedeu limite", None)

    def register_chat_frame(self, frame):
        self.chat_message_frames.append(frame)
        while len(self.chat_message_frames) > self.max_chat_messages:
            old_frame = self.chat_message_frames.pop(0)
            try:
                old_frame.destroy()
            except tk.TclError:
                pass

    def set_status(self, text, mode="info"):
        colors = {
            "ready": THEME["success"],
            "busy": THEME["warning"],
            "warning": THEME["warning"],
            "error": THEME["danger"],
            "info": THEME["muted"],
        }

        def update():
            if mode == "ready" and self.agent_busy:
                self.status_label.configure(text="IA trabalhando...", text_color=THEME["warning"])
                return
            self.status_label.configure(text=text, text_color=colors.get(mode, THEME["muted"]))

        self.after(0, update)

    def set_ai_activity(self, text):
        self.ai_activity_text = text or "IA trabalhando"
        if self.agent_busy:
            self.after(0, self.update_ai_heartbeat_status)

    def has_terminal_processes(self):
        with self.active_process_lock:
            active = False
            finished_pids = []
            for pid, item in self.active_terminal_processes.items():
                process = item.get("process")
                if process and process.poll() is None:
                    active = True
                else:
                    finished_pids.append(pid)
            for pid in finished_pids:
                self.active_terminal_processes.pop(pid, None)
            return active

    def update_ai_heartbeat_status(self):
        if not self.agent_busy:
            return
        elapsed = int(time.time() - (self.ai_busy_started_at or time.time()))
        dots = "." * ((self.ai_activity_step % 3) + 1)
        self.ai_activity_step += 1
        self.status_label.configure(
            text=f"{self.ai_activity_text}{dots} {elapsed}s",
            text_color=THEME["warning"],
        )

    def run_ai_heartbeat(self):
        if not self.agent_busy:
            self.ai_heartbeat_running = False
            return
        self.update_ai_heartbeat_status()
        self.after(1000, self.run_ai_heartbeat)

    def set_ai_busy(self, is_busy):
        with self.ai_work_lock:
            if is_busy:
                if self.ai_work_count == 0:
                    self.ai_busy_started_at = time.time()
                    self.ai_activity_text = "IA trabalhando"
                    self.ai_activity_step = 0
                self.ai_work_count += 1
            else:
                self.ai_work_count = max(0, self.ai_work_count - 1)
            self.agent_busy = self.ai_work_count > 0

        def update():
            if self.agent_busy:
                self.ai_progress.grid()
                self.ai_progress.start()
                self.btn_send.configure(state="normal", text="Cancelar")
                self.update_ai_heartbeat_status()
                if not self.ai_heartbeat_running:
                    self.ai_heartbeat_running = True
                    self.after(1000, self.run_ai_heartbeat)
            else:
                self.ai_progress.stop()
                self.ai_progress.grid_remove()
                self.btn_send.configure(state="normal", text="Cancelar" if self.has_terminal_processes() else "Enviar")
                self.ai_busy_started_at = None
                self.ai_activity_text = "IA trabalhando"
                self.set_status("Pronto.", "ready")

        self.after(0, update)

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
        self.workspace_label.configure(text=self._workspace_title())
        self.load_workspace_files()
        self._write_history(self.current_workspace)
        self.add_chat_message("Sistema", f"Projeto aberto: {self.current_workspace}")
        self.log_agent(f"Workspace alterado para {self.current_workspace}")

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

    def load_workspace_files(self):
        self.after(0, self._load_workspace_files_sync)

    def _load_workspace_files_sync(self):
        self.file_tree.delete(*self.file_tree.get_children())

        query = self.explorer_filter.get().strip().lower() if hasattr(self, "explorer_filter") else ""
        workspace = Path(self.current_workspace)
        root_id = self.file_tree.insert(
            "",
            "end",
            text=self._workspace_title(),
            image=self._file_tree_icon(workspace, "dir"),
            values=(str(workspace), "dir"),
            open=True,
        )

        count = [0]
        has_items = self._populate_tree(root_id, workspace, workspace, query, count)
        if not has_items:
            self.file_tree.insert(
                root_id,
                "end",
                text="Nenhum arquivo encontrado.",
                image=self.file_icons.get("file"),
                values=("", "empty"),
            )

    def _populate_tree(self, parent_id, directory, workspace, query, count, limit=1800):
        if count[0] >= limit:
            return False

        try:
            children = sorted(
                directory.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError:
            return False

        inserted_any = False
        for child in children:
            if count[0] >= limit:
                break
            if child.name.startswith("."):
                continue
            if child.is_dir() and child.name in IGNORED_DIRS:
                continue

            try:
                rel_text = child.relative_to(workspace).as_posix().lower()
            except ValueError:
                rel_text = child.name.lower()

            matches_query = not query or query in rel_text

            if child.is_dir():
                node_id = self.file_tree.insert(
                    parent_id,
                    "end",
                    text=child.name,
                    image=self._file_tree_icon(child, "dir"),
                    values=(str(child), "dir"),
                    open=bool(query),
                )
                count[0] += 1
                child_has_items = self._populate_tree(node_id, child, workspace, query, count, limit)
                if query and not matches_query and not child_has_items:
                    self.file_tree.delete(node_id)
                    continue
                inserted_any = True
            elif child.is_file():
                if query and not matches_query:
                    continue
                self.file_tree.insert(
                    parent_id,
                    "end",
                    text=child.name,
                    image=self._file_tree_icon(child, "file"),
                    values=(str(child), "file"),
                )
                count[0] += 1
                inserted_any = True

        return inserted_any

    def _open_selected_tree_item(self, _event=None):
        selection = self.file_tree.selection()
        if not selection:
            return

        item_id = selection[0]
        kind = self.file_tree.set(item_id, "kind")
        path = self.file_tree.set(item_id, "path")

        if kind == "file" and path:
            self.open_file_in_editor(path)
        elif kind == "dir":
            self.file_tree.item(item_id, open=not self.file_tree.item(item_id, "open"))
        return "break"

    def get_workspace_tree(self, limit=220):
        items = []
        for _path, rel in self.iter_workspace_files(limit=limit + 1):
            items.append(rel.as_posix())

        if not items:
            return "O diretorio atual esta vazio."
        if len(items) > limit:
            hidden = len(items) - limit
            return "\n".join(items[:limit]) + f"\n... ({hidden}+ arquivos omitidos)"
        return "\n".join(items)

    def toggle_explorer(self):
        if self.explorer_visible:
            self.explorer.grid_remove()
            self.grid_columnconfigure(1, minsize=0)
        else:
            self.grid_columnconfigure(1, minsize=self.explorer_width)
            self.explorer.grid()
        self.explorer_visible = not self.explorer_visible
        self.update_idletasks()

    def make_tab_name(self, file_path):
        workspace = Path(self.current_workspace)
        path = Path(file_path)
        try:
            return path.relative_to(workspace).as_posix()
        except ValueError:
            return path.name

    def open_file_in_editor(self, file_path):
        path = Path(file_path)
        tab_name = self.path_to_tab.get(str(path.resolve())) or self.make_tab_name(path)

        if tab_name in self.open_editors:
            self.tabview.set(tab_name)
            return

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.add_chat_message("Erro", f"Arquivo nao parece texto UTF-8: {path}")
            return
        except OSError as exc:
            self.add_chat_message("Erro", f"Falha ao abrir arquivo: {exc}")
            return

        tab = self.tabview.add(tab_name)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        top_bar = self._section_bar(tab, height=44, corner_radius=8)
        top_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top_bar.grid_columnconfigure(0, weight=1)
        top_bar.grid_propagate(False)

        ctk.CTkLabel(
            top_bar,
            text=tab_name,
            font=("Segoe UI", 12, "bold"),
            text_color=THEME["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=6)

        actions = ctk.CTkFrame(top_bar, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e", padx=10, pady=6)

        save_button = self._elevated_button(
            actions,
            text="Salvar",
            width=80,
            height=28,
            fg_color=THEME["success"],
            hover_color="#24965a",
            border_color="#63d99b",
            command=lambda p=str(path), t=tab_name: self.save_file(p, t),
        )
        save_button.elevation_shadow.grid(row=0, column=0, padx=4)

        run_button = self._elevated_button(
            actions,
            text="Executar",
            width=80,
            height=28,
            command=lambda t=tab_name: self.run_python_file_from_tab(t),
        )
        run_button.elevation_shadow.grid(row=0, column=1, padx=4)

        close_button = self._elevated_button(
            actions,
            text="Fechar",
            width=80,
            height=28,
            fg_color=THEME["danger"],
            hover_color="#b84b4b",
            border_color="#ee8888",
            command=lambda t=tab_name: self.close_specific_tab(t),
        )
        close_button.elevation_shadow.grid(row=0, column=2, padx=(4, 0))

        editor_frame, editor = self._create_editor(tab, tab_name)
        editor_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        editor.insert("1.0", content)
        editor.bind("<KeyRelease>", lambda event, name=tab_name: self._on_editor_key(event, name), add="+")

        self.open_editors[tab_name] = {"widget": editor, "path": str(path), "dirty": False}
        self.path_to_tab[str(path.resolve())] = tab_name
        self.tabview.set(tab_name)
        self.highlight_code(tab_name)
        self.update_editor_markers(tab_name)
        self.add_chat_message("Sistema", f"Arquivo aberto: {tab_name}")

    def _on_editor_key(self, _event, tab_name):
        if tab_name in self.open_editors:
            self.open_editors[tab_name]["dirty"] = True

    def _on_editor_content_changed(self, tab_name):
        self.highlight_code(tab_name)
        self.update_editor_markers(tab_name)

    def update_editor_markers(self, tab_name):
        info = self.open_editors.get(tab_name)
        if not info:
            return

        editor = info["widget"]
        line_numbers = getattr(editor, "_line_numbers", None)
        if line_numbers is None:
            return

        try:
            line_count = int(editor.index("end-1c").split(".")[0])
            current_line = int(editor.index("insert").split(".")[0])
        except tk.TclError:
            return

        numbers = "\n".join(str(line) for line in range(1, line_count + 1))
        line_numbers.configure(state="normal")
        line_numbers.delete("1.0", "end")
        line_numbers.insert("1.0", numbers)
        line_numbers.tag_add("right", "1.0", "end")
        line_numbers.tag_remove("current", "1.0", "end")
        line_numbers.tag_add("current", f"{current_line}.0", f"{current_line}.end")
        line_numbers.configure(state="disabled")
        line_numbers.yview_moveto(editor.yview()[0])

        editor.tag_remove("current_line", "1.0", "end")
        editor.tag_add("current_line", "insert linestart", "insert lineend+1c")

    def close_current_tab(self, event=None):
        try:
            tab_name = self.tabview.get()
        except (tk.TclError, AttributeError):
            tab_name = CHAT_TAB_NAME
        self.close_specific_tab(tab_name)
        return "break"

    def _tab_exists(self, tab_name):
        try:
            return tab_name in getattr(self.tabview, "_tab_dict", {})
        except (tk.TclError, AttributeError):
            return False

    def _select_safe_tab_after_close(self):
        for fallback_tab in (CHAT_TAB_NAME, "Scratchpad", "Terminal Local", "Log do Agente"):
            if self._tab_exists(fallback_tab):
                try:
                    self.tabview.set(fallback_tab)
                    return True
                except (tk.TclError, ValueError, KeyError):
                    continue
        return False

    def close_specific_tab(self, tab_name):
        if not tab_name or tab_name in CORE_TABS:
            self._select_safe_tab_after_close()
            self.set_status("O chat principal permanece aberto.", "ready")
            return
        if not self._tab_exists(tab_name):
            self._select_safe_tab_after_close()
            self.open_editors.pop(tab_name, None)
            self.set_status(f"Aba ja fechada: {tab_name}", "ready")
            return
        editor_info = self.open_editors.get(tab_name)
        if editor_info and editor_info.get("dirty"):
            if not messagebox.askyesno(APP_NAME, f"Fechar '{tab_name}' sem salvar?"):
                return

        path = editor_info.get("path") if editor_info else None
        if path:
            self.path_to_tab.pop(str(Path(path).resolve()), None)
        self.open_editors.pop(tab_name, None)
        try:
            self.tabview.delete(tab_name)
            self._select_safe_tab_after_close()
        except (tk.TclError, ValueError, KeyError):
            self._select_safe_tab_after_close()
            self.set_status(f"Aba ja fechada: {tab_name}", "ready")
            return
        self.set_status(f"Aba fechada: {tab_name}", "ready")

    def request_app_close(self):
        dirty_tabs = [name for name, info in self.open_editors.items() if info.get("dirty")]
        if dirty_tabs:
            names = ", ".join(dirty_tabs[:4])
            if len(dirty_tabs) > 4:
                names += f" e mais {len(dirty_tabs) - 4}"
            if not messagebox.askyesno(APP_NAME, f"Sair sem salvar as abas alteradas?\n{names}"):
                return
        try:
            self.voice.stop_keyword_listener()
        except Exception:
            pass
        self.cancel_active_terminal_processes()
        self.destroy()

    def save_current_tab(self, event=None):
        current_tab = self.tabview.get()
        info = self.open_editors.get(current_tab)
        if not info:
            return "break"
        path = info.get("path")
        if not path:
            path = filedialog.asksaveasfilename(initialdir=self.current_workspace, defaultextension=".txt")
            if not path:
                return "break"
            info["path"] = path
        self.save_file(path, current_tab)
        return "break"

    def save_file(self, file_path, tab_name):
        info = self.open_editors.get(tab_name)
        if not info:
            return
        content = info["widget"].get("1.0", "end-1c")
        try:
            Path(file_path).write_text(content, encoding="utf-8")
            info["dirty"] = False
            self.path_to_tab[str(Path(file_path).resolve())] = tab_name
            self.set_status(f"Salvo: {tab_name}", "ready")
            self.log_agent(f"Arquivo salvo: {tab_name}")
        except OSError as exc:
            self.add_chat_message("Erro", f"Erro ao salvar: {exc}")

    def run_current_python_file(self, event=None):
        self.run_python_file_from_tab(self.tabview.get())
        return "break"

    def run_python_file_from_tab(self, tab_name):
        info = self.open_editors.get(tab_name)
        if not info:
            return
        path = info.get("path")
        if not path:
            self.add_chat_message("Sistema", "Salve o scratchpad em um arquivo antes de executar.")
            return
        if Path(path).suffix.lower() != ".py":
            self.add_chat_message("Sistema", "Execucao direta esta habilitada para arquivos .py.")
            return

        self.save_file(path, tab_name)
        self.tabview.set("Terminal Local")
        self.append_to_term(f"\n> python {tab_name}\n")
        self.set_terminal_busy(True, f"Executando Python: {tab_name}")

        def execute():
            try:
                ok, output = self.executor.run_python_code(path)
                self.append_to_term(output or ("Processo finalizado sem saida.\n" if ok else "Falha sem saida.\n"))
            finally:
                self.set_terminal_busy(False)

        threading.Thread(target=execute, daemon=True).start()

    def run_local_command(self, event=None):
        command = self.local_term_in.get().strip()
        if not command:
            return
        self.local_term_in.delete(0, "end")
        self.append_to_term(f"\n{self.current_workspace}> {command}\n")
        self.log_agent(f"Comando local executado: {command}")
        self.set_terminal_busy(True, f"Executando comando local: {command[:70]}")

        def execute():
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.current_workspace,
                )
                self.register_terminal_process(process, command)
                self.stream_process_output(process)
                process.wait(timeout=120)
                if process.returncode != 0:
                    self.append_to_term(f"\n[processo finalizado com codigo {process.returncode}]\n")
            except subprocess.TimeoutExpired:
                self.append_to_term("[aviso] comando ainda em execucao ou demorou demais.\n")
            except Exception as exc:
                self.append_to_term(f"[erro] {exc}\n")
            finally:
                if "process" in locals():
                    self.unregister_terminal_process(process)
                self.set_terminal_busy(False)

        threading.Thread(target=execute, daemon=True).start()

    def append_to_term(self, text):
        self.after(0, lambda: self._append_terminal_text(self.local_term_out, text))

    def set_terminal_busy(self, is_busy, label=None):
        with self.terminal_work_lock:
            if is_busy:
                self.terminal_work_count += 1
            else:
                self.terminal_work_count = max(0, self.terminal_work_count - 1)
            active = self.terminal_work_count > 0
            self.terminal_activity_generation += 1
            generation = self.terminal_activity_generation

        def update():
            if generation != self.terminal_activity_generation:
                return
            if active:
                if label:
                    self.terminal_activity_label.configure(text=label)
                self.terminal_activity_frame.grid()
                self.terminal_activity_bar.start()
                self.btn_send.configure(state="normal", text="Cancelar")
            else:
                self.terminal_activity_bar.stop()
                self.terminal_activity_frame.grid_remove()
                if not self.agent_busy:
                    self.btn_send.configure(state="normal", text="Enviar")

        self.after(0, update)

    def register_terminal_process(self, process, label):
        if not process or process.pid is None:
            return
        with self.active_process_lock:
            self.active_terminal_processes[process.pid] = {
                "process": process,
                "label": label or "processo",
            }

    def unregister_terminal_process(self, process):
        if not process or process.pid is None:
            return
        with self.active_process_lock:
            self.active_terminal_processes.pop(process.pid, None)

    def cancel_active_terminal_processes(self):
        with self.active_process_lock:
            processes = list(self.active_terminal_processes.values())
            self.active_terminal_processes.clear()

        killed = 0
        for item in processes:
            process = item.get("process")
            if not process or process.poll() is not None:
                continue
            if self.terminate_process_tree(process):
                killed += 1
        if killed:
            self.reset_terminal_busy()
        return killed

    def reset_terminal_busy(self):
        with self.terminal_work_lock:
            self.terminal_work_count = 0
            self.terminal_activity_generation += 1
            generation = self.terminal_activity_generation

        def update():
            if generation != self.terminal_activity_generation:
                return
            self.terminal_activity_bar.stop()
            self.terminal_activity_frame.grid_remove()
            if not self.agent_busy:
                self.btn_send.configure(state="normal", text="Enviar")

        self.after(0, update)

    def terminate_process_tree(self, process):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=8,
                )
            else:
                process.terminate()
            return True
        except Exception:
            try:
                process.kill()
                return True
            except Exception:
                return False

    def stream_process_output(self, process, collect=False):
        chunks = []
        buffer = bytearray()
        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            buffer.extend(chunk)
            if chunk in {b"\n", b"\r"} or len(buffer) >= 160:
                text = self._decode_process_output(bytes(buffer))
                if collect:
                    chunks.append(text)
                self.append_to_term(text)
                buffer.clear()

        if buffer:
            text = self._decode_process_output(bytes(buffer))
            if collect:
                chunks.append(text)
            self.append_to_term(text)
        return "".join(chunks)

    def _decode_process_output(self, output):
        if isinstance(output, str):
            return output
        if not output:
            return ""

        encodings = []
        if os.name == "nt":
            encodings.append("utf-8-sig")
            try:
                encodings.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
            except Exception:
                pass
            encodings.extend(["mbcs", "cp1252"])
        else:
            encodings.extend([locale.getpreferredencoding(False), "utf-8"])

        for encoding in dict.fromkeys(encodings):
            try:
                return output.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return output.decode("utf-8", errors="replace")

    def log_agent(self, text):
        self.after(0, lambda: self._log_agent_sync(text))

    def _log_agent_sync(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {text}"
        self._append_text(self.agent_log, f"{entry}\n")
        self.agent_summary.configure(state="normal")
        current = self.agent_summary.get("1.0", "end-1c").splitlines()
        current.append(entry)
        self.agent_summary.delete("1.0", "end")
        self.agent_summary.insert("1.0", "\n".join(current[-8:]))
        self.agent_summary.configure(state="disabled")

    def reset_ai_live_trace(self, task_id, objective):
        with self.ai_live_trace_lock:
            self.ai_live_trace = ""
            self.ai_live_trace_task_id = task_id
            self.ai_live_trace_started_at = time.time()
            self.ai_live_trace_last_log_at = 0
            self.ai_live_trace_last_length = 0
        objective_line = self.clean_live_ai_text(objective or "", limit=120)
        self.log_agent(f"IA ao vivo iniciada: {objective_line or 'tarefa em andamento'}")

    def update_ai_live_trace(self, chunk, task_id=None):
        if not chunk:
            return
        with self.ai_live_trace_lock:
            if task_id is not None and self.ai_live_trace_task_id not in {None, task_id}:
                return
            self.ai_live_trace += chunk
            now = time.time()
            current_length = len(self.ai_live_trace)
            should_log = (
                self.ai_live_trace_last_log_at == 0
                or now - self.ai_live_trace_last_log_at >= 1.25
                or current_length - self.ai_live_trace_last_length >= 360
                or bool(re.search(r"\[(READ|WRITE|REPLACE|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST|SEARCH_TEXT|SCAN_TEXT|UNDO)\s*:", chunk, re.IGNORECASE))
            )
            if not should_log:
                self.update_ai_activity_from_stream(chunk)
                return
            self.ai_live_trace_last_log_at = now
            self.ai_live_trace_last_length = current_length
            live_text = self.describe_live_ai_trace(self.ai_live_trace, chunk)

        if live_text:
            self.log_agent(f"IA ao vivo: {live_text}")
        self.update_ai_activity_from_stream(chunk)

    def finish_ai_live_trace(self, task_id=None):
        with self.ai_live_trace_lock:
            if task_id is not None and self.ai_live_trace_task_id not in {None, task_id}:
                return
            elapsed = int(time.time() - self.ai_live_trace_started_at) if self.ai_live_trace_started_at else 0
            had_trace = bool(self.ai_live_trace.strip())
            self.ai_live_trace = ""
            self.ai_live_trace_task_id = None
            self.ai_live_trace_started_at = 0
            self.ai_live_trace_last_log_at = 0
            self.ai_live_trace_last_length = 0
        if had_trace:
            self.log_agent(f"IA ao vivo finalizada em {elapsed}s; resposta recebida pela IDE.")

    def clean_live_ai_text(self, text, limit=260):
        cleaned = self.strip_agent_action_markup(text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > limit:
            cleaned = "..." + cleaned[-limit:]
        return cleaned

    def describe_live_ai_trace(self, trace, chunk):
        action_matches = re.findall(
            r"\[(READ|WRITE|REPLACE|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:\s*([^\]]*)\]",
            trace or "",
            re.IGNORECASE,
        )
        if action_matches:
            action, payload = action_matches[-1]
            payload = self.clean_live_ai_text(payload, limit=140)
            names = {
                "READ": "pediu leitura",
                "WRITE": "pediu escrita",
                "REPLACE": "pediu substituicao",
                "SEARCH_TEXT": "pediu busca",
                "SCAN_TEXT": "pediu varredura",
                "FIX_MOJIBAKE": "pediu correcao de texto",
                "UNDO": "pediu desfazer",
                "EXECUTE": "pediu execucao",
                "OPEN_URL": "pediu abertura",
                "SCREENSHOT": "pediu print",
                "HUMAN_TEST": "pediu teste visual",
            }
            return f"{names.get(action.upper(), action.upper())}: {payload}".strip()

        cleaned = self.clean_live_ai_text(trace or chunk, limit=260)
        if cleaned:
            return cleaned
        return "processando resposta..."

    def update_ai_activity_from_stream(self, text):
        normalized = self.normalize_plain_text(text or "")
        if re.search(r"\[(write|replace|fix_mojibake|undo)\s*:", normalized):
            self.set_ai_activity("IA preparando alteracao")
        elif re.search(r"\[(execute|open_url|screenshot|human_test)\s*:", normalized):
            self.set_ai_activity("IA preparando validacao")
        elif re.search(r"\[(read|search_text|scan_text)\s*:", normalized):
            self.set_ai_activity("IA pedindo contexto")
        elif any(term in normalized for term in ("patch", "filechange", "apply", "alterando", "escrevendo")):
            self.set_ai_activity("Codex alterando arquivos")
        elif any(term in normalized for term in ("command", "execut", "rodando", "testando")):
            self.set_ai_activity("Codex executando")
        else:
            self.set_ai_activity("IA respondendo ao vivo")

    def _append_text(self, widget, text):
        widget.configure(state="normal")
        widget.insert("end", text)
        if widget is not self.agent_summary:
            self.trim_textbox_lines(widget)
        try:
            widget.see("end")
        except (RecursionError, tk.TclError):
            self.report_callback_exception(RecursionError, "scroll de texto excedeu limite", None)
        if widget is self.agent_summary:
            widget.configure(state="disabled")

    def trim_textbox_lines(self, widget, max_lines=None):
        max_lines = max_lines or self.max_textbox_lines
        try:
            total_lines = int(widget.index("end-1c").split(".")[0])
        except (tk.TclError, ValueError):
            return
        if total_lines <= max_lines:
            return
        delete_to = max(2, total_lines - max_lines + 1)
        try:
            widget.delete("1.0", f"{delete_to}.0")
        except tk.TclError:
            pass

    def _clean_terminal_control_sequences(self, text):
        text = re.sub(r"\x1b\][^\a]*(?:\a|\x1b\\)", "", text)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = re.sub(r"\[\?25[hl]", "", text)
        text = re.sub(r"\[[0-9;]+[A-Za-z]", "", text)
        text = text.replace("\x08", "")
        return text

    def _replace_terminal_current_line(self, widget, text):
        if widget.index("end-1c") == "1.0":
            widget.insert("end", text)
            return
        widget.delete("end-1c linestart", "end-1c")
        widget.insert("end-1c", text)

    def _compact_terminal_progress(self, text):
        if "pulling " not in text and not self.terminal_progress_active:
            return None

        cleaned = text.replace("\r", "\n")
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        progress_lines = [
            line
            for line in lines
            if line.startswith("pulling ")
            or line.startswith("verifying ")
            or line.startswith("writing ")
            or line.startswith("success")
        ]
        if not progress_lines:
            return None

        latest = progress_lines[-1]
        if latest.startswith("success"):
            self.terminal_progress_active = False
            return latest + "\n"

        self.terminal_progress_active = True
        return latest

    def _append_terminal_text(self, widget, text):
        text = self._clean_terminal_control_sequences(str(text))
        widget.configure(state="normal")

        compacted = self._compact_terminal_progress(text)
        if compacted is not None:
            if self.terminal_progress_active:
                self._replace_terminal_current_line(widget, compacted)
            else:
                self._replace_terminal_current_line(widget, compacted.rstrip("\n"))
                widget.insert("end", "\n")
            self.trim_textbox_lines(widget)
            try:
                widget.see("end")
            except (RecursionError, tk.TclError):
                self.report_callback_exception(RecursionError, "scroll do terminal excedeu limite", None)
            return

        buffer = []
        saw_carriage_return = False

        for char in text:
            if char == "\r":
                saw_carriage_return = True
                self._replace_terminal_current_line(widget, "".join(buffer))
                buffer.clear()
                continue

            if char == "\n":
                if buffer:
                    if saw_carriage_return:
                        self._replace_terminal_current_line(widget, "".join(buffer))
                    else:
                        widget.insert("end", "".join(buffer))
                    buffer.clear()
                widget.insert("end", "\n")
                saw_carriage_return = False
                continue

            buffer.append(char)

        if buffer:
            if saw_carriage_return:
                self._replace_terminal_current_line(widget, "".join(buffer))
            else:
                widget.insert("end", "".join(buffer))

        self.trim_textbox_lines(widget)
        try:
            widget.see("end")
        except (RecursionError, tk.TclError):
            self.report_callback_exception(RecursionError, "scroll do terminal excedeu limite", None)

    def add_chat_message(self, sender, text):
        self.after(0, lambda: self._add_chat_message_sync(sender, text))

    def add_chat_image_message(self, sender, image_path, text=""):
        self.after(0, lambda: self._add_chat_image_message_sync(sender, image_path, text))

    def _add_chat_message_sync(self, sender, text):
        user = sender.lower() in {"voce", "você"}
        system = sender.lower() in {"sistema", "erro"}
        border_color = THEME["accent_dark"] if user else THEME["border"]
        sender_color = "#ffffff" if user else THEME["warning"] if sender == "Erro" else THEME["accent"]
        display_text = self.format_chat_text_for_display(text, sender=sender)
        font = self.chat_text_font(display_text)

        frame = ctk.CTkFrame(
            self.chat_history,
            fg_color="transparent",
            border_width=1,
            border_color=border_color,
            corner_radius=6,
        )
        frame.pack(fill="x", padx=6, pady=5)
        self.register_chat_frame(frame)

        ctk.CTkLabel(
            frame,
            text=sender,
            font=("Segoe UI", 12, "bold"),
            text_color=sender_color,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 0))

        textbox = ctk.CTkTextbox(
            frame,
            fg_color="transparent",
            text_color=THEME["text"],
            font=font,
            wrap="word",
            height=42,
        )
        textbox.pack(fill="x", padx=10, pady=(0, 8))
        self._autohide_ctk_textbox_scrollbar(textbox)
        textbox.insert("1.0", display_text)
        textbox.configure(state="disabled")

        self.resize_chat_textbox(textbox, display_text, max_height=560)
        self.scroll_textbox_start(textbox)
        self.safe_chat_scroll_bottom()

    def _add_chat_image_message_sync(self, sender, image_path, text=""):
        user = sender.lower() in {"voce", "você"}
        system = sender.lower() in {"sistema", "erro"}
        border_color = THEME["accent_dark"] if user else THEME["border"]
        sender_color = "#ffffff" if user else THEME["warning"] if sender == "Erro" else THEME["accent"]

        frame = ctk.CTkFrame(
            self.chat_history,
            fg_color="transparent",
            border_width=1,
            border_color=border_color,
            corner_radius=6,
        )
        frame.pack(fill="x", padx=6, pady=5)
        self.register_chat_frame(frame)

        ctk.CTkLabel(
            frame,
            text=sender,
            font=("Segoe UI", 12, "bold"),
            text_color=sender_color,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 0))

        display_text = self.format_chat_text_for_display(text, sender=sender)
        if display_text:
            textbox = ctk.CTkTextbox(
                frame,
                fg_color="transparent",
                text_color=THEME["text"],
                font=self.chat_text_font(display_text),
                wrap="word",
                height=42,
            )
            textbox.pack(fill="x", padx=10, pady=(0, 6))
            self._autohide_ctk_textbox_scrollbar(textbox)
            textbox.insert("1.0", display_text)
            textbox.configure(state="disabled")
            self.resize_chat_textbox(textbox, display_text, max_height=320)
            self.scroll_textbox_start(textbox)

        try:
            with Image.open(image_path) as source:
                image = source.copy()
            image.thumbnail((520, 320))
            chat_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            image_label = ctk.CTkLabel(frame, image=chat_image, text="")
            image_label.image_ref = chat_image
            image_label.pack(anchor="w", padx=10, pady=(0, 10))
            frame.image_ref = chat_image
        except Exception as exc:
            self.log_agent(f"Nao consegui renderizar imagem no chat: {exc}")

        self.safe_chat_scroll_bottom()

    def chat_text_font(self, text):
        code_markers = ("```", "[EXECUTE:", "[READ:", "[WRITE:", "[REPLACE:", "Traceback", "Exception", "error:")
        if any(marker in (text or "") for marker in code_markers):
            return ("Consolas", 12)
        return ("Segoe UI", 14)

    def estimate_chat_line_count(self, text, width=104):
        lines = (text or "").splitlines() or [""]
        count = 0
        for line in lines:
            visual_len = 0
            for char in line:
                visual_len += 2 if ord(char) > 127 else 1
            count += max(1, (visual_len // width) + 1)
        return count

    def format_chat_text_for_display(self, text, sender=""):
        raw = self.normalize_chat_spacing(text)
        if not raw:
            return ""
        parts = re.split(r"(```.*?```)", raw, flags=re.DOTALL)
        formatted = []
        for part in parts:
            if not part:
                continue
            if part.startswith("```") and part.endswith("```"):
                formatted.append(part.strip())
            else:
                formatted.append(self.format_plain_chat_block(part))
        result = "\n\n".join(piece for piece in formatted if piece.strip())
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        return result

    def normalize_chat_spacing(self, text):
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""
        raw = re.sub(r"(?<=[.!?])(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", " ", raw)
        raw = re.sub(r"(?<=[.!?])\s+(?=(Resumo|Arquitetura|Fluxo|Arquivos|Riscos?|Pontos|Próxim|Proxim|Sugest|Implementaç|Implementac|Diagnóstico|Diagnostico)\b)", "\n\n", raw)
        raw = re.sub(r"(?<=[a-záéíóúâêôãõç0-9])(?=(Resumo|Arquitetura|Fluxo|Arquivos|Riscos|Pontos|Próxim|Proxim|Sugest|Implementaç|Implementac|Diagnóstico|Diagnostico)\b)", "\n\n", raw)
        raw = re.sub(r"(?<!\n)([-*]\s+)", r"\n\1", raw)
        raw = re.sub(r"[ \t]{2,}", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        return raw.strip()

    def resize_chat_textbox(self, textbox, text, max_height=560):
        try:
            width_pixels = max(520, textbox.winfo_width() or 820)
        except tk.TclError:
            width_pixels = 820
        chars = max(58, min(112, width_pixels // 8))
        line_count = self.estimate_chat_line_count(text, width=chars)
        height = min(max_height, max(52, line_count * 24 + 22))
        textbox.configure(height=height)

    def scroll_textbox_end(self, textbox):
        try:
            textbox.see("end")
            textbox.yview_moveto(1.0)
        except (RecursionError, tk.TclError):
            pass

    def scroll_textbox_start(self, textbox):
        try:
            textbox.see("1.0")
            textbox.yview_moveto(0.0)
        except (RecursionError, tk.TclError):
            pass

    def format_plain_chat_block(self, text):
        cleaned = text.replace("`", "")
        cleaned = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"^\s*#+\s*", "", cleaned, flags=re.MULTILINE)
        output = []
        previous_kind = ""
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                if output and output[-1] != "":
                    output.append("")
                previous_kind = ""
                continue

            heading = self.chat_heading_text(line)
            if heading:
                if output and output[-1] != "":
                    output.append("")
                output.append(heading)
                previous_kind = "heading"
                continue

            bullet_match = re.match(r"^[-*]\s+(.*)$", line)
            numbered_match = re.match(r"^(\d+)[.)]\s+(.*)$", line)
            if bullet_match:
                bullet = "- " + bullet_match.group(1).strip()
                output.extend(self.wrap_chat_line(bullet, initial_indent="", subsequent_indent="  "))
                previous_kind = "bullet"
                continue
            if numbered_match:
                bullet = f"{numbered_match.group(1)}. {numbered_match.group(2).strip()}"
                output.extend(self.wrap_chat_line(bullet, initial_indent="", subsequent_indent="   "))
                previous_kind = "bullet"
                continue

            if previous_kind == "bullet" and output and output[-1] != "":
                output.append("")
            output.extend(self.wrap_chat_line(line))
            previous_kind = "text"

        return "\n".join(output).strip()

    def chat_heading_text(self, line):
        stripped = line.strip().strip("*").strip()
        if not stripped:
            return ""
        if stripped.endswith(":") and len(stripped) <= 70:
            return stripped
        if len(stripped) <= 46 and not stripped.startswith(("-", "*")):
            words = stripped.split()
            if len(words) <= 6 and any(word[:1].isupper() for word in words):
                return stripped
        return ""

    def wrap_chat_line(self, line, initial_indent="", subsequent_indent="", width=112):
        wrapped = textwrap.wrap(
            line,
            width=width,
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        return wrapped or [line]

    def begin_stream_message(self, sender):
        self.after(0, lambda: self._begin_stream_message_sync(sender))

    def _begin_stream_message_sync(self, sender):
        self.streaming_text = ""
        user = sender.lower() in {"voce", "vocÃª"}
        system = sender.lower() in {"sistema", "erro"}
        border_color = THEME["accent_dark"] if user else THEME["border"]
        sender_color = "#ffffff" if user else THEME["warning"] if sender == "Erro" else THEME["accent"]

        frame = ctk.CTkFrame(
            self.chat_history,
            fg_color="transparent",
            border_width=1,
            border_color=border_color,
            corner_radius=6,
        )
        frame.pack(fill="x", padx=6, pady=5)
        self.register_chat_frame(frame)

        ctk.CTkLabel(
            frame,
            text=sender,
            font=("Segoe UI", 12, "bold"),
            text_color=sender_color,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 0))

        textbox = ctk.CTkTextbox(
            frame,
            fg_color="transparent",
            text_color=THEME["text"],
            font=("Segoe UI", 14),
            wrap="word",
            height=42,
        )
        textbox.pack(fill="x", padx=10, pady=(0, 8))
        self._autohide_ctk_textbox_scrollbar(textbox)
        textbox.configure(state="disabled")
        self.streaming_textbox = textbox
        self.safe_chat_scroll_bottom()

    def append_stream_message(self, text):
        if not text:
            return
        self.after(0, lambda chunk=text: self._append_stream_message_sync(chunk))

    def _append_stream_message_sync(self, text):
        textbox = self.streaming_textbox
        if textbox is None:
            return
        self.streaming_text += text
        stream_text = self.normalize_chat_spacing(self.streaming_text)
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.insert("1.0", stream_text)
        textbox.configure(state="disabled")
        self.resize_chat_textbox(textbox, stream_text, max_height=620)
        self.scroll_textbox_end(textbox)
        self.safe_chat_scroll_bottom()

    def finish_stream_message(self):
        self.after(0, self._finish_stream_message_sync)

    def _finish_stream_message_sync(self):
        textbox = self.streaming_textbox
        if textbox is not None and self.streaming_text:
            display_text = self.format_chat_text_for_display(self.streaming_text)
            textbox.configure(state="normal")
            textbox.delete("1.0", "end")
            textbox.insert("1.0", display_text)
            textbox.configure(state="disabled", font=self.chat_text_font(display_text))
            self.resize_chat_textbox(textbox, display_text, max_height=620)
            self.scroll_textbox_end(textbox)
            self.safe_chat_scroll_bottom()
        self.streaming_textbox = None
        self.streaming_text = ""

    def replace_stream_message(self, text):
        self.after(0, lambda value=text: self._replace_stream_message_sync(value))

    def _replace_stream_message_sync(self, text):
        textbox = self.streaming_textbox
        if textbox is None:
            return
        self.streaming_text = self.format_chat_text_for_display(text or "")
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        if self.streaming_text:
            textbox.insert("1.0", self.streaming_text)
        textbox.configure(state="disabled", font=self.chat_text_font(self.streaming_text))
        self.resize_chat_textbox(textbox, self.streaming_text, max_height=620)
        self.scroll_textbox_end(textbox)
        self.safe_chat_scroll_bottom()

    def highlight_code(self, tab_name="Scratchpad"):
        info = self.open_editors.get(tab_name)
        if not info:
            return
        editor = info["widget"]
        filename = info.get("path") or ("scratch.py" if tab_name == "Scratchpad" else tab_name)
        code = editor.get("1.0", "end-1c")

        try:
            lexer = get_lexer_for_filename(str(filename), code)
        except Exception:
            return

        for tag in editor.tag_names():
            if tag.startswith("color_"):
                editor.tag_remove(tag, "1.0", "end")

        editor.mark_set("range_start", "1.0")
        for token, content in pygments.lex(code, lexer):
            editor.mark_set("range_end", f"range_start + {len(content)}c")
            color = self.style.style_for_token(token).get("color")
            if color:
                tag_name = f"color_{color}"
                editor.tag_config(tag_name, foreground=f"#{color}")
                editor.tag_add(tag_name, "range_start", "range_end")
            editor.mark_set("range_start", "range_end")

    def text_command(self):
        command = self.text_input.get("1.0", "end-1c").strip()
        image_path = self.pending_image_path
        if self.agent_busy or self.has_terminal_processes():
            self.cancel_ai_task()
            return
        if not command and not image_path:
            if self.last_failed_ai_task:
                self.retry_last_ai_task()
            return
        if self.should_retry_last_task(command, image_path):
            self._replace_text(self.text_input, "")
            self.retry_last_ai_task()
            return
        if not command and image_path:
            command = "Analise este print e me diga o que fazer."
        self._replace_text(self.text_input, "")
        if image_path:
            self.add_chat_image_message("Voce", image_path, command)
            self.clear_pending_image()
        else:
            self.add_chat_message("Voce", command)
        self.tabview.set("Chat AI")

        local_reply = self.local_quick_reply(command, image_path=image_path)
        if local_reply:
            self.last_response = local_reply
            self.add_chat_message("Merotec AI", local_reply)
            return

        normalized = self.normalize_plain_text(command)
        local_task_reply = self.local_autonomous_task(command, normalized, image_path=image_path)
        if local_task_reply:
            self.last_response = local_task_reply
            self.add_chat_message("Merotec AI", local_task_reply)
            return

        extra_context = None
        if self.is_project_analysis_request(normalized):
            extra_context = self.build_project_analysis_context()
            self.add_chat_message("Sistema", "Preparando contexto inicial do projeto para a IA...")

        self._run_ai_task(command, image_path=image_path, extra_context=extra_context)

    def cancel_ai_task(self):
        self.cancelled_task_ids.add(self.current_task_id)
        try:
            self.engine.cancel_generation()
        except Exception:
            pass
        killed = self.cancel_active_terminal_processes()
        self.add_chat_message("Sistema", "Cancelando tarefa atual...")
        if killed:
            self.add_chat_message("Sistema", f"Processos encerrados: {killed}")
            self.append_to_term(f"\n[cancelado] {killed} processo(s) encerrado(s) pela IDE.\n")
        self.reset_busy_indicators_after_cancel()

    def is_task_cancelled(self, task_id):
        return task_id is not None and task_id in self.cancelled_task_ids

    def reset_busy_indicators_after_cancel(self):
        with self.ai_work_lock:
            self.ai_work_count = 0
            self.agent_busy = False
            self.ai_busy_started_at = None
        with self.terminal_work_lock:
            self.terminal_work_count = 0
            self.terminal_activity_generation += 1
            generation = self.terminal_activity_generation

        def update():
            if generation != self.terminal_activity_generation:
                return
            self.ai_progress.stop()
            self.ai_progress.grid_remove()
            self.terminal_activity_bar.stop()
            self.terminal_activity_frame.grid_remove()
            self.btn_send.configure(state="normal", text="Enviar")
            self.status_label.configure(text="Cancelado.", text_color=THEME["warning"])

        self.after(0, update)

    def should_retry_last_task(self, command, image_path=None):
        if image_path or not self.last_failed_ai_task:
            return False
        retry_commands = {
            "tentar novamente",
            "tente novamente",
            "reenviar",
            "reenviar tarefa",
            "repetir",
            "retry",
        }
        return self.normalize_plain_text(command) in retry_commands

    def retry_last_ai_task(self):
        task = self.last_failed_ai_task
        if not task:
            return
        self.last_failed_ai_task = None
        self.btn_send.configure(text="Enviar")
        self.add_chat_message("Voce", "Tentar novamente")
        self.add_chat_message("Sistema", f"Reenviando tarefa anterior: {task['command']}")
        self.tabview.set("Chat AI")
        self._run_ai_task(
            task["command"],
            image_path=task.get("image_path"),
            extra_context=task.get("extra_context"),
            task_objective=task.get("task_objective"),
            action_depth=task.get("action_depth", 0),
            task_id=task.get("task_id"),
        )

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

        if self.is_zoom_mobile_verification_request(normalized):
            return self.verify_zoom_mobile_locally(command)

        return None

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
                if any(term in lower for term in ("zoom", "camerazoom", "scale", "fov")):
                    zoom_hits.append(entry)
                if any(term in lower for term in ("mobile", "ismobile", "modo mobile")):
                    mobile_hits.append(entry)
                if any(term in lower for term in ("pinch", "touchstart", "touchmove", "gesture", "wheel")):
                    touch_hits.append(entry)

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
        return (
            f"{verdict}\n\n"
            "Arquivos verificados:\n"
            + "\n".join(summaries[:8])
            + "\n\nEvidencias:\n"
            + evidence_text
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

        for info in self.open_editors.values():
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

    def detect_run_kind(self, workspace):
        workspace = Path(workspace)
        if (workspace / "pubspec.yaml").exists():
            return "flutter"
        if (workspace / "package.json").exists():
            return "node"
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
            command = "flutter pub get && flutter run -d windows"
            self.run_workspace_command(command, f"Executando app Flutter: {rel_label}", cwd=workspace)
            return (
                f"Execucao iniciada para `{rel_label}` como app Flutter pelo Terminal Local. "
                "O `flutter run -d windows` ja faz o build antes de abrir o app."
            )

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
            notes.append("Parece ser um app Flutter/Dart.")
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
            return "Flutter/Dart"
        if (workspace / "package.json").exists():
            return "JavaScript/Node"
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

    def voice_command(self):
        if self.voice_capture_active:
            self.stop_voice_capture_and_send()
            return
        if self.agent_busy:
            return
        self.start_voice_capture()

    def set_voice_button_text(self, text, active=False):
        def update():
            button = self.sidebar_buttons.get("Comando por Voz") if hasattr(self, "sidebar_buttons") else None
            if button:
                button.configure(text=text)
                if active:
                    button.configure(fg_color=("#1f6aa5", "#1f6aa5"), hover_color=("#185887", "#185887"))
                else:
                    button.configure(fg_color=("#3b3b3b", "#2b2b2b"), hover_color=("#4a4a4a", "#3a3a3a"))

        self.after(0, update)

    def start_voice_capture(self):
        try:
            self.voice.start_recording()
            self.voice_capture_active = True
            self.voice_capture_started_at = time.time()
            self.set_voice_button_text("Capturando...", active=True)
            self.add_chat_message("Sistema", "Capturando audio. Clique novamente para parar e enviar.")
            self.set_status("Capturando audio...", "busy")
        except Exception as exc:
            self.voice_capture_active = False
            self.set_voice_button_text("Comando por Voz")
            self.add_chat_message("Erro", f"Nao consegui iniciar a captura de audio: {exc}")

    def stop_voice_capture_and_send(self):
        self.voice_capture_active = False
        elapsed = int(time.time() - (self.voice_capture_started_at or time.time()))
        self.voice_capture_started_at = None
        self.set_voice_button_text("Processando voz...", active=True)
        self.add_chat_message("Sistema", f"Audio capturado por {elapsed}s. Convertendo para texto...")
        self.tabview.set("Chat AI")

        def run():
            try:
                command = self.voice.stop_recording_and_transcribe()
            except Exception as exc:
                self.add_chat_message("Erro", f"Nao consegui converter o audio em texto: {exc}")
                command = None
            finally:
                self.set_voice_button_text("Comando por Voz")
                self.after(200, self.start_voice_keyword_listener)

            if not command:
                self.add_chat_message("Erro", "Nao foi possivel entender o audio.")
                self.set_status("Audio nao entendido.", "warning")
                return
            image_path = self.pending_image_path
            if image_path:
                self.add_chat_image_message("Voce", image_path, command)
                self.after(0, self.clear_pending_image)
            else:
                self.add_chat_message("Voce", command)
            self.set_status("Audio enviado para a IA.", "busy")
            self._run_ai_task(command, image_path=image_path)

        threading.Thread(target=run, daemon=True).start()

    def start_voice_keyword_listener(self):
        if not self.voice_keyword_listener_enabled:
            return
        try:
            started = self.voice.start_keyword_listener(
                self.handle_voice_keyword_command,
                on_capture_state=self.set_voice_keyword_capture_state,
                start_keyword=self.voice_keyword_start,
                end_keyword=self.voice_keyword_end,
            )
        except Exception as exc:
            self.voice_keyword_listener_enabled = False
            self.add_chat_message("Erro", f"Nao consegui iniciar comando de voz automatico: {exc}")
            return

        if started:
            self.add_chat_message(
                "Sistema",
                "Comando de voz automatico ativo: diga 'Merotec', fale a tarefa e finalize com 'ok'.",
            )

    def set_voice_keyword_capture_state(self, active):
        def update():
            if active:
                if self.voice_keyword_capture_active:
                    return
                self.voice_keyword_capture_active = True
                self.set_voice_button_text("Capturando...", active=True)
                self.set_status("Capturando comando de voz... diga 'ok' para enviar.", "busy")
                return
            if not self.voice_keyword_capture_active:
                return
            self.voice_keyword_capture_active = False
            if not self.voice_capture_active:
                self.set_voice_button_text("Comando por Voz")
                self.set_status("Ouvindo palavra-chave Merotec.", "ready")

        self.after(0, update)

    def handle_voice_keyword_command(self, command):
        def run_on_ui():
            clean_command = (command or "").strip()
            self.voice_keyword_capture_active = False
            self.set_voice_button_text("Processando voz...", active=True)
            if not clean_command:
                self.set_voice_button_text("Comando por Voz")
                return
            if self.agent_busy:
                self.add_chat_message(
                    "Sistema",
                    f"Comando de voz ignorado porque a IA ja esta trabalhando: {clean_command}",
                )
                self.set_voice_button_text("Comando por Voz")
                return

            image_path = self.pending_image_path
            self.tabview.set("Chat AI")
            if image_path:
                self.add_chat_image_message("Voce", image_path, clean_command)
                self.after(0, self.clear_pending_image)
            else:
                self.add_chat_message("Voce", clean_command)
            self.set_status("Comando de voz por palavra-chave enviado para a IA.", "busy")
            self.set_voice_button_text("Comando por Voz")
            self._run_ai_task(clean_command, image_path=image_path)

        self.after(0, run_on_ui)

    def _run_ai_task(self, command, image_path=None, extra_context=None, task_objective=None, action_depth=0, task_id=None):
        def process():
            retry_available = False
            stream_started = False
            streamed_text = []
            objective = task_objective or command
            current_task_id = task_id
            if not task_objective:
                self.current_task_id += 1
                current_task_id = self.current_task_id
                self.active_ai_objective = command
                self.ai_read_history = {}
                self.ai_search_history = {}
                self.ai_task_metrics[current_task_id] = {
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
                }
                self.ai_passive_action_count = 0
            elif current_task_id is None:
                current_task_id = self.current_task_id

            def on_stream(chunk):
                nonlocal stream_started
                if self.is_task_cancelled(current_task_id):
                    return
                if not chunk:
                    return
                streamed_text.append(chunk)
                self.update_ai_live_trace(chunk, current_task_id)
                if not stream_started:
                    stream_started = True
                    self.set_ai_activity("IA trabalhando")
                    self.set_status("IA trabalhando...", "busy")

            self.set_ai_busy(True)
            self.set_ai_activity("IA pensando")
            self.reset_ai_live_trace(current_task_id, objective)
            try:
                if self.is_task_cancelled(current_task_id):
                    return
                context = (
                    f"Workspace atual: {self.current_workspace}\n\n"
                    f"MISSAO ATIVA DA IA:\n{objective}\n\n"
                    "MODO CODEX DA IDE:\n"
                    "- Comporte-se como um agente de engenharia, nao como um assistente passivo.\n"
                    "- Use raciocinio altissimo: antes de responder, escolha o proximo passo que realmente muda, executa, valida ou conclui.\n"
                    "- Para perguntas simples, responda direto.\n"
                    "- Para analise/planejamento, entregue diagnostico completo em texto e nao execute/edite sem pedido claro.\n"
                    "- Para implementacao/correcao, avance ate uma mudanca aplicada e uma verificacao plausivel.\n"
                    "- Trabalhe de forma produtiva: responda com conclusao util, diagnostico objetivo ou acao real quando precisar mexer no projeto.\n"
                    "- Use tags da IDE quando elas forem o caminho mais confiavel, mas nao bloqueie uma resposta tecnica util so por nao conter tag.\n"
                    "- Se afirmar que alterou, executou ou validou, garanta que houve acao real da IDE ou mudanca direta detectavel no workspace.\n"
                    "- Leia o contexto necessario sem entrar em loop; prefira agir quando ja houver informacao suficiente.\n"
                    "- Preserve a estrutura existente e faca alteracoes pequenas quando o projeto ja funciona.\n"
                    "- Depois de editar, valide com [EXECUTE], [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST] quando isso for util.\n\n"
                    "Continue essa missao de forma autonoma. "
                    "Atue como especialista senior em desenvolvimento de sistemas, apps e jogos: diagnostique, implemente, valide e corrija ate resolver. "
                    "Se precisar de arquivo, use [READ]; o conteudo retornado pela IDE passa a ser sua memoria de trabalho. "
                    "Se precisar verificar se um recurso/termo existe, use [SEARCH_TEXT: padrao | arquivo]. "
                    "Use [SCAN_TEXT] e [FIX_MOJIBAKE] somente quando a missao pedir correcao de texto, acentos, codificacao ou caracteres corrompidos. "
                    "Nao desvie uma tarefa de interface, logica, camera, build ou execucao para mojibake. "
                    "Se souber a mudanca completa, use [WRITE]. "
                    "Se souber apenas um trecho a trocar, use [REPLACE]. "
                    "Se precisar rodar, use [EXECUTE]. "
                    "Para testar projeto HTML/Web, use [EXECUTE: python -m http.server 8000]; a IDE troca pelo Python real, escolhe porta livre e valida a URL. "
                    "Para abrir uma pagina validada, use [OPEN_URL: http://127.0.0.1:porta/]. "
                    "Para validar visualmente um app/jogo como usuario, use [HUMAN_TEST: auto]; a IDE executa, abre, espera a tela, captura print e devolve a imagem para voce analisar. "
                    "Use [SCREENSHOT: tela] apenas quando a tela ja estiver aberta. "
                    "Depois de analisar o print, corrija com [REPLACE] ou [WRITE] e teste novamente ate funcionar. "
                    "Para arquivo grande, prefira [READ: arquivo] uma vez; a IDE fara uma varredura completa e entregara um mapa do arquivo. "
                    "Entenda a estrutura antes de editar, mas nao fique repetindo leituras do mesmo arquivo. "
                    "Em tarefa grande, faca no maximo algumas leituras estrategicas; depois aja com [REPLACE], [WRITE], [EXECUTE], [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]. "
                    "Evite narrar intencoes vazias; avance com leitura, alteracao, validacao ou uma conclusao clara. "
                    "Nao peca o objetivo novamente enquanto houver uma missao ativa.\n\n"
                    f"Alteracoes recentes feitas pela IDE neste projeto:\n{self.format_recent_changes_for_agent(limit=8)}\n\n"
                    f"Arquivos do workspace:\n{self.get_workspace_tree(limit=220)}"
                )
                if extra_context:
                    context += f"\n\nContexto adicional:\n{extra_context}"
                context += f"\n\n{self.build_project_intelligence_context()}"

                direct_snapshot = self.snapshot_workspace_for_direct_actions()
                response = self.engine.generate_solution(
                    command,
                    image_path=image_path,
                    code_context=context,
                    stream_callback=on_stream,
                    workspace_path=self.current_workspace,
                )
                streamed_joined = "".join(streamed_text)
                if self.is_task_cancelled(current_task_id):
                    return
                self.last_response = response or streamed_joined
                if not (self.last_response or "").strip():
                    self.last_response = (
                        "O Codex terminou sem devolver texto para a IDE. "
                        "A tarefa nao foi concluida. Clique em Reenviar para tentar novamente, "
                        "ou abra o Log do Agente para verificar se houve interrupcao."
                    )
                    self.last_failed_ai_task = {
                        "command": command,
                        "image_path": image_path,
                        "extra_context": extra_context,
                        "task_objective": objective,
                        "action_depth": action_depth,
                        "task_id": current_task_id,
                    }
                    retry_available = True
                direct_changes, direct_change_total = self.detect_direct_workspace_changes(direct_snapshot)
                direct_action_happened = direct_change_total > 0
                if direct_action_happened:
                    self.register_direct_workspace_changes(
                        direct_changes,
                        direct_change_total,
                        task_id=current_task_id,
                    )

                invalid_claim = False
                if (
                    not direct_action_happened
                    and not self.task_has_real_action(current_task_id)
                    and self.claims_concrete_result_without_real_action(
                        self.last_response,
                        task_objective=objective,
                    )
                ):
                    self.log_agent("Aviso: resposta afirma acao sem evidencia direta; exibindo sem bloquear.")
                display_response = self.strip_agent_action_markup(self.last_response)
                has_agent_action = self.response_has_agent_action(self.last_response)
                if has_agent_action:
                    display_response = self.action_execution_message(self.last_response)
                elif direct_action_happened:
                    display_response = self.format_direct_workspace_changes(
                        direct_changes,
                        direct_change_total,
                    )
                elif invalid_claim:
                    display_response = (
                        "A IDE bloqueou uma resposta que dizia ter executado/corrigido, "
                        "mas nao trouxe uma acao real. A proxima resposta precisa vir com uma acao executavel."
                    )
                stream_visible = self.streaming_textbox is not None
                if stream_visible:
                    if has_agent_action:
                        self.replace_stream_message(display_response or "A IDE recebeu uma acao interna e vai executar agora.")
                    elif response and response.strip() and response.strip() not in streamed_joined.strip():
                        self.append_stream_message("\n\n" + response)
                    self.finish_stream_message()
                else:
                    if display_response or not has_agent_action:
                        self.add_chat_message("Merotec AI", display_response or self.last_response)
                if retry_available:
                    pass
                elif self.is_codex_capacity_response(self.last_response):
                    self.last_failed_ai_task = {
                        "command": command,
                        "image_path": image_path,
                        "extra_context": extra_context,
                        "task_objective": objective,
                        "action_depth": action_depth,
                        "task_id": current_task_id,
                    }
                    retry_available = True
                else:
                    self.last_failed_ai_task = None
                self.parse_and_execute_agent_actions(
                    self.last_response,
                    task_objective=objective,
                    action_depth=action_depth,
                    task_id=current_task_id,
                    direct_action_happened=direct_action_happened,
                )
                if getattr(self.engine, "provider", "") == "codex":
                    self.load_workspace_files()
            except Exception as exc:
                self.add_chat_message("Erro", str(exc))
            finally:
                self.finish_ai_live_trace(current_task_id)
                self.set_ai_busy(False)
                if retry_available:
                    self.after(80, self.show_retry_available)

        threading.Thread(target=process, daemon=True).start()

    def is_codex_capacity_response(self, text):
        normalized = self.normalize_plain_text(text or "")
        return "codex esta com alta demanda" in normalized or "alta demanda" in normalized

    def response_has_agent_action(self, text):
        if not text:
            return False
        return bool(
            re.search(
                r"\[(WRITE|REPLACE|READ|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:",
                text,
                re.IGNORECASE,
            )
        )

    def strip_agent_action_markup(self, text):
        if not text:
            return ""
        cleaned = text
        block_patterns = [
            r"\[WRITE:\s*.+?\].*?\[/WRITE\]",
            r"\[REPLACE:\s*.+?\].*?\[/REPLACE\]",
        ]
        for pattern in block_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(
            r"^\s*\[(READ|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:[^\]]+\]\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        cleaned = re.sub(
            r"\[(READ|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:[^\]]+\]",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def action_execution_message(self, text):
        if not text:
            return ""
        tags = {
            match.group(1).upper()
            for match in re.finditer(
                r"\[(WRITE|REPLACE|READ|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:",
                text,
                re.IGNORECASE,
            )
        }
        if tags & {"WRITE", "REPLACE", "FIX_MOJIBAKE", "UNDO"}:
            return "A IDE recebeu uma alteracao real e iniciou a aplicacao no projeto."
        if tags & {"EXECUTE", "OPEN_URL", "SCREENSHOT", "HUMAN_TEST"}:
            return "A IDE recebeu uma execucao real e iniciou a validacao."
        if tags & {"READ", "SEARCH_TEXT", "SCAN_TEXT"}:
            return "A IDE esta coletando contexto objetivo para executar o proximo passo."
        return ""

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

        fix_paths = re.findall(r"\[FIX_MOJIBAKE:\s*(.+?)\]", response_text, re.IGNORECASE)
        if fix_paths and not self.objective_allows_text_repair(task_objective or self.active_ai_objective or ""):
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

        read_paths = re.findall(r"\[READ:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(read_paths)
        if (
            not direct_action_happened
            and not self.task_has_real_action(task_id)
            and self.claims_concrete_result_without_real_action(response_text, task_objective=task_objective)
        ):
            self.redirect_claimed_action_to_real_action(
                response_text,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )
            return
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
            if re.search(r"\[EXECUTE:\s*(.+?)\]", response_text, re.IGNORECASE):
                self.add_chat_message(
                    "Merotec AI",
                    "Leitura priorizada antes da execucao, para agir com base no arquivo correto.",
                )
            if re.search(r"\[SEARCH_TEXT:\s*(.+?)\]", response_text, re.IGNORECASE):
                self.add_chat_message(
                    "Merotec AI",
                    "Leitura priorizada antes da busca, para editar com base concreta.",
                )
            return

        search_requests = re.findall(r"\[SEARCH_TEXT:\s*(.+?)\]", response_text, re.IGNORECASE)
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

        scan_paths = re.findall(r"\[SCAN_TEXT:\s*(.+?)\]", response_text, re.IGNORECASE)
        if scan_paths and not self.objective_allows_text_repair(task_objective or self.active_ai_objective or ""):
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

        undo_paths = re.findall(r"\[UNDO:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(undo_paths)
        for raw_path in undo_paths:
            self.mark_ai_active_action("write", task_id=task_id)
            self._agent_undo(raw_path)

        open_urls = re.findall(r"\[OPEN_URL:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(open_urls)
        for raw_url in open_urls:
            self.mark_ai_active_action("open_url", task_id=task_id)
            self._agent_open_url(raw_url)

        screenshot_requests = re.findall(r"\[SCREENSHOT:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(screenshot_requests)
        for request in screenshot_requests:
            self.mark_ai_active_action("screenshot", task_id=task_id)
            self._agent_screenshot(
                request,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )

        human_test_requests = re.findall(r"\[HUMAN_TEST:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(human_test_requests)
        for request in human_test_requests:
            self.mark_ai_active_action("human_test", task_id=task_id)
            self._agent_human_test(
                request,
                task_objective=task_objective,
                action_depth=action_depth,
                task_id=task_id,
            )

        execute_commands = re.findall(r"\[EXECUTE:\s*(.+?)\]", response_text, re.IGNORECASE)
        has_action = has_action or bool(execute_commands)
        for command in execute_commands:
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
            if self.is_file_mutation_command(command):
                self.mark_ai_active_action("redirect", task_id=task_id)
                self.redirect_mutation_command_to_write(
                    command,
                    task_objective=task_objective,
                    action_depth=action_depth,
                    task_id=task_id,
                )
                continue
            if self.is_file_inspection_command(command):
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
                "([READ], [SEARCH_TEXT], [SCAN_TEXT], [FIX_MOJIBAKE], [REPLACE], [WRITE], [EXECUTE], [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]) "
                "ou entregue uma conclusao final direta se a tarefa ja estiver respondida.",
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
                if plan["shell"]:
                    process = subprocess.Popen(plan["command"], shell=True, **popen_kwargs)
                else:
                    process = subprocess.Popen(
                        plan["command"],
                        shell=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        **popen_kwargs,
                    )
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

                image = ImageGrab.grab()
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

    def build_human_test_plan(self, request, task_objective=None, requested_command=None):
        workspace = Path(self.current_workspace).resolve()
        objective = self.normalize_plain_text((request or "") + "\n" + (task_objective or self.active_ai_objective or ""))
        kind = self.detect_run_kind(workspace)
        if requested_command:
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
        elif normalized_action in {"SEARCH_TEXT", "SCAN_TEXT", "EXECUTE_INSPECTION"}:
            metrics["search_rounds"] += 1
            metrics["searches"] += request_count

        repeated_reads = [
            path for path, count in metrics.get("read_paths", {}).items()
            if path and count >= 8
        ]
        too_many_reads = metrics["read_rounds"] > limits["rounds"] or metrics["read_files"] > limits["files"]
        too_many_searches = metrics["search_rounds"] > limits["search_rounds"]
        too_many_passive = metrics["passive_actions"] > limits["passive"]
        too_deep = action_depth >= 18 and normalized_action in {"READ", "SEARCH_TEXT", "SCAN_TEXT", "EXECUTE_INSPECTION"}
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
                "- Nao use [READ], [SEARCH_TEXT], [SCAN_TEXT], [EXECUTE], [REPLACE] ou [WRITE] nesta resposta.\n"
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
            "- Se a missao pede executar/testar, responda com [EXECUTE].\n"
            "- Se precisa validar visualmente, responda com [HUMAN_TEST: auto].\n"
            "- Se ja sabe que nao da para fazer com seguranca, entregue conclusao curta dizendo exatamente o bloqueio.\n"
            "- Nao use [READ], [SEARCH_TEXT], [SCAN_TEXT] ou comando de inspecao na proxima resposta."
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
                    "Se houver erro visual, comportamento quebrado ou tela vazia, corrija autonomamente com [READ], [REPLACE], [WRITE] ou [EXECUTE]. "
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
                r"\[(WRITE|REPLACE|FIX_MOJIBAKE|UNDO|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:",
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
        objective = task_objective or self.active_ai_objective or "Continuar tarefa atual"
        metrics = self.get_ai_task_metrics(task_id)
        metrics["protocol_violations"] = metrics.get("protocol_violations", 0) + 1

        self.log_agent("Resposta bloqueada: a IA afirmou execucao sem acao real.")
        if metrics["protocol_violations"] > 2 or action_depth >= 8:
            self.add_chat_message(
                "Erro",
                "A IA insistiu em narrar acao sem executar. A IDE parou para evitar alteracoes falsas. Envie o pedido novamente de forma direta.",
            )
            self.set_status("Acao real exigida.", "warning")
            return

        self.add_chat_message(
            "Sistema",
            "A IDE bloqueou a resposta porque a IA disse que corrigiu/executou, mas nao enviou WRITE, REPLACE ou EXECUTE.",
        )
        self._run_ai_task(
            "A resposta anterior afirmou que executou ou corrigiu algo, mas nao houve acao real na IDE. "
            "Continue a missao agora com uma tag executavel. "
            "Correcao so conta com [REPLACE], [WRITE], [FIX_MOJIBAKE] ou [UNDO]. "
            "Validacao so conta com [EXECUTE], [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]. "
            "Nao escreva que corrigiu, aplicou, rodou ou validou sem incluir a tag correspondente.",
            extra_context=(
                f"MISSAO ORIGINAL:\n{objective}\n\n"
                "RESPOSTA BLOQUEADA POR PROMESSA/FALSA EXECUCAO:\n"
                f"{response_text}\n\n"
                "PROXIMA RESPOSTA OBRIGATORIA:\n"
                "- Se precisa alterar arquivo, envie [REPLACE] ou [WRITE] completo.\n"
                "- Se precisa testar/rodar, envie [EXECUTE: comando].\n"
                "- Se precisa ver a tela ou testar como usuario, envie [HUMAN_TEST: auto].\n"
                "- Se a tarefa era apenas pergunta simples, responda o resultado final sem fingir execucao."
            ),
            task_objective=objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )

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
                "- Se precisa validar, use [EXECUTE].\n\n"
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
            "A IA tentou modificar arquivo usando [EXECUTE], o que pode nao alterar nada na IDE.\n"
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
                "- Se a missao pede executar/testar, responda com [EXECUTE].\n"
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

    def mojibake_score(self, text):
        markers = [
            "\ufffd",
            "Ã",
            "Â",
            "â€",
            "â€™",
            "â€œ",
            "â€\x9d",
            "â€“",
            "â€”",
            "ï»¿",
        ]
        return sum(text.count(marker) for marker in markers)

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

    def apply_mojibake_map(self, text):
        replacements = {
            "Ã¡": "á", "Ã ": "à", "Ã¢": "â", "Ã£": "ã", "Ã¤": "ä",
            "Ã©": "é", "Ãª": "ê", "Ã¨": "è", "Ã«": "ë",
            "Ã­": "í", "Ã®": "î", "Ã¬": "ì", "Ã¯": "ï",
            "Ã³": "ó", "Ã´": "ô", "Ãµ": "õ", "Ã²": "ò", "Ã¶": "ö",
            "Ãº": "ú", "Ã»": "û", "Ã¹": "ù", "Ã¼": "ü",
            "Ã§": "ç", "Ã±": "ñ",
            "Ã�": "Á", "Ã‰": "É", "Ã“": "Ó", "Ãš": "Ú", "Ã‡": "Ç",
            "Âº": "º", "Âª": "ª", "Â°": "°", "Â·": "·", "Â ": " ",
            "â€™": "'", "â€˜": "'", "â€œ": '"', "â€\x9d": '"',
            "â€“": "-", "â€”": "-", "â€¦": "...", "ï»¿": "",
        }
        repaired = text
        for bad, good in replacements.items():
            repaired = repaired.replace(bad, good)
        return repaired

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
                "Se a tarefa for implementar algo, aplique [REPLACE] ou [WRITE] agora e depois use [EXECUTE] para validar. "
                "Se ja tiver informacao suficiente, responda com [REPLACE], [WRITE] ou [EXECUTE]. "
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
            if repeated_too_much or coverage_before >= 0.99 or history["requests"] > 40:
                reason = "intervalo repetido em excesso" if repeated_too_much else "arquivo ja foi mapeado quase inteiro"
                return self.build_read_stop_context(rel, total_lines, history, reason)
            history["ranges"].append((start, end))
            history["range_keys"].add(range_key)
            block = self.build_file_context_for_agent(path, rel, line_range=(start, end))
        else:
            history["overview_count"] += 1
            coverage_before = self.read_coverage_ratio(history["ranges"], total_lines)
            if history["overview_count"] > 6 and coverage_before >= 0.90:
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
            "- Se a tarefa for validar, use [EXECUTE] ou [HUMAN_TEST].\n"
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

    def _agent_execute(self, command, task_objective=None, action_depth=0, task_id=None):
        if self.is_task_cancelled(task_id):
            self.log_agent(f"Comando ignorado apos cancelamento: {command}")
            return
        command = command.strip()
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

    def build_command_failure_diagnostic(self, command, output, returncode):
        text = output or ""
        normalized = self.normalize_plain_text(text)
        signature = self.command_failure_signature(command, text)
        count = self.command_failure_signatures.get(signature, 0) + 1
        self.command_failure_signatures[signature] = count

        layer = "Comando local"
        likely_cause = "A saida nao indicou uma camada especifica."
        suggested_files = []
        guidance = [
            "Classifique a camada do erro antes de alterar arquivos.",
            "Nao repita o mesmo comando antes de mudar a causa provavel.",
        ]

        if "generated_plugin_registrant.h" in text and ("c1083" in normalized or "no such file" in normalized):
            layer = "Flutter Windows / C++ include"
            likely_cause = (
                "O arquivo gerado existe ou deveria existir em windows/flutter, "
                "mas o runner Windows nao esta encontrando o include."
            )
            suggested_files.extend([
                "windows/runner/flutter_window.cpp",
                "windows/runner/CMakeLists.txt",
                "windows/flutter/generated_plugin_registrant.h",
            ])
            guidance.extend([
                "Verifique include relativo em flutter_window.cpp.",
                "Verifique target_include_directories no windows/runner/CMakeLists.txt.",
                "Nao mexa em lib/main.dart para esse erro.",
            ])
        elif "dwmsetwindowattribute" in normalized or "lnk2019" in normalized or "lnk1120" in normalized:
            layer = "Flutter Windows / linker C++"
            likely_cause = "Falta linkar uma biblioteca nativa do Windows ou alguma dependencia do runner."
            suggested_files.extend([
                "windows/runner/CMakeLists.txt",
                "windows/runner/win32_window.cpp",
            ])
            guidance.extend([
                "Para DwmSetWindowAttribute, confira dwmapi.lib no target_link_libraries.",
                "Nao corrija esse erro em Dart ou pubspec.yaml.",
            ])
        elif "cmake error" in normalized or "cmakelists" in normalized:
            layer = "CMake"
            likely_cause = "Erro na configuracao CMake do projeto nativo."
            suggested_files.extend(["windows/CMakeLists.txt", "windows/runner/CMakeLists.txt"])
        elif "gradle" in normalized or "assemble" in normalized:
            layer = "Android / Gradle"
            likely_cause = "Erro na configuracao Android, Gradle ou dependencia nativa."
            suggested_files.extend(["android/build.gradle", "android/app/build.gradle", "pubspec.yaml"])
        elif "target of uri doesn't exist" in normalized or "undefined name" in normalized or "error:" in normalized and ".dart" in normalized:
            layer = "Dart / Flutter"
            likely_cause = "Erro de codigo Dart, import ou API usada no app."
            suggested_files.extend(self.extract_workspace_paths_from_output(text))
            suggested_files.extend(["lib/main.dart", "pubspec.yaml"])
        elif "pub get" in normalized or "because " in normalized and "depends on" in normalized:
            layer = "Dependencias Flutter"
            likely_cause = "Conflito ou falta de dependencia no pubspec."
            suggested_files.extend(["pubspec.yaml"])

        suggested_files.extend(self.extract_workspace_paths_from_output(text))
        suggested_files = self.unique_existing_relative_paths(suggested_files)
        snippets = self.read_diagnostic_file_snippets(suggested_files[:6])

        repeated = ""
        if count > 1:
            repeated = (
                f"\nAviso de repeticao: essa mesma falha ja ocorreu {count} vezes nesta sessao. "
                "A IA deve alterar a causa provavel antes de executar o mesmo comando novamente.\n"
            )

        files_text = "\n".join(f"- {path}" for path in suggested_files) or "- Nenhum arquivo especifico detectado."
        guidance_text = "\n".join(f"- {item}" for item in guidance)
        return (
            "\nDIAGNOSTICO DE FALHA GERADO PELA IDE:\n"
            f"Camada provavel: {layer}\n"
            f"Causa provavel: {likely_cause}\n"
            f"Assinatura: {signature}\n"
            f"{repeated}"
            "Arquivos suspeitos:\n"
            f"{files_text}\n"
            "Direcionamento para a IA:\n"
            f"{guidance_text}\n"
            f"{snippets}\n"
        )

    def command_failure_signature(self, command, output):
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        important = []
        markers = ("error", "erro", "fatal", "exception", "lnk", "c1083", "failed")
        for line in lines:
            lower = line.lower()
            if any(marker in lower for marker in markers):
                important.append(re.sub(r"\d+,\d+s|\d+\.\d+s|\d+s", "<tempo>", line))
        basis = important[-3:] if important else lines[-3:]
        return self.normalize_plain_text(command + " | " + " | ".join(basis))[:220]

    def extract_workspace_paths_from_output(self, output):
        workspace = Path(self.current_workspace).resolve()
        paths = []
        patterns = [
            r"([A-Za-z]:\\[^\r\n:]+?\.(?:dart|cpp|cc|h|hpp|c|gradle|yaml|cmake|txt|rc))",
            r"((?:lib|windows|android|ios|web|test|linux|macos)[\\/][^\s:\]\)]+?\.(?:dart|cpp|cc|h|hpp|c|gradle|yaml|cmake|txt|rc))",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, output or ""):
                clean = match.strip().strip("\"'")
                candidate = Path(clean)
                if candidate.is_absolute():
                    try:
                        rel = candidate.resolve().relative_to(workspace).as_posix()
                        paths.append(rel)
                    except (OSError, ValueError):
                        continue
                else:
                    paths.append(clean.replace("\\", "/"))
        return paths

    def unique_existing_relative_paths(self, paths):
        workspace = Path(self.current_workspace).resolve()
        result = []
        seen = set()
        for raw in paths:
            if not raw:
                continue
            clean = raw.strip().replace("\\", "/")
            if clean in seen:
                continue
            candidate = (workspace / clean).resolve()
            try:
                if candidate.exists() and os.path.commonpath([str(workspace), str(candidate)]) == str(workspace):
                    result.append(clean)
                    seen.add(clean)
            except (OSError, ValueError):
                continue
        return result

    def read_diagnostic_file_snippets(self, paths):
        if not paths:
            return ""
        workspace = Path(self.current_workspace).resolve()
        blocks = []
        for rel in paths:
            path = (workspace / rel).resolve()
            if path.is_dir():
                continue
            try:
                blocks.append("\n" + self.build_file_context_for_agent(path, rel))
            except OSError:
                continue
        return "\n".join(blocks)

    def _strip_markdown_code(self, content):
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("\n", 1)[0]
        return cleaned.strip() + "\n"

    def play_last_response(self):
        if not self.last_response:
            self.add_chat_message("Sistema", "Nenhuma resposta para ler ainda.")
            return
        self.voice.stop()
        text = self.last_response.split("```")[0].strip()
        self.voice.speak(text[:3500])

    def upload_and_update_code(self):
        file_path = filedialog.askopenfilename(
            initialdir=self.current_workspace,
            filetypes=[
                ("Codigo", "*.py *.js *.ts *.tsx *.jsx *.cpp *.c *.h *.css *.html *.json *.md"),
                ("Todos", "*.*"),
            ],
        )
        if not file_path:
            return

        try:
            path = Path(file_path)
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            self.add_chat_message("Erro", f"Nao consegui abrir o arquivo: {exc}")
            return

        self._replace_text(self.code_editor, content)
        if "Scratchpad" in self.open_editors:
            self.open_editors["Scratchpad"]["dirty"] = False
        self.tabview.set("Scratchpad")
        self.highlight_code("Scratchpad")
        self.update_editor_markers("Scratchpad")
        self.add_chat_message("Sistema", f"Arquivo carregado no scratchpad: {file_path}")

        dialog = ctk.CTkInputDialog(
            text="O que a IA deve melhorar nesse arquivo?",
            title="Instrucao para a IA",
        )
        instruction = dialog.get_input()
        if instruction:
            try:
                rel = path.resolve().relative_to(Path(self.current_workspace).resolve()).as_posix()
            except ValueError:
                rel = path.name
            file_context = self.build_file_context_for_agent(path, rel)
            self._run_ai_task(instruction, extra_context=f"Arquivo anexado pela interface:\n{file_context}")

    def analyze_hw(self):
        image_path = filedialog.askopenfilename(
            initialdir=self.current_workspace,
            filetypes=[
                ("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp", "*.gif", "*.webm"),
                ("Todos", "*.*"),
            ],
        )
        if not image_path:
            return
        self.add_chat_message("Sistema", f"Imagem enviada para analise: {image_path}")
        self._run_ai_task("Analise a imagem e aponte problemas, oportunidades ou proximos passos.", image_path=image_path)


if __name__ == "__main__":
    app = UniversalApp()
    app.mainloop()

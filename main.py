import json
import keyword
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
import urllib.parse
import urllib.request
import webbrowser
import builtins
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
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageGrab
from pygments.lexers import get_lexer_for_filename
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from modules.app_constants import (
    APP_CHANGE_HISTORY_FILE,
    APP_HISTORY_FILE,
    APP_NAME,
    APP_SETTINGS_FILE,
    CHAT_TAB_NAME,
    CORE_TABS,
    DEFAULT_APP_SETTINGS,
    DEFAULT_WORKSPACE,
    FILE_ICON_COLORS,
    IGNORED_SUFFIXES,
    PROJECTS_DIR,
    PROJECT_ROOT,
    SCRATCHPAD_DEFAULT_TEXT,
    is_ignored_dir_name,
)
from modules.ai_config import AiConfigMixin
from modules.agent_actions import AgentActionsMixin
from modules.app_state import AppStateMixin
from modules.engine import UniversalEngine
from modules.executor import CodeExecutor
from modules.editor_intelligence import completion_items, extract_symbols, word_prefix
from modules.memory import MemorySubnet
from modules.plugin_manager import build_plugin_report_messages, initialize_plugins
from modules.project_manager import ProjectManager
from modules.ui_theme import THEME
from modules.workspace_intelligence import WorkspaceIntelligenceMixin
from modules.voice import VoiceModule
from modules.ui_web_chat_bridge import InternalBrowserWebChatBridge


BASE_MAIN_WINDOW_TITLE = f"{APP_NAME} - IA Engineering Workspace"


def _truthy_env(name):
    value = os.environ.get(name, "")
    return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}


def _single_instance_bypass_requested():
    return (
        _truthy_env("MEROTEC_FORCE_NEW_INSTANCE")
        or _truthy_env("MEROTEC_HUMAN_TEST_INSTANCE")
        or _truthy_env("MEROTEC_VISUAL_TEST_INSTANCE")
        or bool(os.environ.get("MEROTEC_INSTANCE_TITLE_SUFFIX", "").strip())
    )


def _instance_title_suffix():
    suffix = os.environ.get("MEROTEC_INSTANCE_TITLE_SUFFIX", "").strip()
    if suffix:
        return suffix
    if _truthy_env("MEROTEC_HUMAN_TEST_INSTANCE") or _truthy_env("MEROTEC_VISUAL_TEST_INSTANCE"):
        return f" - teste visual {os.getpid()}"
    return ""


MAIN_WINDOW_TITLE = f"{BASE_MAIN_WINDOW_TITLE}{_instance_title_suffix()}"


def _activate_existing_instance():
    if _single_instance_bypass_requested():
        return False
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, MAIN_WINDOW_TITLE)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


class UniversalApp(AppStateMixin, AiConfigMixin, WorkspaceIntelligenceMixin, AgentActionsMixin, ctk.CTk):
    def __init__(self):
        try:
            os.chdir(PROJECT_ROOT)
        except OSError:
            pass
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(MAIN_WINDOW_TITLE)
        self._configure_initial_window_size()
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
        self.ai_context_memory = []
        self.command_failure_signatures = {}
        self.ai_read_history = {}
        self.ai_search_history = {}
        self.memory_subnet = MemorySubnet(self.current_workspace)
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
        self.streaming_sender = ""
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
        self.voice_capture_active = False
        self.voice_capture_started_at = None
        self.voice_keyword_capture_active = False
        self.voice_keyword_start = "merotec"
        self.voice_keyword_end = "ok"
        self.voice_keyword_listener_enabled = bool(self.settings.get("voice_keyword_listener_enabled", False))
        self.terminal_progress_active = False
        self.terminal_work_count = 0
        self.terminal_work_lock = threading.Lock()
        self.terminal_activity_generation = 0
        self.active_terminal_processes = {}
        self.active_process_lock = threading.Lock()
        self.explorer_visible = True
        self.sidebar_width = 228
        self.explorer_width = 270
        self.explorer_refresh_job = None
        self.editor_font_size = 14
        self.editor_tab_spaces = 4
        self.editor_completion_popup = None
        self.editor_completion_listbox = None
        self.editor_completion_items = []
        self.editor_completion_context = None
        self.editor_completion_prefix = ""
        self.editor_completion_job = None
        self.editor_symbol_cache = []
        self.editor_symbol_cache_signature = None
        self.internal_browser_url = "about:blank"
        self.internal_browser_window = None
        self.internal_browser_process = None
        self.internal_browser_reader_thread = None
        self.internal_browser_requests = {}
        self.browser_element_catalog = {}
        self.internal_browser_ready_event = threading.Event()
        self.internal_browser_started = False
        self.internal_browser_backend = ""
        self.internal_browser_lock = threading.Lock()

        self.engine = UniversalEngine()
        self.attach_internal_web_chat_bridge()
        self.voice = VoiceModule(self.settings)
        self.pm = ProjectManager(str(PROJECTS_DIR))
        self.executor = CodeExecutor()
        self.plugin_manager = None
        self.plugin_statuses = []
        self.plugin_capabilities = {}
        self.load_plugins()

        self.style = get_style_by_name("monokai")

        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self.load_workspace_files()
        self.set_status("Pronto para trabalhar.", "ready")
        self.report_plugin_status()
        self.after(450, self.show_local_subnet_status)
        self.after(900, self.ensure_codex_ready)
        self.after(1400, self.start_voice_keyword_listener)

    def plugin_services(self):
        return {
            "app": self,
            "settings": self.settings,
            "workspace": self.current_workspace,
            "engine": self.engine,
            "voice": self.voice,
            "project_manager": self.pm,
            "executor": self.executor,
        }

    def load_plugins(self):
        (
            self.plugin_manager,
            self.plugin_statuses,
            self.plugin_capabilities,
        ) = initialize_plugins(services=self.plugin_services())
        return self.plugin_statuses

    def report_plugin_status(self):
        for sender, message in build_plugin_report_messages(getattr(self, "plugin_statuses", [])):
            self.add_chat_message(sender, message)
            self.log_agent(message)

    def attach_internal_web_chat_bridge(self):
        """Liga o motor Chat Web ao mesmo WebView2 da janela principal.

        Isso impede que uma tarefa abra outra janela de chat e preserva a
        conversa correta quando o projeto muda.
        """
        engine = getattr(self, "engine", None)
        if engine is None:
            return None
        profile = dict(getattr(engine, "web_chat_profile", {}) or {})
        profile.update({
            "web_chat_url": getattr(engine, "web_chat_url", profile.get("web_chat_url", "https://chatgpt.com/")),
            "web_chat_timeout_seconds": getattr(engine, "web_chat_timeout_seconds", profile.get("web_chat_timeout_seconds", 300)),
            "web_chat_message_chars": getattr(engine, "web_chat_message_chars", profile.get("web_chat_message_chars", 28000)),
            "web_chat_auto_attach_media": getattr(engine, "web_chat_auto_attach_media", profile.get("web_chat_auto_attach_media", True)),
        })
        engine.web_chat_bridge = InternalBrowserWebChatBridge(self, profile)
        return engine.web_chat_bridge

    def _is_configured_web_chat_url(self, url):
        """Retorna True apenas para a origem do Chat Web configurado.

        Páginas locais e navegação comum não devem substituir a conversa
        associada ao projeto.
        """
        try:
            configured = self.web_chat_target_for_workspace(self.current_workspace)
            source_host = (urllib.parse.urlparse(configured).hostname or "").lower()
            target_host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
            return bool(source_host and target_host and source_host == target_host)
        except Exception:
            return False

    def _remember_web_chat_navigation_if_needed(self, url, title=""):
        if not self._is_configured_web_chat_url(url):
            return
        if "HTTP ERROR 431" in str(title or "").upper():
            return
        try:
            self.remember_internal_browser_chat_url(url, title)
        except Exception as exc:
            self.log_agent(f"Não consegui salvar a sessão do Chat Web: {exc}")

    def _available_screen_area(self):
        if sys.platform == "win32":
            try:
                class Rect(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                rect = Rect()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    width = max(1, rect.right - rect.left)
                    height = max(1, rect.bottom - rect.top)
                    return rect.left, rect.top, width, height
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _configure_initial_window_size(self):
        left, top, work_width, work_height = self._available_screen_area()
        width = min(1280, max(1050, work_width - 80))
        height = min(820, max(690, work_height - 72))
        x = left + max(0, (work_width - width) // 2)
        y = top + max(0, (work_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1050, max(900, work_width - 80)), min(620, max(560, work_height - 80)))
        self.after(0, self._maximize_initial_window)
        self.after(180, self._bring_initial_window_to_front)

    def _maximize_initial_window(self):
        try:
            self.state("zoomed")
            return
        except tk.TclError:
            pass
        try:
            self.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass
        left, top, work_width, work_height = self._available_screen_area()
        self.geometry(f"{work_width}x{work_height}+{left}+{top}")

    def _bring_initial_window_to_front(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        if sys.platform == "win32":
            try:
                self.attributes("-topmost", True)
                self.after(650, lambda: self.attributes("-topmost", False))
            except tk.TclError:
                pass

    def _build_menu(self):
        self._configure_native_menu_style()
        self.menu = self._native_menu()

        file_menu = self._native_menu()
        file_menu.add_command(label="Novo projeto...", accelerator="Ctrl+Shift+N", command=self.create_new_project)
        file_menu.add_command(label="Abrir projeto/pasta...", command=self.open_project)
        file_menu.add_command(label="Abrir arquivo externo...", accelerator="Ctrl+O", command=self.open_external_file)
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
        editor_menu.add_separator()
        editor_menu.add_command(label="Sugestoes de codigo", accelerator="Ctrl+Espaco", command=self.show_editor_completion)
        editor_menu.add_command(label="Buscar classe/metodo", accelerator="Ctrl+Shift+O", command=self.show_symbol_palette)
        self.menu.add_cascade(label="Editor", menu=editor_menu)

        ai_menu = self._native_menu()
        ai_menu.add_command(label="Enviar missão ao Chat Web", command=self.send_mission_to_web_chat)
        ai_menu.add_command(label="Importar resposta do Chat Web", command=self.import_web_chat_response)
        ai_menu.add_separator()
        ai_menu.add_command(label="Configurações...", command=self.configure_ai)
        self.menu.add_cascade(label="IA", menu=ai_menu)

        self.visual_menus = [file_menu, view_menu, editor_menu, ai_menu]

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
        self.visual_menu_bar.grid_columnconfigure(4, weight=1)

        menu_items = [
            ("Arquivo", 0),
            ("Visualizar", 1),
            ("Editor", 2),
            ("IA", 3),
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
        ).grid(row=0, column=4, sticky="e", padx=(8, 14), pady=(4, 5))

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
            text=self.ai_status_text(),
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
            wraplength=180,
            justify="left",
        )
        self.ai_status_label.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))

        buttons = [
            ("Configurações", self.configure_ai),
            ("Entrar Codex", self.launch_codex_login),
            ("Atualizar Explorer", self.load_workspace_files),
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
        audio.grid_columnconfigure((0, 1), weight=1)

        listen_button = self._elevated_button(audio, text="Ouvir", width=58, height=32, command=self.play_last_response)
        stop_button = self._elevated_button(audio, text="Parar", width=58, height=32, command=self.stop_audio_playback)
        listen_button.elevation_shadow.grid(row=0, column=0, sticky="ew", padx=2)
        stop_button.elevation_shadow.grid(row=0, column=1, sticky="ew", padx=2)

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
        self._style_text_surface(self.agent_summary, THEME["panel_alt"], THEME["muted"])
        self._show_ctk_textbox_scrollbar(self.agent_summary)
        self._replace_text(self.agent_summary, "Ações do agente aparecem aqui.")

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
        self.explorer_filter.bind("<KeyRelease>", lambda _event: self.load_workspace_files(delay=180))

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

    def _hide_ctk_textbox_scrollbar(self, textbox):
        scrollbar = getattr(textbox, "_y_scrollbar", None)
        if scrollbar is None:
            return
        try:
            scrollbar.configure(width=0, fg_color="transparent")
            scrollbar.grid_remove()
        except tk.TclError:
            pass

    def _style_text_surface(self, widget, bg=None, fg=None):
        target = getattr(widget, "_textbox", widget)
        try:
            target.configure(
                background=bg or THEME["panel_alt"],
                foreground=fg or THEME["text"],
                insertbackground=THEME["accent"],
                selectbackground=THEME["accent_dark"],
                selectforeground="#ffffff",
                inactiveselectbackground="#22364f",
                highlightthickness=0,
                borderwidth=0,
                relief="flat",
            )
        except tk.TclError:
            pass

    def _configure_editor_tabs(self, editor):
        tab = " " * max(1, int(getattr(self, "editor_tab_spaces", 4)))
        try:
            font = tkfont.Font(font=editor.cget("font"))
            editor.configure(tabs=(font.measure(tab),))
        except tk.TclError:
            pass

    def _current_editor_info(self):
        try:
            tab_name = self.tabview.get()
        except (tk.TclError, AttributeError):
            tab_name = "Scratchpad"
        info = self.open_editors.get(tab_name)
        if info:
            return tab_name, info
        if "Scratchpad" in self.open_editors:
            return "Scratchpad", self.open_editors["Scratchpad"]
        return None, None

    def _current_editor(self):
        _tab_name, info = self._current_editor_info()
        return info.get("widget") if info else None

    def _is_editor_text_change_event(self, event):
        keysym = getattr(event, "keysym", "") or ""
        char = getattr(event, "char", "") or ""
        state = int(getattr(event, "state", 0) or 0)
        navigation_keys = {
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
            "Control_L",
            "Control_R",
            "Shift_L",
            "Shift_R",
            "Alt_L",
            "Alt_R",
            "Escape",
            "Caps_Lock",
            "Num_Lock",
            "Scroll_Lock",
        }
        if keysym in navigation_keys:
            return False
        if state & 0x4:
            return keysym.lower() in {"v", "x", "z", "y"}
        return bool(char) or keysym in {"BackSpace", "Delete", "Return", "KP_Enter", "Tab", "ISO_Left_Tab"}

    def _sync_editor_horizontal_scrollbar(self, editor):
        scrollbar = getattr(editor, "_horizontal_scrollbar", None)
        if scrollbar is None:
            return
        try:
            scrollbar.set(*editor.xview())
        except (tk.TclError, ValueError):
            pass

    def _keep_editor_insert_visible(self, editor):
        try:
            editor.see("insert")
            self._sync_editor_horizontal_scrollbar(editor)
        except tk.TclError:
            pass

    def _update_editor_view_after_navigation(self, tab_name):
        info = self.open_editors.get(tab_name)
        if not info:
            return
        editor = info["widget"]
        self._keep_editor_insert_visible(editor)
        self.update_editor_markers(tab_name, lightweight=True)

    def _schedule_editor_content_changed(self, tab_name, delay=120):
        info = self.open_editors.get(tab_name)
        if not info:
            return
        job_id = info.get("content_changed_job")
        if job_id is not None:
            try:
                self.after_cancel(job_id)
            except tk.TclError:
                pass
        self.update_editor_markers(tab_name, lightweight=True)
        info["content_changed_job"] = self.after(delay, lambda name=tab_name: self._flush_editor_content_changed(name))

    def _flush_editor_content_changed(self, tab_name):
        info = self.open_editors.get(tab_name)
        if info is not None:
            info["content_changed_job"] = None
        self._on_editor_content_changed(tab_name)

    def _handle_editor_key_release(self, event, tab_name):
        if self._is_editor_text_change_event(event):
            self._on_editor_key(event, tab_name)
            self._schedule_editor_content_changed(tab_name)
        else:
            self.after(1, lambda name=tab_name: self._update_editor_view_after_navigation(name))
        return None

    def _scroll_editor_horizontal(self, event, tab_name):
        info = self.open_editors.get(tab_name)
        if not info:
            return None
        editor = info["widget"]
        try:
            delta = int(getattr(event, "delta", 0) or 0)
            units = -4 if delta > 0 else 4
            editor.xview_scroll(units, "units")
            self._sync_editor_horizontal_scrollbar(editor)
        except tk.TclError:
            pass
        return "break"

    def _selected_editor_lines(self, editor):
        try:
            first = editor.index("sel.first linestart")
            last = editor.index("sel.last lineend")
            return first, last, True
        except tk.TclError:
            return editor.index("insert linestart"), editor.index("insert lineend"), False

    def indent_editor_selection(self, tab_name, event=None):
        info = self.open_editors.get(tab_name)
        if not info:
            return "break"
        editor = info["widget"]
        indent = " " * max(1, int(getattr(self, "editor_tab_spaces", 4)))
        first, last, had_selection = self._selected_editor_lines(editor)
        try:
            start_line = int(first.split(".")[0])
            end_line = int(last.split(".")[0])
            for line in range(start_line, end_line + 1):
                editor.insert(f"{line}.0", indent)
            if had_selection:
                editor.tag_add("sel", f"{start_line}.0", f"{end_line}.end")
            self._on_editor_key(event, tab_name)
            self._on_editor_content_changed(tab_name)
        except tk.TclError:
            pass
        return "break"

    def outdent_editor_selection(self, tab_name, event=None):
        info = self.open_editors.get(tab_name)
        if not info:
            return "break"
        editor = info["widget"]
        width = max(1, int(getattr(self, "editor_tab_spaces", 4)))
        first, last, had_selection = self._selected_editor_lines(editor)
        try:
            start_line = int(first.split(".")[0])
            end_line = int(last.split(".")[0])
            for line in range(start_line, end_line + 1):
                line_start = f"{line}.0"
                text = editor.get(line_start, f"{line}.0+{width}c")
                remove_count = len(text) - len(text.lstrip(" "))
                if remove_count:
                    editor.delete(line_start, f"{line}.0+{min(remove_count, width)}c")
            if had_selection:
                editor.tag_add("sel", f"{start_line}.0", f"{end_line}.end")
            self._on_editor_key(event, tab_name)
            self._on_editor_content_changed(tab_name)
        except tk.TclError:
            pass
        return "break"

    def smart_editor_return(self, tab_name, event=None):
        info = self.open_editors.get(tab_name)
        if not info:
            return None
        editor = info["widget"]
        try:
            line = editor.get("insert linestart", "insert")
            indent = re.match(r"\s*", line).group(0)
            extra = " " * max(1, int(getattr(self, "editor_tab_spaces", 4))) if line.rstrip().endswith(":") else ""
            before = editor.get("insert-1c", "insert")
            after = editor.get("insert", "insert+1c")
            if (before, after) in {("{", "}"), ("[", "]"), ("(", ")")}:
                inner = indent + " " * max(1, int(getattr(self, "editor_tab_spaces", 4)))
                editor.insert("insert", "\n" + inner + "\n" + indent)
                editor.mark_set("insert", "insert-1l lineend")
            else:
                editor.insert("insert", "\n" + indent + extra)
            self._on_editor_key(event, tab_name)
            self._schedule_editor_content_changed(tab_name)
            return "break"
        except tk.TclError:
            return None

    def editor_auto_pair(self, tab_name, event=None):
        if event is None or getattr(event, "state", 0) & 0x4:
            return None
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        info = self.open_editors.get(tab_name)
        if not info:
            return None
        editor = info["widget"]
        char = getattr(event, "char", "")
        keysym = getattr(event, "keysym", "")
        try:
            if keysym == "BackSpace":
                before = editor.get("insert-1c", "insert")
                after = editor.get("insert", "insert+1c")
                if pairs.get(before) == after:
                    editor.delete("insert-1c", "insert+1c")
                    self._on_editor_key(event, tab_name)
                    self._schedule_editor_content_changed(tab_name)
                    return "break"
                return None

            if char in pairs.values() and editor.get("insert", "insert+1c") == char:
                editor.mark_set("insert", "insert+1c")
                self.update_editor_markers(tab_name)
                return "break"

            closing = pairs.get(char)
            if not closing:
                return None
            try:
                selected = editor.get("sel.first", "sel.last")
                editor.delete("sel.first", "sel.last")
                editor.insert("insert", char + selected + closing)
                editor.tag_add("sel", "insert-%dc" % (len(selected) + 1), "insert-1c")
            except tk.TclError:
                editor.insert("insert", char + closing)
                editor.mark_set("insert", "insert-1c")
            self._on_editor_key(event, tab_name)
            self._schedule_editor_content_changed(tab_name)
            return "break"
        except tk.TclError:
            return None

    def toggle_editor_comment(self, tab_name, event=None):
        info = self.open_editors.get(tab_name)
        if not info:
            return "break"
        editor = info["widget"]
        first, last, had_selection = self._selected_editor_lines(editor)
        try:
            start_line = int(first.split(".")[0])
            end_line = int(last.split(".")[0])
            lines = [editor.get(f"{line}.0", f"{line}.end") for line in range(start_line, end_line + 1)]
            non_empty = [line for line in lines if line.strip()]
            should_uncomment = bool(non_empty) and all(line.lstrip().startswith("#") for line in non_empty)
            for line_no in range(start_line, end_line + 1):
                text = editor.get(f"{line_no}.0", f"{line_no}.end")
                if not text.strip():
                    continue
                indent = len(text) - len(text.lstrip(" "))
                pos = f"{line_no}.{indent}"
                if should_uncomment:
                    if editor.get(pos, f"{line_no}.{indent + 2}") == "# ":
                        editor.delete(pos, f"{line_no}.{indent + 2}")
                    elif editor.get(pos, f"{line_no}.{indent + 1}") == "#":
                        editor.delete(pos, f"{line_no}.{indent + 1}")
                else:
                    editor.insert(pos, "# ")
            if had_selection:
                editor.tag_add("sel", f"{start_line}.0", f"{end_line}.end")
            self._on_editor_key(event, tab_name)
            self._on_editor_content_changed(tab_name)
        except tk.TclError:
            pass
        return "break"

    def show_editor_completion(self, event=None):
        """Exibe a janela popup com sugestões de código para o editor atual."""
        tab_name, info = self._current_editor_info()
        if not info:
            return "break"

        editor = info["widget"]
        try:
            text = editor.get("1.0", "end-1c")
            cursor_offset = len(editor.get("1.0", "insert"))
        except tk.TclError:
            return "break"
        suggestions = completion_items(text, info.get("path"), cursor_offset)
        if not suggestions:
            self._hide_editor_completion()
            self.set_status("Nenhuma sugestão para o contexto atual.", "warning")
            return "break"

        self._hide_editor_completion()
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=THEME["border"])
        popup.attributes("-topmost", True)
        listbox = tk.Listbox(
            popup,
            height=min(10, len(suggestions)),
            width=34,
            activestyle="none",
            bg=THEME["panel_alt"],
            fg=THEME["text"],
            selectbackground=THEME["accent_dark"],
            selectforeground="#ffffff",
            borderwidth=1,
            relief="solid",
            font=("Consolas", 11),
        )
        listbox.pack(fill="both", expand=True)
        for suggestion in suggestions:
            listbox.insert("end", suggestion)
        listbox.selection_set(0)
        listbox._editor = editor
        listbox._prefix = word_prefix(text, cursor_offset)
        listbox.bind("<Return>", lambda _event: self._accept_editor_completion(listbox))
        listbox.bind("<Double-Button-1>", lambda _event: self._accept_editor_completion(listbox))
        listbox.bind("<Escape>", lambda _event: self._hide_editor_completion(force=True))
        try:
            bbox = editor.bbox("insert") or (0, 0, 0, 20)
            x = editor.winfo_rootx() + bbox[0]
            y = editor.winfo_rooty() + bbox[1] + bbox[3] + 4
            popup.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        self.editor_completion_popup = popup
        self.set_status(f"{len(suggestions)} sugestão(ões) locais.", "ready")
        listbox.focus_set()
        return "break"

    def _accept_editor_completion(self, listbox):
        selection = listbox.curselection()
        if not selection:
            return "break"
        value = listbox.get(selection[0])
        editor = listbox._editor
        prefix = listbox._prefix
        try:
            if prefix:
                editor.delete(f"insert-{len(prefix)}c", "insert")
            editor.insert("insert", value)
            editor.focus_set()
            tab_name, _info = self._current_editor_info()
            if tab_name:
                self._schedule_editor_content_changed(tab_name, delay=20)
        except tk.TclError:
            pass
        self._hide_editor_completion()
        return "break"

    def _hide_editor_completion(self, force=False):
        popup = getattr(self, "editor_completion_popup", None)
        if popup is not None and not force:
            try:
                focused = self.focus_get()
                if focused is not None and focused.winfo_toplevel() == popup:
                    return "break"
            except tk.TclError:
                pass
        self.editor_completion_popup = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass
        return "break"

    def show_symbol_palette(self, event=None):
        """Exibe a paleta de busca para classes e métodos no arquivo atual."""
        tab_name, info = self._current_editor_info()
        if not info:
            return "break"

        editor = info["widget"]
        try:
            symbols = extract_symbols(editor.get("1.0", "end-1c"), info.get("path"))
        except tk.TclError:
            return "break"
        if not symbols:
            self.set_status("Nenhum símbolo encontrado no arquivo atual.", "warning")
            return "break"

        dialog = tk.Toplevel(self)
        dialog.title(f"Símbolos — {tab_name}")
        dialog.transient(self)
        dialog.configure(bg=THEME["panel"])
        dialog.geometry("560x420")
        entry = tk.Entry(
            dialog, bg=THEME["panel_alt"], fg=THEME["text"],
            insertbackground=THEME["accent"], font=("Segoe UI", 12),
        )
        entry.pack(fill="x", padx=12, pady=(12, 8), ipady=6)
        listbox = tk.Listbox(
            dialog, bg=THEME["panel_alt"], fg=THEME["text"],
            selectbackground=THEME["accent_dark"], selectforeground="#fff",
            font=("Consolas", 11), activestyle="none",
        )
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        visible = []

        def refresh(_event=None):
            query = entry.get().strip().lower()
            visible[:] = [symbol for symbol in symbols if query in symbol.name.lower() or query in symbol.kind.lower()]
            listbox.delete(0, "end")
            for symbol in visible:
                listbox.insert("end", f"{symbol.kind:<10} {symbol.name}{symbol.detail}  · linha {symbol.line}")
            if visible:
                listbox.selection_set(0)

        def navigate(_event=None):
            selection = listbox.curselection()
            if not selection:
                return "break"
            symbol = visible[selection[0]]
            try:
                editor.mark_set("insert", f"{symbol.line}.0")
                editor.see(f"{symbol.line}.0")
                editor.focus_set()
                self.update_editor_markers(tab_name)
            except tk.TclError:
                pass
            dialog.destroy()
            self.set_status(f"{symbol.kind.title()} {symbol.name} — linha {symbol.line}.", "ready")
            return "break"

        entry.bind("<KeyRelease>", refresh)
        entry.bind("<Return>", navigate)
        entry.bind("<Down>", lambda _event: (listbox.focus_set(), "break")[1])
        listbox.bind("<Return>", navigate)
        listbox.bind("<Double-Button-1>", navigate)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        refresh()
        entry.focus_set()
        self.set_status(f"{len(symbols)} símbolo(s) encontrado(s).", "ready")
        return "break"

    def show_editor_find_bar(self, event=None):
        tab_name, info = self._current_editor_info()
        if not info:
            return "break"
        editor = info["widget"]
        try:
            initial = editor.get("sel.first", "sel.last")
        except tk.TclError:
            initial = getattr(editor, "_search_query", "")
        query = simpledialog.askstring(APP_NAME, "Buscar no editor:", initialvalue=initial, parent=self)
        if query is not None:
            editor._search_query = query
            self.find_in_current_editor(1)
        return "break"

    def find_in_current_editor(self, direction=1):
        tab_name, info = self._current_editor_info()
        if not info:
            return "break"
        editor = info["widget"]
        query = getattr(editor, "_search_query", "")
        if not query:
            return self.show_editor_find_bar()

        try:
            editor.tag_remove("search_match", "1.0", "end")
            editor.tag_remove("search_current", "1.0", "end")
            matches = []
            start = "1.0"
            while True:
                pos = editor.search(query, start, stopindex="end", nocase=True)
                if not pos:
                    break
                end = f"{pos}+{len(query)}c"
                matches.append((pos, end))
                editor.tag_add("search_match", pos, end)
                start = end

            editor._search_matches = matches
            if not matches:
                editor._search_match_index = -1
                self.set_status("Busca sem resultados.", "warning")
                return "break"

            current = getattr(editor, "_search_match_index", -1)
            current = (current + (1 if direction >= 0 else -1)) % len(matches)
            editor._search_match_index = current
            pos, end = matches[current]
            editor.tag_add("search_current", pos, end)
            editor.mark_set("insert", pos)
            editor.see(pos)
            self.set_status(f"{current + 1}/{len(matches)} resultado(s) em {tab_name}.", "ready")
        except tk.TclError:
            pass
        return "break"

    def zoom_current_editor(self, delta):
        if delta == 0:
            self.editor_font_size = 14
        else:
            self.editor_font_size = max(9, min(28, self.editor_font_size + delta))
        editor_font = ("Consolas", self.editor_font_size)
        for info in self.open_editors.values():
            editor = info.get("widget")
            if not editor:
                continue
            try:
                editor.configure(font=editor_font)
                self._configure_editor_tabs(editor)
                line_numbers = getattr(editor, "_line_numbers", None)
                if line_numbers is not None:
                    line_numbers.configure(font=editor_font)
            except tk.TclError:
                pass
        self.update_editor_markers(self._current_editor_info()[0] or "Scratchpad")
        return "break"

    def _sync_visible_scrollbar(self, scrollbar, first, last):
        try:
            scrollbar.set(first, last)
            scrollbar.grid()
        except (tk.TclError, ValueError):
            pass

    def _create_long_press_scroll_controls(self, parent, target_yview, scroll_once, grid_kwargs):
        scroll_step = 4
        hold_delay_ms = 320
        repeat_delay_ms = 55
        repeat_job = {"id": None, "active": False}

        def scroll_target(direction):
            scroll_once(direction * scroll_step)

        def cancel_scroll_repeat():
            repeat_job["active"] = False
            job_id = repeat_job.get("id")
            repeat_job["id"] = None
            if job_id is not None:
                try:
                    self.after_cancel(job_id)
                except tk.TclError:
                    pass

        def repeat_scroll(direction):
            if not repeat_job["active"]:
                return
            for _ in range(6):
                scroll_target(direction)
            repeat_job["id"] = self.after(repeat_delay_ms, lambda: repeat_scroll(direction))

        def start_scroll_press(direction):
            cancel_scroll_repeat()
            scroll_target(direction)
            repeat_job["active"] = True
            repeat_job["id"] = self.after(hold_delay_ms, lambda: repeat_scroll(direction))

        def bind_long_press(button, direction):
            button.bind("<ButtonPress-1>", lambda _event: start_scroll_press(direction), add="+")
            button.bind("<ButtonRelease-1>", lambda _event: cancel_scroll_repeat(), add="+")
            button.bind("<Leave>", lambda _event: cancel_scroll_repeat(), add="+")

        scroll_controls = ctk.CTkFrame(parent, fg_color="#111318", width=34, corner_radius=0)
        scroll_controls.grid(**grid_kwargs)
        scroll_controls.grid_columnconfigure(0, weight=1)
        scroll_controls.grid_rowconfigure(1, weight=1)

        scroll_up = ctk.CTkButton(
            scroll_controls,
            text="^",
            width=30,
            height=30,
            corner_radius=4,
            fg_color="#172033",
            hover_color="#24324d",
            text_color=THEME["accent"],
        )
        scroll_up.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 4))
        bind_long_press(scroll_up, -1)

        scroll_track = ctk.CTkFrame(scroll_controls, fg_color="#111318", width=30, corner_radius=0)
        scroll_track.grid(row=1, column=0, sticky="nsew", padx=2, pady=4)
        scroll_track.grid_columnconfigure(0, weight=1)
        scroll_track.grid_columnconfigure(2, weight=1)
        scroll_track.grid_rowconfigure(0, weight=1)

        scroll_bar = ctk.CTkScrollbar(
            scroll_track,
            orientation="vertical",
            width=18,
            command=target_yview,
            fg_color="#111318",
            button_color=THEME["accent_dark"],
            button_hover_color=THEME["accent"],
        )
        scroll_bar.grid(row=0, column=1, sticky="ns")

        scroll_down = ctk.CTkButton(
            scroll_controls,
            text="v",
            width=30,
            height=30,
            corner_radius=4,
            fg_color="#172033",
            hover_color="#24324d",
            text_color=THEME["accent"],
        )
        scroll_down.grid(row=2, column=0, sticky="ew", padx=2, pady=(4, 2))
        bind_long_press(scroll_down, 1)

        return scroll_controls, scroll_bar

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

    def _show_ctk_scrollable_frame_scrollbar(self, scrollable_frame):
        scrollbar = getattr(scrollable_frame, "_scrollbar", None)
        canvas = getattr(scrollable_frame, "_parent_canvas", None)
        if scrollbar is None or canvas is None:
            return
        canvas.configure(yscrollcommand=lambda first, last: self._sync_visible_scrollbar(scrollbar, first, last))
        try:
            scrollbar.configure(width=16, fg_color=THEME["panel_alt"], button_color=THEME["accent_dark"], button_hover_color=THEME["accent"])
            scrollbar.grid()
            self.after(1, lambda: self._sync_visible_scrollbar(scrollbar, *canvas.yview()))
        except tk.TclError:
            pass

    def _install_chat_scroll_controls(self):
        canvas = getattr(self.chat_history, "_parent_canvas", None)
        if canvas is None:
            return

        def scroll_chat(units):
            try:
                canvas.yview_scroll(units, "units")
            except tk.TclError:
                pass

        scroll_controls, scroll_bar = self._create_long_press_scroll_controls(
            self.tab_chat,
            canvas.yview,
            scroll_chat,
            {"row": 0, "column": 1, "sticky": "ns", "padx": (0, 8), "pady": 8},
        )
        self.chat_scroll_controls = scroll_controls
        canvas.configure(yscrollcommand=lambda first, last: scroll_bar.set(first, last))
        self.after(1, lambda: scroll_bar.set(*canvas.yview()))
        return

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

    def _style_chat_panel_layers(self):
        try:
            self.chat_history.configure(fg_color=THEME["bg"])
            canvas = getattr(self.chat_history, "_parent_canvas", None)
            if canvas is not None:
                canvas.configure(bg=THEME["bg"], highlightthickness=0, bd=0)
            parent_frame = getattr(self.chat_history, "_parent_frame", None)
            if parent_frame is not None:
                parent_frame.configure(fg_color=THEME["bg"])
            self._autohide_ctk_scrollable_frame_scrollbar(self.chat_history)
        except tk.TclError:
            pass

    def _build_main_tabs(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=THEME["panel"],
            text_color=THEME["text"],
            border_width=1,
            border_color=THEME["border"],
            segmented_button_fg_color=THEME["nav_button_border"],
            segmented_button_selected_color=THEME["accent_dark"],
            segmented_button_selected_hover_color=THEME["accent"],
            segmented_button_unselected_color=THEME["panel_alt"],
            segmented_button_unselected_hover_color=THEME["panel_soft"],
        )
        self.tabview.grid(row=1, column=2, sticky="nsew", padx=12, pady=(12, 8))
        self.tabview._segmented_button.configure(border_width=2)

        self.tab_chat = self.tabview.add("Chat AI")
        self.tab_editor = self.tabview.add("Scratchpad")
        self.tab_terminal = self.tabview.add("Terminal Local")
        self.tab_browser = self.tabview.add("Navegador")
        self.tab_agent_log = self.tabview.add("Log do Agente")

        for tab in (self.tab_chat, self.tab_editor, self.tab_terminal, self.tab_browser, self.tab_agent_log):
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)

        self.chat_history = ctk.CTkScrollableFrame(self.tab_chat, fg_color=THEME["bg"])
        self.chat_history.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._style_chat_panel_layers()
        self._autohide_ctk_scrollable_frame_scrollbar(self.chat_history)
        self._install_chat_scroll_controls()

        self.code_editor_frame, self.code_editor = self._create_editor(self.tab_editor, "Scratchpad")
        self.code_editor_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.code_editor.insert("1.0", SCRATCHPAD_DEFAULT_TEXT)
        self.open_editors["Scratchpad"] = {"widget": self.code_editor, "path": None, "dirty": False}
        self.update_editor_markers("Scratchpad")

        self.local_term_out = ctk.CTkTextbox(
            self.tab_terminal,
            fg_color=THEME["terminal"],
            text_color="#62f28f",
            font=("Consolas", 13),
            wrap="word",
        )
        self.local_term_out.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._style_text_surface(self.local_term_out, THEME["terminal"], "#62f28f")
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
        self.terminal_cancel_button = self._elevated_button(
            self.terminal_activity_frame,
            text="Cancelar terminal",
            width=136,
            height=28,
            command=self.cancel_terminal_command,
            fg_color=THEME["danger"],
            hover_color="#b83737",
        )
        self.terminal_cancel_button.elevation_shadow.grid(row=0, column=1, rowspan=2, sticky="e", padx=8, pady=6)
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

        self._build_internal_browser_tab()

        self.agent_log = ctk.CTkTextbox(
            self.tab_agent_log,
            fg_color=THEME["terminal"],
            text_color="#d7dee9",
            font=("Consolas", 12),
            wrap="word",
        )
        self.agent_log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._style_text_surface(self.agent_log, THEME["terminal"], "#d7dee9")
        self._show_ctk_textbox_scrollbar(self.agent_log)
        self._replace_text(self.agent_log, "Log iniciado.\n")

    def _build_internal_browser_tab(self):
        self.tab_browser.grid_rowconfigure(1, weight=1)
        self.tab_browser.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(self.tab_browser, fg_color=THEME["panel_alt"], corner_radius=6)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        toolbar.grid_columnconfigure(0, weight=1)

        self.browser_url_entry = ctk.CTkEntry(
            toolbar,
            placeholder_text="https:// ou http://127.0.0.1:porta",
            font=("Segoe UI", 13),
            fg_color=THEME["panel"],
            border_color=THEME["border"],
            text_color=THEME["text"],
            height=34,
        )
        self.browser_url_entry.grid(row=0, column=0, sticky="ew", padx=(8, 6), pady=8)
        self.browser_url_entry.bind("<Return>", self.browse_internal_url_from_entry)

        back_button = self._elevated_button(toolbar, text="Voltar", width=70, height=30, command=self.browser_go_back)
        back_button.elevation_shadow.grid(row=0, column=1, padx=4, pady=8)

        forward_button = self._elevated_button(toolbar, text="Avancar", width=76, height=30, command=self.browser_go_forward)
        forward_button.elevation_shadow.grid(row=0, column=2, padx=4, pady=8)

        go_button = self._elevated_button(toolbar, text="Ir", width=58, height=30, command=self.browse_internal_url_from_entry)
        go_button.elevation_shadow.grid(row=0, column=3, padx=4, pady=8)

        reload_button = self._elevated_button(toolbar, text="Recarregar", width=96, height=30, command=self.reload_internal_browser)
        reload_button.elevation_shadow.grid(row=0, column=4, padx=4, pady=8)

        external_button = self._elevated_button(toolbar, text="Externo", width=78, height=30, command=self.open_current_browser_url_external)
        external_button.elevation_shadow.grid(row=0, column=5, padx=(4, 8), pady=8)

        body = ctk.CTkFrame(self.tab_browser, fg_color=THEME["bg"], corner_radius=6, border_width=1, border_color=THEME["border"])
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self.browser_status_label = ctk.CTkLabel(
            body,
            text="Navegador interno pronto para URLs locais ou web.",
            text_color=THEME["text"],
            anchor="w",
            justify="left",
            font=("Segoe UI", 13, "bold"),
        )
        self.browser_status_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))

        self.browser_info = ctk.CTkTextbox(
            body,
            fg_color=THEME["terminal"],
            text_color="#d7dee9",
            font=("Consolas", 12),
            wrap="word",
        )
        self.browser_info.grid(row=1, column=0, sticky="nsew", padx=14, pady=(8, 14))
        self._style_text_surface(self.browser_info, THEME["terminal"], "#d7dee9")
        self._replace_text(
            self.browser_info,
            "Navegador interno da Merotec IA IDE\n\n"
            "- URLs abertas pela IA via [OPEN_URL] aparecem aqui.\n"
            "- O perfil Chat Web usa esta mesma janela para enviar tarefas, anexar prints e ler respostas.\n"
            "- A URL da conversa é memorizada por projeto; a IDE não clica em Nova conversa automaticamente.\n"
            "- A sessão e os cookies ficam preservados entre as aberturas.\n"
            "- Se o motor interno não estiver disponível, a IDE avisa e pode usar o navegador externo como fallback.\n",
        )

    def normalize_internal_browser_url(self, url):
        raw = str(url or "").strip().strip("\"'")
        if not raw:
            return ""
        if raw.lower() == "about:blank":
            return "about:blank"
        candidate = Path(raw)
        try:
            if candidate.exists():
                return candidate.resolve().as_uri()
        except OSError:
            pass
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
            host = raw.split("/", 1)[0].lower()
            scheme = "http" if host.startswith(("localhost", "127.0.0.1", "0.0.0.0", "[::1]")) else "https"
            raw = f"{scheme}://{raw}"
        return raw

    def browse_internal_url_from_entry(self, event=None):
        url = self.browser_url_entry.get().strip()
        self.open_internal_browser(url, source="usuario")
        return "break"

    def reload_internal_browser(self):
        if not self._send_internal_browser_command("reload"):
            self.open_internal_browser(self.internal_browser_url, source="reload")

    def browser_go_back(self):
        self._send_internal_browser_command("back")

    def browser_go_forward(self):
        self._send_internal_browser_command("forward")

    def open_current_browser_url_external(self):
        url = self.normalize_internal_browser_url(self.internal_browser_url)
        if not url:
            return
        webbrowser.open(url, new=1)
        self.set_internal_browser_status(f"Abrindo fallback externo: {url}", "warning")

    def set_internal_browser_status(self, message, kind="ready"):
        def update():
            label = getattr(self, "browser_status_label", None)
            textbox = getattr(self, "browser_info", None)
            if label is not None:
                color = THEME["warning"] if kind == "warning" else THEME["error"] if kind == "error" else THEME["text"]
                label.configure(text=message, text_color=color)
            if textbox is not None:
                try:
                    textbox.insert("end", f"\n{datetime.now().strftime('%H:%M:%S')} - {message}")
                    textbox.see("end")
                except tk.TclError:
                    pass

        self.after(0, update)

    def open_internal_browser(self, url, source="IA"):
        normalized = self.normalize_internal_browser_url(url)
        if not normalized:
            self.set_internal_browser_status("URL vazia recebida pelo navegador interno.", "error")
            return ""

        # Reabrir a mesma URL reiniciava páginas SPA e podia interromper a
        # conversa corrente. Para a sessão já aberta, apenas trazemos a janela
        # para frente; navegação acontece somente quando o destino muda.
        current = str(getattr(self, "internal_browser_url", "") or "")
        process = getattr(self, "internal_browser_process", None)
        if (
            process is not None
            and process.poll() is None
            and current.rstrip("/") == normalized.rstrip("/")
        ):
            self.internal_browser_url = normalized
            self._send_internal_browser_command("focus")
            self.set_internal_browser_status(
                f"Navegador interno já está na conversa atual: {normalized}", "ready"
            )
            return normalized

        self.internal_browser_url = normalized
        self.browser_element_catalog = {}
        try:
            self.tabview.set("Navegador")
            self.browser_url_entry.delete(0, "end")
            self.browser_url_entry.insert(0, normalized)
        except tk.TclError:
            pass

        opened, detail = self._open_pywebview_browser(normalized)
        if opened:
            self.internal_browser_backend = "pywebview"
            self.set_internal_browser_status(f"Iniciando navegador WebView2: {normalized}", "ready")
            return normalized

        self.internal_browser_backend = "external-fallback"
        self.set_internal_browser_status(
            f"Motor interno indisponivel ({detail}). Abrindo no navegador externo como fallback: {normalized}",
            "warning",
        )
        try:
            webbrowser.open(normalized, new=1)
        except Exception as exc:
            self.set_internal_browser_status(f"Falha ao abrir fallback externo: {exc}", "error")
            return ""
        return normalized

    def _open_pywebview_browser(self, url):
        with self.internal_browser_lock:
            process = self.internal_browser_process
            if process is not None and process.poll() is None:
                entry_url = ""
                try:
                    profile = self.settings.get("ai_profiles", {}).get("web_chat", {})
                    entry_url = str(profile.get("web_chat_url") or self.settings.get("web_chat_url") or "")
                except Exception:
                    entry_url = ""
                if self._write_internal_browser_command(process, "navigate", url=url, entry_url=entry_url):
                    return True, "url enviada"

            helper = PROJECT_ROOT / "modules" / "browser_runtime.py"
            if not helper.exists():
                return False, f"componente ausente: {helper.name}"

            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                process = subprocess.Popen(
                    [sys.executable, "-u", "-m", "modules.browser_runtime", "--url", url],
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except Exception as exc:
                return False, f"nao consegui iniciar o WebView2: {exc}"

            self.internal_browser_process = process
            self.internal_browser_started = True
            self.internal_browser_ready_event.clear()
            reader = threading.Thread(
                target=self._read_internal_browser_events,
                args=(process,),
                daemon=True,
            )
            self.internal_browser_reader_thread = reader
            reader.start()
        return True, "processo iniciado"

    def _write_internal_browser_command(self, process, action, **payload):
        try:
            if process is None or process.poll() is not None or process.stdin is None:
                return False
            process.stdin.write(json.dumps({"action": action, **payload}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def _send_internal_browser_command(self, action, **payload):
        with self.internal_browser_lock:
            return self._write_internal_browser_command(self.internal_browser_process, action, **payload)

    def request_internal_browser_action(self, action, payload=None, callback=None):
        request_id = f"browser-{time.time_ns()}"
        with self.internal_browser_lock:
            process = self.internal_browser_process
            if process is None or process.poll() is not None:
                return ""
            if callback is not None:
                self.internal_browser_requests[request_id] = callback
            sent = self._write_internal_browser_command(
                process,
                action,
                request_id=request_id,
                **(payload or {}),
            )
            if not sent:
                self.internal_browser_requests.pop(request_id, None)
                return ""
        return request_id

    def browser_ai_fallback_enabled(self):
        return bool(self.settings.get("browser_ai_fallback_enabled", True))

    def _browser_ai_fallback_prompt(self, command, code_context):
        max_chars = int(self.settings.get("browser_ai_fallback_max_context_chars", 18000) or 18000)
        context = self.redact_local_training_text(str(code_context or ""))
        if len(context) > max_chars:
            head = max_chars * 2 // 3
            tail = max_chars - head
            context = context[:head] + "\n[...contexto reduzido pela IDE...]\n" + context[-tail:]
        return (
            "Voce e o agente de contingencia da Merotec IA IDE. Continue a mesma missao de engenharia.\n"
            "Escolha a proxima acao concreta e responda com exatamente uma tag da IDE quando precisar agir: "
            "READ, SEARCH_TEXT, WEB_SEARCH, WRITE, REPLACE, EXECUTE, OPEN_URL, BROWSER_INSPECT, "
            "BROWSER_CLICK, BROWSER_TYPE, BROWSER_SCROLL, SCREENSHOT ou HUMAN_TEST. "
            "Se a tarefa terminou, responda com a conclusao final objetiva. Nao use placeholders.\n\n"
            f"MISSAO ORIGINAL:\n{command}\n\n"
            f"CONTEXTO ATUAL DA IDE:\n{context}"
        )

    def request_browser_ai_fallback(self, command, code_context=None, task_id=None):
        if not self.browser_ai_fallback_enabled() or self.is_task_cancelled(task_id):
            return ""
        url = str(self.settings.get("browser_ai_fallback_url") or "https://chatgpt.com/").strip()
        timeout = max(30, min(600, int(self.settings.get("browser_ai_fallback_timeout_seconds", 240) or 240)))

        try:
            current_host = (urllib.parse.urlparse(self.internal_browser_url).hostname or "").lower()
            fallback_host = (urllib.parse.urlparse(url).hostname or "").lower()
        except ValueError:
            current_host = ""
            fallback_host = ""

        process = self.internal_browser_process
        needs_open = process is None or process.poll() is not None or current_host != fallback_host
        if needs_open:
            opened = threading.Event()
            outcome = {"url": ""}

            def open_chat():
                try:
                    outcome["url"] = self.open_internal_browser(url, source="fallback IA")
                finally:
                    opened.set()

            self.after(0, open_chat)
            if not opened.wait(timeout=15) or not outcome["url"]:
                return ""
        if not self.internal_browser_ready_event.wait(timeout=35):
            self.log_agent("Fallback pelo navegador indisponivel: WebView2 nao ficou pronto.")
            return ""

        completed = threading.Event()
        result_holder = {}

        def receive(event):
            result_holder["event"] = event
            completed.set()

        prompt = self._browser_ai_fallback_prompt(command, code_context)
        request_id = self.request_internal_browser_action(
            "chat",
            payload={"prompt": prompt, "timeout": timeout},
            callback=receive,
        )
        if not request_id:
            return ""
        self.log_agent(f"Fallback de IA enviado ao chat web: {fallback_host or url}")
        self.set_status("Sem cota nos provedores; aguardando IA pelo navegador...", "busy")
        deadline = time.time() + timeout + 30
        while not completed.wait(timeout=1):
            if self.is_task_cancelled(task_id):
                self.log_agent("Fallback pelo chat web cancelado com a tarefa.")
                return ""
            if time.time() >= deadline:
                self.log_agent("Fallback pelo chat web expirou sem resposta.")
                return ""
        event = result_holder.get("event") or {}
        raw = event.get("result", "")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {"response": raw}
        response = str((raw or {}).get("response") or "").strip() if isinstance(raw, dict) else ""
        if response:
            self.log_agent("Fallback pelo chat web respondeu; retomando o loop da IDE.")
            return f"[Fallback navegador: {fallback_host or 'chat web'}]\n\n{response}"
        self.log_agent(f"Fallback pelo chat web falhou: {(raw or {}).get('error', 'sem resposta') if isinstance(raw, dict) else 'sem resposta'}")
        return ""

    def _read_internal_browser_events(self, process):
        try:
            if process.stdout is None:
                return
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = event.get("event")
                if name == "ready":
                    self.internal_browser_ready_event.set()
                    self.set_internal_browser_status(
                        "Navegador WebView2 pronto — use cliques e teclado normalmente.",
                        "ready",
                    )
                elif name == "navigated":
                    current = str(event.get("url") or self.internal_browser_url)
                    title = str(event.get("title") or "")
                    self.internal_browser_url = current
                    if event.get("recovered_from_http_431"):
                        self.set_internal_browser_status(
                            "Chat Web recuperado: cookies do WebView2 limpos apos HTTP 431.",
                            "warning",
                        )
                    else:
                        self.set_internal_browser_status(f"Pagina aberta: {current}", "ready")
                    if not event.get("http_error"):
                        self.after(
                            0,
                            lambda url=current, page_title=title: self._remember_web_chat_navigation_if_needed(url, page_title),
                        )
                elif name in {"error", "command_error"}:
                    message = str(event.get("message") or "Falha no navegador WebView2.")
                    self.set_internal_browser_status(message, "error")
                    request_id = str(event.get("request_id") or "")
                    if request_id:
                        failure_event = {
                            "event": "browser_result",
                            "request_id": request_id,
                            "action": str(event.get("action") or "chat"),
                            "ok": False,
                            "result": json.dumps({"ok": False, "error": message}, ensure_ascii=False),
                        }
                        with self.internal_browser_lock:
                            callback = self.internal_browser_requests.pop(request_id, None)
                        if callable(callback):
                            callback(failure_event)
                elif name == "browser_progress":
                    request_id = str(event.get("request_id") or "")
                    phase = str(event.get("phase") or "working")
                    message = str(event.get("message") or "Chat Web processando a tarefa.")
                    # Progresso não resolve o callback: ele apenas mantém a UI
                    # honesta enquanto o WebView aguarda o provedor externo.
                    self.set_internal_browser_status(message, "busy")
                    if phase in {"attachment_paste", "attachment_verified", "sent", "waiting", "timeout"}:
                        self.log_agent(f"Chat Web [{request_id or 'sem-id'}] {phase}: {message}")
                elif name == "browser_result":
                    request_id = str(event.get("request_id") or "")
                    with self.internal_browser_lock:
                        callback = self.internal_browser_requests.pop(request_id, None)
                    raw_result = event.get("result", "")
                    try:
                        decoded_result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
                    except json.JSONDecodeError:
                        decoded_result = {}
                    if isinstance(decoded_result, dict):
                        result_url = str(decoded_result.get("url") or "")
                        result_title = str(decoded_result.get("title") or "")
                        if result_url:
                            self.internal_browser_url = result_url
                            self.after(0, lambda url=result_url, title=result_title: self._remember_web_chat_navigation_if_needed(url, title))
                    if callable(callback):
                        try:
                            callback(event)
                        except Exception as exc:
                            self.set_internal_browser_status(f"Falha ao processar automacao: {exc}", "error")
                elif name == "closed":
                    self.set_internal_browser_status("Janela do navegador fechada.", "warning")
                    with self.internal_browser_lock:
                        if self.internal_browser_process is process:
                            self.internal_browser_process = None
                            self.internal_browser_started = False
                            self.internal_browser_requests.clear()
                            self.internal_browser_ready_event.clear()
                    try:
                        if process.stdin is not None:
                            process.stdin.close()
                    except OSError:
                        pass
        finally:
            pending_callbacks = []
            with self.internal_browser_lock:
                if self.internal_browser_process is process:
                    pending_callbacks = list(self.internal_browser_requests.values())
                    self.internal_browser_requests.clear()
                    self.internal_browser_process = None
                    self.internal_browser_started = False
                    self.internal_browser_ready_event.clear()
            for callback in pending_callbacks:
                try:
                    callback({
                        "event": "browser_result",
                        "ok": False,
                        "result": json.dumps(
                            {"ok": False, "error": "O navegador interno foi encerrado durante a tarefa."},
                            ensure_ascii=False,
                        ),
                    })
                except Exception:
                    pass

    def close_internal_browser(self):
        with self.internal_browser_lock:
            process = self.internal_browser_process
            if process is None or process.poll() is not None:
                return
            self._write_internal_browser_command(process, "close")

        def ensure_closed():
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                except OSError:
                    pass

        threading.Thread(target=ensure_closed, daemon=True).start()

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
        self._style_text_surface(self.text_input, THEME["panel_alt"], THEME["text"])
        self._autohide_ctk_textbox_scrollbar(self.text_input)
        self.text_input.insert("1.0", "")
        self.input_placeholder = ctk.CTkLabel(
            self.text_input,
            text="Digite a tarefa para a IA...",
            text_color=THEME["muted"],
            fg_color=THEME["panel_alt"],
            font=("Segoe UI", 14),
            anchor="w",
        )
        self.input_placeholder.place(x=12, y=10)
        self.input_placeholder.bind("<Button-1>", lambda _event: self.text_input.focus_set())
        self.text_input.bind("<Control-Return>", lambda _event: self.text_command())
        self.text_input.bind("<Control-v>", self.paste_into_chat)
        self.text_input.bind("<Control-V>", self.paste_into_chat)
        self.text_input.bind("<KeyRelease>", self._update_input_placeholder, add="+")
        self.text_input.bind("<FocusIn>", self._update_input_placeholder, add="+")
        self.text_input.bind("<FocusOut>", self._update_input_placeholder, add="+")

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

    def _update_input_placeholder(self, event=None):
        placeholder = getattr(self, "input_placeholder", None)
        textbox = getattr(self, "text_input", None)
        if placeholder is None or textbox is None:
            return
        try:
            has_text = bool(textbox.get("1.0", "end-1c").strip())
        except tk.TclError:
            return
        if has_text:
            placeholder.place_forget()
        else:
            placeholder.place(x=12, y=10)
            placeholder.tkraise()

    def _build_status_bar(self):
        self.status_bar = ctk.CTkFrame(self, fg_color=THEME["panel_alt"], height=34, corner_radius=0)
        self.status_bar.grid(row=3, column=2, sticky="ew", padx=12, pady=(0, 8))
        self.status_bar.grid_propagate(False)
        self.status_bar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="",
            font=("Segoe UI", 12),
            text_color=THEME["muted"],
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)

    def _bind_shortcuts(self):
        self.bind_all("<Control-Shift-N>", lambda _event: self.create_new_project())
        self.bind_all("<Control-Shift-n>", lambda _event: self.create_new_project())
        self.bind_all("<Control-o>", lambda _event: self.open_external_file())
        self.bind_all("<Control-O>", lambda _event: self.open_external_file())
        self.bind_all("<Control-s>", self.save_current_tab)
        self.bind_all("<Control-S>", self.save_current_tab)
        self.bind_all("<Control-w>", self.close_current_tab)
        self.bind_all("<Control-W>", self.close_current_tab)
        self.bind_all("<Control-b>", lambda _event: self.toggle_explorer())
        self.bind_all("<Control-B>", lambda _event: self.toggle_explorer())
        self.bind_all("<Control-r>", self.run_current_python_file)
        self.bind_all("<Control-R>", self.run_current_python_file)
        self.bind_all("<F5>", lambda _event: self.load_workspace_files())
        self.bind_all("<Control-f>", self.show_editor_find_bar)
        self.bind_all("<Control-F>", self.show_editor_find_bar)
        self.bind_all("<F3>", lambda event: self.find_in_current_editor(-1 if event.state & 0x1 else 1))
        self.bind_all("<Control-space>", self.show_editor_completion)
        self.bind_all("<Control-Shift-O>", self.show_symbol_palette)
        self.bind_all("<Control-Shift-o>", self.show_symbol_palette)
        self.bind_all("<Control-plus>", lambda _event: self.zoom_current_editor(1))
        self.bind_all("<Control-minus>", lambda _event: self.zoom_current_editor(-1))
        self.bind_all("<Control-0>", lambda _event: self.zoom_current_editor(0))

    def _bind_terminal_interrupt_shortcuts(self, widget):
        for sequence in ("<Control-c>", "<Control-C>", "<Control-Break>", "<Break>", "<Cancel>"):
            try:
                widget.bind(sequence, self.interrupt_terminal_from_keyboard, add="+")
            except tk.TclError:
                pass

    def interrupt_terminal_from_keyboard(self, event=None):
        if not self.has_terminal_processes():
            return None

        self.append_to_term("\n^C\n")
        self.cancel_terminal_command(source="Ctrl+C")
        return "break"

    def _create_editor(self, parent, tab_name):
        editor_frame = ctk.CTkFrame(parent, fg_color="#16181d", border_width=0, corner_radius=0)
        editor_frame.grid_columnconfigure(1, weight=1)
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_font = ("Consolas", self.editor_font_size)

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
            font=editor_font,
            wrap="none",
            state="disabled",
        )
        line_numbers.grid(row=0, column=0, sticky="ns")
        self._style_text_surface(line_numbers, "#111318", THEME["muted"])
        line_numbers.tag_configure("right", justify="right")
        line_numbers.tag_configure("current", background="#242832", foreground=THEME["text"])

        editor = ctk.CTkTextbox(
            editor_frame,
            fg_color="#16181d",
            text_color=THEME["text"],
            font=editor_font,
            border_width=0,
            wrap="none",
            undo=True,
            activate_scrollbars=False,
        )
        self._configure_editor_tabs(editor)
        editor.grid(row=0, column=1, sticky="nsew")
        self._style_text_surface(editor, "#16181d", THEME["text"])
        self._hide_ctk_textbox_scrollbar(editor)
        editor._line_numbers = line_numbers
        editor._editor_tab_name = tab_name
        editor.tag_config("current_line", background="#20242c")
        editor.tag_config("search_match", background="#61520b", foreground="#ffffff")
        editor.tag_config("search_current", background="#0f7a9e", foreground="#ffffff")
        editor.tag_config("brace_match", background="#31594a", foreground="#ffffff")
        editor.tag_config("brace_mismatch", background="#7c2525", foreground="#ffffff")
        editor.tag_config("identifier_match", background="#26384d")

        def scroll_editor(units):
            try:
                editor.yview_scroll(units, "units")
                line_numbers.yview_moveto(editor.yview()[0])
            except tk.TclError:
                pass

        _scroll_controls, scroll_bar = self._create_long_press_scroll_controls(
            editor_frame,
            editor.yview,
            scroll_editor,
            {"row": 0, "column": 2, "sticky": "ns"},
        )
        self.after(1, lambda: scroll_bar.set(*editor.yview()))
        def sync_scroll(first, last):
            line_numbers.yview_moveto(first)
            scroll_bar.set(first, last)

        horizontal_scroll = ctk.CTkScrollbar(
            editor_frame,
            orientation="horizontal",
            command=editor.xview,
            height=14,
            fg_color="#111318",
            button_color=THEME["border"],
            button_hover_color=THEME["accent_dark"],
        )
        horizontal_scroll.grid(row=1, column=1, sticky="ew")
        editor._horizontal_scrollbar = horizontal_scroll

        def sync_horizontal_scroll(first, last):
            try:
                horizontal_scroll.set(first, last)
            except (tk.TclError, ValueError):
                pass

        status_strip = ctk.CTkFrame(editor_frame, fg_color="#111827", height=28, corner_radius=0)
        status_strip.grid(row=2, column=0, columnspan=3, sticky="ew")
        status_strip.grid_columnconfigure(0, weight=1)
        status_strip.grid_propagate(False)

        status_label = ctk.CTkLabel(
            status_strip,
            text="Pronto",
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
            anchor="w",
        )
        status_label.grid(row=0, column=0, sticky="ew", padx=(10, 8))
        symbol_label = ctk.CTkLabel(
            status_strip,
            text="",
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
        )
        symbol_label.grid(row=0, column=1, sticky="e", padx=8)
        position_label = ctk.CTkLabel(
            status_strip,
            text="Ln 1, Col 1",
            font=("Segoe UI", 11),
            text_color=THEME["text"],
        )
        position_label.grid(row=0, column=2, sticky="e", padx=8)
        language_label = ctk.CTkLabel(
            status_strip,
            text="Texto",
            font=("Segoe UI", 11),
            text_color=THEME["muted"],
        )
        language_label.grid(row=0, column=3, sticky="e", padx=(8, 10))
        editor._status_label = status_label
        editor._symbol_label = symbol_label
        editor._position_label = position_label
        editor._language_label = language_label
        editor._search_query = ""
        editor._search_matches = []
        editor._search_match_index = -1

        editor.configure(yscrollcommand=sync_scroll, xscrollcommand=sync_horizontal_scroll)
        editor.bind("<KeyPress-Tab>", lambda event, name=tab_name: self.indent_editor_selection(name, event), add="+")
        editor.bind("<ISO_Left_Tab>", lambda event, name=tab_name: self.outdent_editor_selection(name, event), add="+")
        editor.bind("<Shift-Tab>", lambda event, name=tab_name: self.outdent_editor_selection(name, event), add="+")
        editor.bind("<Return>", lambda event, name=tab_name: self.smart_editor_return(name, event), add="+")
        editor.bind("<KeyPress>", lambda event, name=tab_name: self.editor_auto_pair(name, event), add="+")
        editor.bind("<Control-slash>", lambda event, name=tab_name: self.toggle_editor_comment(name, event), add="+")
        editor.bind("<Control-question>", lambda event, name=tab_name: self.toggle_editor_comment(name, event), add="+")
        editor.bind("<Control-space>", self.show_editor_completion, add="+")
        editor.bind("<Control-Shift-O>", lambda event: self.show_symbol_palette(event), add="+")
        editor.bind("<Control-Shift-o>", lambda event: self.show_symbol_palette(event), add="+")
        editor.bind("<Escape>", lambda event: self._hide_editor_completion(), add="+")
        editor.bind("<FocusOut>", lambda event: self.after(120, self._hide_editor_completion), add="+")
        editor.bind("<KeyRelease>", lambda event, name=tab_name: self._handle_editor_key_release(event, name), add="+")
        editor.bind("<Shift-MouseWheel>", lambda event, name=tab_name: self._scroll_editor_horizontal(event, name), add="+")
        editor.bind("<ButtonRelease-1>", lambda _event, name=tab_name: self._update_editor_view_after_navigation(name), add="+")
        editor.bind("<MouseWheel>", lambda _event, name=tab_name: self.after(1, lambda: self.update_editor_markers(name)), add="+")
        editor.bind("<Configure>", lambda _event, name=tab_name: self.after(1, lambda: self._update_editor_view_after_navigation(name)), add="+")

        self.after(1, lambda name=tab_name: self.update_editor_markers(name))
        return editor_frame, editor

    def _workspace_title(self):
        return Path(self.current_workspace).name or self.current_workspace

    def _replace_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled" if widget in [getattr(self, "agent_summary", None)] else "normal")
        if widget is getattr(self, "text_input", None):
            self._update_input_placeholder()

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
                self.status_label.configure(
                    text=self.status_with_ai_quota("IA trabalhando..."),
                    text_color=THEME["warning"],
                )
                self.refresh_ai_status()
                return
            self.status_label.configure(
                text=self.status_with_ai_quota(text),
                text_color=colors.get(mode, THEME["muted"]),
            )
            self.refresh_ai_status()

        self.after(0, update)

    def status_with_ai_quota(self, text):
        status = str(text or "")
        if "Cota:" in status:
            return status
        quota = ""
        try:
            quota_status = getattr(self.engine, "quota_status_text", None)
            if callable(quota_status):
                quota = quota_status()
        except Exception:
            quota = ""
        if not quota:
            return status
        return f"{status} | Cota: {quota}" if status else f"Cota: {quota}"

    def show_local_subnet_status(self):
        status = self.ensure_local_training_subnet_ready()
        self.add_chat_message("Sistema", status)
        mode = "ready" if "Sub-rede local: pronta" in status else "warning"
        self.set_status(
            "Sub-rede local pronta para contexto/RAG." if mode == "ready" else "Sub-rede local ainda nao preparada.",
            mode,
        )

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
            text=self.status_with_ai_quota(f"{self.ai_activity_text}{dots} {elapsed}s"),
            text_color=THEME["warning"],
        )
        self.refresh_ai_status()

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
                self.btn_send.configure(state="normal", text="Enviar")
                self.ai_busy_started_at = None
                self.ai_activity_text = "IA trabalhando"
                self.set_status("Pronto.", "ready")

        self.after(0, update)



    def load_workspace_files(self, delay=0):
        previous_job = getattr(self, "explorer_refresh_job", None)
        if previous_job:
            try:
                self.after_cancel(previous_job)
            except tk.TclError:
                pass
        self.explorer_refresh_job = self.after(delay, self._load_workspace_files_now)

    def _load_workspace_files_now(self):
        self.explorer_refresh_job = None
        self._load_workspace_files_sync()

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
            if child.is_dir() and is_ignored_dir_name(child.name):
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

    def unique_tab_name(self, preferred_name, file_path):
        if preferred_name not in self.open_editors:
            return preferred_name
        path = Path(file_path)
        parent = path.parent.name
        candidate = f"{parent}/{path.name}" if parent else path.name
        if candidate not in self.open_editors:
            return candidate
        index = 2
        while f"{candidate} ({index})" in self.open_editors:
            index += 1
        return f"{candidate} ({index})"

    def open_file_in_editor(self, file_path):
        path = Path(file_path).resolve()
        existing_tab = self.path_to_tab.get(str(path))
        tab_name = existing_tab or self.unique_tab_name(self.make_tab_name(path), path)

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
            fg_color="#16835f",
            hover_color="#1f9d73",
            border_color="#2fc28f",
            text_color="#f4fff9",
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

        self.open_editors[tab_name] = {"widget": editor, "path": str(path), "dirty": False}
        self.path_to_tab[str(path.resolve())] = tab_name
        self.tabview.set(tab_name)
        self.highlight_code(tab_name)
        self.update_editor_markers(tab_name)
        self.add_chat_message("Sistema", f"Arquivo aberto: {tab_name}")

    def build_chatgpt_web_mission(self, command):
        objective = (command or "").strip() or self.active_ai_objective or "Analise o projeto e proponha a proxima melhoria concreta."
        return (
            "PROTOCOLO INCREMENTAL MEROTEC IA IDE V9 — esta mensagem substitui qualquer protocolo anterior, inclusive V8, quando houver conflito.\n"
            "A IDE suporta desenvolvimento incremental: arquivos grandes NAO precisam ser reescritos por completo para receber uma correcao.\n\n"
            "Você está colaborando com a Merotec IA IDE. Responda em português e emita ações aplicáveis.\n\n"
            f"MISSÃO:\n{objective}\n\n"
            f"WORKSPACE ATIVO:\n{self.current_workspace}\n\n"
            f"ARQUIVOS VISÍVEIS:\n{self.get_workspace_tree(limit=120)}\n\n"
            "REGRAS DE EXECUÇÃO:\n"
            "- Não recuse uma tarefa porque o arquivo é grande ou porque não cabe inteiro no contexto. Peça somente o trecho necessário.\n"
            "- Leitura total: [READ: caminho/arquivo.ext]. Leitura parcial preferida para arquivos grandes: [READ: caminho/arquivo.ext | linhas 120-260].\n"
            "- Pesquisa precisa: [SEARCH_TEXT: padrao | caminho/arquivo.ext].\n"
            "- Alteração localizada: [REPLACE: caminho/arquivo.ext] com [OLD] e [NEW] em cercas Markdown.\n"
            "- Patch incremental também é aceito: [PATCH] *** Begin Patch / *** Update File / hunks @@ / *** End Patch [/PATCH].\n"
            "- [WRITE] é somente para arquivo novo ou reescrita realmente intencional; não é obrigatório para modificar arquivo existente.\n"
            "- A IDE cria backup antes de cada alteração e valida o conteúdo antes de salvar.\n"
            "- Você pode emitir mais de uma alteração independente na mesma resposta. Quando precisar do resultado de uma leitura, teste ou ação anterior, aguarde a resposta da IDE antes do próximo passo.\n"
            "- Nunca diga que alterou, testou ou validou sem uma ação real. Depois de editar, use [EXECUTE: comando de teste real] ou [HUMAN_TEST: auto] quando apropriado.\n\n"
            "FORMATOS:\n"
            "[REPLACE: caminho/arquivo.ext]\n[OLD]\n```linguagem\ntrecho exato atual\n```\n[/OLD]\n[NEW]\n```linguagem\ntrecho novo\n```\n[/NEW]\n[/REPLACE]\n\n"
            "[PATCH]\n*** Begin Patch\n*** Update File: caminho/arquivo.ext\n@@\n-linha antiga\n+linha nova\n*** End Patch\n[/PATCH]\n\n"
            "[WRITE: caminho/arquivo.ext]\n```linguagem\nconteúdo completo\n```\n[/WRITE]\n\n"
            "Python exige 4 espaços por nível e nunca tab. Nunca use EXECUTE para imprimir/ler arquivo; use READ ou SEARCH_TEXT. Após alterar, valide e corrija até concluir."
        )

    def send_mission_to_web_chat(self):
        command = self.text_input.get("1.0", "end-1c").strip()
        if command:
            self.active_ai_objective = command
        if getattr(self.engine, "provider", "") == "web_chat":
            self.add_chat_message(
                "Sistema",
                "Missao enviada diretamente ao Chat Web. A IDE vai aplicar acoes, validar e continuar sem copiar/colar manual.",
            )
            self.tabview.set(CHAT_TAB_NAME)
            self._run_ai_task(command or self.active_ai_objective or "Continue a missao ativa pelo Chat Web.")
            return
        mission = self.build_chatgpt_web_mission(command)
        try:
            self.clipboard_clear()
            self.clipboard_append(mission)
            self.update_idletasks()
        except tk.TclError as exc:
            self.add_chat_message("Erro", f"Nao consegui copiar a missao: {exc}")
            return
        self.tabview.set("Navegador")
        target = self.web_chat_target_for_workspace(self.current_workspace)
        self.open_internal_browser(target, source="Chat Web")
        self.add_chat_message(
            "Sistema",
            "Missão estruturada copiada. Cole no Chat Web configurado; depois copie a resposta e use IA > Importar resposta do Chat Web.",
        )

    def send_mission_to_chatgpt_web(self):
        # Compatibilidade com atalhos e integrações anteriores.
        return self.send_mission_to_web_chat()

    def import_web_chat_response(self):
        try:
            response = self.clipboard_get().strip()
        except tk.TclError:
            response = ""
        if not response:
            messagebox.showerror(APP_NAME, "Copie primeiro a resposta textual do Chat Web.", parent=self)
            return
        if len(response) > 500000:
            messagebox.showerror(APP_NAME, "A resposta copiada e grande demais para importar com seguranca.", parent=self)
            return

        self.last_response = response
        visible = self.strip_agent_action_markup(response) or "Resposta com acao recebida."
        self.add_chat_message("Chat Web", visible)
        if not self.response_has_agent_action(response):
            self.set_status("Resposta do ChatGPT importada como contexto.", "ready")
            return
        auto_apply = self.web_chat_auto_apply_imported_actions()
        if not auto_apply:
            if not messagebox.askyesno(
                APP_NAME,
                "A resposta contem acoes para o projeto ativo. Deseja valida-las e aplica-las agora?",
                parent=self,
            ):
                self.set_status("Resposta importada sem aplicar alteracoes.", "warning")
                return
        objective = self.active_ai_objective or "Aplicar resposta importada do Chat Web"
        if auto_apply:
            self.add_chat_message("Sistema", "Resposta importada aplicada automaticamente pelo modo autonomo do Chat Web.")
        self.parse_and_execute_agent_actions(response, task_objective=objective)
        self.load_workspace_files()

    def web_chat_auto_apply_imported_actions(self):
        settings = getattr(self, "settings", {})
        profile = (
            settings.get("ai_profiles", {}).get("web_chat", {})
            if isinstance(settings, dict)
            else {}
        )
        value = profile.get(
            "web_chat_auto_apply_imported_actions",
            settings.get("web_chat_auto_apply_imported_actions", True) if isinstance(settings, dict) else True,
        )
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}

    def import_chatgpt_web_response(self):
        # Compatibilidade com o menu anterior.
        return self.import_web_chat_response()

    def _on_editor_key(self, _event, tab_name):
        if tab_name in self.open_editors:
            self.open_editors[tab_name]["dirty"] = True

    def _on_editor_content_changed(self, tab_name):
        self.highlight_code(tab_name)
        self.update_editor_markers(tab_name)
        self._schedule_editor_completion(tab_name)

    def update_editor_markers(self, tab_name, lightweight=False):
        info = self.open_editors.get(tab_name)
        if not info:
            return

        editor = info["widget"]
        line_numbers = getattr(editor, "_line_numbers", None)
        if line_numbers is None:
            return

        try:
            line_count = int(editor.index("end-1c").split(".")[0])
            insert_index = editor.index("insert")
            current_line, current_col = (int(part) for part in insert_index.split("."))
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
        self._update_editor_status_context(tab_name, editor, current_line, current_col)
        self._highlight_matching_brace(editor)
        if not lightweight:
            self._highlight_current_identifier(editor)

    def _update_editor_status_context(self, tab_name, editor, current_line, current_col):
        info = self.open_editors.get(tab_name, {})
        path = info.get("path") or tab_name
        language = self._editor_language_name(path)
        dirty = "Alterado" if info.get("dirty") else "Salvo" if info.get("path") else "Rascunho"
        try:
            # Tenta obter o texto selecionado e conta os caracteres
            selection_text = editor.get("sel.first", "sel.last")
            selected = len(selection_text)
        except Exception:
            # Se não houver nada selecionado, o Tkinter joga uma exceção. Defina como 0.
            selected = 0
        try:
            editor._position_label.configure(text=f"Ln {current_line}, Col {current_col + 1}{selected}")
            editor._language_label.configure(text=language)
            editor._status_label.configure(text=f"{dirty} | {Path(path).name if path else tab_name}")
            editor._symbol_label.configure(text=self._current_symbol_path(editor, current_line))
        except (AttributeError, tk.TclError):
            pass

    def _editor_language_name(self, path):
        suffix = Path(str(path)).suffix.lower()
        names = {
            ".py": "Python",
            ".js": "JavaScript",
            ".jsx": "React JSX",
            ".ts": "TypeScript",
            ".tsx": "React TSX",
            ".html": "HTML",
            ".css": "CSS",
            ".json": "JSON",
            ".md": "Markdown",
            ".cs": "C#",
            ".java": "Java",
            ".dart": "Dart",
            ".cpp": "C++",
            ".c": "C",
            ".h": "C/C++",
        }
        return names.get(suffix, "Texto")

    def _current_symbol_path(self, editor, current_line):
        symbols = []
        try:
            start = max(1, current_line - 220)
            for line_no in range(start, current_line + 1):
                text = editor.get(f"{line_no}.0", f"{line_no}.end")
                match = re.match(
                    r"\s*(?:class|def|async\s+def|function|const|let|var|public\s+(?:class|void|async|static)|private\s+(?:void|async|static)|protected\s+(?:void|async|static))\s+([A-Za-z_$][\w$]*)",
                    text,
                )
                if match:
                    symbols.append(match.group(1))
        except tk.TclError:
            return ""
        return " > ".join(symbols[-3:])

    def _highlight_matching_brace(self, editor):
        pairs = {"(": ")", "[": "]", "{": "}"}
        reverse = {value: key for key, value in pairs.items()}
        try:
            editor.tag_remove("brace_match", "1.0", "end")
            editor.tag_remove("brace_mismatch", "1.0", "end")
            candidates = [("insert-1c", editor.get("insert-1c", "insert")), ("insert", editor.get("insert", "insert+1c"))]
            for index, char in candidates:
                if char in pairs:
                    match = self._find_matching_brace(editor, index, char, pairs[char], 1)
                    tag = "brace_match" if match else "brace_mismatch"
                    editor.tag_add(tag, index, f"{index}+1c")
                    if match:
                        editor.tag_add(tag, match, f"{match}+1c")
                    return
                if char in reverse:
                    match = self._find_matching_brace(editor, index, reverse[char], char, -1)
                    tag = "brace_match" if match else "brace_mismatch"
                    editor.tag_add(tag, index, f"{index}+1c")
                    if match:
                        editor.tag_add(tag, match, f"{match}+1c")
                    return
        except tk.TclError:
            pass

    def _find_matching_brace(self, editor, start_index, opening, closing, direction):
        depth = 0
        try:
            if direction > 0:
                pos = start_index
                end = editor.index("end-1c")
                while editor.compare(pos, "<=", end):
                    char = editor.get(pos, f"{pos}+1c")
                    if char == opening:
                        depth += 1
                    elif char == closing:
                        depth -= 1
                        if depth == 0:
                            return editor.index(pos)
                    pos = editor.index(f"{pos}+1c")
            else:
                pos = start_index
                while editor.compare(pos, ">=", "1.0"):
                    char = editor.get(pos, f"{pos}+1c")
                    if char == closing:
                        depth += 1
                    elif char == opening:
                        depth -= 1
                        if depth == 0:
                            return editor.index(pos)
                    pos = editor.index(f"{pos}-1c")
        except tk.TclError:
            return None
        return None

    def _highlight_current_identifier(self, editor):
        try:
            editor.tag_remove("identifier_match", "1.0", "end")
            word = editor.get("insert wordstart", "insert wordend").strip()
            if len(word) < 3 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word):
                return
            start = "1.0"
            while True:
                pos = editor.search(word, start, stopindex="end", regexp=False)
                if not pos:
                    break
                end = f"{pos}+{len(word)}c"
                before = editor.get(f"{pos}-1c", pos)
                after = editor.get(end, f"{end}+1c")
                if not re.match(r"\w", before or "") and not re.match(r"\w", after or ""):
                    editor.tag_add("identifier_match", pos, end)
                start = end
        except tk.TclError:
            pass

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
        self.close_internal_browser()
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
        if self.is_placeholder_command(command):
            self.append_to_term(
                "\n[erro] Digite um comando real. Reticencias ou texto como 'como administrador' nao sao executaveis.\n"
            )
            self.log_agent("Comando local recusado: placeholder.")
            return
        if self.is_admin_execute_request(command):
            admin_command = self.clean_admin_command(command)
            self._agent_execute_admin(
                admin_command,
                task_objective=admin_command,
                requester="O terminal local",
                terminal_source="via terminal local como administrador",
            )
            return
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
                output = self.stream_process_output(process, collect=True)
                process.wait(timeout=120)
                if process.returncode != 0:
                    self.append_to_term(f"\n[processo finalizado com codigo {process.returncode}]\n")
                    if self.command_output_requires_admin(output):
                        self.append_to_term("[permissao] comando requer administrador; solicitando autorizacao do usuario.\n")
                        self._agent_execute_admin(
                            command,
                            task_objective=command,
                            requester="O terminal local",
                            terminal_source="via terminal local como administrador",
                        )
                        return
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
        if killed or processes:
            self.reset_terminal_busy()
        return killed

    def cancel_terminal_command(self, source="usuario"):
        killed = self.cancel_active_terminal_processes()
        if killed:
            self.append_to_term(f"\n[cancelado] {killed} processo(s) encerrado(s) no Terminal Local.\n")
            self.add_chat_message("Sistema", f"Terminal Local cancelado ({killed} processo(s)).")
            self.set_status("Terminal Local cancelado.", "warning")
        else:
            self.set_status("Nenhum processo ativo no Terminal Local.", "ready")
        self.log_agent(f"Cancelamento do terminal solicitado por {source}: {killed} processo(s).")
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
                or bool(re.search(r"\[(READ|WRITE|REPLACE|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|SCREENSHOT|HUMAN_TEST|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|UNDO)\s*:", chunk, re.IGNORECASE))
            )
            if not should_log:
                self.update_ai_activity_from_stream(chunk)
                return
            self.ai_live_trace_last_log_at = now
            self.ai_live_trace_last_length = current_length
            live_text = self.describe_live_ai_trace(self.ai_live_trace, chunk)

        if live_text:
            self.log_agent(f"IA: {live_text}")
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
            self.log_agent(f"IA finalizada em {elapsed}s; resposta recebida pela IDE.")

    def clean_live_ai_text(self, text, limit=260):
        cleaned = self.strip_agent_action_markup(text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > limit:
            cleaned = "..." + cleaned[-limit:]
        return cleaned

    def describe_live_ai_trace(self, trace, chunk):
        action_matches = re.findall(
            r"(?:\[(READ|WRITE|REPLACE|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|SCREENSHOT|HUMAN_TEST)\s*:\s*([^\]]*)\]|\[(READ|WRITE|REPLACE|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|SCREENSHOT|HUMAN_TEST)\]\s*([^\r\n]*)|^(READ|WRITE|REPLACE|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|SCREENSHOT|HUMAN_TEST)\s*(?::|\s+)\s*([^\r\n]+))",
            trace or "",
            re.IGNORECASE | re.MULTILINE,
        )
        action_matches = [
            ((match[0] or match[2] or match[4]).upper(), (match[1] or match[3] or match[5]).strip())
            for match in action_matches
            if (match[0] or match[2] or match[4]) and (match[1] or match[3] or match[5]).strip()
        ]
        if action_matches:
            action, payload = action_matches[-1]
            payload = self.clean_live_ai_text(payload, limit=140)
            names = {
                "READ": "pediu leitura",
                "WRITE": "pediu escrita",
                "REPLACE": "pediu substituicao",
                "SEARCH_TEXT": "pediu busca",
                "WEB_SEARCH": "pediu busca web",
                "SCAN_TEXT": "pediu varredura",
                "FIX_MOJIBAKE": "pediu correcao de texto",
                "UNDO": "pediu desfazer",
                "EXECUTE": "pediu execucao",
                "EXECUTE_ADMIN": "pediu administrador",
                "OPEN_URL": "pediu abertura",
                "BROWSER_INSPECT": "pediu leitura da pagina",
                "BROWSER_CLICK": "pediu clique",
                "BROWSER_TYPE": "pediu digitacao",
                "BROWSER_SCROLL": "pediu rolagem",
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
        if re.search(r"(?:\[(write|replace|fix_mojibake|undo)(?:\s*:|\])|^(write|replace|fix_mojibake|undo)\s*(?::|\s))", normalized, re.MULTILINE):
            self.set_ai_activity("IA preparando alteracao")
        elif re.search(r"(?:\[(execute|execute_admin|open_url|browser_inspect|browser_click|browser_type|browser_scroll|screenshot|human_test)(?:\s*:|\])|^(execute|execute_admin|open_url|browser_inspect|browser_click|browser_type|browser_scroll|screenshot|human_test)\s*(?::|\s))", normalized, re.MULTILINE):
            self.set_ai_activity("IA preparando validacao")
        elif re.search(r"(?:\[(read|search_text|web_search|scan_text)(?:\s*:|\])|^(read|search_text|web_search|scan_text)\s*(?::|\s))", normalized, re.MULTILINE):
            self.set_ai_activity("IA pedindo contexto")
        elif any(term in normalized for term in ("patch", "filechange", "apply", "alterando", "escrevendo")):
            self.set_ai_activity(f"{self.ai_assistant_display_name()} alterando arquivos")
        elif any(term in normalized for term in ("command", "execut", "rodando", "testando")):
            self.set_ai_activity(f"{self.ai_assistant_display_name()} executando")
        else:
            self.set_ai_activity("IA respondendo ao vivo")

    def ai_assistant_display_name(self):
        engine = getattr(self, "engine", None)
        if engine is not None and hasattr(engine, "assistant_display_name"):
            return engine.assistant_display_name()
        return "IA"

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
        self.remember_ai_context_message(sender, text)
        self.after(0, lambda: self._add_chat_message_sync(sender, text))

    def add_chat_image_message(self, sender, image_path, text=""):
        image_note = f"{text}\n[imagem anexada: {Path(image_path).name}]" if text else f"[imagem anexada: {Path(image_path).name}]"
        self.remember_ai_context_message(sender, image_note)
        self.after(0, lambda: self._add_chat_image_message_sync(sender, image_path, text))

    def remember_ai_context_message(self, sender, text, max_chars=2400):
        text = str(text or "").strip()
        if not text:
            return
        sender = str(sender or "").strip() or "Mensagem"
        compact = re.sub(r"\s+", " ", text)
        if len(compact) > max_chars:
            compact = compact[: max_chars - 3].rstrip() + "..."
        self.ai_context_memory.append(
            {
                "sender": sender,
                "text": compact,
                "task_id": self.current_task_id,
                "objective": self.active_ai_objective or "",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.ai_context_memory = self.ai_context_memory[-80:]

    def build_recent_ai_context_memory(self, limit=16, max_chars=6000):
        messages = getattr(self, "ai_context_memory", [])[-limit:]
        lines = []
        total = 0
        for item in messages:
            sender = item.get("sender") or "Mensagem"
            text = item.get("text") or ""
            if not text:
                continue
            line = f"- {sender}: {text}"
            total += len(line)
            if total > max_chars:
                lines.append("- ... historico recente truncado para caber no contexto.")
                break
            lines.append(line)
        return "\n".join(lines)

    def _add_chat_message_sync(self, sender, text):
        user = sender.lower() in {"voce", "você"}
        system = sender.lower() in {"sistema", "erro"}
        border_color = THEME["accent_dark"] if user else THEME["border"]
        sender_color = "#ffffff" if user else THEME["warning"] if sender == "Erro" else THEME["accent"]
        display_text = self.format_chat_text_for_display(text, sender=sender)
        font = self.chat_text_font(display_text)

        frame = ctk.CTkFrame(
            self.chat_history,
            fg_color=THEME["panel"],
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
            fg_color=THEME["panel"],
            text_color=THEME["text"],
            font=font,
            wrap="word",
            height=42,
        )
        textbox.pack(fill="x", padx=10, pady=(0, 8))
        self._style_text_surface(textbox, THEME["panel"], THEME["text"])
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
            fg_color=THEME["panel"],
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
                fg_color=THEME["panel"],
                text_color=THEME["text"],
                font=self.chat_text_font(display_text),
                wrap="word",
                height=42,
            )
            textbox.pack(fill="x", padx=10, pady=(0, 6))
            self._style_text_surface(textbox, THEME["panel"], THEME["text"])
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
        repair = getattr(self, "repair_common_mojibake", None)
        if callable(repair) and self.mojibake_score(raw):
            raw = repair(raw)
        raw = re.sub(r"(?<=[.!?])(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", " ", raw)
        raw = re.sub(r"(?<=[.!?])\s+(?=(Resumo|Arquitetura|Fluxo|Arquivos|Riscos?|Pontos|Próxim|Proxim|Sugest|Implementaç|Implementac|Diagnóstico|Diagnostico)\b)", "\n\n", raw)
        raw = re.sub(r"(?<=[a-záéíóúâêôãõç0-9])(?=(Resumo|Arquitetura|Fluxo|Arquivos|Riscos|Pontos|Próxim|Proxim|Sugest|Implementaç|Implementac|Diagnóstico|Diagnostico)\b)", "\n\n", raw)
        raw = re.sub(r"(?<!\n)([-*]\s+)", r"\n\1", raw)
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
        self.streaming_sender = sender
        user = sender.lower() in {"voce", "vocÃª"}
        system = sender.lower() in {"sistema", "erro"}
        border_color = THEME["accent_dark"] if user else THEME["border"]
        sender_color = "#ffffff" if user else THEME["warning"] if sender == "Erro" else THEME["accent"]

        frame = ctk.CTkFrame(
            self.chat_history,
            fg_color=THEME["panel"],
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
            fg_color=THEME["panel"],
            text_color=THEME["text"],
            font=("Segoe UI", 14),
            wrap="word",
            height=42,
        )
        textbox.pack(fill="x", padx=10, pady=(0, 8))
        self._style_text_surface(textbox, THEME["panel"], THEME["text"])
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
            self.remember_ai_context_message(self.streaming_sender or "Merotec IA", display_text)
            textbox.configure(state="normal")
            textbox.delete("1.0", "end")
            textbox.insert("1.0", display_text)
            textbox.configure(state="disabled", font=self.chat_text_font(display_text))
            self.resize_chat_textbox(textbox, display_text, max_height=620)
            self.scroll_textbox_end(textbox)
            self.safe_chat_scroll_bottom()
        self.streaming_textbox = None
        self.streaming_sender = ""
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
        if self.agent_busy:
            self.cancel_ai_task()
            return
        if self.has_terminal_processes():
            self.set_status("Terminal Local em execucao; use Cancelar terminal na aba Terminal Local.", "warning")
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

        answer_only = self.is_answer_only_question(command, normalized)
        continuation_context = None
        task_objective = None
        if not answer_only and self.should_continue_active_ai_task(command, normalized):
            task_objective = self.active_ai_objective
            continuation_context = self.build_active_task_continuation_context(command)
            self.add_chat_message("Sistema", "Continuando a missao anterior com memoria recente da IDE.")

        extra_context = continuation_context
        if answer_only:
            answer_context = (
                "MODO RESPOSTA SOMENTE:\n"
                "- O usuario fez uma pergunta ou perguntou sobre capacidade.\n"
                "- Responda primeiro em texto claro, sem iniciar execucao, sem editar arquivos e sem emitir tags da IDE.\n"
                "- Se a pergunta envolver uma acao possivel, explique o que voce consegue fazer e quais dados faltam para executar depois."
            )
            extra_context = f"{extra_context}\n\n{answer_context}" if extra_context else answer_context
        if self.is_project_analysis_request(normalized):
            project_context = self.build_project_analysis_context()
            extra_context = f"{extra_context}\n\n{project_context}" if extra_context else project_context
            self.add_chat_message("Sistema", "Preparando contexto inicial do projeto para a IA...")

        self._run_ai_task(
            command,
            image_path=image_path,
            extra_context=extra_context,
            task_objective=task_objective,
            answer_only=answer_only,
        )

    def cancel_ai_task(self):
        self.cancelled_task_ids.add(self.current_task_id)
        try:
            self.engine.cancel_generation()
        except Exception:
            pass
        self.add_chat_message("Sistema", "Cancelando tarefa do Chat IA...")
        self.reset_ai_busy_after_cancel()

    def is_task_cancelled(self, task_id):
        return task_id is not None and task_id in self.cancelled_task_ids

    def reset_ai_busy_after_cancel(self):
        with self.ai_work_lock:
            self.ai_work_count = 0
            self.agent_busy = False
            self.ai_busy_started_at = None

        def update():
            self.ai_progress.stop()
            self.ai_progress.grid_remove()
            self.btn_send.configure(state="normal", text="Enviar")
            self.status_label.configure(text="Chat IA cancelado.", text_color=THEME["warning"])
            self.refresh_ai_status()

        self.after(0, update)

    def reset_busy_indicators_after_cancel(self):
        self.reset_ai_busy_after_cancel()
        self.reset_terminal_busy()

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

    def should_continue_active_ai_task(self, command, normalized=None):
        if not self.active_ai_objective:
            return False
        normalized = normalized or self.normalize_plain_text(command or "")
        if not normalized:
            return False
        continuation_commands = {
            "continue",
            "continua",
            "continuar",
            "prossiga",
            "segue",
            "siga",
            "pode continuar",
            "continue dai",
            "continue daqui",
            "continue de onde parou",
            "continue a tarefa",
            "continua a tarefa",
            "termina",
            "termine",
            "conclua",
            "finalize",
            "faca isso",
            "faz isso",
            "corrija isso",
            "aplique isso",
            "agora faca",
        }
        if normalized in continuation_commands:
            return True
        return any(
            marker in normalized
            for marker in (
                "continue de onde",
                "continua de onde",
                "continue o que",
                "continua o que",
                "termine a tarefa",
                "conclua a tarefa",
                "finalize a tarefa",
                "faca a correcao",
                "faz a correcao",
            )
        )

    def build_active_task_continuation_context(self, command):
        objective = self.active_ai_objective or ""
        last_response = self.strip_agent_action_markup(self.last_response or "").strip()
        pieces = [
            "CONTINUIDADE DA MISSAO NA IDE:",
            f"Pedido atual do usuario: {command}",
            f"Missao ativa anterior: {objective}",
        ]
        if last_response:
            pieces.append(f"Ultima resposta visivel da IA:\n{last_response[-2400:]}")
        recent_changes = self.format_recent_changes_for_agent(limit=10)
        if recent_changes:
            pieces.append(f"Alteracoes/acoes recentes registradas:\n{recent_changes}")
        recent_chat = self.build_recent_ai_context_memory(limit=18)
        if recent_chat:
            pieces.append(f"Conversa recente relevante:\n{recent_chat}")
        pieces.append(
            "Ordem de continuidade: nao trate o pedido atual como tarefa isolada; "
            "continue a missao ativa usando as evidencias, leituras, acoes e respostas recentes."
        )
        return "\n\n".join(pieces)


    def voice_command(self):
        if self.voice_capture_active:
            self.stop_voice_capture_and_send()
            return
        if self.agent_busy:
            return
        self.start_voice_capture()

    def apply_voice_keyword_listener_setting(self):
        enabled = bool(self.settings.get("voice_keyword_listener_enabled", False))
        self.voice_keyword_listener_enabled = enabled
        if not enabled:
            try:
                self.voice.stop_keyword_listener()
            except Exception:
                pass
            self.voice_keyword_capture_active = False
            if not self.voice_capture_active:
                self.set_voice_button_text("Comando por Voz")
            self.set_status("Escuta automatica do microfone desativada.", "ready")
            return
        if not self.voice_capture_active:
            self.after(200, self.start_voice_keyword_listener)

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

    def ask_codex_app_server_approval(self, method, params, workspace):
        is_command_request = self.is_codex_command_approval_method(method)
        command = self.extract_codex_app_server_command(params) if is_command_request else ""
        if is_command_request and (not command or self.is_placeholder_command(command)):
            reason = "sem comando extraivel" if not command else "comando placeholder"
            self.log_agent(f"Permissao app-server negada: {reason} em {method}.")
            self.add_chat_message(
                "Erro",
                "A IDE recusou um pedido do Codex app-server com comando vazio ou demonstrativo.",
            )
            self.add_chat_message(
                "Sistema",
                "Use um comando real. Para administrador, envie uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].",
            )
            return False

        action = self.describe_codex_app_server_approval(method)
        if self.codex_auto_approve_app_server_enabled():
            self.log_agent(f"Permissao app-server autoaprovada: {method}")
            self.add_chat_message("Sistema", f"Permissao autoaprovada para {action}.")
            return True

        details = self.format_codex_app_server_approval(method, params, workspace)
        title = "Autorizar acao do Codex?"
        admin_notice = ""
        if command and self.is_admin_execute_request(command):
            admin_notice = (
                "\n\nEste comando menciona administrador. A autorizacao da IDE libera o pedido do Codex, "
                "mas elevacao real no Windows ainda depende do UAC. Para elevacao controlada pela IDE, "
                "prefira uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups]."
            )
        message = (
            f"O Codex app-server pediu permissao para {action}.\n\n"
            f"{details}\n\n"
            "Autorizar esta acao pela IDE?"
            f"{admin_notice}"
        )
        result_queue = queue.Queue(maxsize=1)

        def ask():
            try:
                self.set_status("Aguardando autorizacao do usuario...", "busy")
                approved = bool(messagebox.askyesno(title, message))
                if approved:
                    self.log_agent(f"Permissao app-server aprovada: {method}")
                    self.add_chat_message("Sistema", f"Permissao aprovada para {action}.")
                else:
                    self.log_agent(f"Permissao app-server negada: {method}")
                    self.add_chat_message("Sistema", f"Permissao negada para {action}.")
                result_queue.put(approved)
            except Exception as exc:
                result_queue.put(exc)

        if threading.current_thread() is threading.main_thread():
            ask()
        else:
            self.after(0, ask)

        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return bool(result)

    def is_codex_command_approval_method(self, method):
        normalized = self.normalize_plain_text(method or "")
        return "command" in normalized or "exec" in normalized

    def describe_codex_app_server_approval(self, method):
        normalized = self.normalize_plain_text(method or "")
        if self.is_codex_command_approval_method(method):
            return "executar um comando"
        if "permission" in normalized or "permissions" in normalized:
            return "ampliar permissoes do app-server"
        if "file" in normalized or "patch" in normalized:
            return "alterar arquivos"
        return "continuar uma acao restrita"

    def format_codex_app_server_approval(self, method, params, workspace):
        lines = [f"Metodo: {method}", f"Workspace: {workspace}"]
        command = self.extract_codex_app_server_command(params)
        if command:
            lines.append(f"Comando:\n{command}")
        else:
            details = json.dumps(params or {}, ensure_ascii=False, indent=2)
            if len(details) > 1800:
                details = details[:1800].rstrip() + "\n..."
            lines.append(f"Detalhes:\n{details}")
        return "\n\n".join(lines)

    def extract_codex_app_server_command(self, value, depth=0):
        if depth > 5:
            return ""
        if isinstance(value, dict):
            executable_keys = (
                "program",
                "executable",
                "filePath",
                "file_path",
                "binary",
                "shell",
            )
            argument_keys = (
                "argv",
                "args",
                "arguments",
                "argList",
                "argumentList",
                "argument_list",
            )
            executable = next(
                (
                    value[key]
                    for key in executable_keys
                    if key in value and value[key] not in (None, "", False)
                ),
                None,
            )
            arguments = next(
                (
                    value[key]
                    for key in argument_keys
                    if key in value and value[key] not in (None, "", [])
                ),
                None,
            )
            if executable is not None and arguments is not None:
                command_parts = [
                    self.compact_codex_approval_value(executable),
                    self.compact_codex_approval_value(arguments),
                ]
                return " ".join(part for part in command_parts if part).strip()

            preferred_keys = (
                "command",
                "commandLine",
                "command_line",
                "cmdLine",
                "cmdline",
                "cmd",
                "shellCommand",
                "shell_command",
                "script",
                "argv",
                "args",
                "arguments",
                "argList",
                "argumentList",
                "argument_list",
            )
            for key in preferred_keys:
                if key in value:
                    nested_value = value[key]
                    if isinstance(nested_value, dict):
                        found = self.extract_codex_app_server_command(nested_value, depth + 1)
                        if found:
                            return found
                    return self.compact_codex_approval_value(nested_value)
            for nested in value.values():
                found = self.extract_codex_app_server_command(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = self.extract_codex_app_server_command(nested, depth + 1)
                if found:
                    return found
        return ""

    def compact_codex_approval_value(self, value):
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def build_web_chat_visual_receipt_contract(self, image_path):
        """Inclui uma instrução curta para análise do print anexado.

        O recebimento técnico do anexo é confirmado pelo navegador interno.
        Não exigimos uma tag textual do modelo, porque ela conflita com a ação
        executável que o agente precisa devolver na mesma resposta.
        """
        if not image_path or getattr(self.engine, "provider", "") != "web_chat":
            return ""
        name = Path(str(image_path)).name or "screenshot.png"
        return (
            "EVIDÊNCIA VISUAL DISPONÍVEL:\n"
            f"- A IDE anexou a imagem `{name}` nesta mesma mensagem e confirmou o envio pelo navegador.\n"
            "- Use a imagem na análise, mas responda diretamente com a próxima ação executável da IDE ou uma conclusão objetiva.\n"
            "- Só informe que não recebeu a imagem quando ela realmente não estiver visível para você."
        )

    def web_chat_response_recovery_reason(self, response_text, task_objective=None, task_id=None, direct_action_happened=False):
        """Retorna um motivo quando o Chat Web não entregou ação verificável.

        A resposta em linguagem natural é mantida no histórico, mas não pode
        encerrar uma missão de correção sem alteração, leitura, execução ou
        conclusão verificável. Antes esta situação virava apenas um aviso e a
        tarefa terminava silenciosamente.
        """
        if getattr(self.engine, "provider", "") != "web_chat":
            return ""
        metrics = self.get_ai_task_metrics(task_id)
        pending_diagnostic = metrics.get("requires_error_correction")
        # Depois de um erro real de terminal/teste, uma leitura ou acao anterior
        # nao basta para encerrar a recuperacao. O Chat Web precisa devolver uma
        # proxima tag aplicavel; sem isso o diagnostico some da conversa e o ciclo
        # aparenta travar mesmo com o erro visivel no Terminal Local.
        if direct_action_happened:
            return ""
        if not pending_diagnostic and self.task_has_real_action(task_id):
            return ""
        objective = task_objective or self.active_ai_objective or ""
        if not self.objective_requires_concrete_change(objective):
            return ""
        response = str(response_text or "").strip()
        if not response:
            return "O Chat Web não devolveu texto utilizável."
        if self.response_has_agent_action(response):
            return ""
        if pending_diagnostic:
            command_name = str(pending_diagnostic.get("command") or "o ultimo teste/comando") if isinstance(pending_diagnostic, dict) else "o ultimo teste/comando"
            return (
                "A IDE enviou um diagnostico real de falha para o Chat Web, mas a resposta "
                f"nao trouxe uma proxima acao executavel para corrigir {command_name}."
            )
        normalized = self.normalize_plain_text(response)
        transport_failures = (
            "chat web nao concluiu",
            "tempo esgotado aguardando resposta",
            "terminou sem texto de resposta",
            "navegador interno foi encerrado",
            "campo de conversa nao encontrado",
            "nao confirmou o envio",
        )
        if any(item in normalized for item in transport_failures):
            return "O Chat Web não concluiu uma resposta executável."
        if self.claims_concrete_result_without_real_action(response, task_objective=objective):
            return "O Chat Web afirmou alteração, teste ou correção sem enviar uma ação que a IDE possa executar."
        return "O Chat Web respondeu sem uma ação executável para a missão ativa."

    def continue_after_web_chat_protocol_stall(
        self,
        *,
        command,
        image_path,
        extra_context,
        task_objective,
        action_depth,
        task_id,
        response_text,
        reason,
    ):
        """Reenvia uma instrução curta ao mesmo chat, sem criar outra conversa.

        O padrão é contínuo (limite 0). Um limite positivo pode ser definido
        em `web_chat_protocol_recovery_max_attempts` por quem quiser controlar
        consumo de um provedor externo.
        """
        if self.is_task_cancelled(task_id) or not self.should_continue_development_loop(action_depth, task_id):
            return False
        metrics = self.get_ai_task_metrics(task_id)
        attempts = int(metrics.get("web_chat_protocol_recovery_attempts", 0) or 0)
        try:
            configured_limit = int(self.settings.get("web_chat_protocol_recovery_max_attempts", 4) or 4)
        except (TypeError, ValueError, AttributeError):
            configured_limit = 4
        configured_limit = max(0, min(200, configured_limit))
        if configured_limit and attempts >= configured_limit:
            self.add_chat_message(
                "Erro",
                "O Chat Web repetiu respostas sem ação executável até o limite configurado. "
                "A missão permanece pendente; use Reenviar ou aumente o limite de recuperação.",
            )
            self.set_status("Chat Web sem ação executável; missão pendente.", "warning")
            return False

        fingerprint = self.normalize_plain_text(response_text or "")[:1400]
        previous = str(metrics.get("web_chat_last_protocol_response", ""))
        repeated = int(metrics.get("web_chat_repeated_protocol_response", 0) or 0)
        repeated = repeated + 1 if fingerprint and fingerprint == previous else 0
        metrics["web_chat_last_protocol_response"] = fingerprint
        metrics["web_chat_repeated_protocol_response"] = repeated
        metrics["web_chat_protocol_recovery_attempts"] = attempts + 1
        metrics["protocol_violations"] = int(metrics.get("protocol_violations", 0) or 0) + 1

        # A mesma resposta no mesmo estado não adiciona evidência nova.
        # Pausar antes de reenviar impede que o ciclo recrie a mesma rodada.
        if repeated >= 2:
            self.add_chat_message(
                "Erro",
                "O Chat Web repetiu a mesma resposta sem uma ação executável. "
                "A missão foi pausada para evitar ciclo infinito; nenhum arquivo foi alterado.",
            )
            self.log_agent("Recuperação do Chat Web pausada por resposta repetida sem progresso.")
            self.set_status("Pausado: Chat Web repetiu resposta sem ação.", "warning")
            return False

        cadence = (
            f"{attempts + 1}/{configured_limit}"
            if configured_limit else f"{attempts + 1}/contínuo"
        )
        self.add_chat_message(
            "Sistema",
            "O Chat Web não entregou uma ação verificável. A IDE preservou o estado atual e "
            f"vai continuar a mesma missão na conversa existente ({cadence}).\n\nMotivo: {reason}",
        )
        self.log_agent(f"Recuperação contínua do Chat Web {cadence}: {reason}")
        self.set_ai_activity("IA recuperando resposta do Chat Web")

        # ``repeated_warning`` precisa existir em todas as tentativas.
        # A versão anterior concatenava essa variável sem inicializá-la e
        # encerrava a recuperação com ``NameError`` depois que o Chat Web
        # devolvia uma resposta sem ação.
        repeated_warning = ""
        if repeated:
            repeated_warning = (
                "\nATENÇÃO: a resposta atual é semelhante à anterior e ainda não contém "
                "uma ação executável. Não repita a explicação; emita somente a próxima tag "
                "da IDE com os parâmetros necessários.\n"
            )

        recovery_context = (
            f"MISSAO ORIGINAL:\n{task_objective or self.active_ai_objective or command}\n\n"
            "RECUPERAÇÃO OBRIGATÓRIA DO PROTOCOLO:\n"
            f"{reason}\n"
            "A resposta anterior não foi aceita como conclusão porque nenhuma ação da IDE foi aplicada. "
            "Não resuma, não prometa e não diga que corrigiu/testou. Protocolo incremental V9: não exija WRITE completo para arquivo grande. Responda agora com a próxima ação necessária: "
            "[READ: arquivo | linhas inicio-fim], [SEARCH_TEXT: padrão | arquivo], [PATCH]...[/PATCH], [WRITE: arquivo]...[/WRITE], "
            "[REPLACE: arquivo]...[OLD]...[/OLD][NEW]...[/NEW][/REPLACE], "
            "[EXECUTE: comando real] ou [HUMAN_TEST: auto].\n"
            + repeated_warning
            + "\nRESPOSTA ANTERIOR SEM AÇÃO:\n"
            + str(response_text or "")[:7000]
        )
        if extra_context:
            recovery_context += "\n\nCONTEXTO TÉCNICO ANTERIOR:\n" + str(extra_context)[-8000:]

        # Esta função é chamada por uma thread de trabalho. Usar ``after`` de
        # Tk diretamente daqui pode falhar silenciosamente em alguns Windows e
        # deixar a missão parada. Um Timer agenda a próxima rodada fora da UI;
        # _run_ai_task por sua vez cria a thread de trabalho e atualiza a tela
        # por seus métodos sincronizados.
        def resume_same_mission():
            if self.is_task_cancelled(task_id):
                return
            self._run_ai_task(
                "Continue a missão com uma ação executável da IDE.",
                image_path=image_path,
                extra_context=recovery_context,
                task_objective=task_objective or self.active_ai_objective,
                action_depth=action_depth + 1,
                task_id=task_id,
            )

        timer = threading.Timer(0.45, resume_same_mission)
        timer.daemon = True
        timer.start()
        return True

    def web_chat_visual_delivery_problem(self, image_path):
        """Valida a entrega visual pelo estado do navegador, não por tag textual.

        Quando o WebView confirma preview/arquivo enviado (ou a imagem já aparece
        na mensagem do usuário), o print está disponível para o Chat Web mesmo
        que o modelo responda diretamente com uma ação e não escreva um recibo.
        """
        if not image_path or getattr(self.engine, "provider", "") != "web_chat":
            return ""
        delivery = getattr(self.engine, "latest_web_chat_delivery", {})
        if not isinstance(delivery, dict):
            return "O navegador não devolveu o estado da entrega visual."
        if not delivery.get("attachments_requested"):
            return str(delivery.get("attachment_error") or "A IDE não conseguiu preparar o print para o Chat Web.")
        if not delivery.get("ok"):
            return str(delivery.get("error") or "O Chat Web não concluiu o envio do print.")
        attachment_error = str(delivery.get("attachment_error") or "").strip()
        if attachment_error:
            return attachment_error
        if not delivery.get("attachment_verified"):
            return "O navegador não confirmou o recebimento do print antes do envio."
        if int(delivery.get("attachment_count") or 0) <= 0:
            return "O navegador não confirmou a seleção do print para upload."
        receipt = str(delivery.get("visual_receipt") or "").strip().lower()
        if receipt == "missing":
            return "O próprio Chat Web informou que a imagem não ficou visível para análise."
        # receipt pode ser 'received', 'conversation_confirmed' ou
        # 'transport_confirmed'. Todos representam entrega confirmada.
        if receipt in {"received", "conversation_confirmed", "transport_confirmed"}:
            return ""
        return "O navegador enviou a mensagem, mas não conseguiu confirmar o anexo."

    def retry_web_chat_visual_delivery(
        self,
        *,
        command,
        image_path,
        extra_context,
        task_objective,
        action_depth,
        task_id,
        reason,
    ):
        """Repete o envio visual sem criar conversa; depois deixa o ciclo seguir.

        A tarefa nunca é marcada como validada sem entrega técnica confirmada,
        mas uma falha de upload também não pode encerrar toda a missão de desenvolvimento.
        """
        metrics = self.ai_task_metrics.setdefault(task_id, {})
        retries = int(metrics.get("web_chat_visual_delivery_retries", 0) or 0)
        try:
            limit = max(1, min(3, int(self.settings.get("web_chat_visual_delivery_retries", 2))))
        except (AttributeError, TypeError, ValueError):
            limit = 2
        if retries >= limit:
            return False
        metrics["web_chat_visual_delivery_retries"] = retries + 1
        self.log_agent(f"Evidência visual sem confirmação técnica ({retries + 1}/{limit}): {reason}")
        delivery = getattr(self.engine, "latest_web_chat_delivery", {})
        delivery_state = str(delivery.get("attachment_delivery") or "desconhecido") if isinstance(delivery, dict) else "desconhecido"
        self.add_chat_message(
            "Sistema",
            "O navegador não confirmou o envio técnico do print. A IDE vai reenviar a mesma evidência na conversa atual, sem abrir outra sessão. "
            f"Estado técnico do anexo: {delivery_state}.",
        )
        retry_context = (
            f"{extra_context or ''}\n\n"
            "RECUPERAÇÃO DE EVIDÊNCIA VISUAL:\n"
            "A mensagem anterior não confirmou tecnicamente o anexo. O mesmo print será anexado de novo. "
            "Depois de receber a imagem, responda diretamente com a próxima ação executável da IDE ou com uma conclusão objetiva.\n"
            f"Motivo técnico anterior: {reason}"
        ).strip()
        self._run_ai_task(
            command,
            image_path=image_path,
            extra_context=retry_context,
            task_objective=task_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )
        return True

    def continue_after_visual_delivery_failure(
        self,
        *,
        command,
        extra_context,
        task_objective,
        action_depth,
        task_id,
        reason,
    ):
        """Mantém a missão ativa após bloqueio de mídia sem forjar validação."""
        metrics = self.ai_task_metrics.setdefault(task_id, {})
        if metrics.get("visual_delivery_fallback_started"):
            return False
        metrics["visual_delivery_fallback_started"] = True
        self.add_chat_message(
            "Sistema",
            "A evidência visual não foi confirmada pelo navegador. A missão continua com diagnóstico, código e testes locais; a IDE não aceitará conclusão de validação visual sem entrega técnica confirmada.",
        )
        fallback_context = (
            f"{extra_context or ''}\n\n"
            "BLOQUEIO EXTERNO DE EVIDÊNCIA VISUAL:\n"
            f"O navegador não confirmou o envio do print após as tentativas automáticas. Motivo: {reason}\n"
            "Continue a missão sem afirmar que a tela foi validada. Use o diagnóstico do terminal, READ/SEARCH_TEXT, "
            "edições e testes reais para corrigir o que puder. Quando precisar novamente de validação visual, use [HUMAN_TEST: auto]."
        ).strip()
        self._run_ai_task(
            "Continue a mesma missão. A validação visual permanece pendente; faça a próxima correção ou teste real.",
            extra_context=fallback_context,
            task_objective=task_objective,
            action_depth=action_depth + 1,
            task_id=task_id,
        )
        return True

    def _run_ai_task(self, command, image_path=None, extra_context=None, task_objective=None, action_depth=0, task_id=None, answer_only=False):
        def process():
            retry_available = False
            stream_started = False
            streamed_text = []
            objective = task_objective or command
            current_task_id = task_id
            if not task_objective:
                self.current_task_id += 1
                current_task_id = self.current_task_id
                if not answer_only:
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
                autonomy_directive = (
                    "- Modo irrestrito ativo: o modelo configurado dirige a estrategia, escolhe ferramentas e continua os ciclos necessarios ate concluir.\n"
                    "- A IDE nao deve substituir uma tarefa executavel por relatorio local, briefing ou classificacao de gatilhos.\n"
                    "- Limites restantes: escopo do workspace e confirmacao para efeitos externos sensiveis ou irreversiveis.\n"
                    if self.model_directed_autonomy_enabled()
                    else ""
                )
                context = (
                    f"Workspace atual: {self.current_workspace}\n\n"
                    f"MISSAO ATIVA DA IA:\n{objective}\n\n"
                    "MODO CODEX DA IDE:\n"
                    f"{autonomy_directive}"
                    "- Comporte-se como um agente de engenharia, nao como um assistente passivo.\n"
                    "- Use raciocinio altissimo: antes de responder, escolha o proximo passo que realmente muda, executa, valida ou conclui.\n"
                    "- Para perguntas simples ou perguntas de capacidade, responda direto antes de qualquer execucao.\n"
                    "- Se o usuario perguntar se voce consegue/pode fazer algo, explique a capacidade e o que falta; nao execute comandos nem edite arquivos nessa rodada.\n"
                    "- A mensagem mais recente do usuario tem prioridade sobre historico, conversa recente, MISSAO ORIGINAL e MISSAO ATIVA quando ela pedir algo diferente.\n"
                    "- Continue uma missao anterior somente quando o pedido atual for claramente de continuidade, como 'continue', 'termine' ou 'faca isso'.\n"
                    "- Para analise/planejamento, entregue diagnostico completo em texto e nao execute/edite sem pedido claro.\n"
                    "- Para implementacao/correcao, avance ate uma mudanca aplicada e uma verificacao plausivel.\n"
                    "- Trabalhe de forma produtiva: responda com conclusao util, diagnostico objetivo ou acao real quando precisar mexer no projeto.\n"
                    "- PROTOCOLO INCREMENTAL V9 ATIVO: esta instrucao substitui V8 e qualquer regra anterior conflitante. Arquivo grande nao exige reescrita completa. Use [READ: arquivo | linhas inicio-fim] e [SEARCH_TEXT] para obter somente o contexto necessario; aplique [REPLACE] ou [PATCH] para mudancas locais e [WRITE] apenas para arquivo novo ou reescrita intencional.\n"
                    "- Se afirmar que alterou, executou ou validou, garanta que houve acao real da IDE ou mudanca direta detectavel no workspace.\n"
                    "- Leia o contexto necessario sem entrar em loop; prefira agir quando ja houver informacao suficiente.\n"
                    "- Preserve a estrutura existente e faca alteracoes pequenas quando o projeto ja funciona.\n"
                    "- Depois de editar, valide com uma tag EXECUTE/EXECUTE_ADMIN ja preenchida, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST] quando isso for util.\n\n"
                    "Continue essa missao de forma autonoma. "
                    "Atue como especialista senior em desenvolvimento de sistemas, apps e jogos: diagnostique, implemente, valide e corrija ate resolver. "
                    "Se precisar de arquivo, use [READ] ou ferramenta direta equivalente; o conteudo retornado passa a ser sua memoria de trabalho. Nunca use [EXECUTE] para imprimir, enumerar ou extrair linhas de arquivo: isso deve ser [READ]. "
                    "Se precisar verificar se um recurso/termo existe no projeto, use [SEARCH_TEXT: padrao | arquivo] ou busca direta equivalente. "
                    "Se a solucao depender de informacao atual, documentacao externa ou erro desconhecido, use [WEB_SEARCH: consulta objetiva] para a IDE buscar na internet. "
                    "Use [SCAN_TEXT]/[FIX_MOJIBAKE] ou ferramenta direta equivalente quando fizer sentido para a missao. "
                    "Para criar arquivo novo ou reescrever um arquivo inteiro, use [WRITE] com conteúdo completo. "
                    "Para mudar um trecho de arquivo existente, use [REPLACE] com [OLD] exato e [NEW] ou [PATCH] incremental no formato *** Begin Patch / *** Update File / hunks @@ / *** End Patch. Nao exija o arquivo inteiro quando uma mudanca local resolver. "
                    "Se precisar rodar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest]. "
                    "Se o comando realmente exigir administrador no Windows, use uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups]; nao escreva 'como administrador' dentro do comando. "
                    "Nunca use reticencias, 'comando', 'comando real', texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real. "
                    "Nunca copie literalmente 'comando concreto' nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto. "
                    "Para testar projeto HTML/Web, use [EXECUTE: python -m http.server 8000]; a IDE troca pelo Python real, escolhe porta livre e valida a URL. "
                    "Para abrir uma pagina validada no navegador interno da IDE, use [OPEN_URL: http://127.0.0.1:porta/]. "
                    "Para controlar a pagina, comece com [BROWSER_INSPECT: pagina]; use os refs retornados em [BROWSER_CLICK: e1] e [BROWSER_TYPE: e2 | texto], ou role com [BROWSER_SCROLL: down]. "
                    "Use somente uma acao BROWSER por resposta e inspecione novamente depois que a pagina mudar. No modo irrestrito, interacoes web comuns sao autoaprovadas; apenas dados sensiveis e acoes destrutivas/financeiras podem pedir autorizacao. "
                    "Para validar visualmente um app/jogo como usuario, use [HUMAN_TEST: auto]; a IDE executa, abre, espera a tela, captura print e devolve a imagem para voce analisar. "
                    "Use [SCREENSHOT: tela] apenas quando a tela ja estiver aberta. "
                    "Depois de analisar o print, corrija com [REPLACE] ou [WRITE] e teste novamente ate funcionar. "
                    "Para arquivo grande, prefira [READ: arquivo | linhas inicio-fim] e [SEARCH_TEXT: padrao | arquivo]. A IDE devolve o intervalo exato sem forcar reescrita completa. "
                    "Entenda a estrutura antes de editar, mas nao fique repetindo leituras do mesmo arquivo. "
                    "Em tarefa grande, continue coletando apenas os intervalos e buscas que forem indispensaveis para uma alteracao segura; depois aja com [REPLACE], [PATCH], [WRITE], EXECUTE/EXECUTE_ADMIN, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]. "
                    "Evite narrar intencoes vazias; avance com leitura, alteracao, validacao ou uma conclusao clara. "
                    "Nao peca o objetivo novamente enquanto houver uma missao ativa, mas se o usuario acabou de pedir algo diferente, substitua a missao anterior pelo pedido atual.\n\n"
                    f"Alteracoes recentes feitas pela IDE neste projeto:\n{self.format_recent_changes_for_agent(limit=8)}\n\n"
                    f"Conversa recente que deve ser preservada:\n{self.build_recent_ai_context_memory(limit=18)}\n\n"
                    f"{self.build_smart_task_brief(command, objective=objective)}\n\n"
                    f"Arquivos do workspace:\n{self.get_workspace_tree(limit=220)}"
                )
                local_training_context = self.build_local_training_context(objective or command)
                if local_training_context:
                    context += f"\n\n{local_training_context}"
                # Contexto geral vem antes. Resultados de execucao, erros reais e
                # estado de recuperacao precisam ir no final: o bridge do Chat Web
                # compacta prompts longos preservando cabeca e cauda; quando o
                # diagnostico ficava no meio, podia ser descartado antes do envio.
                context += f"\n\n{self.build_project_intelligence_context()}"
                visual_contract = self.build_web_chat_visual_receipt_contract(image_path)
                priority_tail = []
                if extra_context:
                    priority_tail.append(
                        "CONTEXTO TECNICO PRIORITARIO DA IDE — nao omita este bloco na proxima acao:\n"
                        + str(extra_context)
                    )
                if visual_contract:
                    priority_tail.append(visual_contract)
                if priority_tail:
                    context += "\n\n" + "\n\n".join(priority_tail)

                direct_snapshot = self.snapshot_workspace_for_direct_actions()
                response = self.engine.generate_solution(
                    command,
                    image_path=image_path,
                    code_context=context,
                    stream_callback=on_stream,
                    workspace_path=self.current_workspace,
                    approval_callback=self.ask_codex_app_server_approval,
                )
                streamed_joined = "".join(streamed_text)
                if self.is_task_cancelled(current_task_id):
                    return
                visual_delivery_problem = self.web_chat_visual_delivery_problem(image_path)
                if visual_delivery_problem:
                    if self.retry_web_chat_visual_delivery(
                        command=command,
                        image_path=image_path,
                        extra_context=extra_context,
                        task_objective=objective,
                        action_depth=action_depth,
                        task_id=current_task_id,
                        reason=visual_delivery_problem,
                    ):
                        return
                    self.log_agent(f"Evidência visual indisponível após tentativas: {visual_delivery_problem}")
                    if self.continue_after_visual_delivery_failure(
                        command=command,
                        extra_context=extra_context,
                        task_objective=objective,
                        action_depth=action_depth,
                        task_id=current_task_id,
                        reason=visual_delivery_problem,
                    ):
                        return
                    self.last_response = (
                        "A validação visual continua pendente porque o Chat Web não confirmou o print. "
                        "O código e os testes locais foram preservados; a missão não foi marcada como concluída.\n\n"
                        f"Motivo técnico: {visual_delivery_problem}"
                    )
                    self.add_chat_message("Erro", self.last_response)
                    self.set_status("Validação visual pendente de confirmação.", "warning")
                    return
                self.last_response = response or streamed_joined
                provider_failed = self.is_external_model_failure_response(self.last_response)
                try:
                    provider_failed = provider_failed or self.engine.should_try_external_ai_fallback(self.last_response)
                except Exception:
                    pass
                if provider_failed and not image_path:
                    self.add_chat_message(
                        "Sistema",
                        "Os provedores configurados ficaram indisponiveis. Continuando a mesma missao pelo chat web...",
                    )
                    browser_fallback = self.request_browser_ai_fallback(
                        objective,
                        code_context=context,
                        task_id=current_task_id,
                    )
                    if browser_fallback:
                        self.last_response = browser_fallback
                local_fallback = self.local_llm_fallback_reply(
                    command,
                    self.last_response,
                    image_path=image_path,
                )
                if local_fallback:
                    self.last_response = local_fallback
                if not (self.last_response or "").strip():
                    self.last_response = (
                        f"{self.ai_assistant_display_name()} terminou sem devolver texto para a IDE. "
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
                if answer_only:
                    cleaned_answer = self.strip_agent_action_markup(self.last_response or "").strip()
                    if cleaned_answer:
                        self.last_response = cleaned_answer

                # O navegador mantém a mídia na conversa do provedor. Exibir a
                # confirmação aqui torna a criação de imagem/áudio verificável na
                # própria IDE, sem tentar baixar URLs autenticadas de terceiros.
                web_artifacts = getattr(self.engine, "latest_web_chat_artifacts", {})
                if (
                    getattr(self.engine, "provider", "") == "web_chat"
                    and isinstance(web_artifacts, dict)
                ):
                    image_count = len(web_artifacts.get("images") or [])
                    audio_count = len(web_artifacts.get("audio") or [])
                    if image_count or audio_count:
                        generated = []
                        if image_count:
                            generated.append(f"{image_count} imagem(ns)")
                        if audio_count:
                            generated.append(f"{audio_count} áudio(s)")
                        self.add_chat_message(
                            "Sistema",
                            "Chat Web detectou "
                            + " e ".join(generated)
                            + ". O resultado permanece disponível na conversa restaurada do navegador interno.",
                        )
                direct_changes, direct_change_total = self.detect_direct_workspace_changes(direct_snapshot)
                direct_action_happened = direct_change_total > 0
                if direct_action_happened:
                    self.register_direct_workspace_changes(
                        direct_changes,
                        direct_change_total,
                        task_id=current_task_id,
                    )

                invalid_claim = False
                web_chat_recovery_reason = self.web_chat_response_recovery_reason(
                    self.last_response,
                    task_objective=objective,
                    task_id=current_task_id,
                    direct_action_happened=direct_action_happened,
                )
                if web_chat_recovery_reason:
                    invalid_claim = self.claims_concrete_result_without_real_action(
                        self.last_response,
                        task_objective=objective,
                    )
                    self.log_agent(
                        "Chat Web devolveu texto sem ação verificável; iniciando recuperação contínua: "
                        + web_chat_recovery_reason
                    )
                has_agent_action = self.response_has_agent_action(self.last_response)
                display_response = self.build_ai_display_response(
                    self.last_response,
                    direct_changes=direct_changes,
                    direct_change_total=direct_change_total,
                    has_agent_action=has_agent_action,
                    invalid_claim=invalid_claim,
                )
                stream_visible = self.streaming_textbox is not None
                if stream_visible:
                    if has_agent_action:
                        self.replace_stream_message(display_response or "A IDE recebeu uma acao interna e vai executar agora.")
                    elif (
                        display_response
                        and display_response.strip()
                        and display_response.strip() != streamed_joined.strip()
                    ):
                        self.replace_stream_message(display_response)
                    elif response and response.strip() and response.strip() not in streamed_joined.strip():
                        self.append_stream_message("\n\n" + response)
                    self.finish_stream_message()
                else:
                    if display_response or not has_agent_action:
                        self.add_chat_message(self.ai_assistant_display_name(), display_response or self.last_response)
                if web_chat_recovery_reason and not answer_only:
                    if self.continue_after_web_chat_protocol_stall(
                        command=command,
                        image_path=image_path,
                        extra_context=extra_context,
                        task_objective=objective,
                        action_depth=action_depth,
                        task_id=current_task_id,
                        response_text=self.last_response,
                        reason=web_chat_recovery_reason,
                    ):
                        return
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
                if not answer_only:
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
        return bool(self.extract_agent_action_names(text))


    # MEROTEC_AUTONOMOUS_STRIP_V2
    def strip_agent_action_markup(self, text):
        if not text:
            return ""
        cleaned = text
        for pattern in (
            r"\[PATCH(?:\s*:\s*[^\]\r\n]+)?\].*?\[/PATCH\]",
            r"\[WRITE:\s*.+?\].*?\[/WRITE\]",
            r"\[REPLACE:\s*.+?\].*?\[/REPLACE\]",
        ):
            cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        # ``.*`` até o fim da linha preserva comandos com regex/listas, por
        # exemplo [EXECUTE: python -c "re.findall(r'<script[^>]*>', html)"].
        action_names = (
            "READ|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|"
            "EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|"
            "BROWSER_SCROLL|BROWSER_CHAT|SCREENSHOT|HUMAN_TEST"
        )
        # Mantém a visualização coerente com o executor: além da forma
        # canônica [READ: arquivo], remove as variantes que chats web às vezes
        # produzem, como [READ] arquivo e READ arquivo.
        action_line = re.compile(
            rf"^\s*(?:\[(?:{action_names})(?:[ \t]*:\s*[^\r\n]*)?\][ \t]*[^\r\n]*|(?:{action_names})[ \t]*(?::|[ \t]+)[ \t]*.+)\s*$",
            re.IGNORECASE,
        )
        kept = [line for line in cleaned.splitlines() if not action_line.fullmatch(line)]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    def action_execution_message(self, text):
        if not text:
            return ""
        tags = self.extract_agent_action_names(text)
        if tags & {"PATCH", "WRITE", "REPLACE", "FIX_MOJIBAKE", "UNDO"}:
            return "A IDE recebeu uma alteracao real e iniciou aplicacao, validacao e teste automatico quando necessario."
        if tags & {"EXECUTE", "EXECUTE_ADMIN", "OPEN_URL", "BROWSER_INSPECT", "BROWSER_CLICK", "BROWSER_TYPE", "BROWSER_SCROLL", "BROWSER_CHAT", "SCREENSHOT", "HUMAN_TEST"}:
            return "A IDE recebeu uma execucao real e iniciou a validacao."
        if tags & {"READ", "SEARCH_TEXT", "WEB_SEARCH", "SCAN_TEXT"}:
            return "A IDE esta coletando contexto objetivo para executar o proximo passo."
        return ""

    def build_ai_display_response(
        self,
        response_text,
        direct_changes=None,
        direct_change_total=0,
        has_agent_action=None,
        invalid_claim=False,
    ):
        if has_agent_action is None:
            has_agent_action = self.response_has_agent_action(response_text)
        if has_agent_action:
            return self.action_execution_message(response_text)

        if invalid_claim:
            return (
                "A IDE bloqueou uma resposta que dizia ter executado/corrigido, "
                "mas nao trouxe uma acao real. A proxima resposta precisa vir com uma acao executavel."
            )

        display_response = self.strip_agent_action_markup(response_text)
        if direct_change_total > 0:
            direct_summary = self.format_direct_workspace_changes(
                direct_changes or [],
                direct_change_total,
            )
            if display_response:
                return f"{display_response}\n\n{direct_summary}"
            return direct_summary

        return display_response


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

        if self.command_output_is_placeholder_error(command, text):
            layer = "Comando placeholder"
            likely_cause = "Foi executado texto incompleto ou descritivo em vez de um comando real."
            guidance.extend([
                "Nao repita esse comando.",
                "Use EXECUTE_ADMIN somente com comando real ja preenchido, por exemplo [EXECUTE_ADMIN: whoami /groups].",
                "Se ainda nao existe comando real para rodar, entregue uma conclusao direta ao usuario.",
            ])
        elif self.command_output_requires_admin(text):
            layer = "Permissao / UAC do Windows"
            likely_cause = "O comando tentou uma operacao que exige privilegios de administrador."
            guidance.extend([
                "A IDE pode pedir autorizacao do usuario com uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].",
                "Nao execute reticencias nem escreva 'como administrador' dentro de [EXECUTE].",
                "Se o usuario negar o UAC, entregue uma alternativa sem administrador ou explique o bloqueio.",
            ])
        elif "generated_plugin_registrant.h" in text and ("c1083" in normalized or "no such file" in normalized):
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
        markers = ("error", "erro", "fatal", "exception", "lnk", "c1083", "failed", "denied", "negado", "elevation")
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

    def stop_audio_playback(self):
        self.voice.stop()
        self.set_status("Leitura de audio parada.", "ready")

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
                ("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.webm"),
                ("Todos", "*.*"),
            ],
        )
        if not image_path:
            return
        self.add_chat_message("Sistema", f"Imagem enviada para analise: {image_path}")
        self._run_ai_task("Analise a imagem e aponte problemas, oportunidades ou proximos passos.", image_path=image_path)



# MEROTEC_VISUAL_AUTONOMY_V3
def _merotec_visual_browser_state(self):
    state = getattr(self, "_merotec_visual_browser", None)
    if not isinstance(state, dict):
        state = {
            "process": None,
            "reader": None,
            "ready": __import__("threading").Event(),
            "url": "",
            "title": "",
            "last_error": "",
        }
        self._merotec_visual_browser = state
    return state


def _merotec_read_visual_browser_events(self, process):
    state = self._merotec_visual_browser_state()
    try:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            event_name = str(event.get("event") or "")
            if event_name in {"ready", "navigated"}:
                state["url"] = str(event.get("url") or state.get("url") or "")
                state["ready"].set()
                self.set_internal_browser_status(
                    f"Teste visual aberto em navegador dedicado: {state['url']}",
                    "ready",
                )
            elif event_name in {"error", "command_error"}:
                state["last_error"] = str(event.get("message") or "Falha no navegador de teste.")
                self.log_agent(f"Navegador visual: {state['last_error']}")
            elif event_name == "closed":
                state["ready"].clear()
    finally:
        if state.get("process") is process:
            state["process"] = None
            state["ready"].clear()


def _merotec_open_visual_test_browser(self, url, task_id=None):
    normalized = self.normalize_internal_browser_url(url)
    if not normalized:
        return {"opened": False, "error": "URL de teste visual vazia."}

    state = self._merotec_visual_browser_state()
    state["url"] = normalized
    state["last_error"] = ""
    state["ready"].clear()
    workspace_name = Path(str(getattr(self, "current_workspace", "projeto"))).name or "projeto"
    title = f"Merotec IA - Teste Visual - {workspace_name}"
    state["title"] = title

    process = state.get("process")
    if process is not None and process.poll() is None:
        if self._write_internal_browser_command(process, "navigate", url=normalized):
            if threading.current_thread() is not threading.main_thread():
                state["ready"].wait(timeout=12)
            return {
                "opened": bool(state["ready"].is_set()),
                "url": normalized,
                "title": title,
                "error": state.get("last_error", ""),
            }

    helper = PROJECT_ROOT / "modules" / "browser_runtime.py"
    if not helper.exists():
        return {"opened": False, "error": f"Componente ausente: {helper}"}

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["MEROTEC_VISUAL_TEST_BROWSER"] = "1"
    try:
        process = subprocess.Popen(
            [
                sys.executable, "-u", "-m", "modules.browser_runtime",
                "--url", normalized,
                "--title", title,
                "--storage-scope", "visual-tests",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except Exception as exc:
        return {"opened": False, "error": f"Não consegui iniciar o navegador visual: {exc}"}

    state["process"] = process
    reader = threading.Thread(target=self._read_visual_test_browser_events, args=(process,), daemon=True)
    state["reader"] = reader
    reader.start()
    if threading.current_thread() is not threading.main_thread():
        state["ready"].wait(timeout=18)
    return {
        "opened": bool(state["ready"].is_set()),
        "url": normalized,
        "title": title,
        "error": state.get("last_error", ""),
    }


def _merotec_get_visual_test_browser_info(self):
    state = self._merotec_visual_browser_state()
    process = state.get("process")
    if process is None or process.poll() is not None:
        return {}
    return {
        "url": state.get("url", ""),
        "title": state.get("title", ""),
        "ready": bool(state.get("ready") and state["ready"].is_set()),
    }


def _merotec_close_visual_test_browser(self):
    state = self._merotec_visual_browser_state()
    process = state.get("process")
    if process is None or process.poll() is not None:
        return
    self._write_internal_browser_command(process, "close")
    def terminate_later():
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
    threading.Thread(target=terminate_later, daemon=True).start()


_original_merotec_request_app_close = UniversalApp.request_app_close

def _merotec_request_app_close_with_visual_browser(self):
    # Não use ``return`` em ``finally``: além do SyntaxWarning, isso pode
    # mascarar uma falha do navegador e interromper o fechamento normal.
    try:
        self.close_visual_test_browser()
    except Exception as exc:
        try:
            self.log_agent(f"Falha ao fechar navegador visual: {exc}")
        except Exception:
            pass
    return _original_merotec_request_app_close(self)


UniversalApp._merotec_visual_browser_state = _merotec_visual_browser_state
UniversalApp._visual_browser_state = _merotec_visual_browser_state
UniversalApp._read_visual_test_browser_events = _merotec_read_visual_browser_events
UniversalApp.open_visual_test_browser = _merotec_open_visual_test_browser
UniversalApp.get_visual_test_browser_info = _merotec_get_visual_test_browser_info
UniversalApp.close_visual_test_browser = _merotec_close_visual_test_browser
UniversalApp.request_app_close = _merotec_request_app_close_with_visual_browser

# MEROTEC_BROWSER_ERROR_DISPATCH_V1
UniversalApp._merotec_browser_error_dispatch_v1 = True


# MEROTEC_CONFIGURED_PROVIDER_LOCK_V1
# Não troca para ChatGPT/navegador externo nem modelo local quando o provedor
# ativo falha. A tarefa retorna o erro do provedor configurado ao usuário.

def _merotec_locked_browser_ai_fallback_enabled(self):
    return False


def _merotec_locked_request_browser_ai_fallback(self, command, code_context=None, task_id=None):
    self.log_agent(
        "Fallback pelo navegador bloqueado: a tarefa permanece no provedor configurado."
    )
    return ""


def _merotec_locked_is_external_model_failure_response(self, text):
    return False


def _merotec_locked_local_llm_fallback_reply(self, command, external_response="", image_path=None):
    return ""


UniversalApp.browser_ai_fallback_enabled = _merotec_locked_browser_ai_fallback_enabled
UniversalApp.request_browser_ai_fallback = _merotec_locked_request_browser_ai_fallback
UniversalApp.is_external_model_failure_response = _merotec_locked_is_external_model_failure_response
UniversalApp.local_llm_fallback_reply = _merotec_locked_local_llm_fallback_reply

# Inicialize somente depois de registrar todos os patches da aplicação.
if __name__ == "__main__":
    if not _activate_existing_instance():
        app = UniversalApp()
        app.mainloop()

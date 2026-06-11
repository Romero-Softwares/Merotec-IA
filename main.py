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
    IGNORED_DIRS,
    IGNORED_SUFFIXES,
    PROJECT_ROOT,
    SCRATCHPAD_DEFAULT_TEXT,
)
from modules.ai_config import AiConfigMixin
from modules.agent_actions import AgentActionsMixin
from modules.app_state import AppStateMixin
from modules.engine import UniversalEngine
from modules.executor import CodeExecutor
from modules.memory import MemorySubnet
from modules.project_manager import ProjectManager
from modules.ui_theme import THEME
from modules.workspace_intelligence import WorkspaceIntelligenceMixin
from modules.voice import VoiceModule


MAIN_WINDOW_TITLE = f"{APP_NAME} - IA Engineering Workspace"


def _activate_existing_instance():
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
        self.explorer_refresh_job = None

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
            text="↑",
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
            text="↓",
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
                self._position_chat_background()
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
        self._install_chat_scroll_controls()
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
            activate_scrollbars=False,
        )
        editor.grid(row=0, column=1, sticky="nsew")
        self._hide_ctk_textbox_scrollbar(editor)
        editor._line_numbers = line_numbers
        editor.tag_config("current_line", background="#20242c")

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
                or bool(re.search(r"\[(READ|WRITE|REPLACE|EXECUTE|EXECUTE_ADMIN|OPEN_URL|SCREENSHOT|HUMAN_TEST|SEARCH_TEXT|SCAN_TEXT|UNDO)\s*:", chunk, re.IGNORECASE))
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
            r"\[(READ|WRITE|REPLACE|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|SCREENSHOT|HUMAN_TEST)\s*:\s*([^\]]*)\]",
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
                "EXECUTE_ADMIN": "pediu administrador",
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
        elif re.search(r"\[(execute|execute_admin|open_url|screenshot|human_test)\s*:", normalized):
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
                    "- Depois de editar, valide com uma tag EXECUTE/EXECUTE_ADMIN ja preenchida, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST] quando isso for util.\n\n"
                    "Continue essa missao de forma autonoma. "
                    "Atue como especialista senior em desenvolvimento de sistemas, apps e jogos: diagnostique, implemente, valide e corrija ate resolver. "
                    "Se precisar de arquivo, use [READ]; o conteudo retornado pela IDE passa a ser sua memoria de trabalho. "
                    "Se precisar verificar se um recurso/termo existe, use [SEARCH_TEXT: padrao | arquivo]. "
                    "Use [SCAN_TEXT] e [FIX_MOJIBAKE] somente quando a missao pedir correcao de texto, acentos, codificacao ou caracteres corrompidos. "
                    "Nao desvie uma tarefa de interface, logica, camera, build ou execucao para mojibake. "
                    "Se souber a mudanca completa, use [WRITE]. "
                    "Se souber apenas um trecho a trocar, use [REPLACE]. "
                    "Se precisar rodar, use uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest]. "
                    "Se o comando realmente exigir administrador no Windows, use uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups]; nao escreva 'como administrador' dentro do comando. "
                    "Nunca use reticencias, 'comando', 'comando real', texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real. "
                    "Nunca copie literalmente 'comando concreto' nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto. "
                    "Para testar projeto HTML/Web, use [EXECUTE: python -m http.server 8000]; a IDE troca pelo Python real, escolhe porta livre e valida a URL. "
                    "Para abrir uma pagina validada, use [OPEN_URL: http://127.0.0.1:porta/]. "
                    "Para validar visualmente um app/jogo como usuario, use [HUMAN_TEST: auto]; a IDE executa, abre, espera a tela, captura print e devolve a imagem para voce analisar. "
                    "Use [SCREENSHOT: tela] apenas quando a tela ja estiver aberta. "
                    "Depois de analisar o print, corrija com [REPLACE] ou [WRITE] e teste novamente ate funcionar. "
                    "Para arquivo grande, prefira [READ: arquivo] uma vez; a IDE fara uma varredura completa e entregara um mapa do arquivo. "
                    "Entenda a estrutura antes de editar, mas nao fique repetindo leituras do mesmo arquivo. "
                    "Em tarefa grande, faca no maximo algumas leituras estrategicas; depois aja com [REPLACE], [WRITE], uma tag EXECUTE/EXECUTE_ADMIN ja preenchida, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST]. "
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
                    approval_callback=self.ask_codex_app_server_approval,
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
        return bool(self.extract_agent_action_names(text))

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
            r"^[ \t]*\[(READ|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|SCREENSHOT|HUMAN_TEST)[ \t]*:[^\]\r\n]+\][ \t]*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def action_execution_message(self, text):
        if not text:
            return ""
        tags = self.extract_agent_action_names(text)
        if tags & {"WRITE", "REPLACE", "FIX_MOJIBAKE", "UNDO"}:
            return "A IDE recebeu uma alteracao real e iniciou a aplicacao no projeto."
        if tags & {"EXECUTE", "EXECUTE_ADMIN", "OPEN_URL", "SCREENSHOT", "HUMAN_TEST"}:
            return "A IDE recebeu uma execucao real e iniciou a validacao."
        if tags & {"READ", "SEARCH_TEXT", "SCAN_TEXT"}:
            return "A IDE esta coletando contexto objetivo para executar o proximo passo."
        return ""


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
                ("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp", "*.gif", "*.webm"),
                ("Todos", "*.*"),
            ],
        )
        if not image_path:
            return
        self.add_chat_message("Sistema", f"Imagem enviada para analise: {image_path}")
        self._run_ai_task("Analise a imagem e aponte problemas, oportunidades ou proximos passos.", image_path=image_path)


if __name__ == "__main__":
    if not _activate_existing_instance():
        app = UniversalApp()
        app.mainloop()

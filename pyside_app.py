"""Interface PySide6 da Merotec IA IDE.

O nucleo da aplicacao continua nos modulos existentes; esta camada substitui a
janela Tk por uma superficie desktop inspirada no mockup da IDE.
"""

from __future__ import annotations

import locale
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import ImageGrab

from PySide6.QtCore import QModelIndex, QDir, QFileInfo, QProcess, QProcessEnvironment, QSortFilterProxyModel, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QStyle, QTabWidget, QTextEdit, QToolBar, QTreeView, QProgressBar,
    QVBoxLayout, QWidget, QFileSystemModel, QScrollArea,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl

from modules.engine import UniversalEngine
from modules.executor import CodeExecutor
from modules.qt_ui_bridge import QtUiBridge
from modules.ui_web_chat_bridge import InternalBrowserWebChatBridge
from modules.app_constants import APP_CHANGE_HISTORY_FILE, APP_HISTORY_FILE, APP_SETTINGS_FILE, PROJECTS_DIR, IGNORED_SUFFIXES, is_ignored_dir_name
from modules.app_state import AppStateMixin
from modules.workspace_intelligence import WorkspaceIntelligenceMixin
from modules.memory import MemorySubnet
from modules.qt_settings_dialog import QtSettingsDialog
from modules.qt_agent_actions import QtAgentActions
from modules.project_manager import ProjectManager
from modules.voice import VoiceModule
from modules.plugin_manager import build_plugin_report_messages, initialize_plugins
from modules.video_generation import VIDEO_SUFFIXES, VideoGenerationRequest, VideoGenerationService


ROOT = Path(__file__).resolve().parent
ACCENT = "#18c9e8"
APP_ICON_PATH = ROOT / "assets" / "merotec-ide-icon.svg"


class PythonHighlighter(QSyntaxHighlighter):
    """Coloracao leve para manter o editor legivel sem dependencia extra."""

    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#d58ae8"))
        keywords = "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split()
        for word in keywords:
            self.rules.append((rf"\b{word}\b", keyword_format))
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#e8a36a"))
        self.rules.extend([(r"'[^'\n]*'", string_format), (r'"[^"\n]*"', string_format)])
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#667a91"))
        self.rules.append((r"#[^\n]*", comment_format))
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#63b7ff"))
        self.rules.append((r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()", function_format))

    def highlightBlock(self, text):
        from re import finditer
        for pattern, text_format in self.rules:
            for match in finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), text_format)


class GettingStartedHighlighter(QSyntaxHighlighter):
    """Destaca orientacoes e comandos na aba informativa inicial."""

    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        body_format = QTextCharFormat()
        body_format.setForeground(QColor("#aebdcb"))
        self.rules.append((r"^#.*$", body_format))

        title_format = QTextCharFormat()
        title_format.setForeground(QColor(ACCENT))
        title_format.setFontWeight(QFont.Weight.Bold)
        self.rules.append((r"^# Bem-vindo.*$", title_format))

        section_format = QTextCharFormat()
        section_format.setForeground(QColor("#7cc7ff"))
        section_format.setFontWeight(QFont.Weight.Bold)
        self.rules.append((r"^# (Como comecar|Teste visual de projetos|Terminal|Atalhos)$", section_format))

        step_format = QTextCharFormat()
        step_format.setForeground(QColor("#d8e6f2"))
        self.rules.append((r"^# \d+\..*$", step_format))

        command_format = QTextCharFormat()
        command_format.setForeground(QColor("#ffc96b"))
        command_format.setFontWeight(QFont.Weight.DemiBold)
        self.rules.append((r"`[^`\n]+`", command_format))

        url_format = QTextCharFormat()
        url_format.setForeground(QColor("#57d9bf"))
        self.rules.append((r"https?://[^\s`]+", url_format))

    def highlightBlock(self, text):
        from re import finditer
        for pattern, text_format in self.rules:
            for match in finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), text_format)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editor")
        self.setFont(QFont("Cascadia Mono", 11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = PythonHighlighter(self.document())
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_area_width(0)
        self._highlight_current_line()

    def line_number_area_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 13 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, _count):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        content = self.contentsRect()
        self.line_number_area.setGeometry(content.left(), content.top(), self.line_number_area_width(), content.height())

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#09131f"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor("#77a0ba") if block == self.textCursor().block() else QColor("#43576a"))
                painter.drawText(0, top, self.line_number_area.width() - 7, self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1

    def _highlight_current_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#102238"))
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Tab and modifiers == Qt.KeyboardModifier.NoModifier:
            self._indent_selection()
            return
        if key == Qt.Key.Key_Backtab:
            self._outdent_selection()
            return
        if key == Qt.Key.Key_Slash and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.toggle_comment()
            return
        pairs = {"(": ")", "[": "]", "{": "}", "'": "'", '"': '"'}
        text = event.text()
        if text in pairs and not (modifiers & Qt.KeyboardModifier.ControlModifier):
            cursor = self.textCursor()
            selected = cursor.selectedText()
            if selected:
                cursor.insertText(text + selected + pairs[text])
            else:
                cursor.insertText(text + pairs[text])
                cursor.movePosition(cursor.MoveOperation.Left)
                self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)

    def _line_range_cursor(self):
        cursor = self.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        cursor.setPosition(start)
        cursor.movePosition(cursor.MoveOperation.StartOfBlock)
        start = cursor.position()
        cursor.setPosition(end)
        if end > start and cursor.atBlockStart():
            cursor.movePosition(cursor.MoveOperation.PreviousBlock)
        cursor.movePosition(cursor.MoveOperation.EndOfBlock)
        return start, cursor.position()

    def _indent_selection(self):
        start, end = self._line_range_cursor()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start)
        while cursor.position() <= end:
            cursor.insertText("    ")
            if not cursor.movePosition(cursor.MoveOperation.NextBlock):
                break
            end += 4
        cursor.endEditBlock()

    def _outdent_selection(self):
        start, end = self._line_range_cursor()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start)
        while cursor.position() <= end:
            cursor.movePosition(cursor.MoveOperation.StartOfBlock)
            block_text = cursor.block().text()
            remove = 4 if block_text.startswith("    ") else 1 if block_text.startswith("\t") else 0
            if remove:
                cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.KeepAnchor, remove)
                cursor.removeSelectedText()
                end -= remove
            if not cursor.movePosition(cursor.MoveOperation.NextBlock):
                break
        cursor.endEditBlock()

    def toggle_comment(self):
        start, end = self._line_range_cursor()
        cursor = self.textCursor()
        blocks = []
        cursor.setPosition(start)
        while True:
            blocks.append(cursor.block())
            if cursor.position() >= end or not cursor.movePosition(cursor.MoveOperation.NextBlock):
                break
        uncomment = all(block.text().lstrip().startswith("#") for block in blocks if block.text().strip())
        cursor.beginEditBlock()
        for block in blocks:
            cursor.setPosition(block.position())
            text = block.text()
            indent = len(text) - len(text.lstrip())
            cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.MoveAnchor, indent)
            if uncomment and text[indent:].startswith("#"):
                cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.KeepAnchor, 2 if text[indent:].startswith("# ") else 1)
                cursor.removeSelectedText()
            elif not uncomment:
                cursor.insertText("# ")
        cursor.endEditBlock()


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class RecursiveFileFilter(QSortFilterProxyModel):
    """Mantem pastas que contenham qualquer arquivo correspondente ao filtro."""

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False
        if not self.filterRegularExpression().pattern():
            return True
        if self.filterRegularExpression().match(model.fileName(index)).hasMatch():
            return True
        for child_row in range(model.rowCount(index)):
            if self.filterAcceptsRow(child_row, index):
                return True
        return False


class TerminalInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = []
        self.history_index = 0

    def remember(self, command):
        if command and (not self.history or self.history[-1] != command):
            self.history.append(command)
        self.history_index = len(self.history)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up and self.history:
            self.history_index = max(0, self.history_index - 1)
            self.setText(self.history[self.history_index])
            return
        if event.key() == Qt.Key.Key_Down and self.history:
            self.history_index = min(len(self.history), self.history_index + 1)
            self.setText(self.history[self.history_index] if self.history_index < len(self.history) else "")
            return
        super().keyPressEvent(event)


class ChatInput(QPlainTextEdit):
    """Compositor que envia com Enter e converte prints colados em anexos."""

    def __init__(self, on_image_paste, on_submit, parent=None):
        super().__init__(parent)
        self._on_image_paste = on_image_paste
        self._on_submit = on_submit

    def keyPressEvent(self, event):
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self._on_submit()
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source is not None and source.hasImage() and self._on_image_paste():
            return
        super().insertFromMimeData(source)


class ChatBubble(QFrame):
    def __init__(self, author: str, message: str, outgoing=False, attachments=None):
        super().__init__()
        self.setObjectName("chatOutgoing" if outgoing else "chatIncoming")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        self.label = QLabel(message)
        # Diagnósticos podem conter trechos como <html>; mostre-os literalmente.
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setObjectName("chatText")
        layout.addWidget(self.label)
        self.video_players = []
        for path in attachments or []:
            path = Path(path)
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    preview = QLabel()
                    preview.setObjectName("chatImagePreview")
                    preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    preview.setPixmap(
                        pixmap.scaled(
                            260,
                            180,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    preview.setToolTip(path.name)
                    layout.addWidget(preview)
                    actions = QHBoxLayout()
                    open_button = QPushButton("Abrir")
                    open_button.setObjectName("chatImageAction")
                    open_button.clicked.connect(lambda _checked=False, image_path=path: self._open_image(image_path))
                    actions.addWidget(open_button)
                    save_button = QPushButton("Salvar como...")
                    save_button.setObjectName("chatImageAction")
                    save_button.clicked.connect(lambda _checked=False, image_path=path: self._save_image_as(image_path))
                    actions.addWidget(save_button)
                    actions.addStretch()
                    layout.addLayout(actions)
                    continue
            if path.suffix.lower() in VIDEO_SUFFIXES:
                player = QMediaPlayer(self)
                audio = QAudioOutput(self)
                player.setAudioOutput(audio)
                preview = QVideoWidget(self)
                preview.setObjectName("chatVideoPreview")
                preview.setMinimumSize(260, 180)
                preview.setMaximumSize(520, 330)
                player.setVideoOutput(preview)
                player.setSource(QUrl.fromLocalFile(str(path.resolve())))
                self.video_players.append((player, audio))
                layout.addWidget(preview)
                actions = QHBoxLayout()
                play_button = QPushButton("Reproduzir")
                play_button.setObjectName("chatImageAction")
                play_button.clicked.connect(lambda _checked=False, media_player=player: self._toggle_video(media_player))
                actions.addWidget(play_button)
                open_button = QPushButton("Abrir")
                open_button.setObjectName("chatImageAction")
                open_button.clicked.connect(lambda _checked=False, video_path=path: self._open_media(video_path))
                actions.addWidget(open_button)
                save_button = QPushButton("Salvar como...")
                save_button.setObjectName("chatImageAction")
                save_button.clicked.connect(lambda _checked=False, video_path=path: self._save_media_as(video_path))
                actions.addWidget(save_button)
                actions.addStretch()
                layout.addLayout(actions)
                continue
            attachment_name = QLabel(f"Anexo: {path.name}")
            attachment_name.setObjectName("chatAttachmentName")
            attachment_name.setWordWrap(True)
            layout.addWidget(attachment_name)
        sender = QLabel(author)
        sender.setObjectName("chatMeta")
        layout.addWidget(sender)

    def _open_image(self, path: Path):
        self._open_media(path)

    def _open_media(self, path: Path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            QMessageBox.warning(self, "Merotec IA", "Não foi possível abrir a mídia.")

    def _save_image_as(self, path: Path):
        self._save_media_as(path, "Salvar imagem gerada", "Imagens PNG (*.png);;Todos os arquivos (*.*)")

    def _save_media_as(self, path: Path, title="Salvar vídeo gerado", filters="Vídeos (*.mp4 *.webm *.mov *.mkv *.avi);;Todos os arquivos (*.*)"):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(Path.home() / path.name),
            filters,
        )
        if not filename:
            return
        try:
            shutil.copy2(path, filename)
        except OSError as exc:
            QMessageBox.warning(self, "Merotec IA", f"Não foi possível salvar a mídia.\n{exc}")

    @staticmethod
    def _toggle_video(player):
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()


class MerotecIDE(AppStateMixin, WorkspaceIntelligenceMixin, QMainWindow):
    chat_reply = Signal(str)
    chat_stream = Signal(str)
    image_generation_finished = Signal(str, str)
    video_generation_finished = Signal(str, str)
    browser_action_requested = Signal(str, object, object)

    def __init__(self):
        super().__init__()
        self.ui_bridge = QtUiBridge(self)
        self.settings_file = APP_SETTINGS_FILE
        self.history_file = APP_HISTORY_FILE
        self.change_history_file = APP_CHANGE_HISTORY_FILE
        self.settings = self._load_settings()
        self.change_history = self._load_change_history()
        self._apply_settings_to_environment()
        self.current_workspace = str(self._initial_workspace())
        self.workspace = Path(self.current_workspace)
        # ``projects`` e a pasta que abriga projetos, nao um projeto aberto.
        # O estado separado evita que o explorer pareca reabrir um projeto ao fecha-lo.
        self.project_open = self.workspace.resolve() != PROJECTS_DIR.resolve()
        self.terminal_working_directory = self.workspace
        self.memory_subnet = MemorySubnet(self.current_workspace)
        self.engine = UniversalEngine()
        self.pm = ProjectManager(str(PROJECTS_DIR))
        self.executor = CodeExecutor()
        self.voice = VoiceModule(self.settings)
        self.voice_capture_active = False
        self.terminal_process = None
        self.terminal_session = None
        self._terminal_command = ""
        self._terminal_cancel_requested = False
        self._terminal_replace_current_line = False
        self._terminal_progress_sources = {}
        self._terminal_progress_frame = 0
        self._terminal_progress_percent = None
        self._terminal_session_command_number = 0
        self._terminal_session_marker = ""
        self._terminal_session_output_buffer = ""
        self._terminal_session_queue = []
        self._terminal_session_interactive = False
        self.pending_attachments = []
        self.video_cancel_event = threading.Event()
        self._next_video_generation_options = {}
        self.chat_busy = False
        self.codex_login_started = False
        self.chat_started_at = None
        self.chat_last_activity = ""
        # O app-server do Codex usa uma thread efemera por rodada. Sem uma
        # janela de contexto local, a proxima mensagem chegava como se fosse
        # uma conversa nova, mesmo com a IDE ainda aberta.
        self.active_ai_objective = ""
        self.ai_context_memory = []
        self.last_response = ""
        self.restore_workspace_ai_context_memory()
        self.speech_active = False
        self.streaming_bubble = None
        self.streaming_text = ""
        self.activity_bubble = None
        self.activity_lines = []
        self._chat_task_prompt = ""
        self._chat_agent_round = 0
        self._chat_waiting_for_browser = False
        self._agent_browser_action_pending = None
        self.browser_view = None
        self.browser_profile = None
        self.internal_browser_url = "about:blank"
        self.internal_browser_ready_event = threading.Event()
        self._last_internal_browser_load_ok = None
        self.paths_by_tab = {}
        self.attach_internal_web_chat_bridge()
        self._apply_brand_icon()
        self.setWindowTitle("Merotec IA IDE — Engenharia Autônoma")
        self._enable_dark_title_bar()
        self.resize(1400, 700)
        self.setMinimumSize(1120, 600)
        self._build_ui()
        self._connect_signals()
        self.terminal_progress_timer = QTimer(self)
        self.terminal_progress_timer.setInterval(180)
        self.terminal_progress_timer.timeout.connect(self._render_terminal_progress)
        self.quota_refresh_timer = QTimer(self)
        self.quota_refresh_timer.setInterval(500)
        self.quota_refresh_timer.timeout.connect(self._refresh_chat_runtime_status)
        self._open_initial_file()
        self.load_plugins()
        self.report_plugin_status()

    def _apply_brand_icon(self):
        """Aplica a marca da IDE na barra de título, seletor Alt+Tab e taskbar."""
        icon = QIcon(str(APP_ICON_PATH))
        if icon.isNull():
            return
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def _enable_dark_title_bar(self):
        """Alinha a moldura nativa do Windows ao azul escuro da IDE."""
        if sys.platform != "win32":
            return
        try:
            enabled = ctypes.c_int(1)
            window_handle = int(self.winId())
            dwmapi = ctypes.windll.dwmapi
            # 20 e o atributo atual; 19 atende versoes mais antigas do Windows 10.
            for attribute in (20, 19):
                result = dwmapi.DwmSetWindowAttribute(
                    window_handle,
                    attribute,
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
                if result == 0:
                    break

            # Windows 11 aceita a cor da legenda diretamente pelo DWM.
            # COLORREF usa BGR: #0a1421 vira 0x21140A.
            caption_color = ctypes.c_uint(0x21140A)
            text_color = ctypes.c_uint(0xEBDED5)  # #d5deeb em BGR
            for attribute, color in ((35, caption_color), (36, text_color)):
                dwmapi.DwmSetWindowAttribute(
                    window_handle,
                    attribute,
                    ctypes.byref(color),
                    ctypes.sizeof(color),
                )
        except (AttributeError, OSError):
            # Em ambientes sem DWM, a janela segue com o comportamento nativo.
            pass

    def set_status(self, text, mode="info"):
        if hasattr(self, "status"):
            self.status.setText(f"●  {text}")
            self.refresh_quota_status()

    def attach_internal_web_chat_bridge(self):
        """Conecta o Chat Web ao QWebEngine visível da janela principal.

        A ponte abre a aba do navegador sob demanda e executa as ações nela.
        Assim, uma tarefa do agente não depende de um segundo processo WebView2
        nem conclui incorretamente que o navegador da sessão está indisponível.
        """
        engine = getattr(self, "engine", None)
        if engine is None:
            return None
        profile = dict(getattr(engine, "web_chat_profile", {}) or {})
        profile.update(
            {
                "web_chat_url": getattr(
                    engine,
                    "web_chat_url",
                    profile.get("web_chat_url", "https://chatgpt.com/"),
                ),
                "web_chat_timeout_seconds": getattr(
                    engine,
                    "web_chat_timeout_seconds",
                    profile.get("web_chat_timeout_seconds", 300),
                ),
                "web_chat_message_chars": getattr(
                    engine,
                    "web_chat_message_chars",
                    profile.get("web_chat_message_chars", 28000),
                ),
                "web_chat_auto_attach_media": getattr(
                    engine,
                    "web_chat_auto_attach_media",
                    profile.get("web_chat_auto_attach_media", True),
                ),
            }
        )
        engine.web_chat_bridge = InternalBrowserWebChatBridge(self, profile)
        return engine.web_chat_bridge

    def refresh_quota_status(self):
        """Mantem a cota visivel sem alongar a mensagem principal de status."""
        if not hasattr(self, "quota_status"):
            return
        try:
            quota = str(self.engine.quota_status_text() or "").strip()
        except Exception:
            quota = ""
        self.quota_status.setText(f"Cota: {quota}" if quota else "Cota: indisponivel")
        self.quota_status.setToolTip(quota or "A cota sera exibida quando o provedor informar o status.")

    def _refresh_chat_runtime_status(self):
        """Atualiza cota e estado da rodada sem deixar a barra presa em 'pensando'."""
        self.refresh_quota_status()
        if not self.chat_busy or not self.chat_started_at or not hasattr(self, "status"):
            return
        elapsed = max(0, int(time.monotonic() - self.chat_started_at))
        detail = self.chat_last_activity or "Processando a tarefa"
        if len(detail) > 72:
            detail = detail[:69].rstrip() + "..."
        self.status.setText(f"🤖  {detail} ({elapsed}s)")

    def log_agent(self, text):
        # Durante a migracao, o terminal e o registro visivel da atividade.
        if hasattr(self, "terminal"):
            self.append_terminal(f"[Agente] {text}\n")

    def add_chat_message(self, sender, text):
        self.ui_bridge.call_soon(lambda: self.add_chat(sender, text, sender.lower() in {"voce", "você"}))

    def add_chat_image_message(self, sender, image_path, text=""):
        """Publica uma imagem produzida pelo agente como anexo visível no chat.

        O fluxo de ações do agente chama este contrato para screenshots e
        imagens geradas. A versão Qt tinha somente ``add_chat_message``, por
        isso o callback terminava sem entregar o arquivo ao ``ChatBubble``.
        """
        path = Path(image_path)
        if not self._is_image_attachment(path):
            self.add_chat_attachment_message(sender, path, text)
            return
        note = f"{text}\n[imagem anexada: {path.name}]" if text else f"[imagem anexada: {path.name}]"
        self._remember_ai_context_message(sender, note)
        outgoing = str(sender or "").lower() in {"voce", "você"}
        self.ui_bridge.call_soon(
            lambda: self.add_chat(sender, text, outgoing, attachments=[path])
        )

    def add_chat_attachment_message(self, sender, file_path, text=""):
        """Mantém anexos não visuais rastreáveis no mesmo fluxo do chat."""
        path = Path(file_path)
        note = f"{text}\n[arquivo anexado: {path.name}]" if text else f"[arquivo anexado: {path.name}]"
        self._remember_ai_context_message(sender, note)
        outgoing = str(sender or "").lower() in {"voce", "você"}
        self.ui_bridge.call_soon(
            lambda: self.add_chat(sender, text or f"Anexo: {path.name}", outgoing, attachments=[path])
        )

    def plugin_services(self):
        return {"app": self, "settings": self.settings, "workspace": self.current_workspace, "engine": self.engine, "voice": self.voice, "project_manager": self.pm, "executor": self.executor}

    def load_plugins(self):
        self.plugin_manager, self.plugin_statuses, self.plugin_capabilities = initialize_plugins(services=self.plugin_services())

    def report_plugin_status(self):
        for sender, message in build_plugin_report_messages(getattr(self, "plugin_statuses", [])):
            self.add_chat(sender, message)

    def update_recent_menu(self):
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        menu.clear()
        projects = [Path(item) for item in self.settings.get("recent_projects", []) if Path(item).is_dir()]
        if not projects:
            action = menu.addAction("Nenhum projeto recente")
            action.setEnabled(False)
            return
        for project in projects[:10]:
            menu.addAction(str(project), lambda checked=False, path=project: self.open_workspace(path))

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter = content
        content.setChildrenCollapsible(False)
        content.addWidget(self._build_activity_bar())
        content.addWidget(self._build_explorer())
        content.addWidget(self._build_workspace())
        content.addWidget(self._build_chat())
        content.setStretchFactor(2, 1)
        content.setSizes([64, 330, 780, 420])
        layout.addWidget(content, 1)
        layout.addWidget(self._build_statusbar())

    def _build_menu(self):
        menu = self.menuBar()
        menu.setObjectName("menuBar")
        for name in ("Arquivo", "Editar", "Selecionar", "Exibir", "Executar", "Terminal", "Ajuda"):
            current = menu.addMenu(name)
            if name == "Arquivo":
                current.addAction("Abrir pasta...", self.choose_workspace)
                current.addAction("Abrir arquivo...", self.open_external_file, QKeySequence.StandardKey.Open)
                current.addAction("Novo projeto...", self.create_project, "Ctrl+Shift+N")
                current.addAction("Fechar projeto", self.close_project, "Ctrl+Shift+W")
                self.recent_menu = current.addMenu("Projetos recentes")
                self.update_recent_menu()
                current.addAction("Novo arquivo", self.new_file)
                current.addSeparator()
                current.addAction("Salvar", self.save_current, QKeySequence.StandardKey.Save)
            if name == "Executar":
                current.addAction("Executar arquivo atual", self.run_current, "F5")
            if name == "Editar":
                self._add_menu_action(current, "Localizar", "Ctrl+F", self.find_in_current_editor)
                self._add_menu_action(current, "Localizar proximo", "F3", self.find_next)
                self._add_menu_action(current, "Comentar selecao", "Ctrl+/", self.toggle_current_comment)
                self._add_menu_action(current, "Paleta de simbolos", "Ctrl+Shift+O", self.show_symbol_palette)
            if name == "Exibir":
                self._add_menu_action(current, "Mostrar/ocultar explorador", "Ctrl+B", self.toggle_explorer)
                self._add_menu_action(current, "Aumentar fonte", "Ctrl++", lambda: self.zoom_editor(1))
                self._add_menu_action(current, "Diminuir fonte", "Ctrl+-", lambda: self.zoom_editor(-1))
                self._add_menu_action(current, "Restaurar fonte", "Ctrl+0", lambda: self.zoom_editor(0))
                current.addAction("Navegador interno", lambda: self.open_internal_browser("https://chatgpt.com/", "usuario"))
            if name == "Terminal":
                current.addAction("Novo comando", self.focus_terminal_input, "Ctrl+`")
                current.addAction("Interromper processo", self.cancel_terminal_process, "Ctrl+C")
                current.addAction("Limpar", self.terminal_clear)

    def _add_menu_action(self, menu, text, shortcut, callback):
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        menu.addAction(action)

    def _tool_button(self, icon: QStyle.StandardPixmap, title: str, callback):
        action = QAction(self.style().standardIcon(icon), title, self)
        action.triggered.connect(callback)
        return action

    def _build_toolbar(self):
        toolbar = QToolBar("Ferramentas")
        toolbar.setObjectName("toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_FileIcon, "Novo arquivo", self.new_file))
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_DialogOpenButton, "Abrir pasta", self.choose_workspace))
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_DialogSaveButton, "Salvar", self.save_current))
        toolbar.addSeparator()
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_MediaPlay, "Executar arquivo atual", self.run_current))
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_MediaStop, "Interromper processo", self.cancel_terminal_process))
        toolbar.addSeparator()
        toolbar.addAction(self._tool_button(QStyle.StandardPixmap.SP_BrowserReload, "Atualizar ficheiros", self.refresh_tree))
        self.addToolBar(toolbar)

    def _build_activity_bar(self):
        bar = QFrame()
        bar.setObjectName("activityBar")
        bar.setFixedWidth(66)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(12)
        for icon, title, handler in [
            (QStyle.StandardPixmap.SP_DirIcon, "Explorador", self.focus_explorer),
            (QStyle.StandardPixmap.SP_FileDialogContentsView, "Pesquisar", self.focus_search),
            (QStyle.StandardPixmap.SP_CommandLink, "Executar", self.run_current),
            (QStyle.StandardPixmap.SP_ComputerIcon, "Projetos", self.choose_workspace),
        ]:
            button = QPushButton()
            button.setObjectName("activityButton")
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(title)
            button.clicked.connect(handler)
            layout.addWidget(button)
        layout.addStretch()
        settings = QPushButton()
        settings.setObjectName("activityButton")
        settings.setText("⚙")
        settings.setFont(QFont("Segoe UI Symbol", 20))
        settings.setAccessibleName("Configurações da IA")
        settings.setToolTip("Configurações da IA")
        settings.clicked.connect(self.show_settings_hint)
        layout.addWidget(settings)
        return bar

    def _build_explorer(self):
        panel = QFrame()
        panel.setObjectName("explorer")
        panel.setMinimumWidth(230)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 13, 10, 10)
        layout.setSpacing(9)
        header = QHBoxLayout()
        title = QLabel("PROJETOS")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch()
        add = QPushButton("+")
        add.setObjectName("tinyButton")
        add.setToolTip("Novo arquivo")
        add.clicked.connect(self.new_file)
        header.addWidget(add)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Filtrar arquivos")
        self.search.textChanged.connect(self.filter_tree)
        layout.addWidget(self.search)
        self.workspace_root_label = QLabel()
        self.workspace_root_label.setObjectName("explorerRoot")
        self.workspace_root_label.setToolTip(str(self.workspace))
        layout.addWidget(self.workspace_root_label)
        self._update_workspace_root_label()
        self.model = QFileSystemModel(self)
        self.model.setRootPath(str(self.workspace))
        self.file_filter = RecursiveFileFilter(self)
        self.file_filter.setSourceModel(self.model)
        self.file_filter.setRecursiveFilteringEnabled(True)
        self.tree = QTreeView()
        self.tree.setObjectName("fileTree")
        self.tree.setModel(self.file_filter)
        self.tree.setRootIndex(self.file_filter.mapFromSource(self.model.index(str(self.workspace))))
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.doubleClicked.connect(self.open_index)
        for column in range(1, 4):
            self.tree.hideColumn(column)
        self.tree.setVisible(self.project_open)
        layout.addWidget(self.tree, 1)
        return panel

    def _update_workspace_root_label(self):
        if not hasattr(self, "workspace_root_label"):
            return
        if not getattr(self, "project_open", True):
            self.workspace_root_label.setText("Nenhum projeto aberto")
            self.workspace_root_label.setToolTip("Use Arquivo > Abrir projeto para selecionar uma pasta.")
            return
        self.workspace_root_label.setText(f"📁  {self.workspace.name or str(self.workspace)}")
        self.workspace_root_label.setToolTip(str(self.workspace))

    def _build_workspace(self):
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("editorTabs")
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.workspace_splitter.addWidget(self.tabs)
        terminal_panel = QFrame()
        terminal_panel.setObjectName("terminalPanel")
        terminal_layout = QVBoxLayout(terminal_panel)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_header = QHBoxLayout()
        terminal_header.setContentsMargins(14, 8, 10, 7)
        title = QLabel("TERMINAL")
        title.setObjectName("terminalTitle")
        terminal_header.addWidget(title)
        self.terminal_progress_label = QLabel()
        self.terminal_progress_label.setObjectName("terminalProgress")
        self.terminal_progress_label.hide()
        terminal_header.addWidget(self.terminal_progress_label)
        terminal_header.addStretch()
        clear = QPushButton("Limpar")
        clear.setObjectName("terminalAction")
        clear.clicked.connect(self.terminal_clear)
        terminal_header.addWidget(clear)
        stop = QPushButton("Interromper")
        stop.setObjectName("terminalAction")
        stop.clicked.connect(self.cancel_terminal_process)
        terminal_header.addWidget(stop)
        terminal_layout.addLayout(terminal_header)
        self.terminal_progress_bar = QProgressBar()
        self.terminal_progress_bar.setObjectName("terminalProgressBar")
        self.terminal_progress_bar.setTextVisible(False)
        self.terminal_progress_bar.setFixedHeight(5)
        self.terminal_progress_bar.hide()
        terminal_layout.addWidget(self.terminal_progress_bar)
        self.terminal = QPlainTextEdit()
        self.terminal.setObjectName("terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Cascadia Mono", 10))
        self.terminal.setPlainText("")
        terminal_layout.addWidget(self.terminal, 1)
        self.terminal_input = TerminalInput()
        self.terminal_input.setObjectName("terminalInput")
        self.terminal_input.setPlaceholderText("Digite um comando e pressione Enter")
        self.terminal_input.returnPressed.connect(self.run_terminal_command)
        terminal_layout.addWidget(self.terminal_input)
        self._terminal_prompt()
        self.workspace_splitter.addWidget(terminal_panel)
        self.workspace_splitter.setSizes([560, 230])
        return self.workspace_splitter

    def _build_chat(self):
        panel = QFrame()
        panel.setObjectName("chatPanel")
        panel.setMinimumWidth(310)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 13, 14, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Merotec IA")
        title.setObjectName("chatTitle")
        header.addWidget(title)
        header.addStretch()
        self.provider_label = QLabel(f"{self.engine.provider}")
        self.provider_label.setObjectName("provider")
        header.addWidget(self.provider_label)
        layout.addLayout(header)
        self.chat_body = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_body)
        self.chat_layout.setContentsMargins(0, 6, 0, 6)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch()
        from PySide6.QtWidgets import QScrollArea
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setWidget(self.chat_body)
        layout.addWidget(self.chat_scroll, 1)
        self.add_chat("Merotec IA", "Ola! Sou o Merotec IA. Como posso ajudar voce hoje?")
        self.attachment_panel = QFrame()
        self.attachment_panel.setObjectName("attachmentPanel")
        attachment_layout = QVBoxLayout(self.attachment_panel)
        attachment_layout.setContentsMargins(8, 7, 8, 8)
        attachment_layout.setSpacing(6)
        attachment_header = QHBoxLayout()
        self.attachment_label = QLabel("Anexos na fila (0)")
        self.attachment_label.setObjectName("attachmentLabel")
        attachment_header.addWidget(self.attachment_label)
        attachment_header.addStretch()
        self.clear_attachments_button = QPushButton("Limpar")
        self.clear_attachments_button.setObjectName("attachmentClearButton")
        self.clear_attachments_button.clicked.connect(self.clear_attachments)
        attachment_header.addWidget(self.clear_attachments_button)
        attachment_layout.addLayout(attachment_header)
        self.attachment_items = QWidget()
        self.attachment_items_layout = QVBoxLayout(self.attachment_items)
        self.attachment_items_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_items_layout.setSpacing(6)
        attachment_layout.addWidget(self.attachment_items)
        self.attachment_panel.hide()
        layout.addWidget(self.attachment_panel)
        composer = QHBoxLayout()
        self.attach_button = QPushButton()
        self.attach_button.setObjectName("attachButton")
        self.attach_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogStart))
        self.attach_button.setToolTip("Adicionar arquivo ou gerar imagem")
        self.attach_button.clicked.connect(self.show_attachment_menu)
        composer.addWidget(self.attach_button)
        self.voice_button = QPushButton()
        self.voice_button.setObjectName("attachButton")
        self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.voice_button.setToolTip("Gravar comando de voz")
        self.voice_button.clicked.connect(self.toggle_voice_capture)
        composer.addWidget(self.voice_button)
        self.speak_button = QPushButton()
        self.speak_button.setObjectName("attachButton")
        self.speak_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.speak_button.setToolTip("Ler ultima resposta")
        self.speak_button.clicked.connect(self.play_last_response)
        composer.addWidget(self.speak_button)
        self.chat_input = ChatInput(self.add_clipboard_image, self.send_chat)
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setPlaceholderText("Digite sua mensagem...")
        self.chat_input.setFixedHeight(64)
        composer.addWidget(self.chat_input, 1)
        self.send_button = QPushButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.send_button.setToolTip("Enviar mensagem")
        self.send_button.clicked.connect(self.send_chat)
        composer.addWidget(self.send_button)
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("attachButton")
        self.cancel_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self.cancel_button.setToolTip("Cancelar tarefa da IA")
        self.cancel_button.clicked.connect(self.cancel_ai_task)
        self.cancel_button.hide()
        composer.addWidget(self.cancel_button)
        layout.addLayout(composer)
        return panel

    def _build_statusbar(self):
        bar = QFrame()
        bar.setObjectName("statusbar")
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(17, 0, 17, 0)
        self.status = QLabel("●  Pronto")
        self.status.setObjectName("readyStatus")
        layout.addWidget(self.status)
        layout.addStretch()
        self.quota_status = QLabel()
        self.quota_status.setObjectName("quotaStatus")
        self.quota_status.setMaximumWidth(420)
        layout.addWidget(self.quota_status)
        agent = QLabel("🤖  Agente IA: ativo")
        agent.setObjectName("agentStatus")
        layout.addWidget(agent)
        self.language = QLabel("Python")
        self.language.setObjectName("statusText")
        layout.addWidget(self.language)
        self.cursor = QLabel("Ln 1, Col 1")
        self.cursor.setObjectName("statusText")
        layout.addWidget(self.cursor)
        encoding = QLabel("UTF-8    CRLF    4 espacos")
        encoding.setObjectName("statusText")
        layout.addWidget(encoding)
        self.refresh_quota_status()
        return bar

    def _connect_signals(self):
        self.chat_reply.connect(self.finish_chat_reply)
        self.chat_stream.connect(self.append_chat_stream)
        self.image_generation_finished.connect(self.finish_image_generation)
        self.video_generation_finished.connect(self.finish_video_generation)
        # Ação emitida pelo worker do agente: sempre volte ao event loop Qt
        # antes de consultar ou controlar o QWebEngineView.
        self.browser_action_requested.connect(
            self._run_browser_action,
            Qt.ConnectionType.QueuedConnection,
        )

    # Contrato de agendamento usado pela migracao dos mixins para Qt.
    def after(self, milliseconds, callback):
        return self.ui_bridge.after(milliseconds, callback)

    def after_cancel(self, token):
        self.ui_bridge.after_cancel(token)

    def start_human_test(self, request="auto"):
        """Executa e captura um teste visual sem reutilizar o terminal da IDE."""
        if getattr(self, "_visual_test_active", False):
            self.add_chat("Sistema", "Ja existe um teste visual em andamento.")
            return False
        self._close_visual_test_preview()
        plan = self._build_visual_test_plan(request)
        if not plan:
            self.add_chat("Erro", "Nao encontrei um alvo visual seguro para testar neste projeto.")
            return False
        self._visual_test_active = True
        self._append_chat_activity(f"Teste visual iniciado: {plan['display']}")
        self.append_terminal(f"\n[teste visual] {plan['display']}\n")
        threading.Thread(target=self._run_human_test, args=(plan,), daemon=True).start()
        return True

    def _close_visual_test_preview(self):
        """Fecha a prévia anterior e seu servidor antes de iniciar outro teste."""
        window = getattr(self, "_visual_test_browser_window", None)
        if window is not None:
            self.ui_bridge.call_soon(window.close)
        process = getattr(self, "_visual_test_server_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self._visual_test_server_process = None

    def _find_visual_html_target(self, workspace, request=""):
        """Encontra a página HTML mais adequada, inclusive em subprojetos.

        O workspace da IDE pode ser um repositório Python que contém vários
        exemplos web. O teste visual deve abrir a página do subprojeto, nunca
        uma página incidental de cache, perfil do navegador ou dependência.
        """
        workspace = Path(workspace).resolve()
        objective = " ".join((str(request or ""), str(getattr(self, "_chat_task_prompt", "") or ""))).lower()
        requested_files = re.findall(r"[^\s\"']+\.html?\b", objective)
        candidates = []

        for root, directories, filenames in os.walk(workspace):
            directories[:] = [
                name for name in directories
                if not is_ignored_dir_name(name)
                and name not in {".visual-profile", ".merotec_attachments", ".merotec_backups"}
            ]
            base = Path(root)
            for filename in filenames:
                if Path(filename).suffix.lower() not in {".html", ".htm"}:
                    continue
                path = base / filename
                try:
                    relative = path.relative_to(workspace).as_posix().lower()
                except ValueError:
                    continue
                score = 0
                if path.name.lower() == "index.html":
                    score += 100
                if path.parent == workspace:
                    score += 30
                if any(relative.endswith(name.replace("\\", "/")) for name in requested_files):
                    score += 300
                for term in re.findall(r"[a-z0-9_-]{3,}", objective):
                    if term in relative:
                        score += 12
                score -= len(path.relative_to(workspace).parts)
                candidates.append((score, relative, path))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][2]

    @staticmethod
    def _allocate_visual_test_port():
        """Reserva uma porta livre apenas para montar o comando de teste."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _requested_local_visual_url(request, objective=""):
        """Retorna uma URL loopback já informada para um servidor em execução."""
        text = " ".join((str(request or ""), str(objective or "")))
        match = re.search(
            r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?(?:/[^\s\]>)},;]*)?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        url = match.group(0).rstrip(".,;:!?")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
            return ""
        return url

    @staticmethod
    def _find_visual_server_target(workspace):
        """Identifica o entrypoint de um servidor web local do projeto.

        A preferência por um servidor real evita renderizar templates Flask ou
        Django como HTML estático, o que ocultava rotas, dados e erros visuais.
        """
        workspace = Path(workspace).resolve()
        candidates = []
        for root, directories, filenames in os.walk(workspace):
            directories[:] = [
                name for name in directories
                if not is_ignored_dir_name(name)
                and name not in {".visual-profile", ".merotec_attachments", ".merotec_backups"}
            ]
            base = Path(root)
            for filename in filenames:
                path = base / filename
                if path.name == "manage.py":
                    candidates.append((320, path, "django", ""))
                    continue
                if path.suffix.lower() != ".py" or path.name.startswith("test_"):
                    continue
                try:
                    source = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                score = 80 if path.parent == workspace else 0
                if path.name in {"app.py", "main.py", "server.py"}:
                    score += 40
                flask = re.search(
                    r"^\s*([A-Za-z_]\w*)\s*=\s*(?:flask\.)?Flask\s*\(",
                    source,
                    re.MULTILINE,
                )
                if flask and re.search(r"(?:^|\n)\s*(?:from\s+flask\s+import|import\s+flask\b)", source):
                    candidates.append((score + 180, path, "flask", flask.group(1)))
                    continue
                fastapi = re.search(
                    r"^\s*([A-Za-z_]\w*)\s*=\s*FastAPI\s*\(",
                    source,
                    re.MULTILINE,
                )
                if fastapi and re.search(r"(?:^|\n)\s*(?:from\s+fastapi\s+import|import\s+fastapi\b)", source):
                    candidates.append((score + 180, path, "fastapi", fastapi.group(1)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1].as_posix().lower()))
        _score, path, framework, application = candidates[0]
        return {"path": path, "framework": framework, "application": application}

    def _build_visual_server_plan(self, workspace, request):
        """Cria um plano de navegador para servidor existente ou detectado."""
        existing_url = self._requested_local_visual_url(
            request,
            getattr(self, "_chat_task_prompt", ""),
        )
        if existing_url:
            return {
                "kind": "browser",
                "command": None,
                "display": f"navegador interno em servidor local já ativo ({existing_url})",
                "cwd": workspace,
                "url": existing_url,
                "keep_open": True,
            }

        target = self._find_visual_server_target(workspace)
        if target is None:
            return None
        port = self._allocate_visual_test_port()
        path = target["path"]
        framework = target["framework"]
        if framework == "django":
            command = [sys.executable, "-u", path.name, "runserver", f"127.0.0.1:{port}", "--noreload"]
            url = f"http://127.0.0.1:{port}/"
        elif framework == "flask":
            module = path.relative_to(workspace).with_suffix("").as_posix().replace("/", ".")
            command = [
                sys.executable, "-u", "-m", "flask", "--app", f"{module}:{target['application']}",
                "run", "--host", "127.0.0.1", "--port", str(port),
            ]
            url = f"http://127.0.0.1:{port}/"
        else:
            module = path.relative_to(workspace).with_suffix("").as_posix().replace("/", ".")
            command = [
                sys.executable, "-u", "-m", "uvicorn", f"{module}:{target['application']}",
                "--host", "127.0.0.1", "--port", str(port),
            ]
            url = f"http://127.0.0.1:{port}/docs"
        return {
            "kind": "browser",
            "command": command,
            "display": f"{framework} em 127.0.0.1:{port} ({path.relative_to(workspace).as_posix()})",
            "cwd": workspace,
            "url": url,
            "keep_open": True,
        }

    def _build_visual_test_plan(self, request):
        workspace = self.workspace.resolve()
        server_plan = self._build_visual_server_plan(workspace, request)
        if server_plan is not None:
            return server_plan
        html_target = self._find_visual_html_target(workspace, request)
        if html_target and html_target.is_file():
            port = self._allocate_visual_test_port()
            html_workspace = html_target.parent
            return {
                "kind": "browser",
                "command": [sys.executable, "-u", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                "display": f"{Path(sys.executable).name} -m http.server {port} --bind 127.0.0.1 ({html_target.relative_to(workspace).as_posix()})",
                "cwd": html_workspace,
                "url": f"http://127.0.0.1:{port}/{html_target.name}",
                "keep_open": True,
            }
        target = workspace / "app.py"
        if not target.exists():
            target = workspace / "main.py"
        if target.exists():
            return {
                "kind": "window",
                "command": [sys.executable, "-u", target.name],
                "display": f"{Path(sys.executable).name} -u {target.name}",
                "cwd": workspace,
                "url": "",
            }
        return None

    def _run_human_test(self, plan):
        process = None
        try:
            if plan.get("command"):
                kwargs = {
                    "cwd": str(plan["cwd"]),
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                }
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                process = subprocess.Popen(plan["command"], **kwargs)
            keep_process = False
            if plan["kind"] == "browser":
                if not self._wait_for_visual_url(plan["url"]):
                    output = self._drain_visual_process_output(process)
                    raise RuntimeError(output or "O servidor local nao respondeu.")
                image_path = self._capture_qt_visual_browser(plan["url"], keep_open=plan.get("keep_open", False))
                if plan.get("keep_open"):
                    self._visual_test_server_process = process
                    keep_process = True
            else:
                image_path = self._capture_visual_window(process.pid)
                if image_path is None:
                    output = self._drain_visual_process_output(process)
                    raise RuntimeError(output or "A janela do aplicativo nao apareceu.")
            self.ui_bridge.call_soon(lambda path=image_path: self._finish_human_test(path, ""))
        except Exception as exc:
            self.ui_bridge.call_soon(lambda detail=str(exc): self._finish_human_test(None, detail))
        finally:
            if process and process.poll() is None and not locals().get("keep_process", False):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    @staticmethod
    def _drain_visual_process_output(process):
        if not process or process.poll() is None:
            return ""
        try:
            return (process.stdout.read() or "").strip()[-6000:]
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _wait_for_visual_url(url, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0) as response:
                    if 200 <= getattr(response, "status", 200) < 500:
                        return True
            except OSError:
                time.sleep(0.2)
        return False

    def _capture_qt_visual_browser(self, url, timeout=25.0, keep_open=False):
        """Abre uma página local em uma janela Qt e retorna sua captura real.

        O executor anterior dependia de um processo ``pywebview`` separado. Em
        algumas instalações do Windows esse processo criava filhos WebView2 sem
        publicar a janela para captura, fazendo o teste terminar sem print. A
        aplicação já usa QWebEngine; criar uma janela temporária nele deixa a
        tela visível e mantém a captura no mesmo loop gráfico da IDE.
        """
        completed = threading.Event()
        result = {"path": None, "error": ""}
        holder = {"window": None}

        def finish(path=None, error=""):
            if completed.is_set():
                return
            result["path"] = path
            result["error"] = error
            completed.set()

        def close_preview():
            window = holder.get("window")
            if window is not None:
                window.close()

        def open_preview():
            try:
                expected_url = QUrl.fromUserInput(str(url)).toString()
                window = QMainWindow()
                window.setWindowTitle(f"Merotec IA - Teste visual {time.time_ns()}")
                window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
                window.resize(1280, 820)
                # O perfil padrão pode apontar para AppLocalDataLocation, que
                # é bloqueado em instalações portáteis/sandbox. O navegador
                # interno já usa um perfil local dedicado; o teste visual usa
                # o mesmo padrão, isolado por execução.
                profile_root = ROOT / ".merotec_local_ai" / "webengine" / f"visual-test-{time.time_ns()}"
                profile_root.mkdir(parents=True, exist_ok=True)
                profile = QWebEngineProfile(f"merotec-visual-{time.time_ns()}", window)
                profile.setPersistentStoragePath(str(profile_root / "storage"))
                profile.setCachePath(str(profile_root / "cache"))
                view = QWebEngineView(window)
                view.setPage(QWebEnginePage(profile, view))
                # Mantém o perfil vivo durante a página e a captura.
                window._visual_test_profile = profile
                window.setCentralWidget(view)
                holder["window"] = window
                self._visual_test_browser_window = window
                capture_scheduled = False

                def capture():
                    try:
                        # ``QWebEngineView.grab()`` pode retornar um quadro
                        # branco porque o WebView é composto em uma superfície
                        # filha de GPU. A captura do handle nativo ocorre após
                        # a composição e preserva o que o usuário realmente
                        # vê na janela de teste.
                        geometry = window.frameGeometry()
                        bbox = (geometry.left(), geometry.top(), geometry.right() + 1, geometry.bottom() + 1)
                        if bbox[2] - bbox[0] < 160 or bbox[3] - bbox[1] < 120:
                            finish(error="A janela de teste não recebeu dimensões capturáveis.")
                            return
                        destination = self.workspace / ".merotec_attachments" / f"visual_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            ImageGrab.grab(bbox=bbox).save(destination, "PNG")
                        except Exception:
                            # Fallback para ambientes sem captura de desktop.
                            pixmap = view.grab()
                            if pixmap.isNull() or not pixmap.save(str(destination), "PNG"):
                                finish(error="A página foi aberta, mas o QWebEngine não retornou uma captura.")
                                return
                        finish(path=destination)
                    finally:
                        if not keep_open:
                            QTimer.singleShot(250, close_preview)

                def schedule_capture(delay):
                    nonlocal capture_scheduled
                    if capture_scheduled:
                        return
                    capture_scheduled = True
                    QTimer.singleShot(delay, capture)

                def page_loaded(success):
                    # A criação de QWebEngineView pode finalizar o documento
                    # inicial about:blank depois que o sinal já foi conectado.
                    # Esse evento não pertence à página do teste e não deve
                    # encerrar a captura antes da navegação solicitada.
                    if view.url().toString() != expected_url:
                        return
                    if not success:
                        # Em algumas versões do WebEngine, a troca inicial de
                        # about:blank entrega ``False`` mesmo com a nova URL
                        # já sendo pintada. Capture a tela resultante: se o
                        # servidor realmente falhar, o print mostrará a página
                        # de erro em vez de perder toda a evidência visual.
                        schedule_capture(1400)
                        return
                    # Espera o primeiro paint completo do conteúdo, não apenas
                    # o evento de navegação do QWebEngine.
                    schedule_capture(850)

                def preview_closed(*_args):
                    if getattr(self, "_visual_test_browser_window", None) is not window:
                        return
                    self._visual_test_browser_window = None
                    process = getattr(self, "_visual_test_server_process", None)
                    if process is not None and process.poll() is None:
                        process.terminate()
                    self._visual_test_server_process = None

                window.destroyed.connect(preview_closed)
                view.loadFinished.connect(page_loaded)
                window.show()
                window.raise_()
                window.activateWindow()
                view.setUrl(QUrl(expected_url))
            except Exception as exc:
                finish(error=f"Não consegui abrir a página do teste visual: {exc}")

        self.ui_bridge.call_soon(open_preview)
        if not completed.wait(timeout):
            self.ui_bridge.call_soon(close_preview)
            raise RuntimeError("A página do teste visual não ficou pronta para captura no tempo limite.")
        if result["path"] is None:
            raise RuntimeError(result["error"] or "O teste visual não gerou uma captura.")
        return result["path"]

    def _capture_visual_window(self, pid, timeout=14.0, title=""):
        if os.name != "nt":
            return None
        user32 = ctypes.windll.user32
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = []

            def collect(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                window_pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                length = user32.GetWindowTextLengthW(hwnd)
                window_title = ""
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    window_title = buffer.value
                if window_pid.value != pid and (not title or title not in window_title):
                    return True
                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                width, height = rect.right - rect.left, rect.bottom - rect.top
                if width >= 160 and height >= 120:
                    matches.append((width * height, hwnd, (rect.left, rect.top, rect.right, rect.bottom)))
                return True

            callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(collect)
            user32.EnumWindows(callback, 0)
            if matches:
                _area, hwnd, bbox = max(matches)
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                destination = self.workspace / ".merotec_attachments" / f"visual_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
                destination.parent.mkdir(parents=True, exist_ok=True)
                ImageGrab.grab(bbox=bbox).save(destination, "PNG")
                return destination
            time.sleep(0.25)
        return None

    def _finish_human_test(self, image_path, error):
        self._visual_test_active = False
        if image_path:
            self.add_chat("Teste visual", "Interface aberta e captura visual concluida.", attachments=[image_path])
            self.append_terminal(f"[teste visual] captura salva em {image_path}\n")
            # Capturar a janela e encerrar o processo de teste nao encerra a
            # validacao: o proximo ciclo precisa receber o print para verificar
            # erros visiveis, layout e o fluxo solicitado pelo usuario.
            if self.chat_busy and self._chat_agent_round < 12:
                self._chat_agent_round += 1
                self._chat_waiting_for_command = False
                self.streaming_bubble = None
                self.streaming_text = ""
                self._append_chat_activity(
                    f"Captura pronta; inspecionando visualmente a interface ({self._chat_agent_round}/12)."
                )
                self.set_status("IA analisando a captura visual...")
                observation = (
                    "HUMAN_TEST concluido com captura real.\n"
                    f"ARQUIVO DA CAPTURA: {image_path.name}\n\n"
                    "Inspecione a imagem anexada antes de concluir. Procure por tela em branco, "
                    "erros/tracebacks, dialogs inesperados, texto cortado, layout quebrado, "
                    "controles inacessiveis ou fluxo incoerente. Se encontrar um problema, "
                    "descreva a evidencia visivel e continue com a proxima acao da IDE; se "
                    "estiver adequada, entregue uma conclusao objetiva dizendo exatamente o que viu."
                )
                continuation = (
                    "[MEROTEC_AGENT_CONTINUATION]\n"
                    f"Tarefa original: {self._chat_task_prompt}\n\n"
                    "A IDE abriu a interface, capturou a tela e anexou a evidencia visual. "
                    "Analise o print agora; nao finalize apenas porque a captura existe.\n\n"
                    f"RESULTADO DA IDE:\n{observation}"
                )
                threading.Thread(
                    target=self._generate_reply,
                    args=(continuation, observation, image_path),
                    daemon=True,
                ).start()
                return
            self.set_status("Teste visual concluido")
        else:
            self.add_chat("Erro", f"Teste visual falhou: {error}")
            self.append_terminal(f"[teste visual] falhou: {error}\n")
            self.set_status("Teste visual com erro")
        self._set_chat_busy(False)

    def _open_initial_file(self):
        """Exibe orientacoes iniciais sem expor o codigo-fonte da propria IDE."""
        editor = CodeEditor()
        editor.setReadOnly(True)
        editor.highlighter.setDocument(None)
        editor.highlighter = GettingStartedHighlighter(editor.document())
        editor.setPlainText(
            "# Bem-vindo ao Merotec IA IDE\n\n"
            "# Como comecar\n"
            "# 1. Abra uma pasta de projeto em Arquivo > Abrir projeto.\n"
            "# 2. Selecione um arquivo no painel PROJETOS para edita-lo.\n"
            "# 3. Crie arquivos pelo botao + ou use Arquivo > Novo arquivo.\n"
            "# 4. Execute o arquivo Python aberto pelo botao ▶ ou menu Executar.\n\n"
            "# Teste visual de projetos\n"
            "# Flutter: no terminal, execute `flutter pub get` e depois `flutter run -d chrome`.\n"
            "#          Para abrir como aplicativo Windows, use `flutter run -d windows`.\n"
            "# Flet: execute `flet run main.py --web` para abrir no navegador.\n"
            "#       Sem `--web`, `flet run main.py` abre a janela do aplicativo.\n"
            "# Python: abra o arquivo principal e pressione F5; a saida aparece no terminal.\n"
            "# HTML/CSS/JS: execute `python -m http.server 8000 --bind 127.0.0.1`\n"
            "#               e abra http://127.0.0.1:8000 no navegador.\n"
            "# Flask API: execute `flask --app app run --debug` e abra http://127.0.0.1:5000.\n"
            "#            Troque `app` pelo arquivo/modulo que contem a aplicacao, se necessario.\n"
            "# FastAPI: execute `uvicorn main:app --reload` e abra http://127.0.0.1:8000/docs.\n"
            "#          Troque `main:app` pelo modulo e objeto da sua API, se necessario.\n\n"
            "# C++: abra um arquivo .cpp e pressione F5; a IDE usa g++ para compilar e executar.\n"
            "# C#: abra Program.cs e pressione F5; a IDE executa o projeto com `dotnet run`.\n\n"
            "# Terminal\n"
            "# Digite comandos na caixa abaixo do terminal e pressione Enter.\n"
            "# O comando e executado na pasta do projeto aberto. Para parar um servidor, use Ctrl+C.\n"
            "# Use Interromper para encerrar um processo em execucao.\n\n"
            "# Atalhos\n"
            "# Ctrl+S  Salvar arquivo\n"
            "# Ctrl+`  Focar o terminal\n"
            "# Ctrl+C  Interromper o processo do terminal\n\n"
            "# Esta aba e somente informativa. Abra ou crie um arquivo para comecar.\n"
        )
        editor.cursorPositionChanged.connect(self.update_cursor)
        tab = self.tabs.addTab(editor, "Comece aqui")
        self.tabs.setCurrentIndex(tab)
        self.language.setText("Uso da IDE")

    def open_index(self, index):
        path = Path(self.model.filePath(self.file_filter.mapToSource(index)))
        if path.is_file():
            self.open_file(path)

    def open_file(self, path: Path):
        path = path.resolve()
        for index, existing in self.paths_by_tab.items():
            if existing == path:
                self.tabs.setCurrentIndex(index)
                return
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            self.open_image(path)
            return
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "Merotec IA", f"Nao foi possivel abrir o arquivo.\n{exc}")
            return
        editor = CodeEditor()
        editor.setPlainText(text)
        editor.cursorPositionChanged.connect(self.update_cursor)
        editor.textChanged.connect(lambda e=editor: self.mark_dirty(e))
        tab = self.tabs.addTab(editor, path.name)
        self.paths_by_tab[tab] = path
        self.tabs.setCurrentIndex(tab)
        self.language.setText(path.suffix.lstrip(".").upper() or "Texto")

    def open_image(self, path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "Merotec IA", "Nao foi possivel carregar a imagem.")
            return
        panel = QWidget()
        panel.setObjectName("imageViewer")
        layout = QVBoxLayout(panel)
        tools = QHBoxLayout()
        scale = {"value": 1.0}
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        def render():
            width = max(1, int(pixmap.width() * scale["value"]))
            height = max(1, int(pixmap.height() * scale["value"]))
            label.setPixmap(pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        for title, delta in (("-", -0.2), ("100%", 0), ("+", 0.2)):
            button = QPushButton(title)
            button.setObjectName("terminalAction")
            def adjust(_checked=False, change=delta):
                scale["value"] = 1.0 if change == 0 else max(0.2, min(4.0, scale["value"] + change))
                render()
            button.clicked.connect(adjust)
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)
        render()
        tab = self.tabs.addTab(panel, path.name)
        self.paths_by_tab[tab] = path
        self.tabs.setCurrentIndex(tab)
        self.language.setText("Imagem")

    def _internal_browser_is_usable(self):
        """Retorna se a WebView de sessão ainda existe no lado nativo do Qt."""
        view = self.browser_view
        if view is None:
            return False
        try:
            return view.page() is not None
        except RuntimeError:
            # Um QObject pode permanecer referenciado em Python depois de o Qt
            # destruí-lo (por exemplo, depois do fechamento de uma aba). Não
            # reutilize esse wrapper: a próxima abertura cria uma sessão nova.
            return False

    def _internal_browser_destroyed(self, *_args):
        """Descarta referências Qt inválidas sem afetar a janela de teste visual."""
        self.browser_view = None
        self.browser_profile = None
        self.internal_browser_url = "about:blank"
        self._last_internal_browser_load_ok = None
        self.internal_browser_ready_event.set()

    def open_internal_browser(self, url, source="usuario"):
        if not self._internal_browser_is_usable():
            self.browser_view = None
            self.browser_profile = None
            # O perfil padrão do Qt pode ser efêmero ou variar conforme o modo
            # de execução. Um perfil nomeado, com caminhos próprios da IDE,
            # preserva autenticação, cookies e armazenamento do Chat Web entre
            # reinicializações sem misturar dados com o navegador visual.
            # O sandbox do app-server e algumas instalacoes portaveis bloqueiam
            # AppLocalDataLocation. Guardar o perfil junto da IDE mantem o
            # navegador visual funcional e evita que a validacao pare antes da
            # primeira pagina por erro de permissao.
            storage_root = ROOT / ".merotec_local_ai" / "webengine" / "chat-web"
            storage_root.mkdir(parents=True, exist_ok=True)
            self.browser_profile = QWebEngineProfile("merotec-chat-web", self)
            self.browser_profile.setPersistentStoragePath(str(storage_root / "storage"))
            self.browser_profile.setCachePath(str(storage_root / "cache"))
            self.browser_profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
            self.browser_view = QWebEngineView()
            self.browser_view.setPage(QWebEnginePage(self.browser_profile, self.browser_view))
            self.browser_view.setObjectName("internalBrowser")
            self.browser_view.urlChanged.connect(lambda value: setattr(self, "internal_browser_url", value.toString()))
            self.browser_view.loadFinished.connect(self._internal_browser_loaded)
            self.browser_view.destroyed.connect(self._internal_browser_destroyed)
            index = self.tabs.addTab(self.browser_view, "Navegador")
            self.tabs.setCurrentIndex(index)
        else:
            index = self.tabs.indexOf(self.browser_view)
            if index < 0:
                index = self.tabs.addTab(self.browser_view, "Navegador")
            self.tabs.setCurrentIndex(index)
        target = QUrl.fromUserInput(str(url))
        self.internal_browser_ready_event.clear()
        self._last_internal_browser_load_ok = None
        self.browser_view.load(target)
        self.internal_browser_url = target.toString()
        self.set_status(f"Navegador: {source}")
        return self.internal_browser_url

    def _internal_browser_loaded(self, ok):
        self._last_internal_browser_load_ok = bool(ok)
        if ok:
            self.internal_browser_ready_event.set()
            page = self.browser_view.page() if self.browser_view is not None else None
            if page is not None:
                page.runJavaScript(
                    "document.title || ''",
                    lambda title: self.remember_internal_browser_chat_url(
                        self.internal_browser_url, str(title or "")
                    ),
                )
            self.set_status("Navegador interno pronto")
        else:
            # Libera quem estiver aguardando a navegacao. Sem isso a thread da
            # conversa ficava presa por 35 segundos e parecia que a IDE nao
            # falava mais com o navegador.
            self.internal_browser_ready_event.set()
            self.set_status("O navegador interno nao conseguiu carregar a pagina", "error")

    def request_internal_browser_action(self, action, payload=None, callback=None):
        """Enfileira uma ação do navegador para a thread Qt.

        Esta função também é chamada pela thread que conversa com o provedor de
        IA. Consultar ``QWebEngineView.page()`` nessa thread não é seguro e
        fazia a ponte devolver "navegador interno não está disponível" mesmo
        depois de a página ter sido aberta. O slot ``_run_browser_action``
        recebe o sinal na thread da interface e é o único lugar que valida a
        sessão nativa antes de usar o WebEngine.
        """
        request_id = f"qt-browser-{time.time_ns()}"
        self.browser_action_requested.emit(str(action), dict(payload or {}), callback)
        return request_id

    def run_agent_browser_action(self, action, payload=None):
        """Executa uma etapa do navegador e preserva a tarefa ate o retorno real."""
        if self._agent_browser_action_pending is not None:
            self.add_chat("Erro", "A acao anterior do navegador ainda esta em andamento.")
            return False
        action = str(action or "").strip().lower()
        payload = dict(payload or {})
        self._agent_browser_action_pending = {"action": action, "payload": payload}

        def complete(result):
            self._finish_agent_browser_action(result)

        if action == "open":
            url = str(payload.get("url") or "").strip()
            parsed = QUrl.fromUserInput(url)
            if not url or not parsed.isValid() or parsed.scheme().lower() not in {"http", "https", "file"}:
                self._agent_browser_action_pending = None
                self.add_chat("Erro", "OPEN_URL precisa informar uma URL HTTP(S) ou arquivo local valida.")
                return False
            self.open_internal_browser(url, "IA")

            def wait_for_page(remaining=60):
                pending = self._agent_browser_action_pending
                if pending is None or pending.get("action") != "open":
                    return
                if self.internal_browser_ready_event.is_set():
                    complete({"result": {
                        "ok": bool(self._last_internal_browser_load_ok),
                        "url": self.internal_browser_url,
                        "error": "A pagina nao carregou no navegador interno."
                        if not self._last_internal_browser_load_ok else "",
                    }})
                elif remaining <= 0:
                    complete({"result": {"ok": False, "error": "Tempo esgotado ao abrir a pagina.", "url": self.internal_browser_url}})
                else:
                    QTimer.singleShot(250, lambda: wait_for_page(remaining - 1))

            # O callback precisa ocorrer depois de finish_chat_reply registrar a
            # espera; caso contrario uma pagina em cache pode encerrar a tarefa
            # antes de o resultado chegar ao agente.
            QTimer.singleShot(0, wait_for_page)
            return True

        if not self._internal_browser_is_usable():
            self._agent_browser_action_pending = None
            self.add_chat("Erro", "Abra uma pagina com OPEN_URL antes de usar BROWSER_*.")
            return False

        QTimer.singleShot(
            0,
            lambda: self.request_internal_browser_action(action, payload, complete),
        )
        return True

    def _finish_agent_browser_action(self, event):
        pending = self._agent_browser_action_pending
        self._agent_browser_action_pending = None
        if pending is None or not self.chat_busy or not self._chat_waiting_for_browser:
            return
        self._chat_waiting_for_browser = False
        result = event.get("result", event) if isinstance(event, dict) else event
        if isinstance(result, str):
            decoded = self._decode_web_javascript_result(result)
            result = decoded if decoded is not None else {
                "ok": False,
                "error": "Resposta invalida do navegador interno.",
            }
        if not isinstance(result, dict):
            result = {"ok": False, "error": "Resposta invalida do navegador interno."}
        self._continue_agent_after_browser_action(pending["action"], pending["payload"], result)

    def _continue_agent_after_browser_action(self, action, payload, result):
        if self._chat_agent_round >= 12:
            self._set_chat_busy(False)
            self.add_chat("Erro", "Limite de ciclos do agente atingido apos acao no navegador.")
            return
        self._chat_agent_round += 1
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        observation = (
            f"BROWSER_{action.upper()} concluido.\n"
            f"SOLICITACAO: {json.dumps(payload, ensure_ascii=False)}\n"
            f"RESULTADO REAL DO NAVEGADOR:\n{serialized}"
        )
        self._append_chat_activity(
            f"Navegador respondeu; continuando a tarefa ({self._chat_agent_round}/12)."
        )
        self.streaming_bubble = None
        self.streaming_text = ""
        continuation = (
            "[MEROTEC_AGENT_CONTINUATION]\n"
            f"Tarefa original: {self._chat_task_prompt}\n\n"
            "A IDE executou a acao no navegador. Use o resultado real para decidir a proxima "
            "acao ou concluir a tarefa.\n\n"
            f"RESULTADO DA IDE:\n{observation}"
        )
        threading.Thread(
            target=self._generate_reply,
            args=(continuation, observation, None),
            daemon=True,
        ).start()

    @staticmethod
    def _decode_web_javascript_result(result):
        """Normaliza o retorno do QWebEngine antes de validar a automaÃ§Ã£o.

        Dependendo da versÃ£o do Qt/WebEngine, objetos retornados por
        ``runJavaScript`` chegam como ``dict`` ou como JSON serializado. A
        segunda forma era tratada como resposta invÃ¡lida, apesar de o envio ao
        Chat Web ter sido aceito pelo navegador.
        """
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                decoded = json.loads(result)
            except json.JSONDecodeError:
                return None
            return decoded if isinstance(decoded, dict) else None
        return None

    def _run_browser_chat_action(self, payload, callback):
        """Envia uma mensagem e acompanha a resposta sem devolver uma Promise.

        ``QWebEnginePage.runJavaScript`` não aguarda funções JavaScript
        ``async``: ele entrega o resultado da Promise imediatamente ao Python.
        O bridge então interpretava esse retorno vazio como sucesso e o agente
        ficava sem resposta. Mantemos cada avaliação síncrona e fazemos o
        acompanhamento pelo timer do Qt.
        """
        prompt = json.dumps(str(payload.get("prompt", "")), ensure_ascii=False)
        timeout_ms = max(30000, min(600000, int(payload.get("timeout", 300) or 300) * 1000))
        prepare_script = f"""(() => {{
            const visible = el => {{ if (!el) return false; const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; }};
            const assistantSelector = '[data-message-author-role="assistant"], article[data-turn="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]';
            const assistants = () => [...document.querySelectorAll(assistantSelector)].filter(visible);
            const inputs = () => ['#prompt-textarea','textarea[placeholder]','[contenteditable="true"][role="textbox"]','.ql-editor[contenteditable="true"]','textarea','[contenteditable]:not([contenteditable="false"])'].flatMap(s => [...document.querySelectorAll(s)]).filter(visible);
            const latest = () => {{ const items=assistants(); return items.length ? (items[items.length-1].innerText || items[items.length-1].textContent || '').trim() : ''; }};
            const input = inputs()[0];
            if (!input) return {{ok:false,error:'Campo de mensagem nao encontrado.',url:location.href,title:document.title}};
            // Guarda avisos transitórios do provedor. Eles normalmente somem
            // antes do próximo ciclo Python, tornando a falha impossível de
            // diagnosticar pela IDE.
            window.__merotecChatNotice = '';
            window.__merotecChatNoticeObserver?.disconnect?.();
            window.__merotecChatNoticeObserver = new MutationObserver(() => {{
                const notices = [...document.querySelectorAll('[role="alert"], [data-testid*="toast" i], [class*="toast" i], [class*="notification" i]')]
                    .filter(visible)
                    .map(el => (el.innerText || el.textContent || '').trim())
                    .filter(text => text && text.length < 600);
                if (notices.length) window.__merotecChatNotice = notices.at(-1);
            }});
            window.__merotecChatNoticeObserver.observe(document.body, {{childList:true, subtree:true, characterData:true}});
            const prompt = {prompt};
            input.focus();
            if (input.isContentEditable) {{
                const selection = getSelection(), range = document.createRange();
                range.selectNodeContents(input); range.collapse(false);
                selection?.removeAllRanges(); selection?.addRange(range);
                // execCommand ainda e o caminho que React/ProseMirror observam
                // de forma consistente em QWebEngine; textContent sozinho nao
                // atualiza o estado interno de alguns chats.
                if (!document.execCommand('insertText', false, prompt)) input.textContent = prompt;
                input.dispatchEvent(new InputEvent('input', {{bubbles:true, composed:true, inputType:'insertText', data:prompt}}));
            }} else {{
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(input, prompt); else input.value = prompt;
                input.dispatchEvent(new InputEvent('input', {{bubbles:true, composed:true, inputType:'insertText', data:prompt}}));
                input.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
            const sendTarget = ['button[data-testid="send-button"]','button[data-testid*="send" i]','button[aria-label*="Send" i]','button[aria-label*="Enviar" i]','button[title*="Send" i]','button[title*="Enviar" i]','button[type="submit"]'].flatMap(s => [...document.querySelectorAll(s)]).find(el => visible(el) && !el.disabled) || [...document.querySelectorAll('button,[role="button"]')].find(el => visible(el) && !el.disabled && /send|enviar|submit/i.test((el.getAttribute('aria-label')||'')+' '+(el.getAttribute('data-testid')||'')+' '+(el.getAttribute('title')||'')+' '+el.innerText));
            const sendRect = sendTarget?.getBoundingClientRect();
            const before = latest();
            const beforeCount = assistants().length;
            // O envio e feito pelo Qt como tecla Enter nativa. Alguns provedores
            // descartam click() sintético e exibem um aviso breve sem publicar
            // a mensagem, embora o botão esteja visível no DOM.
            return {{ok:true,before:before,beforeCount:beforeCount,sendX:sendRect ? Math.round(sendRect.left + sendRect.width / 2) : -1,sendY:sendRect ? Math.round(sendRect.top + sendRect.height / 2) : -1,url:location.href,title:document.title}};
        }})()"""
        # Serializar o valor explicitamente evita a conversão inconsistente de
        # objetos JavaScript para QVariant entre versões do QWebEngine. O
        # invólucro também transforma uma exceção da página em erro útil, em
        # vez de o callback receber ``None``.
        prepare_script = f"""(() => {{
            try {{
                return JSON.stringify(({prepare_script}));
            }} catch (error) {{
                return JSON.stringify({{ok:false,error:'Falha ao preparar a mensagem: ' + String(error?.message || error),url:location.href,title:document.title}});
            }}
        }})()"""

        def complete(result):
            if callback:
                callback({"result": result})

        def prepared(result):
            prepared_result = self._decode_web_javascript_result(result)
            if not prepared_result or not prepared_result.get("ok"):
                complete(prepared_result or {"ok": False, "error": "Resposta invalida ao enviar mensagem do Chat Web."})
                return
            before = str(prepared_result.get("before") or "")
            deadline = time.monotonic() + (timeout_ms / 1000.0)
            before_count = int(prepared_result.get("beforeCount") or 0)
            state = {
                "text": "",
                "changed_at": 0.0,
                "finished": False,
                "sent": False,
                "send_checks": 0,
                "manual_send_announced": False,
            }
            poll_script = """(() => { try { const visible = el => { if (!el) return false; const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; }; const inputs=['#prompt-textarea','textarea[placeholder]','[contenteditable="true"][role="textbox"]','.ql-editor[contenteditable="true"]','textarea','[contenteditable]:not([contenteditable="false"])'].flatMap(s=>[...document.querySelectorAll(s)]).filter(visible); const input=inputs[0]; const composer=input ? String(input.isContentEditable ? (input.innerText || input.textContent || '') : (input.value || '')).trim() : ''; const items=[...document.querySelectorAll('[data-message-author-role="assistant"], article[data-turn="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]')].filter(visible); const stopping=!!['[data-testid="stop-button"]','button[aria-label*="Stop" i]','button[aria-label*="Parar" i]'].flatMap(s=>[...document.querySelectorAll(s)]).find(visible); const send=[...document.querySelectorAll('button,[role="button"]')].find(el => visible(el) && /send|enviar|submit/.test((el.getAttribute('aria-label')||'')+' '+(el.getAttribute('data-testid')||'')+' '+(el.getAttribute('title')||'')+' '+el.innerText)); return JSON.stringify({ok:true,url:location.href,title:document.title,response:items.length ? (items[items.length-1].innerText || items[items.length-1].textContent || '').trim() : '',count:items.length,composer:composer,streaming:stopping,notice:String(window.__merotecChatNotice || ''),inputTag:input?.tagName || '',inputFocused:!!input && (document.activeElement === input || input.contains(document.activeElement)),sendDisabled:send ? !!send.disabled : null}); } catch (error) { return JSON.stringify({ok:false,error:'Falha ao ler a resposta: '+String(error?.message || error)}); } })()"""

            def poll():
                if state["finished"]:
                    return
                if time.monotonic() >= deadline:
                    state["finished"] = True
                    complete({"ok": False, "error": "Tempo esgotado aguardando a resposta do Chat Web.", "url": self.internal_browser_url})
                    return

                def received(current):
                    if state["finished"]:
                        return
                    current_result = self._decode_web_javascript_result(current)
                    if not current_result:
                        QTimer.singleShot(700, poll)
                        return
                    if not current_result.get("ok", True):
                        state["finished"] = True
                        complete({"ok": False, "error": str(current_result.get("error") or "Falha ao ler a resposta do Chat Web."), "url": current_result.get("url", self.internal_browser_url)})
                        return
                    answer = str(current_result.get("response") or "").strip()
                    notice = str(current_result.get("notice") or "").strip()
                    if notice and not current_result.get("streaming"):
                        state["finished"] = True
                        complete({"ok": False, "error": f"O Chat Web recusou o envio: {notice}", "url": current_result.get("url", self.internal_browser_url)})
                        return
                    if not state["sent"]:
                        state["send_checks"] += 1
                        state["sent"] = (
                            not str(current_result.get("composer") or "").strip()
                            or bool(current_result.get("streaming"))
                            or int(current_result.get("count") or 0) > before_count
                        )
                        if not state["sent"]:
                            if not state["manual_send_announced"] and state["send_checks"] >= 2:
                                state["manual_send_announced"] = True
                                self.set_status(
                                    "Mensagem pronta no Chat Web — clique na seta azul para enviar.",
                                    "info",
                                )
                            # O provedor exige um gesto diretamente do usuário
                            # neste WebEngine. A tarefa fica ativa e, após o
                            # clique manual, este mesmo loop recebe a resposta.
                            QTimer.singleShot(700, poll)
                            return
                    if answer and (answer != before or int(current_result.get("count") or 0) > before_count):
                        now = time.monotonic()
                        if answer != state["text"]:
                            state["text"] = answer
                            state["changed_at"] = now
                        elif not current_result.get("streaming") and now - state["changed_at"] >= 1.8:
                            state["finished"] = True
                            complete({"ok": True, "response": answer, "url": current_result.get("url", self.internal_browser_url), "title": current_result.get("title", ""), "artifacts": {}})
                            return
                    QTimer.singleShot(700, poll)

                self.browser_view.page().runJavaScript(poll_script, received)

            QTimer.singleShot(700, poll)

        self.browser_view.page().runJavaScript(prepare_script, prepared)

    def _run_browser_action(self, action, payload=None, callback=None):
        if not self._internal_browser_is_usable():
            if callback:
                callback({"result": {"ok": False, "error": "O navegador interno nao esta disponivel."}})
            return
        payload = payload or {}
        target = str(payload.get("target", ""))
        value = str(payload.get("value", ""))
        if action == "inspect":
            script = """(() => JSON.stringify({ok:true,url: location.href, title: document.title, text: document.body.innerText.slice(0,12000), elements: [...document.querySelectorAll('a,button,input,textarea,select')].slice(0,120).map((e,i)=>({ref:'e'+i,tag:e.tagName.toLowerCase(),label:e.innerText||e.getAttribute('aria-label')||e.name||e.placeholder||'',href:e.href||''}))}))()"""
        elif action == "scroll":
            script = f"(() => JSON.stringify((window.scrollBy(0, {'-600' if target == 'up' else '600'}), {{ok:true,url:location.href}})))()"
        elif action == "chat":
            self._run_browser_chat_action(payload, callback)
            return
        else:
            ref = target.replace("e", "")
            selector = f"[...document.querySelectorAll('a,button,input,textarea,select')][{ref}]"
            if action == "click":
                script = f"(() => {{ const e={selector}; if(!e) return JSON.stringify({{ok:false,error:'elemento nao encontrado'}}); e.click(); return JSON.stringify({{ok:true,url:location.href}}); }})()"
            elif action == "type":
                script = f"(() => {{ const e={selector}; if(!e) return JSON.stringify({{ok:false,error:'elemento nao encontrado'}}); e.focus(); e.value={json.dumps(value, ensure_ascii=False)}; e.dispatchEvent(new Event('input',{{bubbles:true}})); return JSON.stringify({{ok:true,url:location.href}}); }})()"
            else:
                if callback:
                    callback({"result": {"ok": False, "error": f"Acao nao suportada: {action}"}})
                return
        self.browser_view.page().runJavaScript(script, lambda result: callback({"result": result}) if callback else None)

    def current_editor(self):
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, CodeEditor) else None

    def current_path(self):
        return self.paths_by_tab.get(self.tabs.currentIndex())

    def save_current(self):
        editor = self.current_editor()
        if not editor:
            return
        path = self.current_path()
        if path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Salvar arquivo", str(self.workspace / "novo_arquivo.py"))
            if not filename:
                return
            path = Path(filename)
            self.paths_by_tab[self.tabs.currentIndex()] = path
        try:
            path.write_text(editor.toPlainText(), encoding="utf-8", newline="\n")
        except OSError as exc:
            QMessageBox.critical(self, "Merotec IA", f"Nao foi possivel salvar.\n{exc}")
            return
        self.tabs.setTabText(self.tabs.currentIndex(), path.name)
        self.set_status("Arquivo salvo")
        QTimer.singleShot(1800, lambda: self.set_status("Pronto"))
        self.refresh_tree()

    def mark_dirty(self, editor):
        index = self.tabs.indexOf(editor)
        path = self.paths_by_tab.get(index)
        if path and not self.tabs.tabText(index).endswith(" *"):
            self.tabs.setTabText(index, f"{path.name} *")

    def close_tab(self, index):
        editor = self.tabs.widget(index)
        if editor is self.browser_view:
            # A aba mantém a sessão autenticada usada pelo Chat Web. Removê-la
            # deixa um QObject órfão e as validações seguintes passam a ver o
            # navegador como indisponível. Apenas oculte a aba; ela é reaberta
            # sob demanda por open_internal_browser().
            self.tabs.setCurrentIndex(max(0, index - 1))
            self.tabs.removeTab(index)
            return
        if editor and self.tabs.tabText(index).endswith(" *"):
            choice = QMessageBox.question(self, "Merotec IA", "Salvar alteracoes antes de fechar?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self.tabs.setCurrentIndex(index)
                self.save_current()
        self.tabs.removeTab(index)
        self.paths_by_tab = {i if i < index else i - 1: p for i, p in self.paths_by_tab.items() if i != index}

    def closeEvent(self, event):
        dirty = [self.tabs.tabText(index) for index in range(self.tabs.count()) if self.tabs.tabText(index).endswith(" *")]
        if dirty:
            choice = QMessageBox.question(
                self,
                "Merotec IA",
                "Ha arquivos com alteracoes nao salvas. Fechar mesmo assim?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._close_visual_test_preview()
        self._shutdown_terminal_processes()
        try:
            self.voice.stop()
        except Exception:
            pass
        self.settings["last_workspace"] = str(self.workspace)
        self._save_settings()
        event.accept()

    def _shutdown_terminal_processes(self):
        """Encerra processos antes de destruir a janela e seus objetos Qt."""
        for attribute in ("terminal_process", "terminal_session"):
            process = getattr(self, attribute, None)
            if not process:
                continue
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(1500)
            except RuntimeError:
                pass
            setattr(self, attribute, None)
        self._stop_terminal_progress("process")
        self._stop_terminal_progress("session")
        self._terminal_session_marker = ""
        self._terminal_session_queue.clear()
        self._terminal_session_interactive = False

    def new_file(self):
        editor = CodeEditor()
        editor.setPlainText("# Novo arquivo Merotec IA\n")
        editor.cursorPositionChanged.connect(self.update_cursor)
        editor.textChanged.connect(lambda e=editor: self.mark_dirty(e))
        tab = self.tabs.addTab(editor, "sem_titulo.py *")
        self.tabs.setCurrentIndex(tab)

    def choose_workspace(self):
        folder = QFileDialog.getExistingDirectory(self, "Abrir pasta do projeto", str(self.workspace))
        if folder:
            self.open_workspace(folder)

    def open_project(self):
        self.choose_workspace()

    def set_workspace(self, path):
        target = Path(path)
        if not target.is_dir():
            QMessageBox.warning(self, "Merotec IA", "Pasta invalida.")
            return
        self.open_workspace(target)

    def open_external_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Abrir arquivo", str(self.workspace))
        if filename:
            path = Path(filename).resolve()
            self.open_file(path)

    def create_project(self):
        name, accepted = QInputDialog.getText(self, "Novo projeto", "Nome do projeto:")
        if not accepted or not name.strip():
            return
        kinds = ["python", "web", "flet", "dart", "flutter", "cpp", "csharp", "empty"]
        kind, accepted = QInputDialog.getItem(self, "Tipo do projeto", "Template:", kinds, 0, False)
        if not accepted:
            return
        parent = QFileDialog.getExistingDirectory(self, "Onde criar o projeto", str(PROJECTS_DIR))
        if not parent:
            return
        try:
            project = self.pm.create_project(parent, name, kind)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Merotec IA", f"Nao foi possivel criar o projeto.\n{exc}")
            return
        self.open_workspace(project)
        self.add_chat("Sistema", f"Projeto criado e aberto: {project}")

    def close_project(self):
        """Fecha as abas pertencentes ao projeto e retorna à pasta neutra."""
        workspace = self.workspace.resolve()
        neutral_workspace = PROJECTS_DIR.resolve()
        if workspace == neutral_workspace:
            self.set_status("Nenhum projeto aberto.")
            return

        project_tabs = []
        for index, path in self.paths_by_tab.items():
            try:
                Path(path).resolve().relative_to(workspace)
            except ValueError:
                continue
            project_tabs.append(index)

        dirty_tabs = [
            self.tabs.tabText(index).removesuffix(" *")
            for index in project_tabs
            if self.tabs.tabText(index).endswith(" *")
        ]
        if dirty_tabs:
            names = ", ".join(dirty_tabs[:4])
            if len(dirty_tabs) > 4:
                names += f" e mais {len(dirty_tabs) - 4}"
            choice = QMessageBox.question(
                self,
                "Merotec IA",
                f"Fechar o projeto sem salvar as abas alteradas?\n{names}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return

        removed = set(project_tabs)
        for index in sorted(removed, reverse=True):
            self.tabs.removeTab(index)
        self.paths_by_tab = {
            index - sum(1 for removed_index in removed if removed_index < index): path
            for index, path in self.paths_by_tab.items()
            if index not in removed
        }

        neutral_workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = neutral_workspace
        self.current_workspace = str(neutral_workspace)
        self.project_open = False
        self.terminal_working_directory = neutral_workspace
        self.memory_subnet.reset_workspace(neutral_workspace)
        self.restore_workspace_ai_context_memory(neutral_workspace)
        # O usuario fechou o projeto explicitamente: nao restaure um projeto
        # automaticamente na proxima abertura da IDE.
        self.settings["last_workspace"] = ""
        self.settings["start_without_project"] = True
        self._save_settings()
        self.update_recent_menu()
        self._update_workspace_root_label()
        self.refresh_tree()
        self.append_terminal(f"\nProjeto fechado: {workspace.name}\n")
        self._terminal_prompt()
        self.add_chat("Sistema", f"Projeto fechado: {workspace.name}. Abra um projeto para continuar.")
        self.set_status("Projeto fechado. Selecione um projeto para continuar.")

    def open_workspace(self, folder):
        self.workspace = Path(folder).resolve()
        self.project_open = True
        self.terminal_working_directory = self.workspace
        self.current_workspace = str(self.workspace)
        self.memory_subnet.reset_workspace(self.workspace)
        self.restore_workspace_ai_context_memory(self.workspace)
        self.settings["start_without_project"] = False
        self.settings["last_workspace"] = self.current_workspace
        self.settings["recent_projects"] = [self.current_workspace, *[item for item in self.settings.get("recent_projects", []) if item != self.current_workspace]][:10]
        self._save_settings()
        self.update_recent_menu()
        self._update_workspace_root_label()
        self.model.setRootPath(self.current_workspace)
        self.tree.setVisible(True)
        self.tree.setRootIndex(self.file_filter.mapFromSource(self.model.index(self.current_workspace)))
        self.append_terminal(f"\nPasta aberta: {self.workspace}\n")
        self._terminal_prompt()

    def refresh_tree(self):
        if not getattr(self, "project_open", True):
            self.tree.setVisible(False)
            self.tree.setRootIndex(QModelIndex())
            return
        self.tree.setVisible(True)
        self.model.setRootPath("")
        self.model.setRootPath(str(self.workspace))
        self.tree.setRootIndex(self.file_filter.mapFromSource(self.model.index(str(self.workspace))))

    def filter_tree(self, query):
        self.file_filter.setFilterFixedString(query.strip())
        if query.strip():
            self.tree.expandAll()

    def run_current(self):
        path = self.current_path()
        if not path:
            QMessageBox.information(self, "Merotec IA", "Abra ou salve um arquivo Python, C++ ou C# para executar.")
            return
        self.save_current()
        suffix = path.suffix.lower()
        if suffix in {".cpp", ".cc", ".cxx"}:
            self._run_cpp(path)
            return
        if suffix == ".cs":
            self._run_csharp(path)
            return
        if suffix != ".py":
            QMessageBox.information(self, "Merotec IA", "A execucao integrada esta disponivel para arquivos Python, C++ e C#.")
            return
        self.set_status("Executando...")
        working_directory = path.parent
        self.append_terminal(f"\nPS {working_directory}> python {path.name}\n")
        self.start_terminal_process(
            sys.executable,
            ["-u", str(path)],
            f"python {path.name}",
            working_directory=working_directory,
        )

    def _run_cpp(self, path: Path):
        """Compila e executa o arquivo C++ aberto usando o compilador g++ instalado."""
        output_dir = path.parent / "build"
        output_dir.mkdir(exist_ok=True)
        executable = output_dir / path.stem
        if os.name == "nt":
            executable = executable.with_suffix(".exe")
        command = f'g++ -std=c++17 "{path}" -o "{executable}" && "{executable}"'
        self.append_terminal(f"\nPS {path.parent}> {command}\n")
        self._send_to_terminal_session(command)
        self.set_status("Compilando e executando C++")

    def _run_csharp(self, path: Path):
        """Executa o projeto .NET associado ao arquivo C# aberto."""
        project_file = next(path.parent.glob("*.csproj"), None)
        for parent in path.parents:
            if project_file or parent == self.workspace.parent:
                break
            project_file = next(parent.glob("*.csproj"), None)
        if not project_file:
            QMessageBox.information(
                self,
                "Merotec IA",
                "Nenhum arquivo .csproj foi encontrado. Crie um projeto C# pela opção Novo projeto ou abra uma pasta .NET.",
            )
            return
        command = f'dotnet run --project "{project_file}"'
        self.append_terminal(f"\nPS {project_file.parent}> {command}\n")
        self._send_to_terminal_session(command)
        self.set_status("Compilando e executando C#")

    def terminal_clear(self):
        self.terminal.clear()
        self._terminal_prompt()

    def _terminal_prompt(self):
        self.terminal.appendPlainText(f"PS {self.terminal_working_directory}> ")

    def append_terminal(self, text):
        """Acrescenta saida preservando atualizacoes de progresso com ``\r``."""
        text = str(text).replace("\r\n", "\n")
        cursor = self.terminal.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if self._terminal_replace_current_line:
            if not text.startswith("\n"):
                cursor.select(cursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
            self._terminal_replace_current_line = False
        pieces = text.split("\r")
        cursor.insertText(pieces[0])
        for piece in pieces[1:]:
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(piece)
        self._terminal_replace_current_line = text.endswith("\r")
        self.terminal.setTextCursor(cursor)
        self.terminal.ensureCursorVisible()

    def focus_terminal_input(self):
        self.terminal_input.setFocus()

    def run_terminal_command(self):
        command = self.terminal_input.text().strip()
        if not command:
            return
        if self.terminal_process and self.terminal_process.state() != QProcess.ProcessState.NotRunning:
            self.append_terminal("Um processo ja esta em execucao. Interrompa-o antes de iniciar outro.\n")
            return
        self.terminal_input.clear()
        if self._terminal_session_marker and self._terminal_session_interactive:
            self._send_terminal_session_input(command)
            return
        self.terminal_input.remember(command)
        self._send_to_terminal_session(command)

    def _send_to_terminal_session(self, command):
        """Envia comandos para o mesmo PowerShell, preservando venv, cd e variaveis."""
        if self._terminal_session_marker:
            self._terminal_session_queue.append(command)
            self.append_terminal(f"[Terminal] Comando adicionado a fila ({len(self._terminal_session_queue)}): {command}\n")
            self.set_status("Comando aguardando na fila do terminal")
            return
        process = self.terminal_session
        try:
            session_is_running = bool(process) and process.state() != QProcess.ProcessState.NotRunning
        except RuntimeError:
            session_is_running = False
            self.terminal_session = None
        if not session_is_running:
            process = QProcess(self)
            process.setWorkingDirectory(str(self.terminal_working_directory.resolve()))
            # O painel recebe stdout por pipe, nao por um console Win32. Flet/Rich
            # precisa de saida simples nesse caso; o modo Live tenta controlar o
            # cursor do terminal e encerra o build com traceback.
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert("FLET_CLI_NO_RICH_OUTPUT", "1")
            environment.insert("TERM", "dumb")
            environment.insert("TTY_COMPATIBLE", "0")
            environment.insert("TTY_INTERACTIVE", "0")
            environment.insert("PYTHONUTF8", "1")
            environment.insert("PYTHONUNBUFFERED", "1")
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(lambda p=process: self._read_terminal_stdout(p))
            process.finished.connect(lambda exit_code, exit_status, p=process: self._terminal_session_finished(p, exit_code, exit_status))
            process.errorOccurred.connect(lambda error, p=process: self._terminal_session_error(p, error))
            self.terminal_session = process
            if os.name == "nt":
                # A compilacao Android/Flet normalmente e documentada e testada
                # via activate.bat. Usar cmd preserva esse ambiente entre comandos.
                process.start(os.environ.get("COMSPEC", "cmd.exe"), ["/Q", "/K"])
            else:
                process.start("/bin/sh", ["-i"])
        self._terminal_session_command_number += 1
        marker = f"__MEROTEC_COMMAND_DONE_{self._terminal_session_command_number}__"
        self._terminal_session_marker = marker
        self._terminal_session_output_buffer = ""
        self._terminal_session_interactive = self._command_requires_interactive_input(command)
        self._start_terminal_progress("session", f"Executando: {command}")
        if os.name == "nt":
            wrapped_command = f"{command} & echo {marker}"
        else:
            wrapped_command = f"({command}); printf '{marker}\\n'"
        try:
            process.write((wrapped_command + "\r\n").encode("utf-8"))
        except RuntimeError:
            self.terminal_session = None
            self._stop_terminal_progress("session")
            self._terminal_session_queue.clear()
            self.append_terminal("A sessao do terminal foi encerrada. Envie o comando novamente.\n")
            return
        self.set_status("Executando comando no terminal")

    @staticmethod
    def _command_requires_interactive_input(command):
        """Ferramentas que normalmente pedem senha ou confirmação no próprio terminal."""
        return bool(re.search(r"\b(?:jarsigner|keytool|apksigner|adb)\b", str(command or ""), re.IGNORECASE))

    def _send_terminal_session_input(self, value):
        """Envia uma resposta ao processo ativo sem expô-la como comando ou histórico."""
        process = self.terminal_session
        try:
            if not process or process.state() == QProcess.ProcessState.NotRunning:
                raise RuntimeError("sessao encerrada")
            process.write((value + "\r\n").encode("utf-8"))
        except RuntimeError:
            self.terminal_session = None
            self._terminal_session_interactive = False
            self._stop_terminal_progress("session")
            self.append_terminal("A sessao interativa foi encerrada. Execute o comando novamente.\n")
            return
        self.append_terminal("[Terminal] Resposta enviada ao processo interativo.\n")
        self.set_status("Aguardando o processo interativo")

    def _terminal_session_finished(self, process, _exit_code, _exit_status):
        if process is not self.terminal_session:
            return
        self._read_terminal_stdout(process)
        self.terminal_session = None
        self._stop_terminal_progress("session")
        self._terminal_session_marker = ""
        self._terminal_session_queue.clear()
        self._terminal_session_interactive = False
        try:
            process.deleteLater()
        except RuntimeError:
            pass
        self.set_status("Sessao do terminal encerrada")

    def _terminal_session_error(self, process, error):
        if process is not self.terminal_session or error != QProcess.ProcessError.FailedToStart:
            return
        try:
            detail = process.errorString()
            process.deleteLater()
        except RuntimeError:
            detail = "processo removido"
        self.append_terminal(f"Nao foi possivel iniciar o terminal: {detail}.\n")
        self.terminal_session = None
        self._stop_terminal_progress("session")
        self._terminal_session_marker = ""
        self._terminal_session_queue.clear()
        self._terminal_session_interactive = False
        self.set_status("Falha ao iniciar terminal")

    def _shell_command(self, command):
        """Retorna o interpretador que corresponde ao prompt exibido pela IDE."""
        if os.name == "nt":
            # O terminal sempre exibiu um prompt PowerShell. Executar com cmd /c
            # fazia comandos como Get-ChildItem, $env:PATH e pipelines PowerShell
            # serem interpretados pelo shell errado.
            powershell = QStandardPaths.findExecutable("pwsh.exe") or QStandardPaths.findExecutable("powershell.exe")
            if powershell:
                command = self._expand_cmd_environment_variables(command)
                command = self._normalize_powershell_command(command)
                return powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
            # Fallback para instalacoes Windows reduzidas sem PowerShell.
            return os.environ.get("COMSPEC", "cmd.exe"), ["/d", "/s", "/c", command]
        return "/bin/sh", ["-lc", command]

    @staticmethod
    def _expand_cmd_environment_variables(command):
        """Aceita tambem %VAR% dos comandos copiados de cmd.exe/batch."""
        def replace(match):
            name = match.group(1)
            return os.environ.get(name, match.group(0))

        return re.sub(r"%([^%]+)%", replace, command)

    @staticmethod
    def _normalize_powershell_command(command):
        """Acrescenta o operador PowerShell para um executável entre aspas."""
        text = str(command or "")
        quoted_executable = r'^([ \t]*)(["\'])(?:[^"\']+?\.(?:exe|cmd|bat))\2(?=\s|$)'
        if re.match(quoted_executable, text, flags=re.IGNORECASE):
            return re.sub(r"^([ \t]*)", r"\1& ", text, count=1)
        return text

    def _start_shell_command(self, command, working_directory=None):
        program, arguments = self._shell_command(self._make_python_output_unbuffered(command))
        return self.start_terminal_process(program, arguments, command, working_directory=working_directory)

    @staticmethod
    def _make_python_output_unbuffered(command):
        """Evita que Python/PyInstaller retenha logs quando a saída é um pipe."""
        pattern = (
            r"^(\s*(?:&\s+)?(?:"
            r"python(?:\.exe)?|py|"
            r"[\"'][^\"']*?pythonw?(?:\.exe)?[\"']"
            r")(?:\s+-\d+(?:\.\d+)*)?)(?!\s+-u\b)(?=\s)"
        )
        return re.sub(pattern, r"\1 -u", command, count=1, flags=re.IGNORECASE)

    def start_terminal_process(self, program, arguments, label, working_directory=None):
        if self.terminal_process and self.terminal_process.state() != QProcess.ProcessState.NotRunning:
            self.append_terminal("Um processo ja esta em execucao. Interrompa-o antes de iniciar outro.\n")
            return False
        process = QProcess(self)
        process.setWorkingDirectory(str(Path(working_directory or self.terminal_working_directory).resolve()))
        # Um único fluxo preserva a ordem entre stdout/stderr e evita que logs
        # importantes do compilador só apareçam quando o processo terminar.
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda p=process: self._read_terminal_stdout(p))
        process.finished.connect(lambda exit_code, exit_status, p=process: self._terminal_finished(p, exit_code, exit_status))
        process.errorOccurred.connect(lambda error, p=process: self._terminal_error(p, error))
        self.terminal_process = process
        self._terminal_command = label
        self._terminal_cancel_requested = False
        self.terminal_input.setEnabled(False)
        self._start_terminal_progress("process", f"Executando: {label}")
        process.start(program, arguments)
        return True

    def _start_terminal_progress(self, source, description):
        self._terminal_progress_percent = None
        self._terminal_progress_sources[source] = (str(description), time.monotonic())
        if hasattr(self, "terminal_progress_timer") and not self.terminal_progress_timer.isActive():
            self.terminal_progress_timer.start()
        self._render_terminal_progress()

    def _stop_terminal_progress(self, source):
        self._terminal_progress_sources.pop(source, None)
        if self._terminal_progress_sources:
            self._render_terminal_progress()
            return
        if hasattr(self, "terminal_progress_timer"):
            self.terminal_progress_timer.stop()
        if hasattr(self, "terminal_progress_label"):
            self.terminal_progress_label.hide()
        if hasattr(self, "terminal_progress_bar"):
            self.terminal_progress_bar.hide()

    def _render_terminal_progress(self):
        if not self._terminal_progress_sources or not hasattr(self, "terminal_progress_label"):
            return
        description, started_at = next(reversed(self._terminal_progress_sources.values()))
        elapsed = max(0, int(time.monotonic() - started_at))
        frames = ("|", "/", "-", "\\")
        frame = frames[self._terminal_progress_frame % len(frames)]
        self._terminal_progress_frame += 1
        self.terminal_progress_label.setText(f"{frame} {description} ({elapsed}s)")
        self.terminal_progress_label.show()
        if hasattr(self, "terminal_progress_bar"):
            if self._terminal_progress_percent is None:
                self.terminal_progress_bar.setRange(0, 0)
            else:
                self.terminal_progress_bar.setRange(0, 100)
                self.terminal_progress_bar.setValue(self._terminal_progress_percent)
            self.terminal_progress_bar.show()

    @staticmethod
    def _decode_process_output(data):
        """Aceita tanto UTF-8 quanto a pagina de codigo do terminal local."""
        if not data:
            return ""
        for encoding in ("utf-8", locale.getpreferredencoding(False), "cp850", "cp1252"):
            try:
                return data.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return data.decode("utf-8", errors="replace")

    def _read_terminal_stdout(self, process=None):
        process = process or self.terminal_process
        if not process:
            return
        try:
            data = bytes(process.readAllStandardOutput())
        except RuntimeError:
            return
        text = self._decode_process_output(data)
        if process is self.terminal_process and getattr(self, "_agent_command_pending", ""):
            self._agent_command_output = (getattr(self, "_agent_command_output", "") + text)[-12000:]
        self._observe_terminal_progress_output(text)
        if process is self.terminal_session:
            self._append_terminal_session_output(text)
            return
        self.append_terminal(text)

    def _observe_terminal_progress_output(self, text):
        """Aproveita porcentagens emitidas por Flutter/Flet e outros instaladores."""
        matches = re.findall(r"\b(\d{1,3})%", str(text or ""))
        if not matches:
            return
        percent = int(matches[-1])
        if 0 <= percent <= 100:
            self._terminal_progress_percent = percent
            self._render_terminal_progress()

    def _append_terminal_session_output(self, text):
        """Remove o marcador interno usado para saber quando um comando da sessão terminou."""
        self._terminal_session_output_buffer += text
        marker = self._terminal_session_marker
        if not marker:
            self.append_terminal(self._terminal_session_output_buffer)
            self._terminal_session_output_buffer = ""
            return
        marker_index = self._terminal_session_output_buffer.find(marker)
        if marker_index >= 0:
            before = self._terminal_session_output_buffer[:marker_index]
            after = self._terminal_session_output_buffer[marker_index + len(marker):]
            self.append_terminal((before + after).lstrip("\r\n"))
            self._terminal_session_output_buffer = ""
            self._terminal_session_marker = ""
            self._terminal_session_interactive = False
            self._stop_terminal_progress("session")
            self.set_status("Terminal pronto")
            self._run_next_terminal_session_command()
            return
        safe_length = max(0, len(self._terminal_session_output_buffer) - len(marker) - 2)
        if safe_length:
            self.append_terminal(self._terminal_session_output_buffer[:safe_length])
            self._terminal_session_output_buffer = self._terminal_session_output_buffer[safe_length:]

    def _run_next_terminal_session_command(self):
        if not self._terminal_session_queue:
            return
        command = self._terminal_session_queue.pop(0)
        QTimer.singleShot(0, lambda value=command: self._send_to_terminal_session(value))

    def _read_terminal_stderr(self, process=None):
        process = process or self.terminal_process
        if not process:
            return
        try:
            data = bytes(process.readAllStandardError())
        except RuntimeError:
            return
        text = self._decode_process_output(data)
        if process is self.terminal_process and getattr(self, "_agent_command_pending", ""):
            self._agent_command_output = (getattr(self, "_agent_command_output", "") + text)[-12000:]
        self.append_terminal(text)

    def _terminal_error(self, process, error):
        if process is not self.terminal_process:
            return
        if error == QProcess.ProcessError.FailedToStart:
            detail = process.errorString().strip()
            self.append_terminal(f"Nao foi possivel iniciar '{self._terminal_command}': {detail or 'executavel indisponivel'}.\n")
            self.set_status("Falha ao iniciar comando")
            self.terminal_input.setEnabled(True)
            self._terminal_cancel_requested = False
            self._stop_terminal_progress("process")
            process.deleteLater()
            self.terminal_process = None
            self._terminal_prompt()
            self.terminal_input.setFocus()
            agent_command = getattr(self, "_agent_command_pending", "")
            if agent_command:
                self._agent_command_pending = ""
                self._continue_agent_after_command(agent_command, False, -1, detail or "Não foi possível iniciar o comando.")

    def _terminal_finished(self, process, exit_code, exit_status):
        if process is not self.terminal_process:
            return
        self._read_terminal_stdout(process)
        self._read_terminal_stderr(process)
        succeeded = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        was_cancelled = self._terminal_cancel_requested
        agent_command = getattr(self, "_agent_command_pending", "")
        agent_output = getattr(self, "_agent_command_output", "")
        if was_cancelled:
            message = "\nProcesso interrompido.\n"
        else:
            message = "\nProcesso concluido com sucesso.\n" if succeeded else f"\nProcesso terminou com codigo {exit_code}.\n"
        self.append_terminal(message)
        self.set_status("Interrompido" if was_cancelled else ("Pronto" if succeeded else "Execucao com erro"))
        self.terminal_input.setEnabled(True)
        self._terminal_cancel_requested = False
        self._stop_terminal_progress("process")
        process.deleteLater()
        self.terminal_process = None
        self._terminal_prompt()
        self.terminal_input.setFocus()
        if agent_command:
            self._agent_command_pending = ""
            self._agent_command_output = ""
            self._continue_agent_after_command(agent_command, succeeded and not was_cancelled, exit_code, agent_output)

    def cancel_terminal_process(self):
        process = self.terminal_process or self.terminal_session
        if not process or process.state() == QProcess.ProcessState.NotRunning:
            self.set_status("Nenhum processo em execucao")
            return
        self.append_terminal("\nInterrompendo processo...\n")
        self._terminal_cancel_requested = True
        process.terminate()
        QTimer.singleShot(1500, lambda: self._force_stop_terminal_process(process))

    def _force_stop_terminal_process(self, process):
        """Encerra tambem os filhos iniciados pelo shell, se ainda houver processo."""
        if process not in (self.terminal_process, self.terminal_session) or process.state() == QProcess.ProcessState.NotRunning:
            return
        if os.name == "nt" and process.processId():
            QProcess.startDetached("taskkill.exe", ["/PID", str(process.processId()), "/T", "/F"])
        process.kill()

    def add_chat(self, author, message, outgoing=False, attachments=None):
        bubble = ChatBubble(author, message, outgoing, attachments)
        row = QHBoxLayout()
        if outgoing:
            row.addStretch()
        row.addWidget(bubble)
        if not outgoing:
            row.addStretch()
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, lambda: self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum()))
        return bubble

    def _is_image_attachment(self, path: Path):
        return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

    def _set_button_state(self, button, *, active=False, busy=False):
        if not button:
            return
        button.setProperty("active", active)
        button.setProperty("busy", busy)
        button.style().unpolish(button)
        button.style().polish(button)

    def _set_chat_busy(self, busy):
        self.chat_busy = busy
        if busy:
            self.chat_started_at = time.monotonic()
            self.chat_last_activity = "Preparando a tarefa"
            self._start_terminal_progress("ai", "IA processando")
        else:
            self.chat_started_at = None
            self._stop_terminal_progress("ai")
        self.chat_input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.attach_button.setEnabled(not busy)
        if hasattr(self, "image_button"):
            self.image_button.setEnabled(not busy)
        self._set_button_state(self.send_button, busy=busy)
        self._set_button_state(self.cancel_button, active=busy)
        self.cancel_button.setVisible(busy)
        if hasattr(self, "quota_refresh_timer"):
            if busy:
                self.quota_refresh_timer.start()
            else:
                self.quota_refresh_timer.stop()
                self.refresh_quota_status()

    def _clear_attachment_rows(self):
        while self.attachment_items_layout.count():
            item = self.attachment_items_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _attachment_preview_widget(self, path: Path):
        preview = QLabel()
        preview.setObjectName("attachmentPreview")
        preview.setFixedSize(58, 42)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._is_image_attachment(path):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                preview.setPixmap(
                    pixmap.scaled(
                        58,
                        42,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return preview
        preview.setText("FILE")
        return preview

    def render_attachments(self):
        self._clear_attachment_rows()
        if not self.pending_attachments:
            self.attachment_label.setText("Anexos na fila (0)")
            self.attachment_panel.hide()
            return
        self.attachment_label.setText(f"Anexos na fila ({len(self.pending_attachments)})")
        for path in self.pending_attachments:
            row = QFrame()
            row.setObjectName("attachmentItem")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(7, 6, 7, 6)
            row_layout.setSpacing(8)
            row_layout.addWidget(self._attachment_preview_widget(path))
            meta = QVBoxLayout()
            name = QLabel(path.name)
            name.setObjectName("attachmentName")
            name.setWordWrap(True)
            detail = QLabel("Imagem pronta" if self._is_image_attachment(path) else path.suffix.lower().lstrip(".").upper() or "Arquivo")
            detail.setObjectName("attachmentDetail")
            meta.addWidget(name)
            meta.addWidget(detail)
            row_layout.addLayout(meta, 1)
            remove = QPushButton("X")
            remove.setObjectName("attachmentRemoveButton")
            remove.setToolTip("Remover anexo")
            remove.clicked.connect(lambda _checked=False, target=path: self.remove_attachment(target))
            row_layout.addWidget(remove)
            self.attachment_items_layout.addWidget(row)
        self.attachment_panel.show()

    def add_attachments(self):
        self._set_button_state(self.attach_button, busy=True)
        try:
            paths, _ = QFileDialog.getOpenFileNames(self, "Anexar arquivos", str(self.workspace))
        finally:
            self._set_button_state(self.attach_button, busy=False)
        if not paths:
            return
        queued = {str(path.resolve()) for path in self.pending_attachments}
        for path in paths:
            item = Path(path)
            key = str(item.resolve())
            if key not in queued:
                self.pending_attachments.append(item)
                queued.add(key)
        self.render_attachments()

    def show_attachment_menu(self):
        """Exibe as ações de anexo em uma lista vertical junto ao compositor."""
        menu = QMenu(self)
        menu.setObjectName("attachmentMenu")
        attach_action = menu.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            "Anexar arquivo...",
        )
        image_action = menu.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Gerar imagem com IA...",
        )
        video_action = menu.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            "Gerar vídeo com IA...",
        )
        attach_action.triggered.connect(self.add_attachments)
        image_action.triggered.connect(self.request_image_generation)
        video_action.triggered.connect(self.request_video_generation)
        menu.exec(self.attach_button.mapToGlobal(self.attach_button.rect().topLeft()))

    def clear_attachments(self):
        self.pending_attachments = []
        self.render_attachments()

    def remove_attachment(self, path):
        target = str(Path(path).resolve())
        self.pending_attachments = [item for item in self.pending_attachments if str(item.resolve()) != target]
        self.render_attachments()

    def add_clipboard_image(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if not mime_data or not mime_data.hasImage():
            return False
        image = clipboard.image()
        if image.isNull():
            self.add_chat("Erro", "Nao consegui ler a imagem copiada.")
            return True

        attachments_dir = self.workspace / ".merotec_attachments"
        filename = f"print_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        path = attachments_dir / filename
        try:
            attachments_dir.mkdir(parents=True, exist_ok=True)
            if not image.save(str(path), "PNG"):
                raise OSError("o Windows nao permitiu salvar a imagem do clipboard")
        except OSError as exc:
            self.add_chat("Erro", f"Nao consegui anexar o print: {exc}")
            return True

        self.pending_attachments.append(path)
        self.render_attachments()
        self.set_status("Print anexado; envie a mensagem quando estiver pronto.")
        return True

    def send_chat(self):
        prompt = self.chat_input.toPlainText().strip()
        attachments = list(self.pending_attachments)
        if not prompt and not attachments:
            return
        if not prompt and attachments:
            prompt = "Analise os anexos e me diga o que fazer."
        self._set_chat_busy(True)
        self._chat_task_prompt = prompt
        self._chat_agent_round = 0
        self._chat_pending_validation_paths = set()
        self._chat_auto_validation_paths = set()
        self._chat_waiting_for_command = False
        self._chat_waiting_for_browser = False
        self._agent_browser_action_pending = None
        self._chat_write_staging = {}
        self.chat_input.clear()
        self.add_chat("Voce", prompt, True, attachments)
        self._remember_ai_context_message("Voce", prompt)
        if not self._is_direct_chat_request(prompt) and not self._is_chat_continuation_request(prompt):
            self.active_ai_objective = prompt
            self.persist_workspace_ai_context_memory()
        if attachments:
            self.clear_attachments()
        self.set_status("Merotec IA pensando...")
        if self._is_image_generation_request(prompt):
            self.streaming_text = ""
            self.streaming_bubble = None
            self.activity_bubble = self.add_chat("Atividade da IA", "• Gerando imagem...")
            self.activity_lines = ["• Gerando imagem..."]
            threading.Thread(target=self._generate_image, args=(prompt,), daemon=True).start()
            return
        if self._is_video_generation_request(prompt):
            self.streaming_text = ""
            self.streaming_bubble = None
            reference_image = next((path for path in attachments if self._is_image_attachment(path)), None)
            self.video_cancel_event = threading.Event()
            video_options = dict(getattr(self, "_next_video_generation_options", {}) or {})
            self._next_video_generation_options = {}
            self.activity_bubble = self.add_chat("Atividade da IA", "• Preparando geração de vídeo...")
            self.activity_lines = ["• Preparando geração de vídeo..."]
            threading.Thread(
                target=self._generate_video,
                args=(prompt, reference_image, video_options, self.video_cancel_event),
                daemon=True,
            ).start()
            return
        editor = self.current_editor()
        direct_conversation = self._is_direct_chat_request(prompt)
        # Perguntas comuns não precisam copiar o documento inteiro antes de
        # abrir o Chat Web; em arquivos grandes isso atrasava a resposta.
        context_parts = [editor.toPlainText()] if editor and not direct_conversation else []
        for path in attachments:
            if not self._is_image_attachment(path):
                try:
                    context_parts.append(f"ANEXO: {path.name}\n{path.read_text(encoding='utf-8', errors='replace')[:900]}")
                except OSError:
                    context_parts.append(f"ANEXO: {path.name} (binario ou indisponivel)")
        image = next((path for path in attachments if self._is_image_attachment(path)), None)
        self.streaming_text = ""
        self.streaming_bubble = None
        self.activity_bubble = self.add_chat("Atividade da IA", "• Preparando a tarefa...")
        self.activity_lines = ["• Preparando a tarefa..."]
        threading.Thread(target=self._generate_reply, args=(prompt, "\n\n".join(context_parts), image), daemon=True).start()

    def request_image_generation(self):
        prompt, accepted = QInputDialog.getMultiLineText(
            self,
            "Gerar imagem",
            "Descreva a imagem que deseja criar:",
        )
        if not accepted or not prompt.strip() or self.chat_busy:
            return
        self.chat_input.setPlainText(f"Gere uma imagem: {prompt.strip()}")
        self.send_chat()

    def request_video_generation(self):
        prompt, accepted = QInputDialog.getMultiLineText(
            self,
            "Gerar vídeo",
            "Descreva o vídeo. Anexe uma imagem antes para usá-la como referência:",
        )
        if not accepted or not prompt.strip() or self.chat_busy:
            return
        aspect_ratio, accepted = QInputDialog.getItem(
            self, "Gerar vídeo", "Formato:", ["16:9", "9:16", "1:1"], 0, False
        )
        if not accepted:
            return
        duration_seconds, accepted = QInputDialog.getInt(
            self, "Gerar vídeo", "Duração (segundos):", 5, 1, 30
        )
        if not accepted:
            return
        quality, accepted = QInputDialog.getItem(
            self, "Gerar vídeo", "Qualidade (o workflow decide como aplicar):", ["standard", "high"], 0, False
        )
        if not accepted:
            return
        self._next_video_generation_options = {
            "aspect_ratio": aspect_ratio,
            "duration_seconds": duration_seconds,
            "quality": quality,
        }
        self.chat_input.setPlainText(f"Gere um vídeo: {prompt.strip()}")
        self.send_chat()

    @staticmethod
    def _is_image_generation_request(prompt):
        text = " ".join(str(prompt or "").casefold().split())
        return bool(re.match(
            r"^(?:gere|gerar|crie|criar|faça|faca|produza|desenhe|imagine)\s+(?:uma\s+)?imagem(?:\s|:|$)",
            text,
        ))

    @staticmethod
    def _is_video_generation_request(prompt):
        text = " ".join(str(prompt or "").casefold().split())
        return bool(re.match(
            r"^(?:gere|gerar|crie|criar|faça|faca|produza|anime|renderize)\s+(?:um\s+)?v[ií]deo(?:\s|:|$)",
            text,
        ))

    def _generate_image(self, prompt):
        try:
            output_dir = self.workspace / ".merotec_system_ai" / "generated_images"
            path = self.engine.generate_image(prompt, output_dir)
            self.image_generation_finished.emit(str(path), "")
        except Exception as exc:
            self.image_generation_finished.emit("", str(exc))

    def finish_image_generation(self, image_path, error):
        self._set_chat_busy(False)
        if error:
            self._append_chat_activity("Geração de imagem indisponível.")
            self.add_chat("Erro", error)
            self.set_status("Pronto")
            return
        path = Path(image_path)
        self._append_chat_activity("Imagem pronta no chat.")
        self.last_response = "Imagem gerada. Use Abrir ou Salvar como... abaixo da prévia."
        self._remember_ai_context_message("Merotec IA", self.last_response)
        self.add_chat("Merotec IA", self.last_response, attachments=[path])
        self.set_status("Imagem gerada.")

    def _generate_video(self, prompt, reference_image, options, cancel_event):
        try:
            clean_prompt = re.sub(
                r"^(?:gere|gerar|crie|criar|faça|faca|produza|anime|renderize)\s+(?:um\s+)?v[ií]deo\s*:\s*",
                "",
                prompt.strip(),
                flags=re.IGNORECASE,
            ).strip() or prompt.strip()
            service = VideoGenerationService(self.settings)
            request = VideoGenerationRequest(
                prompt=clean_prompt,
                reference_image=reference_image,
                aspect_ratio=str(options.get("aspect_ratio") or "16:9"),
                duration_seconds=int(options.get("duration_seconds") or 5),
                quality=str(options.get("quality") or "standard"),
            )
            output_dir = self.workspace / ".merotec_system_ai" / "generated_videos"
            path = service.generate(
                request,
                output_dir,
                progress_callback=lambda message: self.chat_stream.emit(f"[ATIVIDADE] {message}"),
                cancel_event=cancel_event,
            )
            self.video_generation_finished.emit(str(path), "")
        except Exception as exc:
            self.video_generation_finished.emit("", str(exc))

    def finish_video_generation(self, video_path, error):
        self._set_chat_busy(False)
        if error:
            if self.video_cancel_event.is_set() or "cancelada" in error.casefold():
                self._append_chat_activity("Geração de vídeo cancelada.")
            else:
                self._append_chat_activity("Geração de vídeo indisponível.")
                self.add_chat("Erro", error)
            self.set_status("Pronto")
            return
        path = Path(video_path)
        self._append_chat_activity("Vídeo pronto no chat.")
        self.last_response = "Vídeo gerado. Use Reproduzir, Abrir ou Salvar como... abaixo da prévia."
        self._remember_ai_context_message("Merotec IA", self.last_response)
        self.add_chat("Merotec IA", self.last_response, attachments=[path])
        self.set_status("Vídeo gerado.")

    def _generate_reply(self, prompt, context, image_path=None):
        try:
            direct_conversation = self._is_direct_chat_request(prompt)
            continuity_context = self.build_chat_continuity_context(prompt)
            if direct_conversation:
                self.chat_stream.emit("[ATIVIDADE] Preparando conversa direta...")
                # Perguntas e mensagens comuns não devem receber uma árvore de
                # arquivos nem o protocolo de agente; isso fazia o modelo ler
                # o projeto antes de simplesmente responder ao usuário.
                smart_context = "\n\n".join(part for part in (context, continuity_context) if part)
                provider_prompt = "[MEROTEC_DIRECT_CHAT]\n" + prompt
            else:
                self.chat_stream.emit("[ATIVIDADE] Montando o contexto do editor e do projeto...")
                smart_context = "\n\n".join(part for part in [
                    context,
                    self.build_smart_task_brief(prompt, objective=prompt),
                    self.build_project_intelligence_context(),
                    f"Arquivos do workspace:\n{self.get_workspace_tree()}",
                    continuity_context,
                ] if part)
                provider_prompt = prompt
            self.chat_stream.emit("[ATIVIDADE] Enviando a tarefa para o provedor de IA...")
            reply = self.engine.generate_solution(provider_prompt, image_path=str(image_path) if image_path else None, code_context=smart_context, stream_callback=self.chat_stream.emit, workspace_path=self.current_workspace)
            self._deliver_generated_chat_image(
                getattr(self.engine, "latest_generated_image_paths", None)
                or getattr(self.engine, "latest_generated_image_path", "")
            )
        except Exception as exc:
            reply = f"Nao foi possivel consultar o provedor configurado: {exc}"
        self.chat_reply.emit(reply or "Nao recebi uma resposta do provedor configurado.")

    def _deliver_generated_chat_image(self, image_paths):
        """Entrega no chat cada artefato imagegen retornado na resposta atual."""
        if isinstance(image_paths, (str, Path)):
            image_paths = [image_paths]
        delivered = set()
        for image_path in image_paths or []:
            path = Path(image_path) if image_path else None
            if path is None or not path.is_file() or not self._is_image_attachment(path):
                continue
            key = str(path.resolve()).casefold()
            if key in delivered:
                continue
            delivered.add(key)
            self.add_chat_image_message("Merotec IA", path, "Imagem gerada pelo Codex.")

    @staticmethod
    def _is_direct_chat_request(prompt):
        """Separa conversa comum de pedidos que exigem agir no workspace."""
        text = str(prompt or "").strip().lower()
        if not text:
            return True
        project_markers = (
            "arquivo", "codigo", "código", "projeto", "pasta", "bug", "erro",
            "corrig", "implemente", "implementa", "edite", "altere", "modifique",
            "crie", "criar", "teste", "execute", "roda", "terminal", "commit",
            ".py", ".js", ".ts", ".html", ".css", ".json", "/", "\\",
        )
        return not any(marker in text for marker in project_markers)

    @staticmethod
    def _is_chat_continuation_request(prompt):
        """Reconhece pedidos curtos que dependem da tarefa em andamento."""
        normalized = " ".join(str(prompt or "").casefold().split())
        if normalized in {
            "continue", "continua", "continuar", "prossiga", "segue", "siga",
            "continue dai", "continue daqui", "continue de onde parou",
            "termine", "finalize", "conclua", "faca isso", "faça isso", "faz isso",
            "corrija isso", "aplique isso", "tente novamente", "repetir",
        }:
            return True
        return any(marker in normalized for marker in (
            "continue de onde", "continua de onde", "termine a tarefa",
            "finalize a tarefa", "conclua a tarefa", "retome a missao",
            "retome o projeto", "volte ao projeto",
        ))

    def _remember_ai_context_message(self, author, message, max_chars=2200):
        """Mantem somente o historico util que precisa atravessar rodadas."""
        text = " ".join(str(message or "").split()).strip()
        if not text:
            return
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        self.ai_context_memory.append({"author": str(author or "Mensagem"), "text": text})
        self.ai_context_memory = self.ai_context_memory[-24:]
        self.persist_workspace_ai_context_memory()

    def build_chat_continuity_context(self, current_prompt, limit=12, max_chars=6000):
        """Reconstrui o contexto para provedores que nao mantem a thread viva."""
        messages = self.ai_context_memory[-limit:]
        lines = []
        used = 0
        for item in messages:
            line = f"- {item.get('author', 'Mensagem')}: {item.get('text', '')}"
            if not item.get("text"):
                continue
            if used + len(line) > max_chars:
                lines.append("- Historico anterior reduzido pela IDE para caber no contexto.")
                break
            lines.append(line)
            used += len(line)
        if not lines and not self.active_ai_objective:
            return ""
        sections = [
            "CONTINUIDADE DA CONVERSA NA MESMA SESSAO:",
            "A thread do provedor pode ser efemera; trate este bloco como o historico confiavel da conversa atual.",
        ]
        if self.active_ai_objective:
            sections.append(f"Missao ativa mais recente: {self.active_ai_objective}")
        if self.last_response:
            sections.append(f"Ultima resposta da IA: {self.last_response[-1600:]}")
        if lines:
            sections.append("Mensagens recentes:\n" + "\n".join(lines))
        sections.append(
            f"Pedido atual: {current_prompt}\n"
            "Priorize o pedido atual quando ele mudar de assunto; se ele for uma continuidade, use a missao e o historico acima. "
            "Nunca alegue que nao ha conversa anterior sem antes considerar este bloco."
        )
        return "\n\n".join(sections)

    def get_workspace_tree(self, limit=220):
        paths = []
        try:
            for path in self.workspace.rglob("*"):
                relative = path.relative_to(self.workspace)
                if any(is_ignored_dir_name(item) for item in relative.parts[:-1]):
                    continue
                paths.append(relative.as_posix() + ("/" if path.is_dir() else ""))
                if len(paths) >= limit:
                    break
        except OSError:
            return ""
        return "\n".join(paths)

    def iter_workspace_files(self, limit=500):
        count = 0
        for root, directories, filenames in os.walk(self.workspace):
            directories[:] = [name for name in sorted(directories) if not is_ignored_dir_name(name) and not name.startswith(".")]
            for filename in sorted(filenames):
                path = Path(root) / filename
                if filename.startswith(".") or path.suffix.lower() in IGNORED_SUFFIXES:
                    continue
                yield path, path.relative_to(self.workspace)
                count += 1
                if count >= limit:
                    return

    def cancel_ai_task(self):
        if not self.chat_busy:
            return
        if hasattr(self, "video_cancel_event"):
            self.video_cancel_event.set()
        self.engine.cancel_generation()
        self.streaming_bubble = None
        self._set_chat_busy(False)
        self.set_status("Tarefa da IA cancelada")
        self.add_chat("Sistema", "Tarefa da IA cancelada pelo usuario.")

    def append_chat_stream(self, chunk):
        text = str(chunk or "")
        if text.startswith("[STREAM_UPDATE]"):
            partial = text.removeprefix("[STREAM_UPDATE]")
            if not self.streaming_bubble:
                self.streaming_bubble = self.add_chat("Merotec IA", "")
            should_follow = self._chat_scroll_is_at_end()
            self.chat_last_activity = "Recebendo a resposta da IA"
            self.streaming_text = partial
            self.streaming_bubble.label.setText(partial)
            self._follow_chat_scroll_if_needed(should_follow)
            return
        if text.startswith("[TERMINAL_IA]"):
            command = text.removeprefix("[TERMINAL_IA]").strip()
            if command:
                self.append_terminal(f"\n[IA - comando executado] {command}\n")
                self._append_chat_activity(f"Comando enviado ao terminal: {command}")
            return
        if text.startswith("[TERMINAL_IA_OUTPUT]"):
            output = text.removeprefix("[TERMINAL_IA_OUTPUT]").lstrip()
            if output:
                self.append_terminal(f"[IA - saida]\n{output}\n")
            return
        if text.startswith("[ATIVIDADE]"):
            self._append_chat_activity(text.removeprefix("[ATIVIDADE]").strip())
            return
        if not self.streaming_bubble:
            self.streaming_bubble = self.add_chat("Merotec IA", "")
        should_follow = self._chat_scroll_is_at_end()
        self.chat_last_activity = "Recebendo a resposta da IA"
        self.streaming_text += text
        self.streaming_bubble.label.setText(self.streaming_text)
        self._follow_chat_scroll_if_needed(should_follow)

    def _chat_scroll_is_at_end(self):
        """Indica se o usuário está acompanhando a conversa no final."""
        scrollbar = self.chat_scroll.verticalScrollBar()
        return scrollbar.maximum() - scrollbar.value() <= 24

    def _follow_chat_scroll_if_needed(self, should_follow):
        """Mantém o acompanhamento automático sem interromper uma leitura anterior."""
        if should_follow:
            scrollbar = self.chat_scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_chat_activity(self, detail):
        """Exibe etapas do agente sem mistura-las ao texto final da resposta."""
        detail = " ".join(str(detail or "").split())
        if not detail:
            return
        self.chat_last_activity = detail
        line = f"• {detail}"
        if self.activity_lines and self.activity_lines[-1] == line:
            return
        self.activity_lines.append(line)
        self.activity_lines = self.activity_lines[-12:]
        if not self.activity_bubble:
            self.activity_bubble = self.add_chat("Atividade da IA", "")
        should_follow = self._chat_scroll_is_at_end()
        self.activity_bubble.label.setText("\n".join(self.activity_lines))
        self._follow_chat_scroll_if_needed(should_follow)

    @staticmethod
    def _is_unactionable_browser_preamble(reply):
        """Identifica o fallback narrado que nao emite uma acao da IDE."""
        text = " ".join(str(reply or "").casefold().split())
        if not text:
            return False
        has_action = bool(re.search(
            r"\[(?:read|search_text|write|replace|patch|execute|open_url|browser_inspect|"
            r"browser_click|browser_type|browser_scroll|screenshot|human_test)\s*:",
            text,
            re.IGNORECASE,
        ))
        if has_action:
            return False
        mentions_browser = "navegador" in text and (
            "controle do navegador" in text
            or "navegador interno" in text
            or "browser control" in text
        )
        unavailable = any(marker in text for marker in (
            "nao esta disponivel",
            "não está disponível",
            "indisponivel nesta sessao",
            "indisponível nesta sessão",
        ))
        fallback = any(marker in text for marker in (
            "navegador instalado localmente",
            "browser installed locally",
            "validar a renderizacao",
            "validar a renderização",
        ))
        return mentions_browser and (unavailable or fallback)

    def _recover_unactionable_browser_preamble(self, reply):
        """Converte a narracao de fallback em um teste visual local real."""
        if not self._is_unactionable_browser_preamble(reply):
            return False
        if self._chat_agent_round >= 12 or getattr(self, "_visual_test_active", False):
            return False
        self._append_chat_activity(
            "Resposta sem acao de navegador ignorada; iniciando teste visual local."
        )
        if not self.start_human_test("auto"):
            return False
        if self.streaming_bubble:
            self.streaming_bubble.label.setText("Teste visual local em andamento...")
            self.streaming_bubble = None
        return True

    def finish_chat_reply(self, reply):
        self.last_response = reply
        self._remember_ai_context_message("Merotec IA", reply)
        actions = self._apply_agent_reply_actions(reply)
        if actions is not None:
            self._chat_pending_validation_paths.update(
                Path(path).resolve() for path in getattr(actions, "changed_paths", [])
            )
            self._chat_pending_validation_paths.difference_update(
                Path(path).resolve() for path in getattr(actions, "last_validated_paths", [])
            )
            if actions.last_command_requested:
                self._chat_waiting_for_command = True
                self._append_chat_activity("Aguardando o resultado real do EXECUTE no terminal.")
                return
            if actions.last_visual_test_requested:
                self._append_chat_activity("Aguardando a captura do teste visual.")
                if self.streaming_bubble:
                    self.streaming_bubble.label.setText("Teste visual em andamento...")
                    self.streaming_bubble = None
                return
            if actions.last_browser_action_requested:
                self._chat_waiting_for_browser = True
                self._append_chat_activity("Aguardando o resultado real da acao no navegador.")
                return
            if actions.changed_paths and self._start_automatic_validation(actions.changed_paths):
                return
        if self._recover_unactionable_browser_preamble(reply):
            return
        # READ/SEARCH/WRITE/PATCH são passos intermediários. A versão PySide
        # antes parava aqui, então a IA só conseguia ler arquivos. Reenviamos
        # o resultado à mesma conversa para ela concluir a tarefa.
        if (
            actions is not None
            and (
                actions.last_followup_required
                or ("[FINAL:" in str(reply or "").upper() and self._chat_pending_validation_paths)
            )
            and self._chat_agent_round < 12
        ):
            self._chat_agent_round += 1
            observation = "\n\n".join(actions.last_observations)[-18000:]
            if "[FINAL:" in str(reply or "").upper() and self._chat_pending_validation_paths:
                paths = ", ".join(path.relative_to(self.workspace).as_posix() for path in self._chat_pending_validation_paths)
                observation += (
                    "\n\nA alteração foi aplicada, mas ainda falta validação local obrigatória. "
                    f"Responda somente [VALIDATE: {paths.split(', ', 1)[0]}]."
                )
            self._append_chat_activity(
                f"Ação {self._chat_agent_round} aplicada; continuando a tarefa."
            )
            self.streaming_bubble = None
            self.streaming_text = ""
            continuation = (
                "[MEROTEC_AGENT_CONTINUATION]\n"
                f"Tarefa original: {self._chat_task_prompt}\n\n"
                "A IDE executou sua última ação. Use o resultado abaixo para continuar "
                "e responda com a próxima ação necessária ou uma conclusão final.\n\n"
                f"RESULTADO DA IDE:\n{observation}"
            )
            threading.Thread(
                target=self._generate_reply,
                args=(continuation, observation, None),
                daemon=True,
            ).start()
            return

        self._set_chat_busy(False)
        self._append_chat_activity("Resposta recebida; finalizando a tarefa.")
        if self.streaming_bubble:
            self.streaming_bubble.label.setText(reply)
            self.streaming_bubble = None
        else:
            self.add_chat("Merotec IA", reply)
        self.set_status("Pronto")

    def _apply_agent_reply_actions(self, reply):
        actions = QtAgentActions(
            self.workspace,
            self.add_chat,
            self.run_agent_command,
            self.agent_changed_files,
            task_objective=getattr(self, "_chat_task_prompt", ""),
            write_staging=getattr(self, "_chat_write_staging", None),
            on_human_test=self.start_human_test,
            on_browser_action=self.run_agent_browser_action,
        )
        actions.changed_paths = actions.apply(reply)
        return actions

    def _start_automatic_validation(self, changed_paths):
        """Inicia a primeira validacao local apos uma alteracao do agente."""
        paths = {Path(path).resolve() for path in changed_paths if path}
        if not paths:
            return False
        command = self.validation_command_for_changed_paths(
            [str(path) for path in sorted(paths)],
            getattr(self, "_chat_task_prompt", ""),
        )
        if not command:
            return False
        self._chat_auto_validation_paths = paths
        self._chat_waiting_for_command = True
        self._append_chat_activity("Alteracao aplicada; iniciando a primeira validacao local automatica.")
        if self.run_agent_command(command):
            return True
        self._chat_waiting_for_command = False
        self._chat_auto_validation_paths = set()
        self.add_chat("Erro", "A primeira validacao automatica nao conseguiu iniciar no Terminal Local.")
        return False

    def toggle_voice_capture(self):
        if not self.voice_capture_active:
            self.voice_capture_active = True
            self.voice_button.setProperty("recording", True)
            self._set_button_state(self.voice_button, active=True)
            self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
            self.voice_button.setToolTip("Parar gravacao e enviar")
            self.set_status("Gravando comando de voz...")
            QTimer.singleShot(0, self._start_voice_recording)
            return
        self.voice_capture_active = False
        self.voice_button.setProperty("recording", False)
        self._set_button_state(self.voice_button, busy=True)
        self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.voice_button.setToolTip("Gravar comando de voz")
        self.set_status("Transcrevendo audio...")
        threading.Thread(target=self._transcribe_voice, daemon=True).start()

    def _start_voice_recording(self):
        try:
            self.voice.start_recording()
        except Exception as exc:
            self.voice_capture_active = False
            self.voice_button.setProperty("recording", False)
            self._set_button_state(self.voice_button, active=False, busy=False)
            self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
            self.voice_button.setToolTip("Gravar comando de voz")
            self.set_status("Gravacao indisponivel")
            self.add_chat("Erro", f"Nao foi possivel iniciar a gravacao: {exc}")

    def _transcribe_voice(self):
        try:
            text = self.voice.stop_recording_and_transcribe()
        except Exception as exc:
            def restore_button():
                self._set_button_state(self.voice_button, active=False, busy=False)
                self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
            self.ui_bridge.call_soon(restore_button)
            self.ui_bridge.call_soon(lambda: self.add_chat("Erro", f"Falha na transcricao: {exc}"))
            return
        def completed():
            self._set_button_state(self.voice_button, active=False, busy=False)
            self.voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
            if text:
                self.chat_input.setPlainText(text)
                self.send_chat()
            else:
                self.add_chat("Sistema", "Nenhum comando de voz reconhecido.")
            self.set_status("Pronto")
        self.ui_bridge.call_soon(completed)

    def play_last_response(self):
        if self.speech_active:
            self.stop_speech_playback()
            return
        text = getattr(self, "last_response", "").strip()
        if not text:
            self.add_chat("Sistema", "Nenhuma resposta para ler ainda.")
            return
        self.speech_active = True
        self.speak_button.setProperty("stop", True)
        self._set_button_state(self.speak_button, active=True, busy=True)
        self.speak_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.speak_button.setToolTip("Parar leitura")
        self.set_status("Lendo ultima resposta...")
        if not self.voice.speak(text):
            self.finish_speech_playback()
            self.add_chat("Sistema", "Leitura por voz indisponivel nesta instalacao.")
            return
        self._watch_speech_playback()

    def _watch_speech_playback(self):
        if self.speech_active and self.voice.is_speaking:
            QTimer.singleShot(120, self._watch_speech_playback)
            return
        self.finish_speech_playback()

    def stop_speech_playback(self):
        self.voice.stop()
        self.finish_speech_playback("Leitura interrompida")

    def finish_speech_playback(self, status_text="Pronto"):
        if not self.speech_active:
            return
        self.speech_active = False
        self.speak_button.setProperty("stop", False)
        self._set_button_state(self.speak_button, active=False, busy=False)
        self.speak_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.speak_button.setToolTip("Ler ultima resposta")
        self.set_status(status_text)

    def run_agent_command(self, command):
        if self.terminal_process and self.terminal_process.state() != QProcess.ProcessState.NotRunning:
            self.add_chat("Sistema", "Comando da IA nao executado: o terminal ja esta ocupado.")
            return False
        self.append_terminal(f"\n[IA - comando executado] {command}\n")
        self._append_chat_activity(f"Comando enviado ao terminal: {command}")
        self._agent_command_pending = command
        self._agent_command_output = ""
        started = self._start_shell_command(command, working_directory=self.workspace)
        if not started:
            self._agent_command_pending = ""
        return started

    def _continue_agent_after_command(self, command, succeeded, exit_code, output):
        """Entrega ao agente o resultado real de um EXECUTE antes de encerrar a tarefa."""
        if not getattr(self, "_chat_waiting_for_command", False) or not self.chat_busy:
            return
        if self._chat_agent_round >= 12:
            self._chat_waiting_for_command = False
            self._set_chat_busy(False)
            self.add_chat("Erro", "Limite de ciclos do agente atingido após EXECUTE; revise a saída do terminal.")
            return
        self._chat_waiting_for_command = False
        self._chat_agent_round += 1
        automatic_paths = set(getattr(self, "_chat_auto_validation_paths", set()))
        self._chat_auto_validation_paths = set()
        if succeeded and automatic_paths:
            self._chat_pending_validation_paths.difference_update(automatic_paths)
        status = "sucesso" if succeeded else f"falha (código {exit_code})"
        observation = (
            f"EXECUTE concluído com {status}.\nCOMANDO: {command}\n"
            f"SAÍDA:\n{str(output or '(sem saída)').strip()[-12000:]}"
        )
        if automatic_paths:
            if succeeded:
                observation += "\n\nVALIDACAO AUTOMATICA APROVADA PARA OS ARQUIVOS ALTERADOS."
            else:
                observation += "\n\nA VALIDACAO AUTOMATICA FALHOU; corrija a causa antes de concluir."
        self._append_chat_activity(f"EXECUTE terminou; continuando a tarefa ({self._chat_agent_round}/12).")
        continuation = (
            "[MEROTEC_AGENT_CONTINUATION]\n"
            f"Tarefa original: {self._chat_task_prompt}\n\n"
            "A IDE executou o comando solicitado. Use o resultado para corrigir, validar ou concluir a tarefa.\n\n"
            f"RESULTADO DA IDE:\n{observation}"
        )
        threading.Thread(
            target=self._generate_reply,
            args=(continuation, observation, None),
            daemon=True,
        ).start()

    def agent_changed_files(self, paths):
        self.refresh_tree()
        for path in paths:
            target = Path(path).resolve()
            for index, opened in self.paths_by_tab.items():
                if opened != target:
                    continue
                editor = self.tabs.widget(index)
                if isinstance(editor, CodeEditor):
                    try:
                        cursor_position = editor.textCursor().position()
                        editor.blockSignals(True)
                        editor.setPlainText(target.read_text(encoding="utf-8", errors="replace"))
                        cursor = editor.textCursor()
                        cursor.setPosition(min(cursor_position, len(editor.toPlainText())))
                        editor.setTextCursor(cursor)
                        self.tabs.setTabText(index, target.name)
                    finally:
                        editor.blockSignals(False)

    def update_cursor(self):
        editor = self.current_editor()
        if editor:
            cursor = editor.textCursor()
            self.cursor.setText(f"Ln {cursor.blockNumber() + 1}, Col {cursor.columnNumber() + 1}")

    def find_in_current_editor(self):
        editor = self.current_editor()
        if not editor:
            return
        initial = getattr(self, "last_find_text", editor.textCursor().selectedText())
        text, accepted = QInputDialog.getText(self, "Localizar", "Texto:", text=initial)
        if accepted and text:
            self.last_find_text = text
            self._find_in_editor(False)

    def find_next(self):
        if getattr(self, "last_find_text", ""):
            self._find_in_editor(False)
        else:
            self.find_in_current_editor()

    def _find_in_editor(self, backwards):
        editor = self.current_editor()
        if not editor:
            return
        flags = QTextDocument.FindFlag.FindBackward if backwards else QTextDocument.FindFlag(0)
        if not editor.find(self.last_find_text, flags):
            cursor = editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End if backwards else cursor.MoveOperation.Start)
            editor.setTextCursor(cursor)
            editor.find(self.last_find_text, flags)

    def toggle_current_comment(self):
        editor = self.current_editor()
        if editor:
            editor.toggle_comment()

    def show_symbol_palette(self):
        editor = self.current_editor()
        if not editor:
            return
        import re
        symbols = []
        for number, line in enumerate(editor.toPlainText().splitlines()):
            match = re.match(r"\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", line)
            if match:
                symbols.append((f"{match.group(1)}  (linha {number + 1})", number))
        if not symbols:
            self.set_status("Nenhum simbolo encontrado")
            return
        labels = [item[0] for item in symbols]
        choice, accepted = QInputDialog.getItem(self, "Simbolos", "Ir para:", labels, 0, False)
        if accepted:
            number = dict(symbols)[choice]
            cursor = editor.textCursor()
            cursor.setPosition(editor.document().findBlockByNumber(number).position())
            editor.setTextCursor(cursor)
            editor.setFocus()

    def zoom_editor(self, delta):
        editor = self.current_editor()
        if not editor:
            return
        font = editor.font()
        size = 11 if delta == 0 else max(8, min(24, font.pointSize() + delta))
        font.setPointSize(size)
        editor.setFont(font)
        editor._update_line_number_area_width(0)

    def toggle_explorer(self):
        explorer = self.main_splitter.widget(1)
        explorer.setVisible(not explorer.isVisible())

    def focus_explorer(self):
        self.search.setFocus()

    def focus_search(self):
        self.search.setFocus()

    def show_settings_hint(self):
        dialog = QtSettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings_to_environment()
            self._save_settings()
            self.engine = UniversalEngine()
            self.attach_internal_web_chat_bridge()
            self.provider_label.setText(self.engine.provider)
            self.set_status("Configuracoes salvas.")

    def launch_codex_login(self):
        """Abre a autenticacao da conta Codex selecionada no Windows."""
        if self.codex_login_started:
            self.set_status("Login do Codex ja esta aberto.")
            return

        executable = self.engine._find_codex_executable()
        if not executable:
            QMessageBox.warning(
                self,
                "Codex nao encontrado",
                "Instale o Codex CLI para entrar nesta sessao e tente novamente.",
            )
            return

        if self.engine._codex_is_logged_in(executable):
            self.set_status("A conta Codex ja esta conectada.")
            return

        escaped_executable = str(executable).replace("'", "''")
        command = (
            f"& '{escaped_executable}' login; "
            f"& '{escaped_executable}' login status; "
            "Write-Host ''; "
            "Write-Host 'Quando o login terminar, feche esta janela e volte para a Merotec IA IDE.'"
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NoExit", "-Command", command],
                cwd=self.current_workspace,
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Merotec IA", f"Nao foi possivel abrir o login do Codex.\n{exc}")
            return

        self.codex_login_started = True
        self.set_status("Login do Codex aberto.")
        self.add_chat("Sistema", "Conclua o login do Codex na janela aberta. A sessao sera usada pela IDE.")
        QTimer.singleShot(4000, self._refresh_codex_session_after_login)

    def _refresh_codex_session_after_login(self):
        executable = self.engine._find_codex_executable()
        if executable and self.engine._codex_is_logged_in(executable):
            self.engine = UniversalEngine()
            self.attach_internal_web_chat_bridge()
            self.provider_label.setText(self.engine.provider)
            self.codex_login_started = False
            self.set_status("Codex conectado.")
            self.add_chat("Sistema", "Sessao Codex conectada e pronta para uso.")
            return
        self.set_status("Conclua o login do Codex na janela aberta.")


STYLE = """
QMainWindow, QWidget#root { background: #0a1421; color: #d5deeb; font-family: 'Segoe UI'; font-size: 14px; }
QMenuBar { background: #0d1927; border-bottom: 1px solid #223347; padding: 4px 10px; color: #ced8e6; }
QMenuBar::item { padding: 8px 12px; } QMenuBar::item:selected, QMenu::item:selected { background: #213247; }
QMenu { background: #101d2c; border: 1px solid #2a3c52; color: #d5deeb; } QMenu::item { padding: 8px 28px; }
QScrollBar:vertical { background: #0c1826; width: 10px; margin: 0; border: 0; }
QScrollBar::handle:vertical { background: #365d79; min-height: 38px; border: 2px solid #0c1826; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #4f8caf; }
QScrollBar::handle:vertical:pressed { background: #20bddd; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical, QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { background: transparent; height: 0; }
QScrollBar:horizontal { background: #0c1826; height: 10px; margin: 0; border: 0; }
QScrollBar::handle:horizontal { background: #365d79; min-width: 38px; border: 2px solid #0c1826; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #4f8caf; }
QScrollBar::handle:horizontal:pressed { background: #20bddd; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { background: transparent; width: 0; }
QToolBar#toolbar { background: #0d1927; border: 0; border-bottom: 1px solid #223347; spacing: 5px; padding: 5px 12px; }
QToolButton { color: #eef8ff; border: 1px solid #285a79; border-radius: 4px; background: #1d455f; padding: 6px; } QToolButton:hover { background: #2c7097; border-color: #86d5ed; } QToolButton:pressed { background: #2387a6; border-color: #b3edf8; color: #ffffff; }
QSplitter::handle { background: #223347; } QSplitter::handle:hover { background: #2f607a; }
QFrame#activityBar, QFrame#explorer, QFrame#chatPanel { background: #0c1826; border-right: 1px solid #26384c; }
QFrame#chatPanel { border-right: 0; border-left: 1px solid #26384c; }
QPushButton#activityButton { min-width: 42px; max-width: 42px; min-height: 42px; border: 0; border-radius: 4px; background: transparent; color: #dce8f6; } QPushButton#activityButton:hover { background: #183449; color: #ffffff; } QPushButton#activityButton:pressed, QPushButton#activityButton[active="true"], QPushButton#activityButton[busy="true"] { background: #1d5b78; color: #ffffff; }
QLabel#panelTitle, QLabel#terminalTitle { color: #dbe6f5; font-weight: 700; font-size: 15px; } QLabel#explorerRoot { color: #dbe6f5; background: #122235; border: 1px solid #2b4057; border-radius: 4px; padding: 6px 8px; font-weight: 600; } QLineEdit#search { background: #122235; border: 1px solid #2b4057; border-radius: 4px; padding: 7px; color: #dce8f6; }
QTreeView#fileTree { background: transparent; border: 0; color: #c6d1df; padding: 3px; } QTreeView#fileTree::item { padding: 5px; border-radius: 4px; } QTreeView#fileTree::item:selected { background: #24344b; color: white; }
QPushButton#tinyButton { background: transparent; border: 0; color: #c9d9ea; font-size: 21px; } QPushButton#tinyButton:hover { color: #27d7f0; }
QTabWidget#editorTabs::pane { border: 0; } QTabBar::tab { background: #0d1927; color: #b5c3d3; padding: 10px 18px; border-right: 1px solid #223347; min-width: 105px; } QTabBar::tab:hover { background: #17334a; color: #edf8ff; } QTabBar::tab:selected { background: #173b56; color: #eef6ff; border-top: 2px solid #20cbea; } QTabBar::close-button { background: #29445b; border: 1px solid #416881; border-radius: 4px; margin: 3px; } QTabBar::close-button:hover { background: #a63e50; border-color: #f28b99; } QTabBar::close-button:pressed { background: #d05064; border-color: #ffd0d6; }
QPlainTextEdit#editor { background: #0c1725; color: #d9e2ed; border: 0; padding: 10px; selection-background-color: #294a65; }
QFrame#terminalPanel { background: #0a1420; border-top: 1px solid #26384c; } QLabel#terminalProgress { color: #79d8e9; padding-left: 12px; } QProgressBar#terminalProgressBar { background: #11263a; border: 0; } QProgressBar#terminalProgressBar::chunk { background: #20cbe8; } QPlainTextEdit#terminal { background: #09131f; border: 0; border-top: 1px solid #203349; color: #bdc9d9; padding: 10px; } QLineEdit#terminalInput { background: #0b1725; border: 1px solid #203349; color: #dce8f6; padding: 8px 12px; } QPushButton#terminalAction { background: transparent; border: 0; color: #a5b8cc; padding: 4px 9px; } QPushButton#terminalAction:hover { color: #21d0eb; }
QLabel#chatTitle { font-weight: 700; font-size: 17px; color: #eef5ff; } QLabel#provider { color: #68cfea; font-size: 11px; } QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget > QWidget { border: 0; background: #0c1826; } QScrollArea#chatScroll QScrollBar:vertical { background: #0c1826; width: 10px; } QScrollArea#chatScroll QScrollBar::handle:vertical { background: #365d79; border: 2px solid #0c1826; border-radius: 5px; min-height: 38px; } QScrollArea#chatScroll QScrollBar::handle:vertical:hover { background: #4f8caf; } QFrame#chatIncoming, QFrame#chatOutgoing { border-radius: 8px; max-width: 300px; } QFrame#chatIncoming { background: #182637; } QFrame#chatOutgoing { background: #164a75; } QLabel#chatText { color: #e1ebf6; } QLabel#chatMeta { color: #8fa2b7; font-size: 11px; }
QFrame#attachmentPanel { background: #102238; border: 1px solid #2e5e7b; border-radius: 6px; }
QLabel#chatImagePreview { background: #091522; border: 1px solid #355d78; border-radius: 5px; padding: 3px; } QPushButton#chatImageAction { background: #214963; border: 1px solid #3b7597; border-radius: 4px; color: #edf8ff; padding: 4px 8px; } QPushButton#chatImageAction:hover { background: #2c6d90; border-color: #94dff1; } QPushButton#imageButton { min-width: 55px; min-height: 36px; border: 1px solid #2f6687; border-radius: 4px; background: #24536f; color: #eef8ff; padding: 0 7px; } QPushButton#imageButton:hover { background: #31749a; border-color: #79cdeb; } QPushButton#imageButton:disabled { background: #1d3446; border-color: #2a4a61; color: #8298ac; }
QLabel#attachmentLabel { color: #9be7f6; font-weight: 600; }
QFrame#attachmentItem { background: #0d1b2c; border: 1px solid #264863; border-radius: 5px; }
QLabel#attachmentPreview { background: #081421; border: 1px solid #345b78; border-radius: 4px; color: #9bc4d8; font-size: 10px; font-weight: 700; }
QLabel#attachmentName { color: #edf6ff; font-size: 12px; }
QLabel#attachmentDetail { color: #82a9bd; font-size: 11px; }
QPushButton#attachmentClearButton { background: transparent; border: 0; color: #8fd8ef; padding: 3px 8px; }
QPushButton#attachmentClearButton:hover, QPushButton#attachmentClearButton:pressed { color: #ffffff; background: #1d425e; border-radius: 4px; }
QPushButton#attachmentRemoveButton { min-width: 24px; max-width: 24px; min-height: 24px; border: 0; border-radius: 4px; background: transparent; color: #aebfd1; }
QPushButton#attachmentRemoveButton:hover, QPushButton#attachmentRemoveButton:pressed { background: #8d2936; color: #ffffff; }
QPushButton#attachButton { min-width: 36px; max-width: 36px; min-height: 36px; border: 1px solid #2f6687; border-radius: 4px; background: #24536f; color: #eef8ff; } QPushButton#attachButton:hover { background: #31749a; border-color: #79cdeb; } QPushButton#attachButton:pressed, QPushButton#attachButton[active="true"], QPushButton#attachButton[busy="true"] { background: #2386a5; border-color: #a0e6f5; color: #ffffff; } QPushButton#attachButton[recording="true"], QPushButton#attachButton[stop="true"] { background: #943747; border-color: #f08a98; color: #ffffff; } QPushButton#attachButton[recording="true"]:hover, QPushButton#attachButton[stop="true"]:hover { background: #b34859; } QPushButton#attachButton:disabled { background: #1d3446; border-color: #2a4a61; color: #8298ac; }
QPlainTextEdit#chatInput { background: #101f30; border: 1px solid #29425b; border-radius: 7px; color: #edf5ff; padding: 7px; } QPlainTextEdit#chatInput:disabled { background: #0b1624; color: #7d8fa2; }
QPushButton#sendButton { min-width: 42px; max-width: 42px; min-height: 42px; border: 1px solid #3a86aa; border-radius: 21px; background: #1f6086; color: #f3fbff; } QPushButton#sendButton:hover { background: #2b7faa; border-color: #91dff2; } QPushButton#sendButton:pressed, QPushButton#sendButton[busy="true"] { background: #2390ad; border-color: #b2edf8; } QPushButton#sendButton:disabled { background: #1b3346; border-color: #2d526a; color: #7f97aa; }
QFrame#statusbar { background: #0c1826; border-top: 1px solid #26384c; } QLabel#readyStatus { color: #e1edf8; } QLabel#readyStatus::first-letter { color: #35bf70; } QLabel#quotaStatus { color: #8ed7e8; padding: 0 16px; } QLabel#agentStatus { color: #c1d2e3; padding-right: 20px; } QLabel#statusText { color: #c7d6e5; padding: 0 18px; border-left: 1px solid #31485e; } QStatusBar { background: #0c1826; }
"""


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("Merotec IA IDE")
    app.setStyleSheet(STYLE)
    window = MerotecIDE()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())

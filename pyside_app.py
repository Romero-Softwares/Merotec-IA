"""Interface PySide6 da Merotec IA IDE.

O nucleo da aplicacao continua nos modulos existentes; esta camada substitui a
janela Tk por uma superficie desktop inspirada no mockup da IDE.
"""

from __future__ import annotations

import locale
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDir, QFileInfo, QProcess, QProcessEnvironment, QSortFilterProxyModel, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QStyle, QTabWidget, QTextEdit, QToolBar, QTreeView, QProgressBar,
    QVBoxLayout, QWidget, QFileSystemModel, QScrollArea,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl

from modules.engine import UniversalEngine
from modules.executor import CodeExecutor
from modules.qt_ui_bridge import QtUiBridge
from modules.app_constants import APP_CHANGE_HISTORY_FILE, APP_HISTORY_FILE, APP_SETTINGS_FILE, PROJECTS_DIR, IGNORED_SUFFIXES, is_ignored_dir_name
from modules.app_state import AppStateMixin
from modules.workspace_intelligence import WorkspaceIntelligenceMixin
from modules.memory import MemorySubnet
from modules.qt_settings_dialog import QtSettingsDialog
from modules.qt_agent_actions import QtAgentActions
from modules.project_manager import ProjectManager
from modules.voice import VoiceModule
from modules.plugin_manager import build_plugin_report_messages, initialize_plugins


ROOT = Path(__file__).resolve().parent
ACCENT = "#18c9e8"


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
    """Compositor que converte prints colados em anexos da conversa."""

    def __init__(self, on_image_paste, parent=None):
        super().__init__(parent)
        self._on_image_paste = on_image_paste

    def insertFromMimeData(self, source):
        if source is not None and source.hasImage() and self._on_image_paste():
            return
        super().insertFromMimeData(source)


class ChatBubble(QFrame):
    def __init__(self, author: str, message: str, outgoing=False):
        super().__init__()
        self.setObjectName("chatOutgoing" if outgoing else "chatIncoming")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setObjectName("chatText")
        layout.addWidget(self.label)
        sender = QLabel(author)
        sender.setObjectName("chatMeta")
        layout.addWidget(sender)


class MerotecIDE(AppStateMixin, WorkspaceIntelligenceMixin, QMainWindow):
    chat_reply = Signal(str)
    chat_stream = Signal(str)

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
        self.chat_busy = False
        self.chat_started_at = None
        self.chat_last_activity = ""
        self.speech_active = False
        self.streaming_bubble = None
        self.streaming_text = ""
        self.activity_bubble = None
        self.activity_lines = []
        self.browser_view = None
        self.internal_browser_url = "about:blank"
        self.paths_by_tab = {}
        self.setWindowTitle("Merotec IA IDE")
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

    def set_status(self, text, mode="info"):
        if hasattr(self, "status"):
            self.status.setText(f"●  {text}")
            self.refresh_quota_status()

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
        self.status.setText(f"â—  IA: {detail} ({elapsed}s)")

    def log_agent(self, text):
        # Durante a migracao, o terminal e o registro visivel da atividade.
        if hasattr(self, "terminal"):
            self.append_terminal(f"[Agente] {text}\n")

    def add_chat_message(self, sender, text):
        self.ui_bridge.call_soon(lambda: self.add_chat(sender, text, sender.lower() in {"voce", "você"}))

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
        settings.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        settings.setToolTip("Configuracoes")
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
        layout.addWidget(self.tree, 1)
        return panel

    def _update_workspace_root_label(self):
        if not hasattr(self, "workspace_root_label"):
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
        self.attach_button.setToolTip("Anexar arquivos")
        self.attach_button.clicked.connect(self.add_attachments)
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
        self.chat_input = ChatInput(self.add_clipboard_image)
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
        agent = QLabel("◉  Agente IA: ativo")
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

    # Contrato de agendamento usado pela migracao dos mixins para Qt.
    def after(self, milliseconds, callback):
        return self.ui_bridge.after(milliseconds, callback)

    def after_cancel(self, token):
        self.ui_bridge.after_cancel(token)

    def _open_initial_file(self):
        """Exibe orientacoes iniciais sem expor o codigo-fonte da propria IDE."""
        editor = CodeEditor()
        editor.setReadOnly(True)
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

    def open_internal_browser(self, url, source="usuario"):
        if self.browser_view is None:
            self.browser_view = QWebEngineView()
            self.browser_view.setObjectName("internalBrowser")
            self.browser_view.urlChanged.connect(lambda value: setattr(self, "internal_browser_url", value.toString()))
            index = self.tabs.addTab(self.browser_view, "Navegador")
            self.tabs.setCurrentIndex(index)
        else:
            self.tabs.setCurrentWidget(self.browser_view)
        target = QUrl.fromUserInput(str(url))
        self.browser_view.load(target)
        self.internal_browser_url = target.toString()
        self.set_status(f"Navegador: {source}")
        return self.internal_browser_url

    def request_internal_browser_action(self, action, payload=None, callback=None):
        if self.browser_view is None:
            return None
        payload = payload or {}
        target = str(payload.get("target", ""))
        value = str(payload.get("value", "")).replace("\\", "\\\\").replace("'", "\\'")
        if action == "inspect":
            script = """(() => ({url: location.href, title: document.title, text: document.body.innerText.slice(0,12000), elements: [...document.querySelectorAll('a,button,input,textarea,select')].slice(0,120).map((e,i)=>({ref:'e'+i,tag:e.tagName.toLowerCase(),label:e.innerText||e.getAttribute('aria-label')||e.name||e.placeholder||'',href:e.href||''}))}))()"""
        elif action == "scroll":
            script = f"window.scrollBy(0, {'-600' if target == 'up' else '600'}); ({'{'}url:location.href{'}'})"
        else:
            ref = target.replace("e", "")
            selector = f"[...document.querySelectorAll('a,button,input,textarea,select')][{ref}]"
            if action == "click":
                script = f"(() => {{ const e={selector}; if(!e) return {{error:'elemento nao encontrado'}}; e.click(); return {{url:location.href}}; }})()"
            elif action == "type":
                script = f"(() => {{ const e={selector}; if(!e) return {{error:'elemento nao encontrado'}}; e.focus(); e.value='{value}'; e.dispatchEvent(new Event('input',{{bubbles:true}})); return {{url:location.href}}; }})()"
            else:
                return None
        self.browser_view.page().runJavaScript(script, lambda result: callback({"result": result}) if callback else None)
        return action

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

    def open_workspace(self, folder):
        self.workspace = Path(folder).resolve()
        self.terminal_working_directory = self.workspace
        self.current_workspace = str(self.workspace)
        self.memory_subnet.reset_workspace(self.workspace)
        self.settings["last_workspace"] = self.current_workspace
        self.settings["recent_projects"] = [self.current_workspace, *[item for item in self.settings.get("recent_projects", []) if item != self.current_workspace]][:10]
        self._save_settings()
        self.update_recent_menu()
        self._update_workspace_root_label()
        self.model.setRootPath(self.current_workspace)
        self.tree.setRootIndex(self.file_filter.mapFromSource(self.model.index(self.current_workspace)))
        self.append_terminal(f"\nPasta aberta: {self.workspace}\n")
        self._terminal_prompt()

    def refresh_tree(self):
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

    def _start_shell_command(self, command):
        program, arguments = self._shell_command(self._make_python_output_unbuffered(command))
        self.start_terminal_process(program, arguments, command)

    @staticmethod
    def _make_python_output_unbuffered(command):
        """Evita que Python/PyInstaller retenha logs quando a saída é um pipe."""
        pattern = r"^(\s*(?:python(?:\.exe)?|py)(?:\s+-\d+(?:\.\d+)*)?)(?!\s+-u\b)(?=\s)"
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
        self.append_terminal(self._decode_process_output(data))

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

    def _terminal_finished(self, process, exit_code, exit_status):
        if process is not self.terminal_process:
            return
        self._read_terminal_stdout(process)
        self._read_terminal_stderr(process)
        succeeded = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        was_cancelled = self._terminal_cancel_requested
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

    def add_chat(self, author, message, outgoing=False):
        bubble = ChatBubble(author, message, outgoing)
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
        self.chat_input.clear()
        self.add_chat("Voce", prompt, True)
        if attachments:
            self.add_chat("Voce", "Arquivos anexados: " + ", ".join(path.name for path in attachments), True)
            self.clear_attachments()
        self.set_status("Merotec IA pensando...")
        editor = self.current_editor()
        context_parts = [editor.toPlainText()] if editor else []
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

    def _generate_reply(self, prompt, context, image_path=None):
        try:
            self.chat_stream.emit("[ATIVIDADE] Montando o contexto do editor e do projeto...")
            smart_context = "\n\n".join(part for part in [
                context,
                self.build_smart_task_brief(prompt, objective=prompt),
                self.build_project_intelligence_context(),
                f"Arquivos do workspace:\n{self.get_workspace_tree()}",
            ] if part)
            self.chat_stream.emit("[ATIVIDADE] Enviando a tarefa para o provedor de IA...")
            reply = self.engine.generate_solution(prompt, image_path=str(image_path) if image_path else None, code_context=smart_context, stream_callback=self.chat_stream.emit, workspace_path=self.current_workspace)
        except Exception as exc:
            reply = f"Nao foi possivel consultar o provedor configurado: {exc}"
        self.chat_reply.emit(reply or "Nao recebi uma resposta do provedor configurado.")

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
        self.engine.cancel_generation()
        self.streaming_bubble = None
        self._set_chat_busy(False)
        self.set_status("Tarefa da IA cancelada")
        self.add_chat("Sistema", "Tarefa da IA cancelada pelo usuario.")

    def append_chat_stream(self, chunk):
        text = str(chunk or "")
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
        self.chat_last_activity = "Recebendo a resposta da IA"
        self.streaming_text += text
        self.streaming_bubble.label.setText(self.streaming_text)
        self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum())

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
        self.activity_bubble.label.setText("\n".join(self.activity_lines))
        self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum())

    def finish_chat_reply(self, reply):
        self.last_response = reply
        self._set_chat_busy(False)
        self._append_chat_activity("Resposta recebida; finalizando a tarefa.")
        if self.streaming_bubble:
            self.streaming_bubble.label.setText(reply)
            self.streaming_bubble = None
        else:
            self.add_chat("Merotec IA", reply)
        # Atualiza a interface antes de processar PATCH/EXECUTE da resposta,
        # pois essas acoes podem levar alguns segundos no thread principal.
        self.set_status("Pronto")
        QTimer.singleShot(0, lambda text=reply: self._apply_agent_reply_actions(text))

    def _apply_agent_reply_actions(self, reply):
        actions = QtAgentActions(self.workspace, self.add_chat, self.run_agent_command, self.agent_changed_files)
        actions.apply(reply)

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
            return
        self.append_terminal(f"\n[IA - comando executado] {command}\n")
        self._append_chat_activity(f"Comando enviado ao terminal: {command}")
        self._start_shell_command(command)

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
            self.engine = UniversalEngine()
            self.provider_label.setText(self.engine.provider)
            self._save_settings()
            self.set_status("Configuracoes salvas.")


STYLE = """
QMainWindow, QWidget#root { background: #0a1421; color: #d5deeb; font-family: 'Segoe UI'; font-size: 14px; }
QMenuBar { background: #0d1927; border-bottom: 1px solid #223347; padding: 4px 10px; color: #ced8e6; }
QMenuBar::item { padding: 8px 12px; } QMenuBar::item:selected, QMenu::item:selected { background: #213247; }
QMenu { background: #101d2c; border: 1px solid #2a3c52; color: #d5deeb; } QMenu::item { padding: 8px 28px; }
QScrollBar:vertical { background: #132338; width: 13px; margin: 0; border-left: 1px solid #29425d; }
QScrollBar::handle:vertical { background: #4f7897; min-height: 36px; border-radius: 5px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #6ca3c6; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: transparent; }
QScrollBar:horizontal { background: #132338; height: 13px; margin: 0; border-top: 1px solid #29425d; }
QScrollBar::handle:horizontal { background: #4f7897; min-width: 36px; border-radius: 5px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: #6ca3c6; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; background: transparent; }
QToolBar#toolbar { background: #0d1927; border: 0; border-bottom: 1px solid #223347; spacing: 5px; padding: 5px 12px; }
QToolButton { color: #eef8ff; border: 1px solid #285a79; border-radius: 4px; background: #1d455f; padding: 6px; } QToolButton:hover { background: #2c7097; border-color: #86d5ed; } QToolButton:pressed { background: #2387a6; border-color: #b3edf8; color: #ffffff; }
QSplitter::handle { background: #223347; } QSplitter::handle:hover { background: #2f607a; }
QFrame#activityBar, QFrame#explorer, QFrame#chatPanel { background: #0c1826; border-right: 1px solid #26384c; }
QFrame#chatPanel { border-right: 0; border-left: 1px solid #26384c; }
QPushButton#activityButton { min-width: 42px; max-width: 42px; min-height: 42px; border: 0; border-radius: 4px; background: transparent; } QPushButton#activityButton:hover { background: #183449; } QPushButton#activityButton:pressed, QPushButton#activityButton[active="true"], QPushButton#activityButton[busy="true"] { background: #1d5b78; color: #ffffff; }
QLabel#panelTitle, QLabel#terminalTitle { color: #dbe6f5; font-weight: 700; font-size: 15px; } QLabel#explorerRoot { color: #dbe6f5; background: #122235; border: 1px solid #2b4057; border-radius: 4px; padding: 6px 8px; font-weight: 600; } QLineEdit#search { background: #122235; border: 1px solid #2b4057; border-radius: 4px; padding: 7px; color: #dce8f6; }
QTreeView#fileTree { background: transparent; border: 0; color: #c6d1df; padding: 3px; } QTreeView#fileTree::item { padding: 5px; border-radius: 4px; } QTreeView#fileTree::item:selected { background: #24344b; color: white; }
QPushButton#tinyButton { background: transparent; border: 0; color: #c9d9ea; font-size: 21px; } QPushButton#tinyButton:hover { color: #27d7f0; }
QTabWidget#editorTabs::pane { border: 0; } QTabBar::tab { background: #0d1927; color: #b5c3d3; padding: 10px 18px; border-right: 1px solid #223347; min-width: 105px; } QTabBar::tab:hover { background: #17334a; color: #edf8ff; } QTabBar::tab:selected { background: #173b56; color: #eef6ff; border-top: 2px solid #20cbea; } QTabBar::close-button { background: #29445b; border: 1px solid #416881; border-radius: 4px; margin: 3px; } QTabBar::close-button:hover { background: #a63e50; border-color: #f28b99; } QTabBar::close-button:pressed { background: #d05064; border-color: #ffd0d6; }
QPlainTextEdit#editor { background: #0c1725; color: #d9e2ed; border: 0; padding: 10px; selection-background-color: #294a65; }
QFrame#terminalPanel { background: #0a1420; border-top: 1px solid #26384c; } QLabel#terminalProgress { color: #79d8e9; padding-left: 12px; } QProgressBar#terminalProgressBar { background: #11263a; border: 0; } QProgressBar#terminalProgressBar::chunk { background: #20cbe8; } QPlainTextEdit#terminal { background: #09131f; border: 0; border-top: 1px solid #203349; color: #bdc9d9; padding: 10px; } QLineEdit#terminalInput { background: #0b1725; border: 1px solid #203349; color: #dce8f6; padding: 8px 12px; } QPushButton#terminalAction { background: transparent; border: 0; color: #a5b8cc; padding: 4px 9px; } QPushButton#terminalAction:hover { color: #21d0eb; }
QLabel#chatTitle { font-weight: 700; font-size: 17px; color: #eef5ff; } QLabel#provider { color: #68cfea; font-size: 11px; } QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget > QWidget { border: 0; background: #0c1826; } QFrame#chatIncoming, QFrame#chatOutgoing { border-radius: 8px; max-width: 300px; } QFrame#chatIncoming { background: #182637; } QFrame#chatOutgoing { background: #164a75; } QLabel#chatText { color: #e1ebf6; } QLabel#chatMeta { color: #8fa2b7; font-size: 11px; }
QFrame#attachmentPanel { background: #102238; border: 1px solid #2e5e7b; border-radius: 6px; }
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

"""Arduino sketch authoring widget independent of the main IDE editor."""
from pathlib import Path
import re

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QColor, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QComboBox, QLabel, QFileDialog, QInputDialog, QMessageBox, QScrollArea)

from modules.electronics import BLINK
from modules.arduino_examples import FIVE_LEDS, MOTOR_PWM, RELAYS, SEVEN_SEGMENT

TEMPLATES = {
    "Vazio": "void setup() {\n  // Inicialização\n}\n\nvoid loop() {\n  // Código principal\n}\n",
    "LED sem bloqueio": BLINK,
    "Entrada analógica A0": "void setup() {\n  Serial.begin(115200);\n}\n\nvoid loop() {\n  Serial.println(analogRead(A0));\n  delay(100);\n}\n",
    "Botão com pull-up": "// Botão entre pino 2 e GND; leitura simples, sem debounce.\nvoid setup() {\n  pinMode(2, INPUT_PULLUP);\n  pinMode(LED_BUILTIN, OUTPUT);\n}\n\nvoid loop() {\n  digitalWrite(LED_BUILTIN, digitalRead(2) == LOW ? HIGH : LOW);\n}\n",
    "Eco serial": "void setup() {\n  Serial.begin(115200);\n}\n\nvoid loop() {\n  while (Serial.available() > 0) {\n    Serial.write(Serial.read());\n  }\n}\n",
    "5 LEDs / 3 efeitos": FIVE_LEDS,
    "Motor PWM + botão": MOTOR_PWM,
    "Relés sequenciais": RELAYS,
    "Display 7 segmentos": SEVEN_SEGMENT,
}


class ArduinoHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        for pattern, color in [(r"\b(?:void|bool|int|long|unsigned|const|char|float|if|else|while|for|return|static|HIGH|LOW|OUTPUT|INPUT_PULLUP|LED_BUILTIN)\b", "#379dd9"),
                               (r'"(?:[^"\\]|\\.)*"', "#8ba86a"), (r"//.*$", "#8694a1")]:
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), style)
        # Preserve multi-line comment state across blocks.
        start = 0 if self.previousBlockState() == 1 else text.find("/*")
        self.setCurrentBlockState(0)
        while start >= 0:
            end = text.find("*/", start + (0 if self.previousBlockState() == 1 and start == 0 else 2))
            style = QTextCharFormat()
            style.setForeground(QColor("#8694a1"))
            if end < 0:
                self.setFormat(start, len(text) - start, style)
                self.setCurrentBlockState(1)
                break
            self.setFormat(start, end + 2 - start, style)
            start = text.find("/*", end + 2)


class ArduinoEditor(QWidget):
    runRequested = Signal(str)
    rootChanged = Signal(str)
    configureRequested = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = Path(workspace)
        self.path = None
        self.root = None
        self.saved_text = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        actions = QHBoxLayout()
        self.template = QComboBox()
        self.template.addItems(TEMPLATES)
        actions.addWidget(self.template)
        for label, callback in [("Novo código", self.new_code), ("Criar sketch…", self.create_sketch),
                                ("Abrir código…", self.open_code), ("Salvar", self.save), ("Salvar como…", self.save_as)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.file_label = QLabel("Código sem arquivo — crie, abra ou cole um sketch Arduino.")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
        self.code = QPlainTextEdit()
        self.code.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.code.setPlaceholderText("Cole seu código Arduino aqui ou escolha um modelo e clique em Novo código.")
        self.highlighter = ArduinoHighlighter(self.code.document())
        self.code.document().modificationChanged.connect(self.update_label)
        shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.save)
        layout.addWidget(self.code, 1)
        operations = QHBoxLayout()
        for label, callback in [("Colar", self.code.paste), ("Desfazer", self.code.undo), ("Refazer", self.code.redo),
                                ("Configurar placa/porta", self.configureRequested.emit)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            operations.addWidget(button)
        for label, action in [("Salvar e compilar", "build"), ("Salvar e enviar à placa", "upload"), ("Simular", "simulate"), ("Monitor serial", "monitor")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, a=action: self.runRequested.emit(a))
            operations.addWidget(button)
        layout.addLayout(operations)
        note = QLabel("Usa a placa, porta e baud selecionados em Firmware e serial. Salvar e enviar compila antes de gravar. Saída no painel inferior.")
        note.setWordWrap(True)
        layout.addWidget(note)

    def update_label(self, *_):
        label = str(self.path) if self.path else "Código sem arquivo"
        self.file_label.setText(label + (" • alterado" if self.code.document().isModified() else ""))

    def confirm_discard(self):
        if not self.code.document().isModified():
            return True
        answer = QMessageBox.question(self, "Código Arduino alterado", "Salvar o código antes de continuar?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new_code(self):
        if self.confirm_discard():
            self.path, self.root, self.saved_text = None, None, ""
            self.code.setPlainText(TEMPLATES[self.template.currentText()])
            self.code.document().setModified(True)
            self.update_label()

    def set_file(self, path, text):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        if self.path.suffix != ".ino":
            for parent in self.path.parents:
                if (parent / "platformio.ini").is_file() or (parent / "merotec-firmware.json").is_file():
                    self.root = parent
                    break
        self.saved_text = text
        self.code.setPlainText(text)
        self.code.document().setModified(False)
        self.update_label()
        self.rootChanged.emit(str(self.root))

    def load_file(self, path):
        try:
            path = Path(path).resolve()
            if path.suffix.lower() not in {".ino", ".cpp", ".h", ".hpp", ".c"}:
                raise ValueError("Escolha um arquivo Arduino/C/C++.")
            if path.stat().st_size > 2_000_000:
                raise ValueError("Código excede 2 MB.")
            text = path.read_text(encoding="utf-8-sig")
            if not self.confirm_discard():
                return False
            self.set_file(path, text)
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Abrir código Arduino", str(exc))
            return False

    def open_code(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir código Arduino", str(self.root or self.workspace), "Arduino e C/C++ (*.ino *.cpp *.h *.hpp *.c)")
        if path:
            self.load_file(path)

    def create_sketch(self):
        if not self.confirm_discard():
            return
        parent = QFileDialog.getExistingDirectory(self, "Onde criar o sketch", str(self.workspace))
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "Novo sketch Arduino", "Nome (letras, números e _):")
        if not ok:
            return
        try:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Use um nome iniciado por letra ou _, sem espaços ou acentos.")
            folder = Path(parent) / name
            folder.mkdir()  # Never replace an existing sketch directory.
            path = folder / (name + ".ino")
            text = TEMPLATES[self.template.currentText()]
            path.write_text(text, encoding="utf-8")
            self.set_file(path, text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Criar sketch", str(exc))

    def save(self):
        if self.path is None:
            return self.save_as()
        return self.write_file(self.path)

    def write_file(self, path):
        try:
            path = Path(path).resolve()
            if path.exists() and path != self.path:
                if QMessageBox.question(self, "Substituir arquivo", f"Substituir {path.name}?") != QMessageBox.StandardButton.Yes:
                    return False
            elif path.exists() and path == self.path:
                disk = path.read_text(encoding="utf-8-sig")
                if disk != self.saved_text and QMessageBox.question(self, "Arquivo alterado no disco", "O arquivo mudou fora da bancada. Substituir pelo código do editor?") != QMessageBox.StandardButton.Yes:
                    return False
            # QSaveFile commits atomically and preserves the previous file on error.
            from PySide6.QtCore import QSaveFile, QIODevice
            handle = QSaveFile(str(path))
            data = self.code.toPlainText().encode("utf-8")
            if not handle.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(handle.errorString())
            if handle.write(data) != len(data) or not handle.commit():
                handle.cancelWriting()
                raise OSError(handle.errorString())
            # Keep undo history after saving.
            self.path = path
            self.saved_text = self.code.toPlainText()
            self.root = path.parent
            if path.suffix != ".ino":
                for ancestor in path.parents:
                    if (ancestor / "platformio.ini").is_file() or (ancestor / "merotec-firmware.json").is_file():
                        self.root = ancestor
                        break
            self.code.document().setModified(False)
            self.update_label()
            self.rootChanged.emit(str(self.root))
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Salvar código Arduino", str(exc))
            return False

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salvar código Arduino", str(self.path or (self.workspace / "sketch.ino")), "Arduino (*.ino);;C++ (*.cpp);;Cabeçalho (*.h)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return False
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".ino")
        if target.suffix.lower() not in {".ino", ".cpp", ".h", ".hpp", ".c"}:
            QMessageBox.warning(self, "Salvar código Arduino", "Extensão de código não suportada.")
            return False
        return self.write_file(target)

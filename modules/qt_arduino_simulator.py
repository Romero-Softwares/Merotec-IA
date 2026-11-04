"""Interactive training panels driven by actual AVR firmware pin state."""
from __future__ import annotations

import codecs
import math
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QMetaObject, QCoreApplication
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QCheckBox, QSlider, QLineEdit, QFileDialog, QScrollArea)

from modules.avr_simulator import SimulationWorker

PANELS = {"LEDs (5)": [2, 3, 4, 5, 6], "Relés (4)": [2, 3, 4, 5],
          "Motor DC + ponte H": [9, 7, 8], "Entradas / saídas": [],
          "Display 7 segmentos": [2, 3, 4, 5, 6, 7, 8], "Display serial 16×2": [],
          "Analisador lógico": [2, 3, 4, 5, 6, 13]}


def pin_name(pin):
    return f"D{pin}" if pin < 14 else f"A{pin - 14}"


class DeviceCanvas(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setMinimumHeight(220)
        self.angle = 0.0

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#101f2c"))
        p.setPen(QColor("#dce9f4"))
        p.setFont(QFont("Segoe UI", 10))
        owner = self.owner
        kind = owner.panel.currentText()
        p.drawText(16, 24, kind + " • ATmega328P / 16 MHz")
        if not owner.state:
            p.drawText(16, 65, "Compile e simule o código ou abra um HEX compatível.")
            return
        pins = owner.state["pins"]
        mapped = [pins[control.value()] for control in owner.pin_controls]
        w = self.width()
        if kind.startswith("LEDs"):
            for index, pin in enumerate(mapped):
                x = int((index + .5) * w / 5)
                duty = pin["duty"] if pin["mode"] == "OUTPUT" else 0
                color = QColor.fromRgbF(.15 + .85 * duty, .12 + .65 * duty, .08)
                p.setBrush(color)
                p.setPen(QPen(QColor("#eebd63"), 2))
                p.drawEllipse(x - 23, 65, 46, 46)
                p.setPen(QColor("#dce9f4"))
                p.drawText(x - 33, 140, pin_name(int(pin["pin"])))
                p.drawText(x - 33, 162, f"PWM {duty * 100:.0f}%")
        elif kind.startswith("Relés"):
            for index, pin in enumerate(mapped):
                active = pin["mode"] == "OUTPUT" and (not pin["level"] if owner.active_low.isChecked() else pin["level"])
                x = int(index * w / 4 + 14)
                p.setBrush(QColor("#29a577" if active else "#294050"))
                p.setPen(QPen(QColor("#8baec2"), 2))
                p.drawRoundedRect(x, 65, int(w / 4 - 28), 65, 8, 8)
                p.setPen(QColor("#ffffff"))
                p.drawText(x + 9, 93, f"{pin_name(int(pin['pin']))}: {'ON' if active else 'OFF'}")
                p.drawText(x + 9, 115, "COM → NO" if active else "COM → NC")
        elif kind.startswith("Motor"):
            enable, a, b = mapped
            duty = enable["duty"] if enable["mode"] == "OUTPUT" else 0
            valid = all(pin["mode"] == "OUTPUT" for pin in mapped)
            direction = (int(a["level"]) - int(b["level"])) if valid else 0
            p.setPen(QPen(QColor("#57c6d8"), 4))
            p.drawEllipse(40, 55, 110, 110)
            radians = math.radians(self.angle)
            p.drawLine(95, 110, int(95 + math.cos(radians) * 45), int(110 + math.sin(radians) * 45))
            p.setPen(QColor("#dce9f4"))
            p.drawText(175, 83, f"Comando PWM: {100*duty:.0f}%")
            mode = "frente" if direction > 0 else "ré" if direction < 0 else "freio/parado"
            p.drawText(175, 110, f"Sentido: {mode}")
            p.drawText(175, 142, "Modelo ideal; não calcula torque, corrente ou RPM reais.")
        elif kind.startswith("Display 7"):
            segments = [(50, 55, 110, 55), (115, 60, 115, 105), (115, 115, 115, 160),
                        (50, 165, 110, 165), (45, 115, 45, 160), (45, 60, 45, 105), (50, 110, 110, 110)]
            for index, (pin, coords) in enumerate(zip(mapped, segments)):
                active = pin["mode"] == "OUTPUT" and (not pin["level"] if owner.active_low.isChecked() else pin["level"])
                p.setPen(QPen(QColor("#ff6e53" if active else "#354350"), 8))
                p.drawLine(*coords)
            p.setPen(QColor("#dce9f4"))
            p.drawText(155, 100, "Mapa: a, b, c, d, e, f, g")
            p.drawText(155, 130, "Ativo LOW permite modelo de ânodo comum.")
        elif kind.startswith("Display serial"):
            p.fillRect(24, 50, min(w - 48, 500), 110, QColor("#9bbd57"))
            p.setPen(QColor("#162f1d"))
            p.setFont(QFont("Consolas", 18))
            lines = owner.serial_text.replace("\r", "").split("\n")
            visible = lines[-2:] if len(lines) > 1 else [lines[-1], ""]
            for index, text in enumerate(visible):
                p.drawText(38, 90 + index * 34, text[-16:])
            p.setFont(QFont("Segoe UI", 10))
            p.setPen(QColor("#dce9f4"))
            p.drawText(24, 190, "Visualização da UART; não emula um controlador LCD/I²C.")
        elif kind == "Analisador lógico":
            selected = [control.value() for control in owner.pin_controls]
            samples = list(owner.history)
            for row, pin in enumerate(selected):
                baseline = 54 + row * 22
                p.setPen(QColor("#dce9f4"))
                p.drawText(8, baseline + 4, pin_name(pin))
                p.setPen(QPen(QColor("#5cd2a7"), 1.5))
                for index in range(1, len(samples)):
                    x1 = 48 + (index - 1) * (w - 65) / max(1, len(samples) - 1)
                    x2 = 48 + index * (w - 65) / max(1, len(samples) - 1)
                    y1 = baseline - samples[index - 1][1][pin] * 12
                    y2 = baseline - samples[index][1][pin] * 12
                    p.drawLine(int(x1), int(y1), int(x2), int(y2))
            if samples:
                p.setPen(QColor("#9cb6c9"))
                p.drawText(48, 204, f"{samples[0][0]:.1f} → {samples[-1][0]:.1f} ms • duty médio por amostra, não captura de todas as bordas")
        else:
            columns = 10
            for index, pin in enumerate(pins):
                x = int((index % columns) * w / columns + 8)
                y = 55 + (index // columns) * 74
                p.setPen(QPen(QColor("#e66c65" if pin["conflict"] else "#6f94a8"), 1))
                p.setBrush(QColor("#2ba478" if pin["level"] else "#293d4c"))
                p.drawRoundedRect(x, y, max(28, int(w / columns - 12)), 38, 4, 4)
                p.setPen(QColor("white"))
                p.drawText(x + 3, y + 17, pin_name(index))
                p.setFont(QFont("Segoe UI", 7))
                p.drawText(x, y + 54, pin["mode"])
                p.setFont(QFont("Segoe UI", 10))
        conflicts = [pin_name(i) for i, pin in enumerate(pins) if pin["conflict"]]
        if conflicts:
            p.setPen(QColor("#ff937d"))
            p.drawText(16, self.height() - 14, "Conflito: entrada externa força nível oposto à saída em " + ", ".join(conflicts))


class ArduinoSimulator(QWidget):
    compileRequested = Signal()
    log = Signal(str)
    loadCommand = Signal(str)
    resumeCommand = Signal()
    pauseCommand = Signal()
    stepCommand = Signal()
    changeCommand = Signal(object)
    loaded = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = Path(workspace)
        self.thread = None
        self.worker = None
        self.state = None
        self.hex_path = None
        self.serial_text = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.history = deque(maxlen=300)
        self.pin_controls = []
        self.running = False
        self.is_paused = True
        if QCoreApplication.instance() is not None:
            QCoreApplication.instance().aboutToQuit.connect(self.shutdown)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        for label, callback in [("Salvar, compilar e simular", self.compileRequested.emit), ("Abrir HEX…", self.open_hex),
                                ("Executar", self.resume), ("Pausar", self.pause), ("Passo", self.step), ("Reiniciar", self.reset), ("Encerrar", self.stop)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        selectors = QHBoxLayout()
        self.panel = QComboBox()
        self.panel.addItems(PANELS)
        self.panel.currentTextChanged.connect(self.rebuild_mapping)
        selectors.addWidget(QLabel("Painel:"))
        selectors.addWidget(self.panel)
        self.active_low = QCheckBox("Ativo em LOW")
        self.active_low.toggled.connect(lambda: self.canvas.update())
        selectors.addWidget(self.active_low)
        self.speed = QComboBox()
        for label, ratio in [("0,25×", .25), ("0,5×", .5), ("1× — tempo real", 1.0), ("2×", 2.0), ("Máximo", 0.0)]:
            self.speed.addItem(label, ratio)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda: self.changeCommand.emit(["speed", self.speed.currentData()]))
        selectors.addWidget(self.speed)
        layout.addLayout(selectors)
        self.mapping = QHBoxLayout()
        layout.addLayout(self.mapping)
        self.canvas = DeviceCanvas(self)
        layout.addWidget(self.canvas)
        self.status = QLabel("Sem firmware. Uno/Nano ATmega328P. Velocidade padrão: 1×, sincronizada ao relógio real.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.inputs_toggle = QPushButton("Entradas digitais / analógicas")
        input_actions = QHBoxLayout()
        input_actions.addWidget(self.inputs_toggle)
        self.button_pin = QSpinBox()
        self.button_pin.setRange(0, 19)
        self.button_pin.setValue(2)
        self.button_pin.setPrefix("Botão em P")
        input_actions.addWidget(self.button_pin)
        self.momentary = QPushButton("Pressionar botão → GND")
        self.momentary.pressed.connect(lambda: self.digital_controls[self.button_pin.value()].setCurrentIndex(1))
        self.momentary.released.connect(lambda: self.digital_controls[self.button_pin.value()].setCurrentIndex(0))
        input_actions.addWidget(self.momentary)
        layout.addLayout(input_actions)
        self.inputs = QWidget()
        grid = QGridLayout(self.inputs)
        self.digital_controls = []
        for pin in range(20):
            control = QComboBox()
            for text, value in [("Flutuante", None), ("GND / LOW", False), ("5 V / HIGH", True)]:
                control.addItem(text, value)
            control.currentIndexChanged.connect(lambda index, p=pin, c=control: self.changeCommand.emit(["digital", p, c.currentData()]))
            row, column = pin // 4, (pin % 4) * 2
            grid.addWidget(QLabel(pin_name(pin)), row, column)
            grid.addWidget(control, row, column + 1)
            self.digital_controls.append(control)
        self.analog_controls = []
        for channel in range(6):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1023)
            value_label = QLabel("0 / 0.00 V")
            slider.valueChanged.connect(lambda v, ch=channel, label=value_label: (label.setText(f"{v} / {v*5/1023:.2f} V"), self.changeCommand.emit(["analog", ch, v])))
            grid.addWidget(QLabel(f"ADC A{channel}"), 5 + channel, 0)
            grid.addWidget(slider, 5 + channel, 1, 1, 5)
            grid.addWidget(value_label, 5 + channel, 6, 1, 2)
            self.analog_controls.append(slider)
        self.inputs.setVisible(False)
        self.inputs_toggle.clicked.connect(lambda: self.inputs.setVisible(not self.inputs.isVisible()))
        layout.addWidget(self.inputs)
        serial_row = QHBoxLayout()
        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText("Enviar dados à UART simulada (sem usar porta física)")
        serial_row.addWidget(self.serial_input)
        send = QPushButton("Enviar + LF")
        send.clicked.connect(self.send_serial)
        self.serial_input.returnPressed.connect(self.send_serial)
        serial_row.addWidget(send)
        layout.addLayout(serial_row)
        note = QLabel("Executa instruções do HEX no emulador AVR8js incorporado. Saídas representam sinais lógicos/PWM. Relés e motores são modelos ideais, sem carga, corrente ou mecânica reais. Não simula qualquer periférico/biblioteca automaticamente.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.rebuild_mapping()

    def rebuild_mapping(self, *_):
        while self.mapping.count():
            item = self.mapping.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.pin_controls = []
        motor = self.panel.currentText().startswith("Motor")
        for index, pin in enumerate(PANELS[self.panel.currentText()]):
            label = ["PWM", "IN1", "IN2"][index] if motor else ("abcdefg"[index] if self.panel.currentText().startswith("Display 7") else str(index + 1))
            self.mapping.addWidget(QLabel(label))
            control = QSpinBox()
            control.setRange(0, 19)
            control.setPrefix("P")
            control.setValue(pin)
            control.setToolTip("0–13 = D0–D13; 14–19 = A0–A5")
            control.valueChanged.connect(lambda: self.canvas.update())
            self.mapping.addWidget(control)
            self.pin_controls.append(control)
        self.mapping.addStretch()
        self.canvas.update()

    def ensure_worker(self):
        if self.thread is not None:
            return
        self.thread = QThread(self)
        self.worker = SimulationWorker()
        self.worker.moveToThread(self.thread)
        self.loadCommand.connect(self.worker.load)
        self.resumeCommand.connect(self.worker.resume)
        self.pauseCommand.connect(self.worker.pause)
        self.stepCommand.connect(self.worker.tick)
        self.changeCommand.connect(self.worker.change)
        self.worker.frame.connect(self.receive_frame)
        self.worker.failed.connect(self.failure)
        self.worker.ready.connect(self.on_ready)
        self.worker.paused.connect(self.on_paused)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def load_hex(self, path, board="uno"):
        from modules.avr_simulator import SUPPORTED_BOARDS
        if board not in SUPPORTED_BOARDS:
            self.failure("Simulador atual suporta apenas Uno/Nano ATmega328P; selecione um perfil compatível.")
            return False
        self.ensure_worker()
        self.running = False
        self.hex_path = Path(path)
        self.state = None
        self.history.clear()
        self.serial_text = ""
        self.decoder.reset()
        self.status.setText("Carregando firmware AVR…")
        self.canvas.update()
        self.loadCommand.emit(str(path))
        return True

    def open_hex(self):
        path, _ = QFileDialog.getOpenFileName(self, "HEX ATmega328P / 16 MHz", str(self.workspace), "Intel HEX (*.hex)")
        if path:
            self.load_hex(path)

    def on_ready(self):
        for pin, control in enumerate(self.digital_controls):
            self.changeCommand.emit(["digital", pin, control.currentData()])
        for channel, control in enumerate(self.analog_controls):
            self.changeCommand.emit(["analog", channel, control.value()])
        self.changeCommand.emit(["speed", self.speed.currentData()])
        self.resume()
        self.loaded.emit()

    def resume(self):
        if self.thread is not None and self.state is not None:
            self.running = True
            self.is_paused = False
            self.resumeCommand.emit()

    def pause(self):
        self.running = False
        self.is_paused = self.thread is None
        self.pauseCommand.emit()
        if self.state:
            self.status.setText("Pausando após o lote atual…")

    def on_paused(self):
        if not self.running:
            self.is_paused = True
            if self.state:
                self.status.setText(f"Pausado • {self.state['time_ms']:.2f} ms simulados • PC {self.state['pc']}")

    def step(self):
        self.pause()
        self.stepCommand.emit()

    def reset(self):
        if self.hex_path:
            self.load_hex(self.hex_path)

    def stop(self):
        self.shutdown()
        self.state = None
        self.is_paused = True
        self.history.clear()
        self.status.setText("Simulação encerrada. Reiniciar recarrega o último HEX.")
        self.canvas.update()

    def send_serial(self):
        if self.state is not None:
            self.changeCommand.emit(["serial", self.serial_input.text() + "\n"])
            self.serial_input.clear()

    def receive_frame(self, state):
        previous = self.state
        self.state = state
        self.history.append((state["time_ms"], [p["duty"] for p in state["pins"]]))
        if self.panel.currentText().startswith("Motor") and previous and self.pin_controls:
            pins = [state["pins"][c.value()] for c in self.pin_controls]
            direction = int(pins[1]["level"]) - int(pins[2]["level"])
            self.canvas.angle = (self.canvas.angle + (state["time_ms"] - previous["time_ms"]) * pins[0]["duty"] * direction * .6) % 360
        if state["serial"]:
            text = self.decoder.decode(bytes(int(b) for b in state["serial"]))
            self.serial_text = (self.serial_text + text)[-4096:]
            self.log.emit(text)
        if state["serial_dropped"]:
            self.log.emit(f"\n[Simulador: {int(state['serial_dropped'])} bytes seriais descartados por limite de buffer]\n")
        clock_text = ""
        if "wall_ms" in state:
            ratio = state["speed_ratio"]
            target = state["requested_speed"]
            target_text = f"{target:g}×" if target else "máximo"
            clock_text = f" • {state['wall_ms']/1000:.2f} s reais • média {ratio:.2f}× / alvo {target_text}"
            if self.running and state["wall_ms"] > 1000 and target and ratio < target * .85:
                clock_text += " • abaixo da velocidade solicitada"
            clock_text += f" • {state.get('engine', '')}"
            if state.get("engine_warning"):
                clock_text += " • " + state["engine_warning"]
        self.status.setText(f"{'Executando' if self.running else 'Pausado'} • {state['time_ms']/1000:.3f} s simulados{clock_text} • PC {int(state['pc'])}")
        self.canvas.update()

    def failure(self, message):
        self.pause()
        self.status.setText(message)
        self.log.emit("\n[Simulador] " + message + "\n")

    def shutdown(self):
        if self.thread is not None:
            self.running = False
            QMetaObject.invokeMethod(self.worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait()
            self.thread.deleteLater()
            self.thread = self.worker = None

"""Electronics workbench with its own asynchronous process/output panel."""
import codecs
import json
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtGui import QTextCursor

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
                              QLineEdit, QPushButton, QLabel, QMessageBox, QTabWidget, QWidget, QPlainTextEdit, QHBoxLayout, QSplitter, QScrollArea, QSizePolicy, QFileDialog)

from modules.electronics import calculate, tool_arguments, detect_embedded
from modules.native_serial import ports
from modules.process_runtime import ProcessObservation

BAUD_RATES = (300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400,
              57600, 76800, 115200, 230400, 250000, 460800, 500000, 921600, 1000000, 2000000)


class ElectronicsDialog(QDialog):
    def __init__(self, ide):
        super().__init__(ide)
        self.ide = ide
        self.process = None
        self._stopper = None
        self._close_pending = False
        self._stopping = False
        self._after_process = None
        self.setWindowTitle("Merotec | Bancada elétrica e eletrônica")
        self.resize(900, 680)
        self.setMinimumHeight(360)
        layout = QVBoxLayout(self)
        intro = QLabel("Desenvolvimento embarcado • Cálculos offline • Dossiê de engenharia")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tabs = QTabWidget()
        self.tabs = tabs
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tabs)
        layout.addWidget(splitter, 1)
        tools = QWidget()
        form = QFormLayout(tools)
        self.backend = QComboBox()
        self.backend.addItems(["native", "arduino", "platformio"])
        self.backend.setItemText(0, "Merotec Python (nativo)")
        self.backend.setItemData(0, "native")
        self.folder = QLineEdit(str(ide.workspace / "firmware" if (ide.workspace / "firmware/firmware.ino").exists() else ide.workspace))
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.folder, 1)
        self.browse_firmware_button = QPushButton("Buscar pasta…")
        self.browse_firmware_button.clicked.connect(self.browse_firmware)
        folder_layout.addWidget(self.browse_firmware_button)
        open_code = QPushButton("Abrir código…")
        open_code.clicked.connect(self.open_arduino_code)
        folder_layout.addWidget(open_code)
        self.fqbn = QLineEdit("arduino:avr:uno")
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.port.lineEdit().setPlaceholderText("Selecione ou digite a porta")
        port_row = QWidget()
        port_layout = QHBoxLayout(port_row)
        port_layout.setContentsMargins(0, 0, 0, 0)
        port_layout.addWidget(self.port, 1)
        self.refresh_ports_button = QPushButton("Atualizar portas")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        port_layout.addWidget(self.refresh_ports_button)
        self.port_status = QLabel()
        self.port_status.setWordWrap(True)
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.baud.addItems([str(rate) for rate in BAUD_RATES])
        self.baud.setCurrentText("115200")
        self.baud.setToolTip("Selecione ou digite o baud rate. A taxa deve ser suportada pelo dispositivo e pelo driver.")
        self.environment = QLineEdit()
        self.environment.setPlaceholderText("Ambiente do platformio.ini, se houver")
        for title, widget in [("Ferramenta", self.backend), ("Pasta do firmware", folder_row), ("Placa / FQBN", self.fqbn), ("Ambiente", self.environment), ("Porta", port_row), ("", self.port_status), ("Baud", self.baud)]:
            form.addRow(title, widget)
        self.refresh_ports()
        from modules.firmware_packages import installed_paths
        try:
            installed_paths()
            native_status = "Toolchain AVR pronta"
        except ValueError:
            native_status = "Pacotes AVR pendentes: use Preparar toolchain"
        status = QLabel("Gerenciador Python próprio. " + native_status + ".\nLegados opcionais: " + " | ".join(f"{name}: {'disponível' if shutil.which(name) else 'ausente'}" for name in ("arduino-cli", "pio")))
        status.setWordWrap(True)
        form.addRow(status)
        self.install_button = QPushButton("Preparar toolchain AVR da IDE")
        self.install_button.clicked.connect(lambda checked=False: self.run_tool("install"))
        self.backend.currentIndexChanged.connect(lambda index: self.install_button.setEnabled(index == 0))
        form.addRow(self.install_button)
        for label, action in [("Listar dispositivos", "boards"), ("Compilar firmware", "build"), ("Compilar e gravar na placa", "upload"), ("Monitor serial", "monitor")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, a=action: self.run_tool(a))
            form.addRow(button)
        form.addRow(QLabel("Acompanhe as saídas no painel inferior desta janela."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(tools)
        tabs.addTab(scroll, "Firmware e serial")
        from modules.qt_arduino_editor import ArduinoEditor
        self.arduino_editor = ArduinoEditor(ide.workspace, self)
        self.arduino_editor.rootChanged.connect(self.folder.setText)
        self.arduino_editor.runRequested.connect(self.run_editor_action)
        self.arduino_editor.configureRequested.connect(lambda: self.tabs.setCurrentIndex(0))
        tabs.addTab(self.arduino_editor, "Código Arduino")
        from modules.qt_arduino_simulator import ArduinoSimulator
        self.simulator = ArduinoSimulator(ide.workspace, self)
        self.simulator.compileRequested.connect(lambda: self.run_editor_action("simulate"))
        self.simulator.log.connect(self.append_output)
        self.simulator.loaded.connect(self.simulation_loaded)
        tabs.addTab(self.scroll_page(self.simulator), "Simulador Arduino")
        self.simulator_page = tabs.widget(tabs.count() - 1)
        calc = QWidget()
        form = QFormLayout(calc)
        self.operation = QComboBox()
        self.operation.addItems(["Lei de Ohm", "Resistor de LED", "Filtro RC", "Potência DC"])
        self.first = QLineEdit("5")
        self.second = QLineEdit("1000")
        self.first_label, self.second_label = QLabel(), QLabel()
        form.addRow("Cálculo", self.operation)
        form.addRow(self.first_label, self.first)
        form.addRow(self.second_label, self.second)
        self.result = QLabel()
        self.result.setWordWrap(True)
        button = QPushButton("Calcular")
        button.clicked.connect(self.compute)
        form.addRow(button)
        form.addRow(self.result)
        self.operation.currentIndexChanged.connect(self.update_units)
        self.update_units()
        note = QLabel("Valores ideais. Dimensione tolerâncias, potência nominal, temperatura e margens usando os datasheets. LED: informe Vs − Vf.")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(self.scroll_page(calc), "Calculadoras")
        diagnostics = QWidget()
        diagnostic_layout = QVBoxLayout(diagnostics)
        diagnostic_note = QLabel("Analisa BOM, pinagem, arquitetura, medições e projetos nativos .mpcb.json com ERC/DRC interno. KiCad CLI é opcional para arquivos KiCad. A saída aparece abaixo e o relatório é carregado ao concluir.")
        diagnostic_note.setWordWrap(True)
        diagnostic_layout.addWidget(diagnostic_note)
        for title, use_kicad in [("Diagnosticar arquivos e testes funcionais", False), ("Diagnosticar incluindo KiCad ERC/DRC", True)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, k=use_kicad: self.run_diagnostics(k))
            diagnostic_layout.addWidget(button)
        refresh = QPushButton("Carregar último relatório")
        refresh.clicked.connect(self.load_diagnostic_report)
        diagnostic_layout.addWidget(refresh)
        self.diagnostic_report = QPlainTextEdit()
        self.diagnostic_report.setReadOnly(True)
        self.diagnostic_report.setPlaceholderText("Os resultados são salvos em .merotec_system_ai/pcb_diagnostics/report.txt e report.json.")
        diagnostic_layout.addWidget(self.diagnostic_report)
        tabs.addTab(self.scroll_page(diagnostics), "PCB e arquitetura")
        guide = QLabel("Fluxo de engenharia\n\n1. Especifique alimentação, cargas, interfaces e ambiente.\n2. Revise datasheets, pinagem e compatibilidade elétrica.\n3. Preencha hardware/bom.csv e hardware/pinout.csv nos novos projetos.\n4. Registre cálculos e hipóteses em docs/engineering.md.\n5. Execute tests/bench-plan.csv e registre medições.\n\nNo chat: /skill electrical-electronics-engineer seguido da missão.\nAbrange eletrônica analógica/digital, fontes, sensores, PCB, firmware, automação, Modbus e diagnóstico industrial.\n\nProjetos de potência e funções de segurança exigem revisão técnica e ensaios apropriados; a IDE não certifica hardware.")
        guide.setWordWrap(True)
        tabs.addTab(self.scroll_page(guide), "Engenharia assistida")
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 4, 0, 0)
        controls = QHBoxLayout()
        self.process_status = QLabel("Saída da bancada — pronto")
        self.process_status.setWordWrap(True)
        self.process_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls.addWidget(self.process_status, 1)
        self.stop_button = QPushButton("Interromper")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_process)
        controls.addWidget(self.stop_button)
        clear = QPushButton("Limpar saída")
        clear.clicked.connect(lambda: self.output.clear())
        controls.addWidget(clear)
        panel_layout.addLayout(controls)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(6000)
        self.output.setPlaceholderText("Compilação, instalação, gravação, diagnósticos e monitor serial aparecem aqui.")
        self.output.setMinimumHeight(70)
        panel_layout.addWidget(self.output)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 180])

    @staticmethod
    def scroll_page(widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def append_output(self, text):
        bar = self.output.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 3
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        if self.output.document().characterCount() > 1_000_000:
            trim = QTextCursor(self.output.document())
            trim.setPosition(0)
            trim.setPosition(200000, QTextCursor.MoveMode.KeepAnchor)
            trim.removeSelectedText()
        if follow:
            bar.setValue(bar.maximum())

    def start_process(self, program, arguments, label, working_directory, after=None):
        if self.process is not None or self._stopper is not None:
            self.append_output("\nJá há um processo em execução nesta bancada. Interrompa-o antes de iniciar outro.\n")
            return False
        observation = ProcessObservation([str(program), *map(str, arguments)], self.ide.workspace,
                                         cwd=working_directory, origin="electronics")
        self._process_observation = observation
        if observation.assess().get("should_pause"):
            self.append_output("\n[Diagnóstico] A mesma operação falhou anteriormente; confira ambiente e saída antes de repetir.\n")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(str(Path(working_directory).resolve()))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("MEROTEC_EXECUTION_WORKSPACE", str(observation.workspace))
        process.setProcessEnvironment(env)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.process = process
        self._stopping = False
        self.last_exit_code = None
        self._after_process = after
        process.readyReadStandardOutput.connect(lambda: self.read_output(process))
        process.finished.connect(lambda code, status: self.process_finished(process, code, status))
        process.errorOccurred.connect(lambda error: self.process_error(process, error))
        self.stop_button.setEnabled(True)
        self.process_status.setText(f"Executando: {label}")
        self.append_output(f"\n▶ {label}\n")
        process.start(program, arguments)
        if observation.timeout:
            QTimer.singleShot(int(observation.timeout * 1000), lambda p=process, o=observation: self.process_deadline(p, o))
        return True

    def process_deadline(self, process, observation):
        if self.process is process and not observation.finished:
            observation.timed_out = True
            self.append_output(f"\n[Diagnóstico] Tempo limite de {observation.timeout:g}s excedido; causa não confirmada.\n")
            self.stop_process()

    def read_output(self, process):
        if self.process is process:
            text = self._decoder.decode(bytes(process.readAllStandardOutput()))
            observation = getattr(self, "_process_observation", None)
            if observation:
                observation.feed(text)
            self.append_output(text)

    def process_error(self, process, error):
        if self.process is not process:
            return
        if error == QProcess.ProcessError.FailedToStart:
            observation = getattr(self, "_process_observation", None)
            if observation:
                observation.feed(process.errorString())
                observation.finish(launch_error=True)
            self.append_output(f"\nFalha ao iniciar: {process.errorString()}\n")
            self.process_finished(process, -1, QProcess.ExitStatus.CrashExit)

    def process_finished(self, process, code, status):
        if self.process is not process:
            return
        self.read_output(process)
        self.append_output(self._decoder.decode(b"", final=True))
        stopped = self._stopping
        observation = getattr(self, "_process_observation", None)
        if observation:
            observation.finish(code, crashed=status != QProcess.ExitStatus.NormalExit, cancelled=stopped)
            if observation.result and observation.result.get("diagnosis"):
                self.append_output("\n[Diagnóstico] " + observation.result["diagnosis"] + "\n")
        self.last_exit_code = code
        self.process = None
        self.stop_button.setEnabled(False)
        message = "Interrompido" if stopped else ("Concluído" if code == 0 and status == QProcess.ExitStatus.NormalExit else f"Finalizado com código {code}")
        self.process_status.setText(message)
        self.append_output(f"\n[{message}]\n")
        after, self._after_process = self._after_process, None
        process.deleteLater()
        if after is not None and not stopped and code >= 0:
            after()
        if self._close_pending and self._stopper is None:
            self._close_pending = False
            self.simulator.shutdown()
            super().reject()

    def stop_process(self):
        process = self.process
        if process is None or self._stopping:
            return
        self._stopping = True
        self.stop_button.setEnabled(False)
        self.process_status.setText("Interrompendo…")
        # Kill only the process tree launched by this window, including compiler children.
        if os.name == "nt" and process.processId():
            stopper = QProcess(self)
            self._stopper = stopper
            stopper.finished.connect(lambda *_: self.finish_stop(process, stopper))
            stopper.errorOccurred.connect(lambda *_: self.finish_stop(process, stopper))
            stopper.start("taskkill.exe", ["/PID", str(process.processId()), "/T", "/F"])
        else:
            process.terminate()
        QTimer.singleShot(2000, lambda: self.force_stop(process))

    def finish_stop(self, process, stopper):
        if self._stopper is stopper:
            self._stopper = None
            stopper.deleteLater()
        self.force_stop(process)
        if self._close_pending and self.process is None:
            self._close_pending = False
            self.simulator.shutdown()
            super().reject()

    def force_stop(self, process):
        if self.process is process and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def reject(self):
        if not self.arduino_editor.confirm_discard():
            return
        if self.process is not None or self._stopper is not None:
            self._close_pending = True
            self.arduino_editor.setEnabled(False)
            self.stop_process()
        else:
            self.simulator.shutdown()
            super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()

    def browse_firmware(self):
        selected = QFileDialog.getExistingDirectory(self, "Buscar pasta do firmware", self.folder.text())
        if not selected:
            return
        root = Path(selected).resolve()
        sketches = sorted(root.glob("*.ino"))
        if not sketches and (root / "firmware").is_dir():
            sketches = sorted((root / "firmware").glob("*.ino"))
        if sketches:
            preferred = next((p for p in sketches if p.stem == p.parent.name), sketches[0])
            if self.arduino_editor.load_file(preferred):
                self.tabs.setCurrentWidget(self.arduino_editor)
        else:
            self.folder.setText(str(root))
            self.append_output(f"\nPasta selecionada: {root}\nUse Abrir código para editar fontes ou Código Arduino para criar um sketch.\n")

    def open_arduino_code(self):
        self.arduino_editor.open_code()
        if self.arduino_editor.path is not None:
            self.tabs.setCurrentWidget(self.arduino_editor)

    def run_editor_action(self, action):
        if self.process is not None or self._stopper is not None:
            self.append_output("\nInterrompa a operação atual antes de compilar/gravar ou abrir outro monitor.\n")
            return
        if action in {"build", "upload", "simulate"}:
            if not self.arduino_editor.save():
                return
            self.folder.setText(str(self.arduino_editor.root))
        if action == "simulate":
            self.start_simulation()
            return
        self.run_tool(action)

    def start_simulation(self):
        try:
            from modules.native_firmware import configuration
            from modules.avr_simulator import SUPPORTED_BOARDS
            root = Path(self.folder.text()).expanduser().resolve()
            config = configuration(root, self.fqbn.text().strip(), self.environment.text().strip())
            if config["board"] not in SUPPORTED_BOARDS:
                raise ValueError("Simulação atual: Uno/Nano ATmega328P; Mega e outras arquiteturas não são suportadas.")
            self.simulator.pause()
            self.tabs.setCurrentWidget(self.simulator_page)
            script = Path(__file__).with_name("native_firmware.py")
            args = ["-u", str(script), "build", "--project", str(root), "--board", config["board"], "--environment", self.environment.text().strip()]
            self.start_process(sys.executable, args, "Compilar firmware para simulação AVR", root,
                               after=lambda: self.simulation_built(root, config["board"]))
        except (ValueError, OSError, KeyError) as exc:
            self.append_output(f"\n[Simulador] {exc}\n")

    def simulation_built(self, root, board):
        if self.last_exit_code != 0:
            self.simulator.failure("Compilação falhou; firmware anterior não foi iniciado.")
            return
        try:
            report_path = root / ".merotec_system_ai/firmware_build" / board / "build.json"
            report_path.resolve().relative_to(root)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            path = Path(report["hex"]).resolve()
            path.relative_to(root)
            self.simulator.load_hex(path, report["board"])
        except (ValueError, OSError, KeyError) as exc:
            self.simulator.failure(str(exc))

    def simulation_loaded(self):
        self.tabs.setCurrentWidget(self.simulator_page)
        self.append_output("\n[Simulador] HEX carregado no emulador AVR offline. Nenhuma porta física será usada.\n")

    def refresh_ports(self):
        selected = self.port.currentText().strip()
        try:
            detected = list(dict.fromkeys(ports()))
        except OSError as exc:
            self.port_status.setText(f"Falha na detecção: {exc}. Você pode digitar a porta manualmente.")
            return
        self.port.blockSignals(True)
        try:
            self.port.clear()
            self.port.addItem("")
            self.port.addItems(detected)
            self.port.setCurrentText(selected)
        finally:
            self.port.blockSignals(False)
        message = f"{len(detected)} porta(s) detectada(s). Selecione a porta do dispositivo." if detected else "Nenhuma porta detectada. Conecte o dispositivo e atualize, ou digite manualmente."
        if selected and selected not in detected:
            message += f" A porta {selected} não foi detectada; seleção mantida."
        self.port_status.setText(message)

    def run_diagnostics(self, use_kicad=False):
        script = Path(__file__).with_name("pcb_diagnostics.py")
        args = ["-u", str(script), str(self.ide.workspace.resolve())]
        if use_kicad:
            args.append("--kicad")
        self.start_process(sys.executable, args, "Diagnóstico PCB e arquitetura", self.ide.workspace, after=self.load_diagnostic_report)

    def load_diagnostic_report(self):
        from modules.pcb_diagnostics import REPORT_DIR
        try:
            root = self.ide.workspace.resolve()
            report = root / REPORT_DIR / "report.txt"
            report.resolve().relative_to(root)
            self.diagnostic_report.setPlainText(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.diagnostic_report.setPlainText(f"Relatório indisponível: {exc}")

    def update_units(self):
        labels = [("Tensão (V)", "Resistência (Ω)"), ("Queda Vs − Vf (V)", "Corrente LED (mA)"), ("Resistência (Ω)", "Capacitância (µF)"), ("Tensão (V)", "Corrente (A)")]
        a, b = labels[self.operation.currentIndex()]
        self.first_label.setText(a)
        self.second_label.setText(b)
        self.result.setText("")

    def compute(self):
        try:
            self.result.setText(calculate(("ohm", "led", "rc", "power")[self.operation.currentIndex()], self.first.text(), self.second.text()))
        except (ValueError, OverflowError) as exc:
            self.result.setText(str(exc))

    def run_tool(self, action):
        try:
            if self.process is not None or self._stopper is not None:
                self.append_output("\nInterrompa a operação atual antes de iniciar outra.\n")
                return
            folder = Path(self.folder.text()).expanduser().resolve()
            if action in {"build", "upload"} and self.arduino_editor.root in {None, folder} and self.arduino_editor.code.document().isModified():
                if not self.arduino_editor.save():
                    return
                folder = Path(self.folder.text()).expanduser().resolve()
            if not folder.is_dir():
                raise ValueError("Pasta do firmware inexistente.")
            backend = "native" if self.backend.currentIndex() == 0 else self.backend.currentText()
            args = tool_arguments(backend, action, fqbn=self.fqbn.text(), port=self.port.currentText().strip(), baud=self.baud.currentText().strip(), environment=self.environment.text())
            program = shutil.which(args[0])
            if not program:
                raise ValueError(f"Instale {args[0]} e adicione ao PATH antes de executar.")
            if action == "upload" and QMessageBox.question(self, "Gravar firmware", f"Gravar em {self.port.currentText().strip()}?\nConfira a placa e desconecte cargas/atuadores antes da gravação.") != QMessageBox.StandardButton.Yes:
                return
            self.start_process(program, args[1:], " ".join(args), folder)
        except (ValueError, OSError, OverflowError) as exc:
            QMessageBox.warning(self, "Bancada eletrônica", str(exc))

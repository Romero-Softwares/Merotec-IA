"""Offline ATmega328P emulation with embedded V8 and a QtJS compatibility fallback."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from PySide6.QtQml import QJSEngine
from modules.simulation_clock import SimulationClock


SUPPORTED_BOARDS = {"uno", "nano", "nano-old"}


def read_hex(path):
    path = Path(path)
    if path.stat().st_size > 200_000:
        raise ValueError("HEX excede o limite ATmega328P.")
    flash, base, eof, used = bytearray([255] * 32768), 0, False, False
    for line in path.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if not line:
            continue
        if eof or not line.startswith(":"):
            raise ValueError("Registro Intel HEX inválido.")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ValueError("Caracteres Intel HEX inválidos.") from exc
        if len(record) < 5 or record[0] + 5 != len(record) or sum(record) & 255:
            raise ValueError("Checksum ou tamanho Intel HEX inválido.")
        count, address, kind = record[0], (record[1] << 8) | record[2], record[3]
        data = record[4:-1]
        if kind == 0:
            address += base
            if address + count > len(flash):
                raise ValueError("HEX não corresponde à Flash ATmega328P de 32 KB.")
            flash[address:address + count] = data
            used = used or count > 0
        elif kind == 1 and count == 0:
            eof = True
        elif kind in {2, 4} and count == 2:
            base = int.from_bytes(data, "big") << (4 if kind == 2 else 16)
        elif kind not in {3, 5}:
            raise ValueError("Registro HEX não suportado.")
    if not eof or not used:
        raise ValueError("HEX vazio ou sem registro de fim.")
    return bytes(flash)


class AVRMachine:
    """Create and use on a single thread with an existing QCoreApplication."""
    def __init__(self, path, engine="auto"):
        image = read_hex(path)
        self.engine_warning = ""
        self.native_v8 = False
        if engine != "qtjs":
            try:
                from py_mini_racer import MiniRacer
                self.engine = MiniRacer()
                self.native_v8 = True
            except (ImportError, OSError, RuntimeError) as exc:
                if engine == "v8":
                    raise ValueError(f"Runtime V8 indisponível: {exc}") from exc
                self.engine_warning = "Runtime V8 ausente; instale as dependências da IDE para acelerar."
        if not self.native_v8:
            self.engine = QJSEngine()
            self.engine.evaluate("this.globalThis = this;")
        self.engine_name = "V8 incorporado" if self.native_v8 else "QtJS (compatibilidade)"
        self.last_cycles = 0
        folder = Path(__file__).parent
        for source in (folder / "vendor/avr8js.js", folder / "avr_simulator.js"):
            self.evaluate(source.read_text(encoding="utf-8"), str(source), raw=True)
        self.initial = self.evaluate("createSimulator(" + json.dumps(list(image)) + ")")

    def evaluate(self, code, filename="Merotec simulator", raw=False):
        if self.native_v8:
            try:
                result = self.engine.eval(code if raw else "JSON.stringify({value:(" + code + ")})", timeout_sec=2, max_memory=256 * 1024 * 1024)
                return None if raw else json.loads(result).get("value")
            except Exception as exc:
                raise ValueError(f"Simulador AVR: {exc}") from exc
        value = self.engine.evaluate(code, filename)
        if value.isError():
            raise ValueError(f"Simulador AVR: {value.toString()} (linha {value.property('lineNumber').toInt()})")
        return value.toVariant()

    def advance(self, cycles=160000):
        if not isinstance(cycles, int) or not 0 <= cycles <= 500000:
            raise ValueError("Passo deve conter 0 a 500000 ciclos.")
        state = self.evaluate(f"simulator.advance({cycles})")
        self.last_cycles = int(state["cycles"])
        return state

    def close(self):
        if self.native_v8 and self.engine is not None:
            self.engine.close()
        self.engine = None

    def set_input(self, pin, value):
        if not isinstance(pin, int) or not 0 <= pin < 20 or value not in (None, True, False):
            raise ValueError("Entrada digital inválida.")
        self.evaluate(f"simulator.input({pin}, {json.dumps(value)})")

    def set_analog(self, channel, value):
        if not isinstance(channel, int) or not 0 <= channel < 6 or not isinstance(value, int) or not 0 <= value <= 1023:
            raise ValueError("Entrada ADC inválida.")
        self.evaluate(f"simulator.analog({channel}, {value})")

    def send_serial(self, text):
        self.evaluate("simulator.receive(" + json.dumps(list(text.encode("utf-8"))) + ")")


class SimulationWorker(QObject):
    frame = Signal(object)
    failed = Signal(str)
    ready = Signal()
    paused = Signal()

    def __init__(self):
        super().__init__()
        self.machine = None
        self.timer = None
        self.cycles_per_tick = 160000
        self.clock = SimulationClock()
        self.running = False

    @Slot(str)
    def load(self, path):
        try:
            if self.timer is None:
                self.timer = QTimer(self)
                self.timer.setSingleShot(True)
                self.timer.timeout.connect(self.tick)
            self.timer.stop()
            self.running = False
            if self.machine:
                self.machine.close()
            self.machine = AVRMachine(path)
            self.clock = SimulationClock(self.clock.speed)
            self.emit_frame(self.machine.initial)
            self.ready.emit()
        except Exception as exc:
            self.machine = None
            self.failed.emit(str(exc))

    @Slot()
    def resume(self):
        if self.machine:
            self.running = True
            self.clock.resume(self.machine.last_cycles)
            self.timer.start(0)

    @Slot()
    def pause(self):
        self.running = False
        if self.timer:
            self.timer.stop()
        if self.machine:
            self.clock.pause(self.machine.last_cycles)
        self.paused.emit()

    @Slot()
    def tick(self):
        if not self.machine:
            return
        try:
            cycles = self.clock.due(self.machine.last_cycles) if self.running else self.cycles_per_tick
            if self.running and self.clock.speed and cycles < 160000:
                wait = math.ceil((160000 - cycles) / (16_000_000 * self.clock.speed) * 1000)
                self.timer.start(max(1, wait))
                return
            self.emit_frame(self.machine.advance(cycles))
            if self.running:
                self.timer.start(0)
        except Exception as exc:
            self.pause()
            self.failed.emit(str(exc))

    def emit_frame(self, state):
        state.update(self.clock.metrics(int(state["cycles"])))
        state["engine"] = self.machine.engine_name
        state["engine_warning"] = self.machine.engine_warning
        self.frame.emit(state)

    @Slot(object)
    def change(self, command):
        if not self.machine:
            return
        try:
            kind, *values = command
            if kind == "digital":
                self.machine.set_input(*values)
            elif kind == "analog":
                self.machine.set_analog(*values)
            elif kind == "serial":
                self.machine.send_serial(*values)
            elif kind == "speed":
                self.clock.set_speed(values[0], self.machine.last_cycles)
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot()
    def shutdown(self):
        self.pause()
        if self.machine:
            self.machine.close()
        self.machine = None  # Destroy QJSEngine on its owner thread.

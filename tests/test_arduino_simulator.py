import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

FIXTURE = Path(__file__).parent / "fixtures/arduino_sim/firmware.hex"
HAS_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_QT, "PySide6 indisponível")
class AVRSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_compiled_code_controls_pullup_led_adc_pwm_interrupts_and_uart(self):
        from modules.avr_simulator import AVRMachine
        machine = AVRMachine(FIXTURE)
        initial = machine.advance(160000)
        self.assertEqual("INPUT_PULLUP", initial["pins"][2]["mode"])
        self.assertTrue(initial["pins"][2]["level"])
        self.assertFalse(initial["pins"][13]["level"])
        machine.set_input(2, False)
        machine.set_analog(0, 512)
        machine.send_serial("OK")
        states = [machine.advance(160000) for _ in range(6)]
        self.assertTrue(states[-1]["pins"][13]["level"])
        self.assertTrue(.4 < states[-1]["pins"][9]["duty"] < .6)
        self.assertTrue(any(s["pins"][3]["level"] for s in states))
        self.assertTrue(any(not s["pins"][3]["level"] for s in states))
        serial = bytes(int(b) for state in states for b in state["serial"])
        self.assertIn(b"OK", serial)
        self.assertIn(b"A=512 E=1", serial)
        machine.set_input(2, None)
        self.assertFalse(machine.advance(160000)["pins"][13]["level"])

    def test_input_output_contention_is_reported(self):
        from modules.avr_simulator import AVRMachine
        machine = AVRMachine(FIXTURE)
        machine.advance(160000)
        machine.set_input(13, True)
        self.assertTrue(machine.advance(1000)["pins"][13]["conflict"])

    def test_invalid_hex_checksum_eof_and_flash_bounds_are_rejected(self):
        from modules.avr_simulator import read_hex
        for content in (":00000001FE\n", ":0100000000FF\n", ":020000040001F9\n:0100000000FF\n:00000001FF\n"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "invalid.hex"
                path.write_text(content, encoding="ascii")
                with self.assertRaises(ValueError):
                    read_hex(path)

    def test_bounded_steps_and_invalid_inputs(self):
        from modules.avr_simulator import AVRMachine
        machine = AVRMachine(FIXTURE)
        for cycles in (-1, 500001, "100"):
            with self.assertRaises(ValueError):
                machine.advance(cycles)
        with self.assertRaises(ValueError):
            machine.set_input(20, True)
        with self.assertRaises(ValueError):
            machine.set_analog(0, 1024)
        self.assertEqual(0, machine.advance(0)["cycles"])

    def test_worker_paces_cycles_from_elapsed_time_instead_of_fixed_timer_batches(self):
        from modules.avr_simulator import SimulationWorker
        from modules.simulation_clock import SimulationClock
        now = [0.0]
        worker = SimulationWorker()
        worker.clock = SimulationClock(now=lambda: now[0])
        worker.timer = Mock()
        worker.machine = Mock(last_cycles=0, engine_name="fixture", engine_warning="")
        def advance(cycles):
            worker.machine.last_cycles += cycles
            return {"cycles": worker.machine.last_cycles}
        worker.machine.advance.side_effect = advance
        worker.resume()
        now[0] = .020
        worker.tick()
        worker.machine.advance.assert_called_once_with(320000)
        worker.pause()
        now[0] = 50
        worker.resume()
        worker.tick()
        self.assertEqual(1, worker.machine.advance.call_count)

    @unittest.skipUnless(importlib.util.find_spec("py_mini_racer"), "V8 opcional não instalado")
    def test_fast_runtime_preserves_same_firmware_behavior_as_qt_fallback(self):
        from modules.avr_simulator import AVRMachine
        fast = AVRMachine(FIXTURE, engine="v8")
        slow = AVRMachine(FIXTURE, engine="qtjs")
        self.addCleanup(fast.close)
        self.addCleanup(slow.close)
        self.assertEqual("V8 incorporado", fast.engine_name)
        for machine in (fast, slow):
            machine.set_input(2, False)
            machine.set_analog(0, 512)
        a, b = fast.advance(320000), slow.advance(320000)
        self.assertEqual(a["cycles"], b["cycles"])
        self.assertEqual(a["pins"], b["pins"])
        self.assertEqual(a["serial"], b["serial"])


@unittest.skipUnless(HAS_QT, "PySide6 indisponível")
class ArduinoSimulatorQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate):
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtTest import QTest
        timer = QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 10000:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_load_pause_step_reset_and_clean_shutdown(self):
        from modules.qt_arduino_simulator import ArduinoSimulator
        from PySide6.QtTest import QTest
        widget = ArduinoSimulator(FIXTURE.parent)
        self.addCleanup(widget.shutdown)
        widget.load_hex(FIXTURE)
        self.wait_until(lambda: widget.state and widget.state["cycles"] >= 160000)
        widget.pause()
        self.wait_until(lambda: widget.is_paused)
        cycles = widget.state["cycles"]
        QTest.qWait(100)
        self.assertEqual(cycles, widget.state["cycles"])
        widget.step()
        self.wait_until(lambda: widget.state["cycles"] > cycles)
        self.assertFalse(widget.running)
        widget.reset()
        self.wait_until(lambda: widget.state and widget.state["cycles"] < cycles)
        widget.shutdown()
        self.assertIsNone(widget.thread)

    def test_panels_mapping_inputs_and_serial_can_be_selected(self):
        from modules.qt_arduino_simulator import ArduinoSimulator, PANELS
        widget = ArduinoSimulator(FIXTURE.parent)
        for name, pins in PANELS.items():
            widget.panel.setCurrentText(name)
            self.assertEqual(pins, [control.value() for control in widget.pin_controls])
        widget.panel.setCurrentText("LEDs (5)")
        self.assertEqual([2, 3, 4, 5, 6], [c.value() for c in widget.pin_controls])
        widget.momentary.pressed.emit()
        self.assertFalse(widget.digital_controls[2].currentData())
        widget.momentary.released.emit()
        self.assertIsNone(widget.digital_controls[2].currentData())
        self.assertFalse(widget.load_hex(FIXTURE, "mega2560"))
        self.assertIsNone(widget.thread)

    def test_bad_hex_is_visible_and_worker_can_close(self):
        from modules.qt_arduino_simulator import ArduinoSimulator
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.hex"
            path.write_text("bad", encoding="ascii")
            widget = ArduinoSimulator(folder)
            self.addCleanup(widget.shutdown)
            widget.load_hex(path)
            self.wait_until(lambda: "inválido" in widget.status.text())
            self.assertFalse(widget.running)

    def test_workbench_simulation_build_does_not_use_upload_or_serial(self):
        from PySide6.QtWidgets import QMainWindow
        from modules.qt_electronics import ElectronicsDialog
        with tempfile.TemporaryDirectory() as folder:
            ide = QMainWindow()
            ide.workspace = Path(folder)
            dialog = ElectronicsDialog(ide)
            dialog.start_process = Mock(return_value=True)
            with patch("modules.native_firmware.configuration", return_value={"board": "uno"}):
                dialog.start_simulation()
            args = dialog.start_process.call_args.args[1]
            self.assertIn("build", args)
            self.assertNotIn("upload", args)
            self.assertNotIn("--port", args)
            dialog.last_exit_code = 1
            with patch.object(dialog.simulator, "load_hex") as load:
                dialog.simulation_built(Path(folder), "uno")
                load.assert_not_called()
            dialog.reject()
            ide.close()

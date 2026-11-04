import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.pcb_diagnostics import diagnose, save_report, REPORT_DIR


class PCBDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("hardware/bom.csv", "Reference,Quantity,Value,Footprint\nU1,1,MCU,QFN32\n")
        self.write("hardware/pinout.csv", "Signal,MCU_Pin,Direction,Voltage\nLED,PA1,output,3.3\n")
        self.write("tests/functional-tests.csv", "Test,Requirement,Min,Max,Measured,Unit,Evidence\nRail,REQ-1,3.1,3.5,3.3,V,meter-log-1\n")
        self.architecture = {"schema_version": 1,
            "rails": [{"id": "3V3", "voltage_v": 3.3, "max_current_ma": 100}],
            "blocks": [dict(id=name, rail="3V3", current_ma=30, supply_min_v=3, supply_max_v=3.6,
                            logic_voltage_v=3.3, max_input_v=3.6) for name in ("mcu", "sensor")],
            "interfaces": [{"from": "mcu", "to": "sensor"}]}
        self.save_architecture()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def save_architecture(self):
        self.write("hardware/architecture.json", json.dumps(self.architecture))

    def test_local_checks_pass_but_missing_cad_is_pending(self):
        report = diagnose(self.root)
        self.assertEqual("pending", report["status"])
        self.assertEqual(0, report["counts"]["fail"])
        self.assertGreater(report["counts"]["pass"], 5)
        path = save_report(self.root, report)
        self.assertIn("Não certifica hardware", path.read_text(encoding="utf-8"))
        self.assertEqual(report, json.loads((self.root / REPORT_DIR / "report.json").read_text(encoding="utf-8")))

    def test_overcurrent_voltage_and_logic_mismatch_fail(self):
        self.architecture["rails"][0]["max_current_ma"] = 10
        self.architecture["blocks"][0]["supply_min_v"] = 3.4
        self.architecture["blocks"][0]["logic_voltage_v"] = 5
        self.save_architecture()
        report = diagnose(self.root)
        self.assertEqual("fail", report["status"])
        self.assertEqual(3, report["counts"]["fail"])

    def test_duplicate_bom_and_pin_allocations_fail(self):
        self.write("hardware/bom.csv", "Reference,Quantity,Value,Footprint\nR1,1,1k,0603\nR1,1,2k,0603\n")
        self.write("hardware/pinout.csv", "Signal,MCU_Pin,Direction,Voltage\nA,PA1,output,3.3\nB,PA1,input,3.3\n")
        self.assertEqual(2, diagnose(self.root)["counts"]["fail"])

    def test_missing_measurement_is_pending_and_out_of_range_fails(self):
        self.write("tests/functional-tests.csv", "Test,Requirement,Min,Max,Measured,Unit,Evidence\nA,R1,0,5,,V,\nB,R2,0,5,6,V,log\nC,R3,-5,-1,-1,V,log\n")
        report = diagnose(self.root)
        self.assertEqual(1, report["counts"]["fail"])
        self.assertTrue(any(f["check"].endswith(": A") and f["status"] == "pending" for f in report["findings"]))
        self.assertTrue(any(f["check"].endswith(": C") and f["status"] == "pass" for f in report["findings"]))

    def test_malformed_architecture_does_not_abort_other_checks(self):
        for data in ([], {"schema_version": 1, "rails": [None], "blocks": [], "interfaces": []},
                     {**self.architecture, "interfaces": [{"from": "unknown", "to": "mcu"}]}):
            self.write("hardware/architecture.json", json.dumps(data))
            report = diagnose(self.root)
            self.assertEqual("fail", report["status"])
            self.assertTrue(any(f["check"].startswith("Ensaio") for f in report["findings"]))

    def test_empty_project_never_passes(self):
        with tempfile.TemporaryDirectory() as empty:
            report = diagnose(empty)
        self.assertEqual("pending", report["status"])
        self.assertEqual(0, report["counts"]["pass"])

    def test_missing_kicad_never_passes(self):
        self.write("board.kicad_pcb", "fixture")
        with patch("modules.pcb_diagnostics.shutil.which", return_value=None):
            report = diagnose(self.root, kicad=True)
        self.assertTrue(any(f["check"].startswith("DRC") and f["status"] == "pending" for f in report["findings"]))

    def test_kicad_results_success_violation_timeout_and_error(self):
        self.write("design board.kicad_pcb", "fixture")
        for outcome in (0, 5, "timeout", "error", "no-report"):
            def run(args, **kwargs):
                self.assertIn("--exit-code-violations", args)
                self.assertNotIn("shell", kwargs)
                if outcome == "timeout":
                    raise subprocess.TimeoutExpired(args, 1)
                if outcome == "error":
                    raise OSError("tool failed")
                if outcome != "no-report":
                    Path(args[args.index("--output") + 1]).write_text("DRC evidence", encoding="utf-8")
                return subprocess.CompletedProcess(args, outcome if isinstance(outcome, int) else 0, "", "")
            with self.subTest(outcome=outcome), patch("modules.pcb_diagnostics.shutil.which", return_value="kicad-cli"), patch("modules.pcb_diagnostics.subprocess.run", side_effect=run):
                report = diagnose(self.root, kicad=True)
            self.assertEqual("pass" if outcome == 0 else "fail", report["status"])

    def test_invalid_numeric_limits_and_bom_headers_fail(self):
        self.write("hardware/bom.csv", "Wrong\nvalue\n")
        self.write("tests/functional-tests.csv", "Test,Requirement,Min,Max,Measured,Unit,Evidence\nA,R1,0,inf,1,V,log\n")
        self.assertEqual(2, diagnose(self.root)["counts"]["fail"])

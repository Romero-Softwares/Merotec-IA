import copy
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from modules import native_pcb as cad
from modules.pcb_diagnostics import diagnose


def example():
    design = cad.new_design()
    cad.add_component(design, "R_0603", "1k", 20, 20)
    cad.add_component(design, "R_0603", "2k", 40, 20)
    for c in design["components"]:
        c["nets"] = {"1": "A", "2": "B"}
    design["tracks"] = [
        {"from": "R1.1", "to": "R2.1", "width": .3, "points": [[19.2, 15], [39.2, 15]]},
        {"from": "R1.2", "to": "R2.2", "width": .3, "points": [[20.8, 25], [40.8, 25]]}]
    return design


class NativePCBTests(unittest.TestCase):
    def codes(self, design):
        return {f["code"] for f in cad.checks(design)}

    def test_routed_design_passes_without_external_tool(self):
        with patch("subprocess.run", side_effect=AssertionError("External tool forbidden")):
            self.assertEqual([], cad.checks(example()))

    def test_connectivity_is_graph_not_track_count(self):
        d = example()
        d["tracks"] = [d["tracks"][0], copy.deepcopy(d["tracks"][0])]
        self.assertIn("DRC_UNROUTED", self.codes(d))

    def test_crossing_tracks_and_wrong_net_fail(self):
        d = example()
        d["tracks"][0]["points"] = [[30, 20]]
        d["tracks"][1]["points"] = [[30, 20]]
        self.assertIn("DRC_TRACK_CLEARANCE", self.codes(d))
        d["tracks"][0]["to"] = "R2.2"
        self.assertIn("DRC_SHORT", self.codes(d))

    def test_width_edges_and_component_overlap(self):
        d = example()
        d["tracks"][0]["width"] = .1
        d["components"][0]["x"] = -.1
        self.assertTrue({"DRC_WIDTH", "DRC_EDGE"} <= self.codes(d))
        d = example()
        d["components"][1]["x"] = 21.6
        self.assertIn("DRC_PAD_CLEARANCE", self.codes(d))

    def test_erc_unassigned_drivers_and_nc(self):
        d = example()
        d["components"][0]["roles"]["1"] = "output"
        d["components"][1]["roles"]["1"] = "output"
        d["components"][0]["roles"]["2"] = "nc"
        self.assertTrue({"ERC_DRIVER_CONFLICT", "ERC_NC_CONNECTED"} <= self.codes(d))
        d["components"][0]["nets"]["2"] = ""
        d["components"][0]["roles"]["2"] = "input"
        self.assertIn("ERC_UNASSIGNED", self.codes(d))

    def test_missing_input_and_power_drivers(self):
        d = example()
        d["components"][0]["roles"] = {"1": "input", "2": "power_in"}
        self.assertTrue({"ERC_NO_DRIVER", "ERC_NO_POWER"} <= self.codes(d))

    def test_atomic_roundtrip_and_failed_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "design.mpcb.json"
            d = example()
            cad.save(path, d)
            self.assertEqual(d, cad.load(path))
            original = path.read_bytes()
            with patch("os.replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
                cad.save(path, d)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([path], list(Path(folder).iterdir()))

    def test_invalid_documents_and_unknown_footprints_are_rejected(self):
        for mutate in (lambda d: d.update(version=99),
                       lambda d: d["components"][0].update(kind="unknown"),
                       lambda d: d["components"][0].update(x=float("nan")),
                       lambda d: d["components"][0].update(x="20"),
                       lambda d: d["tracks"][0].update(to="MISSING.1")):
            d = example()
            mutate(d)
            with self.assertRaises(ValueError):
                cad.validate(d)

    def test_rotation_and_rectangular_pad_geometry(self):
        d = example()
        d["components"][0]["rotation"] = 90
        self.assertAlmostEqual(20, cad.pad(d, "R1.1")[0])
        self.assertAlmostEqual(19.2, cad.pad(d, "R1.1")[1])
        d = cad.new_design()
        cad.add_component(d, "SOIC_8", "generic", 20, 20)
        self.assertNotIn("DRC_PAD_CLEARANCE", self.codes(d))
        self.assertEqual(0, cad.segment_distance((0, 0), (2, 2), (0, 2), (2, 0)))
        self.assertEqual(1, cad.segment_distance((0, 0), (2, 0), (0, 1), (2, 1)))

    def test_delete_removes_tracks_and_history_restores(self):
        d = example()
        history = cad.History()
        history.record(d)
        cad.remove_component(d, "R1")
        self.assertEqual([], d["tracks"])
        d = history.undo(d)
        self.assertEqual(2, len(d["tracks"]))
        d = history.redo(d)
        self.assertEqual(1, len(d["components"]))

    def test_exports_escape_svg_and_csv(self):
        d = example()
        d["components"][0]["value"] = '<1k & "test",value>'
        ElementTree.fromstring(cad.export_svg(d))
        self.assertIn("&lt;1k", cad.export_svg(d))
        self.assertIn('"<1k & ""test"",value>"', cad.export_bom(d))

    def test_diagnostics_recognizes_native_design_without_kicad(self):
        with tempfile.TemporaryDirectory() as folder:
            cad.save(Path(folder) / "design.mpcb.json", example())
            with patch("subprocess.run", side_effect=AssertionError("External tool forbidden")):
                report = diagnose(folder)
            self.assertTrue(any(f["check"].startswith("ERC/DRC nativo") and f["status"] == "pass" for f in report["findings"]))
            self.assertFalse(any(f["check"] == "KiCad ERC/DRC" for f in report["findings"]))

    def test_standalone_diagnostic_worker_loads_native_engine(self):
        from modules import pcb_diagnostics
        with tempfile.TemporaryDirectory() as folder:
            cad.save(Path(folder) / "design.mpcb.json", cad.demo_design())
            result = subprocess.run([sys.executable, pcb_diagnostics.__file__, folder], cwd=folder,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(2, result.returncode, result.stderr)
            report = json.loads((Path(folder) / ".merotec_system_ai/pcb_diagnostics/report.json").read_text(encoding="utf-8"))
            self.assertTrue(any(f["status"] == "pass" and f["check"].startswith("ERC/DRC nativo") for f in report["findings"]))


class NativePCBQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PySide6 indisponível")
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_routes_undo_redo_and_keeps_design_on_failed_load(self):
        from PySide6.QtWidgets import QMainWindow
        from modules.qt_native_pcb import NativePCBWindow
        with tempfile.TemporaryDirectory() as folder:
            ide = QMainWindow()
            ide.workspace = Path(folder)
            window = NativePCBWindow(ide)
            window.design = example()
            window.design["tracks"] = []
            window.mode.setCurrentIndex(1)
            window.route_endpoint("R1.1")
            window.route_points = [[19.2, 15], [39.2, 15]]
            window.route_endpoint("R2.1")
            self.assertEqual(1, len(window.design["tracks"]))
            window.undo()
            self.assertEqual(0, len(window.design["tracks"]))
            window.redo()
            self.assertEqual(1, len(window.design["tracks"]))
            window.path = Path(folder) / "test.mpcb.json"
            self.assertTrue(window.save())
            before = copy.deepcopy(window.design)
            invalid = Path(folder) / "bad.mpcb.json"
            invalid.write_text('{}', encoding="utf-8")
            with patch("modules.qt_native_pcb.QFileDialog.getOpenFileName", return_value=(str(invalid), "")), patch.object(window, "warning") as warning:
                window.open()
                warning.assert_called_once()
            self.assertEqual(before, window.design)
            self.assertTrue(window.scene.items())
            window.close()
            ide.close()

    def test_editor_check_worker_reports_snapshot_and_stale_results(self):
        from modules.qt_native_pcb import CheckJob, NativePCBWindow
        from PySide6.QtWidgets import QMainWindow
        with tempfile.TemporaryDirectory() as folder:
            ide = QMainWindow()
            ide.workspace = Path(folder)
            window = NativePCBWindow(ide)
            d = example()
            job = CheckJob(d)
            results = []
            job.signals.done.connect(results.append)
            job.run()
            self.assertEqual([], results[0][1])
            window.receive_checks(results[0])
            self.assertIn("mudou durante", window.report.toPlainText())
            window.close()
            ide.close()

    def test_background_check_returns_to_editor_event_loop(self):
        from PySide6.QtWidgets import QMainWindow
        from PySide6.QtTest import QTest
        from modules.qt_native_pcb import NativePCBWindow
        with tempfile.TemporaryDirectory() as folder:
            ide = QMainWindow()
            ide.workspace = Path(folder)
            window = NativePCBWindow(ide)
            window.design = cad.demo_design()
            window.run_checks()
            for _ in range(100):
                if window.check_job is None:
                    break
                QTest.qWait(10)
            self.assertIsNone(window.check_job)
            self.assertIn("Nenhuma violação", window.report.toPlainText())
            window.close()
            ide.close()

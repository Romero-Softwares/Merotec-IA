import unittest

from modules.simulation_clock import SimulationClock


class SimulationClockTests(unittest.TestCase):
    def setUp(self):
        self.time = 0.0
        self.clock = SimulationClock(now=lambda: self.time)

    def test_twenty_real_ms_require_twenty_virtual_ms_at_one_x(self):
        self.clock.resume(0)
        self.time = .020
        self.assertEqual(320000, self.clock.due(0))
        self.assertEqual(0, self.clock.due(320000))
        self.assertAlmostEqual(1, self.clock.metrics(320000)["speed_ratio"])

    def test_pause_excludes_elapsed_wall_time_and_resume_does_not_jump(self):
        self.clock.resume(0)
        self.time = 1
        self.clock.pause(16_000_000)
        self.time = 100
        self.assertEqual(0, self.clock.due(16_000_000))
        self.clock.resume(16_000_000)
        self.assertEqual(0, self.clock.due(16_000_000))
        self.time = 100.25
        self.assertEqual(500000, self.clock.due(16_000_000))
        metrics = self.clock.metrics(20_000_000)
        self.assertAlmostEqual(1250, metrics["wall_ms"])
        self.assertAlmostEqual(1, metrics["speed_ratio"])

    def test_speed_changes_reanchor_without_skipping_instructions(self):
        self.clock.resume(0)
        self.time = .01
        self.clock.set_speed(.5, 160000)
        self.assertEqual(0, self.clock.due(160000))
        self.time = .03
        self.assertAlmostEqual(160000, self.clock.due(160000), delta=1)
        self.clock.set_speed(2, 320000)
        self.time = .04
        self.assertAlmostEqual(320000, self.clock.due(320000), delta=1)
        self.clock.set_speed(0, 640000)
        self.assertEqual(500000, self.clock.due(640000))

    def test_invalid_speed_is_rejected(self):
        for value in (-1, 5, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.clock.set_speed(value, 0)

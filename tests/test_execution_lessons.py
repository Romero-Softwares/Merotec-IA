import json
from pathlib import Path
import tempfile
import unittest

from modules.execution_lessons import ExecutionLessons


class ExecutionLessonsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.memory = ExecutionLessons(self.root)

    def test_only_observed_zero_without_timeout_becomes_verified(self):
        for code, timeout in ((None, False), (True, False), (False, False), (1, False), (0, True)):
            with self.subTest(code=code, timeout=timeout):
                entry = self.memory.record("python -m unittest", code, timeout, evidence="72 testes aprovados")
                self.assertNotEqual("verified_success", entry["status"])
        entry = self.memory.record("python -m unittest", 0, strategy="workspace_test_runtime", context="one")
        self.assertEqual("verified_success", entry["status"])
        self.assertTrue(entry["persisted"])
        reopened = ExecutionLessons(self.root).assess_repeat("python -m unittest", context="one")
        self.assertEqual("workspace_test_runtime", reopened["verified_strategy"])
        self.assertFalse(reopened["automatic_execution_authorized"])

    def test_two_failures_pause_even_when_durations_in_output_change(self):
        self.memory.record("python -m unittest", 1, context="same", evidence="duration=120")
        self.assertFalse(self.memory.assess_repeat("python -m unittest", context="same")["should_pause"])
        self.memory.record("python -m unittest", 1, context="same", evidence="duration=180")
        self.assertTrue(self.memory.assess_repeat("python -m unittest", context="same")["should_pause"])
        self.assertFalse(self.memory.assess_repeat("python -m unittest", context="changed")["should_pause"])
        self.assertFalse(self.memory.assess_repeat("python -m unittest tests.other", context="same")["should_pause"])
        self.memory.record("python -m unittest", 0, context="same")
        self.assertFalse(self.memory.assess_repeat("python -m unittest", context="same")["should_pause"])

    def test_verified_alternative_strategy_survives_failure_of_original_strategy(self):
        self.memory.record("python -m unittest", 0, strategy="isolated", context="one")
        for _ in range(2):
            self.memory.record("python -m unittest", 1, strategy="original", context="one")
        result = self.memory.assess_repeat("python -m unittest", context="one")
        self.assertEqual("isolated", result["verified_strategy"])
        self.assertIn("isolated", result["recommendation"])

    def test_memory_redacts_credentials_and_omits_inline_source(self):
        self.memory.record('tool --api-key private-value --token=another-value', 1,
                           evidence="Authorization: Bearer sk-testsecret123456789")
        self.memory.record(["python", "-c", "print('private literal source')"], 0)
        saved = self.memory.path.read_text(encoding="utf-8")
        for value in ("private-value", "another-value", "sk-testsecret123456789", "private literal source"):
            self.assertNotIn(value, saved)
        self.assertIn("sha256", saved)

    def test_corrupt_and_oversized_memory_are_tolerated(self):
        self.memory.path.parent.mkdir()
        self.memory.path.write_text("broken json", encoding="utf-8")
        self.assertIn("Sem execuções", self.memory.format_for_agent())
        self.memory.path.write_text(" " * 256_001, encoding="utf-8")
        self.assertFalse(self.memory.assess_repeat("python -m unittest")["should_pause"])

    def test_retention_is_bounded_and_workspaces_are_separate(self):
        memory = ExecutionLessons(self.root, max_entries=3)
        for index in range(8):
            memory.record(f"tool {index}", 0)
        self.assertEqual(3, len(json.loads(memory.path.read_text(encoding="utf-8"))["entries"]))
        other = self.root / "other"
        other.mkdir()
        self.assertIn("Sem execuções", ExecutionLessons(other).format_for_agent())

    def test_loaded_boolean_exit_code_cannot_claim_verified_success(self):
        self.memory.record("tool", 0)
        payload = json.loads(self.memory.path.read_text(encoding="utf-8"))
        payload["entries"][0]["exit_code"] = False
        self.memory.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertNotIn("[VERIFICADO", self.memory.format_for_agent())

    def test_large_records_remain_below_file_limit_without_discarding_latest_run(self):
        for index in range(45):
            self.memory.record(f"tool {index} " + "Á" * 1500, 1, evidence="é" * 280,
                               cwd="p" * 400, diagnosis="d" * 400, context="c" * 240)
        self.assertLessEqual(self.memory.path.stat().st_size, 256_000)
        entries = json.loads(self.memory.path.read_text(encoding="utf-8"))["entries"]
        self.assertIn("tool 44", entries[-1]["command"])
        self.assertNotIn("Sem execuções", self.memory.format_for_agent())

    def test_malformed_record_fields_cannot_crash_or_block_the_executor(self):
        self.memory.record("tool", 1)
        payload = json.loads(self.memory.path.read_text(encoding="utf-8"))
        payload["entries"][0].update(status=["invalid"], updated_at={"invalid": True}, consecutive_failures=99)
        self.memory.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(self.memory.assess_repeat("tool")["should_pause"])
        self.assertIn("UNCONFIRMED", self.memory.format_for_agent())

    def test_long_shell_command_with_corrected_tail_is_not_the_same_failed_recipe(self):
        common = "powershell -Command " + "x" * 2000
        for _ in range(2):
            self.memory.record(common + " invalid-tail", 1)
        self.assertTrue(self.memory.assess_repeat(common + " invalid-tail")["should_pause"])
        self.assertFalse(self.memory.assess_repeat(common + " corrected-tail")["should_pause"])


if __name__ == "__main__":
    unittest.main()

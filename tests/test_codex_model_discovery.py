import json
import queue
import subprocess
import unittest
from unittest.mock import patch

from modules.codex_model_discovery import ModelDiscoveryError, discover_codex_models


class FakeOutput:
    def __init__(self):
        self.lines = queue.Queue()

    def put(self, value):
        self.lines.put(json.dumps(value) + "\n" if value is not None else None)

    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line

    def close(self):
        self.lines.put(None)


class FakeInput:
    def __init__(self, process):
        self.process = process
        self.closed = False

    def write(self, data):
        for line in data.splitlines():
            message = json.loads(line)
            self.process.sent.append(message)
            self.process.handle(message)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, pages=None, mode="normal"):
        self.pages = list(pages or [{"data": [], "nextCursor": None}])
        self.mode = mode
        self.sent = []
        self.stdin = FakeInput(self)
        self.stdout = FakeOutput()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def handle(self, message):
        if message.get("method") == "initialize":
            self.stdout.put({"id": message["id"], "result": {"userAgent": "fake-codex"}})
        elif message.get("method") == "model/list":
            if self.mode == "silent":
                return
            if self.mode == "closed":
                self.stdout.put(None)
                return
            if self.mode == "error":
                self.stdout.put({"id": message["id"], "error": {"code": -32600, "message": "sensitive-auth-value"}})
                return
            if self.mode == "server-request":
                self.stdout.put({"id": 99, "method": "item/commandExecution/requestApproval", "params": {"command": "never execute"}})
            self.stdout.put({"method": "harmless/notification", "params": {}})
            self.stdout.put({"id": message["id"], "result": self.pages.pop(0)})

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.mode != "stubborn":
            self.returncode = 0
            self.stdout.close()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.close()

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class CodexModelDiscoveryTests(unittest.TestCase):
    def test_exact_model_identifiers_names_and_reasoning_from_all_pages(self):
        process = FakeProcess([
            {"data": [{"id": "picker-row", "model": "gpt-6.1-sol", "displayName": "GPT-6.1 Sol", "isDefault": True,
                       "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [
                           {"reasoningEffort": "medium", "description": "balanced"}, {"reasoningEffort": "high"}]}],
             "nextCursor": "next-page"},
            {"data": [{"id": "another", "model": {"id": "custom-model"}}, {"model": "gpt-6.1-sol"},
                      {"model": "hidden-model", "hidden": True}, None], "nextCursor": None},
        ])
        with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process) as popen:
            models = discover_codex_models("codex.exe")
        self.assertEqual(["gpt-6.1-sol", "custom-model"], [model["id"] for model in models])
        self.assertEqual("GPT-6.1 Sol", models[0]["name"])
        self.assertTrue(models[0]["default"])
        self.assertEqual(["medium", "high"], models[0]["reasoning"])
        self.assertEqual("medium", models[0]["default_reasoning"])
        self.assertEqual(["codex.exe", "app-server", "-c", 'model_provider="openai"'], popen.call_args.args[0])
        calls = [message for message in process.sent if message.get("method") == "model/list"]
        self.assertEqual("next-page", calls[1]["params"]["cursor"])
        self.assertEqual({"initialize", "initialized", "model/list"}, {m["method"] for m in process.sent})
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.terminated)

    def test_empty_catalog_is_valid(self):
        process = FakeProcess()
        with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
            self.assertEqual([], discover_codex_models("codex"))

    def test_server_error_is_sanitized_and_process_is_stopped(self):
        process = FakeProcess(mode="error")
        with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
            with self.assertRaises(ModelDiscoveryError) as error:
                discover_codex_models("codex")
        self.assertNotIn("sensitive-auth-value", str(error.exception))
        self.assertTrue(process.terminated)

    def test_timeout_and_closed_stdout_are_actionable_and_cleaned_up(self):
        for mode in ("silent", "closed"):
            process = FakeProcess(mode=mode)
            with self.subTest(mode=mode), patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
                with self.assertRaises(ModelDiscoveryError) as error:
                    discover_codex_models("codex", timeout=.02)
            self.assertIn("limite" if mode == "silent" else "encerrou", str(error.exception))
            self.assertTrue(process.terminated)

    def test_repeated_cursor_and_invalid_result_are_rejected(self):
        for pages in ([{"data": [], "nextCursor": "same"}, {"data": [], "nextCursor": "same"}], [{"data": {}}]):
            process = FakeProcess(pages)
            with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
                with self.assertRaises(ModelDiscoveryError):
                    discover_codex_models("codex")

    def test_unexpected_server_requests_are_denied_never_approved(self):
        process = FakeProcess(mode="server-request")
        with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
            discover_codex_models("codex")
        replies = [message for message in process.sent if message.get("id") == 99]
        self.assertEqual(-32601, replies[0]["error"]["code"])
        self.assertNotIn("result", replies[0])

    def test_cleanup_kills_process_that_does_not_terminate(self):
        process = FakeProcess(mode="stubborn")
        with patch("modules.codex_model_discovery.subprocess.Popen", return_value=process):
            discover_codex_models("codex")
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_missing_executable_invalid_deadline_and_launch_errors_are_safe(self):
        with patch("modules.codex_model_discovery.subprocess.Popen") as popen:
            for timeout in (0, -1, float("nan"), float("inf")):
                with self.assertRaises(ModelDiscoveryError):
                    discover_codex_models("codex", timeout)
            with self.assertRaises(ModelDiscoveryError):
                discover_codex_models("")
            popen.assert_not_called()
        with patch("modules.codex_model_discovery.subprocess.Popen", side_effect=OSError("private path secret")):
            with self.assertRaises(ModelDiscoveryError) as error:
                discover_codex_models("codex")
        self.assertNotIn("private path secret", str(error.exception))

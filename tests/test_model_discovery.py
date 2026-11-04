import io
import json
import socket
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from modules.model_discovery import (
    ModelDiscoveryError,
    _SameOriginRedirectHandler,
    discover_openai_models,
    normalize_openai_base_url,
)


class ModelDiscoveryTests(unittest.TestCase):
    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_normalization_preserves_custom_prefixes_and_removes_endpoint_suffixes(self):
        for raw, expected in (
            ("", "https://api.openai.com/v1"),
            ("https://api.openai.com/", "https://api.openai.com/v1"),
            ("https://api.openai.com/v1/models/", "https://api.openai.com/v1"),
            ("https://openrouter.ai/api/v1/chat/completions", "https://openrouter.ai/api/v1"),
            ("http://127.0.0.1:1234/v1/responses", "http://127.0.0.1:1234/v1"),
            ("https://example.test/custom/api/v1/", "https://example.test/custom/api/v1"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(expected, normalize_openai_base_url(raw))
        self.assertEqual("https://openrouter.ai/api/v1", normalize_openai_base_url("https://api.openai.com/v1", "sk-or-example"))
        self.assertEqual("https://proxy.test/v1", normalize_openai_base_url("https://proxy.test/v1", "sk-or-example"))

    def test_invalid_urls_are_rejected_without_echoing_credentials(self):
        for value in ("file:///models", "https://user:secret@example.test/v1", "https://api.openai.com/v1?key=secret", "https://api.openai.com/v1#secret", "https://example.test:99999/v1", "https://example.test/\nmodels"):
            with self.subTest(value=value), self.assertRaises(ModelDiscoveryError) as captured:
                normalize_openai_base_url(value)
            self.assertNotIn("secret", str(captured.exception))

    def test_get_models_keeps_unknown_and_non_chat_ids_without_generating(self):
        opener = Mock()
        opener.open.return_value = self.response({"data": [{"id": "z-new-model"}, {"id": "embedding-model"}, {"id": "gpt-exact-snapshot"}, {"id": "z-new-model"}]})
        with patch("modules.model_discovery.urllib.request.build_opener", return_value=opener):
            result = discover_openai_models("https://api.openai.com/v1", "sk-example", timeout=3)
        self.assertEqual(["embedding-model", "gpt-exact-snapshot", "z-new-model"], result)
        request = opener.open.call_args.args[0]
        self.assertEqual("GET", request.get_method())
        self.assertEqual("https://api.openai.com/v1/models", request.full_url)
        self.assertEqual("Bearer sk-example", request.get_header("Authorization"))
        self.assertIsNone(request.data)
        self.assertEqual(3, opener.open.call_args.kwargs["timeout"])

    def test_authless_compatible_server_and_empty_catalog(self):
        opener = Mock()
        opener.open.return_value = self.response({"data": []})
        with patch("modules.model_discovery.urllib.request.build_opener", return_value=opener):
            self.assertEqual([], discover_openai_models("http://localhost:8080/api"))
        self.assertIsNone(opener.open.call_args.args[0].get_header("Authorization"))

    def test_malformed_payload_is_not_mistaken_for_empty_catalog(self):
        for payload in ([], {}, {"data": {}}, {"data": [None]}, {"data": [{"id": 7}]}, {"data": [{"id": " "}]}):
            opener = Mock()
            opener.open.return_value = self.response(payload)
            with self.subTest(payload=payload), patch("modules.model_discovery.urllib.request.build_opener", return_value=opener), self.assertRaises(ModelDiscoveryError):
                discover_openai_models("https://example.test/v1")

    def test_http_network_and_json_errors_do_not_echo_server_secrets(self):
        errors = [urllib.error.HTTPError("https://example.test/v1/models", code, "sk-secret", {}, io.BytesIO(b"sk-secret")) for code in (401, 403, 404, 429, 500)]
        errors.extend([urllib.error.URLError("sk-secret"), socket.timeout("sk-secret")])
        for error in errors:
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(error=type(error).__name__), patch("modules.model_discovery.urllib.request.build_opener", return_value=opener), self.assertRaises(ModelDiscoveryError) as captured:
                discover_openai_models("https://example.test/v1", "sk-secret")
            self.assertNotIn("sk-secret", str(captured.exception))
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"<html>sk-secret</html>")
        with patch("modules.model_discovery.urllib.request.build_opener", return_value=opener), self.assertRaises(ModelDiscoveryError) as captured:
            discover_openai_models("https://example.test/v1", "sk-secret")
        self.assertNotIn("sk-secret", str(captured.exception))

    def test_cross_origin_and_downgrade_redirects_are_rejected(self):
        handler = _SameOriginRedirectHandler()
        request = urllib.request.Request("https://example.test/v1/models", headers={"Authorization": "Bearer sk-secret"})
        for target in ("https://other.test/models", "http://example.test/models", "https://example.test:444/models", "https://example.test:0/models"):
            with self.subTest(target=target), self.assertRaises(ModelDiscoveryError):
                handler.redirect_request(request, None, 302, "redirect", {}, target)
        redirected = handler.redirect_request(request, None, 302, "redirect", {}, "/models")
        self.assertEqual("https://example.test/models", redirected.full_url)
        self.assertEqual("Bearer sk-secret", redirected.get_header("Authorization"))

    def test_invalid_timeout_and_header_are_rejected_before_request(self):
        for timeout in (0, -1, float("nan"), "invalid"):
            with self.subTest(timeout=timeout), self.assertRaises(ModelDiscoveryError):
                discover_openai_models("https://example.test", timeout=timeout)
        with self.assertRaises(ModelDiscoveryError):
            discover_openai_models("https://example.test", "key\nInjected: header")

    def test_response_is_size_limited(self):
        opener = Mock()
        opener.open.return_value = self.response({"data": [{"id": "model"}]})
        with patch("modules.model_discovery.MAX_RESPONSE_BYTES", 10), patch("modules.model_discovery.urllib.request.build_opener", return_value=opener), self.assertRaisesRegex(ModelDiscoveryError, "limite"):
            discover_openai_models("https://example.test/v1")

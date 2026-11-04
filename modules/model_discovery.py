"""Read-only model discovery for OpenAI-compatible APIs.

The catalogue does not infer endpoint capabilities from model names. It preserves
the exact identifiers returned by the configured service and never stores keys.
"""
from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_RESPONSE_BYTES = 5_000_000


class ModelDiscoveryError(ValueError):
    """Safe-to-display discovery failure, without credentials or server bodies."""


def _parsed_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "\\" in parsed.netloc
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError
        parsed.port  # Validate malformed/out-of-range ports before opening a request.
    except (TypeError, ValueError):
        raise ModelDiscoveryError("Informe uma URL HTTP(S) válida, sem credenciais, query ou fragmento.") from None
    return parsed


def normalize_openai_base_url(base_url, api_key=""):
    value = str(base_url or "").strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
    parsed = _parsed_url(value)
    if str(api_key or "").strip().startswith("sk-or-") and parsed.hostname.lower() == "api.openai.com":
        return "https://openrouter.ai/api/v1"
    path = parsed.path.rstrip("/")
    while True:
        suffix = next((item for item in ("/chat/completions", "/responses", "/models") if path.lower().endswith(item)), None)
        if suffix is None:
            break
        path = path[:-len(suffix)].rstrip("/")
    if not path and parsed.hostname.lower() == "api.openai.com":
        path = "/v1"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def _origin(url):
    parsed = _parsed_url(url)
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme.lower() == "https" else 80)
    return parsed.scheme.lower(), parsed.hostname.lower(), port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward an Authorization header to a different service or port."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _origin(req.full_url) != _origin(target):
            raise ModelDiscoveryError("O servidor redirecionou a consulta para outra origem; atualize a URL configurada.")
        return super().redirect_request(req, fp, code, msg, headers, target)


def discover_openai_models(base_url, api_key="", timeout=8):
    """GET ``<base>/models`` and return unique, exact model IDs sorted by ID.

    Only this read-only endpoint is called. Missing authentication is allowed for
    local/compatible services; authenticated services can return a clear 401.
    """
    key = str(api_key or "").strip()
    if "\r" in key or "\n" in key:
        raise ModelDiscoveryError("A chave de API contém caracteres inválidos.")
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ModelDiscoveryError("O tempo limite deve ser um número positivo.") from None
    endpoint = normalize_openai_base_url(base_url, key) + "/models"
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        opener = urllib.request.build_opener(_SameOriginRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelDiscoveryError("O catálogo de modelos excedeu o limite de resposta.")
        payload = json.loads(raw.decode("utf-8-sig"))
    except ModelDiscoveryError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        messages = {
            401: "Chave de API ausente ou inválida para consultar modelos.",
            403: "A conta ou chave não tem permissão para consultar modelos.",
            404: "O endpoint /models não foi encontrado; confira a URL base.",
            429: "O serviço limitou a consulta de modelos. Tente novamente em instantes.",
        }
        raise ModelDiscoveryError(messages.get(status, f"O serviço não conseguiu listar modelos (HTTP {status}).")) from None
    except (socket.timeout, TimeoutError):
        raise ModelDiscoveryError("Tempo esgotado ao consultar modelos. Tente novamente.") from None
    except (urllib.error.URLError, OSError):
        raise ModelDiscoveryError("Não foi possível conectar ao serviço de modelos. Confira a URL e a conexão.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ModelDiscoveryError("O serviço não retornou um catálogo JSON válido.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ModelDiscoveryError("Catálogo inválido: esperado um objeto com a lista data.")
    ids = []
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ModelDiscoveryError("Catálogo inválido: um modelo não contém um identificador textual.")
        ids.append(item["id"].strip())
    return sorted(set(ids))

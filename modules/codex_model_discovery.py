"""Read the current Codex picker catalog without opening a chat or changing config."""
from __future__ import annotations

import json
import os
import queue
from modules.process_runtime import subprocess
import threading
import time


class ModelDiscoveryError(ValueError):
    """A safe, user-facing discovery failure; server output is never echoed."""


def _catalog_entry(item):
    if not isinstance(item, dict) or item.get("hidden") is True:
        return None
    model = item.get("model")
    if isinstance(model, dict):
        model = model.get("id")
    identifier = model if isinstance(model, str) and model.strip() else item.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    identifier = identifier.strip()
    reasoning = []
    for option in item.get("supportedReasoningEfforts") or []:
        value = option.get("reasoningEffort") if isinstance(option, dict) else option
        if isinstance(value, str) and value and value not in reasoning:
            reasoning.append(value)
    return {
        "id": identifier,
        "name": str(item.get("displayName") or identifier),
        "default": item.get("isDefault") is True,
        "reasoning": reasoning,
        "default_reasoning": str(item.get("defaultReasoningEffort") or ""),
    }


def discover_codex_models(executable, timeout=12):
    """Return exact model IDs from paginated app-server ``model/list``.

    Discovery uses the existing local Codex authentication and the same in-memory
    OpenAI provider override as the engine; it does not edit config.toml. It performs only the
    initialization handshake and catalog reads, never starts a thread or turn.
    The total response deadline applies across initialization and all pages.
    """
    if not str(executable or "").strip():
        raise ModelDiscoveryError("Codex não encontrado. Instale ou localize o executável e tente atualizar os modelos.")
    try:
        duration = float(timeout)
        if not 0 < duration <= 120:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ModelDiscoveryError("Prazo de descoberta Codex inválido.") from exc

    process = None
    reader_thread = None
    messages = queue.Queue()
    closed = object()
    deadline = time.monotonic() + duration

    try:
        process = subprocess.Popen(
            [str(executable), "app-server", "-c", 'model_provider="openai"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def read_messages():
            try:
                for line in process.stdout:
                    try:
                        message = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(message, dict):
                        messages.put(message)
            except (OSError, ValueError):
                pass
            finally:
                messages.put(closed)

        reader_thread = threading.Thread(target=read_messages, daemon=True, name="merotec-codex-models")
        reader_thread.start()

        def send(payload):
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def request(method, params, identifier):
            send({"id": identifier, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelDiscoveryError("A descoberta de modelos Codex excedeu o tempo limite. Tente atualizar novamente.")
                try:
                    message = messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise ModelDiscoveryError("A descoberta de modelos Codex excedeu o tempo limite. Tente atualizar novamente.") from exc
                if message is closed:
                    raise ModelDiscoveryError("Codex encerrou antes de informar os modelos. Verifique o login e a instalação.")
                if message.get("id") == identifier and "method" not in message:
                    if "error" in message:
                        raise ModelDiscoveryError("Codex não conseguiu listar os modelos. Verifique o login, a versão do Codex e as regras da conta.")
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise ModelDiscoveryError("Codex retornou um catálogo de modelos inválido.")
                    return result
                if "id" in message and "method" in message:
                    # No discovery operation may grant permissions or execute tools.
                    send({"id": message["id"], "error": {"code": -32601, "message": "Request unavailable during model discovery"}})

        request("initialize", {
            "clientInfo": {"name": "merotec_model_picker", "title": "Merotec model discovery", "version": "1.0.0"},
            "capabilities": {},
        }, 1)
        send({"method": "initialized", "params": {}})
        catalog = {}
        cursor = None
        seen_cursors = set()
        for page in range(100):
            params = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = request("model/list", params, page + 2)
            rows = result.get("data")
            if not isinstance(rows, list):
                raise ModelDiscoveryError("Codex retornou um catálogo de modelos inválido.")
            for item in rows:
                entry = _catalog_entry(item)
                if entry is not None and entry["id"] not in catalog:
                    catalog[entry["id"]] = entry
            cursor = result.get("nextCursor")
            if cursor in (None, ""):
                return list(catalog.values())
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ModelDiscoveryError("A paginação dos modelos Codex é inválida.")
            seen_cursors.add(cursor)
        raise ModelDiscoveryError("O catálogo Codex excedeu o limite de páginas.")
    except ModelDiscoveryError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ModelDiscoveryError("Não foi possível consultar o Codex local. Verifique instalação, login e permissões.") from exc
    finally:
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if reader_thread:
                reader_thread.join(timeout=.2)
            try:
                if process.stdout:
                    process.stdout.close()
            except (OSError, ValueError):
                pass

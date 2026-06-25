"""Ponte estável entre o motor e o navegador interno do Chat Web."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from modules.ai_profiles import normalize_web_url, workspace_session_key


class InternalBrowserWebChatBridge:
    """Mantém uma conversa por projeto sem recarregar a mesma origem a cada ação."""

    def __init__(self, app: Any, profile: dict | None = None) -> None:
        self.app = app
        self.profile = dict(profile or {})
        self.workspace_path = ""
        self.current_url = ""
        self.current_session_key = ""
        self.last_artifacts: dict = {}
        self.lock = threading.RLock()
        # O navegador é da janela principal da IDE. Cancelar não pode descartá-lo.
        self.managed_by_ide = True

    def _entry_url(self) -> str:
        return normalize_web_url(
            self.profile.get("web_chat_url"),
            "https://chatgpt.com/",
        )

    def _decode_event(self, event: dict | None) -> dict:
        if not isinstance(event, dict):
            return {"ok": False, "error": "Resposta inválida do navegador interno."}
        raw = event.get("result", event)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {"response": raw}
        return raw if isinstance(raw, dict) else {"result": raw}

    def _call_ui(self, callback: Callable[[], None], timeout: float = 20.0) -> None:
        done = threading.Event()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        try:
            self.app.after(0, run)
        except Exception as exc:
            raise RuntimeError(
                f"Não consegui agendar a ação no navegador interno: {exc}"
            ) from exc
        if not done.wait(timeout=max(1.0, float(timeout or 20.0))):
            raise TimeoutError("A interface da IDE não respondeu ao preparar o Chat Web.")
        if errors:
            raise RuntimeError(str(errors[0]))

    def _remove_pending_request(self, request_id: str) -> None:
        lock = getattr(self.app, "internal_browser_lock", None)
        requests = getattr(self.app, "internal_browser_requests", None)
        if not request_id or not isinstance(requests, dict):
            return
        try:
            if lock is not None:
                with lock:
                    requests.pop(request_id, None)
            else:
                requests.pop(request_id, None)
        except Exception:
            pass

    def _wait_request(self, action: str, payload: dict, timeout: float) -> dict:
        completed = threading.Event()
        holder: dict = {}

        def receive(event: dict) -> None:
            holder["event"] = event
            completed.set()

        request_id = self.app.request_internal_browser_action(
            action,
            payload=payload,
            callback=receive,
        )
        if not request_id:
            return {"ok": False, "error": "O navegador interno não está disponível."}

        deadline = time.monotonic() + max(10.0, float(timeout or 10.0))
        while not completed.wait(timeout=0.25):
            engine = getattr(self.app, "engine", None)
            if getattr(engine, "cancel_requested", False):
                self._remove_pending_request(request_id)
                return {"ok": False, "error": "Tarefa cancelada."}
            if time.monotonic() >= deadline:
                self._remove_pending_request(request_id)
                return {
                    "ok": False,
                    "error": f"Tempo esgotado aguardando {action} no Chat Web.",
                }
        return self._decode_event(holder.get("event"))

    def _wait_request_with_recovery(self, action: str, payload: dict, timeout: float) -> dict:
        first = self._wait_request(action, payload, timeout)
        if first.get("ok", True) or action != "chat":
            return first
        error = str(first.get("error") or "").lower()
        recoverable = any(
            marker in error
            for marker in (
                "nao esta disponivel",
                "tempo esgotado aguardando chat",
                "navegador interno",
                "webview2",
            )
        )
        if not recoverable:
            return first
        try:
            self._restore_workspace_session(self.workspace_path)
        except Exception as exc:
            return {"ok": False, "error": f"{first.get('error')} Recuperacao falhou: {exc}"}
        time.sleep(0.4)
        second = self._wait_request(action, payload, max(30.0, timeout))
        if not second.get("ok", True):
            second["first_error"] = first.get("error", "")
        return second

    @staticmethod
    def _same_origin(left: str, right: str) -> bool:
        try:
            a = urllib.parse.urlparse(str(left or ""))
            b = urllib.parse.urlparse(str(right or ""))
            return bool(
                a.scheme
                and b.scheme
                and a.scheme.lower() == b.scheme.lower()
                and (a.hostname or "").lower() == (b.hostname or "").lower()
                and (a.port or None) == (b.port or None)
            )
        except Exception:
            return False

    @staticmethod
    def _is_entry_url(url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(str(url or ""))
            return parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
        except Exception:
            return False

    @staticmethod
    def _provider_message_margin(entry_url: str) -> int:
        host = (urllib.parse.urlparse(str(entry_url or "")).hostname or "").lower()
        if "gemini.google" in host:
            return 1800
        if "chatgpt.com" in host or "chat.openai.com" in host:
            return 1200
        return 1600

    def _message_limit(self) -> int:
        configured = int(self.profile.get("web_chat_message_chars", 28000) or 28000)
        return max(8000, configured - self._provider_message_margin(self._entry_url()))

    @staticmethod
    def _compact_single_message(message: str, limit: int) -> tuple[str, bool]:
        message = str(message or "").strip()
        if len(message) <= limit:
            return message, False
        head = max(3600, int(limit * 0.68))
        tail = max(2200, limit - head - 220)
        compacted = (
            message[:head].rstrip()
            + "\n\n[Contexto intermediario omitido pela IDE para estabilidade do Chat Web. "
            "Priorize a missao, os arquivos citados, erros, testes e as instrucoes finais.]\n\n"
            + message[-tail:].lstrip()
        )
        return compacted[:limit], True

    def _should_navigate(self, current: str, target: str) -> bool:
        """Só navega quando troca origem ou uma sessão explícita de projeto."""
        current = str(current or "").strip()
        target = str(target or "").strip()
        if not current or current == "about:blank":
            return True
        if not target:
            return False
        if current.rstrip("/") == target.rstrip("/"):
            return False
        if not self._same_origin(current, target):
            return True
        # Uma URL de entrada do provedor configurado não deve
        # reiniciar uma conversa que já está na mesma origem.
        if self._is_entry_url(target):
            return False
        # Um alvo não-raiz é uma sessão explicitamente salva para outro projeto.
        return True

    def _restore_workspace_session(self, workspace_path: str | Path | None) -> str:
        """Restaura a conversa sem esperar resposta para a navegação.

        O runtime estável do navegador trata `navigate` como comando de UI e
        emite `navigated`, não um `browser_result`. A versão anterior da ponte
        passou a esperar um browser_result de navigate e travava a tarefa antes
        do envio. O loop do runtime é serial: a próxima ação `chat` só é lida
        depois que a página terminou de carregar.
        """
        workspace = str(
            workspace_path
            or self.workspace_path
            or getattr(self.app, "current_workspace", "")
            or Path.cwd()
        )
        self.workspace_path = workspace
        entry_url = self._entry_url()
        # A sessão precisa incluir a origem configurada. Assim, alternar entre
        # Origens diferentes não misturam conversas do mesmo projeto.
        session_key = workspace_session_key(workspace, "web_chat", entry_url)
        restore_session = bool(self.profile.get("web_chat_restore_project_session", True))
        target_getter = getattr(self.app, "web_chat_target_for_workspace", None)
        target = target_getter(workspace) if restore_session and callable(target_getter) else entry_url
        target = normalize_web_url(target or entry_url, entry_url)

        process = getattr(self.app, "internal_browser_process", None)
        current = str(
            getattr(self.app, "internal_browser_url", "")
            or self.current_url
            or ""
        )

        if process is None or process.poll() is not None:
            self._call_ui(
                lambda: self.app.open_internal_browser(target, source="Chat Web")
            )
            ready = getattr(self.app, "internal_browser_ready_event", None)
            if ready is not None and not ready.wait(timeout=35):
                raise TimeoutError("O WebView2 não ficou pronto para o Chat Web.")
        elif self._should_navigate(current, target):
            # Usa o mesmo caminho que os controles da IDE usam. Não registra
            # callback pendente porque o runtime estável responde com
            # `navigated`, e não com `browser_result`.
            self._call_ui(
                lambda: self.app.open_internal_browser(target, source="Chat Web")
            )
            time.sleep(0.15)
        elif current:
            target = current

        self.current_session_key = session_key
        self.current_url = str(getattr(self.app, "internal_browser_url", "") or target)
        return self.current_url

    @staticmethod
    def _chunks(text: str, limit: int) -> list[str]:
        text = str(text or "")
        limit = max(4000, int(limit or 28000))
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = remaining.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        return chunks

    def _remember(self, url: str, title: str = "") -> None:
        if not url:
            return
        self.current_url = url
        callback = getattr(self.app, "remember_internal_browser_chat_url", None)
        if callable(callback):
            try:
                self.app.after(0, lambda: callback(url, title))
            except Exception:
                pass

    def ensure_workspace_session(self, workspace_path: str | Path | None) -> str:
        with self.lock:
            return self._restore_workspace_session(workspace_path)

    def chat(
        self,
        prompt: str,
        *,
        workspace_path: str | Path | None = None,
        attachments: list[dict] | None = None,
        timeout: int | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> dict:
        """Envia uma única mensagem por tarefa.

        O navegador não deve receber várias mensagens automáticas de contexto:
        isso polui a conversa e pode interromper a automação em provedores como
        alguns provedores web. O motor já compacta o contexto; aqui apenas aplicamos um corte
        seguro caso uma mensagem excepcional ultrapasse o limite configurado.
        """
        with self.lock:
            self._restore_workspace_session(workspace_path)

            message = str(prompt or "").strip()
            message_limit = max(
                8000,
                int(self.profile.get("web_chat_message_chars", 28000) or 28000),
            )
            if len(message) > message_limit:
                head = max(3200, int(message_limit * 0.70))
                tail = max(1800, message_limit - head - 130)
                message = (
                    message[:head].rstrip()
                    + "\n\n[Contexto central reduzido pela IDE para envio único.]\n\n"
                    + message[-tail:].lstrip()
                )
                if stream_callback:
                    stream_callback(
                        "Chat Web: contexto reduzido para uma única mensagem.\n"
                    )

            message, dynamically_compacted = self._compact_single_message(message, self._message_limit())
            if dynamically_compacted and stream_callback:
                stream_callback(
                    "Chat Web: contexto ajustado dinamicamente para uma mensagem mais estavel.\n"
                )

            response_timeout = max(
                30,
                int(
                    timeout
                    or self.profile.get("web_chat_timeout_seconds", 300)
                    or 300
                ),
            )
            result = self._wait_request_with_recovery(
                "chat",
                {
                    "prompt": message,
                    "wait_response": True,
                    "timeout": response_timeout,
                    "attachments": attachments or [],
                    "session_key": self.current_session_key,
                },
                timeout=response_timeout + 45,
            )
            url = str(result.get("url") or self.current_url)
            self._remember(url, str(result.get("title") or ""))

            if isinstance(result.get("artifacts"), dict):
                self.last_artifacts = result["artifacts"]
            else:
                self.last_artifacts = {}
            return result

    def close(self) -> None:
        # A janela pertence à interface. Não destruir a sessão ao cancelar.
        return None

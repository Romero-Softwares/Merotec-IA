"""Ponte processual para usar qualquer Chat Web no navegador interno.

A ponte não depende de uma API proprietária: ela abre ``browser_runtime.py``,
mantém o mesmo WebView2 e registra a URL final da conversa por projeto. O site
precisa estar autenticado pelo usuário no navegador; a IDE não tenta burlar
contas, planos ou limites do provedor.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from modules.ai_profiles import (
    ensure_ai_profiles,
    get_web_chat_session,
    normalize_web_url,
    remember_web_chat_session,
    workspace_session_key,
)


class WebChatBridge:
    """Cliente serializado do processo WebView2 usado pelo provedor ``web_chat``."""

    def __init__(
        self,
        *,
        runtime_path: str | Path,
        settings_path: str | Path,
        profile: dict,
        workspace_path: str | Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.settings_path = Path(settings_path)
        self.profile = dict(profile or {})
        self.workspace_path = str(workspace_path or "")
        self.log = log or (lambda _message: None)
        self.process: subprocess.Popen | None = None
        self.events: queue.Queue[dict] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.request_counter = 0
        self.current_session_key = ""
        self.current_url = ""
        self.last_artifacts: dict = {}

    def _settings(self) -> dict:
        try:
            with self.settings_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            return ensure_ai_profiles(payload if isinstance(payload, dict) else {})
        except (OSError, json.JSONDecodeError):
            return ensure_ai_profiles({})

    def _save_settings(self, settings: dict) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(settings, file, indent=2, ensure_ascii=False)
            temporary.replace(self.settings_path)
        except OSError as exc:
            self.log(f"Não consegui persistir a sessão do Chat Web: {exc}")

    def _remember_url(self, workspace_path: str, url: str, title: str = "") -> None:
        if not workspace_path or not url:
            return
        settings = self._settings()
        remember_web_chat_session(
            settings,
            workspace_path,
            "web_chat",
            url,
            entry_url=str(self.profile.get("web_chat_url") or ""),
            title=title,
        )
        self._save_settings(settings)

    def _reader(self, process: subprocess.Popen, events: queue.Queue[dict]) -> None:
        """Lê eventos de uma única geração do WebView2.

        A fila é passada explicitamente: se uma janela antiga fechar depois de
        uma nova abrir, seu evento ``closed`` não pode encerrar a conversa da
        nova janela.
        """
        if not process or not process.stdout:
            return
        for raw in iter(process.stdout.readline, ""):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.log(f"Chat Web: saída inesperada: {line[:320]}")
                continue
            if isinstance(event, dict):
                events.put(event)
        events.put({"event": "closed"})

    def _start(self, initial_url: str) -> None:
        if self.process and self.process.poll() is None:
            return
        if not self.runtime_path.is_file():
            raise RuntimeError(f"Runtime do navegador não encontrado: {self.runtime_path}")

        command = [sys.executable, str(self.runtime_path), "--url", initial_url]
        env = os.environ.copy()
        env["MEROTEC_WEB_CHAT_SESSIONS_FILE"] = str(self.settings_path)
        # O processo filho produz eventos JSON com texto em português e esta
        # ponte os lê explicitamente como UTF-8. Sem esta variável, o Python
        # no Windows pode escrever no codec ANSI, corrompendo "não" para
        # "nÃ£o" antes mesmo de a UI receber o evento.
        env["PYTHONIOENCODING"] = "utf-8"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        # Cada processo possui sua própria fila. O WebView pode fechar alguns
        # instantes depois de outro ser iniciado, e uma fila compartilhada
        # fazia esse fechamento antigo derrubar a sessão recém-aberta.
        events: queue.Queue[dict] = queue.Queue()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=flags,
        )
        self.process = process
        self.events = events
        self.reader_thread = threading.Thread(
            target=self._reader,
            args=(process, events),
            daemon=True,
        )
        self.reader_thread.start()
        ready = self._wait_for(lambda event: event.get("event") in {"ready", "error", "closed"}, timeout=30)
        if not ready:
            raise RuntimeError("O navegador interno não respondeu ao iniciar.")
        if ready.get("event") == "error":
            raise RuntimeError(str(ready.get("message") or "Falha ao abrir o navegador interno."))
        if ready.get("event") == "closed":
            raise RuntimeError("O navegador interno foi fechado antes de iniciar.")
        self.current_url = str(ready.get("url") or initial_url)

    def _next_request_id(self) -> str:
        self.request_counter += 1
        return f"web-{int(time.time() * 1000)}-{self.request_counter}"

    def _wait_for(self, predicate, timeout: float, on_event=None) -> dict | None:
        deadline = time.time() + max(1.0, float(timeout))
        deferred: list[dict] = []
        matched = None
        try:
            while time.time() < deadline:
                try:
                    event = self.events.get(timeout=min(0.5, max(0.05, deadline - time.time())))
                except queue.Empty:
                    continue
                if callable(on_event) and on_event(event):
                    continue
                if predicate(event):
                    matched = event
                    break
                deferred.append(event)
        finally:
            for item in deferred:
                self.events.put(item)
        return matched

    def request(
        self,
        action: str,
        payload: dict | None = None,
        timeout: float = 60,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict:
        with self.lock:
            entry_url = normalize_web_url(self.profile.get("web_chat_url"), "https://chatgpt.com/")
            self._start(entry_url)
            if not self.process or not self.process.stdin:
                raise RuntimeError("O processo do navegador não está disponível.")
            request_id = self._next_request_id()
            message = {"action": action, "request_id": request_id, **(payload or {})}
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

            def handle_progress(item: dict) -> bool:
                if item.get("request_id") != request_id or item.get("event") != "browser_progress":
                    return False
                if callable(progress_callback):
                    partial = item.get("partial")
                    if isinstance(partial, str) and partial:
                        progress_callback("[STREAM_UPDATE]" + partial)
                    else:
                        detail = str(item.get("message") or "Chat Web processando a conversa.").strip()
                        if detail:
                            progress_callback("[ATIVIDADE] " + detail)
                return True

            event = self._wait_for(
                # ``browser_runtime`` responde a navigate/reload com o evento
                # ``navigated`` (não com browser_result). O contrato anterior
                # esperava apenas a resposta genérica e bloqueava a primeira
                # mensagem da sessão antes de ela chegar ao Chat Web.
                lambda item: (
                    item.get("request_id") == request_id
                    and item.get("event")
                    == ("navigated" if action in {"navigate", "reload"} else "browser_result")
                )
                or item.get("event") in {"error", "command_error", "closed"},
                timeout=timeout,
                on_event=handle_progress,
            )
            if not event:
                raise TimeoutError(f"O navegador não respondeu à ação {action}.")
            if event.get("event") in {"error", "command_error"}:
                raise RuntimeError(str(event.get("message") or "Falha no navegador interno."))
            if event.get("event") == "closed":
                raise RuntimeError("O navegador interno foi fechado.")
            result = event.get("result", event)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"text": result}
            if isinstance(result, dict):
                # O protocolo carrega ``ok`` no envelope e no resultado final.
                # Preservar o envelope evita transformar uma falha sem payload
                # em "erro desconhecido" na camada do motor.
                if "ok" not in result and "ok" in event:
                    result["ok"] = bool(event.get("ok"))
                if not result.get("ok", True) and not result.get("error"):
                    result["error"] = str(event.get("message") or "Falha sem detalhe retornada pelo navegador.")
                return result
            return {
                "ok": bool(event.get("ok")),
                "error": str(event.get("message") or "Resposta vazia retornada pelo navegador."),
                "result": result,
            }

    def ensure_workspace_session(self, workspace_path: str | Path | None) -> str:
        workspace = str(workspace_path or self.workspace_path or Path.cwd())
        self.workspace_path = workspace
# MEROTEC_CHAT_URL_SESSIONS_V2
        entry_url = normalize_web_url(self.profile.get("web_chat_url"), "https://chatgpt.com/")
        session_key = workspace_session_key(workspace, "web_chat", entry_url)
        restore_session = bool(self.profile.get("web_chat_restore_project_session", True))
        settings = self._settings()
        saved = (
            get_web_chat_session(settings, workspace, "web_chat", entry_url=entry_url)
            if restore_session
            else {}
        )
        target_url = str(saved.get("url") or entry_url)
        # Na primeira abertura, inicie o WebView diretamente na conversa
        # desejada. Além de poupar uma navegação, isso evita depender de um
        # comando ``navigate`` enquanto o loop de comandos do pywebview ainda
        # está se estabilizando (o caso que deixava a UI parada em branco).
        process = self.process
        process_is_alive = process is not None and process.poll() is None
        if session_key == self.current_session_key and process_is_alive:
            return self.current_url or entry_url
        if not process_is_alive:
            self._start(target_url)
            self.current_session_key = session_key
            self.current_url = str(self.current_url or target_url)
            self._remember_url(workspace, self.current_url)
            return self.current_url

        if str(self.current_url or "").rstrip("/") == target_url.rstrip("/"):
            self.current_session_key = session_key
            return self.current_url or target_url

        result = self.request(
            "navigate",
            {
                "url": target_url,
                "session_key": session_key,
                "entry_url": entry_url,
                "restore_session": bool(restore_session and saved.get("url")),
                # Nunca solicita que o site clique em “nova conversa”.
                "new_conversation": False,
            },
            timeout=45,
        )
        self.current_session_key = session_key
        self.current_url = str(result.get("url") or target_url)
        self._remember_url(workspace, self.current_url, str(result.get("title") or ""))
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

    def chat(
        self,
        prompt: str,
        *,
        workspace_path: str | Path | None = None,
        attachments: list[dict] | None = None,
        timeout: int | None = None,
        stream_callback: Callable[[str], None] | None = None,
        direct_chat: bool = False,
    ) -> dict:
        with self.lock:
            self.ensure_workspace_session(workspace_path)
            message_limit = max(4000, int(self.profile.get("web_chat_message_chars", 28000) or 28000))
            chunks = self._chunks(prompt, message_limit)
            if len(chunks) > 1 and stream_callback:
                stream_callback(f"Chat Web: enviando contexto completo em {len(chunks)} partes.\n")

            for index, chunk in enumerate(chunks):
                is_last = index == len(chunks) - 1
                prefix = "" if direct_chat and is_last else (
                    f"[CONTEXTO {index + 1}/{len(chunks)} — preserve esta parte; "
                    "não responda até receber a parte final]\n"
                    if not is_last
                    else (
                        f"[CONTEXTO {index + 1}/{len(chunks)} — PARTE FINAL]\n"
                        "Use todo o contexto acima e responda à missão agora.\n"
                    )
                )
                result = self.request(
                    "chat",
                    {
                        "prompt": prefix + chunk,
                        "wait_response": is_last,
                        "timeout": int(timeout or self.profile.get("web_chat_timeout_seconds", 300) or 300),
                        "attachments": attachments if is_last else [],
                        "direct_chat": bool(direct_chat and is_last),
                        "session_key": self.current_session_key,
                    },
                    timeout=max(45, int(timeout or self.profile.get("web_chat_timeout_seconds", 300) or 300) + 45),
                    progress_callback=stream_callback if is_last else None,
                )
                url = str(result.get("url") or self.current_url)
                if url:
                    self.current_url = url
                    self._remember_url(self.workspace_path, url, str(result.get("title") or ""))
                if not is_last:
                    if not result.get("ok", True):
                        raise RuntimeError(str(result.get("error") or "O Chat Web recusou uma parte do contexto."))
                    time.sleep(0.25)
                    continue
                self.last_artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
                return result
        return {"ok": False, "error": "Nenhuma resposta do Chat Web."}

    def close(self) -> None:
        with self.lock:
            process = self.process
            self.process = None
            if not process:
                return
            try:
                if process.stdin:
                    process.stdin.write(json.dumps({"action": "close"}) + "\n")
                    process.stdin.flush()
            except Exception:
                pass
            try:
                process.terminate()
            except Exception:
                pass

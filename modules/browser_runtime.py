"""Processo isolado que hospeda o WebView2 usado pela IDE.

O pywebview precisa iniciar o loop grafico na thread principal. A IDE e Tk ja
ocupa essa thread, portanto o navegador vive neste processo e recebe comandos
JSON pela entrada padrao.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import io
import json
import os
import sys
import threading
import time
from pathlib import Path


def _emit(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def _storage_path(scope: object = "chat-web") -> str:
    """Retorna um perfil WebView separado por finalidade, sem aceitar paths arbitrários."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    safe_scope = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in str(scope or "chat-web")
    ).strip(".-_") or "chat-web"
    path = Path(base) / "MerotecIA" / "WebView2" / safe_scope[:80]
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _js_arg(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _set_windows_clipboard_image(data_base64: str) -> tuple[bool, str]:
    """Coloca uma imagem PNG/JPEG no clipboard do Windows como CF_DIB.

    O fallback existe para chats que só materializam o campo de upload depois
    de receber uma colagem real no compositor. Ele é usado apenas para o print
    que a própria IDE acabou de capturar para uma validação visual solicitada.
    """
    if sys.platform != "win32":
        return False, "colagem direta de imagem disponível apenas no Windows"
    try:
        from PIL import Image

        raw = base64.b64decode(str(data_base64 or ""), validate=True)
        if not raw:
            return False, "dados do print vazios"
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        stream = io.BytesIO()
        image.save(stream, format="BMP")
        # CF_DIB recebe o BMP sem o cabeçalho de arquivo de 14 bytes.
        dib = stream.getvalue()[14:]
        if not dib:
            return False, "não foi possível converter o print para o clipboard"

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_bool
        user32.CloseClipboard.argtypes = []
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_bool
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p

        # Outros aplicativos podem manter o clipboard por um instante.
        opened = False
        for _ in range(20):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            return False, "o clipboard do Windows está ocupado"

        handle = None
        try:
            if not user32.EmptyClipboard():
                return False, "não foi possível limpar o clipboard do Windows"
            GMEM_MOVEABLE = 0x0002
            CF_DIB = 8
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
            if not handle:
                return False, "não foi possível reservar memória para o print"
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False, "não foi possível gravar o print no clipboard"
            try:
                ctypes.memmove(pointer, dib, len(dib))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_DIB, handle):
                kernel32.GlobalFree(handle)
                return False, "o Windows recusou a imagem no clipboard"
            # Depois de SetClipboardData bem-sucedido, o Windows possui o handle.
            handle = None
        finally:
            if handle:
                kernel32.GlobalFree(handle)
            user32.CloseClipboard()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _paste_windows_clipboard_image(window, attachment: dict) -> tuple[bool, str]:
    """Foca o compositor e envia Ctrl+V para anexar o print diretamente.

    O envio só continua depois da confirmação visual do anexo no DOM.
    """
    if not isinstance(attachment, dict):
        return False, "anexo de imagem inválido"
    ok, error = _set_windows_clipboard_image(str(attachment.get("data_base64") or ""))
    if not ok:
        return False, error
    try:
        window.show()
        window.restore()
        window.evaluate_js(r"""
        (() => {
          const visible = el => {
            if (!el) return false;
            const s = getComputedStyle(el), r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
          };
          const input = [
            document.querySelector('#prompt-textarea'),
            document.querySelector('textarea[placeholder]'),
            document.querySelector('[contenteditable="true"][role="textbox"]'),
            document.querySelector('.ql-editor[contenteditable="true"]'),
            document.querySelector('textarea'),
            document.querySelector('[contenteditable]:not([contenteditable="false"])')
          ].find(visible);
          if (!input) return false;
          input.scrollIntoView({block: 'center', inline: 'nearest'});
          input.focus();
          if (input.isContentEditable) {
            const selection = getSelection(), range = document.createRange();
            range.selectNodeContents(input);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
          }
          return document.hasFocus();
        })()
        """)
        time.sleep(0.35)
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def run(
    initial_url: str,
    *,
    title: str = "Merotec IA - Navegador",
    storage_scope: str = "chat-web",
) -> int:
    try:
        import webview
    except Exception as exc:
        _emit("error", message=f"pywebview/WebView2 nao instalado: {exc}")
        return 2

    # Permite downloads com o diálogo nativo de salvar arquivo do WebView2.
    webview.settings["ALLOW_DOWNLOADS"] = True

    window = webview.create_window(
        str(title or "Merotec IA - Navegador"),
        initial_url,
        width=1280,
        height=820,
        min_size=(760, 520),
        resizable=True,
        focus=True,
        text_select=True,
        zoomable=True,
    )
    page_loaded = threading.Event()
    window.events.loaded += lambda: page_loaded.set()

    def command_loop() -> None:
        page_loaded.wait(timeout=20)
        _emit("ready", url=initial_url)
        for raw_line in sys.stdin:
            try:
                command = json.loads(raw_line)
                action = str(command.get("action", ""))
                request_id = str(command.get("request_id", ""))
                if action == "navigate":
                    url = str(command.get("url", "")).strip()
                    if url:
                        page_loaded.clear()
                        window.load_url(url)
                        page_loaded.wait(timeout=20)
                        window.show()
                        window.restore()
                        _emit("navigated", url=url)
                elif action == "reload":
                    current = window.get_current_url() or initial_url
                    page_loaded.clear()
                    window.load_url(current)
                    page_loaded.wait(timeout=20)
                    _emit("navigated", url=current)
                elif action == "back":
                    window.evaluate_js("history.back()")
                elif action == "forward":
                    window.evaluate_js("history.forward()")
                elif action == "focus":
                    window.show()
                    window.restore()
                elif action == "inspect":
                    for _ in range(30):
                        try:
                            state = window.evaluate_js("document.readyState")
                            if state in {"interactive", "complete"}:
                                break
                        except Exception:
                            pass
                        time.sleep(0.1)
                    script = r"""
                    (() => {
                      const visible = (el) => {
                        const s = getComputedStyle(el), r = el.getBoundingClientRect();
                        return s.display !== 'none' && s.visibility !== 'hidden' &&
                               Number(s.opacity || 1) > 0 && r.width > 0 && r.height > 0;
                      };
                      window.__merotecElementSeq = window.__merotecElementSeq || 0;
                      const nodes = [...document.querySelectorAll(
                        'a[href],button,input,textarea,select,[role="button"],[contenteditable]:not([contenteditable="false"])'
                      )].filter(visible).slice(0, 120).map((el) => {
                        let id = el.getAttribute('data-merotec-id');
                        if (!id) { id = 'e' + (++window.__merotecElementSeq); el.setAttribute('data-merotec-id', id); }
                        const label = (el.innerText || el.getAttribute('aria-label') ||
                          el.getAttribute('placeholder') || el.getAttribute('title') ||
                          el.value || '').replace(/\s+/g, ' ').trim().slice(0, 180);
                        return {
                          ref: id,
                          tag: el.tagName.toLowerCase(),
                          type: el.getAttribute('type') || '',
                          role: el.getAttribute('role') || '',
                          label,
                          href: (el.href || '').slice(0, 300),
                          disabled: !!el.disabled
                        };
                      });
                      const text = (document.body?.innerText || '')
                        .replace(/\n{3,}/g, '\n\n').trim().slice(0, 12000);
                      return JSON.stringify({url: location.href, title: document.title, text, elements: nodes});
                    })()
                    """
                    result = window.evaluate_js(script)
                    _emit("browser_result", request_id=request_id, action=action, ok=True, result=result)
                elif action == "chat":
                    prompt = str(command.get("prompt", "")).strip()
                    timeout_seconds = max(30, min(600, int(command.get("timeout", 240) or 240)))
                    _emit(
                        "browser_progress",
                        request_id=request_id,
                        action=action,
                        phase="preparing",
                        message="Chat Web preparando mensagem e verificando a página.",
                    )
                    raw_attachments = command.get("attachments", [])
                    attachments = []
                    attachment_error = ""
                    if isinstance(raw_attachments, list):
                        total_data_size = 0
                        for item in raw_attachments[:6]:
                            if not isinstance(item, dict):
                                continue
                            data_base64 = str(item.get("data_base64") or "").strip()
                            if not data_base64:
                                continue
                            # Limite defensivo: o motor principal já limita o
                            # arquivo, mas o processo isolado também precisa se
                            # proteger contra mensagens JSON grandes demais.
                            if len(data_base64) > 14 * 1024 * 1024:
                                attachment_error = "anexo ignorado por exceder o limite seguro do navegador"
                                continue
                            total_data_size += len(data_base64)
                            if total_data_size > 18 * 1024 * 1024:
                                attachment_error = "anexos ignorados por excederem o limite seguro do navegador"
                                break
                            attachments.append({
                                "name": str(item.get("name") or "anexo.bin")[:180],
                                "mime_type": str(item.get("mime_type") or "application/octet-stream")[:120],
                                "data_base64": data_base64,
                            })
                    elif raw_attachments:
                        attachment_error = "formato de anexo inválido"

                    prepare_script = f"""
                    (() => {{
                      const visible = el => {{
                        if (!el) return false;
                        const s=getComputedStyle(el), r=el.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                      }};
                      const assistants = () => [...document.querySelectorAll(
                        '[data-message-author-role="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]'
                      )].filter(visible);
                      const before = assistants();
                      const attachmentPayload = {_js_arg(attachments)};
                      let attachmentError = {_js_arg(attachment_error)};
                      let attachmentCount = 0;
                      let attachmentInputFound = false;
                      const input = [
                        document.querySelector('#prompt-textarea'),
                        document.querySelector('textarea[placeholder]'),
                        document.querySelector('[contenteditable="true"][role="textbox"]'),
                        document.querySelector('.ql-editor[contenteditable="true"]'),
                        document.querySelector('textarea'),
                        document.querySelector('[contenteditable]:not([contenteditable="false"])')
                      ].find(visible);
                      if (!input) return JSON.stringify({{ok:false,error:'campo de conversa nao encontrado; feche modais ou faca login',attachmentError,attachmentCount,attachmentInputFound}});

                      if (attachmentPayload.length) {{
                        const deepElements = () => {{
                          const found = [];
                          const seenRoots = new Set();
                          const walk = root => {{
                            if (!root || seenRoots.has(root) || !root.querySelectorAll) return;
                            seenRoots.add(root);
                            for (const element of root.querySelectorAll('*')) {{
                              found.push(element);
                              if (element.shadowRoot) walk(element.shadowRoot);
                              if (found.length >= 14000) return;
                            }}
                          }};
                          walk(document);
                          return found;
                        }};
                        const transfer = new DataTransfer();
                        try {{
                          for (const attachment of attachmentPayload) {{
                            const raw = atob(String(attachment.data_base64 || ''));
                            const bytes = new Uint8Array(raw.length);
                            for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
                            const blob = new Blob([bytes], {{type: String(attachment.mime_type || 'application/octet-stream')}});
                            transfer.items.add(new File([blob], String(attachment.name || 'anexo.bin'), {{type: blob.type}}));
                          }}
                        }} catch (error) {{
                          attachmentError ||= 'não foi possível preparar o print: ' + String(error?.message || error);
                        }}

                        const elements = deepElements();
                        const fileInputs = elements.filter(el =>
                          el instanceof HTMLInputElement && String(el.type || '').toLowerCase() === 'file' && !el.disabled && !el.readOnly
                        );
                        const fileInput = fileInputs.find(el => /image|file|pdf|video/i.test(String(el.accept || ''))) || fileInputs[0];
                        const emit = (target, event) => {{
                          try {{ target?.dispatchEvent(event); }} catch (_) {{}}
                        }};
                        const parentTargets = [];
                        let cursor = input;
                        for (let level = 0; cursor && level < 7; level += 1, cursor = cursor.parentElement) parentTargets.push(cursor);
                        const dropTargets = [...new Set([
                          input,
                          input.closest('form'),
                          ...parentTargets,
                          document.body,
                          document
                        ].filter(Boolean))];

                        if (fileInput && transfer.files.length) {{
                          attachmentInputFound = true;
                          try {{
                            const filesSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'files')?.set;
                            if (filesSetter) filesSetter.call(fileInput, transfer.files); else fileInput.files = transfer.files;
                            emit(fileInput, new Event('input', {{bubbles:true, composed:true}}));
                            emit(fileInput, new Event('change', {{bubbles:true, composed:true}}));
                            attachmentCount = fileInput.files ? fileInput.files.length : 0;
                          }} catch (error) {{
                            attachmentError ||= 'o chat bloqueou o campo de anexo: ' + String(error?.message || error);
                          }}
                        }}

                        // Alguns chats não expõem input[type=file] até o usuário abrir
                        // o menu. Eles aceitam o arquivo por drop/paste diretamente no
                        // compositor. Esse caminho é tentado mesmo sem input visível.
                        if (transfer.files.length) {{
                          for (const target of dropTargets) {{
                            try {{ emit(target, new DragEvent('dragenter', {{bubbles:true, cancelable:true, dataTransfer:transfer}})); }} catch (_) {{}}
                            try {{ emit(target, new DragEvent('dragover', {{bubbles:true, cancelable:true, dataTransfer:transfer}})); }} catch (_) {{}}
                            try {{ emit(target, new DragEvent('drop', {{bubbles:true, cancelable:true, dataTransfer:transfer}})); }} catch (_) {{}}
                            try {{ emit(target, new ClipboardEvent('paste', {{bubbles:true, cancelable:true, clipboardData:transfer}})); }} catch (_) {{}}
                          }}
                        }}
                      }}

                      const value = {_js_arg(prompt)};
                      input.focus();
                      if (input.isContentEditable) {{
                        const selection=getSelection(), range=document.createRange();
                        range.selectNodeContents(input); selection.removeAllRanges(); selection.addRange(range);
                        if (!document.execCommand('insertText', false, value)) input.textContent=value;
                      }} else {{
                        const proto=input.tagName==='TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
                        if (setter) setter.call(input,value); else input.value=value;
                      }}
                      input.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'insertText',data:value}}));
                      input.dispatchEvent(new Event('change',{{bubbles:true}}));
                      return JSON.stringify({{
                        ok:true,
                        beforeCount:before.length,
                        beforeText:(before.at(-1)?.innerText||'').trim(),
                        inputTag:input.tagName.toLowerCase(),
                        attachmentError,
                        attachmentCount,
                        attachmentInputFound
                      }});
                    }})()
                    """
                    prepared_raw = window.evaluate_js(prepare_script)
                    prepared = json.loads(prepared_raw) if isinstance(prepared_raw, str) else (prepared_raw or {})
                    if not prepared.get("ok"):
                        _emit("browser_result", request_id=request_id, action=action, ok=False, result=prepared_raw)
                        continue
                    # O print deve chegar ao modelo como anexo real, nunca apenas
                    # como nome de arquivo no texto. Primeiro tentamos input/drop;
                    # se o site não expõe preview no DOM, o Windows cola a imagem
                    # nativamente no WebView2. Colagem nativa bem-sucedida é uma
                    # confirmação de transporte suficiente para enviar a mensagem;
                    # depois também procuramos a imagem na conversa publicada.
                    attachment_verified = not bool(attachments)
                    attachment_preview_verified = not bool(attachments)
                    attachment_in_conversation = False
                    attachment_verify_detail = ""
                    native_paste_attempted = False
                    native_paste_succeeded = False
                    if attachments and int(prepared.get("attachmentCount") or 0) < len(attachments):
                        native_paste_attempted = True
                        _emit(
                            "browser_progress",
                            request_id=request_id,
                            action=action,
                            phase="attachment_paste",
                            message="Chat Web: anexando o print diretamente no compositor.",
                        )
                        pasted, paste_error = _paste_windows_clipboard_image(window, attachments[0])
                        native_paste_succeeded = bool(pasted)
                        prepared["nativePasteAttempted"] = True
                        prepared["nativePasteError"] = paste_error
                        if not pasted and paste_error:
                            attachment_verify_detail = paste_error

                    if attachments:
                        expected_names = [str(item.get("name") or "") for item in attachments]
                        verify_script = f"""
                        (() => {{
                          const names = {_js_arg(expected_names)}.filter(Boolean);
                          const visible = el => {{
                            if (!el) return false;
                            const s = getComputedStyle(el), r = el.getBoundingClientRect();
                            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                          }};
                          const findInput = () => [
                            document.querySelector('#prompt-textarea'),
                            document.querySelector('textarea[placeholder]'),
                            document.querySelector('[contenteditable="true"][role="textbox"]'),
                            document.querySelector('.ql-editor[contenteditable="true"]'),
                            document.querySelector('textarea'),
                            document.querySelector('[contenteditable]:not([contenteditable="false"])')
                          ].find(visible);
                          const deep = () => {{
                            const list = [], seen = new Set();
                            const walk = root => {{
                              if (!root || seen.has(root) || !root.querySelectorAll) return;
                              seen.add(root);
                              for (const element of root.querySelectorAll('*')) {{
                                list.push(element);
                                if (element.shadowRoot) walk(element.shadowRoot);
                                if (list.length >= 16000) return;
                              }}
                            }};
                            walk(document);
                            return list;
                          }};
                          const input = findInput();
                          const root = input?.closest('form') || input?.parentElement?.parentElement || input?.parentElement || input;
                          const all = deep();
                          const tree = root ? [root, ...root.querySelectorAll('*')] : all;
                          const fileCount = all.filter(el => el instanceof HTMLInputElement && String(el.type || '').toLowerCase() === 'file')
                            .reduce((total, el) => total + Number(el.files?.length || 0), 0);
                          const previewNodes = tree.filter(el => {{
                            if (!visible(el)) return false;
                            const marker = [el.getAttribute?.('data-testid'), el.getAttribute?.('aria-label'), el.getAttribute?.('title'), el.className, el.alt]
                              .filter(Boolean).join(' ').toLowerCase();
                            const source = String(el.currentSrc || el.src || '');
                            const image = el.tagName === 'IMG' && (/^(blob:|data:image|https?:)/i.test(source) || /image|photo|screenshot|print/i.test(marker));
                            return image || /attachment|upload|file|image|photo|anexo|arquivo|imagem|foto|preview|preview/.test(marker);
                          }});
                          const rootText = String(root?.innerText || root?.textContent || '').toLowerCase();
                          const named = names.length && names.every(name => rootText.includes(String(name).toLowerCase()));
                          const ready = fileCount >= names.length || previewNodes.length >= names.length || named;
                          return JSON.stringify({{ready, fileCount, previews: previewNodes.length, named}});
                        }})()
                        """
                        verify_deadline = time.time() + 14.0
                        last_verify = {}
                        while time.time() < verify_deadline:
                            try:
                                raw_verify = window.evaluate_js(verify_script)
                                last_verify = json.loads(raw_verify) if isinstance(raw_verify, str) else (raw_verify or {})
                            except Exception as exc:
                                last_verify = {"error": str(exc)}
                            if last_verify.get("ready"):
                                attachment_preview_verified = True
                                attachment_verified = True
                                prepared["attachmentCount"] = max(
                                    int(prepared.get("attachmentCount") or 0),
                                    int(last_verify.get("fileCount") or 0),
                                    len(attachments),
                                )
                                break
                            time.sleep(0.45)

                        if not attachment_verified and native_paste_succeeded:
                            # Alguns provedores aceitam Ctrl+V, porém não deixam
                            # preview acessível ao DOM. A chamada nativa foi aceita
                            # pelo WebView2, portanto enviamos e confirmamos depois
                            # pela mensagem publicada na conversa.
                            attachment_verified = True
                            prepared["attachmentCount"] = max(
                                int(prepared.get("attachmentCount") or 0),
                                len(attachments),
                            )
                            _emit(
                                "browser_progress",
                                request_id=request_id,
                                action=action,
                                phase="attachment_verified",
                                message="Chat Web: print colado no compositor; confirmando a publicação na conversa.",
                            )
                        elif not attachment_verified:
                            detail = attachment_verify_detail or str(prepared.get("attachmentError") or "")
                            if not detail:
                                detail = "o navegador não expôs preview nem confirmou a colagem do print"
                            failure = {
                                "ok": False,
                                "error": "O print não foi anexado de forma confirmada; a IDE não enviou uma análise visual sem evidência.",
                                "attachment_error": detail,
                                "attachment_count": int(prepared.get("attachmentCount") or 0),
                                "attachment_requested": True,
                                "attachment_verified": False,
                                "native_paste_attempted": native_paste_attempted,
                                "verification": last_verify,
                            }
                            _emit(
                                "browser_result",
                                request_id=request_id,
                                action=action,
                                ok=False,
                                result=json.dumps(failure, ensure_ascii=False),
                            )
                            continue
                        else:
                            _emit(
                                "browser_progress",
                                request_id=request_id,
                                action=action,
                                phase="attachment_verified",
                                message="Chat Web: print anexado e confirmado antes do envio.",
                            )

                    _emit(
                        "browser_progress",
                        request_id=request_id,
                        action=action,
                        phase="prepared",
                        message="Mensagem preparada; aguardando o botão de envio do Chat Web.",
                    )
                    # Uploads em chats web podem levar vários
                    # segundos. O código anterior esperava só 1s e desistia antes
                    # de o botão de envio ser habilitado; assim o print nunca
                    # chegava ao modelo. Abaixo, aguardamos e repetimos o envio
                    # sem recriar a conversa.
                    # Alguns chats aceitam ``button.click()`` no JavaScript,
                    # mas não publicam a mensagem. Considerar o clique como
                    # sucesso criava um falso positivo: o prompt ficava visível
                    # no compositor e a IDE esperava uma resposta que nunca
                    # seria gerada. A IDE agora confirma que o campo foi limpo
                    # ou que uma nova resposta/stream começou antes de seguir.
                    send_wait_seconds = 18.0 if prepared.get("attachmentCount") else 6.0
                    send_deadline = time.time() + send_wait_seconds
                    send_script = r"""
                    (() => {
                      const visible = el => {
                        if (!el) return false;
                        const s=getComputedStyle(el), r=el.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                      };
                      const direct = [
                        document.querySelector('[data-testid="send-button"]'),
                        document.querySelector('button[aria-label*="Send" i]'),
                        document.querySelector('button[aria-label*="Enviar" i]'),
                        document.querySelector('button[type="submit"]')
                      ].find(el => visible(el) && !el.disabled);
                      let button = direct;
                      if (!button) button = [...document.querySelectorAll('button')].find(el => {
                        const label=(el.innerText||el.getAttribute('aria-label')||'').toLowerCase();
                        return visible(el) && !el.disabled && /send|enviar|submit/.test(label);
                      });
                      if (button) {
                        try {
                          button.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                          button.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                          button.click();
                          return JSON.stringify({ok:true,method:'button'});
                        } catch (error) {
                          return JSON.stringify({ok:false,error:'falha ao acionar envio: '+String(error?.message||error)});
                        }
                      }
                      const input=document.querySelector('#prompt-textarea,textarea,[contenteditable]:not([contenteditable="false"])');
                      const form=input?.closest('form');
                      if (form?.requestSubmit) { form.requestSubmit(); return JSON.stringify({ok:true,method:'form'}); }
                      if (input) {
                        try {
                          input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',code:'Enter',bubbles:true,cancelable:true}));
                          input.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter',code:'Enter',bubbles:true,cancelable:true}));
                          return JSON.stringify({ok:true,method:'enter'});
                        } catch (_) {}
                      }
                      return JSON.stringify({ok:false,error:'botao de envio nao encontrado ou desabilitado'});
                    })()
                    """
                    confirm_script = r"""
                    (() => {
                      const visible = el => {
                        if (!el) return false;
                        const s=getComputedStyle(el), r=el.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                      };
                      const input = [
                        document.querySelector('#prompt-textarea'),
                        document.querySelector('textarea[placeholder]'),
                        document.querySelector('[contenteditable="true"][role="textbox"]'),
                        document.querySelector('.ql-editor[contenteditable="true"]'),
                        document.querySelector('textarea'),
                        document.querySelector('[contenteditable]:not([contenteditable="false"])')
                      ].find(visible);
                      const composerText = input ? String(input.isContentEditable ? (input.innerText || input.textContent || '') : (input.value || '')).trim() : '';
                      const assistants = [...document.querySelectorAll(
                        '[data-message-author-role="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]'
                      )].filter(visible);
                      const streaming = !![
                        document.querySelector('[data-testid="stop-button"]'),
                        document.querySelector('button[aria-label*="Stop" i]'),
                        document.querySelector('button[aria-label*="Parar" i]')
                      ].find(visible);
                      return JSON.stringify({
                        composerCleared: !composerText,
                        composerLength: composerText.length,
                        assistantCount: assistants.length,
                        streaming
                      });
                    })()
                    """
                    sent = {}
                    sent_raw = ""
                    last_send_error = ""
                    sent_confirmed = False
                    send_attempts = 0
                    while time.time() < send_deadline and not sent_confirmed:
                        sent_raw = window.evaluate_js(send_script)
                        sent = json.loads(sent_raw) if isinstance(sent_raw, str) else (sent_raw or {})
                        if not sent.get("ok"):
                            last_send_error = str(sent.get("error") or "botão de envio não ficou disponível")
                            time.sleep(0.6)
                            continue

                        send_attempts += 1
                        confirm_deadline = min(send_deadline, time.time() + 3.6)
                        while time.time() < confirm_deadline:
                            time.sleep(0.25)
                            confirm_raw = window.evaluate_js(confirm_script)
                            try:
                                confirm = json.loads(confirm_raw) if isinstance(confirm_raw, str) else (confirm_raw or {})
                            except json.JSONDecodeError:
                                confirm = {}
                            if (
                                confirm.get("composerCleared")
                                or int(confirm.get("assistantCount") or 0) > int(prepared.get("beforeCount") or 0)
                                or bool(confirm.get("streaming"))
                            ):
                                sent_confirmed = True
                                break
                        if not sent_confirmed:
                            last_send_error = "o chat não confirmou o envio: a mensagem permaneceu no campo de texto"
                            # Tenta uma segunda vez apenas quando o primeiro
                            # clique não mudou o compositor. Nunca cria outra conversa.
                            if send_attempts >= 2:
                                break

                    if not sent_confirmed:
                        if prepared.get("attachmentCount"):
                            attachment_error = attachment_error or (
                                "o chat não concluiu o upload do print antes do envio: " + last_send_error
                            )
                        failure = {
                            "ok": False,
                            "error": last_send_error or "o chat não confirmou o envio da mensagem",
                            "attachment_error": attachment_error,
                            "attachment_count": int(prepared.get("attachmentCount") or 0),
                            "send_attempts": send_attempts,
                        }
                        _emit("browser_result", request_id=request_id, action=action, ok=False, result=json.dumps(failure, ensure_ascii=False))
                        continue

                    _emit(
                        "browser_progress",
                        request_id=request_id,
                        action=action,
                        phase="sent",
                        message="Mensagem enviada; aguardando a resposta do Chat Web.",
                    )
                    deadline = time.time() + timeout_seconds
                    last_text = ""
                    stable_polls = 0
                    response_text = ""
                    last_progress_at = time.time()
                    while time.time() < deadline:
                        time.sleep(1.0)
                        if time.time() - last_progress_at >= 10.0:
                            _emit(
                                "browser_progress",
                                request_id=request_id,
                                action=action,
                                phase="waiting",
                                message="Chat Web ainda está processando a resposta.",
                            )
                            last_progress_at = time.time()
                        poll_raw = window.evaluate_js(r"""
                        (() => {
                          const visible = el => {
                            if (!el) return false;
                            const s=getComputedStyle(el), r=el.getBoundingClientRect();
                            return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                          };
                          const nodes=[...document.querySelectorAll(
                            '[data-message-author-role="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]'
                          )].filter(visible);
                          const messageText = node => {
                            if (!node) return '';
                            const preNodes = [...node.querySelectorAll('pre')];
                            if (!preNodes.length) return node.innerText || node.textContent || '';
                            const clone = node.cloneNode(true);
                            const codeParts = [];
                            [...clone.querySelectorAll('pre')].forEach((pre, index) => {
                              const marker = `__MEROTEC_CODE_BLOCK_${index}__`;
                              const original = preNodes[index];
                              codeParts.push({marker, code: original?.textContent || original?.innerText || ''});
                              pre.replaceWith(document.createTextNode(`\n${marker}\n`));
                            });
                            let result = clone.innerText || clone.textContent || '';
                            for (const part of codeParts) {
                              // Re-add a fence around code copied from the DOM. This preserves
                              // leading spaces even when surrounding Markdown is rendered as text.
                              result = result.replace(part.marker, `\n\`\`\`\n${part.code}\n\`\`\`\n`);
                            }
                            return result;
                          };
                          const text=messageText(nodes.at(-1)).trim();
                          const stopping=!![
                            document.querySelector('[data-testid="stop-button"]'),
                            document.querySelector('button[aria-label*="Stop" i]'),
                            document.querySelector('button[aria-label*="Parar" i]')
                          ].find(visible);
                          return JSON.stringify({count:nodes.length,text,streaming:stopping});
                        })()
                        """)
                        poll = json.loads(poll_raw) if isinstance(poll_raw, str) else (poll_raw or {})
                        current = str(poll.get("text") or "").strip()
                        changed = poll.get("count", 0) > prepared.get("beforeCount", 0) or current != prepared.get("beforeText", "")
                        if current and changed:
                            response_text = current
                            if current == last_text and not poll.get("streaming"):
                                stable_polls += 1
                            else:
                                stable_polls = 0
                            last_text = current
                            if stable_polls >= 2:
                                break

                    if not response_text:
                        rescue_raw = window.evaluate_js(r"""
                        (() => {
                          const visible = el => {
                            if (!el) return false;
                            const s=getComputedStyle(el), r=el.getBoundingClientRect();
                            return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                          };
                          const messageText = node => {
                            if (!node) return '';
                            const preNodes = [...node.querySelectorAll('pre')];
                            if (!preNodes.length) return node.innerText || node.textContent || '';
                            const clone = node.cloneNode(true);
                            const codeParts = [];
                            [...clone.querySelectorAll('pre')].forEach((pre, index) => {
                              const marker = `__MEROTEC_CODE_BLOCK_${index}__`;
                              const original = preNodes[index];
                              codeParts.push({marker, code: original?.textContent || original?.innerText || ''});
                              pre.replaceWith(document.createTextNode(`\n${marker}\n`));
                            });
                            let result = clone.innerText || clone.textContent || '';
                            for (const part of codeParts) {
                              result = result.replace(part.marker, `\n\`\`\`\n${part.code}\n\`\`\`\n`);
                            }
                            return result;
                          };
                          const selectors = [
                            '[data-message-author-role="assistant"]',
                            'model-response',
                            'response-container',
                            'message-content',
                            '.model-response-text',
                            '.response-content',
                            '[data-testid*="model-response"]',
                            '[data-test-id*="model-response"]',
                            '[class*="assistant-message"]',
                            '[class*="model-response"]',
                            '[class*="response-container"]',
                            '[class*="response-content"]',
                            '.markdown',
                            '[class*="markdown"]'
                          ].join(',');
                          const nodes = [...document.querySelectorAll(selectors)].filter(visible);
                          const actionRe = /\[(?:FINAL|READ|SEARCH_TEXT|WEB_SEARCH|SCAN_TEXT|FIX_MOJIBAKE|UNDO|EXECUTE|EXECUTE_ADMIN|OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL|BROWSER_CHAT|SCREENSHOT|HUMAN_TEST|WRITE|REPLACE|PATCH)\s*:/i;
                          for (const node of nodes.slice().reverse()) {
                            const text = messageText(node).trim();
                            if (text && actionRe.test(text)) return JSON.stringify({text, source:'assistant-node'});
                          }
                          const body = String(document.body?.innerText || document.body?.textContent || '');
                          const upper = body.toUpperCase();
                          let best = -1;
                          for (const marker of [
                            '[FINAL:', '[READ:', '[SEARCH_TEXT:', '[WEB_SEARCH:', '[SCAN_TEXT:',
                            '[FIX_MOJIBAKE:', '[UNDO:', '[EXECUTE:', '[EXECUTE_ADMIN:', '[OPEN_URL:',
                            '[BROWSER_INSPECT:', '[BROWSER_CLICK:', '[BROWSER_TYPE:', '[BROWSER_SCROLL:',
                            '[BROWSER_CHAT:', '[SCREENSHOT:', '[HUMAN_TEST:', '[WRITE:', '[REPLACE:', '[PATCH'
                          ]) {
                            const index = upper.lastIndexOf(marker.toUpperCase());
                            if (index > best) best = index;
                          }
                          if (best >= 0) {
                            let text = body.slice(best).trim();
                            text = text.split(/\n(?:Avaliar resposta|Gostei|Não gostei|Copiar|Compartilhar|Regenerar|Tentar novamente|Nova resposta|Read aloud|Copy|Share|Regenerate|Try again|Good response|Bad response|Like|Dislike)/i)[0].trim();
                            return JSON.stringify({text, source:'body-action-tail'});
                          }
                          return JSON.stringify({text:'', source:'none'});
                        })()
                        """)
                        try:
                            rescue = json.loads(rescue_raw) if isinstance(rescue_raw, str) else (rescue_raw or {})
                        except Exception:
                            rescue = {}
                        rescued_text = str(rescue.get("text") or "").strip()
                        if rescued_text:
                            response_text = rescued_text
                            _emit(
                                "browser_progress",
                                request_id=request_id,
                                action=action,
                                phase="response_rescued",
                                message="Resposta visivel do Chat Web recuperada do navegador.",
                            )

                    # Confirmação final: depois que a mensagem saiu do compositor,
                    # verifica se o último turno do usuário expõe imagem ou nome do
                    # arquivo. Este estado é informativo; a confirmação de transporte
                    # acima já permite que o ciclo siga quando o DOM do provedor é
                    # fechado/sombreado.
                    if attachments:
                        expected_names = [str(item.get("name") or "") for item in attachments]
                        conversation_verify_script = f"""
                        (() => {{
                          const names = {_js_arg(expected_names)}.filter(Boolean);
                          const visible = el => {{
                            if (!el) return false;
                            const s = getComputedStyle(el), r = el.getBoundingClientRect();
                            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                          }};
                          const candidates = [...document.querySelectorAll(
                            '[data-message-author-role="user"], user-query, [data-testid*="user-message"], [data-test-id*="user-message"], [class*="user-message"], [class*="user-query"]'
                          )].filter(visible);
                          const node = candidates.at(-1);
                          if (!node) return JSON.stringify({{ready:false, images:0, named:false}});
                          const media = [...node.querySelectorAll('img,video,figure,picture')].filter(el => {{
                            if (!visible(el)) return false;
                            const source = String(el.currentSrc || el.src || '');
                            const marker = [el.alt, el.getAttribute?.('aria-label'), el.getAttribute?.('title'), el.className]
                              .filter(Boolean).join(' ').toLowerCase();
                            return /^(blob:|data:image|https?:)/i.test(source) || /image|photo|screenshot|print|attachment|upload|anexo|imagem|foto/.test(marker);
                          }});
                          const body = String(node.innerText || node.textContent || '').toLowerCase();
                          const named = names.length && names.every(name => body.includes(String(name).toLowerCase()));
                          return JSON.stringify({{ready: media.length >= names.length || named, images: media.length, named}});
                        }})()
                        """
                        conversation_deadline = time.time() + 8.0
                        while time.time() < conversation_deadline:
                            try:
                                conversation_raw = window.evaluate_js(conversation_verify_script)
                                conversation_state = json.loads(conversation_raw) if isinstance(conversation_raw, str) else (conversation_raw or {})
                            except Exception:
                                conversation_state = {}
                            if conversation_state.get("ready"):
                                attachment_in_conversation = True
                                _emit(
                                    "browser_progress",
                                    request_id=request_id,
                                    action=action,
                                    phase="attachment_conversation_confirmed",
                                    message="Chat Web: print confirmado na mensagem enviada.",
                                )
                                break
                            time.sleep(0.35)

                    artifact_raw = window.evaluate_js(r"""
                    (() => {
                      const visible = el => {
                        if (!el) return false;
                        const s=getComputedStyle(el), r=el.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0;
                      };
                      const node=[...document.querySelectorAll(
                        '[data-message-author-role="assistant"], model-response, response-container, message-content, .model-response-text, .response-content, [data-testid*="model-response"], [data-test-id*="model-response"], [class*="assistant-message"]'
                      )].filter(visible).at(-1);
                      const collect = selector => node ? [...node.querySelectorAll(selector)]
                        .map(el => el.currentSrc || el.src || el.href || '')
                        .filter(Boolean).slice(0, 12) : [];
                      return JSON.stringify({images: collect('img'), audio: collect('audio source,audio,video source,video')});
                    })()
                    """)
                    try:
                        artifacts = json.loads(artifact_raw) if isinstance(artifact_raw, str) else (artifact_raw or {})
                    except json.JSONDecodeError:
                        artifacts = {}
                    title_raw = window.evaluate_js("document.title || ''")
                    result = {
                        "ok": bool(response_text),
                        "response": response_text,
                        "url": window.get_current_url(),
                        "title": str(title_raw or ""),
                        "attachment_error": str(attachment_error or prepared.get("attachmentError") or ""),
                        "attachment_count": int(prepared.get("attachmentCount") or 0),
                        "attachment_requested": bool(attachments),
                        "attachment_verified": bool(attachment_verified),
                        "attachment_preview_verified": bool(attachment_preview_verified),
                        "attachment_in_conversation": bool(attachment_in_conversation),
                        "native_paste_attempted": bool(native_paste_attempted),
                        "native_paste_succeeded": bool(native_paste_succeeded),
                        "attachment_delivery": (
                            "conversation_confirmed" if attachments and attachment_in_conversation and attachment_verified and not (attachment_error or prepared.get("attachmentError"))
                            else "sent" if attachments and attachment_verified and not (attachment_error or prepared.get("attachmentError"))
                            else "unavailable" if attachments else "none"
                        ),
                        "artifacts": artifacts if isinstance(artifacts, dict) else {},
                        "error": "tempo esgotado aguardando resposta do chat" if not response_text else "",
                    }
                    _emit(
                        "browser_progress",
                        request_id=request_id,
                        action=action,
                        phase="response_received" if response_text else "timeout",
                        message=("Resposta do Chat Web recebida pela IDE." if response_text else "Tempo esgotado aguardando resposta do Chat Web."),
                    )
                    _emit("browser_result", request_id=request_id, action=action, ok=bool(response_text), result=json.dumps(result, ensure_ascii=False))
                elif action in {"click", "type", "scroll"}:
                    target = str(command.get("target", "")).strip()
                    value = str(command.get("value", ""))
                    if action == "click":
                        script = f"""
                        (() => {{
                          const target = {_js_arg(target)};
                          let el = document.querySelector('[data-merotec-id="' + CSS.escape(target) + '"]');
                          if (!el) {{ try {{ el = document.querySelector(target); }} catch (_) {{}} }}
                          if (!el) return JSON.stringify({{ok:false,error:'elemento nao encontrado: ' + target}});
                          el.scrollIntoView({{block:'center',inline:'center'}}); el.focus(); el.click();
                          return JSON.stringify({{ok:true,ref:target,label:(el.innerText || el.getAttribute('aria-label') || '').trim().slice(0,180),url:location.href}});
                        }})()
                        """
                    elif action == "type":
                        script = f"""
                        (() => {{
                          const target = {_js_arg(target)}, value = {_js_arg(value)};
                          let el = document.querySelector('[data-merotec-id="' + CSS.escape(target) + '"]');
                          if (!el) {{ try {{ el = document.querySelector(target); }} catch (_) {{}} }}
                          if (!el) return JSON.stringify({{ok:false,error:'elemento nao encontrado: ' + target}});
                          el.scrollIntoView({{block:'center'}}); el.focus();
                          if (el.isContentEditable) {{
                            el.dispatchEvent(new InputEvent('beforeinput', {{bubbles:true,cancelable:true,inputType:'insertText',data:value}}));
                            const selection = getSelection();
                            const range = document.createRange();
                            range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range);
                            if (!document.execCommand('insertText', false, value)) el.textContent = value;
                          }}
                          else {{
                            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype :
                              el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
                            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                            if (setter) setter.call(el, value); else el.value = value;
                          }}
                          el.dispatchEvent(new InputEvent('input', {{bubbles:true,inputType:'insertText',data:value}}));
                          el.dispatchEvent(new Event('change', {{bubbles:true}}));
                          return JSON.stringify({{ok:true,ref:target,length:value.length,url:location.href}});
                        }})()
                        """
                    else:
                        script = f"""
                        (() => {{
                          const direction = {_js_arg(target or 'down')};
                          const amount = Math.max(200, Math.floor(innerHeight * 0.8));
                          scrollBy({{top: direction === 'up' ? -amount : amount, behavior:'smooth'}});
                          return JSON.stringify({{ok:true,direction,url:location.href}});
                        }})()
                        """
                    result = window.evaluate_js(script)
                    _emit("browser_result", request_id=request_id, action=action, ok=True, result=result)
                elif action == "close":
                    window.destroy()
                    break
            except Exception as exc:
                _emit("command_error", request_id=request_id, action=action, message=str(exc))

    try:
        start_kwargs = {
            "debug": False,
            "private_mode": False,
            "storage_path": _storage_path(storage_scope),
        }
        if sys.platform == "win32":
            start_kwargs["gui"] = "edgechromium"
        webview.start(command_loop, **start_kwargs)
        _emit("closed")
        # O callback do pywebview fica bloqueado lendo stdin. Ao fechar a
        # janela, finalize-o junto com o processo para a IDE nunca reutilizar
        # um WebView que ja nao existe.
        os._exit(0)
    except Exception as exc:
        _emit("error", message=f"falha ao iniciar WebView2: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="about:blank")
    parser.add_argument("--title", default="Merotec IA - Navegador")
    parser.add_argument("--storage-scope", default="chat-web")
    args = parser.parse_args()
    return run(args.url, title=args.title, storage_scope=args.storage_scope)


if __name__ == "__main__":
    raise SystemExit(main())

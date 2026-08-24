"""Geração de vídeo local por jobs do ComfyUI.

O ComfyUI não possui um workflow universal: cada conjunto de nós/modelos de
vídeo define os próprios nós. Por isso a IDE recebe um JSON de workflow do
usuário e substitui tokens estáveis antes de enviá-lo para ``/prompt``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
VIDEO_SETTING_DEFAULTS = {
    "video_provider": "comfyui",
    "comfyui_base_url": "http://127.0.0.1:8188",
    "comfyui_video_workflow_path": "",
    "video_timeout_seconds": 900,
}
_ASPECT_SIZES = {
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "1:1": (768, 768),
}


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    aspect_ratio: str = "16:9"
    duration_seconds: int = 5
    quality: str = "standard"
    reference_image: Path | None = None

    @property
    def size(self) -> tuple[int, int]:
        return _ASPECT_SIZES.get(self.aspect_ratio, _ASPECT_SIZES["16:9"])


class VideoGenerationService:
    """Cliente pequeno e sem dependências extras para a API local do ComfyUI."""

    def __init__(self, settings: dict | None = None):
        settings = settings or {}
        self.provider = str(
            os.getenv("MEROTEC_VIDEO_PROVIDER", settings.get("video_provider", "comfyui"))
        ).strip().lower() or "comfyui"
        self.base_url = str(
            os.getenv("MEROTEC_COMFYUI_URL", settings.get("comfyui_base_url", "http://127.0.0.1:8188"))
        ).strip().rstrip("/")
        self.workflow_path = str(
            os.getenv("MEROTEC_COMFYUI_VIDEO_WORKFLOW", settings.get("comfyui_video_workflow_path", ""))
        ).strip()
        raw_timeout = os.getenv("MEROTEC_VIDEO_TIMEOUT_SECONDS", settings.get("video_timeout_seconds", 900))
        try:
            self.timeout_seconds = max(60, int(raw_timeout))
        except (TypeError, ValueError):
            self.timeout_seconds = 900

    def generate(
        self,
        request: VideoGenerationRequest,
        output_dir: str | Path,
        progress_callback: Callable[[str], None] | None = None,
        cancel_event=None,
    ) -> Path:
        if self.provider != "comfyui":
            raise RuntimeError("O provedor de vídeo configurado ainda não é suportado. Selecione 'comfyui'.")
        if not request.prompt.strip():
            raise ValueError("Descreva o vídeo que deseja gerar.")

        self._report(progress_callback, "Conectando ao ComfyUI local...")
        self._request_json("GET", "/system_stats", timeout=10)
        workflow = self._load_workflow(request)
        self._check_cancel(cancel_event)
        self._report(progress_callback, "Enviando job de vídeo ao ComfyUI...")
        response = self._request_json(
            "POST",
            "/prompt",
            {"prompt": workflow, "client_id": f"merotec-{uuid.uuid4()}"},
            timeout=30,
        )
        prompt_id = str(response.get("prompt_id") or "").strip()
        if not prompt_id:
            detail = response.get("error") or response.get("node_errors") or response
            raise RuntimeError(f"O ComfyUI recusou o workflow de vídeo: {detail}")

        try:
            output = self._wait_for_video(prompt_id, progress_callback, cancel_event)
        except RuntimeError:
            if cancel_event is not None and cancel_event.is_set():
                self.cancel(prompt_id)
            raise

        self._report(progress_callback, "Baixando o vídeo gerado...")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        source_name = Path(str(output["filename"])).name
        suffix = Path(source_name).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            suffix = ".mp4"
        target = output_dir / f"video_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}{suffix}"
        self._download_output(output, target)
        if not target.is_file() or not target.stat().st_size:
            raise RuntimeError("O ComfyUI concluiu o job, mas retornou um vídeo vazio.")
        self._report(progress_callback, "Vídeo pronto.")
        return target

    def cancel(self, prompt_id: str) -> None:
        """Remove um job pendente quando o usuário cancela a tarefa na IDE."""
        if not prompt_id:
            return
        try:
            self._request_json("POST", "/queue", {"delete": [prompt_id]}, timeout=10)
        except RuntimeError:
            # O cancelamento local não deve esconder a ação do usuário caso o
            # ComfyUI já tenha terminado ou não aceite remover o job.
            pass

    def _load_workflow(self, request: VideoGenerationRequest) -> dict:
        if not self.workflow_path:
            raise RuntimeError(
                "Configure o arquivo JSON de workflow de vídeo do ComfyUI em Configurações da IA. "
                "O template deve conter o token $PROMPT."
            )
        path = Path(self.workflow_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"Workflow de vídeo do ComfyUI não encontrado: {path}")
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Não foi possível ler o workflow de vídeo: {exc}") from exc
        if not isinstance(workflow, dict):
            raise RuntimeError("O workflow de vídeo do ComfyUI precisa ser um objeto JSON.")

        serialized = json.dumps(workflow, ensure_ascii=False)
        if "$PROMPT" not in serialized:
            raise RuntimeError("O workflow de vídeo precisa conter o token $PROMPT em um campo de texto.")
        if request.reference_image and "$REFERENCE_IMAGE" not in serialized:
            raise RuntimeError("O workflow não possui $REFERENCE_IMAGE para receber a imagem de referência.")

        reference_name = ""
        if request.reference_image:
            reference_name = self._upload_reference_image(request.reference_image)
        width, height = request.size
        tokens = {
            "$PROMPT": request.prompt.strip(),
            "$REFERENCE_IMAGE": reference_name,
            "$WIDTH": str(width),
            "$HEIGHT": str(height),
            "$DURATION_SECONDS": str(max(1, int(request.duration_seconds))),
            "$QUALITY": request.quality,
        }
        return self._replace_tokens(workflow, tokens)

    @staticmethod
    def _replace_tokens(value, tokens: dict[str, str]):
        if isinstance(value, str):
            if value in {"$WIDTH", "$HEIGHT", "$DURATION_SECONDS"}:
                return int(tokens[value])
            for token, replacement in tokens.items():
                value = value.replace(token, replacement)
            return value
        if isinstance(value, list):
            return [VideoGenerationService._replace_tokens(item, tokens) for item in value]
        if isinstance(value, dict):
            return {key: VideoGenerationService._replace_tokens(item, tokens) for key, item in value.items()}
        return value

    def _upload_reference_image(self, source: Path) -> str:
        source = Path(source)
        if not source.is_file():
            raise RuntimeError("A imagem de referência não existe mais.")
        boundary = f"----MerotecVideo{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        payload = b"".join((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{source.name}"\r\n'.encode(),
            f"Content-Type: {mime_type}\r\n\r\n".encode(),
            source.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ))
        response = self._request_json(
            "POST",
            "/upload/image",
            payload,
            timeout=60,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        name = str(response.get("name") or "").strip()
        if not name:
            raise RuntimeError("O ComfyUI não confirmou o envio da imagem de referência.")
        subfolder = str(response.get("subfolder") or "").strip().strip("/\\")
        return f"{subfolder}/{name}" if subfolder else name

    def _wait_for_video(self, prompt_id: str, progress_callback, cancel_event) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        reported_wait = False
        while time.monotonic() < deadline:
            self._check_cancel(cancel_event)
            history = self._request_json("GET", f"/history/{urllib.parse.quote(prompt_id)}", timeout=20)
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
                if status.get("status_str") == "error":
                    messages = status.get("messages") or "erro sem detalhes"
                    raise RuntimeError(f"O ComfyUI falhou ao gerar o vídeo: {messages}")
                output = self._find_video_output(entry)
                if output:
                    return output
            if not reported_wait:
                self._report(progress_callback, "Renderizando vídeo no ComfyUI...")
                reported_wait = True
            time.sleep(1.5)
        raise RuntimeError(f"A geração de vídeo excedeu o limite de {self.timeout_seconds} segundos.")

    @staticmethod
    def _find_video_output(history_entry: dict) -> dict | None:
        outputs = history_entry.get("outputs")
        if not isinstance(outputs, dict):
            return None
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for key in ("videos", "gifs", "images"):
                files = node_output.get(key)
                if not isinstance(files, list):
                    continue
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    filename = str(item.get("filename") or "")
                    if Path(filename).suffix.lower() in VIDEO_SUFFIXES:
                        return item
        return None

    def _download_output(self, output: dict, target: Path) -> None:
        query = urllib.parse.urlencode({
            "filename": str(output.get("filename") or ""),
            "subfolder": str(output.get("subfolder") or ""),
            "type": str(output.get("type") or "output"),
        })
        url = f"{self.base_url}/view?{query}"
        try:
            with urllib.request.urlopen(url, timeout=90) as response, target.open("wb") as file:
                shutil.copyfileobj(response, file)
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"Não foi possível baixar o vídeo do ComfyUI: {exc}") from exc

    def _request_json(self, method: str, endpoint: str, payload=None, timeout=30, headers=None) -> dict:
        data = payload
        request_headers = {"Accept": "application/json"}
        if isinstance(payload, dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:900]
            raise RuntimeError(f"ComfyUI respondeu HTTP {exc.code}: {detail}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"Não foi possível conectar ao ComfyUI em {self.base_url}: {exc}") from exc
        if not raw:
            return {}
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("O ComfyUI retornou uma resposta inválida.") from exc
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _report(callback, message: str) -> None:
        if callable(callback):
            callback(message)

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Geração de vídeo cancelada.")

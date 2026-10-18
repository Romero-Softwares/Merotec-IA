"""Geração de vídeo local por jobs do ComfyUI.

O ComfyUI não possui um workflow universal: cada conjunto de nós/modelos de
vídeo define os próprios nós. Por isso a IDE recebe um JSON de workflow do
usuário e substitui tokens estáveis antes de enviá-lo para ``/prompt``.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
VIDEO_SETTING_DEFAULTS = {
    "video_provider": "moviepy",
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


@dataclass(frozen=True)
class VideoNarrationAnalysis:
    """Evidência visual extraída de um vídeo para orientar um roteiro."""

    storyboard_path: Path
    timestamps: tuple[float, ...]


class VideoNarrationService:
    """Extrai telas de vídeos de demonstração e prepara contexto visual.

    A análise é deliberadamente baseada em frames: demonstrações de software
    normalmente não têm fala, mas as telas e as mudanças de estado são a fonte
    necessária para criar a locução. O modelo de IA recebe uma prancha com as
    telas na ordem em que ocorreram, nunca o arquivo de vídeo inteiro.
    """

    DEFAULT_FRAME_COUNT = 6

    @staticmethod
    def is_video_file(path: str | Path) -> bool:
        return Path(path).suffix.casefold() in VIDEO_SUFFIXES

    @staticmethod
    def is_narration_request(prompt: str) -> bool:
        text = " ".join(str(prompt or "").casefold().split())
        video_terms = ("vídeo", "video", "gravação de tela", "gravacao de tela", "demonstração", "demonstracao")
        narration_terms = ("narre", "narração", "narracao", "áudio", "audio", "locução", "locucao", "roteiro")
        return any(term in text for term in video_terms) and any(term in text for term in narration_terms)

    def extract_storyboard(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        frame_count: int | None = None,
    ) -> VideoNarrationAnalysis:
        source = Path(video_path)
        if not source.is_file() or not self.is_video_file(source):
            raise ValueError("Anexe um arquivo de vídeo válido para criar a narração.")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError(
                "A análise de vídeo precisa do FFmpeg (ffmpeg e ffprobe) instalado e disponível no PATH."
            )

        output_dir = Path(output_dir)
        frames_dir = output_dir / f"frames_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        total_frames = max(2, min(12, int(frame_count or self.DEFAULT_FRAME_COUNT)))
        duration = self.read_duration(source)
        timestamps = self._sample_timestamps(duration, total_frames)
        frame_paths = []
        for index, timestamp in enumerate(timestamps, start=1):
            target = frames_dir / f"frame_{index:02d}.jpg"
            command = [
                # ``-hide_banner`` não existe em versões antigas do FFmpeg
                # ainda comuns no Windows; ``-loglevel error`` já mantém a
                # saída limpa e continua compatível.
                "ffmpeg", "-loglevel", "error", "-y",
                "-ss", f"{timestamp:.3f}", "-i", str(source), "-frames:v", "1",
                # ``-1`` mantém a proporção inclusive em builds antigos; o
                # valor moderno ``-2`` não é aceito pelo FFmpeg instalado em
                # algumas versões do Windows.
                "-vf", "scale=min(960\\,iw):-1", "-q:v", "3", str(target),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=45)
            if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                detail = (result.stderr or result.stdout or "erro sem detalhes").strip()[:400]
                raise RuntimeError(f"Não foi possível extrair as telas do vídeo: {detail}")
            frame_paths.append(target)

        storyboard = output_dir / f"storyboard_{source.stem}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        self._compose_storyboard(frame_paths, timestamps, storyboard)
        return VideoNarrationAnalysis(storyboard, tuple(timestamps))

    @staticmethod
    def read_duration(source: Path) -> float:
        command = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        try:
            duration = float(result.stdout.strip())
        except (TypeError, ValueError):
            duration = 0.0
        if result.returncode != 0 or duration <= 0:
            detail = (result.stderr or "duração indisponível").strip()[:300]
            raise RuntimeError(f"Não foi possível ler a duração do vídeo: {detail}")
        return duration

    @staticmethod
    def _sample_timestamps(duration: float, frame_count: int) -> list[float]:
        # Evita a primeira e a última fração de segundo, onde players e telas
        # de transição costumam produzir quadros vazios.
        margin = min(0.5, duration * 0.08)
        start, end = margin, max(margin, duration - margin)
        if frame_count == 1 or end <= start:
            return [max(0.0, duration / 2)]
        return [start + (end - start) * index / (frame_count - 1) for index in range(frame_count)]

    @staticmethod
    def _compose_storyboard(frame_paths: list[Path], timestamps: list[float], target: Path) -> None:
        thumbs = []
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                copy = image.convert("RGB")
                copy.thumbnail((480, 270), Image.Resampling.LANCZOS)
                thumbs.append(copy.copy())
        columns = 2
        cell_width, cell_height = 500, 315
        rows = (len(thumbs) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#101820")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        for index, (thumbnail, timestamp) in enumerate(zip(thumbs, timestamps)):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            offset_x = x + (cell_width - thumbnail.width) // 2
            offset_y = y + 28 + (270 - thumbnail.height) // 2
            sheet.paste(thumbnail, (offset_x, offset_y))
            minutes, seconds = divmod(int(timestamp), 60)
            draw.text((x + 12, y + 8), f"Tela {index + 1} — {minutes}:{seconds:02d}", fill="#f4f8fb", font=font)
        sheet.save(target, quality=92)

    @staticmethod
    def build_narration_prompt(
        original_request: str,
        analysis: VideoNarrationAnalysis,
        target_duration_seconds: float | None = None,
    ) -> str:
        timeline = ", ".join(f"{stamp:.1f}s" for stamp in analysis.timestamps)
        duration_instruction = ""
        if target_duration_seconds and target_duration_seconds > 0:
            duration_instruction = (
                f" Escreva uma locução que ocupe aproximadamente {target_duration_seconds:.0f} segundos, "
                "distribuindo o conteúdo ao longo do vídeo sem acelerar artificialmente."
            )
        return (
            "Analise a prancha de frames anexada, extraída em ordem cronológica de um vídeo. "
            "Crie SOMENTE o texto de uma narração natural em português do Brasil, adequada para acompanhar o vídeo. "
            "Descreva apenas controles e mudanças realmente visíveis; não invente cliques, dados, telas ou resultados. "
            "Use frases fluidas e objetivas, sem títulos, marcadores, avisos ou comentários sobre a análise. "
            "Se algo estiver ilegível, descreva de modo genérico e seguro. "
            f"As telas representam aproximadamente estes instantes: {timeline}. "
            f"{duration_instruction}"
            f"Pedido do usuário: {str(original_request or '').strip()}"
        )

    def replace_audio_with_narration(
        self,
        video_path: str | Path,
        narration_path: str | Path,
        target_path: str | Path,
    ) -> Path:
        """Cria uma cópia do vídeo usando somente a narração como áudio.

        O mapeamento explícito de streams descarta toda faixa de áudio da
        origem. Assim uma trilha local anterior não continua tocando por baixo
        da locução. O ``apad`` preserva a duração visual quando a narração
        termina antes do vídeo.
        """
        source = Path(video_path)
        narration = Path(narration_path)
        target = Path(target_path)
        if not source.is_file() or not self.is_video_file(source):
            raise ValueError("O vídeo de origem para a narração não é válido.")
        if not narration.is_file() or narration.stat().st_size == 0:
            raise ValueError("O arquivo de narração não é válido.")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError(
                "Para substituir a trilha pela narração, instale o FFmpeg e deixe ffmpeg e ffprobe disponíveis no PATH."
            )

        duration = self.read_duration(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-loglevel", "error", "-y",
            "-i", str(source), "-i", str(narration),
            "-filter_complex", "[1:a]aresample=async=1,apad[narration]",
            "-map", "0:v:0", "-map", "[narration]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(target),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(90, int(duration * 4)))
        if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "erro sem detalhes").strip()[:500]
            raise RuntimeError(f"Não foi possível adicionar a narração ao vídeo: {detail}")
        return target


class VideoGenerationService:
    """Cliente pequeno e sem dependências extras para a API local do ComfyUI."""

    def __init__(self, settings: dict | None = None):
        settings = settings or {}
        self.provider = str(
            os.getenv("MEROTEC_VIDEO_PROVIDER", settings.get("video_provider", "moviepy"))
        ).strip().lower() or "moviepy"
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
        if self.provider == "moviepy":
            return self._generate_moviepy(request, output_dir, progress_callback, cancel_event)
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

    def _generate_moviepy(
        self,
        request: VideoGenerationRequest,
        output_dir: str | Path,
        progress_callback: Callable[[str], None] | None,
        cancel_event,
    ) -> Path:
        """Renderiza uma animacao MP4 local, sem servidor ou workflow externo."""
        if not request.prompt.strip():
            raise ValueError("Descreva o video que deseja gerar.")
        try:
            from moviepy import VideoClip
        except ImportError as exc:
            raise RuntimeError(
                "O modo MoviePy precisa da biblioteca 'moviepy'. Execute: "
                "venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ) from exc

        self._check_cancel(cancel_event)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        duration = max(1, min(120, int(request.duration_seconds)))
        width, height = request.size
        prompt = " ".join(request.prompt.strip().split())
        reference = self._load_reference_frame(request.reference_image, width, height)
        background = self._moviepy_background(width, height, reference)
        self._report(progress_callback, "Planejando cenas e movimentos para o video local...")
        visual_plan = self._moviepy_creative_plan(self._moviepy_scene_plan(prompt), prompt)
        self._report(progress_callback, "Renderizando a animacao local com MoviePy...")

        def make_frame(timestamp):
            self._check_cancel(cancel_event)
            return self._moviepy_frame(
                background, reference, prompt, width, height, float(timestamp), duration, visual_plan
            )

        target = output_dir / f"video_local_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}.mp4"
        clip = VideoClip(frame_function=make_frame, duration=duration)
        soundtrack = self._moviepy_soundtrack(visual_plan, duration)
        if soundtrack is not None:
            clip = clip.with_audio(soundtrack)
        try:
            clip.write_videofile(
                str(target), fps=24, codec="libx264", audio=soundtrack is not None, logger=None, threads=2,
            )
        except RuntimeError:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Geracao de video cancelada.")
            raise
        finally:
            clip.close()
            if soundtrack is not None:
                soundtrack.close()
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("O MoviePy terminou sem criar um arquivo de video valido.")
        self._report(progress_callback, "Video local pronto.")
        return target

    @staticmethod
    def _load_reference_frame(source: Path | None, width: int, height: int) -> Image.Image | None:
        if source is None:
            return None
        source = Path(source)
        if not source.is_file():
            raise RuntimeError("A imagem de referencia nao existe mais.")
        try:
            with Image.open(source) as image:
                # Mantem a resolucao da referencia. O enquadramento e aplicado no
                # frame para que ampliacoes solicitadas pelo usuario nao percam
                # nitidez antes da renderizacao.
                return image.convert("RGB").copy()
        except OSError as exc:
            raise RuntimeError(f"Nao foi possivel abrir a imagem de referencia: {exc}") from exc

    @staticmethod
    def _moviepy_background(
        width: int,
        height: int,
        reference: Image.Image | None = None,
    ) -> Image.Image:
        if reference is not None:
            # A imagem ocupa todo o fundo, mantendo sua paleta e atmosfera.
            return ImageOps.fit(reference, (width, height), method=Image.Resampling.LANCZOS).filter(
                ImageFilter.GaussianBlur(radius=max(10, min(width, height) // 28))
            )
        image = Image.new("RGB", (width, height))
        pixels = image.load()
        for y in range(height):
            mix = y / max(1, height - 1)
            color = (int(12 + 12 * mix), int(22 + 26 * mix), int(48 + 55 * mix))
            for x in range(width):
                pixels[x, y] = color
        return image

    @staticmethod
    def _moviepy_frame(
        background: Image.Image,
        reference: Image.Image | None,
        prompt: str,
        width: int,
        height: int,
        timestamp: float,
        duration: int,
        visual_plan: dict | None = None,
    ):
        import numpy as np

        image = background.copy()
        draw = ImageDraw.Draw(image, "RGBA")
        phase = timestamp / max(duration, 1)
        plan = visual_plan or VideoGenerationService._moviepy_creative_plan(
            VideoGenerationService._moviepy_scene_plan(prompt), prompt
        )
        hide_bubbles = plan["hide_bubbles"]
        use_3d_text = plan["use_3d_text"]
        accent = plan["accent"]
        scene_count = plan.get("scene_count", 3)
        scene = min(scene_count - 1, int(phase * scene_count))
        # Feixes precisam cruzar a imagem-base; os demais efeitos ficam no
        # fundo para preservar a legibilidade do assunto principal.
        if reference is None or plan.get("effect") != "beams":
            VideoGenerationService._draw_moviepy_effects(draw, plan, width, height, phase, scene)
        if not hide_bubbles:
            radius = max(width, height) // 4
            for index, color in enumerate(((*accent, 42), (154, 77, 255, 30), (39, 213, 182, 24))):
                angle = phase * math.tau + index * 2.1
                x = int(width * (0.5 + 0.32 * math.cos(angle)))
                y = int(height * (0.5 + 0.28 * math.sin(angle * 0.8)))
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

        if reference is not None:
            # A referencia e o assunto principal: usa quase toda a tela nas
            # revisoes de "imagem maior", sem distorcer nem cortar o conteudo.
            content_top = max(14, int(height * 0.025))
            content_bottom = int(height * plan.get("reference_coverage", 0.78))
            available_height = content_bottom - content_top
            available_width = max(1, int(width * 0.96))
            scene_position = phase * scene_count - scene
            motion = plan.get("reference_motion", "float")
            pulse = math.sin(phase * math.tau * (0.55 + plan["tempo"] * 0.18))
            zoom = 1.0 + (0.018 * pulse if motion != "center" else 0.0)
            if plan.get("transition") == "zoom":
                zoom += max(0.0, 0.07 * (1.0 - min(1.0, scene_position * 5)))
            scaled = ImageOps.contain(
                reference,
                (max(1, int(available_width * zoom)), max(1, int(available_height * zoom))),
                method=Image.Resampling.LANCZOS,
            )
            x = (width - scaled.width) // 2
            drift = 0 if motion == "center" else math.sin(phase * math.tau * (1.0 + plan["tempo"] * 0.35)) * (4 + scene * 3)
            y = content_top + (available_height - scaled.height) // 2 + int(drift)
            draw.rounded_rectangle(
                (x - 12, y - 12, x + scaled.width + 12, y + scaled.height + 12),
                radius=22, fill=(255, 255, 255, 18), outline=(*accent, 118), width=2,
            )
            image.paste(scaled, (x, y))
            if plan.get("effect") == "beams":
                VideoGenerationService._draw_moviepy_effects(draw, plan, width, height, phase, scene)
            VideoGenerationService._draw_moviepy_caption(
                draw, plan, width, height, phase, scene, vertical_center=int(height * 0.94)
            )
            return np.asarray(image)

        margin = max(36, width // 18)
        panel_top, panel_bottom = int(height * 0.12), int(height * 0.88)
        draw.rounded_rectangle(
            (margin, panel_top, width - margin, panel_bottom), radius=30,
            fill=(8, 16, 34, 190), outline=(150, 190, 255, 72), width=2,
        )
        font = ImageFont.load_default()
        draw.text((margin + 28, panel_top + 26), "MEROTEC VIDEO", fill=(*accent, 255), font=font)
        draw.text((margin + 28, panel_top + 58), plan["style_label"], fill=(246, 249, 255, 255), font=font)

        text_y = panel_top + 102
        max_chars = max(32, (width - margin * 2 - 56) // 8)
        words, line, lines = prompt.split(), "", []
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and len(candidate) > max_chars:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for line in lines[:3]:
            draw.text((margin + 28, text_y), line, fill=(232, 238, 250, 245), font=font)
            text_y += 22

        VideoGenerationService._draw_moviepy_caption(draw, plan, width, height, phase, scene)

        progress = int((width - margin * 2 - 56) * min(1.0, timestamp / max(duration, 0.1)))
        track_y = panel_bottom - 58
        draw.rounded_rectangle((margin + 28, track_y, width - margin - 28, track_y + 8), radius=4, fill=(255, 255, 255, 35))
        draw.rounded_rectangle((margin + 28, track_y, margin + 28 + progress, track_y + 8), radius=4, fill=(*accent, 220))
        return np.asarray(image)

    @staticmethod
    def _moviepy_visual_directives(prompt: str) -> tuple[bool, bool]:
        """Traduz requisitos visuais explicitos do pedido para o renderer local."""
        text = " ".join(str(prompt or "").casefold().split())
        hide_bubbles = bool(re.search(r"\bsem\s+(?:as?\s+)?bolhas?\b", text))
        use_3d_text = bool(re.search(
            r"\b(?:textos?|letras?)\s*(?:em\s*)?3\s*d\b|\b3\s*d\s*(?:textos?|letras?)\b",
            text,
        ))
        return hide_bubbles, use_3d_text

    @staticmethod
    def _moviepy_scene_plan(prompt: str) -> dict:
        """Transforma instrucoes simples em uma direcao visual local e deterministica."""
        text = " ".join(str(prompt or "").casefold().split())
        hide_bubbles, use_3d_text = VideoGenerationService._moviepy_visual_directives(text)
        # "Sem textos com barra" significa texto limpo, e não ausência total
        # de texto. A distinção evita apagar a chamada principal por acidente.
        no_text = bool(re.search(
            r"\bsem\s+(?:qualquer\s+)?(?:textos?|letras?)(?!\s+(?:com|em)\s+(?:barra|faixa|fundo|caixa|tarja))\b",
            text,
        ))
        plain_text = bool(re.search(
            r"\bsem\s+(?:(?:textos?|letras?)\s+(?:com|em)\s+)?(?:barra|faixa|fundo|caixa|tarja)(?:\s+de\s+fundo)?\b",
            text,
        ))
        small_text = bool(
            re.search(r"\b(?:texto|letra)s?\s+(?:menor|pequen[oa])\b", text)
            or (
                any(word in text for word in ("texto", "letra"))
                and re.search(r"\b(?:bem\s+)?(?:menor|pequen[oa])\b", text)
            )
        )
        large_reference = bool(re.search(
            r"\b(?:imagem|foto|refer[eê]ncia)\b[^\n]{0,36}\b(?:bem\s+)?maior\b"
            r"|\b(?:amplie|aumente|aproxime)\b[^\n]{0,36}\b(?:imagem|foto|refer[eê]ncia)\b",
            text,
        ))
        full_frame_reference = any(phrase in text for phrase in (
            "ocupa quase toda a tela", "ocupe quase toda a tela", "quase toda tela",
            "tela inteira", "tela toda", "preencha a tela", "ocupando a tela",
        ))
        centered_reference = any(phrase in text for phrase in (
            "imagem centralizada", "foto centralizada", "centralize a imagem", "centralizada na tela",
        ))
        palettes = (
            ((234, 111, 76), ("anúncio", "anuncio", "promoção", "promocao", "oferta", "venda")),
            ((90, 205, 177), ("natureza", "saúde", "saude", "orgânico", "organico", "verde")),
            ((176, 112, 255), ("futuro", "tecnologia", "digital", "inovação", "inovacao", "ia")),
        )
        accent = (93, 183, 255)
        for color, words in palettes:
            if any(word in text for word in words):
                accent = color
                break
        headline = VideoGenerationService._moviepy_headline_from_prompt(text)
        return {
            "accent": accent,
            "headline": headline[:46],
            "hide_bubbles": hide_bubbles,
            "use_3d_text": use_3d_text,
            "no_text": no_text,
            "plain_text": plain_text,
            "small_text": small_text,
            "reference_coverage": 0.985 if full_frame_reference else (0.93 if large_reference else 0.78),
            "reference_motion": "center" if centered_reference else "float",
        }

    @staticmethod
    def _moviepy_headline_from_prompt(text: str) -> str:
        """Extrai só o assunto; instruções técnicas nunca viram texto na tela."""
        original = " ".join(str(text or "").casefold().split())
        explicit = re.search(r"\b(?:texto|frase|chamada)\s*:\s*[\"']?([^\"'\n]{3,72})", original)
        if explicit:
            return " ".join(explicit.group(1).split()).upper()[:46]

        # Revisões e mensagens internas da IDE são contexto de produção, não
        # copy publicitária. Isso elimina títulos como "REQUISITOS".
        subject = re.split(
            r"\b(?:requisitos?\s+obrigat[oó]rios?[^:]{0,48}|dire[cç][aã]o criativa)\s*:",
            original,
            maxsplit=1,
        )[0]
        subject = re.sub(
            r"\bgere um vídeo animado que preserve[^.]*\.?|\ba imagem de referência[^.]*\.?",
            " ", subject,
        )
        stop_words = {
            "gere", "gerar", "crie", "criar", "refaça", "refaca", "regenere", "regere", "novamente",
            "video", "vídeo", "um", "uma", "com", "sem", "texto", "textos", "letra", "letras",
            "barra", "faixa", "fundo", "caixa", "tarja", "menor", "pequeno", "bolha", "bolhas",
            "audio", "áudio", "trilha", "musica", "música", "segundos", "segundo", "tema", "imagem",
            "referencia", "referência", "requisitos", "obrigatorios", "obrigatórios", "anuncio", "anúncio",
        }
        words = [word for word in re.findall(r"[\wÀ-ÿ]+", subject) if len(word) > 2 and word not in stop_words]
        return " ".join(words[:4]).upper() or "SUA IDEIA EM MOVIMENTO"

    @staticmethod
    def _moviepy_creative_plan(base_plan: dict, prompt: str) -> dict:
        """Amplia o plano base com estilo, ritmo, efeitos e cenas locais."""
        text = " ".join(str(prompt or "").casefold().split())
        plan = dict(base_plan)
        styles = (
            ((234, 111, 76), "ANUNCIO DE IMPACTO", ("anúncio", "anuncio", "promoção", "promocao", "oferta", "venda", "produto")),
            ((90, 205, 177), "ORGANICO E LEVE", ("natureza", "saúde", "saude", "orgânico", "organico", "verde", "bem-estar")),
            ((176, 112, 255), "NEON FUTURISTA", ("futuro", "tecnologia", "digital", "inovação", "inovacao", "ia", "neon", "cyber")),
            ((232, 193, 91), "PREMIUM CINEMATICO", ("cinematográfico", "cinematografico", "cinemático", "cinematico", "filme", "trailer", "luxo", "premium")),
            ((239, 111, 153), "REEL VIBRANTE", ("reel", "tiktok", "instagram", "viral", "dinâmico", "dinamico", "energético", "energetico")),
            ((255, 135, 74), "RETRO VINTAGE", ("retro", "retrô", "vintage", "anos 80", "anos 90")),
            ((93, 183, 255), "INSTITUCIONAL LIMPO", ("institucional", "corporativo", "empresa", "apresentação", "apresentacao", "tutorial")),
        )
        plan.setdefault("style_label", "CRIACAO ORIGINAL")
        for color, label, words in styles:
            if any(word in text for word in words):
                plan["accent"], plan["style_label"] = color, label
                break
        if any(word in text for word in ("rapido", "rápido", "agil", "ágil", "energético", "energetico", "dinâmico", "dinamico")):
            plan["tempo"] = 1.45
        elif any(word in text for word in ("calmo", "suave", "lento", "minimalista", "elegante")):
            plan["tempo"] = 0.72
        else:
            plan["tempo"] = 1.0
        if any(word in text for word in ("sem transi", "cortes secos", "corte seco")):
            plan["transition"] = "cut"
        elif any(word in text for word in ("glitch", "falha digital")):
            plan["transition"] = "glitch"
        elif any(word in text for word in ("zoom", "aproximacao", "aproximação")):
            plan["transition"] = "zoom"
        else:
            plan["transition"] = "dissolve"
        if any(word in text for word in ("particula", "partícula", "brilho", "magico", "mágico", "glitter")):
            plan["effect"] = "sparkles"
        elif any(word in text for word in ("grade", "grid", "hud", "tecnologia", "digital", "neon")):
            plan["effect"] = "grid"
        elif any(word in text for word in ("retro", "retrô", "vintage", "filme")):
            plan["effect"] = "scanlines"
        else:
            plan["effect"] = "rays"
        explicit_beams = any(word in text for word in (
            "raio", "raios", "feixe", "feixes", "laser", "lightning", "beam",
        ))
        if explicit_beams:
            plan["effect"] = "beams"
        elif any(word in text for word in ("fogo", "flame", "incendio", "explosao")):
            plan["effect"] = "embers"
        elif any(word in text for word in ("onda", "ondas", "wave", "liquido", "liquid")):
            plan["effect"] = "waves"

        if any(word in text for word in ("wipe", "varredura", "cortina")):
            plan["transition"] = "wipe"
        elif explicit_beams or any(word in text for word in ("flash", "clarão", "explosao")):
            plan["transition"] = "flash"
        elif any(word in text for word in ("transicao", "transição", "transitions")):
            plan["transition"] = "flash"

        plan["scene_count"] = 5 if any(word in text for word in (
            "historia", "história", "story", "jornada", "tutorial", "passo a passo"
        )) else 3
        no_music = bool(re.search(r"\bsem\s+(?:m[uú]sica|trilha|som|[aá]udio)\b", text))
        plan["soundtrack"] = not no_music and bool(re.search(
            r"\b(?:com\s+)?(?:m[uú]sica|trilha|instrumental|beat|som)\b", text
        ))
        headline = str(plan.get("headline") or "SUA IDEIA EM MOVIMENTO")
        if any(word in text for word in ("sistema", "sistemas", "software", "automacao", "automação", "app", "aplicativo")):
            captions = ["SISTEMAS QUE MOVEM NEGOCIOS", "SUA IDEIA GANHA FORMA", "PRONTO PARA EVOLUIR"]
        elif plan["style_label"] == "ANUNCIO DE IMPACTO":
            captions = [headline, "QUALIDADE EM CADA DETALHE", "DESCUBRA AGORA"]
        else:
            captions = [headline, "EXPERIENCIA QUE CONECTA", "DESCUBRA AGORA"]
        if plan["scene_count"] > 3:
            captions = [captions[0]] + [captions[1]] * (plan["scene_count"] - 2) + [captions[-1]]
        plan["scene_captions"] = [caption[:46] for caption in captions]
        return plan

    @staticmethod
    def _draw_moviepy_effects(
        draw: ImageDraw.ImageDraw, plan: dict, width: int, height: int, phase: float, scene: int
    ) -> None:
        """Desenha efeitos leves e animados, sem depender de assets externos."""
        effect = plan["effect"]
        accent = plan["accent"]
        if effect == "grid":
            spacing = max(32, min(width, height) // 10)
            drift = int((phase * spacing * 2) % spacing)
            for x in range(-spacing + drift, width + spacing, spacing):
                draw.line((x, 0, x, height), fill=(*accent, 32), width=1)
            for y in range(-spacing + drift, height + spacing, spacing):
                draw.line((0, y, width, y), fill=(*accent, 24), width=1)
        elif effect == "scanlines":
            spacing = max(4, height // 90)
            for y in range(0, height, spacing * 2):
                draw.line((0, y, width, y), fill=(20, 8, 28, 28), width=spacing)
        elif effect == "sparkles":
            for index in range(18):
                angle = phase * math.tau * (0.8 + index / 18) + index * 2.4
                x = int(width * (0.5 + 0.46 * math.sin(angle * 1.7)))
                y = int(height * (0.5 + 0.42 * math.cos(angle)))
                radius = 1 + (index + scene) % 3
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*accent, 150))
        elif effect == "beams":
            # Feixes diagonais e pulsos centrais: um pedido de raios precisa
            # mudar claramente a leitura do quadro, nao apenas a cor de fundo.
            pulse = 0.45 + 0.55 * (math.sin(phase * math.tau * 2.4) + 1) / 2
            beam_width = max(2, width // 95)
            for index in range(11):
                origin_x = int((index / 10) * width)
                origin_y = 0 if index % 2 == 0 else height
                target_x = int(width * (0.22 + 0.56 * ((phase * 1.7 + index * 0.19) % 1)))
                target_y = height - origin_y
                alpha = int(34 + 105 * pulse * (0.55 + (index % 3) * 0.15))
                draw.line((origin_x, origin_y, target_x, target_y), fill=(*accent, alpha), width=beam_width)
            core = int(min(width, height) * (0.035 + 0.02 * pulse))
            draw.ellipse(
                (width // 2 - core, height // 2 - core, width // 2 + core, height // 2 + core),
                fill=(255, 255, 255, int(45 + 90 * pulse)),
            )
        elif effect == "embers":
            for index in range(28):
                rise = (phase * (0.55 + index / 90) + index * 0.071) % 1
                x = int(width * ((index * 0.173 + math.sin(phase * 4 + index) * 0.04) % 1))
                y = int(height * (1.04 - rise * 1.16))
                radius = 1 + index % 4
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 154, 64, 105 + index % 100))
        elif effect == "waves":
            for index in range(6):
                radius = int(min(width, height) * (0.10 + ((phase * 0.7 + index / 6) % 1) * 0.72))
                alpha = int(72 * (1 - index / 7))
                draw.ellipse(
                    (width // 2 - radius, height // 2 - radius, width // 2 + radius, height // 2 + radius),
                    outline=(*accent, alpha), width=max(1, width // 320),
                )
        else:
            center_x, center_y = width // 2, height // 2
            for index in range(7):
                angle = phase * 0.8 + index * math.tau / 7
                end_x = int(center_x + math.cos(angle) * width)
                end_y = int(center_y + math.sin(angle) * height)
                draw.line((center_x, center_y, end_x, end_y), fill=(*accent, 14), width=max(1, width // 230))
        scene_position = phase * plan["scene_count"] - scene
        if plan["transition"] == "glitch" and scene_position < 0.10 and scene:
            band = max(8, height // 24)
            for y in range(0, height, band * 3):
                draw.rectangle((0, y, width, y + band), fill=(*accent, 46))
        elif plan["transition"] == "flash" and scene_position < 0.14 and scene:
            alpha = int((0.14 - scene_position) / 0.14 * 185)
            draw.rectangle((0, 0, width, height), fill=(*accent, alpha))
            draw.rectangle((0, int(height * 0.44), width, int(height * 0.56)), fill=(255, 255, 255, alpha // 2))
        elif plan["transition"] == "wipe" and scene_position < 0.16 and scene:
            progress = max(0.0, min(1.0, scene_position / 0.16))
            edge = int(width * progress)
            draw.rectangle((edge, 0, width, height), fill=(4, 10, 28, int(190 * (1 - progress))))
            draw.line((edge, 0, edge, height), fill=(*accent, 220), width=max(2, width // 170))
        elif plan["transition"] == "dissolve" and scene_position < 0.08 and scene:
            alpha = int((0.08 - scene_position) / 0.08 * 62)
            draw.rectangle((0, 0, width, height), fill=(255, 255, 255, alpha))

    @staticmethod
    def _moviepy_soundtrack(plan: dict, duration: int):
        """Cria uma trilha instrumental simples sob demanda, inteiramente local."""
        if not plan.get("soundtrack"):
            return None
        try:
            from moviepy import AudioClip
            import numpy as np
        except ImportError:
            return None
        tempo = float(plan.get("tempo", 1.0))
        base_frequency = 110.0 + (plan["accent"][0] % 70)

        def make_audio(timestamp):
            time_values = np.asarray(timestamp, dtype=float)
            beat = np.maximum(0.0, np.sin(time_values * math.tau * (1.05 * tempo))) ** 10
            melody = np.sin(time_values * math.tau * base_frequency) * 0.035
            harmony = np.sin(time_values * math.tau * base_frequency * 1.5) * 0.018
            pulse = beat * np.sin(time_values * math.tau * base_frequency * 0.5) * 0.075
            value = melody + harmony + pulse
            if time_values.ndim == 0:
                return [float(value), float(value)]
            return np.column_stack((value, value))

        return AudioClip(frame_function=make_audio, duration=duration, fps=22050)

    @staticmethod
    def _draw_moviepy_caption(
        draw: ImageDraw.ImageDraw, plan: dict, width: int, height: int, phase: float,
        scene: int, vertical_center: int | None = None,
    ) -> None:
        if plan["no_text"]:
            return
        captions = plan.get("scene_captions") or [plan["headline"]]
        title = captions[min(scene, len(captions) - 1)]
        font, lines, line_height = VideoGenerationService._moviepy_caption_layout(
            draw, title, width, height, bool(plan["small_text"])
        )
        if not lines:
            return
        text_width = max(draw.textbbox((0, 0), line, font=font, stroke_width=1)[2] for line in lines)
        text_height = line_height * len(lines)
        x = (width - text_width) // 2
        center = vertical_center if vertical_center is not None else int(height * 0.78)
        entrance = min(1.0, max(0.0, (phase * 3.0 - scene) * 2.8))
        y = int(center + (1.0 - entrance) * height * 0.06 + math.sin(phase * math.tau) * 4) - text_height // 2
        safe_margin = max(12, height // 28)
        y = max(safe_margin, min(height - safe_margin - text_height, y))
        if not plan["plain_text"]:
            padding = max(12, line_height // 3)
            draw.rounded_rectangle(
                (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
                radius=padding, fill=(5, 14, 34, 190), outline=(*plan["accent"], 110), width=1,
            )
        for index, line in enumerate(lines):
            line_bbox = draw.textbbox((0, 0), line, font=font, stroke_width=1)
            line_width = line_bbox[2] - line_bbox[0]
            line_x = (width - line_width) // 2
            line_y = y + index * line_height
            if plan["use_3d_text"]:
                depth = max(2, line_height // 14)
                for offset in range(depth, 0, -1):
                    draw.text((line_x + offset, line_y + offset), line, font=font, fill=(8, 25, 68, 210), stroke_width=1)
            draw.text((line_x, line_y), line, font=font, fill=(244, 250, 255, 255), stroke_width=1, stroke_fill=(*plan["accent"], 255))

    @staticmethod
    def _moviepy_caption_layout(
        draw: ImageDraw.ImageDraw, title: str, width: int, height: int, small_text: bool
    ) -> tuple[ImageFont.ImageFont, list[str], int]:
        """Mantém o texto em até duas linhas dentro da área segura do vídeo."""
        max_width = int(width * 0.84)
        initial_size = max(18, min(width, height) // (19 if small_text else 14))
        minimum_size = max(14, initial_size // 2)
        for font_size in range(initial_size, minimum_size - 1, -2):
            try:
                font = ImageFont.truetype("arialbd.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
            words = str(title or "").split()
            lines, current = [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and draw.textlength(candidate, font=font) > max_width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
            if len(lines) <= 2 and all(draw.textlength(line, font=font) <= max_width for line in lines):
                bbox = draw.textbbox((0, 0), "Ag", font=font, stroke_width=1)
                return font, lines, max(1, int((bbox[3] - bbox[1]) * 1.2))

        # Uma chamada muito extensa é resumida em vez de invadir as bordas.
        compact = " ".join(str(title or "").split()[:5]).rstrip(".,;:") + "…"
        try:
            font = ImageFont.truetype("arialbd.ttf", minimum_size)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "Ag", font=font, stroke_width=1)
        return font, [compact], max(1, int((bbox[3] - bbox[1]) * 1.2))

    @staticmethod
    def _draw_moviepy_3d_text(
        draw: ImageDraw.ImageDraw, width: int, height: int, phase: float, vertical_center: int | None = None
    ) -> None:
        """Sobrepoe titulo com profundidade e movimento, sem depender de fontes externas."""
        font_size = max(24, min(width, height) // 11)
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
        title = "EM DESTAQUE"
        bbox = draw.textbbox((0, 0), title, font=font, stroke_width=1)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) // 2
        center = vertical_center if vertical_center is not None else int(height * 0.78)
        y = int(center + height * 0.018 * math.sin(phase * math.tau)) - text_height // 2
        depth = max(3, font_size // 12)
        for offset in range(depth, 0, -1):
            draw.text((x + offset, y + offset), title, font=font, fill=(8, 25, 68, 230), stroke_width=1)
        draw.text((x, y), title, font=font, fill=(238, 249, 255, 255), stroke_width=2, stroke_fill=(57, 163, 255, 255))

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

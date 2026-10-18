from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.video_generation import (
    VideoGenerationRequest,
    VideoGenerationService,
    VideoNarrationAnalysis,
    VideoNarrationService,
)


class VideoGenerationTests(unittest.TestCase):
    def test_workflow_replaces_prompt_and_render_tokens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "video-workflow.json"
            workflow_path.write_text(json.dumps({
                "1": {"inputs": {"text": "$PROMPT", "width": "$WIDTH", "height": "$HEIGHT"}},
            }), encoding="utf-8")
            service = VideoGenerationService({"comfyui_video_workflow_path": str(workflow_path)})

            workflow = service._load_workflow(VideoGenerationRequest("uma rua chuvosa", aspect_ratio="9:16"))

        self.assertEqual("uma rua chuvosa", workflow["1"]["inputs"]["text"])
        self.assertEqual(576, workflow["1"]["inputs"]["width"])
        self.assertEqual(1024, workflow["1"]["inputs"]["height"])

    def test_generate_waits_for_history_and_saves_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "video-workflow.json"
            workflow_path.write_text(json.dumps({"1": {"inputs": {"text": "$PROMPT"}}}), encoding="utf-8")
            service = VideoGenerationService({
                "video_provider": "comfyui",
                "comfyui_video_workflow_path": str(workflow_path),
                "video_timeout_seconds": 60,
            })
            calls = []

            def request_json(method, endpoint, payload=None, timeout=30, headers=None):
                calls.append((method, endpoint))
                if endpoint == "/prompt":
                    return {"prompt_id": "job-123"}
                if endpoint == "/history/job-123":
                    return {"job-123": {"outputs": {"9": {"gifs": [{
                        "filename": "output.mp4", "subfolder": "", "type": "output",
                    }]}}}}
                return {}

            service._request_json = request_json
            service._download_output = lambda _output, target: target.write_bytes(b"video-bytes")
            target = service.generate(VideoGenerationRequest("teste"), Path(temp_dir) / "videos")

            self.assertEqual(b"video-bytes", target.read_bytes())
            self.assertEqual(".mp4", target.suffix)
            self.assertIn(("POST", "/prompt"), calls)
            self.assertIn(("GET", "/history/job-123"), calls)

    def test_reference_requires_compatible_workflow_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow_path = root / "video-workflow.json"
            workflow_path.write_text(json.dumps({"1": {"inputs": {"text": "$PROMPT"}}}), encoding="utf-8")
            reference = root / "reference.png"
            reference.write_bytes(b"png")
            service = VideoGenerationService({"comfyui_video_workflow_path": str(workflow_path)})

            with self.assertRaisesRegex(RuntimeError, "REFERENCE_IMAGE"):
                service._load_workflow(VideoGenerationRequest("teste", reference_image=reference))

    def test_moviepy_provider_does_not_require_comfyui_workflow(self):
        service = VideoGenerationService({"video_provider": "moviepy"})
        self.assertEqual("moviepy", service.provider)
        self.assertEqual("", service.workflow_path)

    def test_moviepy_is_the_default_provider(self):
        self.assertEqual("moviepy", VideoGenerationService().provider)

    def test_moviepy_uses_reference_image_as_the_video_theme_background(self):
        reference = Image.new("RGB", (120, 80), (196, 42, 78))
        background = VideoGenerationService._moviepy_background(320, 180, reference)
        red, green, blue = background.getpixel((160, 90))
        self.assertGreater(red, green)
        self.assertGreater(red, blue)

    def test_moviepy_honors_bubble_and_3d_text_directives(self):
        no_bubbles, has_3d_text = VideoGenerationService._moviepy_visual_directives(
            "gere novamente sem as bolhas e com textos 3d"
        )
        self.assertTrue(no_bubbles)
        self.assertTrue(has_3d_text)

        no_bubbles, has_3d_text = VideoGenerationService._moviepy_visual_directives(
            "gere um video institucional"
        )
        self.assertFalse(no_bubbles)
        self.assertFalse(has_3d_text)

    def test_moviepy_creates_a_local_visual_plan_for_clean_small_text(self):
        plan = VideoGenerationService._moviepy_scene_plan(
            "regere o anuncio sem barra de fundo no texto e texto menor"
        )
        self.assertTrue(plan["plain_text"])
        self.assertTrue(plan["small_text"])
        self.assertEqual((234, 111, 76), plan["accent"])

    def test_moviepy_treats_sem_textos_com_barra_as_clean_text(self):
        plan = VideoGenerationService._moviepy_scene_plan(
            "video anuncio com audio sem bolhas sem textos com barra"
        )
        self.assertFalse(plan["no_text"])
        self.assertTrue(plan["plain_text"])

    def test_moviepy_never_uses_revision_instructions_as_headline(self):
        headline = VideoGenerationService._moviepy_headline_from_prompt(
            "gere um video de sistemas\n\nRequisitos obrigatorios da revisao: imagem maior, sem barra"
        )
        self.assertNotIn("REQUISITOS", headline)
        self.assertEqual("SISTEMAS", headline)

    def test_moviepy_caption_layout_keeps_long_copy_inside_two_lines(self):
        image = Image.new("RGB", (576, 1024))
        draw = ImageDraw.Draw(image)
        font, lines, _line_height = VideoGenerationService._moviepy_caption_layout(
            draw,
            "ESTA É UMA CHAMADA MUITO LONGA QUE NÃO PODE CORTAR NAS BORDAS DA TELA",
            576,
            1024,
            False,
        )
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(draw.textlength(line, font=font) <= 576 * 0.84 for line in lines))

    def test_moviepy_renders_short_video_with_clean_ad_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.png"
            Image.new("RGB", (1280, 720), (25, 65, 110)).save(reference)
            output = VideoGenerationService({"video_provider": "moviepy"}).generate(
                VideoGenerationRequest(
                    "gere um video anuncio de criacao de sistemas com audio sem bolhas sem textos com barra",
                    duration_seconds=1,
                    reference_image=reference,
                ),
                root / "output",
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1024)

    def test_moviepy_enlarges_reference_image_on_explicit_revision(self):
        plan = VideoGenerationService._moviepy_scene_plan(
            "regere o ultimo video: a imagem deve ficar bem maior no video"
        )
        self.assertEqual(0.93, plan["reference_coverage"])

    def test_moviepy_creative_plan_supports_presets_and_soundtrack(self):
        base = VideoGenerationService._moviepy_scene_plan("gere um video")
        plan = VideoGenerationService._moviepy_creative_plan(
            base,
            "reel neon futurista dinamico com grid, glitch e trilha instrumental",
        )
        self.assertEqual("NEON FUTURISTA", plan["style_label"])
        self.assertEqual("grid", plan["effect"])
        self.assertEqual("glitch", plan["transition"])
        self.assertGreater(plan["tempo"], 1.0)
        self.assertTrue(plan["soundtrack"])
        self.assertEqual(3, len(plan["scene_captions"]))

    def test_moviepy_creative_plan_can_disable_music_and_use_five_scenes(self):
        base = VideoGenerationService._moviepy_scene_plan("gere um video")
        plan = VideoGenerationService._moviepy_creative_plan(
            base,
            "tutorial passo a passo calmo sem trilha",
        )
        self.assertEqual(5, plan["scene_count"])
        self.assertFalse(plan["soundtrack"])
        self.assertEqual(5, len(plan["scene_captions"]))

    def test_moviepy_creative_plan_makes_beams_and_generic_transition_explicit(self):
        base = VideoGenerationService._moviepy_scene_plan(
            "regere a imagem ocupando quase toda tela"
        )
        plan = VideoGenerationService._moviepy_creative_plan(
            base,
            "regere com transicao e feixes saindo da imagem",
        )
        self.assertEqual(0.985, plan["reference_coverage"])
        self.assertEqual("beams", plan["effect"])
        self.assertEqual("flash", plan["transition"])

    def test_moviepy_beams_are_drawn_over_the_reference_image(self):
        reference = Image.new("RGB", (160, 90), (18, 40, 90))
        background = VideoGenerationService._moviepy_background(320, 180, reference)
        plain = VideoGenerationService._moviepy_creative_plan(
            VideoGenerationService._moviepy_scene_plan("sem bolhas sem textos"),
            "sem bolhas sem textos",
        )
        beams = VideoGenerationService._moviepy_creative_plan(
            VideoGenerationService._moviepy_scene_plan("sem bolhas sem textos"),
            "sem bolhas sem textos com feixes e transicao",
        )
        plain_frame = VideoGenerationService._moviepy_frame(
            background, reference, "", 320, 180, 0.5, 2, plain
        )
        beam_frame = VideoGenerationService._moviepy_frame(
            background, reference, "", 320, 180, 0.5, 2, beams
        )
        self.assertNotEqual(plain_frame.tobytes(), beam_frame.tobytes())

    def test_video_narration_detects_request_and_builds_visual_prompt(self):
        service = VideoNarrationService()
        self.assertTrue(service.is_video_file("demo.MP4"))
        self.assertTrue(service.is_narration_request("Analise este vídeo e crie uma narração"))
        self.assertFalse(service.is_narration_request("Gere um vídeo de uma cidade"))
        analysis = VideoNarrationAnalysis(Path("storyboard.jpg"), (0.5, 8.0, 16.5))
        prompt = service.build_narration_prompt("narre a demonstração", analysis, target_duration_seconds=30)
        self.assertIn("0.5s, 8.0s, 16.5s", prompt)
        self.assertIn("não invente", prompt)
        self.assertIn("30 segundos", prompt)

    def test_video_narration_replaces_original_audio_with_narration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "source.mp4"
            narration = root / "narration.wav"
            target = root / "output" / "narrated.mp4"
            video.write_bytes(b"video")
            narration.write_bytes(b"audio")
            service = VideoNarrationService()
            service.read_duration = lambda _source: 12.5

            commands = []
            def fake_run(command, **_kwargs):
                commands.append(command)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"narrated-video")
                return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

            import unittest.mock as mock
            with mock.patch("modules.video_generation.shutil.which", return_value="ffmpeg"), mock.patch(
                "modules.video_generation.subprocess.run", side_effect=fake_run
            ):
                result = service.replace_audio_with_narration(video, narration, target)

        self.assertEqual(target, result)
        self.assertIn("0:v:0", commands[0])
        self.assertNotIn("0:a:0", commands[0])
        self.assertIn("[narration]", commands[0])

    def test_video_narration_samples_evenly_inside_video(self):
        timestamps = VideoNarrationService._sample_timestamps(20.0, 4)
        self.assertEqual(4, len(timestamps))
        self.assertGreater(timestamps[0], 0)
        self.assertLess(timestamps[-1], 20.0)
        self.assertEqual(timestamps, sorted(timestamps))


if __name__ == "__main__":
    unittest.main()

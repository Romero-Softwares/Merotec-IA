from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.video_generation import VideoGenerationRequest, VideoGenerationService


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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from modules.voice import (
    DEFAULT_EDGE_VOICE_ID,
    TTS_ENGINE_EDGE,
    VoiceModule,
    _partes_para_fala,
    _select_tts_voice,
    _voice_preference_score,
)


class VoiceTtsTests(unittest.TestCase):
    def test_prefers_male_pt_br_voice(self):
        female_pt = SimpleNamespace(
            id="HKEY_LOCAL_MACHINE\\VOICE\\Maria",
            name="Microsoft Maria Desktop - Portuguese Brazil",
            gender="Female",
            languages=["pt-BR"],
        )
        male_pt = SimpleNamespace(
            id="HKEY_LOCAL_MACHINE\\VOICE\\Antonio",
            name="Microsoft Antonio Desktop - Portuguese Brazil",
            gender="Male",
            languages=["pt-BR"],
        )

        self.assertGreater(_voice_preference_score(male_pt), _voice_preference_score(female_pt))

    def test_female_does_not_match_male_term(self):
        female = SimpleNamespace(id="voice-female", name="Female English", gender="Female", languages=["en-US"])
        male = SimpleNamespace(id="voice-male", name="Male English", gender="Male", languages=["en-US"])

        self.assertGreater(_voice_preference_score(male), _voice_preference_score(female))

    def test_speech_parts_remove_agent_actions_and_keep_short_chunks(self):
        parts = _partes_para_fala("[READ: main.py] Olá! " + ("texto " * 120))

        self.assertTrue(parts)
        self.assertNotIn("READ", " ".join(parts))
        self.assertLessEqual(max(len(part) for part in parts), 420)

    def test_speech_parts_do_not_drop_long_words(self):
        long_word = "x" * 901

        parts = _partes_para_fala(long_word)

        self.assertEqual(long_word, "".join(parts))
        self.assertLessEqual(max(len(part) for part in parts), 420)

    def test_configured_edge_voice_is_selected_exactly(self):
        voices = [
            {
                "id": "pt-BR-FranciscaNeural",
                "name": "Microsoft Francisca Neural - Portuguese Brazil",
                "engine": TTS_ENGINE_EDGE,
                "gender": "Female",
                "languages": ["pt-BR"],
            },
            {
                "id": DEFAULT_EDGE_VOICE_ID,
                "name": "Microsoft Antonio Neural - Portuguese Brazil",
                "engine": TTS_ENGINE_EDGE,
                "gender": "Male",
                "languages": ["pt-BR"],
            },
        ]

        selected = _select_tts_voice(voices, DEFAULT_EDGE_VOICE_ID, engine_name=TTS_ENGINE_EDGE)

        self.assertEqual(selected["id"], DEFAULT_EDGE_VOICE_ID)

    def test_edge_tts_failure_falls_back_to_sapi(self):
        voice = VoiceModule({"tts_engine": "auto"})
        calls = []

        def fail_edge(_text):
            calls.append("edge")
            raise RuntimeError("network down")

        def speak_sapi(_text):
            calls.append("sapi")
            return True

        voice._speak_with_edge_tts = fail_edge
        voice._speak_with_sapi = speak_sapi

        with redirect_stdout(StringIO()):
            self.assertTrue(voice._speak_text_once("ola"))
        self.assertEqual(calls, ["edge", "sapi"])

    def test_edge_tts_does_not_silently_replace_neural_voice_with_sapi(self):
        voice = VoiceModule({"tts_engine": "edge"})
        calls = []

        def fail_edge(_text):
            calls.append("edge")
            raise RuntimeError("network down")

        def speak_sapi(_text):
            calls.append("sapi")
            return True

        voice._speak_with_edge_tts = fail_edge
        voice._speak_with_sapi = speak_sapi

        with redirect_stdout(StringIO()):
            self.assertFalse(voice._speak_text_once("ola"))
        self.assertEqual(calls, ["edge"])

    def test_generate_audio_file_uses_local_fallback_when_edge_is_unavailable(self):
        voice = VoiceModule({"tts_engine": "auto"})
        calls = []

        def fail_edge(_text, _destination):
            calls.append("edge")
            raise RuntimeError("offline")

        def save_sapi(_text, destination):
            calls.append("sapi")
            Path(destination).write_bytes(b"RIFFtestWAVE")
            return True

        voice._save_with_edge_tts = fail_edge
        voice._save_with_sapi = save_sapi
        with TemporaryDirectory() as temp_dir, redirect_stdout(StringIO()):
            result = Path(voice.generate_audio_file("Olá, Merotec.", temp_dir))

        self.assertEqual(calls, ["edge", "sapi"])
        self.assertEqual(result.suffix, ".wav")
        self.assertEqual(result.parent, Path(temp_dir))

    def test_generate_audio_file_uses_windows_speech_after_sapi_failure(self):
        voice = VoiceModule({"tts_engine": "sapi"})
        calls = []

        def fail_sapi(_text, _destination):
            calls.append("sapi")
            raise RuntimeError("Classe não registrada")

        def save_windows(_text, destination):
            calls.append("windows_speech")
            Path(destination).write_bytes(b"RIFFtestWAVE" + b"\0" * 48)
            return True

        voice._save_with_sapi = fail_sapi
        voice._save_with_windows_speech = save_windows
        with TemporaryDirectory() as temp_dir, redirect_stdout(StringIO()):
            result = Path(voice.generate_audio_file("Olá, Merotec.", temp_dir))

        self.assertEqual(calls, ["sapi", "windows_speech"])
        self.assertEqual(result.suffix, ".wav")


if __name__ == "__main__":
    unittest.main()

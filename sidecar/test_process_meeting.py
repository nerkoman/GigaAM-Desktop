import unittest
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np

from .meeting_transcript import Utterance
from .process_meeting import diarize, main, read_tracks, transcribe_turns


class ProcessMeetingTest(unittest.TestCase):
    def test_microphone_keeps_known_speaker_and_offset(self):
        segments = transcribe_turns(
            samples=[0.0] * 16000,
            turns=[(0, 8000, "SPEAKER_00")],
            kind="microphone",
            offset_seconds=0.5,
            recognizer=lambda samples, sample_rate: "привет",
        )
        self.assertEqual((segments[0].speaker_key, segments[0].ts_start_ms), ("microphone", 500))

    def test_system_uses_detected_speaker_and_skips_empty_text(self):
        responses = iter(["текст", " "])
        segments = transcribe_turns(
            samples=[0.0] * 32000,
            turns=[(0, 8000, "SPEAKER_01"), (8000, 16000, "SPEAKER_02")],
            kind="system",
            offset_seconds=0.0,
            recognizer=lambda samples, sample_rate: next(responses),
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker_key, "SPEAKER_01")

    def test_missing_tracks_are_rejected(self):
        with self.assertRaises(ValueError):
            read_tracks(Path(__file__).parent)

    def test_legacy_archive_without_manifest_uses_separate_tracks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for file in ("microphone.flac", "system-recording.wav", "mixed.ogg", "system.f32"):
                (root / file).touch()
            self.assertEqual([(t["kind"], t["path"].name) for t in read_tracks(root)],
                             [("microphone", "microphone.flac"), ("system", "system-recording.wav")])

    def test_known_speaker_count_is_a_pipeline_argument(self):
        pipeline = Mock()
        pipeline.return_value.exclusive_speaker_diarization.itertracks.return_value = []
        diarize(np.zeros(16000, dtype=np.float32), pipeline, speaker_count=1)
        self.assertEqual(pipeline.call_args.kwargs, {"num_speakers": 1})
        self.assertNotIn("num_speakers", pipeline.call_args.args[0])

    def test_unavailable_ollama_preserves_diarized_output_and_reports_partial_success(self):
        result = [Utterance("SPEAKER_00", "Участник 1", "unknown", "Привет", 0, 1000)]
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "protocol.md").write_text("Предыдущий протокол")
            argv = ["sidecar", ".", "--asr-model", ".", "--diarization-model", ".", "--output", directory, "--json-result"]
            with patch("sys.argv", argv), patch("sidecar.process_meeting.process_session", return_value=(result, "Привет")), \
                 patch("sidecar.process_meeting.generate_protocol", side_effect=ConnectionError("offline")), redirect_stdout(io.StringIO()) as stream:
                main()
            self.assertIn("offline", json.loads(stream.getvalue())["protocol_warning"])
            self.assertEqual(json.loads((output / "segments.json").read_text())[0]["speaker_name"], "Участник 1")
            self.assertEqual((output / "protocol.md").read_text(), "Предыдущий протокол")


if __name__ == "__main__":
    unittest.main()

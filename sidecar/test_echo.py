import unittest

from .echo import suppress_microphone_echo
from .meeting_transcript import Segment


class EchoTest(unittest.TestCase):
    def test_removes_matching_playback_from_microphone(self):
        segments = [
            Segment("microphone", "Пару секунд растояли", 1000, 2000),
            Segment("SPEAKER_00", "Пару секунд растояли", 1100, 2100),
        ]
        result = suppress_microphone_echo(segments)
        self.assertEqual([segment.speaker_key for segment in result], ["SPEAKER_00"])

    def test_preserves_different_simultaneous_speech(self):
        segments = [
            Segment("microphone", "Я согласен с этим предложением", 1000, 2000),
            Segment("SPEAKER_00", "Совершенно другая фраза собеседника", 1000, 2000),
        ]
        self.assertEqual(len(suppress_microphone_echo(segments)), 2)

    def test_removes_exact_short_answer(self):
        segments = [
            Segment("microphone", "Да", 1000, 1500),
            Segment("SPEAKER_00", "Да", 1100, 1600),
        ]
        self.assertEqual(len(suppress_microphone_echo(segments)), 1)


if __name__ == "__main__":
    unittest.main()

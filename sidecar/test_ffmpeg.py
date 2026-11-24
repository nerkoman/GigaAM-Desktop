import os
import unittest
from unittest.mock import patch

from .process_meeting import ffmpeg_binary


class FFmpegTest(unittest.TestCase):
    def test_explicit_binary_wins(self):
        with patch.dict(os.environ, {"FFMPEG_BINARY": "/custom/ffmpeg"}):
            self.assertEqual(ffmpeg_binary(), "/custom/ffmpeg")

    def test_homebrew_fallback_without_gui_path(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("sidecar.process_meeting.shutil.which", return_value=None), \
             patch("sidecar.process_meeting.Path.exists", return_value=True):
            self.assertEqual(ffmpeg_binary(), "/opt/homebrew/bin/ffmpeg")


if __name__ == "__main__":
    unittest.main()

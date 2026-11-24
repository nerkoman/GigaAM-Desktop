import unittest

from .process_meeting import clean_protocol


class ProtocolTest(unittest.TestCase):
    def test_removes_reasoning(self):
        self.assertEqual(clean_protocol("рассуждение\n</think>\n## Протокол\n"), "## Протокол")

    def test_preserves_clean_answer(self):
        self.assertEqual(clean_protocol("## Протокол\n"), "## Протокол")


if __name__ == "__main__":
    unittest.main()

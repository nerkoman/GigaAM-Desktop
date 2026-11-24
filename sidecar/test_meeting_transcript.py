# -*- coding: utf-8 -*-
"""Тесты сборки транскрипта: склейка, overlap, unknown-нумерация, сортировка."""
import unittest
from .meeting_transcript import Segment, assemble, render_markdown

NAMES = {"s_vadim": "Вадим Мамаев", "s_alena": "Алёна"}
resolve = lambda k: NAMES.get(k)


class TestAssemble(unittest.TestCase):
    def test_merge_same_speaker_within_gap(self):
        segs = [Segment("s_vadim", "Привет", 0, 1000),
                Segment("s_vadim", "как дела", 1200, 2000)]
        u = assemble(segs, resolve)
        self.assertEqual(len(u), 1)
        self.assertEqual(u[0].text, "Привет как дела")
        self.assertEqual(u[0].speaker_name, "Вадим Мамаев")
        self.assertEqual(u[0].kind, "known")
        self.assertEqual(u[0].ts_end_ms, 2000)

    def test_no_merge_over_big_gap(self):
        segs = [Segment("s_vadim", "Раз", 0, 1000), Segment("s_vadim", "Два", 5000, 6000)]
        self.assertEqual(len(assemble(segs, resolve)), 2)

    def test_speaker_switch_splits(self):
        segs = [Segment("s_vadim", "Вопрос", 0, 1000), Segment("s_alena", "Ответ", 1100, 2000)]
        u = assemble(segs, resolve)
        self.assertEqual([x.speaker_name for x in u], ["Вадим Мамаев", "Алёна"])

    def test_overlap_marked_both(self):
        segs = [Segment("s_vadim", "Я думаю что", 0, 3000),
                Segment("s_alena", "перебиваю", 1000, 2000)]
        u = assemble(segs, resolve)
        kinds = {x.speaker_name: x.kind for x in u}
        self.assertEqual(kinds["Вадим Мамаев"], "overlap")
        self.assertEqual(kinds["Алёна"], "overlap")

    def test_unknown_stable_numbering(self):
        segs = [Segment("s_x", "раз", 0, 500), Segment("s_y", "два", 600, 1000),
                Segment("s_x", "три", 5000, 5500)]
        u = assemble(segs, resolve)
        by = [(x.speaker_name, x.kind) for x in u]
        self.assertEqual(by, [("Участник 1", "unknown"), ("Участник 2", "unknown"),
                              ("Участник 1", "unknown")])

    def test_out_of_order_input_sorted(self):
        segs = [Segment("s_alena", "второе", 2000, 3000), Segment("s_vadim", "первое", 0, 1000)]
        u = assemble(segs, resolve)
        self.assertEqual([x.text for x in u], ["первое", "второе"])

    def test_empty_and_blank(self):
        self.assertEqual(assemble([]), [])
        u = assemble([Segment("s_vadim", "   ", 0, 500), Segment("s_vadim", "текст", 600, 1000)], resolve)
        self.assertEqual(len(u), 1); self.assertEqual(u[0].text, "текст")

    def test_mixed_known_unknown_and_render(self):
        segs = [Segment("s_vadim", "Открываю встречу", 0, 2000),
                Segment("s_z", "Реплика гостя", 2200, 3000)]
        u = assemble(segs, resolve)
        self.assertEqual(u[0].kind, "known")
        self.assertEqual(u[1].speaker_name, "Участник 1")
        md = render_markdown(u)
        self.assertIn("**Вадим Мамаев**", md); self.assertIn("[00:00]", md)
        self.assertIn("**Участник 1**", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)

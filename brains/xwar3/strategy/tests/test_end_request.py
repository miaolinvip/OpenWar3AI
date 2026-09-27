# -*- coding: utf-8 -*-
"""「这一局是谁结束的」要能查（run_brain.end_request / end_request_text）。

09-23 上一局被记成手动结束，但实际并没有人手动点结束 ——
那一轮 6 局里有 4 局记成 ended-by-operator，日志只有「手动结束」四个字，
既分不出是网页点的还是脚本发的，也看不出是什么时候发的条子。
farsight 的 mark_operator_end 现在把来源写进 end_request.json（why / who / at），这里读出来打进局末那行。
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_brain as RB  # noqa: E402


def note(d, **kw):
    with open(os.path.join(d, "end_request.json"), "w", encoding="utf-8") as fh:
        json.dump(kw, fh, ensure_ascii=False)


class EndRequest(unittest.TestCase):
    def test_no_note_means_nobody_ended_it(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(RB.end_request(d, time.time()))
            self.assertFalse(RB.operator_ended(d, time.time()))
            self.assertEqual(RB.end_request_text(None), "")

    def test_note_from_this_match_says_who(self):
        with tempfile.TemporaryDirectory() as d:
            note(d, why="console-end-game", wall=time.time(), who="脚本·127.0.0.1·curl/8.4", at="2026-09-23 01:31:22")
            got = RB.end_request(d, time.time() - 10)
            self.assertEqual(got["who"], "脚本·127.0.0.1·curl/8.4")
            self.assertTrue(RB.operator_ended(d, time.time() - 10))
            text = RB.end_request_text(got)
            for part in ("01:31:22", "console-end-game", "脚本"):
                self.assertIn(part, text)

    def test_a_note_older_than_this_match_does_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            note(d, why="console-end-game", wall=time.time(), who="网页·127.0.0.1")
            self.assertIsNone(RB.end_request(d, time.time() + 10), "上一局的条子不能算到这一局头上")
            self.assertFalse(RB.operator_ended(d, time.time() + 10))

    def test_a_broken_note_still_counts_as_operator_ended(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "end_request.json"), "w", encoding="utf-8") as fh:
                fh.write("{坏了")
            self.assertEqual(RB.end_request(d, time.time() - 10), {})     # 条子在就是有人按过
            self.assertTrue(RB.operator_ended(d, time.time() - 10))
            self.assertEqual(RB.end_request_text({}), "")


if __name__ == "__main__":
    unittest.main()

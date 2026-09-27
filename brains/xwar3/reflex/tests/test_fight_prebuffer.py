# -*- coding: utf-8 -*-
"""mod_fight 接触前预录（worldmodel/REQUESTS_TO_AI_SESSION.md #2）：没交战时每 0.5 秒存一帧、留最近 10 秒，接触时先写进帧文件。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import battles  # noqa: E402
import mod_fight as F  # noqa: E402


class PreBuffer(unittest.TestCase):
    def test_rate_and_window(self):
        b = F.PreBuffer(keep=10.0, every=0.5)
        added = sum(b.add(t / 10.0, lambda: [[1]]) for t in range(0, 121))     # 0.0 ~ 12.0 秒，每 0.1 秒问一次
        self.assertEqual(added, 25)                                            # 每 0.5 秒一帧
        out = b.drain(12.0)
        self.assertEqual([d["t"] for d in out][0], -10.0)                      # 只留最近 10 秒（t 相对接触时刻，负数）
        self.assertEqual(out[-1]["t"], 0.0)
        self.assertTrue(all(d["pre"] == 1 and d["u"] == [[1]] for d in out))
        self.assertEqual(b.drain(12.0), [])                                    # 写过就清空

    def test_rows_only_built_when_stored(self):
        b = F.PreBuffer(keep=10.0, every=0.5)
        calls = []
        b.add(0.0, lambda: calls.append(1) or [])
        b.add(0.1, lambda: calls.append(1) or [])
        self.assertEqual(len(calls), 1)


class SegmentIgnoresPreFrames(unittest.TestCase):
    def test_pre_frames_do_not_start_a_fight(self):
        # 行：[addr, type, owner, x, y, hp, order, tlo, thi, hpmax, hlo, hhi]
        def row(a, owner, x):
            return [a, "hfoo", owner, x, 0, 400, 0, -1, -1, 420, a, 1]
        far = [row(1, 0, 0), row(2, 1, 5000)]
        near = [row(1, 0, 0), row(2, 1, 500)]
        frames = [(-2.0, far), (-1.5, far), (0.0, near), (0.15, near)]
        self.assertEqual(battles.segment(frames, 0), [(2, 3)])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""游戏内时间（世界块扩展区）：is_night / seconds_until 的算法；扩展区缺失时是 None 不是 0。"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
import w3world as W  # noqa: E402


def state(tod=None, scale=None):
    s = W.WorldState(1, 0, 10.0, 1, 0, True, 200, 50, 0, 0, 0, [], [], [])
    s.time_of_day, s.tod_scale = tod, scale
    return s


class Stub(openwar3.Game):
    def __init__(self, s):
        self._s = s

    def snapshot(self, max_age=0.05):
        return self._s


class TimeOfDay(unittest.TestCase):
    def test_night_boundaries(self):
        self.assertEqual([state(t).is_night for t in (5.9, 6.0, 17.99, 18.0, 23.5, 0.0)],
                         [True, False, False, True, True, True])
        self.assertIsNone(state().is_night)

    def test_seconds_until_dusk(self):
        g = Stub(state(8.0, 1.0))
        self.assertAlmostEqual(g.seconds_until(18), 200.0)            # 10 小时 × 20 游戏秒
        self.assertAlmostEqual(g.seconds_until(6), 440.0)             # 跨午夜
        self.assertAlmostEqual(Stub(state(8.0, 2.0)).seconds_until(18), 100.0)   # 昼夜流速 ×2
        self.assertIsNone(Stub(state()).seconds_until(18))
        self.assertIsNone(Stub(state()).is_night())

    def test_ext_block_constants(self):
        self.assertEqual(struct.pack("<I", W.EXT_MAGIC), b"EXT1")
        self.assertEqual(W.OFF_EXT, W.WORLD_SIZE)


if __name__ == "__main__":
    unittest.main()

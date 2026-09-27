# -*- coding: utf-8 -*-
"""远见画板接口（09-25）：JSON 里的单位地址换成快照里的单位；center 在 circle 里是圆心、在文字里是「居中」开关
（true 也是 int，第一版把它当成单位地址去找，09-25 实测报「快照里没有地址 True 的单位」）；缺 key/kind 报原来那一条。"""
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import canvasview as CV  # noqa: E402


class FakeCanvas:
    def __init__(self, units):
        snap = NS(units=units)
        self.g = NS(snapshot=lambda max_age=0.05: snap)
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **kw: self.calls.append((name, a, kw))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.hero = NS(addr=1234, x=1.0, y=2.0)
        self.c = FakeCanvas([self.hero])

    def test_center_flag_vs_circle_center(self):
        CV._apply(self.c, {"key": "t", "kind": "text", "text": "hi", "frac": [0.5, 0.1], "center": True})
        name, args, kw = self.c.calls[-1]
        self.assertEqual((name, args, kw["center"], kw["frac"]), ("text", ("t", "hi"), True, (0.5, 0.1)))
        CV._apply(self.c, {"key": "z", "kind": "circle", "center": 1234, "radius": 300})
        name, args, kw = self.c.calls[-1]
        self.assertIs(args[1], self.hero)                   # 圆心 = 那个单位（跟着走）

    def test_units_and_points(self):
        CV._apply(self.c, {"key": "hp", "kind": "bar", "value": 0.5, "unit": 1234})
        self.assertIs(self.c.calls[-1][2]["unit"], self.hero)
        CV._apply(self.c, {"key": "r", "kind": "path", "points": [1234, [5, 6]]})
        self.assertEqual(self.c.calls[-1][1][1], [self.hero, (5, 6)])
        with self.assertRaises(ValueError):
            CV._apply(self.c, {"key": "x", "kind": "bar", "unit": 999})     # 快照里没有

    def test_bad_kind_reports_original(self):
        with self.assertRaises(ValueError) as e:
            CV._apply(self.c, {"key": "x", "kind": "star"})
        self.assertIn("star", str(e.exception))


if __name__ == "__main__":
    unittest.main()

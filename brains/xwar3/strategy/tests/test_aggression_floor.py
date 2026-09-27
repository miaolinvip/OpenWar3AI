# -*- coding: utf-8 -*-
"""进攻性下限：AMAI 人格的进攻性列可以是负的（实机抽到 RomuLus -11），叠上「英雄全死了 -10」会变成 -12，
整局 0% 出击、被推平。下限默认 0，可在控制台改。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import config as CFG  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def snap(units, gold=300, lumber=300):
    return SimpleNamespace(units=units, me=0, gold=gold, lumber=lumber, food_used=30, food_cap=50)


class TestAggressionFloor(unittest.TestCase):
    def test_default_floor_is_zero(self):
        self.assertEqual(CFG.DEFAULTS["aggression_floor"], 0.0)

    def test_floor_applied(self):
        units = [U(0x100, "htow", 0, 0), U(0x800, "unpl", 9000, 0, owner=1)]
        units += [U(0x300 + i, "hpea", 100, 0) for i in range(5)]
        for floor, expect_at_least in ((0.0, 0.0), (-20.0, -20.0)):
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "brain_config.json"), "w", encoding="utf-8") as fh:
                    json.dump({"aggression_floor": floor}, fh)
                b = Brain(FakeCmd(), None, d)
                b.act.scan_trees = lambda x, y: []
                b.step(snap(units), 200.0)
                self.assertGreaterEqual(b.world.aggression, expect_at_least)
        # 没有英雄时 AMAI 公式会 -10，下限 0 的话不能是负的
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.act.scan_trees = lambda x, y: []
            b.step(snap(units), 200.0)
            self.assertGreaterEqual(b.world.aggression, 0.0)


if __name__ == "__main__":
    unittest.main()

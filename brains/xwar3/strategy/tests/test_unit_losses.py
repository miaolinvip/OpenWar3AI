# -*- coding: utf-8 -*-
"""阵亡记账（09-18 晚复盘：前 7.5 游戏分钟死约 5 个兵，战斗账本只记打玩家的仗，打野死的约 3 个从没进账）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def world(units, t):
    base = [U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    return build_world(base + units, 0, t, 300, 300, 20, 40)


class UnitLosses(unittest.TestCase):
    def test_classify_by_what_was_near(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            creep_side = [U(10, "hfoo", 4000, 0), U(11, "hfoo", 4000, 80), U(700, "nwlg", 4300, 0, owner=12)]
            foe_side = [U(12, "hrif", -4000, 0), U(800, "ugho", -4300, 0, owner=1)]
            home_side = [U(13, "hfoo", 200, 200)]
            summon = [U(14, "hwat", 4000, 160)]
            before = world(creep_side + foe_side + home_side + summon, 100.0)
            after = world([creep_side[1], creep_side[2], foe_side[1]], 101.0)     # 10、12、13 死了，水元素到期
            b._unit_losses(before, after, 101.0)
            self.assertEqual((b.counts["阵亡·打野"], b.counts["阵亡·打玩家"], b.counts["阵亡·别处"]), (1, 1, 1))
            self.assertEqual(b.counts["阵亡·打野·hfoo"], 1)
            self.assertEqual(dict(b.tick_deaths), {"打野": 1, "打玩家": 1, "别处": 1})
            self.assertTrue(any("打野阵亡：hfoo" in n for _t, n in b.notes), b.notes)

    def test_shrunk_snapshot_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            before = world([U(10 + i, "hfoo", 4000, 80 * i) for i in range(10)], 100.0)
            after = build_world([U(1, "htow", 0, 0)], 0, 101.0, 0, 0, 0, 0)
            b._unit_losses(before, after, 101.0)
            self.assertEqual(sum(b.tick_deaths.values()), 0)


if __name__ == "__main__":
    unittest.main()

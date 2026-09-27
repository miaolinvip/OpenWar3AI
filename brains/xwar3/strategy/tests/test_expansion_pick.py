# -*- coding: utf-8 -*-
"""挑分矿：距离 + 野怪强度 × EXP_GUARD_W，同样远近先挑野怪弱的；被排除的候选记下原因。

09-18 AMAI 引擎测试局：清掉最近那座分矿的野怪之后一次开矿都没试 —— 原来只挑最近的，
最近那座一被临时排除就换去打一座强得多的矿，「清分矿·兵力不够」×46。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HOME = (0, 0)


def world(near_guard=0, far_guard=0, foe_near_mine=False):
    hall = U(0x100, "htow", *HOME)
    home_mine = U(0x600, "ngol", 500, 500, owner=15)
    near = U(0x601, "ngol", 4000, 0, owner=15)          # 离家 4000
    far = U(0x602, "ngol", 0, 4800, owner=15)           # 离家 4800
    units = [hall, home_mine, near, far] + [U(0x300 + i, "hpea", 200, 0) for i in range(8)]
    units += [U(0x700 + i, "nogr", 4000 + 150 * i, 300, owner=12) for i in range(near_guard)]
    units += [U(0x800 + i, "nwlg", 150 * i, 4800 + 300, owner=12) for i in range(far_guard)]
    if foe_near_mine:
        units += [U(0x900 + i, "ugho", 4200, 200, owner=1) for i in range(2)]
    return build_world(units, 0, 300.0, 500, 500, 20, 40)


class TestExpansionPick(unittest.TestCase):
    def pick(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            m = b._expansion_mine(world(**kw))
            return (m.addr if m else None), b.counts

    def test_nearest_when_both_clean(self):
        self.assertEqual(self.pick()[0], 0x601)

    def test_strong_guard_on_near_mine_prefers_clean_far_one(self):
        addr, _c = self.pick(near_guard=4)             # 近的有 4 只食人魔，远的只多 800 且没野怪
        self.assertEqual(addr, 0x602)

    def test_weak_guard_still_takes_near_one(self):
        addr, _c = self.pick(near_guard=1, far_guard=3)
        self.assertEqual(addr, 0x601)

    def test_exclusion_reason_counted(self):
        addr, counts = self.pick(foe_near_mine=True)
        self.assertEqual(addr, 0x602)
        self.assertGreaterEqual(counts.get("分矿候选排除·敌兵2000内", 0), 1)

    def test_weight_is_positive(self):
        self.assertGreater(RT.EXP_GUARD_W, 0)


if __name__ == "__main__":
    unittest.main()

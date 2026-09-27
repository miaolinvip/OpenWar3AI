# -*- coding: utf-8 -*-
"""近矿都采空之后要去远矿，而不是站着（09-21）。

4-009 局金币收入卡死：金矿被挖空，没钱再造基地，其余农民也不去另一座矿采集，一直停在原地。

实机 match-20260921-020051-009：1922~2401 游戏秒金子**恒定 24**、20 个农民、2 座大厅。
`job_gold` 的矿候选是「离我方大厅 1000 以内」，主矿和分矿都采空之后这个列表为空，
函数直接 return —— 农民彻底没活干，收入归零，也就再也攒不出开新矿的钱。

War3 里农民可以去**任何**金矿采，金子运回最近的大厅；远只是慢，不是不能采。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain.runtime import Brain, FAR_MINE_CREEP_R, FAR_MINE_FOE_R  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U, FakeCmd  # noqa: E402

HOME = (-5184.0, 2944.0)


def scene(mines, creeps=(), foes=(), foe_hall=(9000.0, 9000.0)):
    """mines = [(x, y), ...] 全是中立金矿；我方一座大厅在 HOME。"""
    units = [U(1, "htow", *HOME), U(900, "unpl", *foe_hall, owner=1)]
    units += [U(300 + i, "hpea", HOME[0] + 40 * i, HOME[1]) for i in range(6)]
    for i, (x, y) in enumerate(mines):
        units.append(U(0x400 + i, "ngol", x, y, owner=15))
    for i, (x, y) in enumerate(creeps):
        units.append(U(0x500 + i, "nogm", x, y, owner=12))
    for i, (x, y) in enumerate(foes):
        units.append(U(0x600 + i, "hfoo", x, y, owner=1))
    return build_world(units, 0, 1900.0, 24, 500, 40, 60)


def brain_with(w):
    b = Brain(FakeCmd(), None, tempfile.mkdtemp())
    return b, [h for h in w.my_halls]


class FarMineFallback(unittest.TestCase):
    def test_picks_the_nearest_usable_far_mine(self):
        near = (HOME[0] + 8000.0, HOME[1])
        far = (HOME[0] + 12000.0, HOME[1])
        w = scene([far, near])
        b, halls = brain_with(w)
        out = b._far_mines(w, halls)
        self.assertTrue(out, "近矿都没了时应该给出远矿")
        self.assertAlmostEqual(out[0].x, near[0], delta=1.0)

    def test_skips_mines_next_to_the_enemy_base(self):
        """敌方老家门口的矿别去 —— 农民过去就是送人头。"""
        foe_hall = (9000.0, 9000.0)
        beside_foe = (foe_hall[0] + FAR_MINE_FOE_R - 200.0, foe_hall[1])
        w = scene([beside_foe], foe_hall=foe_hall)
        b, halls = brain_with(w)
        self.assertFalse(b._far_mines(w, halls))

    def test_skips_mines_guarded_by_creeps(self):
        mine = (HOME[0] + 8000.0, HOME[1])
        w = scene([mine], creeps=[(mine[0] + FAR_MINE_CREEP_R - 100.0, mine[1])])
        b, halls = brain_with(w)
        self.assertFalse(b._far_mines(w, halls))

    def test_skips_mines_with_enemy_army_nearby(self):
        mine = (HOME[0] + 8000.0, HOME[1])
        w = scene([mine], foes=[(mine[0] + FAR_MINE_FOE_R - 100.0, mine[1])])
        b, halls = brain_with(w)
        self.assertFalse(b._far_mines(w, halls))

    def test_depleted_mines_are_excluded(self):
        """已经判过采空的矿不能又被远矿兜底捡回来 —— 那就成了来回派人的死循环。"""
        mine = (HOME[0] + 8000.0, HOME[1])
        w = scene([mine])
        b, halls = brain_with(w)
        m = next(u for u in w.mines)
        b.depleted_mines.add((m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)))
        self.assertFalse(b._far_mines(w, halls))

    def test_at_most_two_mines(self):
        """只取最近的两座：金子要运回大厅，把农民撒满全图等于没有收入。"""
        w = scene([(HOME[0] + 6000.0 + 800.0 * i, HOME[1]) for i in range(5)])
        b, halls = brain_with(w)
        self.assertLessEqual(len(b._far_mines(w, halls)), 2)

    def test_no_halls_means_no_far_mines(self):
        """没有大厅时采了也没处放，不派。"""
        w = scene([(HOME[0] + 8000.0, HOME[1])])
        b, _ = brain_with(w)
        self.assertFalse(b._far_mines(w, []))


if __name__ == "__main__":
    unittest.main()

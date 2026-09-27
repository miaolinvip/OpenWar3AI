# -*- coding: utf-8 -*-
"""开矿先要有兵、不去对面那半边开矿（runtime._expand_hold / _expansion_mine）。

09-22 晚 不在敌方基地附近开三矿，先把自家兵力养起来；没有兵顶不住敌方频繁骚扰（1-414，对 AMAI 兽族）。
1-414：5 分钟 6 兵开二矿；14 分钟我方 9 兵（血 5227）vs 对面 16 个（6634）
开在对面二矿位 (3776,-2688)，5 分钟后三矿、两座哨塔全丢（「分矿守不住（兵力比 0.37~0.55）」），
整局民兵号召 9 次、守军战力比常年 0~1，26.7 分钟认输。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

# Echo Isles 实测坐标（和 test_runtime_smoke.test_expansion_mine_enemy_natural 同一套）
MINES = ((-5632, 3584), (-4864, -3200), (5120, 3584), (4352, -3200))
MAIN, NATURAL, FOE_MAIN = (-5184, 2944), (-4352, -2624), (4672, 2944)


def world(ours=(), foes=(), natural=True, t=900.0):
    units = [U(0x600 + i, "ngol", x, y, owner=15) for i, (x, y) in enumerate(MINES)]
    units += [U(0x100, "hcas", *MAIN)] + ([U(0x101, "htow", *NATURAL)] if natural else [])
    units += [U(0x300 + i, "hpea", MAIN[0] + 200, MAIN[1] - 100 * i) for i in range(4)]
    units += [U(0x800, "ofrt", *FOE_MAIN, owner=1), U(0x801, "obar", 4096, 2752, owner=1)]
    units += list(ours) + list(foes)
    return build_world(units, 0, t, 3000, 800, 50, 70)


def army(code, n, x, y, owner=0, base=0x200):
    return [U(base + i, code, x, y + 60 * i, owner=owner) for i in range(n)]


class ExpandHold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Brain(FakeCmd(), None, self.tmp.name)

    def test_1414_third_mine_waits_until_the_army_is_built(self):
        ours = army("hfoo", 5, -4000, 2000) + army("hrif", 4, -4100, 2000, base=0x220)
        foes = army("ogru", 12, 3000, 2000, owner=1, base=0x900) + army("ohun", 3, 3100, 2000, owner=1, base=0x920)
        w = world(ours, foes)
        self.assertLess(w.own_strength, RT.EXP_AHEAD_3 * w.foe_strength, "前提：这批兵确实不够")
        self.assertEqual(self.b.check(w, Want("expand", "htow", 3, 64, "mine")), "开矿·兵力没养起来")
        more = ours + army("hkni", 12, -4200, 2000, base=0x240)
        w2 = world(more, foes)
        self.assertGreaterEqual(w2.own_strength, RT.EXP_AHEAD_3 * w2.foe_strength)
        self.assertNotEqual(self.b.check(w2, Want("expand", "htow", 3, 64, "mine")), "开矿·兵力没养起来")

    def test_natural_needs_parity_not_dominance(self):
        foes = army("ogru", 3, 3000, 2000, owner=1, base=0x900)
        ours = army("hfoo", 6, -4000, 2000)
        w = world(ours, foes, natural=False)
        self.assertGreaterEqual(w.own_strength, RT.EXP_AHEAD_2 * w.foe_strength)
        self.assertIsNone(self.b._expand_hold(w))
        w2 = world(ours, army("ogru", 10, 3000, 2000, owner=1, base=0x900), natural=False)   # 12 对 30 = 0.4
        self.assertEqual(self.b._expand_hold(w2), "开矿·兵力没养起来")

    def test_1418_natural_is_not_held_when_roughly_even(self):
        """09-23 分矿基地又迟迟造不起来，周围明明没有东西挡着（1-418）—— 1.0 倍把二矿从 5 分钟拖到 14 分钟，
        挡它的是「11 对 12」「22 对 27」。自家二矿只在明显打不过时缓。"""
        w = world(army("hfoo", 6, -4000, 2000), army("ogru", 5, 3000, 2000, owner=1, base=0x900), natural=False)   # 12 对 15 = 0.8
        self.assertLess(w.own_strength, w.foe_strength)
        self.assertIsNone(self.b._expand_hold(w))

    def test_only_enemy_side_mines_left_says_so(self):
        foes = army("ogru", 6, 3000, 2000, owner=1, base=0x900)
        w = world(army("hkni", 8, -4000, 2000), foes)
        self.assertEqual(self.b.check(w, Want("expand", "htow", 3, 64, "mine")),
                         "开矿·剩下的矿都在对面那半边（兵力压倒对面才去）")

    def test_home_raided_recently_holds_the_expansion(self):
        w = world(army("hkni", 10, -4000, 2000), (), natural=False)
        self.b.home_militia_seen = w.t - 20
        self.assertEqual(self.b._expand_hold(w), "开矿·家里刚被打，先守家")
        self.b.home_militia_seen = w.t - RT.EXP_CALM_S - 1
        self.assertIsNone(self.b._expand_hold(w))

    def test_1414_enemy_side_mine_only_when_their_army_is_gone(self):
        """我方占优但没压倒（1.5 倍）：对面二矿位不开；对面主力打没了（09-17 那条）照旧去占。"""
        foes = army("ogru", 6, 3000, 2000, owner=1, base=0x900)
        ours = army("hkni", 8, -4000, 2000)
        w = world(ours, foes)
        self.assertGreater(w.own_strength, w.foe_strength)
        self.assertLess(w.own_strength, RT.EXP_FOE_SIDE_AHEAD * w.foe_strength, "前提：占优但没到压倒")
        self.assertIsNone(self.b._expansion_mine(w))
        self.assertGreaterEqual(self.b.counts.get("分矿候选排除·在对面那半边（兵力没压倒）", 0), 1)
        m = self.b._expansion_mine(world(ours, ()))
        self.assertEqual((int(m.x), int(m.y)), MINES[3])

    def test_our_half_mine_is_not_affected(self):
        w = world(army("hfoo", 2, -4000, 2000), army("ogru", 6, 3000, 2000, owner=1, base=0x900), natural=False)
        m = self.b._expansion_mine(w)
        self.assertEqual((int(m.x), int(m.y)), MINES[1], "自家二矿位不受「对面那半边」这条影响（兵力那道闸在 _expand_hold）")


if __name__ == "__main__":
    unittest.main()

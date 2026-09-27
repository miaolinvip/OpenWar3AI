# -*- coding: utf-8 -*-
"""守家时把我方的塔算进「打不打得过」（fightcall.judge 的 support + tactics.Defense 传进去）。

★★ 09-23 敌军拆分矿时我方不敢上前守（1-439）。
实测那一刻：我方 12 步兵 + 6 火枪 对 18 食尸鬼 + 3 蜘蛛，判「分矿守不住（胜率 21%），保兵回主基地」，
而分矿旁边**两座箭塔正在开火**（第 128/129 拍才被拆掉）——判据里一点没算它们。
算上塔：21% -> 27%，越过压上线（P_EVEN 24%）。这是漏项，不是把阈值调松。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import fightcall as FC  # noqa: E402
from test_tactics import U  # noqa: E402


def many(code, n, owner=0, x=0.0, y=0.0):
    return [U(1000 + i + hash(code) % 97, code, owner, x + 30 * i, y) for i in range(n)]


OURS = many("hfoo", 12) + many("hrif", 6)            # 1-439 那一刻的双方
THEIRS = many("ugho", 18, owner=1) + many("ucry", 3, owner=1)


class Support(unittest.TestCase):
    def test_1439_without_towers_we_give_up_the_mine(self):
        call = FC.judge(OURS, THEIRS, where="expansion")
        self.assertEqual(call.band, "hold")
        self.assertLess(call.p, FC.LINES["expansion"])
        self.assertEqual(call.support, 0.0)

    def test_one_tower_already_crosses_the_line(self):
        call = FC.judge(OURS, THEIRS, where="expansion", support=many("hgtw", 1))
        self.assertEqual(call.band, "commit", call.why)
        self.assertGreater(call.support, 0)
        self.assertIn("含我方塔", call.why)

    def test_two_towers_like_the_real_game(self):
        call = FC.judge(OURS, THEIRS, where="expansion", support=many("hgtw", 2))
        self.assertEqual(call.band, "commit")
        self.assertGreater(call.p, 0.26)

    def test_towers_do_not_rescue_a_hopeless_fight(self):
        call = FC.judge(many("hfoo", 4), THEIRS, where="expansion", support=many("hgtw", 2))
        self.assertEqual(call.band, "giveup", call.why)


class Wiring(unittest.TestCase):
    """★ 模块好用不等于接上了 ——驱动 Defense.step 本体，看同一场仗加了塔之后不再「保兵回主基地」。"""

    MAIN = U(1, "htow", 0, -5184, 2944)
    EXPO = U(3, "htow", 0, -4864, -3200)
    FOE_MAIN = U(900, "unpl", 1, 4672, 2944)

    def world(self, towers=0):
        from brain.world import build_world
        peas = [U(300 + i, "hpea", 0, -5000, 2800) for i in range(5)]
        # 主基地那边多几座建筑：否则分矿一放两座塔就成了「建筑最多的那座城」= 主基地，判据走的是另一条线
        town = [U(200 + i, "hhou", 0, -5300 + 150 * i, 3100) for i in range(4)] + [U(210, "hbar", 0, -5000, 3200)]
        ours = [U(10 + i, "hfoo", 0, -4800 + 40 * i, -3100) for i in range(12)] + \
               [U(40 + i, "hrif", 0, -4900 + 40 * i, -3150) for i in range(6)]
        foes = [U(600 + i, "ugho", 1, -4400, -3300 + 50 * i) for i in range(18)] + \
               [U(660 + i, "ucry", 1, -4350, -3000 + 50 * i) for i in range(3)]
        tw = [U(80 + i, "hgtw", 0, -4700 + 120 * i, -3050) for i in range(towers)]
        return build_world([self.MAIN, self.EXPO, self.FOE_MAIN] + peas + town + ours + foes + tw,
                           0, 900.0, 300, 300, 60, 90, levels={})

    def gave_up(self, out):
        return [i for i in out if "分矿守不住" in i.why]

    def test_without_towers_we_pull_back(self):
        from brain import tactics as T
        mode, out = T.Defense().step(self.world(0), None)
        self.assertEqual(mode, "toostrong")
        self.assertTrue(self.gave_up(out), [i.why for i in out])

    def test_two_towers_and_we_hold_the_mine(self):
        from brain import tactics as T
        d = T.Defense()
        mode, out = d.step(self.world(2), None)
        self.assertEqual(d.counts.get("回防·算上我方塔"), 1, dict(d.counts))
        self.assertFalse(self.gave_up(out), [i.why for i in out])
        self.assertEqual(d.counts.get("判仗·分矿·压上"), 1, dict(d.counts))


if __name__ == "__main__":
    unittest.main()

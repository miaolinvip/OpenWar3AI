# -*- coding: utf-8 -*-
"""空档时去拆对面别的矿（tactics._idle_plan 最后一步，raid_target 放宽距离）。

★★ 09-23 空档时去清剩下的野点，或攻打敌方其它矿点（1-438 对面 3 座矿，我方 14 个兵在自家门口驻守）。
野点那一步本来就有；这里补的是「对面自己那半边的矿」—— 原来 raid_target 只看离我家 RAID_HOME_R 7000 以内。
放宽的只是距离：当地守军 + 赶得到的援军 + 塔 那套算法一个字没改，打不过照旧不去。
⚠ 正规出击（choose_target）本来就会挑对面的分矿，这条只是它判否时的兜底，所以直接驱动 _idle_plan 来测。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import amai  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain import world as W  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from test_tactics import U  # noqa: E402

HOME = U(1, "htow", 0, -6000, 0)
FOE_MAIN = (7000, 7000)                 # 对面主基地和它的大部队都在另一头
EXPO = (1500, -4000)                    # 对面的另一座矿：离我家 8500 > RAID_HOME_R 7000


def world(foot=16, guards=0, t=900.0, expo=True):
    mine = [HOME, U(2, "Hamg", 0, -5800, 0, 500, 500)] + [U(10 + i, "hfoo", 0, -5700 + 50 * i, 0) for i in range(foot)]
    foes = [U(900, "unp2", 1, *FOE_MAIN)] + [U(940 + i, "ugho", 1, FOE_MAIN[0] - 200, FOE_MAIN[1] + 60 * i) for i in range(20)]
    if expo:
        foes.append(U(910, "unpl", 1, *EXPO))
        foes += [U(960 + i, "ugho", 1, EXPO[0] + 100, EXPO[1] + 60 * i) for i in range(guards)]
    return W.build_world(mine + foes, 0, t, 500, 300, 60, 100, levels={2: 3})


def seq():
    s = T.AttackSequence(dict(PROFILE))
    s.allow_attack = s.allow_creep = True
    s.home_xy = (HOME.x, HOME.y)
    return s


def army_of(w):
    return w.my_army + w.my_heroes


class Gate(unittest.TestCase):
    def test_the_old_radius_could_not_see_it(self):
        w = world()
        expo = next(h for h in w.enemy_halls if h.addr == 910)
        self.assertGreater(T.dist(expo, HOME), T.RAID_HOME_R, "前提：这座矿在旧口径之外")
        own = amai.strength(army_of(w), w.levels)
        self.assertIsNone(T.raid_target(w, HOME, own, army_of(w), T._Pt(HOME.x, HOME.y)))
        got = T.raid_target(w, HOME, own, army_of(w), T._Pt(HOME.x, HOME.y), max_home=T.IDLE_RAID_MAX_HOME)
        self.assertIsNotNone(got)
        self.assertEqual(got[0].addr, 910)

    def test_a_guarded_mine_is_still_not_worth_it(self):
        w = world(guards=8)
        own = amai.strength(army_of(w), w.levels)
        self.assertIsNone(T.raid_target(w, HOME, own, army_of(w), T._Pt(HOME.x, HOME.y), max_home=T.IDLE_RAID_MAX_HOME),
                          "守军够多就不去（放宽的只是距离，不是判据）")


class Idle(unittest.TestCase):
    def plan(self, w, s=None):
        s = s or seq()
        army = army_of(w)
        return s, s._idle_plan(w, army, T.main_force(army, w.me, w.levels))

    def test_idle_army_goes_for_the_other_mine(self):
        s, it = self.plan(world())
        self.assertEqual(s.counts.get("空闲·去拆对面的分矿"), 1, s.counts)
        self.assertEqual((s.state, s.target.addr, s.target_kind), ("attack", 910, "骚扰分矿"))
        self.assertTrue(it and it[0].units)

    def test_guarded_mine_falls_back_to_holding_the_front(self):
        # 10 个守军才挡得住：主力从自己的位置（离目标比大厅近）出发时，能赶到的援军按到达时间折算后更少（实算 8 个还过线）
        s, _ = self.plan(world(guards=10))
        self.assertFalse(s.counts.get("空闲·去拆对面的分矿"))
        self.assertEqual(s.counts.get("空闲·前沿驻守"), 1, s.counts)

    def test_not_right_after_a_retreat(self):
        w = world()
        s = seq()
        s.retreat_at = w.t - 1.0                     # 刚撤回来
        s, _ = self.plan(w, s)
        self.assertFalse(s.counts.get("空闲·去拆对面的分矿"), "撤退冷却期内不许又冲出去")
        self.assertEqual(s.counts.get("空闲·前沿驻守"), 1, s.counts)

    def test_no_enemy_expansion_holds_the_front(self):
        s, _ = self.plan(world(expo=False))
        self.assertEqual(s.counts.get("空闲·前沿驻守"), 1, s.counts)


if __name__ == "__main__":
    unittest.main()

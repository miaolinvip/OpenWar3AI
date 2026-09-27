# -*- coding: utf-8 -*-
"""挑营地只算主力附近的人、等不到人就换营地（09-18 晚复盘 s10 370 局：每局平均 18 拍只在集合点等人，
77% 是连续 30 游戏秒以上的长等；先到的退回集合点时到场战力比中位 0.6 —— 挑营地按全军算，连还在家里的新兵也算进去）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def base():
    return ([U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
            + [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)])


GROUP = lambda: [U(2, "Hamg", 3000, 0)] + [U(10 + i, "hfoo", 3000, 80 * i) for i in range(6)]    # 主力（强度 4 + 12 = 16）
HOMEBODIES = lambda n: [U(40 + i, "hfoo", 0, 300 + 60 * i) for i in range(n)]     # 刚出兵营、还在家（离主力 3000）
BIG = lambda: [U(700 + i, "nogm", 4200, 60 * i, owner=12) for i in range(3)]       # 3 只食人魔
SMALL = lambda: [U(710, "nwlt", 3600, 900, owner=12)]                              # 1 只狼


class GroupPick(unittest.TestCase):
    def setUp(self):
        # 09-23 这组测「按小队挑营地」；场景会触发「对面一矿暴兵不出远门」（test_hold_window）
        p = mock.patch.object(T, "hold_window", lambda w: False)
        p.start()
        self.addCleanup(p.stop)

    def test_ratios_of_scene(self):
        # 先把这个场景的战力比算出来（期望是算的不是猜的）：主力这一小队打不了大营地，全军（带上家里 6 个）打得了
        w = build_world(base() + GROUP() + HOMEBODIES(5) + BIG(), 0, 600.0, 300, 300, 20, 40, levels={2: 2})
        big = [c for c in w.creeps]
        grp = [u for u in w.my_army + w.my_heroes if u.x > 2000]
        self.assertLess(T.power_ratio(grp, big, w.levels), T.CREEP_POWER_MIN)
        self.assertGreaterEqual(T.power_ratio(w.my_army + w.my_heroes, big, w.levels), T.CREEP_POWER_MIN)

    def test_pick_fits_the_group_not_the_whole_army(self):
        w = build_world(base() + GROUP() + HOMEBODIES(5) + BIG() + SMALL(), 0, 600.0, 300, 300, 20, 40, levels={2: 2})
        s = T.AttackSequence(dict(PROFILE))
        s.step(w, w.my_halls[0])
        self.assertEqual(s.camp_ids, {710}, s.notes)                               # 小队打得动的那只狼，不是等家里的兵来打食人魔


class Repick(unittest.TestCase):
    def creeping(self, w, waited):
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since = "creep", {700, 701, 702}, w.t - waited
        s.camp = dict(x=4200.0, y=60.0, strength=15.0, units=[])
        return s

    def test_give_up_after_20s_when_nobody_else_is_coming(self):
        w = build_world(base() + GROUP() + BIG(), 0, 600.0, 300, 300, 20, 40, levels={2: 2})
        s = self.creeping(w, 25.0)
        s.step(w, w.my_halls[0])
        self.assertEqual(s.counts.get("打野·等不到人换营地"), 1)
        self.assertEqual(s.state, "retreat")
        self.assertTrue(s.creep_withdraw)  # 旧营地仍被跟踪，不能丢下接战单位就重选。

    def test_keep_waiting_when_help_is_close(self):
        near_help = [U(40 + i, "hfoo", 2700, 300 + 60 * i) for i in range(6)]    # 离营地 1500~2500：马上到
        w = build_world(base() + GROUP() + near_help + BIG(), 0, 600.0, 300, 300, 20, 40, levels={2: 2})
        s = self.creeping(w, 25.0)
        s.step(w, w.my_halls[0])
        self.assertIsNone(s.counts.get("打野·等不到人换营地"))
        self.assertEqual(s.camp_ids, {700, 701, 702})


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""野外就地治疗（runtime._field_heal_spot）+ 收尾拆家不等治疗（runtime._finishing / tactics finishing）。

09-22 晚 后期出门时让背包有空格的单位带上恢复卷轴，残血单位不必回家等治疗，退到安全区域就地治疗；
最后阶段敌军基本没有单位、只剩拆家时不再等治疗，直接上去快速拆完。
1-403 / 1-407 拆家那几分钟对面强度 0~7、我方 45~54。
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def world(hero_x=6000.0, hero_frac=1.0, foes=(), knights=10, enemy_base=True):
    hero = U(3, "Hamg", hero_x, 0)
    hero.hp = hero.hp_max * hero_frac
    units = [U(1, "hcas", 0, 0), U(2, "hbar", 500, 0), hero]
    units += [U(10 + i, "hkni", hero_x - 200, 80 * i) for i in range(knights)]
    if enemy_base:
        units += [U(900, "unpl", 12000, 0, owner=1)]
    return build_world(units + list(foes), 0, 1500.0, 500, 300, 60, 90)


def brain(d, bag=("sreg", None, None, None, None, None)):
    b = Brain(FakeCmd(), None, d)
    b.set_race("human")
    b.recovery.bag = lambda cmd, u, t: list(bag) if bag is not None else None
    return b


class FieldHeal(unittest.TestCase):
    def test_hero_with_a_scroll_out_in_the_field_is_the_meeting_point(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world()
            x, y, h = b._field_heal_spot(w, w.my_halls[0])
            self.assertEqual(h.type, "Hamg")
            self.assertAlmostEqual(math.hypot(x - 6000, y), RT.FIELD_HEAL_BACK, delta=1)
            self.assertLess(x, 6000, "往家的方向退")

    def test_not_when_unsafe_near_home_or_without_a_scroll(self):
        with tempfile.TemporaryDirectory() as d:
            ghouls = [U(800 + i, "ugho", 6500, 60 * i, owner=1) for i in range(3)]
            w = world(foes=ghouls)
            self.assertIsNone(brain(d)._field_heal_spot(w, w.my_halls[0]), "英雄身边有敌人")
            w = world(hero_x=1500.0)
            self.assertIsNone(brain(d)._field_heal_spot(w, w.my_halls[0]), "离家近：回家附近空地一样")
            w = world()
            self.assertIsNone(brain(d, bag=("phea", None, None, None, None, None))._field_heal_spot(w, w.my_halls[0]))


class Finishing(unittest.TestCase):
    def test_enemy_army_gone_means_finish_them(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            self.assertTrue(b._finishing(world()), "对面一个兵都没有、建筑还在")
            ghouls = [U(800 + i, "ugho", 11000, 60 * i, owner=1) for i in range(12)]
            self.assertFalse(b._finishing(world(foes=ghouls)), "对面还有一支队伍")
            self.assertFalse(b._finishing(world(enemy_base=False)), "看不见对面建筑：不算拆家")
            self.assertFalse(b._finishing(world(knights=1)), "我们自己也没多少兵")

    def test_heroes_do_not_wait_to_heal_when_finishing(self):
        # 55%：高于英雄保命脱离线、低于回血线 HERO_READY_HP（七成）—— 平时「健康主力先走、英雄单独恢复」
        s = T.AttackSequence(dict(PROFILE))
        s.finishing = True
        w = world(hero_x=200.0, hero_frac=0.55)
        s.step(w, w.my_halls[0])
        self.assertEqual(s.counts.get("收尾拆家·英雄不等回血"), 1)
        self.assertIsNone(s.counts.get("出击·健康主力先走·英雄单独恢复"))
        s2 = T.AttackSequence(dict(PROFILE))
        w = world(hero_x=200.0, hero_frac=0.55)
        s2.step(w, w.my_halls[0])
        self.assertEqual(s2.counts.get("出击·健康主力先走·英雄单独恢复"), 1)


if __name__ == "__main__":
    unittest.main()

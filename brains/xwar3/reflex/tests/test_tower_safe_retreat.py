# -*- coding: utf-8 -*-
"""撤退点避开塔射程（mod_combat.tower_safe_point）；英雄补攻击令不打塔下的目标（pick_refill 的 towers）。

09-22（1-333 22 分）大法师再次被敌方防御塔射杀，同类问题反复出现。
那局大法师两次都死在兽族地洞射程里。9 分那次是本模块把它拉进去的：12:35:07「英雄被集火撤：拉到 (3896,2645)」——
背离打它的兽族步兵，那个方向就是 (4128,2720) 的地洞（落点离它 256），撤退点只看会动的敌人。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, owner, x, y, hp=500, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp,
                hp_max=hp_max if hp_max is not None else hp, order=0, order_known=True)


def reach(t):
    return C.RANGE.get(t.type, 800.0) + C.TOWER_SAFE_PAD


# 1-333 那局对方基地的四个地洞（帧里的坐标）
BURROWS = [unit(100 + i, "otrb", 1, x, y, hp=1100) for i, (x, y) in
           enumerate(((4128, 2720), (5216, 3040), (4576, 3488), (5024, 2208)))]


class TowerSafePoint(unittest.TestCase):
    def test_precondition_burrow_shoots(self):
        self.assertGreater(C.DPS.get("otrb", 0.0), 0, "地洞在伤害表里（否则 enemy_towers 不认它，下面全是空测）")
        self.assertEqual({t.addr for t in C.enemy_towers(BURROWS, 0)}, {b.addr for b in BURROWS})

    def test_1_333_escape_into_the_burrow_is_redirected_to_our_side(self):
        hero = unit(1, "Hamg", 0, 3266, 2746, hp=476, hp_max=725)
        x, y, fixed = C.tower_safe_point(hero, 3896, 2645, BURROWS)
        self.assertTrue(fixed)
        for b in BURROWS:
            self.assertGreater(math.hypot(x - b.x, y - b.y), reach(b), b.addr)
        self.assertLess(x, 4128 - reach(BURROWS[0]) + 1, "退回英雄这一侧，没有绕到地洞后面")

    def test_path_through_a_tower_bends_around_it(self):
        tower = unit(100, "owtw", 1, 1000, 0)
        u = unit(1, "hfoo", 0, 0, 0)
        x, y, fixed = C.tower_safe_point(u, 2000, 0, [tower])          # 目的地在塔后面 1000，直线正穿过去
        self.assertTrue(fixed)
        self.assertGreater(C._seg_dist(tower.x, tower.y, 0, 0, x, y), reach(tower))
        self.assertAlmostEqual(math.hypot(x, y), 2000, delta=1, msg="走的距离不变，只换方向")

    def test_clean_retreat_is_untouched(self):
        u = unit(1, "Hamg", 0, 3729, -2393)
        tower = unit(100, "otrb", 1, 4320, -2336)                      # 1-333 22 分：它已经在射程里，往西北撤是出射程
        self.assertEqual(C.tower_safe_point(u, 3035, -1997, [tower]), (3035, -1997, False))
        self.assertEqual(C.tower_safe_point(u, 3035, -1997, []), (3035, -1997, False))


class HeroRefillAvoidsTowers(unittest.TestCase):
    def test_hero_takes_the_target_outside_the_tower_footman_may_take_either(self):
        tower = unit(100, "otrb", 1, 4000, 0, hp=1100)
        under = unit(200, "Ofar", 1, 3600, 0, hp=200, hp_max=800)     # 残血英雄，在地洞射程里
        outside = unit(201, "ogru", 1, 2600, 300, hp=600, hp_max=700)
        hero = unit(1, "Hamg", 0, 2800, 0)
        self.assertTrue(hero.is_hero)
        got = {u.addr: t.addr for u, t, _ in C.pick_refill([hero], [under, outside], {}, now=0.0, towers=[tower])}
        self.assertEqual(got, {1: 201})
        # 没有塔时照旧会去打那个残血英雄 —— 否则上面这条测不出东西
        got = {u.addr: t.addr for u, t, _ in C.pick_refill([hero], [under, outside], {}, now=0.0)}
        self.assertEqual(got, {1: 200})

    def test_hero_with_only_targets_under_the_tower_is_not_sent(self):
        tower = unit(100, "otrb", 1, 4000, 0, hp=1100)
        under = unit(200, "Ofar", 1, 3600, 0, hp=200, hp_max=800)
        hero = unit(1, "Hamg", 0, 2800, 0)
        foot = unit(2, "hfoo", 0, 2800, 100)
        got = {u.addr: t.addr for u, t, _ in C.pick_refill([hero, foot], [under], {}, now=0.0, towers=[tower])}
        self.assertEqual(got, {2: 200}, "英雄这一轮不补；步兵打不打塔下的归大脑的「先打兵」管")


if __name__ == "__main__":
    unittest.main()

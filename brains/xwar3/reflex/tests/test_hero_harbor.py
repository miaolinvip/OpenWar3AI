# -*- coding: utf-8 -*-
"""英雄接触前预警撤退往自己的大部队撤（mod_combat.friendly_harbor）。

09-22（1-298 24 分）大法师在地图最北边的水域长时间停留不动，主力已经在南边开打。
01:10:55「英雄预警撤退：Hamg 725/725 身边 3000 内敌 6 个、我方 2 个」—— 它落在主力后面、身后没自己人，
focus_escape_point 直直背离敌人退 1600，退到了地图最北边的水边；主力在西边回防。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402


def u(addr, t, x, y, hero=False):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=100.0, hp_max=100.0, is_hero=hero)


HERO = u(1, "Hamg", 0, 0, hero=True)
THREATS = [u(100 + i, "ogru", 300 + 40 * i, -1200) for i in range(6)]      # 东南边


def army(x, y, n=8):
    return [u(10 + i, "hfoo", x + 50 * i, y) for i in range(n)]


class Harbor(unittest.TestCase):
    def test_1298_goes_to_the_army_in_the_west_not_away_into_the_north(self):
        p = C.friendly_harbor(HERO, THREATS, [HERO] + army(-3000, 0))
        self.assertIsNotNone(p)
        self.assertLess(p[0], -2500)
        # 对照：原来的背离敌人往北（y 变大）走
        ox, oy = C.focus_escape_point(HERO, THREATS, [], (0.0, 5000.0), back=C.PULL_BACK * 2)
        self.assertGreater(oy, 1000)

    def test_route_through_the_enemies_is_not_taken(self):
        self.assertIsNone(C.friendly_harbor(HERO, THREATS, [HERO] + army(600, -2400)))

    def test_army_already_in_contact_is_not_a_harbor(self):
        self.assertIsNone(C.friendly_harbor(HERO, THREATS, [HERO] + army(1200, -1000)))

    def test_a_couple_of_units_is_not_an_army(self):
        self.assertIsNone(C.friendly_harbor(HERO, THREATS, [HERO] + army(-3000, 0, n=3)))


if __name__ == "__main__":
    unittest.main()

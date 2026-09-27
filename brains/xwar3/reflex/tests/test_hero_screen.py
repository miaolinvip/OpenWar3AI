# -*- coding: utf-8 -*-
"""英雄落在自己部队后面、部队正在和敌人交火 —— 不算孤军，不做接触前预警撤退（mod_combat precontact_hero_retreat HERO_SCREEN_*）。

09-22（1-394 23:30）部队已经开战，英雄仍停在地图中间，没有上前支援。
21:12:14 两个满血英雄被外圈预警拉回 1000（「3000 内敌 6~7 个、我方 2~3 个」），那批敌人正和我方 13 个兵在 2700 外交火。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


class HeroScreen(unittest.TestCase):
    def test_1394_heroes_behind_their_fighting_army_are_not_pulled(self):
        am = unit(1, "Hamg", 419, -879, 825, 825)
        army = [unit(10 + i, "hkni", 2250 + 40 * i, -1850 + 20 * (i % 3), 835, 835) for i in range(13)]
        foes = [unit(100 + i, "ogru", 2800 + 60 * i, -2100, 700, 700, 1) for i in range(7)]
        self.assertIsNone(C.precontact_hero_retreat(am, foes, [am] + army))

    def test_alone_in_midfield_still_pulled(self):
        """1-215：两英雄在中场、自己的兵都在 4200 外，敌方六人集团进了 3000 —— 照旧拉。"""
        am = unit(1, "Hamg", 0, 0, 825, 825)
        army = [unit(10 + i, "hkni", -4200, 40 * i, 835, 835) for i in range(8)]
        foes = [unit(100 + i, "ogru", 2200 + 60 * i, 0, 700, 700, 1) for i in range(6)]
        self.assertIsNotNone(C.precontact_hero_retreat(am, foes, [am] + army))

    def test_a_couple_of_units_is_not_a_screen(self):
        am = unit(1, "Hamg", 0, 0, 825, 825)
        army = [unit(10 + i, "hfoo", 2300, 40 * i, 420, 420) for i in range(2)]
        foes = [unit(100 + i, "otau", 2700 + 60 * i, 0, 1300, 1300, 1) for i in range(6)]
        self.assertIsNotNone(C.precontact_hero_retreat(am, foes, [am] + army))


if __name__ == "__main__":
    unittest.main()

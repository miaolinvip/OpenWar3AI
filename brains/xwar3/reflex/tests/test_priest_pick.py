# -*- coding: utf-8 -*-
"""牧师定向治疗（mod_combat.priest_pick）。09-21 牧师优先给主力英雄回血，不随意分散治疗，先保战斗力最强的单位。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def u(addr, typ, x, hp, hp_max):
    return Unit(index=addr, addr=addr, type=typ, owner=0, x=float(x), y=0.0, hp=hp, hp_max=hp_max,
                order=0, order_known=True)


PRIEST = u(1, "hmpr", 0, 290, 290)


class PriestPick(unittest.TestCase):
    def test_hurt_hero_first(self):
        hero = u(2, "Hamg", 300, 420, 700)                   # 缺 40%
        knight = u(3, "hkni", 200, 250, 835)                 # 缺 70%
        foot = u(4, "hfoo", 100, 84, 420)                    # 缺 80%
        self.assertEqual(C.priest_pick(PRIEST, [PRIEST, hero, knight, foot]).addr, 2)

    def test_stronger_unit_before_weaker_one(self):
        knight = u(3, "hkni", 200, 420, 835)                 # 缺 50%
        foot = u(4, "hfoo", 100, 210, 420)                   # 缺 50%
        self.assertGreater(C._unit_dps("hkni"), C._unit_dps("hfoo"))   # 前提：骑士输出更高
        self.assertEqual(C.priest_pick(PRIEST, [PRIEST, knight, foot]).addr, 3)

    def test_skips_summons_machines_far_and_scratches(self):
        water = u(5, "hwt2", 100, 100, 675)
        gyro = u(6, "hgyr", 100, 50, 400)
        far = u(7, "hfoo", C.PRIEST_R + 50, 50, 420)
        scratch = u(8, "hkni", 100, 835 - 20, 835)
        self.assertIsNone(C.priest_pick(PRIEST, [PRIEST, water, gyro, far, scratch]))


if __name__ == "__main__":
    unittest.main()

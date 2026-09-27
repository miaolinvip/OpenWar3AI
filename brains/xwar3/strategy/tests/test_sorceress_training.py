# -*- coding: utf-8 -*-
"""女巫训练进愿望表（tables/human.py）+ 研究建筑（races.py researchers "Rhst": "hars"）。

09-22 有技能的高级兵要对敌军高伤害单位放技能。变形术要女巫训练两级（最大蓝 200 -> 400），
原来愿望表里没有 Rhst、研究表里也没有法师圣所 —— 女巫一辈子只会自动减速（毫秒层 war3-modules/unitspells.py）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import races  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(hall, n_sor):
    units = [U(0x100, hall, 0, 0), U(0x101, "hars", 600, 0), U(0x200, "ngol", 0, 700, owner=15)]
    units += [U(0x300 + i, "hpea", 100 + i * 30, 100) for i in range(8)]
    units += [U(0x400 + i, "hsor", 300, 300 + 30 * i) for i in range(n_sor)]
    return build_world(units, 0, 600.0, 1000, 800, 30, 60)


def rhst(w):
    return [x for x in human.wants(w, human.PROFILE) if x.code == "Rhst"]


class SorceressTraining(unittest.TestCase):
    def test_keep_wants_adept_castle_wants_master(self):
        self.assertEqual([x.qty for x in rhst(world("hkee", 2))], [1])
        self.assertEqual([x.qty for x in rhst(world("hcas", 3))], [2])

    def test_not_without_sorceresses_or_before_keep(self):
        self.assertEqual(rhst(world("hcas", 1)), [])
        self.assertEqual(rhst(world("htow", 4)), [])

    def test_arcane_sanctum_researches_it(self):
        self.assertEqual(races.SPECS["human"]["researchers"].get("Rhst"), "hars")


if __name__ == "__main__":
    unittest.main()

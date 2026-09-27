# -*- coding: utf-8 -*-
"""补兵填人口（tables/human.py 末尾那段，照 AMAI DynamicBuildUnit(40)）。

09-23 1-420 对 AMAI 人族被一边倒压制，复盘对比双方的出兵。
1-420 前 12 分钟我方下单 8105 金，兵只占 28%（13 个兵）；对面一座矿、17 个农民，12 分钟 23 个兵，一波把我方 11 个兵打光。
差别在 AMAI 表外的 DynamicBuildUnit：表里的兵出齐以后按「人口上限 - 已用人口」一直补兵。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(hall="hkee", foot=5, rifle=4, knights=0, used=37, cap=60, foes=(), t=600.0):
    units = [U(1, hall, -5184, 2944), U(2, "hbar", -4700, 2944), U(3, "halt", -5500, 2944), U(4, "hbla", -4800, 2600),
             U(5, "hlum", -5500, 3300)]
    units += [U(100 + i, "hfoo", -4400, 2700 + 60 * i) for i in range(foot)]
    units += [U(120 + i, "hrif", -4500, 2700 + 60 * i) for i in range(rifle)]
    units += [U(140 + i, "hkni", -4600, 2700 + 60 * i) for i in range(knights)]
    units += [U(0x800, "hkee", 4672, 2944, owner=1)] + list(foes)
    return build_world(units, 0, t, 400, 300, used, cap)


def fills(w):
    return [x for x in human.wants(w, human.PROFILE) if x.kind == "unit" and "补兵填人口" in x.note]


AMAI_ARMY = [U(0x900 + i, "hfoo", 3000, 60 * i, owner=1) for i in range(11)] + \
            [U(0x920 + i, "hrif", 3100, 60 * i, owner=1) for i in range(4)]


class Fill(unittest.TestCase):
    def test_1420_table_done_keeps_building_army(self):
        w = world()
        f = fills(w)
        self.assertEqual(len(f), 1, "表里的 5 步兵 4 火枪出齐了：接着补")
        self.assertEqual((f[0].code, f[0].qty, f[0].prio), ("hrif", 6, human.FILL_PRIO), "二本 1:1：火枪 4 < 步兵 5 -> 火枪再要 2 个")

    def test_1420_outnumbered_puts_army_before_castle_and_second_hero(self):
        w = world(foes=AMAI_ARMY)
        self.assertGreater(w.foe_strength, human.FILL_BEHIND * w.own_strength)
        f = fills(w)
        self.assertEqual(f[0].prio, human.FILL_PRIO_BEHIND)
        castle = max(x.prio for x in human.wants(w, human.PROFILE) if x.code == "hcas")
        hero2 = max(x.prio for x in human.wants(w, human.PROFILE) if x.kind == "hero" and x.code == human.PROFILE["heroes"][1])
        self.assertGreater(f[0].prio, max(castle, hero2))

    def test_stops_at_the_food_soft_cap(self):
        self.assertEqual(fills(world(used=G.FOOD_SOFT_CAP - 1, cap=90)), [], "80 以内只剩 1：步兵要 2 人口，不补")

    def test_tier_mix(self):
        self.assertEqual(fills(world(hall="htow", foot=7, rifle=3))[0].code, "hfoo", "一本：步兵 < 2 × 火枪 + 2")
        self.assertEqual(fills(world(hall="htow", foot=8, rifle=3))[0].code, "hrif")
        castle = world(hall="hcas", foot=5, rifle=4, knights=2)
        self.assertEqual(fills(castle)[0].code, "hkni", "三本只补骑士")
        wyverns = [U(0x950 + i, "owyv", 3000, 100 * i, owner=1) for i in range(3)]
        self.assertEqual(fills(world(hall="hcas", knights=2, foes=wyverns))[0].code, "hgry")


if __name__ == "__main__":
    unittest.main()

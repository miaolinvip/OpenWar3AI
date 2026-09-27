# -*- coding: utf-8 -*-
"""分矿两座能打的塔、朝来路（runtime.expansion_tower_sector + tables/human.py EXP_TOWERS / 哨塔升防御塔）。

09-22 分矿从没在合适的位置造过防御塔：要求至少两座能攻击的塔，而原来唯一一座塔还盖在分矿背后，起不到保护作用。
最近 16 局每局分矿 1 座哨塔、从没升级（哨塔不能攻击）；1-399 分矿 (-4352,-2624)、金矿在西南，塔盖在正南 (-4352,-3104)，敌人从东北来。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT  # noqa: E402
from brain.tables import human  # noqa: E402
from test_infi_doctrine import expansion_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def P(x, y):
    return SimpleNamespace(x=float(x), y=float(y))


def angle_to(hall, p):
    return math.degrees(math.atan2(p[1] - hall.y, p[0] - hall.x))


class Sector(unittest.TestCase):
    def test_1399_mine_behind_the_hall_towers_face_the_approach(self):
        hall, mine = P(-4352, -2624), P(-4864, -3200)
        first = RT.expansion_tower_sector(hall, mine, RT.TOWER_FRONT_DEFAULT)[0]
        self.assertGreater(first[0], hall.x)                  # 朝东北（地图中心 / 来路）
        self.assertGreater(first[1], hall.y)
        self.assertLess(abs(angle_to(hall, first) - 31.1), 5)

    def test_mine_to_the_side_towers_sit_between_mine_and_approach(self):
        hall, mine = P(0, 0), P(0, 700)                       # 金矿正北，敌人正东：两个方向夹角 90°
        first = RT.expansion_tower_sector(hall, mine, (8000.0, 0.0))[0]
        self.assertLess(abs(angle_to(hall, first) - 45.0), 3, first)
        self.assertLess(math.hypot(first[0] - mine.x, first[1] - mine.y), 700, "罩得住金矿（防御塔射程 700）")


class TableWants(unittest.TestCase):
    def test_two_towers_per_expansion_and_every_scout_tower_upgrades(self):
        w = expansion_world([U(0x400, "hwtw", 4800, 500), U(0x401, "hlum", 300, 300)])
        wants = human.wants(w, human.PROFILE)
        town = [x for x in wants if x.code == "hwtw" and x.where.startswith("town:") and "分矿" in x.note]   # 主基地门口那条见 test_front_towers
        self.assertEqual([x.qty for x in town], [2])
        morph = [x for x in wants if x.kind == "morph" and x.code == "hgtw"]
        self.assertEqual([x.qty for x in morph], [1], "现有 1 座哨塔 -> 要 1 座防御塔")

    def test_no_upgrade_without_lumber_mill(self):
        w = expansion_world([U(0x400, "hwtw", 4800, 500)])
        # 防御塔要伐木场；奥术塔只要哨塔（AMAI StandardUnits ARCANE_TOWER，09-23 门口那座，test_front_towers）
        self.assertFalse([x for x in human.wants(w, human.PROFILE) if x.kind == "morph" and x.code == "hgtw"])


if __name__ == "__main__":
    unittest.main()

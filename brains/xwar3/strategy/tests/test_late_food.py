# -*- coding: utf-8 -*-
"""后期人口放到 100（runtime.food_soft_cap / world.farm_cap）+ 后期多要兵、按看得见的敌情补兵（tables/human.py 三本那段）。

09-22 晚 1-403 拆家阶段资源充足（金 4000 多）却不多造拆家单位；后期人口上限直接放到 100，
交战时家里也要按战况继续补相应的单位。
1-403 最后 3 分钟人口卡 80/84、兵 15~17、金 1900 -> 4254、木 6045。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G  # noqa: E402
from brain import runtime as RT  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def world(hall="hcas", gold=3000, used=80, cap=84, foes=(), army=0, enemy_base=False, t=1200.0):
    units = [U(1, hall, 0, 0), U(2, "hbar", 400, 0), U(3, "halt", -400, 0), U(4, "hgra", 0, 400), U(5, "hars", 0, -400)]
    units += [U(100 + i, "hhou", 600 + 100 * i, 600) for i in range(10)]
    units += [U(200 + i, "hkni", 300, 200 + 60 * i) for i in range(army)]
    if enemy_base:
        units.append(U(900, "unpl", 12000, 0, owner=1))
    return build_world(units + list(foes), 0, t, gold, 3000, used, cap)


GHOULS = [U(800 + i, "ugho", 11000, 60 * i, owner=1) for i in range(12)]


class LateCap(unittest.TestCase):
    """09-22 晚 1-411 起调整规则：只在最后大优势（收尾拆家）时突破 80，其它时间守在 80 以内（81~100 收入 -60%）。"""

    def test_1403_finishing_opens_to_100_then_closes_after_the_hold(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w = world(gold=1500, army=10, enemy_base=True)                # 对面一个兵都没了、建筑还在（存款不到 2000，只看收尾）
            self.assertEqual(b.soft_food_free(w), 4, "80/84：放开到 100 后还能用 84 - 80")
            later = world(gold=1500, army=10, enemy_base=True, foes=GHOULS, t=1200.0 + RT.LATE_FOOD_HOLD_S - 5)
            self.assertEqual(b.food_soft_cap(later), 100, "收尾刚结束：再留一会儿，别在门槛上抖")
            much_later = world(gold=1500, army=10, enemy_base=True, foes=GHOULS, t=1200.0 + RT.LATE_FOOD_HOLD_S + 5)
            self.assertEqual(b.food_soft_cap(much_later), G.FOOD_SOFT_CAP)

    def test_castle_with_ordinary_money_stays_under_80(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            self.assertEqual(b.food_soft_cap(world(gold=RT.LATE_FOOD_BANK - 1, army=10, enemy_base=True, foes=GHOULS)),
                             G.FOOD_SOFT_CAP, "对面还有一支队伍、钱不到 2000：守在 80")

    def test_1418_money_piling_up_opens_to_100(self):
        """09-23 1-416 / 1-418：人口卡 80、金子涨到 4000~6000 花不出去。AMAI Upkeep.txt HIGH：存款 > 2000 就允许越过 80。"""
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            self.assertEqual(b.food_soft_cap(world(gold=RT.LATE_FOOD_BANK, army=10, enemy_base=True, foes=GHOULS)), 100)
            self.assertEqual(b.counts.get("存款过 2000·人口放到 100"), 1)

    def test_farms_go_to_100_when_late(self):
        w = world()
        self.assertFalse([x for x in human.wants(w, human.PROFILE) if x.code == "hhou"], "平时 84 >= 80 不再盖房子")
        w.late_food, w.farm_cap = True, G.FOOD_FARM_CAP_LATE
        self.assertTrue([x for x in human.wants(w, human.PROFILE) if x.code == "hhou"])


class LateWants(unittest.TestCase):
    def qty(self, w, code):
        return max([x.qty for x in human.wants(w, human.PROFILE) if x.kind == "unit" and x.code == code] or [0])

    def test_more_knights_and_gryphons_when_late(self):
        w = world()
        self.assertEqual((self.qty(w, "hkni"), self.qty(w, "hgry")), (6, 7))
        w.late_food = True
        self.assertEqual((self.qty(w, "hkni"), self.qty(w, "hgry")), (10, 10))

    def test_counters_follow_what_the_enemy_fields(self):
        wyverns = [U(900 + i, "owyv", 3000, 100 * i, owner=1) for i in range(4)]
        shamans = [U(950 + i, "oshm", 3200, 100 * i, owner=1) for i in range(3)]
        w = world(foes=wyverns + shamans)
        self.assertEqual(self.qty(w, "hdhw"), 4, "对面 4 只飞龙：多出龙鹰锁它")
        self.assertEqual(self.qty(w, "hspt"), 4, "对面 3 个萨满：多出破法者")
        self.assertEqual((self.qty(world(), "hdhw"), self.qty(world(), "hspt")), (2, 2))


if __name__ == "__main__":
    unittest.main()

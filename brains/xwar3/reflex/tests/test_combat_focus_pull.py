# -*- coding: utf-8 -*-
"""英雄被敌军集火就立刻拉（mod_combat.focus_count / focus_escape_point）。

09-17 v287 四个实例 61 次英雄阵亡：死前 3 秒血量中位 67%、从 >=60% 血到死中位 3.2 秒，
死前 2 秒典型「13/16 个敌人的攻击目标是它」—— 按掉血速度判断必然晚。见 mod_combat FOCUS_R 上面那段。
期望值都是按函数里的几何手算的，不是照代码现状写的。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1


def unit(addr, typ, x, y, owner=ME, hl=0, hh=0, hp=400, hp_max=675):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp_max, order=0, order_known=True, handle_lo=hl, handle_hi=hh)


HERO = unit(0x100, "Hamg", 0, 0, hl=100, hh=200)
HOME = (0.0, -5000.0)


class FocusCount(unittest.TestCase):
    def test_counts_only_this_hero(self):
        rows = [(1, (100, 200)), (2, (100, 200)), (3, None), (4, (5, 6)), (5, (100, 201))]
        self.assertEqual(C.focus_count(HERO, rows), 2)

    def test_threshold_is_three(self):
        self.assertEqual(C.FOCUS_PULL_N, 3)

    def test_three_attackers_pull_even_full_health_hero(self):
        """#94 日志曾出现“3~5 个在打英雄、拉 0”：健康血量不能把阈值偷偷抬到 5。"""
        self.assertTrue(C.hero_focus_danger(3))
        self.assertFalse(C.hero_focus_danger(2))


class EscapePoint(unittest.TestCase):
    attackers = [unit(1, "hfoo", 500, 0, FOE), unit(2, "hrif", 600, 100, FOE), unit(3, "hrif", 400, -100, FOE)]

    def test_back_behind_own_army(self):
        # 打它的人重心 (500,0) -> 往 (-1,0) 退；身后自己人 (-600,50)(-800,-50) 重心 (-700,0)，再往后 300 = (-1000,0)
        allies = [unit(10, "hfoo", -600, 50), unit(11, "hfoo", -800, -50), unit(12, "hfoo", 300, 0)]
        self.assertEqual(C.focus_escape_point(HERO, self.attackers, allies, HOME), (-1000.0, 0.0))

    def test_no_army_behind_straight_back(self):
        allies = [unit(12, "hfoo", 300, 0)]                                   # 在前面，不算
        self.assertEqual(C.focus_escape_point(HERO, self.attackers, allies, HOME), (-900.0, 0.0))

    def test_army_too_close_straight_back(self):
        # 身后只有 (-100,0)：(-100,0)+(-300,0) = (-400,0)，离英雄 400 < 900*0.5 -> 直线退 900
        allies = [unit(10, "hfoo", -100, 0)]
        self.assertEqual(C.focus_escape_point(HERO, self.attackers, allies, HOME), (-900.0, 0.0))

    def test_army_out_of_radius_ignored(self):
        allies = [unit(10, "hfoo", -2000, 0)]                                 # 超过 FOCUS_BEHIND 1500
        self.assertEqual(C.focus_escape_point(HERO, self.attackers, allies, HOME), (-900.0, 0.0))

    def test_surrounded_goes_home_direction(self):
        ring = [unit(1, "hfoo", 100, 0, FOE), unit(2, "hfoo", -100, 0, FOE),
                unit(3, "hfoo", 0, 100, FOE), unit(4, "hfoo", 0, -100, FOE)]   # 重心 (0,0)，离英雄 < 150
        self.assertEqual(C.focus_escape_point(HERO, ring, [], HOME), (0.0, -900.0))

    def test_hero_itself_not_counted_as_army(self):
        self.assertEqual(C.focus_escape_point(HERO, self.attackers, [HERO], HOME), (-900.0, 0.0))


class TowardHealer(unittest.TestCase):
    """拉出来往身后的己方牧师那里拉（由后排牧师加血）。期望按函数手算。"""
    u = unit(0x200, "hfoo", 0, 0)

    def test_nearest_healer_behind(self):
        allies = [unit(1, "hmpr", -600, 100), unit(2, "hmpr", -300, 0),
                  unit(3, "hmpr", 200, 0),                       # 在前面
                  unit(4, "hfoo", -100, 0),                      # 不是治疗单位
                  unit(5, "hmpr", -200, 0, hp=0),                # 死了
                  unit(6, "hmpr", -1700, 0)]                     # 超出 reach
        self.assertEqual(C.toward_healer(self.u, -1.0, 0.0, allies, 1500.0), (-450.0, 0.0))

    def test_none_when_no_healer(self):
        self.assertIsNone(C.toward_healer(self.u, -1.0, 0.0, [unit(4, "hfoo", -100, 0)], 1500.0))

    def test_focus_escape_prefers_healer_over_army(self):
        allies = [unit(10, "hfoo", -600, 50), unit(11, "hfoo", -800, -50), unit(12, "hmpr", -700, 0)]
        # 打它的人重心 (500,0) -> 方向 (-1,0)；牧师 (-700,0) 身后 150 = (-850,0)，优先于部队重心那条
        self.assertEqual(C.focus_escape_point(HERO, EscapePoint.attackers, allies, HOME), (-850.0, 0.0))


if __name__ == "__main__":
    unittest.main()

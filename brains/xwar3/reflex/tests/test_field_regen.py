# -*- coding: utf-8 -*-
"""后期带恢复卷轴出门、残血的就地治疗（itemuse.shop_want(late) / REGEN_GATHER_R + mod_combat 物品段）。

09-22 晚 后期出门时背包有空格的英雄带上恢复卷轴，残血单位不必回家等治疗，退到附近安全区域就地回血。
毫秒层原来拿全图的兵判「伤兵到齐」：家里有一个伤兵，野外带卷轴的英雄就永远等不到。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itemuse as I  # noqa: E402


def u(addr, t, x, y, hp, hp_max):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=float(hp), hp_max=float(hp_max))


class LateWant(unittest.TestCase):
    def test_two_scrolls_late(self):
        # 09-22 晚起任何时候都备 SREG_CARRY 张（test_scroll_carry）；后期那条照旧成立
        self.assertIn(("sreg", I.LATE_SREG), I.shop_want("Hamg", late=True))
        bag = ["sreg", "phea", "pman", None, None, None]
        self.assertEqual(I.shop_wish(bag, 2000, want=I.shop_want("Hamg", late=True), tier=3), "sreg", "后期再备一张")
        self.assertIsNone(I.shop_wish(["sreg"] + bag[1:3] + ["sreg", "stwp", None], 2000, want=I.shop_want("Hamg", late=True), tier=3))


class FieldGather(unittest.TestCase):
    def test_a_wounded_unit_back_home_does_not_block_the_field_scroll(self):
        hero = u(1, "Hamg", 5000, 0, 700, 825)
        field = [u(10 + i, "hkni", 5100, 60 * i, 300, 835) for i in range(3)]
        at_home = u(20, "hfoo", -5000, 0, 100, 420)
        allies = [hero] + field + [at_home]
        self.assertFalse(I.regen_formation_ready(hero, allies, []), "旧口径：全图算，家里那个没到齐")
        near = [a for a in allies if I.dist(a, hero) <= I.REGEN_GATHER_R]
        self.assertTrue(I.regen_formation_ready(hero, near, []))

    def test_still_waits_for_the_ones_on_their_way(self):
        hero = u(1, "Hamg", 5000, 0, 700, 825)
        allies = [hero] + [u(10 + i, "hkni", 5100, 60 * i, 300, 835) for i in range(3)] + [u(20, "hfoo", 5000 + 1200, 0, 100, 420)]
        near = [a for a in allies if I.dist(a, hero) <= I.REGEN_GATHER_R]
        self.assertFalse(I.regen_formation_ready(hero, near, []), "1200 外正在赶来的：等它进 500")


if __name__ == "__main__":
    unittest.main()

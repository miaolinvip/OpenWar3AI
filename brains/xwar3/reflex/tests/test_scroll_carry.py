# -*- coding: utf-8 -*-
"""英雄随身常备两张恢复卷轴（itemuse.SREG_CARRY / scroll_short / item_reserve + mod_scavenge 商店段）。

09-22 晚 恢复卷轴随身常备两张，商店有货就买，交战后大批残血单位随时能回血。
原来平时每个英雄 1 张、三本以后才 2 张；队里别的英雄有一张就不买；缺卷轴时大脑不留钱，钱常被出兵花光。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itemuse as I  # noqa: E402


def u(addr, t, x, y, hp=800.0, hp_max=800.0):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=float(hp), hp_max=float(hp_max))


VAULT = u(90, "hvlt", 0, 0, 485, 485)


class Carry(unittest.TestCase):
    def test_two_scrolls_at_any_tier(self):
        self.assertEqual(I.SREG_CARRY, 2)
        self.assertIn(("sreg", 2), I.shop_want("Hamg"))
        self.assertIn(("sreg", 2), I.shop_want("Hmkg", late=True))
        bag = ["phea", "sreg", None, None, None, None]
        self.assertEqual(I.shop_wish(bag, 400, want=I.shop_want("Hmkg"), tier=2), "sreg", "一本二本也补到两张")
        self.assertEqual(I.shop_wish(bag, 250, want=I.shop_want("Hmkg"), tier=2), "sreg", "卷轴按急救线：买完剩 150 就买")

    def test_scroll_short(self):
        self.assertTrue(I.scroll_short(["sreg", None, None, None, None, None]))
        self.assertTrue(I.scroll_short([None] * 6))
        self.assertFalse(I.scroll_short(["sreg", "sreg", None, None, None, None]))
        self.assertFalse(I.scroll_short(["phea", "stwp", "pman", "rde1", "bspd", "sreg"]), "背包满了：买不进来")
        self.assertFalse(I.scroll_short(None), "读不到不算缺")


class Reserve(unittest.TestCase):
    def test_hero_near_the_vault_short_of_scrolls_keeps_one_scroll_of_gold(self):
        h = u(1, "Hamg", 800, 0)
        gold, why, kind = I.item_reserve([h], {1: ["sreg", None, None, None, None, None]}, [VAULT], 2)
        self.assertEqual((gold, kind), (I.PRICE["sreg"], "卷轴不到两张"))
        self.assertIn("1/2", why)
        self.assertEqual(I.item_reserve([h], {1: ["sreg", "sreg", None, None, None, None]}, [VAULT], 2)[0], 0)

    def test_far_from_the_vault_or_no_vault_keeps_nothing(self):
        far = u(1, "Hamg", I.SHOP_WALK_R + 100, 0)
        self.assertEqual(I.item_reserve([far], {1: [None] * 6}, [VAULT], 2)[0], 0, "在外面打野：不替它压着钱")
        self.assertEqual(I.item_reserve([u(1, "Hamg", 100, 0)], {1: [None] * 6}, [], 2)[0], 0)

    def test_hurt_potion_comes_first(self):
        hurt = u(1, "Hamg", 5000, 0, hp=300)
        calm = u(2, "Hmkg", 300, 0)
        gold, _, kind = I.item_reserve([calm, hurt], {1: [None] * 6, 2: [None] * 6}, [VAULT], 2)
        self.assertEqual((gold, kind), (I.HURT_RESERVE, "残血缺血瓶"))
        gold1, _, kind1 = I.item_reserve([calm, hurt], {1: [None] * 6, 2: [None] * 6}, [VAULT], 1)
        self.assertEqual((gold1, kind1), (I.SREG_RESERVE, "卷轴不到两张"), "一本藏宝室不卖血瓶：只留卷轴的钱")


if __name__ == "__main__":
    unittest.main()

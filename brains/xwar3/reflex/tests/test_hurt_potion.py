# -*- coding: utf-8 -*-
"""残血英雄攒钱买血瓶（itemuse.hurt_needs_potion / shop_wish(hurt=True) / bag_full_missing + mod_scavenge.write_item_reserve）。

09-22 英雄残血时留钱买生命药水补血；背包满时考虑扔一件到地上腾位置。
1 号实例 09-22：该喝血瓶时背包里没有 183 次、喝上 18 次；常备补货要买完还剩 450，血瓶 150 —— 手里要 600 才买。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itemuse as I  # noqa: E402


def hero(hp, hp_max=800):
    return SimpleNamespace(addr=1, type="Hamg", hp=hp, hp_max=hp_max, x=0.0, y=0.0)


class HurtPotion(unittest.TestCase):
    def test_hurt_hero_buys_a_potion_on_the_emergency_line(self):
        bag = ["sreg", "sreg", "stwp", None, None, None]                # 卷轴两张齐了（09-22 晚 SREG_CARRY），只看血瓶
        self.assertIsNone(I.shop_wish(bag, 320, tier=2), "平时：买完要剩 450，320 金不买血瓶")
        self.assertEqual(I.shop_wish(bag, 320, tier=2, hurt=True), "phea", "残血：买完剩 170 >= 150 就买")
        self.assertIsNone(I.shop_wish(bag, 280, tier=2, hurt=True), "急救线也要守住 150")
        self.assertIsNone(I.shop_wish(bag, 900, tier=1, hurt=True), "一本藏宝室不卖血瓶")

    def test_hurt_puts_the_potion_first(self):
        self.assertEqual(I.shop_wish(["stwp", None, None, None, None, None], 2000, tier=2, hurt=True), "phea")

    def test_who_needs_a_potion(self):
        self.assertTrue(I.hurt_needs_potion(hero(300), ["stwp", None, None, None, None, None], 2))
        self.assertFalse(I.hurt_needs_potion(hero(500), [None] * 6, 2), "血过半不算残血")
        self.assertFalse(I.hurt_needs_potion(hero(300), ["phea", None, None, None, None, None], 2), "背包里有了")
        self.assertFalse(I.hurt_needs_potion(hero(300), None, 2), "背包读不到：别为它留钱")
        self.assertFalse(I.hurt_needs_potion(hero(300), [None] * 6, 1), "一本买不到")

    def test_bag_full_is_counted_separately(self):
        full = ["stwp", "rde1", "cnob", "ratc", "sreg", "belv"]
        self.assertEqual(I.bag_full_missing(full, (("phea", 1), ("sreg", 1))), "phea")
        self.assertIsNone(I.bag_full_missing(full[:5] + [None], (("phea", 1),)), "有空格不算满")
        self.assertIsNone(I.bag_full_missing(full, (("sreg", 1),)), "满了但不缺")


class ReserveFile(unittest.TestCase):
    def test_write_then_brain_reads_it(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import mod_scavenge as S
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "item_reserve.json")
            S.write_item_reserve(I.HURT_RESERVE, "Hamg 300/800 缺生命药水", 4242, path=p)
            with open(p, encoding="utf-8") as fh:
                got = json.load(fh)
            self.assertEqual((got["pid"], got["gold"]), (4242, 150))
            S.write_item_reserve(0, "", 4242, path=p)
            with open(p, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["gold"], 0, "买上了 / 不残血了：清掉")


if __name__ == "__main__":
    unittest.main()

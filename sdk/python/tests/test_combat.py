# -*- coding: utf-8 -*-
"""openwar3.combat：数值对着游戏里看得到的面板钉住（1.27 数据表）。data/game 没提取（开源仓库默认没有）时整组跳过。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402

HAVE_DATA = os.path.exists(w3paths.game_data("UnitBalance.slk")) and os.path.exists(w3paths.game_data("MiscGame.txt"))
if HAVE_DATA:
    from openwar3 import combat as C  # noqa: E402


@unittest.skipUnless(HAVE_DATA, "data/game 没有提取（python data/tools/extract_game_data.py）")
class Stats(unittest.TestCase):
    def test_footman_base_and_upgrades(self):
        s = C.stats("hfoo")
        self.assertEqual((s.hp, s.armor, s.armor_type, s.speed), (420, 2, "large", 270))
        w = s.weapons[0]
        self.assertEqual((w.dmg_min, w.dmg_max, w.cooldown, w.range, w.attack_type), (12, 13, 1.35, 90, "normal"))
        u = C.stats("hfoo", tech={"Rhme": 1, "Rhar": 2, "Rhra": 3})       # 远程攻击升级步兵用不上
        self.assertEqual((u.weapons[0].dmg_min, u.weapons[0].dmg_max, u.armor), (13, 15, 6))
        self.assertEqual(sorted(u.applied), [("Rhar", 2), ("Rhme", 1)])

    def test_hero_attributes(self):
        p = C.stats("Hpal", hero_level=1)                                   # 游戏面板：650 血 255 蓝 3.9 甲 24-34
        self.assertEqual((p.hp, p.mana, p.armor), (650, 255, 3.9))
        self.assertEqual((p.weapons[0].dmg_min, p.weapons[0].dmg_max), (24, 34))
        self.assertAlmostEqual(p.weapons[0].cooldown, 2.2 / 1.26, places=3)
        self.assertEqual(C.stats("Hamg", hero_level=1).mana, 285)
        self.assertGreater(C.stats("Hpal", hero_level=5).hp, p.hp)

    def test_air_and_weapon_choice(self):
        gry, rif, foot = C.stats("hgry"), C.stats("hrif"), C.stats("hfoo")
        self.assertTrue(gry.is_air)
        self.assertEqual(foot.dps_vs(gry), 0.0)                             # 步兵打不到飞的
        self.assertGreater(rif.dps_vs(gry), 0)
        self.assertEqual(gry.weapon_vs(gry).targets, frozenset({"air"}))    # 狮鹫打空中用第二把

    def test_damage_math(self):
        self.assertEqual(C.damage_multiplier("pierce", "small"), 2.0)
        self.assertEqual(C.damage_multiplier("magic", "large"), 2.0)
        self.assertEqual(C.damage_multiplier("siege", "fort"), 1.5)
        self.assertAlmostEqual(C.armor_factor(0), 1.0)
        self.assertAlmostEqual(C.armor_factor(2), 1 - 0.12 / 1.12)
        self.assertAlmostEqual(C.armor_factor(-2), 2 - 0.94 ** 2)
        foot, grunt = C.stats("hfoo"), C.stats("ogru")
        per_sec = 12.5 * 1.0 * C.armor_factor(1) / 1.35                    # 普通打大甲 1.0、兽人步兵 1 甲
        self.assertAlmostEqual(foot.dps_vs(grunt), per_sec)
        self.assertAlmostEqual(C.time_to_kill([foot] * 6, grunt), 700 / (6 * per_sec))
        self.assertIsNone(C.time_to_kill([foot], C.stats("hgry")))
        self.assertIsNone(C.stats("XXXX"))


if __name__ == "__main__":
    unittest.main()

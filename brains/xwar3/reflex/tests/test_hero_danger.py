# -*- coding: utf-8 -*-
"""09-17 英雄接触前预警 mod_combat.hero_danger：身边 1300 内敌方作战单位 >= 5 且 t_them > 2 × t_us 才拉。期望按 DPS 表手算。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def u(addr, typ, owner, x, y, hp=400):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp, hp_max=hp, order=0, order_known=True)


class HeroDanger(unittest.TestCase):
    def setUp(self):
        self.hero = u(1, "Hmkg", 0, 0, 0, 700)

    def test_many_strong_foes_pull(self):
        foes = [u(10 + i, "ugho", 1, 800, i * 40, 340) for i in range(8)]
        ours = [self.hero, u(2, "hfoo", 0, 100, 0, 420)]
        dps_f = sum(C._unit_dps("ugho") for _ in foes)              # 09-18 起和生产代码同一个口径（英雄按 25，不再查表 3.2）
        dps_m = C._unit_dps("Hmkg") + C._unit_dps("hfoo")
        t_them, t_us = 8 * 340 / dps_m, (700 + 420) / dps_f
        self.assertGreater(t_them, 2 * t_us)                      # 先确认这个场景按公式确实是明显打不过
        d = C.hero_danger(self.hero, foes, ours)
        self.assertIsNotNone(d)
        self.assertEqual((d[2], d[3]), (8, 2))
        self.assertAlmostEqual(d[0], t_them, places=3)

    def test_too_few_foes_or_far(self):
        ours = [self.hero]
        self.assertIsNone(C.hero_danger(self.hero, [u(10 + i, "ugho", 1, 800, i * 40) for i in range(4)], ours))   # 4 < 5
        self.assertIsNone(C.hero_danger(self.hero, [u(10 + i, "ugho", 1, 1400, i * 40) for i in range(8)], ours))  # 1400 外

    def test_even_fight_not_pulled(self):
        foes = [u(10 + i, "hfoo", 1, 800, i * 40, 420) for i in range(5)]
        ours = [self.hero] + [u(20 + i, "hfoo", 0, 100, i * 40, 420) for i in range(6)]
        self.assertIsNone(C.hero_danger(self.hero, foes, ours))

    def test_healthy_hero_with_four_escorts_still_retreats_from_catastrophic_force(self):
        """095~202：旧调用处会因血量 >65% 且护卫 >=4 忽略已经判定的两倍劣势。"""
        foes = [u(10 + i, "otau", 1, 700, i * 35, 1300) for i in range(8)]
        ours = [self.hero] + [u(20 + i, "hfoo", 0, 100, i * 40, 420) for i in range(4)]
        decision = C.precontact_hero_retreat(self.hero, foes, ours)
        self.assertIsNotNone(decision)
        self.assertEqual((decision[2], decision[3]), (8, 5))

    def test_isolated_heroes_see_enemy_reserve_before_it_reaches_contact_range(self):
        """1-215：敌军 3000 内已成群逼近，永久兵在 4000 外，不能等进 1300 才预警。"""
        mate = u(2, "Hamg", 0, 100, 0, 675)
        foes = [u(10 + i, "ugho", 1, 2450 + i * 60, i * 30, 340) for i in range(6)]
        self.assertIsNone(C.hero_danger(self.hero, foes, [self.hero, mate]))
        decision = C.precontact_hero_retreat(self.hero, foes, [self.hero, mate])
        self.assertIsNotNone(decision)
        self.assertEqual((decision[2], decision[3]), (6, 2))

    def test_enemy_reserve_does_not_pull_hero_with_real_escort_squad(self):
        escorts = [u(20 + i, "hfoo", 0, 100, i * 60, 420) for i in range(C.HERO_ESCORT_MIN)]
        foes = [u(10 + i, "ugho", 1, 2450 + i * 60, i * 30, 340) for i in range(6)]
        self.assertIsNone(C.precontact_hero_retreat(self.hero, foes, [self.hero] + escorts))


if __name__ == "__main__":
    unittest.main()


class FocusHero(unittest.TestCase):
    """v292 远程集火敌方最脆英雄（和 war3ai.ps1 W3-WeakestHero 同一条规则）。"""

    def test_ranged_picks_weakest_hero_in_reach(self):
        rifle = u(1, "hrif", 0, 0, 0)
        mk = u(10, "Hmkg", 1, 500, 0, 500)
        am = u(11, "Hamg", 1, 450, 100, 300)
        peon = u(12, "hfoo", 1, 300, 0, 50)
        e = lambda f: f.hp * (1.0 + 0.06 * max(0.0, C.ARMOR.get(f.type, 0.0)))  # noqa: E731
        want = am if e(am) < e(mk) else mk
        self.assertIs(C.focus_hero(rifle, [mk, am, peon]), want)

    def test_melee_or_out_of_reach(self):
        self.assertIsNone(C.focus_hero(u(1, "hfoo", 0, 0, 0), [u(10, "Hmkg", 1, 100, 0)]))
        reach = C.RANGE.get("hrif", 100.0) + C.FOCUS_HERO_PAD
        self.assertIsNone(C.focus_hero(u(1, "hrif", 0, 0, 0), [u(10, "Hmkg", 1, reach + 10, 0)]))
        self.assertIsNotNone(C.focus_hero(u(1, "hrif", 0, 0, 0), [u(10, "Hmkg", 1, reach - 10, 0)]))

    def test_refill_ranged_goes_hero_even_if_healer_there(self):
        rifle = u(1, "hrif", 0, 0, 0)
        hero = u(10, "Hmkg", 1, 500, 0, 700)
        healer = u(11, "hmpr", 1, 400, 0, 50)
        picks = C.pick_refill([rifle], [hero, healer], {}, 100.0)
        self.assertEqual([(a.addr, t.addr) for a, t, _j in picks], [(1, 10)])
        melee = u(2, "hfoo", 0, 0, 0)
        picks = C.pick_refill([melee], [hero, healer], {}, 100.0)
        self.assertEqual(picks[0][1].addr, 11)       # 近战照旧挑脆皮（50 血的牧师）

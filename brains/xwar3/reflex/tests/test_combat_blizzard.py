# -*- coding: utf-8 -*-
"""躲暴风雪 + 召唤物算敌方作战单位 —— 直接调 mod_combat 的生产函数。

v257 三局决定性防守战：敌方 Archmage 接触帧 27%~37% 在引导暴风雪，
我方掉血 44%/46% 是"同一帧 250 内 >=3 个一起掉"（没有暴风雪的那局 18%）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1


def unit(addr, typ, owner, x, y, hp=400, order=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=max(hp, 1), order=order, order_known=True)


class BlizzardDodge(unittest.TestCase):
    def scene(self, caster_order=C.BLIZZARD, caster_at=(0, -800)):
        caster = unit(100, "Hamg", FOE, caster_at[0], caster_at[1], hp=800, order=caster_order)
        foot = [unit(1, "hfoo", ME, 0, 0), unit(2, "hfoo", ME, 100, 0),
                unit(3, "hfoo", ME, 0, 100), unit(4, "hrif", ME, -100, 50)]
        far = unit(5, "hfoo", ME, 900, 900)
        return caster, foot, far

    def test_no_blizzard_no_move(self):
        caster, foot, far = self.scene(caster_order=C.ORDER_ATTACK)
        moves, center, casting = C.blizzard_dodge([caster], foot, foot + [far])
        self.assertEqual((moves, center, casting), ([], None, False))

    def test_cluster_under_blizzard_steps_away_from_caster(self):
        caster, foot, far = self.scene()
        moves, center, casting = C.blizzard_dodge([caster], foot, foot + [far])
        self.assertTrue(casting)
        self.assertEqual(sorted(u.addr for u, _, _ in moves), [1, 2, 3, 4])
        # 簇重心 = 四个点的平均：x (0+100+0-100)/4 = 0，y (0+0+100+50)/4 = 37.5；
        # 施法者在正下方 -> 往 +y 挪 BLIZ_STEP
        u, tx, ty = next(m for m in moves if m[0].addr == 1)
        self.assertAlmostEqual(center[0], 0.0)
        self.assertAlmostEqual(center[1], 37.5)
        self.assertAlmostEqual(ty - u.y, C.BLIZ_STEP, delta=1.0)
        self.assertAlmostEqual(tx - u.x, 0.0, delta=1.0)

    def test_two_hits_is_not_a_wave(self):
        caster, foot, far = self.scene()
        moves, center, casting = C.blizzard_dodge([caster], foot[:2], foot + [far])
        self.assertEqual((moves, center, casting), ([], None, True))

    def test_cluster_far_from_caster_is_something_else(self):
        caster, foot, far = self.scene(caster_at=(0, -3000))
        moves, center, casting = C.blizzard_dodge([caster], foot, foot + [far])
        self.assertEqual((moves, center, casting), ([], None, True))

    def test_units_not_offered_are_not_moved(self):
        caster, foot, far = self.scene()
        moves, _, _ = C.blizzard_dodge([caster], foot, foot[:2])
        self.assertEqual(sorted(u.addr for u, _, _ in moves), [1, 2])


class FoeFighterType(unittest.TestCase):
    def test_water_elementals_count(self):
        for typ in ("hwat", "hwt2", "hwt3"):
            self.assertIn(typ, C.SUMMONS, typ)
            self.assertTrue(C.foe_fighter_type(typ), typ)

    def test_regular_units_still_count(self):
        self.assertTrue(C.foe_fighter_type("hfoo"))

    def test_buildings_and_zeppelins_do_not(self):
        for typ in ("hgtw", "htow", "hhou", "nzep"):
            self.assertFalse(C.foe_fighter_type(typ), typ)


if __name__ == "__main__":
    unittest.main()

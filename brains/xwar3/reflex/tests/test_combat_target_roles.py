# -*- coding: utf-8 -*-
"""补攻击令挑目标：远程集火治疗、近战先杀脆皮 —— 数值和 war3ai.ps1 的 test-target-roles.ps1 同一组手算。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, hp, owner=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp, order=0, order_known=True)


def best(u, foes):
    return max(foes, key=lambda f: C.target_score(u, f)).type


class TargetRoles(unittest.TestCase):
    def test_ranged_focuses_healer(self):
        rif = unit(1, "hrif", 0, 0, 535)
        foes = [unit(10, "hmpr", 400, 0, 290, 1), unit(11, "hfoo", 200, 0, 420, 1), unit(12, "hsor", 400, 50, 325, 1)]
        self.assertEqual(best(rif, foes), "hmpr")
        self.assertAlmostEqual(C.target_score(rif, foes[0]), 6 * 6 * 1000 / 290, places=3)

    def test_melee_prefers_squishy_over_armored(self):
        foot = unit(2, "hfoo", 0, 0, 420)
        sor, kni = unit(10, "hsor", 200, 0, 325, 1), unit(11, "hkni", 200, 0, 835, 1)
        self.assertEqual(best(foot, [sor, kni]), "hsor")
        self.assertAlmostEqual(C.target_score(foot, sor), 20.0, places=1)
        self.assertAlmostEqual(C.target_score(foot, kni), 4.6, places=1)

    def test_melee_does_not_chase_hero_or_far_backline(self):
        foot = unit(2, "hfoo", 0, 0, 420)
        self.assertEqual(best(foot, [unit(10, "Hmkg", 200, 0, 500, 1), unit(11, "hfoo", 200, 0, 420, 1)]), "hfoo")
        self.assertEqual(best(foot, [unit(10, "hfoo", 150, 0, 420, 1), unit(11, "hmpr", 1200, 0, 290, 1)]), "hfoo")

    def test_refill_uses_target_score(self):
        import ast
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mod_combat.py"),
                   encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "main")
        # 09-16 跟打；09-22 加了塔（英雄不补打塔下的目标，test_tower_safe_retreat）
        self.assertIn("pick_refill(idle_now[:6], fz, focus_recent, now, enemy_towers(snap.units, snap.me))", ast.unparse(fn))
        pr = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "pick_refill")
        self.assertIn("target_score(u, f)", ast.unparse(pr))

    def test_creep_focus_prefers_high_damage_core_over_low_hp_small_creep(self):
        small = unit(10, "nmrr", 200, 0, 30, C.CREEP_OWNER)
        core = unit(11, "ntrg", 220, 0, 1250, C.CREEP_OWNER)
        self.assertEqual(C.creep_focus_target([small, core]).addr, core.addr)

    def test_creep_refill_units_join_same_reachable_core(self):
        rifles = [unit(1, "hrif", 0, 0, 535), unit(2, "hrif", 0, 40, 535)]
        for rifle in rifles:
            rifle.task_known, rifle.has_task = True, False
        small = unit(10, "nmrr", 300, 0, 30, C.CREEP_OWNER)
        core = unit(11, "ntrg", 320, 0, 1250, C.CREEP_OWNER)
        small.hp_max = core.hp_max = 1300
        small.order_known = core.order_known = True
        small.order = core.order = C.ORDER_ATTACK
        picks, held = C.creep_refill_picks(rifles, [small, core], 1.0, {}, {}, {}, {})
        self.assertEqual(held, 0)
        self.assertEqual({target.addr for _u, target in picks}, {core.addr})


if __name__ == "__main__":
    unittest.main()

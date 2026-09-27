# -*- coding: utf-8 -*-
"""补攻击令挑谁 —— mod_combat.refill_candidates（施法保护期）。

09-16 施法回执：暴风雪 4 次、风暴之锤 1 次令接了却没进冷却，1.2 秒后英雄订单是攻击令 0x000D000F 或槽空，
一次也没挂着施法令 —— 刚下了施法令的英雄像个闲兵，被补攻击令改成去打人。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, has_task=False, task_known=True, typ="hfoo", x=0.0, hp=400, hp_max=400, owner=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=x, y=0.0, hp=hp, hp_max=hp_max,
                order=0, order_known=True, task_known=task_known, has_task=has_task)


class Refill(unittest.TestCase):
    def test_idle_units_are_candidates(self):
        got, held = C.refill_candidates([unit(1), unit(2)], 10.0, set(), {}, {}, {})
        self.assertEqual(([u.addr for u in got], held), ([1, 2], 0))

    def test_busy_or_unknown_task_skipped(self):
        got, _ = C.refill_candidates([unit(1, has_task=True), unit(2, task_known=False)], 10.0, set(), {}, {}, {})
        self.assertEqual(got, [])

    def test_rotating_dodged_pulled_skipped(self):
        us = [unit(1), unit(2), unit(3), unit(4)]
        got, held = C.refill_candidates(us, 10.0, {1}, {2: 11.0}, {3: 12.0}, {})
        self.assertEqual(([u.addr for u in got], held), ([4], 0))

    def test_casting_hero_is_held_until_expiry(self):
        hero = unit(7, typ="Hamg")
        got, held = C.refill_candidates([hero], 10.0, set(), {}, {}, {7: 10.0 + C.CAST_HOLD})
        self.assertEqual((got, held), ([], 1))
        got, held = C.refill_candidates([hero], 10.0 + C.CAST_HOLD + 0.1, set(), {}, {}, {7: 10.0 + C.CAST_HOLD})
        self.assertEqual(([u.addr for u in got], held), ([7], 0))

    def test_hold_longer_than_receipt_delay(self):
        # 保护期要盖住施法回执的第一次查看，否则回执看到的还是被覆盖后的订单
        self.assertGreater(C.CAST_HOLD, C.CAST_RCPT_DELAY)

    def test_awake_creep_refills_idle_ranged_hero_at_attack_edge(self):
        hero = unit(7, typ="Hamg", x=700.0, hp=600, hp_max=600)
        creep = unit(70, typ="nwlt", x=0.0, hp=299, hp_max=300, owner=C.CREEP_OWNER)
        got, held = C.creep_refill_picks([hero], [creep], 10.0, set(), {}, {}, {})
        self.assertEqual(([(u.addr, t.addr) for u, t in got], held), ([(7, 70)], 0))

    def test_sleeping_creep_or_protected_cast_does_not_get_refill(self):
        hero = unit(7, typ="Hamg", x=500.0, hp=600, hp_max=600)
        sleeping = unit(70, typ="nwlt", x=0.0, hp=300, hp_max=300, owner=C.CREEP_OWNER)
        self.assertEqual(C.creep_refill_picks([hero], [sleeping], 10.0, set(), {}, {}, {}), ([], 0))
        sleeping.has_task = True
        got, held = C.creep_refill_picks([hero], [sleeping], 10.0, set(), {}, {}, {7: 20.0})
        self.assertEqual((got, held), ([], 1))

    def test_critical_unit_is_not_sent_back_by_refill(self):
        foot = unit(8, typ="hfoo", x=200.0, hp=100, hp_max=420)
        creep = unit(70, typ="nwlt", x=0.0, hp=250, hp_max=300, owner=C.CREEP_OWNER)
        self.assertEqual(C.creep_refill_picks([foot], [creep], 10.0, set(), {}, {}, {}), ([], 0))


if __name__ == "__main__":
    unittest.main()

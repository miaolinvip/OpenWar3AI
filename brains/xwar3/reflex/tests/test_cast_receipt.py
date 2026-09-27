# -*- coding: utf-8 -*-
"""施法回执 + 施法前判学没学 —— mod_combat.cast_receipt / plan_hero_cast(learned=...)。

09-16：施法令返回 1 只说明引擎接了令；技能真放出去才会进冷却（技能的冷却标志，实测水元素 18.0 秒递减）。
012306 局首批：水元素/雷霆一击 5/5 进冷却，风暴之锤、暴风雪 4/4 没进冷却 —— 没进冷却时再看英雄还挂不挂着施法令；
同一局山丘 1 级去放没学的雷霆一击被引擎退（施法被退原因：thunderclap·没学）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1


def order(oid):
    return {"order": oid, "target": None, "base_target": None, "x": 0.0, "y": 0.0}


class CastReceipt(unittest.TestCase):
    def test_cooling_means_cast_went_off(self):
        self.assertEqual(C.cast_receipt({"level": 1, "flags": 0x40001215}), "进冷却")

    def test_not_cooling_split_by_order(self):
        ready = {"level": 1, "flags": 0x10}
        bolt = C.SPELL_ORDERS["stormbolt"]
        self.assertEqual(C.cast_receipt(ready, None, bolt), "没进冷却·槽空")
        self.assertEqual(C.cast_receipt(ready, order(bolt), bolt), "没进冷却·还挂着施法令")
        self.assertEqual(C.cast_receipt(ready, order(0x000D0003), bolt), "没进冷却·订单000D0003")

    def test_not_learned_and_unreadable(self):
        self.assertEqual(C.cast_receipt({"level": 0}), "没学")
        self.assertEqual(C.cast_receipt(None), "读不到")

    def test_delay_shorter_than_shortest_cooldown(self):
        # 最短的圣光术 5 游戏秒 ≈ 1.67 墙钟秒（3 倍速）：回执要在它冷却完之前看
        self.assertLess(C.CAST_RCPT_DELAY, 5.0 / 3)


def u(addr, typ, owner, x, y, hp=400):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp, order=0, order_known=True)


class LearnedGate(unittest.TestCase):
    def surrounded_mk(self):
        hero = u(1, "Hmkg", ME, 0, 0, 700)
        foes = [u(10 + i, "hfoo", FOE, 100 + 40 * i, 0) for i in range(3)]      # 300 内 3 个：雷霆一击的时机
        return hero, foes

    def test_unlearned_thunderclap_skipped(self):
        hero, foes = self.surrounded_mk()
        skipped = {}
        plan = C.plan_hero_cast(hero, foes, [hero], lambda n: True,
                                learned={"AHtb": 1}, unlearned=skipped)
        self.assertIsNone(plan)                     # 附近没有英雄/施法者，风暴之锤没目标；雷霆一击没学
        self.assertEqual(skipped, {"thunderclap": 1})

    def test_learned_thunderclap_cast(self):
        hero, foes = self.surrounded_mk()
        plan = C.plan_hero_cast(hero, foes, [hero], lambda n: True, learned={"AHtb": 1, "AHtc": 1})
        self.assertEqual(plan[0], "thunderclap")

    def test_unknown_learned_does_not_gate(self):
        hero, foes = self.surrounded_mk()
        plan = C.plan_hero_cast(hero, foes, [hero], lambda n: True, learned=None)
        self.assertEqual(plan[0], "thunderclap")


if __name__ == "__main__":
    unittest.main()

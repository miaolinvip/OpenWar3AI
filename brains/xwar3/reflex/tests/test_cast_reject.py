# -*- coding: utf-8 -*-
"""施法被退的原因 —— mod_combat.reject_reason + w3cmd.find_ability（假通道）。

09-16：以前下令失败只返回 0、不说原因（没学 / 没蓝 / 冷却分不开）；蓝、技能、冷却标志都读得到之后当场分类。
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
import w3cmd  # noqa: E402


class Reason(unittest.TestCase):
    def test_unreadable(self):
        self.assertEqual(C.reject_reason(None, 300.0, 125.0), "读不到技能")

    def test_not_learned(self):
        self.assertEqual(C.reject_reason({"level": 0}, 300.0, 125.0), "没学")

    def test_cooling_beats_mana(self):
        self.assertEqual(C.reject_reason({"level": 2, "flags": 0x210}, 10.0, 125.0), "冷却中")

    def test_low_mana(self):
        self.assertEqual(C.reject_reason({"level": 1, "flags": 0x10}, 70.0, 125.0), "蓝不够")

    def test_unknown_mana_is_other(self):
        self.assertEqual(C.reject_reason({"level": 1, "flags": 0x10}, None, 125.0), "别的原因")

    def test_every_spell_has_ability_code(self):
        self.assertEqual(set(C.SPELL_ABIL), set(C.SPELL_ORDERS))


class FindAbilityFeedsReason(unittest.TestCase):
    """find_ability（W3P 世界块）的返回形状正好是 reject_reason 要的：flags 0x200 = 冷却中。"""

    def cmd(self, abilities, detail=True):
        import w3world as W
        u = W.Unit(0, 0x14000000, 1, 2, "Hamg", 0, W.ALIVE | W.HERO | (W.DETAIL if detail else 0),
                   0.0, 0.0, 500.0, 500.0, 300.0, 300.0, None, None, 0.0, 0.0, None, None, None,
                   3, 0, 0, 0, abilities=abilities, has_detail=detail)

        class World:
            def read(self):
                return W.WorldState(1, 0, 1.0, 1, 0, True, 300, 50, 0, 0, 0, [], [u], [])
        return w3cmd.Cmd.with_backends(None, World())

    def test_found(self):
        import w3world as W
        got = self.cmd([W.Ability("AHwe", 2, 12.0, 0x210)]).find_ability(0x14000000, "AHwe")
        self.assertEqual((got["code"], got["level"], got["flags"]), ("AHwe", 2, 0x210))
        self.assertEqual(C.reject_reason(got, 300.0, 125.0), "冷却中")

    def test_missing_and_failure(self):
        self.assertEqual(self.cmd([]).find_ability(0x14000000, "AHwe"), {"level": 0})
        self.assertIsNone(self.cmd([], detail=False).find_ability(0x14000000, "AHwe"))


if __name__ == "__main__":
    unittest.main()

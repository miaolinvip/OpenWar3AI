# -*- coding: utf-8 -*-
"""下施法令前按引擎冷却顺延 —— mod_combat.SpellCooldowns.hold。

09-16：SPELL_CD 常数表比实际短，024229 局水元素被引擎以「冷却中」退了 2 次；
引擎给得出技能是否在冷却、还剩几游戏秒（实测水元素 18.0 秒按 3 倍速递减）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402

HERO = 0x14A5C864


class Hold(unittest.TestCase):
    def test_hold_pushes_ready_time(self):
        cds = C.SpellCooldowns()
        cds.cast(HERO, "waterelemental", 100.0)                      # 常数表：100 + 7
        self.assertTrue(cds.ready(HERO, "waterelemental", 107.5))
        cds.hold(HERO, "waterelemental", 100.0 + 18.0 / C.GAME_SPEED + 3.0)
        self.assertFalse(cds.ready(HERO, "waterelemental", 107.5))   # 引擎说还有这么久
        self.assertTrue(cds.ready(HERO, "waterelemental", 109.1))

    def test_hold_never_moves_earlier(self):
        cds = C.SpellCooldowns()
        cds.rejected(HERO, "blizzard", 100.0)                        # 退避到 110
        cds.hold(HERO, "blizzard", 101.0)
        self.assertFalse(cds.ready(HERO, "blizzard", 105.0))

    def test_game_speed_matches_cd_table(self):
        # SPELL_CD 按「游戏秒 / 3」写的：水元素冷却 20 游戏秒 ≈ 7 墙钟秒
        self.assertAlmostEqual(20.0 / C.GAME_SPEED, C.SPELL_CD["waterelemental"], delta=0.5)


if __name__ == "__main__":
    unittest.main()

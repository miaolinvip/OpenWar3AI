# -*- coding: utf-8 -*-
"""打野时英雄血多就顶着打（mod_combat.hero_exposure 的 CREEP_TANK_FRAC）。

09-23 打野时英雄不应动辄跑远、停止攻击；开打后英雄持续输出，血量充足时顶一会伤害
（给小兵回血时英雄自己也会回血）。1-418 23:41:46~55（清营地强度 13）：大法师 498/525 被「孤军且敌方占优，敌 6、有效护卫 0」
连拉 3 次各 10 秒，部队只能「打野原营地重整，等英雄归队」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


CAMP = [unit(100 + i, "nftr" if i < 4 else "nfsh", 120 + 40 * i, 60, 450, 450, C.CREEP_OWNER) for i in range(6)]


class CreepTank(unittest.TestCase):
    def test_1418_healthy_hero_keeps_fighting_the_camp(self):
        h = unit(1, "Hamg", hp=498, maximum=525)
        self.assertIsNone(C.hero_exposure(h, CAMP, [h]), "95% 血、六只野怪贴着：顶着打")
        half = unit(1, "Hamg", hp=300, maximum=525)                  # 57%
        self.assertIsNone(C.hero_exposure(half, CAMP, [half]))

    def test_low_hero_still_leaves(self):
        low = unit(1, "Hamg", hp=200, maximum=525)                   # 38%
        ex = C.hero_exposure(low, CAMP, [low])
        self.assertIsNotNone(ex)
        self.assertEqual(ex[0], "低血量脱离接触")

    def test_players_are_unchanged(self):
        h = unit(1, "Hamg", hp=498, maximum=525)
        foes = [unit(200 + i, "ogru", 120 + 40 * i, 60, 700, 700, 1) for i in range(6)]
        ex = C.hero_exposure(h, foes, [h])
        self.assertIsNotNone(ex, "对面玩家的六个兽人贴着孤身英雄：照旧拉")


if __name__ == "__main__":
    unittest.main()

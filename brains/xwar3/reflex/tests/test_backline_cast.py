# -*- coding: utf-8 -*-
"""撤退中的英雄在后排也能放够得着的瞬发技能（mod_combat.plan_hero_cast + BACKLINE_CASTS）。

09-22（1-301 15:50）敌军来打分矿、人扎成一堆，正是放技能的好时机，血法师却几乎没放技能。
心跳：那一分钟烈焰风暴「下令 0、被退 0、没学 0、缺蓝 0」；她两次被「大群贴脸提前脱离」拉到后排，撤退中只放逃生技能。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402


def u(addr, t, x, y, owner=0, hp=100.0, hp_max=100.0, hero=False):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), owner=owner, hp=hp, hp_max=hp_max,
                           is_hero=hero, order=0, handle_lo=addr, handle_hi=1)


def plan(hero, foes, pulled=True):
    return C.plan_hero_cast(hero, foes, [hero], lambda n: True, mana=500.0,
                            learned={"AHfs": 1, "AHbz": 1, "AHwe": 1, "AHdr": 1}, ctx={"pulled": pulled})


def clump(x, n=6):
    return [u(100 + i, "earc", x + 30 * (i % 3), 30 * (i // 3), owner=1, hp=245, hp_max=245) for i in range(n)]


class BacklineCast(unittest.TestCase):
    def test_pulled_blood_mage_flame_strikes_a_clump_in_range(self):
        bm = u(1, "Hblm", 0, 0, hp=360, hp_max=600, hero=True)            # 60%
        p = plan(bm, clump(650))
        self.assertIsNotNone(p)
        self.assertEqual(p[0], "flamestrike")

    def test_not_when_low(self):
        bm = u(1, "Hblm", 0, 0, hp=240, hp_max=600, hero=True)            # 40%
        p = plan(bm, clump(650))
        self.assertTrue(p is None or p[0] != "flamestrike")

    def test_not_when_it_would_have_to_walk_forward(self):
        bm = u(1, "Hblm", 0, 0, hp=500, hp_max=600, hero=True)
        rng = C.skill("flamestrike", 1).get("Rng") or 800.0
        p = plan(bm, clump(rng + 150))                                   # 规则的 reach 带 pad 能选中，撤退中不许走过去
        self.assertTrue(p is None or p[0] != "flamestrike")
        # 09-22 起烈焰风暴不在撤退中也不走过去放（no_walk）：先走过去的那一两秒敌人就散了，
        #   火落地时落点里一个敌人都没有的占 57%（mod_combat STILL_WINDOW 上面那段）。射程内的照旧放。
        p2 = plan(bm, clump(rng + 150), pulled=False)
        self.assertTrue(p2 is None or p2[0] != "flamestrike")
        self.assertEqual(plan(bm, clump(rng - 150), pulled=False)[0], "flamestrike")

    def test_channelled_blizzard_is_not_cast_while_pulled(self):
        self.assertNotIn("blizzard", C.BACKLINE_CASTS)
        am = u(2, "Hamg", 0, 0, hp=500, hp_max=500, hero=True)
        p = plan(am, clump(500))
        self.assertTrue(p is None or p[0] != "blizzard")


if __name__ == "__main__":
    unittest.main()

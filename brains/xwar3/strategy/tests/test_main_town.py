# -*- coding: utf-8 -*-
"""主基地判定（World.main_town）和它决定的建筑落点。

09-21 1-284：分矿大厅盖好、主城还没升完的那一段，两座都是城镇大厅（htow），
原来「同本数取第一个」按快照顺序取到了分矿 —— 23:35:40 藏宝室盖在分矿 (-3776,-2624)，离主基地 5600，
家里的伤兵买不到卷轴。商店应建在主基地附近，不要建到二矿；二矿那边只造防御塔。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tactics import U, world  # noqa: E402

MAIN = (-5184.0, 2944.0)
EXPO = (-4352.0, -2624.0)


def base(main_first: bool, main_type="htow"):
    main = U(1, main_type, 0, *MAIN)
    expo = U(2, "htow", 0, *EXPO)
    around_main = [U(10 + i, t, 0, MAIN[0] + 300 + 200 * i, MAIN[1] - 300)
                   for i, t in enumerate(("halt", "hbar", "hhou", "hhou", "hlum", "hbla"))]
    around_expo = [U(30, "hwtw", 0, EXPO[0] + 500, EXPO[1])]
    halls = [main, expo] if main_first else [expo, main]
    return world(halls + around_main + around_expo)


class MainTown(unittest.TestCase):
    def test_two_town_halls_pick_the_one_with_the_base_around_it(self):
        for main_first in (True, False):                       # 快照顺序不再决定答案
            w = base(main_first)
            self.assertEqual(w.main_town().hall.addr, 1, f"main_first={main_first}")

    def test_higher_tier_still_wins_over_building_count(self):
        w = world([U(1, "htow", 0, *MAIN), U(2, "hkee", 0, *EXPO)]
                  + [U(10 + i, "hhou", 0, MAIN[0] + 300, MAIN[1] + 100 * i) for i in range(6)])
        self.assertEqual(w.main_town().hall.addr, 2)

    def test_other_races_tier_counts_too(self):
        w = world([U(1, "ogre", 0, *MAIN), U(2, "ostr", 0, *EXPO)])
        self.assertEqual(w.main_town().hall.addr, 2)


if __name__ == "__main__":
    unittest.main()

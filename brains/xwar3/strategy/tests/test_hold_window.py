# -*- coding: utf-8 -*-
"""对一矿全押兵的对手（AMAI），6~13 分钟它那一波之前不出远门打野（tactics.hold_window / creep_plans）。

09-23 针对 AMAI 的短板优化：它基本不开二矿，前期一直大量出兵骚扰。
15 局对 AMAI：对面一次二矿都没开；兵力 6 分钟持平，10 分钟 3~11 对 12~17、14 分钟 3~17 对 15~25 ——
1-420 9~10 分钟去打离二矿 2940 的强度 16 营地、死了两个火枪手，12 分钟对面压过来时主力还在西边。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

MAIN, NATURAL = (-5184, 2944), (-4352, -2624)


def world(t=600.0, foe_halls=1, foes=8, camp_xy=(-6639, -773)):
    units = [U(1, "htow", *MAIN), U(2, "htow", *NATURAL), U(3, "Hamg", MAIN[0] + 300, MAIN[1])]
    units += [U(10 + i, "hfoo", MAIN[0] + 400, MAIN[1] - 60 * i) for i in range(8)]
    units += [U(900 + i, "htow", 4672, 2944 - 6000 * i, owner=1) for i in range(foe_halls)]
    units += [U(920 + i, "hfoo", 4400, 2944 + 60 * i, owner=1) for i in range(foes)]
    units += [U(700 + i, "nwlg", camp_xy[0], camp_xy[1] + 60 * i, owner=12) for i in range(2)]
    return build_world(units, 0, t, 300, 300, 30, 60, levels={3: 2})


def why(w):
    out = {}
    T.creep_plans(w, w.my_army + w.my_heroes, MAIN, w.my_halls[0], why=out)
    return out


class Hold(unittest.TestCase):
    def test_1420_far_camp_is_skipped_while_the_one_base_enemy_masses(self):
        w = world()
        self.assertTrue(T.hold_window(w))
        self.assertIn("对面一矿暴兵·这段时间不出远门", why(w), "离二矿 2940 的营地（1-420 那个）")

    def test_camp_next_to_our_base_is_still_fine(self):
        self.assertNotIn("对面一矿暴兵·这段时间不出远门", why(world(camp_xy=(NATURAL[0] - 1500, NATURAL[1] + 800))))

    def test_window_conditions(self):
        self.assertFalse(T.hold_window(world(t=900.0)), "13 分钟以后")
        self.assertFalse(T.hold_window(world(t=300.0)), "6 分钟以前")
        self.assertFalse(T.hold_window(world(foe_halls=2)), "对面开了二矿：不是一矿全押兵")
        self.assertFalse(T.hold_window(world(foes=2)), "对面没什么兵")
        self.assertFalse(T.hold_window(world(foe_halls=0)), "看不见对面大厅")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""水元素护卫只打「已经在打我们的人」的野怪（mod_combat.guard_picks creep_targets）。

09-22（1-301 4:30）近几局准备清分矿野时，水元素总跑到另一堆野怪那里；英雄已经就位，水元素没有跟上。
水元素的行动要和英雄统一决策。
命令记录：大脑让水元素跟主力去分矿集合点，本规则同时让它去打离大法师最近的野怪 —— 路过的另一个营地。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402


def u(addr, t, owner, x, y):
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=float(x), y=float(y), hp=500.0, hp_max=500.0,
                           is_hero=t[:1].isupper(), handle_lo=addr, handle_hi=7)


HERO = u(1, "Hamg", 0, 0, 0)
WATER = u(2, "hwat", 0, 100, 0)
FEET = [u(20, "hfoo", 0, 300, 0), u(21, "hfoo", 0, -300, 0)]
CREEP = u(10, "ngno", 12, 500, 0)


class EngagedOnly(unittest.TestCase):
    def test_sleeping_creep_of_another_camp_is_left_alone(self):
        picks = C.guard_picks(HERO, [WATER], [CREEP], {}, 0.0, [HERO] + FEET, creep_targets={})
        self.assertEqual(picks, [])

    def test_creep_hitting_our_footman_is_taken_on(self):
        picks = C.guard_picks(HERO, [WATER], [CREEP], {}, 0.0, [HERO] + FEET, creep_targets={10: (20, 7)})
        self.assertEqual([(w.addr, t.addr) for w, t in picks], [(2, 10)])

    def test_creep_hitting_the_water_elemental_itself_counts(self):
        picks = C.guard_picks(HERO, [WATER], [CREEP], {}, 0.0, [HERO] + FEET, creep_targets={10: (2, 7)})
        self.assertEqual(len(picks), 1)

    def test_enemy_player_unit_still_guarded_without_aggro(self):
        grunt = u(11, "ogru", 1, 500, 0)
        picks = C.guard_picks(HERO, [WATER], [grunt, CREEP], {}, 0.0, [HERO], creep_targets={})
        self.assertEqual([t.addr for _w, t in picks], [11])


if __name__ == "__main__":
    unittest.main()

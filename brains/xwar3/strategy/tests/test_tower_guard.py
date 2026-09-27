# -*- coding: utf-8 -*-
"""附近还有敌兵时先打兵、不顶着能打人的建筑打（tactics._tower_guard）。

09-22 1-309 12 分打对面分矿：几个兵去打建筑、另一些兵和英雄在外面打兵，火力分散；要求全体优先集火敌方单位，
小兵也不要顶着有攻击力的建筑打 —— 扛不住塔的伤害，阵亡了好几个。
目标表：t=233~241 我方 3~4 个在打 owtw/otrb/ostr，1200 内 5~8 个敌兵；5 个步兵死在 owtw + otrb 旁边、死时 800 内没有敌方单位。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HALL = U(900, "ofrt", 4200, -2600, owner=1)
TOWER = U(901, "owtw", 3900, -2900, owner=1)
BURROW = U(902, "otrb", 4300, -2300, owner=1)


def world(foes, mine):
    base = [U(1, "htow", -5184, 2944), HALL, TOWER, BURROW]
    return build_world(base + foes + mine, 0, 700.0, 300, 300, 60, 80, levels={2: 4})


def seq(w):
    s = T.AttackSequence(dict(PROFILE), state="attack")
    s.hero_ids = {u.addr for u in w.my_heroes}
    return s


def under_tower(u):
    return math.hypot(u.x - TOWER.x, u.y - TOWER.y) <= G.stat("owtw", "range", 800) + T.TOWER_CLEAR_PAD


GRUNTS_OUT = [U(600 + i, "ogru", 3000, -1500 + 60 * i, owner=1) for i in range(3)]     # 塔射程外


class TowerGuard(unittest.TestCase):
    def test_units_under_the_tower_go_for_the_grunts_outside(self):
        near = [U(10 + i, "hfoo", 3700, -2700 + 40 * i) for i in range(4)]             # 箭塔 800 以内
        far = [U(20 + i, "hrif", 2800, -1800 + 40 * i) for i in range(3)]
        w = world(GRUNTS_OUT, near + far)
        self.assertTrue(all(under_tower(u) for u in near))
        out = seq(w)._tower_guard(w, [T.Intent("attack_move", near + far, HALL.x, HALL.y, why="打分矿")])
        hit = [i for i in out if i.why.startswith("先打兵")]
        self.assertEqual(len(hit), 1, [(i.kind, i.why) for i in out])
        self.assertEqual({u.addr for u in hit[0].units}, {10, 11, 12, 13})
        self.assertEqual((hit[0].kind, hit[0].target.type), ("attack", "ogru"))
        rest = [i for i in out if i.why == "打分矿"]
        self.assertEqual({u.addr for u in rest[0].units}, {20, 21, 22})              # 不在塔下的照原令

    def test_attacking_the_hall_while_grunts_are_around_switches_to_grunts(self):
        army = [U(10 + i, "hfoo", 3300, -2000 + 40 * i) for i in range(5)] + [U(2, "Hamg", 3300, -2100)]
        w = world(GRUNTS_OUT, army)
        out = seq(w)._tower_guard(w, [T.Intent("attack", army, HALL.x, HALL.y, HALL, why="打分矿")])
        hit = [i for i in out if i.why.startswith("先打兵")]
        self.assertEqual({u.addr for u in hit[0].units}, {10, 11, 12, 13, 14})
        self.assertEqual([u.addr for i in out if i.why == "打分矿" for u in i.units], [2])   # 英雄不在这条规则里

    def test_no_enemy_units_leaves_tower_focus_alone(self):
        near = [U(10 + i, "hfoo", 3700, -2700 + 40 * i) for i in range(4)]
        w = world([], near)
        its = [T.Intent("attack", near, TOWER.x, TOWER.y, TOWER, why="敌兵清空，集火攻击型建筑")]
        self.assertIs(seq(w)._tower_guard(w, its), its)

    def test_grunts_hiding_under_the_tower_and_we_are_few_step_out(self):
        hiding = [U(600 + i, "ogru", 3950, -2850 + 40 * i, owner=1) for i in range(3)]
        near = [U(10 + i, "hfoo", 3700, -2700 + 40 * i) for i in range(3)]
        w = world(hiding, near)
        out = seq(w)._tower_guard(w, [T.Intent("attack_move", near, HALL.x, HALL.y, why="打分矿")])
        self.assertFalse([i for i in out if i.kind in ("attack", "attack_move")], [(i.kind, i.why) for i in out])
        for i in out:
            reach = G.stat("owtw", "range", 800) + T.TOWER_CLEAR_PAD
            self.assertGreater(math.hypot(i.x - TOWER.x, i.y - TOWER.y), reach)

    def test_grunts_under_the_tower_but_we_are_many_go_in_together(self):
        hiding = [U(600 + i, "ogru", 3950, -2850 + 40 * i, owner=1) for i in range(3)]
        near = [U(10 + i, "hfoo", 3700, -2700 + 40 * i) for i in range(T.TOWER_GUARD_GROUP)]
        w = world(hiding, near)
        out = seq(w)._tower_guard(w, [T.Intent("attack_move", near, HALL.x, HALL.y, why="打分矿")])
        hit = [i for i in out if i.why.startswith("先打兵")]
        self.assertEqual(len(hit[0].units), T.TOWER_GUARD_GROUP)

    def test_force_attack_and_moves_untouched(self):
        near = [U(10, "hfoo", 3700, -2700)]
        w = world(GRUNTS_OUT, near)
        its = [T.Intent("force_attack", near, 0, 0, near[0], why="强制攻击"), T.Intent("move", near, 0, 0, why="回家")]
        self.assertIs(seq(w)._tower_guard(w, its), its)


if __name__ == "__main__":
    unittest.main()

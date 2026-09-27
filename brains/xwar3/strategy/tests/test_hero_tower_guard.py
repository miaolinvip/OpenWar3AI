# -*- coding: utf-8 -*-
"""英雄不进能打人的敌方建筑的射程（tactics.hero_tower_guard，runtime 下令前统一过一遍）。

09-22 大法师再次被敌方防御塔打死，同类问题反复出现（1-333 22 分）。
那局大法师两次都死在兽族地洞（otrb，射程 700）里：_tower_guard 只改非英雄，_towers_first 不认地洞、敌兵还活着就跳过。
22 分那次是「支援交战·集火残血核心 先知」—— 先知往自家基地跑，大法师点名追进地洞射程站了 7 秒。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T      # noqa: E402
from test_tactics import U, world   # noqa: E402

BURROW_X = 4000.0
REACH = 700.0 + T.TOWER_CLEAR_PAD     # otrb 射程 700


def scene(hero_x, far_seer_x):
    home = U(1, "htow")
    hero = U(2, "Hamg", 0, hero_x, 0)
    foot = U(3, "hfoo", 0, hero_x - 50, 80)
    burrow = U(100, "otrb", 1, BURROW_X, 0)
    seer = U(101, "Ofar", 1, far_seer_x, 0, hp=200, hp_max=800)
    grunt = U(102, "ogru", 1, 2600, 0)
    return world([home, hero, foot, burrow, seer, grunt]), hero, foot, seer, grunt


class HeroTowerGuard(unittest.TestCase):
    def test_hero_chasing_a_target_under_the_burrow_walks_to_the_edge_instead(self):
        w, hero, foot, seer, _ = scene(2800, 3500)
        its = [T.Intent("attack", [hero, foot], seer.x, seer.y, seer, why="支援交战·集火残血核心")]
        counts = {}
        out = T.hero_tower_guard(w, its, counts)
        hero_its = [it for it in out if hero in it.units]
        self.assertEqual(len(hero_its), 1)
        it = hero_its[0]
        self.assertEqual(it.kind, "move")
        self.assertGreaterEqual(math.hypot(it.x - BURROW_X, it.y), REACH, "落点在地洞射程外")
        self.assertLess(it.x, BURROW_X, "在英雄这一侧，不绕到地洞后面")
        self.assertEqual(it.control, "hold", "补攻击令（55）不能再把它拽回去")
        self.assertIn("otrb", it.why)
        self.assertEqual(counts.get("英雄不进塔的射程·目标在塔下"), 1)
        # 步兵照旧（兵打不打塔下的目标归 _tower_guard 管）
        self.assertTrue(any(it.kind == "attack" and foot in it.units and hero not in it.units for it in out))

    def test_hero_already_inside_does_not_attack_move_there(self):
        w, hero, foot, _, grunt = scene(3500, 3600)
        its = [T.Intent("attack_move", [hero], 3300, 0, why="打分矿·英雄跟在主力后面")]
        out = T.hero_tower_guard(w, its, {})
        self.assertEqual(out[0].kind, "move")
        self.assertGreaterEqual(math.hypot(out[0].x - BURROW_X, out[0].y), REACH)

    def test_hero_inside_moving_out_is_left_alone(self):
        w, hero, *_ = scene(3500, 3600)
        its = [T.Intent("move", [hero], 1000, 0, why="回家路上")]
        self.assertIs(T.hero_tower_guard(w, its, {})[0], its[0])

    def test_target_outside_the_burrow_is_untouched(self):
        w, hero, foot, _, grunt = scene(2000, 3500)
        its = [T.Intent("attack", [hero, foot], grunt.x, grunt.y, grunt, why="支援交战·就近接战")]
        self.assertEqual(T.hero_tower_guard(w, its, {}), its)

    def test_skirmish_control_is_kept(self):
        w, hero, foot, seer, _ = scene(2800, 3500)
        its = [T.Intent("attack", [hero], seer.x, seer.y, seer, why="支援交战", control="skirmish")]
        self.assertEqual(T.hero_tower_guard(w, its, {})[0].control, "skirmish")

    def test_exit_point_is_outside_every_nearby_tower(self):
        w = world([U(1, "htow"), U(2, "Hamg", 0, 3000, 0), U(100, "otrb", 1, 4000, 0), U(103, "owtw", 1, 3300, -700)])
        armed = [b for b in w.enemy_buildings if b.type in T.ARMED_BUILDINGS]
        ex, ey = T.tower_exit(armed[0], 3000, 0, armed)
        for b in armed:
            self.assertGreater(math.hypot(ex - b.x, ey - b.y), T.armed_reach(b), b.type)


class TowersFirstKnowsBurrows(unittest.TestCase):
    def test_armed_buildings_include_the_orc_burrow(self):
        self.assertIn("otrb", T.ARMED_BUILDINGS)


if __name__ == "__main__":
    unittest.main()

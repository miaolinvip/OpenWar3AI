# -*- coding: utf-8 -*-
"""伐木场贴着树林盖（act.tree_edge_spots、runtime._building_spots 的伐木场分支、runtime._chop_anchor）。

09-22 伐木场尽量建在离树木最近的位置。
最近 20 局（1 号实例）伐木场全盖在主基地东南 (-4448,2656)/(-4576,2720)：离最近的树 805~864，600 以内一棵树都没有。
"""
import json
import math
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

TERRAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "war3-analytics", "w3data", "terrain_EchoIsles.json")
HALL = SimpleNamespace(x=-5184.0, y=2944.0)


def tree(x, y, hp=50.0):
    return dict(addr=int(abs(x) * 7 + abs(y)), type="LTlt", x=float(x), y=float(y), hp=hp)


def near_tree(p, trees):
    return min(math.hypot(t["x"] - p[0], t["y"] - p[1]) for t in trees if t["hp"] > 0)


def overlaps(p, trees, code="hlum"):
    half, th = A.footprint(code) / 2.0, A.TREE_FOOT / 2.0
    return any(abs(t["x"] - p[0]) < half + th and abs(t["y"] - p[1]) < half + th for t in trees if t["hp"] > 0)


class EdgeSpots(unittest.TestCase):
    def test_prefers_a_forest_over_a_closer_lone_tree(self):
        hall = SimpleNamespace(x=0.0, y=0.0)
        forest = [tree(-832 - 64 * i, y) for i in range(3) for y in range(-640, 641, 64)]
        lone = [tree(448, 0)]
        spots = A.tree_edge_spots("hlum", hall, forest + lone)
        self.assertTrue(spots)
        self.assertLess(spots[0][0], 0)                                    # 林子那一边
        self.assertLessEqual(near_tree(spots[0], forest), 300)
        self.assertFalse(any(overlaps(p, forest + lone) for p in spots))   # 没有一个点压着树

    def test_dead_trees_and_far_trees_ignored(self):
        hall = SimpleNamespace(x=0.0, y=0.0)
        self.assertEqual(A.tree_edge_spots("hlum", hall, [tree(-600, 0, hp=0.0), tree(3000, 0)]), [])

    @unittest.skipUnless(os.path.exists(TERRAIN), "没有 Echo Isles 地形表")
    def test_echo_isles_west_spawn_gets_next_to_the_trees(self):
        with open(TERRAIN, encoding="utf-8") as fh:
            trees = [tree(x, y) for _t, x, y in json.load(fh)["trees"] if math.hypot(x - HALL.x, y - HALL.y) <= 2500]
        spots = A.tree_edge_spots("hlum", HALL, trees)
        self.assertTrue(spots)
        self.assertLessEqual(near_tree(spots[0], trees), 300)              # 原来 805~864
        self.assertLessEqual(math.hypot(spots[0][0] - HALL.x, spots[0][1] - HALL.y), A.LUMBER_TREE_R)


class RuntimeUsesThem(unittest.TestCase):
    def brain_world(self, trees):
        d = tempfile.mkdtemp()
        b = Brain(FakeCmd(), None, d)
        units = [U(0x100, "htow", HALL.x, HALL.y), U(0x600, "ngol", -5632, 3584, owner=15)]
        units += [U(0x300 + i, "hpea", -5000, 2800) for i in range(6)]
        w = build_world(units, 0, 300.0, 900, 900, 20, 40)
        b.trees, b.trees_at = trees, w.t                                   # 不去真扫树
        return b, w

    @unittest.skipUnless(os.path.exists(TERRAIN), "没有 Echo Isles 地形表")
    def test_first_candidate_is_by_the_trees(self):
        with open(TERRAIN, encoding="utf-8") as fh:
            trees = [tree(x, y) for _t, x, y in json.load(fh)["trees"] if math.hypot(x - HALL.x, y - HALL.y) <= 2500]
        b, w = self.brain_world(trees)
        cands = b._building_spots(w, "hlum", "base", exits=False)
        self.assertTrue(cands)
        self.assertLessEqual(near_tree(cands[0], trees), 300)
        self.assertEqual(b.counts.get("落点·伐木场贴树林·有候选"), 1)

    def test_farm_placement_unchanged(self):
        b, w = self.brain_world([tree(-5632, 2240)])
        b._building_spots(w, "hhou", "base", exits=False)
        self.assertFalse([k for k in b.counts if k.startswith("落点·伐木场")])

    def test_chop_near_the_finished_mill(self):
        b, w = self.brain_world([tree(-5632, 2240), tree(-4480, 3520)])
        mill = U(0x200, "hlum", -4700, 3300)
        w2 = build_world(list(w.units) + [mill], 0, w.t, 900, 900, 20, 40)
        main = w2.main_town()
        self.assertIs(b._chop_anchor(w2, main), next(x for x in w2.my_buildings if x.type == "hlum"))
        peon = next(u for u in w2.my_workers)
        self.assertEqual(b._pick_tree(b._chop_anchor(w2, main), peon)["x"], -4480)
        self.assertIs(b._chop_anchor(w, w.main_town()), w.main_town())    # 没有伐木场：照旧按主基地


if __name__ == "__main__":
    unittest.main()

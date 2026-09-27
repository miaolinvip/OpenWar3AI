# -*- coding: utf-8 -*-
"""openwar3.pathing：在合成地图上钉住 A* 的几件事 —— 绕墙、走不通、树只挡一格、建筑按占地挡、斜穿夹角不许、路径拐点。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openwar3 import pathing  # noqa: E402

CELL = 128


def mapinfo(rows):
    """rows：字符串列表，第 0 行在最下面（y 小）；'#' 不能走。"""
    cols = len(rows[0])
    cells = bytes(1 if ch == "#" else 0 for row in rows for ch in row)
    return SimpleNamespace(cols=cols, rows=len(rows), cell=CELL, min_x=0.0, min_y=0.0, cells=cells)


def at(c, r):
    return (c + 0.5) * CELL, (r + 0.5) * CELL


class AStar(unittest.TestCase):
    def test_straight_and_around_wall(self):
        g = pathing.Grid.from_map(mapinfo(["......", "......", "......"]))
        self.assertAlmostEqual(g.distance(at(0, 1), at(5, 1)), 5 * CELL)
        w = pathing.Grid.from_map(mapinfo(["......", "..#...", "..#...", "..#..."]))   # 墙挡住下面三行
        d = w.distance(at(0, 1), at(5, 1))
        self.assertGreater(d, 5 * CELL)                                   # 要从最上面一行绕
        pts = w.path(at(0, 1), at(5, 1))
        self.assertTrue(all(w.walkable_cell(*w.cell_of(x, y)) for x, y in pts))
        self.assertEqual(pts[-1], at(5, 1))

    def test_unreachable_island(self):
        g = pathing.Grid.from_map(mapinfo(["..#..", "..#..", "..#.."]))
        self.assertFalse(g.reachable(at(0, 0), at(4, 0)))
        self.assertIsNone(g.distance(at(0, 0), at(4, 0)))

    def test_no_corner_cutting(self):
        g = pathing.Grid.from_map(mapinfo(["#.", ".#"]))                 # 空格 (1,0) 和 (0,1) 只能斜着穿两个障碍之间：不许
        self.assertFalse(g.reachable(at(1, 0), at(0, 1)))

    def test_tree_blocks_one_cell_building_blocks_footprint(self):
        tree = SimpleNamespace(x=2.5 * CELL, y=1.5 * CELL, life=50.0)
        g = pathing.Grid.from_map(mapinfo(["....."] * 3), trees=[tree])
        self.assertEqual(sum(g.blocked), 1)
        hall = SimpleNamespace(x=4 * CELL, y=4 * CELL, type="htow")
        g2 = pathing.Grid.from_map(mapinfo(["........"] * 8), buildings=[hall], building_radius=lambda b: 256.0)
        self.assertEqual(sum(g2.blocked), 16)                            # 512 宽 = 4x4 格
        dead = SimpleNamespace(x=2.5 * CELL, y=1.5 * CELL, life=0.0)
        self.assertEqual(sum(pathing.Grid.from_map(mapinfo(["....."] * 3), trees=[dead]).blocked), 0)

    def test_block_area_on_copy_detours_without_touching_original(self):
        g = pathing.Grid.from_map(mapinfo(["......."] * 5))
        direct = g.distance(at(0, 2), at(6, 2))
        h = g.copy()
        h.block_area(*at(3, 2), 130)                                       # "塔"压住中间 3x3
        self.assertGreater(h.distance(at(0, 2), at(6, 2)), direct)        # 绕开
        self.assertEqual(g.distance(at(0, 2), at(6, 2)), direct)          # 原网格没被改
        self.assertEqual(sum(h.blocked), 9)

    def test_start_inside_building_snaps_to_open_cell(self):
        g = pathing.Grid.from_map(mapinfo(["....", ".#..", "...."]))
        self.assertIsNotNone(g.distance(at(1, 1), at(3, 2)))              # 起点压在障碍上：挪到最近能走的格


if __name__ == "__main__":
    unittest.main()

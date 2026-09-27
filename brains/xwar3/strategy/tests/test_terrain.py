# -*- coding: utf-8 -*-
"""打野集合点按地形挑（brain/terrain.py + tactics._stage_for）。

09-21 对局画面：左下角鱼人营地，我方挤在水和树林之间 —— 集合点周围要留足移动空间，让残血单位有地方撤退。
旧算法一律「营地朝家的方向退 1000」，那个点夹在一条两三格宽的缝里；营地东边是一大片空地。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import tactics as T  # noqa: E402
from brain import terrain as TR  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402

STEP = 128.0
N = 40                           # 40 x 40 格，中心 (0,0)


def doc():
    """营地在 (0,0)。北边（朝家）x < 0 是水，x 在 0~256 是一条缝，x 在 256~512 是树林，再往东空地。南边、东边全空。"""
    x0 = y0 = -(N / 2) * STEP + STEP / 2
    rows, trees = [], []
    for j in range(N):
        y = y0 + j * STEP
        row = ""
        for i in range(N):
            x = x0 + i * STEP
            row += "1" if (x < -64 and y > 300) or abs(x) > 2400 or abs(y) > 2400 else "0"
            if y > 300 and 256 <= x <= 512:
                trees.append(["LTlt", x, y])
        rows.append(row)
    return {"x0": x0, "y0": y0, "step": STEP, "n": N, "blocked_rows": rows, "trees": trees}


class StagePoint(unittest.TestCase):
    def setUp(self):
        self.t = TR.Terrain(doc())
        self.home = (0.0, 5000.0)          # 家在正北

    def test_old_point_was_the_narrow_gap(self):
        """前提：旧算法（朝家退 1000）的点 = (0, 1000)，落在水和树林之间的缝里（算出来周围空地 58.6%）。"""
        self.assertTrue(self.t.walkable(0, 1000))
        self.assertLess(self.t.openness(0, 1000), 0.6)

    def test_picks_an_open_side_with_a_way_back(self):
        x, y, why = self.t.stage_point(0, 0, 1000, self.home)
        self.assertGreater(self.t.openness(x, y), 0.9, why)
        in_gap = y > 300 and -64 <= x <= 512
        self.assertFalse(in_gap, f"不能再选北边那条缝：({x:.0f},{y:.0f})")

    def test_route_from_the_army_does_not_graze_the_camp(self):
        """部队从正东来：南边的点要绕过营地，直线离营地中心太近 —— 不选。"""
        x, y, _ = self.t.stage_point(0, 0, 1000, self.home, origin=(3000.0, 0.0), wake_r=650.0)
        self.assertGreaterEqual(T.segment_distance(0, 0, 3000, 0, x, y)[0], 650.0)

    def test_attack_sequence_uses_it(self):
        seq = T.AttackSequence(dict(PROFILE))
        seq.terrain = self.t
        seq.home_xy = self.home
        x, y = seq._stage_for({"x": 0.0, "y": 0.0}, (0.0, 3000.0))
        self.assertGreater(self.t.openness(x, y), 0.9)
        self.assertEqual(seq.counts.get("打野·集合点按地形挑"), 1)
        seq.terrain = None                                       # 没有地形表：照旧朝家
        self.assertEqual(tuple(round(v) for v in seq._stage_for({"x": 0.0, "y": 0.0}, (0.0, 3000.0))), (0, 1000))

    def test_real_map_table_loads(self):
        t = TR.load()
        if t is None:
            self.skipTest("没有 war3-analytics/w3data/terrain_EchoIsles.json")
        self.assertFalse(t.walkable(-7800, -7800))              # 海里
        self.assertTrue(t.walkable(-4800, 2600))                # 我方主基地旁
        # 09-21 那个鱼人营地：旧点空地 55%，新点要明显更空
        x, y, _ = t.stage_point(-6454, -3565, 1000, (-5184, 2944))
        self.assertGreater(t.openness(x, y), t.openness(-6262, -2583) + 0.1)


def band_doc(gap_y=1800.0):
    """全是空地；一条南北向的树林带，树按地图上的真实摆法（LTlt 按 64 错位：三列 x=448/512/576，
    每列隔 256 一棵、相邻列错开 128；占地 ±64 -> 树林带 x 384~640，其中 448~576 两列重叠、实心）。
    gap_y 以北没有树（绕过去的路）。营地在 (0,0)，家在正东。"""
    d = doc()
    d["blocked_rows"] = ["0" * N if abs(d["y0"] + j * STEP) <= 2400 else "1" * N for j in range(N)]
    d["trees"] = [["LTlt", 448.0 + 64 * c, -2400.0 + 256 * r + (128 if c % 2 else 0)]
                  for c in range(3) for r in range(20) if -2400.0 + 256 * r + (128 if c % 2 else 0) < gap_y]
    return d


class TreeBand(unittest.TestCase):
    """★★ 09-22 对局画面：同一个海龟鱼人营地，一队人被树林隔成两拨 —— 集结经常被树隔开，要从根上解决。
    树（LTlt）的通行占地 128×128、按 64 错位摆，大多压在两个 128 格中间；原来每棵只记一格，树林带成了筛子，
    集合点挑进了树林带中间（实测 (-5530,-3182)），各人走到树林自己这一侧就停下。"""

    def setUp(self):
        self.t = TR.Terrain(band_doc())

    def test_staggered_trees_make_a_solid_wall(self):
        for y in range(-2000, 1700, 64):
            self.assertFalse(self.t.walkable(480, y), f"树林带中间 (480,{y}) 不能走")
        # 从西边到东边：只能绕到北边的缺口（1800 以北）过去，路程远大于直线 800
        far = self.t.path_dist(0, 0, 8000).get(self.t._cell(800, 0))
        self.assertIsNotNone(far)
        self.assertGreater(far, 3000)

    def test_openness_counts_only_what_you_can_walk_to(self):
        """贴着树林西边的点：树林东边的空地在圆里但走不过去，不算。期望按格子算出来（不是猜）：
        实心的是 448~576（两列树重叠），它西边能走的格子都连着 (300,0)，东边的都走不到。"""
        disk = self.t._disk(300, 0, TR.OPEN_R)
        free = sum(1 for c in disk if self.t._ok(*c)) / len(disk)
        west = sum(1 for (i, j) in disk if self.t._ok(i, j) and self.t._xy(i, j)[0] < 448) / len(disk)
        self.assertLess(west, free)                                       # 前提：东边确实有圆里的空地
        self.assertAlmostEqual(self.t.openness(300, 0), west, places=6)

    def test_stage_point_stays_on_the_camp_side(self):
        """家在正东：朝家退 1000 的点 (1000,0) 本身是空地，但和营地隔着树林（要绕到北边缺口）—— 不选。"""
        self.assertTrue(self.t.clear(1000, 0))
        self.assertGreater(self.t.path_dist(0, 0, 8000)[self.t._cell(1000, 0)], TR.STAGE_DETOUR * 1000)
        x, y, why = self.t.stage_point(0, 0, 1000, (5000.0, 0.0))
        self.assertLess(x, 384, f"集合点跑到树林对面去了：({x:.0f},{y:.0f}) {why}")
        self.assertTrue(self.t.clear(x, y))

    def test_snap_moves_a_point_out_of_the_trees_to_the_anchor_side(self):
        self.assertFalse(self.t.clear(480, 0))
        west = self.t.snap(480, 0, anchor=(0.0, 0.0))
        east = self.t.snap(480, 0, anchor=(1500.0, 0.0))
        self.assertLess(west[0], 384)
        self.assertGreater(east[0], 640)
        self.assertIsNotNone(self.t.snap(480, 0), "不给锚点也要挪到一块空地")
        self.assertTrue(self.t.clear(*west) and self.t.clear(*east))
        self.assertEqual(self.t.snap(-1000, 0), (-1000, 0), "本来就站得下的点原样返回")

    def test_backline_spread_is_moved_to_the_camp_side(self):
        seq = T.AttackSequence(dict(PROFILE))
        seq.terrain = self.t
        x, y = seq._ground(480, 0, (0.0, 0.0))
        self.assertLess(x, 384)
        self.assertEqual(seq.counts.get("打野·站位点挪到营地这一侧的空地"), 1)
        seq.terrain = None
        self.assertEqual(seq._ground(480, 0, (0.0, 0.0)), (480, 0))

    def test_real_map_turtle_camp_stage_is_on_open_ground_next_to_the_camp(self):
        t = TR.load()
        if t is None:
            self.skipTest("没有 war3-analytics/w3data/terrain_EchoIsles.json")
        self.assertFalse(t.walkable(-5530, -3182), "旧集合点在树林带里")
        x, y, why = t.stage_point(-6454, -3565, T.CREEP_STAGE, (-5184.0, 2944.0), (-5184.0, 2944.0), T.CREEP_WAKE_R)
        self.assertTrue(t.clear(x, y), why)
        pd = t.path_dist(-6454, -3565, 3000).get(t._cell(x, y))
        self.assertLessEqual(pd, TR.STAGE_DETOUR * math.hypot(x + 6454, y + 3565), why)


class OrdersOnGround(unittest.TestCase):
    """runtime 下令前统一过一遍：对点的命令落在树里就挪到最近的空地；对单位的攻击不动。"""

    def test_point_in_trees_is_moved_and_unit_targets_are_left_alone(self):
        import tempfile
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from brain.runtime import Brain                   # noqa: E402
        from test_runtime_smoke import FakeCmd            # noqa: E402
        from test_tactics import U                        # noqa: E402
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.attack.terrain = TR.Terrain(band_doc())
            us = [U(10, "hfoo", x=0, y=0)]
            moved = b._on_ground(T.Intent("move", us, 480, 0, why="去集合点"))
            self.assertTrue(b.attack.terrain.clear(moved.x, moved.y))
            self.assertEqual(b.counts["意图·目标点挪到空地"], 1)
            ok = T.Intent("attack_move", us, -1000, 0)
            self.assertIs(b._on_ground(ok), ok)
            hit = T.Intent("attack", us, 480, 0, target=U(99, "nmrr", x=480, y=0))
            self.assertIs(b._on_ground(hit), hit)
            borrowed = T.Intent("move", us, 480, 0, control="skirmish")
            self.assertIs(b._on_ground(borrowed), borrowed)
            self.assertEqual(b.counts["意图·目标点挪到空地"], 1)


class LiveMap(unittest.TestCase):
    """09-23 P3：地形表从运行时的 W3P 地图块现场生成（不限地图），格式和离线 json 一样。"""

    def fake_world(self, epoch=1):
        from types import SimpleNamespace
        # 4 列 x 2 行、128 一格、左下角 (0,0)：第 0 行第 2 格是水（bit0），第 1 行全能走
        cells = bytes([0, 0, 1, 0, 0, 2, 0, 0])
        mp = SimpleNamespace(epoch=epoch, cell=128, cols=4, rows=2, min_x=0.0, min_y=0.0, cells=cells)
        tree = SimpleNamespace(type="LTlt", x=448.0, y=192.0, life=50.0, alive=True)
        calls = []
        return SimpleNamespace(pid=77, map=lambda: mp, trees=lambda: (calls.append(1), [tree])[1], calls=calls)

    def test_doc_is_square_and_padded(self):
        w = self.fake_world()
        doc = TR.doc_from_map(w.map(), w.trees())
        self.assertEqual((doc["n"], doc["step"], doc["x0"], doc["y0"]), (4, 128.0, 64.0, 64.0))
        self.assertEqual(doc["blocked_rows"], ["0010", "0000", "1111", "1111"])     # 补的两行在地图外：不能走
        self.assertEqual(doc["trees"], [["LTlt", 448, 192]])

    def test_from_world_builds_terrain_once_per_epoch(self):
        w = self.fake_world(epoch=12345)
        tr = TR.from_world(w)
        self.assertTrue(tr.walkable(64, 64))
        self.assertFalse(tr.walkable(320, 64))           # 水
        self.assertFalse(tr.walkable(448, 192))          # 树
        self.assertIs(TR.from_world(w), tr)
        self.assertEqual(len(w.calls), 1)                 # 同一局只算一次
        self.assertIsNone(TR.from_world(type("W", (), {"map": lambda self: None})()))   # 还没算好


if __name__ == "__main__":
    unittest.main()

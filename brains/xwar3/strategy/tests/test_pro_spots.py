# -*- coding: utf-8 -*-
"""高手录像落点（brain/data/pro_spots.json，tools/make_pro_spots.py 从 Yumiko 的两局 Echo Isles 录像抽）。

09-18 照高手录像学开局的建筑顺序和落点。早先的建筑位置是按这份录像摆的，后来又退化成了散乱摆放。
新大脑的落点原来 = 老 AI 记忆点（散点）+ 大厅外的环，而且候选检查比贴边多要 64 —— 高手贴着大厅盖的农场（+320,+64）进不了候选。
5 号 08/09 局实测：兵营 (0,-832)、农场 960~1580 往东南散；同一个出生点 Yumiko：兵营 (+576,+192)、农场 (+320,+64) 贴着大厅。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import runtime as RT  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

WEST, EAST = (-5184, 2944), (4672, 2944)


def world(hall_xy, extra=()):
    hx, hy = hall_xy
    hall = U(0x100, "htow", hx, hy)
    mine = U(0x600, "ngol", hx - 448 if hx < 0 else hx + 448, hy + 640, owner=15)     # 主矿在大厅外侧上方（录像里西侧 (-5632,3584)）
    peas = [U(0x300 + i, "hpea", hx + 200, hy - 200) for i in range(6)]
    return build_world([hall, mine] + peas + list(extra), 0, 400.0, 900, 900, 20, 40)


def brain(d):
    return Brain(FakeCmd(), None, d)


class Data(unittest.TestCase):
    def test_both_spawns_have_the_human_base(self):
        sp = RT._load_json(RT.PRO_SPOTS, {})["spawns"]
        west, east = sp["-5184,2944"]["spots"], sp["4672,2944"]["spots"]
        self.assertEqual(west["hbar"][0], [-4608.0, 3136.0])        # TH000 0:03 兵营 (+576,+192)
        self.assertEqual(west["halt"][0], [-5408.0, 2400.0])        # 0:16 祭坛 (-224,-544)
        self.assertEqual(west["hhou"][0], [-4864.0, 3008.0])        # 1:31 农场 (+320,+64) —— 贴着大厅
        self.assertEqual(east["hbar"][0], [4096.0, 3200.0])         # LawLiet 那局东侧 0:02 兵营 (-576,+256)

    def test_far_farms_are_not_base_layout(self):
        # 两局都派一个农民去基地出口（~2000）和地图中间（~5000）盖视野农场：不收
        d = RT._load_json(RT.PRO_SPOTS, {})
        for sp in d["spawns"].values():
            hx, hy = (float(v) for v in next(k for k, v in d["spawns"].items() if v is sp).split(","))
            for pts in sp["spots"].values():
                for x, y in pts:
                    self.assertLessEqual(((x - hx) ** 2 + (y - hy) ** 2) ** 0.5, d["baseR"])
        self.assertTrue(any(s["code"] == "hhou" and s["dist"] > 1900 for s in d["skipped"]))


class Candidates(unittest.TestCase):
    def test_pro_farm_touching_the_hall_is_kept(self):
        w = world(WEST)
        hall = w.my_halls[0]
        ring = A.spot_candidates("hhou", hall, w.my_buildings, w.mines)
        self.assertNotIn((-4864.0, 3008.0), ring)                     # 环形候选要多空 64：贴着大厅的点进不来
        got = A.spot_candidates("hhou", hall, w.my_buildings, w.mines, pro=[(-4864, 3008), (-4864, 3136)])
        self.assertEqual(got[:2], [(-4864.0, 3008.0), (-4864.0, 3136.0)])

    def test_pro_spot_overlapping_a_building_is_skipped(self):
        w = world(WEST, [U(0x700, "hhou", -4864, 3008)])
        got = A.spot_candidates("hhou", w.my_halls[0], w.my_buildings, w.mines, pro=[(-4864, 3008), (-4864, 3136)])
        self.assertEqual(got[0], (-4864.0, 3136.0))                   # 上下贴边（128 = 64 + 64）不算重叠


class Runtime(unittest.TestCase):
    def test_west_barracks_goes_where_yumiko_put_it(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(WEST)
            b._execute(w, Want("building", "hbar", 1, 90, "base"))
            self.assertEqual([(x, y) for _u, _o, x, y in b.cmd.builds][:1], [(-4608.0, 3136.0)])
            self.assertEqual(b.counts["落点·高手·hbar"], 1)
            self.assertIn("高手落点", b.notes[-1][1])

    def test_east_uses_mirrored_west_spot_when_east_replay_has_none(self):
        # 东侧那局 Yumiko 把铁匠铺盖在分矿 -> 东侧没有主基地铁匠铺，用西侧 (-5120,2048) 镜像：x' = -512 - x = 4608
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(EAST)
            self.assertEqual(b._pro_spots(w.my_halls[0], "hbla")[:1], [(4608.0, 2048.0)])
            self.assertEqual(b._pro_spots(w.my_halls[0], "hbar")[0], (4096.0, 3200.0))      # 同侧的在镜像前面

    def test_no_mirrored_spots_when_this_side_has_its_own(self):
        # 第 17/18 局：西侧农场高手点用完后接着用了东侧镜像的 (-5056,2624)，把 Yumiko 留的通道堵上，大法师卡在家里
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(WEST)
            farms = b._pro_spots(w.my_halls[0], "hhou")
            self.assertNotIn((-5056.0, 2624.0), farms)
            self.assertEqual(farms[0], (-4864.0, 3008.0))
            self.assertEqual(len(farms), 6)                                                  # 只有 TH000 那局西侧的 6 个

    def test_other_maps_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world((1280, -6848))                                                         # LostTemple 的出生点
            self.assertEqual(b._pro_spots(w.my_halls[0], "hbar"), [])
            b.known_spots = {"1280,-6848|hbar": [[640, -6848]]}                              # 离大厅 640 > 256+160+64，过得了环形检查
            self.assertEqual(b._building_spots(w, "hbar")[0], (640.0, -6848.0))              # 没有高手点：记忆点照旧排最前

    def test_fallback_does_not_take_another_buildings_pro_spot(self):
        # 12 局（22:26:52）实测：伐木场的高手点开局被树挡住被退，回落到环上的 (-4960,2432) 压着宝物库的高手点 (-4928,2496)
        #   现场：1:03 时祭坛、第一个农场、兵营都已经在高手点上；同侧和镜像的伐木场高手点、环上最前面两个点都被引擎退了
        #   旧代码下一个就是 (-4960,2432)（先跑旧代码算出来的，一次性分析脚本 hlum_old.py）
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(WEST, [U(0x700, "halt", -5408, 2400), U(0x701, "hhou", -4864, 3008), U(0x702, "hbar", -4608, 3136)])
            b.bad_spots["hlum"] = {(-5792.0, 2592.0), (-5536.0, 2464.0), (-5184.0, 3520.0), (-5760.0, 2944.0)}
            first = b._building_spots(w, "hlum")[0]
            vx, vy = -4928.0, 2496.0
            gap = (A.footprint("hlum") + A.footprint("hvlt")) / 2.0
            self.assertFalse(abs(first[0] - vx) < gap and abs(first[1] - vy) < gap, first)
            self.assertNotEqual(first, (-4960.0, 2432.0))

    def test_only_same_race_spots_are_reserved(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(WEST)
            held = b._pro_reserved(w.my_halls[0], "hlum")
            self.assertTrue(held)
            self.assertEqual({g.type[0] for g in held}, {"h"})                              # 同一出生点存的暗夜布局不占人族的地方
            self.assertNotIn("hlum", {g.type for g in held})                                # 自己的高手点不预留给自己

    def test_old_memory_spots_go_after_the_ring_when_pro_exists(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            w = world(WEST)
            b.known_spots = {"-5184,2944|hhou": [[-3840, 2880]], "-5184,2944|hgra": [[-3392, 2624]]}
            spots = b._building_spots(w, "hhou", exits=False)                               # 只看排序（通路检查会截到前几个）
            self.assertEqual(spots[0], (-4864.0, 3008.0))
            ring_first = next(i for i, p in enumerate(spots) if p not in b.pro_last)
            self.assertGreater(spots.index((-3840.0, 2880.0)), ring_first)                  # 老 AI 的散点排到环后面
            self.assertEqual(b._building_spots(w, "hgra")[0], (-3392.0, 2624.0))             # 高手没盖过的建筑：记忆点照旧


if __name__ == "__main__":
    unittest.main()

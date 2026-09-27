# -*- coding: utf-8 -*-
"""建筑落点的通路检查（act.seals_exit / runtime._keep_exits）。

09-18 大法师出祭坛后多次被旧策略造的建筑堵在家里。只按建筑算，最近 6 局都没有把祭坛单独围死 ——
是祭坛挨着西边的树、再被农场/铁匠铺贴上去。所以树（大脑扫到的）和金矿一起算，放下候选点会让出兵建筑从出得去变成出不去就不用它。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HX, HY = -5184.0, 2944.0
AX, AY = -5408.0, 2400.0            # Yumiko 的祭坛高手点（西边挨着树）


def tree_wall():
    """祭坛西、南、北三面一排树（128 一棵，紧挨着），只留东面一个口。"""
    trees = []
    for k in range(-3, 4):
        trees.append((A.TREE_FOOT, AX - 160 - 64, AY + k * 128))          # 西
    for k in range(-2, 3):
        trees.append((A.TREE_FOOT, AX + k * 128, AY - 160 - 64))          # 南
        trees.append((A.TREE_FOOT, AX + k * 128, AY + 160 + 64))          # 北
    return trees


class SealsExit(unittest.TestCase):
    def test_closing_the_last_side_seals_the_altar(self):
        items = [(512, HX, HY), (320, AX, AY)] + tree_wall()
        doors = [("halt", 320, AX, AY)]
        # 东面：一座铁匠铺（320）正好堵在口上
        self.assertEqual(A.seals_exit(items, doors, (320, AX + 320 + 32, AY), HX, HY), ["halt"])
        # 放远一点、留出一条路：不堵
        self.assertEqual(A.seals_exit(items, doors, (320, AX + 320 + 160, AY + 400), HX, HY), [])

    def test_already_trapped_is_not_this_spots_fault(self):
        items = [(512, HX, HY), (320, AX, AY), (320, AX + 320 + 32, AY)] + tree_wall()   # 已经被围死
        doors = [("halt", 320, AX, AY)]
        self.assertEqual(A.seals_exit(items, doors, (128, HX + 900, HY), HX, HY), [])


class Pocket(unittest.TestCase):
    """09-18 第 20 局：大法师在兵营北边 (-4732,3408) 困了一分多钟。开局照 Yumiko 的点放了兵营、两个农场、祭坛，
    88 秒又放了夹在祭坛和大厅之间的农场 (-5248,2624) —— 大厅北面整片采矿区的最后一个口被封上。树用这局 brain_live 的真实数据。"""

    def test_farm_between_altar_and_hall_seals_the_mining_area(self):
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trees_echo_west_0918.json"), encoding="utf-8") as fh:
            trees = json.load(fh)["trees"]
        items = [(A.footprint("htow"), HX, HY), (A.footprint("ngol"), -5632, 3584)] + [(A.TREE_FOOT, x, y) for x, y in trees]
        items += [(A.footprint(c), x, y) for c, x, y in (("hbar", -4608, 3136), ("hhou", -4864, 3008), ("halt", AX, AY),
                                                          ("hhou", -4864, 3136))]
        doors = [("htow", A.footprint("htow"), HX, HY)]
        got = A.seals_exit(items, doors, (A.footprint("hhou"), -5248, 2624), HX, HY)
        self.assertTrue(any(s.startswith("隔出死角") for s in got), got)
        self.assertEqual(A.seals_exit(items, doors, (A.footprint("hhou"), -6336, 3008), HX, HY), [])     # 西边树林外的高手农场点照放


class KeepExits(unittest.TestCase):
    def test_runtime_skips_the_spot_that_would_trap_the_altar(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            hall = U(0x100, "htow", HX, HY)
            altar = U(0x101, "halt", AX, AY)
            mine = U(0x600, "ngol", -5632, 3584, owner=15)
            peas = [U(0x300 + i, "hpea", HX + 200, HY - 200) for i in range(6)]
            w = build_world([hall, altar, mine] + peas, 0, 400.0, 900, 900, 20, 40)
            b.trees = [dict(x=x, y=y, hp=50.0) for _s, x, y in tree_wall()]
            b.trees_at = w.t
            plug = (AX + 320 + 32, AY)
            got = b._keep_exits(w, "hbla", [plug, (HX + 700, HY + 300)], [])
            self.assertEqual(got, [(HX + 700, HY + 300)])
            self.assertEqual(b.counts["落点·会堵门·hbla"], 1)


if __name__ == "__main__":
    unittest.main()

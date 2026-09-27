# -*- coding: utf-8 -*-
"""数据多了以后（09-26 对局记录切到「所有实例」时浏览器卡死）：对局记录的汇总 / 分页 / 摘要存盘 / 进行中限频重读、
单局曲线抽样、决策明细增量读、磁盘占用分类。全部用临时目录里造的假数据，不碰真实数据。"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import disk as DK  # noqa: E402
from server import matches as MT  # noqa: E402

OLD = time.time() - 3 * 86400


def write_match(d: str, name: str, outcome: str | None, kernel: int = 3, finalized: bool = True, states: int = 5,
                mtime: float | None = OLD) -> str:
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    rows = [{"kind": "experiment", "data": {"brain": "war3-brain", "kernel": kernel, "kernelRev": f"r{kernel}"}},
            {"kind": "start", "data": {"game": 1}}]
    rows += [{"kind": "state", "wallElapsedSec": i, "gameElapsedSec": i, "data": {"race": "human", "ownStr": i, "halls": 1}}
             for i in range(states)]
    rows += [{"kind": "action", "data": {"text": "x"}}]
    if outcome:
        rows.append({"kind": "result", "wallElapsedSec": states, "data": {"outcome": outcome, "minutes": 10, "peakArmy": 20}})
    if finalized:
        rows.append({"kind": "finalized"})
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class MatchesVolumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = os.path.join(self.tmp.name, "bin")
        self.saved = (C.BIN, MT.INDEX_PATH)
        C.BIN = self.bin
        MT.INDEX_PATH = os.path.join(self.tmp.name, "cache", "match_index.json")
        MT._cache.clear()
        MT._index.update(loaded=False, saved=0.0, sig=None)
        # 实例 1：7 局（胜 4、负 2、卡住 1），实例 2：3 局（胜 1、负 2）
        for i, oc in enumerate(["victory"] * 4 + ["defeated"] * 2 + ["ended-stuck"]):
            write_match(os.path.join(self.bin, "inst1", "matches"), f"match-20260920-1000{i:02d}-{i}.jsonl", oc)
        for i, oc in enumerate(["victory", "defeated", "defeated"]):
            write_match(os.path.join(self.bin, "inst2", "matches"), f"match-20260921-1000{i:02d}-{i}.jsonl", oc, kernel=4)

    def tearDown(self):
        C.BIN, MT.INDEX_PATH = self.saved
        MT._cache.clear()
        MT._index.update(loaded=False, saved=0.0, sig=None)
        self.tmp.cleanup()

    def test_overview_stats_groups_and_paging(self):
        ov = MT.overview(scope="all", limit=0, kind="all", size=4)
        self.assertEqual((ov["scope"], ov["inst"]), ("all", None))           # 网页靠它认范围
        self.assertEqual((ov["scanned"], ov["total"], len(ov["rows"])), (10, 10, 4))
        st = ov["stats"]
        self.assertEqual((st["n"], st["done"], st["reliable"], st["wins"]), (10, 10, 9, 5))   # ended-stuck 不算可信
        self.assertEqual([g["inst"] for g in ov["byInst"]], [1, 2])
        self.assertEqual([g["kernel"] for g in ov["byKernel"]], [4, 3])
        self.assertEqual(ov["rows"][0]["inst"], 2)                                              # 新到旧：实例 2 的局更晚
        page3 = MT.overview(scope="all", limit=0, kind="all", size=4, offset=8)
        self.assertEqual(len(page3["rows"]), 2)
        # 点选实例只筛表格，不改「按实例」分组
        picked = MT.overview(scope="all", limit=0, kind="all", pick_inst=2)
        self.assertEqual((picked["total"], len(picked["byInst"])), (3, 2))
        # 搜索：网页把翻译过的结局名对上结局码发过来
        won = MT.overview(scope="all", limit=0, kind="all", q="胜利", outcomes=("victory",))
        self.assertEqual(won["total"], 5)
        self.assertEqual(MT.overview(scope="all", limit=3, kind="all")["scanned"], 3)
        one = MT.overview(scope="inst", inst=1, limit=0, kind="all")
        self.assertEqual((one["scope"], one["inst"], one["total"], one["byInst"]), ("inst", 1, 7, []))

    def test_index_survives_restart_without_reparsing(self):
        MT.list_all(0)
        MT.save_index(force=True)
        self.assertTrue(os.path.exists(MT.INDEX_PATH))
        MT._cache.clear()
        MT._index.update(loaded=False, saved=0.0, sig=None)
        real = MT._parse
        MT._parse = lambda *a: self.fail("有索引还重新解析了")
        try:
            self.assertEqual(len(MT.list_all(0)), 10)
        finally:
            MT._parse = real

    def test_index_not_rewritten_when_nothing_changed(self):
        MT.list_all(0)
        MT.save_index(force=True)
        first = os.path.getmtime(MT.INDEX_PATH)
        os.utime(MT.INDEX_PATH, (first - 100, first - 100))
        MT.save_index(force=True)
        self.assertEqual(os.path.getmtime(MT.INDEX_PATH), first - 100)

    def test_live_match_reparsed_at_most_every_few_seconds(self):
        d = os.path.join(self.bin, "inst1", "matches")
        p = write_match(d, "match-20260922-100000-9.jsonl", None, finalized=False, states=3, mtime=None)
        name = os.path.basename(p)
        self.assertEqual(MT._summary(p, name)["states"], 3)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "state", "data": {}}) + "\n")
        self.assertEqual(MT._summary(p, name)["states"], 3)          # 刚读过：先用上次的
        mt, sz, s, _ = MT._cache[p]
        MT._cache[p] = (mt, sz, s, time.time() - MT.LIVE_REPARSE_S - 1)
        self.assertEqual(MT._summary(p, name)["states"], 4)          # 过了间隔：重读

    def test_thin_keeps_ends(self):
        xs = list(range(6001))
        t = MT._thin(xs, 600)
        self.assertEqual((len(t), t[0], t[-1]), (600, 0, 6000))
        self.assertEqual(MT._thin([1, 2], 600), [1, 2])


class JsonlFromTest(unittest.TestCase):
    def test_incremental_and_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "decisions.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"a": 1}\n{"a": 2}\n{"a": 3')          # 最后一行还没写完
            rows, off = C.jsonl_from(p, 0)
            self.assertEqual([r["a"] for r in rows], [1, 2])
            with open(p, "a", encoding="utf-8") as fh:
                fh.write('}\n{"a": 4}\n')
            rows, off2 = C.jsonl_from(p, off)
            self.assertEqual([r["a"] for r in rows], [3, 4])
            self.assertEqual(C.jsonl_from(p, off2), ([], off2))
            with open(p, "w", encoding="utf-8") as fh:           # 换局清空：从头读
                fh.write('{"a": 5}\n')
            self.assertEqual([r["a"] for r in C.jsonl_from(p, off2)[0]], [5])

    def test_huge_line_does_not_stall(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x" * 100 + "\n")
            rows, off = C.jsonl_from(p, 0, max_bytes=10)
            self.assertEqual((rows, off), ([], 10))


class DiskUsageTest(unittest.TestCase):
    def test_categories_instances_and_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = (C.ROOT, C.BIN, C.FARSIGHT)
            C.ROOT, C.BIN, C.FARSIGHT = tmp, os.path.join(tmp, "bin"), os.path.join(tmp, "console")
            try:
                files = {
                    "bin/inst3/fights/fight-1.jsonl": 5000, "bin/inst3/matches/match-1.jsonl": 300,
                    "bin/inst3/decisions.jsonl": 200, "bin/inst3/brain.log": 100, "bin/inst3/mod_fight.out": 50,
                    "bin/inst3/spy.jsonl": 40, "bin/inst3/units.json": 10, "bin/render_hook.dll": 900,
                    "bin/render_hook.log.1": 70, "bin/farm/x.txt": 30, "bin/backup/y.py": 20,
                    "console/cache/match_index.json": 60,
                }
                for rel, size in files.items():
                    p = os.path.join(tmp, *rel.split("/"))
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb") as fh:
                        fh.write(b"x" * size)
                old = os.path.join(tmp, "bin", "inst3", "fights", "fight-1.jsonl")
                os.utime(old, (OLD, OLD))
                d = DK.scan()
            finally:
                C.ROOT, C.BIN, C.FARSIGHT = saved
        cats = {c["key"]: c["bytes"] for c in d["categories"]}
        self.assertEqual(cats, {"fights": 5000, "matches": 300, "decisions": 200, "logs": 220, "streams": 40,
                                "state": 10, "program": 900, "farm": 30, "backup": 20, "cache": 60})
        self.assertEqual(d["categories"][0]["key"], "fights")
        inst = d["instances"][0]
        self.assertEqual((inst["inst"], inst["bytes"], inst["cats"]["logs"]), (3, 5700, 150))
        self.assertEqual(d["total"]["bytes"], sum(files.values()))
        self.assertEqual(d["total"]["growth24h"], sum(files.values()) - 5000)   # 录制是三天前的
        self.assertEqual(d["largest"][0]["path"], "bin/inst3/fights/fight-1.jsonl")
        self.assertIsNotNone(d["drive"])

    def test_open_path_refuses_outside_repo(self):
        with self.assertRaises(ValueError):
            DK.open_path("../../Windows")


if __name__ == "__main__":
    unittest.main()

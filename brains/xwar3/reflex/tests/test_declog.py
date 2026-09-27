# -*- coding: utf-8 -*-
"""战斗决策明细（declog）：给人看的记录 —— 中文名 #编号、理由、依据、耗时。耗时的期望按函数手算。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import declog  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class DecisionLogTest(unittest.TestCase):
    def test_record_carries_names_ids_reason_inputs_and_timing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "decisions.jsonl")
            clk = Clock(100.050)                         # 拿到快照在 100.000，50ms 后写这一条
            log = declog.DecisionLog(p, "毫秒层", ids_fn=lambda: {0x14340A84: 49}, clock=clk, wall=lambda: 1790000000.0)
            knight = SimpleNamespace(addr=0x14340A84, type="hkni", hp=319, hp_max=835)
            log.log("孤兵回撤", knight, "身边 4 个敌人、没有自己人", point=(-4808, 1630),
                    inputs={"身边敌人": 4}, t0=100.000, cmd_s=0.012, age_ms=5.0, g=1220.3)
            r = declog.tail(p)[0]
            self.assertEqual(r["units"][0]["id"], 49)
            self.assertEqual(r["units"][0]["name"], declog.unit_name("hkni"))
            self.assertEqual(r["target"], {"x": -4808, "y": 1630})
            # decide = (100.050 - 100.000 - 0.012) × 1000 = 38；total = 快照旧的 5 + 从拿到快照到写 50 = 55
            self.assertEqual(r["timing"], {"snapAge": 5.0, "decide": 38.0, "cmd": 12.0, "total": 55.0})
            self.assertEqual((r["g"], r["inputs"]["身边敌人"], r["src"]), (1220.3, 4, "毫秒层"))

    def test_names_table_is_the_game_one(self):
        if declog.unit_name("hkni") == "hkni":
            self.skipTest("没有 build_intel4/names.json")
        self.assertEqual(declog.unit_name("hkni"), "骑士")
        self.assertEqual(declog.unit_name("Hblm"), "血魔法师")

    def test_tail_reads_only_the_end_and_skips_a_cut_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "decisions.jsonl")
            log = declog.DecisionLog(p, "大脑")
            u = SimpleNamespace(addr=1, type="hfoo", hp=1, hp_max=1)
            for i in range(50):
                log.log("出击", u, f"第 {i} 条")
            rows = declog.tail(p, limit=10, max_bytes=2000)
            self.assertEqual([r["why"] for r in rows][-1], "第 49 条")
            self.assertLessEqual(len(rows), 10)

    def test_a_failing_write_never_raises(self):
        log = declog.DecisionLog(os.path.join("Z:\\没有这个盘", "x.jsonl"), "大脑")
        log.log("出击", SimpleNamespace(addr=1, type="hfoo", hp=1, hp_max=1), "写不进去")
        self.assertEqual(log.failed, 1)


if __name__ == "__main__":
    unittest.main()

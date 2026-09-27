# -*- coding: utf-8 -*-
"""残血英雄缺血瓶：毫秒层写 item_reserve.json，大脑下单前留出这笔钱（runtime._item_reserve）。

09-22 英雄残血时要攒钱买血瓶补血。常备补货要买完剩 450，钱一直被造兵花掉，一天 183 次该喝时背包里没有。
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain.world import build_world  # noqa: E402
from test_revive import base_units, brain_with_dead  # noqa: E402


def reserve(d, gold, pid=4242, age=0.0):
    with open(os.path.join(d, "item_reserve.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "wall": time.time() - age, "gold": gold, "why": "Hamg 300/800 缺生命药水"}, fh)


class ItemReserve(unittest.TestCase):
    def test_reads_only_fresh_same_game(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            self.assertEqual(b._item_reserve(), 0, "没有文件")
            reserve(d, 150)
            self.assertEqual(b._item_reserve(), 150)
            reserve(d, 150, age=30.0)
            self.assertEqual(b._item_reserve(), 0, "过期：模块停了 / 英雄买上了")
            reserve(d, 150, pid=1)
            self.assertEqual(b._item_reserve(), 0, "上一局的")

    def test_reserved_gold_is_not_spent(self):
        # 复活 3 级大法师 255 金；手里 350。不留钱照常复活，留 150 之后只剩 200 —— 停下来攒
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            b.job_build(build_world(base_units(), 0, 400.0, 350, 500, 20, 40))
            self.assertTrue(b.revived)
        with tempfile.TemporaryDirectory() as d:
            reserve(d, 150)
            b = brain_with_dead(d)
            b.job_build(build_world(base_units(), 0, 400.0, 350, 500, 20, 40))
            self.assertEqual(b.revived, [])
            self.assertGreaterEqual(b.counts.get("攒钱·残血英雄买血瓶", 0), 1)


if __name__ == "__main__":
    unittest.main()

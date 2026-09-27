# -*- coding: utf-8 -*-
"""撤退令回执分类（mod_combat.retreat_receipt，心跳「撤退令回执」）。

09-22：本模块的撤退移动令有 48% 在 2 秒内没让单位离目的地近 150（英雄 55%），引擎都回了「接了」；
1-333 22 分大法师收到三次撤退令，原地站了 7 秒被地洞射死。战斗帧的订单字段对移动令几乎全是 0，
分不清被控住 / 被别的令顶掉 / 令还挂着但寻路没出来 —— 回执把这三种分开数。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402

TASK_TARGET = "task_target"
TASK_POINT = "task_point"


class RetreatReceipt(unittest.TestCase):
    def test_moving_needs_no_reads(self):
        self.assertEqual(C.retreat_receipt(C.RT_PROGRESS, None, None, (0, 0)), "在走")

    def test_order_still_our_move(self):
        o = {"order": C.ORDER_MOVE, "x": 1000.0, "y": 20.0}
        self.assertEqual(C.retreat_receipt(10, o, None, (1000, 0)), "挂着撤退令没走")
        self.assertEqual(C.retreat_receipt(10, o, None, (0, 0)), "挂着别处的移动令没走")

    def test_order_replaced(self):
        self.assertEqual(C.retreat_receipt(0, {"order": 0x000D000F, "x": 0, "y": 0}, None, (0, 0)), "订单被换成000D000F")

    def test_slot_empty_by_task(self):
        self.assertEqual(C.retreat_receipt(0, None, {"cls": TASK_TARGET}, (0, 0)), "撤退令没了·任务在打人")
        self.assertEqual(C.retreat_receipt(0, None, {"cls": TASK_POINT}, (0, 0)), "撤退令没了·任务是点")
        self.assertEqual(C.retreat_receipt(0, None, {"cls": None}, (0, 0)), "撤退令没了·任务读不出类名")
        self.assertEqual(C.retreat_receipt(0, None, None, (0, 0)), "撤退令没了·闲着")


if __name__ == "__main__":
    unittest.main()

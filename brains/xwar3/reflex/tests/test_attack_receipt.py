# -*- coding: utf-8 -*-
"""攻击令回执：补攻击令之后抽查当前订单的分类 —— mod_combat.attack_receipt。

09-16：攻击令 0x000D000F 从没实机验证过；current_order 能读订单号 + 目标句柄之后被动验证。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402

TGT = (0x3C5B, 0x3C65)


def order(oid, target=None):
    return {"order": oid, "target": target, "base_target": None, "x": 0.0, "y": 0.0}


class AttackReceipt(unittest.TestCase):
    def test_attack_on_requested_target(self):
        self.assertEqual(C.attack_receipt(order(C.ORDER_ATTACK, TGT), TGT, True), "攻击令·目标对上")

    def test_attack_on_other_target(self):
        self.assertEqual(C.attack_receipt(order(C.ORDER_ATTACK, (1, 2)), TGT, True), "攻击令·换了目标")

    def test_other_order_is_named(self):
        self.assertEqual(C.attack_receipt(order(0x000D0003, TGT), TGT, True), "订单000D0003")

    def test_empty_slot_split_by_task(self):
        self.assertEqual(C.attack_receipt(None, TGT, True), "槽空·有任务")
        self.assertEqual(C.attack_receipt(None, TGT, False), "槽空·闲着")
        self.assertEqual(C.attack_receipt(None, TGT, None), "单位没了")

    def test_empty_slot_task_on_requested_target(self):
        task = {"cls": "task_target", "id": 0x000D0174, "target": TGT}
        self.assertEqual(C.attack_receipt(None, TGT, True, task), "任务·目标对上")

    def test_empty_slot_task_on_other_target(self):
        task = {"cls": "task_target", "id": 0x000D0174, "target": (7, 8)}
        self.assertEqual(C.attack_receipt(None, list(TGT), True, task), "任务·别的目标")

    def test_empty_slot_point_task(self):
        task = {"cls": "task_point", "id": 0x000D016F, "target": None}
        self.assertEqual(C.attack_receipt(None, TGT, True, task), "任务·对点")

    def test_list_target_pair_matches(self):
        self.assertEqual(C.attack_receipt(order(C.ORDER_ATTACK, TGT), list(TGT), True), "攻击令·目标对上")


if __name__ == "__main__":
    unittest.main()

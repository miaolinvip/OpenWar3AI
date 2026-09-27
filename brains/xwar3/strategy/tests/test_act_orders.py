# -*- coding: utf-8 -*-
"""执行层：同一个单位同一条点令不重下（09-17 实机 12 分钟 1620 条攻击移动）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402


class FakeCmd:
    def __init__(self):
        self.moves = []

    def move_to(self, u, x, y, order):
        self.moves.append((u, x, y, order))
        return True


def unit(addr, x, y, order=0, has_task=False, typ="hfoo"):
    return SimpleNamespace(addr=addr, x=x, y=y, order=order, has_task=has_task, type=typ)


class TestRepeat(unittest.TestCase):
    def setUp(self):
        self.cmd = FakeCmd()
        self.act = A.Actuator(self.cmd, None)

    def test_busy_unit_not_reordered(self):
        u = unit(1, 0, 0, order=A.ORDER_ATTACK, has_task=True)
        self.assertEqual(self.act.attack_move([u], 1000, 0), 1)
        self.assertEqual(self.act.attack_move([u], 1300, 0), 0)          # 离上次的点 300（400 内）算同一个点
        self.assertEqual(self.act.counts.get("攻击移动·同令不重下"), 1)
        self.assertEqual(self.act.attack_move([u], 1500, 0), 1)          # 离上次的点 500 -> 换了点，重下
        self.assertEqual(self.act.move([u], 1500, 0), 1)                  # 换了令（smart），重下

    def test_idle_unit(self):
        u = unit(2, 0, 0)
        self.assertEqual(self.act.attack_move([u], 1000, 0), 1)
        self.assertEqual(self.act.attack_move([u], 1000, 0), 1)          # 闲着、离点 1000 -> 重下
        u.x = 900
        self.assertEqual(self.act.attack_move([u], 1000, 0), 0)          # 闲着但已经站在点上 -> 不下

    def test_expiry(self):
        u = unit(3, 0, 0, order=A.ORDER_ATTACK, has_task=True)
        self.act.attack_move([u], 1000, 0)
        old = A.REPEAT_S
        A.REPEAT_S = 0.0
        try:
            self.assertEqual(self.act.attack_move([u], 1000, 0), 1)
        finally:
            A.REPEAT_S = old


class TestFormation(unittest.TestCase):
    def setUp(self):
        self.cmd = FakeCmd()
        self.act = A.Actuator(self.cmd, None)

    def test_group_marches_in_parallel_columns(self):
        units = [unit(i, 0, i * 20) for i in range(1, 7)]
        self.assertEqual(self.act.move(units, 2000, 0), 6)
        ys = {round(y) for _addr, _x, y, _order in self.cmd.moves}
        xs = {round(x) for _addr, x, _y, _order in self.cmd.moves}
        self.assertGreaterEqual(len(ys), 4)       # 横向展开，不再六人争同一个点/同一条线
        self.assertGreaterEqual(len(xs), 2)       # 多排有纵深

    def test_melee_leads_and_hero_stays_in_back_row(self):
        units = [unit(1, 0, 0, typ="Hamg"), unit(2, 0, 20, typ="hrif"),
                 unit(3, 0, 40, typ="hfoo"), unit(4, 0, 60, typ="hfoo"),
                 unit(5, 0, 80, typ="hfoo"), unit(6, 0, 100, typ="hfoo")]
        self.act.move(units, 2000, 0)
        by_addr = {addr: (x, y) for addr, x, y, _order in self.cmd.moves}
        self.assertGreater(by_addr[3][0], by_addr[1][0])
        self.assertGreater(by_addr[4][0], by_addr[2][0])

    def test_single_unit_and_recovery_keep_exact_point(self):
        lone = unit(1, 0, 0)
        self.act.move([lone], 2000, 100)
        self.assertEqual(self.cmd.moves[-1][1:3], (2000.0, 100.0))
        self.cmd.moves.clear()
        group = [unit(i, 0, i * 20) for i in range(2, 6)]
        self.act.recover(group, 1200, 300)
        self.assertTrue(all((x, y) == (1200.0, 300.0) for _a, x, y, _o in self.cmd.moves))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""大脑点命令的下令路（act._issue_point）：09-23 P3 起只有一条 —— w3cmd.move_to（W3P point，运行时里走 JASS IssuePointOrder 那条路）。

09-22 毫秒层撤退令回执：动作 41 对正在攻击的单位常常顶不掉手上的攻击令，「内核」（JASS IssuePointOrder 那条路）英雄撤退 13 次 12 次走了；
1-402 两个骑士照打塔 7 秒 —— 移动意图下「移动令」+「内核」。W3P 的 point 命令就是这条路，动作 41 那条不再存在。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402


class Cmd:
    def __init__(self, ok=True):
        self.moves = []
        self.ok = ok

    def move_to(self, u, x, y, order):
        self.moves.append((u, order))
        return self.ok


def unit(addr, x=0.0, order=0):
    return SimpleNamespace(addr=addr, type="hfoo", x=x, y=0.0, order=order, has_task=False)


class PointPath(unittest.TestCase):
    def test_moves_are_move_orders_through_w3p(self):
        cmd = Cmd()
        act = A.Actuator(cmd)
        n = act.move([unit(1), unit(2, 40.0)], 2000.0, 0.0)
        self.assertEqual(n, 2)
        self.assertEqual(cmd.moves, [(1, A.ORDER_MOVE), (2, A.ORDER_MOVE)])
        self.assertEqual(act.counts.get("点命令·W3P"), 2)

    def test_attack_move_keeps_its_order(self):
        cmd = Cmd()
        A.Actuator(cmd).attack_move([unit(1)], 2000.0, 0.0)
        self.assertEqual(cmd.moves, [(1, A.ORDER_ATTACK)])

    def test_still_attacking_after_move_is_reissued(self):
        """下了移动令、它还挂着攻击令 = 没接住：MOVE_RETRY_S 后重下，不等 REPEAT_S。"""
        cmd = Cmd()
        act = A.Actuator(cmd)
        act.move([unit(1)], 2000.0, 0.0)
        k = act.last_point[1]
        act.last_point[1] = (k[0], k[1], k[2], k[3] - A.MOVE_RETRY_S - 0.1)
        act.move([unit(1, order=A.ORDER_ATTACK)], 2000.0, 0.0)
        self.assertEqual(len(cmd.moves), 2)
        self.assertEqual(act.counts.get("移动·还在攻击·重下移动令"), 1)

    def test_refused_counts(self):
        act = A.Actuator(Cmd(ok=False))
        self.assertEqual(act.move([unit(1)], 2000.0, 0.0), 0)
        self.assertEqual(act.counts.get("移动·没下成"), 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Game.batch()（09-23 夜 A1）：块里的命令攒成一批、块结束时一次 lane.commands；Pending 在提交前后的行为；
异常 = 整批作废并放回仲裁；仲裁挡下的单位当场 held、不进批；train 的补解释在提交后才做；读自己刚写的。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
import w3fast as F  # noqa: E402
from openwar3.game import CANCELLED, HELD, Pending  # noqa: E402


class Lane:
    def __init__(self, status=0, verdict=None, order_after=0x000D0012):
        self.calls = []
        self.status, self.verdict, self.order_after = status, verdict, order_after

    def commands(self, calls):
        self.calls.append(list(calls))
        return [F.Receipt(c.op, self.status, 0, self.verdict, 0, self.order_after, (0,) * 8) for c in calls]


class Claims:
    def __init__(self, deny=()):
        self.deny, self.given_back = set(deny), []

    def take(self, addr, seconds, prio=None):
        return addr not in self.deny

    def give_back(self, addr):
        self.given_back.append(addr)


def u(h):
    return SimpleNamespace(addr=0x1000 + h, handle_lo=h, handle_hi=h, handle=(h, h), order=0, type="hfoo")


class Stub(openwar3.Game):
    def __init__(self, lane=None, claims=None):          # 不连游戏
        self._w3fast = F
        self.lane = lane or Lane()
        self.claims = claims or Claims()
        self.priority = 50
        self._recent_orders = {}
        self.last_receipts = []


class BatchTest(unittest.TestCase):
    def test_block_is_one_commands_call(self):
        g = Stub()
        with g.batch() as b:
            r1 = g.move([u(1), u(2)], 10.0, 20.0)
            r2 = g.attack(u(3), u(9))
            r3 = g.stop(u(4))
            self.assertEqual(g.lane.calls, [])                   # 块里一条都没发
            self.assertIsInstance(r2, Pending)
            with self.assertRaises(RuntimeError):
                bool(r2)                                         # 回执还不存在
        self.assertEqual(len(g.lane.calls), 1)
        self.assertEqual([c.op for c in g.lane.calls[0]], [F.OP["point"], F.OP["point"], F.OP["target"], F.OP["immediate"]])
        self.assertTrue(all(r1) and r2 and r3)
        self.assertEqual((b.sent, len(b.receipts)), (4, 4))
        self.assertEqual(r2.reason, "accepted")

    def test_without_block_each_call_goes_alone(self):
        g = Stub()
        g.move(u(1), 1.0, 1.0)
        g.stop(u(2))
        self.assertEqual(len(g.lane.calls), 2)

    def test_nested_blocks_join_the_outer_batch(self):
        g = Stub()
        with g.batch() as outer:
            g.stop(u(1))
            with g.batch() as inner:
                g.stop(u(2))
            self.assertIs(inner, outer)
            self.assertEqual(g.lane.calls, [])
        self.assertEqual(len(g.lane.calls[0]), 2)

    def test_exception_cancels_and_gives_units_back(self):
        g = Stub()
        with self.assertRaises(ValueError):
            with g.batch():
                r = g.move(u(1), 1.0, 1.0)
                raise ValueError("bot 自己的 bug")
        self.assertEqual(g.lane.calls, [])
        self.assertEqual(r.status, CANCELLED)
        self.assertIn("cancelled", r.reason)
        self.assertEqual(g.claims.given_back, [0x1001])
        self.assertIsNone(g._batch)                               # 出错后不会一直卡在批量模式

    def test_held_unit_is_answered_now_and_not_batched(self):
        g = Stub(claims=Claims(deny={0x1002}))
        with g.batch():
            a, b = g.move([u(1), u(2)], 1.0, 1.0)
            self.assertTrue(b.done)
            self.assertEqual(b.status, HELD)
        self.assertEqual(len(g.lane.calls[0]), 1)
        self.assertTrue(a)

    def test_train_explanation_applies_after_flush(self):
        g = Stub(lane=Lane(status=1, verdict=221))
        with g.batch():
            r = g.train(u(1), "Hpal")
        self.assertIn("revive", r.note)

    def test_read_your_writes_after_batch(self):
        g = Stub()
        w = u(5)
        with g.batch():
            g.gather(w, u(9))
        self.assertEqual(g.order_of(w), 0x000D0012)               # 快照还没追上：以回执里的新订单为准


class ManyQueries(unittest.TestCase):
    def test_can_do_many_one_call(self):
        g = Stub(lane=Lane(verdict=8))
        self.assertEqual(g.can_do_many([(u(1), "hfoo"), (u(1), "hkni")]), [8, 8])
        self.assertEqual(len(g.lane.calls), 1)
        self.assertEqual(g.can_do_many([]), [])

    def test_tech_many_one_call(self):
        g = Stub()
        g.me = lambda: 0
        out = g.tech_many(["Rhme", "hbar"])
        self.assertEqual(set(out), {"Rhme", "hbar"})
        self.assertEqual(len(g.lane.calls), 1)


if __name__ == "__main__":
    unittest.main()

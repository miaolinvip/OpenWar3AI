# -*- coding: utf-8 -*-
"""mod_fight「谁在打谁」采样：只取双方接触单位（敌方只算玩家）、双方交替、名额/预算用完就停、读不到不抛。"""
import os
import sys
import unittest
from types import SimpleNamespace as U

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_fight as MF  # noqa: E402


class FakeCmd:
    def __init__(self, tasks, boom=()):
        self.tasks, self.boom, self.calls = tasks, set(boom), []

    def current_task(self, addr):
        self.calls.append(addr)
        if addr in self.boom:
            raise OSError("通道抖了")
        return self.tasks.get(addr)


def u(addr, owner, x, y=0.0):
    return U(addr=addr, owner=owner, x=float(x), y=float(y))


class ContactUnits(unittest.TestCase):
    def test_only_players_and_within_contact(self):
        mine = [u(1, 0, 0), u(2, 0, 5000)]
        foes = [u(10, 1, 800), u(11, 1, 3000), u(20, 12, 100)]   # 20 是野怪，挨着 1 也不算
        cm, cf = MF.contact_units(mine, foes)
        self.assertEqual([a.addr for a in cm], [1])
        self.assertEqual([b.addr for b in cf], [10])


class SampleTargets(unittest.TestCase):
    def test_kinds_and_interleave(self):
        mine = [u(1, 0, 0), u(2, 0, 50), u(3, 0, 100)]
        foes = [u(10, 1, 300)]
        cmd = FakeCmd({1: {"target": (0x100, 0x200)}, 10: {"target": (0x300, 0x400)},
                       2: {"target": None, "x": 5.0, "y": 6.0}}, boom={3})
        rows, n, _ = MF.sample_targets(cmd, mine, foes)
        self.assertEqual(cmd.calls, [1, 10, 2, 3])                 # 我、敌、我、我：交替到一方取完
        self.assertEqual(n, 4)
        self.assertEqual(rows, [[1, 0, "T", 0x100, 0x200], [10, 1, "T", 0x300, 0x400],
                                [2, 0, "P", -1, -1], [3, 0, "-", -1, -1]])

    def test_limit_and_budget(self):
        mine = [u(i, 0, i) for i in range(1, 11)]
        foes = [u(100 + i, 1, i) for i in range(10)]
        rows, n, _ = MF.sample_targets(FakeCmd({}), mine, foes, limit=5)
        self.assertEqual(n, 5)
        ticks = iter([0.0, 0.1, 0.2, 0.5, 0.6, 0.7, 0.8])          # 第 3 次看表已超 0.35
        rows, n, _ = MF.sample_targets(FakeCmd({}), mine, foes, clock=lambda: next(ticks))
        self.assertEqual(n, 2)


def tu(addr, owner, task=(1, 1), ident=(7, 7)):
    return U(addr=addr, owner=owner, x=0.0, y=0.0, task_handle_lo=task[0], task_handle_hi=task[1],
             handle_lo=ident[0], handle_hi=ident[1])


class Cache(unittest.TestCase):
    """09-17 TargetCache：任务句柄变了才重读、没变的 REFRESH 兜底、每帧预算封顶、地址复用换人重读。"""

    def test_first_read_then_quiet_then_changed(self):
        c = MF.TargetCache(refresh=2.0)
        a, b = tu(1, 0), tu(2, 1)
        cmd = FakeCmd({1: {"target": (0x10, 0x20)}, 2: {"target": None, "x": 1.0}})
        self.assertEqual(c.step(cmd, [a, b], 0.0, clock=lambda: 0.0), 2)
        self.assertEqual(c.rows([a, b], 0.1), [[1, 0, "T", 0x10, 0x20], [2, 1, "P", -1, -1]])
        self.assertEqual(c.step(cmd, [a, b], 0.15, clock=lambda: 0.0), 0)      # 没变、没到 2 秒：不读
        a2 = tu(1, 0, task=(9, 9))                                              # 1 号下了新令
        self.assertEqual(c.step(cmd, [a2, b], 0.3, clock=lambda: 0.0), 1)
        self.assertEqual(cmd.calls, [1, 2, 1])
        self.assertEqual(c.changed_reads, 3)

    def test_refresh_after_two_seconds_oldest_first(self):
        c = MF.TargetCache(refresh=2.0)
        a, b = tu(1, 0), tu(2, 1)
        cmd = FakeCmd({})
        c.step(cmd, [a], 0.0, clock=lambda: 0.0)
        c.step(cmd, [b], 1.0, clock=lambda: 0.0)
        cmd.calls.clear()
        self.assertEqual(c.step(cmd, [a, b], 2.5, clock=lambda: 0.0), 1)      # 只有 a 到期
        self.assertEqual(cmd.calls, [1])

    def test_budget_but_at_least_one(self):
        c = MF.TargetCache()
        units = [tu(i, 0) for i in range(1, 6)]
        ticks = iter([0.0, 0.0, 0.03, 0.06, 0.09, 0.12])                       # 第 3 次看表已超 0.05
        n = c.step(FakeCmd({}), units, 0.0, budget=0.05, clock=lambda: next(ticks))
        self.assertEqual(n, 3)
        ticks2 = iter([1.0, 2.0, 3.0])                                          # 读完第 1 个再看表已超预算 0
        n2 = c.step(FakeCmd({}), units, 0.15, budget=0.0, clock=lambda: next(ticks2))
        self.assertEqual(n2, 1)                                                 # 预算用完也至少读 1 个

    def test_address_reuse_rereads_and_hides_old_row(self):
        c = MF.TargetCache()
        cmd = FakeCmd({1: {"target": (0x10, 0x20)}})
        c.step(cmd, [tu(1, 0, ident=(7, 7))], 0.0, clock=lambda: 0.0)
        other = tu(1, 1, ident=(8, 8))                                          # 同一地址换了个单位
        self.assertEqual(c.rows([other], 0.1), [])
        self.assertEqual(c.step(cmd, [other], 0.2, clock=lambda: 0.0), 1)

    def test_read_error_is_row_not_crash(self):
        c = MF.TargetCache()
        self.assertEqual(c.step(FakeCmd({}, boom={1}), [tu(1, 0)], 0.0, clock=lambda: 0.0), 1)
        self.assertEqual(c.rows([tu(1, 0)], 0.1), [[1, 0, "-", -1, -1]])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""统一仲裁（w3claim 优先级）：高优先级能抢，同级/低级抢不走，到期谁都能接，自己续租改写优先级。

两个 Claims 对象在**同一个进程里同时活着**（w3claim 文档：先后两个进程测不出来，表会随最后一个句柄销毁）。
pid 用一个不会和真实游戏撞的数，表名 Local\\War3Claims_<pid>，不碰正在跑的实例。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3claim as W  # noqa: E402

PID = 987_651
U = 0x14109144


class Priority(unittest.TestCase):
    def setUp(self):
        self.combat = W.Claims("combat", pid=PID)
        self.worker = W.Claims("economy", pid=PID)
        self.scav = W.Claims("scavenge", pid=PID)
        self.strat = W.Claims("strategy", pid=PID)
        for c in (self.combat, self.worker, self.scav, self.strat):
            c.give_back(U)

    def test_lower_or_equal_cannot_take_higher_can(self):
        self.assertTrue(self.strat.take(U, 2.0, prio=W.PRIO["strategy_eco"]))     # 策略层派工 45
        self.assertFalse(self.worker.take(U, 2.0))                                # mod_worker 40 抢不走
        self.assertEqual(self.strat.lease_of(U)[:2], (W.MODULES["strategy"], 45))
        self.assertTrue(self.combat.take(U, 2.0, prio=W.PRIO["safety"]))          # 保命 90 抢得走
        self.assertFalse(self.scav.take(U, 2.0))                                  # 捡装备 70 抢不走保命
        self.assertEqual(self.combat.owner_of(U), W.MODULES["combat"])

    def test_equal_priority_first_come(self):
        self.assertTrue(self.combat.take(U, 2.0, prio=60))
        self.assertFalse(self.scav.take(U, 2.0, prio=60))

    def test_own_renew_rewrites_priority(self):
        self.assertTrue(self.combat.take(U, 2.0, prio=W.PRIO["safety"]))
        self.assertTrue(self.combat.take(U, 2.0, prio=W.PRIO["refill"]))         # 自己降级续租
        self.assertTrue(self.scav.take(U, 2.0))                                   # 70 > 55，现在抢得走

    def test_expired_anyone(self):
        self.assertTrue(self.combat.take(U, 0.05, prio=W.PRIO["safety"]))
        time.sleep(0.12)
        self.assertTrue(self.worker.take(U, 1.0))

    def test_strategy_hold_between_refill_and_rotate(self):
        """v288 回防聚拢：去集合点等齐的兵，补攻击令（55）抢不走，打野轮换（60）和保命照样抢得走。"""
        self.assertTrue(self.strat.take(U, 3.5, prio=W.PRIO["strategy_hold"]))
        self.assertFalse(self.combat.take(U, 1.5, prio=W.PRIO["refill"]))
        self.assertTrue(self.combat.take(U, 2.0, prio=W.PRIO["rotate"]))
        self.assertEqual((W.PRIO["refill"], W.PRIO["strategy_hold"], W.PRIO["rotate"]), (55, 58, 60))

    def test_steal_is_safety(self):
        self.assertTrue(self.scav.take(U, 2.0))
        self.assertTrue(self.combat.take(U, 2.0, steal=True))
        self.assertEqual(self.combat.lease_of(U)[1], W.PRIO["safety"])

    def test_clock_is_tickcount(self):
        # PowerShell 那边用 [Environment]::TickCount64 —— 两边必须是同一个钟
        import ctypes
        k = ctypes.WinDLL("kernel32")
        k.GetTickCount64.restype = ctypes.c_uint64
        self.assertLess(abs(W._now_ms() - (int(k.GetTickCount64()) & 0xFFFFFFFF)), 50)


class ProbeWindow(unittest.TestCase):
    """★ 09-17 同一首选格的两个单位（地址差 0x2000 -> (addr>>4)%512 相同）：后登记的那个在下一格。
    前一格过期或被归还之后，后一格的有效租约不能被绕过、不能被看丢（w3claim._window 上面那段）。"""
    X, Y = U, U + 0x2000

    def setUp(self):
        self.combat = W.Claims("combat", pid=PID)
        self.scav = W.Claims("scavenge", pid=PID)
        self.strat = W.Claims("strategy", pid=PID)
        for c in (self.combat, self.scav, self.strat):
            c.give_back(self.X)
            c.give_back(self.Y)
        self.assertEqual(self.combat._slot(self.X), self.combat._slot(self.Y))

    def tearDown(self):
        for c in (self.combat, self.scav, self.strat):
            c.give_back(self.X)
            c.give_back(self.Y)

    def test_hole_from_give_back(self):
        self.assertTrue(self.scav.take(self.Y, 5.0))                               # Y 占首选格
        self.assertTrue(self.combat.take(self.X, 8.0, prio=W.PRIO["safety"]))      # X 落到下一格
        self.scav.give_back(self.Y)                                                # 首选格归还
        self.assertEqual(self.strat.lease_of(self.X)[:2], (W.MODULES["combat"], 90))
        self.assertFalse(self.strat.take(self.X, 3.0, prio=W.PRIO["strategy"]))
        self.assertEqual(self.strat.owner_of(self.X), W.MODULES["combat"])

    def test_expired_ahead(self):
        self.assertTrue(self.scav.take(self.Y, 0.05))
        self.assertTrue(self.combat.take(self.X, 8.0, prio=W.PRIO["safety"]))
        time.sleep(0.12)                                                           # 首选格过期
        self.assertFalse(self.strat.take(self.X, 3.0, prio=W.PRIO["strategy"]))
        self.assertEqual(self.combat.lease_of(self.X)[:2], (W.MODULES["combat"], 90))

    def test_renew_does_not_duplicate(self):
        self.assertTrue(self.scav.take(self.Y, 5.0))
        self.assertTrue(self.combat.take(self.X, 8.0, prio=W.PRIO["safety"]))
        self.scav.give_back(self.Y)
        self.assertTrue(self.combat.take(self.X, 8.0, prio=W.PRIO["refill"]))      # 自己续租：改写原记录
        live = [h for h in self.combat.held() if h[0] == self.X]
        self.assertEqual(len(live), 1)
        self.assertTrue(self.scav.take(self.X, 2.0))                               # 70 > 55
        self.assertEqual(len([h for h in self.combat.held() if h[0] == self.X]), 1)

    def test_give_back_then_anyone(self):
        self.assertTrue(self.scav.take(self.Y, 5.0))
        self.assertTrue(self.combat.take(self.X, 8.0, prio=W.PRIO["safety"]))
        self.combat.give_back(self.X)
        self.assertIsNone(self.strat.lease_of(self.X))
        self.assertTrue(self.strat.take(self.X, 3.0))
        self.assertEqual(self.scav.lease_of(self.Y)[0], W.MODULES["scavenge"])     # 邻居不受影响


if __name__ == "__main__":
    unittest.main()

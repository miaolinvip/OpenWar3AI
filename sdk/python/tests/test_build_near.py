# -*- coding: utf-8 -*-
"""build_near 的跟踪：驱动多次调用（第一个点工人走到才失败 -> 拉黑；第二个点地基出现 -> 成）。

09-23 实机：树林里的点引擎当场接下、工人走到了才失败；同一帧的回执判不出来，所以 build_near 要跨拍跟踪。
单次调用的测试抓不到这种问题，要驱动几拍。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
import w3fast  # noqa: E402

HHOU = 0x68686F75


def unit(h, typ, x, y, owner=0, order=0, first=0, hp=100):
    return SimpleNamespace(handle_lo=h, handle_hi=h, handle=(h, h), type=typ, x=x, y=y, owner=owner, order=order,
                           first_seen=first, alive=hp > 0, hp=hp, hp_max=100, addr=h)


class Stub(openwar3.Game):
    RECENT_S = 0.0                                # 测试里连续调用是同一时刻：关掉"刚下令"的宽限

    def __init__(self):                           # 不连游戏
        self._bad_spots, self._pending_builds, self._recent_orders, self.pub = {}, {}, {}, 1
        self.worker = unit(7, "hpea", 0, 0)
        self.units_ = [self.worker]
        self.issued = []

    def snapshot(self, max_age=0.05):
        return SimpleNamespace(units=self.units_, me=0, publish_count=self.pub,
                               by_handle={u.handle: u for u in self.units_})

    rich = True

    def can_afford(self, code):
        return self.rich

    def is_building(self, u):
        return u.type == "hhou"

    def build(self, worker, code, x, y):
        self.issued.append((x, y))
        worker.order = HHOU                       # 引擎当场接下（哪怕点在树林里）
        return w3fast.Receipt(4, 0, 0, None, 0, HHOU, (0,) * 8)


class BuildNearTest(unittest.TestCase):
    def test_fail_at_arrival_then_blacklist_then_success(self):
        g = Stub()
        first = g.build_near(g.worker, "hhou", 0, 0)
        self.assertEqual(g.issued, [first])
        # 工人还在路上：不重复下令
        self.assertEqual(g.build_near(g.worker, "hhou", 0, 0), first)
        self.assertEqual(len(g.issued), 1)
        # 走到了放不下：引擎撤掉订单，没有地基 -> 这个点拉黑，换第二个点
        g.worker.order = 0
        g.pub = 10
        second = g.build_near(g.worker, "hhou", 0, 0)
        self.assertNotEqual(second, first)
        self.assertIn(first, g._bad_spots["hhou"])
        # 第二个点地基出现 -> 成；再要一座时不会再试已经拉黑的第一个点
        g.units_.append(unit(99, "hhou", second[0], second[1], first=11))
        g.worker.order = 0x000D001B
        g.pub = 12
        third = g.build_near(g.worker, "hhou", 0, 0)
        self.assertNotIn(third, (first,))
        self.assertNotIn(second, g._bad_spots["hhou"], "成功的点不能被拉黑")


class PoorTest(unittest.TestCase):
    def test_no_money_means_no_tries_and_no_blacklist(self):
        """09-23 实机：55 金盖 80 金的农场，24 个点全被拒并拉黑，之后有钱了也没点可试。"""
        g = Stub()
        g.rich = False
        self.assertIsNone(g.build_near(g.worker, "hhou", 0, 0))
        self.assertEqual(g.issued, [])
        self.assertEqual(g._bad_spots.get("hhou", {}), {})
        g.rich = True
        self.assertIsNotNone(g.build_near(g.worker, "hhou", 0, 0))

    def test_blacklist_expires(self):
        import time
        g = Stub()
        g._bad_spots["hhou"] = {(448, 0): time.monotonic() - 1}     # 已过期
        g.build_near(g.worker, "hhou", 0, 0)
        self.assertEqual(g.issued[0], (448, 0), "过期的点应该重新可试")


class ReadYourWritesTest(unittest.TestCase):
    """09-23 实机：同一拍里 hello_bot 派农民盖农场、rush_bot 从快照里看它"空闲"又派去盖兵营，农场半途而废。"""

    def test_recent_receipt_wins_until_snapshot_catches_up(self):
        import time
        g = Stub()
        g.RECENT_S = 0.35
        w = g.worker
        w.order = 0                                          # 快照：空闲
        g._recent_orders[w.handle] = (HHOU, time.monotonic())  # 回执：刚接下盖农场
        self.assertEqual(g.order_of(w), HHOU)
        self.assertTrue(g.is_constructing(w))
        g._recent_orders[w.handle] = (HHOU, time.monotonic() - 1.0)   # 过了宽限：以快照为准
        self.assertEqual(g.order_of(w), 0)


if __name__ == "__main__":
    unittest.main()

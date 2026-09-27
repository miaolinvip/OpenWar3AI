# -*- coding: utf-8 -*-
"""买恢复卷轴被引擎拒：当场读裁决码记下来、按补货周期退避（recovery.buy_verdict / BUY_REFUSED_S）。

09-22 对局画面（1-312 10:55~13:10）：部队在家停了很久，没有任何动作。
「买卷轴 -> 4 秒后购买被拒 -> 30 游戏秒后再来一轮」循环 5 次，每一轮整队恢复都把出击层按住；buy_item 失败只回 0，看不出原因。
"""
import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parents[1]), str(pathlib.Path(__file__).resolve().parents[2] / 'war3-modules'),
                str(pathlib.Path(__file__).resolve().parent)]
from brain import recovery as R  # noqa: E402
from test_army_recovery import Cmd  # noqa: E402
from test_tactics import U, world  # noqa: E402


class RefusingCmd(Cmd):
    """买被拒：W3P buy 回执带回引擎原因码（09-23 起运行时被拒时当场查）。"""

    def __init__(self, verdict):
        super().__init__()
        self.verdict = verdict
        self.last_receipt = None

    def buy_item(self, a, c):
        self.buys.append(c)
        self.last_receipt = SimpleNamespace(verdict=self.verdict)
        return False


def scene():
    troops = [U(10 + i, 'hfoo', x=450 + i * 15, hp=180, hp_max=420) for i in range(6)]
    w = world([U(1, 'htow'), U(2, 'hvlt', x=300), U(3, 'Hamg', x=500)] + troops, t=100)
    w.gold = 1000
    return w


class BuyRefused(unittest.TestCase):
    def test_verdict_is_recorded_and_retry_waits_a_restock(self):
        w = scene()
        r = R.ArmyRecovery()
        c = RefusingCmd(88)
        self.assertIsNone(r.step(w, c, None))
        self.assertEqual(r.phase, 'idle')
        self.assertEqual(r.counts['整队恢复·买卷轴被拒·裁决88'], 1)
        self.assertIn('裁决 88：多半是缺货', r.notes[-1])
        self.assertEqual(r.next_try, w.t + R.BUY_REFUSED_S)
        w.t += 31                                                             # 原来 30 秒后又来一轮
        self.assertIsNone(r.step(w, c, None))
        self.assertEqual(len(c.buys), 1)
        self.assertEqual(r.counts['整队恢复·没开始·冷却中'], 1)
        w.t += R.BUY_REFUSED_S
        r.step(w, c, None)
        self.assertEqual(len(c.buys), 2)

    def test_unreadable_verdict_does_not_break(self):
        w = scene()
        r = R.ArmyRecovery()
        c = RefusingCmd(None)                                              # 回执里没有原因码
        self.assertIsNone(r.step(w, c, None))
        self.assertEqual(r.counts['整队恢复·买卷轴被拒·裁决None'], 1)


if __name__ == "__main__":
    unittest.main()

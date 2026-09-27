# -*- coding: utf-8 -*-
"""水元素护卫大法师：下令成功后握住 GUARD_HOLD 秒（按施法那一级，高过大脑整队交战 76），威胁没了提前还（guard_release）。

09-22：原来护卫令下完当场还租约，交战中「水元素护卫大法师 <-> 支援交战 / 先打兵」6 局约 140 次来回。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3claim import PRIO  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, owner, x, y, hp=500):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp, hp_max=hp,
                order=0, order_known=True)


class GuardHold(unittest.TestCase):
    def test_hold_beats_the_brains_skirmish_lease(self):
        self.assertGreater(PRIO[C.GUARD_PRIO], 76, "大脑整队交战租约 76（brain/act.py skirmish）")
        self.assertLess(PRIO[C.GUARD_PRIO], PRIO["safety"], "保命照样能抢")

    def test_release_when_the_threat_is_gone_or_left_the_mage(self):
        mage = unit(1, "Hamg", 0, 0, 0)
        grunt = unit(100, "ogru", 1, 300, 0)
        held = {10: 50.0, 11: 50.0, 12: 50.0, 13: 5.0}
        last = {10: (100, 0.0), 11: (101, 0.0), 12: (100, 0.0), 13: (100, 0.0)}
        of = {10: mage, 11: mage, 12: None, 13: mage}
        out = set(C.guard_release(held, last, of, [grunt], now=10.0))
        # 10 还在护（目标活着、在大法师身边）；11 的目标没了；12 的大法师没了；13 握到期了
        self.assertEqual(out, {11, 12, 13})
        grunt.x = C.GUARD_R + 100                        # 目标离开大法师身边
        self.assertEqual(set(C.guard_release({10: 50.0}, last, of, [grunt], now=10.0)), {10})


class GuardSticky(unittest.TestCase):
    """09-22：「离大法师最近的敌人」一两秒换一个（368 局一只水元素 20 秒里换了 6 个目标），上一个还在身边就接着打。"""

    def test_keeps_previous_target_while_close_enough(self):
        mage = unit(1, "Hamg", 0, 0, 0)
        water = unit(2, "hwat", 0, 100, 0)
        old = unit(10, "ogru", 1, 400, 0)
        new = unit(11, "ogru", 1, 300, 50)                 # 比上一个近一点
        last = {2: (10, 100.0)}
        # 过了 GUARD_SWITCH、没过 GUARD_SAME：接着打同一个 = 不重下
        self.assertEqual(C.guard_picks(mage, [water], [old, new], last, 103.0), [])
        # 过了 GUARD_SAME：刷新的还是上一个
        self.assertEqual([t.addr for _, t in C.guard_picks(mage, [water], [old, new], last, 107.0)], [10])

    def test_switches_when_the_old_one_is_much_farther_or_gone(self):
        mage = unit(1, "Hamg", 0, 0, 0)
        water = unit(2, "hwat", 0, 100, 0)
        old = unit(10, "ogru", 1, 800, 0)
        new = unit(11, "ogru", 1, 150, 0)
        last = {2: (10, 100.0)}
        self.assertEqual([t.addr for _, t in C.guard_picks(mage, [water], [old, new], last, 103.0)], [11])
        self.assertEqual([t.addr for _, t in C.guard_picks(mage, [water], [new], last, 103.0)], [11])


if __name__ == "__main__":
    unittest.main()

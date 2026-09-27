# -*- coding: utf-8 -*-
"""撤退令下完至少握 SAFE_RELEASE_HOLD 秒才可能提前放手（mod_combat.pull_held_long_enough）。

09-22：被集火撤 / 预警撤是 pulled = LEASE（8 秒）、solo = SOLO_LEASE（10 秒），旧写法按 10 秒倒推「已握 2 秒」，
下一轮就放手 —— 17:20:28 血法师被集火撤，17:20:29 就被放开、本模块接着给它下了烈焰风暴，没撤成。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402


class PullHold(unittest.TestCase):
    def test_focus_pull_is_held_even_though_solo_is_longer(self):
        seen = {}
        t = 100.0
        until = t + C.LEASE                        # 被集火撤：pulled 8 秒（solo 另记 10 秒）
        self.assertFalse(C.pull_held_long_enough(seen, 1, until, t + 0.1))
        # 旧写法在这一刻就放行了：until - now = 7.9，不大于 SOLO_LEASE - SAFE_RELEASE_HOLD = 8.5
        self.assertLessEqual(until - (t + 0.1), C.SOLO_LEASE - C.SAFE_RELEASE_HOLD)
        self.assertFalse(C.pull_held_long_enough(seen, 1, until, t + 1.0))
        self.assertTrue(C.pull_held_long_enough(seen, 1, until, t + 0.1 + C.SAFE_RELEASE_HOLD))

    def test_renewed_pull_restarts_the_hold(self):
        seen = {}
        self.assertFalse(C.pull_held_long_enough(seen, 1, 108.0, 100.0))
        self.assertTrue(C.pull_held_long_enough(seen, 1, 108.0, 102.0))
        self.assertFalse(C.pull_held_long_enough(seen, 1, 110.5, 102.5), "又拉了一次（期限变了）：重新计时")

    def test_units_are_independent(self):
        seen = {}
        C.pull_held_long_enough(seen, 1, 108.0, 100.0)
        self.assertFalse(C.pull_held_long_enough(seen, 2, 109.0, 101.0))
        self.assertTrue(C.pull_held_long_enough(seen, 1, 108.0, 101.6))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""统一仲裁收口到下令出口（w3cmd._logged）：别的模块同级以上占着就不下、记「让出」；自己占着的照常下。

租约表用假 pid（WAR3_PID=987653，Local\\War3Claims_987653），两个 Claims 在同一进程里同时活着。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["WAR3_PID"] = "987653"

import modlog  # noqa: E402
import w3claim  # noqa: E402
import w3cmd  # noqa: E402

UNIT, ITEM = 0x14AA0010, 0x14BB0020


class Ch:
    """假车道：记下发出去的语义命令（09-23 起 w3cmd 走 W3P，不再有动作 28/41）。"""

    def __init__(self):
        self.calls = []

    def commands(self, calls):
        import w3fast
        inv = {v: k for k, v in w3fast.OP.items()}
        self.calls += [(inv[c.op], c.args[0], c.args[3]) for c in calls]
        return [w3fast.Receipt(c.op, 0, 1, None, 0, 0, (0,) * 8) for c in calls]


class _NoWorld:
    def read(self):
        return None


def cmd(ch):
    return w3cmd.Cmd.with_backends(ch, _NoWorld(), pid=987653)


class Arbiter(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.orig_write, self.orig_name = w3cmd._cmdlog_write, modlog._current
        w3cmd._cmdlog_write = lambda t: self.lines.append(t)
        w3cmd._claims_by_module.clear()
        self.combat = w3claim.Claims("combat", pid=987653)
        self.scav = w3claim.Claims("scavenge", pid=987653)
        self.combat.give_back(UNIT)
        self.scav.give_back(UNIT)

    def tearDown(self):
        w3cmd._cmdlog_write, modlog._current = self.orig_write, self.orig_name
        self.combat.give_back(UNIT)
        self.scav.give_back(UNIT)

    def test_reassert_without_lease_yields_to_safety(self):
        """mod_scavenge 补令不抢租约 —— 保命 90 占着时出口挡下。"""
        self.assertTrue(self.combat.take(UNIT, 5.0, prio=w3claim.PRIO["safety"]))
        modlog._current = "mod_scavenge"
        ch = Ch()
        self.assertFalse(cmd(ch).get_item(UNIT, ITEM, 1.0, 2.0))
        self.assertEqual(ch.calls, [])                                  # 没下
        self.assertIn("让出 占着=3/90", self.lines[-1])

    def test_own_lease_passes(self):
        self.assertTrue(self.combat.take(UNIT, 5.0, prio=w3claim.PRIO["safety"]))
        modlog._current = "mod_combat"
        ch = Ch()
        self.assertTrue(cmd(ch).move_to(UNIT, 10.0, 20.0))
        self.assertEqual(ch.calls[0][0], "point")

    def test_lower_priority_holder_does_not_block(self):
        """策略层 50 占着，mod_combat（默认 55）没抢租约的出口也能下；mod_worker（40）不能。"""
        strat = w3claim.Claims("strategy", pid=987653)
        self.assertTrue(strat.take(UNIT, 5.0, prio=w3claim.PRIO["strategy"]))
        modlog._current = "mod_combat"
        self.assertTrue(cmd(Ch()).move_to(UNIT, 1.0, 2.0))
        modlog._current = "mod_worker"
        ch = Ch()
        self.assertFalse(cmd(ch).target_order(UNIT, ITEM, 0x000D0032))
        self.assertEqual(ch.calls, [])
        strat.give_back(UNIT)

    def test_unregistered_process_not_checked(self):
        self.assertTrue(self.combat.take(UNIT, 5.0, prio=w3claim.PRIO["safety"]))
        modlog._current = None
        self.assertTrue(cmd(Ch()).move_to(UNIT, 1.0, 2.0))


if __name__ == "__main__":
    unittest.main()

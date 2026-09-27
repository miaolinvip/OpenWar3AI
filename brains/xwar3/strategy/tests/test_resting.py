# -*- coding: utf-8 -*-
"""养伤名单（毫秒层 war3-modules/unitmind.py 发布的 resting.json）在大脑这边怎么用。

09-21 仍常见残血单位不撤退、打到阵亡。18:00 之后六个实例 1079 个战死里 262 个是
「拉出来又被派回去」—— 撤退只是 8 秒租约，到期后出击/打野令把 10% 血的兵又派回去（死时血中位 10%）。
现在：被拉出来的兵进养伤，血没回到五成之前出击/打野/守家都不派它；整队恢复看得见它；没人管它时先回家。
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T          # noqa: E402
from brain import runtime as RT         # noqa: E402
from brain.runtime import Brain         # noqa: E402
from test_tactics import U, world       # noqa: E402
from test_runtime_smoke import FakeCmd  # noqa: E402


class ReadResting(unittest.TestCase):
    def test_same_match_only(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                        "war3-modules"))
        import w3lease
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd()
            cmd.pid = 4242
            b = Brain(cmd, None, d)
            p = os.path.join(d, "resting.json")
            w3lease.write_deadlines(p, {0x55: time.monotonic() + 2.5}, time.monotonic(), 4242)
            self.assertEqual(b._read_resting(), {0x55})
            w3lease.write_deadlines(p, {0x55: time.monotonic() + 2.5}, time.monotonic(), 999)
            self.assertEqual(b._read_resting(), set())


class RestingInTactics(unittest.TestCase):
    def run_tick(self, resting):
        home = U(1, "htow")
        feet = [U(10 + i, "hfoo", x=100 + i * 20) for i in range(6)]
        far = U(20, "hfoo", x=0, y=3000)
        far.hp = 90
        w = world([home] + feet + [far, U(100, "htow", 1, 4000), U(101, "hpea", 1, 4100)], t=600)
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.config.values["enable_harass"] = False
            b.attack.terrain = None          # 不带地形表：养伤回城堡、沿用 REST_HOME_R（去空地见 test_open_ground）
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 1000, 0, why="打野")]
            seen_by_recovery = []
            b.recovery.step = lambda v, cmd, claims, blocked=None, **kw: seen_by_recovery.append(
                {u.addr for u in v.my_army}) or None
            sent = []
            b._do = lambda it, t: sent.append(it)
            b._hero_idle_probe = lambda *args: None
            b._read_hurt = lambda: set()
            b._read_resting = lambda: set(resting)
            b.job_tactics(w)
        return sent, seen_by_recovery, b

    def test_resting_units_are_not_sent_to_fight(self):
        sent, _, b = self.run_tick({10, 20})
        fight = {u.addr for it in sent if it.kind != "move" or "养伤" not in it.why for u in it.units}
        self.assertFalse({10, 20} & fight, [(it.kind, it.why, [u.addr for u in it.units]) for it in sent])
        self.assertEqual(b.counts["养伤·不派"], 2)

    def test_recovery_sees_them(self):
        _, seen, _ = self.run_tick({10, 20})
        self.assertTrue(seen and {10, 20} <= seen[0])

    def test_far_resting_unit_goes_home_near_one_stays(self):
        sent, _, b = self.run_tick({10, 20})
        home_moves = [it for it in sent if it.kind == "move" and "养伤" in it.why]
        self.assertEqual([[u.addr for u in it.units] for it in home_moves], [[20]])   # 10 离家 100 < REST_HOME_R
        self.assertLess(RT.math.hypot(100, 0), RT.REST_HOME_R)

    def test_without_resting_nothing_changes(self):
        sent, _, b = self.run_tick(set())
        fight = {u.addr for it in sent for u in it.units}
        self.assertTrue({10, 20} <= fight)
        self.assertFalse([it for it in sent if "养伤" in it.why])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""打野被野怪盯上拉出来、血低于 AGGRO_STAY_FRAC 的兵：野怪不再盯它就回家养伤，不在营地边站着（mod_combat 打野仇恨段）。

09-22 残血单位直接撤回家，不再留在战场边。1-397 心跳一分钟「血太低不回 47」人·轮 —— 原来就在营地边等营地打完。
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_combat as C  # noqa: E402
from test_combat_main_smoke import FakeClaims, FakeCmd, FakeOdds, FakeTime, _Stop  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(a, typ, x=0, y=0, hp=420, maximum=420, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True,
                handle_lo=0x100000 + a, handle_hi=0x200000 + a)


class LowGoesHome(unittest.TestCase):
    def test_low_footman_rests_at_home_once_the_creep_switches_target(self):
        clock = FakeTime()
        a = unit(1, "hfoo", 2000, 0)
        b = unit(2, "hfoo", 2050, 60, hp=120)                  # 28%
        r = unit(3, "hrif", 1850, 0, 535, 535)
        hall = unit(90, "htow", -3000, -3000, 1500, 1500)
        creeps = [unit(100 + i, "ngnb", 2150, 40 * i, 330, 330, 12) for i in range(2)]
        t_switch = clock.t + 5.0

        class Cmd(FakeCmd):
            def current_task(self, u):
                tgt = b if clock.t < t_switch else a            # 5 秒后野怪换去打 A
                return {"target": (tgt.handle_lo, tgt.handle_hi)}

        cmd = Cmd({})
        mine = [a, b, r, hall]
        snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + creeps, age_ms=5)
        ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: snap)
        notes = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", clock), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *x, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *x, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *x, **k: None), \
                self.assertRaises(_Stop):
            C.main()
        self.assertFalse([n for n in notes if "出错" in n])
        beat = next(n for n in notes if n.startswith("心跳"))
        self.assertRegex(beat, r"其中回家养伤 [1-9]", beat[beat.find("打野仇恨"):][:160])
        self.assertIn(("move", 2), cmd.calls)
        # 进了养伤就不再被补攻击令派回营地
        n_move = max(i for i, c in enumerate(cmd.calls) if c == ("move", 2))
        self.assertFalse([c for c in cmd.calls[n_move:] if c[0] == "target" and c[1] == 2], "养伤的兵被派回去打了")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""主循环里的火圈（mod_combat ff_zone / ff_fence_moves）：放完烈焰风暴圈里的自己人推出去；火没灭时走进来的也推出去。

09-22 晚 血法师的技能区域在效果结束前，所有单位都要绕开；移动路径在此期间也不得穿过该区域。
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
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


class FireZoneMain(unittest.TestCase):
    def test_inside_steps_out_and_walker_is_kept_out(self):
        clock = FakeTime()
        bm = unit(1, "Hblm", 0, 0, 600, 600)
        foot = unit(2, "hfoo", 520, 20)
        knight = unit(3, "hkni", -3000, 0, 835, 835)
        hall = unit(90, "htow", -4000, -4000, 1500, 1500)
        foes = [unit(100 + i, "ogru", 500 + 30 * (i % 2), 30 * (i // 2), 700, 700, 1) for i in range(4)]
        mine = [bm, foot, knight, hall]
        snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + foes, age_ms=5)
        cmd = FakeCmd({1: "Hblm"})
        state = {"cast_at": None}

        def read():
            if state["cast_at"] is None and any(c[0] == "point" and c[1] == 1 for c in cmd.calls):
                state["cast_at"] = clock.t
            if state["cast_at"] is not None and clock.t - state["cast_at"] >= 1.0:
                knight.x, knight.y = 560.0, 0.0              # 火还在烧，骑士走进圈里
            return snap

        ses = SimpleNamespace(pid=4242, cmd=cmd, read=read)
        notes = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", clock), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            C.main()
        self.assertFalse([n for n in notes if "出错" in n])
        self.assertIsNotNone(state["cast_at"], "得先放出烈焰风暴")
        beat = next(n for n in notes if n.startswith("心跳"))
        seg = beat[beat.find("自己放烈焰风暴"):][:80]
        self.assertRegex(seg, r"让圈里自己人躲开 [1-9]", seg)
        self.assertRegex(seg, r"路过火圈绕开 [1-9]", seg)
        self.assertIn(("move", 3), cmd.calls)


if __name__ == "__main__":
    unittest.main()

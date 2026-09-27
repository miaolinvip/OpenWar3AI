# -*- coding: utf-8 -*-
"""牧师定向治疗走 cast_target 那条路（w3cmd.cast_target），不走 target_order（DLL 动作 28）。

09-22 对局画面（1-307 13:32~14:37）：在家等牧师加血太慢，浪费时间。两个牧师每 3 秒一条治疗令、引擎回 ok，
65 游戏秒里目标步兵只涨了 182 -> 198（= 自然回血），牧师离它 291/356（治疗射程 250）一步没动 ——
动作 28 是给右键/捡装备造的订单，目标施法走它基本放不出来（w3cmd.cast_target 文档：30 次只进冷却 1 次）。
"""
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mod_combat as C  # noqa: E402
from test_combat_main_smoke import FakeClaims, FakeCmd, FakeOdds, FakeTime, _Stop, unit  # noqa: E402


def scenario(_i):
    mine = [unit(1, "Hamg", 0, 0, 0, 500),
            unit(50, "hmpr", 0, 300, 0, 290),
            unit(60, "hfoo", 0, 500, 100, 182, 420),       # 缺 238 血，离牧师 224
            unit(90, "htow", 0, -2000, -2000, 1500)]
    return SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=list(mine), age_ms=5)


class PriestHealPath(unittest.TestCase):
    def test_heal_goes_through_cast_target(self):
        clock = FakeTime()
        cmd = FakeCmd({u.addr: u.type for u in scenario(0).mine})
        ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: scenario(0))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        notes = []
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
        self.assertEqual([n for n in notes if "出错" in n], [])
        heals = [c for c in cmd.calls if c[0] == "cast_target" and c[1] == 50 and c[2] == C.ORDER_HEAL]
        self.assertTrue(heals, [c for c in cmd.calls if c[1] == 50][:5])
        self.assertEqual({c[3] for c in heals}, {60})
        self.assertFalse([c for c in cmd.calls if c[0] == "target" and c[2] == C.ORDER_HEAL])


if __name__ == "__main__":
    unittest.main()

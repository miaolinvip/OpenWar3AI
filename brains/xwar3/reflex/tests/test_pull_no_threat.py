# -*- coding: utf-8 -*-
"""按掉血拉人要身边有敌人（mod_combat PULL_THREAT_R）。

09-22（1-399 22 分）#26 骑士一直在家里来回徘徊，没有实际行动。
骑士 26% 血回到家、挂着每墙钟秒约 2 血的持续伤害，「血低于 LOW_FRAC 就拉」不看有没有敌人：拉出来 -> 1.5 秒后没敌人提前放手 ->
又掉血 -> 再拉、换一个撤退点。21:32:10~21:33:22 一共 32 次，在家里来回走。
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


def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


def run(enemy_x):
    knight = unit(26, "hkni", -4700, 2300, 220, 835)
    hall = unit(90, "htow", -4944, 2735, 1500, 1500)
    foe = unit(100, "ugho", enemy_x, 2300, 340, 340, 1)
    mine = [knight, hall]
    snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + [foe], age_ms=5)

    def read():
        knight.hp = max(60, knight.hp - 2)              # 持续伤害：每轮掉 2
        return snap

    cmd = FakeCmd({})
    ses = SimpleNamespace(pid=4242, cmd=cmd, read=read)
    notes = []
    tmp = tempfile.TemporaryDirectory()
    try:
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", FakeTime()), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None):
            try:
                C.main()
            except _Stop:
                pass
    finally:
        tmp.cleanup()
    return notes, cmd


class PullNeedsThreat(unittest.TestCase):
    def test_no_enemy_around_no_pull(self):
        notes, cmd = run(enemy_x=3000)                    # 敌人在 7700 外
        self.assertFalse([n for n in notes if "出错" in n])
        pulls = [n for n in notes if "hkni@1A" in n and "拉出来" in n]
        self.assertEqual(pulls, [], pulls[:3])
        beat = next(n for n in notes if n.startswith("心跳"))
        self.assertRegex(beat, r"身边没敌人不拉 [1-9]")

    def test_enemy_close_still_pulled(self):
        notes, cmd = run(enemy_x=-4100)                   # 600 外有敌兵
        self.assertTrue([n for n in notes if "hkni@1A" in n and "拉出来" in n])


if __name__ == "__main__":
    unittest.main()

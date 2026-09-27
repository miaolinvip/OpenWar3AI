# -*- coding: utf-8 -*-
"""毫秒层的撤退 / 脱离移动令走 JASS IssuePointOrder 那条路（retreat_move，标签「内核」）。

09-22 A/B（同一批局）：英雄撤退走「内核」13 次里 12 次在走；走动作 41 的 8 次里 6 次，没走的那 2 次下令当下订单槽里还是原来的攻击令
（动作 41 的「接了」只说明 DLL 调用走完了）。09-23 P3 起 W3P point 命令在运行时里走的就是这条路，开放层只有 move_to 一条路。
主循环跑一遍英雄局部自保（和 test_hero_local_safety 同一个场景）：撤退令走 move_to、订单号是 move（0x000D0012）。
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


class MoveCmd(FakeCmd):
    """记下每条点命令的订单号（FakeCmd 只记单位）。"""

    def __init__(self, types):
        super().__init__(types)
        self.point_orders = []

    def move_to(self, u, x, y, order=0):
        self.point_orders.append((u, order))
        return super().move_to(u, x, y, order)


def run_loop(cmd):
    hero = unit(1, "Hamg")
    mine = [hero, unit(90, "htow", -2000, -2000, 1500, 1500)]
    foes = [unit(100 + i, "ugho", 200, i * 30, 800, 800, 1) for i in range(3)]
    snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + foes, age_ms=5)
    ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: snap)
    notes, logged = [], []
    tmp = tempfile.TemporaryDirectory()
    try:
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", FakeTime()), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "append", lambda name, text: logged.append((name, text))), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                mock.patch.object(C, "under_fire", lambda *a, **k: True):
            # 09-22 起没在挨打的英雄不因「孤军」拉（mod_combat HIT_WIN 上面那段）；这里验的是撤退令的走法，场景定成在挨打
            try:
                C.main()
            except _Stop:
                pass
    finally:
        tmp.cleanup()
    return hero, notes, logged


class RetreatKernel(unittest.TestCase):
    def test_hero_escape_is_a_move_order(self):
        cmd = MoveCmd({1: "Hamg"})
        hero, notes, _ = run_loop(cmd)
        self.assertFalse([n for n in notes if "出错" in n])
        self.assertTrue(any("英雄局部自保" in n for n in notes))
        mine = [o for u, o in cmd.point_orders if u == hero.addr]
        self.assertTrue(mine)
        self.assertTrue(all(o == C.ORDER_MOVE for o in mine))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""牧师不奶大脑送死名单里的兵（强制攻击腾人口 / 喂野怪）：定向治疗跳过，身边的牧师关自动治疗，名单空了再开。

09-22 bug：一边强制攻击（反补）自己的小兵，牧师一边给这些要打掉的小兵加血。
决策明细：18 次强制攻击，大多数被打的那个前后 30 秒被牧师奶了 2~10 次（1-316 03:12:38 奶到 157、03:12:44 掉到 15 又奶回 117）。
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
from test_combat_main_smoke import LOOPS, FakeClaims, FakeCmd, FakeOdds, FakeTime, _Stop, unit  # noqa: E402

PRIEST, VICTIM, OTHER = 50, 60, 61


def scenario(_i):
    mine = [unit(1, "Hamg", 0, 0, 0, 500),
            unit(PRIEST, "hmpr", 0, 300, 0, 290),
            unit(VICTIM, "hfoo", 0, 500, 100, 150, 420),       # 被自己人打的那个，缺血最多
            unit(OTHER, "hfoo", 0, 420, 60, 300, 420),
            unit(90, "htow", 0, -2000, -2000, 1500)]
    return SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=list(mine), age_ms=5)


def run(sac_fn):
    clock = FakeTime()
    cmd = FakeCmd({u.addr: u.type for u in scenario(0).mine})
    ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: scenario(0))
    tmp = tempfile.TemporaryDirectory()
    notes = []
    with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
            mock.patch.object(C, "Session", lambda: ses), \
            mock.patch.object(C, "Claims", FakeClaims), \
            mock.patch.object(C, "time", clock), \
            mock.patch.object(C, "note", notes.append), \
            mock.patch.object(C, "load_sacrifice", lambda path, now: sac_fn(clock)), \
            mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
            mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
            mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
            mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
            mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None):
        try:
            C.main()
        except _Stop:
            pass
    tmp.cleanup()
    return cmd, notes


class PriestSkipsSacrifice(unittest.TestCase):
    def test_pick_skips(self):
        pr = unit(PRIEST, "hmpr", 0, 300, 0, 290)
        allies = [unit(VICTIM, "hfoo", 0, 500, 100, 150, 420), unit(OTHER, "hfoo", 0, 420, 60, 300, 420)]
        self.assertEqual(C.priest_pick(pr, allies).addr, VICTIM)             # 对照：不跳过就挑缺血最多的它
        self.assertEqual(C.priest_pick(pr, allies, skip={VICTIM}).addr, OTHER)

    def test_heal_goes_elsewhere_and_autocast_off_then_back_on(self):
        start = {}

        def sac(clock):                                                      # 前一半时间在名单里，后一半名单空了
            start.setdefault("t", clock.t)
            return {VICTIM} if clock.t - start["t"] < LOOPS * 0.35 / 2 else set()
        cmd, notes = run(sac)
        self.assertEqual([n for n in notes if "出错" in n], [])
        toggles = [c[2] for c in cmd.calls if c[0] == "instant" and c[1] == PRIEST and c[2] in (C.ORDER_HEAL_OFF, C.ORDER_HEAL_ON)]
        self.assertEqual(toggles, [C.ORDER_HEAL_OFF, C.ORDER_HEAL_ON])        # 关一次、名单空了开一次，不刷

    def test_never_heals_the_victim_while_listed(self):
        cmd, _ = run(lambda clock: {VICTIM})
        heals = [c for c in cmd.calls if c[0] == "cast_target" and c[1] == PRIEST and c[2] == C.ORDER_HEAL]
        # 第一轮名单还没读进来（毫秒层每秒读一次），之后一次都不许奶它
        self.assertTrue(heals)
        self.assertFalse([c for c in heals[1:] if c[3] == VICTIM], heals[:4])


if __name__ == "__main__":
    unittest.main()

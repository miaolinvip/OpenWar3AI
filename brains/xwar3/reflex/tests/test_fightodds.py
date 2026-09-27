# -*- coding: utf-8 -*-
"""fightodds：接触计时、按秒数选门槛、冷却、换局重置、写信号。predict 用假的（期望按 RULES 手算）。"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fightodds  # noqa: E402


def U(t="hfoo", x=0.0, y=0.0, hp=400):
    return SimpleNamespace(type=t, x=x, y=y, hp=hp, hp_max=420)


class FakePredict:
    def __init__(self, p):
        self.p = p
        self.calls = []

    def __call__(self, units, k):
        self.calls.append((len(units), k))
        return dict(p_win_value=self.p, n_near=(3, 4))


MINE = [U(x=i * 50) for i in range(4)]
FOES = [U(x=300 + i * 50) for i in range(4)]


class TestFightOdds(unittest.TestCase):
    def test_threshold_by_seconds(self):
        self.assertIsNone(fightodds.threshold(4.9))
        self.assertEqual(fightodds.threshold(5.0), 0.06)
        self.assertEqual(fightodds.threshold(9.9), 0.06)
        self.assertEqual(fightodds.threshold(10.0), 0.08)

    def test_no_decision_before_5s(self):
        fp = FakePredict(0.01)
        o = fightodds.FightOdds(fp)
        for t in (100.0, 102.0, 104.9):
            self.assertIsNone(o.update(t, MINE, FOES, True))
        self.assertEqual(fp.calls, [])

    def test_fires_at_5s_when_losing(self):
        o = fightodds.FightOdds(FakePredict(0.05))
        o.update(100.0, MINE, FOES, True)
        d = o.update(105.0, MINE, FOES, True)
        self.assertIsNotNone(d)
        self.assertEqual((d["k"], d["p"], d["thr"]), (5.0, 0.05, 0.06))

    def test_between_thresholds(self):
        # p=0.07：5 秒门槛 0.06 不撤，10 秒门槛 0.08 撤
        o = fightodds.FightOdds(FakePredict(0.07))
        o.update(100.0, MINE, FOES, True)
        self.assertIsNone(o.update(105.0, MINE, FOES, True))
        self.assertIsNone(o.update(106.0, MINE, FOES, True))
        d = o.update(110.0, MINE, FOES, True)
        self.assertEqual(d["thr"], 0.08)

    def test_winning_never_fires(self):
        o = fightodds.FightOdds(FakePredict(0.4))
        o.update(100.0, MINE, FOES, True)
        self.assertTrue(all(o.update(100.0 + s, MINE, FOES, True) is None for s in range(1, 30)))
        self.assertEqual(o.last["p"], 0.4)

    def test_cooldown_and_pred_every(self):
        fp = FakePredict(0.01)
        o = fightodds.FightOdds(fp)
        o.update(100.0, MINE, FOES, True)
        self.assertIsNotNone(o.update(105.0, MINE, FOES, True))
        self.assertIsNone(o.update(105.5, MINE, FOES, True))      # 不到 1 秒不算
        self.assertIsNone(o.update(112.0, MINE, FOES, True))      # 冷却 8 秒内不再叫
        self.assertIsNotNone(o.update(113.0, MINE, FOES, True))   # 105 + 8

    def test_gap_ends_fight_and_restarts_timer(self):
        o = fightodds.FightOdds(FakePredict(0.01))
        o.update(100.0, MINE, FOES, True)
        self.assertIsNone(o.update(103.0, MINE, FOES, False))
        self.assertIsNone(o.update(108.0, MINE, FOES, False))     # 离上次接触 8 秒 > 4：这一仗结束
        self.assertIsNone(o.start)
        self.assertIsNone(o.update(109.0, MINE, FOES, True))      # 新的一仗从 109 算
        self.assertIsNotNone(o.update(114.0, MINE, FOES, True))

    def test_short_gap_keeps_timer(self):
        o = fightodds.FightOdds(FakePredict(0.01))
        o.update(100.0, MINE, FOES, True)
        o.update(102.0, MINE, FOES, False)
        self.assertIsNotNone(o.update(105.0, MINE, FOES, True))   # 断开 3 秒 < 4：还是同一仗，k=5

    def test_small_skirmish_ignored(self):
        fp = FakePredict(0.01)
        o = fightodds.FightOdds(fp)
        o.update(100.0, MINE[:2], FOES, True)
        self.assertIsNone(o.update(106.0, MINE[:2], FOES, True))
        self.assertEqual(fp.calls, [])

    def test_clock_backwards_resets(self):
        o = fightodds.FightOdds(FakePredict(0.01))
        o.update(500.0, MINE, FOES, True)
        o.update(3.0, MINE, FOES, True)                            # 新的一局
        self.assertEqual(o.start, 3.0)

    def test_predict_error_counted(self):
        def boom(units, k):
            raise RuntimeError("x")
        o = fightodds.FightOdds(boom)
        o.update(100.0, MINE, FOES, True)
        self.assertIsNone(o.update(105.0, MINE, FOES, True))
        self.assertEqual(o.fails, 1)

    def test_real_model_loads(self):
        o = fightodds.FightOdds()
        o.update(100.0, MINE, FOES, True)
        o.update(105.0, MINE, FOES, True)
        self.assertIsNone(o.error)
        self.assertEqual(o.checks, 1)
        self.assertIsNotNone(o.last)

    def test_write_signal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "fight_retreat.json")
            fightodds.write_signal(p, 1234, 7, 321.5, dict(k=5.0, p=0.03, thr=0.06), 100.4, -200.6, 9, 12, 3)
            got = json.load(open(p, encoding="utf-8"))
        self.assertEqual((got["seq"], got["pid"], got["game_ms"], got["x"], got["y"], got["n_me"]), (3, 1234, 321500, 100, -201, 9))


if __name__ == "__main__":
    unittest.main()

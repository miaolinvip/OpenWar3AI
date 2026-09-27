# -*- coding: utf-8 -*-
"""openwar3.run 的局末判断（09-24）：暂停（时钟不走、世界块照发）不算结束；建筑全没了 = 负（兵还活着也是，失败对话框时钟停住）；
对面建筑没了 = 胜，end_game=True 时发结束令；快车道不收命令（游戏在收尾）不算 bot 出错；时钟停太久才收工。
Game 和时间都换成假的，一拍一拍按脚本走，不连游戏。"""
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402

w3paths.setup()
from openwar3 import bot as B  # noqa: E402
from w3fast import FastLaneUnavailable  # noqa: E402


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)


class FakeGame:
    """script(t) -> dict：clock（游戏秒）、mine（我方建筑数）、units（我方单位数）、foes（对面建筑数）、lane（快车道收不收命令）。"""
    script = None
    last = None

    def __init__(self, **kw):
        self.pid = 1
        self.ended = 0
        self.pub = 0
        FakeGame.last = self

    def _st(self):
        return FakeGame.script(B.time.t - 1000.0)

    def snapshot(self, max_age=0.05):
        st = self._st()
        self.pub += 1                                      # 世界块一直在发（暂停时也是，09-24 实测）
        return NS(in_game=True, clock=st["clock"], publish_count=self.pub, me=0)

    def clock(self):
        return self._st()["clock"]

    def units(self, owner="all", types=None, alive=True):
        return [NS()] * self._st().get("units", 5)

    def my_buildings(self, types=None):
        return [NS()] * self._st()["mine"]

    @property
    def world(self):
        foes = self._st()["foes"]
        return NS(read=lambda: NS(units=[NS(alive=True, owner=1, building=True)] * foes))

    def is_building(self, u):
        return True

    def events(self):
        return []

    def status(self):
        return {"fastLane": {}, "collectUs": 0}

    def end_game(self):
        self.ended += 1
        return True


class TickBot(B.Bot):
    name = "t"

    def __init__(self, raise_lane_after=None):
        self.ticks = 0
        self.raise_lane_after = raise_lane_after

    def on_tick(self, g):
        self.ticks += 1
        if self.raise_lane_after is not None and B.time.t - 1000.0 > self.raise_lane_after:
            raise FastLaneUnavailable("车道里空槽不够（0 < 1）")


def running(t, t0=0.0):
    """游戏时钟跟墙钟走（2 倍速），从 2 秒开始（过了「等进局」那道 1.5 秒）。"""
    return 2.0 + 2 * (t - t0)


class Run(unittest.TestCase):
    def setUp(self):
        self.saved = (B.Game, B.time)
        B.Game, B.time = FakeGame, FakeTime()

    def tearDown(self):
        B.Game, B.time = self.saved

    def go(self, script, bot=None, **kw):
        FakeGame.script = script
        B.time = FakeTime()                                  # 每一局从 t=0 开始
        bot = bot or TickBot()
        reason = B.run(bot, pid=1, hz=5, max_minutes=30, verbose=False, **kw)
        return reason, bot, FakeGame.last

    def test_pause_then_defeat_dialog_is_loss(self):
        # 0~20 秒正常打；20 秒起暂停 90 秒（时钟冻住、建筑都在）；110 秒起建筑没了（失败对话框，时钟照样冻着，兵还活着）
        def script(t):
            if t < 20:
                return {"clock": running(t), "mine": 3, "foes": 2}
            return {"clock": running(20), "mine": 3 if t < 110 else 0, "foes": 2, "units": 4}
        reason, bot, g = self.go(script, end_game=True)
        self.assertEqual((reason, bot.run_stats["result"]), ("我方没有建筑了", "loss"))
        self.assertGreater(B.time.t - 1000.0, 109)          # 暂停那 90 秒没被当成结束
        self.assertEqual(g.ended, 1)                         # 分出胜负，发了结束令

    def test_win_sends_end_game_only_when_asked(self):
        def script(t):
            return {"clock": running(t), "mine": 3, "foes": 2 if t < 15 else 0}
        reason, bot, g = self.go(script, end_game=True)
        self.assertEqual((reason, bot.run_stats["result"], g.ended), ("对手没有建筑了", "win", 1))
        reason, bot, g = self.go(script)
        self.assertEqual((bot.run_stats["result"], g.ended), ("win", 0))

    def test_lane_unavailable_is_not_a_bot_error(self):
        def script(t):
            return {"clock": running(t), "mine": 3, "foes": 2}
        reason, bot, g = self.go(script, bot=TickBot(raise_lane_after=10))
        self.assertEqual(reason, "游戏 30 秒不接受命令（在收尾？）")
        self.assertEqual(bot.run_stats["errors"], 0)
        self.assertGreater(B.time.t - 1000.0, 40)            # 不是 20 拍（4 秒）就停

    def test_rpg_mode_does_not_judge(self):
        # judge=False（RPG / 玩伴）：建筑、单位、对面建筑都没了也不判胜负、不发结束令，只按时钟停太久收工
        def script(t):
            return {"clock": running(min(t, 30)), "mine": 0 if t > 5 else 3, "foes": 0 if t > 10 else 2,
                    "units": 0 if t > 15 else 4}
        reason, bot, g = self.go(script, end_game=True, judge=False, stuck_s=60)
        self.assertTrue(reason.startswith("游戏时钟停了"), reason)
        self.assertEqual((bot.run_stats["result"], g.ended), ("unknown", 0))
        self.assertGreater(B.time.t - 1000.0, 89)

    def test_frozen_too_long_ends_without_result(self):
        def script(t):
            return {"clock": running(min(t, 5)), "mine": 3, "foes": 2}
        reason, bot, g = self.go(script, end_game=True, stuck_s=60)
        self.assertTrue(reason.startswith("游戏时钟停了"), reason)
        self.assertEqual((bot.run_stats["result"], g.ended), ("unknown", 0))   # 分不出胜负，不替人关游戏


if __name__ == "__main__":
    unittest.main()

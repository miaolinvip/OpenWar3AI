# -*- coding: utf-8 -*-
"""端到端冒烟：假快照 + 假通道 + 假游戏时钟，跑**真的** run_brain.play_match 一整局（开局 -> 每轮 step -> 心跳 -> 判胜 -> 收尾）。

09-19 之前主循环没有任何测试驱动过，而监督器每一局都走它：这里写错一处，5~11 号每局一开就「一局中途出错」。
顺带守着 steptime 接进主循环的几件事：心跳带计时摘要、steptime.jsonl 有分钟记录、state 带 stepMax、lesson 带 steptime、
通道请求真的经过了计时包装（instrument 包住的是 play_match 里那条通道）。

外部的东西全换成假的：游戏进程（pid_alive/read_pid）、拉模块、git 版本号、监督器的墙钟（monotonic 每次 +7 秒，让心跳几轮就到）；
租约表用真的 w3claim（一个不存在的 pid 名下），这样租约锁的计时也走真路径。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_brain as RB  # noqa: E402  （它把 war3-modules 加进 sys.path）
import fightodds  # noqa: E402
import modlog  # noqa: E402
import w3claim  # noqa: E402
import w3fast  # noqa: E402
import w3state  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

FAKE_PID = 999_983


class TimedLane:
    """冒烟测试的假快车道（09-23 P3：HUD、查询都走 w3fast.FastLane.commands；instrument 包的就是它）。"""

    def __init__(self, cmd):
        self.cmd = cmd

    def commands(self, calls):
        self.cmd.submits += 1
        return [w3fast.Receipt(c.op, 0, 1, None, 0, 0, (0,) * 8) for c in calls]


class TimedCmd(FakeCmd):
    def __init__(self):
        super().__init__()
        self.submits = 0
        self.lane = TimedLane(self)
        self.ctl = SimpleNamespace(action=lambda *a, **k: True, summary="")   # 问题快捷键走运行时动作 57


class Game:
    """游戏这一侧：时钟每读一次走 5 秒；60 秒起对面主城没了（-> 20 秒后判胜）。"""

    def __init__(self):
        self.t = 0.0
        self.cmd = TimedCmd()
        self.mine = [U(0x100, "htow", 4352, -2688), U(0x200, "ngol", 4352, -3200, owner=15)] + \
            [U(0x300 + i, "hpea", 4300 + i * 40, -2900, order=0x000D0032, has_task=True) for i in range(3)] + \
            [U(0x310 + i, "hpea", 4400, -2500) for i in range(2)]
        self.foe_town = [U(0x800, "hcas", -4352, 2944, owner=1)]
        self.foe_peons = [U(0x900 + i, "hpea", -4300 + i * 40, 2700, owner=1, order=0x000D0032, has_task=True) for i in range(5)]

    def snap(self):
        units = self.mine + (self.foe_town if self.t < 60 else []) + self.foe_peons
        return SimpleNamespace(units=units, me=0, gold=500, lumber=150, food_used=5, food_cap=12, in_game=True)


class FakeSession:
    def __init__(self, game):
        self.game, self.cmd = game, game.cmd

    def read(self):
        return self.game.snap()


class FakeClock:
    def __init__(self, game):
        self.game = game

    def read(self):
        self.game.t += 5.0
        return self.game.t


class PidClaims(w3claim.Claims):
    """真的租约表，挂在一个不存在的 pid 名下（w3claim 里写的是 Claims._Lock，所以要换成子类，不能换成 lambda）。"""

    def __init__(self, module, pid=None):
        super().__init__(module, pid=FAKE_PID)


class Mono:
    """监督器的墙钟：monotonic 每次 +7 秒（心跳 60 秒一行，几轮就到）；sleep 不睡。"""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        self.now += 7.0
        return self.now

    def sleep(self, s):
        pass

    def time(self):
        return time.time()


class TestPlayMatchSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = Game()
        game = self.game
        patches = [
            (w3state, "Session", lambda: FakeSession(game)),
            (fightodds, "GameClock", lambda ch: FakeClock(game)),
            (w3claim, "Claims", PidClaims),
            (modlog, "tee_stdout", lambda name: None),
            (RB, "BIN", self.tmp.name),
            (RB, "set_env", lambda d, p: {}),
            (RB, "start_modules", lambda env, d, ops=None: []),
            (RB, "pid_alive", lambda p: True),
            (RB, "read_pid", lambda i: FAKE_PID),
            (RB, "exe_name", lambda p: None),
            (RB, "kernel_version", lambda: {}),
            (RB, "time", Mono()),
        ]
        for obj, name, val in patches:
            self.addCleanup(setattr, obj, name, getattr(obj, name))
            setattr(obj, name, val)
        self.inst_dir = os.path.join(self.tmp.name, "inst77")
        os.makedirs(self.inst_dir)
        with open(os.path.join(self.inst_dir, "ops.json"), "w", encoding="utf-8") as fh:
            json.dump({"kill_game_after_end": False}, fh)

    def test_one_match_end_to_end(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            outcome = RB.play_match(77, FAKE_PID, 1, 40.0)
        log = out.getvalue()
        self.assertNotIn("一局中途出错", log, log[-3000:])
        self.assertEqual(outcome, "victory", log[-3000:])
        beats = [ln for ln in log.splitlines() if "心跳" in ln]
        self.assertTrue(beats, log[-2000:])
        self.assertIn("循环 ", beats[-1])
        self.assertIn("最慢一轮", beats[-1])
        self.assertIn("｜循环 ", beats[-1])                  # 接在心跳原有内容后面，没有替掉它
        self.assertIn("｜计数 ", beats[-1])
        self.assertGreater(self.game.cmd.submits, 0)

        with open(os.path.join(self.inst_dir, "steptime.jsonl"), encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh]
        minutes = [r for r in recs if r["kind"] == "minute"]
        self.assertTrue(minutes)
        self.assertTrue(minutes[0]["match"].startswith("match-"))
        self.assertGreater(sum(r["chan"]["calls"] for r in minutes), 0)     # 请求经过了 instrument 包的那条通道
        seen = set().union(*(r["sum"] for r in minutes))                      # 分钟记录的 sum 不截断：主循环每一段都出现过
        for sec in ("循环·其它", "读快照", "读时钟", "大脑", "感知·英雄等级", "世界模型", "游戏内按钮", "记录", "判局末",
                    "任务·build", "build·规划", "build·下令", "任务·tactics", "tactics·下令", "任务·gold"):
            self.assertIn(sec, seen)

        mdir = os.path.join(self.inst_dir, "matches")
        (name,) = [f for f in os.listdir(mdir) if f.endswith(".jsonl")]
        with open(os.path.join(mdir, name), encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh]
        states = [r["data"] for r in rows if r["kind"] == "state"]
        self.assertTrue(states)
        self.assertTrue(all("stepMax" in s and "stepSlow" in s for s in states))
        (lesson,) = [r["data"] for r in rows if r["kind"] == "lesson"]
        self.assertEqual(lesson["outcome"], "victory")
        st = json.loads(lesson["steptime"])
        self.assertGreater(st["loops"], 5)
        self.assertLessEqual(len(st["sum"]), 12)
        # 09-23 P3 之前最重的一段是「build·下令」（下建造令后 sleep 0.25 秒回头看）；W3P 同一帧回执之后那段 sleep 没了
        self.assertIn("build·下令", st["sum"])
        self.assertTrue(st["calls"])
        self.assertIn("finalized", [r["kind"] for r in rows])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""局末「卡住」判据按读数时刻计时（EndWatch）、开局设置 / 内核版本记进对局。

09-18 15:01~15:30 inst5 连续 11 局在半路被判「游戏时钟停了 5 秒」：大脑一轮 step 花了 5 秒以上，
原来拿「现在 - 时钟上次前进」量，step 的时间被算成了时钟不走。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_brain as RB  # noqa: E402

LIMIT = 5.0   # ops.json 里 end_stuck_s 的最小值，也是 09-18 实际用的值


def drive(watch, reads):
    """reads: [(读数时刻, 时钟走没走, 是不是坏读数)]；每次读完照监督器循环开头那样问一次，返回第一次判「卡住」的时刻。"""
    for now, advanced, bad in reads:
        watch.read(now, advanced=advanced, bad=bad)
        if watch.clock_stopped() > LIMIT or watch.bad_for() > LIMIT:
            return now
    return None


class TestEndWatch(unittest.TestCase):
    def test_slow_step_is_not_a_stopped_clock(self):
        # 040 局的形状：0.2 秒一读，中间有一轮 step 花了 6 秒，之后读回来的时钟照样在走
        w = RB.EndWatch(0.0)
        reads = [(0.2 * i, True, False) for i in range(1, 11)]          # 0.2 .. 2.0
        reads += [(8.0, True, False), (8.2, True, False), (8.4, True, False)]
        self.assertIsNone(drive(w, reads))
        self.assertEqual(w.clock_stopped(), 0.0)

    def test_frozen_clock_ends_after_limit(self):
        # 胜负对话框：快照是好的，只有时钟不走
        w = RB.EndWatch(0.0)
        reads = [(1.0, True, False)] + [(1.0 + 0.25 * i, False, False) for i in range(1, 40)]
        self.assertEqual(drive(w, reads), 6.25)          # 最后一次前进在 1.0；6.0 时正好 5 秒不算，下一读 6.25 超过

    def test_frozen_clock_across_slow_step(self):
        # 时钟真停了，读数之间又夹着一轮慢 step：两次读都没走、相隔超过 5 秒 —— 照样判停
        w = RB.EndWatch(0.0)
        self.assertEqual(drive(w, [(1.0, True, False), (1.2, False, False), (7.0, False, False)]), 7.0)

    def test_paused_panel_resets_clock(self):
        w = RB.EndWatch(0.0)
        w.read(1.0, advanced=True, bad=False)
        w.paused(30.0)                                   # 人开着任务面板 29 秒
        w.read(30.2, advanced=False, bad=False)
        self.assertLess(w.clock_stopped(), LIMIT)

    def test_bad_streak_measured_between_reads(self):
        # 一份缩水的快照、然后 step 慢了 6 秒、下一份正常 —— 不算连续坏
        w = RB.EndWatch(0.0)
        self.assertIsNone(drive(w, [(1.0, True, True), (7.0, True, False), (7.2, True, False)]))
        # 坏读数一直坏（时钟照走，只测这一道）：从第一次坏的读数量起
        w = RB.EndWatch(0.0)
        hit = drive(w, [(1.0, True, False)] + [(1.0 + 0.25 * i, True, True) for i in range(1, 40)])
        self.assertEqual(hit, 6.5)                       # 第一次坏在 1.25；6.25 正好 5 秒不算，下一读 6.5 超过


class TestMatchSetup(unittest.TestCase):
    def test_launcher_line_wins_over_next_game(self):
        with tempfile.TemporaryDirectory() as d:
            inst_dir = os.path.join(d, "inst7")
            os.makedirs(inst_dir)
            with open(os.path.join(inst_dir, "next_game.json"), "w", encoding="utf-8") as fh:
                fh.write('{"race": 1, "enemyRace": 2, "difficulty": 2, "speed": 300, "map": "X:\\\\Maps\\\\(2)EchoIsles.w3x"}')
            os.makedirs(os.path.join(d, "farm"))
            with open(os.path.join(d, "farm", "inst7.out"), "wb") as fh:
                fh.write("opening: 种族 我方=1 敌方=4（覆盖 槽0=1 槽1=4）｜难度=3(字段1) 覆盖0 次\r\n".encode("utf-8"))
            old = RB.BIN
            RB.BIN = d
            try:
                s = RB.match_setup(inst_dir, 7)
            finally:
                RB.BIN = old
            self.assertEqual(s, {"map": "X:\\Maps\\(2)EchoIsles.w3x", "speed": 300, "race": 1, "enemyRace": 4, "difficulty": 3})

    def test_missing_files_give_empty(self):
        with tempfile.TemporaryDirectory() as d:
            old = RB.BIN
            RB.BIN = d
            try:
                self.assertEqual(RB.match_setup(d, 9), {})
            finally:
                RB.BIN = old


class TestKernelVersion(unittest.TestCase):
    def test_shape(self):
        v = RB.kernel_version()
        if not v:                                        # 不在 git 仓库里（例如副本目录）就是空的，不报错
            return
        self.assertGreater(v["kernel"], 0)
        self.assertRegex(v["kernelRev"], r"^[0-9a-f]{7,}$")
        self.assertIsInstance(v["kernelDirty"], bool)

    def test_combat_version_shape(self):
        v = RB.combat_version()
        if not v:
            return
        self.assertRegex(v["combatVersion"], r"^creep-output-retreat-v\d+$")
        self.assertRegex(v["combatRev"], r"^[0-9a-f]{7,}$")
        self.assertIsInstance(v["combatDirty"], bool)

    def test_own_exe_name(self):
        self.assertTrue(RB.exe_name(os.getpid()).lower().startswith("python"))
        self.assertIsNone(RB.exe_name(0x7FFFFFF0))       # 不存在的 pid


if __name__ == "__main__":
    unittest.main()

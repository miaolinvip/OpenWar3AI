# -*- coding: utf-8 -*-
"""问题快捷键（brain/issuemark.py + DLL 动作 57）：读 DLL 的按键记录、下发键、记标记；ops 的快捷键选项；Hud.poll_pause。
09-19 游戏内按一个快捷键，自动记下当前进程 ID、对局场次和时间点，同时暂停游戏；再按一次继续。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import hud as HUD  # noqa: E402
from brain import issuemark as IM  # noqa: E402
from brain import ops as OPS  # noqa: E402


class FakeCh:
    def __init__(self, ok=True):
        self.ok = ok
        self.actions = []

    def action(self, action, command=0, timeout=5.0):
        self.actions.append((action, command))
        return self.ok


class TestPoll(unittest.TestCase):
    def test_reads_new_lines_only_and_keeps_half_line(self):
        with tempfile.TemporaryDirectory() as d:
            log = IM.hotkey_log(d, 4242)
            with open(log, "w", encoding="ascii") as fh:
                fh.write("100 pause\n200 resume\n")             # 接管之前按的：不算
            h = IM.IssueHotkey(None, d, 4242, 5, os.path.join(d, "inst5"))
            self.assertEqual(h.poll(), [])
            with open(log, "a", encoding="ascii") as fh:
                fh.write("300 pause\n400 res")                  # DLL 正写到一半
            self.assertEqual(h.poll(), ["pause"])
            with open(log, "a", encoding="ascii") as fh:
                fh.write("ume\n")
            self.assertEqual(h.poll(), ["resume"])
            self.assertEqual(h.poll(), [])
            self.assertEqual(h.presses, 2)

    def test_no_file_yet(self):
        with tempfile.TemporaryDirectory() as d:
            h = IM.IssueHotkey(None, d, 1, 5, d)
            self.assertEqual(h.poll(), [])
            with open(IM.hotkey_log(d, 1), "w", encoding="ascii") as fh:
                fh.write("5 pause\n")                          # 第一次按才建文件：从头读
            self.assertEqual(h.poll(), ["pause"])


class TestConfigure(unittest.TestCase):
    def test_sends_once_and_on_change(self):
        ch = FakeCh()
        h = IM.IssueHotkey(ch, tempfile.gettempdir(), 1, 5, tempfile.gettempdir())
        h.configure("pause")
        h.configure("pause")
        h.configure("scroll")
        h.configure("off")
        self.assertEqual(ch.actions, [(57, 2 | (0x13 << 8)), (57, 2 | (0x91 << 8)), (57, 2)])

    def test_retries_after_failure(self):
        ch = FakeCh(ok=False)
        h = IM.IssueHotkey(ch, tempfile.gettempdir(), 1, 5, tempfile.gettempdir())
        h.configure("pause")
        h.configure("pause")
        self.assertEqual(len(ch.actions), 2)


class TestMark(unittest.TestCase):
    def test_mark_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            inst_dir = os.path.join(d, "inst7")
            h = IM.IssueHotkey(None, d, 999, 7, inst_dir)
            m1 = h.mark("match-20260919-120000-003-abcdef12.jsonl", 3, 205.37, exe="War3-7.exe", extra={"kernel": 64})
            h.mark("match-20260919-120000-003-abcdef12.jsonl", 3, 300.0)
            rows = [json.loads(x) for x in open(os.path.join(inst_dir, IM.MARKS_FILE), encoding="utf-8")]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], m1)
            self.assertEqual((m1["inst"], m1["pid"], m1["game"], m1["gameTime"], m1["exe"], m1["kernel"], m1["source"]),
                             (7, 999, 3, 205.4, "War3-7.exe", 64, "hotkey"))
            self.assertNotEqual(rows[0]["id"], rows[1]["id"])


class TestOpsChoice(unittest.TestCase):
    def test_schema_and_coerce(self):
        row = next(r for r in OPS.schema() if r["key"] == "issue_hotkey")
        self.assertEqual(row["type"], "choice")
        self.assertEqual(row["default"], "pause")
        self.assertEqual(row["options"], ["pause", "scroll", "insert", "off"])
        self.assertEqual(row["labels"]["pause"], "Pause/Break")
        self.assertEqual(OPS.coerce("issue_hotkey", "scroll"), "scroll")
        with self.assertRaises(KeyError):
            OPS.coerce("issue_hotkey", "F13")

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            r = OPS.save(d, {"issue_hotkey": "insert"})
            self.assertEqual(r["overrides"], {"issue_hotkey": "insert"})
            self.assertEqual(OPS.load(d)["issue_hotkey"], "insert")
            r = OPS.save(d, {"issue_hotkey": "pause"})                 # 回到默认不存
            self.assertEqual(r["overrides"], {})


class TestPollPause(unittest.TestCase):
    def test_throttled_and_fault_tolerant(self):
        now = [100.0]
        h = HUD.Hud(None, clock=lambda: now[0])
        answers = [True, False]
        calls = []

        def fake():
            calls.append(now[0])
            v = answers.pop(0)
            if v is None:
                raise OSError("通道忙")
            return v
        h.paused_by_user = fake
        every = HUD.PAUSE_EVERY                                        # 09-19 起 1 秒（steptime 实测后从 0.5 放宽），按常量算
        self.assertTrue(h.poll_pause())
        now[0] += 0.4 * every
        self.assertTrue(h.poll_pause())                                # PAUSE_EVERY 内不再问，沿用上次
        now[0] += 0.8 * every
        self.assertFalse(h.poll_pause())
        self.assertEqual(len(calls), 2)
        answers.append(None)
        now[0] += 2 * every
        self.assertFalse(h.poll_pause())                               # 问出错：当没暂停，不抛


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""问题备忘（farsight/server/issues.py）：收快捷键标记（增量、去重、删了不回来）、补描述自动转「待处理」、
附近事件按游戏秒取对局记录并换算墙钟捞日志、日志二分查找。
09-19 需求：记录已发现问题的备忘，记录时可选进程和时间点（关联该时间点附近的事件）。
跑法：cd farsight && python -m unittest tests.test_issues -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import issues as ISS  # noqa: E402

MATCH = "match-20260919-120000-007-abcdef12.jsonl"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (C.BIN, ISS.STORE, C.inst_dir)
        C.BIN = self.tmp.name
        ISS.STORE = os.path.join(self.tmp.name, "issues.json")
        C.inst_dir = lambda n: os.path.join(self.tmp.name, f"inst{n}")
        os.makedirs(os.path.join(self.tmp.name, "inst5", "matches"))

    def tearDown(self):
        C.BIN, ISS.STORE, C.inst_dir = self.saved
        self.tmp.cleanup()

    def mark(self, mid, t=100.0, n=5):
        with open(os.path.join(self.tmp.name, f"inst{n}", ISS.MARKS_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": mid, "at": 1789766000.0, "inst": n, "pid": 4242, "exe": f"War3-{n}.exe",
                                 "match": MATCH, "game": 7, "gameTime": t, "source": "hotkey", "kernel": 64}) + "\n")


class TestIngest(Base):
    def test_four_digit_match_number_is_not_truncated(self):
        self.assertEqual(ISS._game_no("match-20260920-130000-1007-11223344.jsonl"), 1007)

    def test_marks_become_drafts_once(self):
        self.mark("m1", 100.0)
        self.mark("m2", 250.5)
        r = ISS.listing()
        self.assertEqual(r["pending"], 2)
        a = next(x for x in r["issues"] if x["markId"] == "m1")
        self.assertEqual((a["status"], a["inst"], a["pid"], a["game"], a["gameTime"], a["match"], a["kernel"]),
                         ("draft", 5, 4242, 7, 100.0, MATCH, 64))
        self.assertEqual(len(ISS.listing()["issues"]), 2)            # 再列一次不重复收

    def test_deleted_issue_not_recreated(self):
        self.mark("m1")
        iid = ISS.listing()["issues"][0]["id"]
        self.assertTrue(ISS.delete(iid))
        self.assertEqual(ISS.listing()["issues"], [])
        os.remove(os.path.join(self.tmp.name, "inst5", ISS.MARKS_FILE))  # 文件删了重建、同一个标记又出现
        self.mark("m1")
        self.assertEqual(ISS.listing()["issues"], [])

    def test_half_written_line_waits(self):
        path = os.path.join(self.tmp.name, "inst5", ISS.MARKS_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"id": "m9", "inst": 5, "match": "' + MATCH)
        self.assertEqual(ISS.listing()["issues"], [])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('", "gameTime": 3}\n')
        self.assertEqual(len(ISS.listing()["issues"]), 1)


class TestEdit(Base):
    def test_note_moves_draft_to_open(self):
        self.mark("m1")
        iid = ISS.listing()["issues"][0]["id"]
        x = ISS.update(iid, {"note": "英雄一个人冲进野怪堆"})
        self.assertEqual(x["status"], "open")
        x = ISS.update(iid, {"status": "fixed"})
        self.assertEqual(x["status"], "fixed")
        with self.assertRaises(ValueError):
            ISS.update(iid, {"status": "whatever"})
        with self.assertRaises(KeyError):
            ISS.update("i-nope", {"note": "x"})

    def test_create_defaults_to_latest_match(self):
        write_match(os.path.join(self.tmp.name, "inst5", "matches", MATCH))
        x = ISS.create({"inst": 5, "title": "开局农民发呆"})
        self.assertEqual((x["match"], x["game"], x["status"], x["source"]), (MATCH, 7, "open", "web"))
        self.assertEqual(x["gameTime"], 60)                            # 那一局记到的最后一个游戏秒
        with self.assertRaises(ValueError):
            ISS.create({"title": "没选实例"})


def write_match(path):
    """一局：开局引擎时钟 2 秒；每 10 游戏秒一份状态；3 倍速（墙钟 1 秒 = 游戏 3 秒）；动作按相对开局的游戏秒。"""
    w0 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    rows = []
    for g in range(0, 61, 10):
        rows.append({"kind": "state", "gameElapsedSec": g + 2, "utc": datetime.fromtimestamp(w0 + g / 3, timezone.utc).isoformat(),
                     "data": {"gold": 100 + g, "lumber": 50, "foodUsed": 5, "foodCap": 12, "army": g // 10}})
    for g in (5, 29, 31, 55):
        rows.append({"kind": "action", "gameElapsedSec": g, "utc": datetime.fromtimestamp(w0 + g / 3, timezone.utc).isoformat(),
                     "data": {"text": f"动作@{g}"}})
    with open(path, "w", encoding="utf-8") as fh:
        for r in sorted(rows, key=lambda r: r["utc"]):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return w0


class TestContext(Base):
    def test_window_and_logs(self):
        w0 = write_match(os.path.join(self.tmp.name, "inst5", "matches", MATCH))
        # 监督器日志：本地时间；游戏 30 秒 = 墙钟 w0 + 10 秒
        with open(os.path.join(self.tmp.name, "inst5", "brain.log"), "w", encoding="utf-8") as fh:
            for sec, text in ((0, "开局"), (9, "近处的一行"), (11, "近处第二行"), (19, "太远了")):
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(w0 + sec)) + f" {text}\n")
        c = ISS.context(5, MATCH, 30, 6)
        self.assertEqual([a["text"] for a in c["actions"]], ["动作@29", "动作@31"])
        self.assertEqual([s["t"] for s in c["states"]], [20, 30, 40])  # 状态按相对开局（引擎时钟减开局 2 秒），窗口外放 10 秒
        self.assertEqual([x["text"] for x in c["logs"]["brain"]["lines"]], ["近处的一行", "近处第二行"])
        self.assertEqual(ISS.context(5, MATCH, None, 0)["t"], 60)

    def test_log_binary_search_big_file(self):
        path = os.path.join(self.tmp.name, "big.log")
        base = time.mktime((2026, 9, 19, 10, 0, 0, 0, 0, -1))
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(20000):                                     # 20000 秒，每秒一行，约 700 KB
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base + i)) + f" 第{i}行 " + "x" * 10 + "\n")
        rows = ISS._log_lines(path, base + 15000, base + 15002)
        # 上沿多给 1 秒：日志时间只到整秒，窗口上沿带小数（_log_lines 里 cur > t_to + 1 才停）
        self.assertEqual([r["text"].split()[0] for r in rows], ["第15000行", "第15001行", "第15002行", "第15003行"])


if __name__ == "__main__":
    unittest.main()

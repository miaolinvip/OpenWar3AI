# -*- coding: utf-8 -*-
"""对局记录格式：紧凑 JSON 不转义中文、文件名、同 tick 去重、planVer/outcome 在、外围工具的字节匹配能命中。"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import world as W  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain.recorder import MatchRecorder  # noqa: E402


def U(addr, t, owner=0, x=0.0, y=0.0):
    m = float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m, hp_max=m)


class TestRecorder(unittest.TestCase):
    def test_format(self):
        with tempfile.TemporaryDirectory() as d:
            r = MatchRecorder(d, game=2, now=datetime(2026, 9, 17, 23, 5, 9))
            self.assertRegex(r.name, r"^match-20260917-230509-002-[0-9a-f]{8}\.jsonl$")
            w = W.build_world([U(1, "htow"), U(2, "hpea"), U(3, "hfoo"), U(10, "ugho", 1, 5000, 0)], 0, 123.4, 300, 50, 3, 12)
            r.start(2, 4)
            r.experiment()
            self.assertTrue(r.state(7, w))
            self.assertFalse(r.state(7, w))            # 同 tick 第二条不写
            self.assertTrue(r.intel(7, w))
            r.action(7, "出击（AMAI）：打分矿")
            r.result(9, "conceded", 2.5, peakArmy=3)
            r.finalize(9)
            raw = open(r.path, "rb").read()
            lines = raw.decode("utf-8").splitlines()
            self.assertEqual(len(lines), 7)
            self.assertIn(b'"planVer":1000', raw)                      # data_index 的字节正则
            self.assertIn(b'"kind":"result"', raw)
            self.assertIn(b'"kind":"action"', raw)                     # known_spots_seed 的子串预过滤
            self.assertIn("出击（AMAI）".encode("utf-8"), raw)          # 中文不转义
            self.assertNotIn(b'": ', raw)
            st = json.loads(lines[2])
            self.assertEqual((st["kind"], st["tick"], st["gameElapsedSec"], st["data"]["units"]), ("state", 7, 123, "hfoo=1,hpea=1,htow=1"))
            self.assertIsInstance(st["data"]["gold"], int)
            it = json.loads(lines[3])
            self.assertEqual((it["data"]["composition"], it["data"]["race"]), ("ughox1", "undead"))
            self.assertRegex(it["data"]["food"], r"^\d+/\d+$")
            r.action(10, "关了之后不再写")
            self.assertEqual(len(open(r.path, encoding="utf-8").read().splitlines()), 7)


if __name__ == "__main__":
    unittest.main()

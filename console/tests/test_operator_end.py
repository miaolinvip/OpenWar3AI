# -*- coding: utf-8 -*-
"""结束这一局的条子要带来源（instances.mark_operator_end / who_from）。

09-23 对局结果记成「手动结束」，却查不出是谁发的结束 —— 网页点的和脚本发的要能分开。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402


def req(ua, host="127.0.0.1"):
    return SimpleNamespace(headers={"user-agent": ua}, client=SimpleNamespace(host=host))


class Who(unittest.TestCase):
    def test_browser_and_script_are_told_apart(self):
        self.assertTrue(I.who_from(req("Mozilla/5.0 (Windows NT 10.0) Chrome/140")).startswith("网页·127.0.0.1·"))
        self.assertTrue(I.who_from(req("curl/8.4.0")).startswith("脚本·127.0.0.1·"))
        self.assertEqual(I.who_from(req("", host="10.0.0.9")), "无 UA·10.0.0.9")
        self.assertEqual(I.who_from(None), "unknown")


class Note(unittest.TestCase):
    def test_the_note_records_who_and_when(self):
        with tempfile.TemporaryDirectory() as d:
            old = C.inst_dir
            C.inst_dir = lambda n: d                                   # noqa: ARG005
            try:
                I.mark_operator_end(1, "console-end-game", "网页·127.0.0.1·Mozilla/5.0")
            finally:
                C.inst_dir = old
            got = json.load(open(os.path.join(d, "end_request.json"), encoding="utf-8"))
            self.assertEqual(got["why"], "console-end-game")
            self.assertEqual(got["who"], "网页·127.0.0.1·Mozilla/5.0")
            self.assertRegex(got["at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
            self.assertGreater(got["wall"], 0)


if __name__ == "__main__":
    unittest.main()

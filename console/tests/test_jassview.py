# -*- coding: utf-8 -*-
"""远见 JASS 控制台后端（09-24）：目录的分类计数对得上总数；游戏没在跑时明确报错、不去枚举全机进程。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import jassview as JV  # noqa: E402


class JassViewTest(unittest.TestCase):
    def test_natives(self):
        d = JV.natives()
        self.assertEqual(sum(c["count"] for c in d["categories"]), d["total"])
        self.assertGreater(d["total"], 1200)
        self.assertGreater(d["verified"], 50)
        v = JV.natives("TextTag", "visual")
        self.assertTrue(v["natives"] and all(n["category"] == "visual" for n in v["natives"]))
        self.assertTrue(all(n["zh"] and n["en"] and n["desc"] == n["zh"] for n in v["natives"]))

    def test_natives_english(self):
        d = JV.natives("floating text", lang="en")
        self.assertIn("CreateTextTag", [n["name"] for n in d["natives"]])
        self.assertTrue(all(n["desc"] == n["en"] for n in d["natives"]))
        self.assertTrue(all(not any("一" <= ch <= "鿿" for ch in c["name"]) for c in d["categories"]))

    def test_no_game(self):
        saved = C.FARM_DIR
        with tempfile.TemporaryDirectory() as tmp:
            C.FARM_DIR = tmp
            try:
                r = JV.run(987, "GetLocalPlayer()")
                self.assertFalse(r["ok"])
                self.assertIn("没在跑", r["error"])
                with open(os.path.join(tmp, "inst987.out"), "w", encoding="utf-8") as fh:
                    fh.write("control channel: ok\nWAR3_PID=999999\n")       # 进程早没了
                self.assertIsNone(JV._game_pid(987))
            finally:
                C.FARM_DIR = saved


if __name__ == "__main__":
    unittest.main()

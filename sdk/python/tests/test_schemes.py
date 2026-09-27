# -*- coding: utf-8 -*-
"""AI 方案库（09-24）：导入别人的 zip（挡住 zip slip、没说明书、入口缺失）、不影响「我的方案」、信任、复制、导出再导入、
载入 Bot 子类、战绩统计。方案目录全换到临时目录，不碰真实的 schemes/。"""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402
from openwar3 import schemes as S  # noqa: E402

BOT = """from openwar3 import Bot
class MyBot(Bot):
    name = "mine"
    def on_tick(self, g):
        pass
"""


def zip_of(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def manifest(**kw):
    m = {"format": 1, "id": "fast-rush", "name": "速推", "version": "1.0.0", "author": "zhangsan", "kind": "bot", "entry": "rush.py"}
    m.update(kw)
    return json.dumps(m, ensure_ascii=False)


class Schemes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (w3paths.SCHEMES_MINE, w3paths.SCHEMES_INSTALLED)
        w3paths.SCHEMES_MINE = os.path.join(self.tmp.name, "mine")
        w3paths.SCHEMES_INSTALLED = os.path.join(self.tmp.name, "installed")

    def tearDown(self):
        w3paths.SCHEMES_MINE, w3paths.SCHEMES_INSTALLED = self.saved
        self.tmp.cleanup()

    def test_builtins_listed(self):
        refs = {s["ref"]: s for s in S.list_schemes()}
        self.assertEqual(refs["builtin/xwar3"]["kind"], "brain")
        self.assertTrue(all(refs[f"builtin/{x}"]["kind"] == "bot" for x in ("hello", "rush", "macro", "micro")))

    def test_install_untrusted_then_trust(self):
        s = S.install_zip(zip_of({"scheme.json": manifest(), "rush.py": BOT}))
        self.assertEqual((s["ref"], s["trusted"]), ("installed/fast-rush", False))
        S.trust("installed/fast-rush")
        self.assertTrue(S.get("installed/fast-rush")["trusted"])
        # 重装（替换）后信任作废；包里夹带的 .trusted 不算数
        s = S.install_zip(zip_of({"x/scheme.json": manifest(version="1.1.0"), "x/rush.py": BOT, "x/.trusted": "yes"}), replace=True)
        self.assertEqual((s["version"], s["trusted"]), ("1.1.0", False))

    def test_install_refuses_duplicates_bad_paths_and_missing_parts(self):
        S.install_zip(zip_of({"scheme.json": manifest(), "rush.py": BOT}))
        with self.assertRaisesRegex(S.SchemeError, "已经装过"):
            S.install_zip(zip_of({"scheme.json": manifest(), "rush.py": BOT}))
        with self.assertRaisesRegex(S.SchemeError, "不安全"):
            S.install_zip(zip_of({"scheme.json": manifest(id="evil"), "rush.py": BOT, "../../evil.py": "x"}))
        with self.assertRaisesRegex(S.SchemeError, "找不到 scheme.json"):
            S.install_zip(zip_of({"rush.py": BOT}))
        with self.assertRaisesRegex(S.SchemeError, "入口文件"):
            S.install_zip(zip_of({"scheme.json": manifest(id="noentry"), "other.py": BOT}))
        with self.assertRaisesRegex(S.SchemeError, "id"):
            S.install_zip(zip_of({"scheme.json": manifest(id="Bad Id!"), "rush.py": BOT}))
        with self.assertRaisesRegex(S.SchemeError, "不是 zip"):
            S.install_zip(b"not a zip")

    def test_installed_never_touches_mine(self):
        S.fork("builtin/hello", "fast-rush", "我的速推")                  # 我自己也有一个同名的
        S.install_zip(zip_of({"scheme.json": manifest(), "rush.py": BOT}))  # 装别人的同名方案
        mine, other = S.get("mine/fast-rush"), S.get("installed/fast-rush")
        self.assertEqual((mine["author"], other["author"]), ("我", "zhangsan"))
        self.assertNotEqual(mine["dir"], other["dir"])
        S.delete("installed/fast-rush")
        self.assertEqual(S.get("mine/fast-rush")["name"], "我的速推")      # 删别人的不影响我的

    def test_fork_export_reimport_roundtrip(self):
        s = S.fork("builtin/micro", "my-micro")
        self.assertTrue(s["trusted"])
        self.assertIn("forked_from", s)
        self.assertEqual(s["i18n"]["en"]["name"], "Micro example (micro_bot) (my copy)")   # 英文名跟着加「副本」，简介原样带过去
        self.assertTrue(s["i18n"]["en"]["description"])
        named = S.fork("builtin/hello", "my-hello", "我的经济")
        self.assertNotIn("name", named["i18n"]["en"])                  # 起了新名字就不留旧译名（英文界面显示新名字）
        for f in ("micro_bot.py", "macro_bot.py", "hello_bot.py"):             # 示例互相 import：一起复制
            self.assertTrue(os.path.isfile(os.path.join(s["dir"], f)))
        name, data = S.export_zip("mine/my-micro")
        self.assertTrue(name.startswith("my-micro-"))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("my-micro/scheme.json", z.namelist())
        again = S.install_zip(data)
        self.assertEqual((again["ref"], again["entry"]), ("installed/my-micro", "micro_bot.py"))
        with self.assertRaisesRegex(S.SchemeError, "已经有"):
            S.fork("builtin/micro", "my-micro")
        with self.assertRaisesRegex(S.SchemeError, "不能删"):
            S.delete("builtin/micro")

    def test_load_bot_class(self):
        S.install_zip(zip_of({"scheme.json": manifest(), "rush.py": BOT}))
        cls = S.load_bot_class(S.get("installed/fast-rush"))
        self.assertEqual((cls.__name__, cls.name), ("MyBot", "mine"))
        micro = S.load_bot_class(S.get("builtin/micro"))
        self.assertEqual(micro.__name__, "MicroBot")

    def test_broken_scheme_listed_with_error(self):
        os.makedirs(os.path.join(w3paths.SCHEMES_MINE, "broken"))
        item = next(s for s in S.list_schemes() if s["ref"] == "mine/broken")
        self.assertIn("scheme.json", item["error"])
        with self.assertRaisesRegex(S.SchemeError, "坏了"):
            S.get("mine/broken")

    def test_stats(self):
        rows = [{"scheme": "mine/a", "result": "win", "gameMinutes": 20, "end": 5},
                {"scheme": "mine/a", "result": "loss", "gameMinutes": 30, "end": 9},
                {"scheme": "mine/a", "result": "unknown", "gameMinutes": 10, "end": 7},
                {"scheme": "mine/a", "result": "loss", "gameMinutes": 20, "end": 8, "midGame": True}]   # 半路接手的输局不算负
        st = S.stats(rows)["mine/a"]
        self.assertEqual((st["games"], st["wins"], st["losses"], st["unknown"], st["midGame"], st["avgMinutes"], st["last"]),
                         (4, 1, 1, 1, 1, 20.0, 9))

    def test_bad_ref(self):
        for ref in ("xwar3", "other/x", "mine/Bad"):
            with self.assertRaises(S.SchemeError):
                S.parse_ref(ref)


if __name__ == "__main__":
    unittest.main()

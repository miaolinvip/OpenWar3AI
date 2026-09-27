# -*- coding: utf-8 -*-
"""模块永久日志 modlog：stdout 照常、整行加日期进 .log；读的一侧 .log 优先、旧 .hist/.out 不重复。

09-16：对局中 ai_run.log 被 Tee-Object 独占，模块 note() 追加失败被吞（[保命] 415 行 vs .hist 30310 行）。
"""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modlog  # noqa: E402


def put(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class DayTeeTest(unittest.TestCase):
    def test_stdout_unchanged_and_whole_lines_dated(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mod_x.log")
            inner = io.StringIO()
            tee = modlog.DayTee(inner, p, clock=lambda: datetime(2026, 9, 16, 4, 30, 1))
            print("04:30:01 心跳 施法 3", file=tee)
            tee.write("04:30:01 半")
            self.assertEqual(inner.getvalue(), "04:30:01 心跳 施法 3\n04:30:01 半")
            self.assertEqual(open(p, encoding="utf-8").read(), "2026-09-16 04:30:01 心跳 施法 3\n")
            tee.write("行\n\n")
            self.assertEqual(open(p, encoding="utf-8").read(),
                             "2026-09-16 04:30:01 心跳 施法 3\n2026-09-16 04:30:01 半行\n")

    def test_unwritable_log_does_not_break_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            inner = io.StringIO()
            tee = modlog.DayTee(inner, d, clock=lambda: datetime(2026, 9, 16))   # 目录，打不开
            print("04:30:01 x", file=tee)
            self.assertEqual(inner.getvalue(), "04:30:01 x\n")


class ReadTest(unittest.TestCase):
    def test_log_wins_legacy_only_before_it(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "m.hist"), "2026-09-16 03:00:00 旧 a\n2026-09-16 04:00:00 重复 b\n")
            put(os.path.join(d, "m.log"), "2026-09-16 03:30:00 新 c\n2026-09-16 04:00:00 重复 b\n")
            got = modlog.read("m", base=d)
            self.assertEqual([t for _, t in got], ["旧 a", "新 c", "重复 b"])

    def test_out_dated_by_ref_across_midnight(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "m.out"), "23:55:00 前\n00:10:00 后\nTraceback 不带时间\n")
            got = modlog.read("m", ref=datetime(2026, 9, 15, 23, 50), base=d)
            self.assertEqual(got, [(datetime(2026, 9, 15, 23, 55), "前"),
                                   (datetime(2026, 9, 16, 0, 10), "后")])

    def test_nothing_there(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(modlog.read("m", base=d), [])


class InstanceBaseTest(unittest.TestCase):
    """09-16 多开：四个实例的模块日志原来都追加进 war3-modules/<模块>.log、不带实例号，复盘按墙钟对齐时四份加在一起。"""

    def setUp(self):
        self.old = os.environ.pop("WAR3_INST_DIR", None)

    def tearDown(self):
        os.environ.pop("WAR3_INST_DIR", None)
        if self.old is not None:
            os.environ["WAR3_INST_DIR"] = self.old

    def test_default_base_follows_inst_dir(self):
        self.assertEqual(modlog.default_base(), modlog.HERE)
        with tempfile.TemporaryDirectory() as d:
            os.environ["WAR3_INST_DIR"] = d
            self.assertEqual(modlog.default_base(), d)
            self.assertTrue(modlog.append("cmdlog", "mod_combat 0x1 0x000D0003 move_to ok",
                                          clock=lambda: datetime(2026, 9, 16, 21, 50)))
            self.assertEqual(open(os.path.join(d, "cmdlog.log"), encoding="utf-8").read(),
                             "2026-09-16 21:50:00 mod_combat 0x1 0x000D0003 move_to ok\n")
            self.assertEqual(modlog.read("cmdlog"), [(datetime(2026, 9, 16, 21, 50), "mod_combat 0x1 0x000D0003 move_to ok")])
        os.environ["WAR3_INST_DIR"] = os.path.join(tempfile.gettempdir(), "没有这个目录-modlog")
        self.assertEqual(modlog.default_base(), modlog.HERE)          # 设了但目录不存在 -> 不往不存在的地方写

    def test_base_for_match_dir(self):
        self.assertEqual(modlog.base_for(r"D:\x\build_intel4\inst2\matches"), os.path.abspath(r"D:\x\build_intel4\inst2"))
        self.assertEqual(modlog.base_for(r"D:\x\build_intel4\matches"), modlog.HERE)
        self.assertEqual(modlog.base_for(r"D:\x\build_intel4\instx\matches"), modlog.HERE)

    def test_two_instances_do_not_mix(self):
        with tempfile.TemporaryDirectory() as root:
            a, b = os.path.join(root, "inst1"), os.path.join(root, "inst2")
            os.makedirs(a); os.makedirs(b)
            # inst1 的旧数据在 .hist（run-ai 按实例重定向攒下来的），.log 从切换那一刻开始
            put(os.path.join(a, "mod_combat.hist"), "2026-09-16 21:30:00 心跳：救下 1 个\n")
            put(os.path.join(a, "mod_combat.log"), "2026-09-16 21:55:00 心跳：救下 2 个\n")
            put(os.path.join(b, "mod_combat.log"), "2026-09-16 21:55:00 心跳：救下 9 个\n")
            got = modlog.read("mod_combat", base=modlog.base_for(os.path.join(a, "matches")))
            self.assertEqual([t for _, t in got], ["心跳：救下 1 个", "心跳：救下 2 个"])


if __name__ == "__main__":
    unittest.main()

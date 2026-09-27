# -*- coding: utf-8 -*-
"""对局运行开关 ops.json：默认值、只存改过的、越界夹住、不认识的键报错；监督器按开关拉模块。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import ops as OPS  # noqa: E402
import run_brain as RB  # noqa: E402


class TestOps(unittest.TestCase):
    def test_defaults_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            v = OPS.load(d)
            self.assertTrue(v["auto_gg"] and v["ai_enabled"] and v["kill_game_after_end"])
            r = OPS.save(d, {"auto_gg": False, "end_stuck_s": 999, "gg_strength_ratio": 0.3})
            self.assertEqual(r["overrides"], {"auto_gg": False, "end_stuck_s": 120})   # 0.3 是默认值不存；999 夹到 120
            self.assertFalse(OPS.load(d)["auto_gg"])
            r = OPS.save(d, {"auto_gg": None})
            self.assertEqual(r["overrides"], {"end_stuck_s": 120})
            with self.assertRaises(KeyError):
                OPS.save(d, {"no_such": 1})

    def test_start_modules_respects_switches(self):
        started = []

        class P:
            pid = 1

        orig = RB.subprocess.Popen
        RB.subprocess.Popen = lambda cmd, **kw: started.append(os.path.basename(cmd[1])) or P()
        try:
            with tempfile.TemporaryDirectory() as d:
                procs = RB.start_modules({}, d, dict(OPS.DEFAULTS, mod_fight=False))
                self.assertEqual(started, ["mod_combat.py", "mod_scavenge.py", "mod_spy.py"])
                for _n, _p, out in procs:
                    out.close()
                started.clear()
                self.assertEqual(RB.start_modules({}, d, dict(OPS.DEFAULTS, ai_enabled=False)), [])
                self.assertEqual(started, [])
        finally:
            RB.subprocess.Popen = orig


class TestKillGame(unittest.TestCase):
    def test_foreign_pid_is_spared(self):
        # ★ 09-19 pid 复用：旧局的游戏 pid 被系统分给了别的程序，按 pid 直接杀就误杀了它 —— 名字不是 War3 就不动
        import subprocess
        import time
        p = subprocess.Popen(["ping", "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL, creationflags=0x08000000)
        try:
            r = OPS.kill_game(99, p.pid)
            time.sleep(0.3)
            self.assertIsNone(p.poll(), "别的程序被杀了")
            self.assertIn("别的程序", r)
        finally:
            p.kill()

    def test_gone_pid(self):
        self.assertEqual(OPS.kill_game(99, 0x7FFFFFF0), "已经退了")


if __name__ == "__main__":
    unittest.main()

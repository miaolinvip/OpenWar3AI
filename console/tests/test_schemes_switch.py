# -*- coding: utf-8 -*-
"""实例换 AI 方案（09-24）：记进 next_game.json；实例在跑时立刻停旧 AI、起新 AI；参考大脑走 run_brain、玩家方案走 run_scheme；
没被信任的别人方案拒绝运行；实例没在跑时只记下不起进程。进程查询 / 杀进程 / WMI 起进程都换成假的，只测判断。"""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402
from openwar3 import schemes as S  # noqa: E402
import w3paths  # noqa: E402


def st(n, game=0, farm=False, brain=(), bot=()):
    return {"inst": n, "game": {"pid": game, "alive": bool(game)},
            "farm": [{"pid": 900, "range": [n, n], "keep": True}] if farm else [],
            "launcher": None, "brain": [{"pid": p} for p in brain], "bot": [{"pid": p} for p in bot],
            "oldAi": [], "director": [], "modules": [], "held": None}


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(I, k) for k in ("status", "kill_tree", "spawn_detached", "start_farm", "game_problem")}
        self.saved_dir = C.inst_dir
        self.saved_paths = (w3paths.SCHEMES_MINE, w3paths.SCHEMES_INSTALLED)
        w3paths.SCHEMES_MINE = os.path.join(self.tmp.name, "mine")
        w3paths.SCHEMES_INSTALLED = os.path.join(self.tmp.name, "installed")
        C.inst_dir = lambda n: os.path.join(self.tmp.name, f"inst{n}")
        self.world, self.killed, self.spawned = {}, [], []
        I.status = lambda n=None, procs=None: [self.world[n]] if n is not None else list(self.world.values())
        I.kill_tree = lambda pid, include_self=True: (self.killed.append(pid) or [pid])
        I.spawn_detached = lambda cmd, cwd: (self.spawned.append(cmd) or {"pid": 4242, "rc": 0})
        I.game_problem = lambda: None          # 不依赖本机 openwar3.json 里设没设游戏目录（干净的克隆里没有）
        I.time.sleep = lambda s: None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(I, k, v)
        C.inst_dir = self.saved_dir
        w3paths.SCHEMES_MINE, w3paths.SCHEMES_INSTALLED = self.saved_paths
        import time as _t
        I.time.sleep = _t.sleep
        self.tmp.cleanup()

    def test_switch_now_replaces_running_brain_with_scheme(self):
        self.world[5] = st(5, game=111, farm=True, brain=[301])
        r = I.switch_scheme(5, "builtin/micro", now=True)
        self.assertEqual((r["ok"], r["applied"]), (True, "now"))
        self.assertIn(301, self.killed)                                         # 参考大脑停了
        self.assertTrue(any("run_scheme.py" in c and "--scheme builtin/micro" in c and "--inst 5" in c for c in self.spawned))
        self.assertEqual(I.next_game(5)["scheme"], "builtin/micro")

    def test_switch_back_to_brain_uses_run_brain(self):
        self.world[5] = st(5, game=111, farm=True, bot=[401])
        r = I.switch_scheme(5, "builtin/xwar3", now=True)
        self.assertTrue(r["ok"])
        self.assertIn(401, self.killed)
        self.assertTrue(any("run_brain.py" in c for c in self.spawned))
        self.assertNotIn("scheme", I.next_game(5))                               # 默认方案不写进文件

    def test_not_running_only_records(self):
        self.world[6] = st(6)
        r = I.switch_scheme(6, "builtin/macro", now=True)
        self.assertEqual((r["applied"], self.spawned, self.killed), ("next", [], []))
        self.assertEqual(I.scheme_of(6), "builtin/macro")

    def test_untrusted_installed_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("scheme.json", json.dumps({"id": "other", "kind": "bot", "entry": "b.py"}))
            z.writestr("b.py", "from openwar3 import Bot\nclass B(Bot):\n    pass\n")
        S.install_zip(buf.getvalue())
        self.world[7] = st(7, game=111, farm=True, brain=[501])
        r = I.switch_scheme(7, "installed/other", now=True)
        self.assertFalse(r["ok"])
        self.assertEqual((self.killed, self.spawned), ([], []))                  # 旧 AI 没被停
        S.trust("installed/other")
        self.assertTrue(I.switch_scheme(7, "installed/other", now=True)["ok"])

    def test_start_test_uses_selected_scheme(self):
        self.world[8] = st(8)
        I.write_next_game(8, {"scheme": "builtin/rush"})
        I.start_farm = lambda n, hours=6.0, *a, **k: {"ok": True}                # tearDown 还原
        r = I.start_test(8, 24)
        self.assertTrue(r["ok"])
        self.assertEqual(r["scheme"], "builtin/rush")
        self.assertTrue(any("--scheme builtin/rush" in c for c in self.spawned))

    def test_bad_scheme_ref_rejected(self):
        self.world[9] = st(9, game=1, farm=True)
        with self.assertRaises(S.SchemeError):
            I.switch_scheme(9, "mine/nope", now=True)


if __name__ == "__main__":
    unittest.main()

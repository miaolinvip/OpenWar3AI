# -*- coding: utf-8 -*-
"""实例起停（farsight/server/instances.py）：停止挂停止牌、按范围起的 farm 不误杀、开始测试摘牌且不重复起、
kill-proc 只认这个实例名下的 pid。进程查询 / 杀进程 / WMI 起进程都换成假的，只测判断。
09-19 需求：网页上能随时停止任意编号的游戏或进程，停止后不再自动启动、开局。
跑法：cd farsight && python -m unittest tests.test_instances -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402


def st(n, farm=None, brain=(), game=0, launcher=0, modules=(), director=(), old_ai=()):
    return {"inst": n, "game": {"pid": game, "alive": bool(game)},
            "farm": [{"pid": p, "range": list(r), "keep": True} for p, r in (farm or [])],
            "launcher": {"pid": launcher, "started": None} if launcher else None,
            "brain": [{"pid": p} for p in brain], "oldAi": [{"pid": p} for p in old_ai],
            "director": [{"pid": p} for p in director], "modules": [{"name": "mod_combat", "pid": p} for p in modules],
            "held": None}


class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(I, k) for k in ("status", "kill_tree", "_kill_game", "spawn_detached", "_procs", "game_problem")}
        self.saved_inst_dir = C.inst_dir
        C.inst_dir = lambda n: os.path.join(self.tmp.name, f"inst{n}")
        self.world = {}
        self.killed = []
        self.spawned = []
        I.status = lambda n=None, procs=None: [self.world[n]] if n is not None else list(self.world.values())
        I.kill_tree = lambda pid, include_self=True: (self.killed.append((pid, include_self)) or [pid])
        I._kill_game = lambda n, pid: (self.killed.append(("game", n)) or [])
        I.spawn_detached = lambda cmd, cwd: (self.spawned.append(cmd) or {"pid": 4242, "rc": 0})
        I._procs = lambda: []
        I.game_problem = lambda: None          # 不依赖本机 openwar3.json 里设没设游戏目录（干净的克隆里没有）

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(I, k, v)
        C.inst_dir = self.saved_inst_dir
        self.tmp.cleanup()

    def held(self, n):
        return os.path.exists(os.path.join(self.tmp.name, f"inst{n}", I.HOLD_FILE))


class TestStop(Fake):
    def test_own_farm_is_killed_and_hold_set(self):
        self.world[5] = st(5, farm=[(100, (5, 5))], brain=[200], game=300, launcher=301)
        r = I.stop_instance(5)
        self.assertTrue(self.held(5))
        self.assertIn((100, True), self.killed)                 # 只管 5 号的 farm：连树杀
        self.assertIn((200, True), self.killed)                 # 新大脑
        self.assertIn(("game", 5), self.killed)
        self.assertEqual(r["notes"], [])

    def test_range_farm_is_spared(self):
        # 命令行起的 farm 管 6~11 号：停 7 号不能杀它（它的子进程里有 6、8~11 号的启动器守护）
        self.world[7] = st(7, farm=[(100, (6, 11))], brain=[200], game=300)
        r = I.stop_instance(7)
        self.assertTrue(self.held(7))
        self.assertNotIn(100, [p for p, _ in self.killed if p != "game"])
        self.assertEqual(len(r["notes"]), 1)
        self.assertIn(("game", 7), self.killed)

    def test_range_farm_stopped_with_its_last_instance(self):
        # 全部停止时逐个停：管 6~7 号的 farm 在停 6 号时留着，停 7 号时 6、7 都挂了牌 —— 它没东西可管了，一起停
        self.world = {6: st(6, farm=[(100, (6, 7))], game=600), 7: st(7, farm=[(100, (6, 7))], game=700)}
        I.stop_instance(6)
        self.assertNotIn((100, True), self.killed)
        I.stop_instance(7)
        self.assertIn((100, True), self.killed)

    def test_keep_farm_does_not_hold(self):
        self.world[5] = st(5, farm=[(100, (5, 5))], game=300)
        I.stop_instance(5, keep_farm=True)
        self.assertFalse(self.held(5))
        self.assertNotIn(100, [p for p, _ in self.killed])

    def test_stop_all_only_touches_running(self):
        self.world = {5: st(5, farm=[(100, (5, 5))]), 6: st(6), 7: st(7, brain=[700])}
        r = I.stop_all()
        self.assertEqual(r["stopped"], [5, 7])
        self.assertTrue(self.held(5) and self.held(7))
        self.assertFalse(self.held(6))


class TestStart(Fake):
    def test_start_clears_hold_and_starts_both(self):
        self.world[5] = st(5)
        I.set_hold(5, "test")
        r = I.start_test(5, 24)
        self.assertTrue(r["ok"])
        self.assertFalse(self.held(5))
        self.assertEqual(len(self.spawned), 2)                   # farm + 新大脑
        self.assertIn("farm.py", self.spawned[0])                # 09-26 起 farm 是 Python（不再要 PowerShell 7）
        self.assertIn("--first 5 --count 5", self.spawned[0])
        self.assertIn("--keep", self.spawned[0])
        self.assertNotIn("pwsh", self.spawned[0])
        self.assertIn("run_brain.py", self.spawned[1])
        self.assertIn("--inst 5", self.spawned[1])

    def test_running_halves_not_duplicated(self):
        self.world[5] = st(5, farm=[(100, (5, 5))], brain=[200])
        r = I.start_test(5, 24)
        self.assertTrue(r["ok"])
        self.assertEqual(self.spawned, [])

    def test_old_ai_blocks_new_brain(self):
        self.world[5] = st(5, farm=[(100, (5, 5))], old_ai=[900])
        r = I.start_test(5, 24)
        self.assertEqual(self.spawned, [])
        self.assertIn("老 AI", r["brain"]["note"])

    def test_start_many_staggers_and_dedups(self):
        self.world = {5: st(5), 6: st(6)}
        r = I.start_many([5, 6, 5], hours=1, gap_s=0)
        self.assertEqual([x["inst"] for x in r["results"]], [5, 6])
        self.assertEqual(len(self.spawned), 4)


class TestStalePid(unittest.TestCase):
    """★ 09-19：inst9.out 里是昨晚 02:38 那局的游戏 pid 31788，今天这个 pid 属于别的程序 ——
    只按 pid 判「游戏在跑」，「全部停止」把那个无关的进程杀了。status 要核对进程名。"""

    def setUp(self):
        self.saved = {k: getattr(I, k) for k in ("_procs", "read_farm_out", "known_numbers", "latest_match", "next_game", "speed_of")}
        I.read_farm_out = lambda n: {"pid": 31788, "channel": True}
        I.known_numbers = lambda: [9]
        I.latest_match = lambda n: None
        I.next_game = lambda n: {}
        I.speed_of = lambda n: None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(I, k, v)

    def proc(self, name):
        return [{"pid": 31788, "ppid": 1, "name": name, "cmd": name, "args": [name], "started": 0.0}]

    def test_foreign_process_is_not_the_game(self):
        s = I.status(procs=self.proc("chrome.exe"))[0]
        self.assertFalse(s["game"]["alive"])

    def test_war3_names_are(self):
        for name in ("War3-9.exe", "War3.exe", "war3-12.EXE"):
            self.assertTrue(I.status(procs=self.proc(name))[0]["game"]["alive"], name)
        self.assertFalse(I.status(procs=self.proc("War3Edit.exe"))[0]["game"]["alive"])


class TestDirector(Fake):
    """导演台按实例开关（09-19）：next_game.json 的 director；独立导播 director_inst.py 由 Farsight 起停。"""

    def director_proc(self, n, pid):
        return {"pid": pid, "ppid": 1, "name": "python.exe", "cmd": f"python -u D:\\x\\war3-analytics\\director_inst.py --inst {n}",
                "args": [], "started": 0.0}

    def test_start_test_records_flag(self):
        self.world[5] = st(5)
        r = I.start_test(5, 24, director=True)
        self.assertTrue(r["director"])
        self.assertTrue(I.next_game(5).get("director"))
        r = I.start_test(5, 24, director=None)                  # 不给 = 照原来的开关
        self.assertTrue(r["director"])
        r = I.start_test(5, 24, director=False)
        self.assertFalse(r["director"])
        self.assertNotIn("director", I.next_game(5))

    def test_start_director_spawns_once(self):
        I.start_director(6)
        self.assertEqual(len(self.spawned), 1)
        self.assertIn("director_inst.py", self.spawned[0])
        self.assertIn("--inst 6", self.spawned[0])
        I._procs = lambda: [self.director_proc(6, 777)]
        self.assertIn("已经在跑", I.start_director(6)["note"])
        self.assertEqual(len(self.spawned), 1)

    def test_stop_director_clears_own_lock_only(self):
        os.makedirs(C.inst_dir(6), exist_ok=True)
        lock = os.path.join(C.inst_dir(6), "director.lock")
        I._procs = lambda: [self.director_proc(6, 777), self.director_proc(16, 888)]   # --inst 16 不能被 6 号误认
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("777")
        self.assertEqual(I.stop_director(6), [777])
        self.assertFalse(os.path.exists(lock))
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("12345")                                   # 锁是别人的（导播台页托管那份）：不清
        I.stop_director(6)
        self.assertTrue(os.path.exists(lock))

    def test_stop_instance_stops_director_keep_flag(self):
        self.world[5] = st(5, farm=[(100, (5, 5))], game=300)
        I.write_next_game(5, {"director": True})
        I._procs = lambda: [self.director_proc(5, 555)]
        r = I.stop_instance(5)
        self.assertIn(555, r["killed"])
        self.assertTrue(I.next_game(5).get("director"))          # 开关留着，下次开始测试照带


class TestKillProc(Fake):
    def test_rejects_foreign_pid(self):
        self.world[5] = st(5, brain=[200])
        r = I.kill_proc(5, 999)
        self.assertFalse(r["ok"])
        self.assertEqual(self.killed, [])

    def test_module_and_brain_killed_as_tree(self):
        self.world[5] = st(5, brain=[200], modules=[210])
        self.assertEqual(I.kill_proc(5, 210)["kind"], "modules")
        self.assertEqual(I.kill_proc(5, 200)["kind"], "brain")
        self.assertEqual(self.killed, [(210, True), (200, True)])

    def test_game_goes_through_launcher_path(self):
        self.world[5] = st(5, game=300, launcher=301)
        self.assertEqual(I.kill_proc(5, 301)["kind"], "launcher")
        self.assertEqual(self.killed, [("game", 5)])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""09-26 farm 从 farm.ps1 改写成 runtime/farm.py（部署不用再装 PowerShell 7）、网页随仓库发（不用 Node.js）：
- 远见认得新旧两种 farm 进程（升级前起的 farm.ps1 可能还在跑），按命令行算出它管的号段；
- farm 的纯逻辑：下一局设置、启动器参数、实例 exe、实例目录种子；起守护进程只交三个标准句柄；
- 网页源码指纹：Python（start.bat 核对用）和 Node（编完写 dist/.source-hash）两边算得一样，换行 CRLF / LF 不算改动。
跑法：cd console && python -m unittest tests.test_farm -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402
from server import webstamp as WS  # noqa: E402

sys.path.insert(0, C.w3paths.RUNTIME)
import farm as F  # noqa: E402


class FarmRangeTest(unittest.TestCase):
    def test_new_and_old_command_lines(self):
        py = r'"C:\py\python.exe" -u "D:\ow3\runtime\farm.py" --first 5 --count 7 --keep --no-director --hours 24'
        ps = r'"C:\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "D:\ow3\runtime\farm.ps1" -First 5 -Count 5 -Keep'
        self.assertEqual(I._farm_range(py), (5, 7))
        self.assertEqual(I._farm_range(ps), (5, 5))

    def test_defaults_and_one_shot_commands(self):
        self.assertEqual(I._farm_range(r"python D:\ow3\runtime\farm.py"), (1, 6))
        self.assertIsNone(I._farm_range(r"python D:\ow3\runtime\farm.py --stop"))
        self.assertIsNone(I._farm_range(r"python D:\ow3\runtime\farm.py --status"))
        self.assertIsNone(I._farm_range(r"pwsh -File D:\ow3\runtime\farm.ps1 -Stop"))
        self.assertIsNone(I._farm_range(r"python D:\ow3\tools\server_farm.py --first 5"))    # 名字里带 farm 的别的脚本

    def test_status_counts_python_farm_not_cmd_wrapper(self):
        cmd = r'python.exe -u D:\ow3\runtime\farm.py --first 5 --count 5 --keep'
        procs = [{"pid": 10, "ppid": 1, "name": "cmd.exe", "cmd": 'cmd.exe /c "' + cmd + ' > log 2>&1"', "args": [], "started": 0},
                 {"pid": 11, "ppid": 10, "name": "python.exe", "cmd": cmd, "args": [], "started": 0}]
        saved = I.read_farm_out
        I.read_farm_out = lambda n: {}
        try:
            row = I.status(5, procs=procs)[0]
        finally:
            I.read_farm_out = saved
        self.assertEqual([f["pid"] for f in row["farm"]], [11])
        self.assertTrue(row["farm"][0]["keep"])
        self.assertEqual(row["farm"][0]["range"], [5, 5])


class FarmLogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.maps = os.path.join(self.tmp, "Maps")
        os.makedirs(os.path.join(self.maps, "Download"))
        self.base_map = os.path.join(self.maps, "(2)EchoIsles.w3x")
        self.other = os.path.join(self.maps, "Download", "x.w3x")
        for p in (self.base_map, self.other):
            open(p, "wb").close()
        self.base = {"map": self.base_map, "race": 1, "enemyRace": 0, "difficulty": 2, "speed": 300}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_ng(self, obj):
        with open(os.path.join(self.tmp, "next_game.json"), "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_parse_defaults(self):
        a = F.parse_args([])
        self.assertEqual((a.first, a.count, a.speed, a.race, a.difficulty, a.window), (1, 6, 300, 1, 2, "0,0,1920,1080"))
        a = F.parse_args(["--first", "5", "--count", "5", "--keep", "--no-director", "--enemy-race", "3"])
        self.assertTrue(a.keep and a.no_director)
        self.assertEqual(a.enemy_race, 3)

    def test_next_game_without_file_is_base(self):
        self.assertEqual(F.next_game(self.tmp, self.base, self.maps), self.base)

    def test_next_game_relative_map_and_overrides(self):
        self.write_ng({"map": r"Download\x.w3x", "race": 4, "enemyRace": 2, "speed": "", "difficulty": None})
        g = F.next_game(self.tmp, self.base, self.maps)
        self.assertEqual(g["map"], self.other)
        self.assertEqual((g["race"], g["enemyRace"], g["speed"], g["difficulty"]), (4, 2, 300, 2))   # 空的项用命令行参数

    def test_next_game_missing_map_falls_back(self):
        self.write_ng({"map": "gone.w3x", "race": 3})
        g = F.next_game(self.tmp, self.base, self.maps)
        self.assertEqual(g["map"], self.base_map)
        self.assertEqual(g["race"], 3)

    def test_next_game_broken_file_uses_base(self):
        self.write_ng("{not json")
        self.assertEqual(F.next_game(self.tmp, self.base, self.maps), self.base)
        self.write_ng("\ufeff" + json.dumps({"race": 2}))                  # 记事本存的带 BOM
        self.assertEqual(F.next_game(self.tmp, self.base, self.maps)["race"], 2)

    def test_launch_args(self):
        a = F.launch_args(r"D:\g\War3-5.exe", 5, self.base, 2600, False, "0,0,960,540")
        self.assertEqual(a[:4], ["--exe", r"D:\g\War3-5.exe", "--map", self.base_map])
        self.assertNotIn("--enemy-race", a)                                    # 0 = 镜像，不传
        self.assertEqual(a[-2:], ["--client", "0,0,960,540"])
        a = F.launch_args("x.exe", 5, dict(self.base, enemyRace="2"), 2600, True, "0,0,1,1")
        self.assertIn("--enemy-race", a)
        self.assertEqual(a[-1], "--minimize")
        self.assertNotIn("--client", a)

    def test_instance_exe_copies_and_refreshes(self):
        src = os.path.join(self.tmp, "War3.exe")
        with open(src, "wb") as fh:
            fh.write(b"shell-v1")
        dst = F.instance_exe(7, self.tmp)
        self.assertEqual(dst, os.path.join(self.tmp, "War3-7.exe"))
        with open(src, "wb") as fh:
            fh.write(b"shell-v2")                                              # 游戏打了补丁：副本要重拷
        F.instance_exe(7, self.tmp)
        with open(dst, "rb") as fh:
            self.assertEqual(fh.read(), b"shell-v2")

    def test_instance_dir_seeds_and_required(self):
        seed = os.path.join(self.tmp, "seed")
        os.makedirs(seed)
        for name in ("units.json", "names.json", "race.txt"):
            with open(os.path.join(seed, name), "w", encoding="utf-8") as fh:
                fh.write(name)
        saved = (F.SEED_DIRS, F.w3paths.inst_dir)
        F.SEED_DIRS = (seed,)
        F.w3paths.inst_dir = lambda n: os.path.join(self.tmp, f"inst{n}")
        try:
            d = F.new_instance_dir(3)
            self.assertTrue(os.path.isdir(os.path.join(d, "matches")) and os.path.isdir(os.path.join(d, "fights")))
            with open(os.path.join(d, "race.txt"), "w", encoding="utf-8") as fh:
                fh.write("changed")                                            # 已经有的不覆盖
            F.new_instance_dir(3)
            with open(os.path.join(d, "race.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "changed")
            os.remove(os.path.join(seed, "names.json"))
            with self.assertRaises(RuntimeError):
                F.new_instance_dir(4)                                          # 基础数据缺了当场报
        finally:
            F.SEED_DIRS, F.w3paths.inst_dir = saved

    def test_start_launcher_writes_out_file(self):
        out = os.path.join(self.tmp, "inst9.out")
        saved = F.w3paths.LAUNCH_EXE
        F.w3paths.LAUNCH_EXE = sys.executable
        try:
            pid = F.start_launcher(["-c", "print('WAR3_PID=4242'); print('control channel: ok')"], out)
        finally:
            F.w3paths.LAUNCH_EXE = saved
        self.assertGreater(pid, 0)
        for _ in range(100):
            if "control channel: ok" in F.read_text(out):
                break
            time.sleep(0.05)
        text = F.read_text(out)
        self.assertEqual(F.war3_pid(text), 4242)
        self.assertIn("control channel: ok", text)


class WebStampTest(unittest.TestCase):
    def setUp(self):
        self.web = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.web, "src", "pages"))
        os.makedirs(os.path.join(self.web, "dist"))
        self.put("package.json", b'{"name": "x"}\n')
        self.put("src/main.tsx", b"line1\nline2\n")
        self.put("src/pages/Home.tsx", b"export {}\n")

    def tearDown(self):
        shutil.rmtree(self.web, ignore_errors=True)

    def put(self, rel, data):
        with open(os.path.join(self.web, *rel.split("/")), "wb") as fh:
            fh.write(data)

    def test_states(self):
        self.assertEqual(WS.check(self.web)["state"], "missing")
        self.put("dist/index.html", b"<html></html>")
        self.assertEqual(WS.check(self.web)["state"], "unstamped")
        WS.write(self.web)
        self.assertEqual(WS.check(self.web)["state"], "ok")
        self.put("src/pages/Home.tsx", b"export const x = 1\n")
        self.assertEqual(WS.check(self.web)["state"], "stale")

    def test_crlf_checkout_is_not_a_change(self):
        fp = WS.fingerprint(self.web)
        self.put("src/main.tsx", b"line1\r\nline2\r\n")                       # git 在 Windows 上检出成 CRLF
        self.assertEqual(WS.fingerprint(self.web), fp)

    def test_node_and_python_agree(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("没装 Node.js")
        script = os.path.join(WS.WEB, "scripts", "stamp.mjs")
        r = subprocess.run([node, script, "--print"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), WS.fingerprint())


if __name__ == "__main__":
    unittest.main()

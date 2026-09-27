# -*- coding: utf-8 -*-
"""首页「控制中心」的后端（09-26，console/server/services.py）：认服务进程、判状态、读日志尾巴、核对发布版运行时。

要守住的几件事：
* 认进程按「参数以仓库相对路径结尾」—— 手动 `python gateway/server.py` 起的要认得；别的仓库里同名的 speak_api.py 不能误认
  （它的旧远见也默认 8866，混过一次）；远见包的那层 cmd.exe 不算服务本身。
* 端口在听但不是我们的进程 = conflict（别当成在跑，也别去停别人的程序）。
* 日志文件很大时只读尾巴，截掉被切了半截的第一行。
* 没有 native/ 源码时，bin/ 里的二进制要和 prebuilt/manifest.json 的 SHA256 对得上，对不上报 stale。
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402
from server import services as S  # noqa: E402


def proc(pid, name, args):
    return {"pid": pid, "ppid": 1, "name": name, "cmd": " ".join(args), "args": args, "started": 100.0 + pid}


class ScriptProcsTest(unittest.TestCase):
    def setUp(self):
        self.saved = I._procs
        speak = os.path.join(C.ROOT, "speech", "speak_api.py")
        I._procs = lambda: [
            proc(1, "python.exe", ["python.exe", "-u", speak, "--port", "8872"]),                  # 远见起的（绝对路径）
            proc(2, "python.exe", ["python.exe", "speech/speak_api.py"]),                          # 手动在仓库根起的（相对路径）
            proc(3, "python.exe", ["python.exe", r"D:\Other\oldrepo\modules\speak_api.py"]),     # 别的仓库的同名脚本
            proc(4, "cmd.exe", ["cmd.exe", "/c", f'"python.exe" -u "{speak}" > "{S.SPEECH_LOG}"']),  # 包的那层 cmd
            proc(5, "python.exe", ["python.exe", "-u", os.path.join(C.ROOT, "gateway", "server.py")]),
        ]

    def tearDown(self):
        I._procs = self.saved

    def test_matches_absolute_and_relative_only(self):
        self.assertEqual([p["pid"] for p in S._script_procs(S.SPEECH_PY)], [1, 2])

    def test_wrapper_found_by_log_path(self):
        self.assertEqual([p["pid"] for p in S._wrappers(S.SPEECH_LOG)], [4])


class LeftoversTest(unittest.TestCase):
    """全部停止时收拾漏网进程：只认本仓库的组件入口；别的终端在仓库里跑的测试、别的仓库的同名脚本、MCP 都不能碰。"""

    def setUp(self):
        self.saved = I._procs
        j = lambda *p: os.path.join(C.ROOT, *p)  # noqa: E731
        I._procs = lambda: [
            proc(1, "python.exe", ["python.exe", "-u", j("brains", "xwar3", "strategy", "run_brain.py"), "--inst", "5"]),
            proc(2, "pwsh.exe", ["pwsh.exe", "-File", j("runtime", "farm.ps1"), "-First", "5"]),        # 升级前起的旧 farm
            proc(5, "python.exe", ["python.exe", "-u", j("runtime", "farm.py"), "--first", "6"]),
            proc(3, "war3_launch.exe", [j("bin", "war3_launch.exe"), "--exe", "War3-5.exe"]),
            proc(4, "python.exe", ["python.exe", j("brains", "xwar3", "reflex", "mod_combat.py")]),
            proc(10, "python.exe", ["python.exe", j("tools", "run_tests.py")]),                        # 别的终端在跑测试
            proc(11, "python.exe", ["python.exe", r"D:\Other\oldrepo\farsight\run.py"]),              # 别的仓库
            proc(12, "python.exe", ["python.exe", j("tools", "war3_mcp.py"), "--inst", "5"]),          # MCP 归客户端管
            proc(13, "python.exe", ["python.exe", "-u", j("console", "run.py"), "--port", "8866"]),    # 远见后台由调用方最后停
            proc(14, "war3_launch.exe", [r"D:\Other\oldrepo\build\war3_launch.exe"]),                 # 别处的启动器
        ]

    def tearDown(self):
        I._procs = self.saved

    def test_only_our_components(self):
        self.assertEqual(sorted(p["pid"] for p in S._leftovers()), [1, 2, 3, 4, 5])


class StateTest(unittest.TestCase):
    def setUp(self):
        S._starting.clear()

    def test_states(self):
        me = [{"pid": 1}]
        self.assertEqual(S._state(True, me, "x"), "running")
        self.assertEqual(S._state(True, [], "x"), "conflict")          # 端口被别人占着
        self.assertEqual(S._state(False, me, "x"), "starting")          # 进程在、端口还没听上
        self.assertEqual(S._state(False, [], "x"), "stopped")

    def test_just_clicked_start_counts_as_starting(self):
        S._starting["x"] = time.time()
        self.assertEqual(S._state(False, [], "x"), "starting")
        S._starting["x"] = time.time() - S.STARTING_S - 1
        self.assertEqual(S._state(False, [], "x"), "stopped")


class LogTailTest(unittest.TestCase):
    def test_missing_and_tail(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "svc.out")
            saved = dict(S.LOGS)
            S.LOGS["gateway"] = path
            try:
                self.assertTrue(S.log("gateway")["missing"])
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("x" * (200 * 1024) + "\n" + "\n".join(f"第 {i} 行" for i in range(50)) + "\n")
                r = S.log("gateway", lines=5)
                self.assertEqual(r["lines"], [f"第 {i} 行" for i in range(45, 50)])
                self.assertFalse(any(ln.startswith("xxx") for ln in S.log("gateway", lines=2000)["lines"]))   # 半截的第一行丢掉
            finally:
                S.LOGS.clear()
                S.LOGS.update(saved)

    def test_unknown_service(self):
        self.assertEqual(S.log("nope")["lines"], [])
        self.assertFalse(S.act("nope", "start", 8866)["ok"])
        self.assertFalse(S.act("gateway", "explode", 8866)["ok"])
        self.assertFalse(S.act("console", "stop", 8866)["ok"])            # 远见自己只能重启


class RuntimeCheckTest(unittest.TestCase):
    """没有 native/ 源码（开源发布包）时核对 bin/ 和 prebuilt/manifest.json。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.saved = (C.BIN, S.W.PREBUILT, S.W.NATIVE)
        self.bin, self.pre = os.path.join(d, "bin"), os.path.join(d, "prebuilt")
        os.makedirs(self.bin)
        os.makedirs(self.pre)
        files = {}
        for f in ("render_hook.dll", "war3_launch.exe", "war3_ui_command.exe"):
            data = f.encode() * 10
            for base in (self.bin, self.pre):
                with open(os.path.join(base, f), "wb") as fh:
                    fh.write(data)
            files[f] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        with open(os.path.join(self.pre, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "2026.09.26.0208", "files": files}, fh)
        C.BIN, S.W.PREBUILT, S.W.NATIVE = self.bin, self.pre, os.path.join(d, "native")   # native/ 不存在

    def tearDown(self):
        C.BIN, S.W.PREBUILT, S.W.NATIVE = self.saved
        self.tmp.cleanup()

    def test_prebuilt_in_sync(self):
        r = S._runtime_check()
        self.assertEqual((r["status"], r["code"], r["extra"]["version"]), ("ok", "runtime-prebuilt", "2026.09.26.0208"))

    def test_stale_and_missing(self):
        with open(os.path.join(self.bin, "render_hook.dll"), "wb") as fh:
            fh.write(b"old build")
        r = S._runtime_check()
        self.assertEqual((r["status"], r["code"], r["extra"]["diff"]), ("warn", "runtime-stale", ["render_hook.dll"]))
        os.remove(os.path.join(self.bin, "war3_launch.exe"))
        r = S._runtime_check()
        self.assertEqual((r["status"], r["extra"]["missing"]), ("error", ["war3_launch.exe"]))


class FileVersionTest(unittest.TestCase):
    def test_system_dll_has_version(self):
        v = S.file_version(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "kernel32.dll"))
        self.assertRegex(v or "", r"^\d+\.\d+\.\d+\.\d+$")
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            fh.write(b"no version resource")
        try:
            self.assertIsNone(S.file_version(fh.name))
        finally:
            os.remove(fh.name)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""09-26 这一批：版本号三处一致、远见只收本机请求、游戏目录检查 / 保存、地图相对路径、反馈客户端（打包、脱敏、限流、离线存盘）。
不需要开游戏；反馈的端到端（真运行时签名 + 真服务端验签）要本机有私有密钥和 bin/war3_ui_command.exe，没有就跳过。"""
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from server import common as C  # noqa: E402
from server import feedback as FB  # noqa: E402
from server import gamedir as GD  # noqa: E402
from server import mapinfo as M  # noqa: E402

ROOT = C.ROOT
W = C.w3paths


class VersionTest(unittest.TestCase):
    def test_three_places_agree(self):
        ver = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
        self.assertRegex(ver, r"^\d+\.\d+\.\d+$")
        ts = open(os.path.join(ROOT, "console", "web", "src", "lib", "changelog.ts"), encoding="utf-8").read()
        self.assertEqual(re.search(r'VERSION = "([\d.]+)"', ts).group(1), ver, "changelog.ts 的 VERSION 和仓库根 VERSION 不一致")
        site_file = os.path.join(ROOT, "website", "public", "version.json")
        if not os.path.isfile(site_file):          # 官网单独维护、不在公开仓库里：只有开发机上核对
            return
        site = json.load(open(site_file, encoding="utf-8"))
        self.assertRegex(site["latest"], r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(tuple(map(int, site["latest"].split("."))), tuple(map(int, ver.split("."))),
                                "官网 version.json 的 latest 比仓库版本还旧：发版时记得改")


class LocalOnlyTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI, WebSocket
        from fastapi.testclient import TestClient
        from server.app import LocalOnly
        a = FastAPI()

        @a.get("/r")
        def r():
            return {"ok": True}

        @a.post("/w")
        def w():
            return {"ok": True}

        @a.websocket("/ws")
        async def ws(sock: WebSocket):
            await sock.accept()
            await sock.send_json({"ok": True})
            await sock.close()

        a.add_middleware(LocalOnly)
        self.c = TestClient(a, base_url="http://127.0.0.1:8866")

    def test_host_and_origin(self):
        self.assertEqual(self.c.post("/w").status_code, 200)                                          # 本机脚本（没 Origin）
        self.assertEqual(self.c.post("/w", headers={"Origin": "http://127.0.0.1:8866"}).status_code, 200)  # 远见自己的页面
        self.assertEqual(self.c.post("/w", headers={"Origin": "http://localhost:5173"}).status_code, 200)  # 本机开发服务器
        self.assertEqual(self.c.post("/w", headers={"Origin": "https://evil.example"}).status_code, 403)   # 跨站
        self.assertEqual(self.c.get("/r", headers={"Origin": "https://evil.example"}).status_code, 200)    # 只读的放行（读不到：没 CORS）
        self.assertEqual(self.c.get("/r", headers={"Host": "evil.example:8866"}).status_code, 403)         # DNS 重绑定

    def test_websocket_origin(self):
        with self.c.websocket_connect("/ws", headers={"Origin": "http://127.0.0.1:8866"}) as s:
            self.assertTrue(s.receive_json()["ok"])
        with self.assertRaises(Exception):
            with self.c.websocket_connect("/ws", headers={"Origin": "https://evil.example"}) as s:
                s.receive_json()


class GameDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (W.CONFIG, list(GD.on_changed))
        W.CONFIG = os.path.join(self.tmp.name, "openwar3.json")        # 别碰真的 openwar3.json
        with open(W.CONFIG, "w", encoding="utf-8") as fh:
            json.dump({"python": "keep-me"}, fh)
        W.config.cache_clear()
        GD.on_changed.clear()
        self.game = os.path.join(self.tmp.name, "Warcraft III")
        os.makedirs(os.path.join(self.game, "Maps", "Download"))
        for f in ("War3.exe", "Game.dll"):
            open(os.path.join(self.game, f), "wb").close()

    def tearDown(self):
        W.CONFIG, handlers = self.saved
        GD.on_changed[:] = handlers
        W.config.cache_clear()
        self.tmp.cleanup()

    def test_check(self):
        c = GD.check(self.game)
        self.assertTrue(c["ok"] and c["maps"])
        self.assertFalse(c["supported"])                     # 空的 Game.dll 没版本号 = 不支持
        self.assertEqual(GD.check(os.path.join(self.game, "War3.exe"))["path"], self.game)   # 选成了 exe 本身
        self.assertFalse(GD.check(os.path.join(self.tmp.name, "nope"))["exists"])

    def test_save_keeps_other_keys_and_blocks_launch(self):
        self.assertIn("还没设置", GD.version_problem())
        r = GD.save(self.game)
        self.assertTrue(r["ok"])
        cfg = json.load(open(W.CONFIG, encoding="utf-8"))
        self.assertEqual((cfg["game_dir"], cfg["python"]), (self.game, "keep-me"))
        self.assertEqual(W.maps_dir(), os.path.join(self.game, "Maps"))
        self.assertIn("War3AI.com", GD.version_problem())    # 版本不对：开局前拦下、给温馨提示
        self.assertFalse(GD.save(os.path.join(self.tmp.name, "nope"))["ok"])

    def test_map_paths_relative_to_game(self):
        GD.save(self.game)
        p = os.path.join(self.game, "Maps", "Download", "(2)Foo.w3x")
        open(p, "wb").close()
        self.assertEqual(M.rel(p), os.path.join("Download", "(2)Foo.w3x"))
        self.assertEqual(M.resolve(os.path.join("Download", "(2)Foo.w3x")), p)
        self.assertIsNone(M.resolve(r"..\..\Windows\notepad.exe"))     # 跑出 Maps 目录的不认
        self.assertEqual([m["file"] for m in M.list_maps()], ["(2)Foo.w3x"])


class FeedbackClientTest(unittest.TestCase):
    def test_build_and_limits(self):
        with self.assertRaises(ValueError):
            FB.build({"kind": "spam", "title": "a", "text": "b"})
        with self.assertRaises(ValueError):
            FB.build({"kind": "issue", "title": "", "text": "b"})
        with self.assertRaises(ValueError):
            FB.build({"kind": "issue", "title": "a", "text": "x" * 9000})
        p = FB.build({"kind": "suggestion", "title": " 标题 ", "text": "内容", "diag": False})
        self.assertEqual((p["v"], p["title"], p["client"]["version"]), (1, "标题", FB.version()))
        self.assertNotIn("diag", p)

    def test_scrub(self):
        home = os.path.expanduser("~")
        s = FB.scrub(f"打不开 {home}\\Desktop\\x.w3x")
        self.assertNotIn(os.path.basename(home), s)
        self.assertIn("%USERPROFILE%", s)

    def test_service_url_only_https(self):
        saved = W.config()
        try:
            for url, want in (("http://evil.example", W.WEBSITE), ("https://war3ai.com/", "https://war3ai.com"),
                              ("http://127.0.0.1:9", "http://127.0.0.1:9")):
                W.config.cache_clear()
                orig = W.config
                W.config = lambda u=url: dict(saved, service_url=u)       # noqa: E731
                try:
                    self.assertEqual(FB.service_url(), want)
                finally:
                    W.config = orig
        finally:
            W.config.cache_clear()


PRIVATE_KEY = os.path.join(ROOT, "private", "feedback_server_key.txt")
SERVER_APP = os.path.join(ROOT, "server", "feedback", "app.py")


@unittest.skipUnless(os.path.isfile(PRIVATE_KEY) and os.path.isfile(W.CLI_EXE) and os.path.isfile(SERVER_APP),
                     "没有私有密钥 / 运行时 / 服务端代码（公开仓库和内测包里本来就没有）")
class FeedbackEndToEndTest(unittest.TestCase):
    """真运行时签名 -> 真服务端验签：两边用的是不是同一把钥匙、同一个签名格式。"""

    def setUp(self):
        import uvicorn
        sys.path.insert(0, os.path.join(ROOT, "server", "feedback"))
        import app as SRV
        self.tmp = tempfile.TemporaryDirectory()
        key = bytes.fromhex([ln for ln in open(PRIVATE_KEY, encoding="utf-8") if not ln.startswith("#")][0].strip())
        self.srv_app = SRV.create_app(key=key, db_path=os.path.join(self.tmp.name, "fb.db"), trust_proxy=False)
        self.server = uvicorn.Server(uvicorn.Config(self.srv_app, host="127.0.0.1", port=0, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.saved = (FB.service_url, FB.HISTORY, FB.OUTBOX, FB.DIR)
        FB.service_url = lambda: f"http://127.0.0.1:{port}"               # noqa: E731
        FB.DIR = self.tmp.name
        FB.HISTORY = os.path.join(self.tmp.name, "history.jsonl")
        FB.OUTBOX = os.path.join(self.tmp.name, "outbox")

    def tearDown(self):
        FB.service_url, FB.HISTORY, FB.OUTBOX, FB.DIR = self.saved
        self.server.should_exit = True
        time.sleep(0.3)
        self.srv_app.state.store.db.close()
        self.tmp.cleanup()

    def test_submit_signed_by_runtime(self):
        r = FB.submit({"kind": "issue", "title": "端到端测试", "text": "运行时签名，服务端验签", "diag": False})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["ref"].startswith("FB-"))
        again = FB.submit({"kind": "issue", "title": "马上再发", "text": "x"})
        self.assertEqual(again["error"], "too-fast")                        # 本机限流：15 秒一条
        self.assertEqual(len(FB.history()["rows"]), 1)

    def test_offline_goes_to_outbox_then_retry(self):
        port = FB.service_url()
        FB.service_url = lambda: "http://127.0.0.1:9"                     # noqa: E731  没人听的端口
        r = FB.submit({"kind": "suggestion", "title": "离线", "text": "先存着"})
        self.assertEqual(r["error"], "offline")
        rid = r["record"]["id"]
        self.assertTrue(os.path.isfile(os.path.join(FB.OUTBOX, rid + ".json")))
        FB.service_url = lambda: port                                       # noqa: E731
        self.assertTrue(FB.retry(rid)["ok"])
        self.assertFalse(os.path.exists(os.path.join(FB.OUTBOX, rid + ".json")))
        self.assertEqual(FB.retry("not-an-id")["error"], "bad-id")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""远见「头顶气泡」页的转发（09-26，console/server/speechview.py）：只放行气泡服务的那几个接口；服务没起来回 speech-down；
查询参数 / 请求体原样转过去，气泡服务的报错（状态码 + detail）原样带回；汇总接口把三个功能的开关、实例在不在跑算对。
用一个本机的假气泡服务测，不需要开游戏、也不需要真的 speak_api。"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import services as S  # noqa: E402
from server import speechview as SP  # noqa: E402

SEEN: list = []


class Fake(BaseHTTPRequestHandler):
    """假的 speak_api：5 号实例在跑、茶话会开着；别的实例一律 404（和真的 resolve_pid 一样）。"""

    def log_message(self, *a):  # noqa: D401  别往测试输出里打访问日志
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        SEEN.append(("GET", u.path, q))
        inst = q.get("inst", [None])[0]
        if u.path in ("/api/banter", "/api/camtalk", "/api/events"):
            if inst != "5":
                return self._send(404, {"detail": f"没有在跑的 {inst} 号实例"})
            return self._send(200, {"running": u.path == "/api/banter", "config": {}})
        if u.path == "/api/autostart":
            return self._send(200, {"autostart": {"banter": [5], "camtalk": [], "events": []}})
        if u.path == "/api/config":
            return self._send(200, {"config": {"avoid_overlap": True}})
        if u.path == "/api/llm":
            return self._send(200, {"config": {"model": "m"}, "reachable": False, "models": []})
        return self._send(404, {"detail": "no"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        SEEN.append(("POST", self.path, body))
        if body.get("inst") == 9:
            return self._send(404, {"detail": "没有在跑的 9 号实例"})
        return self._send(200, {"ok": True, "echo": body})


class SpeechProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.saved = (SP.port, S._listening)
        SP.port = lambda: cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        SP.port, S._listening = cls.saved
        cls.srv.shutdown()

    def setUp(self):
        S._listening = lambda port, fresh=False: True
        SEEN.clear()

    def test_only_whitelisted_paths(self):
        self.assertEqual(SP.call("GET", "units")[0], 404)          # 单位走远见自己的快照，不转发
        self.assertEqual(SP.call("GET", "../admin")[0], 404)
        self.assertEqual(SEEN, [])

    def test_down_when_nobody_listens(self):
        S._listening = lambda port, fresh=False: False
        self.assertEqual(SP.call("GET", "banter", {"inst": 5}), (503, {"detail": SP.DOWN}))
        self.assertEqual(SP.overview(5), {"up": False})

    def test_forwards_query_body_and_errors(self):
        st, d = SP.call("GET", "banter", {"inst": 5, "empty": ""})
        self.assertEqual((st, d["running"]), (200, True))
        self.assertEqual(SEEN[-1][2], {"inst": ["5"]})              # 空参数不转
        st, d = SP.call("POST", "banter/poke", None, {"inst": 5, "topic": "打野"})
        self.assertEqual((st, d["echo"]["topic"]), (200, "打野"))
        self.assertEqual(SP.call("POST", "say", None, {"inst": 9}), (404, {"detail": "没有在跑的 9 号实例"}))

    def test_overview(self):
        ov = SP.overview(5)
        self.assertTrue(ov["up"] and ov["instanceUp"] and ov["avoidOverlap"])
        self.assertEqual({k: v["running"] for k, v in ov["features"].items()}, {"banter": True, "camtalk": False, "events": False})
        self.assertEqual(ov["autostart"]["banter"], [5])
        self.assertEqual(ov["llm"]["config"]["model"], "m")
        ov = SP.overview(6)                                          # 6 号的游戏没在跑
        self.assertFalse(ov["instanceUp"])
        self.assertFalse(any(v["running"] for v in ov["features"].values()))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""网关协议（09-25）：握手、调用、订阅推送、出错只回这一条、令牌。会话换成替身，不需要游戏。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server as S  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeG:
    pid = 4242
    _ui = None


class FakeSession:
    def __init__(self, inst, pid, role, player, fair):
        if role == "player" and player is None:
            raise S.R.RemoteError("role=player 要带 player=玩家号")
        self.role, self.g, self.sent = role, FakeG(), 0

    async def run(self, fn, *a):
        return fn(*a)

    def call(self, method, args, kwargs):
        if method == "boom":
            raise S.R.RemoteError("没有方法 'boom'")
        return {"method": method, "args": args, "kwargs": kwargs}

    def jass(self, code):
        if self.role != "dev":
            raise S.R.RemoteError("JASS 脚本只给 dev 角色")
        return [{"line": 1, "code": code}]

    def state(self, units, detail):
        return {"clock": 1.0, "units": [], "which": units}

    def events(self):
        self.sent += 1
        return [{"kind": "ui.click", "key": "shop"}] if self.sent == 1 else []

    def close(self):
        pass


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self._orig = S.Session
        S.Session = FakeSession
        S.CONFIG.update(require_token=False, token="")
        self.c = TestClient(S.app)

    def tearDown(self):
        S.Session = self._orig

    def test_http_index_and_api(self):
        self.assertEqual(self.c.get("/").json()["protocol"], S.PROTOCOL)
        obs = {m["name"] for m in self.c.get("/api", params={"role": "observer"}).json()["methods"]}
        self.assertIn("units", obs)
        self.assertNotIn("move", obs)
        self.assertEqual(self.c.get("/api", params={"role": "god"}).status_code, 400)

    def test_ws_call_error_and_subscribe(self):
        with self.c.websocket_connect("/ws?inst=9&role=dev") as ws:
            hello = ws.receive_json()
            self.assertEqual((hello["type"], hello["role"], hello["pid"]), ("hello", "dev", 4242))
            ws.send_json({"id": 1, "op": "call", "method": "units", "args": ["me"]})
            self.assertEqual(ws.receive_json(), {"id": 1, "ok": True, "result": {"method": "units", "args": ["me"], "kwargs": None}})
            ws.send_json({"id": 2, "op": "call", "method": "boom"})
            r = ws.receive_json()
            self.assertEqual((r["id"], r["ok"]), (2, False))
            ws.send_json({"id": 3, "op": "jass", "code": "call X()"})
            self.assertTrue(ws.receive_json()["ok"])                          # 出错之后连接还在
            ws.send_json({"id": 4, "op": "subscribe", "state": {"hz": 20, "units": "mine"}, "events": True})
            got = {}
            for _ in range(6):
                m = ws.receive_json()
                got.setdefault(m.get("type") or f"id{m.get('id')}", m)
                if {"events", "state", "id4"} <= set(got):
                    break
            self.assertEqual(got["events"]["events"][0]["key"], "shop")
            self.assertEqual(got["state"]["state"]["which"], "mine")
            ws.send_json({"id": 5, "op": "nope"})
            for _ in range(20):
                m = ws.receive_json()
                if m.get("id") == 5:
                    break
            self.assertFalse(m["ok"])

    def test_player_role_needs_player_and_no_jass(self):
        with self.c.websocket_connect("/ws?inst=9&role=player") as ws:
            self.assertEqual(ws.receive_json()["type"], "error")
        with self.c.websocket_connect("/ws?inst=9&role=player&player=0") as ws:
            ws.receive_json()
            ws.send_json({"id": 1, "op": "jass", "code": "x"})
            self.assertFalse(ws.receive_json()["ok"])

    def test_foreign_web_page_is_rejected(self):
        # 09-26 审查：浏览器里任何网页都能连本机网关（默认不要令牌）拿 dev 角色跑 JASS —— 按 Origin 挡
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect("/ws?inst=9", headers={"origin": "http://evil.example"}) as ws:
                ws.receive_json()
        with self.c.websocket_connect("/ws?inst=9", headers={"origin": "http://127.0.0.1:8870"}) as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")          # 网关自己的演示页
        with self.c.websocket_connect("/ws?inst=9") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")          # 不带 Origin 的程序（Python / Node）
        r = self.c.post("/call", json={"inst": 9, "method": "units"}, headers={"origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_bad_messages_keep_the_connection(self):
        with self.c.websocket_connect("/ws?inst=9&role=dev") as ws:
            ws.receive_json()
            ws.send_text("这不是 JSON")
            self.assertFalse(ws.receive_json()["ok"])
            ws.send_json({"id": 2, "op": "subscribe", "state": {"hz": "快", "units": "mine"}})
            self.assertFalse(ws.receive_json()["ok"])                         # 参数当场报错，不是推送任务悄悄死掉
            ws.send_json({"id": 3, "op": "ping"})
            self.assertEqual(ws.receive_json()["result"], "pong")

    def test_token(self):
        S.CONFIG.update(require_token=True, token="abc")
        with self.c.websocket_connect("/ws?inst=9") as ws:
            self.assertEqual(ws.receive_json()["type"], "error")
        with self.c.websocket_connect("/ws?inst=9&token=abc") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")
        self.assertEqual(self.c.post("/call", json={"inst": 9, "method": "units"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()

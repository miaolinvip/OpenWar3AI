# -*- coding: utf-8 -*-
"""远程调用层（网关 / MCP 共用）和 MCP 服务器（09-25）：角色白名单、单位参数、JSON 形状、JSON-RPC 握手和工具调用（不需要游戏）。"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3world as W  # noqa: E402
from openwar3 import remote as R  # noqa: E402
from openwar3.game import Game  # noqa: E402
from openwar3.mcp import Server  # noqa: E402


def unit(addr, typ="hpea", owner=0, hero=False):
    u = W.Unit(0, addr, addr + 1, 2, typ, owner, W.ALIVE | (W.HERO if hero else 0), 10.0, 20.0, 100.0, 220.0,
               50.0 if hero else 0.0, 100.0 if hero else 0.0, None, None, 0.0, 0.0, None, None, None,
               3 if hero else 0, 0, 0, 1)
    return u


class _Snap:
    def __init__(self, units):
        self.units = units


class FakeGame(Game):
    """不走 __init__ 的替身：只给 remote / mcp 用到的几样。"""

    def __init__(self):  # noqa: D401 - 故意不调父类
        self.moved = []
        self._units = [unit(100), unit(200, "Hpal", hero=True), unit(300, "ogru", owner=1)]

    def snapshot(self, max_age=0.05):
        return _Snap(self._units)

    def units(self, owner="all", types=None, alive=True):
        us = [u for u in self._units if owner == "all" or (owner == "me" and u.owner == 0) or (owner == "enemy" and u.owner == 1)]
        return [u for u in us if not types or u.type in types]

    def move(self, units, x, y, queue=False):
        self.moved.append(([u.addr for u in units], x, y))
        return [True for _ in units]

    def events(self):
        return [W.Event(7, "spell.cast", 12.5, 200, (201, 2), "Hpal", 0, W.rawcode("AHhb"), 1, 1.0, 2.0, 9.0, 0),
                W.Event(8, "order.changed", 12.6, 100, (101, 2), "hpea", 0, 1, 2, 0.0, 0.0, 0.0, 0)]


class RemoteTest(unittest.TestCase):
    def test_role_whitelist(self):
        dev = {e["name"] for e in R.catalog("dev")}
        player = {e["name"] for e in R.catalog("player")}
        obs = {e["name"] for e in R.catalog("observer")}
        self.assertIn("spawn", dev)                   # 沙盒
        self.assertNotIn("spawn", player)
        self.assertIn("move", player)
        self.assertNotIn("move", obs)
        self.assertIn("units", obs)
        self.assertIn("ui.button", player)
        self.assertNotIn("ui.button", obs)
        g = FakeGame()
        with self.assertRaises(R.RemoteError):
            R.resolve(g, "move", "observer")
        with self.assertRaises(R.RemoteError):
            R.resolve(g, "jass.CreateUnit", "player")
        with self.assertRaises(R.RemoteError):
            R.resolve(g, "__class__", "dev")

    def test_unit_args_and_call(self):
        g = FakeGame()
        out = R.call(g, "move", "player", [[{"unit": 100}, {"addr": 200}], 5.0, 6.0])
        self.assertEqual(g.moved, [([100, 200], 5.0, 6.0)])
        self.assertEqual(out, [True, True])
        with self.assertRaises(R.RemoteError):
            R.resolve_args(g, {"unit": 999})
        with self.assertRaises(R.RemoteError):
            R.resolve_args(g, {"unit": 100, "handle": [1, 1]})       # 句柄对不上 = 地址被复用了

    def test_player_role_cannot_run_the_match_or_peek(self):
        # 09-26 审查：player 角色原来能关游戏、改倍速、暂停，公平模式下还能查别人的资源 / 电脑的进攻目标
        player = {e["name"] for e in R.catalog("player")}
        for name in ("end_game", "set_speed", "pause", "set_publish_period", "players", "enemy_ai_plan"):
            self.assertNotIn(name, player, name)
            self.assertIn(name, {e["name"] for e in R.catalog("dev")}, name)
        self.assertNotIn("canvas.image", player)                      # 会让游戏进程打开本机任意文件
        g = FakeGame()
        g.player = 0
        g.resources = lambda player=None: {"player": player}
        with self.assertRaises(R.RemoteError):
            R.call(g, "resources", "player", [1])                     # 查别人
        self.assertEqual(R.call(g, "resources", "player", [0]), {"player": 0})
        self.assertEqual(R.call(g, "resources", "player"), {"player": None})
        with self.assertRaises(R.RemoteError):
            R.resolve(g, "canvas.image", "player")

    def test_ui_methods_have_real_signatures(self):
        e = next(x for x in R.catalog("dev") if x["name"] == "ui.button")
        self.assertIn("on_click", e["signature"])                     # 以前只有 ui.button(...)，大模型只能盲调

    def test_json_shapes(self):
        u = R.to_json(unit(200, "Hpal", hero=True))
        self.assertEqual((u["type"], u["hero"], u["level"], u["handle"]), ("Hpal", True, 3, [201, 2]))
        e = R.to_json(FakeGame().events()[0])
        self.assertEqual((e["kind"], e["spell"]), ("spell.cast", "AHhb"))
        self.assertEqual(json.loads(json.dumps(R.to_json({"a": (1, 2.5, float("nan")), "b": b"x"}))), {"a": [1, 2.5, None], "b": None})


class McpTest(unittest.TestCase):
    def rpc(self, server, *msgs):
        inp = io.StringIO("".join(json.dumps(m) + "\n" for m in msgs))
        out = io.StringIO()
        server.serve(inp, out)
        return [json.loads(ln) for ln in out.getvalue().splitlines()]

    def test_handshake_list_and_call(self):
        s = Server(None, None, "player", 0)             # 不绑实例号：绑了会按 9 号的真实进程号「换局重连」，把替身换掉
        s._g = FakeGame()
        r = self.rpc(s,
                     {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                     {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "war3_units", "arguments": {"owner": "enemy"}}},
                     {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "war3_call", "arguments": {"method": "move", "args": [[{"unit": 100}], 1, 2]}}},
                     {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "war3_events", "arguments": {}}},
                     {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "war3_jass", "arguments": {"code": "x"}}},
                     {"jsonrpc": "2.0", "id": 7, "method": "nope"})
        self.assertEqual([m.get("id") for m in r], [1, 2, 3, 4, 5, 6, 7])          # 通知没有回应
        self.assertEqual(r[0]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in r[1]["result"]["tools"]}
        self.assertIn("war3_ask_player", names)
        self.assertNotIn("war3_jass", names)                                   # player 角色没有 JASS
        self.assertEqual(json.loads(r[2]["result"]["content"][0]["text"])[0]["type"], "ogru")
        self.assertEqual(s._g.moved, [([100], 1, 2)])
        kinds = [e["kind"] for e in json.loads(r[4]["result"]["content"][0]["text"])]
        self.assertEqual(kinds, ["spell.cast"])                                 # order.changed 默认滤掉
        self.assertTrue(r[5]["result"]["isError"])
        self.assertEqual(r[6]["error"]["code"], -32601)

    def test_malformed_messages_do_not_kill_the_server(self):
        s = Server(None, None, "dev", None)
        s._g = FakeGame()
        r = self.rpc(s, [1, 2], {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": [1]},
                     {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "war3_units", "arguments": "x"}},
                     {"jsonrpc": "2.0", "id": 5, "method": "ping"})
        self.assertEqual([m["error"]["code"] for m in r[:4]], [-32600, -32600, -32602, -32602])
        self.assertEqual(r[4], {"jsonrpc": "2.0", "id": 5, "result": {}})              # 后面的照常处理


if __name__ == "__main__":
    unittest.main()

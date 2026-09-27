# -*- coding: utf-8 -*-
"""远见「事件与输入」「接入与扩展」（09-25）：事件解码成网页要的 JSON；高频事件分开放、默认不给；MCP 工具表和 SDK 的一致。"""
import os
import sys
import unittest
from collections import deque
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import connectview as CN  # noqa: E402
from server import eventsview as EV  # noqa: E402

import w3world as W  # noqa: E402


def ev(seq, kind, **kw):
    base = dict(clock=12.5, addr=0, handle=(0, 0), type="", owner=0, a=0, b=0, x=0.0, y=0.0, value=0.0, extra=0)
    base.update(kw)
    return W.Event(seq, kind, **base)


class FakeWorld:
    def __init__(self, events=(), messages=None):
        self._events = list(events)
        self._messages = messages or {}
        self.dropped = 0

    def message(self, seq):
        text = self._messages.get(seq)
        return {"seq": seq, "text": text, "frame": 1, "repeat": 0, "game_ms": 0} if text else None

    def events(self, limit=4096):
        out, self._events = self._events, []
        return out

    def read(self):
        return None


class DecodeTest(unittest.TestCase):
    def test_chat_message_is_split(self):
        w = FakeWorld(messages={7: "[所有人] |cffff0303War3AI: |rchat test 42"})
        d = EV._decode(w, ev(1, "message", a=7, b=1), None)
        self.assertEqual(d["chat"], {"channel": "所有人", "sender": "War3AI", "text": "chat test 42"})
        self.assertEqual(d["frame"], 1)

    def test_overwritten_message_has_no_text(self):
        d = EV._decode(FakeWorld(), ev(1, "message", a=99), None)
        self.assertIsNone(d["text"])
        self.assertIsNone(d["chat"])

    def test_spell_cast_fields(self):
        d = EV._decode(FakeWorld(), ev(1, "spell.cast", a=W.rawcode("AHds"), b=1, value=35.0, type="Hpal"), None)
        self.assertEqual((d["spell"], d["level"], d["cooldown"]), ("AHds", 1, 35.0))

    def test_player_left_and_ui_click(self):
        d = EV._decode(FakeWorld(), ev(1, "player.left", a=1, b=2), None)
        self.assertEqual((d["player"], d["slot"]), (1, "left"))
        d = EV._decode(FakeWorld(), ev(2, "ui.click", a=5, b=2, extra=W.MOD_CTRL), None)
        self.assertEqual((d["item"], d["button"], d["mods"]), (5, "right", ["ctrl"]))


class RingTest(unittest.TestCase):
    def setUp(self):
        self.world = FakeWorld([ev(1, "order.changed"), ev(2, "unit.died", type="hfoo"), ev(3, "damage"), ev(4, "spell.cast", a=W.rawcode("AHhb"))])
        EV._state[99] = {"pid": 4242, "world": self.world, "ring": deque(maxlen=EV.RING), "noisy": deque(maxlen=EV.NOISY_RING), "input": False}
        self._open = EV._open
        EV._open = lambda n: EV._state[n]

    def tearDown(self):
        EV._open = self._open
        EV._state.pop(99, None)

    def test_noisy_hidden_by_default_but_head_counts_them(self):
        r = EV.events(99, 0, 100)
        self.assertTrue(r["ok"])
        self.assertEqual([e["kind"] for e in r["events"]], ["unit.died", "spell.cast"])
        self.assertEqual(r["head"], 4)
        r = EV.events(99, 0, 100, noisy=True)
        self.assertEqual([e["seq"] for e in r["events"]], [1, 2, 3, 4])
        self.assertEqual(EV.events(99, 4, 100)["events"], [])


class McpToolsTest(unittest.TestCase):
    def test_tool_names_match_sdk(self):
        from openwar3 import mcp
        sdk = {t["name"] for t in mcp._tools("dev")}
        self.assertEqual(sdk, {t[0] for t in CN.MCP_TOOLS})


if __name__ == "__main__":
    unittest.main()

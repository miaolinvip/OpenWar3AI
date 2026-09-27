# -*- coding: utf-8 -*-
"""界面与输入 + 事件补齐（09-25）：常量 / 布局对上 C 头文件、热键写法、聊天解析、事件便捷字段、g.ui 的分发（不需要游戏）。"""
import os
import re
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3canvas as C  # noqa: E402
import w3fast as F  # noqa: E402
import w3input as I  # noqa: E402
import w3world as W  # noqa: E402
from openwar3 import ui as U  # noqa: E402

HEADER_H = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "native", "src", "ui_control_protocol.h")
HAS_HEADER = os.path.exists(HEADER_H)       # 发布包可能不带 native/：对头文件的核对跳过，其余照跑
SRC = open(HEADER_H, encoding="utf-8").read() if HAS_HEADER else ""


def const(name):
    m = re.search(rf"\b{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", SRC)
    assert m, name
    return int(m.group(1), 0)


def ev(kind, a=0, b=0, x=0.0, y=0.0, value=0.0, extra=0):
    return W.Event(1, kind, 0.0, 0, (0, 0), "", 0xFFFFFFFF, a, b, x, y, value, extra)


@unittest.skipUnless(HAS_HEADER, "没有 native/ 的头文件")
class ProtocolTest(unittest.TestCase):
    def test_new_event_kinds(self):
        for kind, name in ((13, "kEvSpellCast"), (14, "kEvPlayerLeft"), (15, "kEvSelection"), (16, "kEvMessage"),
                           (20, "kEvUiClick"), (21, "kEvUiHover"), (22, "kEvHotkey"), (23, "kEvMouseWorld"),
                           (101, "kEvGameEnded")):
            self.assertEqual(const(name), kind)
            self.assertIn(kind, W.EVENT_KINDS)
        self.assertEqual(W.EVENT_KINDS[13], "spell.cast")
        self.assertEqual(W.EVENT_KINDS[101], "game.ended")

    def test_input_block(self):
        self.assertEqual(const("kWar3InputMagic"), I.MAGIC)
        self.assertEqual(const("kWar3InputLayout"), I.LAYOUT)
        self.assertEqual(const("kWar3InputHotkeys"), I.HOTKEYS)
        self.assertIn("offsetof(War3InputBlock, hotkeys) == 128", SRC)
        self.assertEqual(I.HDR, 128)
        self.assertEqual((const("kHotkeySwallow"), const("kHotkeyRepeat")), (I.SWALLOW, I.REPEAT))
        self.assertEqual((const("kMouseReportLeft"), const("kMouseReportRight"), const("kMouseCaptureLeft"),
                          const("kMouseCaptureRight")),
                         (I.MOUSE_REPORT_LEFT, I.MOUSE_REPORT_RIGHT, I.MOUSE_CAPTURE_LEFT, I.MOUSE_CAPTURE_RIGHT))
        self.assertEqual((const("kModCtrl"), const("kModShift"), const("kModAlt")), (1, 2, 4))
        self.assertEqual(I.MOD, {"ctrl": 1, "shift": 2, "alt": 4})
        self.assertEqual(16 + I._STATE.size, I.O_MOUSE_FLAGS)

    def test_msgs_and_canvas_and_op(self):
        self.assertEqual(const("kWar3MsgsMagic"), W.MSGS_MAGIC)
        self.assertEqual((const("kWar3MsgSlots"), const("kWar3MsgBytes") + 16), (W.MSG_SLOTS, W.MSG_SIZE))
        self.assertIn("offsetof(War3MsgRing, msgs) == 32", SRC)
        self.assertEqual(const("kCanvasFlagInteractive"), C.FLAG["interactive"])
        self.assertEqual(const("kInputEnable"), F.OP["input_enable"])

    def test_world_ext_selection_layout(self):
        # 扩展区：magic tod todScale prodCount | selCount selLeader selAddrs[12] slotStates | reserved[13] = 128
        self.assertIn("DWORD selAddrs[12];", SRC)
        self.assertIn("DWORD reserved[13];", SRC)
        self.assertEqual(4 * 4 + 4 + 4 + 48 + 4 + 13 * 4, W.EXT_SIZE)


class KeysAndChatTest(unittest.TestCase):
    def test_parse_key(self):
        self.assertEqual(I.parse_key("F5"), (0x74, 0))
        self.assertEqual(I.parse_key("ctrl+shift+Q"), (ord("Q"), 3))
        self.assertEqual(I.parse_key("Alt+1"), (ord("1"), 4))
        self.assertEqual(I.parse_key("numpad3"), (0x63, 0))
        self.assertEqual(I.parse_key("esc"), (0x1B, 0))
        with self.assertRaises(ValueError):
            I.parse_key("hyper+x")
        with self.assertRaises(ValueError):
            I.parse_key("F99")

    def test_parse_chat(self):
        c = W.parse_chat("[所有人] |cffff0303War3AI: |rchat test 42")
        self.assertEqual(c, {"channel": "所有人", "sender": "War3AI", "text": "chat test 42"})
        c = W.parse_chat("|cff0042ffBob: |r|cffffcc00gg|r")
        self.assertEqual((c["channel"], c["sender"], c["text"]), (None, "Bob", "gg"))
        self.assertIsNone(W.parse_chat("需要更多的农场"))
        self.assertIsNone(W.parse_chat(""))

    def test_event_helpers(self):
        e = ev("spell.cast", a=W.rawcode("AHds"), b=1, value=35.0, extra=7)
        self.assertEqual((e.spell, e.target_handle_lo), ("AHds", 7))
        self.assertEqual(ev("ui.click", a=3, b=2, extra=1 | 4).mods, {"ctrl", "alt"})
        self.assertEqual(ev("ui.click", a=3, b=2).button, "right")
        self.assertEqual(ev("mouse.world", a=1).button, "left")
        self.assertEqual(ev("player.left", a=5).player, 5)
        self.assertEqual(ev("hotkey", a=9).hotkey_id, 9)
        self.assertIsNone(ev("damage").spell)


class _Canvas:
    def __init__(self):
        self.keys, self.removed, self.items = {}, [], {}

    def key_of(self, i):
        return self.keys.get(i)

    def remove(self, k):
        self.removed.append(k)

    def text(self, key, text, **kw):
        self.items[key] = (text, kw)

    def batch(self):
        import contextlib
        return contextlib.nullcontext()


class _Block:
    def __init__(self):
        self.flags, self.hot = 0, []

    def set_hotkeys(self, keys):
        self.hot = list(keys)

    def set_mouse(self, f):
        self.flags = f

    def mouse_flags(self):
        return self.flags

    def state(self):
        return {"back": (1920, 1080), "cursor": None, "world": None, "hover": 0}


class _G:
    def __init__(self):
        self.paused, self.queue, self._event_backlog = [], [], []
        self.world = self

    def pause(self, on=True):
        self.paused.append(on)

    def events(self):
        out, self.queue = self.queue, []
        return out


def make_ui():
    u = object.__new__(U.UI)
    u.g, u.canvas, u.block = _G(), _Canvas(), _Block()
    u._click, u._hotkeys, u._choices, u._toasts, u.clicks = {}, {}, {}, {}, []
    u._next_hotkey, u._mouse_cb, u._pick, u.hover = 1, None, None, None
    return u


class UiDispatchTest(unittest.TestCase):
    def test_button_callback_and_unclaimed_click(self):
        u = make_ui()
        hits = []
        u.button("shop", "买药", screen=(10, 10), on_click=lambda g, e: hits.append(e.key))
        u.canvas.keys = {1: "shop", 2: "other"}
        u.handle([ev("ui.click", a=1, b=1), ev("ui.click", a=2, b=1)])
        self.assertEqual(hits, ["shop"])
        self.assertEqual([k for k, _e in u.clicks], ["other"])
        self.assertIn("clickable", u.canvas.items["shop"][1])

    def test_choice_pick_closes_and_unpauses(self):
        u = make_ui()
        picked = []
        ch = u.choice("选一个", ["A", ("B", "说明")], key="c", pause=True, on_pick=lambda g, i: picked.append(i))
        self.assertEqual(u.g.paused, [True])
        self.assertTrue({"c.title", "c.0", "c.1"} <= set(u.canvas.items))
        self.assertIn("B\n\n说明", u.canvas.items["c.1"][0])
        u.canvas.keys = {5: "c.1"}
        u.handle([ev("ui.click", a=5, b=1)])
        self.assertEqual((ch.picked, picked, ch.closed), (1, [1], True))
        self.assertEqual(u.g.paused, [True, False])
        self.assertIn("c.0", u.canvas.removed)

    def test_hotkey_registration_and_dispatch(self):
        u = make_ui()
        got = []
        hid = u.hotkey("ctrl+F7", lambda g, e: got.append(e.key))
        self.assertEqual(u.block.hot, [(hid, 0x76, 1, I.SWALLOW)])
        u.handle([ev("hotkey", a=hid, b=0x76, extra=1)])            # 运行时的热键事件：a = id、b = 键码、extra = 修饰键
        u.handle([ev("hotkey", a=hid, b=0x76, extra=0)])            # 修饰键不一样：不是它
        self.assertEqual(got, ["ctrl+F7"])
        u.remove_hotkey(hid)
        self.assertEqual(u.block.hot, [])

    def test_pick_point_and_backlog(self):
        u = make_ui()
        u.block.flags = I.MOUSE_REPORT_RIGHT
        u.g.queue = [ev("unit.died"), ev("mouse.world", a=1, x=100.0, y=-50.0, value=1.0)]
        self.assertEqual(u.pick_point(timeout=1.0), (100.0, -50.0))
        self.assertEqual(u.block.flags, I.MOUSE_REPORT_RIGHT)          # 开关还原
        # 别的事件没丢；等待时就地分发过的界面事件也留着（标成已分发，下一次 events() 不会再跑一遍回调）
        self.assertEqual([e.kind for e in u.g._event_backlog], ["unit.died", "mouse.world"])
        self.assertTrue(u.g._event_backlog[1]._handled)
        self.assertEqual(u.block.hot, [])                                # Esc 热键收回

    def test_pick_point_cancel_with_esc(self):
        u = make_ui()
        u.g.queue = [ev("hotkey", a=1, b=0x1B, extra=0)]                  # Esc（按键码认，id 可能是别的客户端的）
        self.assertIsNone(u.pick_point(timeout=1.0))


if __name__ == "__main__":
    unittest.main()

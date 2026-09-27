# -*- coding: utf-8 -*-
"""画板（09-25）：Python 打包的布局和 C 头文件一致（字段偏移、条目大小、块大小）；颜色写法；
只有影响贴图的内容变了才涨 version（位置变了不重画）；锚点。共享内存换成假的。"""
import os
import re
import struct
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402

w3paths.setup()
import w3canvas as W  # noqa: E402
from openwar3.canvas import Canvas  # noqa: E402

HEADER = os.path.join(w3paths.ROOT, "native", "src", "ui_control_protocol.h")


class LayoutTest(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(HEADER), "没有闭源侧的头文件（开源发布包不带 native/）")
    def test_matches_c_header(self):
        with open(HEADER, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(f"kWar3CanvasItems = {W.ITEMS};", src)
        self.assertIn("kWar3CanvasPool = 64 * 1024;", src)
        self.assertIn(f"sizeof(War3CanvasItem) == {W.ITEM.size}", src)
        self.assertIn(f"offsetof(War3CanvasBlock, items) == {W.HDR}", src)
        m = re.search(r"kWar3CanvasMagic = (0x[0-9A-Fa-f]+)", src)
        self.assertEqual(int(m.group(1), 16), W.MAGIC)
        for name, v in (("kCanvasText", 1), ("kCanvasBar", 2), ("kCanvasImage", 3), ("kCanvasCircle", 4), ("kCanvasPath", 5)):
            self.assertIn(f"{name} = {v},", src)
        for name, v in (("Shadow", 0x1), ("Bold", 0x2), ("Center", 0x4), ("Arrow", 0x8), ("Hidden", 0x10), ("OverUi", 0x20)):
            self.assertRegex(src, rf"kCanvasFlag{name} = 0x{v:X};")

    def test_pack_fields(self):
        raw, pool, n = W.pack([{"id": 7, "kind": "path", "anchor": "world", "points": [(1.5, 2.5), (3, 4)],
                                "text": "你好", "color": (255, 0, 0), "flags": W.FLAG["arrow"], "version": 3, "layer": 2}])
        self.assertEqual(n, 1)
        f = W.ITEM.unpack(raw)
        self.assertEqual(f[0:4], (7, 5, 2, 0x8))                      # id kind anchor flags
        self.assertEqual(f[16], 0xFFFF0000)                            # color
        t_off, t_len, p_off, p_n = f[19:23]
        self.assertEqual(pool[t_off:t_off + t_len].decode(), "你好")
        self.assertEqual(struct.unpack_from("<4f", pool, p_off), (1.5, 2.5, 3.0, 4.0))
        self.assertEqual(p_n, 2)
        self.assertEqual(f[23:25], (3, 2))                             # version layer

    def test_colors(self):
        self.assertEqual(W.argb("#FF8000"), 0xFFFF8000)
        self.assertEqual(W.argb("#FF800040"), 0x40FF8000)
        self.assertEqual(W.argb((1, 2, 3, 4)), 0x04010203)
        self.assertEqual(W.argb(None, 7), 7)


class FakeBlock:
    def __init__(self):
        self.writes = []

    def write(self, items):
        self.writes.append([dict(i) for i in items])
        return len(items)


def canvas():
    c = Canvas.__new__(Canvas)
    c.g, c.block, c.items, c._ids, c._next_id, c._batch = None, FakeBlock(), {}, {}, 1, 0
    return c


class CanvasTest(unittest.TestCase):
    def test_version_only_bumps_on_content(self):
        c = canvas()
        u = NS(addr=0x100, handle_lo=5, handle_hi=6, x=0.0, y=0.0)
        c.bar("hp", 0.5, unit=u, lift=100)
        self.assertEqual(c.items["hp"]["version"], 1)
        c.bar("hp", 0.5, unit=u, lift=150)                           # 只挪位置：不用重画贴图
        self.assertEqual(c.items["hp"]["version"], 1)
        c.bar("hp", 0.4, unit=u, lift=150)                           # 比例变了：重画
        self.assertEqual(c.items["hp"]["version"], 2)
        self.assertEqual((c.items["hp"]["anchor"], c.items["hp"]["unit"], c.items["hp"]["handle_lo"]), ("unit", 0x100, 5))

    def test_anchors_batch_and_remove(self):
        c = canvas()
        with c.batch():
            c.text("a", "hi", screen=(-200, 50))
            c.circle("b", (100.0, 200.0), 300, fill=(255, 0, 0, 40))
            c.circle("c", NS(addr=9, handle_lo=1, handle_hi=2, x=0, y=0), 500)
            c.path("d", [(0, 0), NS(x=10.0, y=20.0)])
        self.assertEqual(len(c.block.writes), 1)                     # batch 里只写一次
        self.assertEqual((c.items["a"]["anchor"], c.items["a"]["x"]), ("screen", -200.0))
        self.assertEqual((c.items["b"]["anchor"], c.items["b"]["w"]), ("world", 300.0))
        self.assertEqual(c.items["c"]["anchor"], "unit")
        self.assertEqual(c.items["d"]["points"], [(0.0, 0.0), (10.0, 20.0)])
        ids = {c.items[k]["id"] for k in "abcd"}
        self.assertEqual(len(ids), 4)
        c.remove("b")
        self.assertEqual(len(c.block.writes[-1]), 3)
        with self.assertRaises(ValueError):
            c.text("x", "没有位置")


if __name__ == "__main__":
    unittest.main()

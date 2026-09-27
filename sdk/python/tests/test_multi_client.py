# -*- coding: utf-8 -*-
"""多个客户端同时用画板 / 输入（09-26）：以前谁写谁整张覆盖 —— 实测进程 B 一画，进程 A 的按钮和热键全没了。
现在画板按主人读-改-写（别人的条目原样留着、偏移重排、死进程的清掉），输入按客户端登记合并（同一个键合成一条）。"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3canvas as C  # noqa: E402
import w3input as I  # noqa: E402

A, B, DEAD = (1111, 1), (2222, 1), (3333, 1)
ALIVE = {1111, 2222}.__contains__
HEADER_H = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "native", "src", "ui_control_protocol.h")


def flat_fields(src, struct_name):
    """C 结构体的字段按 4 字节一格摊平：[(名字, 下标)]；数组 x[N] 摊成 N 个 (x, 0..N-1)。"""
    body = re.search(rf"struct {struct_name} \{{(.*?)\n\}};", src, re.S).group(1)
    body = re.sub(r"//[^\n]*", "", body)
    out = []
    for _typ, names in re.findall(r"(?:volatile\s+)?(\w+)\s+([^;]+);", body):
        for n in names.split(","):
            m = re.match(r"\s*(\w+)(?:\[(\w+)\])?", n)
            count = m.group(2)
            if count is None:
                out.append((m.group(1), None))
            else:
                size = int(count) if count.isdigit() else int(re.search(rf"\b{count}\s*=\s*(\d+)", src).group(1))
                out.extend((m.group(1), i) for i in range(size))
    return out


def text_of(raw, pool, i):
    f = C.ITEM.unpack_from(raw, i * C.ITEM.size)
    return f, pool[f[C.F_TEXT_OFF]:f[C.F_TEXT_OFF] + f[C.F_TEXT_LEN]].decode("utf-8")


def item(i, text, **kw):
    return dict({"id": i, "kind": "text", "text": text, "anchor": "screen", "x": 10, "y": 20}, **kw)


class CanvasMergeTest(unittest.TestCase):
    def test_b_keeps_a_items_and_rebases_pool(self):
        raw_a, pool_a, n_a, _ = C.pack_all([item(0x10000, "进程 A 的按钮"), item(0x10001, "A 的第二条")], A)
        keep = C.others(raw_a, pool_a, n_a, B, alive=ALIVE)
        self.assertEqual(len(keep), 2)
        raw, pool, n, kept = C.pack_all([item(0x10002, "进程 B 的按钮")], B, keep)
        self.assertEqual((n, kept), (3, 2))
        texts = [text_of(raw, pool, i)[1] for i in range(n)]
        self.assertEqual(texts, ["进程 A 的按钮", "A 的第二条", "进程 B 的按钮"])
        owners = [text_of(raw, pool, i)[0][C.F_OWNER_PID] for i in range(n)]
        self.assertEqual(owners, [1111, 1111, 2222])

    def test_writer_replaces_only_its_own_items(self):
        raw, pool, n, _ = C.pack_all([item(0x10000, "A")], A)
        raw, pool, n, _ = C.pack_all([item(0x10001, "B 旧")], B, C.others(raw, pool, n, B, alive=ALIVE))
        raw, pool, n, _ = C.pack_all([item(0x10001, "B 新")], B, C.others(raw, pool, n, B, alive=ALIVE))
        self.assertEqual([text_of(raw, pool, i)[1] for i in range(n)], ["A", "B 新"])
        raw, pool, n, _ = C.pack_all([], B, C.others(raw, pool, n, B, alive=ALIVE))     # B clear()：只清自己的
        self.assertEqual([text_of(raw, pool, i)[1] for i in range(n)], ["A"])

    def test_dead_and_ownerless_items_are_dropped(self):
        raw, pool, n, _ = C.pack_all([item(1, "老客户端（没主人）")], (0, 0))
        raw, pool, n, _ = C.pack_all([item(0x10000, "死掉的进程")], DEAD, C.others(raw, pool, n, DEAD, alive=lambda p: True))
        self.assertEqual(n, 1)                                      # 没主人的那条被清了
        keep = C.others(raw, pool, n, A, alive=ALIVE)               # 3333 已经退出
        self.assertEqual(keep, [])

    def test_same_process_two_clients_are_separate_owners(self):
        s1, s2 = (1111, 1), (1111, 2)                               # 网关：同一进程里两个会话
        raw, pool, n, _ = C.pack_all([item(0x10000, "会话 1")], s1)
        keep = C.others(raw, pool, n, s2, alive=ALIVE)
        self.assertEqual(len(keep), 1)

    def test_pack_still_returns_three_values(self):
        raw, pool, n = C.pack([item(1, "x")])
        self.assertEqual(n, 1)


class InputMergeTest(unittest.TestCase):
    def test_same_key_merges_flags_and_keeps_first_id(self):
        recs = [(1111, 1, I.MOUSE_REPORT_LEFT, [(0x10000, 0x75, 0, 0)]),
                (2222, 1, I.MOUSE_CAPTURE_LEFT, [(0x10001, 0x75, 0, I.SWALLOW), (0x10002, 0x76, 1, 0)])]
        table, mouse = I.merge_clients(recs, ALIVE)
        self.assertEqual(table, [(0x10000, 0x75, 0, I.SWALLOW), (0x10002, 0x76, 1, 0)])
        self.assertEqual(mouse, I.MOUSE_REPORT_LEFT | I.MOUSE_CAPTURE_LEFT)

    def test_dead_clients_do_not_count(self):
        recs = [(3333, 1, I.MOUSE_CAPTURE_RIGHT, [(0x10000, 0x70, 0, I.SWALLOW)]), (0, 0, 0, [])]
        self.assertEqual(I.merge_clients(recs, ALIVE), ([], 0))


@unittest.skipUnless(os.path.exists(HEADER_H), "没有 native/ 的头文件")
class ProtocolTest(unittest.TestCase):
    """多客户端约定是 SDK 和运行时（DLL 的回收线程）共用的：两边的偏移、魔数、名字要一致。"""

    @classmethod
    def setUpClass(cls):
        with open(HEADER_H, encoding="utf-8") as fh:
            cls.src = fh.read()

    def const(self, name):
        m = re.search(rf"\b{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", self.src)
        self.assertIsNotNone(m, name)
        return int(m.group(1), 0)

    def test_canvas_owner_fields(self):
        f = flat_fields(self.src, "War3CanvasItem")
        self.assertEqual(len(f) * 4, C.ITEM.size)
        self.assertEqual((f.index(("textOff", None)), f.index(("textLen", None)), f.index(("pointsOff", None)),
                          f.index(("pointCount", None)), f.index(("reserved", 1)), f.index(("reserved", 2))),
                         (C.F_TEXT_OFF, C.F_TEXT_LEN, C.F_PTS_OFF, C.F_PTS_N, C.F_OWNER_PID, C.F_OWNER_SERIAL))

    def test_canvas_id_counter(self):
        f = flat_fields(self.src, "War3CanvasBlock")
        self.assertEqual(f.index(("reserved", 5)) * 4, C.O_NEXT_ID)
        self.assertIn("offsetof(War3CanvasBlock, reserved) + 5 * sizeof(DWORD) == 60", self.src)
        self.assertIn("Local\\War3CanvasMutex_<pid>", self.src)

    def test_input_clients_layout(self):
        self.assertEqual((self.const("kWar3InputClientsMagic"), self.const("kWar3InputClientsMax")),
                         (I.CLIENTS_MAGIC, I.CLIENTS_MAX))
        rec = flat_fields(self.src, "War3InputClient")
        self.assertEqual([n for n, _ in rec[:4]], ["pid", "serial", "mouseFlags", "hotkeyCount"])
        self.assertEqual(I.CL_REC.size, 16)
        self.assertEqual(sum(1 for n, _ in rec if n == "hotkeys"), I.HOTKEYS)       # 后面跟 War3InputHotkey[32]（每条 16 字节）
        self.assertEqual(I.CL_SIZE, 16 + I.HOTKEYS * I.HOTKEY.size)
        self.assertIn("sizeof(War3InputClient) == 16 + 16 * kWar3InputHotkeys", self.src)
        hdr = [n for n, _ in flat_fields(self.src, "War3InputClients")[:4]]
        self.assertEqual(hdr, ["magic", "version", "nextHotkeyId", "reserved"])      # SDK 在偏移 8 分配热键 id
        self.assertEqual(I.CL_HDR, 16)
        self.assertEqual(I.CL_BLOCK, 16 + I.CLIENTS_MAX * I.CL_SIZE)
        self.assertIn('L"Local\\\\War3InputClients_%lu"', self.src)
        self.assertIn("Local\\War3InputMutex_<pid>", self.src)


if __name__ == "__main__":
    unittest.main()

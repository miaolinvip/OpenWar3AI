# -*- coding: utf-8 -*-
"""openwar3 门面：订单表、四字码、接口目录（不需要游戏）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
from openwar3 import ORDER, ORDER_NAME, catalog, code_of, rawcode  # noqa: E402


class OrdersTest(unittest.TestCase):
    def test_known_orders_from_table(self):
        # 值来自 data/order-ids.txt，和实测过的几条对得上
        self.assertEqual(ORDER("blizzard"), 0x000D0079)
        self.assertEqual(ORDER("thunderbolt"), 0x000D007F)
        self.assertEqual(ORDER("move"), 0x000D0012)
        self.assertEqual(ORDER("attack"), 0x000D000F)
        self.assertEqual(ORDER("stop"), 0x000D0004)

    def test_aliases(self):
        self.assertEqual(ORDER("stormbolt"), ORDER("thunderbolt"))
        self.assertEqual(ORDER("HolyLight"), ORDER("holybolt"))

    def test_unknown_raises_with_hint(self):
        with self.assertRaises(KeyError):
            ORDER("fireball_of_doom")

    def test_rawcode_roundtrip(self):
        self.assertEqual(rawcode("hfoo"), 0x68666F6F)
        self.assertEqual(code_of(rawcode("AHbz")), "AHbz")
        self.assertEqual(ORDER_NAME(0x000D0012), "move")
        with self.assertRaises(ValueError):
            rawcode("hf")


class NoAddressTest(unittest.TestCase):
    """闭源运行时 + 开源 SDK 的边界：openwar3 包里（代码和说明文字）不出现 Game.dll 地址 / 结构偏移。
    订单号 0x000Dxxxx 和四字码阈值 0x20202020 是协议常量，放行。"""

    def test_no_engine_addresses_in_facade(self):
        import ast
        import re
        pkg = os.path.dirname(openwar3.__file__)
        bad = []
        for fn in sorted(os.listdir(pkg)):
            if not fn.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(pkg, fn), encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
                    v = node.value
                    if v >= 0x10000 and not (0x000D0000 <= v <= 0x000DFFFF) and v != 0x20202020:
                        bad.append(f"{fn}:{node.lineno} 数字 {v:#x}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for m in re.findall(r"\+0x[0-9A-Fa-f]{2,}", node.value):
                        bad.append(f"{fn}:{node.lineno} 文字 {m}")
        self.assertEqual(bad, [], "开源门面里出现了引擎地址/偏移")


class CatalogTest(unittest.TestCase):
    def test_every_entry_documented(self):
        es = catalog.entries()
        self.assertGreater(len(es), 40)
        for e in es:
            self.assertIn(e["category"], catalog.CATEGORIES, e["name"])
            self.assertIn(e["status"], catalog.STATUS, e["name"])
            self.assertTrue(e["doc"], f"{e['name']} 没有说明（接口目录要给大模型读，空说明等于没这个接口）")
            self.assertTrue(e["mechanism"], f"{e['name']} 没写底层机制")
            self.assertTrue(hasattr(openwar3.Game, e["name"]), e["name"])

    def test_commands_cover_the_basics(self):
        names = {e["name"] for e in catalog.entries() if e["category"] == "command"}
        for need in ("move", "attack_move", "attack", "stop", "gather", "build", "train", "cast", "learn",
                     "rally", "revive", "use_item", "pick_up"):
            self.assertIn(need, names)

    def test_generated_docs_are_current(self):
        """docs/API_ZH.md 必须和代码一致：改了接口忘了重新生成，这条会红。"""
        import w3paths
        with open(os.path.join(w3paths.ROOT, "docs", "API_ZH.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().rstrip("\n"), catalog.markdown().rstrip("\n"),
                             "接口改了：python -m openwar3 catalog --write")


if __name__ == "__main__":
    unittest.main()

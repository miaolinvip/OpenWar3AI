# -*- coding: utf-8 -*-
"""同名地图怎么区分（09-26，console/server/mapinfo.py fingerprint）：本机 4 份 Echo Isles 名字、缩略图都一样，
靠各部分的指纹 + 自带的 AI 脚本 + 固定玩家设置在「选地图」里标出哪里不一样。这里用假的 MPQ 测规则，不需要游戏。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import common  # noqa: E402,F401  先导它：它把 sdk/python 放上 sys.path，mapinfo 才 import 得到 w3paths
from server import mapinfo as M  # noqa: E402


class FakeMpq:
    def __init__(self, files: dict):
        self.files = files

    def read(self, name):
        if name == "boom":
            raise ValueError("坏块")
        return self.files.get(name)


BASE = {"war3map.j": b"script", "war3map.w3e": b"terrain", "war3map.w3i": b"info", "war3map.doo": b"doo", "war3mapUnits.doo": b"units"}


class FingerprintTest(unittest.TestCase):
    def test_plain_map(self):
        r = M.fingerprint(b"blob", FakeMpq(BASE), 0)
        self.assertEqual((r["aiScripts"], r["aiKind"], r["fixedSlots"]), ([], None, False))
        self.assertEqual(set(r["parts"]), set(M.FINGERPRINT))
        self.assertTrue(all(r["parts"].values()))

    def test_amai_injected(self):
        files = dict(BASE, **{"Scripts\\common.ai": b"// AMAI constants", "Scripts\\human.ai": b"x", "Scripts\\elf.ai": b"y"})
        r = M.fingerprint(b"blob2", FakeMpq(files), 0)
        self.assertEqual(r["aiScripts"], ["common.ai", "human.ai", "elf.ai"])
        self.assertEqual(r["aiKind"], "AMAI")
        self.assertEqual(r["parts"], M.fingerprint(b"blob", FakeMpq(BASE), 0)["parts"])   # 地图本身没动

    def test_other_ai_and_fixed_slots(self):
        r = M.fingerprint(b"b", FakeMpq(dict(BASE, **{"Scripts\\orc.ai": b"ai"})), M.FLAG_FIXED_SLOTS | 0x4)
        self.assertEqual((r["aiKind"], r["fixedSlots"]), ("custom", True))

    def test_missing_parts_are_none(self):
        r = M.fingerprint(b"b", FakeMpq({"war3map.w3e": b"t"}), 0)
        self.assertIsNone(r["parts"]["war3map.j"])
        self.assertIsNotNone(r["parts"]["war3map.w3e"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""tools/make_opening.py：高手录像 -> 开局谱草稿（后面的参考录像都走这条路，09-18 起要求方便扩展）。"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.path.dirname(HERE)
ROOT = os.path.dirname(BRAIN)
sys.path.insert(0, BRAIN)
from brain import opening as OP  # noqa: E402

REPLAY = os.path.join(ROOT, "war3-analytics", "replays", "1481530917_Yumiko_TH000_Echo Isles.nwg")


def tool():
    spec = importlib.util.spec_from_file_location("make_opening_for_test", os.path.join(BRAIN, "tools", "make_opening.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(os.path.exists(REPLAY), "没有 TH000 录像")
class Draft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "war3-analytics"))
        import replay_global as G
        cls.op, cls.skipped = tool().draft(G.analyse(REPLAY), "Yumiko", 10)

    def test_order_and_parse(self):
        steps = self.op["steps"]
        self.assertEqual([(s["kind"], s["code"]) for s in steps[:2]], [("building", "hbar"), ("building", "halt")])
        self.assertEqual(steps[2], {"kind": "unit", "code": "hfoo", "qty": 2, "note": "录像 1:37"})   # 1:37 两下 = 排了两个
        self.op.update(id="t", name="t")
        self.assertEqual(OP.parse(self.op).race, "human")

    def test_expand_goes_before_the_expansion_tower(self):
        kinds = [(s["kind"], s.get("where", "")) for s in self.op["steps"]]
        self.assertLess(kinds.index(("expand", "")), kinds.index(("building", "expansion")))

    def test_vision_farms_and_base_farms_are_left_out(self):
        self.assertFalse([s for s in self.op["steps"] if s["code"] == "hhou"])
        self.assertTrue(any("hhou 离家 2083" in s for s in self.skipped))


if __name__ == "__main__":
    unittest.main()

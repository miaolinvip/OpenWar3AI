# -*- coding: utf-8 -*-
"""兽族开局冒烟：认出种族、造风暴祭坛/兵营/地洞（不造人族建筑）、苦工去金矿、首英雄剑圣。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


class TestOrc(unittest.TestCase):
    def test_opening(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=-4800 + i * 64, y=3600, hp=50.0) for i in range(10)]
            hall = U(0x100, "ogre", -5184, 2944)
            mine = U(0x200, "ngol", -5632, 3584, owner=15)
            peons = [U(0x300 + i, "opeo", -5300, 3200) for i in range(5)]
            enemy = [U(0x800, "hcas", 4672, 2944, owner=1)]
            for t in range(2, 40, 2):
                snap = SimpleNamespace(units=[hall, mine] + peons + enemy, me=0, gold=500, lumber=150, food_used=5, food_cap=10)
                b.step(snap, float(t))
            self.assertEqual(b.race, "orc")
            self.assertEqual({n: s for n, s in b.jobs.stats().items() if s["errors"]}, {})
            built = {o for _, o, _, _ in cmd.builds}
            self.assertTrue(built & {A.rawcode("oalt"), A.rawcode("obar"), A.rawcode("otrb")}, cmd.builds)
            self.assertFalse(built & {A.rawcode("halt"), A.rawcode("hbar"), A.rawcode("hhou")})
            self.assertTrue(any(t == 0x200 for u, t, o in cmd.gathers), cmd.gathers)
            self.assertEqual(b.profile["heroes"][0], "Obla")


if __name__ == "__main__":
    unittest.main()

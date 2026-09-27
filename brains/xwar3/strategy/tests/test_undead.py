# -*- coding: utf-8 -*-
"""亡灵开局冒烟：认出种族、不判「没有大厅」、造亡灵的祭坛/地穴、侍僧去闹鬼金矿、食尸鬼去砍树且不算进军队。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


class TestUndead(unittest.TestCase):
    def test_opening(self):
        self.assertTrue(G.is_building("unpl"))
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=-4800 + i * 64, y=3600, hp=50.0) for i in range(10)]
            hall = U(0x100, "unpl", -5184, 2944)
            mine = U(0x200, "ugol", -5632, 3584)
            acos = [U(0x300 + i, "uaco", -5300, 3200) for i in range(3)]
            ghoul = U(0x400, "ugho", -5000, 2900)
            enemy = [U(0x800, "hcas", 4672, 2944, owner=1)]
            for t in range(2, 40, 2):
                snap = SimpleNamespace(units=[hall, mine, ghoul] + acos + enemy, me=0, gold=500, lumber=150, food_used=5, food_cap=10)
                b.step(snap, float(t))
            self.assertEqual(b.race, "undead")
            self.assertEqual({n: s for n, s in b.jobs.stats().items() if s["errors"]}, {})
            built = {o for _, o, _, _ in cmd.builds}
            self.assertIn(A.rawcode("uaod"), built)
            self.assertNotIn(A.rawcode("halt"), built)
            self.assertTrue(any(t == 0x200 for u, t, o in cmd.gathers if u in (0x300, 0x301, 0x302)), cmd.gathers)   # 侍僧 -> 闹鬼金矿
            self.assertTrue(any(u == 0x400 and t >= 0x9000 for u, t, o in cmd.gathers), cmd.gathers)                  # 食尸鬼 -> 树
            self.assertFalse(any(u >= 0x9000 or (u in (0x300, 0x301, 0x302) and t >= 0x9000) for u, t, o in cmd.gathers))
            self.assertIn(0x400, b.lumber_crew)
            self.assertEqual(b.profile["peons_lumber"], 4)


if __name__ == "__main__":
    unittest.main()

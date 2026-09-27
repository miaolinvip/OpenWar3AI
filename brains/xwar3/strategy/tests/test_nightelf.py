# -*- coding: utf-8 -*-
"""暗夜开局冒烟：认出种族、小精灵只派缠绕金矿（不派旁边的中立矿）、造长者祭坛不造人族建筑。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


class TestNightElf(unittest.TestCase):
    def test_opening(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=-4800 + i * 64, y=3600, hp=50.0) for i in range(10)]
            hall = U(0x100, "etol", -5184, 2944)
            neutral = U(0x201, "ngol", -5632, 3584, owner=15)       # 实机：缠绕金矿和中立金矿在同一个位置各有一个单位
            egol = U(0x200, "egol", -5632, 3584)
            wisps = [U(0x300 + i, "ewsp", -5300, 3200) for i in range(5)]
            enemy = [U(0x800, "hcas", 4672, 2944, owner=1)]
            for t in range(2, 40, 2):
                snap = SimpleNamespace(units=[hall, neutral, egol] + wisps + enemy, me=0, gold=500, lumber=150, food_used=5, food_cap=10)
                b.step(snap, float(t))
            self.assertEqual(b.race, "nightelf")
            self.assertEqual({n: s for n, s in b.jobs.stats().items() if s["errors"]}, {})
            built = {o for _, o, _, _ in cmd.builds}
            self.assertTrue(built & {A.rawcode("eate"), A.rawcode("eaom"), A.rawcode("emow")}, cmd.builds)
            self.assertNotIn(A.rawcode("halt"), built)
            gold_targets = {t for u, t, o in cmd.gathers if t < 0x9000}
            self.assertEqual(gold_targets, {0x200})                   # 只派缠绕金矿

    def test_entangle_expansion_mine(self):
        from brain.world import build_world
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.set_race("nightelf")
            main = U(0x100, "etol", -5184, 2944)
            main_mines = [U(0x201, "ngol", -5632, 3584, owner=15), U(0x200, "egol", -5632, 3584)]
            new_tree = U(0x101, "etol", -4352, -2624)                  # 分矿边上种好的生命之树
            nat = U(0x202, "ngol", -4864, -3200, owner=15)
            w = build_world([main, new_tree, nat] + main_mines, 0, 600.0, 300, 300, 20, 40)
            b.job_entangle(w)
            self.assertEqual(b.counts.get("缠绕金矿·下了"), 1)       # 只缠分矿；主矿已经有 egol
            b.job_entangle(w)
            self.assertEqual(b.counts.get("缠绕金矿·下了"), 1)       # 30 秒内不重下


if __name__ == "__main__":
    unittest.main()

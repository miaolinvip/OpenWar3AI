# -*- coding: utf-8 -*-
"""运行时：开矿被野怪挡住转成清营地目标、训练在路上按预期数量兑现、建筑没了记下附近有谁、局末结束原因。"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import gamedata as G  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402
import run_brain as RB  # noqa: E402


def W(units, t=300.0, gold=500, lumber=300):
    return build_world(units, 0, t, gold, lumber, 20, 40)


class TestRuntime(unittest.TestCase):
    def test_expand_blocked_by_creeps(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            hall = U(0x100, "htow", -5184, 2944)
            mines = [U(0x600, "ngol", -5632, 3584, owner=15), U(0x601, "ngol", -4864, -3200, owner=15)]
            peas = [U(0x300 + i, "hpea", -5000, 2800) for i in range(5)]
            wolves = [U(0x700 + i, "nwlg", -5900, -3600, owner=12) for i in range(2)]   # 离分矿矿点 1110
            why = b.check(W([hall] + mines + peas + wolves), Want("expand", "htow", 2, 66, "mine"))
            self.assertEqual(why, "分矿旁边有野怪（强度 8），先清")
            self.assertEqual(b.attack.clear_target["ids"], {0x700, 0x701})
            self.assertIsNone(b.check(W([hall] + mines + peas), Want("expand", "htow", 2, 66, "mine")))

    def test_pending_units_expect_count(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            bar = U(0x500, "hbar", -5184, 2112)
            hall = U(0x100, "htow", -5184, 2944)
            w0 = W([hall, bar])
            b._execute(w0, Want("unit", "hkni", 2, 55))
            b._execute(w0, Want("unit", "hkni", 2, 55))
            self.assertEqual([p["expect"] for p in b.pending], [1, 2])
            bt = float(G.stat("hkni", "bldtm", 20))                                     # 数据表里骑士 40 秒（别凭记忆写 45）
            self.assertEqual([round(p["ttl"]) for p in b.pending], [round(bt * 1.3 + 10), round(2 * bt * 1.3 + 10)])
            b._prune_pending(W([hall, bar, U(0x900, "hkni", -5100, 2100)], t=350.0))   # 第一个出来了
            self.assertEqual([p["expect"] for p in b.pending], [2])                    # 第二个还在队列里，不当兑现

    def test_building_loss_note(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            hall = U(0x100, "htow", -5184, 2944)
            site = U(0x101, "htow", -4352, -2624)
            site.hp = 300.0
            wolves = [U(0x700 + i, "nwlg", -4400, -2700, owner=12) for i in range(2)]
            filler = [U(0x300 + i, "hpea", -5000, 2800) for i in range(8)]
            prev = W([hall, site] + wolves + filler)
            b._building_losses(prev, W([hall] + wolves + filler), 400.0)
            self.assertEqual(b.counts.get("建筑没了·htow·工地"), 1)
            self.assertIn("900 内 nwlg×2", b.notes[-1][1])

    def test_wait_start_gives_up_when_game_replaced(self):
        class NoSnap:
            def read(self):
                return None
        t0 = time.monotonic()
        self.assertEqual(RB.wait_game_start(NoSnap(), None, deadline_s=10.0, alive=lambda: False), (None, None))
        self.assertLess(time.monotonic() - t0, 3.0)                  # 不等满 10 秒（01:12:50 那次等满了 90 秒）

    def test_classify_end(self):
        good = dict(my_buildings=12, my_halls=2, enemy_buildings=9, own=30.0, foe=40.0)
        self.assertEqual(RB.classify_end(True, good), "ended-by-operator")
        self.assertEqual(RB.classify_end(False, dict(good, enemy_buildings=0)), "victory")
        self.assertEqual(RB.classify_end(False, dict(good, my_buildings=0)), "defeated")
        self.assertEqual(RB.classify_end(False, dict(good, my_halls=0, own=20.0, foe=70.0)), "defeated")   # 00:03 亡灵那局的形状
        self.assertEqual(RB.classify_end(False, good), "crash")
        self.assertEqual(RB.classify_end(False, None), "crash")


if __name__ == "__main__":
    unittest.main()

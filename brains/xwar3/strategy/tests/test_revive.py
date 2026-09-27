# -*- coding: utf-8 -*-
"""复活英雄归规划器：死亡英雄表里有人 -> 高优先级「复活」需求，钱不够停下攒；够了对祭坛下复活令；死了的英雄不再被当成「要再训练一个」。
起因 09-18 010409 兽族局：job_heroes 要「下令那一刻金 >= 200」，规划器先把钱花光，两个英雄死了 5 分钟没复活（「没有能出 Obla 的建筑」×206）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import runtime as RT  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

DEAD = 0xDEAD0


def base_units():
    hall = U(0x100, "htow", -5184, 2944)
    altar = U(0x200, "halt", -4800, 2600)
    bar = U(0x500, "hbar", -5184, 2112)
    peas = [U(0x300 + i, "hpea", -5000, 2800) for i in range(12)]
    return [hall, altar, bar] + peas


def brain_with_dead(d, level=3):
    b = Brain(FakeCmd(), None, d)
    b.hero_seen[DEAD] = ("Hamg", level)
    b.act.dead_heroes = lambda any_unit: [DEAD]
    b.revived = []

    def revive(altar, ptr):
        b.revived.append((altar, ptr))
        return True
    b.act.revive = revive
    return b


class TestRevive(unittest.TestCase):
    def test_cost_formula(self):
        self.assertEqual(RT.revive_cost("Hamg", 1), (170, 0))     # 425 × 0.4 —— 和 010409 局实测差价 170 对得上
        self.assertEqual(RT.revive_cost("Hamg", 3), (255, 0))     # 425 × 0.6
        self.assertEqual(RT.revive_cost("Hamg", 20)[0], 700)      # 封顶
        self.assertEqual(RT.revive_cost("hero", 1), (170, 0))     # 认不出是谁按 425 估

    def test_short_of_gold_stops_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            b.job_build(build_world(base_units(), 0, 400.0, 200, 500, 20, 40))
            self.assertEqual(b.last_plan["stoppedAt"], "Hamg")
            self.assertEqual(b.revived, [])
            row = next(x for x in b.last_plan["wants"] if x["kind"] == "revive")
            self.assertEqual(row["status"], "攒钱")
            self.assertEqual(b.cmd.trains, [])                       # 没有把钱花在优先级更低的兵上

    def test_enough_gold_revives_and_counts_pending(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            w = build_world(base_units(), 0, 400.0, 600, 500, 20, 40)
            b.job_build(w)
            self.assertEqual(b.revived, [(0x200, DEAD)])
            self.assertEqual([p["kind"] for p in b.pending if p["kind"] == "revive"], ["revive"])
            b.act.dead_heroes = lambda any_unit: []                  # 接下后引擎清槽
            b.job_build(build_world(base_units(), 0, 403.5, 600, 500, 20, 40))
            self.assertEqual(len(b.revived), 1)                      # 不重下
            self.assertEqual(b.have(w, Want("hero", "Hamg", 1, 95)), 1)   # 正在复活的算有，不去再训练一个

    def test_dead_hero_counts_as_have(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            w = build_world(base_units(), 0, 400.0, 100, 500, 20, 40)
            b._refresh_dead(w)
            self.assertEqual(b.have(w, Want("hero", "Hamg", 1, 95)), 1)
            self.assertEqual(b.have(w, Want("hero", "Hmkg", 1, 80)), 0)

    def test_no_altar(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain_with_dead(d)
            units = [u for u in base_units() if u.type != "halt"]
            w = build_world(units, 0, 400.0, 600, 500, 20, 40)
            b._refresh_dead(w)
            self.assertEqual(b.check(w, Want("revive", "Hamg", 1, RT.REVIVE_PRIO)), "没有祭坛复活不了")


if __name__ == "__main__":
    unittest.main()

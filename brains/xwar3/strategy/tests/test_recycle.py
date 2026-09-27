# -*- coding: utf-8 -*-
"""低级兵喂野怪腾人口（09-18 前期的低级兵到中后期要尽快消耗掉、换成高级单位；不送给敌军，交给野怪打死再换高级兵）。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(food=92, gold=800, hall="hcas", camp_lvls=(5, 5, 5, 5, 5), enemy_near_camp=False, n_foot=8):
    units = [U(1, hall, 0, 0), U(900, "unpl", 12000, 0, owner=1)]
    units += [U(300 + i, "hpea", 100, 0) for i in range(5)]
    units += [U(10 + i, "hfoo", 500, 60 * i) for i in range(n_foot)]
    units += [U(40 + i, "hkni", 600, 60 * i) for i in range(6)]
    units += [U(2, "Hamg", 500, -200)]
    types = {5: "nogm", 4: "nwlg", 3: "ngnb", 2: "nftr", 1: "ngno"}   # 按等级挑野怪类型（nogm 5 级、nwlg 4 级……）
    units += [U(700 + i, types.get(l, "nwlg"), 3000 + 80 * i, 0, owner=12) for i, l in enumerate(camp_lvls)]
    if enemy_near_camp:
        units.append(U(950, "unpl", 3500, 1000, owner=1))
    return build_world(units, 0, 900.0, gold, 300, food, 100, levels={2: 4})


class Recycle(unittest.TestCase):
    def test_sends_footmen_to_strong_camp(self):
        w = world()
        s = T.AttackSequence(dict(PROFILE))
        out = s.step(w, w.my_halls[0])
        sac = [i for i in out if "喂野怪" in i.why]
        self.assertEqual(len(sac), 1, [i.why for i in out])
        self.assertEqual({u.type for u in sac[0].units}, {"hfoo"})
        self.assertEqual(len(sac[0].units), 7)                           # 92 -> 78：每个步兵 2 人口，7 个；营地 25 >= 7×2×1.5=21
        self.assertAlmostEqual(sac[0].x, 3000 + 160, delta=100)
        self.assertEqual(s.counts.get("喂野怪·派出"), 1)
        others = [i for i in out if "喂野怪" not in i.why]
        self.assertFalse(any(u.addr in s.sacrifice for i in others for u in i.units))   # 别的意图里没有它们

    def test_keeps_sending_same_units(self):
        w = world()
        s = T.AttackSequence(dict(PROFILE))
        s.step(w, w.my_halls[0])
        first = set(s.sacrifice)
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.sacrifice, first)
        self.assertEqual(s.counts.get("喂野怪·派出"), 1)
        self.assertEqual(len([i for i in out if "喂野怪" in i.why]), 1)

    def test_no_recycle_below_food_or_tier_or_gold(self):
        for kw in (dict(food=80), dict(hall="hkee"), dict(gold=100)):
            w = world(**kw)
            s = T.AttackSequence(dict(PROFILE))
            out = s.step(w, w.my_halls[0])
            self.assertFalse([i for i in out if "喂野怪" in i.why], kw)

    def test_camp_too_weak_or_near_enemy(self):
        for kw in (dict(camp_lvls=(4,)), dict(enemy_near_camp=True)):        # 4 < 3 个步兵 × 2 级 × 1.5 = 9
            w = world(**kw)
            s = T.AttackSequence(dict(PROFILE))
            s.step(w, w.my_halls[0])
            self.assertEqual(s.sacrifice, set(), kw)
            self.assertEqual(s.counts.get("喂野怪·没有打得死它们的营地"), 1, kw)

    def test_weaker_camp_takes_smaller_batch(self):
        w = world(camp_lvls=(5, 5, 2))                                    # 12：打得死 4 个（4×2×1.5=12）
        s = T.AttackSequence(dict(PROFILE))
        s.step(w, w.my_halls[0])
        self.assertEqual(len(s.sacrifice), 4)

    def test_not_while_attacking(self):
        w = world()
        s = T.AttackSequence(dict(PROFILE))
        s.state = "attack"
        s._set_target(w.enemy_halls[0], "主基地")
        s.step(w, w.my_halls[0])
        self.assertEqual(s.sacrifice, set())

    def test_dead_units_leave_list_and_camp_gone_releases(self):
        w = world()
        s = T.AttackSequence(dict(PROFILE))
        s.step(w, w.my_halls[0])
        w2 = world(camp_lvls=())                                         # 营地被打光了、也没别的营地
        s.step(w2, w2.my_halls[0])
        self.assertEqual(s.sacrifice, set())
        self.assertEqual(s.counts.get("喂野怪·营地没了、放回来"), 1)


class Publish(unittest.TestCase):
    def test_runtime_writes_sacrifice_json(self):
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.attack.sacrifice = {11, 12}
            b._publish_sacrifice()
            data = json.load(open(os.path.join(d, "sacrifice.json"), encoding="utf-8"))
            self.assertEqual(data["units"], [11, 12])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""英雄被毫秒层拉着时营地不丢、其余的兵不回家；近处现在就打得过的营地先打（09-18 5-031 / 5-032 局：
打下方最近营地时只有两个小兵辅助英雄、其余全回家；明明可以先打下方，却绕去打上方；小兵全回基地集结，
只剩两个水元素在前面扛）。

5-031 局 14:28:30：毫秒层打野整队撤退把英雄也拉走 -> hurt.json -> 大脑 world.my_heroes 暂时为空 -> _creep 清掉营地 ->
其余的兵被「集结」派回家；英雄回来重选营地选了上面那个（第 17 拍：下面强度 5 走 6 秒不用等兵，上面强度 9 走 14 + 等兵 18）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(units):
    base = [U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    base += [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)]
    return build_world(base + units, 0, 600.0, 300, 300, 20, 40, levels={2: 2})


class HeldHero(unittest.TestCase):
    def test_hero_pulled_keeps_camp_and_army_keeps_creeping(self):
        feet = [U(10 + i, "hfoo", 2600, 80 * i) for i in range(3)]
        camp = [U(700 + i, "nwlt", 3000, 60 * i, owner=12) for i in range(2)]
        w = world(feet + camp)                                   # 英雄 2 被拉着：不在 world 里
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since, s.creep_engaged = "creep", {700, 701}, w.t, True
        s.camp = dict(x=3000.0, y=30.0, strength=4.0, units=[])
        s.held, s.held_heroes = {2}, 1
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.camp_ids, {700, 701})
        self.assertEqual(s.state, "creep")
        self.assertFalse([i for i in out if "回家" in i.why or i.why == "集结"], [i.why for i in out])
        self.assertTrue([i for i in out if i.kind == "attack"], [i.why for i in out])

    def test_everyone_pulled_keeps_state(self):
        w = world([U(700, "nwlt", 3000, 0, owner=12)])
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since = "creep", {700}, w.t
        s.camp = dict(x=3000.0, y=0.0, strength=2.0, units=[])
        s.held, s.held_heroes = {2, 10, 11}, 1
        self.assertEqual(s.step(w, w.my_halls[0]), [])
        self.assertEqual((s.state, s.camp_ids), ("creep", {700}))
        self.assertEqual(s.counts.get("全员被毫秒层拉着·等"), 1)

    def test_really_no_hero_drops_camp(self):
        w = world([U(10, "hfoo", 2600, 0), U(700, "nwlt", 3000, 0, owner=12)])
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since = "creep", {700}, w.t
        s.camp = dict(x=3000.0, y=0.0, strength=2.0, units=[])
        s.step(w, w.my_halls[0])
        self.assertEqual(s.camp_ids, set())


class NearFirst(unittest.TestCase):
    def test_near_beatable_camp_beats_bigger_camp_that_needs_waiting(self):
        h = U(2, "Hamg", 0, 0)
        c_near, c_far = U(700, "nwlt", 1000, 0, owner=12), U(710, "nwlg", 4000, 0, owner=12)
        w = world([h, U(10, "hfoo", 0, 80), c_near, c_far])
        near = dict(x=1000.0, y=0.0, strength=5.0, units=[c_near])
        far = dict(x=4000.0, y=0.0, strength=9.0, units=[c_far])
        plans = [dict(camp=far, r=1.9, travel=14.0, wait=18.0, fight=25.0, total=50.0, score=0.18),
                 dict(camp=near, r=3.0, travel=6.0, wait=0.0, fight=20.0, total=36.0, score=0.14)]
        orig = T.creep_plans
        T.creep_plans = lambda *a, **k: [dict(p) for p in plans]
        try:
            s = T.AttackSequence(dict(PROFILE))
            s.step(w, w.my_halls[0])
        finally:
            T.creep_plans = orig
        self.assertIs(s.camp, near)
        self.assertEqual(s.counts.get("打野·先打近处能打的"), 1)


if __name__ == "__main__":
    unittest.main()

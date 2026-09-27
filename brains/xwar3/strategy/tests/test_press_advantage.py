# -*- coding: utf-8 -*-
"""占优时多骚扰（harass.HARASS_AHEAD_NEED / HARASS_AHEAD_CD）+ 出击顺路清野（tactics.AttackSequence._route_camp）。

09-22 晚 占上风时能骚扰就骚扰，出击路上顺手清掉途中打得过的野怪，尽量扩大优势。
1-403（赢，204 拍里 131 拍占优）骚扰不派的头号理由「人数不够」×634（整支部队健康作战单位要 > 8）；最近 13 局每局出发 0~9 次。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import harass as H  # noqa: E402
from brain import tactics as T  # noqa: E402
from test_fight_support import attacking, world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

G1 = next(g for g in H.HUMAN_GROUPS if g["id"] == 1)


class HarassWhenAhead(unittest.TestCase):
    def test_six_healthy_fighters_are_enough_when_ahead(self):
        args = dict(cfg=G1, have=3, foe_race="orc", t=600.0, last=-1e9, busy=False, aggr=3.0, roll=0.0, army_n=6)
        self.assertEqual(H.want(ahead=False, **args), "人数不够")
        self.assertEqual(H.want(ahead=True, **args), "go")

    def test_cooldown_is_shorter_when_ahead(self):
        args = dict(cfg=G1, have=3, foe_race="orc", t=600.0, last=520.0, busy=False, aggr=3.0, roll=0.0, army_n=10)
        self.assertEqual(H.want(ahead=False, **args), "冷却中")
        self.assertEqual(H.want(ahead=True, **args), "go", "80 秒 > 180 × 0.35")


class RouteCreeps(unittest.TestCase):
    def scene(self, creep="nwlg", n=3, off=300):
        army = [U(10 + i, "hkni", 2000, 80 * i) for i in range(8)]
        camp = [U(700 + i, creep, 5000, off + 60 * i, owner=12) for i in range(n)]
        return world(army + camp, foe_army=False)

    def test_easy_camp_on_the_way_is_cleared_first(self):
        w = self.scene()
        s = attacking(w)
        out = s.step(w, w.my_halls[0])
        route = [i for i in out if "顺路清野" in i.why]
        self.assertTrue(route, [(i.kind, i.why) for i in out])
        self.assertEqual(route[0].kind, "attack")
        self.assertIn(route[0].target.addr, {700, 701, 702})

    def test_camp_off_the_route_or_too_strong_is_left(self):
        w = self.scene(off=2500)                                          # 离行军线 2500：不顺路
        self.assertFalse([i for i in attacking(w).step(w, w.my_halls[0]) if "顺路清野" in i.why])
        w = self.scene(creep="nrdr", n=6)                                 # 红龙：打不动
        self.assertFalse([i for i in attacking(w).step(w, w.my_halls[0]) if "顺路清野" in i.why])


if __name__ == "__main__":
    unittest.main()

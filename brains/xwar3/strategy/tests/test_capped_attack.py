# -*- coding: utf-8 -*-
"""兵满钱多不在家干站（tables/human 的迫击炮、tactics.capped_rich / _assess）。

09-23 后期部队卡住不动、资源持续累积（1-416）；1-418 全程在家驻守，不出去进攻。
1-416 14 分钟起人口 80/84、兵 16、金子 1828 -> 6356；1-418 23 分钟起 79/96、兵 16~17、金子 713 -> 4289，出击每拍「目标不好」：
对面 AMAI 兽族一矿龟缩（16 座建筑全在 10 座箭塔 / 地洞射程里）、我方没有攻城单位，offense.assess 里塔按 ×4 算，
要 84.7 对我方 64（守军 47.8 × 1.25 + 塔 24）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import offense  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

FOE_MAIN = (4672, 2944)


def world(gold=4289, used=79, cap=96, armed=10, t=2100.0):
    units = [U(1, "hcas", -5184, 2944), U(2, "hbar", -4700, 2944), U(3, "halt", -5500, 2944), U(4, "hgra", -5184, 2500),
             U(5, "hars", -5184, 3300), U(6, "harm", -4800, 3300), U(7, "hbla", -4800, 2600)]
    units += [U(100 + i, "hkni", -4400, 2700 + 60 * i) for i in range(6)]
    units += [U(0x800, "ofrt", *FOE_MAIN, owner=1)]
    units += [U(0x810 + i, "owtw" if i % 2 else "otrb", FOE_MAIN[0] - 300 + 60 * i, FOE_MAIN[1] - 300, owner=1) for i in range(armed)]
    return build_world(units, 0, t, gold, 3000, used, cap)


def qty(w, code):
    return max([x.qty for x in human.wants(w, human.PROFILE) if x.code == code and x.kind == "unit"] or [0])


def prio(w, kind, code):
    return max([x.prio for x in human.wants(w, human.PROFILE) if x.code == code and x.kind == kind] or [0])


class Siege(unittest.TestCase):
    def test_1418_turtle_behind_towers_asks_for_mortars(self):
        w = world()
        self.assertEqual(qty(w, "hmtm"), human.SIEGE_MAX, "10 座塔 / 地洞：迫击炮要到上限")
        self.assertGreaterEqual(prio(w, "unit", "hmtm"), 56, "排到骑士（55）前面")
        self.assertGreaterEqual(prio(w, "building", "harm"), 58)

    def test_no_towers_no_extra_siege(self):
        w = world(armed=1)
        self.assertLessEqual(prio(w, "unit", "hmtm"), 10, "三本表原来那条（优先级 10）")


class CappedRich(unittest.TestCase):
    def test_capped_rich(self):
        self.assertTrue(T.capped_rich(world()), "79/96、软上限 80、存款 4289")
        self.assertFalse(T.capped_rich(world(gold=500)))
        self.assertFalse(T.capped_rich(world(used=60)))

    def test_assess_uses_the_lower_margin(self):
        w = world()
        s = T.AttackSequence(dict(human.PROFILE))
        s.target = w.enemy_halls[0]
        s.target_kind = "主基地"
        army = w.my_army + w.my_heroes
        home = w.my_halls[0]
        ev = s._assess(w, army, home)
        self.assertEqual(ev.lhs, offense.assess(w, army, home, s.target, T.CAPPED_MARGIN, 0.0).lhs)
        self.assertEqual(s.counts.get("出击·兵满钱多·门槛降到 1.0"), 1)
        poor = world(gold=500)
        s2 = T.AttackSequence(dict(human.PROFILE))
        s2.target, s2.target_kind = poor.enemy_halls[0], "主基地"
        ev2 = s2._assess(poor, poor.my_army, poor.my_halls[0])
        self.assertEqual(ev2.lhs, offense.assess(poor, poor.my_army, poor.my_halls[0], s2.target, T.ATTACK_MARGIN, 0.0).lhs)


if __name__ == "__main__":
    unittest.main()

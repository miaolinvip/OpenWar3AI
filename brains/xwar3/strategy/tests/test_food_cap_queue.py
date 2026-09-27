# -*- coding: utf-8 -*-
"""人口不过 80：已下单没出来的兵也算、英雄/复活也算、英雄被挡也腾人口、前期步兵够用就行。

09-22 人口不超过 80，到 79 之前就要开始优化人口；前期小兵数量够用即可，
把经济更多留给后面的高级单位。f58f9a3 之后 5 局 4 局到过 81~85：生产队列里的兵开始训练才占人口（309 一拍 80->85）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_food_soft_cap import cull_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def world(used, cap, gold=900, tier_hall="hkee", extra=()):
    units = [U(1, tier_hall, 0, 0), U(2, "hbar", 400, 0), U(3, "halt", -400, 0)] + list(extra)
    return build_world(units, 0, 900.0, gold, 500, used, cap)


class QueuedFood(unittest.TestCase):
    def test_units_in_the_queue_count(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w = world(72, 90)
            self.assertEqual(b.soft_food_free(w), 8)
            b.pending += [dict(kind="unit", code="hkni", t=899.0, addr=2, expect=1),
                          dict(kind="unit", code="hkni", t=899.0, addr=2, expect=2),
                          dict(kind="building", code="hhou", t=899.0)]
            self.assertEqual(b.queued_food(), 2 * G.food("hkni"))
            self.assertEqual(b.soft_food_free(w), 0)                       # 72 + 两个骑士 8 = 80：不能再下

    def test_revive_is_held_at_the_soft_cap(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w = world(78, 90)                                              # 真实上限还空 12，软上限只空 2
            b.last_revive = -1e9
            self.assertEqual(b.check(w, Want("revive", "Hamg", 1, 94)), "人口软上限")
            self.assertIsNone(b.check(world(70, 90), Want("revive", "Hamg", 1, 94)))


class CullForHeroes(unittest.TestCase):
    # 09-22 晚起强制攻击默认停用（tactics.CULL_ENABLED）；机制还在，这里打开测
    def setUp(self):
        self._cull = T.CULL_ENABLED
        T.CULL_ENABLED = True

    def tearDown(self):
        T.CULL_ENABLED = self._cull

    def test_blocked_hero_triggers_a_cull_even_when_gold_is_short(self):
        w = cull_world(food=78, gold=100)                                   # 100 金：买不起骑士，但英雄/复活不看钱
        s = T.AttackSequence(dict(PROFILE))
        s.allow_attack = False
        s.food_blocked = ["Hpal"]
        out = s.step(w, w.my_halls[0])
        self.assertTrue([i for i in out if i.kind == "force_attack"], [(i.kind, i.why) for i in out])
        s2 = T.AttackSequence(dict(PROFILE))
        s2.allow_attack = False
        s2.food_blocked = ["hkni"]
        self.assertFalse([i for i in s2.step(w, w.my_halls[0]) if i.kind == "force_attack"])
        self.assertEqual(s2.counts.get("强制A低级兵·钱不够换"), 1)

    def test_cull_starts_at_76(self):
        self.assertEqual(T.CULL_AT, 76)
        w = cull_world(food=76)
        s = T.AttackSequence(dict(PROFILE))
        s.allow_attack = False
        s.food_blocked = ["hkni"]
        self.assertTrue([i for i in s.step(w, w.my_halls[0]) if i.kind == "force_attack"])


class FewFootmen(unittest.TestCase):
    """09-22 晚 前期低级单位不造太多 —— 二本低级兵排在升三本后面；三本不再造低级兵。
    一本二本试过 4 步兵 + 2~3 火枪，1-405 七分钟被电脑 8 步兵 4 火枪打穿，回到 5 步兵 + 3~4 火枪（家被打再叫民兵）。"""

    def test_tier_one_and_two_want_five(self):
        # 09-23 起表外还有「补兵填人口」（AMAI DynamicBuildUnit，test_fill_army）—— 它排在表里所有东西后面（优先级 40）；
        # 表本身 5 -> 8 步兵（学 AMAI 开局多出兵的打法，AMAI NormalHuman 8）；三本照旧不补低级兵（下面那条）
        for hall in ("htow", "hkee"):
            w = world(20, 40, tier_hall=hall)
            foot = [x for x in human.wants(w, dict(PROFILE)) if x.kind == "unit" and x.code == "hfoo" and "补兵填人口" not in x.note]
            self.assertEqual([x.qty for x in foot], [8], hall)
            rif = [x.qty for x in human.wants(w, dict(PROFILE)) if x.kind == "unit" and x.code == "hrif"]
            self.assertLessEqual(max(rif), 4, hall)

    def test_tier_two_low_units_after_castle(self):
        wants = human.wants(world(20, 40, tier_hall="hkee"), dict(PROFILE))
        castle = next(x.prio for x in wants if x.kind == "upgrade_hall" and x.code == "hcas")
        low = [x.prio for x in wants if x.kind == "unit" and x.code == "hrif"]
        self.assertTrue(low and all(p < castle for p in low), (low, castle))

    def test_tier_three_builds_no_new_low_units(self):
        wants = human.wants(world(20, 40, tier_hall="hcas"), dict(PROFILE))
        self.assertFalse([x for x in wants if x.kind == "unit" and x.code in ("hfoo", "hrif")])


if __name__ == "__main__":
    unittest.main()

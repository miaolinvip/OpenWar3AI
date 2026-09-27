# -*- coding: utf-8 -*-
"""第二支打野小队（brain/creepsplit.py）。

★★ 09-23 单位多、有两个英雄时分两路练级：各带一支小分队打野；
   评估打得过就给英雄配上合适的单位去打。

判「打得过」和主队同一条线（creep_plans 的 r >= CREEP_POWER_MIN、不许靠「等兵」），
而且拆完之后主队对它自己那个营地也得还打得动。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import creepsplit as S  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain import world as W  # noqa: E402
from test_tactics import U  # noqa: E402

HOME = U(1, "htow", 0, -6000, 0)
CAMP_A = (0, 3000)          # 主队那个营地
CAMP_B = (0, -3000)         # 第二队那个营地


def creeps(base, n, code="nfor", at=100):
    return [U(at + i, code, 12, base[0] + 80 * i, base[1]) for i in range(n)]


# 这几个数是算出来的（一次性分析脚本 split_probe2.py，判据 CREEP_POWER_MIN 1.808）：
#   小队 = 4 个步兵 + 带队英雄 -> 对 2 只野怪战力比 2.27（打得过）、对 3 只 1.01（打不过）；
#   家里 14 兵、主营地 3 只 -> 拆走小队后主队还有 4.03；家里 10 兵、主营地 6 只 -> 拆走只剩 0.45（不许拆）。
def world(foot=14, heroes=2, camp_a=3, camp_b=2, t=600.0, hero_hp=1.0):
    mine = [HOME] + [U(10 + i, "hfoo", 0, -5500 + 60 * i, 0) for i in range(foot)]
    hs = [U(2, "Hamg", 0, -5500, 100, 500 * hero_hp, 500), U(3, "Hmkg", 0, -5400, 100, 700 * hero_hp, 700)][:heroes]
    wild = creeps(CAMP_A, camp_a, at=100) + creeps(CAMP_B, camp_b, at=200)
    return W.build_world(mine + hs + wild, 0, t, 500, 300, 60, 100, levels={2: 3, 3: 2})


def army_of(w):
    return list(w.my_army)


def heroes_of(w):
    return list(w.my_heroes)


class Gates(unittest.TestCase):
    def test_needs_two_heroes(self):
        s, w = S.SplitCreep(), world(heroes=1)
        self.assertEqual(s.step(w, HOME, army_of(w), heroes_of(w)), [])
        self.assertEqual(s.counts.get("分队打野·英雄不够"), 1)

    def test_needs_enough_units(self):
        s, w = S.SplitCreep(), world(foot=S.SPLIT_MIN_ARMY - 1)
        self.assertEqual(s.step(w, HOME, army_of(w), heroes_of(w)), [])
        self.assertEqual(s.counts.get("分队打野·人不够"), 1)

    def test_hurt_hero_does_not_lead(self):
        s, w = S.SplitCreep(), world(hero_hp=0.5)
        self.assertEqual(s.step(w, HOME, army_of(w), heroes_of(w)), [])
        self.assertEqual(s.counts.get("分队打野·英雄不够"), 1)

    def test_no_second_camp_it_can_beat(self):
        s, w = S.SplitCreep(), world(camp_b=3)            # 小队对 3 只只有 1.01 < 1.808
        s.step(w, HOME, army_of(w), heroes_of(w), main_camp_ids={c.addr for c in w.creeps if c.y > 0})
        self.assertEqual(s.counts.get("分队打野·没有打得过的第二个营地"), 1, s.counts)
        self.assertIsNone(s.party)

    def test_does_not_gut_the_main_force(self):
        w = world(foot=S.SPLIT_MIN_ARMY, camp_a=6)        # 主队那个营地要全军才打得动
        main_ids = {c.addr for c in w.creeps if c.y > 0}
        s = S.SplitCreep()
        s.step(w, HOME, army_of(w), heroes_of(w), main_camp_ids=main_ids)
        self.assertEqual(s.counts.get("分队打野·拆了主队就打不动"), 1, s.counts)
        self.assertIsNone(s.party)


class Party(unittest.TestCase):
    def form(self):
        s, w = S.SplitCreep(), world()
        main_ids = {c.addr for c in w.creeps if c.y > 0}
        it = s.step(w, HOME, army_of(w), heroes_of(w), main_camp_ids=main_ids)
        return s, w, it

    def test_sends_the_lower_level_hero_with_an_escort(self):
        s, w, it = self.form()
        self.assertEqual(s.counts.get("分队打野·派出"), 1, s.counts)
        self.assertEqual(s.party.hero, 3, "等级低的那个英雄带队去练级（Hmkg 2 级 < Hamg 3 级）")
        self.assertEqual(len(s.party.addrs), S.SPLIT_ESCORT)
        self.assertEqual([(x.kind, x.why) for x in it], [("attack_move", "第二队打野：走向营地")])
        self.assertEqual((s.party.camp["x"], s.party.camp["y"]), (it[0].x, it[0].y))
        self.assertLess(math.hypot(it[0].x - CAMP_B[0], it[0].y - CAMP_B[1]), 200, "去的是另一个营地，不是主队那个")
        self.assertEqual(s.busy_addrs, set(s.party.addrs) | {3})

    def test_engages_once_the_squad_is_there(self):
        s, w, _ = self.form()
        ids = s.busy_addrs
        there = [U(a, "hfoo" if a != 3 else "Hmkg", 0, CAMP_B[0] + 40, CAMP_B[1] + 40) for a in sorted(ids)]
        rest = [u for u in w.units if u.addr not in ids]
        w2 = W.build_world(there + rest, 0, w.t + 20, 500, 300, 60, 100, levels={2: 3, 3: 2})
        it = s.step(w2, HOME, army_of(w2), heroes_of(w2))
        self.assertEqual(s.counts.get("分队打野·开打"), 1, s.counts)
        self.assertEqual(it[0].kind, "attack")
        self.assertIn(it[0].target.addr, s.party.camp_ids)

    def test_cleared_camp_ends_the_party(self):
        s, w, _ = self.form()
        rest = [u for u in w.units if u.addr not in s.party.camp_ids]
        w2 = W.build_world(rest, 0, w.t + 40, 500, 300, 60, 100, levels={2: 3, 3: 2})
        self.assertEqual(s.step(w2, HOME, army_of(w2), heroes_of(w2)), [])
        self.assertEqual(s.counts.get("分队打野·营地清空"), 1)
        self.assertIsNone(s.party)

    def test_recall_brings_them_home(self):
        s, w, _ = self.form()
        it = s.step(w, HOME, army_of(w), heroes_of(w), recall=True)
        self.assertEqual(s.counts.get("分队打野·收队·主力要用人"), 1)
        self.assertEqual((it[0].kind, it[0].x, it[0].y), ("move", HOME.x, HOME.y))
        self.assertIsNone(s.party)

    def test_losing_the_fight_pulls_them_out(self):
        s, w, _ = self.form()
        ids = sorted(s.busy_addrs)
        hurt = [U(a, "hfoo" if a != 3 else "Hmkg", 0, CAMP_B[0], CAMP_B[1], 20, 420) for a in ids]
        rest = [u for u in w.units if u.addr not in set(ids)]
        w2 = W.build_world(hurt + rest, 0, w.t + 30, 500, 300, 60, 100, levels={2: 3, 3: 2})
        it = s.step(w2, HOME, army_of(w2), heroes_of(w2))
        self.assertTrue(s.counts.get("分队打野·收队·英雄残血") or s.counts.get("分队打野·收队·打不动"), s.counts)
        self.assertEqual(it[0].kind, "move")
        self.assertIsNone(s.party)

    def test_cooldown_before_the_next_split(self):
        s, w, _ = self.form()
        s.step(w, HOME, army_of(w), heroes_of(w), recall=True)
        self.assertEqual(s.step(w, HOME, army_of(w), heroes_of(w)), [], "刚收队不立刻再分")
        later = world(t=w.t + S.SPLIT_COOLDOWN_S + 1)
        self.assertTrue(s.step(later, HOME, army_of(later), heroes_of(later)))


class Distance(unittest.TestCase):
    def test_escort_comes_from_around_the_lead_hero(self):
        s, w = S.SplitCreep(), world()
        s.step(w, HOME, army_of(w), heroes_of(w))
        lead = next(h for h in w.my_heroes if h.addr == s.party.hero)
        picked = [u for u in w.my_army if u.addr in set(s.party.addrs)]
        far = [u for u in w.my_army if u.addr not in set(s.party.addrs)]
        self.assertLessEqual(max(math.hypot(u.x - lead.x, u.y - lead.y) for u in picked),
                             min(math.hypot(u.x - lead.x, u.y - lead.y) for u in far) + 1e-6)


if __name__ == "__main__":
    unittest.main()

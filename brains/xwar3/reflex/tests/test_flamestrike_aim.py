# -*- coding: utf-8 -*-
"""烈焰风暴：只瞄站桩的敌人、射程内不走路；圈里有自己人照样放，放完让他们躲开（mod_combat Motion / ff_dodge_moves / _rule_aoe_point）。

09-22 烈焰风暴会伤到自家单位：放技能时让自家单位主动撤离作用区域；
同时提高落点精度，尽量覆盖更多敌方单位。
1 号实例 313 次：火落地后落点 200 内我方多掉血平均 +82、敌方 +54；落点里一个敌人都没有的 57%（瞄重心、先走过去，1.7 秒后人散了）。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402


def u(addr, t, x, y, owner=0, hp=300.0, hp_max=300.0, hero=False):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), owner=owner, hp=hp, hp_max=hp_max,
                           is_hero=hero, order=0, handle_lo=addr, handle_hi=1)


def plan(hero, foes, allies, still):
    return C.plan_hero_cast(hero, foes, [hero] + allies, lambda n: n == "flamestrike", mana=500.0,
                            learned={"AHfs": 2}, ctx={"pulled": False, "still": still})


class Still(unittest.TestCase):
    def test_standing_vs_walking(self):
        m = C.Motion()
        a = u(10, "ogru", 0, 0, owner=1)
        b = u(11, "ogru", 0, 100, owner=1)
        for k in range(6):                       # 0.6 墙钟秒，每 0.1 秒一次
            b.x = 60.0 * k                       # 每墙钟秒 600（移速 270 × 3 倍速 = 810 的 74%）
            m.update([a, b], 100.0 + 0.1 * k)
        self.assertTrue(m.still(a, 100.5))
        self.assertFalse(m.still(b, 100.5))

    def test_short_history_is_not_still(self):
        m = C.Motion()
        a = u(10, "ogru", 0, 0, owner=1)
        m.update([a], 100.0)
        self.assertFalse(m.still(a, 100.1), "历史不够长，不知道")


class Aim(unittest.TestCase):
    def setUp(self):
        self.bm = u(1, "Hblm", 0, 0, hero=True)

    def test_aims_at_the_standing_group_not_the_bigger_moving_one(self):
        standing = [u(100 + i, "ogru", 600 + 40 * i, 0, owner=1) for i in range(3)]
        moving = [u(200 + i, "ogru", 0, 600 + 40 * i, owner=1) for i in range(5)]
        p = plan(self.bm, standing + moving, [], {f.addr for f in standing})
        self.assertEqual(p[0], "flamestrike")
        self.assertLess(math.hypot(p[4] - 640, p[5]), 60, "落在站桩那一团")
        # 没有站桩数据（旧调用方）时照旧打最大那一团
        p = plan(self.bm, standing + moving, [], None)
        self.assertGreater(p[5], 500)

    def test_only_moving_enemies_means_no_cast(self):
        moving = [u(200 + i, "ogru", 500, 40 * i, owner=1) for i in range(5)]
        self.assertIsNone(plan(self.bm, moving, [], set()))

    def test_stays_in_range_no_walking(self):
        rng = C.skill("flamestrike", 2).get("Rng") or 800.0
        far = [u(100 + i, "ogru", rng + 50 + 30 * i, 0, owner=1) for i in range(4)]
        self.assertIsNone(plan(self.bm, far, [], {f.addr for f in far}), "射程外不走过去放")
        near = [u(100 + i, "ogru", rng - 200 + 30 * i, 0, owner=1) for i in range(4)]
        p = plan(self.bm, near, [], {f.addr for f in near})
        self.assertLessEqual(math.hypot(p[4], p[5]), rng)

    def test_allies_in_the_circle_do_not_block_and_fewer_allies_win_a_tie(self):
        a = [u(100 + i, "ogru", 600, 40 * i, owner=1) for i in range(3)]
        b = [u(200 + i, "ogru", 40 * i, 600, owner=1) for i in range(3)]
        allies = [u(10 + i, "hfoo", 620, 30 * i) for i in range(2)] + [u(20, "hfoo", 40, 620)]
        p = plan(self.bm, a + b, allies, {f.addr for f in a + b})
        self.assertEqual(p[0], "flamestrike", "圈里有自己人照样放（放完让他们躲）")
        self.assertGreater(p[5], 500, "两团一样多时挑要躲的自己人少的那团")


class Dodge(unittest.TestCase):
    """09-22 晚 血法师技能区域在效果结束前所有单位都要绕开，移动路径也不得穿过 —— 火圈（ff_zone）。"""

    def zone(self, now=100.0):
        bm = u(1, "Hblm", 0, 0, hero=True)
        return C.ff_zone(600.0, 0.0, C.skill("flamestrike", 1), bm, now), bm

    def test_zone_lasts_the_whole_burn_not_just_the_full_damage_part(self):
        z, _ = self.zone()
        self.assertAlmostEqual(z["until"] - 100.0, (1.33 + 9.0) / C.GAME_SPEED + C.FF_ZONE_TAIL, places=2)
        self.assertGreater(z["until"] - 100.0, 3.0, "原来只握 1.6 秒（前摇 + 满伤害），后 6 游戏秒照样烧")

    def test_allies_in_the_circle_step_out(self):
        z, bm = self.zone()
        inside = [u(10, "hfoo", 650, 0), u(11, "hfoo", 600, 100), u(12, "hfoo", 600, 0)]
        edge = u(13, "hrif", 600 + 200 + C.FF_DODGE_PAD - 10, 0)
        outside = u(14, "hrif", 600, 600)
        moves = C.ff_fence_moves(z, [bm] + inside + [edge, outside], {})
        self.assertEqual({m[0].addr for m in moves}, {10, 11, 12, 13})
        for unit, tx, ty in moves:
            self.assertAlmostEqual(math.hypot(tx - 600, ty), 200 + C.FF_DODGE_OUT, delta=1)
        centre = next(m for m in moves if m[0].addr == 12)
        self.assertGreater(centre[1], 600, "站在落点正中的背离施法者走（往敌人那边外侧，不回头穿过施法者）")

    def test_units_walking_in_later_are_pushed_out_once(self):
        z, bm = self.zone()
        walker = u(20, "hkni", 700, 0)                       # 火还在烧的时候走进圈边
        held = {}
        moves = C.ff_fence_moves(z, [walker], held)
        self.assertEqual([m[0].addr for m in moves], [20])
        held[20] = z["until"]
        self.assertEqual(C.ff_fence_moves(z, [walker], held), [], "这个圈已经推过、握着：不重复下令")

    def test_the_caster_is_fenced_too(self):
        z, bm = self.zone()
        bm.x = 610.0                                         # 血法自己走进火里
        self.assertEqual([m[0].addr for m in C.ff_fence_moves(z, [bm], {})], [1])

    def test_flamestrike_is_marked_for_dodging(self):
        self.assertEqual(C.SPELLS["flamestrike"]["opts"].get("friendly_fire"), "dodge")


if __name__ == "__main__":
    unittest.main()

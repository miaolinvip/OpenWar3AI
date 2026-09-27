# -*- coding: utf-8 -*-
"""空档安排：没营地可打、也不该出击时不干站（tactics.AttackSequence._idle_plan）。

09-22 晚 打完野后部队长时间闲置（1-409 12 分）。要求提前部署后续安排，并随局势发展随时调整。
22:44:41~22:45:15 大部队 12~13 个每拍都是「出击·目标不好」+「打野·没有可选营地」，原来走到这里 return []，直到对面打上门。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HOME = (-5000.0, 3000.0)
EXP = (-4352.0, -2624.0)
FOE_MAIN = (4672.0, 2944.0)


def world(extra=(), foe_army=True):
    units = [U(1, "hcas", *HOME), U(2, "htow", *EXP), U(3, "hbar", HOME[0] + 500, HOME[1])]
    units += [U(300 + i, "hpea", HOME[0] + 100, HOME[1]) for i in range(5)]
    units += [U(10 + i, "hkni", EXP[0] + 100, EXP[1] + 700 + 60 * i) for i in range(6)]
    units += [U(900, "unpl", *FOE_MAIN, owner=1), U(901, "unpl", 3840, -3328, owner=1)]
    if foe_army:                                   # 对面比我们强：出击评估不通过
        units += [U(800 + i, "ugho", FOE_MAIN[0] - 300, FOE_MAIN[1] + 60 * i, owner=1) for i in range(16)]
    return build_world(units + list(extra), 0, 700.0, 300, 300, 40, 60, levels={})


def idle(w, incoming=()):
    s = T.AttackSequence(dict(PROFILE))
    s.incoming = list(incoming)
    return s, s.step(w, w.my_halls[0])


class IdlePlan(unittest.TestCase):
    def test_no_camp_no_attack_goes_to_the_front_of_the_exposed_base(self):
        s, out = idle(world())
        self.assertTrue(out, "原来这里一条令都没有")
        hold = [i for i in out if "前沿驻守" in i.why]
        self.assertTrue(hold, [(i.kind, i.why) for i in out])
        exposed = min([HOME, EXP], key=lambda h: (h[0] - FOE_MAIN[0]) ** 2 + (h[1] - FOE_MAIN[1]) ** 2)
        self.assertLess(((hold[0].x - exposed[0]) ** 2 + (hold[0].y - exposed[1]) ** 2) ** 0.5, T.IDLE_FRONT_R + 50)

    def test_incoming_captain_pre_positions_at_that_town(self):
        w = world()
        town = next(t for t in w.towns if t.owner == w.me and abs(t.x - HOME[0]) < 10)
        s, out = idle(w, [dict(town=town, captain=dict(x=0.0, y=0.0), dist=6000.0)])
        pre = [i for i in out if "电脑队长要打" in i.why]
        self.assertTrue(pre, [(i.kind, i.why) for i in out])
        self.assertLess(((pre[0].x - HOME[0]) ** 2 + (pre[0].y - HOME[1]) ** 2) ** 0.5, T.IDLE_FRONT_R + 50)

    def plan(self, w):
        s = T.AttackSequence(dict(PROFILE))
        s.allow_attack = s.allow_creep = True
        return s, s._idle_plan(w, w.my_army + w.my_heroes, None) or []

    def test_1411_camps_on_the_enemy_side_are_left_alone(self):
        """1-411 23:05:09：整支部队被派去对面家后面、离家 1 万多的营地，撞上对面主力，大法师被睡眠定住、留下来被围死。"""
        far = [U(700 + i, "nmrm", 6013, -4621 + 60 * i, owner=12) for i in range(4)]
        near_exp = [U(710 + i, "nmrr", 2820, -3792 + 60 * i, owner=12) for i in range(3)]
        s, out = self.plan(world(far + near_exp, foe_army=False))
        self.assertFalse([i for i in out if i.kind == "attack"], [(i.kind, i.why) for i in out])

    def test_camp_on_our_half_is_taken_when_we_are_not_weaker(self):
        ours = [U(720 + i, "nmrr", -1500, -2000 + 60 * i, owner=12) for i in range(3)]
        s, out = self.plan(world(ours, foe_army=False))
        take = [i for i in out if i.kind == "attack"]
        self.assertTrue(take, [(i.kind, i.why) for i in out])
        self.assertIn(take[0].target.addr, {720, 721, 722})

    def test_not_when_the_enemy_is_stronger_or_their_army_is_on_it(self):
        ours = [U(720 + i, "nmrr", -1500, -2000 + 60 * i, owner=12) for i in range(3)]
        s, out = self.plan(world(ours))                                  # 对面 16 个食尸鬼：比我们强
        self.assertFalse([i for i in out if i.kind == "attack"])
        self.assertEqual(s.counts.get("空闲·对面比我们强·不去抢营地"), 1)
        guards = [U(850 + i, "ugho", -1400, -1800 + 60 * i, owner=1) for i in range(3)]
        s, out = self.plan(world(ours + guards, foe_army=False))         # 对面有人守着这营
        self.assertFalse([i for i in out if i.kind == "attack"])


class IdleHarass(unittest.TestCase):
    """1-410 14 分：部队一直待在家里，其实可以分路去骚扰 —— 大部队空档时骚扰按占优放行（harass.step idle）。"""

    def test_idle_flag_is_set_only_on_the_idle_branch(self):
        s, _ = idle(world())
        self.assertTrue(s.idle_now)
        s2 = T.AttackSequence(dict(PROFILE))
        s2.allow_attack = s2.allow_creep = False                       # 出击、打野都关：不安排，也不标空闲
        w = world()
        s2.step(w, w.my_halls[0])
        self.assertFalse(s2.idle_now)

    def test_harass_goes_out_while_the_army_is_idle_even_when_not_ahead(self):
        from brain import harass as H
        from brain import world as W
        from test_harass_gates import U as HU
        home = HU(1, "htow", 0, 0, 0)
        army = [HU(10 + i, "hfoo", 0, 100 * i, 0) for i in range(3)] + [HU(20 + i, "hrif", 0, 200, 100 * i) for i in range(3)]
        enemy = [HU(100, "hcas", 1, 9000, 9000), HU(111, "hpea", 1, 4000, 0), HU(112, "htow", 1, 4200, 0)]
        enemy += [HU(120 + i, "hfoo", 1, 9000, 8800 + 60 * i) for i in range(12)]      # 对面 12 个兵：不占优
        w = W.build_world([home, HU(2, "Hamg", 0, 0, 0)] + army + enemy, 0, 300.0, 500, 200, 40, 60, levels={2: 3})
        never = SimpleNamespace(uniform=lambda a, b: 100.0)            # 抽签永远没抽中
        m = H.HarassManager(rng=never)
        m.step(w, home, busy=False, town_threatened=False)
        self.assertEqual(m.active, {}, m.counts)
        m2 = H.HarassManager(rng=never)
        m2.step(w, home, busy=False, town_threatened=False, idle=True)
        self.assertIn(1, m2.active, m2.counts)
        self.assertEqual(m2.counts.get("主力空闲·按占优放行"), 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""按野怪真实在打谁轮换（09-18 读出单位是否被敌方单位（含野怪）列为攻击目标，据此把残血单位拉回、
不再被当作攻击目标时再送上去，打野时尤其需要）。只读实测：打野中 32 次读，27 次野怪的任务目标对上我方具体单位。"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, hp, hp_max=None, owner=0, task=(1, 1)):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp, hp_max=hp_max or hp,
                order=0, order_known=True, handle_lo=addr, handle_hi=7, task_handle_lo=task[0], task_handle_hi=task[1])


class Cache(unittest.TestCase):
    def test_due_changed_first_then_stale(self):
        a, b = unit(100, "ngnb", 0, 0, 400, owner=12), unit(101, "ngna", 50, 0, 240, owner=12)
        ag = C.CreepAggro()
        self.assertEqual([c.addr for c in ag.due([a, b], 0.0)], [100, 101])          # 没读过
        ag.put(a, (1, 7), 0.0)
        ag.put(b, (2, 7), 0.5)
        self.assertEqual(ag.due([a, b], 0.9), [])
        self.assertEqual([c.addr for c in ag.due([a, b], 1.2)], [100])               # a 到了兜底重读
        a2 = unit(100, "ngnb", 0, 0, 400, owner=12, task=(9, 9))                     # 任务句柄变了 = 换目标了
        self.assertEqual([c.addr for c in ag.due([a2, b], 1.2)], [100])
        self.assertEqual(ag.due([a2, b], 1.2)[0].addr, 100)

    def test_by_target_only_fresh(self):
        a, b = unit(100, "ngnb", 0, 0, 400, owner=12), unit(101, "ngna", 50, 0, 240, owner=12)
        ag = C.CreepAggro()
        ag.put(a, (1, 7), 0.0)
        ag.put(b, (1, 7), 2.0)
        self.assertEqual({k: [c.addr for c in v] for k, v in ag.by_target([a, b], 2.0).items()}, {(1, 7): [100, 101]})
        self.assertEqual({k: [c.addr for c in v] for k, v in ag.by_target([a, b], 3.0).items()}, {(1, 7): [101]})   # a 的读数过期
        ag.put(a, None, 3.0)                                                          # 睡着/没目标
        self.assertEqual([c.addr for c in ag.by_target([a, b], 3.0)[(1, 7)]], [101])


class Pull(unittest.TestCase):
    def setUp(self):
        self.brute = unit(100, "ngnb", 100, 0, 400, owner=12)

    def test_low_hp_targeted_is_pulled(self):
        foot = unit(1, "hfoo", 0, 0, 170, 420)                                     # 40%
        out = C.aggro_pulls([foot], {(1, 7): [self.brute]}, set(), 1)
        self.assertEqual([u.addr for u, _a, _t in out], [1])

    def test_healthy_or_untargeted_not_pulled(self):
        foot = unit(1, "hfoo", 0, 0, 400, 420)
        other = unit(2, "hfoo", 0, 50, 100, 420)                                    # 血低但没被盯着
        out = C.aggro_pulls([foot, other], {(1, 7): [self.brute]}, set(), 2)
        self.assertEqual(out, [])

    def test_ttd_triggers_even_when_hp_ok(self):
        foot = unit(1, "hfoo", 0, 0, 250, 420)                                      # 60%：血量这条不触发
        eight = [unit(100 + i, "ngnb", 100, 20 * i, 400, owner=12) for i in range(8)]   # 8 x 10.7 = 85.6/秒 -> 2.9 秒
        three = eight[:3]                                                           # 3 x 10.7 = 32.1/秒 -> 7.8 秒
        out = C.aggro_pulls([foot], {(1, 7): eight}, set(), 1)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][2], 250 / (8 * C.DPS["ngnb"]))
        self.assertEqual(C.aggro_pulls([foot], {(1, 7): three}, set(), 1), [])

    def test_summon_not_pulled_and_room_cap(self):
        wat = unit(3, "hwat", 0, 0, 100, 525)
        f1, f2 = unit(1, "hfoo", 0, 0, 60, 420), unit(2, "hfoo", 0, 0, 120, 420)
        bt = {(3, 7): [self.brute], (1, 7): [self.brute], (2, 7): [self.brute]}
        out = C.aggro_pulls([wat, f1, f2], bt, set(), 1)
        self.assertEqual([u.addr for u, _a, _t in out], [1])                        # 最危险的那个，水元素不拉
        self.assertEqual(C.aggro_pulls([wat, f1, f2], bt, {1}, 5)[0][0].addr, 2)   # 已经在外面的跳过

    def test_healthy_permanent_melee_rotates_immediately_when_summon_can_tank(self):
        wat = unit(3, "hwat", 0, 0, 525, 525)
        foot = unit(1, "hfoo", 30, 0, 420, 420)
        out = C.aggro_pulls([wat, foot], {(1, 7): [self.brute]}, set(), 1)
        self.assertEqual([u.addr for u, _a, _t in out], [foot.addr])
        self.assertEqual(out[0][2], -1.0)  # 不是残血；只因召唤物在场而交接仇恨

    def test_hero_threshold_higher(self):
        am = unit(4, "Hamg", 0, 0, 350, 675)                                        # 52%：兵不拉、英雄拉
        self.assertEqual(len(C.aggro_pulls([am], {(4, 7): [self.brute]}, set(), 1)), 1)

    # ★ 09-18（5-035 局 8 分钟）大法师冲在最前面给野怪当坦克，被打到残血
    def test_ranged_hero_pulled_at_full_hp_when_a_tank_is_there(self):
        am = unit(4, "Hamg", 0, 0, 675, 675)                                        # 满血
        wat = unit(3, "hwt2", 50, 0, 675)                                           # 水元素在场扛
        out = C.aggro_pulls([am, wat], {(4, 7): [self.brute]}, set(), 1)
        self.assertEqual([u.addr for u, _a, _t in out], [4])

    def test_ranged_hero_tanks_when_nobody_else_can(self):
        am = unit(4, "Hamg", 0, 0, 675, 675)
        rif = unit(5, "hrif", 50, 0, 535)                                           # 只有远程兵：没人能扛，照旧等血低再拉
        self.assertEqual(C.aggro_pulls([am, rif], {(4, 7): [self.brute]}, set(), 1), [])

    def test_melee_hero_uses_old_threshold(self):
        mk = unit(6, "Hmkg", 0, 0, 700, 700)
        foot = unit(1, "hfoo", 50, 0, 420)
        self.assertEqual(C.aggro_pulls([mk, foot], {(6, 7): [self.brute]}, set(), 1), [])

    def test_ranged_hero_goes_first(self):
        am = unit(4, "Hamg", 0, 0, 675, 675)
        f1 = unit(1, "hfoo", 0, 0, 60, 420)                                         # 被盯着、快死了
        f2 = unit(2, "hfoo", 30, 0, 400, 420)
        out = C.aggro_pulls([am, f1, f2], {(4, 7): [self.brute], (1, 7): [self.brute]}, set(), 1)
        self.assertEqual([u.addr for u, _a, _t in out], [4])


class Geometry(unittest.TestCase):
    def test_out_point_beyond_attacker_range(self):
        foot = unit(1, "hfoo", 0, 0, 100, 420)
        ranged = unit(100, "ngna", 300, 0, 240, owner=12)                           # 刺客远程
        x, y = C.aggro_out_point(foot, [ranged])
        self.assertLess(x, 0)
        self.assertGreaterEqual(math.hypot(x - ranged.x, y - ranged.y), C.RANGE.get("ngna", 100) + C.AGGRO_OUT_PAD)
        self.assertGreaterEqual(math.hypot(x, y), C.ROT_OUT - 1)


class Ready(unittest.TestCase):
    def test_back_only_after_clear(self):
        foot = unit(1, "hfoo", 0, 0, 250, 420)                                      # 60%（09-18 晚起 45% 以下留在外面，见下一条）
        st = {"out_at": 0.0, "clear_since": None}
        self.assertEqual(C.aggro_ready(foot, True, st, 0.3), "chased")
        self.assertEqual(C.aggro_ready(foot, False, st, 0.6), "stay")               # 刚没人盯
        self.assertEqual(C.aggro_ready(foot, False, st, 1.0), "stay")
        self.assertEqual(C.aggro_ready(foot, False, st, 1.25), "back")              # 满 0.6 秒
        st2 = {"out_at": 0.0, "clear_since": None}
        self.assertEqual(C.aggro_ready(foot, False, st2, 0.6), "stay")
        self.assertEqual(C.aggro_ready(foot, True, st2, 0.9), "chased")             # 又被盯：重新计时
        self.assertEqual(C.aggro_ready(foot, False, st2, 1.2), "stay")

    def test_too_low_stays_out(self):
        # ★ 027 局后 AGGRO_STAY_FRAC -> 0.40：30% 仍撑不到换目标后的第一轮伤害。
        foot = unit(1, "hfoo", 0, 0, 100, 420)                                      # 24%：原来会被送回
        st = {"out_at": 0.0, "clear_since": 0.0}
        self.assertEqual(C.aggro_ready(foot, False, st, 10.0), "stay")
        ok = unit(2, "hfoo", 0, 0, 180, 420)                                        # 43%：过安全线且不再被盯着才送回
        self.assertEqual(C.aggro_ready(ok, False, {"out_at": 0.0, "clear_since": 0.0}, 10.0), "back")


if __name__ == "__main__":
    unittest.main()

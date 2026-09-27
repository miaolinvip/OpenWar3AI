# -*- coding: utf-8 -*-
"""打野肉盾先接火（09-18 5-035 局 8 分钟：大法师冲在最前面替全队承伤，被野怪打到残血）。

原来到场的人一起点名打同一只野怪，大法师移速 320 比步兵快、又是远程，常常第一个出手，仇恨在它身上；
毫秒层按仇恨轮换要等它掉到 55% 血以下才拉。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

CAMP = (4000.0, 0.0)


def world(tank_x):
    base = [U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    base += [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)]
    am = U(2, "Hamg", CAMP[0] - 700, 0)
    tanks = [U(10 + i, "hfoo", tank_x, 80 * i) for i in range(3)] + [U(20, "hwt2", tank_x, -100)]
    rif = [U(30, "hrif", CAMP[0] - 650, 200)]
    creeps = [U(700 + i, "nwlg", CAMP[0] + 60 * i, 0, owner=12) for i in range(2)]
    return build_world(base + [am] + tanks + rif + creeps, 0, 600.0, 300, 300, 20, 40, levels={2: 2})


def creeping(w):
    s = T.AttackSequence(dict(PROFILE))
    s.state, s.camp_ids, s.creep_since, s.creep_engaged = "creep", {700, 701}, w.t, True
    s.camp = dict(x=CAMP[0] + 30, y=0.0, strength=8.0, units=[])
    return s


class TankFirst(unittest.TestCase):
    def test_ranged_wait_until_tanks_in_contact(self):
        w = world(tank_x=CAMP[0] - 600)                                    # 肉盾离野怪 600：还没贴上
        s = creeping(w)
        out = s.step(w, w.my_halls[0])
        hit = next(i for i in out if i.kind == "attack")
        waits = [i for i in out if i.kind == "move" and "永久兵不抢承伤" in i.why]
        self.assertEqual({u.addr for u in hit.units}, {20})                 # 有水元素时只让召唤物先接火
        self.assertEqual({u.addr for i in waits for u in i.units}, {2, 10, 11, 12, 30})
        tgt = hit.target
        for wait in waits:
            self.assertAlmostEqual(((wait.x - tgt.x) ** 2 + (wait.y - tgt.y) ** 2) ** 0.5,
                                   T.CREEP_BACK_STAND, delta=300.0)
        self.assertEqual(s.counts.get("打野·召唤物优先承伤"), 1)

    def test_everyone_hits_once_tanks_in_contact(self):
        w = world(tank_x=CAMP[0] - 100)                                    # 贴上了
        s = creeping(w)
        out = s.step(w, w.my_halls[0])
        hit = next(i for i in out if i.kind == "attack")
        self.assertEqual({2, 20, 30}, {u.addr for u in hit.units})          # 召唤物刚贴上：远程输出，永久近战再等 2 秒
        self.assertFalse({10, 11, 12} & {u.addr for u in hit.units})
        w.t += T.CREEP_SUMMON_SETTLE_S + .1
        out = s.step(w, w.my_halls[0])
        self.assertTrue({2, 30, 10, 11, 12, 20} <= {u.addr for i in out if i.kind == "attack" for u in i.units})
        self.assertIsNone(s.counts.get("打野·远程等肉盾接火"))

    def test_no_tanks_everyone_hits(self):
        units = [U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
        units += [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)]
        units += [U(2, "Hamg", CAMP[0] - 700, 0)] + [U(30 + i, "hrif", CAMP[0] - 650, 80 * i) for i in range(4)]
        units += [U(700, "nwlt", CAMP[0], 0, owner=12)]
        w = build_world(units, 0, 600.0, 300, 300, 20, 40, levels={2: 2})
        s = creeping(w)
        s.camp_ids = {700}
        out = s.step(w, w.my_halls[0])
        self.assertIn(2, {u.addr for i in out if i.kind == "attack" for u in i.units})

    def test_backline_classification(self):
        self.assertTrue(T.is_backline("Hamg"))
        self.assertTrue(T.is_backline("hrif"))
        self.assertFalse(T.is_backline("hwt2"))                             # 水元素：召唤物，拿来扛的
        self.assertFalse(T.is_backline("Hmkg"))
        self.assertFalse(T.is_backline("hfoo"))

    def test_opening_hero_tags_then_water_anchor_takes_over(self):
        w = world(tank_x=CAMP[0] - 600)
        s = creeping(w)
        s.creep_engaged = False
        out = s.step(w, w.my_halls[0])
        first = next(i for i in out if i.kind == "attack")
        self.assertEqual({u.addr for u in first.units}, {2})                # 全队到齐后，大法师先点一下拉怪
        self.assertEqual(s.counts.get("打野·英雄先手拉怪"), 1)

        w.t += 2
        out = s.step(w, w.my_halls[0])
        anchors = next(i for i in out if i.kind == "attack")
        flanks = [i for i in out if i.why == "打野·前排两翼包围拉出的核心"]
        waits = [i for i in out if i.kind == "move"]
        self.assertEqual({u.addr for u in anchors.units}, {20})             # 下一拍水元素接仇恨
        self.assertFalse(flanks)                                             # 永久近战也在外圈，不和召唤物抢第一轮仇恨
        self.assertEqual({u.addr for i in waits for u in i.units}, {2, 10, 11, 12, 30})
        self.assertEqual(s.counts.get("打野·英雄回拉肉盾接火"), 1)

    def test_focuses_highest_damage_core_before_low_hp_small_creep(self):
        small = U(700, "nmrr", CAMP[0], 0, owner=12)
        core = U(701, "ntrg", CAMP[0] + 80, 0, owner=12)
        small.hp = 30
        self.assertEqual(T.creep_focus_target([small, core], {}).addr, core.addr)

    def test_keeps_finishing_wounded_current_core(self):
        core = U(700, "ntrg", CAMP[0], 0, owner=12)
        peer = U(701, "nenf", CAMP[0] + 80, 0, owner=12)
        core.hp = 300
        self.assertEqual(T.creep_focus_target([core, peer], {}, current_id=core.addr).addr, core.addr)


if __name__ == "__main__":
    unittest.main()

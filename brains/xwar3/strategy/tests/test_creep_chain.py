# -*- coding: utf-8 -*-
"""打野不来回拉扯、轻松营地先安全成团、清完就近接着打（09-18 部队在打野和回集合点之间反复往返，浪费大量时间；
要求附近打得过的营地直接打，打完按最近距离和当前剩余血量、战力评估能否接着打周围最近的营地）。

实机 140827-027 局：第 14~16 拍「选营地 战力比 0.9、等兵 77 秒」-> 下一秒「打不动回家 0.92 < 1.0」，1.5 秒一圈；
第 36 拍强度 8、全军战力比 4.7 的小营地，先到的 2 个也被拉回集合点等人。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

FOE = (0, 16000)


def world(units, t=600.0):
    base = [U(1, "htow", 0, 0), U(900, "htow", *FOE, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    base += [U(800 + i, "uabo", FOE[0], FOE[1] - 500 + 80 * i, owner=1) for i in range(10)]    # 对面家里有兵（不然出击先抢走）
    return build_world(base + units, 0, t, 300, 300, 30, 60, levels={2: 3})


def hero(x, y, frac=1.0):
    h = U(2, "Hamg", x, y)
    h.hp_max = 600.0
    h.hp = 600.0 * frac
    return h


def creeping(w, camp_xy, ids, strength, engaged=False):
    s = T.AttackSequence(dict(PROFILE))
    s.state, s.camp_ids, s.creep_since, s.creep_engaged = "creep", set(ids), w.t, engaged
    s.camp = dict(x=float(camp_xy[0]), y=float(camp_xy[1]), strength=float(strength), units=[])
    return s


class NoPingPong(unittest.TestCase):
    def scene(self):
        army = [hero(3000, 0)] + [U(10 + i, "hfoo", 3000, 80 * i) for i in range(2)]
        camp = [U(700 + i, "nwlg", 4200, 60 * i, owner=12) for i in range(3)]              # 打不过（战力比 < 1）
        return world(army + camp)

    def test_not_engaged_does_not_abort(self):
        w = self.scene()
        s = creeping(w, (4200, 60), range(700, 703), 12)
        self.assertLess(T.power_ratio(w.my_army + w.my_heroes, w.creeps, w.levels), T.CREEP_ABORT_POWER)
        for _ in range(3):
            out = s.step(w, w.my_halls[0])
        self.assertIsNone(s.counts.get("打野·打不动回家"), s.counts)
        self.assertEqual(s.state, "creep")
        self.assertFalse([i for i in out if "回家" in i.why], [i.why for i in out])

    def test_engaged_and_losing_aborts(self):
        w = self.scene()
        s = creeping(w, (4200, 60), range(700, 703), 12, engaged=True)
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.counts.get("打野·打不动回家"), 1)
        self.assertEqual([i.why for i in out], ["打野打不动，整队脱离"])


class EasyCamp(unittest.TestCase):
    def test_first_arrivals_engage_easy_camp(self):
        near = [hero(3700, 0)] + [U(10 + i, "hfoo", 4050, 80 * i) for i in range(2)]      # 步兵已经贴上狼（肉盾接火，大法师可以打）
        far = [U(20 + i, "hfoo", 1000, 80 * i) for i in range(8)]
        camp = [U(700, "nwlt", 4200, 0, owner=12)]                                         # 一只狼：全军战力比很高
        w = world(near + far + camp)
        s = creeping(w, (4200, 0), [700], 2)
        out = s.step(w, w.my_halls[0])
        hit = [i for i in out if i.kind == "attack"]
        self.assertEqual(len(hit), 1, [i.why for i in out])
        self.assertFalse([i for i in out if i.kind == "move"])                              # 不退回集合点

    def test_nobody_there_moves_to_safe_stage_without_opening_camp(self):
        army = [hero(500, 0)] + [U(10 + i, "hfoo", 500, 80 * i) for i in range(6)]
        w = world(army + [U(700, "nwlt", 4200, 0, owner=12)])
        s = creeping(w, (4200, 0), [700], 2)
        out = s.step(w, w.my_halls[0])
        self.assertEqual([(i.kind, round(i.x)) for i in out], [("move", 3200)])
        self.assertEqual(s.counts.get("打野·轻松营地也先到安全点"), 1)


class ChainAfterClear(unittest.TestCase):
    def setUp(self):
        # 09-23 这组测「打完接着打」；场景里对面一座大厅 + 一支部队、第 600 秒，会触发「对面一矿暴兵不出远门」（test_hold_window）
        p = mock.patch.object(T, "hold_window", lambda w: False)
        p.start()
        self.addCleanup(p.stop)

    def test_many_wounded_units_rest_instead_of_chaining_same_tick(self):
        hurt = []
        for i in range(4):
            u = U(10 + i, "hfoo", 3000, 80 * i)
            u.hp, u.hp_max = 180.0, 420.0
            hurt.append(u)
        w = world([hero(3000, 0)] + hurt + [U(710, "nwlt", 4000, 0, owner=12)])
        s = creeping(w, (3000, 0), [700], 8, engaged=True)            # 700 已死亡，旁边仍有下一营地
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.state, "creep")
        self.assertEqual(s.camp_ids, {710})
        self.assertGreater(s.creep_rest_until, w.t)
        self.assertEqual(s.counts.get("打野·清营后伤兵补给"), 1)
        self.assertEqual([i.why for i in out], ["打野后伤兵多，向下一营地安全点前移集结"])
        self.assertNotEqual((out[0].x, out[0].y), (w.my_halls[0].x, w.my_halls[0].y))

    def test_hurt_hero_keeps_creeping_near_easy_camp(self):
        army = [hero(3000, 0, frac=0.5)] + [U(10 + i, "hfoo", 3000, 80 * i) for i in range(6)]
        w = world(army + [U(700, "nwlt", 4000, 0, owner=12)])                               # 1000 外一只狼
        s = T.AttackSequence(dict(PROFILE))
        s.state = "creep"
        s.step(w, w.my_halls[0])
        self.assertEqual(s.counts.get("打野·英雄没满血·就近接着打"), 1)
        self.assertEqual(s.camp_ids, {700})
        self.assertIsNone(s.counts.get("英雄没回血·先不出门"))

    def test_hurt_hero_goes_home_when_nothing_easy_nearby(self):
        army = [hero(3000, 0, frac=0.5)] + [U(10 + i, "hfoo", 3000, 80 * i) for i in range(2)]
        w = world(army + [U(700 + i, "nwlg", 4000, 60 * i, owner=12) for i in range(3)])  # 近处只有打不过的
        s = T.AttackSequence(dict(PROFILE))
        s.state = "creep"
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.counts.get("英雄没回血·先不出门"), 1)
        self.assertEqual([i.why for i in out], ["英雄没回血，单独回家恢复"])
        self.assertEqual([u.type for u in out[0].units], ["Hamg"])


if __name__ == "__main__":
    unittest.main()

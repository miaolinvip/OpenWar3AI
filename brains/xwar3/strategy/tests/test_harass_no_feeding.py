# -*- coding: utf-8 -*-
"""骚扰别去送：敌方主力边上不去、被毫秒层接管的队员不算没了、撤到我方地盘才算撤回来、拉扯过的地方不回去、命令抢不走。

09-22 骚扰派出三个兵，阵亡两个、只回来一个，等于给对方送经验。
1-329：目标在对方主基地（主力在家）；补攻击令（55 > 骚扰普通移动 50）把人派去打大厅边的单位；
毫秒层拉撤后骚扰模块把「在毫秒层手里的两个活人」数成 0、记「人没了」，没人再带它们回家，25 秒后死在对方家门口。
"""
import os
import random
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import gamedata as G  # noqa: E402
from brain import harass as H  # noqa: E402
from brain import world as W  # noqa: E402

C1 = H.HUMAN_GROUPS[0]


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None):
    m = float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=float(x), y=float(y), hp=m if hp is None else hp, hp_max=m)


HOME = U(1, "htow", 0, 0, 0)
FOE_MAIN = [U(100, "ofrt", 1, 9000, 0)] + [U(130 + i, "obar", 1, 9000 + 300 * i, 400) for i in range(3)]
EXPO = U(112, "ostr", 1, 6000, -3000)


def world(mine=(), foes=(), t=300.0, peons=((6000, -4000),)):
    base = [HOME, U(2, "Hamg", 0, 0, 0)] + [U(30 + i, "hrif", 0, 150 * i, 300) for i in range(8)]
    pe = [U(200 + i, "opeo", 1, x, y) for i, (x, y) in enumerate(peons)]
    return W.build_world(base + list(mine) + FOE_MAIN + [EXPO] + pe + list(foes), 0, t, 500, 200, 40, 60, levels={2: 3})


SQUAD = [U(10 + i, "hfoo", 0, 100 * i, 0) for i in range(3)]


def manager():
    m = H.HarassManager(groups_cfg=(C1,), rng=random.Random(0))
    m.rng.uniform = lambda a, b: 0.0
    return m


def moved(squad_xy, t, foes=(), **kw):
    x, y = squad_xy
    return world([U(10 + i, "hfoo", 0, x + 40 * i, y) for i in range(3)], foes, t=t, **kw)


class DontGoWhereTheirArmyIs(unittest.TestCase):
    # 敌方主力离农民 1500：在「守军」（800 内）之外，老规则照样把它当目标 —— 1-329 就是这样挑到了对方主基地
    ARMY = [U(600 + i, "ogru", 1, 6000 + 60 * i, -5500) for i in range(6)]

    def test_peons_next_to_the_enemy_army_are_not_targets(self):
        w = world(SQUAD, self.ARMY)
        self.assertIn(200, {p.addr for p in H.safe_peons(w, 7)})                       # 对照：不看军团时它是目标
        self.assertNotIn(200, {p.addr for p in H.safe_peons(w, 7, squad_str=6.0)})

    def test_nothing_is_sent_when_every_peon_is_next_to_their_army(self):
        army = self.ARMY
        m = manager()
        m.step(world(SQUAD, army), HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active, {})
        self.assertEqual(m.counts.get("没有安全的农民"), 1)


class KeepTheSquad(unittest.TestCase):
    def test_members_held_by_the_micro_layer_are_still_the_squad(self):
        m = manager()
        m.step(world(SQUAD), HOME, busy=False, town_threatened=False)
        self.assertEqual(sorted(m.active[1].addrs), [10, 11])                   # 09-23 AMAI 一队 2 个
        w2 = moved((5000, -3500), 320.0)
        out = m.step(w2, HOME, busy=False, town_threatened=False, excluded={10, 11, 12})   # 三个都在毫秒层手里
        self.assertIn(1, m.active, "活着的人被毫秒层拉着，不是「人没了」")
        self.assertEqual(out, [])
        self.assertEqual(m.counts.get("队员在毫秒层手里·等它放手"), 1)
        out = m.step(w2, HOME, busy=False, town_threatened=False, excluded={10})          # 放了两个：整队撤
        self.assertEqual(m.active[1].state, "flee")
        self.assertEqual((out[0].why, out[0].control), ("骚扰有人被拉去保命，整队撤退", "skirmish"))

    def test_flee_is_not_over_until_back_on_our_land(self):
        m = manager()
        m.step(world(SQUAD), HOME, busy=False, town_threatened=False)
        m.active[1].state = "flee"
        out = m.step(moved((5000, -3500), 320.0), HOME, busy=False, town_threatened=False)   # 对方地盘上，身边暂时没敌人
        self.assertIn(1, m.active)
        self.assertEqual((out[0].x, out[0].y, out[0].control), (HOME.x, HOME.y, "skirmish"))
        m.step(moved((600, 0), 340.0), HOME, busy=False, town_threatened=False)
        self.assertNotIn(1, m.active)
        self.assertEqual(m.counts.get("结束·逃回来了"), 1)


class HotSpots(unittest.TestCase):
    def test_pressed_near_the_target_goes_home_not_back(self):
        m = manager()
        m.step(world(SQUAD), HOME, busy=False, town_threatened=False)
        army = [U(600 + i, "ogru", 1, 5700, -3700 + 60 * i) for i in range(6)]
        m.step(moved((5400, -3700), 320.0, army), HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "kite")
        self.assertTrue(m.hot)
        out = m.step(moved((3500, -2500), 330.0), HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "return")
        self.assertEqual(out[0].control, "skirmish")
        # 90 秒内再派，那片的农民不是目标
        self.assertNotIn(200, {p.addr for p in H.safe_peons(moved((0, 0), 340.0), 7, squad_str=6.0, hot=m.hot)})

    def test_pressed_on_the_way_elsewhere_goes_back_to_work(self):
        m = manager()
        m.step(world(SQUAD), HOME, busy=False, town_threatened=False)
        army = [U(600 + i, "ogru", 1, 2300, -800 + 60 * i) for i in range(6)]        # 半路上（离矿区 4000）遇到的
        m.step(moved((2000, -1000), 320.0, army), HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "kite")
        out = m.step(moved((1200, -600), 330.0), HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "go")
        self.assertEqual(m.counts.get("拉扯·拉开了·回去接着骚扰"), 1)
        self.assertEqual(out[0].control, "hold")                                       # 走路令补攻击令抢不走


if __name__ == "__main__":
    unittest.main()

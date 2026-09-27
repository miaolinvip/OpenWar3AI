# -*- coding: utf-8 -*-
"""防塔攻（brain/towerrush.py + runtime.job_tactics 塔攻那段）。

09-22 晚 补上应对塔攻（TR）战术的策略。1-415（AMAI 中等兽族）：约 3.8 游戏分钟起苦工在我方主基地门外
下了 10 座箭塔 + 1 座巫毒商店，从离大厅 1700 推到 570，我们一座没拆掉（主力在 6000 外清野怪；回防「不进塔射程」
「敌人不在家门口不出去」），7.7 分钟认输。坐标是 1-415 交战帧里的实测值。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import towerrush as TRUSH  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HALL = (-5184, 2944)
SITES = [(-3520, 2624), (-3584, 2496), (-3456, 2496), (-3392, 2624)]      # 1-415 第一批 4 座
PEAS = [0x300 + i for i in range(8)]


def foundation(addr, x, y, hp=52.0):
    b = U(addr, "owtw", x, y, owner=1)
    b.hp = hp
    return b


def world(t=240.0, peons=4, sites=SITES, done=(), army=(), foe_army=(), extra=()):
    units = [U(1, "htow", *HALL), U(2, "hbar", HALL[0] + 500, HALL[1])]
    units += [U(a, "hpea", HALL[0] + 150 + 30 * i, HALL[1] - 200) for i, a in enumerate(PEAS)]
    units += [U(0x900 + i, "opeo", SITES[0][0] + 120, SITES[0][1] + 40 * i, owner=1, order=0x000D001C if i < 2 else 0)
              for i in range(peons)]
    units += [foundation(0x950 + i, x, y) for i, (x, y) in enumerate(sites)]
    units += [U(0x980 + i, "owtw", x, y, owner=1) for i, (x, y) in enumerate(done)]
    units += [U(0x800, "ogre", 4672, 2944, owner=1), U(0x801, "obar", 4200, 2800, owner=1)]
    units += list(army) + list(foe_army) + list(extra)
    return build_world(units, 0, t, 400, 300, 20, 30)


def footmen(n, x=-4200, y=2700):
    return [U(0x500 + i, "hfoo", x, y + 40 * i) for i in range(n)]


class Watch(unittest.TestCase):
    def step(self, w, crew=None):
        tr = TRUSH.TowerRushWatch()
        out = tr.step(w, crew if crew is not None else w.my_army + w.my_heroes)
        return tr, out

    def test_1415_builders_first(self):
        w = world(army=footmen(6))
        tr, out = self.step(w)
        self.assertTrue(tr.active)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, "attack")
        self.assertIn(out[0].target.addr, {0x900, 0x901}, "正在施工的苦工最先打")
        self.assertIn("苦工", out[0].why)

    def test_then_the_weakest_foundation(self):
        w = world(peons=0, army=footmen(6))
        w.enemy_buildings[0].hp  # noqa: B018  （只是确认有建筑）
        weakest = next(b for b in w.enemy_buildings if b.addr == 0x952)
        weakest.hp = 30.0
        tr, out = self.step(w)
        self.assertEqual(out[0].target.addr, 0x952, "苦工没了：血最少的地基")

    def test_foundation_under_a_finished_tower_needs_enough_force(self):
        done = [(SITES[0][0] + 150, SITES[0][1])]
        few = world(peons=0, sites=SITES[:1], done=done, army=footmen(1))
        tr, out = self.step(few)
        self.assertEqual(out, [], "一个步兵顶着盖好的箭塔去拆：不去，在塔外等")
        self.assertGreaterEqual(tr.counts.get("塔攻·目标都在塔射程里", 0), 1)
        many = world(peons=0, sites=SITES[:1], done=done, army=footmen(8) + [U(0x5A0, "Hamg", -4200, 2400)])
        tr2, out2 = self.step(many)
        self.assertEqual(out2[0].target.addr, 0x950)

    def test_not_a_tower_rush(self):
        self.assertIsNone(self.step(world(peons=1, sites=(), army=footmen(3)))[1], "一个苦工是侦察")
        self.assertIsNone(self.step(world(peons=0, sites=(), army=footmen(3)))[1], "对面的建筑都在它自己家")

    def test_clears_after_the_site_is_gone(self):
        tr = TRUSH.TowerRushWatch()
        tr.step(world(army=footmen(6)), footmen(6))
        clean = world(t=245.0, peons=0, sites=(), army=footmen(6))
        self.assertEqual(tr.step(clean, clean.my_army), [], "刚清完 5 秒：还在戒备（没有目标）")
        later = world(t=240.0 + TRUSH.TR_CLEAR_S + 1, peons=0, sites=(), army=footmen(6))
        self.assertIsNone(tr.step(later, later.my_army))
        self.assertFalse(tr.active)

    def test_militia_when_the_army_is_away(self):
        tr = TRUSH.TowerRushWatch()
        w = world(army=footmen(6, x=-4600, y=-2900))              # 主力在二矿清野怪（1-415 那一刻）
        tr.step(w, w.my_army)
        self.assertTrue(tr.wants_militia(w))
        home = world(army=footmen(9))
        tr.step(home, home.my_army)
        self.assertFalse(tr.wants_militia(home), "9 个兵已经在塔位边上：不停工")


class InBrain(unittest.TestCase):
    def run_tactics(self, w):
        sent = []
        d = tempfile.mkdtemp()
        b = Brain(FakeCmd(), None, d)
        b.set_race("human")
        b.config.values["enable_harass"] = False
        b.recovery.step = lambda *a, **k: None
        b._do = lambda it, t: sent.append(it)
        b._hero_idle_probe = lambda *args: None
        b._read_hurt = lambda: set()
        b._read_resting = lambda: set()
        b.job_tactics(w)
        return b, sent

    def test_1415_army_out_creeping_comes_back_and_peasants_turn_militia(self):
        w = world(army=footmen(6, x=-4600, y=-2900))
        b, sent = self.run_tactics(w)
        tr = [it for it in sent if it.why.startswith("防塔攻")]
        self.assertTrue(tr, [it.why for it in sent])
        self.assertEqual({u.addr for u in tr[0].units}, {0x500 + i for i in range(6)}, "全军都派过去")
        self.assertEqual(sorted(u for u, o in b.cmd.trains if o == A.ORDER_MILITIA), PEAS, "大厅边上的农民变民兵")
        self.assertEqual(b.counts.get("塔攻·发现"), 1)
        self.assertEqual(b.home_militia_seen, w.t, "开矿闸跟着停")


if __name__ == "__main__":
    unittest.main()

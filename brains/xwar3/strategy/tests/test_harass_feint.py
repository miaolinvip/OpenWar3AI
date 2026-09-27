# -*- coding: utf-8 -*-
"""骚扰小队 v2：塔按射程算、塔外等农民、敌兵压过来拉扯、离敌方主力远的那边、双线佯攻避开主力目标（harass._group_step 等）。

09-22 实战里几乎看不到骚扰；骚扰不以打不打得过为前提。可以分两队，一边佯攻、另一边主力进攻；
先判断哪边是敌军主力，遇到主力不硬拼、改为拉扯，也尽量不损失单位。
1-312 派出去 5 次，5 次都是出门 1~3 秒「骚扰没有安全目标，低损收队」：目标农民走回塔边（2200 内有塔就不算安全）。
"""
import math
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
EXPO = U(112, "ostr", 1, 6000, 0)
TOWER = U(113, "owtw", 1, 6000, 400)


def world(extra=(), t=300.0, peon_at=(6000, -1100), squad_at=None):
    mine = [HOME, U(2, "Hamg", 0, 0, 0)] + [U(10 + i, "hfoo", 0, 100 * i, 0) for i in range(9)]
    if squad_at is not None:
        mine = [HOME, U(2, "Hamg", 0, 0, 0)] + [U(10 + i, "hfoo", 0, squad_at[0] + 40 * i, squad_at[1]) for i in range(3)] \
            + [U(13 + i, "hfoo", 0, 100 * i, 0) for i in range(6)]
    enemy = [U(100, "ofrt", 1, 9000, 9000), EXPO, TOWER, U(111, "opeo", 1, *peon_at)]
    return W.build_world(mine + enemy + list(extra), 0, t, 500, 200, 40, 60, levels={2: 3})


def manager():
    m = H.HarassManager(groups_cfg=(C1,), rng=random.Random(0))
    m.rng.uniform = lambda a, b: 0.0
    return m


class Towers(unittest.TestCase):
    def test_peon_1500_from_a_tower_is_fair_game(self):
        w = world()                                                              # 农民离塔 1500（原来 2200 内一律不打）
        self.assertIn(111, {p.addr for p in H.safe_peons(w, 7)})

    def test_peon_under_the_tower_is_not(self):
        w = world(peon_at=(6000, 0))                                             # 离塔 400
        self.assertNotIn(111, {p.addr for p in H.safe_peons(w, 7)})


class Linger(unittest.TestCase):
    def test_peon_walks_under_the_tower_squad_waits_outside_instead_of_going_home(self):
        m = manager()
        w = world()
        m.step(w, HOME, busy=False, town_threatened=False)
        self.assertEqual(list(m.active), [1])
        w2 = world(peon_at=(6000, 200), squad_at=(4800, -1000), t=310.0)       # 农民回到塔边
        it = m.step(w2, HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "go")
        self.assertEqual(m.counts.get("塔外等农民"), 1)
        for i in it:
            self.assertIsNone(H.under_fire(i, H.armed(w2)))                    # 等的位置在塔的射程外
        w3 = world(peon_at=(6000, 200), squad_at=(4800, -1000), t=310.0 + H.HARASS_LINGER_S + 1)
        it3 = m.step(w3, HOME, busy=False, town_threatened=False)
        self.assertEqual((m.active[1].state, it3[0].x, it3[0].y), ("return", HOME.x, HOME.y))

    def test_standing_in_tower_range_steps_out(self):
        m = manager()
        m.step(world(), HOME, busy=False, town_threatened=False)
        w2 = world(squad_at=(5800, 0), t=305.0)                                  # 小队走进了塔的射程
        it = m.step(w2, HOME, busy=False, town_threatened=False)
        self.assertEqual(m.counts.get("避塔"), 1)
        self.assertTrue(it and all(H.under_fire(i, H.armed(w2)) is None for i in it))


class Kite(unittest.TestCase):
    def test_army_arrives_squad_pulls_toward_home_then_quits_after_the_time_cap(self):
        m = manager()
        m.step(world(), HOME, busy=False, town_threatened=False)
        army = [U(300 + i, "ogru", 1, 5200, -1000 + 60 * i) for i in range(6)]
        w2 = world(extra=army, squad_at=(4800, -1000), t=320.0)
        it = m.step(w2, HOME, busy=False, town_threatened=False)
        self.assertEqual(m.active[1].state, "kite")
        self.assertLess(math.hypot(it[0].x, it[0].y), math.hypot(4800, -1000))  # 往家那边
        w3 = world(squad_at=(3800, -800), t=300.0 + H.HARASS_MAX_S + 1)         # 拉开了，但一趟到时间了
        it3 = m.step(w3, HOME, busy=False, town_threatened=False)
        self.assertEqual((m.active[1].state, it3[0].why), ("return", "骚扰到时间了，收队"))

    def test_losing_a_man_goes_home(self):
        m = manager()
        m.step(world(), HOME, busy=False, town_threatened=False)
        w2 = world(squad_at=(4800, -1000), t=310.0)
        w2 = W.build_world([u for u in w2.units if u.addr not in (11, 12)], 0, 310.0, 500, 200, 40, 60)   # 三个死了两个
        it = m.step(w2, HOME, busy=False, town_threatened=False)
        self.assertEqual((m.active[1].state, it[0].why), ("flee", "骚扰掉了人，整队回家"))


MAIN = [U(100, "ofrt", 1, 9000, 9000)] + [U(130 + i, "obar", 1, 9000 + 300 * i, 8600) for i in range(3)]   # 建筑多 = 主基地


class TargetSide(unittest.TestCase):
    def test_goes_where_the_enemy_army_is_not(self):
        peons = [U(111, "opeo", 1, 6000, -1100), U(121, "opeo", 1, -1000, 7000)]
        halls = [EXPO, U(122, "ostr", 1, -1000, 8000)]
        base = [HOME] + peons + halls + MAIN
        w0 = W.build_world(base, 0, 300.0, 500, 200, 40, 60)
        self.assertEqual({h.addr for h in H._enemy_expansions(w0)}, {112, 122})   # 前提：两边都是分矿
        self.assertEqual(H.peon_target(w0, peons, HOME).addr, 111)                # 对照：没有敌军时挑近的
        army = [U(300 + i, "ogru", 1, 6500, -2500 + 60 * i) for i in range(6)]    # 敌方主力在东边那座分矿旁
        w = W.build_world(base + army, 0, 300.0, 500, 200, 40, 60)
        self.assertEqual(H.peon_target(w, peons, HOME).addr, 121)

    def test_feint_avoids_the_main_army_target(self):
        peons = [U(111, "opeo", 1, 6000, -1100), U(121, "opeo", 1, -1000, 7000)]
        halls = [EXPO, U(122, "ostr", 1, -1000, 8000)]
        w = W.build_world([HOME] + peons + halls + MAIN, 0, 300.0, 500, 200, 40, 60)
        self.assertEqual(H.peon_target(w, peons, HOME, avoid=halls[1]).addr, 111)     # 主力打西边那座 -> 佯攻东边
        self.assertEqual(H.peon_target(w, peons, HOME, avoid=EXPO).addr, 121)


class Busy(unittest.TestCase):
    """★★ 09-23 战术佯攻：小分队打一路，主队攻打另一路。

    1-432：主队整局没出门（busy 恒假）-> 双线一次没走；而单独骚扰 564 次里 319 次卡在「没有安全的农民」。
    佯攻成立的前提是：**正在和我方主队交火的守军，守不了另一头**。原来 safe_peons 把它们照样算成农民身边的守军。"""

    HALL = U(100, "ofrt", 1, 9000, 9000)                   # 对面唯一的基地（AMAI 基本不开矿）= 主队的目标
    PEON = U(111, "opeo", 1, 9600, 9600)                    # 它家矿上的苦工

    def scene(self, main_at):
        """主队在 main_at；5 个敌兵 (9300,9300) 守在苦工和来路上；小队从家 (0,0) 出发。"""
        guards = [U(200 + i, "ogru", 1, 9300, 9300 + 50 * i) for i in range(5)]
        mine = [HOME, U(2, "Hamg", 0, 0, 0)] + [U(10 + i, "hfoo", 0, 100 * i, 0) for i in range(3)]
        army = [U(50 + i, "hfoo", 0, main_at[0] + 60 * i, main_at[1]) for i in range(8)]
        return W.build_world(mine + army + [self.HALL, self.PEON] + guards, 0, 300.0, 500, 200, 45, 100, levels={2: 3})

    def test_guards_locked_on_our_main_force_do_not_count(self):
        w = self.scene((8400, 8400))                       # 主队已经打到它家门口，守军在和主队打
        self.assertFalse(H.safe_peons(w, 7, HOME), "前提：不扣掉被缠住的守军时，这个苦工不算安全")
        near = H.main_force([u for u in w.my_army if u.addr >= 50], w.me, w.levels)
        self.assertTrue(H.safe_peons(w, 7, HOME, busy_xy=(near.x, near.y)), "被主队缠住的守军不算守军")
        self.assertFalse(H.safe_peons(w, 7, HOME, busy_xy=(0.0, 0.0)), "主队还在家：谁也没缠住")

    def test_feint_squad_goes_out_while_the_main_force_is_in_contact(self):
        m = manager()
        m.rng.uniform = lambda a, b: 1.0
        w = self.scene((8400, 8400))
        it = m.step(w, HOME, busy=True, town_threatened=False, dual_target=self.HALL)
        self.assertEqual(m.counts.get("佯攻·主队已经缠住守军"), 1, m.counts)
        self.assertTrue(m.active and m.active[1].dual_line, m.counts)
        self.assertEqual([(x.kind, x.why) for x in it], [("move", "双线骚扰：走向农民")])

    def test_no_feint_while_the_main_force_is_still_on_the_way(self):
        m = manager()
        m.rng.uniform = lambda a, b: 1.0
        w = self.scene((2000, 2000))                       # 主队还在半路：守军没被缠住，别拿小队去送
        m.step(w, HOME, busy=True, town_threatened=False, dual_target=self.HALL)
        self.assertFalse(m.counts.get("佯攻·主队已经缠住守军"))
        self.assertEqual(m.counts.get("没有安全的农民"), 1, m.counts)


class Squads(unittest.TestCase):
    """★★ 09-23 骚扰时其余单位全部闲置，没有并行操作；只派一两个人骚扰效果差。
    改为一队或两队分路骚扰，主力同时照常行动。

    1-438 第 68~78 拍实测：2 个步兵在外面拉扯十拍，日志里主力一条命令都没有，最后被 27 强度的敌兵撵回来。
    主力闲着的时候，出去的那队按可用兵力放大；人够多再开第二路，两路的目标互相避开。"""

    def test_squad_size_follows_the_free_army(self):
        cfg = dict(C1)
        self.assertEqual(H.squad_size(cfg, 8, idle=False), 2, "主力在忙：还是 AMAI 的 2 人")
        self.assertEqual(H.squad_size(cfg, 8, idle=True), 2, "人少：带不动更多（最多带走一半）")
        self.assertEqual(H.squad_size(cfg, H.HARASS_BIG_ARMY, idle=True), H.HARASS_BIG_QTY)
        self.assertEqual(H.squad_size(cfg, H.HARASS_HUGE_ARMY, idle=True), H.HARASS_HUGE_QTY)
        self.assertLessEqual(H.squad_size(cfg, 40, idle=True), H.HARASS_HUGE_QTY, "再多也有上限")

    def base(self, n_foot, peons):
        mine = [HOME, U(2, "Hamg", 0, 0, 0)] + [U(10 + i, "hfoo", 0, 60 * i, 0) for i in range(n_foot)]
        enemy = [U(100, "ofrt", 1, 9000, 9000)] + list(peons)
        return W.build_world(mine + enemy, 0, 500.0, 500, 200, 60, 100, levels={2: 3})

    def test_idle_army_sends_a_real_squad_not_two_men(self):
        m = manager()
        m.rng.uniform = lambda a, b: 1.0
        w = self.base(20, [U(111, "opeo", 1, 6000, -1100)])
        it = m.step(w, HOME, busy=False, town_threatened=False, idle=True)
        self.assertEqual(len(it[0].units), H.HARASS_HUGE_QTY, m.counts)
        self.assertEqual(m.counts.get(f"骚扰·主力闲着·这队带 {H.HARASS_HUGE_QTY} 个"), 1, m.counts)

    def test_two_lines_go_to_different_places(self):
        m = H.HarassManager(groups_cfg=H.HUMAN_GROUPS, rng=random.Random(0))
        m.rng.uniform = lambda a, b: 1.0
        peons = [U(111, "opeo", 1, 6000, -1100), U(121, "opeo", 1, -1000, 7000)]   # 两头各一个
        w = self.base(20, peons)
        it = m.step(w, HOME, busy=False, town_threatened=False, idle=True)
        lines = [x for x in it if x.why == "骚扰：走向农民"]
        self.assertEqual(len(lines), 2, f"人够多要开两路：{m.counts}")
        self.assertEqual({x.target.addr for x in lines}, {111, 121}, "两路去不同的地方")
        self.assertFalse({u.addr for u in lines[0].units} & {u.addr for u in lines[1].units}, "两路不能是同一批人")

    def test_a_small_army_still_sends_only_one_line(self):
        m = H.HarassManager(groups_cfg=H.HUMAN_GROUPS, rng=random.Random(0))
        m.rng.uniform = lambda a, b: 1.0
        w = self.base(9, [U(111, "opeo", 1, 6000, -1100), U(121, "opeo", 1, -1000, 7000)])
        it = m.step(w, HOME, busy=False, town_threatened=False, idle=True)
        self.assertEqual(len([x for x in it if x.why == "骚扰：走向农民"]), 1, "9 个兵分两队各 2 个不是分路，是送两次")
        self.assertEqual(m.counts.get("人数不够"), 2, "第二路（硬门槛 12）和狮鹫组（没有狮鹫）各一次")


if __name__ == "__main__":
    unittest.main()

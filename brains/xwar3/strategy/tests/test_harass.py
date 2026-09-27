# -*- coding: utf-8 -*-
"""骚扰小队：派不派（和 test-amai.ps1 同一组判定）、全图挑安全的农民、一步步走/打/撤/离队。"""
import os
import random
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import harass as H, world as W  # noqa: E402
from brain import gamedata as G  # noqa: E402

C1 = H.HUMAN_GROUPS[0]
C4 = H.HUMAN_GROUPS[2]


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None, hp_max=None):
    m = hp_max if hp_max is not None else float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m)


class TestWant(unittest.TestCase):
    def test_same_as_powershell(self):
        self.assertEqual(H.want(C1, 9, "human", 300, -1e9, False, 5, 50), "go")
        self.assertEqual(H.want(C1, 9, "human", 300, -1e9, False, 5, 80), "没抽中")
        self.assertEqual(H.want(C1, 6, "human", 300, -1e9, False, 5, 1), "人数不够")
        self.assertEqual(H.want(C1, 9, "undead", 300, -1e9, False, 5, 1), "go")
        self.assertEqual(H.want(C1, 9, "human", 300, -1e9, True, 5, 1), "主力在打仗")
        self.assertEqual(H.want(C1, 9, "human", 200, -1e9, False, 5, 1), "还没到时间")
        self.assertEqual(H.want(C1, 9, "human", 300, 150, False, 5, 1), "冷却中")
        self.assertEqual(H.want(C4, 1, "human", 300, -1e9, False, 5, 1), "对手种族不派")
        self.assertEqual(H.want(C4, 1, "undead", 300, -1e9, False, 5, 1), "go")

    def test_step(self):
        self.assertEqual(H.step_action(2, 4, 4, False, 3, 5, 0.75, 0, True, 1000), "move")
        self.assertEqual(H.step_action(2, 4, 4, False, 3, 5, 0.75, 0, True, 500), "attack")
        self.assertEqual(H.step_action(2, 4, 4, False, 5, 5, 0.75, 0, True, 500), "flee")
        self.assertEqual(H.step_action(2, 4, 4, True, 0, 5, 0.75, 0, True, 500), "flee")
        self.assertEqual(H.step_action(2, 3, 4, False, 0, 5, 0.75, 0, True, 500), "flee")
        self.assertEqual(H.step_action(2, 4, 4, False, 0, 5, 0.75, 0, False, 0), "end")


class TestManager(unittest.TestCase):
    def base(self, extra=(), t=300.0, food=45):
        home = U(1, "htow", 0, 0, 0)
        feet = [U(10 + i, "hfoo", 0, 100 * i, 0) for i in range(9)]
        hero = U(2, "Hamg", 0, 0, 0, 500, 500)
        enemy = [U(100, "hcas", 1, 9000, 9000), U(101, "hgtw", 1, 9100, 9000),
                 U(110, "hpea", 1, 9000, 9200),          # 塔下的农民
                 U(111, "hpea", 1, 4000, 0), U(112, "htow", 1, 4200, 0)]   # 分矿农民，没塔
        return home, feet, W.build_world([home, hero] + feet + enemy + list(extra), 0, t, 500, 200, food, 100, levels={2: 3})

    def test_dispatch_to_safe_peon(self):
        home, feet, w = self.base()
        m = H.HarassManager(rng=random.Random(0))
        m.rng.uniform = lambda a, b: 1.0     # 必中
        m.step(w, home, busy=False, town_threatened=False)
        self.assertEqual(list(m.active), [1])
        self.assertEqual(m.active[1].addrs, [10, 11])   # 离家最近的两名混编地面兵（09-23 AMAI 一队 2 个）
        it = m.step(w, home, busy=False, town_threatened=False)
        self.assertEqual((it[0].kind, it[0].target.addr), ("move", 111))   # 塔下那个不选

    def test_kite_on_guard_then_go_back(self):
        home, feet, w = self.base()
        m = H.HarassManager()
        m.rng.uniform = lambda a, b: 1.0
        m.step(w, home, False, False)
        # 小队走到分矿农民旁，冒出 3 个步兵（6 >= 5）-> 撤
        moved = [U(10, "hfoo", 0, 3900, 0), U(11, "hfoo", 0, 3900, 0)]
        guards = [U(200 + i, "hfoo", 1, 4100, 0) for i in range(3)]
        w2 = W.build_world([home] + moved + feet[2:] + [U(111, "hpea", 1, 4000, 0), U(112, "htow", 1, 4200, 0)] + guards,
                           0, 310.0, 500, 200, 45, 100)
        it = m.step(w2, home, False, False)
        # 09-22 遇到敌方主力不硬拼、改为拉扯 —— 不再直接回家，往家的方向拉开
        self.assertEqual((m.active[1].state, it[0].kind, it[0].why[:4]), ("kite", "move", "骚扰拉扯"))
        self.assertLess(it[0].x, 3900)                                      # 往家（0,0）那边
        # 拉开了（敌兵不在 KITE_CLEAR_R 内）：09-22 起被压过来的那片记成危险区（HARASS_HOT_S），不回同一个矿区去送，收队
        #   （1-329 / 1-331：拉扯一次、拉开了又回同一片去，第二次没回来）。在别处被压的拉开后照旧回去，见 test_harass_feint
        w3 = W.build_world([home] + [U(10, "hfoo", 0, 200, 0), U(11, "hfoo", 0, 300, 0)] + feet[2:]
                           + [U(111, "hpea", 1, 4000, 0), U(112, "htow", 1, 4200, 0)], 0, 320.0, 500, 200, 45, 100)
        it3 = m.step(w3, home, False, False)
        self.assertEqual(m.active[1].state, "return")
        self.assertEqual(m.counts.get("拉扯·拉开了·那片有敌方主力·收队"), 1)
        self.assertIn("不回去送", it3[0].why)

    def test_hurt_leaves_and_town_threat_recalls(self):
        home, feet, w = self.base()
        m = H.HarassManager()
        m.rng.uniform = lambda a, b: 1.0
        m.step(w, home, False, False)
        hurt = [U(10, "hfoo", 0, 3000, 0, hp=90, hp_max=420), U(11, "hfoo", 0, 3000, 0)]
        w2 = W.build_world([home] + hurt + feet[2:] + [U(111, "hpea", 1, 4000, 0)], 0, 310.0, 500, 200, 45, 100)
        it = m.step(w2, home, False, False)
        self.assertEqual((m.active[1].state, it[0].why), ("flee", "骚扰出现伤员，整队低损撤退"))
        m.step(w2, home, False, town_threatened=True)
        self.assertEqual((m.active, m.counts.get("结束·家里受威胁")), ({}, 1))

    def test_dual_line_launches_only_with_main_reserve(self):
        home, _, w = self.base()
        target = next(h for h in w.enemy_halls if h.x < 5000)
        m = H.HarassManager(rng=random.Random(0))
        m.rng.uniform = lambda a, b: 1.0
        intents = m.step(w, home, busy=True, town_threatened=False, dual_target=target)
        self.assertTrue(m.active[1].dual_line)
        self.assertEqual((intents[0].kind, intents[0].why), ("move", "双线骚扰：走向农民"))
        self.assertEqual(m.counts.get("双线派出·hfoo"), 1)

        # 同样有七个步兵可拆，但没有英雄留下坐镇，不能把“人数够”误判成双线可行。
        m2 = H.HarassManager(rng=random.Random(0))
        m2.rng.uniform = lambda a, b: 1.0
        # 09-22 起「至少几个才派」数整支部队（harass.want army_n）；原来切成 my_army[:7] 会先挡在「人数不够」，测不到英雄这条
        weak = replace(w, my_heroes=[])
        m2.step(weak, home, busy=True, town_threatened=False, dual_target=target)
        self.assertFalse(m2.active)
        self.assertEqual(m2.counts.get("双线不拆·主力没有英雄"), 1)

    def test_dual_line_does_not_peel_frontline_in_contact(self):
        guard = U(300, "hfoo", 1, 350, 0)
        home, _, w = self.base(extra=[guard])
        target = next(h for h in w.enemy_halls if h.x < 5000)
        m = H.HarassManager()
        m.rng.uniform = lambda a, b: 1.0
        m.step(w, home, busy=True, town_threatened=False, dual_target=target)
        self.assertFalse(m.active)
        self.assertGreaterEqual(m.counts.get("人数不够", 0), 1)

    def test_main_retreat_recalls_dual_line_all_the_way_home(self):
        home, feet, w = self.base()
        target = next(h for h in w.enemy_halls if h.x < 5000)
        m = H.HarassManager()
        m.rng.uniform = lambda a, b: 1.0
        m.step(w, home, busy=True, town_threatened=False, dual_target=target)
        moved = [U(10, "hfoo", 0, 3000, 0), U(11, "hfoo", 0, 3100, 0)]
        w2 = W.build_world([home] + moved + feet[2:] + [U(111, "hpea", 1, 4000, 0), target],
                           0, 310.0, 500, 200, 45, 100)
        intents = m.step(w2, home, busy=True, town_threatened=False, recall=True)
        self.assertEqual((m.active[1].state, intents[0].kind, intents[0].why),
                         ("return", "move", "双线收队，回主基地重整"))

    def test_known_enemy_on_route_blocks_worker_target(self):
        guards = [U(300 + i, "hfoo", 1, 2000, i * 50) for i in range(3)]
        home, _, w = self.base(extra=guards)
        self.assertNotIn(111, {u.addr for u in H.safe_peons(w, 5, home)})

    def test_prefers_safe_workers_at_enemy_expansion(self):
        home, feet, w = self.base(extra=[U(120,"hpea",1,8500,8500)])
        m=H.HarassManager();m.rng.uniform=lambda a,b:1.0
        intents=m.step(w,home,False,False)
        self.assertEqual(intents[0].target.addr,111)

    def test_amai_solo_paladin_harass_is_disabled(self):
        home = U(1, "htow", 0, 0, 0)
        pal = U(2, "Hpal", 0, 100, 0)
        enemy = [U(100, "unpl", 1, 4000, 0), U(101, "uaco", 1, 4100, 0)]
        w = W.build_world([home, pal] + enemy, 0, 300.0, 500, 200, 20, 40, levels={2: 3})
        m = H.HarassManager(groups_cfg=(C4,))
        m.rng.uniform = lambda a, b: 0.0
        self.assertEqual(m.step(w, home, busy=False, town_threatened=False), [])
        self.assertFalse(m.active)
        self.assertEqual(m.counts.get("不派英雄单独骚扰"), 1)


if __name__ == "__main__":
    unittest.main()

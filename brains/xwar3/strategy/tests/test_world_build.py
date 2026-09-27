# -*- coding: utf-8 -*-
"""世界状态（军团聚类、朝城镇来的满威胁、分类）+ 声明式建造（钱不够就停、研究不挡、前置跳过、优先级老化）+ 任务调度 + 人族表。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import amai, world as W  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain.build import Planner, Want  # noqa: E402
from brain.jobs import Scheduler  # noqa: E402
from brain.tables import human  # noqa: E402


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None, hp_max=None):
    m = hp_max if hp_max is not None else float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m)


class TestArmies(unittest.TestCase):
    def test_cluster(self):
        us = [U(1, "hfoo", 1, 0, 0), U(2, "hfoo", 1, 1000, 0), U(3, "hfoo", 1, 2400, 0),   # 一轮内重心固定：2400 离新重心 500 也有 1900 > 1500，自成一团
              U(4, "hfoo", 1, 9000, 0), U(5, "hpea", 1, 100, 0), U(6, "hbar", 1, 50, 0)]
        armies = W.cluster_armies(us, 1)
        self.assertEqual(sorted(len(a.units) for a in armies), [1, 1, 2])
        big = max(armies, key=lambda a: len(a.units))
        self.assertEqual((big.strength, big.x), (4, 500))

    def test_heading_full_threat(self):
        # 敌军在 (3000,0)、上一拍在 (5000,0)：每拍走 2000，朝 (0,0) 来 -> 距离 3000 < 移动量? 否（3000 > 2000）-> 按预测位置 (-3000,0) 距 3000 与现在 3000 取小 -> 3000 > 2000 不算
        a = W.Army(1, [], 3000, 0, 10, dx=-2000, dy=0)
        self.assertEqual(W.army_town_dist(a, 0, 0), 3000)
        # 在 (1500,0)、每拍走 2000 朝家 -> 现在距离 1500 < 2000 且方向夹角 0 -> 满威胁距离 1000
        b = W.Army(1, [], 1500, 0, 10, dx=-2000, dy=0)
        self.assertEqual(W.army_town_dist(b, 0, 0), 1000)
        # 在 (1500,0) 但往外走 -> 取 min(max(预测 7500,1000), 1500) = 1500
        c = W.Army(1, [], 1500, 0, 10, dx=2000, dy=0)
        self.assertEqual(W.army_town_dist(c, 0, 0), 1500)

    def test_world(self):
        us = [U(1, "htow", 0, 0, 0), U(2, "hpea", 0, 100, 0), U(3, "hfoo", 0, 200, 0), U(4, "Hamg", 0, 200, 0, 500, 500),
              U(10, "unpl", 1, 8000, 8000), U(11, "ugho", 1, 700, 0), U(12, "ugho", 1, 800, 0), U(20, "nwlg", 12, 3000, 3000),
              U(30, "ngol", 15, 500, 500)]
        w = W.build_world(us, 0, 400.0, 500, 200, 10, 20, levels={4: 2})
        self.assertEqual([u.addr for u in w.my_army], [3])
        self.assertEqual([u.addr for u in w.my_heroes], [4])
        self.assertEqual(w.foe_race, "undead")
        self.assertEqual(len(w.enemy_armies), 1)
        home = [t for t in w.towns if t.owner == 0][0]
        # 两只食尸鬼 (2+2)//2=2 各 2，重心 750 -> 540×4/1000^0.8
        self.assertAlmostEqual(home.threat, 540 * 4 / 1000 ** 0.8, places=6)
        self.assertEqual(w.own_strength, 2 + 4)      # 步兵 2 + 大法师 2 级 4
        self.assertEqual(w.aggression, amai.aggression(10, 1, "undead", 1))
        self.assertEqual(len(w.mines), 1)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.p = Planner()

    def _plan(self, wants, have=None, gold=1000, lumber=1000, food=50, block=None):
        have = have or {}
        block = block or {}
        return self.p.plan(wants, lambda w: have.get(w.code, 0), gold, lumber, food, lambda w: block.get(w.code))

    def test_stop_when_poor(self):
        wants = [Want("unit", "hfoo", 2, 65), Want("building", "halt", 1, 96), Want("unit", "hrif", 1, 55)]
        r = self._plan(wants, gold=200, lumber=100)
        self.assertEqual([d.want.code for d in r.decisions], ["halt"])      # 祭坛 180 金；步兵 135 买不起了 -> 停，火枪手不看
        self.assertEqual(r.stopped_at.code, "hfoo")

    def test_upgrade_does_not_block(self):
        wants = [Want("upgrade", "Rhde", 1, 90), Want("unit", "hfoo", 1, 60)]
        r = self.p.plan(wants, lambda w: 0, 140, 100, 10, lambda w: None,
                        cost=lambda w: G.upgrade_cost(w.code) if w.kind == "upgrade" else G.cost(w.code))
        self.assertEqual([d.want.code for d in r.decisions], ["hfoo"])     # 防御姿态 150 金买不起，但不挡后面的步兵
        self.assertIsNone(r.stopped_at)

    def test_cannot_build_skips(self):
        wants = [Want("unit", "hrif", 1, 80), Want("unit", "hfoo", 1, 60)]
        r = self._plan(wants, block={"hrif": "缺铁匠铺"})
        self.assertEqual([d.want.code for d in r.decisions], ["hfoo"])
        self.assertEqual(r.skipped[0][1], "缺铁匠铺")

    def test_have_enough_and_money_deducted(self):
        wants = [Want("unit", "hfoo", 3, 70), Want("unit", "hrif", 1, 60), Want("unit", "hpea", 1, 50)]
        r = self._plan(wants, have={"hfoo": 3}, gold=280, lumber=30)
        self.assertEqual([d.want.code for d in r.decisions], ["hrif", "hpea"])   # 205+75 = 280 刚好
        r = self._plan(wants, have={"hfoo": 3}, gold=279, lumber=30)
        self.assertEqual([d.want.code for d in r.decisions], ["hrif"])
        self.assertEqual(r.stopped_at.code, "hpea")

    def test_food(self):
        r = self._plan([Want("unit", "hkni", 1, 80), Want("unit", "hfoo", 1, 60)], food=3)
        self.assertEqual([d.want.code for d in r.decisions], ["hfoo"])          # 骑士 4 人口放不下，跳过

    def test_aging(self):
        a, b = Want("unit", "hfoo", 5, 60), Want("unit", "hrif", 5, 62)
        for _ in range(3):
            self._plan([a, b], block={"hrif": "x", "hfoo": "x"})                 # 都造不了，各自老化 3
        r = self._plan([a, b], gold=205, lumber=30, block={"hrif": "x"})
        self.assertEqual(self.p.aging[a.key], 4)
        self.assertEqual([d.want.code for d in r.decisions], ["hfoo"])


class TestJobs(unittest.TestCase):
    def test_schedule_and_errors(self):
        s = Scheduler()
        calls = []
        s.add("a", 2.0, lambda w, b: calls.append(("a", w.t)))
        s.add("boom", 1.0, lambda w, b: 1 / 0)
        for t in (0.0, 1.0, 2.0, 3.5):
            s.run_due(SimpleNamespace(t=t))
        self.assertEqual(calls, [("a", 0.0), ("a", 2.0)])
        self.assertEqual(s.jobs["boom"].errors, 4)
        self.assertIn("ZeroDivisionError", s.jobs["boom"].last_error)


class TestHumanTable(unittest.TestCase):
    def test_opening_priorities(self):
        us = [U(1, "htow", 0, 0, 0)] + [U(10 + i, "hpea", 0, 100, 0) for i in range(5)]
        w = W.build_world(us, 0, 5.0, 500, 150, 7, 12)       # 剩 5 <= 6 + 2×0 -> 补房子
        wants = human.wants(w)
        top = sorted(wants, key=lambda x: -x.prio)[:4]
        self.assertEqual([x.code for x in top], ["halt", "Hamg", "hhou", "hbar"])
        w2 = W.build_world(us, 0, 5.0, 500, 150, 5, 12)      # 剩 7 > 6 -> 还不补
        self.assertNotIn("hhou", [x.code for x in human.wants(w2)])
        # 已有一座在建的房子（血没满）：要的是「建成的 0 + 1 = 1」，在建那座已经满足，不会再要第二座
        building = U(50, "hhou", 0, 300, 300, hp=120, hp_max=500)
        w3 = W.build_world(us + [building], 0, 20.0, 500, 150, 7, 12)
        farm = [x for x in human.wants(w3) if x.code == "hhou"][0]
        self.assertEqual(farm.qty, 1)
        self.assertIn("hkee", [x.code for x in wants if x.kind == "upgrade_hall"])

    def test_farms_fill_the_gap(self):
        # 09-17 实机：人口 54/54，4 座出兵建筑 -> 门槛 6 + 8 = 14，缺口 14 - 0 + 1 = 15 -> 3 座；建成 5 座 + 停工 1 座 -> 要 5 + 3 = 8
        us = [U(1, "hcas", 0, 0, 0)] + [U(2 + i, c, 0, 600, i * 400) for i, c in enumerate(("hbar", "hars", "harm", "hgra"))] \
            + [U(20 + i, "hhou", 0, -600, i * 200) for i in range(5)] + [U(30, "hhou", 0, -800, 0, hp=54, hp_max=500)]
        w = W.build_world(us, 0, 1500.0, 8881, 3000, 54, 54)
        wants = human.wants(w)
        self.assertEqual([x.qty for x in wants if x.code == "hhou"], [8])
        self.assertIn(("expand", 2), [(x.kind, x.qty) for x in wants if x.note == "钱多了开矿"])   # 金子 >= 1500：一座大厅 + 1

    def test_expand_before_army(self):
        us = [U(1, "htow", 0, 0, 0), U(2, "hbar", 0, 600, 0)] + [U(10 + i, "hpea", 0, 100, 0) for i in range(13)]
        # 09-23 一度改成 8 个兵 / 8 分钟又改回 4 个兵 / 6 分钟（两矿是对 AMAI 一矿全押兵的长处，见 human.EXPAND_FORCE_S）
        feet = [U(40 + i, "hfoo", 0, 300, 300) for i in range(4)]
        exp = lambda w: [(x.qty, x.prio) for x in human.wants(w) if x.kind == "expand"]
        self.assertEqual(exp(W.build_world(us + feet[:3], 0, 300.0, 200, 900, 20, 30)), [(2, 53)])            # 3 个兵、5 分钟：照原表 53
        # 4 个兵：插到步兵 65 前面；09-21 起 83 —— AMAI expa_prio 200 在英雄 80 之上（能这么排是因为野怪没清/农民没造满时 runtime 跳过它）
        self.assertEqual(exp(W.build_world(us + feet, 0, 300.0, 200, 900, 20, 30)), [(2, 53), (2, 83)])
        self.assertEqual(exp(W.build_world(us + feet[:1], 0, human.EXPAND_FORCE_S, 200, 900, 20, 30)), [(2, 53), (2, 83)])   # 6 分钟
        # ★ 09-21 农民目标换成 AMAI PeonBuilder（照 AMAI 的方式）。原来钉的是旧公式
        #   「每矿 5 + 伐木人数」（木 399/400 -> 10/7）。现在：一座矿、这一轮想开第二座（表里有 expand 2）->
        #   按**下一座矿**的数量先造（pendingExpansionPeons）。期望用生产代码那一份公式**算**，不手写。
        # ★ 09-21 晚：再按「采金满员 + 按木头库存要的伐木 + 建造工」封顶（tables.peon_need，木头溢出那条）。
        from brain.tables import peon_need
        peas = lambda lum: max(x.qty for x in human.wants(W.build_world(us, 0, 300.0, 200, lum, 20, 30)) if x.code == "hpea")
        for lum in (0, 399, 400, 1500):
            wl = W.build_world(us, 0, 300.0, 200, lum, 20, 30)
            self.assertEqual(peas(lum), max(peon_need(wl, "human", 1), peon_need(wl, "human", 2)), f"木 {lum}")
        self.assertGreater(peas(399), 10, "想开第二座矿时按两座矿的数量先造")

    def test_peasants_capped_by_what_the_mines_and_wood_need(self):
        """按实际需要的资源量决定造多少农民采集。近 30 局 28 局结束时木头 >= 1000、金子全程一两百。
        封顶 = 5 × 在采的矿 + 伐木（木 <200:5、<400:4、<700:3、>=700:2）+ 1 个建造工 —— 期望按这个式子算。"""
        from brain.tables import peon_need, wood_crew_need
        from brain.amai_strategy import data as D
        d = D.AmaiData("human")
        for mines, lum, cap in ((1, 0, 11), (1, 1000, 8), (2, 1000, 13), (2, 300, 15), (3, 2500, 18)):
            w = SimpleNamespace(lumber=lum)
            self.assertEqual(5 * mines + wood_crew_need(lum) + 1, cap)
            self.assertEqual(peon_need(w, "human", mines), min(D.peon_target(d, mines, lum), cap), (mines, lum))
        self.assertLess(peon_need(SimpleNamespace(lumber=1000), "human", 2), D.peon_target(d, 2, 1000))

    def test_peasants_outrank_every_combat_unit_like_amai(self):
        """AMAI common.eai:357：farm_prio 400 > peon_prio 280 > expa_prio 200 > BuildSequence 的英雄 80 / 兵 ≤65。
        4-030 实机 3:00~8:30 农民卡在 12 个（AMAI 目标 17）：「补满农民」原来是 60，比步兵 65 低，钱先被兵花掉。
        人族、兽族三个科技等级都查：补满农民要排在所有兵、非首英雄前面，只让给人口房子 / 祭坛 / 首英雄。"""
        from brain.tables import orc
        cases = (
            (human, "hpea", [U(1, "htow", 0, 0, 0), U(2, "hbar", 0, 600, 0), U(3, "halt", 0, 300, 0)]),
            (human, "hpea", [U(1, "hkee", 0, 0, 0), U(2, "hbar", 0, 600, 0), U(3, "halt", 0, 300, 0)]),
            (human, "hpea", [U(1, "hcas", 0, 0, 0), U(2, "hbar", 0, 600, 0), U(3, "halt", 0, 300, 0)]),
            (orc, "opeo", [U(1, "ogre", 0, 0, 0), U(2, "obar", 0, 600, 0), U(3, "oalt", 0, 300, 0)]),
            (orc, "opeo", [U(1, "ostr", 0, 0, 0), U(2, "obar", 0, 600, 0), U(3, "oalt", 0, 300, 0)]),
        )
        for table, peon, base in cases:
            us = base + [U(10 + i, peon, 0, 100, 0) for i in range(12)]
            wants = table.wants(W.build_world(us, 0, 400.0, 200, 300, 20, 60))
            fill = [x.prio for x in wants if x.code == peon and "PeonBuilder" in x.note]
            units = [x.prio for x in wants if x.kind == "unit" and x.code != peon]
            later_heroes = [x.prio for x in wants if x.kind == "hero" and x.note != "首英雄"]
            self.assertTrue(fill and units, (table.__name__, base[0].type))
            self.assertGreater(fill[0], max(units + later_heroes), (table.__name__, base[0].type))

    def test_tier2_jumps_ahead_once_enough_army(self):
        # 09-18 晚：兵 >= tier2_army（4）时升二本排到出兵（65）和第二矿插队（66）前面；原表 42 排在所有兵后面，二本中位 10 游戏分钟
        us = [U(1, "htow", 0, 0, 0), U(2, "hbar", 0, 600, 0), U(3, "halt", 0, 300, 0)] + [U(10 + i, "hpea", 0, 100, 0) for i in range(13)]
        feet = [U(40 + i, "hfoo", 0, 300, 0) for i in range(4)]
        keep = lambda units: [x.prio for x in human.wants(W.build_world(units, 0, 300.0, 200, 300, 20, 30)) if x.code == "hkee"]
        self.assertEqual(keep(us + feet[:3]), [42])
        self.assertEqual(keep(us + feet), [67])

    def test_tier2_has_second_hero_and_castle(self):
        us = [U(1, "hkee", 0, 0, 0), U(2, "halt", 0, 300, 0), U(3, "hbar", 0, 600, 0)]
        w = W.build_world(us, 0, 600.0, 500, 150, 30, 60)
        codes = {(x.kind, x.code) for x in human.wants(w)}
        # ⚠ 09-21 二英雄从山丘换成血法师（PROFILE["heroes"][1]，理由见 tables/human.py）。
        #   这里按表取，不写死英雄代码 —— 否则每次换英雄都要改一次测试，而它要守的是
        #   「二本会想要**第二个**英雄」，不是「一定是山丘」。
        self.assertIn(("hero", human.PROFILE["heroes"][1]), codes)
        self.assertIn(("upgrade_hall", "hcas"), codes)
        self.assertNotIn(("upgrade_hall", "hkee"), codes)

    def test_tier2_researches_backpack_after_vault_finishes(self):
        us = [U(1, "hkee", 0, 0, 0), U(2, "hvlt", 0, 300, 0), U(3, "hbar", 0, 600, 0)]
        w = W.build_world(us, 0, 600.0, 500, 150, 30, 60)
        wants = human.wants(w)
        self.assertIn(("upgrade", "Rhpm"), {(x.kind, x.code) for x in wants})


if __name__ == "__main__":
    unittest.main()

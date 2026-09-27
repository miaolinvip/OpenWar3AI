# -*- coding: utf-8 -*-
"""英雄行动评分表（brain/heroplan.py）。

09-21 设计原则：英雄是全局优先级最高的单位。259 局数据同向：败局每局英雄阵亡 4.17 次、胜局 1.33 次；
英雄一死 -> 攒钱复活 -> 出击频次砍半（attack_move 11.9/分 -> 6.1/分）-> 被拆家（败局 59% vs 胜局 7%）-> 停在 2 矿。

这里测的是**评分表这个结构**该有的性质，不是复述某一组权重：
  ① 没有理由时不许乱跑（满血无敌人 -> 作战）；
  ② 有理由时按理由的轻重排序（残血 < 快死了 < 被围且无援）；
  ③ 硬约束用 -1e6 表达，不会被一堆小分数累加压过去；
  ④ 队伍在场时英雄跟队走 —— 不能和部队各跑一个方向（这是老架构"两层抢同一个单位"换个地方复发）。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import heroplan as HP  # noqa: E402
from brain import tactics as T  # noqa: E402

HOME = SimpleNamespace(x=0.0, y=0.0)


def hero(hp=1.0, x=3000.0, y=0.0):
    return SimpleNamespace(addr=2, type="Hamg", x=x, y=y, hp=100.0 * hp, hp_max=100.0)


def scene(h, foes=0, allies=0, creeps=0, foe_d=400.0, ally_d=300.0, threat=0.0):
    fs = [SimpleNamespace(addr=600 + i, type="hfoo", owner=1, x=h.x + foe_d, y=h.y + 40.0 * i, hp=100.0, hp_max=100.0)
          for i in range(foes)]
    al = [SimpleNamespace(addr=20 + i, type="hfoo", owner=0, x=h.x + ally_d, y=h.y + 40.0 * i, hp=100.0, hp_max=100.0)
          for i in range(allies)]
    cr = [SimpleNamespace(addr=100 + i, type="nogm", owner=12, x=h.x + foe_d, y=h.y + 40.0 * i, hp=100.0, hp_max=100.0)
          for i in range(creeps)]
    towns = [SimpleNamespace(owner=0, threat=threat)]
    return SimpleNamespace(t=600.0, me=0, my_heroes=[h], my_army=al, enemy_fighters=fs, creeps=cr, towns=towns)


def decide(h, w, **kw):
    return HP.HeroPlanner().decide(w, HOME, h, **{"active": True, **kw})


class NoReasonNoRun(unittest.TestCase):
    def test_healthy_and_safe_keeps_fighting(self):
        h = hero()
        d = decide(h, scene(h))
        self.assertEqual(d.action, HP.FIGHT, d.reasons)
        # 硬约束：满血无敌人时脱离/回家必须是不可行，而不是"分数低一点"
        self.assertLessEqual(d.scores[HP.WITHDRAW], HP.IMPOSSIBLE / 2)
        self.assertLessEqual(d.scores[HP.HOME], HP.IMPOSSIBLE / 2)

    def test_already_home_never_orders_home_again(self):
        """已经在家门口还下回家令 = 原地不动白占一拍，而且会盖掉别的意图。"""
        h = hero(hp=0.2, x=300.0)
        d = decide(h, scene(h, foes=1))
        self.assertNotEqual(d.action, HP.HOME)
        self.assertLessEqual(d.scores[HP.HOME], HP.IMPOSSIBLE / 2)


class StaysInTeamFights(unittest.TestCase):
    """09-21 进攻对方主基地或二矿时，英雄常常离战场很远不上前，血魔法师还有一半左右血就退缩。
    要求被打时再往后躲，没人打就上。出击中英雄 40%~70% 血被判脱离/回家约 310 次。"""

    def decide_in_attack(self, hp, allies):
        h = hero(hp=hp)
        return decide(h, scene(h, foes=4, allies=allies, foe_d=400.0, ally_d=300.0), offensive=True)

    def test_half_hp_hero_stays_with_the_team(self):
        d = self.decide_in_attack(0.55, allies=4)
        self.assertIn(d.action, (HP.FIGHT, HP.BACKLINE), d.reasons)

    def test_hurt_hero_casts_from_the_backline(self):
        d = self.decide_in_attack(0.42, allies=4)
        self.assertEqual(d.action, HP.BACKLINE, d.reasons)

    def test_critical_hero_still_leaves(self):
        d = self.decide_in_attack(0.30, allies=4)
        self.assertIn(d.action, (HP.HOME, HP.WITHDRAW), d.reasons)

    def test_without_a_team_fight_recover_first_as_before(self):
        d = self.decide_in_attack(0.55, allies=0)
        self.assertIn(d.action, (HP.HOME, HP.WITHDRAW), d.reasons)


class EnemyWipedOut(unittest.TestCase):
    """09-22 明显优势时大法师和血法师却回家了（1-287 18:30）。
    18:22「英雄残血先回家」：大法师 68%、敌方强度 3.0 对我方 44.4、身边没敌兵 ——「血不满，进攻中先恢复」在拆建筑时也生效。"""

    def w(self, h, foe_s, own_s=44.4, foes=0):
        s = scene(h, foes=foes, foe_d=400.0)
        s.foe_strength, s.own_strength = foe_s, own_s
        return s

    def test_68pct_hero_keeps_pushing_when_the_enemy_is_gone(self):
        h = hero(hp=0.68, x=6000.0)
        d = decide(h, self.w(h, foe_s=3.0), offensive=True)
        self.assertIn(d.action, (HP.FIGHT, HP.BACKLINE), d.reasons)

    def test_hurt_hero_backs_off_but_does_not_go_home(self):
        h = hero(hp=0.40, x=6000.0)
        d = decide(h, self.w(h, foe_s=3.0), offensive=True)
        self.assertEqual(d.action, HP.BACKLINE, d.reasons)

    def test_still_recovers_when_the_enemy_army_is_alive(self):
        h = hero(hp=0.68, x=6000.0)
        d = decide(h, self.w(h, foe_s=30.0), offensive=True)
        self.assertIn(d.action, (HP.HOME, HP.WITHDRAW), d.reasons)

    def test_enemy_next_to_the_hero_is_not_won(self):
        h = hero(hp=0.40, x=6000.0)
        d = decide(h, self.w(h, foe_s=3.0, foes=1), offensive=True)
        self.assertIn(d.action, (HP.HOME, HP.WITHDRAW), d.reasons)

    def test_dying_hero_still_leaves(self):
        h = hero(hp=0.25, x=6000.0)
        d = decide(h, self.w(h, foe_s=3.0), offensive=True)
        self.assertIn(d.action, (HP.HOME, HP.WITHDRAW), d.reasons)


class NoPacingAtHome(unittest.TestCase):
    def test_hurt_hero_near_home_settles_instead_of_pacing(self):
        """2-032 30 分钟：大法师 125~186/700 每 2 秒在「回家 -> 大厅」和「脱离 -> 离家 1600 的安全点」之间翻。
        驱动多拍：每拍把英雄挪到它这一拍的目标点，从家外 1600 开始走 12 拍 —— 进了家就不能再被推出 HOME_NEAR。"""
        h = hero(hp=0.2, x=-1600.0, y=0.0)
        path = []
        for _ in range(12):
            w = scene(h, foes=1, foe_d=-900.0)          # 敌人在它和家之间的另一侧（家在 x=0，敌人在它左边 900）
            d = decide(h, w)
            if d.action in (HP.HOME, HP.WITHDRAW):
                h.x, h.y = d.x, d.y
            path.append((d.action, round(math.hypot(h.x, h.y))))
        inside = [i for i, (_a, dist) in enumerate(path) if dist <= HP.HOME_NEAR]
        self.assertTrue(inside, path)
        self.assertTrue(all(dist <= HP.HOME_NEAR for _a, dist in path[inside[0]:]), path)

    def test_withdraw_point_at_home_stays_behind_the_hall(self):
        h = hero(hp=0.2, x=300.0)
        d = decide(h, scene(h, foes=1))                 # 敌人在它右边 400（离家更远那侧）
        self.assertEqual(d.action, HP.WITHDRAW)
        self.assertLessEqual(math.hypot(d.x, d.y), HP.HOME_REST_R + 1e-6)
        self.assertLess(d.x, h.x)                         # 背着敌人退


class ReasonsAreOrdered(unittest.TestCase):
    def test_worse_situation_pushes_further_from_fight(self):
        """越危险越该走：作战得分必须单调下降，而且不能出现"更危险反而更想打"。"""
        mild = decide(hero(hp=0.8), scene(hero(hp=0.8), foes=1))
        hurt = decide(hero(hp=0.45), scene(hero(hp=0.45), foes=1))
        dying = decide(hero(hp=0.2), scene(hero(hp=0.2), foes=1))
        self.assertGreater(mild.scores[HP.FIGHT], hurt.scores[HP.FIGHT])
        self.assertGreater(hurt.scores[HP.FIGHT], dying.scores[HP.FIGHT])
        self.assertEqual(mild.action, HP.FIGHT)
        self.assertIn(hurt.action, (HP.HOME, HP.WITHDRAW))
        self.assertEqual(dying.action, HP.HOME)

    def test_dying_hero_goes_home_not_just_backline(self):
        h = hero(hp=0.15)
        self.assertEqual(decide(h, scene(h, foes=3)).action, HP.HOME)

    def test_town_under_attack_pulls_home(self):
        """家在挨打时回家是有意义的，这时不该选"脱离到附近"。"""
        h = hero(hp=0.45)
        d = decide(h, scene(h, foes=1, threat=40.0))
        self.assertEqual(d.action, HP.HOME)

    def test_far_from_home_prefers_local_withdraw(self):
        """离家很远时回家几乎只有机会成本（败局 home 占 57%、胜局 33%）—— 优先脱离到附近。"""
        h = hero(hp=0.45, x=9000.0)
        d = decide(h, scene(h, foes=2))
        self.assertEqual(d.action, HP.WITHDRAW)
        self.assertGreater(d.scores[HP.WITHDRAW], d.scores[HP.HOME])
        # 脱离点要背着敌人，不能往敌人那边退
        self.assertLess(d.x, h.x)


class StaysWithTheArmy(unittest.TestCase):
    def test_swarm_with_escort_does_not_split_the_army(self):
        """全队都在场时被一群人围住是部队层面的决策；英雄再单独往另一个方向跑 = 把队伍拆两半。"""
        h = hero()
        d = decide(h, scene(h, foes=8, allies=5))
        self.assertEqual(d.action, HP.FIGHT, d.reasons)

    def test_swarm_alone_withdraws(self):
        h = hero()
        d = decide(h, scene(h, foes=8, allies=0))
        self.assertEqual(d.action, HP.WITHDRAW, d.reasons)

    def test_distant_reinforcements_count_as_escort(self):
        """援兵正在赶来也算有队伍 —— 只数贴身的人会让英雄先跑，狮鹫扑空。"""
        h = hero()
        near = scene(h, foes=8, allies=0)
        near.my_army = [SimpleNamespace(addr=30 + i, type="hgry", owner=0, x=h.x + 3000.0, y=100.0 * i,
                                        hp=100.0, hp_max=100.0) for i in range(6)]
        self.assertEqual(decide(h, near).action, HP.FIGHT)

    def test_allies_fighting_keeps_healthy_hero_in(self):
        """队友正在打、自己还健康 -> 不许跑（英雄跑了这仗就崩）。"""
        h = hero(hp=0.8)
        d = decide(h, scene(h, foes=4, allies=3, foe_d=300.0, ally_d=200.0))
        self.assertEqual(d.action, HP.FIGHT)


class MicroLayerHandoff(unittest.TestCase):
    def test_micro_held_hero_is_not_sent_back(self):
        """毫秒层正在救它时，战略层同一拍不能把它派回去（老架构里租约一到期就来回抖）。"""
        h = hero(hp=0.8)
        d = decide(h, scene(h, foes=2), micro_held=True)
        self.assertIn(d.action, (HP.WITHDRAW, HP.HOME))


class Observable(unittest.TestCase):
    def test_every_decision_reports_scores(self):
        """"为什么没撤"必须能从日志一眼回答 —— 这是换掉 if 链的主要理由。"""
        h = hero(hp=0.45)
        p = HP.HeroPlanner()
        w = scene(h, foes=3)
        out = p.plan(w, HOME, active=True)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].reasons)
        self.assertRegex(out[0].top3, r"[a-z]+[+-]\d+")
        self.assertEqual(p.counts[f"英雄·{out[0].action}"], 1)
        self.assertTrue(any("->" in n for n in p.notes))

    def test_note_only_on_action_change(self):
        """同一个动作不要每拍刷一条 —— 日志会被淹掉。"""
        h = hero(hp=0.45)
        p = HP.HeroPlanner()
        w = scene(h, foes=3)
        p.plan(w, HOME, active=True)
        n1 = len(p.notes)
        p.plan(w, HOME, active=True)
        self.assertEqual(len(p.notes), n1)


class LiveThresholds(unittest.TestCase):
    def test_offensive_flag_gates_the_not_ready_rule(self):
        """打野打到一半血不满不回家（原行为）；正规进攻中才先恢复。
        两个标志合成一个会让打野一路往返 —— MATCH_001_009 记的"回城空转"。"""
        h = hero(hp=T.HERO_READY_HP - 0.05)
        w = scene(h)
        self.assertEqual(decide(h, w, active=True, offensive=False).action, HP.BACKLINE)
        self.assertIn(decide(h, w, active=True, offensive=True).action, (HP.HOME, HP.WITHDRAW))


if __name__ == "__main__":
    unittest.main()

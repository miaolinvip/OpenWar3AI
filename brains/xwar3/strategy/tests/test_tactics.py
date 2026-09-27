# -*- coding: utf-8 -*-
"""出击循环 / 守家 / 打野挑营地：驱动几步看状态和意图（期望按 AMAI 公式手算，写在断言旁边）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import amai, tactics as T, world as W  # noqa: E402
from brain import gamedata as G  # noqa: E402

PROFILE = dict(min_attack_strength=4)


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None, hp_max=None):
    m = hp_max if hp_max is not None else float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m)


def world(units, food=50, t=600.0, levels=None):
    return W.build_world(units, 0, t, 500, 300, food, 100, levels=levels or {})


def idle_only(intents) -> bool:
    """09-22 晚起「在家、没仗可打」不再一条令都不下：只有空档安排（tactics._idle_plan）的前沿站位 / 提前部署。"""
    return all("前沿" in (i.why or "") or "电脑队长" in (i.why or "") for i in intents)


class TestTargets(unittest.TestCase):
    def test_expansion_first(self):
        foes = [U(100, "hcas", 1, 10000, 10000), U(101, "hbar", 1, 10200, 10000), U(102, "hhou", 1, 9800, 10000),
                U(103, "htow", 1, 4000, 0)] + [U(110 + i, "hfoo", 1, 10000, 9500) for i in range(5)]     # 对面 5 步兵 = 10
        mine = [U(1, "htow", 0, 0, 0), U(2, "Hamg", 0, 0, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 100, 0) for i in range(4)]
        w = world(mine + foes, food=45, levels={2: 3})
        tgt, kind, ev = T.choose_target(w, SimpleNamespace(x=0, y=0))
        # 分矿附近没有兵，远处主力赶不上；不能把全图的 10 强度直接塞进门槛。
        self.assertEqual((tgt.addr, kind), (103, "分矿"))
        self.assertEqual((ev.local, ev.response), (0.0, 0.0))
        self.assertLess(ev.lhs, ev.rhs)

    def test_new_attack_reserve_can_reject_barely_good_target(self):
        foes = [U(100, "hcas", 1, 10000, 10000), U(101, "hbar", 1, 10200, 10000),
                U(102, "hhou", 1, 9800, 10000), U(103, "htow", 1, 4000, 0)] \
               + [U(110 + i, "hfoo", 1, 10000, 9500) for i in range(5)]
        mine = [U(1, "htow", 0, 0, 0), U(2, "Hamg", 0, 0, 0, 500, 500)] \
               + [U(10 + i, "hfoo", 0, 100, 0) for i in range(4)]
        w = world(mine + foes, food=45, levels={2: 3})
        tgt, _kind, ev = T.choose_target(w, SimpleNamespace(x=0, y=0))
        self.assertEqual(tgt.addr, 103)
        reserve = ev.rhs / ev.lhs + 0.01
        tgt, _kind, _ev = T.choose_target(w, SimpleNamespace(x=0, y=0), reserve=reserve)
        self.assertIsNone(tgt)

    def test_towers_make_main_bad(self):
        foes = [U(100, "hcas", 1, 10000, 0), U(101, "hbar", 1, 10200, 0)] + [U(120 + i, "hgtw", 1, 10000, 300) for i in range(4)] \
            + [U(110 + i, "hfoo", 1, 10000, 0) for i in range(5)]
        mine = [U(1, "htow", 0, 0, 0), U(2, "Hamg", 0, 0, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 100, 0) for i in range(4)]
        w = world(mine + foes, food=45, levels={2: 3})
        tgt, kind, ev = T.choose_target(w, SimpleNamespace(x=0, y=0))
        # 当地步兵强度 10，加上无攻城时塔强度 48，仍然不能去。
        self.assertIsNone(tgt)
        self.assertEqual(kind, "主基地")
        self.assertEqual((ev.local, ev.tower), (10.0, 48.0))
        self.assertGreater(ev.lhs, ev.rhs)

    def test_main_base_rejects_the_failed_match_reserve_band(self):
        # 069~082 失败局出击余量中位 1.18；这个场景纸面可打但不够主基地那一档，不接。
        # ⚠ 09-21：这里原来钉的是 `> 1.10`，那是**旧的 ATTACK_MARGIN=1.25 下**的数。
        #   margin 现在由 winprob 从校准过的胜率推出来（1.38），同一支部队算出来的 rhs/lhs 自然更小。
        #   要守的不是那个数，是「纸面过得去、但够不到主基地那一档」这个**位置**，所以按活的常量比。
        foes = [U(100, "hcas", 1, 10000, 0), U(101, "hbar", 1, 10200, 0)] \
               + [U(200 + i, "hfoo", 1, 10000, 300) for i in range(4)]
        mine = [U(1, "htow"), U(2, "Hamg", 0, 0, 0)] \
               + [U(10 + i, "hfoo", 0, 100, 0) for i in range(3)]
        w = world(mine + foes, food=45, levels={2: 3})
        ev = T.offense.assess(w, w.my_army + w.my_heroes, SimpleNamespace(x=0, y=0),
                              w.enemy_halls[0], T.ATTACK_MARGIN)
        self.assertGreater(ev.rhs / ev.lhs, 1.0)
        self.assertLess(ev.rhs / ev.lhs, T.ATTACK_MAIN_RESERVE)
        self.assertIsNone(T.choose_target(w, SimpleNamespace(x=0, y=0))[0])

    def test_main_base_rejects_new_failure_band_but_keeps_real_window(self):
        # 086/088/089 以 1.22~1.33 出发后都被长途行军期间的新产兵/复活英雄翻盘；这一档必须拒绝。
        # ⚠ 09-21 同上：原来钉的 1.30 是旧 margin 下的读数，改按活的常量比位置。
        foes = [U(100, "hcas", 1, 10000, 0), U(101, "hbar", 1, 10200, 0)] \
               + [U(200 + i, "hfoo", 1, 10000, 300) for i in range(7)]
        mine = [U(1, "htow"), U(2, "Hamg")] \
               + [U(10 + i, "hfoo", 0, 100, 0) for i in range(8)]
        w = world(mine + foes, food=45, levels={2: 3})
        ev = T.offense.assess(w, w.my_army + w.my_heroes, SimpleNamespace(x=0, y=0),
                              w.enemy_halls[0], T.ATTACK_MARGIN)
        self.assertGreater(ev.rhs / ev.lhs, 1.0)
        self.assertLess(ev.rhs / ev.lhs, T.ATTACK_MAIN_RESERVE)
        self.assertIsNone(T.choose_target(w, SimpleNamespace(x=0, y=0))[0])

        mine.append(U(99, "hfoo", 0, 100, 0))
        w = world(mine + foes, food=45, levels={2: 3})
        ev2 = T.offense.assess(w, w.my_army + w.my_heroes, SimpleNamespace(x=0, y=0),
                               w.enemy_halls[0], T.ATTACK_MARGIN)
        self.assertGreater(ev2.rhs / ev2.lhs, T.ATTACK_MAIN_RESERVE)
        self.assertIsNotNone(T.choose_target(w, SimpleNamespace(x=0, y=0))[0])


class TestAttackSequence(unittest.TestCase):
    def setUp(self):
        self.home = U(1, "htow", 0, 0, 0)
        self.hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        self.feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        self.exp = U(103, "htow", 1, 5000, 0)
        self.main = [U(100, "hcas", 1, 12000, 0), U(101, "hbar", 1, 12200, 0)]

    def test_attack_then_flee(self):
        seq = T.AttackSequence(PROFILE)
        w = world([self.home, self.hero] + self.feet + [self.exp] + self.main, food=45, levels={2: 3})
        it = seq.step(w, self.home)
        self.assertEqual((seq.state, it[0].kind, it[0].target.addr), ("attack", "attack_move", 103))
        # 走到分矿边上，撞见 12 个骑士（48） > 我方 6×2+5=17 × 逃跑倍数（进攻性 9 -> 2+ (9+15)/60 = 2.4）= 40.8 -> 撤
        army = [U(2, "Hamg", 0, 4800, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 4800, 0) for i in range(6)]
        knights = [U(200 + i, "hkni", 1, 5000, 100) for i in range(12)]
        w2 = world([self.home] + army + [self.exp] + self.main + knights, food=45, levels={2: 3})
        it2 = seq.step(w2, self.home)
        self.assertEqual((seq.skirmish.phase, it2[0].kind), ("withdraw", "move"))
        self.assertEqual(it2[0].control, "skirmish")

    def test_target_dead_search_then_chain(self):
        seq = T.AttackSequence(PROFILE)
        w = world([self.home, self.hero] + self.feet + [self.exp] + self.main, food=45, levels={2: 3})
        seq.step(w, self.home)
        # 分矿拆掉了，附近还有 1 个农民 -> 附近接着打
        army = [U(2, "Hamg", 0, 5000, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 5000, 0) for i in range(6)]
        peon = U(300, "hpea", 1, 5300, 0)
        it = seq.step(world([self.home] + army + [peon] + self.main, food=45, levels={2: 3}), self.home)
        self.assertEqual((it[0].target.addr, it[0].why), (300, "目标没了，附近接着打"))
        # 农民也没了、兵力没掉 -> 接下一个目标（主基地：无塔，10000... 对面没兵 -> 0 + 7000/1400=5 <= 17+9）
        it = seq.step(world([self.home] + army + self.main, food=45, levels={2: 3}), self.home)
        self.assertEqual((seq.state, it[0].target.addr, seq.counts.get("出击·接下一个目标")), ("attack", 100, 1))

    def test_creep_when_not_ready(self):
        # 人口 10、英雄 1 级、只有一个英雄 -> 出击时机不到；我方强度 = 大法师 1 级 3 + 步兵 2 = 5，营地上限 5 × 0.6 = 3
        base = [self.home, self.hero, self.feet[0]] + self.main
        seq = T.AttackSequence(PROFILE)
        it = seq.step(world(base + [U(400, "nwlt", 12, 2000, 2000)], food=10, levels={2: 1}), self.home)
        self.assertEqual((seq.state, it[0].kind), ("creep", "move"))              # 未到场先纯移动到安全点，不能攻击移动开怪
        seq = T.AttackSequence(PROFILE)
        it = seq.step(world(base + [U(400, "nwlg", 12, 2000, 2000), U(401, "nwlg", 12, 2100, 2000)], food=10, levels={2: 1}), self.home)
        self.assertEqual((seq.state, idle_only(it)), ("home", True))              # 两只 4 级狼 = 8 > 3 -> 不去（没仗可打去前沿）


class TestLiveFixes(unittest.TestCase):
    """09-17 实机 5 号第二局照出来的四条：集结、援兵、撤回冷却、打野认准营地。"""

    def setUp(self):
        self.home = U(1, "htow", 0, 0, 0)
        self.exp = U(103, "htow", 1, 5000, 0)
        self.main = [U(100, "hcas", 1, 12000, 0), U(101, "hbar", 1, 12200, 0)]

    def test_gather_then_split_reinforcements(self):
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        here = [U(10 + i, "hfoo", 0, 100, 0) for i in range(3)]
        away = [U(20 + i, "hfoo", 0, 4000, 3000) for i in range(3)]
        guards = [U(300 + i, "hfoo", 1, 12000, 300) for i in range(3)]   # 对面还有 6 > 17 × 0.25，要汇合（兵力打没了就不汇合）
        self.main = self.main + guards
        seq = T.AttackSequence(PROFILE)
        w = world([self.home, hero] + here + away + [self.exp] + self.main, food=45, levels={2: 3})
        it = seq.step(w, self.home)
        # 有明确目标后，整队先沿目标方向前推到锁定的前沿集结点，不在基地重心原地等。
        self.assertEqual(seq.state, "gather")
        gx, gy = seq.gather_xy
        self.assertGreater(gx, 1000)
        self.assertGreaterEqual(self.exp.x - gx, T.GATHER_STANDOFF - 1)
        self.assertEqual([(i.kind, len(i.units), round(i.x), round(i.y), i.why) for i in it],
                         [("move", 7, round(gx), round(gy), "向下一目标前沿集结")])
        for u in [hero] + here:
            u.x, u.y = gx, gy
        # 40 游戏秒还没凑齐 -> 出发；离主力 2000 外的 3 个先去汇合
        it = seq.step(world([self.home, hero] + here + away + [self.exp] + self.main, food=45, t=640.0, levels={2: 3}), self.home)
        self.assertEqual(seq.state, "attack")
        # ★ 09-18：行军时大法师（远程英雄）拆出来跟在主力后面（_backline），所以攻击移动是 3 个步兵 + 1 个英雄两条
        self.assertEqual([(i.kind, len(i.units)) for i in it], [("attack_move", 3), ("attack_move", 1), ("move", 3)])
        self.assertEqual((it[0].target.addr, seq.counts.get("集结·等满出发")), (103, 1))
        # 对面兵打没了：不分汇合，全军直接去（英雄照样跟在后面）
        it = seq.step(world([self.home, hero] + here + away + [self.exp] + self.main[:2], food=45, t=642.0, levels={2: 3}), self.home)
        self.assertEqual([(i.kind, len(i.units)) for i in it], [("attack_move", 6), ("attack_move", 1)])

    def test_gather_does_not_loosen_main_base_to_barely_even(self):
        foes = self.main + [U(200 + i, "hfoo", 1, 12000, 300) for i in range(4)]
        army = [U(2, "Hamg", 0, 100, 0)] + [U(10 + i, "hfoo", 0, 100, 0) for i in range(3)]
        w = world([self.home] + army + foes, food=45, t=650.0, levels={2: 3})
        seq = T.AttackSequence(PROFILE)
        seq.state, seq.gather_at, seq.home_xy = "gather", 600.0, (0.0, 0.0)
        seq._set_target(w.enemy_halls[0], "主基地")
        cx, cy = T.centroid(army)
        out = seq._gather(w, army, T.main_force(army, 0, w.levels), cx, cy,
                          T.amai.strength(army, w.levels))
        self.assertEqual(seq.state, "retreat")
        self.assertEqual(out[0].why, "集结超时且到场兵力不足，重整")

    def test_gather_rejects_main_base_at_new_failure_reserve(self):
        foes = self.main + [U(200 + i, "hfoo", 1, 12000, 300) for i in range(5)]
        army = [U(2, "Hamg", 0, 100, 0)] + [U(10 + i, "hfoo", 0, 100, 0) for i in range(5)]
        w = world([self.home] + army + foes, food=45, t=650.0, levels={2: 3})
        seq = T.AttackSequence(PROFILE)
        seq.state, seq.gather_at, seq.home_xy = "gather", 600.0, (0.0, 0.0)
        seq._set_target(w.enemy_halls[0], "主基地")
        ev = seq._assess(w, army, SimpleNamespace(x=100, y=0))
        # ⚠ 09-21：原来钉的 1.20 是旧 ATTACK_MARGIN=1.25 下的读数。margin 现在从校准过的胜率推，
        #   同一支部队的 rhs/lhs 自然更小；要守的是「纸面过得去、但够不到集结复核那一档」这个位置。
        self.assertGreater(ev.rhs / ev.lhs, 1.0)
        self.assertLess(ev.rhs / ev.lhs, T.GATHER_MAIN_COMMIT_RESERVE)
        cx, cy = T.centroid(army)
        out = seq._gather(w, army, T.main_force(army, 0, w.levels), cx, cy,
                          T.amai.strength(army, w.levels))
        self.assertEqual(seq.state, "retreat")
        self.assertEqual(out[0].why, "集结超时且到场兵力不足，重整")

    def test_main_force_sticks_to_front(self):
        # 两团差不多强（8 对 10，8 >= 10 × 0.8）：有目标时取离目标近的那团；没目标取最强
        front = [U(10 + i, "hfoo", 0, 4000, 0) for i in range(4)]
        back = [U(20 + i, "hfoo", 0, 0, 0) for i in range(5)]
        target = SimpleNamespace(x=5000, y=0)
        self.assertEqual(round(T.main_force(front + back, 0, {}, target).x), 4000)
        self.assertEqual(round(T.main_force(front + back, 0, {}).x), 0)
        self.assertEqual(round(T.main_force(front[:3] + back, 0, {}, target).x), 0)    # 6 < 8：前面那团太弱，不算

    def test_retreat_cooldown(self):
        seq = T.AttackSequence(PROFILE)
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        seq.step(world([self.home, hero] + feet + [self.exp] + self.main, food=45, levels={2: 3}), self.home)
        army = [U(2, "Hamg", 0, 4800, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 4800, 0) for i in range(6)]
        knights = [U(200 + i, "hkni", 1, 5000, 100) for i in range(12)]
        seq.step(world([self.home] + army + [self.exp] + self.main + knights, food=45, levels={2: 3}), self.home)
        self.assertEqual(seq.skirmish.phase, "withdraw")           # t=600 先分段撤，保留反打机会
        it = seq.step(world([self.home, hero] + feet + [self.exp] + self.main, food=45, t=620.0, levels={2: 3}), self.home)
        self.assertEqual(seq.state, "retreat")                     # 已回家，结束局部接战任务
        it = seq.step(world([self.home, hero] + feet + [self.exp] + self.main, food=45, t=621.0, levels={2: 3}), self.home)
        self.assertEqual((seq.state, idle_only(it), seq.counts.get("出击·撤回来冷却中")), ("home", True, 1))
        it = seq.step(world([self.home, hero] + feet + [self.exp] + self.main, food=45, t=680.0, levels={2: 3}), self.home)
        self.assertEqual((seq.state, it[0].kind), ("attack", "attack_move"))

    def test_no_halls_left_keep_razing(self):
        # 09-17 实机拍 137：对面主基地拆了、没有大厅，剩兵营和农场 -> 不回家，接着拆（对面兵力 0 <= 我方 1/4，不看剩 70%）
        seq = T.AttackSequence(PROFILE)
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        seq.step(world([self.home, hero] + feet + self.main, food=45, levels={2: 3}), self.home)
        self.assertEqual((seq.state, seq.target.addr), ("attack", 100))
        army = [U(2, "Hamg", 0, 11800, 0, 100, 500)] + [U(10 + i, "hfoo", 0, 11800, 0) for i in range(2)]   # 掉到出发时一半以下
        farms = [U(500, "hhou", 1, 14000, 2000), U(501, "hhou", 1, 14200, 2000)]
        left = [U(101, "hbar", 1, 12200, 0)] + farms
        it = seq.step(world([self.home] + army + left, food=45, levels={2: 3}), self.home)
        # 大法师 100/500 = 20% < 35% -> 先单独离开（回家或就近脱离）；其余照拆
        # ★ 09-22：「血不满先恢复」只在集结时给之后，离家 11800 的快死英雄按评分表就近脱离（W_FAR_HOME：横穿地图不划算）
        self.assertIn(it[0].why, ("英雄残血先回家", "英雄脱离到附近安全点"))
        self.assertEqual([u.addr for u in it[0].units], [2])
        self.assertEqual((it[1].target.addr, it[1].why), (101, "目标没了，附近接着打"))
        seq.search_left = 0
        it = seq.step(world([self.home] + army + farms, food=45, levels={2: 3}), self.home)
        self.assertEqual((seq.state, it[1].target.addr, it[1].why), ("attack", 500, "接着打建筑"))
        # 家里挂机那种情况：在家、对面没大厅没兵、剩农场 -> 出击目标是建筑
        tgt, kind, ev = T.choose_target(world([self.home, hero] + feet + farms, food=45, levels={2: 3}), self.home)
        self.assertEqual((tgt.addr, kind), (500, "建筑"))

    def test_creep_distance_rule(self):
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        base = [self.home, hero, U(10, "hfoo", 0, 100, 0)] + self.main
        for x, ok in ((8000, False), (6500, False), (-3000, True)):
            w = world(base + [U(400, "nwlt", 12, x, 0)], food=10, levels={2: 1})
            camp = T.pick_creep_camp(w, self.home)
            # 8000：离家超过 7000；6500：离对面主基地 5500 比离家近；-3000：可以
            self.assertEqual(camp is not None, ok, x)

    def test_creep_commit_then_abort(self):
        seq = T.AttackSequence(PROFILE)
        wolf = U(400, "nwlt", 12, 2000, 2000)
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        foot = U(10, "hfoo", 0, 100, 0)
        it = seq.step(world([self.home, hero, foot, wolf] + self.main, food=10, levels={2: 1}), self.home)
        self.assertEqual((seq.state, it[0].kind), ("creep", "move"))           # 灰狼可打，但未到场先去安全点
        # 掉血：大法师 1.5 + 步兵 1.0 = 2.5，按 0.6 挑已经不够（1.5 < 2），但营地剩 2 <= 主力 2.5 -> 接着打
        hurt = [U(2, "Hamg", 0, 1900, 1900, 250, 500), U(10, "hfoo", 0, 1900, 1900, 210, 420)]
        it = seq.step(world([self.home] + hurt + [wolf] + self.main, food=10, levels={2: 1}), self.home)
        # 主力 (1900,1900) 离营地 141 < 900 -> 直接点名打野怪（夜里睡着的野怪攻击移动不打）
        self.assertEqual((seq.state, it[0].kind, round(it[0].x), it[0].target.addr), ("creep", "attack", 2000, 400))
        # 再掉：0.6 + 1.0 = 1.6 < 2 -> 回家
        worse = [U(2, "Hamg", 0, 1900, 1900, 100, 500), U(10, "hfoo", 0, 1900, 1900, 210, 420)]
        it = seq.step(world([self.home] + worse + [wolf] + self.main, food=10, levels={2: 1}), self.home)
        self.assertEqual((seq.state, it[0].kind, seq.counts.get("打野·打不动回家")), ("retreat", "move", 1))

    def test_creep_cleared(self):
        seq = T.AttackSequence(PROFILE)
        hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        foot = U(10, "hfoo", 0, 100, 0)
        seq.step(world([self.home, hero, foot, U(400, "nwlt", 12, 2000, 2000)] + self.main, food=10, levels={2: 1}), self.home)
        it = seq.step(world([self.home, hero, foot] + self.main, food=10, levels={2: 1}), self.home)
        self.assertEqual((seq.state, idle_only(it), seq.counts.get("打野·营地清空")), ("home", True, 1))


class TestIteration0918(unittest.TestCase):
    """09-18 复盘四条：清分矿野怪、护送开矿、出兵要明显占优、英雄残血先回家。"""

    def setUp(self):
        self.home = U(1, "htow", 0, 0, 0)
        self.hero = U(2, "Hamg", 0, 200, 0, 500, 500)
        self.main = [U(100, "hcas", 1, 12000, 0), U(101, "hbar", 1, 12200, 0)]

    def test_clear_expansion_camp(self):
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(7)]
        guards = [U(400 + i, "nwlg", 12, -4000, -3000) for i in range(3)]          # 3 只 4 级狼 = 12
        base = [self.home, self.hero] + feet + self.main + guards
        seq = T.AttackSequence(PROFILE)
        seq.clear_target = dict(ids={400, 401, 402}, x=-4200, y=-3200, strength=12)
        # 战力比（总血 × 每秒伤害，09-18 二）：1 级大法师 + 7 步兵 对 3 只巨狼 = 3.94 >= CLEAR_POWER -> 去清
        it = seq.step(world(base, food=20, levels={2: 1}), self.home)
        # ★ 09-18 起清分矿野怪也先到集合点、到场够强才开打（test_engage_gate），说明文字保留「清分矿野怪」
        # ⚠ 09-21：门槛改由 winprob 推之后 CREEP_DIRECT_POWER 从 4.0 降到 3.11，3.94 这一档现在算
        #   「轻松营地」，走的是同一个动作（move 到安全集结点）但换了一句说明。动作没变，只是理由更准了。
        self.assertEqual((seq.state, it[0].kind), ("creep", "move"))
        self.assertIn(it[0].why, ("清分矿野怪（强度 12）·全军很强但尚未到场，先去安全集结点",
                                  "清分矿野怪（强度 12）·去集合点（未成团不开怪）"))
        seq2 = T.AttackSequence(PROFILE)
        seq2.clear_target = dict(ids={400, 401, 402}, x=-4200, y=-3200, strength=12)
        it = seq2.step(world([self.home, self.hero] + feet[:4] + self.main + guards, food=20, levels={2: 1}), self.home)
        # 1 级大法师 + 4 步兵：战力比 1.65 < CLEAR_POWER 1.81 -> 兵力不够，不去
        self.assertEqual((seq2.state, idle_only(it), seq2.counts.get("清分矿·兵力不够")), ("home", True, 1))

    def test_escort_only_when_threatened(self):
        # 09-18 对局画面：部队站在二矿落点上，农民「无法在这里建造」，也不去打别的野。没威胁不护送、护送站在落点外
        seq = T.AttackSequence(PROFILE)
        seq.escort = (-4352, -2624)
        base = [self.home, self.hero, U(10, "hfoo", 0, 100, 0)] + self.main
        seq.step(world(base, food=10, levels={2: 1}), self.home)
        self.assertEqual(seq.counts.get("护送开矿·没威胁接着打野"), 1)
        self.assertIsNone(seq.counts.get("护送开矿"))
        foes = [U(300 + i, "hfoo", 1, -4352, -1024) for i in range(2)]            # 落点北边 1600
        it = seq.step(world(base + foes, food=10, levels={2: 1}), self.home)
        self.assertEqual((it[0].kind, round(it[0].x), round(it[0].y)), ("attack_move", -4352, -2624 + 700))
        self.assertIn("护送开矿", it[0].why)

    def test_idle_units_clear_build_spot(self):
        seq = T.AttackSequence(PROFILE)
        seq.allow_attack = False
        seq.build_spot = (-4352, -2624)
        on = [U(10 + i, "hfoo", 0, -4352 + 100 * i, -2624) for i in range(3)]
        it = seq.step(world([self.home, self.hero] + on + self.main, food=10, levels={2: 1}), self.home)
        mv = [i for i in it if i.why == "让开分矿落点"]
        self.assertEqual([u.addr for u in mv[0].units], [10, 11, 12])
        self.assertGreaterEqual(((mv[0].x + 4352) ** 2 + (mv[0].y + 2624) ** 2) ** 0.5, T.SPOT_CLEAR_TO - 1)

    def test_attack_needs_clear_advantage(self):
        """分矿守军加上能及时赶来的援军仍然强于我方，不能为了出击而送。"""
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        exp = U(103, "htow", 1, 5000, 0)
        guards = [U(200 + i, "hfoo", 1, 5100, 100) for i in range(6)]              # 当地守军 12 > 我方 17 / 1.6 -> 偷不了
        foes = [U(300 + i, "hfoo", 1, 12000, 300) for i in range(7)]               # 敌方主力 14，全图 26
        seq = T.AttackSequence(PROFILE)
        seq.step(world([self.home, self.hero] + feet + [exp] + guards + self.main + foes, food=45, levels={2: 3}), self.home)
        self.assertEqual((seq.state, seq.counts.get("出击·目标不好")), ("home", 1))

    def test_attacks_when_clearly_stronger(self):
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]                    # 我方 5 + 12 = 17
        foes = [U(300 + i, "hfoo", 1, 12000, 300) for i in range(4)]               # 敌方 8（没有分矿可偷）
        seq = T.AttackSequence(PROFILE)
        seq.step(world([self.home, self.hero] + feet + self.main + foes, food=45, levels={2: 3}), self.home)
        self.assertEqual((seq.state, seq.counts.get("出击·目标好·主基地")), ("attack", 1))

    def test_loot_does_not_hold_army_after_camp_cleared(self):
        seq = T.AttackSequence(PROFILE)
        foot = U(10, "hfoo", 0, 1900, 1900)
        hero = U(2, "Hamg", 0, 1950, 1900, 500, 500)
        seq.step(world([self.home, hero, foot, U(400, "nwlt", 12, 2000, 2000)] + self.main, food=10, levels={2: 1}, t=600.0), self.home)
        seq.ground = [(2050, 2020)]                                                  # 灰狼掉的东西
        it = seq.step(world([self.home, hero, foot] + self.main, food=10, levels={2: 1}, t=602.0), self.home)
        self.assertNotEqual(seq.state, "loot")
        self.assertEqual(seq.counts.get("打野·掉落交给捡拾模块"), 1)
        self.assertFalse(it == [] and seq.state == "loot")

    def test_hurt_hero_flees_enemy_squad_any_state(self):
        # 09-18 野外遇到敌军小分队，丝血英雄也不跑 —— 在家状态（护送/集结路上）也要跑
        seq = T.AttackSequence(PROFILE)
        hero = U(2, "Hamg", 0, 3000, 0, 100, 500)                                    # 20%，离家 3000
        squad = [U(300 + i, "hfoo", 1, 3800, 0) for i in range(3)]                   # 800 外的敌方小分队
        it = seq.step(world([self.home, hero, U(10, "hfoo", 0, 3000, 0)] + self.main + squad, food=10, levels={2: 1}), self.home)
        self.assertEqual((it[0].why, [u.addr for u in it[0].units]), ("英雄残血先回家", [2]))

    def test_healthy_army_attacks_while_hero_recovers(self):
        feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        exp = U(103, "htow", 1, 5000, 0)
        hero = U(2, "Hamg", 0, 200, 0, 250, 500)                                     # 50% < 60%，在家（不触发残血回家）
        seq = T.AttackSequence(PROFILE)
        it = seq.step(world([self.home, hero] + feet + [exp] + self.main, food=45, levels={2: 3}), self.home)
        self.assertEqual(seq.state, "attack")
        self.assertEqual({u.addr for i in it if i.kind == "attack_move" for u in i.units}, {u.addr for u in feet})
        self.assertTrue(any(i.kind == "move" and hero in i.units for i in it))

    def test_lone_reinforcement_waits_at_forward_rally(self):
        seq = T.AttackSequence(PROFILE)
        seq.home_xy = (0, 0)
        army = [U(10 + i, "hfoo", 0, 8000, 0) for i in range(5)] + [U(30, "hfoo", 0, 1000, 0)]   # 主力在 8000，单个援兵在 1000
        it = seq._push(army, 8000, 0, 9000, 0, None, "打分矿")
        self.assertEqual([(i.kind, len(i.units), i.why) for i in it],
                         [("attack_move", 5, "打分矿"), ("move", 1, "援兵太少太远，去前沿集结点攒人")])
        self.assertEqual((it[1].x, it[1].y), (8000, 0))

    def test_all_heroes_march_behind_nonhero_frontline(self):
        seq = T.AttackSequence(PROFILE)
        heroes = [U(2, "Hamg", 0, 0, 0), U(3, "Hmkg", 0, 0, 0)]
        front = [U(10+i, "hfoo", 0, 0, 0) for i in range(3)]
        target = U(103, "htow", 1, 5000, 0)
        seq.hero_ids = {u.addr for u in heroes}
        out = seq._backline([T.Intent("attack_move", front+heroes, 5000, 0, target, "打分矿")])
        self.assertEqual([{u.addr for u in i.units} for i in out],
                         [{u.addr for u in front}, {u.addr for u in heroes}])
        self.assertLess(out[1].x, 0)       # 英雄站在前排背向目标的一侧

    def test_abandoned_creep_camp_uses_local_escape_point(self):
        seq = T.AttackSequence(PROFILE)
        seq.home_xy = (0, 0)
        army = [U(10+i, "hfoo", 0, 4000, i*50) for i in range(4)]
        creeps = [U(700+i, "nwlg", 12, 4400, i*50) for i in range(3)]
        seq.camp = dict(x=4400.0, y=50.0, strength=12.0, units=creeps)
        seq.camp_ids = {u.addr for u in creeps}
        w = world([self.home] + army + creeps + self.main, food=20, levels={})
        out = seq._leave_camp(w, army, "放弃未开打营地，先收队")
        self.assertNotEqual((out[0].x, out[0].y), seq.home_xy)
        self.assertAlmostEqual(((out[0].x-4000)**2 + (out[0].y-75)**2)**0.5,
                               T.CREEP_STAGE, delta=80)

    def test_hurt_hero_goes_home_when_creeping(self):
        seq = T.AttackSequence(PROFILE)
        foot = U(10, "hfoo", 0, 100, 0)
        wolf = U(400, "nwlt", 12, 2000, 2000)
        seq.step(world([self.home, self.hero, foot, wolf] + self.main, food=10, levels={2: 1}), self.home)
        hurt = U(2, "Hamg", 0, 1900, 1900, 150, 500)
        it = seq.step(world([self.home, hurt, U(10, "hfoo", 0, 1900, 1900), wolf] + self.main, food=10, levels={2: 1}), self.home)
        self.assertEqual((it[0].kind, it[0].why, [u.addr for u in it[0].units]), ("move", "英雄残血先回家", [2]))
        self.assertEqual([u.addr for u in it[1].units], [10])                        # 步兵接着打


class TestDefense(unittest.TestCase):
    def test_modes(self):
        # 027 局终局证明压倒性劣势直接接战会在十几秒内团灭；满血大厅先当肉盾，守军在背敌一侧集中。
        hall = U(1, "hcas", 0, 0, 0)
        feet = [U(10 + i, "hfoo", 0, 200, 0) for i in range(4)]
        knights = [U(200 + i, "hkni", 1, 1200, 0) for i in range(12)]
        d = T.Defense()
        w = world([hall] + feet + knights, food=8, t=400.0)
        mode, it = d.step(w, hall)
        self.assertEqual(mode, "toostrong")
        self.assertEqual({i.kind for i in it}, {"move"})
        self.assertEqual({u.addr for i in it for u in i.units}, {u.addr for u in feet})
        self.assertEqual(d.counts.get("toostrong"), 1)
        self.assertEqual(d.counts.get("回防·压倒劣势借建筑集结"), 1)

        # 主力实际到达锁定集结点后立即反打。
        for u in feet:
            u.x, u.y = d.preserve_point
        mode, it = d.step(world([hall] + feet + knights, food=8, t=419.0), hall)
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in it))
        self.assertEqual(d.counts.get("回防·主力到齐反打"), 1)

        # 第一次反打后重新开始集结窗口，不能永久继承旧超时、每拍都重发反打令。
        for u in feet:
            u.x, u.y = 200, 0
        mode, it = d.step(world([hall] + feet + knights, food=8, t=422.0), hall)
        self.assertEqual(mode, "toostrong")
        self.assertEqual({i.kind for i in it}, {"move"})

        hall.hp = 500                                                    # 大厅只剩 33%，不能再让，最后决战
        mode, it = d.step(world([hall] + feet + knights, food=8, t=425.0), hall)
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in it))
        self.assertEqual(d.counts.get("回防·大厅残血最后决战"), 1)

    def test_enemy_on_building_forces_last_stand_even_with_one_unit(self):
        hall = U(1, "hcas", 0, 0, 0)
        feet = [U(10 + i, "hfoo", 0, 200, 0) for i in range(4)]
        knights = [U(200 + i, "hkni", 1, 1200, 0) for i in range(12)]
        d = T.Defense()
        d.step(world([hall] + feet + knights, food=8, t=400.0), hall)
        lone = U(50, "hfoo", 0, -900, 0)
        for u in knights:
            u.x = -500                       # 已压到锁定集结点附近，也不能让一个新兵独自反打
        mode, it = d.step(world([hall, lone] + knights, food=2, t=419.0), hall)
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in it))
        self.assertEqual(d.counts.get("回防·敌军正在拆建筑反打"), 1)

    def test_defend_and_minor(self):
        hall = U(1, "htow", 0, 0, 0)
        army = [U(10 + i, "hkni", 0, 300, 0) for i in range(10)]
        d = T.Defense()
        mode, it = d.step(world([hall] + army + [U(200 + i, "hfoo", 1, 500, 0) for i in range(8)], food=40, t=400.0), hall)
        # 威胁 540×16/251.19 = 34.4 > 可接受 11.67，能扛 540×40/251.19×(2+6+1=9 -> 1.4) = 120 -> 回防
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in it))
        mode, it = d.step(world([hall] + army + [U(200, "hfoo", 1, 1900, 0)], food=40, t=400.0), hall)
        self.assertEqual((mode, it), ("minor", []))

    def test_militia_defend_together(self):
        # 民兵和兵一起回防；压倒性劣势时也一起到建筑后集中，不把两队拆开。
        hall = U(1, "hcas", 0, 0, 0)
        feet = [U(10 + i, "hfoo", 0, 200, 0) for i in range(2)]
        mil = [U(50 + i, "hmil", 0, 100, 0) for i in range(3)]
        knights = [U(200 + i, "hkni", 1, 1200, 0) for i in range(12)]
        mode, it = T.Defense().step(world([hall] + feet + mil + knights, food=5, t=400.0), hall)
        self.assertEqual(mode, "toostrong")
        self.assertEqual({i.kind for i in it}, {"move"})
        self.assertEqual({u.addr for i in it for u in i.units}, {u.addr for u in feet + mil})


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""打野选路（09-18 设计要求：优先打附近营地、缩短路线，减少跑路时间；近处营地打不过时，比较补兵时间和
去远处营地的跑路时间取更优解；估算战力时把召唤物也算进去）。"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

FOE_HALL = (14000, 0)


def world(units, t=600.0):
    base = [U(1, "htow", 0, 0), U(900, "htow", *FOE_HALL, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    return build_world(base + units, 0, t, 300, 300, 30, 60, levels={2: 3})


def hero(x, y, hp=600.0):
    h = U(2, "Hamg", x, y)
    h.hp = h.hp_max = hp
    return h


def wolves(addr, x, y, n=1, typ="nwlt"):
    return [U(addr + i, typ, x + 60 * i, y, owner=12) for i in range(n)]


class Power(unittest.TestCase):
    def test_summon_counts_with_real_hp_and_damage(self):
        am = hero(0, 0)
        wat = U(20, "hwt3", 0, 0)                                    # 3 级水元素：900 血、45 / 1.5 = 30 每秒
        h1, d1 = T.power([am], {2: 3})
        h2, d2 = T.power([am, wat], {2: 3})
        self.assertAlmostEqual(h2 - h1, 900.0)
        self.assertAlmostEqual(d2 - d1, 30.0)
        self.assertAlmostEqual(d1, T.HERO_DPS_BASE + 3 * T.HERO_DPS_LVL)

    def test_ratio_is_time_to_kill_ratio(self):
        ours = [U(10 + i, "hfoo", 0, 0) for i in range(4)]            # 1680 血 × 37.0/秒
        camp = wolves(700, 0, 0, 2)                                   # 600 血 × 18.5/秒
        ho, do = T.power(ours)
        hc, dc = T.power(camp)
        self.assertAlmostEqual(T.power_ratio(ours, camp), (ho / dc) / (hc / do))   # 他们打光我们要几秒 / 我们打光他们要几秒

    def test_water_elemental_makes_camp_beatable(self):
        """水元素要能把一个「原本打不过」的营地变成「打得过」。

        ⚠ 09-21：原来写死 3 只巨狼，靠的是当时 CREEP_POWER_MIN 正好是 2.5。
          门槛现在由 winprob 从校准过的损失份额推出来（会随重新拟合变），
          写死营地大小的话，这条测试每次重新校准都会以「测试挂了」的样子报出来，而代码是对的
          （测试挂的应该是期望，不是代码现状）。
          所以改成**扫一遍营地大小**，只要求「存在一个营地，水元素能把判断翻过来」——
          那才是这条测试真正想说的话。"""
        am = hero(0, 0, 500.0)
        feet = [U(10 + i, "hfoo", 0, 0) for i in range(4)]
        flipped = []
        for n in range(2, 9):
            camp = wolves(700, 3000, 0, n, "nwlg")
            without = T.power_ratio([am] + feet, camp, {2: 3})
            with_we = T.power_ratio([am, U(20, "hwt2", 0, 0)] + feet, camp, {2: 3})
            self.assertGreater(with_we, without, f"{n} 只狼：加了水元素战力比反而没涨")
            if without < T.CREEP_POWER_MIN <= with_we:
                flipped.append(n)
        self.assertTrue(flipped, f"没有任何营地大小能被水元素翻过门槛 {T.CREEP_POWER_MIN:.2f}")


class Route(unittest.TestCase):
    def test_next_camp_is_near_the_army_not_near_home(self):
        # 主力在 (5000,3000) 刚打完一个营地；A 离主力 1000、离家 6000，B 离家 1500、离主力 5000 —— 同样大小先打 A
        army = [hero(5000, 3000)] + [U(10 + i, "hfoo", 5000, 3000) for i in range(4)]
        a = wolves(700, 5000, 4000)
        b = wolves(710, 1500, 0)
        w = world(army + a + b)
        plans = T.creep_plans(w, army, (5000.0, 3000.0), w.my_halls[0])
        self.assertEqual(plans[0]["camp"]["units"][0].addr, 700)
        self.assertLess(plans[0]["travel"], plans[1]["travel"])
        seq = T.AttackSequence(dict(PROFILE))
        seq.allow_attack = False                                      # 对面没兵时出击会先走，这里只看打野
        seq.step(w, w.my_halls[0])
        self.assertEqual(seq.camp_ids, {700})
        self.assertEqual(seq.counts.get('打野·选营地'), 1)

    def test_unbeatable_near_camp_skipped_when_no_reinforcements(self):
        army = [hero(0, 0, 500.0)] + [U(10 + i, "hfoo", 0, 0) for i in range(2)]
        near = wolves(700, 1500, 0, 3, "nwlg")                        # 近、打不过
        far = wolves(710, -5000, 0, 1)                                # 远、打得过
        w = world(army + near + far)
        plans = T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0], rate=0.0)
        self.assertEqual([p["camp"]["units"][0].addr for p in plans], [710])   # 不涨兵 = 近的永远等不到
        self.assertLess(T.power_ratio(army, near, w.levels), T.CREEP_POWER_MIN)

    def test_wait_for_reinforcements_when_cheaper_than_walking(self):
        army = [hero(0, 0, 500.0)] + [U(10 + i, "hfoo", 0, 0) for i in range(3)]
        near = wolves(700, 1200, 0, 3, "nwlg")                        # 等级和 12，打不过
        far = wolves(710, -6500, 0, 1)                                # 等级和 3，打得过，但要走 6500
        w = world(army + near + far)
        r = T.power_ratio(army, near, w.levels)
        own = w.own_strength
        rate = 0.5                                                    # 每游戏秒涨 0.5 强度（出兵快）
        plans = T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0], rate=rate)
        first = plans[0]
        self.assertEqual(first["camp"]["units"][0].addr, 700, [(p["camp"]["units"][0].addr, round(p["score"], 3)) for p in plans])
        self.assertAlmostEqual(first["wait"], ((T.CREEP_POWER_MIN / r) ** 0.5 - 1.0) * own / rate)
        slow = T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0], rate=0.01)   # 出兵慢：等兵超过 CREEP_WAIT_MAX，去远的
        self.assertEqual(slow[0]["camp"]["units"][0].addr, 710)

    def test_own_rate_from_history(self):
        seq = T.AttackSequence(dict(PROFILE))
        w0 = world([hero(0, 0)] + [U(10, "hfoo", 0, 0)], t=100.0)
        seq.step(w0, w0.my_halls[0])
        w1 = world([hero(0, 0)] + [U(10 + i, "hfoo", 0, 0) for i in range(4)], t=220.0)
        seq.step(w1, w1.my_halls[0])
        self.assertAlmostEqual(seq.own_rate(w1), (w1.own_strength - w0.own_strength) / 120.0)

    def test_does_not_cross_one_camp_to_reach_another(self):
        # 1-011 的形状：远处大营地分数高，但直线必须穿过另一个野点。
        army = [hero(0, 0, 650.0)] + [U(10+i, "hfoo", 0, 50*i) for i in range(8)]
        blocker = wolves(700, 2000, 120, 1)
        target = wolves(710, 4000, 0, 3)
        w = world(army + blocker + target)
        plans = T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0])
        ids = [{u.addr for u in p["camp"]["units"]} for p in plans]
        self.assertIn({700}, ids)
        self.assertNotIn({710, 711, 712}, ids)

    def test_nearby_camp_behind_origin_does_not_block_route_away(self):
        army = [hero(0, 0, 650.0)] + [U(10+i, "hfoo", 0, 50*i) for i in range(8)]
        behind = wolves(700, -700, 0, 1)
        ahead = wolves(710, 3000, 0, 1)
        w = world(army + behind + ahead)
        ids = [{u.addr for u in p["camp"]["units"]}
               for p in T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0])]
        self.assertIn({710}, ids)

    def test_second_camp_contact_aborts_and_tracks_both_camps(self):
        army = [hero(3300, 0, 650.0)] + [U(10+i, "hfoo", 3300, 60*i) for i in range(4)]
        target = wolves(700, 4000, 0, 2)
        accidental = wolves(710, 3000, 300, 2)
        w = world(army + target + accidental)
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.state, seq.camp, seq.camp_ids = "creep", dict(x=4030.0, y=0.0, strength=6.0, units=target), {700, 701}
        seq.creep_engaged = True
        out = seq.step(w, w.my_halls[0])
        self.assertEqual(seq.state, "retreat")
        self.assertTrue({700, 701, 710, 711}.issubset(seq.camp_ids))
        self.assertTrue(any(abs(x-3030.0) < 100 and abs(y-300.0) < 100
                            for x, y, _until in seq.skip_camps), seq.skip_camps)
        self.assertTrue(any("第二营地" in i.why for i in out), [(i.kind, i.why) for i in out])

    def cleared_camp_with_hurt_army(self, next_camp):
        army = [hero(4000, 3000, 650.0)] + [U(10 + i, "hfoo", 4000, 3000 + 60 * i) for i in range(6)]
        for u in army[1:5]:
            u.hp = 0.3 * u.hp_max                                   # 4 个带伤（4-036：缺 895 血）
        w = world(army + next_camp)
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.state, seq.camp, seq.camp_ids = "creep", dict(x=4000.0, y=3100.0, strength=6.0, units=[]), {690}
        seq.creep_engaged = True
        seq.step(w, w.my_halls[0])
        return seq

    def test_hurt_army_does_not_walk_past_a_camp_to_restage(self):
        """4-036 8:35：带伤前移的直线离下一个营地约 580，把它惊醒 —— 英雄被拉走、大脑收队，4 个步兵死在撤退路上。
        营地在 (2000,1600)，集合点在它和家（0,0）之间，从 (4000,3000) 过去正好擦着营地走。"""
        seq = self.cleared_camp_with_hurt_army(wolves(720, 2000, 1600, 1))
        self.assertEqual(seq.counts.get("打野·带伤前移会惊醒营地·原地补给"), 1, dict(seq.counts))
        self.assertIsNone(seq.counts.get("打野·清营后前往下一营地"))
        self.assertEqual(seq.camp_ids, set())                       # 没锁定那个营地

    def test_hurt_army_still_restages_when_the_route_is_clear(self):
        """对照：营地在队伍身后（离家更远的一侧），去集合点的路不经过它 —— 照常前移。"""
        seq = self.cleared_camp_with_hurt_army(wolves(720, 4000, 5000, 1))
        self.assertEqual(seq.counts.get("打野·清营后前往下一营地"), 1, dict(seq.counts))

    def test_a_reinforcement_passing_another_camp_does_not_pull_the_army(self):
        """6-191 6 分钟：正在打的营地在 (4000,0)，一个刚出兵的步兵从家赶来、路过 2700 外另一个营地 681 以内 ——
        原来整队「误入第二营地」撤走、一半人在旁边看戏。只算在这场仗里（离当前营地 CREEP_SECOND_SCOPE 内）的人。"""
        army = [hero(3300, 0, 650.0)] + [U(10+i, "hfoo", 3300, 60*i) for i in range(4)]
        walker = U(20, "hfoo", 1300, 2000)                       # 赶路的援兵
        target = wolves(700, 4000, 0, 2)
        sleeping = wolves(710, 1300 + 600, 2000 + 300, 3)       # 援兵身边的另一个营地
        w = world(army + [walker] + target + sleeping)
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.state, seq.camp, seq.camp_ids = "creep", dict(x=4030.0, y=0.0, strength=6.0, units=target), {700, 701}
        seq.creep_engaged = True
        self.assertGreater(math.hypot(walker.x - 4030.0, walker.y), T.CREEP_SECOND_SCOPE)   # 场景前提
        seq.step(w, w.my_halls[0])
        self.assertNotEqual(seq.state, "retreat")
        self.assertIsNone(seq.counts.get("打野·误入第二营地撤离"))
        self.assertEqual(seq.camp_ids, {700, 701})

    def test_recent_contact_point_blocks_routes_behind_it(self):
        army = [hero(0, 0, 650.0)] + [U(10+i, "hfoo", 0, 50*i) for i in range(8)]
        hazard = wolves(700, 2000, 100, 1)
        behind = wolves(710, 4000, 0, 1)
        w = world(army + hazard + behind)
        skip = [(2000.0, 100.0, w.t + T.CREEP_SKIP_S)]
        ids = [{u.addr for u in p["camp"]["units"]}
               for p in T.creep_plans(w, army, (0.0, 0.0), w.my_halls[0], skip=skip)]
        self.assertNotIn({700}, ids)                                  # 危险点自身不再选
        self.assertNotIn({710}, ids)                                  # 也不穿过它去选后面的营地

    def test_expansion_guard_clears_route_blocker_before_target_camp(self):
        # 1-015 现场：clear_target 原来绕过 creep_plans，撤离后会再选同一条被挡的路。
        army = [hero(0, 0, 650.0)] + [U(10+i, "hfoo", 0, 50*i) for i in range(8)]
        blocker = wolves(700, 2000, 100, 1)
        target = wolves(710, 4000, 0, 2)
        w = world(army + blocker + target)
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.clear_target = {"ids": {710, 711}}
        out = seq.step(w, w.my_halls[0])
        self.assertEqual(seq.camp_ids, {700})
        self.assertEqual(seq.counts.get("打野·清分矿路线挡路营地"), 1)
        self.assertTrue(any("挡路野怪" in i.why for i in out), [(i.kind, i.why) for i in out])

    def test_strong_route_blocker_means_creep_elsewhere_not_stand_at_home(self):
        """v30 实机 1/3 号：去分矿守卫的路上有打不过的营地时整队在家站着（「清分矿·路线有强营地挡路」28~36 拍）。
        AMAI 打不下分矿时照常打野攒实力；普通选营地本来就不穿越别的营地。"""
        army = [hero(0, 0, 650.0)] + [U(10+i, "hfoo", 0, 50*i) for i in range(8)]
        blocker = wolves(700, 2000, 100, 8, "nwlg")        # 路上一大群，打不过
        guard = wolves(730, 4000, 0, 2)
        side = wolves(740, -300, 2500, 1)                  # 另一边一个能打的小营地
        w = world(army + blocker + guard + side)
        self.assertLess(T.power_ratio(army, blocker, w.levels), T.CLEAR_POWER)   # 场景前提：挡路营确实打不过
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.clear_target = {"ids": {730, 731}}
        seq.step(w, w.my_halls[0])
        self.assertEqual(seq.counts.get("清分矿·路线有强营地挡路"), 1)
        self.assertEqual(seq.camp_ids, {740})

    def test_after_clearing_a_camp_the_expansion_guard_comes_next(self):
        """4-030：清完分矿路上的挡路营（219 秒）后接着去打别的营地（强度 16/17/9），分矿守卫一直没人管，
        「建造跳过·分矿旁边有野怪」114 次、分矿 9 分 37 秒才开。清空营地那一拍原来直接走普通选营地，
        清分矿那一段只在「进来时手上没营地」才看。AMAI 的 attack 线程是先 needs_exp、再普通打野。"""
        army = [hero(2000, 0, 650.0)] + [U(10+i, "hfoo", 2000, 50*i) for i in range(8)]
        guard = wolves(710, 4000, 0, 2)
        other = wolves(720, 2000, 1300, 2)                # 一样大、离得更近的营地（在 CREEP_SECOND_CONTACT 750 之外）
        w = world(army + guard + other)
        # 先确认这个场景确实能分开两种写法：普通选营地挑的是身边那个，不是分矿守卫
        plans = T.creep_plans(w, army, (2000.0, 0.0), w.my_halls[0])
        self.assertEqual({u.addr for u in plans[0]["camp"]["units"]}, {720, 721})
        seq = T.AttackSequence(dict(PROFILE)); seq.allow_attack = False
        seq.clear_target = {"ids": {710, 711}}
        # 手上的营地（挡路营 700）这一拍刚打光：它的野怪已经不在快照里
        seq.state, seq.camp, seq.camp_ids = "creep", dict(x=2000.0, y=100.0, strength=3.0, units=[]), {700}
        seq.creep_engaged = True
        seq.step(w, w.my_halls[0])
        self.assertEqual(seq.counts.get("打野·营地清空"), 1)
        self.assertEqual(seq.camp_ids, {710, 711})
        self.assertEqual(seq.counts.get("打野·清分矿野怪"), 1)


class SummonRegroupsBeforeCamp(unittest.TestCase):
    def test_water_elemental_returns_to_stage_with_other_first_arrivals(self):
        # 1-011：毫秒层水元素护卫会主动碰野；未达到开打线时召唤物也必须收到纯移动集合令。
        camp = wolves(700, 4000, 0, 3, "nwlg")
        wat = U(20, "hwat", 3700, 0)                                  # 营地边：水元素 + 一个步兵，战力不够开打
        foot = U(10, "hfoo", 3700, 100)
        rest = [hero(500, 0, 500.0)] + [U(11 + i, "hfoo", 500, 100 * i) for i in range(3)]
        w = world([wat, foot] + rest + camp)
        seq = T.AttackSequence(dict(PROFILE))
        seq.allow_attack = False
        seq.camp_ids = {700, 701, 702}
        seq.creep_since = w.t
        seq.camp = dict(x=4060.0, y=0.0, strength=12.0, units=camp)
        out = seq.step(w, w.my_halls[0])
        back = [i for i in out if i.kind == "move" and "先到的退到集合点" in i.why]
        self.assertEqual([[u.addr for u in i.units] for i in back], [[20, 10]], [(i.kind, i.why) for i in out])
        self.assertFalse([i for i in out if i.kind == "attack"])


if __name__ == "__main__":
    unittest.main()

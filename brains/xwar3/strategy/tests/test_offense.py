"""进攻融合的跨拍回归：机会、承诺、接战安全和命令所有权。"""
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import offense as O, tactics as T, harass as H
from brain.runtime import Brain
from test_tactics import U, world, PROFILE
from test_runtime_smoke import FakeCmd


class Opportunity(unittest.TestCase):
    def setUp(self):
        self.home = U(1, "htow")
        self.exp = U(100, "htow", 1, 4500, 0)
        self.main = [U(101, "hcas", 1, 18000, 10000), U(102, "hbar", 1, 18200, 10000)]
        self.here = [U(10 + i, "hfoo", x=100) for i in range(4)]
        self.away = [U(20 + i, "hfoo", x=-3500, y=1000) for i in range(3)]
        self.foes = [U(200 + i, "hkni", 1, 18000, 10000) for i in range(20)]

    def w(self, t=600, extra=(), army=None):
        return world([self.home, self.exp] + self.main + self.foes + (self.here + self.away if army is None else army) + list(extra), t=t)

    def test_larger_remote_army_does_not_cancel_committed_gather(self):
        seq = T.AttackSequence(PROFILE)
        seq.step(self.w(), self.home)
        self.assertEqual((seq.state, seq.target.addr), ("gather", self.exp.addr))
        seq.step(self.w(t=602), self.home)
        self.assertEqual((seq.state, seq.target.addr), ("gather", self.exp.addr))
        seq.step(self.w(t=640), self.home)
        self.assertEqual((seq.state, seq.target.addr), ("attack", self.exp.addr))
        self.assertGreater(self.w().foe_strength, self.w().own_strength * 3)

    def test_nearby_reinforcements_close_the_window(self):
        seq = T.AttackSequence(PROFILE)
        seq.step(self.w(), self.home)
        w2 = self.w(t=602)
        w2.enemy_fighters = [U(300 + i, "hkni", 1, 6500, 0) for i in range(10)]
        seq.step(w2, self.home)
        self.assertNotIn(seq.state, ("attack", "gather"))
        self.assertEqual(seq.counts.get("集结·目标变得不好"), 1)

    def test_low_loss_expansion_raid_withdraws_if_reinforcements_arrive(self):
        seq = T.AttackSequence(PROFILE, state="attack", target=self.exp,
                               target_kind="骚扰分矿", start_strength=8)
        w = self.w(t=602)
        w.enemy_fighters = [U(300+i, "hkni", 1, 5000, i*50) for i in range(10)]
        out = seq.step(w, self.home)
        self.assertEqual(seq.state, "retreat")
        self.assertEqual(seq.counts.get("出击·援军封路撤"), 1)
        self.assertTrue(out and all(i.kind == "move" for i in out))

    def test_gather_timeout_cannot_spend_absent_reinforcements(self):
        # 四个在场步兵 8，加远处三个 6，总计 14；当地五个步兵 10。
        # 总兵力能打，在场兵力不能打，40 秒到期也必须取消。
        guards = [U(300 + i, "hfoo", 1, 4500, 100) for i in range(5)]
        seq = T.AttackSequence(PROFILE)
        seq.step(self.w(extra=guards), self.home)
        self.assertEqual(seq.state, "gather")
        seq.step(self.w(t=640, extra=guards), self.home)
        self.assertEqual(seq.state, "retreat")
        self.assertEqual(seq.counts.get("集结·到场兵力不足放弃"), 1)

    def test_route_army_is_not_ignored_as_remote_defenders(self):
        blockers = [U(300 + i, "hkni", 1, 2500, 500) for i in range(10)]
        w = self.w(extra=blockers)
        ev = O.assess(w, self.here + self.away, self.home, self.exp)
        self.assertFalse(ev.good)
        self.assertGreater(ev.route, 0)
        self.assertLessEqual(ev.route, ev.response)

    def test_route_towers_are_counted(self):
        w = self.w(extra=[U(300 + i, "hgtw", 1, 2000, 0) for i in range(5)])
        ev = O.assess(w, self.here + self.away, self.home, self.exp)
        self.assertFalse(ev.good)
        self.assertGreater(ev.tower, 0)

    def test_high_aggression_does_not_authorize_overwhelming_defenders(self):
        w = self.w(extra=[U(300 + i, "hkni", 1, 4500, 0) for i in range(20)])
        w.aggression = 100
        # 高进攻性、低倍率和分矿高加分也不能越过硬性强度底线。
        ev = O.assess(w, self.here, self.home, self.exp, margin=0.5, bonus=1000)
        self.assertFalse(ev.good)

    def test_stalled_attack_releases_target(self):
        seq = T.AttackSequence(PROFILE)
        seq.step(self.w(army=self.here + [U(20, "hfoo", x=150)]), self.home)
        self.assertEqual(seq.state, "attack")
        seq.step(self.w(t=661, army=self.here + [U(20, "hfoo", x=150)]), self.home)
        self.assertEqual(seq.state, "retreat")
        self.assertIn(self.exp.addr, seq.skip_targets)

    def test_damage_to_target_counts_as_progress(self):
        seq = T.AttackSequence(PROFILE)
        army = [U(10 + i, "hfoo", x=4300) for i in range(6)]
        seq.step(self.w(army=army), self.home)
        self.exp.hp -= 100
        seq.step(self.w(t=661, army=army), self.home)
        self.assertEqual(seq.state, "attack")

    def test_healing_hero_is_not_reenlisted_next_tick(self):
        # ★ 09-22 原来是 250/500（50%）：出击中「血不满先恢复」只在集结时给之后（1-296 英雄全回了家），
        #   半血英雄出击中跟着打；这条要守的是**残血**（< HERO_RETREAT_HP）的英雄不被下一拍重新派回去。
        hero = U(2, "Hamg", hp=225, hp_max=500)
        seq = T.AttackSequence(PROFILE)
        army = self.here + [U(20, "hfoo", x=150), hero]
        for t in (600, 602, 604):
            intents = seq.step(self.w(t=t, army=army), self.home)
            self.assertEqual(seq.state, "attack")
            self.assertNotIn(2, {u.addr for it in intents if it.kind.startswith("attack") for u in it.units})

    def test_lagging_hero_cannot_hold_army_forever(self):
        seq = T.AttackSequence(PROFILE)
        seq.hero_ids = {2}
        hero = U(2, "Hamg", x=0)
        army = [U(10 + i, "hfoo", x=3000) for i in range(6)] + [hero]
        seq.now = 100
        self.assertIsNotNone(seq._wait_heroes(army, 3000, 0, 9000, 0, self.exp, "test"))
        seq.now = 113
        self.assertIsNone(seq._wait_heroes(army, 3000, 0, 9000, 0, self.exp, "test"))

    def test_defense_interrupts_old_task(self):
        seq = T.AttackSequence(PROFILE, state="attack", target=self.exp, camp_ids={300})
        seq.interrupt()
        self.assertEqual((seq.state, seq.target, seq.camp_ids), ("home", None, set()))

    def test_new_offense_does_not_abandon_live_creep_fight(self):
        wolf = U(700, "nwlt", 12, 150, 100)
        hero = U(2, "Hmkg", x=100)
        seq = T.AttackSequence(PROFILE, state="creep", camp_ids={700}, creep_engaged=True, creep_since=600)
        seq.camp = dict(x=150, y=100, strength=2, units=[wolf])
        out = seq.step(self.w(army=self.here + [hero], extra=[wolf]), self.home)
        self.assertEqual(seq.state, "creep")
        self.assertTrue(any(it.target is wolf for it in out))

    def test_badly_hurt_soldier_does_not_rejoin_mission_next_tick(self):
        low = U(90, "hfoo", hp=40, hp_max=420)
        seq = T.AttackSequence(PROFILE)
        for t in (600, 602):
            out = seq.step(self.w(t=t, army=self.here + [U(20, "hfoo", x=150), low]), self.home)
            self.assertEqual(seq.state, "attack")
            self.assertNotIn(90, {u.addr for it in out if it.kind.startswith("attack") for u in it.units})


class Incoming(unittest.TestCase):
    def test_far_intent_leaves_time_for_other_tasks(self):
        army = [U(10, "hfoo", x=3000)]
        inc = dict(town=U(1, "htow"), captain=dict(army_strength=20), dist=12000)
        self.assertFalse(O.return_due(inc, army))
        inc["dist"] = 4500
        self.assertTrue(O.return_due(inc, army))


class Ownership(unittest.TestCase):
    def scene(self):
        home = U(1, "htow")
        # 三人骚扰队必须留下至少六名健康地面兵；这些所有权用例还会排除一名伤员/微操单位，
        # 因此给十名，让剩余九名恰好通过安全门槛。
        feet = [U(10 + i, "hfoo", x=100 + i * 20) for i in range(10)]
        return home, feet, world([home] + feet + [U(100, "htow", 1, 4000), U(101, "hpea", 1, 4100)], t=600)

    def test_failed_roll_does_not_consume_real_sortie_cooldown(self):
        home, _, w = self.scene()
        manager = H.HarassManager(groups_cfg=(H.HUMAN_GROUPS[0],))
        manager.rng.uniform = lambda a, b: 100.0
        # food 50 给的进攻性仍小于全必派阈值。
        # 09-22 起优势（我方强度 >= 对面 × HARASS_AHEAD）不抽签；这个场景对面没有兵 = 优势。这条测的是抽签：
        # 给对面放一支远在地图另一头的兵（不在农民/路线边上），不是优势
        w = replace(w, enemy_fighters=[U(200 + i, "hkni", 1, 9000, 9000) for i in range(8)])
        self.assertLess(w.own_strength, w.foe_strength * H.HARASS_AHEAD)
        manager.step(w, home, False, False)
        self.assertFalse(manager.active)
        self.assertFalse(manager.last)
        manager.rng.uniform = lambda a, b: 0.0
        manager.step(replace(w, t=601), home, False, False)
        self.assertFalse(manager.active)
        manager.step(replace(w, t=621), home, False, False)
        self.assertTrue(manager.active)

    def test_harass_does_not_reclaim_units_held_by_micro(self):
        home, feet, w = self.scene()
        manager = H.HarassManager()
        manager.rng.uniform = lambda a, b: 0.0
        manager.step(w, home, False, False, excluded={feet[0].addr})
        self.assertNotIn(feet[0].addr, manager.busy_addrs)
        intents = manager.step(w, home, False, False, excluded=set(manager.busy_addrs))
        self.assertFalse(any(feet[0] in it.units for it in intents))

    def test_runtime_dispatch_gives_each_unit_one_role(self):
        home, _, w = self.scene()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.config.values["enable_attack"] = False
            b.config.values["enable_creep"] = False
            b.harass.rng.uniform = lambda a, z: 0.0
            sent = []
            b._do = lambda it, t: sent.append(it)
            b._hero_idle_probe = lambda *args: None
            b._read_hurt = lambda: {10}
            b.job_tactics(w)
            self.assertTrue(b.harass.busy_addrs)
            seen = [u.addr for it in sent for u in it.units]
            self.assertEqual(len(seen), len(set(seen)))
            self.assertNotIn(10, seen)

    def test_runtime_defense_recalls_harass_and_cancels_old_attack(self):
        home, _, w = self.scene()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.harass.active[1] = H.Group(H.HUMAN_GROUPS[0], [10, 11], 4)
            b.attack.state, b.attack.target = "gather", w.enemy_halls[0]
            sent = []
            b._do = lambda it, t: sent.append(it)
            b._hero_idle_probe = lambda *args: None
            b._read_hurt = lambda: {12}
            b.act.last_point[12] = (("target", 999), 500, 0, 1e9)
            b.defense.step = lambda v, h: ("defend", [T.Intent("attack_move", v.my_army, 500, 0, why="回防")])
            b.job_tactics(w)
            self.assertEqual((b.attack.state, b.attack.target, b.harass.active), ("home", None, {}))
            assigned = {u.addr for it in sent for u in it.units}
            self.assertTrue({10, 11} <= assigned)
            self.assertNotIn(12, assigned)
            self.assertNotIn(12, b.act.last_point)

    def test_runtime_does_not_take_new_harass_crew_from_engaged_creep(self):
        """09-22 起：打野**已接战**不抽骚扰队（原来整个 creep 状态都不抽）。
        不能只顾打野、不去骚扰 —— 1-304~308 每局骚扰只派出过 1 次，主力在打仗 316~390 次大多是在走向营地。"""
        for engaged in (True, False):
            home, feet, w = self.scene()
            with tempfile.TemporaryDirectory() as d:
                b = Brain(FakeCmd(), None, d)
                b.harass.rng.uniform = lambda a, z: 0.0
                b.attack.state = "creep"
                b.attack.creep_engaged = engaged
                b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 1000, 0, why="打野")]
                sent = []
                b._do = lambda it, t: sent.append(it)
                b._hero_idle_probe = lambda *args: None
                b.job_tactics(w)
                if engaged:
                    self.assertFalse(b.harass.active)
                    self.assertEqual({u.addr for it in sent for u in it.units}, {u.addr for u in feet})
                else:
                    self.assertTrue(b.harass.active)
                    crew = b.harass.busy_addrs
                    creep = {u.addr for it in sent if it.why == "打野" for u in it.units}
                    self.assertFalse(crew & creep)                         # 一个兵只有一个角色

    def test_runtime_splits_safe_reserve_during_human_main_attack(self):
        home, feet, old = self.scene()
        hero = U(2, "Hamg", x=100)
        w = world([home, hero] + feet + old.enemy_halls + old.enemy_workers, t=600)
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.attack.state, b.attack.target = "attack", w.enemy_halls[0]
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army + v.my_heroes,
                                                   b.attack.target.x, b.attack.target.y, why="主攻")]
            b.harass.rng.uniform = lambda a, z: 0.0
            sent = []
            b._do = lambda it, t: sent.append(it)
            b._hero_idle_probe = lambda *args: None
            b._read_hurt = lambda: set()
            b.job_tactics(w)
            self.assertTrue(b.harass.active[1].dual_line)
            assigned = [u.addr for it in sent for u in it.units]
            self.assertEqual(len(assigned), len(set(assigned)))
            self.assertTrue(any(it.why == "主攻" for it in sent))
            self.assertTrue(any(it.why == "双线骚扰：走向农民" for it in sent))


if __name__ == "__main__":
    unittest.main()

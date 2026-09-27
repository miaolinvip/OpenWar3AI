# -*- coding: utf-8 -*-
"""AMAI 战略引擎接进 runtime：默认 table 模式一行不变，amai 模式能跑起来并下出像样的单。

假通道和假单位复用 test_runtime_smoke 的 FakeCmd / U（U 给英雄的血是 100，英雄要自己设 hp/hp_max）。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A                            # noqa: E402
from brain import gamedata as G                       # noqa: E402
from brain.build import Want                          # noqa: E402
from brain.runtime import Brain                       # noqa: E402
from brain.tables import human as human_table         # noqa: E402
from brain.world import build_world                   # noqa: E402
from test_runtime_smoke import FakeCmd, U             # noqa: E402

RACE_SETUP = {
    "human": ("htow", "hpea"), "orc": ("ogre", "opeo"),
    "undead": ("unpl", "uaco"), "nightelf": ("etol", "ewsp"),
}


def make_brain(tmp, engine=None, **cfg):
    if engine or cfg:
        with open(os.path.join(tmp, "brain_config.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(cfg, **({"strategy_engine": engine} if engine else {})), fh)
    b = Brain(FakeCmd(), None, tmp)
    b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=4000 + i * 64, y=-3500, hp=50.0) for i in range(6)]
    return b


def world_of(race="human", gold=800, lumber=400, food_used=5, food_cap=12, enemies=None, extra=None, t=30.0):
    hall_code, worker = RACE_SETUP[race]
    units = [U(0x100, hall_code, 4352, -2688), U(0x200, "ngol", 4352, -3200, owner=15),
             U(0x201, "ngol", -4352, -3200, owner=15)]
    units += [U(0x300 + i, worker, 4300 + i * 40, -2900, order=A.ORDER_HARVEST, has_task=True) for i in range(5)]
    units += enemies if enemies is not None else [U(0x800, "hcas", -4352, 2944, owner=1)]
    units += extra or []
    return build_world(units, 0, t, gold, lumber, food_used, food_cap)


class TestTableModeUnchanged(unittest.TestCase):
    def test_default_is_table_and_no_engine(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d)
            self.assertEqual(b.config["strategy_engine"], "table")
            w = world_of()
            b.job_build(w)
            self.assertIsNone(b.strategy)                       # 默认不建 AMAI 引擎
            codes = [r["code"] for r in b.last_plan["wants"]]
            table_codes = [x.code for x in human_table.wants(w, b.profile)]
            self.assertEqual(sorted(codes), sorted(table_codes))
            self.assertIsNone(b.last_plan.get("engine"))

    def test_bad_engine_value_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d, engine="nonsense")
            self.assertEqual(b.config["strategy_engine"], "table")   # 不认识的值被 coerce 丢掉


class TestExpansionGuardedByCreeps(unittest.TestCase):
    """★ 09-18 实机（LT、Echo Isles，25 游戏分钟还是一座大厅）：分矿旁边有野怪时，引擎原来只记「还没到时候」就返回，
    永远开不出去。现在改成派兵先清（和 table 模式同一条路：写 attack.clear_target）。"""

    def run_builder(self, creeps):
        d = tempfile.mkdtemp()
        b = make_brain(d, engine="amai")
        peas = [U(0x400 + i, "hpea", 4300 + i * 40, -2800, order=A.ORDER_HARVEST, has_task=True) for i in range(12)]
        for t in range(2, 420, 2):                       # 和 drive() 一样用 step 驱动（引擎在 step 里按种族建起来）
            w = world_of(t=float(t), extra=peas + creeps)
            b.step(SimpleNamespace(units=w.units, me=0, gold=w.gold, lumber=w.lumber,
                                   food_used=w.food_used, food_cap=w.food_cap), float(t))
        return b

    def test_creeped_expansion_asks_army_to_clear_it(self):
        creeps = [U(0x700 + i, "nwlg", -4352 + 200 * i, -3100, owner=12) for i in range(3)]
        b = self.run_builder(creeps)
        counts = b.strategy.counts
        self.assertGreaterEqual(counts.get("开矿·分矿旁边有野怪，先清", 0), 1)   # 到点之后每轮都在喊「先清」
        ct = b.attack.clear_target
        self.assertIsNotNone(ct)
        self.assertEqual(ct["ids"], {c.addr for c in creeps})

    def test_clean_expansion_goes_into_queue(self):
        b = self.run_builder([])
        self.assertEqual(b.strategy.counts.get("开矿·分矿旁边有野怪，先清", 0), 0)
        # 引擎里带随机（选战略/人格/CheckFastExpansion），所以只断言和野怪有关的这两条 —— 它们是确定的
        self.assertEqual(b.strategy.counts.get("开矿·分矿旁边有野怪（认不出是哪几只）", 0), 0)
        self.assertIsNone(b.attack.clear_target)


class TestAmaiMode(unittest.TestCase):
    def drive(self, race, seconds=180, **kw):
        d = tempfile.mkdtemp()
        b = make_brain(d, engine="amai")
        for t in range(2, seconds, 2):
            w = world_of(race, t=float(t), **kw)
            b.step(SimpleNamespace(units=w.units, me=0, gold=w.gold, lumber=w.lumber,
                                   food_used=w.food_used, food_cap=w.food_cap), float(t))
        return b

    def test_four_races_run_clean(self):
        for race in ("human", "orc", "undead", "nightelf"):
            b = self.drive(race)
            errs = {n: s for n, s in b.jobs.stats().items() if s["errors"]}
            self.assertEqual(errs, {}, f"{race} 有任务抛异常：{errs}")
            st = b.strategy
            self.assertIsNotNone(st, race)
            self.assertIn(st.strategy, st.data.strategy_names, race)
            self.assertEqual(len(st.heroes), 3, race)
            self.assertTrue(all(h for h in st.heroes), race)
            self.assertTrue(st.queue.rounds > 0, race)
            self.assertEqual(b.last_plan.get("engine"), "amai", race)
            # 解释器里不该有"语句出错"和"没接的函数"
            bad = {k: v for k, v in st.seq.counts.items() if k.startswith("语句出错") or k.startswith("没接的函数")}
            self.assertEqual(bad, {}, f"{race}: {bad}")

    def test_orders_are_issued(self):
        b = self.drive("human", seconds=240)
        built = {o for _u, o, _x, _y in b.cmd.builds}
        self.assertIn(A.rawcode("halt"), built)                 # 祭坛（global_init 的 Block 逼着先造）
        self.assertIn(A.rawcode("hpea"), [o for _u, o in b.cmd.trains])
        self.assertTrue(b.strategy.queue.entries)

    def test_live_snapshot_has_strategy(self):
        b = self.drive("human", seconds=120)
        from brain import live
        snap = live.snapshot(b)
        s = snap["strategy"]
        self.assertIsNotNone(s)
        for key in ("engine", "strategy", "counter", "heroes", "skillLines", "enemy", "queue", "queueLen",
                    "profile", "lastChangeT", "lastChangeWhy", "changes", "counterUnit", "counterBlind",
                    "ally", "rounds", "expTimes", "notes", "counts"):
            self.assertIn(key, s)
        self.assertEqual(s["engine"], "amai")
        json.dumps(snap, ensure_ascii=False, default=str)       # 能序列化（brain_live.json 要写盘）

    def test_table_mode_snapshot_has_no_strategy(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d)
            b.step(SimpleNamespace(units=world_of().units, me=0, gold=500, lumber=200, food_used=5, food_cap=12), 4.0)
            from brain import live
            self.assertIsNone(live.snapshot(b)["strategy"])

    def test_strength_update_and_counter(self):
        """50~65 游戏秒之后 enemy_* 才有值；有值之后换反制类别按它选。"""
        foes = [U(0x800, "hcas", -4352, 2944, owner=1)] + [U(0x810 + i, "hfoo", -4300 + i * 30, 2900, owner=1) for i in range(6)]
        b = self.drive("human", seconds=200, enemies=foes)
        st = b.strategy
        self.assertGreater(st.strengths.updates, 0)
        self.assertGreater(st.strengths.enemy["normal"], 0.0)   # 6 个步兵 normal 2 × 6 = 12（再加预测规则）
        self.assertIsNotNone(st.chosen_counter)

    def test_dead_hero_becomes_revive_request(self):
        """死亡英雄表 -> 复活请求进本轮排序（优先级 200，排在农场/农民后面、其它全部前面）。"""
        b = self.drive("human", seconds=60)
        st = b.strategy
        hero = st.heroes[0]
        b.act.dead_heroes = lambda addr: [0x4321]
        b.hero_seen[0x4321] = (hero, 3)
        st.next_build_at = 0.0
        w = world_of("human", t=70.0, extra=[U(0x505, "halt", 4700, -2500)])
        b.world = w
        b.job_build_amai(w)
        rows = [r for r in b.last_plan["wants"] if r["kind"] == "revive"]
        self.assertTrue(rows, b.last_plan["wants"][:5])
        self.assertEqual(rows[0]["prio"], 200)

    def test_revive_priority_from_settings(self):
        b = self.drive("human", seconds=60)
        st = b.strategy
        b.dead_waiting = [{"ptr": 0x1, "code": st.heroes[0], "level": 3}]
        entries = st.revive_entries(b, b.world)
        self.assertEqual([(e.kind, e.code, e.prio) for e in entries], [("revive", st.heroes[0], 200)])
        b.dead_waiting = [{"ptr": 0x2, "code": st.heroes[1], "level": 1}]
        self.assertEqual(st.revive_entries(b, b.world)[0].prio, 180)

    def test_aggression_uses_profile_and_strategy(self):
        """§6.1：进攻性 = 全局 2 + 人格第 3 列 + 人口档 + RaceAggression + 战略那几列（-10 如果没英雄）。"""
        b = self.drive("human", seconds=60)
        st = b.strategy
        w = b.world
        from brain.amai_strategy import choose as C
        expect = 2 + int(C.profile_value(st.profile, "Aggression", 0))
        expect += st.race_aggression[w.foe_race][w.tier - 1] if w.foe_race in st.race_aggression else 0
        expect -= 10 if not w.my_heroes else 0            # 人口 5 -> 人口档加 0
        self.assertEqual(st.aggression(w), expect)
        self.assertEqual(b.profile["min_attack_strength"],
                         int(C.profile_value(st.profile, "Minimum Attack Strength", 4)))

    def test_skill_line_follows_amai(self):
        """amai 模式下英雄加点按 AMAI 选的位次和主/次/稀有线，而不是 learned.json。"""
        from brain import races as R
        d = tempfile.mkdtemp()
        b = make_brain(d, engine="amai")
        hero = U(0x400, "Hamg", 4300, -2700)
        hero.hp = hero.hp_max = 700.0
        for t in range(2, 40, 2):
            w = world_of("human", t=float(t), extra=[hero])
            b.step(SimpleNamespace(units=w.units, me=0, gold=800, lumber=400, food_used=10, food_cap=20), float(t))
        st = b.strategy
        st.heroes = ["Hamg", "Hmkg", "Hpal"]
        st.skill_lines = ["rare", "major", "major"]
        b.skill_idx = {}
        learned = []
        b.act.hero_points = lambda addr: 1 if not learned else 0      # 只有一个技能点
        b.act.learn = lambda addr, code: learned.append(code) or True
        saved = dict(R.SKILL_OVERRIDE)
        R.SKILL_OVERRIDE.clear()                          # 这条测的是「AMAI 线优先于录像」；手工指定表另有一条
        try:
            b.job_heroes(b.world)
        finally:
            R.SKILL_OVERRIDE.update(saved)
        self.assertEqual(learned, [R.amai_skill_order("human", "Hamg", 1, "rare")[0]])
        self.assertEqual(learned, ["AHbz"])               # 稀有线第一点是暴风雪
        # ★ 09-18：手工指定表（SKILL_OVERRIDE）比 AMAI 线和录像都优先
        learned.clear()
        b.skill_idx = {}
        b.job_heroes(b.world)
        self.assertEqual(learned, [R.SKILL_OVERRIDE["Hamg"][0]])

    def test_morph_building_upgrade(self):
        """建筑升级（哨塔 -> 箭塔）：have / cost / check 三条路都认得。"""
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d, engine="amai")
            tower = U(0x500, "hwtw", 4600, -2600)
            w = world_of("human", extra=[tower])
            want = Want("morph", "hgtw", 1, 50)
            self.assertEqual(b.have(w, want), 0)
            self.assertEqual(b.cost(w, want), (100 - 30, 70 - 20))
            self.assertIsNone(b.check(w, want))           # 有哨塔 -> 能升
            self.assertEqual(b.check(w, Want("morph", "hctw", 1, 50)), None)
            w2 = world_of("human")
            self.assertIsNotNone(b.check(w2, want))       # 没哨塔 -> 说得出原因

    def test_items_are_not_queued(self):
        b = self.drive("human", seconds=120)
        st = b.strategy
        self.assertEqual([e for e in st.queue.entries if e.kind == "item"], [])
        self.assertTrue(any(k.startswith("物品没接") for k in st.seq.counts))

    def test_human_tier2_combat_army_gets_two_priests_and_sanctum(self):
        """1-090：二本主力成形后不能等到 15 分钟才开始补第一名牧师。"""
        b = self.drive("human", seconds=60)
        st = b.strategy
        st.queue.clear("测试人族支援下限")
        army = [U(0x600 + i, "hfoo", 4200 + i * 25, -2500) for i in range(10)]
        w = world_of("human", food_used=40, food_cap=60, t=240.0,
                     extra=[U(0x505, "hkee", 4352, -2688)] + army)
        from brain.amai_strategy.engine import WorldEnv
        st.pre_round(WorldEnv(b, w, st))
        priests = [e for e in st.queue.entries if e.code == "hmpr"]
        self.assertTrue(any(e.qty == 2 and e.prio == 70 and e.note == "HumanSupportFloor" for e in priests))
        self.assertTrue(any(e.code == "hars" for e in st.queue.entries))  # RefreshNeeded 自动补神秘圣地

    def test_human_support_floor_waits_for_tier2_combat_scale(self):
        b = self.drive("human", seconds=60)
        st = b.strategy
        from brain.amai_strategy.engine import WorldEnv
        for extra, food in (([], 40), ([U(0x505, "hkee", 4352, -2688)], 34)):
            st.queue.clear("测试支援闸门")
            w = world_of("human", food_used=food, food_cap=60, t=240.0, extra=extra)
            st.human_support_builder(WorldEnv(b, w, st))
            self.assertFalse([e for e in st.queue.entries if e.code == "hmpr"])

    def test_strategy_change_when_enemy_strong(self):
        """人口 < 85 且最强的敌人 >= 25 时才检查换战略；换了就清队列、重跑 init_strategy。"""
        foes = [U(0x800, "hcas", -4352, 2944, owner=1)] + [U(0x810 + i, "hkni", -4300 + i * 30, 2900, owner=1) for i in range(8)]
        b = self.drive("human", seconds=200, enemies=foes)
        st = b.strategy
        before = st.strategy
        st.x = 13                                          # 直接把倒计时推过检查点
        w = world_of("human", t=300.0, enemies=foes)
        b.step(SimpleNamespace(units=w.units, me=0, gold=w.gold, lumber=w.lumber,
                               food_used=w.food_used, food_cap=w.food_cap), 300.0)
        self.assertGreaterEqual(st.counts.get("检查换战略", 0), 1)
        if st.strategy != before:
            self.assertTrue(st.history)
            self.assertEqual(st.history[-1][1], before)

    def test_no_change_when_enemy_weak(self):
        b = self.drive("human", seconds=60)
        st = b.strategy
        st.x = 13
        w = world_of("human", t=120.0)                     # 对面只有一座城堡 -> 强度 0
        b.step(SimpleNamespace(units=w.units, me=0, gold=w.gold, lumber=w.lumber,
                               food_used=w.food_used, food_cap=w.food_cap), 120.0)
        self.assertGreaterEqual(st.counts.get("不换战略·敌人还不够强", 0), 1)


if __name__ == "__main__":
    unittest.main()

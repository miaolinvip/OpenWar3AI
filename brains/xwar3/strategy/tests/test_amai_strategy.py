# -*- coding: utf-8 -*-
"""AMAI 战略层的数据、表达式、强度、选择（brain/amai_strategy 的 data/expr/strength/choose）。

期望值全部是按表和公式算出来的：AMAI 源码在 docs/amai_strategy_spec.md 里有行号，
数值来自 brain/amai_data/*.json（StandardUnits、Strategy.txt、Heroes.txt、GlobalSettings）。
随机全部用脚本化的假 rng，不靠种子。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import gamedata as G                       # noqa: E402
from brain.amai_strategy import choose as C           # noqa: E402
from brain.amai_strategy import data as D             # noqa: E402
from brain.amai_strategy import strength as S         # noqa: E402
from brain.amai_strategy.expr import Evaluator, jass_div  # noqa: E402


class SeqRng:
    """脚本化的随机：按顺序吐出给定的值，用完了退回下界。"""

    def __init__(self, values=()):
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0) if self.values else a

    def uniform(self, a, b):
        return a


class FakeEnv:
    """choose/expr 用到的那些取值。"""

    def __init__(self, **kw):
        self.d = dict(counts={}, done={}, upgrades={}, gold=500, wood=200, tier=1, food_used=0,
                      elapsed=300.0, foe="undead", enemy={}, ally={}, heroes=["Hamg", "Hmkg", "Hpal"],
                      skill_lines=["major", "minor", "rare"], mines=1)
        self.d.update(kw)
        self.missed = []

    def town_count(self, code, only_done=False, town=-1):
        if not code:
            return 0
        src = self.d["done"] if only_done else self.d["counts"]
        return sum(int(src.get(c, 0)) for c in [code] + D.count_alts(code))

    def unit_count(self, code, only_done=False, town=-1):
        src = self.d["done"] if only_done else self.d["counts"]
        return int(src.get(code, 0)) if code else 0

    def upgrade_level(self, code):
        return int(self.d["upgrades"].get(code, 0))

    def gold(self):
        return self.d["gold"]

    def wood(self):
        return self.d["wood"]

    def tier(self):
        return self.d["tier"]

    def food_used(self):
        return self.d["food_used"]

    def elapsed(self):
        return self.d["elapsed"]

    def mines_owned(self):
        return self.d["mines"]

    def has_expanded(self):
        return self.d["mines"] > 1

    def rush_factor(self):
        return 8000

    def enemy(self, cat):
        return self.d["enemy"].get(cat, 0.0)

    def ally(self, cat):
        return self.d["ally"].get(cat, 0.0)

    def c_enemy_total(self):
        return 1

    def c_ally_total(self):
        return 0

    def c_enemy(self, race):
        return 1 if race == self.d["foe"] else 0

    def c_ally(self, race):
        return 0

    def c_enemy_random(self):
        return 0

    def is_1on1(self):
        return True

    def is_ffa(self):
        return False

    def hero(self, n):
        hs = self.d["heroes"]
        return hs[n - 1] if 0 < n <= len(hs) else None

    def var(self, name):
        if name == "tier":
            return self.tier()
        if name.startswith("enemy_"):
            return self.enemy(name[6:])
        if name == "nearest_enemy":
            return "nearest_enemy"
        if name in ("towerrush", "militia_expanding"):
            return False
        return 0

    def var_index(self, name, index):
        if name == "c_enemy":
            return self.c_enemy(D.RACES[int(index) - 1])
        if name == "hero_skills":
            return C.SKILL_LINES.index(self.d["skill_lines"][int(index) - 1])
        return 0

    def const(self, name):
        return {"RACE_UNDEAD": "undead", "RACE_NIGHTELF": "nightelf", "HARASS_TARGET_PEONS": 0,
                "HERO_SKILL_MINOR": 1}.get(name, 0)

    def map_flag(self, name):
        return False

    def player_race(self, node):
        return self.d["foe"]

    def player_unit_count(self, p, code):
        return 0

    def random_int(self, a, b):
        return a

    def miss(self, key):
        self.missed.append(key)


HUMAN = D.AmaiData("human")


class TestData(unittest.TestCase):
    def test_needed_chain(self):
        self.assertEqual(D.needed("hkni"), ("hbar", "hcas", "hbla"))        # 兵营 / 城堡 / 铁匠铺
        self.assertEqual(D.needed("hgtw")[0], "hwtw")
        self.assertEqual(D.is_upgraded_from("hgtw"), "hwtw")                # 箭塔由哨塔升级
        self.assertEqual(D.is_upgraded_from("hkee"), "htow")
        self.assertIsNone(D.is_upgraded_from("hbar"))

    def test_count_alternates(self):
        self.assertEqual(D.count_alts("htow"), ["hkee", "hcas"])            # 数主城时二本三本一起数
        self.assertEqual(D.count_alts("hpea"), ["hmil"])                    # 农民 + 民兵
        self.assertEqual(D.count_alts("hwtw"), ["hgtw", "hctw", "hatw"])
        # 请求"把哨塔升成箭塔"时，同一条升级链上的形态要排除，别把塔数重了
        self.assertEqual(D.count_alts("hwtw", exclude="hgtw"), ["hctw", "hatw"])

    def test_cost_uses_increment_for_upgrades(self):
        self.assertEqual(G.cost("hkee"), (705, 415))                        # units.json 是累计价
        self.assertEqual(D.cost("hkee"), (705 - 385, 415 - 205))            # 真实要花的是差价
        self.assertEqual(D.cost("hgtw"), (100 - 30, 70 - 20))
        self.assertEqual(D.cost("hfoo"), G.cost("hfoo"))

    def test_buy_types(self):
        self.assertEqual(D.buy_type("hfoo"), D.BT["UNIT"])
        self.assertEqual(D.buy_type("hbar"), D.BT["BUILDING"])
        self.assertEqual(D.buy_type("Hamg"), D.BT["HERO"])
        self.assertEqual(D.buy_type("Rhde"), D.BT["UPGRADE"])
        self.assertEqual(D.buy_type("Rhme"), D.BT["ML_UPGRADE"])
        self.assertTrue(D.buy_type("nzep") > D.BT["RACIAL_ITEM"])           # 中立：我们买不了

    def test_settings_merged(self):
        self.assertEqual(HUMAN.setting_int("farm_prio"), 400)
        self.assertEqual(HUMAN.setting_int("peon_prio"), 280)
        self.assertEqual(HUMAN.setting_int("rp_strat_normal"), 250)         # 全局设置
        self.assertEqual(HUMAN.setting_int("fav_hero_add"), 20)             # A15：写了两次，后写的生效
        self.assertTrue(HUMAN.setting("race_militia_expansion_available"))
        self.assertEqual(HUMAN.setting("race_simple_melee"), "hfoo")
        self.assertEqual(HUMAN.setting("race_advanced_melee"), "hkni")
        self.assertEqual(D.AmaiData("undead").setting_int("ghoul_prio"), 250)

    def test_racial_units(self):
        self.assertEqual(HUMAN.halls, ["htow", "hkee", "hcas"])
        self.assertEqual(HUMAN.racial("racial_peon"), "hpea")
        self.assertEqual(D.AmaiData("undead").racial("racial_expansion"), "ugol")
        self.assertEqual(D.AmaiData("nightelf").racial("racial_farm"), "emow")
        self.assertEqual(HUMAN.hall_tier("hkee"), 2)
        self.assertEqual(HUMAN.hall_tier("hbar"), 0)

    def test_strategies_and_skills(self):
        self.assertEqual(HUMAN.strategy_names[:3], ["Rifle", "NormalHuman", "Copters"])   # 按 strat_id 排
        self.assertEqual(HUMAN.strategy("NormalHuman")["base rp"], 30)
        self.assertEqual(HUMAN.race_aggression()["human"], (1, 3, 5))       # RaceAggression.txt 第 2 行
        self.assertEqual(HUMAN.skill_codes("Hamg", 1, "major")[:3], ["AHwe", "AHab", "AHwe"])
        self.assertEqual(HUMAN.skill_codes("Hamg", 1, "rare")[0], "AHbz")   # 稀有线一级就点暴风雪


class TestExpr(unittest.TestCase):
    def setUp(self):
        self.env = FakeEnv(counts={"hfoo": 7, "hbar": 1}, done={"hfoo": 7, "hbar": 1}, upgrades={"Rhme": 2})
        self.ev = Evaluator(self.env)

    def test_jass_integer_division(self):
        self.assertEqual(jass_div(7, 2), 3)
        self.assertEqual(jass_div(-7, 2), -3)          # 向零截断，不是向下取整
        self.assertEqual(jass_div(7.0, 2), 3.5)
        self.assertEqual(jass_div(5, 0), 0)

    def test_min_with_town_count(self):
        node = {"fn": "Min", "args": [{"lhs": {"fn": "TownCountDone", "code": "hfoo", "name": "FOOTMAN"},
                                       "op": "div", "rhs": 5}, 1]}
        self.assertEqual(self.ev.int_value(node), 1)   # Min(7/5, 1) = Min(1, 1)
        self.env.d["done"]["hfoo"] = 3
        self.assertEqual(self.ev.int_value(node), 0)   # Min(3/5, 1) = 0

    def test_conditions(self):
        gold_gt = {"lhs": {"fn": "GetGold"}, "op": "gt", "rhs": 400}
        self.assertTrue(self.ev.truth(gold_gt))
        self.assertFalse(self.ev.truth({"arg": gold_gt, "op": "not"}))
        self.assertTrue(self.ev.truth({"args": [gold_gt, {"fn": "HasExpanded"}], "op": "or"}))
        self.assertFalse(self.ev.truth({"args": [gold_gt, {"fn": "HasExpanded"}], "op": "and"}))
        self.assertEqual(self.ev.int_value({"fn": "GetUpgradeLevel", "code": "Rhme", "name": "UPG_MELEE"}), 2)

    def test_race_and_hero_comparisons(self):
        cond = {"lhs": {"args": [{"var": "nearest_enemy"}], "fn": "GetPlayerRace"}, "op": "ne",
                "rhs": {"const": "RACE_UNDEAD", "jass": "ConvertRace(3)"}}
        self.assertFalse(self.ev.truth(cond))          # 对手就是亡灵
        self.env.d["foe"] = "orc"
        self.assertTrue(self.ev.truth(cond))
        self.assertTrue(self.ev.truth({"lhs": {"hero": 1}, "op": "eq", "rhs": {"code": "Hamg", "unit": "ARCHMAGE"}}))
        self.assertTrue(self.ev.truth({"lhs": {"index": 2, "var": "hero_skills"}, "op": "eq",
                                       "rhs": {"const": "HERO_SKILL_MINOR", "value": 1}}))   # 第二英雄是次线

    def test_c_enemy_index(self):
        node = {"index": {"const": "R_ELF", "value": 1}, "var": "c_enemy"}
        self.assertEqual(self.ev.int_value(node), 0)
        self.env.d["foe"] = "nightelf"
        self.assertEqual(self.ev.int_value(node), 1)

    def test_real_statement_from_json(self):
        """直接拿人族 global_build 里那条 BuildAdvUpgr2(哨塔数 + 对面是暗夜) 的参数求值。"""
        stmts = HUMAN.global_build(2)
        node = next(s for s in stmts if s.get("fn") == "BuildAdvUpgr2"
                    and (s.get("args") or {}).get("upgid", {}).get("code") == "Rhse")
        env = FakeEnv(counts={"hwtw": 2}, done={"hwtw": 2}, foe="nightelf")
        self.assertEqual(Evaluator(env).int_value(node["args"]["unitcount"]), 3)   # 2 座哨塔 + 1 个暗夜敌人


class TestStrength(unittest.TestCase):
    def test_category_weights(self):
        counts = {"hfoo": 2, "hrif": 1}
        self.assertEqual(S.category_strength(counts, "normal", HUMAN, 100.0), 4.0)       # 步兵 normal 2 × 2
        self.assertEqual(S.category_strength(counts, "heavyarmor", HUMAN, 100.0), 4.0)
        self.assertEqual(S.category_strength(counts, "piercing", HUMAN, 100.0), 3.0)     # 火枪手 piercing 3
        self.assertEqual(S.category_strength(counts, "mediumarmor", HUMAN, 100.0), 3.0)

    def test_prediction_rules(self):
        """看到铁匠铺就提前把"穿刺"威胁算上（0~20 个 ai_time 内 +5）；过了窗口就没了。"""
        counts = {"hrif": 1, "hbla": 1}

        def pred(cond, cc):
            from brain.amai_strategy.engine import _PredEnv
            return Evaluator(_PredEnv(cc)).truth(cond)

        self.assertEqual(S.category_strength(counts, "piercing", HUMAN, 10.0, lambda c, cc=counts: pred(c, cc)), 8.0)
        self.assertEqual(S.category_strength(counts, "piercing", HUMAN, 30.0, lambda c, cc=counts: pred(c, cc)), 3.0)
        # "normal +8"那条是恒真规则，0~30 个 ai_time 内一直加
        self.assertEqual(S.category_strength({}, "normal", HUMAN, 10.0, lambda c, cc={}: pred(c, cc)), 8.0)

    def test_unit_and_player_strength(self):
        from types import SimpleNamespace

        def U(addr, t, hp=None):
            m = float(G.stat(t, "hp", 100))
            return SimpleNamespace(addr=addr, type=t, hp=hp if hp is not None else m, hp_max=m, x=0.0, y=0.0, owner=1)

        # 步兵：(人口 2 + 等级 2)/2 = 2；火枪手：(3 + 2)/2 = 2.5 -> 按作者意图四舍五入 3（A5，AMAI 原版是 2）
        self.assertEqual(S.unit_strength_ex(U(1, "hfoo")), 2)
        self.assertEqual(S.unit_strength_ex(U(2, "hrif")), 3)
        self.assertEqual(S.hero_strength(1), 3)          # 2 + 等级
        self.assertEqual(S.hero_strength(6), 11)         # 6 级 +3（大招）
        units = [U(1, "hfoo"), U(2, "hfoo"), U(3, "Hamg"), U(4, "hpea"), U(5, "hbar")]
        # 2 个步兵 4 + 大法师（1 级 3，血满）3 + 大法师的"bonus hero strength" 2 = 9；农民和建筑不算
        self.assertEqual(S.player_strength(units, {3: 1}), 9.0)

    def test_tracker_update_without_noise(self):
        tracker = S.StrengthTracker(HUMAN, area_uncertainty=5, rng=None)
        tracker.update({1: {"hfoo": 2}, 0: {"hrif": 1}}, enemies={1}, allies={0}, ai_time=100.0)
        self.assertEqual(tracker.enemy["normal"], 4.0)
        self.assertEqual(tracker.ally["piercing"], 3.0)
        self.assertEqual(tracker.enemy["air"], 0.0)


class TestChoose(unittest.TestCase):
    def test_base_rp_by_name(self):
        """A1 修正：base rp 按战略名给（AMAI 原版按表行号写，四族都错位）。
        1v1 那一列也会加上去，所以期望 = base rp + 1on1 bonus。"""
        env = FakeEnv(foe=None)
        rp = C.strategy_rp(HUMAN, env, {"codes": {}}, mid_game=False)
        by_name = dict(zip(HUMAN.strategy_names, rp))
        for s in HUMAN.strategies:
            name = s["Strategy Function Name"]
            self.assertEqual(by_name[name], max(int(s["base rp"]) + int(s["1on1 bonus"]), 0), name)
        self.assertEqual(int(HUMAN.strategy("NormalHuman")["base rp"]), 30)
        self.assertEqual(int(HUMAN.strategy("BarrackMix")["base rp"]), 40)
        self.assertEqual(int(HUMAN.strategy("Copters")["base rp"]), 0)     # base 0，但 1v1 那一列给了 10

    def test_enemy_bonus_and_fav_unit(self):
        env = FakeEnv(foe="undead")
        base = dict(zip(HUMAN.strategy_names, C.strategy_rp(HUMAN, FakeEnv(foe=None), {"codes": {}}, False)))
        with_foe = dict(zip(HUMAN.strategy_names, C.strategy_rp(HUMAN, env, {"codes": {}}, False)))
        for s in HUMAN.strategies:
            name = s["Strategy Function Name"]
            self.assertEqual(with_foe[name], max(base[name] + int(s["UNDEAD enemy bonus"]), 0), name)
        # 人格偏好兵种 == 战略的 key_unit -> +20
        fav = {"codes": {"Favorite HUMAN Unit": "hgry"}}
        with_fav = dict(zip(HUMAN.strategy_names, C.strategy_rp(HUMAN, FakeEnv(foe=None), fav, False)))
        gryph_key = (HUMAN.strategy("Gryph")["codes"] or {}).get("key_unit")
        self.assertEqual(gryph_key, "hgry")
        self.assertEqual(with_fav["Gryph"], base["Gryph"] + 20)

    def test_mid_game_bonus_is_one_third(self):
        """中途换战略时先 ×3 再 ÷3：base rp 不变，各种加分只算三分之一。"""
        env = FakeEnv(foe="undead")
        mid = dict(zip(HUMAN.strategy_names, C.strategy_rp(HUMAN, env, {"codes": {}}, mid_game=True)))
        s = HUMAN.strategy("MassSp")            # 对亡灵 -5
        self.assertEqual(int(s["UNDEAD enemy bonus"]), -5)
        self.assertEqual(mid["MassSp"], (24 * 3 - 5) // 3)

    def test_remove_non_beginning_and_small_map(self):
        rp = [10] * len(HUMAN.strategies)
        kept = C.remove_non_beginning(HUMAN, rp)
        by_name = dict(zip(HUMAN.strategy_names, kept))
        self.assertEqual(by_name["Mortars"], 0)          # beginning_strat = false
        self.assertEqual(by_name["NormalHuman"], 10)
        small = C.small_map_add(HUMAN, [0] * len(HUMAN.strategies), players=2, map_size=2)
        tier1 = [s["Strategy Function Name"] for s in HUMAN.strategies if int(s["main_strategy_tier"]) == 1]
        self.assertTrue(tier1)
        self.assertEqual(dict(zip(HUMAN.strategy_names, small))[tier1[0]], 20)
        self.assertEqual(C.small_map_add(HUMAN, [0] * len(HUMAN.strategies), 4, 2), [0] * len(HUMAN.strategies))

    def test_roll_weighted(self):
        self.assertEqual(C.roll_weighted(["a", "b", "c"], [10, 0, 5], SeqRng([10])), "a")
        self.assertEqual(C.roll_weighted(["a", "b", "c"], [10, 0, 5], SeqRng([11])), "c")
        self.assertIsNone(C.roll_weighted(["a"], [0], SeqRng([1])))

    def test_determine_counter(self):
        """t[类] = int(max(enemy - ally_克制1/20 - ally_克制2/20, 0) × 10)；< 3 的清零。"""
        enemy = {c: 0.0 for c in D.CATEGORIES}
        ally = {c: 0.0 for c in D.CATEGORIES}
        enemy["air"] = 5.0
        ally["piercing"] = 20.0                 # air 的克制项是 piercing：5 - 1 = 4 -> 40
        idx, blind, t = C.determine_counter(HUMAN, enemy, ally, None, 1000, SeqRng([1]))
        self.assertEqual((idx, blind, t[0]), (0, False, 40))
        # 惯性：上一次也选 air -> +strat_persistence/20 = +50
        idx2, _b, t2 = C.determine_counter(HUMAN, enemy, ally, 0, 1000, SeqRng([1]))
        self.assertEqual((idx2, t2[0]), (0, 90))
        # 什么都不知道 -> 随便挑一类，并标记 no_counter
        zero = {c: 0.0 for c in D.CATEGORIES}
        idx3, blind3, _t3 = C.determine_counter(HUMAN, zero, zero, None, 1000, SeqRng([7]))
        self.assertEqual((idx3, blind3), (7, True))

    def test_build_anti_weighted(self):
        entries = [("hrif", 29), ("hfoo", 1)]
        self.assertEqual(C.build_anti(entries, SeqRng([0])), "hrif")
        self.assertEqual(C.build_anti(entries, SeqRng([29])), "hrif")
        self.assertEqual(C.build_anti(entries, SeqRng([30])), "hfoo")
        self.assertIsNone(C.build_anti([], SeqRng([1])))

    def test_key_add_time_mult(self):
        self.assertEqual(C.key_time_mult(120.0, 8), 24)      # 开局 ×3
        self.assertEqual(C.key_time_mult(1200.0, 8), 8)      # 20 分钟后 ×1
        self.assertEqual(C.key_time_mult(660.0, 8), 16)      # 中间线性

    def test_key_building_add(self):
        env = FakeEnv(counts={"hbar": 1}, done={"hbar": 1})
        self.assertEqual(C.key_building_add(HUMAN, env, "hbar"), 8)       # 建成 1 座
        env2 = FakeEnv(counts={"hbar": 2}, done={"hbar": 2})
        self.assertEqual(C.key_building_add(HUMAN, env2, "hbar"), 10)     # 建成 2 座以上
        env3 = FakeEnv(counts={"hbar": 1}, done={})
        self.assertEqual(C.key_building_add(HUMAN, env3, "hbar"), 9)      # A16：在建的比建成的分还高
        self.assertEqual(C.key_building_add(HUMAN, FakeEnv(), "hbar"), 0)
        self.assertEqual(C.key_upgrade_add(HUMAN, FakeEnv(upgrades={"Rhme": 2}), "Rhme"), 7)

    def test_strategy_change_scores(self):
        """当前战略拿惯性分（人格 1000 + 3×人口），别的战略要超过"当前不含惯性的分 × 1.05"才留得下来。"""
        env = FakeEnv(foe="undead", food_used=20, enemy={"air": 10.0}, elapsed=600.0)
        scores = C.strategy_change_scores(HUMAN, env, {"codes": {}}, "NormalHuman", 1000)
        self.assertGreaterEqual(scores["NormalHuman"], 1000)
        # 被 nullify 清零的那些确实是 0，而且没有负分
        self.assertTrue(all(v >= 0 for v in scores.values()))
        self.assertTrue(any(v == 0 for v in scores.values()))

    def test_pick_changed_strategy_ties_prefer_larger_index(self):
        scores = {n: 0 for n in HUMAN.strategy_names}
        scores["Rifle"] = 10          # strat_id 0
        scores["Knight"] = 10         # strat_id 12
        pick = C.pick_changed_strategy(HUMAN, scores, SeqRng([1]))
        self.assertEqual(pick, "Knight")       # 同分时下标大的排前面（插入排序的效果）

    def test_calc_strat_timer(self):
        self.assertEqual(C.calc_strat_timer(30, SeqRng([0])), -180)
        self.assertEqual(C.calc_strat_timer(30, SeqRng([30])), -150)

    def test_pick_heroes(self):
        """只在本族 4 个英雄里选（执行层不会去酒馆买），三个位次各不相同。"""
        env = FakeEnv(foe="undead")
        heroes = C.pick_heroes(HUMAN, env, {"codes": {}}, "NormalHuman", SeqRng([1, 1, 1]))
        self.assertEqual(len(heroes), 3)
        self.assertEqual(len(set(heroes)), 3)
        race_codes = {(r.get("codes") or {}).get("Hero") for r in HUMAN.hero_rows() if not r.get("neutral")}
        self.assertTrue(set(heroes) <= race_codes)

    def test_fav_hero_bonus(self):
        """人格偏好英雄 +20：roll 落在最前面时选中的就是它（rp 按 Heroes.txt 行序累加）。"""
        env = FakeEnv(foe=None)
        prof = {"codes": {"Favorite HUMAN Hero": "Hpal"}}
        rows = [r for r in HUMAN.hero_rows() if not r.get("neutral")]
        codes = [(r.get("codes") or {}).get("Hero") for r in rows]
        self.assertEqual(codes, ["Hamg", "Hmkg", "Hpal", "Hblm"])
        # 大法师 base 41 + 第一英雄加分 10 = 51；roll 52 落到山丘之王
        self.assertEqual(C.pick_heroes(HUMAN, env, prof, "NormalHuman", SeqRng([52, 1, 1]))[0], "Hmkg")
        self.assertEqual(C.pick_heroes(HUMAN, env, prof, "NormalHuman", SeqRng([1, 1, 1]))[0], "Hamg")

    def test_roll_skill_line(self):
        self.assertEqual(C.roll_skill_line(HUMAN, SeqRng([10])), "rare")    # perc <= 稀有 10
        self.assertEqual(C.roll_skill_line(HUMAN, SeqRng([11])), "minor")
        self.assertEqual(C.roll_skill_line(HUMAN, SeqRng([40])), "minor")   # perc <= 次 30 + 稀有 10
        self.assertEqual(C.roll_skill_line(HUMAN, SeqRng([41])), "major")

    def test_choose_profile(self):
        p = C.choose_profile(HUMAN, SeqRng([1, 0]))                        # 非稀有（randint(0,9) != 0）
        self.assertIn(p.get("Race"), ("RANDOM", "HUMAN"))
        self.assertEqual(int(p.get("Rare Profile") or 0), 0)
        rare = C.choose_profile(HUMAN, SeqRng([0, 0]))                     # 10% 那一支
        self.assertEqual(int(rare.get("Rare Profile") or 0), 1)


if __name__ == "__main__":
    unittest.main()

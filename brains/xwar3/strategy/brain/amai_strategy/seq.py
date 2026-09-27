# -*- coding: utf-8 -*-
"""建造序列解释器：把 BuildSequence.ai 的一条条 call 变成队列里的请求。

AMAI 的 build_sequence()（races.eai:540）= global_build_sequence() + build_sequence_<当前战略>()，
每约 3×slm 秒跑一遍；init_strategy()（races.eai:527）= global_init_strategy() + init_strategy_<战略>()，
只在开局和换战略时跑一遍。JSON 里 global_build / 各战略都按 tier 部分求值过（tiers["1"|"2"|"3"]），
所以这里只要按当前 tier 取那一份，再对每条语句的 cond 求值。
"""
from __future__ import annotations

from .. import gamedata as G
from . import data as D
from .choose import build_anti
from .expr import Evaluator


class Sequencer:
    """一个战略周期内的解释器状态（反制表、骚扰定义、脚本里的 set 变量）。"""

    def __init__(self, data: D.AmaiData, queue, rng=None):
        self.data = data
        self.queue = queue
        self.rng = rng
        self.anti = {c: [] for c in D.CATEGORIES}
        self.harass_groups: dict = {}          # 组号 -> [(兵种, 数量)]
        self.harass_calls: list = []           # Harass(...) 的参数，只记录不执行
        self.vars: dict = {}                   # 脚本里的 set（只有 distraction_group）
        self.chosen_counter_unit: str | None = None
        self.reset_counter_unit = 1
        self.enable_items = False              # BuildItem：执行层还不会买东西，见 _call 里的注释
        self.counts: dict = {}

    def count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    # ── 跑一段语句 ──
    def run(self, stmts, env) -> None:
        ev = Evaluator(env)
        for s in stmts or []:
            try:
                if not ev.truth(s.get("cond")):
                    continue
                self._call(s, ev, env)
            except Exception as exc:            # noqa: BLE001  一条语句坏了不能停掉整段序列
                self.count(f"语句出错·{s.get('fn')}·{type(exc).__name__}")

    def _call(self, s: dict, ev: Evaluator, env) -> None:
        fn = s.get("fn")
        a = s.get("args") or {}
        self.count(f"语句·{fn}")
        if fn == "set":
            self.vars[s.get("var")] = ev.value(s.get("value"))
            return
        if fn in ("BuildUnit", "BuildFront", "BuildAtMine"):
            loc = {"BuildUnit": "std", "BuildFront": "front", "BuildAtMine": "mine"}[fn]
            self.queue.add("unit", ev.int_value(a.get("qty")), ev.code(a.get("unitid")),
                           ev.int_value(a.get("prio")), env, loc=loc, note=fn)
            return
        if fn == "BuildUpgr":
            self.queue.add("upgrade", ev.int_value(a.get("qty")), ev.code(a.get("unitid")),
                           ev.int_value(a.get("prio")), env, note=fn)
            return
        if fn == "BuildItem":
            # 买物品这条路没做（执行层只会让 mod_scavenge 捡地上的）。进队列也只会每轮被退一次、
            # 还跟着老化爬到所有请求前面去，所以只记数不入队；真接上的时候把 enable_items 打开。
            if not self.enable_items:
                self.count(f"物品没接·{ev.code(a.get('unitid'))}")
                return
            self.queue.add("item", ev.int_value(a.get("qty")), ev.code(a.get("unitid")),
                           ev.int_value(a.get("prio")), env, note=fn)
            return
        if fn == "BasicExpansionAM":
            code = ev.code(a.get("unitid"))
            if ev.truth(a.get("build_it")) and code and env.halls_completed(code):
                self.queue.add("expand", env.town_count(code, only_done=False) + 1, code,
                               ev.int_value(a.get("prio")), env, note="BasicExpansionAM")
            return
        if fn in ("DefendTownsDone", "DefendTownsFrontDone", "DefendTowns", "DefendTownsFront"):
            self._defend_towns(fn, ev, a, env)
            return
        if fn == "DefendTownsCond":
            self._defend_towns_cond(ev, a, env)
            return
        if fn == "basic_melee":
            self._basic_melee(ev.int_value(a.get("food")), ev.int_value(a.get("prio")), env)
            return
        if fn == "SetBuildReact":
            self._build_react(ev, a, env)
            return
        if fn == "BuildAdvUpgr":
            self._adv_upgr(ev, a, env)
            return
        if fn == "BuildAdvUpgr2":
            self._adv_upgr2(ev, a, env)
            return
        if fn == "AddBlock":
            self.queue.add_block(ev.int_value(a.get("req_qty")), ev.code(a.get("req_type")),
                                 ev.truth(a.get("only_done")), ev.int_value(a.get("allow_qty")),
                                 ev.code(a.get("allow_type")), ev.int_value(a.get("expire_time")))
            return
        if fn == "SetTierBlock":
            self.queue.set_tier_block(ev.int_value(a.get("tier")), ev.value(a.get("unitper")),
                                      ev.int_value(a.get("foodlimit")), ev.truth(a.get("expansionblock")))
            return
        if fn == "AddHarass":
            code = ev.code(a.get("unitid"))
            if code:
                self.harass_groups.setdefault(ev.int_value(a.get("groupnum")), []).append((code, ev.int_value(a.get("qty"))))
            return
        if fn == "Harass":
            self.harass_calls.append({k: (ev.value(v) if k not in ("cond",) else ev.truth(v)) for k, v in a.items()})
            return
        if fn == "ResetDynamicSystem":
            self.anti = {c: [] for c in D.CATEGORIES}
            self.reset_counter_unit = 1
            return
        if fn.startswith("AddUnitToAnti"):
            cat = fn[len("AddUnitToAnti"):]
            self._add_anti(cat, ev.code(a.get("u")), ev.int_value(a.get("percent")))
            return
        if fn == "DynamicBuildUnit":
            self.dynamic_build_unit(ev.int_value(a.get("prio")), env)
            return
        self.count(f"没接的函数·{fn}")

    # ── 各函数 ──
    def _defend_towns(self, fn: str, ev: Evaluator, a: dict, env) -> None:
        """DefendTownsEx（common.eai:10843）：每个"有主城且有矿"的分矿各自造到 qty 个。
        *Done 版本只数已完成的；*Front 版本连主基地也算（主基地那份建在前沿点）。"""
        only_done = fn.endswith("Done")
        at_front = "Front" in fn
        code = ev.code(a.get("unitid"))
        qty, prio = ev.int_value(a.get("qty")), ev.int_value(a.get("prio"))
        if not code:
            return
        for town in env.towns(include_main=at_front):
            if env.town_count(code, only_done=only_done, town=town["id"]) >= qty:
                continue
            if town["main"] and at_front:
                self.queue.add("unit", qty, code, prio, env, loc="front", note=fn)
            else:
                self.queue.add("unit", qty, code, prio, env, town=town["id"], note=fn)

    def _defend_towns_cond(self, ev: Evaluator, a: dict, env) -> None:
        """⚠ A12：源码写的是 `town_dist >= min_dist and town_dist >= max_dist`（上限失效），
        这里按作者意图改成 min_dist <= 离家距离 <= max_dist。矿里还剩多少金我们读不到，min_gold 当成满足。"""
        code = ev.code(a.get("unitid"))
        if not code:
            return
        qty, prio = ev.int_value(a.get("qty")), ev.int_value(a.get("prio"))
        lo, hi = ev.int_value(a.get("min_dist")), ev.int_value(a.get("max_dist"))
        expansion = self.data.racial("racial_expansion")
        limit = qty * max(env.town_count(expansion, only_done=True), 1)
        if env.town_count(code, only_done=True) >= limit:
            return
        for town in env.towns(include_main=False):
            if lo <= town["dist"] <= hi:
                self.queue.add("unit", qty, code, prio, env, town=town["id"], note="DefendTownsCond")

    def _basic_melee(self, food: int, prio: int, env) -> None:
        """basic_melee / FoodPoolAM（races.eai:490、common.eai:10929）：能出高级近战就把这份人口全给它，否则全给低级兵。"""
        simple = self.data.setting("race_simple_melee")
        advanced = self.data.setting("race_advanced_melee")
        if advanced and env.can_build(advanced):
            use2 = max(G.food(advanced), 1)
            qty = (food - G.food(simple) * env.town_count(simple, only_done=False)) // use2
            self.queue.add("unit", qty, advanced, prio, env, note="basic_melee")
        elif simple:
            use1 = max(G.food(simple), 1)
            qty = (food - (G.food(advanced) if advanced else 0) * env.town_count(advanced, only_done=False)) // use1
            self.queue.add("unit", qty, simple, prio, env, note="basic_melee")

    def _build_react(self, ev: Evaluator, a: dict, env) -> None:
        """SetBuildReact（common.eai:10406）：敌方强度低 -> 这份人口全给 unit1，高 -> 全给 unit2，中间线性分；
        两种兵的优先级交错排（BuildUnit 会把第 k 个拆成 prio + 2(n-k)）。"""
        from .choose import lerp
        food = ev.int_value(a.get("food"))
        u1, u2 = ev.code(a.get("unit1")), ev.code(a.get("unit2"))
        min1, min2 = ev.int_value(a.get("min1")), ev.int_value(a.get("min2"))
        s1, s2 = ev.value(a.get("strength1")), ev.value(a.get("strength2"))
        prio = ev.int_value(a.get("prio"))
        strength = ev.value(a.get("enemy_strength"))
        f1 = max(G.food(u1) if u1 else 1, 1)
        f2 = max(G.food(u2) if u2 else 1, 1)
        num1 = int(lerp(s1, s2, float(food), 0.0, strength)) // f1
        num2 = (food - num1 * f1) // f2
        n1, n2 = min1 + num1, min2 + num2
        if n1 >= n2:
            p1, p2 = prio - n1 * 2, prio - 1 + (n2 - n1) * 2
        else:
            p1, p2 = prio + (n1 - n2) * 2, prio - n2 * 2 - 1
        if n1 > 0 and u1:
            self.queue.add("unit", n1, u1, p1, env, note="SetBuildReact")
        if n2 > 0 and u2:
            self.queue.add("unit", n2, u2, p2, env, note="SetBuildReact")

    def _adv_upgr(self, ev: Evaluator, a: dict, env) -> None:
        """BuildAdvUpgr（common.eai:10330）：兵越多优先级越高（线性插值），到 maxunits 就是满优先级。"""
        from .choose import lerp
        start = ev.int_value(a.get("starttier"))
        tier = env.tier()
        if tier < start:
            return
        count = ev.int_value(a.get("unitcount"))
        if count <= 0:
            return
        qty = ev.int_value(a.get("qty"))
        maxunits = ev.int_value(a.get("maxunits"))
        prio = ev.int_value(a.get("prio")) + (tier - start) * ev.int_value(a.get("tierprio"))
        level = min(qty, tier - start + 1)
        self.queue.add("upgrade", level, ev.code(a.get("upgid")) or ev.code(a.get("unitid")),
                       int(lerp(0, maxunits, 1, prio, count)), env, note="BuildAdvUpgr")

    def _adv_upgr2(self, ev: Evaluator, a: dict, env) -> None:
        """BuildAdvUpgr2（common.eai:10353）：按兵数掷骰子决定这一轮要不要下研究令；
        进了队列就靠老化留着，所以兵够多最后基本都会研究。"""
        start = ev.int_value(a.get("starttier"))
        tier = env.tier()
        if tier < start:
            return
        count = ev.int_value(a.get("unitcount"))
        per = ev.value(a.get("unitper"))
        if count <= per:
            return
        per = per or 1
        cap = ev.int_value(a.get("max"))
        chance = min(int(10 * count / per), cap)
        r = self.rng
        roll1 = r.randint(1, 100) if r else 100
        roll2 = r.randint(1, 100) if r else 100
        if roll1 <= chance or (count > per * 2 and roll2 <= cap):
            qty = ev.int_value(a.get("qty"))
            level = min(qty, tier - start + 1)
            self.queue.add("upgrade", level, ev.code(a.get("upgid")), ev.int_value(a.get("prio")) + (tier - start) * 4,
                           env, note="BuildAdvUpgr2")

    def _add_anti(self, cat: str, code: str | None, percent: int) -> None:
        """AddUnitToAnti<类>（common.eai:10536）：权重 <= 0 的不进表（例如依赖升级等级算出来是 0）。"""
        if cat not in self.anti or not code or percent <= 0:
            return
        self.anti[cat].append((code, percent))
        if code == self.chosen_counter_unit and self.reset_counter_unit == 1:
            self.reset_counter_unit = 0

    # ── 反制兵种 ──
    def select_counter_unit(self, counter_index: int) -> str | None:
        """DynamicStrategySelector -> BuildAnti<类>Strength：在这一类的表里按权重抽一个。"""
        cats = list(D.CATEGORIES)
        if not (0 <= counter_index < len(cats)):
            return None
        code = build_anti(self.anti[cats[counter_index]], self.rng)
        if code:
            self.chosen_counter_unit = code
        return code

    def dynamic_build_unit(self, prio: int, env) -> None:
        """DynamicBuildUnitBase（common.eai:10581）：用克制兵种把剩下的人口填满（最多比已有的多 10 个）。"""
        u = self.chosen_counter_unit
        if self.reset_counter_unit == 1 and u:
            self.chosen_counter_unit = None     # 上一轮选的兵种已经不在任何一张表里（多半升本换代了）
            self.count("反制兵种失效·等重抽")
            return
        if not u:
            return
        count = max(100, env.food_cap())
        if D.is_structure(u):
            self.queue.add("unit", 1, u, prio * 2, env, note="DynamicBuildUnit")
            return
        num = int((count - env.food_used()) / max(G.food(u), 1))
        if num > 0 and env.town_count(u, only_done=False) < num:
            self.queue.add("unit", min(num, env.town_count(u, only_done=True) + 10), u, prio, env, note="DynamicBuildUnit")

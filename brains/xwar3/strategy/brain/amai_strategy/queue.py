# -*- coding: utf-8 -*-
"""AMAI 的建造请求队列 + 建造循环（common.eai:10158-10276 SetBuildAllAM/RefreshNeeded、12272 OneBuildLoopAM）。

和 brain/build.py 的 Planner 的关系：Planner 是"每轮给一张愿望单、按优先级走一遍"的无状态规划器；
AMAI 的队列是**有状态**的——条目一旦进队列就留着，每轮优先级 +1，只有造出来/换战略/换反制/换本/每 100 轮才清；
同一条请求按数量拆成 n 条（第 k 个的优先级 p + 2(n-k)）；本轮已经处理过更大数量的同一个 id 就把小的丢掉（max_order）。
这些语义塞不进 Planner 的 `wants -> 决定` 形状，硬塞会改动手写表那条路的行为，所以单开一个队列，
但**输出仍然是 build.Want / build.Decision / build.RoundResult**，runtime 那边的执行和展示一行都不用改。

一条请求的内部 kind 沿用 AMAI 的四类：unit（含建筑/主城升级/英雄）、upgrade、expand、item；
交给执行层时再翻成 runtime 认识的 Want.kind（want_kind()）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import gamedata as G
from ..build import Decision, RoundResult, Want
from . import data as D

BUILT_ALL = "built_all"
BUILT_SOME = "built_some"
NOT_ENOUGH_RES = "not_enough_res"
CANNOT_BUILD = "cannot_build"

PRIO_T_INC = 1          # 每轮老化（Settings.txt prio_t_inc）
PRIO_Q_INC = 2          # 数量拆条（prio_q_inc）
PRIO_N_INC = 2          # 前置加成（prio_n_inc）
MAX_ENTRIES = 400       # AMAI 是 JASS 数组上限 8191；我们一局撑死几十条，给个上限防失控


def want_kind(code: str, data: D.AmaiData) -> str:
    """AMAI 的 unit 请求 -> runtime 认识的 Want.kind。"""
    bt = D.buy_type(code)
    if bt == D.BT["HERO"]:
        return "hero"
    if bt in (D.BT["UPGRADE"], D.BT["ML_UPGRADE"]):
        return "upgrade"
    if D.is_upgraded_from(code):
        return "upgrade_hall" if data.hall_tier(code) else "morph"
    if bt == D.BT["BUILDING"]:
        return "building"
    return "unit"


@dataclass
class Entry:
    kind: str                 # unit / upgrade / expand / item / revive（复活只在当轮插队，不进队列）
    code: str
    qty: int                  # 目标总数（含已有的和在建的）
    prio: int
    town: int = -1            # -1 = 任意城镇；其它值 = 那座分矿大厅的地址
    loc: str = "std"          # std / front / mine
    note: str = ""

    def want(self, data: D.AmaiData) -> Want:
        kind = {"upgrade": "upgrade", "expand": "expand", "item": "item",
                "revive": "revive"}.get(self.kind) or want_kind(self.code, data)
        where = {"std": "base", "front": "front", "mine": "mine"}.get(self.loc, "base")
        if self.town != -1:
            where = f"town:{self.town}"
        return Want(kind, self.code, self.qty, self.prio, where, self.note)


class Upkeep:
    """维护费闸（common.eai:8920-9037）。⚠ A9：源码每轮都把 save time 重置，等于"判了不允许就一直预留"；
    这里按作者意图修正——从第一次判"不允许"起算 save_time（ai_time 单位），过了就不再预留（限量仍在）。"""

    def __init__(self, data: D.AmaiData, rng=None):
        self.rows = []
        for r in data.upkeep:
            save = r.get("Save time")
            if isinstance(save, str):                       # "30+GetRandomInt(0, 60)"
                base = int(save.split("+")[0])
                span = int(save.split("(")[-1].split(",")[-1].strip(" )")) if "GetRandomInt" in save else 0
                save = base + (rng.randint(0, span) if (rng and span) else span // 2)
            self.rows.append(dict(border=int(r["Food value border"]), resistance=int(r["Resistance"]),
                                  mines=int(r["Mines needed"]), save=int(save or 0), gold_change=float(r["GoldChange"])))
        self.allowed = [None] * len(self.rows)              # None=未定 True=允许 False=不允许
        self.since = [None] * len(self.rows)
        self.reserved = 0

    def begin(self, food_used: int) -> None:
        for i, row in enumerate(self.rows):
            if food_used > row["border"]:
                self.allowed[i] = True
                self.since[i] = None
            else:
                self.allowed[i] = None
        self.reserved = 0

    def check(self, afford_food: int, food_used: int, food_cost: int, need_qty: int,
              total_gold: int, mines: int, ai_time: float) -> tuple:
        """返回 (afford_food, 这一条要预留多少金)。"""
        reserve = 0
        for i, row in enumerate(self.rows):
            if food_used + food_cost * need_qty <= row["border"]:
                continue
            if self.allowed[i] is None:
                self.allowed[i] = total_gold > row["resistance"] or mines >= row["mines"]
                if not self.allowed[i] and self.since[i] is None:
                    self.since[i] = ai_time
            if self.allowed[i] is False:
                afford_food = max(row["border"] - food_used, 0) // max(food_cost, 1)
                if self.since[i] is not None and ai_time <= self.since[i] + row["save"]:
                    reserve = max(reserve, row["resistance"])
        return afford_food, reserve


class BuildQueue:
    """一份持久的请求队列。env 见 engine.WorldEnv（取数量、钱、执行层能不能下）。"""

    def __init__(self, data: D.AmaiData, rng=None):
        self.data = data
        self.entries: list = []
        self.blocks: list = []                  # AddBlock
        self.tier_unitlock = {1: 0.0, 2: 0.0, 3: 0.0}
        self.tier_foodlock = {1: 0, 2: 0, 3: 0}
        self.tier_expansionblock = {1: False, 2: False, 3: False}
        self.lock_active = {1: True, 2: True, 3: True}
        self.upgrade_time: dict = {}            # 下过研究令之后 10 个 ai_time 内不再处理
        self.available_time: dict = {}          # 建筑升级令之后的冷却（needed3 == UPGRADED）
        self.upkeep = Upkeep(data, rng)
        self.needed_peons = 0
        self.counts: dict = {}                  # 计数器：无条件累加（先记数，再决定要不要打印）
        self.rounds = 0

    def count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    # ── 队列维护 ──
    def clear(self, why: str = "") -> None:
        """InitBuildArrayAM：换战略 / 换反制 / 换本 / 每 100 轮。"""
        self.entries = []
        self.count(f"清空队列·{why}" if why else "清空队列")

    def init_blocks(self, tier: int) -> None:
        """InitBlockList（common.eai:10087）：清 Block 表，并把**低于当前 tier 的**那几档升本闸参数清零。"""
        self.blocks = []
        for t in range(1, tier + 1):
            if t <= 3:
                self.tier_unitlock[t] = 0.0
                self.tier_foodlock[t] = 0
                self.tier_expansionblock[t] = False

    def add_block(self, req_qty, req_type, only_done, allow_qty, allow_type, expire_time) -> None:
        self.blocks.append(dict(req_qty=int(req_qty), req_type=req_type, only_done=bool(only_done),
                                allow_qty=int(allow_qty), allow_type=allow_type, expire=float(expire_time)))

    def set_tier_block(self, tier: int, unitper: float, foodlimit: int, expansionblock: bool) -> None:
        tier = max(1, int(tier))
        if tier <= 3:
            self.tier_unitlock[tier] = float(unitper)
            self.tier_foodlock[tier] = int(foodlimit)
            self.tier_expansionblock[tier] = bool(expansionblock)

    def block_check(self, qty: int, code: str, env) -> int:
        """BlockListCheck（common.eai:10099）：同一个 allow_type 有多条时取最小的允许数量。"""
        out = qty
        for b in self.blocks:
            if env.ai_time() < b["expire"] and b["allow_type"] == code:
                if env.town_count(b["req_type"], only_done=b["only_done"]) < b["req_qty"]:
                    out = min(out, b["allow_qty"])
        return out

    # ── 加请求（SetBuildAllAM）──
    def add(self, kind: str, qty, code: str | None, prio, env, town: int = -1, loc: str = "std", note: str = "") -> None:
        if not code:
            return
        qty = int(qty)
        prio = int(prio)
        qty = self.block_check(qty, code, env)
        if qty <= 0:
            self.count(f"被 Block 挡住·{code}")
            return
        if self._core(kind, qty, code, town, loc, prio, env, note):
            self._refresh_needed(kind, qty, code, town, loc, prio, env)

    def _core(self, kind, qty, code, town, loc, prio, env, note="") -> bool:
        """SetBuildAllAMCore：已经够了就不插（也不补前置）；已有同 id、数量不少、优先级不低的条目就不重复插；
        数量 > 1 时递归拆一条"少一个、优先级 +2"的（于是第 k 个的优先级 = p + 2(n-k)）。"""
        if not code or qty <= 0:
            return False
        if kind == "upgrade":
            if env.upgrade_level(code) >= qty:
                return False
        elif kind == "item":
            if env.item_count(code) >= qty:
                return False
        else:
            if env.town_count(code, only_done=False, town=town) >= qty:
                return False
            if D.buy_type(code) > D.BT["RACIAL_ITEM"] and not env.neutral_available(code):
                self.count(f"中立买不到·{code}")
                return False
        if len(self.entries) >= MAX_ENTRIES:
            self.count("队列满了")
            return False
        i = 0
        for i, e in enumerate(self.entries):
            if e.prio < prio:
                break
            if e.code == code and e.qty >= qty and e.town == town:
                return True                     # 已经有更强的同类请求：不重复插，但前置还是要补
        else:
            i = len(self.entries)
        self.entries.insert(i, Entry(kind, code, qty, prio, town, loc, note))
        if qty > 1:
            self._core(kind, qty - 1, code, town, loc, prio + PRIO_Q_INC, env, note)
        return True

    def _refresh_needed(self, kind, qty, code, town, loc, prio, env) -> None:
        """RefreshNeeded（common.eai:10216）：自动补前置，前置的优先级比本体高。"""
        bt = D.buy_type(code)
        n1, n2, n3 = D.needed(code)
        inc = PRIO_N_INC
        if bt == D.BT["ML_UPGRADE"]:
            if qty == 1:
                if n1:
                    self._core("unit", self._f_qty(qty, code, town, env), n1, -1, "std", prio + 3 * inc, env)
            elif qty == 2:
                self._core("upgrade", 1, code, town, "std", prio + 2 * inc, env)
                if n2:
                    self._core("unit", 1, n2, -1, "std", prio + 2 * inc, env)
            else:
                self._core("upgrade", 1, code, town, "std", prio + 2 * inc, env)
                self._core("upgrade", 2, code, town, "std", prio + inc, env)
                if n3 and n3 != D.UPGRADED:
                    self._core("unit", 1, n3, -1, "std", prio + inc, env)
        elif bt in (D.BT["HERO"], D.BT["NEUTRAL_HERO"]):
            heroes = env.heroes()
            if code in heroes:
                if n1:
                    self._core("unit", 1, n1, -1, "std", prio + 2 * inc, env)
                slot = heroes.index(code) + 1
                if slot in (2, 3) and not env.hero_built(slot) and n2:
                    self._core("unit", 1, n2, -1, "std", prio + 2 * inc, env)
                if slot == 3 and not env.hero_built(3) and n3 and n3 != D.UPGRADED:
                    self._core("unit", 1, n3, -1, "std", prio + 2 * inc, env)
        else:
            if n1:
                if n3 == D.UPGRADED:
                    # 由 needed1 升级而来（哨塔 -> 箭塔、主城 -> 要塞）：要的是"在已升级的之外再来这么多源建筑"
                    if env.ai_time() >= self.available_time.get(code, -1e9):
                        extra = sum(env.town_count(a, only_done=True, town=town) for a in D.count_alts(n1, exclude=code))
                        self._core("unit", max(0, qty + extra), n1, town, loc, prio + max(inc - 1, 1), env)
                else:
                    self._core("unit", self._f_qty(qty, code, town, env), n1, -1, "std", prio + 3 * inc, env)
            if n2:
                self._core("unit", 1, n2, -1, "std", prio + 2 * inc, env)
            if n3 and n3 != D.UPGRADED:
                self._core("unit", 1, n3, -1, "std", prio + inc, env)
        for t, num, need in D.needed_extra(code):
            self._core("upgrade" if t == "UPGRADE" else "unit", num, need, -1, "std", prio + inc, env)

    # ── 生产建筑要几座（Get_f_qty / GetFactoryQty，common.eai:9982-10061）──
    def _f_qty(self, qty: int, code: str, town: int, env) -> int:
        if D.buy_type(code) != D.BT["UNIT"] or code == self.data.racial("racial_peon"):
            return 1
        if env.hall_upgrading():        # isTieringUp（A4：原版恒 false，这里按作者意图判"正在升本"）
            return 1
        n1, _n2, _n3 = D.needed(code)
        return self._factory_qty(qty - env.town_count(code, only_done=False, town=town), code, n1, env)

    def _factory_qty(self, q: int, code: str, fac: str | None, env) -> int:
        bt = max(int(G.stat(code, "bldtm", 1) or 1), 1)
        g, w = D.cost(code)
        rate = 0.0 if q <= 3 else 1 - 1.0 / q
        g_income = rate * env.gold_income()
        g_need = max(g / bt, 1)
        w_income = rate * (env.wood() * env.gold_unit_percentage() + 10)
        wood_factories = int(w_income / max(w / bt, 1))
        req = max(min(int(g_income / g_need), wood_factories, env.difficulty()), 1)
        if fac and env.town_count(fac, only_done=False) < req and req > 1:
            fg, fw = D.cost(fac)
            while req != 1:
                have = env.town_count(fac, only_done=False)
                if env.gold() > (req - have) * fg * 1.75 and env.wood() > (req - have) * fw * 1.75:
                    return req
                req -= 1
        return req

    # ── 建造循环一轮（OneBuildLoopAM）──
    def plan(self, env, extra: list | None = None) -> RoundResult:
        """extra：不进队列、只在这一轮参与排序的请求（复活英雄——AMAI 每轮由 HeroReviver 重发）。"""
        self.rounds += 1
        res = RoundResult()
        gold, wood = env.gold_available(), env.wood_available()
        food_limit = env.food_limit()
        food_free = food_limit - env.food_used()
        self.upkeep.begin(env.food_used())
        build_free = env.build_free()
        max_order: dict = {}
        blocktierup = False
        expablocktier = False
        kept: list = []
        refresh: list = []
        stopped = False
        tier = env.tier()
        items = [(e, False) for e in self.entries] + [(e, True) for e in (extra or [])]
        items.sort(key=lambda it: -it[0].prio)
        for e, is_extra in items:
            if stopped:
                if not is_extra:
                    kept.append(self._aged(e))
                continue
            qty = self.block_check(e.qty, e.code, env)
            key = (e.code, e.town)
            mo = max_order.get(key, 0)
            if qty <= mo or qty <= 0:
                self.count(f"本轮已处理过更大的请求·{e.code}" if qty <= mo else f"被 Block 挡成 0·{e.code}")
                continue
            max_order[key] = max(mo, qty)
            want = e.want(self.data)
            want.qty = qty
            # 升本闸（SetTierBlock，common.eai:12337）
            if e.kind == "unit" and self._is_hall_above(e.code, tier) \
                    and ((blocktierup and self.lock_active.get(tier, True)) or expablocktier):
                cg, cl = env.cost(want)
                if cg * 4 > gold and cl * 4 > wood:
                    res.skipped.append((want, "升本闸：前面的兵还没出够"))
                    self.count("升本被闸门挡下")
                    kept.append(self._aged(e))
                    refresh.append(e)
                    continue
            if e.kind == "unit" and self._is_hall_above(e.code, tier):
                self.lock_active[tier] = False          # 真正尝试过一次升本，这一级的兵力闸就永久解除
            ret, decided, gold, wood, food_free = self._dispatch(e, want, qty, env, gold, wood, food_free,
                                                                 build_free, res)
            for d in decided:
                res.decisions.append(d)
            if ret == CANNOT_BUILD and not is_extra:
                refresh.append(e)
            # blocktierup：本轮升本请求**前面**处理过的非建筑请求没出够 && 人口没到闸门
            if e.kind == "unit" and not D.is_structure(e.code) and D.buy_type(e.code) != D.BT["HERO"] \
                    and env.town_count(e.code, only_done=False, town=e.town) < e.qty * self.tier_unitlock.get(tier, 0.0) \
                    and env.food_used() < self.tier_foodlock.get(tier, 0):
                blocktierup = True
            if ret == BUILT_SOME and e.kind == "expand" and env.mines_harvested() < 3:
                expablocktier = self.tier_expansionblock.get(tier, False)
            if ret == NOT_ENOUGH_RES and e.kind not in ("item", "upgrade"):
                stopped = True
                res.stopped_at = want
                self.count(f"钱不够停下·{e.code}")
                if not is_extra:
                    kept.append(self._aged(e))
                continue
            if ret in (CANNOT_BUILD, BUILT_SOME, NOT_ENOUGH_RES) or (ret == BUILT_ALL and qty < e.qty):
                if not is_extra:
                    kept.append(self._aged(e))
            elif ret == BUILT_ALL:
                res.done.append(want)
        self.entries = kept
        for e in refresh:                                  # 本轮 CANNOT_BUILD 的，轮末补前置
            self._refresh_needed(e.kind, e.qty, e.code, e.town, e.loc, e.prio, env)
        return res

    @staticmethod
    def _aged(e: Entry) -> Entry:
        return Entry(e.kind, e.code, e.qty, e.prio + PRIO_T_INC, e.town, e.loc, e.note)

    def _is_hall_above(self, code: str, tier: int) -> bool:
        """IsRacialHallId(id, tier+1)：这个码是不是比当前本更高的主城。"""
        t = self.data.hall_tier(code)
        return bool(t) and t >= tier + 1

    # ── 各类请求的执行（StartUnitAM / StartUpgradeAM / StartExpansionAM / StartItem）──
    def _dispatch(self, e: Entry, want: Want, qty: int, env, gold, wood, food_free, build_free, res):
        if e.kind == "upgrade":
            return self._start_upgrade(e, want, qty, env, gold, wood, food_free, build_free, res)
        if e.kind == "expand":
            return self._start_expand(e, want, qty, env, gold, wood, food_free, res)
        if e.kind == "item":
            res.skipped.append((want, "物品还没接（执行层不会买东西）"))
            self.count(f"物品跳过·{e.code}")
            return CANNOT_BUILD, [], gold, wood, food_free
        if e.kind == "revive":
            return self._start_revive(e, want, env, gold, wood, food_free, res)
        return self._start_unit(e, want, qty, env, gold, wood, food_free, build_free, res)

    def _start_revive(self, e, want, env, gold, wood, food_free, res):
        """HeroReviver：AMAI 走的是 BuildUnit(1, hero[i], 200)，我们的执行层是"祭坛 + 死亡英雄表"那条路。
        优先级 200 排在农场(400)/农民(280)后面、其它全部前面 —— 钱不够就停下攒，不让出兵把钱花光。"""
        cg, cl = env.cost(want)
        if cg > gold or cl > wood:
            return NOT_ENOUGH_RES, [], gold, wood, food_free
        why = env.check(want)
        if why:
            res.skipped.append((want, why))
            self.count(f"复活下不了·{why}")
            return CANNOT_BUILD, [], gold, wood, food_free
        return BUILT_SOME, [Decision(want, want.prio, cg, cl)], gold - cg, wood - cl, food_free

    def _start_unit(self, e, want, qty, env, gold, wood, food_free, build_free, res):
        code = e.code
        if e.town != -1 and not env.town_alive(e.town):
            res.skipped.append((want, "那座分矿没了"))
            return CANNOT_BUILD, [], gold, wood, food_free
        if D.is_upgraded_from(code) and env.ai_time() < self.available_time.get(code, -1e9):
            res.skipped.append((want, "刚下过升级令"))
            return BUILT_SOME, [], gold, wood, food_free
        if code == self.data.racial("racial_peon"):
            self.needed_peons += qty
        have = env.town_count(code, only_done=False, town=e.town)
        if have >= qty:
            return BUILT_ALL, [], gold, wood, food_free
        need = qty - have
        cg, cl = env.cost(want)
        cf = G.food(code)
        afford = need
        if cg > 0:
            afford = min(afford, gold // cg)
        if cl > 0:
            afford = min(afford, wood // cl)
        if cf > 0:
            afford = min(afford, food_free // cf)
        if cf > 0 and cf > food_free:
            res.skipped.append((want, "人口不够"))
            return CANNOT_BUILD, [], gold, wood, food_free
        if cf > 0:
            afford_food, reserve = self.upkeep.check(food_free // max(cf, 1), env.food_used(), cf, afford,
                                                     gold, env.mines_owned(), env.ai_time())
            if reserve:
                gold -= reserve
                self.count("维护费预留")
            afford = min(afford, afford_food)
        if afford <= 0:
            return NOT_ENOUGH_RES, [], gold, wood, food_free
        bt = D.buy_type(code)
        if bt in (D.BT["UNIT"], D.BT["HERO"]):
            n1 = D.needed(code)[0]
            free = build_free.get(n1, 0)
            cost_qty = min(need, free)
            build_free[n1] = max(0, free - cost_qty)
        else:
            cost_qty = need
        afford = min(afford, cost_qty)
        if afford <= 0:
            res.skipped.append((want, "出兵建筑都在忙"))
            self.count(f"出兵建筑都在忙·{code}")
            return CANNOT_BUILD, [], gold, wood, food_free
        gold -= cg * afford
        wood -= cl * afford
        food_free -= cf * afford
        why = env.check(want)                    # 执行层：有没有建筑/工人、引擎裁决
        if why:
            res.skipped.append((want, why))
            self.count(f"下不了·{why}")
            return CANNOT_BUILD, [], gold, wood, food_free
        if D.is_upgraded_from(code):
            self.available_time[code] = env.ai_time() + 10
        decided = [Decision(want, want.prio, cg, cl) for _ in range(afford)]
        # ⚠ 源码原样：拿 afford + have 和 need 比（不是和 qty 比）
        return (BUILT_ALL if afford + have == need else BUILT_SOME), decided, gold, wood, food_free

    def _start_upgrade(self, e, want, qty, env, gold, wood, food_free, build_free, res):
        code = e.code
        if env.upgrade_level(code) >= qty:
            return BUILT_ALL, [], gold, wood, food_free
        if env.ai_time() < self.upgrade_time.get(code, -1e9):
            return BUILT_SOME, [], gold, wood, food_free
        if env.town_threatened():
            res.skipped.append((want, "城镇受威胁，先不研究"))
            return BUILT_SOME, [], gold, wood, food_free
        cg, cl = env.cost(want)
        gold -= cg                               # 研究总是先占着钱（钱不够也不挡后面）
        wood -= cl
        if gold < 0 and cg > 0:
            return NOT_ENOUGH_RES, [], gold, wood, food_free
        if wood < 0 and cl > 0:
            return NOT_ENOUGH_RES, [], gold, wood, food_free
        n1 = D.needed(code)[0]
        if build_free.get(n1, 0) >= 1:
            build_free[n1] -= 1
        why = env.check(want)
        if why:
            res.skipped.append((want, why))
            self.count(f"研究下不了·{why}")
            return CANNOT_BUILD, [], gold, wood, food_free
        self.upgrade_time[code] = env.ai_time() + 10
        return BUILT_SOME, [Decision(want, want.prio, cg, cl)], gold, wood, food_free

    def _start_expand(self, e, want, qty, env, gold, wood, food_free, res):
        have = env.expand_count()
        if have >= qty:
            return BUILT_ALL, [], gold, wood, food_free
        if env.town_threatened():
            res.skipped.append((want, "城镇受威胁，先不开矿"))
            return BUILT_SOME, [], gold, wood, food_free
        if not env.peons_saturated():
            res.skipped.append((want, "现有矿上的工人还没满，先不开矿"))
            self.count("开矿·工人没满")
            return CANNOT_BUILD, [], gold, wood, food_free
        cg, cl = env.cost(want)
        gold -= cg
        wood -= cl
        why = env.check(want)                    # runtime 会顺手把"分矿旁边的野怪"设成清野目标
        if why:
            res.skipped.append((want, why))
            self.count(f"开矿下不了·{why}")
            if env.own_strength() < env.expansion_strength():
                gold += cg                       # 打不过守卫就把钱还回去，让它去造兵（§7.4）
                wood += cl
            return CANNOT_BUILD, [], gold, wood, food_free
        if gold < 0 or wood < 0:
            return NOT_ENOUGH_RES, [], gold, wood, food_free
        return BUILT_SOME, [Decision(want, want.prio, cg, cl)], gold, wood, food_free

# -*- coding: utf-8 -*-
"""声明式建造（AMAI OneBuildLoopAM，common.eai L12272）。

表只说「要几个 X、优先级多少」（Want）。每轮：
  1. 按优先级从高到低走；
  2. 已经有够了（含在建、在训练队列、刚下过令还没出现的）-> 跳过；
  3. 前置/建造者/人口不满足（CANNOT_BUILD）-> 跳过，继续看下一个（同时把缺的前置记下来）；
  4. 钱不够（NOT_ENOUGH_RES）-> **兵、建筑、开矿就停下，后面优先级低的都不花钱**（研究和物品例外，不挡后面）；
  5. 能造 -> 记一条决定、扣掉这笔钱、继续。
这就是 AMAI 的攒钱：不需要另写预留。原来的策略层「预留被穿透十三次」，缺的就是第 4 条。
没造出来的每轮优先级 +PRIO_AGING（AMAI prio_t_inc），封顶 +AGING_CAP，造出来就清零。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import gamedata as G

BUILT_ALL = "built_all"
BUILT_SOME = "built_some"
NOT_ENOUGH_RES = "not_enough_res"
CANNOT_BUILD = "cannot_build"
PRIO_AGING = 1
AGING_CAP = 20


@dataclass
class Want:
    kind: str            # unit / building / upgrade / expand / item / hero
    code: str
    qty: int
    prio: int
    where: str = ""      # 建筑落点提示：base / front / mine
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.code}:{self.where}"


@dataclass
class Decision:
    want: Want
    prio: float
    gold: int
    lumber: int


@dataclass
class RoundResult:
    decisions: list = field(default_factory=list)
    stopped_at: Want | None = None           # 钱不够停在哪一条
    skipped: list = field(default_factory=list)   # (want, 原因)
    done: list = field(default_factory=list)
    short: str = ""                          # 停下是因为缺什么："gold" / "lumber" / "both"
    through: list = field(default_factory=list)   # 攒钱期间照样放过去的（不要缺的那种资源）


class Planner:
    def __init__(self):
        self.aging: dict[str, int] = {}

    def plan(self, wants: list[Want], have: Callable[[Want], int], gold: int, lumber: int, food_free: int,
             check: Callable[[Want], str | None], cost: Callable[[Want], tuple[int, int]] | None = None,
             unit_food_free: int | None = None) -> RoundResult:
        """have(want) -> 已有数（含在建/队列/刚下令）；check(want) -> None 能下 / 原因字符串（前置、没有建造者…）；
        cost(want) -> (金, 木)，缺省按兵种表。
        unit_food_free：兵、农民、英雄能用的人口（软上限 G.FOOD_SOFT_CAP 算出来的，已下单没出来的也扣了）。
        被软上限挡下的记「人口软上限」（和真没人口的「人口不够」分开：前者战术层会强制攻击低级兵腾地方，后者等房子）。
        ★ 09-22 英雄也算进软上限（人口不能冲过 80）—— 原来英雄按真实上限，想出第三个英雄时就冲过 80。"""
        cost = cost or (lambda w: G.cost(w.code))
        unit_free = food_free if unit_food_free is None else min(unit_food_free, food_free)
        res = RoundResult()
        ordered = sorted(wants, key=lambda w: -(w.prio + min(self.aging.get(w.key, 0), AGING_CAP)))
        g, l = gold, lumber
        for w in ordered:
            n = have(w)
            if n >= w.qty:
                res.done.append(w)
                self.aging.pop(w.key, None)
                continue
            # 停下之后才看到的条目**不涨优先级**：原来 break 之后根本走不到它们、也就不涨；
            # 现在为了放不缺那种资源的东西过去才走到，涨了会慢慢反超停下的那一项，改掉原来的顺序。
            if res.stopped_at is None:
                self.aging[w.key] = self.aging.get(w.key, 0) + PRIO_AGING
            if w.kind in ("unit", "hero") and G.food(w.code) > food_free:
                res.skipped.append((w, "人口不够"))
                continue
            if w.kind in ("unit", "hero") and G.food(w.code) > unit_free:
                res.skipped.append((w, "人口软上限"))
                continue
            why = check(w)
            if why:
                res.skipped.append((w, why))
                continue
            cg, cl = cost(w)
            if res.stopped_at is not None:
                # ★★ 09-21 1-262 局：金 3000、木 30 **整整 7 分钟一个农民都没造**。
                #   原来这里是 `break` —— 主城升级 / 第二座大厅因为**木头**不够停下，
                #   后面**只要金子**的农民（优先级 60）也跟着一个不造；而木头少正是因为砍树的农民不够。死锁。
                #   AMAI 攒钱的本意是「别让低优先级的把高优先级要的钱花掉」—— 停下那一项要的金和木已经留住了
                #   （下面 g/l 在停下时扣掉），**不需要所缺那种资源**的东西让它过，一分都碰不到它要攒的。
                if (res.short in ("lumber", "both") and cl > 0) or (res.short in ("gold", "both") and cg > 0):
                    res.skipped.append((w, f"攒{'木头' if res.short == 'lumber' else '金子' if res.short == 'gold' else '钱'}中，要这种资源的不插队"))
                    continue
                if cg > g or cl > l:
                    res.skipped.append((w, "攒钱中，剩下的钱也不够"))
                    continue
                res.through.append(w)
            elif cg > g or cl > l:
                if w.kind in ("upgrade", "item"):
                    res.skipped.append((w, "钱不够（研究/物品不挡后面）"))
                    continue
                res.stopped_at = w
                res.short = "both" if (cg > g and cl > l) else ("gold" if cg > g else "lumber")
                # 停下那一项要的钱先留住：后面放过去的只能花**剩下的**，碰不到它要攒的那份
                g, l = max(0, g - cg), max(0, l - cl)
                continue
            res.decisions.append(Decision(w, w.prio + min(self.aging.get(w.key, 0), AGING_CAP), cg, cl))
            g -= cg
            l -= cl
            if w.kind in ("unit", "hero"):
                food_free -= G.food(w.code)
                unit_free -= G.food(w.code)
        return res

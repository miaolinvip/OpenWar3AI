# -*- coding: utf-8 -*-
"""第二支打野小队：兵多、两个英雄时分两路各练一路的级。

★★ 09-23 单位多、有两个英雄时分两路练级：各带一支小分队打野，
   只要判定打得过，就配合适的单位跟着英雄去打。

判「打得过」用的是主队那一套同一个函数（tactics.creep_plans / power_ratio），不另起一套：
  * 小队自己对目标营地的战力比 >= CREEP_POWER_MIN（和主队开怪同一条线，「等兵」不算数：wait 必须是 0）；
  * 拆完之后**主队对它自己那个营地**照样 >= CREEP_POWER_MIN —— 不能为了分兵把主队拖成打不动；
  * 打起来掉到 CREEP_ABORT_POWER 以下、带队英雄残血、掉了人，整队收队回家。

⚠ 谁指挥谁：小队的人登记在 busy_addrs 里，runtime 会把它们从主队的意图里摘掉（和骚扰小队同一条路）。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .tactics import CREEP_ABORT_POWER, CREEP_POWER_MIN, Intent, centroid, creep_plans, power_ratio

SPLIT_MIN_HEROES = 2         # 至少两个英雄（一个留给主队）
SPLIT_MIN_ARMY = 10          # 可用的非英雄作战单位至少这么多（「单位多」的门槛）
SPLIT_ESCORT = 4             # 给第二个英雄配几个兵
SPLIT_MIN_ESCORT = 2
SPLIT_HERO_HP = 0.7          # 带队英雄血量低于这个不出门
SPLIT_UNIT_HP = 0.8          # 配的兵血量低于这个不带
SPLIT_RETURN_HERO_HP = 0.45  # 带队英雄掉到这个收队
SPLIT_COOLDOWN_S = 40.0      # 收队之后隔这么久才再分一次
SPLIT_ENGAGE_R = 900.0       # 离营地这么近就点名开打，远了先走过去


def _hp_ok(u, frac: float) -> bool:
    return u.hp_max <= 0 or u.hp >= frac * u.hp_max


@dataclass
class Party:
    hero: int
    addrs: list
    camp: dict
    camp_ids: set
    born: float


@dataclass
class SplitCreep:
    """一次只带一支第二队（再多了主队就散了）。step 返回这支队伍这一拍的意图。"""
    party: Party | None = None
    retry_at: float = -1e9
    counts: Counter = field(default_factory=Counter)
    notes: list = field(default_factory=list)

    @property
    def busy_addrs(self) -> set:
        return set(self.party.addrs) | {self.party.hero} if self.party else set()

    def _count(self, key: str) -> None:
        self.counts[key] += 1

    def _note(self, text: str) -> None:
        self.notes.append(text)

    def step(self, world, home, army, heroes, main_camp_ids=(), recall: bool = False) -> list[Intent]:
        if self.party is not None:
            return self._run(world, home, recall)
        if recall:
            self._count("分队打野·主力要用人·不分")
            return []
        return self._form(world, home, army, heroes, set(main_camp_ids))

    # ── 已经派出去了 ──
    def _run(self, world, home, recall: bool) -> list[Intent]:
        by = {u.addr: u for u in world.my_army + world.my_heroes if u.hp > 0}
        hero = by.get(self.party.hero)
        units = [by[a] for a in self.party.addrs if a in by] + ([hero] if hero is not None else [])
        home_xy = (home.x, home.y) if home is not None else centroid(units) if units else (0.0, 0.0)
        if recall:
            return self._home(world, units, home_xy, "第二队收队：主力要用人", "分队打野·收队·主力要用人")
        if hero is None or len(units) <= SPLIT_MIN_ESCORT:
            return self._home(world, units, home_xy, "第二队收队：人不够了", "分队打野·收队·掉人")
        alive = [c for c in world.creeps if c.addr in self.party.camp_ids and c.hp > 0]
        if not alive:
            self._count("分队打野·营地清空")
            self._note(f"第二队清空营地（强度 {self.party.camp['strength']:.0f}），{len(units)} 个人归队")
            self.party, self.retry_at = None, world.t + SPLIT_COOLDOWN_S
            return []
        if not _hp_ok(hero, SPLIT_RETURN_HERO_HP):
            return self._home(world, units, home_xy, "第二队收队：带队英雄残血", "分队打野·收队·英雄残血")
        r = power_ratio(units, alive, world.levels)
        if r < CREEP_ABORT_POWER:
            self._note(f"第二队撤：对营地剩下的战力比 {r:.2f} < {CREEP_ABORT_POWER}")
            return self._home(world, units, home_xy, "第二队收队：打不动", "分队打野·收队·打不动")
        cx, cy = self.party.camp["x"], self.party.camp["y"]
        near = [u for u in units if math.hypot(u.x - cx, u.y - cy) <= SPLIT_ENGAGE_R]
        if len(near) < max(SPLIT_MIN_ESCORT, len(units) - 1):
            self._count("分队打野·走过去")
            return [Intent("attack_move", units, cx, cy, why="第二队打野：走向营地")]
        focus = min(alive, key=lambda c: (c.hp, math.hypot(c.x - cx, c.y - cy)))
        self._count("分队打野·开打")
        return [Intent("attack", units, focus.x, focus.y, focus, why=f"第二队打野（营地强度 {self.party.camp['strength']:.0f}）")]

    def _home(self, world, units, home_xy, why: str, count_key: str) -> list[Intent]:
        self._count(count_key)
        self.party, self.retry_at = None, world.t + SPLIT_COOLDOWN_S
        return [Intent("move", units, home_xy[0], home_xy[1], why=why)] if units else []

    # ── 要不要分队 ──
    def _form(self, world, home, army, heroes, main_camp_ids: set) -> list[Intent]:
        if world.t < self.retry_at:
            return []
        healthy_heroes = [h for h in heroes if h.hp > 0 and _hp_ok(h, SPLIT_HERO_HP)]
        if len(healthy_heroes) < SPLIT_MIN_HEROES:
            self._count("分队打野·英雄不够")
            return []
        pool = [u for u in army if u.hp > 0 and _hp_ok(u, SPLIT_UNIT_HP)]
        if len(pool) < SPLIT_MIN_ARMY:
            self._count("分队打野·人不够")
            return []
        # 等级低的那个英雄带队去练级（分两路练级）；主队留等级高的
        lead = min(healthy_heroes, key=lambda h: ((world.levels or {}).get(h.addr, 1), h.addr))
        escort = sorted([u for u in pool], key=lambda u: math.hypot(u.x - lead.x, u.y - lead.y))[:SPLIT_ESCORT]
        if len(escort) < SPLIT_MIN_ESCORT:
            self._count("分队打野·人不够")
            return []
        squad = escort + [lead]
        taken = {u.addr for u in squad}
        rest = [u for u in world.my_army + world.my_heroes if u.addr not in taken and u.hp > 0]
        plans = [p for p in creep_plans(world, squad, (lead.x, lead.y), home)
                 if p["wait"] <= 1e-3 and p["r"] >= CREEP_POWER_MIN
                 and not ({c.addr for c in p["camp"]["units"]} & main_camp_ids)]
        if not plans:
            self._count("分队打野·没有打得过的第二个营地")
            return []
        plan = plans[0]
        if main_camp_ids:
            main_alive = [c for c in world.creeps if c.addr in main_camp_ids and c.hp > 0]
            if main_alive and power_ratio(rest, main_alive, world.levels) < CREEP_POWER_MIN:
                self._count("分队打野·拆了主队就打不动")
                return []
        camp = plan["camp"]
        self.party = Party(lead.addr, [u.addr for u in escort], camp, {c.addr for c in camp["units"] if c.hp > 0}, world.t)
        self._count("分队打野·派出")
        self._note(f"第二队出发：{lead.type} 带 {len(escort)} 个去打营地（强度 {camp['strength']:.0f}，战力比 {plan['r']:.1f}），"
                   f"主队还剩 {len(rest)} 个")
        return [Intent("attack_move", squad, camp["x"], camp["y"], why="第二队打野：走向营地")]


__all__ = ["SplitCreep", "Party"]

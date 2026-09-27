# -*- coding: utf-8 -*-
"""AMAI 的强度 / 进攻性 / 威胁 / 目标好不好（common.eai、Jobs/ARMY_TRACK.eai、Jobs/HARASS.eai、TFT/GlobalSettings.txt）。

和 war3-button-hook-lab/war3amai.ps1 是同一套公式（策略层 v293 在用）；tests/test_amai.py 和 test-amai.ps1 钉的是同一组期望值。
我们比 AMAI 多的：敌方强度用真实值（AMAI 按人格 uncertainty 加噪模拟看不见）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import gamedata as G

HERO_BASE = 2
HERO_ULT_LEVEL = 6
HERO_ULT_VALUE = 3
TOWER_VALUE = 3
TOWER_RADIUS = 2000.0
BASE_AGGRESSION = 2
RANGE_DIV = 1400.0
LOW_AGGR, HIGH_AGGR = -15.0, 45.0
FLEE_MULT = (1.0, 2.0, 3.0)
FULL_THREAT_DIST, NO_THREAT_DIST, THREAT_EXP, THREAT_MULT = 1000.0, 2000.0, 0.8, 540.0
ATL_TIME = (300.0, 900.0, 10.0, 20.0)
EXPANSION_BONUS = 7
ARMY_RADIUS = 1500.0
FOOD_AGGR = ((99, 25), (88, 20), (80, 12), (70, 10), (60, 8), (50, 7), (40, 6), (30, 5), (20, 2))
# Human/RaceAggression.txt：对手种族 -> 一本/二本/三本
RACE_AGGR = {"nightelf": (2, 3, 4), "human": (1, 3, 5), "orc": (-1, 0, 1), "undead": (-1, -1, 2)}


def lerp(x0: float, x1: float, y0: float, y1: float, x: float) -> float:
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def unit_strength(u, levels: dict | None = None) -> float:
    """GetUnitStrength：普通单位 floor((人口+等级)/2) × 血量比例；英雄 (2+等级，6 级 +3) × 血量；会打人的塔 3 × 血量；农民建筑 0。"""
    if u is None or u.hp <= 0:
        return 0.0
    t = u.type
    frac = min(1.0, u.hp / u.hp_max) if u.hp_max > 0 else 1.0
    if G.is_tower(t):
        return TOWER_VALUE * frac
    if t not in G.units() or G.is_building(t) or G.is_worker(t):
        return 0.0
    if G.is_hero(t):
        lvl = int((levels or {}).get(u.addr, 1))
        s = HERO_BASE + lvl + (HERO_ULT_VALUE if lvl >= HERO_ULT_LEVEL else 0)
        return s * frac
    return float((G.food(t) + int(G.stat(t, "level", 0))) // 2) * frac


def strength(units, levels: dict | None = None) -> float:
    return sum(unit_strength(u, levels) for u in units)


def aggression(food_used: int, heroes: int, foe_race: str | None, tier: int,
               base: float | None = None, race_table: dict | None = None) -> float:
    """base / race_table 不传 = 和以前完全一样（全局 2 + 写死的人族 RaceAggression 表）。
    AMAI 战略层（brain/amai_strategy）会把本局的真值传进来：base = 2 + 人格第 3 列，
    race_table = 本族 RaceAggression.txt + 当前战略第 45~56 列（同一个"强度点"量级，直接相加）。"""
    a = float(BASE_AGGRESSION if base is None else base)
    for need, add in FOOD_AGGR:
        if food_used >= need:
            a += add
            break
    table = RACE_AGGR if race_table is None else race_table
    if foe_race in table:
        a += table[foe_race][max(0, min(2, tier - 1))]
    if heroes < 1:
        a -= 10
    return a


def flee_mult(aggr: float, flee_check: bool = False) -> float:
    lo, hi = (FLEE_MULT[1], FLEE_MULT[2]) if flee_check else (FLEE_MULT[0], FLEE_MULT[1])
    return lerp(LOW_AGGR, HIGH_AGGR, lo, hi, aggr)


@dataclass
class TargetEval:
    good: bool
    lhs: float
    rhs: float
    target: float
    tower: float


def target_good(foe_str: float, tower_str: float, has_siege: bool, dist: float, own_str: float, aggr: float, bonus: float = 0.0) -> TargetEval:
    """IsTargetGood：对方整体强度（< 100 时 + 距离/1400，封顶 100）+ 目标塔（没攻城 ×4）<= 我方 + 进攻性 + 目标加成。"""
    target = foe_str
    if target < 100:
        target = min(target + dist / RANGE_DIV, 100.0)
    tower = tower_str if has_siege else 4 * tower_str
    lhs, rhs = target + tower, own_str + aggr + bonus
    return TargetEval(lhs <= rhs, lhs, rhs, target, tower)


def threat_of(strength_value: float, dist: float) -> float:
    if dist > NO_THREAT_DIST:
        return 0.0
    return THREAT_MULT * strength_value / max(dist, FULL_THREAT_DIST) ** THREAT_EXP


def town_threat(tx: float, ty: float, foe_units, levels: dict | None = None) -> float:
    """按单位累加的城镇威胁：敌塔（2000 内）2×强度；其余 540×强度/max(距离,1000)^0.8（2000 外不算）。"""
    total = 0.0
    for u in foe_units:
        if u.hp <= 0:
            continue
        s = unit_strength(u, levels)
        if s <= 0:
            continue
        d = math.hypot(u.x - tx, u.y - ty)
        if G.is_tower(u.type):
            total += 2 * s if d <= TOWER_RADIUS else 0.0
        else:
            total += threat_of(s, d)
    return total


def accepted_threat(game_sec: float) -> float:
    return lerp(*ATL_TIME, game_sec)


def player_threat(own_str: float, aggr: float) -> float:
    return THREAT_MULT * own_str / FULL_THREAT_DIST ** THREAT_EXP * flee_mult(aggr)


def defend_mode(threat: float, accepted: float, player: float) -> str:
    if threat <= accepted:
        return "minor"
    if threat <= player:
        return "defend"
    return "toostrong"


def harass_chance(aggr: float) -> float:
    return lerp(-15, 15, 10, 100, aggr)


def should_flee(local_foe_str: float, local_own_str: float, aggr: float) -> bool:
    """RETREAT_CONTROL：敌方 > 我方 × (2→3 按进攻性) 就整队撤。"""
    return local_foe_str > local_own_str * flee_mult(aggr, flee_check=True)

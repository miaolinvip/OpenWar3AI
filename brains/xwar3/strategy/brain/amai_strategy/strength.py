# -*- coding: utf-8 -*-
"""敌我分类强度 enemy_<类> / ally_<类>，以及"某个玩家有多强"（换战略的闸门用）。

对应 AMAI：
  Jobs/UPDATE_STRENGTH.eai         每 15×slm 秒一轮，第一次 50~65 秒；enemy_x 加 ±area_uncertainty 噪声
  common.eai:5550 GetPlayer<类>Strength   StandardUnits 第 17~27 列 × 该兵种数量 + StrengthPredictionRules
  common.eai:5487 GetPlayerStrength       非英雄非农民非建筑的 unit_strength 之和 + 英雄加成
  common.eai:5531 GetMaximumEnemyStrength 所有敌人里最大的那个

和 AMAI 的差别（写进报告）：
  * AMAI 全知（GetPlayerUnitTypeCount 不看视野）；我们读的是内存快照里的全图单位，口径基本一致，
    但**在训练队列里、还没出现在场上的兵我们数不到**，AMAI 的原生计数可能数得到；
  * 让分（handicap）一律按 100% 算（sqrt(1)=1）；
  * 我方 ally_* 只算自己（1v1 没有盟友）。
"""
from __future__ import annotations

import math
from collections import Counter

from .. import gamedata as G
from . import data as D

HERO_BASE = 2
HERO_ULT_LEVEL = 6
HERO_ULT_VALUE = 3


def unit_counts(units) -> dict:
    """某个玩家场上各兵种的数量（含在建建筑，和 world.counts 一个口径）。"""
    c = Counter()
    for u in units:
        if u.hp > 0:
            c[u.type] += 1
    return c


def hero_strength(level: int) -> int:
    """GetHeroStrength（common.eai:5411）：2 + 等级（6 级以上再 +3）。"""
    v = HERO_BASE + level + (HERO_ULT_VALUE if level >= HERO_ULT_LEVEL else 0)
    return int(v + 0.5)


def unit_strength_ex(u, levels: dict | None = None, creep: bool = False) -> int:
    """GetUnitStrengthEx（common.eai:5445）。
    ⚠ 附录 A5：AMAI 写的是 R2I((food+level)/2 + 0.5)，而 JASS 里 (整数+整数)/2 已经是整数除法，
      +0.5 没起作用 = 向下取整。这里按**作者意图**四舍五入（brain/amai.py 里给战术用的那份仍是向下取整，
      改它会动到手写表路径的行为）。"""
    lvl = int(G.stat(u.type, "level", 0) or 0)
    if creep:
        return int(lvl + 0.5)
    if G.is_hero(u.type):
        return hero_strength(int((levels or {}).get(getattr(u, "addr", None), 1)))
    return int((G.food(u.type) + lvl + 1) // 2)


def unit_strength(u, levels: dict | None = None, creep: bool = False) -> float:
    """GetUnitStrength（common.eai:5472）：塔 3×血量比例；其余 强度 × 血量比例。"""
    frac = min(1.0, u.hp / u.hp_max) if getattr(u, "hp_max", 0) > 0 else 1.0
    if G.is_tower(u.type):
        return 3.0 * frac
    return unit_strength_ex(u, levels, creep) * frac


def player_strength(units, levels: dict | None = None) -> float:
    """GetPlayerStrength：非英雄、非农民、非建筑的活单位 + 英雄加成（英雄本体按血量折算 + 第 16 列）。"""
    total = 0.0
    for u in units:
        if u.hp <= 0:
            continue
        t = u.type
        if G.is_hero(t) or G.is_worker(t) or G.is_building(t):
            continue
        total += unit_strength(u, levels)
    # GetPlayerHeroStrengthMod（common.eai:5424）
    for u in units:
        if u.hp > 0 and G.is_hero(u.type):
            frac = min(1.0, u.hp / u.hp_max) if getattr(u, "hp_max", 0) > 0 else 1.0
            total += int(hero_strength(int((levels or {}).get(getattr(u, "addr", None), 1))) * frac)
    for code, n in unit_counts(units).items():
        b = D.hero_bonus(code)
        if b:
            total += b * n
    return total


def category_strength(counts: dict, cat: str, data: D.AmaiData, ai_time: float, pred_eval=None) -> float:
    """GetPlayer<类>Strength：兵种数量 × 权重，再加 StrengthPredictionRules 的"看到科技建筑就提前算上"。"""
    s = 0.0
    for code, n in counts.items():
        w = D.cat_weight(code, cat)
        if w:
            s += w * n
    if pred_eval is not None:
        for rule in data.prediction_rules:
            if rule.get("Strength") != cat:
                continue
            start, end = float(rule.get("Start Time", 0)), float(rule.get("End Time", -1))
            if ai_time < start or (end >= 0 and ai_time > end):
                continue
            cond = (rule.get("parsed") or {}).get("Condition")
            if pred_eval(cond, counts):
                s += float(rule.get("Bonus") or 0)
    return s


class StrengthTracker:
    """enemy_* / ally_*：第一次在 50~65 游戏秒更新，之后每 15 秒一次（UPDATE_STRENGTH 的周期）。"""

    FIRST_MIN, FIRST_MAX, PERIOD = 50.0, 65.0, 15.0

    def __init__(self, data: D.AmaiData, area_uncertainty: int = 5, rng=None):
        self.data = data
        self.area_uncertainty = int(area_uncertainty)
        self.rng = rng
        self.enemy = {c: 0.0 for c in D.CATEGORIES}
        self.ally = {c: 0.0 for c in D.CATEGORIES}
        self.next_at = None
        self.updates = 0

    def due(self, t: float) -> bool:
        if self.next_at is None:
            self.next_at = self.FIRST_MIN if self.rng is None else self.rng.uniform(self.FIRST_MIN, self.FIRST_MAX)
        return t >= self.next_at

    def random_diff(self) -> int:
        """GetRandomDiff（common.eai:1864）：一半概率 +randint(0,m)，一半 -randint(0,m)。"""
        m = self.area_uncertainty
        if not m or self.rng is None:
            return 0
        v = self.rng.randint(0, m)
        return v if self.rng.randint(0, 1) else -v

    def update(self, counts_by_player: dict, enemies: set, allies: set, ai_time: float, pred_eval=None) -> None:
        e = {c: 0.0 for c in D.CATEGORIES}
        a = {c: 0.0 for c in D.CATEGORIES}
        for p, counts in counts_by_player.items():
            bucket = e if p in enemies else (a if p in allies else None)
            if bucket is None:
                continue
            for c in D.CATEGORIES:
                bucket[c] += category_strength(counts, c, self.data, ai_time,
                                               (lambda cond, cc=counts: pred_eval(cond, cc)) if pred_eval else None)
        for c in D.CATEGORIES:
            self.enemy[c] = max(e[c] + self.random_diff(), 0.0) if e[c] > 0 else 0.0
            self.ally[c] = a[c]
        self.updates += 1

    def advance(self, t: float) -> None:
        self.next_at = t + self.PERIOD


def creep_strength_sum(creeps, x: float, y: float, radius: float) -> float:
    """GetLocationCreepStrength（common.eai:5610）：半径内野怪强度之和（野怪只看等级）。"""
    s = 0.0
    for c in creeps:
        if c.hp > 0 and math.hypot(c.x - x, c.y - y) <= radius:
            s += unit_strength(c, None, creep=True)
    return s

# -*- coding: utf-8 -*-
"""选：人格、开局战略、中途换战略、反制类别/兵种、英雄和加点线。

对应 AMAI：races.eai:211-310（chooseStrategyRP / StrategyChanger）、common.eai:9360-9700（各种加分、
归一化、finish_strategy_change）、common.eai:10443-10617（DetermineCounterForce / BuildAnti）、
races.eai:316-406 + common.eai:9717-9900（选英雄）、common.eai:14235（加点线）。

⚠ 附录 A 的两个错位（A1 base rp 按表行号写、A2 第二三英雄的位次加分按原行号加减）这里都**按作者意图修正**：
  所有 rp 以"战略名 / 英雄 4 字码"为键，不会错位。要复刻 AMAI 实际行为得另加开关（没做，报告里写了）。
随机全部走注入的 rng（默认 random.Random()），测试里固定种子。
"""
from __future__ import annotations

import random

from . import data as D


def _rng(rng):
    return rng if rng is not None else random.Random()


def roll_weighted(names, weights, rng) -> str | None:
    """AMAI 的加权随机：GetRandomInt(1, total) 两端都包含，从头累加第一个盖住 roll 的（common.eai:9469）。"""
    total = int(sum(weights))
    if total <= 0:
        return None
    roll = _rng(rng).randint(1, total)
    acc = 0
    for name, w in zip(names, weights):
        acc += int(w)
        if roll <= acc:
            return name
    return names[-1] if names else None


# ── 人格（Profiles.txt，common.eai:3030-3135）──
def choose_profile(data: D.AmaiData, rng=None) -> dict:
    """种族匹配（RANDOM 或本族）、本局没被别的 AMAI 用过的人格里均匀随机；10% 概率改从稀有人格里选。
    我们只有一个 AI，"用过"的表永远是空的。"""
    r = _rng(rng)
    own = D.RACE_NAME[data.race]
    normal = [p for p in data.profiles if int(p.get("Rare Profile") or 0) == 0 and p.get("Race") in ("RANDOM", own)]
    rare = [p for p in data.profiles if int(p.get("Rare Profile") or 0) == 1 and p.get("Race") in ("RANDOM", own)]
    if rare and r.randint(0, 9) == 0:
        return rare[r.randint(0, len(rare) - 1)]
    if normal:
        return normal[r.randint(0, len(normal) - 1)]
    return data.profiles[r.randint(0, len(data.profiles) - 1)]


def profile_value(profile: dict, key: str, default=0):
    v = profile.get(key, default)
    return v if isinstance(v, (int, float)) else default


# ── 开局选战略（§1）──
def strategy_rp(data: D.AmaiData, env, profile: dict, mid_game: bool) -> list:
    """chooseStrategyRP：按 data.strategies（strat_id 顺序）返回每个战略的 rp。
    mid_game=True 对应 ai_time > 2（中途换战略）：各种加分只算三分之一（先 ×3 再 ÷3 的整数除法）。"""
    strats = data.strategies
    rp = [int(s.get("base rp") or 0) for s in strats]
    # AddFavoriteUnitRP（common.eai:3140）：人格偏好兵种 == 战略的 key_unit -> +fav_unit_add
    fav = profile.get("codes", {}).get(f"Favorite {D.RACE_NAME[data.race]} Unit")
    add = data.setting_int("fav_unit_add", 20)
    for i, s in enumerate(strats):
        if fav and (s.get("codes") or {}).get("key_unit") == fav:
            rp[i] += add
    # 同队重复战略类型扣分（A6）：读的是队友 AMAI 写的哈希表，单 AI 恒为空 -> 不扣
    if mid_game:
        rp = [v * 3 for v in rp]
    if env.c_ally_total() > 0:
        rp = [v + int(s.get("ally bonus") or 0) for v, s in zip(rp, strats)]
    if env.is_1on1():
        rp = [v + int(s.get("1on1 bonus") or 0) for v, s in zip(rp, strats)]
    if env.is_ffa():
        rp = [v + int(s.get("FFA bonus") or 0) for v, s in zip(rp, strats)]
    if env.c_enemy_random() > 0:
        rp = [v + int(s.get("random enemy bonus") or 0) for v, s in zip(rp, strats)]
    for race, rname in D.RACE_NAME.items():
        if env.c_enemy(race) > 0:
            rp = [v + int(s.get(f"{rname} enemy bonus") or 0) for v, s in zip(rp, strats)]
    for race, rname in D.RACE_NAME.items():
        if env.c_ally(race) > 0:
            rp = [v + int(s.get(f"{rname} ally bonus") or 0) for v, s in zip(rp, strats)]
    if mid_game:
        rp = [int(v / 3) for v in rp]           # JASS 整数除法（向零截断）
    return [max(v, 0) for v in rp]


def small_map_add(data: D.AmaiData, rp: list, players: int, map_size: int) -> list:
    """SmallMapsAdd（common.eai:9452）：<=3 个玩家且地图小于 medium 时，一本战略 +20。"""
    if players > 3 or map_size >= 3:
        return rp
    bonus = 20
    return [v + bonus if int(s.get("main_strategy_tier") or 0) == 1 else v for v, s in zip(rp, data.strategies)]


def remove_non_beginning(data: D.AmaiData, rp: list) -> list:
    """removeNonBeginningStrats（common.eai:9676）：普通 1v1/2v2 下只留 beginning_strat 的战略。"""
    return [v if s.get("beginning_strat") else 0 for v, s in zip(rp, data.strategies)]


def choose_strategy(data: D.AmaiData, rp: list, rng=None) -> str | None:
    return roll_weighted(data.strategy_names, rp, rng)


# ── 中途换战略（§2）──
def lerp(x0, x1, y0, y1, x):
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def key_time_mult(elapsed_s: float, v: int) -> int:
    """KeyAddTimeModify（common.eai:9360）：开局 ×3，20 分钟后 ×1。"""
    return int(lerp(120.0, 1200.0, 3.0, 1.0, elapsed_s) * v)


def key_building_add(data: D.AmaiData, env, code: str | None) -> int:
    """GetKeyBuildingAddExt（common.eai:9366）。⚠ A16：在建（9）比建成（8）分还高，照抄。"""
    if not code:
        return 0
    if env.town_count(code, only_done=True) > 1:
        return data.setting_int("kb_add4", 10)
    if env.town_count(code, only_done=True) > 0:
        return data.setting_int("kb_add1", 8)
    if env.town_count(code, only_done=False) > 0:
        return data.setting_int("kb_add2", 9)
    n1, n2, _n3 = D.needed(code)
    if n1 and n2 and env.town_count(n1, only_done=True) > 0 and env.town_count(n2, only_done=True) > 0:
        return data.setting_int("kb_add3", 3)
    return 0


def key_upgrade_add(data: D.AmaiData, env, code: str | None) -> int:
    if not code:
        return 0
    lvl = env.upgrade_level(code)
    if lvl >= 3:
        return data.setting_int("ku_add3", 8)
    if lvl == 2:
        return data.setting_int("ku_add2", 7)
    if lvl == 1:
        return data.setting_int("ku_add1", 4)
    return 0


def strategy_change_scores(data: D.AmaiData, env, profile: dict, current: str, strat_persistence: int) -> dict:
    """StrategyChanger + finish_strategy_change 的打分部分，返回 {名字: 分}（不含最后的加权随机）。"""
    from .. import gamedata as G
    strats = data.strategies
    rp = strategy_rp(data, env, profile, mid_game=True)
    # normalize_rp_strat：rp = rp * 250 / 总和
    total = max(sum(rp), 1)
    norm = data.setting_int("rp_strat_normal", 250)
    rp = [(v * norm) // total for v in rp]
    # anti 列 × enemy 强度（Strategy.txt 第 26~36 列）
    for i, s in enumerate(strats):
        anti = 0.0
        for cat in D.CATEGORIES:
            anti += float(s.get(f"anti {cat} strength") or 0) * env.enemy(cat)
        rp[i] += int(anti)
    # 关键建筑 / 科技 / 兵种
    elapsed = env.elapsed()
    for i, s in enumerate(strats):
        codes = s.get("codes") or {}
        rp[i] += key_time_mult(elapsed, key_building_add(data, env, codes.get("key_building1"))
                               + key_building_add(data, env, codes.get("key_building2")))
        rp[i] += key_time_mult(elapsed, key_upgrade_add(data, env, codes.get("key_upgrade1"))
                               + key_upgrade_add(data, env, codes.get("key_upgrade2")))
        ku = codes.get("key_unit")
        rp[i] += key_time_mult(elapsed, env.town_count(ku, only_done=True) * G.food(ku)) if ku else 0
    # tier 加分
    tier_bonus = data.setting_int("strategy_tier_bonus", 22)
    for i, s in enumerate(strats):
        if int(s.get("main_strategy_tier") or 0) == env.tier():
            rp[i] += tier_bonus
    # 惯性
    f = strat_persistence + data.setting_int("strat_food_persistence", 3) * env.food_used()
    names = data.strategy_names
    cur = names.index(current) if current in names else 0
    rp[cur] += f
    threshold = data.setting_float("strategy_change_threshold", 1.05)
    base_cur = rp[cur] - f
    rp = [0 if v < base_cur * threshold else v for v in rp]
    rp[cur] = int(rp[cur] * max(env.c_enemy_total(), 1) ** 0.75)
    rp = [max(v, 0) for v in rp]
    return dict(zip(names, rp))


def pick_changed_strategy(data: D.AmaiData, scores: dict, rng=None) -> str | None:
    """finish_strategy_change 的收尾：按分从高到低排（同分时**下标大的排前面**，插入排序的效果），
    取前 relevant_strat_change_number 个加权随机。"""
    names = data.strategy_names
    order = sorted(range(len(names)), key=lambda i: (scores.get(names[i], 0), i), reverse=True)
    top = order[: data.setting_int("relevant_strat_change_number", 3)]
    return roll_weighted([names[i] for i in top], [scores.get(names[i], 0) for i in top], rng)


def calc_strat_timer(strat_minimum_time: int, rng=None) -> int:
    """calculate_strat_timer（races.eai:829）：randint(-30,30) - 最短时长×6。
    ⚠ A3：tier 系数和难度系数被 R2I 截断成 1，这里照抄（截断后本来就等于不乘）。"""
    return _rng(rng).randint(-30, 30) - strat_minimum_time * 6


# ── 反制（§4）──
def determine_counter(data: D.AmaiData, enemy: dict, ally: dict, chosen_counter: int | None,
                      strat_persistence: int, rng=None) -> tuple:
    """DetermineCounterForce（common.eai:10443）：返回 (类别下标, 是否没信息随便挑的, 各类的分)。"""
    r = _rng(rng)
    t = []
    for row in data.strengths:
        cat = row["strength"]
        a1 = ally.get(row.get("ally_counter1") or "", 0.0)
        a2 = ally.get(row.get("ally_counter2") or "", 0.0)
        t.append(int(max(enemy.get(cat, 0.0) - a1 / 20.0 - a2 / 20.0, 0.0) * 10))
    if chosen_counter is not None and 0 <= chosen_counter < len(t) and t[chosen_counter] > 0:
        t[chosen_counter] += int(strat_persistence / 20)      # 惯性
    t = [0 if v < 3 else v for v in t]
    total = sum(t)
    if total == 0:
        return r.randint(0, len(t) - 1), True, t
    roll = r.randint(1, total)
    acc = 0
    for i, v in enumerate(t):
        acc += v
        if roll <= acc:
            return i, False, t
    return len(t) - 1, False, t


def build_anti(entries: list, rng=None) -> str | None:
    """BuildAnti<类>Strength（common.eai:10550）：在这一类的克制兵种表里按权重抽一个。
    entries = [(4 字码, 权重)]，权重 <= 0 的在 AddUnitToAnti 时就没进表。"""
    total = sum(p for _u, p in entries)
    if total <= 0:
        return None
    choice = _rng(rng).randint(0, total)        # 源码是 GetRandomInt(0, total)，0 也可能抽到
    acc = 0
    for code, p in entries:
        acc += p
        if choice <= acc:
            return code
    return entries[-1][0]


# ── 英雄（§5）──
def pick_heroes(data: D.AmaiData, env, profile: dict, strategy_name: str, rng=None,
                allow_neutral: bool = False) -> list:
    """AMAI_PickMeleeHero（races.eai:316）。返回 [第一英雄, 第二, 第三] 的 4 字码。
    allow_neutral=False：只在本族 4 个英雄里选（我们的执行层只会在祭坛出英雄，酒馆买不了）。"""
    rows = [r for r in data.hero_rows() if allow_neutral or not r.get("neutral")]
    codes = [(r.get("codes") or {}).get("Hero") for r in rows]
    rp = {c: int(r.get("base rp") or 0) for c, r in zip(codes, rows)}
    col = {c: r for c, r in zip(codes, rows)}
    own = D.RACE_NAME[data.race]
    fav = (profile.get("codes") or {}).get(f"Favorite {own} Hero")
    fav_n = (profile.get("codes") or {}).get("Favorite Neutral Hero")
    fav_add = data.setting_int("fav_hero_add", 20)     # A15：GlobalSettings 里写了两次，后写的 20 生效
    for c in codes:
        if c and c in (fav, fav_n):
            rp[c] += fav_add
    strat = data.strategy(strategy_name) or {}
    scodes = strat.get("codes") or {}

    def strat_hero_add(which: str):
        h, amount = scodes.get(f"{which} hero bonus"), int(strat.get(f"{which} hero bonus amount") or 0)
        if h in rp:
            rp[h] += amount

    if env.c_ally_total() > 0:
        for c in codes:
            rp[c] += int(col[c].get("ally bonus") or 0)
    if env.c_enemy_random() > 0:
        for c in codes:
            rp[c] += int(col[c].get("random enemy bonus") or 0)
    for race, rname in D.RACE_NAME.items():
        if env.c_enemy(race) > 0:
            for c in codes:
                rp[c] += int(col[c].get(f"{rname} enemy bonus") or 0)
        if env.c_ally(race) > 0:
            for c in codes:
                rp[c] += int(col[c].get(f"{rname} ally bonus") or 0)

    picked = []
    pool = list(codes)
    for which, column in (("first", "first hero bonus"), ("second", "second hero bonus"), ("third", "third hero bonus")):
        for c in codes:                                   # 位次加分（A2 修正：按英雄码加，不按行号）
            rp[c] += int(col[c].get(column) or 0)
        strat_hero_add(which)                             # 战略指定的英雄加分，AMAI 加了不减，照抄
        for c in codes:                                   # make_hero_rp_positive：负分永久截成 0
            rp[c] = max(rp[c], 0)
        chosen = roll_weighted(pool, [rp[c] for c in pool], rng)
        if chosen is None:
            chosen = pool[0] if pool else None
        picked.append(chosen)
        if chosen in pool:
            pool.remove(chosen)
        for c in codes:
            rp[c] -= int(col[c].get(column) or 0)
    return picked


SKILL_LINES = ("major", "minor", "rare")


def roll_skill_line(data: D.AmaiData, rng=None) -> str:
    """LoadSkills（common.eai:14235）：perc = randint(0, 主+次+稀有)，<= 稀有取稀有，<= 次+稀有取次，否则主。"""
    major = data.setting_int("major_hero_skill_value", 60)
    minor = data.setting_int("minor_hero_skill_value", 30)
    rare = data.setting_int("rare_hero_skill_value", 10)
    perc = _rng(rng).randint(0, major + minor + rare)
    if perc <= rare:
        return "rare"
    if perc <= minor + rare:
        return "minor"
    return "major"

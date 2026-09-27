# -*- coding: utf-8 -*-
"""兵种表和种族常量。兵种数值来自 build_intel4/units.json（和策略层 W3Units 同一份）。"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import w3paths  # noqa: E402  brain/__init__ 已把 sdk/python 放上 sys.path

ROOT = w3paths.ROOT
UNITS_JSON = w3paths.UNITS_JSON

WORKERS = frozenset({"hpea", "opeo", "uaco", "ewsp"})
# 会打人的塔（哨塔 hwtw 不打人，不算）
TOWERS = frozenset({"hgtw", "hctw", "hatw", "owtw", "uzg1", "uzg2", "etrp"})
SIEGE = frozenset({"hmtm", "hrtt", "ocat", "umtw", "ebal"})
TIER3_HALLS = frozenset({"hcas", "ofrt", "unp2", "etoe"})
TIER2_HALLS = frozenset({"hkee", "ostr", "unp1", "etoa"})
HALLS = frozenset({"htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"})
MINES = frozenset({"ngol", "ugol", "egol"})
CREEP_OWNER = 12
NEUTRAL_OWNERS = frozenset({12, 13, 14, 15})
PASSIVE_OWNER = 15             # 中立被动：金矿、商店、酒馆，还有地图上乱跑的小动物
CRITTER_HP_MAX = 100           # 中立被动里血这么少、又不是建筑/金矿的 = 小动物（浣熊、螃蟹、青蛙、羊……units.json 都是 15 血）

# ★ 09-22 人口尽量控制在 80 以内。维护费 51~80 收入 70%、81~100 只剩 40%
#   （battle.net upkeep，09-15 调研）—— 过 80 每一块金子都打四折。
FOOD_SOFT_CAP = 80             # 兵/农民/英雄/复活都算到这里为止（见 build.Planner；已下单还没出来的兵也算，runtime.queued_food）
# ★ 09-22 人口不能冲过 80，到 79 之前就要开始控制。f58f9a3 之后 5 局里 4 局到过 81~85：
#   英雄没变（一直 2 个），是生产队列 —— 兵开始训练才占人口，排在后面的还没占，一拍里好几个兵一起开练（309 80->85、311 73->82）。
FOOD_FARM_CAP = 80             # 人口建筑盖到上限 >= 这个就停（原来 86 = 80 + 一个英雄的空；英雄现在也算在 80 里）
# ★★ 09-22 晚 1-403 拆家阶段：金子攒到 4000 多仍不多造拆家单位；后期人口上限放到 100。
#   1-403 最后 3 分钟人口卡 80/84、兵 15~17、金 1900 -> 4254、木 6045 —— 钱花不出去时维护费打几折都无所谓。
#   ★ 09-22 晚 1-411 收紧条件：只在终局大优势、敌军基本被消灭时突破 80（收尾拆家，runtime._finishing），
#   其它时间守在 80 以内（81~100 收入 -60%）。原来的「三本 + 金子 >= 600」不用了（runtime.food_soft_cap）。
FOOD_SOFT_CAP_LATE = 100
FOOD_FARM_CAP_LATE = 100
# 09-22 晚：空中作战单位（和毫秒层 war3-modules/mod_combat.AIR_TYPES 同一张表，这里补上暗夜的奇美拉 / 精灵龙 / 鸦德；改一边看另一边）
AIR_UNITS = frozenset({"hgry", "hgyr", "hdhw", "owyv", "obat", "otbr", "ugar", "ufro", "ubsp", "ehip", "nzep",
                       "echm", "efdr", "edtm"})
# 施法单位（按它们多不多决定要不要多出破法者）：萨满 / 巫医 / 灵魂行者 / 通灵僧 / 女妖 / 熊德 / 鸟德 / 女巫 / 牧师
ENEMY_CASTERS = frozenset({"oshm", "odoc", "ospw", "ospm", "unec", "uban", "edot", "edoc", "hsor", "hmpr"})

LUMBER_DROPS = frozenset({"hlum", "ofor", "ugrv"})   # 伐木场 / 战争磨坊 / 墓地：木头就近交到这里（暗夜小精灵不用来回跑）

RACES = {
    "human": dict(worker="hpea", supply="hhou", altar="halt", halls=("htow", "hkee", "hcas"), militia="hmil",
                  heroes=("Hamg", "Hmkg", "Hpal", "Hblm")),
    "orc": dict(worker="opeo", supply="otrb", altar="oalt", halls=("ogre", "ostr", "ofrt"), militia=None,
                heroes=("Obla", "Ofar", "Otch", "Oshd")),
    "undead": dict(worker="uaco", supply="uzig", altar="uaod", halls=("unpl", "unp1", "unp2"), militia=None,
                   heroes=("Udea", "Ulic", "Udre", "Ucrl")),
    "nightelf": dict(worker="ewsp", supply="emow", altar="eate", halls=("etol", "etoa", "etoe"), militia=None,
                     heroes=("Ekee", "Emoo", "Edem", "Ewar")),
}
RACE_BY_LETTER = {"h": "human", "o": "orc", "u": "undead", "e": "nightelf"}


@lru_cache(maxsize=1)
def units() -> dict:
    with open(UNITS_JSON, encoding="utf-8") as fh:
        d = json.load(fh)
    return d if isinstance(d, dict) else {u["id"]: u for u in d}


@lru_cache(maxsize=1)
def _skill_reqs() -> dict:
    """技能码 -> (第 1 级要英雄几级 reqLevel, 每多一级再加几级 levelSkip, 最多几级 levels)（AbilityData.slk）。读不到返回 {}。"""
    try:
        from w3slk import read_slk
        rows = read_slk(w3paths.game_data("AbilityData.slk"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows:
        code = r.get("alias") or r.get("code")
        try:
            out[code] = (int(r.get("reqLevel") or 1), int(r.get("levelSkip") or 2), int(r.get("levels") or 3))
        except (TypeError, ValueError):
            continue
    return out


def skill_level_needed(code: str, rank: int) -> int | None:
    """学技能 code 的第 rank 级要英雄几级：reqLevel + (rank-1) × levelSkip（普通技能 1/3/5，大招 6）。
    超过技能最高级返回 99（学不了）；表里没有这个码返回 None（不挡）。"""
    req = _skill_reqs().get(code)
    if req is None:
        return None
    first, skip, levels = req
    if rank > levels:
        return 99
    return first + (rank - 1) * (skip or 2)


def stat(t: str, key: str, default=0):
    v = (units().get(t) or {}).get(key)
    return default if v in (None, "") else v


def cost(t: str) -> tuple[int, int]:
    return int(stat(t, "gold", 0)), int(stat(t, "lumber", 0))


def food(t: str) -> int:
    return int(stat(t, "food", 0))


def is_hero(t: str) -> bool:
    return bool(t) and "A" <= t[0] <= "Z"


def is_building(t: str) -> bool:
    # ★ 09-18：units.json 里亡灵大厅 unpl/unp1/unp2 的 foot 是 0 —— 只看 foot 会把亡灵大厅当成非建筑，大脑判「没有大厅」10 秒认输
    return t in HALLS or float(stat(t, "foot", 0)) > 0


def is_worker(t: str) -> bool:
    return t in WORKERS


def is_critter(u) -> bool:
    """小动物：中立被动、不是金矿/建筑、血很少。它们挡建筑落点（引擎不许把地基压在不归你管的单位身上），
    而且是中立不是敌人 —— 右键只会跟着走，要攻击令才打。"""
    return (u.owner == PASSIVE_OWNER and u.hp > 0 and u.type not in MINES and not is_building(u.type)
            and 0 < (getattr(u, "hp_max", 0) or u.hp) <= CRITTER_HP_MAX)


def is_tower(t: str) -> bool:
    return t in TOWERS


def race_of(t: str) -> str | None:
    return RACE_BY_LETTER.get(t[:1].lower()) if t else None


UPGRADE_SLK = w3paths.game_data("UpgradeData.slk")


def read_slk(path: str) -> list[dict]:
    """SLK 表 -> 行字典列表（和 war3-analytics/parse_slk.py 同一个读法；那个文件一导入就会重写 units.json，所以不导入它）。
    ⚠ sdk/python/w3slk.py 是同一份读法的 SDK 版：P3 迁移时这里改用它，之前改一处要改两处。"""
    cells, maxx, maxy, y = {}, 0, 0, 1
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("C;"):
                continue
            x = val = None
            for part in line.rstrip("\r\n").split(";")[1:]:
                if not part:
                    continue
                k, rest = part[0], part[1:]
                if k == "X":
                    x = int(rest)
                elif k == "Y":
                    y = int(rest)
                elif k == "K":
                    val = rest
            if x is None or val is None:
                continue
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            cells[(x, y)] = val
            maxx, maxy = max(maxx, x), max(maxy, y)
    headers = {x: cells[(x, 1)] for x in range(1, maxx + 1) if (x, 1) in cells}
    rows = []
    for yy in range(2, maxy + 1):
        row = {h: cells[(x, yy)] for x, h in headers.items() if (x, yy) in cells}
        if row:
            rows.append(row)
    return rows


@lru_cache(maxsize=1)
def upgrades() -> dict:
    return {r["upgradeid"]: r for r in read_slk(UPGRADE_SLK) if r.get("upgradeid")}


def _num(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def upgrade_cost(code: str, level: int = 1) -> tuple[int, int]:
    """研究第 level 级的价格 = base + mod × (level-1)（UpgradeData.slk goldbase/goldmod/lumberbase/lumbermod）。"""
    r = upgrades().get(code)
    if not r:
        return 0, 0
    k = max(0, level - 1)
    return (int(_num(r.get("goldbase")) + _num(r.get("goldmod")) * k),
            int(_num(r.get("lumberbase")) + _num(r.get("lumbermod")) * k))


def upgrade_max_level(code: str) -> int:
    return int(_num((upgrades().get(code) or {}).get("maxlevel"), 1))


# 升本价：units.json 里 hkee/hcas 是**累计价**（705/1065），真实要花的是差价（v251 踩过）
HALL_CHAIN = {"hkee": "htow", "hcas": "hkee", "ostr": "ogre", "ofrt": "ostr", "unp1": "unpl", "unp2": "unp1", "etoa": "etol", "etoe": "etoa"}


def hall_upgrade_cost(code: str) -> tuple[int, int]:
    base = HALL_CHAIN.get(code)
    g, l = cost(code)
    if not base:
        return g, l
    bg, bl = cost(base)
    return g - bg, l - bl


def tier_of_halls(types) -> int:
    ts = set(types)
    if ts & TIER3_HALLS:
        return 3
    if ts & TIER2_HALLS:
        return 2
    return 1

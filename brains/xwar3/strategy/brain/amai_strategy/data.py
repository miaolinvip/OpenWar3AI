# -*- coding: utf-8 -*-
"""AMAI 数据访问层：把 brain/amai_data/*.json（tools/amai_import.py 导出的 AMAI TFT 表）包成按 4 字码查的接口。

只读，不做决策。行号引用的是 AMAI 源码（third_party/AMAI，提交 ea95cfd）：
  StandardUnits 的列语义       common.eai:2560-2700（TownCountEx / GetUnitCountAlt）
  buy type 常量                common.eai:219-229
  needed1/2/3 与 UPGRADED      common.eai:10216-10276（RefreshNeeded）
  各族 racial_* / Settings     races.eai:127-180、TFT/<Race>/Settings.txt
价格/人口/建造时间仍取 units.json（brain/gamedata.py），因为那是我们实机验证过的一份。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from .. import gamedata as G

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "amai_data")

# Races.txt 的行序（R_ELF=1 R_HUMAN=2 R_ORC=3 R_UNDEAD=4，common.eai:159-167）
RACES = ("nightelf", "human", "orc", "undead")
RACE_INDEX = {r: i + 1 for i, r in enumerate(RACES)}
RACE_NAME = {"nightelf": "ELF", "human": "HUMAN", "orc": "ORC", "undead": "UNDEAD"}
NAME_RACE = {v: k for k, v in RACE_NAME.items()}
# Strengths.txt 的行序（DetermineCounterForce 的 chosen_counter 就是这个下标）
CATEGORIES = ("air", "casters", "towers", "piercing", "normal", "siege", "magic",
              "unarmored", "lightarmor", "mediumarmor", "heavyarmor")
UPGRADED = "UPGRADED"
# common.eai:219-229
BT = {"UNIT": 0, "BUILDING": 1, "HERO": 2, "UPGRADE": 3, "ML_UPGRADE": 4, "RACIAL_ITEM": 5,
      "NEUTRAL_HERO": 6, "MERC": 7, "GOBLIN_MERC": 8, "DRAGON": 9, "MERCHANT_ITEM": 10}


@lru_cache(maxsize=8)
def load(name: str) -> dict:
    with open(os.path.join(DATA_DIR, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _unit_rows() -> dict:
    """4 字码 -> StandardUnits 行（同码多行取第一行：别名行数据一致，导入报告第六节）。"""
    out = {}
    for r in load("common")["standard_units"]["rows"]:
        out.setdefault(r["4-letter-code"], r)
    return out


@lru_cache(maxsize=1)
def _tc_add() -> dict:
    """UnitEquivalence.txt 的 TC_ADD：本体码 -> 另外要一起数的码（common.eai:2680）。"""
    out = {}
    for r in load("common")["unit_equivalence"]["rows"]:
        if r.get("Equivalence Type") == "TC_ADD":
            c = (r.get("codes") or {})
            if c.get("Name in AI") and c.get("Equivalent Unit"):
                out.setdefault(c["Name in AI"], []).append(c["Equivalent Unit"])
    return out


@lru_cache(maxsize=1)
def _needed_extra() -> list:
    """NeededExtra.txt：(本体码, 类型, 数量, 需要的码)（common.eai:10271）。"""
    rows = []
    for r in load("common")["needed_extra"]["rows"]:
        c = r.get("codes") or {}
        if c.get("Name in AI") and c.get("Id needed"):
            rows.append((c["Name in AI"], r["Type Needed"], int(r["Number needed"]), c["Id needed"]))
    return rows


def unit(code: str) -> dict | None:
    return _unit_rows().get(code) if code else None


def buy_type(code: str) -> int:
    r = unit(code)
    return BT.get((r or {}).get("buy type", ""), -1)


def types(code: str) -> list:
    return list((unit(code) or {}).get("types") or [])


def is_structure(code: str) -> bool:
    """AMAI 用 IsUnitIdType(STRUCTURE)；这里按 buy type = BUILDING（StandardUnits 第 5 列）。"""
    return buy_type(code) == BT["BUILDING"]


def needed(code: str) -> tuple:
    """(needed1, needed2, needed3)；needed3 == "UPGRADED" 表示本体由 needed1 升级而来。"""
    r = unit(code)
    if not r:
        return (None, None, None)
    c = r.get("codes") or {}
    n3 = c.get("needed3")
    if not n3 and r.get("needed3") == UPGRADED:
        n3 = UPGRADED
    return (c.get("built in/by, needed1"), c.get("needed2"), n3)


def is_upgraded_from(code: str) -> str | None:
    """本体是由哪座建筑升级来的（哨塔 -> 箭塔、主城 -> 要塞）；不是升级关系返回 None。"""
    n1, _n2, n3 = needed(code)
    return n1 if n3 == UPGRADED else None


def same_upgrade_line(a: str, b: str) -> bool:
    """isSameUpgradeLine（common.eai:2594）：a、b 是不是同一条升级链上的两个形态。"""
    if not a or not b:
        return False
    for x, y in ((a, b), (b, a)):
        cur, depth = x, 0
        while cur and depth < 6:
            n1, _n2, n3 = needed(cur)
            if n3 == UPGRADED and n1 == y:
                return True
            cur = n1 if n3 == UPGRADED else None
            depth += 1
    return False


def count_alts(code: str, exclude: str | None = None) -> list:
    """GetUnitCountAlt（common.eai:2655）：还要一起数的码 = add for counting 1/2 + TC_ADD；
    排除 exclude 本身和与它同升级链的（避免"升级目标"把源建筑数重了）。"""
    r = unit(code)
    out = []
    if r:
        c = r.get("codes") or {}
        for key in ("add for counting 1", "add for counting 2"):
            v = c.get(key)
            if v:
                out.append(v)
    out += _tc_add().get(code, [])
    if exclude:
        out = [x for x in out if x != exclude and not same_upgrade_line(x, exclude)]
    return out


def needed_extra(code: str) -> list:
    return [(t, n, need) for (c, t, n, need) in _needed_extra() if c == code]


def cat_weight(code: str, cat: str) -> float:
    """StandardUnits 第 17~27 列：这个兵种给 <类别> 强度贡献多少（common.eai:5550）。"""
    r = unit(code)
    return float((r or {}).get(f"{cat} strength") or 0)


def hero_bonus(code: str) -> int:
    """第 16 列 bonus hero strength（GetPlayerHeroBonusStrength，common.eai:5403）。"""
    r = unit(code)
    return int((r or {}).get("bonus hero strength") or 0)


def item_price(code: str) -> tuple:
    r = unit(code) or {}
    return int(r.get("gold_price (items only)") or 0), int(r.get("wood_price (items only)") or 0)


def cost(code: str) -> tuple:
    """要花的钱：升级形态（要塞/箭塔）取差价，物品取表里的价，其余取 units.json。"""
    src = is_upgraded_from(code)
    g, l = G.cost(code)
    if src:
        sg, sl = G.cost(src)
        return max(0, g - sg), max(0, l - sl)
    if buy_type(code) in (BT["RACIAL_ITEM"], BT["MERCHANT_ITEM"]):
        ig, il = item_price(code)
        if ig or il:
            return ig, il
    return g, l


@lru_cache(maxsize=1)
def _global_settings() -> dict:
    out = {}
    for r in load("common")["global_settings"]["rows"]:
        out[r["Variable Setting"]] = r.get("parsed", r.get("Value"))
    return out


def peon_target(d: "AmaiData", mines: int, wood: float) -> int:
    """GetPeonTargetForMines（common.eai:9931）：木头越多农民越少（去砍树的那部分省下来）。

    人族（TFT/Human/Settings.txt）：1 矿 木 <=200 -> 14、>=2000 -> 8；2 矿 18 -> 14；3 矿以上 22 -> 19，中间线性插值。
    ★ 09-21 起 AMAI 战略引擎和**手写建造表**共用这一份（手写表也按 AMAI 的方式算）。
      手写表原来是「每矿 5 个 + 伐木人数」，一座矿木 500 时只要 7 个农民，AMAI 要 13 个 ——
      一直少造三到五成，6-182 局一座矿 10 个农民、金子始终不够用。"""
    if d.setting("race_has_ghouls"):
        if mines <= 1:
            return d.setting_int("min_peon_number_single_mine", 5)
        if mines <= 2:
            return d.setting_int("min_peon_number_double_mine", 10)
        return d.setting_int("min_peon_number_multi_mine", 15)
    lo_w = d.setting_int("maximum_peon_wood", 200)
    hi_w = d.setting_int("minimum_peon_wood", 2000)
    if mines <= 1:
        hi, lo = d.setting_int("peon_1_mine_number", 14), d.setting_int("min_peon_number_single_mine", 8)
    elif mines <= 2:
        hi, lo = d.setting_int("peon_2_mines_number", 18), d.setting_int("min_peon_number_double_mine", 14)
    else:
        hi, lo = d.setting_int("peon_mines_number", 22), d.setting_int("min_peon_number_multi_mine", 19)
    from .choose import lerp
    return max(int(lerp(lo_w, hi_w, hi, lo, wood)), lo)


class AmaiData:
    """一个种族的全部 AMAI 数据。构造很便宜（JSON 只读一次，按进程缓存）。"""

    def __init__(self, race: str):
        if race not in RACES:
            raise ValueError(race)
        self.race = race
        self.json = load(race)
        self.common = load("common")
        self.settings = dict(_global_settings())
        for r in self.json["settings"]["rows"]:      # 种族 Settings.txt 覆盖全局（races.eai:974）
            self.settings[r["Variable Setting"]] = r.get("parsed", r.get("Value"))
        self.strategies = sorted(self.json["strategies"], key=lambda s: s["strat_id"])
        self.strategy_names = [s["Strategy Function Name"] for s in self.strategies]
        self.heroes = self.json["heroes"]["rows"]
        self.hero_skill_rows = self.json["hero_skills"]["rows"]
        self.profiles = self.common["profiles"]["rows"]
        self.prediction_rules = self.common["strength_prediction_rules"]["rows"]
        self.upkeep = self.common["upkeep"]["rows"]
        self.added_aggression = self.common["added_aggression"]["rows"]
        self.strengths = self.common["strengths"]["rows"]

    # ── 种族固定单位 ──
    def racial(self, key: str) -> str | None:
        v = (self.json["racial_units"] or {}).get(key)
        if isinstance(v, dict):
            return v.get("code")
        if isinstance(v, list):
            return v[0] if v else None
        return None

    @property
    def halls(self) -> list:
        return [self.racial(f"racial_hall[{i}]") for i in (1, 2, 3)]

    def hall_tier(self, code: str) -> int:
        """这个码是第几本主城；不是主城返回 0。"""
        halls = self.halls
        return halls.index(code) + 1 if code in halls else 0

    # ── 设置 ──
    def setting(self, name: str, default=None):
        v = self.settings.get(name, default)
        if isinstance(v, dict) and set(v) & {"code", "rawcode", "str"}:
            return v.get("code") or v.get("rawcode") or v.get("str")
        return v

    def setting_int(self, name: str, default: int = 0) -> int:
        v = self.setting(name, default)
        return int(v) if isinstance(v, (int, float)) else default

    def setting_float(self, name: str, default: float = 0.0) -> float:
        v = self.setting(name, default)
        return float(v) if isinstance(v, (int, float)) else default

    # ── 战略表 ──
    def strategy(self, name: str) -> dict | None:
        for s in self.strategies:
            if s["Strategy Function Name"] == name:
                return s
        return None

    def build_block(self, name: str) -> dict | list | None:
        return (self.json.get("build") or {}).get(name)

    def global_init(self) -> list:
        return self.build_block("global_init") or []

    def global_build(self, tier: int) -> list:
        blk = self.build_block("global_build") or {}
        return (blk.get("tiers") or {}).get(str(tier), [])

    def strategy_init(self, name: str) -> list:
        blk = self.build_block(name) or {}
        return blk.get("init", []) if isinstance(blk, dict) else []

    def strategy_build(self, name: str, tier: int) -> list:
        blk = self.build_block(name) or {}
        return ((blk.get("tiers") or {}).get(str(tier), [])) if isinstance(blk, dict) else []

    # ── 英雄 ──
    def hero_rows(self) -> list:
        return self.heroes

    def skill_codes(self, hero_code: str, slot: int, line: str) -> list:
        rows = [r for r in self.hero_skill_rows if r.get("hero_code") == hero_code and r.get("line") == line]
        row = next((r for r in rows if r.get("hero_slot") == slot), rows[0] if rows else None)
        return list(row["codes"]) if row else []

    # ── 种族进攻性表 RaceAggression.txt（行 = 对手种族，列 = 我方 tier）──
    def race_aggression(self) -> dict:
        out = {}
        for r in self.json["race_aggression"]["rows"]:
            out[NAME_RACE.get(r["race"], r["race"])] = (int(r["Tier1"]), int(r["Tier2"]), int(r["Tier3"]))
        return out

    def strategy_race_aggression(self, name: str) -> dict:
        """Strategy.txt 第 45~56 列：这个战略对某族在某本额外加多少进攻性。"""
        s = self.strategy(name) or {}
        out = {}
        for rname, race in NAME_RACE.items():
            out[race] = tuple(int(s.get(f"enemy_{rname} add agg t{t}", 0) or 0) for t in (1, 2, 3))
        return out

# -*- coding: utf-8 -*-
"""单位战斗属性与伤害计算（纯数据，不连游戏）：职业玩家算"打不打得过、先打谁、升几级攻防"的底子。

    from openwar3 import combat
    s = combat.stats("hfoo", tech={"Rhme": 1, "Rhar": 2})      # 步兵：攻一防二
    s.dps_vs(combat.stats("ogru"))                             # 对兽人步兵的有效每秒伤害（算上克制和护甲）
    combat.time_to_kill([s] * 6, combat.stats("ogru"))          # 6 个步兵多久打死 1 个兽人步兵

来源（全部是本机游戏 MPQ 里的暴雪数据表，data/game/，`python data/tools/extract_game_data.py` 重建）：
  UnitBalance.slk（血、护甲、护甲类型、移速、视野、英雄三围、能用的升级）、UnitWeapons.slk（两把武器：能打什么、射程、
  间隔、骰子、溅射）、UpgradeData.slk（升级效果）、MiscGame.txt（伤害克制表、护甲系数、三围换算）。
⚠ 不含：物品、buff/光环、技能被动（重击、闪避）、英雄等级之外的临时加成。实战里把它当"基准值"，再用实时血量/事件校正。
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

import w3paths
from w3slk import read_slk

ARMOR_TYPES = ["small", "medium", "large", "fort", "normal", "hero", "divine", "none"]   # MiscGame DamageBonus* 的列顺序
_ATTACK_KEY = {"normal": "Normal", "pierce": "Pierce", "siege": "Siege", "magic": "Magic", "chaos": "Chaos",
               "spells": "Spells", "hero": "Hero"}


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def misc_game() -> dict:
    """MiscGame.txt 的 键=值（值是字符串）。"""
    out = {}
    try:
        with open(w3paths.game_data("MiscGame.txt"), encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                m = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", ln)
                if m and m.group(1) not in out:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


@lru_cache(maxsize=1)
def _tables():
    def by(name, key):
        try:
            return {r[key]: r for r in read_slk(w3paths.game_data(name)) if r.get(key)}
        except OSError:
            return {}
    return by("UnitBalance.slk", "unitBalanceID"), by("UnitWeapons.slk", "unitWeapID"), by("UpgradeData.slk", "upgradeid")


def damage_multiplier(attack_type: str, armor_type: str) -> float:
    """攻击类型打护甲类型的倍率（MiscGame DamageBonus*）。例：pierce 打 small = 2.0、magic 打 large = 2.0、siege 打 fort = 1.5。"""
    row = misc_game().get("DamageBonus" + _ATTACK_KEY.get(attack_type, "Normal"))
    if not row or armor_type not in ARMOR_TYPES:
        return 1.0
    vals = [_num(v, 1.0) for v in row.split(",")]
    i = ARMOR_TYPES.index(armor_type)
    return vals[i] if i < len(vals) else 1.0


def armor_factor(armor: float) -> float:
    """护甲把伤害乘以多少：正护甲 1 - 0.06a/(1+0.06a)（每点约减 6%，递减），负护甲 2 - 0.94^(-a)（每点约加 6%）。"""
    k = _num(misc_game().get("DefenseArmor"), 0.06)
    if armor >= 0:
        return 1.0 - (armor * k) / (1.0 + armor * k)
    return 2.0 - (1.0 - k) ** (-armor)


@dataclass
class Weapon:
    targets: frozenset          # 能打什么：ground / air / structure / tree ...
    range: float
    cooldown: float             # 攻击间隔（秒；英雄已算敏捷加速）
    dmg_min: float
    dmg_max: float
    attack_type: str            # normal / pierce / siege / magic / chaos / spells / hero
    splash: float = 0.0         # 溅射半径（0 = 单体）
    min_range: float = 0.0      # 最近攻击距离（迫击炮 250）

    @property
    def dmg_avg(self) -> float:
        return (self.dmg_min + self.dmg_max) / 2.0

    @property
    def dps(self) -> float:
        """面板每秒伤害（不算克制和护甲）。"""
        return self.dmg_avg / self.cooldown if self.cooldown > 0 else 0.0


@dataclass
class UnitStats:
    code: str
    hp: float
    mana: float
    armor: float
    armor_type: str
    speed: float
    sight_day: float
    sight_night: float
    weapons: list = field(default_factory=list)
    level: int = 0                      # 英雄等级（非英雄 0）
    attributes: dict = field(default_factory=dict)   # 英雄 {'str','agi','int','primary'}
    is_air: bool = False
    applied: list = field(default_factory=list)      # 算进去的升级 [(码, 等级)]
    unhandled: list = field(default_factory=list)    # 升级里没建模的效果码（知道自己漏了什么）

    def weapon_vs(self, target: "UnitStats"):
        """打 target 用哪把武器（按 targs：空中单位要能打 air 的那把）；打不到返回 None。"""
        want = "air" if target.is_air else ("structure" if target.armor_type == "fort" else "ground")
        for w in self.weapons:
            if want in w.targets or (want == "structure" and "ground" in w.targets):
                return w
        return None

    def dps_vs(self, target: "UnitStats") -> float:
        """对 target 的有效每秒伤害 = 平均伤害 × 克制倍率 × 护甲系数 / 攻击间隔。打不到 = 0。"""
        w = self.weapon_vs(target)
        if w is None or w.cooldown <= 0:
            return 0.0
        return w.dmg_avg * damage_multiplier(w.attack_type, target.armor_type) * armor_factor(target.armor) / w.cooldown

    def hits_to_kill(self, target: "UnitStats", target_hp: float | None = None) -> int | None:
        w = self.weapon_vs(target)
        if w is None:
            return None
        per = w.dmg_avg * damage_multiplier(w.attack_type, target.armor_type) * armor_factor(target.armor)
        return math.ceil((target.hp if target_hp is None else target_hp) / per) if per > 0 else None


def _weapons(code: str, wrow: dict, tech_dice: float, tech_range: float, agi_speed: float, hero_dmg: float) -> list:
    out = []
    if not wrow:
        return out
    on = int(_num(wrow.get("weapsOn"), 1))
    for i in (1, 2):
        if not on & i:
            continue
        targs = frozenset(t.strip() for t in (wrow.get(f"targs{i}") or "").split(",") if t.strip())
        cool = _num(wrow.get(f"cool{i}"))
        if not targs or cool <= 0:
            continue
        dice, sides, plus = _num(wrow.get(f"dice{i}"), 1), _num(wrow.get(f"sides{i}"), 1), _num(wrow.get(f"dmgplus{i}"))
        dice += tech_dice
        out.append(Weapon(targs, _num(wrow.get(f"rangeN{i}")) + tech_range, cool / (1.0 + agi_speed),
                          plus + dice + hero_dmg, plus + dice * sides + hero_dmg,
                          (wrow.get(f"atkType{i}") or "normal").strip(),
                          _num(wrow.get(f"Qarea{i}")) or _num(wrow.get(f"Harea{i}")) or _num(wrow.get(f"Farea{i}")),
                          _num(wrow.get("minRange"))))
    return out


def stats(code: str, tech: dict | None = None, hero_level: int | None = None) -> UnitStats | None:
    """code 的战斗属性。tech = {升级码: 等级}（比如 g.tech_many 的结果），只算这个单位能用的那些；
    英雄给 hero_level 按三围成长算（力量加血和主属性伤害、敏捷加护甲和攻速、智力加蓝）。表里没有返回 None。"""
    bal, weap, upg = _tables()
    b = bal.get(code)
    if b is None:
        return None
    w = weap.get(code, {})
    tech = tech or {}
    mg = misc_game()
    hp = _num(b.get("realHP") or b.get("HP"))
    mana = _num(b.get("realM") or b.get("mana0"))
    armor = _num(b.get("def"))
    attrs, hero_dmg, agi_speed, lvl = {}, 0.0, 0.0, 0
    if b.get("Primary") and b.get("Primary") not in ("-", "_"):
        lvl = max(1, int(hero_level or 1))
        attrs = {k.lower(): _num(b.get(k)) + _num(b.get(k + "plus")) * (lvl - 1) for k in ("STR", "AGI", "INT")}
        attrs = {k: math.floor(v) for k, v in attrs.items()}
        attrs["primary"] = b["Primary"].lower()
        base_hp, base_mana = _num(b.get("HP")), _num(b.get("manaN"))     # 英雄蓝 = manaN + 智力×15（大法师 1 级 285）
        hp = base_hp + attrs["str"] * _num(mg.get("StrHitPointBonus"), 25)
        mana = base_mana + attrs["int"] * _num(mg.get("IntManaBonus"), 15)
        armor = _num(b.get("def")) + _num(mg.get("AgiDefenseBase"), -2) + attrs["agi"] * _num(mg.get("AgiDefenseBonus"), 0.3)
        agi_speed = attrs["agi"] * _num(mg.get("AgiAttackSpeedBonus"), 0.02)
        hero_dmg = attrs[attrs["primary"]] * _num(mg.get("StrAttackBonus"), 1.0)
    applied, unhandled = [], []
    tech_dice = tech_range = speed_bonus = 0.0
    allowed = {u.strip() for u in (b.get("upgrades") or "").split(",") if u.strip()}
    for up, level in tech.items():
        if up not in allowed or not level:
            continue
        row = upg.get(up, {})
        applied.append((up, int(level)))
        for i in (1, 2, 3, 4):
            eff = (row.get(f"effect{i}") or "").strip()
            if not eff or eff in ("-", "_"):
                continue
            total = _num(row.get(f"base{i}")) + _num(row.get(f"mod{i}")) * (level - 1)
            if eff == "ratd":                        # 攻击骰子 +1/级
                tech_dice += total or level
            elif eff == "rarm":                      # 护甲：每级加单位自己的 defUp
                armor += _num(b.get("defUp"), 2) * level
            elif eff == "rhpx":
                hp += total
            elif eff == "rmnx":
                mana += total
            elif eff == "ratr":
                tech_range += total
            elif eff == "rmvx":
                speed_bonus += total
            else:
                unhandled.append(f"{up}:{eff}")
    return UnitStats(code, hp, mana, round(armor, 2), (b.get("defType") or "normal").strip(),
                     _num(b.get("spd")) + speed_bonus, _num(b.get("sight")), _num(b.get("nsight")),
                     _weapons(code, w, tech_dice, tech_range, agi_speed, hero_dmg), lvl, attrs,
                     _is_air(code), applied, unhandled)


@lru_cache(maxsize=1)
def _air_units() -> frozenset:
    """飞行单位（UnitData.slk movetp = fly）。"""
    try:
        return frozenset(r["unitID"] for r in read_slk(w3paths.game_data("UnitData.slk"))
                         if (r.get("movetp") or "").strip() == "fly")
    except (OSError, KeyError):
        return frozenset()


def _is_air(code: str) -> bool:
    return code in _air_units()


def sight(code: str, night: bool = False) -> float:
    """视野半径（UnitBalance sight / nsight）；表里没有按白天 1400 / 夜里 800。"""
    b = _tables()[0].get(code)
    if b is None:
        return 800.0 if night else 1400.0
    return _num(b.get("nsight" if night else "sight"), 800.0 if night else 1400.0)


def time_to_kill(attackers: list, target: UnitStats, target_hp: float | None = None) -> float | None:
    """一群攻击者（UnitStats 列表）一起打 target 要几秒（不算走位、溅射、过量伤害）。没人打得到 = None。"""
    total = sum(a.dps_vs(target) for a in attackers)
    if total <= 0:
        return None
    return (target.hp if target_hp is None else target_hp) / total

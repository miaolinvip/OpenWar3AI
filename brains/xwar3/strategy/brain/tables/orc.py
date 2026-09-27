# -*- coding: utf-8 -*-
"""兽族建造表（AMAI TFT/Orc/BuildSequence.ai：global_init_strategy 的祭坛/地洞/兵营/战争磨坊前置 + NormalOrc 三本）。

和人族表的差别：人口建筑是地洞 otrb（10 人口）；远程兵猎头者要战争磨坊；二本兽栏（掠夺者）、灵魂归宿（萨满），三本牛头人图腾。
兽族苦工和人族农民一样要守着工地（续建任务照开），开矿 = 在矿边盖大厅。
没搬的：地洞防守、瞭望塔前压、巫毒商店物品、雇佣兵、DynamicBuildUnit。
"""
from __future__ import annotations

from .. import gamedata as G
from ..build import Want
from . import amai_peons, tier_up_priority

PROFILE = dict(
    name="NormalOrc",
    farm_left=6,
    peons_per_mine=5,
    peons_lumber=7,
    peons_max=26,
    tier2_army=4,               # 兵够这么多就先升二本（tables.tier_up_priority）
    min_attack_strength=4,
    heroes=("Obla", "Ofar", "Otch"),
)

MELEE = ("ogru", "orai", "otau", "okod")
RANGED = ("ohun", "owyv", "otbr", "ocat")


def _n(world, *codes) -> int:
    return sum(world.counts.get(c, 0) for c in codes)


def wants(world, profile=PROFILE) -> list[Want]:
    w: list[Want] = []
    tier = world.tier
    heroes = profile["heroes"]
    halls = _n(world, "ogre", "ostr", "ofrt")
    production = _n(world, "obar", "obea", "osld", "otto")

    w.append(Want("building", "oalt", 1, 96, "base", "风暴祭坛（英雄先出）"))
    w.append(Want("hero", heroes[0], 1, 95, note="首英雄"))
    w.append(Want("building", "obar", 1, 90, "base", "兵营"))

    left = profile["farm_left"] + 2 * production
    if world.food_cap < world.farm_cap and world.food_free <= left:      # 80 人口软上限，见 human.py 同一行
        done = sum(1 for b in world.my_buildings if b.type == "otrb" and b.hp_max > 0 and b.hp >= 0.97 * b.hp_max)
        need = max(1, -(-(left - world.food_free + 1) // 10))
        w.append(Want("building", "otrb", done + min(need, 3), 92, "base", "人口快满（地洞）"))

    # ★ 09-21：苦工目标照 AMAI PeonBuilder（见 tables.amai_peons）。这里先算「现有矿」的目标给下面的升本闸用；
    #   真正的苦工 Want 在函数末尾加（要先知道这一轮想不想开矿：想开就按下一座矿的数量先造）。
    peons = min(profile["peons_max"], amai_peons(world, "orc", halls, False))
    w.append(Want("building", "ofor", 1, 72, "base", "战争磨坊（猎头者前置）"))

    if tier == 1:
        # NormalOrc tier 1：兽人步兵 2@60、猎头者、巫毒商店（不搬）、猎头者 3@46、步兵 4@38
        w.append(Want("unit", "ogru", 2, 66, note="兽人步兵"))
        w.append(Want("unit", "ohun", 1, 58, note="猎头者"))
        w.append(Want("unit", "ogru", 4, 56))
        w.append(Want("unit", "ohun", 3, 52))
        w.append(Want("unit", "ogru", 6, 44))
        if _n(world, "opeo") >= min(peons, 12):
            w.append(Want("upgrade_hall", "ostr", 1, tier_up_priority(len(world.my_army), profile, 42), note="升二本"))
        w.append(Want("expand", "ogre", 2, 53, "mine", "第二座矿"))
    elif tier == 2:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 60, note="二英雄"))
        w.append(Want("building", "obea", 1, 56, "base", "兽栏"))
        w.append(Want("unit", "orai", 3, 50, note="掠夺者"))
        w.append(Want("building", "osld", 1, 52, "base", "灵魂归宿"))
        w.append(Want("unit", "oshm", 2, 52, note="萨满"))
        w.append(Want("unit", "ogru", 6, 48))
        w.append(Want("unit", "ohun", 4, 46))
        w.append(Want("unit", "ocat", 2, 44, note="投石车"))
        w.append(Want("upgrade", "Robs", 1, 45, note="狂暴力量"))
        w.append(Want("upgrade", "Rost", 1, 44, note="萨满进阶"))
        if _n(world, "opeo") >= min(peons, 12):
            w.append(Want("upgrade_hall", "ofrt", 1, 40, note="升三本"))
        w.append(Want("expand", "ogre", 2, 53, "mine"))
    else:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 60))
        w.append(Want("hero", heroes[2], 1, 50))
        w.append(Want("building", "otto", 1, 55, "base", "牛头人图腾"))
        w.append(Want("unit", "otau", 4, 52, note="牛头人"))
        w.append(Want("building", "obea", 1, 56, "base"))
        w.append(Want("unit", "owyv", 3, 48, note="双足飞龙"))
        w.append(Want("unit", "orai", 4, 46))
        w.append(Want("unit", "ogru", 6, 44))
        w.append(Want("unit", "oshm", 3, 46))
        w.append(Want("unit", "okod", 1, 48, note="科多兽"))
        w.append(Want("expand", "ogre", 3, 45, "mine", "第三座矿"))

    fighters = len(world.my_army)
    if halls < 2 and (fighters >= profile.get("expand_army", 4) or world.t >= 360):
        w.append(Want("expand", "ogre", 2, 83, "mine", "第二座矿插到出兵前面"))   # 66 -> 83，理由见人族表同一行
    if world.gold >= profile.get("rich_gold", 1500):
        w.append(Want("expand", "ogre", halls + 1, 58, "mine", "钱多了开矿"))
        w.append(Want("building", "obar", 2, 44, "base", "钱多了加兵营"))

    melee = _n(world, *MELEE)
    ranged = _n(world, *RANGED)
    if melee >= 5:
        w.append(Want("upgrade", "Rome", min(3, tier), 35, note="近战武器"))
        w.append(Want("upgrade", "Roar", min(3, tier), 35, note="护甲"))
    if ranged >= 4:
        w.append(Want("upgrade", "Rora", min(3, tier), 35, note="远程武器"))
    if _n(world, "ohun") >= 4 and tier >= 2:
        w.append(Want("upgrade", "Rotr", 1, 38, note="巨魔再生"))

    # PeonBuilder（AMAI）：想开下一座矿就按下一座矿的数量先造（和人族表同一份 tables.amai_peons）
    expanding = any(x.kind == "expand" and x.qty > halls for x in w)
    all_peons = min(profile["peons_max"], amai_peons(world, "orc", halls, expanding))
    w.append(Want("unit", "opeo", min(all_peons, 12), 85, note="开局苦工"))
    w.append(Want("unit", "opeo", all_peons, 84, note="补满苦工（AMAI PeonBuilder）"))   # 60 -> 84，理由见人族表同一行
    return w

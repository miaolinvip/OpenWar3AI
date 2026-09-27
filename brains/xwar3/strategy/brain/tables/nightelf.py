# -*- coding: utf-8 -*-
"""暗夜建造表（AMAI TFT/Elf/BuildSequence.ai：global_init_strategy 的祭坛/战争古树/月井前置 + NormalElf 三本）。

和人族表的差别：
  * 小精灵造建筑会**并进建筑里消耗掉**（不是站着盖）—— 小精灵要比人族农民多备 2 个；
  * 人口建筑是月井 emow（10 人口）；小精灵采金采木都不用来回跑；
  * 开矿要先在矿边种一棵生命之树再缠绕金矿（树由这张表的 expand 要，缠绕由 runtime.job_entangle 做）。
没搬的：守护古树前压、奇美拉、雇佣兵、物品、DynamicBuildUnit。
"""
from __future__ import annotations

from .. import gamedata as G
from ..build import Want
from . import lumber_crew, tier_up_priority

PROFILE = dict(
    name="NormalElf",
    farm_left=6,
    peons_per_mine=5,
    peons_lumber=5,
    peons_max=18,
    tier2_army=4,               # 兵够这么多就先升二本（tables.tier_up_priority）
    min_attack_strength=4,
    heroes=("Edem", "Ekee", "Emoo"),
)

MOON = ("earc", "esen", "ehip")          # 月之力量 / 月之护甲
WILD = ("edry", "edoc", "emtg", "edot", "efdr", "echm")   # 野性力量 / 强化外皮


def _n(world, *codes) -> int:
    return sum(world.counts.get(c, 0) for c in codes)


def wants(world, profile=PROFILE) -> list[Want]:
    w: list[Want] = []
    tier = world.tier
    heroes = profile["heroes"]
    production = _n(world, "eaom", "eaoe", "eaow", "edos")
    wisps = _n(world, "ewsp")

    w.append(Want("building", "eate", 1, 96, "base", "长者祭坛（英雄先出）"))
    w.append(Want("hero", heroes[0], 1, 95, note="首英雄"))
    w.append(Want("building", "eaom", 1, 90, "base", "战争古树"))

    left = profile["farm_left"] + 2 * production
    if world.food_cap < world.farm_cap and world.food_free <= left:      # 80 人口软上限，见 human.py 同一行
        done = sum(1 for b in world.my_buildings if b.type == "emow" and b.hp_max > 0 and b.hp >= 0.97 * b.hp_max)
        need = max(1, -(-(left - world.food_free + 1) // 10))
        w.append(Want("building", "emow", done + min(need, 3), 92, "base", "人口快满（月井）"))

    lumber = lumber_crew(world, profile, half_floor=2)
    peons = min(profile["peons_max"], profile["peons_per_mine"] + lumber + 2)   # +2：造建筑会吃掉小精灵
    w.append(Want("unit", "ewsp", min(peons, 10), 86, note="开局小精灵"))
    w.append(Want("unit", "ewsp", peons, 60, note="补满小精灵"))
    w.append(Want("building", "edob", 1, 70, "base", "猎手大厅"))

    if tier == 1:
        # NormalElf tier 1：弓箭手 6@60、女猎手、升二本
        w.append(Want("unit", "earc", 3, 66, note="弓箭手"))
        w.append(Want("unit", "earc", 6, 60))
        w.append(Want("unit", "esen", 2, 52, note="女猎手"))
        if wisps >= min(peons, 10):
            w.append(Want("upgrade_hall", "etoa", 1, tier_up_priority(len(world.my_army), profile, 45), note="升二本"))
        w.append(Want("upgrade", "Reib", 1, 40, note="强化弓"))
    elif tier == 2:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 70, note="二英雄"))
        w.append(Want("building", "eaoe", 1, 55, "base", "知识古树"))
        w.append(Want("unit", "edry", 3, 52, note="树妖"))
        w.append(Want("unit", "earc", 6, 50))
        w.append(Want("unit", "esen", 4, 50))
        w.append(Want("unit", "ebal", 2, 42, note="投刃车"))
        w.append(Want("upgrade", "Resi", 1, 45, note="驱散魔法"))
        if wisps >= min(peons, 10):
            w.append(Want("upgrade_hall", "etoe", 1, 40, note="升三本"))
    else:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 64))
        w.append(Want("hero", heroes[2], 1, 60))
        w.append(Want("building", "eaoe", 1, 60, "base"))
        w.append(Want("unit", "emtg", 3, 60, note="山岭巨人"))
        w.append(Want("unit", "edry", 4, 58))
        w.append(Want("unit", "earc", 6, 54))
        w.append(Want("unit", "esen", 7, 50))
        w.append(Want("upgrade", "Redc", 2, 40, note="利爪德鲁伊训练"))

    # 开矿：矿边种一棵生命之树，建成后运行时 job_entangle 去缠绕金矿（AMAI ANCIENT_EXPANSION）
    halls = _n(world, "etol", "etoa", "etoe")
    fighters = len(world.my_army)
    if tier >= 2:
        w.append(Want("expand", "etol", 2, 53, "mine", "第二座矿（种生命之树）"))
    if halls < 2 and (fighters >= profile.get("expand_army", 4) + 2 or world.t >= 480):
        w.append(Want("expand", "etol", 2, 62, "mine", "第二座矿插到出兵前面"))
    if world.gold >= profile.get("rich_gold", 1500):
        w.append(Want("expand", "etol", halls + 1, 58, "mine", "钱多了开矿"))
        w.append(Want("building", "eaom", 2, 44, "base", "钱多了加战争古树"))

    moon = _n(world, *MOON)
    wild = _n(world, *WILD)
    if moon >= 5:
        w.append(Want("upgrade", "Resm", min(3, tier), 35, note="月之力量"))
        w.append(Want("upgrade", "Rema", min(3, tier), 35, note="月之护甲"))
    if wild >= 5:
        w.append(Want("upgrade", "Resw", min(3, tier), 35, note="野性力量"))
        w.append(Want("upgrade", "Rerh", min(3, tier), 35, note="强化外皮"))
    return w

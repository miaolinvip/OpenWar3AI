# -*- coding: utf-8 -*-
"""亡灵建造表（AMAI TFT/Undead/BuildSequence.ai：global_init_strategy 的祭坛/地穴/墓地前置 + global_build_sequence 的食尸鬼伐木
+ NormalUndead 三本）。

和人族表的差别：
  * 侍僧只采金（每座闹鬼金矿 5 个），**伐木是食尸鬼**（AMAI race_min_ghouls 3 / race_max_ghouls 5；ghoul_prio 在木头 < 500 时最高）；
  * 人口建筑是冢 uzig（10 人口）；建筑召唤出来自己长，不用工人守着；
  * 开矿 = 在金矿上直接盖闹鬼金矿 ugol。
没搬的：冰塔/蜘蛛塔（BuildAtMine uZIGGURAT_FROST 按 GetRushFactor）、牺牲深渊/毁灭者、雇佣兵、物品、DynamicBuildUnit。
"""
from __future__ import annotations

from .. import gamedata as G
from ..build import Want
from . import lumber_crew, tier_up_priority

PROFILE = dict(
    name="NormalUndead",
    farm_left=6,
    peons_per_mine=5,
    peons_lumber=4,            # 这里是「伐木食尸鬼」数（和人族表共用配置键，控制台里一个旋钮）
    peons_max=15,
    tier2_army=4,               # 兵（不算伐木食尸鬼）够这么多就先升二本（tables.tier_up_priority）
    min_attack_strength=4,
    heroes=("Udea", "Ulic", "Udre"),
)

MELEE = ("ugho", "uabo")
RANGED = ("ucry", "ugar", "ufro")


def _n(world, *codes) -> int:
    return sum(world.counts.get(c, 0) for c in codes)


def wants(world, profile=PROFILE) -> list[Want]:
    w: list[Want] = []
    tier = world.tier
    heroes = profile["heroes"]
    mines = _n(world, "ugol")
    production = _n(world, "usep", "utod", "uslh", "ubon")

    w.append(Want("building", "uaod", 1, 96, "base", "亡灵祭坛（英雄先出）"))
    w.append(Want("hero", heroes[0], 1, 95, note="首英雄"))
    w.append(Want("building", "usep", 1, 90, "base", "地穴"))

    # 冢：剩余人口 <= farm_left + 2×出兵建筑 就补，缺口按每座 10 人口算（和人族表同一个「建成的 + 缺几座」写法）
    left = profile["farm_left"] + 2 * production
    if world.food_cap < world.farm_cap and world.food_free <= left:      # 80 人口软上限，见 human.py 同一行
        done = sum(1 for b in world.my_buildings if b.type == "uzig" and b.hp_max > 0 and b.hp >= 0.97 * b.hp_max)
        need = max(1, -(-(left - world.food_free + 1) // 10))
        w.append(Want("building", "uzig", done + min(need, 3), 92, "base", "人口快满"))

    acolytes = min(profile["peons_max"], profile["peons_per_mine"] * max(1, mines))
    w.append(Want("unit", "uaco", min(acolytes, 5), 86, note="开局侍僧"))
    w.append(Want("unit", "uaco", acolytes, 60, note="补满侍僧"))
    lumber_ghouls = lumber_crew(world, profile, half_floor=1)
    if world.lumber >= 2 * profile.get("lumber_high", 800):
        lumber_ghouls = 1   # ★ 09-18 实机木头 2870、金子 280：木头远多于金子时只留 1 个伐木
    w.append(Want("unit", "ugho", lumber_ghouls, 88, note="伐木食尸鬼（AMAI ghoul_prio）"))
    w.append(Want("building", "ugrv", 1, 70, "base", "墓地"))

    if tier == 1:
        # NormalUndead tier 1：食尸鬼 3@90 + 4@55、首英雄、升二本、吃尸体
        w.append(Want("unit", "ugho", lumber_ghouls + 3, 64, note="作战食尸鬼"))
        w.append(Want("unit", "ugho", lumber_ghouls + 6, 55))
        w.append(Want("unit", "ucry", 2, 52, note="地穴恶魔"))
        # ★ 09-18 实机 541 秒侍僧只剩 3 个：亡者大厅升级那 140 秒出不了侍僧（引擎裁决 221），侍僧没补满就不升
        if _n(world, "uaco") >= min(acolytes, 5):
            w.append(Want("upgrade_hall", "unp1", 1, tier_up_priority(len(world.my_army) - min(_n(world, "ugho"), lumber_ghouls),
                                                                      profile, 50), note="升二本"))
        w.append(Want("upgrade", "Ruac", 1, 45, note="吃尸体"))
        w.append(Want("expand", "ugol", 2, 53, "mine", "第二座矿"))
    elif tier == 2:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 60, note="二英雄"))
        w.append(Want("unit", "ugho", lumber_ghouls + 6, 45))
        w.append(Want("unit", "ucry", 4, 52))
        w.append(Want("building", "utod", 1, 50, "base", "诅咒神庙"))
        w.append(Want("unit", "unec", 3, 50, note="亡灵巫师"))
        w.append(Want("building", "uslh", 1, 46, "base", "屠宰场"))
        w.append(Want("unit", "uabo", 2, 48, note="憎恶"))
        w.append(Want("upgrade", "Ruwb", 1, 56, note="蛛网"))
        w.append(Want("upgrade", "Rune", 1, 54, note="巫师进阶"))
        if _n(world, "uaco") >= min(acolytes, 5):
            w.append(Want("upgrade_hall", "unp2", 1, 45, note="升三本"))
        w.append(Want("expand", "ugol", 2, 53, "mine"))
    else:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 60))
        w.append(Want("hero", heroes[2], 1, 40))
        w.append(Want("building", "ubon", 1, 50, "base", "骨场"))
        w.append(Want("unit", "ufro", 2, 50, note="冰霜巨龙"))
        w.append(Want("unit", "uabo", 3, 48))
        w.append(Want("unit", "ucry", 5, 50))
        w.append(Want("unit", "unec", 4, 48))
        w.append(Want("unit", "ugho", lumber_ghouls + 8, 40))
        w.append(Want("upgrade", "Rune", 2, 54))
        w.append(Want("upgrade", "Rufb", 1, 50, note="冰冻吐息"))
        w.append(Want("expand", "ugol", 3, 45, "mine", "第三座矿"))

    fighters = len(world.my_army) - min(_n(world, "ugho"), lumber_ghouls)
    if mines < 2 and (fighters >= profile.get("expand_army", 4) or world.t >= 360):
        w.append(Want("expand", "ugol", 2, 66, "mine", "第二座矿插到出兵前面"))
    if world.gold >= profile.get("rich_gold", 1500):
        w.append(Want("expand", "ugol", mines + 1, 58, "mine", "钱多了开矿"))
        w.append(Want("building", "usep", 2, 44, "base", "钱多了加地穴"))

    melee = _n(world, *MELEE)
    ranged = _n(world, *RANGED)
    if melee >= 5:
        w.append(Want("upgrade", "Rume", min(3, tier), 35, note="邪恶力量"))
        w.append(Want("upgrade", "Ruar", min(3, tier), 35, note="邪恶护甲"))
    if ranged >= 4:
        w.append(Want("upgrade", "Rucr", min(3, tier), 35, note="生物攻击"))
        w.append(Want("upgrade", "Rura", min(3, tier), 35, note="生物甲壳"))
    if tier >= 3 and _n(world, "ugho") >= 6:
        w.append(Want("upgrade", "Rugf", 1, 40, note="食尸鬼狂暴"))
    return w

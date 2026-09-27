# -*- coding: utf-8 -*-
"""人族建造表（AMAI TFT/Human/BuildSequence.ai：global_init_strategy / global_build_sequence / NormalHuman 三本 + FarmBuilder / PeonBuilder / ExpansionBuilder）。

AMAI 原表里还有反冲塔、雇佣兵、龙、按对方兵种动态反制（DynamicBuildUnit）—— 第一版先搬主干，没搬的在 SKIPPED 里写明。
前置建筑 AMAI 靠引擎 CANNOT_BUILD 后自动补（RefreshAllNeeded）；这里直接把前置写进表，优先级略高于依赖它的兵。
"""
from __future__ import annotations

from .. import gamedata as G
from ..build import Want
from . import amai_peons, tier_up_priority

PROFILE = dict(
    name="Normal",
    farm_left=6,                # Profiles.txt「Build Farm at left food」（Hunter 6）
    peons_per_mine=5,
    # ★ 09-18 复盘（s10 343 局中位）：伐木 8 人时木头 5 分钟 490、7.5 分钟 700、10 分钟 900 堆着不用，金子始终约 150 ——
    #   一座矿只养得了 5 个采金，多出来的农民全在砍用不上的木头。伐木 8 -> 5，库存到 400（原来 800）就再减半。
    peons_lumber=5,
    lumber_high=400,
    peons_max=28,
    tier2_army=4,               # 有这么多个兵（不算英雄/工人）就把升二本排到出兵前面（见 tier_up_priority；二本中位原来在第 58~62 拍）
    basic_foot=8,               # 一本、二本要几个步兵（AMAI 复刻表 8；09-22 前期小兵只求够用，经济优先留给高级单位；
                                #   09-22 晚试过 4，1-405 七分钟被 8 步兵 4 火枪打穿，回到 5，见 wants 里低级兵那段）
                                # ★ 09-23 对 AMAI 人族连输 4 局，改学 AMAI 的打法（开局多出兵、频繁骚扰）-> 回到 AMAI 的 8
    min_attack_strength=4,      # Profiles.txt「Minimum Attack Strength」
    # ★★ 09-21 山丘出场后阵亡过多、血魔法师从未出场，英雄组合里去掉山丘、改出血魔法师。
    #   证据（28 局 lesson.counts，打玩家的阵亡）：
    #       山丘 Hmkg 1.0 次/局   大法师 Hamg 0.5   圣骑士 Hpal 0.2
    #   而山丘是**二英雄**、出场局数比大法师少 —— 按出场折算差得更多。
    #   原因也说得通：山丘是近战（射程 90），站在前排；血法师射程 600，还有烈焰风暴这个群伤。
    #   加点表见 races.py 的 skills["Hblm"]（AMAI 没有血法师那一行，必须自己给）。
    heroes=("Hamg", "Hblm", "Hpal"),
)

SKIPPED = ("反冲塔 GetRushFactor", "雇佣兵/龙", "物品 BuildItem（交给 mod_scavenge）", "飞艇")
# 09-23：前沿箭塔 BuildFront 已补（主基地门口 FRONT_TOWERS 座，一座奥术塔）
# 09-23：DynamicBuildUnit 已补（wants 末尾「补兵填人口」，按档位取权重最大的兵种，没做完整的按对方兵种反制）

MELEE = ("hfoo", "hkni", "hspt", "hdhw", "hgry")
EXP_TOWERS = 2              # 09-22 每座分矿几座塔（都升防御塔，见 wants 开头那段）
FRONT_TOWERS = 2            # 09-23 主基地门口几座塔（一座升奥术塔、其余升防御塔；1-422 AMAI BuildFront）
EXPAND_FORCE_S = 360        # 兵不够也到这么多游戏秒就让第二座矿插到出兵前面（09-23 一度 480，改回：两矿是对 AMAI 一矿的长处）
ARMED = G.TOWERS | {"otrb"}  # 能打人的敌方建筑（和 tactics.ARMED_BUILDINGS 同一张表：兽族地洞苦工钻进去会射人；改一边看另一边）
SIEGE_TOWERS = 2            # 09-23 看得见对面这么多座带攻击建筑就多要迫击炮（1-416/418）
SIEGE_MAX = 4               #   最多这么多台（每台 3 人口）
FILL_PRIO = 40              # 09-23 补兵填人口（AMAI DynamicBuildUnit(40)，1-420）
FILL_PRIO_BEHIND = 58       #   被对面压着时：排到升三本 45 / 二英雄 50 / 车间 46~58 前面
FILL_BEHIND = 1.3           #   对面强度 > 我方这么多倍 = 被压着
FILL_STEP = 2               #   一轮最多多要这么多个（生产队列满了由 runtime.check 挡）
RANGED = ("hrif", "hmtm", "hgyr")


def _n(world, *codes) -> int:
    return sum(world.counts.get(c, 0) for c in codes)


def _fill_unit(world, tier: int) -> str | None:
    """DynamicBuildUnit 这一轮补哪种兵（AMAI 按对面兵种挑反制；这里取它各档里权重最大的那几种）。"""
    if tier <= 1:
        return "hfoo" if _n(world, "hfoo") < 2 * _n(world, "hrif") + 2 else "hrif"
    if tier == 2:
        return "hrif" if _n(world, "hrif") < _n(world, "hfoo") else "hfoo"
    air = sum(1 for f in world.enemy_fighters if f.type in G.AIR_UNITS)
    return "hgry" if air >= 3 else "hkni"


def wants(world, profile=PROFILE) -> list[Want]:
    w: list[Want] = []
    tier = world.tier
    heroes = profile["heroes"]
    halls = _n(world, "htow", "hkee", "hcas")
    production = _n(world, "hbar", "hars", "harm", "hgra")

    # INFI Echo Isles 两局：分矿大厅下令后 2~5 秒就下第一座哨塔。每座非主矿大厅各自
    # 配一座，where 带大厅地址，runtime.have 会按该矿局部计数，主基地的塔不能误满足。
    main = world.main_town()
    expansions = [h for h in world.my_halls if main is None or h.addr != main.hall.addr]
    # ★★ 09-22 分矿至少要两座能攻击的塔。最近 16 局每局分矿 1 座哨塔、从没升级（哨塔本身不能攻击）。
    #   每座分矿 EXP_TOWERS 座，所有哨塔升防御塔（hgtw，要伐木场；升级 70 金 50 木）；落点朝来路（runtime.expansion_tower_sector）。
    for hall in expansions:
        w.append(Want("building", "hwtw", EXP_TOWERS, 67, f"town:{hall.addr}", "分矿两座塔（朝来路）"))
    # ★★ 09-23 对照 1-422：我方主基地从没造过能攻击的塔，AMAI 家门口有两座塔（一座奥术塔抽魔法、一座防御塔）。
    #   AMAI NormalHuman：BuildFront(1, uARCANE_TOWER, 45 / 57 / 55)（一 / 二 / 三本），front_base_distance 1100 朝敌人；
    #   1-422 对面家门口 hatw (3648,2496) + hwtw (3584,2752)。我们这张表原来把「前沿箭塔 BuildFront」列在没抄的里面。
    #   主基地门口（runtime.main_front_sector，朝最近的敌方大厅）FRONT_TOWERS 座哨塔：一座升奥术塔（抽魔法、打召唤物），其余升防御塔。
    if main is not None:
        w.append(Want("building", "hwtw", FRONT_TOWERS, 57 if tier >= 2 else 45, f"town:{main.hall.addr}",
                      "主基地门口的塔（AMAI BuildFront）"))
    scouts = _n(world, "hwtw")
    if scouts and not _n(world, "hatw"):
        w.append(Want("morph", "hatw", 1, 63, note="门口一座升奥术塔（抽魔法、打召唤物）"))
    if scouts and _n(world, "hlum"):
        w.append(Want("morph", "hgtw", _n(world, "hgtw") + scouts, 62, note="哨塔升防御塔（哨塔不能攻击）"))

    # ── 全局（global_init_strategy 的祭坛先于兵营 + 英雄优先）──
    w.append(Want("building", "halt", 1, 96, "base", "祭坛（英雄先出）"))
    w.append(Want("hero", heroes[0], 1, 95, note="首英雄"))
    w.append(Want("building", "hbar", 1, 90, "base"))

    # FarmBuilder：剩余人口 <= farm_left + 2×出兵建筑 就多造一座房子。
    #   ★ 09-17 实机 5 号 170 秒盖了 7 座：人口上限要等房子**盖完**才涨，而在建的已经算进数量，
    #   「数量 + 1」每冒出一个地基就再要一座。改成「建成的 + 1」，在建的/在路上的算作已经在补。
    #   ★ 同一局后来：人口卡 54/54 二十分钟、金子攒到 8881 —— 一次只要一座，而那座地基停工了（续建见 runtime.job_construction）。
    #   缺多少补多少：缺口 = 门槛 - 剩余人口，每座 6 人口；在建的算已经在补（盖完剩余人口涨、缺口就小，要的总数不变）。
    left = profile["farm_left"] + 2 * production
    # ★ 09-22 人口控制在 80 以内：兵只出到 G.FOOD_SOFT_CAP，房子盖到 FOOD_FARM_CAP（留一个英雄的空）就停
    #   09-22 晚：后期放到 100（world.farm_cap，runtime.food_soft_cap）
    if world.food_cap < world.farm_cap and world.food_free <= left:
        done = sum(1 for b in world.my_buildings if b.type == "hhou" and b.hp_max > 0 and b.hp >= 0.97 * b.hp_max)
        need = max(1, -(-(left - world.food_free + 1) // 6))
        w.append(Want("building", "hhou", done + min(need, 3), 92, "base", "人口快满"))

    # PeonBuilder 在函数末尾（要先知道这一轮想不想开矿：想开就按下一座矿的数量先造，见 amai_peons）

    w.append(Want("building", "hlum", 1, 70, "base"))
    w.append(Want("building", "hbla", 1, 56, "base", "火枪手前置"))

    # ★ 09-22 控制前期小兵数量：够用即可，经济更多留给后期的高级单位。
    #   AMAI 复刻表一本、二本各要 8 个步兵，三本就不要了 —— 到 80 人口时步兵占 14~16（1-312/315/316），
    #   正是要被强制攻击腾掉的那批：先花钱造、后面再杀。5 个够打野、守开矿，省下的钱给升本和二三本的兵。
    # ★★ 09-22 晚 前期低级单位造得过多，临近 80 人口时还得强制攻击自己的小兵腾位置；改为攒钱出高级兵。
    #   最近 10 局：步兵 / 火枪手每局都到 5 / 4~5 个（25 人口），1-399 人口到 70 时 5 步兵 5 火枪、一个骑士都没有，
    #   之后高级兵撞上 80 人口、强制攻击自己的小兵 19 次（tactics._cull）。
    #   现在：二本低级兵排到升三本后面（钱先给城堡 / 迫击炮）、只补不加；三本不再造低级兵。
    #   强制攻击腾人口停用（tactics.CULL_ENABLED）—— 到了 80 就攒钱，低级兵打没了换高级兵。
    # ★★ 09-22 晚 1-405 输：一本改成 4 步兵 + 2~3 火枪之后，6.9 游戏分钟对面（人族电脑）8 步兵 4 火枪 + 山丘打进家，
    #   我方 7 个兵、60 秒打光、10.9 分钟认输（前 5 局峰兵中位 17）。要解决的是**后期**快到 80 时满地低级兵要杀掉 ——
    #   那条由「三本不造低级兵 + 人口放到 100 + 不强制攻击」解决；前期兵量回到 5 步兵 + 3~4 火枪，家被打再叫民兵（runtime._home_militia_step）。
    basic = profile.get("basic_foot", 5)
    if tier == 1:
        # NormalHuman tier 1（"THIS IS REPLICA OF BLIZZARDS AI"）
        w.append(Want("unit", "hfoo", basic, 65))
        w.append(Want("unit", "hrif", 3, 55))
        w.append(Want("building", "hvlt", 1, 54, "base"))
        w.append(Want("expand", "htow", 2, 53, "mine", "第二座矿（GetMinesOwned() < 2）"))
        w.append(Want("unit", "hrif", 4, 43))
        w.append(Want("upgrade_hall", "hkee", 1, tier_up_priority(len(world.my_army), profile, 42), note="升二本"))
    elif tier == 2:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("unit", "hfoo", basic, 52))
        w.append(Want("unit", "hrif", 4, 44))
        w.append(Want("building", "hvlt", 1, 57, "base"))
        if any(b.type == "hvlt" and b.hp_max > 0 and b.hp >= .97*b.hp_max for b in world.my_buildings):
            w.append(Want("upgrade", "Rhpm", 1, 61, note="二本背包：地面部队可携带恢复物品"))
        w.append(Want("hero", heroes[1], 1, 50, note="二英雄"))
        w.append(Want("building", "harm", 1, 46, "base", "迫击炮前置"))
        w.append(Want("unit", "hmtm", 2, 45))
        w.append(Want("upgrade_hall", "hcas", 1, 45, note="升三本"))
        w.append(Want("building", "hars", 1, 41, "base", "牧师前置"))
        w.append(Want("unit", "hmpr", 2, 40))
        w.append(Want("upgrade", "Rhlh", 1, 40, note="伐木（UPG_WOOD）"))
        w.append(Want("expand", "htow", 2, 53, "mine"))
    else:
        w.append(Want("hero", heroes[0], 1, 80))
        w.append(Want("hero", heroes[1], 1, 60))
        w.append(Want("building", "hvlt", 1, 70, "base"))
        if any(b.type == "hvlt" and b.hp_max > 0 and b.hp >= .97*b.hp_max for b in world.my_buildings):
            w.append(Want("upgrade", "Rhpm", 1, 61, note="背包：地面部队可携带恢复物品"))
        # ★★ 09-22 晚 1-403：资源充足却不多造兵。后期人口上限直接放到 100，交战时家里继续按战况补相应单位。
        #   后期（world.late_food，人口放到 100）骑士 / 狮鹫多要；看得见的敌方空军 >= 3 多出龙鹰（空中锁镣）、施法单位 >= 3 多出破法者。
        late = world.late_food
        air = sum(1 for f in world.enemy_fighters if f.type in G.AIR_UNITS)
        casters = sum(1 for f in world.enemy_fighters if f.type in G.ENEMY_CASTERS)
        w.append(Want("unit", "hkni", 10 if late else 6, 55))
        w.append(Want("building", "hgra", 1, 45, "base", "狮鹫/龙鹰前置"))
        w.append(Want("unit", "hgry", 10 if late else 7, 40))
        w.append(Want("unit", "hdhw", 4 if air >= 3 else 2, 50 if air >= 3 else 40,
                      note=f"对面空军 {air} 个：多出龙鹰锁它" if air >= 3 else ""))
        w.append(Want("building", "hars", 1, 42, "base"))
        w.append(Want("unit", "hsor", 3, 40))
        w.append(Want("unit", "hmpr", 2, 40))
        w.append(Want("unit", "hspt", 4 if casters >= 3 else 2, 48 if casters >= 3 else 40,
                      note=f"对面施法单位 {casters} 个：多出破法者" if casters >= 3 else ""))
        w.append(Want("hero", heroes[2], 1, 40))
        w.append(Want("building", "harm", 1, 12, "base"))
        w.append(Want("unit", "hmtm", 2, 10))
        w.append(Want("expand", "htow", 3, 45, "mine", "第三座矿"))

    # ★ 09-17 扩张意愿不足。第三局 980 秒才开第二座矿：表里开矿 53 排在步兵 65 / 火枪 55 后面，金子一直先被兵花掉。
    #   有 4 个兵（守得住开矿的工人）或到 6 分钟，第二座矿插到出兵前面；三本有 10 个兵，第三座矿也插到前面。（AMAI 原表没有这条）
    #   ★ 09-21 66 -> 83：AMAI 分矿 expa_prio 200，排在英雄 80 和所有兵前面。它能这么排是因为只在「守卫清掉、
    #   现有矿农民造满」之后才入队 —— 我们这两道在 runtime 的开矿检查里（野怪没清 / 没造满时这条被跳过、不占钱）。
    # ★★ 09-23 1-416 / 1-418：对面 AMAI 兽族一矿龟缩（16 座建筑全在 10 座箭塔 / 地洞射程里），我方没有攻城单位 ——
    #   出击评估里塔按 ×4 算（offense.assess：没有 SIEGE 就 ×4），要 84.7 对我方 64，二十多分钟一直「出击·目标不好」、钱堆到 4000~6000。
    #   看得见对面 >= SIEGE_TOWERS 座带攻击建筑就多要迫击炮（射程 1150 > 箭塔 800、对建筑加成），车间一起提前。三本那张表里它们只排 10~12。
    armed = sum(1 for b in world.enemy_buildings if b.hp > 0 and b.type in ARMED)
    if tier >= 2 and armed >= SIEGE_TOWERS:
        w.append(Want("building", "harm", 1, 58, "base", f"对面 {armed} 座塔 / 地洞：迫击炮前置"))
        w.append(Want("unit", "hmtm", min(SIEGE_MAX, 2 + armed // 4), 56, note=f"对面 {armed} 座塔 / 地洞：攻城"))
    fighters = len(world.my_army)
    # 09-23 一度照 AMAI 把插队推到 8 个兵 / 8 分钟；AMAI 一矿全押兵（15 局没开过二矿）是它的短板，二矿照旧早开（4 个兵 / 6 分钟）
    if halls < 2 and (fighters >= profile.get("expand_army", 4) or world.t >= EXPAND_FORCE_S):
        w.append(Want("expand", "htow", 2, 83, "mine", "第二座矿插到出兵前面"))
    if tier >= 3 and halls < 3 and fighters >= 10:
        w.append(Want("expand", "htow", 3, 64, "mine", "第三座矿插到出兵前面"))

    # 钱花不出去（AMAI 没有这条；09-17 实机金子攒到 8881）：多开一座矿、多一座兵营/狮鹫笼
    if world.gold >= profile.get("rich_gold", 1500):
        w.append(Want("expand", "htow", halls + 1, 58, "mine", "钱多了开矿"))
        w.append(Want("building", "hbar", 2, 44, "base", "钱多了加兵营"))
        if tier >= 3:
            w.append(Want("building", "hgra", 2, 43, "base", "钱多了加狮鹫笼"))

    # 研究（BuildAdvUpgr2：兵越多越该研究；AMAI 是按兵数算概率，这里取确定性门槛）
    melee = _n(world, *MELEE)
    ranged = _n(world, *RANGED)
    if _n(world, "hfoo") >= 4:
        w.append(Want("upgrade", "Rhde", 1, 60, note="防御姿态"))
    if melee >= 5:
        w.append(Want("upgrade", "Rhme", min(3, tier), 35, note="近战攻击"))
        w.append(Want("upgrade", "Rhar", min(3, tier), 35, note="护甲"))
    if ranged >= 5:
        w.append(Want("upgrade", "Rhra", min(3, tier), 35, note="远程攻击"))
        w.append(Want("upgrade", "Rhla", min(3, tier), 35, note="皮甲"))
    # ★★ 09-22 带技能的高级兵要对敌军高伤害单位放技能。女巫的变形术（60 秒把牛头人变羊）
    #   要女巫训练两级（Rhst：每级 +100 最大蓝，220 蓝的变形术要 400；一级要主城、二级要城堡），原来愿望表里没有 ->
    #   女巫一辈子只会自动减速。毫秒层 war3-modules/unitspells.py 按研究等级决定放不放变形术。
    if _n(world, "hsor") >= 2 and tier >= 2:
        w.append(Want("upgrade", "Rhst", min(2, tier - 1), 45, note="女巫训练（二级学会变形术）"))

    # ★★ 09-23 1-420 被 AMAI 人族一波打穿，对比双方出兵找差距。
    #   AMAI 人族表面上和这张表一样（8 步兵 + 3~4 火枪），但表外还有一步 DynamicBuildUnit(40)
    #   （third_party/AMAI/TFT/Human/BuildSequence.ai:180、common.eai:10581）：表里的兵出齐以后，每一轮按「人口上限 - 已用人口」
    #   继续补兵，一直把人口填满。我们没抄这一步（SKIPPED 里写着）—— 表一满，钱就流向塔 / 升本 / 二英雄 / 研究 / 车间：
    #   1-420 前 12 分钟下单 8105 金，兵只占 28%（13 个），对面一矿 17 农民、12 分钟 23 个兵，一波把我们 11 个兵打光。
    #   现在照它补：一本步兵 : 火枪 2 : 1、二本 1 : 1、三本只补骑士（对面空军多补狮鹫；09-22 起三本不再造低级兵）。
    #   被对面压着（对面强度 > 我方 FILL_BEHIND 倍）时补兵提到 FILL_PRIO_BEHIND，排到升三本 / 二英雄 / 车间前面（AMAI 没有这条）。
    fill = _fill_unit(world, tier)
    soft = G.FOOD_SOFT_CAP_LATE if world.late_food else G.FOOD_SOFT_CAP
    room = soft - world.food_used
    if fill and room >= G.food(fill):
        behind = world.foe_strength > FILL_BEHIND * max(1.0, world.own_strength)
        n = _n(world, fill) + min(FILL_STEP, room // max(1, G.food(fill)))
        w.append(Want("unit", fill, n, FILL_PRIO_BEHIND if behind else FILL_PRIO,
                      note=f"补兵填人口（AMAI DynamicBuildUnit）{'·被对面压着先补兵' if behind else ''}"))

    # PeonBuilder（AMAI common.eai:11899）：农民目标按「在采的矿数 + 木头」插值；这一轮想开下一座矿，
    #   就按下一座矿的数量先造（pendingExpansionPeons）。开矿本身要等现有矿造满（runtime 的建造检查里）。
    #   ★ 09-21 起替换「每矿 5 个 + 伐木人数」：那个公式一座矿木 500 时只要 7 个，AMAI 要 13 个。
    expanding = any(x.kind == "expand" and x.qty > halls for x in w)
    peons = min(profile["peons_max"], amai_peons(world, "human", halls, expanding))
    # ★ 09-21 优先级照 AMAI 的量级关系（common.eai:357 farm_prio 400 > peon_prio 280 > expa_prio 200 >
    #   BuildSequence 里的英雄 80 / 兵 ≤65）：农民排在英雄和所有兵前面，只让给人口房子和首英雄。
    #   原来补满是 60、比步兵 65 低 —— 4-030 实机 3:00~8:30 农民一直 12 个（AMAI 目标 17），钱先被步兵花掉。
    w.append(Want("unit", "hpea", min(peons, 12), 85, note="开局农民"))
    w.append(Want("unit", "hpea", peons, 84, note="补满农民（AMAI PeonBuilder）"))
    return w

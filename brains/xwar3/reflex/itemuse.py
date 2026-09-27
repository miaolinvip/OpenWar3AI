# -*- coding: utf-8 -*-
"""itemuse.py —— 物品用法的判定（纯函数）：买什么、什么时候喝药、什么时候放恢复卷轴、什么时候读回城。

09-17 补上商店物品这一块（此前完全没有用过物品），和战术研究一起做。
依据（war3-analytics/replays/LEARNED.md「中后期打法与商店物品」、w3data/AbilityData.slk、ItemData.slk）：
  * 高手（虎牙Yumiko 胜局）买了 39 件 5005 金：传送权杖 12 次、避难权杖救人、医疗卷轴交战中用、象牙塔控图；
  * 我们这边：v287 61 次英雄阵亡死前 3 秒血量中位 67%；决定性一仗全在家门口、常常是部队不在家。
第一版只做**自家神秘藏宝室买得到**的两样 + 开局英雄自带的回城卷轴（动作底座 w3cmd.buy_item / use_item，09-17 演练台验过）：
  phea 生命药水  150 金、要二本  —— 英雄血低或被集火时自救（瞬回 250）
  sreg 恢复卷轴  100 金          —— 600 范围友军 45 秒回 225（AIsl），交战里太慢，打完/撤下来回血用
  stwp 回城卷轴  开局第一个英雄送一张 —— 主基地挨打、英雄离家远时传回去（引导 5 秒，1100 范围一起走，目标是自家建筑）
医疗卷轴（瞬回 150、冷却 25）和小无敌药水只在地精商店（地图中间），第二版再说。

⚠ 买东西不经过策略层的收费口（它在 PowerShell 进程里），所以只在金子 >= BUY_GOLD_FLOOR 时买，每个英雄只补到 SHOP_WANT 为止 ——
  「预留被穿透十三次」不能在这里再来一次。
"""
from __future__ import annotations

import math

VAULTS = {"hvlt"}                     # 自家商店（第一版只有人族神秘藏宝室）
# ★★ 09-22 晚 恢复卷轴随身常备两张，商店有货就买，交战后大批残血单位随时能回血。
#   原来平时每个英雄 1 张、三本以后才 2 张（LATE_SREG），而且队里别的英雄有一张就不买（mod_scavenge「团队已有恢复卷轴」）。
#   现在每个英雄任何时候都备 SREG_CARRY 张；缺的时候让大脑留出一张的钱（item_reserve），别被出兵花光。
SREG_CARRY = 2
SHOP_WANT = (("phea", 1), ("sreg", SREG_CARRY))  # 每个英雄背包里保持的数量，按优先级
# 09-23：shop_want 末尾再备一张回城卷轴（最后买、按常备线 BUY_GOLD_FLOOR）—— AMAI BUY_ITEM 会补 TOWN_PORTAL，
#   打输了整队传回家（retreat_portal_due）；原来只有开局送的那一张
PORTAL_WANT = ("stwp", 1)
PRICE = {"phea": 150, "sreg": 100, "stwp": 350, "tsct": 40, "pman": 200}
# ★ 09-21 施法英雄蓝不够时直接买魔法药水补。pman 魔法药水瞬回 150 蓝、200 金（ItemData.slk goldcost）；
#   卖不卖、要几本查的是 AMAI 自己的数据（third_party/AMAI/TFT/StandardUnits.txt：MANA_POTION pman RACIAL_ITEM racial_shop
#   racial_hall[2] = 种族商店、要二本；HealingItems.txt MANA_POTION tier >= 2 MANAITEM_HERO）。只给靠技能吃饭的英雄备。
MANA_HEROES = {"Hamg", "Hblm"}
MANA_POTION_BELOW = 100.0             # 战斗中蓝低于这个就喝（大法师水元素 125、血法师烈焰风暴 135 都放不出来了）


def shop_want(hero_type: str, late: bool = False) -> tuple:
    """这个英雄背包里该常备什么：大家都备生命药水、恢复卷轴（SREG_CARRY 张）；施法英雄再备一瓶魔法药水。
    late（三本以后）原来决定卷轴备 1 还是 2 张；09-22 晚起任何时候都是 SREG_CARRY，参数留着给老调用方。"""
    base = tuple((c, LATE_SREG if late and c == "sreg" else n) for c, n in SHOP_WANT)
    return base + ((("pman", 1),) if hero_type in MANA_HEROES else ()) + (PORTAL_WANT,)


LATE_SREG = SREG_CARRY
LATE_SHOP_HOME_R = 1800.0             # 三本以后英雄离自家大厅这么近（部队在家整备）：带着部队也可以走去藏宝室补货
BUY_GOLD_FLOOR = 450                  # 金子扣完低于这个不买（给升本/出兵/英雄留钱）
REGEN_BUY_FLOOR = 150                 # ★ 09-21：**救急买恢复卷轴**另算一条更低的线。
#   常备补货（SHOP_WANT）可以等有钱再说，但「已经有三个以上伤兵在等」是急事：
#   那一张卷轴回的血（>= 450）比同样 100 金能造出来的兵多，拖着不买等于白白扔掉已经付过钱的兵。
#   一个兵（尤其高级兵）比一张恢复卷轴贵得多，卷轴不该跟常备补货抢同一条金子线。
#   09-17 700 -> 450：11 局英雄在家附近因「钱不够（<800 才走）」没去买的采样每局 40~208 次，和「离太远」一样多，0 次成交。
#   一件药 100~150 金；下一批看 tTier / tExpand 有没有被拖后。
SHOP_REACH = 250.0                    # 英雄离藏宝室这么近才买（商店把东西给附近的英雄）
#   ★ 09-22 450 -> 250（1-297 整局没见到卷轴的购买和使用）：藏宝室的 Aall（友方建筑）交互距离 Rng1 = 350，
#     450 有一截在范围外。全部实例 cmdlog：捡装备模块（<= 450 就买）卷轴 530 成 / 562 拒（48%），
#     大脑整队恢复（先走到藏宝室 +80 再买）298 / 59（83%）；1-297 那三次被拒时库存满、钱够、背包空、藏宝室已盖好。
#     下单日志现在带距离（mod_scavenge「商店：给 X 买 …」），下一轮按距离核对这个判断。
WALK_GOLD_MIN = REGEN_BUY_FLOOR + 100  # 09-21 英雄为补货走去藏宝室的金子门槛（原来 BUY_GOLD_FLOOR + 100 = 550；卷轴按急救线买，见 shop_wish）
SHOP_WALK_R = 2500.0                  # 09-17：离藏宝室这么近（在家附近）、缺药、钱够、周围没敌人 -> 自己走过去
#   v289 首批三局商店 0 成交：心跳几乎全是「英雄不在藏宝室边上」—— 英雄大部分时间在外面打野，从不专门回藏宝室。
ENEMY_NEAR = 1500.0                   # 这么近有敌方作战单位 = 在打仗，不去逛商店
WALK_ARMY_R = 1200.0                  # 09-17：英雄身边这么近
WALK_ARMY_MAX = 3                     #   有这么多自己的作战单位（不算工人/英雄/建筑）= 正带着部队打野/行军，不专门走回去买药
#   inst1 211637：21:18:59 打完野捡到指环，21:19:01「离藏宝室 2201、缺 sreg -> 走过去」（租约 20 秒），
#   之后 16 秒策略层给这个英雄的 10 条令（去下一个营地的集合点、放技能）全被让出 —— 表现为英雄打完野后原地停顿约 5~10 秒。
WORKER_TYPES = {"hpea", "opeo", "uaco", "ewsp"}

POTION_HP = 0.45                      # 打玩家时英雄血量低于这个比例喝生命药水
POTION_FOCUS_HP = 0.70                # 被 >= 3 个敌人集火时放宽到这个比例
POTION_FOE_R = 1200.0

REGEN_R = 600.0                       # 恢复卷轴范围（AIsl Area1）
REGEN_READY_R = 500.0                 # 集合判据留 100 距离余量，防止移动误差把伤兵漏在范围外
# ★★ 09-21 4 局出现同一问题：一堆残血单位等着恢复卷轴回血，卷轴一直不放。
#   一个兵（尤其高级兵）的价钱远高于一张卷轴，门槛不该定得这么高。
#   门槛按**每金回血**算出来，不是拍的：
#     步兵 135 金 420 血 = 每金 3.1 血；卷轴 100 金 -> 要够本得回 100 × 3.1 ≈ 310 血。
#   所以「总缺血 >= 450」就已经比拿这 100 金去造兵划算（留了约 45% 余量给走位损耗和自然回血）。
#   旧值 6 人 / 1050 血是往「一张卷轴回满 6 个人」上靠，28 局实测被它挡掉
#   「伤兵只有 3/4/5 个」9.0 次/局，而整条流程「放出卷轴」只有 0.1 次/局。
REGEN_MIN_HURT = 3                    # 至少三名明显受伤的有机友军
REGEN_MIN_MISSING = 450.0             # 且总缺血至少这么多（= 卷轴够本线 310 血 + 余量）
REGEN_FOE_R = 900.0                   # 这么近还有敌人就先别放（交战里太慢，打完再放）
# ★ 09-22 晚 后期出门时背包有空格的英雄带上恢复卷轴，残血单位不必回家等治疗，退到附近安全区域就地回血。
#   毫秒层判「伤兵到齐」原来拿全图的兵算：家里有一个伤兵，野外带卷轴的英雄就永远等不到「到齐」。
#   现在只看英雄 REGEN_GATHER_R 内的伤兵（还在路上的照样要等它进 REGEN_READY_R）；大脑把养伤的兵派到带卷轴的英雄身后（runtime._field_heal_spot）。
REGEN_GATHER_R = 1500.0

TP_FAR = 4500.0                       # 英雄离主基地这么远才值得读回城
TP_HOME_R = 1500.0                    # 主基地这么近的敌方作战单位算「家被打」
TP_HOME_FOES = 5
TP_SELF_R = 900.0                     # 英雄自己身边有敌人就不读（引导 5 秒会被打断）
# ★★ 09-22（1-388）读回城整队传送太仓促：不少单位不在范围内，只传回了几个。改为立即开始引导，
#   同时把英雄身边的战斗单位叫到英雄周围，利用 5 秒引导时间完成集结。
#   现场（战斗帧 20:35:08）：下令那一刻英雄 1100 内 5 个兵、2500 内 7 个；引导的 3 秒里步兵/火枪手被大脑的回防令带着往家走，
#   304 -> 1821、600 -> 1639 …… 传送那一刻 1100 内只剩 2 个水元素，9 个走了 2 个。
#   AbilityData AItp：Cast 5（游戏秒引导）、Area 1100（传送那一刻施法者身边这么远的自己人一起走）、DataA 90（最多 90 个）。
#   所以读卷轴的同一拍，把引导时间内走得到的战斗单位叫到英雄身边（TP_GATHER_STOP 以内），期间锁住别被别的命令带走。
TP_AREA = 1100.0
TP_CHANNEL = 5.0                      # 游戏秒
TP_EDGE_MARGIN = 150.0                # 算「走得到」时离范围边缘留这么多（引导结束时站在 950 以内）
TP_WALK_SHARE = 0.8                   # 引导时间里按移速只算能走这么多（绕路、起步）
TP_GATHER_STOP = 250.0                # 叫到离英雄这么近就停（不往一个点上挤）

MECHANICAL = {"hgyr", "hmtt", "hrtt", "ocat", "umtw", "ebal", "nzep", "hmtw"}   # 恢复卷轴只回有机单位


def rawcode(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def shop_wish(bag, gold, want=SHOP_WANT, floor=BUY_GOLD_FLOOR, price=PRICE, tier=None, recovery=False, hurt=False):
    """背包（6 格物品码或 None，读不到 None）+ 金子 -> 该买的物品码；不买 None。

    背包满了不买；金子扣掉这件之后低于 floor 不买（不是「金子 >= floor」—— 700 金买 150 的药会把 700 花穿）。
    hurt：英雄残血（HURT_POTION_FRAC 下面那段）—— 生命药水排第一、按急救线 REGEN_BUY_FLOOR 买。"""
    if bag is None or gold is None or None not in bag:
        return None
    if recovery:
        want = (("sreg", 1),) + tuple(p for p in want if p[0] != "sreg")
    if hurt:
        want = (("phea", 1),) + tuple(p for p in want if p[0] != "phea")
    for code, n in want:
        if code in ("phea", "pman", "stwp") and tier is not None and tier < 2:
            continue                  # 二本才卖（AMAI StandardUnits.txt：racial_hall[2]；回城卷轴还要开局 440 秒后）
        # ★ 09-21 商店恢复卷轴每次刷新都给血魔法师补上，卷轴便宜，不该卡在常备补货的金子线上。
        #   两小时六个实例「不走·钱不够」2265 次：常备补货要买完剩 450，而卷轴 100 金一张。卷轴走急救那条线（REGEN_BUY_FLOOR）。
        fl = min(floor, REGEN_BUY_FLOOR) if code == "sreg" or (hurt and code == "phea") else floor
        if bag.count(code) < n and gold - price.get(code, 0) >= fl:
            return code
    return None


# ★★ 09-22 英雄残血时留钱买生命药水补血。1 号实例 09-22 一天：该喝血瓶时背包里没有 183 次、真喝上 18 次；
#   捡装备模块下单血瓶 61 次。常备补货要买完还剩 BUY_GOLD_FLOOR（450）—— 血瓶 150，手里要有 600 才买，钱一直被造兵花掉。
#   残血英雄缺血瓶按急救线买（和救急恢复卷轴同一条 REGEN_BUY_FLOOR），离藏宝室 SHOP_HURT_WALK_R 内、带着部队也走回去，
#   同时让大脑留出 HURT_RESERVE 金（item_reserve.json，大脑 runtime._item_reserve）—— 攒着买药，别在这之前全花在兵上。
HURT_POTION_FRAC = 0.5
SHOP_HURT_WALK_R = 4000.0
HURT_RESERVE = PRICE["phea"]
RESERVE_TTL = 10.0                    # 大脑只认这么久以内写的留钱（捡装备模块停了 / 英雄买上了，留钱自己过期）


def hurt_needs_potion(hero, bag, tier) -> bool:
    """残血英雄、背包里没有生命药水、已经二本（藏宝室二本才卖）。背包读不到（None）不算缺 —— 读不到就别为它留钱。"""
    return (hero.hp < HURT_POTION_FRAC * max(1.0, hero.hp_max) and bag is not None and "phea" not in bag
            and (tier is None or tier >= 2))


SREG_RESERVE = PRICE["sreg"]


def scroll_short(bag) -> bool:
    """恢复卷轴不到 SREG_CARRY 张、背包还有空格（满了买不进来，留钱也没用）。读不到（None）不算缺。"""
    return bag is not None and None in bag and bag.count("sreg") < SREG_CARRY


def item_reserve(heroes, bags, vaults, tier):
    """大脑下单前要替物品留多少金（mod_scavenge.write_item_reserve -> war3-brain runtime._item_reserve）。
    -> (金, 理由, 类别)。残血英雄缺血瓶（HURT_RESERVE）优先；其次藏宝室 SHOP_WALK_R 内有英雄恢复卷轴不到 SREG_CARRY 张（SREG_RESERVE）。
    bags：英雄地址 -> 背包（没读 / 读不到 = 不在里面或 None，都不留钱）。"""
    if not vaults:
        return 0, "", ""
    for h in heroes:
        if hurt_needs_potion(h, bags.get(h.addr), tier):
            return HURT_RESERVE, f"{h.type} {int(h.hp)}/{int(h.hp_max)} 缺生命药水", "残血缺血瓶"
    for h in heroes:
        bag = bags.get(h.addr)
        if scroll_short(bag) and min(dist(v, h) for v in vaults) <= SHOP_WALK_R:
            return SREG_RESERVE, f"{h.type} 恢复卷轴 {bag.count('sreg')}/{SREG_CARRY} 张", "卷轴不到两张"
    return 0, "", ""


def bag_full_missing(bag, want) -> str | None:
    """背包满了、又缺 want 里的东西 -> 缺的那件（心跳「背包满了·缺 X」；扔一件腾地方之前先量它有多常见）。"""
    if bag is None or None in bag:
        return None
    return next((code for code, n in want if bag.count(code) < n), None)


def load_food(path: str | None = None) -> dict:
    """units.json 的 food（占人口）表：> 0 = 能走的单位，0 = 建筑。读不到返回 {}（army_with 就数 0，走去买药照旧）。"""
    import json
    import os
    p = path or os.environ.get("WAR3_UNITS_JSON", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "game", "units.json"))
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return {k: float(v.get("food") or 0) for k, v in d.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return {}


def army_with(hero, mine, food: dict, r: float = WALK_ARMY_R) -> int:
    """英雄身边 r 内自己的作战单位数：占人口、不是工人、不是英雄（大写开头）、活着。"""
    return sum(1 for u in mine
               if u.hp > 0 and u is not hero and u.type not in WORKER_TYPES and not u.type[:1].isupper()
               and food.get(u.type, 0.0) > 0.0 and dist(u, hero) <= r)


def fighters_near(unit, foes, r, is_fighter=None):
    return [f for f in foes if f.hp > 0 and (is_fighter is None or is_fighter(f)) and dist(f, unit) <= r]


def potion_due(hero, foes_near: int, focused: int = 0) -> bool:
    """英雄该不该喝生命药水（不看背包，背包由调用方读）：身边有敌人，且血量低于 POTION_HP，或被集火时低于 POTION_FOCUS_HP。"""
    if hero.hp_max <= 0 or foes_near <= 0:
        return False
    frac = hero.hp / hero.hp_max
    return frac < POTION_HP or (focused >= 3 and frac < POTION_FOCUS_HP)


def mana_potion_due(hero, mana, foes_near: int) -> bool:
    """施法英雄该不该喝魔法药水：身边有敌人（在打仗）且蓝低于 MANA_POTION_BELOW。读不到蓝（None）不喝。"""
    return hero.type in MANA_HEROES and foes_near > 0 and mana is not None and mana < MANA_POTION_BELOW


def regen_worth(holder, allies, foes) -> tuple[bool, int, float]:
    """恢复卷轴值不值得放：范围内受伤的有机友军数、合计缺血，且附近没有敌人。-> (放不放, 受伤人数, 缺血)。"""
    hurt, missing = 0, 0.0
    for a in allies:
        if a.hp <= 0 or a.hp_max <= 0 or a.type in MECHANICAL or dist(a, holder) > REGEN_R:
            continue
        miss = a.hp_max - a.hp
        if miss >= 60:
            hurt += 1
            missing += miss
    close = any(f.hp > 0 and dist(f, holder) <= REGEN_FOE_R for f in foes)
    return (hurt >= REGEN_MIN_HURT and missing >= REGEN_MIN_MISSING and not close), hurt, missing


def regen_formation_ready(holder, allies, foes) -> bool:
    """毫秒层只在策略层要求的恢复队形已经形成时才允许消费卷轴。"""
    injured = [a for a in allies if a.hp > 0 and a.hp_max > 0 and a.type not in MECHANICAL
               and a.hp < .7*a.hp_max and a.hp_max-a.hp >= 100]
    if len(injured) < REGEN_MIN_HURT or sum(a.hp_max-a.hp for a in injured) < REGEN_MIN_MISSING:
        return False
    if any(dist(a, holder) > REGEN_READY_R for a in injured):
        return False
    # ★ 09-22（1-301 9:55）不再要求满血单位退出作用圈：卷轴 600 内所有单位各回 225、没有人数上限，
    #   满血的站在里面不挡任何人；这条让大脑那边伤兵全到了也等到集合超时放弃（和 war3-brain/brain/recovery.py formation 同一条规则，两边一起改）。
    return regen_worth(holder, injured, foes)[0]


# ★ 09-23 在自家主基地或守自家分矿时不读回城：能打就打，打不过就拉扯着打。
#   英雄在自家任何一座大厅 TP_OWN_BASE_R 内（守主基地 / 守分矿）两种回城都不读 —— 回城只用来从野外赶回家、从野外打输了撤。
TP_OWN_BASE_R = 2000.0


def at_own_base(hero, own_halls) -> bool:
    return any(math.hypot(hero.x - x, hero.y - y) <= TP_OWN_BASE_R for x, y in (own_halls or ()))


def town_portal_due(hero, hall, home_foes: int, self_foes: int, own_halls=()) -> bool:
    """回城卷轴：主基地挨打（大厅 TP_HOME_R 内敌方作战单位 >= TP_HOME_FOES）、英雄离大厅 > TP_FAR、英雄自己身边没敌人、
    英雄不在自家别的大厅边上（守分矿的不传走）。own_halls = 自家大厅坐标。"""
    if hall is None or at_own_base(hero, own_halls):
        return False
    return home_foes >= TP_HOME_FOES and dist(hero, hall) > TP_FAR and self_foes == 0


# ★★ 09-23 对局观察（1-420，AMAI 人族）：AMAI 打不过时会直接读回城，全队传回家。
#   AMAI Jobs/MICRO_HERO.eai：进攻中判定打不过、部队开始撤（isfleeing）、英雄离家 > teleport_low_threat_distance（6000）
#   -> 英雄直接读回城，1100 内的部队一起传走（TeleportHome）。我们原来只在「家被打」时读回城，野外打输了就一路走回家、被追着打光
#   （1-420 12 分钟那一仗 13 个进场、死了 12 个）。毫秒层判「这片战场放弃、整队撤退」之后接这条。
TP_RETREAT_FAR = 6000.0               # 英雄离家这么远（AMAI teleport_low_threat_distance）
TP_RETREAT_R = 1500.0                 # 英雄离放弃的那片战场这么近才算「这支部队的英雄」
TP_RETREAT_ARMY = 3                   # 英雄 TP_AREA 内至少这么多自己人（AMAI teleport_army_min_strength：部队值得救才传）
TP_RETREAT_WINDOW = 6.0               # 叫撤之后这么多墙钟秒内有效


def retreat_portal_due(hero, fight_xy, home_xy, army_near: int, own_halls=()) -> bool:
    """整队撤退的那支部队离家远、英雄在场、身边还有人：读回城整队传走（引导时不看身边有没有敌人 —— 挨打不会打断回城）。
    英雄在自家任何一座大厅边上（守分矿）不读（TP_OWN_BASE_R）。"""
    if fight_xy is None or home_xy is None or at_own_base(hero, own_halls):
        return False
    return (math.hypot(hero.x - fight_xy[0], hero.y - fight_xy[1]) <= TP_RETREAT_R
            and math.hypot(hero.x - home_xy[0], hero.y - home_xy[1]) > TP_RETREAT_FAR
            and army_near >= TP_RETREAT_ARMY)


def tp_gather(hero, units, speed_of):
    """读回城的同时叫人靠拢（判据见 TP_AREA 上面那段）：units 里引导时间内走得到的 -> ([(单位, tx, ty)], 够不着的个数)。
    落点在「单位 -> 英雄」连线上、离英雄 TP_GATHER_STOP（本来就更近的原地站住 —— 也要下令，别让它被原来的命令带走）。
    speed_of(类型) -> 移速（游戏单位 / 游戏秒）。英雄自己不算。"""
    out, far = [], 0
    for u in units:
        if u.addr == hero.addr or u.hp <= 0:
            continue
        d = dist(u, hero)
        walk = (speed_of(u.type) or 270.0) * TP_CHANNEL * TP_WALK_SHARE
        if d - (TP_AREA - TP_EDGE_MARGIN) > walk:
            far += 1
            continue
        if d <= TP_GATHER_STOP:
            out.append((u, u.x, u.y))
            continue
        k = TP_GATHER_STOP / d
        out.append((u, hero.x + (u.x - hero.x) * k, hero.y + (u.y - hero.y) * k))
    return out, far

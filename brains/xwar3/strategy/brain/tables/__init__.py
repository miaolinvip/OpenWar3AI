# -*- coding: utf-8 -*-
"""表驱动（AMAI TFT/<Race>/BuildSequence.ai + Strategy.txt + Profiles.txt）：每个种族一个模块，给出 wants(world) 和人格参数。"""

TIER_UP_PRIO = 67        # 兵够了之后升二本的优先级：高于出兵（65）；09-21 起低于「第二座矿插到出兵前面」（83，AMAI expa_prio 200 > 升本 42~45）


WOOD_SHORT_GOLD = 600    # 金子至少这么多
WOOD_SHORT_RATIO = 4     # 且金子 >= 木头 × 这个 = 缺木头
WOOD_SHORT_EXTRA = 5     # 缺木头时伐木多派这么多人


def amai_peons(world, race: str, mines: int, expanding: bool) -> int:
    """该有几个农民 —— 照 AMAI PeonBuilder（common.eai:11899）：

        target = max(GetPeonTargetForMines(在采的矿数, 木头), pendingExpansionPeons)

    pendingExpansionPeons：准备开下一座矿时，**先按下一座矿的数量把农民造出来**
    （QueueExpansionPeonsIfNeeded，common.eai:9931），大厅一落地就有人去挖。
    ★ 09-21 运营层从大模型切回非 LLM 模式，农民数参考 AMAI 的算法。手写表原来是
      「每矿 5 个 + 伐木人数」，比 AMAI 少三到五成（见 amai_strategy/data.peon_target）。"""
    n = peon_need(world, race, max(1, mines))
    if expanding:
        n = max(n, peon_need(world, race, max(1, mines) + 1))
    return n


# ★ 09-21 伐木农民过多，每局结束时木头堆到一两千以上。资源要平衡，溢出说明运营策略有问题：
#   按实际需求算该派多少农民采集。
#   近 30 局（按 2 游戏分钟分段的中位）：金子全程 100~210（花光了，是瓶颈），木头 4 分钟 585 -> 10 分钟 1143
#   -> 20 分钟 1882 -> 30 分钟 4841；30 局里 28 局结束时木头 >= 1000。农民 17~21 个，两座矿只放得下 10 个采金，
#   其余全去砍用不掉的木头（AMAI 的「其余都去伐木」，它的农民目标到木头 2000 才降到 14）。
#   农民目标再按「每座在采的矿 GOLD_PER_MINE 个 + 按木头库存要的伐木人数 + 1 个建造工」封顶。
WOOD_CREW_STEPS = ((200, 5), (400, 4), (700, 3))   # 木头 < 200 要 5 个伐木、< 400 要 4 个、< 700 要 3 个
WOOD_CREW_MIN = 2                                  # 木头 >= 700：只留 2 个（造房子 / 升级那点木头够了）
PEON_BUILDERS = 1


def wood_crew_need(wood: float) -> int:
    for lim, n in WOOD_CREW_STEPS:
        if wood < lim:
            return n
    return WOOD_CREW_MIN


def peon_need(world, race: str, mines: int) -> int:
    """mines 座在采的矿该有几个农民：AMAI GetPeonTargetForMines，再按「采金满员 + 按木头库存要的伐木 + 建造工」封顶。
    「该造几个」（amai_peons）和「造满了才开下一座」（amai_saturated）都用这一个 —— 两处各算一份就会一个松一个紧。"""
    from ..amai_strategy import data as D
    from ..harvest import GOLD_PER_MINE
    amai_n = D.peon_target(D.AmaiData(race), max(1, mines), world.lumber)
    return min(amai_n, GOLD_PER_MINE * max(1, mines) + wood_crew_need(world.lumber) + PEON_BUILDERS)


def amai_saturated(world, race: str, mines: int, peons_done: int) -> bool:
    """QueueExpansionPeonsIfNeeded 的闸：现有矿的农民**造满了**才开下一座（common.eai:9931）。
    没造满就开，新矿落地没人挖、老矿也缺人 —— 钱花在大厅上却没有收入。"""
    if mines <= 0:
        return True
    return peons_done >= peon_need(world, race, mines)


def wood_short(world) -> bool:
    """金多木少 = 缺木头。建造表的伐木人数（lumber_crew）和 runtime 的采金/伐木调人（_rebalance）
    **共用这一个判据** —— 两处各写一份，改一处等于没改。"""
    return world.gold >= WOOD_SHORT_GOLD and world.gold >= WOOD_SHORT_RATIO * max(world.lumber, 1)


def lumber_crew(world, profile: dict, half_floor: int = 0) -> int:
    """该派几个人伐木 —— 四族建造表（农民总数）和 runtime.job_lumber（真正派人）共用这一份（原来五处各写一份）。
    木头 >= lumber_high 减半；★ 09-19 第 39 局（5 号 Echo Isles）：伐木 5 人（09-18 按失落神庙木头堆着用不掉调低的），
    升到三本后木头整局 0~150、金子堆到 6000，攒钱的全是要木头的（狮鹫笼/狮鹫/城堡/车间/圣地）—— 金多木少时多派人砍树。"""
    n = int(profile["peons_lumber"])
    if world.lumber >= profile.get("lumber_high", 800):
        return max(half_floor, n // 2)
    if wood_short(world):
        return n + WOOD_SHORT_EXTRA
    return n


def tier_up_priority(fighters: int, profile: dict, base: int) -> int:
    """升二本排在哪：兵（不算英雄/工人）>= profile["tier2_army"] 就提到 TIER_UP_PRIO，否则用表里原来的 base。
    ★ 09-18 复盘（s10 343 局，人族 vs 疯狂电脑）：二本中位第 58~62 拍（10 游戏分钟）、三本 14 分钟 —— 原表升二本优先级 42
      排在所有兵后面，有钱先出步兵；第二英雄、二本兵都跟着晚，电脑 7.5 分钟就 2 个英雄。四族共用这一条（tier2_army 可在控制台/调参里改）。"""
    need = profile.get("tier2_army")
    if need is None:
        return base
    return max(base, TIER_UP_PRIO) if fighters >= int(need) else base

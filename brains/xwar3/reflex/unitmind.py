# -*- coding: utf-8 -*-
"""每个单位一份「处境」：谁在打它、预计还能撑几秒、现在该打还是该退 —— 毫秒层（mod_combat）每 100ms 更新一次。

★ 09-21 残血单位仍常常不撤、白白阵亡。为此给每个单位配一套实时处境分析，
  让它按自己当下的情况即时应对，同时服从团队协作规则。

18:00 之后六个实例 1079 个我方单位战死，按死前那段残血时间分（战斗帧 + cmdlog + targets 采样）：
  被秒        388（36%）从 35% 以上到死不到 1.5 秒 —— 按掉血外推来不及。但其中 79% 死前读得到 >=3 个敌人
                        同时在打它，中位提前 1.8 墙钟秒、那一刻血还有 65%。原来只有英雄读「谁在打它」。
  拉了没跑掉  378（35%）撤退令下了还是死 —— >=3 人打它时血中位 47%，而兵要掉到 25% 才进危险名单
  拉了又派回 262（24%）残血窗口中位 61 秒：撤退是 8 秒租约，到期（或跑远了提前放）后
                        大脑 / 补攻击令把 10% 血的人又派回去（死时血中位 10%）
  没人管       51（5%） 家门口大仗不拉残血
缺的不是「再多一条撤退规则」，而是三样东西：
  1. 每个单位知道**有几个人在打它**：FocusBoard 按敌方单位读任务目标，全军共用一张表
  2. **预计还能撑几秒**按打它的那些人的伤害算（ttd），不等血掉下来
  3. 「退」是一个**状态**，不是 8 秒租约：血回到 RETURN_FRAC 以上才归队（原则：能用条件就别用时长）。
     状态发布给大脑（resting.json），出击 / 打野 / 守家不再派它，整队恢复照样能带它去回血
团队规则仍由 mod_combat 统一裁决（同时撤下的人数上限、家门口大仗），但**预计 TTD_CRIT 游戏秒内就死**的不受这两条限制：
留下也只能再打一两下，拉走不损输出（和 CRIT_FRAC「丝血不受上限」同一个道理，只是按伤害算、不按血量算）。
另加一条团队规则 cover_picks：一个人被拉出来时，附近健康的队友去打**正在打它的那几个** —— 追它的人就是送上门的。

英雄不走这里的「养伤」状态：英雄有自己的一整套（集火撤离、孤身撤回、治疗后归队、heroplan），这里只给它们算处境。
召唤物不养伤（水元素本来就是拿来挡刀的）。
"""
from __future__ import annotations

import math

BOARD_R = 700.0          # 敌方作战单位离我方任一作战单位这么近才读它在打谁（近战贴脸 + 远程射程）
BOARD_REFRESH = 0.5      # 同一个敌人多久重读一次（墙钟秒；2 倍速约 1 游戏秒）
BOARD_FRESH = 1.2        # 读数这么久以内才算数
BOARD_BUDGET = 0.04      # 一轮（100ms 循环一圈）最多花这么多秒读（一次约 12ms，约 3 个）
TTD_OUT = 3.5            # 游戏秒：打它的那些人这么快能打死它 -> 退。反应链：读数最多旧 0.5 墙钟秒（2 倍速 1 游戏秒）+ 下令起步
TTD_CRIT = 1.8           # 游戏秒：再不走就死 —— 不受「同时撤下人数上限」和「家门口大仗不拉」
RETURN_FRAC = 0.50       # 养伤到血这么多才归队
ISO_OUT_MAX = 30.0       # 孤兵拉出来最多脱离这么久（墙钟秒）；身边重新有自己人就提前归队（5-115 #49，见 mod_combat 孤兵回撤）
RESTING_TTL = 2.5        # resting.json 每条租约多久（墙钟秒）；养伤期间每 0.5 秒续一次，状态一结束最多 2.5 秒就失效
COVER_N = 2              # 一个人被拉出来时，最多叫几个队友去打追它的人
COVER_PAD = 250.0        # 队友离「打它的人」在自己射程 + 这么远以内才叫
COVER_MIN_FRAC = 0.5     # 血比这么高的队友才去掩护


def armor_factor(armor: float) -> float:
    """War3 护甲减伤：正护甲 1 - 0.06a/(1+0.06a)；负护甲 2 - 0.94^(-a)。"""
    if armor >= 0:
        return 1.0 - 0.06 * armor / (1.0 + 0.06 * armor)
    return 2.0 - 0.94 ** (-armor)


def frac(u) -> float:
    return (u.hp / u.hp_max) if getattr(u, "hp_max", 0) else 1.0


class FocusBoard:
    """谁在打谁：一个敌人一行（它的任务目标句柄 + 读的时刻）。全军共用，读一次所有人都用得上。

    ⚠ 「读到 0 个在打它」和「身边的敌人没读过」必须分开（同一个 0 不能表示两件事），所以另有 coverage()。"""

    def __init__(self):
        self.ent: dict[int, tuple] = {}      # 敌方地址 -> (目标句柄对 或 None, 读的时刻)
        self.reads = 0
        self.read_s = 0.0
        self.unread = 0                      # 读了但没有目标（没任务 / 读不到）

    def due(self, foes, ours, now: float) -> list:
        """该读的敌人：离我方作战单位 BOARD_R 内、没读过或超过 BOARD_REFRESH。
        预算有限时先读「身边我方血比最低」的敌人 —— 先看快死的人身边。"""
        cand = []
        for f in foes:
            if f.hp <= 0:
                continue
            e = self.ent.get(f.addr)
            if e is not None and now - e[1] < BOARD_REFRESH:
                continue
            risk = None
            for u in ours:
                if u.hp > 0 and math.hypot(f.x - u.x, f.y - u.y) <= BOARD_R:
                    r = frac(u)
                    risk = r if risk is None else min(risk, r)
            if risk is None:
                continue
            age = (now - e[1]) if e is not None else 1e9
            cand.append((risk, -age, f.addr, f))
        cand.sort(key=lambda t: t[:3])
        return [t[3] for t in cand]

    def put(self, addr: int, tg, now: float) -> None:
        pair = (int(tg[0]), int(tg[1])) if tg else None
        if pair is None:
            self.unread += 1
        self.ent[addr] = (pair, now)
        self.reads += 1

    def attackers(self, u, foes, now: float) -> list:
        me = (getattr(u, "handle_lo", None), getattr(u, "handle_hi", None))
        out = []
        for f in foes:
            e = self.ent.get(f.addr)
            if e is not None and e[0] is not None and now - e[1] <= BOARD_FRESH and e[0] == me and f.hp > 0:
                out.append(f)
        return out

    def coverage(self, u, foes, now: float, r: float = BOARD_R) -> tuple[int, int]:
        """(身边 r 内读过的敌人, 身边 r 内的敌人)。"""
        near = [f for f in foes if f.hp > 0 and math.hypot(f.x - u.x, f.y - u.y) <= r]
        fresh = sum(1 for f in near if (e := self.ent.get(f.addr)) is not None and now - e[1] <= BOARD_FRESH)
        return fresh, len(near)

    def prune(self, alive: set) -> None:
        for a in [a for a in self.ent if a not in alive]:
            self.ent.pop(a, None)


def incoming_dps(u, attackers, dps_of, armor_of) -> float:
    """打它的那些人每游戏秒能打掉它多少血（表里的攻击 / 冷却，按它的护甲减）。"""
    raw = sum(dps_of(a.type) for a in attackers)
    return raw * armor_factor(armor_of(u.type))


def ttd(u, inc: float) -> float:
    """按打它的人算，还能撑几游戏秒（没人打它 = 无穷）。"""
    return u.hp / inc if inc > 0 else math.inf


class Minds:
    """每个单位的「打 / 养伤」状态。进养伤由 mod_combat 的拉人那一步写（mark_out），出养伤看血（update）。"""

    def __init__(self):
        self.out: dict[int, dict] = {}       # 地址 -> {since, why, hp0}
        self.last: dict[int, dict] = {}      # 地址 -> 这一轮的处境（给心跳 / 发布用）
        self.counts = {"进养伤": 0, "回血归队": 0, "养伤中死亡或消失": 0, "孤兵归队": 0}

    def resting(self, u) -> bool:
        return u.addr in self.out

    def mark_out(self, u, now: float, why: str, kind: str = "hp") -> None:
        """kind：hp = 残血拉出来，血回到 RETURN_FRAC 才归队；iso = 孤兵拉出来，身边重新有自己人（rejoined）才归队。"""
        if u.addr not in self.out:
            self.out[u.addr] = {"since": now, "why": why, "hp0": u.hp, "kind": kind}
            self.counts["进养伤"] += 1

    def update(self, units_by_addr: dict, now: float, rejoined=None) -> list:
        """该归队的归队；看不见了的清掉。返回这一轮归队的单位。
        残血的看血（RETURN_FRAC）；孤兵的看 rejoined(u)（调用方给：身边重新有自己人 / 回到家门口），最长 ISO_OUT_MAX 秒。"""
        back = []
        for addr in list(self.out):
            u = units_by_addr.get(addr)
            if u is None or u.hp <= 0:
                self.out.pop(addr, None)
                self.counts["养伤中死亡或消失"] += 1
                continue
            e = self.out[addr]
            if e.get("kind") == "iso":
                if frac(u) < RETURN_FRAC:
                    e["kind"] = "hp"                       # 孤兵路上掉到半血以下：按残血养
                elif (rejoined is not None and rejoined(u)) or now - e["since"] >= ISO_OUT_MAX:
                    self.out.pop(addr, None)
                    self.counts["孤兵归队"] += 1
                    back.append(u)
                continue
            if frac(u) >= RETURN_FRAC:
                self.out.pop(addr, None)
                self.counts["回血归队"] += 1
                back.append(u)
        return back

    def deadlines(self, now: float) -> dict:
        return {a: now + RESTING_TTL for a in self.out}


NO_REST = {"hmil"}       # 民兵 45 秒后变回农民，归经济那边管


def can_rest(u, summons) -> bool:
    """谁可以进养伤：非英雄、非召唤物、非民兵的作战单位。"""
    return not getattr(u, "is_hero", False) and u.type not in summons and u.type not in NO_REST


def cover_picks(victim, attackers, allies, busy: set, range_of, max_n: int = COVER_N) -> list:
    """被拉出来的人身后：挑最多 max_n 个健康队友去打「正在打它的人」里离得最近的。
    -> [(队友, 目标)]。队友要：不在 busy（撤退 / 养伤 / 轮换 / 施法保护…）、血比 >= COVER_MIN_FRAC、
       离那个敌人在自己射程 + COVER_PAD 以内。远程优先（不用追、不会被拖进人堆）。"""
    if not attackers:
        return []
    picks, used = [], set()
    cands = []
    for a in allies:
        if a.addr == victim.addr or a.addr in busy or a.hp <= 0 or frac(a) < COVER_MIN_FRAC:
            continue
        reach = range_of(a.type) + COVER_PAD
        best = min(attackers, key=lambda f: math.hypot(f.x - a.x, f.y - a.y))
        d = math.hypot(best.x - a.x, best.y - a.y)
        if d > reach:
            continue
        cands.append((range_of(a.type) < 300, d, a.addr, a, best))
    cands.sort(key=lambda t: t[:3])
    for _melee, _d, addr, a, tgt in cands:
        if addr in used:
            continue
        picks.append((a, tgt))
        used.add(addr)
        if len(picks) >= max_n:
            break
    return picks

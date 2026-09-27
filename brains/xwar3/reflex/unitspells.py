# -*- coding: utf-8 -*-
"""unitspells.py —— 兵种技能（纯逻辑）：女巫变形术 / 减速、龙鹰空中锁镣，对敌方高伤害单位**分散**放，效果结束再补。

★★ 09-22 让带技能的高级兵对敌方高伤害单位放技能：分散到不同目标，不集中在同一个单位上；
  目标恢复正常后由其它施法兵接着补。
现状（1 号实例最近 30 个战斗帧文件，一次性分析脚本 caster_orders.py / army_types.py）：
  * 对面 27/30 是兽族：牛头人 otau、步兵 ogru、双足飞龙 owyv、猎头者 ohun、狼骑 orai、蝙蝠 otbr，先知的狼 osw1/2/3 445 只
  * 女巫 hsor 107 只：只有引擎自动施法的减速（进入减速订单 217 次），目标引擎随手挑，不看伤害高低
  * 龙鹰 hdhw 45 只：**空中锁镣 0 次**（对面飞龙 97、蝙蝠 62）；破法者 10 只魔法盗取 1 次
  * 大脑从没研究过女巫训练 Rhst（愿望表里没有），所以女巫学不到变形术
技能数据（w3data/AbilityData.slk；时长是游戏秒）：
  Aply 变形术  220 蓝  射程 500  单位 60 秒、不能对英雄、只对有机    要女巫训练两级（最大蓝 200 -> 400，UpgradeData Rhst rmnx +100/级）
  Aslo 减速    50 蓝   射程 700  单位 60 秒 / 英雄 10 秒
  Amls 空中锁镣 75 蓝  射程 550  单位 40 秒 / 英雄 10 秒、只对有机空中单位、要一直引导（龙鹰也不能动）
分散 / 补：每个目标同一种技能只挂一个（active），被变羊 / 被锁住的整个跳过；一拍里一个目标只收一条新令；
  恢复正常的判据：变形术 = 快照里类型变回原样（变羊时类型是 nshe/nshf…）；锁镣 = 放锁镣的龙鹰订单不再是 magicleash；
  减速没有免费的信号，按时长到期。到期 / 恢复 / 目标死了就从 active 里拿掉，下一拍别的施法兵接着上。
放没放出来：下令 RCPT_DELAY 秒后读施法兵的蓝，掉了 >= 六成技能蓝才算放出来；没掉 = 没放出来，从 active 里拿掉、这只施法兵短退避。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

GAME_SPEED = 3.0


@dataclass(frozen=True)
class Spell:
    name: str
    cn: str
    caster: str
    abil: str
    order: int
    cost: float
    rng: float
    dur: float            # 游戏秒（对普通单位）
    hero_dur: float       # 游戏秒（对英雄）；0 = 不能对英雄
    air: bool
    ground: bool
    organic_only: bool
    weight: float = 1.0   # 同一个目标上几种技能怎么排：定住（变羊 / 锁住）比减速值钱
    channel: bool = False
    disables: bool = False
    research: tuple = ()  # (研究码, 等级)：要研究到这一级才有


SPELLS = (
    Spell("polymorph", "变形术", "hsor", "Aply", 0x000D006A, 220.0, 500.0, 60.0, 0.0, True, True, True,
          weight=1.6, disables=True, research=("Rhst", 2)),
    Spell("magicleash", "空中锁镣", "hdhw", "Amls", 0x000D0200, 75.0, 550.0, 40.0, 10.0, True, False, True,
          weight=1.5, channel=True, disables=True),
    Spell("slow", "减速", "hsor", "Aslo", 0x000D006B, 50.0, 700.0, 60.0, 10.0, True, True, False),
)
BY_NAME = {s.name: s for s in SPELLS}
CASTER_TYPES = frozenset(s.caster for s in SPELLS)
SHEEP = frozenset({"nshe", "nshf", "nsha", "nshw"})   # Aply DataB..E：变羊之后的单位类型
CAST_SLACK = 60.0         # 射程外多给这么多（引擎走一小步），不为放技能往敌群里走
HERO_DPS = 35.0           # units.json 里英雄只有基础攻击（牛头人酋长 3.4），按这个保底
POLY_MIN_FRAC = 0.4       # 变形术 220 蓝：快死的不变（我们的兵马上就打死了）
MAX_PER_TICK = 4          # 一拍最多下几条（每条一次调用 + 一次读蓝）
RCPT_DELAY = 1.0          # 墙钟秒：下令后多久读蓝核对
RCPT_DROP = 0.6           # 蓝掉了技能蓝的这么多 = 放出来了
FAIL_BACKOFF = 3.0        # 没放出来：这只施法兵这个技能歇这么久
REJECT_BACKOFF = 15.0     # 引擎不接：歇这么久
SPELL_OFF_AFTER = 3       # 同一个技能连续被退这么多次（多半是没研究），整个技能歇 SPELL_OFF_S
SPELL_OFF_S = 30.0
LEASH_CHECK = 1.0         # 锁镣下令后这么久起，龙鹰订单不是 magicleash 就算锁镣断了


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


class UnitSpells:
    def __init__(self, dps, air_types, summons, mechanical, game_speed: float = GAME_SPEED):
        self.dps, self.air, self.summons, self.mech = dps, air_types, summons, mechanical
        self.speed = game_speed
        self.active: dict[tuple[str, int], float] = {}      # (技能, 目标地址) -> 到期（墙钟）
        self.leash: dict[int, tuple[int, float, bool]] = {}       # 龙鹰地址 -> (目标地址, 下令时刻, 看到过在引导)
        self.poly_type: dict[int, tuple[str, float, bool]] = {}   # 被变羊的目标 -> (原来的类型, 下令时刻, 看到过变羊)
        self.backoff: dict[tuple[int, str], float] = {}     # (施法兵, 技能) -> 歇到
        self.spell_off: dict[str, float] = {}               # 技能 -> 整个歇到
        self.rejects: Counter = Counter()                   # 技能 -> 连续被退次数
        self.pending: list[tuple[int, str, int, float, float]] = []   # (施法兵, 技能, 目标, 下令时刻, 下令前的蓝)
        self.tally: Counter = Counter()                     # 「技能·结果」 -> 次（心跳）
        self.target_types: Counter = Counter()              # 放出来的目标类型

    # ── 目标 ────────────────────────────────────────────────────────────
    def disabled(self, f) -> bool:
        return f.type in SHEEP or any((s.name, f.addr) in self.active for s in SPELLS if s.disables)

    def score(self, sp: Spell, f) -> float:
        """这个技能放在这个敌人身上值多少；0 = 不能 / 不该放。按每秒伤害排（优先敌方高伤害单位）。"""
        if f.hp <= 0 or f.type in self.summons or self.disabled(f) or (sp.name, f.addr) in self.active:
            return 0.0
        air = f.type in self.air
        if (air and not sp.air) or (not air and not sp.ground):
            return 0.0
        if sp.organic_only and f.type in self.mech:
            return 0.0
        hero = bool(getattr(f, "is_hero", False))
        if hero and sp.hero_dur <= 0:
            return 0.0
        base = self.dps.get(f.type, 0.0) or 0.0
        if hero:
            base = max(base, HERO_DPS)
        if base <= 0:
            return 0.0
        frac = f.hp / max(1.0, f.hp_max)
        if sp.name == "polymorph" and frac < POLY_MIN_FRAC:
            return 0.0
        dur_w = math.sqrt(sp.hero_dur / sp.dur) if hero else 1.0     # 对英雄时长短（减速 10 秒 vs 60 秒）
        return base * sp.weight * dur_w * (0.5 + 0.5 * frac)

    # ── 一拍 ────────────────────────────────────────────────────────────
    def refresh(self, units_by_addr: dict, now: float) -> list[int]:
        """到期 / 目标死了 / 恢复正常的从 active 里拿掉。返回锁镣结束了的龙鹰（调用方还指挥权）。
        ⚠ 两个「恢复」信号（变羊后快照类型是羊、引导时龙鹰订单是 magicleash）没实机验证过：
          只有**先看到过**变羊 / 在引导，才拿「变回来了 / 不引导了」判恢复；没看到过只按时长（心跳「看到变羊」「看到在引导」数它灵不灵）。"""
        for key, until in list(self.active.items()):
            sp_name, t = key
            u = units_by_addr.get(t)
            if until <= now or u is None or u.hp <= 0:
                self.active.pop(key, None)
                if sp_name == "polymorph":
                    self.poly_type.pop(t, None)
                continue
            if sp_name == "polymorph" and t in self.poly_type:
                orig, t0, seen = self.poly_type[t]
                if u.type in SHEEP and not seen:
                    self.poly_type[t] = (orig, t0, True)
                    self.tally["变形术·看到变羊"] += 1
                elif seen and u.type == orig:
                    self.active.pop(key, None)             # 变回原样了（被驱散）：别的女巫接着上
                    self.poly_type.pop(t, None)
                    self.tally["变形术·提前恢复"] += 1
        ended = []
        order = BY_NAME["magicleash"].order
        for c, (t, t0, seen) in list(self.leash.items()):
            cu = units_by_addr.get(c)
            tu = units_by_addr.get(t)
            gone = cu is None or cu.hp <= 0 or tu is None or tu.hp <= 0
            cur = getattr(cu, "order", None) if cu is not None else None
            if not gone and cur == order and not seen:
                self.leash[c] = (t, t0, True)
                self.tally["空中锁镣·看到在引导"] += 1
                continue
            broke = not gone and seen and now - t0 >= LEASH_CHECK and cur is not None and cur != order
            if gone or broke or ("magicleash", t) not in self.active:
                self.leash.pop(c, None)
                self.active.pop(("magicleash", t), None)
                if broke:
                    self.tally["空中锁镣·断了"] += 1
                ended.append(c)
        return ended

    def plan(self, casters, foes, now: float, mana_of, tech=None) -> list:
        """-> [(施法兵, 技能, 目标, 下令前的蓝)]。casters：能用的施法兵（调用方已排掉撤退 / 轮换中的）；
        foes：敌方玩家单位；mana_of(施法兵) -> 蓝或 None（读不到不放）；tech(研究码) -> 等级或 None（None = 不知道，照放、靠被退退避）。"""
        cands = []
        for i, sp in enumerate(SPELLS):
            if self.spell_off.get(sp.name, 0.0) > now:
                continue
            if sp.research and tech is not None:
                lv = tech(sp.research[0])
                if lv is not None and lv < sp.research[1]:
                    continue
            for f in foes:
                s = self.score(sp, f)
                if s > 0:
                    cands.append((-s, i, f.addr, sp, f))
        cands.sort(key=lambda c: c[:3])
        out, used_c, used_t = [], set(), set()
        for _, _, _, sp, f in cands:
            if len(out) >= MAX_PER_TICK:
                break
            if f.addr in used_t:
                continue
            best = None
            for c in casters:
                if (c.type != sp.caster or c.addr in used_c or c.addr in self.leash or c.hp <= 0
                        or self.backoff.get((c.addr, sp.name), 0.0) > now):
                    continue
                d = _dist(c, f)
                if d > sp.rng + CAST_SLACK or (best is not None and d >= best[0]):
                    continue
                m = mana_of(c)
                if m is None or m < sp.cost:
                    continue
                best = (d, c, m)
            if best is None:
                continue
            out.append((best[1], sp, f, best[2]))
            used_c.add(best[1].addr)
            used_t.add(f.addr)
        return out

    def issued(self, caster, sp: Spell, target, now: float, mana_before: float, ok: bool) -> None:
        """下了令之后记账：接了先占住目标（别人别再挑它），RCPT_DELAY 后按蓝核对。"""
        if not ok:
            self.tally[f"{sp.cn}·被退"] += 1
            self.backoff[(caster.addr, sp.name)] = now + REJECT_BACKOFF
            self.rejects[sp.name] += 1
            if self.rejects[sp.name] >= SPELL_OFF_AFTER:
                self.spell_off[sp.name] = now + SPELL_OFF_S
                self.rejects[sp.name] = 0
                self.tally[f"{sp.cn}·连续被退整个歇"] += 1
            return
        self.tally[f"{sp.cn}·下令"] += 1
        dur = sp.hero_dur if getattr(target, "is_hero", False) else sp.dur
        self.active[(sp.name, target.addr)] = now + dur / self.speed
        if sp.name == "polymorph":
            self.poly_type[target.addr] = (target.type, now, False)
        if sp.channel:
            self.leash[caster.addr] = (target.addr, now, False)
        self.pending.append((caster.addr, sp.name, target.addr, now, mana_before))

    def receipts(self, now: float, mana_of, units_by_addr: dict) -> None:
        keep = []
        for c, name, t, t0, m0 in self.pending:
            if now - t0 < RCPT_DELAY:
                keep.append((c, name, t, t0, m0))
                continue
            sp = BY_NAME[name]
            cu = units_by_addr.get(c)
            m1 = mana_of(cu) if cu is not None else None
            if m1 is None:
                self.tally[f"{sp.cn}·读不到蓝"] += 1
                continue
            if m0 - m1 >= RCPT_DROP * sp.cost:
                self.tally[f"{sp.cn}·放出来"] += 1
                self.rejects[name] = 0
                tu = units_by_addr.get(t)
                if tu is not None:
                    self.target_types[self.poly_type.get(t, (tu.type,))[0] if name == "polymorph" else tu.type] += 1
            else:
                self.tally[f"{sp.cn}·没放出来"] += 1
                self.active.pop((name, t), None)
                if name == "polymorph":
                    self.poly_type.pop(t, None)
                if self.leash.get(c, (None,))[0] == t:
                    self.leash.pop(c, None)
                self.backoff[(c, name)] = now + FAIL_BACKOFF
        self.pending = keep

    def heartbeat(self) -> str:
        if not self.tally and not self.target_types:
            return "无"
        tt = "、".join(f"{k} {v}" for k, v in self.target_types.most_common(6))
        return f"{dict(self.tally)}" + (f"（放在 {tt}）" if tt else "")

    def reset_counts(self) -> None:
        self.tally.clear()
        self.target_types.clear()

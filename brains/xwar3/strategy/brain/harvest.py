# -*- coding: utf-8 -*-
"""采金 / 伐木派工 —— 照 AMAI `universal_peon_assignment`（third_party/AMAI/common.eai:14605）。

★ 09-21 运营层从大模型切回非 LLM 模式，派工参考 AMAI 的做法。

## AMAI 怎么派

每 3 秒 `ClearHarvestAI()`，然后按顺序声明一串「采金 / 伐木位」，引擎按这个顺序把**全部**农民填进去：

    主矿金 4 个 -> 木 1 个 -> 主矿金第 5 个 -> 木 pwh 个 -> 木 swh 个
      -> 每座分矿金 5 个（金子特别多时 3 个）-> 剩下的全去砍木（HarvestWood(0, 20)）

  * pwh：源码是一串 if/elif，但第一档 `g - w > 300` 把后面 500 / 900 那两档都截住了，**实际恒为 2**。
    这是 AMAI 自己的怪癖，照搬（我们要的是它实际的行为，不是它注释里的意图）。
  * swh：木 < 200 -> 4；否则按金 / 木比：< 1.1 -> 0，< 1.4 -> 1，< 1.7 -> 2，< 2.0 -> 3，其余 -> 4。

## 和我们原来的写法差在哪

原来 job_gold / job_lumber **只从闲人里拿人**，全在采集时谁也调不动谁，于是一层层打补丁
（伐木人数一条、缺木头多派一条、互相调人一条……），09-21 一天里其中两条出了 bug（1-262 木头死锁）。
AMAI 是**每一轮对所有农民按同一张优先级表重新算**：谁该在哪，只有一个答案，不可能两条规则互相抢。

我们不是引擎，下令有代价（走过去要时间、刚派的人快照里要一会儿才对得上），所以
「按表重新算」之后：闲人马上按表派；在采的人每 REBALANCE_EVERY 秒最多挪一个（runtime._amai_harvest）。
"""
from __future__ import annotations

from collections import Counter

GOLD_PER_MINE = 5          # ver_optimal_gold（common.eai:335）
PWH = 2                    # 见上：AMAI 源码里 pwh 实际恒为 2
WOOD_TAIL = 20             # HarvestWood(0, 20)：剩下的全去砍木


def secondary_wood(gold: float, wood: float) -> int:
    """swh（common.eai:14627）。"""
    if wood < 200:
        return 6 - PWH
    r = gold / max(wood, 1.0)
    for limit, n in ((0.5, 0), (0.8, 1), (1.1, 2), (1.4, 3), (1.7, 4), (2.0, 5)):
        if r < limit:
            return max(n - PWH, 0)
    return max(6 - PWH, 0)


def slots(gold: float, wood: float, mines: list, heroes: int = 0) -> list[tuple[str, object]]:
    """按优先级排好的采集位：[("gold", 矿地址) | ("wood", None), ...]。mines 要**主矿在前**。"""
    out: list[tuple[str, object]] = []
    if mines:
        main = mines[0]
        out += [("gold", main)] * (GOLD_PER_MINE - 1)
        out += [("wood", None)]
        out += [("gold", main)]
    else:
        out += [("wood", None)]
    out += [("wood", None)] * PWH
    out += [("wood", None)] * secondary_wood(gold, wood)
    gdivw = gold / max(wood, 1.0)
    for m in mines[1:]:
        # common.eai:14684：金/木 < 3、或金 < 300、或（三英雄都出了且金 <= 2500）-> 5 个，否则 3 个
        full = gdivw < 3.0 or gold < 300 or (gold <= 2500 and heroes >= 3)
        out += [("gold", m)] * (GOLD_PER_MINE if full else GOLD_PER_MINE - 2)
    out += [("wood", None)] * WOOD_TAIL
    return out


def desired(slot_list: list, n: int) -> tuple[Counter, int]:
    """n 个农民按表填进去之后：(每座矿几个, 伐木几个)。"""
    take = slot_list[:max(0, n)]
    return Counter(a for kind, a in take if kind == "gold"), sum(1 for kind, _a in take if kind == "wood")


def deficits(slot_list: list, n: int, have_gold: Counter, have_wood: int) -> list[tuple[str, object]]:
    """按优先级列出**还缺人**的位（每缺一个人列一次）。第一个就是下一个人该去的地方。"""
    need: Counter = Counter()
    out = []
    for kind, a in slot_list[:max(0, n)]:
        need[(kind, a)] += 1
        have = have_gold.get(a, 0) if kind == "gold" else have_wood
        if need[(kind, a)] > have:
            out.append((kind, a))
    return out


def surplus(slot_list: list, n: int, have_gold: Counter, have_wood: int) -> tuple[Counter, int]:
    """多出来的人：(每座矿多几个, 伐木多几个)。只有多出来的才能被挪走。"""
    want_gold, want_wood = desired(slot_list, n)
    extra_gold = Counter({a: have_gold[a] - want_gold.get(a, 0) for a in have_gold if have_gold[a] > want_gold.get(a, 0)})
    return extra_gold, max(0, have_wood - want_wood)

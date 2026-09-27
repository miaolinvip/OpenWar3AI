# -*- coding: utf-8 -*-
"""感知：快照之外、要走通道读的全局信息（AMAI 没有的）。都带限频缓存，读失败当没有。

  * 英雄等级：w3cmd.hero_level，每个英雄 10 游戏秒一读
  * **电脑队长的目标点**：敌方作战单位跟着的是电脑 AI 的队长逻辑，读出 当前位置 / 目标点 / 家（w3cmd.captain）。
    09-16 实机：电脑 7 个兵还在自家集结时，进攻队长的目标点已经是我方主基地门口 —— 出门之前就知道要打哪。
    每个敌方军团挑一个兵读它的订单，5 游戏秒一轮；同一个队长只记一次。
  * incoming(world, captains)：目标点离我方某座城镇 2000 内、队长本身还在 2500 外 = 这一波正要来这座城
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import amai, offense

HERO_EVERY = 10.0
CAPTAIN_EVERY = 5.0
INCOMING_GOAL_R = 2000.0
INCOMING_FAR = 2500.0


@dataclass
class Perception:
    cmd: object = None
    levels: dict = field(default_factory=dict)       # addr -> (等级, 读的游戏秒)
    captains: list = field(default_factory=list)
    captains_at: float = -1e9
    reads: dict = field(default_factory=dict)

    def _count(self, k: str) -> None:
        self.reads[k] = self.reads.get(k, 0) + 1

    def hero_levels(self, heroes, t: float) -> dict:
        out = {}
        for h in heroes:
            c = self.levels.get(h.addr)
            if c is None or t - c[1] >= HERO_EVERY:
                lvl = None
                if self.cmd is not None:
                    try:
                        lvl = self.cmd.hero_level(h.addr)
                    except Exception:  # noqa: BLE001
                        lvl = None
                self._count("英雄等级·读" if lvl else "英雄等级·读不到")
                c = (lvl or (c[0] if c else 1), t)
                self.levels[h.addr] = c
            out[h.addr] = c[0]
        return out

    def read_captains(self, enemy_armies, t: float) -> list:
        if self.cmd is None or t - self.captains_at < CAPTAIN_EVERY:
            return self.captains
        self.captains_at = t
        seen, out = set(), []
        for army in enemy_armies:
            for u in army.units[:3]:          # 一个军团试前 3 个兵，读到就换下一个军团
                try:
                    order = self.cmd.current_order(u.addr)
                    cap = self.cmd.captain(order)
                except Exception:  # noqa: BLE001
                    cap = None
                if not cap:
                    self._count("队长·没读到")
                    continue
                key = cap.get("addr") or (round(cap["goal_x"]), round(cap["goal_y"]))
                if key in seen:
                    break
                seen.add(key)
                cap = dict(cap, owner=army.owner, army_strength=army.strength, army_n=len(army.units),
                           army_addrs=[u.addr for u in army.units])
                out.append(cap)
                self._count("队长·读到")
                break
        self.captains = out
        return out


def incoming(world, captains) -> list[dict]:
    """队长提供目标意图，部队实际位置提供距离；队长位置可能先跳到目标，不能拿来算 ETA。"""
    out = []
    for cap in captains:
        x, y = cap["x"], cap["y"]
        if "army_addrs" in cap:
            ids = set(cap["army_addrs"])
            actual = [u for u in world.enemy_fighters if u.addr in ids]
            if not actual:
                continue
            x, y = sum(u.x for u in actual) / len(actual), sum(u.y for u in actual) / len(actual)
            cap = dict(cap, army_strength=amai.strength(actual, world.levels), army_speed=offense.speed(actual))
        for town in world.towns:
            if town.owner != world.me:
                continue
            if math.hypot(cap["goal_x"] - town.x, cap["goal_y"] - town.y) > INCOMING_GOAL_R:
                continue
            d = math.hypot(x - town.x, y - town.y)
            if d <= INCOMING_FAR:
                continue
            out.append(dict(town=town, captain=cap, dist=d))
    return out

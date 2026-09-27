"""AMAI 式目标价值 + 可抵达的守军评估。纯逻辑；距离/速度只是 ETA 估计，不是寻路。"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from . import amai, gamedata as G

POLICY = "amai-opportunity-v1.1"
LOCAL_R = 1800.0
RESPONSE_SLACK = 8.0
ROUTE_R = 900.0


def speed(units) -> float:
    return min((max(150.0, float(G.stat(u.type, "spd", 270) or 270)) for u in units), default=270.0)


def distance(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def on_route(u, origin, target) -> bool:
    dx, dy = target.x - origin.x, target.y - origin.y
    length2 = dx * dx + dy * dy
    if length2 < 1:
        return distance(u, origin) <= ROUTE_R
    t = ((u.x - origin.x) * dx + (u.y - origin.y) * dy) / length2
    return 0 <= t <= 1 and math.hypot(u.x - origin.x - t * dx, u.y - origin.y - t * dy) <= ROUTE_R


@dataclass(frozen=True)
class Assessment:
    good: bool
    lhs: float                    # 出击所需强度
    rhs: float                    # 实际参与部队的强度
    local: float
    response: float               # 能赶来的援兵（按到达时间折算）
    route: float                  # 沿路威胁；已计入 response，不再重复加
    tower: float                  # 按攻城能力加权后的塔强度
    eta: float

    def live(self) -> dict:
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(self).items()}


def assess(world, army, origin, target, margin=1.25, bonus=0.0) -> Assessment:
    own = amai.strength(army, world.levels)
    eta = distance(origin, target) / speed(army) * 1.1
    local = response = route = 0.0
    for foe in world.enemy_fighters:
        value = amai.strength([foe], world.levels)
        if distance(foe, target) <= LOCAL_R:
            local += value
            continue
        travel = max(0.0, distance(foe, target) - LOCAL_R) / speed([foe])
        weight = max(0.0, min(1.0, (eta + RESPONSE_SLACK - travel) / RESPONSE_SLACK))
        if on_route(foe, origin, target):
            weight = 1.0
            route += value
        response += value * weight
    towers = [t for t in world.enemy_towers if distance(t, target) <= amai.TOWER_RADIUS or on_route(t, origin, target)]
    tower = amai.strength(towers, world.levels) * (1.0 if any(u.type in G.SIEGE for u in army) else 4.0)
    defenders = local + response
    # AMAI 的进攻性和分矿价值仍有作用，但信用最多为有效兵力的 15%，不能把劣势局算成优势。
    credit = min(own * 0.15, max(0.0, world.aggression * 0.5 + bonus))
    need = max(defenders * margin + tower - credit, defenders * 0.95 + tower * 0.75)
    need += distance(origin, target) / 7000.0
    return Assessment(own > 0 and own >= need, need, own, local, response, route, tower, eta)


def return_due(inc, army) -> bool:
    """队长的进攻意图不是立即回防命令；敌人接近到我军需要启程回防时才召回。"""
    if not army or inc["captain"].get("army_strength", 0) < 4:
        return False
    town = inc["town"]
    cx = sum(u.x for u in army) / len(army)
    cy = sum(u.y for u in army) / len(army)
    our_eta = max(0.0, math.hypot(cx - town.x, cy - town.y) - 1500) / speed(army) * 1.1
    foe_eta = max(0.0, inc["dist"] - 2000) / max(150.0, inc["captain"].get("army_speed", 320.0))
    return foe_eta <= our_eta + 8.0

# -*- coding: utf-8 -*-
"""全局世界状态（AMAI 的 ARMY_TRACK / TOWN_TRACK，但数据是真实的全图，不是 AMAI 的"看得见的"）。

输入是 w3state 的快照（全部单位带所属玩家、我方金木人口）+ 游戏时钟 + 英雄等级；
输出一份只读的 World：敌我单位分类、本数、敌我军团聚类（带移动方向和预测位置）、每座城镇的威胁。
单位对象只要有 addr/type/owner/x/y/hp/hp_max（w3state.Unit 或测试里的 SimpleNamespace 都行）。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from . import amai
from . import gamedata as G

ARMY_FUTURE_MULT = 3.0        # ARMY_TRACK army_future_mult：预测位置 = 现在 + 移动量 × 3
HEADING_ANGLE = 0.4           # 朝城镇方向夹角 <= 0.4 弧度且快到了 -> 按满威胁算
MAIN_TIE_R = 1500.0           # main_town：两座大厅同本数时，数这个方框内的自家非大厅建筑，多的是主基地


@dataclass
class Army:
    owner: int
    units: list
    x: float
    y: float
    strength: float
    dx: float = 0.0
    dy: float = 0.0

    @property
    def future(self) -> tuple[float, float]:
        return self.x + self.dx * ARMY_FUTURE_MULT, self.y + self.dy * ARMY_FUTURE_MULT


@dataclass
class Town:
    owner: int
    hall: object
    x: float
    y: float
    threat: float = 0.0
    threat_army: Army | None = None


def is_fighter(u) -> bool:
    t = u.type
    return u.hp > 0 and not G.is_building(t) and not G.is_worker(t) and t in G.units() and float(G.stat(t, "spd", 0)) > 0


def cluster_armies(units, owner: int, levels: dict | None = None, radius: float = amai.ARMY_RADIUS) -> list[Army]:
    """把一个玩家的作战单位按 1500 聚成军团（反复吸收重心 1500 内的单位直到不再变）。"""
    pool = [u for u in units if u.owner == owner and is_fighter(u)]
    armies = []
    while pool:
        members = [pool.pop(0)]
        cx, cy = members[0].x, members[0].y
        changed = True
        while changed:
            changed = False
            for u in pool[:]:
                if math.hypot(u.x - cx, u.y - cy) <= radius:
                    members.append(u)
                    pool.remove(u)
                    changed = True
            cx = sum(m.x for m in members) / len(members)
            cy = sum(m.y for m in members) / len(members)
        armies.append(Army(owner, members, cx, cy, amai.strength(members, levels)))
    return armies


def track_motion(armies: list[Army], previous: list[Army] | None, match_dist: float = 2000.0) -> None:
    """和上一次的军团按同一玩家、最近重心配对，记下移动量（方向和预测位置用）。"""
    if not previous:
        return
    for a in armies:
        best, bd = None, match_dist
        for p in previous:
            if p.owner != a.owner:
                continue
            d = math.hypot(a.x - p.x, a.y - p.y)
            if d < bd:
                best, bd = p, d
        if best is not None:
            a.dx, a.dy = a.x - best.x, a.y - best.y


def army_town_dist(a: Army, tx: float, ty: float) -> float:
    """UpdateSpecificTownThreat 的距离：取 现在/预测 位置里近的；正朝城镇来且快到了 -> 满威胁距离。"""
    fx, fy = a.future
    d_now = math.hypot(a.x - tx, a.y - ty)
    dist = min(max(math.hypot(fx - tx, fy - ty), amai.FULL_THREAT_DIST), d_now)
    move = math.hypot(a.dx, a.dy)
    if move > 0 and d_now < move:
        ang = abs(math.atan2(ty - a.y, tx - a.x) - math.atan2(a.dy, a.dx))
        ang = min(ang, 2 * math.pi - ang)
        if ang <= HEADING_ANGLE:
            dist = amai.FULL_THREAT_DIST
    return dist


def town_threat(town_x: float, town_y: float, enemy_armies: list[Army], enemy_towers, levels=None) -> tuple[float, Army | None]:
    threat = 2 * sum(amai.unit_strength(t, levels) for t in enemy_towers
                     if math.hypot(t.x - town_x, t.y - town_y) <= amai.TOWER_RADIUS)
    worst, worst_t = None, 0.0
    for a in enemy_armies:
        t = amai.threat_of(a.strength, army_town_dist(a, town_x, town_y))
        threat += t
        if t > worst_t:
            worst, worst_t = a, t
    return threat, worst


@dataclass
class World:
    t: float
    me: int
    gold: int
    lumber: int
    food_used: int
    food_cap: int
    units: list
    levels: dict = field(default_factory=dict)
    foe_race: str | None = None
    # 以下由 build() 填
    mine: list = field(default_factory=list)
    my_buildings: list = field(default_factory=list)
    my_army: list = field(default_factory=list)
    my_heroes: list = field(default_factory=list)
    my_workers: list = field(default_factory=list)
    my_halls: list = field(default_factory=list)
    my_militia: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    enemy_buildings: list = field(default_factory=list)
    enemy_fighters: list = field(default_factory=list)
    late_food: bool = False            # 09-22 晚：后期人口放到 100（runtime.food_soft_cap 设；愿望表据此多要兵）
    farm_cap: int = G.FOOD_FARM_CAP    #   人口建筑盖到这里（后期 G.FOOD_FARM_CAP_LATE）
    enemy_workers: list = field(default_factory=list)
    enemy_halls: list = field(default_factory=list)
    enemy_towers: list = field(default_factory=list)
    creeps: list = field(default_factory=list)
    critters: list = field(default_factory=list)     # 中立被动的小动物（G.is_critter）：挡分矿落点
    mines: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    tier: int = 1
    foe_tier: int = 1
    my_armies: list = field(default_factory=list)
    enemy_armies: list = field(default_factory=list)
    towns: list = field(default_factory=list)
    aggression: float = 0.0

    @property
    def food_free(self) -> int:
        return self.food_cap - self.food_used

    @property
    def own_strength(self) -> float:
        return amai.strength(self.my_army + self.my_heroes, self.levels)

    @property
    def foe_strength(self) -> float:
        return amai.strength(self.enemy_fighters, self.levels)

    def main_town(self) -> Town | None:
        mine = [tw for tw in self.towns if tw.owner == self.me]
        if not mine:
            return None
        # 主基地 = 本数最高的大厅；同本数取**周围自家建筑最多**的那座。
        # ★ 09-21 1-284：原来「同本数取第一个」—— 分矿大厅盖好、主城还没升完主城的那一段，两座都是 htow，
        #   列表顺序是快照顺序（内存地址），这局取到了分矿：23:35:40 藏宝室盖到了分矿 (-3776,-2624)，
        #   离主基地 5600，家里的伤兵买不到卷轴（商店应当建在主基地附近，不能落到二矿）。
        #   主基地有祭坛/兵营/农场，分矿只有几座塔 —— 数 MAIN_TIE_R 内的非大厅建筑就分得开。
        def rank(tw):
            if not tw.hall:
                return (0, 0)
            around = sum(1 for b in self.my_buildings if b.type not in G.HALLS
                         and abs(b.x - tw.x) <= MAIN_TIE_R and abs(b.y - tw.y) <= MAIN_TIE_R)
            return (3 if tw.hall.type in G.TIER3_HALLS else 2 if tw.hall.type in G.TIER2_HALLS else 1, around)
        return max(mine, key=rank)


def build_world(snap_units, me: int, t: float, gold: int, lumber: int, food_used: int, food_cap: int,
                levels: dict | None = None, previous: World | None = None) -> World:
    w = World(t=t, me=me, gold=gold or 0, lumber=lumber or 0, food_used=food_used or 0, food_cap=food_cap or 0,
              units=list(snap_units), levels=dict(levels or {}))
    alive = [u for u in w.units if u.hp > 0]
    w.mine = [u for u in alive if u.owner == me]
    for u in w.mine:
        w.counts[u.type] += 1
    militia = {r["militia"] for r in G.RACES.values() if r["militia"]}
    w.my_buildings = [u for u in w.mine if G.is_building(u.type)]
    w.my_workers = [u for u in w.mine if G.is_worker(u.type)]
    w.my_heroes = [u for u in w.mine if G.is_hero(u.type)]
    w.my_militia = [u for u in w.mine if u.type in militia]
    w.my_army = [u for u in w.mine if is_fighter(u) and not G.is_hero(u.type) and u.type not in militia]
    w.my_halls = [u for u in w.my_buildings if u.type in G.HALLS]
    w.enemies = [u for u in alive if u.owner != me and u.owner < G.CREEP_OWNER]
    w.enemy_buildings = [u for u in w.enemies if G.is_building(u.type)]
    w.enemy_workers = [u for u in w.enemies if G.is_worker(u.type)]
    w.enemy_fighters = [u for u in w.enemies if is_fighter(u)]
    w.enemy_halls = [u for u in w.enemy_buildings if u.type in G.HALLS]
    w.enemy_towers = [u for u in w.enemy_buildings if G.is_tower(u.type)]
    w.creeps = [u for u in alive if u.owner == G.CREEP_OWNER]
    w.critters = [u for u in alive if G.is_critter(u)]
    w.mines =[u for u in w.units if u.type in G.MINES and u.hp > 0]
    w.tier = G.tier_of_halls(u.type for u in w.my_halls)
    w.foe_tier = G.tier_of_halls(u.type for u in w.enemy_halls)
    if w.foe_race is None:
        letters = Counter(G.race_of(u.type) for u in w.enemies if G.race_of(u.type))
        w.foe_race = letters.most_common(1)[0][0] if letters else None
    w.my_armies = cluster_armies(w.mine, me, w.levels)
    w.enemy_armies = [a for o in sorted({u.owner for u in w.enemy_fighters}) for a in cluster_armies(w.enemies, o, w.levels)]
    if previous is not None:
        track_motion(w.my_armies, previous.my_armies)
        track_motion(w.enemy_armies, previous.enemy_armies)
    for h in w.my_halls:
        th, ta = town_threat(h.x, h.y, w.enemy_armies, w.enemy_towers, w.levels)
        w.towns.append(Town(me, h, h.x, h.y, th, ta))
    for h in w.enemy_halls:
        mine_armies = w.my_armies
        th, ta = town_threat(h.x, h.y, mine_armies, [u for u in w.my_buildings if G.is_tower(u.type)], w.levels)
        w.towns.append(Town(h.owner, h, h.x, h.y, th, ta))
    w.aggression = amai.aggression(w.food_used, len(w.my_heroes), w.foe_race, w.tier)
    return w

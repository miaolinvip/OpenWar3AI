# -*- coding: utf-8 -*-
"""防塔攻（TR）：对面在我方基地边上下塔时，全军 + 民兵先杀下塔的苦工、再拆没盖完的地基。

09-22 晚 研究 AMAI 的 TR 战术（开局前期直接在对方家门口造箭塔），并补上防御 TR 的策略。
1-415（AMAI 中等兽族）：约 3.8 游戏分钟起 4 个苦工跟着英雄摸到我方主基地门外，先在 1700 下 4 座箭塔、再推到 1200~1300、
最后贴到离大厅 570，一共 10 座箭塔 + 1 座巫毒商店 —— 我们一座都没拆掉，连没盖完的地基都没碰（主力和民兵在 6000 外清二矿野怪，
回防又是「英雄不进箭塔射程」「敌人不在家门口，不出去迎、不追」），7.7 分钟认输。

AMAI 源码（third_party/AMAI/Jobs/TOWER_RUSH*.eai、common.eai ShouldEndTowerRushStrength）：
  * 开局 0~10 秒判一次，兽族 32% 概率、只在 1v1；4 个苦工（race_tower_peon）先把货交回去，再跟英雄去我方主基地门口；
  * 前 3 座在最外面、第 4~6 座往里 460、6 座以后贴着大厅，并补一座商店；塔掉到 70% 以下苦工去修；
  * 收手：塔位 1200 内我方强度 > 它全军 4 倍，或 > 2 倍且塔一直盖不起来；苦工还没到位时我方 > 2 倍且看得见它的苦工 ——
    收手时没盖完的塔全部取消（退钱）、苦工回家。
所以应对是：**苦工优先**（杀光就没人下塔）、**地基其次**（盖好之前不能打人，刚放下时只有 10% 血）、
盖好的塔只在局部打得过时才拆；主力一压到塔位，AMAI 自己就会按上面那条收手。
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from . import battlepower as BP
from . import gamedata as G
from .tactics import ARMED_BUILDINGS, Intent

TR_R = 2600.0          # 对面的建筑 / 苦工进了我方任何一座大厅这么近 = 塔攻（1-415 第一批塔离大厅 1661~1820）
TR_PEONS = 2           # 没有建筑时，至少这么多个苦工摸进来才算（一个苦工可能只是侦察）
TR_CLEAR_S = 10.0      # 苦工和建筑都没了（或都离开 TR_R）这么多游戏秒才解除
TR_FULL_EPS = 2.0      # 血差不到这么多 = 盖完了（和 powerbuild.SITE_FULL_EPS 同一个口径）
TR_TOWER_R = 950.0     # 打一个目标之前看它这么近的已完工带攻击建筑（箭塔射程 800 + 余量）
TR_TOWER_RATIO = 1.5   # 目标在已完工塔的射程里：我方（这批人）对塔 + 附近敌兵的战力比至少这么多才去
TR_MIL_R = 2200.0      # 塔位离我方大厅这么近才叫民兵（农民要跑进大厅变身）
TR_MIL_ARMY = 8        # 塔位 2000 内我方作战单位不到这么多就叫民兵（民兵免费、45 秒自己变回）


def _d(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


@dataclass
class TowerRushWatch:
    active: bool = False
    last_seen: float = -1e9
    started: float = -1e9
    seen_full: set = field(default_factory=set)        # 对面见过满血的建筑地址（之后掉血是挨打，不是地基）
    counts: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    site: tuple | None = None                           # 最近一次塔攻的中心（民兵、开矿闸用）
    hall: object = None                                 # 离塔位最近的我方大厅

    def _count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    def scan(self, world):
        """-> (苦工, 地基, 其它建筑, 已完工带攻击建筑)，都在我方某座大厅 TR_R 内。"""
        halls = world.my_halls
        for b in world.enemy_buildings:
            if b.hp_max > 0 and b.hp >= b.hp_max - TR_FULL_EPS:
                self.seen_full.add(b.addr)
        if not halls:
            return [], [], [], []

        def near(u):
            return any(_d(u, h) <= TR_R for h in halls)
        peons = [p for p in world.enemy_workers if p.hp > 0 and near(p)]
        blds = [b for b in world.enemy_buildings if b.hp > 0 and b.owner < 12 and near(b) and b.type not in G.MINES]
        sites = [b for b in blds if b.addr not in self.seen_full]
        armed = [b for b in blds if b.addr in self.seen_full and b.type in ARMED_BUILDINGS]
        other = [b for b in blds if b.addr in self.seen_full and b.type not in ARMED_BUILDINGS]
        return peons, sites, other, armed

    def step(self, world, crew) -> list | None:
        """crew = 能派的兵 + 英雄 + 民兵。返回 None = 没有塔攻；列表 = 这一拍的应对意图（可能为空：塔攻中但这批人打不动）。"""
        peons, sites, other, armed = self.scan(world)
        present = bool(sites or other or armed) or len(peons) >= TR_PEONS
        if present:
            if not self.active:
                self.started = world.t
                self._count("塔攻·发现")
                where = sites or other or armed or peons
                self.notes.append(f"发现塔攻：我方基地边上 {len(peons)} 个苦工、{len(sites)} 座没盖完的、"
                                  f"{len(armed)} 座盖好的塔（{where[0].type} @ ({int(where[0].x)},{int(where[0].y)})）")
            self.active = True
            self.last_seen = world.t
        elif self.active and world.t - self.last_seen >= TR_CLEAR_S:
            self.active = False
            self.site = self.hall = None
            self._count("塔攻·解除")
            self.notes.append(f"塔攻解除：基地边上的苦工和建筑都清掉了（持续 {world.t - self.started:.0f} 秒）")
        if not self.active:
            return None
        pts = sites + other + armed + peons
        if pts:
            cx = sum(u.x for u in pts) / len(pts)
            cy = sum(u.y for u in pts) / len(pts)
            self.site = (cx, cy)
            self.hall = min(world.my_halls, key=lambda h: math.hypot(h.x - cx, h.y - cy), default=None)
        crew = [u for u in crew if u.hp > 0]
        if not crew:
            self._count("塔攻·没人可派")
            return []
        target, why = self.pick(world, crew, peons, sites, other, armed)
        if target is None:
            self._count("塔攻·打不动·在塔外等")
            return []
        self._count("塔攻·打" + why.split("（")[0])
        return [Intent("attack", crew, target.x, target.y, target, f"防塔攻：{why}")]

    def pick(self, world, crew, peons, sites, other, armed):
        """先苦工（在施工的优先）、再地基（血少的优先）、再其它建筑、最后盖好的塔；
        目标在盖好的塔射程里时，这批人对「塔 + 附近敌兵」的战力比要够 TR_TOWER_RATIO。"""
        cx = sum(u.x for u in crew) / len(crew)
        cy = sum(u.y for u in crew) / len(crew)
        fighters = [f for f in world.enemy_fighters if f.hp > 0]

        def reachable(t) -> bool:
            guns = [b for b in armed if b.addr != t.addr and _d(b, t) <= TR_TOWER_R]
            if t in armed:
                guns = guns + [t]
            if not guns:
                return True
            guards = guns + [f for f in fighters if _d(f, t) <= TR_TOWER_R]
            # BP.estimate 只算 6 秒内接得上火的 —— 部队还在塔外时双方都是 0。按「打到目标边上」估：这批人放到目标位置
            return BP.estimate([_at(u, t) for u in crew], guards, world.levels)["ratio"] >= TR_TOWER_RATIO

        def dist_c(u):
            return math.hypot(u.x - cx, u.y - cy)
        groups = (
            ("苦工（下塔的人）", sorted(peons, key=lambda p: (not _building(p), dist_c(p)))),
            ("没盖完的地基", sorted(sites, key=lambda b: (b.hp / max(1.0, b.hp_max), dist_c(b)))),
            ("没攻击力的建筑", sorted(other, key=dist_c)),
            ("盖好的塔", sorted(armed, key=lambda b: (b.hp / max(1.0, b.hp_max), dist_c(b)))),
        )
        blocked = 0
        for why, targets in groups:
            for t in targets:
                if reachable(t):
                    return t, f"{why}（{t.type} 血 {int(t.hp)}/{int(t.hp_max)}）"
                blocked += 1
        if blocked:
            self._count("塔攻·目标都在塔射程里", blocked)
        return None, ""

    def wants_militia(self, world) -> bool:
        """塔位离大厅 TR_MIL_R 内、塔位 2000 内我方作战单位不到 TR_MIL_ARMY 个：叫大厅边的农民变民兵。"""
        if not self.active or self.site is None or self.hall is None:
            return False
        sx, sy = self.site
        if math.hypot(self.hall.x - sx, self.hall.y - sy) > TR_MIL_R:
            return False
        near = sum(1 for u in world.my_army + world.my_heroes if u.hp > 0 and math.hypot(u.x - sx, u.y - sy) <= 2000)
        return near < TR_MIL_ARMY


def _at(u, t):
    c = copy.copy(u)
    c.x, c.y = t.x, t.y
    return c


def _building(p) -> bool:
    """苦工正在施工（订单是兽族/人族/亡灵/暗夜的施工令，或建筑 rawcode）。读不到订单 = 不算。"""
    o = getattr(p, "order", 0) or 0
    return o in (0x000D001B, 0x000D001C, 0x000D001D, 0x000D001E, 0x000D001F) or o >= 0x41000000

# -*- coding: utf-8 -*-
"""骚扰小队（AMAI Jobs/HARASS.eai + Human/BuildSequence.ai L267-269 的 Harass 调用），纯逻辑只出意图。

AMAI：主力不在打仗时按进攻性抽签（10%→100%）派小队去打**离得最近的农民**；看见塔、强度掉到出发时 75%、
附近敌方非农民强度 >= 限值就逃回家；单兵血 < max(30%,100) 离队；家里受威胁全部回来。
我们多的：全图数据 —— 挑目标时直接避开 2200 内有塔、800 内有敌方作战强度 >= 限值的农民（AMAI 只能走过去才看见）。
时间单位：AMAI ai_time = 5 秒，Harass(..., min_time 50, time 80) = 开局 250 秒后、间隔 400 秒。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import amai
from . import offense
from . import gamedata as G
from .tactics import ARMED_BUILDINGS, Intent, centroid, main_force

TOWER_RADIUS = 2200.0       # 只剩 AMAI 对照测试用；挑目标 / 走路线 / 避塔现在按每座塔的射程（armed_reach）
ATTACK_RADIUS = 800.0
FLEE_RADIUS = 1200.0
HURT_FRAC, HURT_MIN = 0.55, 160.0
DUAL_CONTACT_R = 1600.0
DUAL_BUSY_R = 1600.0        # 佯攻：离我方主队重心这么近的敌方作战单位算「被缠住」，不计进农民身边的守军
DUAL_BUSY_NEAR = 2400.0     # 主队离目标这么近才算真的缠上了（还在半路上不算，不然小队先送）
DUAL_MIN_MAIN_STRENGTH = 10.0
DUAL_RESERVE_RATIO = 1.12
# ★ 09-22 骚扰派得太少：分矿建起来之后就该去骚扰对方分矿，占优势时更要主动骚扰，不能只顾自己这边。
#   1-304~308 每局：主力在打仗 316~390、人数不够 200~458，一共只派出过 1 次。两道闸都量错了东西：
#   「主力在打仗」把**打野**也算进去（runtime 传 busy，整局大半在打野）—— 现在打野真接战了才算；
#   「至少有几个才派」只数健康的步兵/火枪/骑士，迫击炮、牧师、女巫、狮鹫都不算 —— 现在数整支部队的健康作战单位（army_n）。
#   目标本来就优先对方分矿 1400 内的农民（peon_target）。
HARASS_AHEAD = 1.15         # 我方强度 >= 对面 × 这个 = 优势：冷却缩短、不抽签、主力留人要求放宽
# ★★ 09-22 晚 占上风时能骚扰就骚扰，尽量扩大优势。
#   1-403（赢，204 拍里 131 拍占优）一局只派出 ~15 次，不派的头号理由「人数不够」×634：第一组要整支部队健康作战单位 > 8。
#   最近 13 局每局出发 0~9 次。占优时：主力留人要求降到 HARASS_AHEAD_NEED，冷却 × HARASS_AHEAD_CD（180 秒 -> 约 63 秒）。
HARASS_AHEAD_CD = 0.35
HARASS_AHEAD_NEED = 5
# ★ 09-22 对局里几乎看不到骚扰。骚扰不以「打不打得过」为前提：可分两队，一队佯攻、一队主攻；
#   先判断敌军主力在哪边，碰上主力不硬拼、拉扯即可，并且尽量不损失单位。
#   1-312（ccd9803 起第一局）派出去 5 次，5 次都是出门 1~3 秒就「骚扰没有安全目标，低损收队」：派的时候那个农民在塔外，
#   下一秒它走回大厅（塔边）就不「安全」了 —— 而「安全」是 2200 内不能有塔（塔的射程才 700~800），走到半路看见塔也一律撤。
#   现在：塔只按它自己的射程（+HARASS_TOWER_PAD）算；到了矿区一时没有塔外的农民就在塔外等 HARASS_LINGER_S；
#   敌方兵力压过来不回家，往家的方向拉开 KITE_STEP，追兵离开 KITE_CLEAR_R 就回去接着骚扰（一趟最多 HARASS_MAX_S）；
#   掉人 / 有人受伤才整队回家。挑目标：对方分矿优先，其次离敌方主力远的，双线时避开我方主力在打的那座（佯攻另一边）。
HARASS_TOWER_PAD = 250.0
HARASS_LINGER_S = 25.0
HARASS_MAX_S = 150.0
KITE_CLEAR_R = 1800.0
KITE_STEP = 900.0
FEINT_AVOID_R = 2500.0      # 双线：离主力目标这么近的农民排后面
# ★ 09-22 骚扰小队出去三个、死了两个，只回来一个，等于白给对面经验。
#   1-329 第一队 3 个步兵：目标挑在对方主基地（敌方主力就在家），12:00:13 被毫秒层「补攻击令」（55 > 骚扰的普通移动 50）
#   派去打大厅边的单位；12:00:16 毫秒层拉撤，骚扰模块按「大脑能指挥的人」数成 0，记「人没了」结束 ——
#   其实还活着两个，只是在毫秒层手里；没人再带它们回家，留在对方家门口被补攻击令反复派去打，25 秒后死掉。
#   同局 2 只狮鹫、1-331 又一队步兵同样的形状：拉扯一次、拉开了又回同一片去，第二次没回来。
HARASS_ARMY_CLEAR = 2500.0  # 离「吃得掉这支小队」的敌方军团（强度 >= 小队 × 0.8）这么近的农民不打
# ★★ 09-23 骚扰时其余单位全部停摆，没有并行操作；一两个兵的骚扰意义不大，
#   要骚扰就一队或两队分路去。1-438 实测第 68~78 拍：2 个步兵在外面拉扯了十拍，
#   日志里主力一条命令都没有（营地打光了、还打不动对面，就站在前沿），小队被 27 强度的敌兵撵回来。
#   现在：主力闲着的时候小队按可用兵力放大（AMAI 的 2 人是「主力在忙」时的规格），够多时再开第二路，
#   而且两路的目标互相避开 FEINT_AVOID_R，不走同一条道。
HARASS_BIG_ARMY, HARASS_BIG_QTY = 12, 4
HARASS_HUGE_ARMY, HARASS_HUGE_QTY = 18, 6
HARASS_MAX_SHARE = 0.5      # 一次最多带走可用兵力的一半（家里要留得住）
HARASS_SECOND_NEED = 12     # 可用作战单位够这么多才开第二路


class _Spot:
    """给 peon_target 的 avoid 用：只要有 x / y。"""

    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y


def squad_size(cfg: dict, army_n: int, idle: bool) -> int:
    """这一队带几个：AMAI 的 2 人是「主力在忙」时的规格；主力闲着就按可用兵力放大，最多带走一半。"""
    want_n = cfg["qty"]
    if idle and army_n >= HARASS_HUGE_ARMY:
        want_n = max(want_n, HARASS_HUGE_QTY)
    elif idle and army_n >= HARASS_BIG_ARMY:
        want_n = max(want_n, HARASS_BIG_QTY)
    return max(cfg["qty"], min(want_n, int(army_n * HARASS_MAX_SHARE)))
HARASS_HOT_S = 90.0         # 在一片地方被压过来拉扯过 -> 这片（HARASS_ARMY_CLEAR 内）这么多游戏秒内不再去
HOME_LAND_R = 2500.0        # 撤退撤到离我方建筑这么近才算撤回来了

# Human：(组号, 兵种, 数量, 至少有几个才派, 对手不是, 对手只能是, 开局秒, 间隔秒, 强度限值, 逃跑比例, 逃跑人数)
HUMAN_GROUPS = (
    # 混编地面骚扰：原来只数步兵且必须有 7 个，火枪/骑士再多也永远显示“人数不够”。
    # 三人一队，主力至少留六名健康地面兵；优先找敌方分矿农民，180 秒后可再派。
    # ★ 09-23 照 AMAI 的打法频繁骚扰。AMAI Human：AddHarass(1, 2, uFOOTMAN)，
    #   Harass(1, …, TownCountDone(uFOOTMAN) > 6, …) —— 2 个人、出够 6 个以上就派。原来 3 人、要全军健康兵 > 8，
    #   对 AMAI 人族四局「人数不够」225~382 次、一次没出门（我方兵常年 6~10 个）。
    dict(id=1, type="hfoo", types=("hfoo", "hrif", "hkni"), qty=2, need=6, not_vs=None, only_vs=None,
         min_sec=240, cd_sec=180, limit=7, flee_pct=0.88, flee_n=1),
    dict(id=3, type="hgry", qty=2, need=4, not_vs=None, only_vs=None, min_sec=400, cd_sec=500, limit=5, flee_pct=0.75, flee_n=0),
    dict(id=4, type="Hpal", qty=1, need=0, not_vs=None, only_vs="undead", min_sec=250, cd_sec=400, limit=4, flee_pct=0.1, flee_n=0),
    # 第二路（09-23 起一队或两队分路骚扰）：兵够多才开，目标避开第一路那一片
    dict(id=6, type="hfoo", types=("hfoo", "hrif", "hkni"), qty=2, need=6, hard_need=HARASS_SECOND_NEED,
         not_vs=None, only_vs=None, min_sec=300, cd_sec=180, limit=7, flee_pct=0.88, flee_n=1),
)


# Undead（AMAI global_init_strategy AddHarass(1,1,uGHOUL)/(2,2,uGHOUL)/(5,2,uGARGOYLE)）
UNDEAD_GROUPS = (
    dict(id=1, type="ugho", qty=2, need=6, not_vs=None, only_vs=None, min_sec=250, cd_sec=400, limit=5, flee_pct=0.75, flee_n=0),
    dict(id=5, type="ugar", qty=2, need=4, not_vs=None, only_vs=None, min_sec=400, cd_sec=500, limit=5, flee_pct=0.75, flee_n=0),
)
# Night Elf（AMAI global_init_strategy AddHarass(1,2,uHUNTRESS)）
NIGHTELF_GROUPS = (
    dict(id=1, type="esen", qty=2, need=4, not_vs=None, only_vs=None, min_sec=300, cd_sec=400, limit=5, flee_pct=0.75, flee_n=0),
)
# Orc（AMAI global_init_strategy AddHarass(1,1,uGRUNT)/(3,2,uWYVERN)）
ORC_GROUPS = (
    dict(id=1, type="ogru", qty=2, need=6, not_vs=None, only_vs=None, min_sec=250, cd_sec=400, limit=5, flee_pct=0.75, flee_n=0),
    dict(id=3, type="owyv", qty=2, need=4, not_vs=None, only_vs=None, min_sec=400, cd_sec=500, limit=5, flee_pct=0.75, flee_n=0),
)
GROUPS = {"human": HUMAN_GROUPS, "undead": UNDEAD_GROUPS, "nightelf": NIGHTELF_GROUPS, "orc": ORC_GROUPS}


def want(cfg: dict, have: int, foe_race: str | None, t: float, last: float, busy: bool, aggr: float, roll: float,
         dual_line: bool = False, army_n: int | None = None, ahead: bool = False) -> str:
    """have = 能去骚扰的（这一组的兵种、血够）几个；army_n = 整支部队健康的作战单位几个（不给就用 have，老口径）。"""
    if busy and not dual_line:
        return "主力在打仗"
    if cfg["not_vs"] and foe_race == cfg["not_vs"]:
        return "对手种族不派"
    if cfg["only_vs"] and foe_race != cfg["only_vs"]:
        return "对手种族不派"
    need = min(cfg["need"], HARASS_AHEAD_NEED) if ahead else cfg["need"]
    n_army = army_n if army_n is not None else have
    # hard_need：第二路那条线的硬门槛，**不受「占优放宽」影响** —— 9 个兵分成两队各 2 个不是分路，是送两次
    if cfg.get("hard_need") is not None and n_army < cfg["hard_need"]:
        return "人数不够"
    if n_army <= need or have < cfg["qty"]:
        return "人数不够"
    if t < cfg["min_sec"]:
        return "还没到时间"
    if t - last < cfg["cd_sec"] * (HARASS_AHEAD_CD if ahead else 1.0):
        return "冷却中"
    # 中高进攻性且其余安全闸都通过时直接抓分矿机会；低进攻性仍沿用 AMAI 的概率节奏。优势时不抽签。
    if not ahead and aggr < 6 and roll > amai.harass_chance(aggr):
        return "没抽中"
    return "go"


def step_action(count: int, cur: float, start: float, tower_seen: bool, enemy_str: float, limit: float,
                flee_pct: float, flee_n: int, has_target: bool, target_dist: float) -> str:
    if count <= 0:
        return "end"
    if count <= flee_n or tower_seen or cur <= start * flee_pct:
        return "flee"
    # 小队以低损失换农民；对方接近本队 80% 战力就撤，不等到死人或掉至 75% 才反应。
    if enemy_str >= min(limit, max(2.0, cur * .8)):
        return "flee"
    if not has_target:
        return "end"
    return "move" if target_dist > 650 else "attack"


def _segment_distance(u, a, b) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    if length2 <= 1:
        return math.hypot(u.x - a.x, u.y - a.y)
    q = max(0.0, min(1.0, ((u.x - a.x) * dx + (u.y - a.y) * dy) / length2))
    return math.hypot(u.x - (a.x + q * dx), u.y - (a.y + q * dy))


def armed(world) -> list:
    """能打人的敌方建筑（塔 + 兽族地洞，tactics.ARMED_BUILDINGS）。"""
    return [b for b in world.enemy_buildings if b.hp > 0 and b.type in ARMED_BUILDINGS]


def armed_reach(b) -> float:
    return float(G.stat(b.type, "range", 800) or 800) + HARASS_TOWER_PAD


def under_fire(p, towers) -> object | None:
    return next((b for b in towers if math.hypot(b.x - p.x, b.y - p.y) <= armed_reach(b)), None)


def engaged_by(world, busy_xy) -> set:
    """★★ 09-23 佯攻战术：小分队打一路、主队打另一路 ——
    主队已经贴上去（busy_xy = 主力重心）时，DUAL_BUSY_R 以内的敌方作战单位正被主力缠着，
    它们守不了另一头的农民。不扣掉它们，佯攻小队永远判「没有安全的农民」（1-432 这条 319 次）。"""
    if busy_xy is None:
        return set()
    bx, by = busy_xy
    return {f.addr for f in world.enemy_fighters if math.hypot(f.x - bx, f.y - by) <= DUAL_BUSY_R}


def safe_peons(world, limit: float, origin=None, squad_str: float | None = None, hot=(), busy_xy=None) -> list:
    """全图数据挑目标：目标和已知路线都避塔（按每座塔的射程）、避达到撤退强度的守军；
    squad_str 给了就再避开「吃得掉这支小队」的敌方军团 HARASS_ARMY_CLEAR 以内；hot = [(x, y, 到期)] 拉扯过的地方。
    busy_xy = 我方主队的重心（佯攻）：被主队缠住的守军不算数（engaged_by）。"""
    out = []
    towers = armed(world)
    busy = engaged_by(world, busy_xy)
    free = [u for u in world.enemy_fighters if u.addr not in busy]
    big = []
    if squad_str is not None:
        line = max(2.0, 0.8 * squad_str)
        big = [a for a in getattr(world, "enemy_armies", ())
               if a.strength >= line and (busy_xy is None or math.hypot(a.x - busy_xy[0], a.y - busy_xy[1]) > DUAL_BUSY_R)]
    hot = [(x, y) for x, y, until in hot if until > world.t]
    for p in world.enemy_workers:
        if under_fire(p, towers) is not None:
            continue
        if any(math.hypot(a.x - p.x, a.y - p.y) <= HARASS_ARMY_CLEAR for a in big):
            continue                                         # 敌方主力就在边上：去了就是送
        if any(math.hypot(x - p.x, y - p.y) <= HARASS_ARMY_CLEAR for x, y in hot):
            continue
        guard = amai.strength([u for u in free if math.hypot(u.x - p.x, u.y - p.y) <= ATTACK_RADIUS], world.levels)
        if guard >= limit:
            continue
        if origin is not None:
            if any(_segment_distance(t, origin, p) <= armed_reach(t) for t in towers):
                continue
            route_guard = amai.strength([u for u in free
                                         if _segment_distance(u, origin, p) <= FLEE_RADIUS], world.levels)
            if route_guard >= limit:
                continue
        out.append(p)
    return out


@dataclass
class _P:
    x: float
    y: float


def _standoff(spot, towers, home, step: float = 150.0, max_d: float = 2500.0) -> tuple:
    """矿区 spot 往家的方向挪，挪到所有能打人的建筑射程外的第一个点（塔外等农民的位置）。"""
    sx, sy = spot
    d = math.hypot(home.x - sx, home.y - sy) or 1.0
    ux, uy = (home.x - sx) / d, (home.y - sy) / d
    k = 0.0
    while k <= max_d:
        p = _P(sx + ux * k, sy + uy * k)
        if under_fire(p, towers) is None:
            return p.x, p.y
        k += step
    return sx + ux * max_d, sy + uy * max_d


def _enemy_expansions(world) -> list:
    """在本模块内识别敌方分矿，避免和 tactics 形成循环导入。"""
    if len(world.enemy_halls) <= 1:
        return []
    main = max(world.enemy_halls,
               key=lambda h: sum(math.hypot(b.x-h.x, b.y-h.y) <= 1500 for b in world.enemy_buildings))
    return [h for h in world.enemy_halls if h.addr != main.addr]


def peon_target(world, candidates, origin, avoid=None):
    """优先杀分矿 1400 内的农民；再挑离敌方主力远的（每 1000 一档）；双线时离主力目标 avoid FEINT_AVOID_R 内的排后面；最后比路程。
    所有候选已通过塔和守军安全检查。"""
    if not candidates:
        return None
    expos = _enemy_expansions(world)
    foe = max(world.enemy_armies, key=lambda a: a.strength, default=None)

    def key(p):
        far = min(math.hypot(p.x - foe.x, p.y - foe.y), 6000.0) if foe is not None else 6000.0
        near_main = avoid is not None and math.hypot(p.x - avoid.x, p.y - avoid.y) <= FEINT_AVOID_R
        return (near_main, not any(math.hypot(p.x-h.x, p.y-h.y) <= 1400 for h in expos),
                -int(far // 1000), math.hypot(p.x-origin.x, p.y-origin.y))
    return min(candidates, key=key)


@dataclass
class Group:
    cfg: dict
    addrs: list
    start: float
    state: str = "go"               # go / kite（拉扯）/ flee（掉人、伤员回家）/ return（收队回家）
    target: int | None = None
    dual_line: bool = False
    born: float = 0.0               # 出发的游戏秒（HARASS_MAX_S）
    spot: tuple | None = None       # 这一趟骚扰的矿区（目标农民最近的敌方大厅）
    avoid: object = None            # 双线：主力在打的目标，佯攻离它远一点
    linger_since: float | None = None   # 到了矿区、一时没有塔外农民，从这一刻起在塔外等


def dual_line_ready(world, remaining: list, home, target) -> tuple[bool, str, offense.Assessment | None]:
    """拆队以后重新评估主攻，不能拿一次旧的“能打”判断透支主力。"""
    if target is None:
        return False, "没有主攻目标", None
    if not any(G.is_hero(u.type) for u in remaining):
        return False, "主力没有英雄", None
    strength = amai.strength(remaining, world.levels)
    if strength < DUAL_MIN_MAIN_STRENGTH:
        return False, "主力绝对战力不足", None
    ev = offense.assess(world, remaining, home, target)
    if not ev.good or ev.rhs < ev.lhs * DUAL_RESERVE_RATIO:
        return False, "拆队后主攻余量不足", ev
    return True, "ok", ev


@dataclass
class HarassManager:
    groups_cfg: tuple = HUMAN_GROUPS
    active: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)
    retry_at: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)
    hot: list = field(default_factory=list)       # [(x, y, 到期游戏秒)] 被压过来拉扯过的地方，见 HARASS_HOT_S

    @property
    def busy_addrs(self) -> set:
        return {a for g in self.active.values() for a in g.addrs}

    def _count(self, k: str) -> None:
        self.counts[k] = self.counts.get(k, 0) + 1

    def _other_spot(self, world):
        """已经在外面那支队伍打的那一片（09-23 两队分路骚扰：第二路别跟着去同一个地方）。"""
        for g in self.active.values():
            if g.state in ("go", "kite") and g.spot:
                return _Spot(float(g.spot[0]), float(g.spot[1]))
        tgt = next((c for g in self.active.values() for c in world.enemy_workers if c.addr == g.target), None)
        return _Spot(tgt.x, tgt.y) if tgt is not None else None

    def _group_step(self, world, home, g: Group, units: list) -> list[Intent]:
        """一支在外面的小队这一拍怎么走（HARASS_* / KITE_* 上面那段）：掉人回家 > 敌兵压过来拉扯 > 出了塔的射程 > 打塔外的农民 > 塔外等。"""
        cx, cy = centroid(units)
        here = _P(cx, cy)
        lead = units[0]
        cur = amai.strength(units, world.levels)
        foes = [u for u in world.enemy_fighters if u.hp > 0]
        press_line = min(g.cfg["limit"], max(2.0, cur * .8))
        if len(units) <= g.cfg["flee_n"] or cur <= g.start * g.cfg["flee_pct"]:
            g.state, g.target = "flee", None
            self._count("掉人·整队回家")
            self.hot.append((cx, cy, world.t + HARASS_HOT_S))
            return [Intent("move", units, home.x, home.y, why="骚扰掉了人，整队回家", control="skirmish")]
        # 按离小队**任何一个人**多近算（掉队的人会把重心拉到半路，按重心量会看不见贴着前面那个的敌人）
        near = amai.strength([f for f in foes if any(math.hypot(f.x - u.x, f.y - u.y) <= FLEE_RADIUS for u in units)], world.levels)
        chasing = amai.strength([f for f in foes if any(math.hypot(f.x - u.x, f.y - u.y) <= KITE_CLEAR_R for u in units)],
                                world.levels)
        if near >= press_line or (g.state == "kite" and chasing >= press_line):
            if g.state != "kite":
                self.notes.append(f"骚扰小队 {g.cfg['id']}：敌兵压过来（强度 {near:.0f} 对 {cur:.0f}），往家的方向拉开，不硬打")
                self.hot.append((cx, cy, world.t + HARASS_HOT_S))     # 这片有吃得掉我们的人：HARASS_HOT_S 内不再来
            g.state, g.target = "kite", None
            self._count("拉扯")
            d = math.hypot(home.x - cx, home.y - cy) or 1.0
            step = min(KITE_STEP, d)
            return [Intent("move", units, cx + (home.x - cx) / d * step, cy + (home.y - cy) / d * step,
                           why="骚扰拉扯：敌兵压过来，往家的方向拉开，不硬打", control="skirmish")]
        if g.state == "kite":
            spot_hot = g.spot is not None and any(until > world.t and math.hypot(x - g.spot[0], y - g.spot[1]) <= HARASS_ARMY_CLEAR
                                                  for x, y, until in self.hot)
            if world.t - g.born > HARASS_MAX_S or spot_hot:
                g.state = "return"
                self._count("拉扯·拉开了·" + ("那片有敌方主力·收队" if spot_hot else "到时收队"))
                return [Intent("move", units, home.x, home.y, control="skirmish",
                               why="骚扰：那片有敌方主力，不回去送，收队" if spot_hot else "骚扰到时间了，收队")]
            g.state, g.linger_since = "go", None
            self._count("拉扯·拉开了·回去接着骚扰")
        towers = armed(world)
        hot = [(u, under_fire(u, towers)) for u in units]
        hot = [(u, b) for u, b in hot if b is not None]
        if hot:
            self._count("避塔")
            out = []
            for u, b in hot:
                r = armed_reach(b) + 100.0
                d = math.hypot(u.x - b.x, u.y - b.y) or 1.0
                out.append(Intent("move", [u], b.x + (u.x - b.x) / d * r, b.y + (u.y - b.y) / d * r,
                                  why="骚扰：别站在塔的射程里", control="skirmish"))
            return out
        cands = safe_peons(world, g.cfg["limit"], lead, squad_str=cur, hot=self.hot)
        target = next((u for u in cands if u.addr == g.target), None)
        if target is None:
            target = peon_target(world, cands, lead, avoid=g.avoid)
            g.target = target.addr if target else None
        if target is not None:
            g.linger_since = None
            tdist = math.hypot(target.x - lead.x, target.y - lead.y)
            act = step_action(len(units), cur, g.start, False, 0.0, g.cfg["limit"], g.cfg["flee_pct"], g.cfg["flee_n"], True, tdist)
            self._count(act)
            # control=hold（58 > 毫秒层补攻击令 55）：1-329 走在路上的骚扰兵被补攻击令派去打对方大厅边的单位
            if act == "move":
                return [Intent("move", units, target.x, target.y, target, "骚扰：走向农民", control="hold")]
            return [Intent("attack", units, target.x, target.y, target, "骚扰：打农民")]
        if g.linger_since is None:
            g.linger_since = world.t
        if world.t - g.born > HARASS_MAX_S or world.t - g.linger_since > HARASS_LINGER_S or g.spot is None:
            g.state, g.target = "return", None
            self._count("没有安全目标·收队")
            return [Intent("move", units, home.x, home.y, why="骚扰：塔外一直没有农民，收队", control="skirmish")]
        sx, sy = _standoff(g.spot, towers, home)
        line = max(2.0, 0.8 * cur)
        if any(a.strength >= line and math.hypot(a.x - sx, a.y - sy) <= HARASS_ARMY_CLEAR for a in getattr(world, "enemy_armies", ())):
            g.state, g.target = "return", None
            self._count("塔外等的地方有敌方主力·收队")
            return [Intent("move", units, home.x, home.y, why="骚扰：那片有敌方主力，不在那儿等，收队", control="skirmish")]
        self._count("塔外等农民")
        if math.hypot(sx - cx, sy - cy) <= 300:
            return []
        return [Intent("move", units, sx, sy, why="骚扰：到矿区塔外等农民出来", control="hold")]

    def end(self, gid: int, why: str) -> None:
        if self.active.pop(gid, None) is not None:
            self._count(f"结束·{why}")
            self.notes.append(f"骚扰小队 {gid} 结束：{why}")

    def step(self, world, home, busy: bool, town_threatened: bool, excluded=(), dual_target=None,
             recall: bool = False, idle: bool = False) -> list[Intent]:
        """idle（09-22 晚）：大部队这一拍在空档（tactics._idle_plan：没营地、不出击）—— 骚扰按占优的规矩放行
        （主力留人 HARASS_AHEAD_NEED、冷却 × HARASS_AHEAD_CD、不抽签）。1-410 14 分：部队一直待在家里，空档时至少要动起来，分路去骚扰。"""
        intents: list[Intent] = []
        if home is None:
            return intents
        if town_threatened:
            for gid in list(self.active):
                self.end(gid, "家里受威胁")
        by_addr = {u.addr: u for u in world.mine if u.addr not in excluded}
        alive = {u.addr for u in world.mine if u.hp > 0}
        for gid, g in list(self.active.items()):
            # 成员按「还活着」算：被毫秒层接管（保命拉走）的人还是这支队的，保护期过了接着带回家（见 HARASS_ARMY_CLEAR 上面那段）
            g.addrs = [a for a in g.addrs if a in alive]
            if not g.addrs:
                self.end(gid, "人没了")
                continue
            units = [by_addr[a] for a in g.addrs if a in by_addr and by_addr[a].hp > 0]
            if not units:
                self._count("队员在毫秒层手里·等它放手")
                continue
            hurt = [u for u in units if u.hp < max(HURT_FRAC * u.hp_max, HURT_MIN)]
            if hurt or len(units) < len(g.addrs):
                # 不把伤员一个人丢在撤退路上，也不让剩下两人继续赌命；整队带伤员回撤。有人被毫秒层拉走 = 已经打起来了，同样撤
                g.state, g.target = "flee", None
                self._count("出现伤员·整队撤退" if hurt else "有人被毫秒层拉走·整队撤退")
                intents.append(Intent("move", units, home.x, home.y, control="skirmish",
                                      why="骚扰出现伤员，整队低损撤退" if hurt else "骚扰有人被拉去保命，整队撤退"))
                continue
            lead = units[0]
            if recall and g.state != "return":
                g.state, g.target = "return", None
                self._count("主力撤退·召回")
                self.notes.append(f"骚扰小队 {gid} 收队：主力撤退或整队治疗")
            if g.state in ("flee", "return"):
                # 撤到我方地盘才算撤回来 —— 原来「身边暂时没敌人」就结束，人还在对方家门口，没人管了
                home_land = any(math.hypot(b.x - lead.x, b.y - lead.y) <= HOME_LAND_R for b in world.my_buildings)
                if home_land or math.hypot(lead.x - home.x, lead.y - home.y) <= 1000:
                    self.end(gid, "逃回来了")
                else:
                    why = "双线收队，回主基地重整" if g.state == "return" else "骚扰撤退"
                    intents.append(Intent("move", units, home.x, home.y, why=why, control="skirmish"))
                continue
            intents += self._group_step(world, home, g, units)
        if town_threatened:
            return intents
        taken = self.busy_addrs
        ahead = world.own_strength > 0 and world.own_strength >= world.foe_strength * HARASS_AHEAD   # 对面一个兵都看不见也算
        if idle and not ahead:
            self._count("主力空闲·按占优放行")
            ahead = True
        army_ids = {u.addr for u in world.my_army}
        for cfg in self.groups_cfg:
            if cfg["id"] in self.active:
                continue
            if world.t < self.retry_at.get(cfg["id"], -1e9):
                continue
            # AMAI 原表有“圣骑士单人骚扰亡灵”；这会直接复现英雄脱队送死。英雄骚扰必须另做带护卫状态机。
            if G.is_hero(cfg["type"]):
                self._count("不派英雄单独骚扰")
                continue
            dual_line = busy and dual_target is not None and not G.is_hero(cfg["type"])
            allowed_types = cfg.get("types", (cfg["type"],))
            pool = [u for u in by_addr.values() if u.type in allowed_types and u.hp >= 0.85 * u.hp_max and u.addr not in taken]
            if dual_line:
                # 只拆尚未接战的后备兵；不能把正在顶住敌人的前排突然抽走。
                hazards = world.enemy_fighters + world.enemy_towers + world.creeps
                pool = [u for u in pool if not any(math.hypot(u.x - f.x, u.y - f.y) <= DUAL_CONTACT_R for f in hazards)]
            army_n = sum(1 for a, u in by_addr.items() if a in army_ids and a not in taken and u.hp >= 0.85 * u.hp_max)
            res = want(cfg, len(pool), world.foe_race, world.t, self.last.get(cfg["id"], -1e9), busy, world.aggression,
                       self.rng.uniform(0, 100), dual_line=dual_line, army_n=army_n, ahead=ahead)
            if res == "没抽中":
                self.retry_at[cfg["id"]] = world.t + 20.0
                self._count("没抽中")
                continue
            if res != "go":
                self._count(res)
                continue
            # ★★ 09-23 不再只派一两个兵骚扰，改成一队或两队分路 ——
            #   主力闲着的时候按可用兵力放大这一队（squad_size）；AMAI 的 2 人是「主力在忙」时的规格
            n_take = squad_size(cfg, army_n, idle and not dual_line)
            if n_take > cfg["qty"]:
                self._count(f"骚扰·主力闲着·这队带 {n_take} 个")
            pick = sorted(pool, key=lambda u: math.hypot(u.x - home.x, u.y - home.y))[:n_take]
            # ★★ 09-23 佯攻战术：小分队打一路、主队打另一路 ——
            #   主队已经打到目标跟前（DUAL_BUSY_NEAR）时，缠着它的守军守不了另一头（engaged_by）。
            #   1-432：主队整局没出门 -> busy 恒假 -> 双线一次没走；而单独骚扰被「没有安全的农民」挡了 319 次
            busy_xy = None
            if dual_line:
                mates = [u for u in world.my_army + world.my_heroes
                         if u.addr not in taken and u.addr not in {p.addr for p in pick} and u.addr not in set(excluded)]
                mf = main_force(mates, world.me, world.levels)
                if mf is not None and math.hypot(mf.x - dual_target.x, mf.y - dual_target.y) <= DUAL_BUSY_NEAR:
                    busy_xy = (mf.x, mf.y)
                    self._count("佯攻·主队已经缠住守军")
            targets = safe_peons(world, cfg["limit"], home, squad_str=amai.strength(pick, world.levels), hot=self.hot,
                                 busy_xy=busy_xy)
            if not targets:
                self._count("没有安全的农民")
                continue
            if dual_line:
                reserved = taken | {u.addr for u in pick} | set(excluded)
                remaining = [u for u in world.my_army + world.my_heroes if u.addr not in reserved and u.hp > 0]
                ok, why, ev = dual_line_ready(world, remaining, home, dual_target)
                if not ok:
                    self._count(f"双线不拆·{why}")
                    continue
            start = amai.strength(pick, world.levels)
            self.last[cfg["id"]] = world.t
            # 两队分路：第二路避开已经出去那队那一片（peon_target 把 FEINT_AVOID_R 内的排后面）
            avoid = dual_target if dual_line else self._other_spot(world)
            target = peon_target(world, targets, pick[0], avoid=avoid)
            hall = min(world.enemy_halls, key=lambda h: math.hypot(h.x - target.x, h.y - target.y), default=None)
            spot = ((hall.x, hall.y) if hall is not None and math.hypot(hall.x - target.x, hall.y - target.y) <= 2500
                    else (target.x, target.y))
            self.active[cfg["id"]] = Group(cfg, [u.addr for u in pick], start, target=target.addr, dual_line=dual_line,
                                           born=world.t, spot=spot, avoid=avoid)
            intent_why = "双线骚扰：走向农民" if dual_line else "骚扰：走向农民"
            intents.append(Intent("move", pick, target.x, target.y, target, intent_why))
            taken |= {u.addr for u in pick}
            self._count(f"{'双线派出' if dual_line else '派出'}·{cfg['type']}")
            if ahead:
                self._count("派出·优势")
            reserve = f"，主力余量 {ev.rhs:.1f}/{ev.lhs:.1f}" if dual_line and ev else ""
            reserve += f"，优势 {world.own_strength:.0f}/{world.foe_strength:.0f}" if ahead else ""
            self.notes.append(f"{'双线' if dual_line else '骚扰'}小队 {cfg['id']} 出发：{len(pick)} 个 {cfg['type']}"
                              f"（强度 {start:.1f}、进攻性 {world.aggression:.1f}{reserve}）")
        return intents

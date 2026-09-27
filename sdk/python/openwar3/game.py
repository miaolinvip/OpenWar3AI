# -*- coding: utf-8 -*-
"""Game：OpenWar3 给 bot 作者的唯一入口（W3P v2）。

    from openwar3 import Game
    g = Game(inst=5)                 # 连 5 号实例（或 Game(pid=...)；都不给就用环境变量 WAR3_PID）
    s = g.snapshot()                 # 一份完整的世界（零等待，运行时推送）
    for w in g.idle_workers():
        g.gather(w, g.nearest(g.gold_mines(), w))

这一层**不包含任何游戏内存地址**：
  * 读：全部来自运行时推送的世界状态（w3world）—— 单位的血/蓝/订单/订单目标/任务目标/英雄等级/技能冷却/buff/背包、
    全部玩家的资源、地上物品、树、游戏时钟，再加一条事件流。读一次约 0.4 ms，不往游戏线程排队。
  * 写：全部是 W3P 语义命令（w3fast.command）—— 走哪条引擎路径由运行时决定，每条命令返回一张回执（Receipt）：
    接下 / 被拒（带原因：人口不够、金不够、目标看不见……）/ 单位不存在 / 不是你的。
  * 游戏控制（倍速、消息、气泡、结束）走控制通道的动作号，也是协议层面的，不是地址。

还有几条规矩（都是这个项目栽过的坑）：
  * 所有命令先过统一仲裁表（w3claim），默认优先级 50；保命/施法类更高的层握着单位时命令返回 status=held。
  * 读不到就是 None，**不要当成 0**。
  * 回执"接下"= 引擎接受了这条命令，不等于做成了（地基可能出不来、技能可能被打断）—— 效果看快照和事件。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from functools import lru_cache

import w3paths
from w3world import parse_chat

from .catalog import api
from .orders import (ATTACK, HARVEST, HOLD, MOVE, REPAIR, SMART, STOP, TOWNBELL_OFF, TOWNBELL_ON, ORDER, rawcode)
from .jass import ALLIANCE as _ALLY

WORKERS = frozenset({"hpea", "opeo", "uaco", "ewsp"})
GOLD_MINES = frozenset({"ngol", "egol", "ugol"})
HALLS = frozenset({"htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"})
CREEP_OWNER = 12
HELD = 98            # 回执状态：单位被更高优先级的层握着（仲裁表），这条没发出去
CANCELLED = 97       # 回执状态：批量块里抛了异常，整批没发出去


class Pending:
    """批量块（with g.batch()）里下的一条命令。块结束、整批提交之后它就代表那张 Receipt
    （.ok / .reason / .verdict … 都转给 Receipt）；提交之前读它会抛 RuntimeError —— 回执还不存在。"""
    __slots__ = ("receipt", "_after")

    def __init__(self, receipt=None):
        self.receipt = receipt
        self._after = None

    def _resolve(self, r) -> None:
        self.receipt = r
        if self._after is not None:
            self._after(r)

    @property
    def done(self) -> bool:
        return self.receipt is not None

    def __getattr__(self, k):
        r = object.__getattribute__(self, "receipt")
        if r is None:
            raise RuntimeError("批量命令的回执要等 with g.batch() 块结束才有")
        return getattr(r, k)

    def __bool__(self) -> bool:
        return bool(self.__getattr__("ok"))

    def __repr__(self) -> str:
        return f"Pending({self.receipt!r})" if self.receipt is not None else "Pending(未提交)"


class Batch:
    """g.batch() 返回的上下文。块结束时把攒下的命令一次交给运行时（同一帧执行，只等一次游戏线程）。
    .receipts：提交后每条的回执（按下令顺序）；.sent：发出去几条；.wait_ms：这一批等了多久。"""

    def __init__(self, game):
        self.game = game
        self.items: list = []            # (Call, Pending, 句柄)
        self.receipts: list = []
        self.sent = 0
        self.wait_ms = 0.0

    def __enter__(self):
        g = self.game
        if g._batch is not None:         # 嵌套：并进外层那一批
            g._batch_depth += 1
            return g._batch
        g._batch, g._batch_depth = self, 1
        return self

    def __exit__(self, exc_type, exc, tb):
        g = self.game
        g._batch_depth -= 1
        if g._batch_depth > 0:
            return False
        g._batch = None
        if exc_type is not None:         # 块里出错：整批作废（半截命令比不发更危险），握着的单位放回去
            for _c, p, _h, addr in self.items:
                try:
                    g.claims.give_back(addr)
                except Exception:  # noqa: BLE001
                    pass
                p._resolve(g._w3fast.Receipt(0, CANCELLED, 0, None, 0, 0, (0,) * 8))
            return False
        self.flush()
        return False

    def flush(self) -> list:
        g = self.game
        items, self.items = self.items, []
        t0 = time.perf_counter()
        rs = g.lane.commands([c for c, _p, _h, _a in items]) if items else []
        self.wait_ms = (time.perf_counter() - t0) * 1000.0
        now = time.monotonic()
        for (_c, p, h, _a), r in zip(items, rs):
            if r.ok and r.order_after and h is not None:
                g._recent_orders[h] = (r.order_after, now)
            p._resolve(r)
        self.sent += len(items)
        self.receipts += rs
        g.last_receipts = list(rs)
        return rs


@lru_cache(maxsize=1)
def unit_table() -> dict:
    """四字码 -> {hp, gold, lumber, food, spd, range, atk, ...}（data/game/units.json，从本机游戏提取）。"""
    try:
        with open(w3paths.UNITS_JSON, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _buff_table() -> dict:
    """buff 码 -> [行]（data/game/buffs.json）。"""
    try:
        with open(w3paths.game_data("buffs.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _find_pid(inst: int | None) -> int | None:
    if inst is not None:
        try:
            text = open(os.path.join(w3paths.FARM_DIR, f"inst{inst}.out"), "rb").read().decode("utf-8", "replace")
            m = re.findall(r"WAR3_PID=(\d+)", text.replace("\0", ""))
            return int(m[-1]) if m else None
        except OSError:
            return None
    env = os.environ.get("WAR3_PID")
    return int(env) if env and env.isdigit() else None


class Game:
    """一局游戏的全部读写接口。一个 bot 进程开一个。

    player=N（P5 车道角色）：这个 Game 以 N 号玩家的身份下令 —— 运行时只让它指挥 N 号的单位（别人的单位 = not_owner），
    也不许用 JASS 通道；me() / snapshot().me 都是 N。两个 bot 各开一个 player 车道，就是 AI 对 AI 的基础。
    fair=True：快照只留「N 号自己的 + N 号此刻看得见的」单位（运行时每次发布按 IsVisibleToPlayer 算的 visible_mask）——
    没有迷雾作弊的公平模式。⚠ 本机模式下角色是自己声明的（约定，不是安全边界）；对战平台由裁判进程建车道。"""

    _batch = None          # 类级默认：测试里的替身不走 __init__ 也能用
    _batch_depth = 0
    _event_backlog: tuple = ()
    _ui = None

    def __init__(self, inst: int | None = None, pid: int | None = None, owner: str = "bot", priority: int = 50,
                 fast: bool = True, player: int | None = None, fair: bool = False, observer: bool = False):
        pid = pid or _find_pid(inst)
        if not pid:
            raise RuntimeError("找不到游戏 pid：给 Game(inst=N) 或 Game(pid=...)，或设环境变量 WAR3_PID")
        self.pid = pid
        self.inst = inst
        os.environ["WAR3_PID"] = str(pid)
        import w3claim
        import w3fast
        import w3world
        self._w3fast = w3fast
        self.world = w3world.World(pid)
        self.player = player
        self.fair = fair
        if observer:
            # 观众（网关 / MCP 的 observer）：运行时这一层也只放行查询、镜头、界面 —— 不只靠上层白名单挡着
            self.lane = w3fast.FastLane(pid, role=w3fast.ROLE_OBSERVER)
        elif player is None:
            self.lane = w3fast.FastLane(pid)
        else:
            self.lane = w3fast.FastLane(pid, role=w3fast.ROLE_PLAYER, player=int(player))
        self.claims = w3claim.Claims(owner, pid)
        self.priority = priority
        self._ch = None                            # 控制通道：只用来发动作号（倍速/消息/结束），懒创建
        self._snap = None
        self._snap_at = 0.0
        self._bad_spots: dict[str, dict] = {}      # build_near：被引擎拒过的点 -> 到期时刻（按建筑类型）
        self._pending_builds: dict[str, dict] = {}  # build_near：每种建筑正在进行的那一次（工人还在路上）
        self._recent_orders: dict[tuple, tuple] = {}  # 句柄 -> (刚被接下的订单, 时刻)：快照追上之前以它为准
        self.last_receipts: list = []
        self._batch: Batch | None = None            # with g.batch() 里攒着的那一批
        self._batch_depth = 0
        self._event_backlog: list = []              # g.ui 等待（choice().wait / pick_point）时读到的别的事件，下一次 events() 先给
        self._ui = None

    # ── 连接 ──────────────────────────────────────────────────
    @api("meta", "verified", "世界块 + 快车道 + 仲裁表")
    def status(self) -> dict:
        """连接状态：pid、世界发布（周期、采集耗时）、快车道计数。"""
        s = self.snapshot()
        return {"pid": self.pid, "publishes": s.publish_count if s else None, "periodMs": s.period_ms if s else None,
                "collectUs": s.cost_us if s else None, "collectMaxUs": s.cost_max_us if s else None,
                "fastLane": self.lane.stats(), "eventsDropped": self.world.dropped}

    def _control(self):
        if self._ch is None:
            from w3chan import Channel
            self._ch = Channel(self.pid)
        return self._ch

    def close(self) -> None:
        """放掉这个 Game 占的东西：自己画的画板项、热键和鼠标开关（别人的不动）、快车道车道（运行时一共 16 条）、
        共享内存映射。网关每个连接、MCP 换局时都要调；以前没有这个方法，全靠垃圾回收，车道迟迟不还。可以用 with Game(...) as g。"""
        ui = self.__dict__.get("_ui")
        if ui is not None:
            try:
                ui.close()
            except Exception:  # noqa: BLE001 - 游戏已经没了也要把本地的放掉
                pass
        canvas = self.__dict__.get("_canvas")
        if canvas is not None:
            try:
                canvas.clear()
            except Exception:  # noqa: BLE001
                pass
            for blk in (getattr(canvas, "block", None), canvas.__dict__.get("_input_client")):
                if blk is not None:
                    try:
                        if hasattr(blk, "detach") and blk is canvas.__dict__.get("_input_client"):
                            blk.detach()
                        blk.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._canvas = None
        for name in ("lane", "world"):
            obj = self.__dict__.get(name)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ── 观察：快照 ────────────────────────────────────────────
    @api("observe", "verified", "W3P 世界块 Local\\War3World_<pid>（运行时每 50 ms 推送，seqlock）", "推送快照")
    def snapshot(self, max_age: float = 0.05):
        """整张地图的完整状态（WorldState）：.units .players .items .clock .me，max_age 秒内重复调用返回同一份。
        ⚠ 进了金矿的工人不在表里；默认全图可见（锁步模型本地什么都有），Game(fair=True) 才按视野过滤。"""
        now = time.monotonic()
        if self._snap is None or now - self._snap_at > max_age:
            s = self.world.read()
            if s is not None:
                self._snap, self._snap_at = self._view(s), now
                self._remember(s)
        return self._snap

    def _view(self, s):
        """player 车道：me 换成那个玩家；fair：只留它自己的 + 它此刻看得见的（单位 / 生产表 / 地上物品）。"""
        if self.player is None and not self.fair:
            return s
        import dataclasses
        p = s.me if self.player is None else int(self.player)
        units, items, prods = s.units, s.items, s.production
        if self.fair:
            units = [u for u in s.units if u.owner == p or u.visible_to(p)]
            seen = {u.handle for u in units}
            prods = [pr for pr in s.production if pr.building in seen]
            items = self._items_in_sight(s, p)
        return dataclasses.replace(s, me=p, units=units, by_handle={u.handle: u for u in units}, items=items,
                                   production=prods)

    def _items_in_sight(self, s, p) -> list:
        """地上物品只留在 p 的单位视野里的（白天/夜里用各自的视野半径；数据表缺了按 800）。"""
        from . import combat
        night = s.is_night
        eyes = [(u.x, u.y, combat.sight(u.type, bool(night))) for u in s.units if u.owner == p and u.alive]
        return [it for it in s.items if any((it.x - x) ** 2 + (it.y - y) ** 2 <= r * r for x, y, r in eyes)]

    # ── 敌人最后一次看见（职业选手脑子里的那张图）───────────────────
    def _remember(self, s) -> None:
        p = s.me if self.player is None else int(self.player)
        if not hasattr(self, "_last_seen"):
            self._last_seen = {}
        if getattr(self, "_seen_epoch", None) != s.epoch:
            self._last_seen, self._seen_epoch = {}, s.epoch        # 新的一局
        for u in s.units:
            if u.owner == p or u.owner >= 12 and u.owner != CREEP_OWNER:
                continue
            if not u.alive:
                self._last_seen.pop(u.handle, None)                 # 看见它死了：忘掉
            elif u.visible_to(p):
                self._last_seen[u.handle] = (u, s.clock)

    @api("observe", "verified", "推送快照的 visibleTo（每次刷新快照时记下看得见的敌方/野怪单位）", "推送快照")
    def last_seen(self, owner: str | int = "enemy", max_age: float | None = None) -> list:
        """最后一次看见的敌方（或 'creep' 野怪、或某个玩家号）单位：[(单位当时的样子, 当时的游戏时钟, 过去了几秒)]，新的在前。
        看见它死了就从表里删掉。公平模式和普通模式都按"我方此刻看得见"来记 —— 这就是玩家脑子里的那张图：
        侦察到的兵力、上次看见对方英雄在哪、对面分矿什么时候开的。max_age 只要这么多游戏秒以内的。"""
        s = self.snapshot()
        if not s:
            return []
        now = s.clock
        out = []
        for u, t in getattr(self, "_last_seen", {}).values():
            if owner == "enemy" and u.owner == CREEP_OWNER:
                continue
            if owner == "creep" and u.owner != CREEP_OWNER:
                continue
            if isinstance(owner, int) and u.owner != owner:
                continue
            if max_age is not None and now - t > max_age:
                continue
            out.append((u, t, now - t))
        out.sort(key=lambda r: -r[1])
        return out

    @api("observe", "verified", "W3P 地图块 Local\\War3Map_<pid>（开局后运行时分批算，IsTerrainPathable 行走/建造）", "推送快照")
    def map(self):
        """这一局的地形表 MapInfo：.walkable(x,y) .buildable(x,y) .at(x,y) .bounds（可玩区）.starts（出生点）.cells（bit0 不能走、bit1 不能盖）。
        开局后要几秒才算好，没算好返回 None。树不在里面（用 trees()）。"""
        return self.world.map()

    @api("observe", "verified", "世界块头", "推送快照")
    def me(self) -> int | None:
        """我是几号玩家（0~11）。"""
        s = self.snapshot()
        return s.me if s else None

    @api("observe", "verified", "世界块 players[16]", "推送快照")
    def resources(self, player: int | None = None) -> dict | None:
        """{'gold','lumber','food_used','food_cap','gold_gathered','lumber_gathered'}；player 默认我方，任何玩家都读得到。
        读不到返回 None，别当成 0。"""
        s = self.snapshot()
        if not s:
            return None
        p = s.players[s.me if player is None else player]
        if not p.known:
            return None
        return {"gold": p.gold, "lumber": p.lumber, "food_used": p.food_used, "food_cap": p.food_cap,
                "gold_gathered": p.gold_gathered, "lumber_gathered": p.lumber_gathered}

    @api("observe", "verified", "世界块 players[16]", "推送快照")
    def players(self) -> list:
        """全部 16 个玩家槽：Player(id, gold, lumber, food_used, food_cap, gold_gathered, lumber_gathered, race, known)。"""
        s = self.snapshot()
        return s.players if s else []

    @api("observe", "verified", "世界块 units[]", "推送快照")
    def units(self, owner: str | int = "all", types=None, alive: bool = True) -> list:
        """按主人/类型筛单位。owner：'me' / 'enemy' / 'creep' / 'all' / 玩家号。types：四字码集合。"""
        s = self.snapshot()
        if not s:
            return []
        out = []
        for u in s.units:
            if alive and not u.alive:
                continue
            if owner == "me" and u.owner != s.me:
                continue
            if owner == "enemy" and (u.owner == s.me or u.owner >= 12):
                continue
            if owner == "creep" and u.owner != CREEP_OWNER:
                continue
            if isinstance(owner, int) and u.owner != owner:
                continue
            if types is not None and u.type not in types:
                continue
            out.append(u)
        return out

    @api("observe", "verified", "世界块 by_handle", "推送快照")
    def unit(self, handle) -> object | None:
        """按句柄对 (lo, hi) 找单位（订单目标、任务目标、事件给的都是句柄对）。"""
        s = self.snapshot()
        return s.unit_by_handle(handle) if s else None

    @api("observe", "verified", "快照 + units.json（spd==0 = 建筑）", "推送快照")
    def is_building(self, u) -> bool:
        """是不是建筑（含塔）。按单位表移动速度 0 判；亡灵大厅的占地是 0，别用占地判。"""
        row = unit_table().get(u.type)
        return bool(row) and not row.get("spd") and u.type not in GOLD_MINES

    def my_units(self, types=None) -> list:
        return self.units("me", types)

    @api("observe", "verified", "推送快照", "推送快照")
    def my_workers(self) -> list:
        """我方工人（农民/苦工/侍僧/小精灵）。"""
        return self.units("me", WORKERS)

    @api("observe", "verified", "推送快照（订单槽 + 任务槽）", "推送快照")
    def idle_workers(self) -> list:
        """没活干的工人：没有订单、也没有任务（这一拍刚被你派了活的不算）。
        ⚠ 给有任务的工人重下采集令会打断采集周期（收入归零）。"""
        return [u for u in self.my_workers() if (self.order_of(u) or 0) == 0 and u.has_task is False]

    @api("observe", "verified", "推送快照", "推送快照")
    def my_heroes(self) -> list:
        """我方活着的英雄（死了的在祭坛复活表里，见 revive）。"""
        return [u for u in self.units("me") if u.is_hero]

    @api("observe", "verified", "推送快照 + units.json", "推送快照")
    def my_army(self) -> list:
        """我方作战单位：不是工人、不是建筑。"""
        return [u for u in self.units("me") if u.type not in WORKERS and not self.is_building(u)]

    @api("observe", "verified", "推送快照", "推送快照")
    def my_buildings(self, types=None) -> list:
        """我方建筑（含塔、在建的地基）；types 可以只要某几种，如 {'hbar'}。"""
        return [u for u in self.units("me", types) if self.is_building(u)]

    @api("observe", "verified", "推送快照（订单 = 建筑四字码，或施工令/修理令）", "推送快照")
    def is_constructing(self, worker) -> bool:
        """这个工人是不是正在盖房子（或正走去盖 / 在帮着修；含这一拍刚派的）。挑建造工时要跳过它，否则上一座地基会停工。"""
        o = self.order_of(worker) or 0
        return o in (0x000D001B, 0x000D001C, REPAIR) or o >= 0x20202020

    @api("observe", "verified", "推送快照（地基的血从很低一路涨到满）", "推送快照")
    def under_construction(self, building) -> bool:
        """这座建筑还没盖完（血没满）。⚠ 被打残的建筑也不满血 —— 开局判断够用，打起来之后要结合时间看。"""
        return building.hp < building.hp_max

    @api("observe", "verified", "推送快照（ngol/egol/ugol）", "推送快照")
    def gold_mines(self) -> list:
        """地图上的金矿。⚠ 暗夜缠绕金矿和中立金矿同一坐标各有一个单位，采集要派到自己那一个。"""
        return self.units("all", GOLD_MINES)

    @api("observe", "verified", "推送快照", "推送快照")
    def enemies(self, fighters_only: bool = False) -> list:
        """敌方玩家的单位（不含野怪）。fighters_only：去掉工人和建筑。"""
        us = self.units("enemy")
        if fighters_only:
            us = [u for u in us if u.type not in WORKERS and not self.is_building(u)]
        return us

    @api("observe", "verified", "推送快照（owner 12 = 中立敌对）", "推送快照")
    def creeps(self) -> list:
        """野怪（中立敌对）。⚠ 夜里视野变短，远处营地进迷雾后对它的目标命令会被拒（原因码 1001）。"""
        return self.units("creep")

    @staticmethod
    def dist(a, b) -> float:
        ax, ay = (a.x, a.y) if hasattr(a, "x") else a
        bx, by = (b.x, b.y) if hasattr(b, "x") else b
        return math.hypot(ax - bx, ay - by)

    @api("meta", "verified", "纯计算")
    def nearest(self, candidates, to):
        """离 to（单位或 (x,y)）最近的一个；没有候选返回 None。"""
        return min(candidates, key=lambda c: self.dist(c, to), default=None)

    # ── 观察：单位细节（都在推送快照里）──────────────────────────
    @api("observe", "verified", "世界块单位 hp/hpMax/mana/manaMax", "推送快照")
    def life_mana(self, u) -> dict | None:
        """{'hp','hp_max','mana','mana_max'}（浮点，引擎原值）。u 用快照里拿到的单位即可（会换成最新一份）。"""
        v = self._fresh(u)
        return None if v is None else {"hp": v.hp, "hp_max": v.hp_max, "mana": v.mana, "mana_max": v.mana_max}

    @api("observe", "verified", "世界块单位 level/xp/skillPoints", "推送快照")
    def hero_info(self, hero) -> dict | None:
        """{'level','xp','skill_points'}。"""
        v = self._fresh(hero)
        return None if v is None or not v.is_hero else {"level": v.level, "xp": v.xp, "skill_points": v.skill_points}

    @api("observe", "verified", "世界块细节：技能（代码/等级/标志/剩余冷却）", "推送快照")
    def abilities(self, u) -> list:
        """[{code, level, cooldown, flags}]；buff 在 buffs(u)。只有"有细节"的单位才有（英雄 > 玩家单位 > 野怪，最多 256 个）。"""
        v = self._fresh(u)
        return [{"code": a.code, "level": a.level, "cooldown": a.cooldown, "flags": a.flags} for a in (v.abilities if v else [])]

    @api("observe", "verified", "世界块细节：B 开头的技能对象", "推送快照")
    def buffs(self, u) -> list:
        """单位身上的 buff 码（如 'BHds' 神圣护盾、'Bslo' 减速）。码对应什么效果见 data/game/buffs.json。"""
        v = self._fresh(u)
        return list(v.buffs) if v else []

    @api("observe", "verified", "世界块细节：技能剩余冷却（技能计时器）", "推送快照")
    def cooldown(self, u, ability: str) -> float | None:
        """这个技能还要冷却几秒（游戏秒）；0 = 能放；没有这个技能（或这个单位没细节）返回 None。"""
        v = self._fresh(u)
        a = v.ability(ability) if v else None
        return None if a is None else a.cooldown

    @api("observe", "verified", "世界块细节：背包 6 格", "推送快照")
    def inventory(self, hero) -> list | None:
        """6 格物品四字码（空格为 None）；没有背包返回 None。"""
        v = self._fresh(hero)
        if v is None or not v.has_detail or not any(a.code == "AInv" for a in v.abilities):
            return None
        return list(v.items)

    @api("observe", "verified", "世界块单位 order / 订单目标 / 订单目标点", "推送快照")
    def current_order(self, u) -> dict | None:
        """{'order','target','x','y'}：单位手上这条订单（order 是 0x000D00xx 或建筑四字码，0 = 空闲）。
        target 是句柄对，用 g.unit(target) 换成单位。"""
        v = self._fresh(u)
        if v is None or v.order is None:
            return None
        return {"order": v.order, "target": v.order_target, "x": v.order_x, "y": v.order_y}

    @api("observe", "verified", "世界块单位任务目标", "推送快照")
    def current_target(self, u):
        """单位**实际在打/在追**的那个单位（没有返回 None）。
        ⚠ 攻击令下完订单槽很快变空，攻击挂在任务上 —— 判"在打谁"用这个，别用 current_order。"""
        v = self._fresh(u)
        return self.unit(v.task_target) if v and v.task_target else None

    @api("observe", "verified", "世界块头 clockMs（引擎游戏时钟）", "推送快照")
    def clock(self) -> float | None:
        """引擎游戏时钟（游戏秒，载入中为 0）。倍速下它比墙钟走得快。"""
        s = self.snapshot()
        return s.clock if s else None

    @api("observe", "verified", "世界块生产表（Aque/ABnP/AUnP 技能对象 + 运行时跟踪的已进行时间；实测误差 < 0.2 游戏秒）", "推送快照")
    def production(self, building):
        """这座建筑正在做什么：Production(kind, queue, duration, elapsed, blocked, progress, remaining…)，没在做返回 None。
          kind 'queue'（训练/研究/英雄，queue 最多 7 格，[0] 是正在做的）/ 'construction'（在建）/ 'upgrade'（升级大厅/塔）；
          blocked = 有排队但没开始（多半是人口不够 —— 该造农场了）；progress 0..1。
        对手的建筑也能看（公平模式下只有看得见的建筑才有）。"""
        s = self.snapshot()
        if not s or building is None:
            return None
        return s.production_of(building)

    @api("observe", "verified", "世界块生产表", "推送快照")
    def queue(self, building) -> list:
        """训练/研究队列里的四字码（[0] 正在做）；空闲或不是生产建筑 = []。"""
        p = self.production(building)
        return list(p.queue) if p and p.kind == "queue" else []

    @api("observe", "verified", "世界块生产表", "推送快照")
    def all_production(self, owner: str | int = "me") -> list:
        """全部正在进行的生产 [(建筑, Production)]。owner 同 units()：'me' / 'enemy' / 玩家号 / 'all'。
        职业用法：看对手在练什么兵、研究什么科技、什么时候升本（侦察到建筑时）。"""
        s = self.snapshot()
        if not s:
            return []
        mine = {u.handle for u in self.units(owner)}
        return [(s.by_handle[p.building], p) for p in s.production if p.building in mine]

    GRID_TTL = 2.0      # 寻路网格缓存（墙钟秒）：树块 2 秒一刷，建筑也不会秒变

    def grid(self, with_buildings: bool = True):
        """地面寻路网格 pathing.Grid（地图块 + 活着的树 + 建筑），2 秒缓存；地图块没算好返回 None。"""
        from . import pathing
        now = time.monotonic()
        key = bool(with_buildings)
        cache = getattr(self, "_grid_cache", None) or {}
        hit = cache.get(key)
        if hit and now - hit[0] < self.GRID_TTL:
            return hit[1]
        gr = pathing.Grid.from_game(self, with_buildings=with_buildings)
        cache[key] = (now, gr)
        self._grid_cache = cache
        return gr

    @staticmethod
    def _xy(p):
        return (p.x, p.y) if hasattr(p, "x") else (float(p[0]), float(p[1]))

    @api("observe", "verified", "地图块（引擎 IsTerrainPathable）+ 树块 + 建筑占地，SDK 侧 A*（128 一格）", "推送快照 + 本地计算")
    def path_distance(self, a, b) -> float | None:
        """地面单位从 a 走到 b 的路程（a、b 给单位或 (x,y)）；走不到 None。岛图上判断"这个野点/分矿地面过不过得去"用它，
        比直线距离靠谱（绕树林、绕悬崖、绕建筑）。精度一格 128；窄于一格的缝判成不通。"""
        gr = self.grid()
        return gr.distance(self._xy(a), self._xy(b)) if gr else None

    @api("observe", "verified", "同上", "推送快照 + 本地计算")
    def reachable(self, a, b) -> bool | None:
        """地面能不能走到（地图块没算好 = None）。"""
        gr = self.grid()
        return gr.reachable(self._xy(a), self._xy(b)) if gr else None

    @api("observe", "verified", "同上", "推送快照 + 本地计算")
    def walk_path(self, a, b) -> list | None:
        """路径拐点 [(x,y)...]（最后一点是 b）；配合 path(units, 点列表) 让部队按这条路走（绕开塔、走小路）。"""
        gr = self.grid()
        return gr.path(self._xy(a), self._xy(b)) if gr else None

    # ── 运营常识（纯计算，职业玩家天天在看的几个数）──────────────────
    @api("observe", "inferred", "1.27 固定规则：0~50 人口不收、51~80 收入 ×0.7、81~100 ×0.4", "推送快照")
    def upkeep(self, player: int | None = None) -> dict | None:
        """维护费档位：{'level': 'none'/'low'/'high', 'income': 1.0/0.7/0.4, 'next_at': 下一档的人口（没有 = None）}。
        职业常识：升三本/攻防时停在 50 人口，决战前才上 80。"""
        res = self.resources(player)
        if not res:
            return None
        used = res["food_used"]
        if used <= 50:
            return {"level": "none", "income": 1.0, "next_at": 51}
        if used <= 80:
            return {"level": "low", "income": 0.7, "next_at": 81}
        return {"level": "high", "income": 0.4, "next_at": None}

    @staticmethod
    def hero_xp_needed(level: int) -> int:
        """到 level 级要的累计经验（MiscGame NeedHeroXP=200、公式 B=100：200/500/900/1400/2000/…/5400）。"""
        need = 0
        for lv in range(2, max(2, level) + 1):
            need = 200 if lv == 2 else need + 100 * lv
        return need

    @api("observe", "verified", "世界块 level/xp + MiscGame NeedHeroXP 公式", "推送快照")
    def xp_to_next(self, hero) -> int | None:
        """英雄离下一级还差多少经验（10 级 = 0）。"""
        info = self.hero_info(hero)
        if not info:
            return None
        if info["level"] >= 10:
            return 0
        return max(0, self.hero_xp_needed(info["level"] + 1) - info["xp"])

    @staticmethod
    def creep_xp(creep_level: int, hero_level: int) -> float:
        """打死一只 creep_level 级野怪，一个 hero_level 级英雄独得多少经验（MiscGame GrantHeroXP × HeroFactorXP）：
        英雄 1/2/3/4 级拿 80/70/60/50%，**5 级以后打野 0 经验**（职业打法：5 级之后该去打人）。多个英雄在旁边会平分。"""
        grant = [100, 120, 160, 220, 300]
        base = grant[creep_level - 1] if 1 <= creep_level <= 5 else 300 + 100 * (creep_level - 5)
        factor = [0.8, 0.7, 0.6, 0.5][hero_level - 1] if 1 <= hero_level <= 4 else 0.0
        return base * factor

    @api("observe", "verified", "推送快照（野怪按 600 连成一群）+ units.json 等级", "推送快照")
    def creep_camps(self, link: float = 600.0) -> list:
        """把场上（看得见的）野怪聚成营地：[{'x','y','units','level','hp','max_level'}]，按离我方主基地由近到远。
        level = 营地总等级（打野难度的常用口径），hp = 总血量。配合 time_to_kill / path_distance 挑点。"""
        cs = [c for c in self.creeps() if c.alive]
        groups = []
        for c in cs:
            near = [gr for gr in groups if any((c.x - o.x) ** 2 + (c.y - o.y) ** 2 <= link * link for o in gr)]
            merged = [c]
            for gr in near:
                merged += gr
                groups.remove(gr)
            groups.append(merged)
        tbl = unit_table()
        halls = self.my_buildings(HALLS)
        hx, hy = (halls[0].x, halls[0].y) if halls else (0.0, 0.0)
        out = []
        for gr in groups:
            lv = [int((tbl.get(u.type) or {}).get("level") or 0) for u in gr]
            x, y = sum(u.x for u in gr) / len(gr), sum(u.y for u in gr) / len(gr)
            out.append({"x": x, "y": y, "units": gr, "level": sum(lv), "max_level": max(lv), "hp": sum(u.hp for u in gr)})
        out.sort(key=lambda c: (c["x"] - hx) ** 2 + (c["y"] - hy) ** 2)
        return out

    @api("observe", "verified", "data/game/buffs.json（AbilityData.slk 的 BuffID -> 技能/效果/时长）", "本地数据")
    def buff_info(self, code: str) -> dict | None:
        """buff 码是什么：{'ability','effect','dur','hero_dur','targets'}（例 'Bslo' -> 减速）。一码多行时给第一行。"""
        rows = _buff_table().get(code)
        if not rows:
            return None
        r = rows[0]
        return {"ability": r.get("ability"), "effect": r.get("effect"), "dur": r.get("dur"), "hero_dur": r.get("heroDur"),
                "targets": r.get("targets")}

    TECH_TTL = 5.0      # stats() 的科技等级缓存（墙钟秒）：升级几十秒才完成一级，5 秒一查足够

    def _tech_levels(self, player: int) -> dict:
        from . import combat
        now = time.monotonic()
        cache = getattr(self, "_tech_cache", None)
        if cache is None:
            cache = self._tech_cache = {}
        hit = cache.get(player)
        if hit and now - hit[0] < self.TECH_TTL:
            return hit[1]
        bal, _w, upg = combat._tables()
        codes = sorted(upg)                                   # 全部升级码一批问（一次等待）
        try:
            levels = {c: v for c, v in self.tech_many(codes, player=player).items() if v}
        except Exception:  # noqa: BLE001 - 读不到就当没升级
            levels = {}
        cache[player] = (now, levels)
        return levels

    @api("observe", "verified", "数据表（UnitBalance/UnitWeapons/UpgradeData/MiscGame）+ 实时科技等级 + 英雄等级", "推送快照 + 快车道（5 秒一批）")
    def stats(self, u, player: int | None = None):
        """单位的战斗属性 combat.UnitStats：血/蓝上限、护甲（含攻防升级、英雄敏捷）、护甲类型、移速、白天/夜里视野、
        武器（能打什么、射程、攻击间隔、伤害区间、攻击类型、溅射）。u 给单位（自动用它主人的科技、英雄等级）或四字码（player 默认我方）。
        再配 .dps_vs(对方) / .hits_to_kill(对方) / combat.time_to_kill(一群, 对方)。⚠ 不含物品、光环、buff。"""
        from . import combat
        if isinstance(u, str):
            p = self.me() if player is None else player
            return combat.stats(u, tech=self._tech_levels(p) if p is not None else None)
        v = self._fresh(u) or u
        p = v.owner if player is None else player
        tech = self._tech_levels(p) if p is not None and p < 12 else None
        return combat.stats(v.type, tech=tech, hero_level=v.level if getattr(v, "is_hero", False) else None)

    @api("observe", "verified", "stats() + 实时血量", "推送快照")
    def time_to_kill(self, attackers, target) -> float | None:
        """这群单位一起打 target 要几游戏秒（用 target 现在的血；算克制、护甲、攻防升级；不算走位、溅射、治疗）。
        职业用法：集火先打"最快能打死"的那个（time_to_kill 最小），而不是最近的那个。打不到 = None。"""
        from . import combat
        v = self._fresh(target) or target
        ts = self.stats(v)
        if ts is None:
            return None
        return combat.time_to_kill([s for s in (self.stats(a) for a in attackers) if s], ts, target_hp=v.hp)

    @api("observe", "verified", "世界块扩展区：GetFloatGameState(GAME_STATE_TIME_OF_DAY)", "推送快照")
    def time_of_day(self) -> float | None:
        """游戏内时间（小时，0~24）。开局是早上 8 点；一整天 = 480 游戏秒（白天、黑夜各 240 秒，按昼夜流速缩放）。
        读不到（老运行时 / 不在局里）返回 None。"""
        s = self.snapshot()
        return s.time_of_day if s else None

    @api("observe", "verified", "世界块扩展区（6~18 点白天）", "推送快照")
    def is_night(self) -> bool | None:
        """现在是不是夜里（18:00~6:00）。职业打法：夜里野怪睡着（先手打野不被围）、所有单位视野变短（偷袭好时机），
        暗夜精灵的哨兵/单位夜里在树边隐身。读不到返回 None。"""
        s = self.snapshot()
        return s.is_night if s else None

    @api("observe", "verified", "世界块扩展区 + 480 秒一天（实测 20 游戏秒/小时）", "推送快照")
    def seconds_until(self, hour: float) -> float | None:
        """离游戏内时间 hour 点还有几游戏秒（比如 seconds_until(18) = 离天黑还有多久，计划夜里打野用）。"""
        s = self.snapshot()
        if not s or s.time_of_day is None:
            return None
        scale = s.tod_scale or 1.0
        return ((hour - s.time_of_day) % 24.0) * 20.0 / scale     # 一小时 = 20 游戏秒（480 / 24）

    @api("observe", "verified", "世界块 items[]（只含地上的：持有者句柄全 FF）", "推送快照")
    def items_on_ground(self) -> list:
        """地上的物品 [Item(addr, handle_lo, handle_hi, type, x, y, life)]。捡走/用掉会发 item.removed 事件。"""
        s = self.snapshot()
        return list(s.items) if s else []

    @api("observe", "verified", "树块 Local\\War3Trees_<pid>（每 2 秒刷新）", "推送快照")
    def trees(self, x: float | None = None, y: float | None = None, limit: int = 60) -> list:
        """活着的树（DestructableData 里 targType 含 tree 的），给了 (x,y) 就按距离由近到远，最多 limit 棵。
        每棵是 Tree(addr, handle_lo, handle_hi, type, x, y, life)，可以直接交给 gather 去伐木。"""
        ts = self.world.trees()
        if x is not None and y is not None:
            ts.sort(key=lambda t: (t.x - x) ** 2 + (t.y - y) ** 2)
        return ts[:limit]

    @api("observe", "verified", "事件环 Local\\War3Events_<pid>（发布对比 + 运行时捕获的伤害事件）", "推送快照")
    def events(self) -> list:
        """上次调用之后发生的事：unit.appeared / unit.died / unit.removed / unit.damaged / order.changed /
        hero.levelup / owner.changed / item.appeared / item.removed / game.started（这些按发布对比，精度 = 发布周期 50 ms），
        以及引擎级的 damage / killed（运行时在游戏线程上当场记下，**每一下**都有）：
            damage：handle = 挨打的，.source_addr = 打它的（snapshot().unit_by_addr 换成单位），.value = 实际掉的血，
                    .raw_damage = 护甲前的伤害，.attack_type（normal/pierce/siege/magic/chaos/hero/spell），.damage_type
            killed：这一下把它打死了，.source_addr = 凶手
        以及运行时跟踪生产表得出的 production.done（精度 = 发布周期）：单位 = 建筑，.done_code = 做完的四字码，
            .done_kind = 'training'（兵/英雄/复活）/ 'research' / 'construction'（建筑盖好）/ 'upgrade'（升本/升塔），.value = 用了几游戏秒
        09-25 补齐：
            spell.cast：单位 = 施法者，.spell 技能四字码，b 等级，value 冷却秒，x,y 施法点（技能开始冷却时认出来，精度 = 发布周期）
            player.left：.player 离开 / 被判负移除的玩家号；game.ended：离开对局
            selection.changed：本机玩家的选择变了（g.selection() 取单位）
            message：屏幕消息框里的一条（游戏提示、聊天、系统）：.text 全文，.frame 消息框编号，
                     .chat = {'channel', 'sender', 'text'}（是聊天时；玩家在聊天框打的字就从这里读）
            ui.click / ui.hover / hotkey / mouse.world：界面与输入（g.ui），.key 是画板 key / 热键写法
        每条是 Event(seq, kind, clock, addr, handle, type, owner, a, b, x, y, value, extra)。
        公平模式（fair=True）只给：自己的单位的事件、此刻看得见（或 1 秒内还看得见）的单位的事件、打我方/我方打的伤害，
        以及本机的界面 / 消息 / 对局类事件。"""
        evs = list(self._event_backlog) + self.world.events()
        self._event_backlog = []
        for e in evs:
            if e.kind == "message" and not hasattr(e, "text"):
                m = self.world.message(e.a)
                e.text, e.frame = (m["text"], m["frame"]) if m else ("", e.b)
                e.chat = parse_chat(e.text)          # 聊天：{'channel', 'sender', 'text'}；游戏提示 / 系统消息 = None
        if self._ui is not None:
            self._ui.handle(evs)
        elif getattr(self, "_canvas", None) is not None:
            # 没用 g.ui、直接画了 clickable=True 的画板项：点击 / 悬停事件也带上画的时候给的 key
            for e in evs:
                if e.kind in ("ui.click", "ui.hover"):
                    e.key = self._canvas.key_of(e.item_id) if e.item_id else None
        if not self.fair:
            return evs
        s = self.snapshot(max_age=0)
        if not s:
            return []
        p = s.me
        vis = set(s.by_handle)
        recent = {h for h, (_u, t) in getattr(self, "_last_seen", {}).items() if s.clock - t <= 1.0}
        mine = {u.addr for u in s.units if u.owner == p}
        local = ("game.started", "game.ended", "player.left", "selection.changed", "message",
                 "ui.click", "ui.hover", "hotkey", "mouse.world")
        return [e for e in evs if e.owner == p or e.handle in vis or e.handle in recent or e.kind in local
                or (e.kind in ("damage", "killed") and e.source_addr in mine)]

    @api("observe", "verified", "W3P 世界块扩展区 selAddrs（运行时每次发布时带上本机玩家的选择）", "推送快照")
    def selection(self) -> list:
        """本机玩家现在选中的单位（主单位排第一；最多 12 个）。选择一变会发 selection.changed 事件。"""
        s = self.world.read()
        return s.selected if s else []

    @api("observe", "verified", "共享内存 Local\\War3Msgs_<pid>（运行时捕获的屏幕消息）", "推送快照")
    def messages(self) -> list:
        """上次调用之后屏幕消息框里新出的消息：[{'text', 'frame', 'repeat', 'seq', 'game_ms'}]。
        游戏提示（「需要更多的农场」「不能在那里建造」）、聊天、系统消息都在这里；frame 区分是哪个消息框。
        和事件流里的 message 事件是同一批（各有各的游标）。"""
        return self.world.messages()

    @property
    @api("control", "verified", "W3P 74 input_enable + 共享内存 Local\\War3Input_<pid>（运行时接收窗口输入）", "共享内存")
    def ui(self):
        """界面与输入（openwar3.ui.UI）：可点的按钮和选项卡、热键、点地面选位置、鼠标指着哪儿。
        点在按钮上的这一下游戏收不到；纯本机输入 + 本机绘制，多人局也安全。"""
        if self._ui is None:
            from .ui import UI
            self._ui = UI(self)
        return self._ui

    def _fresh(self, u):
        """把旧快照里的单位换成最新一份（按句柄）；已经不在了返回 None。"""
        s = self.snapshot()
        if s is None or u is None:
            return None
        return s.by_handle.get((getattr(u, "handle_lo", None), getattr(u, "handle_hi", None)))

    # ── 查询（运行时代问引擎）──────────────────────────────────
    def _one(self, call):
        r = self.lane.commands([call])[0]
        self.last_receipts = [r]
        return r

    @api("observe", "verified", "W3P 查询 q_tech（引擎的玩家科技计数）", "快车道")
    def tech(self, code: str, player: int | None = None) -> int | None:
        """研究等级 / 建完的建筑数（升级链算在内：城堡也算 htow）。player 默认我方，任何玩家都能查。"""
        p = self.me() if player is None else player
        r = self._one(self._w3fast.command("q_tech", code=rawcode(code), extra=p or 0))
        return r.values[0] if r.ok else None

    @api("observe", "verified", "W3P 查询 q_feasible（引擎可行性检查）", "快车道")
    def can_do(self, u, code: str) -> int | None:
        """引擎的可行性裁决：0/220 能下；3 人口 8 缺金 9 缺木 32 队列满 183 缺前置 185 祭坛在复活 221 没有这一项/建造中。
        ⚠ 对工人造建筑恒 221，不能用来判落点（用 build_near）。"""
        r = self._one(self._w3fast.command("q_feasible", u, rawcode(code)))
        return r.verdict if r.ok else None

    @api("observe", "verified", "W3P 查询 q_feasible × N，一批提交", "快车道 × 1")
    def can_do_many(self, pairs) -> list:
        """一次问很多个 can_do：pairs = [(单位, 四字码), ...]，返回同样顺序的裁决码列表（问不到的是 None）。
        规划一拍要造/练什么时先整体问一遍，比逐个 can_do 快 N 倍（参考大脑 09-23：建造规划 76 -> 25 ms）。"""
        pairs = list(pairs)
        C = self._w3fast.command
        rs = self.lane.commands([C("q_feasible", u, rawcode(c)) for u, c in pairs]) if pairs else []
        return [r.verdict if r.ok else None for r in rs]

    @api("observe", "verified", "W3P 查询 q_tech × N，一批提交", "快车道 × 1")
    def tech_many(self, codes, player: int | None = None) -> dict:
        """一次查很多个科技/建筑计数：{四字码: 数量或 None}。"""
        codes = list(codes)
        p = self.me() if player is None else player
        C = self._w3fast.command
        rs = self.lane.commands([C("q_tech", code=rawcode(c), extra=p or 0) for c in codes]) if codes else []
        return {c: (r.values[0] if r.ok else None) for c, r in zip(codes, rs)}

    @api("observe", "verified", "W3P 查询 q_visible（看得见 / 迷雾 / 黑区）", "快车道")
    def visible(self, x: float, y: float) -> bool | None:
        """这一点我方现在看不看得见（不在迷雾/黑区里）。公平模式的 bot 应该只用看得见的敌人。"""
        r = self._one(self._w3fast.command("q_visible", x=x, y=y))
        return bool(r.values[0]) if r.ok else None

    @api("observe", "inferred", "W3P 查询 q_mine_gold（引擎的金矿余量）", "快车道")
    def gold_left(self, mine) -> int | None:
        """金矿还剩多少金。"""
        r = self._one(self._w3fast.command("q_mine_gold", target=mine))
        return r.values[0] if r.ok else None

    @api("observe", "verified", "W3P 查询 q_captain（敌兵跟着的电脑队长）", "快车道")
    def enemy_ai_plan(self, enemy_unit) -> dict | None:
        """电脑 AI 的队长：它带兵要去哪（出门前就知道要打你家哪里）。只对电脑对手有效；没跟队长走返回 None。"""
        r = self._one(self._w3fast.command("q_captain", enemy_unit))
        if not r.ok:
            return None
        f = [r.value_float(i) for i in range(6)]
        return {"x": f[0], "y": f[1], "goal_x": f[2], "goal_y": f[3], "home_x": f[4], "home_y": f[5]}

    # ── 命令 ──────────────────────────────────────────────────
    def _take(self, u, seconds: float = 3.0) -> bool:
        try:
            return self.claims.take(u.addr, seconds, prio=self.priority)
        except Exception:  # noqa: BLE001 - 仲裁表打不开就照常下令（和参考大脑一致）
            return True

    def _issue(self, units, make, hold_s: float = 3.0):
        """对一个或一批单位下同一种命令：过仲裁 -> 一批提交（同一帧执行）-> 回执。
        传单个单位返回一张 Receipt，传列表返回列表。"""
        single = not isinstance(units, (list, tuple))
        units = [units] if single else list(units)
        if self._batch is not None:                 # 批量块里：先过仲裁、攒着，块结束时一起提交
            out = []
            for u in units:
                if not self._take(u, hold_s):
                    out.append(Pending(self._w3fast.Receipt(0, HELD, 0, None, 0, 0, (0,) * 8)))
                    continue
                p = Pending()
                h = (u.handle_lo, u.handle_hi) if hasattr(u, "handle_lo") else None
                self._batch.items.append((make(u), p, h, int(getattr(u, "addr", u))))
                out.append(p)
            return out[0] if single else out
        calls, slots, out = [], [], [None] * len(units)
        for i, u in enumerate(units):
            if not self._take(u, hold_s):
                out[i] = self._w3fast.Receipt(0, HELD, 0, None, 0, 0, (0,) * 8)
                continue
            calls.append(make(u))
            slots.append(i)
        now = time.monotonic()
        for i, r in zip(slots, self.lane.commands(calls) if calls else []):
            out[i] = r
            if r.ok and r.order_after:               # 读到自己刚写的：快照追上之前以回执里的新订单为准
                self._recent_orders[(units[i].handle_lo, units[i].handle_hi)] = (r.order_after, now)
        self.last_receipts = out
        return out[0] if single else out

    @staticmethod
    def _then(rc, fn):
        """回执到手后再做的事（补解释）：普通命令当场做，批量块里的等提交之后做。"""
        if isinstance(rc, Pending) and not rc.done:
            rc._after = fn
        elif rc is not None:
            fn(rc.receipt if isinstance(rc, Pending) else rc)
        return rc

    @api("command", "verified", "块里的命令攒成一批，块结束时一次提交（同一帧执行、只等一次游戏线程）", "快车道 × 1")
    def batch(self) -> Batch:
        """把一拍里的命令合成一批：

            with g.batch() as b:
                g.attack(archers, target)          # 返回 Pending，块结束后才变成回执
                g.move(wounded, *home)
                g.cast(hero, "thunderclap")
            print(b.sent, b.wait_ms, [r.reason for r in b.receipts])

        每条命令单独发都要等游戏线程处理一次（约 10 ms）；一批只等一次 —— 参考大脑 09-23 靠这个一轮 48 -> 26 ms。
        * 仲裁照旧逐条过（被握着的单位当场得到 held 回执，不进批）；
        * 块里的命令返回 Pending：块结束前读它的 .ok 会抛错（回执还不存在），块结束后和 Receipt 一样用；
        * 块里抛了异常 = 整批作废（status 97 cancelled），握着的单位放回去；
        * 查询（can_do / tech / visible …）和 build_near、buy 不进批，照旧当场问 —— 它们的结果当场要用；
          要一次问很多个用 can_do_many / tech_many；
        * 嵌套的 with g.batch() 并进最外层那一批；超过 16 条运行时自动分几段（每段一次等待）。"""
        return Batch(self)

    RECENT_S = 0.35     # 快照每 50 ms 一份，下令后约 1~2 份就能看到；留足余量

    @api("observe", "verified", "快照订单 + 本进程刚被接下的命令（回执）", "推送快照")
    def order_of(self, u) -> int | None:
        """单位现在的订单，**含你这一拍刚下的**（快照还没追上时用回执里的新订单）。
        ⚠ 09-23 实机：hello_bot 刚派农民去盖农场，同一拍 rush_bot 从快照里看它"空闲"又派去盖兵营，农场一次次半途而废。
          挑"闲着的/没在盖房子的"单位时，用这个而不是 u.order。"""
        rec = self._recent_orders.get((u.handle_lo, u.handle_hi))
        if rec and time.monotonic() - rec[1] < self.RECENT_S:
            return rec[0]
        v = self._fresh(u) or u
        return v.order

    # 排队方式（W3P extra 位）：None 立刻执行（清掉原来排着的）/ 'after' 做完手上这件再做（插在当前订单后面）。
    # ⚠ 引擎没有"追加到末尾"：要按顺序走一串点用 path()、连造用 build_queue()（它们倒序插，结果正好按顺序）。
    # （运行时还有 extra=3"打断后恢复"，09-24 实测单位已经在走时不生效，不对外）
    _QUEUE = {None: 0, "after": 1}

    def _q(self, queue) -> int:
        if queue not in self._QUEUE:
            raise ValueError(f"queue 只能是 None 或 'after'，不是 {queue!r}")
        return self._QUEUE[queue]

    @api("command", "verified", "W3P point：move（extra 位 = 排队方式）", "快车道")
    def move(self, units, x: float, y: float, queue: str | None = None):
        """走到 (x,y)，路上不打人（撤退用这个）。可以传一个单位或一个列表（同一帧一起下）。
        queue='after'：做完手上这件再去（插在当前订单后面）。回执 values[0] = 下令后这个单位排着几条订单（含正在做的）。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("point", u, MOVE, x=x, y=y, extra=q))

    @api("command", "verified", "W3P point：attack 对点", "快车道")
    def attack_move(self, units, x: float, y: float, queue: str | None = None):
        """攻击移动（A 地面）：路上遇敌就打。queue 同 move。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("point", u, ATTACK, x=x, y=y, extra=q))

    @api("command", "verified", "W3P target：目标命令（右键 smart）", "快车道")
    def attack(self, units, target, force: bool = False, queue: str | None = None):
        """打 target。默认用右键（对敌人 = 攻击这一个，09-23 实测订单目标/任务目标都是它）。
        ⚠ 目标必须在视野里，看不见的会被拒（原因码 1001）。
        force=True 用攻击令 0x0F（打自己人/中立小动物时要它）—— 实测它只换上攻击令、不记住目标，
        会去打附近别的敌人，打具体目标别用它。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("target", u, ATTACK if force else SMART, target=target, extra=q))

    @api("command", "verified", "W3P immediate：stop", "快车道")
    def stop(self, units):
        """停下手上的一切（订单号 0x000D0004），排着的订单也清掉。"""
        C = self._w3fast.command
        return self._issue(units, lambda u: C("immediate", u, STOP))

    @api("command", "verified", "W3P immediate：holdposition", "快车道")
    def hold(self, units, queue: str | None = None):
        """原地待命（不追出去，只打射程里的）。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("immediate", u, HOLD, extra=q))

    @api("command", "inferred", "W3P point：patrol", "快车道")
    def patrol(self, units, x: float, y: float, queue: str | None = None):
        """在当前位置和 (x,y) 之间巡逻。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("point", u, ORDER("patrol"), x=x, y=y, extra=q))

    @api("command", "verified", "W3P point：attackground（攻城单位 / 迫击炮 / 投石车）", "快车道")
    def attack_ground(self, units, x: float, y: float, queue: str | None = None):
        """攻击地面：炮对一块地方开火（打隐形单位、打树林后面的人、封路口）。只有能攻击地面的单位接受。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(units, lambda u: C("point", u, ORDER("attackground"), x=x, y=y, extra=q))

    @api("command", "verified", "W3P immediate：cancel", "快车道")
    def cancel(self, building):
        """取消：训练/研究队列的最后一格（退钱）、正在盖的建筑（退 75%）、正在升级的大厅。"""
        C = self._w3fast.command
        return self._issue(building, lambda u: C("immediate", u, ORDER("cancel")))

    @api("command", "verified", "一批：第一段立刻执行，其余倒序用 queue='after' 插（引擎只有插在当前后面）", "快车道 × 1")
    def path(self, units, points, attack: bool = False):
        """按顺序走过一串点（Shift 连点：路径点、绕开塔、侦察路线）。attack=True 每段都是攻击移动。
        一次提交；回执是每个点一张（按 points 顺序）。"""
        pts = list(points)
        if not pts:
            return []
        fn = self.attack_move if attack else self.move
        with self.batch():
            first = fn(units, *pts[0])
            rest = [fn(units, x, y, queue="after") for x, y in reversed(pts[1:])]
        return [first] + list(reversed(rest))

    @api("command", "verified", "W3P target：harvest（金矿或树）", "快车道")
    def gather(self, workers, target, queue: str | None = None):
        """采金/伐木（target 是金矿或 trees() 里的树）。⚠ 只给空闲工人派（idle_workers）：给有任务的工人重下会打断采集周期。
        职业用法：盖完房子回去采矿 = build(...) 之后 gather(worker, mine, queue='after')。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(workers, lambda u: C("target", u, HARVEST, target=target, extra=q))

    @api("command", "verified", "W3P target：repair", "快车道")
    def repair(self, workers, building, queue: str | None = None):
        """修理 / 帮着盖（人族兽族的工地没人盖会停工）。"""
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(workers, lambda u: C("target", u, REPAIR, target=building, extra=q))

    @api("command", "verified", "W3P build：建造订单，同一帧读回工人订单确认", "快车道")
    def build(self, worker, code: str, x: float, y: float, queue: str | None = None):
        """让工人在 (x,y) 造 code（坐标对齐 32 格）。回执接下 = 工人订单已经是这座建筑（或开工令）；
        queue='after' 时 = 排进了工人的订单队列（回执 values[0] 排队数）。
        ⚠ 接下 ≠ 造得成：树林里的点引擎也当场接下，工人走到了才失败（09-23 实测）；钱被别处花掉也会让地基出不来。
        不知道哪里放得下就用 build_near（它会跟踪结果、拉黑失败的点）。连造几座用 build_queue。"""
        x, y = round(x / 32) * 32, round(y / 32) * 32
        C, q = self._w3fast.command, self._q(queue)
        return self._issue(worker, lambda u: C("build", u, rawcode(code), x=x, y=y, extra=q), hold_s=5.0)

    @api("command", "verified", "一批：第一座立刻，其余倒序 queue='after'", "快车道 × 1")
    def build_queue(self, worker, plan):
        """一个工人按顺序连造几座（Shift 连造）：plan = [(四字码, x, y), ...]。一次提交；回执按 plan 顺序。
        ⚠ 钱是开工时才扣的（排队时不扣）—— 排了 3 座但钱只够 1 座，后两座会在工人走到时失败。"""
        plan = list(plan)
        if not plan:
            return []
        with self.batch():
            first = self.build(worker, *plan[0])
            rest = [self.build(worker, c, x, y, queue="after") for c, x, y in reversed(plan[1:])]
        return [first] + list(reversed(rest))

    @api("command", "verified", "逐点 build + 跟踪（地基出现 = 成；工人放弃订单又没地基 = 这个点拉黑）", "快车道 × 试的点数")
    def build_near(self, worker, code: str, x: float, y: float, min_r: float = 450, max_r: float = 1500,
                   max_tries: int = 24):
        """在 (x,y) 周围由近到远找个放得下的点盖 code。**不阻塞**，每拍调都行：
          * 这种建筑有一次还在进行中（工人走在路上）-> 返回那个点，不重复下令；
          * 上一次成了（地基出现）-> 这次按需再找新点；
          * 上一次失败了（工人走到才发现放不下，订单被引擎撤掉、没有地基）-> 那个点拉黑 45 秒，换下一个；
          * 钱不够 -> 直接返回 None（不试、不拉黑）；都试完了返回 None。
        ⚠ 为什么要跟踪：09-23 实机，树林里的点引擎**当场接下**、工人走到了才失败（同一帧的回执判不出来）；
          而引擎的落点检查对工人造建筑恒回 221，也没法先"查"再盖。只有明显被占的点（大厅正中）会当场拒绝。"""
        now = time.monotonic()
        bad = self._bad_spots.setdefault(code, {})
        for spot in [p for p, until in bad.items() if until < now]:
            del bad[spot]                            # 黑名单会过期：临时有单位站在那儿也会被拒，不该永久拉黑
        pend = self._pending_builds.get(code)
        if pend is not None:
            state = self._build_state(pend)
            if state == "walking":
                return pend["spot"]
            self._pending_builds.pop(code, None)
            if state == "failed":
                bad[pend["spot"]] = now + self.BAD_SPOT_S
        # ⚠ 09-23 实机：55 金时盖 80 金的农场，24 个点全被拒 —— 被当成"放不下"全部拉黑，之后有钱了也没点可试。
        #   引擎的建造拒绝不带原因，所以先自己算买不买得起：买不起直接返回，不试、不拉黑。
        if not self.can_afford(code):
            return None
        tries = 0
        r = min_r
        while r <= max_r and tries < max_tries:
            for k in range(12):
                a = 2 * math.pi * k / 12
                px, py = round((x + r * math.cos(a)) / 32) * 32, round((y + r * math.sin(a)) / 32) * 32
                if (px, py) in bad:
                    continue
                tries += 1
                outer, self._batch = self._batch, None          # 要当场看回执决定换不换点：不进批
                try:
                    rc = self.build(worker, code, px, py)
                finally:
                    self._batch = outer
                if rc:
                    s = self.snapshot(max_age=0)
                    self._pending_builds[code] = {"spot": (px, py), "worker": (worker.handle_lo, worker.handle_hi),
                                                  "since": s.publish_count if s else 0, "t": time.monotonic()}
                    return px, py
                if rc.status == HELD:
                    return None
                bad[(px, py)] = now + self.BAD_SPOT_S
                if tries >= max_tries:
                    break
            r += 200
        return None

    BAD_SPOT_S = 45.0   # 被拒的落点多久之后再给一次机会（墙钟秒）

    @api("observe", "verified", "推送快照的我方资源 + units.json 的价格", "推送快照")
    def can_afford(self, code: str) -> bool:
        """现在的金/木够不够买 code（单位、建筑；按 units.json 的价格）。价格表里没有的一律当买得起。
        ⚠ 升本的四字码在表里是累计价，这里会偏保守；最终以引擎的回执为准。"""
        row = unit_table().get(code)
        res = self.resources()
        if not row or not res:
            return True
        return res["gold"] >= row.get("gold", 0) and res["lumber"] >= row.get("lumber", 0)

    def _build_state(self, pend) -> str:
        """一次 build_near 的进度：'done'（地基出现）/ 'walking'（工人还挂着这个建造令）/ 'failed'。"""
        s = self.snapshot(max_age=0)
        if s is None or time.monotonic() - pend["t"] < self.RECENT_S:
            return "walking"                         # 刚下的令快照还没追上：别拿旧快照判失败
        px, py = pend["spot"]
        for u in s.units:
            if u.owner == s.me and u.first_seen >= pend["since"] and abs(u.x - px) < 128 and abs(u.y - py) < 128 \
                    and self.is_building(u):
                return "done"
        w = s.by_handle.get(pend["worker"])
        if w is not None and w.alive and (w.order or 0) >= 0x20202020 and time.monotonic() - pend["t"] < 60:
            return "walking"
        return "failed"

    @api("command", "verified", "W3P immediate：四字码，被拒时带可行性原因码", "快车道")
    def train(self, building, code: str):
        """训练单位 / 研究科技 / 升级大厅（升本 = 对大厅本身下目标大厅的四字码，如 'hkee'）。
        被拒时回执的 reason 会说为什么（人口不够、缺金、缺木、队列满、缺前置……）。"""
        C = self._w3fast.command
        def explain(r):
            if not r.ok and r.verdict == 221 and code[:1].isupper():
                # 09-23 实机：圣骑士阵亡后 rush_bot 每拍"训练 Hpal"都回 221 —— 英雄唯一，死了只能复活
                r.note = f"{code} 已经有了（在训练中，或死了要用 revive 复活）"
        rc = self._issue(building, lambda u: C("immediate", u, rawcode(code)))
        if isinstance(rc, list):
            for r in rc:
                self._then(r, explain)
            return rc
        return self._then(rc, explain)

    research = train
    upgrade = train

    @api("command", "verified", "W3P learn：技能点减少才算学会", "快车道")
    def learn(self, hero, ability: str):
        """英雄学技能（四字码，如 'AHbz' 暴风雪）。"""
        C = self._w3fast.command
        return self._issue(hero, lambda u: C("learn", u, rawcode(ability)))

    @api("command", "verified", "W3P target / point / immediate（按参数选）", "快车道")
    def cast(self, u, spell, target=None, x: float | None = None, y: float | None = None):
        """放技能。spell 是订单名（'thunderbolt' 风暴之锤、'blizzard'、'holybolt' 圣光术…，见 data/order-ids.txt）或订单号。
        给 target = 对单位；给 x,y = 对地面；都不给 = 无目标（雷霆一击、神圣护盾、召唤水元素）。
        回执接下只代表引擎接受了；放没放出来看 cooldown() 有没有进冷却、buffs() 有没有出现。"""
        order = ORDER(spell)
        C = self._w3fast.command
        if target is not None:
            return self._issue(u, lambda v: C("target", v, order, target=target), hold_s=2.0)
        if x is not None and y is not None:
            return self._issue(u, lambda v: C("point", v, order, x=x, y=y), hold_s=2.0)
        return self._issue(u, lambda v: C("immediate", v, order), hold_s=2.0)

    @api("command", "verified", "W3P rally", "快车道")
    def rally(self, building, x: float | None = None, y: float | None = None, target=None):
        """设集结点（对点，或者对一个单位/金矿）。"""
        C = self._w3fast.command
        if target is not None:
            return self._issue(building, lambda u: C("rally", u, target=target, mode=2))
        return self._issue(building, lambda u: C("rally", u, x=x, y=y, mode=1))

    @api("command", "verified", "W3P revive：死亡英雄表 -> 祭坛对死英雄施放复活", "快车道")
    def revive(self, altar, hero=None):
        """在祭坛复活死掉的英雄（hero 不给就复活表里第一个）。
        被拒的常见原因（回执 reason 里会写）：人口不够（英雄也占人口）、钱不够、刚死不久（死后约 3 游戏秒才能复活）、
        复活已在进行（接下时引擎当场清掉那一槽）。"""
        C = self._w3fast.command
        rc = self._issue(altar, lambda u: C("revive", u, target=hero))

        def explain(r):
            if not r.ok and r.values[1]:
                self._explain_revive(r)
        return self._then(rc, explain)

    def _explain_revive(self, rc) -> None:
        """引擎不说复活为什么被拒；用推送快照里的人口/资源自己判一下，写进回执的 note。"""
        from w3world import fourcc
        typ = fourcc(rc.values[1])
        need = (unit_table().get(typ) or {}).get("food", 5)
        res = self.resources() or {}
        if res and res["food_used"] + need > res["food_cap"]:
            rc.note = f"人口不够（{typ} 要 {need}，现在 {res['food_used']}/{res['food_cap']}）"
        elif res and res["gold"] < 100:
            rc.note = "金不够"
        else:
            rc.note = "可能刚死不久（死后约 3 游戏秒才能复活）或复活已在进行"

    @api("command", "verified", "W3P target：右键物品", "快车道")
    def pick_up(self, hero, item):
        """英雄去捡地上的物品（item 来自 items_on_ground）。捡到后背包里出现、地上发 item.removed 事件。"""
        C = self._w3fast.command
        return self._issue(hero, lambda u: C("target", u, SMART, target=item))

    @api("command", "verified", "W3P use_item（按格号）", "快车道")
    def use_item(self, hero, slot: int, target=None, x: float | None = None, y: float | None = None):
        """用背包第 slot 格（0~5）的物品；可带目标单位或目标点。
        ⚠ 对点用物品（比如象牙塔）引擎成功也回 0，回执一律算接下 —— 看背包那格空没空。"""
        C = self._w3fast.command
        if target is not None:
            return self._issue(hero, lambda u: C("use_item", u, target=target, extra=slot, mode=2), hold_s=2.0)
        if x is not None and y is not None:
            return self._issue(hero, lambda u: C("use_item", u, x=x, y=y, extra=slot, mode=1), hold_s=2.0)
        return self._issue(hero, lambda u: C("use_item", u, extra=slot, mode=0), hold_s=2.0)

    def _item_at(self, hero, slot: int):
        """背包第 slot 格物品对象的地址（世界块细节里的 itemAddrs）；空格 / 没细节返回 None。"""
        v = self._fresh(hero) or hero
        addrs = getattr(v, "item_addrs", None) or []
        return addrs[slot] if 0 <= slot < len(addrs) and addrs[slot] else None

    def _item_cmd(self, hero, slot, make):
        item = self._item_at(hero, slot)
        if item is None:
            return self._w3fast.Receipt(0, 7, 0, None, 0, 0, (0,) * 8, note=f"第 {slot} 格没有物品")
        return self._issue(hero, lambda u: make(u, item), hold_s=2.0)

    @api("command", "verified", "W3P item_drop（照抄 JASS UnitDropItemPoint：dropitem 0xD0021 对点 + 物品即时目标）", "快车道")
    def drop_item(self, hero, slot: int, x: float, y: float):
        """把背包第 slot 格的东西丢在 (x,y)（英雄走过去放下）。"""
        C = self._w3fast.command
        return self._item_cmd(hero, slot, lambda u, it: C("item_drop", u, 0, target=it, x=x, y=y))

    @api("command", "verified", "W3P item_drop（照抄 JASS UnitDropItemTarget：dropitem 对单位）", "快车道")
    def give_item(self, hero, slot: int, to):
        """把背包第 slot 格的东西给 to（别的英雄 / 单位，走过去递过去）。给商店 = 卖掉（见 sell_item）。"""
        C = self._w3fast.command
        return self._item_cmd(hero, slot, lambda u, it: C("item_drop", u, int(to.addr), target=it))

    @api("command", "verified", "同 give_item，目标是商店（实测避难权杖卖 125 金）", "快车道")
    def sell_item(self, hero, slot: int, shop):
        """把背包第 slot 格的东西卖给商店（英雄要走到商店旁边；可卖的物品才收，回一半价钱）。"""
        return self.give_item(hero, slot, shop)

    @api("command", "verified", "W3P target：订单 0xD0022+格号，目标 = 物品（照抄 JASS UnitDropItemSlot）", "快车道")
    def move_item(self, hero, slot: int, to_slot: int):
        """背包里换格子（第 slot 格挪到第 to_slot 格；两格都有东西就对调）。整理快捷键位用。"""
        C = self._w3fast.command
        return self._item_cmd(hero, slot, lambda u, it: C("target", u, 0x000D0022 + int(to_slot), target=it))

    @api("command", "inferred", "W3P buy：商店卖给旁边的英雄", "快车道")
    def buy(self, shop, item_code: str):
        """在商店买物品（给站在商店旁边的英雄）。缺科技前置时引擎回 0、不扣钱。"""
        return self._one(self._w3fast.command("buy", shop, rawcode(item_code)))

    @api("command", "verified", "W3P immediate：townbellon/off", "快车道")
    def call_to_arms(self, hall, on: bool = True):
        """人族战斗号召：农民变民兵（一本城镇大厅没有这个技能，只对主城/城堡有效）。"""
        C = self._w3fast.command
        return self._issue(hall, lambda u: C("immediate", u, TOWNBELL_ON if on else TOWNBELL_OFF))

    # ── 游戏控制 ───────────────────────────────────────────────
    @api("control", "verified", "动作 47（25~800%）", "控制通道")
    def set_speed(self, percent: int) -> bool:
        """游戏倍速（100 = 原速）。"""
        return self._control().action(47, int(percent))

    @api("control", "verified", "W3P pause", "快车道")
    def pause(self, on: bool = True):
        """暂停 / 继续游戏。暂停时引擎时钟停住，快车道照常能下令（事件分发还在跑）。"""
        return self._one(self._w3fast.command("pause", extra=1 if on else 0))

    @api("control", "verified", "世界块 requestedPeriodMs", "推送快照")
    def set_publish_period(self, ms: int) -> None:
        """世界状态的发布周期（16~1000 毫秒，默认 50）。一次采集约 0.5 ms，33 ms 也没问题；全机共用一个值，最后写的算。"""
        self.world.set_period(ms)

    @api("control", "verified", "动作 56", "控制通道")
    def say(self, u, text: str, seconds: float = 4.0) -> bool:
        """单位头顶冒一个聊天气泡（直播/调试用，不影响游戏）。没冒出来返回 False，原因在 g.last_say_error。"""
        import w3speak
        try:
            if not hasattr(self, "_speaker"):
                self._speaker = w3speak.Speaker(self.pid)
            rs = self._speaker.say([w3speak.Bubble(text=text, unit=u.addr, duration_ms=int(seconds * 1000))])
            # 回执形如 'bubble batch ok=1 bad=0 n=1'；以前不看回执，运行时拒了（单位不在、DLL 旧）也返回 True
            ok = bool(rs) and all(re.search(r"\bok=[1-9]", r) and not re.search(r"\bbad=[1-9]", r) for r in rs)
            self.last_say_error = None if ok else "; ".join(rs)
            return ok
        except Exception as exc:  # noqa: BLE001 - 老 DLL（BubbleUnsupported）、通道超时……
            self.last_say_error = f"{type(exc).__name__}: {exc}"
            return False

    @api("control", "inferred", "动作 45", "控制通道")
    def message(self, text: str) -> bool:
        """在游戏左下角消息区打一行字（只有本机看得见）。要等游戏自己先弹过一条提示（DLL 从那次抓消息框）。"""
        import war3_offsets as O
        data = text.encode("utf-8")[:4000] + b"\0"
        ch = self._control()
        with ch._hold():
            ch._buf[O.R_SCRATCH:O.R_SCRATCH + len(data)] = data
            return ch._submit(45, 0)

    @api("control", "verified", "动作 22", "控制通道")
    def end_game(self) -> bool:
        """结束这个游戏进程（farm.py --keep 会按 next_game.json 自动开下一局）。"""
        try:
            self._control().action(22, 0, timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
        return True

    @property
    @api("control", "verified", "W3P 73 canvas_enable + 共享内存 Local\\War3Canvas_<pid>（运行时每帧在游戏画指针前画，指针盖在上面）", "共享内存")
    def canvas(self):
        """画板：往游戏画面上画文字框、面板、进度条、图片、地上的圈和路线（openwar3.canvas.Canvas）。
        运行时自己画，不建游戏句柄、不改游戏状态 —— 多人局也安全；样式随意（中文、圆角、半透明）。"""
        if getattr(self, "_canvas", None) is None:
            from .canvas import Canvas
            self._canvas = Canvas(self)
        return self._canvas

    @api("control", "verified", "PostMessage WM_KEYDOWN/UP 空格到游戏窗口（不抢焦点）", "窗口消息")
    def press_to_continue(self) -> bool:
        """在「按下任意键以继续」的载入画面上按一下空格。很多 RPG / 剧情地图载入完要按键才开始（09-24 WarChasers 实测：
        不按就一直停在载入画面，游戏时钟 0、快车道不排空）。openwar3.run 等进局时自己会按，一般不用手调。"""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
                found.append(hwnd)
            return True

        user32.EnumWindows(each, 0)
        down = 1 | (0x39 << 16)                  # lParam：重复 1 次、扫描码 0x39（空格）
        up = down | (3 << 30)                    # 抬起：bit30 之前是按下的、bit31 正在抬起
        for hwnd in found:
            user32.PostMessageW(hwnd, 0x0100, 0x20, down)      # WM_KEYDOWN 空格
            user32.PostMessageW(hwnd, 0x0101, 0x20, up)        # WM_KEYUP
        return bool(found)

    @property
    @api("observe", "verified", "地图文件（启动器 --map 的路径）：w3u/w3t/w3a + wts，保护过的图读图里的 TXT", "读文件（第一次约 0.1 秒）")
    def map_data(self):
        """正在打的这张地图的数据（openwar3.mapdata.MapData）：name_of('HC07') 自定义单位/物品/技能的名字、hero_names、tooltip。
        RPG 地图的单位大多是地图自己造的，内置名字表里没有；不是启动器起的游戏（找不到地图文件）返回 None。"""
        if getattr(self, "_map_data_for", None) != self.pid:
            from . import mapdata
            try:
                self._map_data = mapdata.for_pid(self.pid)
            except Exception:  # noqa: BLE001 - 地图文件读坏了不该让 bot 起不来
                self._map_data = None
            self._map_data_for = self.pid
        return self._map_data

    # ── 沙盒：JASS 通道（09-24，RPG 辅助 / 玩伴）─────────────────────
    @property
    @api("sandbox", "verified", "W3P 70 jass（运行时按名字查 native 表，1291 个）", "快车道")
    def jass(self):
        """按名字调任意 JASS native：g.jass.CreateUnit(g.jass.Player(1), "Hpal", x, y, 270.0)。
        参数 I/R/B/S/H 自动转换（单位/物品对象直接传），多人局里只能调只读的。详见 openwar3/jass.py 和 docs/COMPANION_ZH.md。"""
        if getattr(self, "_jass", None) is None:
            from .jass import Jass
            self._jass = Jass(self)
        return self._jass

    @api("sandbox", "verified", "JASS GetPlayerController / GetPlayerSlotState / IsPlayerAlly", "快车道")
    def player_slots(self) -> list[dict]:
        """16 个玩家槽：controller（user 真人 / computer / neutral…）、state（empty / playing / left）、human、me、ally（和我是不是盟友）。
        RPG 地图里找空槽放玩伴、判断是不是单人局都用它。"""
        return self.jass.player_slots()

    @api("sandbox", "verified", "JASS CreateUnit + W3P 72 句柄 -> 单位", "快车道")
    def spawn(self, code: str, x: float, y: float, player: int | None = None, facing: float = 270.0):
        """在 (x,y) 造一个单位（player 默认本机玩家），返回快照里的单位（等下一次世界发布，约 50 ms）；造不出来返回 None。
        返回的单位多一个属性 jass_handle。⚠ 只在单人局能用（多人局不同步）。"""
        j = self.jass
        p = j.GetLocalPlayer() if player is None else j.Player(int(player))
        h = j.CreateUnit(p, code, float(x), float(y), float(facing))
        if not h:
            return None
        u = j.unit_of(h)
        if u is not None:
            try:
                u.jass_handle = h
            except AttributeError:
                pass
        return u

    @api("sandbox", "verified", "JASS SetPlayerAlliance", "快车道")
    def set_alliance(self, a: int, b: int, allied: bool = True, vision: bool = True, control: bool = False,
                     xp: bool = False, both: bool = True) -> None:
        """设玩家 a 对 b 的同盟关系：allied = 不互相攻击 + 互相求援；vision 共享视野；control 共享单位控制（b 能指挥 a 的单位）；
        xp 分享经验。both=True 两个方向一起设（control 只设 a -> b）。"""
        j = self.jass
        pa, pb = j.Player(int(a)), j.Player(int(b))
        pairs = [(pa, pb), (pb, pa)] if both else [(pa, pb)]
        for x, y in pairs:
            for k in ("passive", "help_request", "help_response"):
                j.SetPlayerAlliance(x, y, _ALLY[k], allied)
            j.SetPlayerAlliance(x, y, _ALLY["shared_vision"], vision)
            j.SetPlayerAlliance(x, y, _ALLY["shared_xp"], xp)
        j.SetPlayerAlliance(pa, pb, _ALLY["shared_control"], control)

    @api("sandbox", "verified", "JASS SetPlayerName", "快车道")
    def set_player_name(self, player: int, name: str) -> None:
        """改玩家名字（计分板、聊天、盟友面板里显示的那个）。给玩伴起名字用。"""
        self.jass.SetPlayerName(self.jass.Player(int(player)), name)

    @api("sandbox", "verified", "JASS DisplayTimedTextToPlayer", "快车道")
    def show_text(self, text: str, seconds: float = 6.0, player: int | None = None) -> None:
        """屏幕左下角显示一行字（地图触发器用的那种文字），默认给本机玩家看。支持 |cffRRGGBB 颜色码。"""
        j = self.jass
        p = j.GetLocalPlayer() if player is None else j.Player(int(player))
        j.DisplayTimedTextToPlayer(p, 0.0, 0.0, float(seconds), text)

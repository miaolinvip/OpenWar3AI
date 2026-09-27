# -*- coding: utf-8 -*-
"""W3P v2 推送式信息层的读取器（协议见 native/src/ui_control_protocol.h 的 War3WorldBlock / War3TreeBlock / War3EventRing）。

    w = World(pid)
    s = w.read()             # 一份完整的世界：全部玩家资源、所有单位（含蓝/订单目标/任务目标/英雄等级/技能冷却/buff/背包）、地上物品、时钟
    ts = w.trees()           # 树（每 2 秒刷新）
    evs = w.events()         # 上次调用之后的新事件（出生/死亡/掉血/换订单/升级/物品出现消失）

零等待：运行时在游戏线程上采好推进共享内存，这里只是拷贝 + 解析，不往游戏线程排队。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import struct
import time
from dataclasses import dataclass, field
from functools import lru_cache

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]
FILE_MAP_READ, FILE_MAP_ALL = 0x0004, 0x000F001F

# ── 协议常量（tests/test_w3world.py 从头文件解析核对）──
WORLD_MAGIC, TREES_MAGIC, EVENTS_MAGIC = 0x444C5257, 0x45455254, 0x544E5645
LAYOUT = 1
MAX_UNITS, MAX_DETAILS, MAX_ITEMS, PLAYERS = 1024, 256, 256, 16
N_ABIL, N_BUFF = 12, 8
MAX_TREES, MAX_EVENTS = 4096, 8192
HDR = 96
PLAYER_SIZE = 32
UNIT_SIZE = 112
ABIL_SIZE = 16
DETAIL_SIZE = 16 + ABIL_SIZE * N_ABIL + 4 * N_BUFF + 24 + 24
ITEM_SIZE = 32
OFF_PLAYERS = HDR
OFF_UNITS = OFF_PLAYERS + PLAYER_SIZE * PLAYERS
OFF_DETAILS = OFF_UNITS + UNIT_SIZE * MAX_UNITS
OFF_ITEMS = OFF_DETAILS + DETAIL_SIZE * MAX_DETAILS
WORLD_SIZE = OFF_ITEMS + ITEM_SIZE * MAX_ITEMS
OFF_EXT, EXT_SIZE, EXT_MAGIC = WORLD_SIZE, 128, 0x31545845    # 块尾扩展区（'EXT1'）：游戏内时间、昼夜流速
OFF_PRODS, PROD_SIZE, MAX_PRODS = OFF_EXT + EXT_SIZE, 64, 128   # 生产表（09-24），条数在扩展区 +12
_PROD = struct.Struct("<3I7I3f3I")
TREE_HDR, TREE_SIZE = 32, 32
TREES_SIZE = TREE_HDR + TREE_SIZE * MAX_TREES
EV_HDR, EV_SIZE = 32, 64
EVENTS_SIZE = EV_HDR + EV_SIZE * MAX_EVENTS
H_SEQ, H_REQ_PERIOD = 8, 48

# 单位 flags（War3WorldUnitFlags）
ALIVE, HERO, ORDER_KNOWN, TASK_KNOWN, HAS_TASK, TASK_TARGET, TASK_POINT, ORDER_TARGET, DETAIL = (1 << i for i in range(9))
EVENT_KINDS = {1: "unit.appeared", 2: "unit.died", 3: "unit.removed", 4: "unit.damaged", 5: "order.changed",
               6: "hero.levelup", 7: "owner.changed", 8: "item.appeared", 9: "item.removed",
               10: "damage", 11: "killed", 12: "production.done",
               # 09-25 补齐：施法（技能开始冷却）、玩家离开 / 被判负、本机选择变化、屏幕消息（提示 / 聊天）
               13: "spell.cast", 14: "player.left", 15: "selection.changed", 16: "message",
               # 09-25 界面与输入（要先开输入：用一次 g.ui，或画 clickable=True 的画板项）
               20: "ui.click", 21: "ui.hover", 22: "hotkey", 23: "mouse.world",
               100: "game.started", 101: "game.ended"}
SLOT_STATES = {0: "empty", 1: "playing", 2: "left"}
MOD_CTRL, MOD_SHIFT, MOD_ALT = 1, 2, 4
MSGS_MAGIC, MSG_SLOTS, MSG_SIZE, MSG_HDR = 0x5347534D, 128, 256, 32
PROD_KINDS = {1: "queue", 2: "construction", 3: "upgrade"}
# damage/killed 事件的 b 字段：低 8 位 = 伤害类型（JASS DAMAGE_TYPE_*）、次 8 位 = 攻击类型（JASS ATTACK_TYPE_*）。
# 攻击类型用物编里的叫法（JASS 的 ATTACK_TYPE_MELEE 就是物编的 normal）。09-23 实测：步兵 normal、火枪手 pierce、普攻伤害类型 normal
ATTACK_TYPES = ["spell", "normal", "pierce", "siege", "magic", "chaos", "hero"]
DAMAGE_TYPES = {0: "unknown", 4: "normal", 5: "enhanced", 8: "fire", 9: "cold", 10: "lightning", 11: "poison",
                12: "disease", 13: "divine", 14: "magic", 15: "sonic", 16: "acid", 17: "force", 18: "death", 19: "mind",
                20: "plant", 21: "defensive", 22: "demolition", 23: "slow_poison", 24: "spirit_link",
                25: "shadow_strike", 26: "universal"}
MAP_MAGIC = 0x4250414D
MAP_HDR = 160
MAP_MAX = 256 * 256
MAP_SIZE = MAP_HDR + MAP_MAX
NO_HANDLE = 0xFFFFFFFF

_UNIT = struct.Struct("<6I 6f I 2I 2f 2I 2f 3I 2I 2I")     # 112 字节
_PLAYER = struct.Struct("<8I")
_ITEM = struct.Struct("<4I3fI")
_TREE = struct.Struct("<4I3fI")
_EVENT = struct.Struct("<iII5I2I3fI2I")
_HEADER = struct.Struct("<3I I I I I I I I I I i 3I 3I 5I")   # 最后 5 个是分段耗时 scan/detail/players/diff/trees


_CHAT = re.compile(r"^(?:\[(?P<channel>[^\]]{1,24})\]\s*)?\|c[0-9a-fA-F]{8}(?P<sender>[^|]+?):\s*\|r(?P<text>.*)$", re.S)
_COLOR = re.compile(r"\|c[0-9a-fA-F]{8}|\|r")


def parse_chat(text: str) -> dict | None:
    """聊天消息框里的一行 -> {'channel': '所有人' / '盟友' / 'All' / None, 'sender': 玩家名, 'text': 内容}；不是聊天返回 None。
    09-25 实测单人局：'[所有人] |cffff0303War3AI: |rchat test 42'（名字带玩家颜色码）。"""
    m = _CHAT.match(text or "")
    if not m:
        return None
    return {"channel": m.group("channel"), "sender": m.group("sender").strip(), "text": _COLOR.sub("", m.group("text")).strip()}


def fourcc(v: int) -> str:
    return bytes(((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255)).decode("latin-1", "replace")


def rawcode(code: str) -> int:
    b = code.encode("latin-1")
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


@dataclass
class Ability:
    code: str
    level: int
    cooldown: float          # 剩余冷却（游戏秒），0 = 能放
    flags: int


@dataclass
class Unit:
    index: int
    addr: int
    handle_lo: int
    handle_hi: int
    type: str
    owner: int
    flags: int
    x: float
    y: float
    hp: float
    hp_max: float
    mana: float
    mana_max: float
    order: int | None
    order_target: tuple | None
    order_x: float
    order_y: float
    task_target: tuple | None
    task_x: float | None
    task_y: float | None
    level: int
    xp: int
    skill_points: int
    first_seen: int
    visible_mask: int = 0                            # bit p = 玩家 p 此刻看得见它（P5；只算场上有单位的 0..11 号）
    abilities: list = field(default_factory=list)   # [Ability]（有细节的单位才有）
    buffs: list = field(default_factory=list)       # ['Bslo', ...]
    items: list = field(default_factory=list)       # 6 格物品类型，空格 None
    item_addrs: list = field(default_factory=list)
    has_detail: bool = False

    @property
    def alive(self) -> bool:
        return bool(self.flags & ALIVE)

    @property
    def is_hero(self) -> bool:
        return bool(self.flags & HERO)

    @property
    def has_task(self) -> bool | None:
        return bool(self.flags & HAS_TASK) if self.flags & TASK_KNOWN else None

    @property
    def identity(self) -> tuple[int, int, int]:
        return self.addr, self.handle_lo, self.handle_hi

    @property
    def handle(self) -> tuple[int, int]:
        return self.handle_lo, self.handle_hi

    def ability(self, code: str) -> Ability | None:
        return next((a for a in self.abilities if a.code == code), None)

    def visible_to(self, player: int) -> bool:
        return bool(self.visible_mask >> player & 1)


@dataclass
class Player:
    id: int
    gold: int
    lumber: int
    food_used: int
    food_cap: int
    gold_gathered: int
    lumber_gathered: int
    race: int
    known: bool


@dataclass
class Item:
    addr: int
    handle_lo: int
    handle_hi: int
    type: str
    x: float
    y: float
    life: float


@dataclass
class Tree:
    addr: int
    handle_lo: int
    handle_hi: int
    type: str
    x: float
    y: float
    life: float

    @property
    def alive(self) -> bool:
        return self.life > 0


@dataclass
class Event:
    seq: int
    kind: str
    clock: float
    addr: int
    handle: tuple
    type: str
    owner: int
    a: int
    b: int
    x: float
    y: float
    value: float
    extra: int = 0

    # damage / killed 事件的便捷字段（来源 = a 地址；用 WorldState.unit_by_addr 换成单位）
    @property
    def source_addr(self) -> int:
        return self.a if self.kind in ("damage", "killed") else 0

    @property
    def raw_damage(self) -> float:
        return struct.unpack("<f", struct.pack("<I", self.extra))[0] if self.kind in ("damage", "killed") else 0.0

    @property
    def attack_type(self) -> str:
        i = (self.b >> 8) & 0xFF
        return ATTACK_TYPES[i] if i < len(ATTACK_TYPES) else str(i)

    @property
    def damage_type(self) -> str:
        i = self.b & 0xFF
        return DAMAGE_TYPES.get(i, str(i))

    # production.done 事件：单位 = 建筑，a = 做完的四字码，b = 1 队列 / 2 建造 / 3 升级，value = 用了多少游戏秒
    @property
    def done_code(self) -> str | None:
        return fourcc(self.a) if self.kind == "production.done" else None

    @property
    def done_kind(self) -> str | None:
        """'training'（训练出单位 / 英雄，含复活）/ 'research'（科技，四字码 R 开头）/ 'construction' / 'upgrade'。"""
        if self.kind != "production.done":
            return None
        if self.b == 2:
            return "construction"
        if self.b == 3:
            return "upgrade"
        return "research" if fourcc(self.a).startswith("R") else "training"

    # ── 09-25 新事件的便捷字段 ──
    @property
    def spell(self) -> str | None:
        """spell.cast：技能四字码（'AHhb' 圣光术……）；等级在 b，冷却秒在 value，施法点在 x,y。"""
        return fourcc(self.a) if self.kind == "spell.cast" else None

    @property
    def target_handle_lo(self) -> int:
        """spell.cast：目标句柄低位（订单 / 任务对着单位时；0 = 对着点或没有）。"""
        return self.extra if self.kind == "spell.cast" else 0

    @property
    def player(self) -> int | None:
        """player.left：哪个玩家（0..11）。"""
        return self.a if self.kind == "player.left" else None

    @property
    def item_id(self) -> int:
        """ui.click / ui.hover：画板项 id（hover 的 0 = 移出）。"""
        return self.a if self.kind in ("ui.click", "ui.hover") else 0

    @property
    def button(self) -> str | None:
        """ui.click / mouse.world：'left' / 'right'（ui.click 的键在 b，mouse.world 的在 a）。"""
        if self.kind == "ui.click":
            return "left" if self.b == 1 else "right"
        if self.kind == "mouse.world":
            return "left" if self.a == 1 else "right"
        return None

    @property
    def hotkey_id(self) -> int:
        return self.a if self.kind == "hotkey" else 0

    @property
    def mods(self) -> set:
        """ui.click / hotkey / mouse.world 按下时的修饰键：{'ctrl', 'shift', 'alt'} 的子集。"""
        if self.kind not in ("ui.click", "ui.hover", "hotkey", "mouse.world"):
            return set()
        return {n for bit, n in ((MOD_CTRL, "ctrl"), (MOD_SHIFT, "shift"), (MOD_ALT, "alt")) if self.extra & bit}

    @property
    def message_seq(self) -> int:
        """message：消息序号（World.message(seq) 取全文）。"""
        return self.a if self.kind == "message" else 0


@dataclass
class Production:
    """一座建筑正在做的事（生产表的一条）。进度 = elapsed / duration。"""
    building: tuple             # 建筑句柄对（用 WorldState.by_handle 换成单位）
    kind: str                   # 'queue'（训练/研究/英雄）/ 'construction'（在建）/ 'upgrade'（升级中）
    queue: list                 # 四字码；[0] 是正在做的。建造 = [建筑类型]；升级 = [目标类型]
    duration: float             # 这一格总时长（游戏秒）；0 = 卡住没开始（人口 / 资源不够）
    elapsed: float              # 已经做了多少游戏秒
    speed: float
    blocked: bool               # 有排队但没开始（多半是人口不够）
    exact: bool                 # 开始时刻是运行时亲眼看到的；False = 从中途看到，elapsed 偏小
    from_type: str | None = None   # 升级：原类型

    @property
    def progress(self) -> float | None:
        """0..1；卡住 / 时长未知 = None。"""
        if self.blocked or self.duration <= 0:
            return None
        return min(1.0, self.elapsed / self.duration)

    @property
    def remaining(self) -> float | None:
        """这一格还要多少游戏秒。"""
        if self.blocked or self.duration <= 0:
            return None
        return max(0.0, self.duration - self.elapsed)


@dataclass
class MapInfo:
    """地图块：地形格子（bit0 不能走、bit1 不能盖）+ 边界 + 出生点。"""
    epoch: int
    ready: bool
    cell: int
    cols: int
    rows: int
    min_x: float
    min_y: float
    bounds: tuple               # (minX, maxX, minY, maxY)
    starts: list                # [(x, y)] 0..11 号出生点（0,0 = 这个槽没有）
    cells: bytes

    def at(self, x: float, y: float) -> int | None:
        c, r = int((x - self.min_x) // self.cell), int((y - self.min_y) // self.cell)
        if 0 <= c < self.cols and 0 <= r < self.rows:
            return self.cells[r * self.cols + c]
        return None

    def walkable(self, x: float, y: float) -> bool:
        v = self.at(x, y)
        return v is not None and not v & 1

    def buildable(self, x: float, y: float) -> bool:
        v = self.at(x, y)
        return v is not None and not v & 2


@dataclass
class WorldState:
    publish_count: int
    real_ms: int
    clock: float               # 引擎游戏时钟（秒）
    epoch: int
    me: int
    in_game: bool
    speed_pct: int
    period_ms: int
    cost_us: int
    cost_max_us: int
    event_seq: int
    players: list
    units: list
    items: list
    by_handle: dict = field(default_factory=dict)
    cost_parts: dict = field(default_factory=dict)   # 运行时采集的分段耗时（微秒）
    time_of_day: float | None = None   # 游戏内时间（小时 0..24；6~18 白天）。老运行时 / 读不到 = None
    tod_scale: float | None = None     # 昼夜流速倍数（默认 1）
    production: list = field(default_factory=list)   # [Production]：正在训练/研究/建造/升级的建筑（09-24）
    # 09-25：本机玩家的选择（地址，对得上 units[].addr；引擎记的总数可能多于 12）和 12 个玩家的槽状态
    selection_addrs: list = field(default_factory=list)
    selection_count: int = 0
    selection_leader: int = 0
    slot_states: list = field(default_factory=list)   # ['playing' / 'left' / 'empty'] × 12；老运行时 = []

    @property
    def selected(self) -> list:
        """本机玩家现在选中的单位（主单位排第一）。"""
        units = [self.unit_by_addr(a) for a in self.selection_addrs]
        units = [u for u in units if u is not None]
        if self.selection_leader:
            units.sort(key=lambda u: u.addr != self.selection_leader)
        return units

    def production_of(self, building):
        """这座建筑正在做的事（Production），没在做返回 None。building 给单位或句柄对都行。"""
        h = building if isinstance(building, tuple) else (building.handle_lo, building.handle_hi)
        return next((p for p in self.production if p.building == h), None)

    @property
    def is_night(self) -> bool | None:
        """现在是不是夜里（18 点到次日 6 点）：野怪睡觉、视野变短、暗夜精灵隐身。读不到返回 None。"""
        if self.time_of_day is None:
            return None
        return self.time_of_day >= 18.0 or self.time_of_day < 6.0

    # 和 w3state.Snapshot 同名的便捷属性（老代码能直接换用）
    @property
    def resources_known(self) -> bool:
        return 0 <= self.me < PLAYERS and self.players[self.me].known

    @property
    def gold(self):
        return self.players[self.me].gold if self.resources_known else None

    @property
    def lumber(self):
        return self.players[self.me].lumber if self.resources_known else None

    @property
    def food_used(self):
        return self.players[self.me].food_used if self.resources_known else None

    @property
    def food_cap(self):
        return self.players[self.me].food_cap if self.resources_known else None

    @property
    def mine(self) -> list:
        return [u for u in self.units if u.owner == self.me and u.alive]

    @property
    def foes(self) -> list:
        return [u for u in self.units if u.alive and u.owner != self.me and u.owner < 16]

    @property
    def creeps(self) -> list:
        return [u for u in self.units if u.alive and u.owner == 12]

    @property
    def heroes(self) -> list:
        return [u for u in self.mine if u.is_hero]

    def unit_by_handle(self, handle) -> Unit | None:
        """按句柄对找单位（订单目标 / 任务目标给的就是句柄对）。"""
        return self.by_handle.get(tuple(handle)) if handle else None

    def unit_by_addr(self, addr: int) -> Unit | None:
        """按地址找（伤害/击杀事件的来源给的是地址）。⚠ 地址会复用，只适合"刚发生的事"。"""
        if not hasattr(self, "_by_addr"):
            self._by_addr = {u.addr: u for u in self.units}
        return self._by_addr.get(addr)


class WorldUnavailable(RuntimeError):
    pass


class _Mapping:
    def __init__(self, name: str, size: int, writable: bool = False):
        self.h = _k32.OpenFileMappingW(FILE_MAP_ALL if writable else FILE_MAP_READ, False, name)
        if not self.h:
            raise WorldUnavailable(f"{name} 打不开（运行时太旧，或游戏还没装好）")
        self.p = _k32.MapViewOfFile(self.h, FILE_MAP_ALL if writable else FILE_MAP_READ, 0, 0, size)
        if not self.p:
            _k32.CloseHandle(self.h)
            raise WorldUnavailable(f"{name} MapViewOfFile 失败")
        self.size = size

    def u32(self, off: int) -> int:
        return ctypes.c_uint32.from_address(self.p + off).value

    def i32(self, off: int) -> int:
        return ctypes.c_int32.from_address(self.p + off).value

    def copy(self, off: int, n: int) -> bytes:
        return ctypes.string_at(self.p + off, n)

    def close(self):
        if self.p:
            _k32.UnmapViewOfFile(self.p)
            _k32.CloseHandle(self.h)
            self.p = None


def _seqlocked(m: _Mapping, seq_off: int, grab, retries: int = 20):
    for _ in range(retries):
        s1 = m.i32(seq_off)
        if s1 & 1:
            time.sleep(0)
            continue
        data = grab()
        if m.i32(seq_off) == s1:
            return data
    return None


def parse_units(ubytes: bytes, dbytes: bytes, n: int, nd: int) -> list:
    units = []
    for i, f in enumerate(_UNIT.iter_unpack(ubytes[:n * UNIT_SIZE])):
        (addr, hlo, hhi, typ, owner, flags, x, y, hp, hpm, mana, manam, order, otlo, othi, ox, oy,
         ttlo, tthi, tx, ty, level, xp, sp, detail, first, vis, _r1) = f
        u = Unit(i, addr, hlo, hhi, fourcc(typ), owner, flags, x, y, hp, hpm, mana, manam,
                 order if flags & ORDER_KNOWN else None,
                 (otlo, othi) if flags & ORDER_TARGET else None, ox, oy,
                 (ttlo, tthi) if flags & TASK_TARGET else None,
                 tx if flags & TASK_POINT else None, ty if flags & TASK_POINT else None,
                 level, xp, sp, first, vis)
        if flags & DETAIL and detail < nd:
            base = detail * DETAIL_SIZE
            _ui, na, nb, _ni = struct.unpack_from("<4I", dbytes, base)
            for k in range(min(na, N_ABIL)):
                code, lv, fl, cd = struct.unpack_from("<3If", dbytes, base + 16 + k * ABIL_SIZE)
                u.abilities.append(Ability(fourcc(code), lv, cd, fl))
            off = base + 16 + ABIL_SIZE * N_ABIL
            u.buffs = [fourcc(c) for c in struct.unpack_from(f"<{N_BUFF}I", dbytes, off)[:min(nb, N_BUFF)]]
            its = struct.unpack_from("<6I", dbytes, off + 4 * N_BUFF)
            u.items = [fourcc(c) if c else None for c in its]
            u.item_addrs = list(struct.unpack_from("<6I", dbytes, off + 4 * N_BUFF + 24))
            u.has_detail = True
        units.append(u)
    return units


def parse_prods(raw: bytes, units: list) -> list:
    """生产表字节 -> [Production]（单位下标超界的丢掉）。"""
    out = []
    for i in range(len(raw) // PROD_SIZE):
        (ui, kind, count, *rest) = _PROD.unpack_from(raw, i * PROD_SIZE)
        codes, (dur, elapsed, speed), (flags, from_type, _r) = rest[:7], rest[7:10], rest[10:13]
        if ui >= len(units):
            continue
        u = units[ui]
        out.append(Production((u.handle_lo, u.handle_hi), PROD_KINDS.get(kind, str(kind)),
                              [fourcc(c) for c in codes[:min(count, 7)]], dur, elapsed, speed,
                              bool(flags & 1), bool(flags & 2), fourcc(from_type) if from_type else None))
    return out


@lru_cache(maxsize=1)
def tree_types() -> frozenset:
    """哪些可破坏物是树（DestructableData.slk 的 targType 含 tree）。读不到返回空集（=不过滤）。"""
    try:
        import w3paths
        from w3slk import read_slk
        rows = read_slk(w3paths.game_data("DestructableData.slk"))
        return frozenset(r["DestructableID"] for r in rows if "tree" in (r.get("targType") or ""))
    except Exception:  # noqa: BLE001
        return frozenset()


_SHARED_WORLD: dict = {}


def shared_world(pid: int) -> "World":
    """本进程共用的一个 World（老接口 w3cmd 用；事件游标是各 World 自己的，要收事件就自己开一个）。"""
    w = _SHARED_WORLD.get(pid)
    if w is None:
        w = _SHARED_WORLD[pid] = World(pid)
    return w


class World:
    """连到一个游戏实例的推送式信息层。一个进程开一个就够。"""

    def __init__(self, pid: int):
        self.pid = pid
        # 块尾扩展（09-23 夜 游戏内时间、09-24 生产表）：老运行时的块短，按能映射的最大那一档退
        self._has_ext = self._has_prods = False
        for size, ext, prods in ((OFF_PRODS + PROD_SIZE * MAX_PRODS, True, True), (WORLD_SIZE + EXT_SIZE, True, False),
                                 (WORLD_SIZE, False, False)):
            try:
                self._w = _Mapping(f"Local\\War3World_{pid}", size, writable=True)
                self._has_ext, self._has_prods = ext, prods
                break
            except WorldUnavailable:
                if size == WORLD_SIZE:
                    raise
        if self._w.u32(0) != WORLD_MAGIC or self._w.u32(4) != LAYOUT:
            raise WorldUnavailable(f"世界块协议不对：magic={self._w.u32(0):08X} layout={self._w.u32(4)}")
        self._t = _Mapping(f"Local\\War3Trees_{pid}", TREES_SIZE)
        self._e = _Mapping(f"Local\\War3Events_{pid}", EVENTS_SIZE)
        try:
            self._m = _Mapping(f"Local\\War3Map_{pid}", MAP_SIZE)
        except WorldUnavailable:
            self._m = None                      # 旧运行时没有地图块
        try:
            self._msgs = _Mapping(f"Local\\War3Msgs_{pid}", MSG_HDR + MSG_SIZE * MSG_SLOTS)   # 09-25 屏幕消息
        except WorldUnavailable:
            self._msgs = None
        self._msg_cursor = self._msgs.i32(8) if self._msgs is not None else 0
        self._map_cache = None
        self._cursor = self._e.i32(8)          # 从现在开始收事件（要历史就 rewind）
        self.dropped = 0

    def close(self):
        for m in (self._w, self._t, self._e, self._m, self._msgs):
            if m is not None:
                m.close()

    # ── 09-25 屏幕消息（游戏提示「需要更多的农场」、聊天、系统消息）──
    def message(self, seq: int) -> dict | None:
        """按序号取一条（message 事件的 a）。已被覆盖 / 老运行时返回 None。
        返回 {'seq', 'text', 'frame'（消息框编号：聊天和提示在不同的框）, 'repeat', 'game_ms'}。"""
        m = self._msgs
        if m is None or seq <= 0 or m.u32(0) != MSGS_MAGIC:
            return None
        off = MSG_HDR + ((seq - 1) % MSG_SLOTS) * MSG_SIZE
        raw = m.copy(off, MSG_SIZE)
        s, game_ms, frame, repeat = struct.unpack_from("<i3I", raw, 0)
        if s != seq:
            return None
        text = raw[16:].split(b"\0", 1)[0].decode("utf-8", "replace")
        return {"seq": s, "text": text, "frame": frame, "repeat": repeat, "game_ms": game_ms}

    def messages(self, limit: int = 128) -> list:
        """上次调用之后的新消息（各 World 自己的游标，互不影响）。"""
        m = self._msgs
        if m is None or m.u32(0) != MSGS_MAGIC:
            return []
        head = m.i32(8)
        start = max(self._msg_cursor, head - MSG_SLOTS)
        out = []
        for seq in range(start + 1, head + 1):
            msg = self.message(seq)
            if msg is not None:
                out.append(msg)
            if len(out) >= limit:
                head = seq
                break
        self._msg_cursor = head
        return out

    def map(self) -> MapInfo | None:
        """这一局的地图块（算好了才返回；开局后运行时要几秒分批算完）。结果按 epoch 缓存。"""
        m = self._m
        if m is None or m.u32(0) != MAP_MAGIC:
            return None
        hdr = _seqlocked(m, 4, lambda: m.copy(0, MAP_HDR))
        if hdr is None:
            return None
        (_mg, _seq, epoch, state, cell, cols, rows, done) = struct.unpack_from("<8I", hdr, 0)
        if state != 2:
            return None
        if self._map_cache is not None and self._map_cache.epoch == epoch:
            return self._map_cache
        min_x, min_y, bx0, bx1, by0, by1 = struct.unpack_from("<6f", hdr, 32)
        n_start = struct.unpack_from("<I", hdr, 56)[0]
        starts = [struct.unpack_from("<2f", hdr, 60 + 8 * i) for i in range(12)]
        cells = m.copy(MAP_HDR, cols * rows)
        self._map_cache = MapInfo(epoch, True, cell, cols, rows, min_x, min_y, (bx0, bx1, by0, by1),
                                  starts[:max(n_start, 0)], cells)
        return self._map_cache

    def set_period(self, ms: int) -> None:
        """请求发布周期（16..1000 毫秒；0 = 默认 50）。全机共用一个值，最后写的算。"""
        ctypes.c_int32.from_address(self._w.p + H_REQ_PERIOD).value = int(ms)

    def read(self) -> WorldState | None:
        """最新一份世界状态。运行时没发布新的（发布计数没变）就返回上一次解析好的那份 ——
        一拍里 snapshot / events(公平模式) / selection / 模组各读一次，以前每次都全量解析 1024 个单位。
        ⚠ 返回的对象是共用的，别改它（要改先 dataclasses.replace）。"""
        m = self._w
        pub_now = m.u32(12)
        if pub_now and pub_now == self.__dict__.get("_last_pub") and self.__dict__.get("_last_state") is not None:
            return self._last_state

        def grab():
            hdr = m.copy(0, HDR)
            (_mg, _ly, _seq, pub, real, _gms, clock, epoch, me, ingame, speed, period, _req,
             nu, nd, ni, cost, costmax, evseq, *_r) = _HEADER.unpack(hdr)
            nu, nd, ni = min(nu, MAX_UNITS), min(nd, MAX_DETAILS), min(ni, MAX_ITEMS)
            return (hdr, m.copy(OFF_PLAYERS, PLAYER_SIZE * PLAYERS), m.copy(OFF_UNITS, UNIT_SIZE * nu),
                    m.copy(OFF_DETAILS, DETAIL_SIZE * nd), m.copy(OFF_ITEMS, ITEM_SIZE * ni), nu, nd, ni,
                    m.copy(OFF_EXT, EXT_SIZE) if self._has_ext else b"",
                    m.copy(OFF_PRODS, PROD_SIZE * min(m.u32(OFF_EXT + 12), MAX_PRODS))
                    if self._has_prods and m.u32(OFF_EXT) == EXT_MAGIC else b"")
        got = _seqlocked(m, H_SEQ, grab)
        if got is None:
            return None
        hdr, pb, ub, db, ib, nu, nd, ni, ext, prb = got
        (_mg, _ly, _seq, pub, real, _gms, clock, epoch, me, ingame, speed, period, _req,
         _nu, _nd, _ni, cost, costmax, evseq, *parts) = _HEADER.unpack(hdr)
        if pub == 0:
            return None
        players = [Player(i, *(_PLAYER.unpack_from(pb, i * PLAYER_SIZE)[:7]), bool(_PLAYER.unpack_from(pb, i * PLAYER_SIZE)[7] & 1))
                   for i in range(PLAYERS)]
        units = parse_units(ub, db, nu, nd)
        items = [Item(a, lo, hi, fourcc(t), x, y, life) for a, lo, hi, t, x, y, life, _ in _ITEM.iter_unpack(ib)]
        st = WorldState(pub, real, clock / 1000.0, epoch, me, bool(ingame), speed, period, cost, costmax, evseq,
                        players, units, items)
        st.by_handle = {(u.handle_lo, u.handle_hi): u for u in units}
        st.cost_parts = dict(zip(("scan", "detail", "players", "diff", "trees"), parts))
        if len(ext) >= 12 and struct.unpack_from("<I", ext, 0)[0] == EXT_MAGIC:
            tod, scale = struct.unpack_from("<2f", ext, 4)
            if tod >= 0:
                st.time_of_day, st.tod_scale = tod, scale
            if len(ext) >= 76:                 # 09-25：选择 + 槽状态（老运行时这里是 0）
                cnt, leader = struct.unpack_from("<2I", ext, 16)
                addrs = struct.unpack_from("<12I", ext, 24)
                slots = struct.unpack_from("<I", ext, 72)[0]
                st.selection_count, st.selection_leader = cnt, leader
                st.selection_addrs = [a for a in addrs if a]
                if slots:
                    st.slot_states = [SLOT_STATES.get((slots >> (2 * p)) & 3, "?") for p in range(12)]
        st.production = parse_prods(prb, units)
        self._last_pub, self._last_state = pub, st
        return st

    def trees(self, only_trees: bool = True, alive: bool = True) -> list:
        m = self._t
        if m.u32(0) != TREES_MAGIC:
            return []
        got = _seqlocked(m, 4, lambda: m.copy(0, TREE_HDR + TREE_SIZE * min(m.u32(12), MAX_TREES)))
        if got is None:
            return []
        n = min(struct.unpack_from("<I", got, 12)[0], MAX_TREES)
        kinds = tree_types() if only_trees else frozenset()
        out = []
        for a, lo, hi, t, x, y, life, _ in _TREE.iter_unpack(got[TREE_HDR:TREE_HDR + n * TREE_SIZE]):
            code = fourcc(t)
            if kinds and code not in kinds:
                continue
            if alive and not life > 0:
                continue
            out.append(Tree(a, lo, hi, code, x, y, life))
        return out

    def rewind(self, n: int = MAX_EVENTS) -> None:
        """让下一次 events() 从最近 n 条开始（环里最多留 8192 条）。"""
        self._cursor = max(0, self._e.i32(8) - n)

    def events(self, limit: int = 4096) -> list:
        """上次调用之后的新事件（按序号）。消费太慢被覆盖的计入 self.dropped。"""
        m = self._e
        head = m.i32(8)
        out = []
        if head - self._cursor > MAX_EVENTS:
            self.dropped += head - self._cursor - MAX_EVENTS
            self._cursor = head - MAX_EVENTS
        seq = self._cursor
        while seq < head and len(out) < limit:
            seq += 1
            off = EV_HDR + ((seq - 1) % MAX_EVENTS) * EV_SIZE
            raw = m.copy(off, EV_SIZE)
            (s, kind, clock, addr, hlo, hhi, typ, owner, a, b, x, y, value, extra, _r1, _r2) = _EVENT.unpack(raw)
            if s > seq:                          # 这一格已经被更新的事件覆盖了：丢了一条
                self.dropped += 1
                continue
            if s != seq:                         # 0 = 正在写 / 比期望旧 = 还没写到：下次再读
                seq -= 1
                break
            out.append(Event(s, EVENT_KINDS.get(kind, str(kind)), clock / 1000.0, addr, (hlo, hhi),
                             fourcc(typ) if typ else "", owner, a, b, x, y, value, extra))
        self._cursor = seq
        return out

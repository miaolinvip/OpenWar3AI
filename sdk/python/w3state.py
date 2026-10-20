"""w3state.py —— 读 DLL 发布到共享内存的世界状态快照。

    from w3state import State
    st = State()                 # 自己找 War3 进程
    snap = st.read()             # 约 0.3 毫秒，拿到全场单位 + 本方资源
    for u in snap.mine:          # 我方单位
        ...

为什么要有这一层
----------------
走控制通道读一次世界状态实测约 46 毫秒，而通道**每个游戏进程只有一个请求槽**
（外加一把互斥量）。想让经济、战斗、建造、捡装备各跑各的、都在毫秒级响应，
每个模块都付 46ms 还要排队，这条路走不通。

那 46 毫秒的本质是"跨进程去问游戏"。可数据本来就在游戏进程里，所以由 DLL
每 50ms 往共享内存写一份，这边只是 mmap 过来读：
    读者数量不限、互不阻塞、不占请求槽、不用锁。

★ 为什么是多进程而不是多线程
    这些模块之间**不共享状态**：各读各的快照，各下各的命令。
    分进程比分线程好三点：
      1. Python 的 GIL 在多进程下完全不是问题；
      2. 一个模块抛异常只死它自己，其它照跑 —— 要挂机几小时，这条比性能重要；
      3. 每个模块可以有自己的节奏（战斗 250ms、经济 1s、建造 3s），互不牵制。

★ seqlock：读者永远不阻塞写者
    写者跑在**游戏线程**上，被读者卡住就是卡住游戏画面。所以不能用锁：
        写：seq++（奇数）-> 写数据 -> seq++（偶数）
        读：读 seq -> 读数据 -> 再读 seq；不一致或是奇数就重读
    读到撕裂的数据代价是几微秒重读一次；用锁的代价是整个游戏卡住。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import mmap
import os
import struct
import time
from dataclasses import dataclass, replace

# ── 必须和 ui_control_protocol.h 里的 War3UiStateBlock 严格一致 ──────────
# ⚠ 这是整个文件最危险的地方：字段错位**不会报错**，只会读出垃圾数字，
#   而垃圾数字会被当成真实战况用来做决策。改了 C++ 那边就必须改这里，
#   而且 layout 版本号要一起加 —— read() 会校验它，对不上直接拒绝返回。
MAGIC = 0x54533357          # 'W3ST'
LAYOUT = 2
UNITS_MAX = 512
ORDER_KNOWN = 1
TASK_KNOWN = 2
HAS_TASK = 4
ROSTER_COMPLETE = 1
RESOURCES_KNOWN = 2

_HDR = "<9I4I8I"            # magic layout seq publishMs publishCount periodMs
                            # localPlayerId inGame unitCount / resources / epoch validity reserved[6]
_HDR_SIZE = struct.calcsize(_HDR)          # = 21 * 4 = 84
_UNIT = "<6I2f9I"           # index registryHandle address unitTypeId ownerId orderContext
                            # x y / agent handleLo handleHi hp hpMax currentOrder
                            # validity taskHandleLo taskHandleHi
_UNIT_SIZE = struct.calcsize(_UNIT)        # = 68; control-channel units remain 56
_BLOCK_SIZE = _HDR_SIZE + UNITS_MAX * _UNIT_SIZE

FILE_MAP_READ = 0x0004
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]


def _rawcode(v: int) -> str:
    """类型码是大端 FourCC：0x68666F6F -> 'hfoo'。"""
    return bytes(((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)).decode(
        "latin1", "replace"
    )


@dataclass(slots=True)
class Unit:
    index: int
    addr: int
    type: str
    owner: int
    x: float
    y: float
    hp: int
    hp_max: int
    order: int | None       # None = unknown; zero is valid only with order_known
    handle_lo: int = 0
    handle_hi: int = 0
    order_known: bool = False
    task_known: bool = False
    has_task: bool | None = None
    task_handle_lo: int | None = None
    task_handle_hi: int | None = None

    @property
    def identity(self) -> tuple[int, int, int]:
        return self.addr, self.handle_lo, self.handle_hi

    @property
    def addr_hex(self) -> str:
        return "0x%08X" % self.addr

    @property
    def is_hero(self) -> bool:
        # 引擎自己就是这么判的（ReviveHero 里看单位类型四字码的首字母是不是 'A'..'Z'）。
        return bool(self.type) and "A" <= self.type[0] <= "Z"

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass(slots=True)
class Snapshot:
    age_ms: int          # 这份快照发布到现在过了多久
    me: int              # 我们是几号玩家
    in_game: bool
    publishes: int       # 累计发布次数 —— 它不涨就是发布线断了
    gold: int | None
    lumber: int | None
    food_used: int | None
    food_cap: int | None
    units: list[Unit]
    epoch: int = 0
    roster_complete: bool = False  # Capacity check only; hidden workers may be absent.
    resources_known: bool = False

    @property
    def mine(self) -> list[Unit]:
        return [u for u in self.units if u.owner == self.me and u.hp > 0]

    @property
    def foes(self) -> list[Unit]:
        # 12 = 中立敌对（野怪）。0..11 里除我们之外的是别的玩家。
        return [u for u in self.units if u.hp > 0 and u.owner != self.me and u.owner < 16]

    @property
    def creeps(self) -> list[Unit]:
        return [u for u in self.units if u.hp > 0 and u.owner == 12]

    @property
    def heroes(self) -> list[Unit]:
        return [u for u in self.mine if u.is_hero]


class StateUnavailable(RuntimeError):
    pass


def decode_snapshot(raw: bytes, age_ms: int = 0) -> Snapshot | None:
    """Decode one already-consistent buffer; also used by offline protocol tests.

    Reject old/truncated layouts. Do not turn unavailable observations into zero,
    and do not expose a task target/type until that separate semantic is known.
    """
    if len(raw) < _HDR_SIZE:
        return None
    (magic, layout, seq, _pub_ms, pubs, _period, me, in_game, n,
     gold, lumber, food_u, food_c, epoch, flags, *_res) = struct.unpack_from(_HDR, raw)
    if magic != MAGIC or layout != LAYOUT or seq & 1 or n > UNITS_MAX:
        return None
    if len(raw) < _HDR_SIZE + n * _UNIT_SIZE:
        return None
    if in_game and (me >= 12 or epoch == 0 or n == 0):
        return None
    units = []
    for off in range(_HDR_SIZE, _HDR_SIZE + n * _UNIT_SIZE, _UNIT_SIZE):
        (idx, _rh, addr, tid, owner, _ctx, x, y, _agent, hlo, hhi,
         hp, hp_max, order, valid, task_lo, task_hi) = struct.unpack_from(_UNIT, raw, off)
        task_known = bool(valid & TASK_KNOWN)
        has_task = bool(valid & HAS_TASK)
        empty_pair = task_lo == task_hi == 0xFFFFFFFF
        assigned_pair = task_lo != 0xFFFFFFFF and task_hi != 0xFFFFFFFF
        task_known = task_known and (assigned_pair if has_task else empty_pair)
        order_known = bool(valid & ORDER_KNOWN)
        units.append(Unit(idx, addr, _rawcode(tid), owner, x, y, hp, hp_max,
                          order if order_known else None, hlo, hhi, order_known,
                          task_known, has_task if task_known else None,
                          task_lo if task_known else None, task_hi if task_known else None))
    known = bool(flags & RESOURCES_KNOWN)
    return Snapshot(age_ms, me, bool(in_game), pubs,
                    gold if known else None, lumber if known else None,
                    food_u if known else None, food_c if known else None, units,
                    epoch, bool(flags & ROSTER_COMPLETE), known)


class State:
    """映射一次，之后每次 read() 只是内存拷贝。"""

    def __init__(self, pid: int | None = None):
        self.pid = pid or self._find_war3()
        self._view = None
        self._handle = None
        self._buf = None
        self._last_pubs = -1          # 上一次看到的发布计数
        self._last_pubs_at = 0.0      # 它上一次变化时的本地时刻（单调钟）
        self._fresh_seen = False     # Attach must witness an actual publication.
        self._last_epoch = -1
        self._open()

    @staticmethod
    def _find_war3() -> int:
        # ★ 多开：先认 WAR3_PID 环境变量，再退回"挑第一个"。
        #
        # 09-16 打通了多开（farm.ps1 拉 6 个实例，各跑 3.09 倍、互不拖累），
        # 而"挑第一个 War3"在多开下必然认错实例 —— 而且错得很安静：
        # 模块会连上别人那一局，命令照发、快照照读，只是全打在错误的对局上。
        #
        # 启动器 war3_launch.exe 做完开局会打印 WAR3_PID=<pid>，由上层塞进模块的环境。
        # w3claim.py 早就是这么做的（它按 WAR3_PID 给共享内存起名），这里对齐它。
        import os
        import subprocess
        env_pid = os.environ.get("WAR3_PID", "").strip()
        if env_pid.isdigit() and int(env_pid) > 0:
            return int(env_pid)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             # 原名 War3.exe 和多开改名后的 War3-<N>.exe 都认（runtime/farm.py 每实例一个名字）
             "(Get-Process | Where-Object { $_.ProcessName -match '^War3(-\\d+)?$' } "
             "| Select-Object -First 1).Id"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        if not out:
            raise StateUnavailable("War3 没有在跑")
        # 多开时这条退路会认错实例，所以要说出来，别让它静默地连错。
        if len(out.splitlines()) > 1 or os.environ.get("WAR3_FARM"):
            print(f"[w3state] ⚠ 没有 WAR3_PID，回退到第一个 War3 进程 {out}"
                  f"（多开时这会连错实例）", flush=True)
        return int(out.splitlines()[0])

    def _open(self) -> None:
        name = f"Local\\War3UiState_{self.pid}"
        h = _k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not h:
            raise StateUnavailable(f"打不开共享内存 {name}（DLL 是旧版？游戏没起来？）")
        # Read only the fixed header first: a layout-1 mapping is smaller and
        # must never be interpreted using layout-2's larger unit stride.
        header = _k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, _HDR_SIZE)
        if not header:
            _k32.CloseHandle(h)
            raise StateUnavailable("MapViewOfFile header 失败")
        magic, layout = struct.unpack("<2I", ctypes.string_at(header, 8))
        _k32.UnmapViewOfFile(header)
        if magic != MAGIC or layout != LAYOUT:
            _k32.CloseHandle(h)
            raise StateUnavailable(f"快照布局 {layout} 不支持（需要 {LAYOUT}）；请重启到新 DLL")
        p = _k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, _BLOCK_SIZE)
        if not p:
            _k32.CloseHandle(h)
            raise StateUnavailable("MapViewOfFile 失败")
        self._handle, self._view = h, p
        # ctypes 指针包成 memoryview，之后的解析全是零拷贝切片
        self._buf = (ctypes.c_char * _BLOCK_SIZE).from_address(p)

    def close(self) -> None:
        if self._view:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def _seq(self) -> int:
        return struct.unpack_from("<I", self._buf, 8)[0]

    def read(self, max_age_ms: int = 500, retries: int = 8) -> Snapshot | None:
        """读一份快照。拿不到就返回 None —— 调用方该退回慢路径，而不是用旧数据。

        ⚠ 宁可返回 None 也不要返回过期的快照：拿几秒前的坐标去下命令，
          比多花 46 毫秒糟得多。

        ⚠⚠ "新不新"**不能比绝对时间**。
            这个项目为了做游戏倍速，在游戏进程里**钩了 GetTickCount** ——
            DLL 写进快照的那个时间戳是被倍速缩放过的，和我们这个进程的
            时钟完全不是一回事。实测两边差了 57 秒，而且差值会随倍速漂移。
            （第一版就栽在这里：数据全对，却因为 age 算出个天文数字全被丢掉。）

            改成看 **publishCount 涨没涨**：它每发布一次 +1，含义唯一，
            不依赖任何时钟。连续 max_age_ms 没涨过 = 发布线停了。
        """
        for _ in range(retries):
            s1 = self._seq()
            if s1 & 1:                      # 奇数 = 写者正在写
                continue
            raw = bytes(self._buf)          # 一次拷贝，之后随便解析
            if self._seq() != s1:           # 拷贝期间被写过 -> 撕裂，重来
                continue
            snap = decode_snapshot(raw)
            if snap is None:
                return None
            # 新鲜度看发布计数（说明见上面的注释），不看时间戳。
            now = time.monotonic()
            if self._last_pubs < 0 or snap.epoch != self._last_epoch:
                self._last_pubs = snap.publishes
                self._last_epoch = snap.epoch
                self._last_pubs_at = now
                self._fresh_seen = False
                return None
            if snap.publishes != self._last_pubs:
                self._last_pubs = snap.publishes
                self._last_pubs_at = now
                self._fresh_seen = True
            stale_ms = int((now - self._last_pubs_at) * 1000)
            if stale_ms > max_age_ms:       # 发布线停了 —— 数据不能用
                return None
            if not self._fresh_seen:
                return None
            snap.age_ms = stale_ms
            return snap
        return None


class Session:
    """会自己重连的读取器 —— 游戏重启之后接回去。

    ⚠ 这个类是为了修一个**会静默废掉整个模块**的 bug 而加的。

    现场：mod_combat 接的是 pid=62880，而 mod_scavenge 还连着 pid=46764 ——
    一个二十分钟前就已经不存在的进程。模块进程活着、循环在跑、
    日志没有一行错误，但它**再也不会做任何事**。

    根因：游戏没了之后 `State.read()` 只是返回 None（快照不再更新），
    **不抛异常**。而模块里的重连分支挂在 `except StateUnavailable` 上，
    于是永远走不到，`continue` 一直转。

    这正是这个项目最贵的那类故障：**看起来一切正常，就是什么都没发生。**
    所以判据不能是"有没有抛异常"，要是"**有多久没拿到新数据了**"。

    Session 同时持有 State 和 Cmd —— 重连时两个都要换（Cmd 里存着 pid
    和窗口句柄，只换一个等于没换）。
    """

    def __init__(self, stale_seconds: float = 8.0, player: int | None = None):
        configured = os.environ.get("WAR3_CONTROL_PLAYER", "").strip()
        self.player = player if player is not None else (int(configured) if configured else None)
        if self.player is not None and not 0 <= self.player < 12:
            raise ValueError("控制玩家号必须在 0~11")
        self.stale_seconds = stale_seconds
        self.state: State | None = None
        self.cmd = None
        self._blank_since = 0.0
        self.reconnects = 0

    @property
    def pid(self) -> int:
        return self.state.pid if self.state else 0

    def _connect(self) -> bool:
        from w3cmd import Cmd            # 延迟导入，避免循环依赖
        try:
            self.state = State()
            self.cmd = Cmd(self.state.pid) if self.player is None else Cmd(self.state.pid, player=self.player)
            self._blank_since = 0.0
            return True
        except Exception:                # noqa: BLE001
            self.state = None
            self.cmd = None
            return False

    def read(self) -> Snapshot | None:
        """拿一份快照。拿不到就自己想办法（重连），调用方不用管。"""
        if self.state is None:
            if not self._connect():
                return None
            self.reconnects += 1
            return None
        try:
            snap = self.state.read()
            if snap is not None and self.player is not None:
                snap = self._player_view(snap)
        except Exception:                # noqa: BLE001
            snap = None
        now = time.monotonic()
        if snap is not None:
            self._blank_since = 0.0
            return snap
        # 拿不到数据。**先记时间，超过阈值才断开** ——
        # 单次拿不到是正常的（写者正在写、还没进对局），不该动不动就重连。
        if self._blank_since == 0.0:
            self._blank_since = now
        elif now - self._blank_since > self.stale_seconds:
            try:
                self.state.close()
            except Exception:            # noqa: BLE001
                pass
            self.state = None
            self.cmd = None
            self._blank_since = 0.0
        return None

    def _player_view(self, snap: Snapshot) -> Snapshot | None:
        """旧快照的资源只属于本机；从同一局的 W3P 玩家表取控制方资源，保留全图。"""
        world = self.cmd.snapshot(fresh=True)
        if world is None or world.epoch != snap.epoch or not world.in_game or not world.resources_known:
            return None
        return replace(snap, me=self.player, gold=world.gold, lumber=world.lumber,
                       food_used=world.food_used, food_cap=world.food_cap, resources_known=True)


def _now_ms() -> int:
    """本进程的 GetTickCount。⚠ 只用于诊断 —— 游戏进程里这个函数被钩了
    （用来做倍速），两边的值没有可比性。见 read() 里的说明。"""
    _k32.GetTickCount.restype = wt.DWORD
    return _k32.GetTickCount() & 0xFFFFFFFF


if __name__ == "__main__":
    # 自检：连上、读十次、报延迟和内容
    st = State()
    print(f"War3 pid = {st.pid}")
    ok = 0
    t0 = time.perf_counter()
    for _ in range(10):
        s = st.read()
        if s:
            ok += 1
            last = s
    dt = (time.perf_counter() - t0) / 10 * 1000
    print(f"读 10 次成功 {ok} 次，平均 {dt:.3f} ms/次")
    if ok:
        s = last
        print(f"  快照年龄 {s.age_ms} ms | 发布 {s.publishes} 次 | 对局中={s.in_game}")
        print(f"  我方玩家 {s.me} | 金 {s.gold} 木 {s.lumber} 人口 {s.food_used}/{s.food_cap}")
        print(f"  全场 {len(s.units)} 个单位：我方 {len(s.mine)} / 野怪 {len(s.creeps)} / 英雄 {len(s.heroes)}")
        for u in s.mine[:6]:
            print(f"    {u.type:5s} {u.addr_hex} ({u.x:8.0f},{u.y:8.0f}) {u.hp}/{u.hp_max}")

# -*- coding: utf-8 -*-
"""OpenWar3 快车道客户端：发 W3P 语义命令（协议见 docs/W3P_PROTOCOL_ZH.md §4，War3FastBlock 版本 1）。

    lane = FastLane(pid)
    r = lane.commands([command("point", unit, order, x=x, y=y)])[0]      # 一条语义命令：约 1 帧，回执 Receipt
    rs = lane.commands([command(...), command(...), ...])               # 一批：同一帧里全部执行，每条一张回执

和控制通道（w3chan.Channel，只发运行时动作号）的区别：
  * 控制通道一次一个请求，要抢全机共用的互斥量、等游戏下一次取消息（实测 20~40 ms/次）；
  * 快车道一个客户端对象独占一条车道，**不加锁**；运行时在游戏线程上（每秒数百次）
    把提交的槽成批执行 —— 一批 16 条命令大约一帧落地。
  * 每个槽有截止时间：游戏暂停时车道不会被排空，超时的槽运行时不再执行（faulted=2），
    所以"超时后重发"不会造成迟到的重复命令。

scratch：每个槽 256 字节。前 52 字节是运行时写的回执（+52 / +56 是执行耗时），HUD 文字、镜头状态从 +64 起。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import threading
import time
from dataclasses import dataclass, field

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenEventW.restype = wt.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.ReleaseMutex.argtypes = [wt.HANDLE]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenProcess.restype = wt.HANDLE
_k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_k32.GetTickCount.restype = wt.DWORD

FILE_MAP_ALL = 0x000F001F
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0, WAIT_ABANDONED = 0x0, 0x80

# ── 协议常量（和 ui_control_protocol.h 一一对应；tests/test_w3fast.py 从头文件解析核对）──
MAGIC = 0x4C534146            # 'FASL'
VERSION = 1
LANES = 16
SLOTS = 16
SCRATCH = 256
SLOT_SIZE = 96 + SCRATCH
HDR_SIZE = 64
LANE_SIZE = 32
SLOTS_OFF = HDR_SIZE + LANE_SIZE * LANES
BLOCK_SIZE = SLOTS_OFF + SLOT_SIZE * LANES * SLOTS
FREE, SUBMITTED, DONE = 0, 2, 4

# War3FastBlock 头
H_MAGIC, H_VERSION, H_LANES, H_SLOTS, H_SLOT_SIZE, H_VIEW = 0, 4, 8, 12, 16, 20
H_DRAINS, H_SERVED, H_FAULTS, H_BUDGET_HITS, H_LAST_DRAIN = 24, 28, 32, 36, 40
H_MAX_EXEC_US, H_LAST_DRAIN_US = 44, 48       # 09-23 夜：单条语义命令最慢一次 / 最近一次排空占游戏线程多久
# War3FastLane（相对车道起点）
L_OWNER, L_SUBMIT, L_DRAIN, L_SERVED, L_ROLE, L_PLAYER, L_REFUSED = 0, 4, 8, 12, 16, 20, 24
# P5 车道角色（ui_control_protocol.h kLaneRole*）
ROLE_DEV, ROLE_PLAYER, ROLE_OBSERVER = 0, 1, 2
# War3FastSlot（相对槽起点）。target = 操作码；conv / mask 语义命令用不上，写 0
S_STATE, S_TAG, S_TARGET, S_CONV, S_ARGC, S_FLAGS, S_MASK = 0, 4, 8, 12, 16, 20, 24
S_RSV0, S_RSV1 = 28, 32                      # 保留，写 0
S_RET, S_FAULTED, S_ARGS, S_DEADLINE, S_SCRATCH = 36, 40, 44, 92, 96


class FastLaneUnavailable(RuntimeError):
    pass


@dataclass
class Call:
    """一条语义命令（用 command() 构造）。"""
    op: int                            # 操作码（OP 表）
    conv: int = 0                      # 槽里的 convention 字：语义命令恒为 0
    args: list = field(default_factory=list)
    scratch: bytes = b""               # 执行前写进本槽 scratch（回执、HUD 文字都在这里）


@dataclass
class Result:
    ret: int
    faulted: int                       # 0 正常 / 1 异常被接住 / 2 过期没执行 / 3 车道角色不允许
    data: bytes                        # 本槽 scratch（回执 + 运行时写回的数据）

    @property
    def ok(self) -> bool:
        return self.faulted == 0


def _pid_alive(pid: int) -> bool:
    h = _k32.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wt.DWORD()
    ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
    _k32.CloseHandle(h)
    return bool(ok) and code.value == 259          # STILL_ACTIVE


_HELD: dict[int, set[int]] = {}        # 游戏 pid -> 本进程里活着的 FastLane 占着的车道号
_HELD_LOCK = threading.Lock()
_SHARED: dict[object, "FastLane"] = {}
_SHARED_LOCK = threading.Lock()


def shared_lane(pid: int, player: int | None = None) -> "FastLane":
    """按游戏和控制身份共享车道；玩家车道绝不改写同进程里的 dev 车道。"""
    if player is not None and not 0 <= player < 12:
        raise ValueError("控制玩家号必须在 0~11")
    key = pid if player is None else (pid, ROLE_PLAYER, player)
    with _SHARED_LOCK:
        lane = _SHARED.get(key)
        if lane is None or not getattr(lane, "_p", None):
            lane = FastLane(pid) if player is None else FastLane(pid, role=ROLE_PLAYER, player=player)
            _SHARED[key] = lane
        return lane


class FastLane:
    """独占一条车道。一个进程开一个就够（线程之间共享时本类自带锁）；要不同角色就各开一个（各占一条）。

    role：ROLE_DEV（全部语义命令 + JASS 通道）/ ROLE_PLAYER（只能指挥 player 号玩家的单位）/
    ROLE_OBSERVER（只能查询、镜头、界面）。⚠ 本机模式下角色是自己声明的（约定，不是安全边界）；对战平台由裁判建车道。"""

    def __init__(self, pid: int, role: int = ROLE_DEV, player: int = 0):
        import threading
        self._lock = threading.RLock()
        self.pid = pid
        self.role, self.player = role, player
        self._h = _k32.OpenFileMappingW(FILE_MAP_ALL, False, f"Local\\War3Fast_{pid}")
        if not self._h:
            raise FastLaneUnavailable("快车道的共享内存打不开（DLL 太旧，或还没装好）")
        p = _k32.MapViewOfFile(self._h, FILE_MAP_ALL, 0, 0, BLOCK_SIZE)
        if not p:
            raise FastLaneUnavailable("MapViewOfFile 失败")
        self._p = p
        self._buf = (ctypes.c_char * BLOCK_SIZE).from_address(p)
        if self._u32(H_MAGIC) != MAGIC or self._u32(H_VERSION) != VERSION:
            raise FastLaneUnavailable(f"快车道协议不对：magic={self._u32(H_MAGIC):08X} version={self._u32(H_VERSION)}")
        if self._u32(H_LANES) != LANES or self._u32(H_SLOTS) != SLOTS or self._u32(H_SLOT_SIZE) != SLOT_SIZE:
            raise FastLaneUnavailable("快车道布局和客户端对不上（DLL 和 SDK 版本不一致）")
        self.lane = self._claim()
        self._ev = _k32.OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, False, f"Local\\War3FastDone_{pid}_{self.lane}")
        self._tag = 0
        self._stale: set[int] = set()     # 超时后还没被 DLL 标成 Done 的槽：等它变 Done 才能复用

    # ── 共享内存读写 ───────────────────────────────────────────
    def _u32(self, off: int) -> int:
        return struct.unpack_from("<I", self._buf, off)[0]

    def _put(self, off: int, v: int) -> None:
        struct.pack_into("<I", self._buf, off, v & 0xFFFFFFFF)

    def _lane_off(self, lane: int | None = None) -> int:
        return HDR_SIZE + LANE_SIZE * (self.lane if lane is None else lane)

    def _slot_off(self, s: int) -> int:
        return SLOTS_OFF + SLOT_SIZE * (self.lane * SLOTS + s)

    # ── 车道认领 ──────────────────────────────────────────────
    def _claim(self) -> int:
        mx = _k32.CreateMutexW(None, False, f"Local\\War3FastMutex_{self.pid}")
        if not mx:
            raise FastLaneUnavailable("快车道互斥量取不到")
        r = _k32.WaitForSingleObject(mx, 2000)
        if r not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            _k32.CloseHandle(mx)
            raise FastLaneUnavailable("认领车道超时")
        try:
            me = os.getpid()
            pick = None
            # 每个 FastLane 对象独占一条车道。⚠ 09-23 实测：原来"主人 = 本进程就复用"，同一进程再开一条
            # observer/player 车道会改写第一条的角色（dev 车道变成 player=1，指挥别人的单位被接下），
            # 关掉时还把第一条的主人清零。本进程里已经没有活对象占着的车道（上一个对象没 close 就没了）照样可以收回。
            with _HELD_LOCK:
                held = _HELD.get(self.pid, set())
                for lane in range(LANES):
                    owner = self._u32(self._lane_off(lane) + L_OWNER)
                    if owner == me and lane not in held or owner == 0 or (owner != me and not _pid_alive(owner)):
                        pick = lane
                        break
                if pick is None:
                    raise FastLaneUnavailable(f"{LANES} 条车道都被占了")
                _HELD.setdefault(self.pid, set()).add(pick)
            base = HDR_SIZE + LANE_SIZE * pick
            # 接手别人留下的车道：先把残留的槽清掉（旧主人已经死了，没人会来取结果）
            for s in range(SLOTS):
                off = SLOTS_OFF + SLOT_SIZE * (pick * SLOTS + s)
                if self._u32(off + S_STATE) == DONE:
                    self._put(off + S_STATE, FREE)
            self._put(base + L_ROLE, self.role)
            self._put(base + L_PLAYER, self.player)
            self._put(base + L_OWNER, me)
            return pick
        finally:
            _k32.ReleaseMutex(mx)
            _k32.CloseHandle(mx)

    def close(self) -> None:
        if getattr(self, "_p", None):
            if self._u32(self._lane_off() + L_OWNER) == os.getpid():
                self._put(self._lane_off() + L_OWNER, 0)
            with _HELD_LOCK:
                _HELD.get(self.pid, set()).discard(self.lane)
            if self._ev:
                _k32.CloseHandle(self._ev)
            _k32.UnmapViewOfFile(self._p)
            _k32.CloseHandle(self._h)
            self._p = None

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    # ── 诊断 ──────────────────────────────────────────────────
    def stats(self) -> dict:
        return {"lane": self.lane, "role": self.role, "drains": self._u32(H_DRAINS), "served": self._u32(H_SERVED),
                "faults": self._u32(H_FAULTS), "budgetHits": self._u32(H_BUDGET_HITS),
                "maxExecUs": self._u32(H_MAX_EXEC_US), "lastDrainUs": self._u32(H_LAST_DRAIN_US),
                "laneServed": self._u32(self._lane_off() + L_SERVED),
                "refused": self._u32(self._lane_off() + L_REFUSED)}

    def set_player(self, player: int) -> None:
        """player 角色绑定的玩家号（开局后才知道本机是几号时用）。"""
        self.player = player
        self._put(self._lane_off() + L_PLAYER, player)

    # ── 提交 ──────────────────────────────────────────────────
    def _free_slots(self) -> list[int]:
        out = []
        for s in range(SLOTS):
            st = self._u32(self._slot_off(s) + S_STATE)
            if s in self._stale:
                if st == DONE:                     # DLL 终于处理了（执行或判过期），收回
                    self._put(self._slot_off(s) + S_STATE, FREE)
                    self._stale.discard(s)
                    out.append(s)
                continue
            if st == FREE:
                out.append(s)
            elif st == DONE:                       # 上次没取的结果：丢掉
                self._put(self._slot_off(s) + S_STATE, FREE)
                out.append(s)
        return out

    def run(self, calls: list[Call], timeout: float = 0.25) -> list[Result | None]:
        """提交一批（<= 16 条），等它们全部完成。超时没完成的那条返回 None（DLL 保证过期后不再执行它）。"""
        if not calls:
            return []
        with self._lock:
            return self._run(calls, timeout)

    def _run(self, calls: list[Call], timeout: float) -> list[Result | None]:
        if len(calls) > SLOTS:
            out: list[Result | None] = []
            for i in range(0, len(calls), SLOTS):
                out += self.run(calls[i:i + SLOTS], timeout)
            return out
        free = self._free_slots()
        if len(free) < len(calls):
            raise FastLaneUnavailable(f"车道里空槽不够（{len(free)} < {len(calls)}，多半是游戏暂停着）")
        timeout_ms = max(20, int(timeout * 1000))
        deadline = (_k32.GetTickCount() + timeout_ms) & 0xFFFFFFFF
        used = []
        for c, s in zip(calls, free):
            off = self._slot_off(s)
            if len(c.args) > 12:
                raise ValueError("最多 12 个参数")
            if len(c.scratch) > SCRATCH:
                raise ValueError(f"scratch 最多 {SCRATCH} 字节")
            self._tag = (self._tag + 1) & 0xFFFFFFFF
            self._put(off + S_TAG, self._tag)
            self._put(off + S_TARGET, c.op)
            self._put(off + S_CONV, c.conv if isinstance(c.conv, int) else 0)
            self._put(off + S_ARGC, len(c.args))
            self._put(off + S_FLAGS, SEMANTIC)
            self._put(off + S_MASK, 0)
            self._put(off + S_RSV0, 0)
            self._put(off + S_RSV1, 0)
            self._put(off + S_RET, 0)
            self._put(off + S_FAULTED, 0)
            args = list(c.args) + [0] * (12 - len(c.args))
            struct.pack_into("<12I", self._buf, off + S_ARGS, *[a & 0xFFFFFFFF for a in args])
            self._put(off + S_DEADLINE, deadline or 1)
            if c.scratch:
                ctypes.memmove(self._p + off + S_SCRATCH, c.scratch, len(c.scratch))
            used.append(s)
        # 槽体全部写完再改状态，状态全部改完再推进 submitSeq（x86 的写顺序保证 DLL 看到的是写好的槽）
        for s in used:
            self._put(self._slot_off(s) + S_STATE, SUBMITTED)
        lane = self._lane_off()
        self._put(lane + L_SUBMIT, self._u32(lane + L_SUBMIT) + 1)

        # 等：运行时每排空一次车道就 SetEvent；再多等 50 ms 越过截止时间，之后没完成的一定不会再执行
        end = time.perf_counter() + timeout + 0.05
        pending = set(used)
        while pending:
            pending = {s for s in pending if self._u32(self._slot_off(s) + S_STATE) != DONE}
            if not pending:
                break
            left = end - time.perf_counter()
            if left <= 0:
                break
            _k32.WaitForSingleObject(self._ev, max(1, min(int(left * 1000), 20)))

        out = []
        for c, s in zip(calls, used):
            off = self._slot_off(s)
            if s in pending:
                self._stale.add(s)
                out.append(None)
                continue
            n = len(c.scratch)
            r = Result(self._u32(off + S_RET), self._u32(off + S_FAULTED),
                       bytes(self._buf[off + S_SCRATCH:off + S_SCRATCH + n]) if n else b"")
            self._put(off + S_STATE, FREE)
            out.append(r if r.faulted != 2 else None)
        return out

    def commands(self, calls: list[Call], timeout: float = 0.25) -> list:
        """提交一批语义命令（command() 造的），返回每条的 Receipt；超时的那条是 Receipt(status=TIMEOUT)。"""
        out = []
        for c, r in zip(calls, self.run(calls, timeout)):
            out.append(Receipt.timeout(c.op) if r is None else Receipt.parse(c.op, r))
        return out


# ── W3P 语义命令（协议见 ui_control_protocol.h 的 War3CmdOp / War3CmdStatus / War3CmdReceipt）──
SEMANTIC = 0x10
OP = {"point": 1, "target": 2, "immediate": 3, "build": 4, "learn": 5, "use_item": 6, "revive": 7,
      "rally": 8, "buy": 9, "item_drop": 10, "q_tech": 20, "q_feasible": 21, "q_visible": 22, "q_mine_gold": 23,
      "q_captain": 24, "q_dead_heroes": 25, "pause": 30,
      "cam_state": 40, "cam_field": 41, "cam_look": 42, "cam_follow": 43, "cam_reset": 44, "cam_rotate": 45,
      "cam_bounds": 46, "cam_smoothing": 47, "cam_show_ui": 48, "cam_clean_view": 49, "cam_fog": 50,
      "hud_button": 60, "hud_quest": 61, "hud_quest_refresh": 62, "hud_user_dialog": 63,
      "jass": 70, "jass_handle_of": 71, "jass_unit_of": 72, "canvas_enable": 73, "input_enable": 74}
STATUS = {0: "accepted", 1: "rejected", 2: "bad_unit", 3: "not_owner", 4: "fault", 5: "bad_args",
          6: "unsupported", 7: "bad_target", 8: "forbidden（车道角色不允许）",
          97: "cancelled（批量块里抛了异常，整批没发出去）",
          98: "held（单位被更高优先级的层握着，没发出去）", 99: "timeout"}
TEXT_OFF = 64          # 带文字的命令（HUD）：文字从 scratch+64 起，最多 190 字节（前 52 字节是回执）
TEXT_MAX = SCRATCH - TEXT_OFF - 2
CAMSTATE_OFF = 64
# 引擎可行性检查的原因码（实测）
VERDICT = {0: "可以", 220: "可以", 3: "人口不够", 8: "金不够", 9: "木不够", 32: "训练队列满（7 格）",
           183: "缺前置科技/建筑", 185: "建筑正忙（祭坛在复活英雄 / 大厅队列没空时不能升级）", 221: "没有这一项 / 建造中 / 升级中（商店：这家不卖）",
           89: "商店还没到货（开局按 ItemData stockStart 才上架 / 卖完等 stockRegen）",
           1001: "目标看不见（在迷雾/黑区里）"}
RECEIPT_SIZE = 52


def _fbits(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


def _ref(u) -> tuple[int, int, int]:
    """单位/目标 -> (地址, 句柄 lo, 句柄 hi)。有句柄就带上（运行时会核对，地址被复用时拒绝）。"""
    if u is None:
        return 0, 0, 0
    if isinstance(u, int):
        return u, 0, 0
    return int(u.addr), int(getattr(u, "handle_lo", 0) or 0), int(getattr(u, "handle_hi", 0) or 0)


def command(op: str, unit=None, code: int = 0, target=None, x: float | None = None, y: float | None = None,
            extra: int = 0, mode: int = 0, text: str | None = None, fargs: dict | None = None) -> Call:
    """造一条语义命令。unit / target 可以是带 addr(+handle_lo/hi) 的对象，也可以是裸地址。
    text：HUD 类命令的文字（UTF-8，超长截断）；fargs：{下标: 值} 直接写 args（镜头类命令用 4..6 放额外的 float）。"""
    args = [0] * 11
    args[0:3] = _ref(unit)
    args[3] = code & 0xFFFFFFFF
    args[4:7] = _ref(target)
    args[7] = _fbits(x) if x is not None else 0
    args[8] = _fbits(y) if y is not None else 0
    args[9] = extra & 0xFFFFFFFF
    args[10] = mode
    for i, v in (fargs or {}).items():
        args[i] = _fbits(v) if isinstance(v, float) else int(v) & 0xFFFFFFFF
    scratch = bytes(RECEIPT_SIZE + 8)             # +52 运行时写回的执行耗时、+56 其中引擎下令函数的耗时（微秒）
    if text is not None:
        b = text[:TEXT_MAX] if isinstance(text, bytes) else clip_utf8_bytes(text, TEXT_MAX)
        scratch = bytes(TEXT_OFF) + b + b"\0"
    elif op == "cam_state":
        scratch = bytes(CAMSTATE_OFF + CAMSTATE_SIZE)
    return Call(OP[op], 0, args, scratch=scratch)


CAM_FIELDS = ("distance", "farz", "aoa", "fov", "roll", "rotation", "zoffset")
_CAMSTATE = struct.Struct("<3I 3f 3f 7f 4f 2f 2I 4f")
CAMSTATE_SIZE = _CAMSTATE.size        # 112


def parse_cam_state(data: bytes) -> dict | None:
    """kCamState 回执 -> dict（角度是弧度）。不在对局里 / 取不到相机返回 None。"""
    d = (data or b"")[CAMSTATE_OFF:CAMSTATE_OFF + CAMSTATE_SIZE]
    if len(d) < CAMSTATE_SIZE:
        return None
    v = _CAMSTATE.unpack(d)
    if not v[0]:
        return None
    return {"locked": bool(v[1]), "ui_epoch": v[2], "target": v[3:6], "eye": v[6:9],
            "fields": dict(zip(CAM_FIELDS, v[9:16])), "bounds": v[16:20], "live": v[20:22],
            "fog": {"color": v[22], "style": v[23], "density": v[24], "start": v[25], "end": v[26], "k": v[27]}}


def clip_utf8_bytes(text: str, limit: int) -> bytes:
    """UTF-8 截到 limit 字节以内，不切断字符。"""
    b = text.encode("utf-8")
    if len(b) <= limit:
        return b
    return b[:limit].decode("utf-8", errors="ignore").encode("utf-8")


def text_chunks(text: str, limit: int = TEXT_MAX) -> list[bytes]:
    """长文字按 UTF-8 字符边界切成每段 <= limit 字节（HUD 任务描述分段传）。"""
    out, cur = [], b""
    for ch in text:
        e = ch.encode("utf-8")
        if len(cur) + len(e) > limit:
            out.append(cur)
            cur = b""
        cur += e
    if cur or not out:
        out.append(cur)
    return out


@dataclass
class Receipt:
    op: int
    status: int
    engine_return: int
    verdict: int | None
    order_before: int
    order_after: int
    values: tuple
    note: str = ""                     # 上层补的解释（比如复活被拒时按人口/资源判的原因）
    data: bytes = field(default=b"", repr=False)   # 整块便签（cam_state 的结构在 +64）

    @classmethod
    def parse(cls, op: int, r: Result) -> "Receipt":
        d = (r.data or b"") + bytes(RECEIPT_SIZE)
        st, ret, verdict, ob, oa = struct.unpack_from("<5I", d, 0)
        return cls(op, st if r.faulted in (0, 1) else (8 if r.faulted == 3 else 4), ret, None if verdict == 0xFFFFFFFF else verdict, ob, oa,
                   struct.unpack_from("<8I", d, 20), data=r.data or b"")

    @classmethod
    def timeout(cls, op: int) -> "Receipt":
        return cls(op, 99, 0, None, 0, 0, (0,) * 8)

    @property
    def ok(self) -> bool:
        return self.status == 0

    @property
    def exec_us(self) -> int | None:
        """这条命令在游戏线程上执行了多少微秒（运行时用真实 QPC 量，09-23 夜起）；老运行时 / 没执行返回 None。
        ⚠ 实测移动类命令一条就要几毫秒（引擎当场算路径）—— 一拍给 50 个单位各下一条移动令会占掉游戏线程几十毫秒。"""
        if len(self.data) < RECEIPT_SIZE + 4:
            return None
        return struct.unpack_from("<I", self.data, RECEIPT_SIZE)[0]

    @property
    def engine_us(self) -> int | None:
        """exec_us 里引擎下令函数本身占了多少微秒（只有 point / target / immediate 量；其它命令是 0）。"""
        if len(self.data) < RECEIPT_SIZE + 8:
            return None
        return struct.unpack_from("<I", self.data, RECEIPT_SIZE + 4)[0]

    def __bool__(self) -> bool:            # 老代码 `if g.move(...)` 照常能用
        return self.ok

    @property
    def reason(self) -> str:
        s = STATUS.get(self.status, str(self.status))
        if self.verdict is not None and self.status != 0:
            s += f"（{VERDICT.get(self.verdict, f'原因码 {self.verdict}')}）"
        if self.note:
            s += f"：{self.note}"
        return s

    def value_float(self, i: int) -> float:
        return struct.unpack("<f", struct.pack("<I", self.values[i]))[0]

    def __repr__(self) -> str:
        return f"Receipt({self.reason}, ret={self.engine_return}, order {self.order_before:#x}->{self.order_after:#x})"

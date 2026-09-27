# -*- coding: utf-8 -*-
"""画板的共享内存客户端（09-25）：往游戏画面上画东西。协议见 native/src/ui_control_protocol.h 的 War3CanvasBlock。

运行时建块（语义命令 canvas_enable 时），客户端写一整张显示列表（seqlock：seq 变奇数 -> 写 -> 变偶数），
运行时每帧读一次、画出来。纯本机绘制，不调游戏函数、不改游戏状态 —— 多人局也安全。
一般不直接用这一层，用 openwar3.canvas.Canvas（g.canvas）。

★ 多个客户端同时画（09-26）：显示列表只有一张，以前谁写谁整张覆盖 —— 玩法模组画的按钮，远见 / MCP / 网关一画就没了
（实测：进程 B 画完，进程 A 的按钮和热键全不见）。现在每条带主人（items[].reserved[1] = 进程号、reserved[2] = 进程内序号），
写的时候持命名互斥量 Local\\War3CanvasMutex_<pid> 读-改-写：只换掉自己的条目，别人的原样留着，主人进程已经退出的顺手清掉。
条目 id 从头部 reserved[5] 的计数器统一分配（以前每个进程都从 1 编，两个进程的「1 号按钮」被点时两边都以为是自己的）。
别的语言直接写这块内存时照这个规矩来；不持锁、reserved[1] 写 0 的条目会在别人下一次写时被当成没主人的清掉。
"""
from __future__ import annotations

import ctypes
import itertools
import os
import struct
from ctypes import wintypes as wt

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.ReleaseMutex.argtypes = [wt.HANDLE]
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenProcess.restype = wt.HANDLE
_k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
FILE_MAP_ALL = 0x000F001F
WAIT_OBJECT_0, WAIT_ABANDONED = 0, 0x80
PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
ERROR_ACCESS_DENIED = 5


def pid_alive(pid: int) -> bool:
    """进程还在不在（打不开但是拒绝访问 = 在，别的进程权限高）。"""
    if pid <= 0:
        return False
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wt.DWORD()
        return bool(_k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)


class NamedLock:
    """跨进程的命名互斥量（with 用）。持有者死在半路（WAIT_ABANDONED）照样算拿到。"""

    def __init__(self, name: str, timeout_ms: int = 2000):
        self.name, self.timeout_ms = name, timeout_ms
        self._h = None

    def __enter__(self):
        self._h = _k32.CreateMutexW(None, False, self.name)
        if not self._h:
            raise OSError(f"命名互斥量 {self.name} 建不了")
        r = _k32.WaitForSingleObject(self._h, self.timeout_ms)
        if r not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            _k32.CloseHandle(self._h)
            self._h = None
            raise TimeoutError(f"等 {self.name} 超时（别的进程拿着没放）")
        return self

    def __exit__(self, *exc):
        if self._h:
            _k32.ReleaseMutex(self._h)
            _k32.CloseHandle(self._h)
            self._h = None
        return False


_serial = itertools.count(1)       # 同一个进程里的第几个客户端（网关每个会话一个 Game，同一进程里好几个画板客户端）

MAGIC = 0x564E4143          # 'CANV'
LAYOUT = 1
ITEMS = 256
POOL = 64 * 1024
HDR = 64
ITEM = struct.Struct("<7I 3f 2f 4f 3I 2I 2I 2I 3I")
assert ITEM.size == 112, ITEM.size
BLOCK = HDR + ITEMS * ITEM.size + POOL
# 头：magic layout seq count enabled drawnFrames itemsDrawn faults lastUs torn reserved[6]
O_SEQ, O_COUNT, O_ENABLED, O_DRAWN, O_ITEMS_DRAWN, O_FAULTS, O_LAST_US, O_TORN = 8, 12, 16, 20, 24, 28, 32, 36
O_NEXT_ID = 60                 # reserved[5]：条目 id 计数器（客户端之间共用，持锁分配；运行时不碰）
FIRST_ID = 0x10000             # 从这里编起：和老客户端（每个进程从 1 编）的 id 错开
# 条目字段下标（ITEM.unpack 的元组）
F_TEXT_OFF, F_TEXT_LEN, F_PTS_OFF, F_PTS_N, F_OWNER_PID, F_OWNER_SERIAL = 19, 20, 21, 22, 26, 27

KIND = {"text": 1, "bar": 2, "image": 3, "circle": 4, "path": 5}
ANCHOR = {"screen": 0, "frac": 1, "world": 2, "unit": 3}
FLAG = {"shadow": 0x1, "bold": 0x2, "center": 0x4, "arrow": 0x8, "hidden": 0x10, "over_ui": 0x20,
        "interactive": 0x40}   # 09-25：可点 / 可悬停（要先 input_enable）；悬停高亮色放 hover（reserved[0]）


def argb(c, default: int = 0) -> int:
    """颜色 -> ARGB 整数。可以是 (r, g, b) / (r, g, b, a) / "#RRGGBB" / "#RRGGBBAA" / 整数 ARGB / None（= default）。"""
    if c is None:
        return default
    if isinstance(c, int):
        return c & 0xFFFFFFFF
    if isinstance(c, str):
        s = c.lstrip("#")
        if len(s) not in (6, 8):
            raise ValueError(f"颜色写法不对：{c!r}（要 #RRGGBB 或 #RRGGBBAA）")
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        a = int(s[6:8], 16) if len(s) == 8 else 255
        return (a << 24) | (r << 16) | (g << 8) | b
    r, g, b, *a = c
    return ((a[0] if a else 255) << 24) | (int(r) << 16) | (int(g) << 8) | int(b)


class CanvasUnavailable(RuntimeError):
    pass


def pack(items: list[dict], owner: tuple[int, int] = (0, 0)) -> tuple[bytes, bytes, int]:
    """显示列表 -> (items 区字节, pool 字节, 条数)。items 里每条是 dict（字段名同 C 结构，缺的给 0）。"""
    return pack_all(items, owner)[:3]


def pack_all(items: list[dict], owner: tuple[int, int] = (0, 0), keep: list | None = None) -> tuple[bytes, bytes, int, int]:
    """同 pack，外加别的客户端留下的条目 keep = [(字段元组, 文字字节, 点字节)]：先放它们（偏移按新 pool 重排），
    自己的跟在后面，总数超过 256 的截掉。owner = (进程号, 进程内序号) 写进自己每条的 reserved[1..2]。
    返回 (items 区字节, pool 字节, 总条数, 其中别人的条数)。"""
    pool = bytearray()
    out = bytearray()
    n = 0
    for fields, text, pts in keep or []:
        if n >= ITEMS or len(pool) + len(text) + len(pts) > POOL:
            break
        f = list(fields)
        f[F_TEXT_OFF] = len(pool)
        pool += text
        f[F_PTS_OFF] = len(pool)
        pool += pts
        out += ITEM.pack(*f)
        n += 1
    kept = n
    for it in items[:ITEMS - n]:
        text = (it.get("text") or "").encode("utf-8")
        t_off = len(pool)
        pool += text
        pts = it.get("points") or []
        p_off = len(pool)
        for x, y in pts:
            pool += struct.pack("<ff", float(x), float(y))
        if len(pool) > POOL:
            raise CanvasUnavailable(f"画板的文字和点一共超过 {POOL} 字节")
        out += ITEM.pack(
            it["id"], KIND[it["kind"]] if isinstance(it["kind"], str) else it["kind"],
            ANCHOR[it.get("anchor", "screen")] if isinstance(it.get("anchor", "screen"), str) else it["anchor"],
            it.get("flags", 0), it.get("unit", 0), it.get("handle_lo", 0), it.get("handle_hi", 0),
            float(it.get("x", 0)), float(it.get("y", 0)), float(it.get("z", 0)),
            float(it.get("w", 0)), float(it.get("h", 0)),
            float(it.get("value", 0)), float(it.get("font_px", 0)), float(it.get("line_w", 0)), float(it.get("radius_px", 0)),
            argb(it.get("color"), 0xFFFFFFFF), argb(it.get("color2"), 0), argb(it.get("color3"), 0),
            t_off, len(text), p_off, len(pts), it.get("version", 0), it.get("layer", 0),
            argb(it.get("hover"), 0), owner[0], owner[1])
        n += 1
    return bytes(out), bytes(pool), n, kept


def others(raw_items: bytes, pool: bytes, count: int, owner: tuple[int, int], alive=pid_alive) -> list:
    """共享列表里别的客户端的条目 [(字段元组, 文字字节, 点字节)]：去掉自己的、没主人的（老客户端 / 别的语言没按规矩写的）、
    主人进程已经退出的。alive(pid) 可以换成假的（测试）。"""
    out = []
    checked: dict = {}
    for i in range(min(count, ITEMS)):
        f = ITEM.unpack_from(raw_items, i * ITEM.size)
        pid, serial = f[F_OWNER_PID], f[F_OWNER_SERIAL]
        if not pid or (pid, serial) == tuple(owner):
            continue
        if pid not in checked:
            checked[pid] = alive(pid)
        if not checked[pid]:
            continue
        text = pool[f[F_TEXT_OFF]:f[F_TEXT_OFF] + f[F_TEXT_LEN]]
        pts = pool[f[F_PTS_OFF]:f[F_PTS_OFF] + 8 * f[F_PTS_N]]
        out.append((f, bytes(text), bytes(pts)))
    return out


class CanvasBlock:
    def __init__(self, pid: int):
        self.pid = pid
        self.owner = (os.getpid(), next(_serial))      # 这个客户端写的条目都带这个主人标记
        self._h = _k32.OpenFileMappingW(FILE_MAP_ALL, False, f"Local\\War3Canvas_{pid}")
        if not self._h:
            raise CanvasUnavailable("画板的共享内存打不开（DLL 太旧，或者还没发 canvas_enable）")
        p = _k32.MapViewOfFile(self._h, FILE_MAP_ALL, 0, 0, BLOCK)
        if not p:
            raise CanvasUnavailable("MapViewOfFile 失败")
        self._p = p
        self._buf = (ctypes.c_char * BLOCK).from_address(p)
        magic, layout = struct.unpack_from("<II", self._buf, 0)
        if magic != MAGIC or layout != LAYOUT:
            raise CanvasUnavailable(f"画板协议对不上：magic={magic:08X} layout={layout}")

    def _u32(self, off: int) -> int:
        return struct.unpack_from("<I", self._buf, off)[0]

    def _put(self, off: int, v: int) -> None:
        struct.pack_into("<I", self._buf, off, v & 0xFFFFFFFF)

    def _lock(self) -> NamedLock:
        return NamedLock(f"Local\\War3CanvasMutex_{self.pid}")

    def alloc_id(self) -> int:
        """新条目的 id（所有客户端共用一个计数器，不会撞）。"""
        with self._lock():
            v = self._u32(O_NEXT_ID)
            if v < FIRST_ID or v >= 0x7FFFFFFF:
                v = FIRST_ID
            self._put(O_NEXT_ID, v + 1)
            return v

    def write(self, items: list[dict]) -> int:
        """把这个客户端自己的显示列表写进去：持锁读-改-写，别的客户端的条目原样留着（seqlock 写）。返回自己写进去几条。"""
        with self._lock():
            count = min(self._u32(O_COUNT), ITEMS)
            keep = others(ctypes.string_at(self._p + HDR, count * ITEM.size),
                          ctypes.string_at(self._p + HDR + ITEMS * ITEM.size, POOL), count, self.owner)
            raw, pool, n, kept = pack_all(items, self.owner, keep)
            seq = self._u32(O_SEQ)
            if seq & 1:
                seq += 1                               # 上一个写的人死在半路：跳过这个奇数
            self._put(O_SEQ, seq + 1)                  # 奇数：正在写
            ctypes.memmove(self._p + HDR, raw, len(raw))
            ctypes.memmove(self._p + HDR + ITEMS * ITEM.size, pool, len(pool))
            self._put(O_COUNT, n)
            self._put(O_SEQ, seq + 2)                  # 偶数：写完
            return n - kept

    def stats(self) -> dict:
        return {"enabled": bool(self._u32(O_ENABLED)), "drawnFrames": self._u32(O_DRAWN),
                "itemsDrawn": self._u32(O_ITEMS_DRAWN), "faults": self._u32(O_FAULTS),
                "lastUs": self._u32(O_LAST_US), "torn": self._u32(O_TORN), "count": self._u32(O_COUNT)}

    def close(self) -> None:
        if getattr(self, "_p", None):
            _k32.UnmapViewOfFile(self._p)
            self._p = None
        if getattr(self, "_h", None):
            _k32.CloseHandle(self._h)
            self._h = None

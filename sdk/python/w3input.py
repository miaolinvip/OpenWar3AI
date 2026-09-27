# -*- coding: utf-8 -*-
"""界面与输入的共享内存客户端（09-25）。协议见 native/src/ui_control_protocol.h 的 War3InputBlock。

运行时（语义命令 input_enable 之后）接管游戏窗口过程：
  · 画板上标了可交互的项被点 -> 事件 ui.click（这一下游戏收不到），鼠标移进移出 -> ui.hover
  · 客户端登记的热键按下 -> 事件 hotkey（可选吞掉）
  · 开了鼠标开关时点在世界上 -> 事件 mouse.world（可选吞掉：「点地面选一个位置」）
并且每帧回写鼠标位置、鼠标指着的地面点、悬停的画板项。纯本机输入，不进指令流 —— 多人局也安全。
一般不直接用这一层，用 openwar3.ui.UI（g.ui）。
"""
from __future__ import annotations

import ctypes
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
FILE_MAP_ALL = 0x000F001F

MAGIC = 0x54504E49            # 'INPT'
LAYOUT = 1
HOTKEYS = 32
HDR = 128
HOTKEY = struct.Struct("<4I")
BLOCK = HDR + HOTKEYS * HOTKEY.size
# 头：magic layout seq enabled | cursorX cursorY worldX worldY worldZ worldValid hoverId frames clicks keys backW backH
#    | mouseFlags hotkeyCount reserved[14]
O_SEQ, O_ENABLED = 8, 12
_STATE = struct.Struct("<2i 3f 7I")          # +16 起，到 +64
O_MOUSE_FLAGS, O_HOTKEY_COUNT = 64, 68

MOD = {"ctrl": 1, "shift": 2, "alt": 4}
SWALLOW, REPEAT = 0x1, 0x2
MOUSE_REPORT_LEFT, MOUSE_REPORT_RIGHT, MOUSE_CAPTURE_LEFT, MOUSE_CAPTURE_RIGHT = 0x1, 0x2, 0x4, 0x8

_NAMED = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
          "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
          "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13, "capslock": 0x14,
          "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
          ",": 0xBC, ".": 0xBE, "/": 0xBF}


def parse_key(spec: str) -> tuple[int, int]:
    """'F5' / 'ctrl+shift+Q' / 'alt+1' / 'numpad3' / 'space' -> (虚拟键码, 修饰键位)。大小写都行。"""
    parts = [p.strip().lower() for p in str(spec).replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(f"热键写法不对：{spec!r}")
    mods = 0
    for p in parts[:-1]:
        if p not in MOD:
            raise ValueError(f"不认识的修饰键 {p!r}（只有 ctrl / shift / alt）")
        mods |= MOD[p]
    k = parts[-1]
    if len(k) >= 2 and k[0] == "f" and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x70 + int(k[1:]) - 1, mods
    if k.startswith("numpad") and k[6:].isdigit():
        return 0x60 + int(k[6:]), mods
    if len(k) == 1 and ("a" <= k <= "z" or "0" <= k <= "9"):
        return ord(k.upper()), mods
    if k in _NAMED:
        return _NAMED[k], mods
    raise ValueError(f"不认识的键 {k!r}（F1..F24 / A..Z / 0..9 / numpad0..9 / space enter esc tab 方向键 ……）")


class InputUnavailable(RuntimeError):
    pass


# ── ★ 多个客户端同时用（09-26）──
# 运行时的热键表和鼠标开关只有一份，以前谁写谁整张覆盖（实测：进程 B 登记 F7，进程 A 的 F6 就没了；A 调 ui.close()
# 还会让运行时整个放开窗口过程，B 的按钮也点不了）。现在各客户端把自己要的登记在一块 SDK 自己建的共享内存
# Local\War3InputClients_<pid> 里（运行时不认识它），持命名互斥量 Local\War3InputMutex_<pid> 改完自己那条，
# 再把所有还活着的客户端合并成一张表写进运行时的输入块：热键按「键码 + 修饰键」去重（吞不吞、自动重复取并集，
# id 用第一个登记的 —— 运行时只报第一条匹配的，所以各客户端按键码 + 修饰键认自己的热键），鼠标开关取并集。
CLIENTS_MAGIC = 0x4C434E49      # 'INCL'
CLIENTS_MAX = 16
CL_HDR = 16                     # magic、version、下一个热键 id、保留
CL_REC = struct.Struct("<4I")   # 进程号、进程内序号、鼠标开关、热键条数
CL_SIZE = CL_REC.size + HOTKEYS * HOTKEY.size
CL_BLOCK = CL_HDR + CLIENTS_MAX * CL_SIZE
FIRST_HOTKEY_ID = 0x10000
_k32.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
_k32.CreateFileMappingW.restype = wt.HANDLE
INVALID_HANDLE_VALUE = wt.HANDLE(-1)
PAGE_READWRITE = 0x04


def merge_clients(records: list[tuple], alive) -> tuple[list[tuple], int]:
    """各客户端的登记 [(pid, serial, mouse_flags, [(id, vk, mods, flags), ...])] -> (合并后的热键表, 合并后的鼠标开关)。
    进程已经退出的（alive(pid) 为假）不算。同一个键码 + 修饰键只留一条：id 用先登记的，flags 取并集。"""
    table: dict = {}
    mouse = 0
    for pid, _serial, mflags, keys in records:
        if not pid or not alive(pid):
            continue
        mouse |= mflags
        for hid, vk, mods, flags in keys:
            k = (vk, mods)
            if k in table:
                table[k] = (table[k][0], vk, mods, table[k][3] | flags)
            else:
                table[k] = (hid, vk, mods, flags)
    return list(table.values())[:HOTKEYS], mouse


class InputBlock:
    def __init__(self, pid: int):
        import w3canvas                                  # 借用它的命名锁、进程存活判断、进程内序号
        self._W = w3canvas
        self.pid = pid
        self.owner = (os.getpid(), next(w3canvas._serial))
        self._mine_mouse = 0
        self._mine_keys: list = []
        self._h = _k32.OpenFileMappingW(FILE_MAP_ALL, False, f"Local\\War3Input_{pid}")
        if not self._h:
            raise InputUnavailable("输入块打不开（DLL 太旧，或者还没发 input_enable）")
        p = _k32.MapViewOfFile(self._h, FILE_MAP_ALL, 0, 0, BLOCK)
        if not p:
            raise InputUnavailable("MapViewOfFile 失败")
        self._p = p
        self._buf = (ctypes.c_char * BLOCK).from_address(p)
        magic, layout = struct.unpack_from("<II", self._buf, 0)
        if magic != MAGIC or layout != LAYOUT:
            raise InputUnavailable(f"输入块协议对不上：magic={magic:08X} layout={layout}")
        # 客户端登记表：谁先来谁建，之后的打开同一块（最后一个客户端关掉时系统回收）
        self._ch = _k32.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, CL_BLOCK,
                                           f"Local\\War3InputClients_{pid}")
        if not self._ch:
            raise InputUnavailable("客户端登记表建不了")
        cp = _k32.MapViewOfFile(self._ch, FILE_MAP_ALL, 0, 0, CL_BLOCK)
        if not cp:
            raise InputUnavailable("客户端登记表 MapViewOfFile 失败")
        self._cp = cp
        self._cbuf = (ctypes.c_char * CL_BLOCK).from_address(cp)
        with self._lock():
            if struct.unpack_from("<I", self._cbuf, 0)[0] != CLIENTS_MAGIC:
                ctypes.memset(cp, 0, CL_BLOCK)
                struct.pack_into("<3I", self._cbuf, 0, CLIENTS_MAGIC, 1, FIRST_HOTKEY_ID)

    def _lock(self):
        return self._W.NamedLock(f"Local\\War3InputMutex_{self.pid}")

    # ── 客户端登记表 ──
    def _records(self) -> list[tuple]:
        out = []
        for i in range(CLIENTS_MAX):
            off = CL_HDR + i * CL_SIZE
            pid, serial, mflags, n = CL_REC.unpack_from(self._cbuf, off)
            keys = [HOTKEY.unpack_from(self._cbuf, off + CL_REC.size + j * HOTKEY.size) for j in range(min(n, HOTKEYS))]
            out.append((pid, serial, mflags, keys))
        return out

    def _publish(self, remove: bool = False) -> int:
        """（持锁）更新自己那条登记、清掉死掉的客户端、把合并结果写进运行时的输入块。返回还登记着的别的客户端数。"""
        recs = self._records()
        slot, free = None, None
        for i, (pid, serial, _m, _k) in enumerate(recs):
            if (pid, serial) == self.owner:
                slot = i
            elif pid and not self._W.pid_alive(pid):
                recs[i] = (0, 0, 0, [])
                ctypes.memset(self._cp + CL_HDR + i * CL_SIZE, 0, CL_SIZE)
            if free is None and not recs[i][0]:
                free = i
        if remove:
            if slot is not None:
                recs[slot] = (0, 0, 0, [])
                ctypes.memset(self._cp + CL_HDR + slot * CL_SIZE, 0, CL_SIZE)
        else:
            i = slot if slot is not None else free
            if i is None:
                raise InputUnavailable(f"同时用输入的客户端超过 {CLIENTS_MAX} 个")
            keys = list(self._mine_keys)[:HOTKEYS]
            recs[i] = (self.owner[0], self.owner[1], self._mine_mouse, keys)
            off = CL_HDR + i * CL_SIZE
            CL_REC.pack_into(self._cbuf, off, self.owner[0], self.owner[1], self._mine_mouse, len(keys))
            for j, k in enumerate(keys):
                HOTKEY.pack_into(self._cbuf, off + CL_REC.size + j * HOTKEY.size, *k)
        table, mouse = merge_clients(recs, lambda pid: True)       # 死掉的上面已经清了

        def fill():
            for j, k in enumerate(table):
                HOTKEY.pack_into(self._buf, HDR + j * HOTKEY.size, *k)
            self._put(O_HOTKEY_COUNT, len(table))
        self._write(fill)
        self._put(O_MOUSE_FLAGS, mouse)
        return sum(1 for pid, serial, _m, _k in recs if pid and (pid, serial) != self.owner)

    def attach(self) -> None:
        """登记成一个输入客户端（没有热键也算：别人 close 时据此判断还有没有人在用输入）。"""
        with self._lock():
            self._publish()

    def detach(self) -> int:
        """撤掉自己的热键和鼠标开关。返回还在用输入的别的客户端数（0 = 可以让运行时放开窗口过程了）。"""
        self._mine_keys, self._mine_mouse = [], 0
        with self._lock():
            return self._publish(remove=True)

    def alloc_hotkey_id(self) -> int:
        """新热键的 id（所有客户端共用一个计数器）。"""
        with self._lock():
            v = struct.unpack_from("<I", self._cbuf, 8)[0]
            if v < FIRST_HOTKEY_ID or v >= 0x7FFFFFFF:
                v = FIRST_HOTKEY_ID
            struct.pack_into("<I", self._cbuf, 8, v + 1)
            return v

    def _u32(self, off: int) -> int:
        return struct.unpack_from("<I", self._buf, off)[0]

    def _put(self, off: int, v: int) -> None:
        struct.pack_into("<I", self._buf, off, v & 0xFFFFFFFF)

    def state(self) -> dict:
        """运行时每帧回写的：enabled、cursor（后备缓冲像素，不在窗口里 = None）、world（鼠标指着的地面点，指着界面 / 天空 = None）、
        hover（悬停的画板项 id，0 = 没有）、frames、clicks（吞掉的点击数）、keys（报过的热键数）、back（后备缓冲尺寸）。"""
        cx, cy, wx, wy, wz, wv, hover, frames, clicks, keys, bw, bh = _STATE.unpack_from(self._buf, 16)
        return {"enabled": bool(self._u32(O_ENABLED)), "cursor": (cx, cy) if cx >= 0 and cy >= 0 else None,
                "world": (wx, wy, wz) if wv else None, "hover": hover, "frames": frames, "clicks": clicks,
                "keys": keys, "back": (bw, bh)}

    def _write(self, fn) -> None:
        seq = self._u32(O_SEQ)
        if seq & 1:
            seq += 1                                   # 上一个写的人死在半路：跳过这个奇数
        self._put(O_SEQ, seq + 1)
        try:
            fn()
        finally:
            self._put(O_SEQ, seq + 2)

    def set_hotkeys(self, keys: list[tuple[int, int, int, int]]) -> None:
        """这个客户端的热键表：[(id, vk, mods, flags)]，最多 32 条。和别的客户端的合并后写进运行时。"""
        self._mine_keys = [tuple(int(v) for v in k) for k in list(keys)[:HOTKEYS]]
        with self._lock():
            self._publish()

    def set_mouse(self, flags: int) -> None:
        """这个客户端的鼠标开关（kMouse*）。运行时收到的是所有客户端的并集。"""
        self._mine_mouse = int(flags)
        with self._lock():
            self._publish()

    def mouse_flags(self) -> int:
        """这个客户端自己要的鼠标开关（不是合并后的）。"""
        return self._mine_mouse

    def close(self) -> None:
        if getattr(self, "_cp", None):
            _k32.UnmapViewOfFile(self._cp)
            self._cp = None
        if getattr(self, "_ch", None):
            _k32.CloseHandle(self._ch)
            self._ch = None
        if getattr(self, "_p", None):
            _k32.UnmapViewOfFile(self._p)
            self._p = None
        if getattr(self, "_h", None):
            _k32.CloseHandle(self._h)
            self._h = None

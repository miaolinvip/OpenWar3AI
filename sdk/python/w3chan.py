"""w3chan.py —— 直连运行时的动作通道（发运行时动作号），不起进程。

    ch = Channel(pid)
    ch.action(22)                                   # 发一个运行时动作（22 = 结束这个游戏进程）
    ch.summary                                      # 上一个动作的回执（一行文字）

下命令、查询、镜头、HUD 都不走这里 —— 走快车道的 W3P 语义命令（w3fast.py）。
这里只剩运行时自己的动作号：结束游戏（22）、左下角消息（45，openwar3.Game.message）、
单位气泡（56，w3speak.py）等。共享内存的布局在 war3_offsets.py。

为什么要有它
------------
原来每发一个动作都要起一个 war3_ui_command.exe，实测 **130 毫秒**，
而其中绝大部分是 CreateProcess 的开销 —— 真正的通道往返只有几毫秒。
所以把那个 exe 做的事在 Python 里重做一遍：
共享内存 + 互斥量 + PostThreadMessage + 等事件，全是 ctypes 能直接做的。

★ 互斥量拿到 WAIT_ABANDONED 要当成成功
    上一个持有者没释放就死了（命令被打断时经常如此）。判成失败会**永久
    毒化通道**：之后每条命令都报 busy，游戏就停在那儿不动 ——
    这个坑 C++ 那边的注释里写着，说曾经看起来像"菜单导航坏了"查了好几个小时。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import war3_offsets as O          # noqa: E402  动作通道的共享内存布局

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)

FILE_MAP_ALL = 0x000F001F
EVENT_ALL = 0x001F0003
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x0
WAIT_ABANDONED = 0x80
WM_NULL = 0x0000

_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenEventW.restype = wt.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD
_k32.ReleaseMutex.argtypes = [wt.HANDLE]
_k32.ResetEvent.argtypes = [wt.HANDLE]
_u32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
_u32.RegisterWindowMessageW.restype = wt.UINT
_u32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
_u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_u32.GetWindowThreadProcessId.restype = wt.DWORD
_u32.IsWindowVisible.argtypes = [wt.HWND]


def find_window(pid: int) -> tuple[int, int]:
    """找这个进程的主窗口，返回 (hwnd, threadId)。"""
    best = [0, 0, 0]      # hwnd, threadId, area
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lp):
        owner = wt.DWORD()
        tid = _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not _u32.IsWindowVisible(hwnd):
            return True
        r = wt.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        if area > best[2]:
            best[0], best[1], best[2] = hwnd, tid, area
        return True

    _u32.EnumWindows(CB(cb), 0)
    return best[0], best[1]


class ChannelUnavailable(RuntimeError):
    pass


class Channel:
    def __init__(self, pid: int):
        self.pid = pid
        self.hwnd, self.tid = find_window(pid)
        if not self.hwnd:
            raise ChannelUnavailable(f"找不到 pid={pid} 的窗口")
        h = _k32.OpenFileMappingW(FILE_MAP_ALL, False, f"Local\\War3UiControl_{pid}")
        if not h:
            raise ChannelUnavailable("控制通道的共享内存打不开（仪表盘没挂上？）")
        p = _k32.MapViewOfFile(h, FILE_MAP_ALL, 0, 0, O.RESULT_SIZE)
        if not p:
            raise ChannelUnavailable("MapViewOfFile 失败")
        self._buf = (ctypes.c_char * O.RESULT_SIZE).from_address(p)
        self._done = _k32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False,
                                     f"Local\\War3UiControlDone_{pid}")
        self._mx = _k32.CreateMutexW(None, False, f"Local\\War3UiControlMutex_{pid}")
        self._msg = _u32.RegisterWindowMessageW("War3RenderHook.Control.v1")
        if not self._done or not self._mx or not self._msg:
            raise ChannelUnavailable("通道的事件/互斥量/消息号取不到")

    # ── 底层 ────────────────────────────────────────────────
    def _u32_at(self, off: int) -> int:
        return struct.unpack_from("<I", self._buf, off)[0]

    def _put_u32(self, off: int, v: int) -> None:
        struct.pack_into("<I", self._buf, off, v & 0xFFFFFFFF)

    def _submit(self, action: int, command: int, timeout: float = 5.0) -> bool:
        """发一个请求并等它完成。**调用方必须已经持有互斥量。**"""
        _k32.ResetEvent(self._done)
        req = (self._u32_at(O.R_REQUEST_ID) + 1) & 0xFFFFFFFF
        self._put_u32(O.R_REQUEST_ID, req)
        if not _u32.PostThreadMessageW(self.tid, self._msg, action, command):
            return False
        _u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)   # 戳一下消息循环
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._u32_at(O.R_COMPLETED_ID) == req:
                return True
            _k32.WaitForSingleObject(self._done, 2)
        return False

    class _Held:
        def __init__(self, mx): self.mx = mx
        def __enter__(self):
            r = _k32.WaitForSingleObject(self.mx, 5000)
            # ⚠ WAIT_ABANDONED 必须当成拿到了：上一个持有者没释放就死了，
            #   判成失败会永久毒化通道（说明见文件头）。
            if r not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                raise ChannelUnavailable("通道忙")
            return self
        def __exit__(self, *a):
            _k32.ReleaseMutex(self.mx)

    def _hold(self):
        return Channel._Held(self._mx)

    # ── 对外 ────────────────────────────────────────────────
    def action(self, action: int, command: int = 0, timeout: float = 5.0) -> bool:
        with self._hold():
            return self._submit(action, command, timeout)

    @property
    def summary(self) -> str:
        raw = bytes(self._buf[O.R_SUMMARY:O.R_SUMMARY + 160])
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace")

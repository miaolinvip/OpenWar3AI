"""玩家模式的前台状态；原生全屏由游戏自身管理。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetForegroundWindow.restype = W.HWND
_u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
_u32.IsIconic.argtypes = [W.HWND]


def game_active(pid: int) -> bool:
    """本局窗口在前台且未最小化；不需要打开游戏进程句柄。"""
    hwnd = _u32.GetForegroundWindow()
    owner = W.DWORD()
    if not hwnd or _u32.IsIconic(hwnd):
        return False
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value == pid

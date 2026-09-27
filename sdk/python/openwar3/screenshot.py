# -*- coding: utf-8 -*-
"""给游戏窗口截图（09-25，给大模型「看」画面用）：PrintWindow 让窗口把自己画进内存，前面挡着别的窗口也不影响、不抢焦点。

    img = screenshot.grab(pid)            # PIL.Image（客户区），截不到返回 None
    png = screenshot.png_bytes(pid, scale=0.5)

四个坑（都是静默出错）：取尺寸前先声明 DPI 感知；PrintWindow 画的是整个窗口，客户区要自己裁；
某些后台缓冲会给一张纯色图（按方差判，不按亮度 —— 夜里的画面本来就暗）；War3 改了自己的 DACL，按进程找窗口要走快照，
这里直接按 pid 枚举窗口（GetWindowThreadProcessId 不需要进程句柄）。需要 Pillow。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x2
_dpi_done = False


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def _dpi_aware() -> None:
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            u32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def game_window(pid: int) -> int:
    """这个进程最大的可见顶层窗口（War3 只有一个）。"""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            r = _RECT()
            u32.GetClientRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True
    u32.EnumWindows(each, 0)
    return max(found)[1] if found else 0


def grab(pid: int, scale: float = 1.0):
    _dpi_aware()
    from PIL import Image
    hwnd = game_window(pid)
    if not hwnd:
        return None
    wr = _RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(wr))
    ww, wh = wr.right - wr.left, wr.bottom - wr.top
    if ww <= 0 or wh <= 0:
        return None
    hdc = u32.GetDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, ww, wh)
    old = g32.SelectObject(mem, bmp)
    img = None
    try:
        if u32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            bi = _BIH()
            bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(_BIH), ww, -wh, 1, 32
            buf = ctypes.create_string_buffer(ww * wh * 4)
            if g32.GetDIBits(mem, bmp, 0, wh, buf, ctypes.byref(bi), 0):
                img = Image.frombuffer("RGB", (ww, wh), buf, "raw", "BGRX", 0, 1)
    finally:
        g32.SelectObject(mem, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(hwnd, hdc)
    if img is None:
        return None
    cr = _RECT()
    u32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = _POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    ox, oy = pt.x - wr.left, pt.y - wr.top
    img = img.crop((ox, oy, ox + cr.right - cr.left, oy + cr.bottom - cr.top))
    sm = img.convert("L").resize((48, 27))
    px = sm.tobytes()
    mean = sum(px) / len(px)
    if sum((p - mean) ** 2 for p in px) / len(px) < 4.0 and mean < 40:
        return None                                   # 纯色 = 没截到（窗口最小化 / 后台缓冲不给）
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return img


def png_bytes(pid: int, scale: float = 0.5) -> bytes | None:
    img = grab(pid, scale)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

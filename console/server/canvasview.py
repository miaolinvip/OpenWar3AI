# -*- coding: utf-8 -*-
"""远见的画板接口（09-25）：外部程序往游戏画面上画东西（运行时自绘，纯本机，多人局也安全）。只听本机。

    GET  /api/instances/{n}/canvas                          画板状态（画过几帧、现在有哪些东西）
    POST /api/instances/{n}/canvas  {"set": [...], "remove": [...], "clear": false}

set 里每一条：{"key": "hp", "kind": "text|panel|bar|image|circle|path", ...参数}，参数和 openwar3.canvas.Canvas 的方法一样：
    {"key": "t", "kind": "text", "text": "你好", "screen": [40, 120], "color": "#FFDC50", "bg": [0, 0, 0, 170]}
    {"key": "p", "kind": "panel", "title": "状态", "lines": ["心情：开心"], "screen": [-300, 160]}
    {"key": "hp", "kind": "bar", "value": 0.6, "unit": 599669636, "lift": 250}        单位写快照里的地址
    {"key": "zone", "kind": "circle", "center": [x, y], "radius": 400, "fill": [255, 0, 0, 50]}
    {"key": "route", "kind": "path", "points": [[x1, y1], [x2, y2]], "arrow": true}
锚点：screen / frac / world（坐标）或 unit（单位地址）；circle 的 center 可以是坐标或单位地址。
"""
from __future__ import annotations

import threading

from .jassview import _game_pid

_sessions: dict = {}           # 实例号 -> (游戏 pid, Canvas)
_lock = threading.Lock()
KINDS = ("text", "panel", "bar", "image", "circle", "path")


def _canvas(n: int):
    pid = _game_pid(n)
    if not pid:
        raise RuntimeError(f"实例 {n} 的游戏没在跑")
    cur = _sessions.get(n)
    if cur and cur[0] == pid:
        return cur[1]
    import openwar3
    c = openwar3.Game(pid=pid).canvas
    _sessions[n] = (pid, c)
    return c


def _unit(g, addr):
    s = g.snapshot(max_age=0)
    u = next((u for u in (s.units if s else []) if u.addr == int(addr)), None)
    if u is None:
        raise ValueError(f"快照里没有地址 {addr} 的单位")
    return u


def _is_addr(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)      # True/False 也是 int：center=true 是「居中」不是单位


def _apply(c, spec: dict) -> None:
    original = spec
    spec = dict(spec)
    key, kind = spec.pop("key", None), spec.pop("kind", None)
    if not key or kind not in KINDS:
        raise ValueError(f"每一条要有 key 和 kind（{'/'.join(KINDS)}）：{original}")
    # 单位地址 -> 快照里的单位。⚠ center 在 circle 里是圆心，在 text/bar/image 里是「居中对齐」开关
    for k in ("unit",) + (("center",) if kind == "circle" else ()):
        if _is_addr(spec.get(k)):
            spec[k] = _unit(c.g, spec[k])
    if kind == "path":
        spec["points"] = [_unit(c.g, p) if _is_addr(p) else tuple(p) for p in spec.get("points", [])]
    for k in ("screen", "frac", "world", "center"):
        if isinstance(spec.get(k), list):
            spec[k] = tuple(spec[k])
    if kind == "text":
        c.text(key, spec.pop("text", ""), **spec)
    elif kind == "panel":
        c.panel(key, spec.pop("title", ""), spec.pop("lines", []), **spec)
    elif kind == "bar":
        c.bar(key, spec.pop("value", 0.0), **spec)
    elif kind == "image":
        c.image(key, spec.pop("path", ""), **spec)
    elif kind == "circle":
        c.circle(key, spec.pop("center"), spec.pop("radius"), **spec)
    else:
        c.path(key, spec.pop("points"), **spec)


def update(n: int, body: dict) -> dict:
    with _lock:
        try:
            c = _canvas(n)
            with c.batch():
                if body.get("clear"):
                    c.items.clear()
                for key in body.get("remove") or []:
                    c.items.pop(str(key), None)
                for spec in body.get("set") or []:
                    _apply(c, spec)
            return {"ok": True, "keys": sorted(c.items), "stats": c.stats()}
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            return {"ok": False, "error": str(e)}


def status(n: int) -> dict:
    with _lock:
        try:
            c = _canvas(n)
            return {"ok": True, "keys": sorted(c.items), "stats": c.stats()}
        except (RuntimeError, ValueError) as e:
            return {"ok": False, "error": str(e)}

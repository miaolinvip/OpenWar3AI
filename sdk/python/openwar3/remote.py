# -*- coding: utf-8 -*-
"""远程调用层（09-25）：网关（gateway/，WebSocket / JSON）和大模型工具接口（openwar3.mcp）共用的一层。

  · 按角色的接口白名单：dev（本机工具，全部）/ player（能下令、能画界面，不能用沙盒改世界）/ observer（只读）
  · 参数里的单位：远端写 {"unit": 地址} 或 {"addr": 地址}（也可以带 "handle": [lo, hi] 核对），这里换成快照里的单位对象
  · 结果变 JSON：回执、单位、事件、世界状态都有固定的形状
  · 紧凑的局面视图：给远端和大模型读（一份几 KB，不是整张 1024 单位的表）
方法名：Game 上用 @api 登记的公开方法（docs/API_ZH.md），外加 ui.* / canvas.*（画界面）和 jass.<native>（只给 dev）。
"""
from __future__ import annotations

import dataclasses
import math

ROLES = ("dev", "player", "observer")
ROLE_CATEGORIES = {
    "dev": {"observe", "command", "control", "sandbox", "meta"},
    "player": {"observe", "command", "control", "meta"},
    "observer": {"observe", "meta"},
}
# 画界面：本机绘制 + 本机输入，observer 以外都给（on_click 这类回调从远端给不了：点击走事件流，带 .key）
SUB_METHODS = {
    "ui": {"button", "remove", "choice", "toast", "hotkey", "remove_hotkey", "mouse", "cursor"},
    "canvas": {"text", "panel", "bar", "image", "circle", "path", "remove", "clear", "hide", "stats"},
}
# 09-26 审查：player 角色（替某个玩家下场）原来拿到整个 control 类 —— 能关游戏进程、改倍速、暂停、拖慢所有人的快照。
# 这些管整局的只给 dev；公平模式下的选手也不该看到全体玩家的资源表、电脑对手的进攻目标。
DEV_ONLY = {"end_game", "set_speed", "pause", "set_publish_period", "press_to_continue"}
NOT_FOR_PLAYER = DEV_ONLY | {"players", "enemy_ai_plan"}
SELF_ONLY_PARAM = {"resources", "upkeep", "tech", "tech_many", "stats"}   # 带 player 参数的查询：选手只能查自己
_CATALOG_CACHE: dict = {}


class RemoteError(ValueError):
    pass


def catalog(role: str = "dev") -> list[dict]:
    """这个角色能调的方法（接口目录的子集）+ ui / canvas 子方法。按角色缓存（以前每次调用都 inspect 一遍一百多个方法）。"""
    if role in _CATALOG_CACHE:
        return _CATALOG_CACHE[role]
    import inspect
    from . import canvas as canvas_mod, ui as ui_mod
    from .catalog import entries
    cats = ROLE_CATEGORIES[role]
    out = [e for e in entries() if e["category"] in cats and not (role == "player" and e["name"] in NOT_FOR_PLAYER)]
    if role != "observer":
        classes = {"ui": ui_mod.UI, "canvas": canvas_mod.Canvas}
        for obj, names in SUB_METHODS.items():
            for n in sorted(names):
                if obj == "canvas" and n == "image" and role != "dev":
                    continue
                fn = getattr(classes[obj], n)
                sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
                doc = (inspect.getdoc(fn) or f"openwar3.{obj} 的 {n}").strip()
                out.append({"name": f"{obj}.{n}", "category": "control", "status": "verified",
                            "signature": f"{obj}.{n}{sig}", "doc": doc,
                            "mechanism": "画板 / 输入块（共享内存）", "latency": ""})
    _CATALOG_CACHE[role] = out
    return out


def resolve(g, name: str, role: str):
    """方法名 -> 可调用对象（过白名单）。"""
    if role not in ROLES:
        raise RemoteError(f"角色 {role!r} 不认识（{'/'.join(ROLES)}）")
    obj, _, sub = name.partition(".")
    if sub:
        if obj == "jass":
            if role != "dev":
                raise RemoteError("jass.* 只给 dev 角色（改世界只在单人局、本机工具里）")
            return getattr(g.jass, sub)
        if obj in SUB_METHODS and sub in SUB_METHODS[obj]:
            if role == "observer":
                raise RemoteError("observer 角色不能画界面")
            if obj == "canvas" and sub == "image" and role != "dev":
                raise RemoteError("canvas.image 会让游戏进程打开本机任意文件，只给 dev 角色")
            return getattr(getattr(g, obj), sub)
        raise RemoteError(f"没有方法 {name!r}")
    allowed = {e["name"] for e in catalog(role)}
    if name not in allowed:
        raise RemoteError(f"方法 {name!r} 不存在，或 {role} 角色不能调（docs/API_ZH.md；observer 只读，player 不能用沙盒、"
                          f"不能管整局（结束 / 倍速 / 暂停），公平模式下看不到别人的资源表）")
    attr = getattr(type(g), name, None)
    return getattr(g, name) if not isinstance(attr, property) else (lambda: getattr(g, name))


def resolve_args(g, v, _index=None):
    """参数里的 {"unit": 地址} / {"addr": 地址} -> 快照里的单位（找不到抛 RemoteError）；列表递归。"""
    if _index is None:
        _index = {}
    if isinstance(v, list):
        return [resolve_args(g, x, _index) for x in v]
    if isinstance(v, dict):
        key = "unit" if "unit" in v else ("addr" if "addr" in v else None)
        if key is not None and len(v) <= 3:
            if not _index:                     # 一次调用只建一次 地址 -> 单位 的表（以前每个单位参数都扫一遍快照）
                s = g.snapshot()
                for u in (s.units if s else []):
                    _index.setdefault(u.addr, []).append(u)
                _index.setdefault(None, [])
            addr = int(v[key])
            h = v.get("handle")
            for u in _index.get(addr, []):
                if not h or tuple(h) == u.handle:
                    return u
            raise RemoteError(f"快照里没有地址 {addr} 的单位（死了 / 被覆盖了？）")
        return {k: resolve_args(g, x, _index) for k, x in v.items()}
    return v


def call(g, name: str, role: str, args=None, kwargs=None):
    fn = resolve(g, name, role)
    index: dict = {}
    a = resolve_args(g, list(args or []), index)
    k = {str(key): resolve_args(g, val, index) for key, val in (kwargs or {}).items()}
    for bad in ("on_click", "on_press", "on_pick"):
        k.pop(bad, None)                       # 回调给不了；点击 / 热键从事件流收
    if role == "player" and name in SELF_ONLY_PARAM:
        import inspect
        try:
            asked = inspect.signature(fn).bind_partial(*a, **k).arguments.get("player")
        except TypeError as exc:
            raise RemoteError(f"{name} 参数不对：{exc}") from None
        mine = getattr(g, "player", None)
        if asked is not None and (mine is None or int(asked) != int(mine)):
            raise RemoteError(f"player 角色只能查自己（{name} 的 player 参数不给或者写 {mine}）")
    return to_json(fn(*a, **k))


# ── 变 JSON ─────────────────────────────────────────────────────
def unit_view(u, detail: bool = True) -> dict:
    d = {"addr": u.addr, "handle": [u.handle_lo, u.handle_hi], "type": u.type, "owner": u.owner,
         "x": round(u.x, 1), "y": round(u.y, 1), "hp": round(u.hp, 1), "hp_max": round(u.hp_max, 1)}
    if u.mana_max:
        d["mana"], d["mana_max"] = round(u.mana, 1), round(u.mana_max, 1)
    if u.order:
        d["order"] = u.order
    if u.is_hero:
        d["hero"] = True
        d["level"] = u.level
    if detail and u.abilities:
        d["abilities"] = [{"code": a.code, "level": a.level, "cooldown": round(a.cooldown, 1)} for a in u.abilities]
    if detail and any(u.items):
        d["items"] = u.items
    return d


EVENT_EXTRAS = ("spell", "key", "text", "chat", "button", "player", "frame")


def event_view(e) -> dict:
    d = {"seq": e.seq, "kind": e.kind, "clock": round(e.clock, 2), "addr": e.addr, "handle": list(e.handle),
         "type": e.type, "owner": e.owner, "a": e.a, "b": e.b, "x": round(e.x, 1), "y": round(e.y, 1),
         "value": round(e.value, 2), "extra": e.extra}
    for k in EVENT_EXTRAS:
        try:
            v = getattr(e, k)
        except AttributeError:
            continue
        if v not in (None, "", 0) or k == "chat" and v:
            d[k] = v
    if e.kind in ("ui.click", "ui.hover", "hotkey", "mouse.world"):
        d["mods"] = sorted(e.mods)
    return d


def to_json(v, depth: int = 0):
    """回执 / 单位 / 事件 / dataclass / 列表 -> 能 json.dumps 的东西。"""
    if depth > 6:
        return repr(v)
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (list, tuple, set, frozenset)):
        return [to_json(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): to_json(x, depth + 1) for k, x in v.items()}
    if isinstance(v, bytes):
        return None
    name = type(v).__name__
    if name == "Unit":
        return unit_view(v)
    if name == "Event":
        return event_view(v)
    if name in ("Receipt", "Pending"):
        r = v.receipt if name == "Pending" else v
        if r is None:
            return {"pending": True}
        return {"ok": r.ok, "status": r.status, "reason": getattr(r, "reason", None), "note": r.note,
                "values": list(r.values)}
    if name == "WorldState":
        return state_view(v)
    if dataclasses.is_dataclass(v):
        return {f.name: to_json(getattr(v, f.name), depth + 1) for f in dataclasses.fields(v)
                if not isinstance(getattr(v, f.name), bytes)}
    if hasattr(v, "__dict__"):
        return {k: to_json(x, depth + 1) for k, x in vars(v).items() if not k.startswith("_") and not callable(x)}
    return repr(v)


def state_view(s, units: str = "all", detail: bool = False, only_player: int | None = None) -> dict:
    """世界状态的 JSON：units = 'all' / 'mine' / 'none'。only_player：玩家表只留这一个（player 角色，公平模式）。"""
    if s is None:
        return {"in_game": False}
    us = [] if units == "none" else [u for u in s.units if u.alive and (units == "all" or u.owner == s.me)]
    players = s.players[:12] if only_player is None else [p for p in s.players[:12] if p.id == only_player]
    return {"clock": round(s.clock, 2), "me": s.me, "in_game": s.in_game, "epoch": s.epoch,
            "time_of_day": s.time_of_day, "players": [dataclasses.asdict(p) for p in players],
            "slot_states": s.slot_states, "selection": s.selection_addrs,
            "units": [unit_view(u, detail) for u in us],
            "production": [to_json(p) for p in s.production]}


def overview(g) -> dict:
    """给大模型的一页局面：资源、我方兵种数、英雄（血蓝等级冷却）、看得见的敌人、正在生产的。"""
    s = g.snapshot(max_age=0)
    if s is None:
        return {"in_game": False}
    mine = [u for u in s.units if u.alive and u.owner == s.me]
    foes = [u for u in s.units if u.alive and u.owner < 12 and u.owner != s.me]
    count = {}
    for u in mine:
        count[u.type] = count.get(u.type, 0) + 1
    fcount = {}
    for u in foes:
        fcount[u.type] = fcount.get(u.type, 0) + 1
    p = s.players[s.me] if 0 <= s.me < len(s.players) else None
    return {"clock": round(s.clock, 1), "me": s.me, "time_of_day": s.time_of_day,
            "gold": p.gold if p else None, "lumber": p.lumber if p else None,
            "food": f"{p.food_used}/{p.food_cap}" if p else None,
            "my_units": count, "heroes": [unit_view(u) for u in mine if u.is_hero],
            "enemy_units": fcount, "creeps": sum(1 for u in s.units if u.alive and u.owner == 12),
            "selection": [u.type for u in s.selected],
            "production": [{"building": list(x.building), "kind": x.kind, "queue": x.queue,
                            "progress": round(x.progress, 2) if x.progress is not None else None} for x in s.production]}

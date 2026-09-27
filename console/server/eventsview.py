# -*- coding: utf-8 -*-
"""远见「事件与输入」页的后端（09-25）：读这一局的事件环、屏幕消息、输入块、本机选中。只读，不改游戏。

    GET /api/i/{n}/events?since=<上次看到的 seq>&limit=400
    -> {"ok", "pid", "head", "dropped", "events": [...], "input": {...} | None, "selection": [...], "clock"}

每个实例在后端进程里开一个 w3world.World（游戏换局换 pid 时重开）；读到的事件解码成 JSON 放进 3000 条的环，
网页带上次看到的 seq 取增量（多个页面同时看也互不影响）。第一次打开先倒回最近 400 条，页面一打开就有东西看。
新事件（spell.cast / message / ui.click …）的字段见 docs/W3P_PROTOCOL_ZH.md；聊天用 w3world.parse_chat 拆出发言人和内容。
"""
from __future__ import annotations

import threading
from collections import deque

from . import common as C              # 先导它：它把 sdk/python 加进 sys.path
from .jassview import _game_pid

import w3world as W                    # noqa: E402

_lock = threading.Lock()
_state: dict[int, dict] = {}           # 实例号 -> {"pid", "world", "ring", "noisy", "input"}
RING = 3000
BACKFILL = 8192                        # 第一次打开把事件环整个倒回来（高频的另放一个小环，挤不掉有用的）
# 高频事件（一局几万条）单独一个小环，默认不返回：09-25 实测 9 号实例最近 400 条里 395 条是 order.changed
NOISY = frozenset(("order.changed", "unit.damaged", "damage", "ui.hover"))
NOISY_RING = 1500


def _open(n: int) -> dict:
    pid = _game_pid(n)
    if not pid:
        raise RuntimeError(f"实例 {n} 的游戏没在跑")
    st = _state.get(n)
    if st and st["pid"] == pid:
        return st
    if st:
        try:
            st["world"].close()
        except Exception:  # noqa: BLE001
            pass
    w = W.World(pid)
    w.rewind(BACKFILL)
    st = _state[n] = {"pid": pid, "world": w, "ring": deque(maxlen=RING), "noisy": deque(maxlen=NOISY_RING), "input": None}
    return st


def _input_state(st: dict) -> dict | None:
    """输入块（界面与输入）：鼠标在哪、指着的地面点、悬停的画板项、吞掉的点击数。老 DLL / 还没建块 -> None。"""
    if st["input"] is None:
        try:
            import w3input
            st["input"] = w3input.InputBlock(st["pid"])
        except Exception:  # noqa: BLE001 - 老运行时没有输入块
            st["input"] = False
    if not st["input"]:
        return None
    s = st["input"].state()
    return {"enabled": s["enabled"], "cursor": s["cursor"], "world": [round(v) for v in s["world"]] if s["world"] else None,
            "hover": s["hover"], "clicks": s["clicks"], "keys": s["keys"], "back": s["back"]}


def _decode(w, e, state) -> dict:
    d = {"seq": e.seq, "kind": e.kind, "clock": round(e.clock, 2), "addr": e.addr, "type": e.type, "owner": e.owner,
         "x": round(e.x), "y": round(e.y), "value": round(e.value, 2)}
    k = e.kind
    if k == "spell.cast":
        d.update(spell=e.spell, spellName=C.ability_names().get(e.spell), level=e.b, cooldown=round(e.value, 1))
    elif k == "message":
        m = w.message(e.message_seq)
        text = m["text"] if m else None
        d.update(text=text, frame=e.b, chat=W.parse_chat(text) if text else None)
    elif k in ("damage", "killed"):
        src = state.unit_by_addr(e.source_addr) if (state is not None and e.source_addr) else None
        d.update(source=e.source_addr, sourceType=src.type if src else None, sourceOwner=src.owner if src else None,
                 attack=e.attack_type, damageType=e.damage_type, raw=round(e.raw_damage, 1))
    elif k == "production.done":
        d.update(done=e.done_code, doneKind=e.done_kind)
    elif k == "player.left":
        d.update(player=e.player, slot=W.SLOT_STATES.get(e.b, str(e.b)))
    elif k in ("ui.click", "ui.hover"):
        d.update(item=e.item_id, button=e.button, mods=sorted(e.mods))
    elif k == "hotkey":
        d.update(hotkey=e.hotkey_id, vk=e.b, mods=sorted(e.mods))
    elif k == "mouse.world":
        d.update(button=e.button, swallowed=bool(e.value), mods=sorted(e.mods))
    elif k == "selection.changed":
        d.update(count=e.a)
    return d


def events(n: int, since: int = 0, limit: int = 400, noisy: bool = False) -> dict:
    """noisy=True 连高频事件（换订单、掉血、每一下伤害、悬停）一起给。"""
    with _lock:
        try:
            st = _open(n)
            w = st["world"]
            try:
                state = w.read()
            except Exception:  # noqa: BLE001 - 读世界失败不影响收事件（damage 的来源就不解析了）
                state = None
            for e in w.events(limit=BACKFILL):
                (st["noisy"] if e.kind in NOISY else st["ring"]).append(_decode(w, e, state))
            ring = st["ring"]
            pool = sorted(list(ring) + list(st["noisy"]), key=lambda d: d["seq"]) if noisy else ring
            out = [d for d in pool if d["seq"] > since][-max(1, min(limit, RING)):]
            head = max(ring[-1]["seq"] if ring else 0, st["noisy"][-1]["seq"] if st["noisy"] else 0)
            sel = []
            if state is not None:
                for a in getattr(state, "selection_addrs", None) or []:
                    u = state.unit_by_addr(a)
                    if u is not None:
                        sel.append({"addr": a, "type": u.type, "owner": u.owner, "hp": round(u.hp), "hpMax": round(u.hp_max)})
            return {"ok": True, "pid": st["pid"], "head": head, "dropped": w.dropped,
                    "events": out, "input": _input_state(st), "selection": sel,
                    "clock": round(state.clock, 1) if state is not None else None}
        except (RuntimeError, W.WorldUnavailable, OSError) as exc:
            return {"ok": False, "error": str(exc)}

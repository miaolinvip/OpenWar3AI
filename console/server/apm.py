# -*- coding: utf-8 -*-
"""指令速度（APM）：AI 每游戏分钟下了多少条令，折算成人类操作后和人类 APM 放在一起比。

数据全来自实例目录里的日志，不连游戏：
  cmdlog.log      一令一行「日期 时间 来源 单位 订单 方法 结果 [目标=]」（w3cmd 包的六个下令出口 + 大脑 act 补记的四条）
  brain.log       「第 N 局开始/结束」划对局；「心跳 游戏 X 秒」把墙钟换成游戏时间（倍速局一墙钟分钟约 3 游戏分钟）
  mod_combat.log  「（NNNms 内响应）」= 毫秒层从看到危险到下完撤退令的延迟

两种口径：
  原始   每个单位单独一条令（AI 给 12 个兵下攻击令 = 12 条）
  折算   同一秒、同一来源、同一订单、同一目标的令合并成一次点击；这批单位和这个来源上一批不一样，再加一次「选中」
         —— 人类选中一队兵右键一下是 1~2 个操作，所以这个数才能和人类 APM 比。
人类 APM 用游戏内时间算（录像里的 APM 就是这样），这里也按游戏时间折算，不按墙钟。
"""
from __future__ import annotations

import os
import re
import statistics
import threading
import time
from datetime import datetime

from . import common as C

CMD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\S+) 0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8}) (\S+) (\S+)(?: 目标=0x([0-9A-Fa-f]{8}))?")
START_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 第 (\d+) 局开始：(match-\S+?\.jsonl)")
END_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 第 (\d+) 局结束：([^｜\s]+)")
BEAT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 心跳 游戏 (\d+) 秒")
REACT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*（(\d+)ms 内响应）")

SOURCE_LABEL = {
    "brain": "大脑（战略 · 战术 · 经济）",
    "mod_combat": "毫秒层（保命 · 施法 · 补攻击）",
    "mod_scavenge": "捡装备",
    "mod_worker": "派工",
    "console": "控制台手动",
}

_cache: dict = {}
_lock = threading.Lock()


def _epoch(ts: str) -> float:
    return time.mktime(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timetuple())


def _lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _segments(brain_lines: list[str]) -> list[dict]:
    segs, cur = [], None
    for ln in brain_lines:
        m = START_RE.match(ln)
        if m:
            cur = {"no": int(m.group(2)), "name": m.group(3), "startTs": m.group(1), "start": _epoch(m.group(1)),
                   "end": None, "outcome": None, "beats": []}
            segs.append(cur)
            continue
        if cur is None:
            continue
        m = BEAT_RE.match(ln)
        if m:
            cur["beats"].append((_epoch(m.group(1)), float(m.group(2))))
            continue
        m = END_RE.match(ln)
        if m and int(m.group(2)) == cur["no"]:
            cur["end"] = _epoch(m.group(1))
            cur["outcome"] = m.group(3)
            cur = None
    return segs


def _clock(seg: dict, default_speed: float):
    """墙钟 -> 游戏秒。两条以上心跳按首尾拟合倍速；一条按默认倍速挂在它上面；没有就从开局算。"""
    beats = seg["beats"]
    if len(beats) >= 2 and beats[-1][0] > beats[0][0]:
        speed = (beats[-1][1] - beats[0][1]) / (beats[-1][0] - beats[0][0])
        w0, g0 = beats[0]
    elif beats:
        speed = default_speed
        w0, g0 = beats[0]
    else:
        speed, w0, g0 = default_speed, seg["start"], 1.5
    speed = speed if 0.2 <= speed <= 10.0 else default_speed
    return (lambda w: max(0.0, g0 + speed * (w - w0))), speed


def _analyze(seg: dict, acts: list[tuple], reacts: list[int], default_speed: float, now: float) -> dict:
    g_of, speed = _clock(seg, default_speed)
    live = seg["end"] is None
    g_end = g_of(seg["end"] if not live else now)
    minutes = max(g_end / 60.0, 0.05)
    by_src_raw, by_src_eq = {}, {}
    groups = {}
    order_keys = []
    for w, ts, src, unit, order, how, target in acts:
        by_src_raw[src] = by_src_raw.get(src, 0) + 1
        key = (ts, src, order, target or how)
        if key not in groups:
            groups[key] = (w, set())
            order_keys.append(key)
        groups[key][1].add(unit)
    last_sel: dict = {}
    eq_events = []                       # (游戏秒, 来源, 操作数)
    for key in order_keys:
        w, units = groups[key]
        src = key[1]
        n = 1
        sel = frozenset(units)
        if last_sel.get(src) != sel:
            n += 1
            last_sel[src] = sel
        by_src_eq[src] = by_src_eq.get(src, 0) + n
        eq_events.append((g_of(w), src, n))
    raw_events = [(g_of(a[0]), a[2]) for a in acts]
    n_min = int(g_end // 60) + 1
    timeline = [{"minute": i, "raw": 0, "eq": 0, "src": {}} for i in range(max(1, n_min))]
    for g, src in raw_events:
        i = min(int(g // 60), len(timeline) - 1)
        timeline[i]["raw"] += 1
        timeline[i]["src"][src] = timeline[i]["src"].get(src, 0) + 1
    for g, src, n in eq_events:
        timeline[min(int(g // 60), len(timeline) - 1)]["eq"] += n
    if timeline and live and g_end % 60 < 20 and len(timeline) > 1:
        timeline = timeline[:-1]          # 进行中那一分钟不满 20 秒，画出来是个假低谷

    def peak(events, weight):
        buckets = {}
        for e in events:
            b = int(e[0] // 10)
            buckets[b] = buckets.get(b, 0) + weight(e)
        return max(buckets.values()) * 6 if buckets else 0

    raw_total = len(acts)
    eq_total = sum(n for _g, _s, n in eq_events)
    out = {
        "no": seg["no"], "name": seg["name"], "start": seg["startTs"], "live": live, "outcome": seg["outcome"],
        "speed": round(speed, 2), "gameMinutes": round(g_end / 60.0, 2),
        "raw": {"total": raw_total, "apm": round(raw_total / minutes, 1), "peak10s": peak(raw_events, lambda e: 1)},
        "eq": {"total": eq_total, "apm": round(eq_total / minutes, 1), "peak10s": peak(eq_events, lambda e: e[2])},
        "sources": sorted(({"key": s, "label": SOURCE_LABEL.get(s, s), "raw": by_src_raw.get(s, 0), "eq": by_src_eq.get(s, 0),
                            "rawApm": round(by_src_raw.get(s, 0) / minutes, 1), "eqApm": round(by_src_eq.get(s, 0) / minutes, 1)}
                           for s in by_src_raw), key=lambda r: -r["raw"]),
        "timeline": timeline,
        "reaction": ({"n": len(reacts), "median": int(statistics.median(reacts)), "p90": int(sorted(reacts)[int(0.9 * (len(reacts) - 1))]),
                      "min": min(reacts)} if reacts else None),
        "complete": any(a[5] == "gather_kernel" for a in acts),
    }
    if live:
        recent_raw = sum(1 for g, _s in raw_events if g >= g_end - 60)
        recent_eq = sum(n for g, _s, n in eq_events if g >= g_end - 60)
        span = min(1.0, max(g_end, 1.0) / 60.0)
        out["lastMinute"] = {"raw": round(recent_raw / span, 1), "eq": round(recent_eq / span, 1)}
    return out


def summary(n: int, matches: int = 8) -> dict:
    d = C.inst_dir(n)
    paths = {k: os.path.join(d, f"{k}.log") for k in ("cmdlog", "brain", "mod_combat")}
    sig = tuple((os.path.getsize(p), os.path.getmtime(p)) if os.path.exists(p) else None for p in paths.values())
    with _lock:
        hit = _cache.get((n, matches))
        if hit and hit[0] == sig and time.time() - hit[1] < 2.0:
            return hit[2]
    ng = C.load_json(os.path.join(d, "next_game.json"), {}) or {}
    default_speed = (ng.get("speed") or 300) / 100.0
    segs = [s for s in _segments(_lines(paths["brain"])) if s["end"] is None or s["end"] - s["start"] >= 30]
    segs = segs[-max(1, matches):]
    now = time.time()
    result = {"inst": n, "generatedAt": now, "matches": []}
    if not segs:
        return result
    first_ts = segs[0]["startTs"]
    acts_by, react_by = {id(s): [] for s in segs}, {id(s): [] for s in segs}

    def owner(w):
        for s in reversed(segs):
            if w >= s["start"] and (s["end"] is None or w <= s["end"] + 1):
                return s
        return None

    for ln in _lines(paths["cmdlog"]):
        if ln[:19] < first_ts:
            continue
        m = CMD_RE.match(ln)
        if not m or m.group(6) == "让出":
            continue
        w = _epoch(m.group(1))
        s = owner(w)
        if s is not None:
            acts_by[id(s)].append((w, m.group(1), m.group(2), m.group(3).upper(), m.group(4).upper(), m.group(5),
                                   m.group(7).upper() if m.group(7) else None))
    for ln in _lines(paths["mod_combat"]):
        if ln[:19] < first_ts or "内响应" not in ln:
            continue
        m = REACT_RE.match(ln)
        if m:
            s = owner(_epoch(m.group(1)))
            if s is not None:
                react_by[id(s)].append(int(m.group(2)))
    result["matches"] = [_analyze(s, acts_by[id(s)], react_by[id(s)], default_speed, now) for s in reversed(segs)]
    live = C.load_json(os.path.join(d, "brain_live.json"), {}) or {}
    act = live.get("act") or {}
    if result["matches"] and result["matches"][0]["live"] and isinstance(act, dict):
        result["matches"][0]["restraint"] = {
            "sameOrderSkipped": sum(v for k, v in act.items() if "同令不重下" in k),
            "yielded": sum(v for k, v in act.items() if "抢不到指挥权" in k),
        }
    with _lock:
        _cache[(n, matches)] = (sig, time.time(), result)
    return result

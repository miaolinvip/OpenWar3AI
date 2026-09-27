# -*- coding: utf-8 -*-
"""问题备忘：测试时发现的问题记在这里（Farsight「问题备忘」页）。

09-19 新增：后台的问题备忘，用来记录测试中遇到的任何问题。记录时可选进程和时间点（关联该时间点附近的事件），
也可以在游戏里直接按快捷键记下当前进程的问题。

两个来源：
  * 游戏里按快捷键（DLL 动作 57 -> 新大脑监督器 -> inst<N>/issue_marks.jsonl，见 war3-brain/brain/issuemark.py）：
    这里扫各实例的标记文件，没收过的变成「待填写」的问题 —— 实例号、游戏 pid、局号、对局文件、游戏时间点都带好了
  * 网页上新建：选实例、选哪一局、填游戏时间点
存储：war3-analytics/issues.json（只有 Farsight 这一个进程写；原子替换）。标记文件只追加，按字节偏移 + 标记 id 去重，
删掉的问题不会被重新收回来。
附近事件（context）：那一局对局文件里时间点前后 window 游戏秒的 AI 动作 / 状态 / 敌情，
再把这段游戏时间按对局文件里的墙钟换算过去，捞监督器（brain.log）和毫秒层（mod_combat / mod_scavenge）同一段的日志。
"""
from __future__ import annotations

import bisect
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from . import common as C

STORE = os.path.join(C.BIN, "issues.json")
MARKS_FILE = "issue_marks.jsonl"          # war3-brain/brain/issuemark.py MARKS_FILE 同一个文件名
STATUSES = ("draft", "open", "fixed", "wontfix")   # 待填写 / 待处理 / 已解决 / 不是问题
FIELDS = ("status", "title", "note", "category", "inst", "match", "game", "gameTime", "window", "pid", "exe")
LOGS = (("brain", "监督器"), ("mod_combat", "毫秒层·保命施法"), ("mod_scavenge", "毫秒层·捡装备"))
LOG_CAP = 300                              # 每个日志文件最多给这么多行

_lock = threading.Lock()


def _load() -> dict:
    d = C.load_json(STORE, None) or {}
    d.setdefault("issues", [])
    d.setdefault("offsets", {})
    d.setdefault("seenMarks", [])
    return d


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def _inst_dirs() -> list[tuple[int, str]]:
    out = []
    try:
        names = os.listdir(C.BIN)
    except OSError:
        return out
    for name in names:
        m = re.fullmatch(r"inst(\d+)", name)
        if m:
            out.append((int(m.group(1)), os.path.join(C.BIN, name)))
    return sorted(out)


def _game_no(match: str | None) -> int | None:
    m = re.match(r"match-\d{8}-\d{6}-(\d+)", match or "")
    return int(m.group(1)) if m else None


def ingest(d: dict) -> int:
    """把各实例 issue_marks.jsonl 里新追加的标记收成「待填写」的问题。返回收了几条。调用方持锁。"""
    seen = set(d["seenMarks"])
    added = 0
    for n, idir in _inst_dirs():
        path = os.path.join(idir, MARKS_FILE)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        key = f"inst{n}"
        pos = int(d["offsets"].get(key, 0))
        if size < pos:                        # 文件被删过重建：从头读，靠 seenMarks 去重
            pos = 0
        if size == pos:
            continue
        with open(path, "rb") as fh:
            fh.seek(pos)
            chunk = fh.read(size - pos)
        end = chunk.rfind(b"\n") + 1
        d["offsets"][key] = pos + end
        for ln in chunk[:end].decode("utf-8", "replace").splitlines():
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(m, dict) or not m.get("id") or m["id"] in seen:
                continue
            seen.add(m["id"])
            d["seenMarks"].append(m["id"])
            now = time.time()
            d["issues"].append({
                "id": "i-" + uuid.uuid4().hex[:8], "status": "draft", "title": "", "note": "", "category": "",
                "inst": m.get("inst", n), "pid": m.get("pid"), "exe": m.get("exe"), "match": m.get("match"),
                "game": m.get("game") if m.get("game") is not None else _game_no(m.get("match")),
                "gameTime": m.get("gameTime"), "window": 30, "source": m.get("source", "hotkey"), "markId": m["id"],
                "kernel": m.get("kernel"), "kernelRev": m.get("kernelRev"), "at": m.get("at") or now,
                "created": now, "updated": now,
            })
            added += 1
    d["seenMarks"] = d["seenMarks"][-5000:]
    return added


def listing() -> dict:
    with _lock:
        d = _load()
        if ingest(d):
            _save(d)
    items = sorted(d["issues"], key=lambda x: -(x.get("at") or x.get("created") or 0))
    return {"issues": items, "pending": sum(1 for x in items if x.get("status") == "draft")}


def pending() -> int:
    return listing()["pending"]


def _clean(patch: dict) -> dict:
    out = {}
    for k in FIELDS:
        if k not in patch:
            continue
        v = patch[k]
        if k == "status" and v not in STATUSES:
            raise ValueError(f"状态只能是 {STATUSES}")
        if k in ("inst", "game", "pid", "window") and v is not None:
            v = int(v)
        if k == "gameTime" and v is not None:
            v = round(float(v), 1)
        if k in ("title", "note", "category", "match", "exe") and v is not None:
            v = str(v)[:4000]
        out[k] = v
    if "window" in out and out["window"] is not None:
        out["window"] = max(5, min(600, out["window"]))
    if "match" in out and out["match"] and not re.fullmatch(r"match-[\w\-]+\.jsonl", out["match"]):
        raise ValueError("对局文件名不对")
    return out


def create(body: dict) -> dict:
    """网页上新建。没给对局 = 那个实例最新一局；没给时间点 = 那一局记到的最后一个游戏时间。"""
    it = _clean(body)
    if it.get("inst") is None:
        raise ValueError("要选实例")
    n = it["inst"]
    if not it.get("match"):
        d = os.path.join(C.inst_dir(n), "matches")
        try:
            files = sorted(f for f in os.listdir(d) if f.startswith("match-") and f.endswith(".jsonl"))
        except OSError:
            files = []
        it["match"] = files[-1] if files else None
    if it.get("gameTime") is None and it.get("match"):
        it["gameTime"] = context(n, it["match"], None, 0)["t"]
    now = time.time()
    issue = {"id": "i-" + uuid.uuid4().hex[:8], "status": "open", "title": "", "note": "", "category": "", "window": 30,
             "pid": None, "exe": None, "source": "web", "markId": None, "kernel": None, "kernelRev": None,
             "at": now, "created": now, "updated": now}
    issue.update(it)
    issue["game"] = issue.get("game") if issue.get("game") is not None else _game_no(issue.get("match"))
    with _lock:
        d = _load()
        ingest(d)
        d["issues"].append(issue)
        _save(d)
    return issue


def update(iid: str, patch: dict) -> dict:
    p = _clean(patch)
    with _lock:
        d = _load()
        for x in d["issues"]:
            if x["id"] == iid:
                x.update(p)
                if x.get("status") == "draft" and (x.get("title") or x.get("note")) and "status" not in p:
                    x["status"] = "open"          # 补了描述就从「待填写」变「待处理」
                if "match" in p:
                    x["game"] = _game_no(x.get("match"))
                x["updated"] = time.time()
                _save(d)
                return x
    raise KeyError(iid)


def delete(iid: str) -> bool:
    with _lock:
        d = _load()
        before = len(d["issues"])
        d["issues"] = [x for x in d["issues"] if x["id"] != iid]
        if len(d["issues"]) == before:
            return False
        _save(d)
        return True


def get(iid: str) -> dict:
    for x in listing()["issues"]:
        if x["id"] == iid:
            return x
    raise KeyError(iid)


# ── 附近事件 ─────────────────────────────────────────────────────────────
def _epoch(utc: str | None) -> float | None:
    if not utc:
        return None
    try:
        return datetime.fromisoformat(utc).timestamp()
    except ValueError:
        return None


def _wall_at(pairs: list[tuple[float, float]], g: float) -> float | None:
    """游戏秒 -> 墙钟（对局文件里每条记录都有游戏秒和 utc）。暂停时游戏秒不动、墙钟在走：取那一段的第一条。"""
    if not pairs:
        return None
    gs = [p[0] for p in pairs]
    i = bisect.bisect_left(gs, g)
    if i <= 0:
        return pairs[0][1] - (pairs[0][0] - g) / 3.0          # 前面没有：按 3 倍速外推
    if i >= len(pairs):
        return pairs[-1][1] + (g - pairs[-1][0]) / 3.0
    (g0, w0), (g1, w1) = pairs[i - 1], pairs[i]
    return w0 if g1 <= g0 else w0 + (g - g0) * (w1 - w0) / (g1 - g0)


def _log_lines(path: str, t_from: float, t_to: float, cap: int = LOG_CAP) -> list[dict]:
    """「YYYY-MM-DD HH:MM:SS 内容」按时间追加的日志：二分查到 t_from 那一行，读到 t_to。"""
    def stamp(line: bytes) -> float | None:
        try:
            return time.mktime(time.strptime(line[:19].decode("ascii"), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, UnicodeDecodeError):
            return None
    try:
        size = os.path.getsize(path)
        fh = open(path, "rb")
    except OSError:
        return []
    out = []
    with fh:
        lo, hi = 0, size
        while hi - lo > 4096:                                  # 找第一行时间 >= t_from 的大致位置
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            ts = None
            for _ in range(20):
                ln = fh.readline()
                if not ln:
                    break
                ts = stamp(ln)
                if ts is not None:
                    break
            if ts is None or ts >= t_from:
                hi = mid
            else:
                lo = mid
        fh.seek(lo)
        if lo:
            fh.readline()
        cur = None
        for ln in fh:
            ts = stamp(ln)
            if ts is not None:
                cur = ts
            if cur is None or cur < t_from:
                continue
            if cur > t_to + 1:
                break
            text = ln.decode("utf-8", "replace").rstrip("\r\n")
            out.append({"wall": cur, "text": text[20:] if ts is not None else text})
            if len(out) >= cap:
                break
    return out


def context(n: int, match: str, t: float | None, window: float) -> dict:
    """那一局 t 前后 window 游戏秒的事件。t = None：用那一局记到的最后一个游戏时间（进行中的局 = 现在）。
    游戏秒一律是「相对开局」（和对局时间线、问题的 gameTime 一个口径；state 记的是引擎时钟，减掉开局时刻）。"""
    if not re.fullmatch(r"match-[\w\-]+\.jsonl", match or ""):
        raise ValueError("对局文件名不对")
    path = os.path.join(C.inst_dir(n), "matches", match)
    acts, states, intel, pairs = [], [], [], []
    t0 = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            k, g, d = r.get("kind"), r.get("gameElapsedSec"), r.get("data")
            if g is None:
                continue
            if k == "state" and isinstance(d, dict):
                if t0 is None:
                    t0 = g
                rel = g - t0
                states.append({"t": rel, "gold": d.get("gold"), "lumber": d.get("lumber"), "food": f"{d.get('foodUsed')}/{d.get('foodCap')}",
                               "army": d.get("army"), "heroes": d.get("heroes"), "workers": d.get("workers"),
                               "ownStr": d.get("ownStr"), "foeStr": d.get("foeStr"), "attack": d.get("attack"), "defense": d.get("defense"),
                               "why": d.get("why")})
            elif k == "action":
                rel = g
                acts.append({"t": g, "text": d.get("text") if isinstance(d, dict) else str(d)})
            elif k == "intel" and isinstance(d, dict):
                rel = g - (t0 or 0)
                intel.append({"t": rel, "army": d.get("army"), "composition": d.get("composition"), "race": d.get("race")})
            else:
                continue
            w = _epoch(r.get("utc"))
            if w is not None:
                pairs.append((rel, w))
    pairs.sort()
    last = max([p[0] for p in pairs] or [0])
    if t is None:
        t = last
    lo, hi = t - window, t + window
    around = {"t": t, "window": window, "lastT": last, "match": match, "inst": n,
              "actions": [a for a in acts if lo <= a["t"] <= hi][:600],
              "states": [s for s in states if lo - 10 <= s["t"] <= hi + 10],
              "intel": [x for x in intel if lo - 10 <= x["t"] <= hi + 10],
              "logs": {}}
    w_lo, w_hi = _wall_at(pairs, lo), _wall_at(pairs, hi)
    if w_lo is not None and w_hi is not None and window > 0:
        around["wall"] = [w_lo, w_hi]
        for key, label in LOGS:
            rows = _log_lines(os.path.join(C.inst_dir(n), f"{key}.log"), w_lo, w_hi)
            if rows:
                around["logs"][key] = {"label": label, "lines": rows}
    return around


def context_of(iid: str, window: float | None = None) -> dict:
    x = get(iid)
    if not x.get("match") or x.get("inst") is None:
        raise ValueError("这条问题没有对局")
    return context(int(x["inst"]), x["match"], x.get("gameTime"), float(window or x.get("window") or 30))

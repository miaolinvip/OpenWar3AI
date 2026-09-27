# -*- coding: utf-8 -*-
"""对局文件：列表（带结局摘要）和详情（资源/兵力曲线、动作时间线、lesson）。

列表每局一份摘要（_summary）：局号/episode、哪个进程跑的（实例、游戏 pid、exe、监督器 pid）、内核版本、双方种族、
开局设置（地图/难度/倍速）、结局和结局依据、峰值兵力我/敌、分矿、最高本、局末强度、墙钟时长。
整份文件解析一次后按 (mtime, size) 缓存，只有还在写的那局会重读。

★ 09-26 数据多了以后（旧仓库 4851 局、1.6 GB）：
  * 摘要存盘（console/cache/match_index.json）：远见重启后不用把几千个文件重新解析一遍；
  * 还在写的那局最多 LIVE_REPARSE_S 秒重读一次（以前每次请求都整份重读，7 个实例同时在打就是 7 份）；
  * overview()：统计、分组（按实例 / 内核 / 开局谱）、筛选、分页都在这里算，网页只拿一页（以前把 600 局整份发给网页，
    每 20 秒一次，网页上 2.4 万个节点整表重画，浏览器卡死、连鼠标都不跟手）。
"""
from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter

from . import common as C

# 内核版本号 = 动过这两处的提交数；和 war3-brain/run_brain.py 的 KERNEL_PATHS 是同一组（改一处要改两处）。
# 新局开局时监督器自己记（kernel/kernelRev/kernelDirty）；没记的老局按开局时间对齐到「开局前最近一次大脑代码提交」，标 approx。
KERNEL_PATHS = ("brains/xwar3/strategy/brain", "brains/xwar3/strategy/run_brain.py")
TIER = {"hkee": 2, "hcas": 3, "ostr": 2, "ofrt": 3, "unp1": 2, "unp2": 3, "etoa": 2, "etoe": 3}

_lock = threading.Lock()
_cache: dict[str, tuple[float, int, dict, float]] = {}      # 路径 -> (mtime, size, 摘要, 解析时刻)
_kern: dict = {"at": 0.0, "list": []}

INDEX_PATH = os.path.join(C.FARSIGHT, "cache", "match_index.json")
INDEX_VERSION = 1                 # _parse 的输出改了就 +1（老索引整份作废）
LIVE_S = 90.0                     # 90 秒内还在写 = 进行中
LIVE_REPARSE_S = 15.0             # 进行中的那局最多这么久重读一次
_index: dict = {"loaded": False, "saved": 0.0, "sig": None}
UNRELIABLE = {"crash", "ended-stuck", "ended-by-operator"}   # 结局不可信的不进胜率（和网页、tuner.py 同一口径）


def _dir(n: int) -> str:
    return os.path.join(C.inst_dir(n), "matches")


def kernels() -> list[dict]:
    """大脑代码的提交历史（旧 -> 新），n = 内核版本号。60 秒缓存。"""
    with _lock:
        if time.time() - _kern["at"] < 60:
            return _kern["list"]
    try:
        out = subprocess.run(["git", "-C", C.ROOT, "--no-optional-locks", "log", "--reverse", "--format=%h%x09%ct%x09%s", "--", *KERNEL_PATHS],
                             capture_output=True, text=True, encoding="utf-8", timeout=15, creationflags=0x08000000).stdout
        lst = []
        for ln in out.splitlines():
            parts = ln.split("\t", 2)
            if len(parts) == 3 and parts[1].isdigit():
                lst.append({"n": len(lst) + 1, "rev": parts[0], "at": int(parts[1]), "subject": parts[2][:300]})
    except Exception:  # noqa: BLE001
        lst = _kern["list"]
    with _lock:
        _kern.update(at=time.time(), list=lst)
    return lst


def _kernel_at(ts: float) -> dict | None:
    lst = kernels()
    i = bisect.bisect_right([k["at"] for k in lst], ts)
    return {"kernel": lst[i - 1]["n"], "kernelRev": lst[i - 1]["rev"], "approx": True} if i else None


def _opening(exp: dict) -> dict:
    """这局用的开局谱：experiment.config 里的 opening（没有 = none）+ 中文名（war3-brain/brain/openings/*.json）。"""
    oid = str((exp.get("config") or {}).get("opening") or "none")
    try:
        if C.BRAIN not in sys.path:
            sys.path.insert(0, C.BRAIN)
        from brain import opening as OP
        name = OP.labels().get(oid, oid)
    except Exception:  # noqa: BLE001
        name = "不用（按建造表）" if oid == "none" else oid
    return {"id": oid, "name": name}


def _loads(ln: str):
    try:
        return json.loads(ln)
    except ValueError:
        return None


def _parse(path: str, name: str) -> dict:
    start = exp = res = lesson = fin = None
    first_state = last_state = prev_state = None
    first_state_rec = None
    n_states = n_actions = 0
    max_halls = 0
    tier = 1
    max_own = 0.0
    foe_races: dict[str, int] = {}
    peak_foe = 0
    first_t = last_t = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            head = ln[:160]
            if '"kind":"action"' in head:
                n_actions += 1
                continue
            r = _loads(ln)
            if not isinstance(r, dict):
                continue
            k = r.get("kind")
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            if first_t is None:
                first_t = r.get("wallElapsedSec")
            last_t = r.get("wallElapsedSec", last_t)
            if k == "state":
                n_states += 1
                if first_state is None:
                    first_state, first_state_rec = d, r
                prev_state, last_state = last_state, r
                max_halls = max(max_halls, int(d.get("halls") or 0))
                for code in re.findall(r"([A-Za-z0-9]{4})=", str(d.get("units") or "")):
                    tier = max(tier, TIER.get(code, 1))
                try:
                    max_own = max(max_own, float(d.get("ownStr") or 0))
                except (TypeError, ValueError):
                    pass
            elif k == "intel":
                rc = d.get("race")
                if rc:
                    foe_races[rc] = foe_races.get(rc, 0) + 1
                try:
                    peak_foe = max(peak_foe, int(d.get("army") or 0))
                except (TypeError, ValueError):
                    pass
            elif k == "start" and start is None:
                start = d
            elif k == "experiment" and exp is None:
                exp = d
            elif k == "result":
                res, res_rec = d, r
            elif k == "lesson":
                lesson = d
            elif k == "finalized":
                fin = r
    m = re.match(r"match-(\d{8})-(\d{6})(?:-(\d+))?(?:-([0-9a-f]{8}))?", name)
    date, tm = (m.group(1), m.group(2)) if m else (None, None)
    start_ts = time.mktime(time.strptime(date + tm, "%Y%m%d%H%M%S")) if m else None
    exp = exp or {}
    start = start or {}
    brain = exp.get("brain") == "war3-brain" or start.get("brain") == "war3-brain"
    plan = exp.get("planVer")
    kernel = None
    if brain:
        if exp.get("kernel"):
            kernel = {"kernel": exp["kernel"], "kernelRev": exp.get("kernelRev"), "dirty": bool(exp.get("kernelDirty")),
                      "combatVersion": exp.get("combatVersion"), "combatRev": exp.get("combatRev"),
                      "combatDirty": bool(exp.get("combatDirty")), "approx": False}
        elif start_ts:
            kernel = _kernel_at(start_ts)
    last = (last_state or {}).get("data") or {}
    endnote = None
    if res and res.get("outcome") == "ended-stuck" and last_state:
        # 判「卡住」之前最后一次读数时游戏时钟还在不在走：结局紧跟在最后一份快照后面（< 1 秒）、那一拍时钟又往前走了
        # = 那不是结算画面，是大脑一轮 step 太慢被当成时钟停了（09-18 15:01~15:30 那 12 局，run_brain.EndWatch 修）
        gap = (res_rec.get("wallElapsedSec") or 0) - (last_state.get("wallElapsedSec") or 0)
        adv = (last_state.get("gameElapsedSec") or 0) - ((prev_state or {}).get("gameElapsedSec") or 0)
        endnote = {"gap": round(gap, 1), "advanced": adv, "suspect": gap < 1.0 and adv > 0}
    setup = exp.get("setup") or {}
    return {
        "game": start.get("game") if start.get("game") is not None else (int(m.group(3)) if m and m.group(3) else None),
        "episode": (m.group(4) if m and m.group(4) else None) or str(exp.get("episodeId") or "")[:8] or None,
        "runId": str((first_state_rec or {}).get("runId") or exp.get("runId") or "")[:8] or None,
        "instance": exp.get("instance"), "pid": exp.get("pid"), "exe": exp.get("exe"), "supervisorPid": exp.get("supervisorPid"),
        # 开局谱（09-18，war3-brain/brain/openings）是人手选的，不算「调参」；没选 = none（按建造表）
        "brain": brain, "planVer": plan, "kernel": kernel,
        "tuned": bool({k for k in (exp.get("config") or {}) if k != "opening"}), "config": exp.get("config") or {},
        "opening": _opening(exp) if brain else None,
        # 09-18 这一局属于哪批调参实验、第几组（run_brain.tune_meta；没调参或写晚了对不上就没有）
        "tuning": exp.get("tuning") or None,
        "race": (first_state or {}).get("race"), "foeRace": max(foe_races, key=foe_races.get) if foe_races else None,
        "map": os.path.basename(str(setup["map"])) if setup.get("map") else None, "difficulty": setup.get("difficulty"),
        "speed": setup.get("speed"), "setupRace": setup.get("race"), "setupEnemyRace": setup.get("enemyRace"),
        "result": res, "finalized": fin is not None, "endnote": endnote,
        "peakFoeArmy": max(peak_foe, int((lesson or {}).get("peakEnemyArmy") or 0)) or None,
        "maxHalls": max_halls or None, "tier": tier if n_states else None, "maxOwnStr": round(max_own, 1) if max_own else None,
        "endOwnStr": last.get("ownStr"), "endFoeStr": last.get("foeStr"),
        "wallMinutes": round(((last_t or 0) - (first_t or 0)) / 60.0, 1) if last_t is not None else None,
        "actions": n_actions, "states": n_states,
        "date": date, "time": tm, "startTs": start_ts,
    }


def _load_index() -> None:
    """第一次用时把存盘的摘要读回内存（版本对不上就当没有）。"""
    if _index["loaded"]:
        return
    data = C.load_json(INDEX_PATH, None)
    with _lock:
        if _index["loaded"]:
            return
        _index["loaded"] = True
        if isinstance(data, dict) and data.get("version") == INDEX_VERSION:
            for p, e in (data.get("entries") or {}).items():
                if isinstance(e, list) and len(e) == 3 and isinstance(e[2], dict) and p not in _cache:
                    _cache[p] = (e[0], e[1], e[2], 0.0)
            _index["sig"] = hash(frozenset((p, e[0], e[1]) for p, e in _cache.items()))    # 和盘上一样就不用重写


def save_index(force: bool = False) -> None:
    """摘要存盘：最多 30 秒看一次，要存的那一批（不在写的局）真变了才写 —— 对局进行中不会每 30 秒重写几 MB。
    已经删掉的对局文件顺手去掉。写临时文件再换名，写一半不会坏。"""
    now = time.time()
    with _lock:
        if not force and now - _index["saved"] < 30:
            return
        _index["saved"] = now
        entries = {p: [e[0], e[1], e[2]] for p, e in _cache.items() if now - e[0] >= LIVE_S}   # 还在写的不存
    sig = hash(frozenset((p, e[0], e[1]) for p, e in entries.items()))
    if sig == _index.get("sig"):
        return
    _index["sig"] = sig
    entries = {p: e for p, e in entries.items() if os.path.exists(p)}
    try:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": INDEX_VERSION, "entries": entries}, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, INDEX_PATH)
    except OSError:
        pass


def _summary(path: str, name: str) -> dict:
    _load_index()
    st = os.stat(path)
    now = time.time()
    live = now - st.st_mtime < LIVE_S
    with _lock:
        hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        s = hit[2]
    elif hit and live and not hit[2].get("finalized") and now - hit[3] < LIVE_REPARSE_S:
        s = hit[2]                        # 还在写：刚读过就先用上次的（缓存里的 mtime/size 不动，过了间隔照常重读）
    else:
        s = _parse(path, name)
        with _lock:
            _cache[path] = (st.st_mtime, st.st_size, s, now)
    return dict(s, name=name, size=st.st_size, mtime=st.st_mtime, live=not s["finalized"] and live)


def _match_files(d: str) -> list[str]:
    try:
        return [f for f in os.listdir(d) if f.startswith("match-") and f.endswith(".jsonl")]
    except OSError:
        return []


def list_matches(n: int, limit: int = 200) -> list[dict]:
    d = _dir(n)
    out = []
    names = sorted(_match_files(d), reverse=True)
    for f in (names[:limit] if limit else names):
        try:
            out.append(dict(_summary(os.path.join(d, f), f), inst=n))
        except OSError:
            continue
    save_index()
    return out


def list_all(limit: int = 600) -> list[dict]:
    """所有实例的对局，按开局时间（文件名里的 YYYYMMDD-HHMMSS）新到旧；每条带 inst（从目录来，老局的 experiment 里没有实例号）。
    limit = 0：全部。"""
    files = []
    try:
        names = os.listdir(C.BIN)
    except OSError:
        return []
    for name in names:
        m = re.fullmatch(r"inst(\d+)", name)
        if m:
            d = os.path.join(C.BIN, name, "matches")
            files += [(f[6:21], int(m.group(1)), d, f) for f in _match_files(d)]
    files.sort(reverse=True)
    out = []
    for _, n, d, f in (files[:limit] if limit else files):
        try:
            out.append(dict(_summary(os.path.join(d, f), f), inst=n))
        except OSError:
            continue
    save_index()
    return out


def _avg(xs: list) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def _stats(ms: list[dict]) -> dict:
    """一组对局的局数、打完几局、结局可信几局、胜几局、平均时长 / 峰值兵力（我 / 敌），和网页原来的口径一样。"""
    fin = [m for m in ms if m.get("result")]
    rel = [m for m in fin if m["result"].get("outcome") not in UNRELIABLE]
    return {
        "n": len(ms), "done": len(fin), "reliable": len(rel),
        "wins": sum(1 for m in rel if m["result"].get("outcome") == "victory"),
        "avgMinutes": _avg([m["result"].get("minutes") or 0 for m in fin]),
        "avgPeakArmy": _avg([m["result"].get("peakArmy") or 0 for m in fin]),
        "avgPeakFoe": _avg([m.get("peakFoeArmy") or 0 for m in fin]),
        "suspects": sum(1 for m in ms if (m.get("endnote") or {}).get("suspect")),
    }


def _hay(m: dict) -> str:
    k = m.get("kernel") or {}
    parts = [m.get("name"), f"inst{m.get('inst')}", m.get("inst"), m.get("pid"), m.get("exe"), k.get("kernelRev"),
             f"#{k['kernel']}" if k.get("kernel") else "", (m.get("result") or {}).get("outcome"),
             m.get("race"), m.get("foeRace"), m.get("map"), (m.get("opening") or {}).get("name")]
    return " ".join(str(x) for x in parts if x is not None).lower()


def overview(scope: str = "inst", inst: int | None = None, limit: int = 300, kind: str = "brain",
             pick_inst: int | None = None, pick_kernel: int | None = None, pick_opening: str | None = None,
             q: str = "", outcomes: tuple = (), offset: int = 0, size: int = 50) -> dict:
    """对局记录页要的全部东西：总体统计、结局分布、按实例 / 内核 / 开局谱分组（这三组不受「点一行」筛选影响，
    和网页原来的行为一样）、筛选后的一页对局。scope = all 时看所有实例最近 limit 局（0 = 全部）。
    outcomes = 网页按当前语言把搜索词对上的结局码（结局名是翻译过的，这边只认得码）。"""
    allrows = list_all(limit) if scope == "all" else list_matches(int(inst or 0), limit)
    base = [m for m in allrows if kind == "all" or (bool(m.get("brain")) == (kind == "brain"))]
    ql = q.strip().lower()
    oc = set(outcomes)

    def keep(m: dict) -> bool:
        if pick_inst is not None and m.get("inst") != pick_inst:
            return False
        if pick_kernel is not None and (m.get("kernel") or {}).get("kernel") != pick_kernel:
            return False
        if pick_opening is not None and ((m.get("opening") or {}).get("id") or "none") != pick_opening:
            return False
        if ql and ql not in _hay(m) and (m.get("result") or {}).get("outcome") not in oc:
            return False
        return True

    rows = [m for m in base if keep(m)]
    by_inst: list[dict] = []
    if scope == "all":
        g: dict[int, list[dict]] = {}
        for m in base:
            g.setdefault(m["inst"], []).append(m)
        for n in sorted(g):
            ms, last = g[n], g[n][0]
            ks = sorted({m["kernel"]["kernel"] for m in ms if m.get("brain") and m.get("kernel")}, reverse=True)
            by_inst.append(dict(_stats(ms), inst=n, kernels=ks[:4], moreKernels=len(ks) > 3,
                                last={"date": last.get("date"), "time": last.get("time"), "live": last.get("live"),
                                      "outcome": (last.get("result") or {}).get("outcome")}))
    gk: dict[int, list[dict]] = {}
    go: dict[str, list[dict]] = {}
    for m in base:
        if m.get("brain") and m.get("kernel"):
            gk.setdefault(m["kernel"]["kernel"], []).append(m)
        if m.get("brain") and m.get("opening"):
            go.setdefault(m["opening"]["id"], []).append(m)
    by_kernel = [dict(_stats(gk[k]), kernel=k) for k in sorted(gk, reverse=True)[:12]]
    by_opening = [dict(_stats(ms), id=oid, name=ms[0]["opening"].get("name") or oid)
                  for oid, ms in sorted(go.items(), key=lambda kv: -len(kv[1]))]
    offset = max(0, min(offset, max(0, len(rows) - 1)))
    return {
        "scope": scope, "inst": inst if scope != "all" else None,      # 网页据此认出这份是哪个范围的（切换时别拿旧的冒充）
        "scanned": len(allrows), "total": len(rows), "offset": offset, "size": size,
        "stats": _stats(rows),
        "outcomes": Counter(m["result"]["outcome"] for m in rows if m.get("result")).most_common(),
        "byInst": by_inst, "byKernel": by_kernel, "byOpening": by_opening,
        "rows": rows[offset:offset + size],
    }


def _thin(xs: list, n: int) -> list:
    """均匀抽 n 个（保留第一个和最后一个）：曲线画 600 个点和画 6000 个点看起来一样，网页轻得多。"""
    if len(xs) <= n:
        return xs
    step = (len(xs) - 1) / (n - 1)
    return [xs[round(i * step)] for i in range(n)]


def detail(n: int, name: str, max_actions: int = 4000, max_points: int = 600) -> dict:
    if not re.fullmatch(r"match-[\w\-]+\.jsonl", name):
        raise ValueError("文件名不对")
    p = os.path.join(_dir(n), name)
    states, actions, intel = [], [], []
    meta = {}
    with open(p, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            k = r.get("kind")
            d = r.get("data")
            if k == "state" and isinstance(d, dict):
                ex = d.get("extra") or {}
                states.append({"tick": r.get("tick"), "t": r.get("gameElapsedSec"), "gold": d.get("gold"), "lumber": d.get("lumber"),
                               "foodUsed": d.get("foodUsed"), "foodCap": d.get("foodCap"), "units": d.get("units"),
                               "ownStr": ex.get("ownStr", d.get("ownStr")), "foeStr": ex.get("foeStr", d.get("foeStr")),
                               "attack": ex.get("attack", d.get("attack")), "defense": ex.get("defense", d.get("defense"))})
            elif k == "action":
                if len(actions) < max_actions:
                    text = d.get("text") if isinstance(d, dict) else str(d)
                    actions.append({"tick": r.get("tick"), "t": r.get("gameElapsedSec"), "text": text})
            elif k == "intel" and isinstance(d, dict):
                intel.append({"tick": r.get("tick"), "army": d.get("army"), "workers": d.get("workers"), "composition": d.get("composition")})
            elif k in ("start", "experiment", "result", "lesson", "finalized"):
                meta[k] = d
    # 老 AI 的 experiment 带整份文件哈希清单（几十 KB），详情页用不上
    if isinstance(meta.get("experiment"), dict):
        meta["experiment"] = {k: v for k, v in meta["experiment"].items() if k != "policyManifest"}
    return {"name": name, "meta": meta, "summary": dict(_summary(p, name), inst=n),
            "states": _thin(states, max_points), "statesTotal": len(states),
            "actions": actions, "actionsCapped": len(actions) >= max_actions, "intel": _thin(intel, max_points)}

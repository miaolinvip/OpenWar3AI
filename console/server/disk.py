# -*- coding: utf-8 -*-
"""数据与磁盘（09-26）：远见和底座一直在往盘上写（战斗录制、对局记录、决策明细、日志……），这里统计占了多少、
在哪、最近 24 小时涨了多少、所在磁盘还剩多少。只读，不删任何东西。

旧仓库实测：构建目录 101.8 GB，其中战斗录制（inst*/fights）一个实例就 21.7 GB —— 录制没有上限，不看根本不知道。

分类（key 给网页翻译成名字和说明）：
  fights 战斗录制 · matches 对局记录 · decisions 战斗决策明细 · logs 日志 · director 导播录制 · streams 其它运行数据流（*.jsonl）
  state 实例状态文件 · farm 批量对局输出 · perf 性能采样 · backup 备份 · program 程序文件 · cache 远见缓存
  tuning 调参数据 · schemes AI 方案 · gamedata 游戏数据（从本机游戏提取） · other 其它
扫一遍几万个文件不到一秒（Windows 的 scandir 自带大小和时间，不用逐个 stat）；结果缓存 60 秒。
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time

from . import common as C

CACHE_S = 60.0
LOG_RX = re.compile(r"\.(log|out|err|hist)(\.\d+)?$|\.\d+$", re.I)
_lock = threading.Lock()
_last: dict = {"at": 0.0, "data": None}


def _walk(root: str):
    """(相对 root 的路径, 大小, 修改时间)；读不了的目录跳过。不跟符号链接。"""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        yield os.path.relpath(e.path, root), st.st_size, st.st_mtime
                except OSError:
                    continue


def _inst_cat(rel: str) -> str:
    """实例目录（bin/inst<N>/）里的一个文件归哪一类。rel 相对实例目录。"""
    head = rel.split(os.sep, 1)[0].lower()
    if os.sep in rel:
        return {"fights": "fights", "matches": "matches", "director": "director"}.get(head, "state")
    name = rel.lower()
    if name == "decisions.jsonl":
        return "decisions"
    if LOG_RX.search(name):
        return "logs"
    if name.endswith(".jsonl"):
        return "streams"
    return "state"


def _bin_cat(rel: str) -> str:
    """bin/ 下面、不在实例目录里的文件。"""
    parts = rel.split(os.sep)
    head = parts[0].lower()
    if len(parts) == 1:
        return "logs" if LOG_RX.search(head) else "program"
    return {"farm": "farm", "perf": "perf", "backup": "backup", "fights": "fights", "matches": "matches",
            "reports": "other", "gateway": "other", "hdtex": "other"}.get(head, "other")


def _roots() -> list[tuple[str, str, str | None]]:
    """(显示用的相对路径, 绝对路径, 固定分类；None = 按文件判断)。"""
    r = C.ROOT
    return [
        ("console/cache", os.path.join(C.FARSIGHT, "cache"), "cache"),
        ("director/director", os.path.join(r, "director", "director"), "director"),
        ("brains/xwar3/strategy/tuning", os.path.join(r, "brains", "xwar3", "strategy", "tuning"), "tuning"),
        ("schemes", os.path.join(r, "schemes"), "schemes"),
        ("data/game", os.path.join(r, "data", "game"), "gamedata"),
    ]


def scan() -> dict:
    t0 = time.perf_counter()
    now = time.time()
    day = now - 86400
    cats: dict[str, dict] = {}
    insts: dict[int, dict] = {}
    dirs: dict[str, dict] = {}
    largest: list[tuple[int, str, float, str]] = []
    total = {"bytes": 0, "files": 0, "growth24h": 0}

    def add(key: str, dir_key: str, rel_path: str, size: int, mtime: float, inst: int | None = None):
        c = cats.setdefault(key, {"key": key, "bytes": 0, "files": 0, "growth24h": 0})
        c["bytes"] += size
        c["files"] += 1
        dd = dirs.setdefault(dir_key, {"path": dir_key, "bytes": 0, "files": 0, "newest": 0.0})
        dd["bytes"] += size
        dd["files"] += 1
        dd["newest"] = max(dd["newest"], mtime)
        total["bytes"] += size
        total["files"] += 1
        if mtime >= day:
            c["growth24h"] += size
            total["growth24h"] += size
        if inst is not None:
            it = insts.setdefault(inst, {"inst": inst, "bytes": 0, "files": 0, "growth24h": 0, "newest": 0.0, "cats": {}})
            it["bytes"] += size
            it["files"] += 1
            it["cats"][key] = it["cats"].get(key, 0) + size
            it["newest"] = max(it["newest"], mtime)
            if mtime >= day:
                it["growth24h"] += size
        if len(largest) < 20 or size > largest[-1][0]:
            largest.append((size, rel_path, mtime, key))
            largest.sort(key=lambda x: -x[0])
            del largest[20:]

    bin_rel = os.path.relpath(C.BIN, C.ROOT).replace(os.sep, "/")
    for rel, size, mtime in _walk(C.BIN):
        m = re.match(r"inst(\d+)(?:[\\/](.*))?$", rel, re.I)
        path = f"{bin_rel}/{rel}".replace(os.sep, "/")
        if m and m.group(2):
            n = int(m.group(1))
            add(_inst_cat(m.group(2)), f"{bin_rel}/inst{n}", path, size, mtime, inst=n)
        else:
            head = rel.split(os.sep, 1)[0]
            add(_bin_cat(rel), f"{bin_rel}/{head}" if os.sep in rel else bin_rel, path, size, mtime)
    for shown, absdir, key in _roots():
        if not os.path.isdir(absdir):
            continue
        for rel, size, mtime in _walk(absdir):
            add(key or "other", shown, f"{shown}/{rel}".replace(os.sep, "/"), size, mtime)
    speech = os.path.join(C.ROOT, "speech")
    try:
        for e in os.scandir(speech):
            if e.is_file() and LOG_RX.search(e.name):
                st = e.stat()
                add("logs", "speech", f"speech/{e.name}", st.st_size, st.st_mtime)
    except OSError:
        pass

    try:
        du = shutil.disk_usage(C.ROOT)
        drive = {"path": os.path.splitdrive(C.ROOT)[0] or C.ROOT, "total": du.total, "used": du.used, "free": du.free}
    except OSError:
        drive = None
    return {
        "scannedAt": now, "tookMs": round((time.perf_counter() - t0) * 1000), "root": C.ROOT,
        "drive": drive, "total": total,
        "categories": sorted(cats.values(), key=lambda c: -c["bytes"]),
        "instances": sorted(insts.values(), key=lambda i: -i["bytes"]),
        "dirs": sorted(dirs.values(), key=lambda d: -d["bytes"]),
        "largest": [{"path": p, "bytes": s, "mtime": t, "key": k} for s, p, t, k in largest],
    }


def open_path(rel: str) -> str:
    """在资源管理器里打开（文件就打开它所在的文件夹并选中它）。只认仓库里的路径（统计结果里给的都是相对仓库的）。"""
    p = os.path.normpath(os.path.join(C.ROOT, rel))
    root = os.path.normpath(C.ROOT)
    if os.path.commonpath([p, root]) != root or not os.path.exists(p):
        raise ValueError("不在仓库里，或者已经不在了")
    import subprocess
    if os.path.isdir(p):
        os.startfile(p)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer", "/select,", p])
    return p


def usage(refresh: bool = False) -> dict:
    """缓存 60 秒；两个请求同时来只扫一遍。"""
    with _lock:
        if not refresh and _last["data"] is not None and time.time() - _last["at"] < CACHE_S:
            return dict(_last["data"], cached=True)
        data = scan()
        _last.update(at=time.time(), data=data)
        return dict(data, cached=False)

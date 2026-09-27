# -*- coding: utf-8 -*-
"""远见「AI 方案」页的后端：方案列表 + 战绩 + 哪些实例在用。导入 / 导出 / 复制 / 信任 / 删除都转给 openwar3.schemes。

战绩两路来源：
  * 玩家方案（kind=bot）：方案运行器每局一行 bin/inst<N>/scheme_matches.jsonl；
  * 参考大脑（builtin/xwar3）：它自己的对局文件 bin/inst<N>/matches/match-*.jsonl 的结局（victory = 胜，认输/失败 = 负）。
"""
from __future__ import annotations

import os
import re

from . import common as C
from . import instances as I
from . import matches as MT
from openwar3 import schemes as S

LOSS = {"conceded-weak", "conceded", "defeat", "lost", "loss", "eliminated"}


def _inst_dirs() -> list[str]:
    try:
        return [os.path.join(C.BIN, d) for d in os.listdir(C.BIN) if re.fullmatch(r"inst\d+", d)]
    except OSError:
        return []


def _brain_stats() -> dict:
    s = {"games": 0, "wins": 0, "losses": 0, "unknown": 0, "midGame": 0, "avgMinutes": 0, "last": 0}
    mins = 0.0
    for m in MT.list_all(limit=1000):
        if not m.get("finalized"):
            continue
        res = m.get("result") or {}
        s["games"] += 1
        oc = res.get("outcome")
        s["wins" if oc == "victory" else "losses" if oc in LOSS else "unknown"] += 1
        mins += float(res.get("minutes") or 0)
        s["last"] = max(s["last"], float(m.get("mtime") or 0))
    s["avgMinutes"] = round(mins / s["games"], 1) if s["games"] else 0
    return s


def overview() -> dict:
    lst = S.list_schemes()
    stats = S.stats(S.results(_inst_dirs()))
    try:
        stats["builtin/xwar3"] = _brain_stats()
    except Exception:  # noqa: BLE001 - 对局文件读坏了不该让方案页打不开
        pass
    insts = []
    for s in I.status():
        if s["game"]["alive"] or s["farm"] or s["brain"] or s.get("bot") or s.get("nextGame", {}).get("scheme"):
            insts.append({"inst": s["inst"], "scheme": s.get("scheme"), "running": s.get("schemeRunning"),
                          "alive": s["game"]["alive"], "farm": bool(s["farm"]), "held": bool(s["held"])})
    return {"schemes": lst, "stats": stats, "instances": insts, "dirs": {"mine": S.w3paths.SCHEMES_MINE,
                                                                       "installed": S.w3paths.SCHEMES_INSTALLED}}


def recent_results(ref: str, limit: int = 30) -> list[dict]:
    rows = [r for r in S.results(_inst_dirs()) if r.get("scheme") == ref]
    rows.sort(key=lambda r: -float(r.get("end") or 0))
    return rows[:limit]

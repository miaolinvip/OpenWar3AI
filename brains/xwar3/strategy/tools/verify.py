# -*- coding: utf-8 -*-
"""按版本比中间指标，不只比胜负。

**为什么需要这个**（09-20 架构评审第 1 节）：
胜负每局只有 1 个样本。按 259 局实测基线 25.6% 算，α=.05 / power=.8 下要分辨 +10 个百分点需要每组 332 局，
+20 个点也要 89 局。而过去三天 15 个代码组合的**中位样本量是 2 局** —— 那个区间能看到的最小效应约是 +60 个点，
所以「这版好一点 / 这版又退了」绝大多数是在读噪声。

中间指标每局有几十到上百个样本，3~5 局就能看出改动有没有生效：
  英雄阵亡/局、attack_move 频次、home 状态占比、可工作基地、被堵让路次数……
它们只能回答「这个改动有没有按设计工作」，**不能**代替胜率回答「这个改动是不是更强」——
别拿它们当优化目标（代理量可能和目标反向）。

用法：
    python tools/verify.py                      # 最近 60 局，按 kernelRev 分组
    python tools/verify.py --n 200 --inst 1,2,3 # 指定局数和实例
    python tools/verify.py --since 20260921     # 只看这天之后开的局
    python tools/verify.py --baseline 5318d2f   # 拿某一版当基线，其它版本显示差值
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MATCHES = os.path.join(ROOT, "war3-button-hook-lab", "build_intel4", "inst{n}", "matches")
COUNT_RE = re.compile(r"(\S+?)x(\d+)(?=\s|$)")
WIN = "victory"
REAL = ("victory", "conceded-weak")


def parse_counts(raw):
    """lesson 的计数：新局有机器可读的 countsJson；老局只有「名称xN 名称xN」的人类串。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {k: int(v) for k, v in COUNT_RE.findall(raw)}
    return {}


def load(path):
    exp = res = les = None
    states = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                k, d = o.get("kind"), o.get("data") or {}
                if k == "experiment":
                    exp = d
                elif k == "result":
                    res = d
                elif k == "lesson":
                    les = d
                elif k == "state":
                    states.append(d)
    except OSError:
        return None
    if not res or not exp or res.get("outcome") not in REAL:
        return None
    les = les or {}
    cj = les.get("countsJson") or {}
    counts = parse_counts(cj.get("counts") if cj else les.get("counts"))
    attack = parse_counts(cj.get("attack") if cj else les.get("attack"))
    minutes = res.get("minutes") or 0.0
    home = sum(1 for s in states if s.get("attack") == "home")
    return dict(
        path=path, rev=(exp.get("kernelRev") or "?")[:7], kernel=exp.get("kernel"),
        combat=exp.get("combatVersion") or "-", dirty=bool(exp.get("kernelDirty")),
        speed=(exp.get("setup") or {}).get("speed"), win=res.get("outcome") == WIN,
        minutes=minutes, peak_army=res.get("peakArmy") or 0,
        wall=None, counts=counts, attack=attack,
        home_pct=(100.0 * home / len(states)) if states else None,
        halls=max((s.get("workHalls") or 0) for s in states) if states else 0,
        # 英雄阵亡：计数键形如「阵亡·打玩家·Hamg」「阵亡·打野·Hmkg」——
        # 兵种码首字母大写的才是英雄（gamedata.is_hero 同一个判据）
        deaths_hero=sum(v for k, v in counts.items() if re.match(r"阵亡·[^·]+·[A-Z]", k)),
        atk_move=counts.get("意图·attack_move", 0),
    )


def agg(rows):
    n = len(rows)
    if not n:
        return None
    wins = sum(1 for r in rows if r["win"])
    p = wins / n
    se = math.sqrt(p * (1 - p) / n) if n else 0.0

    def per_min(key):
        vals = [r[key] / max(1.0, r["minutes"]) for r in rows]
        return sum(vals) / len(vals)

    def mean(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    return dict(n=n, wins=wins, wr=100 * p, lo=100 * max(0.0, p - 1.96 * se), hi=100 * min(1.0, p + 1.96 * se),
                minutes=mean("minutes"), army=mean("peak_army"), halls=mean("halls"),
                home=mean("home_pct"), atk=per_min("atk_move"), herod=mean("deaths_hero"),
                stuck=sum(r["counts"].get("英雄被堵·让路", 0) for r in rows) / n,
                withdraw=sum(r["attack"].get("英雄·withdraw", 0) for r in rows) / n,
                gohome=sum(r["attack"].get("英雄·home", 0) for r in rows) / n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="每个实例最多看最近几局")
    ap.add_argument("--inst", default="1,2,3,4,5,6")
    ap.add_argument("--since", default="", help="只看文件名里日期 >= 这个的局，例如 20260921")
    ap.add_argument("--baseline", default="", help="拿这个 kernelRev 当基线，其它行显示差值")
    ap.add_argument("--by", default="rev", choices=("rev", "combat", "both", "speed", "rev+speed"))
    a = ap.parse_args()

    rows = []
    for n in [x.strip() for x in a.inst.split(",") if x.strip()]:
        files = sorted(glob.glob(os.path.join(MATCHES.format(n=n), "match-*.jsonl")))
        if a.since:
            files = [f for f in files if re.search(r"match-(\d{8})", os.path.basename(f)) and
                     re.search(r"match-(\d{8})", os.path.basename(f)).group(1) >= a.since]
        for f in files[-a.n:]:
            r = load(f)
            if r:
                r["inst"] = n
                rows.append(r)
    if not rows:
        print("没有可用对局。")
        return

    # ⚠ 一次只改一个变量才能归因。09-21 同时换了内核版本和游戏倍速，按 rev 分组会把两者混在一起 ——
    #   要判断版本效果就在同一倍速里比（rev+speed），要判断倍速效果就在同一版本里比。
    key = {"rev": lambda r: r["rev"], "combat": lambda r: r["combat"],
           "both": lambda r: f"{r['rev']}/{r['combat']}",
           "speed": lambda r: f"{r['speed']}x",
           "rev+speed": lambda r: f"{r['rev']}@{r['speed']}"}[a.by]
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)

    order = sorted(groups, key=lambda k: min(g["kernel"] or 0 for g in groups[k]))
    base = agg(groups[a.baseline]) if a.baseline in groups else None

    hdr = ("版本", "局数", "胜", "胜率", "95%CI", "局长", "峰兵", "工作矿", "home%", "攻移/分", "英雄亡", "让路", "脱离", "回家")
    print("%-16s %4s %3s %6s %-12s %6s %5s %6s %6s %7s %6s %5s %5s %5s" % hdr)
    for k in order:
        g = agg(groups[k])
        dirty = sum(1 for r in groups[k] if r["dirty"])
        mark = f"  ⚠{dirty} 局脏工作区" if dirty else ""
        print("%-16s %4d %3d %5.0f%% [%3.0f%%,%3.0f%%] %6.1f %5.1f %6.2f %6.1f %7.2f %6.2f %5.1f %5.1f %5.1f%s" % (
            k, g["n"], g["wins"], g["wr"], g["lo"], g["hi"], g["minutes"], g["army"], g["halls"],
            g["home"], g["atk"], g["herod"], g["stuck"], g["withdraw"], g["gohome"], mark))
        if base and k != a.baseline:
            print("%-16s %4s %3s %+5.0f%% %-12s %+6.1f %+5.1f %+6.2f %+6.1f %+7.2f %+6.2f" % (
                "  ↑ 对基线", "", "", g["wr"] - base["wr"], "", g["minutes"] - base["minutes"],
                g["army"] - base["army"], g["halls"] - base["halls"], g["home"] - base["home"],
                g["atk"] - base["atk"], g["herod"] - base["herod"]))

    spd = Counter(r["speed"] for r in rows)
    print("\n样本 %d 局（实例 %s）｜倍速设置 %s" % (
        len(rows), ",".join(sorted({r["inst"] for r in rows})),
        "、".join(f"{k}×{v}" for k, v in spd.most_common())))
    print("⚠ 胜率列的置信区间通常很宽 —— 要分辨 +10 个百分点需要每组约 330 局。")
    print("  中间指标（home%、攻移/分、英雄亡）每局几十到上百个样本，用来判断改动**有没有按设计工作**；")
    print("  判断**是不是更强**只能看胜率，而且要等样本够。两件事不要混。")


if __name__ == "__main__":
    main()

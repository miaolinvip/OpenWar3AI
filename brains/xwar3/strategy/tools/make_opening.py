# -*- coding: utf-8 -*-
"""高手录像 -> 开局谱草稿（brain/openings/<id>.json），给新的参考录像用。

09-18 后续还会加入更多参考录像，开局谱要方便扩展。
  python tools/make_opening.py <录像.nwg|.w3g> --who Yumiko --id human_xxx --name "某某速开矿" [--upto 10] [--print] [--force]
出来的是**草稿**：人看一眼（步兵要几个、哪几步可以并行、max_wait_s）再放进 brain/openings/，Farsight 的「开局谱」选项里就多一个。
human_yumiko_fe.json 就是这个套路手工定的（两局对照、每步标了录像时刻）。

规则（和 make_pro_spots.py 同一个口径）：
  * 农民、人口建筑（农场/地洞/通灵塔/月井）不列：开局谱的 keep 让建造表一直补（两局 Yumiko 都是一直补、按需盖）；
  * 离主基地 1300 以外：大厅 = 开分矿；分矿 1500 内的塔/建筑 = where "expansion"；别的（出口/地图中间的视野农场）不收；
  * 连点（同一个东西 3 秒内再点）只算一次；训练按累计个数，同一种兵连着的几条并成一步；
  * 塔按这一族的塔合计（count），研究同一项隔 30 秒以上再点算升一级。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(os.path.dirname(HERE), "brain", "openings")
BASE_R = 1300.0
EXP_R = 1500.0
RACE = {"人族": "human", "兽族": "orc", "亡灵": "undead", "暗夜": "nightelf"}
HALLS = {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}
FOOD = {"hhou", "otrb", "uzig", "emow"}
TOWERS = {"human": ["hwtw", "hatw", "hgtw", "hctw"], "orc": ["owtw"], "undead": ["uzg1", "uzg2"], "nightelf": ["etrp"]}
KEEP = {"human": {"hpea": 86, "hhou": None}, "orc": {"opeo": 86, "otrb": None},
        "undead": {"uaco": 86, "uzig": None}, "nightelf": {"ewsp": 86, "emow": None}}


def mmss(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def draft(r: dict, who: str | None, upto_min: float) -> tuple[dict, list]:
    ps = r.get("players") or []
    p = next((x for x in ps if who and who.lower() in str(x.get("name", "")).lower()), ps[0] if ps else None)
    if p is None:
        raise ValueError("录像里没有玩家")
    race = RACE.get(p.get("race"), "")
    home = p.get("home") or [0, 0]
    towers = TOWERS.get(race, [])
    upto = upto_min * 60000
    exps = [(e["x"], e["y"]) for e in (p.get("expansions") or [])]
    ev, skipped = [], []
    for b in p.get("builds") or []:
        if b["t"] > upto:
            continue
        d = math.hypot(b["x"] - home[0], b["y"] - home[1])
        near_exp = any(math.hypot(b["x"] - x, b["y"] - y) <= EXP_R for x, y in exps)
        if b["code"] in FOOD and d <= BASE_R:
            continue                                            # 人口建筑交给建造表按需盖
        if d <= BASE_R:
            ev.append((b["t"], "building", b["code"], "base", (b["x"], b["y"])))
        elif b["code"] in HALLS:
            ev.append((b["t"], "expand", b["code"], "", (b["x"], b["y"])))
        elif near_exp and b["code"] not in FOOD:
            ev.append((b["t"], "building", b["code"], "expansion", (b["x"], b["y"])))
        else:
            skipped.append(f"{mmss(b['t'])} {b['code']} 离家 {int(d)}（基地外，不是分矿）")
    for x in p.get("trains") or []:
        if x["t"] <= upto and x["code"] not in WORKERS:
            ev.append((x["t"], "hero" if x.get("hero") else "unit", x["code"], "", None))
    for x in p.get("research") or []:
        if x["t"] <= upto:
            ev.append((x["t"], "upgrade", x["code"], "", None))
    for x in p.get("tierUps") or []:
        if x["t"] <= upto:
            ev.append((x["t"], "upgrade_hall", x["code"], "", None))
    for x in p.get("buildingUpgrades") or []:
        if x["t"] <= upto:
            ev.append((x["t"], "morph", x["code"], "", None))
    ev.sort(key=lambda e: e[0])

    steps, last_click, total, research_at = [], {}, {}, {}
    heroes_seen = set()
    for t, kind, code, where, pos in ev:
        key = (kind, code, where, pos)
        if kind != "unit" and key in last_click and t - last_click[key] < 3000:
            continue                                            # 连点（训练连点是在排队出几个，照算）
        last_click[key] = t
        if kind == "hero":
            if code in heroes_seen:
                continue                                        # 同一个英雄连点两下
            heroes_seen.add(code)
            steps.append({"kind": "hero", "code": code, "note": f"录像 {mmss(t)}"})
            continue
        if kind == "upgrade":
            if code in research_at and t - research_at[code] < 30000:
                continue                                        # 同一项研究连点
            research_at[code] = t
        fam = tuple(towers) if code in towers and kind == "building" else (code,)
        total[fam] = total.get(fam, 0) + 1
        qty = total[fam] + (1 if kind == "expand" else 0)       # 开分矿：第一座分矿 = 大厅总数 2
        st = {"kind": kind, "code": code, "qty": qty, "note": f"录像 {mmss(t)}"}
        if kind == "building":
            st["where"] = where
            if len(fam) > 1:
                st["count"] = list(fam)
        prev = steps[-1] if steps else None
        if prev and kind == "unit" and prev["kind"] == "unit" and prev["code"] == code:
            head = prev["note"].split("~")[0]
            prev["qty"], prev["note"] = qty, head if head.endswith(" " + mmss(t)) else f"{head}~{mmss(t)}"
            continue
        steps.append(st)
    # 高手常先在分矿放塔、再放大厅（Yumiko 4:03 塔、4:13 大厅）；大脑的 where "expansion" 要等分矿大厅出现才有落点，
    # 照录像顺序会白等到超时被跳过 —— 开分矿那一步挪到第一条分矿建筑前面
    first_exp = next((i for i, s in enumerate(steps) if s["kind"] == "expand"), None)
    first_at = next((i for i, s in enumerate(steps) if s.get("where") == "expansion"), None)
    if first_exp is not None and first_at is not None and first_at < first_exp:
        steps.insert(first_at, steps.pop(first_exp))
    for s in steps:
        if s.get("qty") == 1:
            s.pop("qty")
    op = {"id": "", "name": "", "race": race, "desc": "（草稿：tools/make_opening.py 生成，人看一眼再用）",
          "source": [f"{os.path.basename(r.get('file') or '')} {p.get('name')}（{p.get('race')}，出生点 {int(home[0])},{int(home[1])}）"],
          "prio": 84, "until_s": int(upto_min * 60 + 120), "keep": KEEP.get(race, {}), "steps": steps}
    return op, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("replay")
    ap.add_argument("--who", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--upto", type=float, default=10.0, help="前几分钟")
    ap.add_argument("--print", action="store_true", help="只打印不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖同名文件")
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, "war3-analytics"))
    import replay_global as G  # noqa: E402
    r = G.analyse(a.replay)
    op, skipped = draft(r, a.who, a.upto)
    op["id"] = a.id or f"{op['race']}_draft"
    op["name"] = a.name or op["id"]
    text = json.dumps(op, ensure_ascii=False, indent=1)
    print(text)
    if skipped:
        print("没收（基地外、不在分矿旁）：", *skipped, sep="\n  ", file=sys.stderr)
    if a.print:
        return 0
    out = os.path.join(OUT_DIR, op["id"] + ".json")
    if os.path.exists(out) and not a.force:
        print(f"{out} 已经有了（--force 覆盖）", file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("写入", out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

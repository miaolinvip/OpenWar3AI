# -*- coding: utf-8 -*-
"""高手录像 -> 主基地建筑落点表（brain/data/pro_spots.json），新大脑造建筑时排在候选最前（runtime._building_spots）。

09-18 重新学习 1481530917_Yumiko_TH000_Echo Isles.nwg 录像里的开局建筑顺序和位置：
             早先的建筑落点是照这份录像摆的，后来又退化成乱摆。
  查下来：照录像摆的是老 AI（war3ai.ps1 v276，bo/human-east-yumiko.json，而且只有东侧；西侧那次试镜像被地形挡住后回落了老逻辑）。
  09-18 换成新大脑后，落点 = inst<N>/known_spots.json（09-16 老 AI 自己打出来的点）+ 大厅外 420~1300 的环，
  而候选检查要求和已有建筑之间再空 64 —— 高手贴着大厅盖的农场（大厅半边 256 + 农场半边 64 = 320，正好贴边）根本进不了候选。
  实测 5 号 08/09 局：兵营 (0,-832)、祭坛 (+480,-416)、农场 960~1580 一路往东南散；Yumiko 同一个出生点：兵营 (+576,+192)、
  祭坛 (-224,-544)、农场 (+320,+64)/(+320,+192)/(-64,-320) 贴着大厅。

只收主基地 BASE_R 以内的建筑（基地布局）。两份录像里 Yumiko 都在开局派一个农民去基地出口（离家 ~2000）和地图中间（~5000）
盖农场 —— 那是看路的视野农场，不是布局，不收（写进 skipped）；分矿的塔/大厅也不收（分矿落点另有 known_halls）。
重复点击同一点只留一次；同一个出生点有多局就按录像顺序接在后面。

  python tools/make_pro_spots.py            # 扫 war3-analytics/replays/*.nwg|*.w3g，写 brain/data/pro_spots.json
  python tools/make_pro_spots.py --print    # 只打印不写
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
REPLAYS = os.path.join(ROOT, "war3-analytics", "replays")
OUT = os.path.join(os.path.dirname(HERE), "brain", "data", "pro_spots.json")
BASE_R = 1300.0
# Echo Isles 左右镜像：x' = -512 - x（war3-replay skill 的口径表；两个出生点 (-5184,2944) / (4672,2944)）
MIRROR = {"EchoIsles": {"axis": "x", "c": -512}}


def _map_key(name: str) -> str | None:
    base = os.path.basename(str(name or "")).replace(" ", "")
    return next((k for k in MIRROR if k.lower() in base.lower()), None)


def build(paths: list[str]) -> dict:
    sys.path.insert(0, os.path.join(ROOT, "war3-analytics"))
    import replay_global as G  # noqa: E402

    spawns: dict[str, dict] = {}
    skipped, sources = [], []
    for path in paths:
        try:
            r = G.analyse(path)
        except Exception as exc:  # noqa: BLE001
            print("跳过（解析失败）", os.path.basename(path), exc, file=sys.stderr)
            continue
        mk = _map_key(r.get("mapPath") or r.get("map"))
        if not mk:
            continue
        sources.append(os.path.basename(path))
        for p in r.get("players") or []:
            home = p.get("home")
            if not home:
                continue
            hk = f"{int(home[0])},{int(home[1])}"
            sp = spawns.setdefault(hk, {"map": mk, "from": [], "spots": {}})
            sp["from"].append(f"{os.path.basename(path)} {p.get('name')}（{p.get('race')}）")
            for b in p.get("builds") or []:
                code, x, y = b["code"], float(b["x"]), float(b["y"])
                d = math.hypot(x - home[0], y - home[1])
                if d > BASE_R:
                    skipped.append({"file": os.path.basename(path), "who": p.get("name"), "t": round(b["t"] / 1000), "code": code,
                                    "x": x, "y": y, "dist": int(d), "why": "基地外（视野农场 / 分矿的塔和大厅）"})
                    continue
                lst = sp["spots"].setdefault(code, [])
                if [x, y] not in lst:
                    lst.append([x, y])
    return {"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": sources, "baseR": BASE_R,
            "mirror": MIRROR, "spawns": spawns, "skipped": skipped}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    paths = sorted(glob.glob(os.path.join(REPLAYS, "*.nwg")) + glob.glob(os.path.join(REPLAYS, "*.w3g")))
    data = build(paths)
    for hk, sp in data["spawns"].items():
        print(f"出生点 {hk}（{sp['map']}）来自 {'；'.join(sp['from'])}")
        for code, pts in sp["spots"].items():
            print(f"  {code}: {pts}")
    print(f"基地外没收 {len(data['skipped'])} 条")
    if "--print" in argv:
        return 0
    with open(OUT + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(OUT + ".tmp", OUT)
    print("写入", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

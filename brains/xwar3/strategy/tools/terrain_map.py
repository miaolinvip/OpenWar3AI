# -*- coding: utf-8 -*-
"""把一局的「能不能走」表存成 json：运行时算好的 W3P 地图块（IsTerrainPathable 行走，128 一格）+ 开局的树。只读。

★ 09-21 打野时部队被带进角落作战，没有微操空间，残血单位也无处可撤。
  —— 原来打野集合点一律「营地朝家的方向退 1000」，不看那边是水、是树林还是悬崖。于是有了这张表（brain/terrain.py）。

09-23 P3：原来这个工具在开放层里自己按 128 一格调 IsTerrainPathable（一次约 5.5 毫秒，全图要几分钟）、按网格锚点扫树；
现在运行时开局后分批算好放进地图块（Local\\War3Map_<pid>），大脑直接用（terrain.from_world）。这个工具只是把它存下来，
给离线分析和没有运行时的测试用。格式和原来的 terrain_EchoIsles.json 一样。

    python tools/terrain_map.py --inst 20                    # 写 data/game/terrain_<地图>.json（默认 terrain_live.json）
    python tools/terrain_map.py --pid 12345 --out x.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_r = HERE
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
sys.path.insert(0, os.path.dirname(HERE))
import w3paths  # noqa: E402

w3paths.setup()
import w3world  # noqa: E402
from brain.terrain import doc_from_map  # noqa: E402


def pid_of(inst: int) -> int:
    text = open(os.path.join(w3paths.FARM_DIR, f"inst{inst}.out"), "rb").read().decode("utf-8", "replace")
    return int(re.findall(r"WAR3_PID=(\d+)", text.replace("\0", ""))[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", type=int, default=20)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--out", default=w3paths.game_data("terrain_live.json"))
    ap.add_argument("--wait", type=float, default=30.0, help="地图块没算好时最多等几秒")
    a = ap.parse_args()
    pid = a.pid or pid_of(a.inst)
    w = w3world.World(pid)
    t0 = time.time()
    mp = w.map()
    while mp is None and time.time() - t0 < a.wait:
        time.sleep(0.5)
        mp = w.map()
    if mp is None:
        print(f"地图块 {a.wait:.0f} 秒内没算好（还没进局？运行时太旧？）")
        return 1
    doc = doc_from_map(mp, w.trees())
    doc.update({"made": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": pid, "bounds": list(mp.bounds), "starts": mp.starts,
                "note": "blocked_rows[j][i] = '1' 表示 (x0+i*step, y0+j*step) 地形不能走（运行时 IsTerrainPathable 行走）；树另列"})
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    walk = sum(r.count("0") for r in doc["blocked_rows"])
    print(f"写好 {a.out}：{doc['n']}x{doc['n']} 格（地图块 {mp.cols}x{mp.rows}）、能走 {walk}、树 {len(doc['trees'])} 棵、出生点 {len(mp.starts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

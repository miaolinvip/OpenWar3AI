# -*- coding: utf-8 -*-
"""一条命令：起一局游戏（注入 + 开局），再让你的 bot 接管。

    python tools/play.py --bot brains/examples/hello_bot.py
    python tools/play.py --bot my_bot.py --inst 9 --race 2 --enemy-race 1 --difficulty 3 --speed 200
    python tools/play.py --bot my_bot.py --inst 9 --attach      # 游戏已经在跑（远见网页起的），只接 bot

种族 1 人族 2 兽族 3 亡灵 4 暗夜；难度 2 简单 3 普通 4 疯狂。地图默认 openwar3.json 的 default_map。
⚠ 实例号别和正在跑的参考大脑撞（远见网页「实例与开局」页能看到哪些号在用）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sdk", "python"))
import w3paths  # noqa: E402

w3paths.setup()


def wait_channel(inst: int, timeout: float = 120.0) -> int | None:
    path = os.path.join(w3paths.FARM_DIR, f"inst{inst}.out")
    end = time.time() + timeout
    while time.time() < end:
        try:
            text = open(path, "rb").read().decode("utf-8", "replace").replace("\0", "")
            if "control channel: ok" in text:
                m = re.findall(r"WAR3_PID=(\d+)", text)
                if m:
                    return int(m[-1])
        except OSError:
            pass
        time.sleep(0.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True)
    ap.add_argument("--inst", type=int, default=9)
    ap.add_argument("--race", type=int, default=1)
    ap.add_argument("--enemy-race", type=int, default=0)
    ap.add_argument("--difficulty", type=int, default=2)
    ap.add_argument("--speed", type=int, default=100)
    ap.add_argument("--map", default="")
    ap.add_argument("--minimize", action="store_true")
    ap.add_argument("--attach", action="store_true", help="不起游戏，只接到已经在跑的实例")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--fair", action="store_true", help="公平模式：bot 只看得见视野里的东西")
    ap.add_argument("--player", type=int, help="以几号玩家的身份指挥（AI 对 AI）")
    ap.add_argument("--rpg", action="store_true",
                    help="RPG / 自定义地图：不按对战规则判胜负（英雄死了不算输），玩伴 brains/examples/buddy.py 用它")
    a = ap.parse_args()

    if not os.path.isfile(os.path.join(w3paths.BIN, "render_hook.dll")):
        print("bin\\render_hook.dll 不存在：先双击 start.bat（会从 prebuilt\\ 装上）")
        return 2
    if not a.attach:
        cmd = [sys.executable, os.path.join(w3paths.RUNTIME, "farm.py"), "--first", str(a.inst), "--count", str(a.inst),
               "--no-director", "--race", str(a.race), "--enemy-race", str(a.enemy_race),
               "--difficulty", str(a.difficulty), "--speed", str(a.speed)]
        if a.map:
            cmd += ["--map", a.map]
        if a.minimize:
            cmd.append("--minimize")
        print("起游戏：", " ".join(cmd[2:]), flush=True)
        r = subprocess.run(cmd, cwd=w3paths.RUNTIME)
        if r.returncode != 0:
            print(f"farm.py 退出码 {r.returncode}")
            return r.returncode
    pid = wait_channel(a.inst)
    if not pid:
        print(f"等不到实例 {a.inst} 的控制通道（看 bin\\farm\\inst{a.inst}.out）")
        return 3
    print(f"游戏 pid={pid}，拉起 bot {a.bot}", flush=True)
    from openwar3.__main__ import load_bot
    import openwar3
    reason = openwar3.run(load_bot(a.bot), pid=pid, hz=a.hz, max_minutes=a.minutes, fair=a.fair, player=a.player,
                          judge=not a.rpg)
    print("结束：", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""方案运行器：让一个实例每一局都由指定的 AI 方案（openwar3.Bot 写的）来打。远见「开始测试 / 切换方案」起的就是它。

    python tools/run_scheme.py --inst 5 --scheme builtin/micro [--hours 24]
    python tools/run_scheme.py --inst 5 --scheme installed/fast-rush --one-game --pid 1234    # 只打这一局（监督器内部用）

监督器（常驻）：
  * 等 farm 开好一局（bin/farm/inst<N>.out 出现 control channel: ok + 新的 WAR3_PID）；
  * 每局起一个**子进程**来跑方案（方案代码崩了不连累监督器；「我的方案」改了代码下一局就生效）；
  * 子进程打完报结果，监督器记一行到 bin/inst<N>/scheme_matches.jsonl（哪个方案、版本、胜负、用时、原因）；
  * 实例挂了停止牌（hold.json，远见「停止实例」）就退出，不再接管新局。
方案没被信任（别人的方案第一次跑之前要在远见里点「信任」）就拒绝运行 —— 方案是代码，权限和你本人一样。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sdk", "python"))
import w3paths  # noqa: E402

w3paths.setup()
from openwar3 import schemes as S  # noqa: E402

HOLD_FILE = "hold.json"      # 和 console/server/instances.py、runtime/farm.py、run_brain.py 认同一个文件名


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def inst_dir(n: int) -> str:
    return os.path.join(w3paths.BIN, f"inst{n}")


def read_pid(n: int) -> int | None:
    try:
        text = open(os.path.join(w3paths.FARM_DIR, f"inst{n}.out"), "rb").read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return None
    if "control channel: ok" not in text:
        return None
    m = re.findall(r"WAR3_PID=(\d+)", text)
    return int(m[-1]) if m else None


def pid_alive(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        return bool(re.match(r"^war3(-\d+)?\.exe$", p.name() or "", re.I))
    except Exception:  # noqa: BLE001
        return False


def held(n: int) -> bool:
    return os.path.exists(os.path.join(inst_dir(n), HOLD_FILE))


def operator_end(n: int, since: float) -> str | None:
    """远见「结束这一局 / 停止」会写 end_request.json：这局记成 ended-by-operator（不算输赢）。"""
    p = os.path.join(inst_dir(n), "end_request.json")
    try:
        if os.path.getmtime(p) < since:
            return None
        return json.load(open(p, encoding="utf-8")).get("why", "operator")
    except (OSError, ValueError):
        return None


def close_lingering(n: int, pid: int, wait_s: float = 20.0) -> str | None:
    """分出胜负后 bot 发过结束令（run(end_game=True)），游戏 wait_s 秒还没退就强关：杀这个实例的启动器守护
    （游戏挂在它的 Job 里跟着退，零 UAC），再补一刀游戏本身。和 console/server/instances.py _kill_game 同一个办法。"""
    t_end = time.time() + wait_s
    while pid_alive(pid) and time.time() < t_end:
        time.sleep(0.5)
    if not pid_alive(pid):
        return None
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "war3_launch.exe" and f"War3-{n}.exe" in " ".join(p.info["cmdline"] or []):
                p.kill()
        except psutil.Error:
            pass
    try:
        if pid_alive(pid):                    # pid_alive 核对过进程名，pid 被复用了不会误杀
            psutil.Process(pid).kill()
    except psutil.Error:
        pass
    return f"结束令 {int(wait_s)} 秒没生效，强关了游戏"


def one_game(a) -> int:
    """子进程：载入方案、打这一局，最后一行打印结果 JSON（前缀 RESULT）。"""
    import openwar3
    s = S.get(a.scheme)
    cls = S.load_bot_class(s)
    bot = cls()
    reason = openwar3.run(bot, pid=a.pid, hz=float(s.get("hz") or 5), max_minutes=a.minutes, fair=bool(s.get("fair", True)),
                          end_game=True, judge=bool(s.get("judge", True)))
    stats = getattr(bot, "run_stats", {"reason": reason, "result": "unknown"})
    print("RESULT " + json.dumps(stats, ensure_ascii=False), flush=True)
    return 0


def supervise(a) -> int:
    s = S.get(a.scheme)
    if s.get("kind") not in S.KINDS:
        log(f"{a.scheme} 不是 bot / mod 方案（参考大脑用 run_brain.py）")
        return 2
    if not s.get("trusted"):
        log(f"{a.scheme} 还没被信任：别人的方案是代码，先在远见的「AI 方案」页确认信任再运行")
        return 3
    log(f"方案运行器：实例 {a.inst} <- {s['ref']}（{s['name']} v{s['version']}，作者 {s['author']}）")
    deadline = time.time() + a.hours * 3600
    played = set()
    d = inst_dir(a.inst)
    # 远见「切换（立刻生效）」时游戏已经在打：这一局是半路接手的，胜负不该算在这个方案头上（stats 单列 midGame）
    pre = read_pid(a.inst)
    mid_pid = pre if pre and pid_alive(pre) else None
    while time.time() < deadline:
        if held(a.inst):
            log("实例挂了停止牌（hold.json），退出，不再接管新局")
            return 0
        pid = read_pid(a.inst)
        if not pid or pid in played or not pid_alive(pid):
            time.sleep(1.0)
            continue
        played.add(pid)
        s = S.get(a.scheme)                 # 每局重读说明书：我的方案改了版本/入口，下一局就用新的
        start = time.time()
        mid = pid == mid_pid
        log(f"{'接管已经开着的这一局' if mid else '新的一局'} pid={pid}：{s['name']} v{s['version']} 接管")
        out_path = os.path.join(d, "scheme_bot.out")
        os.makedirs(d, exist_ok=True)
        cmd = [sys.executable, "-u", os.path.abspath(__file__), "--inst", str(a.inst), "--scheme", a.scheme,
               "--one-game", "--pid", str(pid), "--minutes", str(max(1.0, (deadline - time.time()) / 60))]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", WAR3_PID=str(pid), WAR3_INST_DIR=d)
        stats = {"reason": "子进程没报结果", "result": "unknown"}
        with open(out_path, "a", encoding="utf-8") as out:
            out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {s['ref']} v{s['version']} pid={pid} =====\n")
            out.flush()
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=s["dir"],
                                 text=True, encoding="utf-8", errors="replace")
            for ln in p.stdout:
                out.write(ln)
                out.flush()
                if ln.startswith("RESULT "):
                    try:
                        stats = json.loads(ln[7:])
                    except ValueError:
                        pass
            p.wait()
        why = operator_end(a.inst, start)
        if why:
            stats = dict(stats, result="unknown", reason=f"手动结束（{why}）")
        row = {"scheme": s["ref"], "name": s["name"], "version": s["version"], "author": s["author"], "inst": a.inst,
               "pid": pid, "start": round(start, 1), "end": round(time.time(), 1), **stats}
        # 「已经开着」不等于「半路」：换方案那一刻游戏可能刚开（09-24 实测：结束一局紧接着切方案，新局 2 秒后被当成半路接手）。
        # 按接手时的游戏时钟判：打过 60 游戏秒才算半路，不进胜率
        if mid and float(stats.get("joinClock") or 0) > 60:
            row["midGame"] = True
        S.record_result(d, row)
        log(f"这一局结束：{stats.get('result')}（{stats.get('reason')}），{stats.get('gameMinutes', '?')} 游戏分钟")
        if not why and stats.get("result") in ("win", "loss"):
            note = close_lingering(a.inst, pid)
            if note:
                log(note)
    log("到时间了，退出")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", type=int, required=True)
    ap.add_argument("--scheme", required=True, help="来源/id，比如 builtin/micro、mine/my-rush、installed/fast-rush")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--one-game", action="store_true")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--minutes", type=float, default=90.0)
    a = ap.parse_args()
    try:
        return one_game(a) if a.one_game else supervise(a)
    except S.SchemeError as exc:
        log(f"方案出错：{exc}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

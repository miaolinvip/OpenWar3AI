# -*- coding: utf-8 -*-
"""新大脑的监督器（照旧 PowerShell AI 监督器 run-ai 的职责移植）：farm 只起游戏（不带 -WithAi），这里接管一个实例。

    python brains/xwar3/strategy/run_brain.py --inst 5 [--hours 6] [--max-minutes 40]

每局：等 farm\\inst<N>.out 出现 control channel: ok + 活着的 WAR3_PID -> 设 12 个路径环境变量 -> 拉毫秒层模块
-> 等开局硬判据（引擎时钟 >= 1.5 秒且看得见自己的大厅）-> 跑大脑 -> 写 result/lesson/finalized
-> 动作 22 结束游戏（不结束 farm 永远不会重开）-> 收掉自己起的模块 -> 等下一局换 pid。
⚠ 这个号段不能给 farm 加 -WithAi：farm 的 AI 看护看不见 Python 大脑，会补拉 run-ai.ps1，两个大脑同时指挥。
"""
from __future__ import annotations

import argparse
from collections import deque
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
ROOT = w3paths.ROOT
BIN = w3paths.BIN
MODDIR = w3paths.REFLEX          # 毫秒层模块在 brains/xwar3/reflex
sys.path.insert(0, HERE)

from brain.offense import POLICY as ATTACK_POLICY
from brain.engagement import POLICY as COMBAT_POLICY

# mod_worker 不拉：09-17 实机它走的动作 28 采金令返回成功但农民不动，还会按陈旧的采集目标判「矿满 5 人」；采金由大脑 job_gold 派
MODULES = ("mod_combat", "mod_scavenge", "mod_spy", "mod_fight")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi")


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    arr = (wt.DWORD * 4096)()
    needed = wt.DWORD()
    if not _psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return False
    return pid in set(arr[: needed.value // ctypes.sizeof(wt.DWORD)])


def read_pid(inst: int) -> int | None:
    path = os.path.join(BIN, "farm", f"inst{inst}.out")
    try:
        text = open(path, "rb").read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return None
    if "control channel: ok" not in text:
        return None
    m = re.findall(r"WAR3_PID=(\d+)", text)
    return int(m[-1]) if m else None


def set_env(inst_dir: str, pid: int) -> dict:
    env = dict(os.environ)
    env.update({
        "WAR3_PID": str(pid), "WAR3_INST_DIR": inst_dir, "WAR3_BUILD_DIR": inst_dir, "WAR3_UNATTENDED": "1",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "WAR3_MOD_LOG": os.path.join(inst_dir, "ai_run.log"), "WAR3_MINE_CREWS": os.path.join(inst_dir, "mine_crews.json"),
        "WAR3_WORKER_BUSY": os.path.join(inst_dir, "worker_busy.json"), "WAR3_RESERVED_WORKERS": os.path.join(inst_dir, "reserved.json"),
        "WAR3_HURT_FILE": os.path.join(inst_dir, "hurt.json"), "WAR3_SOLO_FILE": os.path.join(inst_dir, "solo.json"),
        "WAR3_SPY_FILE": os.path.join(inst_dir, "spy.jsonl"), "WAR3_FIGHT_DIR": os.path.join(inst_dir, "fights"),
        "WAR3_MATCH_DIR": os.path.join(inst_dir, "matches"), "WAR3_HOOK_LOG": os.path.join(BIN, "render_hook.log"),
        "WAR3_UNITS_JSON": os.path.join(inst_dir, "units.json"),
    })
    os.environ.update(env)
    return env


def start_modules(env: dict, inst_dir: str, ops: dict | None = None) -> list:
    """ops（brain/ops.py，Farsight 设置页「对局运行」）：AI 关着一个都不拉；各模块按自己的开关。"""
    procs = []
    ops = ops or {}
    names = [m for m in MODULES if ops.get(m, True)] if ops.get("ai_enabled", True) else []
    for name in names:
        out = open(os.path.join(inst_dir, f"{name}.out"), "a", encoding="utf-8")
        p = subprocess.Popen([sys.executable, os.path.join(MODDIR, f"{name}.py")], cwd=MODDIR, env=env,
                             stdout=out, stderr=subprocess.STDOUT, creationflags=0x08000000)
        procs.append((name, p, out))
    skipped = [m for m in MODULES if m not in names]
    log(f"模块已拉起：{', '.join(f'{n}={p.pid}' for n, p, _ in procs) or '无'}" + (f"（开关关着没拉：{', '.join(skipped)}）" if skipped else ""))
    return procs


def stop_modules(procs: list) -> None:
    for name, p, out in procs:
        if p.poll() is None:
            p.terminate()
    for name, p, out in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        out.close()


def classify_end(op_ended: bool, last: dict | None) -> str:
    """游戏进程没了的时候这局算什么（09-18：真输被记成 crash、我手动结束的被记成 defeated，战绩不能用）。
    last = 最后一份**正常**快照（单位数没缩水）里的 {my_buildings, my_halls, enemy_buildings, own, foe}。"""
    if op_ended:
        return "ended-by-operator"
    if not last:
        return "crash"
    if last["enemy_buildings"] == 0:
        return "victory"
    if last["my_buildings"] == 0 or (last["my_halls"] == 0 and last["foe"] > 2 * max(1.0, last["own"])):
        return "defeated"
    return "crash"


class EndWatch:
    """局末两道「卡住」判据（游戏时钟不走 / 连续读到坏快照）的计时：**只按读数时刻算，不按现在几点算**。
    ★ 09-18 15:01~15:30 inst5 连续 11 局在半路被判「游戏时钟停了 5 秒」结束（ended-stuck，最长一局打到 29 分钟）：
      结局都写在最后一份快照之后 0.2 秒，而那一拍游戏时钟刚走了 5~18 秒 —— 游戏在正常跑。
      原来判据放在循环开头、拿「现在 - 时钟上次前进」比，大脑那一轮 step 花了 5 秒以上就被算成时钟停了
      （end_stuck_s 设成了最小值 5）。现在两个时长都量「读数之间」：step 再慢，下一次读回来时钟走了就不算停。"""

    def __init__(self, now: float):
        self.read_at = now          # 最近一次读快照/时钟的时刻
        self.clock_at = now         # 最近一次读到游戏时钟往前走的时刻
        self.bad_since = None       # 连续坏读数（空的/时钟倒退/缩水）从哪一次读开始；读到正常的就清掉

    def paused(self, now: float) -> None:
        """人开着任务/盟友面板（单人局会暂停）：时钟不走不算。"""
        self.clock_at = now

    def read(self, now: float, advanced: bool, bad: bool) -> None:
        self.read_at = now
        if advanced:
            self.clock_at = now
        if bad:
            self.bad_since = self.bad_since if self.bad_since is not None else now
        else:
            self.bad_since = None

    def clock_stopped(self) -> float:
        return self.read_at - self.clock_at

    def bad_for(self) -> float:
        return 0.0 if self.bad_since is None else self.read_at - self.bad_since


# 内核版本号 = 动过这两处的提交数（第几次改大脑代码）；farsight/server/matches.py 给没记版本的老对局按开局时间对齐，用的是同一组路径（改一处要改两处）
KERNEL_PATHS = ("brains/xwar3/strategy/brain", "brains/xwar3/strategy/run_brain.py")
COMBAT_PATH = "brains/xwar3/reflex/mod_combat.py"


def kernel_version() -> dict:
    """这一局跑的新大脑是哪一版：kernel = 内核版本号（动过大脑代码的提交数）、kernelRev = 那次提交的短哈希、
    kernelDirty = 工作区里大脑代码有没提交的改动（有就是 #N+）。开局时取：局间重载保证开局时进程里的代码就是磁盘上的。
    git 不可用就返回 {}，不影响这一局。"""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", ROOT, "--no-optional-locks", *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=10, creationflags=0x08000000).stdout.strip()
    try:
        head = git("log", "-1", "--format=%h %ct", "--", *KERNEL_PATHS).split()
        n = int(git("rev-list", "--count", "HEAD", "--", *KERNEL_PATHS) or 0)
        dirty = [ln for ln in git("status", "--porcelain", "--", *KERNEL_PATHS).splitlines() if ln.rstrip('"').endswith(".py")]
        return {"kernel": n, "kernelRev": head[0], "kernelAt": int(head[1]), "kernelDirty": bool(dirty)} if n and len(head) == 2 else {}
    except Exception:  # noqa: BLE001
        return {}


def power_calibration() -> dict:
    """这一局的战力阈值是从哪一次校准推出来的。

    出击/撤退/打野的十几个门槛现在全部由 `brain/winprob.py` 的三条线推出来
    （P_COMMIT / P_GIVEUP / LOSS_CHEAP），而三条线要经过 `brain/data/powercal.json` 的系数
    才变成具体的比值。**不记下来就没法回答「这一局跑的是哪一版阈值」** ——
    和 kernelRev 存在的理由完全一样（局号不能当版本号）。"""
    try:
        from brain import winprob as W
        m = W.meta()
        return {"files": m.get("files"), "samples": m.get("samples"),
                "pCommit": W.P_COMMIT, "pGiveup": W.P_GIVEUP, "lossCheap": W.LOSS_CHEAP,
                "rAttack": round(W.R_ATTACK_MAIN, 3), "rCreep": round(W.R_CREEP, 3)}
    except Exception:  # noqa: BLE001 - 记账不能拖垮一局
        return {}


def combat_version() -> dict:
    """独立毫秒战斗模块的源码版本。

    kernel 只数大脑路径；英雄保命、团战撤退等改在 ``war3-modules/mod_combat.py`` 时不会
    增加内核号。把两套版本同时写进对局，避免“内核号没变 = 战斗优化没加载”的误判。
    """
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", ROOT, "--no-optional-locks", *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=10, creationflags=0x08000000).stdout.strip()
    try:
        head = git("log", "-1", "--format=%h", "--", COMBAT_PATH)
        dirty = [ln for ln in git("status", "--porcelain", "--", COMBAT_PATH).splitlines()
                 if ln.rstrip('"').endswith(".py")]
        with open(os.path.join(ROOT, COMBAT_PATH.replace("/", os.sep)), encoding="utf-8") as fh:
            m = re.search(r'^HERO_SAFETY_VERSION\s*=\s*["\']([^"\']+)', fh.read(), re.MULTILINE)
        return {"combatVersion": m.group(1), "combatRev": head, "combatDirty": bool(dirty)} if m and head else {}
    except Exception:  # noqa: BLE001
        return {}


def tune_meta(inst_dir: str, overrides: dict) -> dict:
    """这一局属于哪批调参实验、第几组：tuner.py 派参数时写的 tune_meta.json {study, trial, tag, config}。
    只有它记的参数和这一局开局实际读到的覆盖项（brain_config.json）一致才记 —— 调参服务写晚了（开局之后才写）就不认，免得记错组。
    ★ 09-18 对局记录里 s12 与代码版本容易混淆，远见后台也拿不到版本：s12 是调参实验名，代码版本是 kernel_version()。"""
    try:
        import json
        with open(os.path.join(inst_dir, "tune_meta.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        cfg = m.get("config") or {}
        overrides = {k: v for k, v in overrides.items() if k not in ("opening",)}     # 人手选的开局谱不是调参维度（tuner.KEEP_KEYS）
        same = set(cfg) == set(overrides) and all(abs(float(cfg[k]) - float(overrides[k])) <= 1e-3 * max(1.0, abs(float(cfg[k])))
                                                   for k in cfg)
        return {"study": m.get("study"), "trial": m.get("trial"), "tag": m.get("tag")} if same and m.get("study") else {}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def match_setup(inst_dir: str, inst: int) -> dict:
    """开局设置，记进对局文件（之后改 next_game.json 不影响回看）：地图/倍速取 next_game.json（farm 起这一局前刚读过它，
    runtime/farm.py next_game），我方/对手种族和难度以启动器实际按住的为准（farm\\inst<N>.out 的 opening 行）。"""
    out = {}
    try:
        import json
        with open(os.path.join(inst_dir, "next_game.json"), encoding="utf-8-sig") as fh:
            g = json.load(fh)
        out = {k: g[k] for k in ("map", "speed", "race", "enemyRace", "difficulty") if g.get(k) not in (None, "")}
    except (OSError, ValueError):
        pass
    try:
        text = open(os.path.join(BIN, "farm", f"inst{inst}.out"), "rb").read().decode("utf-8", "replace").replace("\0", "")
        m = re.findall(r"opening: 种族 我方=(\d+) 敌方=(\d+).*?难度=(\d+)", text)
        if m:
            out.update(race=int(m[-1][0]), enemyRace=int(m[-1][1]), difficulty=int(m[-1][2]))
    except OSError:
        pass
    return out


class _ProcEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def exe_name(pid: int) -> str | None:
    """游戏进程的 exe 名（多开时 War3-<N>.exe）。走进程快照，不打开进程 ——
    提权的游戏进程连 PROCESS_QUERY_LIMITED_INFORMATION 都拒（09-18 实测 OpenProcess 错误 5）。"""
    snap_fn = _k32.CreateToolhelp32Snapshot
    snap_fn.restype = ctypes.c_void_p
    h = snap_fn(0x2, 0)                                  # TH32CS_SNAPPROCESS
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        e = _ProcEntry()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Process32FirstW(ctypes.c_void_p(h), ctypes.byref(e))
        while ok:
            if e.th32ProcessID == pid:
                return e.szExeFile
            ok = _k32.Process32NextW(ctypes.c_void_p(h), ctypes.byref(e))
        return None
    finally:
        _k32.CloseHandle(ctypes.c_void_p(h))


def end_request(inst_dir: str, since_wall: float) -> dict | None:
    """这一局开始之后有没有人从控制台按过结束 —— 有就返回那张条子（why / who / at）。
    ★ 09-23 上一局被记成手动结束，但无法确认是谁、从哪里结束的 ——「手动结束」四个字答不了「谁点的」，
      现在 farsight 的 mark_operator_end 会把来源（网页 / 脚本 + IP + UA）写进条子，这里读出来打进日志。"""
    path = os.path.join(inst_dir, "end_request.json")
    try:
        if os.path.getmtime(path) < since_wall:
            return None
    except OSError:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def end_request_text(req: dict | None) -> str:
    if not req:
        return ""
    return f"（{req.get('at') or '?'} {req.get('why') or '?'}｜来源 {req.get('who') or 'unknown'}）"


def operator_ended(inst_dir: str, since_wall: float) -> bool:
    """控制台「结束这一局」会先写 end_request.json（farsight/server/app.py）。"""
    return end_request(inst_dir, since_wall) is not None


def wait_game_start(ses, clock, deadline_s: float = 90.0, alive=None):
    """alive()：游戏还是不是这一个 —— 09-18 01:12:50 接管的 pid 34000 七秒后被 farm 重开掉，这里照等满 90 秒，
    新局（31688）开打 75 秒墙钟（约 225 游戏秒）没人指挥。每秒问一次，不是了立刻放弃、回主循环重新接管。"""
    t_end = time.monotonic() + deadline_s
    next_alive = time.monotonic() + 1.0
    while time.monotonic() < t_end:
        if alive is not None and time.monotonic() >= next_alive:
            next_alive = time.monotonic() + 1.0
            if not alive():
                log("等开局时游戏进程已经换了，放弃这一个")
                return None, None
        snap = ses.read()
        t = clock.read() if clock else None
        if snap is not None and snap.in_game and t is not None and t >= 1.5:
            if any(u.owner == snap.me and u.hp_max >= 1200 and u.hp > 0 for u in snap.units):
                return snap, t
        time.sleep(0.12)
    return None, None


def play_match(inst: int, pid: int, game_no: int, max_minutes: float) -> str:
    from brain.act import Actuator
    from brain.recorder import MatchRecorder
    from brain.runtime import Brain
    from fightodds import GameClock
    import modlog
    from w3claim import Claims
    from w3state import Session
    from brain import ops as OPS
    from brain import hud as HUD
    from brain.issuemark import IssueHotkey
    from brain.steptime import StepTimer, instrument
    from brain.world import build_world

    inst_dir = os.path.join(BIN, f"inst{inst}")
    env = set_env(inst_dir, pid)
    ops = OPS.load(inst_dir)
    procs = start_modules(env, inst_dir, ops)
    outcome = "crash"
    rec = None
    brain = None
    tm = None
    t = t0 = 0.0
    try:
        try:
            modlog.tee_stdout("brain")
        except Exception:  # noqa: BLE001
            pass
        ses = Session()
        for _ in range(100):
            if ses.read() is not None and ses.cmd is not None:
                break
            time.sleep(0.2)
        if ses.cmd is None:
            log("连不上游戏（快照/通道）")
            return "crash"
        clock = GameClock(ses.cmd)
        snap, t = wait_game_start(ses, clock, alive=lambda: pid_alive(pid) and read_pid(inst) in (None, pid))
        if snap is None:
            log("开局判据 90 秒内没成立")
            return "crash"
        claims = Claims("strategy")
        brain = Brain(ses.cmd, claims, inst_dir)
        rec = MatchRecorder(os.path.join(inst_dir, "matches"), game=game_no)
        rec.start(game_no, len(snap.units))
        # ★ 09-19：一轮有时 5~9 秒墙钟（3 倍速 = 15~27 游戏秒不决策），先埋数据：每段墙钟 + 段里的通道请求（抢锁/游戏处理/谁调的）。
        #   每分钟进心跳，结构化的进 inst<N>/steptime.jsonl（brain/steptime.py）
        tm = StepTimer(path=os.path.join(inst_dir, "steptime.jsonl"), label=rec.name)
        brain.timer = tm
        instrument(tm, lane=ses.cmd.lane, claims=claims, act=brain.act)
        # 这一局用的配置覆盖项（brain_config.json）：自动调参（tuner.py）按它认「这一局试的是哪组参数」
        # 09-18 对局记录要能看出是哪个进程跑的、哪一版内核、什么开局设置（Farsight 对局记录页读这几项）
        tuning = tune_meta(inst_dir, dict(brain.config.overrides))
        exe = exe_name(pid)
        kv = kernel_version()
        kv.update(combat_version())
        kv["powercal"] = power_calibration()
        rec.experiment(instance=f"inst{inst}", pid=pid, exe=exe, supervisorPid=os.getpid(),
                       config=dict(brain.config.overrides), setup=match_setup(inst_dir, inst), **kv,
                       **({"tuning": tuning} if tuning else {}))
        log(f"第 {game_no} 局开始：{rec.name}")
        # 09-18 左上角「任务」显示 War3AI-实例号-对局号、「盟友」显示游戏时间 mm:ss，点任务按钮看本局日志（brain/hud.py）
        hud = HUD.Hud(ses.cmd.lane) if ops["hud_labels"] else None
        hud_label = f"War3AI-{inst}-{game_no:03d}"
        hud_log = deque(maxlen=HUD.LOG_LINES)
        hud_paused_logged = False
        # 游戏暂停（任务/盟友/记录面板开着）的检查不跟 HUD 开关走：问题快捷键打开任务面板暂停，HUD 关着也要认
        panels = hud if hud is not None else HUD.Hud(ses.cmd.lane)
        # 09-19 问题快捷键（brain/issuemark.py）：游戏里按一下暂停并记下实例/局号/游戏时间点，Farsight「问题备忘」收进去
        # （DLL 动作 57 走控制通道 —— 运行时的动作号，不是引擎地址）
        issue = IssueHotkey(ses.cmd.ctl, BIN, pid, inst, inst_dir)
        issue.configure(ops["issue_hotkey"])
        t0 = t
        wall0 = time.time()
        last_good = None
        last_units = 0
        last_tick = -1
        no_home_since = None
        no_enemy_bld_since = None
        weak_since = None
        last_beat = time.monotonic()
        watch = EndWatch(time.monotonic())          # 游戏时钟多久没走、坏快照连续多久 —— 都按读数时刻量（见 EndWatch）
        while True:
            tm.begin()                              # 一轮从这里算到 sleep 0.2 之前（steptime：各段只记自己那部分）
            ops = OPS.load(inst_dir)
            # ★ 09-18：单人局打开任务面板游戏会暂停 —— 人在看日志，不是结算画面，时钟停了不算这局结束
            issue.configure(ops["issue_hotkey"])
            if panels.poll_pause():
                watch.paused(time.monotonic())
                if not hud_paused_logged:
                    log("任务/盟友面板开着，游戏暂停中（不算对局结束）")
                    hud_paused_logged = True
            else:
                hud_paused_logged = False
            # ⚠ 只数「读回来是坏的」那段时间，不数两次读之间隔了多久 —— 大脑某一拍卡了 20 秒不能被当成对局结束
            # ★ 09-18 对局画面：打赢了停在「胜利！继续游戏/退出游戏」对话框，下一局不开。
            #   那时候快照是好的、只有**游戏时钟冻住**，而「对面没建筑满 20 秒」「打不过满 45 秒」这些判据全按游戏时间算，
            #   永远等不到；坏快照计时也不会涨（读数不算坏）。所以时钟不走够久也算这一局结束。
            if ops["auto_end_stuck"] and watch.clock_stopped() > ops["end_stuck_s"] and pid_alive(pid):
                outcome = classify_end(operator_ended(inst_dir, wall0), last_good)
                if outcome == "crash":
                    outcome = "victory" if no_enemy_bld_since is not None else "ended-stuck"
                log(f"游戏时钟停了 {int(watch.clock_stopped())} 秒（多半是胜负结算画面），按最后一份正常快照判：{outcome}")
                break
            if ops["auto_end_stuck"] and watch.bad_for() > ops["end_stuck_s"] and pid_alive(pid):
                # ★ 09-18 游戏胜负结算后不能自动结束 —— 011447 局 01:22:12 起快照是空的（结算画面），
                #   shrunk 快照不判局末、时钟倒退的读数直接 continue，这个循环就一直空转到有人手动关游戏（01:24:06）。
                #   有效快照断了这么久 = 这局已经结束：按最后一份正常快照判，finally 里发结束令（不退就按开关强关）
                outcome = classify_end(operator_ended(inst_dir, wall0), last_good)
                if outcome == "crash":
                    outcome = "victory" if no_enemy_bld_since is not None else "ended-stuck"
                log(f"读不到对局 {int(watch.bad_for())} 秒（结算画面或游戏在收尾），按最后一份正常快照判：{outcome}")
                break
            if not pid_alive(pid):
                # ★ 09-17 inst5 match-20260917-232703：拍 110 对面建筑拆光，引擎判胜后游戏进程自己退了，
                #   比下面「对面没建筑满 20 秒」早 —— 第一场胜局被记成 crash。进程没了时对面已经一座建筑都没有 = 胜
                outcome = classify_end(operator_ended(inst_dir, wall0), last_good)
                if outcome == "crash" and no_enemy_bld_since is not None:
                    outcome = "victory"
                break
            tm.lap("读快照")
            snap = ses.read()
            tm.lap("读时钟")
            t_now = clock.read()
            if snap is None or t_now is None:
                watch.read(time.monotonic(), advanced=False, bad=True)
                tm.end(t - t0)
                time.sleep(0.1)
                continue
            if t_now < t - 1.0:
                # ★ 09-17 match-20260917-234019：判胜那一刻引擎时钟读回 0（对象已经在拆），tick 记成 -8、局长 0.0 分钟。倒退的读数丢掉
                watch.read(time.monotonic(), advanced=False, bad=True)
                tm.end(t - t0)
                time.sleep(0.1)
                continue
            n_units = len(snap.units)
            watch.read(time.monotonic(), advanced=t_now > t + 0.05, bad=n_units < 0.7 * last_units or n_units < 10)
            t = t_now
            # 问题快捷键：游戏停在按键那一刻，这一轮读到的时钟就是那个时间点
            for what in issue.poll():
                if what == "pause":
                    m = issue.mark(rec.name, game_no, t - t0, exe=exe,
                                   extra={"kernel": kv.get("kernel"), "kernelRev": kv.get("kernelRev")})
                    text = f"✎ 按快捷键记下一个问题（{m['id']}，游戏已暂停，再按一次继续）"
                    log(f"问题快捷键：记下 {m['id']} 第 {game_no} 局 {HUD.clock_text(t - t0)}")
                elif what == "resume":
                    text = "▶ 按快捷键继续游戏"
                else:
                    continue
                rec.action(int((t - t0) // 10), text, t - t0)
                hud_log.append(f"{HUD.clock_text(t - t0)} {text}")
            tm.lap("大脑")
            if ops["ai_enabled"]:
                brain.step(snap, t)
            else:                                   # AI 关着：只建世界模型判局末、记录，不跑任何任务（不下令）
                brain.world = build_world(snap.units, snap.me, t, snap.gold or 0, snap.lumber or 0, snap.food_used or 0,
                                          snap.food_cap or 0, previous=brain.world)
            w = brain.world
            n_units = len(snap.units)
            shrunk = n_units < 0.7 * last_units or n_units < 10     # 游戏在结束/切换：这份快照不用来判局末
            last_units = max(n_units, int(last_units * 0.98))
            tm.lap("游戏内按钮")
            if hud is not None and not shrunk:
                try:
                    hud.update(hud_label, t - t0, [
                        ("log", f"对局日志（{hud_label}）", HUD.join_lines(hud_log)),
                        ("status", "当前状态", HUD.status_text(w, brain.attack.state, brain.defense_mode, t - t0,
                                                            brain.attack.tick_reasons)),
                    ])
                except Exception as exc:  # noqa: BLE001
                    log(f"游戏内按钮文字出错，这局不再更新：{exc}")
                    hud = None
            tm.lap("记录")
            if not shrunk:
                last_good = dict(my_buildings=len(w.my_buildings), my_halls=len(w.my_halls), enemy_buildings=len(w.enemy_buildings),
                                 own=w.own_strength, foe=w.foe_strength)
            tick = int((t - t0) // 10)
            if tick != last_tick:
                last_tick = tick
                # ★ 09-18 英雄和小队决策迟疑，有时会停一阵没有动作。实测「有兵却没军事动作 >= 30 游戏秒」占 7%~19%，
                #   几乎全在 home 状态，可对局记录只有整局总数、答不上「那一拍为什么不动」—— 每拍记下出击层出过的理由（前 4 个）
                why = sorted(brain.attack.tick_reasons.items(), key=lambda kv: -kv[1])[:4]
                brain.attack.tick_reasons = {}
                dead = dict(brain.tick_deaths)            # 09-18 这一拍的阵亡：打野 / 打玩家 / 别处（runtime._unit_losses）
                brain.tick_deaths.clear()
                step_max, step_slow = tm.take_tick()      # 09-19 这一拍里最慢一轮的墙钟秒数和最重的段（steptime）
                rec.state(tick, w, {"workHalls": len(w.my_halls), "defense": brain.defense_mode, "attack": brain.attack.state,
                                    "aggression": round(w.aggression, 1), "ownStr": round(w.own_strength, 1), "foeStr": round(w.foe_strength, 1),
                                    "why": dict(why), "dead": dead,
                                    "attackPolicy": ATTACK_POLICY, "combatPolicy": COMBAT_POLICY,
                                    "attackAssessment": dict(brain.attack.assessment),
                                    "stepMax": round(step_max, 2), "stepSlow": step_slow})
                rec.intel(tick, w)
            for nt, text in brain.notes:
                rec.action(tick, text, nt - t0)
                if hud is not None and HUD.log_worthy(text):
                    hud_log.append(f"{HUD.clock_text(nt - t0)} {text}")
            brain.notes.clear()
            tm.lap("判局末")
            # 局末
            # ★ 09-18：原来看「没有大厅」—— 亡灵大厅被 is_building 认错，10 秒判输、动作 22 结束了游戏。一座建筑都没有才算输
            if not w.my_buildings and not shrunk:
                no_home_since = no_home_since if no_home_since is not None else t
                if t - no_home_since > 30:
                    outcome = "ended-by-operator" if operator_ended(inst_dir, wall0) else "defeated"
                    break
            else:
                no_home_since = None
            if not w.enemy_buildings and not shrunk:
                no_enemy_bld_since = no_enemy_bld_since if no_enemy_bld_since is not None else t
                if t - no_enemy_bld_since > 20:
                    outcome = "victory"
                    break
            else:
                no_enemy_bld_since = None
            # 自动认输（ops：auto_gg / gg_*）。09-18 被团灭后已无发展空间的局直接认输，结束游戏重新开局
            #   原来写死：600 秒后、强度 < 对面 25%、工人 < 5、持续 90 秒 —— 工人那条让「兵被团灭、农民还在」的局一直拖着
            army_n = len(w.my_army) + len(w.my_heroes)
            if (ops["auto_gg"] and not shrunk and t - t0 > ops["gg_min_minutes"] * 60 and w.foe_strength > 0
                    and w.own_strength < ops["gg_strength_ratio"] * w.foe_strength and army_n <= ops["gg_army_max"]):
                weak_since = weak_since if weak_since is not None else t
                if t - weak_since > ops["gg_hold_s"]:
                    log(f"认输：强度 我 {w.own_strength:.1f} / 敌 {w.foe_strength:.1f}，能打的剩 {army_n} 个，持续 {int(t - weak_since)} 游戏秒")
                    outcome = "conceded-weak"
                    break
            else:
                weak_since = None
            if t - t0 > ops["max_game_minutes"] * 60:
                outcome = "timeout"
                break
            if time.monotonic() - last_beat > 60:
                last_beat = time.monotonic()
                log(f"心跳 游戏 {int(t - t0)} 秒｜金 {w.gold} 木 {w.lumber} 人口 {w.food_used}/{w.food_cap}｜兵 {len(w.my_army)} 英雄 {len(w.my_heroes)} "
                    f"工人 {len(w.my_workers)}｜我 {w.own_strength:.1f} 敌 {w.foe_strength:.1f} 进攻性 {w.aggression:.1f}｜"
                    f"守家 {brain.defense_mode} 出击 {brain.attack.state}｜"
                    f"计数 {dict(brain.counts.most_common(10))}｜下令 {dict(sorted(brain.act.counts.items(), key=lambda kv: -kv[1])[:10])}｜"
                    f"任务出错 { {n: s['last_error'] for n, s in brain.jobs.stats().items() if s['errors']} }｜"
                    f"{tm.minute_line()}")
            tm.end(t - t0)
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        import traceback
        log("一局中途出错：" + traceback.format_exc())
        outcome = "crash"
    finally:
        if rec is not None and brain is not None:
            tk = max(0, int((t - t0) // 10))
            rec.result(tk, outcome, max(0.0, t - t0) / 60.0, peakArmy=brain.peak["army"],
                       peakGold=brain.peak["gold"], peakWorkers=brain.peak["workers"], peakHeroes=brain.peak["heroes"], enemy="computer")
            rec.lesson(tk, dict(brain.lesson(), outcome=outcome,
                                **({"steptime": json.dumps(tm.lesson(), ensure_ascii=False)} if tm is not None else {})))
            rec.finalize(tk)
            # 「手动结束」要能答出是谁结束的（09-23 出现过来源不明的「手动结束」）
            mark = end_request_text(end_request(inst_dir, wall0)) if outcome == "ended-by-operator" else ""
            log(f"第 {game_no} 局结束：{outcome}{mark}｜{rec.path}")
        try:
            if brain is not None and pid_alive(pid):
                brain.act.end_game()
        except Exception:  # noqa: BLE001
            pass
        stop_modules(procs)
        try:
            ops = OPS.load(inst_dir)
            if ops["kill_game_after_end"] and pid_alive(pid):
                t_kill = time.monotonic() + ops["kill_wait_s"]
                while pid_alive(pid) and time.monotonic() < t_kill:
                    time.sleep(0.5)
                if pid_alive(pid):
                    log(f"结束令发了 {ops['kill_wait_s']} 秒游戏还没退，强制关闭：{OPS.kill_game(inst, pid)}")
        except Exception as exc:  # noqa: BLE001
            log(f"强制关闭游戏出错：{exc}")
    return outcome


# 停止牌：Farsight 网页「停止实例 / 全部停止」写 inst<N>\hold.json —— 停了就别再接管新局、别再局间重启。
# console/server/instances.py（HOLD_FILE）和 runtime/farm.py（HOLD_FILE）认同一个文件名，改一处要改三处。
HOLD_FILE = "hold.json"
GAME_SEQUENCE_FILE = "game_sequence.json"
MATCH_GAME_RE = re.compile(r"^match-\d{8}-\d{6}-(\d+)(?:-|\.jsonl$)")


def initial_game_no(inst_dir: str, requested: int = 0) -> int:
    """恢复实例已经用过的最大局号；兼容旧对局，也不因监督器/电脑重启归零。"""
    used = max(0, int(requested or 0))
    try:
        with open(os.path.join(inst_dir, GAME_SEQUENCE_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        used = max(used, int(data.get("last", 0) if isinstance(data, dict) else data))
    except (OSError, ValueError, TypeError):
        pass
    try:
        for name in os.listdir(os.path.join(inst_dir, "matches")):
            m = MATCH_GAME_RE.match(name)
            if m:
                used = max(used, int(m.group(1)))
    except OSError:
        pass
    return used


def reserve_game_no(inst_dir: str, used: int) -> int:
    """在接管新进程前原子落盘。进程意外退出最多留下空号，不会复用旧局号。"""
    number = max(int(used), initial_game_no(inst_dir)) + 1
    os.makedirs(inst_dir, exist_ok=True)
    path = os.path.join(inst_dir, GAME_SEQUENCE_FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last": number, "updated": datetime.now().isoformat(timespec="seconds")}, fh,
                  ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return number


def held_since(inst: int, since: float) -> bool:
    """这个实例在 since 之后挂了停止牌。比 since 早的牌不算：那是挂牌之后有人又手动起了监督器（手动起 = 要跑）。"""
    try:
        return os.path.getmtime(os.path.join(BIN, f"inst{inst}", HOLD_FILE)) >= since
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", type=int, required=True)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--max-minutes", type=float, default=40.0)
    ap.add_argument("--game-no", type=int, default=0)       # 局间重载提示；0 也会从实例计数器和历史对局自动恢复
    ap.add_argument("--skip-pid", type=int, default=0)      # 局间重载时上一个进程刚打完的那局
    ap.add_argument("--hold-since", type=float, default=0)  # 局间重载时上一个进程从哪一刻起认停止牌（接班的进程启动得晚，不能按自己的启动时间算）
    args = ap.parse_args(argv)
    mx = None
    for _ in range(30):                                     # 局间重载：旧进程放开互斥体到退出有一小段
        mx = _k32.CreateMutexW(None, True, f"Global\\War3BrainSupervisor.inst{args.inst}")
        if ctypes.get_last_error() != 183:
            break
        _k32.CloseHandle(mx)
        mx = None
        time.sleep(1.0)
    if mx is None:
        log("这个实例已经有新大脑监督器在跑，退出")
        return 1
    end_at = time.monotonic() + args.hours * 3600
    started = time.time()
    since = args.hold_since or started
    done_pids = {args.skip_pid} if args.skip_pid else set()
    inst_dir = os.path.join(BIN, f"inst{args.inst}")
    game_no = initial_game_no(inst_dir, args.game_no)
    log(f"新大脑监督器 pid={os.getpid()} 管 inst{args.inst}，跑 {args.hours} 小时")
    while time.monotonic() < end_at:
        if held_since(args.inst, since):
            log("实例挂了停止牌（hold.json，网页停止），监督器退出，不再接管新局")
            break
        pid = read_pid(args.inst)
        if not pid or pid in done_pids or not pid_alive(pid):
            time.sleep(1.0)
            continue
        game_no = reserve_game_no(inst_dir, game_no)
        log(f"接管游戏 pid={pid}")
        outcome = play_match(args.inst, pid, game_no, args.max_minutes)
        done_pids.add(pid)
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(1.0)
        if held_since(args.inst, since):
            log("实例挂了停止牌（hold.json，网页停止），监督器退出，不再接管新局")
            break
        if newest_source() > started:
            if respawn(mx, args, end_at, game_no, pid, since):
                return 0
            break
    _k32.ReleaseMutex(mx)
    return 0


def newest_source() -> float:
    paths = [os.path.abspath(__file__)]
    for d, _, files in os.walk(os.path.join(HERE, "brain")):
        paths += [os.path.join(d, f) for f in files if f.endswith(".py")]
    return max(os.path.getmtime(p) for p in paths)


def respawn(mx, args, end_at: float, game_no: int, done_pid: int, since: float = 0.0) -> bool:
    """局间重载（改到一半的源码会被下一局读到）：源码比本进程新 -> 等源码静 20 秒（最多 2 分钟）
    -> 放开互斥体 -> 同样参数起一个新监督器（继承同一份 stdout）-> 本进程退出。新进程跳过刚打完的那局 pid。
    ★ 09-17：手动杀监督器换代码时，它刚接管新局、拉起一套模块就被杀，模块成了孤儿，同一局上跑了两套毫秒层。
    等源码静的这段时间里被网页停了（挂停止牌）就不起接班的，返回 False。"""
    t_end = time.monotonic() + 120
    while time.time() - newest_source() < 20 and time.monotonic() < t_end:
        if held_since(args.inst, since):
            break
        time.sleep(2.0)
    if held_since(args.inst, since):
        log("等局间重启时实例挂了停止牌（hold.json），不起接班的监督器")
        return False
    hours = max(0.05, (end_at - time.monotonic()) / 3600)
    log(f"源码比监督器新，局间重启：剩 {hours:.2f} 小时")
    _k32.ReleaseMutex(mx)
    _k32.CloseHandle(mx)
    cmd = [sys.executable, "-u", os.path.abspath(__file__), "--inst", str(args.inst), "--hours", f"{hours:.3f}",
           "--max-minutes", str(args.max_minutes), "--game-no", str(game_no), "--skip-pid", str(done_pid),
           "--hold-since", f"{since:.3f}"]
    subprocess.Popen(cmd, cwd=HERE, stdout=sys.__stdout__, stderr=sys.__stderr__, creationflags=0x00000008 | 0x00000200)   # sys.stdout 被 modlog.tee_stdout 换成了 DayTee
    return True


if __name__ == "__main__":
    raise SystemExit(main())

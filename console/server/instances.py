# -*- coding: utf-8 -*-
"""实例：有哪些、各自在跑什么（游戏 / farm 守护 / 新大脑监督器 / 老 AI / 导播 / 毫秒层模块），以及起停。

进程归属一律按**命令行 + 父进程**认（原则：模块进程按父进程筛，不能按脚本名，否则会互相误杀）。
起进程走 WMI Win32_Process.Create —— 父进程是 WmiPrvSE，不挂在控制台服务的进程树 / Job 里，
控制台重启不会把 farm 和大脑连带杀掉（farm 别从后台任务管理器拉起，那边结束任务会连整棵进程树一起杀）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from functools import wraps

import psutil

from . import common as C
import w3spawn  # noqa: E402  sdk/python（common 已经放上 sys.path）：WMI 后台起进程

# ★ 09-26 farm 从 farm.ps1 改成了 farm.py（部署不用再装 PowerShell 7）。认进程时两种都认：升级前起的 farm.ps1 可能还在跑
LAB_FARM = os.path.join(C.LAB, "farm.py")
_FARM_SCRIPT = re.compile(r"(^|[\\/\s\"'])farm\.(ps1|py)\b", re.I)
_FARM_HOSTS = ("python", "pwsh", "powershell")
RUN_BRAIN = os.path.join(C.BRAIN, "run_brain.py")
RUN_SCHEME = os.path.join(C.ROOT, "tools", "run_scheme.py")     # 方案运行器（玩家的 openwar3.Bot 方案）
from openwar3 import schemes as S  # noqa: E402  AI 方案库（内置 / 我的 / 已安装）
# 停止牌：实例被人从控制台停了 —— farm 不再重开这个号、新大脑监督器不再接管新局。开始测试时摘掉。
# 同一个文件名 runtime/farm.py（HOLD_FILE）和 war3-brain/run_brain.py（HOLD_FILE）也在认，改一处要改三处。
HOLD_FILE = "hold.json"
# 游戏进程名：原名 War3.exe 或多开的 War3-<N>.exe（按进程名找游戏一律认 ^War3(-\d+)?$，两种都要覆盖）。
# ★ 09-19 pid 会被系统复用：inst9.out 里昨晚 02:38 的游戏 pid 31788，今天属于别的程序 —— 只按 pid 判「游戏在跑」，
#   「全部停止」就把那个无关的进程杀了。认游戏、关游戏前都要核对进程名。
WAR3_EXE = re.compile(r"^war3(-\d+)?\.exe$", re.I)
RACES = {1: "人族", 2: "兽族", 3: "亡灵", 4: "暗夜"}
DIFFICULTY = {2: "简单", 3: "普通", 4: "疯狂"}
DEFAULT_MODE = "ai_vs_builtin"
HUMAN_MODE = "human_vs_openwar3ai"
_instance_locks: dict[int, threading.RLock] = {}
_instance_locks_guard = threading.Lock()


def _instance_locked(fn):
    @wraps(fn)
    def run(n, *args, **kwargs):
        with _instance_locks_guard:
            lock = _instance_locks.setdefault(n, threading.RLock())
        with lock:
            return fn(n, *args, **kwargs)
    return run


_PROCS_TTL = 1.5
_procs_cache: tuple[float, list] = (0.0, [])
_procs_lock = threading.Lock()


def _procs() -> list[dict]:
    """全机进程表，1.5 秒内的并发调用共用一次扫描。
    ⚠ 09-24 实测：这台机器 648 个进程，psutil 扫一遍 0.25 秒、新进程里第一次 20 秒；网页轮询、导播看护、JASS 控制台
    各扫各的时候互相拖，远见每个接口都要等 1.6~5 秒（查 native 这种不碰进程的也一样）。"""
    global _procs_cache
    with _procs_lock:                              # 同一时刻只有一个在扫，其余的等它扫完直接拿结果
        at, rows = _procs_cache
        if time.monotonic() - at < _PROCS_TTL:
            return rows
        rows = _scan_procs()
        _procs_cache = (time.monotonic(), rows)
        return rows


def invalidate_procs() -> None:
    """起了 / 杀了进程之后调：下一次查询重新扫。⚠ 09-24 加缓存后切方案（停旧运行器 -> 1 秒后起新的）读到了缓存里的旧运行器，
    判成「已经有方案运行器在跑」拒绝启动 —— 缓存只给「看」用，改了进程就作废。"""
    global _procs_cache
    with _procs_lock:
        _procs_cache = (0.0, [])


def _scan_procs() -> list[dict]:
    out = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            info = p.info
            cl = info.get("cmdline") or []
            out.append({"pid": info["pid"], "ppid": info["ppid"], "name": info["name"] or "", "cmd": " ".join(cl),
                        "args": cl, "started": info.get("create_time")})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _farm_range(cmd: str) -> tuple[int, int] | None:
    """farm 进程命令行 -> 它管的号段 (first, count)；不是 farm（或是 --stop / --status 这种一次性的）= None。
    认两种写法：farm.py --first 5 --count 5、升级前的 farm.ps1 -First 5 -Count 5。"""
    if not _FARM_SCRIPT.search(cmd) or re.search(r"(?i)\s--?(stop|status)\b", cmd):
        return None
    first = re.search(r"(?i)\s--?first\s+(\d+)", cmd)
    count = re.search(r"(?i)\s--?count\s+(\d+)", cmd)
    f = int(first.group(1)) if first else 1
    c = int(count.group(1)) if count else 6
    return f, c        # farm 管 first..count（含两头）：count 是**最后一个号**，不是个数


def _is_farm_host(p: dict) -> bool:
    """farm 本体（python 跑 farm.py / 升级前 pwsh 跑 farm.ps1）；包在外面的 cmd.exe /c 不算。"""
    return p["name"].lower().startswith(_FARM_HOSTS)


def read_farm_out(n: int) -> dict:
    path = os.path.join(C.FARM_DIR, f"inst{n}.out")
    try:
        text = open(path, "rb").read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return {}
    m = re.findall(r"WAR3_PID=(\d+)", text)
    opening = re.findall(r"opening: 种族 我方=(\d+) 敌方=(\d+).*?难度=(\d+)", text)
    return {"pid": int(m[-1]) if m else 0, "channel": "control channel: ok" in text, "openingDone": "opening done" in text,
            "race": int(opening[-1][0]) if opening else None, "enemyRace": int(opening[-1][1]) if opening else None,
            "mtime": os.path.getmtime(path)}


def _is_director(p: dict, n: int) -> bool:
    """这个实例的独立导播进程。只认 python 那一层：Farsight 用 cmd.exe /c 包一层起它（输出重定向），包装的 cmd 不算。"""
    return (p["name"].lower().startswith("python") and "director_inst.py" in p["cmd"]
            and re.search(rf"--inst\s+{n}\b", p["cmd"]) is not None)


def known_numbers() -> list[int]:
    nums = set()
    if os.path.isdir(C.FARM_DIR):
        for fn in os.listdir(C.FARM_DIR):
            m = re.fullmatch(r"inst(\d+)\.out", fn)
            if m:
                nums.add(int(m.group(1)))
    for fn in os.listdir(C.BIN):
        m = re.fullmatch(r"inst(\d+)", fn)
        if m and os.path.isdir(os.path.join(C.BIN, fn)):
            nums.add(int(m.group(1)))
    return sorted(nums)


def latest_match(n: int) -> dict | None:
    d = os.path.join(C.inst_dir(n), "matches")
    try:
        files = sorted((f for f in os.listdir(d) if f.startswith("match-") and f.endswith(".jsonl")), reverse=True)
    except OSError:
        return None
    if not files:
        return None
    p = os.path.join(d, files[0])
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as fh:
            fh.seek(max(0, size - 6000))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    outcome = re.findall(r'"outcome":"([^"]+)"', tail)
    return {"name": files[0], "finalized": '"kind":"finalized"' in tail, "outcome": outcome[-1] if outcome else None,
            "mtime": os.path.getmtime(p)}


def next_game(n: int) -> dict:
    return C.load_json(os.path.join(C.inst_dir(n), "next_game.json"), {}) or {}


def hold_path(n: int) -> str:
    return os.path.join(C.inst_dir(n), HOLD_FILE)


def set_hold(n: int, why: str) -> None:
    os.makedirs(C.inst_dir(n), exist_ok=True)
    with open(hold_path(n), "w", encoding="utf-8") as fh:
        json.dump({"why": why, "at": time.time(), "by": "farsight"}, fh, ensure_ascii=False)


def clear_hold(n: int) -> bool:
    try:
        os.remove(hold_path(n))
        return True
    except OSError:
        return False


def speed_of(n: int) -> int | None:
    try:
        return int(open(os.path.join(C.inst_dir(n), "speed.txt"), encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return None


_KIND_CACHE: dict[str, tuple[float, str | None]] = {}


def _scheme_kind(ref: str | None) -> str | None:
    """方案的 kind（brain / bot / mod）；读不到返回 None。status() 每 3 秒跑一遍，缓存 30 秒免得反复读 scheme.json。"""
    if not ref:
        return None
    hit = _KIND_CACHE.get(ref)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    try:
        kind = S.get(ref).get("kind")
    except Exception:  # noqa: BLE001 - 方案坏了 / 删了：不影响实例列表
        kind = None
    _KIND_CACHE[ref] = (time.time(), kind)
    return kind


def status(n: int | None = None, procs: list | None = None) -> list[dict]:
    procs = procs if procs is not None else _procs()
    by_parent: dict[int, list] = {}
    for p in procs:
        by_parent.setdefault(p["ppid"], []).append(p)
    out = []
    for k in ([n] if n is not None else known_numbers()):
        fo = read_farm_out(k)
        game_pid = fo.get("pid") or 0
        # 只认名字是 War3 的：inst<N>.out 里的 pid 可能是很久以前那局的，早被系统分给了别的程序
        game = next((p for p in procs if p["pid"] == game_pid and WAR3_EXE.match(p["name"] or "")), None) if game_pid else None
        farms = [p for p in procs if (r := _farm_range(p["cmd"])) and r[0] <= k <= r[1] and _is_farm_host(p)]
        brains = [p for p in procs if "run_brain.py" in p["cmd"] and re.search(rf"--inst\s+{k}\b", p["cmd"]) and p["name"].lower().startswith("python")]
        # 方案运行器（常驻那一层；每局的子进程带 --one-game，不算）
        bots = [p for p in procs if "run_scheme.py" in p["cmd"] and "--one-game" not in p["cmd"]
                and re.search(rf"--inst\s+{k}\b", p["cmd"]) and p["name"].lower().startswith("python")]
        old_ai = [p for p in procs if p["name"].lower().startswith("pwsh") and "run-ai.ps1" in p["cmd"] and f"inst{k}" in p["cmd"]]
        directors = [p for p in procs if _is_director(p, k)]
        modules = []
        for b in brains + old_ai:
            for c in by_parent.get(b["pid"], []):
                m = re.search(r"(mod_\w+)\.py", c["cmd"])
                if m:
                    modules.append({"name": m.group(1), "pid": c["pid"], "parent": b["pid"]})
        ng = next_game(k)
        launcher = next((p for p in procs if p["name"].lower() == "war3_launch.exe" and f"War3-{k}.exe" in p["cmd"]), None)
        m = re.search(r'--map\s+"?([^"]+?\.w3[xm])"?(\s|$)', launcher["cmd"]) if launcher else None
        cur_map = m.group(1) if m else None
        out.append({
            "inst": k,
            "game": {"pid": game_pid, "alive": game is not None, "exe": game["name"] if game else None,
                     "started": game["started"] if game else None, "channel": fo.get("channel", False),
                     "openingDone": fo.get("openingDone", False), "race": fo.get("race"), "enemyRace": fo.get("enemyRace")},
            # range：这个 farm 管哪几个号（命令行按范围起的 farm 一个进程管好几个实例，停其中一个不能杀它）
            "farm": [{"pid": p["pid"], "cmd": p["cmd"][-220:], "withAi": "-WithAi" in p["cmd"],
                      "keep": re.search(r"(?i)\s--?keep", p["cmd"]) is not None,
                      "range": list(_farm_range(p["cmd"]) or (k, k)), "started": p["started"]} for p in farms],
            "launcher": {"pid": launcher["pid"], "started": launcher["started"]} if launcher else None,
            "held": (C.load_json(hold_path(k), None) or {"why": "?"}) if os.path.exists(hold_path(k)) else None,
            "brain": [{"pid": p["pid"], "started": p["started"],
                       "matchMode": HUMAN_MODE if f"--match-mode {HUMAN_MODE}" in p["cmd"] else DEFAULT_MODE} for p in brains],
            "bot": [{"pid": p["pid"], "started": p["started"],
                     "scheme": (re.search(r"--scheme\s+(\S+)", p["cmd"]) or [None, None])[1]} for p in bots],
            # 这个实例选的方案（next_game.json 的 scheme；没选过 = 参考大脑）和现在实际在跑的
            "scheme": ng.get("scheme") or S.DEFAULT_REF,
            "schemeRunning": ("builtin/xwar3" if brains else
                              (re.search(r"--scheme\s+(\S+)", bots[0]["cmd"]) or [None, None])[1] if bots else None),
            # 在跑的（没在跑就是选的）方案是 bot 还是 mod：网页按它写「方案在指挥」/「模组在跑」
            "schemeKind": _scheme_kind(((re.search(r"--scheme\s+(\S+)", bots[0]["cmd"]) or [None, None])[1] if bots else None)
                                       or ng.get("scheme") or S.DEFAULT_REF),
            "oldAi": [{"pid": p["pid"], "started": p["started"]} for p in old_ai],
            "director": [{"pid": p["pid"], "started": p["started"]} for p in directors],
            "modules": modules,
            "speed": speed_of(k),
            "map": cur_map,
            "match": latest_match(k),
            "nextGame": ng,
            "takeover": _takeover(k, game_pid, [p["pid"] for p in brains]) if game else None,
            "brainLive": os.path.exists(os.path.join(C.inst_dir(k), "brain_live.json")),
        })
    return out


def spawn_detached(cmdline: str, cwd: str) -> dict:
    """WMI Win32_Process.Create：脱离控制台服务的进程树（sdk/python/w3spawn.py，走 Windows 自带的 PowerShell 5.1）。返回 {pid, rc}。
    ★ 09-18 起进程会弹出一堆 cmd 窗口 —— 起 6 个实例的 farm + 大脑弹了 12 个控制台：现在都隐藏窗口起。"""
    r = w3spawn.spawn_detached(cmdline, cwd)
    invalidate_procs()
    return r


def kill_tree(pid: int, include_self: bool = True) -> list[int]:
    invalidate_procs()             # 杀之后紧接着的查询（切方案：停旧的 -> 起新的）必须看到新的进程表
    killed = []
    try:
        p = psutil.Process(pid)
        children = p.children(recursive=True)
    except psutil.Error:           # 已经退了（常见：同一批里先停了它的父进程，它跟着退了）
        return killed
    for c in children:
        try:
            c.kill()
            killed.append(c.pid)
        except psutil.Error:
            pass
    if include_self:
        try:
            p.kill()
            killed.append(pid)
        except psutil.Error:
            pass
    invalidate_procs()             # 杀的过程中别的请求可能又把旧表缓存上了
    return killed


def game_problem() -> str | None:
    """开局前先看游戏目录和版本（09-26）：没设、不对、或者不是运行时支持的版本（只认 1.27a）就不开 ——
    版本不对硬开，注入的 DLL 按 1.27a 的地址干活，游戏直接崩。返回给网页的一句提示，没问题 = None。"""
    from . import gamedir as GD                      # 懒导入：gamedir -> services -> instances 会绕回来
    return GD.version_problem()


def _takeover(n: int, pid: int, supervisors: list[int] | None = None) -> dict | None:
    state = C.load_json(os.path.join(C.inst_dir(n), "takeover.json"), {}) or {}
    if supervisors is not None and state.get("supervisorPid") not in supervisors:
        return None
    return state if state.get("pid") == pid else None


def match_mode_of(n: int) -> str:
    return next_game(n).get("matchMode") or DEFAULT_MODE


def mode_problem(n: int, st: dict | None = None) -> str | None:
    mode = match_mode_of(n)
    if mode not in (DEFAULT_MODE, HUMAN_MODE):
        return "未知对局模式"
    if mode != HUMAN_MODE:
        return None
    if scheme_of(n) != S.DEFAULT_REF:
        return "玩家对战模式目前仅支持 builtin/xwar3"
    st = st or status(n)[0]
    if st["oldAi"] or st.get("bot") or any(b.get("matchMode", DEFAULT_MODE) != HUMAN_MODE for b in st["brain"]):
        return "当前有其他模式的 AI 在跑，请先停止实例再开始陪练"
    return None


@_instance_locked
def start_farm(n: int, hours: float = 6.0, speed: int | None = None, difficulty: int | None = None, race: int | None = None,
               enemy_race: int | None = None, map_path: str | None = None) -> dict:
    problem = game_problem() or mode_problem(n)
    if problem:
        return {"ok": False, "error": problem}
    clear_hold(n)
    farms = status(n)[0]["farm"]
    if farms:
        # 按范围起的 farm 还活着、只是这个号挂过停止牌：摘了牌它 0.5 秒内自己把这个号开起来
        return {"ok": True, "note": f"实例 {n} 已经有 farm（pid {farms[0]['pid']}）在管，已解除停止，它会自己开局"}
    parts = [f'"{C.PYTHON}"', "-u", f'"{LAB_FARM}"', "--first", str(n), "--count", str(n),
             "--keep", "--no-director", "--hours", str(hours)]
    if speed:
        parts += ["--speed", str(int(speed))]
    if difficulty:
        parts += ["--difficulty", str(int(difficulty))]
    if race:
        parts += ["--race", str(int(race))]
    if enemy_race:
        parts += ["--enemy-race", str(int(enemy_race))]
    if map_path:
        parts += ["--map", f'"{map_path}"']
    os.makedirs(C.FARM_DIR, exist_ok=True)
    log = os.path.join(C.FARM_DIR, f"farm_console{n}.out")
    cmd = f'cmd.exe /c "set PYTHONIOENCODING=utf-8&& {" ".join(parts)} > "{log}" 2>&1"'
    r = spawn_detached(cmd, C.LAB)
    return {"ok": r.get("rc") == 0, **r, "log": log}


def who_from(request) -> str:
    """★ 09-23 对局结果记成「手动结束」，但查不出是谁发的结束 —— 光记「手动结束」答不了「谁点的」。
    浏览器（网页控制台）和脚本（curl / 自动化工具）的 User-Agent 分得开，来源 IP 也记上。"""
    if request is None:
        return "unknown"
    ua = (request.headers.get("user-agent") or "")[:60]
    host = getattr(getattr(request, "client", None), "host", "") or "?"
    kind = "网页" if "Mozilla" in ua else ("脚本" if ua else "无 UA")
    return f"{kind}·{host}·{ua}" if ua else f"{kind}·{host}"


def mark_operator_end(n: int, why: str, who: str = "unknown") -> None:
    """人从控制台结束/停掉这一局：写 end_request.json，新大脑监督器据此把结局记成 ended-by-operator（不算输赢）。
    who = 谁发的（who_from）：新大脑的日志会把它打出来，事后能查「这局是谁结束的」。"""
    try:
        with open(os.path.join(C.inst_dir(n), "end_request.json"), "w", encoding="utf-8") as fh:
            json.dump({"why": why, "wall": time.time(), "who": who,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}, fh, ensure_ascii=False)
    except OSError:
        pass


def clear_operator_end(n: int) -> None:
    """撤掉 end_request.json（结束令没发出去时用）。"""
    try:
        os.remove(os.path.join(C.inst_dir(n), "end_request.json"))
    except OSError:
        pass


def _kill_game(n: int, pid: int) -> list[int]:
    """关游戏：游戏提权、直接杀常被拒 —— 杀这个实例的启动器守护（游戏挂在它的 Job 里，跟着退，零 UAC），再补一刀游戏本身。
    和 war3-brain/brain/ops.py 的 kill_game 同一个办法。"""
    killed = []
    for launcher in [p for p in _procs() if p["name"].lower() == "war3_launch.exe" and f"War3-{n}.exe" in p["cmd"]]:
        killed += kill_tree(launcher["pid"])
    if pid:
        try:
            p = psutil.Process(pid)
            if WAR3_EXE.match(p.name() or ""):      # 动手前再核对一次：pid 被复用了就不是我们的游戏
                p.kill()
                killed.append(pid)
        except psutil.Error:
            pass
    return killed


@_instance_locked
def stop_instance(n: int, keep_farm: bool = False, who: str = "unknown") -> dict:
    """停一个实例，停完不会再自己开起来：先挂停止牌 → farm 守护 → 大脑/老 AI 和它们的模块 → 游戏。
    按范围起的 farm（一个进程管好几个号）不杀 —— 杀了会把它名下别的号的游戏一起带走（启动器守护是它的子进程）；
    它看见停止牌就不再重开这个号。keep_farm = 只停这一局和 AI、让 farm 按下一局设置重开（不挂牌）。"""
    if not keep_farm:
        set_hold(n, "console-stop")
    mark_operator_end(n, "console-stop", who)
    st = status(n)[0]
    killed, notes = [], []
    if not keep_farm:
        for f in st["farm"]:
            lo, hi = f["range"]
            if all(os.path.exists(hold_path(k)) for k in range(lo, hi + 1)):
                # 只管这一个号，或者它管的号全挂了牌（全部停止时停到最后一个）：它已经没有要管的了，连树停掉
                killed += kill_tree(f["pid"], include_self=False)   # farm 的子进程里有启动器守护，一起
                killed += kill_tree(f["pid"])
            else:
                notes.append(f"farm pid {f['pid']} 还管着 {lo}~{hi} 号，没杀它；它看到停止牌不会再开 {n} 号")
    for b in st["brain"] + st["oldAi"] + st.get("bot", []):
        killed += kill_tree(b["pid"])
    killed += _kill_game(n, st["game"]["pid"] if st["game"]["alive"] else 0)
    if not keep_farm:
        # 导播也停（09-19 起导演台按实例开关：next_game.json 的 director，下次「开始测试」再按开关带上）
        killed += stop_director(n)
    return {"ok": True, "inst": n, "killed": sorted(set(killed)), "notes": notes}


def stop_all() -> dict:
    """全部停止：每个有 farm / AI / 游戏 / 导播在跑的实例都挂停止牌、停掉（导演台开关留着，下次开始测试照带）。"""
    res = []
    for s in status():
        if s["farm"] or s["brain"] or s["oldAi"] or s.get("bot") or s["game"]["alive"] or s["launcher"] or s["director"]:
            res.append(stop_instance(s["inst"]))
    return {"ok": True, "stopped": [r["inst"] for r in res], "results": res}


@_instance_locked
def start_test(n: int, hours: float = 24.0, director: bool | None = None) -> dict:
    """开始测试 = 摘停止牌 + farm 守护（打完按下一局设置自动重开）+ 新大脑监督器 + 导演台（按开关）。已经在跑的不重复起。
    director：True / False = 这次顺手把这个实例的导演台开关设成开 / 关（记在 next_game.json，以后每次开始测试照这个）；
    None = 照这个实例原来的开关。导播真正的拉起在 app.py（要先停掉 Farsight 导播台页托管的那份）。"""
    problem = game_problem() or mode_problem(n)
    if match_mode_of(n) == HUMAN_MODE and director:
        problem = "玩家对战模式关闭自动导播，请选择不带导演台"
    if problem:                                      # 游戏目录没设 / 版本不支持：AI 也别起（起了也没游戏可接）
        return {"ok": False, "inst": n, "error": problem, "farm": {"ok": False, "error": problem}, "brain": None,
                "scheme": scheme_of(n), "director": False}
    clear_hold(n)
    if director is not None:
        write_next_game(n, {"director": bool(director)})
    st = status(n)[0]
    farm = start_farm(n, hours)
    if not farm.get("ok"):
        return {"ok": False, "inst": n, "farm": farm, "brain": None, "scheme": scheme_of(n), "director": False}
    if st["oldAi"]:
        brain = {"ok": True, "note": f"实例 {n} 在跑老 AI，不起新大脑（两个大脑会同时指挥）"}
    elif st["brain"] or st.get("bot"):
        brain = {"ok": True, "note": "AI 已经在跑"}
    else:
        brain = start_ai(n, hours)
    return {"ok": bool(farm.get("ok") and brain.get("ok")), "inst": n, "farm": farm, "brain": brain,
            "scheme": scheme_of(n), "director": bool(next_game(n).get("director"))}


# ── AI 方案（09-24）：每个实例选一个方案（next_game.json 的 scheme），开始测试 / 切换时按它起 AI ──
def scheme_of(n: int) -> str:
    return next_game(n).get("scheme") or S.DEFAULT_REF


def start_scheme(n: int, ref: str, hours: float = 24.0) -> dict:
    """起方案运行器（tools/run_scheme.py）：每局一个子进程跑这个方案，结果记进 inst<N>/scheme_matches.jsonl。"""
    clear_hold(n)
    if status(n)[0].get("bot"):
        return {"ok": False, "error": f"实例 {n} 已经有方案运行器在跑"}
    d = C.inst_dir(n)
    os.makedirs(d, exist_ok=True)
    cmd = (f'cmd.exe /c "set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{RUN_SCHEME}" --inst {n} --scheme {ref} --hours {hours} '
           f'>> "{os.path.join(d, "scheme_supervisor.out")}" 2>&1"')
    r = spawn_detached(cmd, C.ROOT)
    return {"ok": r.get("rc") == 0, **r}


@_instance_locked
def start_ai(n: int, hours: float = 24.0) -> dict:
    """按这个实例选的方案起 AI：参考大脑 -> run_brain.py；玩家方案 -> run_scheme.py（没被信任的拒绝）。"""
    problem = mode_problem(n)
    if problem:
        return {"ok": False, "error": problem}
    ref = scheme_of(n)
    try:
        s = S.get(ref)
    except S.SchemeError as exc:
        return {"ok": False, "error": str(exc)}
    if s["kind"] == "brain":
        return start_brain(n, hours)
    if not s.get("trusted"):
        return {"ok": False, "error": f"方案 {ref} 还没被信任（别人的代码，先在「AI 方案」页确认）"}
    return start_scheme(n, ref, hours)


def stop_ai(n: int) -> list[int]:
    """停掉这个实例上所有的 AI（参考大脑监督器 + 方案运行器，连同它们的子进程）。游戏和 farm 不动。"""
    st = status(n)[0]
    killed = []
    for b in st["brain"] + st.get("bot", []):
        killed += kill_tree(b["pid"])
    return killed


@_instance_locked
def switch_scheme(n: int, ref: str, now: bool = True, hours: float = 24.0) -> dict:
    """给实例换方案。now=True 且实例在跑：停掉当前 AI、新方案立刻接管这一局（当前这局不重开）；
    now=False：只记下来，下次「开始测试」/ AI 重起时生效。"""
    s = S.get(ref)                                   # 不存在 / 坏掉的方案在这里就报错
    if s["kind"] in S.KINDS and not s.get("trusted"):
        return {"ok": False, "error": f"方案 {ref} 还没被信任（别人的代码，先确认信任）"}
    write_next_game(n, {"scheme": ref})
    st = status(n)[0]
    running = st["game"]["alive"] or st["farm"]
    if not now or not running:
        return {"ok": True, "inst": n, "scheme": ref, "applied": "next", "note": "已记下，下次开始测试时用这个方案"}
    killed = stop_ai(n)
    if killed:
        time.sleep(1.0)                              # 让旧 AI 的租约 / 车道释放
    r = start_ai(n, hours)
    return {"ok": bool(r.get("ok")), "inst": n, "scheme": ref, "applied": "now", "killed": killed, "start": r}


def start_many(nums: list[int], hours: float = 24.0, gap_s: float = 4.0, director: bool | None = None) -> dict:
    """依次起好几个实例的测试。两局之间隔 gap_s 秒 —— 几个游戏同时加载会抢显卡和地图加载，
    抢种族的窗口只有 3 秒（farm.py 起多个实例时也是错开 4 秒）。"""
    res = []
    for i, n in enumerate(dict.fromkeys(int(x) for x in nums)):
        if i:
            time.sleep(gap_s)
        res.append(start_test(n, hours, director))
    return {"ok": all(r["ok"] for r in res), "results": res}


def kill_proc(n: int, pid: int) -> dict:
    """停这个实例名下的某一个进程（只认 status 里列出来的，别的 pid 一律不动）。
    farm 只杀它自己：这局照打，打完不再重开；游戏 / 启动器：关掉这局（farm 在的话会按下一局设置重开）。"""
    st = status(n)[0]
    kinds = {}
    if st["game"]["alive"]:
        kinds[st["game"]["pid"]] = "game"
    if st["launcher"]:
        kinds[st["launcher"]["pid"]] = "launcher"
    for key in ("farm", "brain", "oldAi", "director", "modules", "bot"):
        for p in st.get(key, []):
            kinds[p["pid"]] = key
    kind = kinds.get(pid)
    if not kind:
        return {"ok": False, "error": f"pid {pid} 不是实例 {n} 的进程（或已经退了）"}
    if kind in ("game", "launcher"):
        mark_operator_end(n, "console-kill-game")
        killed = _kill_game(n, st["game"]["pid"] if st["game"]["alive"] else 0)
    elif kind == "farm":
        try:
            psutil.Process(pid).kill()
            killed = [pid]
        except psutil.Error as exc:
            return {"ok": False, "error": str(exc)}
    else:
        killed = kill_tree(pid)
    return {"ok": True, "kind": kind, "killed": killed}


DIRECTOR_PY = os.path.join(C.ANALYTICS, "director_inst.py")


def director_lock(n: int) -> int:
    """inst<N>/director.lock 里记的导播 pid（独立导播进程或 Farsight worker 里托管的那份都写它）；没有 = 0。"""
    try:
        return int((open(os.path.join(C.inst_dir(n), "director.lock"), encoding="utf-8").read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


def clear_director_lock(n: int) -> None:
    try:
        os.remove(os.path.join(C.inst_dir(n), "director.lock"))
    except OSError:
        pass


def start_director(n: int) -> dict:
    """拉起这个实例的独立导播 war3-analytics/director_inst.py（导演台那条线的代码，这里只负责起停）：
    自动运镜 + 头顶血条看门线程（敌我配色、长度随镜头缩放，设置在 war3-analytics/bars-state.json，每局换游戏自动重发）。
    它按进程名 War3-<N>.exe 每 5 秒重找游戏，farm 每局重开游戏也跟得上 —— 起一次管到停。
    ⚠ 同一个实例只能有一个导播（director.lock）：Farsight 导播台页在 worker 里托管的那份要先停掉、清锁（app.py ensure_director）。"""
    if [p for p in _procs() if _is_director(p, n)]:
        return {"ok": True, "note": "导播进程已经在跑"}
    out = os.path.join(C.FARM_DIR, f"director{n}.out")
    err = os.path.join(C.FARM_DIR, f"director{n}.err")
    cmd = f'cmd.exe /c ""{C.PYTHON}" -u "{DIRECTOR_PY}" --inst {n} >> "{out}" 2>> "{err}""'
    r = spawn_detached(cmd, C.ANALYTICS)
    return {"ok": r.get("rc") == 0, **r}


def stop_director(n: int) -> list[int]:
    """停这个实例的独立导播进程；锁是它的就一起清掉。Farsight worker 里托管的那份不在这里管（app.py）。"""
    killed = []
    for p in [p for p in _procs() if _is_director(p, n)]:
        killed += kill_tree(p["pid"])
    if director_lock(n) in killed:
        clear_director_lock(n)
    return killed


def start_brain(n: int, hours: float = 6.0) -> dict:
    clear_hold(n)
    if status(n)[0]["brain"]:
        return {"ok": False, "error": f"实例 {n} 的新大脑已经在跑"}
    d = C.inst_dir(n)
    os.makedirs(d, exist_ok=True)
    mode_args = f" --match-mode {HUMAN_MODE}" if match_mode_of(n) == HUMAN_MODE else ""
    cmd = (f'cmd.exe /c "set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{RUN_BRAIN}" --inst {n} --hours {hours}{mode_args} '
           f'>> "{os.path.join(d, "brain_supervisor.out")}" 2>> "{os.path.join(d, "brain_supervisor.err")}""')
    r = spawn_detached(cmd, C.BRAIN)
    return {"ok": r.get("rc") == 0, **r}


def stop_brain(n: int) -> dict:
    st = status(n)[0]
    killed = []
    for b in st["brain"]:
        killed += kill_tree(b["pid"])
    return {"ok": True, "killed": killed, "note": "当前这局的对局文件不会写结局（监督器被强停）"}


@_instance_locked
def write_next_game(n: int, patch: dict) -> dict:
    path = os.path.join(C.inst_dir(n), "next_game.json")
    cur = next_game(n)
    mode = patch.get("matchMode", cur.get("matchMode") or DEFAULT_MODE)
    if mode not in (DEFAULT_MODE, HUMAN_MODE):
        raise ValueError("未知对局模式")
    if mode != (cur.get("matchMode") or DEFAULT_MODE):
        st = status(n)[0]
        if (st["game"]["alive"] or st.get("launcher") or st["brain"] or st.get("bot") or st["oldAi"]
                or (st["farm"] and not os.path.exists(hold_path(n)))):
            raise ValueError("请先停止实例，再切换对局模式")
        if mode == HUMAN_MODE:
            cur.update(matchMode=mode, speed=100, difficulty=2)
            cur.pop("scheme", None)
            cur.pop("director", None)
        else:
            cur.pop("matchMode", None)
    if mode == HUMAN_MODE:
        if patch.get("scheme") not in (None, "", S.DEFAULT_REF):
            raise ValueError("玩家对战模式目前仅支持 builtin/xwar3")
        if patch.get("director"):
            raise ValueError("玩家对战模式关闭自动导播")
        if "difficulty" in patch and patch["difficulty"] != 2:
            raise ValueError("玩家对战模式使用基础电脑难度 2，开局后暂停原生 AI")
    if "director" in patch:
        # 这个实例带不带导演台（独立导播进程：自动运镜 + 头顶血条）。farm 不读这一项，Farsight 按它拉起 / 看护导播（start_director）
        if patch["director"]:
            cur["director"] = True
        else:
            cur.pop("director", None)
    if "scheme" in patch:
        # 这个实例用哪个 AI 方案（来源/id）；空 = 回到默认（参考大脑）
        if patch["scheme"] and patch["scheme"] != S.DEFAULT_REF:
            S.parse_ref(patch["scheme"])
            cur["scheme"] = patch["scheme"]
        else:
            cur.pop("scheme", None)
    for k in ("map", "race", "enemyRace", "difficulty", "speed"):
        if k in patch:
            v = patch[k]
            if v in (None, "", 0) and k != "map":
                cur.pop(k, None)
            elif v in (None, ""):
                cur.pop(k, None)
            else:
                cur[k] = v if k == "map" else int(v)
    os.makedirs(C.inst_dir(n), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    if cur.get("speed"):
        with open(os.path.join(C.inst_dir(n), "speed.txt"), "w", encoding="utf-8") as fh:
            fh.write(str(cur["speed"]))
    return cur

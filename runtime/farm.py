# -*- coding: utf-8 -*-
r"""farm.py —— 一条命令拉起 N 个游戏实例（多开跑批）。

    python runtime\farm.py --count 6                         起 1~6 号：左上角、画面 1920x1080、3 倍速、人族对人族、简单电脑
    python runtime\farm.py --first 5 --count 5 --keep        只管 5 号，打完按 next_game.json 自动重开（远见「开始测试」就这样起）
    python runtime\farm.py --count 2 --window 0,0,1600,900   换位置 / 分辨率（x,y 是外框左上角，w,h 是画面区域）
    python runtime\farm.py --count 6 --minimize              最小化（关渲染省约 40% CPU；⚠ 最小化的局模拟是停着的，只适合量成本）
    python runtime\farm.py --status                          现在有哪些实例
    python runtime\farm.py --stop                            本仓库起的全停：farm、AI、导播、游戏、启动器

★ 09-26 从 farm.ps1 改写成 Python（参数一样，写法换成 --小写）：部署只要 Python，不用再装 PowerShell 7。
  原来给旧 PowerShell AI 用的 -WithAi（策略层监督器、AI 看护、回收旧监督器）没搬 —— OpenWar3 的大脑由远见另起（run_brain.py）。

每个实例 = 一个 war3_launch.exe 守护进程 + 一个 War3-<N>.exe。守护进程必须常驻（运行时靠它挂着，
它一退控制通道就没了），输出分开存 bin\farm\inst<N>.out —— 多开时混在一起的日志没法读。
判据：inst<N>.out 里出现 `WAR3_PID=<pid>` 和 `control channel: ok` = 控制通道建好了（可以下令）；
只看进程在不在不够（09-16 踩过：进程活着但通道没建起来，一条命令也发不出去）。
开局职责（种族 / 难度 / 倍速 / 镜头）做完另打一行 `opening done`，和通道分开：AI 可以和游戏加载并行起。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r)
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402

import psutil  # noqa: E402

BIN = w3paths.BIN
FARM_DIR = w3paths.FARM_DIR
SEED_DIRS = (w3paths.SEED, w3paths.GAME_DATA)
# 只读基础数据**必须预置**：空目录会让策略层静默降级（读不到就用内置缺省，而且不报错）。
# 产出物（matches/ fights/ 日志、reserved.json ……）各实例独立，由程序自己生成。
# 跨局记忆（known_halls / known_spots）先各自一份初值、各自演进：共享能互相学落点，但并发写会静默覆盖。
SEEDS = ("units.json", "names.json", "params.json", "doctrine.txt", "race.txt", "enemyrace.txt", "speed.txt",
         "known_halls.json", "known_spots.json")
REQUIRED_SEEDS = ("units.json", "names.json")
# 停止牌：远见「停止实例 / 全部停止」挂上 = 这个号停了就别再自己开起来。
# 同一个文件名 console/server/instances.py（HOLD_FILE）和 run_brain.py（HOLD_FILE）也在认，改一处要改三处。
HOLD_FILE = "hold.json"
# 游戏进程名：原名 War3.exe 或多开的 War3-<N>.exe。⚠ pid 会被系统复用，认游戏一律核对进程名
WAR3_EXE = re.compile(r"^war3(-\d+)?\.exe$", re.I)
# 本仓库的 AI / 导播入口：--stop 只收这些（别的仓库、别的终端的同名脚本不碰）
AI_SCRIPT = re.compile(r"^(mod_\w+|tuner|autoloop|director_inst|run_brain|run_scheme)\.py$", re.I)
NO_WINDOW = 0x08000000        # CREATE_NO_WINDOW：守护进程、导播都不开控制台窗口（输出本来就进文件）
RACES = {1: "人族", 2: "兽族", 3: "亡灵", 4: "暗夜"}


def log(text: str = "") -> None:
    print(text, flush=True)


def now() -> str:
    return time.strftime("%H:%M:%S")


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ── inst<N>.out ──────────────────────────────────────────────────────
def out_path(n: int) -> str:
    return os.path.join(FARM_DIR, f"inst{n}.out")


def read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return ""


def war3_pid(text: str) -> int:
    m = re.findall(r"WAR3_PID=(\d+)", text)
    return int(m[-1]) if m else 0


def game_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        return bool(WAR3_EXE.match(psutil.Process(pid).name()))
    except psutil.Error:
        return False


def channel_ready(text: str) -> bool:
    return "control channel: ok" in text and game_alive(war3_pid(text))


def farm_instances() -> list[dict]:
    rows = []
    try:
        names = os.listdir(FARM_DIR)
    except OSError:
        return rows
    for fn in names:
        m = re.fullmatch(r"inst(\d+)\.out", fn)
        if not m:
            continue
        text = read_text(os.path.join(FARM_DIR, fn))
        pid = war3_pid(text)
        rows.append({"inst": int(m.group(1)), "war3Pid": pid, "alive": game_alive(pid),
                     "channel": "control channel: ok" in text})
    return sorted(rows, key=lambda r: r["inst"])


def print_table(rows: list[dict]) -> None:
    log(f"{'inst':>5} {'war3Pid':>8} {'alive':>6} {'channel':>8}")
    for r in rows:
        log(f"{r['inst']:>5} {r['war3Pid']:>8} {str(r['alive']):>6} {str(r['channel']):>8}")


# ── 实例目录 / exe / 下一局 ─────────────────────────────────────────
def held(n: int) -> bool:
    return os.path.exists(os.path.join(w3paths.inst_dir(n), HOLD_FILE))


def new_instance_dir(n: int) -> str:
    d = w3paths.inst_dir(n)
    for sub in ("", "matches", "fights"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    for name in SEEDS:
        dst = os.path.join(d, name)
        if os.path.exists(dst):
            continue
        for sd in SEED_DIRS:
            src = os.path.join(sd, name)
            if os.path.isfile(src):
                shutil.copyfile(src, dst)
                break
    # 基础数据没预置成的话后面全是白跑：当场检查，不等出问题
    missing = [x for x in REQUIRED_SEEDS if not os.path.isfile(os.path.join(d, x))]
    if missing:
        raise RuntimeError(f"实例 {n} 的基础数据缺失：{', '.join(missing)}（在远见设好游戏目录会自动提取）")
    return d


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def instance_exe(n: int, game_dir: str) -> str:
    """★ 每个实例用自己名字的 exe：War3-1.exe、War3-2.exe ……（09-16 起：直播时多个窗口要分得清哪个是哪个）。
    窗口标题都一样，进程名分得出来：OBS「匹配可执行文件」在重开一局后也能自动绑回同一个实例。
    War3.exe 只是 514 KB 的启动壳，Game.dll / Storm.dll / MPQ 都按工作目录找，所以拷贝放在游戏目录里、原样拷贝整条链路照常。
    ⚠ 不碰 Game.dll（项目硬性约束），拷的只是启动壳；和原版哈希不同就重拷。"""
    src = os.path.join(game_dir, "War3.exe")
    dst = os.path.join(game_dir, f"War3-{n}.exe")
    if not os.path.isfile(dst) or _sha256(dst) != _sha256(src):
        try:
            shutil.copyfile(src, dst)
        except OSError:
            if not os.path.isfile(dst):          # 正被上一局占着拷不进去：旧的还在就先用着，别让这一局起不来
                raise
    return dst


def resolve_map(m: str, maps_dir: str) -> str:
    return m if not m or os.path.isabs(m) else os.path.join(maps_dir, m)


def next_game(inst_dir: str, base: dict, maps_dir: str) -> dict:
    """下一局设置：实例目录下 next_game.json（远见「下一局设置」写的 map / race / enemyRace / difficulty / speed），缺的项用命令行参数。
    每局起之前读一次 —— 在远见换地图 / 种族 / 难度 / 倍速不用重启 farm，下一局生效。没有这个文件 = 全用命令行参数。"""
    g = dict(base)
    f = os.path.join(inst_dir, "next_game.json")
    if not os.path.isfile(f):
        return g
    try:
        with open(f, encoding="utf-8-sig") as fh:
            j = json.load(fh)
        for k in ("map", "race", "enemyRace", "difficulty", "speed", "matchMode"):
            if j.get(k) is not None and str(j[k]) != "":
                g[k] = j[k]
    except (OSError, ValueError, AttributeError) as exc:
        log(f"  next_game.json 读不了（{exc}），用命令行参数")
        return dict(base)
    g["map"] = resolve_map(str(g["map"]), maps_dir)
    if g["map"] != base["map"] and not os.path.isfile(g["map"]):
        log(f"  next_game.json 的地图不存在：{g['map']}，用 {base['map']}")
        g["map"] = base["map"]
    return g


def launch_args(exe: str, n: int, g: dict, cam_distance: int, minimize: bool, window: str) -> list[str]:
    a = ["--exe", exe, "--map", str(g["map"]), "--race", str(g["race"]), "--difficulty", str(g["difficulty"]),
         "--speed", str(g["speed"]), "--inst", str(n), "--cam-distance", str(cam_distance)]
    if _int(g.get("enemyRace")) > 0:
        a += ["--enemy-race", str(g["enemyRace"])]
    if g.get("matchMode") != "human_vs_openwar3ai":
        a += ["--minimize"] if minimize else ["--client", window]
    return a


def describe(n: int, g: dict) -> str:
    er = _int(g.get("enemyRace"))
    return (f"  实例 {n} 这一局：地图 {os.path.basename(str(g['map']))}｜我方 {RACES.get(_int(g['race']), g['race'])} "
            f"对手 {RACES.get(er, er) if er > 0 else '镜像'}｜难度 {g['difficulty']}｜倍速 {g['speed']}")


# ── 起进程 ───────────────────────────────────────────────────────────
def start_launcher(args: list[str], out: str, fullscreen: bool = False) -> int:
    """后台起启动器守护进程，输出进 inst<N>.out。
    只把 stdin / stdout / stderr 三个句柄交给它（Python 默认不继承别的句柄）：
    原来 farm.ps1 的守护进程会继承调用方的管道，管道永远不关，调用方一直卡住 —— 现在不会了。"""
    launcher = w3paths.LAUNCH_EXE
    if fullscreen:
        from w3fullscreen import fullscreen_launcher
        launcher = fullscreen_launcher(launcher)
        log("  玩家模式：原生全屏（去掉启动器固定的 -window）")
    os.makedirs(FARM_DIR, exist_ok=True)
    with open(out, "wb") as fh:
        p = subprocess.Popen([launcher, *args], cwd=BIN, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    return p.pid


def ensure_director(n: int) -> int | None:
    """每实例导播（director_inst.py --inst N，按进程名 War3-<N>.exe 跟，每局换 pid 也跟得上）。
    锁文件 inst<N>\\director.lock 里的 pid 还活着就不管（director_inst.py 自己也按锁防重复）。"""
    lock = os.path.join(w3paths.inst_dir(n), "director.lock")
    try:
        with open(lock, encoding="utf-8") as fh:
            old = _int(fh.read().strip())
    except OSError:
        old = 0
    if old > 0 and psutil.pid_exists(old):
        return None
    script = os.path.join(w3paths.DIRECTOR, "director_inst.py")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(os.path.join(FARM_DIR, f"director{n}.out"), "wb") as out, \
            open(os.path.join(FARM_DIR, f"director{n}.err"), "wb") as err:
        p = subprocess.Popen([w3paths.python_exe(), "-u", script, "--inst", str(n)], cwd=w3paths.DIRECTOR,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=NO_WINDOW, env=env)
    return p.pid


# ── --stop ───────────────────────────────────────────────────────────
def _ours(arg: str) -> str | None:
    """命令行参数是本仓库里的文件就返回它的文件名（小写），否则 None。"""
    root = os.path.normcase(w3paths.ROOT) + os.sep
    try:
        full = os.path.normcase(os.path.abspath(arg))
    except (TypeError, ValueError):
        return None
    return os.path.basename(full) if full.startswith(root) else None


def _kill(p: psutil.Process, tree: bool) -> int:
    n = 0
    procs = (p.children(recursive=True) if tree else []) + [p]
    for q in procs:
        try:
            q.kill()
            n += 1
        except psutil.Error:
            pass
    return n


def _our_launchers() -> list[psutil.Process]:
    want = {os.path.normcase(w3paths.LAUNCH_EXE),
            os.path.normcase(os.path.join(BIN, "human-fullscreen", "war3_launch.exe"))}
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        cl = p.info["cmdline"] or []
        if (p.info["name"] or "").lower() == "war3_launch.exe" and cl and os.path.normcase(os.path.abspath(cl[0])) in want:
            out.append(p)
    return out


def _our_games() -> list[psutil.Process]:
    """本仓库起的游戏：我们的启动器的子进程，或者 inst<N>.out 里记着的 pid（核对进程名）。自己开着玩的魔兽不碰。"""
    pids = {r["war3Pid"] for r in farm_instances() if r["alive"]}
    for lp in _our_launchers():
        try:
            pids |= {c.pid for c in lp.children()}
        except psutil.Error:
            pass
    out = []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if WAR3_EXE.match(p.name()):
                out.append(p)
        except psutil.Error:
            pass
    return out


def stop_all() -> None:
    """先停 farm（不然它看见游戏没了又重开）→ AI 和导播（连子进程）→ 游戏（进程内动作 22，零 UAC；
    外部强杀会撞 War3 自己改写的 DACL）→ 兜底清理残留的启动器守护。只认本仓库的进程。"""
    me = os.getpid()
    farms, ai = 0, 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.pid == me:
            continue
        name = (p.info["name"] or "").lower()
        cl = p.info["cmdline"] or []
        files = [f for f in (_ours(a) for a in cl[1:8]) if f]
        if name.startswith(("python", "pwsh", "powershell")) and ("farm.py" in files or "farm.ps1" in files) \
                and not ({"--status", "--stop", "-status", "-stop"} & {a.lower() for a in cl}):
            farms += _kill(p, tree=False)
        elif name.startswith("python") and any(AI_SCRIPT.match(f) for f in files):
            ai += _kill(p, tree=True)
    if farms:
        log(f"  停掉 {farms} 个 farm")
    if ai:
        log(f"  停掉 {ai} 个 AI / 导播进程")
    for g in _our_games():
        try:
            subprocess.run([w3paths.CLI_EXE, "--pid", str(g.pid), "--action", "22"], capture_output=True, timeout=10,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    for _ in range(30):
        time.sleep(0.5)
        if not _our_games():
            break
    time.sleep(2)                                    # 守护本来就会跟着自己那局退出；这里只兜底
    for lp in _our_launchers():
        _kill(lp, tree=False)
    time.sleep(2)                                    # 守护一死，Job 会带走它那局游戏，但要一点时间（立刻数会多数一个）
    log(f"已停止。War3 剩：{len(_our_games())}  守护剩：{len(_our_launchers())}")


# ── 主流程 ───────────────────────────────────────────────────────────
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="拉起 N 个游戏实例（多开跑批）")
    ap.add_argument("--count", type=int, default=6, help="最后一个号（管 first..count）")
    ap.add_argument("--first", type=int, default=1, help="从几号开始；两个 farm 各管各的号、互不干扰")
    ap.add_argument("--map", default="", help="空 = openwar3.json 的 default_map；相对路径按 <游戏目录>\\Maps 补全")
    ap.add_argument("--speed", type=int, default=300)
    ap.add_argument("--race", type=int, default=1, help="1 人族 2 兽族 3 亡灵 4 暗夜")
    ap.add_argument("--enemy-race", type=int, default=0, help="对手种族；0 = 镜像")
    ap.add_argument("--difficulty", type=int, default=2, help="2 简单 3 普通 4 疯狂")
    ap.add_argument("--cam-distance", type=int, default=2600, help="镜头高度（对战默认 1650，2600 是导播的基准）")
    ap.add_argument("--game-dir", default="", help="空 = openwar3.json 的 game_dir（在远见里设）")
    ap.add_argument("--window", default="0,0,1920,1080", help="x,y,w,h：外框左上角 + 画面区域（渲染分辨率就是它）")
    ap.add_argument("--minimize", action="store_true", help="最小化（关渲染省 CPU；模拟是停着的）")
    ap.add_argument("--keep", action="store_true", help="跑批：某个实例打完就按下一局设置重开，直到 --hours 到点")
    ap.add_argument("--keep-only", action="store_true", help="不拉新实例，直接接管已经在跑的（换 farm 版本时用，隐含 --keep）")
    ap.add_argument("--no-director", action="store_true", help="不起每实例导播（远见起的 farm 都带这个，导播由远见管）")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    return ap.parse_args(argv)


class Farm:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.rng = range(a.first, a.count + 1)

    def game_dir(self) -> str:
        return self.a.game_dir or w3paths.game_dir()

    def maps_dir(self) -> str:
        if self.a.game_dir and not w3paths.config().get("maps_dir"):
            return os.path.join(self.a.game_dir, "Maps")
        return w3paths.maps_dir()

    def base(self) -> dict:
        m = resolve_map(self.a.map, self.maps_dir()) if self.a.map else w3paths.default_map()
        return {"map": m, "race": self.a.race, "enemyRace": self.a.enemy_race, "difficulty": self.a.difficulty,
                "speed": self.a.speed}

    def launch(self, n: int) -> int:
        inst = new_instance_dir(n)
        g = next_game(inst, self.base(), self.maps_dir())
        exe = instance_exe(n, self.game_dir())
        log(describe(n, g))
        out = out_path(n)
        # ⚠ 先删旧输出：里面还是上一局的 pid，新进程的重定向要稍后才写，早读会连上一个已经死掉的进程
        try:
            os.remove(out)
        except OSError:
            pass
        launcher = start_launcher(launch_args(exe, n, g, self.a.cam_distance, self.a.minimize, self.a.window), out,
                                  fullscreen=g.get("matchMode") == "human_vs_openwar3ai")
        return launcher

    def start_all(self) -> None:
        for n in self.rng:
            try:
                os.remove(os.path.join(w3paths.inst_dir(n), HOLD_FILE))   # 起 farm 本身就是「要开」：摘掉自己管的号的停止牌
            except OSError:
                pass
            pid = self.launch(n)
            log(f"  实例 {n}：守护 pid={pid}")
            if not self.a.no_director:
                d = ensure_director(n)
                if d:
                    log(f"            导播 pid={d}")
            # ⚠ 错开启动：几个进程同时抢地图加载和显卡初始化，容易有实例卡在加载（抢种族的窗口只有 3 秒）
            if n != self.rng[-1]:
                time.sleep(4)
        log("\n等各实例把通道建好（判据是 WAR3_PID= 那一行）…")
        deadline = time.time() + 120
        while time.time() < deadline:
            if all(war3_pid(read_text(out_path(n))) for n in self.rng):
                break
            time.sleep(0.3)
        log("\n=== 结果 ===")
        print_table(farm_instances())

    def keep(self) -> None:
        """跑批回收：一实例一局，打完（游戏进程没了）就按 next_game.json 重开一局，直到 --hours 到点。
        判据是「游戏进程还在不在」，不是「AI 还在不在」：AI 可能别的原因退了而游戏还好好的，那种情况重拉只会多占一份资源。
        挂了停止牌的号不重开、不补导播（一个 farm 按范围管好几个号时，停其中一个不必杀整个 farm）。"""
        stop_at = time.time() + self.a.hours * 3600
        rounds: dict[int, int] = {}
        held_said: dict[int, bool] = {}
        director_at = 0.0
        log(f"\n跑批模式：某实例打完就重起，直到 {time.strftime('%H:%M', time.localtime(stop_at))}"
            f"（Ctrl+C 或 farm.py --stop 结束）")
        while time.time() < stop_at:
            if not self.a.no_director and time.time() >= director_at:       # 导播 10 秒看一次（退了拉回来）
                director_at = time.time() + 10
                for n in self.rng:
                    if not held(n):
                        d = ensure_director(n)
                        if d:
                            log(f"  实例 {n} 导播 pid={d} {now()}")
            # ★ 09-16 认输后立刻重开下一局。认输后游戏 0.3 秒内就退出，剩下的等待几乎全是轮询间隔：0.5 秒一看
            time.sleep(0.5)
            for n in self.rng:
                try:
                    self._keep_one(n, rounds, held_said)
                except Exception as exc:  # noqa: BLE001 - 一个号重开失败（地图没了、exe 被占……）不能把整个跑批带走
                    log(f"  实例 {n} 重开出错：{exc}（半秒后再试）")
                    time.sleep(2)
        log("\n跑批结束。各实例局数：" + "  ".join(f"inst{n}={rounds[n] + 1}" for n in sorted(rounds)))

    def _keep_one(self, n: int, rounds: dict, held_said: dict) -> None:
        if game_alive(war3_pid(read_text(out_path(n)))):
            return
        if held(n):
            if not held_said.get(n):
                log(f"  实例 {n} 挂了停止牌（{HOLD_FILE}），不重开 {now()}")
                held_said[n] = True
            return
        held_said[n] = False
        rounds[n] = rounds.get(n, 0) + 1
        log(f"  实例 {n} 打完了，起第 {rounds[n] + 1} 局 {now()}")
        w3paths.config.cache_clear()                 # 玩家在远见里换了游戏目录：下一局就用新的
        self.launch(n)
        # 等「活着的 WAR3_PID + 控制通道就绪」就走，最多 40 秒（比定长等更快，也不会在通道没好时就算开好）
        for _ in range(80):
            time.sleep(0.5)
            if channel_ready(read_text(out_path(n))):
                break

    def summary(self) -> int:
        want = len(self.rng)
        live = [r for r in farm_instances() if r["alive"] and r["inst"] in self.rng]
        log(f"通道就绪且存活：{len(live)} / {want}")
        if len(live) < want:
            log(f"⚠ 有实例没起来，看 {FARM_DIR}\\inst<N>.out 最后几行（handshake / control channel 那两行）")
        return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    a = parse_args(argv)
    if a.status:
        rows = farm_instances()
        if not rows:
            log("没有实例记录")
            return 0
        print_table(rows)
        log(f"活着的实例：{sum(r['alive'] for r in rows)} / 记录 {len(rows)}")
        return 0
    if a.stop:
        stop_all()
        return 0
    farm = Farm(a)
    if not os.path.isfile(w3paths.LAUNCH_EXE):
        log(f"缺 {w3paths.LAUNCH_EXE}：先双击仓库根目录的 start.bat（会从 prebuilt\\ 装上）")
        return 1
    gd = farm.game_dir()
    if not gd or not os.path.isfile(os.path.join(gd, "War3.exe")):
        log(f"游戏目录没设或不对（{gd or '空'}）：在远见「控制中心」最上面设置魔兽争霸 III 的目录")
        return 1
    if not os.path.isfile(farm.base()["map"]):
        log(f"地图不在：{farm.base()['map']}")
        return 1
    os.makedirs(FARM_DIR, exist_ok=True)
    try:
        if a.keep_only:
            log("只接管正在跑的实例（--keep-only）：不拉起新实例")
        else:
            # ⚠ 只删自己管的号：另一个 farm（比如直播的 1 号）正靠它自己的 inst<N>.out 认游戏
            for n in farm.rng:
                try:
                    os.remove(out_path(n))
                except OSError:
                    pass
            farm.start_all()
        if a.keep or a.keep_only:
            farm.keep()
    except KeyboardInterrupt:
        log("\n收到 Ctrl+C，farm 退出（游戏和守护进程留着；要全停：farm.py --stop）")
        return 0
    except (RuntimeError, OSError) as exc:
        log(f"✘ {exc}")
        return 1
    return farm.summary()


if __name__ == "__main__":
    sys.exit(main())

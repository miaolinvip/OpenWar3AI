# -*- coding: utf-8 -*-
"""远见首页「控制中心」的后端（09-26）：本机各个服务在不在跑、起停、看日志，以及环境自检。只听本机。

    GET  /api/services                     每个服务的状态（进程、端口、地址、日志路径、能做的操作）
    POST /api/services/{key}/{action}      start / stop / restart（远见自己只有 restart）
    GET  /api/services/{key}/log?lines=    日志最后几行
    GET  /api/health?refresh=              环境自检：Python、依赖包、Windows PowerShell、注入 DLL、游戏目录和版本、
                                           游戏数据、AMAI、网页、Node.js（可选）、LM Studio、磁盘

以前每个服务一个 bat（start-gateway.bat / start-speech.bat / start-website.bat）；09-26 起只剩一个 start.bat
（部署 + 起远见），其余都在这里点。起进程一律走 instances.spawn_detached（WMI，脱离远见的进程树：远见重启不会把它们带走）。
给人看的文字都在网页那边按 key 写（要翻译），这里只给状态和数据；报错原文（异常、日志）照原样给。
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes

import psutil

from . import coach as CO
from . import common as C
from . import connectview as CN
from . import instances as I
from . import webstamp as WS

W = C.w3paths
NOWIN = 0x08000000                                   # CREATE_NO_WINDOW
SPEECH_PY = os.path.join(W.SPEECH, "speak_api.py")
SPEECH_LOG = os.path.join(W.SPEECH, "speak_api.out")
WEBSITE = os.path.join(C.ROOT, "website")
WEBSITE_LOG = os.path.join(C.BIN, "website", "website.out")
CONSOLE_LOG = os.path.join(C.FARSIGHT, "cache", "server.out")


def console_log(port: int) -> str:
    """远见后台的日志：默认端口 server.out，别的端口 server-<端口>.out（和 console/launch.py 的 log_path 同一个规则；
    两个后台写同一个文件时后起的那个根本起不来）。"""
    return CONSOLE_LOG if port == W.port("console", 8866) else os.path.join(C.FARSIGHT, "cache", f"server-{port}.out")
MCP_PY = os.path.join(C.ROOT, "tools", "war3_mcp.py")
STATE_FILE = os.path.join(W.ENV_DIR, "state.json")   # setup.ps1 每次部署完写：什么时候、用的哪个 Python / Node
STARTING_S = 15.0                                    # 刚点了启动、进程还没出现在进程表里：这么久以内也算「启动中」
_starting: dict[str, float] = {}


_ports_cache: tuple[float, set[int] | None] = (0.0, None)


def _listening(port: int, fresh: bool = False) -> bool:
    """这个端口有没有程序在听。读系统的 TCP 监听表（1 毫秒，1 秒缓存）——
    ⚠ 别用「连一下试试」：Windows 上连一个没人听的本机端口要等到超时（实测每个 0.3~2 秒），首页一次查五六个服务就卡好几秒。"""
    global _ports_cache
    at, ports = _ports_cache
    if fresh or ports is None or time.monotonic() - at > 1.0:
        try:
            ports = {c.laddr.port for c in psutil.net_connections("tcp") if c.status == psutil.CONN_LISTEN}
        except (psutil.Error, OSError):
            ports = None
        _ports_cache = (time.monotonic(), ports)
    if ports is not None:
        return port in ports
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _script_procs(script: str) -> list[dict]:
    """跑着这个脚本的 python 进程。按「参数以 仓库相对路径 结尾」认 —— 手动 `python gateway/server.py` 起的也认得；
    外面包的 cmd.exe 不算（名字不是 python）。别的仓库里同名的脚本（旧仓库 xwar3 的 speak_api.py 在 war3-modules 下）不会误认。"""
    rel = _norm(os.path.relpath(script, C.ROOT))
    full = _norm(script)
    out = []
    for p in I._procs():
        if not p["name"].lower().startswith("python"):
            continue
        for a in p["args"][1:3]:
            n = _norm(a)
            if n == full or (not os.path.isabs(a) and n.endswith(rel)):
                out.append(p)
                break
    return out


def _wrappers(log: str) -> list[dict]:
    """远见起服务时包的那层 cmd.exe（命令行里有这个服务独有的日志路径）：它还在 = 这个服务的进程树还在。"""
    key = _norm(log)
    return [p for p in I._procs() if p["name"].lower() == "cmd.exe" and key in _norm(p["cmd"])]


def _website_procs() -> list[dict]:
    site = _norm(WEBSITE)
    return [p for p in I._procs() if p["name"].lower().startswith("node") and "astro" in p["cmd"].lower()
            and site in _norm(p["cmd"])]


def _state(up: bool, procs: list, key: str) -> str:
    if up and procs:
        return "running"
    if up:
        return "conflict"                            # 端口被别的程序占着
    if procs or time.time() - _starting.get(key, 0) < STARTING_S:
        return "starting"                            # 进程在了、端口还没听上（官网第一次要先 npm install，要一两分钟）
    return "stopped"


def _row(key: str, **kw) -> dict:
    procs = kw.pop("procs", [])
    return {"key": key, "pids": [p["pid"] for p in procs],
            "started": min((p["started"] or 0 for p in procs), default=None), **kw}


def _console(port: int) -> dict:
    me = psutil.Process()
    workers = 0
    for c in me.children():
        try:
            workers += "worker.py" in " ".join(c.cmdline() or [])
        except psutil.Error:
            pass
    try:
        web_built = os.path.getmtime(os.path.join(C.WEB_DIST, "index.html"))
    except OSError:
        web_built = None
    return {"key": "console", "state": "running", "pids": [me.pid], "started": me.create_time(), "port": port,
            "url": f"http://127.0.0.1:{port}/", "log": console_log(port), "actions": ["restart"],
            "extra": {"python": platform.python_version(), "pythonPath": sys.executable, "workers": workers,
                      "webBuilt": web_built}}


def _gateway() -> dict:
    port = CN._port()
    procs = _script_procs(CN.GATEWAY)
    ok = os.path.exists(CN.GATEWAY) and importlib.util.find_spec("websockets") is not None
    st = _state(_listening(port), procs, "gateway")
    return _row("gateway", procs=procs, state=st if ok or st != "stopped" else "unavailable",
                reason=None if ok else "missing-websockets", port=port,
                url=f"http://127.0.0.1:{port}/demo", log=CN.GATEWAY_LOG, actions=["start", "stop", "restart"])


def _speech() -> dict:
    port = W.port("speech", 8872)
    procs = _script_procs(SPEECH_PY)
    return _row("speech", procs=procs, state=_state(_listening(port), procs, "speech"), port=port,
                url=f"http://127.0.0.1:{port}/", log=SPEECH_LOG, actions=["start", "stop", "restart"])


def _lms_exe() -> str | None:
    import speak_launch as SL                        # speech/，w3paths.setup() 放上了 sys.path
    return SL.lms_exe()


def _llm() -> dict:
    # LM Studio 没在听就不发 HTTP（连不上要白等 1.5 秒）；在听才问它载了哪些模型
    s = CO.llm_server() if _listening(1234) or CO._start["running"] else {
        "up": False, "want": CO._brain_model(), "wantLoaded": False, "loaded": [], "error": ""}
    have = _lms_exe() is not None
    if CO._start["running"]:
        st = "starting"
    elif s["up"]:
        st = "running" if s["wantLoaded"] else "partial"      # 服务在，但要的模型没载入
    else:
        st = "stopped" if have else "unavailable"
    return {"key": "llm", "state": st, "pids": [], "started": None, "port": 1234, "url": None,
            "reason": None if have or s["up"] else "missing-lmstudio", "log": None,
            "actions": ["start", "stop"] if have else [],
            "extra": {"model": W.llm_model(), "want": s["want"], "loaded": [m["id"] for m in s["loaded"]],
                      "error": s["error"], "startOk": CO._start["ok"]}}


def _website() -> dict:
    port = W.port("website", 4321)
    procs = _website_procs()
    node = W.node_exe()
    ok = node is not None and os.path.isfile(os.path.join(WEBSITE, "package.json"))
    st = _state(_listening(port), procs or _wrappers(WEBSITE_LOG), "website")
    return _row("website", procs=procs, state=st if ok or st != "stopped" else "unavailable",
                reason=None if ok else ("missing-node" if node is None else "missing-website"), port=port,
                url=f"http://127.0.0.1:{port}/", log=WEBSITE_LOG, actions=["start", "stop", "restart"],
                extra={"node": node, "installed": os.path.isfile(os.path.join(WEBSITE, "node_modules", "astro", "astro.js"))})


def _mcp() -> dict:
    procs = _script_procs(MCP_PY)
    return _row("mcp", procs=procs, state="on-demand", port=None, url=None, log=None, actions=[],
                extra={"clients": len(procs)})


def services(port: int) -> dict:
    rows = []
    site = (("website", _website),) if os.path.isdir(WEBSITE) else ()     # 官网不在公开仓库里：只有开发机上显示
    for key, fn in (("console", lambda: _console(port)), ("gateway", _gateway), ("speech", _speech), ("llm", _llm),
                    *site, ("mcp", _mcp)):
        try:
            rows.append(fn())
        except Exception as e:  # noqa: BLE001  一个服务查挂了不影响别的
            rows.append({"key": key, "state": "error", "pids": [], "actions": [], "error": f"{type(e).__name__}: {e}"})
    return {"services": rows, "at": time.time(), "root": C.ROOT}


# ── 起停 ─────────────────────────────────────────────────────────────
def _kill(procs: list[dict], log: str | None) -> list[int]:
    killed = []
    for p in (_wrappers(log) if log else []) + procs:
        killed += I.kill_tree(p["pid"])
    return killed


def _wait_port(port: int, seconds: float) -> bool:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        if _listening(port, fresh=True):
            return True
        time.sleep(0.3)
    return False


def _start_speech() -> dict:
    port = W.port("speech", 8872)
    if _listening(port, fresh=True):
        return {"ok": True, "note": "already"}
    cmd = (f'cmd.exe /c "set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{SPEECH_PY}" --port {port} '
           f'> "{SPEECH_LOG}" 2>&1"')
    r = I.spawn_detached(cmd, W.SPEECH)
    if r.get("rc") != 0:
        return {"ok": False, "error": r.get("error") or f"rc={r.get('rc')}"}
    _starting["speech"] = time.time()
    if _wait_port(port, 12):
        return {"ok": True, "pid": r.get("pid")}
    return {"ok": False, "error": f"pid {r.get('pid')} 12 秒内没在 {port} 端口应答，看日志 {SPEECH_LOG}"}


def _start_website() -> dict:
    port = W.port("website", 4321)
    if _listening(port, fresh=True):
        return {"ok": True, "note": "already"}
    node = W.node_exe()
    if not node:
        return {"ok": False, "error": "官网预览要 Node.js：命令行运行 start.bat node 装一份（约 33 MB，装在 bin\\env\\node），"
                                      "或者自己装 Node.js 22"}
    ndir = os.path.dirname(node)
    astro = os.path.join(WEBSITE, "node_modules", "astro", "astro.js")
    npm = os.path.join(ndir, "npm.cmd")
    install = ""
    if not os.path.isfile(astro):                    # 第一次：先装依赖（一两分钟），国内连不上官方源就换镜像
        install = (f'("{npm}" install --no-audit --no-fund || "{npm}" install --no-audit --no-fund '
                   f'--registry=https://registry.npmmirror.com)&& ')
    os.makedirs(os.path.dirname(WEBSITE_LOG), exist_ok=True)
    cmd = (f'cmd.exe /c "set "PATH={ndir};%PATH%"&& cd /d "{WEBSITE}"&& ({install}"{node}" "{astro}" dev '
           f'--host 127.0.0.1 --port {port}) > "{WEBSITE_LOG}" 2>&1"')
    r = I.spawn_detached(cmd, WEBSITE)
    if r.get("rc") != 0:
        return {"ok": False, "error": r.get("error") or f"rc={r.get('rc')}"}
    _starting["website"] = time.time()
    if not install and _wait_port(port, 15):
        return {"ok": True, "pid": r.get("pid")}
    return {"ok": True, "pid": r.get("pid"), "note": "starting"}


def _restart_console(port: int) -> dict:
    """远见自己重启：另起一个脱离进程树的 cmd，等 2 秒（这边先退、让出端口）再在同一个端口起 run.py；然后这个进程退出。
    实例 worker 是这个进程的子进程，stdin 断了会自己退；游戏、farm、大脑、网关这些都是 WMI 起的，不受影响。"""
    run = os.path.join(C.FARSIGHT, "run.py")
    cmd = (f'cmd.exe /c "ping -n 3 127.0.0.1 >nul & set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{run}" '
           f'--port {port} > "{console_log(port)}" 2>&1"')
    r = I.spawn_detached(cmd, C.ROOT)
    if r.get("rc") != 0:
        return {"ok": False, "error": r.get("error") or f"rc={r.get('rc')}"}
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return {"ok": True, "note": "restarting"}


def _llm_stop() -> dict:
    """只卸载本系统用的那个模型（LM Studio 给载入的实例加 `:2` 这类后缀，按前缀认）；别的模型、LM Studio 程序本身都不碰 ——
    以前用 `lms unload --all`，会把用户自己为别的事载入的模型一起卸掉。"""
    import speak_launch as SL
    if not _listening(1234, fresh=True):
        return {"ok": True, "note": "LM Studio 本地服务没在跑"}
    s = CO.llm_server(0)
    want = s["want"] or W.llm_model()
    mine = [m["id"] for m in s["loaded"] if m["id"] and (m["id"] == want or m["id"].startswith(want + ":"))]
    errors = []
    for ident in mine:
        code, out = SL.lms(["unload", ident], timeout=60)
        if code != 0:
            errors.append(out.strip()[-200:])
    CO._cache["at"] = 0.0
    return {"ok": not errors, "unloaded": mine, "error": "; ".join(errors) or None}


# ── 彻底停止 ─────────────────────────────────────────────────────────
# 本系统的组件（按脚本 / 程序认）：全部停止时收拾 stop_all 没管到的漏网进程（比如 farm 输出被删了、实例号认不出来的）。
# 只认本仓库里的这些入口 —— 仓库里另外在跑的测试、编辑器的语言服务、别的仓库的同名脚本都不碰。
_COMPONENTS = (os.path.join("brains", "xwar3", "strategy", "run_brain.py"), os.path.join("tools", "run_scheme.py"),
               os.path.join("director", "director_inst.py"), os.path.join("runtime", "farm.py"),
               os.path.join("runtime", "farm.ps1"),                 # 09-26 前的 farm（升级前起的可能还在跑）
               os.path.join("gateway", "server.py"), os.path.join("speech", "speak_api.py"))


def _leftovers() -> list[dict]:
    root = _norm(C.ROOT)
    comps = [_norm(os.path.join(C.ROOT, c)) for c in _COMPONENTS]
    reflex = _norm(os.path.join(W.REFLEX, "mod_"))
    out = []
    for p in I._procs():
        name = p["name"].lower()
        args = [_norm(a) for a in p["args"][1:6] if a]
        if name == "war3_launch.exe" and _norm(p["cmd"]).find(_norm(C.BIN)) >= 0:
            out.append(p)
        elif name.startswith(("python", "pwsh", "powershell")) and any(a in comps or (a.startswith(reflex) and a.startswith(root)) for a in args):
            out.append(p)
    return out


def stop_everything(llm: bool = True) -> dict:
    """彻底停止（start.bat stop / stop.bat / 控制中心「全部停止」）：
    游戏实例（挂停止牌 → farm、AI、游戏、导播）→ 网关、气泡、官网预览 → 漏网的组件进程 → 本系统用的本地大模型（llm=True 时）。
    远见后台本身由调用方最后停（网页：回完请求再退出；命令行：launch.py 杀 run.py）。
    不碰：MCP 服务器（归 Claude 等客户端管，杀了客户端那边会报错）、LM Studio 程序本身、不是本系统起的魔兽窗口。"""
    out: dict = {"instances": I.stop_all(), "services": {}}
    out["services"]["gateway"] = _kill(_script_procs(CN.GATEWAY), CN.GATEWAY_LOG)
    out["services"]["speech"] = _kill(_script_procs(SPEECH_PY), SPEECH_LOG)
    out["services"]["website"] = _kill(_website_procs(), WEBSITE_LOG)
    _starting.clear()
    stray = []
    for p in _leftovers():
        stray += I.kill_tree(p["pid"])
    out["leftovers"] = sorted(set(stray))
    if llm and _lms_exe() is not None:
        try:
            out["llm"] = _llm_stop()
        except Exception as e:  # noqa: BLE001  LM Studio 出问题不挡别的
            out["llm"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out["mcp"] = len(_script_procs(MCP_PY))
    out["ok"] = True
    return out


def shutdown_console(delay: float = 0.8) -> None:
    """网页上点了「全部停止 + 关闭远见」：回完这个请求再退出。实例 worker 是这个进程的子进程，一起收掉。"""
    def bye():
        try:
            for c in psutil.Process().children(recursive=True):
                c.kill()
        except psutil.Error:
            pass
        os._exit(0)
    threading.Timer(delay, bye).start()


def act(key: str, action: str, port: int) -> dict:
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": f"不认识的操作 {action}"}
    if key == "console":
        if action != "restart":
            return {"ok": False, "error": "远见后台只能重启（停掉它这个页面就没了；要停在命令行 start.bat stop）"}
        return _restart_console(port)
    if key == "gateway":
        if action in ("stop", "restart"):
            killed = _kill(_script_procs(CN.GATEWAY), CN.GATEWAY_LOG)
            if action == "stop":
                return {"ok": True, "killed": killed}
            time.sleep(0.8)
        _starting["gateway"] = time.time()
        return CN.start_gateway()
    if key == "speech":
        if action in ("stop", "restart"):
            killed = _kill(_script_procs(SPEECH_PY), SPEECH_LOG)
            if action == "stop":
                return {"ok": True, "killed": killed}
            time.sleep(0.8)
        return _start_speech()
    if key == "website":
        if action in ("stop", "restart"):
            killed = _kill(_website_procs(), WEBSITE_LOG)
            _starting.pop("website", None)
            if action == "stop":
                return {"ok": True, "killed": killed}
            time.sleep(0.8)
        return _start_website()
    if key == "llm":
        if _lms_exe() is None:
            return {"ok": False, "error": "没装 LM Studio（或它的 lms 命令不在 ~/.lmstudio/bin）"}
        if action == "stop":
            return _llm_stop()
        return CO.start_llm()
    return {"ok": False, "error": f"没有服务 {key}"}


# ── 日志 ─────────────────────────────────────────────────────────────
LOGS = {"console": CONSOLE_LOG, "gateway": CN.GATEWAY_LOG, "speech": SPEECH_LOG, "website": WEBSITE_LOG}


def log(key: str, lines: int = 200, port: int | None = None) -> dict:
    if key == "llm":
        return {"key": key, "path": None, "lines": list(CO._start["log"])[-lines:], "running": CO._start["running"]}
    path = console_log(port) if key == "console" and port else LOGS.get(key)
    if not path:
        return {"key": key, "path": None, "lines": [], "error": "这个服务没有日志文件"}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 96 * 1024))
            text = fh.read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return {"key": key, "path": path, "lines": [], "missing": True}
    rows = text.splitlines()
    if size > 96 * 1024 and rows:
        rows = rows[1:]                              # 第一行多半被截了半截
    return {"key": key, "path": path, "lines": rows[-max(1, min(2000, lines)):], "size": size,
            "mtime": os.path.getmtime(path)}


# ── 环境自检 ─────────────────────────────────────────────────────────
_hlock = threading.Lock()
_hcache: dict = {"at": 0.0, "data": None}
HEALTH_TTL = 30.0
REQUIRED = ("numpy", "psutil", "fastapi", "uvicorn", "pydantic", "websockets", "httpx", "mpyq", "PIL")
OPTIONAL = ("scipy", "sklearn")                     # 参考大脑的自动调参器才用


def file_version(path: str) -> str | None:
    """Windows 文件版本号（Game.dll 1.27a = 1.27.0.52240）；没有版本资源 = None。"""
    v = ctypes.windll.version
    v.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    v.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    v.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = v.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not v.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr, n = ctypes.c_void_p(), wintypes.UINT()
    if not v.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) or not ptr.value:
        return None
    ffi = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents   # dwSignature, dwStrucVersion, dwFileVersionMS, dwFileVersionLS
    ms, ls = ffi[2], ffi[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_version(exe: str, *args: str) -> str | None:
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=8, creationflags=NOWIN)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def _runtime_check() -> dict:
    """注入 DLL + 启动器 + 命令行。有 native/ 源码 = 本机自己编的；没有 = 用随仓库发的 prebuilt/，核对是不是同一份。"""
    files = ("render_hook.dll", "war3_launch.exe", "war3_ui_command.exe")
    missing = [f for f in files if not os.path.isfile(os.path.join(C.BIN, f))]
    dll = os.path.join(C.BIN, "render_hook.dll")
    source = os.path.isfile(os.path.join(W.NATIVE, "build.ps1"))
    out = {"key": "runtime", "path": C.BIN, "value": None,
           "extra": {"mode": "source" if source else "prebuilt", "missing": missing}}
    if missing:
        return dict(out, status="error", code="runtime-missing")
    st = os.stat(dll)
    out["value"] = f"{st.st_size // 1024} KB · {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}"
    manifest = C.load_json(os.path.join(W.PREBUILT, "manifest.json"), None)
    if source or not manifest:
        return dict(out, status="ok", code="runtime-source" if source else "runtime-ok")
    diff = [f for f in files if (manifest.get("files") or {}).get(f, {}).get("sha256") not in (None, _sha256(os.path.join(C.BIN, f)))]
    out["extra"]["version"] = manifest.get("version")
    out["extra"]["diff"] = diff
    return dict(out, status="warn" if diff else "ok", code="runtime-stale" if diff else "runtime-prebuilt")


def _checks() -> list[dict]:
    rows: list[dict] = []

    def add(key, status, value=None, code=None, path=None, **extra):
        rows.append({"key": key, "status": status, "value": value, "code": code or f"{key}-{status}", "path": path,
                     "extra": extra or None})

    add("os", "ok" if platform.machine().endswith("64") else "error", f"Windows {platform.release()} ({platform.version()})")
    pv = sys.version_info
    add("python", "ok" if pv >= (3, 11) else "error", platform.python_version(), path=sys.executable)
    miss = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    miss_opt = [m for m in OPTIONAL if importlib.util.find_spec(m) is None]
    add("packages", "error" if miss else "warn" if miss_opt else "ok", None, missing=miss, optional=miss_opt)
    # Windows 自带的 PowerShell 5.1：后台起游戏和服务（WMI）要它。09-26 起不再需要 PowerShell 7（farm 改成了 Python）
    ps = W.powershell_exe()
    add("powershell", "ok" if os.path.isfile(ps) else "error", "5.1" if os.path.isfile(ps) else None, path=ps)
    r = _runtime_check()
    rows.append(r)

    gd = W.game_dir()
    exe, gdll = os.path.join(gd, "War3.exe"), os.path.join(gd, "Game.dll")
    if not gd:
        add("game", "error", None, code="game-unset")          # 新装还没设：控制中心顶上有设置卡片
    elif not (os.path.isfile(exe) and os.path.isfile(gdll)):
        add("game", "error", None, code="game-missing", path=gd)
    else:
        ver = file_version(gdll)
        good = ver in W.SUPPORTED_GAME_VERSIONS
        add("game", "ok" if good else "error", ver, code="game-ok" if good else "game-version", path=gd, want=W.GAME_VERSION_LABEL)
    mp = W.default_map()
    if gd:
        add("map", "ok" if os.path.isfile(mp) else "warn", os.path.basename(mp), path=mp)
    need = [f for f in ("units.json", "names.json", "skills.json") if not os.path.isfile(W.game_data(f))]
    add("gamedata", "error" if need else "ok", None, path=W.GAME_DATA, missing=need)
    amai = os.path.join(W.STRATEGY, "brain", "amai_data", "human.json")
    add("amai", "ok" if os.path.isfile(amai) else "warn", None, path=os.path.dirname(amai))

    # 网页：dist/ 随仓库发，按源码指纹（webstamp）判断是不是这份源码编的 —— 对得上就不用 Node.js
    ws = WS.check()
    if ws["state"] == "missing":
        add("web", "error", None, code="web-missing", path=C.WEB_DIST)
    else:
        built = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(os.path.join(C.WEB_DIST, "index.html"))))
        add("web", "ok" if ws["state"] == "ok" else "warn", built,
            code="web-ok" if ws["state"] == "ok" else "web-stale", path=C.WEB_DIST)
    node = W.node_exe()
    add("node", "ok" if node else "info", _run_version(node, "--version") if node else None, path=node)
    lms = _lms_exe()
    add("lmstudio", "ok" if lms else "info", None, path=lms)
    try:
        du = shutil.disk_usage(C.ROOT)
        pct = du.free / du.total * 100
        add("disk", "error" if pct < 5 else "warn" if pct < 12 else "ok", f"{du.free / 2**30:.0f} GB",
            path=os.path.splitdrive(C.ROOT)[0], pct=round(pct, 1))
    except OSError:
        pass
    return rows


def health(refresh: bool = False) -> dict:
    """缓存 30 秒（要起 node --version、算 DLL 的哈希）；两个请求同时来只查一遍。"""
    with _hlock:
        if not refresh and _hcache["data"] is not None and time.time() - _hcache["at"] < HEALTH_TTL:
            return _hcache["data"]
        rows = _checks()
        summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warn", "error", "info")}
        data = {"checks": rows, "summary": summary, "at": time.time(), "root": C.ROOT,
                "deploy": C.load_json(STATE_FILE, None), "config": W.CONFIG if os.path.isfile(W.CONFIG) else None}
        _hcache.update(at=time.time(), data=data)
        return data

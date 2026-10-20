# -*- coding: utf-8 -*-
"""Farsight 服务端（FastAPI）。只监听 127.0.0.1。

REST（全部 JSON）：
  GET  /api/meta                         名字 / 订单名 / 兵种数值 / 技能名和放法
  GET  /api/instances                    所有实例的进程状态、当前对局、下一局设置
  POST /api/instances/{n}/{action}       farm-start / stop / brain-start / brain-stop / end-game
  PUT  /api/instances/{n}/next-game      下一局：地图 / 我方种族 / 对手种族 / 难度 / 倍速
  GET  /api/maps  GET /api/maps/info?path=  GET /api/maps/preview/{file}
  GET  /api/i/{n}/unit/{addr}?type=      单位详情（订单、任务、魔法、英雄等级经验、技能冷却、背包）
  POST /api/i/{n}/command                手动下令（优先级 manual 95 的租约）
  GET  /api/i/{n}/leases                 租约表
  GET  /api/i/{n}/cam   POST /api/i/{n}/cam/{op}   POST /api/i/{n}/director/{action}
  GET  /api/i/{n}/brain                  brain_live.json
  GET  /api/brain/schema   GET|PUT /api/i/{n}/brain/config
  GET  /api/i/{n}/logs   GET /api/i/{n}/logs/{key}?lines=
  GET  /api/i/{n}/matches   GET /api/i/{n}/matches/{name}   GET /api/matches?limit=（所有实例）   GET /api/kernels
  GET  /api/matches/overview?scope=&inst=&limit=&kind=&pi=&pk=&po=&q=&oc=&offset=&size=   对局记录页：统计 + 分组 + 一页对局（后端算）
  GET  /api/disk?refresh=   数据与磁盘：各类数据占多少、按实例、24 小时增量、磁盘剩余（disk.py）
  POST /api/tests/start {insts, hours}   POST /api/instances/stop-all   POST /api/instances/{n}/{test-start|kill-proc|issue-toggle|farm-start|stop|brain-start|brain-stop|end-game|kill-game}
  GET|POST /api/issues   PUT|DELETE /api/issues/{id}   GET /api/issues/{id}/context   GET /api/i/{n}/matches/{name}/around?t=&w=   问题备忘（issues.py）
  GET  /api/i/{n}/stream[?match=&tick=]   GET /api/i/{n}/stream/meta   直播页（/log）的滚动条目 / 顶栏简报（stream.py）
  GET|PUT /api/settings
  GET  /api/coach   POST /api/coach/llm-start   运营顾问（本地大模型）：模型服务在不在 + 每个实例的顾问状态（coach.py）
  GET  /api/i/{n}/decisions?limit=&unit=&src=&q=&since=&offset=   战斗决策明细（offset：只读上次之后新写的）（war3-modules/declog.py 写的 decisions.jsonl）
  GET  /api/i/{n}/events?since=&limit=   事件流 + 屏幕消息 + 输入状态 + 本机选中（eventsview.py）
  GET  /api/connect   POST /api/connect/gateway/start   网关状态 / 起网关、MCP 挂接信息（connectview.py）
  GET  /api/services   POST /api/services/{key}/{start|stop|restart}   GET /api/services/{key}/log?lines=
  GET  /api/health?refresh=   首页「控制中心」：各服务状态和起停、日志、环境自检（services.py）
  POST /api/system/stop {llm, console}   彻底停止：实例 + 服务 + 模型，最后远见后台自己退出（同 start.bat stop）
  GET  /api/speech-overview?inst=   GET|POST /api/speech/{path}   头顶气泡页：转发给气泡服务 speak_api（speechview.py）
页面：/ 控制台（SPA）、/log 或 /live 直播日志页（web/live.html，OBS 浏览器源用）
WebSocket：
  /ws/i/{n}   推 snapshot（4 Hz，可调）、brain（变了才推）、status、log（订阅的日志源的新行）
  /ws/global  推 instances（3 秒）
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import common as C
from . import instances as I
from . import logs as L
from . import mapinfo as M
from . import matches as MT
from . import apm as APM
from . import stream as ST
from . import issues as ISS
from . import coach as CO
from . import schemes_view as SV
from . import jassview as JV
from . import canvasview as CV
from . import eventsview as EV
from . import connectview as CN
from . import disk as DK
from . import services as SVC
from . import speechview as SP
from . import gamedir as GD
from . import feedback as FB
from . import update as UP

if C.BRAIN not in sys.path:
    sys.path.insert(0, C.BRAIN)
from brain import config as BCFG  # noqa: E402
from brain import ops as BOPS  # noqa: E402

app = FastAPI(title="Farsight", docs_url="/api/docs", openapi_url="/api/openapi.json")


class LocalOnly:
    """远见只给这台电脑自己用（09-26 加，配合「反馈与建议」的安全策略）：
      * Host 必须是本机名（127.0.0.1 / localhost / ::1）—— 挡 DNS 重绑定：恶意网站把自己的域名解析到 127.0.0.1 再来调远见
      * 改东西的请求（POST / PUT / DELETE…）和 WebSocket 如果带了 Origin，Origin 也必须是本机 —— 挡跨站请求：
        你开着的任何网页都能往 http://127.0.0.1:8866 发 POST（全部停止、提交反馈、起停服务……），浏览器会带上它自己的 Origin
    本机的脚本 / curl / start.bat 不带 Origin，照常能用；远见自己的页面 Origin 就是本机，不受影响。"""
    LOCAL = ("127.0.0.1", "localhost", "::1", "testserver")
    SAFE = ("GET", "HEAD", "OPTIONS")

    def __init__(self, inner):
        self.inner = inner

    @classmethod
    def _host_ok(cls, value: str) -> bool:
        v = value.strip().lower()
        host = v[1:v.index("]")] if v.startswith("[") and "]" in v else v.rsplit(":", 1)[0] if v.count(":") == 1 else v
        return host in cls.LOCAL

    @classmethod
    def _origin_ok(cls, origin: str) -> bool:
        from urllib.parse import urlparse
        try:
            return (urlparse(origin).hostname or "").lower() in cls.LOCAL
        except ValueError:
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            origin = h.get("origin")
            unsafe = scope["type"] == "websocket" or scope.get("method", "GET") not in self.SAFE
            if not self._host_ok(h.get("host", "")) or (unsafe and origin and not self._origin_ok(origin)):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": "只接受本机的请求"}, status_code=403)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


app.add_middleware(LocalOnly)


# ── 实例 worker ──
class Worker:
    def __init__(self, n: int, hub: "Hub"):
        self.n, self.hub = n, hub
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.pending: dict[int, Future] = {}
        self.seq = 0
        self.started_at = 0.0

    def ensure(self) -> None:
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            self.proc = subprocess.Popen([sys.executable, "-u", os.path.join(C.HERE, "worker.py"), str(self.n)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(C.FARSIGHT, "cache", f"worker{self.n}.err"), "a", encoding="utf-8"),
                                         cwd=C.HERE, env=env, text=True, encoding="utf-8", bufsize=1, creationflags=0x08000000)
            self.started_at = time.time()
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                fut = self.pending.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif "event" in msg:
                self.hub.from_worker(msg)
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_result({"ok": False, "error": "worker 退出了"})
        self.pending.clear()

    async def call(self, op: str, args: dict | None = None, timeout: float = 20.0):
        self.ensure()
        with self.lock:
            self.seq += 1
            rid = self.seq
            fut: Future = Future()
            self.pending[rid] = fut
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "op": op, "args": args or {}}, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.pending.pop(rid, None)
                raise HTTPException(503, f"worker 写不进去：{exc}")
        try:
            msg = await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
        except asyncio.TimeoutError:
            self.pending.pop(rid, None)
            raise HTTPException(504, f"{op} 超时")
        if not msg.get("ok"):
            raise HTTPException(502, msg.get("error") or "worker 出错")
        return msg.get("result")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class Hub:
    """一个实例的所有 WebSocket 订阅者 + 最新快照 + 大脑状态 + 日志跟随。"""

    def __init__(self, n: int):
        self.n = n
        self.worker = Worker(n, self)
        self.clients: set["Client"] = set()
        self.snapshot = None
        self.status = None
        self.brain = None
        self.brain_mtime = 0.0
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.last_client = time.time()

    def from_worker(self, msg: dict) -> None:
        ev, data = msg["event"], msg.get("data")
        if ev == "snapshot":
            self.snapshot = data
        elif ev == "status":
            self.status = data
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._fanout, ev, data)

    def _fanout(self, ev: str, data) -> None:
        for c in list(self.clients):
            c.offer(ev, data)

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.worker.ensure()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._poll())

    async def _poll(self) -> None:
        path = os.path.join(C.inst_dir(self.n), "brain_live.json")
        while True:
            try:
                m = os.path.getmtime(path) if os.path.exists(path) else 0.0
                if m and m != self.brain_mtime:
                    self.brain_mtime = m
                    data = C.load_json(path, None)
                    if data is not None:
                        self.brain = data
                        self._fanout("brain", data)
                for c in list(self.clients):
                    for key, off in list(c.logs.items()):
                        rows, new = await asyncio.to_thread(L.read_since, self.n, key, off)
                        c.logs[key] = new
                        if rows:
                            c.offer("log", {"key": key, "lines": rows[-500:]})
            except Exception as exc:  # noqa: BLE001
                print("[hub]", self.n, exc, file=sys.stderr)
            await asyncio.sleep(0.7)


HUBS: dict[int, Hub] = {}


def hub(n: int) -> Hub:
    if n not in HUBS:
        HUBS[n] = Hub(n)
    h = HUBS[n]
    if h.loop is None:
        try:
            h.start()
        except RuntimeError:
            pass
    else:
        h.worker.ensure()
    return h


class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.hz = 4.0
        self.last_snap = 0.0
        self.logs: dict[str, int] = {}

    def offer(self, ev: str, data) -> None:
        if ev == "snapshot":
            if self.hz <= 0:                         # 暂停（网页标签页切到后台）：一帧都不推
                return
            now = time.monotonic()
            if now - self.last_snap < 1.0 / max(0.2, self.hz):
                return
            self.last_snap = now
        try:
            self.queue.put_nowait({"type": ev, "data": data})
        except asyncio.QueueFull:
            if ev != "snapshot":                     # 快照丢了无所谓，下一帧就来；别的要尽量送到
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait({"type": ev, "data": data})
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


@app.websocket("/ws/i/{n}")
async def ws_instance(ws: WebSocket, n: int):
    await ws.accept()
    h = hub(n)
    h.start()
    c = Client(ws)
    h.clients.add(c)
    await ws.send_json({"type": "hello", "data": {"inst": n, "status": h.status, "brain": h.brain, "snapshot": h.snapshot}})

    async def pump():
        while True:
            msg = await c.queue.get()
            await ws.send_text(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    sender = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "rate":
                # 09-26 多标签页：网页按「标签页在不在前台、这页要不要实时画面」要速率 —— 后台 0（暂停）、
                # 非战场页 1 帧/秒（只给顶栏的资源数）。从暂停恢复时马上补一帧缓存的，不用等下一帧
                was = c.hz
                c.hz = max(0.0, min(20.0, float(msg.get("hz", 4))))
                if was <= 0 < c.hz and h.snapshot is not None:
                    c.last_snap = time.monotonic()
                    try:
                        c.queue.put_nowait({"type": "snapshot", "data": h.snapshot})
                    except asyncio.QueueFull:
                        pass
            elif t == "logs":
                keys = [k for k in msg.get("keys", []) if L.path_of(n, k)]
                c.logs = {k: c.logs.get(k, os.path.getsize(L.path_of(n, k)) if os.path.exists(L.path_of(n, k)) else 0) for k in keys}
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        print("[ws]", exc, file=sys.stderr)
    finally:
        sender.cancel()
        h.clients.discard(c)


class GlobalFeed:
    """/ws/global 的共用广播：实例列表 + 问题备忘待填写数，每 3 秒**算一次**发给所有连着的标签页。
    ⚠ 09-26 以前每个连接各算各的：开 5 个标签页就是每 3 秒 5 遍 I.status()（扫进程表、读每个实例的文件）。
    没有标签页连着时停下来；新连上的先拿最近一份，不用等下一轮。"""

    def __init__(self):
        self.socks: set[WebSocket] = set()
        self.last: list[str] = []
        self.task: asyncio.Task | None = None

    async def join(self, ws: WebSocket) -> None:
        self.socks.add(ws)
        for text in self.last:
            await ws.send_text(text)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self.socks:
            texts = []
            try:
                data = await asyncio.to_thread(I.status)
                texts.append(json.dumps({"type": "instances", "data": data}, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                print("[global]", exc, file=sys.stderr)
            # 问题备忘的「待填写」数：侧栏角标；游戏里按了快捷键，网页 3 秒内弹提示（web/src/lib/live.ts）
            try:
                pend = await asyncio.to_thread(ISS.pending)
                texts.append(json.dumps({"type": "issues", "pending": pend}))
            except Exception as exc:  # noqa: BLE001
                print("[issues]", exc, file=sys.stderr)
            if texts:
                self.last = texts

            async def send(ws: WebSocket) -> None:
                try:
                    for text in texts:
                        await asyncio.wait_for(ws.send_text(text), 5.0)
                except Exception:  # noqa: BLE001  断了 / 卡住的连接收掉，别拖累别的标签页
                    self.socks.discard(ws)
            await asyncio.gather(*(send(ws) for ws in list(self.socks)))
            await asyncio.sleep(3.0)


GLOBAL = GlobalFeed()


@app.websocket("/ws/global")
async def ws_global(ws: WebSocket):
    await ws.accept()
    try:
        await GLOBAL.join(ws)
        while True:
            await ws.receive_text()                  # 网页不发东西；等它断开
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        GLOBAL.socks.discard(ws)


# ── REST ──
_META: dict = {}          # 语言 -> meta（名字表按界面语言给，见 C.meta）
RACES_EN = {1: "Human", 2: "Orc", 3: "Undead", 4: "Night Elf"}
DIFFICULTY_EN = {2: "Easy", 3: "Normal", 4: "Insane"}


@app.get("/api/meta")
def api_meta(lang: str = "zh"):
    lang = "en" if lang == "en" else "zh"
    if lang not in _META:
        m = C.meta(lang)
        m["races"] = RACES_EN if lang == "en" else I.RACES
        m["difficulty"] = DIFFICULTY_EN if lang == "en" else I.DIFFICULTY
        _META[lang] = m
    return dict(_META[lang], webVer=_web_ver())


def _web_ver() -> float:
    """前端构建产物的时间戳：页面每 20 秒问一次，变了就在顶部亮横幅（改完页面不用手动 Ctrl+F5）。"""
    try:
        return round(os.path.getmtime(os.path.join(C.WEB_DIST, "index.html")), 1)
    except OSError:
        return 0.0


@app.get("/api/version")
def api_version():
    """前端版本号：页面每 20 秒问一次（09-26 以前问的是整份 /api/meta，一次 196 KB）。
    app / root：start.bat（console/launch.py）按它认端口上跑的是不是**这份**仓库的远见 —— 旧仓库的远见也默认 8866。"""
    return {"webVer": _web_ver(), "app": "openwar3-farsight", "root": C.ROOT}


@app.get("/api/instances")
def api_instances():
    return I.status()


# ── 导演台（每实例开关，09-19）──────────────────────────────────────────────
# 需求：开局时能按实例勾选要不要带导演台（通常只让一个游戏进程带），勾上的实例每局自动附加运镜、敌军红色血条、
#        随镜头缩放的血条长度等功能。
# 开关记在 inst<N>/next_game.json 的 director。开着就保证有一个独立导播进程 war3-analytics/director_inst.py
# （导演台那条线的代码：运镜 + 头顶血条看门线程，每局换游戏自动跟上）。导播台页「启动」托管在 worker 里的那份没有血条看门线程、
# 网页后台一重启就没了 —— 开关开着时把它停掉、清锁，交给独立进程。

async def _console_director_stop(n: int) -> bool:
    """这个实例的导播锁是不是 Farsight 自己的 worker（导播台页「启动」的那份）：是就停掉它、清锁。"""
    holder = I.director_lock(n)
    h = HUBS.get(n)
    if not holder or not h or not h.worker.proc or h.worker.proc.pid != holder:
        return False
    try:
        await h.worker.call("director", {"action": "stop"}, timeout=10.0)
    except Exception as exc:  # noqa: BLE001
        print("[director] 停控制台导播出错", n, exc, file=sys.stderr)
    I.clear_director_lock(n)
    return True


async def ensure_director(n: int) -> dict:
    st = (await asyncio.to_thread(I.status, n))[0]
    if st["director"]:
        return {"ok": True, "note": "导播进程已经在跑"}
    took_over = await _console_director_stop(n)
    holder = I.director_lock(n)
    if holder and not took_over:
        import psutil
        if psutil.pid_exists(holder):
            return {"ok": False, "error": f"导播锁被 pid {holder} 占着（不是这个网页后台的），没动它"}
        I.clear_director_lock(n)
    r = await asyncio.to_thread(I.start_director, n)
    return dict(r, tookOver=took_over)


async def director_keeper() -> None:
    """每 10 秒：导演台开关开着、实例在跑（没挂停止牌）而没有独立导播的，补起来（导播意外退出、开关刚打开都靠它）。"""
    while True:
        await asyncio.sleep(10)
        try:
            for s in await asyncio.to_thread(I.status):
                running = s["game"]["alive"] or s["farm"]
                if s["nextGame"].get("director") and running and not s["held"] and not s["director"]:
                    r = await ensure_director(s["inst"])
                    print("[director] 看护补起", s["inst"], r, file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print("[director] 看护出错", exc, file=sys.stderr)


@app.post("/api/instances/{n}/director")
async def api_instance_director(n: int, body: dict = Body(...)):
    """导演台开关：{on: true/false}。开 = 记下开关并马上起独立导播（实例没在跑就等开始测试时起）；关 = 记下并停掉这个实例的导播。"""
    on = bool(body.get("on"))
    try:
        await asyncio.to_thread(I.write_next_game, n, {"director": on})
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    st = (await asyncio.to_thread(I.status, n))[0]
    if on:
        if st["game"]["alive"] or st["farm"]:
            return dict(await ensure_director(n), on=True)
        return {"ok": True, "on": True, "note": "实例没在跑，开始测试时带上"}
    await _console_director_stop(n)
    killed = await asyncio.to_thread(I.stop_director, n)
    return {"ok": True, "on": False, "killed": killed}


@app.post("/api/instances/stop-all")
async def api_stop_all():
    """全部停止：每个在跑的实例挂停止牌（不再自动重开/接管）、停 farm、AI、游戏、导播。"""
    r = await asyncio.to_thread(I.stop_all)
    for n in list(HUBS):
        await _console_director_stop(n)
    return r


@app.post("/api/tests/start")
async def api_tests_start(body: dict = Body(...)):
    """开始测试（一个或多个实例）：body = {insts: [5, 6], hours: 24}。每个 = farm 守护 + 新大脑，依次起、间隔 4 秒。
    start.bat（console/launch.py）带编号时走的也是这里。"""
    nums = [int(x) for x in body.get("insts") or []]
    if not nums or any(not 1 <= x <= 30 for x in nums):
        raise HTTPException(400, "实例号要在 1~30")
    director = body.get("director")          # True / False = 这几个实例的导演台开关设成开 / 关；不给 = 照各自原来的开关
    r = await asyncio.to_thread(I.start_many, nums, float(body.get("hours", 24)), 4.0,
                                None if director is None else bool(director))
    for x in r["results"]:
        if x.get("director"):
            x["directorStart"] = await ensure_director(x["inst"])
    return r


# ── AI 方案（09-24）：内置 / 我的 / 已安装，每个实例选一个，一键切换 ──────────────────────────
# 目标：别人设计好的 AI 方案可以下载到本地，不影响自己的方案，随时加载来跑测试、对照研究，在远见里直接切换。
from openwar3 import schemes as S  # noqa: E402


def _scheme_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except S.SchemeExists as exc:            # 网页按 409 问「要不要替换」
        raise HTTPException(409, str(exc))
    except S.SchemeError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/schemes")
def api_schemes():
    return SV.overview()


@app.get("/api/schemes/results")
def api_scheme_results(ref: str = Query(...)):
    return {"ref": ref, "rows": SV.recent_results(ref)}


@app.post("/api/schemes/import")
def api_scheme_import(body: dict = Body(...)):
    """导入别人的方案：body = {filename, data: base64(zip), replace: bool}（前端读文件转 base64，不依赖 multipart）。"""
    import base64
    try:
        raw = base64.b64decode(body.get("data") or "", validate=True)
    except ValueError:
        raise HTTPException(400, "文件内容不是 base64")
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(400, "文件太大（> 60 MB）")
    s = _scheme_call(S.install_zip, raw, bool(body.get("replace")))
    return {"ok": True, "scheme": s}


@app.get("/api/schemes/export")
def api_scheme_export(ref: str = Query(...)):
    from fastapi.responses import Response
    from urllib.parse import quote
    name, data = _scheme_call(S.export_zip, ref)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/api/schemes/fork")
def api_scheme_fork(body: dict = Body(...)):
    return {"ok": True, "scheme": _scheme_call(S.fork, body.get("ref", ""), body.get("id", ""), body.get("name"))}


@app.post("/api/schemes/trust")
def api_scheme_trust(body: dict = Body(...)):
    _scheme_call(S.trust, body.get("ref", ""))
    return {"ok": True}


@app.delete("/api/schemes")
def api_scheme_delete(ref: str = Query(...)):
    using = [i["inst"] for i in I.status() if i.get("schemeRunning") == ref or (i.get("nextGame") or {}).get("scheme") == ref]
    if using:
        raise HTTPException(400, f"实例 {'、'.join(map(str, using))} 在用这个方案，先给它们换一个")
    _scheme_call(S.delete, ref)
    return {"ok": True}


@app.post("/api/schemes/open-folder")
def api_scheme_open(body: dict = Body(...)):
    """在资源管理器里打开方案所在的文件夹（改「我的方案」代码用）。只在本机有意义。"""
    s = _scheme_call(S.get, body.get("ref", ""))
    try:
        os.startfile(s["dir"])  # type: ignore[attr-defined]
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "dir": s["dir"]}


@app.post("/api/instances/{n}/scheme")
async def api_instance_scheme(n: int, body: dict = Body(...)):
    """给实例换方案：{ref, now: true = 停掉当前 AI、新方案马上接管这一局 / false = 下一次开始测试生效}。"""
    ref = body.get("ref") or S.DEFAULT_REF
    return await asyncio.to_thread(_scheme_call, I.switch_scheme, n, ref, bool(body.get("now", True)),
                                   float(body.get("hours", 24)))


# ── JASS 控制台（09-24）：外部随时调游戏里的 JASS 能力（console/server/jassview.py）──
@app.get("/api/jass/natives")
def api_jass_natives(q: str = "", cat: str = "", lang: str = "zh"):
    return JV.natives(q, cat, lang)


@app.post("/api/instances/{n}/jass")
async def api_instance_jass(n: int, body: dict = Body(...)):
    """跑 JASS 小脚本：{"code": "call PingMinimapEx(...)\\nset t = CreateTextTag()"}（直接粘 JASS；变量在这个实例上一直记着）。"""
    return await asyncio.to_thread(JV.run, n, str(body.get("code") or ""))


@app.post("/api/instances/{n}/jass/call")
async def api_instance_jass_call(n: int, body: dict = Body(...)):
    """调一个 native：{"name": "CreateUnit", "args": [...]}；单位参数写 {"unit": 地址}。"""
    return await asyncio.to_thread(JV.call, n, str(body.get("name") or ""), list(body.get("args") or []))


@app.post("/api/instances/{n}/jass/reset")
async def api_instance_jass_reset(n: int):
    return JV.reset(n)


# ── 画板（09-25）：外部程序往游戏画面上画东西，运行时自绘、多人局也安全（console/server/canvasview.py）──
@app.get("/api/instances/{n}/canvas")
async def api_instance_canvas_status(n: int):
    return await asyncio.to_thread(CV.status, n)


@app.post("/api/instances/{n}/canvas")
async def api_instance_canvas(n: int, body: dict = Body(...)):
    """{"set": [{"key", "kind": text/panel/bar/image/circle/path, ...}], "remove": [key...], "clear": false}"""
    return await asyncio.to_thread(CV.update, n, body)


# ── 事件与输入（09-25）：这一局的事件流、屏幕消息 / 聊天、鼠标与悬停、本机选中（console/server/eventsview.py）──
@app.get("/api/i/{n}/events")
async def api_events(n: int, since: int = 0, limit: int = 400, noisy: bool = False):
    return await asyncio.to_thread(EV.events, n, since, limit, noisy)


# ── 接入与扩展（09-25）：网关状态 / 一键启动、MCP 挂接信息（console/server/connectview.py）──
@app.get("/api/connect")
async def api_connect():
    return await asyncio.to_thread(CN.info)


@app.post("/api/connect/gateway/start")
async def api_connect_gateway_start():
    return await asyncio.to_thread(CN.start_gateway)


# ── 控制中心（09-26）：各服务状态 / 起停 / 日志、环境自检（console/server/services.py）──
@app.get("/api/services")
async def api_services(request: Request):
    return await asyncio.to_thread(SVC.services, request.url.port or C.w3paths.port("console", 8866))


@app.post("/api/services/{key}/{action}")
async def api_service_action(key: str, action: str, request: Request):
    return await asyncio.to_thread(SVC.act, key, action, request.url.port or C.w3paths.port("console", 8866))


@app.get("/api/services/{key}/log")
async def api_service_log(key: str, request: Request, lines: int = 200):
    return await asyncio.to_thread(SVC.log, key, lines, request.url.port)


# ── 游戏目录（09-26）：玩家在控制中心 / 设置页自己设；版本不对给温馨提示（console/server/gamedir.py）──
@app.get("/api/gamedir")
async def api_gamedir():
    return await asyncio.to_thread(GD.status)


@app.get("/api/gamedir/detect")
async def api_gamedir_detect():
    return await asyncio.to_thread(GD.detect)


@app.get("/api/gamedir/check")
async def api_gamedir_check(path: str = ""):
    return await asyncio.to_thread(GD.check, path)


@app.post("/api/gamedir/browse")
async def api_gamedir_browse():
    return await asyncio.to_thread(GD.browse)


@app.post("/api/gamedir")
async def api_gamedir_save(body: dict = Body(...)):
    return await asyncio.to_thread(GD.save, str(body.get("path") or ""))


@app.post("/api/gamedir/extract")
async def api_gamedir_extract():
    return await asyncio.to_thread(GD.extract)


def _game_changed() -> None:
    """游戏目录换了 / 游戏数据重新提取完：名字表按新数据重读、地图按新目录重扫、环境自检马上重查。"""
    _META.clear()
    SVC._hcache["at"] = 0.0
    if not _MAPS_WARM.get("running"):
        threading.Thread(target=_warm_maps, daemon=True).start()


GD.on_changed.append(_game_changed)


# ── 新版本提醒（09-26，console/server/update.py）：启动时和每 6 小时问 war3ai.com/version.json ──
@app.get("/api/update")
def api_update():
    return UP.status()


@app.post("/api/update/check")
async def api_update_check():
    return await asyncio.to_thread(UP.check)


# ── 反馈与建议（09-26，console/server/feedback.py）：闭源运行时签名 + 本机识别码，发到 war3ai.com ──
@app.post("/api/feedback/preview")
async def api_feedback_preview(body: dict = Body(...)):
    try:
        return await asyncio.to_thread(FB.preview, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/feedback")
async def api_feedback(body: dict = Body(...)):
    try:
        return await asyncio.to_thread(FB.submit, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/feedback/history")
def api_feedback_history():
    return FB.history()


@app.post("/api/feedback/retry/{rid}")
async def api_feedback_retry(rid: str):
    return await asyncio.to_thread(FB.retry, rid)


@app.get("/api/speech-overview")
async def api_speech_overview(inst: int | None = None):
    """头顶气泡页的汇总（console/server/speechview.py）：服务在不在、模型、三个功能开没开、自动开启表。"""
    return await asyncio.to_thread(SP.overview, inst)


@app.api_route("/api/speech/{path:path}", methods=["GET", "POST"])
async def api_speech(path: str, request: Request):
    """转发给气泡服务 speak_api（只放行 speechview.ALLOWED）。和 AI 对话要等模型说完，给 90 秒。"""
    body = None
    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            body = {}
    timeout = 90.0 if path.strip("/") == "chat" else 15.0
    status, data = await asyncio.to_thread(SP.call, request.method, path, dict(request.query_params), body, timeout)
    return JSONResponse(data, status_code=status)


@app.get("/api/health")
async def api_health(refresh: int = 0):
    return await asyncio.to_thread(SVC.health, bool(refresh))


@app.post("/api/system/stop")
async def api_system_stop(body: dict = Body(default={})):
    """彻底停止（控制中心「全部停止」）：实例、网关、气泡、官网预览、漏网组件，llm=true 时卸载本系统用的模型；
    console=true 时回完这个请求远见后台自己也退出（页面会显示「已停止」，要再用就双击 start.bat）。命令行同款：start.bat stop。"""
    r = await asyncio.to_thread(SVC.stop_everything, bool(body.get("llm", True)))
    for n in list(HUBS):
        await _console_director_stop(n)
    if body.get("console", True):
        SVC.shutdown_console()
        r["console"] = "stopping"
    return r


@app.post("/api/instances/{n}/{action}")
async def api_instance_action(n: int, action: str, request: Request, body: dict = Body(default={})):
    if action in ("test-start", "farm-start"):
        # 游戏目录没设 / 版本不支持：直接回 409 + 提示（网页的「开始测试」按钮只认报错，不看 ok 字段）
        problem = await asyncio.to_thread(I.game_problem)
        if problem:
            raise HTTPException(409, problem)
    if action == "test-start":
        director = body.get("director")
        r = await asyncio.to_thread(I.start_test, n, float(body.get("hours", 24)), None if director is None else bool(director))
        if r.get("director"):
            r["directorStart"] = await ensure_director(n)
        return r
    if action == "kill-proc":
        return await asyncio.to_thread(I.kill_proc, n, int(body.get("pid") or 0))
    if action == "issue-toggle":
        # 等同在游戏里按一下问题快捷键：暂停并记一条 / 继续（DLL 动作 57；标记由新大脑监督器写）
        game = I.status(n)[0]["game"]
        if not game["alive"]:
            return {"ok": False, "error": "游戏没在跑"}
        return await hub(n).worker.call("issue_toggle")
    if action == "farm-start":
        return await asyncio.to_thread(I.start_farm, n, float(body.get("hours", 6)), body.get("speed"), body.get("difficulty"),
                                       body.get("race"), body.get("enemyRace"), body.get("map"))
    if action == "stop":
        r = await asyncio.to_thread(I.stop_instance, n, bool(body.get("keepFarm")), I.who_from(request))
        if not body.get("keepFarm") and await _console_director_stop(n):
            r.setdefault("notes", []).append("导播台页托管的导播也停了")
        return r
    if action == "brain-start":                     # 起 AI：按这个实例选的方案（参考大脑 / 玩家方案）
        return await asyncio.to_thread(I.start_ai, n, float(body.get("hours", 6)))
    if action == "brain-stop":
        killed = await asyncio.to_thread(I.stop_ai, n)
        return {"ok": True, "killed": killed, "note": "当前这局没有 AI 指挥了"}
    if action == "end-game":
        I.mark_operator_end(n, "console-end-game", I.who_from(request))
        game = I.status(n)[0]["game"]
        try:
            r = await _call_connected(n, "end_game")
        except HTTPException:
            # 结束令没发出去：撤掉刚写的 end_request.json，不然这局之后真正的结局会被记成「人工结束」
            I.clear_operator_end(n)
            raise
        ops = BOPS.load(C.inst_dir(n))
        if ops["kill_game_after_end"] and game["alive"] and game["pid"]:
            threading.Thread(target=_kill_if_lingering, args=(n, game["pid"], ops["kill_wait_s"]), daemon=True).start()
        return dict(r or {}, killFallback=bool(ops["kill_game_after_end"]))
    if action == "kill-game":
        I.mark_operator_end(n, "console-kill-game", I.who_from(request))
        game = I.status(n)[0]["game"]
        if not game["alive"] or not game["pid"]:
            return {"ok": False, "error": "游戏没在跑"}
        return {"ok": True, "result": await asyncio.to_thread(BOPS.kill_game, n, game["pid"])}
    raise HTTPException(404, f"不认识的动作 {action}")


async def _call_connected(n: int, op: str, args: dict | None = None, wait_s: float = 10.0):
    """worker 是第一次用到这个实例时才起的（没人打开过这个实例的页面、脚本直接调接口），刚起的几秒还没连上游戏：
    「还没连上游戏」就每 0.5 秒重试，最多 wait_s 秒。09-24 实测：脚本点「结束这一局」第一下 502。"""
    t_end = time.monotonic() + wait_s
    while True:
        try:
            return await hub(n).worker.call(op, args)
        except HTTPException as exc:
            if "还没连上游戏" not in str(exc.detail) or time.monotonic() >= t_end:
                raise
        await asyncio.sleep(0.5)


def _kill_if_lingering(n: int, pid: int, wait_s: float) -> None:
    """控制台「结束本局」之后：游戏进程 wait_s 秒还在（结束令在结算画面上没生效之类）就强制关（ops.kill_game_after_end）。"""
    import psutil
    t_end = time.monotonic() + wait_s
    while psutil.pid_exists(pid) and time.monotonic() < t_end:
        time.sleep(0.5)
    if psutil.pid_exists(pid):
        BOPS.kill_game(n, pid)


@app.get("/api/ops/schema")
def api_ops_schema():
    return BOPS.schema()


@app.get("/api/i/{n}/ops")
def api_ops(n: int):
    ov = BOPS.load_overrides(os.path.join(C.inst_dir(n), BOPS.FILE))
    return {"overrides": ov, "values": dict(BOPS.DEFAULTS, **ov)}


@app.put("/api/i/{n}/ops")
def api_ops_put(n: int, body: dict = Body(...)):
    """body = {key: value 或 null（null = 恢复默认）}；监督器 2 秒内、导播 3 秒内读到。"""
    try:
        return BOPS.save(C.inst_dir(n), body)
    except KeyError as exc:
        raise HTTPException(400, f"不认识的开关 {exc}")


@app.put("/api/instances/{n}/next-game")
def api_next_game(n: int, body: dict = Body(...)):
    if body.get("map"):
        full = M.resolve(body["map"])
        if not full or not os.path.exists(full):
            raise HTTPException(400, "地图不在游戏的 Maps 目录里")
        # 存相对 <游戏目录>\Maps 的路径（runtime/farm.py 按玩家设置的游戏目录补全）：换了游戏目录也不用重选地图
        body = dict(body, map=M.rel(full))
    try:
        return I.write_next_game(n, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


_MAPS_WARM = {"done": 0, "total": 0, "running": False}


def _warm_maps():
    _MAPS_WARM.update(running=True, done=0)
    ms = M.list_maps()                 # 全部地图（文件名没有 (N) 前缀的也读：人数要从地图里取）
    _MAPS_WARM["total"] = len(ms)
    for m in ms:
        try:
            M.info(m["path"])
        except Exception:  # noqa: BLE001
            pass
        _MAPS_WARM["done"] += 1
    _MAPS_WARM["running"] = False


@app.get("/api/maps")
def api_maps(all: bool = False):
    """<游戏目录>\\Maps 下的全部地图（所有子文件夹）。rel = 相对地图目录的路径（选图、下一局设置都用它）。
    ⚠ 09-26 以前人数只看文件名的 (N) 前缀，没有前缀的 144 张（Download 里下载的图、战役）一张都不显示，看上去像只读了 FrozenThrone。
    现在人数没有前缀就用地图里读出来的玩家位；all=0 时只去掉读不出玩家位的（坏图 / 还没读过的）。"""
    out = []
    for m in M.list_maps():
        key = M._cache_key(m["path"])
        cached = C.load_json(os.path.join(M.CACHE, key + ".json"), None)
        if cached:
            m.update({k: cached.get(k) for k in ("name", "author", "suggested", "preview", "previewSource", "tileset", "description",
                                                 "sha1", "parts", "aiScripts", "aiKind", "fixedSlots")})
            m["slots"] = len(cached.get("players") or [])
            # 每个位置是谁、什么种族（固定玩家设置的地图，比如 HvH = 人族 对 电脑人族，网页上照着写出来）
            m["seats"] = [{"type": p.get("type"), "race": p.get("race")} for p in cached.get("players") or []]
            if not m["players"] and m["slots"]:
                m["players"] = m["slots"]
        if not all and not m["players"]:
            continue
        m["rel"] = M.rel(m["path"])
        out.append(m)
    return {"maps": out, "warm": _MAPS_WARM, "root": M.maps_root()}


@app.get("/api/maps/info")
def api_map_info(path: str = Query(...)):
    """path 可以是相对 <游戏目录>\\Maps 的（下一局设置里存的），也可以是老的绝对路径。"""
    full = M.resolve(path)
    if not full or not os.path.exists(full):
        raise HTTPException(404, "没有这张地图")
    return dict(M.info(full), rel=M.rel(full))


@app.get("/api/maps/preview/{name}")
def api_map_preview(name: str):
    p = os.path.join(M.CACHE, os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/i/{n}/unit/{addr}")
async def api_unit(n: int, addr: int, type: str = ""):
    return await hub(n).worker.call("unit", {"addr": addr, "type": type})


@app.post("/api/i/{n}/command")
async def api_command(n: int, body: dict = Body(...)):
    body.setdefault("hold", settings().get("manualHoldSeconds", 8))
    return await hub(n).worker.call("command", body)


@app.get("/api/i/{n}/players")
async def api_players(n: int):
    """双方资源/人口/累计采集（全知信息，页面上有标注）。"""
    return await hub(n).worker.call("players", timeout=15)


@app.get("/api/i/{n}/leases")
async def api_leases(n: int):
    return await hub(n).worker.call("leases")


@app.get("/api/i/{n}/status")
async def api_status(n: int):
    return await hub(n).worker.call("status")


@app.get("/api/i/{n}/cam")
async def api_cam(n: int):
    return await hub(n).worker.call("cam_status", timeout=30)


@app.post("/api/i/{n}/cam/{op}")
async def api_cam_op(n: int, op: str, body: dict = Body(default={})):
    return await hub(n).worker.call("cam", {"op": op, "payload": body}, timeout=40)


@app.get("/api/i/{n}/director/shots")
async def api_director_shots(n: int, limit: int = 20, stamp: str = ""):
    return await hub(n).worker.call("director_shots", {"limit": limit, "stamp": stamp}, timeout=10)


@app.get("/api/i/{n}/director/stamps")
async def api_director_stamps(n: int):
    return await hub(n).worker.call("director_stamps", {}, timeout=10)


@app.post("/api/i/{n}/director/{action}")
async def api_director(n: int, action: str, body: dict = Body(default={})):
    return await hub(n).worker.call("director", {"action": action, "config": body.get("config"), "takeover": body.get("takeover")}, timeout=30)


@app.get("/api/coach")
def api_coach():
    return CO.overview()


@app.post("/api/coach/llm-start")
def api_coach_llm_start():
    return CO.start_llm()


@app.get("/api/i/{n}/decisions")
def api_decisions(n: int, limit: int = 400, unit: int | None = None, src: str | None = None,
                  q: str | None = None, since: float | None = None, offset: int | None = None):
    """战斗决策明细（毫秒层 + 大脑，declog.py）。unit = 血条编号（单位或目标是它都算）；src = 毫秒层 / 大脑；
    q = 在「决策 + 理由」里找这几个字；since = 只要这个墙钟之后的；最新的在最后。
    ★ 09-26 offset = 上次回的 offset：只读这之后新写的（以前页面每 1.5 秒让这里读 8 MB、解析 6000 条再筛出几条新的）。"""
    mod = C.MODDIR
    if mod not in sys.path:
        sys.path.append(mod)
    import declog
    path = os.path.join(C.inst_dir(n), "decisions.jsonl")
    if offset is not None:
        rows, new_offset = C.jsonl_from(path, offset)
    else:
        try:
            new_offset = os.path.getsize(path)     # 先记大小再读尾巴：中间新写的下次会再读到一遍，网页带 since 去重
        except OSError:
            new_offset = 0
        rows = declog.tail(path, limit=6000, max_bytes=8 * 1024 * 1024)

    def keep(r):
        if since is not None and r.get("wall", 0) <= since:
            return False
        if src and r.get("src") != src:
            return False
        if unit is not None and not (any(u.get("id") == unit for u in r.get("units", ()))
                                     or (r.get("target") or {}).get("id") == unit):
            return False
        if q and q not in (r.get("act", "") + r.get("why", "")):
            return False
        return True
    rows = [r for r in rows if keep(r)]
    return {"rows": rows[-max(1, min(limit, 3000)):], "exists": os.path.exists(path), "now": time.time(), "offset": new_offset}


@app.get("/api/i/{n}/brain")
def api_brain(n: int):
    return C.load_json(os.path.join(C.inst_dir(n), "brain_live.json"), None)


@app.get("/api/brain/schema")
def api_brain_schema():
    return BCFG.schema()


@app.get("/api/i/{n}/brain/config")
def api_brain_config(n: int):
    path = os.path.join(C.inst_dir(n), "brain_config.json")
    ov = BCFG.load_overrides(path)
    return {"overrides": ov, "values": dict(BCFG.DEFAULTS, **ov)}


@app.put("/api/i/{n}/brain/config")
def api_brain_config_put(n: int, body: dict = Body(...)):
    """body = {key: value 或 null（null = 恢复默认）}。只存和默认不同的项。"""
    path = os.path.join(C.inst_dir(n), "brain_config.json")
    ov = BCFG.load_overrides(path)
    for k, v in body.items():
        if k not in BCFG.DEFAULTS:
            raise HTTPException(400, f"不认识的配置 {k}")
        if v is None:
            ov.pop(k, None)
            continue
        v = BCFG.coerce(k, v)
        if v == BCFG.DEFAULTS[k]:
            ov.pop(k, None)
        else:
            ov[k] = v
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ov, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return {"overrides": ov, "values": dict(BCFG.DEFAULTS, **ov)}


@app.get("/api/i/{n}/logs")
def api_logs(n: int):
    return L.sources(n)


@app.get("/api/i/{n}/logs/{key}")
def api_log_tail(n: int, key: str, lines: int = 400):
    return L.tail(n, key, max(10, min(5000, lines)))


@app.get("/api/i/{n}/matches")
def api_matches(n: int, limit: int = 200):
    return MT.list_matches(n, limit)


@app.get("/api/matches")
def api_matches_all(limit: int = 600):
    """所有实例的对局（对局记录页「所有实例」），按开局时间新到旧，每条带 inst。"""
    return MT.list_all(max(1, min(3000, limit)))


@app.get("/api/disk")
def api_disk(refresh: int = 0):
    """数据与磁盘：各类数据（战斗录制、对局记录、决策明细、日志……）占多少、按实例分、最近 24 小时涨了多少、磁盘还剩多少。只读。"""
    return DK.usage(bool(refresh))


@app.post("/api/disk/open")
def api_disk_open(body: dict = Body(...)):
    """在资源管理器里打开统计里的一个目录 / 文件（只认仓库里的路径）。"""
    try:
        return {"ok": True, "path": DK.open_path(str(body.get("path") or ""))}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/matches/overview")
def api_matches_overview(scope: str = "inst", inst: int | None = None, limit: int = 300, kind: str = "brain",
                         pi: int | None = None, pk: int | None = None, po: str | None = None, q: str = "",
                         oc: str = "", offset: int = 0, size: int = 50):
    """对局记录页：统计、分组、筛选、分页都在后端算，网页只拿一页（09-26：以前「所有实例」把 600 局整份发过去，浏览器卡死）。
    scope = inst / all；limit = 最近多少局（0 = 全部）；kind = brain / old / all；pi / pk / po = 点选的实例 / 内核 / 开局谱；
    q = 搜索；oc = 网页按当前语言对上的结局码（逗号分隔）；offset / size = 分页。"""
    if scope == "inst" and inst is None:
        raise HTTPException(400, "scope=inst 要带 inst")
    return MT.overview(scope="all" if scope == "all" else "inst", inst=inst, limit=max(0, min(50000, limit)),
                       kind=kind if kind in ("brain", "old", "all") else "brain", pick_inst=pi, pick_kernel=pk,
                       pick_opening=po or None, q=q[:100], outcomes=tuple(x for x in oc.split(",") if x)[:20],
                       offset=max(0, offset), size=max(10, min(200, size)))


@app.get("/api/issues")
def api_issues():
    """问题备忘：全部问题（新到旧）+ 待填写数。每次都先把各实例新的快捷键标记收进来。"""
    return ISS.listing()


@app.post("/api/issues")
def api_issue_create(body: dict = Body(...)):
    try:
        return ISS.create(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/issues/{iid}")
def api_issue_update(iid: str, body: dict = Body(...)):
    try:
        return ISS.update(iid, body)
    except KeyError:
        raise HTTPException(404, "没有这条问题")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/issues/{iid}")
def api_issue_delete(iid: str):
    if not ISS.delete(iid):
        raise HTTPException(404, "没有这条问题")
    return {"ok": True}


@app.get("/api/issues/{iid}/context")
def api_issue_context(iid: str, window: float | None = None):
    try:
        return ISS.context_of(iid, window)
    except KeyError:
        raise HTTPException(404, "没有这条问题")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/i/{n}/matches/{name}/around")
def api_match_around(n: int, name: str, t: float | None = None, w: float = 30):
    """那一局 t（相对开局的游戏秒，不给 = 最新）前后 w 秒的 AI 动作 / 状态 / 敌情 / 监督器和毫秒层日志。"""
    try:
        return ISS.context(n, name, t, max(0.0, min(600.0, w)))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/kernels")
def api_kernels():
    """新大脑内核版本（动过大脑代码的提交，旧 -> 新）：对局记录页按版本汇总、鼠标悬停看这一版改了什么。"""
    return MT.kernels()


@app.get("/api/i/{n}/apm")
def api_apm(n: int, matches: int = 8):
    return APM.summary(n, max(1, min(30, matches)))


@app.get("/api/i/{n}/matches/{name}")
def api_match(n: int, name: str):
    try:
        return MT.detail(n, name)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc))


# ── 直播页（/log，OBS 浏览器源）：旧 8777/log 的新大脑版，数据怎么取见 stream.py ──
@app.get("/api/i/{n}/stream")
def api_stream(n: int, match: str = "", tick: int | None = None, limit: int = 80):
    return ST.stream(n, match or None, tick, max(10, min(300, limit)))


@app.get("/api/i/{n}/stream/meta")
def api_stream_meta(n: int, match: str = ""):
    return ST.meta(n, match or None)


DEFAULT_SETTINGS = {"manualHoldSeconds": 8, "snapshotHz": 4}


def settings() -> dict:
    return dict(DEFAULT_SETTINGS, **(C.load_json(C.SETTINGS, {}) or {}))


@app.get("/api/settings")
def api_settings():
    return settings()


@app.put("/api/settings")
def api_settings_put(body: dict = Body(...)):
    cur = settings()
    for k, v in body.items():
        if k in DEFAULT_SETTINGS:
            cur[k] = v
    with open(C.SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=1)
    return cur


@app.on_event("startup")
async def _startup():
    os.makedirs(os.path.join(C.FARSIGHT, "cache"), exist_ok=True)
    threading.Thread(target=_warm_maps, daemon=True).start()
    UP.start()                                       # 问 war3ai.com 有没有新版本（3 秒后第一次，之后每 6 小时）
    # 全机进程表第一次扫要 12~20 秒（psutil 冷启动，这台机器 600 多个进程）：启动时后台先扫一遍，别让第一个请求等（09-24）
    threading.Thread(target=I._procs, daemon=True).start()
    # 对局记录「所有实例」第一次要把最近 600 局整份解析一遍（单独约 1 秒，启动时和预热地图抢 CPU 能拖到 20 秒）—— 后台解析进缓存。
    # 推迟 30 秒：09-19 实测刚启动的前半分钟请求时快时慢（10 秒才第一次应答），别再往启动那一段里挤
    threading.Thread(target=lambda: (time.sleep(30), MT.list_all(600)), daemon=True).start()
    asyncio.get_running_loop().create_task(director_keeper())      # 导演台开关开着的实例：保证有导播（09-19）


@app.on_event("shutdown")
async def _shutdown():
    for h in HUBS.values():
        h.worker.stop()


# ── 前端 ──
if os.path.isdir(C.WEB_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(C.WEB_DIST, "assets")), name="assets")


@app.get("/log")
@app.get("/live")
def live_page():
    """直播日志页：独立入口（web/live.html），不带控制台的侧栏/顶栏。?inst=5 看哪个实例，?match=局名&tick=拍 回看。"""
    p = os.path.join(C.WEB_DIST, "live.html")
    if os.path.exists(p):
        return FileResponse(p, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "直播页还没构建：cd console/web && npm run build"}, 503)


@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = os.path.join(C.WEB_DIST, path)
    if path and os.path.isfile(f):
        return FileResponse(f)
    index = os.path.join(C.WEB_DIST, "index.html")
    if os.path.exists(index):
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "前端还没构建：cd console/web && npm run build"}, 503)

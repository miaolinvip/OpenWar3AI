# -*- coding: utf-8 -*-
"""OpenWar3 网关（09-25，W3P 第 3 档）：把快车道 + 推送状态包成 WebSocket / JSON —— 任何语言、浏览器、远程机器、大模型都能接。

    python gateway/server.py                      # 127.0.0.1:8870（端口在 openwar3.json 的 ports.gateway）
    python gateway/server.py --host 0.0.0.0       # 给局域网别的机器用：自动要求令牌（bin/gateway/token.txt）

协议（全部 JSON，详见 docs/GATEWAY_ZH.md）：
    ws://127.0.0.1:8870/ws?inst=9&role=dev                 # role：dev（本机工具，全部）/ player（&player=N，能下令，默认公平模式）/ observer（只读）
    <- {"type": "hello", "protocol": 1, "role": "dev", "pid": 1234, "methods": [...]}
    -> {"id": 1, "op": "call", "method": "units", "args": ["me"]}
    <- {"id": 1, "ok": true, "result": [{"addr": ..., "type": "hpea", ...}]}
    -> {"id": 2, "op": "call", "method": "move", "args": [[{"unit": 123456}], 100.0, 200.0]}   # 单位写 {"unit": 地址}
    -> {"id": 3, "op": "subscribe", "state": {"hz": 4, "units": "mine"}, "events": true}
    <- {"type": "state", "state": {...}}      <- {"type": "events", "events": [{"kind": "ui.click", "key": "shop", ...}]}
    -> {"id": 4, "op": "jass", "code": "call PingMinimapEx(0, 0, 3, 255, 0, 0, false)"}      # 只给 dev
    -> {"id": 5, "op": "overview"}                                                         # 一页局面（给大模型）
HTTP：GET / （说明）  GET /api?role=player（方法目录）  POST /call {"inst", "role", "method", "args", "kwargs"}（一次性调用）。
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import ipaddress
import json
import mimetypes
import os
import secrets
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sdk", "python"))
import w3paths  # noqa: E402

w3paths.setup()
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from openwar3 import Game  # noqa: E402
from openwar3 import remote as R  # noqa: E402

PROTOCOL = 1
TOKEN_FILE = os.path.join(w3paths.BIN, "gateway", "token.txt")
app = FastAPI(title="OpenWar3 gateway")
CONFIG = {"require_token": False, "token": "", "check_host": False, "port": 8870, "origins": set()}
MAX_SESSIONS = 12                 # 运行时一共 16 条快车道：给 bot / 模组 / 远见留几条
HTTP_IDLE_S = 600.0               # HTTP /call 的会话空闲这么久就收掉（放车道）
_SESSIONS: set = set()
# 客户端和演示页：浏览器不让 file:// 的页面 import 模块，所以由网关自己发（http://127.0.0.1:8870/demo）。
# Windows 的类型表里没有 .mjs：不补就发成 text/plain，浏览器拒绝当模块载入（09-25 实测）
mimetypes.add_type("text/javascript", ".mjs")
app.mount("/clients", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "clients")), name="clients")


@app.get("/demo")
def demo():
    return RedirectResponse("/clients/js/demo.html")


def load_token() -> str:
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    try:
        t = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_hex(16)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(t)
    return t


def token_ok(given: str | None) -> bool:
    return not CONFIG["require_token"] or secrets.compare_digest((given or "").encode(), CONFIG["token"].encode())


def origin_ok(headers) -> str | None:
    """★ 09-26 审查：浏览器里任何网页都能 new WebSocket("ws://127.0.0.1:8870/ws") —— 本机默认又不要令牌，
    随便一个网站就能以 dev 身份跑 JASS、结束游戏。浏览器连 WebSocket / 发跨站请求一定带 Origin：
    没带的（Python / Node / curl 这类程序）放行；带了的只认网关自己的页面（演示页）和 --allow-origin 给的。
    本机模式再核对 Host（挡 DNS 重绑定：外站域名解析到 127.0.0.1 时 Host 是那个域名）。返回拒绝原因，None = 放行。"""
    port = CONFIG["port"]
    if CONFIG["check_host"]:
        host = (headers.get("host") or "").lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            return f"Host {host!r} 不是本机网关地址"
    origin = headers.get("origin")
    if not origin:
        return None
    o = origin.lower().rstrip("/")
    own = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
    if o in own or o in CONFIG["origins"]:
        return None
    return f"来源网页 {origin} 不在允许名单里（只认网关自己的演示页；别的页面启动时加 --allow-origin {origin}）"


class Session:
    """一个连接 = 一个 Game（一条快车道），所有调用在同一个工作线程里跑（Game 不是线程安全的）。"""

    def __init__(self, inst: int | None, pid: int | None, role: str, player: int | None, fair: bool):
        if role not in R.ROLES:
            raise R.RemoteError(f"role 只能是 {'/'.join(R.ROLES)}")
        if role == "player" and player is None:
            raise R.RemoteError("role=player 要带 player=玩家号")
        if len(_SESSIONS) >= MAX_SESSIONS:
            raise R.RemoteError(f"网关同时最多 {MAX_SESSIONS} 个会话（运行时一共 16 条快车道，要给 bot / 模组留几条）")
        self.role = role
        self.player = player if role == "player" else None
        self.last_used = time.monotonic()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ow3-session")
        self.g: Game = self.pool.submit(lambda: Game(inst=inst, pid=pid, player=self.player,
                                                     fair=fair if role == "player" else False,
                                                     observer=role == "observer")).result()
        _SESSIONS.add(self)

    async def run(self, fn, *a):
        self.last_used = time.monotonic()
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *a)

    def call(self, method: str, args, kwargs):
        return R.call(self.g, method, self.role, args, kwargs)

    def jass(self, code: str):
        if self.role != "dev":
            raise R.RemoteError("JASS 脚本只给 dev 角色")
        from openwar3.jassrun import Script
        return R.to_json(Script(self.g).run(code))

    def state(self, units: str, detail: bool):
        return R.state_view(self.g.snapshot(max_age=0), units, detail, only_player=self.player if self.g.fair else None)

    def events(self):
        return [R.event_view(e) for e in self.g.events()]

    def close(self):
        """断开：收掉这个会话画的东西和热键（别人的不动）、还车道、放映射。阻塞最多几秒，别在事件循环里直接调。"""
        _SESSIONS.discard(self)
        try:
            self.pool.submit(self.g.close).result(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self.pool.shutdown(wait=False)


@app.get("/")
def index():
    return {"name": "OpenWar3 gateway", "protocol": PROTOCOL, "ws": "/ws?inst=<N>&role=dev|player|observer",
            "roles": {k: sorted(v) for k, v in R.ROLE_CATEGORIES.items()},
            "token": "required" if CONFIG["require_token"] else "not required (loopback)", "docs": "docs/GATEWAY_ZH.md"}


@app.get("/api")
def api(role: str = "dev"):
    if role not in R.ROLES:
        return JSONResponse({"ok": False, "error": f"role 只能是 {'/'.join(R.ROLES)}"}, status_code=400)
    return {"role": role, "methods": [{k: e[k] for k in ("name", "category", "status", "signature", "doc")}
                                      for e in R.catalog(role)]}


_HTTP_SESSIONS: dict = {}
_HTTP_LOCK = asyncio.Lock()


def _resolve_pid(inst, pid) -> int | None:
    if pid:
        return int(pid)
    from openwar3.game import _find_pid
    return _find_pid(int(inst)) if inst is not None else None


async def _http_session(body: dict) -> Session:
    """HTTP /call 复用的会话：按「真实 pid + 角色」建（以前按实例号，换局以后一直指着旧 pid）；
    建的时候加锁（两个并发请求以前会各建一个、漏一条车道）；空闲太久的顺手收掉。"""
    role, player, fair = body.get("role", "dev"), body.get("player"), bool(body.get("fair", True))
    pid = await asyncio.to_thread(_resolve_pid, body.get("inst"), body.get("pid"))
    key = (pid, role, player, fair)
    async with _HTTP_LOCK:
        now = time.monotonic()
        for k, old in list(_HTTP_SESSIONS.items()):
            if k != key and (now - old.last_used > HTTP_IDLE_S or (pid and k[0] != pid and k[1:] == key[1:])):
                _HTTP_SESSIONS.pop(k, None)
                await asyncio.to_thread(old.close)
        s = _HTTP_SESSIONS.get(key)
        if s is None:
            s = _HTTP_SESSIONS[key] = await asyncio.to_thread(Session, None, pid, role, player, fair)
        return s


@app.post("/call")
async def http_call(body: dict, request: Request):
    bad = origin_ok(request.headers)
    if bad:
        return JSONResponse({"ok": False, "error": bad}, status_code=403)
    if not token_ok(body.get("token")):
        return JSONResponse({"ok": False, "error": "令牌不对（bin/gateway/token.txt）"}, status_code=401)
    try:
        s = await _http_session(body)
        result = await s.run(s.call, str(body["method"]), body.get("args"), body.get("kwargs"))
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=400)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    q = sock.query_params
    bad = origin_ok(sock.headers)
    if bad:
        await sock.close(code=4403, reason=bad[:120])     # 握手阶段直接拒（浏览器里的外站页面连不上）
        return
    await sock.accept()
    if not token_ok(q.get("token")):
        await sock.send_json({"type": "error", "error": "令牌不对（bin/gateway/token.txt；?token=...）"})
        await sock.close(code=4401)
        return
    role = q.get("role", "dev")
    try:
        s = await asyncio.to_thread(Session, int(q["inst"]) if q.get("inst") else None,
                                    int(q["pid"]) if q.get("pid") else None, role,
                                    int(q["player"]) if q.get("player") else None, q.get("fair", "1") != "0")
    except Exception as exc:  # noqa: BLE001
        await sock.send_json({"type": "error", "error": f"连不上游戏：{type(exc).__name__}: {exc}"})
        await sock.close(code=4404)
        return
    await sock.send_json({"type": "hello", "protocol": PROTOCOL, "role": role, "pid": s.g.pid,
                          "methods": [e["name"] for e in R.catalog(role)]})
    sub = {"state": None, "events": False}
    send_lock = asyncio.Lock()

    async def send(obj):
        async with send_lock:
            await sock.send_text(json.dumps(obj, ensure_ascii=False))

    async def pump():
        """订阅：按 hz 推状态；事件每 50 ms 看一次，有就推。出错告诉客户端并停掉这份订阅（以前推送任务悄悄死掉，客户端什么也收不到）。"""
        next_state = 0.0
        while True:
            try:
                now = time.monotonic()
                if sub["events"]:
                    evs = await s.run(s.events)
                    if evs:
                        await send({"type": "events", "events": evs})
                st = sub["state"]
                if st and now >= next_state:
                    next_state = now + 1.0 / st["hz"]
                    await send({"type": "state", "state": await s.run(s.state, st["units"], st["detail"])})
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                return
            except Exception as exc:  # noqa: BLE001
                sub.update(state=None, events=False)
                try:
                    await send({"type": "error", "error": f"推送停了：{type(exc).__name__}: {exc}（重新 subscribe 再开）"})
                except Exception:  # noqa: BLE001
                    return
            await asyncio.sleep(0.05)

    pumper = asyncio.create_task(pump())
    try:
        while True:
            frame = await sock.receive()
            if frame.get("type") == "websocket.disconnect":
                break
            try:
                msg = json.loads(frame.get("text") or "")
                if not isinstance(msg, dict):
                    raise ValueError("消息要是 JSON 对象")
            except ValueError as exc:                  # 坏 JSON / 二进制帧：回一条错，连接不断（和文档一致）
                await send({"id": None, "ok": False, "error": f"看不懂这条消息：{exc}"})
                continue
            mid, op = msg.get("id"), msg.get("op")
            try:
                if op == "call":
                    result = await s.run(s.call, str(msg["method"]), msg.get("args"), msg.get("kwargs"))
                elif op == "subscribe":
                    st = msg.get("state") or None
                    if st is not None:                 # 参数当场校验（以前 hz 写成字符串，推送任务直接死掉）
                        if not isinstance(st, dict):
                            raise R.RemoteError("state 要是对象：{\"hz\": 2, \"units\": \"mine\"}")
                        units = st.get("units", "all")
                        if units not in ("all", "mine", "none"):
                            raise R.RemoteError("state.units 只能是 all / mine / none")
                        st = {"hz": max(0.2, min(20.0, float(st.get("hz", 2)))), "units": units, "detail": bool(st.get("detail"))}
                    sub["state"] = st
                    sub["events"] = bool(msg.get("events", False))
                    result = {"state": sub["state"], "events": sub["events"]}
                elif op == "unsubscribe":
                    sub.update(state=None, events=False)
                    result = True
                elif op == "jass":
                    result = await s.run(s.jass, str(msg["code"]))
                elif op == "overview":
                    result = await s.run(R.overview, s.g)
                elif op == "api":
                    result = [{k: e[k] for k in ("name", "category", "signature", "doc")} for e in R.catalog(role)]
                elif op == "ping":
                    result = "pong"
                else:
                    raise R.RemoteError(f"不认识的 op {op!r}（call / subscribe / unsubscribe / jass / overview / api / ping）")
                await send({"id": mid, "ok": True, "result": result})
            except Exception as exc:  # noqa: BLE001 - 一条调用出错只回这一条，连接不断
                err = f"{type(exc).__name__}: {exc}"
                if not isinstance(exc, (R.RemoteError, KeyError, TypeError, ValueError)):
                    err += "\n" + traceback.format_exc(limit=2)
                await send({"id": mid, "ok": False, "error": err})
    except WebSocketDisconnect:
        pass
    finally:
        pumper.cancel()
        await asyncio.to_thread(s.close)       # 放车道、收自己画的东西；别在事件循环里阻塞（以前卡住所有连接最多 3 秒）


def _open_demo_when_up(port: int, token: str) -> None:
    """端口听上了再开演示页（以前 bat 里写死 8870、固定等 3 秒，改了端口或起得慢就打开个空页）。"""
    import socket
    import threading
    import webbrowser

    def run():
        t_end = time.monotonic() + 30
        while time.monotonic() < t_end:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.3)
        else:
            return
        webbrowser.open(f"http://127.0.0.1:{port}/demo" + (f"?token={token}" if token else ""))

    threading.Thread(target=run, daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=w3paths.port("gateway", 8870))
    ap.add_argument("--auth", action="store_true", help="本机也要令牌")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="URL",
                    help="允许这个网页连网关（比如 http://localhost:5173）；默认只认网关自己的演示页，可以给好几次")
    ap.add_argument("--open", action="store_true", help="起来后在浏览器里打开演示页（命令行手动起时用；平时在远见首页「控制中心」起停）")
    a = ap.parse_args()
    try:
        loopback = ipaddress.ip_address(a.host).is_loopback
    except ValueError:
        loopback = a.host == "localhost"
    CONFIG["require_token"] = a.auth or not loopback
    CONFIG["check_host"] = loopback           # 只听本机时才核对 Host（挡 DNS 重绑定）；给局域网用时有令牌兜着
    CONFIG["port"] = a.port
    CONFIG["origins"] = {o.lower().rstrip("/") for o in a.allow_origin}
    if CONFIG["require_token"]:
        CONFIG["token"] = load_token()
        print(f"令牌（连的时候 ?token=...）：{CONFIG['token']}（存在 {TOKEN_FILE}）", flush=True)
    import uvicorn
    print(f"OpenWar3 网关：ws://{a.host}:{a.port}/ws?inst=<N>&role=dev|player|observer", flush=True)
    if a.open:
        _open_demo_when_up(a.port, CONFIG["token"] if CONFIG["require_token"] else "")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""远见「头顶气泡」页的后端（09-26）：把请求转给气泡服务 speech/speak_api.py（本机 ports.speech，默认 8872）。

以前气泡有自己的调试页（http://127.0.0.1:8872/，speech/speak_api.html），和远见是两套界面；09-26 起并进远见，
所有功能在一个系统里管。气泡服务本身不变（茶话会 / 镜头对白 / 战况触发的线程都在它里面跑，远见重启不影响它们），
远见这边只转发 + 汇总：

    GET|POST /api/speech/{path}          转发到 speak_api 的 /api/{path}（只放行下面 ALLOWED 里的）
    GET      /api/speech-overview?inst=  一次拿齐：服务在不在、模型、三个功能在这个实例上开没开、自动开启表、避免遮挡

气泡服务没起来时一律回 503 + detail "speech-down"，网页据此显示「启动气泡服务」按钮（走 services.py 的起停）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import common as C

# 放行的 speak_api 接口（单位列表用远见自己的实时快照，不走气泡服务：省得它为同一局再读一份快照）
ALLOWED = {"say", "clear", "status", "chat", "llm", "config", "banter", "banter/poke", "banter/announce",
           "camtalk", "events", "autostart", "camera", "instances"}
DOWN = "speech-down"
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="speech")


def port() -> int:
    return int(C.w3paths.port("speech", 8872))


def call(method: str, path: str, query: dict | None = None, body: dict | None = None,
         timeout: float = 10.0) -> tuple[int, dict]:
    """(状态码, JSON)。连不上 = (503, {"detail": "speech-down"})；speak_api 报错原样带回它的状态码和 detail。"""
    path = path.strip("/")
    if path not in ALLOWED:
        return 404, {"detail": f"气泡服务没有这个接口：{path}"}
    from . import services as S                     # 先看端口有没有人在听（1 毫秒）：Windows 上连没人听的本机端口要白等约 2 秒
    if not S._listening(port()):
        return 503, {"detail": DOWN}
    q = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")})
    url = f"http://127.0.0.1:{port()}/api/{path}" + (f"?{q}" if q else "")
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail") or e.reason
        except ValueError:
            detail = e.reason
        return e.code, {"detail": detail}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)) or "refused" in str(reason).lower():
            return 503, {"detail": DOWN}
        return 504, {"detail": f"气泡服务没有回应：{reason}"}


def overview(inst: int | None) -> dict:
    """首屏和每 3 秒一次的汇总：五个请求并发发给气泡服务（每个自己 2~3 秒超时），一个没回来不拖别的。"""
    st, auto = call("GET", "autostart", timeout=3)
    if st == 503 and auto.get("detail") == DOWN:
        return {"up": False}
    q = {"inst": inst} if inst else {}
    jobs = {k: _pool.submit(call, "GET", k, q if k in ("banter", "camtalk", "events") else None, None, 3.0)
            for k in ("banter", "camtalk", "events", "config", "llm")}
    got = {k: f.result() for k, f in jobs.items()}

    def feature(k):
        s, d = got[k]
        return {"running": bool(d.get("running")), "error": d.get("last_error") or None} if s == 200 else {"running": False, "error": d.get("detail")}

    s_llm, llm = got["llm"]
    s_cfg, cfg = got["config"]
    return {
        "up": True,
        "port": port(),
        # 这个实例的游戏没在跑时 speak_api 回 404：网页显示「实例没在跑」，功能开关先不给点
        "instanceUp": all(got[k][0] != 404 for k in ("banter", "camtalk", "events")),
        "features": {k: feature(k) for k in ("banter", "camtalk", "events")},
        "autostart": auto.get("autostart", {}) if st == 200 else {},
        "avoidOverlap": bool((cfg.get("config") or {}).get("avoid_overlap")) if s_cfg == 200 else False,
        "llm": {"config": llm.get("config"), "reachable": bool(llm.get("reachable")), "models": llm.get("models") or []}
        if s_llm == 200 else {"config": None, "reachable": False, "models": []},
    }

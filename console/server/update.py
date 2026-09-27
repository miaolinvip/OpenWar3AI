# -*- coding: utf-8 -*-
"""远见的新版本提醒（09-26）：启动时（和之后每 6 小时）向 war3ai.com 要一份发布清单，看有没有新版本。

    GET  /api/update          本机版本、服务器上的最新版本、有没有更新、说明、去哪下
    POST /api/update/check    马上再问一次

清单是官网的静态文件 https://war3ai.com/version.json（发新版时改它）：
    {"latest": "0.4.0", "released": "…", "url": "https://war3ai.com/", "notes": {"zh": "…", "en": "…"}, "games": ["1.27.0.52240"]}
安全：只走 https（openwar3.json 的 service_url，见 feedback.service_url）；5 秒超时、最多读 32 KB；
字段逐个校验（版本号必须是 x.y.z，链接只认 war3ai.com / github.com）；只提示、给链接，**从不自动下载或执行任何东西**。
问不到（没网、服务器挂了）就安静地什么都不提示，下次再问。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

from . import common as C
from . import feedback as FB

INTERVAL_S = 6 * 3600
RX_VER = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
ALLOWED_HOSTS = ("war3ai.com", "www.war3ai.com", "github.com")
_state: dict = {"latest": None, "released": None, "url": None, "notes": None, "games": None, "checkedAt": None, "error": None}
_lock = threading.Lock()


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def _safe_url(u) -> str | None:
    try:
        p = urllib.parse.urlparse(str(u))
    except ValueError:
        return None
    return str(u) if p.scheme == "https" and (p.hostname or "").lower() in ALLOWED_HOSTS else None


def check() -> dict:
    url = FB.service_url() + "/version.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"OpenWar3-Farsight/{FB.version()}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read(32 * 1024))
        if not isinstance(d, dict) or not RX_VER.match(str(d.get("latest", ""))):
            raise ValueError("清单格式不对")
        notes = d.get("notes") if isinstance(d.get("notes"), dict) else {}
        with _lock:
            _state.update(latest=d["latest"], released=str(d.get("released") or "")[:20],
                          url=_safe_url(d.get("url")) or "https://war3ai.com/",
                          notes={k: str(v)[:600] for k, v in notes.items() if k in ("zh", "en")},
                          games=[str(g)[:20] for g in (d.get("games") or [])][:10] if isinstance(d.get("games"), list) else None,
                          checkedAt=time.time(), error=None)
    except Exception as e:  # noqa: BLE001  问不到不影响任何功能
        with _lock:
            _state.update(checkedAt=time.time(), error=f"{type(e).__name__}: {str(e)[:120]}")
    return status()


def status() -> dict:
    cur = FB.version()
    with _lock:
        s = dict(_state)
    newer = bool(s["latest"] and RX_VER.match(cur) and _ver(s["latest"]) > _ver(cur))
    return dict(s, current=cur, newer=newer, source=FB.service_url() + "/version.json")


def start() -> None:
    """后台线程：启动后 3 秒问第一次，之后每 6 小时一次。"""
    def loop():
        time.sleep(3)
        while True:
            check()
            time.sleep(INTERVAL_S)
    threading.Thread(target=loop, name="update-check", daemon=True).start()

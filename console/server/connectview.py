# -*- coding: utf-8 -*-
"""远见「接入与扩展」页的后端（09-25）：网关在不在跑、一键起网关、MCP 服务器怎么挂。只听本机。

    GET  /api/connect                    网关（端口、在不在跑、演示页地址）+ MCP（python 路径、脚本路径、工具表）
    POST /api/connect/gateway/start      起网关（gateway/server.py，脱离远见的进程树；已经在跑就什么都不做）

网关和 MCP 本身的说明见 docs/GATEWAY_ZH.md、docs/MCP_ZH.md。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from . import common as C
from . import instances as I

GATEWAY = os.path.join(C.ROOT, "gateway", "server.py")
MCP = os.path.join(C.ROOT, "tools", "war3_mcp.py")
GATEWAY_LOG = os.path.join(C.BIN, "gateway", "gateway.out")

# MCP 的 10 个工具（和 sdk/python/openwar3/mcp.py 的 _tools 一致；说明是给人看的短句）
MCP_TOOLS = [
    ("war3_overview", "一页局面：时间、资源、人口、兵种数、英雄、看得见的敌人、生产", "all"),
    ("war3_units", "单位列表（我方 / 敌方 / 野怪），地址用来下命令", "all"),
    ("war3_events", "上次调用之后发生的事：死亡、升级、施法、生产完成、聊天、点按钮", "all"),
    ("war3_call", "调任意公开接口：移动、攻击、训练、建造、施法、画按钮……", "all"),
    ("war3_api", "按关键字查接口", "all"),
    ("war3_toast", "屏幕上方显示一行字", "draw"),
    ("war3_say", "单位头顶说一句话", "draw"),
    ("war3_ask_player", "屏幕中间给玩家几张选项卡，等玩家点", "draw"),
    ("war3_screenshot", "游戏画面截图", "all"),
    ("war3_jass", "跑一段 JASS（只给开发角色）", "dev"),
]


def _port() -> int:
    try:
        return int(C.w3paths.port("gateway", 8870))
    except Exception:  # noqa: BLE001
        return 8870


def _probe(port: int) -> dict | None:
    """网关的 GET /（说明页，JSON）；连不上 = None。0.6 秒超时，别拖慢页面。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.6) as r:
            body = r.read(20000).decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body[:200]}
    except OSError:
        return None


def info() -> dict:
    port = _port()
    hello = _probe(port)
    return {
        "gateway": {"port": port, "running": hello is not None, "hello": hello,
                    "ws": f"ws://127.0.0.1:{port}/ws", "demo": f"http://127.0.0.1:{port}/demo", "http": f"http://127.0.0.1:{port}/",
                    "script": GATEWAY, "log": GATEWAY_LOG, "exists": os.path.exists(GATEWAY)},
        "mcp": {"python": C.PYTHON, "script": MCP, "exists": os.path.exists(MCP),
                "tools": [{"name": n, "desc": d, "who": w} for n, d, w in MCP_TOOLS]},
        "root": C.ROOT,
    }


def start_gateway() -> dict:
    port = _port()
    if _probe(port) is not None:
        return {"ok": True, "note": "网关已经在跑", "port": port}
    if not os.path.exists(GATEWAY):
        return {"ok": False, "error": f"找不到 {GATEWAY}"}
    os.makedirs(os.path.dirname(GATEWAY_LOG), exist_ok=True)
    cmd = (f'cmd.exe /c "set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{GATEWAY}" --port {port} '
           f'>> "{GATEWAY_LOG}" 2>&1"')
    r = I.spawn_detached(cmd, C.ROOT)
    if r.get("rc") != 0:
        return {"ok": False, "error": r.get("error") or f"起进程失败（rc={r.get('rc')}）"}
    for _ in range(20):                       # 等它开始监听（通常 1~2 秒）
        time.sleep(0.3)
        if _probe(port) is not None:
            return {"ok": True, "pid": r.get("pid"), "port": port}
    return {"ok": False, "error": f"网关进程起来了（pid {r.get('pid')}），但 6 秒内没在 {port} 端口应答；看 {GATEWAY_LOG}"}

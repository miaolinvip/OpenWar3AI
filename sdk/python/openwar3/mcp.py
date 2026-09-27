# -*- coding: utf-8 -*-
"""给大模型的工具接口（09-25）：MCP 服务器（stdio，换行分隔的 JSON-RPC 2.0）。Claude / 本地大模型 / 任何支持 MCP 的
agent 挂上它，就能直接看局面、下命令、在屏幕上跟玩家说话、问玩家、截图看画面。

    python -m openwar3.mcp --inst 9                    # dev 角色（本机工具：全部接口，含 JASS）
    python -m openwar3.mcp --inst 9 --role player --player 0   # 只指挥 0 号玩家、公平模式（看不见迷雾里的）
    claude mcp add war3 -- python -m openwar3.mcp --inst 9      # Claude Code 里挂上

工具（都返回 JSON 文本；截图返回图片）：
  war3_overview        一页局面：时间、资源、人口、我方兵种数、英雄（血蓝等级冷却）、看得见的敌人、生产
  war3_units           单位列表（owner = me / enemy / creep / all，types 过滤）
  war3_events          上次调用之后发生的事（默认去掉刷屏的 unit.damaged / order.changed / damage / ui.hover）
  war3_call            调任意公开接口（docs/API_ZH.md；单位写 {"unit": 地址}）—— 下命令、查科技、画界面都走它
  war3_api             查接口：名字 / 签名 / 说明（按关键字）
  war3_toast           屏幕上方一行字；war3_say：单位头顶说一句
  war3_ask_player      屏幕中间给玩家几个选项卡，等玩家点（可选暂停），返回选了哪个
  war3_screenshot      游戏画面截图（PNG）
  war3_jass            跑一段 JASS（只给 dev；改世界只在单人局）
第一次调工具时才连游戏（游戏可以后开）。日志走 stderr，stdout 只有协议。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import traceback

from . import remote as R

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
NOISY = {"unit.damaged", "order.changed", "damage", "ui.hover"}
MAX_TEXT = 200 * 1000              # 一个工具结果的文字上限（字符）：超过的截断并说明，免得把客户端的上下文撑爆
MAX_ASK_SECONDS = 120.0            # war3_ask_player 最多等这么久（等的时候整个服务器是阻塞的）


def _tools(role: str) -> list[dict]:
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    tools = [
        ("war3_overview", "一页局面：游戏时间、资源、人口、我方各兵种数量、英雄（血/蓝/等级/技能冷却）、看得见的敌方兵种、正在生产的。先调它。",
         obj({})),
        ("war3_units", "单位列表：地址 addr（下命令时写 {\"unit\": addr}）、类型四字码、主人、坐标、血蓝、英雄等级和技能冷却。",
         obj({"owner": {"type": "string", "enum": ["me", "enemy", "creep", "all"], "default": "me"},
              "types": {"type": "array", "items": {"type": "string"}, "description": "只要这些四字码，比如 [\"hpea\", \"hfoo\"]"},
              "limit": {"type": "integer", "default": 60}})),
        ("war3_events", "上次调用之后发生的事：unit.died / killed / hero.levelup / spell.cast / production.done / message（含聊天 .chat）/ "
                        "ui.click（玩家点了画板按钮，.key）/ hotkey / selection.changed / player.left / game.ended …",
         obj({"limit": {"type": "integer", "default": 80},
              "include_noisy": {"type": "boolean", "default": False, "description": "带上 unit.damaged / order.changed / damage / ui.hover"}})),
        ("war3_call", "调 Game 的任意公开接口（war3_api 查名字和参数）：比如 move([{\"unit\": a}], x, y)、attack_move、attack、train(building, 'hfoo')、"
                      "build(worker, 'hbar', x, y)、cast(hero, 'thunderbolt', target=…)、learn、tech、ui.button、canvas.text…。单位参数写 {\"unit\": 地址}。",
         obj({"method": {"type": "string"}, "args": {"type": "array", "default": []},
              "kwargs": {"type": "object", "default": {}}}, ("method",))),
        ("war3_api", "查接口目录：按关键字搜名字和说明，返回签名和说明（这个角色能调的）。",
         obj({"query": {"type": "string", "default": ""}})),
        ("war3_toast", "在游戏屏幕上方显示一行字（几秒后消失）。和玩家说话用它。",
         obj({"text": {"type": "string"}, "seconds": {"type": "number", "default": 4}}, ("text",))),
        ("war3_say", "让一个单位头顶冒一句话（气泡）。", obj({"unit": {"type": "integer", "description": "单位地址"},
                                                        "text": {"type": "string"}, "seconds": {"type": "number", "default": 4}},
                                                       ("unit", "text"))),
        ("war3_ask_player", "在游戏屏幕中间给玩家几张选项卡，等玩家用鼠标点一个，返回选中的下标和文字（超时 = null）。pause=true 时等的时候游戏暂停（单人局）。",
         obj({"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
              "timeout": {"type": "number", "default": 30}, "pause": {"type": "boolean", "default": False}},
             ("question", "options"))),
        ("war3_screenshot", "游戏画面截图（PNG）。看界面、看战况用。", obj({"scale": {"type": "number", "default": 0.5}})),
    ]
    if role == "dev":
        tools.append(("war3_jass", "跑一段 JASS（一行一句，可以直接粘 JASS 或用 Python 写法；内置 hero() me() unit('hfoo')）。改世界只在单人局。",
                      obj({"code": {"type": "string"}}, ("code",))))
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s in tools]


class Server:
    def __init__(self, inst: int | None, pid: int | None, role: str, player: int | None):
        self.inst, self.pid, self.role, self.player = inst, pid, role, player
        self._g = None

    @property
    def g(self):
        """连着的 Game。按实例号连的：farm 重开游戏换了进程，就放掉旧的、连新的（MCP 服务器一挂就是好几局，
        以前一直连着第一局的 pid，换局以后命令全部超时）。"""
        from .game import Game, _find_pid
        if self._g is not None and self.pid is None and self.inst is not None:
            now = _find_pid(self.inst)
            if now and now != self._g.pid:
                try:
                    self._g.close()
                except Exception:  # noqa: BLE001
                    pass
                self._g = None
        if self._g is None:
            self._g = Game(inst=self.inst, pid=self.pid, player=self.player if self.role == "player" else None,
                           fair=self.role == "player", observer=self.role == "observer")
        return self._g

    # ── 工具 ──
    def tool(self, name: str, a: dict):
        g = self.g
        if name == "war3_overview":
            return R.overview(g)
        if name == "war3_units":
            us = g.units(a.get("owner", "me"), set(a["types"]) if a.get("types") else None)
            return [R.unit_view(u) for u in us[: int(a.get("limit", 60))]]
        if name == "war3_events":
            evs = [e for e in g.events() if a.get("include_noisy") or e.kind not in NOISY]
            return [R.event_view(e) for e in evs[-int(a.get("limit", 80)):]]
        if name == "war3_call":
            return R.call(g, str(a["method"]), self.role, a.get("args"), a.get("kwargs"))
        if name == "war3_api":
            q = str(a.get("query", "")).lower()
            return [{"name": e["name"], "signature": e["signature"], "doc": e["doc"].splitlines()[0] if e["doc"] else ""}
                    for e in R.catalog(self.role) if not q or q in e["name"].lower() or q in e["doc"].lower()][:40]
        if name == "war3_toast":
            self._need_draw()
            g.ui.toast(str(a["text"]), seconds=float(a.get("seconds", 4)))
            return {"ok": True}
        if name == "war3_say":
            self._need_draw()
            u = R.resolve_args(g, {"unit": int(a["unit"])})
            ok = bool(g.say(u, str(a["text"]), float(a.get("seconds", 4))))
            return {"ok": ok} if ok else {"ok": False, "error": getattr(g, "last_say_error", None)}
        if name == "war3_ask_player":
            self._need_draw()
            opts = [str(o) for o in a["options"]][:6]
            if not opts:
                raise R.RemoteError("options 至少给一项")
            ch = g.ui.choice(str(a["question"]), opts, key="mcp.ask", pause=bool(a.get("pause")))
            i = ch.wait(timeout=max(1.0, min(float(a.get("timeout", 30)), MAX_ASK_SECONDS)))
            return {"picked": i, "option": opts[i] if i is not None else None}
        if name == "war3_screenshot":
            from .screenshot import png_bytes
            data = png_bytes(g.pid, max(0.1, min(float(a.get("scale", 0.5)), 1.0)))
            if data is None:
                raise R.RemoteError("截不到（窗口最小化了？）")
            return {"__image__": base64.b64encode(data).decode("ascii")}
        if name == "war3_jass":
            if self.role != "dev":
                raise R.RemoteError("war3_jass 只给 dev 角色")
            from .jassrun import Script
            return R.to_json(Script(g).run(str(a["code"])))
        raise R.RemoteError(f"没有工具 {name}")

    def _need_draw(self):
        if self.role == "observer":
            raise R.RemoteError("observer 角色不能往屏幕上画东西")

    # ── JSON-RPC ──
    def handle(self, msg) -> dict | None:
        # 格式不对的消息回协议错误，不能让服务器崩掉（以前非对象消息 / params 不是对象直接 AttributeError 退出）
        if not isinstance(msg, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request: 消息要是 JSON 对象"}}
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid params: params 要是对象"}}
        if method == "initialize":
            want = params.get("protocolVersion")
            return self._ok(mid, {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                                  "capabilities": {"tools": {"listChanged": False}},
                                  "serverInfo": {"name": "openwar3", "version": "0.1.0"},
                                  "instructions": "魔兽争霸 III 1.27 的实时接口。先 war3_overview 看局面；单位用 addr 指代（{\"unit\": addr}）；"
                                                  "war3_api 查能调什么；war3_events 看发生了什么；和玩家说话用 war3_toast / war3_ask_player。"})
        if method in ("notifications/initialized", "notifications/cancelled") or mid is None:
            return None
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": _tools(self.role)})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if not isinstance(args, dict):
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid params: arguments 要是对象"}}
            try:
                out = self.tool(str(name), args)
            except Exception as exc:  # noqa: BLE001 - 工具出错按 MCP 规矩放在结果里（isError），不是协议错误
                detail = "" if isinstance(exc, (R.RemoteError, KeyError, ValueError, TypeError)) else "\n" + traceback.format_exc(limit=2)
                return self._ok(mid, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}{detail}"}], "isError": True})
            if isinstance(out, dict) and "__image__" in out:
                return self._ok(mid, {"content": [{"type": "image", "data": out["__image__"], "mimeType": "image/png"}]})
            text = json.dumps(out, ensure_ascii=False, default=str)
            if len(text) > MAX_TEXT:
                text = text[:MAX_TEXT] + f"\n…（结果太长，截掉了 {len(text) - MAX_TEXT} 个字符；用 limit / types 参数缩小范围）"
            return self._ok(mid, {"content": [{"type": "text", "text": text}]})
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    @staticmethod
    def _ok(mid, result) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, inp=None, out=None) -> None:
        inp = inp or sys.stdin
        out = out or sys.stdout
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                out.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                out.flush()
                continue
            for m in (msg if isinstance(msg, list) else [msg]):
                try:
                    resp = self.handle(m)
                except Exception as exc:  # noqa: BLE001 - 服务器不能因为一条消息退出
                    mid = m.get("id") if isinstance(m, dict) else None
                    resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {type(exc).__name__}: {exc}"}}
                if resp is not None:
                    out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                    out.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m openwar3.mcp")
    ap.add_argument("--inst", type=int)
    ap.add_argument("--pid", type=int)
    ap.add_argument("--role", default="dev", choices=R.ROLES)
    ap.add_argument("--player", type=int)
    a = ap.parse_args(argv)
    if a.role == "player" and a.player is None:
        ap.error("--role player 要带 --player N")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    print(f"openwar3 MCP：inst={a.inst} pid={a.pid} role={a.role}", file=sys.stderr, flush=True)
    Server(a.inst, a.pid, a.role, a.player).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

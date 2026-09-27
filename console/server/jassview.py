# -*- coding: utf-8 -*-
"""远见「JASS 控制台」的后端（09-24）：外部程序随时调游戏里的 JASS 能力。远见只听本机（run.py 默认 127.0.0.1）。

    GET  /api/jass/natives?q=TextTag&cat=visual&lang=zh   查 native（名字 / 签名 / 参数名 / 中英文说明 / 分类 / 只读 / 实机调过）；
                                                          q 也按说明搜（q=漂浮文字）
    POST /api/instances/{n}/jass        {"code": "..."}   跑 JASS 小脚本（直接粘 JASS；变量在这个实例上一直记着）
    POST /api/instances/{n}/jass/call   {"name": "CreateUnit", "args": [...]}   调一个 native（程序调用用这个）
    POST /api/instances/{n}/jass/reset                     清掉记着的变量

args 里的单位 / 物品可以写 {"unit": 地址}（快照里的 addr）；其余按签名自动转（'Hpal' 四字码、实数、布尔、字符串）。
脚本语法见 sdk/python/openwar3/jassrun.py；边界（单人局、字符串、code 参数只能 null）见 docs/JASS_API_ZH.md。
"""
from __future__ import annotations

import threading
from collections import Counter

from . import common as C              # 先导它：它把 sdk/python 加进 sys.path，下面才 import 得到 openwar3

from openwar3 import jass as J        # noqa: E402
from openwar3.jassrun import Script, ScriptError, show  # noqa: E402

_sessions: dict = {}          # 实例号 -> (游戏 pid, Game, Script)
_lock = threading.Lock()      # 同一时间只跑一段：脚本之间的变量、快车道都是共享的


def natives(q: str = "", cat: str = "", lang: str = "zh") -> dict:
    """lang=zh/en：分类名和 desc 用哪种语言（每条 native 的 zh、en 说明都带着）。q 按名字和中英文说明搜。"""
    lang = "en" if lang == "en" else "zh"
    counts = Counter(J.category_of(n) for n in J.signatures())
    return {"categories": [{"key": k, "name": J.category_name(k, lang), "count": counts.get(k, 0)} for k in J.CATEGORY_NAMES],
            "natives": J.catalog(q, cat, lang=lang), "verified": len(J.VERIFIED), "total": len(J.signatures())}


def _game_pid(n: int) -> int | None:
    """这个实例的游戏 pid：读启动器写的 bin/farm/inst<N>.out（最后一个 WAR3_PID），再看进程在不在。
    ⚠ 别用 I.status()：它枚举全部进程，新进程里第一次要 20 秒以上（09-24 实测远见重启后第一次调 JASS 等了 78 秒）。"""
    import os
    import re
    import psutil
    try:
        with open(os.path.join(C.FARM_DIR, f"inst{n}.out"), "rb") as fh:
            text = fh.read().decode("utf-8", "replace").replace("\0", "")
    except OSError:
        return None
    if "control channel: ok" not in text:
        return None
    m = re.findall(r"WAR3_PID=(\d+)", text)
    pid = int(m[-1]) if m else 0
    return pid if pid and psutil.pid_exists(pid) else None


def _session(n: int):
    pid = _game_pid(n)
    if not pid:
        raise J.JassError(f"实例 {n} 的游戏没在跑")
    cur = _sessions.get(n)
    if cur and cur[0] == pid:
        return cur
    import openwar3
    g = openwar3.Game(pid=pid)
    cur = (pid, g, Script(g))
    _sessions[n] = cur                    # 换局（pid 变了）就是新的一套变量
    return cur


def run(n: int, code: str) -> dict:
    with _lock:
        try:
            pid, g, sc = _session(n)
        except J.JassError as e:
            return {"ok": False, "error": str(e)}
        printed: list = []
        sc.echo = printed.append
        try:
            rows = sc.run(code or "")
            return {"ok": True, "pid": pid, "rows": rows, "printed": printed,
                    "vars": {k: show(v) for k, v in sc.vars.items()}}
        except ScriptError as e:
            return {"ok": False, "pid": pid, "error": str(e), "line": e.line, "printed": printed,
                    "vars": {k: show(v) for k, v in sc.vars.items()}}
        except J.JassError as e:
            return {"ok": False, "pid": pid, "error": str(e), "printed": printed}


def call(n: int, name: str, args: list) -> dict:
    with _lock:
        try:
            pid, g, _sc = _session(n)
            conv = [_arg(g, a) for a in (args or [])]
            return {"ok": True, "pid": pid, "value": show(g.jass.call(name, *conv)), "signature": J.signatures().get(name)}
        except J.JassError as e:
            return {"ok": False, "error": str(e), "signature": J.signatures().get(name)}


def _arg(g, a):
    """{"unit": 地址} -> 快照里的那个单位（JASS 通道会换成 JASS 句柄）；其余原样。"""
    if isinstance(a, dict) and "unit" in a:
        s = g.snapshot(max_age=0)
        u = next((u for u in (s.units if s else []) if u.addr == int(a["unit"])), None)
        if u is None:
            raise J.JassError(f"快照里没有地址 {a['unit']} 的单位")
        return u
    return a


def reset(n: int) -> dict:
    with _lock:
        _sessions.pop(n, None)
    return {"ok": True}

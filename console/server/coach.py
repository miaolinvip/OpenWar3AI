# -*- coding: utf-8 -*-
"""运营顾问（本地大模型）总览：模型服务在不在 + 每个实例的顾问状态。

  GET  /api/coach              {server, instances: [...]}
  POST /api/coach/llm-start    后台拉起 LM Studio 服务并载入模型（和气泡那边的一键启动同一套逻辑）

★ 09-21 同时跑 6 个游戏进程时，无从确认大模型运营层到底起没起来，需要在远见后台按进程看到这一层的状态。
  查下来：6 个大脑都开着顾问，但 **LM Studio 根本没起**，每次请求都连不上 ——
  而大脑侧看不出来：顾问设计成「失败 = 当没有这一层」，行为和纯规则一模一样，
  心跳又只打印前 10 个计数，「顾问·出错」排不进去。能安全降级的东西最容易悄悄降级一整天。
  所以这一页要同时回答两件事，缺一不可：
    模型服务在不在（服务端视角）    每个实例连上没有、最近一条建议是什么（大脑视角）
  两边对不上（服务在、实例却一直 down）本身就是一条线索。

数据源：模型服务直接问 LM Studio 的 /api/v0/models（带 loaded/not-loaded 状态）；
每个实例读自己的 brain_live.json 里的 coach 段（brain/live.py 发布，约 1 秒一份）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

from . import common as C
from . import instances as I

LLM_BASE = "http://127.0.0.1:1234"
LIVE_FRESH_S = 15.0        # brain_live.json 超过这么久没更新 = 大脑没在跑（和 stream.py 同一个口径）
LIVE_KEEP_S = 1800.0       # 超过这么久的实例不列（早就停了的 7~12 号）
_cache: dict = {"at": 0.0, "data": None}
_start: dict = {"running": False, "log": [], "at": 0.0, "ok": None}


def _brain_model() -> str:
    """大脑用的模型名 —— 读生产代码那一份常量，不在这里另写（抄的那一刻就分叉了）。"""
    if C.BRAIN not in sys.path:
        sys.path.insert(0, C.BRAIN)
    try:
        from brain import coach as CH
        return CH.MODEL
    except Exception:  # noqa: BLE001
        return ""


def llm_server(max_age: float = 5.0) -> dict:
    """LM Studio 在不在、载入了哪几个模型、大脑要的那个载没载。5 秒缓存（页面 2~3 秒刷一次）。"""
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < max_age:
        return _cache["data"]
    want = _brain_model()
    out = {"baseUrl": LLM_BASE + "/v1", "up": False, "want": want, "wantLoaded": False,
           "loaded": [], "error": "", "checkedAt": now}
    try:
        with urllib.request.urlopen(LLM_BASE + "/api/v0/models", timeout=1.5) as r:
            rows = json.loads(r.read()).get("data", [])
        out["up"] = True
        out["loaded"] = [{"id": m.get("id"), "ctx": m.get("loaded_context_length"), "quant": m.get("quantization")}
                         for m in rows if m.get("state") == "loaded"]
        # ⚠ LM Studio 给载入的实例加编号后缀（`…aggressive:2`），按前缀认，不按全名 ——
        #   speak_launch.ensure_llm 就是按全名认的，于是载入成功了还报「✘ 载入失败」（09-21 实测）。
        out["wantLoaded"] = any((m["id"] or "") == want or (m["id"] or "").startswith(want + ":")
                                for m in out["loaded"])
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    _cache.update(at=now, data=out)
    return out


def instance_rows() -> list[dict]:
    rows = []
    now = time.time()
    for k in I.known_numbers():
        path = os.path.join(C.inst_dir(k), "brain_live.json")
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if now - mt > LIVE_KEEP_S:
            continue
        d = C.load_json(path, None) or {}
        coach = d.get("coach")
        rows.append({
            "inst": k,
            "liveAge": round(now - mt, 1),
            "fresh": now - mt <= LIVE_FRESH_S,
            "gameT": d.get("t"),
            # 老版本大脑（09-21 之前开局的）不发布 coach 段：和「顾问关着」要分开
            "coach": coach,
            "hasCoach": coach is not None,
            "world": {k2: (d.get("world") or {}).get(k2) for k2 in ("gold", "lumber", "foodUsed", "foodCap", "workers")},
            "economy": d.get("economy"),
        })
    return rows


def overview() -> dict:
    return {"server": llm_server(), "instances": instance_rows(), "start": dict(_start, log=_start["log"][-12:])}


def start_llm() -> dict:
    """后台拉起本地模型。**不阻塞请求**：载入 20 GB 要十几秒到几十秒。
    复用 war3-modules/speak_launch.ensure_llm —— 和气泡的一键启动是同一套，不另写一份。"""
    if _start["running"]:
        return {"ok": True, "already": True}

    def run():
        _start.update(running=True, log=[], at=time.time(), ok=None)
        try:
            if C.MODDIR not in sys.path:
                sys.path.insert(0, C.MODDIR)
            import speak_launch as SL
            SL.say = lambda msg: _start["log"].append(str(msg))      # 把它的进度输出接到这一页
            SL.ensure_llm(skip=False)
            _cache["at"] = 0.0
            # ensure_llm 按全名认模型会误报失败（见 llm_server 的注释），这里以服务端实测为准
            _start["ok"] = llm_server(0)["wantLoaded"]
        except Exception as e:  # noqa: BLE001
            _start["log"].append(f"出错：{type(e).__name__}: {e}")
            _start["ok"] = False
        finally:
            _start["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "started": True}

# -*- coding: utf-8 -*-
"""战斗决策明细（给人看的）：每条 = 哪个单位（中文名 #编号）、做了什么、对谁、为什么、依据的数据、从拿到数据到下令的耗时。

★ 09-21 数据分析层多久做一次决策、从数据到决策滞后多少，原来看不出来。改成把决策逐条在后台显示：哪个单位、什么类型
  （骑士还是大法师）、带 # 后面的编号；这个单位在这个时间点上怎么判定、执行了什么；依据了哪些信息（默认折叠，点开看详情）。
  重点是战斗相关的决策；每条都带从拿到信息到执行的各段耗时。
  原来的日志是写给开发者看的流水账（「hfoo@14D4E0E4 188/420 正在挨 58 血/秒（1.5 秒窗口）……」），没有中文名、没有编号、没有耗时。

一条记录（decisions.jsonl，一行一条 JSON；远见「战斗决策」页读它）：
  wall      写下这条的墙钟（秒）          g        游戏秒（知道的话）
  src       毫秒层 / 大脑                act      决策（中文短语：拉出撤退 / 补攻击令 / 放技能 / 集火 / 打野开打 ……）
  units     [{id, name, type, addr, hp, hpMax}]   做这件事的单位（大脑一条意图可能带好几个）
  target    对谁：{id, name, type, addr} 或 {x, y}
  why       一句话理由（给人看的）
  inputs    依据的数据（页面默认折叠）
  timing    耗时（毫秒）：snapAge 快照读出来时已经旧了多久 / decide 从拿到快照到做出决定 / cmd 下命令本身 / total 合计
            gap 这一轮和上一轮读快照隔了多久（事件落在两轮之间最多再等这么久；不进 total）
            ⚠ snapAge 只量得到「读者看见快照变了之后」那一段（w3state 按发布计数判新旧，游戏里的时钟被倍速钩子改过、
              两边不能直接比），而 DLL 每 50 ms 发一次 —— 毫秒层 100 ms 一轮时它几乎总是 0，快照本身最多再早 50 ms。
写失败只记数，不影响打游戏；文件超过 MAX_BYTES 轮换成 .1（只留一份旧的）。
"""
from __future__ import annotations

import json
import os
import time

MAX_BYTES = 30 * 1024 * 1024
NAMES_PATH = __import__('w3paths').NAMES_JSON
_NAMES: dict | None = None


def names() -> dict:
    global _NAMES
    if _NAMES is None:
        try:
            with open(NAMES_PATH, encoding="utf-8") as fh:
                _NAMES = (json.load(fh) or {}).get("byCode", {}) or {}
        except Exception:  # noqa: BLE001
            _NAMES = {}
    return _NAMES


def unit_name(typ: str) -> str:
    return names().get(typ, typ)


class DecisionLog:
    def __init__(self, path: str, src: str, ids_fn=None, clock=time.monotonic, wall=time.time):
        """ids_fn() -> {地址: 编号}（本局的血条编号，brain/unitids 发布的 unit_ids.json）。"""
        self.path, self.src, self.ids_fn, self.clock, self.wall = path, src, ids_fn, clock, wall
        self.written = 0
        self.failed = 0

    def _ids(self) -> dict:
        try:
            return (self.ids_fn() if self.ids_fn else {}) or {}
        except Exception:  # noqa: BLE001
            return {}

    def unit(self, u, ids: dict | None = None) -> dict:
        ids = self._ids() if ids is None else ids
        return {"id": ids.get(u.addr), "name": unit_name(u.type), "type": u.type, "addr": f"{u.addr:X}",
                "hp": int(getattr(u, "hp", 0) or 0), "hpMax": int(getattr(u, "hp_max", 0) or 0)}

    def log(self, act: str, units, why: str, *, target=None, point=None, inputs=None,
            t0: float | None = None, cmd_s: float = 0.0, age_ms: float | None = None, g: float | None = None,
            gap_ms: float | None = None) -> None:
        """t0：这一轮拿到快照的时刻（和 clock 同一个钟）；cmd_s：下命令那一下花了几秒；age_ms：快照读出来时已经旧了多久；
        gap_ms：这一轮和上一轮读快照隔了多久 —— 一件事在两轮之间发生，最多要等这么久才被看见（不计入 total）。"""
        try:
            ids = self._ids()
            now = self.clock()
            timing = {}
            if age_ms is not None:
                timing["snapAge"] = round(float(age_ms), 1)
            if t0 is not None:
                timing["decide"] = round(max(0.0, (now - t0 - cmd_s)) * 1000, 1)
            timing["cmd"] = round(cmd_s * 1000, 1)
            timing["total"] = round(timing.get("snapAge", 0.0) + (max(0.0, now - t0) * 1000 if t0 is not None else timing["cmd"]), 1)
            if gap_ms is not None:
                timing["gap"] = round(float(gap_ms), 1)
            rec = {"wall": round(self.wall(), 3), "src": self.src, "act": act,
                   "units": [self.unit(u, ids) for u in (units if isinstance(units, (list, tuple)) else [units])],
                   "why": why, "timing": timing}
            if g is not None:
                rec["g"] = round(float(g), 1)
            if target is not None:
                rec["target"] = self.unit(target, ids)
            elif point is not None:
                rec["target"] = {"x": round(point[0]), "y": round(point[1])}
            if inputs:
                rec["inputs"] = inputs
            line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
            try:
                if os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
            self.written += 1
        except Exception:  # noqa: BLE001 - 给人看的日志出任何问题都不能影响打游戏
            self.failed += 1


def tail(path: str, limit: int = 500, max_bytes: int = 4 * 1024 * 1024) -> list:
    """读文件最后 limit 条（只读末尾 max_bytes，远见用）。"""
    out = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            data = fh.read().decode("utf-8", "replace")
        lines = data.splitlines()
        if size > max_bytes and lines:
            lines = lines[1:]                     # 第一行多半被截断
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out

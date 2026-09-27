# -*- coding: utf-8 -*-
"""对局记录 match-*.jsonl（和 war3ai.ps1 同一格式，让 war3db / match_review / worldmodel data_index / tactical 脚本照常读）。

格式约束（外围消费者调研，09-17）：
  * 紧凑 JSON、中文不转义：json.dumps(separators=(',', ':'), ensure_ascii=False) —— data_index / known_spots_seed 按字节子串匹配
  * 文件名 match-YYYYMMDD-HHMMSS-NNN-<8位hex>.jsonl，本地时间 = 开局那一刻（data_index 二分查找、页面按位置切）
  * 同一 tick 最多一条 state、一条 intel（war3db 主键 (match_id, tick)，重复会整批入库失败）
  * 数值写 JSON 数字；state.units 'hpea=10,hfoo=2'、intel.composition 'hfoox6 hrifx2'、intel.food 'N/M' 字符串
  * experiment.data.planVer 必须有（data_index / counter_audit 按它筛局）；result.data.outcome 必须有
  * action.text 的措辞是接口（war3db 动词前缀、tactical 正则）—— 新大脑的动作用新前缀，旧前缀只在语义相同的时候用
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

PLAN_VER = 1000          # 新大脑的版本号从 1000 起，和策略层 v2xx 分开（data_index / counter_audit 按 planVer 筛）


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def units_str(counts) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(counts.items()))


def comp_str(counts) -> str:
    return " ".join(f"{k}x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


class MatchRecorder:
    def __init__(self, matches_dir: str, game: int = 1, plan_ver: int = PLAN_VER, now: datetime | None = None,
                 run_id: str | None = None):
        os.makedirs(matches_dir, exist_ok=True)
        now = now or datetime.now()
        self.episode = uuid.uuid4().hex
        self.run_id = run_id or uuid.uuid4().hex
        self.name = f"match-{now:%Y%m%d-%H%M%S}-{game:03d}-{self.episode[:8]}.jsonl"
        self.path = os.path.join(matches_dir, self.name)
        self.plan_ver = plan_ver
        self.t0 = time.monotonic()
        self._state_ticks: set[int] = set()
        self._intel_ticks: set[int] = set()
        self.closed = False

    def write(self, kind: str, tick: int | None, data: dict, game_sec: float | None = None) -> None:
        if self.closed:
            return
        now = datetime.now()
        rec = {"t": f"{now:%H:%M:%S}.{now.microsecond // 1000:03d}", "utc": datetime.now(timezone.utc).isoformat(),
               "tick": tick, "kind": kind, "episodeId": self.episode, "runId": self.run_id,
               "wallElapsedSec": round(time.monotonic() - self.t0, 3),
               "gameElapsedSec": None if game_sec is None else int(game_sec), "clockSource": "game_clock", "data": data}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(dumps(rec) + "\n")

    def start(self, game: int, seen_units: int) -> None:
        self.write("start", 0, {"at": f"{datetime.now():%Y-%m-%dT%H:%M:%S}", "game": game, "seenUnits": seen_units,
                                "brain": "war3-brain"})

    def experiment(self, **extra) -> None:
        self.write("experiment", 0, dict({"planVer": self.plan_ver, "brain": "war3-brain"}, **extra))

    def state(self, tick: int, world, extra: dict | None = None) -> bool:
        if tick in self._state_ticks:
            return False
        self._state_ticks.add(tick)
        data = {"race": getattr(world, "race", None) or "human", "gold": world.gold, "lumber": world.lumber, "foodUsed": world.food_used, "foodCap": world.food_cap,
                "army": len(world.my_army), "heroes": len(world.my_heroes), "workers": len(world.my_workers),
                "halls": len(world.my_halls), "armyHp": int(sum(u.hp for u in world.my_army + world.my_heroes)),
                "units": units_str(world.counts)}
        data.update(extra or {})
        self.write("state", tick, data, world.t)
        return True

    def intel(self, tick: int, world, player_id: int | None = None, extra: dict | None = None) -> bool:
        if tick in self._intel_ticks:
            return False
        self._intel_ticks.add(tick)
        from collections import Counter
        comp = Counter(u.type for u in world.enemy_fighters)
        food_used = 0
        try:
            from . import gamedata as G
            food_used = sum(G.food(u.type) for u in world.enemies)
        except Exception:  # noqa: BLE001
            pass
        data = {"race": world.foe_race, "playerId": player_id, "gold": -1, "lumber": -1, "food": f"{food_used}/{food_used}",
                "army": len(world.enemy_fighters), "armyHp": int(sum(u.hp for u in world.enemy_fighters)),
                "workers": len(world.enemy_workers), "heroes": sum(1 for u in world.enemy_fighters if u.type[:1].isupper()),
                "expos": len(world.enemy_halls), "composition": comp_str(comp)}
        data.update(extra or {})
        self.write("intel", tick, data, world.t)
        return True

    def action(self, tick: int, text: str, game_sec: float | None = None) -> None:
        self.write("action", tick, {"text": text}, game_sec)

    def result(self, tick: int, outcome: str, minutes: float, **extra) -> None:
        self.write("result", tick, dict({"outcome": outcome, "minutes": round(minutes, 2)}, **extra))

    def lesson(self, tick: int, data: dict) -> None:
        self.write("lesson", tick, data)

    def finalize(self, tick: int) -> None:
        self.write("finalized", tick, {"at": f"{datetime.now():%Y-%m-%dT%H:%M:%S}"})
        self.closed = True

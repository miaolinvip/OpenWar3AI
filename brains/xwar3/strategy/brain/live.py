# -*- coding: utf-8 -*-
"""大脑实时状态发布：实例目录下 brain_live.json（Farsight 控制台「AI 决策」页读它）。

一秒左右（游戏秒 1.5）整份重写一次，原子替换。读的一侧拿不到/读到半截就等下一份 —— 这里写失败只记数，不影响打游戏。
"""
from __future__ import annotations

import json
import os
import time

from . import amai
from .offense import POLICY
from .engagement import POLICY as COMBAT_POLICY


def _u(u) -> dict | None:
    if u is None:
        return None
    return {"addr": getattr(u, "addr", None), "type": getattr(u, "type", None), "x": round(getattr(u, "x", 0.0)), "y": round(getattr(u, "y", 0.0))}


def _trees_near_main(brain, r: float = 1800.0) -> list:
    w = brain.world
    main = w.main_town() if w is not None else None
    if main is None:
        return []
    return [[round(t["x"]), round(t["y"])] for t in (getattr(brain, "trees", None) or [])
            if t.get("hp", 1) > 0 and abs(t["x"] - main.x) < r and abs(t["y"] - main.y) < r]


def _coach(brain) -> dict | None:
    c = getattr(brain, "coach", None)
    if c is None:
        return None
    d = c.live(brain.world.t)
    # 开关以配置为准：job_coach 只在开着时才把 enabled 置真，关掉之后 c.enabled 会停在上一次的值
    d["enabled"] = bool(brain.config["enable_coach"]) if brain.config else d["enabled"]
    if not d["enabled"]:
        d["state"] = "off"
    return d


def snapshot(brain) -> dict:
    w = brain.world
    a = brain.attack
    camp = None
    if a.camp:
        alive = [c for c in w.creeps if c.addr in a.camp_ids] if a.camp_ids else []
        camp = {"x": round(a.camp["x"]), "y": round(a.camp["y"]), "strength": a.camp["strength"], "alive": len(alive), "committed": bool(a.camp_ids)}
    plan = brain.last_plan or {}
    return {
        "v": 1,
        "wall": time.time(),
        "t": round(w.t, 1),
        "configVersion": brain.config.version if brain.config else 0,
        "config": brain.config.overrides if brain.config else {},
        "powerbuild": {"policy": "human-double-build-v1", "helper": brain.powerbuild.helper,
                       "sites": brain.powerbuild.sites},
        "repairs": {"policy": "human-building-repair-v1", "tasks": brain.repairs.tasks},
        "world": {
            "gold": w.gold, "lumber": w.lumber, "foodUsed": w.food_used, "foodCap": w.food_cap,
            "tier": w.tier, "foeTier": w.foe_tier, "foeRace": w.foe_race,
            "ownStrength": round(w.own_strength, 1), "foeStrength": round(w.foe_strength, 1), "aggression": round(w.aggression, 1),
            "army": len(w.my_army), "heroes": len(w.my_heroes), "workers": len(w.my_workers), "halls": len(w.my_halls),
            "enemyFighters": len(w.enemy_fighters), "enemyBuildings": len(w.enemy_buildings), "creeps": len(w.creeps),
            "myArmies": [{"n": len(x.units), "x": round(x.x), "y": round(x.y), "strength": round(x.strength, 1)} for x in w.my_armies],
            "enemyArmies": [{"owner": x.owner, "n": len(x.units), "x": round(x.x), "y": round(x.y), "strength": round(x.strength, 1),
                             "dx": round(x.dx), "dy": round(x.dy)} for x in w.enemy_armies],
            "towns": [{"owner": tw.owner, "hall": tw.hall.type if tw.hall else None, "x": round(tw.x), "y": round(tw.y), "threat": round(tw.threat, 1)}
                      for tw in w.towns],
            "acceptedThreat": round(amai.accepted_threat(w.t), 1),
        },
        "attack": {
            "policy": POLICY, "combatPolicy": COMBAT_POLICY, "assessment": dict(a.assessment),
            "skirmish": dict(a.skirmish.report, phase=a.skirmish.phase),
            "state": a.state, "target": _u(a.target), "targetKind": a.target_kind, "startStrength": round(a.start_strength, 1),
            "searchLeft": a.search_left, "camp": camp, "gatherAt": a.gather_at if a.gather_at > -1e8 else None,
            "retreatAt": a.retreat_at if a.retreat_at > -1e8 else None, "safe": a.safe, "counts": dict(a.counts),
        },
        # 英雄去留的评分（brain/heroplan.py）：每个英雄当前动作 + 各动作得分。
        # 换掉 if 链的主要理由就是这个 —— "为什么没撤"要能一眼看出是哪条规则压过了哪条。
        "heroes": {
            "counts": dict(a.heroes.counts),
            "decisions": [{"addr": d.hero.addr, "type": d.hero.type,
                           "hp": round(d.hero.hp / d.hero.hp_max, 2) if d.hero.hp_max else None,
                           "action": d.action, "scores": {k: round(v, 1) for k, v in d.scores.items()},
                           "top3": d.top3, "reasons": d.reasons}
                          for d in a.heroes.last.values()],
            "stuck": dict(brain.stuck.stuck_now), "stuckCounts": dict(brain.stuck.counts),
        },
        "defense": {"mode": brain.defense_mode, "counts": dict(brain.defense.counts), "preserveSince": brain.defense.preserve_since},
        "recovery": {"phase": brain.recovery.phase, "started": brain.recovery.started},
        # 运营顾问（本地大模型，brain/coach.py）：连没连上、最近一条建议、延迟、计数。
        # 09-21 排查大模型运营层是否在运行时发现，LM Studio 根本没起、而大脑这边看不出来 ——
        # 能安全降级的东西最容易悄悄降级一整天，所以状态必须摆到远见上。
        "coach": _coach(brain),
        # 血条编号（brain/unitids.py）：分了几个号、这一轮标了几个、标不上的原因
        "unitIds": {"assigned": len(brain.unit_ids.ids), "labeled": len(brain.unit_ids.labeled),
                    "off": brain.label_off, "counts": dict(brain.unit_ids.counts)} if getattr(brain, "unit_ids", None) else None,
        "harass": {"counts": dict(brain.harass.counts), "busy": len(brain.harass.busy_addrs)},
        # AMAI 战略引擎（strategy_engine == "amai" 才有；"table" 时是 None，网页按空处理）
        "strategy": brain.strategy.live() if getattr(brain, "strategy", None) is not None
                    and getattr(brain.strategy, "started", False) else None,
        # 开局谱进度（brain/opening.py；没选或种族不符是 None）
        "opening": brain.opening_run.live() if getattr(brain, "opening_run", None) is not None else None,
        # 主基地附近扫到的树（通路检查 act.seals_exit 用的就是这份；09-18 核对「大法师被堵在家里」时要拿真实的树对模型）
        "trees": _trees_near_main(brain),
        "plan": plan,
        "pending": [{k: (v if k != "worker" else v) for k, v in p.items()} for p in brain.pending],
        "economy": {"goldCrew": len(brain.gold_crew), "lumberCrew": len(brain.lumber_crew), "trees": len(brain.trees),
                    "sites": len(brain.site_hp),
                    # AMAI 派工这一轮的表（人族 / 兽族）：n 个农民按表该几个采金 / 伐木、现在各几个
                    "harvestPlan": getattr(brain, "harvest_plan", None) or None},
        "intents": brain.last_intents,
        "notes": list(brain.recent_notes),
        "counts": dict(brain.counts.most_common(60)),
        "act": dict(sorted(brain.act.counts.items(), key=lambda kv: -kv[1])[:40]),
        "jobs": brain.jobs.stats(),
        "peak": dict(brain.peak),
    }


def publish(brain, path: str) -> bool:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot(brain), fh, ensure_ascii=False, separators=(",", ":"), default=str)
        os.replace(tmp, path)
        return True
    except OSError:
        brain.counts["实时发布·写失败"] += 1
        return False

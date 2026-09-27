# -*- coding: utf-8 -*-
"""交战胜率叫撤（09-17，配合策略层 v291）：打上 5~10 游戏秒之后用世界模型 M1 的胜率判断要不要整队撤。

09-17 要求：明显打不过时整队回撤，不把所有单位硬顶到全灭。

为什么是「接触后 5/10 秒的胜率」而不是开打前的推演（worldmodel/reports/m1_v0_v3_1220.md，真实大仗按局 5 折）：
  接触那一刻 W3-Simulate AUC 0.61、Brier 0.32（比直接报基率 0.14 还差）；接触后 5 秒 lr AUC 0.77、10 秒 0.81。
  开打前谁都判不准，打上几秒就判得出来。输掉的大仗里我方参战单位阵亡 95% —— 现在基本等于不撤。
门槛（RULES，按折外概率分档校准，真实大仗 k=5 504 场 / k=10 456 场）：
  k>=5 秒 p<0.06：这一档实际赢 6~7%；k>=10 秒 p<0.08：实际赢约 5%（p<0.03 只赢 1%）。
  只在明显要输时撤 —— mod_combat 09-07 的教训：势均力敌时叫撤比明显要输时撤还惨一倍（被追着打）。
家门口不撤（和 mod_combat 原来的遭遇战撤退同一条规矩，在 mod_combat 里判）。

输入口径和训练一致：worldmodel.winpred_infer.predict(我方 + 敌方作战单位, k)，它自己按接触重心 1500 挑近处单位。
「接触」由调用方给（mod_combat 的 fz/mz：双方 900 内），连续 END_GAP 游戏秒没接触算这一仗结束。
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = brains/xwar3（worldmodel 包的父目录）
RULES = ((10.0, 0.08), (5.0, 0.06))   # (接触后至少几游戏秒, 胜率低于这个就撤)，按秒数从大到小匹配
MIN_SIDE = 3          # 双方各至少这么多作战单位才算（零星摸一下不判）
END_GAP = 4.0         # 游戏秒：这么久没接触 = 这一仗打完了
PRED_EVERY = 1.0      # 游戏秒：交战中多久算一次
COOLDOWN = 8.0        # 游戏秒：叫过一次撤，这么久内不再叫（撤退令本身要时间生效）


class GameClock:
    """引擎游戏时钟（秒）= W3P 世界块的 clock（运行时每次发布时读引擎时钟，步长 5 毫秒）。
    和 worldmodel/shadow.py、策略层 game_ms 同一个量。src = w3cmd.Cmd（有 snapshot()）或 w3world.World（有 read()）。
    09-23 P3：原来在这里自己去游戏里读，开放层不再这么做。"""

    def __init__(self, src):
        self.src = src

    def read(self) -> float | None:
        try:
            s = self.src.snapshot() if hasattr(self.src, "snapshot") else self.src.read()
            return s.clock if s is not None and s.in_game else None
        except Exception:  # noqa: BLE001
            return None


def _default_predict():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from worldmodel.winpred_infer import predict   # noqa: WPS433
    return predict


def threshold(k: float) -> float | None:
    for kk, th in RULES:
        if k >= kk:
            return th
    return None


class FightOdds:
    """一仗一个状态：update() 每轮喂一次，返回 None 或叫撤的决定 dict(k, p, thr, n_near)。"""

    def __init__(self, predict=None):
        self._predict = predict
        self.error = None          # 模型加载失败的原因（非 None = 这条规则停用，mod_combat 心跳里报）
        self.checks = 0            # 算了几次胜率
        self.fails = 0             # predict 抛异常 / 没给出概率的次数
        self.reset()

    def reset(self) -> None:
        self.start = None
        self.last_contact = None
        self.last_pred = -1e9
        self.fired_at = -1e9
        self.last = None           # 最近一次 dict(k, p)

    def _pred(self):
        if self._predict is None and self.error is None:
            try:
                self._predict = _default_predict()
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
        return self._predict

    def update(self, t: float | None, mine, foes, contact: bool) -> dict | None:
        if t is None:
            return None
        if self.last_contact is not None and t + 5.0 < self.last_contact:     # 时钟倒退 = 换局
            self.reset()
        if contact:
            # 断开多久算打完只在「没接触」那一支判：两次调用之间没喂数据（快照缺几轮）不等于断开过
            if self.start is None:
                self.start = t
            self.last_contact = t
        else:
            if self.start is not None and t - self.last_contact > END_GAP:
                self.reset()
            return None
        k = t - self.start
        thr = threshold(k)
        if thr is None or t - self.last_pred < PRED_EVERY or t - self.fired_at < COOLDOWN:
            return None
        mine = [u for u in mine if u.hp > 0]
        foes = [u for u in foes if u.hp > 0]
        if len(mine) < MIN_SIDE or len(foes) < MIN_SIDE:
            return None
        pred = self._pred()
        if pred is None:
            return None
        self.last_pred = t
        units = [dict(type=u.type, owner=0, x=u.x, y=u.y, hp=u.hp, hp_max=u.hp_max) for u in mine] + \
                [dict(type=u.type, owner=1, x=u.x, y=u.y, hp=u.hp, hp_max=u.hp_max) for u in foes]
        self.checks += 1
        try:
            r = pred(units, k=k)
        except Exception:  # noqa: BLE001
            self.fails += 1
            return None
        p = r.get("p_win_value")
        if p is None:
            self.fails += 1
            return None
        self.last = dict(k=round(k, 1), p=round(float(p), 3))
        if p < thr:
            self.fired_at = t
            return dict(k=round(k, 1), p=round(float(p), 3), thr=thr, n_near=tuple(r.get("n_near") or ()))
        return None


def centroid(units) -> tuple[float, float] | None:
    us = list(units)
    if not us:
        return None
    return sum(u.x for u in us) / len(us), sum(u.y for u in us) / len(us)


def near(units, cx: float, cy: float, r: float):
    return [u for u in units if math.hypot(u.x - cx, u.y - cy) <= r]


def write_signal(path: str, game_pid, epoch, game_s: float, decision: dict, cx: float, cy: float,
                 n_me: int, n_foe: int, seq: int, why: str = "胜率") -> None:
    """叫撤信号给策略层（fight_retreat.json，原子替换）：策略层据此结束追击/反推、60 秒内不再进攻，免得把刚撤下来的人又派回去。
    why：胜率（本模块 FightOdds）/ 血量输出（mod_combat 遭遇战撤退 1.6 倍）/ 英雄预警（接触前英雄身边敌军明显打不过）。"""
    if game_s is None:
        return
    data = dict(seq=seq, pid=game_pid, epoch=epoch, game_ms=int(game_s * 1000), wall=round(time.time(), 3), why=why,
                k=decision.get("k", -1), p=decision.get("p", -1), thr=decision.get("thr", -1),
                x=round(cx), y=round(cy), n_me=n_me, n_foe=n_foe)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass

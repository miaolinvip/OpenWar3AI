# -*- coding: utf-8 -*-
"""开局谱（opening book）：照高手录像定的开局顺序，可在线切换，一份 JSON 一个开局。

09-18 按 Yumiko 的顺序做速开矿开局，与原开局并存成两个版本、可随时切换；
      以后还会加入更多参考开局，结构上要方便扩展。

  * 开局谱放在 brain/openings/*.json，放进去就多一个选项（在线配置 opening，Farsight「AI 决策」页 / 设置页点选，2 秒内生效）；
    默认 none = 不用，行为和没有这一层时一模一样（按建造表）。
  * 新的参考录像：python tools/make_opening.py <录像> --who 名字 --id xxx 出一份草稿，人看一眼改好再放进 openings/。

怎么接进建造（只接手写建造表那条路；strategy_engine = amai 时不管）：
  开局谱在跑的时候，建造表的需求只留 keep 里列的（默认农民、农场 —— 这两样是「一直补、按需盖」，两局录像里 Yumiko 也是这么做的），
  其余全部让位给开局谱；开局谱每次只把「当前这一步」（加上紧跟着标了 with_prev 的几步）变成需求，优先级 prio。
  一步「有了」（在建/在训练队列/刚下过令都算；开分矿必须观察到地基）就往下走，兵死了不回去补。
  已经过的分矿步骤保留补建需求，避免地基被毁后一直等到开局谱结束。
  一步等了 max_wait_s 游戏秒还没下出去就跳过（记原因），整份最多跑到 until_s 游戏秒，之后交回建造表。

步骤字段：kind（unit / hero / building / expand / upgrade / upgrade_hall / morph）、code、qty（全场总数，和建造表一样）、
  where（building 的落点：base 主基地 / expansion 最新那座分矿大厅旁边）、note、with_prev、not_before_s、max_wait_s。
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field

from . import gamedata as G
from .build import Want

OPENINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openings")
NONE = "none"
NONE_LABEL = "不用（按建造表）"
KINDS = ("unit", "hero", "building", "expand", "upgrade", "upgrade_hall", "morph")
KIND_CN = {"unit": "训练", "hero": "英雄", "building": "建造", "expand": "开分矿", "upgrade": "研究", "upgrade_hall": "升本", "morph": "升级"}
DEFAULT_PRIO = 84          # 低于建造表的「开局农民」85 和「人口快满」农场 92：农民、房子不断，开局谱的钱排在它们后面
DEFAULT_WAIT_S = 90.0
DEFAULT_UNTIL_S = 720.0
DEFAULT_KEEP = {"hpea": None, "hhou": None, "opeo": None, "otrb": None, "uaco": None, "uzig": None, "ewsp": None, "emow": None}
_NAMES: dict | None = None


def cn(code: str) -> str:
    """四字码 -> 中文名（build_intel4/names.json，和 Farsight 同一份）；读不到就原样返回。"""
    global _NAMES
    if _NAMES is None:
        try:
            with open(G.w3paths.NAMES_JSON, encoding="utf-8-sig") as fh:
                _NAMES = (json.load(fh) or {}).get("byCode", {})
        except (OSError, ValueError):
            _NAMES = {}
    return _NAMES.get(code, code)


@dataclass
class Step:
    kind: str
    code: str
    qty: int = 1
    where: str = ""
    note: str = ""
    with_prev: bool = False
    not_before_s: float = 0.0
    max_wait_s: float = DEFAULT_WAIT_S
    count: tuple = ()        # 按这几种合计算「有了几个」（哨塔升成神秘之塔后哨塔数会变少：塔的步骤按 hwtw+hatw+hgtw+hctw 算总数）

    def label(self) -> str:
        return f"{KIND_CN.get(self.kind, self.kind)} {cn(self.code)}" + (f" ×{self.qty}" if self.qty > 1 else "") \
            + ({"expansion": "（分矿）", "base": "（主基地）"}.get(self.where, ""))


@dataclass
class Opening:
    id: str
    name: str
    race: str
    steps: list
    desc: str = ""
    source: list = field(default_factory=list)
    keep: dict = field(default_factory=lambda: dict(DEFAULT_KEEP))
    prio: int = DEFAULT_PRIO
    until_s: float = DEFAULT_UNTIL_S
    path: str = ""
    fill_kinds: tuple = ("unit",)   # 建造表里这几类需求照样留着、排在开局谱这一步后面（见 OpeningRun.wants）


def parse(d: dict, path: str = "") -> Opening:
    """JSON -> Opening；字段不对直接抛 ValueError（load_all 会把出错的文件记下来跳过，不让一份坏文件拖垮大脑）。"""
    oid = str(d.get("id") or os.path.splitext(os.path.basename(path))[0])
    if not oid or oid == NONE:
        raise ValueError("id 不能为空也不能叫 none")
    steps = []
    for i, s in enumerate(d.get("steps") or []):
        if s.get("kind") not in KINDS or not s.get("code"):
            raise ValueError(f"第 {i + 1} 步 kind/code 不对：{s}")
        steps.append(Step(kind=s["kind"], code=str(s["code"]), qty=int(s.get("qty", 1)), where=str(s.get("where") or ""),
                          note=str(s.get("note") or ""), with_prev=bool(s.get("with_prev")),
                          not_before_s=float(s.get("not_before_s") or 0.0), max_wait_s=float(s.get("max_wait_s") or DEFAULT_WAIT_S),
                          count=tuple(s.get("count") or ())))
    if not steps:
        raise ValueError("没有步骤")
    keep = d.get("keep")
    return Opening(id=oid, name=str(d.get("name") or oid), race=str(d.get("race") or ""), steps=steps, desc=str(d.get("desc") or ""),
                   source=list(d.get("source") or []), keep=dict(DEFAULT_KEEP) if keep is None else dict(keep),
                   prio=int(d.get("prio", DEFAULT_PRIO)), until_s=float(d.get("until_s", DEFAULT_UNTIL_S)), path=path,
                   fill_kinds=tuple(d.get("fill_kinds", ("unit",))))


_cache: dict = {"sig": None, "all": {}, "errors": {}}


def load_all(folder: str = OPENINGS_DIR) -> dict:
    """id -> Opening。按文件名和修改时间缓存：往 openings/ 里放新文件、改旧文件，下一次读就生效。"""
    files = sorted(glob.glob(os.path.join(folder, "*.json")))
    sig = (folder, tuple((f, os.path.getmtime(f)) for f in files))
    if _cache["sig"] == sig:
        return _cache["all"]
    out, errors = {}, {}
    for f in files:
        try:
            with open(f, encoding="utf-8-sig") as fh:
                op = parse(json.load(fh), f)
            out[op.id] = op
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors[os.path.basename(f)] = str(exc)
    _cache.update(sig=sig, all=out, errors=errors)
    return out


def choices() -> tuple:
    return (NONE,) + tuple(sorted(load_all()))


def labels() -> dict:
    return {NONE: NONE_LABEL, **{k: op.name for k, op in load_all().items()}}


class OpeningRun:
    """一局里一份开局谱的进度。brain 要提供 have(w, want) 和 main_town/my_halls（world）。"""

    def __init__(self, op: Opening, t0: float = 0.0):
        self.op = op
        self.idx = 0
        self.front_since: float | None = None
        self.done_at: float | None = None
        self.why_done = ""
        self.skipped: list = []          # [(步号, 标签, 原因)]
        self.last_reason: dict = {}      # 步号 -> 最近一轮规划给的原因（攒钱 / 前置 / 引擎裁决…）
        self.t0 = t0
        self._front: list = []

    @property
    def active(self) -> bool:
        return self.done_at is None

    def want_of(self, i: int, w) -> Want | None:
        s = self.op.steps[i]
        where = s.where
        if s.kind == "building" and where == "expansion":
            main = w.main_town()
            others = [h for h in w.my_halls if main is None or h is not main.hall]
            if not others:
                return None                                     # 分矿大厅还没出现（开矿令下了、农民还在路上）
            where = f"town:{others[-1].addr}"
        elif s.kind == "building" and not where:
            where = "base"
        elif s.kind == "expand":
            where = "mine"
        return Want(s.kind, s.code, s.qty, self.op.prio, where, f"开局谱 {self.op.name} 第 {i + 1}/{len(self.op.steps)} 步")

    def has(self, brain, w, i: int, want: Want | None) -> int:
        s = self.op.steps[i]
        if s.kind == "expand" and want is not None:
            # 发令和工人行走不代表分矿兑现，至少观察到地基后才推进步骤。
            return brain.have(w, want) - brain._pending_n(s.kind, s.code)
        if s.count:
            if want is not None and want.where.startswith("town:"):
                # 分矿步骤只能由这座分矿附近的塔满足；主基地已有塔不能让开局谱跳过它。
                return brain.have_near_town(w, want, s.count)
            return sum(w.counts.get(c, 0) for c in s.count) + brain._pending_n(s.kind, s.code)
        return brain.have(w, want) if want is not None else 0

    def advance(self, brain, w, notes: list) -> None:
        steps = self.op.steps
        while self.active and self.idx < len(steps):
            want = self.want_of(self.idx, w)
            if want is not None and self.has(brain, w, self.idx, want) >= steps[self.idx].qty:
                notes.append(f"开局谱第 {self.idx + 1}/{len(steps)} 步：{steps[self.idx].label()}")
                self.idx += 1
                self.front_since = None
                continue
            if self.front_since is None:
                self.front_since = w.t
            if w.t - self.front_since > steps[self.idx].max_wait_s:
                why = self.last_reason.get(self.idx) or ("分矿大厅还没出现" if want is None else "没轮到")
                self.skipped.append((self.idx + 1, steps[self.idx].label(), why))
                notes.append(f"开局谱跳过第 {self.idx + 1} 步 {steps[self.idx].label()}：等了 {int(w.t - self.front_since)} 秒（{why}）")
                self.idx += 1
                self.front_since = None
                continue
            break
        if self.active and self.idx >= len(steps):
            self.finish(w.t, "走完", notes)
        elif self.active and w.t > self.op.until_s:
            self.finish(w.t, f"到 {int(self.op.until_s)} 秒还没走完（停在第 {self.idx + 1} 步）", notes)

    def finish(self, t: float, why: str, notes: list) -> None:
        self.done_at, self.why_done = t, why
        notes.append(f"开局谱 {self.op.name} {why}：{self.idx}/{len(self.op.steps)} 步，跳过 {len(self.skipped)} 步，"
                     f"用时 {int(t)} 秒，交回建造表")

    def frontier(self, w) -> list[tuple[int, Want]]:
        """当前这一步 + 紧跟着标了 with_prev 的几步（还没到 not_before_s 的不出）。"""
        out = []
        i = self.idx
        while i < len(self.op.steps):
            s = self.op.steps[i]
            if i > self.idx and not s.with_prev:
                break
            want = self.want_of(i, w)
            if want is not None and w.t >= s.not_before_s:
                out.append((i, want))
            i += 1
        return out

    def wants(self, brain, w, table_wants: list, notes: list) -> list:
        """建造表需求 -> 开局谱在跑时的需求：keep 里的留下（可改优先级），别的让位；加上开局谱当前这一步。"""
        self.advance(brain, w, notes)
        if not self.active:
            return table_wants
        kept = []
        for i, step in enumerate(self.op.steps[:self.idx]):
            if step.kind == "expand":
                want = self.want_of(i,w)
                if want is not None and brain.have(w,want) < step.qty:
                    kept.append(want)  # 工地被毁/未兑现，补建需求持续存在，不等整份开局谱结束。
        for x in table_wants:
            if x.code in self.op.keep:
                p = self.op.keep[x.code]
                kept.append(Want(x.kind, x.code, x.qty, max(x.prio, int(p)) if p is not None else x.prio, x.where, x.note)
                            if p is not None else x)
            elif x.kind in self.op.fill_kinds:
                # ★ 09-18 第 17~21 局（速开矿 5 局全输、峰兵 7~10）：开局谱当前这一步被「不是缺钱」的原因卡住（分矿有野怪先清、前置没有…）时，
                #   原来整个建造停着 —— 第 21 局 9 分钟金子 1935、兵 8 个。Yumiko 是边开矿边一直出步兵的。
                #   现在建造表的出兵照样留着、排在这一步后面：为这一步攒钱时规划器在它那里停下（兵不花钱），被别的卡住时照常出兵
                kept.append(Want(x.kind, x.code, x.qty, min(x.prio, self.op.prio - 1), x.where, x.note))
        self._front = self.frontier(w)
        return kept + [wt for _i, wt in self._front]

    def observe(self, res) -> None:
        """规划结果回填：当前这几步为什么没下出去（跳过时写进笔记，复盘能看出是缺钱、缺前置还是引擎不让）。"""
        ids = {id(wt): i for i, wt in getattr(self, "_front", [])}
        if res.stopped_at is not None and id(res.stopped_at) in ids:
            self.last_reason[ids[id(res.stopped_at)]] = "攒钱"
        for x, why in res.skipped:
            if id(x) in ids:
                self.last_reason[ids[id(x)]] = why

    def live(self) -> dict:
        n = len(self.op.steps)
        cur = self.op.steps[self.idx].label() if self.active and self.idx < n else ""
        return {"id": self.op.id, "name": self.op.name, "step": min(self.idx + 1, n), "steps": n, "current": cur,
                "active": self.active, "doneAt": self.done_at, "why": self.why_done,
                "skipped": [{"step": a, "label": b, "why": c} for a, b, c in self.skipped]}

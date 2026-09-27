# -*- coding: utf-8 -*-
"""主循环计时（09-19）：一轮到底慢在哪。

现象：run_brain.play_match 一轮（读快照 -> 读时钟 -> brain.step -> 记录 -> sleep 0.2）有时要 5~9 秒墙钟，
3 倍速 = 15~27 游戏秒大脑不做决策。lesson.jobs 只有每个任务的**平均**毫秒（build 180~350、tactics 50~130），
看不见尖峰，也分不开「Python 在算」「抢通道锁」「等游戏处理」「sleep」。先埋数据再改：

  * 段：一轮里每段的墙钟，**只记自己那部分**（嵌套进去的段从外层扣掉），所以各段加起来 = 这一轮。
      tm.begin() / tm.end(t)             一轮（sleep 0.2 不算在内）
      with tm.section("任务·build"):     嵌套一层；退出时把里面还开着的 lap 一起关掉（任务抛异常也不会串层）
      tm.lap("build·规划")               同一层里换到下一段（关掉上一个 lap，开新的）
    ⚠ lap 只会关「栈顶是 lap」的那一段 —— 所以 lap 只能用在自己开的那一层里（run_brain 的循环、run_due 包好的任务里），
      brain.step 里用 section，不要直接 lap（那会把 run_brain 的「大脑」段关掉）。
  * 通道：instrument(tm, ch=..., claims=..., act=...) 给**本进程**的通道对象包一层（实例属性，不改 w3chan.py ——
    四个毫秒层模块也在用它，改它会连带重载模块）：
      锁   = 抢实例互斥量 War3UiControlMutex_<pid> 等了多久（毫秒层四个模块、Farsight worker、导播都在抢同一把）
      游戏 = 请求发出去到游戏 UI 线程处理完（_submit；返回 False = 5 秒超时，记「失败」）
      租约 = 租约表 War3ClaimsMx 的锁
      等待 = act.build 下令后 sleep 0.25 秒看接没接（tm.sleep）
    每次记到当时所在的段，以及**是谁调的**：第一个不在 w3chan/w3cmd 里的函数，带上 w3cmd 的方法名（Actuator.feasible>context）。
  * 每分钟（run_brain 心跳）一行：几轮、≥2 秒/≥5 秒几轮、最慢一轮多少秒慢在哪几段、谁在调通道、单次最慢的请求；
    结构化的同一份追加进 inst<N>/steptime.jsonl（kind=minute），≥2 秒的每一轮也各记一条（kind=slow）。
    计数无条件累加（先记数，再决定要不要打印）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager, nullcontext

SLOW_S = 2.0                 # 这么慢的一轮单独落一条 kind=slow
VERY_SLOW_S = 5.0
ROTATE_BYTES = 20 * 1024 * 1024
ROOT_NAME = "循环·其它"      # 一轮里不在任何段里的时间（ops/判局末前面那些）
KINDS = ("lock", "game", "claim", "wait")
KIND_ZH = {"lock": "锁", "game": "游戏", "claim": "租约", "wait": "等待"}
_SKIP_FILES = {"w3chan.py", "w3cmd.py", "w3claim.py", "steptime.py", "contextlib.py"}


def _stat() -> dict:
    return {"s": 0.0, "n": 0, "calls": 0, "fail": 0, "lock": 0.0, "game": 0.0, "claim": 0.0, "wait": 0.0}


def caller() -> str:
    """调通道的是谁：从调用栈往外找第一个不在 w3chan/w3cmd/本文件里的帧（qualname），带上最外层 w3cmd 方法名。"""
    f = sys._getframe(1)
    cmdfn = None
    while f is not None:
        base = os.path.basename(f.f_code.co_filename)
        if base == "w3cmd.py":
            cmdfn = f.f_code.co_name                 # 往外走时不断覆盖 = 最外层（离调用方最近）那个
        elif base not in _SKIP_FILES:
            q = getattr(f.f_code, "co_qualname", f.f_code.co_name)
            return q + (f">{cmdfn}" if cmdfn else "")
        f = f.f_back
    return "?"


class NullTimer:
    """不计时（测试、离线回放）：接口和 StepTimer 一样，全是空操作。"""

    def begin(self) -> None:
        pass

    def end(self, t=None):
        return None

    def section(self, name: str):
        return nullcontext()

    def lap(self, name: str) -> None:
        pass

    def chan(self, kind: str, dt: float, ok: bool = True, who: str | None = None) -> None:
        pass

    def sleep(self, s: float) -> None:
        time.sleep(s)


class StepTimer(NullTimer):
    def __init__(self, clock=time.perf_counter, path: str | None = None, label: str = "", wall=time.time):
        self.clock = clock
        self.wall = wall
        self.path = path                 # steptime.jsonl（None = 不落盘）
        self.label = label               # 对局名，落盘时带上
        self._stack: list = []           # [名字, 开始, 子段合计, 是不是 lap]
        self.loop: dict | None = None
        self.minute = self._fresh()
        self.match = self._fresh()
        self.tick = (0.0, None)          # 这一拍（run_brain 每 10 游戏秒取走）最慢一轮 (秒, 最重的段)
        self.write_errors = 0

    @staticmethod
    def _fresh() -> dict:
        return {"loops": 0, "wall": 0.0, "slow2": 0, "slow5": 0, "sec": {}, "max": None, "who": {},
                "chan": {"calls": 0, "fail": 0, "lock": 0.0, "game": 0.0, "claim": 0.0, "wait": 0.0},
                "worst": {k: (0.0, None, None) for k in KINDS}}

    # ── 一轮 ──
    def begin(self) -> None:
        if self.loop is not None:
            self.end()
        now = self.clock()
        self.loop = {"sec": {}, "who": {}, "start": now}
        self._stack = [[ROOT_NAME, now, 0.0, False]]

    def end(self, t=None):
        if self.loop is None:
            return None
        now = self.clock()
        while self._stack:
            self._close(now)
        loop, self.loop = self.loop, None
        loop["wall"] = now - loop.pop("start")
        loop["t"] = t
        self._archive(loop)
        return loop

    def _close(self, now: float) -> None:
        name, start, child, _lap = self._stack.pop()
        dur = now - start
        st = self.loop["sec"].setdefault(name, _stat())
        st["s"] += dur - child
        st["n"] += 1
        if self._stack:
            self._stack[-1][2] += dur

    @contextmanager
    def section(self, name: str):
        if self.loop is None:
            yield
            return
        depth = len(self._stack)
        self._stack.append([name, self.clock(), 0.0, False])
        try:
            yield
        finally:
            if self.loop is not None:
                now = self.clock()
                while len(self._stack) > depth:
                    self._close(now)

    def lap(self, name: str) -> None:
        if self.loop is None:
            return
        now = self.clock()
        if self._stack and self._stack[-1][3]:
            self._close(now)
        self._stack.append([name, now, 0.0, True])

    # ── 通道 / 锁 / sleep ──
    def chan(self, kind: str, dt: float, ok: bool = True, who: str | None = None) -> None:
        m = self.minute
        m["chan"][kind] += dt
        if kind == "game":
            m["chan"]["calls"] += 1
        if not ok:
            m["chan"]["fail"] += 1
        if self.loop is None:
            return                                   # 一轮之外（开局等待、局末收尾）：只进这一分钟的合计
        name = self._stack[-1][0] if self._stack else ROOT_NAME
        who = who or caller()
        st = self.loop["sec"].setdefault(name, _stat())
        st[kind] += dt
        if kind == "game":
            st["calls"] += 1
        if not ok:
            st["fail"] += 1
        w = self.loop["who"].setdefault((name, who), {"n": 0, "s": 0.0, "fail": 0})
        if kind == "game":
            w["n"] += 1
        w["s"] += dt
        if not ok:
            w["fail"] += 1
        if dt > m["worst"][kind][0]:
            m["worst"][kind] = (dt, name, who)

    def sleep(self, s: float) -> None:
        t0 = self.clock()
        time.sleep(s)
        self.chan("wait", self.clock() - t0)

    # ── 归档 / 摘要 ──
    def _archive(self, loop: dict) -> None:
        wall = loop["wall"]
        for agg in (self.minute, self.match):
            agg["loops"] += 1
            agg["wall"] += wall
            agg["slow2"] += wall >= SLOW_S
            agg["slow5"] += wall >= VERY_SLOW_S
            for name, st in loop["sec"].items():
                a = agg["sec"].setdefault(name, dict(_stat(), max=0.0))
                for k, v in st.items():
                    a[k] += v
                a["max"] = max(a["max"], st["s"])
            for (_sec, who), v in loop["who"].items():
                a = agg["who"].setdefault(who, [0, 0.0])
                a[0] += v["n"]
                a[1] += v["s"]
            if agg["max"] is None or wall > agg["max"]["wall"]:
                agg["max"] = loop
        heavy = self.heaviest(loop)
        if wall > self.tick[0]:
            self.tick = (wall, heavy)
        if wall >= SLOW_S:
            self._write(dict(kind="slow", **self.brief(loop)))

    @staticmethod
    def heaviest(loop: dict) -> str | None:
        return max(loop["sec"].items(), key=lambda kv: kv[1]["s"])[0] if loop["sec"] else None

    def take_tick(self) -> tuple:
        out, self.tick = self.tick, (0.0, None)
        return out

    @staticmethod
    def _sec_text(name: str, st: dict) -> str:
        parts = []
        if st["calls"]:
            parts.append(f"通道 {st['calls']} 次")
        parts += [f"{KIND_ZH[k]} {st[k]:.2f}" for k in KINDS if st[k] >= 0.01]
        if st["fail"]:
            parts.append(f"失败 {st['fail']}")
        return f"{name} {st['s']:.2f}" + (f"（{' '.join(parts)}）" if parts else "")

    @staticmethod
    def _top_who(loop: dict, n: int = 3) -> list:
        rows = sorted(loop["who"].items(), key=lambda kv: -kv[1]["s"])[:n]
        return [(sec, who, v) for (sec, who), v in rows]

    @staticmethod
    def _who_text(loop: dict, n: int = 3) -> str:
        """心跳里的「谁在调」：同一个调用方跨段合起来（落盘的 brief 按段分开），不到 0.01 秒的不列。"""
        agg: dict = {}
        for (_sec, who), v in loop["who"].items():
            a = agg.setdefault(who, [0, 0.0])
            a[0] += v["n"]
            a[1] += v["s"]
        rows = [kv for kv in sorted(agg.items(), key=lambda kv: -kv[1][1]) if kv[1][1] >= 0.01][:n]
        return "、".join(f"{who}{f'×{c}' if c else ''} {s:.2f}秒" for who, (c, s) in rows)

    @staticmethod
    def _sec_chan(agg: dict) -> dict:
        """各段的通道合计（有请求或有等待的段）：{段: {calls, lock, game, claim, wait}}，按 锁+游戏+等待 从大到小。"""
        rows = [(k, st) for k, st in agg["sec"].items() if st["calls"] or st["wait"] or st["claim"] >= 0.001]
        rows.sort(key=lambda kv: -(kv[1]["lock"] + kv[1]["game"] + kv[1]["wait"] + kv[1]["claim"]))
        return {k: {kk: (round(st[kk], 3) if isinstance(st[kk], float) else st[kk]) for kk in ("calls",) + KINDS if st[kk]}
                for k, st in rows}

    @staticmethod
    def _who_total(agg: dict, n: int) -> list:
        """这一段时间里按调用方合计：[[谁, 通道次数, 秒（锁+游戏+租约+等待）], ...]，按秒从大到小。"""
        rows = sorted(agg["who"].items(), key=lambda kv: -kv[1][1])[:n]
        return [[who, c, round(s, 3)] for who, (c, s) in rows]

    def brief(self, loop: dict) -> dict:
        """一轮 -> 可落盘的 dict：各段（按秒从大到小）+ 调通道最多的 8 个（段、谁、次数、秒）。"""
        secs = sorted(loop["sec"].items(), key=lambda kv: -kv[1]["s"])
        return {"wall": round(loop["wall"], 3), "t": loop.get("t"),
                "sec": {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in st.items() if vv}
                        for k, st in secs},
                "who": [[sec, who, v["n"], round(v["s"], 3)] + ([v["fail"]] if v["fail"] else [])
                        for sec, who, v in self._top_who(loop, 8)]}

    def minute_line(self) -> str:
        """这一分钟的摘要（给 run_brain 心跳），同时落一条 kind=minute，然后清零。"""
        m = self.minute
        self.minute = self._fresh()
        rec = {"kind": "minute", "loops": m["loops"], "avg": round(m["wall"] / max(1, m["loops"]), 3),
               "slow2": m["slow2"], "slow5": m["slow5"],
               "chan": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m["chan"].items()},
               "worst": {k: [round(v[0], 3), v[1], v[2]] for k, v in m["worst"].items() if v[1] is not None},
               "sum": {k: round(st["s"], 3) for k, st in sorted(m["sec"].items(), key=lambda kv: -kv[1]["s"])},
               "secMax": {k: round(st["max"], 3) for k, st in sorted(m["sec"].items(), key=lambda kv: -kv[1]["max"])},
               "secChan": self._sec_chan(m),
               "who": self._who_total(m, 15),
               "max": self.brief(m["max"]) if m["max"] else None}
        self._write(rec)
        text = f"循环 {m['loops']} 轮 平均 {rec['avg']:.2f} 秒 ≥2秒 {m['slow2']} ≥5秒 {m['slow5']}"
        if m["max"]:
            mx = m["max"]
            secs = sorted(mx["sec"].items(), key=lambda kv: -kv[1]["s"])
            secs = [kv for kv in secs[:4] if kv[1]["s"] >= 0.01] or secs[:1]
            at = f"@游戏{int(mx['t'])}秒" if isinstance(mx.get("t"), (int, float)) else ""
            text += f"；最慢一轮 {mx['wall']:.2f} 秒{at}：" + "、".join(self._sec_text(k, st) for k, st in secs)
            who = self._who_text(mx)
            if who:
                text += "；这一轮里谁在调（通道/锁/等待）：" + who
        worst = [f"{KIND_ZH[k]} {v[0]:.2f}秒 {v[2]}（{v[1]}）" for k, v in m["worst"].items() if v[1] is not None and v[0] >= 0.2]
        if worst:
            text += "；单次最慢 " + " / ".join(worst)
        c = m["chan"]
        text += f"；通道 {c['calls']} 次 锁 {c['lock']:.1f} 游戏 {c['game']:.1f} 失败 {c['fail']}"
        return text

    def lesson(self) -> dict:
        """整局：几轮、慢轮数、最慢一轮的拆分、各段合计与单轮最大（进对局记录的 lesson.steptime）。"""
        M = self.match
        return {"loops": M["loops"], "avg": round(M["wall"] / max(1, M["loops"]), 3), "slow2": M["slow2"], "slow5": M["slow5"],
                "sum": {k: round(st["s"], 2) for k, st in sorted(M["sec"].items(), key=lambda kv: -kv[1]["s"])[:12]},
                "secMax": {k: round(st["max"], 2) for k, st in sorted(M["sec"].items(), key=lambda kv: -kv[1]["max"])[:12]},
                "calls": {k: st["calls"] for k, st in sorted(M["sec"].items(), key=lambda kv: -kv[1]["calls"])[:12] if st["calls"]},
                "who": self._who_total(M, 12),
                "max": self.brief(M["max"]) if M["max"] else None}

    def _write(self, rec: dict) -> None:
        if not self.path:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > ROTATE_BYTES:
                os.replace(self.path, self.path + ".1")
            rec = dict(at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.wall())), match=self.label, **rec)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            self.write_errors += 1


class _TimedHold:
    """包住 w3chan.Channel._Held / w3claim.Claims._Lock：量进锁等了多久。拿不到锁抛的异常原样往外抛（记一次失败）。"""

    def __init__(self, inner, timer, kind: str):
        self.inner, self.timer, self.kind = inner, timer, kind

    def __enter__(self):
        t0 = self.timer.clock()
        try:
            self.inner.__enter__()
        except BaseException:
            self.timer.chan(self.kind, self.timer.clock() - t0, ok=False)
            raise
        self.timer.chan(self.kind, self.timer.clock() - t0)
        return self

    def __exit__(self, *a):
        return self.inner.__exit__(*a)


def instrument(timer, ch=None, claims=None, act=None, lane=None) -> None:
    """给本进程的通道 / 快车道 / 租约表 / 执行层接上计时。只包实例属性（同一个对象包一次）；行为、返回值、异常都原样。
    lane（09-23 P3）：w3fast.FastLane —— 下令和查询都走它了，一批命令从提交到回执记成「游戏」。"""
    if lane is not None and not getattr(lane, "_steptime", False):
        commands = lane.commands

        def timed_commands(calls, *a, **kw):
            t0 = timer.clock()
            ok = False
            try:
                rs = commands(calls, *a, **kw)
                ok = all(r is not None for r in rs)
                return rs
            finally:
                timer.chan("game", timer.clock() - t0, ok=ok)

        lane.commands, lane._steptime = timed_commands, True
    if ch is not None and not getattr(ch, "_steptime", False):
        hold, submit = ch._hold, ch._submit

        def timed_hold():
            return _TimedHold(hold(), timer, "lock")

        def timed_submit(action, command, timeout=5.0):
            t0 = timer.clock()
            ok = False
            try:
                ok = submit(action, command, timeout)
                return ok
            finally:
                timer.chan("game", timer.clock() - t0, ok=bool(ok))

        ch._hold, ch._submit, ch._steptime = timed_hold, timed_submit, True
    if claims is not None and not getattr(claims, "_steptime", False):
        lock = claims._lock

        def timed_lock():
            return _TimedHold(lock(), timer, "claim")

        claims._lock, claims._steptime = timed_lock, True
    if act is not None:
        act.sleep_fn = timer.sleep

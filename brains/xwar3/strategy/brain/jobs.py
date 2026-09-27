# -*- coding: utf-8 -*-
"""任务调度（AMAI Jobs.txt + TQAddJob）：每个任务管一件事，按游戏秒各跑各的频率。

    s = Scheduler()
    s.add("build", 3.0, build_job)          # fn(world, brain) -> None
    s.run_due(world, brain)

一个任务抛异常不影响别的任务（计数进 errors，最近一次异常留在 last_error）。
计数全都无条件累加（先记数，再决定要不要打印）。
"""
from __future__ import annotations

import time
import traceback
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    name: str
    every: float
    fn: Callable
    next_at: float = 0.0
    runs: int = 0
    errors: int = 0
    wall_ms: float = 0.0
    max_ms: float = 0.0              # 单次最慢（平均值看不见尖峰：09-19 一轮 5~9 秒，而 build 平均才 180~350 ms）
    last_error: str | None = None
    enabled: bool = True


@dataclass
class Scheduler:
    jobs: dict = field(default_factory=dict)

    def add(self, name: str, every: float, fn: Callable, start_at: float = 0.0) -> Job:
        job = Job(name, float(every), fn, float(start_at))
        self.jobs[name] = job
        return job

    def due(self, t: float) -> list[Job]:
        return [j for j in self.jobs.values() if j.enabled and t >= j.next_at]

    def run_due(self, world, brain=None) -> list[str]:
        ran = []
        tm = getattr(brain, "timer", None)          # brain/steptime.py：每个任务一段（任务里面可以再 lap 细分）
        for job in self.due(world.t):
            t0 = time.perf_counter()
            with (tm.section(f"任务·{job.name}") if tm is not None else nullcontext()):
                try:
                    job.fn(world, brain)
                except Exception:  # noqa: BLE001
                    job.errors += 1
                    job.last_error = traceback.format_exc(limit=3).strip().splitlines()[-1]
            job.runs += 1
            ms = (time.perf_counter() - t0) * 1000.0
            job.wall_ms += ms
            job.max_ms = max(job.max_ms, ms)
            job.next_at = world.t + job.every
            ran.append(job.name)
        return ran

    def reset(self) -> None:
        for j in self.jobs.values():
            j.next_at = 0.0

    def stats(self) -> dict:
        return {n: dict(runs=j.runs, errors=j.errors, ms=round(j.wall_ms / max(1, j.runs), 1), max=round(j.max_ms), last_error=j.last_error)
                for n, j in self.jobs.items()}

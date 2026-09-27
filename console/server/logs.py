# -*- coding: utf-8 -*-
"""日志源：每个实例有哪些日志、从尾巴读 N 行、按偏移增量读（WebSocket 推新行用）。"""
from __future__ import annotations

import os
import re

from . import common as C


def sources(n: int) -> list[dict]:
    d = C.inst_dir(n)
    cands = [
        # 09-25：方案 / 模组运行器（tools/run_scheme.py）—— 跑玩家方案时新大脑那几份是空的，日志页默认先看这两份
        ("scheme_bot", "方案 / 模组输出（这一局）", os.path.join(d, "scheme_bot.out"), "scheme"),
        ("scheme_supervisor", "方案运行器", os.path.join(d, "scheme_supervisor.out"), "scheme"),
        ("brain_supervisor", "新大脑监督器", os.path.join(d, "brain_supervisor.out"), "brain"),
        ("brain", "大脑日志（带日期）", os.path.join(d, "brain.log"), "brain"),
        ("brain_err", "大脑错误输出", os.path.join(d, "brain_supervisor.err"), "brain"),
        ("mod_combat", "毫秒层·战斗", os.path.join(d, "mod_combat.log"), "module"),
        ("mod_scavenge", "毫秒层·捡装备", os.path.join(d, "mod_scavenge.log"), "module"),
        ("mod_spy", "毫秒层·侦察", os.path.join(d, "mod_spy.log"), "module"),
        ("mod_fight", "毫秒层·交战录制", os.path.join(d, "mod_fight.log"), "module"),
        ("cmdlog", "命令来源日志", os.path.join(d, "cmdlog.log"), "module"),
        ("launcher", "启动器（开局/种族/镜头）", os.path.join(C.FARM_DIR, f"inst{n}.out"), "game"),
        ("farm_console", "farm（控制台拉起的）", os.path.join(C.FARM_DIR, f"farm_console{n}.out"), "game"),
        ("director", "导播", os.path.join(C.FARM_DIR, f"director{n}.out"), "game"),
        ("ai_run", "老 AI 监督器", os.path.join(d, "ai_run.log"), "old"),
    ]
    out = []
    for key, label, path, group in cands:
        if os.path.exists(path):
            st = os.stat(path)
            out.append({"key": key, "label": label, "group": group, "size": st.st_size, "mtime": st.st_mtime})
    return out


def path_of(n: int, key: str) -> str | None:
    d = C.inst_dir(n)
    table = {"brain_supervisor": os.path.join(d, "brain_supervisor.out"), "brain": os.path.join(d, "brain.log"),
             "brain_err": os.path.join(d, "brain_supervisor.err"), "cmdlog": os.path.join(d, "cmdlog.log"),
             "launcher": os.path.join(C.FARM_DIR, f"inst{n}.out"), "farm_console": os.path.join(C.FARM_DIR, f"farm_console{n}.out"),
             "director": os.path.join(C.FARM_DIR, f"director{n}.out"), "ai_run": os.path.join(d, "ai_run.log"),
             "scheme_bot": os.path.join(d, "scheme_bot.out"), "scheme_supervisor": os.path.join(d, "scheme_supervisor.out")}
    if re.fullmatch(r"mod_\w+", key):
        return os.path.join(d, f"{key}.log")
    return table.get(key)


def _decode(b: bytes) -> str:
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    text = b.decode("utf-8", "replace")
    if "�" in text and text.count("�") > len(text) // 50:
        try:
            return b.decode("gbk", "replace")
        except LookupError:
            pass
    return text.replace("\0", "")


def tail(n: int, key: str, lines: int = 400, max_bytes: int = 600_000) -> dict:
    p = path_of(n, key)
    if not p or not os.path.exists(p):
        return {"key": key, "lines": [], "offset": 0, "missing": True}
    size = os.path.getsize(p)
    start = max(0, size - max_bytes)
    with open(p, "rb") as fh:
        fh.seek(start)
        raw = fh.read()
    text = _decode(raw)
    rows = text.splitlines()
    if start > 0 and rows:
        rows = rows[1:]                    # 第一行多半是半截
    return {"key": key, "lines": rows[-lines:], "offset": size}


def read_since(n: int, key: str, offset: int, max_bytes: int = 200_000) -> tuple[list[str], int]:
    p = path_of(n, key)
    if not p or not os.path.exists(p):
        return [], offset
    size = os.path.getsize(p)
    if size < offset:                      # 文件被截断/轮换：从头
        offset = 0
    if size == offset:
        return [], offset
    with open(p, "rb") as fh:
        fh.seek(offset)
        raw = fh.read(min(max_bytes, size - offset))
    cut = raw.rfind(b"\n")
    if cut < 0:
        return [], offset
    raw = raw[:cut + 1]
    return _decode(raw).splitlines(), offset + len(raw)

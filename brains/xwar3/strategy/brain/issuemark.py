# -*- coding: utf-8 -*-
"""问题快捷键：游戏里按一下 = 暂停 + 记下「哪个实例、哪一局、游戏第几秒」，Farsight「问题备忘」页收进去补描述。

09-19 需求：游戏里按一个快捷键就自动记下当前进程 ID、对局场次和时间点（问题描述到网页上补），
同时游戏暂停，再按一次继续。

分工：
  DLL（运行时的动作 57）          在游戏里认键：没面板开着 -> 打开任务面板（单人局暂停）；
                                  有面板开着 -> 关掉（继续）。每按一次往 DLL 旁边的 issue_hotkey_<游戏pid>.log 追加
                                  「GetTickCount64 毫秒 pause|resume|fail」一行。动作 57 command：0 查、1 等同按一次、2|(键码<<8) 换键
  监督器（run_brain.py）          开局按 ops.issue_hotkey 下发键；每轮读那个文件的新行，pause 就记一条标记
                                  到 inst<N>/issue_marks.jsonl（只追加），并写进对局时间线
  Farsight（server/issues.py）    扫各实例的 issue_marks.jsonl，没收过的标记变成「待填写」的问题

游戏暂停时时钟不走 —— 监督器靠 Hud.poll_pause（任务面板开着 = 人在看）不把它判成对局卡住。
暂停期间游戏时钟停在按键那一刻，所以记的游戏时间点就是监督器这一轮读到的时钟。
"""
from __future__ import annotations

import json
import os
import time
import uuid

from . import ops as OPS

ACTION = 57
MARKS_FILE = "issue_marks.jsonl"      # farsight/server/issues.py 读同一个文件名


def hotkey_log(bin_dir: str, pid: int) -> str:
    """DLL 写的按键记录（运行时的动作 57 写的：DLL 所在目录 = build_intel4）。"""
    return os.path.join(bin_dir, f"issue_hotkey_{pid}.log")


class IssueHotkey:
    """每局一个。ch = w3chan 通道（None = 只读文件、不下发键，测试用）。"""

    def __init__(self, ch, bin_dir: str, pid: int, inst: int, inst_dir: str):
        self.ch = ch
        self.path = hotkey_log(bin_dir, pid)
        self.pid = pid
        self.inst = inst
        self.inst_dir = inst_dir
        self.vk = None                 # 已经下发给 DLL 的键码（None = 还没下发成功）
        try:                           # 接管之前（加载时）按的不算：从现在的文件尾开始读
            self.pos = os.path.getsize(self.path)
        except OSError:
            self.pos = 0
        self.presses = 0

    def configure(self, key: str) -> None:
        """按 ops.issue_hotkey 下发键；没变不发，发失败下一轮再发。"""
        vk = OPS.HOTKEYS.get(key, OPS.HOTKEYS["pause"])[1]
        if vk == self.vk or self.ch is None:
            return
        try:
            if self.ch.action(ACTION, 2 | (vk << 8), timeout=2.0):
                self.vk = vk
        except Exception:  # noqa: BLE001
            pass

    def poll(self) -> list[str]:
        """上次之后 DLL 记的新按键：['pause', 'resume', ...]。文件没有 = 还没按过。"""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.pos:            # 文件被删过重建
            self.pos = 0
        if size == self.pos:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            chunk = fh.read(size - self.pos)
        end = chunk.rfind(b"\n") + 1  # 只收完整的行，半行留到下一轮
        self.pos += end
        out = []
        for ln in chunk[:end].decode("ascii", "replace").splitlines():
            parts = ln.split()
            if len(parts) == 2:
                out.append(parts[1])
        self.presses += len(out)
        return out

    def mark(self, match: str, game: int, game_sec: float, exe: str | None = None, extra: dict | None = None) -> dict:
        """记一条标记（只追加）。返回写进去的那条。"""
        m = {"id": uuid.uuid4().hex[:10], "at": round(time.time(), 3), "inst": self.inst, "pid": self.pid, "exe": exe,
             "match": match, "game": game, "gameTime": round(game_sec, 1), "source": "hotkey"}
        m.update(extra or {})
        os.makedirs(self.inst_dir, exist_ok=True)
        with open(os.path.join(self.inst_dir, MARKS_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        return m

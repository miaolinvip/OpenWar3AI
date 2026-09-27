# -*- coding: utf-8 -*-
"""给场上每个我方英雄 / 作战单位一个本局唯一的编号：显示在血条正上方（气泡 DLL 动作 56），日志里自动带上。

★ 09-21 给战场上每个单位标一个编号（显示在血条上方，参考气泡项目的实现），
  指出某个单位异常时能快速定位当时的战斗决策。

为什么有用：只说「那个大法师」「那个塔」时，要靠时间和兵种去猜是哪一个；有了编号，「5-109 的 #23 在 7:05 跑了」
就能直接落到：对局记录里「编号 #23 = hfoo@14D4E0E4」-> cmdlog 里这个地址收到的每条命令（谁下的）-> 战斗帧里它的血量曲线。

三件事、各有唯一的出处：
  * 编号由大脑分配（这一局第一次见到就给下一个号，死了不回收），分配那一刻写进对局记录；
  * 发布到实例目录 unit_ids.json —— 毫秒层（mod_combat）的日志按它补编号；
  * 日志补编号统一走 annotate()：凡是「类型@地址」（hfoo@14D4E0E4）前面补「#23 」。改一处，两层的日志都带上。

显示：同一个单位头上只能有一个气泡（DLL 按单位存），编号会顶掉正在说的台词 ——
所以这个实例开着对话气泡（speak_api.json 的 autostart 里有它）时编号自动让开，不和台词抢。
DLL 同时最多 32 个气泡：最多标 LABEL_MAX 个，英雄优先，其次离敌人最近的（交战中的单位才是最常需要排查的）。
"""
from __future__ import annotations

import json
import math
import os
import re
import time

LABEL_MAX = 28            # 气泡 DLL 同时最多 32 个，留 4 个给战况/对白
LABEL_MS = 6000           # 每个编号气泡显示多久（毫秒）；job 每 LABEL_EVERY 游戏秒重发一次，重发 = 重新计时
LABEL_EVERY = 2.0         # 游戏秒
WORKERS = {"hpea", "opeo", "uaco", "ewsp", "hmil"}
_TAG = re.compile(r"\b([A-Za-z][A-Za-z0-9]{3})@([0-9A-F]{6,8})\b")


class UnitIds:
    def __init__(self):
        self.ids: dict[int, int] = {}          # 地址 -> 编号
        self.types: dict[int, str] = {}
        self.next = 1
        self.labeled: set[int] = set()         # 上一次发出去的编号气泡（地址）
        self.counts = {"分配": 0, "标注": 0, "让给对话气泡": 0}

    def assign(self, units) -> list[tuple[int, int, str]]:
        """这一拍第一次见到的我方英雄 / 作战单位给号。返回 [(编号, 地址, 类型)]。
        地址被新单位复用（类型变了）当新单位，给新号。"""
        new = []
        for u in units:
            if u.type in WORKERS or u.hp <= 0:
                continue
            if u.addr in self.ids and self.types.get(u.addr) == u.type:
                continue
            n = self.next
            self.next += 1
            self.ids[u.addr], self.types[u.addr] = n, u.type
            new.append((n, u.addr, u.type))
            self.counts["分配"] += 1
        return new

    def get(self, addr: int) -> int | None:
        return self.ids.get(addr)

    def annotate(self, text: str, ids: dict | None = None) -> str:
        """「hfoo@14D4E0E4」-> 「#23 hfoo@14D4E0E4」；已经带着这个编号的不重复加。"""
        ids = self.ids if ids is None else ids
        return annotate(text, ids)

    def pick(self, units, foes) -> list:
        """标哪几个：英雄全标，其余离敌人（敌兵 + 野怪）最近的优先，最多 LABEL_MAX 个。"""
        cand = [u for u in units if u.addr in self.ids and u.hp > 0]

        def near(u):
            return min((math.hypot(f.x - u.x, f.y - u.y) for f in foes), default=1e9)
        cand.sort(key=lambda u: (not u.type[:1].isupper(), near(u), self.ids[u.addr]))
        return cand[:LABEL_MAX]

    def bubbles(self, units, foes):
        """这一轮要发的气泡：选中的发编号（重新计时），上一轮标了这一轮没选中的发 remove。"""
        import w3speak
        chosen = self.pick(units, foes)
        out = []
        for u in chosen:
            hero = u.type[:1].isupper()
            st = w3speak.Style(font_px=16 if hero else 14, pad_px=2, radius_px=3, border_px=0, tail=False,
                               fade=False, type_ms=0, max_width_px=90, bg_color="#A0000000",
                               text_color="#FFD860" if hero else "#F0F0F0")
            out.append(w3speak.Bubble(unit=u.addr, text=f"#{self.ids[u.addr]}", duration_ms=LABEL_MS, style=st))
        keep = {u.addr for u in chosen}
        for a in self.labeled - keep:
            out.append(w3speak.Bubble(unit=a, remove=True))
        self.labeled = keep
        self.counts["标注"] += len(chosen)
        return out

    def publish(self, path: str, pid: int) -> None:
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"pid": pid, "wall": time.time(),
                           "ids": {f"{a:X}": n for a, n in self.ids.items()},
                           "types": {f"{a:X}": t for a, t in self.types.items()}}, fh, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            pass


def annotate(text: str, ids: dict) -> str:
    """ids：地址(int) -> 编号。毫秒层读 unit_ids.json 后也调这一个（war3-modules/mod_combat.note）。"""
    if not ids or "@" not in text:
        return text

    def sub(m):
        n = ids.get(int(m.group(2), 16))
        if n is None:
            return m.group(0)
        head = text[max(0, m.start() - 8):m.start()]
        if re.search(rf"#{n}\s*$", head):
            return m.group(0)
        return f"#{n} {m.group(0)}"
    return _TAG.sub(sub, text)


def load(path: str, pid: int | None = None) -> dict:
    """读 unit_ids.json -> {地址: 编号}；pid 对不上（上一局）当空。"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if pid is not None and int(d.get("pid", 0)) != int(pid):
            return {}
        return {int(a, 16): int(n) for a, n in d.get("ids", {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def speech_on(inst: int, speak_cfg: str) -> bool:
    """这个实例开着对话气泡（茶话会 / 镜头对白 / 战况）吗 —— 开着就不标编号，免得顶掉台词。"""
    try:
        with open(speak_cfg, encoding="utf-8") as fh:
            auto = (json.load(fh) or {}).get("autostart") or {}
        return any(int(inst) in {int(i) for i in (v or [])} for v in auto.values())
    except Exception:  # noqa: BLE001
        return False

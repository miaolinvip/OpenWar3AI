# -*- coding: utf-8 -*-
"""玩法模组示例：无尽守城（endless-defense）。

守住主城：怪一波比一波多，从对面出生点沿地上那条红线冲过来。每守住一波给金子；
点屏幕右边的按钮（或 F7）提前叫下一波，奖励 ×1.5；F8 之后左键点地面放一座免费箭塔（右键取消，每守住两波多一座）。
主城倒了算输，守住 12 波算赢。你照常造兵、盖塔，模组负责出怪和发奖励。单人局。

    python tools/play.py --bot brains/examples/mod_endless_defense.py --inst 9
    （或者远见「AI 方案」页：内置 → 无尽守城（模组示例）→ 用到实例）

用到的底座能力（09-25）：g.ui.button（画板上可点的按钮）、g.ui.hotkey、g.ui.mouse（抓地面点击：左键放塔、右键取消，
这一下游戏收不到）、画板的面板 / 进度条 / 地面路线；事件 unit.died / killed；JASS 刷怪、加金子、放塔；Mod 的刷怪和 finish。
"""
from __future__ import annotations

import math

from openwar3 import Mod
from openwar3.mod import HALLS

WAVES = 12
WAVE_EVERY = 45.0
FIRST_WAVE = 25.0
TOWER = "hgtw"                  # 防御塔
WAVE_UNITS = [
    [("ugho", 4)], [("ugho", 6)], [("ogru", 4), ("ohun", 2)], [("ogru", 6), ("ohun", 3)],
    [("ucry", 4), ("ugho", 4)], [("hkni", 3), ("hfoo", 5)], [("uabo", 3), ("ucry", 4)], [("okod", 2), ("ogru", 8)],
    [("hkni", 6), ("hsor", 3)], [("uabo", 6), ("ugho", 8)], [("okod", 4), ("ohun", 8), ("ogru", 6)],
    [("uabo", 8), ("hkni", 8), ("okod", 3)],
]
GOLD = 1                        # JASS PLAYER_STATE_RESOURCE_GOLD


class EndlessDefense(Mod):
    name = "endless-defense"

    def __init__(self):
        super().__init__()
        self.foe = 11
        self.hall = None              # 主城句柄对
        self.home_xy = (0.0, 0.0)
        self.spawn_at = (0.0, 0.0)
        self.wave = 0
        self.wave_live = False        # 当前这一波还有没清完的怪
        self.next_at = 0.0
        self.bonus = 1.0
        self.kills = 0
        self.towers = 1
        self.placing = False

    # ── 开局 ──
    def on_start(self, g):
        super().on_start(g)
        self.foe = self.wave_player(g)
        halls = [u for u in g.units("me") if u.type in HALLS]
        if not halls:
            self.finish("unknown", "没有主城，守不了")
            return
        self.hall = halls[0].handle
        self.home_xy = (halls[0].x, halls[0].y)
        self.spawn_at = self.far_start(g)
        ui = g.ui
        ui.button("next", "▶ 提前叫下一波（奖励 ×1.5）  F7", screen=(-330, 330), width=300, size=16,
                  on_click=lambda g_, ev: self.call_early(g_))
        ui.hotkey("F7", lambda g_, ev: self.call_early(g_))
        ui.hotkey("F8", lambda g_, ev: self.start_placing(g_))
        g.canvas.path("route", [self.spawn_at, self.home_xy], color=(255, 70, 70, 170), width=5, arrow=True)
        g.ui.toast("无尽守城：守住主城 12 波 · F7 提前叫下一波 · F8 放一座免费箭塔", seconds=7)
        self.next_at = (g.clock() or 0.0) + FIRST_WAVE
        self.every(3.0, self.push)
        self.every(0.5, self.draw_hud, first=0.2)

    def far_start(self, g) -> tuple:
        """离主城最远的那个出生点（地图块开局后几秒才算好；没有就往主城对角推 3000）。"""
        m = g.world.map()
        hx, hy = self.home_xy
        starts = [p for p in (m.starts if m else []) if p != (0.0, 0.0)]
        if starts:
            return max(starts, key=lambda p: (p[0] - hx) ** 2 + (p[1] - hy) ** 2)
        return (hx + 2200.0 * math.copysign(1, -hx or 1), hy + 2200.0 * math.copysign(1, -hy or 1))

    # ── 波次 ──
    def on_tick(self, g):
        super().on_tick(g)
        if self.wave < WAVES and not self.wave_live and (g.clock() or 0.0) >= self.next_at:
            self.spawn_wave(g)

    def call_early(self, g):
        if self.wave >= WAVES:
            return
        if self.wave_live:
            g.ui.toast("这一波还没清完", seconds=2)
            return
        self.bonus = 1.5
        self.spawn_wave(g)

    def spawn_wave(self, g):
        self.wave += 1
        self.wave_live = True
        for code, n in WAVE_UNITS[self.wave - 1]:
            self.spawn_ring(g, self.foe, code, n, self.spawn_at, 260, attack_to=self.home_xy)
        g.ui.toast(f"第 {self.wave} 波来了！" + ("（最后一波）" if self.wave == WAVES else ""), seconds=3)

    def push(self, g):
        """怪一直往主城冲；这一波清完了就发奖励。"""
        if not self.wave_live:
            return
        if self.alive_of(g, self.foe):
            self.attack_move_all(g, self.foe, self.home_xy)
            return
        self.wave_live = False
        reward = int((100 + 30 * self.wave) * self.bonus)
        j = g.jass
        me, st = j.GetLocalPlayer(), j.ConvertPlayerState(GOLD)
        j.SetPlayerState(me, st, j.GetPlayerState(me, st) + reward)
        if self.wave % 2 == 0:
            self.towers += 1
        g.ui.toast(f"第 {self.wave} 波守住了！+{reward} 金" + ("（提前叫的 ×1.5）" if self.bonus > 1 else ""), seconds=3)
        self.bonus = 1.0
        self.next_at = (g.clock() or 0.0) + WAVE_EVERY
        if self.wave >= WAVES:
            self.finish("win", f"守住了 {WAVES} 波，击杀 {self.kills}")

    # ── 放塔：抓一次地面点击 ──
    def start_placing(self, g):
        if self.placing:
            return
        if self.towers <= 0:
            g.ui.toast("没有箭塔可放了（每守住两波多一座）", seconds=2)
            return
        self.placing = True
        g.ui.toast("左键点地面放箭塔 · 右键取消", seconds=20, key="place")
        g.ui.mouse(self.place, capture=True, buttons=("left", "right"))

    def place(self, g, ev):
        g.ui.mouse(report=False)
        g.canvas.remove("place")
        self.placing = False
        if ev.button != "left":
            g.ui.toast("取消了", seconds=1)
            return
        if g.jass.CreateUnit(g.jass.GetLocalPlayer(), TOWER, float(ev.x), float(ev.y), 270.0):
            self.towers -= 1
            g.ui.toast("箭塔放好了", seconds=2)

    # ── 事件 ──
    def on_event(self, g, ev):
        if ev.kind == "unit.died" and self.hall and ev.handle == self.hall:
            self.finish("loss", f"主城在第 {self.wave} 波倒了（击杀 {self.kills}）")
        elif ev.kind == "killed" and ev.owner == self.foe:
            self.kills += 1

    # ── 界面 ──
    def draw_hud(self, g):
        now = g.clock() or 0.0
        alive = len(self.alive_of(g, self.foe))
        if self.wave_live:
            state = f"这一波还剩 {alive} 个"
            frac = 0.0
        elif self.wave < WAVES:
            left = max(0.0, self.next_at - now)
            state = f"下一波 {int(left)} 秒后"
            frac = 1.0 - left / (FIRST_WAVE if self.wave == 0 else WAVE_EVERY)
        else:
            state, frac = "全部守住了", 1.0
        self.hud(g, "无尽守城", [f"第 {self.wave}/{WAVES} 波 · {state}", f"击杀 {self.kills}",
                                 f"免费箭塔 {self.towers}（F8 放）", "F7 提前叫下一波（奖励 ×1.5）"])
        g.canvas.bar("mod.next", frac, screen=(-330, 300), width=300, height=10, color=(230, 180, 60), bg=(0, 0, 0, 150),
                     layer=31)

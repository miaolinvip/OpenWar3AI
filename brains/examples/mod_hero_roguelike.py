# -*- coding: utf-8 -*-
"""玩法模组示例：英雄肉鸽（hero-roguelike）。

你只有一个圣骑士，一波波怪从四面围上来；每升一级，屏幕中间弹出三张卡片三选一强化（选的时候游戏暂停）。
撑过 10 波算赢，英雄死了算输。你自己在游戏窗口里操作英雄，模组负责出题。单人局。

    python tools/play.py --bot brains/examples/mod_hero_roguelike.py --inst 9
    （或者远见「AI 方案」页：内置 → 英雄肉鸽（模组示例）→ 用到实例）

用到的底座能力（09-25）：g.ui.choice（可点的选项卡 + 暂停）、g.ui.hotkey、g.ui.toast、画板面板；
事件 hero.levelup / killed / unit.died / spell.cast / message（聊天打 -help）；JASS 刷怪、改英雄属性、给物品；
Mod 的定时器（every）、刷怪（spawn_ring / attack_move_all）、结束（finish）。
"""
from __future__ import annotations

import random

from openwar3 import Mod

WAVES = 10
WAVE_EVERY = 40.0            # 游戏秒
FIRST_WAVE = 12.0
# 每一波：[(单位, 个数)]
WAVE_UNITS = [
    [("ugho", 4)], [("ugho", 6)], [("ugho", 5), ("ucry", 1)],
    [("ogru", 4), ("ohun", 2)], [("ogru", 5), ("ohun", 3)], [("ogru", 6), ("okod", 1)],
    [("ucry", 4), ("uabo", 2)], [("uabo", 4), ("ucry", 4)], [("hkni", 4), ("hsor", 2), ("hmpr", 2)],
    [("uabo", 4), ("hkni", 4), ("okod", 2)],
]
UNIT_LIFE, UNIT_MAX_LIFE, UNIT_MANA, UNIT_MAX_MANA = 0, 1, 2, 3     # JASS UNIT_STATE_*


def _stat(setter: str, getter: str, n: int):
    def apply(g, h):
        j = g.jass
        getattr(j, setter)(h, getattr(j, getter)(h, False) + n, True)
    return apply


def _refill(g, h):
    j = g.jass
    j.SetUnitState(h, j.ConvertUnitState(UNIT_LIFE), j.GetUnitState(h, j.ConvertUnitState(UNIT_MAX_LIFE)))
    j.SetUnitState(h, j.ConvertUnitState(UNIT_MANA), j.GetUnitState(h, j.ConvertUnitState(UNIT_MAX_MANA)))


def _potions(g, h):
    for _ in range(2):
        g.jass.UnitAddItemById(h, "phea")


# (卡片标题, 说明, 怎么给)
REWARDS = [
    ("力量 +6", "更多生命和攻击", _stat("SetHeroStr", "GetHeroStr", 6)),
    ("敏捷 +6", "攻速和护甲", _stat("SetHeroAgi", "GetHeroAgi", 6)),
    ("智力 +6", "更多法力、回蓝更快", _stat("SetHeroInt", "GetHeroInt", 6)),
    ("治疗药水 ×2", "物品栏里多两瓶", _potions),
    ("攻击之爪 +12", "一件攻击装备", lambda g, h: g.jass.UnitAddItemById(h, "ratc")),
    ("技能点 +1", "多学一级技能", lambda g, h: g.jass.UnitModifySkillPoints(h, 1)),
    ("移动速度 +40", "跑得更快，好风筝", lambda g, h: g.jass.SetUnitMoveSpeed(h, g.jass.GetUnitMoveSpeed(h) + 40.0)),
    ("满血满蓝", "立刻回满", _refill),
]


class HeroRoguelike(Mod):
    name = "hero-roguelike"

    def __init__(self):
        super().__init__()
        self.hero = None              # 快照里的英雄（按句柄认：.handle / .addr）
        self.hero_h = 0               # JASS 句柄
        self.foe = 11
        self.wave = 0
        self.next_at = 0.0
        self.kills = 0
        self.spells = 0
        self.picks = 0                # 还欠几次三选一（一下升两级就是 2）
        self.choice = None

    # ── 开局 ──
    def on_start(self, g):
        super().on_start(g)
        self.foe = self.wave_player(g)
        heroes = g.my_heroes()
        if heroes:
            self.hero = heroes[0]
            self.hero_h = g.jass.handle_of(self.hero)
        else:
            hx, hy = self.home(g)
            self.hero = g.spawn("Hpal", hx + 250, hy - 250)
            if self.hero is None:
                self.finish("unknown", "刷不出英雄")
                return
            self.hero_h = self.hero.jass_handle
        g.ui.hotkey("F8", lambda g_, ev: self.help(g_))
        self.help(g)
        self.next_at = (g.clock() or 0.0) + FIRST_WAVE
        self.every(3.0, self.chase)
        self.every(1.0, self.draw_hud, first=0.2)

    def help(self, g):
        g.ui.toast("英雄肉鸽：怪一波波围上来，每升一级三选一强化 · 撑过 10 波 · F8 / 聊天打 -help 看这条", seconds=7)

    # ── 每拍 ──
    def on_tick(self, g):
        super().on_tick(g)
        if self.wave < WAVES and (g.clock() or 0.0) >= self.next_at:
            self.next_wave(g)

    def next_wave(self, g):
        self.wave += 1
        self.next_at = (g.clock() or 0.0) + WAVE_EVERY
        at = self.hero_pos(g)
        for code, n in WAVE_UNITS[self.wave - 1]:
            self.spawn_ring(g, self.foe, code, n, at, 1300, attack_to=at)
        g.ui.toast(f"第 {self.wave} 波来了！" + ("（最后一波）" if self.wave == WAVES else ""), seconds=3)

    def chase(self, g):
        """怪追着英雄走；最后一波清完就赢了。"""
        alive = self.alive_of(g, self.foe)
        if alive:
            self.attack_move_all(g, self.foe, self.hero_pos(g))
        elif self.wave >= WAVES:
            self.finish("win", f"撑过了 {WAVES} 波，击杀 {self.kills}")

    def hero_pos(self, g) -> tuple:
        s = g.snapshot()
        u = s.unit_by_handle(self.hero.handle) if s and self.hero else None
        return (u.x, u.y) if u else self.home(g)

    # ── 事件 ──
    def on_event(self, g, ev):
        if self.hero is None:
            return
        if ev.kind == "hero.levelup" and ev.handle == self.hero.handle:
            self.picks += max(1, ev.a - ev.b)
            self.offer(g, ev.a)
        elif ev.kind == "unit.died" and ev.handle == self.hero.handle:
            self.finish("loss", f"英雄倒在第 {self.wave} 波（击杀 {self.kills}）")
        elif ev.kind == "killed" and ev.source_addr == self.hero.addr:
            self.kills += 1
        elif ev.kind == "spell.cast" and ev.handle == self.hero.handle:
            self.spells += 1
        elif ev.kind == "message" and getattr(ev, "chat", None) and ev.chat["text"].strip().lower() == "-help":
            self.help(g)

    def offer(self, g, level: int):
        if self.picks <= 0 or (self.choice is not None and not self.choice.closed):
            return
        opts = random.sample(REWARDS, 3)
        self.choice = g.ui.choice(f"升到 {level} 级！选一个强化", [(t, d) for t, d, _ in opts], key="pick", pause=True,
                                  on_pick=lambda g_, i: self.take(g_, opts[i], level))

    def take(self, g, opt, level: int):
        opt[2](g, self.hero_h)
        self.picks -= 1
        g.ui.toast(f"获得：{opt[0]}", seconds=2)
        self.offer(g, level)                          # 一下升了好几级：接着选

    # ── 界面 ──
    def draw_hud(self, g):
        left = max(0, int(self.next_at - (g.clock() or 0.0))) if self.wave < WAVES else 0
        s = g.snapshot()
        u = s.unit_by_handle(self.hero.handle) if s and self.hero else None
        hp = f"{int(u.hp)}/{int(u.hp_max)}" if u else "?"
        self.hud(g, "英雄肉鸽", [
            f"第 {self.wave}/{WAVES} 波" + (f" · 下一波 {left} 秒" if self.wave < WAVES else " · 清完最后一波就赢"),
            f"场上的怪：{len(self.alive_of(g, self.foe))}",
            f"英雄 {u.level if u else '?'} 级 · 血 {hp}",
            f"击杀 {self.kills} · 施法 {self.spells}",
            "F8 帮助",
        ])

# -*- coding: utf-8 -*-
"""玩法模组（09-25）：方案不只是 AI（kind: bot），也可以是**一套规则**（kind: mod）—— 开局布置、按事件出招、屏幕上给玩家界面。

    from openwar3 import Mod, run

    class Survive(Mod):
        name = "survive"
        def on_start(self, g):
            super().on_start(g)                   # 单人局检查、压住电脑对手（neutralize_ai = True 时）
            self.every(30, self.wave)             # 每 30 游戏秒一波
            g.ui.hotkey("F7", lambda g, ev: self.wave(g))
        def wave(self, g):
            p = self.wave_player(g)               # 一个空槽玩家当「刷怪方」：没有电脑 AI 抢指挥
            self.spawn_ring(g, p, "ugho", 6, self.home(g), 1400, attack_to=self.home(g))
        def on_event(self, g, ev):
            if ev.kind == "unit.died" and ev.handle == self.town_handle:
                self.finish("loss", "主城被推了")

    run(Survive, inst=9, judge=False, fair=False)   # 或者做成方案：scheme.json 写 "kind": "mod"

和 Bot 一样的四个钩子（on_start / on_tick / on_event / on_end），多了：
  every(秒, fn) / after(秒, fn)  按**游戏时间**的定时器（暂停时不走），fn(g)
  finish(result, reason)        结束这一局（'win' / 'loss' / 'unknown'），运行器下一拍停，屏幕上给个结果面板
  hud(g, title, lines)          右上角一块面板；toast 用 g.ui.toast
  wave_player(g)                第一个空槽玩家（刷出来的怪归它：它和谁都不结盟、没有 AI）
  spawn_ring(g, player, code, n, center, radius, attack_to)  在一圈上刷 n 个单位（返回 JASS 句柄），可以顺手攻击移动
  alive_of(g, player) / attack_move_all(g, player, point)     某个玩家的活单位 / 全部攻击移动过去
  home(g)                       我方主城的位置
⚠ 模组改世界（刷单位、改属性）走 JASS 通道，只能单人局：on_start 查到有别的真人玩家就拒绝运行。
"""
from __future__ import annotations

import math

from .bot import Bot

HALLS = ("htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe")


class ModError(RuntimeError):
    pass


class Mod(Bot):
    name = "mod"
    mod_rules = True              # 运行器：不按对战规则判胜负（finish 报），不是公平模式
    neutralize_ai = True          # 开局把电脑对手压住（单位全部暂停、资源清零）：模组自己定规则，不要它来搅局
    single_player_only = True

    def __init__(self):
        self._timers: list = []    # [下次触发的游戏秒, 间隔（0 = 只一次）, fn]
        self._finish = None
        self._neutralized: set = set()
        self._next_neutralize = 0.0

    # ── 钩子（子类覆盖时记得 super().on_start(g) / super().on_tick(g)）──
    def on_start(self, g) -> None:
        self._clock_hint = g.clock() or 0.0
        if self.single_player_only:
            humans = [s for s in g.player_slots() if s.get("human") and s.get("state") == "playing"]
            if len(humans) > 1:
                raise ModError(f"这个模组只能单人局用（现在有 {len(humans)} 个真人玩家）：改世界的 JASS 会让别人不同步")
        if self.neutralize_ai:
            s = g.snapshot(max_age=0)
            for p, st in enumerate(s.slot_states or []):
                if p != s.me and st == "playing":
                    self._neutralized.add(p)
            self._keep_neutral(g)

    def on_tick(self, g) -> None:
        if self._finish:                          # 已经分出胜负：运行器下一拍就停，别再刷怪 / 给奖励
            return
        now = g.clock() or 0.0
        self._clock_hint = now
        due = [t for t in self._timers if t[0] <= now]
        for t in due:
            if t[1] > 0:
                t[0] = now + t[1]
            else:
                self._timers.remove(t)
            try:
                t[2](g)
            except Exception:  # noqa: BLE001 - 一个定时器出错不该把整个模组带崩（记下来，别的照常）
                import sys
                import traceback
                print(f"[mod {self.name}] 定时器 {getattr(t[2], '__name__', t[2])} 出错：", file=sys.stderr)
                traceback.print_exc()
            if self._finish:
                return
        if self._neutralized and now >= self._next_neutralize:
            self._keep_neutral(g)

    # ── 定时器（游戏时间）──
    def every(self, seconds: float, fn, first: float | None = None) -> None:
        """每 seconds 游戏秒调一次 fn(g)；first = 第一次在多少秒后（默认 = seconds）。
        ⚠ 子类自己写 __init__ 要先 super().__init__()。"""
        self._timers.append([self._clock_hint + (seconds if first is None else first), float(seconds), fn])

    def after(self, seconds: float, fn) -> None:
        self._timers.append([self._clock_hint + seconds, 0.0, fn])

    _clock_hint = 0.0             # on_tick 之外登记定时器时的「现在」（运行器每拍更新）

    # ── 结束 ──（finish(result, reason) 在 Bot 上；模组结束时 on_end 在屏幕中间画结果面板）
    linger_s = 6.0

    def on_end(self, g, reason: str) -> None:
        fin = self._finish or ("unknown", reason)
        result_panel(g, *fin)
        try:
            if g._ui is not None:
                g._ui.close()
        except Exception:  # noqa: BLE001 - 游戏已经没了也要能收尾
            pass

    # ── 常用 ──
    def hud(self, g, title: str, lines: list, key: str = "mod.hud") -> None:
        g.canvas.panel(key, title, [str(x) for x in lines], screen=(-330, 120), width=300, size=17, layer=30)

    def home(self, g) -> tuple:
        halls = [u for u in g.units("me") if u.type in HALLS]
        u = halls[0] if halls else (g.units("me") or [None])[0]
        return (u.x, u.y) if u else (0.0, 0.0)

    def wave_player(self, g) -> int:
        """第一个空槽玩家（刷怪方）。空槽玩家和谁都不结盟、没有电脑 AI，单位只听我们的命令。"""
        s = g.snapshot(max_age=0)
        for p, st in enumerate(s.slot_states or []):
            if st == "empty" and p != s.me:
                return p
        return 11

    def spawn_ring(self, g, player: int, code: str, n: int, center, radius: float, attack_to=None) -> list:
        """在 center 周围半径 radius 的一圈上刷 n 个 code，面朝圆心；给了 attack_to=(x, y) 就立刻攻击移动过去。
        返回 JASS 单位句柄（不等快照：一波几十个也不卡；要快照里的单位用 alive_of）。"""
        j = g.jass
        hp = j.Player(int(player))
        cx, cy = center
        base = (g.clock() or 0.0) * 0.7
        out = []
        for i in range(n):
            ang = base + 2 * math.pi * i / max(1, n)
            h = j.CreateUnit(hp, code, cx + radius * math.cos(ang), cy + radius * math.sin(ang),
                             (math.degrees(ang) + 180.0) % 360.0)
            if h:
                out.append(h)
                if attack_to is not None:
                    j.IssuePointOrder(h, "attack", float(attack_to[0]), float(attack_to[1]))
        if out:
            self._await_publish(g)
        return out

    @staticmethod
    def _await_publish(g, timeout: float = 0.3) -> None:
        """等运行时发布下一份世界状态（约 50 ms）：刚刷的怪要到下一次发布才进快照，这之前 alive_of 看不到它们 ——
        09-26 审查：定时器正好这时候判「这一波清完了」，守城提前叫波约 7% 的概率误判。"""
        import time
        try:
            start = g.world.read()
            n0 = start.publish_count if start else 0
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                s = g.world.read()
                if s is not None and s.publish_count != n0:
                    return
                time.sleep(0.01)
        except Exception:  # noqa: BLE001 - 替身没有世界块：不等
            return

    def alive_of(self, g, player: int) -> list:
        """某个玩家活着的单位（快照，全图；模组是裁判视角）。"""
        s = g.world.read()
        return [u for u in (s.units if s else []) if u.alive and u.owner == player]

    def attack_move_all(self, g, player: int, point) -> int:
        """把某个玩家的全部活单位攻击移动到 point（刷出来的怪追着目标走：隔几秒调一次）。返回下了几个。"""
        j = g.jass
        n = 0
        for u in self.alive_of(g, player):
            if j.IssuePointOrder(u, "attack", float(point[0]), float(point[1])):
                n += 1
        return n

    def _keep_neutral(self, g) -> None:
        """被压住的电脑：单位全部暂停、金木清零（每 5 游戏秒补一次：新出来的也按住）。"""
        self._next_neutralize = (g.clock() or 0.0) + 5.0
        j = g.jass
        for p in self._neutralized:
            hp = j.Player(p)
            j.SetPlayerState(hp, j.ConvertPlayerState(1), 0)      # PLAYER_STATE_RESOURCE_GOLD
            j.SetPlayerState(hp, j.ConvertPlayerState(2), 0)      # PLAYER_STATE_RESOURCE_LUMBER
        s = g.world.read()
        paused = self.__dict__.setdefault("_paused_units", set())
        for u in (s.units if s else []):
            if u.alive and u.owner in self._neutralized and u.handle not in paused:
                j.PauseUnit(u, True)                             # 单位对象直接传：jass 自己换成句柄
                paused.add(u.handle)                             # 暂停过的不再每 5 秒调一遍（一次 JASS 几十毫秒）


def result_panel(g, result: str, reason: str) -> None:
    """模组结束时屏幕中间的结果面板。"""
    title = {"win": "胜利！", "loss": "失败"}.get(result, "结束")
    color = {"win": (120, 230, 120), "loss": (240, 110, 110)}.get(result, (230, 230, 230))
    try:
        g.canvas.text("mod.result", f"{title}\n{reason}", frac=(0.5, 0.35), center=True, size=30, bold=True, color=color,
                      bg=(8, 10, 16, 225), border=(196, 156, 64, 235), radius=12, layer=90)
    except Exception:  # noqa: BLE001 - 结束画面画不出来不该让收尾出错
        pass

# -*- coding: utf-8 -*-
"""Bot 基类 + 运行器。写一个 bot = 继承 Bot，实现 on_tick。

    from openwar3 import Bot, run

    class Hello(Bot):
        def on_tick(self, g):
            for w in g.idle_workers():
                mine = g.nearest(g.gold_mines(), w)
                if mine:
                    g.gather(w, mine)

    run(Hello, inst=5)          # 或命令行：python -m openwar3 run my_bot.py --inst 5

节奏：on_tick 按 hz 调（默认 5 次/墙钟秒）。快照是零等待的，命令走快车道约 1 帧，
所以一拍的耗时基本是你自己的计算 —— 一拍超过 1/hz 就自动顺延，不会越积越多。
"""
from __future__ import annotations

import time
import traceback

from .game import Game


class Bot:
    """覆盖需要的钩子即可。g 是 Game。"""
    name = "bot"

    def on_start(self, g: Game) -> None:
        """进局后第一拍之前调一次。"""

    def on_tick(self, g: Game) -> None:
        """每一拍调一次。"""

    def on_event(self, g: Game, ev) -> None:
        """每拍 on_tick 之前，把上一拍以来的事件逐条交给你（单位死亡、掉血、换订单、英雄升级、物品出现/消失……）。"""

    def on_end(self, g: Game, reason: str) -> None:
        """这一局结束（游戏进程没了 / 我方没单位了 / 手动停）时调一次。"""

    linger_s = 0.0                # 分出胜负、发结束令之前在结果画面停几秒（模组给玩家看结果面板用）

    def finish(self, result: str = "unknown", reason: str = "自己结束") -> None:
        """结束这一局（09-25）：运行器下一拍停，run_stats 里的 result / reason 就是这里给的（'win' / 'loss' / 'unknown'）。
        对战规则之外的胜负（模组的「守住 10 波」「英雄死了」）用它报。"""
        self._finish = (result, reason)


def run(bot_cls, inst: int | None = None, pid: int | None = None, hz: float = 5.0,
        max_minutes: float = 60.0, fast: bool = True, verbose: bool = True,
        fair: bool = False, player: int | None = None, end_game: bool = False, stuck_s: float = 300.0,
        judge: bool = True) -> str:
    """连游戏 -> 等进局 -> 循环 on_tick -> 返回结束原因。一局一次；要连打多局在外面套循环。
    fair=True：只看得见视野里的东西（没有迷雾作弊，擂台就是这个规则）；player=N：以 N 号玩家的身份指挥（AI 对 AI）。
    end_game=True：分出胜负后发结束令（动作 22）—— 胜利/失败对话框会一直停着，farm -Keep 要等游戏退了才开下一局
      （方案运行器 tools/run_scheme.py 开着它）。
    stuck_s：游戏时钟连续这么多墙钟秒不走就当这局结束。暂停（人按 Pause / g.pause）也是时钟不走，所以默认给得宽。
    judge=False：不按对战规则判胜负（RPG / 自定义地图：英雄死了能复活、没有"建筑全没了算输"），
      只在游戏退出 / 时钟停太久 / 到时间时结束，也不发结束令 —— 玩伴、RPG 辅助用它。"""
    from w3fast import FastLaneUnavailable
    bot = bot_cls() if isinstance(bot_cls, type) else bot_cls
    if getattr(bot, "mod_rules", False):          # 玩法模组（09-25）：胜负它自己定（finish），要改世界所以不是公平模式
        judge, fair = False, False
    g = Game(inst=inst, pid=pid, fast=fast, fair=fair, player=player)
    say = print if verbose else (lambda *a, **k: None)
    say(f"[{bot.name}] 连上 pid={g.pid}（W3P：推送快照 + 语义命令）")

    t0 = time.monotonic()
    zero_since, pressed_at = None, 0.0
    while True:                                   # 等进局：快照说在局里、时钟走过 1.5 秒
        s = g.snapshot()
        c = g.clock()
        if s and s.in_game and c and c >= 1.5:
            break
        # 很多 RPG / 剧情地图载入完停在「按下任意键以继续」：快照已经在局里、时钟一直是 0（09-24 WarChasers 实测）
        now = time.monotonic()
        if s and s.in_game and not c:
            zero_since = zero_since or now
            if now - zero_since > 3 and now - pressed_at > 5:
                pressed_at = now
                if g.press_to_continue():
                    say(f"[{bot.name}] 载入画面在等按键，按了一下空格")
        else:
            zero_since = None
        if now - t0 > 120:
            return "等了两分钟还没进局"
        time.sleep(0.2)

    join_clock = g.clock() or 0.0                 # 接手时这局打了多久（方案运行器据此判「半路接手」）
    bot.on_start(g)
    period = 1.0 / max(0.1, hz)
    end_at = time.monotonic() + max_minutes * 60
    ticks, errors, reason, max_errors = 0, 0, "到时间了", 0
    last_pub, last_pub_at, foe_seen, next_foe_check = None, time.monotonic(), False, 0.0
    last_clock, clock_at, paused_said, lane_down_since, had_building = None, time.monotonic(), False, None, False
    result = "unknown"
    try:
        while time.monotonic() < end_at:
            start = time.monotonic()
            s = g.snapshot(max_age=0)
            if s is None:
                reason = "快照断了（游戏退出？）"
                break
            if s.publish_count != last_pub:
                last_pub, last_pub_at = s.publish_count, start
            elif start - last_pub_at > 10:            # 世界块 10 秒没更新：游戏进程没了（共享内存还映射着，读到的是最后一份）
                reason = "世界停止更新（游戏退出？）"
                break
            # 暂停（人按 Pause 键 = 动作 57、g.pause）：世界块照发、快车道照收命令，只有游戏时钟不走（09-24 两种都实测过）。
            # 胜利/失败对话框也是时钟不走 —— 两者只能靠「谁的建筑没了」分开，分不开的按暂停等，超过 stuck_s 才算结束
            if s.clock != last_clock:
                last_clock, clock_at = s.clock, start
                if paused_said:
                    say(f"[{bot.name}] 游戏继续")
                    paused_said = False
            frozen = start - clock_at
            if frozen > 3 and not paused_said:
                say(f"[{bot.name}] 游戏时钟不走了（暂停中？），等着")
                paused_said = True
            if frozen > stuck_s:
                reason = f"游戏时钟停了 {int(frozen)} 秒"
                break
            if judge and not g.units("me"):
                reason, result = "我方没有单位了", "loss"
                break
            # War3 对战的判负规则是建筑全没了（兵还活着也判负，失败对话框出来、时钟停住）—— 只看单位会一直等下去
            if judge and g.my_buildings():
                had_building = True
            elif judge and had_building:
                reason, result = "我方没有建筑了", "loss"
                break
            if judge and start >= next_foe_check:     # 对手还有没有建筑（按全图、不受公平模式影响 —— 这是裁判的判断，不给 bot 看）
                next_foe_check = start + 2.0
                raw = g.world.read()
                me = s.me
                foes = [u for u in (raw.units if raw else []) if u.alive and u.owner < 12 and u.owner != me
                        and g.is_building(u)]
                if foes:
                    foe_seen = True
                elif foe_seen:
                    reason, result = "对手没有建筑了", "win"
                    break
            try:
                for ev in g.events():
                    bot.on_event(g, ev)
                bot.on_tick(g)
            except FastLaneUnavailable:
                # 快车道不收命令：游戏在收尾（结束令之后、进程在退）—— 不是 bot 的 bug，不计出错；30 秒还这样就收工。
                # 09-24 实机：结束令之后 macro 连抛 20 次，被记成「on_tick 连续出错」
                if lane_down_since is None:
                    lane_down_since = start
                    say(f"[{bot.name}] 快车道不收命令了（游戏在收尾？）")
                elif start - lane_down_since > 30:
                    reason = "游戏 30 秒不接受命令（在收尾？）"
                    break
            except Exception:  # noqa: BLE001 - bot 的 bug 不该打断整局；连着错 20 拍才停
                errors += 1
                max_errors += 1
                say(traceback.format_exc(limit=3))
                if errors >= 20:
                    reason = "on_tick 连续出错"
                    break
            else:
                errors, lane_down_since = 0, None
            fin = getattr(bot, "_finish", None)
            if fin:                                   # bot / 模组自己宣布结束（09-25）
                result, reason = fin
                break
            ticks += 1
            left = period - (time.monotonic() - start)
            if left > 0:
                time.sleep(left)
    except KeyboardInterrupt:
        reason = "手动停止"
    try:
        bot.on_end(g, reason)
    except Exception:  # noqa: BLE001
        say(traceback.format_exc(limit=3))
    st = g.status()
    # 给方案运行器 / 跑批用：这一局怎么结束的（result = win / loss / unknown）
    bot.run_stats = {"reason": reason, "result": result, "ticks": ticks, "errors": max_errors,
                     "gameMinutes": round((g.clock() or 0) / 60.0, 2), "joinClock": round(join_clock, 1)}
    say(f"[{bot.name}] 结束：{reason}（{ticks} 拍；快车道 {st['fastLane']}；运行时采集 {st['collectUs']}us/次）")
    if end_game and result in ("win", "loss"):
        if getattr(bot, "linger_s", 0):
            time.sleep(float(bot.linger_s))
        g.end_game()
        say(f"[{bot.name}] 已发结束令（胜负已分，关掉结算画面让 farm 开下一局）")
    return reason

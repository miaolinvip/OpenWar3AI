# -*- coding: utf-8 -*-
"""游戏里左上角按钮栏 + 任务面板当日志面板（09-18）：

  需求：左上角「任务」按钮显示 War3AI-x-xx（x 实例号、xx 对局号，不带空格），查日志时能直接对上是哪一场；
  「盟友」按钮显示游戏已开始的时间 mm:ss；原本灰色的按钮改成可点，点开能看本局日志等信息。

  任务按钮 = 「War3AI-实例号-对局号」（对局号 = 对局文件名 match-日期-时刻-NNN-xxxx 里的 NNN）
  盟友按钮 = 游戏已开始的时间 mm:ss（按游戏时钟）
  点任务按钮 = 游戏自己的任务面板（F9），里面是我们建的任务：「对局日志」「当前状态」，描述就是内容，定时刷新。
    游戏里有任务时引擎自己把任务按钮设成可点 —— 所以「变成可点」不用我们管；
    ⚠ 引擎刷新任务时会把按钮文字改回「任务 (F9)」，所以按钮文字定时重写（09-19 起 3 秒一次，我们自己刷新任务后立刻重写）。
    ⚠ 单人局任务面板开着时**游戏暂停**（实测 2 秒墙钟游戏时钟只走 0.025 秒）—— 监督器「时钟停了就判这局结束」
      要先问 user_paused（当前打开的面板是任务/盟友/记录面板 = 人在看），见 run_brain。

09-23 P3：全部走 W3P 的 HUD 命令（运行时里做：三个文字对象都设、任务每局建一次、描述分段拼好再写），
这里不再有任何引擎地址。命令语义见 native/src/ui_control_protocol.h 的 kHud*：
  hud_button        extra 0 任务按钮 / 1 盟友按钮，mode bit0 = 顺便设成可点，文字 UTF-8
  hud_quest         extra = 任务槽 0..7；mode 0 标题 / 1 描述从这一段开始 / 2 追加一段 / 3 写进去
  hud_quest_refresh 让任务面板按新内容重画（引擎会把任务按钮文字改回「任务 (F9)」，所以之后要重写按钮）
  hud_user_dialog   values[0] = 1：人打开了任务/盟友/记录面板（菜单不算：胜负画面也可能走它）
"""
from __future__ import annotations

import time

from w3fast import command as _C
from w3fast import text_chunks

# ★ 09-19 steptime 实测（1 号 5 局 1889 轮）：大脑 93% 的墙钟在等控制通道，其中 HUD 占 33%。
#   那时一次请求 20~40 ms；W3P 一帧内回执、一批多条同一帧做完，但节奏照旧（观众看到的一样）：
#   任务按钮强制重写 3 秒一次（我们自己刷新任务后立刻重写）；时间按钮每秒变；查面板 1 秒一次。
LABEL_EVERY = 3.0        # 墙钟秒：任务按钮文字强制重写 + 盟友按钮启用的间隔（引擎刷新任务会改回「任务 (F9)」）
CLOCK_EVERY = 1.0        # 墙钟秒：盟友按钮上的游戏时间最多这么频繁重写
QUEST_EVERY = 3.0        # 墙钟秒：任务描述刷新间隔
PAUSE_EVERY = 1.0        # 墙钟秒：看一次是不是人打开了面板（时钟停 15 秒才判结束，1 秒的延迟无所谓）
DESC_MAX = 3000          # 描述最多这么多字节（UTF-8；运行时每个任务攒描述的缓冲是 4096）
BUTTON = {"quests": 0, "allies": 1}
QUEST_SLOTS = 8


def clock_text(secs: float) -> str:
    """游戏秒 -> mm:ss（超过 99 分钟照写分钟数）。"""
    s = max(0, int(secs))
    return f"{s // 60:02d}:{s % 60:02d}"


def clip_utf8(text: str, limit: int = DESC_MAX) -> str:
    """超长就从前面砍（留最新的），按字节算、不切断字符。"""
    b = text.encode("utf-8")
    if len(b) <= limit:
        return text
    return b[-limit:].decode("utf-8", errors="ignore").split("\n", 1)[-1]


class Hud:
    """每局一个。lane = w3fast.FastLane（有 commands()；w3cmd.Cmd 的 .lane 就是）。任务槽按键的先后分配。"""

    def __init__(self, lane, clock=time.monotonic):
        self.lane = lane
        self.clock = clock
        self.shown: dict = {}
        self.label_at = -1e9
        self.clock_at = -1e9
        self.quest_at = -1e9
        self.pause_at = -1e9
        self.quests: dict = {}               # 键 -> 任务槽 0..7（标题写过 = 在这里）
        self.quest_text: dict = {}
        self.user_paused = False
        self.errors = 0
        self._batch = None                   # update() 期间攒一批：[(calls, 失败时撤销, 拿到回执后)]

    def _send(self, calls, undo=None, then=None) -> bool:
        """发一组命令。update() 期间先攒着（乐观地当它成了），update 末尾一批发出去，失败的那组再撤销（undo）。
        ★ 09-23 体检：游戏内按钮一局 3.3 万次命令 × 8 ms（大脑时间 13%），一次刷新 3~8 条各等一次 —— 合成一批只等一次。"""
        if self._batch is not None:
            self._batch.append((calls, undo, then))
            return True
        rs = self.lane.commands(calls)
        ok = all(r is not None and r.ok for r in rs)
        if not ok:
            self.errors += 1
            if undo:
                undo()
        if then:
            then(rs)
        return ok

    def _flush(self) -> None:
        batch, self._batch = self._batch or [], None
        calls = [c for grp, _u, _t in batch for c in grp]
        if not calls:
            return
        rs = self.lane.commands(calls)
        k = 0
        for grp, undo, then in batch:
            part = rs[k:k + len(grp)]
            k += len(grp)
            if not (len(part) == len(grp) and all(r is not None and r.ok for r in part)):
                self.errors += 1
                if undo:
                    undo()
            if then:
                then(part)

    def set(self, which: str, text: str, force: bool = False, enable: bool = False) -> bool:
        if not force and self.shown.get(which) == text:
            return True
        if not self._send([_C("hud_button", extra=BUTTON[which], mode=1 if enable else 0, text=text)],
                          undo=lambda: self.shown.pop(which, None)):
            return False                            # 菜单里 / 还没进局：运行时找不到按钮栏
        self.shown[which] = text
        return True

    def quest(self, key: str, title: str, text: str) -> bool:
        """建（每局一次，运行时建）并写一个任务：标题 + 描述。描述没变不重写。返回这次有没有写。"""
        slot = self.quests.get(key)
        if slot is None:
            slot = len(self.quests)
            if slot >= QUEST_SLOTS or not self._send([_C("hud_quest", extra=slot, mode=0, text=title)],
                                                     undo=lambda: self.quests.pop(key, None)):
                return False
            self.quests[key] = slot
        text = clip_utf8(text)
        if self.quest_text.get(key) == text:
            return False
        parts = text_chunks(text)
        calls = [_C("hud_quest", extra=slot, mode=1 if i == 0 else 2, text=p) for i, p in enumerate(parts)]
        calls.append(_C("hud_quest", extra=slot, mode=3))
        if not self._send(calls, undo=lambda: self.quest_text.pop(key, None)):
            return False
        self.quest_text[key] = text
        return True

    def paused_by_user(self) -> bool:
        """当前打开的面板是任务/盟友/记录面板 = 人在看（单人局会暂停）。运行时一帧内判好，这里一条查询。"""
        r = self.lane.commands([_C("hud_user_dialog")])[0]
        return bool(r is not None and r.ok and r.values[0])

    def poll_pause(self) -> bool:
        """每 PAUSE_EVERY 秒问一次有没有人打开面板，返回 user_paused。
        ★ 09-19 拆出来单独调：问题快捷键（DLL 动作 57）打开任务面板暂停游戏，HUD 按钮关着（hud_labels 关）也要认，
          不然监督器 5 秒后按「时钟停了」把这局结束掉。监督器每轮都调它，不管 HUD 开没开。"""
        now = self.clock()
        if now - self.pause_at >= PAUSE_EVERY:
            self.pause_at = now
            try:
                self.user_paused = self.paused_by_user()
            except Exception:  # noqa: BLE001
                self.user_paused = False
        return self.user_paused

    def update(self, label: str, game_secs: float, panels: list | None = None) -> None:
        """label：任务按钮文字；panels：[(键, 标题, 内容), ...] 任务面板里的几项。"""
        now = self.clock()
        self._batch = []
        try:
            self._update(now, label, game_secs, panels)
        finally:
            self._flush()

    def _update(self, now, label, game_secs, panels) -> None:
        if now - self.pause_at >= PAUSE_EVERY:          # 查面板开没开也放进这一批（poll_pause 同一个节奏）
            self.pause_at = now

            def got(rs):
                self.user_paused = bool(rs and rs[0] is not None and rs[0].ok and rs[0].values[0])
            self._send([_C("hud_user_dialog")], then=got)
        force = now - self.label_at >= LABEL_EVERY
        # 任务面板先刷新：刷新会把按钮文字改回「任务 (F9)」，要紧接着重写 —— 和定时重写并成一次
        # （09-19 之前两个都是 3 秒、总在同一轮，同一轮把任务按钮写两遍 = 多 3 次通道请求）
        if panels and now - self.quest_at >= QUEST_EVERY:
            self.quest_at = now
            wrote = False
            for key, title, text in panels:
                wrote = self.quest(key, title, text) or wrote
            if wrote:
                self._send([_C("hud_quest_refresh")])
                force = True
        if force:
            self.label_at = now
        self.set("quests", label, force=force)
        clock = clock_text(game_secs)
        if force or (now - self.clock_at >= CLOCK_EVERY and self.shown.get("allies") != clock):
            self.clock_at = now
            # 盟友按钮每次强制重写时顺便设成可点（09-18 时间按钮也要显示成可点击的样式；单人局引擎把它设成灰的）
            self.set("allies", clock, force=force, enable=force)


# ---------------------------------------------------------------- 面板内容
LOG_LINES = 40
_NOISE_PREFIX = ("train ", "build ", "英雄看戏", "续建", "在路上过期")


def join_lines(lines) -> str:
    return "\n".join(lines) or "（还没有记录）"


def log_worthy(text: str) -> bool:
    """对局日志只收决策和事件：执行命令（「N/M 个 -> (x,y)」）、造兵造房子、诊断埋点不收。"""
    return bool(text) and "个 -> (" not in text and not text.startswith(_NOISE_PREFIX)


def status_text(w, attack_state: str, defense_mode: str, game_secs: float, why: dict | None = None) -> str:
    """「当前状态」面板：一屏看懂现在在干什么、为什么。"""
    lines = [
        f"游戏时间 {clock_text(game_secs)}　出击层 {attack_state}　守家 {defense_mode}",
        f"强度 我方 {w.own_strength:.1f} / 敌方 {w.foe_strength:.1f}　进攻性 {w.aggression:.1f}",
        f"金 {w.gold}　木 {w.lumber}　人口 {w.food_used}/{w.food_cap}　农民 {len(w.my_workers)}　兵 {len(w.my_army)}　英雄 {len(w.my_heroes)}　大厅 {len(w.my_halls)}",
    ]
    if why:
        lines.append("出击层这一拍的理由：" + "、".join(f"{k}×{v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])[:6]))
    return "\n".join(lines)

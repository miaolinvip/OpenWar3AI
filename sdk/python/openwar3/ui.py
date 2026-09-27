# -*- coding: utf-8 -*-
"""界面与输入（09-25）：可点的按钮 / 选项卡、热键、点地面选位置、鼠标指着哪儿 —— 建在画板和运行时的输入块上。

    ui = g.ui                                               # 第一次用时运行时接管窗口过程（点击 / 热键 / 地面点击）
    ui.button("shop", "买一瓶药（50 金）", screen=(40, 300), on_click=lambda g, ev: buy_potion(g))
    c = ui.choice("升级了！选一个奖励", [("力量 +5", "更能扛"), ("攻速 +20%", "更能打"), ("召唤狼", "多一个帮手")],
                  pause=True)                               # 屏幕中间一排卡片；pause=True 选的时候游戏暂停
    i = c.wait(timeout=30)                                  # 0 / 1 / 2，超时 None（也可以不等，之后看 c.picked）
    ui.hotkey("F5", lambda g, ev: g.say(hero, "收到！"))    # 热键（默认吞掉，游戏收不到）
    ui.hotkey("ctrl+shift+Q", on_press=..., swallow=False)
    xy = ui.pick_point("点地面：塔建在哪？")                  # 下一次左键点地面 -> (x, y)；Esc / 超时 -> None
    ui.cursor()                                             # {'screen': (x, y), 'world': (x, y, z) 或 None, 'hover': key}
    ui.toast("第 3 波来了！", seconds=3)

回调 (g, ev) 在你调 g.events() 时触发（Bot / Mod 的运行器每拍都调）。等待类方法（choice().wait、pick_point）
自己泵事件；等待期间读到的别的事件不会丢，下一次 g.events() 照样给你。
全部是本机输入 + 本机绘制：不进指令流，多人局也安全（但回调里做的事要是改世界，那还是只能单机）。
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

import w3input as I

CARD_BG = (18, 20, 30, 230)
CARD_BORDER = (196, 156, 64, 235)
CARD_HOVER = (255, 220, 120, 60)
TITLE_BG = (10, 12, 18, 220)
BUTTON_BG = (24, 28, 40, 225)
BUTTON_BORDER = (120, 140, 180, 220)
LOCAL_KINDS = ("ui.click", "ui.hover", "hotkey", "mouse.world")


def _safe(cb, *args) -> None:
    """跑一个用户回调：出错打印出来，不往外抛 —— 一个按钮回调的 bug 不该让同一批的 unit.died / game.ended 跟着丢。"""
    try:
        cb(*args)
    except Exception:  # noqa: BLE001
        print("[openwar3.ui] 回调出错（这一个跳过，别的事件照常）：", file=sys.stderr)
        traceback.print_exc()


class Choice:
    """ui.choice 返回的：.picked（选中的下标，没选 = None）、.wait(timeout)、.close()。"""

    def __init__(self, ui, key: str, options: list, on_pick, paused: bool):
        self.ui, self.key, self.options, self.on_pick = ui, key, options, on_pick
        self.picked: int | None = None
        self.closed = False
        self._paused = paused

    def wait(self, timeout: float = 60.0) -> int | None:
        """泵事件直到选了或超时。返回下标（超时 None，卡片收起）。"""
        end = time.monotonic() + timeout
        try:
            while self.picked is None and not self.closed and time.monotonic() < end:
                self.ui._pump()
                time.sleep(0.03)
        finally:
            if self.picked is None:
                self.close()                  # 超时 / 出错：卡片收起、暂停恢复（以前出错会把游戏一直停在暂停）
        return self.picked

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        c = self.ui.canvas
        with c.batch():
            c.remove(f"{self.key}.title")
            for i in range(len(self.options)):
                c.remove(f"{self.key}.{i}")
                self.ui._click.pop(f"{self.key}.{i}", None)
        self.ui._choices.pop(self.key, None)
        if self._paused:
            self.ui.g.pause(False)
            self._paused = False


class UI:
    def __init__(self, g):
        self.g = g
        r = g.lane.commands([g._w3fast.command("input_enable", extra=1)], timeout=5.0)[0]
        if not r.ok:
            from .canvas import enable_hint
            raise I.InputUnavailable(f"运行时开不了输入：{enable_hint(g, r.reason)}")
        self.block = I.InputBlock(g.pid)
        self.block.attach()               # 登记成输入客户端：别人 close() 时知道还有人在用，不会让运行时放开窗口过程
        self.canvas = g.canvas
        self._click: dict = {}            # 画板 key -> 回调(g, ev) 或 None
        self._hotkeys: dict = {}          # 热键 id -> (vk, mods, flags, 回调, 写法)
        self._next_hotkey = 1
        self._choices: dict = {}          # key -> Choice
        self._mouse_cb = None
        self._pick = None                 # pick_point 等着的那一次：{'xy': ..., 'cancel': ...}
        self._toasts: dict = {}           # key -> 到期时刻
        self.hover: str | None = None     # 现在悬停的画板 key
        self.clicks: list = []            # 没有回调的点击：[(key, ev)]，自己取走

    # ── 控件 ──
    def button(self, key, text: str, *, screen=None, frac=None, world=None, unit=None, lift: float = 0.0,
               on_click=None, size: float = 0, width: float = 0, color=(240, 240, 240), bg=BUTTON_BG,
               border=BUTTON_BORDER, hover=None, radius: float = 6, center: bool = False, layer: int = 10,
               over_ui: bool = False, bold: bool = False) -> str:
        """可点的文字按钮。on_click(g, ev)；不给回调的点击进 ui.clicks。"""
        key = str(key)
        self.canvas.text(key, text, screen=screen, frac=frac, world=world, unit=unit, lift=lift, color=color, bg=bg,
                         border=border, size=size, width=width, radius=radius, center=center, layer=layer,
                         over_ui=over_ui, bold=bold, clickable=True, hover=hover)
        self._click[key] = on_click
        return key

    def remove(self, key) -> None:
        self.canvas.remove(key)
        self._click.pop(str(key), None)

    def choice(self, title: str, options: list, *, key: str = "choice", on_pick=None, pause: bool = False,
               width: float = 250, size: float = 18, y_frac: float = 0.42) -> Choice:
        """屏幕中间一排选项卡片（肉鸽「三选一」那种）。options：['文字', ...] 或 [('标题', '说明'), ...]。
        on_pick(g, index)；pause=True 选的时候游戏暂停（单人局）。同一个 key 再调一次会先收起上一次的。"""
        if key in self._choices:
            self._choices[key].close()
        back_w = (self.block.state().get("back") or (1920, 1080))[0] or 1920
        n = len(options)
        gap = 28
        total = n * width + (n - 1) * gap
        left = (back_w - total) / 2
        ch = Choice(self, key, list(options), on_pick, pause)
        c = self.canvas
        with c.batch():
            c.text(f"{key}.title", title, frac=(0.5, y_frac - 0.13), center=True, size=size + 10, bold=True,
                   color=(255, 224, 140), bg=TITLE_BG, border=CARD_BORDER, radius=8, layer=50)
            for i, opt in enumerate(options):
                head, desc = (opt, "") if isinstance(opt, str) else (str(opt[0]), str(opt[1]) if len(opt) > 1 else "")
                cx = left + i * (width + gap) + width / 2
                body = f"{head}\n\n{desc}" if desc else head
                c.text(f"{key}.{i}", body, screen=(cx, self._y_px(y_frac)), center=True, width=width,
                       size=size, color=(245, 245, 245), bg=CARD_BG, border=CARD_BORDER, radius=10, layer=51,
                       clickable=True, hover=CARD_HOVER)
                self._click[f"{key}.{i}"] = None
        self._choices[key] = ch
        if pause:
            self.g.pause(True)
        return ch

    def _y_px(self, frac: float) -> float:
        back_h = (self.block.state().get("back") or (1920, 1080))[1] or 1080
        return frac * back_h

    def toast(self, text: str, seconds: float = 3.0, key: str = "toast", color=(255, 236, 170)) -> None:
        """屏幕上方一行提示，seconds 秒后自己消失（在 g.events() 里收）。"""
        self.canvas.text(key, text, frac=(0.5, 0.12), center=True, size=24, bold=True, color=color,
                         bg=(0, 0, 0, 170), radius=8, layer=60)
        self._toasts[key] = time.monotonic() + seconds
        expire = getattr(self.canvas, "expire", None)
        if expire is not None:
            expire(key, seconds)          # 到点自己消失，不靠有人调 g.events()

    # ── 热键 / 鼠标 ──
    def hotkey(self, spec: str, on_press=None, *, swallow: bool = True, repeat: bool = False) -> int:
        """登记热键（'F5' / 'ctrl+shift+Q' / 'alt+1'），返回 id。on_press(g, ev)。
        swallow=True：游戏收不到这个键（也收不到它生成的字符）。⚠ 不带修饰键的字母 / 数字会和聊天输入、游戏快捷键打架，
        优先用 F5~F8 这类游戏没占的键或 ctrl / alt 组合。"""
        vk, mods = I.parse_key(spec)
        alloc = getattr(self.block, "alloc_hotkey_id", None)
        if alloc is not None:
            hid = alloc()                 # 所有客户端共用的计数器（以前每个进程从 1 编，会撞）
        else:                             # 测试替身
            hid = self._next_hotkey
            self._next_hotkey += 1
        self._hotkeys[hid] = (vk, mods, (I.SWALLOW if swallow else 0) | (I.REPEAT if repeat else 0), on_press, spec)
        self._sync_hotkeys()
        return hid

    def remove_hotkey(self, hid: int) -> None:
        if self._hotkeys.pop(hid, None) is not None:
            self._sync_hotkeys()

    def _sync_hotkeys(self) -> None:
        self.block.set_hotkeys([(hid, vk, mods, fl) for hid, (vk, mods, fl, _cb, _s) in self._hotkeys.items()])

    def mouse(self, on_click=None, *, report: bool = True, capture: bool = False, buttons=("left",)) -> None:
        """点在世界上的鼠标：report=True 发 mouse.world 事件（on_click(g, ev)，ev.button 是 'left' / 'right'，ev.x/ev.y 地面坐标），
        capture=True 同时吞掉（游戏收不到）。buttons 管哪几个键，比如 ('left', 'right')：左键放、右键取消。
        report 和 capture 都给 False = 关掉。"""
        flags = 0
        for b in buttons:
            left = b == "left"
            if report or capture:
                flags |= I.MOUSE_REPORT_LEFT if left else I.MOUSE_REPORT_RIGHT
            if capture:
                flags |= I.MOUSE_CAPTURE_LEFT if left else I.MOUSE_CAPTURE_RIGHT
        self._mouse_cb = on_click if flags else None
        self.block.set_mouse(flags)

    def pick_point(self, prompt: str = "点地面选一个位置（Esc 取消）", timeout: float = 30.0) -> tuple | None:
        """等玩家在地面上左键点一下（这一下游戏收不到），返回 (x, y)；按 Esc 或超时返回 None。"""
        old = self.block.mouse_flags()
        self.block.set_mouse(old | I.MOUSE_REPORT_LEFT | I.MOUSE_CAPTURE_LEFT)
        esc = self.hotkey("esc", swallow=True)
        self._pick = {"xy": None, "cancel": False, "esc": esc}
        self.toast(prompt, seconds=timeout, key="__pick")
        end = time.monotonic() + timeout
        try:
            while time.monotonic() < end and self._pick["xy"] is None and not self._pick["cancel"]:
                self._pump()
                time.sleep(0.02)
            return self._pick["xy"]
        finally:
            self.block.set_mouse(old)
            self.remove_hotkey(esc)
            self.canvas.remove("__pick")
            self._toasts.pop("__pick", None)
            self._pick = None

    def cursor(self) -> dict:
        """{'screen': (x, y) 或 None, 'world': (x, y, z) 或 None（指着界面 / 窗口外）, 'hover': 画板 key 或 None}。"""
        st = self.block.state()
        return {"screen": st["cursor"], "world": st["world"], "hover": self.canvas.key_of(st["hover"]) if st["hover"] else None}

    # ── 事件分发（Game.events() 调）──
    def handle(self, events: list) -> None:
        now = time.monotonic()
        for k, t in list(self._toasts.items()):
            if now >= t:
                self.canvas.remove(k)
                self._toasts.pop(k, None)
        for ev in events:
            if getattr(ev, "_handled", False):          # 等待时（_pump）已经分发过的，不再分发第二次
                continue
            if ev.kind in LOCAL_KINDS:
                ev._handled = True
            if ev.kind == "ui.click":
                key = self.canvas.key_of(ev.item_id)
                ev.key = key
                if key is None:
                    continue
                base, _, idx = key.rpartition(".")
                ch = self._choices.get(base)
                if ch is not None and idx.isdigit():
                    ch.picked = int(idx)
                    ch.close()
                    if ch.on_pick:
                        _safe(ch.on_pick, self.g, ch.picked)
                    continue
                cb = self._click.get(key)
                if cb:
                    _safe(cb, self.g, ev)
                else:
                    self.clicks.append((key, ev))
                    del self.clicks[:-64]
            elif ev.kind == "ui.hover":
                self.hover = self.canvas.key_of(ev.item_id) if ev.item_id else None
                ev.key = self.hover
            elif ev.kind == "hotkey":
                # 按「键码 + 修饰键」认：好几个客户端登记同一个键时，运行时合并成一条、事件里只带第一个登记的 id
                vk, mods = ev.b, ev.extra
                if self._pick is not None and (vk, mods) == (0x1B, 0):
                    self._pick["cancel"] = True
                    continue
                mine = [h for h in self._hotkeys.values() if (h[0], h[1]) == (vk, mods)]
                ev.key = mine[0][4] if mine else None
                for h in mine:
                    if h[3]:
                        _safe(h[3], self.g, ev)
            elif ev.kind == "mouse.world":
                if self._pick is not None and ev.button == "left":
                    self._pick["xy"] = (ev.x, ev.y)
                    continue
                if self._mouse_cb:
                    _safe(self._mouse_cb, self.g, ev)

    def _pump(self) -> None:
        """等待时泵事件：界面事件就地分发（回调这时就跑），全部事件都留给下一次 g.events()（界面事件标成已分发，不会再跑一遍回调）。"""
        evs = self.g.world.events()
        self.handle([e for e in evs if e.kind in LOCAL_KINDS])
        self.g._event_backlog.extend(evs)

    def close(self) -> None:
        """收起自己的全部控件、清自己的热键和鼠标开关。别的客户端（别的进程 / 网关的别的会话）还在用输入时，
        运行时继续接管窗口过程；最后一个关的才让它放开（以前一关就放开，别人的按钮也点不了了）。"""
        for ch in list(self._choices.values()):
            ch.close()
        for key in list(self._click):
            self.canvas.remove(key)
        self._click.clear()
        self._hotkeys.clear()
        detach = getattr(self.block, "detach", None)
        if detach is not None:
            others = detach()
        else:                             # 测试替身
            self._sync_hotkeys()
            self.block.set_mouse(0)
            others = 0
        if not others:
            self.g.lane.commands([self.g._w3fast.command("input_enable", extra=0)], timeout=2.0)
            if getattr(self.canvas, "__dict__", None) is not None:
                self.canvas.__dict__.pop("_input_on", None)
        if getattr(self.g, "_ui", None) is self:
            self.g._ui = None             # 关掉以后再用 g.ui 会重新开一个（以前留着旧对象：再登记热键不报错也不生效）

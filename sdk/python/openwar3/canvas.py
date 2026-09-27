# -*- coding: utf-8 -*-
"""画板（09-25）：往游戏画面上画东西 —— 运行时（DLL）自己画，纯本机绘制，**多人局也安全**（不建游戏句柄、不改游戏状态）。

    c = g.canvas                                         # 第一次用时运行时装好绘制钩子
    c.text("hello", "你好，这是画板", screen=(40, 120), color=(255, 220, 80), bg=(0, 0, 0, 170), border="#C49C40")
    c.panel("status", "玩伴 · 小圣", ["心情：开心", "击杀：12"], screen=(-260, 120))   # 负数 = 从右边 / 下边往回数
    c.bar("hp", 0.62, unit=hero, lift=40, width=90, height=9, color=(80, 220, 80))    # 跟着单位的血条
    c.text("tag", "Boss 要放大招了！", unit=boss, lift=250, color=(255, 80, 80), size=22, bold=True)
    c.circle("aoe", (x, y), 400, color=(255, 60, 60), fill=(255, 60, 60, 50), width=3)   # 地上的危险区
    c.circle("aura", hero, 900, color=(80, 200, 255, 160))                           # 跟着单位走的范围圈
    c.path("route", [(x1, y1), (x2, y2), (x3, y3)], color=(255, 220, 0), width=4, arrow=True)
    c.image("logo", r"D:\\pics\\logo.png", screen=(20, 60), width=64, height=64)
    c.remove("aoe"); c.clear()
    with c.batch(): ...                                  # 一次改很多条，只写一次共享内存

    c.text("buy", "买一瓶药（50 金）", screen=(40, 300), bg=(20, 20, 30, 220), clickable=True)   # 09-25：可点（g.ui 收点击）

锚点（每个东西给一个）：screen=(x, y) 屏幕像素（负数从右/下数）/ frac=(0.5, 0.1) 屏幕比例 / world=(x, y) 世界坐标 / unit=单位（跟着走）。
可点（clickable=True，文字框 / 进度条 / 图片）：运行时拦住点在它上面的鼠标，发 ui.click 事件，游戏收不到这一下；
鼠标悬停时运行时自己盖一层高亮（hover=颜色，默认白 18%）。要先开输入（g.ui 会自动开）；按钮、选项卡用 g.ui 更省事。
世界 / 单位锚点的文字、进度条：底边中点对准那一点（lift 往上抬多少，游戏单位）；屏幕锚点默认左上角对准（center=True 中心对准）。
世界 / 单位锚点的东西默认扣掉操作台和昼夜球那块（over_ui=True 就盖在上面）。
颜色：(r, g, b) / (r, g, b, a) / "#RRGGBB" / "#RRGGBBAA"。

和 visual.Visual（JASS 那条路）的区别：visual 是游戏自己画的（原生风格、单人局），canvas 是运行时画的（样式随意、多人局安全）。
"""
from __future__ import annotations

import contextlib
import os
import threading
import time

import w3canvas as W

PANEL_BG = (12, 14, 22, 215)
PANEL_BORDER = (196, 156, 64, 235)


def enable_hint(g, reason: str) -> str:
    """canvas_enable / input_enable 没成功时，说清楚大概是为什么。
    ★ 09-26 实测：一局打完停在结算画面时快车道不处理命令（只在对局里排空），以前一律报「DLL 太旧？」，把人往错的方向带。"""
    if reason != "timeout":
        return f"{reason}（DLL 太旧？）"
    try:
        st = g.world.read()
        if st is not None and not st.in_game:
            return "超时：游戏不在对局里（停在菜单或结算画面时快车道不处理命令），进局以后再试"
    except Exception:  # noqa: BLE001 - 读不到世界块就给通用的提示
        pass
    return "超时：运行时没应答（游戏卡住、不在对局里，或者 DLL 太旧没有这条命令）"


class Canvas:
    _vbase = 0             # 类级默认：测试里的替身不走 __init__ 也能用
    _tlock = None

    def _lk(self):
        """本进程里的线程锁（toast 到点由计时线程删；网关一个会话的请求也可能在不同线程上）。"""
        if self._tlock is None:
            self._tlock = threading.RLock()
        return self._tlock

    def __init__(self, g):
        self.g = g
        # 第一次要装绘制钩子、起 GDI+：比普通命令慢，快车道默认等 0.25 秒不够（09-25 实测超时）
        r = g.lane.commands([g._w3fast.command("canvas_enable")], timeout=5.0)[0]
        if not r.ok:
            raise W.CanvasUnavailable(f"运行时开不了画板：{enable_hint(g, r.reason)}")
        self.block = W.CanvasBlock(g.pid)
        self.items: dict = {}          # key -> 条目 dict
        self._ids: dict = {}
        self._keys: dict = {}          # id -> key（点击事件带的是 id）
        self._next_id = 1
        self._batch = 0
        # 运行时按 (id, version) 缓存贴图：换一个客户端进程 id 又从 1 编起，version 也从 1 起就会拿到上一个进程的旧贴图
        # （09-25 实测：新按钮显示成上一次的文字）。version 从每个进程不同的基数起
        self._vbase = ((os.getpid() * 7919) ^ int(time.time() * 1000)) % (1 << 30)

    # ── 内部 ──
    def _anchor(self, screen=None, frac=None, world=None, unit=None, lift: float = 0.0) -> dict:
        if unit is not None:
            return {"anchor": "unit", "unit": unit.addr, "handle_lo": getattr(unit, "handle_lo", 0) or 0,
                    "handle_hi": getattr(unit, "handle_hi", 0) or 0, "z": float(lift)}
        if world is not None:
            return {"anchor": "world", "x": float(world[0]), "y": float(world[1]), "z": float(lift)}
        if frac is not None:
            return {"anchor": "frac", "x": float(frac[0]), "y": float(frac[1])}
        if screen is not None:
            return {"anchor": "screen", "x": float(screen[0]), "y": float(screen[1])}
        raise ValueError("要给一个位置：screen=(x, y) / frac=(x, y) / world=(x, y) / unit=单位")

    def _put(self, key, spec: dict):
        key = str(key)
        # 先单独打包一遍：颜色写错、字太长这类问题当场报出来，不进显示列表
        # （以前坏条目先进了表，之后每一次 text / remove / flush 都抛同一个错，直到有人删掉它）
        W.pack([dict(spec, id=1)])
        with self._lk():
            return self._put_locked(key, spec)

    def _ensure_input(self) -> None:
        """可点的东西要运行时先接管输入：以前只有 g.ui 会发 input_enable，直接 canvas.text(clickable=True) 点了没反应。"""
        if self.__dict__.get("_input_on"):
            return
        g = getattr(self, "g", None)
        lane = getattr(g, "lane", None)
        if lane is None:                                       # 测试替身
            return
        r = lane.commands([g._w3fast.command("input_enable", extra=1)], timeout=5.0)[0]
        if not r.ok:
            raise W.CanvasUnavailable(f"可点的画板项要运行时接管输入，没开成：{enable_hint(g, r.reason)}")
        import w3input
        self._input_client = w3input.InputBlock(g.pid)      # 登记成输入客户端：别人 ui.close() 时不会把输入整个关掉
        self._input_client.attach()
        self._input_on = True

    def _put_locked(self, key: str, spec: dict):
        self.__dict__.get("_expiry", {}).pop(key, None)      # 重新画了：之前定的到期作废
        if spec.get("flags", 0) & W.FLAG["interactive"]:
            self._ensure_input()
        if key not in self._ids:
            # id 由共享块统一分配（别的进程 / 网关的别的会话也在画，各自从 1 编会撞：点一下两边都以为是自己的按钮）
            alloc = getattr(self.block, "alloc_id", None)
            if alloc is not None:
                new_id = alloc()
            else:                                       # 测试替身
                new_id = self._next_id
                self._next_id += 1
            self._ids[key] = new_id
            self.__dict__.setdefault("_keys", {})[new_id] = key
        old = self.items.get(key)
        spec["id"] = self._ids[key]
        same = ("version", "x", "y", "z", "unit", "handle_lo", "handle_hi", "points", "hover")   # 不影响贴图的字段
        content = {k: v for k, v in spec.items() if k not in same}
        old_content = {k: v for k, v in (old or {}).items() if k not in same}
        # 只有影响贴图的东西变了才 +1（位置变了不用重画贴图）
        spec["version"] = (old["version"] + (0 if content == old_content else 1)) if old else self._vbase + 1
        self.items[key] = spec
        self._flush_maybe()
        return key

    def _flush_maybe(self):
        if not self._batch:
            self.flush()

    @staticmethod
    def _flags(**kw) -> int:
        return sum(W.FLAG[k] for k, on in kw.items() if on)

    def key_of(self, item_id: int) -> str | None:
        """ui.click / ui.hover 事件里的项 id -> 画的时候给的 key。"""
        return self.__dict__.get("_keys", {}).get(int(item_id))

    # ── 画东西 ──
    def text(self, key, text: str, *, screen=None, frac=None, world=None, unit=None, lift: float = 0.0,
             color=(255, 255, 255), bg=None, border=None, size: float = 0, bold: bool = False, shadow: bool = True,
             center: bool = False, width: float = 0, height: float = 0, radius: float = 0, layer: int = 0,
             over_ui: bool = False, clickable: bool = False, hover=None):
        """文字框。多行用 \\n；bg 背景色（不给 = 透明）、border 边框色；width 给了就按这个宽自动换行。
        clickable=True：可点（ui.click 事件）；hover：悬停高亮色（默认白 18%）。"""
        spec = {"kind": "text", "text": text, "color": color, "color2": bg, "color3": border, "font_px": size,
                "w": width, "h": height, "radius_px": radius, "layer": layer, "hover": hover,
                "flags": self._flags(shadow=shadow, bold=bold, center=center, over_ui=over_ui, interactive=clickable)}
        spec.update(self._anchor(screen, frac, world, unit, lift))
        return self._put(key, spec)

    def panel(self, key, title: str, lines: list[str], *, screen=None, frac=None, world=None, unit=None, lift: float = 0.0,
              color=(240, 240, 240), bg=PANEL_BG, border=PANEL_BORDER, size: float = 0, width: float = 0, layer: int = 0,
              over_ui: bool = False):
        """面板：标题 + 几行字（深色半透明底、金边）。"""
        return self.text(key, "\n".join([title] + list(lines)), screen=screen, frac=frac, world=world, unit=unit, lift=lift,
                         color=color, bg=bg, border=border, size=size, width=width, layer=layer, over_ui=over_ui)

    def bar(self, key, value: float, *, screen=None, frac=None, world=None, unit=None, lift: float = 0.0,
            width: float = 120, height: float = 10, color=(80, 220, 80), bg=None, border=None, text: str = "",
            center: bool = False, layer: int = 0, over_ui: bool = False, clickable: bool = False, hover=None):
        """进度条：value 0..1（血量、冷却、读条）。text 写在条中间。"""
        spec = {"kind": "bar", "value": max(0.0, min(1.0, float(value))), "text": text, "color": color, "color2": bg,
                "color3": border, "w": width, "h": height, "layer": layer, "hover": hover,
                "flags": self._flags(center=center, over_ui=over_ui, interactive=clickable)}
        spec.update(self._anchor(screen, frac, world, unit, lift))
        return self._put(key, spec)

    def image(self, key, path: str, *, screen=None, frac=None, world=None, unit=None, lift: float = 0.0,
              width: float = 0, height: float = 0, center: bool = False, layer: int = 0, over_ui: bool = False,
              clickable: bool = False, hover=None):
        """图片（本机 png / jpg / bmp / gif 文件）；width/height 不给 = 原尺寸。"""
        spec = {"kind": "image", "text": str(path), "w": width, "h": height, "layer": layer, "hover": hover,
                "flags": self._flags(center=center, over_ui=over_ui, interactive=clickable)}
        spec.update(self._anchor(screen, frac, world, unit, lift))
        return self._put(key, spec)

    def circle(self, key, center, radius: float, *, color=(255, 80, 80), fill=None, width: float = 2.0,
               lift: float = 0.0, layer: int = 0, over_ui: bool = False):
        """地上的圈：center = 单位（跟着走）或 (x, y)；radius 游戏单位；fill 填充色（带透明度，比如 (255, 0, 0, 50)）。"""
        spec = {"kind": "circle", "w": float(radius), "color": color, "color2": fill, "line_w": width, "layer": layer,
                "flags": self._flags(over_ui=over_ui)}
        spec.update(self._anchor(world=None if hasattr(center, "addr") else center,
                                 unit=center if hasattr(center, "addr") else None, lift=lift))
        return self._put(key, spec)

    def path(self, key, points, *, color=(255, 220, 0), width: float = 3.0, arrow: bool = True, lift: float = 0.0,
             layer: int = 0, over_ui: bool = False):
        """地上的折线（行军路线、撤退方向）：points = [(x, y) 或单位, ...]；arrow 末端画箭头。"""
        pts = [(p.x, p.y) if hasattr(p, "x") else (float(p[0]), float(p[1])) for p in points]
        if len(pts) < 2:
            raise ValueError("折线至少两个点")
        spec = {"kind": "path", "anchor": "world", "points": pts, "color": color, "line_w": width, "z": float(lift),
                "layer": layer, "flags": self._flags(arrow=arrow, over_ui=over_ui)}
        return self._put(key, spec)

    # ── 管理 ──
    def hide(self, key, hidden: bool = True):
        with self._lk():
            spec = self.items.get(str(key))
            if spec is not None:
                spec["flags"] = (spec.get("flags", 0) & ~W.FLAG["hidden"]) | (W.FLAG["hidden"] if hidden else 0)
                self._flush_maybe()

    def remove(self, key):
        with self._lk():
            if self.items.pop(str(key), None) is not None:
                self._flush_maybe()

    def clear(self):
        """清掉这个客户端画的全部东西（别的进程 / 会话画的不动）。"""
        with self._lk():
            self.items.clear()
            self.flush()

    def flush(self) -> int:
        """把这个客户端的显示列表写进共享内存（batch 里的改动在退出 batch 时一起写）。别的客户端画的东西原样留着。"""
        with self._lk():
            return self.block.write(list(self.items.values()))

    @contextlib.contextmanager
    def batch(self):
        with self._lk():
            self._batch += 1
            try:
                yield self
            finally:
                self._batch -= 1
                if not self._batch:
                    self.flush()

    def expire(self, key, seconds: float) -> None:
        """seconds 秒后自己删掉（计时线程删，不用等谁来泵事件 —— 以前 toast 只在 g.events() 里过期，
        MCP 的 war3_toast 不再调 war3_events 就永远挂在屏幕上）。之后同一个 key 又画了，这次到期作废。"""
        key = str(key)
        token = object()
        with self._lk():
            if key not in self.items:
                return
            self.__dict__.setdefault("_expiry", {})[key] = token

        def fire():
            with self._lk():
                if self.__dict__.get("_expiry", {}).get(key) is token:
                    del self._expiry[key]
                    self.remove(key)
        t = threading.Timer(max(0.0, float(seconds)), fire)
        t.daemon = True
        t.start()

    def stats(self) -> dict:
        """运行时回写的：画过几帧、画出去的 东西·帧、出过几次异常、上一帧花了多少微秒。判「真的在画」看 drawnFrames 在涨。"""
        return self.block.stats()

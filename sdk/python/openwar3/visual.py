# -*- coding: utf-8 -*-
"""画面与交互（09-24）：把实测过的 JASS 可视化能力包成一句话的接口 —— 浮动文字、连线、特效、范围圈、小地图闪点、
面板、倒计时、头像对白、全屏滤镜、提示音、驱散迷雾、单位外观；以及**不写 JASS 代码也能知道玩家做了什么**：
按钮菜单点了哪个、打了哪条聊天命令、按了哪个方向键 / Esc、任意 JASS 事件发生了几次。

    from openwar3.visual import Visual
    v = Visual(g)
    v.float_text("+128 暴击！", at=hero, color=(255, 204, 0), seconds=3)
    link = v.link(hero, buddy, kind="SPLK")           # 两个单位之间一根线；v.tick() 让它跟着单位动
    ring = v.ring(hero, 600, color=(255, 80, 80))    # 地上的范围圈（跟着单位）
    v.scene("小圣", "主人，前面有宝箱！", portrait="Hpal")   # 底部头像换成说话的圣骑士 + 字幕
    board = v.board("玩伴 · 小圣", [("心情", "开心"), ("击杀", "12")])
    board.set(1, 1, "13")
    menu = v.menu("小圣：接下来做什么？", ["跟着我", "原地守着"])
    cmds = v.chat_commands(["-follow", "-stay"])
    ...每拍：v.tick()；menu.clicked() -> 0/1/None；cmds.poll() -> ["-follow"]
    v.clear()                                          # 画过的全部删掉

⚠ 都走 JASS 通道：只在单人局 / 本机工具（dev 车道）里用；多人局会被拒（本机单方面建句柄会不同步）。
实测记录和每一类的截图说明见 docs/JASS_API_ZH.md。
"""
from __future__ import annotations

import time

# common.j 里的几个常量（enum 在引擎里就是整数）
PLAYER_EVENTS = {"left": 261, "left_up": 262, "right": 263, "right_up": 264, "down": 265, "down_up": 266,
                 "up": 267, "up_up": 268, "esc": 17}
FOG_VISIBLE = 4
BLEND_BLEND = 2
LIGHTNING = {"leash": "LEAS", "spirit": "SPLK", "drain": "DRAL", "heal": "HWPB", "chain": "CLPB", "mana": "MBUR",
             "finger": "AFOD", "forked": "FORK"}
ICON_DIR = "ReplaceableTextures\\CommandButtons\\"


def _xy(at):
    return (at.x, at.y) if hasattr(at, "x") else (float(at[0]), float(at[1]))


class _Item:
    def __init__(self, v, remove):
        self._v, self._remove, self.alive = v, remove, True

    def remove(self):
        if self.alive:
            self.alive = False
            try:
                self._remove()
            except Exception:  # noqa: BLE001 - 游戏换局了、句柄早没了：删不掉也没关系
                pass
            if self in self._v._items:
                self._v._items.remove(self)


class Link(_Item):
    """两点/两个单位之间的一根线（闪电）。跟着单位的话 v.tick() 每拍挪一次。"""

    def __init__(self, v, h, a, b, z):
        super().__init__(v, lambda: v.j.DestroyLightning(h))
        self.handle, self.a, self.b, self.z = h, a, b, z

    def update(self):
        a, b = self._v._fresh(self.a), self._v._fresh(self.b)
        if a is None or b is None:
            return self.remove()
        (ax, ay), (bx, by) = _xy(a), _xy(b)
        self._v.j.MoveLightningEx(self.handle, True, ax, ay, self.z, bx, by, self.z)


class Ring(_Item):
    """地上的圈（CreateImage 贴图），可以跟着单位走。"""

    def __init__(self, v, h, at, size):
        super().__init__(v, lambda: v.j.DestroyImage(h))
        self.handle, self.at, self.size = h, at, size

    def update(self):
        if not hasattr(self.at, "addr"):
            return
        u = self._v._fresh(self.at)
        if u is None:
            return self.remove()
        self._v.j.SetImagePosition(self.handle, u.x - self.size / 2, u.y - self.size / 2, 0.0)


class Board(_Item):
    """右上角的多行面板（multiboard）。行列从 0 数。"""

    def __init__(self, v, h, rows, cols):
        super().__init__(v, lambda: (v.j.MultiboardDisplay(h, False), v.j.DestroyMultiboard(h)))
        self.handle, self.rows, self.cols = h, rows, cols

    def set(self, row: int, col: int, text: str | None = None, color=None, icon: str | None = None, width: float | None = None):
        j = self._v.j
        it = j.MultiboardGetItem(self.handle, row, col)
        if text is not None:
            j.MultiboardSetItemValue(it, text)
        if color is not None:
            r, g, b, *a = color
            j.MultiboardSetItemValueColor(it, r, g, b, a[0] if a else 255)
        if icon is not None:
            j.MultiboardSetItemStyle(it, True, True)
            j.MultiboardSetItemIcon(it, icon if "\\" in icon else ICON_DIR + icon)
        if width is not None:
            j.MultiboardSetItemWidth(it, width)
        j.MultiboardReleaseItem(it)

    def title(self, text: str):
        self._v.j.MultiboardSetTitleText(self.handle, text)

    def show(self, on: bool = True):
        self._v.j.MultiboardDisplay(self.handle, on)


class Countdown(_Item):
    """倒计时窗口（右上角，游戏自己走秒）。"""

    def __init__(self, v, timer, dialog):
        super().__init__(v, lambda: (v.j.TimerDialogDisplay(dialog, False), v.j.DestroyTimerDialog(dialog),
                                     v.j.DestroyTimer(timer)))
        self.timer, self.dialog = timer, dialog

    def restart(self, seconds: float):
        self._v.j.TimerStart(self.timer, float(seconds), False, None)

    def left(self) -> float:
        return self._v.j.TimerGetRemaining(self.timer)


class Counter(_Item):
    """一个 JASS 事件发生了几次：空触发器（没有条件和动作）照样数执行次数（09-24 实测：定时器事件 0 -> 1）。"""

    def __init__(self, v, trig, label=""):
        super().__init__(v, lambda: v.j.DestroyTrigger(trig))
        self.trigger, self.label, self.seen = trig, label, 0

    def total(self) -> int:
        return self._v.j.GetTriggerExecCount(self.trigger)

    def poll(self) -> int:
        """上次 poll 之后又发生了几次。"""
        n = self.total()
        d, self.seen = n - self.seen, n
        return max(0, d)


class Menu(_Item):
    """按钮菜单（屏幕中间的对话框）。每个按钮一个空触发器，数执行次数就知道点了哪个 —— 不用写 JASS 函数。"""

    def __init__(self, v, dialog, counters, texts):
        super().__init__(v, lambda: (v.j.DialogDisplay(v.me, dialog, False), v.j.DialogDestroy(dialog),
                                     [c.remove() for c in counters]))
        self.dialog, self.counters, self.texts = dialog, counters, texts

    def clicked(self) -> int | None:
        """点了哪个按钮（下标）；还没点返回 None。点完对话框自己会关。"""
        for i, c in enumerate(self.counters):
            if c.poll():
                return i
        return None

    def show(self, on: bool = True):
        self._v.j.DialogDisplay(self._v.me, self.dialog, on)


class Commands(_Item):
    """聊天命令 / 按键：poll() 返回上次以来触发了哪些（同一条打两次就出现两次）。"""

    def __init__(self, v, counters: dict):
        super().__init__(v, lambda: [c.remove() for c in counters.values()])
        self.counters = counters

    def poll(self) -> list[str]:
        out = []
        for name, c in self.counters.items():
            out += [name] * c.poll()
        return out


class Visual:
    def __init__(self, g):
        self.g, self.j = g, g.jass
        self.me = self.j.GetLocalPlayer()
        self._items: list = []
        self._expire: list = []          # (到期墙钟, 东西)
        self._subtitles = False

    # ── 内部 ──
    def _keep(self, item, seconds=None):
        self._items.append(item)
        if seconds:
            self._expire.append((time.monotonic() + seconds, item))
        return item

    def _fresh(self, at):
        if not hasattr(at, "addr"):
            return at
        s = self.g.snapshot()
        return next((u for u in (s.units if s else []) if u.addr == at.addr and u.alive), None)

    def tick(self):
        """每拍调一次：到期的删掉、跟着单位的线和圈挪过去。"""
        now = time.monotonic()
        for t, item in list(self._expire):
            if now >= t:
                item.remove()
                self._expire.remove((t, item))
        for item in list(self._items):
            if item.alive and isinstance(item, (Link, Ring)):
                item.update()

    def clear(self):
        """这个 Visual 画过的全部删掉。"""
        for item in list(self._items):
            item.remove()
        self._expire.clear()

    # ── 画面 ──
    def float_text(self, text: str, at, color=(255, 255, 255), size: float = 10.0, seconds: float = 3.0,
                   rise: bool = True, z: float = 60.0):
        """浮动文字（伤害数字、头顶提示）。at = 单位（跟着它）或 (x, y)。支持 |cffRRGGBB 颜色码和中文。
        文字由游戏自己按 seconds 消失，不占 tick。游戏同时最多约 100 个。"""
        j = self.j
        t = j.CreateTextTag()
        j.SetTextTagText(t, text, size * 0.023 / 10)
        if hasattr(at, "addr"):
            j.SetTextTagPosUnit(t, at, z)
        else:
            x, y = _xy(at)
            j.SetTextTagPos(t, x, y, z)
        r, g_, b, *a = color
        j.SetTextTagColor(t, r, g_, b, a[0] if a else 255)
        if rise:
            j.SetTextTagVelocity(t, 0.0, 0.03)
        j.SetTextTagPermanent(t, False)
        j.SetTextTagLifespan(t, float(seconds))
        j.SetTextTagFadepoint(t, max(0.0, float(seconds) - 1.0))
        j.SetTextTagVisibility(t, True)
        return t

    def link(self, a, b, kind: str = "SPLK", color=None, z: float = 60.0, seconds: float | None = None) -> Link:
        """两点 / 两个单位之间连一根线。kind：闪电四字码或 leash/spirit/drain/heal/chain/mana/finger/forked。"""
        code = LIGHTNING.get(kind, kind)
        (ax, ay), (bx, by) = _xy(a), _xy(b)
        h = self.j.AddLightningEx(code, True, ax, ay, z, bx, by, z)
        if color is not None:
            r, g_, b_, *al = color
            self.j.SetLightningColor(h, r / 255, g_ / 255, b_ / 255, (al[0] if al else 255) / 255)
        return self._keep(Link(self, h, a, b, z), seconds)

    def effect(self, model: str, at, attach: str = "origin", seconds: float | None = None) -> _Item:
        """特效模型。at = 单位（attach 挂点：overhead 头顶 / origin 脚下 / chest / hand…）或 (x, y)。
        seconds=0：放一次就删（播完出生动画，适合爆炸、光柱）；None：一直在，自己 remove()。"""
        j = self.j
        h = j.AddSpecialEffectTarget(model, at, attach) if hasattr(at, "addr") else j.AddSpecialEffect(model, *_xy(at))
        item = _Item(self, lambda: j.DestroyEffect(h))
        if seconds == 0:
            j.DestroyEffect(h)
            item.alive = False
            return item
        return self._keep(item, seconds)

    def ring(self, at, radius: float, color=(255, 80, 80), alpha: int = 200,
             texture: str = "ReplaceableTextures\\Selection\\SpellAreaOfEffect.blp", seconds: float | None = None) -> Ring:
        """地上的范围圈（技能范围、危险区、集合点）。at = 单位（跟着走）或 (x, y)；radius 是半径。"""
        j = self.j
        size = float(radius) * 2
        x, y = _xy(at)
        h = j.CreateImage(texture, size, size, 0.0, x - size / 2, y - size / 2, 0.0, 0.0, 0.0, 0.0, 2)
        j.SetImageRenderAlways(h, True)
        j.SetImageColor(h, color[0], color[1], color[2], alpha)
        j.ShowImage(h, True)
        return self._keep(Ring(self, h, at, size), seconds)

    def ping(self, at, color=(255, 60, 60), seconds: float = 3.0, flashy: bool = False):
        """小地图上闪一下。"""
        x, y = _xy(at)
        self.j.PingMinimapEx(x, y, float(seconds), color[0], color[1], color[2], flashy)

    def board(self, title: str, rows: list, icon: str | None = None, width: float = 0.07) -> Board:
        """右上角面板：rows = [(左, 右), ...]（每行两格；想要更多列就传三元组）。icon 放在第一行第一格。"""
        j = self.j
        cols = max(len(r) for r in rows) if rows else 1
        h = j.CreateMultiboard()
        j.MultiboardSetRowCount(h, len(rows))
        j.MultiboardSetColumnCount(h, cols)
        j.MultiboardSetTitleText(h, title)
        j.MultiboardSetItemsStyle(h, True, False)
        j.MultiboardSetItemsWidth(h, width)
        b = Board(self, h, len(rows), cols)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                b.set(r, c, str(text), color=(255, 204, 0) if c == 0 and cols > 1 else None)
        if icon:
            b.set(0, 0, icon=icon)
        j.MultiboardDisplay(h, True)
        j.MultiboardMinimize(h, False)
        return self._keep(b)

    def countdown(self, title: str, seconds: float) -> Countdown:
        """右上角倒计时窗口（复活、Boss 刷新、任务限时）。"""
        j = self.j
        t = j.CreateTimer()
        j.TimerStart(t, float(seconds), False, None)
        d = j.CreateTimerDialog(t)
        j.TimerDialogSetTitle(d, title)
        j.TimerDialogDisplay(d, True)
        return self._keep(Countdown(self, t, d))

    def scene(self, speaker: str, text: str, portrait: str = "Hpal", color: int = 1, seconds: float = 6.0):
        """头像对白（战役里的「传话」）：底部头像换成 portrait 那种单位在说话，屏幕上出「speaker：text」字幕。
        09-24 实测：不开强制字幕只换头像不出字，所以第一次调时顺手 ForceCinematicSubtitles(true)。"""
        j = self.j
        if not self._subtitles:
            j.ForceCinematicSubtitles(True)
            self._subtitles = True
        j.SetCinematicScene(portrait, j.ConvertPlayerColor(int(color)), speaker, text, float(seconds), float(seconds) - 1)

    def screen_tint(self, color=(255, 0, 0), alpha: int = 120, seconds: float = 0.8,
                    texture: str = "ReplaceableTextures\\CameraMasks\\DreamFilter_Mask.blp"):
        """全屏滤镜：默认是四周泛红（残血警告）。seconds 是渐变时间；clear_tint() 去掉。"""
        j = self.j
        j.SetCineFilterTexture(texture)
        j.SetCineFilterBlendMode(j.ConvertBlendMode(BLEND_BLEND))
        j.SetCineFilterTexMapFlags(j.ConvertTexMapFlags(0))
        j.SetCineFilterStartUV(0.0, 0.0, 1.0, 1.0)
        j.SetCineFilterEndUV(0.0, 0.0, 1.0, 1.0)
        j.SetCineFilterStartColor(color[0], color[1], color[2], 0)
        j.SetCineFilterEndColor(color[0], color[1], color[2], int(alpha))
        j.SetCineFilterDuration(float(seconds))
        j.DisplayCineFilter(True)

    def clear_tint(self):
        self.j.DisplayCineFilter(False)

    def sound(self, path: str = "Sound\\Interface\\Warning.wav"):
        """放一个游戏里的声音文件（放完自动回收）。"""
        j = self.j
        s = j.CreateSound(path, False, False, False, 10, 10, "")
        j.StartSound(s)
        j.KillSoundWhenDone(s)

    def reveal(self, at, radius: float = 800.0, seconds: float | None = 10.0) -> _Item:
        """把一片地方的迷雾驱散给本机玩家看。"""
        j = self.j
        x, y = _xy(at)
        fm = j.CreateFogModifierRadius(self.me, j.ConvertFogState(FOG_VISIBLE), x, y, float(radius), True, False)
        j.FogModifierStart(fm)
        return self._keep(_Item(self, lambda: (j.FogModifierStop(fm), j.DestroyFogModifier(fm))), seconds)

    def look(self, unit, tint=None, scale: float | None = None, animation: str | None = None, flash=None):
        """单位外观：tint=(r,g,b[,a]) 变色、scale 放大缩小、animation 播动作（'victory' 'attack' 'spell'…）、
        flash=(r,g,b) 闪一下。恢复原样：tint=(255,255,255) scale=1。"""
        j = self.j
        if tint is not None:
            j.SetUnitVertexColor(unit, tint[0], tint[1], tint[2], tint[3] if len(tint) > 3 else 255)
        if scale is not None:
            j.SetUnitScale(unit, float(scale), float(scale), float(scale))
        if animation:
            j.SetUnitAnimation(unit, animation)
        if flash is not None:
            j.AddIndicator(unit, flash[0], flash[1], flash[2], 255)

    # ── 交互（不写 JASS 函数也知道玩家做了什么）──
    def on(self, register: str, *args, label: str = "") -> Counter:
        """任意 JASS 事件 -> 计数器：v.on("TriggerRegisterUnitEvent", hero, j.ConvertUnitEvent(53)) 数英雄死了几次。
        register 是 TriggerRegister* 系列的名字，args 是触发器之后的参数。拿不到「是谁」（那要在触发器里调 GetTriggerUnit），
        要分清是谁就给每个对象各建一个计数器。"""
        trig = self.j.CreateTrigger()
        self.j.call(register, trig, *args)
        return self._keep(Counter(self, trig, label or register))

    def menu(self, title: str, buttons: list[str]) -> Menu:
        """屏幕中间弹一个按钮菜单；menu.clicked() 查点了哪个。"""
        j = self.j
        d = j.DialogCreate()
        j.DialogSetMessage(d, title)
        counters = []
        for text in buttons:
            b = j.DialogAddButton(d, text, 0)
            counters.append(self.on("TriggerRegisterDialogButtonEvent", b, label=text))
        j.DialogDisplay(self.me, d, True)
        return self._keep(Menu(self, d, counters, list(buttons)))

    def chat_commands(self, commands: list[str], exact: bool = True, player=None) -> Commands:
        """聊天命令：玩家打出来就算一次。exact=False 按开头匹配（"-" 能数所有以 - 开头的话，但读不到具体内容）。
        09-24 实测 -follow 精确匹配 / "-" 前缀匹配都对；中文命令待真人用输入法验证（模拟输入被转成了乱码）。"""
        p = self.me if player is None else self.j.Player(int(player))
        return self._keep(Commands(self, {c: self.on("TriggerRegisterPlayerChatEvent", p, c, exact, label=c)
                                          for c in commands}))

    def hotkeys(self, keys=("left", "right", "up", "down", "esc"), player=None) -> Commands:
        """方向键 / Esc 按下：玩家不打字也能给玩伴发指令（方向键同时会挪镜头，这是游戏本来的行为）。"""
        p = self.me if player is None else self.j.Player(int(player))
        return self._keep(Commands(self, {k: self.on("TriggerRegisterPlayerEvent", p,
                                                     self.j.ConvertPlayerEvent(PLAYER_EVENTS[k]), label=k)
                                          for k in keys}))

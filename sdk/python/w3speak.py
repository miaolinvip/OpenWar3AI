# -*- coding: utf-8 -*-
"""让游戏里任意单位「说话」：头顶对话气泡的 Python 客户端（DLL 动作 56）。

09-18 起的设计要求：后台可以随时让任意单位说话、以任何身份弹同一种气泡；
通过接口直接指定游戏里的单位说话，也支持多个单位同时说；
气泡大小、字号、文字颜色等都可以自定义。

    import w3speak
    sp = w3speak.Speaker(pid)
    sp.say([
        w3speak.Bubble(unit=0x14DF2614, name='山丘之王', text='跟我冲！'),
        w3speak.Bubble(unit=0x14DF2924, name='大法师', text='我来放暴风雪。',
                       style=w3speak.Style(font_px=26, text_color='#FFE080', bg_color='#C0102040')),
        w3speak.Bubble(screen=(960, 120), key=1, name='旁白', text='第一波进攻开始了', style=w3speak.Style(tail=False)),
    ])
    sp.clear()                          # 全部清掉
    sp.say([w3speak.Bubble(unit=u, remove=True)])   # 只清一个

锚点三选一：unit（跟着单位走，有血条就贴在血条正上方）/ world=(x, y)（地图上的一点）/ screen=(x, y)（屏幕像素，旁白用）。
同一个单位只留一个气泡，新话顶掉旧话；world/screen 锚点按 key 顶替（key=0 每次新开一个）。
update=True：已有同一个气泡就只换文字/样式，不重置计时、打字从当前进度接着打 —— 本地大模型流式输出时用。

气泡偏到侧面：Style(side_px=120) —— 主体往右挪 120 像素，尾巴斜过去仍指着单位头顶（负数往左）。
叠在一起的气泡默认就让它叠着；要自动让开用 Speaker.tune(avoid=True)（每个游戏实例一个开关）。
不指定 duration_ms 时显示 3~5 秒（打字时间 + 1.5 秒）；长句会自动加快打字，保证在时间里打完。

二进制布局和 DLL 的 War3BubbleEntry（war3-button-hook-lab/ui_control_protocol.h）一一对应，
改一边必须改另一边（tests/test_w3speak.py 钉着字段顺序和 92 字节）。
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

MAGIC = 0x42423357          # 'W3BB'
VERSION = 2                 # 2：加了 side_px / tail_len_px / opacity
ACTION = 56
OP_CLEAR, OP_TEXT, OP_STATUS, OP_TUNE, OP_BATCH = 0, 1, 2, 3, 5

F_ANCHOR_WORLD = 0x001
F_ANCHOR_SCREEN = 0x002
F_UPDATE = 0x004
F_REMOVE = 0x008
F_NO_TAIL = 0x010
F_NO_SHADOW = 0x020
F_NO_FADE = 0x040
F_LIFT = 0x080

# size flags unit key | x y lift offX offY | duration typeMs | font nameFont maxWidth | pad radius border |
# text name bg border shadow | side tailLen | opacity reserved3[3] | nameLen textLen fontLen reserved
ENTRY_FMT = '<IIII fffff II HHH hhh IIIII hh B BBB HHHH'
ENTRY_SIZE = struct.calcsize(ENTRY_FMT)
HEADER_FMT = '<IHH II'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
SCRATCH_BYTES = 4096
assert ENTRY_SIZE == 92 and HEADER_SIZE == 16

TUNE_AVOID_ON = 0x40000     # 动作 56 command 3 的 bit18 / bit19
TUNE_AVOID_OFF = 0x80000

_NAMED = {
    'white': 0xFFFFFFFF, 'black': 0xFF000000, 'red': 0xFFFF4040, 'green': 0xFF40E040, 'blue': 0xFF4080FF,
    'yellow': 0xFFFFE040, 'gold': 0xFFFFD060, 'orange': 0xFFFFA030, 'purple': 0xFFC080FF, 'cyan': 0xFF40E0FF,
    'pink': 0xFFFF80C0, 'gray': 0xFFA0A0A0, 'grey': 0xFFA0A0A0, 'transparent': 0x00000000,
}


def color(value) -> int:
    """颜色转 ARGB 整数。支持：整数、'#RRGGBB'、'#AARRGGBB'、'#RGB'、'rgba(r,g,b,a)'（a 取 0~1 或 0~255）、几个英文名。
    None -> 0（DLL 用默认色）。⚠ 全透明黑 0x00000000 也会被 DLL 当成「默认」—— 真要透明就给 0x00000001。"""
    if value is None:
        return 0
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    s = str(value).strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    if s.startswith('#'):
        h = s[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) == 6:
            return 0xFF000000 | int(h, 16)
        if len(h) == 8:
            return int(h, 16)
    m = re.fullmatch(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)', s)
    if m:
        r, g, b = (min(255, int(m.group(i))) for i in (1, 2, 3))
        a = m.group(4)
        av = 255 if a is None else (round(float(a) * 255) if float(a) <= 1.0 else min(255, int(float(a))))
        return (av << 24) | (r << 16) | (g << 8) | b
    raise ValueError(f'看不懂的颜色：{value!r}')


@dataclass
class Style:
    """气泡样式。None / -1 = 用 DLL 的默认值（字号按屏幕高度自动）。"""
    font_px: int = 0              # 正文字号（像素）；0 = 自动（屏幕高 x 0.021，1080p 约 22）
    name_font_px: int = 0         # 名字字号；0 = 正文 x 0.8
    max_width_px: int = 0         # 正文最大宽度（超出自动换行）；0 = 正文字号 x 14
    pad_px: int = -1              # 内边距；-1 = 自动
    radius_px: int = -1           # 圆角；-1 = 自动，0 = 直角
    border_px: float = -1.0       # 边框粗细；-1 = 自动，0 = 不画
    text_color: object = None     # 正文颜色，默认浅灰白
    name_color: object = None     # 名字颜色，默认金色
    bg_color: object = None       # 底色（带透明度），默认 #D70C0E16
    border_color: object = None   # 边框颜色，默认暗金
    shadow_color: object = None   # 正文阴影颜色
    tail: bool = True             # 下面的小尾巴
    shadow: bool = True           # 正文阴影
    fade: bool = True             # 最后 0.6 秒淡出
    type_ms: int | None = None    # 每个字几毫秒；None = 默认 45，0 = 一次出全
    offset_x: float = 0.0         # 放好之后整体再挪的像素（右、下为正；尾巴尖也跟着挪，就不指着单位了）
    offset_y: float = 0.0
    side_px: int = 0              # 气泡主体往侧面偏（右正左负），尾巴尖仍指着单位 —— 尾巴斜过去
    tail_len_px: int = -1         # 尾巴长度；-1 = 自动（正文字号 x 0.65）
    opacity: float = 1.0          # 整个气泡（连文字）的不透明度 0~1；底色自己的透明度另算（bg_color 的 alpha）
    font: str = ''                # 字体名，空 = 微软雅黑

    @classmethod
    def from_dict(cls, d: dict | None) -> 'Style':
        if not d:
            return cls()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        unknown = set(d) - set(known)
        if unknown:
            raise ValueError(f'不认识的样式字段：{sorted(unknown)}')
        return cls(**known)


@dataclass
class Bubble:
    text: str = ''
    name: str = ''                # 显示在第一行的说话人，随便写，不必是这个单位
    unit: int = 0                 # 单位地址（快照里的 addr）
    world: tuple | None = None    # (x, y) 地图坐标
    screen: tuple | None = None   # (x, y) 屏幕像素（气泡尖的位置）
    key: int = 0                  # world/screen 气泡的 id
    duration_ms: int = 0          # 0 = 自动 3~5 秒（打字时间 + 1.5 秒）；给了就照给的来
    lift: float | None = None     # 给了就按投影抬这么高（世界单位），不贴血条
    update: bool = False
    remove: bool = False
    style: Style = field(default_factory=Style)

    @classmethod
    def from_dict(cls, d: dict) -> 'Bubble':
        d = dict(d)
        style = Style.from_dict(d.pop('style', None))
        unit = d.pop('unit', 0) or 0
        if isinstance(unit, str):
            unit = int(unit, 0)
        world = d.pop('world', None)
        screen = d.pop('screen', None)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        unknown = set(d) - set(known)
        if unknown:
            raise ValueError(f'不认识的字段：{sorted(unknown)}')
        return cls(unit=unit, world=tuple(world) if world else None, screen=tuple(screen) if screen else None,
                   style=style, **known)


def _utf8(s: str) -> bytes:
    return (s or '').encode('utf-8')


def pack_entry(b: Bubble) -> bytes:
    anchors = int(bool(b.unit)) + int(b.world is not None) + int(b.screen is not None)
    if anchors != 1:
        raise ValueError('unit / world / screen 必须且只能给一个')
    flags = 0
    x = y = 0.0
    if b.world is not None:
        flags |= F_ANCHOR_WORLD
        x, y = float(b.world[0]), float(b.world[1])
    elif b.screen is not None:
        flags |= F_ANCHOR_SCREEN
        x, y = float(b.screen[0]), float(b.screen[1])
    if b.update:
        flags |= F_UPDATE
    if b.remove:
        flags |= F_REMOVE
    s = b.style
    if not s.tail:
        flags |= F_NO_TAIL
    if not s.shadow:
        flags |= F_NO_SHADOW
    if not s.fade:
        flags |= F_NO_FADE
    lift = 0.0
    if b.lift is not None:
        flags |= F_LIFT
        lift = float(b.lift)
    name, text, font = _utf8(b.name), _utf8(b.text), _utf8(s.font)
    if not b.remove and not text:
        raise ValueError('text 不能为空')
    if len(text) > 3000:
        raise ValueError('text 太长（UTF-8 超过 3000 字节）')
    body_len = ENTRY_SIZE + len(name) + len(text) + len(font)
    size = (body_len + 3) & ~3
    border = -1 if s.border_px is None or s.border_px < 0 else int(round(s.border_px * 10))
    side = max(-1600, min(1600, int(s.side_px or 0)))
    tail_len = -1 if s.tail_len_px is None or s.tail_len_px < 0 else min(600, int(s.tail_len_px))
    op = 1.0 if s.opacity is None else max(0.0, min(1.0, float(s.opacity)))
    opacity = max(1, round(op * 255))      # 0 在协议里是「没填 = 不透明」，全透明给 1
    head = struct.pack(
        ENTRY_FMT, size, flags, b.unit & 0xFFFFFFFF, b.key & 0xFFFFFFFF,
        x, y, lift, float(s.offset_x), float(s.offset_y),
        int(b.duration_ms) & 0xFFFFFFFF, 0xFFFFFFFF if s.type_ms is None else int(s.type_ms),
        int(s.font_px), int(s.name_font_px), int(s.max_width_px),
        -1 if s.pad_px is None else int(s.pad_px), -1 if s.radius_px is None else int(s.radius_px), border,
        color(s.text_color), color(s.name_color), color(s.bg_color), color(s.border_color), color(s.shadow_color),
        side, tail_len, opacity, 0, 0, 0,
        len(name), len(text), len(font), 0)
    return head + name + text + font + b'\0' * (size - body_len)


def pack_batches(bubbles: list[Bubble]) -> list[bytes]:
    """打包成一个或多个 scratch 载荷（每个不超过 4096 字节）。"""
    payloads, cur, n = [], b'', 0
    for b in bubbles:
        e = pack_entry(b)
        if HEADER_SIZE + len(e) > SCRATCH_BYTES:
            raise ValueError('单条气泡太大（名字 + 正文 + 字体超过 4KB）')
        if HEADER_SIZE + len(cur) + len(e) > SCRATCH_BYTES:
            payloads.append(struct.pack(HEADER_FMT, MAGIC, VERSION, n, 0, 0) + cur)
            cur, n = b'', 0
        cur += e
        n += 1
    if n:
        payloads.append(struct.pack(HEADER_FMT, MAGIC, VERSION, n, 0, 0) + cur)
    return payloads


class BubbleUnsupported(RuntimeError):
    """这个游戏实例注入的 DLL 没有动作 56（旧版 render_hook.dll）。"""


class Speaker:
    """连到一个游戏实例（按 pid）。线程安全靠 DLL 通道自己的互斥量（和其它模块共用同一把锁）。"""

    def __init__(self, pid: int):
        import w3chan                    # 延迟导入：只做打包/测试时不需要游戏在跑
        self.pid = pid
        self.ch = w3chan.Channel(pid)
        self._O = w3chan.O

    def _request(self, command: int, scratch: bytes | None = None, timeout: float = 5.0) -> tuple[bool, str]:
        O, ch = self._O, self.ch
        with ch._hold():
            if scratch is not None:
                ch._buf[O.R_SCRATCH:O.R_SCRATCH + len(scratch)] = scratch
            if command & 0xFF == OP_TUNE:
                # 调参读 point.orderId / x / y；共享内存跨请求复用，别的模块留下的值会被当成参数 —— 先清零
                ch._buf[O.R_POINT + 4:O.R_POINT + 16] = b'\0' * 12
            done = ch._submit(ACTION, command, timeout)
            summary = ch.summary if done else 'timeout'        # summary 是属性（w3chan.Channel）
            success = done and ch._u32_at(O.R_SUCCESS) == 1
        # 动作 56 的回执一律以「bubble」开头。旧 DLL 不认识这个动作，会回一行界面状态
        # （09-18 实测 5 号：「state=LAN_CREATE_ACTIVE frames=3/22 ...」）—— 以前被当成发成功了，画面上什么都没有。
        if done and not summary.startswith('bubble'):
            raise BubbleUnsupported(f'pid {self.pid} 注入的 render_hook.dll 没有气泡功能（旧版，回执：{summary[:60]}）；'
                                    f'要等这个实例下次启动游戏加载新 DLL')
        return success, summary

    def say(self, bubbles: list[Bubble]) -> list[str]:
        """一次让一批单位说话（自动按 4KB 分包）。返回每包 DLL 的回执（bubble batch ok=N bad=M ...）。"""
        return [self._request(OP_BATCH, p)[1] for p in pack_batches(bubbles)]

    def tune(self, draw_at: int | None = None, debug_dot: bool | None = None, avoid: bool | None = None) -> str:
        """实例级开关（None = 不改）。avoid：叠在一起的气泡主体自动往侧面/上面让开、尾巴斜着指回单位（默认关）。
        调试用：draw_at 1 = 界面画完、鼠标指针画之前（09-25 起；没画指针的帧退回 Present），盖在血条和界面上面、
        指针盖在气泡上面，操作台/昼夜球按列裁掉（09-19 起默认）/
        0 = 世界画完那一刻画（血条会压在气泡上）/ 2 = 界面开画；debug_dot 在气泡尖画品红点。
        回执末尾是当前值：... dot=0 avoid=1"""
        cmd = OP_TUNE
        if draw_at is not None:
            cmd |= (int(draw_at) + 1) << 8
        if debug_dot is True:
            cmd |= 0x10000
        elif debug_dot is False:
            cmd |= 0x20000
        if avoid is True:
            cmd |= TUNE_AVOID_ON
        elif avoid is False:
            cmd |= TUNE_AVOID_OFF
        return self._request(cmd)[1]

    def clear(self) -> str:
        return self._request(OP_CLEAR)[1]

    def status(self) -> str:
        return self._request(OP_STATUS)[1]

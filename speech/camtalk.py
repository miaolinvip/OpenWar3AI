# -*- coding: utf-8 -*-
"""镜头对白：让**镜头里**的人说话 —— 英雄用首领的口吻，随从用随从的口吻；气泡不带名字，只显示说的话。

09-18 设计要求：镜头里的人说话，气泡不带名字、只显示内容；英雄从英雄的角度、用领导者的语气说，
随从用随从的语气。作为独立的一块另做，不改动农民闲聊。

和农民茶话会（banter.py）完全分开：工人和建筑不参与（工人归茶话会），各自一个开关、各自的设置。
由 speak_api.py 挂上（POST /api/camtalk），按**实例号**存（实例每局重开游戏进程，pid 会变）。

一轮：
    读镜头（目标点 / 距离 / 俯角 / 视野，JASS native 经 DLL 通道）+ 快照
    -> 算谁在画面里（war3-analytics/camframe.py 的投影，和导播同一套、09-18 对着截图标定过）
    -> 画面里有英雄：英雄先说一句（首领口吻），离他最近的随从接一句（随从口吻）
       没有英雄：两个兵之间说两句
       （可选）画面里有敌方英雄：偶尔先放一句狠话，我方英雄回
    -> 现场事实（交战 / 打野 / 在家 / 行军、血量、刚才掉了多少血）全从快照来，提示词里要求别编战果
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'director'))
import camframe  # noqa: E402

DATA = ROOT / 'data' / 'game'      # names.json / units.json（从本机游戏提取）
WORKERS = {'hpea', 'hmil', 'opeo', 'uaco', 'ewsp'}
HALLS = {'htow', 'hkee', 'hcas', 'ogre', 'ostr', 'ofrt', 'unpl', 'unp1', 'unp2', 'etol', 'etoa', 'etoe'}

# 常见英雄的说话腔调（名字从 names.json 取；这里只给性格，让模型按魔兽世界观去演）
HERO_VOICE = {
    'Hamg': '睿智从容、爱讲道理、偶尔冷幽默的老法师',
    'Hmkg': '豪爽、嗓门大、爱喝酒、永远冲在最前面的矮人',
    'Hpal': '正气凛然、讲荣耀与圣光的骑士',
    'Hblm': '高傲优雅、瞧不起凡人的精灵贵族',
    'Obla': '话少刀快、极度自信的剑客',
    'Ofar': '神秘、爱用预言口吻说话的萨满',
    'Otch': '沉稳厚重、讲大地与部族荣耀',
    'Oshd': '巫毒腔、狡黠爱开玩笑',
    'Udea': '阴冷霸道、对部下冷酷的堕落骑士',
    'Ulic': '冰冷阴森、爱嘲讽生者',
    'Udre': '阴险、带着优雅恶意的恶魔',
    'Ucrl': '古老低沉、说话慢的虫族君主',
    'Edem': '偏执狠厉、为了力量不择手段',
    'Ekee': '温和、守护森林的半神',
    'Emoo': '优雅虔诚、信奉月神',
    'Ewar': '冷峻、像执法者一样说话',
}


LEAD_ACTS = ['下命令', '判断眼前的局势', '鼓舞士气', '敲打偷懒的手下', '点评敌人或者野怪', '感慨一句这一仗']
FOLLOW_TONES = ['干脆领命', '拍马屁', '有点紧张', '兴奋求战', '小声抱怨', '吐槽敌人', '担心兄弟们的伤亡', '自夸一句']


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


_NAMES = (_load_json(DATA / 'names.json').get('byCode') or {})
_UNITS = _load_json(DATA / 'units.json')


def unit_name(code: str) -> str:
    return _NAMES.get(code, code)


def is_building(code: str) -> bool:
    return (_UNITS.get(code) or {}).get('defT') == 'fort' or code in HALLS


def is_hero(code: str) -> bool:
    return bool(code) and 'A' <= code[0] <= 'Z'


# ── 画面里有谁 ────────────────────────────────────────────────
# 气泡画在单位头上、往上长约 0.1 屏：上沿比 camframe 的可见区再收一点，免得人在画面顶上、气泡被顶栏切掉
VIS_TOP, VIS_BOT, VIS_L, VIS_R = 0.16, 0.72, 0.03, 0.97


def in_view(u, cam: dict) -> bool:
    """cam = {x, y, dist, pitch(度), fov(度), yaw(度)}。只在正北朝向（yaw 90）下按投影算；
    转过镜头的话退回「离镜头目标 1100 以内」。"""
    dx, dy = u.x - cam['x'], u.y - cam['y']
    if abs(((cam.get('yaw') or 90.0) - 90.0 + 180.0) % 360.0 - 180.0) > 3.0:
        return dx * dx + dy * dy < 1100.0 ** 2
    p = camframe.project(dx, dy, cam['dist'], cam['pitch'], cam['fov'] or 70.0)
    return bool(p) and VIS_L <= p[0] <= VIS_R and VIS_TOP <= p[1] <= VIS_BOT


# ── 台词清洗（和 banter.clean_line 同一套规矩，这里没有名字前缀要剥）─────────────
_EMOJI = re.compile('[\U0001F000-\U0001FFFF☀-➿️]')
_BRACKETS = re.compile(r'[（(【\[*][^）)】\]*]{0,40}[）)】\]*]')


def clean_line(text: str, max_chars: int) -> str:
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    lines = [s.strip() for s in text.strip().splitlines() if s.strip()]
    if not lines:
        return ''
    s = re.sub(r'^[^\s:：，,。！!？?]{1,8}[:：]\s*', '', lines[0])     # 「大魔法师：」这种前缀
    s = _BRACKETS.sub('', s)
    s = _EMOJI.sub('', s)
    s = s.strip().strip('"“”「」\'‘’').strip()
    if len(s) > max_chars:
        cut = max((s.rfind(p, 0, max_chars) for p in '。！？!?，,…~'), default=-1)
        s = s[:cut + 1] if cut + 1 >= max_chars * 0.4 else s[:max_chars]
        s = s.rstrip('，,')
    return s.strip()


SYSTEM = """你在给魔兽争霸3（冰封王座）的直播画面配角色台词。镜头里是一支部队，你替画面里的某一个角色说一句话。
观众是 B 站的 war3 老玩家。

角色口吻：
- 英雄 = 这支部队的首领：用领导者的口吻 —— 下命令、判断局势、鼓舞士气、敲打手下，带点威严，符合这个英雄在魔兽世界观里的身份和性格；
- 普通兵 = 随从：用随从的口吻 —— 领命、附和、表忠心、紧张或者兴奋、偶尔小声嘀咕抱怨；对英雄要恭敬（大人、长官、老大之类，按种族习惯）。

要求：
- 只写这一个角色的一句台词，口语，一到两句话，不超过 {max_chars} 个字；
- 紧扣【现场】写的情况（在打仗就说打仗、在打野就说打野、在家就说在家），只用给出的事实，不要编战果和伤亡；
- 可以带一点幽默，但别出戏，别用网络流行语；
- 不要写名字、不要旁白、不要动作描写、不要括号、不要引号、不要表情符号。{extra}"""


@dataclass
class CamTalkConfig:
    interval_s: float = 10.0        # 两轮之间隔多久（一轮 = 英雄一句 + 随从接几句）
    duration_s: float = 0.0         # 每句显示几秒；0 = 自动（按字数 3~5 秒）
    font_px: int = 0                # 字号；0 = 自动（1080p 约 22）
    max_chars: int = 24
    replies: int = 1                # 英雄说完随从接几句（0~3）
    include_foes: bool = False      # 画面里的敌方英雄也偶尔放狠话
    extra: str = ''                 # 额外要求（直接拼进提示词），比如「整体搞笑一点」
    temperature: float = 0.9

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if k in self.__dataclass_fields__ and v is not None:
                setattr(self, k, v)


@dataclass
class Line:
    t: float
    who: str        # 角色（只在网页和提示词里用，气泡上不显示）
    role: str       # lead / follow / soldier / foe
    text: str
    unit: int
    ms: int = 0


class CamTalk:
    """snapshot() -> w3state.Snapshot | None；camera() -> {x,y,dist,pitch,fov,yaw} | None；
    llm(messages, max_tokens, temperature) -> str；send(bubbles) -> 回执。"""

    def __init__(self, inst: int, snapshot, camera, llm, send, config: CamTalkConfig | None = None,
                 sleep=None, fog=None, quiet=None):
        # quiet() -> True：战况触发（eventtalk）正在说，这一轮让一让（同一个英雄头上只能有一个气泡，别互相顶掉）
        self._quiet = quiet
        self.inst = inst
        self.pid = 0
        self._snapshot, self._camera, self._llm, self._send = snapshot, camera, llm, send
        # fog() -> visible(x, y) -> True/False/None：每轮取一次。敌人和野怪只算「我方看得见」的 ——
        # 快照里有迷雾里的单位（09-18 5 号实测：右下黑乎乎的一片里有 4 个两栖怪，差点当成「在打野」）
        self._fog = fog
        self._lead_turn = 0
        self._tone_turn = random.randrange(len(FOLLOW_TONES))
        self.config = config or CamTalkConfig()
        self.transcript: deque[Line] = deque(maxlen=40)
        self.last_error = ''
        self.running = False
        self.view: list[dict] = []          # 最近一次算出来的画面里的人（给网页看）
        self.scene = ''
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_at = 0.0
        self._hero_turn = 0
        self._last_hp: dict[int, int] = {}
        self._last_center: tuple[float, float] | None = None
        self._sleep = sleep or (lambda s: self._stop.wait(s))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._next_at = 0.0
        self._thread = threading.Thread(target=self._run, name=f'camtalk-{self.inst}', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False

    def status(self) -> dict:
        return {'inst': self.inst, 'pid': self.pid,
                'running': self.running and bool(self._thread and self._thread.is_alive()),
                'config': dict(self.config.__dict__), 'last_error': self.last_error, 'scene': self.scene,
                'view': self.view,
                'transcript': [{'t': round(ln.t), 'who': ln.who, 'role': ln.role, 'text': ln.text, 'ms': ln.ms}
                               for ln in list(self.transcript)[-30:]]}

    def _run(self) -> None:
        while not self._stop.wait(0.3):
            if time.monotonic() < self._next_at:
                continue
            if self._quiet and self._quiet():
                self._next_at = time.monotonic() + 2.0
                continue
            try:
                self._round()
            except Exception as e:  # noqa: BLE001
                self.last_error = f'{time.strftime("%H:%M:%S")} {type(e).__name__}: {e}'
                self._next_at = time.monotonic() + 5.0

    # 一轮
    def _round(self) -> None:
        now = time.monotonic()
        snap = self._snapshot()
        cam = self._camera() if snap and snap.in_game else None
        if not snap or not cam:
            self.view, self.scene = [], '（没在对局里 / 读不到镜头）'
            self._next_at = now + 3.0
            return
        alive = [u for u in snap.units if u.hp > 0 and not is_building(u.type) and u.type not in WORKERS
                 and u.type != 'ngol']
        mine = [u for u in alive if u.owner == snap.me and in_view(u, cam)]
        foes = [u for u in alive if u.owner != snap.me and u.owner < 12 and in_view(u, cam)]
        creeps = [u for u in alive if u.owner == 12 and in_view(u, cam)]
        if self._fog and (foes or creeps):
            seen = self._fog()
            if seen:
                foes = [u for u in foes if seen(u.x, u.y)]
                creeps = [u for u in creeps if seen(u.x, u.y)]
        self.view = [{'unit': '0x%08X' % u.addr, 'name': unit_name(u.type), 'hero': is_hero(u.type),
                      'side': '我方' if u.owner == snap.me else '野怪' if u.owner == 12 else '敌方',
                      'hp': round(100 * u.hp / max(1, u.hp_max))} for u in mine + foes + creeps]
        if not mine and not (self.config.include_foes and foes):
            self.scene = '画面里没有我方的人'
            self._next_at = now + 2.0
            return
        scene = self._scene(snap, mine, foes, creeps)
        self.scene = scene

        heroes = [u for u in mine if is_hero(u.type)]
        plan: list[tuple] = []                      # (单位, 角色, 任务)
        foe_heroes = [u for u in foes if is_hero(u.type)]
        if self.config.include_foes and foe_heroes and random.random() < 0.35:
            fh = random.choice(foe_heroes)
            plan.append((fh, 'foe', '你是敌方的首领，冲着画面里的对手放一句狠话。'))
        if heroes:
            leader = heroes[self._hero_turn % len(heroes)]
            self._hero_turn += 1
            act = LEAD_ACTS[self._lead_turn % len(LEAD_ACTS)]
            self._lead_turn += 1
            task = '回敬刚才敌方那句狠话，同时给手下下令。' if plan else f'对手下说一句话，这次是{act}。'
            plan.append((leader, 'lead', task))
            near = sorted((u for u in mine if not is_hero(u.type)),
                          key=lambda u: (u.x - leader.x) ** 2 + (u.y - leader.y) ** 2)[:4]
            for f in random.sample(near, min(len(near), max(0, min(3, int(self.config.replies))))):
                # 09-18 空跑：不给态度时随从三句都是「大人放心……」—— 态度轮换
                tone = FOLLOW_TONES[self._tone_turn % len(FOLLOW_TONES)]
                self._tone_turn += 1
                plan.append((f, 'follow', f'回应{unit_name(leader.type)}刚才那句话，这次的态度：{tone}。'))
        else:
            soldiers = [u for u in mine if not is_hero(u.type)]
            if soldiers:
                picks = random.sample(soldiers, min(2, len(soldiers)))
                plan.append((picks[0], 'soldier', '首领不在，和身边的战友说一句（紧扣现场）。'))
                if len(picks) > 1:
                    plan.append((picks[1], 'soldier', '接上一句话。'))
        if not plan:
            self._next_at = now + 2.0
            return

        leader_name = unit_name(heroes[0].type) if heroes else ''
        for i, (u, role, task) in enumerate(plan):
            if self._stop.is_set():
                return
            text, ms = self._line(u, role, task, scene, leader_name)
            if not text:
                continue
            duration = self._duration(text)
            self._say(u, role, text, duration)
            self.transcript.append(Line(time.time(), unit_name(u.type), role, text, u.addr, ms))
            if i + 1 < len(plan):
                # 下一句等这句差不多打完字、观众读了一半再接（整句显示完再接就太拖了）
                self._sleep(min(duration / 1000.0, 1.2 + len(text) * 0.06))
        self.last_error = ''
        self._next_at = time.monotonic() + max(2.0, float(self.config.interval_s) * random.uniform(0.8, 1.2))

    def _scene(self, snap, mine, foes, creeps) -> str:
        def count(units):
            by: dict[str, int] = {}
            for u in units:
                by[unit_name(u.type)] = by.get(unit_name(u.type), 0) + 1
            return '、'.join(f'{n}×{k}' if k > 1 else n for n, k in by.items())

        rows = []
        heroes = [u for u in mine if is_hero(u.type)]
        if heroes:
            rows.append('- 我方首领：' + '、'.join(f'{unit_name(h.type)}（血 {round(100 * h.hp / max(1, h.hp_max))}%）'
                                              for h in heroes))
        others = [u for u in mine if not is_hero(u.type)]
        if others:
            hp = sum(u.hp for u in others) / max(1, sum(u.hp_max for u in others))
            rows.append(f'- 我方随从：{count(others)}（整体血量 {round(100 * hp)}%）')
        if foes:
            rows.append(f'- 敌方就在眼前：{count(foes)} —— 正在交战或马上要打')
        if creeps:
            rows.append(f'- 野怪：{count(creeps)} —— 在打野')
        if not foes and not creeps:
            halls = [u for u in snap.units if u.owner == snap.me and u.type in HALLS]
            cx = sum(u.x for u in mine) / max(1, len(mine))
            cy = sum(u.y for u in mine) / max(1, len(mine))
            at_home = any((h.x - cx) ** 2 + (h.y - cy) ** 2 < 1500 ** 2 for h in halls)
            moved = self._last_center and math.hypot(cx - self._last_center[0], cy - self._last_center[1]) > 250
            rows.append('- 附近没有敌人，' + ('部队在自家基地' if at_home else '部队在野外') +
                        ('，正在行军' if moved else '，原地待命'))
            self._last_center = (cx, cy)
        # 这一轮和上一轮之间我方掉了多少血（同一批还在画面里的人）
        lost = sum(max(0, self._last_hp[u.addr] - u.hp) for u in mine if u.addr in self._last_hp)
        if lost > 150:
            rows.append(f'- 刚才这一会儿我方掉了 {lost} 点血')
        self._last_hp = {u.addr: u.hp for u in mine}
        if snap.gold is not None:
            rows.append(f'- 家里库存：金 {snap.gold}、木 {snap.lumber}；人口 {snap.food_used}/{snap.food_cap}')
        return '\n'.join(rows)

    def _line(self, u, role: str, task: str, scene: str, leader_name: str) -> tuple[str, int]:
        cfg = self.config
        name = unit_name(u.type)
        if role in ('lead', 'foe'):
            voice = HERO_VOICE.get(u.type, '有威严的首领')
            me = f'你是{name}（英雄，{voice}），{"敌方" if role == "foe" else "这支部队"}的首领。'
        elif role == 'follow':
            me = f'你是一名{name}，是{leader_name}手下的随从。'
        else:
            me = f'你是一名{name}，普通士兵。'
        recent = [ln for ln in self.transcript if time.time() - ln.t < 90][-8:]
        convo = '\n'.join(f'{ln.who}{"（敌方）" if ln.role == "foe" else ""}：{ln.text}' for ln in recent) or '（刚开始）'
        extra = f'\n- 额外要求：{cfg.extra.strip()}' if cfg.extra and cfg.extra.strip() else ''
        system = SYSTEM.format(max_chars=int(cfg.max_chars), extra=extra)
        user = (f'【现场】\n{scene}\n\n【刚才的对话】\n{convo}\n\n【你】\n{me}\n\n【任务】\n{task}'
                f'别重复刚才说过的话。只输出这一句台词，不超过 {int(cfg.max_chars)} 个字。')
        t0 = time.perf_counter()
        raw = self._llm([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], 100,
                        float(cfg.temperature))
        return clean_line(raw, int(cfg.max_chars)), int((time.perf_counter() - t0) * 1000)

    def _duration(self, text: str) -> int:
        d = self.config.duration_s
        if d and d > 0:
            return int(min(30.0, max(1.0, float(d))) * 1000)
        return int(min(5000, max(3000, 1800 + 110 * len(text))))

    def _say(self, u, role: str, text: str, duration: int) -> None:
        import w3speak
        # 不显示名字；英雄的边框金色、敌方红色、随从灰蓝 —— 不看名字也分得出谁是头
        border = {'lead': '#E0B040', 'foe': '#D04040'}.get(role, '#7890A8')
        style = w3speak.Style(font_px=int(self.config.font_px or 0), border_color=border, type_ms=40)
        self._send([w3speak.Bubble(unit=u.addr, name='', text=text, duration_ms=duration, style=style)])

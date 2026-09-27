# -*- coding: utf-8 -*-
"""战况触发：盯着对局，出事了就让相关的人头顶冒一句 —— 不再只按间隔说话。

09-18 设计要求：不只按间隔闲聊，战斗情况 / 游戏进度 / 重大事件发生后也触发气泡，
结合局面说些直播观众看着有意思的话；相关参数都能配置。

每秒读一次快照，和上一秒比，认出这些事（每一种都能单独开关、单独冷却）：
    开战 / 一仗打完（双方各倒下几个）/ 打野 / 英雄残血 / 英雄阵亡 / 英雄复活 /
    升本 / 开分矿 / 建筑被拆 / 家被打 / 一下死一片 / 人口到坎（维护费）
出事以后挑**相关的人**说（打仗挑参战的英雄和兵、升本挑大厅边上的农民……），默认只挑镜头里的人 ——
镜头外说了观众也看不见。台词口吻沿用镜头对白（camtalk.py）那一套：英雄首领、兵随从、农民打工人、敌方英雄放狠话，
气泡不显示名字。

判据全从快照来（快照里有迷雾里的单位，所以「不见了 = 死了」对双方都成立；只有进金矿的工人会暂时不见，这里不数工人）。
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from camtalk import (HALLS, HERO_VOICE, WORKERS, clean_line, in_view, is_building, is_hero, unit_name)

# key: (中文名, 默认开不开, 默认冷却秒, 优先级 越小越先说)
EVENTS = {
    'hero_dead': ('英雄阵亡', True, 10, 0),
    'big_loss': ('一下死一片', True, 60, 1),
    'base_attacked': ('家被打', True, 90, 2),
    'fight_start': ('开战', True, 25, 3),
    'fight_end': ('一仗打完', True, 20, 4),
    'hero_low': ('英雄残血', True, 30, 5),
    'hero_back': ('英雄复活', True, 10, 6),
    'building_lost': ('建筑被拆', True, 30, 7),
    'tier_up': ('升本', True, 30, 8),
    'expand': ('开分矿', True, 30, 9),
    'creep': ('打野', True, 60, 10),
    'upkeep': ('人口到坎（维护费）', True, 0, 11),
}
TIER_CHAINS = [('htow', 'hkee', 'hcas'), ('ogre', 'ostr', 'ofrt'), ('unpl', 'unp1', 'unp2'), ('etol', 'etoa', 'etoe')]
TIER_NAME = {c: i + 1 for chain in TIER_CHAINS for i, c in enumerate(chain)}

SYSTEM = """你在给魔兽争霸3（冰封王座）的直播配角色台词：游戏里刚发生了一件事，由画面里的某个角色冒一句话。
观众是 B 站的 war3 老玩家，要让他们看着有意思：可以幽默、夸张、有反差、有梗，但要在角色里，别出戏。

角色口吻：
- 英雄 = 首领：领导者的口吻，下命令、定调子、鼓舞或者敲打手下，符合这个英雄在魔兽世界观里的身份和性格；
- 兵 = 随从：领命、附和、紧张、兴奋、嘀咕抱怨，对英雄恭敬；
- 农民 = 基地里打工的：打工人口吻，吐槽、慌张、看热闹；
- 敌方英雄 = 对手：放狠话、嘲讽。

要求：
- 只写这一个角色的一句台词，一到两句话，不超过 {max_chars} 个字；
- 紧扣【刚发生的事】，只用给出的事实和数字，不要编战果；
- 不要写名字、不要旁白、不要动作描写、不要括号、不要引号、不要表情符号。{extra}"""


@dataclass
class EventTalkConfig:
    enabled_events: dict = field(default_factory=lambda: {k: v[1] for k, v in EVENTS.items()})
    cooldowns: dict = field(default_factory=lambda: {k: v[2] for k, v in EVENTS.items()})
    min_gap_s: float = 4.0           # 两次事件台词之间至少隔几秒（一堆事一起发生时挑最要紧的说）
    fight_min: int = 3               # 双方各至少几个作战单位贴在一起（600 以内）才算开战
    big_loss_n: int = 5              # 20 秒内我方倒下几个作战单位算「一下死一片」
    hero_low_pct: int = 30           # 英雄血量低于百分之几算残血
    only_in_view: bool = True        # 只让镜头里的人说（镜头外说了观众看不见）
    bystander: bool = True           # 相关的人不在镜头里时，镜头里的自己人当「旁观者」接这个消息说一句
                                     # （09-18 空跑：英雄阵亡、升本、复活都发生在镜头外，全被跳过了）
    replies: int = 1                 # 主角说完旁边的人接几句（0~2）
    duration_s: float = 0.0          # 每句显示几秒；0 = 自动 3~5 秒
    font_px: int = 0
    max_chars: int = 26
    include_foes: bool = False       # 开战时敌方英雄也放一句狠话
    extra: str = ''
    temperature: float = 0.95

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if v is None or k not in self.__dataclass_fields__:
                continue
            if k in ('enabled_events', 'cooldowns') and isinstance(v, dict):
                getattr(self, k).update({kk: vv for kk, vv in v.items() if kk in EVENTS})
            else:
                setattr(self, k, v)


@dataclass
class Event:
    kind: str
    facts: str
    cast: list            # [(单位, 角色, 任务)]，第一个是主角；按顺序说
    t: float = 0.0


def _d2(a, b) -> float:
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2


def _count(units) -> str:
    by: dict[str, int] = {}
    for u in units:
        by[unit_name(u.type)] = by.get(unit_name(u.type), 0) + 1
    return '、'.join(f'{n}×{k}' if k > 1 else n for n, k in by.items()) or '无'


class EventTalk:
    def __init__(self, inst: int, snapshot, camera, llm, send, config: EventTalkConfig | None = None,
                 fog=None, on_say=None, sleep=None, clock=None):
        self.inst = inst
        self.pid = 0
        self._snapshot, self._camera, self._llm, self._send = snapshot, camera, llm, send
        self._fog, self._on_say = fog, on_say
        self.config = config or EventTalkConfig()
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._clock = clock or time.monotonic
        self._thread: threading.Thread | None = None
        self.running = False
        self.last_error = ''
        self.log: deque[dict] = deque(maxlen=60)          # 认出来的事 + 说了什么 / 为什么没说
        self.transcript: deque[dict] = deque(maxlen=30)
        self.counts: dict[str, list[int]] = {k: [0, 0] for k in EVENTS}   # [认出, 说了]
        self._pending: list[Event] = []
        self._last_said: dict[str, float] = {}
        self._last_any = -1e9
        self._reset(None)

    # ── 生命周期 ──
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, name=f'eventtalk-{self.inst}', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False

    def status(self) -> dict:
        cfg = dict(self.config.__dict__)
        return {'inst': self.inst, 'pid': self.pid,
                'running': self.running and bool(self._thread and self._thread.is_alive()),
                'config': cfg, 'last_error': self.last_error,
                'events': [{'key': k, 'name': v[0]} for k, v in EVENTS.items()],
                'counts': self.counts, 'fight': bool(self._fight),
                'log': list(self.log)[-40:], 'transcript': list(self.transcript)[-30:]}

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.last_error = f'{time.strftime("%H:%M:%S")} {type(e).__name__}: {e}'

    # ── 跟踪状态 ──
    def _reset(self, epoch) -> None:
        self._epoch = epoch
        self._first = True
        self._heroes: dict[int, object] = {}         # 活着的英雄（上一秒）
        self._missing: dict[int, int] = {}           # 英雄连续几秒不见
        self._dead_heroes: dict[int, str] = {}
        self._hero_ratio: dict[int, float] = {}
        self._halls: dict[int, str] = {}
        self._buildings: dict[int, object] = {}
        self._combat: dict[int, object] = {}
        self._deaths: deque = deque()
        self._fight: dict | None = None
        self._creep_camps: dict[tuple, float] = {}
        self._food = None
        self._upkeep_done: set[int] = set()

    # ── 每秒一次：认事 + 说话 ──
    def tick(self) -> None:
        snap = self._snapshot()
        if not snap or not snap.in_game:
            return
        if snap.epoch != self._epoch:
            self._reset(snap.epoch)
        now = self._clock()
        events = self._detect(snap, now)
        self._first = False
        for ev in events:
            ev.t = now
            self.counts[ev.kind][0] += 1
            if not self.config.enabled_events.get(ev.kind, True):
                self._note(ev, '这类事关着')
                continue
            if now - self._last_said.get(ev.kind, -1e9) < float(self.config.cooldowns.get(ev.kind, 0)):
                self._note(ev, '冷却中')
                continue
            self._pending.append(ev)
        # 过期的不说了（8 秒前的事再说就不应景了）
        for e in self._pending:
            if now - e.t > 8.0:
                self._note(e, '让给了更要紧的事，过时了')
        self._pending = [e for e in self._pending if now - e.t <= 8.0]
        if not self._pending or now - self._last_any < float(self.config.min_gap_s):
            return
        self._pending.sort(key=lambda e: EVENTS[e.kind][3])
        ev = self._pending.pop(0)
        self._speak(ev, snap, now)

    def _note(self, ev: Event, why: str, lines=None) -> None:
        self.log.append({'t': round(time.time()), 'kind': ev.kind, 'name': EVENTS[ev.kind][0], 'facts': ev.facts,
                         'result': why, 'lines': lines or []})

    def _detect(self, snap, now: float) -> list[Event]:
        cfg = self.config
        mine = [u for u in snap.units if u.owner == snap.me and u.hp > 0]
        combat = [u for u in mine if not is_building(u.type) and u.type not in WORKERS]
        heroes = {u.addr: u for u in combat if is_hero(u.type)}
        workers = [u for u in mine if u.type in WORKERS]
        buildings = {u.addr: u for u in mine if is_building(u.type)}
        halls = {u.addr: u for u in mine if u.type in HALLS}
        foes = [u for u in snap.units if u.hp > 0 and u.owner != snap.me and u.owner < 12 and not is_building(u.type)]
        creeps = [u for u in snap.units if u.hp > 0 and u.owner == 12]
        out: list[Event] = []
        first = self._first

        def near(pool, center, r):
            return sorted((u for u in pool if _d2(u, center) < r * r), key=lambda u: _d2(u, center))

        # 我方作战单位的阵亡（不数工人：进金矿也会「不见」）
        alive_combat = {u.addr for u in combat}
        for addr, u in self._combat.items():
            if addr not in alive_combat and not is_hero(u.type):
                self._deaths.append((now, u))
        while self._deaths and now - self._deaths[0][0] > 20.0:
            self._deaths.popleft()

        # 英雄：残血 / 阵亡（连续 2 秒不见或血 0）/ 复活
        for addr, h in heroes.items():
            r = h.hp / max(1, h.hp_max)
            prev = self._hero_ratio.get(addr, 1.0)
            if not first and r * 100 < cfg.hero_low_pct <= prev * 100:
                cast = [(h, 'lead', '你自己血快没了，说一句（硬撑、喊人掩护、自嘲都行）。')]
                cast += [(f, 'follow', '首领血快没了，你反应一句。') for f in near(combat, h, 900) if f is not h][:2]
                out.append(Event('hero_low', f'{unit_name(h.type)}血只剩 {round(r * 100)}%', cast))
            self._hero_ratio[addr] = r
            if addr in self._dead_heroes:
                name = self._dead_heroes.pop(addr)
                cast = [(h, 'lead', '你刚从祭坛复活回来，说一句（找回场子、自嘲、骂刚才谁坑了你都行）。')]
                cast += [(f, 'follow', '首领复活回来了，你迎一句。') for f in near(combat, h, 900) if f is not h][:1]
                out.append(Event('hero_back', f'{name}复活了', cast))
            self._missing.pop(addr, None)
        for addr, h in list(self._heroes.items()):
            if addr in heroes:
                continue
            self._missing[addr] = self._missing.get(addr, 0) + 1
            if self._missing[addr] >= 2:
                self._missing.pop(addr, None)
                self._heroes.pop(addr, None)
                self._dead_heroes[addr] = unit_name(h.type)
                witnesses = near(combat, h, 1200) or near(workers, h, 1500)
                cast = [(w, 'follow' if w in combat else 'peasant',
                         f'你们的首领{unit_name(h.type)}刚刚阵亡了，你反应一句（悲痛、慌、甩锅、黑色幽默都行）。')
                        for w in witnesses[:2]]
                out.append(Event('hero_dead', f'我方英雄{unit_name(h.type)}阵亡了', cast))
        for addr, h in heroes.items():
            self._heroes[addr] = h

        # 开战 / 一仗打完
        r2 = 600.0 ** 2
        eng_m = [u for u in combat if any(_d2(u, f) < r2 for f in foes)]
        eng_f = [f for f in foes if any(_d2(u, f) < r2 for u in combat)]
        if len(eng_m) >= cfg.fight_min and len(eng_f) >= cfg.fight_min:
            if self._fight is None:
                self._fight = {'t0': now, 'mine': {}, 'foes': {}, 'last': now}
                my_heroes = [u for u in eng_m if is_hero(u.type)]
                foe_heroes = [u for u in eng_f if is_hero(u.type)]
                cast = []
                if cfg.include_foes and foe_heroes:
                    cast.append((foe_heroes[0], 'foe', '两军刚撞上，你冲对面放一句狠话。'))
                lead = my_heroes[0] if my_heroes else eng_m[0]
                cast.append((lead, 'lead' if my_heroes else 'soldier',
                             '两军刚撞上，你喊一句（下令、战吼、给自己人打气）。' if my_heroes else
                             '首领不在，两军刚撞上，你喊一句。'))
                cast += [(f, 'follow' if my_heroes else 'soldier', '仗刚打起来，你接一句。')
                         for f in eng_m if f is not lead and not is_hero(f.type)][:2]
                out.append(Event('fight_start', f'开战了：我方 {_count(eng_m)} 对上敌方 {_count(eng_f)}', cast))
            f = self._fight
            f['last'] = now
            for u in eng_m:
                f['mine'][u.addr] = u
            for u in eng_f:
                f['foes'][u.addr] = u
        elif self._fight and now - self._fight['last'] > 6.0:
            f, self._fight = self._fight, None
            alive_f = {u.addr for u in foes}
            lost = [u for a, u in f['mine'].items() if a not in alive_combat]
            killed = [u for a, u in f['foes'].items() if a not in alive_f]
            secs = round(f['last'] - f['t0'])
            verdict = '我方占了便宜' if len(killed) > len(lost) else '我方吃了亏' if len(lost) > len(killed) else '打了个平手'
            facts = (f'一仗打完了（打了约 {secs} 秒）：我方倒下 {len(lost)} 个（{_count(lost)}），'
                     f'对面倒下 {len(killed)} 个（{_count(killed)}），{verdict}')
            survivors = [u for u in combat if u.addr in f['mine']]      # 参战的、现在还活着的（用这一秒的位置）
            hs = [u for u in survivors if is_hero(u.type)]
            cast = []
            if hs:
                cast.append((hs[0], 'lead', '仗刚打完，你总结一句（按战果：得意、不服、骂手下、安抚都行）。'))
            cast += [(u, 'follow' if hs else 'soldier', '仗刚打完，你说一句（按战果）。')
                     for u in survivors if not is_hero(u.type)][:2]
            if cast:
                out.append(Event('fight_end', facts, cast))

        # 打野：英雄 600 以内有掉了血的野怪（同一营地 = 野怪重心按 800 取整）
        for h in heroes.values():
            camp = [c for c in creeps if _d2(c, h) < r2]
            if camp and any(c.hp < c.hp_max for c in camp):
                key = (round(sum(c.x for c in camp) / len(camp) / 800), round(sum(c.y for c in camp) / len(camp) / 800))
                if now - self._creep_camps.get(key, -1e9) > 120:
                    self._creep_camps[key] = now
                    cast = [(h, 'lead', '正在带人打这窝野怪，你说一句。')]
                    cast += [(u, 'follow', '在打野，你接一句。') for u in near(combat, h, 800) if u is not h][:1]
                    out.append(Event('creep', f'{unit_name(h.type)}在带队打野：{_count(camp)}', cast))
                break

        # 升本 / 开分矿
        for addr, hall in halls.items():
            before = self._halls.get(addr)
            if before and before != hall.type and TIER_NAME.get(hall.type, 0) > TIER_NAME.get(before, 0):
                tier = TIER_NAME.get(hall.type, 0)
                cast = [(w, 'peasant', f'基地刚升到{tier}本（{unit_name(hall.type)}），你说一句。')
                        for w in near(workers, hall, 1200)[:1]]
                cast += [(h, 'lead', f'刚升到{tier}本，你说一句接下来的打算。') for h in list(heroes.values())[:1]]
                out.append(Event('tier_up', f'主基地升到{tier}本：{unit_name(before)} -> {unit_name(hall.type)}', cast))
            if not first and addr not in self._halls and len(halls) > 1:
                cast = [(w, 'peasant', '新的分矿大厅刚立起来，你说一句（要去新矿上班了之类）。')
                        for w in near(workers, hall, 1500)[:2]]
                out.append(Event('expand', f'开了第 {len(halls)} 座大厅（分矿）', cast))
        # 建筑被拆（上一秒还在、这一秒没了，而且旁边有敌人 —— 取消的地基不算）
        for addr, b in self._buildings.items():
            if addr not in buildings and near(foes, b, 1200):
                wit = near([u for u in mine if not is_building(u.type)], b, 1500)[:1]
                cast = [(w, 'soldier' if w in combat else 'peasant', f'我方的{unit_name(b.type)}刚被敌人拆了，你说一句。')
                        for w in wit]
                out.append(Event('building_lost', f'我方{unit_name(b.type)}被拆了', cast))
                break
        # 家被打：任何一座大厅 1300 以内有 2 个以上敌方作战单位
        for hall in halls.values():
            raiders = near(foes, hall, 1300)
            if len(raiders) >= 2:
                cast = [(w, 'peasant', '敌人打到家门口了，你喊一句。') for w in near(workers, hall, 1300)[:1]]
                cast += [(u, 'soldier', '敌人打到家门口了，你说一句。') for u in near(combat, hall, 1300)[:1]]
                out.append(Event('base_attacked', f'敌方 {_count(raiders)} 打到{unit_name(hall.type)}门口了', cast))
                break
        # 一下死一片
        if len(self._deaths) >= cfg.big_loss_n:
            dead = [u for _, u in self._deaths]
            self._deaths.clear()
            speakers = list(heroes.values())[:1] or combat[:1]
            cast = [(u, 'lead' if is_hero(u.type) else 'soldier', '刚才一下子死了好多自己人，你说一句。')
                    for u in speakers]
            out.append(Event('big_loss', f'20 秒内我方倒下 {len(dead)} 个：{_count(dead)}', cast))
        # 人口到坎：50（维护费 70%）、80（维护费 40%）
        food = snap.food_used
        if food is not None and self._food is not None:
            for th, fee in ((50, '收入打七折'), (80, '收入只剩四成')):
                if self._food < th <= food and th not in self._upkeep_done:
                    self._upkeep_done.add(th)
                    speakers = list(heroes.values())[:1] or workers[:1]
                    cast = [(u, 'lead' if is_hero(u.type) else 'peasant',
                             f'人口刚过 {th}，开始交维护费（{fee}），你说一句。') for u in speakers]
                    out.append(Event('upkeep', f'人口到了 {food}，过了 {th} 的坎，维护费：{fee}', cast))
        if food is not None:
            self._food = food

        self._combat = {u.addr: u for u in combat}
        self._halls = {a: h.type for a, h in halls.items()}
        self._buildings = buildings
        return out

    # ── 说话 ──
    def _speak(self, ev: Event, snap, now: float) -> None:
        cfg = self.config
        cast = [c for c in ev.cast if c[0] is not None]
        cam = self._camera() if cfg.only_in_view else None
        if cfg.only_in_view:
            if not cam:
                self._note(ev, '读不到镜头')
                return
            cast = [c for c in cast if in_view(c[0], cam)]
            if not cast and cfg.bystander and ev.kind != 'upkeep':
                cast = self._bystanders(ev, snap, cam)
        if not cast:
            self._note(ev, '相关的人不在镜头里' if cfg.only_in_view else '没有合适的人说')
            return
        main, rest = cast[0], cast[1:]
        n_reply = max(0, min(2, int(cfg.replies)))
        if main[1] == 'foe':                           # 敌方放狠话之后，我方主角回，再加接话
            rest = rest[:1 + n_reply]
        else:
            rest = rest[:n_reply]
        lines = []
        self._last_said[ev.kind] = now
        self._last_any = now
        for i, (u, role, task) in enumerate([main] + rest):
            if self._stop.is_set():
                break
            text, ms = self._line(ev, u, role, task, snap, lines)
            if not text:
                continue
            duration = self._duration(text)
            import w3speak
            border = {'lead': '#E0B040', 'foe': '#D04040', 'peasant': '#8FB070'}.get(role, '#7890A8')
            style = w3speak.Style(font_px=int(cfg.font_px or 0), border_color=border, type_ms=40)
            self._send([w3speak.Bubble(unit=u.addr, name='', text=text, duration_ms=duration, style=style)])
            if self._on_say:
                self._on_say(duration / 1000.0)
            lines.append({'who': unit_name(u.type), 'role': role, 'text': text, 'ms': ms})
            self.transcript.append({'t': round(time.time()), 'kind': ev.kind, 'who': unit_name(u.type),
                                    'role': role, 'text': text, 'ms': ms})
            if i < len(rest):
                self._sleep(min(duration / 1000.0, 1.2 + len(text) * 0.06))
        self._last_any = self._clock()
        if lines:
            self.counts[ev.kind][1] += 1
        self._note(ev, '说了' if lines else '模型没回', lines)

    def _bystanders(self, ev: Event, snap, cam) -> list:
        """镜头里的自己人（英雄 > 兵 > 农民）听说了这件事，说一句；第二个人接。"""
        seen = [u for u in snap.units if u.owner == snap.me and u.hp > 0 and not is_building(u.type)
                and in_view(u, cam)]
        if not seen:
            return []
        seen.sort(key=lambda u: (not is_hero(u.type), u.type in WORKERS))
        first = seen[0]
        others = sorted(seen[1:], key=lambda u: (u.x - first.x) ** 2 + (u.y - first.y) ** 2)[:1]   # 离他最近的接话
        news = f'你不在现场，但刚听说了这件事：{ev.facts}。说一句你的反应（吃瓜、吐槽、担心、幸灾乐祸都行，按你的身份）。'

        def role(u):
            return 'lead' if is_hero(u.type) else 'peasant' if u.type in WORKERS else 'soldier'
        cast = [(first, role(first), news)]
        cast += [(u, role(u), '接上一句，说说你对这件事的看法。') for u in others]
        return cast

    def _line(self, ev: Event, u, role: str, task: str, snap, said: list) -> tuple[str, int]:
        cfg = self.config
        name = unit_name(u.type)
        if role in ('lead', 'foe'):
            me = f'你是{name}（英雄，{HERO_VOICE.get(u.type, "有威严的首领")}），{"敌方" if role == "foe" else "我方"}的首领。'
        elif role == 'follow':
            me = f'你是一名{name}，跟着英雄打仗的随从。'
        elif role == 'peasant':
            me = f'你是基地里的一个{name}，天天挖矿砍树的打工人。'
        else:
            me = f'你是一名{name}，普通士兵。'
        mine = [x for x in snap.units if x.owner == snap.me and x.hp > 0 and not is_building(x.type)
                and x.type not in WORKERS]
        scene = f'我方作战单位 {len(mine)} 个；人口 {snap.food_used}/{snap.food_cap}；库存金 {snap.gold}、木 {snap.lumber}'
        convo = '\n'.join(f'{x["who"]}{"（敌方）" if x["role"] == "foe" else ""}：{x["text"]}' for x in said) or '（你第一个说）'
        extra = f'\n- 额外要求：{cfg.extra.strip()}' if cfg.extra and cfg.extra.strip() else ''
        system = SYSTEM.format(max_chars=int(cfg.max_chars), extra=extra)
        user = (f'【刚发生的事】\n{ev.facts}\n\n【现场】\n{scene}\n\n【这件事上已经说过的】\n{convo}\n\n【你】\n{me}\n\n'
                f'【任务】\n{task}只输出这一句台词，不超过 {int(cfg.max_chars)} 个字。')
        t0 = time.perf_counter()
        raw = self._llm([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], 100,
                        float(cfg.temperature))
        return clean_line(raw, int(cfg.max_chars)), int((time.perf_counter() - t0) * 1000)

    def _duration(self, text: str) -> int:
        d = self.config.duration_s
        if d and d > 0:
            return int(min(30.0, max(1.0, float(d))) * 1000)
        return int(min(5000, max(3000, 1800 + 110 * len(text))))

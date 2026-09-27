# -*- coding: utf-8 -*-
"""头顶对话气泡 + 本地大模型对话的 HTTP 接口（只听 127.0.0.1）。

    python speak_api.py [--port 8872]
    界面在远见的「头顶气泡」页（09-26 起；浏览器打开 http://127.0.0.1:8872/ 会跳过去）

接口（JSON）：
    GET  /api/instances                     在跑的游戏实例 [{pid, inst, exe}]
    GET  /api/units?pid=&owner=&type=&heroes=1   单位列表（addr 就是 say 里的 unit）
    POST /api/say     {pid|inst, bubbles:[{unit|world|screen, name, text, style, duration_ms, key, update, remove, lift}]}
                      也可以不写 bubbles，直接把一条气泡的字段放在顶层
    POST /api/clear   {pid|inst[, unit | key]}  不给 unit/key = 全部清掉
    GET  /api/status?pid=
    POST /api/chat    {pid|inst, unit|world|screen, name, persona, message, style, stream, conversation, reset}
                      本地模型回一句，直接让这个单位说出来；stream=true 时边生成边更新气泡
    GET  /api/llm / POST /api/llm {base_url, model, mode, max_tokens, temperature}   看 / 改模型配置
    GET  /api/banter?inst= / POST /api/banter {pid|inst, enabled, interval_s, max_chars, stats_inst, pro_every, ...}
                      农民茶话会（banter.py）：家里的工人按人设互相吐槽；开局报幕场次和上一局
    POST /api/banter/poke {pid|inst, topic}   抛一个话题（比如弹幕），下一句就接它
    POST /api/banter/announce {pid|inst}      马上报一次幕
    GET  /api/camtalk?inst= / POST /api/camtalk {pid|inst, enabled, interval_s, duration_s, font_px, max_chars,
                      replies, include_foes, extra}   镜头对白（camtalk.py）：镜头里的英雄用首领口吻、随从用随从口吻说话，不显示名字
    GET  /api/camera?inst=                    调试：镜头在哪、画面里有谁
    GET  /api/config / POST /api/config {avoid_overlap[, pid|inst]}   气泡的全局开关（存盘；已经连过的实例和点名的实例
                      立刻生效，之后新连上的实例也会先设好）。avoid_overlap 默认关：叠在一起就叠着（09-18 定的：重叠可以接受）

模型默认走 LM Studio（127.0.0.1:1234）上的 Qwen3.6-35B-A3B，mode=qwen-nothink：
用 /v1/completions 自己拼 ChatML 并预填空的 <think></think> —— LM Studio 不理 /no_think 和 enable_thinking=false，
09-18 在 5090 上实测：不关思考 200 个 token 全在思考、没有回答；关掉后一句回复约 0.3 秒（首字约 0.27 秒）。
mode=chat 走标准 /v1/chat/completions（gpt-oss 之类，实测 gpt-oss-20b 一句 0.3~0.8 秒）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

_r = str(Path(__file__).resolve().parent)
while not os.path.isfile(os.path.join(_r, 'openwar3.root')):
    _r = os.path.dirname(_r)
sys.path.insert(0, os.path.join(_r, 'sdk', 'python'))
import w3paths  # noqa: E402
w3paths.setup()
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import banter
import camtalk
import eventtalk
import w3speak

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / 'speak_api.json'
NAMES_PATH = Path(w3paths.NAMES_JSON)
EXE_RE = re.compile(r'^war3(?:-(\d+))?\.exe$', re.I)

DEFAULT_LLM = {
    'base_url': w3paths.llm_base(),          # openwar3.json 的 llm_base / llm_model；网页上改过的存在 speak_api.json，优先
    'model': w3paths.llm_model(),
    'mode': 'qwen-nothink',          # qwen-nothink | chat
    'max_tokens': 160,
    'temperature': 0.8,
}
DEFAULT_PERSONA = ('你是魔兽争霸3里的一个角色，正在和玩家并肩作战。用口语回答，一到两句话，不超过40个字，'
                   '不要解释，不要加引号，不要带动作描写。')

app = FastAPI(title='War3 头顶气泡接口')


@app.exception_handler(w3speak.BubbleUnsupported)
def _old_dll(_request, exc: w3speak.BubbleUnsupported):
    # 旧 DLL 的实例：清掉 / 看状态 / 调开关也都说清楚，而不是 500
    return JSONResponse(status_code=409, content={'detail': str(exc)})
_lock = threading.Lock()
_speakers: dict[int, w3speak.Speaker] = {}
_conversations: dict[str, list[dict]] = {}
_llm = dict(DEFAULT_LLM)
DEFAULT_BUBBLE = {'avoid_overlap': False}
_bubble_cfg = dict(DEFAULT_BUBBLE)
_banter_cfg: dict = {}                 # 茶话会的配置（间隔、字数、战绩来源……）
_camtalk_cfg: dict = {}                # 镜头对白的配置
_events_cfg: dict = {}                 # 战况触发的配置
# 自动开启：每个功能一张实例号表。表里的实例只要在跑、这个功能没开，看门线程 5 秒内就把它开起来 ——
# 接口重启、游戏每局重开进程、实例晚一点才起来，都会自动接上（09-18 起：指定实例号后，每次重启都自动开始闲聊）。
# 网页上按「开」= 加进表，按「关」= 从表里去掉；也可以直接在「自动开启实例号」里填。
_autostart: dict = {'banter': [], 'camtalk': [], 'events': []}
if CONFIG_PATH.exists():
    try:
        _saved = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        _llm.update(_saved.get('llm', {}))
        _bubble_cfg.update({k: v for k, v in _saved.get('bubble', {}).items() if k in DEFAULT_BUBBLE})
        _banter_cfg.update(_saved.get('banter', {}))
        _camtalk_cfg.update(_saved.get('camtalk', {}))
        _events_cfg.update(_saved.get('events', {}))
        saved_auto = _saved.get('autostart') or _saved.get('running') or {}    # running = 09-18 晚的旧名
        _autostart.update({k: sorted({int(i) for i in v}) for k, v in saved_auto.items() if k in _autostart})
    except (OSError, ValueError):
        pass
_banters: dict[int, banter.Banter] = {}
_camtalks: dict[int, camtalk.CamTalk] = {}
_eventtalks: dict[int, eventtalk.EventTalk] = {}
_match_stats = banter.MatchStats()
_states: dict = {}


def _save_config() -> None:
    CONFIG_PATH.write_text(json.dumps({'llm': _llm, 'bubble': _bubble_cfg, 'banter': _banter_cfg,
                                       'camtalk': _camtalk_cfg, 'events': _events_cfg, 'autostart': _autostart},
                                      ensure_ascii=False, indent=2), encoding='utf-8')


def _mark_running(kind: str, pid: int, on: bool) -> None:
    inst = _inst_of(pid)
    if not inst:
        return                                   # 原名 War3.exe 没有实例号，重启后找不回来，不记
    s = set(_autostart[kind])
    s.add(inst) if on else s.discard(inst)
    _autostart[kind] = sorted(s)
    _save_config()
try:
    _NAMES = json.loads(NAMES_PATH.read_text(encoding='utf-8')).get('byCode', {})
except (OSError, ValueError):
    _NAMES = {}


# ── 实例与连接 ────────────────────────────────────────────────
def instances() -> list[dict]:
    out = []
    for p in psutil.process_iter(['pid', 'name']):
        m = EXE_RE.match(p.info.get('name') or '')
        if m:
            out.append({'pid': p.info['pid'], 'inst': int(m.group(1)) if m.group(1) else 0, 'exe': p.info['name']})
    return sorted(out, key=lambda r: r['inst'])


def resolve_pid(pid: int | None, inst: int | None) -> int:
    running = instances()
    if pid:
        if not any(r['pid'] == pid for r in running):
            raise HTTPException(404, f'pid {pid} 不是在跑的游戏')
        return pid
    if inst is not None:
        for r in running:
            if r['inst'] == inst:
                return r['pid']
        raise HTTPException(404, f'没有在跑的 {inst} 号实例')
    if len(running) == 1:
        return running[0]['pid']
    raise HTTPException(400, f'有 {len(running)} 个游戏在跑，请给 pid 或 inst：{running}')


def speaker(pid: int) -> w3speak.Speaker:
    with _lock:
        sp = _speakers.get(pid)
        if sp is None:
            try:
                sp = w3speak.Speaker(pid)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(503, f'连不上 pid {pid} 的控制通道：{e}') from e
            _speakers[pid] = sp
            # DLL 里的开关每个进程一份、默认关：新连上的实例先按存盘的配置设好
            if _bubble_cfg['avoid_overlap']:
                try:
                    sp.tune(avoid=True)
                except Exception:  # noqa: BLE001
                    pass
        return sp


def drop_speaker(pid: int) -> None:
    with _lock:
        _speakers.pop(pid, None)


# ── 数据模型 ──────────────────────────────────────────────────
class Target(BaseModel):
    pid: int | None = None
    inst: int | None = None


class SayRequest(Target):
    bubbles: list[dict] | None = None
    model_config = {'extra': 'allow'}      # 允许把一条气泡的字段直接放在顶层


class ClearRequest(Target):
    unit: str | int | None = None
    key: int | None = None


class ChatRequest(Target):
    unit: str | int | None = None
    world: list[float] | None = None
    screen: list[float] | None = None
    key: int = 0
    name: str = ''
    persona: str = ''
    message: str
    style: dict | None = None
    duration_ms: int = 0               # 0 = 自动 3~5 秒
    stream: bool = True
    conversation: str = ''
    reset: bool = False
    max_turns: int = 6


class BubbleConfig(Target):
    avoid_overlap: bool | None = None


class LlmConfig(BaseModel):
    base_url: str | None = None
    model: str | None = None
    mode: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


def read_snapshot(pid: int, tries: int = 30):
    """读一份快照（w3state.Snapshot）；读不到返回 None。
    快照发布器平时由 AI 监督器打开；没有 AI 的实例这里顺手打开（动作 52 command 1，只读）。"""
    import w3state
    st = _states.get(pid)
    if st is None:
        try:
            st = w3state.State(pid)
        except Exception:  # noqa: BLE001
            speaker(pid).ch.action(52, 1)
            time.sleep(0.3)
            try:
                st = w3state.State(pid)
            except Exception:  # noqa: BLE001
                return None
        _states[pid] = st
    for _ in range(tries):
        try:
            snap = st.read()
        except Exception:  # noqa: BLE001
            _states.pop(pid, None)
            return None
        if snap:
            return snap
        time.sleep(0.1)
    speaker(pid).ch.action(52, 1)
    return None


def _send(pid: int, bubbles: list[w3speak.Bubble]) -> list[str]:
    try:
        return speaker(pid).say(bubbles)
    except HTTPException:
        raise
    except w3speak.BubbleUnsupported as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        drop_speaker(pid)
        raise HTTPException(503, f'发送失败：{e}') from e


# ── 气泡 ──────────────────────────────────────────────────────
@app.get('/api/instances')
def api_instances():
    return instances()


@app.get('/api/units')
def api_units(pid: int | None = None, inst: int | None = None, owner: int | None = None,
              type: str | None = None, heroes: bool = False, mine: bool = False):  # noqa: A002
    pid = resolve_pid(pid, inst)
    snap = read_snapshot(pid)
    if not snap:
        raise HTTPException(503, '读不到快照（刚打开了快照发布器，过一秒再试）')
    rows = []
    for u in snap.units:
        if u.hp <= 0:
            continue
        if owner is not None and u.owner != owner:
            continue
        if mine and u.owner != snap.me:
            continue
        if type and u.type != type:
            continue
        if heroes and not u.is_hero:
            continue
        rows.append({'unit': u.addr_hex, 'type': u.type, 'name': _NAMES.get(u.type, u.type), 'owner': u.owner,
                     'mine': u.owner == snap.me, 'hero': u.is_hero, 'x': round(u.x), 'y': round(u.y),
                     'hp': u.hp, 'hp_max': u.hp_max})
    return {'pid': pid, 'me': snap.me, 'units': rows}


@app.post('/api/say')
def api_say(req: SayRequest):
    pid = resolve_pid(req.pid, req.inst)
    raw = req.bubbles
    if raw is None:
        extra = {k: v for k, v in (req.model_extra or {}).items()}
        raw = [extra] if extra else []
    if not raw:
        raise HTTPException(400, '没有气泡（给 bubbles 列表，或把一条气泡的字段放在顶层）')
    try:
        bubbles = [w3speak.Bubble.from_dict(b) for b in raw]
        receipts = _send(pid, bubbles)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {'pid': pid, 'count': len(bubbles), 'receipts': receipts}


@app.post('/api/clear')
def api_clear(req: ClearRequest):
    pid = resolve_pid(req.pid, req.inst)
    if req.unit is None and req.key is None:
        return {'pid': pid, 'receipt': speaker(pid).clear()}
    if req.unit is not None:
        unit = int(req.unit, 0) if isinstance(req.unit, str) else int(req.unit)
        b = w3speak.Bubble(unit=unit, remove=True)
    else:
        b = w3speak.Bubble(screen=(0, 0), key=req.key, remove=True)
    return {'pid': pid, 'receipts': _send(pid, [b])}


@app.get('/api/status')
def api_status(pid: int | None = None, inst: int | None = None):
    pid = resolve_pid(pid, inst)
    return {'pid': pid, 'status': speaker(pid).status()}


# ── 本地大模型 ────────────────────────────────────────────────
def _chatml(messages: list[dict]) -> str:
    parts = [f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages]
    return ''.join(parts) + '<|im_start|>assistant\n<think>\n\n</think>\n\n'


def llm_stream(messages: list[dict], max_tokens: int | None = None, temperature: float | None = None):
    """逐段产出模型回复的文字。max_tokens / temperature 不给就用配置里的。"""
    cfg = _llm
    max_tokens = max_tokens or cfg['max_tokens']
    temperature = cfg['temperature'] if temperature is None else temperature
    if cfg['mode'] == 'qwen-nothink':
        url = cfg['base_url'].rstrip('/') + '/completions'
        body = {'model': cfg['model'], 'prompt': _chatml(messages), 'stream': True,
                'max_tokens': max_tokens, 'temperature': temperature, 'top_p': 0.92,
                'stop': ['<|im_end|>', '<|im_start|>']}
    else:
        url = cfg['base_url'].rstrip('/') + '/chat/completions'
        body = {'model': cfg['model'], 'messages': messages, 'stream': True,
                'max_tokens': max_tokens, 'temperature': temperature, 'reasoning_effort': 'low'}
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            obj = json.loads(data)
            for ch in obj.get('choices', []):
                piece = ch.get('text') if 'text' in ch else (ch.get('delta') or {}).get('content')
                if piece:
                    yield piece


def llm_text(messages: list[dict], max_tokens: int | None = None, temperature: float | None = None) -> str:
    return ''.join(llm_stream(messages, max_tokens, temperature))


def _clean(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S)
    return text.strip().strip('"“”「」')


@app.post('/api/chat')
def api_chat(req: ChatRequest):
    pid = resolve_pid(req.pid, req.inst)
    unit = int(req.unit, 0) if isinstance(req.unit, str) else int(req.unit or 0)
    conv_id = req.conversation or f'{pid}:{unit or req.key}'
    if req.reset:
        _conversations.pop(conv_id, None)
    history = _conversations.setdefault(conv_id, [])
    persona = req.persona or DEFAULT_PERSONA
    if req.name and not req.persona:
        persona = f'你扮演「{req.name}」。' + persona
    messages = [{'role': 'system', 'content': persona}] + history[-2 * req.max_turns:] + \
               [{'role': 'user', 'content': req.message}]
    style = w3speak.Style.from_dict(req.style)

    def bubble(text: str, update: bool) -> w3speak.Bubble:
        return w3speak.Bubble(unit=unit, world=tuple(req.world) if req.world else None,
                              screen=tuple(req.screen) if req.screen else None, key=req.key,
                              name=req.name, text=text, update=update, style=style,
                              duration_ms=max(0, int(req.duration_ms or 0)))

    t0 = time.perf_counter()
    first_ms = None
    text, sent, last_push = '', 0, 0.0
    try:
        for piece in llm_stream(messages):
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
            text += piece
            if req.stream:
                shown = _clean(text)
                now = time.perf_counter()
                # 边生成边更新：第一段新开气泡，之后只换文字（不重置计时、打字接着打）
                if shown and (len(shown) - sent >= 4 or now - last_push > 0.15):
                    _send(pid, [bubble(shown, update=sent > 0)])
                    sent, last_push = len(shown), now
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f'本地模型没有响应（{_llm["base_url"]} / {_llm["model"]}）：{e}') from e
    reply = _clean(text)
    if not reply:
        raise HTTPException(502, '模型回了空话')
    receipts = _send(pid, [bubble(reply, update=req.stream and sent > 0)])
    history += [{'role': 'user', 'content': req.message}, {'role': 'assistant', 'content': reply}]
    del history[:-2 * req.max_turns]
    return {'pid': pid, 'reply': reply, 'first_token_ms': round(first_ms or 0),
            'total_ms': round((time.perf_counter() - t0) * 1000), 'receipts': receipts}


@app.get('/api/llm')
def api_llm_get():
    ok, models = False, []
    try:
        with urllib.request.urlopen(_llm['base_url'].rstrip('/') + '/models', timeout=2) as r:
            models = [m.get('id') for m in json.loads(r.read()).get('data', [])]
            ok = True
    except Exception:  # noqa: BLE001
        pass
    return {'config': _llm, 'reachable': ok, 'models': models}


@app.post('/api/llm')
def api_llm_set(cfg: LlmConfig):
    for k, v in cfg.model_dump().items():
        if v is not None:
            _llm[k] = v
    _save_config()
    return {'config': _llm}


@app.get('/api/config')
def api_config_get():
    return {'config': _bubble_cfg}


@app.post('/api/config')
def api_config_set(cfg: BubbleConfig):
    if cfg.avoid_overlap is not None:
        _bubble_cfg['avoid_overlap'] = bool(cfg.avoid_overlap)
    _save_config()
    # 立刻生效的范围：已经连过的实例 + 请求里点名的那个。没用过气泡的实例不去碰（别的实例可能在跑 AI / 直播），
    # 它们第一次连上时 speaker() 会按存盘的配置设好。回执末尾是 DLL 里的当前值（... avoid=0/1）
    running = {r['pid'] for r in instances()}
    with _lock:
        targets = {p for p in _speakers if p in running}
    if cfg.pid or cfg.inst is not None:
        targets.add(resolve_pid(cfg.pid, cfg.inst))
    applied = {}
    for pid in sorted(targets):
        try:
            applied[pid] = speaker(pid).tune(avoid=_bubble_cfg['avoid_overlap'])
        except Exception as e:  # noqa: BLE001
            drop_speaker(pid)
            applied[pid] = f'失败：{getattr(e, "detail", e)}'
    return {'config': _bubble_cfg, 'applied': applied}


# ── 农民茶话会 ────────────────────────────────────────────────
class BanterRequest(Target):
    enabled: bool | None = None
    interval_s: float | None = None
    max_chars: int | None = None
    stats_inst: int | None = None      # 0 = 跟游戏同一个实例
    home_radius: float | None = None
    pro_every: int | None = None
    duration_s: float | None = None    # 每句显示几秒；0 = 自动 3~5 秒
    font_px: int | None = None         # 0 = 自动
    name_font_px: int | None = None
    temperature: float | None = None


class PokeRequest(Target):
    topic: str


def _inst_of(pid: int) -> int:
    return next((r['inst'] for r in instances() if r['pid'] == pid), 0)


def _pid_of(inst: int) -> int | None:
    return next((r['pid'] for r in instances() if r['inst'] == inst), None)


def _banter(pid: int, create: bool = False) -> banter.Banter | None:
    """茶话会按**实例号**存，不按 pid：实例每局重开一个游戏进程（新 pid），
    按 pid 存的话每打完一局茶话会就悄悄停了（09-18 5 号实测）。每一步现查这个实例当前的 pid。
    原名 War3.exe（实例号 0）没法按实例号找，只能按 pid。"""
    inst = _inst_of(pid)
    key = inst if inst else -pid
    with _lock:
        b = _banters.get(key)
    if b is None and create:
        cfg = banter.BanterConfig()
        cfg.update(_banter_cfg)
        current = (lambda: _pid_of(inst)) if inst else (lambda: pid)

        def snap():
            p = current()
            if not p:
                return None                     # 局间：游戏进程正在换
            b.pid = p
            return read_snapshot(p, tries=5)

        def send(bubbles):
            p = current()
            if not p:
                raise RuntimeError(f'{inst} 号实例现在没有游戏进程')
            return _send(p, bubbles)

        b = banter.Banter(pid, inst, snapshot=snap, llm=llm_text, send=send, stats=_match_stats, config=cfg)
        with _lock:
            _banters[key] = b
    return b


@app.get('/api/banter')
def api_banter_get(pid: int | None = None, inst: int | None = None):
    pid = resolve_pid(pid, inst)
    b = _banter(pid)
    if b is None:
        cfg = banter.BanterConfig()
        cfg.update(_banter_cfg)
        return {'pid': pid, 'running': False, 'config': cfg.__dict__, 'cast': [], 'transcript': []}
    return dict(b.status(), pid=pid)


@app.post('/api/banter')
def api_banter_set(req: BanterRequest):
    pid = resolve_pid(req.pid, req.inst)
    b = _banter(pid, create=True)
    changes = {k: v for k, v in req.model_dump(exclude={'pid', 'inst', 'enabled'}).items() if v is not None}
    if changes:
        b.config.update(changes)
        _banter_cfg.update(changes)
        _save_config()
    if req.enabled is True:
        b.start()
    elif req.enabled is False:
        b.stop()
    if req.enabled is not None:
        _mark_running('banter', pid, req.enabled)
    return b.status()


@app.post('/api/banter/poke')
def api_banter_poke(req: PokeRequest):
    pid = resolve_pid(req.pid, req.inst)
    b = _banter(pid)
    if b is None or not b.running:
        raise HTTPException(409, '茶话会没开')
    b.poke(req.topic)
    return {'pid': pid, 'queued': req.topic}


@app.post('/api/banter/announce')
def api_banter_announce(req: Target):
    pid = resolve_pid(req.pid, req.inst)
    b = _banter(pid)
    if b is None or not b.running:
        raise HTTPException(409, '茶话会没开')
    b.announce_now()
    return {'pid': pid, 'ok': True}


# ── 镜头对白（camtalk.py；和茶话会分开，各自开关、各自设置）─────────────
class CamTalkRequest(Target):
    enabled: bool | None = None
    interval_s: float | None = None
    duration_s: float | None = None
    font_px: int | None = None
    max_chars: int | None = None
    replies: int | None = None
    include_foes: bool | None = None
    extra: str | None = None
    temperature: float | None = None


_obs_lanes: dict = {}


def _obs_lane(pid: int):
    """这个游戏进程的一条 observer 车道（W3P：只能查询/读镜头，下不了令 —— 说话的模块用不着指挥单位）。"""
    import w3fast
    with _lock:
        lane = _obs_lanes.get(pid)
        if lane is None:
            lane = _obs_lanes[pid] = w3fast.FastLane(pid, role=w3fast.ROLE_OBSERVER)
    return lane


def read_camera(pid: int) -> dict | None:
    """镜头目标点 + 距离/俯角/视野/朝向（角度换成度）。W3P cam_state，一帧内回执；和导播读的是同一组量。
    09-23 P3：原来在这里自己调 GetCameraTargetPosition / GetCameraField 的 native 地址。"""
    import w3fast
    r = _obs_lane(pid).commands([w3fast.command('cam_state')])[0]
    cs = w3fast.parse_cam_state(r.data) if r is not None and r.ok else None
    if cs is None:
        return None
    x, y = cs['target'][0], cs['target'][1]
    f = cs['fields']
    dist, pitch, fov, yaw = f['distance'], f['aoa'], f['fov'], f['rotation']
    if not (abs(x) < 1e5 and abs(y) < 1e5 and 100 < dist < 20000):
        return None
    deg = 180.0 / math.pi
    return {'x': x, 'y': y, 'dist': dist, 'pitch': pitch * deg % 360.0,
            'fov': (fov or 0) * deg or 70.0, 'yaw': (yaw or 0) * deg % 360.0}


def fog_checker(pid: int):
    """返回 visible(x, y) -> True/False/None：那一点本方现在看不看得见（不在迷雾/黑区里）。W3P q_visible（本机玩家视角）。"""
    import w3fast
    lane = _obs_lane(pid)

    def visible(x: float, y: float) -> bool | None:
        r = lane.commands([w3fast.command('q_visible', x=float(x), y=float(y))])[0]
        return None if r is None or not r.ok else bool(r.values[0])
    return visible


def _camtalk(pid: int, create: bool = False) -> camtalk.CamTalk | None:
    """同茶话会：按实例号存，每一轮现查这个实例当前的 pid（每局换进程）。"""
    inst = _inst_of(pid)
    key = inst if inst else -pid
    with _lock:
        c = _camtalks.get(key)
    if c is None and create:
        cfg = camtalk.CamTalkConfig()
        cfg.update(_camtalk_cfg)
        current = (lambda: _pid_of(inst)) if inst else (lambda: pid)

        def snap():
            p = current()
            if not p:
                return None
            c.pid = p
            return read_snapshot(p, tries=5)

        def cam():
            p = current()
            return read_camera(p) if p else None

        def send(bubbles):
            p = current()
            if not p:
                raise RuntimeError(f'{inst} 号实例现在没有游戏进程')
            return _send(p, bubbles)

        def fog():
            p = current()
            return fog_checker(p) if p else None

        c = camtalk.CamTalk(inst, snapshot=snap, camera=cam, llm=llm_text, send=send, config=cfg, fog=fog,
                            quiet=lambda: time.monotonic() < _event_quiet.get(key, 0.0))
        c.pid = pid
        with _lock:
            _camtalks[key] = c
    return c


@app.get('/api/camtalk')
def api_camtalk_get(pid: int | None = None, inst: int | None = None):
    pid = resolve_pid(pid, inst)
    c = _camtalk(pid)
    if c is None:
        cfg = camtalk.CamTalkConfig()
        cfg.update(_camtalk_cfg)
        return {'pid': pid, 'running': False, 'config': dict(cfg.__dict__), 'view': [], 'transcript': [], 'scene': ''}
    return dict(c.status(), pid=pid)


@app.post('/api/camtalk')
def api_camtalk_set(req: CamTalkRequest):
    pid = resolve_pid(req.pid, req.inst)
    c = _camtalk(pid, create=True)
    changes = {k: v for k, v in req.model_dump(exclude={'pid', 'inst', 'enabled'}).items() if v is not None}
    if changes:
        c.config.update(changes)
        _camtalk_cfg.update(changes)
        _save_config()
    if req.enabled is True:
        c.start()
    elif req.enabled is False:
        c.stop()
    if req.enabled is not None:
        _mark_running('camtalk', pid, req.enabled)
    return dict(c.status(), pid=pid)


# ── 战况触发（eventtalk.py）─────────────────────────────────
_event_quiet: dict[int, float] = {}      # 实例号 -> 战况台词说到什么时候（这之前镜头对白让一让）


class EventsRequest(Target):
    enabled: bool | None = None
    enabled_events: dict | None = None   # {事件: true/false}
    cooldowns: dict | None = None        # {事件: 秒}
    min_gap_s: float | None = None
    fight_min: int | None = None
    big_loss_n: int | None = None
    hero_low_pct: int | None = None
    only_in_view: bool | None = None
    bystander: bool | None = None        # 相关的人不在镜头里时，镜头里的自己人当旁观者说
    replies: int | None = None
    duration_s: float | None = None
    font_px: int | None = None
    max_chars: int | None = None
    include_foes: bool | None = None
    extra: str | None = None
    temperature: float | None = None


def _eventtalk(pid: int, create: bool = False) -> eventtalk.EventTalk | None:
    inst = _inst_of(pid)
    key = inst if inst else -pid
    with _lock:
        e = _eventtalks.get(key)
    if e is None and create:
        cfg = eventtalk.EventTalkConfig()
        cfg.update(_events_cfg)
        current = (lambda: _pid_of(inst)) if inst else (lambda: pid)

        def snap():
            p = current()
            if not p:
                return None
            e.pid = p
            return read_snapshot(p, tries=5)

        def cam():
            p = current()
            return read_camera(p) if p else None

        def send(bubbles):
            p = current()
            if not p:
                raise RuntimeError(f'{inst} 号实例现在没有游戏进程')
            return _send(p, bubbles)

        def on_say(seconds: float):
            _event_quiet[key] = max(_event_quiet.get(key, 0.0), time.monotonic() + seconds)

        e = eventtalk.EventTalk(inst, snapshot=snap, camera=cam, llm=llm_text, send=send, config=cfg, on_say=on_say)
        e.pid = pid
        with _lock:
            _eventtalks[key] = e
    return e


@app.get('/api/events')
def api_events_get(pid: int | None = None, inst: int | None = None):
    pid = resolve_pid(pid, inst)
    e = _eventtalk(pid)
    if e is None:
        cfg = eventtalk.EventTalkConfig()
        cfg.update(_events_cfg)
        return {'pid': pid, 'running': False, 'config': dict(cfg.__dict__), 'log': [], 'transcript': [],
                'events': [{'key': k, 'name': v[0]} for k, v in eventtalk.EVENTS.items()],
                'counts': {k: [0, 0] for k in eventtalk.EVENTS}}
    return dict(e.status(), pid=pid)


@app.post('/api/events')
def api_events_set(req: EventsRequest):
    pid = resolve_pid(req.pid, req.inst)
    e = _eventtalk(pid, create=True)
    changes = {k: v for k, v in req.model_dump(exclude={'pid', 'inst', 'enabled'}).items() if v is not None}
    if changes:
        e.config.update(changes)
        for k, v in changes.items():                  # 两张表按项合并存盘
            if k in ('enabled_events', 'cooldowns'):
                _events_cfg.setdefault(k, {}).update(v)
            else:
                _events_cfg[k] = v
        _save_config()
    if req.enabled is True:
        e.start()
    elif req.enabled is False:
        e.stop()
    if req.enabled is not None:
        _mark_running('events', pid, req.enabled)
    return dict(e.status(), pid=pid)


# ── 自动开启 ──────────────────────────────────────────────────
_FEATURES = {'banter': lambda p: _banter(p, create=True), 'camtalk': lambda p: _camtalk(p, create=True),
             'events': lambda p: _eventtalk(p, create=True)}


class AutostartRequest(BaseModel):
    banter: list[int] | str | None = None
    camtalk: list[int] | str | None = None
    events: list[int] | str | None = None


def _ids(v) -> list[int]:
    if isinstance(v, str):
        v = re.findall(r'\d+', v)
    return sorted({int(i) for i in v or [] if int(i) > 0})


@app.get('/api/autostart')
def api_autostart_get():
    return {'autostart': _autostart}


@app.post('/api/autostart')
def api_autostart_set(req: AutostartRequest):
    """直接填实例号表（比如 {"banter": "5", "events": [5, 7]}）。从表里去掉的实例会被关掉；新加的 5 秒内开起来。"""
    for kind, v in req.model_dump().items():
        if v is None:
            continue
        new = _ids(v)
        for inst in set(_autostart[kind]) - set(new):
            pid = _pid_of(inst)
            obj = {'banter': _banter, 'camtalk': _camtalk, 'events': _eventtalk}[kind](pid) if pid else None
            if obj:
                obj.stop()
        _autostart[kind] = new
    _save_config()
    return {'autostart': _autostart}


@app.on_event('startup')
def _autostart_watch() -> None:
    """看门线程：自动开启表里的实例在跑、功能没开，就开起来（接口重启、游戏重开进程、实例晚起来都接得上）。"""
    def watch():
        time.sleep(1.0)
        while True:
            for kind, make in _FEATURES.items():
                for inst in list(_autostart[kind]):
                    pid = _pid_of(inst)
                    if not pid:
                        continue
                    try:
                        obj = make(pid)
                        if not (obj.running and obj._thread and obj._thread.is_alive()):
                            obj.start()
                            print(f'{time.strftime("%H:%M:%S")} 自动开启 {kind} @ {inst} 号', flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(f'{time.strftime("%H:%M:%S")} 自动开启 {kind} @ {inst} 号失败：{e}', flush=True)
            time.sleep(5.0)
    threading.Thread(target=watch, name='autostart', daemon=True).start()


@app.get('/api/camera')
def api_camera(pid: int | None = None, inst: int | None = None):
    """调试：镜头现在在哪、画面里有谁（不说话）。"""
    pid = resolve_pid(pid, inst)
    cam = read_camera(pid)
    snap = read_snapshot(pid)
    view = []
    if cam and snap:
        seen = fog_checker(pid)
        view = [{'unit': u.addr_hex, 'name': camtalk.unit_name(u.type), 'owner': u.owner,
                 'visible': True if u.owner == snap.me or not seen else seen(u.x, u.y)}
                for u in snap.units if u.hp > 0 and camtalk.in_view(u, cam)]
    return {'pid': pid, 'camera': cam, 'view': view}


# ── 调试页 ────────────────────────────────────────────────────
@app.get('/', response_class=HTMLResponse)
def page():
    """09-26 起调试台并进了远见（页面「头顶气泡」，console/web/src/pages/Speech.tsx，经 console/server/speechview.py 转发到这里）。
    原来的 speak_api.html 删了，这里只留一个跳转 —— 两套界面各改各的，迟早对不上。"""
    url = f"http://127.0.0.1:{w3paths.port('console', 8866)}/#/speech"
    return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={url}">'
            f'<title>头顶气泡</title><body style="font:14px system-ui;background:#14161c;color:#ddd;padding:24px">'
            f'头顶气泡的调试台已经并进远见：<a style="color:#e0b14a" href="{url}">{url}</a>'
            f'（远见没开的话先双击仓库根目录的 start.bat）。</body></html>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('SPEAK_API_PORT') or w3paths.port('speech', 8872)))
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()

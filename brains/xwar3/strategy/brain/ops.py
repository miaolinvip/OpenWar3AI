# -*- coding: utf-8 -*-
"""对局运行开关：每实例 inst<N>/ops.json（只存改过的项），Farsight 设置页「对局运行」改。

和 brain_config.json（大脑怎么打）分开：这里管「跑不跑、什么时候结束这局、结束后怎么收尾」。
读的一方：
  run_brain.py（监督器）  每 2 秒读：AI 开关、模块开关（开局拉模块时）、卡住自动结束、自动认输、超时、结束后强关游戏
  director_inst.py（导播）每 3 秒读 director_enabled
  Farsight end-game        结束本局后按 kill_game_after_end 兜底
09-18 起因：游戏胜负结算后不能自动结束；被团灭、已无发展空间的局应直接认输并重新开局。
为此强制认输做成可配置项，游戏结束后强制关闭进程也做成开关；是否启动 AI、是否开导播台等功能尽量都放到后台配置。
"""
from __future__ import annotations

import json
import os

SCHEMA = [
    # key, 分组, 标签, 类型, 默认, 最小, 最大, 步长, 说明
    ("ai_enabled", "AI 与模块", "新大脑指挥对局", "bool", True, None, None, None,
     "关掉：监督器照常记录、判结束，但大脑不下任何令；下一局开局也不拉毫秒层模块"),
    ("mod_combat", "AI 与模块", "毫秒层：保命 / 施法 / 补攻击", "bool", True, None, None, None, "mod_combat；下一局开局生效"),
    ("mod_scavenge", "AI 与模块", "毫秒层：捡装备", "bool", True, None, None, None, "mod_scavenge；下一局开局生效"),
    ("mod_spy", "AI 与模块", "侦察记录", "bool", True, None, None, None, "mod_spy（只记录不下令）；下一局开局生效"),
    ("mod_fight", "AI 与模块", "战斗帧记录", "bool", True, None, None, None, "mod_fight（交战时 150ms 一帧写 fights/）；下一局开局生效"),
    ("director_enabled", "导播", "自动导播（运镜）", "bool", True, None, None, None, "每实例导播进程按它开停，3 秒内生效"),
    ("hud_labels", "导播", "游戏左上角显示对局号、时间和日志面板", "bool", True, None, None, None,
     "「任务」按钮显示 War3AI-实例号-对局号（对局文件名里那个三位数，例 War3AI-5-021），点开是本局的「对局日志」「当前状态」；"
     "「盟友」按钮显示游戏已开始的时间 mm:ss。⚠ 单人局任务面板开着时游戏暂停（不会被判成对局卡住）。下一局开局生效"),
    ("auto_end_stuck", "对局结束", "胜负画面 / 读不到对局时自动结束", "bool", True, None, None, None,
     "快照一直是空的或游戏时钟不走（结算画面、对象在拆）超过下面的秒数，就按最后一份正常快照判胜负、结束这局"),
    ("end_stuck_s", "对局结束", "读不到对局多少秒算结束", "int", 15, 5, 120, 1, "墙钟秒"),
    ("kill_game_after_end", "对局结束", "结束后强制关闭游戏进程", "bool", True, None, None, None,
     "发了结束令，游戏进程过了下面的秒数还在就直接关掉（关掉后 farm 按下一局设置重开）"),
    ("kill_wait_s", "对局结束", "等游戏自己退出多少秒", "int", 10, 2, 60, 1, "墙钟秒"),
    ("max_game_minutes", "对局结束", "一局最长多少游戏分钟", "float", 40.0, 5.0, 120.0, 1.0, "到了按超时结束"),
    ("auto_gg", "自动认输", "打不过就 GG", "bool", True, None, None, None,
     "开局超过最短时间后，我方强度低于对面的比例、作战单位 + 英雄不超过上限，持续够久就认输结束，开下一局"),
    ("gg_min_minutes", "自动认输", "开局多少游戏分钟后才允许认输", "float", 6.0, 0.0, 60.0, 0.5, ""),
    ("gg_strength_ratio", "自动认输", "我方强度低于对面的比例", "float", 0.3, 0.05, 1.0, 0.05, "我方强度 < 对面 × 这个比例"),
    ("gg_army_max", "自动认输", "我方作战单位 + 英雄不超过", "int", 3, 0, 60, 1, "被团灭的判据：剩下的能打的不超过这么多"),
    ("gg_hold_s", "自动认输", "持续多少游戏秒", "int", 45, 5, 600, 5, "条件连续成立这么久才认输（中途恢复就重新计时）"),
    ("issue_hotkey", "问题备忘", "游戏里记问题的快捷键", "choice", "pause", None, None, None,
     "在游戏窗口里按一下：游戏暂停（打开任务面板，里面是本局的对局日志）并记下实例号、局号、游戏时间点，"
     "到 Farsight「问题备忘」页补一句描述；再按一次继续。键是 DLL 在游戏里认的（render_hook 动作 57），监督器开局时下发，改了下一轮就生效"),
]

# 问题快捷键：选项 -> (显示名, 虚拟键码)。War3 自己不用这几个键；0 = 关
HOTKEYS = {"pause": ("Pause/Break", 0x13), "scroll": ("Scroll Lock", 0x91), "insert": ("Insert", 0x2D), "off": ("关", 0)}
CHOICES = {"issue_hotkey": HOTKEYS}

DEFAULTS = {k: d for k, _g, _l, _t, d, *_ in SCHEMA}
FILE = "ops.json"


def schema() -> list[dict]:
    return [dict(key=k, group=g, label=l, type=t, default=d, min=mn, max=mx, step=st, desc=desc,
                 **({"options": list(CHOICES[k]), "labels": {o: v[0] for o, v in CHOICES[k].items()}} if t == "choice" else {}))
            for k, g, l, t, d, mn, mx, st, desc in SCHEMA]


def coerce(key: str, value):
    row = next((r for r in SCHEMA if r[0] == key), None)
    if row is None:
        raise KeyError(key)
    t, mn, mx = row[3], row[5], row[6]
    if t == "bool":
        return bool(value)
    if t == "choice":
        v = str(value)
        if v not in CHOICES[key]:
            raise KeyError(f"{key}={v}")
        return v
    v = int(round(float(value))) if t == "int" else float(value)
    if mn is not None:
        v = max(mn, v)
    if mx is not None:
        v = min(mx, v)
    return v


def load_overrides(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            out[k] = coerce(k, v)
        except (KeyError, TypeError, ValueError):
            pass
    return out


_cache: dict = {}


def load(inst_dir: str) -> dict:
    """默认值 + ops.json 里改过的项；按文件修改时间缓存（监督器一秒读几次不费事）。"""
    path = os.path.join(inst_dir, FILE)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = None
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    vals = dict(DEFAULTS, **(load_overrides(path) if mt is not None else {}))
    _cache[path] = (mt, vals)
    return vals


def save(inst_dir: str, patch: dict) -> dict:
    """patch = {key: 值 或 None（None = 恢复默认）}；只存和默认不同的项。返回 {overrides, values}。"""
    path = os.path.join(inst_dir, FILE)
    ov = load_overrides(path)
    for k, v in patch.items():
        if k not in DEFAULTS:
            raise KeyError(k)
        if v is None:
            ov.pop(k, None)
            continue
        v = coerce(k, v)
        if v == DEFAULTS[k]:
            ov.pop(k, None)
        else:
            ov[k] = v
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ov, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return {"overrides": ov, "values": dict(DEFAULTS, **ov)}


def kill_game(inst: int, pid: int) -> str:
    """强制关游戏：先直接杀进程；杀不动（游戏提权）就杀这个实例的启动器守护（游戏挂在它的 Job 里，跟着退，零 UAC）。
    ★ 09-19 pid 会被系统复用：直接杀之前核对进程名是 War3.exe / War3-<N>.exe，不是就当游戏已经退了
    （Farsight「全部停止」按一个昨晚的旧 pid 杀掉过一个无关的程序；farsight/server/instances.py WAR3_EXE 同一条规则）。"""
    import re
    import psutil
    try:
        p = psutil.Process(pid)
        if not re.match(r"^war3(-\d+)?\.exe$", p.name() or "", re.I):
            return "已经退了（这个 pid 现在是别的程序，没动它）"
        p.kill()
        return "直接关掉了"
    except psutil.NoSuchProcess:
        return "已经退了"
    except psutil.Error:
        pass
    n = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "war3_launch.exe" and any(f"War3-{inst}.exe" in (c or "") for c in p.info["cmdline"] or []):
                p.kill()
                n += 1
        except psutil.Error:
            pass
    return f"关了启动器守护 {n} 个" if n else "关不掉"

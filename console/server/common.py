# -*- coding: utf-8 -*-
"""Farsight 公共：路径、静态游戏数据（中文名、订单号、兵种数值、英雄技能怎么放）。

只读、进程内缓存。服务端和实例 worker 共用。
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
FARSIGHT = os.path.dirname(HERE)
ROOT = w3paths.ROOT
LAB = w3paths.RUNTIME              # farm.py 在这里
BIN = w3paths.BIN
FARM_DIR = w3paths.FARM_DIR
MODDIR = w3paths.SDK
ANALYTICS = w3paths.DIRECTOR        # director_inst.py 在这里
BRAIN = w3paths.STRATEGY
W3DATA = w3paths.GAME_DATA
ORDER_IDS = w3paths.ORDER_IDS
WEB_DIST = os.path.join(FARSIGHT, "web", "dist")
SETTINGS = os.path.join(FARSIGHT, "settings.json")
PYTHON = w3paths.python_exe()


def inst_dir(n: int) -> str:
    return os.path.join(BIN, f"inst{n}")


def rawcode(code: str) -> int:
    b = code.encode("latin-1")
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def code_of(raw: int) -> str:
    return bytes(((raw >> 24) & 255, (raw >> 16) & 255, (raw >> 8) & 255, raw & 255)).decode("latin-1", "replace")


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def jsonl_from(path: str, offset: int, max_bytes: int = 2 * 1024 * 1024) -> tuple[list, int]:
    """从字节偏移 offset 往后读完整的行（最后半行留到下次），返回 (记录, 新偏移)。文件变短了（换局清空 / 换新文件）从头读。
    页面增量刷新用：只读上次之后新写的，不用每次把整个尾巴重读一遍（09-26 决策明细页以前每 1.5 秒读 8 MB）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        raw = fh.read(min(max_bytes, size - offset))
    cut = raw.rfind(b"\n") + 1
    if not cut and len(raw) >= max_bytes:
        return [], offset + len(raw)          # 一行比 max_bytes 还长：跳过去，别一直卡在这里
    out = []
    for ln in raw[:cut].splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out, offset + cut


@lru_cache(maxsize=1)
def names() -> dict:
    return (load_json(w3paths.NAMES_JSON, {}) or {}).get("byCode", {})


@lru_cache(maxsize=1)
def names_en() -> dict:
    """四字码 -> 英文版游戏显示名（data/game/names_en.json，09-25；单位/建筑/英雄/技能/物品/科技 2000 多条）"""
    return (load_json(os.path.join(w3paths.GAME_DATA, "names_en.json"), {}) or {}).get("byCode", {})


def names_for(lang: str = "zh") -> dict:
    """界面语言对应的名字表：英文缺的退回中文"""
    return names() if lang != "en" else names() | names_en()


def name_map_en() -> dict:
    """中文名 -> 英文名：后端有些地方直接给中文名（大脑的计划、决策记录），远见英文界面按整段名字换"""
    en, out = names_en(), {}
    for code, zh in list(names().items()) + list(ability_names().items()):
        if code in en and zh and zh not in out:
            out[zh] = en[code]
    return out


@lru_cache(maxsize=1)
def orders() -> dict:
    """订单号 -> 名字（order-ids.txt）。"""
    out = {}
    try:
        with open(ORDER_IDS, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                m = re.match(r"^(\w+)\s+0x([0-9A-Fa-f]{8})", ln)
                if m:
                    out.setdefault(int(m.group(2), 16), m.group(1))
    except OSError:
        pass
    return out


@lru_cache(maxsize=1)
def order_ids() -> dict:
    return {v: k for k, v in orders().items()}


@lru_cache(maxsize=1)
def units() -> dict:
    d = load_json(w3paths.UNITS_JSON, {}) or {}
    return d if isinstance(d, dict) else {u["id"]: u for u in d}


@lru_cache(maxsize=1)
def ability_names() -> dict:
    """技能码 -> 中文名（HumanAbilityStrings.txt 等 *Strings.txt 的 Name=），没有中文的退回 skills.json 英文名。"""
    out = {}
    for code, v in (load_json(os.path.join(W3DATA, "skills.json"), {}) or {}).items():
        out[code] = v.get("name", code)
    for fn in os.listdir(W3DATA) if os.path.isdir(W3DATA) else []:
        if not fn.endswith("Strings.txt"):
            continue
        cur = None
        try:
            with open(os.path.join(W3DATA, fn), encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    m = re.match(r"^\[(\w{4})\]", ln)
                    if m:
                        cur = m.group(1)
                    elif cur and ln.startswith("Name="):
                        out[cur] = ln[5:].strip().strip('"')
        except OSError:
            pass
    return out


# 英雄/常用单位技能：技能码 -> (订单名, 目标类型 point/unit/instant)。订单号从 order-ids.txt 查，不手写数字。
CASTS = {
    # 人族
    "AHbz": ("blizzard", "point"), "AHwe": ("waterelemental", "instant"), "AHmt": ("massteleport", "unit"),
    "AHtb": ("thunderbolt", "unit"), "AHtc": ("thunderclap", "instant"), "AHav": ("avatar", "instant"),
    "AHhb": ("holybolt", "unit"), "AHds": ("divineshield", "instant"), "AHre": ("resurrection", "instant"),
    "AHfs": ("flamestrike", "point"), "AHbn": ("banish", "unit"), "AHdr": ("drain", "unit"), "AHpx": ("summonphoenix", "instant"),
    "Ahea": ("heal", "unit"), "Ainf": ("innerfire", "unit"), "Aslo": ("slow", "unit"), "Aply": ("polymorph", "unit"),
    "Adis": ("dispel", "point"), "Aivs": ("invisibility", "unit"), "Afla": ("flare", "point"), "Amic": ("militia", "instant"),
    "Adef": ("defend", "instant"), "Aclf": ("cloudoffog", "point"),
    # 兽族
    "AOcl": ("chainlightning", "unit"), "AOfs": ("farsight", "point"), "AOsf": ("spiritwolf", "instant"), "AOeq": ("earthquake", "point"),
    "AOwk": ("windwalk", "instant"), "AOmi": ("mirrorimage", "instant"), "AOhw": ("healingwave", "unit"), "AOhx": ("hex", "unit"),
    "AOsw": ("serpentward", "point"), "AOvd": ("voodoo", "instant"), "AOws": ("stomp", "instant"), "AOsh": ("shockwave", "point"),
    # 亡灵
    "AUcs": ("carrionswarm", "point"), "AUsl": ("sleep", "unit"), "AUfn": ("frostnova", "unit"), "AUdc": ("deathcoil", "unit"),
    "AUdp": ("deathpact", "unit"), "AUdr": ("darkritual", "unit"), "AUdd": ("deathanddecay", "point"), "AUan": ("animatedead", "instant"),
    # 暗夜
    "AEer": ("entanglingroots", "unit"), "AEfn": ("forceofnature", "point"), "AEtq": ("tranquility", "instant"),
    "AEst": ("starfall", "instant"), "AEmb": ("manaburn", "unit"), "AEme": ("metamorphosis", "instant"), "AEim": ("immolation", "instant"),
    "AEbl": ("blink", "point"), "AEfk": ("fanofknives", "instant"), "AEsh": ("shadowstrike", "unit"), "AEsv": ("vengeance", "instant"),
}


def cast_info(code: str) -> dict | None:
    c = CASTS.get(code)
    if not c:
        return None
    oid = order_ids().get(c[0])
    return None if oid is None else {"order": oid, "orderName": c[0], "target": c[1]}


WORKERS = {"hpea", "opeo", "uaco", "ewsp"}
HALLS = {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}
TOWERS = {"hgtw", "hctw", "hatw", "hwtw", "owtw", "uzg1", "uzg2", "etrp"}
MINES = {"ngol", "ugol", "egol"}


def category(code: str) -> str:
    """hero / worker / hall / tower / building / mine / unit。"""
    if code in MINES:
        return "mine"
    if code and "A" <= code[0] <= "Z":
        return "hero"
    if code in WORKERS:
        return "worker"
    if code in HALLS:
        return "hall"
    if code in TOWERS:
        return "tower"
    u = units().get(code) or {}
    if float(u.get("spd") or 0) <= 0 and (u.get("bldtm") or u.get("hp")):
        return "building"
    return "unit"


def meta(lang: str = "zh") -> dict:
    """前端启动时拿一次：名字、订单名、兵种数值、技能名、放法。lang=en 时名字/技能名用英文（缺的退回中文），
    另带 nameMap（中文名 -> 英文名）给前端换后端直接给的中文名。"""
    us = units()
    en = lang == "en"
    abil = ability_names()
    out = {
        "names": names_for(lang),
        "orders": {f"{k:08X}": v for k, v in orders().items()},
        "units": {k: {f: v.get(f) for f in ("hp", "armor", "food", "gold", "lumber", "level", "spd", "range", "dmgAvg", "cool", "atk", "defT")}
                  for k, v in us.items()},
        "abilities": abil | {k: v for k, v in names_en().items() if k in abil} if en else abil,
        "casts": {k: cast_info(k) for k in CASTS if cast_info(k)},
    }
    if en:
        out["nameMap"] = name_map_en()
    return out

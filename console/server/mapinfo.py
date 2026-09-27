# -*- coding: utf-8 -*-
"""地图库：列出 <游戏目录>\\Maps 下的对战地图，读地图信息（名字/人数/作者/描述/起点/金矿）、生成预览图 PNG。

地图文件 = 512 字节 HM3W 头 + MPQ。预览图用 war3mapPreview.tga（有就用），没有就用小地图 war3mapMap.blp。
★ 这两个文件在暴雪地图里是 **PKWARE DCL implode** 压缩（mpyq 只认 zlib/bzip2，报 Unsupported compression type），
  所以自己带一个 MPQ 读取器和 explode —— 09-24 起在 sdk/python/w3mpq.py，和 SDK 的地图数据共用。BLP/TGA 交给 Pillow。
结果缓存在 farsight/cache/maps/<文件名>-<大小>-<修改时间>.{png,json}，地图不变不重算。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
import zlib

import w3paths  # noqa: E402  调用方先导 server.common（它把 sdk/python 放上 sys.path）


def maps_root() -> str:
    """地图目录 = 玩家设置的游戏目录\\Maps（openwar3.json 另写了 maps_dir 就用它）。
    ⚠ 09-26 起每次现取：游戏目录可以在远见里改（控制中心 / 设置），以前是 import 时定死的常量，改了目录要重启远见。"""
    return w3paths.maps_dir()


def rel(path: str) -> str:
    """相对地图目录的路径（下一局设置、网页上显示都用它：换了游戏目录也不用改）；不在地图目录下的原样返回。"""
    if not maps_root():
        return path
    root = os.path.normcase(os.path.abspath(maps_root()))
    p = os.path.abspath(path)
    return os.path.relpath(p, maps_root()) if os.path.normcase(p).startswith(root + os.sep) else path


def resolve(path: str) -> str | None:
    """网页 / 下一局设置给的地图路径（相对地图目录，或老的绝对路径）-> 绝对路径；不在地图目录里 = None。"""
    if not path or not maps_root():                  # 游戏目录还没设
        return None
    p = os.path.abspath(path if os.path.isabs(path) else os.path.join(maps_root(), path))
    root = os.path.normcase(os.path.abspath(maps_root()))
    return p if os.path.normcase(p).startswith(root + os.sep) else None


HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(HERE), "cache", "maps")
# 缓存格式版本：加了字段就 +1，旧缓存自动重算（预热线程开机时会把所有地图过一遍）
CACHE_VER = 3                    # 3：名字 / 作者 / 描述去掉游戏的颜色代码
_COLOR = re.compile(r"\|[cC][0-9a-fA-F]{8}|\|[rR]|\|[nN]")


def plain(s: str) -> str:
    """去掉魔兽文字里的颜色代码（|cffffcc00…|r）和 |n 换行：网页上照原样显示就是一串乱码。"""
    return _COLOR.sub(lambda m: "\n" if m.group(0).lower() == "|n" else "", s).strip()
# 同名的几份地图怎么区分（09-26 选地图列表里出现几项一模一样的名字 —— 本机有 4 份 Echo Isles：原版、注了 AMAI 的、
# 固定成人族对电脑人族的 HvH、别的图包里改过脚本的）：记下各部分的指纹，网页上同名的几份互相比，告诉人哪里不一样。
FINGERPRINT = ("war3map.j", "war3map.w3e", "war3map.w3i", "war3map.doo", "war3mapUnits.doo")
AI_SCRIPTS = ("Scripts\\common.ai", "Scripts\\human.ai", "Scripts\\orc.ai", "Scripts\\undead.ai", "Scripts\\elf.ai")
FLAG_FIXED_SLOTS = 0x20          # war3map.w3i：固定的玩家设置（种族 / 电脑都写死在地图里，大厅里改不了）


# MPQ 读取 / explode / wts 和 SDK 共用一份（sdk/python/w3mpq.py，09-24 搬过去）
from w3mpq import Mpq, explode, mpq_hash, decrypt, parse_wts, trigstr as _trigstr  # noqa: F401,E402


def _cstr(b: bytes, off: int) -> tuple[str, int]:
    e = b.index(b"\0", off)
    return b[off:e].decode("utf-8", "replace"), e + 1


RACES = {0: "可选", 1: "人族", 2: "兽族", 3: "亡灵", 4: "暗夜"}


def parse_w3i(b: bytes, wts: dict) -> dict:
    o = 0

    def i32():
        nonlocal o
        v = struct.unpack_from("<i", b, o)[0]
        o += 4
        return v

    def f32():
        nonlocal o
        v = struct.unpack_from("<f", b, o)[0]
        o += 4
        return v

    def s():
        nonlocal o
        v, o = _cstr(b, o)
        return v

    ver = i32()
    i32(); i32()
    name, author, desc, players = s(), s(), s(), s()
    cam = [f32() for _ in range(8)]
    comp = [i32() for _ in range(4)]
    w, h = i32(), i32()
    flags = i32()
    tileset = chr(b[o]); o += 1
    if ver >= 25:          # 冰封王座：载入画面编号、自定义载入模型路径、文字/标题/副标题、游戏数据集、序幕路径/文字/标题/副标题
        i32(); s(); s(); s(); s(); i32(); s(); s(); s(); s()
    else:                  # 混乱之治（v18）：载入画面编号、文字/标题/副标题、序幕编号、文字/标题/副标题
        i32(); s(); s(); s(); i32(); s(); s(); s()
    if ver >= 25:
        i32(); f32(); f32(); f32(); o += 4
        o += 4
        s()
        o += 1
        o += 4
    slots = []
    for _ in range(i32()):
        pid, ptype, race, fixed = i32(), i32(), i32(), i32()
        pname = s()
        sx, sy = f32(), f32()
        i32(); i32()
        slots.append({"id": pid, "type": ptype, "race": race, "raceName": RACES.get(race, str(race)), "name": _trigstr(pname, wts),
                      "x": round(sx), "y": round(sy)})
    return {"version": ver, "name": _trigstr(name, wts), "author": _trigstr(author, wts), "description": _trigstr(desc, wts),
            "suggested": _trigstr(players, wts), "cameraBounds": cam, "complements": comp, "playableWidth": w, "playableHeight": h,
            "flags": flags, "tileset": tileset, "players": slots}


def parse_mmp(b: bytes | None) -> list:
    """小地图图标：0 金矿 1 房子 2 起点；坐标 0~255（和小地图图片同一个框）。"""
    if not b or len(b) < 8:
        return []
    _ver, n = struct.unpack_from("<ii", b, 0)
    out = []
    for i in range(n):
        if 8 + (i + 1) * 16 > len(b):
            break
        kind, x, y, color = struct.unpack_from("<iiiI", b, 8 + i * 16)
        out.append({"kind": {0: "mine", 1: "house", 2: "start"}.get(kind, kind), "x": x, "y": y})
    return out


def parse_w3e_header(b: bytes | None) -> dict | None:
    """地形头：宽高（格点数）和左下角偏移 —— 整张地图的世界坐标框，小地图图片就是按它画的。"""
    if not b or b[:4] != b"W3E!":
        return None
    o = 8
    o += 1 + 4
    n = struct.unpack_from("<i", b, o)[0]
    o += 4 + n * 4
    n = struct.unpack_from("<i", b, o)[0]
    o += 4 + n * 4
    w, h = struct.unpack_from("<ii", b, o)
    cx, cy = struct.unpack_from("<ff", b, o + 8)
    return {"width": w, "height": h, "minX": cx, "minY": cy, "maxX": cx + (w - 1) * 128.0, "maxY": cy + (h - 1) * 128.0}


def list_maps(root: str | None = None) -> list[dict]:
    """地图目录下（含所有子文件夹：FrozenThrone、Download、别的图包……）的全部 .w3x / .w3m。
    人数先按文件名的 (N) 前缀；没有前缀的（Download 里下载的图常常没有）由 /api/maps 用地图里读出来的玩家位补上。"""
    root = root or maps_root()
    out = []
    if not root or not os.path.isdir(root):          # 游戏目录还没设 / 不对
        return out
    for d, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith((".w3x", ".w3m")):
                continue
            p = os.path.join(d, fn)
            m = re.match(r"\((\d+)\)", fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"path": p, "file": fn, "folder": os.path.relpath(d, root), "players": int(m.group(1)) if m else None,
                        "size": st.st_size, "mtime": int(st.st_mtime), "tft": fn.lower().endswith(".w3x")})
    # 按文件夹排（根目录 "." 在最前），不再把 FrozenThrone 顶到最前 —— 看上去像只读了那一个目录
    out.sort(key=lambda x: (x["folder"].lower(), x["players"] or 99, x["file"].lower()))
    return out


def fingerprint(blob: bytes, mpq, flags: int) -> dict:
    """区分同名地图用：整份文件的哈希、各部分（脚本 / 地形 / 地图设置 / 装饰物 / 预放单位）的哈希、
    自带的电脑 AI 脚本（AMAI 这类往地图里注 Scripts\\*.ai 的）、是不是固定玩家设置。缺的部分记 None，不算错。"""
    def raw(name):
        try:
            return mpq.read(name)
        except Exception:  # noqa: BLE001
            return None

    parts = {}
    for name in FINGERPRINT:
        d = raw(name)
        parts[name] = hashlib.sha1(d).hexdigest()[:12] if d else None
    ai = [name.split("\\")[-1] for name in AI_SCRIPTS if raw(name)]
    common = raw("Scripts\\common.ai") or b""
    return {"sha1": hashlib.sha1(blob).hexdigest()[:16], "parts": parts, "aiScripts": ai,
            "aiKind": ("AMAI" if b"AMAI" in common else "custom") if ai else None,
            "fixedSlots": bool(flags & FLAG_FIXED_SLOTS)}


def _cache_key(path: str) -> str:
    st = os.stat(path)
    base = re.sub(r"[^\w\-\(\)]+", "_", os.path.basename(path))
    return f"{base}-{st.st_size}-{int(st.st_mtime)}"


def info(path: str) -> dict:
    if path and not os.path.isabs(path):
        path = os.path.join(maps_root(), path)
    os.makedirs(CACHE, exist_ok=True)
    key = _cache_key(path)
    jp = os.path.join(CACHE, key + ".json")
    if os.path.exists(jp):
        try:
            with open(jp, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("v") == CACHE_VER:
                return cached
        except (OSError, ValueError):
            pass
    blob = open(path, "rb").read()
    out = {"path": path, "file": os.path.basename(path), "errors": {}}
    if blob[:4] == b"HM3W":
        out["headerName"] = blob[8:].split(b"\0", 1)[0].decode("utf-8", "replace")
    try:
        mpq = Mpq(blob)
    except Exception as exc:  # noqa: BLE001
        out["errors"]["mpq"] = str(exc)
        return out

    def rd(name):
        try:
            return mpq.read(name)
        except Exception as exc:  # noqa: BLE001
            out["errors"][name] = f"{type(exc).__name__}: {exc}"
            return None

    wts = parse_wts(rd("war3map.wts"))
    w3i = rd("war3map.w3i")
    if w3i:
        try:
            out.update(parse_w3i(w3i, wts))
            for k in ("name", "author", "description", "suggested"):
                if isinstance(out.get(k), str):
                    out[k] = plain(out[k])
        except Exception as exc:  # noqa: BLE001
            out["errors"]["w3i"] = f"{type(exc).__name__}: {exc}"
    out["icons"] = parse_mmp(rd("war3map.mmp"))
    out["terrain"] = parse_w3e_header(rd("war3map.w3e"))
    out.update(fingerprint(blob, mpq, out.get("flags") or 0))
    out["v"] = CACHE_VER
    png = os.path.join(CACHE, key + ".png")
    out["preview"] = None
    for name in ("war3mapPreview.tga", "war3mapMap.blp", "war3mapMap.tga"):
        data = rd(name)
        if not data:
            continue
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
            img.convert("RGB").save(png)
            out["preview"] = key + ".png"
            out["previewSource"] = name
            out["previewSize"] = list(img.size)
            break
        except Exception as exc:  # noqa: BLE001
            out["errors"][name + "·解图"] = f"{type(exc).__name__}: {exc}"
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    return out


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else w3paths.default_map()
    for f in os.listdir(CACHE) if os.path.isdir(CACHE) else []:
        if os.path.basename(p).split(".")[0] in f:
            os.remove(os.path.join(CACHE, f))
    r = info(p)
    print(json.dumps({k: v for k, v in r.items() if k != "description"}, ensure_ascii=False, indent=1)[:2500])

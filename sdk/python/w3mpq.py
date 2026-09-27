# -*- coding: utf-8 -*-
"""读地图文件（.w3x / .w3m = 512 字节 HM3W 头 + MPQ 归档）：MPQ 读取、PKWARE DCL explode、字符串表 wts、
物体编辑器数据（w3u 单位 / w3t 物品 / w3a 技能 / w3h 魔法效果 / w3b 可破坏物 / w3d 装饰物 / w3q 升级）。

★ 暴雪地图里的小文件多是 **PKWARE DCL implode** 压缩（mpyq 只认 zlib/bzip2），所以这里自己带 explode（照 zlib contrib/blast.c）。
远见的地图库（console/server/mapinfo.py）和 SDK 的地图数据（openwar3/mapdata.py）共用这一份 —— 别再抄第二份。
放在 openwar3 包外面（和 w3fast/w3world 一样是格式层）：MPQ 的哈希/解密常量是大数字，门面包的无地址检查不放行大常量。

    m = Mpq(open(path, "rb").read())
    wts = parse_wts(m.read("war3map.wts"))
    units = parse_objects(m.read("war3map.w3u"), wts)        # {"h000": {"base": "hfoo", "fields": {"unam": "玩伴", ...}}}
"""
from __future__ import annotations

import re
import struct
import zlib


# ── MPQ 加解密 ──
def _crypt_table():
    seed, t = 0x00100001, [0] * 0x500
    for i in range(256):
        index = i
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            t1 = (seed & 0xFFFF) << 0x10
            seed = (seed * 125 + 3) % 0x2AAAAB
            t[index] = t1 | (seed & 0xFFFF)
            index += 0x100
    return t


CT = _crypt_table()


def mpq_hash(text: str, kind: int) -> int:
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in text.upper().replace("/", "\\"):
        c = ord(ch)
        s1 = (CT[(kind << 8) + c] ^ (s1 + s2)) & 0xFFFFFFFF
        s2 = (c + s1 + s2 + (s2 << 5) + 3) & 0xFFFFFFFF
    return s1


def decrypt(data: bytes, key: int) -> bytes:
    s1, s2 = key, 0xEEEEEEEE
    n = len(data) // 4
    vals = struct.unpack_from(f"<{n}I", data)
    out = []
    for v in vals:
        s2 = (s2 + CT[0x400 + (s1 & 0xFF)]) & 0xFFFFFFFF
        p = v ^ ((s1 + s2) & 0xFFFFFFFF)
        s1 = ((((~s1) << 0x15) & 0xFFFFFFFF) + 0x11111111) | (s1 >> 0x0B)
        s1 &= 0xFFFFFFFF
        s2 = (p + s2 + (s2 << 5) + 3) & 0xFFFFFFFF
        out.append(p)
    return struct.pack(f"<{n}I", *out) + data[n * 4:]


# ── PKWARE DCL explode（zlib contrib/blast.c）──
MAXBITS = 13
_LITLEN = bytes([11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8, 9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9,
                 11, 12, 6, 7, 22, 5, 7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12, 8, 12, 5, 38, 5, 38, 5, 11, 7,
                 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27, 44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44, 173])
_LENLEN = bytes([2, 35, 36, 53, 38, 23])
_DISTLEN = bytes([2, 20, 53, 230, 247, 151, 248])
_BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)


def _construct(rep: bytes):
    length = []
    for b in rep:
        length += [b & 15] * ((b >> 4) + 1)
    count = [0] * (MAXBITS + 1)
    for ln in length:
        count[ln] += 1
    offs = [0] * (MAXBITS + 2)
    for ln in range(1, MAXBITS):
        offs[ln + 1] = offs[ln] + count[ln]
    symbol = [0] * len(length)
    for sym, ln in enumerate(length):
        if ln:
            symbol[offs[ln]] = sym
            offs[ln] += 1
    return count, symbol


_LIT, _LEN, _DIST = _construct(_LITLEN), _construct(_LENLEN), _construct(_DISTLEN)


class _Bits:
    __slots__ = ("data", "pos", "buf", "cnt")

    def __init__(self, data: bytes):
        self.data, self.pos, self.buf, self.cnt = data, 0, 0, 0

    def bits(self, need: int) -> int:
        while self.cnt < need:
            if self.pos >= len(self.data):
                raise EOFError
            self.buf |= self.data[self.pos] << self.cnt
            self.pos += 1
            self.cnt += 8
        v = self.buf & ((1 << need) - 1)
        self.buf >>= need
        self.cnt -= need
        return v

    def decode(self, h) -> int:
        count, symbol = h
        code = first = index = 0
        for ln in range(1, MAXBITS + 1):
            code |= self.bits(1) ^ 1                      # 码字按位取反存（blast.c 的 invert code）
            c = count[ln]
            if code < first + c:
                return symbol[index + code - first]
            index += c
            first = (first + c) << 1
            code <<= 1
        raise ValueError("explode: 坏的哈夫曼码")


def explode(data: bytes, out_size: int | None = None) -> bytes:
    s = _Bits(data)
    lit = s.bits(8)
    dict_bits = s.bits(8)
    if lit > 1 or not 4 <= dict_bits <= 6:
        raise ValueError(f"explode: 头不对 lit={lit} dict={dict_bits}")
    out = bytearray()
    while True:
        if s.bits(1):
            sym = s.decode(_LEN)
            ln = _BASE[sym] + s.bits(_EXTRA[sym])
            if ln == 519:
                break
            sh = 2 if ln == 2 else dict_bits
            dist = (s.decode(_DIST) << sh) + s.bits(sh) + 1
            if dist > len(out):
                raise ValueError("explode: 距离超出")
            start = len(out) - dist
            for i in range(ln):
                out.append(out[start + i])
        else:
            out.append(s.decode(_LIT) if lit else s.bits(8))
        if out_size is not None and len(out) >= out_size:
            break
    return bytes(out)


# ── MPQ 读取 ──
class Mpq:
    def __init__(self, blob: bytes):
        off = 0
        while off < len(blob):
            if blob[off:off + 4] == b"MPQ\x1a":
                break
            off += 512
        else:
            raise ValueError("没有 MPQ 头")
        self.blob, self.base = blob, off
        hdr_size, arc_size, fmt, shift, ht_pos, bt_pos, ht_n, bt_n = struct.unpack_from("<IIHHIIII", blob, off + 4)
        self.sector = 512 << shift
        self.hash = decrypt(blob[off + ht_pos: off + ht_pos + ht_n * 16], mpq_hash("(hash table)", 3))
        self.block = decrypt(blob[off + bt_pos: off + bt_pos + bt_n * 16], mpq_hash("(block table)", 3))
        self.ht_n = ht_n

    def _find(self, name: str):
        a, b = mpq_hash(name, 1), mpq_hash(name, 2)
        i = mpq_hash(name, 0) % self.ht_n
        for _ in range(self.ht_n):
            ha, hb, _loc, _plat, bi = struct.unpack_from("<IIHHI", self.hash, i * 16)
            if bi == 0xFFFFFFFF:
                return None
            if ha == a and hb == b and bi < 0xFFFFFFFE:
                return struct.unpack_from("<IIII", self.block, bi * 16)
            i = (i + 1) % self.ht_n
        return None

    def read(self, name: str) -> bytes | None:
        ent = self._find(name)
        if ent is None:
            return None
        pos, csize, fsize, flags = ent
        if not flags & 0x80000000:
            return None
        raw = self.blob[self.base + pos: self.base + pos + csize]
        key = None
        if flags & 0x00010000:
            key = mpq_hash(name.replace("/", "\\").split("\\")[-1], 3)
            if flags & 0x00020000:
                key = ((key + pos) ^ fsize) & 0xFFFFFFFF
        if flags & 0x01000000:                           # 单块
            data = decrypt(raw, key) if key is not None else raw
            return self._decompress(data, fsize, flags) if csize < fsize else data[:fsize]
        if not flags & 0x0000FF00:                       # 不压缩
            if key is None:
                return raw[:fsize]
        n = (fsize + self.sector - 1) // self.sector
        table = raw[: (n + 1) * 4]
        if key is not None:
            table = decrypt(table, (key - 1) & 0xFFFFFFFF)
        offs = struct.unpack_from(f"<{n + 1}I", table)
        out = bytearray()
        for i in range(n):
            chunk = raw[offs[i]:offs[i + 1]]
            if key is not None:
                chunk = decrypt(chunk, (key + i) & 0xFFFFFFFF)
            want = min(self.sector, fsize - len(out))
            out += self._decompress(chunk, want, flags) if len(chunk) < want else chunk[:want]
        return bytes(out)

    @staticmethod
    def _decompress(chunk: bytes, want: int, flags: int) -> bytes:
        if flags & 0x00000100:                           # IMPLODE
            return explode(chunk, want)
        mask, body = chunk[0], chunk[1:]
        if mask & 0x10:
            import bz2
            body = bz2.decompress(body)
        if mask & 0x08:
            body = explode(body, want)
        if mask & 0x02:
            body = zlib.decompress(body)
        if mask & ~(0x10 | 0x08 | 0x02):
            raise ValueError(f"不支持的压缩 0x{mask:02X}")
        return body


# ── 字符串表 ──
def parse_wts(b: bytes | None) -> dict:
    """war3map.wts -> {编号: 文字}（地图里 TRIGSTR_123 这种引用查它）"""
    if not b:
        return {}
    text = b.decode("utf-8", "replace").lstrip("﻿")
    out = {}
    for m in re.finditer(r"STRING\s+(\d+)[^\{]*\{\r?\n(.*?)\r?\n\}", text, re.S):
        out[int(m.group(1))] = m.group(2)
    return out


def trigstr(s: str, wts: dict) -> str:
    """'TRIGSTR_123' -> wts 里的文字；不是引用原样返回。"""
    m = re.fullmatch(r"TRIGSTR_(\d+)", (s or "").strip())
    return wts.get(int(m.group(1)), s) if m else s


# 保护/优化过的地图（DotA、很多国产 RPG）不带 w3u/w3t，物体数据合并成了图里的 SLK + 这些 TXT（引擎按标准路径读）。
# 名字、称号、说明在 TXT 里：[四字码] 下面 Name= / Propernames= / Tip= / Ubertip=
PROFILE_FILES = ("Units\\CampaignUnitStrings.txt", "Units\\CampaignUnitFunc.txt", "Units\\CampaignAbilityStrings.txt",
                 "Units\\CampaignAbilityFunc.txt", "Units\\CampaignUpgradeStrings.txt", "Units\\ItemStrings.txt",
                 "Units\\ItemFunc.txt", "Units\\CommonAbilityStrings.txt", "Units\\HumanUnitStrings.txt",
                 "Units\\OrcUnitStrings.txt", "Units\\UndeadUnitStrings.txt", "Units\\NightElfUnitStrings.txt",
                 "Units\\NeutralUnitStrings.txt", "Units\\HumanAbilityStrings.txt", "Units\\OrcAbilityStrings.txt",
                 "Units\\UndeadAbilityStrings.txt", "Units\\NightElfAbilityStrings.txt", "Units\\NeutralAbilityStrings.txt",
                 "Units\\ItemAbilityStrings.txt")


def parse_profile(b: bytes | None, into: dict | None = None) -> dict:
    """INI 形状的 TXT：{四字码: {键: 值}}。给了 into 就合并进去（后读的覆盖先读的同名键）。"""
    out = into if into is not None else {}
    if not b:
        return out
    cur = None
    for raw in b.decode("utf-8", "replace").lstrip("﻿").splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("//"):
            continue
        if ln.startswith("[") and "]" in ln:
            cur = out.setdefault(ln[1:ln.index("]")], {})
        elif cur is not None and "=" in ln:
            k, v = ln.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                v = v[1:-1]
            cur[k.strip()] = v
    return out


def parse_w3i_name(b: bytes | None, wts: dict | None = None) -> str:
    """war3map.w3i 里的地图名（格式版本、保存次数、编辑器版本三个 int 之后就是名字，RoC 18 / TFT 25 一样）。
    完整的 w3i 解析在远见的 console/server/mapinfo.py parse_w3i。"""
    if not b or len(b) < 13:
        return ""
    e = b.find(b"\0", 12)
    return trigstr(b[12:e].decode("utf-8", "replace"), wts or {}) if e > 12 else ""


# ── 物体编辑器数据 ──
def _code(b: bytes, o: int) -> str:
    return b[o:o + 4].decode("latin-1")


def parse_objects(b: bytes | None, wts: dict | None = None, leveled: bool = False) -> dict:
    """war3map.w3u / w3t / w3b / w3h（leveled=False）、w3a / w3d / w3q（leveled=True，多一个等级和数据列）。
    -> {新四字码或原四字码: {"base": 原四字码, "custom": 是不是新建的, "fields": {字段码: 值}}}
    有等级的字段按 "字段码@等级" 存（等级 0 就只存字段码）。字符串值里的 TRIGSTR_ 已经查过 wts。
    格式：版本 int；原有物体表、自定义物体表各一张：个数 int，每个物体 = 原码 4 + 新码 4 + 改动数 int +
    改动 [字段码 4，类型 int(0 整数 1 实数 2 无限制实数 3 字符串)，(等级 int, 数据列 int)?，值，结束标记 4]。"""
    out: dict = {}
    if not b or len(b) < 8:
        return out
    wts = wts or {}
    o = 4                                                # 版本号
    for custom in (False, True):
        if o + 4 > len(b):
            break
        n = struct.unpack_from("<i", b, o)[0]
        o += 4
        for _ in range(n):
            orig, new = _code(b, o), _code(b, o + 4)
            o += 8
            mods = struct.unpack_from("<i", b, o)[0]
            o += 4
            key = new if custom and new.strip("\0") else orig
            ent = out.setdefault(key, {"base": orig, "custom": custom, "fields": {}})
            for _ in range(mods):
                fid = _code(b, o)
                vtype = struct.unpack_from("<i", b, o + 4)[0]
                o += 8
                level = 0
                if leveled:                              # 类型之后才是等级 + 数据列（09-24 先写反了，35 张图的 w3a 全解坏）
                    level = struct.unpack_from("<i", b, o)[0]
                    o += 8
                if vtype == 0:
                    val = struct.unpack_from("<i", b, o)[0]
                    o += 4
                elif vtype in (1, 2):
                    val = struct.unpack_from("<f", b, o)[0]
                    o += 4
                elif vtype == 3:
                    e = b.index(b"\0", o)
                    val = trigstr(b[o:e].decode("utf-8", "replace"), wts)
                    o = e + 1
                else:
                    raise ValueError(f"物体数据里不认识的值类型 {vtype}（字段 {fid}）")
                o += 4                                   # 结束标记（原码或 0）
                ent["fields"][f"{fid}@{level}" if level else fid] = val
    return out

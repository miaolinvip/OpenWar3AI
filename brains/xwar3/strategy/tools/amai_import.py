# -*- coding: utf-8 -*-
"""把 AMAI（冰封王座 TFT 版）的战略数据表和建造序列翻译成 Python 能直接用的 JSON。

用法：
    python tools/amai_import.py [--amai <AMAI 源码根目录>] [--out <输出目录>] [--report <报告路径>]

默认输出：
    brain/amai_data/common.json      全局表：名字->4 字码、强度、强度预测规则、等价单位、人格、全局设置、维护费……
    brain/amai_data/<race>.json      human / orc / undead / nightelf：战略表、建造序列、英雄、加点、设置……
    docs/amai_data_report.md         统计、不支持项、找不到 4 字码的名字、AMAI 原文怪癖、表结构发现

边界：只读 AMAI 源码、只写上面这些文件；不 import war3-brain 的任何运行时代码。
稳定：键排序、UTF-8、ensure_ascii=False、indent=1、不写时间戳和绝对路径 —— 同一份源码重复运行字节级一致。

读表规则照抄 AMAI 自己的预处理器 ejass.pl（sub tableincl / tablerep）：
  * `#INCLUDETABLE <x> #EFR` = 跳过第一行（表头）。不带 #EFR 的表（Strengths / Races / Tiers /
    HeroLevels / MercTypes / DragonTypes）没有表头，第一行就是数据。
  * `%row` 从 1 开始，只数满足 #COND 的行，以 '#' 开头的行不算。
  * 字段按 \\t 切（ejass 用 split /[\\t\\r\\n]/），`%N` 是第 N 列。
常量生成规则（common.eai 开头 `#INCLUDETABLE StandardUnits.txt`）：
  * 每行生成 `NAME = 'code'`、`oNAME = 'code'`（原始 4 字码）和 `uNAME = %row`（AMAI 内部序号）；
  * UnitEquivalence.txt 里 AI_COMP 行再生成别名：`%3 = o%1`、`o%3 = o%1`、`u%3 = u%1`
    （所以 BuildSequence 里裸写的 `TANK` 是 STEAM_TANK 的**原始 4 字码**，不是内部序号）；
  * HeroAbilities.txt 每行生成 `NAME = 'code'`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # brains/xwar3/strategy
DEFAULT_OUT = ROOT / "brain" / "amai_data"
DEFAULT_REPORT = ROOT / "docs" / "amai_data_report.md"
# AMAI 源码的默认位置：仓库根的 third_party/AMAI（setup.ps1 会 clone 到这里；它不进本仓库的 git）
REPO_AMAI = ROOT.parent.parent.parent / "third_party" / "AMAI"
VER = "TFT"

# (输出名, AMAI 目录名, AMAI 种族名 —— Races.txt / R_xxx 里用的那个)
RACES = (("human", "Human", "HUMAN"), ("orc", "Orc", "ORC"),
         ("undead", "Undead", "UNDEAD"), ("nightelf", "Elf", "ELF"))

# StandardUnits.txt 的 buy type -> 名字对照表里的类别
BUY_KIND = {
    "BUILDING": "building", "UNIT": "unit", "HERO": "hero", "NEUTRAL_HERO": "hero",
    "UPGRADE": "upgrade", "ML_UPGRADE": "upgrade", "RACIAL_ITEM": "item", "MERCHANT_ITEM": "item",
    "MERC": "merc", "GOBLIN_MERC": "merc", "DRAGON": "dragon",
}
# 需要原始 4 字码的原生函数（common.j / common.ai）；传 uNAME（内部序号）进去拿不到正确值
RAW_ID_FUNCS = {"GetUpgradeLevel", "GetUnitCountDone", "GetUnitCount", "GetPlayerUnitTypeCount",
                "GetUnitGoldCost", "GetUnitWoodCost", "GetFoodUsed", "IsUnitIdType", "GetUnitBuildTime"}
# 需要 AMAI 内部序号 uNAME 的 AMAI 函数（表达式里出现的那些；call 语句一律按内部序号检查）
INDEX_FUNCS = {"TownCount", "TownCountDone", "TownCountEx", "TownCountTown", "CanBuildUnit"}
# call 语句里哪个参数是"主单位"（拍平成顶层 code/name）
UNIT_PARAMS = ("unitid", "upgid", "u")
# call 语句的单位类别检查：函数 -> 允许的类别
BUILD_KINDS = {
    "BuildUpgr": {"upgrade"}, "BuildAdvUpgr": {"upgrade"}, "BuildAdvUpgr2": {"upgrade"},
    "BuildItem": {"item"},
    "BuildUnit": {"unit", "building", "hero", "merc", "dragon"},
    "BuildFront": {"building"}, "BuildAtMine": {"building"},
    "DefendTownsDone": {"building"}, "DefendTownsFrontDone": {"building"}, "DefendTownsCond": {"building"},
}
TIER_VAR = "tier"


# ============================================================================
# 源码读取
# ============================================================================
class Source:
    """AMAI 源码根目录：读文件时顺手记 sha1（写进 meta，证明 JSON 来自哪份源码）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.hashes: dict[str, str] = {}
        self.warnings: list[str] = []

    def exists(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def text(self, rel: str) -> str:
        data = (self.root / rel).read_bytes()
        self.hashes[rel] = hashlib.sha1(data).hexdigest()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            self.warnings.append(f"{rel} 不是 UTF-8，按 cp1252 解码")
            return data.decode("cp1252")

    def commit(self) -> str | None:
        """读 .git/HEAD 拿提交号（不起子进程，结果稳定）。不是 git 仓库就返回 None。"""
        git = self.root / ".git"
        try:
            head = (git / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not head.startswith("ref:"):
            return head
        ref = head[4:].strip()
        p = git / ref
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        return None


def find_amai_dir(explicit: str | None = None) -> Path | None:
    """找 AMAI 源码：--amai > 环境变量 AMAI_DIR > strategy/third_party/AMAI > 仓库根 third_party/AMAI。"""
    if explicit:
        p = Path(explicit)
        return p if (p / VER / "StandardUnits.txt").is_file() else None
    cands = []
    if os.environ.get("AMAI_DIR"):
        cands.append(Path(os.environ["AMAI_DIR"]))
    cands += [ROOT / "third_party" / "AMAI", REPO_AMAI]
    for c in cands:
        if (c / VER / "StandardUnits.txt").is_file():
            return c
    return None


INT_RE = re.compile(r"^[+-]?\d+$")
REAL_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)$")


def scalar(s):
    """表格字段 -> 数字 / 布尔 / 原字符串（"数值转成数字、true/false 转布尔"）。"""
    if s is None:
        return None
    if INT_RE.match(s):
        return int(s)
    if REAL_RE.match(s):
        return float(s)
    if s == "true":
        return True
    if s == "false":
        return False
    return s


def read_table(src: Source, rel: str, efr: bool):
    """按 ejass.pl 的规则读表。返回 (表头 或 None, 行列表)；每行 {"row": %row, "line": 文件行号, "fields": [...]}。

    字段两端的空格去掉（Heroes.txt 里有 "DARK_RANGER " 这种；JASS 里 u%1 后面多个空格不影响，JSON 里去掉）。
    """
    lines = src.text(rel).split("\n")
    if lines and lines[-1] == "":
        lines.pop()                     # 文件末尾的换行不产生空行（和 Perl 的 <TABLE> 一致）
    header = None
    start = 0
    if efr:
        header = [h.strip() for h in lines[0].rstrip("\r").split("\t")] if lines else []
        dup = [h for h, n in Counter(header).items() if n > 1]
        if dup:
            src.warnings.append(f"{rel} 表头有重复列名 {dup}")
        start = 1
    rows = []
    k = 0
    for i in range(start, len(lines)):
        raw = lines[i].rstrip("\r")
        if raw.startswith("#"):
            continue
        k += 1                          # ejass 的 %row：空行也算一行（本版本 TFT 表里没有空行，出现就报警）
        if raw.strip() == "":
            src.warnings.append(f"{rel} 第 {i + 1} 行是空行（ejass 会把它当成第 {k} 行）")
            continue
        rows.append({"row": k, "line": i + 1, "fields": [f.strip() for f in raw.split("\t")]})
    return header, rows


def row_object(header, fields):
    """按表头把一行变成 {列名: 值}；多出来的列放 _extra，缺的列给 None。"""
    obj = {}
    for j, h in enumerate(header):
        obj[h] = scalar(fields[j]) if j < len(fields) else None
    extra = [f for f in fields[len(header):] if f != ""]
    if extra:
        obj["_extra"] = extra
    return obj


# ============================================================================
# 名字 -> 4 字码
# ============================================================================
class Names:
    """StandardUnits / UnitEquivalence(AI_COMP) / HeroAbilities / NeutralShops 汇成的名字表，顺手记录找不到的名字。"""

    def __init__(self):
        self.units: dict[str, dict] = {}      # NAME -> {"code", "u_index", "race", "buy_type", "kind"}
        self.aliases: dict[str, str] = {}     # AI_COMP 别名 -> NAME
        self.abilities: dict[str, str] = {}   # 技能名 -> 4 字码
        self.shops: dict[str, str] = {}       # 中立商店名 -> 4 字码
        self.missing: dict[str, Counter] = {}  # 名字 -> Counter(上下文)

    def lookup_unit(self, name):
        base = name if name in self.units else self.aliases.get(name)
        return (base, self.units[base]) if base is not None else None

    def miss(self, name, ctx):
        self.missing.setdefault(name, Counter())[ctx] += 1

    def code(self, value, ctx, kinds=("unit", "ability", "shop")):
        """表格里的名字字段 -> 4 字码。'0' / 0 / 空表示"无"，返回 None 且不算缺失。"""
        if value in (None, "", 0, "0"):
            return None
        name = str(value)
        if "unit" in kinds:
            hit = self.lookup_unit(name)
            if hit:
                return hit[1]["code"]
        if "ability" in kinds and name in self.abilities:
            return self.abilities[name]
        if "shop" in kinds and name in self.shops:
            return self.shops[name]
        self.miss(name, ctx)
        return None

    def kind_of(self, name):
        hit = self.lookup_unit(name)
        return hit[1]["kind"] if hit else None


# ============================================================================
# JASS 表达式：词法 + 语法（只做表达式，语句在 parse_functions 里按行处理）
# ============================================================================
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<real>\d+\.\d*|\.\d+)
  | (?P<hex>0[xX][0-9A-Fa-f]+|\$[0-9A-Fa-f]+)
  | (?P<int>\d+)
  | (?P<rawcode>'[^']*')
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<op>==|!=|<=|>=|[-+*/<>(),\[\]])
  | (?P<id>[A-Za-z_]\w*)
""", re.X)


class ParseError(Exception):
    pass


def tokenize(text: str):
    toks, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ParseError(f"无法识别的字符 {text[pos]!r}（第 {pos + 1} 列）")
        pos = m.end()
        if m.lastgroup != "ws":
            toks.append((m.lastgroup, m.group()))
    return toks


class Parser:
    """递归下降。优先级（低 -> 高）：or < and < not < 比较 < 加减 < 乘除 < 负号 < 下标/调用。

    JASS 里 and/or 的相对优先级、not 和比较运算的相对优先级在不同解析器里说法不一，
    所以遇到"and/or 混用没加括号"或"not 后面跟没加括号的比较"直接报不支持，不猜。
    （本版本 TFT 的四个 BuildSequence.ai 里两种写法都没有出现。）
    """
    CMP = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}

    def __init__(self, text: str):
        self.text = text
        self.toks = tokenize(text)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, val=None):
        k, v = self.peek()
        if k is None:
            raise ParseError("表达式意外结束")
        if val is not None and v != val:
            raise ParseError(f"期望 {val!r}，得到 {v!r}")
        self.i += 1
        return k, v

    def parse_all(self):
        node = self.expr()
        if self.i != len(self.toks):
            raise ParseError(f"多余的记号 {self.peek()[1]!r}")
        return node

    def expr(self):
        return self.or_expr()

    def or_expr(self):
        items = [self.and_expr()]
        while self.peek() == ("id", "or"):
            self.take()
            items.append(self.and_expr())
        if len(items) == 1:
            return items[0]
        if any(it["t"] == "and" and not it.get("paren") for it in items):
            raise ParseError("and/or 混用且没有括号（JASS 里两者优先级有歧义，不做猜测）")
        return {"t": "or", "items": items}

    def and_expr(self):
        items = [self.not_expr()]
        while self.peek() == ("id", "and"):
            self.take()
            items.append(self.not_expr())
        return items[0] if len(items) == 1 else {"t": "and", "items": items}

    def not_expr(self):
        if self.peek() == ("id", "not"):
            self.take()
            arg = self.not_expr()
            if arg["t"] == "cmp" and not arg.get("paren"):
                raise ParseError("not 后面紧跟没加括号的比较（JASS 里 not 与比较的优先级有歧义，不做猜测）")
            return {"t": "not", "arg": arg}
        return self.cmp_expr()

    def cmp_expr(self):
        left = self.add_expr()
        k, v = self.peek()
        if k == "op" and v in self.CMP:
            self.take()
            right = self.add_expr()
            k2, v2 = self.peek()
            if k2 == "op" and v2 in self.CMP:
                raise ParseError("连续比较（a < b < c）")
            return {"t": "cmp", "op": self.CMP[v], "l": left, "r": right}
        return left

    def add_expr(self):
        left = self.mul_expr()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            left = {"t": "bin", "op": "add" if op == "+" else "sub", "l": left, "r": self.mul_expr()}
        return left

    def mul_expr(self):
        left = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            left = {"t": "bin", "op": "mul" if op == "*" else "div", "l": left, "r": self.unary()}
        return left

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return {"t": "neg", "arg": self.unary()}
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ("op", "["):
            self.take()
            idx = self.expr()
            self.take("]")
            node = {"t": "index", "base": node, "index": idx}
        return node

    def primary(self):
        k, v = self.take()
        if k == "int":
            return {"t": "num", "v": int(v)}
        if k == "real":
            return {"t": "num", "v": float(v)}
        if k == "hex":
            return {"t": "num", "v": int(v[1:], 16) if v.startswith("$") else int(v, 16)}
        if k == "rawcode":
            return {"t": "rawcode", "v": v[1:-1]}
        if k == "str":
            return {"t": "str", "v": v[1:-1]}
        if (k, v) == ("op", "("):
            node = dict(self.expr())
            self.take(")")
            node["paren"] = True
            return node
        if k == "id":
            if v in ("true", "false"):
                return {"t": "bool", "v": v == "true"}
            if v == "null":
                return {"t": "null"}
            if v in ("and", "or", "not", "then", "if", "call", "set"):
                raise ParseError(f"关键字 {v!r} 出现在不该出现的位置")
            if self.peek() == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.take(")")
                return {"t": "call", "name": v, "args": args}
            return {"t": "id", "name": v}
        raise ParseError(f"意外的记号 {v!r}")


# ============================================================================
# 语法树 -> JSON 表达式结构
# ============================================================================
class Ctx:
    """一次转换收集到的东西：不支持项、找不到的名字、AMAI 原文怪癖、词表（状态量）。"""

    def __init__(self, where: str, vocab: dict):
        self.where = where
        self.issues: list[dict] = []
        self.quirks: list[str] = []
        self.vocab = vocab


def new_vocab():
    return {"fn": Counter(), "var": Counter(), "const": Counter()}


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_unit_ref(x):
    return isinstance(x, dict) and "unit" in x


class Converter:
    """把 Parser 的语法树变成 JSON 结构。

    输出形状：
      数字 / 布尔直接给值；{"str": ...}；{"rawcode": "hmil"}；{"const": "null"}
      {"var": "tier"}、{"var": "c_enemy", "index": {...}}
      {"const": "RACE_UNDEAD", "jass": "ConvertRace(3)"}、{"const": "HERO_SKILL_MINOR", "value": 1}
      {"unit": "GHOUL", "code": "ugho"}（+ "alias" 用了别名、"id_kind": "raw" 原始 4 字码、"unresolved": true 找不到）
      {"ability": "BLIZZARD", "code": "AHbz"}
      {"hero": 1}（hero[1]）、{"special": "merc_range", "kind": "merc", "merc_type": "range"}、
      {"special": "dragons[3]", "kind": "dragon", "index": 3, "dragon_type": "Large Dragon"}
      {"fn": "GetGold"}、单个单位参数拍平 {"fn": "TownCount", "code": "ugho", "name": "GHOUL"}、否则 {"fn", "args": [...]}
      {"op": "eq|ne|lt|le|gt|ge|add|sub|mul|div", "lhs", "rhs"}、{"op": "and|or", "args": [...]}、{"op": "not|neg", "arg"}
      翻译不了：{"raw": "原文", "unsupported": true, "reason": "..."}
    """

    def __init__(self, names: Names, constants: dict, merc_types: list, dragon_types: list):
        self.names = names
        self.constants = constants
        self.merc_types = set(merc_types)
        self.dragon_types = {i + 1: t for i, t in enumerate(dragon_types)}

    # ---- 入口 ----
    def expr(self, text: str, ctx: Ctx):
        try:
            ast = Parser(text).parse_all()
        except ParseError as e:
            ctx.issues.append({"kind": "unsupported", "where": ctx.where, "raw": text, "reason": str(e)})
            return {"raw": text, "unsupported": True, "reason": str(e)}
        return self.conv(ast, ctx)

    # ---- 递归 ----
    def conv(self, a, ctx: Ctx):
        t = a["t"]
        if t in ("num", "bool"):
            return a["v"]
        if t == "null":
            return {"const": "null"}
        if t == "str":
            return {"str": a["v"]}
        if t == "rawcode":
            return {"rawcode": a["v"]}
        if t == "neg":
            x = self.conv(a["arg"], ctx)
            return -x if is_num(x) else {"op": "neg", "arg": x}
        if t == "not":
            return {"op": "not", "arg": self.conv(a["arg"], ctx)}
        if t in ("and", "or"):
            args = []
            for it in a["items"]:
                c = self.conv(it, ctx)
                if isinstance(c, dict) and c.get("op") == t:
                    args.extend(c["args"])
                else:
                    args.append(c)
            return {"op": t, "args": args}
        if t in ("cmp", "bin"):
            return {"op": a["op"], "lhs": self.conv(a["l"], ctx), "rhs": self.conv(a["r"], ctx)}
        if t == "id":
            return self.ident(a["name"], ctx)
        if t == "index":
            return self.index(a, ctx)
        if t == "call":
            return self.fncall(a["name"], [self.conv(x, ctx) for x in a["args"]], ctx)
        ctx.issues.append({"kind": "unsupported", "where": ctx.where, "raw": str(a), "reason": f"未知语法节点 {t}"})
        return {"raw": str(a), "unsupported": True, "reason": f"未知语法节点 {t}"}

    def unit_ref(self, name, id_kind):
        hit = self.names.lookup_unit(name)
        if not hit:
            return None
        base, info = hit
        node = {"unit": base, "code": info["code"]}
        if base != name:
            node["alias"] = name
        if id_kind == "raw":
            node["id_kind"] = "raw"
        return node

    def ident(self, name, ctx: Ctx):
        if name in ("u0", "o0"):
            return 0                    # common.eai: constant integer u0 = 0 / o0 = 0
        m = re.fullmatch(r"([uo])([A-Z][A-Z0-9_]*)", name)
        if m:
            kind = "index" if m.group(1) == "u" else "raw"
            ref = self.unit_ref(m.group(2), kind)
            if ref is not None:
                return ref
            self.names.miss(m.group(2), f"{ctx.where}: {name}")
            ctx.issues.append({"kind": "unresolved", "where": ctx.where, "name": m.group(2)})
            node = {"unit": m.group(2), "code": None, "unresolved": True}
            if kind == "raw":
                node["id_kind"] = "raw"
            return node
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            ref = self.unit_ref(name, "raw")      # 裸名字 = 原始 4 字码（NAME = 'code'，AI_COMP 别名同理）
            if ref is not None:
                return ref
            if name in self.names.abilities:
                return {"ability": name, "code": self.names.abilities[name]}
            ctx.vocab["const"][name] += 1
            node = {"const": name}
            val = self.constants.get(name)
            if isinstance(val, int):
                node["value"] = val
            elif isinstance(val, str):
                node["jass"] = val
            return node
        m = re.fullmatch(r"merc_(\w+)", name)
        if m and m.group(1) in self.merc_types:
            return {"special": name, "kind": "merc", "merc_type": m.group(1)}
        ctx.vocab["var"][name] += 1
        return {"var": name}

    def index(self, a, ctx: Ctx):
        base, idx = a["base"], a["index"]
        if base["t"] != "id":
            ctx.issues.append({"kind": "unsupported", "where": ctx.where, "raw": str(a), "reason": "下标的主体不是变量"})
            return {"raw": str(a), "unsupported": True, "reason": "下标的主体不是变量"}
        bn = base["name"]
        if bn == "old_id":
            inner = self.conv(idx, ctx)
            if is_unit_ref(inner):
                node = dict(inner)
                if node.get("id_kind") == "raw":
                    ctx.quirks.append(f"old_id[] 套在原始 4 字码 {node.get('alias', node['unit'])} 上")
                node["id_kind"] = "raw"
                return node
            ctx.vocab["var"]["old_id"] += 1
            return {"var": "old_id", "index": inner}
        if bn == "hero" and idx["t"] == "num":
            return {"hero": idx["v"]}
        if bn == "dragons" and idx["t"] == "num":
            n = idx["v"]
            return {"special": f"dragons[{n}]", "kind": "dragon", "index": n, "dragon_type": self.dragon_types.get(n)}
        ctx.vocab["var"][bn] += 1
        return {"var": bn, "index": self.conv(idx, ctx)}

    def fncall(self, name, args, ctx: Ctx):
        ctx.vocab["fn"][name] += 1
        node = {"fn": name}
        if len(args) == 1 and is_unit_ref(args[0]):
            ref = args[0]
            node["code"] = ref["code"]
            node["name"] = ref["unit"]
            for k in ("alias", "id_kind", "unresolved"):
                if k in ref:
                    node[k] = ref[k]
        elif args:
            node["args"] = args
        for ref in (x for x in args if is_unit_ref(x)):
            self.check_id_kind(name, ref, ctx)
        return node

    def check_id_kind(self, fn, ref, ctx: Ctx):
        shown = ref.get("alias", ref["unit"])
        if fn in RAW_ID_FUNCS and ref.get("id_kind") != "raw":
            ctx.quirks.append(f"原生函数 {fn} 需要原始 4 字码，这里传的是 AMAI 内部序号 u{shown}（引擎拿到的是一个不存在的 id）")
        if (fn in INDEX_FUNCS or fn.startswith("@")) and ref.get("id_kind") == "raw":
            ctx.quirks.append(f"AMAI 函数 {fn.lstrip('@')} 需要内部序号 u{shown}，这里传的是原始 4 字码 {shown}"
                              "（AMAI 内部经 old_id[序号] 转原始 id，传原始 4 字码会数组越界、读到 0，按源码推断这一处实际不生效）")


# ============================================================================
# 部分求值：把 tier 代成 1/2/3，化简条件、折叠常量
# ============================================================================
def _fold(op, l, r):
    if op == "eq":
        return l == r
    if op == "ne":
        return l != r
    if op == "lt":
        return l < r
    if op == "le":
        return l <= r
    if op == "gt":
        return l > r
    if op == "ge":
        return l >= r
    if op == "add":
        return l + r
    if op == "sub":
        return l - r
    if op == "mul":
        return l * r
    if op == "div":
        if r == 0:
            return None
        if isinstance(l, int) and isinstance(r, int):
            return int(l / r)           # JASS 整数除法向零截断
        return l / r
    return None


def subst(node, var, val):
    """把 {"var": var} 换成 val 并化简。条件变成 True/False 时由调用方决定去留。"""
    if isinstance(node, list):
        return [subst(n, var, val) for n in node]
    if not isinstance(node, dict):
        return node
    if node.get("unsupported"):
        return node
    if node.get("var") == var and "index" not in node:
        return val
    op = node.get("op")
    if op in ("and", "or"):
        args = []
        for a in node["args"]:
            s = subst(a, var, val)
            if isinstance(s, bool):
                if op == "and" and s is False:
                    return False
                if op == "or" and s is True:
                    return True
                continue                # and 里的 True / or 里的 False 可以去掉
            args.append(s)
        if not args:
            return op == "and"
        return args[0] if len(args) == 1 else {"op": op, "args": args}
    if op == "not":
        a = subst(node["arg"], var, val)
        return (not a) if isinstance(a, bool) else {"op": "not", "arg": a}
    if op == "neg":
        a = subst(node["arg"], var, val)
        return -a if is_num(a) else {"op": "neg", "arg": a}
    if op is not None and "lhs" in node:
        l, r = subst(node["lhs"], var, val), subst(node["rhs"], var, val)
        if is_num(l) and is_num(r):
            f = _fold(op, l, r)
            if f is not None:
                return f
        return {"op": op, "lhs": l, "rhs": r}
    return {k: subst(v, var, val) for k, v in node.items()}


def combine(terms):
    """一串条件项 -> None / 单项 / {"op": "and", "args": [...]}（嵌套的 and 拍平）。"""
    args = []
    for t in terms:
        if isinstance(t, dict) and t.get("op") == "and":
            args.extend(t["args"])
        else:
            args.append(t)
    if not args:
        return None
    return args[0] if len(args) == 1 else {"op": "and", "args": args}


def has_unsupported(node) -> bool:
    if isinstance(node, list):
        return any(has_unsupported(n) for n in node)
    if isinstance(node, dict):
        if node.get("unsupported") or node.get("unresolved"):
            return True
        return any(has_unsupported(v) for v in node.values())
    return False


# ============================================================================
# BuildSequence.ai：函数 / if / call / set
# ============================================================================
def strip_comment(line: str) -> str:
    """去掉 // 注释（字符串和 rawcode 里的 // 不算）。"""
    q = None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif line.startswith("//", i):
            return line[:i]
        i += 1
    return line


FUNC_RE = re.compile(r"^function\s+(\w+)\s+takes\s+(.+?)\s+returns\s+(\w+)$")
IF_RE = re.compile(r"^if\s+(.*?)\s*then$")
ELSEIF_RE = re.compile(r"^elseif\s+(.*?)\s*then$")
CALL_RE = re.compile(r"^call\s+(.*)$")
SET_RE = re.compile(r"^set\s+(\w+)\s*(\[(.*)\])?\s*=\s*(.*)$")


SIG_RE = re.compile(r"^function\s+([\w%]+)\s+takes\s+(.+?)\s+returns\s+(\w+)\s*(//.*)?$")


def extract_signatures(src: Source, strengths: list[str]) -> dict:
    """从 common.eai / races.eai 里抽 `function X takes ... returns ...` 的参数表（同名取先出现的）。
    `AddUnitToAnti%1` 这种模板函数（外面包着 #INCLUDETABLE Strengths.txt）按 Strengths.txt 展开成 AddUnitToAntiair ……"""
    sigs = {}
    for rel in ("common.eai", "races.eai"):
        for line in src.text(rel).split("\n"):
            m = SIG_RE.match(line.strip())
            if not m:
                continue
            name, takes = m.group(1), m.group(2).strip()
            params = []
            if takes != "nothing":
                for p in takes.split(","):
                    parts = p.split()
                    params.append({"type": parts[0], "name": parts[-1]})
            names = [name.replace("%1", s) for s in strengths] if "%1" in name else [name]
            for n in names:
                sigs.setdefault(n, params)
    return sigs


class BuildSeqParser:
    """一个种族的 BuildSequence.ai -> {函数名: [语句...]}；语句带 path（外层 if 条件链）。"""

    def __init__(self, conv: Converter, sigs: dict, rel: str, vocab: dict):
        self.conv = conv
        self.sigs = sigs
        self.rel = rel
        self.vocab = vocab
        self.functions: dict[str, dict] = {}
        self.conditions: list[dict] = []    # 每个 if/elseif 头一条
        self.problems: list[dict] = []      # 不认识的语句行等

    def parse(self, text: str):
        func = None
        stack = []
        for lineno, line in enumerate(text.split("\n"), 1):
            s = strip_comment(line.rstrip("\r")).strip()
            if not s:
                continue
            m = FUNC_RE.match(s)
            if m:
                if func is not None:
                    self.problems.append({"line": lineno, "raw": s, "reason": "上一个函数没有 endfunction"})
                func = {"name": m.group(1), "line": lineno, "stmts": []}
                self.functions[m.group(1)] = func
                stack = []
                continue
            if func is None:
                self.problems.append({"line": lineno, "raw": s, "reason": "函数体之外的语句"})
                continue
            if s == "endfunction":
                if stack:
                    self.problems.append({"line": lineno, "raw": s, "reason": "if 没有闭合"})
                func = None
                continue
            m = IF_RE.match(s)
            if m:
                stack.append({"branches": [self.condition(m.group(1), lineno, func["name"])], "else": False})
                continue
            m = ELSEIF_RE.match(s)
            if m:
                if not stack or stack[-1]["else"]:
                    self.problems.append({"line": lineno, "raw": s, "reason": "elseif 位置不对"})
                    continue
                stack[-1]["branches"].append(self.condition(m.group(1), lineno, func["name"]))
                continue
            if s == "else":
                if not stack or stack[-1]["else"]:
                    self.problems.append({"line": lineno, "raw": s, "reason": "else 位置不对"})
                    continue
                stack[-1]["else"] = True
                continue
            if s == "endif":
                if not stack:
                    self.problems.append({"line": lineno, "raw": s, "reason": "多余的 endif"})
                    continue
                stack.pop()
                continue
            terms, raws = self.path(stack)
            m = CALL_RE.match(s)
            if m:
                func["stmts"].append(self.call_stmt(m.group(1), s, lineno, func["name"], terms, raws))
                continue
            m = SET_RE.match(s)
            if m:
                func["stmts"].append(self.set_stmt(m, s, lineno, func["name"], terms, raws))
                continue
            self.problems.append({"line": lineno, "raw": s, "reason": "不认识的语句"})
        return self

    def condition(self, text, lineno, fname):
        ctx = Ctx(f"{self.rel}:{lineno}", self.vocab)
        node = self.conv.expr(text, ctx)
        ok = not ctx.issues
        self.conditions.append({"line": lineno, "function": fname, "raw": text, "ok": ok,
                                "issues": ctx.issues, "quirks": ctx.quirks})
        return node, text

    @staticmethod
    def path(stack):
        """外层 if 链 -> 条件项列表 + 原文列表。elseif/else 分支带上前面各分支的 not。"""
        terms, raws = [], []
        for fr in stack:
            prev = fr["branches"] if fr["else"] else fr["branches"][:-1]
            for node, raw in prev:
                terms.append({"op": "not", "arg": node})
                raws.append(f"not ({raw})")
            if not fr["else"]:
                node, raw = fr["branches"][-1]
                terms.append(node)
                raws.append(raw)
        return terms, raws

    def call_stmt(self, body, raw, lineno, fname, terms, raws):
        ctx = Ctx(f"{self.rel}:{lineno}", self.vocab)
        out = {"fn": None, "line": lineno, "raw": raw, "cond": combine(terms), "cond_raw": raws}
        try:
            ast = Parser(body).parse_all()
            if ast["t"] != "call" or ast.get("paren"):
                raise ParseError("call 后面不是函数调用")
        except ParseError as e:
            ctx.issues.append({"kind": "unsupported", "where": ctx.where, "raw": raw, "reason": str(e)})
            out.update({"fn": "?", "args": {"raw": body, "unsupported": True, "reason": str(e)}})
            return self.finish(out, ctx)
        name = ast["name"]
        out["fn"] = name
        args = [self.conv.conv(x, ctx) for x in ast["args"]]
        params = self.sigs.get(name)
        if params is None or len(params) != len(args):
            reason = "common.eai / races.eai 里找不到这个函数的签名" if params is None else \
                f"参数个数对不上（签名 {len(params)} 个，调用 {len(args)} 个）"
            ctx.issues.append({"kind": "unsupported", "where": ctx.where, "raw": raw, "reason": reason})
            out["args"] = args
            out["signature_problem"] = reason
            return self.finish(out, ctx)
        named = {p["name"]: a for p, a in zip(params, args)}
        out["args"] = named
        # 拍平常用字段
        if "qty" in named:
            out["qty"] = named["qty"]
        if "prio" in named:
            out["prio"] = named["prio"]
        primary = next((p for p in UNIT_PARAMS if p in named), None)
        if primary is not None:
            self.flatten_unit(out, named[primary])
        # AMAI 的 call 语句里单位参数一律是内部序号；类别检查
        for p, a in named.items():
            if is_unit_ref(a):
                self.conv.check_id_kind("@" + name, a, ctx)
        if primary is not None:
            self.check_kind(name, named[primary], ctx)
        return self.finish(out, ctx)

    def flatten_unit(self, out, node):
        if is_unit_ref(node):
            out["code"] = node["code"]
            out["name"] = node["unit"]
            for k in ("alias", "id_kind", "unresolved"):
                if k in node:
                    out[k] = node[k]
        elif isinstance(node, dict) and "hero" in node:
            out["hero"] = node["hero"]
        elif isinstance(node, dict) and "special" in node:
            for k, v in node.items():
                out[k] = v
        else:
            out["unit_expr"] = node

    def check_kind(self, fn, node, ctx):
        allowed = BUILD_KINDS.get(fn)
        if not allowed:
            return
        if is_unit_ref(node) and not node.get("unresolved"):
            kind = self.conv.names.kind_of(node["unit"])
            if kind not in allowed:
                ctx.quirks.append(f"{fn} 的对象 {node['unit']} 是 {kind}（期望 {'/'.join(sorted(allowed))}）")
        elif isinstance(node, dict) and "special" in node and node.get("kind") not in allowed:
            ctx.quirks.append(f"{fn} 的对象是 {node['special']}（{node.get('kind')}，期望 {'/'.join(sorted(allowed))}）")

    def set_stmt(self, m, raw, lineno, fname, terms, raws):
        ctx = Ctx(f"{self.rel}:{lineno}", self.vocab)
        out = {"fn": "set", "line": lineno, "raw": raw, "cond": combine(terms), "cond_raw": raws,
               "var": m.group(1), "value": self.conv.expr(m.group(4), ctx)}
        if m.group(2):
            out["index"] = self.conv.expr(m.group(3), ctx)
        return self.finish(out, ctx)

    @staticmethod
    def finish(out, ctx):
        if ctx.issues:
            out["issues"] = ctx.issues
        if ctx.quirks:
            out["quirks"] = ctx.quirks
        return out


def stmt_for_tier(stmt, t):
    """把一条语句按 tier = t 做部分求值；条件恒假返回 None。"""
    cond = subst(stmt["cond"], TIER_VAR, t) if stmt["cond"] is not None else None
    if cond is False:
        return None
    out = dict(stmt)
    out["cond"] = None if cond is True else cond
    for k in ("args", "qty", "prio", "value", "index", "unit_expr"):
        if k in out:
            out[k] = subst(out[k], TIER_VAR, t)
    return out


# ============================================================================
# 各张表
# ============================================================================
def settings_table(src, rel, conv: Converter, vocab):
    """GlobalSettings.txt / <Race>/Settings.txt：`set %1 = %2`，后写的覆盖先写的。"""
    header, rows = read_table(src, rel, efr=True)
    out_rows, values, seen, dups = [], {}, set(), []
    for r in rows:
        obj = row_object(header, r["fields"])
        key = str(obj[header[0]]).strip()
        raw = r["fields"][1] if len(r["fields"]) > 1 else ""
        ctx = Ctx(f"{rel}:{r['line']}", vocab)
        node = conv.expr(raw, ctx)
        obj[header[0]] = key
        obj.update({"row": r["row"], "parsed": node})
        if ctx.issues:
            obj["issues"] = ctx.issues
        out_rows.append(obj)
        if key in seen:
            dups.append(key)
        seen.add(key)
        values[key] = simple_value(node)
    return {"header": header, "rows": out_rows, "values": values, "duplicate_keys": sorted(set(dups))}


def simple_value(node):
    if isinstance(node, dict):
        if set(node) == {"str"}:
            return node["str"]
        if set(node) == {"rawcode"}:
            return node["rawcode"]
    return node


def generic_table(src, rel, names: Names, name_cols=(), expr_cols=(), conv=None, vocab=None, efr=True):
    """通用表：列名保留原表头，值转数字/布尔；名字列在 codes 里给 4 字码；表达式列在 parsed 里给结构。"""
    header, rows = read_table(src, rel, efr=efr)
    out = []
    for r in rows:
        obj = row_object(header, r["fields"])
        obj["row"] = r["row"]
        codes = {}
        for col in name_cols:
            if col in obj:
                codes[col] = names.code(obj[col], f"{rel}:{r['line']}:{col}")
        if codes:
            obj["codes"] = codes
        parsed, issues = {}, []
        for col in expr_cols:
            if col in obj and obj[col] is not None:
                ctx = Ctx(f"{rel}:{r['line']}:{col}", vocab if vocab is not None else new_vocab())
                parsed[col] = conv.expr(str(r["fields"][header.index(col)]), ctx)
                issues += ctx.issues
        if parsed:
            obj["parsed"] = parsed
        if issues:
            obj["issues"] = issues
        out.append(obj)
    return header, out


def plain_list(src, rel):
    """没有表头、一行一个值的表（Tiers / HeroLevels / MercTypes / DragonTypes）。"""
    _, rows = read_table(src, rel, efr=False)
    return [scalar(r["fields"][0]) for r in rows]


def collect_constants(src: Source, races_rows):
    """表达式里常量的取值：*.eai 里的 `constant integer X = 数字`、Races.txt 生成的 R_xxx、common.j 的 ConvertXxx(...)。"""
    consts = {}
    pat = re.compile(r"^\s*constant\s+integer\s+(\w+)\s*=\s*(-?\d+)\s*(//.*)?$")
    rels = ["common.eai", "races.eai"] + sorted(
        p.relative_to(src.root).as_posix() for p in (src.root / "Jobs").glob("*.eai"))
    for rel in rels:
        for line in src.text(rel).split("\n"):
            m = pat.match(line.rstrip("\r"))
            if m:
                consts.setdefault(m.group(1), int(m.group(2)))
    consts["R_RANDOM"] = 0
    for i, race in enumerate(races_rows, 1):
        consts[f"R_{race}"] = i
    consts["R_NEUTRAL"] = len(races_rows) + 1
    jpat = re.compile(r"^\s*constant\s+\w+\s+(\w+)\s*=\s*(.+?)\s*(//.*)?$")
    for line in src.text(f"{VER}/common.j").split("\n"):
        m = jpat.match(line.rstrip("\r"))
        if m and m.group(1) not in consts:
            v = m.group(2)
            consts[m.group(1)] = int(v) if INT_RE.match(v) else v
    return consts


def search_tree_ids(items):
    """ejass.pl 的 #SEARCHTREE / #SEARCHLIST：按权值（这里是 base rp）两两合并最小的，叶子按树的先序编号 0..n-1。
    Strategy.txt 的 STRAT_<名字> 常量就是这么来的，不是文件行序。（Perl sort 按稳定排序处理。）"""
    nodes = [{"value": v, "name": n} for n, v in items]
    while len(nodes) > 1:
        nodes = sorted(nodes, key=lambda x: x["value"])
        x, y, rest = nodes[0], nodes[1], nodes[2:]
        nodes = rest + [{"value": x["value"] + y["value"], "children": [x, y]}]
    order = []

    def walk(nd):
        if "children" in nd:
            walk(nd["children"][0])
            walk(nd["children"][1])
        else:
            order.append(nd["name"])
    if nodes:
        walk(nodes[0])
    return {n: i for i, n in enumerate(order)}


SKILL_LINES = ("major", "minor", "rare")   # common.eai: HERO_SKILL_MAJOR = 0 / MINOR = 1 / RARE = 2


def hero_skill_rows(src, rel, names: Names, hero_order, base_block, level_num, warnings):
    """HeroSkills.txt / NeutralHeroSkills.txt：表里没有英雄列，行号决定属于谁。

    races.eai InitAllSkills：  allskills[(NEUTRALHEROLNUM - 1 + %row) * HERO_LEVEL_NUMBER + X] = 第 X 级技能
    common.eai InitNeutralSkills：allskills[%row * HERO_LEVEL_NUMBER + X - 10]（即块号 %row - 1）
    common.eai LoadSkills(sa, hn, ta)：读块号 (sa-1)*3 + sk + (hn-1)*9
    races.eai set_skills：遍历 Heroes.txt，hero[1|2|3] == 第 hn 行的英雄时 LoadSkills(1|2|3, hn, ...)
    => 块号 b：hn = b // 9 + 1（Heroes.txt 第 hn 行）、sa = b % 9 // 3 + 1（这个英雄是第几个出的）、sk = b % 3（主/次/稀有）
    """
    header, rows = read_table(src, rel, efr=True)
    out = []
    for r in rows:
        block = base_block + r["row"] - 1
        hn = block // 9 + 1
        hero = hero_order[hn - 1] if hn - 1 < len(hero_order) else None
        if hero is None:
            warnings.append(f"{rel} 第 {r['row']} 行算出 hn={hn}，Heroes.txt 没有这一行")
        spells = [r["fields"][i] if i < len(r["fields"]) else None for i in range(level_num)]
        codes = [names.code(s, f"{rel}:{r['line']}:Level {i + 1} Spell", kinds=("ability",))
                 for i, s in enumerate(spells)]
        out.append({
            "row": r["row"], "block": block, "hero_row": hn, "hero": hero,
            "hero_code": names.code(hero, f"{rel}:{r['line']}:hero") if hero else None,
            "hero_slot": block % 9 // 3 + 1, "line": SKILL_LINES[block % 3],
            "spells": spells, "codes": codes,
        })
    # 自检（验证"行 -> 英雄"的算法）：每个英雄恰好 9 行；去掉等级数字后缀（修补匠的 ROCKETS1..3）后不超过 4 个技能；
    # 不同英雄的技能集合互不相交。大招不一定在 6 级（死亡骑士/巫妖/熊猫有的行放 7 级），所以不拿大招当判据。
    by_hero = {}
    for o in out:
        by_hero.setdefault(o["hero"], []).append(o)
    base_sets = {h: {re.sub(r"\d+$", "", s) for o in lst for s in o["spells"] if s} for h, lst in by_hero.items()}
    checks = {}
    for hero, lst in by_hero.items():
        overlap = sorted({s for h2, st in base_sets.items() if h2 != hero for s in st & base_sets[hero]})
        ok = len(lst) == 9 and len(base_sets[hero]) <= 4 and not overlap
        checks[str(hero)] = {"rows": len(lst), "skills": sorted(base_sets[hero]), "shared_with_other_heroes": overlap, "ok": ok}
        if not ok:
            warnings.append(f"{rel} 英雄 {hero} 的加点行自检不通过：{checks[str(hero)]}")
    return header, out, checks


RACIAL_RE = re.compile(r"^racial_\w+(\[\d+\])?$")


def racial_units(names_units_rows, amai_race, tiers):
    """races.eai initRacialUnits：按 StandardUnits 的 race 列（完全相等）和 type 列（\\b词\\b 匹配）挑本族的 racial_* 变量。
    同一个变量匹配多行时 JASS 里后写的覆盖先写的（取最后一行），candidates 列出全部匹配。"""
    def pick(word, race=amai_race):
        return [r for r in names_units_rows if r["race"] == race and re.search(rf"\b{word}\b", r["type"])]

    def one(rows):
        if not rows:
            return None
        o = {"name": rows[-1]["name"], "code": rows[-1]["code"]}
        if len(rows) > 1:
            o["candidates"] = [r["name"] for r in rows]
        return o
    out = {}
    for word in ("peon", "ghoul", "expansion", "altar", "lumber", "rushcreep", "farm", "shop", "militia", "militiahero"):
        out[f"racial_{word}"] = one(pick(word))
    for t in tiers:
        out[f"racial_hall[{t}]"] = one(pick(f"hall{t}"))
    if out["racial_expansion"] is None:     # races.eai：不是亡灵那种矿上建筑就用一本大厅
        out["racial_expansion"] = dict(out.get("racial_hall[1]") or {}, fallback="racial_hall[1]") or None
    out["racial_goldmine"] = [r["code"] for r in pick("mine")] + [r["code"] for r in pick("mine", "NEUTRAL")]
    out["racial_zigg"] = [r["code"] for r in pick("zigg")]
    return out


# ============================================================================
# 主流程
# ============================================================================
def build_all(amai_dir: Path):
    """返回 ({文件名: JSON 对象}, 报告文本)。"""
    src = Source(amai_dir)
    names = Names()
    common_vocab = new_vocab()

    # ---- 名字表 ----
    su_header, su_rows = read_table(src, f"{VER}/StandardUnits.txt", efr=True)
    for r in su_rows:
        obj = row_object(su_header, r["fields"])
        name = r["fields"][0]
        bt = str(obj.get("buy type"))
        if name in names.units:
            src.warnings.append(f"StandardUnits.txt 名字重复：{name}")
        names.units[name] = {"code": r["fields"][1], "u_index": r["row"], "race": obj.get("race"),
                             "buy_type": bt, "kind": BUY_KIND.get(bt, "other")}
    ue_header, ue_rows_raw = read_table(src, f"{VER}/UnitEquivalence.txt", efr=True)
    for r in ue_rows_raw:
        if len(r["fields"]) > 2 and r["fields"][1] == "AI_COMP":
            names.aliases[r["fields"][2]] = r["fields"][0]
    _, ha_rows = read_table(src, f"{VER}/HeroAbilities.txt", efr=True)
    for r in ha_rows:
        names.abilities[r["fields"][0]] = r["fields"][1]
    _, ns_rows = read_table(src, f"{VER}/NeutralShops.txt", efr=True)
    for r in ns_rows:
        names.shops[r["fields"][0]] = r["fields"][1]

    races_rows = [r["fields"] for r in read_table(src, f"{VER}/Races.txt", efr=False)[1]]
    strengths_rows = [r["fields"] for r in read_table(src, f"{VER}/Strengths.txt", efr=False)[1]]
    merc_types = plain_list(src, f"{VER}/MercTypes.txt")
    dragon_types = plain_list(src, f"{VER}/DragonTypes.txt")
    hero_levels = plain_list(src, f"{VER}/HeroLevels.txt")
    constants = collect_constants(src, [x[0] for x in races_rows])
    conv = Converter(names, constants, [str(m) for m in merc_types], [str(d) for d in dragon_types])
    sigs = extract_signatures(src, [s[0] for s in strengths_rows])

    # ---- common.json ----
    common = {}
    std_units = []
    su_name_cols = ["Name in AI", "built in/by, needed1", "needed2", "needed3", "add for counting 1", "add for counting 2"]
    for r in su_rows:
        obj = row_object(su_header, r["fields"])
        obj["u_index"] = r["row"]
        codes, racial = {}, {}
        for col in su_name_cols:
            v = obj.get(col)
            # needed3 = "UPGRADED" 是关键字（这一行是 needed1 升级来的），不是名字
            if v == "UPGRADED":
                codes[col] = None
                continue
            # racial_altar / racial_hall[2] / racial_shop：物品、雇佣兵行的前置，按种族运行时代入（见各族 JSON 的 racial_units）
            if isinstance(v, str) and RACIAL_RE.match(v):
                codes[col] = None
                racial[col] = v
                continue
            codes[col] = names.code(v, f"{VER}/StandardUnits.txt:{r['line']}:{col}", kinds=("unit",))
        obj["codes"] = codes
        if racial:
            obj["racial_refs"] = racial
        obj["types"] = [] if obj.get("type") in (0, None) else str(obj["type"]).split(",")
        std_units.append(obj)
    common["standard_units"] = {"header": su_header, "rows": std_units}
    su_brief = [{"name": r["fields"][0], "code": r["fields"][1], "race": r["fields"][2],
                 "type": r["fields"][3] if len(r["fields"]) > 3 else ""} for r in su_rows]

    by_kind = {}
    for n, info in names.units.items():
        by_kind.setdefault(info["kind"], {})[n] = info["code"]
    by_kind["ability"] = dict(names.abilities)
    by_kind["neutral_shop"] = dict(names.shops)
    common["names"] = by_kind
    common["aliases"] = {a: {"name": b, "code": names.units[b]["code"] if b in names.units else None}
                         for a, b in names.aliases.items()}
    for a, b in names.aliases.items():
        if b not in names.units:
            names.miss(b, f"{VER}/UnitEquivalence.txt: AI_COMP 别名 {a} 的本名")

    h, rows = generic_table(src, f"{VER}/UnitEquivalence.txt", names,
                            ["Name in AI", "Upgrade ID"])
    for o in rows:   # AI_COMP 的 Equivalent Unit 是别名（不在 StandardUnits 里），其余类型是真名字
        eq = o.get("Equivalent Unit")
        o["codes"]["Equivalent Unit"] = (common["aliases"].get(eq, {}).get("code") if o.get("Equivalence Type") == "AI_COMP"
                                         else names.code(eq, f"{VER}/UnitEquivalence.txt:{o['row']}:Equivalent Unit"))
    common["unit_equivalence"] = {"header": h, "rows": rows}

    common["strengths"] = {
        "note": "Strengths.txt 没有表头。common.eai DetermineCounterForce：需要量 = enemy_<strength> - ally_<ally_counter1>/20 - ally_<ally_counter2>/20；"
                "'null' 表示没有（ally_null 恒为 0）。index = chosen_counter 的取值。",
        "rows": [{"index": i, "strength": s[0],
                  "ally_counter1": None if len(s) < 2 or s[1] == "null" else s[1],
                  "ally_counter2": None if len(s) < 3 or s[2] == "null" else s[2]}
                 for i, s in enumerate(strengths_rows)],
    }
    h, rows = generic_table(src, f"{VER}/StrengthPredictionRules.txt", names, expr_cols=["Condition"],
                            conv=conv, vocab=common_vocab)
    common["strength_prediction_rules"] = {
        "note": "common.eai GetPlayer<Strength>Strength：ai_time >= Start Time 且 (End Time < 0 或 ai_time <= End Time) 且 Condition 成立 -> 强度 + Bonus。"
                "ai_time = 计时器秒数 / 5（common.eai TimeKeeper）。Condition 里的 p 是被估计的玩家，oNAME 是原始 4 字码。",
        "header": h, "rows": rows}
    fav_cols = [c for c in read_table(src, f"{VER}/Profiles.txt", efr=True)[0] if c.startswith("Favorite")]
    h, rows = generic_table(src, f"{VER}/Profiles.txt", names, name_cols=fav_cols)
    common["profiles"] = {"header": h, "rows": rows}
    common["global_settings"] = settings_table(src, f"{VER}/GlobalSettings.txt", conv, common_vocab)
    h, rows = generic_table(src, f"{VER}/Upkeep.txt", names, expr_cols=["Save time"], conv=conv, vocab=common_vocab)
    common["upkeep"] = {"header": h, "rows": rows}
    common["hero_levels"] = hero_levels
    common["merc_types"] = [{"index": i, "type": t, "var": f"merc_{t}"} for i, t in enumerate(merc_types, 1)]
    common["dragon_types"] = [{"index": i, "type": t, "var": f"dragons[{i}]"} for i, t in enumerate(dragon_types, 1)]
    h, rows = generic_table(src, f"{VER}/Mercs.txt", names,
                            name_cols=["Merc1", "Merc2", "Merc3", "Merc4"] + [str(m) for m in merc_types])
    common["mercs"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/Dragons.txt", names, name_cols=[str(d) for d in dragon_types])
    common["dragons"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/NeededExtra.txt", names, name_cols=["Name in AI", "Id needed"])
    common["needed_extra"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/UnitConversions.txt", names, name_cols=["Name in AI", "Merge from 1", "Merge from 2"])
    common["unit_conversions"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/Healers.txt", names, name_cols=["Name", "Needed"])
    common["healers"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/HealingItems.txt", names, name_cols=["Mass Healing Item"],
                            expr_cols=["Condition"], conv=conv, vocab=common_vocab)
    common["healing_items"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/AddedAggression.txt", names)
    common["added_aggression"] = {"header": h, "rows": rows}
    common["races"] = [{"index": i, "race": x[0], "ai": x[1] if len(x) > 1 else None,
                        "ai2": x[2] if len(x) > 2 else None, "const": f"R_{x[0]}"}
                       for i, x in enumerate(races_rows, 1)]
    h, rows = generic_table(src, f"{VER}/NeutralShops.txt", names)
    common["neutral_shops"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/ItemCheck.txt", names)
    common["item_check"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/StandardAiSettings.txt", names, expr_cols=["Value"], conv=conv, vocab=common_vocab)
    common["standard_ai_settings"] = {"header": h, "rows": rows}
    h, rows = generic_table(src, f"{VER}/CommandBuilds.txt", names, name_cols=[x[0].capitalize() for x in races_rows])
    common["command_builds"] = {"header": h, "rows": rows}
    common["hero_abilities"] = [{"row": r["row"], "Ability name (in the AI)": r["fields"][0], "4-letter-code": r["fields"][1]}
                                for r in ha_rows]

    # 中立英雄顺序：各族 Heroes.txt 前 ver_neutral_hero_number 行（四族必须一致）
    gs_values = common["global_settings"]["values"]
    n_neutral = int(gs_values.get("ver_neutral_hero_number", 0))
    heroes_by_race = {}
    for key, folder, _ in RACES:
        hh, hrows = read_table(src, f"{VER}/{folder}/Heroes.txt", efr=True)
        heroes_by_race[key] = (hh, hrows)
    neutral_orders = {key: [r["fields"][0] for r in hrows[:n_neutral]] for key, (hh, hrows) in heroes_by_race.items()}
    neutral_order = neutral_orders["human"]
    for key, order in neutral_orders.items():
        if order != neutral_order:
            src.warnings.append(f"{key}/Heroes.txt 的中立英雄顺序和人族不同：{order}")
    skill_warn = []
    h, rows, checks = hero_skill_rows(src, f"{VER}/NeutralHeroSkills.txt", names, neutral_order, 0, len(hero_levels), skill_warn)
    n_neutral_rows = len(rows)
    common["neutral_hero_skills"] = {"header": h, "rows": rows, "self_check": checks, "hero_order": neutral_order}
    skill_rule = {
        "weights": {"major": gs_values.get("major_hero_skill_value"), "minor": gs_values.get("minor_hero_skill_value"),
                    "rare": gs_values.get("rare_hero_skill_value")},
        "rule": "common.eai LoadSkills：perc = GetRandomInt(0, major+minor+rare)；perc <= rare -> rare，perc <= minor+rare -> minor，否则 major",
        "hero_slot": "英雄是第几个出的（hero[1|2|3]），同一个英雄当第 1/2/3 个出时用不同的 3 行",
    }
    common["hero_skill_rule"] = skill_rule
    common["constants"] = {}      # 最后按引用补

    # ---- 各种族 ----
    race_out = {}
    race_stats = {}
    for key, folder, amai_race in RACES:
        vocab = new_vocab()
        warnings: list[str] = []
        base = f"{VER}/{folder}"
        rj = {}
        # 战略表
        sh, srows = read_table(src, f"{base}/Strategy.txt", efr=True)
        strat_ids = search_tree_ids([(r["fields"][0], scalar(r["fields"][12]) if len(r["fields"]) > 12 else 0)
                                     for r in srows])
        name_cols = ["key_building1", "key_building2", "key_upgrade1", "key_upgrade2", "key_unit",
                     "first hero bonus", "second hero bonus", "third hero bonus"]
        strategies = []
        for r in srows:
            obj = row_object(sh, r["fields"])
            obj["row"] = r["row"]
            obj["strat_id"] = strat_ids[r["fields"][0]]
            obj["codes"] = {c: names.code(obj.get(c), f"{base}/Strategy.txt:{r['line']}:{c}", kinds=("unit",))
                            for c in name_cols if c in obj}
            strategies.append(obj)
        rj["strategies"] = strategies

        # 建造序列
        rel = f"{base}/BuildSequence.ai"
        bp = BuildSeqParser(conv, sigs, rel, vocab).parse(src.text(rel))
        tiers = [int(t) for t in plain_list(src, f"{base}/Tiers.txt")]
        build = {}

        def tiered(stmts):
            return {str(t): [x for x in (stmt_for_tier(s, t) for s in stmts) if x is not None] for t in tiers}

        g_init = bp.functions.get("global_init_strategy")
        g_build = bp.functions.get("global_build_sequence")
        build["global_init"] = g_init["stmts"] if g_init else None
        build["global_build"] = {"calls": g_build["stmts"], "tiers": tiered(g_build["stmts"])} if g_build else None
        if not g_init or not g_build:
            warnings.append(f"{rel} 缺 global_init_strategy / global_build_sequence")
        strat_names = [s["Strategy Function Name"] for s in strategies]
        for n in strat_names:
            fi, fb = bp.functions.get(f"init_strategy_{n}"), bp.functions.get(f"build_sequence_{n}")
            if not fi or not fb:
                warnings.append(f"战略 {n} 在 {rel} 里缺 init_strategy_/build_sequence_ 函数")
            build[str(n)] = {"init": fi["stmts"] if fi else None, "tiers": tiered(fb["stmts"]) if fb else None,
                             "line": fb["line"] if fb else None}
        known = {"global_init_strategy", "global_build_sequence"} | \
                {f"{p}{n}" for n in strat_names for p in ("init_strategy_", "build_sequence_")}
        extra_funcs = sorted(set(bp.functions) - known)
        if extra_funcs:
            warnings.append(f"{rel} 里有 Strategy.txt 没登记的函数：{extra_funcs}")
        rj["build"] = build

        # 英雄
        hh, hrows = heroes_by_race[key]
        heroes = []
        for r in hrows:
            obj = row_object(hh, r["fields"])
            obj["Hero"] = r["fields"][0]
            obj["row"] = r["row"]
            obj["neutral"] = r["row"] <= n_neutral
            obj["codes"] = {"Hero": names.code(r["fields"][0], f"{base}/Heroes.txt:{r['line']}:Hero", kinds=("unit",))}
            heroes.append(obj)
        rj["heroes"] = {"header": hh, "rows": heroes}
        hero_order = [r["fields"][0] for r in hrows]
        h, rows, checks = hero_skill_rows(src, f"{base}/HeroSkills.txt", names, hero_order, n_neutral_rows,
                                          len(hero_levels), warnings)
        rj["hero_skills"] = {"header": h, "rows": rows, "self_check": checks,
                             "note": f"种族行 r 的块号 = {n_neutral_rows} + r - 1（前 {n_neutral_rows} 块是 NeutralHeroSkills.txt）；"
                                     "hero_row = 块号 // 9 + 1 = Heroes.txt 行号；hero_slot = 块号 % 9 // 3 + 1；line = 块号 % 3（major/minor/rare）"}

        # 小表
        rah, rarows = read_table(src, f"{base}/RaceAggression.txt", efr=True)
        rj["race_aggression"] = {
            "header": rah,
            "note": "races.eai SetRaceAggression：b_race_aggression[(row-1)*TIERNUM + X-1] = 第 X+1 列；行序 = Races.txt 顺序（第一列表头为空）",
            "rows": [dict({"row": r["row"], "label": r["fields"][0],
                           "race": races_rows[r["row"] - 1][0] if r["row"] - 1 < len(races_rows) else None},
                          **{rah[j]: scalar(r["fields"][j]) for j in range(1, min(len(rah), len(r["fields"])))})
                     for r in rarows]}
        rj["tiers"] = tiers
        rj["racial_units"] = racial_units(su_brief, amai_race, tiers)
        rj["settings"] = settings_table(src, f"{base}/Settings.txt", conv, vocab)
        cbh, cbrows = read_table(src, f"{base}/CommandBuilds.txt", efr=True)
        rj["command_builds"] = {
            "header": cbh,
            "note": "races.eai cmdBuild：strategy == 49 + row 时执行 <Type>Build，造到 bn_<Type> 个为止（优先级 90）",
            "rows": [{"row": r["row"], cbh[0]: r["fields"][0], cbh[1]: r["fields"][1] if len(r["fields"]) > 1 else None,
                      "code": names.code(r["fields"][1] if len(r["fields"]) > 1 else None,
                                         f"{base}/CommandBuilds.txt:{r['line']}", kinds=("unit",)),
                      "strategy_id": 49 + r["row"]} for r in cbrows]}
        if src.exists(f"{base}/Dynamic.txt"):
            dh, drows = read_table(src, f"{base}/Dynamic.txt", efr=True) if src.text(f"{base}/Dynamic.txt").strip() else ([], [])
            rj["dynamic"] = {"file": f"{base}/Dynamic.txt", "header": dh or None,
                             "rows": [row_object(dh, r["fields"]) for r in drows],
                             "note": "文件存在，但整个 AMAI 仓库没有任何文件引用它（本版本为空文件）"}
        else:
            rj["dynamic"] = None

        # 统计
        all_stmts = []
        func_count = 0
        for fname, f in bp.functions.items():
            func_count += 1
            all_stmts += [(fname, s) for s in f["stmts"]]
        calls = [s for _, s in all_stmts if s["fn"] != "set"]
        sets = [s for _, s in all_stmts if s["fn"] == "set"]
        failed_calls = [s for s in calls if s.get("issues")]
        conds = bp.conditions
        stats = {
            "strategies": len(strategies),
            "functions": func_count,
            "calls": {"total": len(calls), "ok": len(calls) - len(failed_calls), "failed": len(failed_calls),
                      "by_fn": dict(Counter(s["fn"] for s in calls))},
            "sets": {"total": len(sets), "failed": sum(1 for s in sets if s.get("issues"))},
            "conditions": {"total": len(conds), "ok": sum(1 for c in conds if c["ok"]),
                           "unsupported": sum(1 for c in conds if not c["ok"])},
            "quirks": sum(len(s.get("quirks", [])) for _, s in all_stmts) + sum(len(c["quirks"]) for c in conds),
            "tier_stmts": {str(t): sum(len(v["tiers"][str(t)]) for k, v in build.items()
                                       if isinstance(v, dict) and v.get("tiers")) for t in tiers},
            "vocabulary": {k: dict(sorted(c.items())) for k, c in vocab.items()},
            "parse_problems": bp.problems,
            "warnings": warnings,
        }
        rj["stats"] = stats
        race_out[key] = rj
        race_stats[key] = {"stats": stats, "parser": bp, "all_stmts": all_stmts, "folder": folder}

    src.warnings += skill_warn

    # 只保留被引用到的常量
    used = Counter()
    for c in [common_vocab] + [race_stats[k]["stats"]["vocabulary"] for k in race_stats]:
        used.update(c["const"])
    common["constants"] = {k: constants[k] for k in sorted(used) if k in constants}

    commit = src.commit()
    missing = {n: dict(sorted(c.items())) for n, c in sorted(names.missing.items())}
    common["unresolved_names"] = missing
    meta_base = {"generator": "tools/amai_import.py", "amai_repo": "https://github.com/SMUnlimited/AMAI",
                 "amai_commit": commit, "version_dir": VER}
    common["meta"] = dict(meta_base, sources={k: src.hashes[k] for k in sorted(src.hashes) if not k.startswith(f"{VER}/") or
                                             "/" not in k[len(VER) + 1:]},
                          warnings=sorted(set(src.warnings)), common_vocabulary={k: dict(sorted(c.items())) for k, c in common_vocab.items()})
    for key, folder, _ in RACES:
        race_out[key]["meta"] = dict(meta_base, race=key, amai_folder=folder,
                                     sources={k: v for k, v in sorted(src.hashes.items()) if k.startswith(f"{VER}/{folder}/")})

    outputs = {"common.json": common}
    for key, _, _ in RACES:
        outputs[f"{key}.json"] = race_out[key]
    report = make_report(common, race_out, race_stats, names, meta_base, src)
    return outputs, report


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


# ============================================================================
# 报告
# ============================================================================
def make_report(common, race_out, race_stats, names: Names, meta, src: Source) -> str:
    L = []
    w = L.append
    w("# AMAI（TFT）数据导入报告")
    w("")
    w("> 本文件由 `tools/amai_import.py` 生成，重复运行结果不变；要改请改脚本再重跑。")
    w("")
    w(f"- AMAI 源码：{meta['amai_repo']} 提交 `{meta['amai_commit']}`，目录 `{VER}/`")
    w("- 生成命令：`python tools/amai_import.py [--amai <AMAI 源码根目录>]`")
    w("- 输出：`brain/amai_data/common.json`、`human.json`、`orc.json`、`undead.json`、`nightelf.json`")
    w("")
    w("## 一、各种族统计")
    w("")
    w("| 种族 | 战略 | 函数 | call 调用 | 调用翻译成功 | 调用不支持 | set 语句 | if/elseif 条件 | 条件成功 | 条件不支持 | AMAI 原文怪癖 | 展开后 T1/T2/T3 语句数 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    tot = Counter()
    for key, _, _ in RACES:
        s = race_out[key]["stats"]
        ts = s["tier_stmts"]
        w(f"| {key} | {s['strategies']} | {s['functions']} | {s['calls']['total']} | {s['calls']['ok']} | {s['calls']['failed']} | "
          f"{s['sets']['total']} | {s['conditions']['total']} | {s['conditions']['ok']} | {s['conditions']['unsupported']} | "
          f"{s['quirks']} | {ts.get('1', 0)}/{ts.get('2', 0)}/{ts.get('3', 0)} |")
        tot.update({"strategies": s["strategies"], "calls": s["calls"]["total"], "ok": s["calls"]["ok"],
                    "failed": s["calls"]["failed"], "conds": s["conditions"]["total"], "cok": s["conditions"]["ok"],
                    "sets": s["sets"]["total"]})
    cov = 100.0 * tot["ok"] / tot["calls"] if tot["calls"] else 0.0
    ccov = 100.0 * tot["cok"] / tot["conds"] if tot["conds"] else 0.0
    w("")
    w(f"合计：{tot['strategies']} 个战略，{tot['calls']} 个 call（翻译成功 {tot['ok']}，覆盖率 {cov:.1f}%），"
      f"{tot['sets']} 个 set，{tot['conds']} 个条件（翻译成功 {tot['cok']}，覆盖率 {ccov:.1f}%）。")
    w("")
    w("判据：一条 call / 条件算\"翻译成功\" = 语法完整解析 + 里面所有像单位名的标识符（`uNAME` / `oNAME` / 裸大写名）都查到了 4 字码 + 函数签名对得上。"
      "条件里引用的运行时状态量（`GetGold()`、`enemy_air`、`c_enemy[R_ELF]` 这类）照原样结构化，不算失败，清单见第五节。")
    w("")
    w("### 各战略语句数（init / 展开到 T1 / T2 / T3）")
    w("")
    for key, _, _ in RACES:
        rj = race_out[key]
        w(f"**{key}**：" + "；".join(
            f"{s['Strategy Function Name']}(id {s['strat_id']}) "
            f"{len(rj['build'][str(s['Strategy Function Name'])]['init'] or [])}/"
            + "/".join(str(len((rj['build'][str(s['Strategy Function Name'])]['tiers'] or {}).get(str(t), []))) for t in rj["tiers"])
            for s in rj["strategies"]))
        w("")
    w("## 二、不支持的条件 / 调用（原文）")
    w("")
    any_bad = False
    for key, _, _ in RACES:
        st = race_stats[key]
        for c in st["parser"].conditions:
            if not c["ok"]:
                any_bad = True
                reasons = "；".join(i.get("reason") or f"找不到 {i.get('name')}" for i in c["issues"])
                w(f"- {key} `{st['folder']}/BuildSequence.ai:{c['line']}` 条件 `{c['raw']}` —— {reasons}")
        for fname, s in st["all_stmts"]:
            if s.get("issues"):
                any_bad = True
                reasons = "；".join(i.get("reason") or f"找不到 {i.get('name')}" for i in s["issues"])
                w(f"- {key} `{st['folder']}/BuildSequence.ai:{s['line']}` `{s['raw']}` —— {reasons}")
        for p in st["stats"]["parse_problems"]:
            any_bad = True
            w(f"- {key} `{st['folder']}/BuildSequence.ai:{p['line']}` `{p['raw']}` —— {p['reason']}")
    for sect in ("strength_prediction_rules", "upkeep", "healing_items", "standard_ai_settings"):
        for o in common[sect]["rows"]:
            for i in o.get("issues", []):
                any_bad = True
                w(f"- common {sect} 第 {o['row']} 行 `{i.get('raw')}` —— {i.get('reason')}")
    for o in common["global_settings"]["rows"]:
        for i in o.get("issues", []):
            any_bad = True
            w(f"- common GlobalSettings `{i.get('raw')}` —— {i.get('reason')}")
    for key, _, _ in RACES:
        for o in race_out[key]["settings"]["rows"]:
            for i in o.get("issues", []):
                any_bad = True
                w(f"- {key} Settings `{i.get('raw')}` —— {i.get('reason')}")
    if not any_bad:
        w("无。四个 BuildSequence.ai 的全部条件和调用、以及各表里的表达式列都完整解析了"
          "（解析器对\"and/or 混用不加括号\"\"not 后跟不加括号的比较\"这两种有优先级歧义的写法会直接判不支持，本版本没有出现）。")
    w("")
    w("## 三、找不到 4 字码的名字")
    w("")
    if names.missing:
        w("| 名字 | 出现位置（次数） |")
        w("|---|---|")
        for n, ctxs in sorted(names.missing.items()):
            w(f"| `{n}` | " + "<br>".join(f"{c}（{k}）" for c, k in sorted(ctxs.items())) + " |")
    else:
        w("无。")
    w("")
    w("不计入缺失的：名字列里的 `0`（表示\"无\"）、StandardUnits 的 `needed3 = UPGRADED`（关键字：本行由 needed1 升级而来）、"
      "`hero[N]` / `merc_<类型>` / `dragons[N]`（运行时才确定是谁，JSON 里给 `hero` / `special`）。")
    w("")
    w("## 四、AMAI 原文怪癖（翻译是忠实的，但 AMAI 自己这么写可能不是本意）")
    w("")
    q_any = False
    for key, _, _ in RACES:
        st = race_stats[key]
        for c in st["parser"].conditions:
            for q in c["quirks"]:
                q_any = True
                w(f"- {key} `{st['folder']}/BuildSequence.ai:{c['line']}` 条件 `{c['raw']}` —— {q}")
        for fname, s in st["all_stmts"]:
            for q in s.get("quirks", []):
                q_any = True
                w(f"- {key} `{st['folder']}/BuildSequence.ai:{s['line']}` `{s['raw']}` —— {q}")
    if not q_any:
        w("无。")
    w("")
    w("## 五、条件和参数里用到的运行时状态量（移植时要能提供）")
    w("")
    for key, _, _ in RACES:
        v = race_out[key]["stats"]["vocabulary"]
        w(f"**{key}**")
        w("")
        for kind, label in (("fn", "函数"), ("var", "变量"), ("const", "常量")):
            items = sorted(v[kind].items(), key=lambda kv: (-kv[1], kv[0]))
            w(f"- {label}：" + ("、".join(f"`{n}`×{c}" for n, c in items) if items else "无"))
        w("")
    cv = common["meta"]["common_vocabulary"]
    w("**common 表里的表达式列**（StrengthPredictionRules / Upkeep / HealingItems / StandardAiSettings / GlobalSettings）")
    w("")
    for kind, label in (("fn", "函数"), ("var", "变量"), ("const", "常量")):
        items = sorted(cv[kind].items(), key=lambda kv: (-kv[1], kv[0]))
        w(f"- {label}：" + ("、".join(f"`{n}`×{c}" for n, c in items) if items else "无"))
    w("")
    w("## 六、表结构的关键发现")
    w("")
    hs = race_out["human"]["hero_skills"]
    n_neutral_rows = len(common["neutral_hero_skills"]["rows"])
    w("### 1. HeroSkills.txt 的行怎么对应英雄（表里没有英雄列）")
    w("")
    w("链路（全在 AMAI 源码里）：")
    w("")
    w(f"- `common.eai InitNeutralSkills`：`NeutralHeroSkills.txt` 第 r 行写进 `allskills` 的第 `r-1` 块（每块 10 级）。中立表 {n_neutral_rows} 行。")
    w(f"- `races.eai InitAllSkills`：`<Race>/HeroSkills.txt` 第 r 行写进第 `NEUTRALHEROLNUM - 1 + r` 块，NEUTRALHEROLNUM = 中立表行数 = {n_neutral_rows}。")
    w("- `races.eai set_skills`：遍历 `<Race>/Heroes.txt`，第 hn 行的英雄是 `hero[1|2|3]` 时调 `LoadSkills(1|2|3, hn, …)`。")
    wt = common["hero_skill_rule"]["weights"]
    w("- `common.eai LoadSkills(sa, hn, ta)`：读块号 `(sa-1)*3 + sk + (hn-1)*9`，sk = 0 主 / 1 次 / 2 稀有，按 GlobalSettings 的 "
      f"major_hero_skill_value={wt['major']} / minor={wt['minor']} / rare={wt['rare']} 随机"
      "（`perc = GetRandomInt(0, 三者之和)`，`perc <= rare` 取稀有，`<= minor+rare` 取次，否则取主）。")
    w("")
    w("所以：**Heroes.txt 前 8 行是中立英雄（四族同序），第 9~12 行是本族英雄；每个英雄 9 行 = 3 个出场位次（第 1/2/3 个英雄）× 3 条加点线（主/次/稀有）。**"
      f"种族 HeroSkills.txt 第 1~9 行属于 Heroes.txt 第 9 行的英雄（人族 = {hs['rows'][0]['hero']}），第 10~18 行属于第 10 行，依此类推。")
    w("JSON 里每行给 `hero` / `hero_code` / `hero_row` / `hero_slot` / `line`。")
    w("")
    bad = [f"{k}:{h}" for k in race_out for h, c in race_out[k]["hero_skills"]["self_check"].items() if not c["ok"]]
    bad += [f"neutral:{h}" for h, c in common["neutral_hero_skills"]["self_check"].items() if not c["ok"]]
    w("自检（写在 `self_check` 里）：按上面公式分组后，每个英雄恰好 9 行、去掉等级数字后缀（修补匠的 `ROCKETS1..3`）后只用 ≤4 个技能、"
      "不同英雄的技能集合互不相交 —— " + ("四族 + 中立全部通过。" if not bad else f"不通过：{bad}"))
    w("（大招不一定在 6 级：死亡骑士、巫妖、熊猫酒仙有的行 7 级才学，所以不拿大招当判据。）")
    w("")
    w("和 AMAI 自己的预处理产物对照：`tests/test_amai_import.py` 的 `TestAgainstEjass` 用 AMAI 自带的 `ejass.pl` 预处理 `races.eai`"
      "（需要 perl），逐族比对 `set allskills[N] = 技能` 的下标 = `block*10 + 级数`、`set_skills` 里每个英雄的 hn、`STRAT_<名字>` 常量。"
      "09-18 导入时四族全部一致。中立英雄的块号在 common.eai 里，这份 clone 预处理 common.eai 会在缺失的 "
      "`Languages\\English\\ChatAGG.txt` 处中断、走不到 InitNeutralSkills，所以中立表只靠上面的自检和\"种族块从第 "
      f"{n_neutral_rows} 块开始\"这一点间接验证。")
    w("")
    w("### 2. 读表规则（ejass.pl）")
    w("")
    w("- `#EFR` 才跳表头：Strengths / Races / Tiers / HeroLevels / MercTypes / DragonTypes 没有表头，第一行就是数据。")
    w("- `%row` 从 1 开始、`#` 开头的行不算。`uNAME` = StandardUnits 的 %row（AMAI 内部序号），`NAME` / `oNAME` = 原始 4 字码。"
      "BuildSequence 里几乎都用 `uNAME`；原生函数要套 `old_id[uNAME]` 转回原始 4 字码。")
    w("- UnitEquivalence 的 AI_COMP 行生成别名（`%3 = o%1`、`u%3 = u%1`）：`TANK` = STEAM_TANK 的原始 4 字码、`MOON_CHICK` = MOON_PRIESTESS 等。"
      "JSON 的单位引用里用了别名会带 `alias`。")
    w("- Settings 的覆盖顺序：`InitGlobalSettings`（GlobalSettings.txt）先 `set`，再 `<Race>/Settings.txt` 的 `set`，同名后写覆盖先写。"
      f"GlobalSettings.txt 自己就有重复键：{common['global_settings']['duplicate_keys']}（`values` 里取最后一次）。")
    w("")
    w("### 3. Strategy.txt")
    w("")
    w("- 56 列 = 13 列基本信息 + ally/1on1/FFA/random 4 列 + 各族 enemy/ally bonus 各 4 列 + `anti <strength> strength` 11 列（Strengths.txt 行数）"
      " + 两个开矿时间 + 三组英雄加成 + `enemy_<RACE> add agg t1..t3` 12 列。races.eai 里用 `18 + STRENGTHNUM + 2*RACENUM` 这类偏移取列，"
      "**加减种族或强度类型会整体移位**。")
    w("- `STRAT_<名字>` 编号不是行号：`#SEARCHTREE Strats ... %1 %13` 按 base rp 建 Huffman 树、叶子先序编号（JSON 的 `strat_id`，"
      "按 ejass.pl 算法复算，并和 ejass.pl 预处理产物逐族对照一致）。`debug_strategy` 设置用的就是这个编号；命令建造（CommandBuilds）占 50 起的编号。"
      "`build_sequence` 的分派（`#SEARCHCODE Strats strategy`）也是按这棵树生成的 if 链。")
    w("- `strat_minimum_time` 单位是 秒/6（races.eai 里 `strat_minimum_time*6` 秒）；common 里的 `ai_time` = 计时器秒数 / 5（TimeKeeper）。"
      "StrengthPredictionRules 的 Start/End Time、Harass 的 min_time/time 都按 ai_time 比。")
    zero_ku = [f"{k}:{s['Strategy Function Name']}" for k in race_out for s in race_out[k]["strategies"] if s["key_unit"] in (0, "0")]
    w(f"- `key_unit` 为 `0`（没有关键兵种）的战略：{zero_ku}；其余战略的 key_unit / key_building / key_upgrade 全部查到 4 字码（JSON 的 `codes`）。")
    w("")
    w("### 4. BuildSequence.ai 怎么变成 JSON")
    w("")
    w("- 语句只有 `call` / `set` / `if-elseif-else-endif` 三种（没有 loop/local/return）；每条语句的 `cond` 是所有外层 if 条件的合取，"
      "elseif/else 分支带上前面分支的 `not`，`cond_raw` 是对应原文。")
    w("- `tiers` 是把 `tier` 分别代成 Tiers.txt 的 1/2/3 后做部分求值：恒假的语句去掉、恒真的条件去掉、`50 + (tier - 1) * 15` 这类常量折叠成数字。"
      "战略的标准写法 `if tier == 3 … elseif tier == 2 … else …` 因此正好落到对应层；`global_build` 里散落的 `if tier >= 2` 也一样处理，"
      "另外保留未展开的 `calls` 供核对。")
    w("- qty / prio 不一定是数字：`Min(((TownCountDone(uRAIDER)/2)-2),1)`、`ghoul_prio` 这类给表达式结构。"
      "所有 call 都有按 common.eai 函数签名命名的 `args`；有 qty / unitid|upgid|u / prio 参数的另外拍平成顶层 `qty` / `code` + `name`（或 `hero` / `special`）/ `prio`。")
    w("- 几个函数的语义（common.eai）：`BuildAdvUpgr2(qty, upgid, starttier, unitcount, unitper, max, prio)` 在 tier >= starttier 且 "
      "unitcount > unitper 时，按 `min(10*unitcount/unitper, max)%` 的概率下令（unitcount > 2*unitper 时再掷一次 max%），"
      "要的级数 = min(qty, tier-starttier+1)，优先级 prio + (tier-starttier)*4；"
      "`SetBuildReact(food, min1, unit1, min2, unit2, enemy_strength, strength1, strength2, prio)` 按敌方强度在 strength1~strength2 间线性分配 food 给两个兵种；"
      "`AddBlock(req_qty, req_type, only_done, allow_qty, allow_type, expire_time)` 在 ai_time < expire_time 且 req_type 不足 req_qty 时把 allow_type 限到 allow_qty；"
      "`SetTierBlock(tier, unitper, foodlimit, expansionblock)` 是升本闸门；`basic_melee(food, prio)` 用 Settings 的 race_simple_melee / race_advanced_melee。")
    w("- `Elf/Dynamic.txt` 是空文件，AMAI 源码里没有任何地方引用它。")
    w("")
    if common["meta"]["warnings"]:
        w("## 七、导入时的警告")
        w("")
        for x in common["meta"]["warnings"]:
            w(f"- {x}")
        for key, _, _ in RACES:
            for x in race_out[key]["stats"]["warnings"]:
                w(f"- {key}：{x}")
        w("")
    return "\n".join(L) + "\n"


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AMAI（TFT）战略表 + 建造序列 -> JSON")
    ap.add_argument("--amai", help="AMAI 源码根目录（含 TFT/、common.eai、races.eai、ejass.pl）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 输出目录")
    ap.add_argument("--report", default=str(DEFAULT_REPORT), help="报告输出路径")
    args = ap.parse_args(argv)
    amai = find_amai_dir(args.amai)
    if amai is None:
        print("找不到 AMAI 源码：用 --amai 指定，或设置环境变量 AMAI_DIR（目录下要有 TFT/StandardUnits.txt）", file=sys.stderr)
        return 2
    outputs, report = build_all(amai)
    out = Path(args.out)
    for fname, obj in outputs.items():
        write_text(out / fname, dumps(obj))
    write_text(Path(args.report), report)
    for key, _, _ in RACES:
        s = outputs[f"{key}.json"]["stats"]
        print(f"{key}: 战略 {s['strategies']}，call {s['calls']['total']}（成功 {s['calls']['ok']}），"
              f"条件 {s['conditions']['total']}（成功 {s['conditions']['ok']}），怪癖 {s['quirks']}")
    print(f"找不到 4 字码的名字：{len(outputs['common.json']['unresolved_names'])} 个；输出 -> {out}，报告 -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

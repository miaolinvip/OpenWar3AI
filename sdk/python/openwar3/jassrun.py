# -*- coding: utf-8 -*-
"""JASS 小脚本（09-24）：从外面随时调游戏里的 JASS 能力。一行一句，**可以直接粘 JASS 写法**，也可以用 Python 的样子写：

    local texttag t = CreateTextTag()
    call SetTextTagText(t, "|cffffcc00你好|r", 0.024)
    set h = hero()
    call SetTextTagPosUnit(t, h, 60)
    call SetTextTagPermanent(t, false)
    call SetTextTagLifespan(t, 5)
    call PingMinimapEx(h.x, h.y + 300, 3, 255, 0, 0, false)

支持：任意 native（嵌套调用）、变量（set / local / 直接 x = ...）、整数 / 实数 / "字符串" / 'Hpal' 四字码 / true false null、
加减乘除和比较、单位属性（h.x h.y h.hp h.type h.owner h.level）、`//` 注释。
内置：hero() 我方主英雄、me() 本机玩家、unit(类型码[, 玩家号]) 找一个单位、unit_at(x, y[, 半径])、wait(秒)、print(...)。
不支持 if / loop / function（要写逻辑就用 Python 调 g.jass，或者写成方案）。

入口：命令行 `python -m openwar3 jass --inst 20`（交互）/ `-e "代码"` / 脚本文件；远见「JASS 控制台」页；
HTTP `POST /api/instances/{n}/jass {"code": "..."}`（console/server，只听本机）。
"""
from __future__ import annotations

import ast
import re
import time

from .jass import JassError, signatures

MAX_STATEMENTS = 300
MAX_WAIT = 30.0
UNIT_ATTRS = ("x", "y", "hp", "hp_max", "mana", "mana_max", "type", "owner", "level", "addr", "alive", "is_hero")
_TYPES = ("integer", "real", "boolean", "string", "code", "handle", "unit", "player", "item", "effect", "texttag", "lightning",
          "image", "ubersplat", "multiboard", "multiboarditem", "timer", "timerdialog", "trigger", "dialog", "button", "sound",
          "fogmodifier", "location", "rect", "region", "group", "force", "destructable", "leaderboard", "quest", "event",
          "widget", "playercolor", "hashtable")


class ScriptError(RuntimeError):
    def __init__(self, line: int, msg: str):
        super().__init__(f"第 {line} 行：{msg}")
        self.line = line


def _strip_comment(line: str) -> str:
    out, q = [], None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
            out.append(c)
        elif line.startswith("//", i):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def translate(code: str) -> list[tuple[int, str]]:
    """JASS 写法 -> 能用 ast 解析的一行一句。返回 [(原行号, 句子)]。"""
    out = []
    for n, raw in enumerate(code.splitlines(), 1):
        s = _strip_comment(raw).strip()
        if not s:
            continue
        s = re.sub(r"^call\s+", "", s)
        s = re.sub(r"^set\s+", "", s)
        m = re.match(r"^local\s+(\w+)\s+(?:array\s+)?(\w+)\s*(=\s*(.*))?$", s)
        if m:
            if not m.group(3):
                continue                                  # 只声明不赋值
            s = f"{m.group(2)} = {m.group(4)}"
        out.append((n, s))
    return out


class Script:
    """跑小脚本。变量跨多次 run 保留（交互控制台一行一行敲）。"""

    def __init__(self, g, echo=None):
        self.g = g
        self.vars: dict = {}
        self.echo = echo or (lambda *a: None)
        self.builtins = {"hero": self._hero, "me": lambda: g.jass.GetLocalPlayer(), "unit": self._unit,
                         "unit_at": self._unit_at, "wait": self._wait, "print": self._print}

    # ── 内置 ──
    def _hero(self):
        s = self.g.snapshot(max_age=0)
        mine = [u for u in (s.units if s else []) if u.owner == s.me and u.alive]
        heroes = [u for u in mine if u.is_hero]
        if heroes:
            return max(heroes, key=lambda h: (getattr(h, "level", 0) or 0, h.hp_max))
        movers = [u for u in mine if not self.g.is_building(u)]
        if not movers:
            raise JassError("我方没有英雄也没有单位")
        return movers[0]

    def _unit(self, code: str, owner: int | None = None):
        s = self.g.snapshot(max_age=0)
        o = s.me if owner is None else int(owner)
        u = next((u for u in s.units if u.type == code and u.owner == o and u.alive), None)
        if u is None:
            raise JassError(f"玩家 {o} 没有 {code}")
        return u

    def _unit_at(self, x: float, y: float, r: float = 300.0):
        s = self.g.snapshot(max_age=0)
        cand = [u for u in s.units if u.alive and (u.x - x) ** 2 + (u.y - y) ** 2 <= r * r]
        if not cand:
            raise JassError(f"({x:.0f}, {y:.0f}) 半径 {r:.0f} 内没有单位")
        return min(cand, key=lambda u: (u.x - x) ** 2 + (u.y - y) ** 2)

    def _wait(self, seconds: float):
        time.sleep(max(0.0, min(float(seconds), MAX_WAIT)))

    def _print(self, *args):
        self.echo(" ".join(str(show(a)) for a in args))

    # ── 求值 ──
    def run(self, code: str) -> list[dict]:
        """跑一段脚本：[{line, code, value}]。出错抛 ScriptError（带行号），前面已经跑过的句子效果已经生效。"""
        rows = []
        stmts = translate(code)
        if len(stmts) > MAX_STATEMENTS:
            raise ScriptError(stmts[MAX_STATEMENTS][0], f"一次最多 {MAX_STATEMENTS} 句")
        for n, s in stmts:
            try:
                tree = ast.parse(s, mode="exec")
            except SyntaxError as e:
                raise ScriptError(n, f"看不懂这一句：{s}（{e.msg}）") from None
            if len(tree.body) != 1:
                raise ScriptError(n, "一行只写一句")
            st = tree.body[0]
            try:
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    v = self._eval(st.value)
                    self.vars[st.targets[0].id] = v
                elif isinstance(st, ast.Expr):
                    v = self._eval(st.value)
                else:
                    raise JassError("只支持「调用」和「变量 = 值」（没有 if / loop / function）")
            except JassError as e:
                raise ScriptError(n, str(e)) from None
            rows.append({"line": n, "code": s, "value": show(v)})
        return rows

    def _eval(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                return node.value
            raise JassError(f"不支持的常量 {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id in self.vars:
                return self.vars[node.id]
            low = {"true": True, "false": False, "null": None, "True": True, "False": False, "None": None}
            if node.id in low:
                return low[node.id]
            raise JassError(f"没有叫 {node.id} 的变量")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            v = self._eval(node.operand)
            return -v if isinstance(node.op, ast.USub) else (+v if isinstance(node.op, ast.UAdd) else not v)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)):
            a, b = self._eval(node.left), self._eval(node.right)
            ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b,
                   ast.Div: lambda: a / b, ast.Mod: lambda: a % b}
            return ops[type(node.op)]()
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = self._eval(node.left), self._eval(node.comparators[0])
            op = type(node.ops[0])
            return {ast.Eq: a == b, ast.NotEq: a != b, ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b}.get(op)
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Attribute):
            obj = self._eval(node.value)
            if node.attr in UNIT_ATTRS and hasattr(obj, "addr"):
                return getattr(obj, node.attr)
            raise JassError(f"只能读单位的 {'/'.join(UNIT_ATTRS)}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise JassError("只能直接调函数名（不支持 a.b() 和 x=1 这种写法）")
            args = [self._eval(a) for a in node.args]
            fn = node.func.id
            if fn in self.builtins:
                return self.builtins[fn](*args)
            if fn not in signatures():
                raise JassError(f"没有叫 {fn} 的 native（区分大小写；BJ 函数 *BJ 是地图脚本里的，不是 native）")
            return self.g.jass.call(fn, *args)
        raise JassError(f"不支持这种写法：{ast.unparse(node)}")


def show(v):
    """结果转成能打印 / 能放进 JSON 的样子。"""
    if hasattr(v, "addr") and hasattr(v, "type"):
        return {"unit": v.type, "owner": v.owner, "x": round(v.x), "y": round(v.y), "addr": v.addr}
    if isinstance(v, float):
        return round(v, 4)
    return v

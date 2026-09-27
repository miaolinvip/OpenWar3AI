# -*- coding: utf-8 -*-
"""BuildSequence.ai 里表达式（条件、数量、优先级）的求值器。

节点形状来自 tools/amai_import.py 的结构化输出（docs/amai_data_report.md 第四节），全部形状：
    数字 / true,false
    {"lhs","op","rhs"}            add sub mul div gt lt ge le eq ne
    {"args":[...],"op":"and|or"}  {"arg":...,"op":"not"}
    {"var":名} / {"index":..,"var":名}                tier、enemy_air、c_enemy[R_ELF]…
    {"const":名,"value"|"jass"}                       R_ELF、RACE_UNDEAD、MAP_RANDOM_HERO
    {"fn":名}                                         GetGold/GetWood/GetMinesOwned/GetRushFactor/HasExpanded
    {"fn":名,"args":[...]}                            Min/Max/R2I/GetPlayerRace/IsMapFlagSet/GetPlayerUnitTypeCount
    {"fn":名,"code","name"[,"id_kind"]}               TownCountDone/TownCount/GetUpgradeLevel/GetUnitCountDone
    {"code","unit"} / {"hero":N} / {"kind":"merc"|"dragon","special":…}

JASS 语义（踩过的坑都写在这里）：
  * 两个整数相除是**整数除法、向零截断**（`TownCountDone(uFOOTMAN)/5`），任一边是实数才是浮点；
  * Min/Max 在 AMAI 里是**整数**函数（common.eai:1796、1813），RMin/RMax 才是实数版；
  * 条件里的 and/or 在 JASS 不短路，但我们短路求值 —— 这里所有取值都没有副作用，结果一样；
  * 取不到的值一律当 0 / false，并记一次数（env.miss），不抛异常打断一局。
"""
from __future__ import annotations


class EvalError(Exception):
    pass


def _num(v):
    if isinstance(v, bool):
        return 1 if v else 0
    return v if isinstance(v, (int, float)) else 0


def jass_div(a, b):
    """JASS 的除法：两边都是整数 -> 整数除法（向零截断）；除零在 JASS 里是崩线程，这里返回 0。"""
    if b == 0:
        return 0
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q
    return a / b


def r2i(v) -> int:
    return int(v)                      # R2I 向零截断


class Evaluator:
    """env 是世界适配器（engine.WorldEnv），提供 town_count / gold / enemy 等取值。"""

    def __init__(self, env):
        self.env = env

    # ── 入口 ──
    def truth(self, node) -> bool:
        if node is None:
            return True                # cond 为空 = 无条件
        v = self.eval(node)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        return bool(v)

    def value(self, node, default=0):
        v = self.eval(node)
        return v if isinstance(v, (int, float, bool)) else default

    def int_value(self, node, default=0) -> int:
        v = self.value(node, default)
        return int(v)

    def code(self, node) -> str | None:
        """单位引用 -> 4 字码；hero[N] 查当前选的英雄；雇佣兵/龙这类运行时才定的返回 None。"""
        if node is None or isinstance(node, (int, float, bool)):
            return None
        if isinstance(node, dict):
            if "hero" in node:
                return self.env.hero(int(node["hero"]))
            if node.get("kind") in ("merc", "dragon"):
                return None
            if "code" in node:
                return node["code"] or None
        return None

    # ── 递归求值 ──
    def eval(self, node):
        if node is None:
            return 0
        if isinstance(node, bool) or isinstance(node, (int, float)):
            return node
        if isinstance(node, str):
            return node
        if not isinstance(node, dict):
            return 0
        if "op" in node:
            return self._op(node)
        if "fn" in node:
            return self._fn(node)
        if "var" in node:
            return self._var(node)
        if "const" in node:
            return self._const(node)
        if "hero" in node or "kind" in node or "code" in node:
            return self.code(node) or 0
        return 0

    def _op(self, node):
        op = node["op"]
        if op == "not":
            return not self.truth(node.get("arg"))
        if op in ("and", "or"):
            args = node.get("args") or []
            if op == "and":
                return all(self.truth(a) for a in args)
            return any(self.truth(a) for a in args)
        lhs, rhs = node.get("lhs"), node.get("rhs")
        if op in ("eq", "ne"):
            a, b = self._cmp_operand(lhs), self._cmp_operand(rhs)
            return (a == b) if op == "eq" else (a != b)
        a, b = self.eval(lhs), self.eval(rhs)
        a, b = _num(a), _num(b)
        if op == "add":
            return a + b
        if op == "sub":
            return a - b
        if op == "mul":
            return a * b
        if op == "div":
            return jass_div(a, b)
        if op == "gt":
            return a > b
        if op == "lt":
            return a < b
        if op == "ge":
            return a >= b
        if op == "le":
            return a <= b
        raise EvalError(f"不认识的运算符 {op}")

    def _cmp_operand(self, node):
        """== / != 两边可能是单位码、种族、技能线这些非数值量。"""
        c = self.code(node)
        if c is not None:
            return c
        return self.eval(node)

    def _fn(self, node):
        fn = node["fn"]
        if "code" in node:                       # TownCountDone(uFOOTMAN) 这一类
            code = node["code"]
            if fn == "TownCountDone":
                return self.env.town_count(code, only_done=True)
            if fn == "TownCount":
                return self.env.town_count(code, only_done=False)
            if fn == "GetUnitCountDone":
                return self.env.unit_count(code, only_done=True)
            if fn == "GetUpgradeLevel":
                return self.env.upgrade_level(code)
            self.env.miss(f"函数·{fn}")
            return 0
        args = node.get("args")
        if args and fn in ("TownCountDone", "TownCount", "GetUnitCountDone") and any(
                isinstance(a, dict) and a.get("kind") in ("merc", "item") for a in args):
            self.env.miss("没接·雇佣兵/物品计数")   # 雇佣兵和商店物品执行层买不了，按 0 算（别混进「取不到·函数」里刷屏）
            return 0
        if args is None:                         # 无参
            if fn == "GetGold":
                return self.env.gold()
            if fn == "GetWood":
                return self.env.wood()
            if fn == "GetMinesOwned":
                return self.env.mines_owned()
            if fn == "GetRushFactor":
                return self.env.rush_factor()
            if fn == "HasExpanded":
                return self.env.has_expanded()
            self.env.miss(f"函数·{fn}")
            return 0
        if fn == "Min":
            return min(self.int_value(a) for a in args)
        if fn == "Max":
            return max(self.int_value(a) for a in args)
        if fn == "R2I":
            return r2i(self.value(args[0]))
        if fn == "GetPlayerRace":
            return self.env.player_race(args[0])
        if fn == "IsMapFlagSet":
            return self.env.map_flag(self._const_name(args[0]))
        if fn == "GetPlayerUnitTypeCount":
            return self.env.player_unit_count(self.eval(args[0]), self.code(args[1]))
        if fn == "GetRandomInt":
            return self.env.random_int(self.int_value(args[0]), self.int_value(args[1]))
        if fn == "GetFloatGameState" or fn == "GetPlayerState":
            self.env.miss(f"函数·{fn}")
            return 0
        self.env.miss(f"函数·{fn}")
        return 0

    @staticmethod
    def _const_name(node):
        return node.get("const") if isinstance(node, dict) else None

    def _var(self, node):
        name = node["var"]
        if "index" in node:
            return self.env.var_index(name, self.eval(node["index"]))
        return self.env.var(name)

    def _const(self, node):
        name = node.get("const")
        if "value" in node and isinstance(node["value"], (int, float)):
            return node["value"]
        return self.env.const(name)

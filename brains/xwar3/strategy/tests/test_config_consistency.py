# -*- coding: utf-8 -*-
"""守着「同一条规则写在两处，只改一处等于没改」在 config <-> tactics 之间的那一份。

`config.apply()` 直接改写 tactics 的模块常量：`T.HERO_RETREAT_HP = values["hero_retreat_hp"]`。
所以两边默认值不一致时，**tactics.py 里写的那个数从来不会生效**，而且代码、注释、测试看起来全都是对的。

09-21 查出来的实际损失：`47042ea`（内核 #95 / 战斗模块 v10「英雄安全」）把 tactics 的
HERO_RETREAT_HP 改成 0.50、HERO_READY_HP 改成 0.70，config.py 还是 0.35 / 0.60 —— 生产上跑的一直是旧值。
之后 v10~v14 五个版本、二十多局都在调一条没上线的血线。SUPPORT_MIN_RATIO 和 FLEE_LOCAL_RATIO 同样（0.55 写着、0.80 在跑）。

这条测试是**静态**的：直接读两份源码里的字面量比较，不依赖任何一次运行 ——
因为运行时它们必然一致（apply 会覆盖），一致性只能在源码层面检查。

★ 09-21 扩展：tactics 里有一组常量不再是字面量，而是 `= W.R_XXX`（`brain/winprob.py` 从
校准过的胜率/损失份额推出来的）。对这一组，检查的是**两边引用的是不是 winprob 里同一个名字**
—— 那是比数值相等更强的保证：同源就不可能分叉。config.py 那边必须写成 `W.R_XXX`，
不能自己填一个抄过来的数字（抄的那一刻就已经分叉了）。
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import config as CFG  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain")


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _schema_sources():
    """SCHEMA 里每一项的**默认值那一格**的源码文本（不是求值结果）。

    SCHEMA 是一张 (key, 组, 标签, 类型, 默认, 最小, 最大, 步长, 说明) 的元组表，
    默认值是第 5 格。用 ast 取比正则稳：说明里有逗号、有百分号、有跨行拼接。"""
    tree = ast.parse(_read("config.py"))
    out = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SCHEMA" for t in node.targets)):
            continue
        for row in node.value.elts:
            if not isinstance(row, ast.Tuple) or len(row.elts) < 5:
                continue
            key = row.elts[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                out[key.value] = row.elts[4]
    return out


def _winprob_name(expr):
    """`W.R_CREEP` -> "R_CREEP"；别的（字面量、算式）-> None。"""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "W":
        return expr.attr
    return None


class ConfigOverridesMatchSource(unittest.TestCase):
    def test_every_overridden_constant_has_the_same_default(self):
        cfg_src = _read("config.py")
        tac_src = _read("tactics.py")
        pairs = re.findall(r"T\.([A-Z_][A-Z_0-9]*)\s*=\s*values\[\"([a-z_0-9]+)\"\]", cfg_src)
        self.assertGreater(len(pairs), 10, "config.apply 里没找到 T.X = values[...]，正则要跟着改")
        literals = dict(re.findall(r"^([A-Z_][A-Z_0-9]*)\s*=\s*(-?[0-9.]+)", tac_src, re.M))
        # tactics 里写成 `NAME = W.SOMETHING` 的那一组：记下它引用的 winprob 名字
        refs = dict(re.findall(r"^([A-Z_][A-Z_0-9]*)\s*=\s*W\.([A-Z_][A-Z_0-9]*)", tac_src, re.M))
        schema = _schema_sources()
        bad = []
        for const, key in pairs:
            self.assertIn(key, CFG.DEFAULTS, f"config.apply 用了 values[{key!r}]，但 SCHEMA 里没有这一项")
            if const in refs:
                cfg_ref = _winprob_name(schema.get(key))
                if cfg_ref != refs[const]:
                    bad.append(f"{const} 在 tactics.py 里是 W.{refs[const]}，"
                               f"而 config.py 的 {key} 默认值是 "
                               f"{'W.' + cfg_ref if cfg_ref else ast.unparse(schema[key]) if key in schema else '缺失'}"
                               f" —— 两边必须引用 winprob 里同一个名字，不能抄数字")
                continue
            self.assertIn(const, literals,
                          f"config.apply 覆盖了 T.{const}，但 tactics.py 里既不是字面量也不是 W.X")
            if abs(float(literals[const]) - float(CFG.DEFAULTS[key])) > 1e-9:
                bad.append(f"{const}={literals[const]}（tactics.py） vs {key}={CFG.DEFAULTS[key]}（config.py 默认，实际生效）")
        self.assertFalse(bad, "这些常量在 tactics.py 里写的值永远不会生效，改它等于没改：\n  " + "\n  ".join(bad))
        self.assertTrue(refs, "一个 W.X 都没有？winprob 那一层被拆掉了，这条分支就该删")

    def test_apply_really_overwrites(self):
        """证明这条测试有存在的必要：apply 确实会改模块属性（不是只读配置）。"""
        from brain import tactics as T
        before = T.HERO_RETREAT_HP
        try:
            CFG.apply(dict(CFG.DEFAULTS, hero_retreat_hp=0.11), {})
            self.assertEqual(T.HERO_RETREAT_HP, 0.11)
        finally:
            CFG.apply(dict(CFG.DEFAULTS), {})
            self.assertEqual(T.HERO_RETREAT_HP, CFG.DEFAULTS["hero_retreat_hp"])
            T.HERO_RETREAT_HP = before if before == CFG.DEFAULTS["hero_retreat_hp"] else T.HERO_RETREAT_HP


class HeroPlanUsesLiveThresholds(unittest.TestCase):
    """英雄评分表不能自己再存一份血线 —— 那会变成第三份，而且是不接在线配置的那一份。"""

    def test_heroplan_reads_tactics_constants(self):
        from types import SimpleNamespace

        from brain import heroplan as HP
        from brain import tactics as T

        home = SimpleNamespace(x=0.0, y=0.0)
        hero = SimpleNamespace(addr=2, type="Hamg", x=3000.0, y=0.0, hp=44.0, hp_max=100.0)
        world = SimpleNamespace(t=600.0, me=0, my_heroes=[hero], my_army=[], enemy_fighters=[], creeps=[], towns=[])
        planner = HP.HeroPlanner()
        old = T.HERO_RETREAT_HP
        try:
            T.HERO_RETREAT_HP = 0.50                       # 44% 血：算残血 -> 会退
            self.assertIn(planner.decide(world, home, hero, active=True).action, (HP.HOME, HP.WITHDRAW))
            T.HERO_RETREAT_HP = 0.30                       # 同一个英雄，血线调低 -> 不再脱离
            # ⚠ 期望是按权重算出来的、不是想出来的：44% 血仍低于 HERO_READY_HP(0.70)，
            #   所以得分最高的是 backline(+15) 而不是 fight(10-12=-2)。backline 不会把英雄从主力里摘出去，
            #   和 fight 一样属于"不退"。
            self.assertEqual(planner.decide(world, home, hero, active=True).action, HP.BACKLINE)
        finally:
            T.HERO_RETREAT_HP = old


if __name__ == "__main__":
    unittest.main()

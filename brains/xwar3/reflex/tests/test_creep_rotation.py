# -*- coding: utf-8 -*-
"""打野轮换：认不认得出"我们正在打野"。

09-09 打野时丝血单位常常还在硬扛，而旁边就有满血或血更多的单位；
      应当让残血的后撤一段距离，换别的单位顶上。

加了跳过理由计数之后，一分钟的心跳就给出了答案：
    没换的理由 没在打野×525、找不到正在挨打的×53、它已经在外面×5
    没换的理由 没在打野×593                      <- 整整一分钟，每一次采样
**「没在打野」占 78~100%**，轮换整条逻辑从来没启动过。

根因：判据取的是**我方全军的重心**，而 mine_engaged 包含待在家里的单位。
6 个人在 5000 外打野、10 个人在家，重心落在中间空地上，
那里 900 内一只野怪都没有 —— 于是"没在打野"。

**重心不是战斗发生的地方。**
"""
import math
import unittest

CREEP_OWNER = 12


class U:
    def __init__(self, x, y, owner=0, hp=100, type_='x'):
        self.x, self.y, self.owner, self.hp, self.type = x, y, owner, hp, type_


class Snap:
    def __init__(self, units):
        self.units = units


def old_centroid(snap, mine_engaged):
    """旧写法：按全军重心找野怪。"""
    if not mine_engaged:
        return []
    cx = sum(u.x for u in mine_engaged) / len(mine_engaged)
    cy = sum(u.y for u in mine_engaged) / len(mine_engaged)
    return [u for u in snap.units
            if u.owner == CREEP_OWNER and u.hp > 0
            and math.hypot(u.x - cx, u.y - cy) < 900]


def new_per_unit(snap, mine_engaged):
    """新写法：只要有任何一个自己人贴着某只野怪，那只就算参战。"""
    if not mine_engaged:
        return []
    creeps = [u for u in snap.units if u.owner == CREEP_OWNER and u.hp > 0]
    if not creeps:
        return []
    return [c for c in creeps
            if any(math.hypot(c.x - u.x, c.y - u.y) < 900 for u in mine_engaged)]


class CreepDetectTests(unittest.TestCase):
    def scene(self):
        """现场：6 个人在 (5000,0) 打野，10 个人在家 (0,0)，野怪在 (5100,0)。"""
        away = [U(5000, 0) for _ in range(6)]
        home = [U(0, 0) for _ in range(10)]
        creeps = [U(5100, 0, owner=CREEP_OWNER) for _ in range(4)]
        return Snap(away + home + creeps), away + home

    def test_old_misses_the_fight(self):
        """★ 旧写法在这个现场认不出打野 —— 这就是「没在打野×593」的来源。"""
        snap, mine = self.scene()
        self.assertEqual(len(old_centroid(snap, mine)), 0)

    def test_new_finds_the_fight(self):
        """★ 新写法认得出：有 6 个人就贴在野怪边上。"""
        snap, mine = self.scene()
        self.assertEqual(len(new_per_unit(snap, mine)), 4)

    def test_whole_army_out_both_agree(self):
        """全军都在打野时，两种写法结论一样 —— 说明改动只影响分兵的情形。"""
        away = [U(5000, 0) for _ in range(8)]
        creeps = [U(5100, 0, owner=CREEP_OWNER) for _ in range(3)]
        snap = Snap(away + creeps)
        self.assertEqual(len(old_centroid(snap, away)), 3)
        self.assertEqual(len(new_per_unit(snap, away)), 3)

    def test_far_creeps_not_counted(self):
        """地图另一头的野怪不算参战 —— 别把不相干的营地拉进来。"""
        away = [U(5000, 0) for _ in range(4)]
        far = [U(-8000, 0, owner=CREEP_OWNER)]
        snap = Snap(away + far)
        self.assertEqual(len(new_per_unit(snap, away)), 0)

    def test_dead_creeps_ignored(self):
        away = [U(5000, 0)]
        snap = Snap(away + [U(5100, 0, owner=CREEP_OWNER, hp=0)])
        self.assertEqual(len(new_per_unit(snap, away)), 0)

    def test_no_units_no_camp(self):
        snap = Snap([U(0, 0, owner=CREEP_OWNER)])
        self.assertEqual(len(new_per_unit(snap, [])), 0)

    def test_boundary_900(self):
        """边界：899 算参战，901 不算。"""
        me = [U(0, 0)]
        self.assertEqual(len(new_per_unit(Snap(me + [U(899, 0, owner=CREEP_OWNER)]), me)), 1)
        self.assertEqual(len(new_per_unit(Snap(me + [U(901, 0, owner=CREEP_OWNER)]), me)), 0)

    def test_source_uses_per_unit(self):
        """生产代码里确实换成了逐单位判（别让测试和代码分叉）。"""
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath('mod_combat.py').read_text(encoding='utf-8')
        self.assertIn('any(math.hypot(c.x - u.x, c.y - u.y) < 900 for u in mine_engaged)', src)
        # ⚠ 这里**不要**写 assertNotIn('cx = sum(...)') —— 今天第四次栽在同一个坑上：
        #   解释 bug 的注释里必然会引用那句旧写法，于是断言被自己的文档顶掉。
        #   （前三次：^ATTACK\s、过期的占位注释、"25 拍以前"那句日志。）
        #   **不要断言"某个字符串在文件里不存在"** —— 文件里有它可能只是有人在讲它。
        #   要断言的是**那一行代码还在不在函数体里**。
        import ast
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == 'creep_camp_near')
        # ⚠⚠ 文档字符串**也在函数体里**，ast.unparse 会把它一起吐出来 ——
        #   于是同一个坑在 AST 层面又咬了一次（文件文本 -> 函数文本，今天第五次）。
        #   **唯一稳的写法：把注释和 docstring 剔掉，只对真正的代码断言。**
        stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                                and isinstance(fn.body[0].value, ast.Constant)
                                and isinstance(fn.body[0].value.value, str)) else fn.body
        code = chr(10).join(ast.unparse(st) for st in stmts)
        self.assertNotIn('sum(u.x for u in mine_engaged)', code)
        self.assertIn('mine_engaged', code)


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""家门口的大仗不拉残血 —— 直接调 mod_combat.home_brawl，外加主循环里真的用上了它。

09-16 三场决定性守家战（170914 / 172402 / 173118）拉出来 16 / 9 / 11 个，录像段末尾活着 0 / 0 / 0。
"""
import ast
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, owner=0, hp=100, hp_max=400):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp_max, order=0, order_known=True)


HOME = (-5184.0, 2944.0)


class HomeBrawl(unittest.TestCase):
    def foes(self, n, x, y):
        return [unit(100 + i, "hfoo", x + (i % 3) * 40, y + (i // 3) * 40, owner=1) for i in range(n)]

    def test_big_fight_at_home_not_pulled(self):
        u = unit(1, "hsor", HOME[0] + 600, HOME[1])
        self.assertTrue(C.home_brawl(u, HOME, self.foes(C.HOME_BRAWL, HOME[0] + 700, HOME[1])))

    def test_hero_at_home_stays_and_fights_unless_focused(self):
        """★ 09-21 决策变更：敌军快把家拆完、其它单位都在守家时，英雄却躲在地图左上角。
        改为：没有敌方单位在打英雄时，英雄上去参战；只有多个单位同时在打它、
        且掉血很快时才考虑躲开。

        09-16 晚那版「英雄照拉」是按**英雄存活率**定的（被拉 10 活 3 vs 没拉 110 活 12），
        但那优化的不是「家保不保得住」—— 英雄活着而家被拆了，这局照样输。
        新规则：家门口的英雄**没被集火就留下打**；被集火（focus_n >= FOCUS_PULL_N）
        仍然走 focus 那条路拉走，09-16 的结论在那条路上照旧成立。
        """
        foes = self.foes(C.HOME_BRAWL, HOME[0] + 700, HOME[1])
        hero = unit(1, "Hamg", HOME[0] + 600, HOME[1], hp=100, hp_max=525)
        self.assertTrue(hero.is_hero)
        # 没被集火（默认 focus_n=0）-> 留下打
        self.assertTrue(C.home_brawl(hero, HOME, foes))
        # 被 FOCUS_PULL_N 个敌人集火 -> 交给 focus 那条路拉走，这里不再留人
        self.assertFalse(C.home_brawl(hero, HOME, foes, None, C.FOCUS_PULL_N))
        self.assertTrue(C.home_brawl(hero, HOME, foes, None, C.FOCUS_PULL_N - 1))
        # 同一位置的兵照旧按敌人数判（这条对兵没变）
        self.assertTrue(C.home_brawl(unit(2, "hsor", HOME[0] + 600, HOME[1]), HOME, foes))

    def test_hero_far_from_home_is_not_kept(self):
        """这条只管家门口。野外的英雄照旧由保命那几条路管 —— 别把「不躲」扩大到全图。"""
        far = (HOME[0] + 20000.0, HOME[1])
        foes = self.foes(C.HOME_BRAWL, far[0] + 100, far[1])
        hero = unit(1, "Hamg", far[0], far[1], hp=100, hp_max=525)
        self.assertFalse(C.home_brawl(hero, HOME, foes))

    def test_small_raid_at_home_still_pulled(self):
        u = unit(1, "hsor", HOME[0] + 600, HOME[1])
        self.assertFalse(C.home_brawl(u, HOME, self.foes(C.HOME_BRAWL - 1, HOME[0] + 700, HOME[1])))

    def test_big_fight_in_the_field_still_pulled(self):
        x, y = HOME[0] + C.HOME_HOLD + 100, HOME[1]
        self.assertFalse(C.home_brawl(unit(1, "hsor", x, y), HOME, self.foes(20, x + 100, y)))

    def test_far_foes_do_not_count(self):
        u = unit(1, "hgry", HOME[0], HOME[1])
        far = self.foes(20, HOME[0] + C.PULL_NEAR + 300, HOME[1])
        self.assertFalse(C.home_brawl(u, HOME, far))

    def test_unknown_home(self):
        self.assertFalse(C.home_brawl(unit(1, "hfoo", 0, 0), None, self.foes(20, 0, 0)))

    def test_main_loop_filters_danger_before_pull_picks(self):
        # 行为在主循环里（要真游戏），这里核对"先按 home_brawl 过滤、再 pull_picks"这个顺序在代码里
        src = open(os.path.join(HERE, "mod_combat.py"), encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "main")
        code = ast.unparse(fn)
        # ⚠ 别匹配整条调用的精确文本：加一个参数、换一次行这条测试就失配（09-21 加 focus_n 时踩到）。
        #   要守的是**顺序**这个语义 —— 先按 home_brawl 过滤 danger，再 pull_picks。
        i_filter = code.find("home_brawl(")
        i_pick = code.find("pull_picks(")
        self.assertGreater(i_filter, 0, "主循环里找不到 home_brawl 调用")
        self.assertGreater(i_pick, i_filter, "pull_picks 必须排在 home_brawl 过滤之后")


class Expansion(unittest.TestCase):
    """★ 09-16：分矿门口也是家门口；「家」只算建筑。
    210459 局 21:10:38 分矿守家战：打在 (-4248,-80) 附近、离主基地 (-5184,2944) 约 3100，10 秒拉出 15 个、活 0，战损 15:1。"""
    MAIN, EXPO = (-5184.0, 2944.0), (-4000.0, -400.0)

    def foes(self, n, x, y):
        return [unit(100 + i, "hfoo", x + (i % 3) * 40, y + (i // 3) * 40, owner=1) for i in range(n)]

    def test_expansion_brawl_not_pulled(self):
        u = unit(1, "hrif", -4248, -80)
        foes = self.foes(C.HOME_BRAWL, -4150, 0)
        self.assertTrue(C.home_brawl(u, self.MAIN, foes, [self.MAIN, self.EXPO]))
        # 对照：只给主基地那一个点（旧行为）就不认 —— 证明新判据是分矿救的
        self.assertFalse(C.home_brawl(u, self.MAIN, foes))

    def test_near_home_for_squad_retreat(self):
        self.assertTrue(C.near_home(-4200, -100, self.MAIN, [self.MAIN, self.EXPO]))
        self.assertFalse(C.near_home(0, 0, self.MAIN, [self.MAIN, self.EXPO]))
        self.assertTrue(C.near_home(-5000, 2900, self.MAIN, []))       # 拿不到大厅退回 home 点

    def test_destroyed_expansion_remains_home_for_current_battle(self):
        """1-093：二矿大厅先被拆，后续 20 秒团战仍应按守家处理。"""
        class Snap:
            pass
        memory = {}
        live, gone = Snap(), Snap()
        live.mine = [unit(1, "htow", *self.EXPO, hp=1500, hp_max=1500)]
        gone.mine = []
        self.assertEqual(C.recent_halls(memory, live, 10.0), [self.EXPO])
        self.assertEqual(C.recent_halls(memory, gone, 10.0 + C.HALL_MEMORY_S - 0.1), [self.EXPO])
        self.assertEqual(C.recent_halls(memory, gone, 10.0 + C.HALL_MEMORY_S), [])

    def test_catastrophic_home_fallback_is_behind_nearest_hall(self):
        # 敌人在分矿右侧，退点必须在分矿左后方；不能再朝所有建筑的重心跑进敌军。
        x, y = C.home_fallback_point(-3500, -400, self.MAIN, [self.MAIN, self.EXPO])
        self.assertLess(x, self.EXPO[0])
        self.assertAlmostEqual(y, self.EXPO[1])
        self.assertAlmostEqual(math.hypot(x-self.EXPO[0], y-self.EXPO[1]), C.HOME_FALLBACK)

    def test_catastrophic_home_retreat_has_its_own_longer_cooldown(self):
        self.assertTrue(C.squad_retreat_ready(7.0, 0.0, False, 0.0))
        self.assertFalse(C.squad_retreat_ready(7.0, 0.0, True, 0.0))
        self.assertTrue(C.squad_retreat_ready(C.HOME_BREAK_COOLDOWN + 0.1, 0.0, True, 0.0))

    def test_home_is_buildings_only(self):
        class Snap:
            pass
        snap = Snap()
        blds = [unit(1, "htow", -5184, 2944, hp=1500, hp_max=1500), unit(2, "hbar", -4800, 2600, hp=1500, hp_max=1500)]
        army = [unit(10 + i, "hfoo", 3000, 0, hp=420, hp_max=420) for i in range(20)]
        hero = [unit(40, "Hamg", 3000, 0, hp=625, hp_max=625)]
        snap.mine = blds + army + hero
        hx, hy = C.home_of(snap)
        self.assertAlmostEqual(hx, (-5184 - 4800) / 2)          # 20 个步兵和英雄在 (3000,0) 不把家拉走
        self.assertAlmostEqual(hy, (2944 + 2600) / 2)
        self.assertEqual(C.halls_of(snap), [(-5184.0, 2944.0)])


if __name__ == "__main__":
    unittest.main()

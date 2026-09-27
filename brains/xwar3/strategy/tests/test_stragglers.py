# -*- coding: utf-8 -*-
"""落单的兵要有人管（09-21 两局复盘）。

* 5-083 23 分左右：个别残血小兵留在敌方基地里不撤退回家，直到阵亡。
* 1-247 22 分钟：几个残血小兵不回家、一直停在野外，23:22 一堆敌军过来时又迎上去阵亡。

1-247 的确切现场：
    1302  敌强我弱，分段后撤并复评追兵：10/10 个
    1304  脱离接触，回队重整：6/6 个 -> (-5184,2944)      <- 只有 6 个，4 个掉队
    1311~1391（80 游戏秒）  一条令都没有
    1392  支援交战（敌 2 个，我方 2）·增援接战：6/6 个      <- 敌军找上门

根因在 `_step` 的最后一行：`if 主力重心离家 > 1500: 全军回家 else: []`。
**判据在量重心，而问题是个体** —— 重心回家了不等于每个人都回家了。

落单还有别的来源（5-083 里「援兵太少太远，去前沿集结点攒人：**1/1 个**」把单个兵送过去就不管了），
所以除了修这条判据，还有一层通用兜底：这一拍谁都没管、又离大部队很远的，一律归队。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain import unstick as UN  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HOME = (-5184.0, 2944.0)


def world(main_at=HOME, strays=(), foe_army=True):
    """主力在 main_at 成团；strays = 掉队单位的坐标列表。"""
    units = [U(1, "htow", *HOME), U(900, "unpl", 9000, 9000, owner=1)]
    units += [U(300 + i, "hpea", HOME[0], HOME[1]) for i in range(5)]
    units += [U(2, "Hamg", main_at[0], main_at[1])]
    units += [U(10 + i, "hfoo", main_at[0] + 60 * i, main_at[1] + 40) for i in range(8)]
    for i, (x, y) in enumerate(strays):
        units.append(U(50 + i, "hrif", x, y))
    if foe_army:                     # 对面家里有兵，否则 safe 为真会直接冲脸
        units += [U(800 + i, "ugho", 9000, 9000 + 80 * i, owner=1) for i in range(6)]
    return build_world(units, 0, 900.0, 300, 300, 40, 60, levels={2: 3})


class HomeStateCollectsStragglers(unittest.TestCase):
    """主力已经回到家里时，掉队的个别单位必须单独收回来。"""

    def test_stragglers_get_an_order_when_main_force_is_home(self):
        w = world(main_at=HOME, strays=[(3000.0, -3000.0), (3200.0, -2900.0)])
        s = T.AttackSequence(dict(PROFILE))
        s.state = "home"
        # 关掉出击和打野，否则这一拍会先进「向下一目标前沿集结」，走不到收掉队那一行
        s.allow_attack = s.allow_creep = False
        out = s.step(w, w.my_halls[0])
        back = [i for i in out if "掉队" in (i.why or "")]
        self.assertTrue(back, [i.why for i in out])
        self.assertEqual({u.addr for i in back for u in i.units}, {50, 51})
        self.assertEqual(s.counts.get("掉队归队"), 1)

    def test_no_order_when_everyone_is_together(self):
        """全员在一起时不该凭空多出一条令 —— 否则每拍都在刷。"""
        w = world(main_at=HOME, strays=[])
        s = T.AttackSequence(dict(PROFILE))
        s.state = "home"
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if "掉队" in (i.why or "")], [i.why for i in out])

    def test_boundary_is_computed(self):
        """边界按常量算、不凭感觉写：STRAGGLER_R 以内不算掉队。"""
        near = (HOME[0] + T.STRAGGLER_R - 200.0, HOME[1])
        w = world(main_at=HOME, strays=[near])
        s = T.AttackSequence(dict(PROFILE))
        s.state = "home"
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if "掉队" in (i.why or "")], [i.why for i in out])


class GeneralFallback(unittest.TestCase):
    """通用兜底：不管落单是从哪条路径来的，只要这一拍谁都没管就收回来。"""

    def setUp(self):
        self.sw = UN.StuckWatch()

    def test_uncovered_and_far_gets_collected(self):
        w = world(main_at=(0.0, 0.0), strays=[(6000.0, 6000.0)])
        main = [u for u in w.my_army if u.addr < 50]
        intents = [T.Intent("attack_move", main, 100.0, 100.0, why="出击")]
        out = self.sw.strays(w, intents, set(), 900.0)
        self.assertEqual(len(out), 1, [i.why for i in out])
        self.assertEqual({u.addr for u in out[0].units}, {50})
        self.assertEqual(self.sw.counts.get("落单兜底·收回来"), 1)

    def test_covered_units_are_left_alone(self):
        """已经有任务的不碰 —— 兜底不抢活。"""
        w = world(main_at=(0.0, 0.0), strays=[(6000.0, 6000.0)])
        stray = [u for u in w.my_army if u.addr == 50]
        intents = [T.Intent("move", stray, 0.0, 0.0, why="别的任务")]
        self.assertFalse(self.sw.strays(w, intents, set(), 900.0))

    def test_micro_layer_units_are_left_alone(self):
        """毫秒层正在救的不碰 —— 抢了会把保命令覆盖掉（老架构两层互抢的老问题）。"""
        w = world(main_at=(0.0, 0.0), strays=[(6000.0, 6000.0)])
        main = [u for u in w.my_army if u.addr < 50]
        intents = [T.Intent("attack_move", main, 100.0, 100.0, why="出击")]
        self.assertFalse(self.sw.strays(w, intents, {50}, 900.0))

    def test_two_strays_alone_still_go_home(self):
        """主力没了、只剩两个落单的兵：它们会互相聚成一个"团"，
        如果拿这个团当归队点，两个人就永远站在原地 —— 正是 1-247 里停在野外不动的现象。
        最大的团凑不够 MAIN_MIN 个人时，归队点必须是家。"""
        units = [U(1, "htow", *HOME), U(900, "unpl", 9000, 9000, owner=1)]
        units += [U(50, "hrif", 6000.0, 6000.0), U(51, "hrif", 6100.0, 6050.0)]
        w = build_world(units, 0, 900.0, 300, 300, 10, 20)
        out = self.sw.strays(w, [], set(), 900.0)
        self.assertEqual(len(out), 1, [i.why for i in out])
        self.assertEqual({u.addr for u in out[0].units}, {50, 51})
        self.assertAlmostEqual(out[0].x, HOME[0], delta=1.0)
        self.assertAlmostEqual(out[0].y, HOME[1], delta=1.0)

    def test_near_units_are_not_collected(self):
        w = world(main_at=(0.0, 0.0), strays=[(UN.STRAY_R - 300.0, 0.0)])
        main = [u for u in w.my_army if u.addr < 50]
        intents = [T.Intent("attack_move", main, 100.0, 100.0, why="出击")]
        self.assertFalse(self.sw.strays(w, intents, set(), 900.0))
        self.assertEqual(self.sw.counts.get("落单兜底·没人需要收"), 1)


if __name__ == "__main__":
    unittest.main()

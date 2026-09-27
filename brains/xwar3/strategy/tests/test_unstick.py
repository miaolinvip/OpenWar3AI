# -*- coding: utf-8 -*-
"""英雄被自己人堵住 -> 强制让路（09-21 11 分左右大法师被一个农民堵在家里出不去。
这种情况直接强制附近单位让开：英雄的优先级最高）。

真实失败形状（1-232 局 681~741 游戏秒）：681 秒下完「英雄先手拉出营地」后 60 游戏秒没有任何新动作，
到 741 秒才出现「英雄看戏：Hmkg 775/775 没订单没任务」。
被堵住的英雄在引擎里**有订单**（还在试图走），于是每拍都命中 act._point 的「同令不重下」——
既不重下令也没有任何计数会变。所以这里的判据是**位移**，不是订单。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import unstick as UN  # noqa: E402
from brain.tactics import Intent  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HOME = (-5184.0, 2944.0)
GOAL = (-3128.0, -3751.0)          # 232 局那条「英雄先手拉出营地」的目标点


def world_at(t, hero_xy, peasants, foes=(), extra=()):
    """英雄在家门口，身边几个农民；默认视野里没有敌人（不是在打架）。"""
    units = [U(0x100, "htow", *HOME), U(0x900, "hcas", 4000, -3000, owner=1)]
    units += [U(0x2, "Hamg", hero_xy[0], hero_xy[1], order=0x000D0012, has_task=True)]
    units += [U(0x300 + i, "hpea", px, py, order=0x000D0032, has_task=True)
              for i, (px, py) in enumerate(peasants)]
    units += [U(0x700 + i, "hfoo", fx, fy, owner=1) for i, (fx, fy) in enumerate(foes)]
    units += list(extra)
    return build_world(units, 0, float(t), 500, 300, 20, 40)


def march(w):
    """本拍给英雄的任务：走到 GOAL。"""
    hero = w.my_heroes[0]
    return [Intent("move", [hero], GOAL[0], GOAL[1], why="打野·英雄先手拉出营地")]


def run(sw, ticks, hero_xy, peasants, foes=(), protected=None, t0=600.0, dt=2.0):
    """驱动多拍 —— 卡住是「连续几拍都没挪窝」，单张快照判不出来。"""
    out, forget = [], []
    for i in range(ticks):
        w = world_at(t0 + i * dt, hero_xy, peasants, foes)
        o, f = sw.step(w, march(w), protected=protected or set(), t=w.t)
        out += o          # 让路只发生在某一拍（之后进冷却），要把整段的输出累起来看
        forget += f
    return out, forget


class SidePoint(unittest.TestCase):
    def test_pushes_sideways_not_away(self):
        """推远没用：英雄要往前走，挡路的人被推到前面还是挡着。必须往垂直方向让。"""
        hx, hy = 0.0, 0.0
        tx, ty = 1000.0, 0.0                       # 英雄要往 +x 走
        bx, by = 200.0, 30.0                       # 农民挡在正前方偏上
        nx, ny = UN.side_point(hx, hy, tx, ty, bx, by, 380.0)
        self.assertAlmostEqual(nx, bx, delta=1.0)  # 前后不动
        self.assertGreater(ny - by, 300.0)         # 往侧面让开
        # 正好站在连线上的两个人要分到两边，不能一起往同一边挤
        a = UN.side_point(hx, hy, tx, ty, 200.0, 0.0, 380.0, bias=0)
        b = UN.side_point(hx, hy, tx, ty, 200.0, 0.0, 380.0, bias=1)
        self.assertGreater(a[1], 0)
        self.assertLess(b[1], 0)


class HeroStuck(unittest.TestCase):
    def test_peasant_in_the_way_gets_moved(self):
        sw = UN.StuckWatch()
        out, forget = run(sw, 5, (-5100.0, 2800.0), [(-5150.0, 2700.0)])
        clear = [i for i in out if i.why == "给英雄让路"]
        self.assertEqual(len(clear), 1, [i.why for i in out])
        self.assertEqual(clear[0].units[0].type, "hpea")
        self.assertEqual(sw.counts.get("英雄被堵·让路"), 1)
        self.assertIn(0x2, forget)                                 # 英雄的去重缓存要清掉，否则永远不重下令
        # 让出来的位置要偏离「英雄->目标」这条线，而不是顺着它推
        hx, hy = -5100.0, 2800.0
        ux, uy = GOAL[0] - hx, GOAL[1] - hy
        d = math.hypot(ux, uy)
        ux, uy = ux / d, uy / d
        b = clear[0]
        moved_along = (b.x - (-5150.0)) * ux + (b.y - 2700.0) * uy
        moved_side = abs((b.x - (-5150.0)) * -uy + (b.y - 2700.0) * ux)
        self.assertGreater(moved_side, 300.0)
        self.assertLess(abs(moved_along), 40.0)

    def test_moving_hero_is_not_stuck(self):
        """英雄在走就不该有任何动作 —— 这条守着「让路」不会在正常行军里乱开火。"""
        sw = UN.StuckWatch()
        out = []
        for i in range(6):
            x = -5100.0 + i * 400.0                                # 每拍走 400
            w = world_at(600.0 + i * 2.0, (x, 2800.0), [(x - 50.0, 2700.0)])
            out, _ = sw.step(w, march(w), t=w.t)
            self.assertFalse(out, [i.why for i in out])
        self.assertEqual(sw.counts.get("英雄被堵·让路"), None)

    def test_standing_in_a_fight_is_not_stuck(self):
        """交战时站着输出是对的，不能把打架判成卡住（否则一开打就把前排调走）。"""
        sw = UN.StuckWatch()
        out, _ = run(sw, 5, (-5100.0, 2800.0), [(-5150.0, 2700.0)], foes=[(-5000.0, 2750.0)])
        self.assertFalse([i for i in out if i.why == "给英雄让路"])
        self.assertGreaterEqual(sw.counts.get("英雄没挪窝·在交战中", 0), 1)

    def test_arrived_is_not_stuck(self):
        sw = UN.StuckWatch()
        out, _ = run(sw, 5, (GOAL[0] + 80.0, GOAL[1] + 60.0), [(GOAL[0], GOAL[1] + 120.0)])
        self.assertFalse(out, [i.why for i in out])

    def test_no_task_this_tick_is_not_stuck(self):
        """这一拍没给英雄任务 = 我们让它站着，不算卡住。"""
        sw = UN.StuckWatch()
        for i in range(5):
            w = world_at(600.0 + i * 2.0, (-5100.0, 2800.0), [(-5150.0, 2700.0)])
            out, _ = sw.step(w, [], t=w.t)
        self.assertFalse(out)
        self.assertGreaterEqual(sw.counts.get("英雄没挪窝·这一拍没给它任务", 0), 1)

    def test_protected_builder_is_not_pulled(self):
        """正在盖楼/修楼的农民不能被调走（调走等于拆自己的建造流程）。"""
        sw = UN.StuckWatch()
        out, _ = run(sw, 5, (-5100.0, 2800.0), [(-5150.0, 2700.0)], protected={0x300})
        self.assertFalse([i for i in out if i.why == "给英雄让路"])
        self.assertGreaterEqual(sw.counts.get("英雄卡住·身边没人可让", 0), 1)

    def test_escalates_to_hero_sidestep(self):
        """让过路还卡着 -> 英雄自己错身，打断引擎那条走不通的路径。"""
        sw = UN.StuckWatch()
        out, forget = run(sw, 8, (-5100.0, 2800.0), [(-5150.0, 2700.0)])
        step = [i for i in out if i.why.startswith("卡太久")]
        self.assertEqual(len(step), 1, [i.why for i in out])
        self.assertEqual(step[0].units[0].addr, 0x2)
        self.assertEqual(sw.counts.get("英雄卡住·自己错身"), 1)
        self.assertGreater(math.hypot(step[0].x + 5100.0, step[0].y - 2800.0), 300.0)

    def test_clear_has_cooldown(self):
        """同一个英雄不能每拍都把身边的人推一次 —— 农民会在原地抖动，永远回不去干活。"""
        sw = UN.StuckWatch()
        run(sw, 5, (-5100.0, 2800.0), [(-5150.0, 2700.0)])
        self.assertEqual(sw.counts.get("英雄被堵·让路"), 1)
        run(sw, 1, (-5100.0, 2800.0), [(-5150.0, 2700.0)], t0=602.0)
        self.assertEqual(sw.counts.get("英雄被堵·让路"), 1)       # 冷却内不重复
        run(sw, 1, (-5100.0, 2800.0), [(-5150.0, 2700.0)], t0=640.0)
        self.assertEqual(sw.counts.get("英雄被堵·让路"), 2)       # 冷却过了才再来一次

    def test_only_nearby_units_are_moved(self):
        """让路半径之外的人不动，否则一次会把整个基地的农民调走。"""
        sw = UN.StuckWatch()
        far = (-5100.0 + UN.CLEAR_R + 200.0, 2800.0)
        out, _ = run(sw, 5, (-5100.0, 2800.0), [(-5150.0, 2700.0), far])
        clear = [i for i in out if i.why == "给英雄让路"]
        self.assertEqual(len(clear), 1)
        self.assertEqual(clear[0].units[0].addr, 0x300)

    def test_dead_hero_is_forgotten(self):
        """英雄死了要清掉锚点，否则复活之后「上一条命卡了多久」会被继承。"""
        sw = UN.StuckWatch()
        run(sw, 3, (-5100.0, 2800.0), [(-5150.0, 2700.0)])
        self.assertIn(0x2, sw.anchors)
        w = build_world([U(0x100, "htow", *HOME)], 0, 640.0, 500, 300, 20, 40)
        sw.step(w, [], t=w.t)
        self.assertNotIn(0x2, sw.anchors)


if __name__ == "__main__":
    unittest.main()

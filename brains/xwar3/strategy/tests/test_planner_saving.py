# -*- coding: utf-8 -*-
"""建造规划器的「攒钱」：只为**缺的那种资源**停，不缺那种资源的照样能出。

★★ 09-21 1-262 局：金 3000、木 30，**整整 7 分钟一个农民都没造**。
规划器原来是「钱不够就 break，后面优先级低的一律不花钱」（AMAI OneBuildLoopAM）。
主城升级 / 第二座大厅因为**木头**不够停下，后面**只要金子**的农民（优先级 60）也跟着一个不造 ——
而木头少正是因为砍树的农民不够。死锁。
AMAI 攒钱的本意是「别让低优先级的把高优先级要的钱花掉」：停下那一项要的金和木先留住，
不需要所缺那种资源的东西放过去，一分都碰不到它要攒的。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain.build import Planner, Want  # noqa: E402

COST = {"hkee": (320, 210), "hpea": (75, 0), "hfoo": (135, 0), "hrif": (205, 30), "hhou": (80, 20), "Rhme": (100, 50)}


def plan(wants, gold, lumber, planner=None):
    p = planner or Planner()
    return p, p.plan(wants, have=lambda w: 0, gold=gold, lumber=lumber, food_free=50,
                     check=lambda w: None, cost=lambda w: COST[w.code])


class SavingOnlyBlocksTheShortResource(unittest.TestCase):
    def test_the_1_262_deadlock_peasants_still_train_while_saving_wood(self):
        wants = [Want("upgrade_hall", "hkee", 1, 67), Want("unit", "hpea", 3, 60)]
        _p, r = plan(wants, gold=3000, lumber=30)
        self.assertEqual(r.stopped_at.code, "hkee")
        self.assertEqual(r.short, "lumber")
        self.assertEqual([d.want.code for d in r.decisions], ["hpea"], "只要金子的农民必须能过去")
        self.assertEqual([w.code for w in r.through], ["hpea"])

    def test_things_that_need_the_short_resource_still_wait(self):
        """攒木头的时候，要木头的（火枪手、农场）不许插队 —— 攒钱的意义就在这里。"""
        wants = [Want("upgrade_hall", "hkee", 1, 67), Want("unit", "hrif", 1, 60), Want("building", "hhou", 1, 55)]
        _p, r = plan(wants, gold=3000, lumber=30)
        self.assertEqual(r.decisions, [])
        self.assertEqual({w.code for w, why in r.skipped if "不插队" in why}, {"hrif", "hhou"})

    def test_the_saved_gold_is_not_touched(self):
        """停下那一项要的 320 金先留住：后面放过去的只能花剩下的。

        ⚠ 场景要能**区分**留没留：470 金不留的话农民 75 + 步兵 135 都出得起；
          留住 320 只剩 150，只够农民。（第一版写成「一个 Want 要 10 个农民、期望花 150」——
          规划器一轮每个 Want 只出一条决定，那个期望是猜的。）"""
        wants = [Want("upgrade_hall", "hkee", 1, 67), Want("unit", "hpea", 1, 60), Want("unit", "hfoo", 1, 55)]
        _p, r = plan(wants, gold=320 + 150, lumber=30)
        self.assertEqual([d.want.code for d in r.decisions], ["hpea"])
        self.assertIn(("hfoo", "攒钱中，剩下的钱也不够"), [(w.code, why) for w, why in r.skipped])

    def test_short_on_gold_blocks_everything_that_costs_gold(self):
        wants = [Want("upgrade_hall", "hkee", 1, 67), Want("unit", "hpea", 3, 60)]
        _p, r = plan(wants, gold=100, lumber=500)
        self.assertEqual(r.short, "gold")
        self.assertEqual(r.decisions, [])

    def test_upgrades_still_do_not_block_as_before(self):
        wants = [Want("upgrade", "Rhme", 1, 70), Want("unit", "hfoo", 1, 60)]
        _p, r = plan(wants, gold=150, lumber=10)
        self.assertIsNone(r.stopped_at)
        self.assertEqual([d.want.code for d in r.decisions], ["hfoo"])

    def test_items_after_the_stop_do_not_age(self):
        """原来 break 之后根本走不到它们、也就不涨优先级；现在走到了也不能涨，
        否则排在后面的会慢慢反超停下的那一项，改掉原来的顺序。"""
        wants = [Want("upgrade_hall", "hkee", 1, 67), Want("unit", "hrif", 1, 60)]
        p = Planner()
        for _ in range(30):
            plan(wants, gold=3000, lumber=30, planner=p)
        self.assertNotIn("unit:hrif:", p.aging)
        self.assertGreater(p.aging.get("upgrade_hall:hkee:", 0), 0)


if __name__ == "__main__":
    unittest.main()

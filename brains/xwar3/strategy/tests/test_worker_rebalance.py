# -*- coding: utf-8 -*-
"""采金 / 伐木之间调人（runtime._rebalance），按**现场**数、真的下令、以伐木目标为唯一仲裁。

★★ 09-21 1-262 局：木头已经不够用却不补农民，一个农民在采集、另一个闲着；
新运营层的决策效果反而不如旧设计。

那一局：金 3000、木 30 整整 7 分钟。上一版的调人写坏了：
  * 反方向「放 1 个采金的去砍树」**只删名单、没下令**，刷了 20 多次，木头一点没涨；
  * 名单空了（「伐木·缺人·采金名单0人」累计 169 次）矿就看起来缺人，正方向又去伐木队抢人，
    伐木被抢到只剩 1 个。
这类 bug 只在**连续几拍**里出现（静态快照测不出来），所以下面有驱动多拍的测试。
"""
import os
import sys
import unittest
from collections import Counter
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT        # noqa: E402
from brain.act import HARVEST_ORDERS   # noqa: E402

HARVEST = HARVEST_ORDERS[0]
MINE = SimpleNamespace(addr=700, type="ngol", x=0.0, y=0.0, hp=1.0, hp_max=1.0, handle_lo=700, handle_hi=0)
HALL = SimpleNamespace(addr=1, type="htow", x=0.0, y=-600.0)
TREES = [{"addr": 900 + i, "x": 1500.0, "y": 60.0 * i} for i in range(10)]


class FakeAct:
    """记下每一条令；ok=False 模拟引擎拒绝。"""

    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def gather(self, u, target):
        self.calls.append((u.addr, target.addr))
        return self.ok

    def scan_trees(self, x, y):
        return list(TREES)


def miner(addr):         # 在矿边采金
    return SimpleNamespace(addr=addr, type="hpea", x=100.0, y=0.0, hp=220.0, hp_max=220.0, order=HARVEST, has_task=True)


def chopper(addr):       # 在树边砍树
    return SimpleNamespace(addr=addr, type="hpea", x=1480.0, y=0.0, hp=220.0, hp_max=220.0, order=HARVEST, has_task=True)


def brain():
    b = RT.Brain.__new__(RT.Brain)
    b.counts = Counter()
    b.profile = dict(peons_per_mine=5, peons_lumber=5, lumber_high=400)
    b.lumber_crew, b.gold_crew, b.gold_attempts = {}, {}, {}
    b.sent_at, b.mine_full_until = {}, {}
    b.rebalance_at = -1e9
    b.trees, b.trees_at = list(TREES), 0.0
    b.act = FakeAct()
    b.notes = []
    b.note = lambda t, s: b.notes.append(s)
    b.config = {"enable_coach": False}          # 纯规则那条路：伐木目标只由建造表算
    b.coach = RT.CH.Coach()
    return b


def world(workers, gold=500, lumber=200, t=600.0):
    return SimpleNamespace(t=t, my_workers=workers, gold=gold, lumber=lumber, main_town=lambda: HALL)


def apply_moves(b, workers):
    """把这一拍下的令落到「现场」：被派去砍树的走到树边，被派回矿的走到矿边。"""
    tree_ids = {t["addr"] for t in TREES}
    by = {u.addr: u for u in workers}
    for addr, target in b.act.calls:
        u = by[addr]
        u.x, u.y = (1480.0, 0.0) if target in tree_ids else (100.0, 0.0)
    b.act.calls.clear()


class WoodShortPullsFromTheMine(unittest.TestCase):
    def test_the_1_262_case_really_orders_someone_to_chop(self):
        """金 3000、木 30、矿上 5 个、砍树 1 个：必须**真的**派一个人去砍树，名单跟着令走。"""
        ws = [miner(10 + i) for i in range(5)] + [chopper(20)]
        b = brain()
        b._rebalance(world(ws, gold=3000, lumber=30), [MINE])
        self.assertEqual(len(b.act.calls), 1, "上一版在这里只删名单不下令 —— 必须真的有一条砍树令")
        addr, target = b.act.calls[0]
        self.assertIn(target, {t["addr"] for t in TREES})
        self.assertIn(addr, b.lumber_crew)
        self.assertNotIn(addr, b.gold_crew)
        self.assertTrue(any(k.startswith("伐木·从采金调人") for k in b.counts), dict(b.counts))

    def test_empty_ledgers_do_not_hide_the_miners(self):
        """开局自动采矿的农民不是大脑派的，名单是空的 —— 按现场数，照样调得动。"""
        ws = [miner(10 + i) for i in range(5)]
        b = brain()
        self.assertEqual((b.gold_crew, b.lumber_crew), ({}, {}))
        b._rebalance(world(ws, gold=3000, lumber=30), [MINE])
        self.assertEqual(len(b.act.calls), 1)

    def test_wood_first_even_when_the_mine_is_not_full(self):
        """金多木少时木头优先：矿上 4 个（没满）也照样调人去砍树。"""
        ws = [miner(10 + i) for i in range(4)] + [chopper(20)]
        b = brain()
        b._rebalance(world(ws, gold=3000, lumber=30), [MINE])
        self.assertEqual(len(b.act.calls), 1)
        self.assertEqual(b.counts.get("伐木·从采金调人·缺木头优先"), 1, dict(b.counts))

    def test_the_mine_keeps_a_floor(self):
        ws = [miner(10 + i) for i in range(RT.GOLD_KEEP)]
        b = brain()
        b._rebalance(world(ws, gold=3000, lumber=30), [MINE])
        self.assertFalse(b.act.calls, "采金只剩地板那几个时不许再调")


class GoldOnlyTakesSurplusLumberjacks(unittest.TestCase):
    def test_surplus_lumberjack_goes_back_to_the_mine(self):
        """木头够了（不缺）、砍树的多于目标、矿上有空位 -> 调一个回矿。"""
        ws = [miner(10 + i) for i in range(3)] + [chopper(20 + i) for i in range(8)]
        b = brain()
        b._rebalance(world(ws, gold=300, lumber=300), [MINE])      # 伐木目标 5，砍树 8
        self.assertEqual(b.counts.get("采金·从伐木调人"), 1, dict(b.counts))
        self.assertEqual(b.act.calls[0][1], MINE.addr)

    def test_never_steals_below_the_lumber_target(self):
        """上一版会把伐木抢到只剩 1 个。现在砍树的不多于目标就一个都不动。"""
        ws = [miner(10 + i) for i in range(3)] + [chopper(20 + i) for i in range(5)]
        b = brain()
        b._rebalance(world(ws, gold=300, lumber=300), [MINE])      # 伐木目标 5，砍树 5
        self.assertFalse(b.act.calls)
        self.assertEqual(b.counts.get("采金·缺人·砍树的不多余"), 1, dict(b.counts))

    def test_rejected_order_is_counted_separately(self):
        ws = [miner(10 + i) for i in range(5)] + [chopper(20)]
        b = brain()
        b.act = FakeAct(ok=False)
        b._rebalance(world(ws, gold=3000, lumber=30), [MINE])
        self.assertEqual(b.counts.get("伐木·从采金调人·被拒"), 1, dict(b.counts))
        self.assertFalse(b.lumber_crew, "被拒了名单不能动")


class ManyTicksNoPingPong(unittest.TestCase):
    """★ 这条才是上一版真正栽的地方：单拍看每一步都对，连起来两个方向互相抢。"""

    def test_wood_short_converges_to_the_target_and_never_pulls_back(self):
        ws = [miner(10 + i) for i in range(10)] + [chopper(30)]
        b = brain()
        target = None
        for k in range(40):
            w = world(ws, gold=3000, lumber=30, t=600.0 + k * RT.REBALANCE_EVERY)
            target = b.lumber_target(w)
            b._rebalance(w, [MINE])
            apply_moves(b, ws)
        wood = len(b.census(world(ws), [MINE])[2])
        self.assertEqual(b.counts.get("采金·从伐木调人", 0), 0, "缺木头时一次都不许往回调")
        # ⚠ 期望按公式算：砍树最多 = min(伐木目标, 农民总数 - 采金地板)。
        #   第一版写成 `== target`，11 个农民、目标 10、采金留 2 -> 实际上限是 9（挂的是我的期望）。
        expect = min(target, len(ws) - RT.GOLD_KEEP)
        self.assertEqual(wood, expect, f"砍树 {wood} 人，目标 {target}，上限 {expect}")

    def test_wood_rich_converges_back_to_the_mine_and_stops(self):
        ws = [miner(10 + i) for i in range(2)] + [chopper(30 + i) for i in range(9)]
        b = brain()
        for k in range(40):
            w = world(ws, gold=300, lumber=300, t=600.0 + k * RT.REBALANCE_EVERY)
            b._rebalance(w, [MINE])
            apply_moves(b, ws)
        by_mine, _g, wood = b.census(world(ws), [MINE])
        self.assertEqual(by_mine[MINE.addr], 5, "矿应该补满到 5")
        self.assertGreaterEqual(len(wood), b.lumber_target(world(ws, gold=300, lumber=300)))
        self.assertFalse(any(k.startswith("伐木·从采金调人") for k in b.counts), "木头不缺时不该往伐木调")


class CoachCannotUndercutTheRule(unittest.TestCase):
    def test_lower_advice_is_ignored_higher_is_taken(self):
        """1-262：规则要 10 个人砍树（缺木头 +5），顾问一律说 3~4 个。顾问只许往上加。"""
        b = brain()
        b.config = {"enable_coach": True}
        # 农民给够（20 个），让「农民总数 - 采金地板」这道钳位不起作用，只看「顾问 vs 规则」
        w = world([miner(i) for i in range(20)], gold=3000, lumber=30)
        rule = RT.lumber_crew(w, dict(b.profile))
        b.coach._plan = b.coach.clamp({"workers": {"gold": 5, "lumber": 3}}, w.t)
        b.coach.enabled = True
        self.assertEqual(b.lumber_target(w), rule)
        self.assertEqual(b.counts.get("顾问·伐木人数·比规则少·不采纳"), 1)
        b.coach._plan = b.coach.clamp({"workers": {"gold": 5, "lumber": rule + 1}}, w.t)
        self.assertEqual(b.lumber_target(w), rule + 1)


if __name__ == "__main__":
    unittest.main()

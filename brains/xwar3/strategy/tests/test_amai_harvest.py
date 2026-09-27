# -*- coding: utf-8 -*-
"""采金 / 伐木派工照 AMAI universal_peon_assignment（brain/harvest.py + runtime._amai_harvest）。

★ 09-21 大模型派工反复失败、没有效果，改回非 LLM 模式，照 AMAI 的方式派工。

原来的派工「只从闲人里拿人」，全在采集时谁也调不动谁，于是一层层打补丁（伐木人数、缺木头多派、互相调人），
09-21 一天里其中两条出了 bug（1-262：金 3000、木 30 整整 7 分钟）。
AMAI 每一轮对**所有**农民按同一张优先级表算：谁该在哪只有一个答案，两个方向不可能互相抢。
"""
import os
import sys
import unittest
from collections import Counter
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import harvest as HV          # noqa: E402
from brain import runtime as RT          # noqa: E402
from brain.act import HARVEST_ORDERS     # noqa: E402

HARVEST = HARVEST_ORDERS[0]


class TableMatchesTheAmaiSource(unittest.TestCase):
    """期望值逐条对着 third_party/AMAI/common.eai:14605 算，不是凭印象写。"""

    def test_order_main_mine_first_then_wood(self):
        t = HV.slots(500, 500, ["main"])
        self.assertEqual(t[:4], [("gold", "main")] * 4)
        self.assertEqual(t[4], ("wood", None))
        self.assertEqual(t[5], ("gold", "main"))
        self.assertEqual(t[6:8], [("wood", None)] * 2)       # pwh

    def test_pwh_is_always_two_like_the_source(self):
        """源码里 `g - w > 300` 这一档把后面 500 / 900 两档截住了，pwh 实际恒为 2。照搬实际行为。"""
        for g, w in ((3000, 30), (900, 50), (100, 100)):
            gold_first, wood = HV.desired(HV.slots(g, w, ["m"]), 8)
            self.assertEqual(wood, 3, f"金 {g} 木 {w}：前 8 个位里伐木是 1 + pwh 2")

    def test_secondary_wood_table(self):
        # 木 < 200 -> 6 - pwh = 4
        self.assertEqual(HV.secondary_wood(3000, 30), 4)
        # 否则按金 / 木比
        for g, w, expect in ((200, 1000, 0), (700, 1000, 0), (1000, 1000, 0), (1300, 1000, 1),
                             (1600, 1000, 2), (1900, 1000, 3), (3000, 1000, 4)):
            self.assertEqual(HV.secondary_wood(g, w), expect, f"金 {g} 木 {w}")

    def test_expansions_get_five_unless_gold_rich(self):
        t = HV.slots(500, 500, ["main", "exp"])
        self.assertEqual(Counter(a for k, a in t if k == "gold")["exp"], 5)
        rich = HV.slots(3000, 300, ["main", "exp"])           # 金 / 木 = 10 >= 3、金 >= 300
        self.assertEqual(Counter(a for k, a in rich if k == "gold")["exp"], 3)

    def test_the_1_262_economy(self):
        """金 3000、木 30、12 个农民、一座矿：AMAI 要 5 采金、7 砍木（1 + pwh 2 + swh 4）。
        那一局实际是 10 采金、1~2 砍木。"""
        gold, wood = HV.desired(HV.slots(3000, 30, ["main"]), 12)
        self.assertEqual((gold["main"], wood), (5, 7))

    def test_few_peasants_go_gold_first(self):
        gold, wood = HV.desired(HV.slots(500, 150, ["main"]), 4)
        self.assertEqual((gold["main"], wood), (4, 0))
        gold, wood = HV.desired(HV.slots(500, 150, ["main"]), 6)
        self.assertEqual((gold["main"], wood), (5, 1))


# ---------------------------------------------------------------- runtime._amai_harvest 多拍驱动

MINE = SimpleNamespace(addr=700, type="ngol", x=0.0, y=0.0, hp=1.0, hp_max=1.0, handle_lo=700, handle_hi=0)
HALL = SimpleNamespace(addr=1, type="htow", x=0.0, y=-600.0)
TREES = [{"addr": 900 + i, "x": 1500.0, "y": 60.0 * i} for i in range(10)]


class FakeAct:
    def __init__(self):
        self.calls = []

    def gather(self, u, target):
        self.calls.append((u.addr, target.addr))
        return True

    def scan_trees(self, x, y):
        return list(TREES)


def peasant(addr, where):
    x, order = {"gold": (100.0, HARVEST), "wood": (1480.0, HARVEST), "idle": (300.0, 0)}[where]
    return SimpleNamespace(addr=addr, type="hpea", x=x, y=0.0, hp=220.0, hp_max=220.0, order=order,
                           order_known=True, has_task=(where != "idle"))


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
    b.pending = []
    b.powerbuild = SimpleNamespace(busy_addrs=set())
    b.repairs = SimpleNamespace(busy_addrs=set())
    b.harass = SimpleNamespace(busy_addrs=set())
    b.militia_creep_ids, b.militia_expand_builders = set(), set()
    b.militia_expand_until = -1e9
    b.harvest_plan = {}
    return b


def world(ws, gold, lumber, t):
    return SimpleNamespace(t=t, my_workers=ws, gold=gold, lumber=lumber, my_heroes=[], main_town=lambda: HALL)


def settle(b, ws):
    """这一拍下的令落到现场：被派去砍树的走到树边，被派去挖金的走到矿边。"""
    tree_ids = {t["addr"] for t in TREES}
    by = {u.addr: u for u in ws}
    for addr, target in b.act.calls:
        u = by[addr]
        u.x, u.order, u.has_task = (1480.0 if target in tree_ids else 100.0), HARVEST, True
    b.act.calls.clear()


class DrivenOverManyTicks(unittest.TestCase):
    def run_ticks(self, ws, gold, lumber, ticks=40):
        b = brain()
        moves = Counter()
        for k in range(ticks):
            b._amai_harvest(world(ws, gold, lumber, 600.0 + k * RT.REBALANCE_EVERY), [MINE])
            for addr, target in b.act.calls:
                moves["wood" if target != MINE.addr else "gold"] += 1
            settle(b, ws)
        return b, moves

    def test_the_1_262_case_converges_to_the_amai_split(self):
        """1-262：10 挖金 + 1 砍木 + 1 闲着，金 3000 木 30。收敛到 AMAI 的 5 金 7 木，且一次都不往回调。"""
        ws = [peasant(10 + i, "gold") for i in range(10)] + [peasant(30, "wood"), peasant(31, "idle")]
        b, moves = self.run_ticks(ws, 3000, 30)
        by_mine, _g, wood = b.census(world(ws, 3000, 30, 0), [MINE])
        self.assertEqual((by_mine[MINE.addr], len(wood)), (5, 7))
        self.assertEqual(moves["gold"], 0, "缺木头时不许把人从树边调回矿上 —— 不能再有 1-262 那种互抢")

    def test_idle_peasants_are_used_right_away(self):
        """对局里看到一个农民在采集、另一个闲着。闲人第一拍就按表派出去，不等冷却。"""
        ws = [peasant(10 + i, "idle") for i in range(8)]
        b = brain()
        b._amai_harvest(world(ws, 500, 500, 600.0), [MINE])
        self.assertEqual(len(b.act.calls), 8, "8 个闲人一个都不能留")
        gold = sum(1 for _a, t in b.act.calls if t == MINE.addr)
        self.assertEqual(gold, 5)

    def test_rich_wood_moves_surplus_back_to_the_mine(self):
        """木头富余（金 / 木 < 1.1 -> swh 0）：伐木只要 1 + pwh = 3，多出来的回矿补满。"""
        ws = [peasant(10 + i, "gold") for i in range(2)] + [peasant(30 + i, "wood") for i in range(8)]
        b, moves = self.run_ticks(ws, 300, 1500)
        by_mine, _g, wood = b.census(world(ws, 300, 1500, 0), [MINE])
        self.assertEqual(by_mine[MINE.addr], 5)
        self.assertEqual(moves["wood"], 0, "木头富余时不该往伐木调")

    def test_one_move_per_rebalance_window(self):
        ws = [peasant(10 + i, "gold") for i in range(10)]
        b = brain()
        b._amai_harvest(world(ws, 3000, 30, 600.0), [MINE])
        self.assertEqual(len(b.act.calls), 1)
        settle(b, ws)
        b._amai_harvest(world(ws, 3000, 30, 601.0), [MINE])     # 1 秒后：还在窗口里
        self.assertEqual(len(b.act.calls), 0)

    def test_reserved_workers_are_not_touched(self):
        """在盖房 / 预留建分矿的农民不参与派工（AMAI 的 SelectByPeons 也只数准备好采集的）。"""
        ws = [peasant(10 + i, "idle") for i in range(4)]
        b = brain()
        b.militia_expand_builders = {10, 11}
        b.militia_expand_until = 900.0          # 预留还在有效期内（第一版夹具没设，预留当场过期）
        b._amai_harvest(world(ws, 500, 500, 600.0), [MINE])
        self.assertEqual({a for a, _t in b.act.calls}, {12, 13})


    def test_expired_reservation_counts_consistently(self):
        """预留过期那一拍：人数和闲人要按同一个口径（都当预留已清）—— 4 个闲人 4 个都派。"""
        ws = [peasant(10 + i, "idle") for i in range(4)]
        b = brain()
        b.militia_expand_builders = {10, 11}
        b.militia_expand_until = 100.0          # 已经过期
        b._amai_harvest(world(ws, 500, 500, 600.0), [MINE])
        self.assertEqual({a for a, _t in b.act.calls}, {10, 11, 12, 13})


class ExpansionWaitsForSaturation(unittest.TestCase):
    """AMAI QueueExpansionPeonsIfNeeded：现有矿的农民造满了才开下一座。"""

    def test_gate(self):
        from brain.tables import amai_saturated, peon_need
        w = SimpleNamespace(lumber=300)
        need = peon_need(w, "human", 1)          # 09-21 晚起和「该造几个」同一个函数（封顶见 tables.peon_need）
        self.assertFalse(amai_saturated(w, "human", 1, need - 1))
        self.assertTrue(amai_saturated(w, "human", 1, need))


if __name__ == "__main__":
    unittest.main()

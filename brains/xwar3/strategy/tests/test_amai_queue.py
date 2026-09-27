# -*- coding: utf-8 -*-
"""AMAI 建造队列（brain/amai_strategy/queue.py）：数量拆条、补前置、老化、钱不够就停、升本闸、Block、维护费闸。

期望值都是按 AMAI 公式算出来的，不是照代码现状写的：
  数量拆条：BuildUnit(3, hfoo, 65) -> 第 k 个的优先级 = 65 + 2×(3-k)，即 69 / 67 / 65
  前置：hfoo 的 needed1 = 兵营，优先级 = 65 + prio_n_inc×3 = 71；要几座由 GetFactoryQty 算（要造的兵 <= 3 时恒为 1）
  升本闸：keep 造价 320/210，"金 ×4 > 账本 且 木 ×4 > 账本"时才挡 -> 金 < 1280 且 木 < 840
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain.amai_strategy import data as D            # noqa: E402
from brain.amai_strategy.queue import BuildQueue     # noqa: E402


class FakeEnv:
    """只实现队列用到的那些取值。counts = {4 字码: 场上数量}，done = 已建成的数量（缺省同 counts）。"""

    def __init__(self, counts=None, done=None, gold=1000, wood=1000, food_used=0, food_cap=100, tier=1,
                 ai_time=10.0, check=None, buildings=None, upgrades=None, mines=1):
        self.counts = dict(counts or {})
        self.done = dict(done if done is not None else self.counts)
        self._gold, self._wood = gold, wood
        self._food_used, self._food_cap = food_used, food_cap
        self._tier = tier
        self._ai_time = ai_time
        self._check = check or (lambda want: None)
        self.buildings = dict(buildings or {})       # build_free：已建成的生产建筑
        self.upgrades = dict(upgrades or {})
        self._mines = mines
        self.checked = []

    # 时间/资源
    def ai_time(self):
        return self._ai_time

    def elapsed(self):
        return self._ai_time * 5

    def gold(self):
        return self._gold

    def wood(self):
        return self._wood

    def gold_available(self):
        return self._gold

    def wood_available(self):
        return self._wood

    def food_used(self):
        return self._food_used

    def food_cap(self):
        return self._food_cap

    def food_limit(self):
        return self._food_cap

    def tier(self):
        return self._tier

    # 数量
    def town_count(self, code, only_done=False, town=-1):
        if not code:
            return 0
        return sum(self.unit_count(c, only_done, town) for c in [code] + D.count_alts(code))

    def unit_count(self, code, only_done=False, town=-1):
        if not code:
            return 0
        return int((self.done if only_done else self.counts).get(code, 0))

    def upgrade_level(self, code):
        return int(self.upgrades.get(code, 0))

    def item_count(self, code):
        return 0

    def neutral_available(self, code):
        return False

    def build_free(self):
        return dict(self.buildings)

    def halls_completed(self, code):
        return True

    def hall_upgrading(self):
        return False

    def can_build(self, code):
        return True

    # 钱 / 能不能下
    def cost(self, want):
        return D.cost(want.code)

    def check(self, want):
        self.checked.append(want)
        return self._check(want)

    def town_threatened(self):
        return False

    def town_alive(self, addr):
        return True

    def towns(self, include_main=False):
        return []

    # 矿
    def mines_harvested(self):
        return self._mines

    def mines_owned(self):
        return self._mines

    def has_expanded(self):
        return self._mines > 1

    def expand_count(self):
        return self._mines

    def expansion_available(self):
        return True

    def expansion_strength(self):
        return 0.0

    def own_strength(self):
        return 20.0

    def peons_saturated(self):
        return True

    def rush_factor(self):
        return 8000

    # 收入
    def gold_income(self):
        return 10.0

    def gold_unit_percentage(self):
        return 0.9

    def difficulty(self):
        return 2

    # 局面
    def heroes(self):
        return ["Hamg", "Hmkg", "Hpal"]

    def hero_built(self, slot):
        return False

    def miss(self, key):
        pass


def q(race="human"):
    return BuildQueue(D.AmaiData(race))


class TestQueueInsert(unittest.TestCase):
    def test_qty_split_and_prereq(self):
        bq, env = q(), FakeEnv()
        bq.add("unit", 3, "hfoo", 65, env)
        got = [(e.code, e.qty, e.prio) for e in bq.entries]
        self.assertEqual([x for x in got if x[0] == "hfoo"], [("hfoo", 1, 69), ("hfoo", 2, 67), ("hfoo", 3, 65)])
        self.assertIn(("hbar", 1, 71), got)          # needed1 = 兵营，65 + 2×3
        self.assertEqual([e.prio for e in bq.entries], sorted([e.prio for e in bq.entries], reverse=True))

    def test_no_duplicate_on_second_request(self):
        bq, env = q(), FakeEnv()
        bq.add("unit", 3, "hfoo", 65, env)
        n = len(bq.entries)
        bq.add("unit", 3, "hfoo", 65, env)
        self.assertEqual(len(bq.entries), n)

    def test_already_enough_not_inserted(self):
        bq, env = q(), FakeEnv(counts={"hfoo": 3})
        bq.add("unit", 3, "hfoo", 65, env)
        self.assertEqual([e.code for e in bq.entries], [])

    def test_counting_alternates(self):
        """TownCount(uKEEP) 把城堡也数进来：有城堡时"要 1 座要塞"就不再入队。"""
        bq, env = q(), FakeEnv(counts={"hcas": 1})
        bq.add("unit", 1, "hkee", 42, env)
        self.assertEqual([e.code for e in bq.entries], [])

    def test_knight_prereqs(self):
        bq, env = q(), FakeEnv()
        bq.add("unit", 1, "hkni", 55, env)
        got = {(e.code, e.prio) for e in bq.entries}
        self.assertIn(("hkni", 55), got)
        self.assertIn(("hbar", 61), got)             # needed1 + 2×3
        self.assertIn(("hcas", 59), got)             # needed2 + 2×2
        self.assertIn(("hbla", 57), got)             # needed3 + 2×1
        self.assertIn(("hlum", 57), got)             # NeededExtra：骑士还要伐木场，+2

    def test_block_list_limits_qty(self):
        bq, env = q(), FakeEnv(ai_time=10.0)
        bq.add_block(1, "halt", False, 0, "hbar", 16)     # 前 80 秒没有祭坛就不许造兵营
        bq.add("unit", 1, "hbar", 90, env)
        self.assertEqual([e.code for e in bq.entries], [])
        self.assertEqual(bq.counts.get("被 Block 挡住·hbar"), 1)
        bq2, env2 = q(), FakeEnv(ai_time=20.0)            # 过期之后放行
        bq2.add_block(1, "halt", False, 0, "hbar", 16)
        bq2.add("unit", 1, "hbar", 90, env2)
        self.assertIn("hbar", [e.code for e in bq2.entries])


class TestQueueRound(unittest.TestCase):
    def test_aging_and_keep(self):
        """下不了的条目每轮 +1（prio_t_inc），并且留在队列里。"""
        bq, env = q(), FakeEnv(check=lambda w: "没有能出的建筑")
        bq.add("unit", 1, "hfoo", 65, env)
        bq.plan(env)
        self.assertEqual([(e.code, e.prio) for e in bq.entries if e.code == "hfoo"], [("hfoo", 66)])
        bq.plan(env)
        self.assertEqual([(e.code, e.prio) for e in bq.entries if e.code == "hfoo"], [("hfoo", 67)])

    def test_unit_stops_when_broke(self):
        """兵钱不够就停，后面优先级低的一律不花钱（AMAI 的攒钱）。
        前置都已经建好（否则骑士会先把兵营/城堡/铁匠铺/伐木场排在自己前面，停在那些上面）。"""
        bq = q()
        have = {"hbar": 1, "hcas": 1, "hbla": 1, "hlum": 1, "htow": 1}
        env = FakeEnv(counts=have, gold=100, buildings=have)
        bq.add("unit", 1, "hkni", 80, env)            # 245 金，买不起
        bq.add("unit", 1, "hfoo", 50, env)            # 135 金，本来买得起
        res = bq.plan(env)
        self.assertEqual(res.stopped_at.code, "hkni")
        self.assertEqual([d.want.code for d in res.decisions], [])

    def test_upgrade_does_not_break_the_loop(self):
        """研究钱不够不会让循环停在它身上（但会把账本扣成负数，于是后面的兵停下）。"""
        bq = q()
        have = {"hbla": 1, "hbar": 1, "htow": 1}
        env = FakeEnv(counts=have, gold=0, wood=0, buildings=have)
        bq.add("upgrade", 1, "Rhme", 90, env)
        bq.add("unit", 1, "hfoo", 50, env)
        res = bq.plan(env)
        self.assertEqual(res.stopped_at.code, "hfoo")

    def test_affordable_unit_is_decided_once_per_free_factory(self):
        """一座兵营一轮只出一个（build_free = 已建成的出兵建筑数）。"""
        bq = q()
        env = FakeEnv(counts={"hbar": 1}, gold=1000, buildings={"hbar": 1})
        bq.add("unit", 4, "hfoo", 65, env)
        res = bq.plan(env)
        self.assertEqual([d.want.code for d in res.decisions].count("hfoo"), 1)
        env2 = FakeEnv(counts={"hbar": 2}, gold=1000, buildings={"hbar": 2})
        bq2 = q()
        bq2.add("unit", 4, "hfoo", 65, env2)
        res2 = bq2.plan(env2)
        self.assertEqual([d.want.code for d in res2.decisions].count("hfoo"), 2)

    def test_tier_block(self):
        """本轮排在升本前面的兵没出够 75% 且人口 < 60 -> 挡住升二本；兵出够了就不挡。"""
        have = {"hbar": 1, "htow": 1}
        bq = q()
        env = FakeEnv(counts=have, gold=500, wood=300, buildings=have)
        bq.set_tier_block(1, 0.75, 60, True)
        bq.add("unit", 8, "hfoo", 65, env)
        bq.add("unit", 1, "hkee", 42, env)
        res = bq.plan(env)
        self.assertIn("升本闸：前面的兵还没出够", [r for _w, r in res.skipped])
        self.assertEqual(bq.counts.get("升本被闸门挡下"), 1)
        # 兵出够了就不挡：8 × 0.75 = 6
        bq2 = q()
        env2 = FakeEnv(counts=dict(have, hfoo=6), gold=500, wood=300, buildings=have)
        bq2.set_tier_block(1, 0.75, 60, True)
        bq2.add("unit", 8, "hfoo", 65, env2)
        bq2.add("unit", 1, "hkee", 42, env2)
        res2 = bq2.plan(env2)
        self.assertNotIn("升本闸：前面的兵还没出够", [r for _w, r in res2.skipped])

    def test_tier_block_bypassed_when_rich(self):
        """有钱就放行：金 >= 造价×4（320×4=1280）或木 >= 210×4=840 时不挡，而且这一级的兵力闸从此解除。"""
        have = {"hbar": 1, "htow": 1}
        bq = q()
        env = FakeEnv(counts=have, gold=2000, wood=2000, buildings=have)
        bq.set_tier_block(1, 0.75, 60, True)
        bq.add("unit", 8, "hfoo", 65, env)
        bq.add("unit", 1, "hkee", 42, env)
        res = bq.plan(env)
        self.assertNotIn("升本闸：前面的兵还没出够", [r for _w, r in res.skipped])
        self.assertFalse(bq.lock_active[1])
        self.assertIn("hkee", [d.want.code for d in res.decisions])

    def test_upkeep_gate(self):
        """人口 49 再出一个步兵就越过 50 这道维护费线（49 + 2 > 50）：金不到阻力 500、又只有 1 座矿
        -> 只许造到线为止（(50-49)//2 = 0 个），并从本轮账本里预留 500 金。"""
        bq = q()
        env = FakeEnv(counts={"hbar": 1}, gold=400, wood=1000, food_used=49, buildings={"hbar": 1}, mines=1)
        bq.add("unit", 10, "hfoo", 65, env)
        res = bq.plan(env)
        self.assertEqual(len(res.decisions), 0)
        self.assertEqual(res.stopped_at.code, "hfoo")
        self.assertEqual(bq.counts.get("维护费预留"), 1)

    def test_upkeep_allowed_when_rich(self):
        """金 > 阻力 500 就允许越线：三座兵营各出一个，不预留。"""
        bq = q()
        env = FakeEnv(counts={"hbar": 3}, gold=900, wood=1000, food_used=49, buildings={"hbar": 3}, mines=1)
        bq.add("unit", 10, "hfoo", 65, env)
        res = bq.plan(env)
        self.assertEqual(len(res.decisions), 3)
        self.assertIsNone(bq.counts.get("维护费预留"))

    def test_max_order_drops_smaller_request(self):
        """同一个 id 本轮已经按更大的数量处理过 -> 数量更小、优先级更低的那条直接丢掉（max_order）。
        建筑不受"空闲出兵建筑数"限制，所以 qty2 那一条一次能下两座 —— 一共 3 条下单，被丢掉的是 hkni 带出来的 qty1@61。"""
        bq = q()
        env = FakeEnv(counts={"htow": 1}, gold=2000, buildings={"htow": 1})
        bq.add("unit", 1, "hkni", 55, env)             # 顺带排一条 hbar qty1 @61
        bq.add("unit", 2, "hbar", 71, env)             # 更高优先级、数量更大的同一个 id（拆成 qty1@73、qty2@71）
        res = bq.plan(env)
        self.assertEqual(bq.counts.get("本轮已处理过更大的请求·hbar"), 1)
        self.assertEqual([d.want.code for d in res.decisions].count("hbar"), 3)

    def test_clear_on_events(self):
        bq, env = q(), FakeEnv()
        bq.add("unit", 3, "hfoo", 65, env)
        bq.clear("换战略")
        self.assertEqual(bq.entries, [])
        self.assertEqual(bq.counts.get("清空队列·换战略"), 1)


if __name__ == "__main__":
    unittest.main()

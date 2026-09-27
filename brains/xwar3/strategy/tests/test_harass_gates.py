# -*- coding: utf-8 -*-
"""骚扰小队的两道闸按对的东西数（harass.want army_n / ahead、runtime 的 harass_busy）。

09-22 该骚扰时要骚扰：分矿建起来之后就去骚扰对方分矿，占优势时主动骚扰。
1-304~308 每局「主力在打仗」316~390、「人数不够」200~458，一共只派出过 1 次：
打野（没接战）也算主力在打仗；至少几个才派只数步兵/火枪/骑士，迫击炮、牧师、女巫、狮鹫都不算。
"""
import os
import random
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import gamedata as G  # noqa: E402
from brain import harass as H  # noqa: E402
from brain import world as W  # noqa: E402

C1 = H.HUMAN_GROUPS[0]


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None):
    m = float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m)


class Want(unittest.TestCase):
    def test_whole_army_counts_not_just_the_harass_types(self):
        self.assertEqual(H.want(C1, 3, "human", 300, -1e9, False, 10, 1), "人数不够")              # 老口径：只有 3 个步兵
        self.assertEqual(H.want(C1, 3, "human", 300, -1e9, False, 10, 1, army_n=12), "go")        # 整支部队 12 个健康的
        # 09-23 照 AMAI（Harass(1, …, TownCountDone(uFOOTMAN) > 6)）：一队 2 个、全军健康兵 > 6 就派
        self.assertEqual(H.want(C1, 3, "human", 300, -1e9, False, 10, 1, army_n=6), "人数不够")
        self.assertEqual(H.want(C1, 3, "human", 300, -1e9, False, 10, 1, army_n=7), "go")
        self.assertEqual(H.want(C1, 1, "human", 300, -1e9, False, 10, 1, army_n=20), "人数不够")  # 凑不齐一队

    def test_ahead_halves_cooldown_and_skips_the_roll(self):
        last = 300 - C1["cd_sec"] * 0.6
        self.assertEqual(H.want(C1, 9, "human", 300, last, False, 10, 1), "冷却中")
        self.assertEqual(H.want(C1, 9, "human", 300, last, False, 10, 1, ahead=True), "go")
        self.assertEqual(H.want(C1, 9, "human", 300, -1e9, False, 3, 99), "没抽中")
        self.assertEqual(H.want(C1, 9, "human", 300, -1e9, False, 3, 99, ahead=True), "go")


class Manager(unittest.TestCase):
    def world(self, foe_fighters=0):
        home = U(1, "htow", 0, 0, 0)
        army = [U(10 + i, "hfoo", 0, 100 * i, 0) for i in range(3)]
        army += [U(20 + i, t, 0, 200, 100 * i) for i, t in enumerate(("hmtm", "hmtm", "hsor", "hsor", "hmpr", "hmpr", "hgry"))]
        enemy = [U(100, "hcas", 1, 9000, 9000), U(101, "hgtw", 1, 9100, 9000),
                 U(111, "hpea", 1, 4000, 0), U(112, "htow", 1, 4200, 0)]            # 对方分矿的农民，没塔
        enemy += [U(120 + i, "hfoo", 1, 9000, 8800 + 60 * i) for i in range(foe_fighters)]
        return home, W.build_world([home, U(2, "Hamg", 0, 0, 0)] + army + enemy, 0, 300.0, 500, 200, 40, 60, levels={2: 3})

    def test_mixed_army_sends_the_footmen(self):
        home, w = self.world()
        m = H.HarassManager(rng=random.Random(0))
        it = m.step(w, home, busy=False, town_threatened=False)
        self.assertEqual(list(m.active), [1], m.counts)
        self.assertEqual(sorted(m.active[1].addrs), [10, 11])                         # 09-23 AMAI 一队 2 个
        self.assertEqual(it[0].target.addr, 111)                                      # 对方分矿的农民
        self.assertEqual(m.counts.get("派出·优势"), 1)                                 # 对面没兵 = 优势

    def test_still_blocked_when_really_busy(self):
        home, w = self.world()
        m = H.HarassManager(rng=random.Random(0))
        m.step(w, home, busy=True, town_threatened=False)
        self.assertEqual(m.active, {})
        # 09-23 起人族有两条地面路（第二路 id=6，一队或两队分路骚扰）：地面 ×2 + 狮鹫，圣骑士组先记「不派英雄」
        self.assertEqual(m.counts.get("主力在打仗"), 3)


class RuntimeBusy(unittest.TestCase):
    def test_creeping_without_contact_is_not_busy(self):
        """runtime 传给骚扰的 busy：打野没接战不算（源码里那个表达式；断言新写法在，不断言旧写法不在）。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain", "runtime.py"),
                   encoding="utf-8").read()
        self.assertIn('(self.attack.state == "creep" and self.attack.creep_engaged)', src)
        self.assertIn("busy=harass_busy", src)


if __name__ == "__main__":
    unittest.main()

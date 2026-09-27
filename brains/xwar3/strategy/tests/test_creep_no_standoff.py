# -*- coding: utf-8 -*-
"""打野不在外面干站（tactics CREEP_WAIT_MAX_S）。

09-22 打野时单位没挨打也一直远远躲着。要求能打就上，只有被打到的单位才后撤。
最近 20 局：「召唤物优先承伤」（全队在外面等水元素接仇恨）68 段、中位 9 游戏秒、最长 24；那条还把已经贴着野怪在打的兵拉回外圈。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from test_creep_tank import CAMP, creeping  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(foot_x, water_x):
    base = [U(1, "htow", 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    base += [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)]
    am = U(2, "Hamg", CAMP[0] - 700, 0)
    foot = [U(10 + i, "hfoo", foot_x, 80 * i) for i in range(3)]
    water = [U(20, "hwt2", water_x, -100)]
    rif = [U(30, "hrif", CAMP[0] - 650, 200)]
    creeps = [U(700 + i, "nwlg", CAMP[0] + 60 * i, 0, owner=12) for i in range(2)]
    return build_world(base + [am] + foot + water + rif + creeps, 0, 600.0, 300, 300, 20, 40, levels={2: 2})


def attackers(out):
    return {u.addr for i in out if i.kind == "attack" for u in i.units}


def waiting(out):
    return {u.addr for i in out if i.kind == "move" and "打野" in i.why for u in i.units}


class NoStandoff(unittest.TestCase):
    def test_units_already_fighting_are_not_pulled_back_for_the_water_elemental(self):
        w = world(foot_x=CAMP[0] - 100, water_x=CAMP[0] - 900)           # 步兵已经贴上在打，水元素还在 900 外
        out = creeping(w).step(w, w.my_halls[0])
        self.assertFalse({10, 11, 12} & waiting(out), [(i.kind, i.why) for i in out])
        self.assertTrue({2, 10, 11, 12, 20, 30} <= attackers(out), [(i.kind, i.why) for i in out])

    def test_waiting_for_the_summon_is_capped(self):
        w = world(foot_x=CAMP[0] - 600, water_x=CAMP[0] - 900)           # 谁都没贴上：先让水元素去
        s = creeping(w)
        out = s.step(w, w.my_halls[0])
        self.assertEqual(attackers(out), {20})
        self.assertEqual(waiting(out), {2, 10, 11, 12, 30})
        w.t += T.CREEP_WAIT_MAX_S + 0.1                                  # 水元素走不过去 / 卡住：等满上限全队上
        out = s.step(w, w.my_halls[0])
        self.assertTrue({2, 10, 11, 12, 30} <= attackers(out), [(i.kind, i.why) for i in out])
        self.assertEqual(s.counts.get("打野·等接火超时·全队上"), 1)


if __name__ == "__main__":
    unittest.main()

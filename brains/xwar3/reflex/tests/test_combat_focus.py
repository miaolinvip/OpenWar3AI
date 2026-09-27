# -*- coding: utf-8 -*-
"""补攻击令跟打（pick_refill）：自己人刚补令去打的目标加权、按血量设上限、过期不算、同一批互相看得见。
期望里的分数用 target_score 现算，不手写。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, hp, owner=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp, order=0, order_known=True)


A = unit(10, "hfoo", 400, 0, 420, 1)
B = unit(11, "hfoo", 0, 700, 420, 1)
R1 = unit(1, "hrif", 0, 0, 535)
R2 = unit(2, "hrif", 0, 250, 535)


class Focus(unittest.TestCase):
    def test_second_rifle_joins_first(self):
        # 不跟打时 R2 自己会挑 B（离它近）
        self.assertGreater(C.target_score(R2, B), C.target_score(R2, A))
        self.assertGreater(C.target_score(R2, A) * (1 + C.FOCUS_JOIN), C.target_score(R2, B))
        recent = {}
        got = C.pick_refill([R1, R2], [A, B], recent, now=100.0)
        self.assertEqual([(u.addr, f.addr, j) for u, f, j in got], [(1, 10, False), (2, 10, True)])
        self.assertEqual(recent, {10: {1: 103.0, 2: 103.0}})

    def test_cap_by_remaining_hp(self):
        low = unit(12, "hfoo", 300, 0, 100, 1)          # 剩 100 血：上限 max(3, ceil(100/60)) = 3
        self.assertEqual(C.focus_cap(low), 3)
        self.assertGreater(C.target_score(R1, low), C.target_score(R1, B))
        recent = {12: {7: 101.0, 8: 101.0, 9: 101.0}}
        got = C.pick_refill([R1], [low, B], recent, now=100.0)
        self.assertEqual(got[0][1].addr, 11)            # 满了就挑别的
        got = C.pick_refill([R1], [low], {12: {7: 101.0, 8: 101.0, 9: 101.0}}, now=100.0)
        self.assertEqual((got[0][1].addr, got[0][2]), (12, False))   # 全满：退回各挑各的

    def test_expired_and_self_not_counted(self):
        recent = {10: {5: 99.0}, 11: {1: 104.0}}          # 5 号那条已过期；R1 自己挂在 B 上不算跟打
        got = C.pick_refill([R1], [A, B], recent, now=100.0)
        self.assertEqual((got[0][1].addr, got[0][2]), (10, False))
        self.assertNotIn(5, recent.get(10, {}))
        self.assertEqual(recent, {10: {1: 103.0}})      # 换到 A 之后，B 上那条不再算 R1

    def test_dead_foes_ignored(self):
        dead = unit(13, "hmpr", 100, 0, 0, 1)
        got = C.pick_refill([R1], [dead], {}, now=0.0)
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()

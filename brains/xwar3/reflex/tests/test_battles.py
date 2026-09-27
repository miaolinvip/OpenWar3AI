# -*- coding: utf-8 -*-
"""交战账本：切场次、算战损、订单占比、地点 —— 用合成帧钉住口径。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import battles  # noqa: E402

ME, FOE, CREEP = 0, 1, 12
ATK, MOVE = battles.ATTACK, 0x000D0003


def u(addr, typ, owner, x, y, hp=100, order=0):
    return [addr, typ, owner, x, y, hp, order, -1, -1]


def base_units():
    return [u(1, "htow", ME, 0, 0, 1500),
            u(2, "htow", FOE, 8000, 8000, 1500)]


class Segment(unittest.TestCase):
    def test_two_bursts_split_by_gap(self):
        far = base_units() + [u(10, "hfoo", ME, 0, 500), u(20, "hfoo", FOE, 6000, 6000)]
        near = base_units() + [u(10, "hfoo", ME, 0, 500), u(20, "hfoo", FOE, 0, 900)]
        frames = [(0.0, near), (0.15, near), (1.0, far), (20.0, near), (20.15, near)]
        self.assertEqual(battles.segment(frames, ME), [(0, 1), (3, 4)])

    def test_creeps_are_not_a_battle(self):
        creep = base_units() + [u(10, "hfoo", ME, 0, 500), u(30, "nfsh", CREEP, 0, 600)]
        self.assertEqual(battles.segment([(0.0, creep)], ME), [])

    def test_workers_do_not_count_as_contact(self):
        peon = base_units() + [u(11, "hpea", ME, 0, 500), u(20, "hfoo", FOE, 0, 600)]
        self.assertEqual(battles.segment([(0.0, peon)], ME), [])


class Summarize(unittest.TestCase):
    def test_losses_by_type_and_location(self):
        f0 = base_units() + [u(10, "hfoo", ME, 0, 500, order=ATK), u(11, "hrif", ME, 100, 500, order=0),
                             u(12, "Hamg", ME, 50, 400, order=MOVE),
                             u(20, "hkni", FOE, 0, 1000, order=ATK), u(21, "hfoo", FOE, 50, 1000, order=ATK)]
        # 5 秒后：步枪兵死（不在帧里），英雄血量 0，敌方步兵死
        f1 = base_units() + [u(10, "hfoo", ME, 0, 500, order=ATK), u(12, "Hamg", ME, 50, 400, hp=0),
                             u(20, "hkni", FOE, 0, 900, order=ATK)]
        frames = [(0.0, f0), (3.0, f0), (8.0, f1)]
        s = battles.summarize(frames, 0, 1, ME)
        self.assertEqual(s["where"], "我方基地")
        self.assertEqual(s["peak_m"], 3)
        self.assertEqual(s["peak_f"], 2)
        self.assertEqual(dict(s["lost_m"]), {"hrif": 1, "Hamg": 1})
        self.assertEqual(dict(s["lost_f"]), {"hfoo": 1})
        self.assertEqual(s["heroes_dead"], ["Hamg"])
        # 两帧 × 3 个接触单位：攻击 2、smart 2、订单 0 且任务对为空（-1）= 真空闲 2
        self.assertEqual(s["orders"]["攻击"], 2)
        self.assertEqual(s["orders"]["真空闲"], 2)
        self.assertEqual(s["orders"]["smart"], 2)

    def test_order_zero_with_task_is_not_idle(self):
        busy = u(10, "hfoo", ME, 0, 500, order=0)
        busy[7], busy[8] = 0x1234, 0x5678          # 有任务句柄
        f0 = base_units() + [busy, u(20, "hfoo", FOE, 0, 900)]
        s = battles.summarize([(0.0, f0)], 0, 0, ME)
        self.assertEqual(s["orders"]["有任务无订单"], 1)
        self.assertEqual(s["orders"]["真空闲"], 0)

    def test_order_0x000d0004_is_stop_not_smart(self):
        stop = u(10, "hfoo", ME, 0, 500, order=0x000D0004)
        f0 = base_units() + [stop, u(20, "hfoo", FOE, 0, 900)]
        s = battles.summarize([(0.0, f0)], 0, 0, ME)
        self.assertEqual(s["orders"]["其他"], 1)
        self.assertEqual(s["orders"]["smart"], 0)

    def test_recycled_address_counts_as_dead_not_as_a_farm(self):
        # 步兵在接触中死掉，同一个地址马上变成新盖的农场
        f0 = base_units() + [u(10, "hfoo", ME, 0, 500), u(20, "hfoo", FOE, 0, 900)]
        f1 = base_units() + [u(10, "hhou", ME, 0, 500, hp=500), u(20, "hfoo", FOE, 0, 900)]
        s = battles.summarize([(0.0, f0), (2.0, f1)], 0, 0, ME)
        self.assertEqual(dict(s["comp_m"]), {"hfoo": 1})
        self.assertEqual(dict(s["lost_m"]), {"hfoo": 1})

    def test_zeppelins_are_not_fighters(self):
        zep = base_units() + [u(10, "nzep", ME, 0, 500, hp=575), u(20, "hfoo", FOE, 0, 900)]
        self.assertEqual(battles.segment([(0.0, zep)], ME), [])

    def test_water_elementals_are_fighters(self):
        # hwt2/hwt3 是 2/3 级水元素（units.json 的 waterelemental2/3），不是塔
        for typ in ("hwat", "hwt2", "hwt3"):
            f0 = base_units() + [u(10, typ, ME, 0, 500, hp=675), u(20, "hfoo", FOE, 0, 900)]
            self.assertEqual(battles.segment([(0.0, f0)], ME), [(0, 0)], typ)

    def test_open_field(self):
        f0 = base_units() + [u(10, "hfoo", ME, 4000, 4000), u(20, "hfoo", FOE, 4000, 4500)]
        s = battles.summarize([(0.0, f0)], 0, 0, ME)
        self.assertEqual(s["where"], "野外")


if __name__ == "__main__":
    unittest.main()

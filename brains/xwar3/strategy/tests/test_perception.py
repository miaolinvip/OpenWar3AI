# -*- coding: utf-8 -*-
"""感知：英雄等级限频缓存、队长读取（每个军团一个、同一队长去重、读失败不抛）、incoming 判据。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import perception as P, world as W  # noqa: E402
from brain import gamedata as G  # noqa: E402


def U(addr, t, owner=0, x=0.0, y=0.0):
    m = float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m, hp_max=m)


class FakeCmd:
    def __init__(self):
        self.level_calls = 0
        self.order_calls = 0

    def hero_level(self, addr):
        self.level_calls += 1
        return 3

    def current_order(self, addr):
        self.order_calls += 1
        if addr == 999:
            raise RuntimeError("通道抖了")
        return {"addr": addr * 10, "target": (1, 2)}

    def captain(self, order):
        if order["addr"] in (200 * 10, 201 * 10):   # 这两个兵跟同一个队长
            return {"addr": 7777, "x": 9000.0, "y": 9000.0, "goal_x": 300.0, "goal_y": 0.0, "home_x": 9500.0, "home_y": 9500.0}
        return None


class TestPerception(unittest.TestCase):
    def test_hero_levels_cached(self):
        p = P.Perception(FakeCmd())
        h = [U(2, "Hamg")]
        self.assertEqual(p.hero_levels(h, 0.0), {2: 3})
        p.hero_levels(h, 5.0)
        self.assertEqual(p.cmd.level_calls, 1)
        p.hero_levels(h, 10.0)
        self.assertEqual(p.cmd.level_calls, 2)

    def test_captains_and_incoming(self):
        home = U(1, "htow", 0, 0, 0)
        foes = [U(999, "hfoo", 1, 9000, 9100), U(200, "hfoo", 1, 9000, 9000), U(201, "hfoo", 1, 9050, 9000)]
        w = W.build_world([home] + foes, 0, 100.0, 0, 0, 5, 10)
        p = P.Perception(FakeCmd())
        caps = p.read_captains(w.enemy_armies, 100.0)
        self.assertEqual(len(caps), 1)                  # 同一个军团：999 抛异常跳过，200 读到队长就换下一个军团
        self.assertEqual((caps[0]["goal_x"], caps[0]["army_n"]), (300.0, 3))
        self.assertEqual(p.reads.get("队长·读到"), 1)
        p.read_captains(w.enemy_armies, 102.0)          # 5 秒内不重读
        self.assertEqual(p.cmd.order_calls, 2)
        inc = P.incoming(w, caps)
        self.assertEqual((len(inc), inc[0]["town"].hall.addr), (1, 1))
        near = dict(caps[0], x=500.0, y=0.0)            # 队长位置先到门口，部队实际还在远处
        self.assertGreater(P.incoming(w, [near])[0]["dist"], 12000)
        arrived = W.build_world([home, U(200, "hfoo", 1, 500, 0)], 0, 105, 0, 0, 5, 10)
        self.assertEqual(P.incoming(arrived, [near]), [])  # 部队真正到家门口才交给直接防守


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""英雄看戏埋点：满血英雄没订单没任务、旁边自己的兵在交战 -> 计数带「谁锁着 / 大脑给的令」；在打的、残血的、兵没在打的不记。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain.runtime import Brain  # noqa: E402
from brain.tactics import Intent  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def scene(hero_hp=None, hero_order=0, hero_task=False, grunts_fight=True):
    hall = U(0x100, "ogre", -5184, 2944)
    hero = U(0x200, "Obla", 3000, 2000, order=hero_order, has_task=hero_task)
    hero.hp = hero.hp_max = 700.0                                    # 英雄血量不在兵种表里，U() 会给 100
    if hero_hp is not None:
        hero.hp = hero_hp
    grunts = [U(0x300 + i, "ogru", 3500, 2000 + 50 * i, has_task=True) for i in range(3)]
    fx = 3800 if grunts_fight else 7000
    foes = [U(0x900 + i, "hfoo", fx, 2000 + 50 * i, owner=1) for i in range(3)]
    return build_world([hall, hero] + grunts + foes, 0, 600.0, 300, 300, 20, 40), hero


class FakeClaims:
    me = 1

    def __init__(self, lease):
        self.lease = lease

    def lease_of(self, addr):
        return self.lease


class TestHeroIdleProbe(unittest.TestCase):
    def test_current_tick_assignment_is_not_a_false_idle_alarm(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), FakeClaims((3, 90, 5000)), d)          # 3 = combat
            w, hero = scene()
            b._hero_idle_probe(w, [Intent("move", [hero], 0, 0, why="英雄残血先回家")])
            self.assertEqual(dict(b.counts), {})                         # 状态快照在下令前，不能把刚收到意图的英雄算成看戏
            b._hero_idle_probe(w, [])
            self.assertEqual(b.counts["英雄看戏·combat锁着·大脑没给令"], 1)
            self.assertEqual(sum(1 for n in b.notes if "英雄看戏" in n[1]), 1)

    def test_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            for kw in (dict(hero_task=True), dict(hero_order=0x000D000F), dict(hero_hp=200.0), dict(grunts_fight=False)):
                w, hero = scene(**kw)
                b._hero_idle_probe(w, [])
            self.assertEqual([k for k in b.counts if k.startswith("英雄看戏")], [])
            w, hero = scene()
            b._hero_idle_probe(w, [])
            self.assertEqual(b.counts["英雄看戏·没人锁·大脑没给令"], 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""捡装备（09-18 打野掉在地上的装备英雄不捡就走了）：周围安全时残血/被保命锁的英雄也捡脚边的东西；地上物品发布给大脑。"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_scavenge as S  # noqa: E402


def u(x, y, owner=0, hp=100.0):
    return SimpleNamespace(x=x, y=y, owner=owner, hp=hp)


class TestSafePick(unittest.TestCase):
    def test_safe_around(self):
        hero = u(0, 0)
        self.assertTrue(S.safe_around(hero, []))
        self.assertTrue(S.safe_around(hero, [u(1000, 0, owner=1)]))            # 900 外
        self.assertFalse(S.safe_around(hero, [u(800, 0, owner=1)]))            # 敌方玩家
        self.assertFalse(S.safe_around(hero, [u(0, 850, owner=12)]))           # 野怪
        self.assertTrue(S.safe_around(hero, [u(100, 0, owner=15)]))            # 中立被动（小动物/商店）不算
        self.assertTrue(S.safe_around(hero, [u(100, 0, owner=12, hp=0)]))      # 死了的不算

    def test_publish_ground(self):
        with tempfile.TemporaryDirectory() as d:
            old = S.PICKF
            S.PICKF = os.path.join(d, "pickup.json")
            try:
                S.publish_ground([(0x1234, "rde1", 100.4, -200.6)], 0.0, 4242, 1)
                with open(os.path.join(d, "ground_items.json"), encoding="utf-8") as fh:
                    data = json.load(fh)
            finally:
                S.PICKF = old
            self.assertEqual((data["gamePid"], data["items"]), (4242, [[0x1234, "rde1", 100, -201]]))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""「路上有埋伏」只算会打人的东西（mod_scavenge.can_attack / ambush_on_route）。

09-22 1-287：00:03:43「不去捡 tstr：路上 nmh1 离路线 428，我方只有 2 人缠着它」——
nmh1 是两栖鱼人小屋（中立建筑，units.json 攻击 0），力量之书因此在地上躺了 2 分多钟，00:05:54 才被捡走。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_scavenge as S  # noqa: E402


def u(t, x, y, owner=12):
    return SimpleNamespace(type=t, x=x, y=y, owner=owner, hp=100.0)


class AmbushIgnoresHarmlessBuildings(unittest.TestCase):
    def setUp(self):
        if not S.DPS:
            self.skipTest("没有 units.json")
        self.hero = u("Hamg", 0, 0, owner=0)
        self.item = (1, "tstr", 720.0, 0.0)

    def test_murloc_hut_on_the_route_is_not_an_ambush(self):
        self.assertFalse(S.can_attack(u("nmh1", 0, 0)))
        self.assertIsNone(S.ambush_on_route(self.hero, self.item, [u("nmh1", 360, 428)], []))

    def test_a_gnoll_on_the_route_still_is(self):
        self.assertTrue(S.can_attack(u("ngno", 0, 0)))
        amb = S.ambush_on_route(self.hero, self.item, [u("ngno", 360, 428)], [])
        self.assertIsNotNone(amb)
        self.assertEqual(amb[0].type, "ngno")

    def test_unknown_type_is_treated_as_dangerous(self):
        self.assertTrue(S.can_attack(u("zzzz", 0, 0)))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""血法师（09-21 团战和打野时频繁用技能，优先打敌人扎堆的地方）。

1. 烈焰风暴**不伤自己人**（游戏说明「每秒对敌方的地面单位造成伤害」）—— 自己人和敌人搅在一起时照放；
   暴风雪伤自己人（「对区域内的单位造成伤害」），仍然避开。
2. 法力虹吸：蓝不够放烈焰风暴时，从射程内的敌方英雄 / 施法者身上吸（10 蓝）。
3. 凤凰：学了、身边有敌人就召。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp,
                hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def ready(_name):
    return True


class FlameStrikeAmongFriends(unittest.TestCase):
    def test_cast_into_a_melee_brawl(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        foes = [u(10 + i, "ogru", FOE, 500 + 40 * i, 0, 700) for i in range(3)]
        ours = [u(20 + i, "hfoo", ME, 510 + 40 * i, 30, 420) for i in range(3)]     # 自己的步兵贴着它们砍
        plan = C.plan_hero_cast(hero, foes, [hero] + ours, lambda n: n == "flamestrike")
        self.assertIsNotNone(plan, "烈焰风暴不伤自己人：混战里照放")
        self.assertEqual(plan[0], "flamestrike")

    def test_blizzard_still_avoids_friends(self):
        hero = u(1, "Hamg", ME, 0, 0, 700)
        foes = [u(10 + i, "ogru", FOE, 500 + 40 * i, 0, 700) for i in range(5)]
        ours = [u(20, "hfoo", ME, 540, 30, 420)]
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero] + ours, lambda n: n == "blizzard"))
        # 前提：没有自己人时暴风雪是放的（否则上面那条恒真）
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], lambda n: n == "blizzard")[0], "blizzard")


class SiphonMana(unittest.TestCase):
    def scene(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        seer = u(10, "Ofar", FOE, 450, 0, 600)                  # 敌方英雄，在射程 600 内
        grunt = u(11, "ogru", FOE, 0, 800, 700)                 # 分散，凑不成烈焰风暴的一团
        return hero, [seer, grunt]

    def test_low_mana_drains_the_enemy_hero(self):
        hero, foes = self.scene()
        low = {}
        plan = C.plan_hero_cast(hero, foes, [hero], ready, mana=50.0, low=low)
        self.assertEqual((plan[0], plan[3]), ("siphonmana", 10))

    def test_enough_mana_does_not_siphon(self):
        hero, foes = self.scene()
        plan = C.plan_hero_cast(hero, foes, [hero], lambda n: n == "siphonmana", mana=300.0)
        self.assertIsNone(plan)

    def test_unknown_mana_does_not_siphon(self):
        hero, foes = self.scene()
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero], lambda n: n == "siphonmana", mana=None))

    def test_creeps_are_not_drained(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        creep = u(10, "nmrm", C.CREEP_OWNER, 400, 0, 400)
        self.assertIsNone(C.plan_hero_cast(hero, [creep], [hero], lambda n: n == "siphonmana", mana=20.0))


class Phoenix(unittest.TestCase):
    def test_summoned_when_learned_and_enemies_near(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        foes = [u(10, "ogru", FOE, 600, 0, 700)]
        learned = {"AHpx": 1, "AHfs": 3, "AHdr": 2}
        plan = C.plan_hero_cast(hero, foes, [hero], ready, mana=400.0, learned=learned)
        self.assertEqual(plan[0], "phoenix")
        self.assertEqual(plan[1], 0x000D0209)

    def test_not_learned_falls_through(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        foes = [u(10, "ogru", FOE, 600, 0, 700)]
        unlearned = {}
        C.plan_hero_cast(hero, foes, [hero], ready, mana=400.0, learned={"AHfs": 1}, unlearned=unlearned)
        self.assertIn("phoenix", unlearned)


if __name__ == "__main__":
    unittest.main()

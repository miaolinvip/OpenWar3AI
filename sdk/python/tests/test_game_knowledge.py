# -*- coding: utf-8 -*-
"""运营常识（09-24）：维护费档位、英雄经验表、打野经验衰减、野怪聚营地、buff 查表。数值对着 1.27 的规则钉住。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
import w3paths  # noqa: E402

G = openwar3.Game


class Stub(openwar3.Game):
    def __init__(self, food=10, creeps=(), halls=()):
        self.food, self._creeps, self._halls = food, list(creeps), list(halls)

    def resources(self, player=None):
        return {"gold": 0, "lumber": 0, "food_used": self.food, "food_cap": 100}

    def creeps(self):
        return self._creeps

    def my_buildings(self, types=None):
        return self._halls


def creep(t, x, y, hp=300.0):
    return SimpleNamespace(type=t, x=x, y=y, hp=hp, alive=True)


class Knowledge(unittest.TestCase):
    def test_upkeep_levels(self):
        self.assertEqual([Stub(f).upkeep()["level"] for f in (50, 51, 80, 81, 100)], ["none", "low", "low", "high", "high"])
        self.assertEqual(Stub(60).upkeep()["income"], 0.7)

    def test_hero_xp_table(self):
        self.assertEqual([G.hero_xp_needed(lv) for lv in range(2, 11)], [200, 500, 900, 1400, 2000, 2700, 3500, 4400, 5400])

    def test_creep_xp_decays_and_stops_at_5(self):
        self.assertEqual(G.creep_xp(2, 1), 120 * 0.8)
        self.assertEqual(G.creep_xp(5, 4), 300 * 0.5)
        self.assertEqual(G.creep_xp(3, 5), 0.0)                 # 5 级以后打野 0 经验

    def test_creep_camps_cluster_and_sort(self):
        home = SimpleNamespace(x=0.0, y=0.0)
        cs = [creep("ngna", 3000, 0), creep("ngnb", 3300, 100), creep("ngna", 3100, 400),   # 一个营地
              creep("nogr", 1000, 1000)]                                                     # 另一个，更近
        camps = Stub(creeps=cs, halls=[home]).creep_camps()
        self.assertEqual([len(c["units"]) for c in camps], [1, 3])
        self.assertEqual(camps[1]["hp"], 900.0)

    @unittest.skipUnless(os.path.exists(w3paths.game_data("buffs.json")), "data/game 没提取")
    def test_buff_info(self):
        info = Stub().buff_info("Bslo")
        self.assertIn("low", info["effect"].lower())             # Slow
        self.assertIsNone(Stub().buff_info("XXXX"))


if __name__ == "__main__":
    unittest.main()

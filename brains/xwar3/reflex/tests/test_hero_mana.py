# -*- coding: utf-8 -*-
"""英雄施法先看蓝 —— mod_combat.plan_hero_cast 的 mana 参数 + SPELL_MANA 对着游戏数据表。

09-16：读血/魔（index 2 = 魔）实机读到大法师 69.7/360 蓝，而水元素 125 —— 那几局水元素一局被引擎退 9~13 次，
以前读不到蓝，只能靠退令 + 10 秒退避。现在蓝不够的技能不下令，往下试便宜的。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1
SLK = pathlib.Path(__file__).resolve().parents[2] / "war3-analytics" / "w3data" / "AbilityData.slk"
ALIAS = {"waterelemental": "AHwe", "blizzard": "AHbz", "stormbolt": "AHtb", "thunderclap": "AHtc",
         "avatar": "AHav", "holylight": "AHhb", "divineshield": "AHds", "flamestrike": "AHfs"}


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def ready(_):
    return True


def slk_costs(path):
    rows, x, y = {}, 0, 0
    with open(path, encoding="latin-1") as fh:
        lines = fh.readlines()
    for line in lines:
        p = line.rstrip("\r\n").split(";")
        if p[0] != "C":
            continue
        for f in p[1:]:
            if f.startswith("X"):
                x = int(f[1:])
            elif f.startswith("Y"):
                y = int(f[1:])
            elif f.startswith("K"):
                rows.setdefault(y, {})[x] = f[1:].strip('"')
    col = {v: k for k, v in rows[1].items()}
    return {r.get(col["alias"]): r.get(col["Cost1"]) for r in rows.values()}


class ManaGate(unittest.TestCase):
    def test_low_mana_skips_water_elemental(self):
        hero = u(1, "Hamg", ME, 0, 0, 600)
        low = {}
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 700, 0)], [hero], ready, mana=69.7, low=low)
        self.assertIsNone(plan)
        self.assertEqual(low, {"waterelemental": 1})

    def test_low_mana_falls_through_to_cheaper_blizzard(self):
        hero = u(1, "Hamg", ME, 0, 0, 600)
        clump = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(4)]
        low = {}
        plan = C.plan_hero_cast(hero, clump, [hero], ready, mana=100.0, low=low)
        self.assertEqual(plan[0], "blizzard")
        self.assertEqual(low, {"waterelemental": 1})

    def test_unknown_mana_does_not_gate(self):
        hero = u(1, "Hamg", ME, 0, 0, 600)
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 700, 0)], [hero], ready, mana=None)
        self.assertEqual(plan[0], "waterelemental")

    def test_enough_mana_casts_first_choice(self):
        hero = u(1, "Hamg", ME, 0, 0, 600)
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 700, 0)], [hero], ready, mana=125.0)
        self.assertEqual(plan[0], "waterelemental")

    def test_not_counted_when_no_occasion(self):
        hero = u(1, "Hamg", ME, 0, 0, 600)
        low = {}
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 3000, 0)], [hero], ready,
                                           mana=0.0, low=low))
        self.assertEqual(low, {})          # 没到时机的不算"蓝不够"

    @unittest.skipUnless(SLK.exists(), "没有 AbilityData.slk")
    def test_costs_match_game_data(self):
        costs = slk_costs(SLK)
        for name, alias in ALIAS.items():
            self.assertEqual(C.SPELL_MANA[name], float(costs[alias]), name)
        self.assertEqual(set(C.SPELL_MANA), set(C.SPELL_ORDERS))


if __name__ == "__main__":
    unittest.main()

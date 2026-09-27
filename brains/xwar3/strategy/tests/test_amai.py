# -*- coding: utf-8 -*-
"""AMAI 公式：期望值和 war3-button-hook-lab/test-amai.ps1 同一组（两边是同一套公式，一边改了另一边会红）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import amai  # noqa: E402
from brain import gamedata as G  # noqa: E402


def U(addr, t, owner=0, x=0.0, y=0.0, hp=None, hp_max=None):
    m = hp_max if hp_max is not None else float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m)


class TestStrength(unittest.TestCase):
    def test_units(self):
        self.assertEqual(amai.unit_strength(U(1, "hfoo")), 2)
        self.assertEqual(amai.unit_strength(U(1, "hgry")), 4)
        self.assertEqual(amai.unit_strength(U(1, "hkni", hp=417.5, hp_max=835)), 2)
        self.assertEqual(amai.unit_strength(U(1, "hpea")), 0)
        self.assertEqual(amai.unit_strength(U(1, "hbar")), 0)
        self.assertEqual(amai.unit_strength(U(1, "hgtw")), 3)
        self.assertEqual(amai.unit_strength(U(1, "hgtw", hp=250, hp_max=500)), 1.5)
        self.assertEqual(amai.unit_strength(U(1, "hwtw")), 0)
        self.assertEqual(amai.unit_strength(U(1, "hfoo", hp=0, hp_max=420)), 0)

    def test_heroes(self):
        self.assertEqual(amai.unit_strength(U(7, "Hamg", hp=500, hp_max=500), {7: 3}), 5)
        self.assertEqual(amai.unit_strength(U(7, "Hamg", hp=500, hp_max=500), {7: 6}), 11)
        self.assertEqual(amai.unit_strength(U(7, "Hamg", hp=250, hp_max=500), {7: 6}), 5.5)
        self.assertEqual(amai.unit_strength(U(9, "Hamg", hp=500, hp_max=500)), 3)

    def test_sum(self):
        self.assertEqual(amai.strength([U(1, "hfoo"), U(2, "hgry"), U(3, "hpea")]), 6)


class TestAggression(unittest.TestCase):
    def test_table(self):
        self.assertEqual(amai.aggression(45, 1, "human", 1), 9)
        self.assertEqual(amai.aggression(99, 2, "human", 3), 32)
        self.assertEqual(amai.aggression(10, 0, "undead", 1), -9)
        self.assertEqual(amai.aggression(60, 1, "orc", 2), 10)
        self.assertEqual(amai.aggression(30, 1, None, 1), 7)

    def test_flee_mult(self):
        self.assertEqual(amai.flee_mult(-15), 1)
        self.assertEqual(amai.flee_mult(45), 2)
        self.assertEqual(amai.flee_mult(15), 1.5)
        self.assertEqual(amai.flee_mult(15, True), 2.5)
        self.assertAlmostEqual(amai.flee_mult(-3), 1.2)

    def test_harass_chance(self):
        self.assertEqual(amai.harass_chance(5), 70)
        self.assertEqual(amai.harass_chance(-20), 10)


class TestTargetAndThreat(unittest.TestCase):
    def test_target_good(self):
        r = amai.target_good(30, 3, False, 7000, 30, 9, 7)
        self.assertEqual((r.good, r.lhs, r.rhs), (False, 47, 46))
        r = amai.target_good(30, 3, True, 7000, 30, 9, 7)
        self.assertEqual((r.good, r.lhs), (True, 38))
        self.assertEqual(amai.target_good(120, 0, False, 99999, 0, 0).target, 120)
        self.assertEqual(amai.target_good(98, 0, False, 7000, 0, 0).target, 100)

    def test_town_threat(self):
        self.assertEqual(round(amai.town_threat(0, 0, [U(1, "hfoo", 1, 500, 0)]), 2), 4.3)
        self.assertEqual(round(amai.town_threat(0, 0, [U(1, "hfoo", 1, 2000, 0)]), 2), 2.47)
        self.assertEqual(amai.town_threat(0, 0, [U(1, "hfoo", 1, 2500, 0)]), 0)
        self.assertEqual(amai.town_threat(0, 0, [U(1, "hgtw", 1, 1500, 0)]), 6)
        self.assertEqual(amai.town_threat(0, 0, [U(1, "hpea", 1, 100, 0)]), 0)

    def test_defense(self):
        self.assertEqual(amai.accepted_threat(200), 10)
        self.assertEqual(round(amai.accepted_threat(400), 2), 11.67)
        self.assertEqual(amai.accepted_threat(1000), 20)
        self.assertEqual(round(amai.player_threat(20, -15), 2), 43.0)
        self.assertEqual(round(amai.player_threat(20, 45), 2), 85.99)
        self.assertEqual(amai.defend_mode(5, 10, 40), "minor")
        self.assertEqual(amai.defend_mode(30, 10, 40), "defend")
        self.assertEqual(amai.defend_mode(50, 10, 40), "toostrong")

    def test_flee(self):
        self.assertTrue(amai.should_flee(21, 10, -15))      # 2 倍
        self.assertFalse(amai.should_flee(20, 10, -15))


if __name__ == "__main__":
    unittest.main()

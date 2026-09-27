# -*- coding: utf-8 -*-
"""物品给谁（itemvalue）：法师吃智力书、战士吃力量书；经验书给低等级；认不出的谁近谁捡。数值按 items.json / heroes.json 现算。"""
import os
import sys
import unittest
from types import SimpleNamespace as H

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itemvalue as V  # noqa: E402


def hero(t, x=0.0):
    return H(type=t, x=x, y=0.0, addr=hash(t) & 0xFFFF)


class Data(unittest.TestCase):
    def test_tables_loaded(self):
        self.assertEqual(V.ITEMS['tint']['effects'], {'tome_int': 1.0})
        self.assertEqual(V.HEROES['Hamg']['primary'], 'INT')
        self.assertEqual(V.HEROES['Hmkg']['primary'], 'STR')


class Value(unittest.TestCase):
    def test_primary_doubles(self):
        self.assertEqual(V.value('tint', 'Hamg')[0], 2.0)
        self.assertEqual(V.value('tint', 'Hmkg')[0], 1.0)
        self.assertEqual(V.value('tstr', 'Hmkg')[0], 2.0)
        self.assertEqual(V.value('tstr', 'Hamg')[0], 1.0)

    def test_tome_of_knowledge_prefers_low_level(self):
        self.assertGreater(V.value('tkno', 'Hmkg', level=2)[0], V.value('tkno', 'Hamg', level=5)[0])
        self.assertEqual(V.value('texp', 'Hamg', level=10)[0], V.BASELINE)   # 满级吃经验书没用 -> 退回谁近谁捡

    def test_armor_and_hp_to_melee(self):
        self.assertGreater(V.value('rde1', 'Hpal')[0], V.value('rde1', 'Hamg')[0])
        self.assertGreater(V.value('manh', 'Hmkg')[0], V.value('manh', 'Hamg')[0])

    def test_unknown_item_baseline(self):
        self.assertEqual(V.value('zzzz', 'Hamg')[0], V.BASELINE)


class Pick(unittest.TestCase):
    def dist(self, h):
        return abs(h.x)

    def test_int_tome_goes_to_archmage_even_if_mk_is_closer(self):
        mk, am = hero('Hmkg', 100), hero('Hamg', 700)
        h, s, why = V.pick([mk, am], 'tint', self.dist)
        self.assertIs(h, am)                                  # 2 - 700/800 = 1.125 > 1 - 100/800 = 0.875
        self.assertIn('主属性', why)

    def test_but_not_across_the_map(self):
        mk, am = hero('Hmkg', 100), hero('Hamg', 2000)
        h, _, _ = V.pick([mk, am], 'tint', self.dist)
        self.assertIs(h, mk)                                  # 2 - 2.5 = -0.5 < 0.875

    def test_str_tome_to_mountain_king(self):
        mk, am = hero('Hmkg', 600), hero('Hamg', 100)
        self.assertIs(V.pick([mk, am], 'tstr', self.dist)[0], mk)

    def test_level_asked_only_for_level_items(self):
        asked = []

        def level_of(h):
            asked.append(h.type)
            return 3

        V.pick([hero('Hmkg'), hero('Hamg')], 'tint', self.dist, level_of=level_of)
        self.assertEqual(asked, [])
        V.pick([hero('Hmkg'), hero('Hamg')], 'tkno', self.dist, level_of=level_of)
        self.assertEqual(sorted(asked), ['Hamg', 'Hmkg'])

    def test_empty(self):
        self.assertEqual(V.pick([], 'tint', self.dist)[0], None)


if __name__ == '__main__':
    unittest.main()

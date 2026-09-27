# -*- coding: utf-8 -*-
"""点施法回执按离落点远近分开记 —— mod_combat.point_path_label。

09-16 暴风雪走动作 41：此前 16 次没进冷却，031651 局第一次进冷却 1 次 —— 猜射程内才成，先把远近分开记。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402


class PointPathLabel(unittest.TestCase):
    def test_near_and_far(self):
        self.assertEqual(C.point_path_label(0.0), "动作41·近")
        self.assertEqual(C.point_path_label(C.POINT_CAST_NEAR), "动作41·近")
        self.assertEqual(C.point_path_label(C.POINT_CAST_NEAR + 1), "动作41·远")

    def test_matches_blizzard_range(self):
        # 暴风雪施法距离 800（AbilityData.slk AHbz Rng1）
        self.assertEqual(C.POINT_CAST_NEAR, 800.0)


if __name__ == "__main__":
    unittest.main()

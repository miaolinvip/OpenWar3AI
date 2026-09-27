# -*- coding: utf-8 -*-
"""拉出来的人还在敌方塔射程里时，不能判「安全」提前放手（mod_combat.enemy_towers / in_tower_range）。

09-21 攻打敌方建筑时，敌方箭塔一直打英雄，英雄残血了仍顶在塔下不撤。
5 号 21:15 那局山丘在塔下 20 秒 649 -> 1/850：毫秒层拉了 5 次，每次都因为「900 内没有敌人」（塔不算敌人）
1.5 秒就放手，大脑马上又派回去打塔。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, owner, x, y, hp=500):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp, hp_max=hp,
                order=0, order_known=True)


class Towers(unittest.TestCase):
    def test_which_buildings_shoot(self):
        # 前提：表里箭塔有伤害、大厅没有（否则下面恒真/恒假；判据要能区分成功和失败）
        self.assertGreater(C.DPS.get("owtw", 0.0), 0)
        self.assertEqual(C.DPS.get("ogre", 0.0), 0)
        units = [unit(1, "owtw", 1, 0, 0), unit(2, "ogre", 1, 500, 0), unit(3, "ogru", 1, 0, 300),
                 unit(4, "hgtw", 0, 0, 0)]                                   # 最后一个是我方的塔
        self.assertEqual([t.addr for t in C.enemy_towers(units, 0)], [1])

    def test_range_plus_pad(self):
        tower = unit(1, "owtw", 1, 0, 0)
        edge = C.RANGE["owtw"] + C.TOWER_SAFE_PAD                          # 800 + 150
        self.assertTrue(C.in_tower_range(unit(9, "Hmkg", 0, edge - 1, 0), [tower]))
        self.assertFalse(C.in_tower_range(unit(9, "Hmkg", 0, edge + 1, 0), [tower]))

    def test_pad_matches_the_brain(self):
        """同一个数写在两处（只改一处等于没改）：大脑把残血/英雄挪到「塔射程 + TOWER_CLEAR_PAD」，这里按同一条线判安全。"""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                        "war3-brain"))
        from brain import tactics as T
        self.assertEqual(C.TOWER_SAFE_PAD, T.TOWER_CLEAR_PAD)


if __name__ == "__main__":
    unittest.main()

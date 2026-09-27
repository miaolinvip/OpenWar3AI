# -*- coding: utf-8 -*-
"""拆家门口的敌方分矿：只和那座分矿附近的守军比，不和对面全图兵力比。

起因 09-18 AMAI 局：敌方在离我主基地 5916 的矿点开了第三矿（3 座建筑、身边 0 守军），
我方 23 个兵在家里站着 —— 因为出击闸看的是对面全图兵力（62.8 > 我方 50 × 1.25）。
同一局「开矿·地图上没有能开的矿」×62：空矿被它占完了，拆它的分矿同时也是我们唯一的开矿机会。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import tactics as T  # noqa: E402
from brain.tactics import AttackSequence  # noqa: E402
from brain.world import build_world  # noqa: E402
from brain.tables import human  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HOME = (-5184, 2944)
FAR_MAIN = (4632, 2914)          # 敌方主基地（离我家 9816）
NEAR_EXP = (-4725, -2954)        # 敌方三矿（离我家 5916）


def scene(local_guards=0, my_army=14, far_army=20, exp_type="unpl"):
    hall = U(0x100, "htow", *HOME)
    peas = [U(0x300 + i, "hpea", HOME[0] + 100, HOME[1]) for i in range(5)]
    army = [U(0x400 + i, "hfoo", HOME[0] + 200, HOME[1] + 50) for i in range(my_army)]
    foe_main = [U(0x900, "unp1", *FAR_MAIN, owner=1)] + [U(0x910 + i, "uzig", FAR_MAIN[0] + 200, FAR_MAIN[1], owner=1) for i in range(3)]
    foe_far_army = [U(0x950 + i, "ugho", FAR_MAIN[0] + 300, FAR_MAIN[1] + 100, owner=1) for i in range(far_army)]
    exp = [U(0xA00, exp_type, *NEAR_EXP, owner=1), U(0xA01, "ugol", NEAR_EXP[0] + 200, NEAR_EXP[1], owner=1),
           U(0xA02, "uzig", NEAR_EXP[0] - 200, NEAR_EXP[1], owner=1)]
    guards = [U(0xA10 + i, "ugho", NEAR_EXP[0] + 100, NEAR_EXP[1] + 100, owner=1) for i in range(local_guards)]
    units = [hall] + peas + army + foe_main + foe_far_army + exp + guards
    return build_world(units, 0, 900.0, 500, 500, 40, 60), hall


class TestRaid(unittest.TestCase):
    def test_picks_undefended_expansion_near_home(self):
        w, hall = scene()
        tgt = T.raid_target(w, hall, w.own_strength)
        self.assertIsNotNone(tgt)
        self.assertEqual(tgt[0].addr, 0xA00)            # 那座大厅，不是远处的主基地
        self.assertEqual(tgt[1], 0.0)                   # 身边没有守军

    def test_skips_when_guarded(self):
        w, hall = scene(local_guards=12)                # 守军比我们强
        self.assertIsNone(T.raid_target(w, hall, w.own_strength))

    def test_skips_far_expansion(self):
        w, hall = scene()
        far = T.dist(w.enemy_halls[0], hall)
        self.assertGreater(far, T.RAID_HOME_R)          # 敌方主基地在 7000 外
        self.assertEqual(T.raid_target(w, hall, w.own_strength)[0].addr, 0xA00)

    def test_counts_enemy_gold_mine_as_expansion(self):
        w, hall = scene(exp_type="uzig")                # 那边没有大厅，只有闹鬼金矿
        tgt = T.raid_target(w, hall, w.own_strength)
        self.assertIsNotNone(tgt)
        self.assertEqual(tgt[0].type, "ugol")

    def test_skips_when_reinforcements_can_intercept(self):
        w, hall = scene(far_army=0)
        blockers = [U(0xB00+i, "hkni", -2200, -800+i*50, owner=1) for i in range(10)]
        w = build_world(w.units + blockers, 0, 900.0, 500, 500, 40, 60)
        army = w.my_army + w.my_heroes
        self.assertIsNone(T.raid_target(w, hall, w.own_strength, army, hall))

    def test_sequence_goes_for_it_even_though_global_army_is_bigger(self):
        w, hall = scene()
        self.assertLess(w.own_strength, w.foe_strength * T.ATTACK_MARGIN)   # 全图比：我们不占优，老逻辑不会出门
        with tempfile.TemporaryDirectory():
            seq = AttackSequence(dict(human.PROFILE))
            seq.step(w, hall)
            self.assertEqual(seq.counts.get("出击·拆家门口的分矿"), 1)
            self.assertIn(seq.state, ("gather", "attack"))       # 部队本来就在家集结好了，这一拍直接进攻
            self.assertEqual(seq.target.addr, 0xA00)
            self.assertTrue(any("拆家门口的分矿" in n for n in seq.notes))


if __name__ == "__main__":
    unittest.main()

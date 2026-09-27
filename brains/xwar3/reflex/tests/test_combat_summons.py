# -*- coding: utf-8 -*-
"""别打召唤物（09-18 部分单位一直在打召唤物，收益很低；攻击要有优先目标和集火目标）。

原来补攻击令的分数「1000/剩余血」权重最大，骷髅 uske（180 血）比步兵、食尸鬼得分都高 —— 专挑召唤物打。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, hp, owner=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp, order=0, order_known=True)


class SummonScore(unittest.TestCase):
    def test_skeleton_used_to_outscore_ghoul(self):
        foot = unit(1, "hfoo", 0, 0, 420)
        ske, gho = unit(10, "uske", 150, 0, 180, 1), unit(11, "ugho", 150, 0, 340, 1)
        raw_ske, raw_gho = C._score_raw(foot, ske), C._score_raw(foot, gho)
        self.assertGreater(raw_ske, raw_gho)                         # 旧分数：骷髅更高（这就是 bug）
        self.assertAlmostEqual(C.target_score(foot, ske), C.SUMMON_W * raw_ske)
        self.assertLess(C.target_score(foot, ske), C.target_score(foot, gho))

    def test_refill_prefers_real_unit(self):
        rif = unit(1, "hrif", 0, 0, 535)
        foes = [unit(10, "uske", 300, 0, 180, 1), unit(11, "uske", 320, 0, 180, 1), unit(12, "ucry", 500, 0, 550, 1)]
        out = C.pick_refill([rif], foes, {}, 0.0)
        self.assertEqual(out[0][1].type, "ucry")

    def test_only_summons_still_attacked(self):
        foot = unit(1, "hfoo", 0, 0, 420)
        out = C.pick_refill([foot], [unit(10, "uske", 150, 0, 180, 1)], {}, 0.0)
        self.assertEqual(out[0][1].type, "uske")


class SummonSwitch(unittest.TestCase):
    def test_candidate_when_summon_and_real_target_in_reach(self):
        foot = unit(1, "hfoo", 0, 0, 420)
        foes = [unit(10, "uske", 120, 0, 180, 1), unit(11, "ugho", 400, 0, 340, 1)]   # 近战射程 100：300 内有骷髅，450 内有食尸鬼
        self.assertEqual(C.summon_candidates([foot], foes, lambda u: False), [foot])
        self.assertEqual(C.summon_switch_target(foot, foes).type, "ugho")

    def test_not_candidate_without_real_target_nearby(self):
        foot = unit(1, "hfoo", 0, 0, 420)
        foes = [unit(10, "uske", 120, 0, 180, 1), unit(11, "ugho", 900, 0, 340, 1)]
        self.assertEqual(C.summon_candidates([foot], foes, lambda u: False), [])
        self.assertIsNone(C.summon_switch_target(foot, foes))

    def test_candidate_even_when_nearest_is_real(self):
        # 09-18 二：兵先被引擎锁上水元素，之后最近的敌人换成了别人 —— 原来「最近的是召唤物才查」，这种就漏了
        foot = unit(1, "hfoo", 0, 0, 420)
        foes = [unit(10, "ugho", 110, 0, 340, 1), unit(11, "hwt2", 180, 0, 675, 1)]
        self.assertEqual(C.summon_candidates([foot], foes, lambda u: False), [foot])

    def test_skip_and_summons_themselves(self):
        foot = unit(1, "hfoo", 0, 0, 420)
        wat = unit(2, "hwat", 0, 0, 525)
        foes = [unit(10, "uske", 120, 0, 180, 1), unit(11, "ugho", 400, 0, 340, 1)]
        self.assertEqual(C.summon_candidates([foot], foes, lambda u: True), [])          # 被拉/施法保护期
        self.assertEqual(C.summon_candidates([wat], foes, lambda u: False), [])          # 自己的召唤物不管

    def test_ranged_switches_to_weakest_hero_in_reach(self):
        rif = unit(1, "hrif", 0, 0, 535)
        foes = [unit(10, "hwt2", 200, 0, 675, 1), unit(11, "ugho", 500, 0, 340, 1), unit(12, "Ulic", 700, 0, 500, 1)]
        self.assertEqual(C.summon_switch_target(rif, foes).type, "Ulic")


if __name__ == "__main__":
    unittest.main()

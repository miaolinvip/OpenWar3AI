# -*- coding: utf-8 -*-
"""集火判据：读不到攻击目标 ≠ 没在打英雄。

真实失败形状（inst1 1983 分钟心跳，约 33 小时对局）：
英雄身边 900 内有敌方作战单位的 **20927 轮**里，「读到 0 个在打它」占 **77.6%**、
>=3 个的只有 **2.0%**；集火撤离整段时间只触发 123 次，而同期英雄阵亡约 1.9 次/局 ——
保命机制的触发频率和死亡频率是同一个量级。

根因不在阈值，在数据源：实测记过敌方单位
「没目标 2811 / 有目标但不是我方 1488 / 对上我方 97」，约六成读不到任务目标。
`focus_count` 把「读不到」和「在打别人」都算成不计数，于是「>=3 个明确在打它」几乎不可能成立。
问题在于 0 同时表示「没人打它」和「读不到谁在打它」，两件事要分开记。

v8~v14 七个版本都在调这条路的阈值和放行条件，没有一版动过这个 0 的二义性。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, owner=0, hl=0, hh=0, hp=400, hp_max=675):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp_max, order=0, order_known=True, handle_lo=hl, handle_hi=hh)


HERO = unit(0x100, "Hamg", 0, 0, hl=100, hh=200)
ME = (100, 200)
OTHER = (5, 6)


class FocusStats(unittest.TestCase):
    def test_splits_unknown_from_not_attacking(self):
        """三种情况必须分开数，不能都落进同一个 0。"""
        rows = [(1, ME), (2, ME), (3, None), (4, OTHER), (5, None), (6, None)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertEqual((focused, known, unknown), (2, 3, 3))

    def test_agrees_with_focus_count(self):
        rows = [(1, ME), (2, ME), (3, None), (4, OTHER), (5, (100, 201))]
        self.assertEqual(C.focus_stats(HERO, rows)[0], C.focus_count(HERO, rows))


class ExtrapolateUnread(unittest.TestCase):
    def test_the_shape_that_used_to_be_missed(self):
        """6 个敌人贴着英雄，只有 2 个读到目标、都在打它，其余 4 个读不到。

        旧判据：focused=2 < 3 -> 不撤（77.6% 的那些轮就是这样）。
        新判据：读到的 2/2 全在打它，按这个比例外推 4 个读不到的 -> 估计 6 个 >= 3 -> 撤。"""
        rows = [(1, ME), (2, ME), (3, None), (4, None), (5, None), (6, None)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertFalse(C.hero_focus_danger(focused))                       # 旧行为：漏判
        self.assertTrue(C.hero_focus_danger(focused, known, unknown, len(rows)))

    def test_does_not_fire_when_they_are_attacking_someone_else(self):
        """读得清清楚楚、大部分在打别人 —— 这不是漏判，不许外推放行。"""
        rows = [(1, ME), (2, OTHER), (3, OTHER), (4, OTHER), (5, OTHER), (6, None)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertFalse(C.hero_focus_danger(focused, known, unknown, len(rows)))

    def test_small_sample_does_not_extrapolate(self):
        """身边就一两个敌人时外推等于瞎猜：1 个敌人在打它不该触发整队撤离。"""
        rows = [(1, ME), (2, None)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertFalse(C.hero_focus_danger(focused, known, unknown, len(rows)))

    def test_nothing_readable_does_not_fire(self):
        """一个都读不到时没有比例可用，不能凭空撤 —— 否则每次读失败都会拉走英雄。"""
        rows = [(i, None) for i in range(6)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertEqual((focused, known, unknown), (0, 0, 6))
        self.assertFalse(C.hero_focus_danger(focused, known, unknown, len(rows)))

    def test_explicit_three_still_fires_without_extra_args(self):
        """老调用方式（只传 focused）行为不变。"""
        self.assertTrue(C.hero_focus_danger(3))
        self.assertFalse(C.hero_focus_danger(2))

    def test_boundary_is_computed_not_guessed(self):
        """期望按公式算，不靠感觉。
        4 个敌人、读到 2 个其中 1 个在打它、2 个读不到：
        ratio = 1/2 = 0.5 >= FOCUS_EXTRAPOLATE_RATIO，估计 = 1 + 2*0.5 = 2.0 < 3 -> 不撤。"""
        rows = [(1, ME), (2, OTHER), (3, None), (4, None)]
        focused, known, unknown = C.focus_stats(HERO, rows)
        self.assertEqual((focused, known, unknown), (1, 2, 2))
        self.assertEqual(focused + unknown * (focused / known), 2.0)
        self.assertFalse(C.hero_focus_danger(focused, known, unknown, len(rows)))
        # 同样的比例，但身边人更多（6 个里 4 个读不到）：1 + 4*0.5 = 3.0 >= 3 -> 撤
        rows2 = [(1, ME), (2, OTHER), (3, None), (4, None), (5, None), (6, None)]
        f2, k2, u2 = C.focus_stats(HERO, rows2)
        self.assertEqual(f2 + u2 * (f2 / k2), 3.0)
        self.assertTrue(C.hero_focus_danger(f2, k2, u2, len(rows2)))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""09-18 个别丝血小兵不撤退；只剩一两个兵被一群敌人围殴时也不回家。
不留一两个单位被群殴；确实要淘汰的低级兵也不送给敌军，而是让野怪打死、腾人口换高级兵。

丝血兵不受「同时撤下人数」上限、孤兵回撤（往自己人扎堆处退）、喂野怪名单里的兵不拉。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, x, y, hp=400, hp_max=400, owner=0):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp_max, order=0, order_known=True)


class Isolated(unittest.TestCase):
    def foes(self, n=4, x=300):
        return [unit(100 + i, "ugho", x, 60 * i, owner=1) for i in range(n)]

    def test_lone_unit_among_enemies(self):
        me = unit(1, "hfoo", 0, 0)
        self.assertTrue(C.isolated(me, [me], self.foes()))

    def test_one_friend_still_isolated_two_not(self):
        me, a, b = unit(1, "hfoo", 0, 0), unit(2, "hfoo", 200, 0), unit(3, "hfoo", 0, 200)
        self.assertTrue(C.isolated(me, [me, a], self.foes()))
        self.assertFalse(C.isolated(me, [me, a, b], self.foes()))

    def test_few_enemies_not_isolated(self):
        me = unit(1, "hfoo", 0, 0)
        self.assertFalse(C.isolated(me, [me], self.foes(n=2)))
        self.assertFalse(C.isolated(me, [me], self.foes(x=900)))           # 700 外

    def test_outgunned_small_group_is_isolated(self):
        # 09-18 二：020 局步兵 234/420 身边 700 内敌 5 个、900 内自己人 2 个，一直打到 63 血（原来要自己人 <= 1 才算孤兵）
        me, a, b = unit(1, "hfoo", 0, 0, 234, 420), unit(2, "hfoo", 200, 0, 209, 420), unit(3, "hfoo", 0, 200, 160, 420)
        foes = [unit(100 + i, "hfoo", 300, 60 * i, 420, 420, owner=1) for i in range(4)] + [unit(110, "hwt2", 300, 300, 675, 675, owner=1)]
        mine, theirs = C.local_power([me, a, b]), C.local_power(foes)
        self.assertLess(mine, C.ISO_POWER * theirs)                        # 603 血 × 27.8 对 2355 血 × 60.4
        self.assertTrue(C.isolated(me, [me, a, b], foes))

    def test_even_small_fight_not_isolated(self):
        me, a, b = unit(1, "hfoo", 0, 0, 420, 420), unit(2, "hfoo", 200, 0, 420, 420), unit(3, "hfoo", 0, 200, 420, 420)
        foes = [unit(100 + i, "hfoo", 300, 60 * i, 420, 420, owner=1) for i in range(3)]
        self.assertFalse(C.isolated(me, [me, a, b], foes))                # 3 对 3 满血：战力比 1

    def test_hero_not_handled_here(self):
        h = unit(1, "Hamg", 0, 0)
        self.assertFalse(C.isolated(h, [h], self.foes()))


class Rally(unittest.TestCase):
    def test_retreats_to_nearest_friendly_group(self):
        me = unit(1, "hfoo", 0, 0)
        grp = [unit(10 + i, "hfoo", -2000, 60 * i) for i in range(4)]
        far = [unit(20 + i, "hfoo", 2900, 60 * i) for i in range(4)]
        x, y = C.rally_point(me, [me] + grp + far, [unit(100, "ugho", 300, 0, owner=1)], (-6000.0, 0.0), {}, 0.0)
        self.assertEqual(x, -2000.0)

    def test_skips_group_that_is_losing_too(self):
        me = unit(1, "hfoo", 0, 0)
        grp = [unit(10 + i, "hfoo", -2000, 60 * i) for i in range(3)]
        foes = [unit(100 + i, "ugho", -2100, 60 * i, owner=1) for i in range(4)]
        self.assertEqual(C.rally_point(me, [me] + grp, foes, (-6000.0, 0.0), {}, 0.0), (-6000.0, 0.0))

    def test_no_group_goes_home(self):
        me = unit(1, "hfoo", 0, 0)
        self.assertEqual(C.rally_point(me, [me, unit(2, "hfoo", -1500, 0)], [], (-6000.0, 0.0), {}, 0.0), (-6000.0, 0.0))

    def test_group_of_pulled_units_does_not_count(self):
        me = unit(1, "hfoo", 0, 0)
        grp = [unit(10 + i, "hfoo", -2000, 60 * i) for i in range(4)]
        pulled = {u.addr: 9.0 for u in grp}
        self.assertEqual(C.rally_point(me, [me] + grp, [], (-6000.0, 0.0), pulled, 1.0), (-6000.0, 0.0))


class CritBypass(unittest.TestCase):
    def test_nearly_dead_passes_share_cap(self):
        army = [unit(i + 1, "hfoo", (i % 4) * 60, (i // 4) * 60, hp=100) for i in range(10)]
        cap = max(1, int(10 * C.PULL_SHARE))
        out = [unit(200 + i, "hfoo", -C.PULL_BACK, 0) for i in range(cap)]     # 已经撤下来 cap 个
        pulled = {u.addr: 5.0 for u in out}
        crit = unit(50, "hfoo", 30, 30, hp=40, hp_max=420)                     # 9.5%
        half = unit(51, "hfoo", 30, 60, hp=100, hp_max=420)                    # 24%：照旧受上限
        danger = [(u, 80.0, u.hp - 200.0, 2.5, 1.5) for u in (crit, half)]
        C.PULL_STATS["丝血不受上限"] = 0
        sel, capped, _ = C.pull_picks(danger, army + out + [crit, half], pulled, 1.0, False)
        self.assertEqual([t[0].addr for t in sel], [50])
        self.assertEqual(capped, 1)
        self.assertEqual(C.PULL_STATS["丝血不受上限"], 1)

    def test_crit_hero_bypasses_full_pull_quota(self):
        army = [unit(i + 1, "hfoo", (i % 4) * 60, (i // 4) * 60) for i in range(10)]
        out = [unit(200 + i, "hfoo", -C.PULL_BACK, 0) for i in range(3)]
        pulled = {u.addr: 5.0 for u in out}
        h = unit(50, "Hamg", 30, 30, hp=60, hp_max=625)
        sel, capped, _ = C.pull_picks([(h, 80.0, -100.0, 4.0, 1.5)], army + out + [h], pulled, 1.0, False)
        self.assertEqual([t[0].addr for t in sel], [h.addr])
        self.assertEqual(capped, 0)


class Sacrifice(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sacrifice.json")
            json.dump({"units": [11, 12], "wall": 1000.0}, open(p, "w", encoding="utf-8"))
            self.assertEqual(C.load_sacrifice(p, 1010.0), {11, 12})
            self.assertEqual(C.load_sacrifice(p, 1100.0), set())               # 大脑一分钟没更新 = 不在了
            self.assertEqual(C.load_sacrifice(os.path.join(d, "none.json"), 1000.0), set())


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""打玩家时限制同一时刻撤下来的人数 —— 直接调 mod_combat.pull_picks。

09-16 两局决定性的仗：000421 家门口 9 秒拉走 16 人（战损 18:0），
001312 家门口 23 秒拉走 20 人次、两个英雄 78%/82% 血也被拉（战损 21:3）。
MAX_PULL 只限"一轮 100ms"，每人锁 8 秒，于是大半个部队同时背对敌人在走。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME = 0


def unit(addr, typ, x, y, hp=100, hp_max=400):
    return Unit(index=addr, addr=addr, type=typ, owner=ME, x=float(x), y=float(y),
                hp=hp, hp_max=hp_max, order=0, order_known=True)


def danger_of(units):
    # (u, dps, left, secs, basis) —— left 越小越先拉
    return [(u, 80.0, u.hp - 200.0, 2.5, 1.5) for u in units]


class PullCap(unittest.TestCase):
    def army(self, n=12):
        return [unit(i + 1, "hfoo", (i % 4) * 60, (i // 4) * 60) for i in range(n)]

    def test_vs_players_caps_at_share_of_nearby(self):
        army = self.army(12)
        sel, capped, kept = C.pull_picks(danger_of(army[:6]), army, {}, 0.0, False)
        cap = max(1, int(12 * C.PULL_SHARE))
        self.assertEqual(len(sel), cap)
        self.assertEqual(capped, 6 - cap)
        self.assertEqual(kept, 0)

    def test_already_out_counts_against_cap(self):
        army = self.army(12)
        cap = max(1, int(12 * C.PULL_SHARE))
        # 已经撤下来的人走到 PULL_BACK 外，也还算在这场仗里
        out = [unit(100 + i, "hrif", -C.PULL_BACK, 0) for i in range(cap)]
        pulled = {u.addr: 5.0 for u in out}
        sel, capped, _ = C.pull_picks(danger_of(army[:4]), army + out, pulled, 1.0, False)
        self.assertEqual(sel, [])
        self.assertEqual(capped, 4)

    def test_vs_creeps_unchanged(self):
        army = self.army(12)
        d = danger_of(army[:6])
        sel, capped, kept = C.pull_picks(d, army, {}, 0.0, True)
        self.assertEqual(len(sel), C.MAX_PULL)
        self.assertEqual((capped, kept), (0, 0))
        self.assertEqual([t[0].addr for t in sel],
                         [t[0].addr for t in sorted(d, key=lambda t: t[2])[:C.MAX_PULL]])

    def test_hero_above_half_stays_vs_players(self):
        army = self.army(12)
        hmkg = unit(50, "Hmkg", 0, 0, hp=604, hp_max=775)     # 001312 现场
        low = unit(51, "Hamg", 30, 0, hp=200, hp_max=625)
        sel, _, kept = C.pull_picks(danger_of([hmkg, low]), army + [hmkg, low], {}, 0.0, False)
        self.assertEqual(kept, 1)
        self.assertEqual([t[0].addr for t in sel], [51])

    def test_far_army_does_not_raise_cap(self):
        army = self.army(5)
        far = [unit(200 + i, "hfoo", 6000, 6000, hp=400) for i in range(20)]
        sel, _, _ = C.pull_picks(danger_of(army), army + far, {}, 0.0, False)
        self.assertEqual(len(sel), 1)          # 附近 5 人 -> 至少 1 个

    def test_driven_rounds_never_exceed_cap(self):
        """驱动多轮（这类问题只在连续几轮里出现，静态快照测不出来）—— 拉走的人拿租约、走开 PULL_BACK，下一轮还在挨打的继续进 danger。"""
        army = self.army(16)
        cap = max(1, int(16 * C.PULL_SHARE))
        pulled = {}
        total = 0
        for r in range(80):                    # 8 秒，每轮 100ms
            now = r * C.TICK
            hot = [u for u in army if pulled.get(u.addr, 0.0) <= now and u.x > -100]
            sel, _, _ = C.pull_picks(danger_of(hot), army, pulled, now, False)
            for t in sel:
                u = t[0]
                pulled[u.addr] = now + C.LEASE
                army[army.index(u)] = unit(u.addr, u.type, u.x - C.PULL_BACK, u.y)
                total += 1
            active = sum(1 for a, until in pulled.items() if until > now)
            self.assertLessEqual(active, cap, f"第 {r} 轮同时撤下 {active} 人")
        self.assertEqual(total, cap)            # 租约没到期之前不会再补人


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""捡装备：脚边捡不起来要立刻放弃，连着几件捡不起来就是背包满了。

09-09 英雄捡地上的东西很慢：常常一个人在野外待很久，
      看上去像在犹豫要不要捡。

实测（mod_scavenge.out 最近 4000 行）：
    派出去捡 648 次、累计占用英雄 6014 秒，真捡到 27 件 —— 成功率 4%
    其中 226 次出发时距离就 <200（英雄站在东西上面还是捡不起来）
日志原文：
    去捡 stwp（距离 0，给 6 秒）-> 没捡到（走近了 0）-> 再试 -> 再试 -> 放弃
三次 18 秒，全程站着不动。

根因：两条旧规则都是**按件**算的（连续 3 次没走近 / 累计 6 次），
**没有任何一条处理"这个英雄什么都捡不了"**（背包满）——
地上 22 件时它会一件一件试过去。

⚠ 背包满不满**读不到**：物品从注册表枚举，包里的东西坐标就在英雄脚下，
  和地上的分不出来。所以用行为判 —— 这个测试钉的就是那套行为规则。
"""
import unittest

NEAR_DIST = 200.0
NEAR_FAIL_MAX = 1
FULL_STREAK = 3
FULL_COOLDOWN = 90.0


class Scav:
    """生产代码那一段的行为模型：脚边失败怎么记、什么时候判背包满。"""

    def __init__(self):
        self.ignore = set()
        self.near_fail = {}
        self.full_seen = {}
        self.full_until = {}

    def failed(self, hero, item, sent_dist, now=0.0):
        """一次租约到期还没捡到。sent_dist = 下令那一刻的距离。"""
        if sent_dist <= NEAR_DIST and item not in self.ignore:
            self.near_fail[item] = self.near_fail.get(item, 0) + 1
            if self.near_fail[item] >= NEAR_FAIL_MAX:
                self.ignore.add(item)
                self.full_seen.setdefault(hero, set()).add(item)
                if len(self.full_seen[hero]) >= FULL_STREAK:
                    self.full_until[hero] = now + FULL_COOLDOWN
                    self.full_seen[hero] = set()

    def can_send(self, hero, now):
        return now >= self.full_until.get(hero, 0.0)


class ScavengeFullTests(unittest.TestCase):
    def test_near_failure_blacklists_immediately(self):
        """★ 现场那一件：距离 0 失败一次就该拉黑，而不是再烧 12 秒。"""
        s = Scav()
        s.failed(hero=1, item=0xAAA, sent_dist=0.0)
        self.assertIn(0xAAA, s.ignore)

    def test_far_failure_does_not_blacklist(self):
        """走远路的照旧允许重试 —— 那条路是真要走的。"""
        s = Scav()
        for _ in range(3):
            s.failed(hero=1, item=0xBBB, sent_dist=900.0)
        self.assertNotIn(0xBBB, s.ignore)

    def test_three_near_failures_mean_backpack_full(self):
        """★ 连着三件不同的东西在脚边捡不起来 = 这个英雄装不下了。"""
        s = Scav()
        for item in (0xA, 0xB, 0xC):
            s.failed(hero=1, item=item, sent_dist=10.0, now=100.0)
        self.assertFalse(s.can_send(1, now=100.0))
        self.assertTrue(s.can_send(1, now=100.0 + FULL_COOLDOWN))

    def test_same_item_three_times_is_not_full(self):
        """⚠ 同一件失败三次**不算**背包满 —— 它第一次就被拉黑了，
        后两次根本不会发生。这条防的是把"一件的问题"当成"英雄的问题"。"""
        s = Scav()
        for _ in range(3):
            s.failed(hero=1, item=0xAAA, sent_dist=10.0, now=100.0)
        self.assertTrue(s.can_send(1, now=100.0))

    def test_other_hero_unaffected(self):
        """一个英雄满了，不该连累另一个。"""
        s = Scav()
        for item in (0xA, 0xB, 0xC):
            s.failed(hero=1, item=item, sent_dist=10.0, now=100.0)
        self.assertFalse(s.can_send(1, now=100.0))
        self.assertTrue(s.can_send(2, now=100.0))

    def test_cooldown_expires(self):
        """冷却到点要放它回来 —— 背包可能被用掉了一格。"""
        s = Scav()
        for item in (0xA, 0xB, 0xC):
            s.failed(hero=1, item=item, sent_dist=10.0, now=0.0)
        self.assertFalse(s.can_send(1, now=89.0))
        self.assertTrue(s.can_send(1, now=91.0))

    def test_boundary_distance(self):
        """边界：正好 200 算脚边，201 不算。"""
        s = Scav()
        s.failed(hero=1, item=0xA, sent_dist=200.0)
        self.assertIn(0xA, s.ignore)
        s2 = Scav()
        s2.failed(hero=1, item=0xB, sent_dist=201.0)
        self.assertNotIn(0xB, s2.ignore)

    def test_source_wires_the_rules(self):
        """生产代码里确实接上了这几条（别让测试和代码分叉）。"""
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath('mod_scavenge.py').read_text(encoding='utf-8')
        self.assertIn('NEAR_FAIL_MAX', src)
        self.assertIn('sent_at.get(iaddr, 1e9) <= NEAR_DIST', src)
        self.assertIn('len(full_seen.get(haddr, set())) >= FULL_STREAK', src)
        self.assertIn('now >= full_until.get(h.addr, 0.0)', src)


if __name__ == '__main__':
    unittest.main()

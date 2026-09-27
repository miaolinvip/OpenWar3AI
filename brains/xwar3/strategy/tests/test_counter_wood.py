# -*- coding: utf-8 -*-
"""09-19 第 39 局（5 号 Echo Isles）两条：

1. 伐木人数（tables.lumber_crew，四族建造表和 runtime.job_lumber 共用）：伐木 5 人，升三本后木头整局 0~150、金子堆到 6000，
   攒钱的全是要木头的 —— 金多木少时多派人砍树。
2. 反推（tactics.counter_window）：守住一波、对面强度从 55.7 掉到 28.1、我方 37~40 时因为英雄没回血全军回家，白等对面发展被推平。
   刚打赢一仗（60 游戏秒内对面强度掉了 20% 以上）且兵力够出击倍数就不等英雄；平时照旧（09-18 英雄半血去打分矿两个都死了）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables import lumber_crew  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from test_tactics import U, world  # noqa: E402


class Wood(unittest.TestCase):
    def test_three_cases(self):
        p = dict(PROFILE)                                                          # peons_lumber 5, lumber_high 400
        w = lambda g, l: SimpleNamespace(gold=g, lumber=l)                         # noqa: E731
        self.assertEqual(lumber_crew(w(300, 100), p), 5)                           # 平常
        self.assertEqual(lumber_crew(w(6107, 60), p), 10)                          # 第 39 局：金 6107 木 60 -> 多派 5 个
        self.assertEqual(lumber_crew(w(6000, 500), p), 2)                          # 木头多了照旧减半
        self.assertEqual(lumber_crew(w(700, 300), p), 5)                           # 金子没多到木头的 4 倍：不加


class Counter(unittest.TestCase):
    def setUp(self):
        self.home = U(1, "htow", 0, 0, 0)
        self.main = [U(100, "hcas", 1, 12000, 0), U(101, "hbar", 1, 12200, 0)]
        self.feet = [U(10 + i, "hfoo", 0, 100, 0) for i in range(6)]
        self.hero = U(2, "Hamg", 0, 200, 0, 250, 500)                              # 50% < 60%，在家
        self.exp = U(103, "htow", 1, 5000, 0)

    def w(self, t=600.0):
        return world([self.home, self.hero] + self.feet + [self.exp] + self.main, food=45, levels={2: 3}, t=t)

    def test_just_won_a_fight_goes_without_waiting_for_the_hero(self):
        seq = T.AttackSequence(PROFILE)
        w = self.w()
        seq.note_foe(560.0, max(10.0, w.foe_strength * 2))                        # 40 秒前对面强得多（刚被我们打掉一大半）
        seq.note_foe(600.0, w.foe_strength)
        seq.step(w, self.home)
        self.assertNotEqual(seq.state, "home")
        self.assertEqual(seq.counts.get("出击·刚打赢一仗·不等英雄回血"), 1)

    def test_opportunity_does_not_require_recent_victory(self):
        seq = T.AttackSequence(PROFILE)
        w = self.w()
        seq.note_foe(600.0, w.foe_strength)                                        # 对面强度没掉：不是反推
        it = seq.step(w, self.home)
        self.assertEqual(seq.state, "attack")
        self.assertNotIn(self.hero.addr, {u.addr for i in it if i.kind == "attack_move" for u in i.units})
        self.assertIsNone(seq.counts.get("出击·刚打赢一仗·不等英雄回血"))

    def test_window_forgets_old_peaks(self):
        seq = T.AttackSequence(PROFILE)
        seq.note_foe(500.0, 60.0)
        seq.note_foe(600.0, 30.0)                                                  # 100 秒前的峰值不算
        self.assertFalse(seq.counter_window(600.0, 30.0))
        seq.note_foe(620.0, 30.0)
        seq2 = T.AttackSequence(PROFILE)
        seq2.note_foe(1413.0, 55.7)
        seq2.note_foe(1473.0, 28.1)                                                # 第 39 局
        self.assertTrue(seq2.counter_window(1473.0, 28.1))


if __name__ == "__main__":
    unittest.main()

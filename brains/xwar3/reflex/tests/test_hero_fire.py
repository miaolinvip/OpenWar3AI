# -*- coding: utf-8 -*-
"""英雄「打了再撤、不打就回去」（mod_combat under_fire / calm_hold / hero_return_ready / home_fight）。

09-22（1-388 17 分钟敌军打家）英雄躲得太远、输出不足。改为在保命前提下尽量输出：
敌军没在打英雄时不撤；挨打了再撤，不再挨打就立即回去继续打。
回放：血法 313/600 没人打它被「实际护卫不足」拉 10 秒；大法师 825/825 在家门口被预警撤退每 7 秒拉一次，离家 3000~4000、没人打过它。
"""
import os
import sys
import tempfile
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


class UnderFire(unittest.TestCase):
    def test_hp_loss_in_window(self):
        q = deque([(10.0, 825.0), (10.5, 810.0), (11.0, 790.0)])
        self.assertTrue(C.under_fire(q, 790.0, 825.0, 11.0))          # 35 >= 3% × 825 = 24.75
        self.assertFalse(C.under_fire(q, 790.0, 825.0, 12.5))         # 窗口外的最高点不算：没在掉
        self.assertFalse(C.under_fire(deque([(10.0, 600.0), (11.0, 595.0)]), 595.0, 600.0, 11.0))  # 5 < 18

    def test_recent_focus_read(self):
        self.assertTrue(C.under_fire(None, 500.0, 500.0, 5.0, focus_k=1, focus_age=0.4))
        self.assertFalse(C.under_fire(None, 500.0, 500.0, 5.0, focus_k=2, focus_age=3.0))
        self.assertFalse(C.under_fire(None, 500.0, 500.0, 5.0))


class CalmHold(unittest.TestCase):
    def setUp(self):
        self.h = unit(1, "Hamg", hp=450, maximum=450)
        self.ghouls = [unit(100 + i, "ugho", 150, i * 40, 340, 340, 1) for i in range(3)]

    def test_isolation_waits_for_being_hit(self):
        ex = C.hero_exposure(self.h, self.ghouls, [self.h])
        self.assertEqual(ex[0], "孤军且敌方占优")
        self.assertTrue(C.calm_hold(ex, False))
        self.assertFalse(C.calm_hold(ex, True))
        self.assertFalse(C.calm_hold(ex, None), "不知道挨没挨打（旧调用方）照旧拉")

    def test_1411_alone_among_a_crowd_leaves_even_before_being_hit(self):
        """1-411 23:05:2x：部队撤走、大法师睡醒后单独在 8~13 个敌人中间，没挨打就被这条按住 3 次，6 秒被围死。"""
        crowd = [unit(100 + i, "ugho", 150 + 30 * (i % 4), 40 * (i // 4), 340, 340, 1) for i in range(8)]
        ex = C.hero_exposure(self.h, crowd, [self.h])
        self.assertIsNotNone(ex)
        self.assertFalse(C.calm_hold(ex, False), "身边 8 个：不等挨打，先撤")
        self.assertTrue(C.calm_hold(C.hero_exposure(self.h, self.ghouls, [self.h]), False), "3 个：照旧打了再撤")

    def test_low_hp_and_creeps_are_unchanged(self):
        low = unit(1, "Hamg", hp=150, maximum=450)
        ex = C.hero_exposure(low, self.ghouls, [low])
        self.assertEqual(ex[0], "低血量脱离接触")
        self.assertFalse(C.calm_hold(ex, False), "保命那条不看挨没挨打")
        creeps = [unit(100 + i, "nogm", 200, i * 30, 800, 800, 12) for i in range(2)]
        half = unit(1, "Hamg", hp=225, maximum=450)     # 09-23 起满血英雄对野怪顶着打（CREEP_TANK_FRAC）：半血才进这条
        ex = C.hero_exposure(half, creeps, [half])
        self.assertIsNotNone(ex)
        self.assertFalse(C.calm_hold(ex, False), "打野那套不动")


class ReturnReady(unittest.TestCase):
    def test_calm_hero_returns_at_55_percent(self):
        h = unit(1, "Hblm", hp=330, maximum=600)                      # 55%
        self.assertTrue(C.hero_return_ready(h, [], [h], fire=False))
        self.assertFalse(C.hero_return_ready(h, [], [h]), "不知道挨没挨打：照旧等到 75%")
        self.assertFalse(C.hero_return_ready(h, [], [h], fire=True))
        self.assertFalse(C.hero_return_ready(unit(1, "Hblm", hp=300, maximum=600), [], [h], fire=False))

    def test_calm_hero_returns_even_if_outnumbered_but_not_when_low(self):
        h = unit(1, "Hamg", hp=380, maximum=450)
        ghouls = [unit(100 + i, "ugho", 150, i * 40, 340, 340, 1) for i in range(3)]
        self.assertTrue(C.hero_return_ready(h, ghouls, [h], fire=False))
        self.assertFalse(C.hero_return_ready(h, ghouls, [h], fire=True))


class HomeFight(unittest.TestCase):
    def test_1388_archmage_at_home(self):
        """1-388 20:39:55：大法师 825/825 离家 1225，1300 内敌 10、我方 5 —— 预警撤退判据成立，但这是守家。"""
        home = (-4944.0, 2735.0)
        am = unit(1, "Hamg", -4267, 1790, 825, 825)
        foes = [unit(100 + i, "hfoo", -3800 + 60 * i, 1300, 420, 420, 1) for i in range(10)]
        ours = [am] + [unit(10 + i, "hfoo", -4300 + 40 * i, 1900, 420, 420) for i in range(4)]
        self.assertIsNotNone(C.precontact_hero_retreat(am, foes, ours))
        self.assertTrue(C.home_fight(am, foes, home, [home]))

    def test_hero_walking_home_toward_the_raid_counts_too(self):
        home = (0.0, 0.0)
        hero = unit(1, "Hamg", 3500, 0, 825, 825)                    # 离家 3500：不在家门口
        raid = [unit(100 + i, "hfoo", 1200 + 40 * i, 0, 420, 420, 1) for i in range(6)]
        self.assertTrue(C.home_fight(hero, raid, home, [home]))
        field = [unit(100 + i, "hfoo", 5500 + 40 * i, 0, 420, 420, 1) for i in range(6)]
        self.assertFalse(C.home_fight(hero, field, home, [home]), "野外遭遇照旧先跑")


class MainLoop(unittest.TestCase):
    def test_lone_hero_not_being_hit_is_not_pulled_away_from_players(self):
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop
        notes = []
        cmd = FakeCmd({1: "Hamg"})
        h = unit(1, "Hamg")
        mine = [h, unit(90, "htow", -2000, -2000, 1500, 1500)]
        foes = [unit(100 + i, "ugho", 200, i * 30, 800, 800, 1) for i in range(3)]
        snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + foes, age_ms=5)
        ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: snap)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", FakeTime()), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            C.main()
        self.assertFalse([n for n in notes if "出错" in n])
        self.assertFalse([n for n in notes if "英雄局部自保" in n], "三个食尸鬼贴着但一滴血没掉：不拉")
        self.assertTrue(any("没挨打不拉·孤军且敌方占优" in n for n in notes), "心跳里要数得到（0 = 没生效）")


if __name__ == "__main__":
    unittest.main()

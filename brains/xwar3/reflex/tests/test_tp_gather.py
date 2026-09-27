# -*- coding: utf-8 -*-
"""读回城卷轴的同时叫身边的兵靠拢（itemuse.tp_gather）。

09-22（1-388）读回城整队传送太仓促：不少单位不在范围内，只传回了几个。
改为立即开始引导，同时把英雄身边的单位叫到英雄周围集结。
战斗帧 20:35:08：下令时 1100 内 5 个、2500 内 7 个；引导的 3 秒里被回防令带着往家走，传送时 1100 内只剩 2 个水元素。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itemuse as I  # noqa: E402


def u(addr, t, x, y, hp=400.0):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=hp)


SPD = {"hfoo": 270.0, "hrif": 270.0, "hkni": 350.0, "hwat": 220.0}


class TpGather(unittest.TestCase):
    def setUp(self):
        self.hero = u(1, "Hamg", -3690, -4275)

    def moves(self, units):
        return I.tp_gather(self.hero, [self.hero] + units, SPD.get)

    def test_1388_everyone_within_walking_reach_is_called_in(self):
        # 1-388 下令那一刻的距离：水元素 155/561、步兵 600/304/875、火枪手 1277/1598、两个步兵 6000+
        near = [u(10, "hwat", -3690 + 155, -4275), u(11, "hwat", -3690, -4275 + 561),
                u(12, "hfoo", -3690 - 600, -4275), u(13, "hfoo", -3690, -4275 - 304), u(14, "hfoo", -3690 + 875, -4275),
                u(15, "hrif", -3690, -4275 + 1277), u(16, "hrif", -3690 - 1598, -4275)]
        far = [u(17, "hfoo", -3690 + 6316, -4275), u(18, "hfoo", -3690, -4275 + 6180)]
        moves, n_far = self.moves(near + far)
        self.assertEqual({m[0].addr for m in moves}, {10, 11, 12, 13, 14, 15, 16})
        self.assertEqual(n_far, 2)
        for unit_, tx, ty in moves:
            self.assertLessEqual(math.hypot(tx - self.hero.x, ty - self.hero.y), I.TP_GATHER_STOP + 1)

    def test_reach_depends_on_speed(self):
        # 步兵 270 × 5 × 0.8 = 1080 + 950 = 2030 走得到；2100 走不到；骑士 350 × 4 = 1400 + 950 = 2350
        moves, n_far = self.moves([u(10, "hfoo", -3690 + 2000, -4275), u(11, "hfoo", -3690 + 2100, -4275),
                                   u(12, "hkni", -3690 + 2300, -4275)])
        self.assertEqual({m[0].addr for m in moves}, {10, 12})
        self.assertEqual(n_far, 1)

    def test_already_close_stays_put_but_still_gets_an_order(self):
        moves, _ = self.moves([u(10, "hfoo", -3690 + 100, -4275)])
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0][1], moves[0][2]), (-3690 + 100, -4275))

    def test_dead_and_the_hero_are_skipped(self):
        moves, n_far = self.moves([u(10, "hfoo", -3690 + 300, -4275, hp=0)])
        self.assertEqual(moves, [])
        self.assertEqual(n_far, 0)


class MainLoopTp(unittest.TestCase):
    def test_reading_the_scroll_calls_the_nearby_footman_in(self):
        import tempfile
        from unittest import mock
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import mod_combat as C
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop
        from w3state import Unit

        def unit(a, typ, x=0, y=0, hp=420, maximum=420, owner=0):
            return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)

        class Cmd(FakeCmd):
            def inventory(self, u):
                return ["stwp", None, None, None, None, None]

        notes = []
        cmd = Cmd({1: "Hamg"})
        mine = [unit(1, "Hamg", 5000, 0, 825, 825), unit(2, "hfoo", 5300, 0), unit(3, "hfoo", 5000, 8000),
                unit(90, "htow", 0, 0, 1500, 1500)]
        raid = [unit(100 + i, "hfoo", 600, 40 * i, 420, 420, 1) for i in range(5)]
        snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + raid, age_ms=5)
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
        tp = [n for n in notes if n.startswith("回城带兵")]
        self.assertTrue(tp)
        self.assertIn("叫来靠拢 1 个（够不着 1 个）", tp[0])
        self.assertIn(("move", 2), cmd.calls)
        self.assertNotIn(("move", 3), cmd.calls)


if __name__ == "__main__":
    unittest.main()

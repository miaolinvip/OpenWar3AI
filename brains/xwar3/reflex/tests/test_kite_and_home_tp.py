# -*- coding: utf-8 -*-
"""远程拉扯近战（mod_combat.kite_moves，AMAI GroupMicroSystem）+ 守自家基地 / 分矿时不读回城（itemuse.TP_OWN_BASE_R）。

09-23 在自家主基地或守自家分矿时不读回城：能打就打，打不过就拉扯着打。
AMAI Jobs/FOCUSFIRE_CONTROL.eai GroupMicroSystem：带伤的远程被敌方近战贴脸（200 内、跑得不比它快）-> 背离它走开，1.8 游戏秒后回来接着打。
"""
import math
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itemuse as I  # noqa: E402
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(a, typ, x=0, y=0, hp=None, maximum=None, owner=0):
    m = maximum or {"hrif": 535, "hfoo": 420, "ogru": 700, "hkni": 835, "Hamg": 450}.get(typ, 400)
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=m if hp is None else hp, hp_max=m, order=0, order_known=True)


class Kite(unittest.TestCase):
    def test_damaged_rifleman_steps_away_from_a_grunt(self):
        r = unit(1, "hrif", 0, 0, hp=300)
        g = unit(100, "ogru", 150, 0, owner=1)
        out = C.kite_moves([r], [g], 10.0, {})
        self.assertEqual(len(out), 1)
        u, tx, ty, m = out[0]
        self.assertLess(tx, -500, "背离近战（近战在东边，往西走）")
        self.assertEqual(m.addr, 100)

    def test_who_does_not_kite(self):
        g = unit(100, "ogru", 150, 0, owner=1)
        self.assertEqual(C.kite_moves([unit(1, "hrif", hp=535)], [g], 10.0, {}), [], "满血的站着打")
        self.assertEqual(C.kite_moves([unit(2, "hfoo", hp=200)], [g], 10.0, {}), [], "近战不拉")
        self.assertEqual(C.kite_moves([unit(3, "hrif", hp=300)], [unit(101, "ogru", 400, 0, owner=1)], 10.0, {}), [],
                         "没贴脸（400）")
        knight = unit(102, "hkni", 150, 0, owner=1)
        self.assertEqual(C.kite_moves([unit(4, "hrif", hp=300)], [knight], 10.0, {}), [], "骑士跑得比火枪快：拉开也会被追上")
        self.assertEqual(C.kite_moves([unit(5, "hrif", hp=300)], [g], 10.0, {5: 9.0}), [], "刚拉过（KITE_CD）")


class HomeTp(unittest.TestCase):
    main = unit(90, "htow", -5184, 2944)
    natural = (-4352, -2624)

    def test_hero_defending_our_expansion_does_not_teleport_to_main(self):
        hero = unit(1, "Hamg", -4300, -2400)
        self.assertTrue(I.town_portal_due(hero, self.main, 6, 0), "对照：不传自家大厅坐标时照旧读")
        self.assertFalse(I.town_portal_due(hero, self.main, 6, 0, [(self.main.x, self.main.y), self.natural]),
                         "英雄在二矿边上守着：不传走")

    def test_field_hero_still_teleports(self):
        hero = unit(1, "Hamg", 1000, 0)
        self.assertTrue(I.town_portal_due(hero, self.main, 6, 0, [(self.main.x, self.main.y), self.natural]))

    def test_retreat_portal_not_at_own_base(self):
        hero = unit(1, "Hamg", -4300, -2400)
        self.assertFalse(I.retreat_portal_due(hero, (-4200, -2400), (self.main.x, self.main.y), 5, [self.natural]))
        far = unit(1, "Hamg", 2000, 0)
        self.assertTrue(I.retreat_portal_due(far, (2100, 0), (self.main.x, self.main.y), 5, [self.natural]))


class MainLoop(unittest.TestCase):
    def test_damaged_rifleman_is_pulled_back_then_released(self):
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop
        notes = []
        cmd = FakeCmd({})
        mine = [unit(1, "hrif", 3000, 0, hp=300), unit(2, "hfoo", 3100, 100), unit(90, "htow", 0, 0, 1500, 1500)]
        foes = [unit(100, "ogru", 3150, 0, owner=1)]
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
        self.assertIn(("move", 1), cmd.calls, "带伤火枪被兽人贴脸：拉开")
        self.assertNotIn(("move", 2), cmd.calls, "步兵不拉")
        beat = [n for n in notes if "远程拉扯" in n]
        self.assertTrue(beat and "拉开" in beat[-1], beat[-1:] or notes[-3:])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""打输了整队撤、离家远：英雄读回城把部队一起传回家（itemuse.retreat_portal_due + mod_combat 物品段）。

09-23 对局观察（1-420，AMAI 人族）：AMAI 打不过时会直接读回城，全队传回家。
AMAI Jobs/MICRO_HERO.eai：进攻中判定打不过、部队开始撤（isfleeing）、英雄离家 > 6000 -> TeleportHome（1100 内一起传走）。
我们原来只在家被打时读回城；1-420 12 分钟那一仗野外打输了一路走回家，13 个进场死了 12 个。
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itemuse as I  # noqa: E402


def u(addr, t, x, y, hp=400.0, hp_max=400.0):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=hp, hp_max=hp_max)


class Due(unittest.TestCase):
    def test_far_and_losing_with_an_army(self):
        hero = u(1, "Hamg", 7000, 0)
        self.assertTrue(I.retreat_portal_due(hero, (7200, 0), (0, 0), 4))
        self.assertFalse(I.retreat_portal_due(u(1, "Hamg", 5000, 0), (5200, 0), (0, 0), 4), "离家 5000：走回去")
        self.assertFalse(I.retreat_portal_due(hero, (9500, 0), (0, 0), 4), "英雄不在那片战场")
        self.assertFalse(I.retreat_portal_due(hero, (7200, 0), (0, 0), 2), "身边只剩 2 个：不值得")

    def test_shop_keeps_one_portal_from_tier_two(self):
        bag = ["phea", "sreg", "sreg", None, None, None]
        self.assertIn(("stwp", 1), I.shop_want("Hmkg"))
        self.assertEqual(I.shop_wish(bag, 1000, want=I.shop_want("Hmkg"), tier=2), "stwp")
        self.assertIsNone(I.shop_wish(bag, 1000, want=I.shop_want("Hmkg"), tier=1), "一本不卖")
        self.assertIsNone(I.shop_wish(bag, 700, want=I.shop_want("Hmkg"), tier=2), "700 - 350 < 450：常备线")


class MainLoop(unittest.TestCase):
    def test_outmatched_far_from_home_reads_the_scroll(self):
        import battlecommit as BC
        import mod_combat as C
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop
        from w3state import Unit

        def unit(a, typ, x=0, y=0, hp=420, maximum=420, owner=0):
            return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)

        class Cmd(FakeCmd):
            def inventory(self, a):
                return ["stwp", None, None, None, None, None] if a == 1 else [None] * 6

        notes = []
        cmd = Cmd({1: "Hamg"})
        mine = [unit(1, "Hamg", 7000, 0, 825, 825), unit(90, "htow", 0, 0, 1500, 1500)]
        mine += [unit(2 + i, "hfoo", 7050, 60 * i, 300, 420) for i in range(4)]
        foes = [unit(100 + i, "hkni", 7250, 50 * i, 835, 835, 1) for i in range(12)]
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
                mock.patch.object(BC.BattleCommit, "observe", lambda self, *a, **k: True), \
                mock.patch.object(C, "precontact_hero_retreat", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            # 整队撤退要「持续打输」的证据（battle_commit），这里直接给成立；英雄预警会先把英雄单独拉走（那条不升级成整队回城），关掉
            C.main()
        self.assertFalse([n for n in notes if "出错" in n])
        self.assertTrue([n for n in notes if n.startswith("遭遇战撤退")], notes[:5])
        used = [n for n in notes if n.startswith("用物品") and "stwp" in n]
        self.assertTrue(used, "打输了整队撤、离家 7000：读回城")
        self.assertTrue([n for n in notes if n.startswith("回城带兵")])


if __name__ == "__main__":
    unittest.main()

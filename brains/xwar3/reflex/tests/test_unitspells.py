# -*- coding: utf-8 -*-
"""兵种技能：对敌方高伤害单位分散放、恢复了再补（unitspells.UnitSpells + mod_combat 主循环那段）。

09-22 带技能的高级兵对敌方高伤害单位放技能，分散到不同目标、不集中在同一个单位上；
目标恢复正常后由其它施法兵接着补。1 号实例 30 个战斗帧文件：龙鹰 45 只空中锁镣 0 次、女巫只有引擎自动减速。
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unitspells as U  # noqa: E402

DPS = {"otau": 17.4, "ogru": 12.2, "owyv": 20.0, "otbr": 7.8, "ocat": 17.9, "osw2": 16.5, "Otch": 3.4}
AIR = {"owyv", "otbr"}
SUMMONS = {"osw2"}
MECH = {"ocat"}


def u(addr, t, x, y, owner=1, hp=None, hp_max=None, hero=False, order=0):
    hm = hp_max or 700.0
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), owner=owner, hp=hm if hp is None else hp,
                           hp_max=hm, is_hero=hero, order=order)


def mk():
    return U.UnitSpells(DPS, AIR, SUMMONS, MECH, 3.0)


def mana(v):
    return lambda c: v


class Spread(unittest.TestCase):
    def test_three_sorceresses_slow_three_different_units_tauren_first(self):
        s = mk()
        sors = [u(1 + i, "hsor", 0, 100 * i, owner=0) for i in range(3)]
        foes = [u(10, "ogru", 500, 0), u(11, "otau", 500, 100), u(12, "ogru", 500, 200), u(13, "ogru", 500, 300)]
        plan = s.plan(sors, foes, 100.0, mana(200.0), tech=lambda c: 0)
        self.assertEqual(len(plan), 3)
        self.assertEqual({p[1].name for p in plan}, {"slow"})
        self.assertEqual(len({p[2].addr for p in plan}), 3, "分散：三只女巫三个不同的目标")
        self.assertIn(11, {p[2].addr for p in plan}, "牛头人伤害最高，先挂")

    def test_already_slowed_is_skipped_until_it_wears_off(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 500, 0)
        gru = u(10, "ogru", 500, 100)
        (c, sp, f, m0), = s.plan([sor], [tau, gru], 100.0, mana(200.0), tech=lambda c: 0)
        s.issued(c, sp, f, 100.0, m0, True)
        sor2 = u(2, "hsor", 0, 50, owner=0)
        (c2, sp2, f2, _), = s.plan([sor2], [tau, gru], 100.3, mana(200.0), tech=lambda c: 0)
        self.assertEqual(f2.addr, 10, "牛头人已经挂着减速：第二只女巫去减速步兵")
        # 60 游戏秒 / 3 倍速 = 20 墙钟秒后减速到期：再轮到牛头人
        s.refresh({x.addr: x for x in (sor, sor2, tau, gru)}, 121.0)
        (c3, sp3, f3, _), = s.plan([sor2], [tau], 121.0, mana(200.0), tech=lambda c: 0)
        self.assertEqual(f3.addr, 11)

    def test_summons_and_out_of_range_are_skipped(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        self.assertEqual(s.plan([sor], [u(20, "osw2", 300, 0), u(21, "otau", 900, 0)], 1.0, mana(200.0)), [])


class Polymorph(unittest.TestCase):
    def test_polymorph_only_after_master_training_and_never_on_heroes(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 400, 0)
        chief = u(12, "Otch", 450, 0, hero=True)
        plan = s.plan([sor], [tau, chief], 1.0, mana(400.0), tech=lambda c: 1)
        self.assertEqual(plan[0][1].name, "slow", "女巫训练 1 级：还没有变形术")
        plan = s.plan([sor], [tau, chief], 1.0, mana(400.0), tech=lambda c: 2)
        self.assertEqual((plan[0][1].name, plan[0][2].addr), ("polymorph", 11))
        plan = s.plan([sor], [chief], 1.0, mana(400.0), tech=lambda c: 2)
        self.assertEqual(plan[0][1].name, "slow", "英雄不能变羊，只能减速")
        self.assertEqual(s.plan([sor], [tau], 1.0, mana(200.0), tech=lambda c: 2)[0][1].name, "slow", "蓝不够 220")

    def test_sheep_is_not_touched_and_recovery_frees_the_target(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 400, 0)
        (c, sp, f, m0), = s.plan([sor], [tau], 1.0, mana(400.0), tech=lambda c: 2)
        s.issued(c, sp, f, 1.0, m0, True)
        sheep = u(11, "nshe", 400, 0, hp_max=700)
        s.refresh({1: sor, 11: sheep}, 2.0)
        self.assertEqual(s.tally["变形术·看到变羊"], 1)
        self.assertEqual(s.plan([u(2, "hsor", 0, 0, owner=0)], [sheep], 2.1, mana(400.0), tech=lambda c: 2), [],
                         "羊不再挂任何东西")
        s.refresh({1: sor, 11: tau}, 5.0)                  # 被驱散：变回牛头人
        self.assertEqual(s.tally["变形术·提前恢复"], 1)
        plan = s.plan([u(2, "hsor", 0, 0, owner=0)], [tau], 5.1, mana(400.0), tech=lambda c: 2)
        self.assertEqual((plan[0][1].name, plan[0][2].addr), ("polymorph", 11), "恢复了：别的女巫接着上")

    def test_unseen_sheep_signal_falls_back_to_the_timer(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 400, 0)
        (c, sp, f, m0), = s.plan([sor], [tau], 1.0, mana(400.0), tech=lambda c: 2)
        s.issued(c, sp, f, 1.0, m0, True)
        s.refresh({1: sor, 11: tau}, 5.0)                  # 快照类型一直没变成羊：不当成恢复
        self.assertIn(("polymorph", 11), s.active)
        s.refresh({1: sor, 11: tau}, 22.0)                 # 60 / 3 = 20 秒到期
        self.assertNotIn(("polymorph", 11), s.active)


class Leash(unittest.TestCase):
    def test_dragonhawks_shackle_air_units_only_one_each(self):
        s = mk()
        hawks = [u(1, "hdhw", 0, 0, owner=0), u(2, "hdhw", 0, 100, owner=0)]
        foes = [u(10, "owyv", 400, 0), u(11, "owyv", 400, 150), u(12, "otau", 300, 0), u(13, "ocat", 300, 50)]
        plan = s.plan(hawks, foes, 1.0, mana(150.0))
        self.assertEqual({(p[1].name, p[2].addr) for p in plan}, {("magicleash", 10), ("magicleash", 11)})

    def test_broken_leash_frees_the_hawk_and_the_target(self):
        s = mk()
        hawk = u(1, "hdhw", 0, 0, owner=0)
        wyv = u(10, "owyv", 400, 0)
        (c, sp, f, m0), = s.plan([hawk], [wyv], 1.0, mana(150.0))
        s.issued(c, sp, f, 1.0, m0, True)
        hawk.order = U.BY_NAME["magicleash"].order
        self.assertEqual(s.refresh({1: hawk, 10: wyv}, 1.5), [])
        self.assertEqual(s.tally["空中锁镣·看到在引导"], 1)
        self.assertEqual(s.plan([u(2, "hdhw", 0, 0, owner=0)], [wyv], 1.6, mana(150.0)), [], "锁着的不再锁")
        hawk.order = 0x000D0012                             # 被拉走了：锁镣断了
        self.assertEqual(s.refresh({1: hawk, 10: wyv}, 2.5), [1])
        self.assertEqual(s.tally["空中锁镣·断了"], 1)
        plan = s.plan([u(2, "hdhw", 0, 0, owner=0)], [wyv], 2.6, mana(150.0))
        self.assertEqual(plan[0][2].addr, 10, "挣脱了：别的龙鹰接着锁")


class Receipts(unittest.TestCase):
    def test_no_mana_drop_means_not_cast(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 500, 0)
        (c, sp, f, m0), = s.plan([sor], [tau], 1.0, mana(200.0))
        s.issued(c, sp, f, 1.0, m0, True)
        s.receipts(1.5, mana(200.0), {1: sor, 11: tau})
        self.assertIn(("slow", 11), s.active, "还没到核对时间")
        s.receipts(2.1, mana(200.0), {1: sor, 11: tau})
        self.assertNotIn(("slow", 11), s.active)
        self.assertEqual(s.tally["减速·没放出来"], 1)

    def test_mana_drop_confirms(self):
        s = mk()
        sor = u(1, "hsor", 0, 0, owner=0)
        tau = u(11, "otau", 500, 0)
        (c, sp, f, m0), = s.plan([sor], [tau], 1.0, mana(200.0))
        s.issued(c, sp, f, 1.0, m0, True)
        s.receipts(2.1, mana(150.0), {1: sor, 11: tau})
        self.assertEqual(s.tally["减速·放出来"], 1)
        self.assertEqual(s.target_types["otau"], 1)

    def test_repeated_rejects_pause_the_spell(self):
        s = mk()
        for i in range(U.SPELL_OFF_AFTER):
            s.issued(u(1 + i, "hsor", 0, 0, owner=0), U.BY_NAME["polymorph"], u(11, "otau", 400, 0), 1.0, 400.0, False)
        plan = s.plan([u(9, "hsor", 0, 0, owner=0)], [u(11, "otau", 400, 0)], 2.0, mana(400.0))
        self.assertEqual(plan[0][1].name, "slow", "变形术连续被退（多半没研究）：整个歇 30 秒，先减速")


class MainLoop(unittest.TestCase):
    def test_sorceress_slows_the_tauren_in_the_main_loop(self):
        import mod_combat as C
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop
        from w3state import Unit

        def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
            return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)

        notes = []
        cmd = FakeCmd({})
        mine = [unit(1, "hsor", 0, 0, 325, 325), unit(90, "htow", -3000, -3000, 1500, 1500)]
        foes = [unit(100, "otau", 600, 0, 1300, 1300, 1), unit(101, "ogru", 650, 100, 700, 700, 1)]
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
        slows = [c for c in cmd.calls if c[0] == "cast_target" and c[1] == 1 and c[2] == U.BY_NAME["slow"].order]
        self.assertTrue(slows)
        self.assertEqual(slows[0][3], 100, "先减速牛头人")
        self.assertTrue(any("兵种技能 {" in n for n in notes), "心跳里要有兵种技能的计数")


if __name__ == "__main__":
    unittest.main()

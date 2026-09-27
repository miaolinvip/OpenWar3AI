# -*- coding: utf-8 -*-
"""unitmind：每个单位的处境（谁在打它 / 还能撑几秒 / 养伤状态）+ 掩护撤退 + 致命的不受团队闸。

背景（09-21，18:00 之后六个实例 1079 个战死）：被秒 36%（其中 79% 死前读得到 >=3 个敌人在打它）、
拉了没跑掉 35%、拉了又被派回去 24%（撤退是 8 秒租约）、家门口没人管 5%。
期望值都按公式算，不是照代码现状或凭感觉写。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unitmind as UM  # noqa: E402
from w3state import Unit  # noqa: E402


def unit(addr, typ, owner, x, y, hp, hp_max, hlo=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp, hp_max=hp_max,
                order=0, order_known=True, handle_lo=addr if hlo is None else hlo, handle_hi=7)


DPS = {"ogru": 12.0, "ohun": 10.0, "hfoo": 7.5, "hrif": 11.0, "hmtm": 5.0}
ARMOR = {"hfoo": 2.0, "hrif": 0.0, "ogru": 1.0}
RANGE = {"hfoo": 100.0, "hrif": 500.0, "hmtm": 1150.0, "hkni": 100.0}


class Arithmetic(unittest.TestCase):
    def test_armor_factor(self):
        self.assertAlmostEqual(UM.armor_factor(0), 1.0)
        self.assertAlmostEqual(UM.armor_factor(2), 1 - 0.12 / 1.12)
        self.assertAlmostEqual(UM.armor_factor(-2), 2 - 0.94 ** 2)

    def test_six_grunts_on_a_wounded_footman(self):
        """6 个兽人步兵（每个 12/游戏秒）砍 273 血、2 甲的步兵：72 × (1 - 0.12/1.12) = 64.29/秒 -> 4.25 秒。"""
        foot = unit(1, "hfoo", 0, 0, 0, 273, 420)
        grunts = [unit(100 + i, "ogru", 1, 50, 0, 700, 700) for i in range(6)]
        inc = UM.incoming_dps(foot, grunts, DPS.get, lambda t: ARMOR.get(t, 0.0))
        self.assertAlmostEqual(inc, 72 * (1 - 0.12 / 1.12))
        self.assertAlmostEqual(UM.ttd(foot, inc), 273 / inc)
        self.assertGreater(UM.ttd(foot, inc), UM.TTD_OUT)          # 4.25 > 3.5：还不用退
        self.assertLess(UM.ttd(unit(1, "hfoo", 0, 0, 0, 200, 420), inc), UM.TTD_OUT)   # 200 血 3.11 秒：退
        self.assertEqual(UM.ttd(foot, 0.0), math.inf)


class Board(unittest.TestCase):
    def setUp(self):
        self.ours = [unit(1, "hfoo", 0, 0, 0, 100, 420), unit(2, "hrif", 0, 2000, 0, 500, 535)]
        self.foes = [unit(100, "ogru", 1, 100, 0, 700, 700),     # 贴着残血步兵
                     unit(101, "ogru", 1, 2100, 0, 700, 700),    # 贴着健康火枪
                     unit(102, "ogru", 1, 9000, 0, 700, 700)]    # 远处，不读

    def test_due_reads_near_the_weakest_first_and_skips_far_and_fresh(self):
        b = UM.FocusBoard()
        self.assertEqual([f.addr for f in b.due(self.foes, self.ours, 10.0)], [100, 101])
        b.put(100, (1, 7), 10.0)
        self.assertEqual([f.addr for f in b.due(self.foes, self.ours, 10.2)], [101])          # 100 刚读过
        self.assertEqual([f.addr for f in b.due(self.foes, self.ours, 10.0 + UM.BOARD_REFRESH)], [100, 101])

    def test_attackers_need_matching_handle_and_fresh_read(self):
        b = UM.FocusBoard()
        b.put(100, (1, 7), 10.0)            # 在打步兵（句柄 1/7）
        b.put(101, (2, 7), 10.0)            # 在打火枪
        b.put(102, None, 10.0)              # 读不到
        self.assertEqual([f.addr for f in b.attackers(self.ours[0], self.foes, 10.5)], [100])
        self.assertEqual(b.attackers(self.ours[0], self.foes, 10.0 + UM.BOARD_FRESH + 0.1), [])
        self.assertEqual(b.unread, 1)

    def test_coverage_separates_not_read_from_zero(self):
        """「读到 0 个在打它」和「身边的敌人没读过」要分开（同一个 0 不能表示两件事）。"""
        b = UM.FocusBoard()
        self.assertEqual(b.coverage(self.ours[0], self.foes, 10.0), (0, 1))
        b.put(100, (2, 7), 10.0)
        self.assertEqual(b.coverage(self.ours[0], self.foes, 10.0), (1, 1))


class Resting(unittest.TestCase):
    def test_out_until_hp_is_back_not_for_a_fixed_time(self):
        m = UM.Minds()
        u = unit(1, "hfoo", 0, 0, 0, 80, 420)
        m.mark_out(u, 0.0, "打玩家")
        m.mark_out(u, 1.0, "打玩家")                        # 重复进不重复计
        self.assertEqual(m.counts["进养伤"], 1)
        # 100 游戏秒后血还是 40%：还在养伤（原来 8 秒租约一到就被派回去）
        u.hp = 168
        self.assertEqual(m.update({1: u}, 100.0), [])
        self.assertTrue(m.resting(u))
        u.hp = 210                                          # 50%
        self.assertEqual([x.addr for x in m.update({1: u}, 101.0)], [1])
        self.assertFalse(m.resting(u))
        self.assertEqual(m.counts["回血归队"], 1)

    def test_isolated_unit_stays_out_until_it_rejoins(self):
        """5-115 #49 骑士：孤兵回撤（92% 血）后 5 秒租约放手，被补攻击令和大脑派回去两轮，死。孤兵按「回到队伍里」归队，不按时间。"""
        m = UM.Minds()
        u = unit(1, "hkni", 0, 0, 0, 766, 835)
        m.mark_out(u, 0.0, "孤兵", kind="iso")
        self.assertEqual(m.update({1: u}, 10.0, rejoined=lambda x: False), [])   # 血满也不归队
        self.assertTrue(m.resting(u))
        self.assertEqual([x.addr for x in m.update({1: u}, 11.0, rejoined=lambda x: True)], [1])
        self.assertEqual(m.counts["孤兵归队"], 1)

    def test_isolated_unit_that_gets_hurt_becomes_a_wounded_one(self):
        m = UM.Minds()
        u = unit(1, "hkni", 0, 0, 0, 766, 835)
        m.mark_out(u, 0.0, "孤兵", kind="iso")
        u.hp = 300                                                          # 36%
        self.assertEqual(m.update({1: u}, 5.0, rejoined=lambda x: True), [])  # 回到队伍里了但血不够：继续养
        self.assertEqual(m.out[1]["kind"], "hp")

    def test_isolated_state_times_out(self):
        m = UM.Minds()
        u = unit(1, "hkni", 0, 0, 0, 800, 835)
        m.mark_out(u, 0.0, "孤兵", kind="iso")
        self.assertEqual([x.addr for x in m.update({1: u}, UM.ISO_OUT_MAX, rejoined=lambda x: False)], [1])

    def test_vanished_unit_is_dropped(self):
        m = UM.Minds()
        m.mark_out(unit(1, "hfoo", 0, 0, 0, 80, 420), 0.0, "打玩家")
        m.update({}, 1.0)
        self.assertEqual((m.out, m.counts["养伤中死亡或消失"]), ({}, 1))

    def test_heroes_and_summons_do_not_rest(self):
        self.assertFalse(UM.can_rest(unit(1, "Hamg", 0, 0, 0, 100, 500), {"hwat"}))
        self.assertFalse(UM.can_rest(unit(2, "hwat", 0, 0, 0, 100, 500), {"hwat"}))
        self.assertFalse(UM.can_rest(unit(4, "hmil", 0, 0, 0, 100, 220), {"hwat"}))    # 民兵会变回农民
        self.assertTrue(UM.can_rest(unit(3, "hfoo", 0, 0, 0, 100, 420), {"hwat"}))


class Cover(unittest.TestCase):
    def test_ranged_first_skip_busy_wounded_and_out_of_reach(self):
        victim = unit(1, "hfoo", 0, 0, 0, 90, 420)
        chaser = unit(100, "ogru", 1, 200, 0, 700, 700)
        allies = [unit(2, "hfoo", 0, 250, 150, 420, 420),      # 近战，够得着（100 + 250 >= 150）
                  unit(3, "hrif", 0, 800, 0, 535, 535),        # 远程，600 <= 500 + 250
                  unit(4, "hrif", 0, 700, 0, 100, 535),        # 远程但残血：不叫
                  unit(5, "hmtm", 0, 300, 300, 400, 400),      # 远程但在 busy（施法保护 / 撤退…）
                  unit(6, "hfoo", 0, 3000, 0, 420, 420)]       # 太远
        picks = UM.cover_picks(victim, [chaser], allies, {5}, lambda t: RANGE.get(t, 100.0))
        self.assertEqual([(a.addr, t.addr) for a, t in picks], [(3, 100), (2, 100)])

    def test_no_attackers_no_cover(self):
        self.assertEqual(UM.cover_picks(unit(1, "hfoo", 0, 0, 0, 90, 420), [], [], set(), RANGE.get), [])


class LethalBypassesTeamCap(unittest.TestCase):
    """mod_combat.pull_picks：打玩家时同时撤下的人数上限 PULL_SHARE —— 致命的（按打它的人算 TTD_CRIT 内死）不受限。"""

    def test_lethal_goes_even_when_cap_is_full(self):
        import mod_combat as C
        army = [unit(i + 1, "hfoo", 0, (i % 4) * 60, (i // 4) * 60, 300, 420) for i in range(12)]
        cap = max(1, int(12 * C.PULL_SHARE))
        # 名额已经被先撤下来的人占满（和 test_combat_pull_cap.test_already_out_counts_against_cap 同一个摆法）
        out = [unit(100 + i, "hrif", 0, -C.PULL_BACK, 0, 100, 535) for i in range(cap)]
        pulled = {u.addr: 5.0 for u in out}
        danger = [(u, 80.0, u.hp - 200.0, 2.5, 1.5) for u in army[:4]]
        sel, capped, _ = C.pull_picks(danger, army + out, pulled, 1.0, False)
        self.assertEqual((sel, capped), ([], 4))                   # 没有 lethal：一个都不拉
        lethal = {army[2].addr, army[3].addr}
        sel2, capped2, _ = C.pull_picks(danger, army + out, pulled, 1.0, False, lethal)
        self.assertEqual({t[0].addr for t in sel2}, lethal)        # 致命的两个照拉，其余照样被上限挡住
        self.assertEqual(capped2, 2)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""打不打只问 fightcall：校准胜率三档（放弃 / 观望 / 压上），回防、支援交战、分矿共用一份。

09-22 打不赢的团战总要把单位打光才停：看似撤退了，又被安排回头继续打。要求打不赢时剩余单位不再接战。
原来同一个问题三套判据互相不认（回防 AMAI 威胁、支援 amai 兵力比、分矿打光时间 × 1.5），毫秒层叫撤回防不看。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import amai  # noqa: E402
from brain import fightcall as FC  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain import winprob as W  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def army(n, t="hkni", x=0.0, y=0.0, owner=0, base=10):
    return [U(base + i, t, x + 40 * (i % 5), y + 40 * (i // 5), owner=owner) for i in range(n)]


def with_ratio(target, t_ours="hkni", t_theirs="hkni"):
    """凑一对双方单位，让 amai 兵力比最接近 target。"""
    ours = army(10, t_ours)
    best = None
    for n in range(1, 40):
        theirs = army(n, t_theirs, owner=1, base=600)
        r = amai.strength(ours) / amai.strength(theirs)
        if best is None or abs(r - target) < abs(best[2] - target):
            best = (ours, theirs, r)
    return best


class Bands(unittest.TestCase):
    def test_lines_come_from_the_calibrated_curve(self):
        self.assertEqual(FC.P_GIVEUP, W.P_GIVEUP)
        self.assertAlmostEqual(FC.P_EVEN, W.win_prob(1.0))
        self.assertLess(FC.P_GIVEUP, FC.P_EVEN)

    def test_three_bands(self):
        for target, band in ((0.4, "giveup"), (0.8, "hold"), (1.5, "commit")):
            ours, theirs, r = with_ratio(target)
            c = FC.judge(ours, theirs, where="field")
            self.assertEqual(c.band, band, (target, r, c.p))
            self.assertAlmostEqual(c.p, W.win_prob(c.ratio))

    def test_micro_retreat_signal_means_giveup(self):
        ours, theirs, _ = with_ratio(3.0)
        self.assertEqual(FC.judge(ours, theirs, lost=True).band, "giveup")
        self.assertTrue(FC.recently_lost((100.0, 0.0, 0.0), 110.0, 500.0, 0.0))
        self.assertFalse(FC.recently_lost((100.0, 0.0, 0.0), 100.0 + FC.LOST_HOLD_S + 1, 0.0, 0.0))
        self.assertFalse(FC.recently_lost((100.0, 0.0, 0.0), 110.0, FC.LOST_R + 100, 0.0))
        self.assertFalse(FC.recently_lost(None, 110.0, 0.0, 0.0))


HOME = (-5184, 2944)


def field_world(mine, foes):
    base = [U(1, "htow", *HOME), U(900, "etol", 5000, 2944, owner=1)] + [U(300 + i, "hpea", -5000, 2800) for i in range(5)]
    return build_world(base + mine + foes, 0, 770.0, 300, 300, 50, 80, levels={2: 4})


def seq():
    s = T.AttackSequence(dict(PROFILE), state="home")
    s.allow_attack = False
    return s


class SupportUsesTheCall(unittest.TestCase):
    def test_hold_with_reinforcements_falls_back_instead_of_pressing(self):
        front = army(4, "hfoo", -1300, 2400, base=10)
        body = army(3, "hfoo", -4000, 2400, base=30)
        foes = army(8, "hfoo", -900, 2400, owner=1, base=600)
        ratio = amai.strength(front + body) / amai.strength(foes)
        self.assertEqual(FC.judge(front + body, foes).band, "hold", ratio)          # 场景前提：合起来到不了压上
        w = field_world(front + body, foes)
        s = seq()
        out = s.step(w, w.my_halls[0])
        back = [i for i in out if "前面的人先退回去跟援兵汇合" in i.why]
        self.assertEqual({u.addr for u in back[0].units}, {10, 11, 12, 13}, [(i.kind, i.why) for i in out])
        self.assertEqual(s.counts.get("交战·合起来也不够压上·先退回去汇合"), 1)
        self.assertFalse([i for i in out if i.kind == "attack"])                    # 没有人被点名去打

    def test_hold_with_nobody_coming_retreats_everyone(self):
        front = army(6, "hfoo", -1300, 2400, base=10)
        foes = army(8, "hfoo", -900, 2400, owner=1, base=600)
        self.assertEqual(FC.judge(front, foes).band, "hold")
        w = field_world(front, foes)
        s = seq()
        out = s.step(w, w.my_halls[0])
        self.assertEqual([i.why for i in out], ["交战打不过，整队回家"])
        self.assertEqual(s.counts.get("交战·观望·没有援兵·整队撤"), 1)

    def test_micro_retreat_signal_stops_support_even_when_we_would_win(self):
        front = army(8, "hkni", -1300, 2400, base=10)
        foes = army(3, "hfoo", -900, 2400, owner=1, base=600)
        w = field_world(front, foes)
        s = seq()
        self.assertTrue([i for i in s.step(w, w.my_halls[0]) if i.why.startswith("支援交战")])   # 对照：没叫撤就打
        s2 = seq()
        s2.lost_at = (w.t - 5.0, -900.0, 2400.0)
        out = s2.step(w, w.my_halls[0])
        self.assertEqual([i.why for i in out], ["交战打不过，整队回家"])
        self.assertEqual(s2.counts.get("交战·毫秒层叫过撤·不再派"), 1)


class DefenseUsesTheCall(unittest.TestCase):
    def test_main_base_outnumbered_regroups_even_if_amai_says_defend(self):
        hall = U(1, "htow", 0, 0)
        mine = army(6, "hfoo", -2500, 2500, base=10)
        foes = army(8, "hfoo", 1400, 0, owner=1, base=600)
        self.assertNotEqual(FC.judge(mine, foes, where="main").band, "commit")
        w = build_world([hall] + mine + foes + [U(300 + i, "hpea", 100, 0) for i in range(5)], 0, 900.0, 300, 300, 50, 80)
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertTrue(all(i.kind == "move" for i in out))
        self.assertTrue(any(k.startswith("判仗·主基地·") for k in d.counts))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""校准层 `brain/winprob.py`：曲线、反函数、以及「推出来的阈值 == tactics 里实际在跑的值」对账。

背景见 winprob.py 的文件头。一句话：出击/撤退/打野的十几个比值原来是手调的，
09-21 用 964 个战斗帧文件、约 6000 场仗校准之后，它们全部由三条线推出来
（P_COMMIT / P_GIVEUP / LOSS_CHEAP）。这条测试守两件事：

  1. 曲线和反函数自洽（说七成就真是七成那条路上的数学部分）；
  2. `winprob.derived_thresholds()` 里每一行，和 `tactics` 里那个常量**真的相等** ——
     否则又会出现「文档说是从胜率推的，代码里其实是另一个数」。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import tactics as T      # noqa: E402
from brain import winprob as W      # noqa: E402


class Curve(unittest.TestCase):
    def test_win_prob_is_monotone_and_inside_floor_ceiling(self):
        for kind, est in (("foe", "amai"), ("foe", "power"), ("creep", "power")):
            lo, hi = W.win_floor(kind, est), W.win_ceiling(kind, est)
            self.assertLess(lo, hi)
            prev = -1.0
            for r in (0.05, 0.2, 0.5, 1.0, 2.0, 5.0, 20.0, 49.0):
                p = W.win_prob(r, kind, est)
                self.assertGreaterEqual(p, lo - 1e-9)
                self.assertLessEqual(p, hi + 1e-9)
                self.assertGreater(p, prev - 1e-9, f"{kind}/{est} 在 R={r} 处不单调")
                prev = p

    def test_loss_share_falls_as_the_ratio_rises(self):
        for kind, est in (("foe", "amai"), ("creep", "power")):
            self.assertGreater(W.loss_share(0.2, kind, est), W.loss_share(5.0, kind, est))
            self.assertGreaterEqual(W.loss_share(49.0, kind, est), W.loss_floor(kind, est) - 1e-9)

    def test_inverse_round_trips(self):
        """反函数要真的是反函数：解出来的比值代回去，正好落在那条线上。

        ⚠ 期望是**算出来**的（代回曲线），不是我觉得应该是多少。"""
        for p in (0.15, 0.25, 0.35, 0.40):
            r = W.ratio_for_win(p, "foe", "amai")
            self.assertAlmostEqual(W.win_prob(r, "foe", "amai"), p, places=6)
        for s in (0.20, 0.25, 0.30):
            r = W.ratio_for_loss(s, "creep", "power")
            self.assertAlmostEqual(W.loss_share(r, "creep", "power"), s, places=6)

    def test_unreachable_target_is_reported_not_silently_clamped(self):
        """打玩家的仗胜率有天花板。要一个到不了的把握，必须**明确**拿到一个永不成立的值，
        不能悄悄返回一个看起来很小的比值 —— 那会变成「设了七成，其实两成就放行」。"""
        ceiling = W.win_ceiling("foe", "amai")
        self.assertLess(ceiling, 0.6, "天花板都过 0.6 了，下面这条断言的前提没了")
        self.assertEqual(W.ratio_for_win(ceiling + 0.05, "foe", "amai"), W.R_HI)
        self.assertEqual(W.ratio_for_win(ceiling + 0.05, "foe", "amai", fallback=123.0), 123.0)
        # 反向：低于地板的把握任何比值都满足
        self.assertEqual(W.ratio_for_win(W.win_floor("foe", "amai") - 0.01, "foe", "amai"), W.R_LO)

    def test_fallback_coefficients_match_the_shipped_json(self):
        """兜底系数是给「json 读不到」准备的，不是第二份真相。两边必须一致。"""
        for key, fb in W.FALLBACK.items():
            live = W._FITS.get(key)
            self.assertIsNotNone(live, f"powercal.json 里没有 {key}")
            for field in ("win", "loss"):
                for a, b in zip(fb[field], live[field]):
                    self.assertAlmostEqual(a, b, places=4,
                                           msg=f"{key}.{field} 兜底和 json 不一致：改了 json 要同步 FALLBACK")

    def test_kill_times_are_the_two_halves_of_power_ratio(self):
        """设计上要的判据：对面打光我们要几秒 vs 我们打光他们要几秒。
        它和 power_ratio 必须是同一件事的两种写法，否则又是两份真相。"""
        class U:
            def __init__(self, t, hp):
                self.type, self.hp, self.hp_max, self.addr = t, hp, hp, id(self)

        mine = [U("hfoo", 420.0), U("hfoo", 420.0), U("hrif", 535.0)]
        foes = [U("ogru", 700.0), U("ohun", 350.0)]
        t_us, t_them = W.kill_times(mine, foes)
        self.assertTrue(math.isfinite(t_us) and math.isfinite(t_them))
        self.assertAlmostEqual(t_them / t_us, T.power_ratio(mine, foes), places=6)


class HurtUnitsStayInWinnableFights(unittest.TestCase):
    """「残血不怕」：判据是实测死亡率表的三档，不是拍一个血线。

    13852 个「单位 × 场」实测（进场血比 -> 死亡率 / 战力比低 / 战力比高）：
        0.90~1.00   57%   62%   47%
        0.35~0.50   62%   73%   44%
        0.20~0.35   75%   87%   66%
        0.00~0.20   84%   95%    -
    """

    class U:
        def __init__(self, t, frac, addr):
            self.type, self.addr = t, addr
            self.hp_max = 420.0
            self.hp = frac * self.hp_max

    def army(self, fracs):
        return [self.U("hfoo", f, 10 + i) for i, f in enumerate(fracs)]

    def test_above_the_soft_line_always_stays(self):
        u = self.U("hfoo", 0.40, 1)
        for ratio in (0.1, 0.5, 1.0, 5.0):
            self.assertTrue(W.should_keep(u, ratio), f"战力比 {ratio}：40% 血只比满血多 5 个点风险，不该摘")

    def test_below_the_floor_always_goes(self):
        u = self.U("hfoo", 0.10, 1)
        for ratio in (0.1, 1.0, 20.0):
            self.assertFalse(W.should_keep(u, ratio), f"战力比 {ratio}：低于 20% 死亡率 84%~95%，必须摘")

    def test_middle_band_follows_the_fight(self):
        u = self.U("hfoo", 0.27, 1)
        good = W.ratio_for_win(W.P_GIVEUP + 0.08, "foe", "power")
        bad = W.ratio_for_win(W.P_GIVEUP, "foe", "power") * 0.5
        self.assertTrue(W.should_keep(u, good, "foe"), "打得过的仗里 20~35% 血该留下")
        self.assertFalse(W.should_keep(u, bad, "foe"), "打不过的仗里 20~35% 血该摘")

    def test_an_even_fight_does_not_peel_half_the_army(self):
        """★ 第一版的坑：判据写成「撑得到我们把对面打光吗」，数学上退化成
        「血量要高于全军均值 ÷ 战力比」—— 势均力敌时会摘掉一半满血的人，
        把「残血不怕」做成了「怕得更厉害」。test_flee_local 当场抓到（8 个满血步兵对 14 个食尸鬼）。"""
        mine = self.army([1.0] * 8)
        foes = [self.U("ugho", 1.0, 100 + i) for i in range(14)]
        low, whole = W.hurt_to_peel(mine, foes, {})
        self.assertEqual(low, [], "满血的人一个都不该被摘")
        self.assertFalse(whole)

    def test_many_hurt_means_the_whole_group_pulls_back(self):
        """一次要摘走超过 PEEL_MAX 就不是「个别人残血」—— 整队撤，别一个一个喂进去。"""
        mine = self.army([0.1, 0.1, 0.1, 0.1, 1.0, 1.0])
        foes = [self.U("ugho", 1.0, 100 + i) for i in range(6)]
        low, whole = W.hurt_to_peel(mine, foes, {})
        self.assertEqual(len(low), 4)
        self.assertTrue(whole)

    def test_winning_creep_fight_peels_the_hurt_but_does_not_retreat(self):
        """5-109 7 分钟：「打野整队退：2/7 人撑不住（我们打光营地要 4 秒，营地打光我们要 25 秒）」，营地剩一丝血全队跑了。
        打得过（战力比 >= R_CREEP_HOLD）就只摘残血的，不整队撤。"""
        mine = self.army([0.1, 0.1, 1.0, 1.0, 1.0])
        camp = [self.U("nmrr", 0.05, 100)]                  # 营地只剩一只、一丝血
        ratio = __import__("brain.tactics", fromlist=["x"]).power_ratio(mine, camp, {})
        self.assertGreater(ratio, W.R_CREEP_HOLD)            # 场景前提：确实打得过
        low, whole = W.hurt_to_peel(mine, camp, {}, kind="creep")
        self.assertEqual(len(low), 2)                         # 10% 血低于地板，照样摘
        self.assertFalse(whole)

    def test_no_enemy_nearby_only_checks_the_floor(self):
        """没仗打的时候血量不是问题，但 10% 血的兵也不该跟着出门。"""
        mine = self.army([0.1, 0.25, 0.3, 1.0])
        low, whole = W.hurt_to_peel(mine, [], {})
        self.assertEqual([u.hp / u.hp_max for u in low], [0.1])
        self.assertFalse(whole)

    def test_survival_seconds_shrink_when_the_enemy_hits_harder(self):
        """「还能撑几秒」要把对面有多少输出算进来 —— 同样 30% 血，面对 3 只野怪和 12 个兽人不是一回事。
        ⚠ 它只进日志，不当闸门（见 winprob.unit_survival_s 的注释）。"""
        mine = self.army([0.3] * 4)
        few = [self.U("ogru", 1.0, 200 + i) for i in range(2)]
        many = [self.U("ogru", 1.0, 300 + i) for i in range(12)]
        self.assertGreater(W.unit_survival_s(mine[0], mine, few),
                           W.unit_survival_s(mine[0], mine, many))


class DerivedThresholdsAreWhatTacticsActuallyUses(unittest.TestCase):
    """「文档说从胜率推的、代码里其实是另一个数」是这条线上最容易犯的错，直接对账。"""

    def test_every_derived_row_matches_the_live_constant(self):
        bad = []
        for name, value, _why in W.derived_thresholds():
            mod, const = name.split(".", 1)
            self.assertEqual(mod, "tactics")
            live = getattr(T, const)
            if abs(float(live) - float(value)) > 1e-9:
                bad.append(f"{const}: 推出来 {value:.4f}，tactics 里是 {live:.4f}")
        self.assertFalse(bad, "推导和实际值对不上：\n  " + "\n  ".join(bad))

    def test_the_ladders_keep_their_hysteresis(self):
        """迟滞不是手调出来的，是两条线之间的距离 —— 但方向必须对，否则会在门槛上来回抖。"""
        self.assertGreater(T.CREEP_POWER_MIN, T.CREEP_ENGAGE_POWER_LATE, "开打门槛要严于「等久了」那一档")
        self.assertGreater(T.CREEP_ENGAGE_POWER_LATE, T.CREEP_ABORT_POWER, "「等久了」要严于撤退线")
        self.assertGreater(T.CREEP_DIRECT_POWER, T.CREEP_POWER_MIN, "「轻松营地」必须比普通门槛更高")
        self.assertGreaterEqual(T.ATTACK_MAIN_RESERVE, 1.0, "打主基地不能比打分矿还松")
        self.assertGreaterEqual(T.ATTACK_MAIN_RESERVE, T.GATHER_MAIN_COMMIT_RESERVE,
                                "集结复核要比发起时松（迟滞），不能反过来")

    def test_thresholds_are_in_a_sane_range(self):
        """兜底系数或 json 被改坏时，阈值会跑到荒唐的地方（第一版实测解出过 0.19）。
        这条不判对错，只判「还在人类能解释的范围里」。"""
        for const in ("CREEP_POWER_MIN", "CREEP_ENGAGE_POWER", "CLEAR_POWER",
                      "CREEP_ENGAGE_POWER_LATE", "CREEP_ABORT_POWER", "CREEP_DIRECT_POWER"):
            v = getattr(T, const)
            self.assertTrue(0.3 <= v <= 8.0, f"{const} = {v}，超出可解释范围")
        for const in ("ATTACK_MARGIN", "RAID_MARGIN"):
            v = getattr(T, const)
            self.assertTrue(1.0 <= v <= 3.0, f"{const} = {v}，超出可解释范围")
        self.assertTrue(0.2 <= T.FLEE_LOCAL_RATIO <= 1.0, f"FLEE_LOCAL_RATIO = {T.FLEE_LOCAL_RATIO}")

    def test_commit_line_sits_under_the_ceiling(self):
        """打玩家的仗胜率在 44% 封顶。P_COMMIT 要是定到天花板以上，出击就永远不成立 ——
        而「后期在家等死」本来就是已知问题。"""
        self.assertLess(W.P_COMMIT, W.win_ceiling("foe", "amai"),
                        "P_COMMIT 超过了实测胜率天花板，出击会永远不触发")
        self.assertLess(W.P_EXPANSION, W.P_COMMIT)
        self.assertGreater(W.P_GIVEUP, W.win_floor("foe", "amai"))
        self.assertLess(W.P_GIVEUP, W.P_EXPANSION)


if __name__ == "__main__":
    unittest.main()

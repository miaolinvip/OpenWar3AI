# -*- coding: utf-8 -*-
"""僵持破局 / 兵满钱多真的走到「决定出门」那条路（tactics.choose_target relaxed + AttackSequence.stale_since）。

09-23 1-427 18 分钟起全部单位停在家里、一直没有操作，同类情况已出现多次。
打主基地要 ATTACK_MARGIN 1.38 × ATTACK_MAIN_RESERVE 1.26 ≈ 对面整支部队的 1.73 倍；AMAI 一矿龟缩、全军守家，
两边都顶满人口时永远到不了（1-427：全局 73 对 59，出击要 81.8 对 59），野怪打光、骚扰没目标、三矿只剩对面那半边 -> 只剩「前沿驻守」。
⚠ 09-22 那版「兵满钱多降门槛」写在 _assess 里，而发起出击走的是 choose_target —— 一次都没生效（计数 0）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain import world as W  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from test_tactics import U  # noqa: E402


def world(t, foot=14, foes=12, gold=500, food=60):
    mine = [U(1, "htow", 0, 0, 0), U(2, "Hamg", 0, 300, 0, 500, 500)] + [U(10 + i, "hfoo", 0, 400, 60 * i) for i in range(foot)]
    enemy = [U(900, "hcas", 1, 10000, 0)] + [U(920 + i, "hfoo", 1, 9700, 60 * i) for i in range(foes)]   # 对面全军守在家
    return W.build_world(mine + enemy, 0, t, gold, 300, food, 100, levels={2: 3})


def seq():
    s = T.AttackSequence(dict(PROFILE))
    s.allow_attack = s.allow_creep = True
    return s


class Stale(unittest.TestCase):
    def test_1427_standing_at_home_breaks_the_stalemate(self):
        w = world(900.0)
        self.assertGreaterEqual(w.own_strength, T.STALE_RATIO * w.foe_strength, "前提：我方全局强一点")
        tgt, kind, ev = T.choose_target(w, w.my_halls[0], w.my_army + w.my_heroes)
        self.assertIsNone(tgt, "前提：按原门槛（1.38 × 1.26）打主基地判不够")
        s = seq()
        s.step(w, w.my_halls[0])
        self.assertEqual(s.state, "home")
        self.assertEqual(s.stale_since, 900.0)
        s.step(world(900.0 + T.STALE_S + 5), w.my_halls[0])
        self.assertIn(s.state, ("gather", "attack"), s.counts)          # 人都在一起时同一拍就「集结·出发」
        self.assertEqual(s.counts.get("出击·僵持破局"), 1)
        self.assertTrue(s.relaxed, "途中复评也按放宽的门槛（不然一出门就被 _assess 按 1.73 倍叫回来）")

    def test_not_when_we_are_weaker(self):
        s = seq()
        s.step(world(900.0, foes=16), world(900.0, foes=16).my_halls[0])
        s.step(world(900.0 + T.STALE_S + 5, foes=16), world(900.0, foes=16).my_halls[0])
        self.assertEqual(s.state, "home", "对面全局比我们强：照旧守")
        self.assertFalse(s.counts.get("出击·僵持破局"))


class Capped(unittest.TestCase):
    def test_food_capped_with_money_launches_on_the_first_tick(self):
        w = world(900.0, gold=1500, food=78)
        self.assertTrue(T.capped_rich(w))
        s = seq()
        s.step(w, w.my_halls[0])
        self.assertIn(s.state, ("gather", "attack"), s.counts)
        self.assertGreaterEqual(s.counts.get("出击·兵满钱多·门槛降到 1.0", 0), 1)
        self.assertFalse(s.counts.get("出击·僵持破局"), "兵满钱多第一拍就走，不用等 STALE_S")


class Commit(unittest.TestCase):
    """09-23 1-432 修复后又复发：一直不主动骚扰和进攻。

    破局确实发起了（实局两次「出击打主基地」），但 GATHER_MAX_S 之后的集结复核还按 1.205 倍（GATHER_MAIN_COMMIT_RESERVE）判，
    而破局是按 1.0 倍发起的 —— 48.1/45.6 = 1.055、55.0/49.6 = 1.109 都在这一步被取消，
    然后目标冷却 45 秒、回家、再发起。放宽出的门，复核要走同一条线。"""

    FOOT, FOES = 14, 15          # 这一组的复核比值 1.103，正落在实局那两次（1.055 / 1.109）的区间里

    def w(self, t):
        return world(t, foot=self.FOOT, foes=self.FOES)

    def test_the_band_between_the_two_gates_is_the_one_from_1432(self):
        s = seq()
        w0 = self.w(900.0)
        s.step(w0, w0.my_halls[0])
        w1 = self.w(900.0 + T.STALE_S + 5)
        s.step(w1, w1.my_halls[0])
        self.assertIsNotNone(s.target, "前提：破局发起了")
        ev = s._assess(w1, w1.my_army + w1.my_heroes, w1.my_halls[0])
        self.assertTrue(1.0 <= ev.rhs / ev.lhs < T.GATHER_MAIN_COMMIT_RESERVE,
                        f"前提：够发起（>=1.0）但不够旧的复核门槛 {T.GATHER_MAIN_COMMIT_RESERVE:.3f}，实得 {ev.rhs / ev.lhs:.3f}")

    def test_relaxed_launch_survives_the_gather_recheck(self):
        s = seq()
        w0 = self.w(900.0)
        s.step(w0, w0.my_halls[0])                                           # 第一拍：在家，记下干站起点
        w1 = self.w(900.0 + T.STALE_S + 5)
        s.step(w1, w1.my_halls[0])
        self.assertTrue(s.relaxed)
        for dt in (10.0, 20.0, T.GATHER_MAX_S + 5):                          # 等过集结上限
            w2 = self.w(w1.t + dt)
            s.step(w2, w2.my_halls[0])
        self.assertEqual(s.state, "attack", f"复核不能再把破局的这一趟取消：{s.counts}")
        self.assertEqual(s.counts.get("集结复核·按放宽门槛"), 1)
        self.assertFalse(s.counts.get("集结·到场兵力不足放弃"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""交战按「多久赶得到」算 + 打不过全员撤 + 回家/归队令不被毫秒层补攻击令抢走（tactics._support_split / _fight_retreat、control=hold）。

09-22 1-304 12:50：对方大批单位压上，我方三个小兵原地不动直至阵亡，没有按战场实时信息撤退。
要求判断打不过时所有单位全部撤退，不留任何单位白白阵亡。
  * 12:50：2 个迫击炮 + 1 个火枪手死在 (-1300,2300)，800 内敌 10~16、自己人 0~2；大脑判「敌 14 强度 24，我方 32」——
    32 是 4500 内所有人，主力 15 个在 1700~2200 外，前面几个就地接战、后面「增援接战」一路冲。
  * 14:05：掉在东边的火枪手 + 2 个水元素收到回家令（「集结」「落单归队」，租约 50）的同一秒，毫秒层补攻击令（55）把它们派回去打 13 个人。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import amai  # noqa: E402
from brain import fightcall as FC  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HOME = (-5184, 2944)


def world(units):
    base = [U(1, "htow", *HOME), U(900, "etol", 5000, 2944, owner=1)] + [U(300 + i, "hpea", -5000, 2800) for i in range(5)]
    return build_world(base + units, 0, 770.0, 300, 300, 50, 80, levels={2: 4, 3: 3})


def seq(state="home"):
    s = T.AttackSequence(dict(PROFILE), state=state)
    s.allow_attack = False
    return s


FRONT = [U(50, "hmtm", -1300, 2400), U(51, "hmtm", -1250, 2330), U(52, "hrif", -1350, 2160)]
BODY = ([U(10 + i, "hfoo", -4000, 2300 + 60 * i) for i in range(6)] + [U(20 + i, "hrif", -4100, 2300 + 60 * i) for i in range(5)]
        + [U(30 + i, "hkni", -3950, 2600 + 60 * i) for i in range(2)] + [U(2, "Hamg", -4050, 2500), U(3, "Hblm", -4050, 2560)])
FOES = ([U(600 + i, "earc", -800, 2300 + 60 * i, owner=1) for i in range(6)]
        + [U(610 + i, "edry", -750, 2350 + 60 * i, owner=1) for i in range(4)]
        + [U(620 + i, "esen", -700, 2400 + 60 * i, owner=1) for i in range(4)])


class Regroup(unittest.TestCase):
    def test_1_304_front_falls_back_to_the_body(self):
        w = world(FRONT + BODY + FOES)
        mine, fs = amai.strength(FRONT + BODY, w.levels), amai.strength(FOES, w.levels)
        self.assertGreaterEqual(mine, fs * T.SUPPORT_MIN_RATIO)          # 前提：合起来打得过（不是「全撤」那条）
        self.assertLess(amai.strength(FRONT, w.levels), fs * T.SUPPORT_MIN_RATIO)
        s = seq()
        out = s.step(w, w.my_halls[0])
        back = [i for i in out if "前面的人先退回去跟援兵汇合" in i.why]
        meet = [i for i in out if "援兵在汇合点接应" in i.why]
        self.assertEqual(len(back), 1, [(i.kind, i.why) for i in out])
        self.assertEqual({u.addr for u in back[0].units}, {50, 51, 52})
        self.assertEqual((back[0].kind, back[0].control), ("move", "skirmish"))
        self.assertEqual((meet[0].kind, meet[0].control), ("attack_move", "skirmish"))
        self.assertTrue({10, 20, 30, 2, 3} <= {u.addr for u in meet[0].units})
        self.assertLess(back[0].x, -2800)                               # 汇合点在援兵那边（-4000 往前 35%），不在前线
        self.assertGreater(back[0].x, -4100)
        self.assertFalse([i for i in out if i.kind == "attack" and 50 in {u.addr for u in i.units}])
        self.assertEqual(s.counts.get("交战·前面的人撑不到援兵·先退回去汇合"), 1)

    def test_strong_front_fights_on_the_spot(self):
        front = FRONT + [U(40 + i, "hkni", -1250, 2200 + 60 * i) for i in range(8)]
        w = world(front + BODY + FOES)
        out = seq().step(w, w.my_halls[0])
        self.assertFalse([i for i in out if "汇合" in i.why], [(i.kind, i.why) for i in out])
        self.assertTrue([i for i in out if i.why.startswith("支援交战（")])

    def test_everyone_far_marches_together(self):
        """都还没到（没有人 6 秒内打得到）：不拆，一起走过去。"""
        w = world(BODY + FOES)
        out = seq().step(w, w.my_halls[0])
        self.assertFalse([i for i in out if "汇合" in i.why])

    def test_hysteresis_keeps_regrouping_just_above_the_line(self):
        w = world(FRONT + BODY + FOES)
        s = seq()
        now_s = amai.strength(FRONT, w.levels)
        # 按真实单位凑一个敌群，让前面这 3 个人的兵力比落在「压上」线（兵力相当，fightcall.R_EVEN）的 1.0~1.25 倍之间
        grp = []
        for f in sorted(w.enemy_fighters, key=lambda u: u.addr):
            if now_s / amai.strength(grp + [f], w.levels) < FC.R_EVEN * 1.02:
                break
            grp.append(f)
        ratio = now_s / amai.strength(grp, w.levels)
        self.assertTrue(FC.R_EVEN <= ratio < FC.R_EVEN * T.SUPPORT_REGROUP_HYST, ratio)   # 场景前提
        fs = amai.strength(grp, w.levels)
        sup = FRONT + [u for u in w.my_army + w.my_heroes if u.addr in {10, 11, 20, 2, 3}]
        self.assertIsNone(s._support_split(w, sup, grp, fs))                # 没在汇合：够线就地打
        s.support_regroup_at = w.t - 1.0
        self.assertIsNotNone(s._support_split(w, sup, grp, fs))             # 正在汇合：要 1.25 倍才改回去


class RetreatEveryone(unittest.TestCase):
    def test_lost_fight_in_home_state_pulls_everyone_home(self):
        # 交战点在我方这一侧（离家 3400 > DEFENSE_LEASH_R，但离对方建筑更远）：不是「追到对方地盘」，走判打不过整队撤
        home_army = [U(10 + i, "hfoo", -5000, 2600 + 60 * i) for i in range(6)]
        leftover = [U(20, "hrif", -1800, 2700), U(21, "hwt2", -1900, 2800)]
        foes = [U(600 + i, "earc", -1500, 2600 + 60 * i, owner=1) for i in range(20)]      # 连家里 6 个都算上也打不过
        w = world(home_army + leftover + foes)
        s = seq("home")
        out = s.step(w, w.my_halls[0])
        ret = [i for i in out if i.why == "交战打不过，整队回家"]
        self.assertEqual(len(ret), 1, [(i.kind, i.why) for i in out])
        self.assertTrue({20, 21} <= {u.addr for u in ret[0].units})
        self.assertEqual(ret[0].control, "skirmish")
        self.assertEqual(s.state, "retreat")

    def test_lost_fight_while_creeping_goes_home_not_1000_from_the_creeps(self):
        army = [U(10 + i, "hfoo", 3000, 100 * i) for i in range(3)]
        creeps = [U(700, "nwlg", 2600, 0, owner=12)]                      # 野怪在家那一侧：旧的「背离野怪 1000」会往敌人那边退
        foes = [U(600 + i, "ogru", 3600, 80 * i, owner=1) for i in range(8)]
        w = world(army + creeps + foes)
        s = seq("creep")
        s.camp_ids = {700}
        s.camp = dict(x=2600.0, y=0.0, strength=4.0, units=[])
        out = s.step(w, w.my_halls[0])
        ret = [i for i in out if i.why == "交战打不过，整队回家"]
        self.assertEqual(len(ret), 1, [(i.kind, i.why) for i in out])
        self.assertEqual((ret[0].x, ret[0].y), HOME)
        self.assertTrue(any(abs(x - 2600) < 1 for x, _y, _t in s.skip_camps))


class HoldLease(unittest.TestCase):
    def test_home_and_stray_moves_outrank_the_micro_refill(self):
        """「集结」「掉队归队」「落单归队」「回家路上」走 control=hold -> 租约 strategy_hold 58 > 毫秒层补攻击令 55。"""
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            taken = []

            class Rec:
                def take(self, addr, seconds, prio=None):
                    taken.append((addr, prio))
                    return True
            b.act.claims = Rec()
            b._do(T.Intent("move", [U(20, "hrif", 0, 0)], 500.0, 0.0, why="集结", control="hold"), 900.0)
            b._do(T.Intent("move", [U(21, "hrif", 0, 0)], 500.0, 0.0, why="别的移动"), 900.0)
            self.assertIn((20, 58), taken)
            self.assertIn((21, 50), taken)

    def test_tactics_marks_them_hold(self):
        far = [U(10 + i, "hfoo", 2000, 60 * i) for i in range(6)]
        w = world(far)
        out = seq("home").step(w, w.my_halls[0])
        # 09-22 晚起没仗可打时去前沿驻守（tactics._idle_plan），不再回家集结；两种移动都要 hold（租约高过毫秒层补攻击令）
        home = [i for i in out if i.why == "集结" or "前沿" in (i.why or "")]
        self.assertEqual(home[0].control, "hold")


if __name__ == "__main__":
    unittest.main()

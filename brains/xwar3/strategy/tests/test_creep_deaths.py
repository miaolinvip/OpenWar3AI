# -*- coding: utf-8 -*-
"""打野阵亡分析（09-18 晚，6~11 号 358 局、1971 个打野阵亡）之后的三条改动：
① 实测危险营地（土著营地每回合死 3.61 个）一本时期不选；② 残血兵（< CREEP_UNIT_HP）不进打野进攻令、退到集合点；
③ 开打以后到场的人战力比 < 1 就不再点名开打（孤军添油 16.7%）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

TROLLS = ("ntkc", "ntkh", "ntkh", "ntks", "nitt")


def world(units, hall="htow"):
    base = [U(1, hall, 0, 0), U(900, "htow", 0, 16000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    base += [U(800 + i, "uabo", 0, 15500 + 80 * i, owner=1) for i in range(10)]
    return build_world(base + units, 0, 600.0, 300, 300, 30, 60, levels={2: 3})


def army(x=3000, n=10):
    return [U(2, "Hamg", x, 0)] + [U(10 + i, "hfoo", x, 80 * i) for i in range(n)]


class DangerousCamp(unittest.TestCase):
    def setUp(self):
        # 09-23 这组测「一本不打高损营地」；场景里对面一座大厅 + 10 个憎恶、第 600 秒，会先被「对面一矿暴兵不出远门」筛掉（test_hold_window）
        p = mock.patch.object(T, "hold_window", lambda w: False)
        p.start()
        self.addCleanup(p.stop)

    def test_loss_table_knows_the_troll_camp(self):
        camp = {"units": [U(700 + i, t, 4000, 60 * i, owner=12) for i, t in enumerate(TROLLS)]}
        self.assertGreaterEqual(T.camp_loss(camp), T.CREEP_LOSS_SKIP)
        self.assertEqual(T.camp_loss({"units": [U(700, "nwlt", 0, 0, owner=12)]}), 0.0)      # 表里没有的按 0

    def test_skipped_in_tier1_taken_later(self):
        trolls = [U(700 + i, t, 4000, 60 * i, owner=12) for i, t in enumerate(TROLLS)]
        w1 = world(army(n=15) + trolls)                                                       # 15 个步兵：战力比过 CREEP_POWER_MIN（10 个是 2.18）
        why = {}
        self.assertEqual([p for p in T.creep_plans(w1, w1.my_army + w1.my_heroes, (3000, 0), w1.my_halls[0], why=why)], [])
        self.assertEqual(why, {"一本打高损营地": 1}, "09-22 起说得出是哪一道筛掉的（1-291 那种「一个营地都不选」）")
        w2 = world(army(n=15) + trolls, hall="hkee")                                          # 二本以后可以去，但按预期损失扣分
        plans = T.creep_plans(w2, w2.my_army + w2.my_heroes, (3000, 0), w2.my_halls[0])
        self.assertTrue(plans and plans[0]["loss"] >= T.CREEP_LOSS_SKIP)


class LowHpBacksOff(unittest.TestCase):
    """★★ 09-21：判据从「血 < 40% 就摘」换成实测死亡率那张表的三档（brain/winprob.should_keep）。

    起因：22 局实测「打野·残血兵退到集合点」每局 **100.5 次**，同期每局只清空 6.2 个营地 ——
    摘人的次数是清营的十六倍，等于一直在添油。而 13852 个「单位 × 场」的实测说：
    进场 40% 血比满血只多 5 个点死亡风险（62% vs 57%），低于 20% 才陡增到 84%~95%。
    """

    def _creep(self, hp_frac, n_wolves=1):
        units = army(3700)
        units[3].hp = hp_frac * units[3].hp_max
        wolves = [U(700 + i, "nwlg", 4000, 80 * i, owner=12) for i in range(n_wolves)]
        w = world(units + wolves)
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since, s.creep_engaged = "creep", {u.addr for u in wolves}, w.t, True
        s.camp = dict(x=4000.0, y=0.0, strength=4.0 * n_wolves, units=[])
        return units, w, s.step(w, w.my_halls[0])

    def test_below_the_floor_backs_off(self):
        """低于 20%：不管什么仗都摘。027 局那 7 个死于打野的步兵最后血量是 18~75/420 = 4%~18%，全在这一档。"""
        units, w, out = self._creep(0.12)
        back = [i for i in out if i.why.startswith("打野·残血兵回家恢复")]
        self.assertEqual([[u.addr for u in i.units] for i in back], [[units[3].addr]])
        self.assertEqual((back[0].x, back[0].y), (w.my_halls[0].x, w.my_halls[0].y))
        self.assertFalse([i for i in out if i.kind == "attack" and any(u.addr == units[3].addr for u in i.units)])
        self.assertTrue([i for i in out if i.kind == "attack"])                                # 其余的照打

    def test_hurt_but_above_the_soft_line_keeps_fighting(self):
        """35% 以上一律留下 —— 摘走它才是添油。"""
        units, _w, out = self._creep(0.45)
        self.assertFalse([i for i in out if i.why.startswith("打野·残血兵回家恢复")])
        self.assertTrue([i for i in out if i.kind == "attack" and any(u.addr == units[3].addr for u in i.units)])

    def test_already_fighting_means_everyone_joins(self):
        """★ 09-21 几个兵和英雄停在远处，只有水元素和一个小兵在打野，其余人不上前支援（3-018 局 6 分钟）。
        根因是放行口要 `creep_engaged`（= **大脑下过开打令**），
        召唤物自己拉了仇恨时它是 False —— 又是「一个量同时表示没开打和不是我开的」。
        现在看现场：有人贴着野怪 = 这仗已经开了。"""
        wolves = [U(700 + i, "nwlg", 4000, 80 * i, owner=12) for i in range(4)]
        units = army(3700)
        # 一个水元素贴在野怪身上（CREEP_TANK_CONTACT 250 内），其余人在集合点
        we = U(50, "hwat", 4050, 0)
        w = world(units + [we] + wolves)
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since = "creep", {u.addr for u in wolves}, w.t
        s.creep_engaged = False                                        # 大脑没下过开打令
        s.camp = dict(x=4000.0, y=0.0, strength=16.0, units=[])
        out = s.step(w, w.my_halls[0])
        self.assertTrue([i for i in out if i.kind == "attack"], "已经打起来了就该全队跟上，不能站着看")
        self.assertGreaterEqual(s.counts.get("打野·已经打起来了·全队跟上", 0), 1)

    def test_middle_band_follows_the_fight_not_the_hp(self):
        """20%~35% 这一档看**这一仗打不打得过**：一只狼的营地里 25% 血照样打，
        因为同样 25% 血在打得过的仗里死亡率 66%、打不过的 87%。"""
        units, _w, out = self._creep(0.25, n_wolves=1)
        self.assertTrue([i for i in out if i.kind == "attack" and any(u.addr == units[3].addr for u in i.units)],
                        "打得过的营地里 25% 血的兵不该被摘走")


class NoLoneAttack(unittest.TestCase):
    def test_engaged_but_weak_on_site_does_not_point_attack(self):
        lone = [U(10, "hfoo", 3900, 0)]                                                        # 营地边只剩一个步兵
        others = [U(2, "Hamg", 500, 0)] + [U(20 + i, "hfoo", 500, 80 * i) for i in range(6)]   # 其余的在家（主力）
        w = world(lone + others + [U(700 + i, "nogm", 4200, 60 * i, owner=12) for i in range(3)])
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since, s.creep_engaged = "creep", {700, 701, 702}, w.t, True
        s.camp = dict(x=4200.0, y=60.0, strength=15.0, units=[])
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if i.kind == "attack"], [i.why for i in out])


if __name__ == "__main__":
    unittest.main()

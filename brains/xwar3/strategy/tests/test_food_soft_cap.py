# -*- coding: utf-8 -*-
"""人口软上限 80 + 强制攻击自己的低级兵腾人口（build.Planner unit_food_free、四族人口建筑 G.FOOD_FARM_CAP、tactics.CULL_*）。

09-22 人口尽量控制在 80 以内；必要时强制攻击自己的低级单位，腾出人口。
维护费 51~80 收入 70%、81~100 只剩 40%：过 80 每一块金子打四折。
"""
import os
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.build import Planner, Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def plan(wants, food_used, food_cap, gold=5000, lumber=5000):
    return Planner().plan(wants, lambda w: 0, gold, lumber, food_cap - food_used, lambda w: None,
                          unit_food_free=min(food_cap, G.FOOD_SOFT_CAP) - food_used)


class PlannerSoftCap(unittest.TestCase):
    def test_knight_and_hero_both_wait_at_78(self):
        """09-22 起英雄也算进 80（人口不超过 80）—— 挡下来的英雄进 food_blocked，战术层会腾人口。"""
        res = plan([Want("unit", "hkni", 1, 80), Want("hero", "Hblm", 1, 90)], 78, 90)
        self.assertEqual(res.decisions, [])
        self.assertEqual(sorted((w.code, r) for w, r in res.skipped), [("Hblm", "人口软上限"), ("hkni", "人口软上限")])
        res2 = plan([Want("hero", "Hblm", 1, 90)], 75, 90)
        self.assertEqual([d.want.code for d in res2.decisions], ["Hblm"])        # 75 + 5 = 80，正好

    def test_real_shortage_is_still_called_shortage(self):
        res = plan([Want("unit", "hkni", 1, 80)], 60, 62)              # 上限 62、用了 60：真没人口，等房子
        self.assertEqual([(w.code, r) for w, r in res.skipped], [("hkni", "人口不够")])

    def test_counts_down_within_one_round(self):
        # 一个 Want 一轮只下一个（数量靠多轮），所以用不同兵种：步兵 2 人口 76 -> 78，农民 1 -> 79，火枪手 3 过 80 挡住
        res = plan([Want("unit", "hfoo", 1, 80), Want("unit", "hpea", 1, 79), Want("unit", "hrif", 1, 78)], 76, 90)
        self.assertEqual([d.want.code for d in res.decisions], ["hfoo", "hpea"])
        self.assertEqual(res.skipped, [(res.skipped[0][0], "人口软上限")])
        self.assertEqual(res.skipped[0][0].code, "hrif")

    def test_below_the_cap_nothing_changes(self):
        res = plan([Want("unit", "hkni", 1, 80), Want("unit", "hfoo", 1, 79), Want("unit", "hrif", 1, 78)], 40, 60)
        self.assertEqual(len(res.decisions), 3)


class FarmsStopAtTheSoftCap(unittest.TestCase):
    def world(self, used, cap):
        units = [U(1, "hkee", 0, 0), U(2, "hbar", 400, 0), U(3, "halt", -400, 0)]
        units += [U(10 + i, "hhou", 600, 60 * i) for i in range(8)]
        units += [U(100 + i, "hpea", 100, 0) for i in range(15)]
        return build_world(units, 0, 900.0, 900, 500, used, cap)

    def houses(self, w):
        return [x for x in human.wants(w, dict(PROFILE)) if x.code == "hhou" and x.note == "人口快满"]

    def test_farm_wanted_below_80(self):
        self.assertTrue(self.houses(self.world(72, 78)))

    def test_no_farm_once_the_cap_reaches_80(self):
        self.assertFalse(self.houses(self.world(76, 84)))                          # 09-22 起 86 -> 80：英雄也算在 80 里

    def test_other_races_use_the_same_line(self):
        import inspect
        from brain.tables import nightelf, orc, undead
        for mod in (orc, undead, nightelf, human):
            # 09-22 晚起四族都认 world.farm_cap（平时 G.FOOD_FARM_CAP，后期 G.FOOD_FARM_CAP_LATE，runtime.job_build 设）
            self.assertIn("world.farm_cap", inspect.getsource(mod), mod.__name__)


def cull_world(food=80, cap=90, gold=600, lumber=300, hall="hkee", enemy_near=False, n_foot=6):
    units = [U(1, hall, 0, 0), U(900, "unpl", 12000, 0, owner=1)]
    units += [U(300 + i, "hpea", 100, 0) for i in range(5)]
    units += [U(10 + i, "hfoo", 500, 60 * i) for i in range(n_foot)]
    units += [U(40 + i, "hrif", 700, 60 * i) for i in range(4)]
    units += [U(2, "Hamg", 500, -200)]
    if enemy_near:
        units.append(U(950, "hfoo", 900, 100, owner=1))
    w = build_world(units, 0, 900.0, gold, lumber, food, cap, levels={2: 4})
    hurt = next(u for u in w.my_army if u.addr == 13)
    hurt.hp = 0.6 * hurt.hp_max                                          # 掉了血的那个先挑
    return w


def seq(blocked=("hkni",)):
    s = T.AttackSequence(dict(PROFILE))
    s.food_blocked = list(blocked)
    s.allow_attack = False       # 兵这么多出击层同一拍就会转去打主基地；出击中不挑新的（test_not_while_attacking 单测那条）
    return s


def force(out):
    return [i for i in out if i.kind == "force_attack"]


class Cull(unittest.TestCase):
    # 09-22 晚起强制攻击腾人口默认停用（接近 80 人口时不再强制攻击小兵，改为攒钱出高级兵，tactics.CULL_ENABLED）；
    #   机制本身还在，这里打开它测机制
    def setUp(self):
        self._cull = T.CULL_ENABLED
        T.CULL_ENABLED = True

    def tearDown(self):
        T.CULL_ENABLED = self._cull

    def test_disabled_by_default(self):
        self.assertFalse(self._cull)
        T.CULL_ENABLED = False
        w = cull_world()
        s = seq()
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if i.kind == "force_attack"])

    def test_force_attacks_the_wounded_footman(self):
        w = cull_world()
        s = seq()
        out = s.step(w, w.my_halls[0])
        f = force(out)
        self.assertEqual(len(f), 1, [i.why for i in out])
        self.assertEqual(f[0].target.addr, 13)
        killers = {u.addr for u in f[0].units}
        self.assertTrue(killers)
        self.assertLessEqual(len(killers), T.CULL_KILLERS)
        self.assertNotIn(2, killers)                                         # 英雄不动手（杀自己人不给经验，还耽误它）
        self.assertNotIn(13, killers)
        stand = [i for i in out if i.kind == "move" and [u.addr for u in i.units] == [13]]
        self.assertEqual(len(stand), 1)                                      # 被打的那个走过去等
        busy = killers | {13}
        others = [i for i in out if i not in f and i not in stand]
        self.assertFalse(any(u.addr in busy for i in others for u in i.units))
        self.assertEqual(s.counts.get("强制A低级兵·开始"), 1)
        self.assertEqual(s.cull_ids, {13})

    def test_keeps_the_same_victim(self):
        w = cull_world()
        s = seq()
        s.step(w, w.my_halls[0])
        out = s.step(replace(w, t=w.t + 2.0), w.my_halls[0])
        self.assertEqual(force(out)[0].target.addr, 13)
        self.assertEqual(s.counts.get("强制A低级兵·开始"), 1)

    def test_death_frees_food_then_waits_the_gap(self):
        w = cull_world()
        s = seq()
        s.step(w, w.my_halls[0])
        gone = [u for u in w.units if u.addr != 13]
        w2 = build_world(gone, 0, w.t + 5.0, 600, 300, 78, 90, levels={2: 4})
        out = s.step(w2, w2.my_halls[0])
        self.assertEqual(s.counts.get("强制A低级兵·腾出人口"), 1)
        self.assertFalse(force(out))                                         # CULL_GAP_S 内不挑下一个
        w3 = build_world(gone, 0, w2.t + T.CULL_GAP_S + 0.5, 600, 300, 78, 90, levels={2: 4})
        self.assertTrue(force(s.step(w3, w3.my_halls[0])))                   # 还挡着（骑士 4 人口），挑下一个

    def test_resting_victim_is_not_mistaken_for_dead(self):
        """掉到五成以下会被划进养伤、从出击层的 world.my_army 里拿掉 —— 它还活着，不能记「腾出人口」。"""
        w = cull_world()
        s = seq()
        s.step(w, w.my_halls[0])
        view = replace(w, t=w.t + 2.0, my_army=[u for u in w.my_army if u.addr != 13])
        out = s.step(view, w.my_halls[0])
        self.assertIsNone(s.counts.get("强制A低级兵·腾出人口"))
        self.assertEqual(force(out)[0].target.addr, 13)

    def test_not_when(self):
        cases = {"一本": dict(world=dict(hall="htow")), "没到上限": dict(world=dict(food=70)),
                 "买不起": dict(world=dict(gold=100)), "敌人在旁边": dict(world=dict(enemy_near=True)),
                 "没有被挡的高级兵": dict(blocked=()), "挡着的只是步兵": dict(blocked=("hfoo",))}
        for name, kw in cases.items():
            w = cull_world(**kw.get("world", {}))
            s = seq(kw.get("blocked", ("hkni",)))
            self.assertFalse(force(s.step(w, w.my_halls[0])), name)

    def test_not_while_attacking(self):
        w = cull_world()
        s = seq()
        s.allow_attack = True
        s.state = "attack"
        s._set_target(w.enemy_halls[0], "主基地")
        self.assertFalse(force(s.step(w, w.my_halls[0])))

    def test_victim_is_published_to_the_micro_layer(self):
        """被打的那个进 sacrifice.json：毫秒层看它掉血别去救。"""
        import json
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.attack.cull = dict(victim=13, type="hfoo", food=2, want="hkni", t=0.0, killers={40})
            b._publish_sacrifice()
            with open(os.path.join(d, "sacrifice.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["units"], [13])


class ForceAttackOrder(unittest.TestCase):
    def test_uses_the_attack_order_not_smart(self):
        """右键自己的兵 / 中立小动物 = 跟着走；必须是攻击令 0x000D000F。"""
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            killer, victim = U(40, "hrif", 0, 0), U(13, "hfoo", 100, 0)
            b._do(T.Intent("force_attack", [killer], victim.x, victim.y, victim, why="强制攻击测试"), 900.0)
            self.assertIn((40, 13, A.ORDER_ATTACK), b.cmd.gathers)
            b._do(T.Intent("attack", [U(41, "hrif", 0, 0)], victim.x, victim.y, victim, why="右键"), 900.0)
            self.assertIn((41, 13, A.ORDER_SMART), b.cmd.gathers)


if __name__ == "__main__":
    unittest.main()

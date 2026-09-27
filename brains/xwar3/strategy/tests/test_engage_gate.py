# -*- coding: utf-8 -*-
"""到场够强才开打（09-18 打不过仍不撤：常见一两个兵去打大营地直到阵亡；敌军打二矿时，
大法师只带一两个单位就冲上去和近 20 个敌军开战，白白送经验）。

实机 124037 局 12:47~12:49：营地强度 25，全军 20 个从 5800 外攻击移动过去，狮鹫先到、「900 内 2 个人」就点名开打；
毫秒层「在场 1 人 + 赶来 0 人 vs 4 只」拉回十几次，新大脑不读 hurt.json，每秒重下打野令又派回去。"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

CAMP = (7000.0, 0.0)


def creep_world(n_air_near=2, far_x=1500):
    units = [U(1, "htow", 0, 0), U(900, "unpl", 0, 14000, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    units += [U(10 + i, "hgry", CAMP[0] - 300, 80 * i) for i in range(n_air_near)]              # 先飞到的狮鹫
    units += [U(40 + i, "hfoo", far_x, 80 * i) for i in range(12)]                             # 地面部队还在路上
    units += [U(2, "Hamg", far_x, -200)]
    units += [U(700 + i, "nogm", CAMP[0] + 60 * i, 50, owner=12) for i in range(5)]            # 5 只 5 级 = 25
    units += [U(800 + i, "uabo", 0, 14000 + 80 * i, owner=1) for i in range(12)]               # 对面家里一支大部队（不然出击先抢走）
    return build_world(units, 0, 600.0, 300, 300, 40, 60, levels={2: 3})


def creeping(w):
    s = T.AttackSequence(dict(PROFILE))
    s.camp_ids = {700 + i for i in range(5)}
    s.camp = dict(x=CAMP[0], y=CAMP[1], strength=25.0, units=[])
    s.state, s.creep_since = "creep", w.t
    return s


class CreepGate(unittest.TestCase):
    def test_two_gryphons_do_not_engage_big_camp(self):
        w = creep_world()
        s = creeping(w)
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if i.kind == "attack"], [i.why for i in out])
        back = [i for i in out if i.kind == "move"]
        self.assertEqual({u.addr for u in back[0].units}, {10, 11})                 # 先到的狮鹫退到集合点
        self.assertLess(back[0].x, CAMP[0] - 900)
        self.assertEqual(s.counts.get("打野·等人到齐"), 1)

    def test_engage_when_army_arrives(self):
        w = creep_world(far_x=CAMP[0] - 1000)                                      # 地面部队到了集合点附近
        s = creeping(w)
        out = s.step(w, w.my_halls[0])
        hit = [i for i in out if i.kind == "attack"]
        self.assertEqual(len(hit), 1, [i.why for i in out])
        self.assertTrue(s.creep_engaged)
        # INFI 融合：全队到齐后大法师只下第一下拉怪，下一拍回到肉盾后面。
        wait = [i for i in out if i.why in ("打野·远程等肉盾接火", "打野·后排展开等肉盾接火")]
        assigned = {u.addr for i in wait + hit for u in i.units}
        self.assertIn(2, assigned)
        if 2 in {u.addr for i in hit for u in i.units}:
            self.assertEqual(s.counts.get("打野·英雄先手拉怪"), 1)

    def test_lower_bar_then_give_up_and_skip(self):
        w = creep_world()
        s = creeping(w)
        s.creep_since = w.t - T.CREEP_GIVEUP_S - 1
        out = s.step(w, w.my_halls[0])
        self.assertFalse([i for i in out if i.kind == "attack"])
        self.assertEqual(s.counts.get("打野·凑不齐人放弃"), 1)
        self.assertEqual(s.state, "retreat")
        self.assertTrue(s.creep_withdraw)
        self.assertTrue(any(i.kind == "move" for i in out))
        cand = T.pick_creep_camp(w, w.my_halls[0], s.skip_camps, rate=100.0)     # 涨兵很快 = 打不过也会等，只剩「放弃过」这一条挡
        self.assertTrue(cand is None or abs(cand["x"] - CAMP[0]) > 500)           # 放弃过的营地到期前不再选


class DefenseGate(unittest.TestCase):
    def scene(self, n_far=14, n_foes=12):
        hall = U(1, "htow", 0, 0)
        exp = U(3, "htow", 6000, 0)                                                 # 二矿
        near = [U(2, "Hamg", 5800, 300), U(10, "hfoo", 5900, 300)]                 # 大法师带一个兵先到
        far = [U(40 + i, "hkni", 500, 80 * i) for i in range(n_far)]
        foes = [U(600 + i, "ugho", 6300, 80 * i, owner=1) for i in range(n_foes)] + [U(650, "Udre", 6400, 0, owner=1)]
        peas = [U(300 + i, "hpea", 100, 0) for i in range(5)]
        return build_world([hall, exp] + near + far + foes + peas, 0, 900.0, 300, 300, 50, 80, levels={2: 3})

    # 027 局终局：压倒性劣势仍逐个接战，12 秒死 10 个。所有守军照样回来，但先在大厅背敌一侧集中。
    def test_defenders_all_go_straight_in(self):
        w = self.scene()
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in out))
        self.assertTrue({2, 10, 40} <= {u.addr for i in out for u in i.units})
        self.assertFalse([i for i in out if i.kind == "move"])

    def test_outnumbered_regroups_behind_hall(self):
        w = self.scene(n_far=2, n_foes=20)
        # 这里只验证敌军还没贴建筑时的集结。敌军在分矿右侧 300 会触发“正在拆家立刻反打”，
        # 因此把它们整体右移到仍构成城镇威胁、但还没进入建筑 700 范围的位置。
        for u in w.enemy_fighters:
            u.x += 900
        w = build_world(w.units, 0, 900.0, 300, 300, 50, 80, levels={2: 3})
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertEqual({i.kind for i in out}, {"move"})
        self.assertEqual({u.addr for i in out for u in i.units}, {u.addr for u in w.my_army + w.my_heroes})
        self.assertTrue(all(i.x < w.my_halls[1].x for i in out))         # 敌人在分矿右边，退到大厅左后方

        first = (out[0].x, out[0].y)
        # 敌军重心移动时仍使用第一次锁定的退点，不让整队每拍重新寻路。
        for u in w.enemy_fighters:
            u.x, u.y = 6000, 1600
        w2 = build_world(w.units, 0, 910.0, 300, 300, 50, 80, levels={2: 3})
        mode, out = d.step(w2, w2.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertEqual((out[0].x, out[0].y), first)

        # 09-22 起：被打的是**分矿**、4 个人对 20 个食尸鬼 + 恐惧魔王（打光时间差得远）—— 到齐了也不反打，保兵回主基地
        #   （打不赢的团战，剩余单位不再接着打；tactics.DEFENSE_HOPELESS）。主基地照旧到齐就反打，见下一条。
        for u in w.my_army + w.my_heroes:
            u.x, u.y = first
        w3 = build_world(w.units, 0, 919.0, 300, 300, 50, 80, levels={2: 3})
        mode, out = d.step(w3, w3.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertTrue(all(i.kind == "move" and "分矿守不住" in i.why for i in out))

    def test_outnumbered_at_the_main_base_still_counters_once_formed(self):
        """主基地不能放弃：打不过也是先退到大厅背后集结，到齐了反打（069~081 局：一直走 37~89 游戏秒 = 让家）。"""
        hall = U(1, "htow", 0, 0)
        mine = [U(2, "Hamg", -2500, 2500), U(10, "hfoo", -2500, 2580)] + [U(40 + i, "hkni", -2500, 2660 + 80 * i) for i in range(2)]
        foes = [U(600 + i, "ugho", 1400, 80 * i, owner=1) for i in range(20)] + [U(650, "Udre", 1500, 0, owner=1)]
        peas = [U(300 + i, "hpea", 100, 0) for i in range(5)]
        w = build_world([hall] + mine + foes + peas, 0, 900.0, 300, 300, 50, 80, levels={2: 3})
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertFalse([i for i in out if "分矿守不住" in i.why])
        first = (out[0].x, out[0].y)
        for u in w.my_army + w.my_heroes:
            u.x, u.y = first
        w2 = build_world(w.units, 0, 919.0, 300, 300, 50, 80, levels={2: 3})
        mode, out = d.step(w2, w2.my_halls[0])
        self.assertEqual(mode, "defend")
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in out))


class Signal(unittest.TestCase):
    def attack_world(self):
        units = [U(1, "htow", 0, 0), U(900, "unpl", 12000, 0, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
        units += [U(10 + i, "hfoo", 8000, 80 * i) for i in range(8)] + [U(2, "Hamg", 7900, 0)]
        units += [U(600 + i, "ugho", 12000, 3000 + 80 * i, owner=1) for i in range(6)]
        return build_world(units, 0, 900.0, 300, 300, 50, 80, levels={2: 3})

    def test_main_force_signal_retreats(self):
        w = self.attack_world()
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.start_strength = "attack", w.own_strength
        s._set_target(w.enemy_halls[0], "主基地")
        s.retreat_req = {"x": 8000, "y": 0, "n_me": 7, "n_foe": 12, "why": "胜率"}
        out = s.step(w, w.my_halls[0])
        self.assertEqual([(i.kind, i.why) for i in out], [("move", "毫秒层判这一仗输了，整队回家")])
        self.assertEqual(s.state, "retreat")

    def test_local_signal_ignored(self):
        w = self.attack_world()
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.start_strength = "attack", w.own_strength
        s._set_target(w.enemy_halls[0], "主基地")
        s.retreat_req = {"x": 8000, "y": 0, "n_me": 2, "n_foe": 5, "why": "血量输出"}
        s.step(w, w.my_halls[0])
        self.assertEqual(s.state, "attack")
        self.assertEqual(s.counts.get("毫秒层叫撤·局部（不整队撤）"), 1)
        self.assertIsNone(s.retreat_req)


class RuntimeReaders(unittest.TestCase):
    def brain(self, d, pid=4242):
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd
        cmd = FakeCmd()
        cmd.pid = pid
        return Brain(cmd, None, d)

    def test_read_hurt(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "war3-modules"))
        import w3lease
        with tempfile.TemporaryDirectory() as d:
            b = self.brain(d)
            w3lease.write_deadlines(os.path.join(d, "hurt.json"), {0x1234: time.monotonic() + 5.0}, time.monotonic(), 4242)
            self.assertEqual(b._read_hurt(), {0x1234})
            w3lease.write_deadlines(os.path.join(d, "hurt.json"), {0x1234: time.monotonic() + 5.0}, time.monotonic(), 999)
            self.assertEqual(b._read_hurt(), set())                                 # 别的局（pid 不同）不算

    def test_read_retreat_signal_once(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.brain(d)
            p = os.path.join(d, "fight_retreat.json")
            json.dump({"seq": 3, "pid": 4242, "wall": time.time(), "n_me": 5, "n_foe": 9, "x": 1, "y": 2, "why": "胜率"},
                      open(p, "w", encoding="utf-8"))
            self.assertEqual(b._read_retreat_signal()["seq"], 3)
            self.assertIsNone(b._read_retreat_signal())                              # 同一条只交一次
            json.dump({"seq": 4, "pid": 4242, "wall": time.time() - 60, "n_me": 5}, open(p, "w", encoding="utf-8"))
            self.assertIsNone(b._read_retreat_signal())                              # 太旧


if __name__ == "__main__":
    unittest.main()

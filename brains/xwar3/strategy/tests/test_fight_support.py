# -*- coding: utf-8 -*-
"""交战全员支援 / 行军等英雄 / 英雄不回家攒人。

09-18 地图中间已经交战很久，左下角两个英雄仍不去支援。实机 120354 局第 176~182 拍：狮鹫/龙鹰先飞到敌方分矿，
两个英雄落在 3000 多外，敌方 16 个单位的大部队卡在中间；主力照打分矿不回头，英雄被毫秒层预警往家拉，山丘阵亡。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

FOE_HALL = (12000, 0)


def world(units, foe_army=True):
    base = [U(1, "htow", 0, 0), U(900, "unpl", *FOE_HALL, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    if foe_army:            # 对面家里有一支兵（不然 safe 为真、全军直接冲，走不到行军/援兵那几条）
        base += [U(800 + i, "ugho", FOE_HALL[0], 3000 + 80 * i, owner=1) for i in range(6)]
    return build_world(base + units, 0, 900.0, 300, 300, 50, 80, levels={2: 3, 3: 3})


def attacking(w):
    s = T.AttackSequence(dict(PROFILE))
    hall = next(u for u in w.enemy_halls)
    s.state, s.start_strength = "attack", w.own_strength
    s._set_target(hall, "主基地")
    return s


def by_why(out, key):
    return [i for i in out if key in i.why]


class TestFightSupport(unittest.TestCase):
    def scene(self, n_foes=5, foe_type="ugho", spacing=80):
        air = [U(10 + i, "hgry", 7000, 100 * i) for i in range(6)]
        heroes = [U(2, "Hamg", 4000, 0), U(3, "Hmkg", 4000, 150),
                  U(20, "hfoo", 4000, 300), U(21, "hfoo", 4100, 350)]
        foes = [U(600 + i, foe_type, 4600, spacing * i, owner=1) for i in range(n_foes)] + [U(650, "Udre", 4700, 0, owner=1)]
        return world(air + heroes + foes)

    def test_main_force_turns_back_to_help_heroes(self):
        w = self.scene()
        out = attacking(w).step(w, w.my_halls[0])
        sup = by_why(out, "支援交战")
        self.assertTrue(sup, [(i.kind, i.why) for i in out])
        addrs = {u.addr for i in sup for u in i.units}
        self.assertTrue({2, 3, 10, 15} <= addrs, addrs)                  # 英雄和狮鹫都去
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in sup))
        self.assertTrue(all(abs(i.x - 4620) < 500 for i in sup))
        self.assertFalse(by_why(out, "回家"), [i.why for i in out])

    def test_hopeless_fight_no_support(self):
        w = self.scene(n_foes=40, foe_type="uabo", spacing=20)           # 明确碾压而非单帧轻度劣势。
        s = attacking(w)
        out = s.step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "支援交战"), [i.why for i in out])
        # 09-21 起一场**新的**支援在开打前就判（「开打前就打不过·不派」），已经在打的才走持续判输（「合起来也打不过」）
        self.assertEqual(s.counts.get("交战·合起来也打不过", 0) + s.counts.get("交战·开打前就打不过·不派", 0), 1)
        self.assertEqual([(i.kind, i.why) for i in out], [("move", "交战打不过，整队回家")])   # 进攻中：整队撤，不各打各的
        self.assertEqual(s.state, "retreat")

    def test_lone_leftover_is_not_sent_into_a_fight_it_already_loses(self):
        """3-037 29:45：主力被叫撤回家后，「支援交战（敌 1 个，强度 5，我方 2）·增援接战」把留在敌方分矿边的
        一个兵派回去打，最后送死。比值 0.4 高于「立刻判输」的 0.35、低于 SUPPORT_MIN_RATIO —— 原来要持续输 6 秒才算输。
        一场新的支援必须当下就够线。"""
        from brain import amai
        home_army = [U(10 + i, "hfoo", 0, 200 + 60 * i) for i in range(6)]
        leftover = U(20, "hfoo", 9000, 0)
        foes = [U(600, "ogru", 9300, 0, owner=1), U(601, "ohun", 9300, 80, owner=1)]
        w = world(home_army + [leftover] + foes, foe_army=False)
        ratio = amai.strength([leftover], w.levels) / amai.strength(foes, w.levels)
        self.assertTrue(0.35 <= ratio < T.SUPPORT_MIN_RATIO, ratio)          # 场景前提：正好是原来「先派进去」那一档
        s = T.AttackSequence(dict(PROFILE), state="home")
        out = s.step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "支援交战"), [(i.kind, i.why) for i in out])
        # 09-22 起这个兵在对方地盘上（离对方大厅 12000 比离家近），先被「追到对方地盘·收回来」接住 —— 同样是不送
        self.assertEqual(s.counts.get("交战·开打前就打不过·不派", 0) + s.counts.get("交战·追到对方地盘·收回来", 0), 1)

    def test_small_front_group_waits_for_the_rest_before_pushing(self):
        """1-280 17:50：「出击打主基地：3/3 个」—— 骑士 #41、水元素 #40 和 1 个兵冲进对面基地，后面的兵和英雄在 3000 外追不上。"""
        front = [U(41, "hkni", 9000, 0), U(40, "hwt2", 9050, 60), U(42, "hfoo", 9000, 120)]
        # 只放兵：带英雄会先走「等英雄跟上」那条（行军·等英雄，12 秒超时后主力照走 —— 超时之后才轮到这里）
        back = [U(10 + i, "hfoo", 6000, 80 * i) for i in range(5)]
        s = T.AttackSequence(dict(PROFILE), state="attack")
        s.hero_ids, s.safe, s.now = {2, 3}, False, 900.0
        tgt = U(900, "unpl", 12000, 0, owner=1)
        out = s._push(front + back, 9016.0, 60.0, 12000.0, 0.0, tgt, "出击打主基地")
        self.assertFalse([i for i in out if i.kind == "attack_move" and 41 in {u.addr for u in i.units}],
                         [(i.kind, i.why) for i in out])
        self.assertEqual(s.counts.get("出击·前面人太少先停下等汇合"), 1)
        # 对照：前面的人占多数 —— 照常冲
        s2 = T.AttackSequence(dict(PROFILE), state="attack")
        s2.hero_ids, s2.safe, s2.now = {2, 3}, False, 900.0
        out2 = s2._push(front + back[:2], 9016.0, 60.0, 12000.0, 0.0, tgt, "出击打主基地")
        self.assertTrue([i for i in out2 if i.kind == "attack_move" and 41 in {u.addr for u in i.units}])

    def test_stray_unit_does_not_redirect_army(self):
        air = [U(10 + i, "hgry", 7000, 100 * i) for i in range(6)]
        heroes = [U(2, "Hamg", 4000, 0), U(3, "Hmkg", 4000, 150)]
        w = world(air + heroes + [U(600, "ugho", 4600, 0, owner=1)])     # 1 个食尸鬼，强度 2 < FIGHT_MIN
        out = attacking(w).step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "支援交战"))

    def test_two_isolated_heroes_regroup_instead_of_starting_support_fight(self):
        """1-215：主力在基地，两英雄在中场，敌先头进入视野时不能让英雄单独攻击移动。"""
        body = [U(10 + i, "hfoo", 0, i * 60) for i in range(6)]
        heroes = [U(2, "Hamg", 4000, 0), U(3, "Hmkg", 4000, 140)]
        foes = [U(600 + i, "ugho", 4600, i * 70, owner=1) for i in range(3)]
        w = world(body + heroes + foes)
        s = T.AttackSequence(dict(PROFILE), state="home")
        out = s.step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "支援交战（"), [(i.kind, i.why) for i in out])
        regroup = by_why(out, "护卫未到")
        self.assertEqual(len(regroup), 1, [(i.kind, i.why) for i in out])
        self.assertEqual(regroup[0].kind, "move")
        self.assertEqual({u.addr for u in regroup[0].units}, {2, 3})
        self.assertLess(regroup[0].x, 1000)  # 往基地主力走，而不是向 4600 的敌先头接战
        self.assertEqual(s.counts.get("交战·英雄无护卫先回主力"), 1)

    def test_creeping_army_meets_enemy_army(self):
        army = [U(10 + i, "hfoo", 3000, 100 * i) for i in range(6)] + [U(2, "Hamg", 3000, -100), U(3, "Hmkg", 3000, 700)]
        creeps = [U(700, "nwlg", 3300, 0, owner=12)]
        foes = [U(600 + i, "ugho", 3900, 80 * i, owner=1) for i in range(3)]
        w = world(army + creeps + foes)
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids = "creep", {700}
        s.camp = dict(x=3300.0, y=0.0, strength=4.0, units=[])
        out = s.step(w, w.my_halls[0])
        self.assertEqual([i.kind for i in out], ["attack_move"], [i.why for i in out])
        self.assertIn("支援交战", out[0].why)
        self.assertEqual(s.camp_ids, {700})                              # 打完敌人接着打野


class TestMarchCohesion(unittest.TestCase):
    def test_air_waits_for_heroes(self):
        air = [U(10 + i, "hgry", 5000, 100 * i) for i in range(6)]
        heroes = [U(2, "Hamg", 2500, 0), U(3, "Hmkg", 2500, 150)]
        w = world(air + heroes)
        s = attacking(w)
        out = s.step(w, w.my_halls[0])
        wait = by_why(out, "等英雄跟上")
        self.assertEqual(len(wait), 1, [(i.kind, i.why) for i in out])
        self.assertEqual({u.addr for u in wait[0].units}, set(range(10, 16)))
        self.assertAlmostEqual(wait[0].x, 3000, delta=150)               # 英雄往目标方向 500
        go = [i for i in out if any(u.addr == 3 for u in i.units)]
        self.assertTrue(go and go[0].kind == "attack_move" and go[0].x == FOE_HALL[0])
        self.assertEqual(s.counts.get("行军·等英雄跟上"), 1)

    def test_no_wait_when_heroes_keep_up(self):
        air = [U(10 + i, "hgry", 5000, 100 * i) for i in range(6)]
        heroes = [U(2, "Hamg", 4200, 0), U(3, "Hmkg", 4200, 150)]
        w = world(air + heroes)
        out = attacking(w).step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "等英雄跟上"))

    def test_no_wait_near_target(self):
        air = [U(10 + i, "hgry", 10500, 100 * i) for i in range(6)]
        heroes = [U(2, "Hamg", 7500, 0), U(3, "Hmkg", 7500, 150)]
        w = world(air + heroes)
        out = attacking(w).step(w, w.my_halls[0])
        self.assertFalse(by_why(out, "等英雄跟上"))

    def test_remote_hero_joins_instead_of_going_home(self):
        air = [U(10 + i, "hgry", 7000, 100 * i) for i in range(6)]
        w = world(air + [U(3, "Hmkg", 500, 0)])                          # 刚复活，离主力 6500（> HERO_WAIT_MAX 不等）
        s = attacking(w)
        out = s.step(w, w.my_halls[0])
        hero = [i for i in out if any(u.addr == 3 for u in i.units)]
        self.assertEqual(len(hero), 1, [(i.kind, i.why) for i in out])
        self.assertEqual(hero[0].kind, "attack_move")
        self.assertNotIn("回家", hero[0].why)
        self.assertEqual(s.counts.get("出击·英雄赶去跟主力"), 1)


if __name__ == "__main__":
    unittest.main()

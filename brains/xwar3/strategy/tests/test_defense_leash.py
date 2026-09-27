# -*- coding: utf-8 -*-
"""回防：只打家门口的敌人、打不过不送、认毫秒层叫撤、英雄行军殿后；出击层 home 状态追到对方地盘收回来。

09-22 1-317：16:50 两个单位在敌军基地里一直打、不撤退，直至阵亡；21 分三个英雄相继阵亡；
打不赢的团战，剩余单位看似撤退了，又被安排回头继续打。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import fightcall as FC  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

MAIN = U(1, "htow", -5184, 2944)
EXPO = U(3, "htow", -4864, -3200)
FOE_MAIN = U(900, "unpl", 4672, 2944, owner=1)


def world(mine, foes, t=900.0):
    peas = [U(300 + i, "hpea", -5000, 2800) for i in range(5)]
    return build_world([MAIN, EXPO, FOE_MAIN] + peas + mine + foes, 0, t, 300, 300, 50, 80, levels={2: 4})


ARMY = [U(10 + i, "hkni", -4600, -2600 + 60 * i) for i in range(8)] + [U(2, "Hamg", -4700, -2700)]
DOOR = [U(600 + i, "ugho", -4300, -3100 + 60 * i, owner=1) for i in range(8)] + [U(650, "Udre", -4200, -3000, owner=1)]   # 分矿门口、打得过


class Leash(unittest.TestCase):
    def test_fleeing_heroes_out_of_our_land_are_not_chased(self):
        """1-317 15:55/16:09「回防·集火核心 -> 巫妖 / 恐惧魔王」：敌军主体还在分矿 2000 以内（威胁 43~55），
        它的英雄在往回跑，我方被分去集火英雄的骑士、狮鹫贴着一路追到 (2000~2600,-2300) 被围死。"""
        body = [U(600 + i, "ugho", -3200, -3400 + 60 * i, owner=1) for i in range(10)]    # 离分矿 1700
        heroes = [U(650, "Udre", -1900, -3000, owner=1), U(651, "Ulic", -1950, -2900, owner=1)]   # 离分矿 3000
        chasers = [U(20, "hkni", -2050, -3000), U(21, "hkni", -2100, -2950)]
        w = world(ARMY + chasers, body + heroes)
        mode, out = T.Defense().step(w, w.my_halls[0])
        self.assertIn(mode, ("defend", "toostrong"), "前提：威胁够回防出动")
        far = [i for i in out if i.target is not None and i.target.addr in (650, 651)]
        self.assertFalse(far, [(i.kind, i.why, i.target.type) for i in far])
        for i in out:
            if i.kind == "attack_move":
                self.assertLess(min(math.hypot(i.x - b.x, i.y - b.y) for b in (MAIN, EXPO)), T.DEFENSE_LEASH_R + 1)

    def test_enemy_at_our_door_is_fought(self):
        foes = DOOR
        w = world(ARMY, foes)
        mode, out = T.Defense().step(w, w.my_halls[0])
        self.assertEqual(mode, "defend")
        self.assertTrue([i for i in out if i.kind in ("attack", "attack_move")])


class NoFeeding(unittest.TestCase):
    def test_hopeless_expansion_is_given_up_to_save_the_army(self):
        """1-317 21 分：我方 51.5 对 71~75，照样拉全军去分矿迎 20 多个亡灵。"""
        foes = [U(600 + i, "ugho", -4300, -3100 + 40 * i, owner=1) for i in range(20)] + \
               [U(650, "Ucrl", -4200, -3100, owner=1), U(651, "Udre", -4200, -3000, owner=1), U(652, "Ulic", -4200, -2900, owner=1)]
        mine = [U(10 + i, "hrif", -5000, 2400 + 60 * i) for i in range(5)] + [U(2, "Hamg", -5000, 2300), U(4, "Hpal", -5000, 2200)]
        w = world(mine, foes)
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "toostrong")
        self.assertTrue(out and all(i.kind == "move" and "分矿守不住" in i.why for i in out), [(i.kind, i.why) for i in out])
        self.assertTrue(all(math.hypot(i.x - MAIN.x, i.y - MAIN.y) < 1 for i in out))
        self.assertEqual(d.counts.get("回防·分矿守不住·保兵回主基地"), 1)

    def test_hopeless_expansion_units_already_in_contact_fight_there(self):
        """1-383：分矿守不住时已经贴着敌人的往主基地跑，一路被追着打死（1 号实例 128 次里 75% 25 秒内死）。
        贴上的（800 内有敌方兵）就地打，还没贴上的照旧回主基地。"""
        foes = [U(600 + i, "ugho", -4300, -3100 + 40 * i, owner=1) for i in range(20)] + \
               [U(650, "Ucrl", -4200, -3100, owner=1), U(651, "Udre", -4200, -3000, owner=1)]
        near = [U(10 + i, "hrif", -4700, -3100 + 60 * i) for i in range(3)]          # 离食尸鬼 400：贴上了
        far = [U(20 + i, "hrif", -5000, 2400 + 60 * i) for i in range(3)] + [U(2, "Hamg", -5000, 2300)]
        w = world(near + far, foes)
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "toostrong")
        moved = {u.addr for i in out if i.kind == "move" for u in i.units}
        fought = {u.addr for i in out if i.kind in ("attack", "attack_move") for u in i.units}
        self.assertEqual(fought, {u.addr for u in near})
        self.assertEqual(moved, {u.addr for u in far})
        self.assertTrue(all(i.control == "skirmish" for i in out))
        self.assertEqual(d.counts.get("回防·分矿守不住·已贴上的就地打"), 1)

    def test_micro_retreat_signal_is_respected_at_the_expansion(self):
        foes = DOOR
        w = world(ARMY, foes)
        d = T.Defense()
        mode, out = d.step(w, w.my_halls[0])
        self.assertEqual(mode, "defend")                                               # 对照：没叫撤时照打
        d2 = T.Defense()
        d2.lost = (w.t - 5.0, -4300.0, -3000.0)
        mode2, out2 = d2.step(w, w.my_halls[0])
        self.assertEqual(mode2, "toostrong")
        self.assertTrue(all("毫秒层叫过撤" in i.why for i in out2))
        d3 = T.Defense()
        d3.lost = (w.t - FC.LOST_HOLD_S - 1, -4300.0, -3000.0)                          # 过期了
        self.assertEqual(d3.step(w, w.my_halls[0])[0], "defend")


class HeroesTrail(unittest.TestCase):
    def test_hero_walks_behind_the_front_on_a_long_march(self):
        front = [U(10 + i, "hkni", -3000, 0 + 60 * i) for i in range(4)]
        hero = U(2, "Hpal", -2900, 100)
        its = [T.Intent("attack_move", front + [hero], -4300, -3000, why="回防·增援接战")]
        out = T.heroes_trail(its, {2}, -4300.0, -3000.0)
        h = [i for i in out if 2 in {u.addr for u in i.units}]
        self.assertEqual(len(h), 1)
        self.assertIn("英雄跟在前排后面", h[0].why)
        cx = sum(u.x for u in front) / 4
        cy = sum(u.y for u in front) / 4
        self.assertGreater(math.hypot(h[0].x + 4300, h[0].y + 3000), math.hypot(cx + 4300, cy + 3000))   # 比前排离敌人远

    def test_close_to_the_fight_nothing_changes(self):
        front = [U(10 + i, "hkni", -4300, -2500 + 60 * i) for i in range(4)]
        its = [T.Intent("attack_move", front + [U(2, "Hpal", -4300, -2400)], -4300, -3000, why="回防·增援接战")]
        self.assertEqual(T.heroes_trail(its, {2}, -4300.0, -3000.0), its)


class SupportLeash(unittest.TestCase):
    def test_home_state_contact_in_enemy_land_comes_back(self):
        chasers = [U(20, "hkni", 2600, 2500), U(21, "hgry", 2400, 2450)]
        foes = [U(600 + i, "ugho", 2800, 2400 + 60 * i, owner=1) for i in range(3)]
        w = world([U(10 + i, "hkni", -5000, 2400 + 60 * i) for i in range(6)] + chasers, foes)
        s = T.AttackSequence(dict(PROFILE), state="home")
        s.allow_attack = False
        out = s.step(w, w.my_halls[0])
        back = [i for i in out if i.why == "追到对方地盘了，不打，收回来"]
        self.assertEqual({u.addr for u in back[0].units}, {20, 21})
        self.assertEqual(back[0].control, "skirmish")
        self.assertFalse([i for i in out if i.why.startswith("支援交战")])


if __name__ == "__main__":
    unittest.main()

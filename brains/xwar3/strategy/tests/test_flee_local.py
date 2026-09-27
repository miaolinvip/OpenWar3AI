# -*- coding: utf-8 -*-
"""进攻中当地打不过就撤 + 英雄血不够任何状态都不发起出击（09-18 打不过时中途不撤，一直打到全军覆没）。

125038 局第 85~94 拍：打野中转去打敌方分矿（山丘 354/700），敌人 10 秒内从 6 个涨到 19 个，
AMAI 原版撤退要敌方 > 我方约 2.7 倍，16 个打到剩 10 个、两个英雄阵亡才撤。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


def world(n_foes, hero_hp=None):
    units = [U(1, "htow", 0, 0), U(900, "unpl", 12000, 0, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
    units += [U(10 + i, "hfoo", 9000, 80 * i) for i in range(8)]
    h = U(2, "Hmkg", 8900, 0)
    if hero_hp is not None:
        h.hp = hero_hp
    units.append(h)
    units += [U(600 + i, "ugho", 9400, 80 * i, owner=1) for i in range(n_foes)]
    return build_world(units, 0, 900.0, 300, 300, 50, 80, levels={2: 3})


def attacking(w):
    s = T.AttackSequence(dict(PROFILE))
    s.state, s.start_strength = "attack", w.own_strength
    s._set_target(w.enemy_halls[0], "主基地")
    return s


class FleeLocal(unittest.TestCase):
    def test_moderately_outnumbered_keeps_output(self):
        # 我方 21、敌方 28：不能因为单帧 0.75 战力比就全撤。
        w = world(14)
        s = attacking(w)
        out = s.step(w, w.my_halls[0])
        self.assertTrue(out)
        self.assertTrue(all(i.kind in ("attack", "attack_move") for i in out))
        self.assertEqual(s.state,"attack")

    def test_clear_disadvantage_requires_confirmation_but_collapse_does_not(self):
        s=T.AttackSequence({})
        self.assertFalse(s._persistent_loss("attack",0,10,20,.55))
        self.assertFalse(s._persistent_loss("attack",3,10,20,.55))
        self.assertTrue(s._persistent_loss("attack",6,10,20,.55))
        self.assertFalse(s._persistent_loss("attack",7,20,20,.55))
        self.assertTrue(s._persistent_loss("attack",8,5,20,.55))

    def test_even_fight_keeps_going(self):
        w = world(9)                                                              # 18：21 >= 14.4
        s = attacking(w)
        s.step(w, w.my_halls[0])
        self.assertEqual(s.state, "attack")


class HeroReady(unittest.TestCase):
    def test_locked_creep_mission_is_not_replaced_by_base_attack(self):
        units = [U(1, "htow", 0, 0), U(900, "unpl", 6000, 0, owner=1)] + [U(300 + i, "hpea", 100, 0) for i in range(5)]
        units += [U(10 + i, "hfoo", 500, 80 * i) for i in range(12)]
        h = U(2, "Hmkg", 400, 0)
        h.hp = 0.5 * h.hp_max
        units += [h, U(700, "nwlg", 2000, 0, owner=12)]
        w = build_world(units, 0, 900.0, 300, 300, 50, 80, levels={2: 3})       # 对面没兵：原来打野中会直接转去出击
        s = T.AttackSequence(dict(PROFILE))
        s.state, s.camp_ids, s.creep_since = "creep", {700}, w.t
        s.camp = dict(x=2000.0, y=0.0, strength=4.0, units=[])
        out = s.step(w, w.my_halls[0])
        self.assertEqual(s.state, "creep")
        self.assertNotIn(h.addr, {u.addr for it in out if it.kind.startswith("attack") for u in it.units})
        self.assertIsNone(s.counts.get("出击·健康主力先走·英雄单独恢复"))


if __name__ == "__main__":
    unittest.main()

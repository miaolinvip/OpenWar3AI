# -*- coding: utf-8 -*-
"""英雄被控住（缠绕/眩晕…）时不白下移动令、叫牧师驱散；英雄撤退没挪窝判得更快（mod_combat）。

09-22 会战中英雄阵亡过快（1-299 13 分），排查底层响应链路，让英雄操作即时响应战局变化。
战斗帧：血魔法师撤退令生效 0.2 秒就原地变回攻击、3 游戏秒后才能再动（缠绕根须对英雄 3 秒）；
大法师被守望者盯上后 2 秒纹丝不动，「撤离未脱险·改道」连下三次移动令全白费。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mod_combat as C  # noqa: E402


def u(addr, t, x, y, hero=False, hp=100.0):
    return SimpleNamespace(addr=addr, type=t, x=float(x), y=float(y), hp=hp, hp_max=100.0, is_hero=hero,
                           order=0, handle_lo=addr, handle_hi=1)


class Cmd:
    def __init__(self, buffs):
        self.buffs, self.asked = set(buffs), []

    def ability_level(self, unit, code):
        self.asked.append(code)
        return 1 if code in self.buffs else 0


class Disabled(unittest.TestCase):
    def test_roots_are_seen_and_marked_dispellable(self):
        code, cn = C.disabled_by(Cmd({"BEer"}), u(1, "Hamg", 0, 0, hero=True))
        self.assertEqual((code, cn), ("BEer", "缠绕"))
        self.assertIn(code, C.DISPELLABLE)

    def test_stun_is_seen_but_not_dispellable(self):
        code, _ = C.disabled_by(Cmd({"BPSE"}), u(1, "Hamg", 0, 0, hero=True))
        self.assertEqual(code, "BPSE")
        self.assertNotIn(code, C.DISPELLABLE)

    def test_free_hero(self):
        c = Cmd(set())
        self.assertIsNone(C.disabled_by(c, u(1, "Hamg", 0, 0, hero=True)))
        self.assertEqual(len(c.asked), len(C.DISABLE_BUFFS))


class DispelPick(unittest.TestCase):
    def test_nearest_priest_in_reach_with_mana(self):
        hero = u(1, "Hamg", 0, 0, hero=True)
        near_dry, near, far = u(2, "hmpr", 200, 0), u(3, "hmpr", 400, 0), u(4, "hmpr", 2000, 0)
        mana = {2: 20.0, 3: 200.0, 4: 200.0}
        self.assertEqual(C.dispel_pick(hero, [near_dry, near, far], mana_of=lambda p: mana.get(p.addr)).addr, 3)
        self.assertIsNone(C.dispel_pick(hero, [far], mana_of=lambda p: 200.0))

    def test_unknown_mana_still_tries(self):
        hero = u(1, "Hamg", 0, 0, hero=True)
        self.assertEqual(C.dispel_pick(hero, [u(2, "hmpr", 300, 0)], mana_of=lambda p: None).addr, 2)


class HeroRetreatWatchedFaster(unittest.TestCase):
    def test_hero_is_rechecked_after_rw_hero_s_troop_after_0_8(self):
        rw = C.RetreatWatch()
        hero, foot = u(1, "Hamg", 0, 0, hero=True), u(2, "hfoo", 50, 0)
        foe = u(100, "earc", 300, 0)
        pulled = {1: 99.0, 2: 99.0}
        rw.check([hero, foot], [foe], pulled, 10.0)          # 第一次只记样本
        t = 10.0 + C.RW_HERO_S + 0.01
        retry = rw.check([hero, foot], [foe], pulled, t)     # 两个都没挪窝
        self.assertEqual([r[0].addr for r in retry], [1], "英雄 RW_HERO_S 就判，小兵还要等到 0.8")
        self.assertLess(C.RW_HERO_S, 0.8)
        self.assertLessEqual(C.HERO_DANGER_EVERY, 0.3)


if __name__ == "__main__":
    unittest.main()

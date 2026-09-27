# -*- coding: utf-8 -*-
"""出击已经打起来以后，英雄不因为「血不满（七成以下、五成以上）」被推回家（tactics -> heroplan offensive 只在集结时给）。

09-22 只有小兵在打敌方二矿，英雄要么回了家、要么停在地图中间（1-296 13 分）。
决策明细：血魔法师 66%~68% 连着「英雄脱离到附近安全点」三次、12:56「英雄残血先回家」，一直在地图中间晃；
没英雄的部队到了对面 2 矿局部判「敌强我弱」，13:18「脱离接触，回队重整」整体回家。
「血不满，进攻中先恢复」本意是出发前先恢复（1-287 18:22 68% 回家也是它）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T              # noqa: E402
from brain.tables.human import PROFILE      # noqa: E402
from test_tactics import U, world           # noqa: E402

LEAVE = ("英雄残血先回家", "英雄脱离到附近安全点")


def scene(t, hero_hp=0.68):
    home = U(1, "htow", 0, 0, 0)
    army = [U(10 + i, "hfoo", 0, 5600, 60 * i) for i in range(8)]
    bm = U(23, "Hblm", 0, 3000, 0)
    bm.hp = hero_hp * bm.hp_max
    exp = U(100, "htow", 1, 7000, 0)
    foes = [U(200 + i, "ogru", 1, 12000, 4000 + 60 * i) for i in range(6)]   # 对面主力在老家，没打光
    return world([home, bm] + army + [exp] + foes, food=40, t=t)


class HeroFollowsAttack(unittest.TestCase):
    def step_attack(self, hero_hp):
        s = T.AttackSequence(dict(PROFILE))
        w = scene(600, hero_hp)
        s.state, s.now = "attack", 600
        s._set_target(next(u for u in w.enemy_halls), "分矿")
        out = s.step(w, w.my_halls[0])
        return [it for it in out if it.why in LEAVE and any(u.addr == 23 for u in it.units)]

    def test_68pct_hero_is_not_sent_away_during_an_attack(self):
        self.assertEqual(self.step_attack(0.68), [])

    def test_dying_hero_still_leaves(self):
        self.assertTrue(self.step_attack(0.25))


if __name__ == "__main__":
    unittest.main()

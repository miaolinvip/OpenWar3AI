# -*- coding: utf-8 -*-
"""出击「任务停滞 -> 回家重整」要认「在杀东西」这种进展（tactics._combat_progress）。

09-22 明显优势局里全军突然回家（1-288 18 分）。
18:35「进攻任务无进展，回家重整」：当时我方强度 39.4、敌方 2.8。那一分钟全军在要塞边上
把掠夺者、粉碎者、牛头人酋长、先知都打掉了，只是还没砍到要塞本身 —— _push 只认「离目标近了 200」
和「目标掉血」，交战（走 _support）不刷新进展，交战一结束第一次 _push 就判停滞。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T              # noqa: E402
from brain.tables.human import PROFILE      # noqa: E402
from test_tactics import U, world           # noqa: E402

FORT = (7000.0, 0.0)


def scene(t, grunts=0):
    home = U(1, "htow", 0, 0, 0)
    army = [U(10 + i, "hfoo", 0, 5600, 60 * i) for i in range(8)] + [U(30, "hkni", 0, 5650, 100)]
    fort = U(100, "ofrt", 1, *FORT)
    base = [U(101 + i, "otrb", 1, FORT[0] + 400, -300 + 200 * i) for i in range(3)]
    foes = [U(200 + i, "ogru", 1, 5900, 50 * i, hp=60) for i in range(grunts)]   # 残血步兵：打得过，但正交着手
    return world([home] + army + [fort] + base + foes, food=40, t=t)


class AttackStall(unittest.TestCase):
    def run_seq(self, fighting: bool):
        s = T.AttackSequence(dict(PROFILE))
        w = scene(590)
        home = w.my_halls[0]
        s.state = "attack"
        s.now = 590
        s._set_target(next(u for u in w.enemy_halls if u.type == "ofrt"), "主基地")
        s.step(w, home)                                   # 第一拍：登记离目标的距离
        for t in (620, 640):
            s.step(scene(t, grunts=2 if fighting else 0), home)
        out = s.step(scene(660), home)                    # 交战结束：人没挪、要塞没掉血
        return s, [it.why for it in out or []]

    def test_fighting_near_the_target_is_progress(self):
        s, whys = self.run_seq(fighting=True)
        self.assertNotIn("进攻任务无进展，回家重整", whys)
        self.assertEqual(s.state, "attack")
        self.assertGreaterEqual(s.counts.get("出击·进展·交战中", 0), 1)

    def test_truly_stuck_still_regroups(self):
        s, whys = self.run_seq(fighting=False)
        self.assertIn("进攻任务无进展，回家重整", whys)


if __name__ == "__main__":
    unittest.main()

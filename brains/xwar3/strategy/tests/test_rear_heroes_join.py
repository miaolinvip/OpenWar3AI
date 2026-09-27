# -*- coding: utf-8 -*-
"""局部交战开打时，行军跟在后面的英雄 / 施法支援也拿到命令去前沿（tactics REAR_JOIN_R）。

09-22 其它单位已经交火，英雄却仍停在地图中间，不去支援（1-394 23:30）。
战斗帧：行军时英雄按 _backline 跟在主力后面，21:12:12 大部队 13 个在 (2660,-2163) 开打，英雄离交火点 2700~2900、
离大部队 1600~2300；局部交战的小队只圈「离最密集那个兵 1600 内」的人，英雄不在里面 -> 这一拍起没有任何给英雄的令，
订单空着站在原地（大脑 21:12:30「Hamg 卡了 31 游戏秒没挪窝（离目标 2883）」）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from test_fight_support import attacking, world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402


class RearHeroesJoin(unittest.TestCase):
    def scene(self, hero_x):
        front = [U(10 + i, "hkni", 5000, 80 * i) for i in range(6)] + [U(20, "hrif", 4900, 100), U(21, "hrif", 4900, 200)]
        foes = [U(600 + i, "ogru", 5500, 90 * i, owner=1) for i in range(5)]
        rear = [U(2, "Hamg", hero_x, 0), U(3, "Hblm", hero_x, 150), U(30, "hsor", hero_x - 100, 100)]
        return world(front + foes + rear)

    def test_lagging_heroes_and_casters_are_sent_to_the_fight(self):
        w = self.scene(2500)                                   # 离前沿 2500：在小队 1600 之外
        out = attacking(w).step(w, w.my_halls[0])
        fight = [i for i in out if "交战" in i.why]
        self.assertTrue(fight, [(i.kind, i.why) for i in out])
        sent = {u.addr for i in fight for u in i.units}
        self.assertTrue({2, 3, 30} <= sent, [(i.why, [u.type for u in i.units]) for i in out])
        for i in fight:
            if any(u.addr in (2, 3) for u in i.units):
                self.assertIn(i.kind, ("attack", "attack_move"))
                self.assertGreater(i.x, 4000, "往前沿走，不是原地")

    def test_heroes_far_away_are_left_alone(self):
        w = self.scene(-1000)                                  # 离前沿 6000：在别处（家里 / 打野），不从那边调
        out = attacking(w).step(w, w.my_halls[0])
        sent = {u.addr for i in out if "交战" in i.why for u in i.units}
        self.assertFalse({2, 3} & sent)


if __name__ == "__main__":
    unittest.main()

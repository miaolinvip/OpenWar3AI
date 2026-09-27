# -*- coding: utf-8 -*-
"""走不上前（09-22 对局里常见各单位互相挤位、谁也走不上前）。

1 号实例 6 局量出来两件事（一次性分析脚本 closing_why.py / formation_variants.py）：
  * 近战够不着目标还走不近的时间里，63% 是目标在跑开（电脑把残血往回拉，我们点名"集火残血"一路追），
    48 次追逐只有 14 次 5 秒内追上 —— Engagement 不再点名追不上的逃跑目标（engagement.runs_away）；
  * 整队点令 90% 有人的路线互相交叉：座位按地址排，和人站哪无关 —— formation_points 改成离目标近的进前排、
    每一排按横向位置入座（同一批令推演相撞 1259 -> 623）。
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A                  # noqa: E402
from brain.engagement import Engagement     # noqa: E402
from test_tactics import U                  # noqa: E402


def crossings(points):
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-6 else (1 if v > 0 else -1)
    n = 0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            (u, x, y), (v, x2, y2) = points[i], points[j]
            p1, p2, q1, q2 = (u.x, u.y), (x, y), (v.x, v.y), (x2, y2)
            if orient(p1, p2, q1) * orient(p1, p2, q2) < 0 and orient(q1, q2, p1) * orient(q1, q2, p2) < 0:
                n += 1
    return n


class NoChasingRunners(unittest.TestCase):
    def test_footman_leaves_a_fleeing_low_hp_grunt_for_one_it_can_reach(self):
        seq = Engagement()
        foot = U(1, "hfoo")
        runner = U(100, "ogru", 1, 200, 0, hp=150, hp_max=700)
        other = U(101, "ogru", 1, 300, 250, hp=700, hp_max=700)
        seq.plan([foot], [runner, other], 0)
        runner.x = 450                                  # 1 游戏秒跑开 250（步兵 270，追上要十几秒）
        hits, _ = seq.plan([foot], [runner, other], 1)
        self.assertEqual(hits[0][1].addr, other.addr)
        self.assertEqual(seq.counts.get("接战·不追跑开的目标"), 1)

    def test_knight_still_finishes_a_runner_it_catches_quickly(self):
        seq = Engagement()
        knight = U(1, "hkni")
        runner = U(100, "ogru", 1, 200, 0, hp=150, hp_max=700)
        other = U(101, "ogru", 1, 300, 250, hp=700, hp_max=700)
        seq.plan([knight], [runner, other], 0)
        runner.x = 350                                  # 跑开 150/秒，骑士 350：1.25 秒追上
        hits, _ = seq.plan([knight], [runner, other], 1)
        self.assertEqual(hits[0][1].addr, runner.addr)
        self.assertFalse(seq.counts)

    def test_only_runners_left_hands_over_to_attack_move(self):
        seq = Engagement()
        foot = U(1, "hfoo")
        runner = U(100, "ogru", 1, 200, 0, hp=150, hp_max=700)
        seq.plan([foot], [runner], 0)
        runner.x = 450
        hits, marching = seq.plan([foot], [runner], 1)
        self.assertEqual(hits, [])
        self.assertEqual([u.addr for u in marching], [1])
        self.assertEqual(seq.counts.get("接战·身边只剩跑开的·改攻击移动"), 1)

    def test_first_sight_has_no_speed_so_nothing_is_dropped(self):
        seq = Engagement()
        hits, _ = seq.plan([U(1, "hfoo")], [U(100, "ogru", 1, 450, 0, hp=150, hp_max=700)], 0)
        self.assertEqual(hits[0][1].addr, 100)


class StickyTargets(unittest.TestCase):
    """09-22 交战里命令来回改：「支援交战 -> 支援交战」6 局 1010 对，射程边上来回切攻击/攻击移动、正在打的被换走、增援点乱跳。"""

    def test_footman_keeps_hitting_its_grunt_instead_of_walking_to_a_healer(self):
        seq = Engagement()
        foot = U(1, "hfoo")
        grunt = U(100, "ogru", 1, 140, 0, hp=700, hp_max=700)
        seq.plan([foot], [grunt], 0)
        healer = U(101, "odoc", 1, 250, 60, hp=300, hp_max=300)      # 核心，贴在刀口边（旧规则会立刻切过去）
        hits, _ = seq.plan([foot], [grunt, healer], 1)
        self.assertEqual(hits[0][1].addr, grunt.addr)
        self.assertEqual(seq.counts.get("接战·正在打的不丢下去追别的"), 1)

    def test_low_hp_core_still_breaks_the_hitting_lock(self):
        seq = Engagement()
        foot = U(1, "hfoo")
        grunt = U(100, "ogru", 1, 140, 0, hp=700, hp_max=700)
        seq.plan([foot], [grunt], 0)
        hero = U(101, "Ofar", 1, 250, 60, hp=200, hp_max=800)
        hits, _ = seq.plan([foot], [grunt, hero], 1)
        self.assertEqual(hits[0][1].addr, hero.addr)

    def test_rifle_does_not_flip_to_attack_move_when_its_target_steps_out(self):
        seq = Engagement()
        rifle = U(1, "hrif")
        grunt = U(100, "ogru", 1, 690, 0, hp=500, hp_max=700)
        seq.plan([rifle], [grunt], 0)
        grunt.x = 750                                   # 走出 射程+300 一点（每秒 60，不算逃跑）
        hits, marching = seq.plan([rifle], [grunt], 1)
        self.assertEqual([t.addr for _, t in hits], [grunt.addr])
        self.assertEqual(marching, [])
        self.assertEqual(seq.counts.get("接战·点名的目标刚出圈·不改攻击移动"), 1)

    def test_reinforcement_point_does_not_follow_every_step(self):
        from brain import tactics as T
        eng = Engagement()
        near = U(1, "hfoo", 0, 100, 0)
        far = U(2, "hrif", 0, -2500, 0)
        foe = U(100, "ogru", 1, 300, 0)
        first = [it for it in T.battle_intents(eng, [near, far], [foe], 0, 300, 0, "守家") if it.kind == "attack_move"]
        # 两拍隔 4 游戏秒（> VEL_MAX_DT，不估速度）：这里只测增援点，不让「目标在跑开」掺进来
        foe.x, foe.y = 500, 250                          # 目标挪了 320
        near.x = 300
        second = [it for it in T.battle_intents(eng, [near, far], [foe], 4, 300, 0, "守家") if it.kind == "attack_move"]
        self.assertEqual([u.addr for u in second[0].units], [2], "近的那个仍在打，只有远的在赶路")
        self.assertEqual((second[0].x, second[0].y), (first[0].x, first[0].y))
        foe.x = 1400                                     # 挪远了（> MARCH_KEEP_R）才换点
        near.x = 1200
        third = [it for it in T.battle_intents(eng, [near, far], [foe], 8, 300, 0, "守家") if it.kind == "attack_move"]
        self.assertEqual(third[0].x, 1400)

    def test_priest_is_not_told_to_attack_only_to_follow(self):
        from brain import tactics as T
        foot = U(1, "hfoo", 0, 100, 0)
        priest = U(2, "hmpr", 0, 0, 100)
        foe = U(100, "ogru", 1, 300, 0)
        out = T.battle_intents(Engagement(), [foot, priest], [foe], 0, 300, 0, "支援交战")
        self.assertFalse(any(priest in it.units for it in out), "离前排不远：交给毫秒层加血，不下令")
        priest.x = -1500
        out = T.battle_intents(Engagement(), [foot, priest], [foe], 0, 300, 0, "支援交战")
        mine = [it for it in out if priest in it.units]
        self.assertEqual([it.kind for it in mine], ["move"])
        # 只剩牧师时照旧（别把整队都剔光）
        out = T.battle_intents(Engagement(), [priest], [foe], 0, 300, 0, "支援交战")
        self.assertTrue(any(priest in it.units for it in out))


class FormationSeats(unittest.TestCase):
    def test_side_by_side_squad_keeps_its_lanes(self):
        # 四个步兵并排站在 y = -300..300，往 +x 走 1500；地址和横向顺序正好相反
        feet = [SimpleNamespace(addr=10 - i, type="hfoo", x=0.0, y=-300.0 + 200 * i) for i in range(4)]
        pts = A.formation_points(feet, 1500.0, 0.0)
        self.assertEqual(crossings(pts), 0)
        # 四个人排成 3+1（五列上限、4 人 3 列）：前排按原来的上下顺序入座，后排（座位在正中）是原来靠中间的人
        front = [p for p in pts if p[1] > 1499]
        self.assertEqual([u.addr for u, x, y in sorted(front, key=lambda p: p[2])],
                         [u.addr for u in sorted((p[0] for p in front), key=lambda u: u.y)], "前排谁在下面就还在下面")
        back = [p[0] for p in pts if p[1] <= 1499]
        self.assertEqual(len(back), 1)
        self.assertLessEqual(abs(back[0].y), 100, "后排是中间那个")

    def test_melee_front_ranged_middle_hero_back_still_holds(self):
        us = [SimpleNamespace(addr=1, type="Hamg", x=200.0, y=0.0),          # 英雄站在最前面
              SimpleNamespace(addr=2, type="hrif", x=150.0, y=50.0),
              SimpleNamespace(addr=3, type="hrif", x=150.0, y=-50.0),
              SimpleNamespace(addr=4, type="hfoo", x=0.0, y=60.0),
              SimpleNamespace(addr=5, type="hfoo", x=0.0, y=-60.0)]
        pts = {u.addr: (x, y) for u, x, y in A.formation_points(us, 2000.0, 0.0)}
        self.assertGreater(min(pts[4][0], pts[5][0]), max(pts[2][0], pts[3][0]) - 1, "近战在前")
        self.assertGreater(min(pts[2][0], pts[3][0]), pts[1][0] - 1, "英雄在最后")

    def test_nearer_unit_of_the_same_kind_takes_the_front_row(self):
        feet = [SimpleNamespace(addr=i, type="hfoo", x=float(-400 * (i % 2)), y=float(40 * i)) for i in range(8)]
        pts = {u.addr: (x, y) for u, x, y in A.formation_points(feet, 2000.0, 0.0)}
        front = [a for a, p in pts.items() if p[0] > max(q[0] for q in pts.values()) - 1]
        self.assertTrue(all(a % 2 == 0 for a in front), f"前排是本来就站在前面的（x=0）：{front}")


if __name__ == "__main__":
    unittest.main()

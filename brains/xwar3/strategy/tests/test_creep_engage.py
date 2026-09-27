# -*- coding: utf-8 -*-
"""打野开打不等主力：先到营地 900 内的 2 个人就点名开打，后面的继续攻击移动跟上。

09-18 圣骑士带一队人在地图最右边停了很久、迟迟不开打野。实机那一局：第 56 拍下的打野令，
第 63 拍才「点名开打」—— 中间 70 游戏秒里到场的人是 13/16、1/17、10/15 零零散散，
而开打的判据是**主力重心**到营地 900 内，等于要等最后几个人。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

CAMP = (4000, 0)


def scene(n_near=2, n_far=8):
    home = U(1, "htow", 0, 0)
    hero = U(2, "Hamg", CAMP[0] - 300, 0)          # U(addr, 类型, x, y, owner=0, ...)
    hero.hp = hero.hp_max = 500.0
    near = [U(10 + i, "hfoo", CAMP[0] - 400, 100 * i) for i in range(n_near)]
    far = [U(50 + i, "hfoo", 1500, 100 * i) for i in range(n_far)]          # 还在半路（离营地 2500）
    peas = [U(300 + i, "hpea", 100, 0) for i in range(5)]
    creeps = [U(700 + i, "nwlg", CAMP[0] + 60 * i, 60, owner=12) for i in range(3)]
    w = build_world([home, hero] + near + far + peas + creeps, 0, 600.0, 300, 300, 30, 60, levels={2: 2})
    return w, home, near, far


class TestCreepEngage(unittest.TestCase):
    def test_enough_arrived_start_hitting(self):
        # ★ 09-18 改：原来「900 内到了 2 个人就开打」，实机 2 只狮鹫对强度 25 的营地开打、被拉回又被派回（一两个兵打大营地直到阵亡）。
        #   现在按战力比（总血 × 每秒伤害，09-18 二）：3 只巨狼 1650 血 × 43.3/秒；到场 5 个步兵 + 2 级大法师 = 2600 血 × 68.3/秒 -> 2.48 >= 2.0 开打（4 个步兵是 1.80，不够）
        w, home, near, far = scene(n_near=5)
        seq = T.AttackSequence(dict(PROFILE))
        seq.camp_ids = {700, 701, 702}
        seq.creep_since = w.t
        seq.camp = dict(x=float(CAMP[0]), y=0.0, strength=6.0, units=[])
        out = seq.step(w, home)
        kinds = [(i.kind, len(i.units), i.why) for i in out]
        self.assertTrue(any(k == "attack" for k, _n, _w in kinds), kinds)
        hit = next(i for i in out if i.kind == "attack")
        self.assertLessEqual(len(hit.units), len(near) + 1)              # 只让到场的（含英雄）先打
        self.assertEqual(seq.counts.get("打野·点名开打"), 1)
        follow = [i for i in out if i.kind == "attack_move"]
        self.assertTrue(follow and len(follow[0].units) == len(far), kinds)   # 其余的继续跟上

    def test_nobody_arrived_yet_waits_at_stage(self):
        w, home, near, far = scene(n_near=0)
        seq = T.AttackSequence(dict(PROFILE))
        seq.camp_ids = {700, 701, 702}
        seq.creep_since = w.t
        seq.camp = dict(x=float(CAMP[0]), y=0.0, strength=6.0, units=[])
        out = seq.step(w, home)
        # 只有英雄在营地边上（战力比远不到 2.0），主力重心还远 -> 全员只移动到安全点，不能让召唤物/攻击移动提前开怪
        self.assertEqual([i.kind for i in out], ["move", "move"], [i.why for i in out])
        self.assertFalse([i for i in out if i.kind == "attack"])
        mv = next(i for i in out if i.kind == "move")
        self.assertEqual([u.addr for u in mv.units], [2])
        self.assertAlmostEqual(mv.x, CAMP[0] - T.CREEP_STAGE, delta=50)
        self.assertEqual(seq.counts.get("打野·等人到齐"), 1)

    def test_front_can_start_when_nearby_reinforcements_make_fight_safe(self):
        w, home, near, far = scene(n_near=3, n_far=2)
        for u in far:
            u.x, u.y = 2000, 0       # 营地 2500 内，已明确在增援路上
        seq = T.AttackSequence(dict(PROFILE))
        seq.camp_ids = {700, 701, 702}
        seq.creep_since = w.t
        seq.camp = dict(x=float(CAMP[0]), y=0.0, strength=6.0, units=[])
        out = seq.step(w, home)
        self.assertTrue(any(i.kind == "attack" for i in out), [i.why for i in out])
        self.assertEqual(seq.counts.get("打野·增援在路上提前开打"), 1)
        self.assertTrue(any("增援在路上" in i.why for i in out), [i.why for i in out])


class TestHeroJoinsCreeping(unittest.TestCase):
    """09-18 实机第 4 局：打野时大法师被「远程英雄跟在后面」拆到主力后面，睡着的野怪攻击移动不打，它整场不出手
    （英雄看戏埋点第 22/25/48/59 拍）。打野不再拆英雄；点名开打时 1500 内的英雄一起点名。"""

    def scene(self, hero_dx):
        home = U(1, "htow", 0, 0)
        hero = U(2, "Hamg", CAMP[0] - hero_dx, 0)
        hero.hp = hero.hp_max = 500.0
        near = [U(10 + i, "hfoo", CAMP[0] - 150, 100 * i) for i in range(3)]      # 已经贴上野怪（09-18 起远程英雄等肉盾接火再打）
        far = [U(50 + i, "hfoo", 1500, 100 * i) for i in range(4)]
        peas = [U(300 + i, "hpea", 100, 0) for i in range(5)]
        creeps = [U(700, "nwlg", CAMP[0], 60, owner=12)]      # 1 只（强度 4）：主力（4 个步兵 10）打得动，不会「打不动回家」
        return build_world([home, hero] + near + far + peas + creeps, 0, 600.0, 300, 300, 30, 60, levels={2: 2}), home

    def seq(self):
        s = T.AttackSequence(dict(PROFILE))
        s.camp_ids = {700, 701, 702}
        s.creep_since = 600.0
        s.camp = dict(x=float(CAMP[0]), y=0.0, strength=6.0, units=[])
        return s

    def test_hero_1200_away_joins_the_point_attack(self):
        w, home = self.scene(hero_dx=1200)                 # 在 900 外、1500 内
        out = self.seq().step(w, home)
        hit = next(i for i in out if i.kind == "attack")
        self.assertIn(2, {u.addr for u in hit.units})

    def test_creep_march_does_not_split_hero(self):
        w, home = self.scene(hero_dx=3500)                 # 大家都还在路上
        for u in w.my_army:
            u.x -= 2000                                    # 主力也远：纯行军
        out = self.seq().step(w, home)
        self.assertTrue(all("英雄跟在主力后面" not in i.why for i in out), [i.why for i in out])

    def test_loot_never_holds_or_calls_far_hero(self):
        w, home = self.scene(hero_dx=3000)
        s = self.seq()
        s.camp_ids = set()
        s.ground = [(CAMP[0] + 50.0, 20.0)]
        w.creeps.clear()
        out = s.step(w, home)
        self.assertFalse(any(i.kind == "move" and [u.addr for u in i.units] == [2]
                             and "捡装备" in i.why for i in out))
        self.assertNotEqual(s.state, "loot")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""★★ 09-21 进攻时常有大量单位死在敌方瞭望塔这类攻击型建筑下。
遇到攻击型建筑时，等敌方有伤害的单位都清完，全体集火打塔；
被塔打残的单位撤出它的攻击范围。

实测（120 个战斗帧文件、368 场打玩家的仗）：塔在场的 19 场（5%），
**其中 9 场整场一下都没打过塔**（47%）—— 频次不高，但缺口是真的：
出击的目标只在大厅/分矿/建筑团里选，塔从来不是目标，部队就站在塔下打别的。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G   # noqa: E402
from brain import tactics as T    # noqa: E402
from brain import winprob as W    # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from test_tactics import U, world  # noqa: E402


def scene(with_grunt=False, hurt=0):
    """我方主力贴在敌方大厅旁边，旁边一座兽族箭塔（owtw，射程 800）。"""
    mine = [U(1, "htow"), U(2, "Hamg", 0, 9000, 0)]
    mine += [U(10 + i, "hfoo", 0, 9000, 80 * i) for i in range(6)]
    for i in range(hurt):
        mine[2 + i].hp = 0.25 * mine[2 + i].hp_max
    foes = [U(100, "ogre", 1, 9600, 0), U(101, "owtw", 1, 9300, 200)]
    if with_grunt:
        foes.append(U(200, "ogru", 1, 9200, 100))
    return mine, foes


def seq_at(mine, foes):
    w = world(mine + foes, food=45, levels={2: 3})
    s = T.AttackSequence(dict(PROFILE))
    s.state, s.start_strength = "attack", w.own_strength
    s._set_target(w.enemy_halls[0], "主基地")
    return w, s


class TowersAfterTheFightersAreDead(unittest.TestCase):
    def test_the_tower_is_a_real_tower_in_the_data(self):
        """先钉住前提：owtw 确实被 gamedata 认成「会打人的塔」，射程 800。
        ⚠ 否则下面几条会在一个空集合上恒真地通过（判据要能区分成功和失败）。"""
        self.assertTrue(G.is_tower("owtw"))
        self.assertEqual(float(G.stat("owtw", "range", 0)), 800.0)

    def test_live_fighters_come_first(self):
        """还有会动会打人的敌人在，就不去点塔。"""
        mine, foes = scene(with_grunt=True)
        w, s = seq_at(mine, foes)
        self.assertIsNone(s._towers_first(w, w.my_army + w.my_heroes, 9000.0, 0.0))

    def test_everyone_focuses_the_tower_once_the_fighters_are_gone(self):
        mine, foes = scene()
        w, s = seq_at(mine, foes)
        s.hero_ids = {2}
        army = w.my_army + w.my_heroes
        out = s._towers_first(w, army, 9000.0, 0.0)
        self.assertIsNotNone(out, "敌兵清空、塔还在，应该集火塔")
        hit = [i for i in out if i.kind == "attack"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].target.type, "owtw")
        self.assertEqual({u.addr for u in hit[0].units}, {u.addr for u in w.my_army},
                         "「全体集火」就是全体兵，不是挑几个")

    def test_heroes_never_tank_the_tower(self):
        """★ 09-21 敌方箭塔一直打英雄，英雄却一直顶在塔下；英雄尽量不当肉盾。
        v28 那一版把英雄也算进「全体集火」（上面那条测试原来就是这么钉的），5 号 21:15 那局山丘在塔下 20 秒 649 -> 1。
        英雄满血也不打塔，退到射程 + 余量之外。"""
        mine, foes = scene()
        w, s = seq_at(mine, foes)
        s.hero_ids = {2}
        out = s._towers_first(w, w.my_army + w.my_heroes, 9000.0, 0.0)
        hit = [i for i in out if i.kind == "attack"]
        self.assertNotIn(2, {u.addr for i in hit for u in i.units})
        back = [i for i in out if i.why == "英雄不打塔，退出塔的射程"]
        self.assertEqual([[u.addr for u in i.units] for i in back], [[2]])
        tower = next(f for f in w.enemy_towers if f.type == "owtw")
        reach = float(G.stat("owtw", "range", 800)) + T.TOWER_CLEAR_PAD
        self.assertAlmostEqual(((back[0].x - tower.x) ** 2 + (back[0].y - tower.y) ** 2) ** .5, reach, places=3)

    def test_hurt_units_leave_the_tower_range_instead_of_attacking(self):
        """同一条要求的后半部分：被塔打残的单位撤出攻击范围。"""
        mine, foes = scene(hurt=2)
        w, s = seq_at(mine, foes)
        army = w.my_army + w.my_heroes
        out = s._towers_first(w, army, 9000.0, 0.0)
        pulled = [i for i in out if i.why == "残血退出塔的射程"]
        self.assertEqual(len(pulled), 2)
        tower = next(f for f in w.enemy_towers if f.type == "owtw")
        reach = float(G.stat("owtw", "range", 800)) + T.TOWER_CLEAR_PAD
        for i in pulled:
            # ⚠ 期望是**算**出来的：撤到的点必须真的在射程 + 余量之外，不是"往后挪一点"
            self.assertAlmostEqual(((i.x - tower.x) ** 2 + (i.y - tower.y) ** 2) ** .5, reach, places=3)
        hit = [i for i in out if i.kind == "attack"]
        hurt_ids = {u.addr for i in pulled for u in i.units}
        self.assertTrue(hit)
        self.assertFalse(hurt_ids & {u.addr for u in hit[0].units}, "撤出去的人不该同时被派去打塔")

    def test_the_hurt_line_is_the_calibrated_one(self):
        """血线用的是校准那一套的 SOFT_HP，不是这里另拍一个数。"""
        mine, foes = scene()
        mine[2].hp = (W.SOFT_HP + 0.05) * mine[2].hp_max      # 刚好在线上面
        w, s = seq_at(mine, foes)
        out = s._towers_first(w, w.my_army + w.my_heroes, 9000.0, 0.0)
        self.assertFalse([i for i in out if i.why == "残血退出塔的射程"])
        mine[2].hp = (W.SOFT_HP - 0.05) * mine[2].hp_max      # 刚好在线下面
        w, s = seq_at(mine, foes)
        out = s._towers_first(w, w.my_army + w.my_heroes, 9000.0, 0.0)
        self.assertTrue([i for i in out if i.why == "残血退出塔的射程"])

    def test_no_tower_means_no_change(self):
        mine, foes = scene()
        foes = [f for f in foes if f.type != "owtw"]
        w, s = seq_at(mine, foes)
        self.assertIsNone(s._towers_first(w, w.my_army + w.my_heroes, 9000.0, 0.0))


if __name__ == "__main__":
    unittest.main()

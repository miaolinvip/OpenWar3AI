import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from battlecommit import BattleCommit
import mod_combat as C
from test_hero_local_safety import unit


class Commit(unittest.TestCase):
    def setUp(self):
        self.ours=[unit(i,'hfoo',hp=420,maximum=420) for i in range(1,7)]
        self.foes=[unit(100+i,'ogru',200,i*30,700,700,1) for i in range(6)]

    def test_bad_estimate_without_damage_does_not_cancel_output(self):
        gate=BattleCommit()
        for t in range(10):
            self.assertFalse(gate.observe(t,self.ours,self.foes,40,20))

    def test_sustained_loss_after_actual_exchange_can_retreat(self):
        # 09-23 照 AMAI（敌方线性强度 2 倍才撤 = 这里 4 倍）：打光对面 100 秒、被打光 20 秒 -> 撤
        gate=BattleCommit()
        self.assertFalse(gate.observe(0,self.ours,self.foes,100,20))
        for u in self.ours: u.hp=320
        self.assertFalse(gate.observe(1,self.ours,self.foes,100,20))
        self.assertTrue(gate.observe(3,self.ours,self.foes,100,20))

    def test_amai_keeps_fighting_a_moderately_bad_trade(self):
        """原来 2 倍（线性只强 1.4 倍）打 2.5 秒掉了血就整队撤；AMAI 这种仗接着打（RETREAT_CONTROL 要 2~3 倍线性）。"""
        gate=BattleCommit()
        gate.observe(0,self.ours,self.foes,40,20)
        for u in self.ours: u.hp=300
        for t in (1,3,5,8):
            self.assertFalse(gate.observe(t,self.ours,self.foes,40,20))
            self.assertFalse(gate.observe(t+.5,self.ours,self.foes,40,20,model_bad=True), "世界模型也判输：2 倍 < 2.5 照样打")
        for u in self.ours: u.hp=420       # 新的一仗：满血进场，之后掉到 300（掉 29%）
        judged=BattleCommit()
        judged.observe(0,self.ours,self.foes,60,20,model_bad=True)
        for u in self.ours: u.hp=300
        judged.observe(1.5,self.ours,self.foes,60,20,model_bad=True)
        self.assertTrue(judged.observe(3,self.ours,self.foes,60,20,model_bad=True), "世界模型判输且 3 倍：撤")

    def test_reinforcement_reverses_pending_retreat(self):
        gate=BattleCommit()
        gate.observe(0,self.ours,self.foes,40,20)
        for u in self.ours: u.hp=300
        self.assertFalse(gate.observe(3,self.ours,self.foes,20,30))

    def test_critical_collapse_never_waits_for_grace(self):
        self.assertTrue(BattleCommit().observe(0,self.ours,self.foes,30,2))

    def test_world_model_alone_does_not_pull_healthy_even_fight(self):
        gate=BattleCommit()
        for t in range(10):
            self.assertFalse(gate.observe(t,self.ours,self.foes,20,20,model_bad=True))

    def test_new_battle_does_not_inherit_old_losses(self):
        gate=BattleCommit();gate.observe(0,self.ours,self.foes,40,20)
        for u in self.ours: u.hp=300
        self.assertFalse(gate.observe(10,self.ours,self.foes,40,20))

    def test_healthy_supported_mountain_keeps_fighting(self):
        h=unit(50,'Hmkg',hp=849,maximum=900)
        foes=[unit(100+i,'hkni',220,i*20,1000,1000,1) for i in range(10)]
        self.assertIsNone(C.hero_exposure(h,foes,[h]+self.ours))
        h.hp=400
        self.assertIsNotNone(C.hero_exposure(h,foes,[h]+self.ours))

    def test_4_036_full_hp_hero_does_not_leave_wounded_team_in_the_fight(self):
        """4-036 8:35「英雄局部自保：Hamg 625/625 孤军且敌方占优，敌 5、有效护卫 0」—— 身边 4 个带伤步兵正和同一批野怪打着，
        英雄一走它们全死在撤退路上。和同一批敌人交手的自己人（血 > 20%）在，就不算孤军。"""
        h = unit(1, 'Hamg', 0, 0, 625, 625)
        creeps = [unit(100 + i, 'nmrm', 250, 60 * i, 400, 400, 12) for i in range(5)]
        team = [unit(2 + i, 'hfoo', 180, 60 * i, 130, 420) for i in range(4)]          # 31% 血，贴着野怪
        self.assertIsNone(C.hero_exposure(h, creeps, [h] + team))
        # 对照：同样的人都在撤退（unavailable）/ 血低于 20% —— 英雄确实是一个人，照旧脱离
        # （09-23 起满血英雄对野怪顶着打，CREEP_TANK_FRAC；对照用半血英雄）
        h.hp = 300
        self.assertEqual(C.hero_exposure(h, creeps, [h] + team, {2, 3, 4, 5})[0], "孤军且敌方占优")
        for u in team:
            u.hp = 60
        self.assertEqual(C.hero_exposure(h, creeps, [h] + team)[0], "孤军且敌方占优")

    def test_057_backline_archmage_can_follow_engaged_creep_frontline(self):
        # 取 135.89/136.04 秒相邻录帧之间的接近位置，复现 700 距离边界。
        h=unit(1,'Hamg',-4790,-2100,475,475)
        front=[unit(2,'hfoo',-4625,-2822,400,420),unit(3,'hfoo',-4735,-2885,420,420)]
        creeps=[unit(100,'nftt',-4583,-2345,313,400,12),unit(101,'nomg',-4613,-2935,215,600,12)]
        self.assertIsNone(C.hero_exposure(h,creeps,[h]+front))
        h.hp=230   # 09-23 起满血英雄对野怪顶着打（CREEP_TANK_FRAC）；下面两条验「前排不在 = 孤军」用半血英雄
        self.assertIsNotNone(C.hero_exposure(h,creeps,[h]+front,{2,3}))
        # 身后援军并未牵制野怪，不能当作前排。
        for u in front: u.y=-600
        self.assertIsNotNone(C.hero_exposure(h,creeps,[h]+front))

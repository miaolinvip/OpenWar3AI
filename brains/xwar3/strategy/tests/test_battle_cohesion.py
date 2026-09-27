import pathlib
import sys
import unittest
sys.path[:0] = [str(pathlib.Path(__file__).resolve().parents[1]), str(pathlib.Path(__file__).parent)]
from brain import tactics as T
from brain import engagement as ENG
from brain.engagement import Engagement
from test_tactics import U, world


class CreepMission(unittest.TestCase):
    def setup_mission(self, held=True):
        home=U(1,'htow'); foot=U(2,'hfoo',x=3000)
        creeps=[U(100+i,'nogm',12,3200,i*30) for i in range(2)]
        w=world([home,foot]+creeps,t=174)
        seq=T.AttackSequence({},state='creep',camp_ids={c.addr for c in creeps},creep_engaged=True,
                             creep_since=137,held={3} if held else set(),held_heroes=int(held))
        seq.camp=dict(x=3200,y=15,strength=10,units=creeps)
        seq.home_xy=(0,0)
        return seq,w,foot,creeps

    def test_started_camp_survives_temporary_hero_pull(self):
        seq,w,foot,creeps=self.setup_mission()
        out=seq._creep_engage_core(w,[foot],None,creeps,10)
        self.assertEqual(seq.state,'creep')
        self.assertEqual(seq.camp_ids,{100,101})
        self.assertTrue(out)
        self.assertTrue(all(i.kind=='move' for i in out))

    def test_summon_tanking_keeps_permanent_unit_attacking_while_hero_is_pulled(self):
        seq,w,foot,creeps=self.setup_mission()
        water=U(4,'hwat',x=3180,y=0,hp=500,hp_max=525)
        seq.creep_summon_tank_at=w.t-3
        out=seq._creep_engage_core(world([U(1,'htow'),foot,water]+creeps,t=w.t),
                                   [foot,water],None,creeps,10)
        self.assertTrue(any(i.kind=='attack' and foot in i.units for i in out))

    def test_failed_camp_issues_extraction_instead_of_empty_repick(self):
        seq,w,foot,creeps=self.setup_mission(False)
        out=seq._creep_engage_core(w,[foot],None,creeps,10)
        self.assertEqual(seq.state,'retreat')
        self.assertTrue(seq.creep_withdraw)
        self.assertTrue(seq.camp_ids)
        self.assertEqual(out[0].kind,'move')
        self.assertNotEqual((out[0].x,out[0].y),(0,0))  # 只退到附近安全点，不再整队横穿地图回主基地

    def test_held_straggler_prevents_new_mission(self):
        seq,w,foot,creeps=self.setup_mission(False)
        seq._creep_engage_core(w,[foot],None,creeps,10)
        seq.held_units=[foot]
        fresh=U(4,'hfoo',x=100)
        out=seq._step(world([U(1,'htow'),fresh]+creeps,t=180),U(1,'htow'),[fresh],False)
        self.assertEqual(seq.state,'retreat')
        self.assertTrue(all(i.kind=='move' for i in out))

    def test_held_straggler_only_blocks_extraction_for_bounded_time(self):
        seq,w,foot,creeps=self.setup_mission(False)
        seq._creep_engage_core(w,[foot],None,creeps,10)
        seq.held_units=[foot]
        seq.retreat_at=100
        fresh=U(4,'hfoo',x=100)
        seq._step(world([U(1,'htow'),fresh]+creeps,t=119),U(1,'htow'),[fresh],False)
        self.assertFalse(seq.creep_withdraw)
        self.assertFalse(seq.camp_ids)
        self.assertTrue(any('屏障到期' in n for n in seq.notes))

    def test_disabling_creep_keeps_extraction_barrier(self):
        seq,w,foot,creeps=self.setup_mission(False)
        seq.allow_creep=False
        out=seq._step(w,U(1,'htow'),[foot],False)
        self.assertEqual(seq.state,'retreat')
        self.assertTrue(seq.camp_ids)
        self.assertEqual(out[0].kind,'move')
        seq.held_units=[foot]
        fresh=U(4,'hfoo',x=100)
        seq._step(world([U(1,'htow'),fresh]+creeps,t=180),U(1,'htow'),[fresh],False)
        self.assertEqual(seq.state,'retreat')
        self.assertTrue(seq.camp_ids)

    def test_regroup_has_bounded_deadline(self):
        seq,w,foot,creeps=self.setup_mission()
        seq.creep_regroup_at=w.t-46
        out=seq._creep_engage_core(w,[foot],None,creeps,10)
        self.assertEqual(seq.state,'retreat')
        self.assertTrue(out)

    def test_rally_does_not_flip_when_army_crosses_camp(self):
        seq,w,foot,creeps=self.setup_mission()
        self.assertEqual(seq._camp_stage(2000,0),seq._camp_stage(4000,0))
        self.assertLess(seq._camp_stage(4000,0)[0],seq.camp['x'])

    def test_wounded_troop_returns_home_not_next_camp(self):
        seq,w,foot,creeps=self.setup_mission()
        foot.hp=60
        out=seq._creep_engage(w,[foot],None,creeps,10)
        self.assertEqual((out[-1].kind,out[-1].x,out[-1].y),('move',0,0))

    def test_repick_respects_planned_travel_and_reinforcement_time(self):
        seq,w,foot,creeps=self.setup_mission(False)
        seq.creep_engaged=False
        seq.creep_ready_at=w.t+30
        out=seq._creep_engage_core(w,[foot],None,creeps,10)
        self.assertEqual(seq.state,'creep')
        self.assertTrue(seq.camp_ids)

    def test_healthy_ranged_hero_joins_after_tanks_contact(self):
        seq,w,foot,creeps=self.setup_mission(False)
        hero=U(3,'Hamg',x=2700,hp=500,hp_max=500)
        troops=[U(10+i,'hfoo',x=3150,y=i*40) for i in range(6)]
        out=seq._creep_engage_core(world([hero]+troops+creeps,t=174),[hero]+troops,None,creeps,10)
        self.assertTrue(any(i.kind=='attack' and hero in i.units for i in out))


class BattleOrders(unittest.TestCase):
    def test_melee_does_not_chase_weak_backline_past_front(self):
        foot=U(1,'hfoo'); grunt=U(100,'ogru',1,200,0); priest=U(101,'hmpr',1,600,0,hp=10)
        hit,_=Engagement().plan([foot],[grunt,priest],0)
        self.assertEqual(hit[0][1].addr,grunt.addr)

    def test_ranged_focuses_same_reachable_weak_hero(self):
        rifles=[U(i,'hrif',x=0,y=i*10) for i in range(1,5)]
        weak=U(100,'Ofar',1,500,0,hp=250,hp_max=600)
        strong=U(101,'Otch',1,500,100,hp=800,hp_max=800)
        hits,_=Engagement().plan(rifles,[weak,strong],0)
        self.assertTrue(all(t.addr==100 for u,t in hits))

    def test_ranged_finishes_exposed_low_hp_front_before_healthy_core(self):
        rifles=[U(i,'hrif',x=0,y=i*10) for i in range(1,4)]
        front=U(100,'ogru',1,250,0,hp=40,hp_max=700)
        healer=U(101,'hmpr',1,500,0,hp=300,hp_max=300)
        hits,_=Engagement().plan(rifles,[front,healer],0)
        self.assertEqual(sum(t.addr==front.addr for _,t in hits),2)  # 两发足够收掉，第三人避免过量伤害

    def test_ranged_does_not_chase_fleeing_low_hp_target_through_enemy_pack(self):
        rifle=U(1,'hrif',x=0,y=0)
        close=[U(100+i,'ogru',1,350,i*40,hp=500,hp_max=700) for i in range(3)]
        fleeing=U(200,'ogru',1,650,0,hp=20,hp_max=700)
        hits,_=Engagement().plan([rifle],close+[fleeing],0)
        self.assertNotEqual(hits[0][1].addr,fleeing.addr)

    def test_low_health_core_immediately_breaks_old_target_lock(self):
        rifles=[U(i,'hrif',x=0,y=i*10) for i in range(1,5)]
        healer=U(100,'hmpr',1,500,0,hp=290,hp_max=290)
        hero=U(101,'Otch',1,500,80,hp=800,hp_max=800)
        seq=Engagement();seq.plan(rifles,[healer,hero],0)
        hero.hp=240
        hits,_=seq.plan(rifles,[healer,hero],1)
        self.assertTrue(all(t.addr==hero.addr for _,t in hits))
        self.assertEqual(seq.target_reasons[hero.addr],'集火残血核心')

    def test_ranged_focuses_high_damage_unit_when_no_low_core(self):
        rifles=[U(i,'hrif',x=0,y=i*10) for i in range(1,5)]
        grunt=U(100,'ogru',1,500,0,hp=500,hp_max=700)
        knight=U(101,'hkni',1,500,80,hp=500,hp_max=835)
        seq=Engagement();hits,_=seq.plan(rifles,[grunt,knight],0)
        self.assertTrue(all(t.addr==knight.addr for _,t in hits))
        self.assertEqual(seq.target_reasons[knight.addr],'集火高伤害单位')

    def test_rifle_can_focus_high_damage_air_unit(self):
        rifle=U(1,'hrif');wyvern=U(100,'owyv',1,500,0,hp=300,hp_max=570)
        hits,marching=Engagement().plan([rifle],[wyvern],0)
        self.assertFalse(marching)
        self.assertEqual(hits[0][1].addr,wyvern.addr)

    def test_melee_hits_core_when_already_beside_it(self):
        foot=U(1,'hfoo');front=U(100,'ogru',1,100,0,hp=500)
        healer=U(101,'hmpr',1,220,0,hp=300)
        hits,_=Engagement().plan([foot],[front,healer],0)
        self.assertEqual(hits[0][1].addr,healer.addr)

    def test_target_is_stable_while_two_enemies_exchange_hp(self):
        seq=Engagement(); rifle=U(1,'hrif'); a=U(100,'ogru',1,500,0,hp=300); b=U(101,'ogru',1,500,50,hp=310)
        hits,_=seq.plan([rifle],[a,b],0); old=hits[0][1].addr
        b.hp=290
        self.assertEqual(seq.plan([rifle],[a,b],2)[0][0][1].addr,old)

    def test_dead_target_releases_assignment(self):
        seq=Engagement(); foot=U(1,'hfoo'); a=U(100,'ogru',1,100,0,hp=100); b=U(101,'ogru',1,100,50,hp=300)
        seq.plan([foot],[a,b],0); a.hp=0
        self.assertEqual(seq.plan([foot],[a,b],1)[0][0][1].addr,101)

    def test_melee_front_distributes_instead_of_all_surrounding_one_unit(self):
        army=[U(i,'hfoo') for i in range(1,7)]
        foes=[U(100+i,'ogru',1,200,i*30) for i in range(3)]
        hits,_=Engagement().plan(army,foes,0)
        # 09-23 集火上限照 AMAI 放宽（engagement.FOCUS_CAP_*），近战单独限 FOCUS_CAP_MELEE（挤不上去）
        self.assertLessEqual(max(sum(t.addr==f.addr for _,t in hits) for f in foes),ENG.FOCUS_CAP_MELEE)
        self.assertGreater(ENG.FOCUS_CAP_MELEE, 3)

    def test_reinforcement_and_contact_units_have_different_orders(self):
        near=U(1,'hfoo',x=100); far=U(2,'hrif',x=-2000); foe=U(100,'ogru',1,300,0)
        out=T.battle_intents(Engagement(),[near,far],[foe],0,300,0,'守家')
        self.assertTrue(any(i.kind=='attack' and near in i.units for i in out))
        self.assertTrue(any(i.kind=='attack_move' and far in i.units for i in out))

    def test_air_targets_fall_back_to_engine_attack_move(self):
        near=U(1,'hfoo'); air=U(100,'owyv',1,200,0)
        out=T.battle_intents(Engagement(),[near],[air],0,200,0,'守家')
        self.assertEqual(out[0].kind,'attack_move')


if __name__=='__main__':
    unittest.main()

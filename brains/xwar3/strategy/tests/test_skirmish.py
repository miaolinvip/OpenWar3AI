import sys,pathlib,unittest
from types import SimpleNamespace as N
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from brain import battlepower as P
from brain.skirmish import Skirmish
from brain import tactics as T
from brain.world import build_world
from test_runtime_smoke import U


def squad(n,owner=0,typ='hfoo',x=4000,start=10):
    return [U(start+i,typ,x,(i%5)*45,owner=owner) for i in range(n)]


def world(ours,foes,t=100):
    return build_world([U(1,'htow',0,0)]+ours+foes,0,t,100,100,40,80)


class Power(unittest.TestCase):
    def test_double_identical_army_has_double_power(self):
        a=squad(4);b=squad(8,1,x=4200,start=100)
        v=P.estimate(a,b)
        self.assertAlmostEqual(v['ratio'],.5,delta=.03)

    def test_hurt_army_and_distant_reserves_do_not_count_as_full(self):
        a=squad(6);b=squad(6,1,x=4200,start=100)
        full=P.estimate(a,b)['ratio']
        for u in a:u.hp*=.3
        self.assertLess(P.estimate(a,b)['ratio'],full*.6)
        far=squad(20,x=-5000,start=200)
        self.assertAlmostEqual(P.estimate(a+far,b)['ratio'],P.estimate(a,b)['ratio'])

    def test_footmen_cannot_hit_gryphons(self):
        v=P.estimate(squad(12),squad(3,1,'hgry',4200,100))
        self.assertEqual(v['ours']['dps'],0)
        self.assertGreater(v['enemy']['dps'],0)

    def test_archmage_primary_attribute_and_level_are_counted(self):
        h=U(20,'Hamg',4000,0);h.hp=h.hp_max=500
        foe=squad(3,1,x=4200,start=100)
        self.assertGreater(P.estimate([h],foe,{20:1})['ours']['dps'],8)
        self.assertGreater(P.estimate([h],foe,{20:5})['ours']['dps'],P.estimate([h],foe,{20:1})['ours']['dps'])

    def test_reachable_reinforcements_change_longer_horizon(self):
        a=squad(6);b=squad(2,1,x=4200,start=100)+squad(12,1,x=6100,start=200)
        self.assertLess(P.estimate(a,b,horizon=10)['ratio'],P.estimate(a,b,horizon=6)['ratio'])


class Control(unittest.TestCase):
    def setUp(self):
        self.a=squad(6);self.b=squad(18,1,x=4300,start=100)
        self.s=Skirmish();self.home=U(1,'htow',0,0)

    def call(self,t=100,foes=None):
        return self.s.step(world(self.a,self.b if foes is None else foes,t),self.a,self.home)

    def retreat_then_split(self):
        self.assertEqual(self.call()['mode'],'withdraw')
        for u in self.a:u.x-=650
        chase=self.b[:2]
        for u in chase:u.x=3600
        # 主力留下，且已经超出短时间内可支援这片交战的位置。
        for u in self.b[2:]:u.x=7000
        return chase

    def test_overwhelming_full_health_enemy_forces_immediate_retreat(self):
        d=self.call()
        self.assertEqual(d['mode'],'withdraw')
        self.assertLess(d['point'][0],4000)

    def test_weaker_pursuers_can_be_countered_after_separation(self):
        self.retreat_then_split()
        self.assertEqual(self.call(105)['mode'],'withdraw')
        self.assertEqual(self.call(108)['mode'],'counter')

    def test_advantage_without_movement_does_not_flip_orders(self):
        self.call()
        self.b=self.b[:2]
        for t in (105,108,112):self.assertEqual(self.call(t)['mode'],'withdraw')

    def test_enemy_main_army_following_prevents_false_counter(self):
        self.retreat_then_split()
        for u in self.b[2:]:u.x=4800
        for t in (105,108):self.assertEqual(self.call(t)['mode'],'withdraw')

    def test_a_small_numerical_edge_does_not_end_withdrawal(self):
        self.retreat_then_split()
        for u in self.b[:5]:u.x=3600
        for t in (105,108):self.assertEqual(self.call(t)['mode'],'withdraw')

    def test_tower_coverage_prevents_countering_bait(self):
        self.retreat_then_split()
        towers=squad(8,1,'hgtw',3700,300)
        for t in (105,108):
            d=self.s.step(world(self.a,self.b+towers,t),self.a,self.home)
            self.assertEqual(d['mode'],'withdraw')

    def test_target_under_tower_prevents_turn_before_we_enter_tower_range(self):
        self.retreat_then_split()
        tower=U(300,'hgtw',4500,0,owner=1)
        for t in (105,108):
            self.assertEqual(self.s.step(world(self.a,self.b+[tower],t),self.a,self.home)['mode'],'withdraw')

    def test_counter_immediately_yields_to_new_reinforcements(self):
        self.retreat_then_split();self.call(105);self.call(108)
        for u in self.b[2:]:u.x=3900
        self.assertEqual(self.call(109)['mode'],'withdraw')

    def test_counter_keeps_firing_after_chase_budget_when_already_engaged(self):
        self.retreat_then_split();self.call(105);self.call(108)
        for u in self.b[:2]:u.x=3550
        self.assertEqual(self.call(119)['mode'],'counter')

    def test_counter_does_not_chase_back_to_enemy_main_army(self):
        self.retreat_then_split();self.call(105);self.call(108)
        for u in self.b:u.x=5500
        self.assertEqual(self.call(109)['mode'],'withdraw')

    def test_moderately_outnumbered_contact_is_fought_not_fled(self):
        """09-22 门槛两侧：战力比 0.45~0.62 一照面就撤，自己没少死、杀敌只剩一半 —— 6 打 11（比值约 0.55）打，不撤。"""
        foes = squad(11, 1, x=4300, start=100)
        self.assertGreater(P.estimate(self.a, foes)['ratio'], .45)
        self.assertLess(P.estimate(self.a, foes)['ratio'], .62)
        self.assertEqual(self.call(foes=foes)['mode'], 'fight')

    def test_fight_turning_moderately_bad_is_finished_but_a_rout_still_withdraws(self):
        self.assertEqual(self.call(foes=self.b[:6])['mode'], 'fight')
        self.assertEqual(self.call(102, foes=self.b[:11])['mode'], 'fight', "援军到了、比值掉到 0.55：打完这一波")
        self.assertEqual(self.call(104, foes=self.b[:14])['mode'], 'withdraw', "掉到 0.43：撤")

    def test_even_fight_keeps_output_and_defense_is_not_overridden(self):
        self.assertEqual(self.call(foes=self.b[:6])['mode'],'fight')
        for u in self.a:u.x=200
        self.assertIsNone(self.call())

    def test_strategy_routes_contact_to_one_controller(self):
        s=T.AttackSequence({});s.state='attack'
        w=world(self.a,self.b)
        intents=s.step(w,self.home)
        self.assertTrue(intents)
        self.assertTrue(all(i.kind=='move' and i.control=='skirmish' for i in intents))
        self.assertEqual(s.skirmish.phase,'withdraw')

    def test_interrupt_clears_counter_state(self):
        s=T.AttackSequence({});s.skirmish.phase='counter';s.interrupt()
        self.assertEqual(s.skirmish.phase,'idle')

    def test_return_home_ends_withdrawal_instead_of_immediately_reengaging(self):
        self.call()
        for u in self.a:u.x=200
        self.assertEqual(self.call(105)['mode'],'disengaged')

    def test_controller_renews_deduplicated_orders_but_cannot_steal_safety(self):
        from brain.act import Actuator
        from test_runtime_smoke import FakeCmd
        calls=[]
        claim=N(take=lambda addr,seconds,prio: calls.append((addr,prio)) or addr!=11)
        cmd=FakeCmd();a=Actuator(cmd,claim)
        troops=self.a[:2]
        for u in troops:u.order=0xD0003;u.has_task=True
        it=T.Intent('move',troops,3000,0,why='后撤',control='skirmish')
        a.skirmish(it);a.skirmish(it)
        self.assertEqual(len(cmd.moves),1)  # 不重下移动令，只续租；11 号由个人保命持有。
        self.assertTrue(all(prio==76 for _,prio in calls))

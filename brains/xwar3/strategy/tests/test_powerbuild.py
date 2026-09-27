import json
import os
import tempfile
import unittest
from test_runtime_smoke import Brain, FakeCmd, U, A
from brain.world import build_world
from brain import races as R


class Claims:
    def __init__(self):
        self.blocked = set()
        self.released = []

    def take(self, addr, seconds, prio):
        return addr not in self.blocked

    def give_back(self, addr):
        self.released.append(addr)


class TestPowerBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cmd, self.claims = FakeCmd(), Claims()
        self.brain = Brain(self.cmd,self.claims,self.tmp.name)
        self.home = U(1,'htow',0,0)
        self.site = U(2,'htow',2200,0)
        self.site.hp = 100
        self.primary = U(3,'hpea',2200,100,order=A.rawcode('htow'),has_task=True)
        self.helper = U(4,'hpea',1800,0)
        self.units = [self.home,self.site,self.primary,self.helper]
        self.gold,self.wood,self.used,self.cap = 400,200,20,30
        self.register()

    def world(self,t=100):
        return build_world(self.units,0,t,self.gold,self.wood,self.used,self.cap)

    def register(self):
        self.brain.pending = [dict(kind='expand' if self.site.type=='htow' else 'building',code=self.site.type,
                                   worker=self.primary.addr,t=98,had=1 if self.site.type=='htow' else 0,
                                   x=self.site.x,y=self.site.y)]
        self.brain._prune_pending(self.world())

    def advance(self,t=104):
        self.site.hp += 30
        self.brain.job_construction(self.world(t))

    def farm(self):
        self.brain.powerbuild.sites.clear()
        self.site.type='hhou';self.site.hp_max=500;self.site.x=200
        self.primary.x=200;self.primary.order=A.rawcode('hhou');self.helper.x=300
        self.used=self.cap=20
        self.register()

    def test_safe_expansion_adds_exactly_one_and_reserves_both_workers(self):
        self.advance()
        self.assertEqual(self.cmd.gathers,[(4,2,A.ORDER_REPAIR)])
        self.helper.order=A.ORDER_REPAIR;self.helper.has_task=True
        self.advance(108)
        self.assertEqual(len(self.cmd.gathers),1)
        self.assertTrue(self.brain.powerbuild.helper['confirmed'])
        self.assertNotIn(self.helper,self.brain._builder(self.world()))
        self.assertNotIn(self.primary,self.brain._builder(self.world()))
        self.assertEqual(self.brain._idle_workers(self.world(120)),[])
        with open(os.path.join(self.tmp.name,'reserved.json'),encoding='utf-8') as f:
            reserved=json.load(f)['units']
        self.assertEqual(set(reserved),{'3','4'})
        self.assertEqual(reserved['4']['handleLo'],4)

    def test_blocked_farm_but_not_regular_farm(self):
        self.farm();self.used=15
        self.advance();self.assertFalse(self.cmd.gathers)
        self.used=20;self.advance(108)
        self.assertEqual(self.cmd.gathers,[(4,2,A.ORDER_REPAIR)])

    def test_needs_observed_progress_and_keeps_other_builders(self):
        self.brain.job_construction(self.world(104))
        self.assertFalse(self.cmd.gathers)
        self.helper.order=A.rawcode('hbar');self.helper.has_task=True
        self.advance(108);self.assertFalse(self.cmd.gathers)

    def test_no_gold_worker_or_last_lumberjacks(self):
        self.brain.gold_crew[4]=999
        self.advance();self.assertFalse(self.cmd.gathers)
        self.brain.gold_crew.clear();self.brain.lumber_crew={4:900,5:900,6:900}
        self.helper.order=A.ORDER_HARVEST;self.helper.has_task=True
        self.advance(108);self.assertFalse(self.cmd.gathers)
        self.brain.lumber_crew[7]=900
        self.advance(112);self.assertEqual(len(self.cmd.gathers),1)

    def test_low_resources_and_pending_money_block(self):
        self.gold=149;self.advance();self.assertFalse(self.cmd.gathers)
        self.gold=400
        self.brain.pending.append(dict(kind='expand',code='htow',worker=77,t=104))
        self.advance(108);self.assertFalse(self.cmd.gathers)

    def test_enemy_or_creeps_on_site_or_route_block(self):
        for foe in [U(90,'hfoo',2200,0,owner=1),U(91,'nogr',1800,100,owner=12)]:
            self.units.append(foe);self.advance();self.assertFalse(self.cmd.gathers);self.units.remove(foe)

    def test_finished_releases_helper_and_reservation(self):
        self.advance();self.helper.order=A.ORDER_REPAIR
        self.site.hp=self.site.hp_max
        self.brain.job_construction(self.world(108))
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertEqual(self.cmd.trains,[(4,0xD0004)])
        self.helper.order=0;self.helper.has_task=False
        self.assertIn(self.helper,self.brain._idle_workers(self.world(120)))
        with open(os.path.join(self.tmp.name,'reserved.json'),encoding='utf-8') as f:
            self.assertEqual(json.load(f)['units'],{})
        self.units.append(U(30,'ngol',2200,200,owner=15))
        self.brain.job_gold(self.world(120))
        self.assertEqual(self.cmd.gathers[-1],(4,30,A.ORDER_HARVEST))

    def test_native_humanbuild_order_and_low_budget_release(self):
        self.primary.order=0xD001B
        self.advance();self.assertEqual(len(self.cmd.gathers),1)
        self.helper.order=0xD001B;self.advance(108)
        self.assertTrue(self.brain.powerbuild.helper['confirmed'])
        self.wood=39;self.advance(112)
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertEqual(self.cmd.trains,[(4,0xD0004)])

    def test_threat_withdraws_helper(self):
        self.advance()
        self.units.append(U(90,'hfoo',2300,0,owner=1))
        self.advance(108)
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertEqual(self.cmd.moves[-1][:3],(4,0,0))

    def test_safety_claim_not_overridden_or_released(self):
        self.advance();self.claims.blocked.add(4)
        self.advance(108)
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertFalse(self.cmd.trains or self.cmd.moves or self.claims.released)

    def test_site_destroyed_or_identity_reused(self):
        self.advance();self.site.handle_hi=2
        self.brain.job_construction(self.world(108))
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertFalse(self.brain.powerbuild.sites)

    def test_reused_worker_not_ordered_to_stop(self):
        self.advance();self.helper.handle_hi=2
        self.advance(108)
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertFalse(self.cmd.trains)

    def test_disabled_releases_and_nonhuman_never_assists(self):
        self.advance();self.brain.config.values['enable_powerbuild']=False
        self.advance(108);self.assertIsNone(self.brain.powerbuild.helper)
        self.brain.config.values['enable_powerbuild']=True;self.brain.spec=R.ORC
        self.advance(140);self.assertEqual(len(self.cmd.gathers),1)

    def test_unconfirmed_order_timeout_and_cooldown(self):
        self.advance();self.advance(120)
        self.assertIsNone(self.brain.powerbuild.helper)
        self.assertEqual(len(self.cmd.gathers),1)

    def test_damaged_completed_building_not_adopted(self):
        self.brain.powerbuild.sites.clear()
        self.brain.seen_full.add(self.site.addr)
        self.advance();self.assertFalse(self.brain.powerbuild.sites)
        self.assertIn(self.site.addr,self.brain.repairs.tasks)

    def test_one_global_helper_with_two_eligible_sites(self):
        other=U(8,'hhou',200,0);other.hp=100
        worker=U(9,'hpea',200,100,order=A.rawcode('hhou'),has_task=True)
        self.units.extend([other,worker]);self.used=self.cap
        self.brain.powerbuild.observe(self.world(),dict(code='hhou',worker=9,x=200,y=0))
        other.hp+=30;self.helper.x=800
        self.advance()
        self.assertEqual(self.cmd.gathers,[(4,8,A.ORDER_REPAIR)])

    def test_former_militia_is_preferred_as_expansion_helper(self):
        closer=U(5,'hpea',2150,0)
        self.units.append(closer)
        self.brain.militia_expand_builders={self.helper.addr}
        self.brain.militia_expand_until=200
        self.advance()
        self.assertEqual(self.cmd.gathers,[(self.helper.addr,self.site.addr,A.ORDER_REPAIR)])
        self.assertNotIn(self.helper.addr,self.brain.militia_expand_builders)


if __name__=='__main__':
    unittest.main()

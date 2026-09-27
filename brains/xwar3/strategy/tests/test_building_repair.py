import unittest
import test_powerbuild as fixtures
from test_runtime_smoke import U, A


class TestBuildingRepair(unittest.TestCase):
    def setUp(self):
        f=fixtures.TestPowerBuild();f.setUp();self.addCleanup(f.tmp.cleanup)
        self.f=f;self.b=f.brain
        # 用已建好的兵营；独立冲突测试另行创建分矿地基。
        f.site.hp=f.site.hp_max;self.b.powerbuild.sites.clear()
        self.bar=U(40,'hbar',200,0)
        f.helper.x=250
        f.units.append(self.bar)
        self.b.job_construction(f.world(100))

    def tick(self,t=104):
        self.b.job_construction(self.f.world(t))

    def test_damage_dispatch_and_health_receipt(self):
        self.bar.hp-=300;self.tick()
        self.assertEqual(self.f.cmd.gathers,[(4,40,A.ORDER_REPAIR)])
        self.f.helper.order=A.ORDER_REPAIR
        self.bar.hp+=50;self.tick(108)
        self.assertTrue(self.b.repairs.tasks[40]['confirmed'])
        self.assertTrue(self.b.repairs.tasks[40]['progress'])
        self.assertEqual(len(self.f.cmd.gathers),1)
        self.assertNotIn(self.f.helper,self.b._builder(self.f.world()))

    def test_full_health_releases_to_economy(self):
        self.bar.hp-=300;self.tick();self.bar.hp=self.bar.hp_max;self.tick(108)
        self.assertFalse(self.b.repairs.tasks)
        self.assertEqual(self.f.cmd.trains,[(4,0xD0004)])
        self.assertIn(self.f.helper,self.b._idle_workers(self.f.world(120)))

    def test_unknown_foundation_not_repaired(self):
        self.b.repairs.completed.clear();self.b.seen_full.discard(self.bar.addr);self.bar.hp=50
        self.tick();self.assertFalse(self.b.repairs.tasks)

    def test_safety_control_preserved(self):
        self.bar.hp-=300;self.tick();self.f.claims.blocked.add(4);self.tick(108)
        self.assertFalse(self.b.repairs.tasks)
        self.assertFalse(self.f.cmd.trains or self.f.claims.released)

    def test_threatened_worker_leaves(self):
        self.bar.hp-=300;self.tick()
        self.f.units.append(U(90,'ogru',300,0,owner=1));self.tick(108)
        self.assertFalse(self.b.repairs.tasks)
        self.assertEqual(self.f.cmd.moves[-1][:3],(4,0,0))

    def test_low_budget_does_not_dispatch(self):
        self.bar.hp-=300;self.f.gold=74;self.tick()
        self.assertFalse(self.b.repairs.tasks)

    def test_building_and_worker_reuse_do_not_inherit_repair(self):
        self.bar.hp-=300;self.tick();self.bar.handle_hi+=1;self.f.helper.handle_hi+=1
        self.tick(108)
        self.assertFalse(self.b.repairs.tasks)
        self.assertFalse(self.f.cmd.trains)

    def test_two_workers_cap_and_hall_priority(self):
        self.bar.hp-=300;self.f.home.hp-=300
        tower=U(41,'hgtw',500,0);self.f.units.append(tower)
        self.b.repairs.completed[41]=(tower.addr,tower.handle_lo,tower.handle_hi);tower.hp-=200
        self.f.units.extend([U(42,'hpea',520,0),U(43,'hpea',250,0)])
        self.tick()
        self.assertEqual(set(self.b.repairs.tasks),{1,40,41})

    def test_midgame_attach_repairs_damaged_completed_hall(self):
        self.b.repairs.completed.clear();self.b.seen_full.clear();self.f.home.hp=.65*self.f.home.hp_max
        self.tick()
        self.assertIn(self.f.home.addr,self.b.repairs.tasks)

    def test_repair_and_powerbuild_do_not_share_worker(self):
        self.f.site.hp=100;self.f.register()
        self.bar.hp-=300;self.f.advance()
        self.assertTrue(self.b.repairs.tasks)
        self.assertIsNone(self.b.powerbuild.helper)

    def test_disabling_repair_releases_worker(self):
        self.bar.hp-=300;self.tick();self.b.config.values['enable_repair']=False;self.tick(108)
        self.assertFalse(self.b.repairs.tasks)


if __name__=='__main__':unittest.main()

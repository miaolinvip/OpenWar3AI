import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C
from test_hero_local_safety import unit


class CombatCohesion(unittest.TestCase):
    def test_hero_with_approaching_tanks_is_not_pulled_before_creep_contact(self):
        h=unit(1,'Hamg',hp=450,maximum=450)
        friends=[unit(2+i,'hfoo',50+i*30,0,420,420) for i in range(3)]
        creeps=[unit(100,'ngnb',850,0,400,400,12),unit(101,'ngna',860,80,240,240,12)]
        self.assertIsNone(C.hero_exposure(h,creeps,[h]+friends))

    def test_lone_hero_in_actual_creep_contact_still_flees(self):
        # 09-23 起满血英雄对野怪顶着打（CREEP_TANK_FRAC，对局 1-418）；血掉到一半以下照旧撤，快死了由 aggro_pulls 按 TTD 拉
        creeps=[unit(100+i,'nogm',200,i*30,800,800,12) for i in range(2)]
        self.assertIsNone(C.hero_exposure(unit(1,'Hamg'),creeps,[unit(1,'Hamg')]))
        h=unit(1,'Hamg',hp=250)
        self.assertIsNotNone(C.hero_exposure(h,creeps,[h]))

    def test_safe_hero_returns_to_ranged_creep_position_without_ten_second_wait(self):
        h=unit(1,'Hamg',x=-600)
        creeps=[unit(100+i,'nogm',0,i*50,800,800,12) for i in range(2)]
        feet=[unit(10+i,'hfoo',-90,i*40,420,420) for i in range(3)]
        self.assertTrue(C.hero_return_ready(h,creeps,[h]+feet))

    def test_weak_hero_does_not_return_just_because_enemy_temporarily_disappeared(self):
        h=unit(1,'Hamg',hp=200,maximum=500)
        self.assertFalse(C.hero_return_ready(h,[],[h]))

    def test_seventy_percent_hero_waits_before_reentering_battle(self):
        h=unit(1,'Hamg',hp=350,maximum=500)
        self.assertFalse(C.hero_return_ready(h,[],[h]))
        h.hp=375
        self.assertTrue(C.hero_return_ready(h,[],[h]))

    def test_stationary_hurt_footman_gets_progress_retry(self):
        w=C.RetreatWatch(); h=unit(1,'hfoo',hp=240,maximum=420)
        foe=unit(100,'ngnb',150,0,400,400,12); pulled={1:8}
        self.assertEqual(w.check([h],[foe],pulled,0),[])
        h.hp=140
        retries=w.check([h],[foe],pulled,1)
        self.assertEqual([(u.addr,n) for u,foes,n in retries],[(1,1)])
        self.assertEqual(pulled,{1:8})

    def test_walking_away_does_not_restart_path(self):
        w=C.RetreatWatch(); h=unit(1,'hfoo'); foe=unit(100,'ngnb',200,0,400,400,12)
        w.check([h],[foe],{1:8},0); h.x=-300
        self.assertEqual(w.check([h],[foe],{1:8},1),[])

    def test_moving_while_chased_does_not_restart_path(self):
        w=C.RetreatWatch(); h=unit(1,'hfoo'); foe=unit(100,'ngnb',200,0,400,400,12)
        w.check([h],[foe],{1:8},0); h.y=100; h.hp-=80; foe.y=100
        self.assertFalse(w.check([h],[foe],{1:8},1))

    def test_retries_are_bounded_and_lease_expiry_stops_them(self):
        w=C.RetreatWatch(); h=unit(1,'hfoo'); foe=unit(100,'ngnb',150,0,400,400,12)
        total=sum(len(w.check([h],[foe],{1:8},t)) for t in range(12))
        self.assertEqual(total,4)
        self.assertFalse(w.samples)

    def test_sacrifice_is_not_rescued(self):
        w=C.RetreatWatch(); h=unit(1,'hfoo'); foe=unit(100,'ngnb',150,0,400,400,12)
        self.assertFalse(w.check([h],[foe],{1:8},0,{1}))
        self.assertFalse(w.check([h],[foe],{1:8},1,{1}))

    def test_retry_does_not_alternate_direction_without_new_evidence(self):
        h=unit(1,'hfoo'); foe=unit(100,'ngnb',150,0,400,400,12)
        x1,y1=C.retreat_retry_point(h,[foe],[],(-2000,0),1)
        x2,y2=C.retreat_retry_point(h,[foe],[],(-2000,0),2,(x1,y1))
        self.assertLess(x1,0); self.assertLess(x2,0)
        self.assertEqual((x1,y1),(x2,y2))

    def test_healthy_hero_keeps_firing_while_creeps_hit_retreating_tanks(self):
        h=unit(1,'Hamg',hp=650,maximum=650)
        tank=unit(2,'hfoo',200,0,100,420);tank.handle_lo=2;tank.handle_hi=1
        foes=[unit(100+i,'nogm',200,i*30,800,800,12) for i in range(2)]
        targets={f.addr:(2,1) for f in foes}
        self.assertIsNone(C.hero_exposure(h,foes,[h,tank],{2},targets))
        targets[100]=C._pair(h)
        self.assertIsNone(C.hero_exposure(h,foes,[h,tank],{2},targets), "09-23：满血被野怪盯上也先顶（CREEP_TANK_FRAC）")
        h.hp=320
        self.assertIsNotNone(C.hero_exposure(h,foes,[h,tank],{2},targets))

    def test_low_hero_is_not_protected_by_tank_exception(self):
        h=unit(1,'Hamg',hp=100,maximum=650)
        tank=unit(2,'hfoo',200,0,100,420);tank.handle_lo=2;tank.handle_hi=1
        foes=[unit(100+i,'nogm',200,i*30,800,800,12) for i in range(2)]
        self.assertIsNotNone(C.hero_exposure(h,foes,[h,tank],{2},{f.addr:(2,1) for f in foes}))

    def test_changed_creep_task_invalidates_target_evidence(self):
        c=unit(10,'nogm',200,0,800,800,12);a=C.CreepAggro()
        a.put(c,(2,1),1)
        self.assertEqual(a.targets([c],1),{10:(2,1)})
        c.task_handle_lo=99
        self.assertEqual(a.targets([c],1),{})

    def test_attack_order_while_retreating_is_recovered(self):
        w=C.RetreatWatch();h=unit(1,'Hmkg',hp=100,maximum=850)
        foe=unit(100,'ogru',100,0,700,700,1)
        w.check([h],[foe],{1:8},0)
        h.x=-100;h.order=0xD000F
        self.assertTrue(w.check([h],[foe],{1:8},1))

    def test_clear_route_prefers_home_over_enemy_side(self):
        h=unit(1,'Hmkg',hp=300,maximum=850)
        foes=[unit(100+i,'ogru',200,i*80,700,700,1) for i in range(3)]
        x,y=C.retreat_retry_point(h,foes,[],(-2000,0),1)
        self.assertLess(x,-400)

    def test_isolated_hero_initial_escape_can_double_separation(self):
        h=unit(1,'Hamg',hp=500,maximum=650)
        foes=[unit(100+i,'ogru',200,i*80,700,700,1) for i in range(3)]
        x,y=C.retreat_retry_point(h,foes,[],(-3000,0),0,distance=C.PULL_BACK*2)
        self.assertGreater((x*x+y*y)**.5,C.PULL_BACK*1.9)


if __name__=='__main__':
    unittest.main()

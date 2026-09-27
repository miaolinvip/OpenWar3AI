import pathlib
import sys
import unittest
sys.path[:0]=[str(pathlib.Path(__file__).resolve().parents[1]),str(pathlib.Path(__file__).resolve().parents[2]/'war3-modules')]
import itemuse as I
from brain.recovery import ArmyRecovery
from test_tactics import U,world


class Cmd:
    USESLOT1=0xD0028
    def __init__(self,bag=None): self.bag=([None]*6 if bag is None else bag); self.bags={};self.active=None;self.buys=[];self.uses=[]
    def inventory(self,a):
        bag=self.bags[a] if a in self.bags else (self.bag if a==3 else None);self.active=a
        return list(bag) if bag is not None else None
    def buy_item(self,a,c):
        self.buys.append(c);bag=self.bags[self.active] if self.active in self.bags else self.bag;bag[bag.index(None)]='sreg';return True
    def use_item(self,a,order):
        self.uses.append(order);bag=self.bags[a] if a in self.bags else self.bag;bag[order-self.USESLOT1]=None;return True


class Recovery(unittest.TestCase):
    def scene(self):
        troops=[U(10+i,'hfoo',x=450+i*15,hp=180,hp_max=420) for i in range(6)]
        w=world([U(1,'htow'),U(2,'hvlt',x=300),U(3,'Hamg',x=500)]+troops,t=100)
        w.gold=1000
        return w,troops

    def test_buy_gather_use_then_release_at_once(self):
        """★ 09-22 整队等回血耗时过长（1-290）—— 原来放出后进 healing 按住整队最多 45 游戏秒。
        恢复卷轴是非战斗类（挨打才打断、走路不打断）：确认用掉就放人，边走边回血。"""
        w,troops=self.scene();r=ArmyRecovery();c=Cmd()
        self.assertIsNotNone(r.step(w,c,None));self.assertEqual(r.phase,'buying')
        self.assertEqual(len(c.buys),1)
        w.t+=3;r.step(w,c,None);self.assertEqual(r.phase,'used')
        w.t+=3;self.assertIsNone(r.step(w,c,None),"用掉了就不再给队形令")
        self.assertEqual(r.phase,'idle')
        self.assertEqual(r.counts['整队恢复·放出后不等·边走边回'],1)
        self.assertEqual(len(c.buys),1);self.assertEqual(len(c.uses),1)

    def test_full_or_unreadable_bag_does_not_pull_army_to_shop(self):
        for bag in (['stwp']*6,None):
            w,_=self.scene();c=Cmd();c.bag=bag
            self.assertIsNone(ArmyRecovery().step(w,c,None));self.assertFalse(c.buys)

    def test_brief_enemy_contact_pauses_then_resumes_recovery(self):
        w,_=self.scene();r=ArmyRecovery();c=Cmd();r.step(w,c,None)
        self.assertIsNone(r.step(w,c,None,blocked=True));self.assertNotEqual(r.phase,'idle')
        w.t+=12
        self.assertIsNotNone(r.step(w,c,None));self.assertEqual(r.blocked_since,-1)

    def test_sustained_contact_cancels_recovery(self):
        w,_=self.scene();r=ArmyRecovery();c=Cmd();r.step(w,c,None)
        r.step(w,c,None,blocked=True);w.t+=46
        self.assertIsNone(r.step(w,c,None,blocked=True));self.assertEqual(r.phase,'idle')

    def test_insufficient_gold_keeps_production_reserve(self):
        """★ 09-21：救急买卷轴走的是更低的那条金线（itemuse.REGEN_BUY_FLOOR）。

        连续四局出现同一问题：一堆残血单位等恢复卷轴，却一直没有操作；
        而一个卷轴远比一个兵（尤其高级兵）便宜。
        已经有三个以上伤兵在等的时候，100 金回 450+ 血比拿这 100 金造兵划算，
        所以不再要求「买完还剩 450」。但钱**真的**不够时仍然不买。
        ⚠ 期望值是按公式算的：门槛 = PRICE['sreg']，不是手写一个数。
        ★ 09-22（1-297：造兵一直占用经济，导致卷轴买不了）：不再要求买完还剩 REGEN_BUY_FLOOR ——
        钱不够时在建造预算里预留卷轴价（reserve），生产让路，攒够卷轴价就买。"""
        floor = I.PRICE['sreg']
        w,_=self.scene();w.gold=floor-1;c=Cmd();r=ArmyRecovery()
        self.assertIsNone(r.step(w,c,None));self.assertFalse(c.buys);self.assertEqual(r.reserve,floor)
        w,_=self.scene();w.gold=floor;c=Cmd()
        self.assertIsNotNone(ArmyRecovery().step(w,c,None))

    def test_owned_scroll_works_without_buying_money(self):
        w,_=self.scene();w.gold=0;c=Cmd(['sreg']+[None]*5);r=ArmyRecovery()
        r.step(w,c,None);self.assertFalse(c.buys);self.assertEqual(len(c.uses),1)

    def test_failed_receipt_does_not_repeat_buy_or_wait_forever(self):
        w,_=self.scene();r=ArmyRecovery();c=Cmd()
        c.buy_item=lambda a,code: c.buys.append(code) or True
        r.step(w,c,None)
        w.t+=6;r.step(w,c,None);self.assertEqual(len(c.buys),1)
        w.t+=8;self.assertIsNone(r.step(w,c,None));self.assertEqual(r.phase,'idle')

    def test_scroll_not_used_until_wounded_troops_arrive(self):
        w,troops=self.scene();c=Cmd(['sreg']+[None]*5);r=ArmyRecovery()
        for u in troops:u.x=2000
        r.step(w,c,None);self.assertFalse(c.uses)
        for u in troops:u.x=500
        w.t+=3;r.step(w,c,None);self.assertEqual(len(c.uses),1)

    def test_scroll_waits_for_every_wounded_troop(self):
        w,troops=self.scene();c=Cmd(['sreg']+[None]*5);r=ArmyRecovery()
        troops[-1].x=1200
        r.step(w,c,None);self.assertFalse(c.uses)
        troops[-1].x=700
        w.t+=3;r.step(w,c,None);self.assertEqual(len(c.uses),1)

    def test_missing_straggler_cannot_hold_whole_army_forever(self):
        w,troops=self.scene();c=Cmd(['sreg']+[None]*5);r=ArmyRecovery()
        troops[-1].x=3000
        self.assertIsNotNone(r.step(w,c,None));self.assertEqual(r.phase,'gather')
        w.t+=36
        self.assertIsNone(r.step(w,c,None));self.assertEqual(r.phase,'idle')
        self.assertFalse(c.uses);self.assertIn('保留卷轴继续行动',r.notes[-1])

    def test_healthy_troop_in_the_circle_does_not_hold_the_scroll(self):
        """★ 09-22 单位已集结到位却迟迟不用卷轴，最后散开（1-301 9:55）—— 原来这条测试钉的是
        「满血单位先退出作用圈才放」：满血的兵一直站在圈里，伤兵全到了也不放，35 秒后集合超时放弃。
        卷轴 600 内所有单位各回 225、没有人数上限：满血的站在里面不挡任何人。伤兵到齐当拍就放，满血的也不赶走。"""
        w,_=self.scene();healthy=U(30,'hfoo',x=520,hp=420,hp_max=420);w.my_army.append(healthy)
        c=Cmd(['sreg']+[None]*5);r=ArmyRecovery()
        out=r.step(w,c,None)
        self.assertEqual(len(c.uses),1)
        self.assertFalse([i for i in (out or []) if healthy in i.units], "满血的不被赶出圈")

    def test_only_hero_walks_to_shop_before_scroll_is_owned(self):
        w,troops=self.scene();w.my_heroes[0].x=2000;r=ArmyRecovery();c=Cmd()
        out=r.step(w,c,None)
        self.assertEqual(r.phase,'shopping')
        self.assertEqual([u.addr for u in out[0].units],[w.my_heroes[0].addr])
        self.assertFalse(c.buys)

    def test_committed_recovery_can_buy_after_gold_drops_below_reserve(self):
        w,_=self.scene();w.my_heroes[0].x=1000;r=ArmyRecovery();c=Cmd()
        r.step(w,c,None);self.assertEqual(r.phase,'shopping')
        w.my_heroes[0].x=300;w.gold=100;w.t+=3
        r.step(w,c,None)
        self.assertEqual(len(c.buys),1);self.assertEqual(r.phase,'buying')

    def test_backpack_ground_unit_never_buys_or_holds_the_scroll(self):
        """★ 09-22：兵研究 Backpack 后的背包是 Aihn，AbilityData.slk DataC「能使用物品」= 0 —— 能拿不能用。
        全部实例历史：兵拿卷轴用 165 次全被拒（hrif 78、hfoo 67、hkni 16…），英雄 80 次全成。
        原来这条测试钉的正是那个错行为（英雄背包满就让步兵去买）。"""
        w,troops=self.scene();c=Cmd(['stwp']*6)
        c.bags[troops[0].addr]=[None]*6
        for u in troops[1:]: c.bags[u.addr]=None
        r=ArmyRecovery();self.assertIsNone(r.step(w,c,None))
        self.assertEqual((len(c.buys),r.phase),(0,'idle'))
        # 兵背包里已经有一张卷轴（商店交错了人）：也不当「有卷轴的人」
        c.bags[troops[0].addr]=['sreg']+[None]*5
        r=ArmyRecovery();self.assertIsNone(r.step(w,c,None))
        self.assertIsNone(r.holder_id);self.assertFalse(c.uses)

"""成品建筑维修。与地基续建/双敲分开记身份、派工和结束回采。"""
from dataclasses import dataclass, field
from . import act as A
from .powerbuild import distance, identity


@dataclass
class BuildingRepair:
    completed: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    cooldown: dict = field(default_factory=dict)

    @property
    def busy_addrs(self):
        return {t['worker'] for t in self.tasks.values()}

    def unsafe(self, w, u, emergency=False):
        """平时保守；敌人已在拆家时允许健康农民贴着守军抢修，但仍不送进贴脸/塔火力。"""
        foe_r = 220 if emergency else 500
        tower_r = 700 if emergency else 1000
        hp_floor = .55 if emergency else .6
        return (u.hp<hp_floor*u.hp_max or any(distance(u,f)<foe_r for f in w.enemy_fighters+w.creeps)
                or any(distance(u,f)<tower_r for f in w.enemy_towers))

    def release(self, brain, w, addr, why, danger=False):
        t=self.tasks.pop(addr)
        self.cooldown[addr]=w.t+12
        u=next((u for u in w.my_workers if identity(u)==t['identity']),None)
        if u and brain.act._claim(u.addr,3,'strategy_eco'):
            if danger and (home:=w.main_town()) is not None:
                brain.act.move([u],home.x,home.y)
            else:
                brain.cmd.immediate_order(u.addr,0xD0004)
            if brain.claims is not None:brain.claims.give_back(u.addr)
            brain.sent_at[u.addr]=w.t
        brain.counts['维修·结束·'+why]+=1
        brain.note(w.t,f"维修结束：{t['code']}，{why}，工人 0x{t['worker']:08X}")

    def step(self, brain, w):
        bs={b.addr:b for b in w.my_buildings}
        us={u.addr:u for u in w.my_workers}
        self.completed={a:i for a,i in self.completed.items() if a in bs and identity(bs[a])==i}
        for b in w.my_buildings:
            if b.hp_max>0 and b.hp>=.97*b.hp_max:
                self.completed[b.addr]=identity(b)
        # runtime 的续建器也会记录亲眼见过成品的地址。共享这份证据，避免大脑重载后建筑
        # 先挨打一刀，维修器因没见过 100% 血就把它误当未完成地基。
        for b in w.my_buildings:
            if b.addr in brain.seen_full:
                self.completed[b.addr]=identity(b)
            elif b.hp_max > 0 and b.hp >= .5*b.hp_max and b.addr not in brain.powerbuild.sites:
                # 中途接管时可能第一张快照里的成品已经受损。半血以上且不是已知工地就先纳入
                # 维护；即使它其实是失去主建造工的高进度地基，repair/smart 也会把工程推进。
                self.completed[b.addr]=identity(b)
        enabled=brain.spec['name']=='human' and brain.config['enable_repair']
        cg,cl=brain.committed();gold,wood=w.gold-cg,w.lumber-cl
        for a,t in list(self.tasks.items()):
            b,u=bs.get(a),us.get(t['worker'])
            valid=bool(b and self.completed.get(a)==identity(b) and u and identity(u)==t['identity'])
            danger=valid and self.unsafe(w,u,t.get('emergency',False))
            if not enabled or not valid or danger or gold<25 or wood<10 or b.hp>=.999*b.hp_max:
                why='危险' if danger else ('修满' if valid and b.hp>=.999*b.hp_max else '暂停或目标失效')
                self.release(brain,w,a,why,danger)
                continue
            if not brain.act._claim(u.addr,6,'strategy_eco'):
                self.release(brain,w,a,'让出自保或人工指挥')
                continue
            if b.hp>t['hp']+1:
                if not t['progress']:
                    brain.note(w.t,f"维修回血确认：{b.type} {int(t['hp'])}->{int(b.hp)}")
                    brain.counts['维修·回血确认']+=1
                t['progress']=True
            t['hp']=b.hp
            if u.order==A.ORDER_REPAIR:
                t['confirmed']=True
            elif w.t-t['since']>12:
                self.release(brain,w,a,'未确认维修订单')
        if not enabled:
            return
        candidates=[b for b in w.my_buildings if self.completed.get(b.addr)==identity(b)
                    and b.hp<.985*b.hp_max and b.hp_max-b.hp>=30 and b.addr not in self.tasks
                    and b.addr not in brain.powerbuild.sites and w.t>=self.cooldown.get(b.addr,0)]
        # 大厅、塔优先，其次按受损程度；每座一人，总计至多两人。
        candidates.sort(key=lambda b:(b.type not in ('htow','hkee','hcas','hgtw','hatw','hctw'),b.hp/b.hp_max))
        for b in candidates:
            if len(self.tasks)>=3:break
            foes=[f for f in w.enemy_fighters+w.creeps if distance(b,f)<1000]
            defenders=[u for u in w.my_army+w.my_heroes if u.hp>.5*u.hp_max and distance(b,u)<1000]
            emergency=bool(foes) or b.type in ('htow','hkee','hcas') and b.hp<.7*b.hp_max
            if (gold < (25 if emergency else 75) or wood < (8 if emergency else 30)
                    or (foes and not defenders and b.hp>=.5*b.hp_max)):
                continue
            # 先用空闲/伐木农民；大厅、塔正在挨打或已重伤时允许从金矿抽一个最近农民。
            workers=[u for u in brain._builder(w) if u.type=='hpea' and u.hp>=.75*u.hp_max
                     and getattr(u,'order_known',False)
                     and (((not u.order and u.has_task is False) or (u.addr in brain.lumber_crew
                           and u.order in A.HARVEST_ORDERS and len(brain.lumber_crew)>2))
                          or (emergency and u.addr in brain.gold_crew))
                     and distance(u,b)<2600 and not self.unsafe(w,u,emergency)]
            for u in sorted(workers,key=lambda u:(u.addr in brain.gold_crew,bool(u.order),distance(u,b))):
                if not brain.act.gather(u,b,order=A.ORDER_REPAIR):continue
                self.tasks[b.addr]=dict(worker=u.addr,identity=identity(u),code=b.type,since=w.t,
                                        hp=b.hp,progress=False,confirmed=False,emergency=emergency)
                brain.gold_crew.pop(u.addr,None);brain.gold_attempts.pop(u.addr,None)
                brain.lumber_crew.pop(u.addr,None);brain.sent_at[u.addr]=w.t
                brain.counts['维修·派出·'+b.type]+=1
                brain.note(w.t,f"维修派出：{b.type} {int(b.hp)}/{int(b.hp_max)}，工人 0x{u.addr:08X}")
                gold-=50;wood-=20
                break

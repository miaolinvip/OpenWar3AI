"""人族关键地基双敲：只追踪亲自下令产生的地基，不把受损成品当工地。"""
from dataclasses import dataclass, field
import math
from types import SimpleNamespace
from . import act as A

HUMAN_BUILD = 0x000D001B  # 本地 order-ids.txt：施工中的人族建造订单。
ORC_BUILD = 0x000D001C    # orcbuild：兽族苦工施工中
RESUME_BUILD = 0x000D001F # resumebuild：接着盖停工的地基
BUILDING_ORDERS = frozenset({HUMAN_BUILD, ORC_BUILD, RESUME_BUILD})
# ★★ 09-22 晚 1-413：兵营停在 1491/1500（99.4%）整局没盖完、6.8 分钟零兵认输。原来这里 97% 就把工地删掉、放掉工人，
#   续建器（runtime.job_construction）>= 97% 也当「见过满血」，维修器（repair.py）要 < 98.5% 才修 —— 97%~100% 谁也不管。
#   交战帧里成品常读成差零点几（499/500），所以「满」= 差不到 SITE_FULL_EPS。
SITE_FULL_EPS = 2.0
SITE_BUSY_STALL_S = 8.0   # 地基这么多游戏秒不涨就不再替它占着工人（和 runtime.SITE_STALL_S 同一个数：到这时续建会重新派人）


def site_full(b) -> bool:
    return b.hp_max > 0 and b.hp >= b.hp_max - SITE_FULL_EPS


def constructing(u) -> bool:
    """正在盖房或正要去盖的工人：施工令 / 续建令，或者订单还是建筑 rawcode（'hbar' = 0x68626172，走在去工地的路上）。
    大脑重启之后工地账是空的，靠这条兜住（1-413：续建器按距离挑中了正在盖兵营的农民）。"""
    o = getattr(u, 'order', 0) or 0
    return o in BUILDING_ORDERS or o >= 0x41000000

def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def identity(u):
    return (u.addr, getattr(u, 'handle_lo', 0), getattr(u, 'handle_hi', 0))


@dataclass
class PowerBuild:
    sites: dict = field(default_factory=dict)
    helper: dict | None = None
    cooldown: dict = field(default_factory=dict)

    @property
    def busy_addrs(self):
        return ({s['worker'] for s in self.sites.values() if not s.get('stalled')}
                | ({self.helper['worker']} if self.helper else set()))

    def observe(self, w, pending):
        # ★ 09-22 晚 1-413：原来只登记大厅和农场（双敲只管这两种），兵营的工人一出地基就不算忙了。
        #   现在凡是工人盖的都登记；双敲助手照旧只给卡人口的农场 / 安全分矿（step 里 blocked / expansion 那段）。
        if pending.get('kind') == 'morph' or 'x' not in pending:
            return
        candidates = [b for b in w.my_buildings if b.type == pending['code']
                      and not site_full(b) and b.addr not in self.sites
                      and math.hypot(b.x-pending['x'], b.y-pending['y']) < 320]
        if not candidates:
            return
        b = min(candidates, key=lambda b: math.hypot(b.x-pending['x'], b.y-pending['y']))
        worker = next((u for u in w.my_workers if u.addr==pending['worker']), None)
        self.sites[b.addr] = dict(identity=identity(b), worker=pending['worker'], since=w.t,
                                 worker_identity=identity(worker) if worker else None,
                                 hp=b.hp, progress=w.t, advanced=False, code=b.type)

    def safe(self, w, a, b=None):
        samples = [a] if b is None else [a, b, SimpleNamespace(x=(a.x+b.x)/2, y=(a.y+b.y)/2)]
        return not any(distance(p, f) < 1100 for p in samples
                       for f in w.enemy_fighters+w.enemy_towers+w.creeps if f.hp > 0)

    def release(self, brain, w, reason, danger=False):
        h = self.helper
        if h is None:
            return
        self.helper = None
        self.cooldown[h['site']] = w.t+20
        u = next((u for u in w.my_workers if identity(u) == h['identity']), None)
        if u and brain.act._claim(u.addr, 3, 'strategy_eco'):
            if danger:
                home = w.main_town()
                if home:
                    brain.act.move([u], home.x, home.y)
            else:
                # 停止修理，下一轮采集调度按矿工缺口/伐木需求接回。
                brain.cmd.immediate_order(u.addr, 0x000D0004)
            if brain.claims is not None:
                brain.claims.give_back(u.addr)
            brain.sent_at[u.addr] = w.t
        brain.counts['协建·结束·'+reason] += 1
        brain.note(w.t, f"协建结束：{h['code']}，{reason}，助手 0x{h['worker']:08X}")

    def step(self, brain, w):
        buildings = {b.addr:b for b in w.my_buildings}
        workers = {u.addr:u for u in w.my_workers}
        for addr, s in list(self.sites.items()):
            b = buildings.get(addr)
            if not b or identity(b) != s['identity'] or site_full(b) or w.t-s['since'] > 240:
                if self.helper and self.helper['site'] == addr:
                    self.release(brain,w,'完工' if b and identity(b)==s['identity'] and site_full(b) else '地基失效')
                del self.sites[addr]
                continue
            if b.hp > s['hp']+1:
                s['progress'] = w.t
                s['advanced'] = True
            s['hp'] = b.hp
            s['stalled'] = w.t - s['progress'] > SITE_BUSY_STALL_S
        enabled = brain.spec['name']=='human' and brain.config['enable_powerbuild'] and brain.config['enable_construction']
        cg, cl = brain.committed()
        gold, wood = w.gold-cg, w.lumber-cl
        if brain.dead_waiting:
            gold -= 425  # 复活预算优先于付费抢建。
        if self.helper:
            h = self.helper
            b, u = buildings.get(h['site']), workers.get(h['worker'])
            valid = u is not None and identity(u)==h['identity']
            danger = valid and (u.hp < .6*u.hp_max or not self.safe(w,u,b))
            floor = (75,40) if h['code']=='htow' else (40,15)
            s = self.sites[h['site']]
            if not enabled or not valid or danger or gold<floor[0] or wood<floor[1] or w.t-s['progress']>12:
                self.release(brain,w,'危险' if danger else '暂停或无进展',danger)
            elif not brain.act._claim(u.addr,6,'strategy_eco'):
                self.release(brain,w,'让出自保或人工指挥')
            elif (u.order or 0) in (A.ORDER_REPAIR,HUMAN_BUILD):
                if not h['confirmed']:
                    brain.counts['协建·修理订单确认'] += 1
                    brain.note(w.t,f"协建订单确认：{h['code']}，助手 0x{u.addr:08X}，距工地 {int(distance(u,b))}")
                h['confirmed'] = True
            elif w.t-h['sent'] > 12:
                self.release(brain,w,'未确认修理订单')
        if not enabled or self.helper:
            brain._reserve(w)
            return
        main = w.main_town()
        if main is None:
            brain._reserve(w)
            return
        mxy = getattr(brain, 'militia_expand_xy', None)
        for addr,s in sorted(self.sites.items(), key=lambda item:item[1]['code']!='hhou'):
            b = buildings[addr]
            # 09-22：清分矿留下的原民兵在这座大厅上协建（runtime._militia_assist），这里不另派远处的助手
            #   （1-287 00:04:30 从 2135 外调了一个，1 秒后「暂停或无进展」就停了）
            if (getattr(brain, 'militia_expand_builders', None) and mxy is not None
                    and math.hypot(b.x-mxy[0], b.y-mxy[1]) <= 1400):
                continue
            blocked = b.type=='hhou' and w.food_free<=0 and w.food_cap<100
            expansion = b.type=='htow' and distance(b,main)>1500
            budget = (150,80) if expansion else (80,30)
            primary = workers.get(s['worker'])
            if (not (blocked or expansion) or w.t<self.cooldown.get(addr,0) or b.hp>=.85*b.hp_max
                    or not s['advanced'] or w.t-s['progress']>8 or not primary or distance(primary,b)>650
                    or identity(primary)!=s['worker_identity']
                    or primary.order not in (A.rawcode(b.type),HUMAN_BUILD,A.ORDER_REPAIR,A.ORDER_SMART) or primary.hp<.6*primary.hp_max
                    or gold<budget[0] or wood<budget[1] or not self.safe(w,b)):
                continue
            # 保留矿工；只调明确空闲或有伐木记录的农民，不碰其他工地、交战或手动任务。
            cands = [u for u in brain._builder(w, expansion=expansion) if u.type=='hpea' and u.hp>=.8*u.hp_max
                     and u.addr not in brain.gold_crew and getattr(u,'order_known',False)
                     and ((not u.order and u.has_task is False) or
                          (u.addr in brain.lumber_crew and u.order in A.HARVEST_ORDERS
                           and len(brain.lumber_crew)>(3 if expansion else 2)))
                     and distance(u,b)<1800 and self.safe(w,u,b)]
            for u in sorted(cands,key=lambda u:(u.addr not in brain.militia_expand_builders,bool(u.order),distance(u,b))):
                if not brain.act.gather(u,b,order=A.ORDER_REPAIR):
                    continue
                self.helper = dict(worker=u.addr, identity=identity(u), site=addr, code=b.type, sent=w.t, confirmed=False)
                brain.gold_crew.pop(u.addr,None)
                brain.lumber_crew.pop(u.addr,None)
                brain.sent_at[u.addr]=w.t
                if expansion and u.addr in brain.militia_expand_builders:
                    brain.militia_expand_builders.discard(u.addr)
                    brain.counts['民兵清分矿·转协建工'] += 1
                brain.counts['协建·派出·'+b.type] += 1
                brain.note(w.t,f"协建派出：{b.type}，{'卡人口' if blocked else '安全分矿'}，助手 0x{u.addr:08X}，原工人 0x{s['worker']:08X}")
                break
            if self.helper:
                break
        brain._reserve(w)

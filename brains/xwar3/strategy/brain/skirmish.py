"""野外接战的唯一整队进退状态机；游戏秒计时，保护单兵自保的指挥权。"""
from dataclasses import dataclass, field
import math
from types import SimpleNamespace
from . import battlepower as P

# ★ 09-22 复盘最近 10 局里输的 3 局：输局都是第一场野外仗人数差不多、打一半整队撤，撤的路上被追着砍。
#   对局文件里每次「局部交战 A->B：…比值 R」对上战斗账本（1 号实例 09-20~22，292 局 699 场打玩家的仗，一次性分析脚本 withdraw_rd.py），
#   按状态机第一次评估的战力比看门槛两侧（原门槛 0.62）：
#       0.50~0.62  规则一照面就撤  21 场  阵亡 我 8.4 敌 2.2（净 -6.2）  参战阵亡率 59%
#       0.62~0.80  规则先打        43 场  阵亡 我 8.8 敌 5.4（净 -3.4）  参战阵亡率 62%
#   撤了自己也没少死（参战阵亡率差不多），杀敌只剩一半 —— 已经照面的仗，撤退救不下人（< 0.45 一照面就撤的，参战阵亡率 75%~91%）。
#   门槛降到 0.45：0.45~0.62 这一段打；0.45 以下样本少（17 场）、对面强太多，照旧撤。反打（counter）那几条线不动。
# 09-23 对照 AMAI Jobs/RETREAT_CONTROL.eai：主力 1500 内敌方强度 > 我方 × 2~3（进攻性插值，线性强度）才整队回家。
#   这里的战力 BP.estimate = √(血 × 输出)，对人数是线性的（4 对 8 = 0.5，test_skirmish.Power），0.45 = 对面强 2.2 倍 ——
#   本来就在 AMAI 的区间里，不改。（毫秒层 battlecommit 用的是「打光对面几秒 / 被打光几秒」，那个是平方口径，那边按 AMAI 改了）
WITHDRAW_RATIO = .45
TURN_RATIO = 1.25
RESERVE_RATIO = 1.10


def point(xy):
    return SimpleNamespace(x=xy[0], y=xy[1])


def centre(units):
    return (sum(u.x for u in units)/len(units), sum(u.y for u in units)/len(units))


@dataclass
class Skirmish:
    phase: str = 'idle'
    origin: tuple | None = None
    waypoint: tuple | None = None
    turn_origin: tuple | None = None
    since: float = 0
    good_since: float | None = None
    turn_since: float = 0
    last_seen: float = 0
    last_time: float = -1
    positions: dict = field(default_factory=dict)
    counter_ids: set = field(default_factory=set)
    report: dict = field(default_factory=dict)

    def reset(self):
        self.phase = 'idle'
        self.good_since = None
        self.waypoint = None
        self.counter_ids.clear()

    def escape(self, crew, foes, home, hazards):
        cx,cy = centre(crew)
        fx,fy = centre(foes) if foes else (cx+1,cy)
        dx,dy = cx-fx,cy-fy
        length = math.hypot(dx,dy) or 1
        candidates = []
        for angle in (0, .7, -.7, 1.3, -1.3):
            vx,vy = dx*math.cos(angle)-dy*math.sin(angle), dx*math.sin(angle)+dy*math.cos(angle)
            p = point((cx+850*vx/length,cy+850*vy/length))
            # 同时检查途中和终点，避免退入另一营地/敌塔；地形仍由游戏寻路。
            samples = [point((cx+(p.x-cx)*k,cy+(p.y-cy)*k)) for k in (.33,.66,1)]
            risk = sum(max(0,900-min(P.distance(q,h) for q in samples)) for h in hazards)
            separation = min((P.distance(p,f) for f in foes),default=2000)
            score = separation - .2*P.distance(p,home) - 3*risk
            candidates.append((score,(p.x,p.y)))
        return max(candidates)[1]

    def step(self, world, army, home):
        if not army or home is None:
            self.reset()
            return None
        if self.last_time >= 0 and world.t-self.last_time > 12:
            withdrawing = self.phase in ('withdraw','counter')
            self.reset()
            self.last_time = world.t
            if withdrawing:
                return dict(mode='disengaged',crew=army,foes=[],point=(home.x,home.y))
        if world.t < self.last_time:
            self.reset()
        self.last_time = world.t
        # 以最密集队伍为锚，不让侦察兵或远方孤兵把整支军队拖走。
        anchor = max(army,key=lambda u:sum(P.distance(u,a)<1100 for a in army))
        crew = [u for u in army if P.distance(u,anchor)<1600]
        here = point(centre(crew))
        # 基地内的守城职责继续由 Defense 决定，不能风筝到弃城。
        if any(P.distance(here,h)<1400 for h in world.my_halls):
            withdrawing = self.phase in ('withdraw','counter')
            self.reset()
            if withdrawing:
                return dict(mode='disengaged',crew=army,foes=[],point=(home.x,home.y))
            return None
        local = [f for f in world.enemy_fighters if f.hp>0 and P.distance(f,here)<2400]
        contact = [f for f in local if any(P.distance(f,u)<1000 for u in crew)]
        towers = [f for f in world.enemy_towers if P.distance(f,here)<2400]
        if self.phase == 'idle' and (len(contact)<2 or len(crew)<2):
            return None
        if not local:
            if self.phase in ('withdraw','counter'):
                self.reset()
                return dict(mode='disengaged', crew=army, foes=[], point=(home.x,home.y))
            self.reset()
            return None
        near = [f for f in local if any(P.distance(f,u)<1400 for u in crew)]
        now = P.estimate(crew, local+towers, world.levels)
        reserve = P.estimate(crew, local+towers, world.levels,horizon=10)
        self.report = dict(ratio=round(now['ratio'],3), reserveRatio=round(reserve['ratio'],3),
                           ownPower=round(now['ours']['power'],1), enemyPower=round(now['enemy']['power'],1),
                           ownDps=round(now['ours']['dps'],1), enemyDps=round(now['enemy']['dps'],1),
                           ownCount=len(crew), enemyCount=len(near), reserveCount=len(local), t=world.t)
        if not now['known'] and self.phase in ('idle','fight'):
            return None  # 数据缺失交回既有保护，不把未知单位算成零战力后自信反打。
        danger = now['known'] and now['ratio']<WITHDRAW_RATIO and now['enemy']['dps']>0
        danger = danger or (self.phase == 'counter' and reserve['ratio']<.95)
        if self.phase == 'idle':
            self.phase = 'fight'
        if danger and self.phase != 'withdraw':
            self.phase,self.origin,self.since = 'withdraw',(here.x,here.y),world.t
            self.good_since,self.waypoint = None,None
            self.positions = {u.addr:(u.x,u.y) for u in crew}
        if self.phase == 'withdraw':
            moved = P.distance(here,point(self.origin))
            healthy = [u for u in crew if u.hp >= .45*max(1,u.hp_max)]
            # 原地掉血/兵死造成重心漂移不等于撤退成功：至少多数存活兵越过原位置 500。
            travelled = sum(u.addr in self.positions and P.distance(u,point(self.positions[u.addr]))>=500 for u in crew)>=max(1,math.ceil(.7*len(crew)))
            tower_bait = any(P.distance(f,tower) < float(P.G.stat(tower.type,'range',700))+250
                             for f in contact for tower in towers)
            good = (contact and moved>=500 and travelled and world.t-self.since>=4
                    and len(healthy)>=.7*len(crew) and now['known'] and reserve['known']
                    and now['ratio']>=TURN_RATIO and reserve['ratio']>=RESERVE_RATIO and not tower_bait)
            if good:
                if self.good_since is None:self.good_since=world.t
            else:self.good_since=None
            if self.good_since is not None and world.t-self.good_since>=2:
                self.phase,self.turn_origin,self.turn_since='counter',(here.x,here.y),world.t
                self.counter_ids={f.addr for f in contact}
            else:
                if world.t-self.since>35:
                    self.reset()
                    return dict(mode='disengaged',crew=army,foes=[],point=(home.x,home.y))
                if self.waypoint is None or P.distance(here,point(self.waypoint))<220 or any(P.distance(f,point(self.waypoint))<350 for f in local):
                    self.waypoint=self.escape(crew,near or local,home,world.creeps+towers)
                return dict(mode='withdraw',crew=army,foes=near,point=self.waypoint)
        if self.phase == 'counter':
            targets=[f for f in contact if f.addr in self.counter_ids and P.distance(f,point(self.turn_origin))<=900]
            engaged = sum(any(P.distance(u,f)<=float(P.G.stat(u.type,'range',100))+150 for f in targets)
                          for u in crew)>=max(1,math.ceil(len(crew)*.5))
            # 时间上限约束追赶，不打断已经站定、优势仍在的输出。
            if not targets or P.distance(here,point(self.turn_origin))>700 or (world.t-self.turn_since>10 and not engaged):
                self.phase,self.origin,self.since='withdraw',(here.x,here.y),world.t
                self.good_since,self.waypoint=None,None
                self.positions={u.addr:(u.x,u.y) for u in crew}
                self.waypoint=self.escape(crew,near or local,home,world.creeps+towers)
                return dict(mode='withdraw',crew=army,foes=near,point=self.waypoint)
            return dict(mode='counter',crew=crew,foes=targets,point=centre(targets))
        if contact:
            return dict(mode='fight',crew=crew,foes=contact,point=centre(contact))
        self.reset()
        return None

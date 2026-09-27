"""接战后稳定执行：近战不穿阵追后排，远程有限集火，增援走向同一战场边缘。

AMAI Captain / SleepInCombat 的任务持续性在外部控制器中的实现；选目标沿用本项目的兵种优先级。
不估算胜率，不负责英雄自保或全军撤退。
"""
from dataclasses import dataclass, field
import math
from . import gamedata as G
from . import battlepower as BP
POLICY = 'focus-finish-no-chase-v8'

HEALERS = frozenset({'hmpr','odoc','obs1','obs2'})
CASTERS = frozenset({'oshm','hsor','edry','unec','otbk'})
CORE_FINISH_FRAC = .55       # 核心低于这条血线，立即打断普通目标锁定并转火
FINISH_FRAC = .35            # 任意单位到这条血线，能在当前射程内收掉就优先减员
TARGET_LOCK_S = 4.0          # 小幅血量波动不换目标；高价值目标出现可提前打断
SWITCH_GAIN = 1.35          # 非紧急目标至少好 35% 才打断当前集火
HIGH_DPS = 16.0             # 静态普攻 DPS 到这条线才算高伤害单位，避免“场上唯一目标”被误标
# ★ 09-22 交战中各单位互相拥挤、迟迟走不到位。1 号实例 6 局按每个近战**真实的任务目标**量：
#   够不着目标还走不近的时间里，63% 是**目标在跑开**（沿「我 -> 它」方向跑得 >= 我移速的 40%），被挤慢的 35%，要绕路的 3%；
#   追的多是残血（一半 < 40% 血）往它们自己阵里退的牛头人酋长 / 先知 / 兽人步兵 —— 电脑把残血往回拉，我们点名"集火残血"就一路追进去，
#   48 次追逐只有 14 次在 5 秒内追上，而 67% 的追逐是本模块点名的那一个。
#   现在：够不着、正在跑开、按两边移速 CHASE_MAX_S 秒内追不上的目标，不再作为候选（身边只剩这种就交给攻击移动）。
# ★ 09-22 交战里命令来回改（1 号实例 6 局 decisions.jsonl，交战中的单位·决策 5204 条、3 秒内相邻而且换了的 3343 对）：
#   最大的一块是本模块自己的「支援交战 -> 支援交战」1010 对 —— 射程边上来回切「攻击 / 攻击移动」约 400、
#   正在打的被改去打别的约 400、增援的攻击移动点跟着目标跳 137；两条之间多是 1~2 秒（大脑一两拍）。每条新令引擎都要重新走位。
STICKY_R = 250.0            # 刚点名的目标走出「够得着」的圈这么远以内，仍留在候选里（不在射程边上来回切攻击 / 攻击移动）
CONTACT_PAD = 64.0          # 离目标 <= 射程 + 这个 = 正在打它：只有残血核心能把它换走去打一个要走过去的目标
MARCH_KEEP_R = 600.0        # 增援的攻击移动点：新算出来的点离上一个不到这么远就沿用上一个（battle_intents）
OWN_HEALERS = frozenset({"hmpr"})   # 我方牧师：交战里不点名攻击，加血归毫秒层（mod_combat.PRIESTS 同一张表，改一处改两处）
CHASE_MAX_S = 2.5           # 游戏秒：追上它（进射程）要超过这么久就不追
FOCUS_CAP_NORMAL = 6        # 09-23 同一个普通目标最多几个人打（原来 3；AMAI 集火不设上限，见 plan 里 focus_cap）
FOCUS_CAP_CORE = 8          #   英雄 / 治疗 / 施法 / 高伤害（原来 6）
FOCUS_CAP_FINISH_CORE = 10  #   残血核心（原来 8）
FOCUS_CAP_MELEE = 4         #   近战挤得上去的就这么几个：目标身上已有这么多人时近战改打别的（远程不受这条限制）
FLEE_MIN_FRAC = .4          # 沿我->它方向跑开的速度 >= 我移速的这个比例才算"在跑开"（站着/横走/抖动的不算）
VEL_MIN_DT = .2             # 两次观测至少隔这么久才更新速度（同一游戏时刻的快照不算）
VEL_MAX_DT = 3.0            # 隔太久的旧位置不拿来算速度（中间可能拐过弯、出过视野）


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def identity(u):
    return (u.addr, u.type, getattr(u, 'handle_lo', 0), getattr(u, 'handle_hi', 0))


def core_target(u):
    """战斗核心：英雄、治疗和施法单位。"""
    return G.is_hero(u.type) or u.type in HEALERS or u.type in CASTERS


def hp_fraction(u):
    return u.hp/max(1, u.hp_max)


def finish_core(u):
    return core_target(u) and hp_fraction(u) <= CORE_FINISH_FRAC


def output_dps(attrs):
    return max((mode[0] for mode in attrs.get('modes', ())), default=0.0)


def effective_hp(u, attrs):
    armor = float(attrs.get('armor', G.stat(u.type, 'armor', 0)) or 0)
    factor = 1+.06*armor if armor >= 0 else 1/(2-.94**(-armor))
    return max(1.0, u.hp*factor)


def runs_away(u, f, rng, speed, vel):
    """f 够不着、正在沿 u->f 方向跑开、而且按两边移速 CHASE_MAX_S 内追不上 -> True。vel = f 的 (vx, vy)，没有就 False。"""
    if vel is None:
        return False
    d = distance(u, f)
    gap = d - rng
    if d <= 1 or gap <= 64:
        return False
    away = (vel[0]*(f.x-u.x) + vel[1]*(f.y-u.y)) / d
    speed = max(1.0, float(speed or 0))
    if away < FLEE_MIN_FRAC*speed:
        return False
    closing = speed - away
    return closing <= 0 or gap/closing > CHASE_MAX_S


@dataclass
class Engagement:
    assignments: dict = field(default_factory=dict)
    target_reasons: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)      # identity(敌) -> (游戏秒, x, y, vx, vy)：估敌人在往哪跑
    counts: dict = field(default_factory=dict)
    march_xy: tuple | None = None                 # 上一次给增援的攻击移动点（battle_intents，MARCH_KEEP_R）

    def _count(self, k):
        self.counts[k] = self.counts.get(k, 0)+1

    def _track(self, foes, now):
        """记每个敌人上一次的位置，算速度（游戏单位/游戏秒）。隔不到 VEL_MIN_DT 的保留旧记录（速度照旧）。"""
        seen = {}
        for f in foes:
            key = identity(f)
            prev = self.seen.get(key)
            if prev is not None and now-prev[0] < VEL_MIN_DT:
                seen[key] = prev
            elif prev is not None and now-prev[0] <= VEL_MAX_DT:
                dt = now-prev[0]
                seen[key] = (now, f.x, f.y, (f.x-prev[1])/dt, (f.y-prev[2])/dt)
            else:
                seen[key] = (now, f.x, f.y, None, None)
        self.seen = seen
        return {f.addr: (seen[identity(f)][3], seen[identity(f)][4]) for f in foes
                if seen[identity(f)][3] is not None}

    def plan(self, army, foes, now, levels=None):
        """返回 [(我方单位, 近处目标)] 和尚在赶路的人。保护名单由 runtime 先剔除。"""
        levels = levels or {}
        foes = [f for f in foes if f.hp > 0]
        self.assignments = {identity(u): self.assignments[identity(u)] for u in army if identity(u) in self.assignments}
        self.target_reasons = {}
        vel = self._track(foes, now)
        if not foes:
            self.assignments.clear()
            return [], list(army)
        attrs = {u.addr: BP.attributes(u, levels) for u in army+foes}
        threat = {f.addr: output_dps(attrs[f.addr]) for f in foes}
        max_threat = max(threat.values(), default=0.0)
        high_cut = max(HIGH_DPS, max_threat*.75)
        picked, marching, loads = [], [], {}
        # 先给已经贴脸的近战分配落点，再让远程加入，避免近战全部挤向同一个后排。
        for u in sorted(army, key=lambda u: (max((m[3] for m in attrs[u.addr]['modes']), default=float(G.stat(u.type,'range',100))) > 150,
                                              min(distance(u,f) for f in foes), u.addr)):
            ua = attrs[u.addr]
            rng = max((m[3] for m in ua['modes']), default=float(G.stat(u.type,'range',100) or 100))
            ranged = rng > 150
            # 只给能攻击该目标类型的单位下点名令；步兵不会追空军，火枪/飞行器可以正确集火空中高威胁。
            targetable = [f for f in foes if any(('air' if attrs[f.addr]['air'] else 'ground') in mode[1]
                                                  for mode in ua['modes'])]
            nearest = min((distance(u,f) for f in targetable), default=1e9)
            reach = rng + 300 if ranged else min(650, nearest+180)
            candidates = [f for f in targetable if distance(u,f) <= reach]
            fleeing = {f.addr for f in candidates if runs_away(u, f, rng, ua.get('speed'), vel.get(f.addr))}
            if fleeing:
                candidates = [f for f in candidates if f.addr not in fleeing]
                self._count('接战·不追跑开的目标' if candidates else '接战·身边只剩跑开的·改攻击移动')
            old = self.assignments.get(identity(u))
            prev_t = next((f for f in targetable if old and identity(f) == old[0]), None)
            if (prev_t is not None and all(f is not prev_t for f in candidates)
                    and distance(u, prev_t) <= reach + STICKY_R
                    and not runs_away(u, prev_t, rng, ua.get('speed'), vel.get(prev_t.addr))):
                candidates.append(prev_t)
                self._count('接战·点名的目标刚出圈·不改攻击移动')
            # 身边已经有一坨敌军时，不为追一个逃跑目标额外向前走 300。只比较当前射程边缘
            # 内的目标，让单位边输出边站住阵型，避免一路挨打却追不上残血。
            contact = [f for f in targetable if distance(u,f) <= rng+100]
            if ranged and len(contact) >= 2:
                candidates = [f for f in contact if f.addr not in fleeing]
            if not candidates:
                marching.append(u)
                self.assignments.pop(identity(u), None)
                continue
            def score(f):
                effective = effective_hp(f, attrs[f.addr])
                role = (2.5 if f.type in HEALERS else 2.25 if G.is_hero(f.type)
                        else 2.0 if f.type in CASTERS else 1.0)
                if G.food(f.type)==0 and not G.is_hero(f.type):
                    role *= 0.2
                damage = 1+1.5*threat[f.addr]/max(1.0, max_threat)
                wounded = 1+1.5*(1-hp_fraction(f))
                focus = 1+0.3*loads.get(f.addr,0)
                return role*damage*wounded*focus/effective / (1+max(0,distance(u,f)-rng)/400)
            def focus_cap(f):
                # ★ 09-23 AMAI Jobs/FOCUSFIRE_CONTROL.eai：每 2 秒给一支部队定一个目标（能秒的英雄 > 被定住的 > 全场血最少的），
                #   射程内所有人都去打它，没有人数上限。原来普通目标最多 3 人（满血步兵 470 有效血要打很久，「谁都打一口、一个不杀」）；
                #   交战采样：对面平均 4.6 人打一个目标，我们 3.1~3.4。上限放宽到 AMAI 的量级，按有效血留一点防过量伤害。
                ehp = effective_hp(f, attrs[f.addr])
                if finish_core(f):
                    return min(FOCUS_CAP_FINISH_CORE, max(4, math.ceil(ehp/40)))
                if core_target(f) or threat[f.addr] >= high_cut:
                    return min(FOCUS_CAP_CORE, max(3, math.ceil(ehp/55)))
                return min(FOCUS_CAP_NORMAL, max(2, math.ceil(ehp/60)))
            # 近战受站位限制：一个目标身边挤得下的就这么几个，多出来的只会绕着走（test_battle_cohesion「近战前排分散」）
            free = [f for f in candidates if loads.get(f.addr,0) < (focus_cap(f) if ranged else min(focus_cap(f), FOCUS_CAP_MELEE))]
            pool = free or candidates
            low_core = [f for f in pool if finish_core(f) and (ranged or distance(u,f) <= rng+180)]
            finishable = [f for f in pool if hp_fraction(f) <= FINISH_FRAC
                          and (distance(u,f) <= rng+120 or len(contact) < 2)]
            if low_core:
                # 残血核心是最高优先级，即使已有普通目标锁定也立刻转火，避免“谁都打一口、一个不杀”。
                choice = low_core
            elif finishable:
                # 战场上的残血普通兵同样代表马上能消掉的一份输出；但只收射程内/无贴脸压力的，
                # 不穿过敌方前排长追。
                choice = finishable
            elif ranged:
                # 远程在射程允许时比较核心和高伤害单位；血量、护甲、威胁与距离再共同决定谁先死。
                preferred = [f for f in pool if core_target(f) or threat[f.addr] >= high_cut]
                choice = preferred or pool
            else:
                # 近战仍先接敌方前排；只有核心已经贴在刀口边，才直接切它，避免穿阵追后排。
                preferred = [f for f in pool if core_target(f) and distance(u,f) <= rng+180]
                choice = preferred or pool
            best = max(choice,key=score)
            previous = next((f for f in pool if old and identity(f)==old[0]),None)
            if previous is not None and previous is not best:
                urgent_switch = finish_core(best) and not finish_core(previous)
                hitting = distance(u, previous) <= rng + CONTACT_PAD
                if hitting and distance(u, best) > rng + CONTACT_PAD and not urgent_switch:
                    # 正在打的不丢下去追一个要走过去的（换目标要重新走位，近战最吃亏）；残血核心照样立刻转火
                    best = previous
                    self._count('接战·正在打的不丢下去追别的')
                elif any(f is previous for f in choice) and now-old[1] < TARGET_LOCK_S:
                    clearly_better = score(best) >= score(previous)*SWITCH_GAIN
                    if not urgent_switch and not clearly_better:
                        best = previous
            if old is None or old[0] != identity(best):
                self.assignments[identity(u)] = (identity(best), now)
            loads[best.addr] = loads.get(best.addr,0)+1
            self.target_reasons[best.addr] = ('集火残血核心' if finish_core(best) else
                                              '集火射程内残血单位' if hp_fraction(best) <= FINISH_FRAC else
                                              '集火核心' if core_target(best) else
                                              '集火高伤害单位' if threat[best.addr] >= high_cut else '就近接战')
            picked.append((u,best))
        return picked, marching

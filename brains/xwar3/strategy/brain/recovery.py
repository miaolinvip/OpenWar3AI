"""人族伤兵整队补给：安全时买恢复卷轴、集合使用、验证消耗及回血再出发。"""
from collections import Counter
from dataclasses import dataclass, field
import math
from types import SimpleNamespace
import itemuse as I
from . import gamedata as G
from .tactics import Intent

RECOVERY_READY_R = 500.0       # 比卷轴 600 范围留 100 距离余量；所有伤兵到齐才允许使用
RECOVERY_GATHER_MAX_S = 35.0   # 一名掉队伤兵不能把全军和英雄拖在商店四分钟
RECOVERY_SAFE_R = 1200.0       # 伤兵群（不是全军）离敌人/野怪这么近就先别补给
RECOVERY_SCAN_MAX = 12         # 找「谁手上已经有卷轴」最多读这么多个背包（一次 inventory = 7 次通道请求，别刷爆）
# ★ 09-22 对局画面（1-312 10:55~13:10）：部队在家长时间没有任何动作，排查卡在哪个流程。
#   这 2 分 15 秒大脑只有一条决策：10 名伤兵，血魔法师去藏宝室买恢复卷轴 -> 4 秒后「购买被拒」-> 30 游戏秒后再来一轮，循环 5 次；
#   整队恢复在进行时出击层不跑，而「养伤·不派」两分钟涨了 632（兵被拉下来要回到五成血才重新派，步兵自然回血一秒 0.25）。
#   buy_item 失败只回 0，看不出原因。实测：对藏宝室做训练用的同一个检查（引擎的可行性检查）读到：
#   0 能买、8 缺金（和金子对得上）、88 刚买空两张之后（多半是缺货）、17 / 64 英雄不在店边时（多半是店边没有能接货的买家）。
#   现在被拒时当场读这个码记下来（计数「买卷轴被拒·裁决N」），并按补货周期（sreg stockRegen 90）退避，不再每 30 秒按住全军一轮。
BUY_REFUSED_S = 90.0
# 09-23 实机（运行时协议验收，地精商店）：221 = 这家不卖、89 = 还没到货（ItemData stockStart；现造的商店从造出来那刻算）
BUY_VERDICT_CN = {0: "能买", 8: "缺金", 88: "多半是缺货", 89: "还没到货", 221: "这家不卖",
                  17: "多半是店边没有能接货的买家", 64: "多半是店边没有能接货的买家"}


def buy_verdict(cmd, shop_addr: int = 0, code: str = "sreg"):
    """刚才那次买被拒的原因码：W3P buy 被拒时运行时当场做同一个可行性检查（买家 = 本方玩家），码在回执里。
    读不到（没有回执 / 回执不是这次的）返回 None。shop_addr / code 保留签名。"""
    try:
        r = cmd.last_receipt
        return None if r is None else r.verdict
    except Exception:  # noqa: BLE001
        return None
#   ★ 09-21：原来是「全军任何一个人离任何一个敌人或野怪 1200 内」，在野怪成片的图上几乎恒真，
#   整条恢复流程最近 60 局一条动作都没跑过，而毫秒层同期报了 5878 次「sreg·背包里没有」。
# ★ 09-21 1-284：空闲时每一拍都把十几个人的背包重读一遍（整队恢复 + 家里补给两份），心跳「最慢一轮 2.61 秒：
#   tactics·整队恢复 1.67（ArmyRecovery.step>inventory×123）」—— 大脑下的每一条战斗令因此晚 1.2~2.2 秒。
#   空闲扫描走缓存；买之后 / 用之前那几次核对照旧当场读（它们要的就是「现在」）。
INV_BAG_TTL = 3.0              # 有背包的人：这么多游戏秒内复用上次读的
INV_NONE_TTL = 30.0            # 没背包（普通兵、没研究 Backpack）：这么久不再问
HOLDER_MISSING_S = 10.0        # 买家暂时不在这份视野里（被毫秒层拉走 / 走出家里那一圈）先等这么久，别当场作废
SHOP_MAX = 5000.0              # 英雄离藏宝室这么远以内才专门去买（部队在做事时：来回跑太久）
SHOP_MAX_IDLE = 9000.0         # ★ 09-22 1-291 14 分：部队在家空等牧师回血、浪费时间，恢复卷轴却没用上。
#                                部队在 2 矿闲着、藏宝室在主基地（5600）：超过 5000 英雄不去买，伤兵只能等牧师。
#                                部队闲着（出击状态 home）时放宽：英雄跑 5600 约 17 游戏秒，换回一群伤兵的血。
# ★ 09-22 卷轴只能由英雄拿、英雄用：兵研究 Backpack 后拿到的是 Aihn（AbilityData.slk DataA 格数 2、**DataC 能使用物品 = 0**），
#   能拿不能用。全部实例历史：兵拿着卷轴用 165 次全被引擎拒（hrif 78、hfoo 67、hkni 16…），英雄 80 次全成。
#   原来的注释「已研究 Backpack 的地面部队…可以成为卷轴买家和使用者」是错的 —— 家里的伤兵始终不回血，大半出在这。


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def ident(u):
    return u.addr, getattr(u,'handle_lo',0), getattr(u,'handle_hi',0)


@dataclass
class ArmyRecovery:
    phase: str = 'idle'
    holder_id: tuple | None = None
    shop_id: tuple | None = None
    started: float = 0
    next_try: float = 0
    next_read: float = 0
    deadline: float = 0
    used_at: float = 0
    hp_before: dict = field(default_factory=dict)
    recovered: bool = False
    blocked_since: float = -1
    gather_started: float = -1
    notes: list = field(default_factory=list)
    # 每条路都要能回答「走没走到」—— 这个模块原来一个计数都没有，于是「最近 60 局零动作」
    # 这件事直到 09-21 对局里看到一堆残血兵始终没人补血才被发现（经验：计数器先于结论）
    counts: Counter = field(default_factory=Counter)
    inv_cache: dict = field(default_factory=dict)   # 地址 -> (读的游戏秒, 背包或 None)；runtime 让两份恢复共用一张
    missing_since: float = -1
    terrain: object = None      # brain.terrain.Terrain（runtime 给）；None = 退回伤兵重心
    reserve: int = 0            # 要买卷轴、还没买到：生产先别花这笔钱（runtime.committed() 加上它）
    meet: tuple | None = None   # 这一趟定下的集合空地；中途不漂移

    def cancel(self, now, reason):
        if self.phase != 'idle':
            self.notes.append('整队恢复结束：'+reason)
        if self.holder_id is not None:
            self.inv_cache.pop(self.holder_id[0], None)    # 它的背包刚变过（买 / 用 / 被微操层用掉），下次重读
        self.phase = 'idle'
        self.holder_id = None
        self.shop_id = None
        self.blocked_since = -1
        self.gather_started = -1
        self.missing_since = -1
        self.meet = None
        self.reserve = 0
        self.next_try = now + 30

    def meet_at(self, w, holder, injured, hostiles):
        """这一趟在哪儿集合用卷轴 —— 伤兵重心附近的一块**空地**，定下来就不再漂。
        ★ 09-22 恢复卷轴不在家里用：家里建筑密集没空间，改到外面空地集结，
          英雄也站到空地上，需要恢复的单位都聚到拿卷轴的英雄身边再放。
          原来集合点就是伤兵重心（meeting_point），伤兵都在家里时就落在建筑堆里，一群人挤不开、英雄也进不去。
          现在从重心出发找 terrain.open_ground（离建筑 420、离金矿 700、离敌人野怪 1200 以外、地形最空旷的点）；
          地形表读不到 / 一个合格的点都没有时退回重心。定下之后附近来了敌人才重选。"""
        if self.meet is not None and not any(distance(SimpleNamespace(x=self.meet[0], y=self.meet[1]), f) < RECOVERY_SAFE_R
                                             for f in hostiles):
            return self.meet
        cx, cy = self.meeting_point(holder, injured)
        p = None
        if self.terrain is not None:
            p = self.terrain.open_ground(cx, cy, buildings=list(w.my_buildings) + list(getattr(w, 'enemy_buildings', [])),
                                         mines=getattr(w, 'mines', []), avoid=hostiles)
        self.counts['整队恢复·集合点·' + ('空地' if p else '伤兵重心')] += 1
        self.meet = (p[0], p[1]) if p else (cx, cy)
        return self.meet

    def bag(self, cmd, u, now):
        """空闲扫描用的背包读取（见 INV_BAG_TTL）。"""
        hit = self.inv_cache.get(u.addr)
        if hit is not None and now - hit[0] < (INV_NONE_TTL if hit[1] is None else INV_BAG_TTL):
            self.counts['整队恢复·背包读缓存'] += 1
            return None if hit[1] is None else list(hit[1])
        b = cmd.inventory(u.addr)
        self.counts['整队恢复·背包读通道'] += 1
        self.inv_cache[u.addr] = (now, None if b is None else list(b))
        return b

    @staticmethod
    def meeting_point(holder, injured):
        """在哪儿碰头放卷轴。

        ★ 09-21 回血不必全军回家：由持有恢复卷轴的单位去找残血单位、就地集结使用，
          省掉来回路程和时间。
        原来碰头点写死在**商店门口**（`s.x+200, s.y+200`），于是买完卷轴要把十几个伤兵
        从地图另一头拖回基地商店 —— 路上又挨打、又超时（RECOVERY_GATHER_MAX_S 35 秒）。
        现在碰头点取**伤兵的重心**：持有者一个人走过去，伤兵基本不用动。
        伤兵只有一个人时就用它自己的位置（重心 = 它），效果一样。"""
        if not injured:
            return holder.x, holder.y
        return (sum(u.x for u in injured) / len(injured),
                sum(u.y for u in injured) / len(injured))

    @staticmethod
    def formation(army, holder, injured, cx, cy):
        """伤兵和物品持有者进中心 -> (队形命令, [])。
        ★ 09-22 1-301 9:55：单位已集结却迟迟不放恢复卷轴，等部队散开才放，几个小兵不在作用范围内。
          原来还要求「满血单位退出作用圈」才放：满血的兵只要还站在圈里就一直等，01:28:05 卷轴进包、伤兵全到了，
          01:28:17 集合超时（「仍有 0 名未到」）放弃，部队散开，毫秒层随后自己把卷轴用在了散开的人身上。
          恢复卷轴（AIsl）是 600 内**所有**非机械单位各回 225，没有人数上限 —— 满血的站在里面不挡任何人，还能顺便补满。
          第二个返回值留着兼容调用方，恒为空。"""
        center = [holder] + [u for u in injured if u.addr != holder.addr]
        return [Intent('recover', center, cx, cy, why='整队恢复·伤兵靠拢物品持有者')], []

    def step(self, w, cmd, claims, blocked=False, carrier_min=None, shop_max=SHOP_MAX):
        """carrier_min：买家血量下限（英雄, 小兵）；None = (0.45, 0.40)。家里补给（runtime.home_recovery）传很低的线 ——
        在家是安全的，去藏宝室买卷轴不用血。shop_max：英雄离藏宝室这么远以内才去买（部队闲着时 runtime 传 SHOP_MAX_IDLE）。"""
        army = w.my_army + w.my_heroes
        hostiles = w.enemy_fighters + w.creeps
        if self.phase == 'idle':
            self.reserve = 0                        # 每拍重判：不需要买（被挡、伤兵回血了、手上有卷轴）就不占生产的钱
        injured = [u for u in w.my_army if u.hp > 0 and u.type not in I.MECHANICAL
                   and G.food(u.type) > 0 and u.hp < .7*u.hp_max and u.hp_max-u.hp >= 100]
        # ★ 09-21 对局里一堆残血兵，英雄始终没用恢复道具补血。
        #   查下来整条流程**最近 60 局一条动作都没有**，而毫秒层同期报了 5878 次「sreg·背包里没有」——
        #   不是判据不肯用，是根本没人去买。
        #   原来的 unsafe 是「**全军**任何一个人离**任何一个**敌人或野怪 1200 内」：
        #   Echo Isles 上野怪成片，这个条件几乎恒真（闸门静默恒真，看起来却完全正常）。
        #   改成只看**这次要恢复的那群人**周围 —— 远处有野怪不妨碍这边喝药。
        crew = list(injured)
        if self.holder_id is not None:
            crew += [u for u in army if ident(u) == self.holder_id]
        watch = crew or army
        unsafe = any(distance(u, f) < RECOVERY_SAFE_R for u in watch for f in hostiles)
        if blocked or unsafe:
            # blocked 现在是「谁挡的」那句话（runtime 传下来），不再是一个 bool —— 28 局实测
            # 「上层挡着」259.4 次/局是所有闸门第一，而它回答不了是四个条件里的哪一个。
            self.counts[f'整队恢复·没开始·上层挡着·{blocked}' if blocked else '整队恢复·没开始·周围有敌人'] += 1
            if self.phase != 'idle':
                # 005~010 里整队恢复常被一次短暂接触直接清零；接触结束后又要重新等冷却。
                # 暂停时不保持租约、不和防守/战斗抢人，但保留已选的英雄和卷轴任务。
                if self.blocked_since < 0:
                    self.blocked_since = w.t
                    self.notes.append('整队恢复暂停：接敌或防守优先')
                elif w.t-self.blocked_since >= 45:
                    self.cancel(w.t, '持续接敌，放弃本次补给')
            return None
        if self.blocked_since >= 0:
            self.started += w.t-self.blocked_since       # 暂停时间不算进补给超时
            self.blocked_since = -1
            self.notes.append('整队恢复继续：周围已安全')
        if self.phase == 'idle':
            if w.t < self.next_try:
                self.counts['整队恢复·没开始·冷却中'] += 1
                return None
            if len(injured) < I.REGEN_MIN_HURT:
                self.counts[f'整队恢复·没开始·伤兵只有{len(injured)}个'] += 1
                return None
            if sum(u.hp_max-u.hp for u in injured) < I.REGEN_MIN_MISSING:
                self.counts['整队恢复·没开始·总缺血不够'] += 1
                return None
            # ★ 09-21：先看有没有人**身上已经有卷轴** —— 这一步不要求血量、也不要求商店可用。
            #   有卷轴的人不用跑腿，站着放就行；而最需要放卷轴的时候恰恰大家都残血，
            #   原来的 carriers 血量门槛（英雄 45% / 小兵 40%）会把残血的持有者整个排除，
            #   于是山丘拿着卷轴却永远不被选中（09-21 对局画面里那局）。
            #   英雄排在前面：物品通常在英雄身上，一般 1~3 个，省通道请求。
            held_by = None
            for u in w.my_heroes[:RECOVERY_SCAN_MAX]:      # 兵背包里的卷轴用不了（见文件头 Aihn），不算「有卷轴」
                if u.hp <= 0 or u.type in I.MECHANICAL:
                    continue
                b = self.bag(cmd, u, w.t)
                if b and 'sreg' in b:
                    held_by = (u, b)
                    break
            shops = [u for u in w.my_buildings if u.type == 'hvlt' and u.hp >= .97*u.hp_max
                     and not any(distance(u,f) < 1500 for f in hostiles)]
            # 买家只能是英雄（文件头 Aihn：兵的背包能拿不能用）。carrier_min 第二项（小兵血线）留着兼容调用方，不再用。
            hero_min = (carrier_min or (.45, .40))[0]
            carriers = [h for h in w.my_heroes if h.hp > hero_min*h.hp_max]
            # 每个候选只配最近的商店，避免多座藏宝室时做笛卡尔积、同一单位反复读背包。
            pairs = [(u, min(shops, key=lambda s: distance(u, s))) for u in carriers
                     if min((distance(u, s) for s in shops), default=1e9) < shop_max]
            if held_by is None and not pairs:
                self.counts[f'整队恢复·没开始·{int(shop_max)}内没有能买的人或商店'] += 1
                return None
            owned = []
            open_slot = []
            full_bags = 0            # 六格都占满的候选：排查「背包满了怎么办」就看这个数
            for u, s in pairs:
                bag = self.bag(cmd, u, w.t)
                if bag is None:
                    continue
                if 'sreg' in bag:
                    owned.append((u, s, bag))
                    break
                if None not in bag:
                    full_bags += 1
                else:
                    open_slot.append((u, s, bag))
            if held_by is None and not (owned or open_slot):
                # 09-21 设计要求：某个英雄背包满了（已有 6 个道具）就换背包没满的来使用恢复道具 ——
                # 换的只能是**另一个英雄**（兵用不了物品，见文件头）。走到这里说明英雄背包全满。
                self.counts['整队恢复·没开始·候选背包都满了'] += 1
                self.next_try = w.t+15
                return None
            # ★ 09-22 1-297 14 分：一堆残血单位等恢复卷轴，却一直没有购买和使用，怀疑是造兵在抢钱。
            #   原来要求**拍首**金子 >= 救急线 150 + 卷轴 100，不够就冷却 15 秒 —— 这局金子 86/330/63/152，
            #   钱一进来就被出兵、升级花掉，卷轴从来排不上。现在：需要买、买家和商店都在，就在建造预算里
            #   **预留卷轴的钱**（reserve -> runtime.committed()，需求表/AMAI/协建/修理都先扣它），够了立刻买，3 秒再看一次。
            if held_by is None and not owned and w.gold < I.PRICE['sreg']:
                self.reserve = I.PRICE['sreg']
                self.counts['整队恢复·攒卷轴钱（生产让路）'] += 1
                self.next_try = w.t+3
                return None
            # ★ 手上已经有卷轴的优先，而且**不配商店**（shop_id = None）：它不用跑腿，就地集合放就行。
            #   原来只有通过 carriers（英雄 45% / 小兵 40% 血）筛出来的人才会被检查背包，
            #   于是残血的山丘拿着卷轴也永远轮不到 —— 而那正是最该放卷轴的时候。
            if held_by is not None:
                h, bag = held_by
                s = None
                self.counts['整队恢复·用手上已有的卷轴'] += 1
            else:
                # ★ 09-21 1-284：原来在有空格的人里**随机**挑买家 —— 抽中了家里一个步兵，要走 5600 去分矿的藏宝室，
                #   两个英雄就在藏宝室 2000 外打野，却一次都没被选上（血魔法师、大法师都没去执行）。
                #   现在只在英雄里挑、离商店最近的去。
                h, s, bag = owned[0] if owned else min(open_slot, key=lambda p: distance(p[0], p[1]))
            self.holder_id = ident(h)
            self.shop_id = ident(s) if s is not None else None
            # 先只让持有者去买；卷轴真的在背包里之后才拉伤兵集合。
            # 006/008 两局原来会把十几个兵拖到商店等150秒，英雄未到店就整队超时。
            self.phase,self.started,self.next_read = ('gather' if 'sreg' in bag else 'shopping'),w.t,w.t
            self.gather_started = w.t if self.phase == 'gather' else -1
            self.reserve = 0 if self.phase == 'gather' else I.PRICE['sreg']   # 英雄走去藏宝室的路上钱别被花掉
            self.notes.append(f'整队恢复：{len(injured)} 名伤兵，缺血 {sum(u.hp_max-u.hp for u in injured):.0f}，'
                              + (f'{h.type} 手上已经有恢复卷轴，就地集合' if s is None else f'由 {h.type} 去藏宝室补恢复卷轴'))
        h = next((u for u in army if ident(u)==self.holder_id),None)
        s = next((u for u in w.my_buildings if ident(u)==self.shop_id),None) if self.shop_id else None
        # ★ 09-21 1-284：买家一不在这份视野里就当场作废 —— 家里补给只看主基地 2000 内的人，步兵一出门去分矿的藏宝室
        #   就「不可用」了（23:37:02 开始、23:37:23 作废；23:38:10 开始、1 秒后作废）。被毫秒层短暂拉走也一样。
        #   先等 HOLDER_MISSING_S；runtime 那边也把买家留在家里补给的视野里。
        if h is None and w.t-self.started <= 240:
            if self.missing_since < 0:
                self.missing_since = w.t
            if w.t-self.missing_since < HOLDER_MISSING_S:
                self.counts['整队恢复·买家暂时不在视野'] += 1
                return None
        elif h is not None:
            self.missing_since = -1
        # 卷轴已经在背包里时不需要商店（shop_id 为空）；只有还要去买的时候商店没了才算废了。
        if h is None or (self.shop_id is not None and s is None) or w.t-self.started > 240:
            self.cancel(w.t,'物品持有者/商店不可用或补给超时')
            return None
        if self.phase == 'gather' and self.gather_started >= 0 and w.t-self.gather_started >= RECOVERY_GATHER_MAX_S:
            # 不提前浪费卷轴；保留在背包里，解除恢复编队让主力继续执行任务。
            missing = sum(distance(u, h) > RECOVERY_READY_R for u in injured)
            self.cancel(w.t, f'伤兵集合超过 {int(RECOVERY_GATHER_MAX_S)} 秒（仍有 {missing} 名未到），保留卷轴继续行动')
            return None
        # 采购阶段不动整支军队；买到后才在商店外排恢复队形。
        shopping = self.phase in ('shopping','buying')
        if shopping and s is not None:
            out = [Intent('recover',[h],s.x+80,s.y+80,why='整队恢复·持有者先采购')]
            bystanders = []
        else:
            out, bystanders = self.formation(army, h, injured, *self.meet_at(w, h, injured, hostiles))
        if self.phase == 'healing':
            gain = sum(max(0,u.hp-self.hp_before.get(ident(u),u.hp)) for u in army)
            if not self.recovered and gain >= 30:
                self.recovered = True
                self.notes.append(f'整队恢复回执：已观察到友军净回血 {gain:.0f}')
            if w.t-self.used_at >= 45 or (not injured and w.t-self.used_at >= 6):
                self.cancel(w.t,'恢复完成，继续任务')
                return None
            if w.t-self.used_at >= 15 and not self.recovered:
                self.cancel(w.t,'未观察到回血，不继续空等')
                return None
            return out
        # ★ 09-21 一堆残血兵待在家里，山丘身上带着恢复道具，残血单位却不去找山丘集合使用，
        #   流程一直卡着。根因就是这一行原来写的 `distance(h,s) > SHOP_REACH`：
        #   **只有持有者站在商店 450 内才重读背包**，而卷轴已经在背包里时根本不需要回商店 ——
        #   于是每拍都在这里 return，部队一直排恢复队形却永远推进不到「使用」。
        #   靠近商店这个要求只对「还要去买」成立。
        if (shopping and s is not None and distance(h,s) > I.SHOP_REACH) or w.t < self.next_read:
            if shopping and s is not None and distance(h, s) > I.SHOP_REACH:
                self.counts['整队恢复·买家还在路上'] += 1
            return out
        self.next_read = w.t+3
        bag = cmd.inventory(h.addr)
        if bag is None:
            return out
        if self.phase == 'used':
            if 'sreg' not in bag:
                # ★ 09-22 1-290：恢复卷轴放出后仍原地等单位回血，浪费太多时间。
                #   原来这里进 healing、把整队按在原地最多 45 游戏秒（1-290：00:20:10 放出，00:20:25 才放人）。
                #   恢复卷轴（AIsl：600 内每人 225 血）是「非战斗类一次性物品」—— 挨打才打断，走路不打断，
                #   放出去就该走：边走边回血。
                self.counts['整队恢复·放出后不等·边走边回'] += 1
                self.cancel(w.t, '恢复卷轴已放出：边走边回血（挨打才打断，走路不打断），不原地等')
                return None
            elif w.t >= self.deadline:
                self.cancel(w.t,'卷轴未消耗，稍后重试')
                return None
            return out
        if self.phase == 'buying':
            if 'sreg' in bag:
                self.notes.append('整队恢复回执：恢复卷轴已进背包')
                self.phase = 'gather'
                self.reserve = 0
                self.gather_started = w.t
                # 买到手了就去找伤兵，不再把伤兵拖回商店门口（见 meeting_point）
                out, bystanders = self.formation(army, h, injured, *self.meet_at(w, h, injured, hostiles))
            elif w.t >= self.deadline:
                self.cancel(w.t,'购买未进背包，等待库存/资金')
                return None
            else:
                return out
        if 'sreg' not in bag:
            if None not in bag:
                self.cancel(w.t,'背包已满')
                return None
            # 任务启动时已按 BUY_GOLD_FLOOR 做过一次经济闸门。走到店的几拍里生产队列会花钱，
            # 不能再因“买完后仍留 450”反悔；只要还买得起100金就完成已承诺的急救。
            if w.gold < I.PRICE['sreg']:
                self.next_read = w.t+3
                return out
            # 商店把商品交给附近有背包的单位；让选定买家贴近商店，减少交错发货。
            if any(u.addr != h.addr and distance(u,s) < distance(h,s)
                   and self.bag(cmd, u, w.t) is not None for u in army):
                return [Intent('recover',[h],s.x+80,s.y+80,why='整队恢复·买家靠近商店')]
            if cmd.buy_item(s.addr,I.rawcode('sreg')):
                self.phase,self.deadline = 'buying',w.t+12
                self.notes.append('整队恢复：购买恢复卷轴，等待背包回执')
            else:
                v = buy_verdict(cmd, s.addr)
                self.counts[f'整队恢复·买卷轴被拒·裁决{v}'] += 1
                self.cancel(w.t, f'购买被拒（裁决 {v}：{BUY_VERDICT_CN.get(v, "未知")}，买家离店 {int(distance(h, s))}），'
                                 f'{int(BUY_REFUSED_S)} 游戏秒后再试')
                self.next_try = w.t + BUY_REFUSED_S      # 按补货周期退避（cancel 默认 30 秒）：每一轮都会把全军按在家里
                return None
            return out
        if self.phase == 'shopping':
            self.phase = 'gather'
            self.reserve = 0
            self.gather_started = w.t
            out, bystanders = self.formation(army, h, injured, *self.meet_at(w, h, injured, hostiles))
        # 走路期间伤兵已经自然恢复：保留卷轴给下一次，不为了满足使用门槛无限集合。
        if (self.phase == 'gather' and w.t-self.started >= 12
                and (len(injured) < I.REGEN_MIN_HURT
                     or sum(u.hp_max-u.hp for u in injured) < I.REGEN_MIN_MISSING)):
            self.cancel(w.t,'伤兵已自然恢复，卷轴留待下次')
            return None
        # 必须等“这次要治的所有伤兵”都到持有者身边再放（旧逻辑半径内凑够三人就用，落后的伤兵没到也照样消耗）。
        # ★ 09-22 不再等满血单位退出作用圈（见 formation：卷轴没有人数上限，1-301 就是等它等到超时）。
        if not injured or any(distance(u,h) > RECOVERY_READY_R for u in injured):
            return out
        if not I.regen_worth(h,injured,hostiles)[0]:
            return out
        if claims is not None and not claims.take(h.addr,1.5,prio=80):
            return out
        # ⚠ bag 最多是 3 游戏秒前读的（next_read 节流），而**毫秒层的 itemuse 同期也在用 sreg**
        #   （实测「sreg·用了」155 次）—— 两层用同一张卷轴。09-21 实测这里「使用被拒」30 次、
        #   成功只有 13 次，七成失败。下令前当场重读一次，并把两种失败分开记：
        #   格子里已经不是它了（被抢先用掉 / 槽位变了）vs 引擎真的拒了。哪种占多数决定下一步怎么修。
        fresh = cmd.inventory(h.addr) or bag
        if 'sreg' not in fresh:
            self.counts['整队恢复·卷轴已经不在背包里'] += 1
            self.cancel(w.t,'卷轴已经不在背包里（多半被微操层先用掉了）')
            return None
        if cmd.use_item(h.addr,cmd.USESLOT1+fresh.index('sreg')):
            self.phase,self.used_at,self.deadline = 'used',w.t,w.t+9
            self.hp_before = {ident(u):u.hp for u in army}
            self.recovered = False
            self.counts['整队恢复·放出卷轴'] += 1
            self.notes.append('整队恢复：使用恢复卷轴，等待消耗和回血回执')
        else:
            self.counts['整队恢复·使用被引擎拒'] += 1
            self.cancel(w.t,'使用被拒，稍后重试')
            return None
        return out

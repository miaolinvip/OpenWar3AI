# -*- coding: utf-8 -*-
"""英雄该干什么：一张**加权行动评分表**，不是一串 if。

**为什么换掉 if 链**（09-20 架构评审发现的根因 A）：
「英雄该不该退」这一个决策，原来横跨 tactics.py 的 18 个常量和 mod_combat.py 的 41 个常量，
用 if/return 串行短路协调。串行结构下**加一条新规则就可能遮蔽一条旧规则，而且是在另一个地方遮蔽的** ——
`creep-output-retreat` 从 v7 改到 v14 七个版本全在修英雄，MATCH_095_202 复盘里那句
「预警已经是严格判据……但实际调用处随后又执行：血量高于 65%、局部有四个护卫时忽略预警」就是这个形状。
259 局数据也指向同一处：败局每局英雄阵亡 4.17 次、胜局 1.33 次；英雄一死，出击频次砍半、被拆家、停在 2 矿。

抄的是 AMAI 的 `HeroFleeRules.ai`（本地 third_party/AMAI）：所有候选动作在同一张表上，
每条规则给某个动作加减分，**硬性不可行用 -1e6 表达**（硬约束和软偏好同一个机制），最后取最高分。
好处：① 规则平等参与，不存在遮蔽；② 每次决策能打印各动作得分，"为什么没撤"一眼可答；
③ 规则是数据，权重能进 brain_config.json 在线调、能自动搜。

**这一版故意只改一件事**：把原来「一律回家」拆成 home / withdraw / backline 三档。
数据依据：败局 `home` 状态占 57%、胜局 33%（MATCH_001_009），而人族部队站在主城附近并不回血 ——
那段横穿地图的移动几乎只有机会成本。其余权重都是从原来的阈值翻译过来的，在原场景下给出同样的动作。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# 候选动作。加新动作只要往这里加一项 + 写它的规则，不用动任何已有分支。
FIGHT = "fight"          # 留在队伍里正常作战
BACKLINE = "backline"    # 留在队伍，但站到前排后面（不脱离，只是不吃第一波）
WITHDRAW = "withdraw"    # 脱离接触到附近安全点（不回家）
HOME = "home"            # 回主基地
ACTIONS = (FIGHT, BACKLINE, WITHDRAW, HOME)

IMPOSSIBLE = -1e6        # 硬性不可行（AMAI 用 -1000000 表达同一件事）

# ── 权重（原 tactics 阈值的翻译；带 ★ 的是本版新增的行为）──
W_FIGHT_BASE = 10.0       # 默认倾向作战：没有这条，英雄会为了活命一直待在后面看戏
W_HEALTHY_FIGHT = 12.0    # hp >= HP_HEALTHY 时更敢打
W_HURT_HOME = 40.0        # 原 HERO_RETREAT_HP=0.50「英雄残血先回家」
W_HURT_WITHDRAW = 34.0    # ★ 同样的残血，先考虑"脱离到附近"，只有更危险/更接近家时才真回家
W_HURT_BACKLINE = 18.0
W_HURT_FIGHT = -25.0
W_CRITICAL = 45.0         # hp < HP_CRITICAL 再叠一层，push 到 home
W_NOTREADY_BACKLINE = 15.0   # 原 HERO_READY_HP=0.70「英雄单独恢复」
W_NOTREADY_FIGHT = -12.0
W_NOTREADY_HOME = 22.0       # 只在正规进攻/集结中给：打野中途血不满不回家（原行为）
W_SWARM_WITHDRAW = 55.0   # 身边一大群敌人（和毫秒层 HERO_SWARM_MIN 同一个意思，但这里只负责战略层的走位）
W_SWARM_HOME = 25.0
W_SWARM_FIGHT = -45.0
W_MICRO_WITHDRAW = 60.0   # 毫秒层已经在救它（hurt.json）—— 战略层不要同一拍把它派回去
W_TOWN_HOME = 55.0        # 家在挨打：这时回家是有意义的
W_ALLIES_FIGHT = 20.0     # 队友正在交战、自己还健康 -> 不许跑（英雄跑了这仗就崩）
TEAM_FIGHT_MIN = 2        # ★ 09-21 这么多队友在交战 = 团战：血不满/半血不离队，留后排放技能（见 decide）
WON_FRAC = 0.25           # ★ 09-22 敌方总强度不到我方这么多、英雄身边又没敌兵 = 对面打光了：血不满不回家（见 decide，1-287）
W_HURT_TEAM_BACKLINE = 45.0   # 团战中残血（35%~50%）：后排
W_HURT_TEAM_FIGHT = -8.0
W_TASK_FIGHT = 22.0       # 有明确任务（打野/出击）且健康
W_FAR_HOME = -28.0        # ★ 离家越远，回家越不划算（横穿地图的机会成本）
W_NEAR_HOME_BONUS = 25.0  # ★ 已经在家附近，回家几乎不要钱

HP_HEALTHY = 0.90
HP_READY = 0.70           # = 原 HERO_READY_HP
HP_HURT = 0.50            # = 原 HERO_RETREAT_HP
HP_CRITICAL = 0.35
DANGER_R = 1500.0         # = 原 DANGER_R（英雄多近算"身边有敌人"）
SWARM_R = 1300.0          # = 原 FIGHT_R / 毫秒层 HERO_DANGER_R
SWARM_MIN = 5             # 身边这么多敌方作战单位算"一大群"
SWARM_ESCORT_MIN = 3      # 但有这么多自己人 = 部队在场，整队进退由 AttackSequence 决定，英雄不单独跑
RESCUE_R = 3500.0         # 数"护卫"时算到这么远：赶得过来的援兵也算，英雄不该因为贴身没人就先跑
ALLY_R = 1200.0           # 队友在这么近交战
HOME_NEAR = 900.0         # 离家这么近就算"在家"（原 hurt 判据里的 900）
HOME_REST_R = 630.0       # 已经在家时「脱离」的安全点收在离大厅这么近以内（< HOME_NEAR，见 decide 末尾 2-032 那段）
FAR_HOME = 4000.0         # 离家超过这么远，回家的代价开始显著
WITHDRAW_DIST = 900.0     # 脱离到多远
TOWN_THREAT_MIN = 5.0     # 城镇威胁超过这个才算"家在挨打"


@dataclass
class HeroDecision:
    hero: object
    action: str
    scores: dict
    reasons: list = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0

    @property
    def top3(self) -> str:
        s = sorted(self.scores.items(), key=lambda kv: -kv[1])[:3]
        return " ".join(f"{k}{v:+.0f}" for k, v in s)


class HeroPlanner:
    """对每个英雄独立评分。不下令、不改 world —— 只回答"这个英雄现在该干什么"。"""

    def __init__(self) -> None:
        self.counts: Counter = Counter()
        self.notes: list[str] = []
        self.last: dict[int, HeroDecision] = {}

    def decide(self, world, home, hero, *, active: bool, offensive: bool = False,
               micro_held: bool = False) -> HeroDecision:
        """active = 正在执行任务（出击/集结/打野）；offensive = 正规进攻或集结中。
        两个标志分开是为了保住原行为：原来「血不满就单独回家」只在 attack/gather 生效，
        打野打到一半 60% 血是不回家的 —— 合成一个标志会让打野一路往返（MATCH_001_009 的"回城空转"）。"""
        sc = {a: 0.0 for a in ACTIONS}
        why: list[str] = []

        def add(action: str, w: float, reason: str) -> None:
            if w:
                sc[action] += w
                why.append(f"{action}{w:+.0f} {reason}")

        # ⚠ 血线取 tactics 的模块常量，不在这里再存一份：config.apply() 会在运行时改写 T.HERO_*，
        #   本模块自己留一份默认值就会变成第三份、而且是永远不生效的那一份（见 config.py 那段警告）。
        from . import tactics as T
        hp_hurt = getattr(T, "HERO_RETREAT_HP", HP_HURT)
        hp_ready = getattr(T, "HERO_READY_HP", HP_READY)
        hp = (hero.hp / hero.hp_max) if hero.hp_max > 0 else 1.0
        d_home = math.hypot(hero.x - home.x, hero.y - home.y) if home is not None else 0.0
        foes = [u for u in world.enemy_fighters if u.hp > 0]
        near = [u for u in foes if math.hypot(u.x - hero.x, u.y - hero.y) <= DANGER_R]
        swarm = [u for u in foes if math.hypot(u.x - hero.x, u.y - hero.y) <= SWARM_R]
        allies_fighting = [a for a in world.my_army
                           if math.hypot(a.x - hero.x, a.y - hero.y) <= ALLY_R
                           and any(math.hypot(a.x - f.x, a.y - f.y) <= 500 for f in foes + list(world.creeps))]

        add(FIGHT, W_FIGHT_BASE, "默认作战")
        if hp >= HP_HEALTHY:
            add(FIGHT, W_HEALTHY_FIGHT, f"血 {hp:.0%}")
        # ★ 09-21 进攻对方主基地或 2 矿时，英雄血量充足却常离战场很远不上前（血魔法师半血就开始后撤）；
        #   应该是被集火时再往后躲、没人打就上前，场面明显占优时尤其如此。
        #   18:00 以后出击/集结中英雄换动作的记录：血 40%~70% 被判「脱离」约 170 次、「回家」约 140 次，70% 以上才留下打。
        #   两条把它往外推：「血不满（< 70%）进攻中先恢复」（本意是出发前先恢复，打起来以后也生效）和「残血（< 50%）回家」。
        #   队友正在打（TEAM_FIGHT_MIN 个以上）时，英雄不因为血不满离开；35% 以上留在后排放技能 ——
        #   真被人集火、在掉血，毫秒层按「谁在打它」临时拉开（mod_combat 英雄集火 / 低血量），没人打了大脑再派回来。
        team_fight = len(allies_fighting) >= TEAM_FIGHT_MIN
        # ★ 09-22 1-287 18:30：明显占优时大法师、血法师却跑回家，追查是哪条决策把它们推回去的。
        #   18:22「英雄残血先回家」时大法师 68% 血，敌方强度 3.0 对我方 44.4、身边没有敌兵：
        #   「血不满，进攻中先恢复」（本意是出发前先恢复）在对面已经打光、只剩拆建筑的时候也生效，
        #   而「队友在打」又因为没人可打恒为假。对面打光了（WON_FRAC）且身边没敌兵时：血不满不往回推，
        #   残血（快死以上）也只退后排，不回家。真来了敌兵 near 就不成立，照旧。
        foe_s, own_s = getattr(world, "foe_strength", None), getattr(world, "own_strength", None)
        creeps_near = any(c.hp > 0 and math.hypot(c.x - hero.x, c.y - hero.y) <= DANGER_R for c in getattr(world, "creeps", ()))
        won = (not near and not creeps_near and foe_s is not None and own_s is not None
               and foe_s <= WON_FRAC * max(own_s, 1.0))
        if hp < hp_ready:
            add(BACKLINE, W_NOTREADY_BACKLINE, f"血 {hp:.0%} 不满")
            add(FIGHT, W_NOTREADY_FIGHT, f"血 {hp:.0%} 不满")
            if offensive and not team_fight and not (won and hp >= HP_CRITICAL):
                add(HOME, W_NOTREADY_HOME, f"血 {hp:.0%} 不满，进攻中先恢复")
                add(WITHDRAW, W_NOTREADY_HOME * 0.8, f"血 {hp:.0%} 不满，进攻中先恢复")
        if hp < hp_hurt and (active or near):
            if won and hp >= HP_CRITICAL:
                add(BACKLINE, W_HURT_TEAM_BACKLINE, f"残血 {hp:.0%}，对面已经打光（敌 {foe_s:.0f} / 我 {own_s:.0f}）：后排")
                add(FIGHT, W_HURT_TEAM_FIGHT, f"残血 {hp:.0%}")
            elif team_fight and hp >= HP_CRITICAL:
                add(BACKLINE, W_HURT_TEAM_BACKLINE, f"残血 {hp:.0%} 但 {len(allies_fighting)} 个队友在打：后排放技能")
                add(FIGHT, W_HURT_TEAM_FIGHT, f"残血 {hp:.0%}")
            else:
                add(HOME, W_HURT_HOME, f"残血 {hp:.0%}")
                add(WITHDRAW, W_HURT_WITHDRAW, f"残血 {hp:.0%}")
                add(BACKLINE, W_HURT_BACKLINE, f"残血 {hp:.0%}")
                add(FIGHT, W_HURT_FIGHT, f"残血 {hp:.0%}")
        if hp < HP_CRITICAL:
            add(HOME, W_CRITICAL, f"快死了 {hp:.0%}")
            add(WITHDRAW, W_CRITICAL * 0.6, f"快死了 {hp:.0%}")
            # 不减 FIGHT 的话，45% 和 20% 血给出的"作战"得分一模一样 —— 表就不单调了
            # （test_heroplan.test_worse_situation_pushes_further_from_fight 抓到过）
            add(FIGHT, -W_CRITICAL * 0.5, f"快死了 {hp:.0%}")
        # ⚠ 「身边一大群敌人」只在**英雄相对孤立**时才算英雄自己的问题。
        #   全队都在场时被一群人围住是**部队层面**的决策（AttackSequence 会整队撤），
        #   这时再让英雄单独往另一个方向脱离，等于把队伍拆成两半、英雄留在原地附近 —— 更危险。
        #   这就是两层抢同一个单位的老形状，只不过换到了评分表里面。
        #   同理，「护卫」要把赶得过来的援兵算进去：只数贴身的人，会在「两个英雄 + 两个步兵在前面接战、
        #   六只狮鹫正从 3000 外赶来」这种局面里让英雄先跑掉，把队伍拆成两半（test_fight_support 抓到过）。
        escort = [a for a in world.my_army
                  if math.hypot(a.x - hero.x, a.y - hero.y) <= ALLY_R and a.hp > 0]
        reinforce = [a for a in world.my_army
                     if math.hypot(a.x - hero.x, a.y - hero.y) <= RESCUE_R and a.hp > 0]
        if len(swarm) >= SWARM_MIN and len(reinforce) < SWARM_ESCORT_MIN:
            add(WITHDRAW, W_SWARM_WITHDRAW, f"{int(SWARM_R)} 内敌 {len(swarm)} 个、护卫只有 {len(escort)}")
            add(HOME, W_SWARM_HOME, f"{int(SWARM_R)} 内敌 {len(swarm)} 个、护卫只有 {len(escort)}")
            add(FIGHT, W_SWARM_FIGHT, f"{int(SWARM_R)} 内敌 {len(swarm)} 个、护卫只有 {len(escort)}")
        if micro_held:
            add(WITHDRAW, W_MICRO_WITHDRAW, "毫秒层正在救它")
            add(FIGHT, -W_MICRO_WITHDRAW, "毫秒层正在救它")
        if allies_fighting and hp >= hp_hurt:
            add(FIGHT, W_ALLIES_FIGHT, f"{len(allies_fighting)} 个队友在打")
        if active and hp >= hp_ready:
            add(FIGHT, W_TASK_FIGHT, "有任务且血够")

        # 家 —— 回家的代价随距离涨；家在挨打时反过来
        threat = max((tw.threat for tw in world.towns if tw.owner == world.me), default=0.0)
        if threat >= TOWN_THREAT_MIN:
            add(HOME, W_TOWN_HOME, f"家在挨打（威胁 {threat:.0f}）")
        if home is not None:
            if d_home <= HOME_NEAR:
                add(HOME, IMPOSSIBLE, "已经在家门口了")   # 再下一次回家令 = 原地不动，白占一拍
                add(WITHDRAW, W_NEAR_HOME_BONUS * 0.4, "就在家边上")
            elif d_home >= FAR_HOME:
                add(HOME, W_FAR_HOME, f"离家 {int(d_home)}，横穿地图只有机会成本")
            else:
                add(HOME, W_NEAR_HOME_BONUS * (1.0 - d_home / FAR_HOME), f"离家 {int(d_home)}")
        else:
            add(HOME, IMPOSSIBLE, "没有大厅")

        # 没有威胁也没有伤时，脱离/回家都不该赢过作战
        if hp >= HP_HEALTHY and not near and not micro_held:
            add(WITHDRAW, IMPOSSIBLE, "满血且身边没敌人")
            add(HOME, IMPOSSIBLE, "满血且身边没敌人")

        action = max(ACTIONS, key=lambda a: (sc[a], a == FIGHT))
        d = HeroDecision(hero, action, sc, why)
        if action == WITHDRAW:
            d.x, d.y = self._safe_point(world, hero, home)
            # ★ 09-21 2-032 30 分钟：残血大法师在家里来回左右移动。
            #   实机每 2 秒翻一次：离家 < HOME_NEAR 时「回家」判不可能（再下回家令是原地不动）-> 「脱离」胜出，
            #   安全点背着敌人算、离家约 1600 -> 走出 HOME_NEAR -> 「回家」+61 又赢 -> 走回来……
            #   两条规则交接的那条线两边各有一个会把它推过线的动作。已经在家时，安全点收在家里（大厅背敌那一侧）。
            if home is not None and d_home <= HOME_NEAR:
                dx, dy = d.x - home.x, d.y - home.y
                n = math.hypot(dx, dy)
                if n > HOME_REST_R:
                    d.x, d.y = home.x + dx / n * HOME_REST_R, home.y + dy / n * HOME_REST_R
                    why.append(f"{WITHDRAW} 已在家：安全点收在大厅 {int(HOME_REST_R)} 以内")
        elif action == HOME and home is not None:
            d.x, d.y = home.x, home.y
        return d

    def plan(self, world, home, *, active: bool, offensive: bool = False,
             micro_held: set | None = None) -> list[HeroDecision]:
        micro_held = micro_held or set()
        out = []
        for h in world.my_heroes:
            if h.hp <= 0:
                continue
            d = self.decide(world, home, h, active=active, offensive=offensive,
                            micro_held=h.addr in micro_held)
            self.counts[f"英雄·{d.action}"] += 1
            prev = self.last.get(h.addr)
            if prev is None or prev.action != d.action:
                self.notes.append(f"英雄 {h.type} {int(h.hp)}/{int(h.hp_max)} -> {d.action}：{d.top3}")
                self.counts[f"英雄换动作·{d.action}"] += 1
            self.last[h.addr] = d
            out.append(d)
        return out

    @staticmethod
    def _safe_point(world, hero, home) -> tuple[float, float]:
        """脱离到附近安全点：背着最近的那群敌人退 WITHDRAW_DIST，方向上再朝家偏一点
        （朝家偏是为了下一步万一要回家不用走回头路；但**不是**回家 —— 回家由 HOME 这个动作负责）。"""
        foes = [u for u in world.enemy_fighters if u.hp > 0] + [c for c in world.creeps if c.hp > 0]
        if foes:
            f = min(foes, key=lambda u: math.hypot(u.x - hero.x, u.y - hero.y))
            vx, vy = hero.x - f.x, hero.y - f.y
        elif home is not None:
            vx, vy = home.x - hero.x, home.y - hero.y
        else:
            return hero.x, hero.y
        m = math.hypot(vx, vy) or 1.0
        vx, vy = vx / m, vy / m
        if home is not None:
            hx, hy = home.x - hero.x, home.y - hero.y
            hm = math.hypot(hx, hy) or 1.0
            vx, vy = vx + 0.35 * hx / hm, vy + 0.35 * hy / hm
            m = math.hypot(vx, vy) or 1.0
            vx, vy = vx / m, vy / m
        return hero.x + vx * WITHDRAW_DIST, hero.y + vy * WITHDRAW_DIST

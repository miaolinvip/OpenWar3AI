# -*- coding: utf-8 -*-
"""英雄被自己人堵住时，强制让路（09-21 大法师被一个农民卡在家里出不去；英雄是这个游戏里优先级最高的单位，
遇到这种情况直接强制附近单位让开）。

**为什么以前发现不了**：`act._point` 的去重是「同令同点 400 内、而且它没闲着（有订单或有任务）-> 不重下」。
英雄被农民堵住时引擎里它**有订单**（正在试图走），于是每一拍都命中「同令不重下」——
大脑既不重下令，也没有任何计数会变。`order != 0` 同时表示「在执行」和「在原地执行不了」，
这两件事在日志里长得一模一样（一个数同时表示两件事，要拆开各记各的）。
1-232 局 681 秒下完「英雄先手拉出营地」后，到 741 秒「英雄看戏」之间 60 游戏秒没有任何新动作，就是这个形状。

所以这里做两件事，都带计数：
  ① 判「卡住」不看订单，看**位移**：STUCK_S 游戏秒内挪不到 STUCK_MOVE，而且附近 QUIET_R 内没有敌人（不是站着输出）；
  ② 卡住 -> 让 CLEAR_R 内的自己人往「英雄前进方向的两侧」让开，并清掉英雄的去重缓存让它重新寻路；
     还卡着超过 ESCALATE_S -> 让英雄自己先错身到侧后方，打断引擎那条走不通的路径。

只对英雄生效。普通单位站着不动的正常情况太多（采矿、伐木、守卫、排队），用同一套判据会误伤。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from . import gamedata as G

STUCK_S = 4.0          # 位置没怎么变超过这么多游戏秒 = 卡住（英雄移速 270~350/游戏秒，4 秒正常能走 1000+）
STUCK_MOVE = 140.0     # 这段时间里位移不到这么多算「没动」；留出转身和碰撞微调的余量
QUIET_R = 700.0        # 这么近有敌方作战单位或野怪 -> 它是在打架，不是卡住
CLEAR_R = 300.0        # 让路半径：两个单位直径（约 72×2）加通道余量
CLEAR_PUSH = 380.0     # 挡路的人往侧面挪这么远
CLEAR_MAX = 6          # 一次最多让几个，避免整个基地的农民一起跑
CLEAR_COOLDOWN = 5.0   # 同一个英雄两次让路之间至少隔这么多游戏秒
ESCALATE_S = 9.0       # 让过路还卡这么久 -> 英雄自己错身
ESCALATE_BACK = 420.0  # 错身距离
ESCALATE_COOLDOWN = 8.0
STRAY_R = 2200.0       # 没人管的兵离大部队这么远就收回来（比 tactics.STRAGGLER_R 略宽：这是最后一道兜底）
STRAY_COOLDOWN = 20.0  # 记原话的间隔（游戏秒）；计数不受它限制
STRAY_MAX_SHARE = 0.4  # 散开的超过全军这个比例就不是「个别落单」，交给战术层
MAIN_MIN = 3           # 最大的团至少这么多人才算「有主力」；否则落单的归队点是家


def side_point(hx: float, hy: float, tx: float, ty: float, bx: float, by: float, push: float,
               bias: int = 0) -> tuple[float, float]:
    """把挡在 (hx,hy)->(tx,ty) 这条路上的单位 (bx,by) 推到这条线的侧面，而不是推远。

    推远（径向）没用：英雄要往前走，挡路的人被推到前面去还是挡着。
    往垂直方向推才是「让开通道」。正好站在线上（侧向偏移≈0）的按 bias 分左右，保证两个人不会往同一边挤。"""
    dx, dy = tx - hx, ty - hy
    d = math.hypot(dx, dy)
    if d < 1e-6:
        vx, vy = bx - hx, by - hy
        m = math.hypot(vx, vy) or 1.0
        return bx + vx / m * push, by + vy / m * push
    ux, uy = dx / d, dy / d
    px, py = -uy, ux                       # 垂直于前进方向
    side = (bx - hx) * px + (by - hy) * py
    if abs(side) < 1e-6:
        s = 1.0 if bias % 2 == 0 else -1.0
    else:
        s = 1.0 if side > 0 else -1.0
    return bx + px * s * push, by + py * s * push


@dataclass
class _Anchor:
    t: float
    x: float
    y: float


class StuckWatch:
    """每拍记一次英雄位置；卡住时产出「让路」意图。

    用法（runtime.job_tactics 下令之前）：
        extra, forget = self.stuck.step(w, intents, protected=..., t=w.t)
        intents += extra
        for addr in forget: self.act.last_point.pop(addr, None)
    """

    def __init__(self) -> None:
        self.anchors: dict[int, _Anchor] = {}
        self.cleared_at: dict[int, float] = {}
        self.escalated_at: dict[int, float] = {}
        self.counts: Counter = Counter()
        self.notes: list[str] = []
        self.stuck_now: dict[int, float] = {}    # 英雄 addr -> 已经卡了多少游戏秒（给 live/lesson 看）
        self.stray_at: float = -1e9              # 上次记「落单」原话的游戏秒

    # ── 位移跟踪 ──
    def observe(self, heroes, t: float) -> None:
        alive = set()
        for h in heroes:
            alive.add(h.addr)
            a = self.anchors.get(h.addr)
            if a is None or math.hypot(h.x - a.x, h.y - a.y) > STUCK_MOVE:
                self.anchors[h.addr] = _Anchor(t, h.x, h.y)
        for addr in [k for k in self.anchors if k not in alive]:
            self.anchors.pop(addr, None)
            self.cleared_at.pop(addr, None)
            self.escalated_at.pop(addr, None)

    def stuck_for(self, addr: int, t: float) -> float:
        a = self.anchors.get(addr)
        return 0.0 if a is None else max(0.0, t - a.t)

    # ── 主入口 ──
    def step(self, world, intents, protected: set | None = None, t: float | None = None):
        """返回 (让路意图列表, 要清掉下令去重缓存的 addr 列表)。"""
        from .tactics import Intent          # 延迟导入：tactics 会 import 本模块的常量做测试

        t = world.t if t is None else t
        protected = protected or set()
        heroes = [h for h in world.my_heroes if h.hp > 0]
        self.observe(heroes, t)
        self.stuck_now = {}
        out: list = []
        forget: list[int] = []
        if not heroes:
            return out, forget

        foes = [u for u in world.enemy_fighters + world.creeps if u.hp > 0]
        # 这一拍每个英雄被指派到哪（用来算前进方向）；没有指派的英雄不判卡住 —— 站着不动可能就是我们让它站的
        goal: dict[int, tuple[float, float]] = {}
        for it in intents:
            if it.kind not in ("move", "attack_move", "recover"):
                continue
            for u in it.units:
                if u.addr not in goal:
                    goal[u.addr] = (it.x, it.y)

        for h in heroes:
            held = self.stuck_for(h.addr, t)
            if held < STUCK_S:
                continue
            tgt = goal.get(h.addr)
            if tgt is None:
                self.counts["英雄没挪窝·这一拍没给它任务"] += 1
                continue
            if math.hypot(h.x - tgt[0], h.y - tgt[1]) <= CLEAR_R:
                continue                      # 已经到地方了，站着是对的
            if any(math.hypot(h.x - f.x, h.y - f.y) <= QUIET_R for f in foes):
                self.counts["英雄没挪窝·在交战中"] += 1
                continue
            self.stuck_now[h.addr] = round(held, 1)

            blockers = self._blockers(world, h, tgt, protected)
            if blockers and t - self.cleared_at.get(h.addr, -1e9) >= CLEAR_COOLDOWN:
                self.cleared_at[h.addr] = t
                forget.append(h.addr)
                self.counts["英雄被堵·让路"] += 1
                self.counts[f"英雄被堵·让路·{len(blockers)}人"] += 1
                self.notes.append(
                    f"英雄被自己人堵住：{h.type} 卡了 {held:.0f} 游戏秒没挪窝（离目标 "
                    f"{int(math.hypot(h.x - tgt[0], h.y - tgt[1]))}），让开 {len(blockers)} 个"
                    f"（{'、'.join(sorted({b.type for b in blockers}))}）")
                for i, b in enumerate(blockers):
                    bx, by = side_point(h.x, h.y, tgt[0], tgt[1], b.x, b.y, CLEAR_PUSH, bias=i)
                    out.append(Intent("move", [b], bx, by, why="给英雄让路"))
            elif not blockers:
                self.counts["英雄卡住·身边没人可让"] += 1

            if held >= ESCALATE_S and t - self.escalated_at.get(h.addr, -1e9) >= ESCALATE_COOLDOWN:
                self.escalated_at[h.addr] = t
                forget.append(h.addr)
                self.counts["英雄卡住·自己错身"] += 1
                ex, ey = side_point(h.x, h.y, tgt[0], tgt[1], h.x, h.y, ESCALATE_BACK, bias=int(h.addr) % 2)
                # 往侧面错半步再重走：引擎会丢掉那条走不通的路径重新寻路
                out.append(Intent("move", [h], ex, ey, why="卡太久，英雄自己错身重新寻路"))

        return out, forget

    # ── 落单兜底 ──
    def strays(self, world, intents, busy: set, t: float):
        """这一拍**没被任何意图覆盖**、又离大部队很远的兵 -> 收回来。

        ★ 09-21 5-083 23 分左右个别小兵不撤退、白白战死；
        1-247 22 分钟几个残血小兵不回家、在野外原地不动，敌军一来又冲上去阵亡。
        落单不是从一个地方来的：主力回家时漏掉掉队的（tactics 那条重心判据）、
        「援兵太少太远，去前沿集结点攒人：**1/1 个**」把单个兵送到一个点就不管了、
        撤退时掉队……逐条堵会漏。这里做一条通用兜底：
        **谁都没管、而且离大部队 STRAY_R 以外的，一律归队。**

        只兜底、不抢活：已经被本拍意图覆盖的不动，毫秒层在救的（busy）不动 ——
        那边优先级更高，抢了反而把保命令覆盖掉（这正是老架构的两层互抢）。"""
        from .tactics import Intent

        covered = {u.addr for it in intents for u in it.units}
        pool = [u for u in world.my_army if u.hp > 0 and u.addr not in covered and u.addr not in busy]
        if not pool:
            return []
        # ⚠ 归队点要用**最大的那个聚团**（world 已经按 1500 聚好），不能用「这一拍有任务的人」的重心：
        #   只有一个人有任务时，那一个人就成了「大部队」，其余全员反被判成落单（测试抓到过）。
        armies = [a for a in world.my_armies if a.units]
        main = max(armies, key=lambda a: a.strength) if armies else None
        hall = world.my_halls[0] if world.my_halls else None
        # 最大的团都凑不够 MAIN_MIN 个人 = 没有真正的主力（全军被打散、或者主力没了）。
        # 这时归队点是家，而不是「离自己最近的那一小撮」—— 否则两个落单的兵互相成团，
        # 谁也不回家，正好是 1-247 里「几个残血小兵在野外原地不动」的现象。
        if main is not None and len(main.units) >= MAIN_MIN:
            ax, ay, main_ids = main.x, main.y, {u.addr for u in main.units}
        elif hall is not None:
            ax, ay, main_ids = hall.x, hall.y, set()
        else:
            return []
        far = [u for u in pool if u.addr not in main_ids and math.hypot(u.x - ax, u.y - ay) > STRAY_R]
        # 散开的人占了全军一大半 = 不是「个别落单」，是队形问题，交给战术层，兜底不插手
        alive = [u for u in world.my_army if u.hp > 0]
        if far and len(far) > max(2, len(alive) * STRAY_MAX_SHARE):
            self.counts["落单兜底·散得太多·交给战术层"] += 1
            return []
        if not far:
            self.counts["落单兜底·没人需要收"] += 1
            return []
        if t - self.stray_at >= STRAY_COOLDOWN:
            self.stray_at = t
            self.notes.append(f"落单的 {len(far)} 个兵没人管（离大部队 "
                              f"{min(math.hypot(u.x - ax, u.y - ay) for u in far):.0f}~"
                              f"{max(math.hypot(u.x - ax, u.y - ay) for u in far):.0f}），收回来")
        self.counts["落单兜底·收回来"] += 1
        self.counts[f"落单兜底·收回来·{len(far)}人"] += 1
        # control=hold：租约 58 > 毫秒层补攻击令 55（1-304 14:07 这条令发给三个落单的兵，同一秒被补攻击令盖回去，三个全死）
        return [Intent("move", far, ax, ay, why=f"落单没人管的 {len(far)} 个兵归队", control="hold")]

    def _blockers(self, world, hero, tgt, protected: set) -> list:
        """英雄身边挡路的自己人：非建筑、非英雄、不在交战、没有被别的任务占着。
        农民排在前面 —— 它们最常停在基地窄道上，而且被调走之后 job_gold/job_construction 会自动把它们送回去。"""
        foes = [u for u in world.enemy_fighters + world.creeps if u.hp > 0]
        cands = []
        for u in world.mine:
            if u.addr == hero.addr or u.hp <= 0 or G.is_building(u.type) or G.is_hero(u.type):
                continue
            if u.addr in protected:
                continue
            if math.hypot(u.x - hero.x, u.y - hero.y) > CLEAR_R:
                continue
            if any(math.hypot(u.x - f.x, u.y - f.y) <= QUIET_R for f in foes):
                continue                      # 在打架的兵不调走
            cands.append(u)
        # 优先让挡在前进方向上的（侧向偏移小 = 正堵着路），再按离英雄近的
        dx, dy = tgt[0] - hero.x, tgt[1] - hero.y
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d

        def key(u):
            side = abs((u.x - hero.x) * -uy + (u.y - hero.y) * ux)
            ahead = (u.x - hero.x) * ux + (u.y - hero.y) * uy
            return (0 if G.is_worker(u.type) else 1, 0 if ahead > -40 else 1, side)

        cands.sort(key=key)
        return cands[:CLEAR_MAX]

# -*- coding: utf-8 -*-
"""执行层：把决策的意图/建造决定翻成游戏命令（照旧 PowerShell AI 的底层动作移植）。

所有下令先以 strategy 身份抢指挥权（w3claim.Claims("strategy")）：
  出击/移动 strategy 50、建造/伐木/调工人 strategy_eco 45。抢不到就不下，计数进 counts。
计数全记（成功/被拒/抢不到/出错），不按条件打印。
"""
from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field

from . import gamedata as G

import w3paths  # noqa: E402

ROOT = w3paths.ROOT
MODULES = w3paths.SDK
if MODULES not in sys.path:
    sys.path.insert(0, MODULES)

ORDER_SMART = 0x000D0003
ORDER_ATTACK = 0x000D000F
ORDER_MOVE = 0x000D0012
ORDER_HARVEST = 0x000D0032
ORDER_REVIVE = 0x000D0047
ORDER_REPAIR = 0x000D0038
BUILD_STARTED_ORDERS = frozenset({0x000D001B, 0x000D001C})   # humanbuild / orcbuild：地基已放下、正在施工（和 powerbuild.BUILDING_ORDERS 对齐）
ORDER_ENTANGLE = 0x000D00B3    # order-ids.txt：entangle（生命之树缠绕金矿）
ORDER_MILITIA = 0x000D0068     # 人族农民单体战斗号召（Amil）
ORDER_MILITIA_OFF = 0x000D0069 # 民兵解除战斗号召
HARVEST_ORDERS = (0x000D0032, 0x000D0031, 0x000D0036)
REPEAT_S = 8.0                 # 墙钟秒（3 倍速约 24 游戏秒）：没闲着的单位这么久内不重下同一条点令
# ★ 09-22 点命令的下令路（A/B）。毫秒层撤退令的回执（mod_combat RT_KERNEL_SHARE 那段）：动作 41（w3cmd.move_to）
#   对**正在攻击**的单位常常顶不掉手上的攻击令 —— 「接了」只说明 DLL 调用走完了；JASS IssuePointOrder 那条路（标签「内核」）
#   英雄撤退 13 次 12 次走了。大脑这边：给「正在打某个单位」的兵下移动令，38% 两秒内没怎么走（一次性分析脚本 queue_test.py）。
#   先按 KERNEL_SHARE 一半走内核、一半照旧，cmdlog 里方法名 move_kernel / move_to 分开，一局之后对比再全换。
#   ★ A/B 结果（367 局，一次性分析脚本 brain_ab.py）：两条路没差别 —— 之前在打单位 内核 81% / 动作41 79%、之前攻击移动 75% / 73%、
#     之前移动 91% / 91% 两秒内走近 150。大脑下的是智能命令 0x000D0003 / 攻击移动 0x000D000F，动作 41 的模板就是照右键智能命令抄的；
#     毫秒层吃亏的是 **移动令 0x000D0012** 走动作 41（撤退令，那边已全走内核）。这里回到动作 41（开关留着，要再对照时调它）。
KERNEL_SHARE = 0.0
# ★★ 09-22 晚 1-402 16:30：三个骚扰单位只回来一个，另两个顶着塔一直打到死，明明有充足的撤退时机。
#   21:48:42 大脑给三个骑士下「整队回家」（右键令 0x000D0003 走动作 41，cmdlog 全是 ok）：当时闲着的那个马上走了，
#   正在打远古守护者的两个照打 7 秒（612 -> 331 血），21:48:48 毫秒层用内核移动令一下就拉动了，已经晚了，两个都死。
#   上面那次 A/B 按「大脑上一条令」分组，没分出「自己索敌打上了建筑」。按帧里真实订单 + 任务目标重算（1 号实例 09-22 全天，
#   一次性分析脚本 move_vs_building.py，2 秒内走开 150）：
#     单位正在攻击时           打建筑        打单位
#     动作 41（大脑右键令）     64%（33）     74%（78）
#     内核 + 右键令（大脑）     65%（17）     60%（5）
#     内核 + 移动令（毫秒层）  100%（10）     96%（48）
#   差在「移动令 0x000D0012」+ 内核这个组合。现在大脑的移动意图（回家 / 撤退 / 集结 / 让路）一律下移动令、走内核；
#   攻击移动 / 右键打目标照旧。还挂着攻击令的 MOVE_RETRY_S 后重下（原来 REPEAT_S 8 秒内同令不重下）。
MOVE_KERNEL = True
MOVE_RETRY_S = 1.5
FORMATION_MIN_TRAVEL = 280.0   # 短距离贴身微调不展开；长途移动才保持队形，避免目标点附近反复换位
FORMATION_SIDE = 150.0         # 横向间距：一排多列，减少整队争同一条寻路通道
FORMATION_DEPTH = 150.0        # 纵向间距：前排先到，远程和英雄在后排
FORMATION_MAX_COLS = 5
FORMATION_FRONT_SUMMONS = frozenset({"hwat", "hwt2", "hwt3", "osw1", "osw2", "osw3", "osp1", "osp2", "osp3"})

# 占地（沿用旧 PowerShell AI 落点规则里的数，不要换成 SLK pathTex）
FOOTPRINT = {"htow": 512, "hkee": 512, "hcas": 512, "ogre": 512, "ostr": 512, "ofrt": 512, "unpl": 512, "unp1": 512, "unp2": 512,
             "etol": 512, "etoa": 512, "etoe": 512, "hhou": 128, "uzig": 192, "emow": 192, "otrb": 192}


def rawcode(code: str) -> int:
    b = code.encode("latin-1")
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def footprint(code: str) -> int:
    return FOOTPRINT.get(code, 320)


def snap32(v: float) -> float:
    return float(round(v / 32.0) * 32)


def formation_points(units, x: float, y: float) -> list[tuple[object, float, float]]:
    """给整组点命令分配稳定的多列落点。

    目标点代表队形前排中央。近战/坦克在前，普通远程在中，英雄最后。只有两人以上且确实要走一段路才展开，
    贴身微操仍使用原目标点。
    ★ 09-22 整队移动时单位之间经常互相挤、谁都走不上前。原来同一类按**地址**排座位：
      谁坐左边、谁坐前排和它现在站哪无关 —— 1 号实例 6 局 629 条整队令里 90% 有人的路线互相交叉（单位对 36.6%），
      按直线匀速推演 1259 对会撞上（1200 内的短途令 323 对）。现在：同一类里**离目标近的进前排**，
      每一排（不分兵种）**按现在的横向位置从左到右入座** —— 同一批令推演撞上 1259 -> 623、短途 323 -> 141，
      总路程略短；前后层级（近战前、英雄后）不变。不会每拍洗牌：换座最多挪一两列（150~300），
      _point 的「同一种令、同一个点 400 内」照样不重下。那次比较用的是一次性分析脚本 formation_variants.py。
    """
    us = list(units)
    if len(us) < 2:
        return [(u, float(x), float(y)) for u in us]
    cx = sum(float(getattr(u, "x", x)) for u in us) / len(us)
    cy = sum(float(getattr(u, "y", y)) for u in us) / len(us)
    dx, dy = float(x) - cx, float(y) - cy
    distance = math.hypot(dx, dy)
    if distance < FORMATION_MIN_TRAVEL:
        return [(u, float(x), float(y)) for u in us]
    fx, fy = dx / distance, dy / distance
    lx, ly = -fy, fx

    def role(u):
        typ = getattr(u, "type", "") or ""
        hero = G.is_hero(typ)
        rng = float(G.stat(typ, "range", 100) or 100) if typ else 100.0
        front = rng <= 150.0 or typ in FORMATION_FRONT_SUMMONS
        # 英雄安全级最高：即使是近战英雄，也不把它排在普通前排兵之前。
        return (2 if hero else (0 if front else 1), int(getattr(u, "addr", 0)))

    def ahead(u):
        return float(getattr(u, "x", cx)) * fx + float(getattr(u, "y", cy)) * fy

    def side(u):
        return float(getattr(u, "x", cx)) * lx + float(getattr(u, "y", cy)) * ly

    # 同一层级里离目标近的排前面（按一排的纵深分桶）；同一桶里横向靠外的先入前排、靠中间的去后排 ——
    # 没坐满的后排座位在中间（并排四个人走成 3+1 时，后排那个是中间的人，不横穿）。地址只用来打破平局。
    mid = sum(side(u) for u in us) / len(us)
    ordered = sorted(us, key=lambda u: (role(u)[0], -round(ahead(u) / FORMATION_DEPTH),
                                        -abs(side(u) - mid), role(u)[1]))
    cols = min(FORMATION_MAX_COLS, max(2, int(math.ceil(math.sqrt(len(us) * 1.8)))))
    out = []
    for row in range(0, (len(ordered) + cols - 1) // cols):
        members = sorted(ordered[row * cols:(row + 1) * cols], key=lambda u: (side(u), role(u)[1]))
        for col, u in enumerate(members):
            lateral = (col - (len(members) - 1) / 2.0) * FORMATION_SIDE
            depth = row * FORMATION_DEPTH
            out.append((u, float(x) + lx * lateral - fx * depth,
                        float(y) + ly * lateral - fy * depth))
    return out


@dataclass
class Actuator:
    cmd: object                       # w3cmd.Cmd
    claims: object = None             # w3claim.Claims("strategy")
    prio: dict = field(default_factory=lambda: {"strategy": 50, "strategy_eco": 45, "strategy_hold": 58})
    counts: dict = field(default_factory=dict)
    last_point: dict = field(default_factory=dict)    # 单位地址 -> (订单, x, y, 墙钟)：同令不重下
    sleep_fn: object = None                            # 下令后等接没接的 sleep；run_brain 换成 steptime 的（记进「等待」）
    player_obj: int = 0                                # 我方玩家号（w3cmd.PlayerRef，0 号也为真）：取到一次就缓存
    feas_cache: dict = field(default_factory=dict)     # (建筑地址, 四字码) -> 原因码：这一拍规划开始时一批预取的（prefetch_feasible）

    def prefetch_feasible(self, pairs) -> int:
        """09-23 体检：建造规划逐个问「能不能下」4.6 万次 × 10.9 ms（占大脑时间 24%），每次都等游戏线程一次。
        规划开始时把这一拍可能要问的（生产建筑, 四字码）一批问完，规划里直接用；规划完 clear_feasible()。
        这一拍里还没下任何令（规划在前、下令在后），所以和原来逐个问看到的是同一个状态。"""
        self.feas_cache = {}
        many = getattr(self.cmd, "feasible_many", None)
        pairs = list(dict.fromkeys(pairs))
        if many is None or not pairs:
            return 0
        try:
            vs = many([(a, rawcode(c)) for a, c in pairs])
        except Exception:  # noqa: BLE001
            self._count("可行性预取·出错")
            return 0
        for p, v in zip(pairs, vs):
            if v is not None:
                self.feas_cache[p] = v
        self._count("可行性预取·批")
        return len(pairs)

    def clear_feasible(self) -> None:
        self.feas_cache = {}

    def player(self, any_unit_addr: int) -> int | None:
        """我方单位 -> 我方玩家号。整局不变，取到一次就不再取。⚠ 只能传**我方**单位（调用方都传 w.mine[0]）。"""
        if not self.player_obj:
            self.player_obj = self.cmd.player_of(any_unit_addr) or 0
        return self.player_obj or None

    def _count(self, k: str) -> None:
        self.counts[k] = self.counts.get(k, 0) + 1

    def _cmdlog(self, unit: int, order: int, how: str, ok: bool, target: int | None = None) -> None:
        """w3cmd 只包了六个下令出口（cmdlog.log 一令一行）；大脑直接走通道的四条 —— 采集、建造、集结点、学技能 ——
        在这里补一行同样格式的（09-18 Farsight 指令速度页要全量，不补的话采金/造房这些大头都数不到）。
        没登记模块名（单测）不写；记录出错吞掉，不影响下令。"""
        try:
            import modlog
            who = modlog.current_name()
            if who is None:
                return
            tail = (" 目标=0x%08X" % target) if target else ""
            modlog.append("cmdlog", "%s 0x%08X 0x%08X %s %s%s" % (who, unit or 0, order or 0, how, "ok" if ok else "失败", tail))
        except Exception:  # noqa: BLE001
            pass

    def _claim(self, addr: int, seconds: float, prio_name: str) -> bool:
        if self.claims is None:
            return True
        try:
            ok = self.claims.take(addr, seconds, prio=self.prio[prio_name])
        except Exception:  # noqa: BLE001
            ok = True                                  # 表打不开照常下令（和 drv:879 一样）
            self._count("仲裁表不可用")
        if not ok:
            self._count(f"抢不到指挥权·{prio_name}")
        return ok

    # ── 军队 ──
    def attack_move(self, units, x: float, y: float) -> int:
        return self._point(units, ORDER_ATTACK, x, y, "strategy", "攻击移动")

    def militia(self, unit, enabled: bool) -> bool:
        """单个农民/民兵变身；用战略租约，随后可无缝交给同一层的打野意图。"""
        order = ORDER_MILITIA if enabled else ORDER_MILITIA_OFF
        if not self._claim(unit.addr, 3.0, "strategy"):
            return False
        try:
            ok = bool(self.cmd.immediate_order(unit.addr, order))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"{'号召' if enabled else '解除'}民兵·{'下了' if ok else '没下成'}")
        return ok

    def recover(self, units, x, y):
        self.prio["recovery"] = 75  # 高于捡装备/补攻击，低于施法/保命/人工控制。
        owned = [u for u in units if self._claim(u.addr, 3.0, "recovery")]
        # 补给是明确的脱战移动；用 SMART 途中可被自动索敌转回攻击，又把恢复状态打断。
        # 恢复卷轴要求伤兵都在英雄身边；这里必须收拢到同一点，不能按行军队形摊开。
        return self._point(owned, ORDER_MOVE, x, y, "recovery", "整队恢复", formation=False)

    def move(self, units, x: float, y: float, hold: bool = False) -> int:
        # 09-22 晚：移动意图下移动令（ORDER_MOVE）走内核（见 MOVE_KERNEL 上面那段），原来是右键令 ORDER_SMART 走动作 41
        return self._point(units, ORDER_MOVE, x, y, "strategy_hold" if hold else "strategy", "移动")

    def _point(self, units, order: int, x: float, y: float, prio_name: str, label: str,
               formation: bool = True) -> int:
        """★ 09-17 实机 5 号：战术 2 秒一轮给全军重下同一个攻击移动，12 分钟 1620 条 —— 交战里每 2 秒被打断一次攻击。
        AMAI 下一次令就 SleepInCombat 等着。这里记住每个单位最后一条点令：同一种令、同一个点（400 内）、
        REPEAT_S 秒内下过、而且它没闲着（有订单或有任务）-> 不重下；闲着但已经站在点上 300 内的也不重下。"""
        n = 0
        now = time.monotonic()
        points = formation_points(units, x, y) if formation else [(u, float(x), float(y)) for u in units]
        todo = []
        for u, ux, uy in points:
            last = self.last_point.get(u.addr)
            if last is not None and last[0] == order and math.hypot(last[1] - ux, last[2] - uy) <= 400:
                idle = (getattr(u, "order", 0) or 0) == 0 and getattr(u, "has_task", False) is False
                # 09-22 晚：下了移动令、它还挂着攻击令 = 没接住（1-402 两个骑士照打塔 7 秒）—— MOVE_RETRY_S 后重下，不等 REPEAT_S
                stuck_attacking = order == ORDER_MOVE and getattr(u, "order", 0) == ORDER_ATTACK and now - last[3] >= MOVE_RETRY_S
                if stuck_attacking:
                    self._count(f"{label}·还在攻击·重下移动令")
                elif (not idle and now - last[3] < REPEAT_S) or (idle and math.hypot(u.x - ux, u.y - uy) <= 300):
                    self._count(f"{label}·同令不重下")
                    continue
            if not self._claim(u.addr, 3.0, prio_name):
                continue
            todo.append((u, ux, uy))
        # 09-23 体检：整队一批发（一批只等游戏线程一次；逐条发每条约 12 ms，这一段占大脑时间 26%）
        many = getattr(self.cmd, "move_many", None)
        if many is not None and len(todo) > 1:
            try:
                oks = list(many([(u.addr, ux, uy, order) for u, ux, uy in todo]))
            except Exception:  # noqa: BLE001
                oks = [False] * len(todo)
            results = [(ok, "W3P批") for ok in oks]
        else:
            results = [self._issue_point(u.addr, ux, uy, order) for u, ux, uy in todo]
        for (u, ux, uy), (ok, path) in zip(todo, results):
            self._count(f"{label}·{'下了' if ok else '没下成'}")
            self._count(f"点命令·{path}")
            if ok:
                self.last_point[u.addr] = (order, ux, uy, now)
            n += bool(ok)
        return n

    def _issue_point(self, addr: int, x: float, y: float, order: int) -> tuple[bool, str]:
        """一条点命令 -> (下成没有, 哪条路)。09-23 P3：W3P 的 point 命令在运行时里走的就是 JASS IssuePointOrder 那条路
        （上面 MOVE_KERNEL 那段比出来的好路），动作 41 那条不再有 —— 所有点命令一条路，cmdlog 方法名 move_to。"""
        try:
            return bool(self.cmd.move_to(addr, x, y, order)), "W3P"
        except Exception:  # noqa: BLE001
            return False, "W3P"

    def skirmish(self, it) -> int:
        self.prio['skirmish'] = 76  # 统一整队进退，仍低于施法/躲伤害/个人保命。
        owned = [u for u in it.units if self._claim(u.addr,3.0,'skirmish')]
        if it.kind == 'attack' and it.target is not None:
            return self.attack(owned,it.target,prio_name='skirmish')
        return self._point(owned,ORDER_ATTACK if it.kind == 'attack_move' else ORDER_MOVE,
                           it.x,it.y,'skirmish',it.why)

    def attack(self, units, target, prio_name="strategy", order: int = ORDER_SMART) -> int:
        """order=ORDER_SMART：右键（对敌人 = 攻击，对自己人/中立 = 跟着走）。
        order=ORDER_ATTACK：攻击令（A 键），打自己的兵、中立的小动物只能用它（Intent 的 force_attack）。"""
        n = 0
        now = time.monotonic()
        tkey = ("target" if order == ORDER_SMART else "force", getattr(target, "addr", 0))
        todo = []
        for u in units:
            last = self.last_point.get(u.addr)
            idle = (getattr(u, "order", 0) or 0) == 0 and getattr(u, "has_task", False) is False
            if last is not None and last[0] == tkey and not idle and now - last[3] < REPEAT_S:
                self._count("打目标·同令不重下")
                continue
            if not self._claim(u.addr, 3.0, prio_name):
                continue
            todo.append(u)
        many = getattr(self.cmd, "target_many", None)       # 09-23 体检：整队一批发（同 _point）
        if many is not None and len(todo) > 1:
            try:
                oks = list(many([(u.addr, target.addr, order) for u in todo]))
            except Exception:  # noqa: BLE001
                oks = [False] * len(todo)
        else:
            oks = []
            for u in todo:
                try:
                    oks.append(bool(self.cmd.target_order(u.addr, target.addr, order, target.x, target.y)))
                except Exception:  # noqa: BLE001
                    oks.append(False)
        for u, ok in zip(todo, oks):
            self._count(f"{'打目标' if order == ORDER_SMART else '强制攻击'}·{'下了' if ok else '没下成'}")
            if ok:
                self.last_point[u.addr] = (tkey, target.x, target.y, now)
            n += bool(ok)
        return n

    # ── 训练 / 研究 / 升本 / 英雄（接下 = 引擎当场扣钱）──
    def feasible(self, unit_addr: int, code: str) -> int | None:
        """引擎可行性原因码（W3P q_feasible）：0/220 能下；3 人口 8 缺金 9 缺木 32 训练队列 7 格满（09-18 实机）183 前置 185 复活中 221 没这项/建造中升级中。
        ⚠ 对工人造建筑恒 221，不能用。"""
        v = self.feas_cache.get((unit_addr, code))
        if v is not None:
            self._count("可行性·用预取")                   # 0 = 预取没生效（又回到逐个问）
            return v
        self._count("可行性·单问")
        try:
            return self.cmd.feasible(unit_addr, rawcode(code))
        except Exception:  # noqa: BLE001
            return None

    def train(self, building_addr: int, code: str) -> bool:
        if not self._claim(building_addr, 3.0, "strategy"):
            return False
        try:
            ok = bool(self.cmd.immediate_order(building_addr, rawcode(code)))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"训练/研究·{'接下' if ok else '被退'}")
        return ok

    def hero_points(self, hero_addr: int) -> int | None:
        try:
            return self.cmd.skill_points(hero_addr)
        except Exception:  # noqa: BLE001
            return None

    def learn(self, hero_addr: int, ability: str) -> bool:
        """W3P learn：运行时学完读回技能点，少了才算学会。没技能点就不发。"""
        if not self.hero_points(hero_addr):
            return False
        try:
            ok = bool(self.cmd.learn(hero_addr, rawcode(ability)))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"学技能·{'学会' if ok else '没学上'}")
        return ok

    def dead_heroes(self, any_unit_addr: int) -> list[int] | None:
        """死亡英雄表（W3P q_dead_heroes）：固定 7 槽，复活令接下时当场清槽 —— 剩下的就是还没开始复活的。读不到返回 None。"""
        try:
            player = self.player(any_unit_addr)
            if player is None:
                return None
            return self.cmd.dead_heroes(player)
        except Exception:  # noqa: BLE001
            return None

    def revive(self, altar_addr: int, dead_ptr: int) -> bool:
        """对祭坛下复活令（W3P revive，指定这个死英雄）；接下时引擎当场扣钱。"""
        if not self._claim(altar_addr, 3.0, "strategy"):
            return False
        try:
            ok = bool(self.cmd.revive(altar_addr, dead_ptr))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"复活·{'接下' if ok else '被退'}")
        return ok

    def revive_dead_hero(self, altar_addr: int, any_unit_addr: int) -> str:
        """复活死亡英雄表里第一个（W3P revive 不给目标）。表空 = 复活已在进行（接下时当场清槽）或没死的，不重下。"""
        try:
            if not self._claim(altar_addr, 3.0, "strategy"):
                return "抢不到祭坛"
            ok = self.cmd.revive(altar_addr)
            r = getattr(self.cmd, "last_receipt", None)
            if not ok and r is not None and not r.values[0]:
                return "复活进行中或没有死亡英雄"
            self._count(f"复活·{'接下' if ok else '被退'}")
            return "接下" if ok else "被退"
        except Exception as exc:  # noqa: BLE001
            return f"出错 {type(exc).__name__}"

    # ── 集结点（W3P rally）──
    def rally(self, building_addr: int, x: float, y: float) -> bool:
        if not self._claim(building_addr, 3.0, "strategy"):
            return False
        try:
            ok = bool(self.cmd.rally(building_addr, x, y))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"集结点·{'设了' if ok else '没设上'}")
        return ok

    # ── 造建筑（下令、判接没接）──
    def build(self, worker_addr: int, code: str, x: float, y: float, settle_s: float = 0.25, prev_order: int | None = None) -> str:
        """返回 '接下' / '没发出去' / '被拒' / '抢不到'。接下 = 工人当前订单 == 建筑 rawcode（不是"订单变了"）。
        ★ 09-22 晚 1-413：工人就在落点旁边时，0.25 秒（3 倍速约 0.75 游戏秒）里已经放下地基、订单变成施工令 0x000D001B ——
          原来被判「被拒」、拉黑落点、换点重下，把刚开工的工人拽走，留下一座没人盖的地基（23:12:34 连判 4 次「失败」，
          (-4864,3136) 那座农场就是这么来的）。施工令也算接下 —— 前提是下令之前它不在施工。
        ★ 09-23 P3：这条判据搬进运行时（W3P build 同一帧读回工人下令前/后的订单），不用再 sleep 0.25 秒回头看；
          settle_s / prev_order 保留签名，不再使用。"""
        if not self._claim(worker_addr, 5.0, "strategy_eco"):
            return "抢不到"
        try:
            ok = bool(self.cmd.build(worker_addr, rawcode(code), x, y))
        except Exception:  # noqa: BLE001
            self._count("建造·没发出去")
            return "没发出去"
        r = getattr(self.cmd, "last_receipt", None)
        if not ok and r is not None and r.status not in (1,):          # 不是引擎拒绝（单位没了 / 仲裁 / 通道）
            self._count("建造·没发出去")
            return "没发出去"
        if ok and r is not None and r.order_after in BUILD_STARTED_ORDERS:
            self._count("建造·接下·已开工")                  # 0 = 没碰到过「下令同一帧就开工」（或者判据又没生效）
        self._count(f"建造·{'接下' if ok else '被拒'}")
        if not ok and r is not None:
            # 09-24 D15：被拒分两种 —— 引擎没给出建造订单（engine_return 0：落点被占/不能盖/缺前置）、
            # 订单造出来了但工人订单没换上（提交后被引擎撤掉：多半买不起或工人正忙）。按建筑分开数，体检报告里看比例
            why = "工厂没给订单" if not getattr(r, "engine_return", 0) else "订单没换上"
            self._count(f"建造·被拒·{code}·{why}")
            # 09-24 批（14 局）：344 次被拒全是"订单没换上"（农场 200、秘法圣殿 108、哨塔 36）。再分：工人原来在干什么
            # （空闲 / 采集 0x000D0032 一族 / 别的）—— 分清"工人在矿里/背着货"和"提交时引擎再验落点或资源"
            before = getattr(r, "order_before", 0) or 0
            self._count(f"建造·被拒·原订单·{before:#x}")
        return "接下" if ok else "被拒"

    # ── 采集（只给没任务的工人派，给有任务的重下会取消采集周期）──
    def gather(self, worker, target, order: int = ORDER_HARVEST) -> bool:
        """★ 09-17 实机（5 号实例 pid 15908）：动作 28 对采金令**返回 True 但农民一步不动**；JASS IssueTargetOrder 那条路
        9 秒金子 100 -> 150。09-23 起 W3P target 命令在运行时里走的就是这条路，返回接下才算。"""
        if not self._claim(worker.addr, 3.0, "strategy_eco"):
            return False
        try:
            ok = bool(self.cmd.target_order(worker.addr, target.addr, order))
        except Exception:  # noqa: BLE001
            ok = False
        self._count(f"派采集·{'下了' if ok else '没下成'}")
        return ok

    def scan_trees(self, x: float, y: float, limit: int = 384) -> list[dict]:
        """离 (x, y) 最近的活树（W3P 树块，运行时 1 秒刷一次全图的树）。读不到返回 []。"""
        try:
            trees = [t for t in self.cmd.world.trees() if t.alive]
        except Exception:  # noqa: BLE001
            self._count("找树·出错")
            return []
        trees.sort(key=lambda t: (t.x - x) ** 2 + (t.y - y) ** 2)
        out = [dict(addr=t.addr, type=t.type, x=t.x, y=t.y, hp=t.life) for t in trees[:limit]]
        self._count("找树·读到" if out else "找树·零棵")
        return out

    def end_game(self) -> bool:
        """运行时动作 22：进程内结束本实例 War3（多开局末，零 UAC）。"""
        try:
            return bool(self.cmd.action(22, 0))
        except Exception:  # noqa: BLE001
            return False


# ── 落点候选（第一版简化旧 PowerShell AI 的落点规则：记忆点 + 大厅周围环，吸附 32，避开已有建筑和金矿的方框、避开大厅到矿的采矿通道）──
# ★ 09-18 高手落点 pro（brain/data/pro_spots.json，tools/make_pro_spots.py 从 Yumiko 的 Echo Isles 录像抽）排最前：
#   只要求不和已有建筑重叠（允许贴边），不查金矿方框和采矿通道 —— 高手在同一张图、同一个出生点实际盖成过；
#   环形候选的「再空 64」会把贴着大厅的农场（大厅半边 256 + 农场半边 64 = 320，正好贴边）整个筛掉，那正是高手的摆法。
#   真放不下引擎会退（被拒 -> runtime.bad_spots 拉黑，不会反复试）。
def spot_candidates(code: str, hall, buildings, mines, known: list | None = None, radii=(420, 560, 700, 840, 980, 1120, 1300),
                    angles: int = 16, pro: list | None = None) -> list[tuple[float, float]]:
    half = footprint(code) / 2.0
    out: list[tuple[float, float]] = []

    def free(px: float, py: float) -> bool:
        return all(not (abs(b.x - px) < half + footprint(b.type) / 2.0 and abs(b.y - py) < half + footprint(b.type) / 2.0)
                   for b in buildings)

    def clear(px: float, py: float) -> bool:
        for b in buildings:
            gap = half + footprint(b.type) / 2.0 + 64
            if abs(b.x - px) < gap and abs(b.y - py) < gap:
                return False
        for m in mines:
            gap = half + 256
            if abs(m.x - px) < gap and abs(m.y - py) < gap:
                return False
            # 采矿通道：点到 大厅-矿 线段的距离 < 300 不放
            if _seg_dist(px, py, hall.x, hall.y, m.x, m.y) < 300 and math.hypot(m.x - hall.x, m.y - hall.y) < 1500:
                return False
        return True

    seen = set()
    for kx, ky in pro or []:
        p = (snap32(kx), snap32(ky))
        if p not in seen and free(*p):
            seen.add(p)
            out.append(p)
    for kx, ky in known or []:
        p = (snap32(kx), snap32(ky))
        if p not in seen and clear(*p):
            seen.add(p)
            out.append(p)
    ring = []
    for r in radii:
        for k in range(angles):
            a = 2 * math.pi * k / angles
            p = (snap32(hall.x + r * math.cos(a)), snap32(hall.y + r * math.sin(a)))
            if p not in seen and clear(*p):
                seen.add(p)
                ring.append(p)
    return out + ring


# ── 伐木场贴着树林盖（09-22 伐木场尽量建在离树木最近的位置）──
# 最近 20 局（1 号实例）伐木场全盖在主基地东南 (-4448,2656)/(-4576,2720)：离最近的树 805~864，600 以内一棵树都没有，
# 而树林在西南/西/北边。高手录像点 (-5792,2592) 开局被树挡住（引擎退单、拉黑），回落到大厅一圈候选按「半径 -> 角度」排，第一个就是正东。
# 现在：离大厅 LUMBER_TREE_R 内每棵树四周贴边的 8 个点（和任何树的占地不重叠），按「离大厅 - 附近树的棵数 × LUMBER_DENSE_W」排
# —— 贴着一大片林子、又不太远的排最前；孤零零一棵树旁边的排后面（砍两下就没了）。
LUMBER_TREE_R = 1400.0      # 只看离大厅这么远以内的树（再远伐木场护不住，农民也跑得远）
LUMBER_DENSE_R = 500.0      # 数这么远以内有几棵树 = 这片林子够砍多久
LUMBER_DENSE_W = 60.0       # 每多一棵（封顶 LUMBER_DENSE_CAP）当作离大厅近这么多
LUMBER_DENSE_CAP = 12
LUMBER_EDGE_GAP = 16        # 贴边再留这么宽的缝


def tree_edge_spots(code: str, hall, trees, limit: int = 40) -> list[tuple[float, float]]:
    half = footprint(code) / 2.0
    th = TREE_FOOT / 2.0
    live = [(float(t["x"]), float(t["y"])) for t in trees
            if t.get("hp", 1) > 0 and math.hypot(t["x"] - hall.x, t["y"] - hall.y) <= LUMBER_TREE_R]
    g = half + th + LUMBER_EDGE_GAP
    seen, scored = set(), []
    for tx, ty in live:
        for ox, oy in ((g, 0), (-g, 0), (0, g), (0, -g), (g, g), (g, -g), (-g, g), (-g, -g)):
            p = (snap32(tx + ox), snap32(ty + oy))
            if p in seen:
                continue
            seen.add(p)
            if any(abs(x - p[0]) < half + th and abs(y - p[1]) < half + th for x, y in live):
                continue                                     # 压着树：放不下
            dense = sum(1 for x, y in live if math.hypot(x - p[0], y - p[1]) <= LUMBER_DENSE_R)
            dh = math.hypot(p[0] - hall.x, p[1] - hall.y)
            scored.append((dh - LUMBER_DENSE_W * min(dense, LUMBER_DENSE_CAP), p))
    scored.sort()
    return [p for _s, p in scored[:limit]]


# ── 通路检查（09-18 大法师出生后多次被早先策略造的建筑堵在家里出不去）──
# 高手落点贴着大厅、祭坛摆，祭坛又挨着西边的树；再加上回落的点，祭坛出口被建筑 + 树一起封死。只看建筑查不出来（最近 6 局都没被建筑单独围死），
# 所以把树（大厅附近扫到的，runtime.trees）和金矿也算进去：32 格上从「离大厅 EXIT_R 以外」往里灌，看出兵建筑的外沿格子灌不灌得到。
EXIT_GRID = 32
EXIT_R = 1500.0
TREE_FOOT = 128           # 树的通行占地按 4×4 格（128）算：宁可多挡一点（少一个落点）也别漏掉堵门
UNIT_CELLS = 2            # 找路的单位占 2×2 格（英雄碰撞半径 32 = 直径 64）：一格宽（32）的缝走不过去。
                          # 第一版按 1 格找路，第 15 局祭坛和铁匠铺之间只剩 32 的缝，照样算「出得去」


def blocked_cells(items) -> set:
    """[(占地边长, x, y)] -> 被占的 32 格（格子 (i, j) = [i*32, i*32+32)）。"""
    g = EXIT_GRID
    cells = set()
    for size, x, y in items:
        h = size / 2.0
        for i in range(int(math.floor((x - h) / g)), int(math.ceil((x + h) / g))):
            for j in range(int(math.floor((y - h) / g)), int(math.ceil((y + h) / g))):
                cells.add((i, j))
    return cells


POCKET_R = 1200.0         # 死角只在大厅这么远以内数（再远是野外，不归基地布局管）
POCKET_MAX = 12           # 放下以后「原来连着外面、现在连不上」的空地锚点超过这么多格 = 隔出了死角
# ★ 09-18 第 20 局：大法师在兵营北边树林边 (-4732,3408) 困了一分多钟 —— 开局第 7 秒 Yumiko 的农场点 (-4864,3008) 把大厅和兵营之间
#   160 宽的通道压成 32，兵营北边到树林也只剩一条缝，大厅北面整片采矿区成了死角（Yumiko 是故意堵口防冲家，她的英雄从不进去；
#   我们的单位会走进去出不来）。只查「出兵建筑有没有出口」查不出这种：大厅南面照样有门


def anchor_blocked(cells: set) -> set:
    """锚点放在这里单位会撞上的那些锚点（单位占 [i, i+UNIT_CELLS) × [j, j+UNIT_CELLS) 格）。"""
    u = UNIT_CELLS
    return {(i - di, j - dj) for i, j in cells for di in range(u) for dj in range(u)}


def reachable_from_outside(cells: set, cx: float, cy: float, radius: float = EXIT_R) -> set:
    """从离 (cx,cy) radius 以外的一圈往里灌（四连通），返回灌得到的「单位锚点」（单位占 [i, i+UNIT_CELLS) × [j, j+UNIT_CELLS) 格）。"""
    g = EXIT_GRID
    cells = anchor_blocked(cells)                                                    # 锚点放在这里单位会撞上
    r_cells = int(radius / g) + 2
    ci, cj = int(math.floor(cx / g)), int(math.floor(cy / g))
    inside = lambda i, j: (i - ci) ** 2 + (j - cj) ** 2 <= r_cells * r_cells   # noqa: E731
    edge = (r_cells - 1) ** 2
    q = [(i, j) for i in range(ci - r_cells, ci + r_cells + 1) for j in range(cj - r_cells, cj + r_cells + 1)
         if edge <= (i - ci) ** 2 + (j - cj) ** 2 <= r_cells * r_cells and (i, j) not in cells]
    seen = set(q)
    k = 0
    while k < len(q):
        i, j = q[k]
        k += 1
        for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
            if n not in seen and n not in cells and inside(*n):
                seen.add(n)
                q.append(n)
    return seen


def door_cells(size: float, x: float, y: float) -> list:
    """紧贴建筑外沿的一圈单位锚点（单位从这里出来；锚点口径同 reachable_from_outside）。"""
    g = EXIT_GRID
    h = size / 2.0
    i0, i1 = int(math.floor((x - h) / g)) - UNIT_CELLS, int(math.ceil((x + h) / g))
    j0, j1 = int(math.floor((y - h) / g)) - UNIT_CELLS, int(math.ceil((y + h) / g))
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1) if i in (i0, i1) or j in (j0, j1)]


def seals_exit(items: list, doors: list, cand: tuple, cx: float, cy: float, before: set | None = None,
               reach0: set | None = None) -> list:
    """放下 cand = (占地, x, y) 之后：doors [(名字, 占地, x, y)] 里原来走得出去、现在走不出去的；
    外加「隔出死角 N 格」（大厅 POCKET_R 内原来连着外面的空地，现在连不上的超过 POCKET_MAX 格）。空 = 这个点没问题。
    before / reach0：不放 cand 时走得出去的门、灌得到的锚点（同一轮多个候选共用，省一次灌水）。"""
    if reach0 is None:
        reach0 = reachable_from_outside(blocked_cells(items), cx, cy)
    if before is None:
        before = {d[0] for d in doors if any(c in reach0 for c in door_cells(*d[1:]))}
    cells1 = blocked_cells(items + [cand])
    reach = reachable_from_outside(cells1, cx, cy)
    out = [d[0] for d in doors if d[0] in before and not any(c in reach for c in door_cells(*d[1:]))]
    occ1 = anchor_blocked(cells1)
    g = EXIT_GRID
    r2 = (POCKET_R / g) ** 2
    ci, cj = cx / g, cy / g
    lost = sum(1 for a in reach0 if a not in reach and a not in occ1 and (a[0] - ci) ** 2 + (a[1] - cj) ** 2 <= r2)
    if lost > POCKET_MAX:
        out.append(f"隔出死角 {lost} 格")
    return out


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    if L <= 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


MINE_CLEAR = 384        # 大厅离金矿的最小切比雪夫距离：再近引擎就退「不能建在离金矿过近的地方」
                        # （09-18 实机：候选点最近到 128，一局「建造·被拒」301 次、「没接下·htow」66 次，整局开不出分矿）


def expansion_candidates(mine, halls_all, known: list | None = None) -> list[tuple[float, float]]:
    """人族/兽族开矿：记忆点优先；再以矿为中心 128 网格、切比雪夫 >= MINE_CLEAR、欧氏 <= 900、离任何大厅曼哈顿 >= 1400，按 |切比雪夫-640| 排。"""
    out = [(snap32(x), snap32(y)) for x, y in (known or [])]
    grid = []
    for i in range(-7, 8):
        for j in range(-7, 8):
            dx, dy = i * 128, j * 128
            cheb = max(abs(dx), abs(dy))
            if cheb < MINE_CLEAR or math.hypot(dx, dy) > 900:
                continue
            px, py = snap32(mine.x + dx), snap32(mine.y + dy)
            if any(abs(h.x - px) + abs(h.y - py) < 1400 for h in halls_all):
                continue
            grid.append((abs(cheb - 640), (px, py)))
    grid.sort(key=lambda g: g[0])
    return out + [p for _, p in grid if p not in out]

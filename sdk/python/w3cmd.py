"""w3cmd.py —— 老接口 Cmd 的 W3P 兼容层：同名方法、同样的返回形状，底下全走运行时的语义命令和推送世界块。

    from w3cmd import Cmd
    cmd = Cmd(pid)
    cmd.move_to(unit_addr, x, y)            # 语义命令 point（一帧内回执）
    cmd.current_order(unit_addr)            # 世界块里的订单（不再去游戏里读内存）

★ P3（09-23）：这里原来是一层直接调引擎的封装，只对一个游戏版本有效，换个版本的 War3 全部失效。现在：
    下令     -> W3P 语义命令（point / target / immediate / use_item / buy），回执在 self.last_receipt
    读状态   -> W3P 世界块（订单、任务、等级、经验、蓝、技能/冷却、buff、背包、地上物品）
    查询     -> W3P 查询（科技数 q_tech、电脑队长 q_captain）
  没有等价物、也没有外部调用方的那几个删掉了 ——
  要它们就在运行时里加一个 W3P 查询，而不是在开放层里读内存。

★ 地址作单位身份：老调用方传的都是单位地址。这里在最近一份世界快照里按地址找到单位，
  把句柄一起带上（运行时核对句柄，地址被复用时拒绝 = bad_unit），找不到才传裸地址。
"""

from __future__ import annotations

import os
import struct
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import w3fast  # noqa: E402
import w3world  # noqa: E402
from w3fast import command as _C  # noqa: E402

# 订单号（data/order-ids.txt）
ORDER_GETITEM = 0x000D000D
ORDER_SMART = 0x000D0003
ORDER_STOP = 0x000D0004
ORDER_HARVEST = 0x000D0032
ORDER_ATTACK = 0x000D000F

NO_HANDLE = (0xFFFFFFFF, 0xFFFFFFFF)
# current_task 的 cls 字段：任务是「对着一个单位」还是「对着一点」（调用方拿它分这两种）
TASK_TARGET = "task_target"
TASK_POINT = "task_point"

SNAP_TTL = 0.025          # 世界快照缓存多久（运行时默认 50ms 发布一次）；下过令就作废（读自己刚写的）


def fourcc(v: int) -> str:
    """内存里的四字码（小端 DWORD）-> 'AHwe'。"""
    return struct.pack("<I", v & 0xFFFFFFFF)[::-1].decode("latin-1", "replace")


def rawcode(code: str) -> int:
    """'AHwe' -> 0x41487765（传给引擎的四字码）。"""
    return struct.unpack(">I", code.encode("latin-1"))[0]


class PlayerRef(int):
    """player_of 的返回：玩家号。永远为真 —— 老调用方写 `player_of(u) or 0` / `if player:`，0 号玩家不能被当成"读不到"。"""

    def __bool__(self) -> bool:
        return True


class AbilityRef(tuple):
    """abilities / find_ability 给的 "addr"：(单位地址, 技能码)。cooldown_left 拿它回世界块里查冷却。"""


class Cmd:
    def __init__(self, pid: int, player: int | None = None):
        self.pid = pid
        self.player = player
        self.lane = w3fast.shared_lane(pid) if player is None else w3fast.shared_lane(pid, player=player)
        self.world = w3world.shared_world(pid)
        self._init_state()

    @classmethod
    def with_backends(cls, lane, world, pid: int = 0, player: int | None = None) -> "Cmd":
        """测试 / 嵌入用：lane 要有 commands(list)->[Receipt]，world 要有 read()->WorldState。"""
        c = cls.__new__(cls)
        c.pid, c.lane, c.world = pid, lane, world
        c.player = player
        c._init_state()
        return c

    def _init_state(self):
        self._snap = None
        self._snap_t = 0.0
        self._items_by_addr = {}
        self._trees, self._trees_t = {}, 0.0
        self.last_receipt = None
        self.last_receipts = []
        self.last_cast_path = "W3P"
        self.items_scan_error: str | None = None
        self.items_dead = 0
        self.fog_fallbacks = 0         # 目标在迷雾里、改成攻击移动的次数（target_order）

    # ── 世界快照 ──────────────────────────────────────────────
    def snapshot(self, fresh: bool = False):
        now = time.monotonic()
        if fresh or self._snap is None or now - self._snap_t > SNAP_TTL:
            s = self.world.read()
            if s is not None:
                if self.player is not None:
                    s = replace(s, me=self.player)
                self._snap, self._snap_t = s, now
                self._items_by_addr = {i.addr: i for i in s.items}
        return self._snap

    def snapshot_age_ms(self) -> float | None:
        """最新一份世界块发布到现在过了多少毫秒：世界块头的 realMs 是运行时用**没被倍速钩子改过**的 GetTickCount 记的，
        和本进程的 GetTickCount 同一个钟，可以直接相减（老快照 w3state 的时间戳被倍速改过、不能比，所以决策日志的快照年龄一直是 0）。"""
        s = self.world.read()
        if s is None or not s.real_ms:
            return None
        import ctypes
        d = (ctypes.windll.kernel32.GetTickCount() - s.real_ms) & 0xFFFFFFFF
        return float(d) if d < 60000 else None

    def _unit(self, addr: int):
        s = self.snapshot()
        return s.unit_by_addr(addr) if (s is not None and addr) else None

    def _ref(self, addr):
        """地址 -> 带句柄的对象（单位 / 地上物品 / 树），找不到就是裸地址。"""
        if not isinstance(addr, int) or not addr:
            return addr
        u = self._unit(addr)
        if u is not None:
            return u
        it = self._items_by_addr.get(addr)
        if it is not None:
            return it
        now = time.monotonic()
        if now - self._trees_t > 2.0 and hasattr(self.world, "trees"):
            try:
                self._trees = {t.addr: t for t in self.world.trees()}
            except Exception:  # noqa: BLE001
                self._trees = {}
            self._trees_t = now
        return self._trees.get(addr, addr)

    def _do(self, op: str, unit=None, **kw) -> w3fast.Receipt:
        r = self.lane.commands([_C(op, self._ref(unit) if unit is not None else None, **kw)])[0]
        self.last_receipt = r
        self._snap = None                      # 下过令：下一次读状态要新的一份
        return r

    # ── 下令（名字和老接口一样；返回 True = 引擎接了，原因看 last_receipt）─────────
    def context(self, unit: int) -> int | None:
        """兼容老接口：W3P 下令用不上它。单位在世界里返回 0（老调用方拿 None 当"通道坏了"），不在返回 None。"""
        return 0 if self._unit(unit) is not None else None

    def target_order(self, unit: int, target: int, order: int, x: float = 0.0, y: float = 0.0) -> bool:
        """带目标的命令（采集 / 攻击(右键) / 捡物品 / 修理 / 目标施法）。

        ★ 09-23 实机（参考大脑 20 号，第 2 局 15 分钟）：W3P 对看不见的目标一律拒（verdict 1001），而大脑看的是全图快照，
          追迷雾里的敌人一分钟被拒 342 次、兵原地不动。老通道（动作 28）右键同时带目标和坐标，目标无效时引擎回落到那个点 ——
          兵照样往那边走。这里保留老语义：右键/攻击令因为看不见被拒时，改成对它的位置**攻击移动**（fog_fallbacks 计数）。
          新接口（openwar3.Game）不这么做：它要么公平模式看不见，要么由 bot 自己决定怎么办。"""
        tref = self._ref(target)
        r = self._do("target", unit, code=order, target=tref)
        if r.ok or r.verdict != 1001 or order not in (ORDER_SMART, ORDER_ATTACK):
            return r.ok
        tx, ty = (tref.x, tref.y) if hasattr(tref, "x") else (x, y)
        if not (tx or ty):
            return False
        r2 = self._do("point", unit, code=ORDER_ATTACK, x=float(tx), y=float(ty))
        if r2.ok:
            self.fog_fallbacks += 1
            r2.note = "目标在迷雾里：改成攻击移动到它的位置"
        return r2.ok

    def get_item(self, hero: int, item: int, x: float = 0.0, y: float = 0.0) -> bool:
        """捡地上的东西 —— 右键（09-07 实测 getitem 0x000D000D 接了不捡，smart 才捡）。"""
        return self.target_order(hero, item, ORDER_SMART, x, y)

    def move_to(self, unit: int, x: float, y: float, order: int = ORDER_SMART) -> bool:
        """走到一点（默认右键；0x000D0012 = 移动、0x000D000F = 攻击移动）。"""
        return self._do("point", unit, code=order, x=float(x), y=float(y)).ok

    def immediate_order(self, unit: int, order: int) -> bool:
        """无目标命令：停止/待命/无目标施法/训练(四字码)/研究/升级。"""
        return self._do("immediate", unit, code=order).ok

    def stop(self, unit: int) -> bool:
        return self.immediate_order(unit, ORDER_STOP)

    def cast_target(self, unit: int, order: int, target: int, x: float = 0.0, y: float = 0.0) -> bool:
        """目标施法（风暴之锤、圣光术）。被拒就是被拒（原因在 last_receipt.verdict，1001 = 目标看不见）。"""
        self.last_cast_path = "W3P目标"
        return self._do("target", unit, code=order, target=self._ref(target)).ok

    def cast_point(self, unit: int, order: int, x: float, y: float) -> bool:
        """点施法（暴风雪、烈焰风暴）：运行时走 JASS IssuePointOrder 那条路（演练台 3/3 进冷却）。"""
        self.last_cast_path = "W3P点"
        return self._do("point", unit, code=order, x=float(x), y=float(y)).ok

    def cast_point_ctx(self, unit: int, order: int, x: float, y: float) -> bool:
        """老名字，和 cast_point 同一条路。"""
        self.last_cast_path = "W3P点"
        return self._do("point", unit, code=order, x=float(x), y=float(y)).ok

    USESLOT1 = 0x000D0028

    def use_item(self, unit: int, order: int, x: float | None = None, y: float | None = None,
                 target: int = 0) -> bool:
        """用背包里的物品：order = USESLOT1 + 格号。target 优先，其次对点，都没有是无目标。"""
        slot = order - self.USESLOT1
        if not 0 <= slot < 6:
            return False
        if target:
            return self._do("use_item", unit, extra=slot, mode=2, target=self._ref(target)).ok
        if x is not None and y is not None:
            return self._do("use_item", unit, extra=slot, mode=1, x=float(x), y=float(y)).ok
        return self._do("use_item", unit, extra=slot, mode=0).ok

    def buy_item(self, shop: int, order: int) -> bool:
        """从商店买一件（order = 物品码整数）。东西给商店旁边的英雄；89 = 还没到货、221 = 这家不卖。"""
        return self._do("buy", shop, code=order).ok

    def build(self, worker: int, order: int, x: float, y: float) -> bool:
        """工人造建筑（order = 建筑四字码整数）。⚠ 接下 ≠ 盖成：工人走到落点被挡住还会放弃，要看地基出没出现。"""
        return self._do("build", worker, code=order, x=float(x), y=float(y)).ok

    def learn(self, hero: int, order: int) -> bool:
        """英雄学技能（order = 技能四字码整数）。回执 values[0]/[1] = 学之前/之后的技能点。"""
        return self._do("learn", hero, code=order).ok

    def rally(self, building: int, x: float | None = None, y: float | None = None, target: int = 0) -> bool:
        """集结点：对点，或对一个单位（target）。"""
        if target:
            return self._do("rally", building, mode=2, target=self._ref(target)).ok
        return self._do("rally", building, mode=0, x=float(x), y=float(y)).ok

    def revive(self, altar: int, hero: int = 0) -> bool:
        """祭坛复活：hero 给了复活它，否则死亡英雄表里第一个。回执 values[0] = 死英雄对象（0 = 表空：没有死的 / 复活已在进行），
        values[1] = 它的类型码（被拒时也带回来，人口/钱不够时好说原因）。"""
        return self._do("revive", altar, target=self._ref(hero) if hero else None).ok

    def dead_heroes(self, player) -> list[int] | None:
        """死亡英雄表里还没开始复活的（对象地址）；复活令接下时引擎当场清槽。读不到 None。"""
        r = self._do("q_dead_heroes", extra=int(player))
        return [v for v in r.values[:7] if v] if r.ok else None

    def feasible(self, unit: int, code: int) -> int | None:
        """引擎可行性原因码（0/220 能下，3 人口 8 金 9 木 32 队列满 183 缺前置 185 祭坛复活中 221 没有/在建）。"""
        r = self._do("q_feasible", unit, code=code)
        return r.verdict if r.ok else None

    def skill_points(self, hero: int) -> int | None:
        u = self._unit(hero)
        return u.skill_points if (u is not None and u.is_hero) else None

    # ── 运行时控制动作（结束这一局 / 问题快捷键）：走控制通道的动作号，是运行时协议，不是引擎地址 ──
    @property
    def ctl(self):
        if getattr(self, "_ctl", None) is None:
            from w3chan import Channel
            self._ctl = Channel(self.pid)
        return self._ctl

    def action(self, number: int, arg: int = 0, timeout: float = 5.0) -> bool:
        return bool(self.ctl.action(number, arg, timeout=timeout))

    # ── 读状态（世界块）────────────────────────────────────────
    def current_order(self, unit: int) -> dict | None:
        """{order, target(句柄对/None), base_target(None), x, y, unit}；订单槽空 / 不在世界里返回 None。"""
        u = self._unit(unit)
        if u is None or u.order is None or not u.order:
            return None
        return {"order": u.order, "target": u.order_target, "base_target": None,
                "x": u.order_x, "y": u.order_y, "unit": u.addr, "addr": u.addr}

    def captain(self, order: dict | None) -> dict | None:
        """这个敌兵跟着的电脑队长：{x, y, goal_x, goal_y, home_x, home_y, handle}；不是跟着队长返回 None。"""
        if not order or not order.get("target") or not order.get("unit"):
            return None
        r = self._do("q_captain", order["unit"])
        if not r.ok:
            return None
        v = [r.value_float(i) for i in range(6)]
        return {"x": v[0], "y": v[1], "goal_x": v[2], "goal_y": v[3], "home_x": v[4], "home_y": v[5],
                "handle": order["target"]}

    def current_task(self, unit: int) -> dict | None:
        """{cls, id, target, x, y}：cls = TASK_TARGET（对着一个单位）/ TASK_POINT（对着一点）/ None（别的任务）。"""
        u = self._unit(unit)
        if u is None or not u.has_task:
            return None
        if u.task_target is not None:
            return {"cls": TASK_TARGET, "id": None, "target": u.task_target, "x": None, "y": None}
        if u.task_x is not None:
            return {"cls": TASK_POINT, "id": None, "target": None, "x": u.task_x, "y": u.task_y}
        return {"cls": None, "id": None, "target": None, "x": None, "y": None}

    def has_task(self, unit: int) -> bool:
        u = self._unit(unit)
        return bool(u is not None and u.has_task)

    def hero_level(self, unit: int) -> int | None:
        u = self._unit(unit)
        return u.level if (u is not None and u.is_hero and 0 < u.level <= 30) else None

    def hero_xp(self, unit: int) -> int | None:
        u = self._unit(unit)
        return u.xp if (u is not None and u.is_hero) else None

    def unit_state(self, unit: int, index: int) -> float | None:
        """0 血 1 最大血 2 魔 3 最大魔。"""
        u = self._unit(unit)
        if u is None:
            return None
        return (u.hp, u.hp_max, u.mana, u.mana_max)[index] if 0 <= index <= 3 else None

    def mana(self, unit: int) -> float | None:
        return self.unit_state(unit, 2)

    def abilities(self, unit: int, limit: int = 32) -> list[dict]:
        """技能 + buff（buff 码 B 开头，level 0）。没有细节（世界块只给英雄/玩家单位/野怪前若干个）返回 []。"""
        u = self._unit(unit)
        if u is None or not u.has_detail:
            return []
        out = [{"code": a.code, "level": a.level, "flags": a.flags, "addr": AbilityRef((u.addr, a.code))}
               for a in u.abilities]
        out += [{"code": b, "level": 0, "flags": 0, "addr": AbilityRef((u.addr, b))} for b in u.buffs]
        return out[:limit]

    def find_ability(self, unit: int, code: str) -> dict | None:
        """{addr, code, level, flags}（flags & 0x200 = 冷却中）；没有这个技能 {"level": 0}；读不到 None。"""
        u = self._unit(unit)
        if u is None or not u.has_detail:
            return None
        a = u.ability(code)
        if a is None:
            return {"level": 0}
        return {"code": a.code, "level": a.level, "flags": a.flags, "addr": AbilityRef((u.addr, a.code))}

    def ability_level(self, unit: int, code: str) -> int | None:
        got = self.find_ability(unit, code)
        return None if got is None else got["level"]

    def cooldown_left(self, ability) -> float | None:
        """剩余冷却（游戏秒）。参数是 abilities / find_ability 给的 "addr"。"""
        if not isinstance(ability, tuple) or len(ability) != 2:
            return None
        u = self._unit(ability[0])
        a = u.ability(ability[1]) if (u is not None and u.has_detail) else None
        return a.cooldown if a is not None else None

    def inventory(self, unit: int) -> list[str | None] | None:
        """背包 6 格（空格 None）；没有背包 / 没有细节返回 None。"""
        u = self._unit(unit)
        if u is None or not u.has_detail or u.ability("AInv") is None:
            return None
        return list(u.items)

    def player_of(self, unit: int):
        """单位所属玩家号（PlayerRef，永远为真）；读不到 None。给 tech_count 用。"""
        u = self._unit(unit)
        return PlayerRef(u.owner) if u is not None else None

    def tech_count(self, player, code: str) -> int | None:
        """玩家的研究等级 / 建完的建筑数（升级链算在内，在建的不算）。player = player_of 的结果或玩家号。"""
        if player is None:
            return None
        r = self._do("q_tech", code=rawcode(code), extra=int(player))
        return r.values[0] if r.ok else None

    def item_owned(self, item: int) -> bool | None:
        """在地上 -> False；不在地上的物品世界块里没有 -> None（不知道）。"""
        self.snapshot()
        return False if item in self._items_by_addr else None

    def items_on_ground(self) -> list[tuple[int, str, float, float]]:
        """地上的物品 (地址, 类型码, x, y)；血量 <= 0 的死物品（用掉的书）不算，件数在 items_dead。"""
        s = self.snapshot(fresh=True)
        if s is None:
            self.items_scan_error = "世界块读不到"
            return []
        self.items_scan_error = None
        dead = [i for i in s.items if i.life <= 0.0]
        self.items_dead = len(dead)
        return [(i.addr, i.type, i.x, i.y) for i in s.items if i.life > 0.0]


# ── 命令来源日志 + 统一仲裁（下令出口包一层）─────────────────────────────
# 每条令记一行：来源 单位 订单 方法 结果（cmdlog.log，modlog.read("cmdlog") 读得回来），cmdclash.py 数「谁和谁在抢」。
# 出口先看租约表：这个单位被**别的模块**以 >= 本模块默认优先级占着 -> 不下，返回 False，记「让出」。
# ★ 这是观测 + 仲裁，不是功能：包装原样返回、记录里任何异常都吞掉。test_cmdlog / test_cmd_arbiter 守着。
import inspect as _inspect  # noqa: E402

import modlog as _modlog  # noqa: E402

ORDER_EXITS = ("target_order", "move_to", "immediate_order",
               "cast_target", "cast_point", "cast_point_ctx",
               "use_item", "buy_item", "build", "learn", "rally", "revive")

_MODULE_OF = {"mod_combat": "combat", "mod_worker": "economy", "mod_scavenge": "scavenge"}
_claims_by_module = {}


def _arbiter_refuses(who, unit):
    """-> None 放行；或 (占着的模块 id, 优先级) 表示让出。任何异常都放行（仲裁坏了不能把模块按死）。"""
    mod = _MODULE_OF.get(who or "")
    if not mod or not unit:
        return None
    try:
        import w3claim
        cl = _claims_by_module.get(mod)
        if cl is None:
            cl = _claims_by_module[mod] = w3claim.Claims(mod)
        lease = cl.lease_of(unit)
        if lease and lease[0] != cl.me and lease[1] >= w3claim.DEFAULT_PRIO.get(mod, 50):
            return lease[0], lease[1]
    except Exception:
        return None
    return None


def _cmdlog_write(text):
    """单独一个 cmdlog.log，不混进模块心跳。测试替换的就是这个函数。"""
    _modlog.append("cmdlog", text)


def _logged(fn, how):
    """包一层记录。order / target 在参数表里的位置**算出来**，不手数 —— 出口的签名各不相同。"""
    spec = _inspect.getfullargspec(fn)
    idx = spec.args.index("order") - 1 if "order" in spec.args else None   # *args 不含 self
    tidx = spec.args.index("target") - 1 if "target" in spec.args else None
    defaults = dict(zip(spec.args[len(spec.args) - len(spec.defaults or ()):],
                        spec.defaults or ()))
    fallback = defaults.get("order")

    def _order_of(args, kw):
        if "order" in kw:
            return kw["order"]
        if idx is not None and len(args) > idx:
            return args[idx]
        return fallback

    def wrapper(self, *args, **kw):
        who0 = _modlog.current_name()
        unit0 = args[0] if args else kw.get("unit", 0)
        held = _arbiter_refuses(who0, unit0)
        if held is not None:
            # 回执换成「被更高优先级的层握着」—— 不然调用方看到的 last_receipt 是上一条令的
            self.last_receipt = w3fast.Receipt(0, 98, 0, None, 0, 0, (0,) * 8, note=f"模块 {held[0]} 以优先级 {held[1]} 占着")
            try:
                _cmdlog_write("%s 0x%08X 0x%08X %s 让出 占着=%d/%d" % (
                    who0, unit0 or 0, _order_of(args, kw) or 0, how, held[0], held[1]))
            except Exception:
                pass
            return False
        ok = fn(self, *args, **kw)
        try:
            # 没有来源就不记：来源为空的行对 cmdclash 没用，而且它是测试污染生产日志的唯一入口（09-16 实测 7 条假施法令）
            who = _modlog.current_name()
            if who:
                if "target" in kw:
                    target = kw["target"]
                elif tidx is not None and len(args) > tidx:
                    target = args[tidx]
                else:
                    target = None
                tail = (" 目标=0x%08X" % target) if isinstance(target, int) and target else ""
                r = getattr(self, "last_receipt", None)
                # 被拒原因放在行尾（apm/cmdclash 按空格分列、第 6 列是结果，别粘在结果上）
                why = "" if ok or r is None else " 原因=" + r.reason.replace(" ", "")
                _cmdlog_write("%s 0x%08X 0x%08X %s %s%s%s" % (
                    who, unit0 or 0, _order_of(args, kw) or 0, how, "ok" if ok else "失败", tail, why))
        except Exception:
            pass
        return ok

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper._cmdlogged = True
    return wrapper


for _exit in ORDER_EXITS:
    setattr(Cmd, _exit, _logged(getattr(Cmd, _exit), _exit))


# ── 批量出口（09-23 体检报告）──────────────────────────────────────────────
# 每条 W3P 命令/查询都要等游戏线程下一次处理快车道（中位约 11 ms）。20 局体检：大脑 48 ms 的一轮里一半是在串行地等 ——
# 建造规划逐个问「能不能下」4.6 万次 × 10.9 ms、战术逐个单位下令 4.4 万次 × 12 ms。一批发出去只等一次（一批 16 条同一帧处理）。
# 仲裁和命令日志照单条出口的规矩逐条做，格式一样（cmdclash / apm / 体检报告都照读）。

def _log_line(who, unit, order, how, ok, target, receipt):
    tail = (" 目标=0x%08X" % target) if isinstance(target, int) and target else ""
    why = "" if ok or receipt is None else " 原因=" + receipt.reason.replace(" ", "")
    _cmdlog_write("%s 0x%08X 0x%08X %s %s%s%s" % (who, unit or 0, order or 0, how, "ok" if ok else "失败", tail, why))


def _send_batch(self, how, items):
    """items：[(单位地址, 订单, 目标地址或 None, Call)] -> ([成没成], [回执])。仲裁逐条、一批发出、日志逐条。"""
    who = _modlog.current_name()
    ok = [False] * len(items)
    receipts = [None] * len(items)
    calls, idx = [], []
    for k, (unit, order, _target, call) in enumerate(items):
        held = _arbiter_refuses(who, unit)
        if held is not None:
            receipts[k] = w3fast.Receipt(0, 98, 0, None, 0, 0, (0,) * 8, note=f"模块 {held[0]} 以优先级 {held[1]} 占着")
            try:
                _cmdlog_write("%s 0x%08X 0x%08X %s 让出 占着=%d/%d" % (who, unit or 0, order or 0, how, held[0], held[1]))
            except Exception:
                pass
            continue
        idx.append(k)
        calls.append(call)
    rs = self.lane.commands(calls) if calls else []
    self._snap = None
    for k, r in zip(idx, rs):
        receipts[k] = r
        ok[k] = bool(r is not None and r.ok)
    if who:
        for k in idx:
            try:
                unit, order, target, _c = items[k]
                _log_line(who, unit, order, how, ok[k], target, receipts[k])
            except Exception:
                pass
    self.last_receipts = receipts
    if receipts:
        self.last_receipt = receipts[-1]
    return ok, receipts


def move_many(self, items) -> list[bool]:
    """一批点命令：[(单位地址, x, y, 订单)] -> [接没接]。和逐条 move_to 同样的仲裁、日志（方法名 move_to）。"""
    batch = [(u, order, None, _C("point", self._ref(u), code=order, x=float(x), y=float(y))) for u, x, y, order in items]
    return _send_batch(self, "move_to", batch)[0]


def target_many(self, items) -> list[bool]:
    """一批目标命令：[(单位地址, 目标地址, 订单)] -> [接没接]。右键/攻击令因为目标在迷雾里被拒（1001）的，
    和 target_order 一样改成对它位置的攻击移动（第二批一起发，fog_fallbacks 计数）。"""
    refs = [self._ref(t) for _u, t, _o in items]
    batch = [(u, order, t, _C("target", self._ref(u), code=order, target=ref)) for (u, t, order), ref in zip(items, refs)]
    ok, receipts = _send_batch(self, "target_order", batch)
    retry = [k for k, r in enumerate(receipts) if not ok[k] and r is not None and r.verdict == 1001
             and items[k][2] in (ORDER_SMART, ORDER_ATTACK) and hasattr(refs[k], "x")]
    if retry:
        back, recs = _send_batch(self, "move_to", [(items[k][0], ORDER_ATTACK, None,
                                                         _C("point", self._ref(items[k][0]), code=ORDER_ATTACK,
                                                            x=float(refs[k].x), y=float(refs[k].y))) for k in retry])
        for k, b, r in zip(retry, back, recs):
            if b:
                ok[k] = True
                self.fog_fallbacks += 1
                r.note = "目标在迷雾里：改成攻击移动到它的位置"
    return ok


def feasible_many(self, pairs) -> list:
    """一批引擎可行性检查：[(单位地址, 四字码整数)] -> [原因码或 None]（0/220 = 能下）。纯查询，不进命令日志。"""
    if not pairs:
        return []
    rs = self.lane.commands([_C("q_feasible", self._ref(u), code=code) for u, code in pairs])
    return [r.verdict if (r is not None and r.ok) else None for r in rs]


def tech_many(self, player, codes) -> dict:
    """一批研究等级 / 建成数：{四字码: 数或 None}。"""
    if player is None or not codes:
        return {}
    rs = self.lane.commands([_C("q_tech", code=rawcode(c), extra=int(player)) for c in codes])
    return {c: (r.values[0] if (r is not None and r.ok) else None) for c, r in zip(codes, rs)}


Cmd.move_many = move_many
Cmd.target_many = target_many
Cmd.feasible_many = feasible_many
Cmd.tech_many = tech_many

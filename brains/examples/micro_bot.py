# -*- coding: utf-8 -*-
"""微操示例：在 macro_bot 的运营上，接管"打起来之后"的部队控制。职业玩家的四条微操，每条对应一个 SDK 能力：

    python -m openwar3 run brains/examples/micro_bot.py --inst 5 [--fair]

  1. 集火：全体打"最快能打死"的那个敌人（g.time_to_kill 算上克制、护甲、攻防升级、实时血量），不是打最近的
  2. 拉残血：血量低于 35% 的兵往家的方向拉 500，脱离了再回来（残血的兵活下来 = 省下一个兵的钱和人口）
  3. 英雄保命：英雄血量低于 40% 整个往后撤，别让对面拿到英雄经验
  4. 夜里打野：天黑（is_night）、附近没敌人、部队打得过（time_to_kill 双方对比）-> 去最近的地面走得到的野怪点
  5. 回防：敌兵摸到家门口而部队在外面 -> 全体回家
每一拍的命令用 with g.batch() 一次发出（几十条命令只等一次游戏线程）。
"""
from macro_bot import MacroBot

ENGAGE_R = 1100        # 部队中心这么远以内的敌人算"在打"
PULL_HP = 0.35
HERO_HP = 0.40


def _ttk(g, attacker_stats, target) -> float:
    """一群（已算好的属性）打死 target 要几秒（用它现在的血）；打不到 = 1e9。"""
    from openwar3 import combat
    ts = g.stats(target)
    t = combat.time_to_kill([s for s in attacker_stats if s], ts, target_hp=target.hp) if ts else None
    return t if t is not None else 1e9


def _center(units):
    return sum(u.x for u in units) / len(units), sum(u.y for u in units) / len(units)


class MicroBot(MacroBot):
    name = "micro"

    def on_start(self, g):
        super().on_start(g)
        self.pulled = {}           # 句柄 -> 拉回去的时刻
        self.creeping = False

    def on_tick(self, g):
        super().on_tick(g)                     # 运营 + 出击决定（macro_bot）
        army = [u for u in g.my_army()]
        if not army:
            return
        halls = g.my_buildings(self.halls)
        home = halls[0] if halls else army[0]
        cx, cy = _center(army)
        foes = [e for e in g.units("enemy") + g.creeps()
                if e.alive and (e.x - cx) ** 2 + (e.y - cy) ** 2 < ENGAGE_R ** 2 and not g.is_building(e)]
        now = g.clock() or 0.0
        # 5. 回防：敌兵摸到家门口（1500 内）而部队在外面 -> 全体回家攻击移动（5 游戏秒内不重复下）
        raid = [e for e in g.enemies(fighters_only=True) if g.dist(e, home) < 1500]
        if raid and g.dist((cx, cy), home) > 1200 and now - getattr(self, "last_defend", -99) > 5:
            self.last_defend = now
            self.creeping = False
            g.attack_move(army, home.x, home.y)
            return
        with g.batch():
            if foes:
                self._fight(g, army, foes, home, now)
            else:
                self._maybe_creep(g, army, home, cx, cy)

    def _fight(self, g, army, foes, home, now):
        # 3. 英雄保命 / 2. 拉残血
        fighters = []
        for u in army:
            ratio = u.hp / u.hp_max if u.hp_max else 1.0
            limit = HERO_HP if u.is_hero else PULL_HP
            if ratio < limit:
                if now - self.pulled.get(u.handle, -99) > 3.0:     # 3 游戏秒内不重复拉（别一拍一拍抽搐）
                    dx, dy = home.x - u.x, home.y - u.y
                    d = max(1.0, (dx * dx + dy * dy) ** 0.5)
                    g.move(u, u.x + dx / d * 500, u.y + dy / d * 500)
                    self.pulled[u.handle] = now
                continue
            fighters.append(u)
        if not fighters:
            return
        # 1. 集火：打得死得最快的那个（只算看得见的 —— 看不见的目标命令会被拒 1001；视野用快照里的掩码，不用逐个去问引擎）
        me = g.me()
        visible = [e for e in foes if e.visible_to(me)]
        if not visible:
            return
        atk = [s for s in (g.stats(u) for u in fighters) if s]            # 攻击方属性每拍算一次
        target = min(visible, key=lambda e: _ttk(g, atk, e))
        idle_or_other = [u for u in fighters if g.current_target(u) is None
                         or g.current_target(u).handle != target.handle]
        if idle_or_other:
            g.attack(idle_or_other, target)

    def _maybe_creep(self, g, army, home, cx, cy):
        # 4. 夜里打野：天黑 + 没在出击 + 至少 3 个兵，挑一个打得过、地面走得到的最近野怪点
        if self.attacking or not g.is_night() or len(army) < 3:
            self.creeping = False
            return
        if self.creeping:
            return                                   # 已经在路上了（攻击移动，遇到就打）
        best, best_d = None, None
        atk = [s for s in (g.stats(u) for u in army) if s]
        weakest = min(army, key=lambda u: u.hp)
        for c in g.creeps():
            ours = _ttk(g, atk, c)                                 # 我们打死它要多久
            theirs = _ttk(g, [g.stats(c)], weakest)                # 它打死我们最弱的要多久
            if ours >= 1e9 or ours > theirs:
                continue                                           # 打不过 / 打不到
            d = g.path_distance((cx, cy), c)
            if d is not None and d < 4000 and (best_d is None or d < best_d):
                best, best_d = c, d
        if best is not None:
            self.creeping = True
            g.attack_move(army, best.x, best.y)

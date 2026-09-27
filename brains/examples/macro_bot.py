# -*- coding: utf-8 -*-
"""宏观（运营）示例：职业玩家每局都在做的几件"基本功"，每一条都用 SDK 的一个能力写出来。

    python -m openwar3 run brains/examples/macro_bot.py --inst 5 [--fair]

在 hello_bot（采集 / 造工人 / 盖人口）上加了：
  1. 建造顺序：按表一座一座盖；**盖完让农民自己回去采矿**（build 之后 gather(..., queue='after')，Shift 连点的用法）
  2. 人口不卡：生产表里有"卡住"（blocked = 人口不够没开始）就立刻盖人口建筑，不等"快满了"
  3. 队列常满但不压钱：每座兵营只排 1 个（g.queue 看队列），钱不会锁死在 7 格队列里
  4. 攻防升级：铁匠铺空着就研究下一级（can_do 问引擎能不能研究，缺前置会被拒）
  5. 英雄先出、有技能点就学
  6. 攒够一波按**地面真实路程**选目标（path_distance：岛图上走不到的点不去），用 path() 走拐点；
     出门前比兵力（last_seen 看到过的对面部队，我方要多 1.5 倍）
  7. 侦察：90 秒派一个农民去对面出生点，看完 Shift 回矿（公平模式下对面的信息全靠它）
  8. 升本（缺金时存钱，别让兵营先把钱花光）、钱多补兵营；每个关键决策打一行日志
支持人族和兽族（表里加一行就能支持别的种族）。
"""
from hello_bot import HelloBot

TECH = {
    # tiers：升本顺序（大厅 -> 主城 -> 城堡）；soldiers：会按"谁少出谁"混着出，能不能出由引擎说了算（can_do）
    "h": dict(order=["halt", "hbar", "hbla", "hlum"], altar="halt", hero="Hamg", skills=["AHwe", "AHbz", "AHab"],
              barracks="hbar", soldiers=["hfoo", "hrif", "hkni"], smith="hbla", upgrades=["Rhme", "Rhar", "Rhra", "Rhla"],
              tiers=["hkee", "hcas"]),
    "o": dict(order=["oalt", "obar", "ofor"], altar="oalt", hero="Obla", skills=["AOwk", "AOcr", "AOmi"],
              barracks="obar", soldiers=["ogru", "ohun"], smith="ofor", upgrades=["Rome", "Roar", "Rora"],
              tiers=["ostr", "ofrt"]),
}
WAVE_FOOD = 40          # 部队占人口到这么多就出门（兵 + 英雄）
# ⚠ 09-24 实机：示例一直停在一本出步兵火枪，23 分钟被三本的骑士狮鹫女巫推平 —— 升本和高级兵是运营的一半
HALLS_ALL = {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}


class MacroBot(HelloBot):
    name = "macro"

    def on_start(self, g):
        super().on_start(g)
        self.t = TECH.get(self.race, TECH["h"])
        self.last_build = -99.0
        self.attacking = False
        self.target, self.target_at = None, -99.0
        self.skill_i = {}
        self.scouted = False

    # ── 小工具 ──────────────────────────────────────────────
    def log(self, g, text: str) -> None:
        """关键决策打一行（游戏时钟 + 内容）：复盘时知道它当时在想什么。"""
        print(f"[{self.name} {g.clock() or 0:6.0f}s] {text}", flush=True)

    def _have(self, g, code) -> bool:
        """有这座建筑（盖好的或正在盖的都算）。"""
        return bool(g.my_buildings({code}))

    def _free_worker(self, g, near):
        """挑一个没在盖房子的工人（离 near 最近）。"""
        ws = [w for w in g.my_workers() if not g.is_constructing(w)]
        return g.nearest(ws, near)

    def on_tick(self, g):
        super().on_tick(g)                    # 采集 / 造工人 / 快满时盖人口（hello_bot）
        res = g.resources()
        halls = g.my_buildings(self.halls)
        if res is None or not halls:
            return
        home = halls[0]
        now = g.clock() or 0.0
        t = self.t

        # 2. 人口卡住（生产表 blocked）-> 马上盖人口建筑；hello_bot 只在"还剩 6 格"时盖，打起来掉兵后就会慢一拍
        blocked = any(p.blocked for _b, p in g.all_production("me"))
        building_farm = any(g.under_construction(b) for b in g.my_buildings({self.farm}))
        if blocked and not building_farm and g.can_afford(self.farm) and now - self.last_build > 8:
            w = self._free_worker(g, home)
            if w is not None and g.build_near(w, self.farm, home.x, home.y):
                self.last_build = now
                self.assigned.pop(w.addr, None)

        # 1. 建造顺序：缺的第一座，钱够就派最近的空闲工人去盖，盖完自己回矿（Shift 回矿）
        need = next((c for c in t["order"] if not self._have(g, c)), None)
        if need and g.can_afford(need) and now - self.last_build > 8:
            w = self._free_worker(g, home)
            spot = g.build_near(w, need, home.x, home.y) if w is not None else None
            if spot:
                self.log(g, f"盖 {need} @ {spot}")
                self.last_build = now
                mine = g.nearest(g.gold_mines(), home)
                if mine is not None:
                    g.gather(w, mine, queue="after")          # 盖完回去采矿 —— 不用下一拍再去找它
                self.assigned.pop(w.addr, None)

        # 7. 侦察：开局 90 秒派一个农民去对面出生点看一眼，看完自己回来采矿（路径 + Shift 回矿）。
        #    公平模式下这是 last_seen 里有对面信息的唯一来源（⚠ 09-24 实测：不侦察，"看到过的对面部队"永远是 0）
        if not self.scouted and now > 90:
            mp = g.map()
            far = [p for p in (mp.starts if mp else []) if (p[0] - home.x) ** 2 + (p[1] - home.y) ** 2 > 2000 ** 2]
            w = self._free_worker(g, home)
            if far and w is not None:
                goal = min(far, key=lambda p: g.path_distance(home, p) or 1e9)
                route = g.walk_path(w, goal) or [goal]
                g.path(w, route)
                mine = g.nearest(g.gold_mines(), home)
                if mine is not None:
                    g.gather(w, mine, queue="after")
                self.assigned.pop(w.addr, None)
                self.scouted = True
                self.log(g, f"侦察：派一个农民去 {tuple(round(v) for v in goal)}")

        # 1b. 建造顺序走完、手里有余钱 -> 多一座兵营（一座兵营的产能跟不上收入；钱堆着 = 兵少了）
        n_bar = len(g.my_buildings({t["barracks"]}))
        want_bar = 2 if res["gold"] < 600 else 3
        if need is None and res["gold"] >= 300 and n_bar < want_bar and now - self.last_build > 8:
            w = self._free_worker(g, home)
            if w is not None and g.build_near(w, t["barracks"], home.x, home.y):
                self.last_build = now
                mine = g.nearest(g.gold_mines(), home)
                if mine is not None:
                    g.gather(w, mine, queue="after")
                self.assigned.pop(w.addr, None)

        # 1c. 升本：建造顺序走完、大厅队列空（队列没空时升级会被拒 185），钱够就升；进度看生产表。
        #     钱不够（原因码 8 金 / 9 木）就**存钱**：这一拍兵营不出兵（家里兵太少时除外）——
        #     ⚠ 09-24 实机：不存钱时两座兵营每拍先把钱花光，16 分钟一次都没升上去
        saving = False
        p = g.production(home)
        nxt = next((c for c in t["tiers"] if not g.my_buildings({c})), None)
        if need is None and nxt and not (p and p.kind == "upgrade"):
            v = g.can_do(home, nxt)
            if v in (0, 220) and not g.queue(home):
                if g.upgrade(home, nxt):
                    self.log(g, f"升本 -> {nxt}")
            elif v == 8:          # 只在缺金时存钱：缺木（9）时步兵照出（⚠ 09-24：缺木也存 -> 金子堆到 6526 没变成兵）
                saving = sum(_food(g, u) for u in g.my_army()) >= 12

        # 5. 英雄：祭坛有、英雄没有 -> 先出英雄（死了用复活）
        altars = [a for a in g.my_buildings({t["altar"]}) if not g.under_construction(a)]
        if altars and not g.my_heroes() and not g.queue(altars[0]):
            if g.revive(altars[0]):
                self.log(g, "复活英雄")
            elif g.train(altars[0], t["hero"]):
                self.log(g, f"训练英雄 {t['hero']}")
        for h in g.my_heroes():
            info = g.hero_info(h)
            if info and info["skill_points"]:
                i = self.skill_i.get(h.addr, 0)
                if g.learn(h, t["skills"][i % len(t["skills"])]):
                    self.skill_i[h.addr] = i + 1

        # 3. 兵营：每座只排 1 个；兵种按"现在谁少出谁"混着出（火枪要铁匠铺，can_do 会说"缺前置"）
        have = {s: len(g.units("me", {s})) for s in t["soldiers"]}
        order = sorted(t["soldiers"], key=lambda s: have[s])
        for b in g.my_buildings({t["barracks"]}):
            if saving or g.under_construction(b) or g.queue(b):
                continue
            verdicts = g.can_do_many([(b, s) for s in order])
            code = next((s for s, v in zip(order, verdicts) if v in (0, 220)), None)
            if code:
                g.train(b, code)

        # 4. 攻防升级：铁匠铺空着就研究下一个能研究的
        for sm in g.my_buildings({t["smith"]}):
            if saving or g.under_construction(sm) or g.queue(sm):
                continue
            verdicts = g.can_do_many([(sm, u) for u in t["upgrades"]])
            up = next((u for u, v in zip(t["upgrades"], verdicts) if v in (0, 220)), None)
            if up:
                g.research(sm, up)

        # 6. 出击：部队人口够了 -> 选地面走得到的最近敌方大厅（没看见过就去对面出生点）
        army = g.my_army()
        food = sum(_food(g, u) for u in army)
        # 出门前比兵力：10 分钟内看到过的对面部队（last_seen，公平模式也能用）；我方要多 1.5 倍。一次都没看到过门槛 ×1.5
        # ⚠ 09-24 实机：原来只看 120 秒内的记忆，而侦察在 12 分钟前 —— "看到过的对面部队"永远是 0。窗口放到 10 分钟
        seen = [u for u, _t, _a in g.last_seen(max_age=600) if not g.is_building(u) and u.type not in ("hpea", "opeo", "uaco", "ewsp")]
        foe_food = sum(_food(g, u) for u in seen)
        # 一次都没看到过对面部队 = 不知道对面多强（⚠ 09-24 公平模式实测：没侦察就 42 人口一本兵去冲电脑主基地，两次都打剩 11 人口）
        ready = food >= (max(WAVE_FOOD, foe_food * 1.5) if foe_food else WAVE_FOOD * 1.5)
        if not self.attacking and ready:
            self.attacking = True
            self.target, self.target_at = self._target(g, home), now
            if self.target:
                route = g.walk_path(g.nearest(army, home) or home, self.target) or [self.target]
                g.path(army, route, attack=True)
                self.log(g, f"出击：部队 {food} 人口（看到过的对面部队 {foe_food}）-> "
                            f"{tuple(round(v) for v in self.target)}（{len(route)} 个拐点）")
        elif self.attacking and food < WAVE_FOOD // 3:
            self.attacking = False                 # 打残了：回家重攒
            g.move(army, home.x, home.y)
            self.log(g, f"撤退：部队只剩 {food} 人口")
        elif self.attacking:
            if now - self.target_at > 20:          # 目标可能已经拆掉了：20 游戏秒重选一次
                self.target, self.target_at = self._target(g, home) or self.target, now
            idle = [u for u in army if not g.order_of(u)]        # 后续出的兵、打完一波站着的兵 -> 接着去
            if idle and self.target:
                g.attack_move(idle, *self.target)

    def _target(self, g, home):
        """最近的（按地面路程）敌方大厅：看得见的 > 记忆里的 > 对面出生点。"""
        cands = [(u.x, u.y) for u in g.enemies() if u.type in HALLS_ALL]
        cands += [(u.x, u.y) for u, _t, _a in g.last_seen() if u.type in HALLS_ALL]
        mp = g.map()
        if not cands and mp:
            cands = [p for p in mp.starts if (p[0] - home.x) ** 2 + (p[1] - home.y) ** 2 > 2000 ** 2]
        best, best_d = None, None
        for p in cands:
            d = g.path_distance(home, p)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = p, d
        return best


def _food(g, u) -> int:
    from openwar3.game import unit_table
    return int((unit_table().get(u.type) or {}).get("food", 1))

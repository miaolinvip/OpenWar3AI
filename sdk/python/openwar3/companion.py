# -*- coding: utf-8 -*-
"""玩伴（09-24）：在 RPG / 自定义地图里给玩家配一个 AI 伙伴 —— 跟着你、帮你打怪、给你加血、陪你说话。
怎么用由玩家自己决定：继承 Companion，改几个属性或覆盖几个钩子。

    from openwar3 import run
    from openwar3.companion import Companion

    class MyBuddy(Companion):
        mode = "ally"            # 形态，见下
        unit = "Hpal"            # 造什么单位（任何四字码，地图自定义的也行）
        nickname = "小圣骑"
    run(MyBuddy, inst=20, judge=False)       # judge=False：RPG 地图不按对战规则判胜负

形态（mode）：
  ally   空着的真人槽当一个盟友玩家：有自己的颜色和名字，你点不到它，它自己打（单人局）
  own    造在你自己名下：你随时能手动指挥它，你不管的时候 AI 替你操作（单人局）
  adopt  接管地图里已有的一个单位（地图给的宠物/随从）：覆盖 adopt(g) 返回那个单位
  voice  不造单位，只说话（陪聊、提醒）：不改世界，多人局也能用
行为（每拍按顺序，哪个成立做哪个；都能关、都能在子类里改）：
  retreat 自己残血先退 -> heal 主人残血给他加血 -> assist 帮主人打怪 -> follow 跟着主人
说话：talk（openwar3.talk.Talk，固定台词 / 本地大模型），头顶气泡；voice 形态在屏幕左下角。

⚠ 边界：造单位、改名、设盟友、调等级都是本机单方面改世界 —— 只在单人局（和本机的电脑）能用；多人局会不同步，
  JASS 通道会拒绝，这时自动退回 voice。只用于单机/局域网自建的游戏。
"""
from __future__ import annotations

import math
import random
import time

from .bot import Bot
from .jass import JassError
from .orders import SMART
from .talk import Talk


class Companion(Bot):
    name = "companion"
    # ── 形态 ──
    mode = "ally"                  # ally / own / adopt / voice
    unit = "Hpal"                  # 玩伴是什么（ally / own 形态造它）
    nickname = "小伙伴"             # ally 形态改成这个玩家名；说话时的名字
    slot: int | None = None        # ally 形态用哪个空槽；None = 自动挑一个空着的真人槽
    # ── 行为 ──
    follow_distance = 350.0        # 离主人超过这么远就跟上
    leash = 1600.0                 # 超过这么远不打了，直接跑回主人身边
    assist_radius = 900.0          # 主人身边这么远内的敌人帮着打
    retreat_at = 0.25              # 自己血量低于这个比例先退到主人身后
    heal = ("holybolt", "AHhb", 0.55)   # (施法订单名, 要学的技能, 主人血量低于多少加血)；None = 不加血
    skills = ("AHhb", "AHds", "AHad", "AHre")   # 有技能点就按这个顺序学
    match_level = True             # 玩伴英雄等级跟上主人（本机改世界，只在单人局）
    respawn_s = 30.0               # 玩伴倒下后多少墙钟秒再造一个（ally / own）
    talk: Talk | None = None
    # ── 交互（09-24）：主人在聊天框打命令指挥玩伴（visual.chat_commands：每条命令一个空触发器，数执行次数）──
    commands = {"-follow": "follow", "-stay": "stay", "-heal": "heal", "-hi": "hello",
                "-跟随": "follow", "-停": "stay", "-加血": "heal"}   # 中文命令待真人用输入法验证
    # 这些事件的话用「头像对白」说：底部头像换成玩伴、屏幕上出「名字：话」字幕（战役里人物说话那种）；其余冒头顶气泡
    scene_events = ("hello", "master_died", "master_levelup", "buddy_back")
    # 状态面板（09-25，画板 g.canvas：运行时自绘，多人局也安全）：显示玩伴在干什么、心情、血量、战绩。None = 不显示
    # 放左边中间：右上角是地图自己的面板 / 排行榜 / 倒计时的地方（09-25 实测压在 WarChasers 的「杀敌数」上）
    panel_at = (16, 330)

    # ── 钩子（子类覆盖）──
    def find_master(self, g):
        """主人：本机玩家等级最高的英雄；还没有英雄（比如 WarChasers 开局是个小精灵）就用本机第一个会动的单位。"""
        heroes = [u for u in g.units("me") if u.is_hero]
        if heroes:
            return max(heroes, key=lambda h: (getattr(h, "level", 0) or 0, h.hp_max))
        movers = [u for u in g.units("me") if not g.is_building(u)]
        return movers[0] if movers else None

    def adopt(self, g):
        """mode='adopt' 时：返回要接管的那个单位（快照里的）。默认 None（不接管）。"""
        return None

    def pick_target(self, g, master, enemies):
        """帮主人打谁：正在打主人的 > 主人正在打的 > 离主人最近的。返回 None = 不打。"""
        mt = g.current_target(master)
        attackers = [e for e in enemies if (t := g.current_target(e)) is not None and t.addr == master.addr]
        if attackers:
            return min(attackers, key=lambda e: g.dist(e, master))
        if mt is not None and any(e.addr == mt.addr for e in enemies):
            return mt
        return min(enemies, key=lambda e: g.dist(e, master)) if enemies else None

    def on_poke(self, g):
        """主人右键点了玩伴（在叫它）。默认：说句话。"""
        self.speak("poke")

    def on_command(self, g, action: str):
        """主人打了一条聊天命令（commands 表里映射成 action）。默认：follow 跟着 / stay 原地守着 / heal 马上加血 / hello 打招呼。"""
        b = self.buddy_unit(g) if self.mode != "voice" else None
        if action == "stay":
            self.staying = True
            if b is not None:
                self.cg.hold(b)
            self.speak("cmd_stay")
        elif action == "follow":
            self.staying = False
            self.speak("cmd_follow")
        elif action == "heal":
            m = self._master(g)
            if b is not None and m is not None and self.heal and g.cooldown(b, self.heal[1]) == 0:
                self.cg.cast(b, self.heal[0], target=m)
                self.stats["heals"] += 1
                self.speak("cmd_heal")
            else:
                self.speak("cmd_cant")
        elif action == "hello":
            self.speak("poke")

    # ── 以下是框架 ──
    def on_start(self, g):
        self.g = g
        self.talk = self.talk or Talk()
        self.cg = g                    # 下令用哪个 Game：ally 形态是那个空槽的玩家车道
        self.buddy_addr = None
        self.master_addr = None
        self.dead_at = None
        self.hostile: set[int] = set()
        self.hostile_at = -1e9
        self.last_order = {}
        self.last_poke = None
        self.master_dead = False
        self.master_name = ""
        self.next_idle = time.monotonic() + random.uniform(60, 120)
        self.stats = {"heals": 0, "assists": 0, "kills": 0, "follows": 0, "retreats": 0, "said": 0, "respawns": 0}
        self.map_name = ""
        try:
            md = g.map_data
            self.map_name = md.name if md else ""
        except Exception:  # noqa: BLE001
            pass
        self.staying = False
        self.visual = self.cmds = self.canvas = None
        self.doing, self.mood, self.panel_at_tick = "刚来", "开心", 0.0
        self._setup(g)
        if self.panel_at is not None:
            try:
                self.canvas = g.canvas
            except Exception as e:  # noqa: BLE001 - 老 DLL 没有画板：不显示面板，别的照常
                print(f"[{self.name}] 状态面板用不了（{e}）", flush=True)
        try:                                         # 画面和聊天命令都走 JASS 通道：多人局 / 公平模式里没有，照样能跑
            from .visual import Visual
            self.visual = Visual(g)
            if self.commands:
                self.cmds = self.visual.chat_commands(list(self.commands))
        except JassError as e:
            print(f"[{self.name}] 聊天命令和头像对白用不了（{e}）", flush=True)
        m = self._master(g)
        self.speak("hello", master=self._name(g, m))

    def _setup(self, g):
        if self.mode == "voice":
            return
        if self.mode == "adopt":
            u = self.adopt(g)
            self.buddy_addr = u.addr if u else None
            return
        try:
            if self.mode == "ally":
                slots = g.player_slots()
                free = [s for s in slots if s["controller"] == "user" and s["state"] != "playing"]
                pick = next((s for s in free if s["id"] == self.slot), None) if self.slot is not None else \
                    next((s for s in free if s["ally"]), free[0] if free else None)
                if pick is None:
                    print(f"[{self.name}] 没有空着的真人槽，改成 own 形态（玩伴算在你名下）")
                    self.mode = "own"
                else:
                    self.slot = pick["id"]
                    me = g.snapshot().me
                    g.set_alliance(self.slot, me, allied=True, vision=True)
                    g.set_player_name(self.slot, self.nickname)
                    from .game import Game
                    self.cg = Game(pid=g.pid, player=self.slot)
            self._spawn(g)
        except JassError as e:
            print(f"[{self.name}] 造不了玩伴（{e}），改成只说话")
            self.mode = "voice"

    def _spawn(self, g):
        m = self._master(g)
        if m is None:
            return
        ang = random.uniform(0, 2 * math.pi)
        u = g.spawn(self.unit, m.x + 200 * math.cos(ang), m.y + 200 * math.sin(ang),
                    player=self.slot if self.mode == "ally" else None)
        self.buddy_addr = u.addr if u else None
        if u is not None:
            self._grow(g, u, m)

    def _grow(self, g, b, m):
        """等级跟上主人、有技能点就学。"""
        if not b.is_hero:
            return
        try:
            if self.match_level and m is not None and m.is_hero:
                ml, bl = getattr(m, "level", 1) or 1, getattr(b, "level", 1) or 1
                if ml > bl:
                    g.jass.SetHeroLevel(b, int(ml), False)
            info = g.hero_info(b)
            if (info or {}).get("skill_points"):
                md = g.map_data
                # 自己配的技能顺序在前，再加上地图给这个英雄定的技能（地图英雄的技能表在 w3u 的 uhab）；学不了的引擎会拒，不碍事
                for code in list(self.skills) + (md.hero_skills(b.type) if md else []):
                    self.cg.learn(b, code)
        except JassError:
            pass

    def buddy_unit(self, g):
        """玩伴现在是哪个单位。ally 形态按「那个槽现在最好的单位」找（英雄优先），不按地址死认：
        RPG 地图的触发器会把玩伴换掉 —— 09-24 WarChasers 实测，玩伴跟着主人的小精灵走进选英雄的能量圈，
        地图把圣骑士删了、给 1 号发了一个地图英雄；按地址认会以为玩伴死了、30 秒后再造一个，地图给的英雄却站着不动。"""
        b = self._unit(g, self.buddy_addr)
        if self.mode == "ally" and self.slot is not None:
            mine = [u for u in g.units(self.slot) if not g.is_building(u)]
            heroes = [u for u in mine if u.is_hero]
            best = max(heroes, key=lambda h: (h.level or 0, h.hp_max)) if heroes else (b or (mine[0] if mine else None))
            if best is not None and (b is None or best.addr != b.addr):
                if self.buddy_addr:
                    print(f"[{self.name}] 玩伴换成了 {self._name(g, best)}（{best.type}）", flush=True)
                self.buddy_addr = best.addr
                self.dead_at = None
                self._grow(g, best, self._master(g))
            return best
        if b is None and self.mode == "adopt":
            u = self.adopt(g)
            if u is not None:
                self.buddy_addr = u.addr
                return u
        return b

    def _unit(self, g, addr):
        if not addr:
            return None
        s = g.snapshot()
        return next((u for u in (s.units if s else []) if u.addr == addr and u.alive), None)

    def _master(self, g):
        m = self._unit(g, self.master_addr)
        if m is None or not m.is_hero:
            cand = self.find_master(g)
            if cand is not None:
                m = cand
                self.master_addr = cand.addr
        return m

    def _name(self, g, u) -> str:
        if u is None:
            return "你"
        try:
            md = g.map_data
            names = md.hero_names(u.type) if md else []
            return names[0] if names else (md.name_of(u.type) if md else u.type)
        except Exception:  # noqa: BLE001
            return u.type

    def _hostile_owners(self, g) -> set[int]:
        """真正的敌人（按 JASS 的同盟关系，不是"别的玩家"）：RPG 地图里常有好几家盟友。20 秒刷新一次。"""
        now = time.monotonic()
        if now - self.hostile_at > 20:
            self.hostile_at = now
            try:
                j = g.jass
                me = j.Player(g.snapshot().me)
                self.hostile = {i for i in range(16) if j.IsPlayerEnemy(j.Player(i), me)}
            except JassError:
                me = g.snapshot().me
                self.hostile = {i for i in range(13) if i != me and i != self.slot}
        return self.hostile

    def speak(self, event: str, **ctx) -> None:
        g = self.g
        ctx.setdefault("me", self.nickname)
        ctx.setdefault("map", self.map_name or "这张地图")
        text = self.talk.line(event, **ctx)
        if text:
            self._say(text, event)

    def _say(self, text: str, event: str = "") -> None:
        g = self.g
        b = self.buddy_unit(g) if self.mode != "voice" else None
        print(f"[{self.name}] 说：{text}", flush=True)
        if event in self.scene_events and self.visual is not None:
            try:                                     # 头像对白：底部头像换成玩伴（没有单位就用 unit 那种）+ 字幕
                self.visual.scene(self.nickname, text, portrait=b.type if b is not None else self.unit,
                                  color=self.slot if self.slot is not None else 1)
                self.stats["said"] += 1
                return
            except JassError:
                pass
        ok = g.say(b, text, 5.0) if b is not None else False
        if not ok:
            try:
                g.show_text(f"|cffffcc00{self.nickname}|r：{text}", 6.0)
            except JassError:
                g.message(f"{self.nickname}：{text}")
        self.stats["said"] += 1

    def _order(self, key, fn, every: float = 1.0) -> bool:
        """同一件事 every 秒内不重复下令（右键跟随一直重下会让单位原地抽搐）。"""
        now = time.monotonic()
        last = self.last_order.get(key)
        if last is not None and now - last < every:
            return False
        self.last_order = {key: now}
        fn()
        return True

    def on_event(self, g, ev):
        m_addr, b_addr = self.master_addr, self.buddy_addr
        if ev.kind == "hero.levelup" and ev.addr == m_addr:
            m = self._unit(g, m_addr)
            self.speak("master_levelup", master=self._name(g, m), level=getattr(m, "level", "") if m else "")
            b = self.buddy_unit(g)
            if b is not None:
                self._grow(g, b, m)
        elif ev.kind == "hero.levelup" and ev.addr == b_addr:
            b = self.buddy_unit(g)
            if b is not None:
                self._grow(g, b, self._unit(g, m_addr))
        elif ev.kind in ("unit.died", "killed") and ev.addr == m_addr and not self.master_dead:
            self.master_dead = m_addr                      # 英雄复活还是同一个对象：等它再活过来说「欢迎回来」
            self.speak("master_died", master=self.master_name or "你")
        elif ev.kind == "killed" and b_addr and ev.source_addr == b_addr:
            self.stats["kills"] += 1
            try:
                enemy = g.map_data.name_of(ev.type) if g.map_data else ev.type
            except Exception:  # noqa: BLE001
                enemy = ev.type
            self.speak("kill", enemy=enemy)

    def on_tick(self, g):
        for text in self.talk.take():              # 大模型答回来的话
            self._say(text)
        if self.cmds is not None:                  # 主人打的聊天命令
            try:
                for cmd in self.cmds.poll():
                    print(f"[{self.name}] 主人命令：{cmd}", flush=True)
                    self.on_command(g, self.commands[cmd])
            except JassError:
                pass
        if self.visual is not None:
            self.visual.tick()
        now = time.monotonic()
        if self.master_dead and self._unit(g, self.master_dead) is not None:
            self.master_addr, self.master_dead = self.master_dead, False
            self.speak("master_back", master=self.master_name or "你")
        m = self._master(g)
        if m is None:
            return
        if m.is_hero and m.addr != getattr(self, "_named_addr", None):
            self._named_addr, self.master_name = m.addr, self._name(g, m)
        if self.mode == "voice":
            self._idle(g, m, now)
            return
        b = self.buddy_unit(g)
        if b is None:
            self.doing = "倒下了，等复活"
            self._panel(g, m, None, now)
            self._dead(g, now)
            return
        if self.dead_at is not None:                     # 死了又活了（地图复活的英雄还是同一个对象）
            self.dead_at = None
            self.speak("buddy_back")
        self._poke(g, m, b)
        if self._retreat(g, m, b):
            self.doing = "撤退中"
        elif self._heal(g, m, b):
            self.doing = "给你加血"
        elif self._assist(g, m, b):
            self.doing = "帮你打怪"
        elif self._follow(g, m, b):
            self.doing = "跟上你"
        else:
            self.doing = "原地守着" if self.staying else "在你身边"
            self._idle(g, m, now)
        self._panel(g, m, b, now)

    def _panel(self, g, m, b, now):
        """右上角状态面板（画板）：半秒刷一次。心情按局面粗判 —— 主人倒下难过、主人残血紧张、自己残血害怕、打架兴奋。"""
        if self.canvas is None or now - self.panel_at_tick < 0.5:
            return
        self.panel_at_tick = now
        if self.master_dead:
            self.mood = "难过"
        elif m.hp_max and m.hp < 0.4 * m.hp_max:
            self.mood = "紧张"
        elif b is not None and b.hp_max and b.hp < 0.3 * b.hp_max:
            self.mood = "害怕"
        elif self.doing == "帮你打怪":
            self.mood = "兴奋"
        else:
            self.mood = "开心"
        lines = [f"正在：{self.doing}", f"心情：{self.mood}",
                 f"击杀 {self.stats['kills']} · 加血 {self.stats['heals']}"]
        try:
            with self.canvas.batch():
                self.canvas.panel("buddy_panel", f"{self.nickname}（玩伴）", lines, screen=self.panel_at, width=210, size=18)
                frac = (b.hp / b.hp_max) if b is not None and b.hp_max else 0.0
                x, y = self.panel_at
                # 血条放在面板**上面**：面板高度随字号/分辨率变，放下面会压在一起
                self.canvas.bar("buddy_hp", frac, screen=(x, y - 20), width=210, height=14,
                                color=(80, 220, 80) if frac > 0.4 else (230, 80, 60),
                                text=f"{int(b.hp)}/{int(b.hp_max)}" if b is not None else "倒下了")
        except Exception:  # noqa: BLE001 - 画不上不影响玩伴干活
            self.canvas = None

    def _dead(self, g, now):
        if self.mode in ("adopt", "voice"):
            return
        if self.dead_at is None:
            self.dead_at = now
            self.speak("buddy_died", respawn=int(self.respawn_s))
        elif now - self.dead_at >= self.respawn_s:
            self.dead_at = None
            try:
                # 死的是英雄就原地复活它（等级、物品都在），不另造一个 —— 地图自己也可能复活它，那样 buddy_unit 会先找到
                owner = self.slot if self.mode == "ally" else g.snapshot().me
                dead = [u for u in g.units(owner, alive=False) if u.is_hero and not u.alive]
                m = self._master(g)
                if dead and m is not None:
                    g.jass.ReviveHero(dead[0], m.x + 150.0, m.y, True)
                    self.buddy_addr = dead[0].addr
                else:
                    self._spawn(g)
                self.stats["respawns"] += 1
                self.speak("buddy_back")
            except JassError:
                pass

    def _poke(self, g, m, b):
        """主人右键点了玩伴 = 主人手上是右键（smart）订单、目标就是玩伴。09-24 只比目标时打架中误触发过两次，加上订单号。"""
        o = g.current_order(m)
        tgt = o and o.get("target")
        poked = bool(tgt) and o.get("order") == SMART and tuple(tgt) == tuple(b.handle)
        if poked and not self.last_poke:
            print(f"[{self.name}] 主人右键点了玩伴（主人订单 {o}）", flush=True)
            self.on_poke(g)
        self.last_poke = poked

    def _enemies_near(self, g, pos, radius):
        hostile = self._hostile_owners(g)
        return [u for u in g.units("all") if u.owner in hostile and u.alive and g.dist(u, pos) <= radius
                and not g.is_building(u)]

    def _retreat(self, g, m, b) -> bool:
        if b.hp >= self.retreat_at * b.hp_max:
            return False
        foes = self._enemies_near(g, b, 700)
        if not foes:
            return False
        f = g.nearest(foes, b)
        dx, dy = m.x - f.x, m.y - f.y
        d = math.hypot(dx, dy) or 1.0
        x, y = m.x + dx / d * 250, m.y + dy / d * 250          # 退到主人身后（背对最近的敌人）
        if self._order("retreat", lambda: self.cg.move(b, x, y), every=2.0):
            self.stats["retreats"] += 1
            self.speak("buddy_low")
        return True

    def _heal(self, g, m, b) -> bool:
        if not self.heal or m.hp_max <= 0:
            return False
        order, code, below = self.heal
        if m.hp >= below * m.hp_max or g.dist(m, b) > 900:
            return False
        if g.cooldown(b, code) not in (0, 0.0):
            return False
        r = self.cg.cast(b, order, target=m)
        if getattr(r, "ok", False):
            self.stats["heals"] += 1
            self.speak("healed")
            if m.hp < 0.3 * m.hp_max:
                self.speak("master_low", master=self._name(g, m))
            return True
        return False

    def _assist(self, g, m, b) -> bool:
        if not self.staying and g.dist(m, b) > self.leash:
            return False
        # 原地守着（-stay）的时候只打冲到自己身边的
        foes = self._enemies_near(g, b, self.assist_radius * 0.6) if self.staying else self._enemies_near(g, m, self.assist_radius)
        t = self.pick_target(g, m, foes) if foes else None
        if t is None:
            return False
        cur = g.current_target(b)
        if cur is not None and cur.addr == t.addr:
            return True
        if self._order(("attack", t.addr), lambda: self.cg.attack(b, t), every=1.5):
            self.stats["assists"] += 1
            self.speak("fight")
        return True

    def _follow(self, g, m, b) -> bool:
        if self.staying:                           # 主人说了 -stay：不跟
            return False
        d = g.dist(m, b)
        if d <= self.follow_distance:
            return False
        if d > self.leash:
            fn = lambda: self.cg.move(b, m.x, m.y)          # noqa: E731 - 太远：别恋战，直接跑回来
        else:
            fn = lambda: self.cg.attack(b, m)                # noqa: E731 - 右键盟友 = 跟随
        if self._order("follow", fn, every=2.0):
            self.stats["follows"] += 1
        return True

    def _idle(self, g, m, now):
        if now >= self.next_idle:
            self.next_idle = now + random.uniform(60, 150)
            if not self._enemies_near(g, m, 1200):
                self.speak("idle", master=self._name(g, m))

    def on_end(self, g, reason):
        if self.canvas is not None:
            try:
                self.canvas.remove("buddy_panel")
                self.canvas.remove("buddy_hp")
            except Exception:  # noqa: BLE001
                pass
        if self.visual is not None:
            try:
                self.visual.clear()                # 聊天命令的触发器、画过的东西都收掉（游戏已经退了就算了）
            except Exception:  # noqa: BLE001
                pass
        print(f"[{self.name}] 结束：{reason}；{self.stats}")

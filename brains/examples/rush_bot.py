# -*- coding: utf-8 -*-
"""经济 + 造兵 + 攒够了一波出去打。在 hello_bot 上加了三件事：

  4. 兵营：没有就盖一座；有了就一直出基础兵
  5. 攒到 ARMY_WAVE 个兵 -> 全体攻击移动去敌方大厅（看不见就去离我最远的金矿找）
  6. 英雄：祭坛出第一个英雄，有技能点就学第一个技能

先支持人族和兽族（另外两族的兵营/兵种填进表就能用）。
"""
from openwar3 import Bot
from hello_bot import HelloBot

ARMY = {  # 种族 -> (祭坛, 英雄, 第一个技能, 兵营, 基础兵)
    "h": ("halt", "Hpal", "AHhb", "hbar", "hfoo"),
    "o": ("oalt", "Obla", "AOwk", "obar", "ogru"),
}
ARMY_WAVE = 8


class RushBot(HelloBot):
    name = "rush"

    def on_start(self, g):
        super().on_start(g)
        self.altar, self.hero, self.skill, self.barracks, self.soldier = ARMY.get(self.race, ARMY["h"])
        self.attacking = False
        self.last_build_try = -99.0

    def on_tick(self, g):
        super().on_tick(g)
        res = g.resources()
        halls = g.my_buildings(self.halls)
        if res is None or not halls:
            return
        home = halls[0]
        now = g.clock() or 0.0

        # 4. 兵营 / 祭坛：没有就盖（每 15 游戏秒试一次）。用 build_near 绕着大厅试点，别写死坐标 ——
        #    09-23 实机：写死的 (大厅 -700, ±300) 在 Echo Isles 西家正好是树林，盖了 3 分钟一座都没盖出来
        for code, gold in ((self.barracks, 160), (self.altar, 180)):
            if not g.my_buildings({code}) and res["gold"] >= gold and now - self.last_build_try > 15:
                self.last_build_try = now
                builder = next((w for w in g.my_workers() if not g.is_constructing(w)), None)
                if builder is not None:
                    self.assigned.pop(builder.addr, None)     # 试点被拒也会让它停下，统一交给空闲派工
                    g.build_near(builder, code, home.x, home.y)
        # 6. 英雄先出：有祭坛没英雄时兵营先停，别让步兵把人口吃光（09-23 实机：英雄一直"人口不够"）
        altars = g.my_buildings({self.altar})
        want_hero = bool(altars) and not g.my_heroes()
        if want_hero and not g.revive(altars[0]):      # 死了只能复活（英雄唯一，再训练会被拒 221）
            g.train(altars[0], self.hero)
        for b in g.my_buildings({self.barracks}):
            if not want_hero:
                g.train(b, self.soldier)
        for h in g.my_heroes():
            info = g.hero_info(h)
            if info and info["skill_points"]:
                g.learn(h, self.skill)

        # 5. 出击
        army = g.my_army()
        if not self.attacking and len(army) >= ARMY_WAVE:
            self.attacking = True
        if self.attacking:
            if len(army) < ARMY_WAVE // 2:          # 打残了回家重攒
                self.attacking = False
                for u in army:
                    g.move(u, home.x, home.y)
                return
            enemy_halls = [u for u in g.enemies() if u.type in {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt",
                                                                 "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}]
            target = g.nearest(enemy_halls, home) or max(g.gold_mines(), key=lambda m: g.dist(m, home), default=None)
            if target is not None:
                for u in army:
                    if (u.order or 0) == 0:          # 只给闲着的下令，别一拍一拍重下打断战斗
                        g.attack_move(u, target.x, target.y)

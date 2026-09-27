# -*- coding: utf-8 -*-
"""最小的 bot：只管经济。

    python -m openwar3 run brains/examples/hello_bot.py --inst 5

做三件事：
  1. 闲着的工人 -> 去最近的金矿（每座矿最多 5 个人，多了没用）；矿满了就去砍树
  2. 工人不到"每座矿 5 个 + 伐木 5 个" -> 大厅造工人（队列只排 1 个）
  3. 人口快满 -> 找个工人在大厅旁边盖人口建筑

⚠ 09-23 实机踩到的：每次都挑"第一个工人"去盖房子，人族农场要盖 35 秒，而 20 秒后同一个工人被派去开下一座 ——
  上一座地基就停工了（3 座农场全是半截地基，人口卡在 12/12）。所以：正在盖的工人不挑、有地基没盖完不开新的。

能跑四个种族（种族差异都写在下面的表里）。
"""
from openwar3 import Bot

# 种族 -> (工人, 大厅们, 人口建筑)
RACES = {
    "h": ("hpea", {"htow", "hkee", "hcas"}, "hhou"),
    "o": ("opeo", {"ogre", "ostr", "ofrt"}, "otrb"),
    "u": ("uaco", {"unpl", "unp1", "unp2"}, "uzig"),
    "e": ("ewsp", {"etol", "etoa", "etoe"}, "emow"),
}
MINE_CAP = 5            # 一座矿最多 5 个农民（再多不涨收入）
LUMBER_CREW = 5         # 伐木的人数：每座矿 5 个采金 + 这么多伐木 = 工人目标
# ⚠ 09-24 实机：原来固定 15 个工人、矿满了剩下的全去砍树 —— 一矿时 10 个人砍树，22 分钟木头 2849、金子 7，兵出不来被打光。
#   人族前期金木比大约 5:3~5:5；工人目标跟着矿数走


class HelloBot(Bot):
    name = "hello"

    def on_start(self, g):
        halls = [u for u in g.my_buildings() if any(u.type in r[1] for r in RACES.values())]
        self.race = next((k for k, r in RACES.items() if halls and halls[0].type in r[1]), "h")
        self.worker, self.halls, self.farm = RACES[self.race]
        self.assigned = {}          # 工人地址 -> 矿地址（自己记账，快照看不见进了矿的工人）
        self.last_farm = -99.0
        g.message(f"hello bot 上线：种族 {self.race}")

    def on_tick(self, g):
        res = g.resources()
        if res is None:
            return
        halls = g.my_buildings(self.halls)
        if not halls:
            return
        home = halls[0]
        mines = [m for m in g.gold_mines() if g.dist(m, home) < 2500]

        # 1. 闲工人去采金（按自己记的账算每座矿几个人）；矿都满了就去砍树
        # ⚠ 别按快照剪账：进了金矿的工人不在快照里，剪掉就会往满矿里多派人
        crew = {}
        for w, m in self.assigned.items():
            crew[m] = crew.get(m, 0) + 1
        trees = None
        for w in g.idle_workers():
            open_mines = [m for m in mines if crew.get(m.addr, 0) < MINE_CAP]
            mine = g.nearest(open_mines, w)
            if mine:
                if g.gather(w, mine):
                    self.assigned[w.addr] = mine.addr
                    crew[mine.addr] = crew.get(mine.addr, 0) + 1
                continue
            if trees is None:
                trees = g.trees(home.x, home.y)       # 一拍最多扫一次
            for t in trees[:5]:
                if g.gather(w, t):
                    self.assigned[w.addr] = "lumber"
                    break

        # 2. 造工人：队列里只排 1 个（09-24 实机：每拍都下训练令，大厅排了 4 个农民、300 金锁在队列里，96 秒才多 1 个农民）
        # 木头见底而金子有富余 -> 多养 3 个伐木的（⚠ 09-24 实机：二三本时木头 26、金子 6526，骑士和升级全卡在木头上）
        lumber_crew = LUMBER_CREW + (3 if res["lumber"] < 100 and res["gold"] > 300 else 0)
        target = MINE_CAP * max(1, len(mines)) + lumber_crew
        if (len(g.my_workers()) < target and res["gold"] >= 75 and res["food_used"] < res["food_cap"]
                and not g.queue(home)):
            g.train(home, self.worker)

        # 3. 人口快满：盖人口建筑（离大厅 600 的一圈里试几个点，每 20 游戏秒最多试一次）
        #    有没盖完的人口建筑就等它（它盖好人口就上去了）；建造工不挑正在盖房子的
        now = g.clock() or 0.0
        building = [b for b in g.my_buildings({self.farm}) if g.under_construction(b)]
        # 人族/兽族的地基要工人一直在旁边盖；盖的人比地基少 = 有地基停工了，派个人去续建
        if self.race in ("h", "o") and building:
            busy = [w for w in g.my_workers() if g.is_constructing(w)]
            if len(busy) < len(building):
                helper = next((w for w in g.my_workers() if not g.is_constructing(w)), None)
                if helper is not None and g.repair(helper, building[len(busy)]):
                    self.assigned.pop(helper.addr, None)
        if (res["food_cap"] - res["food_used"] <= 6 and res["food_cap"] < 100 and now - self.last_farm > 20
                and not building):
            builder = next((w for w in g.my_workers() if not g.is_constructing(w)), None)
            if builder is not None:
                self.last_farm = now
                self.assigned.pop(builder.addr, None)       # 试点被拒也会让它停下，交给空闲派工
                g.build_near(builder, self.farm, home.x, home.y)

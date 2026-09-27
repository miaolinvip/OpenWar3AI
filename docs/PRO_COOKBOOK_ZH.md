# 职业打法食谱：每一招怎么用 OpenWar3 写出来

高手的优势大多来自几十个"小习惯"。这一页把常见的职业打法逐条落到 SDK 代码上 —— 每段都能直接抄进 `on_tick`。
接口细节见 `docs/BOT_HANDBOOK_ZH.md`（手册）和 `docs/API_ZH.md`（全表）；完整可跑的例子在 `brains/examples/`。

约定：`g` 是 `Game`，`home` 是我方主基地（`g.my_buildings({'htow','hkee','hcas'})[0]`），`now = g.clock()`。

---

## 一、运营

### 1. 农民永不空闲、一矿 5 人
```python
for w in g.idle_workers():                                   # 只派空闲的（有活的重下会打断采集）
    mine = g.nearest([m for m in g.gold_mines() if crew[m.addr] < 5], w)
    g.gather(w, mine) if mine else g.gather(w, g.trees(w.x, w.y, limit=1)[0])
```
自己记每座矿派了几个（`crew`）：进了金矿的工人不在快照里。整例见 `hello_bot.py`。

### 2. 队列只排 1 个，钱不锁死
```python
for b in g.my_buildings({'hbar'}):
    if not g.queue(b):                                       # 空了才排下一个
        g.train(b, 'hfoo')
```

### 3. 人口永远不卡
```python
stuck = any(p.blocked for _b, p in g.all_production('me'))  # 有排队但没开始 = 人口不够
res = g.resources()
if stuck or res['food_cap'] - res['food_used'] <= 6:
    g.build_near(builder, 'hhou', home.x, home.y)
```
`blocked` 比"快满了"早一步：打仗掉了一片兵、再补兵时队列一卡就知道。

### 4. 建造顺序 + 盖完自己回矿（Shift 回矿）
```python
spot = g.build_near(w, 'hbar', home.x, home.y)
if spot:
    g.gather(w, mine, queue='after')                         # 盖完回去采矿，不用下一拍再找它
```
一个农民连盖几座：`g.build_queue(w, [('hhou', x1, y1), ('hhou', x2, y2)])`。⚠ 钱在开工时才扣。

### 5. 升本时机 / 攻防升级
```python
if not g.queue(hall) and g.can_do(hall, 'hkee') in (0, 220):   # 大厅队列空了才能升（否则 185）
    g.upgrade(hall, 'hkee')
p = g.production(hall)                                          # 升本进度
if p and p.kind == 'upgrade':
    print(f"主城还要 {p.remaining:.0f} 秒")
for sm in g.my_buildings({'hbla'}):
    if not g.queue(sm):
        ok = [u for u, v in zip(UPS, g.can_do_many([(sm, u) for u in UPS])) if v in (0, 220)]
        if ok: g.research(sm, ok[0])
```

### 6. 开分矿：按走路距离选最近的矿
```python
mines = [m for m in g.gold_mines() if g.dist(m, home) > 1500 and not taken(m)]
best = min(mines, key=lambda m: g.path_distance(home, m) or 1e9)   # 岛上的矿返回 None -> 排到最后
```

---

## 二、侦察与信息

### 7. 看对手在干嘛
```python
for b, p in g.all_production('enemy'):          # 看得见的对手建筑在练什么/研究什么/升什么
    print(b.type, p.kind, p.queue, f"{p.progress:.0%}" if p.progress is not None else "卡住")
```
配合事件：`ev.kind == 'production.done' and ev.owner != g.me()` —— 对手刚出了什么。

### 8. 记住看到过的东西（战争迷雾）
```python
for u, t, age in g.last_seen(max_age=60):       # 60 游戏秒内看到过的敌人（最后的位置和血量）
    ...
hero_seen = [r for r in g.last_seen() if r[0].is_hero]   # 对面英雄上次在哪
```
公平模式（`--fair`）下这是你唯一的"对手信息来源"，和人类玩家一样。

### 9. 电脑对手要打哪（只对电脑 AI 有效）
```python
plan = g.enemy_ai_plan(some_enemy_soldier)      # {'goal_x','goal_y',...}：它的队长要去的地方
```
电脑出门前就定好了目标点 —— 提前把兵带回那里。

---

## 三、打野

### 9b. 维护费：卡在 50 人口
```python
u = g.upkeep()
if u['level'] == 'none' and g.resources()['food_used'] >= 48 and not big_fight_soon:
    pass                                         # 攒钱升本/攻防，先别出兵；决战前才一口气上到 80
```

### 10. 夜里打野
```python
if g.is_night():                                 # 18 点~6 点：野怪睡觉（先手不被围）、所有人视野变短
    ...
wait = g.seconds_until(18)                       # 离天黑还有几游戏秒（一天 480 秒）
```

### 10b. 挑营地、算经验
```python
for camp in g.creep_camps():                     # 由近到远；level = 营地总等级
    if camp['level'] <= 12 and g.reachable(home, (camp['x'], camp['y'])):
        break
xp = sum(g.creep_xp(lv, hero.level) for lv in camp_levels)   # 英雄 5 级以后打野 0 经验 —— 5 级就该去打人了
need = g.xp_to_next(hero)
```

### 11. 只打打得过的点
```python
from openwar3 import combat
mine = [g.stats(u) for u in army]
def ttk(target): return combat.time_to_kill(mine, g.stats(target), target_hp=target.hp) or 1e9
camp = [c for c in g.creeps() if g.dist(c, center) < 600]
ours = max(ttk(c) for c in camp)                                 # 打光这个点要多久（粗算）
theirs = g.time_to_kill(camp, weakest_of_mine) or 1e9            # 它们打死我方最弱的要多久
if ours < theirs and g.reachable(center, camp[0]):
    g.attack_move(army, camp[0].x, camp[0].y)
```
整例：`micro_bot.py` 的 `_maybe_creep`。

---

## 四、微操

### 12. 集火：打"最快能打死的"，不是最近的
```python
target = min(visible_enemies, key=lambda e: g.time_to_kill(fighters, e) or 1e9)
g.attack([u for u in fighters if (g.current_target(u) or target).handle != target.handle], target)
```
只给"没在打它"的人下令（`current_target`），别打断已经在打的。

### 13. 拉残血
```python
for u in army:
    if u.hp < u.hp_max * 0.35:
        g.move(u, *toward(home, u, 500))       # 往家的方向退 500；3 秒内别重复拉
```
被集火的判断：事件 `damage` 里 `ev.handle == u.handle` 短时间内多条来源 = 被围了。

### 14. 英雄保命、别送经验
```python
for h in g.my_heroes():
    if h.hp < h.hp_max * 0.4:
        g.move(h, home.x, home.y)
        g.use_item(h, slot_of(h, 'phea'))       # 血瓶：inventory(h) 找格号
```

### 15. 克制：让对的兵打对的目标
```python
s = g.stats(u)
best = max(enemies, key=lambda e: s.dps_vs(g.stats(e)))    # 火枪打狮鹫（穿刺打小甲 ×2）、狮鹫打步兵（魔法打大甲 ×2）
```
克制表来自游戏数据：`combat.damage_multiplier('pierce', 'small') == 2.0`。

### 16. 包夹与路径：绕过塔走
```python
route = g.walk_path(army_center, target)        # 地面最短路的拐点
g.path(army, route, attack=True)                # 按顺序攻击移动过每个点
```
要绕开塔：在寻路网格上把塔周围标成不能走再算：
```python
grid = g.grid().copy()
for t in towers:
    grid.block_area(t.x, t.y, 800)             # 塔射程 700 + 余量
route = grid.path((army_x, army_y), (target.x, target.y))
```

### 17. 一拍的命令一批发
```python
with g.batch():
    g.attack(melee, target_a)
    g.attack(ranged, target_b)
    g.move(wounded, *home_xy)
    g.cast(hero, 'thunderclap')
```
几十条命令只等一次游戏线程（实测 8 条移动 68 ms -> 6.5 ms）。

### 18. 攻城：炮打地面
```python
g.attack_ground(mortars, tower.x, tower.y)       # 迫击炮/投石车对一块地方开火（树林后面、隐形单位）
```

---

## 五、英雄

### 19. 加点表
```python
SKILLS = {'Hamg': ['AHwe', 'AHbz', 'AHwe', 'AHbz', 'AHwe', 'AHmt'], ...}   # 水元素、暴风雪…6 级大招群体传送
info = g.hero_info(h)
if info and info['skill_points']:
    g.learn(h, SKILLS[h.type][learned_count])  # 被拒（等级不够学大招）就等下一级
```

### 20. 技能放没放出来
```python
r = g.cast(h, 'thunderbolt', target=enemy_hero)
# 下一拍：
if g.cooldown(h, 'AHtb'):                         # 进冷却 = 真放出去了；接下 ≠ 放出
    ...
```

### 21. 买药 / 回城 / 递药 / 卖东西
```python
g.buy(shop, 'phea')                               # 英雄站在商店旁边
g.use_item(hero, slot, x=home.x, y=home.y)        # 回城卷轴（对点用物品）
g.give_item(hero, slot, other_hero)               # 把血瓶递给残血的队友英雄（走过去递）
g.sell_item(hero, slot, shop)                     # 打野掉的用不上的东西卖掉换钱
g.move_item(hero, slot, 0)                        # 常用的放第 0 格（整理快捷键位）
g.drop_item(hero, slot, x, y)                     # 背包满了先放地上，捡更好的
```
格号从 `g.inventory(hero)` 看（6 格，空格是 None）。

---

## 六、复盘和调试

* 每拍把决策写进日志（`print` 会进运行窗口），配合 `g.say(单位, '撤')` 在游戏里看。
* `production.done` 事件带"用了多少秒" —— 统计自己的建造顺序时间线（第几秒出第一个英雄、第几秒升本），和高手的对比。

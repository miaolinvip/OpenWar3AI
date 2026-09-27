# Bot 作者手册（OpenWar3 / 魔兽争霸 III 1.27）

写给想做自己 AI 的人：看完这一页就能写出一个会运营、会打架的 bot；想让大模型帮你写，把这一页 + `docs/api.json` + 一个示例 bot 一起贴给它。
全部接口（每个都标了"实机验证过 / 推断"和底层来源）在 `docs/API_ZH.md`；职业打法怎么落到代码见 `docs/PRO_COOKBOOK_ZH.md`。

---

## 0. 五分钟跑起来

```bash
python tools/play.py --bot brains/examples/hello_bot.py            # 起一局 + 接上 bot（只管经济）
python tools/play.py --bot brains/examples/macro_bot.py --speed 200  # 运营：建造顺序、队列、攻防、出击
python tools/play.py --bot brains/examples/micro_bot.py --fair       # 再加微操：集火、拉残血、夜里打野；公平模式
python tools/play.py --bot my_bot.py --inst 9 --attach               # 游戏已经开着，只接 bot
```

最小的 bot：

```python
from openwar3 import Bot

class MyBot(Bot):
    def on_start(self, g):          # 进局后调一次
        g.message("我来了")

    def on_tick(self, g):           # 每秒约 5 次
        for w in g.idle_workers():
            g.gather(w, g.nearest(g.gold_mines(), w))

    def on_event(self, g, ev):      # 每拍之前，上一拍以来的事件逐条给你
        if ev.kind == "production.done" and ev.owner == g.me():
            print("做完了", ev.done_kind, ev.done_code)
```

| 示例 | 学什么 |
|---|---|
| `brains/examples/hello_bot.py` | 采集（一矿 5 人、满了砍树）、造农民（队列只排 1 个）、盖人口建筑、续建停工的地基 |
| `brains/examples/rush_bot.py` | 兵营、英雄（死了复活）、攒够一波攻击移动 |
| `brains/examples/macro_bot.py` | 建造顺序 + 盖完回矿、人口卡住立刻补、兵营队列 1 个、攻防升级、按地面真实路程选目标走路径 |
| `brains/examples/micro_bot.py` | 集火最快能打死的、拉残血、英雄保命、夜里挑打得过的野怪点、回防、一拍的命令一批发 |

⚠ **示例的实力（09-24 实测，公平模式、2 倍速、Echo Isles、简单电脑）**：micro_bot 能盖齐建筑、升到三本、攒到 40+ 人口出击，
但**大约 20 分钟被推平** —— 它没有侦察、不开矿、不打野练级、一本兵硬冲有塔的主基地。示例是教"怎么用接口"的，不是冠军 bot。
想看一个能赢的完整 AI：`brains/xwar3/strategy`（参考大脑，对简单电脑约 8 成胜率；它用的是更底层的接口，思路可以照搬）。
把示例练强的路线就是食谱里的招数：侦察（last_seen）、夜里打野练英雄、开分矿、比兵力再出门、拉残血集火。

**做成方案、一键切换、分享给别人**：bot 文件旁边放一份 `scheme.json`（最少 `{"id": "my-first", "entry": "my_bot.py"}`），
整个文件夹放进 `schemes/mine/`，远见「AI 方案」页就能给任何实例用上、导出 zip 发给别人；战绩按方案自动统计。
最快的起点是在那一页把内置示例「复制到我的」再改。详见 [AI 方案](SCHEMES_ZH.md)。

`openwar3.run` 怎么判一局结束（方案运行器和 `tools/play.py` 共用）：对面建筑全没了 = 胜；我方建筑全没了或单位全没了 = 负；
暂停（时钟不走）照常跑、不算结束，连续 `stuck_s`（默认 300）墙钟秒时钟不走才收工；`end_game=True` 时分出胜负后发结束令关掉结算画面。

---

## 1. 心智模型：快照、命令、事件、一拍

* **快照（读，零等待）**：运行时每 50 ms 把整个世界推到共享内存。`g.snapshot()` 拿到的是一份完整、自洽的世界
  （全部单位、玩家资源、地上物品、生产表、游戏时钟、游戏内时间）。读一次约 0.4 ms，**随便读**。
* **命令（写，一帧）**：`g.move / attack / train / build / cast …` 交给游戏线程执行，每条约等一帧（~10 ms）。
  每条命令返回一张**回执** `Receipt`：`if r:` = 引擎接下了；没接下时 `r.reason` 写着原因（人口不够、缺金、缺前置、目标看不见……）。
  一拍要下很多条命令时用 `with g.batch():` —— **几十条只等一次**。
* **事件**：单位出现/死亡/掉血、换订单、英雄升级、物品出现/消失、**每一下伤害**（谁打谁、攻击类型、护甲前伤害）、
  **生产完成**（训练/研究/建造/升级）。`on_event` 里逐条给你。事件流是全局的：对手的事件也在里面，按 `ev.owner` 过滤。
* **一拍**：`on_tick` 默认每秒 5 次（2 倍速下 = 每 0.4 游戏秒）。一拍超时会自动顺延，不会越积越多。

---

## 2. 接口速查（按"我想做什么"）

### 看局面
| 想知道 | 用 |
|---|---|
| 我方资源 / 人口 | `g.resources()` -> `{'gold','lumber','food_used','food_cap',...}`（读不到是 None） |
| 各类单位 | `g.my_workers() / idle_workers() / my_army() / my_heroes() / my_buildings({'hbar'}) / enemies() / creeps() / gold_mines()` |
| 按条件筛 | `g.units(owner='me'/'enemy'/'creep'/'all'/玩家号, types={'hfoo'})` |
| 单位细节 | `u.hp / hp_max / mana / x / y / order / level`，`g.abilities(u) / buffs(u) / cooldown(u,'AHtb') / inventory(hero)` |
| 它在打谁 | `g.current_target(u)`（攻击挂在任务上，别看订单槽） |
| 建筑在造什么、还要多久 | `g.production(b)` -> `.kind .queue .progress .remaining .blocked`；`g.queue(b)`；`g.all_production('enemy')` 看对手 |
| 能不能做 / 为什么不能 | `g.can_do(u, 'hkni')` 返回原因码；一次问很多用 `g.can_do_many([(u,code),...])` |
| 科技等级 | `g.tech('Rhme')`，一次查很多 `g.tech_many([...])` |
| 战斗属性 | `g.stats(u)` -> 血/甲/护甲类型/移速/视野/武器（射程、间隔、伤害区间、攻击类型、能打空中吗）；自动算上攻防升级和英雄等级 |
| 打多久能打死 | `g.time_to_kill(我的一群, 目标)`（克制表、护甲、升级、目标实时血量） |
| 地图 | `g.map()` -> `.walkable(x,y) .buildable(x,y) .starts .bounds`；`g.trees(x, y)` |
| 走不走得到 / 多远 | `g.reachable(a, b)`、`g.path_distance(a, b)`、`g.walk_path(a, b)`（地面 A*，绕树林悬崖建筑） |
| 白天黑夜 | `g.time_of_day()`、`g.is_night()`、`g.seconds_until(18)` |
| 看得见吗 | 单位 `u.visible_to(g.me())`；任意点 `g.visible(x, y)` |
| 敌人上次在哪 | `g.last_seen()` -> `[(单位当时的样子, 时刻, 过去几秒)]` |
| 电脑对手要打哪 | `g.enemy_ai_plan(敌兵)`（只对电脑 AI 有效：它的集结点和进攻目标） |
| 野怪营地 | `g.creep_camps()` -> `[{'x','y','units','level','hp'}]`（由近到远）；打一只野怪英雄拿多少经验 `g.creep_xp(野怪等级, 英雄等级)` |
| 英雄升级 | `g.xp_to_next(hero)`（离下一级差多少经验）、`g.hero_xp_needed(等级)` |
| 维护费 | `g.upkeep()` -> `{'level': 'none'/'low'/'high', 'income': 1.0/0.7/0.4, 'next_at': 51}` |
| buff 是什么 | `g.buff_info('Bslo')` -> 来源技能、效果、持续时间 |

### 做事
| 想做 | 用 |
|---|---|
| 采集 | `g.gather(workers, 金矿或树)` —— 只给 `idle_workers()` 派 |
| 盖房子 | `g.build_near(worker, 'hbar', x, y)`（自己找放得下的点、跟踪成败）；连造 `g.build_queue(worker, [(码,x,y),...])` |
| 训练 / 研究 / 升本 | `g.train(建筑, 码)`（`research`、`upgrade` 是同一个）；取消 `g.cancel(建筑)` |
| 英雄 | `g.learn(hero, 'AHbz')`、死了 `g.revive(祭坛)` |
| 移动 / 攻击 | `g.move / attack_move / attack(单位或列表, ...)`、`hold / stop / patrol / attack_ground` |
| 排队（Shift） | 上面这些加 `queue='after'`（做完手上这件再做）；按顺序走一串点 `g.path(units, [点...], attack=True)` |
| 施法 | `g.cast(u, 'thunderbolt', target=敌人)` / `cast(u, 'blizzard', x=, y=)` / `cast(u, 'thunderclap')` |
| 物品 | `g.pick_up(hero, item)`、`g.use_item(hero, 格号, target=/x=,y=)`、`g.buy(商店, 'phea')`；离手：`drop_item(hero, 格号, x, y)`、`give_item(hero, 格号, 对方)`、`sell_item(hero, 格号, 商店)`、`move_item(hero, 格号, 新格号)` |
| 集结点 | `g.rally(建筑, x, y)` 或 `rally(建筑, target=金矿)` |
| 一批发 | `with g.batch(): ...`（块里的命令返回 Pending，块结束后变成回执） |

---

## 3. 十五条规矩（每一条都是实机踩出来的）

1. **读不到是 None，不是 0。** `resources()`、`time_of_day()`、`production()` 都可能是 None，先判断。
2. **回执"接下" ≠ 做成。** 树林里的落点引擎也当场接下，工人走到才失败；技能可能被打断。效果看快照和事件
   （盖房用 `build_near`，它跟踪结果；放技能看 `cooldown()` 有没有进冷却）。
3. **看不见的目标不能打**（原因码 1001）。对迷雾里的敌人用 `attack_move` 到它的位置。
4. **只给闲着的单位下令。** 每拍给同一个单位重下同一条命令会打断它（兵在原地抽搐、农民采集周期归零）。
   判断"闲不闲"用 `g.order_of(u)`（含你这一拍刚下的），别用快照里的 `u.order`（快照还没追上）。
5. **一矿最多 5 个工人**，多了不涨收入。进了金矿的工人**不在快照里**（自己记账）。
6. **队列只排 1 个。** 排满 7 格会把钱锁在队列里（实测大厅排了 4 个农民、300 金锁住，开局慢一大截）。用 `g.queue(b)`。
7. **人口卡住看生产表**：`production(b).blocked` = 有排队但没开始（人口不够）。比"快满了再盖"早一步。
8. **英雄唯一**：死了只能 `revive`，再训练会被拒（221）。复活要人口（英雄占 5）。
9. **大厅队列没空时不能升本**（原因码 185"建筑正忙"）。
10. **事件流是全局的**：`production.done`、`unit.died` 里有对手的，按 `ev.owner` 或建筑句柄过滤。
11. **Shift 只有"插在当前后面"**：引擎没有"追加到末尾"，连着 `queue='after'` 发 B、C 会得到 A、C、B。按顺序走一串点用 `g.path`。
12. **用句柄认单位，别用地址**：地址会被新单位复用。`u.handle`、`g.unit(handle)`。
13. **公平模式**（`--fair`）：只看得见视野里的单位、物品、生产和事件；擂台就是这个规则。现在就按公平模式写，将来不用改。
14. **2 倍速下别按墙钟等**：想等 3 游戏秒就看 `g.clock()` 涨了 3，不是 `sleep(1.5)`。
15. **岛图/树林图别用直线距离**：`g.path_distance` 走不到返回 None；野点/分矿按它选。

---

## 4. 调试和性能

* 回执：`print(r.reason)`；`r.exec_us` 是这条命令在游戏线程上花的微秒数（正常几微秒到几百微秒）。
* 游戏里显示：`g.say(单位, '冲')`（头顶气泡）、`g.message('...')`（左下角消息）。
* 一拍慢：先看是不是一条一条发命令 —— 包进 `with g.batch():`；再看有没有逐个调 `g.visible()` / `g.can_do()`（每次等一帧），
  改用快照里的 `u.visible_to()` 和 `can_do_many`。`g.stats()` 的科技等级 5 秒缓存一次，放心调。
---

## 5. 数据从哪来（出了怪事时查）

| 东西 | 来源 | 精度 |
|---|---|---|
| 单位、资源、订单、技能、buff、背包 | 运行时每 50 ms 推送的世界块 | 发布周期 |
| 伤害、击杀事件 | 运行时在游戏线程上当场记下，每一下 | 当场 |
| 生产表 | 引擎里的生产 / 建造 / 升级进度 + 运行时累加已进行时间 | ±0.2 游戏秒 |
| 战斗属性、克制表 | 游戏自带的数据表（`data/game/`，`python data/tools/extract_game_data.py` 从本机游戏提取） | 不含物品/光环/buff |
| 寻路 | 引擎的地形可走性（128 一格）+ 树 + 建筑占地，SDK 侧 A* | 一格；窄于一格的缝判不通 |
| 游戏内时间 | JASS `GetFloatGameState(GAME_STATE_TIME_OF_DAY)` | 发布周期 |

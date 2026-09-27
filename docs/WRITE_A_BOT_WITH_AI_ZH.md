# 不写代码，让 AI 帮你写一个陪玩 AI

适合：会玩魔兽、不会编程的人。你负责说清楚"想要它怎么打"，大模型负责写代码。

## 1. 准备（一次）

1. 按根目录 `README_ZH.md` 装好环境、能起游戏；
2. 先跑通示例：`python tools/play.py --bot brains/examples/hello_bot.py`，看到农民去采矿就对了。

## 2. 把下面这段发给大模型（Claude / ChatGPT / 本地模型都行）

把 `docs/BOT_HANDBOOK_ZH.md`（手册）、`docs/api.json`（全部接口）和一个示例（运营看 `brains/examples/macro_bot.py`，
打架看 `micro_bot.py`）一起贴在后面；想要某种职业打法，再把 `docs/PRO_COOKBOOK_ZH.md` 里对应那一招贴上：

```
你要为《魔兽争霸 III》1.27 写一个 AI（Python）。只能使用下面 api.json 里列出的 Game 方法，
不要编造不存在的方法。写法照 rush_bot.py：继承 openwar3.Bot，实现 on_start(g) 和 on_tick(g)。

规则：
- on_tick 每秒大约调 5 次，要快（别在里面 sleep）。
- 读不到的值是 None，不是 0，先判断再用。
- 命令返回一张回执（Receipt），`if r:` 就是"引擎接下了"；没接下时 `r.reason` 写着原因
  （人口不够、金不够、目标看不见、这个英雄已经有了……），下一拍再试或者换个做法。
- 攻击一个具体的敌人用 g.attack(兵, 敌人)；敌人必须在视野里，看不见的会被拒。
- 英雄死了要用 g.revive(祭坛) 复活，不能再训练一个。
- 盖房子用 g.build_near(工人, 建筑码, x, y)：它会自己找放得下的点、跟踪结果，钱不够时什么也不做。
- 想知道"刚才发生了什么"（谁死了、谁掉血、英雄升级、物品掉了）就实现 on_event(g, ev)。
- 不要每一拍都给同一个单位重复下同一条命令（会打断它正在做的事）；给"闲着的"下令。
- 采集只给 idle_workers() 里的工人派。一座金矿最多 5 个工人。
- 训练队列只排 1 个（g.queue(建筑) 空了再排）；人口卡住看 g.production(建筑).blocked。
- 一拍要下很多条命令时包在 with g.batch(): 里（只等一次游戏线程）。
- 选打谁用 g.time_to_kill(我的一群, 敌人)（算上克制和护甲）；选去哪用 g.path_distance（走不到返回 None）。
- 公平模式下只看得见视野里的东西；以前看到过的敌人用 g.last_seen()。
- 单位用四字码表示（人族农民 hpea、步兵 hfoo、兵营 hbar……），技能用订单名（thunderbolt 风暴之锤、blizzard 暴风雪、holybolt 圣光术……，
  全表在 data/order-ids.txt），技能学习用四字码（AHtb、AHbz……）。

我想要的打法：
<在这里用大白话写，比如：
  "人族，开局 5 农民采金 1 个伐木；先出大法师；两个兵营出步兵和火枪；
   攒到 12 个兵带着英雄去打对面分矿；英雄血低于 30% 就撤回家；
   打野时优先打离家近的营地。">
```

## 3. 跑起来

把大模型给的代码存成 `brains/my_bot.py`，然后：

```bash
python tools/play.py --bot brains/my_bot.py --race 1 --difficulty 2
```

报错就把**整段报错**原样贴回给大模型，说"改一下"。
打得不好就描述你**在游戏里看到了什么**（"英雄一直站在家里不动""兵一个一个送上去"），让它改。

## 4. 常见问题

| 现象 | 多半是 |
|---|---|
| 什么都不动 | 实例号不对（`--inst`），或者游戏还没进局 |
| 农民不采矿 | 给了正在干活的农民下令；用 `idle_workers()` |
| 一直盖不出房子 | 用 build_near，别写死坐标；看回执 reason 是不是钱不够 |
| 英雄不出来 | 看 train 的回执：人口不够？还是英雄死了（要 revive）？ |
| 英雄不放技能 | 没学（`learn`）或者没蓝；放完看 `cooldown()` 有没有进冷却 |
| 兵一拍一拍地抽搐 | 每拍都在重下命令；只给闲着的单位下令 |

## 5. 进阶

* 全部接口和每个接口的底层机制：`docs/API_ZH.md`；
* 参考大脑（`brains/xwar3/strategy`）是一个完整的、会开矿打野出击的 AI，可以读它的思路，但它用的是更底层的接口；
* 以后上擂台（`docs/ARENA_ZH.md`）时，只能看见视野内的敌人 —— 现在就用 `visible(x, y)` 自我约束，将来不用改。

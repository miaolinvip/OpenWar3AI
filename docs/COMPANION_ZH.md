# RPG 辅助与玩伴（09-24）

在 RPG / 自定义地图里给玩家配一个 AI 伙伴：跟着你、帮你打怪、你残血给你加血、陪你说话。
**怎么用由玩家自己决定**。这件事分三层接口，从底到顶，哪层都能直接用：

| 层 | 是什么 | 适合谁 |
|---|---|---|
| **JASS 通道** `g.jass.任意native(...)` | 地图作者能用的 1291 个 JASS 函数，按名字直接调（造单位、设盟友、给物品、改名、显示文字、复活英雄……） | 想自己造玩法的人 |
| **便捷接口** `g.spawn` `g.set_alliance` `g.player_slots` `g.show_text` `g.map_data` … | 常用的几件事包好了 | 写自己的辅助脚本 |
| **玩伴框架** `openwar3.companion.Companion` + `openwar3.talk.Talk` | 继承一下、改几个属性就是一个会跟随/助战/加血/聊天的伙伴 | 想要一个伙伴的人 |

⚠ **只用于单机、局域网自建的游戏**（不上战网、不上对战平台）。造单位、改名、设盟友这些是本机单方面改世界：
单人局（和本机的电脑打）没问题；**多人局会让别的玩家不同步被踢**，所以 JASS 通道在多人局里只放行只读的函数，玩伴自动退成只说话。

## 1. 最快上手：远见里一键开

1. 实例页「下一局设置」→ 点地图，选一张 RPG 地图（`Scenario`、`Download` 文件夹里的都列出来了，比如 `(4)WarChasers`）。
2. 实例卡片「AI 方案」下拉选 **玩伴示例（buddy）** →「选定」。
3. 「开始测试」→ 游戏起来后**你自己在游戏窗口里玩**，玩伴（一个叫「小圣」的圣骑士）会出现在你身边。

命令行：

```bash
python tools/play.py --bot brains/examples/buddy.py --inst 20 --rpg --map "<游戏目录>\Maps\Scenario\(4)WarChasers.w3m"
```

`--rpg`（方案里是 `"judge": false`）= 不按对战规则判胜负：RPG 里英雄死了能复活、没有「建筑全没了算输」。

很多 RPG / 剧情地图载入完停在「**按下任意键以继续**」：`openwar3.run` 看到「在局里但时钟一直是 0」会自己按一下空格
（`g.press_to_continue()`，往游戏窗口发按键消息，不抢焦点）。

## 2. 玩伴框架

```python
from openwar3.companion import Companion
from openwar3.talk import Talk, LocalLLM

class MyBuddy(Companion):
    mode = "ally"                 # 形态
    unit = "Hpal"                 # 造什么（任何四字码，地图自定义的也行）
    nickname = "小圣"
    heal = ("holybolt", "AHhb", 0.55)     # (施法订单名, 要学的技能, 主人血量低于多少加血)；None = 不加血
    follow_distance = 350
    talk = Talk(persona="活泼的小圣骑士，爱给主人加油")
```

### 形态（mode）

| mode | 玩伴是谁 | 说明 |
|---|---|---|
| `ally`（默认） | 空着的真人槽当一个**盟友玩家** | 有自己的颜色和名字（计分板/盟友面板显示 nickname），你点不到它，它自己打；框架自动设成同盟 + 共享视野 |
| `own` | 造在**你名下** | 你随时能手动指挥它，你不管的时候 AI 替你操作 |
| `adopt` | 接管地图里**已有**的单位 | 覆盖 `adopt(g)` 返回那个单位（地图给你的宠物、随从） |
| `voice` | 不造单位，**只说话** | 陪聊、提醒；不改世界，多人局也能用 |

没有空槽时 `ally` 自动退成 `own`；多人局 / 造不出来时自动退成 `voice`。

⚠ **ally 形态的玩伴按「那个槽现在最好的单位」认，不按地址死认**（英雄优先）。09-24 WarChasers 实测：玩伴跟着主人的小精灵走进选英雄的能量圈，
**地图把它当成一个真人玩家，删了圣骑士、给 1 号发了一个地图英雄** —— 现在玩伴会直接接管地图给的英雄（日志「玩伴换成了 MegoTron X」），
也会学地图给这个英雄定的技能（w3u 的 `uhab`）。英雄死了优先原地复活它（`ReviveHero`），地图自己复活了就直接接着用。

### 行为（每拍按顺序，哪个成立做哪个）

| 顺序 | 行为 | 条件 | 可调 |
|---|---|---|---|
| 1 | 撤退 | 自己血 < `retreat_at`（25%）且附近有敌人：退到主人身后（背对最近的敌人） | `retreat_at` |
| 2 | 加血 | 主人血 < `heal[2]` 且技能冷却好了、距离 900 内 | `heal`（None 关掉） |
| 3 | 助战 | 主人身边 `assist_radius` 内有敌人：**正在打主人的 > 主人正在打的 > 最近的** | `assist_radius`、覆盖 `pick_target` |
| 4 | 跟随 | 离主人 > `follow_distance`：右键跟随；> `leash` 直接跑回来不恋战 | `follow_distance` `leash` |
| 5 | 闲聊 | 没敌人时隔 1~2.5 分钟说一句 | 台词表 |

「敌人」按 **JASS 的同盟关系**（`IsPlayerEnemy`）算，20 秒刷新一次 —— RPG 地图常有好几家盟友，
`g.units("enemy")`（= 除我以外的玩家，对战地图的 1v1 假设）在这里不对。

钩子（子类覆盖）：`find_master`（谁是主人，默认本机等级最高的英雄）、`adopt`、`pick_target`、`on_poke`（主人右键点了玩伴）、
以及 Bot 的 `on_start / on_tick / on_event / on_end`。统计在 `self.stats`（加血/助战/击杀/跟随/撤退/说话/复活次数），结束时打印。

**聊天命令（09-24 深夜接上）**：聊天框打 `-follow` 跟着 / `-stay` 原地守着 / `-heal` 马上加血 / `-hi` 打招呼（中文 `-跟随` `-停` `-加血` 待真人用输入法验证）。
读不到任意聊天文字，但每条命令一个空触发器、数执行次数就知道打的是哪条（`docs/JASS_API_ZH.md` §4）；改命令表 = 改 `commands`，改反应 = 覆盖 `on_command`。
打招呼、主人倒下、升级、玩伴回来这几句用**头像对白**说（底部头像换成玩伴 + 屏幕字幕，`scene_events`），其余冒头顶气泡。

**状态面板（09-25）**：左边一块面板（画板，运行时自绘、多人局也安全）显示玩伴的血条、正在干什么、心情（开心 / 兴奋 / 紧张 / 害怕 / 难过，按局面粗判）、
击杀和加血次数（`panel_at` 改位置，None 关掉；`docs/CANVAS_ZH.md`）。

**主人怎么叫玩伴（另一种）**：**右键点玩伴** —— 主人手上是右键订单、目标是玩伴时触发 `on_poke`
（只比目标时打架中误触发过两次，现在要求订单号是右键 smart）。示例里右键点它 = 主人没满血就给主人加一口血，否则说句话。
⚠ **地图脚本给主人下的右键也会被当成「叫玩伴」**：09-24 WarChasers 没人操作时出现过一次（地图把玩伴那个槽当成真人队友，
这类地图常让英雄互相跟随）。代价只是多说一句话；每次触发日志里都有「主人右键点了玩伴（主人订单 …）」，拿不准就看这行。

### 说话（openwar3.talk）

`Talk` 按事件挑台词，头顶冒气泡（`g.say`）；voice 形态或冒不出气泡时在屏幕左下角显示（`g.show_text`，支持 `|cffRRGGBB` 颜色）。
每句话同时打到方案日志 `bin/inst<N>/scheme_bot.out`（`[buddy] 说：……`），事后能查它说过什么。

| 事件 | 什么时候 | 事件 | 什么时候 |
|---|---|---|---|
| hello | 刚来 | master_low | 主人残血 |
| poke | 主人右键点玩伴 | master_levelup | 主人升级 |
| fight | 开打 | master_died / master_back | 主人倒下 / 复活 |
| kill | 玩伴打死一个（说出怪的名字） | buddy_low / buddy_died / buddy_back | 玩伴自己残血 / 倒下 / 回来 |
| healed | 给主人加了血 | idle / item | 闲聊 / 捡到东西 |

台词里能用 `{master} {me} {map} {enemy} {level} {item} {respawn}`；改台词直接改 `talk.lines[事件]`；冷却在 `talk.cooldown`。

**接本地大模型**（提供情绪价值的那部分）：`Talk(llm=LocalLLM(url, model))`，OpenAI 兼容接口都行
（LM Studio `http://127.0.0.1:1234/v1/chat/completions`、Ollama `http://127.0.0.1:11434/v1/chat/completions`）。
模型在后台线程里答，答回来才说；没开 / 超时 / 出错就说固定台词，不卡游戏。示例里设环境变量 `OPENWAR3_LLM_URL` 就会用。
请求只发到你给的本机地址，内容是游戏里的事（主人叫什么、打了什么怪）。

## 3. 地图数据：自定义单位的名字

RPG 地图的单位/物品/英雄大多是地图自己新建的（四字码像 `HC07`、`I00A`），内置名字表里没有。`g.map_data` 读当前这局的地图文件：

```python
md = g.map_data                 # 按游戏进程找启动器的 --map（游戏提权跑、读不到它的命令行）
md.name_of("HC07")              # 'Optimus Primo'：地图改过的名字 > 保护图的 TXT > 内置名字表 > 四字码
md.hero_names("HC07")           # 称号列表
md.hero_skills("OC10")          # ['AOwk', 'AHds', 'AEah', 'AEtq']：地图给这个英雄定的技能
md.tooltip("I00A")              # 说明文字
```

读的是 `war3map.w3u/w3t/w3a/w3h` + `war3map.wts`；**保护/优化过的地图**（DotA、很多国产 RPG）不带这些，
物体数据合并进了图里的 SLK + TXT，名字从 `Units\*Strings.txt` / `*Func.txt` 读（`[四字码]` 下面 `Name=`、`Propernames=`）。
本机 38 张 RPG/自定义地图全部解析成功，37 张拿得到名字（真三国无双的赵云/马超、DotA 的全能骑士、仙之侠道的云渺、犬夜叉……），
热血传奇那张图里没有名字数据。MPQ 读取在 `sdk/python/w3mpq.py`（远见地图库也用这一份）。

## 4. JASS 通道

```python
j = g.jass
p1 = j.Player(1)
h = j.CreateUnit(p1, "Hpal", 100.0, 200.0, 270.0)          # 返回 JASS 单位句柄
u = j.unit_of(h)                                           # -> 快照里的单位
j.SetPlayerAlliance(p1, j.Player(0), 5, True)              # 5 = ALLIANCE_SHARED_VISION（常量表见 openwar3/jass.py）
j.SetUnitOwner(some_unit, p1, True)                        # 快照里的单位/物品直接传，自动换成 JASS 句柄
j.DisplayTimedTextToPlayer(j.GetLocalPlayer(), 0.0, 0.0, 5.0, "|cff00ff00你好|r")
j.signature("CreateUnit")                                  # '(Hplayer;IRRR)Hunit;'
```

| 参数/返回 | 怎么传 |
|---|---|
| I 整数 | int；四字码字符串 `"Hpal"` 自动转 |
| R 实数 | float（运行时转成引擎要的 real*） |
| B 布尔 | bool |
| S 字符串 | str（只适合只读字符串的 native：显示文字、改名、比较；哈希表/游戏缓存会把字符串存起来，别用） |
| H 句柄 | int，或者快照里的单位/物品对象 |
| 返回 | I/H -> int，R -> float，B -> bool，S -> None（引擎返回的是字符串表编号，读不回文字） |

不支持：带 `code` 参数的（要一个 JASS 函数）、会挂起脚本线程的（TriggerSleepAction、Sync*、ExecuteFunc…）。
只在 `Game()` 默认的 dev 车道能用；`Game(player=N)`、公平模式（擂台规则）里调会被拒。一次调用约 13 ms（含句柄转换）。
协议：W3P 操作码 70/71/72（`docs/W3P_PROTOCOL_ZH.md`）；名字 -> 地址的表只在闭源运行时里，开放层只有签名表 `jass_signatures.json`。

便捷接口（都在 `docs/API_ZH.md` 的「沙盒」一节）：`g.player_slots()`（16 个槽：谁控制、在不在玩、是不是盟友）、
`g.spawn(code, x, y, player)`、`g.set_alliance(a, b, allied, vision, control, xp)`、`g.set_player_name`、`g.show_text`、`g.press_to_continue`。

## 5. 做成方案分享

玩伴就是一个 `openwar3.Bot` 子类，可以做成方案（`docs/SCHEMES_ZH.md`）分享给别人。说明书里要多写两项：

```json
{"id": "my-buddy", "name": "我的玩伴", "entry": "my_buddy.py", "fair": false, "judge": false}
```

`fair: false`：要用 JASS 通道（造单位、设盟友）；`judge: false`：不按对战规则判胜负。内置的 `builtin/buddy` 就是这么配的。

## 6. 实测记录和还没做的

**09-24 实测（20 号演练实例，WarChasers，2 倍速）**
* JASS 通道 18 项检查全过：槽位（0 号是我，1/5/6 空着且是盟友）、单位↔句柄往返、real 返回、字符串参数、给空槽造单位、设盟友、改名、
  1 号玩家车道指挥它、删单位；玩家车道调 JASS / TriggerSleepAction / 参数个数不对都被拒。
* 玩伴：自己按过「按任意键继续」→ 出现在小精灵旁边说「圣光在上，我会一直跟着你的！」→ 跟进选英雄的能量圈、被地图发了英雄 MegoTron X、接管 →
  跟随（离主人 200~400）→ 打两栖追随者、打死一个说「漂亮！」→ 残血 143/650 撤退 → 阵亡后地图复活、接着跟。
  右键点它回「我一直跟着你呢！」；头顶气泡和屏幕文字截图确认显示正常。

**还没做**
1. **读不到玩家打的任意聊天字**：固定的聊天命令已经能用了（空触发器计数，见上），但自由对话要拿到文字本身，
   得在运行时里接上聊天事件（之前试过几条路都没成，可以从 JASS 的 `EVENT_PLAYER_CHAT` 那条路查）。做成之后 `Talk` + 本地大模型就能真的陪玩家聊天。
2. 玩伴不懂具体地图的玩法（任务、商店、剧情触发器）：它是通用的跟随/助战/加血。要懂某张图，就在子类里按那张图写（`g.map_data` 能查名字，
   `g.jass` 能调任何函数）—— 这正是「让玩家自己决定怎么用」的那部分。
3. 实例的「下一局设置」里的种族/难度也会写进 RPG 地图的 0/1 号槽（启动器按对战图的习惯写的）；WarChasers 上没影响，别的图要是被改坏了再加开关。

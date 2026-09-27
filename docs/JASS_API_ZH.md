# JASS 对外接口：从外面随时调游戏里的 JASS 能力（09-24）

地图作者在地图脚本里能用的 **1291 个 JASS native**，现在都能从游戏外面按名字直接调：造单位、改属性、画特效、
弹面板和对话框、放声音、动镜头、改迷雾……拿来给游戏做进一步的自定义（RPG 辅助、玩伴、自制小玩法、调试工具）。

| 用法 | 适合 | 入口 |
|---|---|---|
| **远见「JASS 控制台」页** | 手动试、边看边改 | 左边栏 系统 → JASS 控制台：写脚本点运行，右边查 native（点一下插进脚本） |
| **命令行** | 手动试、写成脚本文件反复跑 | `python -m openwar3 jass --inst 20`（交互）/ `-e "代码"` / `my_script.j` / `--list 关键词` |
| **HTTP**（远见后端，只听本机） | 任何语言的外部程序 | `POST /api/instances/{n}/jass`、`/jass/call`、`GET /api/jass/natives`（见 §3） |
| **Python** | 写方案、写玩伴、写工具 | `g.jass.任意native(...)`；常用的画面和交互封装在 `openwar3.visual`（见 §4） |

⚠ **边界**（每条都是机制决定的）：
* 只在**单人局**（和本机的电脑打）里能改世界。本机单方面建句柄、改单位会让多人局里别的玩家不同步被踢 —— 多人局只放行只读的（Get* / Is* / Count* …，Issue* 除外）。
* 只给本机自己的工具（dev 车道）；`Game(player=N)`、公平模式（擂台规则）里调会被拒。
* 只用于单机 / 局域网自建的游戏。

## 1. 脚本写法（控制台、命令行、HTTP 都是这一套）

一行一句，**可以直接粘 JASS**（`call` / `set` / `local`、`true false null`、`'Hpal'` 四字码、`//` 注释），也可以用 Python 的样子写：

```jass
set h = hero()                                   // 内置：我方主英雄
local texttag t = CreateTextTag()
call SetTextTagText(t, "|cffffcc00+128 暴击！|r", 0.024)
call SetTextTagPosUnit(t, h, 60)
call SetTextTagVelocity(t, 0, 0.03)
call SetTextTagPermanent(t, false)
call SetTextTagLifespan(t, 4)
call SetTextTagVisibility(t, true)
call PingMinimapEx(h.x, h.y + 300, 3, 255, 0, 0, false)
set u = CreateUnit(Player(0), 'hfoo', h.x + 200, h.y, 270)
print("造了", u, "英雄等级", GetHeroLevel(h))
```

* 变量一直记着（同一个实例、同一局）：这一段 `set` 的，下一段接着用；远见页的「清变量」/ HTTP `/jass/reset` 清掉；换局自动清。
* 内置：`hero()` 我方主英雄、`me()` 本机玩家、`unit('hfoo'[, 玩家号])` 找一个单位、`unit_at(x, y[, 半径])`、`wait(秒)`、`print(...)`。
* 单位能读 `.x .y .hp .hp_max .mana .type .owner .level`；支持 `+ - * / %` 和比较。
* **不支持** if / loop / function —— 要写逻辑就用 Python 的 `g.jass`（它就是普通函数调用），或者写成方案。
* 出错告诉你第几行、为什么（没这个 native、参数个数不对、变量没定义……）；出错前面的句子已经生效了。

参数和返回值：

| 签名里 | 传什么 | 说明 |
|---|---|---|
| I 整数 | 数字；`'Hpal'` 四字码自动转 | |
| R 实数 | 数字 | 运行时转成引擎要的 real* |
| B 布尔 | true / false | |
| S 字符串 | "..." | 支持中文和 `|cffRRGGBB` 颜色码。查过：会把字符串存起来的那些（浮动文字、面板、按钮、聊天命令、计时窗标题）都是当场拷一份，安全 |
| H 句柄 | 变量里的句柄，或者单位（`hero()` 这种，自动换成 JASS 句柄） | |
| C 函数 | 只能 `null` | 从外面给不出 JASS 函数（`TimerStart(t, 60, false, null)` 这种可以） |
| 返回 S | —— | 引擎返回的是字符串表编号，读不回文字（`GetUnitName` 之类拿不到字；单位名字用 `g.map_data.name_of`） |

## 2. 分类（`--list` / 远见页右边按这个分）

| 分类 | 个数 | 例子 |
|---|---|---|
| 画面效果 | 80 | 浮动文字 TextTag、闪电连线 Lightning、特效 Effect、地面贴图 Image、地面印记 Ubersplat、单位变色/缩放/动作 |
| 界面面板 | 146 | 多行面板 Multiboard、排行榜 Leaderboard、倒计时窗 TimerDialog、对话框 Dialog、任务 Quest、屏幕文字、小地图 Ping、过场/头像对白、全屏滤镜 |
| 镜头 | 44 | SetCameraField、PanCamera、镜头抖动 CameraSetSourceNoise |
| 声音音乐 | 50 | CreateSound / StartSound、PlayMusic |
| 迷雾视野 | 25 | CreateFogModifierRadius、FogEnable |
| 下命令 | 20 | Issue*（我们自己的语义命令在 g.move / g.attack 等，走的路更稳） |
| 物品 / 英雄 / 单位 | 63 / 32 / 161 | CreateItem、SetHeroLevel、SetUnitOwner、UnitAddAbility… |
| 单位组 / 区域 / 坐标 | 68 | CreateGroup、Rect、Location |
| 玩家 / 同盟 / 资源 | 71 | SetPlayerAlliance、SetPlayerState（钱、木头） |
| 触发器 / 事件 / 计时器 | 64 | CreateTrigger、TriggerRegister*Event、CreateTimer、GetTriggerExecCount（执行次数计数） |
| 地形 / 天气 / 可破坏物 | 45 | AddWeatherEffect、SetTerrainType、CreateDestructable |
| 游戏流程 | 57 | SetGameSpeed、PauseGame、SetTimeOfDay… |
| 存储 | 47 | 哈希表、游戏缓存 |
| AI 脚本（common.ai） | 79 | Captain*、AttackMove*（给电脑 AI 脚本用的） |
| 类型转换 / 数学 / 字符串 | 73 | Convert*、I2S、Sin、GetHandleId |
| **事件回应** | 83 | GetTriggerUnit、GetClickedButton…… **只在触发器执行那一刻有值，从外面调拿到的是 0 / 空**。想知道「发生了没有」用 §4 的事件计数器 |
| 其它 | 83 | |

分类是按名字粗分的；`--list 名字` / 远见页可以搜。**✔ = 09-24 实机调过、看过效果**（94 个），其余走的是同一条路、只是没逐个看效果。

**每个 native 都有中英文说明和参数名**（09-25，`sdk/python/openwar3/jass_docs.json`，1291 条全覆盖，测试守着）：
参数名取自 common.j / common.ai（common.j 没声明的 8 个 1.27 隐藏 native 按用途补），说明写了做什么、关键参数的单位
（朝向用度、Sin/Cos 用弧度、时间秒）、要不要配对销毁、事件回应在哪种事件里有值。搜索**名字和说明都算**，几个词用空格隔开要全部出现：

```bash
python -m openwar3 jass --list 漂浮文字            # 按中文说明搜
python -m openwar3 jass --list "camera shake" --lang en
```
```python
print(g.jass.doc("CreateUnit"))
# CreateUnit(player id, integer unitid, real x, real y, real face) -> unit
# 为玩家 id 在地图坐标 (x, y) 创建单位类型 unitid 的单位，face 为朝向（度），返回该单位。
```

远见页右边每条显示 `名字(类型 参数名, …) → 返回` + 当前界面语言的说明，鼠标悬停看两种语言。

## 3. HTTP（任何语言的外部程序）

远见（`console/run.py`，默认 `127.0.0.1:8866`，只听本机；和老仓库的远见同时开时用 `--port 8867`，下面的例子照改端口）：

```http
GET  /api/jass/natives?q=TextTag&cat=visual&lang=zh
     -> 每条 {name, signature, params, paramNames, returns, category, categoryName, readonly, verified, zh, en, desc}
        （desc = lang 那种语言的说明；q 也按说明搜）
POST /api/instances/20/jass        {"code": "set h = hero()\ncall PingMinimapEx(h.x, h.y, 3, 255, 0, 0, false)"}
     -> {"ok": true, "rows": [{"line": 1, "code": "...", "value": ...}], "printed": [...], "vars": {...}}
     -> 出错：{"ok": false, "error": "第 2 行：...", "line": 2}
POST /api/instances/20/jass/call   {"name": "SetUnitScale", "args": [{"unit": 599669636}, 1.4, 1.4, 1.4]}
     -> {"ok": true, "value": null, "signature": "(Hunit;RRR)V"}
POST /api/instances/20/jass/reset
```

单位参数写 `{"unit": 地址}`（地址是快照里的 addr，脚本里 `hero()` 返回的 `addr` 也是它）。实测一次请求 60~90 ms。

## 4. Python：g.jass 和 openwar3.visual

```python
j = g.jass
t = j.CreateTextTag(); j.SetTextTagText(t, "你好", 0.024)       # 和脚本一样的参数规则
j.signature("CreateImage")                                      # '(SRRRRRRRRRI)Himage;'
from openwar3 import jass; jass.catalog("Multiboard")          # 目录
```

`openwar3.visual.Visual(g)` —— 实测过的常用画面和交互，一句话一个（`v.tick()` 每拍调一次：到期的删掉、跟着单位的线和圈挪过去；`v.clear()` 全删）：

| 方法 | 效果 | 实测 |
|---|---|---|
| `float_text(文字, 单位或(x,y), color, size, seconds)` | 浮动文字（伤害数字、头顶提示），中文、颜色码都行 | ✔ |
| `link(a, b, kind)` | 两个单位/点之间一根线：`leash` 橙色牵引 / `spirit` 白色灵魂链 / `drain` 绿色吸血 / `heal` 黄色治疗波…，跟着单位走 | ✔ 四种都看过 |
| `effect(模型, 单位或点, attach, seconds)` | 特效模型：头顶 `overhead` / 脚下 `origin`；seconds=0 放一次（爆炸、光柱） | ✔ |
| `ring(单位或点, 半径, color)` | 地上的范围圈（技能范围、危险区、集合点），可跟随单位 | ✔ |
| `ping(点, color)` | 小地图闪点 | ✔ |
| `board(标题, [(左, 右)...], icon)` → `.set(行, 列, 文字, color, icon)` | 右上角多行面板（带图标） | ✔ |
| `countdown(标题, 秒)` → `.left()` `.restart()` | 右上角倒计时窗（游戏自己走秒） | ✔ |
| `scene(名字, 话, portrait='Hpal')` | **头像对白**：底部头像换成会说话的那种单位 + 屏幕上「名字：话」字幕 | ✔（要强制字幕，已自动开） |
| `screen_tint(color, alpha)` / `clear_tint()` | 全屏滤镜（默认四周泛红：残血警告） | ✔ |
| `sound(路径)` | 放游戏里的声音 | 调通（没法截图验证声音） |
| `reveal(点, 半径, seconds)` | 驱散一片迷雾给玩家看 | 调通 |
| `look(单位, tint, scale, animation, flash)` | 单位变色、放大、播动作、闪一下 | ✔ |

截图（`docs/img/jass/`）：`text_links_ring.png`（浮动文字、四种连线、头顶特效、范围圈）、`board_timer_scene_ping.png`、
`scene_subtitle.png`（头像对白）、`dialog.png`（按钮菜单）、`screen_tint.png`、`unit_tint.png`、`visual_follow_clear.png`（跟随、清除）、
`companion_scene.png`（玩伴用头像对白打招呼）。

### 交互：不写 JASS 函数也知道玩家做了什么

JASS 里响应玩家要写触发器函数，从外面给不出函数。办法：**建一个没有条件、没有动作的空触发器，只注册事件，然后数它执行了几次**
（09-24 实测：空触发器照样计数 —— 定时器事件 0 -> 1）。

| 方法 | 用途 | 实测 |
|---|---|---|
| `chat_commands(["-follow", "-stay"])` → `.poll()` | 玩家在聊天框打的命令（精确匹配；`exact=False` 按开头匹配） | ✔ 英文命令；中文待真人用输入法验证（模拟输入被转成了乱码 `-ú`） |
| `menu(标题, [按钮...])` → `.clicked()` | 屏幕中间的按钮菜单，点了哪个 | 菜单显示 ✔；点击走同一套事件计数，没模拟点击（War3 读的是真鼠标位置，不动你的鼠标） |
| `hotkeys(("left", "right", "up", "down", "esc"))` → `.poll()` | 方向键 / Esc 按下 | ✔ ← |
| `on("TriggerRegister...Event", 参数...)` → `.poll()` | 任意 JASS 事件发生了几次：单位死亡、进入区域、受伤、定时器…… | ✔ 定时器 |

09-25 起：**聊天打了什么字**直接从事件流读 —— `message` 事件的 `.chat = {'channel', 'sender', 'text'}`（运行时收下屏幕消息框的每一条，见 `docs/UI_INPUT_ZH.md`）；
可点的按钮、热键、点地面也有了运行时画的版本（`g.ui`，点击游戏收不到、多人局也安全）。
局限：计数器只知道「发生了几次」，不知道「是谁」（那要在触发器里调事件回应函数）。要分清是谁，就给每个对象各建一个计数器
（例：`v.on("TriggerRegisterUnitEvent", 那个单位, j.ConvertUnitEvent(53))` 数这一个单位死了没有）。

玩伴已经接上了：聊天框打 `-follow` 跟随 / `-stay` 原地守着 / `-heal` 马上加血 / `-hi` 打招呼，打招呼、主人倒下、升级这几句用头像对白说
（`openwar3/companion.py` 的 `commands` 和 `scene_events`，见 `docs/COMPANION_ZH.md`）。

**要多人局也能用、或者要自己定样式**（中文字体、圆角、半透明、图片、贴地形的圈和路线）：用**画板**（`g.canvas`，运行时自绘，`docs/CANVAS_ZH.md`）。

## 5. 还要注意

* **句柄要自己删**：CreateTextTag / AddLightning / CreateImage / CreateMultiboard / CreateTrigger… 建出来不删就一直在（`Visual.clear()` 会删它自己建的）。浮动文字游戏同时最多约 100 个。
* **BJ 函数不是 native**：`CreateTextTagUnitBJ`、`TransmissionFromUnitWithNameBJ` 这类是地图脚本 blizzard.j 里用 native 拼出来的，这里没有 —— 照着它的实现调 native。
* **有些常量要先 Convert**：`ConvertPlayerColor(1)`、`ConvertFogState(4)`、`ConvertBlendMode(2)`、`ConvertPlayerEvent(261)`（common.j 里的常量值）。
* 运行时协议：W3P 操作码 70 / 71 / 72（`docs/W3P_PROTOCOL_ZH.md`）；名字 -> 地址的表只在闭源运行时里，开放层只有签名表 `sdk/python/openwar3/jass_signatures.json`。

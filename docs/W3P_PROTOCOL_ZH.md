# W3P v2 协议（开放层唯一要认的契约）

运行时（闭源 DLL）和外部程序之间只通过**共享内存**交换数据，下面这些就是全部。
权威定义是 `native/src/ui_control_protocol.h`（每个结构都有 `static_assert` 钉住大小和偏移）；
Python 参考实现是 `sdk/python/w3world.py`（读）和 `sdk/python/w3fast.py`（写），`sdk/python/tests/test_w3world.py` 从头文件解析核对两边一致。
**这里没有任何游戏内存地址**：换游戏版本时运行时自己适配，协议不变。

## 1. 共享内存（`<pid>` = 游戏进程号）

| 名字 | 方向 | 内容 | 同步方式 |
|---|---|---|---|
| `Local\War3World_<pid>` | 运行时 → 你 | 世界状态：头 + 16 个玩家 + 最多 1024 个单位 + 256 份单位细节 + 256 个地上物品 | seqlock |
| `Local\War3Trees_<pid>` | 运行时 → 你 | 最多 4096 个可破坏物（树等），每 2 秒刷新 | seqlock |
| `Local\War3Events_<pid>` | 运行时 → 你 | 事件环，8192 条 | 每条自带序号 |
| `Local\War3Map_<pid>` | 运行时 → 你 | 地图块：地形格子（128 一格，最多 256×256）+ 可玩区边界 + 出生点；开局后几秒分批算完 | seqlock（`state==2` 后不再变） |
| `Local\War3Fast_<pid>` | 双向 | 命令车道：**16 条** × 16 槽；每槽一条命令 + 回执；每条车道带**角色** | 每槽单写者单读者 |
| `Local\War3Canvas_<pid>` | 你 → 运行时 | 画板显示列表（09-25，见 `docs/CANVAS_ZH.md`） | seqlock |
| `Local\War3Msgs_<pid>` | 运行时 → 你 | 屏幕消息环（09-25）：游戏提示、聊天、系统消息的全文，128 条 × 256 字节 | 每条自带序号 |
| `Local\War3Input_<pid>` | 双向 | 界面与输入（09-25）：运行时回写鼠标位置 / 指着的地面点 / 悬停项；你写热键表和鼠标开关 | 热键表 seqlock |
| `Local\War3InputClients_<pid>` | 客户端之间（运行时只读） | 输入客户端登记表（09-26）：每个客户端自己的热键和鼠标开关，合并后写进输入块 | 命名互斥量 `Local\War3InputMutex_<pid>` |

**好几个客户端同时用画板 / 输入（09-26）**。两块都只有一份，客户端各写各的会互相覆盖。约定（SDK 照做，自己写客户端也要照做）：
* 画板：持 `Local\War3CanvasMutex_<pid>` 读-改-写，只换自己的条目、别人的原样留着（重排池偏移），主人进程已退出的和没主人的清掉。
  条目 `reserved[1]` = 主人进程号、`reserved[2]` = 进程内序号；条目 id 从块头偏移 60 的计数器分配（`0x10000` 起）。
* 输入：登记表头 16 字节（`magic 'INCL'、version 1、nextHotkeyId、保留`）+ 16 个客户端 × 528 字节（`pid、serial、mouseFlags、hotkeyCount、hotkeys[32]`）。
  持 `Local\War3InputMutex_<pid>` 改自己那条，再把活着的客户端合并写进输入块：热键按 `vk + mods` 去重（id 用先登记的、flags 取并集），
  鼠标开关取并集。事件发给所有客户端，各自按 `vk + mods` 认自己的热键。关输入（`input_enable 0`）要等登记表里没有别的活客户端。
* 运行时：主人进程已退出的可点项不拦点击；每 2 秒看登记表，登记过的客户端全死了就把输入块的热键表和鼠标开关清零。

**屏幕消息块** `War3MsgRing`：头 32 字节（`magic 'MSGS'、capacity、writeSeq、frames`），之后 `War3Msg msgs[128]`，每条 256 字节：
`seq（int32）、gameMs、frame（消息框编号，按第一次出现给，同一局稳定；聊天和游戏提示在不同的框）、repeat（同一句 4 秒内重复就合并计数）、utf8[240]`。
读法同事件环。聊天行的样子（09-25 单人局实测）：`[所有人] |cffff0303玩家名: |r内容`（名字带玩家颜色码；频道前缀是界面语言）。

**输入块** `War3InputBlock`：头 128 字节 + 32 条热键 × 16 字节。
* 运行时每帧（Present 时）回写：`cursorX/Y`（后备缓冲像素，不在窗口里 = -1）、`worldX/Y/Z` + `worldValid`（鼠标指着的地面点，用这一帧画世界的矩阵反投影 + 地形高度迭代）、
  `hoverId`（鼠标下面的可交互画板项）、`frames`、`clicks`（吞掉的点击数）、`keys`（报过的热键数）、`backW/H`。
  不在对局里（菜单、结算页）时 `worldValid = 0`，地面点击不报也不吞。
* 你写：`mouseFlags`（bit0/1 报告左/右键点在世界上的点击 → `mouse.world` 事件；bit2/3 同时吞掉）、`hotkeyCount` +
  `hotkeys[i] = {id（非 0）、vk、mods（1 Ctrl 2 Shift 4 Alt，要完全一致）、flags（1 吞掉 2 自动重复也报）}`（改表时 seq 变奇数 → 写 → 变偶数）。
* 画板项 flags `0x40` = 可交互：点在它露出来的那块上 → `ui.click`，**这一下游戏收不到**；悬停时运行时自己盖一层高亮（颜色在 `reserved[0]`，0 = 白 18%）。
* 为什么能吞：游戏的鼠标按键走普通的窗口消息，运行时在消息到达游戏之前就能拦下。⚠ 系统缩放不是 100% 时，不感知 DPI 的进程 PostMessage 过来的坐标会被系统放大（150% 实测 ×1.5），测试工具要先 `SetProcessDpiAwareness(2)`。

地图块头（160 字节）：`magic 'MAPB'、seq、epoch、state（0 没开始 / 1 在算 / 2 算好）、cellSize、cols、rows、done、
minX、minY（网格左下角）、boundMinX/MaxX/MinY/MaxY、startCount、starts[12][2]`，之后 `cells[cols*rows]`：
bit0 = 这一格中心**不能走**、bit1 = **不能盖**（引擎 IsTerrainPathable；树不算，树看树块）。

## 2. 读世界状态（seqlock）

```
loop:
    s1 = block.seq            (偏移 8，int32)
    if s1 是奇数: 重试        (运行时正在写)
    拷贝 头(96 字节) + players + units[unitCount] + details[detailCount] + items[itemCount]
    if block.seq != s1: 重试
```
头里有：`publishCount`（不涨 = 发布断了）、`clockMs`（引擎游戏时钟）、`epoch`（每局 +1）、`localPlayer`、`inGame`、
`speedPct`、`periodMs`、`costUs`（这一份在游戏线程上采集花的微秒数）、`eventSeq`、分段耗时。
客户端可以写 `requestedPeriodMs`（偏移 48）请求发布周期（16~1000 毫秒，默认 50）。

单位（112 字节）：地址 + 句柄对（**用句柄对认单位**，地址会复用）、类型四字码、主人、flags、坐标、血/蓝（含上限）、
当前订单 + 订单目标句柄 + 订单目标点、任务目标句柄 / 任务目标点（"实际在打谁"）、英雄等级/经验/技能点、细节下标、首次出现的发布序号、
`visibleTo`（位 p = 玩家 p 此刻看得见它；只算场上有单位的 0~11 号，自己的单位对自己恒可见 —— 公平模式按它过滤）。
细节（288 字节，英雄 > 玩家单位 > 野怪，最多 256 个）：12 个技能（码/等级/标志/剩余冷却秒）、8 个 buff 码、6 格背包（类型 + 对象地址）。

**块尾扩展（09-24，不挪动前面任何偏移；老客户端映射得短照常能用）**，紧跟在 items[256] 之后：
* 扩展区 `War3WorldExtra`（128 字节）：`magic 'EXT1'`、`timeOfDay`（游戏内时间，小时 0~24，开局 8:00，一小时 = 20 游戏秒，6~18 白天）、
  `todScale`（昼夜流速）、`prodCount`（生产表条数）、
  09-25：`selCount`（本机玩家选中几个，引擎的数）、`selLeader`（主单位地址，混选时常为 0）、`selAddrs[12]`（前 12 个选中单位的地址）、
  `slotStates`（12 个玩家的槽状态，每人 2 位：0 空 1 在玩 2 离开 / 被判负移除）、保留。**magic 对上才用。**
* 生产表 `War3WorldProd prods[128]`（每条 64 字节）：正在训练 / 研究 / 建造 / 升级的建筑 ——
  `unitIndex`（units[] 下标）、`kind`（1 队列：训练/研究/英雄/复活，2 在建，3 升级中）、`count`、`codes[7]`（[0] 正在做；在建 = 建筑类型；
  升级 = 目标类型）、`duration`（这一格总时长，游戏秒；**0 = 卡住没开始**，多半人口不够）、`elapsed`（已进行游戏秒）、`speed`、
  `flags`（bit0 卡住、bit1 开始时刻是运行时亲眼看到的）、`fromType`（升级的原类型）。
  来源：引擎里的生产 / 建造 / 升级进度给出总时长；
  剩余时间引擎不直接给出，所以 `elapsed` 由运行时从"看到这一格开始"起按游戏时钟 × 速度累加
  （实测：农民 14.9、农场 34.9、Rhme 59.9 游戏秒，误差 < 0.2 秒）。只覆盖"有细节"的单位（最多 256 个，玩家单位优先）。

## 3. 读事件

```
head = ring.writeSeq           (偏移 8)
for seq in (cursor, head]:
    e = ring.events[(seq-1) % 8192]
    if e.seq > seq: 丢了一条（读太慢被覆盖）
    elif e.seq != seq: 还没写完，下次再读
    else: 处理 e
```
事件种类：`unit.appeared / unit.died / unit.removed / unit.damaged / order.changed / hero.levelup / owner.changed /
item.appeared / item.removed / game.started`。由运行时比较相邻两次发布得出，精度 = 发布周期。
⚠ `unit.removed` 不等于死了：进金矿、被转化、尸体腐烂都会触发（`a` = 消失前是否活着）。

引擎级事件（运行时当场捕获，**每一下**都有，不是对比出来的）：
* `damage`（10）：单位 = 挨打的；`a` = 来源单位地址；`b` = 伤害类型（低 8 位，JASS DAMAGE_TYPE，普攻 4）| 攻击类型 << 8
  （JASS ATTACK_TYPE：0 法术 1 普通 2 穿刺 3 攻城 4 魔法 5 混乱 6 英雄）；`x/y` = 挨打的位置；`value` = 实际掉的血；`extra` = 护甲前的伤害（float 位）。
* `killed`（11）：这一下把它打死了；`a` = 凶手地址。
* `production.done`（12，09-24，跟踪生产表得出）：单位 = 建筑；`a` = 做完的四字码；`b` = 1 队列（研究码 R 开头，其余是训练/英雄）
  / 2 建造完成 / 3 升级完成；`value` = 这一格用了多少游戏秒。**对手的建筑也发**（看 `owner`）。

09-25 补齐（对局类按发布对比，精度 = 发布周期；界面类在窗口过程 / Present 里当场发）：
| 种类 | 码 | 字段 |
|---|---|---|
| `spell.cast` | 13 | 技能从「没冷却」变成「在冷却」（或冷却突然变长 = 刚转好又放了）：单位 = 施法者；`a` = 技能四字码；`b` = 等级；`x/y` = 施法点（任务对着点时）否则施法者位置；`value` = 冷却秒；`extra` = 目标句柄低位。只看有细节的单位（英雄优先）；放完单位可能已回到原订单，施法点 / 目标是尽力而为 |
| `player.left` | 14 | 玩家槽从「在玩」变成别的：`owner = a` = 玩家号，`b` = 新槽状态（2 = 离开 / 被判负移除） |
| `selection.changed` | 15 | 本机玩家的选择变了：`owner` = 本机玩家，`a` = 选中数，`addr` = 主单位（没有就第一个）；全表在扩展区 `selAddrs` |
| `message` | 16 | 屏幕消息框里出了一条：`a` = 消息序号（`War3Msgs` 里查全文），`b` = 消息框编号 |
| `ui.click` | 20 | 可交互画板项被点：`a` = 项 id，`b` = 1 左 / 2 右，`x/y` = 屏幕像素，`extra` = 修饰键 |
| `ui.hover` | 21 | 鼠标移进 / 移出可交互项：`a` = 现在的 id（0 = 移出），`b` = 之前的 id |
| `hotkey` | 22 | 登记的热键按下：`a` = 热键 id，`b` = 虚拟键码，`extra` = 修饰键 |
| `mouse.world` | 23 | 开了 `mouseFlags` 时点在世界上：`a` = 1 左 / 2 右，`x/y` = 地面坐标（按点击那一刻的位置反投影），`value` = 1 被吞了，`extra` = 修饰键 |
| `game.ended` | 101 | 离开对局（回菜单）：`a` = 刚结束那一局的 epoch |

事件结构 64 字节：`seq kind clockMs addr handleLo handleHi typeId owner a b x y value extra reserved[2]`。

## 4. 下命令（快车道 + 语义命令）

1. **一个客户端对象占一条车道**：持 `Local\War3FastMutex_<pid>` 找 `ownerPid==0`（或主人进程已死）的车道，写入角色、玩家号、自己的 pid；
   ⚠ 同一进程要两种角色就开两条（09-23 实测：共用一条时后开的 player=1 把 dev 车道的角色改了）；
2. 填槽：`flags = 0x10`（语义命令）、`target = 操作码`、`args[11]`（下标见下）、`deadlineMs = 现在 + 超时`（真实 GetTickCount）；
   `convention` 和紧跟 `flags` 的三个字（偏移 24 / 28 / 32）是保留字段，写 0；
3. 所有槽写完后 `state = 2`，最后把车道的 `submitSeq` 加 1；
4. 等 `Local\War3FastDone_<pid>_<lane>` 事件（或轮询 `state == 4`），读回执，`state = 0` 归还槽。

超时没执行的槽，过了 `deadlineMs` 运行时不会再执行（`faulted = 2`）—— 不会出现"暂停恢复后旧命令又执行一遍"。

args 下标：`0..2` 单位（地址、句柄 lo、句柄 hi；句柄给 0 表示不核对）、`3` 订单号或四字码、`4..6` 目标（同上）、
`7/8` x/y（float 位）、`9` extra（玩家号 / 格号 / 开关）、`10` mode（0 无目标 / 1 对点 / 2 对目标）。

| 操作码 | 名字 | 说明 |
|---|---|---|
| 1 | point | 单位对点下令（move / attack / smart / patrol / attackground / 点施法）。1/2/3/4 的 extra bit0 = **排队**：插在当前订单后面（Shift）；回执 value[0] = 下令后排着几条订单（含正在做的）。⚠ 引擎只有"插在当前后面"，没有"追加到末尾"：一串点要倒序发（SDK `path` / `build_queue` 就这么做） |
| 2 | target | 单位对目标下令（右键攻击 / 采集 / 修理 / 目标施法 / 捡物品）；目标必须看得见 |
| 3 | immediate | 无目标命令（stop / hold / 训练 / 研究 / 升级 / 无目标施法） |
| 4 | build | 工人造建筑（坐标对齐 32） |
| 5 | learn | 英雄学技能 |
| 6 | use_item | 用背包第 extra 格，mode 0/1/2 |
| 7 | revive | 祭坛复活（target 不给 = 死亡英雄表第一个） |
| 8 | rally | 集结点（mode 1 对点 / 2 对目标） |
| 9 | buy | 商店卖物品给旁边的英雄 |
| 10 | item_drop | 物品离手（09-24）：unit = 英雄，target = 背包里的物品；code = 接收的单位地址（给队友 / 卖给商店），0 = 丢在 x,y |
| 20 | q_tech | extra=玩家号，code=四字码 -> value[0] |
| 21 | q_feasible | 引擎可行性检查 -> verdict |
| 22 | q_visible | x,y -> value[0] 看得见 / [1] 迷雾 / [2] 黑区 |
| 23 | q_mine_gold | target=金矿 -> value[0] |
| 24 | q_captain | 敌方单位 -> 它跟着的电脑队长（value 按 float：x,y,goalX,goalY,homeX,homeY） |
| 25 | q_dead_heroes | extra=玩家号 -> value[0..6] 死亡英雄表 7 槽（对象地址，0 空），value[7] 非空个数 |
| 30 | pause | extra=1/0 |
| 40 | cam_state | -> scratch+64 起一份 War3CamState（112 字节：注视点/眼睛/7 个字段/边界/实时注视点/地形雾；角度是弧度） |
| 41 | cam_field | code=字段号（0 距离 1 远裁剪 2 俯角 3 视野 4 滚转 5 朝向 6 高度偏移），x=值（角度用度），y=过渡游戏秒 |
| 42 | cam_look | x,y；args[4]=过渡秒 [5]=高度偏移 [6]=用不用偏移；都为 0 = 瞬移 |
| 43 | cam_follow | target=单位（不给 = 解除跟随，镜头原地不动），x,y=偏移，extra=继承朝向 |
| 44 | cam_reset | x=过渡秒 |
| 45 | cam_rotate | 绕定点转：x,y 圆心，args[4]=弧度，[5]=秒 |
| 46 | cam_bounds | x=minX y=minY args[4]=maxX args[5]=maxY |
| 47 | cam_smoothing | x=平滑系数 0..10 |
| 48 | cam_show_ui | extra=1 显示 / 0 隐藏（自带电影黑边），x=过渡秒 |
| 49 | cam_clean_view | extra=1 只留小地图、世界铺满 / 0 还原（原矩形运行时存着，换局作废） |
| 50 | cam_fog | x=k：地形雾起止按 k 倍推开（1 = 这张图默认） |
| 60 | hud_button | extra 0 任务按钮 / 1 盟友按钮，mode bit0 顺便设成可点；文字 UTF-8 放 scratch+64 起（≤190 字节，0 结尾） |
| 61 | hud_quest | extra=任务槽 0..7；mode 0 标题 / 1 描述从这段开始 / 2 追加一段 / 3 写进去（描述最多 4 KB，分段传） |
| 62 | hud_quest_refresh | 让任务面板按新内容重画 |
| 63 | hud_user_dialog | value[0]=1：人打开了任务/盟友/记录面板（单人局会暂停） |
| 70 | jass | **只给 dev 车道**（09-24，RPG 辅助 / 玩伴）。按名字调 JASS native（1291 个，名字表在运行时里）：scratch+64 起 `名字\0 字符串1\0 字符串2\0…`（≤190 字节、最多 4 个字符串）；`args[i]` = 第 i 个参数（I/B/H 原值，R 放 float 位、运行时转成 real*，S = 第几个字符串 1 起）；`argCount` 必须等于签名的参数个数（不等 = 5）。value[0] = 返回值（R 是 float 位；S 返回字符串表编号，读不回文字），value[1] = 签名的参数个数。code 参数只收 0（null，`TimerStart(t, 60, false, null)` 这类），给别的值、会挂起脚本线程的（*Sleep* / *Sync* / ExecuteFunc / *WaitForSound* / *WaitOnSleeps*）= 6。签名表（没有地址）在 `sdk/python/openwar3/jass_signatures.json`。⚠ 本机单方面改世界，多人局会不同步：SDK 在多人局里只放行只读的（Get*/Is*/Count*…，Issue* 除外） |
| 71 | jass_handle_of | target = 单位（地址 + 句柄 lo/hi）或物品地址 -> value[0] = 它的 **JASS 句柄**（快照里的 handle_lo/hi 不是 JASS 句柄，native 要的是这个） |
| 72 | jass_unit_of | code = JASS 单位句柄 -> value[0] = 单位对象地址（对得上快照的 addr），0 = 不是活着的单位 |
| 73 | canvas_enable | 画板（09-25）：建共享内存 `Local\War3Canvas_<pid>`、起 GDI+，开始在游戏画面上绘制（画板画在鼠标指针下面）；value[0] = 1 成功。任何车道都能发（观战车道也行）—— 画板只画在本机画面上。第一次要初始化，客户端超时给 ≥ 2 秒。块的布局（`War3CanvasBlock`：头 64 字节、256 条 × 112 字节、64 KB 池，seqlock 写）见头文件和 `docs/CANVAS_ZH.md` |
| 74 | input_enable | 界面与输入（09-25）：`extra` = 1 开始接收游戏窗口的输入（画板点击 / 悬停、热键、地面点击，见 §1 输入块），0 = 停止；value[0] = 1 已开始。任何车道都能发（只影响本机输入）。第一次会顺带起画板，客户端超时给 ≥ 2 秒 |

回执（52 字节）：`status`（0 接下 / 1 被拒 / 2 单位不存在或句柄对不上 / 3 不是你的 / 4 异常 / 5 参数错 / 6 不支持 / 7 目标无效 /
**8 车道角色不允许**）、`engineReturn`、`verdict`（被拒原因码：3 人口 8 金 9 木 32 队列满 183 缺前置 185 祭坛复活中
221 没有这一项/在建/已存在/**这家店不卖**、**89 商店还没到货**、**1001 目标看不见**）、
`orderBefore / orderAfter`（同一帧读回的单位订单）、`value[8]`（查询结果）。
回执之后（09-23 夜起）：`+52` **execUs** = 这条命令在游戏线程上执行了多少微秒（真实 QPC）；`+56` **engineUs** = 其中引擎下令函数本身
（只有 point/target/immediate 量）。客户端要把 scratch 至少留 60 字节才读得回来（w3fast 默认 60）。
车道头（`War3FastBlock`）`+44 maxExecUs`（单条最慢一次）、`+48 lastDrainUs`（最近一次排空占游戏线程多久）。
一次排空的时间预算 4 ms 按真实 QPC 量（原来用 GetTickCount，15.6 ms 一跳，等于随机触发）；超了剩下的槽留到下一次事件分发。

### 车道角色（P5）
| 角色 | 能做什么 |
|---|---|
| 0 dev | 全部语义命令（指挥**本机玩家**的单位）+ JASS 通道（70） |
| 1 player | 只能指挥车道 `player` 号玩家的单位（别人的 = not_owner）；JASS 通道 status 8 |
| 2 observer | 只能查询（20~29）、镜头（40~50）、读 HUD 面板状态（63）、开画板 / 本机输入（73 / 74）；别的一律 status 8 |

两个 AI 对打 = 同一个游戏里开两条 player 车道（player 0 / player 1）。⚠ 本机模式下角色是客户端自己声明的（约定，不是安全边界）；
对战平台由裁判进程建车道、只把 player 车道交给选手。车道头 32 字节：`ownerPid submitSeq drainSeq served role player refused reserved`。

## 5. 实测过的语义（09-23，20 号演练实例，运行时协议实机验收 72/72）

P3 这一轮新加的（全在实机验收里；验收脚本在开发者本机，不随仓库发布）：
* 伤害事件：1.5 秒里那只野怪挨了 13 下，来源地址里有下令的步兵（也有旁边自动开火的英雄/水元素）；专门对照：步兵的攻击类型 normal、
  火枪手 pierce、普攻伤害类型 normal；击杀事件的凶手是真正补刀的那个（这次是大法师，不是下令的步兵）。
* 攻击令 0x0F 对具体目标：接下（订单换成攻击），但订单目标/任务目标都不记 —— 要打指定的一个用右键（smart）。
* 对点用物品：地狱火石 infs 对点 -> 地狱火出来、物品消耗。
* 商店：地精商店不卖生命药水（221）；**现造的商店从造出来那刻才开始算 stockStart**（开局 225 秒时新店还是 89），
  地图自带的那座从开局算；东西给店边的**任何一个**我方英雄（不一定是你以为的那个）。
* 地图块：Echo Isles 116×88 格、能走 70%、出生点 2 个，开局几秒算完；可见性：我方大厅 mask=0x1、对方大厅 0x2。
* 镜头：设距离读回、看向一点；雾推开/还原、干净画面、界面隐藏显示；HUD 按钮 / 任务标题 + 5 段描述。
* 角色：observer 下令 forbidden、查询/读镜头照常；player 车道指挥别人的单位 not_owner。

⚠ **最小化起的游戏模拟是停着的**（时钟 0.03 秒不动），核对脚本开头先查时钟走不走；起游戏用窗口模式。

* 右键（smart）对着敌人 = 攻击**这一个**（订单目标 / 任务目标都是它）；攻击令 0x0F 走目标命令只换上攻击令、不记目标，会去打附近别的。
* 引擎不许对看不见的单位下目标命令：天黑后远处营地进了迷雾，右键一律被拒（verdict 1001）。
* 建造"接下"只代表工人接了令：树林里的点也当场接下，工人走到才失败；明显被占的点（大厅正中）当场拒绝。
* 英雄死后约 3 游戏秒才能复活；人口不够也会被拒（英雄占人口）。英雄唯一：死了再训练回 221，要复活。
* 背包里的物品不算地上物品（持有者句柄非空）；捡起来发 `item.removed`。
* 暂停时引擎时钟停住，但命令照常能下（事件分发还在跑）。

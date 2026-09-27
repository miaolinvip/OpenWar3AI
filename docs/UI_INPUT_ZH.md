# 界面与输入：可点的按钮、热键、点地面、鼠标指着哪儿（09-25）

画板（`docs/CANVAS_ZH.md`）原来只能画不能点。现在运行时接管了游戏的窗口过程，外部程序可以：

| 能力 | 一句话 | 游戏收得到吗 |
|---|---|---|
| **可点的画板项** | 按钮、选项卡、面板：点中发 `ui.click`，悬停自动高亮 | 点在按钮上的这一下**收不到** |
| **热键** | 登记 `F5` / `ctrl+shift+Q`，按下发 `hotkey` | 可选吞掉（连同它生成的字符） |
| **点地面** | 点在世界上发 `mouse.world`（带地面坐标） | 可选吞掉（「点一个位置放塔」） |
| **鼠标位置** | 每帧：屏幕像素、指着的地面点、悬停的画板项 | —— |
| **选择** | 本机玩家选中了谁；一变发 `selection.changed` | —— |

全部是**本机输入 + 本机绘制**：不进指令流，多人局也安全（但你在回调里做的事要是改世界 —— 刷单位、改属性 —— 那还是只能单人局）。

## 1. Python：`g.ui`

```python
ui = g.ui                                                   # 第一次用时运行时接管窗口过程
ui.button("shop", "买一瓶药（50 金）", screen=(40, 300), on_click=lambda g, ev: buy(g))
c = ui.choice("升级了！选一个奖励", [("力量 +5", "更能扛"), ("攻速 +20%", "更能打"), ("召唤狼", "多一个帮手")],
              pause=True, on_pick=lambda g, i: give(g, i))  # 屏幕中间一排卡片；pause=True 选的时候游戏暂停
i = c.wait(timeout=30)                                      # 也可以阻塞等（泵事件，别的事件不会丢）
ui.hotkey("F5", lambda g, ev: g.say(hero, "收到！"))        # 默认吞掉
ui.hotkey("ctrl+shift+Q", on_press=..., swallow=False)
ui.mouse(on_click, capture=True, buttons=("left", "right"))  # 抓地面点击：左键 / 右键都报并吞掉
xy = ui.pick_point("点地面：塔建在哪？")                    # 阻塞版：下一次左键点地面 -> (x, y)；Esc / 超时 -> None
ui.cursor()                                                 # {'screen': (x, y), 'world': (x, y, z) 或 None, 'hover': 'shop'}
ui.toast("第 3 波来了！", seconds=3)
ui.close()                                                  # 收起自己的控件和热键；没有别的客户端在用输入时才放开窗口过程
g.close()                                                   # 或者整个断开（也可以 with Game(...) as g:）
```

回调 `(g, ev)` 在你调 `g.events()` 时触发（Bot / Mod 的运行器每拍都调）。没给回调的点击进 `ui.clicks`。回调里抛异常只打日志，不会打断别的回调和事件。
画板底层也能直接用：`g.canvas.text(..., clickable=True, hover=颜色)`，点击从事件流收（`ev.key` = 画的时候给的 key）；
画 `clickable=True` 的项会自动打开输入（不用先 `g.ui`）。

热键写法：`F1`~`F24`、`A`~`Z`、`0`~`9`、`numpad0`~`9`、`space enter esc tab backspace insert delete home end pageup pagedown left up right down`，
前面加 `ctrl+` / `shift+` / `alt+`。⚠ 不带修饰键的字母数字会和聊天输入、游戏快捷键打架 —— 优先用 F5~F8 这类游戏没占的键或组合键。

## 2. 新事件（`g.events()` 里）

| kind | 什么时候 | 便捷字段 |
|---|---|---|
| `ui.click` | 可交互画板项被点 | `.key`（画板 key）`.button`（'left' / 'right'）`.mods` |
| `ui.hover` | 鼠标移进 / 移出 | `.key`（移出 = None） |
| `hotkey` | 登记的热键按下 | `.key`（热键写法）`.mods` |
| `mouse.world` | 开了鼠标开关时点在世界上 | `.x .y`（地面）`.button` `.value`（1 = 被吞了） |
| `selection.changed` | 本机选择变了 | `g.selection()` 取单位 |
| `spell.cast` | 单位放了技能（技能开始冷却） | `.spell`（四字码）`.b` 等级 `.value` 冷却秒 `.x .y` 施法点 |
| `message` | 屏幕消息框出了一条 | `.text` 全文，`.frame` 哪个框，`.chat = {'channel', 'sender', 'text'}`（是聊天时） |
| `player.left` | 玩家离开 / 被判负移除 | `.player` |
| `game.ended` | 离开对局 | |

聊天：以前只能「数命令次数」（空触发器计数），现在玩家在聊天框打的字从 `message` 事件的 `.chat` 直接读：
```python
for ev in g.events():
    if ev.kind == "message" and ev.chat and ev.chat["text"] == "-follow":
        ...
```
`g.messages()` 另有一份游标：游戏提示（「需要更多的农场」）也在里面，写 bot 时用来知道命令为什么没成。

## 3. 实测（09-25，16/16）

点按钮 → `ui.click` + 回调、运行时计数 +1（被吞）；点按钮外面不触发；F6 → `hotkey`；点地面 → `mouse.world`（吞掉）；
刷圣骑士、JASS 选中 → `selection.changed`、`g.selection()` 对；放神圣护甲 → `spell.cast('AHds', 1, 35.0)`；
`DisplayTextToPlayer` → `message`；聊天 → `message`（frame 1，`.chat` 解析出发言人和内容）；
判负电脑 → `player.left(1, 2)`；`EndGame` → `game.ended`。真鼠标点按钮、悬停高亮也看过。

## 4. 好几个程序同时用（09-26）

模组、远见、MCP、网关的每个会话可能同时往一局游戏里放按钮、登记热键。以前输入块只有一份、谁写谁整张覆盖 ——
实测进程 B 一登记热键，进程 A 的热键就失效；B 一 `close()`，A 的按钮也点不了了。现在：

* 每个客户端把自己的热键 / 鼠标开关登记在一张表里（`Local\War3InputClients_<pid>`），SDK 持锁合并成一张写给运行时：
  同一个键（键码 + 修饰键）只留一条，吞不吞取并集；鼠标开关取并集。事件发给所有人，**各自按键码 + 修饰键认自己的热键**。
* 热键 id、画板条目 id 都从共用计数器分配（`0x10000` 起），不会撞号。
* `ui.close()` / `g.close()` 只撤自己的；最后一个客户端走了才关输入。
* 客户端被强杀（没来得及 close）：运行时每 2 秒看一眼，登记过的客户端全死了就清掉热键表和鼠标开关 ——
  以前 `pick_point` 等着的时候被杀，地面左键和 Esc 会一直被吞。它画的可点按钮也不再拦点击，下一个客户端写画板时顺手清掉。

## 5. 边界和坑

* **位置用真鼠标**：游戏按 GetCursorPos 读位置，所以悬停、`cursor()` 反映的是真鼠标。拦截只管按键消息。
* **画板在鼠标指针下面**（09-25 实测发现后修）：War3 自己把指针画进每一帧，原来画板在 Present 时画，把指针盖住了。
  现在画板在游戏画指针的那一步之前画：盖住游戏界面、被指针盖住。这一帧没画指针（藏起来 / 过场）才退回 Present 画。
  头顶气泡和旁白框（`g.say`、w3speak）也挪到了同一处，照样按列扣掉操作台石头边；气泡状态里 `cur=` 是在这里画的次数、`pr=` 是退回 Present 的次数。
* **系统缩放**：不感知 DPI 的进程 PostMessage 过来的鼠标坐标会被系统放大（150% 实测 ×1.5）。自己写测试投点击时先 `SetProcessDpiAwareness(2)`。真人点击不受影响。
* **画板第一次要预热字体**（1 秒多），这期间按钮还没画出来、点不中。
* **不在对局里时不报地面点击**：主菜单、结算页上 `mouse.world` 不发、也不吞，画在世界坐标上的画板项不画。
* **拖出窗口 / 切走**：按下时被吞的那一键，松开没回到窗口（切到别的程序、失去捕获）也会复位，不会把下一次松开也吞掉。
* **1.27 没有新建界面框（Frame / FDF）的 native**（1.31 才有 BlzCreateFrame）：这里的按钮、卡片都是运行时画的，不是游戏的界面框；样式随意，但不会出现在游戏自己的菜单层级里。
* 协议细节（输入块布局、操作码 74、事件字段）见 `docs/W3P_PROTOCOL_ZH.md`。

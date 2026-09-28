# 画板：往游戏画面上画东西（09-25）

外部程序往游戏画面上画**文字框 / 面板、进度条、图片、地上的圈、地上的路线（带箭头）**，由运行时（DLL）每帧自己画。

| | 画板（本页） | JASS 画面函数（`docs/JASS_API_ZH.md`） |
|---|---|---|
| 谁画 | 运行时自绘（D3D + GDI+） | 游戏自己（浮动文字、特效、面板、头像对白…） |
| 多人局 | **安全**：纯本机绘制，不建游戏句柄、不改游戏状态 | 只能单人局（建句柄会不同步） |
| 样式 | 随意：中文字体、圆角、半透明、边框、任意颜色、本机图片 | 游戏的那几种（原生风格） |
| 跟着东西走 | 跟单位 / 世界坐标 / 屏幕位置；地上的圈贴着地形高低起伏 | 看函数 |
| 开销 | 实测每帧 0.2~0.35 ms（9 个东西） | 每次调用约 13 ms（句柄换算） |

两条路可以一起用：原生风格用 JASS，自定义的面板、辅助线、提示用画板。

**09-25：画板能点了。** 文字框 / 进度条 / 图片加 `clickable=True` 就是可点的：点中发 `ui.click` 事件（游戏收不到这一下），
鼠标悬停自动高亮。按钮、三选一卡片、热键、点地面选位置用 `g.ui` 更省事 —— 见 `docs/UI_INPUT_ZH.md`。

截图（`docs/img/canvas/`）：`canvas_all.png`（标题面板、图片、跟着英雄的圈和血条、危险区、路线、状态面板）、
`canvas_follow_update.png`（英雄走动后跟过去、血条和面板更新）、`canvas_http.png`（HTTP 画的）、`companion_panel.png`（玩伴状态面板）。

## 1. Python

```python
c = g.canvas                                   # 第一次用时运行时装绘制钩子（约 0.1 秒）
c.text("title", "你好，这是画板", screen=(40, 110), color=(255, 220, 80), bg=(0, 0, 0, 170), border="#C49C40", size=22, bold=True)
c.panel("status", "玩伴 · 小圣", ["心情：开心", "击杀：12"], screen=(16, 330))
c.bar("hp", 0.62, unit=hero, lift=260, width=100, height=12, color=(80, 220, 80), text="62%")   # 跟着单位
c.text("tag", "Boss 要放大招了！", unit=boss, lift=320, color=(255, 80, 80), size=22, bold=True)
c.circle("danger", (x, y), 300, color=(255, 60, 60), fill=(255, 60, 60, 60), width=3)          # 地上的危险区
c.circle("aura", hero, 450, color=(80, 200, 255, 220))                                         # 跟着单位走的圈
c.path("route", [(x1, y1), (x2, y2), (x3, y3)], color=(255, 220, 0), width=5, arrow=True)
c.image("icon", r"D:\pics\icon.png", screen=(40, 170), width=64, height=64)
c.remove("danger"); c.hide("tag"); c.clear()  # clear 只清自己画的（别的程序画的不动）
c.expire("tag", 5)                             # 5 秒后自己删掉（计时线程删，不用谁来泵事件；之间又画了同一个 key 就作废）
with c.batch(): ...                            # 改很多条只写一次共享内存
c.stats()                                      # {'drawnFrames', 'itemsDrawn', 'faults', 'lastUs', ...}：drawnFrames 在涨 = 真的在画
```

**位置**（每个东西给一个）：`screen=(x, y)` 屏幕像素（负数从右边 / 下边往回数）、`frac=(0.5, 0.1)` 屏幕比例、
`world=(x, y)` 世界坐标、`unit=单位`（跟着走）。世界 / 单位的文字和进度条：底边中点对准那一点，`lift` 往上抬（游戏单位）；
屏幕位置默认左上角对准，`center=True` 中心对准。世界 / 单位的东西默认扣掉操作台和昼夜球（`over_ui=True` 盖在上面）。

**颜色**：`(r, g, b)` / `(r, g, b, a)` / `"#RRGGBB"` / `"#RRGGBBAA"`。

| 方法 | 画什么 | 常用参数 |
|---|---|---|
| `text(key, 文字, ...)` | 文字框，多行用 `\n` | `color` 字色、`bg` 底色（不给 = 透明）、`border` 边框、`size` 字号、`bold`、`shadow`、`width`（给了按这个宽换行）、`radius` 圆角 |
| `panel(key, 标题, [行...], ...)` | 面板（深色半透明底、金边） | 同 text |
| `bar(key, 0..1, ...)` | 进度条（血量、冷却、读条） | `width` `height` `color` 填充、`bg` 底、`border`、`text` 写在条上 |
| `image(key, 路径, ...)` | 本机图片（png / jpg / bmp / gif） | `width` `height`（不给 = 原尺寸） |
| `circle(key, 单位或(x, y), 半径, ...)` | 地上的圈（贴地形） | `color` 线、`fill` 填充（带透明度）、`width` 线宽像素 |
| `path(key, [点...], ...)` | 地上的折线 | `color`、`width`、`arrow` 末端箭头；点可以是 (x, y) 或单位 |

## 2. HTTP（任何语言）

远见（只听本机）：

```http
POST /api/instances/20/canvas
{"set": [
   {"key": "banner", "kind": "text", "text": "来自 HTTP 的画板", "frac": [0.5, 0.12], "center": true, "color": "#FFDC50", "bg": [0, 0, 0, 180]},
   {"key": "hp", "kind": "bar", "value": 0.8, "unit": 596125988, "lift": 260, "text": "80%"},
   {"key": "zone", "kind": "circle", "center": 596125988, "radius": 600, "color": [255, 200, 0], "width": 4},
   {"key": "route", "kind": "path", "points": [[-4587, -9092], [-5387, -8792]], "color": "#50C8FF"}
 ],
 "remove": ["old"], "clear": false}
GET  /api/instances/20/canvas              -> 现在有哪些东西 + 画了几帧
```

单位写快照里的地址（JASS 控制台 `hero()` 返回的 `addr`、`/api/instances` 的快照都有）；circle 的 `center` 可以是坐标或单位地址。
参数名和 Python 方法一样（kind 是方法名：text / panel / bar / image / circle / path）。

## 3. 直接写共享内存（不经过 Python / 远见）

布局见 `sdk/python/w3canvas.py`（`War3CanvasBlock`）：`Local\War3Canvas_<pid>`，头 64 字节 + 256 条 × 112 字节 + 64 KB 文字/点池。
先发一次语义命令 `canvas_enable`（W3P 操作码 73，装绘制钩子、建块），然后按 seqlock 写（seq 变奇数 -> 写条目、池、count -> seq 变偶数）。
运行时每帧读一次，读到写一半的沿用上一帧（seq 没变就不重读）；回写 `drawnFrames / itemsDrawn / faults / lastUs / torn`。Python 实现在 `sdk/python/w3canvas.py`。

**好几个程序同时画（09-26）**：块只有一份，以前谁写谁整张覆盖（模组的按钮，远见一画就没了）。现在的规矩：

* 写之前持命名互斥量 `Local\War3CanvasMutex_<pid>`，读出现有条目 → 留下别人的（重排池偏移）→ 换上自己的 → 写回；
* 每条记主人：`reserved[1]` = 进程号、`reserved[2]` = 进程内序号（网关一个进程里好几个会话）；主人进程已退出的、没主人的（0）写的时候清掉；
* 条目 id 从块头偏移 60 的计数器分配（持锁，`0x10000` 起）；
* 运行时：主人进程已退出的可点项不再拦点击。

自己写客户端也照这个来，否则会把别人的东西冲掉。

## 4. 玩伴用上了

玩伴（`openwar3.companion`）左边显示一块状态面板：血条 + 「正在：跟上你 / 帮你打怪 / 原地守着 / 撤退中」「心情：开心 / 兴奋 / 紧张 / 难过」「击杀 · 加血」
（`panel_at`，None 关掉）。截图 `companion_panel.png`。

## 5. 实测和注意

* 09-25 20 号实例（WarChasers，1920x1080，2 倍速）：9 个东西每帧 0.27~0.34 ms、约 63 帧/秒、0 异常；写 9 条 6 ms；英雄走动时跟单位的圈 / 字 / 血条跟得上；
  内容一改（version 变）才重画贴图，只挪位置不重画。
* 画在游戏画鼠标指针的那一步（09-25 起；这一帧没画指针才退回 Present），所以盖在游戏的界面、血条、单位上面，但在指针下面；
  世界 / 单位的东西会扣掉底下的操作台和顶上的昼夜球，但**不避让地图自己的面板**（右上角的排行榜、倒计时）—— 自己的面板别放右上角。
* 不在对局里（主菜单、结算页）时，锚在世界 / 单位上的东西不画；屏幕位置的照画。
* 地上的圈是把 64 个点各自投到地面上，地形有高低时形状会跟着起伏（这是对的：画在真实的地面上）。
* 第一次打开时运行时要装 D3D 钩子、起 GDI+、预热字体（约 1 秒，期间文字类先不画、圈和线照画）。
* 画的时候出一次异常，本次会话就不再画（和气泡同一个保护），`stats()['faults']` 会变 1。
* 图片路径是游戏进程能读到的本机路径；文字 + 路径 + 点一共 64 KB、最多 256 个东西。

# 网关：任何语言、浏览器、远程机器都能接（09-25，W3P 第 3 档）

`gateway/server.py` 把快车道 + 推送状态包成 **WebSocket / JSON**。Python SDK 能调的公开接口（`docs/API_ZH.md`），
JS / C# / Go / Rust / 浏览器页面 / 另一台机器上的程序 / 大模型都能调；延迟 = 快车道 + 约 1 ms。

**最省事：远见首页「控制中心」→ 网关 → 启动**（停止、重启、看日志、打开演示页也在那张卡片上）。命令行：

```bash
python gateway/server.py                 # ws://127.0.0.1:8870/ws（端口在 openwar3.json 的 ports.gateway）
python gateway/server.py --open          # 同上，端口听上以后打开演示页 http://127.0.0.1:<端口>/demo
python gateway/server.py --host 0.0.0.0  # 给局域网用：自动要求令牌（bin/gateway/token.txt）
python gateway/server.py --allow-origin http://localhost:5173   # 让你自己的网页（开发服务器）也能连
```

## 1. 连接和角色

`ws://127.0.0.1:8870/ws?inst=9&role=dev`（或 `pid=` 代替 `inst=`；要令牌时加 `&token=`）

| 角色 | 能调 | 用途 |
|---|---|---|
| `dev` | 全部：观察、命令、游戏控制、沙盒（JASS 改世界）、画界面 | 本机工具、模组、玩伴 |
| `player`（`&player=N`） | 观察、指挥 N 号的单位、画界面；**默认公平模式**（只看得见 N 号视野里的，`&fair=0` 关） | 替某个玩家下场的 bot / 大模型 |
| `observer` | 只读（走 DLL 的观察者车道，运行时直接拒绝下令） | 观战、解说、数据采集 |

`player` 拿不到：`end_game` `set_speed` `pause` `set_publish_period` `press_to_continue`（只给 dev）、`players` `enemy_ai_plan`（别人的底牌）、
`canvas.image`（会让游戏进程打开本机任意文件）、JASS；`resources` `upkeep` `tech` `tech_many` `stats` 只能查自己。
订阅推送的状态在公平模式下也只带 N 号看得见的单位。

一个连接 = 一个会话 = 一条快车道（运行时共 16 条）。网关同时最多 12 个会话（给 bot / 模组 / 远见留几条），满了新连接报错。
断开时只收掉这个会话自己画的东西和热键，别的客户端（模组、远见、另一个会话）画的不动。

## 2. 消息

连上先收到 `{"type": "hello", "protocol": 1, "role": "dev", "pid": 1234, "methods": [...]}`。之后每条请求带 `id`，回应带同一个 `id`：

| 发 | 回 |
|---|---|
| `{"id": 1, "op": "call", "method": "units", "args": ["me"]}` | `{"id": 1, "ok": true, "result": [单位, …]}` |
| `{"id": 2, "op": "call", "method": "move", "args": [[{"unit": 123456}], 100, 200]}` | 回执列表 |
| `{"id": 3, "op": "call", "method": "ui.button", "args": ["shop", "买药"], "kwargs": {"screen": [40, 300]}}` | 画板 key |
| `{"id": 4, "op": "subscribe", "state": {"hz": 4, "units": "mine"}, "events": true}` | 之后持续推 `{"type": "state", …}` / `{"type": "events", …}` |
| `{"id": 5, "op": "overview"}` | 一页局面（资源、兵种数、英雄、看得见的敌人、生产） |
| `{"id": 6, "op": "jass", "code": "call PingMinimapEx(0, 0, 3, 255, 0, 0, false)"}` | 只给 dev |
| `{"id": 7, "op": "api"}` / `ping` / `unsubscribe` | 方法目录 / pong / 停推送 |

* **单位参数**写 `{"unit": 地址}`（地址 = 单位 JSON 里的 `addr`；可以带 `"handle": [lo, hi]` 核对没被复用）。
* **方法名** = Game 的公开方法（`GET /api?role=player` 列出来），外加 `ui.*`（button / choice / toast / hotkey / mouse / cursor …）、
  `canvas.*`（text / panel / bar / image / circle / path / remove …）、`jass.<native>`（只给 dev）。回调参数（on_click）远端给不了 ——
  点击、热键从事件流收（`ui.click` 事件带 `key`）。
* 一条调用出错只回这一条（`ok: false, error`），连接不断；发来的不是 JSON 也一样。订阅推送出错会推一条 `{"type": "error"}` 并停掉这份订阅。
* 单位 JSON：`addr handle type owner x y hp hp_max [mana mana_max] [order] [hero level] [abilities items]`；事件 JSON 同 `docs/W3P_PROTOCOL_ZH.md` 的字段，
  外加便捷字段（`spell key text chat button player mods`）。

HTTP：`GET /`（说明）、`GET /api?role=`（方法目录）、`POST /call {"inst", "role", "method", "args", "kwargs"}`（一次性调用，curl 能用）。
`/call` 按「游戏进程号 + 角色 + 玩家号」复用会话：游戏重开换了进程号会自动换新的，空闲 10 分钟的收掉。

## 3. 客户端

* **JS**（浏览器 / Node 22+，零依赖）：`gateway/clients/js/openwar3.mjs`
  ```js
  import { OpenWar3, unit } from "./openwar3.mjs";
  const ow = new OpenWar3("ws://127.0.0.1:8870/ws", { inst: 9, role: "dev" });
  await ow.connect();
  const mine = await ow.api.units("me");
  await ow.api.move(mine.slice(0, 3).map(unit), 100, 200);
  await ow.api.ui.button("hi", "点我", { screen: [40, 300] });     // 最后一个普通对象 = 关键字参数
  ow.on("event:ui.click", (e) => console.log("点了", e.key));
  await ow.subscribe({ state: { hz: 2, units: "mine" }, events: true });
  ```
  Node 20 / 21 加 `--experimental-websocket`。例子：`gateway/clients/js/example.mjs`；演示页：`http://127.0.0.1:8870/demo`（局面、我方单位、往游戏里放按钮、事件流）。
* **别的语言**：任何 WebSocket 库 + 上面的 JSON 就行，不用碰共享内存。
* **大模型**：`docs/MCP_ZH.md`（MCP 服务器，直接给 agent 用的工具）。

## 4. 实测（09-25）

实机检查：握手（121 个方法）、`units('me')`、overview、ui.toast、ui.button、
订阅后模拟点游戏里的按钮 → `ui.click` 推到客户端、JASS、坏单位只报这一条、HTTP `/call`（observer）—— 9/9；
JS 客户端（Node）：overview、units、say（气泡）、ui.button、错误、点击事件 —— 全过；
浏览器演示页：连上、局面和单位表在刷、网页放的按钮在游戏里点了 → 网页事件日志收到 `ui.click key=web.btn`。

## 5. 安全

* 默认只听 127.0.0.1、不要令牌（和远见一样）。`--host` 不是回环地址时自动要求令牌；`--auth` 本机也要。令牌比较用常数时间。
* **浏览器里的别的网站连不上**（09-26）：浏览器发起的连接一定带 `Origin`，网关只认自己的页面（演示页）和 `--allow-origin` 给的；
  不带 `Origin` 的程序（Python / Node / curl / 别的语言）照常连。WebSocket 握手阶段就拒（关闭码 4403），`/call` 回 403。
  以前本机不要令牌，随便打开一个网页就能以 dev 身份跑 JASS、结束游戏。
* 只听本机时还核对 `Host` 必须是 `127.0.0.1` / `localhost` / `[::1]` 加网关端口（挡 DNS 重绑定）。
* 角色是连接时自己声明的：本机模式下是约定，不是安全边界 —— **对战平台**要由裁判进程决定谁拿什么角色（`docs/ARENA_ZH.md`）。
* `.mjs` 的 MIME 类型：Windows 的类型表里没有，网关启动时自己补（不补浏览器拒绝当模块载入）。

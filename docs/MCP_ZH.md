# 给大模型的工具接口（MCP，09-25）

`tools/war3_mcp.py` 是一个 **MCP 服务器**（stdio）。Claude Code、Claude Desktop、本地大模型的 agent 框架 —— 任何支持 MCP 的客户端
挂上它，大模型就能**直接**看局面、下命令、在游戏屏幕上跟玩家说话、问玩家、截图看画面，不用先写代码。

官网说的「四种接大模型的方式」（写 bot、当教练、单位说话、网关直接下场）之外，这是第五种：**大模型当工具的使用者**。

## 1. 挂上

```bash
claude mcp add war3 -- python <仓库>\tools\war3_mcp.py --inst 9                     # Claude Code（<仓库> 换成你的 openwar3 目录）
python tools/war3_mcp.py --inst 9 --role player --player 0                          # 只指挥 0 号玩家、公平模式
```

别的客户端：`command = python`，`args = ["<仓库>\\tools\\war3_mcp.py", "--inst", "9"]`。第一次调工具时才连游戏（游戏可以后开）；
游戏关了重开（进程号变了）下一次调用自动重连，不用重启 MCP 服务器。

| 角色 | 能用 |
|---|---|
| `dev`（默认） | 全部工具，含 `war3_jass` |
| `player --player N` | 只指挥 N 号的单位、只看得见 N 号视野；没有 JASS |
| `observer` | 只读：走 DLL 的观察者车道（运行时直接拒绝下令），不能往屏幕上画、不能让单位说话 |

`player` 角色另外的限制（和网关同一套，`openwar3/remote.py`）：
- 拿不到 `end_game` `set_speed` `pause` `set_publish_period` `press_to_continue`（只给 dev），也拿不到 `players` `enemy_ai_plan`（会看到别人的底牌）；
- `resources` `upkeep` `tech` `tech_many` `stats` 这类带 `player` 参数的查询只能查自己，给别人的号直接报错；
- `canvas.image` 只给 dev（它会让游戏进程打开本机任意图片文件）。

上限：一个工具结果最多 20 万字符（超出截断，并提示用 `limit` / `types` 缩小）；`war3_ask_player` 最多等 120 秒；
`war3_screenshot` 的 `scale` 限制在 0.1 ~ 1。坏消息（不是 JSON、缺 `method`、参数类型不对）按 JSON-RPC 回错误码，不断开。

## 2. 工具

| 工具 | 做什么 |
|---|---|
| `war3_overview` | 一页局面：时间、资源、人口、我方各兵种数、英雄（血蓝等级冷却）、看得见的敌方兵种、生产。**先调它** |
| `war3_units` | 单位列表（`owner` me / enemy / creep / all，`types` 过滤）：`addr` 用来下命令 |
| `war3_events` | 上次调用之后发生的事（死亡、升级、施法、生产完成、聊天、玩家点了按钮……；默认去掉刷屏的几种） |
| `war3_call` | 调任意公开接口（`move` `attack_move` `train` `build` `cast` `learn` `tech` `ui.button` `canvas.text` …），单位写 `{"unit": addr}` |
| `war3_api` | 查接口：按关键字搜名字和说明 |
| `war3_toast` / `war3_say` | 屏幕上方一行字 / 单位头顶一句话 |
| `war3_ask_player` | 屏幕中间给玩家几张选项卡，等玩家点，返回选了哪个（可暂停游戏） |
| `war3_screenshot` | 游戏画面截图（PNG，前面挡着窗口也能截，不抢焦点） |
| `war3_jass` | 跑一段 JASS（只给 dev；改世界只在单人局） |

能玩出来的：大模型陪玩 / 教练（`war3_overview` + `war3_toast`）、边打边问玩家（`war3_ask_player`）、
大模型解说（`war3_events` + `war3_say`）、让大模型直接指挥一支部队（`war3_call` + player 角色）、看图调 UI（`war3_screenshot`）。

## 3. 实测（09-25）

initialize → tools/list（10 个工具）→ `war3_overview`（`{'htow': 1, 'hpea': 5}`）→ `war3_units` → `war3_toast` →
`war3_screenshot`（PNG 20 万字节）→ `war3_ask_player`（三张卡，模拟点第二张 → `{'picked': 1, 'option': '开矿'}`）—— 7/7。

真客户端（09-25，Claude Code 2.1.143）：临时配置文件 + `--strict-mcp-config`，不动全局配置 ——
Claude Code 自己起服务器、握手，`mcp_servers: [{'name': 'war3', 'status': 'connected'}]`，10 个工具都以 `mcp__war3__*` 出现在它的工具表里。

```bash
claude -p "调 war3_overview 看局面，再用 war3_toast 在屏幕上打个招呼" --mcp-config war3.json --strict-mcp-config --allowedTools "mcp__war3__war3_overview,mcp__war3__war3_toast"
```

`war3.json`：`{"mcpServers": {"war3": {"command": "python", "args": ["<仓库>\\tools\\war3_mcp.py", "--inst", "9"]}}}`。
`--allowedTools` 列出的工具不用逐个点确认；不列就是每次调用问一下。

## 4. 实现

`sdk/python/openwar3/mcp.py`：换行分隔的 JSON-RPC 2.0（`initialize` / `tools/list` / `tools/call` / `ping`），协议版本 2025-06-18（兼容 2025-03-26、2024-11-05）；
工具出错按 MCP 规矩放在结果里（`isError: true`）。和网关共用 `openwar3/remote.py`（角色白名单、单位参数、JSON 形状、一页局面）。
日志走 stderr，stdout 只有协议。

# AI 方案：多方案切换、分享、导入（09-24）

一个**方案** = 一套完整的 AI：一个文件夹 + 一份说明书 `scheme.json` + 代码。
每个游戏实例选一个方案；在远见（Farsight）里一键切换，正在打的这一局也能立刻换人接管。
别人分享的方案导入进来是**单独一块**，和你自己的方案互不影响；想改就「复制到我的」。

```
schemes/
  mine/<id>/          我的方案：自己写的、或从别的方案复制来改的（可以随便改，下一局生效）
  installed/<id>/     已安装：别人分享的 zip 解开在这里（只读用；第一次运行前要确认信任）
brains/xwar3/         内置：参考大脑（完整 AI，自带监督器）
brains/examples/      内置：四个教学示例 hello / rush / macro / micro
```

`schemes/mine/` 和 `schemes/installed/` 在 `.gitignore` 里 —— 那是你本机的东西，不进仓库。

## 1. 在远见里用

**「AI 方案」页**（左边栏 系统 → AI 方案）：

| 操作 | 做什么 |
|---|---|
| 导入方案（zip） | 装进 `installed/`。同 id 已装过会问要不要替换（替换后要重新确认信任） |
| 用到实例… | 选实例 + 「立刻生效」（停掉当前 AI，新方案接管这一局）或「下次开始测试生效」 |
| 复制到我的 | 拷一份到 `mine/<新 id>`，作者记成「我」、版本 0.1.0、记下复制自哪个方案哪个版本 |
| 导出 zip | 打包成 `<id>-<版本>.zip`，发给别人就是分享 |
| 打开文件夹 | 在资源管理器里打开方案目录，直接改代码 |
| 信任 | 别人的方案第一次运行前必须点（见 §4） |
| 最近战绩 | 这个方案每一局的胜负、时长、结束原因 |
| 删除 | 只能删「我的」和「已安装」；有实例正在用的不让删 |

**实例卡片**（实例页）多了一行「AI 方案」：下拉选方案 → 「切换（立刻生效）」。
实例没在跑时按钮叫「选定」，记进 `bin/inst<N>/next_game.json` 的 `scheme`，下次「开始测试」按它起 AI。
顶栏实例徽章显示在跑的方案（参考大脑仍叫「新大脑」，玩家方案显示 id）。

## 2. 方案的说明书 `scheme.json`

```json
{
  "format": 1,
  "id": "fast-rush",
  "name": "三分钟速攻",
  "version": "1.2.0",
  "author": "某某",
  "description": "一句话说明这个 AI 打什么路子",
  "kind": "bot",
  "entry": "rush_bot.py",
  "class": "RushBot",
  "fair": true,
  "hz": 5,
  "races": ["human", "orc"],
  "license": "MIT",
  "homepage": "https://...",
  "forked_from": "builtin/rush@example"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✔ | 小写字母 / 数字 / `-` / `_`，2~41 个字符。同一来源里唯一（`mine/x` 和 `installed/x` 可以同时存在） |
| `entry` | ✔ | 方案目录里的一个 `.py` 文件（不许绝对路径、不许 `..`） |
| `class` | | 入口里的 Bot 子类名。不写就取入口文件里**最后一个自己定义的** `openwar3.Bot` 子类 |
| `kind` | | `bot`（`openwar3.Bot` 的子类：一个 AI）或 `mod`（`openwar3.Mod` 的子类：一套玩法规则，09-25，见 `docs/MODS_ZH.md`；默认不是公平模式、胜负由模组自己报）。完整监督器那一类（参考大脑）只有内置的 |
| `fair` | | 默认 `true`：只看得见视野里的东西（和对战平台同一个规则）。`false` = 全图可见，也才能用 JASS 通道（玩伴要它） |
| `judge` | | 默认 `true`：按对战规则判胜负。RPG / 玩伴方案写 `false`（英雄死了能复活、不按建筑判输赢，见 `docs/COMPANION_ZH.md`） |
| `hz` | | `on_tick` 每墙钟秒调几次，默认 5 |
| `format` | | 说明书格式版本，现在是 1。比本机 OpenWar3 新的会被拒绝并提示更新 |
| `i18n` | | 别的语言的名字/简介：`{"en": {"name": "3-minute rush", "description": "..."}}`。远见切到英文时显示它，没写就显示 `name` / `description`（`docs/I18N_ZH.md`） |
| 其余 | | `name` `version` `author` `description` `races` `license` `homepage` `forked_from` 只是展示用 |

方案目录会被加进 `sys.path`，所以入口可以 `import` 同目录下的其他文件（内置示例就是 rush 继承 hello）。
第三方包（numpy、torch……）不会自动装 —— 在 `description` 里写清楚要什么。

**最小方案**（两个文件就够）：

```python
# my_bot.py
from openwar3 import Bot

class MyBot(Bot):
    name = "mine"
    def on_tick(self, g):
        for w in g.idle_workers():
            mine = g.nearest(g.gold_mines(), w)
            if mine:
                g.gather(w, mine)
```

```json
{"id": "my-first", "name": "我的第一个 AI", "entry": "my_bot.py"}
```

放进 `schemes/mine/my-first/`，远见刷新就看得见。更省事的起点：在「内置」里挑一个示例点「复制到我的」。

## 3. 运行方式和战绩

玩家方案由**方案运行器** `tools/run_scheme.py` 跑（远见「开始测试 / 切换」起的就是它）：

```bash
python tools/run_scheme.py --inst 20 --scheme builtin/micro --hours 6
```

* 每个实例一个常驻监督器；**每一局起一个子进程**跑方案 —— 方案代码崩了不连累监督器，
  「我的方案」改了代码下一局自动用新的（每局都重读说明书）。
* 每局结束记一行到 `bin/inst<N>/scheme_matches.jsonl`：方案、版本、作者、胜负、原因、游戏时长、拍数、出错次数。
  远见的胜率就是从这里统计的（参考大脑按它自己的对局文件 `matches/match-*.jsonl` 统计）。
* 实例挂了停止牌（远见「停止实例」）就退出，不再接管新局。

胜负怎么判（`openwar3.run`，SDK 里所有 bot 共用）：

| 情况 | 记成 |
|---|---|
| 对面建筑全没了 | 胜 |
| 我方建筑全没了（兵还活着也算 —— War3 对战就是这么判负的） | 负 |
| 我方单位全没了 | 负 |
| 远见「结束这一局 / 停止」 | 未定（手动结束） |
| 切换时这局已经打了 60 游戏秒以上（半路接手） | 单独数「半路接手」，**不进胜率**（刚开的局不算半路） |
| 游戏时钟连续 `stuck_s`（默认 300）墙钟秒不走 | 未定 |

分出胜负后运行器会发结束令关掉结算画面（`run(end_game=True)`），20 秒没退就强关，farm 按「下一局设置」开下一局，方案接着接管。

**暂停不会结束对局**：09-24 实测人按 Pause（动作 57）和 `g.pause()` 两种暂停，世界块照常发布、快车道照常收命令，只有游戏时钟停住；
所以暂停期间 bot 照常跑，时钟停满 `stuck_s` 才算结束。游戏收尾时快车道不收命令（`FastLaneUnavailable`）不算 bot 出错。

## 4. 信任和安全

**方案就是代码，运行时的权限和你本人一样**（能读写文件、联网）。所以：

* `installed/` 里的方案默认**不被信任**，远见和运行器都拒绝运行，点「信任」之前会提示；信任记在方案目录里的 `.trusted`。
* 替换安装同 id 的方案会**重置信任**（新版本等于新代码）。
* 导入时的检查：zip 不超过 50 MB、不超过 2000 个文件；不许绝对路径和 `..`（防止写到方案目录外面）；
  `scheme.json` 必须在 zip 根目录或唯一的顶层文件夹里；说明书不合法或入口文件不存在都直接拒绝。
* 信任之前先「打开文件夹」读一遍代码。只从你信得过的人那里拿方案。

## 5. 接口（给脚本 / 以后的网站用）

| 接口 | 说明 |
|---|---|
| `GET /api/schemes` | 方案列表 + 战绩 + 各实例选的 / 在跑的方案 |
| `GET /api/schemes/results?ref=` | 一个方案最近 30 局 |
| `POST /api/schemes/import` | `{filename, data: base64 的 zip, replace}` |
| `GET /api/schemes/export?ref=` | 下载 zip |
| `POST /api/schemes/fork` | `{ref, id, name}` |
| `POST /api/schemes/trust` | `{ref}` |
| `DELETE /api/schemes?ref=` | 删除（有实例在用时拒绝） |
| `POST /api/schemes/open-folder` | `{ref}` |
| `POST /api/instances/{n}/scheme` | `{ref, now}`：now=true 立刻接管这一局，false 下次开始测试生效 |

Python 里直接用库：`from openwar3 import schemes` —— `list_schemes / get / install_zip / export_zip / fork / trust / delete / load_bot_class / stats`。

## 6. 以后：方案网站（还没做）

导出的 zip 就是分享的单位，网站只需要在它外面加一层。计划的样子：

* **上传**：远见「分享到方案网站」= 本地 `export_zip` -> `POST https://<站点>/api/v1/schemes`（zip + 说明书里的元数据）。
  服务器按 `id + version` 去重，同一个作者才能给同一个 id 发新版本（需要登录，令牌存在本机，不进方案 zip）。
* **下载安装**：网站给出 zip，远见拿到后走和「导入方案」完全同一条路（`install_zip`，同样的检查、同样要确认信任）。
* **战绩**：本机 `scheme_matches.jsonl` 可以选择性上报（方案 id、版本、胜负、对手种族和难度），网站按版本汇总胜率。
* **安全**：服务器存 zip 的 SHA-256，下载后本地校验；以后可以加作者签名。本地「信任」这一步不会因为来自网站就省掉。

远见页面上「分享到方案网站」按钮已经占好位置（现在是灰的）。

# OpenWar3 —— 用任何语言给《魔兽争霸 III》1.27 写 AI

[English](README.md) · **简体中文** · [官网与文档](https://war3ai.com/)

**为 AI 智能体打造的《魔兽争霸 III》开放接口：一个运行时、一套协议，任何模型、任何语言。**

一个注入进游戏的运行时（DLL）+ 一套 Python SDK：运行时每 50 ms 把**整张地图的完整状态**推进共享内存
（全部玩家资源、所有单位的血蓝/订单/在打谁/技能冷却/buff/背包、地上物品、树），外加一条**事件流**；
外部程序以**约一帧的延迟**下语义命令（移动、攻击、采集、建造、训练、施法、学技能、复活、用物品……），每条都有回执说明接没接、为什么。
开放层里没有任何游戏内存地址（协议见 `docs/W3P_PROTOCOL_ZH.md`）。
仓库里还带着一个完整的参考 AI（开矿、打野、出击、英雄施法、保命）、一个网页指挥台、自动运镜和头顶聊天气泡。

> ⚠ 只用于**你自己合法拥有的** War3 1.27 客户端，本机/局域网/自建游戏。不修改磁盘上的 Game.dll，
> 不分发任何暴雪文件，**不得用于 Battle.net 或任何有反作弊的服务器**。

## 快速开始

需要：64 位 Windows 10/11，和你自己的魔兽争霸 III **1.27a**（冰封王座，Game.dll 1.27.0.52240）。别的都不用先装。

**双击 `start.bat`。** 第一次它会自己：

1. 下载 Python 3.13（官方便携包，约 14 MB）到本仓库的 `bin\env\` —— **唯一要装的环境**；不要管理员权限、不改系统 PATH，国内网络自动换镜像
2. 装 Python 包；把 `prebuilt\` 里的注入运行时（DLL / 启动器）核对后装进 `bin\`
3. 下载 AMAI 生成参考大脑的战略数据
4. 打开网页指挥台「远见」 http://127.0.0.1:8866 的首页「控制中心」

PowerShell 用 Windows 自带的 5.1；网页随仓库发的是编好的（`console\web\dist\`），不用 Node.js。
只有改了网页源码，start.bat 才会找 / 下一份 Node.js 重编；官网预览要 Node.js 时运行 `start.bat node`。

**然后在控制中心最上面设置魔兽争霸 III 的目录**（能「自动查找」或「浏览…」自己选）：远见检查游戏版本、从你的游戏里提取数据
（暴雪的文件不随仓库分发）。版本不是 1.27a 会给温馨提示 —— 请下载 1.27.0.52240 版本，更多版本即将支持，关注 [War3AI.com](https://war3ai.com)。
地图、下一局的设置都相对这个目录（`<游戏目录>\Maps` 下所有文件夹的地图都能选）；以后换目录在「设置」页。

以后每次双击只做一两秒的检查就打开远见。开局、换 AI、网关、头顶气泡、本地大模型、官网预览都在远见里点，不用再找别的脚本。
远见启动时会问 War3AI.com 有没有新版本；遇到问题、有建议，在「反馈与建议」页直接提交给我们。

黑色窗口几秒后会自己关掉：远见在后台继续运行，关掉浏览器也不会停。**彻底停止**：双击 `stop.bat`，或者在远见「控制中心」右上角点「全部停止」——
游戏实例（游戏、farm、AI、导播）、网关、气泡、官网预览、本系统用的本地模型、远见后台依次全停。
MCP 服务器归 Claude 等客户端管，不会被停；LM Studio 程序本身也不会被关。

```bat
start.bat              :: 部署检查 + 打开远见
start.bat setup        :: 完整检查：重装 Python 包、重试 AMAI
stop.bat               :: 彻底停止全部（= start.bat stop）；stop.bat --keep-llm 把本地模型留在显存里
start.bat restart      :: 只重启远见后台（游戏和各服务不受影响）
start.bat node         :: 顺带装一份 Node.js（官网预览要它；平时用不着）
start.bat 5 6          :: 顺带开始 5、6 号实例的测试（游戏 + 参考 AI）
```

命令行起一局、示例 bot 接管（`python` 用 `openwar3.json` 里记的那个；start.bat 自己装的在 `bin\env\python\python.exe`）：
`python tools/play.py --bot brains/examples/hello_bot.py`

写你自己的 AI：

```python
from openwar3 import Bot

class MyBot(Bot):
    def on_tick(self, g):                     # 每秒约 5 次
        for w in g.idle_workers():
            g.gather(w, g.nearest(g.gold_mines(), w))
        for hero in g.my_heroes():
            foe = g.nearest(g.enemies(fighters_only=True), hero)
            if foe and g.dist(foe, hero) < 800 and g.cooldown(hero, "AHtb") == 0:
                g.cast(hero, "thunderbolt", target=foe)      # 风暴之锤
```

不会编程：看 [让 AI 帮你写一个陪玩 AI](docs/WRITE_A_BOT_WITH_AI_ZH.md)。
写 bot 先读 [Bot 作者手册](docs/BOT_HANDBOOK_ZH.md)，再照 [职业打法食谱](docs/PRO_COOKBOOK_ZH.md) 一条条加；
示例从简到繁：`hello_bot`（经济）→ `rush_bot`（出兵）→ `macro_bot`（运营）→ `micro_bot`（微操 + 打野）；
玩法模组示例：`mod_hero_roguelike`（英雄肉鸽）、`mod_endless_defense`（无尽守城）。
别的语言用[网关](docs/GATEWAY_ZH.md)（远见「控制中心」一键启动），大模型 agent 挂 [MCP](docs/MCP_ZH.md)（`tools/war3_mcp.py`）。
写好的 bot 可以做成**方案**：在远见「AI 方案」页一键切换、导出 zip 分享、导入别人的方案跑测试（见 [AI 方案](docs/SCHEMES_ZH.md)）。
不只是对战：RPG / 自定义地图里可以给玩家配一个**玩伴**（跟随、助战、加血、陪聊，可接本地大模型），
底下是能按名字调 1291 个 JASS 函数的通道，怎么用你自己定（见 [RPG 辅助与玩伴](docs/COMPANION_ZH.md)，示例 `brains/examples/buddy.py`）。

## 文档

| | |
|---|---|
| [Bot 作者手册](docs/BOT_HANDBOOK_ZH.md) | 五分钟跑起来、心智模型、按"想做什么"查接口、十五条规矩、调试和性能 |
| [职业打法食谱](docs/PRO_COOKBOOK_ZH.md) | 21 招：农民饱和、队列只排 1 个、人口不卡、Shift 回矿、侦察记忆、夜里打野、集火、拉残血、克制、绕塔…… 每招配代码 |
| [AI 方案](docs/SCHEMES_ZH.md) | 多方案切换、导出分享、导入别人的方案（信任和安全）、战绩统计、以后的方案网站 |
| [画板](docs/CANVAS_ZH.md) | 运行时自绘：往游戏画面上画文字框、面板、进度条、图片、地上的圈和路线；多人局也安全；Python / HTTP / 直接写共享内存 |
| [界面与输入](docs/UI_INPUT_ZH.md) | 可点的按钮和选项卡（点击游戏收不到）、热键、点地面选位置、鼠标指着哪儿、选择；施法 / 聊天 / 离开 / 结束等新事件 |
| [玩法模组](docs/MODS_ZH.md) | 方案不只是 AI，也可以是一套规则（`kind: mod`）：英雄肉鸽、无尽守城两个示例 |
| [网关](docs/GATEWAY_ZH.md) | WebSocket / JSON：任何语言、浏览器、远程机器都能接；JS 客户端 + 演示页 |
| [大模型工具接口](docs/MCP_ZH.md) | MCP 服务器：Claude 等 agent 直接看局面、下命令、问玩家、截图 |
| [JASS 对外接口](docs/JASS_API_ZH.md) | 从外面随时调游戏里 1291 个 JASS 函数（每个都有中英文说明和参数名）：远见「JASS 控制台」页、命令行、HTTP、Python；画面效果和交互（聊天命令、按钮菜单、方向键）实测清单和截图 |
| [远见多语言](docs/I18N_ZH.md) | 中文 / English 切换；界面、配置项、单位技能名、native 说明、方案简介怎么翻；加一种新语言的四步 |
| [RPG 辅助与玩伴](docs/COMPANION_ZH.md) | JASS 通道（按名字调任意 native）、造单位/设盟友、自定义地图的单位名字、玩伴框架（四种形态、跟随/助战/加血、台词和本地大模型） |
| [架构](docs/ARCHITECTURE_ZH.md) | 分层、目录、为什么这样做低延迟、五档接口 |
| [接口目录](docs/API_ZH.md) / [api.json](docs/api.json) | 每个接口的实测状态和底层机制（从代码生成） |
| [W3P 协议](docs/W3P_PROTOCOL_ZH.md) | 运行时和外部程序之间的全部契约（共享内存布局、操作码、回执）；用别的语言接入看这个 |
| [对战平台](docs/ARENA_ZH.md) | 让不同玩家的 AI 互相打：裁判、视野过滤、协议、地基实验 |

## 目录

```
start.bat      唯一的入口：从零部署 + 打开远见（逻辑在 setup.ps1，系统自带的 PowerShell 5.1 就能跑）；stop.bat 彻底停止全部
prebuilt/      注入运行时的发布版二进制（render_hook.dll / war3_launch.exe / war3_ui_command.exe）+ SHA256 清单
sdk/python/    接口层：w3world 世界状态和事件 / w3fast 快车道 / w3canvas 画板 / w3input 输入 / w3cmd 已验证配方 /
               w3claim 仲裁 / w3chan 控制通道（兜底）/ w3state 旧快照（兼容）
               openwar3/  对外门面 Game + Bot（从这里开始）
               w3paths.py 唯一的路径中心
brains/        决策层
  xwar3/         参考大脑：strategy（策略，秒级）+ reflex（毫秒层 4 个进程）+ worldmodel（胜率模型）
  examples/      hello_bot（经济）、rush_bot（经济 + 造兵 + 出击）、macro_bot（运营）、micro_bot（微操 + 打野）、
                 buddy（玩伴）、mod_hero_roguelike / mod_endless_defense（玩法模组）
console/       远见网页指挥台（FastAPI + React；编好的网页 web/dist/ 随仓库发，改了源码 npm run build 再一起提交）
gateway/       网关（WebSocket / JSON，任何语言接入）+ clients/js 客户端和演示页
director/      自动运镜、头顶血条
speech/        头顶聊天气泡 + 本地大模型（LM Studio）
runtime/       farm.py：多实例编排（每局按 next_game.json 重开）
data/          order-ids.txt；tools/ 从游戏提取数据；game/（提取结果，不进 git）
tools/         run_tests.py、play.py、run_scheme.py（方案运行器）、war3_mcp.py（MCP）、i18n_check.py
VERSION        当前版本号（远见和 war3ai.com/version.json 比新旧）
bin/           运行时二进制 + 运行期实例数据 + start.bat 装的私有 Python（和需要时的 Node.js，bin/env/）；不进 git
```

## 测试

```bash
python tools/run_tests.py          # sdk / 参考大脑 / 毫秒层 / 指挥台 / 气泡 / 示例 / 网关，每套一个子进程
python tools/run_tests.py gateway  # 只跑名字里带 gateway 的；名字打错会报「没有匹配的测试套」
```

这些都不需要开游戏。

## 第三方软件与商标

- [AMAI](https://github.com/SMUnlimited/AMAI) 有自己的许可：`start.bat` 下载后在本机生成参考大脑的战略数据，生成物不进仓库。
- 魔兽争霸（Warcraft）是暴雪娱乐（Blizzard Entertainment）的商标。本项目与暴雪没有关联，也未获其认可。

## 许可

源码按 [Apache License 2.0](LICENSE) 授权。`prebuilt/` 里的运行时二进制不在这个许可范围内：可以免费使用，也可以随本项目原样分发，
但不附带源码。见 [NOTICE](NOTICE)。

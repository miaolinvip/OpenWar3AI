# 真人 vs xwar3

此模式在一个 Warcraft III 1.27a 客户端中运行：真人操作 Player 0，完整参考大脑 `builtin/xwar3` 指挥 Player 1。xwar3 使用全图信息，生产、建造、研究仍消耗 Player 1 自己的正常资源。

## 使用

1. 启动 Farsight，进入「实例与开局」，选择一个已停止的实例。
2. 将「对局模式」切换为「玩家 vs xwar3」。
3. 选择标准对战地图、真人种族 P0、xwar3 种族 P1。「镜像」表示 AI 与真人同族。
4. 点击「开始陪练」，等待状态变为「xwar3 已接管 P1」，然后在游戏窗口中操作 P0。

选择此模式会选定 `builtin/xwar3`、将倍速设为 1×、原生电脑难度设为基础档，并关闭自动导播。首版固定 P0 / P1，使用标准真人对电脑地图；自定义地图槽位不符合要求时会明确报错。

玩家模式直接以游戏原生全屏启动。此版本不再使用去边框铺满屏幕，也不拦截 Alt+Enter 切换窗口；切走游戏使用 Alt+Tab。最小化或切到后台时，监督器等待窗口恢复，不会因为游戏时钟停住、HUD 或快照暂时不可读而重开；回到前台继续同一局。游戏进程真正退出时仍按原逻辑重开。

「只起游戏」保留独立启动游戏的用途；之后点击「只起 AI」也会自动暂停 P1 原生 AI，再接入 xwar3。玩家模式暂时只支持参考大脑，不能切换到教学 Bot 或玩法模组。

## 每局调用顺序

```text
Farsight start_test
  ├─ farm --keep → human-fullscreen/war3_launch.exe → 注入 Runtime、原生全屏开局
  └─ run_brain.py --match-mode human_vs_openwar3ai
      → 等待当前 PID 的 Runtime / 对局就绪
      → 检查 P0 本机真人、P1 敌对 Computer、仅一个真人
      → dev Game：PauseCompAI(Player(1), true)
      → P1 Session：确认自己的快照、资源、人口和大厅就绪
      → 启动 mod_combat / mod_scavenge / mod_spy / mod_fight
      → 启动策略 Brain
```

策略层与 reflex 通过 `WAR3_CONTROL_PLAYER=1` 使用同一控制身份。`Session` 保留全图单位，并从同一局 W3P 玩家表读取 P1 的金、木、人口；`Cmd` 使用独立 Player 1 车道。缺失 P1 资源时等待，不回退读取 P0 的资源。JASS 准备使用临时 dev 车道，完成后关闭。

旧 AI 测试模式不设置这个身份，保持本机玩家的原有行为。共享快照不被原地修改，控制台仍可观察真人 P0。

## 结束、重开与停止

- 「结束这一局」「立即按新设置重开」保留 farm 和监督器。每个新游戏 PID 都重新暂停 P1 原生 AI、重建策略与 reflex。
- 游戏关闭或崩溃后，farm 会重开；监督器接管新 PID。监督器局间自动加载新源码时也会保留模式参数。
- 「停止 AI」停止策略和 reflex，保留本局，P1 原生 AI 仍暂停；再次「只起 AI」可以重新接管。
- 「停止实例」「全部停止」使用现有停止牌与进程树清理逻辑，停止后不会自动重开。
- 切换模式前先停止实例。地图、种族与倍速可以保存在下一局设置中，再重开生效。

## 状态与排错

实例卡片显示等待开局、暂停原生 AI、等待 xwar3、接管成功或失败。状态只对应当前游戏 PID。

- `bin/instN/takeover.json`：当前接管阶段和失败原因。
- `bin/instN/brain_supervisor.out`：启动、暂停成功、模块 PID、异常。
- `bin/instN/brain.log`、`mod_*.out`：策略和各模块输出。
- `bin/farm/instN.out`：注入、种族设置、控制通道与开局信息。

接管准备失败时，不启动策略/reflex，也不会仅因准备失败关闭真人游戏。可停止 AI 后重试；槽位不符时换用标准对战地图并重开。此模式保留 xwar3 原有全图感知、战术、自动认输和对局时限设置；不是公平视野模式，也不新增难度系统。

对局记录中的胜负以 xwar3（P1）为视角，实验元数据包含模式、控制玩家与 `fair=false`，对手标记为 `human`。

## 部署

当前发布版 `war3_launch.exe` 将 `-window` 固定写在两个游戏命令模板中，且未提供全屏参数；其 native 源码未随仓库发布。`sdk/python/w3fullscreen.py` 仅对 SHA-256 匹配的 2026.09.26.1644 发布版生成 `bin/human-fullscreen/war3_launch.exe` 副本，将这两处参数替换为等长空格。副本旁的 Runtime DLL 与原文件逐字节相同。`prebuilt/`、原版 `bin/war3_launch.exe`、发布清单及游戏文件都不修改；普通 AI 测试继续使用原启动器。遇到未知启动器版本会明确报错，需要适配新版本，不能按旧位置盲改。

已有实例需先点击「停止实例」，再点击「开始陪练」；仅结束当前局或停止 AI 不会更新常驻 farm 的源码。

本次同时构建 `console/web/dist` 并更新源码指纹。若 Farsight 后台已启动，需要运行 `start.bat restart` 加载后端改动，再刷新网页；游戏 Runtime 二进制无需替换。

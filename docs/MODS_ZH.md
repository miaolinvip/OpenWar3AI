# 玩法模组：方案不只是 AI，也可以是一套规则（09-25）

`kind: bot` 的方案是一个 AI，替你打；`kind: mod` 的方案是**一套规则**：你自己在游戏窗口里玩，模组出题 ——
开局布置、按时间 / 事件刷怪、给奖励、屏幕上给你按钮和选项卡、定胜负。

## 1. 两个示例（远见「AI 方案」→ 内置）

| 模组 | 玩法 | 用到的底座能力 |
|---|---|---|
| **英雄肉鸽** `builtin/hero-roguelike`（`brains/examples/mod_hero_roguelike.py`） | 你只有一个圣骑士，怪一波波从四面围上来；每升一级屏幕中间三选一强化（选的时候游戏暂停）；撑过 10 波赢，英雄死了输 | `g.ui.choice`（可点卡片 + 暂停）、`hero.levelup` / `killed` / `spell.cast` / 聊天 `-help`、JASS 改英雄属性 / 给物品 |
| **无尽守城** `builtin/endless-defense`（`brains/examples/mod_endless_defense.py`） | 怪从对面出生点沿地上的红线冲向主城；每守住一波给金子；屏幕按钮 / F7 提前叫波奖励 ×1.5；F8 后左键点地面放一座免费箭塔（右键取消） | `g.ui.button`、`g.ui.hotkey`、`g.ui.mouse`（抓地面点击）、画板面板 / 进度条 / 路线、JASS 刷怪 / 加金子 |

```bash
python tools/play.py --bot brains/examples/mod_hero_roguelike.py --inst 9     # 起一局、模组接管，你在游戏窗口里玩
```

09-25 实机：肉鸽 —— 第一波刷出来、HUD 在走；外部把英雄提到 3 级 → 屏幕中间弹卡片、游戏时钟停住；点两次卡片 → 两次强化生效（力量 22 → 27）、时钟恢复。
守城 —— HUD / 路线 / 按钮都在；F8 + 点地面 → 主城边上多一座防御塔；波次没清完时点按钮 → 「这一波还没清完」。

## 2. 写一个模组

```python
from openwar3 import Mod

class Survive(Mod):
    name = "survive"

    def on_start(self, g):
        super().on_start(g)                        # 单人局检查 + 压住电脑对手（neutralize_ai）
        self.foe = self.wave_player(g)             # 一个空槽玩家当「刷怪方」：和谁都不结盟、没有电脑 AI
        self.every(30, self.wave)                  # 每 30 游戏秒（暂停时不走）
        g.ui.hotkey("F7", lambda g, ev: self.wave(g))

    def wave(self, g):
        self.spawn_ring(g, self.foe, "ugho", 6, self.home(g), 1400, attack_to=self.home(g))

    def on_event(self, g, ev):
        if ev.kind == "unit.died" and ev.type == "htow":
            self.finish("loss", "主城被推了")
```

| Mod 提供的 | 说明 |
|---|---|
| `on_start / on_tick / on_event / on_end` | 和 Bot 一样；覆盖 `on_start` / `on_tick` 记得先 `super()` |
| `every(秒, fn, first=)` / `after(秒, fn)` | 按**游戏时间**的定时器，`fn(g)` |
| `finish(result, reason)` | 结束这一局（'win' / 'loss' / 'unknown'）：运行器下一拍停，屏幕中间画结果面板，方案战绩按它记 |
| `wave_player(g)` | 第一个空槽玩家 |
| `spawn_ring(g, 玩家, 单位, 个数, 中心, 半径, attack_to=)` | 在一圈上刷单位（直接 CreateUnit，一波几十个也不卡），返回 JASS 句柄 |
| `alive_of(g, 玩家)` / `attack_move_all(g, 玩家, 点)` | 某个玩家的活单位 / 全部攻击移动过去（隔几秒调一次，怪就追着走） |
| `home(g)` / `hud(g, 标题, 行)` | 我方主城位置 / 右上角面板 |
| `neutralize_ai = True` | 开局把电脑对手压住：它的单位每 5 秒暂停一遍、金木清零（对战图里总有一个电脑，模组自己定规则时不要它搅局） |
| `single_player_only = True` | 有别的真人玩家就拒绝运行（改世界的 JASS 会让别人不同步） |
| `linger_s = 6` | 胜负已分时，方案运行器发结束令之前在结果画面停几秒 |

`Bot.finish()` 也加在了 Bot 上：普通 bot 也可以自己宣布结束。

几个细节（09-26 审查后改）：
* 定时器里抛异常只打日志，别的定时器和这一拍照常跑（以前一个出错整拍都停）；`finish()` 之后 `on_tick` 不再跑定时器。
* `spawn_ring` 返回前等下一份世界状态发布，紧接着调 `alive_of` / `attack_move_all` 就能看到刚刷的怪。
* `neutralize_ai` 只暂停新出现的电脑单位，不再每 5 秒把全部单位重新暂停一遍。
* 模组画的按钮、热键只属于自己：同时开着远见、MCP、网关也不会互相冲掉（见 `docs/UI_INPUT_ZH.md` §4）。
* 导入已经装过的方案（同一个 id），远见会先问要不要替换，不会悄悄覆盖。

## 3. 做成方案、分享

`scheme.json` 写 `"kind": "mod"`（入口里定义一个 `Mod` 的子类）。模组默认**不是公平模式**、**不按对战规则判胜负**（胜负由 `finish` 报）。
导出 zip / 导入 / 信任和 bot 方案一样（`docs/SCHEMES_ZH.md`）：模组是代码，别人的模组第一次运行前要确认信任。

## 4. 边界

* 只能单人局：刷单位、改属性走 JASS 通道，多人局会不同步（这是锁步模型决定的；要多人玩法得先有同步通道，见平台路线图）。
* 模组看全图（它是裁判，不是玩家）。
* 对战图里的电脑对手只是被「压住」，没有被移除（移除会触发对战规则的胜利判定）。

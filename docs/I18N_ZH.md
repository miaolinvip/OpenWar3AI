# 远见多语言（中文 / English）

09-25 起远见（Farsight，`console/web`）支持中文和英文，结构按「以后加更多语言」设计。
右上角 🌐 下拉（或 设置 → 外观 → 语言 / Language、命令面板 Ctrl K）切换；选择存在浏览器里，切换会刷新一次页面。
第一次打开按浏览器语言选：浏览器是中文就中文，否则英文。

## 1. 怎么工作

源码里照写中文，包一层 `tx()`：

```tsx
<button>{tx("保存")}</button>
toast(tx("实例 {0} 已停止", n));          // 占位符 {0} {1}… 按参数顺序，译文里可以换位置
```

`tx` 在 `src/lib/i18n.ts`：中文界面原样返回；别的语言按**中文原文**查字典（`src/i18n/en.json`），查不到就显示中文。
键就是中文原文，所以读代码的人一眼知道这里显示什么，也不用另起一套键名。

| 部分 | 怎么翻 | 数据在哪 |
|---|---|---|
| 界面文字（1195 条） | `tx("中文")` | `console/web/src/i18n/en.json` |
| 后端发来的固定标签（152 条）：AI 配置项的组名/名字/说明/选项、日志源名、指令来源名、导播预设和运镜序列名 | 显示处 `tx(值)` | 同上；键由 `tools/i18n_check.py` 的 `backend_keys()` 从后端代码收集 |
| 单位 / 建筑 / 英雄 / 技能 / 物品 / 科技名（2024 个码） | `/api/meta?lang=en` 直接给英文名；另给 `nameMap`（中文名 → 英文名），后端直接给中文名的地方 `tx(名字)` 也能换 | `data/game/names_en.json` |
| JASS native 说明（1291 条） | 每条 zh / en 两份，`/api/jass/natives?lang=` | `sdk/python/openwar3/jass_docs.json` |
| AI 方案的名字 / 简介 | `scheme.json` 可选字段 `"i18n": {"en": {"name": "…", "description": "…"}}`，远见按界面语言挑 | 各方案自己的 scheme.json；内置 6 个已写 |
| 日期时间 | `toLocaleString(locale)` | `lib/i18n.ts` 的 `locale` |

英文游戏名的来源：技能和物品取暴雪数据表（AbilityData.slk 的 comments、ItemData.slk 的 comment）里的英文，
单位/科技等按 UnitUI.slk 的内部名等线索对照英文版官方名翻译（本机游戏是中文版，数据里没有现成的英文名）。

## 2. 不翻的（内容，不是界面）

* **AI 自己写的东西**：大脑的决策理由（「打野·营地被筛·离家太远」）、日志正文、导播的切镜理由、战斗决策里的说明。
  这些是参考大脑（一个中文写的 AI）的思考记录，和界面文字不是一回事；以后要翻得在大脑那边按语言写。
* **直播页 `/log` 的滚动播报**：句子是服务端按模板拼的中文，给中文直播用。页面上的固定文字已经翻了。
* **用户自己输入的**：问题备忘的标题/备注、方案作者没写 `i18n` 的方案名。
* 问题备忘的分类**存的是中文原文**（换语言也能按分类筛），显示时再翻。

## 3. 加一种语言（例如日语）

1. 导出要翻的键：
   ```bash
   python tools/i18n_check.py --todo ja --out todo_ja.json
   ```
   得到 `{"中文原文": ""}`，把值填上（可以只翻一部分，没翻的显示中文）。
2. 存成 `console/web/src/i18n/ja.json`。
3. `console/web/src/lib/i18n.ts` 里两处各加一行：
   ```ts
   import ja from "../i18n/ja.json";
   { code: "ja", name: "日本語", htmlLang: "ja" },      // LANGS
   const DICTS = { en, ja };
   ```
   `type Lang` 加上 `"ja"`。
4. 游戏名、native 说明、方案简介要另外给（`names_ja.json`、`jass_docs.json` 加 `ja` 字段、scheme.json 的 `i18n.ja`），
   后端 `C.meta(lang)` / `jass.catalog(lang=)` 目前只认 zh / en，加语言时照 en 的写法扩一下。
5. `cd console/web && npm run build`，跑 `python tools/i18n_check.py` 看缺几条。

## 4. 写新界面时

* 显示给人看的中文都包 `tx()`；拿来比对的数据（后端的枚举值、存盘的分类）**不要**包 —— 包了在英文界面下比对就不相等了。
  显示后端枚举时写 `tx(值)`，再往 en.json 加译文。
* 一个短词两种意思（「分」= 分钟 / 分数）要拆成不同的键（例如 `tx("{0} 分钟", n)`），字典是一对一的。
* 测试 `console/tests/test_i18n.py` 守着：每个键都有英文、占位符对得上、JSX 里没有裸写的中文、语言表和字典文件对得上、
  英文游戏名覆盖中文名表。改完界面跑它，缺什么它会列出来。
* 浏览器控制台 `window.__i18nMissing` 是这次打开页面以来查不到译文的键（多半是后端数据）。

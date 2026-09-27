# -*- coding: utf-8 -*-
"""大脑可在线调的配置（Farsight 控制台「设置 / AI 配置」那一页读写的就是这份）。

实例目录下 brain_config.json 只存**改过的项**；大脑每 2 秒看一次修改时间，变了就重读、套到战术常量和建造表上。
没有这个文件 = 全用默认值 = 和没有这一层时行为一模一样。
"""
from __future__ import annotations

import json
import os
import time

from . import winprob as W

SCHEMA = [
    # key, 分组, 标签, 类型, 默认, 最小, 最大, 步长, 说明
    # ⚠ 类型 choice：值是字符串，coerce 只认 CHOICES 里的；Farsight 的「AI 决策」页目前只渲染 bool，
    #   这一项要改 brain_config.json（或等前端认 choice）才能切
    ("strategy_engine", "开关", "建造需求来源", "choice", "table", None, None, None,
     "table = 手写的每族建造表（默认）；amai = AMAI 战略引擎（选战略/换战略/反制/建造序列解释执行）"),
    ("opening", "开关", "开局谱", "choice", "none", None, None, None,
     "照高手录像定的开局顺序（war3-brain/brain/openings/*.json，放进去就多一个选项）。只在「建造需求来源 = 手写建造表」、种族对得上时生效；"
     "走完（或到时间）交回建造表。开局途中切走立刻停，切过来要等下一局才从头走"),
    ("enable_build", "开关", "建造/训练/研究", "bool", True, None, None, None, "关掉后不再按建造表下单（已下的不撤）"),
    ("enable_gold", "开关", "派矿工", "bool", True, None, None, None, "闲着的工人送去金矿（每矿 5 人）"),
    ("enable_lumber", "开关", "派伐木", "bool", True, None, None, None, "矿满之后的闲人去砍树"),
    ("enable_construction", "开关", "续建停工地基", "bool", True, None, None, None, "人族地基没人盖时派最近的工人"),
    ("enable_powerbuild", "开关", "人族关键建筑双敲", "bool", True, None, None, None, "资源和环境允许时，给安全分矿或卡人口农场追加一名农民；全局至多一名助手"),
    ("enable_repair", "开关", "人族建筑维修", "bool", True, None, None, None, "给已确认建成的受损建筑派农民维修，大厅和塔优先；最多同时两名维修工，保命优先"),
    ("enable_defense", "开关", "守家", "bool", True, None, None, None, "城镇受威胁时回防 / 保存实力 / 拼了"),
    ("enable_attack", "开关", "出击", "bool", True, None, None, None, "够强且时机到了打对面分矿/主基地/建筑"),
    ("enable_creep", "开关", "打野", "bool", True, None, None, None, "没到出击时机时带英雄打野"),
    ("enable_militia_creep", "开关", "人族民兵清分矿", "bool", True, None, None, None,
     "分矿守军较强时临时号召 5~6 名农民；只选主城附近、优先伐木农民，清完解除并留最近两人双敲大厅"),
    ("enable_harass", "开关", "骚扰小队", "bool", True, None, None, None, "AMAI 的 2 步兵/2 狮鹫骚扰农民"),
    # ★ 09-21 晚 大模型顾问实测持续失败、没有带来改善，退回非 LLM 模式，改为参考 AMAI 的方式。
    #   默认关掉。代码留着（brain/coach.py），要再试就在这里或远见「运营顾问」页单个实例打开；
    #   关着时 coach_plan() 返回 None，伐木人数和建造优先级完全按规则表，不发任何模型请求。
    ("enable_coach", "开关", "运营顾问（本地大模型）", "bool", False, None, None, None,
     "每 20 游戏秒把局面发给本地大模型（LM Studio），让它给工人配比、优先出什么、这一分钟的姿态。"
     "默认关（09-21 实测没有带来改善）。异步、钳位，伐木数只许比规则多。见 brain/coach.py"),
    ("enable_heroes", "开关", "英雄加点/复活", "bool", True, None, None, None, "按 learned.json 加点、祭坛复活"),
    ("enable_unit_labels", "开关", "血条编号", "bool", True, None, None, None,
     "我方英雄/作战单位血条上方显示本局编号（#23），日志里同一个单位带同一个编号，方便指出是哪个单位出了问题。"
     "用气泡 DLL；这个实例开着对话气泡时自动让开。见 brain/unitids.py"),
    ("aggression_bias", "出击", "进攻性偏移", "float", 0.0, -20.0, 20.0, 0.5, "加在 AMAI 进攻性上；正数更敢打"),
    ("aggression_floor", "出击", "进攻性下限", "float", 0.0, -20.0, 20.0, 0.5,
     "进攻性再低也不低于这个。AMAI 有些人格的进攻性列是负的（09-18 实机抽到 RomuLus -11），再叠上「英雄全死了 -10」就变成 -12，结果整局 0% 出击、被推平"),
    ("min_attack_strength", "出击", "最低出击强度", "float", 4.0, 0.0, 60.0, 1.0, "我方强度低于这个不出击"),
    ("chain_left", "出击", "接着打的兵力比例", "float", 0.70, 0.1, 1.0, 0.05, "兵力还剩出发时这么多就接下一个目标"),
    ("gather_ready", "出击", "集结完成比例", "float", 0.80, 0.3, 1.0, 0.05, "主力 1200 内强度占全军这么多就出发"),
    ("gather_max_s", "出击", "集结最长等待（游戏秒）", "float", 40.0, 0.0, 180.0, 5.0, "等这么久没凑齐也出发"),
    ("retreat_cooldown_s", "出击", "撤回冷却（游戏秒）", "float", 45.0, 0.0, 300.0, 5.0, "撤回来之后这么久不再出击"),
    ("buildings_when_foe_le", "出击", "对面兵力打没的判据", "float", 0.25, 0.0, 1.0, 0.05, "对面兵力 <= 我方这么多倍时拆零散建筑、不汇合援兵"),
    # ⚠ 09-21 起，下面标了 `W.` 的默认值是 `brain/winprob.py` 从**校准过的胜率/损失份额**推出来的，
    #   和 tactics.py 引用的是同一个名字 —— 两边不可能再分叉。要改就改 winprob 里的三条线
    #   （P_COMMIT / P_GIVEUP / LOSS_CHEAP），别在这里填一个新数字。
    ("attack_margin", "出击", "出击要求的兵力优势", "float", W.R_ATTACK_EXPANSION, 0.5, 3.0, 0.05,
     "对目标守军和可抵达援军的兵力优势；另计塔、路线和有限进攻性加分。默认 = 打分矿 %.0f%% 把握"
     % (100 * W.P_EXPANSION)),
    # ⚠ 09-21：下面四项的默认值**必须**和 brain/tactics.py 文件头的字面量一致 —— apply() 会用这里的默认值
    #   覆盖那边的模块常量，两边不一致时代码里写的那个数从来不会生效。
    #   47042ea（#95 / v10「英雄安全」）就是这么失效的：tactics 改成 0.50/0.70，这里还是 0.35/0.60，
    #   于是 v10~v14 五个版本一直在改一个没上线的血线。tests/test_config_consistency.py 现在守着这件事。
    ("hero_retreat_hp", "出击", "英雄残血先回家", "float", 0.50, 0.0, 0.9, 0.05, "出击/打野中英雄血量低于这个比例单独回家"),
    ("hero_ready_hp", "出击", "英雄出门血线", "float", 0.70, 0.0, 1.0, 0.05, "英雄血量不到这个比例不发起出击、不去新营地"),
    ("support_min_ratio", "出击", "支援交战要求的兵力比", "float", W.R_GIVEUP, 0.3, 2.0, 0.05,
     "能赶去支援的人合起来 >= 交战那团敌人 × 这个才去；不够就整队撤（进攻/集结/打野中）。"
     "默认 = 跌破 %.0f%% 把握（胜率地板 %.0f%%）" % (100 * W.P_GIVEUP, 100 * W.win_floor())),
    ("hero_lag_r", "出击", "行军等英雄的距离", "float", 1500.0, 500.0, 5000.0, 100.0, "行军时英雄比主力离目标远这么多，主力就等英雄"),
    ("raid_margin", "出击", "拆家门口分矿要求的兵力比", "float", W.R_ATTACK_MAIN, 0.8, 4.0, 0.1,
     "我方强度 >= 那座分矿附近守军 × 这个才去拆。默认 = %.0f%% 把握" % (100 * W.P_COMMIT)),
    ("flee_local_ratio", "出击", "进攻中当地打不过就撤的比例", "float", W.R_GIVEUP, 0.3, 2.0, 0.05,
     "进攻中主力 2500 内我方强度 < 主力 1500 内敌方（兵 + 塔）× 这个就整队撤。"
     "默认 = 跌破 %.0f%% 把握" % (100 * W.P_GIVEUP)),
    ("clear_power", "打野", "清分矿野怪的战力比", "float", W.R_CREEP, 0.8, 5.0, 0.1,
     "对分矿旁边营地的战力比 >= 这个就先去清。默认 = 预计损失份额 <= %.0f%%" % (100 * W.LOSS_CHEAP)),
    ("creep_power_min", "打野", "打得过的战力比", "float", W.R_CREEP, 1.0, 6.0, 0.1,
     "战力 = 总血 × 每秒伤害（召唤物照算），我方 / 营地 >= 这个才算现在打得过；不够的按涨兵速度估等多久，和走远路比。"
     "默认 = 预计损失份额 <= %.0f%%（964 个战斗文件校准：打野每一档战力比都赚钱）" % (100 * W.LOSS_CHEAP)),
    ("creep_engage_power", "打野", "到场开打的战力比", "float", W.R_CREEP, 0.8, 5.0, 0.1,
     "营地 1500 内到场的人对营地的战力比 >= 这个才开打"),
    ("creep_max_home", "打野", "营地离家最远", "float", 7000.0, 1000.0, 15000.0, 250.0, "而且要离家比离对面主基地近"),
    ("creep_abort_power", "打野", "打不动回家的战力比", "float", W.R_CREEP_ABORT, 0.1, 3.0, 0.1,
     "打野中主力对营地剩下的战力比 < 这个就回家。默认贴着损失份额的天花板 —— "
     "真正管「打不动」的是按进展判的那条闸，不是比值"),
    ("farm_left", "经济", "剩多少人口补农场", "int", 6, 0, 30, 1, "再加 2 × 出兵建筑数"),
    ("peons_per_mine", "经济", "每矿农民", "int", 5, 1, 8, 1, "一座矿最多 5 个（固定规则）"),
    ("peons_lumber", "经济", "伐木农民", "int", 5, 0, 20, 1,
     "木头多了（见下）按一半算。默认是人族表的值（09-18 从 8 降到 5：8 人时 10 分钟木头堆到 900）；亡灵表是伐木食尸鬼数"),
    ("peons_max", "经济", "农民上限", "int", 28, 5, 60, 1, ""),
    ("lumber_high", "经济", "木头多的门槛", "int", 400, 100, 5000, 50, "木头 >= 这个时伐木人数减半、少造农民（人族表 09-18 从 800 降到 400）"),
    ("rich_gold", "经济", "钱多的门槛", "int", 1500, 300, 10000, 100, "金子 >= 这个时多开矿、加兵营/狮鹫笼"),
    ("expand_army", "经济", "第二矿插队所需兵数", "int", 4, 0, 30, 1,
     "有这么多兵（或 6 分钟）第二座矿插到出兵前面（09-23 一度照 AMAI 改成 8；AMAI 15 局一次二矿都没开过、一矿全押兵，"
     "两矿是我们的长处 —— 改回 4，靠门口的塔 + 民兵 + 6~13 分钟不出远门扛它那一波）"),
    ("tier2_army", "经济", "兵够几个就先升二本", "int", 4, 0, 30, 1,
     "兵（不算英雄/工人）够这么多，升二本就排到出兵和开二矿前面（09-18：原来排在所有兵后面，二本中位 10 游戏分钟）"),
]

DEFAULTS = {k: d for k, _g, _l, _t, d, *_ in SCHEMA}
CHOICES = {"strategy_engine": ("table", "amai")}
CHOICE_LABELS = {"strategy_engine": {"table": "手写建造表", "amai": "AMAI 战略引擎"}}


def choices_of(key: str) -> tuple:
    """开局谱的选项跟着 brain/openings/ 里的文件走（放进去就多一个），别的是写死的。"""
    if key == "opening":
        from . import opening
        return opening.choices()
    return CHOICES.get(key, ())


def labels_of(key: str) -> dict:
    if key == "opening":
        from . import opening
        return opening.labels()
    return CHOICE_LABELS.get(key, {})


def schema() -> list[dict]:
    return [dict(key=k, group=g, label=l, type=t, default=d, min=mn, max=mx, step=st, desc=desc,
                 **({"options": list(choices_of(k)), "labels": labels_of(k)} if t == "choice" else {}))
            for k, g, l, t, d, mn, mx, st, desc in SCHEMA]


def coerce(key: str, value):
    row = next((r for r in SCHEMA if r[0] == key), None)
    if row is None:
        raise KeyError(key)
    t, mn, mx = row[3], row[5], row[6]
    if t == "choice":
        v = str(value)
        if v not in choices_of(key):
            raise ValueError(f"{key} 只能是 {choices_of(key)}")
        return v
    if t == "bool":
        return bool(value)
    v = float(value)
    if mn is not None:
        v = max(mn, v)
    if mx is not None:
        v = min(mx, v)
    return int(round(v)) if t == "int" else v


def load_overrides(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[k] = coerce(k, v)
        except (KeyError, TypeError, ValueError):
            continue
    return out


class LiveConfig:
    """每 2 秒（墙钟）看一次文件修改时间；变了就重读。values = 默认值 + 覆盖。"""

    def __init__(self, path: str):
        self.path = path
        self.mtime = None
        self.checked = 0.0
        self.overrides: dict = {}
        self.values: dict = dict(DEFAULTS)
        self.version = 0

    def refresh(self) -> bool:
        now = time.monotonic()
        if now - self.checked < 2.0:
            return False
        self.checked = now
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            m = None
        if m == self.mtime:
            return False
        self.mtime = m
        self.overrides = load_overrides(self.path) if m is not None else {}
        self.values = dict(DEFAULTS, **self.overrides)
        self.values["__overrides__"] = dict(self.overrides)
        self.version += 1
        return True

    def __getitem__(self, key: str):
        return self.values[key]


def apply(values: dict, profile: dict) -> None:
    """套到战术模块常量和建造 profile 上（战术常量是模块级的，一个进程一个大脑，直接改模块属性）。"""
    from . import tactics as T
    T.CREEP_POWER_MIN = values["creep_power_min"]
    T.CREEP_ENGAGE_POWER = values["creep_engage_power"]
    T.CREEP_MAX_HOME = values["creep_max_home"]
    T.CREEP_ABORT_POWER = values["creep_abort_power"]
    T.CHAIN_LEFT = values["chain_left"]
    T.GATHER_READY = values["gather_ready"]
    T.GATHER_MAX_S = values["gather_max_s"]
    T.RETREAT_COOLDOWN_S = values["retreat_cooldown_s"]
    T.BUILDINGS_WHEN_FOE_LE = values["buildings_when_foe_le"]
    T.ATTACK_MARGIN = values["attack_margin"]
    T.HERO_RETREAT_HP = values["hero_retreat_hp"]
    T.HERO_READY_HP = values["hero_ready_hp"]
    T.SUPPORT_MIN_RATIO = values["support_min_ratio"]
    T.HERO_LAG_R = values["hero_lag_r"]
    T.RAID_MARGIN = values["raid_margin"]
    T.FLEE_LOCAL_RATIO = values["flee_local_ratio"]
    T.CLEAR_POWER = values["clear_power"]
    # 建造 profile 只套**改过的**项：人族表和亡灵表的默认值不一样（亡灵伐木食尸鬼 4、侍僧上限 15），套默认值会把亡灵表冲掉
    overrides = values.get("__overrides__", {})
    for k in ("farm_left", "peons_per_mine", "peons_lumber", "peons_max", "min_attack_strength", "lumber_high", "rich_gold", "expand_army",
              "tier2_army"):
        if k in overrides:
            profile[k] = values[k]

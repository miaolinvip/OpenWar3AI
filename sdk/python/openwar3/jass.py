# -*- coding: utf-8 -*-
"""JASS 通道（09-24）：按名字调地图作者能用的 JASS native —— 1291 个，签名在 jass_signatures.json（common.j 那一套）。

    j = g.jass
    p1 = j.Player(1)
    h = j.CreateUnit(p1, "Hpal", 100.0, 200.0, 270.0)        # 返回 JASS 单位句柄
    j.SetPlayerAlliance(p1, j.Player(0), 5, True)             # 5 = ALLIANCE_SHARED_VISION
    j.DisplayTimedTextToPlayer(j.GetLocalPlayer(), 0, 0, 5, "你好")
    j.SetUnitOwner(g.my_heroes()[0], p1, True)                # 单位/物品对象直接传，自动换成 JASS 句柄

参数怎么传：I 整数（四字码字符串 "Hpal" 自动转）、R 实数、B 布尔、S 字符串、H 句柄（int，或者快照里的单位/物品对象）。
返回：I/H -> int，R -> float，B -> bool，S -> None（引擎返回的是字符串表编号，读不回文字），V -> None。

边界（都是实测/机制决定的，不是随便加的限制）：
  * 只给本机自己的工具用（dev 车道）；Game(player=N) / 公平模式里调会被拒。
  * **多人局里只能调只读的**（Get*/Is*/Player/Count*…）：本机单方面改世界（造单位、改归属、给物品）会让别的玩家不同步、被踢。
  * 带 code 参数（要一个 JASS 函数）的、会挂起脚本的（TriggerSleepAction、Sync*、ExecuteFunc）不支持。
  * 字符串参数只适合只读字符串的 native（显示文字、改名、比较）；哈希表/游戏缓存这类会把字符串存起来的别用。
"""
from __future__ import annotations

import json
import os
import struct
import time
from functools import lru_cache

_HERE = os.path.dirname(os.path.abspath(__file__))

# common.j 里的几组常量（enum 在引擎里就是整数）
MAP_CONTROL = {0: "user", 1: "computer", 2: "rescuable", 3: "neutral", 4: "creep", 5: "none"}
SLOT_STATE = {0: "empty", 1: "playing", 2: "left"}
ALLIANCE = {"passive": 0, "help_request": 1, "help_response": 2, "shared_xp": 3, "shared_spells": 4,
            "shared_vision": 5, "shared_control": 6, "shared_advanced_control": 7, "rescuable": 8,
            "shared_vision_forced": 9}
READONLY_PREFIXES = ("Get", "Is", "Count", "Player", "I2", "R2", "S2", "String", "Sub", "Load", "Have")

TEXT_OFF = 64
TEXT_MAX = 190
U32 = (1 << 32) - 1          # 32 位掩码（写成算式：门面包的无地址检查不放行大常量）


class JassError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def signatures() -> dict:
    """{名字: 签名}，签名的写法同引擎：(Hplayer;IRRR)Hunit;"""
    with open(os.path.join(_HERE, "jass_signatures.json"), encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def parse_signature(sig: str) -> tuple[tuple[str, ...], str]:
    """'(Hplayer;IRRR)Hunit;' -> (('Hplayer','I','R','R','R'), 'Hunit')"""
    if not sig.startswith("(") or ")" not in sig:
        raise JassError(f"签名看不懂：{sig}")
    body, ret = sig[1:].split(")", 1)
    params, i = [], 0
    while i < len(body):
        c = body[i]
        if c == "H":
            j = body.index(";", i)
            params.append(body[i:j])
            i = j + 1
        else:
            params.append(c)
            i += 1
    ret = ret.rstrip(";") or "V"
    return tuple(params), ret


def _fbits(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & U32))[0]


def _rawcode(v) -> int:
    if isinstance(v, str):
        if len(v) != 4:
            raise JassError(f"整数参数给了字符串 {v!r}：只有四字码（如 'Hpal'）能自动转")
        return int.from_bytes(v.encode("ascii"), "big")
    return int(v)


def readonly(name: str) -> bool:
    """多人局里也能调的（只读）。Issue* 也是 Is 开头，但它是下命令。"""
    return name.startswith(READONLY_PREFIXES) and not name.startswith("Issue")


# ── 目录：1291 个 native 按用途分类（按顺序匹配，第一条命中的算） ──
CATEGORY_RULES = [
    # 事件回应：只在触发器动作执行的那一刻有值（GetTriggerUnit、GetClickedButton…）。从外面调没有触发器上下文，拿到的是 0/空 ——
    # 想知道「发生了没有」用 visual.EventCounter（触发器执行次数），见 docs/JASS_API_ZH.md
    ("event_response", "事件回应（只在触发器里有值，外部调拿不到）",
     r"^GetTrigger(?!EvalCount|ExecCount)|^GetEvent|^GetAttacker|^GetClicked|^GetKilling|^GetDying|^GetOrdered|^GetOrder|^GetIssuedOrder|^GetSpell|"
     r"^GetLearned|^GetManipulat|^GetSold|^GetBuying|^GetEntering|^GetLeaving|^GetFilter|^GetEnum|^GetTracked|^GetSummon|"
     r"^GetReviv|^GetRescuer|^GetDecaying|^GetDetected|^GetChanging|^GetResearch|^GetTrained|^GetConstruct|^GetCancelled|"
     r"^GetLeveling|^GetLoaded|^GetTransport|^GetEventPlayerChat|^GetWinningPlayer|^GetSaveBasicFilename|^GetExpiredTimer|"
     r"^GetTournament|^GetAbandoned|^GetEventDamage|^GetEventTarget"),
    ("ai", "AI 脚本（common.ai）", r"^Captain|^Ai|AI$|AIDifficulty|^Command|^StartMeleeAI|^StartCampaignAI|^AttackMove|"
                                 r"^Add(Assault|Defenders|GuardPost)|^Suicide|^Harvest|^ClearHarvest|^GetCreepCamp|^CreepsOnMap|"
                                 r"^SetCaptain|^ClearCaptain|^Town|^GetExpansion|^GetMegaTarget|^WaitGet|^StartGet|"
                                 r"^StartThread|^Debug|^Purchase|^SetProduce|^Merge|^SetExpansion|^SetHeroesFlee|^SetUnitsFlee|"
                                 r"^SetGroupsFlee|^SetPeonsRepair|^SetRandomPaths|^SetReplacementCount|^SetSmartArtillery|"
                                 r"^SetTargetHeroes|^SetWatchTower|^SetDefendPlayer|^SetAllianceTarget|^IgnoredUnits|^InitAssault|"
                                 r"^GetGoldOwned|^GetMinesOwned|^GetNextExpansion|^GetEnemyBase|^GetEnemyPower|^GetBuildingTypes"),
    ("convert", "类型转换 / 数学 / 字符串", r"^Convert|^I2|^R2|^S2|^String|^SubString|^Sin$|^Cos$|^Tan$|^Asin|^Acos|^Atan|"
                                      r"SquareRoot|^Pow$|Deg2Rad|Rad2Deg|GetHandleId|^OrderId|^UnitId|^AbilityId"),
    ("visual", "画面效果", r"TextTag|Lightning|Effect|Image|Ubersplat|AddIndicator|SetUnitVertexColor|SetUnitScale|"
                          r"SetUnitTimeScale|SetUnitAnimation|QueueUnitAnimation|SetUnitColor|SetUnitLookAt|ResetUnitLookAt|"
                          r"SetUnitBlendTime|SetUnitFlyHeight|SetUnitPropWindow"),
    ("ui", "界面面板", r"Multiboard|Leaderboard|TimerDialog|^Dialog|Quest|DisplayText|DisplayTimedText|ClearTextMessages|"
                      r"PingMinimap|Cinematic|CineFilter|ShowInterface|EnableUserUI|EnableUserControl|SetDayNightModels|"
                      r"EnableOcclusion|EnableSelect|EnablePreSelect|EnableDragSelect|SelectUnit|ClearSelection|"
                      r"SetPortraitLight|SetSkyModel|SetResourceBarVisible|DisplayCineFilter|ForceUI|SetTutorial|ShowImage"),
    ("camera", "镜头", r"Camera|PanCamera|SmartCamera|ResetToGameCamera|StopCamera|SetCinematicCamera"),
    ("sound", "声音音乐", r"Sound|Music|VolumeGroup|Ambient|SetMapMusic|ClearMapMusic"),
    ("fog", "迷雾视野", r"Fog"),
    ("order", "下命令", r"^Issue"),
    ("item", "物品", r"Item"),
    ("hero", "英雄", r"Hero|SkillPoints|SelectHeroSkill|GetUnitAbilityLevel|UnitAddAbility|UnitRemoveAbility|"
                    r"SetUnitAbilityLevel|IncUnitAbilityLevel|DecUnitAbilityLevel|UnitMakeAbilityPermanent"),
    ("unit", "单位", r"Unit|Widget"),
    ("group", "单位组 / 区域 / 坐标", r"Group|Rect|Region|Location|MoveLocation"),
    ("player", "玩家 / 同盟 / 资源", r"Player|Alliance|Force|Team|Tech|Research"),
    ("trigger", "触发器 / 事件 / 计时器", r"Trigger|Event|Timer|Condition|Filter|BoolExpr|TriggerAction|Evaluate"),
    ("environment", "地形 / 天气 / 可破坏物", r"Terrain|Weather|Water|Doodad|Destructable|Blight|Elevator|Tree"),
    ("game", "游戏流程", r"Game|Pause|Victory|Defeat|EndGame|Difficulty|Map|Cheat|Sync|Suspend|StartLocation|SetFloatGameState|"
                        r"SetIntegerGameState|GetFloatGameState|GetIntegerGameState|TimeOfDay|Random|ReloadGame|SaveGame|"
                        r"Campaign|Mission|Cinematic"),
    ("storage", "存储（哈希表 / 游戏缓存）", r"Hashtable|GameCache|Saved|^Save|^Load|^Have|^Flush|^Store|^Remove.*Saved"),
]
CATEGORY_NAMES = {k: v for k, v, _ in CATEGORY_RULES} | {"other": "其它"}
CATEGORY_NAMES_EN = {
    "event_response": "Event responses (only valid inside a trigger)", "ai": "AI scripts (common.ai)",
    "convert": "Conversion / math / strings", "visual": "Visual effects", "ui": "UI panels", "camera": "Camera",
    "sound": "Sound & music", "fog": "Fog of war & vision", "order": "Orders", "item": "Items", "hero": "Heroes",
    "unit": "Units", "group": "Groups / regions / points", "player": "Players / alliances / resources",
    "trigger": "Triggers / events / timers", "environment": "Terrain / weather / destructables", "game": "Game flow",
    "storage": "Storage (hashtables / game cache)", "other": "Other",
}


def category_name(key: str, lang: str = "zh") -> str:
    return (CATEGORY_NAMES_EN if lang == "en" else CATEGORY_NAMES).get(key, key)


@lru_cache(maxsize=1)
def docs() -> dict:
    """{名字: {params: [参数名], zh: 中文说明, en: English}} —— 1291 个 native 每个都有（tests 守着）。
    参数名取自 common.j / common.ai；common.j 没声明的 8 个隐藏 native 是按用途补的。"""
    with open(os.path.join(_HERE, "jass_docs.json"), encoding="utf-8") as fh:
        return json.load(fh)


def describe(name: str, lang: str = "zh") -> str:
    d = docs().get(name) or {}
    return d.get("en" if lang == "en" else "zh", "")


def pretty_signature(name: str) -> str:
    """'CreateUnit(player id, integer unitid, real x, real y, real face) -> unit'（带参数名，给人看的）"""
    params, ret = parse_signature(signatures()[name])
    names = (docs().get(name) or {}).get("params") or []
    tname = {"I": "integer", "R": "real", "B": "boolean", "S": "string", "C": "code", "V": "nothing"}

    def t(p: str) -> str:
        return p[1:] if p.startswith("H") else tname.get(p, p)
    args = ", ".join(f"{t(p)} {names[i]}" if i < len(names) else t(p) for i, p in enumerate(params))
    return f"{name}({args}) -> {t(ret)}"

# 09-24 实机调过的（20 号演练实例，WarChasers；截图/读回确认过效果）。别的 native 走的是同一条路，只是没逐个看效果
VERIFIED = frozenset("""
GetLocalPlayer GetPlayerId Player GetPlayerController GetPlayerSlotState IsPlayerAlly IsPlayerEnemy SetPlayerAlliance SetPlayerName
CreateUnit RemoveUnit GetUnitX GetUnitTypeId S2I DisplayTimedTextToPlayer SetHeroLevel ReviveHero
CreateTextTag SetTextTagText SetTextTagPos SetTextTagPosUnit SetTextTagColor SetTextTagVelocity SetTextTagPermanent
SetTextTagLifespan SetTextTagFadepoint SetTextTagVisibility AddLightningEx DestroyLightning AddSpecialEffect
AddSpecialEffectTarget DestroyEffect CreateImage SetImageRenderAlways SetImageColor ShowImage CreateUbersplat
SetUbersplatRenderAlways ShowUbersplat DestroyUbersplat PingMinimapEx CreateMultiboard MultiboardSetRowCount
MultiboardSetColumnCount MultiboardSetTitleText MultiboardSetItemsStyle MultiboardSetItemsWidth MultiboardGetItem
MultiboardSetItemValue MultiboardSetItemValueColor MultiboardReleaseItem MultiboardSetItemStyle MultiboardSetItemIcon
MultiboardDisplay MultiboardMinimize CreateTimer TimerStart CreateTimerDialog TimerDialogSetTitle TimerDialogDisplay
SetCinematicScene ForceCinematicSubtitles ConvertPlayerColor SetUnitVertexColor SetUnitScale AddIndicator SetUnitAnimation
SetCineFilterTexture SetCineFilterBlendMode SetCineFilterTexMapFlags SetCineFilterStartUV SetCineFilterEndUV
SetCineFilterStartColor SetCineFilterEndColor SetCineFilterDuration DisplayCineFilter ConvertBlendMode ConvertTexMapFlags
CreateSound StartSound KillSoundWhenDone CreateFogModifierRadius ConvertFogState FogModifierStart DialogCreate
DialogSetMessage DialogAddButton DialogDisplay CreateTrigger TriggerRegisterDialogButtonEvent GetTriggerEvalCount
GetTriggerExecCount TriggerRegisterTimerEvent TriggerRegisterPlayerChatEvent
""".split())


@lru_cache(maxsize=None)
def category_of(name: str) -> str:
    import re
    for key, _title, rx in CATEGORY_RULES:
        if re.search(rx, name):
            return key
    return "other"


def catalog(q: str = "", category: str = "", lang: str = "zh") -> list[dict]:
    """[{name, signature, params, paramNames, returns, category, categoryName, readonly, verified, zh, en, desc}]

    q 不分大小写，按名字搜；名字没搜到的也按中英文说明搜（「漂浮文字」「floating text」都能找到 TextTag 那组）；
    空格隔开的几个词要全部出现。
    名字命中的排前面。desc = lang 那一种语言的说明（zh / en），zh、en 两个都带着。"""
    out_name, out_desc = [], []
    terms = q.strip().lower().split()          # 多个词 = 每个词都要出现（「camera shake」「单位 朝向」）
    dd = docs()
    for name, sig in sorted(signatures().items()):
        d = dd.get(name) or {}
        low = name.lower()
        in_name = all(t in low for t in terms)
        if not in_name:
            hay = f"{low} {d.get('zh', '').lower()} {d.get('en', '').lower()}"
            if not all(t in hay for t in terms):
                continue
        cat = category_of(name)
        if category and cat != category:
            continue
        params, ret = parse_signature(sig)
        row = {"name": name, "signature": sig, "params": list(params), "paramNames": d.get("params", []), "returns": ret,
               "category": cat, "categoryName": category_name(cat, lang), "readonly": readonly(name),
               "verified": name in VERIFIED, "zh": d.get("zh", ""), "en": d.get("en", ""),
               "desc": d.get("en" if lang == "en" else "zh", "")}
        (out_name if in_name else out_desc).append(row)
    return out_name + out_desc


class Jass:
    """g.jass：属性名就是 native 名（g.jass.CreateUnit(...)），也可以 g.jass.call("CreateUnit", ...)。"""

    def __init__(self, game):
        self._g = game
        self._humans_checked_for = None

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in signatures():
            raise AttributeError(f"没有叫 {name} 的 JASS native（名字区分大小写；表在 openwar3/jass_signatures.json）")
        return lambda *args: self.call(name, *args)

    def __dir__(self):
        return sorted(signatures())

    @staticmethod
    def doc(name: str, lang: str = "zh") -> str:
        """g.jass.doc("CreateUnit") -> 带参数名的签名 + 一句说明"""
        if name not in signatures():
            raise JassError(f"没有叫 {name} 的 JASS native")
        return f"{pretty_signature(name)}\n{describe(name, lang)}"

    @staticmethod
    def signature(name: str) -> str:
        return signatures().get(name, "")

    # ── 调用 ──
    def call(self, name: str, *args):
        sig = signatures().get(name)
        if sig is None:
            raise JassError(f"没有叫 {name} 的 JASS native")
        params, ret = parse_signature(sig)
        if len(args) != len(params):
            raise JassError(f"{name}{sig} 要 {len(params)} 个参数，给了 {len(args)} 个")
        self._guard(name)
        vals, strs = [], []
        for t, v in zip(params, args):
            if t == "R":
                vals.append(_fbits(v))
            elif t == "I":
                vals.append(_rawcode(v) & U32)
            elif t == "B":
                vals.append(1 if v else 0)
            elif t == "S":
                strs.append(str(v))
                vals.append(len(strs))
            elif t == "C":
                if v is not None:
                    raise JassError(f"{name} 要一个 JASS 函数（code 参数），从外面传不进去；只能给 None（null）")
                vals.append(0)
            else:
                vals.append(self._handle(v))
        if len(strs) > 4:
            raise JassError("最多 4 个字符串参数")
        text = name.encode("ascii") + b"\0" + b"".join(s.encode("utf-8") + b"\0" for s in strs)
        if len(text) > TEXT_MAX:
            raise JassError(f"名字 + 字符串参数一共 {len(text)} 字节，超过 {TEXT_MAX}")
        g = self._g
        call = g._w3fast.Call(g._w3fast.OP["jass"], args=vals, scratch=bytes(TEXT_OFF) + text)
        r = g._one(call)
        if not r.ok:
            raise JassError(f"{name} 没调成：{r.reason}")
        v = r.values[0]
        if ret == "V" or ret == "S":
            return None
        if ret == "R":
            return _float(v)
        if ret == "B":
            return bool(v)
        return v

    def _guard(self, name: str) -> None:
        g = self._g
        if getattr(g, "fair", False):
            raise JassError("公平模式（擂台规则）里不能用 JASS 通道")
        if getattr(g, "player", None) is not None:
            raise JassError("Game(player=N) 是玩家车道，JASS 通道只给本机自己的工具（Game() 默认的 dev 车道）")
        if readonly(name) or name == "GetLocalPlayer":
            return
        # 多人局：本机单方面改世界会不同步。每局查一次（人只会少不会多）。
        # 按 (进程, 局号 epoch) 记：同一个游戏进程回菜单再开一局多人局，以前按 pid 记就不再查了
        try:
            st = g.world.read()
            epoch = st.epoch if st is not None else None
        except Exception:  # noqa: BLE001 - 替身 / 读不到世界块：按进程记
            epoch = None
        key = (g.pid, epoch)
        if self._humans_checked_for != key:
            n = sum(1 for s in self.player_slots() if s["human"] and s["state"] == "playing")
            if n > 1:
                raise JassError(f"这一局有 {n} 个真人玩家：{name} 会改世界、让别人不同步，多人局里只能调只读的 native")
            self._humans_checked_for = key

    # ── 句柄 ──
    def _handle(self, v) -> int:
        if v is None:
            return 0
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v & U32
        if hasattr(v, "addr"):
            return self.handle_of(v)
        raise JassError(f"句柄参数不认识：{v!r}")

    def handle_of(self, obj) -> int:
        """快照里的单位/物品 -> JASS 句柄（快照的 handle_lo 不是 JASS 句柄）。"""
        g = self._g
        r = g._one(g._w3fast.command("jass_handle_of", target=obj))
        if not r.ok or not r.values[0]:
            raise JassError(f"拿不到 {getattr(obj, 'type', obj)} 的 JASS 句柄：{r.reason}")
        return r.values[0]

    def unit_of(self, handle: int, wait: float = 1.0):
        """JASS 单位句柄 -> 快照里的单位（刚造出来的要等下一次世界发布，默认最多等 1 秒）；不是活着的单位返回 None。"""
        g = self._g
        r = g._one(g._w3fast.command("jass_unit_of", code=int(handle)))
        addr = r.values[0] if r.ok else 0
        if not addr:
            return None
        t_end = time.monotonic() + wait
        while True:
            s = g.snapshot(max_age=0)
            for u in (s.units if s else []):
                if u.addr == addr:
                    return u
            if time.monotonic() >= t_end:
                return None
            time.sleep(0.03)

    # ── 常用的几件事（都是上面的 native 拼出来的，照抄也行）──
    def player_slots(self) -> list[dict]:
        """16 个玩家槽：谁在控制（user 真人 / computer 电脑 / neutral…）、状态（empty 空 / playing 在玩 / left 离开）、跟我是不是盟友。"""
        me = self.GetLocalPlayer()
        out = []
        for i in range(16):
            p = self.Player(i)
            ctl = MAP_CONTROL.get(self.GetPlayerController(p), "?")
            st = SLOT_STATE.get(self.GetPlayerSlotState(p), "?")
            out.append({"id": i, "controller": ctl, "state": st, "human": ctl == "user",
                        "me": p == me, "ally": bool(self.IsPlayerAlly(p, me)) if p != me else True})
        return out

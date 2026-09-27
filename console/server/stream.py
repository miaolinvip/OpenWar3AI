# -*- coding: utf-8 -*-
"""直播页（/log）的数据：把新大脑的对局记录 + 毫秒层日志翻成观众看得懂的话。

  GET /api/i/{n}/stream[?match=名字&tick=拍]   滚动条目 + 这页在看谁 + 此刻局势（brain_live.json）
  GET /api/i/{n}/stream/meta[?match=名字]       顶栏和简报：战绩、内核版本、倍速、后台调参、上一局、这一局的仗

09-18 把原来 8777 端口上的网页日志（/log）按当前架构重做，并进 8866 这套系统，供直播加载显示。
             界面和原来基本一致，重点改进信息呈现。
旧页（war3-analytics/streamlog.html + livesrc.py）读的是老 AI 的 ai_run.log；新大脑的决策在对局记录的
action 里，毫秒层（保命/施法/整队撤）在 inst<N>/mod_combat.log。这里只负责「读哪、怎么翻」，不碰 HTTP。

翻译放服务端（旧页在浏览器里翻）：规则跟着大脑的句式改，tests/test_stream.py 拿真实句子守着，
而且每条带稳定 id，页面按 id 对齐增量追加 —— 旧页按「文本相同」对齐，同一句话隔几条又出现就对错位置、整屏重建。

规则只做替换不做解析：翻不出来的行一律不给观众看（宁可少显示，也别在直播里露出 `train hfoo（unit，优先级 65）`）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime

from . import common as C

RACE_CN = {"human": "人族", "orc": "兽族", "undead": "亡灵", "nightelf": "暗夜"}
RACE_KEY = {1: "human", 2: "orc", 3: "undead", 4: "nightelf"}
DIFF_CN = {2: "简单", 3: "普通", 4: "疯狂"}
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}
ATTACK_CN = {"home": "在家发育", "creep": "打野", "gather": "集结", "attack": "出击", "retreat": "撤退", "loot": "捡装备"}
DEFENSE_CN = {"minor": "小股骚扰", "defend": "回防", "toostrong": "保存实力", "last_stand": "死守"}
# 进胜率的结局（手动结束 / 中断 / 卡住结束不算，和对局记录页一致：web/src/lib/game.ts 的 OUTCOME）
COUNTED = {"victory", "defeated", "defeated-or-ended", "conceded-weak", "conceded", "timeout"}
OUTCOME_CN = {"victory": "拿下了", "defeated": "被推平", "defeated-or-ended": "没守住", "conceded-weak": "打不动认输",
              "conceded": "认输", "timeout": "时间到", "ended-by-operator": "手动结束", "crash": "中断", "ended-stuck": "卡住结束"}
DEDUP_S = 30.0          # 同一件事（同一个 key）这么多游戏秒内只播一次：大脑每拍都重下同一个意图
# 按 key 前缀放宽/收紧：施法每一发都值得播（水元素冷却 20 游戏秒，30 秒窗口会吞掉一半），毫秒层算账一场仗里连着好几次
# 09-22 起放宽的那几类是大脑每拍重下的「意图」，一次进攻/养伤能连着几分钟：30 秒一条等于每半分钟复读一遍
# （30 局实测：打分矿/打主基地每局 10 条、养伤 32 行原句）；残血拉出是毫秒层逐个兵报的，一场仗一条就够
# 窗口是拿 inst1 09-22 30 局调出来的（一次性分析脚本 log_coverage.py）：加上 09-21 新句式后每游戏分钟 12 条，5 行的滚动条跟不上，
# 压到 8 条左右 —— 水元素是例行召唤（每局 22 发），暴风雪/烈焰风暴这类才是看点，所以只给它单独拉长
WINDOW_S = (("castHamgwaterelemental", 40.0), ("cast", 8.0), ("ms_calc", 20.0), ("pull", 40.0),
            ("atk", 60.0), ("gather", 60.0), ("reinforce", 60.0), ("waithero", 60.0), ("herobehind", 120.0),
            ("heal", 150.0), ("summontank", 120.0), ("focusfire", 90.0), ("unitsfirst", 90.0), ("repair", 180.0),
            # 例行经济：人口建筑（四族）和补农民一整局都在发生，是上屏最多的两条
            ("buildhhou", 90.0), ("buildotrb", 90.0), ("builduzig", 90.0), ("buildemow", 90.0), ("worker", 60.0))


def window_of(key: str) -> float:
    return next((w for p, w in WINDOW_S if key.startswith(p)), DEDUP_S)
LIVE_IDLE_S = 90.0       # 对局记录这么久没动 = 不在直播
KEEP_ITEMS = 400         # 每局内存里留多少条（页面只要最后几十条）
MOD_TAIL = 6_000_000     # 毫秒层日志从尾巴往前最多扫这么多字节找这局的开头


def _name(code: str) -> str | None:
    return C.names().get(code)


_SPELL: dict | None = None
# mod_combat 心跳里的技能叫法和引擎订单名不全一样（它自己起的名字）：stormbolt = 风暴之锤的订单 thunderbolt，
# holylight = 圣光术的订单 holybolt。09-18 实测 5~11 号 mod_combat.log 里出现过的 9 种都能翻出来（test_stream 守着）。
SPELL_ALIAS = {"stormbolt": "thunderbolt", "holylight": "holybolt"}
# 中文技能名表（w3data/*AbilityStrings.txt）目前只抽了人族，别的种族退回 skills.json 的英文名（「Blade Master - Wind Walk」）——
# 直播里不能露英文，这几个按冰封王座简中的叫法补上；游戏自己的中文名优先
SPELL_CN = {
    "windwalk": "疾风步", "mirrorimage": "镜像", "chainlightning": "闪电链", "farsight": "透视", "spiritwolf": "野性之魂",
    "earthquake": "地震", "healingwave": "治疗波", "hex": "妖术", "serpentward": "毒蛇守卫", "voodoo": "巫毒",
    "stomp": "战争践踏", "shockwave": "震荡波", "carrionswarm": "腐臭蜂群", "sleep": "睡眠", "frostnova": "霜冻新星",
    "deathcoil": "死亡缠绕", "deathpact": "死亡契约", "darkritual": "黑暗仪式", "deathanddecay": "死亡凋零",
    "animatedead": "操纵死尸", "entanglingroots": "纠缠根须", "forceofnature": "自然之力", "tranquility": "宁静",
    "starfall": "群星坠落", "manaburn": "法力燃烧", "metamorphosis": "恶魔变身", "immolation": "献祭", "blink": "闪烁",
    "fanofknives": "刀阵旋风", "shadowstrike": "暗影突袭", "vengeance": "复仇天神",
    # 血魔法师：这两个订单名不在 common.CASTS 里（09-22 实测 mod_combat.log 出现过），名字照游戏自己的 AHdr / AHpx
    "siphonmana": "魔法吸吮", "phoenix": "火凤凰",
}
_CJK = re.compile(r"[一-鿿]")


PASSIVE_CN = {"AOcr": "致命一击", "AOae": "耐久光环", "AOre": "重生", "AUau": "邪恶光环", "AUav": "吸血光环", "AUts": "尖刺外壳",
              "AUim": "穿刺", "AEev": "闪避", "AEah": "荆棘光环", "AEar": "强击光环", "AEsf": "灼热之箭"}


def ability_name(code: str) -> str | None:
    """技能码 -> 中文名：游戏自带的中文名 > 订单名补充表（SPELL_CN）> 常见被动；都没有就 None（那一行不播）。"""
    nm = C.ability_names().get(code)
    if nm and _CJK.search(nm):
        return nm
    order = (C.CASTS.get(code) or (None,))[0]
    return SPELL_CN.get(order) if order else PASSIVE_CN.get(code)


def spell_name(order: str) -> str | None:
    """订单名（blizzard）-> 中文技能名（暴风雪）：common.CASTS 反查技能码，再查 *AbilityStrings.txt；没有中文的查 SPELL_CN。"""
    global _SPELL
    if _SPELL is None:
        names = C.ability_names()
        _SPELL = {o: names.get(code) for code, (o, _t) in C.CASTS.items() if names.get(code)}
    order = SPELL_ALIAS.get(order, order)
    nm = _SPELL.get(order)
    return nm if nm and _CJK.search(nm) else SPELL_CN.get(order)


# ── 翻译 ─────────────────────────────────────────────────────────────
# 每条规则返回 [(文本, 分类, key, 可计数)]；key 是去重和相邻合并的依据（文本里的数字每拍都变，key 不变）。
# 分类和页面的勾选一一对应：ai 智能判断 / fight 战斗 / army 兵力 / build 建造 / tech 科技 / econ 经济 / big 里程碑 / sep 场记
# 「可计数」= 一行对应一个实物（清掉一个营地、折了一个兵），相邻合并时显示 ×N；意图类（同一个令每拍重下）不计数 ——
# 旧页 09-15 的教训：「建造 兵营 ×2」数的是日志行不是建筑，**一个不准的数字比没有数字更糟**。

def _t(text, cat, key, count=False):
    return [(text, cat, key, count)]


TARGET_CN = {"分矿": "对面分矿", "主基地": "对面主基地", "建筑": "对面建筑"}


def _train(m):
    code, kind = m.group(1), m.group(2)
    nm = _name(code)
    if not nm:
        return None
    if kind == "hero":
        return _t(f"🎖 招募英雄：{nm}", "army", "hero" + code)
    if kind == "upgrade_hall":
        return _t(f"⬆ 升本 → {nm}", "tech", "tier" + code)
    if kind == "upgrade":
        return _t(f"🔬 研究 {nm}", "tech", "res" + code)
    if code in WORKERS:
        return _t("补农民", "econ", "worker")
    return _t(f"训练 {nm}", "army", "train" + code)


def _learn(m):
    hero, abil = _name(m.group(1)), ability_name(m.group(2))
    return _t(f"📘 {hero} 学会 {abil}", "army", f"learn{m.group(1)}{m.group(2)}") if hero and abil else None


def _code_line(fmt, cat, key, count=False):
    def f(m):
        nm = _name(m.group(1))
        return _t(fmt.format(nm), cat, key + m.group(1), count) if nm else None
    return f


MS_WHY = {"胜率": "世界模型算胜率", "血量输出": "血量×输出", "英雄预警": "英雄被围预警"}
# 09-21 22:18 起大脑和毫秒层给单位编了号，句子里是「#7 hfoo@1458C554」—— 旧规则只认「hfoo@」开头，
# 施法 / 被集火 / 打野阵亡从那以后整段没上过屏（09-22 30 局：施法原句每局 25 条，上屏 0）
U = r"(?:#\d+ )?"
SELF_SAVE_CN = {"大群贴脸提前脱离": "被一大群贴脸", "孤军且敌方占优": "孤军深入、对面人多",
                "低血量脱离接触": "血少了", "实际护卫不足": "身边护卫不够"}


def _n(code: str, dflt: str = "兵") -> str:
    return _name(code) or dflt


def _skirmish(m):
    """局部交战状态机（idle/fight/withdraw/disengaged）：只播进入「打」和进入「撤」两个转折。"""
    to, own, foe, rein = m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(6))
    if to == "fight":
        return _t(f"⚔ 局部交战：我方战力 {own:.0f} vs 对面 {foe:.0f}（算上援军 {rein:.1f} 倍）— 打！", "ai", "skirmishfight")
    if to == "withdraw":
        return _t(f"⚡ 局部交战：我方战力 {own:.0f} vs 对面 {foe:.0f} — 撤", "ai", "skirmishwithdraw")
    return None


def _creep_engage(m):
    strength, rest = m.group(1), m.group(2) or ""
    rows = _t(f"⚔ 开打野怪（营地强度 {strength}）" + ("— 援兵在路上，先打起来" if "增援在路上" in rest else ""),
              "fight", "engage" + strength)
    tactic = m.group(3) or ""
    if "召唤物" in tactic:
        rows += _t("💧 召唤物先顶住仇恨，本体兵不挨揍", "ai", "summontank")
    elif any(w in tactic for w in ("肉盾", "坦克")):
        rows += _t("🛡 肉盾先顶上去，远程等接火再打", "ai", "tankfirst")
    elif "英雄先手" in tactic:
        rows += _t("🎖 英雄先手把野怪拉出营地", "ai", "herolead")
    elif "民兵" in tactic:
        rows += _t("🔔 民兵单独编一组扛伤害", "ai", "militiatank")
    return rows


ACTION_RULES = [
    (r"^种族：", None),
    (r"^英雄看戏", None),                              # 诊断行
    (r"^回家路上", None),
    (r"^让开分矿落点", None),
    (r"^打野·后面的跟上", None),
    (r"^骚扰：走向农民", None),
    (r"^续建 ", None),
    (r"^复活 \w{4}：被退", None),
    (r"^配置更新（第 (\d+) 版）", lambda m: _t(f"🔧 调参器换了一组参数（第 {m.group(1)} 版）", "sep", "cfg")),
    # ── 开局谱（09-18，war3-brain/brain/opening.py：照高手录像的开局顺序）──
    (r"^开局谱：(.+?)（(\d+) 步", lambda m: _t(f"📜 开局照 {m.group(1)} 的顺序走（{m.group(2)} 步）", "sep", "opstart")),
    (r"^开局谱第 (\d+)/(\d+) 步：(.+)$", lambda m: _t(f"📜 开局谱第 {m.group(1)}/{m.group(2)} 步：{m.group(3)}", "ai", "opstep" + m.group(1))),
    (r"^开局谱跳过第 (\d+) 步 (.+?)：等了 \d+ 秒（(.+)）$",
     lambda m: _t(f"⏭ 开局谱跳过第 {m.group(1)} 步 {m.group(2)}（{m.group(3)}）", "ai", "opskip" + m.group(1))),
    (r"^开局谱 (.+?) (走完|到 \d+ 秒还没走完)", lambda m: _t(f"📜 开局谱 {m.group(1)} {m.group(2)} — 交回建造表", "sep", "opdone")),
    (r"^开局谱 (.+?) 中途切走", lambda m: _t(f"📜 开局谱 {m.group(1)} 中途关掉 — 交回建造表", "sep", "opdone")),
    # ── 打野 ──
    (r"^打野选营地：强度 (\d+)，战力比 ([\d.]+)，走 (\d+) \+ 等兵 (\d+) \+ 打 (\d+) 秒",
     lambda m: _t(f"🧭 挑野点：强度 {m.group(1)} 的营地 — 走 {m.group(3)} 秒、打 {m.group(5)} 秒"
                  + (f"，先等援兵 {m.group(4)} 秒" if int(m.group(4)) else "") + f"（战力比 {m.group(2)}）",
                  "ai", "pick" + m.group(1))),
    (r"^清分矿野怪：强度 (\d+)，战力比 ([\d.]+)",
     lambda m: _t(f"🧭 分矿口有野怪（强度 {m.group(1)}）— 推演打得过（战力比 {m.group(2)}），先清掉", "ai", "expocreep")),
    (r"^清分矿野怪（强度 (\d+)）·(?:去集合点|全军很强但尚未到场)", lambda m: _t("🚶 去分矿口清野", "fight", "expocreepgo")),
    (r"^清分矿路线先清挡路野怪：强度 (\d+)",
     lambda m: _t(f"🧭 去分矿的路上有野怪挡着（强度 {m.group(1)}）— 先清掉", "ai", "expopath")),
    (r"^清分矿路线挡路野怪（强度 (\d+)）·去集合点", lambda m: _t("🚶 去清分矿路上的野怪", "fight", "expopathgo")),
    (r"^清分矿野怪（强度 (\d+)）·直接打（全军战力比 ([\d.]+)）",
     lambda m: _t(f"⚡ 分矿口的野太弱（全军战力比 {m.group(2)}）— 直接打", "ai", "expocreepdirect")),
    (r"^打野（营地强度 (\d+)）·直接打（全军战力比 ([\d.]+)）",
     lambda m: _t(f"⚡ 营地太弱（全军战力比 {m.group(2)}）— 不等集合，直接打", "ai", "direct" + m.group(1))),
    (r"^打野（营地强度 (\d+)）·(?:去集合点|全军很强但尚未到场)",
     lambda m: _t(f"🚶 去野点集合（营地强度 {m.group(1)}）", "fight", "stage" + m.group(1))),
    # 09-21 起括号里多了「；核心伤害 10.7/秒」「、增援在路上」，句尾多了分组战术「·召唤物先接仇恨」—— 旧写法要求比值后面紧跟「）」，整句没上过屏
    (r"^打野开打（营地强度 (\d+)，到场战力比 [\d.]+([^）]*)）(·[^：]+)?", _creep_engage),
    (r"^打野·(?:远程|后排)(?:展开)?等肉盾接火", lambda m: _t("🛡 肉盾先顶上去，远程等接火再打", "ai", "tankfirst")),
    (r"^打野·(?:有召唤物时永久兵不抢承伤|召唤物)", lambda m: _t("💧 召唤物先顶住仇恨，本体兵不挨揍", "ai", "summontank")),
    (r"^打野·英雄先手", lambda m: _t("🎖 英雄先手把野怪拉出营地", "ai", "herolead")),
    (r"^打野·前排两翼包围", lambda m: _t("🪖 前排从两边包住野怪的核心", "ai", "flank")),
    (r"^打野·残血兵退到集合点", lambda m: _t("🩸 残血小兵先退下来，不白送", "fight", "lowhpback")),
    (r"^打野·先到的退到集合点等人", lambda m: _t("⏳ 先到的不单挑 — 退回来等人齐", "ai", "waitfirst")),
    (r"^打野·伤员过半", lambda m: _t("🩸 伤员过半 — 整队退回集合点", "fight", "creephalf")),
    (r"^打野整队退：\d+/\d+ 人撑不住（我们打光营地要 (\d+) 秒，营地打光我们要 (\d+) 秒）",
     lambda m: _t(f"⚡ 算账：打光野怪要 {m.group(1)} 秒，野怪打光我们只要 {m.group(2)} 秒 — 撤", "ai", "creepcalc")),
    (r"^打野撤离：目标营地之外又接触 (\d+) 只野怪",
     lambda m: _t(f"⚡ 又惊动了旁边营地 {m.group(1)} 只野怪 — 撤，免得两边夹击", "ai", "twocamp")),
    (r"^误入第二营地", lambda m: _t("⚡ 又惊动了旁边的营地 — 撤，免得两边夹击", "ai", "twocamp")),
    (r"^打野放弃：选定 (\d+) 游戏秒还凑不齐", lambda m: _t(f"⚡ 等了 {m.group(1)} 秒人凑不齐 — 换个营地", "ai", "giveup")),
    (r"^打野放弃：(\d+) 游戏秒里营地总血一点没掉",
     lambda m: _t(f"⚡ 打了 {m.group(1)} 秒野怪一滴血没掉 — 多半够不着，换一个", "ai", "giveupnohit")),
    (r"^打野打不到（血一直没掉）", lambda m: _t("⚡ 野怪一滴血没掉 — 多半够不着，换一个", "ai", "giveupnohit")),
    (r"^打野后(?:伤兵多|转场补给)|^带伤不前移", lambda m: _t("🩹 打完野伤兵多 — 找个安全的地方整队回血", "ai", "creeprest")),
    (r"^清营后直接前移：下一营地强度 (\d+)", lambda m: _t(f"🧭 清完直接去下一个野点（强度 {m.group(1)}）", "ai", "nextcamp")),
    (r"^打野回家：主力对营地剩下的战力比 ([\d.]+)",
     lambda m: _t(f"⚡ 剩下的野打不动了（战力比 {m.group(1)}）— 回家", "ai", "creephome")),
    (r"^打野打不动，回家", lambda m: _t("⚡ 这波野打不动 — 回家", "ai", "creephome")),
    (r"^营地清空（强度 (\d+)）", lambda m: _t(f"✔ 清掉一个野点（强度 {m.group(1)}）", "fight", "clear" + m.group(1), True)),
    (r"^打野阵亡：" + U + r"(\w{4})@", _code_line("☠ 打野折了一个{}", "fight", "creepdeath", True)),
    # ── 出击 ──
    (r"^出击打(分矿|主基地|建筑)：([\d.]+) <= ([\d.]+)",
     lambda m: _t(f"⚡ 推演：{TARGET_CN.get(m.group(1), m.group(1))}那边 兵+塔 {m.group(2)} ≤ 我方 {m.group(3)} — 出击！",
                  "ai", "attackgo")),
    (r"^拆家门口的分矿：\w{4} 离家 \d+，那边守军 ([\d.]+)",
     lambda m: _t(f"🎯 对面在我家门口开分矿（守军 {m.group(1)}）— 去拆", "ai", "raid")),
    (r"^(?:出击|接着)?(?:打分矿|打主基地|打建筑)·英雄(?:和施法支援)?跟在主力后面|^目标没了，附近接着打·英雄跟在主力后面",
     lambda m: _t("🎖 英雄跟在主力后面，不打头阵", "ai", "herobehind")),
    (r"^(?:出击)?(?:打分矿|打主基地)·等英雄跟上", lambda m: _t("⏳ 等英雄跟上再走", "ai", "waithero")),
    (r"^出击·前面人太少", lambda m: _t("⏳ 冲在前面的人太少 — 停下等后面的人，到齐了一起打", "ai", "regroup")),
    (r"^先打兵：附近还有敌兵", lambda m: _t("🎯 先打兵 — 不拆房子、不顶着塔打", "ai", "unitsfirst")),
    (r"^敌兵已清空，全体集火 (\w{4})", lambda m: _t(f"🎯 敌兵清光了 — 全体集火{_n(m.group(1), '防御塔')}", "fight", "towerfocus")),
    (r"^敌兵清空，集火攻击型建筑", lambda m: _t("🎯 敌兵清光了 — 全体集火防御塔", "fight", "towerfocus")),
    (r"^敌兵都在塔下", lambda m: _t("✋ 敌兵缩在塔下 — 不进塔的射程，外面等", "ai", "towerwait")),
    (r"^(?:进攻任务无进展|集结超时且到场兵力不足)", lambda m: _t("⚡ 这波推不动 — 回家重整", "ai", "atkstall")),
    (r"^追到对方地盘了，不打", lambda m: _t("✋ 追到对方地盘了 — 不追，收回来", "ai", "nochase")),
    (r"^目标援军已到，回家重整", lambda m: _t("⚡ 对面援军到了 — 回家重整", "ai", "foereinf")),
    (r"^向下一目标前沿集结", lambda m: _t("在前沿集结，准备打下一个目标", "fight", "frontgather")),
    (r"^接着打(?:主基地|建筑)：兵力还剩出发时 (\d+)%", lambda m: _t(f"⚡ 兵力还剩出发时 {m.group(1)}% — 接着推", "ai", "pushon")),
    (r"^(?:出击|接着)?打分矿：", lambda m: _t("⚔ 出击：打对面分矿", "fight", "atkexpo")),
    (r"^(?:出击|接着)?打主基地：", lambda m: _t("⚔ 出击：直捣对面主基地", "fight", "atkmain")),
    (r"^(?:接着)?打建筑：", lambda m: _t("⚔ 拆对面建筑", "fight", "atkbld")),
    (r"^目标没了，附近接着打", lambda m: _t("目标拆掉了 — 附近接着打", "fight", "nexttarget")),
    (r"^打完了，回家", lambda m: _t("✔ 打完了，回家", "fight", "donehome")),
    (r"^出击前集结", lambda m: _t("出门前先集合", "fight", "pregather")),
    (r"^集结：", lambda m: _t("在家门口集结部队", "fight", "gather")),
    (r"^支援交战（敌 (\d+) 个", lambda m: _t(f"⚔ 主力支援交战（对面 {m.group(1)} 个）", "fight", "support")),
    # 09-21 起的局部交战层（brain 的 skirmish：idle/fight/withdraw）：占优时的分组令是「支援交战·局部战力允许持续输出·<分组>」
    # 其余分组（增援接战/就近接战）不播：和紧挨着的「局部交战：……打！」是同一件事
    (r"^支援交战·局部战力允许持续输出·集火", lambda m: _t("🎯 集火对面的核心输出", "fight", "focusfire")),
    (r"^局部交战 (\w+)->(\w+)：我方战力 ([\d.]+)、敌方 ([\d.]+)，比值 ([\d.]+)，含后续援军比 ([\d.]+)", _skirmish),
    (r"^敌强我弱，分段后撤", lambda m: _t("⚡ 敌强我弱 — 边退边看追兵", "ai", "retreatstep")),
    (r"^交战：敌 (\d+) 个（强度 \d+），前面的人撑不到援兵",
     lambda m: _t(f"⏳ 对面 {m.group(1)} 个，前面的人撑不到援兵 — 先退回去汇合，到齐了一起打", "ai", "regroup")),
    (r"^交战：敌 (\d+) 个（强度 \d+），合起来也不够压上（胜率 (\d+)%",
     lambda m: _t(f"⚡ 对面 {m.group(1)} 个，合起来也打不过（胜率 {m.group(2)}%）— 先退回去汇合", "ai", "regroup")),
    (r"^援兵先去跟主力汇合", lambda m: _t("🧩 新兵先和主力汇合，不单独上", "ai", "reinforce")),
    (r"^援兵太少太远，(先回家|去前沿集结点)攒人",
     lambda m: _t(f"🧩 援兵太少太远 — {'先回家' if m.group(1) == '先回家' else '先去前沿集结点'}攒人", "ai", "reinforcehome")),
    (r"^英雄赶去跟主力汇合", lambda m: _t("英雄赶去和主力汇合", "ai", "herojoin")),
    (r"^护送开矿", lambda m: _t("🛡 大部队护送农民去开分矿", "ai", "escort")),
    # ── 守家 / 撤退 ──
    (r"^回防（威胁 (\d+)）", lambda m: _t(f"🛡 家里来人了（威胁 {m.group(1)}）— 全军回防", "fight", "defend")),
    (r"^回防·敌军正在拆建筑反打", lambda m: _t("🛡 对面在拆家 — 全军反打", "fight", "defendhit")),
    (r"^回防·主力到齐反打", lambda m: _t("🛡 主力到齐 — 反打", "fight", "defendhit")),
    (r"^回防·压倒劣势先到建筑后集结", lambda m: _t("🛡 家门口打不过 — 先退到建筑后面集结", "ai", "defendback")),
    (r"^回防：敌人不在家门口", lambda m: _t("🛡 敌人没到家门口 — 不出去迎、不追，回来等", "ai", "defendwait")),
    (r"^电脑队长的目标点是这座城", lambda m: _t("👁 看穿了：电脑这波冲着这座城来 — 提前回家守", "ai", "captainhome")),
    (r"^分矿守不住（胜率 (\d+)%", lambda m: _t(f"⚡ 分矿守不住（胜率 {m.group(1)}%）— 保住兵，退回主基地", "ai", "expolost")),
    (r"^撤：主力 \d+ 内我方 ([\d.]+) < 附近敌方 ([\d.]+)",
     lambda m: _t(f"⚡ 算账：主力附近 我方 {m.group(1)} < 对面 {m.group(2)} — 撤", "ai", "retreatcalc")),
    (r"^撤：主力附近敌方强度 ([\d.]+) > 我方 ([\d.]+)",
     lambda m: _t(f"⚡ 算账：主力附近 我方 {m.group(2)} < 对面 {m.group(1)} — 撤", "ai", "retreatcalc")),
    (r"^毫秒层叫撤（([^：]+)：在场我方 (\d+) vs 敌 (\d+)）",
     lambda m: _t(f"⚡ 毫秒层判这仗要输（{MS_WHY.get(m.group(1), m.group(1))}，在场 我 {m.group(2)} : 敌 {m.group(3)}）— 整队撤",
                  "ai", "msretreat")),
    (r"^毫秒层判这一仗输了，整队回家", lambda m: _t("⚡ 毫秒层判这仗要输 — 整队撤", "ai", "msretreat")),
    (r"^(?:交战|当地)?打不过，整队回家", lambda m: _t("⚡ 打不过 — 整队回家", "ai", "retreat")),
    (r"^英雄残血先回家", lambda m: _t("🩸 英雄残血 — 先回家", "fight", "herolow")),
    (r"^英雄没回血，(?:单独)?回家", lambda m: _t("英雄没回满血 — 先回家不出门", "ai", "heroheal")),
    (r"^养伤：", lambda m: _t("🩹 伤兵先回家附近空地养伤", "army", "heal")),
    (r"^(?:出击伤员单独回家|伤员单独恢复，健康主力出击)", lambda m: _t("🩹 伤员回家养伤，健康的接着打", "army", "healsplit")),
    (r"^整队恢复：(\d+) 名伤兵，缺血 (\d+)，由 (\w{4}) 去藏宝室补恢复卷轴",
     lambda m: _t(f"🧪 {m.group(1)} 个伤兵缺血 {m.group(2)} — {_n(m.group(3), '英雄')}去商店买恢复卷轴", "army", "scrollbuy")),
    (r"^整队恢复：(\d+) 名伤兵，缺血 (\d+)，(\w{4}) 手上已经有恢复卷轴",
     lambda m: _t(f"🧪 {m.group(1)} 个伤兵缺血 {m.group(2)} — {_n(m.group(3), '英雄')}手上有恢复卷轴，就地集合", "army", "scrollgather")),
    (r"^整队恢复(?:结束)?：(?:使用恢复卷轴|恢复卷轴已放出)", lambda m: _t("🧪 放恢复卷轴 — 大家边走边回血", "army", "scrolluse")),
    (r"^建筑没了：(\w{4})", _code_line("🔥 {}被拆了", "fight", "lost", True)),
    # ── 骚扰 ──
    (r"^(?:骚扰|双线)小队 \d+ 出发：(\d+) 个 (\w{4})",
     lambda m: _t(f"🐎 派 {m.group(1)} 个{_name(m.group(2)) or '兵'}去骚扰对面农民", "ai", "harassgo")),
    (r"^骚扰小队 \d+ 结束：(.+)$", lambda m: _t(f"骚扰结束：{m.group(1)}", "fight", "harassend")),
    (r"^骚扰：到矿区塔外等农民出来", lambda m: _t("🐎 骚扰小队在矿区塔外蹲农民", "ai", "harasswait")),
    (r"^骚扰：打农民", lambda m: _t("🐎 骚扰小队冲进去打农民", "fight", "harasshit")),
    (r"^骚扰小队 \d+：敌兵压过来|^骚扰拉扯：敌兵压过来", lambda m: _t("🐎 骚扰的被追 — 往家的方向拉开，不硬打", "ai", "harasskite")),
    (r"^骚扰撤|^骚扰：(?:那片有敌方主力|塔外一直没有农民)|^双线收队|^骚扰小队 \d+ 收队|^骚扰(?:掉了人|出现伤员|有人被拉去保命)",
     lambda m: _t("骚扰的撤回来", "fight", "harassback")),
    # ── 英雄 / 兵力 / 建造 ──
    (r"^英雄过去捡装备", lambda m: _t("🎁 英雄去捡野怪掉的装备", "army", "loot")),
    (r"^复活 (\w{4})（(\d+) 级.*：接下", lambda m: (_t(f"⛑ 复活 {_name(m.group(1))}（{m.group(2)} 级）", "army", "revive" + m.group(1))
                                                 if _name(m.group(1)) else None)),
    (r"^(\w{4}) 学 (\w{4})：学会", _learn),
    (r"^train (\w{4})（(\w+)，", _train),
    (r"^开分矿 ", lambda m: _t("⛏ 派农民去开分矿", "build", "expand")),
    (r"^人口 (\d+)/\d+（上限 \d+），(\w{4}) 被人口挡着、钱够：让 \d+ 个自己人强制攻击一个 (\w{4})",
     lambda m: _t(f"🔪 人口卡在 {m.group(1)} — 杀掉一个自家{_n(m.group(3))}，给{_n(m.group(2))}腾位置", "army", "foodkill")),
    (r"^民兵清分矿：营地强度 (\d+)，号召 (\d+)/\d+ 名",
     lambda m: _t(f"🔔 敲钟：{m.group(2)} 个农民变民兵去清分矿的野（强度 {m.group(1)}）", "fight", "militia")),
    (r"^民兵清分矿：守军已清，预留 (\d+) 名", lambda m: _t(f"🔨 分矿口清干净了 — 留 {m.group(1)} 个民兵一起盖分矿", "build", "militiabuild")),
    (r"^民兵清分矿：分矿大厅完工", lambda m: _t("🔔 分矿盖好了 — 民兵变回农民去采矿", "econ", "militiaback")),
    (r"^分矿落点附近有小动物 (\w{4})", lambda m: _t(f"🐾 分矿地基上有只{_n(m.group(1), '小动物')}挡路 — 英雄去打掉", "ai", "critter")),
    (r"^维修派出：(\w{4}) ", lambda m: _t(f"🔧 派农民修{_n(m.group(1), '建筑')}", "econ", "repair")),
    (r"^协建派出：(\w{4})", lambda m: _t(f"👷 加派一个农民一起盖{_n(m.group(1), '建筑')}", "build", "cobuild" + m.group(1))),
    # 建造行末尾的「高手落点」（brain/data/pro_spots.json）不播：09-22 起「照高手录像位置」这类字样不再上屏
    (r"^build (\w{4}) @", _code_line("🔨 开建 {}", "build", "build")),
]

MOD_RULES = [
    (r"^胜率叫撤\(世界模型\)：接触后 \d+ 游戏秒 胜率 (\d+)%",
     lambda m: _t(f"🧠 世界模型：这仗胜率只剩 {m.group(1)}% — 全体撤", "ai", "wmretreat")),
    (r"^(?:整队|遭遇战)撤退\((\d+)ms\)：打光他们要 (\d+)s、他们打光我们要 (\d+)s",
     lambda m: _t(f"⚡ {int(m.group(1)) / 1000:.1f} 秒算完这波账：磨死对面要 {m.group(2)} 秒，我方只撑 {m.group(3)} 秒 — 撤！",
                  "ai", "ms_calc")),
    (r"^基地压倒性劣势退到建筑后\((\d+)ms\)：打光他们要 (\d+)s、他们打光我们要 (\d+)s",
     lambda m: _t(f"⚡ 家门口打不过：磨死对面要 {m.group(2)} 秒，我方只撑 {m.group(3)} 秒 — 退到建筑后面", "ai", "ms_calchome")),
    (r"^英雄被集火：" + U + r"(\w{4})@\w+ \d+/\d+，身边 \d+ 个敌人里 (\d+) 个在打它",
     lambda m: _t(f"🎯 {_name(m.group(1))}被集火（{m.group(2)} 个在打它）— 马上拉出来", "fight", "focus" + m.group(1))
     if _name(m.group(1)) else None),
    (r"^英雄预警撤退：" + U + r"(\w{4})@\w+ .*?身边 \d+ 内敌 (\d+) 个、我方 (\d+) 个",
     lambda m: _t(f"👁 {_name(m.group(1))}身边 敌 {m.group(2)} : 我 {m.group(3)} — 没接触就先撤", "ai", "warn" + m.group(1))
     if _name(m.group(1)) else None),
    (r"^英雄局部自保：" + U + r"(\w{4})@\w+ \d+/\d+ (\S+?)，敌 (\d+)、有效护卫 (\d+)",
     lambda m: _t(f"🏃 {_name(m.group(1))}{SELF_SAVE_CN.get(m.group(2), '处境危险')}（敌 {m.group(3)} : 护卫 {m.group(4)}）— 先退一步",
                  "ai", "selfsave" + m.group(1)) if _name(m.group(1)) else None),
    (r"^英雄被控住：" + U + r"(\w{4})@\w+ \d+/\d+ ([^（(]+?)（",
     lambda m: _t(f"😵 {_name(m.group(1))}被{m.group(2)}了", "fight", "stun" + m.group(1)) if _name(m.group(1)) else None),
    (r"^英雄施法：" + U + r"(\w{4})@\w+ (\w+)",
     lambda m: _t(f"✨ {_name(m.group(1))} 放 {spell_name(m.group(2))}", "fight", f"cast{m.group(1)}{m.group(2)}")
     if _name(m.group(1)) and spell_name(m.group(2)) else None),
    # 残血拉出：毫秒层按「谁在打它」算出几秒后会死就拉走（行首就是单位，前面没有句名）；一场仗报几十个，25 秒只播一条
    (r"^" + U + r"(\w{4})@\w+ \d+/\d+ 正在挨 \d+ 血/秒.*?—— 拉出来（[^）]*）（(\d+)ms 内响应）",
     lambda m: _t(f"🩸 {_name(m.group(1))}快被打死了 — {m.group(2)} 毫秒内拉了出来", "fight", "pull") if _name(m.group(1)) else None),
    (r"^用物品：" + U + r"(\w{4})@\w+ .*?第\d+格 (\w{4}).*-> ok",
     lambda m: _t(f"🎒 {_name(m.group(1))} 用了 {_name(m.group(2))}", "army", f"item{m.group(1)}{m.group(2)}")
     if _name(m.group(1)) and _name(m.group(2)) else None),
    (r"^英雄独自扛野怪", lambda m: _t("英雄一个人扛野怪 — 先拉出来等小兵", "ai", "heroalone")),
]

_ACTION = [(re.compile(p), f) for p, f in ACTION_RULES]
_MOD = [(re.compile(p), f) for p, f in MOD_RULES]


def translate(text: str, rules=None) -> list[tuple]:
    """一行 -> [(文本, 分类, key, 可计数)]；不给观众看的返回 []。第一条命中的规则说了算（特例写在通用规则前面）。"""
    for rx, fn in (rules or _ACTION):
        m = rx.search(text)
        if m:
            return (fn(m) or []) if fn else []
    return []


def translate_mod(text: str) -> list[tuple]:
    return translate(text, _MOD)


# ── 一局的增量读取 ───────────────────────────────────────────────────

def _utc_ts(rec: dict) -> float | None:
    try:
        return datetime.fromisoformat(rec["utc"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _race_of(code) -> str | None:
    if isinstance(code, int):
        return RACE_KEY.get(code)
    return code if code in RACE_CN else None


class MatchFeed:
    """一局：对局记录按字节偏移增量读，毫秒层日志按时间窗口增量读；翻好的条目带稳定 id。"""

    def __init__(self, path: str, mod_log: str | None, kernels: list | None = None):
        self.path, self.name = path, os.path.basename(path)
        # 条目 id 的前缀：是哪一局。★ 09-22 滚动字幕反复刷屏 —— id 原来只按行号（b600-0.0），
        #   直播时当前局不够 limit 条会拼上一局的尾巴（stream()），两局同一行号的 id 撞了；页面按 id 找「上次滚到哪」
        #   找到的是上一局那条，把中间九十多条旧内容当新的重滚一遍，下一秒又撞上、再滚一遍。
        m = re.match(r"match-\d{8}-\d{6}-\d+-([0-9a-f]+)", self.name)
        self.tag = m.group(1) if m else re.sub(r"\W", "", self.name.replace(".jsonl", ""))[-16:]
        self.mod_log = mod_log
        self.kernels = kernels or []
        self.off = 0
        self.line = 0
        self.items: list[dict] = []
        self.mod_items: list[dict] = []
        self.seen: dict[str, float] = {}
        self.mod_seen: dict[str, float] = {}
        self.states: list[tuple] = []          # (墙钟, 游戏秒, 拍)
        self.start_wall = None
        self.end_wall = None
        self.exp: dict = {}
        self.result: dict | None = None
        self.lesson: dict | None = None
        self.finalized = False
        self.race = None
        self.foe_votes: dict[str, int] = {}
        self.max_tick = 0
        self.game_no = None
        self.last_g = None
        self.mod_off = None
        self.mod_done = False
        self.learned: dict[str, int] = {}
        self.started = False
        self.lock = threading.Lock()

    # 墙钟 -> (游戏秒, 拍)：取之前最近一份快照，再按倍速外推（最多外推到下一份快照）
    def game_at(self, wall: float) -> tuple:
        if not self.states:
            return None, 0
        lo, hi = 0, len(self.states)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.states[mid][0] <= wall:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return self.states[0][1], self.states[0][2]
        w, g, tk = self.states[lo - 1]
        rate = (self.speed() or 100) / 100.0
        est = g + max(0.0, wall - w) * rate
        if lo < len(self.states):
            est = min(est, self.states[lo][1])
        return est, tk

    def speed(self) -> int | None:
        s = (self.exp.get("setup") or {}).get("speed")
        return int(s) if isinstance(s, (int, float)) else None

    @property
    def foe(self) -> str | None:
        if self.foe_votes:
            return max(self.foe_votes, key=self.foe_votes.get)
        return _race_of((self.exp.get("setup") or {}).get("enemyRace"))

    def _emit(self, out: list, seen: dict, iid: str, wall, g, tick, rows) -> None:
        for k, (text, cat, key, count) in enumerate(rows):
            win = window_of(key)
            if not count and g is not None and key in seen and g - seen[key] < win:
                continue
            if g is not None:
                seen[key] = g
            # src：条目从哪来（b 大脑动作 / m 毫秒层 / s 开局 / k 内核 / r 结局），排序用 —— id 前面带了局的标识，不能再看 id 首字母
            out.append({"id": f"{self.tag}:{iid}.{k}", "src": iid[0], "w": wall, "g": None if g is None else round(g, 1),
                        "tick": tick, "text": text, "cat": cat, "key": key, "count": count})
        if len(out) > KEEP_ITEMS * 2:
            del out[:len(out) - KEEP_ITEMS]

    def _start_item(self, wall) -> None:
        if self.started:
            return
        self.started = True
        setup = self.exp.get("setup") or {}
        me = RACE_CN.get(_race_of(setup.get("race")) or "", "")
        foe = RACE_CN.get(_race_of(setup.get("enemyRace")) or "", "")
        diff = DIFF_CN.get(setup.get("difficulty"))
        mp = map_label(setup.get("map"))
        spd = self.speed()
        bits = [f"{me} vs {foe}（{diff}电脑）" if me and foe and diff else (f"{me} vs {foe}" if me and foe else "")]
        if mp:
            bits.append(mp)
        if spd and spd > 100:
            bits.append(f"{spd / 100:g} 倍速")
        no = f"第 {self.game_no} 局" if self.game_no else "新的一局"
        text = f"━━ {no}开打：{' · '.join(b for b in bits if b)} ━━"
        self._emit(self.items, self.seen, f"s{self.line}", wall, 0.0, 0, _t(text, "sep", "start"))
        kern = self.exp.get("kernel")
        if kern:
            subj = next((k["subject"] for k in self.kernels if k.get("n") == kern), "")
            head = re.split(r"[：:；;。（(]", subj, maxsplit=1)[0][:40] if subj else ""
            combat = self.exp.get("combatVersion")
            combat_rev = self.exp.get("combatRev")
            combat_text = (f" · 战斗 {combat}" + (f"/{combat_rev}" if combat_rev else "")) if combat else ""
            self._emit(self.items, self.seen, f"k{self.line}", wall, 0.0, 0,
                       _t(f"🧠 本局大脑：内核 #{kern}{combat_text}" + (f"（{head}）" if head else ""), "sep", "kernel"))

    def _levels(self, rows: list) -> list:
        """「X 学 Y：学会」不带等级：同一个英雄同一个技能第二次学 = 升到 2 级（每个 key 独立，不去重）。"""
        out = []
        for text, cat, key, count in rows:
            if key.startswith("learn"):
                lv = self.learned[key] = self.learned.get(key, 0) + 1
                if lv > 1:
                    text = text.replace(" 学会 ", " 把 ", 1) + f" 升到 {lv} 级"
                key = f"{key}.{lv}"
            out.append((text, cat, key, count))
        return out

    def _result_item(self, wall, g) -> None:
        oc = (self.result or {}).get("outcome")
        mins = (self.result or {}).get("minutes")
        tail = f"，用时 {mins:.0f} 分钟" if isinstance(mins, (int, float)) else ""
        text = {"victory": f"🏆 拿下了！对面建筑全部拆光{tail}",
                "conceded-weak": "🏳 打不动了 — 投，下一把", "conceded": "🏳 局势已定 — 投，下一把",
                "defeated": "💀 基地被推平了 — 下一把", "defeated-or-ended": "💀 没守住 — 下一把",
                "timeout": "⏱ 时间到 — 下一把", "ended-by-operator": "⏹ 这局手动结束了"}.get(oc, "⚠ 这局中断了")
        self._emit(self.items, self.seen, f"r{self.line}", wall, g, self.max_tick, _t(text, "big", "result", True))

    def update(self) -> None:
        with self.lock:
            self._update_match()
            self._update_mod()

    def _update_match(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size <= self.off:                   # 对局记录只追加；变短了（不该发生）就当没变
            return
        with open(self.path, "rb") as fh:
            fh.seek(self.off)
            raw = fh.read(size - self.off)
        cut = raw.rfind(b"\n")
        if cut < 0:
            return
        self.off += cut + 1
        for ln in raw[:cut + 1].decode("utf-8", "replace").splitlines():
            self.line += 1
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            kind = r.get("kind")
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            wall = _utc_ts(r)
            tick = int(r.get("tick") or 0)
            self.max_tick = max(self.max_tick, tick)
            g = r.get("gameElapsedSec")
            if kind == "start":
                self.start_wall = wall
                self.game_no = d.get("game")
            elif kind == "experiment":
                self.exp = d
                self._start_item(wall)
            elif kind == "state":
                if not self.started:
                    self._start_item(wall)
                self.race = d.get("race") or self.race
                if wall is not None and g is not None:
                    self.states.append((wall, float(g), tick))
                    self.last_g = float(g)
            elif kind == "intel":
                rc = d.get("race")
                if rc:
                    self.foe_votes[rc] = self.foe_votes.get(rc, 0) + 1
            elif kind == "action":
                if not self.started:
                    self._start_item(wall)
                gg = float(g) if g is not None else self.last_g
                for k, part in enumerate(str(d.get("text") or "").split("\n")):
                    part = part.strip()
                    if part:
                        self._emit(self.items, self.seen, f"b{self.line}-{k}", wall, gg, tick, self._levels(translate(part)))
            elif kind == "result":
                self.result = d
                self.end_wall = wall
                self._result_item(wall, self.last_g)
            elif kind == "lesson":
                self.lesson = d
            elif kind == "finalized":
                self.finalized = True
                self.end_wall = self.end_wall or wall

    def _update_mod(self) -> None:
        p = self.mod_log
        if not p or self.start_wall is None or self.mod_done:
            return
        try:
            size = os.path.getsize(p)
        except OSError:
            return
        if self.mod_off is None or size < self.mod_off:
            self.mod_off = self._mod_seek(p, size)
        if size <= self.mod_off:
            return
        with open(p, "rb") as fh:
            fh.seek(self.mod_off)
            raw = fh.read(min(size - self.mod_off, MOD_TAIL))
        cut = raw.rfind(b"\n")
        if cut < 0:
            return
        base = self.mod_off
        self.mod_off += cut + 1
        pos = base
        # 行首是本地时间 "YYYY-MM-DD HH:MM:SS"：先按字符串比窗口（几万行里绝大多数不在这局，别逐行 strptime）
        lo = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_wall - 1))
        hi = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.end_wall + 3)) if self.end_wall else None
        for bl in raw[:cut + 1].split(b"\n"):
            here = pos
            pos += len(bl) + 1
            ln = bl.decode("utf-8", "replace").rstrip("\r")
            if len(ln) < 20 or ln[4] != "-" or ln[10] != " " or ln[:19] < lo:
                continue
            if hi and ln[:19] > hi:
                self.mod_done = True             # 打完的局：过了结尾就不用再读了
                break
            try:
                wall = time.mktime(time.strptime(ln[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            rows = translate_mod(ln[20:].strip())
            if rows:
                g, tick = self.game_at(wall)
                self._emit(self.mod_items, self.mod_seen, f"m{here}", wall, g, tick, rows)

    def _mod_seek(self, p: str, size: int) -> int:
        """从尾巴往前最多 MOD_TAIL 字节，找到第一行墙钟 >= 这局开局的位置（对齐到行首）。"""
        start = max(0, size - MOD_TAIL)
        if start == 0:
            return 0
        with open(p, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(4096)
        nl = chunk.find(b"\n")
        return start + nl + 1 if nl >= 0 else start

    def all_items(self, upto_tick: int | None = None) -> list[dict]:
        with self.lock:
            rows = self.items + self.mod_items
        # 按游戏时间排：大脑的行带的是这一拍开头的游戏秒，毫秒层的行是按墙钟折算的 —— 按墙钟排会出现「5:28」排在「5:25」前面
        rows = sorted(rows, key=lambda x: (x["g"] if x["g"] is not None else -1, x["w"] or 0, x["src"] == "m"))
        if upto_tick is not None:
            rows = [x for x in rows if (x["tick"] or 0) <= upto_tick]
        return rows

    def clock(self) -> dict:
        return {"t": self.last_g, "race": self.race, "foeRace": self.foe, "tick": self.max_tick}


def map_label(path) -> str:
    if not path:
        return ""
    base = os.path.basename(str(path))
    return re.sub(r"\.w3[xm]$", "", re.sub(r"^\(\d+\)", "", base), flags=re.I).replace("_AMAI", "")


# ── 缓存：每个对局文件一个 MatchFeed ───────────────────────────────────
_feeds: dict[str, MatchFeed] = {}
_feeds_lock = threading.Lock()


def _kernels() -> list:
    try:
        from . import matches as MT
        return MT.kernels()
    except Exception:  # noqa: BLE001
        return []


def match_files(n: int) -> list[str]:
    d = os.path.join(C.inst_dir(n), "matches")
    try:
        return sorted((f for f in os.listdir(d) if f.startswith("match-") and f.endswith(".jsonl")), reverse=True)
    except OSError:
        return []


def feed_for(n: int, name: str) -> MatchFeed:
    path = os.path.join(C.inst_dir(n), "matches", name)
    with _feeds_lock:
        f = _feeds.get(path)
        if f is None:
            f = _feeds[path] = MatchFeed(path, os.path.join(C.inst_dir(n), "mod_combat.log"), _kernels())
            if len(_feeds) > 24:                    # 回看翻过很多局时别无限长
                for k in list(_feeds)[:len(_feeds) - 24]:
                    _feeds.pop(k, None)
    f.update()
    return f


def resolve_match(n: int, match: str | None) -> str | None:
    """?match= 可以是完整文件名、match-20260918-215028 这样的前缀，或 20260918-215028。"""
    files = match_files(n)
    if not match:
        return files[0] if files else None
    key = match if match.startswith("match-") else "match-" + match
    return next((f for f in files if f.startswith(key)), None)


def brain_now(n: int) -> dict | None:
    """此刻局势（brain_live.json）：双方强度、在干嘛、兵/英雄/工人。超过 15 秒没更新就不给（大脑没在跑）。"""
    d = C.load_json(os.path.join(C.inst_dir(n), "brain_live.json"), None)
    if not isinstance(d, dict) or time.time() - float(d.get("wall") or 0) > 15:
        return None
    w = d.get("world") or {}
    atk = d.get("attack") or {}
    state = atk.get("state") or "home"
    doing = ATTACK_CN.get(state, state)
    camp = atk.get("camp") or {}
    if state == "creep" and camp.get("strength"):
        doing = f"打野（营地强度 {camp['strength']:g}）"
    elif state in ("gather", "attack") and atk.get("targetKind"):
        doing = f"{doing}：{TARGET_CN.get(atk['targetKind'], atk['targetKind'])}"
    # 局部交战层（09-21 起的 attack.skirmish）：正在打 / 正在撤的时候，这比「在家发育」更是它此刻在干的事；超过 20 秒没更新的不算
    sk = atk.get("skirmish") if isinstance(atk.get("skirmish"), dict) else {}
    if sk.get("phase") in ("fight", "withdraw") and d.get("t") is not None and float(d["t"]) - float(sk.get("t") or 0) <= 20:
        ratio = sk.get("ratio")
        rt = f"（战力 {float(ratio):.1f} 倍）" if isinstance(ratio, (int, float)) else ""
        doing = f"{doing} · " + (f"⚔ 局部交战中{rt}" if sk["phase"] == "fight" else f"⚡ 局部交战撤退中{rt}")
    dmode = (d.get("defense") or {}).get("mode") or "none"
    op = d.get("opening") if isinstance(d.get("opening"), dict) else None
    opening = {k: op.get(k) for k in ("name", "step", "steps", "current", "active")} if op else None
    return {"opening": opening,
            "t": d.get("t"), "own": w.get("ownStrength"), "foe": w.get("foeStrength"), "army": w.get("army"),
            "heroes": w.get("heroes"), "workers": w.get("workers"), "tier": w.get("tier"), "foeTier": w.get("foeTier"),
            "halls": w.get("halls"), "food": f"{w.get('foodUsed', '?')}/{w.get('foodCap', '?')}",
            "gold": w.get("gold"), "lumber": w.get("lumber"), "doing": doing, "state": state,
            "defense": DEFENSE_CN.get(dmode), "foeRace": w.get("foeRace")}


def stream(n: int, match: str | None = None, tick: int | None = None, limit: int = 80) -> dict:
    """滚动条目。直播：当前这局 + 上一局的尾巴（跨局连续滚动，不清屏 —— 沿用旧页 09-15 定下的设计）；回看：那一局到第 tick 拍。"""
    name = resolve_match(n, match)
    if not name:
        return {"source": {"mode": "none", "inst": n, "match": match}, "items": [], "clock": None, "now": None}
    f = feed_for(n, name)
    review = bool(match)
    items = f.all_items(tick if review else None)
    if not review and len(items) < limit:
        files = match_files(n)
        i = files.index(name) if name in files else -1
        if 0 <= i < len(files) - 1:
            prev = feed_for(n, files[i + 1])
            items = prev.all_items()[-(limit - len(items)):] + items
    try:
        idle = time.time() - os.path.getmtime(f.path)
    except OSError:
        idle = None
    src = {"mode": "match" if review else "live", "inst": n, "match": name, "game": f.game_no,
           "maxTick": f.max_tick, "tick": tick, "finalized": f.finalized,
           "outcome": (f.result or {}).get("outcome"), "idleSec": None if idle is None else int(idle)}
    return {"source": src, "items": items[-limit:], "clock": f.clock(), "now": None if review else brain_now(n),
            "at": time.strftime("%H:%M:%S")}


# ── 顶栏 / 简报 ──────────────────────────────────────────────────────

def _summaries(n: int, limit: int = 400) -> list[dict]:
    from . import matches as MT
    return MT.list_matches(n, limit)


def record(rows: list[dict]) -> dict:
    """战绩：只算新大脑、进胜率的结局。连胜/连败从最新一局往回数。"""
    today = time.strftime("%Y%m%d")
    done = [r for r in rows if r.get("brain") and (r.get("result") or {}).get("outcome") in COUNTED]
    wins = sum(1 for r in done if r["result"]["outcome"] == "victory")
    tw = sum(1 for r in done if r.get("date") == today and r["result"]["outcome"] == "victory")
    tl = sum(1 for r in done if r.get("date") == today and r["result"]["outcome"] != "victory")
    streak = 0
    for r in done:                               # rows 新的在前
        win = r["result"]["outcome"] == "victory"
        if streak == 0:
            streak = 1 if win else -1
        elif (streak > 0) == win:
            streak += 1 if win else -1
        else:
            break
    return {"games": len(done), "wins": wins, "todayW": tw, "todayL": tl, "streak": streak}


def kernel_stats(rows: list[dict], kernels: list) -> dict | None:
    """这一版大脑（最新一局的内核）在这个实例上几胜几负，和上一版比 —— 节目的主线是「它在不在变强」。"""
    cur = next((r.get("kernel") for r in rows if r.get("brain") and r.get("kernel")), None)
    if not cur:
        return None
    kn = cur.get("kernel")

    def wl(k):
        ds = [r for r in rows if r.get("brain") and (r.get("kernel") or {}).get("kernel") == k
              and (r.get("result") or {}).get("outcome") in COUNTED]
        return sum(1 for r in ds if r["result"]["outcome"] == "victory"), sum(1 for r in ds if r["result"]["outcome"] != "victory")

    w, l = wl(kn)
    prev = None
    olds = sorted({(r.get("kernel") or {}).get("kernel") for r in rows if r.get("brain") and (r.get("kernel") or {}).get("kernel")
                   and (r.get("kernel") or {}).get("kernel") < kn})
    for k in reversed(olds):
        pw, pl = wl(k)
        if pw + pl:
            prev = {"n": k, "w": pw, "l": pl}
            break
    subj = next((k["subject"] for k in kernels if k.get("n") == kn), "")
    return {"n": kn, "rev": cur.get("kernelRev"), "dirty": cur.get("dirty"), "w": w, "l": l, "prev": prev,
            "subject": re.split(r"[：:；;。（(]", subj, maxsplit=1)[0][:60] if subj else ""}


def tuner_state(now: float | None = None) -> dict | None:
    """后台自动调参（war3-brain/tuning/<最新的实验>/trials.jsonl）：30 分钟内有动静才算在跑。"""
    root = os.path.join(C.BRAIN, "tuning")
    try:
        studies = [os.path.join(root, s) for s in os.listdir(root) if os.path.isfile(os.path.join(root, s, "trials.jsonl"))]
    except OSError:
        return None
    if not studies:
        return None
    path = max((os.path.join(s, "trials.jsonl") for s in studies), key=os.path.getmtime)
    now = time.time() if now is None else now
    games = wins = asked = 0
    insts: set = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("kind") == "ask":
                    asked += 1
                    insts.add(r.get("inst"))
                elif r.get("kind") == "result" and r.get("valid", True):
                    games += 1
                    wins += r.get("outcome") == "victory"
    except OSError:
        return None
    diff = None
    for k in sorted(i for i in insts if isinstance(i, int)):
        ng = C.load_json(os.path.join(C.inst_dir(k), "next_game.json"), {}) or {}
        diff = ng.get("difficulty") or diff
        break
    return {"study": os.path.basename(os.path.dirname(path)), "games": games, "wins": wins, "asked": asked,
            "insts": sorted(i for i in insts if isinstance(i, int)), "difficulty": diff,
            "running": now - os.path.getmtime(path) < 1800}


def brief(rows: list[dict], cur_name: str | None) -> dict | None:
    """上一局（当前这局之前最近打完的一局）。"""
    for r in rows:
        if r.get("name") == cur_name or not r.get("finalized") or not r.get("result"):
            continue
        res = r["result"]
        return {"match": r.get("name"), "game": r.get("game"), "race": r.get("race"), "foeRace": r.get("foeRace"),
                "outcome": res.get("outcome"), "outcomeText": OUTCOME_CN.get(res.get("outcome"), res.get("outcome")),
                "minutes": res.get("minutes"), "army": res.get("peakArmy"), "foeArmy": r.get("peakFoeArmy"),
                "tier": r.get("tier"), "halls": r.get("maxHalls"), "time": r.get("time")}
    return None


def battles(n: int, f: MatchFeed | None, limit: int = 2) -> list[dict]:
    """这一局（直播：开局到现在；回看：那局起止）打玩家的仗，最近 limit 场。战斗账本由 mod_fight 写。"""
    if f is None or f.start_wall is None:
        return []
    p = os.path.join(C.inst_dir(n), "fights", "battles.jsonl")
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as fh:
            fh.seek(max(0, size - 400_000))
            raw = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    end = (f.end_wall + 5) if f.end_wall else None
    for ln in raw:
        try:
            r = json.loads(ln)
            at = datetime.fromisoformat(r["start"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if at < f.start_wall or (end and at > end):
            continue
        g, _tk = f.game_at(at)
        lm, lf = sum((r.get("lostM") or {}).values()), sum((r.get("lostF") or {}).values())
        pm, pf = sum((r.get("compM") or {}).values()), sum((r.get("compF") or {}).values())
        dead = [_name(h) or h for h in (r.get("heroesDead") or [])]
        out.append({"g": None if g is None else round(g), "where": r.get("where"), "dur": round(float(r.get("dur") or 0)),
                    "us": pm, "them": pf, "lostUs": lm, "lostThem": lf, "heroesDead": dead,
                    "win": lf > lm})
    return out[-limit:]


def meta(n: int, match: str | None = None) -> dict:
    name = resolve_match(n, match)
    f = feed_for(n, name) if name else None
    rows = _summaries(n)
    ks = _kernels()
    setup = (f.exp.get("setup") if f else None) or {}
    out = {"inst": n, "record": record(rows), "kernel": kernel_stats(rows, ks), "speed": setup.get("speed"),
           "setup": {"difficulty": setup.get("difficulty"), "map": map_label(setup.get("map")),
                     "race": _race_of(setup.get("race")), "foeRace": _race_of(setup.get("enemyRace"))},
           "tuner": tuner_state(), "brief": brief(rows, name), "battles": battles(n, f),
           "instances": instances_brief()}
    try:
        out["pageVer"] = round(os.path.getmtime(os.path.join(C.WEB_DIST, "live.html")), 1)
    except OSError:
        out["pageVer"] = 0
    return out


def instances_brief() -> list[dict]:
    """选择框用：有对局记录的实例、最近一局是不是还在写。"""
    out = []
    try:
        names = sorted(int(m.group(1)) for d in os.listdir(C.BIN) if (m := re.fullmatch(r"inst(\d+)", d)))
    except OSError:
        return out
    for k in names:
        files = match_files(k)
        if not files:
            continue
        try:
            idle = time.time() - os.path.getmtime(os.path.join(C.inst_dir(k), "matches", files[0]))
        except OSError:
            idle = None
        out.append({"inst": k, "live": idle is not None and idle < LIVE_IDLE_S, "recent": files[:15]})
    return out

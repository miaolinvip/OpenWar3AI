# -*- coding: utf-8 -*-
"""直播页数据（farsight/server/stream.py）：翻译规则、去重、增量读取的 id 稳定、毫秒层按时间窗口折进来、战绩/版本统计。

原句全部来自 09-18 5~11 号的真实对局记录 / mod_combat.log（一次性分析脚本 vocab.py 扫出来的句式清单）；
期望值是先跑一遍翻译再钉住的（经验：期望要算出来，不是照感觉写出来）。
跑法：cd farsight && python -m unittest tests.test_stream -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import stream as S  # noqa: E402


def texts(line, mod=False):
    return [(t, c) for t, c, _k, _n in (S.translate_mod(line) if mod else S.translate(line))]


class Translate(unittest.TestCase):
    CASES = [
        ("打野选营地：强度 5，战力比 0.9，走 10 + 等兵 76 + 打 34 秒",
         [("🧭 挑野点：强度 5 的营地 — 走 10 秒、打 34 秒，先等援兵 76 秒（战力比 0.9）", "ai")]),
        ("打野选营地：强度 11，战力比 3.3，走 17 + 等兵 0 + 打 17 秒；次优 强度 9 走 20 等 0 秒",
         [("🧭 挑野点：强度 11 的营地 — 走 17 秒、打 17 秒（战力比 3.3）", "ai")]),
        ("打野开打（营地强度 5，到场战力比 3.6）·肉盾先上：3/3 个 -> (-5662,783)",
         [("⚔ 开打野怪（营地强度 5）", "fight"), ("🛡 肉盾先顶上去，远程等接火再打", "ai")]),
        ("打野（营地强度 8）·直接打（全军战力比 8.4）：8/8 个 -> (-3130,-3666)", [("⚡ 营地太弱（全军战力比 8.4）— 不等集合，直接打", "ai")]),
        ("营地清空（强度 5）", [("✔ 清掉一个野点（强度 5）", "fight")]),
        ("打野阵亡：hfoo@14EBDAC4 上一拍血 5/420 @ (-4065,-275)；900 内野怪 nmrm×2、nmrl×1，我方 8 个；出击层 attack",
         [("☠ 打野折了一个步兵", "fight")]),
        ("打野放弃：选定 22 游戏秒还凑不齐（到场战力比 1.1，2500 内能赶到的 1.5 < 2.0）", [("⚡ 等了 22 秒人凑不齐 — 换个营地", "ai")]),
        ("出击打分矿：42.6 <= 48.9", [("⚡ 推演：对面分矿那边 兵+塔 42.6 ≤ 我方 48.9 — 出击！", "ai")]),
        ("出击打分矿：13/13 个 -> (3776,-2688)", [("⚔ 出击：打对面分矿", "fight")]),
        ("打主基地·英雄跟在主力后面：1/1 个 -> (-4307,-168)", [("🎖 英雄跟在主力后面，不打头阵", "ai")]),
        # 「接着打主基地：兵力还剩…」必须先于通用的「打主基地：」—— 第一条命中的规则说了算
        ("接着打主基地：兵力还剩出发时 136%", [("⚡ 兵力还剩出发时 136% — 接着推", "ai")]),
        ("接着打主基地：15/16 个 -> (4672,2944)", [("⚔ 出击：直捣对面主基地", "fight")]),
        ("支援交战（敌 3 个，强度 6，我方 32）：15/15 个 -> (3070,-465)", [("⚔ 主力支援交战（对面 3 个）", "fight")]),
        ("回防（威胁 26）：11/11 个 -> (-2572,3065)", [("🛡 家里来人了（威胁 26）— 全军回防", "fight")]),
        ("撤：主力 2500 内我方 9.0 < 附近敌方 16.5 × 0.8", [("⚡ 算账：主力附近 我方 9.0 < 对面 16.5 — 撤", "ai")]),
        ("撤：主力附近敌方强度 20.8 > 我方 2.0 × 2.45", [("⚡ 算账：主力附近 我方 2.0 < 对面 20.8 — 撤", "ai")]),
        ("清分矿野怪（强度 15）·直接打（全军战力比 4.2）：8/9 个 -> (-7128,-7269)", [("⚡ 分矿口的野太弱（全军战力比 4.2）— 直接打", "ai")]),
        ("接着打分矿：15/15 个 -> (-6400,1600)", [("⚔ 出击：打对面分矿", "fight")]),
        ("毫秒层叫撤（胜率：在场我方 13 vs 敌 4），整队回家（原来在 attack）",
         [("⚡ 毫秒层判这仗要输（世界模型算胜率，在场 我 13 : 敌 4）— 整队撤", "ai")]),
        ("拆家门口的分矿：htow 离家 6809，那边守军 0.0、我方 19.4", [("🎯 对面在我家门口开分矿（守军 0.0）— 去拆", "ai")]),
        ("复活 Hamg（4 级，估价 297 金）：接下", [("⛑ 复活 大魔法师（4 级）", "army")]),
        ("Hamg 学 AHwe：学会", [("📘 大魔法师 学会 召唤水元素", "army")]),
        ("train hfoo（unit，优先级 65）", [("训练 步兵", "army")]),
        ("train hpea（unit，优先级 85）", [("补农民", "econ")]),
        ("train Hamg（hero，优先级 95）", [("🎖 招募英雄：大魔法师", "army")]),
        ("train hkee（upgrade_hall，优先级 67）", [("⬆ 升本 → 主城", "tech")]),
        ("train Rhde（upgrade，优先级 60）", [("🔬 研究 防御", "tech")]),
        ("build hbar @ (-5184,2112) 工人 0x14EB4AD4（试了 1 个点）", [("🔨 开建 兵营", "build")]),
        ("build hbar @ (-4608,3136) 工人 0x14EB4AD4（试了 1 个点，高手落点）", [("🔨 开建 兵营", "build")]),
        ("build hhou @ (-4416,2368) 工人 0x14EB4AD4（试了 3 个点，高手点已占满或被退）", [("🔨 开建 农场", "build")]),
        ("建筑没了：hhou @ (-3840,2880) 血 8/500；900 内 hfoo×5、hrif×2、Hamg×1", [("🔥 农场被拆了", "fight")]),
        ("开局谱：Yumiko 速开矿（23 步，到 720 秒为止）", [("📜 开局照 Yumiko 速开矿 的顺序走（23 步）", "sep")]),
        ("开局谱第 6/23 步：开分矿 城镇大厅 ×2", [("📜 开局谱第 6/23 步：开分矿 城镇大厅 ×2", "ai")]),
        ("开局谱跳过第 8 步 升级 神秘之塔：等了 91 秒（引擎裁决 3）", [("⏭ 开局谱跳过第 8 步 升级 神秘之塔（引擎裁决 3）", "ai")]),
        ("开局谱 Yumiko 速开矿 走完：23/23 步，跳过 1 步，用时 610 秒，交回建造表", [("📜 开局谱 Yumiko 速开矿 走完 — 交回建造表", "sep")]),
        # ── 09-21 起的新句式（inst1 09-22 30 局一次性分析脚本 log_coverage.py 扫出来的原句；改之前全都没上过屏）──
        ("打野开打（营地强度 5，到场战力比 3.9；核心伤害 10.7/秒）：2/4 个 -> (-5403,737)", [("⚔ 开打野怪（营地强度 5）", "fight")]),
        ("打野开打（营地强度 9，到场战力比 2.7、增援在路上；核心伤害 14.4/秒）·召唤物接住拉出的野怪：1/1 个 -> (-3489,4500)",
         [("⚔ 开打野怪（营地强度 9）— 援兵在路上，先打起来", "fight"), ("💧 召唤物先顶住仇恨，本体兵不挨揍", "ai")]),
        ("打野开打（营地强度 8，到场战力比 5.5；核心伤害 7.0/秒）·英雄先手拉出营地：1/1 个 -> (-3128,-3751)",
         [("⚔ 开打野怪（营地强度 8）", "fight"), ("🎖 英雄先手把野怪拉出营地", "ai")]),
        ("打野·有召唤物时永久兵不抢承伤：1/1 个 -> (-5280,928)", [("💧 召唤物先顶住仇恨，本体兵不挨揍", "ai")]),
        ("打野（营地强度 17）·全军很强但尚未到场，先去安全集结点：10/11 个 -> (-5530,-3948)", [("🚶 去野点集合（营地强度 17）", "fight")]),
        ("打野放弃：26 游戏秒里营地总血一点没掉（还剩 2650，我方 10 个）—— 多半够不着，换一个营地",
         [("⚡ 打了 26 秒野怪一滴血没掉 — 多半够不着，换一个", "ai")]),
        ("打野整队退：1/2 人撑不住（我们打光营地要 32 秒，营地打光我们要 6 秒）",
         [("⚡ 算账：打光野怪要 32 秒，野怪打光我们只要 6 秒 — 撤", "ai")]),
        ("打野阵亡：#6 hfoo@147CD194 上一拍血 28/420 @ (-6510,-412)；900 内野怪 nkob×2，我方 10 个；出击层 creep",
         [("☠ 打野折了一个步兵", "fight")]),
        ("打分矿·英雄和施法支援跟在主力后面：4/4 个 -> (-4601,2045)", [("🎖 英雄跟在主力后面，不打头阵", "ai")]),
        ("支援交战·局部战力允许持续输出·集火残血核心：1/4 个 -> (2792,-1415)", [("🎯 集火对面的核心输出", "fight")]),
        ("局部交战 idle->fight：我方战力 464.9、敌方 400.0，比值 1.162，含后续援军比 1.339",
         [("⚔ 局部交战：我方战力 465 vs 对面 400（算上援军 1.3 倍）— 打！", "ai")]),
        ("局部交战 fight->withdraw：我方战力 292.0、敌方 705.2，比值 0.414，含后续援军比 0.392",
         [("⚡ 局部交战：我方战力 292 vs 对面 705 — 撤", "ai")]),
        ("交战：敌 9 个（强度 23），前面的人撑不到援兵：6 秒内赶得到的我方 7 个（强度 21），援兵 10 个还在路上 —— 前面的先退回去汇合（766,-1716），到齐了一起打",
         [("⏳ 对面 9 个，前面的人撑不到援兵 — 先退回去汇合，到齐了一起打", "ai")]),
        ("敌兵已清空，全体集火 owtw（1 座塔，撤出 0 个残血）", [("🎯 敌兵清光了 — 全体集火了望塔", "fight")]),
        ("回防·敌军正在拆建筑反打·集火核心：1/1 个 -> (-4514,3466)", [("🛡 对面在拆家 — 全军反打", "fight")]),
        ("电脑队长的目标点是这座城，提前回家：10/10 个 -> (-4352,-2624)", [("👁 看穿了：电脑这波冲着这座城来 — 提前回家守", "ai")]),
        ("分矿守不住（胜率 22%（兵力比 0.97：我方 30 / 敌方 31）），保兵回主基地：4/8 个 -> (-5830,2676)",
         [("⚡ 分矿守不住（胜率 22%）— 保住兵，退回主基地", "ai")]),
        ("养伤：血没回到五成，先回家附近空地（毫秒层 unitmind）：2/2 个 -> (-4264,2024)", [("🩹 伤兵先回家附近空地养伤", "army")]),
        ("整队恢复：3 名伤兵，缺血 544，由 Hamg 去藏宝室补恢复卷轴", [("🧪 3 个伤兵缺血 544 — 大魔法师去商店买恢复卷轴", "army")]),
        ("整队恢复结束：恢复卷轴已放出：边走边回血（挨打才打断，走路不打断），不原地等", [("🧪 放恢复卷轴 — 大家边走边回血", "army")]),
        ("双线小队 1 出发：3 个 hfoo（强度 9.5、进攻性 13.0，主力余量 53.0/1.5，优势 64/0）", [("🐎 派 3 个步兵去骚扰对面农民", "ai")]),
        ("人口 76/102（上限 80），hkni 被人口挡着、钱够：让 4 个自己人强制攻击一个 hfoo（血 51%）腾人口",
         [("🔪 人口卡在 76 — 杀掉一个自家步兵，给骑士腾位置", "army")]),
        ("民兵清分矿：营地强度 11，号召 5/5 名（优先伐木农民），清完就地解除", [("🔔 敲钟：5 个农民变民兵去清分矿的野（强度 11）", "fight")]),
        ("分矿落点附近有小动物 nrac (-4176,-2781)，派 Hamg 用攻击令打掉（它挡着大厅地基）", [("🐾 分矿地基上有只浣熊挡路 — 英雄去打掉", "ai")]),
        ("维修派出：hlum 713/900，工人 0x147C9764", [("🔧 派农民修伐木场", "econ")]),
    ]
    # 诊断行 / 每拍重复的过程行 / 被退的令：不给观众看
    DROPPED = [
        "英雄看戏：Hamg 450/450 没订单没任务，1000 内 5 个自己人在打",
        "回家路上：3/3 个 -> (-5184,2944)",
        "续建 hhou @ (-4608,1472) 血 447/500：工人 0x14EAB1B4",
        "打野·后面的跟上：1/1 个 -> (-5620,679)",
        "复活 Hamg：被退（金 501）",
        "种族：human（建造表 human）",
        "完全不认识的一句话",
        # 09-21 起的诊断 / 内务行（每局几十上百条）
        "给英雄让路：1/1 个 -> (-5166,2879)",
        "编号 #1 Hamg@147C8E34",
        "英雄被自己人堵住：Hamg 卡了 40 游戏秒没挪窝（离目标 1188），让开 3 个（hfoo、hpea）",
        "英雄 Hamg 450/450 -> fight：fight+22 backline+0 withdraw-999990",
        "英雄不进otrb的射程（原令：打分矿）：1/1 个 -> (-3816,129)",
        "局部交战 withdraw->disengaged：我方战力 230.9、敌方 343.9，比值 0.671，含后续援军比 0.692",
        "支援交战·局部战力允许持续输出·增援接战：10/11 个 -> (-2963,1223)",   # 和「局部交战：打！」是同一件事
        "落单没人管的 3 个兵归队：3/3 个 -> (-4234,2046)",
        "维修回血确认：hbla 685->758",
        "双线骚扰：走向农民：3/3 个 -> (4319,164)",
    ]
    MOD = [
        ("胜率叫撤(世界模型)：接触后 42 游戏秒 胜率 2%（门槛 8%），近处 我方 8 vs 敌 3 —— 拉回 8/8 人",
         [("🧠 世界模型：这仗胜率只剩 2% — 全体撤", "ai")]),
        ("整队撤退(100ms)：打光他们要 130s、他们打光我们要 11s（在场 1 人 + 赶来 0 人，共 420 血 vs 3 只 1200 血）—— 拉回 1/1 人",
         [("⚡ 0.1 秒算完这波账：磨死对面要 130 秒，我方只撑 11 秒 — 撤！", "ai")]),
        ("英雄被集火：Hmkg@1422BAE4 775/775，身边 3 个敌人里 3 个在打它 —— 拉到 (1577,-1173)（154ms 内响应）",
         [("🎯 山丘之王被集火（3 个在打它）— 马上拉出来", "fight")]),
        ("英雄施法：Hmkg@1422BAE4 stormbolt -> 0x1234", [("✨ 山丘之王 放 风暴之锤", "fight")]),
        ("用物品：Udea@14E18B24 850/850 第1格 stwp（身边敌军 0、在打它 2、家里敌军 26）-> ok", [("🎒 死亡骑士 用了 回城卷轴", "army")]),
        # 09-22 起播：一场仗报几十个，去重窗口 40 秒（pull）只留一条
        ("hfoo@1499C864 83/420 正在挨 12 血/秒（1.5 秒窗口），2.5 秒后只剩 50 —— 拉出来（打玩家）（2ms 内响应）",
         [("🩸 步兵快被打死了 — 2 毫秒内拉了出来", "fight")]),
        ("孤兵回撤：hrif@14227DA4 535/535 身边 700 内敌 >= 3、900 内自己人 <= 1 —— 退到 (4183,2035)", []),
        ("心跳：采样 473 次，救下 40 个", []),
        # 09-21 22:18 起单位带编号「#1 Hamg@」—— 旧规则认不出，施法 / 被集火从那以后没上过屏
        ("英雄施法：#1 Hamg@14589144 waterelemental @(-5169,1566)", [("✨ 大魔法师 放 召唤水元素", "fight")]),
        ("英雄施法：#18 Hblm@14205BF4 siphonmana -> 1420A264", [("✨ 血魔法师 放 魔法吸吮", "fight")]),
        ("英雄被集火：#1 Hamg@14218E34 609/700，身边 3 个敌人里 3 个在打它 —— 拉到 (2501,2571)（389ms 内响应）",
         [("🎯 大魔法师被集火（3 个在打它）— 马上拉出来", "fight")]),
        ("英雄预警撤退：#19 Hblm@1458CE84 829/850 身边 3000 内敌 8 个、我方 2 个，打光他们要 150s、他们打光我们要 9s —— 没接触就先拉（别等被围）",
         [("👁 血魔法师身边 敌 8 : 我 2 — 没接触就先撤", "ai")]),
        ("英雄局部自保：#1 Hamg@14589144 549/575 孤军且敌方占优，敌 5、有效护卫 0，脱离战场 10.0s -> (-4399,1192)",
         [("🏃 大魔法师孤军深入、对面人多（敌 5 : 护卫 0）— 先退一步", "ai")]),
        ("英雄被控住：#1 Hamg@13EF8E34 492/775 眩晕（BPSE）—— 不能驱散，等它结束", [("😵 大魔法师被眩晕了", "fight")]),
        ("#7 hfoo@1458C554 291/420 正在挨 93 血/秒（1.4 秒窗口），2.5 秒后只剩 56 —— 拉出来（打野）（172ms 内响应）",
         [("🩸 步兵快被打死了 — 172 毫秒内拉了出来", "fight")]),
        ("基地压倒性劣势退到建筑后(100ms)：打光他们要 37s、他们打光我们要 15s（这片战场 我方 6 人 2549 血 vs 敌 11 个 4603 血）—— 拉回 4/6 人",
         [("⚡ 家门口打不过：磨死对面要 37 秒，我方只撑 15 秒 — 退到建筑后面", "ai")]),
        ("撤离未脱险·改道 #7 hfoo@1458C554 215/420 第 1 次 -> (-5095,-2141)", []),
        ("补攻击令：这片战场我方 8 人，其中 4 个引擎说没任务 —— 补了 3 个（敌 4 个）", []),
    ]

    def test_cases(self):
        for line, want in self.CASES:
            self.assertEqual(texts(line), want, line)

    def test_dropped(self):
        for line in self.DROPPED:
            self.assertEqual(S.translate(line), [], line)

    def test_mod(self):
        for line, want in self.MOD:
            self.assertEqual(texts(line, mod=True), want, line)

    def test_every_spell_seen_in_mod_combat_has_a_chinese_name(self):
        # 09-18 5~11 号 mod_combat.log 里出现过的全部 9 种（stormbolt / holylight 是模块自己的叫法）
        for order in ["waterelemental", "stormbolt", "blizzard", "holylight", "windwalk", "chainlightning",
                      "divineshield", "spiritwolf", "thunderclap", "flamestrike", "siphonmana", "phoenix"]:
            nm = S.spell_name(order)
            self.assertTrue(nm and S._CJK.search(nm), (order, nm))

    def test_count_only_for_real_things(self):
        # 可计数（×N）= 一行一个实物；意图类（每拍重下的令）不计数 —— 旧页「建造 兵营 ×2」数的是日志行
        self.assertTrue(S.translate("营地清空（强度 5）")[0][3])
        self.assertFalse(S.translate("build hbar @ (0,0) 工人 0x1（试了 1 个点）")[0][3])


def rec(kind, t0, dt, tick, data, g=None):
    at = t0 + dt
    return json.dumps({"t": time.strftime("%H:%M:%S", time.localtime(at)) + ".000",
                       "utc": datetime.fromtimestamp(at, timezone.utc).isoformat(), "tick": tick, "kind": kind,
                       "gameElapsedSec": g, "data": data}, ensure_ascii=False) + "\n"


def modline(t0, dt, body):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0 + dt)) + " " + body + "\n"


class Feed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "match-20260918-215028-006-e9f74b20.jsonl")
        self.mod = os.path.join(self.tmp, "mod_combat.log")
        self.t0 = float(int(time.time()) - 600)
        t0 = self.t0
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(rec("start", t0, 0, 0, {"game": 6, "brain": "war3-brain"}))
            fh.write(rec("experiment", t0, 0.1, 0, {"brain": "war3-brain", "kernel": 64,
                                                    "combatVersion": "creep-output-retreat-v8", "combatRev": "abc1234",
                                                    "setup": {"map": r"D:\x\(2)EchoIsles.w3x", "speed": 300, "race": 1,
                                                              "enemyRace": 2, "difficulty": 3}}))
            # 墙钟 1 秒 = 游戏 3 秒（3 倍速）
            for i in range(0, 21):
                fh.write(rec("state", t0, 1 + i * 2, i, {"race": "human"}, g=3 + i * 6))
            fh.write(rec("action", t0, 2, 1, {"text": "支援交战（敌 3 个，强度 6，我方 32）：15/15 个 -> (0,0)"}, g=9))
            fh.write(rec("action", t0, 5, 3, {"text": "支援交战（敌 4 个，强度 6，我方 32）：15/15 个 -> (0,0)"}, g=21))   # 12 游戏秒后：吞掉
            fh.write(rec("action", t0, 15, 7, {"text": "支援交战（敌 5 个，强度 6，我方 32）：15/15 个 -> (0,0)"}, g=45))  # 36 秒后：再播
            fh.write(rec("action", t0, 16, 8, {"text": "营地清空（强度 5）\n营地清空（强度 5）"}, g=48))                   # 可计数：两条都留
            fh.write(rec("action", t0, 17, 8, {"text": "Hamg 学 AHwe：学会"}, g=50))
            fh.write(rec("action", t0, 18, 9, {"text": "Hamg 学 AHwe：学会"}, g=53))
        with open(self.mod, "w", encoding="utf-8") as fh:
            fh.write(modline(t0, -30, "英雄施法：Hamg@1 waterelemental @(0,0)"))       # 开局前：不算这局
            fh.write(modline(t0, 10, "英雄施法：Hamg@1 blizzard @(0,0)"))
            fh.write(modline(t0, 11, "孤兵回撤：hrif@1 535/535 —— 退到 (0,0)"))       # 不播

    def feed(self):
        return S.MatchFeed(self.path, self.mod, [{"n": 64, "subject": "打野阵亡的三个主因一起改：① 营地危险度表"}])

    def test_items(self):
        f = self.feed()
        f.update()
        got = [(it["text"], it["cat"]) for it in f.all_items()]
        self.assertEqual(got, [
            ("━━ 第 6 局开打：人族 vs 兽族（普通电脑） · EchoIsles · 3 倍速 ━━", "sep"),
            ("🧠 本局大脑：内核 #64 · 战斗 creep-output-retreat-v8/abc1234（打野阵亡的三个主因一起改）", "sep"),
            ("⚔ 主力支援交战（对面 3 个）", "fight"),
            ("✨ 大魔法师 放 暴风雪", "fight"),
            ("⚔ 主力支援交战（对面 5 个）", "fight"),
            ("✔ 清掉一个野点（强度 5）", "fight"),
            ("✔ 清掉一个野点（强度 5）", "fight"),
            ("📘 大魔法师 学会 召唤水元素", "army"),
            ("📘 大魔法师 把 召唤水元素 升到 2 级", "army"),
        ])
        blz = next(it for it in f.all_items() if "暴风雪" in it["text"])
        # 墙钟 t0+10 在快照 t0+9（游戏 27 秒）之后 1 秒，3 倍速外推 = 30，且不超过下一份快照（t0+11，游戏 33）
        self.assertEqual(blz["g"], 30.0)
        self.assertEqual(f.clock()["foeRace"], "orc")

    def test_ids_stable_across_incremental_reads(self):
        f = self.feed()
        f.update()
        before = [it["id"] for it in f.all_items()]
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(rec("action", self.t0, 40, 20, {"text": "build hbar @ (0,0) 工人 0x1（试了 1 个点）"}, g=120))
            fh.write(rec("result", self.t0, 41, 20, {"outcome": "victory", "minutes": 22.6}))
        with open(self.mod, "a", encoding="utf-8") as fh:
            fh.write(modline(self.t0, 39, "英雄被集火：Hmkg@1 700/700，身边 4 个敌人里 3 个在打它 —— 拉到 (0,0)"))
        f.update()
        after = f.all_items()
        self.assertEqual([it["id"] for it in after][:len(before)], before)
        self.assertEqual([it["text"] for it in after][len(before):],
                         ["🎯 山丘之王被集火（3 个在打它）— 马上拉出来", "🔨 开建 兵营", "🏆 拿下了！对面建筑全部拆光，用时 23 分钟"])

    def test_ids_unique_across_matches(self):
        # 直播时当前局不够条数会拼上一局的尾巴（stream()）；两局同样的行号原来是同一个 id，
        # 页面按 id 找「上次滚到哪」找到上一局那条、把中间的旧内容重滚一遍（09-22 刷屏）
        other = os.path.join(self.tmp, "match-20260918-221500-007-0a1b2c3d.jsonl")
        with open(self.path, encoding="utf-8") as src, open(other, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        a, b = self.feed(), S.MatchFeed(other, self.mod, [])
        a.update()
        b.update()
        ia, ib = [it["id"] for it in a.all_items()], [it["id"] for it in b.all_items()]
        self.assertEqual(len(ia), len(ib))
        self.assertFalse(set(ia) & set(ib))
        self.assertTrue(all(i.startswith("e9f74b20:") for i in ia))
        self.assertEqual({it["src"] for it in a.all_items()}, {"s", "k", "b", "m"})

    def test_review_upto_tick(self):
        f = self.feed()
        f.update()
        self.assertEqual([it["text"] for it in f.all_items(upto_tick=3)][2:], ["⚔ 主力支援交战（对面 3 个）"])


class Now(unittest.TestCase):
    """顶栏局势（brain_live.json）：局部交战层在打 / 在撤时写进「在干嘛」，过时的不算。"""

    def now(self, t, skirmish):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "brain_live.json"), "w", encoding="utf-8") as fh:
            json.dump({"wall": time.time(), "t": t, "world": {}, "attack": {"state": "home", "skirmish": skirmish}}, fh)
        old = S.C.inst_dir
        S.C.inst_dir = lambda n: tmp
        try:
            return S.brain_now(1)["doing"]
        finally:
            S.C.inst_dir = old

    def test_skirmish_phase(self):
        self.assertEqual(self.now(960.0, {"phase": "fight", "ratio": 2.62, "t": 950.7}), "在家发育 · ⚔ 局部交战中（战力 2.6 倍）")
        self.assertEqual(self.now(960.0, {"phase": "withdraw", "ratio": 0.41, "t": 955.0}), "在家发育 · ⚡ 局部交战撤退中（战力 0.4 倍）")
        self.assertEqual(self.now(960.0, {"phase": "idle", "ratio": 2.62, "t": 950.7}), "在家发育")
        self.assertEqual(self.now(990.0, {"phase": "fight", "ratio": 2.62, "t": 950.7}), "在家发育")   # 40 秒没更新：过时


class Stats(unittest.TestCase):
    def row(self, oc, kernel=64, date="20260918", brain=True):
        return {"brain": brain, "result": {"outcome": oc} if oc else None, "kernel": {"kernel": kernel, "kernelRev": "x"},
                "date": date}

    def test_record_counts_only_real_outcomes_and_streak(self):
        rows = [self.row("victory"), self.row("ended-by-operator"), self.row("victory"), self.row("victory"),
                self.row("conceded-weak"), self.row("crash"), self.row(None), self.row("victory", brain=False)]
        r = S.record(rows)
        self.assertEqual((r["games"], r["wins"], r["streak"]), (4, 3, 3))      # 手动结束/中断/没结局/老 AI 不算，连胜跳过手动结束

    def test_kernel_vs_previous(self):
        rows = [self.row("victory", 64), self.row("conceded-weak", 64), self.row("victory", 63), self.row("defeated", 60),
                self.row("victory", 60), self.row("ended-by-operator", 63)]
        k = S.kernel_stats(rows, [{"n": 64, "subject": "打野阵亡的三个主因一起改：① 营地危险度表"}])
        self.assertEqual((k["n"], k["w"], k["l"], k["prev"], k["subject"]),
                         (64, 1, 1, {"n": 63, "w": 1, "l": 0}, "打野阵亡的三个主因一起改"))


if __name__ == "__main__":
    unittest.main()

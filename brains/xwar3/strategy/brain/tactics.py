# -*- coding: utf-8 -*-
"""战术决策（纯逻辑，只出"意图"，不下令）：AMAI 的出击循环 attack_sequence_all + 守家 ARMY_TRACK + 打野。

意图 Intent(kind, units, x, y, target, why)：
  attack_move 攻击移动到点（沿途遇敌就打） / move 纯移动（撤退、集结，不还手） / attack 打指定单位
执行层（brain/act.py）把意图翻成 w3cmd 调用；这里不碰通道，方便单测。

和 AMAI 的差别（都写在对应位置）：
  * 敌方强度用快照真实值，不加噪；出击按目标附近、可抵达援军和沿路威胁评估；
  * 目标打掉后 AMAI 只在原地 1500 找人接着打、然后回家回血；这里兵力还剩出发时 70% 就直接接下一个目标
    （策略层 v291 的证据：胜局出击没崩后 62% 在 15 拍内再出击，其它局 20%）；
  * 守家「威胁超过主力能扛的」AMAI 只是不回防；这里把主力拉到受威胁大厅背敌一侧，主力到齐、敌人压上、18 秒到期或大厅 < 40% 就打。

09-17 实机 5 号第二局补的四条（证据在各常量注释）：主力按聚团算、出击前集结、援兵先汇合、打野认准一个营地打完。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from . import amai
from . import battlepower as BP
from . import fightcall as FC
from . import offense
from . import gamedata as G
from . import heroplan as HP
from . import winprob as W
from . import engagement as ENG
from .engagement import Engagement
from .heroplan import HeroPlanner
from .skirmish import Skirmish
from .world import cluster_armies

CREEP_CAMP_RADIUS = 800.0
# ★★ 09-18 打野顺序：优先打近处营地、缩短路线，减少跑路时间；近处打不过的，要比较「补兵等待」和「跑去远处营地」
#   哪个更省时间，战力估算要把召唤物算进去。
#   实测（09-18 13 点后 40 局，含调参实例）：清完一个营地到下一个营地开打，中位 34 游戏秒、p75 64。原来：
#     * 选营地挑**离家**最近的 —— 打完一个往回走；
#     * 打不过的直接跳过，不算「等援兵」；
#     * 我方强度是 AMAI 口径 (人口+等级)/2：3 级水元素（900 血、每秒 30）只算 3、骷髅算 0；野怪那边按等级和 —— 两边不是一把尺子。
#   改成：
#   ① 战力按兰彻斯特平方律：P = 总血 × 总每秒伤害，召唤物照算（血、伤害都是真的）。
#      R = P我 / P营地 =「他们打光我们要几秒」/「我们打光他们要几秒」—— 和毫秒层整队撤退同一套算术（mod_combat「打光他们要 X 秒」）。
#   ② 每个营地估时间（creep_plans）：从**主力现在的位置**走过去 + 等兵（R 不够时按最近 GROWTH_WINDOW 我方强度的涨速）+ 打（营地总血 / 我方每秒伤害），
#      挑「营地等级和 / 总时间」最高的：近的小营地时间短自然先打；近处打不过要等很久的，就去远处打得过的。
# ★★ 09-21：下面这一组打野门槛不再是手调的比值，全部由 `brain/winprob.py` 的
#   **一条**线推出来：LOSS_CHEAP =「打野预计我方损失份额的上限」。
#   依据是 964 个战斗帧文件 / 约 6000 场仗的校准（tools/calibrate_power.py）：
#     * 打野每一档战力比都**赚钱**（净 +106 ~ +885 金），胜率地板就有 75%；
#     * 所以门槛该按「会死多少人」定，不该按「打不打得过」定；
#     * 旧的 2.5 / 2.0 / 1.6 / 1.0 这条手调梯子，换算过来正好是损失份额 24/26/28/31%
#       —— 手感调对了，只是没人知道它在说什么。现在放宽到 27%（2.5 -> 1.81），
#       因为「打野太少、英雄不练级」是已知问题，而每一档都赚钱。
CREEP_POWER_MIN = W.R_CREEP        # 「现在打得过」= 预计损失份额 <= LOSS_CHEAP
CREEP_ENGAGE_POWER = W.R_CREEP     # 到场（营地 CREEP_GATHER_R 内）的人对营地，同一条线
CREEP_ENGAGE_POWER_LATE = W.R_CREEP_HOLD   # 选定 CREEP_LOWER_S 还没开打：放宽到 LOSS_HOLD（迟滞上沿）
CREEP_ABORT_POWER = W.R_CREEP_ABORT  # 打野中主力对营地剩下的低于这个就回家 ——★ 09-18 只在开打以后判
# ★ 09-21：上面那条回答「打不打得过」，答不了「够不够得着」。3-007 局 201 游戏秒对着
#   (-6667,-3705) 反复下令，战力比一直是 99.0（上限）所以永远不 abort，而营地血一点没掉。
CREEP_NO_PROGRESS_S = 25.0  # 已开打的营地这么久总血没降 -> 判够不着，放弃换下一个
CREEP_PROGRESS_HP = 1.0     # 总血至少掉这么多才算「有进展」（躲开浮点抖动）
CREEP_DIRECT_POWER = W.R_CREEP_EASY   # 全军对营地到这个 = 轻松营地（预计损失落在地板和门槛正中间）；仍先纯移动到安全点
CREEP_EASY_ENGAGE = W.R_CREEP_HOLD    # 先到的人自己对轻松营地不吃亏就点名开打、后面的跟上；连这个都不到就在营地外等
CREEP_REINFORCE_ENGAGE = W.R_CREEP_ABORT  # 到场前排略吃亏也能开（但不到该撤那条线），CREEP_GROUP_R 内在途增援合起来仍要达到正常开打线
CREEP_REINFORCE_MIN = 2        # 至少两名非英雄到场，防止“大法师 + 水元素”借全军战力隔空开怪
CHAIN_TRAVEL_S = 25.0       # ★ 09-18 英雄没满血（< HERO_READY_HP）时，这么多游戏秒走得到、不用等兵（R >= CREEP_POWER_MIN）的营地接着打，不回家
# 09-18 部队在打野和回集合点之间反复徘徊，浪费大量时间。改为：附近营地打得过就直接打，
#   打完按距离和当前剩余血量/战力评估能否接着打周围最近的营地。实机 140827-027 局：
#   第 14~16 拍「选营地 战力比 0.9、等兵 77 秒」-> 下一秒「打不动回家 0.92 < 1.0」，1.5 秒一圈（还没开打就判打不动）；
#   第 21 拍清分矿（全军 3.0）先到的 0.69 就判回家；第 36 拍强度 8、全军 4.7 的小营地，先到的 2 个也被拉回集合点等人。
CLEAR_POWER = W.R_CREEP     # 清分矿野怪：和打野同一条线（LOSS_CHEAP）。这一口直接关系到经济
HERO_DPS_BASE = 12.0        # 英雄每秒伤害按等级估：数据表只有基础攻击（大法师 5），实际 = 基础 + 主属性 + 技能
HERO_DPS_LVL = 5.0
CREEP_WAIT_MAX = 90.0       # 等兵要超过这么久（游戏秒）的营地不考虑
CREEP_PATH = 1.25           # 直线距离 × 这个 ≈ 实际路程（绕树、绕坡）
CREEP_OVERHEAD = 8.0        # 每个营地的固定开销（集合、开打、捡东西），游戏秒
CREEP_ROUTE_AVOID = 950.0   # 去目标营地的直线走廊这么近还有另一组野怪，就会像 1-011 一样同时惊醒两营
IDLE_FRONT_R = 700.0        # 09-22 晚 空档安排（AttackSequence._idle_plan）：城朝敌方方向这么远是「前沿」
IDLE_FRONT_DEFAULT = (0.0, 0.0)   #   看不见敌方主基地：朝地图中心
IDLE_KEEP_R = 400.0         #   已经站在前沿点这么近就不重下
IDLE_CAMP_ARMY_CLEAR = 3000.0     #   剩下的营地：看得见的敌方军团都离它这么远以外才去
IDLE_CAMP_RATIO = 4.0       #   主力对它的战力比（血 × 伤害）至少这么多
IDLE_CAMP_AHEAD = 1.1       #   而且我方强度 >= 对面 × 这个（1-411：对面主力两秒就到，比我们强时不去）
IDLE_CAMP_SIDE = 1.15       #   营地离我方最近大厅 <= 离对面最近大厅 × 这个（我们这半边或中间）
IDLE_CAMP_MAX_HOME = 6000.0 #   而且离我方最近大厅不超过这么远
ROUTE_CREEP_AHEAD = 1.15    # 09-22 晚 出击顺路清野（AttackSequence._route_camp）：占优 = 我方强度 >= 对面 × 这个（和骚扰同一条线）
ROUTE_CREEP_R = 900.0       #   离「主力 -> 目标」连线这么近的营地算顺路（offense.ROUTE_R 同一个数）
ROUTE_CREEP_T = (0.05, 0.8) #   只清前方的（身后的不回头，快到目标的不停）
ROUTE_CREEP_RATIO = 4.0     #   主力对营地的战力比（血 × 伤害）至少这么多：轻松打、不拖出击
CREEP_WAKE_R = 650.0        # 带伤前移（清营后补给）路过任何野怪这么近就不前移（野怪警戒约 500；4-036 那次是约 580）
CREEP_SECOND_CONTACT = 750.0  # 执行中非目标营地的野怪进到这么近，立即按多营夹击撤离
CREEP_SECOND_SCOPE = 1500.0   # 只看离当前营地这么近的我方单位（在这场仗里的人）；远处赶路的援兵路过别的营地不算（6-191）
CREEP_WITHDRAW_MAX_S = 18.0   # 毫秒层已接管的伤员不能把旧营地撤离屏障永久卡住
GROWTH_WINDOW = 120.0       # 我方强度涨速按最近这么久算（游戏秒）
ENEMY_BASE_AVOID = 2500.0   # 离敌方建筑这么近的营地不去
CREEP_MAX_HOME = 7000.0     # 营地离家不超过这么远、且离家比离对面主基地近（09-17 实机：拆完对面分矿，穿过全图去它家门口打野）
CREEP_ENGAGE_MIN = 2        # 到营地 CREEP_ENGAGE 内有这么多人就开打，不等主力重心到（09-18 圣骑士带一队人到了营地附近迟迟不开打：
                            # 部队是零零散散走过去的，主力重心要等最后几个人才算到，实机等了 70 游戏秒才开第一下）
CREEP_HERO_JOIN = 1500.0    # 打野点名开打时，这么近的英雄也一起点名（09-18 小兵已开打，英雄停在后方不参战）
CREEP_ENGAGE = 900.0       # 主力离营地这么近就直接点名打野怪（睡着的野怪攻击移动不打）
SEARCH_RADIUS = 1500.0      # SleepInCombatAM：目标没了在原位置 1500 内找人接着打
# 出击「任务停滞」（_push：60 秒没走近 200、目标没掉血 -> 回家重整）也要认「在杀东西」这种进展（_combat_progress）
# ★ 09-22 优势局全军突然回家（1-288 18 分）—— 18:35「进攻任务无进展，回家重整」，
#   当时我方强度 39.4、敌方 2.8：那 60 秒全军在要塞边上把掠夺者、粉碎者、牛头人酋长、先知都打掉了（敌方 24.8 -> 2.8），
#   只是还没砍到要塞本身，于是被判成停滞，12 个人全回家。
STALL_FIGHT_R = 1000.0      # 主力这么近有敌方作战单位 = 正在交战，算有进展
STALL_NEAR_R = 2500.0       # 主力这么近的敌方建筑少了一座 = 拆掉了东西，算有进展
STALL_FOE_DROP = 0.5        # 敌方总强度降了这么多 = 打死了人，算有进展
SEARCH_TRIES = 4
CHAIN_LEFT = 0.70           # 兵力还剩出发时这么多就接下一个目标（对面兵力打没了不看这条）
BUILDINGS_WHEN_FOE_LE = 0.25  # 对面兵力 <= 我方这么多（或者一座大厅都没了），零散建筑也当出击目标
GATHER_RADIUS = 1200.0      # 出击前集结（AMAI FormGroupAM）：主力 1200 内的强度占全军 80% 才出发
GATHER_READY = 0.80
GATHER_MAX_S = 40.0         # 游戏秒：等这么久还没凑齐也出发
GATHER_FORWARD = 1800.0     # 有明确目标时，集结点从主力位置沿目标方向前推；不要每次都围着基地重新集合
GATHER_STANDOFF = 2800.0    # 但至少离目标这么远，避免零散单位在集结阶段先撞上守军
REINFORCE_FAR = 2000.0      # 出击中离主力这么远的兵先去跟主力汇合，不单独冲目标（策略层 v291 W3-SplitReinforce）；对面兵力打没了不用汇合
MAIN_FORCE_NEAR = 0.8       # 有目标时，强度够最强那团 80% 的团里取离目标最近的当主力
STRAGGLER_R = 2000.0        # 离主力重心这么远的单个兵算掉队，单独收回来（主力聚团半径 1500 之外再留 500 余量）
RETREAT_COOLDOWN_S = 45.0   # 撤回来之后这么久不再出击（回血、等援兵；09-17 实机撤退 4 秒后又拿 7 个兵打主基地）
EXPAND_GUARD_R = 1400.0     # 分矿这么近有野怪就先清（09-18：人族一局开矿 6 次、大厅数 1->2->1 地基十几秒就没；亡灵 8 次闹鬼金矿一次没出现）
# ★★ 09-21：出击这一组也由 winprob 的两条线推出来（P_COMMIT / P_EXPANSION，都是**校准过的胜率**）。
#   实际要求 ≈ 守军强度 × ATTACK_MARGIN × target_reserve，所以基础 margin 取「分矿」那一档，
#   主基地那一档写成两者的商，乘起来正好是推出来的值。
#   ⚠ 校准查出来的硬事实：打玩家的仗胜率**在 44% 封顶**（战力比从 5 涨到 30 毫无改善），
#     而我方损失份额在 57% 触底 —— 十档战力比，每一档的「敌损金 - 我损金」都是负的。
#     所以 P_COMMIT 定 0.40 已经贴着天花板；再往上等于永远不出击，
#     而出击的价值本来就不在换兵，在拆掉对面的建筑和经济。
ATTACK_MARGIN = W.R_ATTACK_EXPANSION          # 可抵达目标的守军优势门槛；不再拿全图总兵力二次否决
CAPPED_MARGIN = 1.0      # 09-23 人口顶满又有存款（capped_rich）时的出击门槛（1-416 / 1-418 兵满钱堆着一直在家）
CAPPED_BANK = 1000       #   存款至少这么多（够补 5~7 个兵）
CAPPED_FREE = 4          #   离人口上限不到这么多 = 顶满（runtime.food_soft_cap 的 80 / 100）
STALE_S = 60.0           # 09-23 在家干站（出击判不够、没营地打）这么多游戏秒 = 僵持（1-427 18 分钟起全员在家不动）
STALE_RATIO = 1.1        #   全局强度我方 >= 对面这么多倍才破局出击（门槛同 CAPPED_MARGIN）
ATTACK_LAUNCH_RESERVE = 1.0                   # 发起时不额外加：目标那一档（下面两行）说了算
ATTACK_MAIN_RESERVE = W.ATTACK_MAIN_MULT      # 086/088/089：1.22~1.33 仍在长途行军后输掉 12:6、14:7、14:5；主基地要给复活/新产兵留余量
ATTACK_EXPANSION_RESERVE = 1.0                # 分矿不再叠，基础 margin 已经是它那一档
GATHER_MAIN_COMMIT_RESERVE = W.GATHER_MAIN_MULT  # 集结复核比发起时松 W.GATHER_HYST（迟滞），不能重新放行 086/088 的区间
GATHER_EXPANSION_COMMIT_RESERVE = W.GATHER_EXPANSION_MULT
HERO_RETREAT_HP = 0.50      # 英雄价值最高：半血就退出战略进攻令，微操层继续负责更早的集火/包围脱离
HERO_READY_HP = 0.70        # 至少恢复到七成血再进入新任务，避免刚脱离就被战略层重新派回前排
COUNTER_WINDOW_S = 60.0     # 反推窗口：这么多游戏秒内对面兵力强度的峰值
COUNTER_DROP = 1.25         #   >= 现在 × 这个（对面刚掉了 20% 以上）= 刚打赢一仗，英雄没回血也可以出击（见 step 里 heroes_ready）
DANGER_R = 1500.0           # 英雄血 < HERO_RETREAT_HP 且这么近有敌方作战单位 -> 不管什么状态都先回家（遇到敌方小分队时英雄丝血也不撤、白白阵亡）
REINFORCE_JOIN_MAX = 3500.0 # 援兵离主力比这远、又凑不够 REINFORCE_MIN 个 -> 先回家攒人（853 秒「援兵先去跟主力汇合 1/1」单个横穿地图撞上敌军）
REINFORCE_MIN = 3
PUSH_MIN_SHARE = 0.5        # 出击中冲在主力团里的人不到会来汇合的一半 -> 主力停下等（1-280 3 个人冲进对面基地送）；
                            # 集结阶段已经等过一轮（等满出发），「前 4 后 3」这种照常走（test_tactics.test_gather_then_split_reinforcements）
RAID_HOME_R = 7000.0        # 敌方分矿离我家这么近就算「家门口」（09-18 AMAI 局：它在离我家 5916 的矿点开了三矿，我们 23 个兵在家站着）
IDLE_RAID_MAX_HOME = 16000.0  # 空档（_idle_plan）去拆对面分矿时的距离上限：整张图都算，打不打得过仍由 raid_target 判
RAID_LOCAL_R = 1800.0       # 数这座分矿身边有多少守军 / 塔
RAID_MARGIN = W.R_ATTACK_MAIN   # 我方强度要 >= 当地守军 × 这个才去拆（不看对面全图兵力 —— 它主力在别处）。
                            # 用主基地那一档（P_COMMIT）：当地守军数得准，不像远程评估要打折。
RAID_TOWER_MULT = 2.0       # 没有攻城单位时塔按 2 倍算
TOWER_FOCUS_R = 1400.0      # 主力这么近有敌方塔，就纳入「敌兵清空后集火塔」的判断（塔射程 700~800）
TOWER_CLEAR_PAD = 150.0     # 残血单位退到塔射程外再留这么多余量（塔不追，走出去就安全）；毫秒层 mod_combat.TOWER_SAFE_PAD 是同一个数
# ★ 09-22 打对面分矿时兵力分散（1-309 12 分）：几个兵去打建筑、其余兵和英雄在外面打兵；应全部优先集火敌方单位，
#   小兵也不该顶着有攻击力的建筑打（扛不住伤害，死了好几个）。
#   目标表 + 战斗帧：t=233~241 我方 3~4 个在打箭塔 owtw / 地洞 otrb / 要塞，同时 1200 内有 5~8 个敌兵、只有 1~3 个在打兵；
#   5 个步兵死在 owtw + otrb 旁边，死时 800 内一个敌方单位都没有 —— 被建筑打死的。它们收到的全是大脑的攻击移动（打分矿、增援接战），
#   走进射程就被引擎自动索敌拖去打建筑。上面 _towers_first 只管「敌兵清空之后」，有敌兵的时候没人管。
#   _tower_guard（出击层意图的最后一道）：附近还有敌兵 -> 点名打建筑的改打兵；站在能打人的建筑射程里的兵改去打射程外的敌兵，
#   敌兵全缩在塔下、我方凑不够 TOWER_GUARD_GROUP 个就先退出射程。
ARMED_BUILDINGS = G.TOWERS | {"otrb"}   # 兽族地洞：苦工钻进去会射人（1-309 死的 5 个步兵旁边都有它）；tables/human.ARMED 是同一张表
# ★ 09-22 大法师再次被敌方塔射死（1-333 22 分），同类问题反复出现。
#   那局大法师死了两次，都死在兽族地洞（otrb，射程 700）里，而「英雄不进塔」这条规则两处都没管到它：
#   _tower_guard 只改非英雄；_towers_first 只认 G.TOWERS（没有地洞），而且敌兵还活着就整个跳过。
#   22 分那次：「支援交战·集火残血核心 先知」—— 先知残血往自家基地跑，大法师点名追，追进地洞射程站了 7 秒被射死。
#   现在 hero_tower_guard 对**所有**出令的英雄生效（runtime 下令前统一过一遍）：
#   打的目标 / 要去的点在能打人的敌方建筑射程（+TOWER_CLEAR_PAD）里，或者自己已经站在里面还要打/攻击移动 ->
#   改成走到那座建筑射程外（从建筑往英雄这一侧），不管附近还有没有敌兵。
TOWER_GUARD_FOE_R = 1500.0  # 单位这么近以内有敌方作战单位 = 先打兵
TOWER_GUARD_GROUP = 6       # 敌兵都在塔的射程里时，至少这么多非英雄一起才进去打
GUARD_KEEP = 400.0          # 「先打兵」改打的目标：上一个比最近的远不到这么多就接着打上一个
BACKLINE_OFFSET = 300.0     # 所有英雄在长途行军时跟在非英雄前排后面多远（朝远离目标的方向）
BACKLINE_MARCH = 800.0      # 主力离目标还有这么远才算「行军」；进入接战范围后恢复原攻击意图
REAR_JOIN_R = 4000.0        # 局部交战开打时，离交火点这么近的后排英雄 / 施法支援一起去（见 _step 局部交战分支）
# ★ 09-21 牧师不该冲在前面：它是治疗单位，应留在后排，需要回血的单位退到后面由它治疗。
#   施法支援单位和英雄一样走后排：血最少（285~335，步兵 420）、射程 600 —— 退到前排后 300
#   仍然在治疗/施法射程里，站前面只会先死。四族各挑了「专职治疗/控制、不是主力输出」的那几个。
#   ⚠ 不用「射程 > 400 就算后排」这种自动判据：那会把火枪手（hrif 400）、狮鹫、飞行器一起拖后，
#     它们本来就是靠射程输出的主力。这里要的是**职能**，不是射程。
SUPPORT_CASTERS = frozenset({
    "hmpr", "hsor",          # 人族 牧师（治疗）/ 女巫（减速、隐身）
    "oshm", "odoc",          # 兽族 萨满 / 巫医（治疗守卫）
    "unec", "uban",          # 亡灵 通灵僧 / 女妖
    "edot", "edoc",          # 暗夜 熊德（回春）/ 鸟德（飓风）
})
# ★ 09-18 地图中间已交战很久，左下角两个英雄一直没去支援。实机 120354 局第 176~182 拍：
#   狮鹫/龙鹰飞得快，成了「主力」先飞到敌方分矿；两个英雄走地面落在 3000 多外，敌方 16 个单位的大部队正好卡在中间。
#   英雄往主力那边赶时迎面撞上（毫秒层「英雄预警撤退：身边 1300 内敌 15 个、我方 2 个」往家拉），
#   主力照样打分矿不回头，大脑还给英雄下「去跟主力汇合」的纯移动令 —— 山丘阵亡。
#   三条：① 哪里在交战（敌方作战单位离我方任何一个人 FIGHT_R 内），SUPPORT_R 内的全去支援（攻击移动）；
#         ② 行军时主力不许甩开英雄 HERO_LAG_R 以上；③ 英雄不走「援兵太少太远，先回家攒人」。
FIGHT_R = 1300.0            # 和毫秒层英雄预警 HERO_DANGER_R 同一个数（mod_combat.py），改一边要改另一边
FIGHT_MIN = 4.0             # 交战那团敌人强度至少这么多才调人（零星一两个兵不值得全军转向）
SUPPORT_R = 4500.0          # 离交战点这么近的都去支援
HERO_SUPPORT_R = 8000.0     # 英雄离交战点这么近也去（刚复活的英雄）
HERO_SUPPORT_ESCORT_R = 1600.0  # 英雄参加支援前，至少要有真正能同时接战的非英雄部队在身边
HERO_SUPPORT_ESCORT_MIN = 2     # 1-215：两个英雄不能因为局部战力账面占优，就脱离主力单独接敌
HERO_LAG_R = 1500.0         # 行军时英雄比主力离目标远这么多，主力就放慢等英雄
HERO_WAIT_MAX = 5000.0      # 英雄离主力超过这个（刚复活还在家）就不等
HERO_LEAD = 500.0           # 主力等在英雄前面这么远
COHESION_MIN = 2500.0       # 主力离目标还有这么远才算行军（快到了就不等）
# ★ 09-22 三个小兵面对大批敌军原地不动、被围死（1-304 12:50），没有根据战场实时态势撤退。
#   战斗帧：2 个迫击炮 + 1 个火枪手死在 (-1300,2300)，身边 800 内敌 10~16、自己人 0~2，主力 15 个在 1700~2200 外。
#   大脑那拍判「敌 14 个强度 24，我方 32」—— 32 是 SUPPORT_R（4500）内所有人加起来，近的就地接战、远的「增援接战」一路冲，
#   前面几个要撑 10 秒以上才等得到人。现在按**多久赶得到**分：SUPPORT_ARRIVE_S 内打得到的算「现在就在打」，
#   它们合起来到不了 SUPPORT_MIN_RATIO 就先退回去跟援兵汇合（汇合点在援兵那团往前 SUPPORT_MEET_FRAC），到齐了再一起打。
SUPPORT_ARRIVE_S = 6.0      # 游戏秒：走到射程内要这么久以内的，算「现在就在打」
SUPPORT_MEET_FRAC = 0.35    # 汇合点：援兵主力那团往前面那群挪这么多（离敌人远一点，前面的人退、后面的人迎）
SUPPORT_REGROUP_HYST = 1.25 # 已经在汇合了：前面够线 × 这个才改回就地接战（别一拍打一拍退）
SUPPORT_MIN_RATIO = W.R_GIVEUP  # 和 FLEE_LOCAL_RATIO 同一条线：合起来也到不了 P_GIVEUP 的把握就不去支援。
                            # 轻度劣势仍支援输出；持续明显劣势才整队撤。
                            # （不用 AMAI 的撤退倍数 2~3：推演 W3-Simulate 本来就严重偏乐观）
SUPPORT_FRESH_S = 8.0       # 这么久没派过支援 = 一场新的支援：必须当下就够 SUPPORT_MIN_RATIO（3-037，见 _support）
# ★ 09-18 前期的低级兵到中后期应尽快消耗掉、腾人口换高级单位，不能一直占着人口；消耗方式是让野怪打死，不送给敌军。
#   实测最近长局人口顶到 87~100，三本以后建造表不再要步兵，可 8 个步兵（16 人口）一直占着：
#   人口到 RECYCLE_FOOD、三本、手里有钱 -> 挑低级兵送去打一个打得死它们的野怪营地（不靠近敌方建筑），腾到 RECYCLE_TO。
#   名单由 runtime 发布成 sacrifice.json，毫秒层（mod_combat.load_sacrifice）不拉、不轮换这些兵。守家时照样参与防守（出击层那一拍不跑）。
RECYCLE_FOOD = 88
RECYCLE_TO = 78
RECYCLE_GOLD = 400
RECYCLE_MIN_TIER = 3
RECYCLE_TYPES = {"hfoo", "earc"}    # 人族步兵、暗夜弓箭手。亡灵食尸鬼兼伐木、兽族大G后期照样能打，不喂
RECYCLE_CAMP_MULT = 1.5             # 营地强度（等级和）>= 送去那批的等级和 × 这个，才打得死它们
# ★ 09-22 人口尽量控制在 80 以内；必要时强制攻击自己的低级单位，释放人口。
#   建造规划只把兵出到 G.FOOD_SOFT_CAP（维护费过 80 收入只剩四成）；到了上限、想要的高级兵被「人口软上限」挡住、
#   手里的钱买得起它 -> 挑附近几个自己人用攻击令打死一个低级兵腾地方。一次一个，打死了隔 CULL_GAP_S 再看要不要下一个
#   （骑士 4 人口 = 两个步兵）。比喂野怪（上面 RECYCLE_*）快：不用走去营地、不挑营地强度，在原地几秒解决。
#   被打的那个也进 sacrifice.json（runtime._publish_sacrifice），毫秒层看它掉血不会去救。
CULL_TYPES = RECYCLE_TYPES
# ★★ 09-22 晚 接近 80 人口时反复强制攻击小兵不划算，改为攒钱出高级兵（1-399 强制攻击 19 次）。
#   停用：低级兵少造（tables/human.py 低级兵那段），到了 80 就攒钱，低级兵打没了由高级兵补上。代码留着，改回 True 就恢复。
CULL_ENABLED = False
CULL_MIN_TIER = 2               # 二本以后才换：一本能出的就是这些兵，杀了也换不出更好的
CULL_AT = G.FOOD_SOFT_CAP - 4   # 人口到这里、还有高级兵/英雄/复活被软上限挡着才动手（09-22 到 79 之前就要开始优化人口，77 -> 76）
CULL_SAFE_R = 1500.0            # 被打的和动手的这么近以内有敌人/野怪/敌塔就不内耗
CULL_KILL_R = 1500.0            # 动手的人从它身边这么近以内挑
CULL_KILLERS = 4
CULL_TIMEOUT_S = 25.0           # 这么久还没打死（跑远了、被挡住）就放手，冷却后重挑
CULL_GAP_S = 3.0                # 打死一个后隔这么久再挑下一个（等建造规划把腾出的人口用上）
AIR_ONLY = frozenset({"hgyr", "ehip"})   # 只打空中的（飞行器、角鹰）：派它去打地上的单位打不了
# ★ 09-22 分矿落点有一只小动物，挡得分矿一直建不起来（1-303 7 分）；英雄清完野应优先清掉它。
#   战斗帧：一只浣熊（nrac，15 血）整局在分矿 (-4864,-3200) 东南 300~700 转悠；01:44:53 第一次下开矿令时它离
#   这座矿的历史落点 (-4352,-2624) 只有 120 —— 引擎当场退单，最好的落点被永久拉黑，之后换点被退 77 次、12 分才在离矿 779 的地方盖起来。
#   中立被动的单位不会给地基让路（自己的兵会被挤开），右键它只会跟着走 —— 要攻击令。runtime 设 critters（要开的分矿附近的小动物，
#   守矿野怪清完才有），这里派人去打；runtime._execute 开矿时不去试被它压着的落点（不拉黑）。
CRITTER_HUNT_R = 3000.0     # 离小动物这么近以内的英雄/兵才派
CRITTER_SAFE_R = 900.0      # 这么近以内有敌人/野怪 = 还在打，不拉走
CRITTER_HERO_HP = 0.5       # 英雄血过半才派。附近有英雄但它正忙就等它（设计上由英雄打完野去清），附近一个英雄都没有才派兵
# ★ 09-18 打不过没有撤退：常见一两个兵去打大营地直到阵亡；
#   敌军打二矿时，大法师只带一两个单位就冲上去和近 20 个敌方单位交战，白送经验。
#   实机 120354/124037 局：营地强度 25，全军 20 个从 5800 外攻击移动过去，狮鹫先到，「900 内到了 2 个人」就点名开打；
#   毫秒层判打不过拉回来（12:47~12:49 十几次「在场 1 人 + 赶来 0 人 vs 4 只」），大脑每秒重下打野令又派回去。
#   回防同一个形状：按全图兵力判「守得住」，各自攻击移动过去，跑得快的先到先死。
#   统一成：**到场的强度够了才开打；不够就在交战点外的集合点等人；合起来都不够就不去**。
CREEP_GATHER_R = 1500.0
CREEP_STAGE = 1000.0        # 集合点：营地往我方主力方向退这么远（醒着的野怪不追到这里）
# ★ 09-21：战略层「残血就摘出打野进攻令」这条已经换成按**打光时间**判（见 _creep_engage / winprob.hurt_to_peel）。
#   这个常量留着只为一件事：它是毫秒层 mod_combat.AGGRO_STAY_FRAC 的镜像（「拉出去的残血兵别再送回去接仇恨」），
#   那是另一个决定、有它自己的证据（027 局：回去时血中位 25%，送回后 44% 又被盯上），所以**没有**跟着改。
#   两条线从此不是同一条：战略层管「进不进这一仗」，毫秒层管「拉出去的要不要送回去」。
CREEP_UNIT_HP = 0.40        # = mod_combat.AGGRO_STAY_FRAC（毫秒层那条线的镜像，改一边要改另一边）
CREEP_STAGE_NEAR = 1100.0   # 没到齐时，离营地这么近的先退到集合点（别先惹醒野怪）
CREEP_LOWER_S = 45.0        # 选定营地这么久还没开打：门槛降到 CREEP_ENGAGE_POWER_LATE
CREEP_GIVEUP_S = 90.0       # 这么久还凑不齐就放弃这个营地（CREEP_SKIP_S 内不再选它）
CREEP_SKIP_S = 120.0
POST_CREEP_REST_S = 30.0    # 清营后已有三名以上明显伤兵时，至少给补给/自然回血一拍窗口，不在同一拍串下一个营地
RECOVERY_HP = 0.70
RECOVERY_MIN_UNITS = 3
RECOVERY_MIN_MISSING = 600.0
RECOVERY_MECHANICAL = frozenset({"hgyr", "hmtt", "hrtt", "ocat", "umtw", "ebal", "nzep", "hmtw"})
DEFENSE_FALLBACK_OFFSET = 900.0  # 压倒性劣势时先到大厅背敌一侧，让建筑吃第一轮伤害、部队重新成团
DEFENSE_LAST_STAND_HP = 0.40     # 大厅掉到这条线才放弃集结、全员决战；避免一直让家，同时避免满血整队送入两倍兵力
DEFENSE_REGROUP_S = 18.0         # 069~081 失败局旧集结连续 37~89 游戏秒；只给约两次策略拍，之后必须反打
DEFENSE_REGROUP_R = 750.0
DEFENSE_REGROUP_READY = 0.70     # 70% 战力已到锁定点就不再等最后几个慢兵
DEFENSE_BREACH_R = 900.0         # 敌军压到集结点或接触任一守军时立即接战，不能站在建筑后看着挨打
DEFENSE_COUNTER_MIN_UNITS = 3    # 088/089：第一次反打后旧超时仍生效，新出的 1~2 个兵连续冲出去
# ★ 09-22 1-317 16:50：两个单位在敌方基地里一直打、不撤退，最终阵亡；
#   （21 分）三个英雄相继阵亡，几乎没有微操，血法师技能也没怎么放，站着挨打。
#   16:50：回防的敌人按「敌方军团重心 1500 内」挑，而威胁按距离加权 —— 敌军退出 7000 还算威胁，回防一路追着英雄打
#     （15:55、16:09「回防·集火核心 -> 巫妖 / 恐惧魔王」，16:33 自己记着「威胁 0」还在集火），2 个骑士 + 1 只狮鹫
#     追到 (2000~2600,-2300)，离主力 3500 外被围死。
#   21:00：我方 51.5 对敌方 71~75，回防照样判「守得住」，15 个人连 3 个英雄从主基地排成一串拉 5000 去分矿迎 20 多个亡灵；
#     英雄走在前面先到：圣骑士到场就被穿刺晕住（订单 0x000D0005 = stunned），3 秒 650 -> 112；血魔法师放了烈焰风暴，
#     被睡眠（BUsl）7 秒，附近没有牧师驱散。毫秒层该做的都做了（「打光他们要 50s、他们打光我们要 19s」就是它的预警），
#     是大脑把人送进了这一仗。
DEFENSE_LEASH_R = 2500.0         # 回防只打离我方建筑这么近的敌人：不出去迎、不追出去
EXPANSION_ENGAGED_R = 800.0      # 分矿守不住时，800 内已经有敌方兵的就地打、不往主基地跑（见 Defense.step 分矿那段）
DEFENSE_TOWER_R = 900.0          # 离这场仗这么近的我方塔算进「打不打得过」（塔的射程 700~800，09-23 1-439）
DEFENSE_HERO_TRAIL_R = 1200.0    # 回防行军：非英雄前排离交战点还有这么远，英雄跟在前排后面（不当第一个进场的）
# 守得住守不住、毫秒层叫撤之后多久不硬打：brain/fightcall.py（回防、支援交战、分矿共用一份）
DEFENSE_COUNTER_MIN_STRENGTH = 8.0
# ★ 09-18 晚复盘（s10 370 局）：每局平均 18 拍（3 游戏分钟）只在集合点等人、没开打，77% 是连续 30 游戏秒以上的长等；
#   先到的退回集合点时到场战力比中位 0.6。挑营地按全军算（连刚出兵营、还在家里的兵），于是挑中的营地要等全军到齐。
CREEP_GROUP_R = 2500.0      # 挑营地只算主力重心这么近的人（家里的新兵照样攻击移动去营地，到了就一起打）
CREEP_REPICK_S = 20.0       # 选定这么久还没开打、而营地 CREEP_GROUP_R 内能赶到的人加起来都不够开打门槛 -> 换一个适合眼前这批人的营地
SIGNAL_MAIN_SHARE = 0.5     # 毫秒层叫撤（fight_retreat.json）时，在场的我方人数 >= 全军这么多 = 主力在输，整队撤
FLEE_LOCAL_RATIO = W.R_GIVEUP   # 跌破 P_GIVEUP 的把握就撤（胜率地板 9%，这条线 11% = 基本没戏）。
                            # 轻微劣势不打断输出；局部明显劣势须持续确认，严重劣势仍紧急撤。
FLEE_MY_R = 2500.0          #   < 主力 1500 内敌方（作战单位 + 塔）× 这个 -> 整队撤。AMAI 原版要敌方 > 我方 2~3 倍（进攻性越高越能扛，
                            #   125038 局约 2.7 倍）才撤，对我们等于不撤：那一仗 16 个打到剩 10 个、两个英雄阵亡才撤。和支援的 0.8 同一个口径
# ★ 09-18 对局画面：一群自己的单位站在农民要建二矿的落点上，提示「无法在这里建造」；清完野应该去打别处的野，而不是守在这里。
#   原来「护送开矿」直接攻击移动到落点本身，而且分矿没建好之前护送一直排在打野前面 —— 部队站在落点上、农民放不下地基，也不去打别的野。
ESCORT_THREAT_R = 1800.0    # 分矿这么近有敌方作战单位才去护送；没有就接着打野
ESCORT_STAND = 700.0        # 护送站在离落点这么远（朝威胁那一侧），不站在落点上
SPOT_CLEAR_R = 450.0        # 地基还没出来时，闲着的自己人站在落点这么近就让开
SPOT_CLEAR_TO = 700.0       #   让到离落点这么远
GATHER_BACK = 900.0


@dataclass
class Intent:
    kind: str
    units: list
    x: float = 0.0
    y: float = 0.0
    target: object = None
    why: str = ""
    control: str = ""


def capped_rich(world) -> bool:
    """人口顶满（离 runtime.food_soft_cap 的 80 / 100 不到 CAPPED_FREE）而且存款 >= CAPPED_BANK：再攒兵也出不来，打掉的马上补得回来。"""
    soft = G.FOOD_SOFT_CAP_LATE if getattr(world, "late_food", False) else G.FOOD_SOFT_CAP
    cap = min(world.food_cap, soft)
    return world.food_used >= cap - CAPPED_FREE and world.gold >= CAPPED_BANK


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def centroid(units) -> tuple[float, float]:
    us = list(units)
    if not us:
        return 0.0, 0.0
    return sum(u.x for u in us) / len(us), sum(u.y for u in us) / len(us)


def main_force(units, me: int, levels: dict | None = None, toward=None):
    """主力 = 按 1500 聚团后强度最大的那一团。
    ★ 09-17 实机：全军重心落在两团兵中间的空地上，「附近我方强度 0.0」判撤、「我方 2.0」判撤 —— 判据要按真正站在一起的那团算。
    ★ 同一天第三局：两团强度差不多时「最强」在两团之间来回换，援兵被调过来又调回去（拍 99「援兵先去跟主力汇合 9/11 -> (4233,562)」）。
      有目标时，强度够最强那团 MAIN_FORCE_NEAR 的团里取离目标最近的 —— 前面那团站住了就一直是它。"""
    armies = cluster_armies(units, me, levels)
    if not armies:
        return None
    top = max(armies, key=lambda a: (a.strength, len(a.units)))
    if toward is None:
        return top
    peers = [a for a in armies if a.strength >= MAIN_FORCE_NEAR * top.strength]
    return min(peers, key=lambda a: math.hypot(a.x - toward.x, a.y - toward.y))


def creep_camps(world) -> list[dict]:
    """野怪按 800 聚成营地，强度 = 等级和（AMAI 野怪强度只看等级）。"""
    pool = [c for c in world.creeps if c.hp > 0]
    camps = []
    while pool:
        seed = pool.pop(0)
        members = [seed]
        for c in pool[:]:
            if math.hypot(c.x - seed.x, c.y - seed.y) <= CREEP_CAMP_RADIUS:
                members.append(c)
                pool.remove(c)
        cx, cy = centroid(members)
        camps.append(dict(units=members, x=cx, y=cy, strength=creep_strength(members)))
    return camps


def creep_strength(members) -> float:
    return float(sum(int(G.stat(c.type, "level", 1)) for c in members))


def unit_power(u, levels: dict | None = None) -> tuple[float, float]:
    """(当前血, 每秒伤害)。召唤物照算；英雄的伤害按等级估（HERO_DPS_*）。"""
    hp = max(0.0, float(u.hp))
    if G.is_hero(u.type):
        return hp, HERO_DPS_BASE + HERO_DPS_LVL * int((levels or {}).get(u.addr, 1))
    cool = float(G.stat(u.type, "cool", 0) or 0)
    return hp, (float(G.stat(u.type, "dmgAvg", 0) or 0) / cool if cool > 0 else 0.0)


def power(units, levels: dict | None = None) -> tuple[float, float]:
    """(总血, 总每秒伤害)。"""
    h = d = 0.0
    for u in units:
        if u.hp <= 0:
            continue
        uh, ud = unit_power(u, levels)
        h, d = h + uh, d + ud
    return h, d


def power_ratio(ours, theirs, levels: dict | None = None) -> float:
    """R = (我方总血 × 我方每秒伤害) / (对面总血 × 对面每秒伤害) =「他们打光我们要几秒」/「我们打光他们要几秒」。"""
    ho, do = power(ours, levels)
    hc, dc = power(theirs, levels)
    if hc * dc <= 0:
        return float("inf")
    return ho * do / (hc * dc)


def is_summon(t: str) -> bool:
    """召唤物（水元素、狼、骷髅、树人……）：不占人口、不是英雄、不是建筑。"""
    return G.food(t) == 0 and not G.is_hero(t) and not G.is_building(t)


CREEP_TANK_CONTACT = 250.0  # 打野：肉盾离某只野怪这么近 = 已经贴上、接住仇恨了，远程可以开打
CREEP_BACK_STAND = 500.0    # 打野：肉盾还没贴上时，远程站在离目标这么远（我方主力那一侧）
BACKLINE_RANGE = 250.0      # 射程超过这个的非召唤物算远程（大法师 600、火枪手 400、牧师 600；水元素 300 是召唤物，算肉盾）
CREEP_CORE_DPS_BAND = 0.85  # 最高 DPS 的 85% 内都算核心；同档优先杀有效血较少的，减少过量承伤
CREEP_FINISH_HP = 0.35      # 已经集火到这条血线且仍有最高威胁一半 DPS，就把它收掉，不频繁换目标
CREEP_SURROUND_R = 170.0    # 首次接火时前排绕到目标多个方向；一两个坦克先拉仇恨，其余近战包围
CREEP_HERO_PULL_S = 3.5     # INFI：远程英雄先点一下，下一拍回拉；肉盾接火后英雄立刻恢复输出
CREEP_SUMMON_SETTLE_S = 2.0 # 召唤物贴上后再给一点时间建立仇恨，永久近战不能和它同拍抢着承伤
# ★★ 09-22 打野时单位没被攻击也一直远远躲开，输出不足。应当能打就打，只有被野怪盯上时才躲。
#   最近 20 局对局文件：「召唤物优先承伤」（全队站在外面等水元素）68 段、每段中位 9 游戏秒、最长 24；
#   「远程等肉盾接火」6 段中位 12 —— 而且那条连已经贴着野怪在打、没人打它的兵也拉回外圈。
#   现在：已经有人贴上了就不拉回；没人贴上时最多等 CREEP_WAIT_MAX_S，之后全队上。
#   谁被野怪盯上、血低了由毫秒层按真实仇恨拉（mod_combat AGGRO_*），不靠站远。
CREEP_WAIT_MAX_S = 4.0


def is_backline(t: str) -> bool:
    """打野时不该先接仇恨的：远程/施法（射程 > BACKLINE_RANGE），召唤物除外（水元素就是拿来扛的）。
    和毫秒层 mod_combat.aggro_pulls 的「远程英雄被野怪盯上就拉」同一个口径（射程 > MELEE_RANGE），改一边要改另一边。"""
    return not is_summon(t) and float(G.stat(t, "range", 100) or 100) > BACKLINE_RANGE


def creep_dps(u, levels: dict | None = None) -> float:
    """野怪的持续输出。优先用战力层的双武器/英雄属性口径，读不到时退回 units.json。"""
    try:
        modes = BP.attributes(u, levels or {}).get("modes", ())
        dps = max((float(mode[0]) for mode in modes), default=0.0)
    except Exception:  # noqa: BLE001 - 静态表缺失时仍要能打野
        dps = 0.0
    if dps <= 0:
        cool = float(G.stat(u.type, "cool", 0) or 0)
        dps = float(G.stat(u.type, "dmgAvg", 0) or 0) / cool if cool > 0 else 0.0
    return dps


def creep_focus_target(creeps, levels: dict | None = None, current_id: int | None = None):
    """全队统一打营地里输出最高的核心，兼顾收掉已被打残的当前核心。

    先取最高 DPS 的威胁档，再在同档里挑有效剩余血最少的；这样不会为了最低血小怪放任
    海龟/刺客等高输出核心持续打满，也不会在两个近似核心之间每拍来回换目标。
    """
    alive = [c for c in creeps if c.hp > 0]
    if not alive:
        return None
    threat = {c.addr: creep_dps(c, levels) for c in alive}
    peak = max(threat.values(), default=0.0)
    old = next((c for c in alive if c.addr == current_id), None)
    if old is not None and peak > 0:
        frac = old.hp / max(1.0, old.hp_max)
        if threat[old.addr] >= CREEP_CORE_DPS_BAND * peak or (
                frac <= CREEP_FINISH_HP and threat[old.addr] >= 0.5 * peak):
            return old
    if peak <= 0:
        return min(alive, key=lambda c: (c.hp, c.addr))
    cores = [c for c in alive if threat[c.addr] >= CREEP_CORE_DPS_BAND * peak]

    def effective_hp(c):
        try:
            armor = max(0.0, float(BP.attributes(c, levels or {}).get("armor", 0.0)))
        except Exception:  # noqa: BLE001
            armor = max(0.0, float(G.stat(c.type, "armor", 0) or 0))
        return c.hp * (1.0 + 0.06 * armor)

    return min(cores, key=lambda c: (effective_hp(c), -threat[c.addr], c.addr))


def creep_surround_slots(units, target, origin: tuple[float, float]):
    """把包围兵分到核心野怪四周，并按最近槽位配对，减少互相穿插。"""
    us = list(units)
    if not us:
        return []
    base = math.atan2(origin[1] - target.y, origin[0] - target.x)
    # 先走两翼，再走背面；正面留给已经点名接火的坦克。
    angles = (60, -60, 120, -120, 180, 90, -90, 150, -150)
    slots = []
    for index in range(len(us)):
        a = math.radians(angles[index % len(angles)])
        radius = CREEP_SURROUND_R + 110.0 * (index // len(angles))
        slots.append((target.x + math.cos(base + a) * radius,
                      target.y + math.sin(base + a) * radius))
    out, left = [], slots
    for u in sorted(us, key=lambda v: (G.is_hero(v.type), v.addr)):
        point = min(left, key=lambda p: math.hypot(u.x-p[0], u.y-p[1]))
        left.remove(point)
        out.append((u, point[0], point[1]))
    return out


def recovery_injuries(army) -> list:
    """和 recovery.ArmyRecovery 同一门槛；用于给补给器让出营地清空后的下一拍。"""
    injured = [u for u in army if not G.is_hero(u.type) and u.type not in RECOVERY_MECHANICAL
               and G.food(u.type) > 0 and u.hp > 0 and u.hp_max > 0
               and u.hp < RECOVERY_HP * u.hp_max and u.hp_max - u.hp >= 100]
    missing = sum(u.hp_max-u.hp for u in injured)
    return injured if len(injured) >= RECOVERY_MIN_UNITS and missing >= RECOVERY_MIN_MISSING else []


def group_speed(units) -> float:
    """一起走的速度 = 最慢的那个（召唤物不算，它们是临时的；读不到按 270）。"""
    sp = [float(G.stat(u.type, "spd", 0) or 0) for u in units if u.hp > 0 and not is_summon(u.type)]
    sp = [s for s in sp if s > 0]
    return max(150.0, min(sp)) if sp else 270.0


def segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> tuple[float, float]:
    """点到线段的距离和投影比例。打野路线还没有网格寻路，至少不能用直线把队伍穿过另一个野点。"""
    dx, dy = bx-ax, by-ay
    den = dx*dx + dy*dy
    if den <= 1e-6:
        return math.hypot(px-ax, py-ay), 0.0
    t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / den))
    return math.hypot(px-(ax+t*dx), py-(ay+t*dy)), t


def creep_route_blockers(camp, camps, origin) -> list[dict]:
    """目标路线上会被顺路惊醒的其它营地。目标自己按单位地址排除。"""
    ids = {u.addr for u in camp["units"]}
    out = []
    for other in camps:
        if any(u.addr in ids for u in other["units"]):
            continue
        nearest = min((segment_distance(u.x, u.y, origin[0], origin[1], camp["x"], camp["y"])
                       for u in other["units"]), default=(float("inf"), 0.0))
        # 另一营地只是靠近起点、而目标方向正在远离它时，不把所有出路一并封死。
        # 真已经贴到的误接触由 _creep 里的 CREEP_SECOND_CONTACT 实时撤离处理。
        if nearest[0] < CREEP_ROUTE_AVOID and nearest[1] > 0.05:
            out.append(other)
    return out


# ★★ 09-23 AMAI 的打法特点：基本不开二矿，前期一直暴兵骚扰。针对这种对手调整前中期节奏。
#   15 局对 AMAI：对面一次二矿都没开；兵力 6 分钟持平（6~9 对 5~9），10 分钟 3~11 对 12~17，14 分钟 3~17 对 15~25 ——
#   差距就在我们开二矿、出门打野的 6~10 分钟里拉开，它 10~12 分钟一波压过来（1-420 9~10 分钟去打离二矿 2940 的强度 16 营地、死两个火枪手，
#   12 分钟对面 12 个兵压过来时主力还在西边）。对一矿全押兵的对手，它那一波之前不出远门：营地离我方大厅 > HOLD_WINDOW_R 的不去，
#   在家门口（塔 + 民兵）接它这一波；打退了、我们两矿它一矿，再出门。
HOLD_WINDOW_S = (360.0, 780.0)   # 游戏秒：6 ~ 13 分钟
HOLD_WINDOW_R = 2500.0           # 营地离我方最近的大厅不超过这么远
HOLD_WINDOW_FOE = 0.8            # 对面全局强度 >= 我方 × 这个（对面明显弱了就不用守）
HOLD_WINDOW_MIN_FOE = 6.0        #   而且对面真有一支部队（强度至少这么多；看不见对面 / 对面没兵不算）


def hold_window(world) -> bool:
    """对面恰好一座大厅（一矿全押兵）、在 HOLD_WINDOW_S 里、对面有一支不比我们弱多少的部队 -> 打野不出远门。"""
    return (HOLD_WINDOW_S[0] <= world.t <= HOLD_WINDOW_S[1] and len(world.enemy_halls) == 1
            and world.foe_strength >= max(HOLD_WINDOW_MIN_FOE, HOLD_WINDOW_FOE * world.own_strength))


def creep_plans(world, army, origin, home, skip=(), rate: float = 0.0, why: dict | None = None) -> list[dict]:
    """每个能去的营地估时间，按「营地等级和 / 总时间」从高到低排：
    总时间 = max(走过去, 等兵) + 打 + CREEP_OVERHEAD（边走边等）。
      走过去 = 主力现在的位置到营地的直线 × CREEP_PATH / 一起走的速度；
      等兵 = R 够 CREEP_POWER_MIN 就 0，否则按我方强度涨速 rate（每游戏秒）估：平方律下战力 ∝ 强度²，
             要涨到 sqrt(CREEP_POWER_MIN / R) 倍；涨不动或超过 CREEP_WAIT_MAX 的不考虑；
      打 = 营地总血 / 我方每秒伤害。
    过滤（和原来一样）：skip 里放弃过的、离敌方建筑 ENEMY_BASE_AVOID 内的、离家超过 CREEP_MAX_HOME 的、离对面主基地比离家近的。
    why（给了就填）：每个营地被哪一道筛掉 -> 次数。★ 09-22 1-291：14 分部队在 2 矿干待着，「打野」一个营地都没选，
    可说不出是哪一道筛的 —— 一排零同时表示「没营地」和「营地全被筛掉」，要拆开各记各的。"""
    def drop(reason):
        if why is not None:
            why[reason] = why.get(reason, 0) + 1

    foe_main = enemy_main_hall(world)
    speed = group_speed(army)
    ho, do = power(army, world.levels)
    own = amai.strength(army, world.levels)
    out = []
    camps = creep_camps(world)
    active_skip = [(sx, sy, until) for sx, sy, until in skip if world.t < until]
    if why is not None and not camps:
        drop("地图上没有营地了")
    for camp in camps:
        if any(math.hypot(camp["x"] - sx, camp["y"] - sy) <= CREEP_CAMP_RADIUS for sx, sy, _until in active_skip):
            drop("放弃过还在冷却")
            continue
        # 025 局第一次误入第二营地后虽然没再选原目标，却立刻改走另一条仍会撞上同一危险点的路线。
        # 放弃/误接触过的营地在冷却期内既不能当目标，也不能当作穿越走廊。
        if any((d := segment_distance(sx, sy, origin[0], origin[1], camp["x"], camp["y"]))[0] < CREEP_ROUTE_AVOID
               and d[1] > 0.05 for sx, sy, _until in active_skip):
            drop("路线穿过放弃过的营地")
            continue
        if any(math.hypot(b.x - camp["x"], b.y - camp["y"]) < ENEMY_BASE_AVOID for b in world.enemy_buildings):
            drop("离敌方建筑太近")
            continue
        d_home = math.hypot(camp["x"] - home.x, camp["y"] - home.y)
        if d_home > CREEP_MAX_HOME:
            drop("离家太远")
            continue
        if hold_window(world) and min((math.hypot(camp["x"] - h.x, camp["y"] - h.y) for h in world.my_halls),
                                      default=d_home) > HOLD_WINDOW_R:
            drop("对面一矿暴兵·这段时间不出远门")
            continue
        if foe_main is not None and d_home >= math.hypot(camp["x"] - foe_main.x, camp["y"] - foe_main.y):
            drop("离对面主基地比离家近")
            continue
        # 1-011：队伍去下方强度 8 营地的直线，距上方另一营只有 361。
        # 四个兵被上方留住，其余人又开了下方营地，最终 8 个单位阵亡。
        # 没有可靠绕行网格时不选这种目标；挡路的野点若能打，它自己会成为候选。
        if creep_route_blockers(camp, camps, origin):
            drop("路上有别的营地挡着")
            continue
        hc, dc = power(camp["units"])
        r = ho * do / (hc * dc) if hc * dc > 0 else float("inf")
        if r >= CREEP_POWER_MIN:
            wait = 0.0
        elif r > 0 and rate > 1e-3 and own > 0:
            wait = (math.sqrt(CREEP_POWER_MIN / r) - 1.0) * own / rate
        else:
            wait = float("inf")
        if wait > CREEP_WAIT_MAX:
            drop("打不过且等兵太久")
            continue
        loss = camp_loss(camp)
        if loss >= CREEP_LOSS_SKIP and world.tier < 2:
            drop("一本打高损营地")
            continue                                     # 一本的部队打这种营地每回合要死 1.5 个以上（土著营地 3.61），升二本前不去
        travel = math.hypot(camp["x"] - origin[0], camp["y"] - origin[1]) * CREEP_PATH / speed
        fight = hc / max(1.0, do)
        total = max(travel, wait) + fight + CREEP_OVERHEAD + loss * CREEP_LOSS_S
        out.append(dict(camp=camp, r=r, travel=travel, wait=wait, fight=fight, total=total, loss=loss,
                        score=camp["strength"] / total))
    out.sort(key=lambda p: -p["score"])
    return out


# ★ 09-18 晚打野阵亡分析（6~11 号 358 局、1971 个打野阵亡）：55.7% 死在同一种营地 —— 土著营地（ntkc 酋长光环 + 2 个 ntkh 治疗 +
#   ntks 心灵之火 + nitt 诱捕，等级和 21），每回合死 3.61 个、94% 的回合至少死一个；大脑的战力比（总血 × 每秒伤害）不算治疗/光环/诱捕，
#   判「轻松营地（全军战力比 >= 4）」的回合照样每回合死 3.43 个 —— 公式对它没有预测力。改用实测：营地组成 -> 每回合阵亡
#   （brain/data/creep_camp_losses.json，一次性分析脚本 make_loss_table.py 从战斗帧回合生成；数据是一本为主的早期部队）。
CREEP_LOSS_SKIP = 1.5       # 每回合预期阵亡 >= 这个的营地，一本时期不选
CREEP_LOSS_S = 20.0         # 每个预期阵亡折成多花这么多游戏秒（一个步兵 135 金 ≈ 一座矿 20 多秒的收入）
CREEP_LOSS_SPAWN = {"uske", "nspd", "nsgt"}   # 召唤出来的骷髅/小蜘蛛不算营地组成
_LOSS_TABLE: dict | None = None


def camp_loss(camp) -> float:
    """这个营地每回合预期死几个兵（实测表里没有的按 0）。"""
    global _LOSS_TABLE
    if _LOSS_TABLE is None:
        try:
            import json
            import os
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "creep_camp_losses.json"), encoding="utf-8") as fh:
                _LOSS_TABLE = {k: float(v.get("deaths_per_round", 0.0)) for k, v in (json.load(fh).get("camps") or {}).items()}
        except (OSError, ValueError, AttributeError):
            _LOSS_TABLE = {}
    key = "+".join(sorted({c.type for c in camp.get("units") or [] if c.type not in CREEP_LOSS_SPAWN}))
    return _LOSS_TABLE.get(key, 0.0)


def pick_creep_camp(world, home, skip=(), army=None, origin=None, rate: float = 0.0) -> dict | None:
    """creep_plans 里分最高的营地。army 默认全军、origin 默认家（测试和旧调用方用）。"""
    army = army if army is not None else world.my_army + world.my_heroes
    origin = origin if origin is not None else (home.x, home.y)
    plans = creep_plans(world, army, origin, home, skip, rate)
    return plans[0]["camp"] if plans else None


def enemy_main_hall(world):
    """对面主基地 = 周围 1500 内对方建筑最多的大厅。"""
    best, n_best = None, -1
    for h in world.enemy_halls:
        n = sum(1 for b in world.enemy_buildings if abs(b.x - h.x) < 1500 and abs(b.y - h.y) < 1500)
        if n > n_best:
            best, n_best = h, n
    return best


def choose_target(world, from_pt, army=None, skip=(), reserve: float = 1.0,
                  relaxed: bool = False) -> tuple[object | None, str, offense.Assessment | None]:
    """ChooseAttackTarget：先分矿，再主基地；发起、集结、续攻共享同一种守军评估。
    relaxed（09-23，兵满钱多 / 僵持破局，见 AttackSequence.stale_since）：门槛 CAPPED_MARGIN、打主基地不再叠 ATTACK_MAIN_RESERVE。"""
    margin = CAPPED_MARGIN if relaxed else ATTACK_MARGIN
    main = enemy_main_hall(world)
    cands = [(h, "分矿", amai.EXPANSION_BONUS) for h in enemy_expansions(world)]
    if main is not None:
        cands.append((main, "主基地", 0))
    army = world.my_army + world.my_heroes if army is None else army
    own, foe = world.own_strength, world.foe_strength
    last = (None, "", None)
    for h, kind, bonus in sorted(cands, key=lambda c: (-c[2], dist(c[0], from_pt))):
        if h.addr in skip:
            continue
        ev = offense.assess(world, army, from_pt, h, margin, bonus)
        target_reserve = 1.0 if relaxed else ATTACK_MAIN_RESERVE if kind == "主基地" else ATTACK_EXPANSION_RESERVE
        if ev.good and ev.rhs >= ev.lhs * max(reserve, target_reserve):
            return h, kind, ev
        last = (None, kind, ev)
    # ★ 09-17 实机 5 号：拍 137 对面主基地拆掉、一座大厅都不剩，这里返回空 -> 「打完了，回家」-> 去打野，
    #   对面兵营/农场/塔一座没拆、兵力 0 也没赢（拆了一半就停下去练级）。
    #   没有大厅、或者对面兵力打没了（<= 我方 1/4）才轮到零散建筑：按 1200 聚团，离得近的先拆。
    if not world.enemy_halls or foe <= own * BUILDINGS_WHEN_FOE_LE:
        for c in sorted(building_clusters(world), key=lambda c: math.hypot(c["x"] - from_pt.x, c["y"] - from_pt.y)):
            b = min(c["units"], key=lambda u: math.hypot(u.x - c["x"], u.y - c["y"]))
            if b.addr in skip:
                continue
            ev = offense.assess(world, army, from_pt, b, margin)
            if ev.good and ev.rhs >= ev.lhs * reserve:
                return b, "建筑", ev
            last = (None, "建筑", ev)
    return last


def enemy_expansions(world) -> list:
    """敌方的分矿：不是主基地的大厅 + 敌方占着的金矿（亡灵闹鬼金矿 ugol、暗夜缠绕金矿 egol）。"""
    main = enemy_main_hall(world)
    out = [h for h in world.enemy_halls if h is not main]
    for b in world.enemy_buildings:
        if b.type in ("ugol", "egol") and all(dist(b, h) > 600 for h in out) and (main is None or dist(b, main) > 1600):
            out.append(b)
    return out


def raid_target(world, home, own: float, army=None, origin=None, max_home: float | None = None):
    """家门口且援军来不及救的敌方分矿 -> 去拆。返回 (目标, 有效守军强度) 或 None。

    ★ 09-18 敌方在我家附近开了三矿，英雄从正在开矿的对手面前经过也不去打。
    实测那一局：敌方三矿离我主基地 5916、3 座建筑 3000 血、身边 0 个守军，我方 23 个兵在家里，
    而出击闸看的是**对面全图兵力**（62.8 > 我方 50 × 1.25），于是永远不出门。
    拆分矿主要和**那座分矿附近及能及时赶到的守军**比 —— 它的主力在地图另一头时才抓机会；
    若敌军在进军路线旁或能比我们更快抵达，则不把它误判成空矿。
    同时这也是我们唯一的开矿机会：地图上的空矿被它占完了（那一局「开矿·地图上没有能开的矿」×62）。"""
    best = None
    army = list(army or [])
    has_siege = any(u.type in G.SIEGE for u in (army or world.my_army))
    origin = origin or home
    # max_home：默认只看「家门口」的分矿；空档那条路（_idle_plan）放宽到 IDLE_RAID_MAX_HOME ——
    # 09-23 空档期应去清剩下的营地或攻打敌方其它矿点。当地守军 / 援军 / 塔的算法一个字不改，放宽的只是距离。
    for h in enemy_expansions(world):
        d = dist(h, home)
        if d > (RAID_HOME_R if max_home is None else max_home):
            continue
        local = amai.strength([f for f in world.enemy_fighters if dist(f, h) <= RAID_LOCAL_R], world.levels)
        towers = amai.strength([t for t in world.enemy_towers if dist(t, h) <= RAID_LOCAL_R], world.levels)
        need = (local + (1.0 if has_siege else RAID_TOWER_MULT) * towers) * RAID_MARGIN
        response = 0.0
        if army:
            ev = offense.assess(world, army, origin, h, RAID_MARGIN, amai.EXPANSION_BONUS)
            # assess 会按双方抵达时间折算远处援军，并把进军走廊上的敌军全额计入。
            # 保留这里较宽松的塔权重，但绝不能忽略 response / route。
            response = ev.response
            need = max(need, (local + response) * RAID_MARGIN
                       + (1.0 if has_siege else RAID_TOWER_MULT) * towers)
        if own >= max(need, 1.0) and (best is None or d < best[2]):
            best = (h, local + response + towers, d)
    return (best[0], best[1]) if best else None


def building_clusters(world, radius: float = 1200.0) -> list[dict]:
    """对面不是大厅的建筑按 1200 聚团。"""
    pool = [b for b in world.enemy_buildings if b.type not in G.HALLS]
    out = []
    while pool:
        seed = pool.pop(0)
        members = [seed]
        for b in pool[:]:
            if math.hypot(b.x - seed.x, b.y - seed.y) <= radius:
                members.append(b)
                pool.remove(b)
        cx, cy = centroid(members)
        out.append(dict(units=members, x=cx, y=cy))
    return out


def local_strengths(world, x: float, y: float, radius: float = amai.ARMY_RADIUS) -> tuple[float, float]:
    """RETREAT_CONTROL：以主力为中心 1500 内 我方强度 vs 敌方（作战单位 + 塔）强度。"""
    mine = [u for u in world.my_army + world.my_heroes if math.hypot(u.x - x, u.y - y) <= radius]
    foes = [u for u in world.enemy_fighters + world.enemy_towers if math.hypot(u.x - x, u.y - y) <= radius]
    return amai.strength(mine, world.levels), amai.strength(foes, world.levels)


@dataclass
class AttackSequence:
    """出击循环。state: home / creep / gather / attack / retreat。每次 step(world) 返回意图列表。"""
    profile: dict
    state: str = "home"
    target: object = None
    target_kind: str = ""
    start_strength: float = 0.0
    search_left: int = 0
    last_x: float = 0.0
    last_y: float = 0.0
    camp: dict | None = None
    heroes: HeroPlanner = field(default_factory=HeroPlanner)   # 英雄去留的评分表（brain/heroplan.py）
    camp_ids: set = field(default_factory=set)
    foe_hist: list = field(default_factory=list)   # [(游戏秒, 对面兵力强度)]：runtime 每拍 note_foe（守家时出击逻辑不跑，也要记）
    gather_at: float = -1e9
    gather_xy: tuple | None = None       # 锁定的前沿集结点；目标不变就不随每拍主力重心漂移
    guard_target: int | None = None      # _tower_guard 上一次改打的那个敌兵（还活着、没远太多就接着打它）
    retreat_at: float = -1e9
    safe: bool = False              # 这一步对面兵力 <= 我方 1/4
    allow_attack: bool = True       # 控制台开关（brain/config.py enable_attack / enable_creep）
    allow_creep: bool = True
    clear_target: dict | None = None   # 运行时设：开矿被分矿旁边的野怪挡住了，{ids, x, y, strength}
    terrain: object = None             # 运行时设：brain/terrain.Terrain（Echo Isles 地形表）；None = 集合点按老办法算
    escort: tuple | None = None        # 运行时设：正在开的分矿位置，主力去护送
    build_spot: tuple | None = None    # 运行时设：开矿令下了、地基还没出来的落点（闲着的人别站在上面）
    ground: list = field(default_factory=list)   # 运行时设：mod_scavenge 发布的地上物品 [(x, y), ...]
    loot_until: float = -1e9
    home_xy: tuple | None = None
    hero_ids: set = field(default_factory=set)
    militia_ids: set = field(default_factory=set)  # 只接纳分矿任务点名的民兵；普通守家民兵不跟主力出门
    own_hist: list = field(default_factory=list)   # [(游戏秒, 我方强度)]，每 5 游戏秒一个，留 GROWTH_WINDOW + 60 秒（算等兵）
    fight_lost: bool = False           # 这一步 _support 判了「有交战、合起来也打不过」
    sacrifice: set = field(default_factory=set)   # 喂野怪腾人口的兵（地址），见 RECYCLE_*
    food_blocked: list = field(default_factory=list)   # 运行时设：这一轮被「人口软上限」挡下的兵（按优先级），见 CULL_*
    cull: dict | None = None           # 正在强制攻击的低级兵 {victim, type, food, want, t, killers}
    cull_done_at: float = -1e9
    critters: list = field(default_factory=list)       # 运行时设：要开的分矿附近的小动物，见 CRITTER_*
    critter_hunter: int | None = None
    creep_since: float = -1e9          # 选定这个营地的游戏秒
    creep_engaged: bool = False        # 这个营地点名开打过没有
    camp_hp_seen: tuple | None = None  # (游戏秒, 当时营地剩余总血)：打野有没有进展，见 CREEP_NO_PROGRESS_S
    creep_focus_id: int | None = None  # 当前营地统一集火的高输出核心；死亡/换营地才切换
    creep_pull_at: float = -1e9        # 英雄先手拉怪的开始游戏秒
    creep_pull_id: int | None = None
    creep_summon_tank_at: float = -1e9 # 本营地第一次确认召唤物已经贴上野怪
    creep_wait_at: float = -1e9        # 本营地开打后第一次「在外面等肉盾 / 召唤物接火」的游戏秒（CREEP_WAIT_MAX_S）
    incoming: list = field(default_factory=list)   # 09-22 晚 runtime 每拍给：电脑队长来袭预报（perception.incoming，还没到必须回家的）
    idle_now: bool = False                         #   这一拍大部队在空档（_idle_plan）：骚扰按占优放行（harass.step idle）
    finishing: bool = False                        # 09-22 晚 runtime 每拍给：收尾拆家（对面没什么兵了）—— 不等治疗
    stale_since: float | None = None               # 09-23 在家干站从什么时候开始（出击判不够那一拍记；有营地 / 出门就清）—— 僵持破局
    relaxed: bool = False                          # 09-23 这一趟是按放宽门槛出的门（兵满钱多 / 僵持破局）：途中复评也按放宽的算
    skip_camps: list = field(default_factory=list)   # [(x, y, 到期游戏秒)] 凑不齐人放弃过的营地
    retreat_req: dict | None = None    # 运行时设：毫秒层叫撤信号（fight_retreat.json）{x, y, n_me, n_foe, why}
    held: set = field(default_factory=set)         # 运行时设：毫秒层正在救的我方单位（hurt.json）—— 暂时不在 world 里，但人还在
    held_heroes: int = 0                           # 其中英雄几个
    held_units: list = field(default_factory=list) # 保留位置用于任务收队；不向微操接管者下令
    engagement: Engagement = field(default_factory=Engagement)
    skirmish: Skirmish = field(default_factory=Skirmish)
    support_ids: set = field(default_factory=set)
    support_seen: float = -1e9
    support_sent_at: float = -1e9      # 上一次真的派人支援交战的时刻（SUPPORT_FRESH_S 内算「已经在打」）
    support_regroup_at: float = -1e9   # 上一次「前面的人撑不到援兵、先退回去汇合」的时刻（SUPPORT_REGROUP_HYST）
    lost_at: tuple | None = None       # 运行时设：毫秒层最近一次叫撤 (游戏秒, x, y)，fightcall.recently_lost 用
    loss_since: dict = field(default_factory=dict)
    creep_ready_at: float = 0.0
    creep_regroup_at: float = -1.0
    creep_withdraw: bool = False
    creep_stage_key: tuple = ()
    creep_stage_xy: tuple | None = None
    creep_withdraw_xy: tuple | None = None  # 放弃营地时只退到附近安全点，脱离后就在当地重新规划
    sac_camp: dict | None = None
    tick_reasons: dict = field(default_factory=dict)
    loot_x: float = 0.0
    loot_y: float = 0.0
    counts: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    assessment: dict = field(default_factory=dict)
    skip_targets: dict = field(default_factory=dict)   # 集结失败/走不动的目标 -> 再试游戏秒
    now: float = 0.0
    progress_at: float = 0.0
    progress_dist: float = float("inf")
    progress_hp: float = float("inf")
    progress_foe: float = float("inf")   # 上一拍敌方总强度（_combat_progress）
    progress_blds: int = 1 << 30         # 上一拍主力 STALL_NEAR_R 内的敌方建筑数
    hero_wait_at: float = -1.0
    creep_rest_until: float = -1e9
    creep_rest_xy: tuple | None = None

    def note_foe(self, t: float, foe: float) -> None:
        """runtime.job_tactics 每拍记一笔对面兵力强度（守家时 step 不跑，打赢那一仗的峰值也得记下来）。"""
        self.foe_hist = [(a, f) for a, f in self.foe_hist if t - a <= COUNTER_WINDOW_S] + [(t, float(foe))]

    def counter_window(self, t: float, foe: float) -> bool:
        """刚打赢一仗：COUNTER_WINDOW_S 内对面强度峰值 >= 现在 × COUNTER_DROP。"""
        peak = max((f for a, f in self.foe_hist if t - a <= COUNTER_WINDOW_S), default=0.0)
        return peak > 0 and peak >= foe * COUNTER_DROP

    def _count(self, k: str) -> None:
        self.counts[k] = self.counts.get(k, 0) + 1
        self.tick_reasons[k] = self.tick_reasons.get(k, 0) + 1   # 这一拍（10 游戏秒）里出过哪些理由，run_brain 每拍取走清零

    def _note(self, s: str) -> None:
        self.notes.append(s)

    def _persistent_loss(self, key, now, own, foe, ratio):
        if foe <= 0 or own >= foe * ratio:
            self.loss_since.pop(key, None)
            return False
        if own < foe * .35:
            return True
        since = self.loss_since.setdefault(key, now)
        return now - since >= 6.0

    def _foes_near(self, world, army) -> list:
        """主力重心 FIGHT_R 内的敌方作战单位 + 塔。

        「残血怕不怕」要按**眼前这一仗**判，不能按全图。没有敌人在场时返回空 ——
        调用方见到空就不摘人（没仗可打，血量不是问题；回血/补给另有路子管）。"""
        if not army:
            return []
        cx, cy = centroid(army)
        return [f for f in world.enemy_fighters + world.enemy_towers
                if f.hp > 0 and math.hypot(f.x - cx, f.y - cy) <= FIGHT_R]

    def step(self, world, home, busy_defending: bool = False) -> list[Intent]:
        self.now = world.t
        self.idle_now = False                 # 这一拍走到空档安排（_idle_plan）才置真；runtime 据此让骚扰按占优放行
        self.skip_targets = {a: t for a, t in self.skip_targets.items() if t > world.t}
        if busy_defending:
            self.interrupt()
            return []
        mission_militia = [u for u in world.my_militia if u.addr in self.militia_ids]
        army = world.my_army + world.my_heroes + mission_militia
        self.hero_ids = {h.addr for h in world.my_heroes}
        if not self.own_hist or world.t - self.own_hist[-1][0] >= 5.0:
            self.own_hist = [p for p in self.own_hist if world.t - p[0] <= GROWTH_WINDOW + 60.0] + [(world.t, world.own_strength)]
        pre: list[Intent] = self._recycle(world, home) if home is not None else []
        if self.sacrifice:
            army = [u for u in army if u.addr not in self.sacrifice]
        if home is not None:
            active = self.state in ("attack", "gather", "creep")
            # ★ 09-21：英雄的去留改走加权评分表（brain/heroplan.py），不再是三段 if 串行剔除。
            #   原来那三段的判据全部翻译成了规则权重；唯一的行为改变是「一律回家」拆成 home / withdraw
            #   —— 败局 home 状态占 57%、胜局 33%，而人族部队站在主城附近并不回血。
            # ★ 09-22 打敌方二矿时只有小兵在打，英雄要么回了家、要么停在地图中间（1-296 13 分）。
            #   血魔法师 66%~68% 一路被「血不满，进攻中先恢复」推回来（脱离 ×3 -> 残血先回家），没跟上部队；
            #   没英雄的部队到了对面 2 矿局部判「敌强我弱」，13:18 整体回家。那条规则本意是「出发前先恢复」，
            #   出击已经打起来以后只剩副作用（1-287 68% 回家也是它）—— 只在集结阶段给。
            #   真被人打、在掉血，毫秒层按「谁在打它」拉开；半血以下 heroplan 的残血那一档照旧。
            for d in self.heroes.plan(world, home, active=active,
                                      offensive=self.state == "gather", micro_held=self.held):
                if d.action not in (HP.WITHDRAW, HP.HOME):
                    continue
                army = [u for u in army if id(u) != id(d.hero)]
                why = "英雄残血先回家" if d.action == HP.HOME else "英雄脱离到附近安全点"
                self._count(why)
                # 得分走 note（heroes.notes 里已经有换动作的那条），不塞进 why：
                # why 是意图的语义标签，日志和下令去重都认它。
                pre = pre + [Intent("move", [d.hero], d.x, d.y, why=why)]
            if self.state in ("attack", "gather"):
                # ★★ 09-21「残血不怕」：判据从「血量低于 35%」换成「撑不撑得到我们把对面打光」。
                #   实测 13852 个「单位 × 场」：满血 57% 死、40% 血 62% 死（只差 5 个点），
                #   而同样是 40% 血，打得过的仗里 44%、打不过的仗里 73%（差 29 个点）。
                #   决定一个兵死不死的是**这一仗打不打得过**，不是它自己还有多少血。
                #   ⚠ 但一次摘走超过 PEEL_MAX 就不摘了 —— 那不是「个别人残血」，是整队该撤，
                #     一个个摘走正是添油：账上 10 个人committed，真正在打的只剩 7 个。
                foes_near = self._foes_near(world, army)
                low, whole = W.hurt_to_peel(army, foes_near, world.levels, self.hero_ids)
                if low and whole:
                    self._count("出击·伤员过半·整队撤")
                    t_us, t_them = W.kill_times(army, foes_near, world.levels)
                    self._note(f"整队撤：{len(low)}/{len(army)} 人撑不住"
                               f"（我们打光他们要 {t_us:.0f} 秒，他们打光我们要 {t_them:.0f} 秒）")
                    self.state, self.target, self.retreat_at = "retreat", None, world.t
                    return self._backline(pre + [Intent("move", army, home.x, home.y,
                                                        why="伤员过半，整队撤回")])
                if low:
                    army = [u for u in army if u not in low]
                    self._count("出击·伤员单独回家")
                    pre += [Intent("move", low, home.x, home.y, why="出击伤员单独回家")]
        if home is not None:
            # 强制攻击低级兵腾人口、清分矿落点上的小动物：占用的人这一拍不再跟主力的意图
            cull_its, taken = self._cull(world, army)
            hunt = self._critter_hunt(world, [u for u in army if u.addr not in taken])
            taken = taken | {u.addr for it in hunt for u in it.units}
            if taken:
                army = [u for u in army if u.addr not in taken]
                pre = pre + cull_its + hunt
        ids = {u.addr for u in army}
        world = replace(world, my_army=[u for u in world.my_army + mission_militia if u.addr in ids],
                        my_heroes=[u for u in world.my_heroes if u.addr in ids])
        return self._tower_guard(world, self._backline(pre + self._step(world, home, army, busy_defending)))

    def _tower_guard(self, world, intents: list) -> list:
        """附近还有敌兵时先打兵、不顶着能打人的建筑打（见 ARMED_BUILDINGS 上面那段）。只改「攻击 / 攻击移动」意图里的非英雄。"""
        live = [f for f in world.enemy_fighters if f.hp > 0]
        if not live or not intents:
            return intents
        armed = [b for b in world.enemy_buildings if b.hp > 0 and b.type in ARMED_BUILDINGS]

        def under(p):
            return next((b for b in armed
                         if math.hypot(b.x - p.x, b.y - p.y) <= float(G.stat(b.type, "range", 800) or 800) + TOWER_CLEAR_PAD), None)

        out, redirect = [], []
        for it in intents:
            if it.kind not in ("attack", "attack_move"):
                out.append(it)
                continue
            on_building = it.target is not None and G.is_building(getattr(it.target, "type", ""))
            keep = []
            for u in it.units:
                if (u.addr not in self.hero_ids and (on_building or under(u) is not None)
                        and any(math.hypot(f.x - u.x, f.y - u.y) <= TOWER_GUARD_FOE_R for f in live)):
                    redirect.append(u)
                else:
                    keep.append(u)
            if keep:
                out.append(replace(it, units=keep) if len(keep) != len(it.units) else it)
        if not redirect:
            return intents
        rx, ry = centroid(redirect)
        safe = [f for f in live if under(f) is None]
        pool = safe or live
        target = min(pool, key=lambda f: math.hypot(f.x - rx, f.y - ry))
        # 09-22：每拍都取「离这群人重心最近的敌兵」，最近的那个一两秒换一个（「先打兵 -> 先打兵」换目标 119 次 / 6 局）。
        #   上一次改打的那个还活着、没比最近的远出 GUARD_KEEP 就接着打它
        prev = next((f for f in pool if f.addr == self.guard_target), None)
        if prev is not None and (math.hypot(prev.x - rx, prev.y - ry)
                                 <= math.hypot(target.x - rx, target.y - ry) + GUARD_KEEP):
            target = prev
        self.guard_target = target.addr
        crowd = sum(1 for u in world.my_army if math.hypot(u.x - target.x, u.y - target.y) <= TOWER_GUARD_FOE_R)
        if safe or crowd >= TOWER_GUARD_GROUP:
            self._count("先打兵·不打建筑" + ("" if safe else "·敌兵在塔下但人够"))
            out.append(Intent("attack", redirect, target.x, target.y, target,
                              why=f"先打兵：附近还有敌兵，别打建筑、别顶着塔（{len(redirect)} 个）"))
            return out
        # 敌兵全缩在塔下、人又不够：在射程里的先退出来，不在射程里的（原来点名打建筑的）去射程外等
        self._count("先打兵·敌兵都在塔下·退出射程")
        for u in redirect:
            b = under(u)
            if b is None:
                out.append(Intent("move", [u], u.x, u.y, why="敌兵都在塔下，射程外等"))
                continue
            reach = float(G.stat(b.type, "range", 800) or 800) + TOWER_CLEAR_PAD + 100.0
            d = math.hypot(u.x - b.x, u.y - b.y) or 1.0
            out.append(Intent("move", [u], b.x + (u.x - b.x) / d * reach, b.y + (u.y - b.y) / d * reach,
                              why="敌兵都在塔下、人不够，先退出塔的射程"))
        return out

    def interrupt(self) -> None:
        """防守接管时结束旧任务，解除之后按当前局面重新选。"""
        self.state, self.target, self.camp_ids = "home", None, set()
        self.camp, self.retreat_req = None, None
        self.creep_withdraw, self.creep_regroup_at = False, -1.0
        self.gather_xy, self.creep_withdraw_xy = None, None
        self.loss_since.clear()
        self.skirmish.reset()
        self.hero_wait_at = -1.0
        self.creep_rest_until = -1e9
        self.creep_rest_xy = None
        self.creep_pull_at, self.creep_pull_id = -1e9, None
        self.creep_summon_tank_at = -1e9

    def _assess(self, world, army, origin):
        margin = ATTACK_MARGIN
        capped = capped_rich(world)
        if (capped or self.relaxed) and margin > CAPPED_MARGIN:
            # ★ 09-23 1-416 / 1-418：人口顶满、存款几千，兵 16 个在家站了十几分钟 —— 这时候打掉的兵马上补得回来，
            #   按「兵力要优势 1.25 倍」等下去只会让钱越堆越多。塔、路线、援军照旧全算（offense.assess 的地板那条也在）。
            #   relaxed = 这一趟是按放宽门槛出的门（僵持破局），途中复评同一条线，不然一出门就被按 1.73 倍叫回来
            margin = CAPPED_MARGIN
            self._count("出击·兵满钱多·门槛降到 1.0" if capped else "出击·僵持破局·途中按放宽门槛复评")
        ev = offense.assess(world, army, origin, self.target, margin,
                            amai.EXPANSION_BONUS if self.target_kind in ("分矿", "骚扰分矿") else 0.0)
        self.assessment = dict(ev.live(), targetAddr=self.target.addr, kind=self.target_kind, t=world.t)
        return ev

    def _backline(self, intents: list) -> list:
        """★ 09-18 大法师带头冲进敌方基地，挨打也不迂回，一直在前面充当肉盾。
        实机：行军是整队攻击移动到同一个点，大法师移速 320、步兵 270，于是它总是第一个到敌人面前；
        毫秒层按「被集火」拉开它三次，大脑下一拍又把它和部队一起派回前线。
        行军时（主力离目标 > BACKLINE_MARCH）把所有英雄拆出来，攻击移动到「非英雄前排重心往回退
        BACKLINE_OFFSET」的点。英雄跟着部队走、遇到敌人照样打，但不会成为第一个接火的单位。"""
        out = []
        for it in intents:
            # ★ 09-18 二：只拆「打敌方玩家」的行军（目标是敌方玩家的单位/建筑）。打野、清分矿野怪、护送开矿不拆 ——
            #   上一版全拆，实机第 4 局大法师被派到「主力重心后 300」、重心被掉队的兵拖在后面，第 60 拍仗在 (-7021,-6905)，
            #   它在 (-2261,-5427)；野怪睡着时攻击移动不会主动打，大法师整场不出手（英雄看戏埋点第 22/25/48/59 拍都抓到）
            tgt_owner = getattr(it.target, "owner", None)
            if it.kind != "attack_move" or not it.units or tgt_owner is None or tgt_owner >= 12:
                out.append(it)
                continue
            # 英雄存活优先级最高。长距离行军时所有英雄都走在非英雄前排之后；
            # 到目标 BACKLINE_MARCH 内仍会按原攻击意图接战，不会让近战英雄全程看戏。
            # ★ 09-21 牧师这类施法支援单位和英雄一起走后排（见 SUPPORT_CASTERS 的理由）
            def _rear(u):
                return u.addr in self.hero_ids or u.type in SUPPORT_CASTERS
            back = [u for u in it.units if _rear(u)]
            front = [u for u in it.units if not _rear(u)]
            if not back or len(front) < 2:
                out.append(it)
                continue
            fx = sum(u.x for u in front) / len(front)
            fy = sum(u.y for u in front) / len(front)
            vx, vy = fx - it.x, fy - it.y
            n = math.hypot(vx, vy)
            if n <= BACKLINE_MARCH:
                out.append(it)
                continue
            bx, by = fx + vx / n * BACKLINE_OFFSET, fy + vy / n * BACKLINE_OFFSET
            casters = sum(1 for u in back if u.addr not in self.hero_ids)
            self._count("行军·英雄跟在非英雄前排后面")
            if casters:
                self._count(f"行军·施法支援跟在后面·{casters}个")
            out.append(Intent(it.kind, front, it.x, it.y, it.target, it.why, it.control))
            out.append(Intent("attack_move", back, bx, by, None,
                              f"{it.why}·英雄{'和施法支援' if casters else ''}跟在主力后面", it.control))
        return out

    def _step(self, world, home, army, busy_defending: bool) -> list[Intent]:
        self.home_xy = (home.x, home.y) if home is not None else None
        if self.state != "home" or self.camp_ids:
            self.stale_since = None                    # 出门了 / 在打野：不算干站
        if self.state == "home":
            self.relaxed = False
        if not army and home is not None and self.held:
            # ★ 09-18：能打的人全被毫秒层拉出去保命了（打野整队撤退）—— 状态和营地都留着，等他们回来接着打，别清掉重选
            self._count("全员被毫秒层拉着·等")
            return []
        if not army or home is None:
            self.state = "home"
            self.camp_ids = set()
            return []
        if busy_defending:
            self.interrupt()
            return []
        if not self.allow_attack and self.state in ("attack", "gather"):
            self.state, self.target, self.retreat_at = "retreat", None, world.t
        if not self.allow_attack:
            self.skirmish.reset()
        if not self.allow_creep and self.camp_ids:
            if self.camp and not self.creep_withdraw:
                return self._leave_camp(world, army, "停止打野，先收回旧营地部队")
            if not self.creep_withdraw:
                self.camp_ids = set()
        own = world.own_strength
        self.safe = world.foe_strength <= own * BUILDINGS_WHEN_FOE_LE
        mf = main_force(army, world.me, world.levels, self.target if self.state in ("attack", "gather") else None)
        cx, cy = (mf.x, mf.y) if mf is not None else centroid(army)
        if self.state == "attack":
            # 放在局部交战分支之前：1-288 那一分钟的「支援交战」全是这个分支下的令，它在出击分支之前就返回了
            self._combat_progress(world, cx, cy)

        # 野外交战由同一状态机决定进退；返回后不再叠加旧的整队撤退门槛。
        before = self.skirmish.phase
        duel = self.skirmish.step(world, army, home) if self.allow_attack and not self.creep_withdraw else None
        if duel is not None:
            self.retreat_req = None
            mode = duel['mode']
            report = self.skirmish.report
            self._count('局部交战·'+mode)
            if before != self.skirmish.phase or mode == 'disengaged':
                self._note(f"局部交战 {before}->{mode}：我方战力 {report.get('ownPower',0)}、敌方 {report.get('enemyPower',0)}，"
                           f"比值 {report.get('ratio',0)}，含后续援军比 {report.get('reserveRatio',0)}")
            if mode == 'disengaged':
                if self.camp_ids and self.camp:
                    return self._leave_camp(world,army,'拉开接触，先收齐旧营地部队')
                self.state,self.target,self.retreat_at='retreat',None,world.t
                return [Intent('move',army,home.x,home.y,why='脱离接触，回队重整',control='skirmish')]
            if mode == 'withdraw':
                return [Intent('move',duel['crew'],*duel['point'],why='敌强我弱，分段后撤并复评追兵',control='skirmish')]
            why = '回头反打追兵' if mode == 'counter' else '支援交战·局部战力允许持续输出'
            crew = list(duel['crew'])
            # ★★ 09-22 已经开战、其它单位都在交火，英雄仍停在地图中间不去支援（1-394 23:30）。
            #   行军时 _backline 让英雄 / 施法支援跟在主力后面，开打时它们离前沿 2700~2900；小队只圈离锚点 1600 内的人，
            #   它们拿不到任何令、原地站着（大脑自己 18 秒后报「Hamg 卡了 31 游戏秒没挪窝（离目标 2883）」）。
            #   REAR_JOIN_R 内的后排一起交给 battle_intents（够不着的走「增援接战」到前沿）；战力估算照旧只算前面的小队。
            ids = {u.addr for u in crew}
            px, py = duel['point']
            rear = [u for u in army if u.addr not in ids and (u.addr in self.hero_ids or u.type in SUPPORT_CASTERS)
                    and math.hypot(u.x - px, u.y - py) <= REAR_JOIN_R]
            if rear:
                self._count('局部交战·后排跟上')
                crew += rear
            return [replace(it,control='skirmish') for it in battle_intents(
                self.engagement,crew,duel['foes'],world.t,*duel['point'],why,world.levels)]

        if self.state == "attack":
            my_local, foe_local = local_strengths(world, cx, cy)
            if amai.should_flee(foe_local, my_local, world.aggression):
                self._count("出击·撤")
                self._note(f"撤：主力附近敌方强度 {foe_local:.1f} > 我方 {my_local:.1f} × {amai.flee_mult(world.aggression, True):.2f}")
                self.state, self.target, self.retreat_at = "retreat", None, world.t
                return [Intent("move", army, home.x, home.y, why="打不过，整队回家")]
            my_near = amai.strength([u for u in army if math.hypot(u.x - cx, u.y - cy) <= FLEE_MY_R], world.levels)
            if self._persistent_loss("attack", world.t, my_near, foe_local, FLEE_LOCAL_RATIO):
                self._count("出击·当地打不过撤")
                self._note(f"撤：主力 {int(FLEE_MY_R)} 内我方 {my_near:.1f} < 附近敌方 {foe_local:.1f} × {FLEE_LOCAL_RATIO}")
                self.state, self.target, self.retreat_at = "retreat", None, world.t
                return [Intent("move", army, home.x, home.y, why="当地打不过，整队回家")]
            sig = self._take_signal(army, cx, cy)
            if sig is not None:
                return self._signal_retreat(world, army, home, sig)
            sup = self._support(world, army, home)
            if sup is not None:
                return sup
            if self.fight_lost:
                return self._fight_retreat(world, army, home)
            tow = self._towers_first(world, army, cx, cy)
            if tow is not None:
                return tow
            if self._alive(world, self.target):
                self.target = next(u for u in world.enemies if u.addr == self.target.addr)
                ev = self._assess(world, army, _Pt(cx, cy))
                if dist(self.target, _Pt(cx, cy)) > 2000 and ev.lhs > ev.rhs / 0.9:
                    self._count("出击·援军封路撤")
                    self.state, self.target, self.retreat_at = "retreat", None, world.t
                    return [Intent("move", army, home.x, home.y, why="目标援军已到，回家重整")]
                route = self._route_camp(world, army, cx, cy, self.target)
                if route is not None:
                    return route
                return self._push(army, cx, cy, self.target.x, self.target.y, self.target, f"打{self.target_kind}")
            # 目标没了：原地 1500 找人接着打（SleepInCombatAM）
            near = [u for u in world.enemies if math.hypot(u.x - self.last_x, u.y - self.last_y) <= SEARCH_RADIUS]
            if near and self.search_left > 0:
                self.search_left -= 1
                t = min(near, key=lambda u: math.hypot(u.x - cx, u.y - cy))
                self._count("出击·附近接着打")
                return self._push(army, cx, cy, t.x, t.y, t, "目标没了，附近接着打")
            if own >= self.start_strength * CHAIN_LEFT or world.foe_strength <= own * BUILDINGS_WHEN_FOE_LE:
                tgt, kind, ev = choose_target(world, _Pt(cx, cy), army, self.skip_targets, relaxed=self.relaxed)
                if tgt is not None:
                    self._count("出击·接下一个目标")
                    self._note(f"接着打{kind}：兵力还剩出发时 {own / max(0.01, self.start_strength):.0%}")
                    self._set_target(tgt, kind, (cx, cy))
                    return self._push(army, cx, cy, tgt.x, tgt.y, tgt, f"接着打{kind}")
            self._count("出击·收兵")
            self.state, self.target, self.retreat_at = "retreat", None, world.t
            return [Intent("move", army, home.x, home.y, why="打完了，回家")]

        if self.state in ("gather", "creep"):
            sig = self._take_signal(army, cx, cy)
            if sig is not None:
                return self._signal_retreat(world, army, home, sig)
        else:
            self.retreat_req = None
        if self.state != "retreat":
            sup = self._support(world, army, home)
            if sup is not None:
                return sup
            # ★ 09-22：「home」原来不在这里 —— 判了打不过没人撤，落到下面的「集结」普通移动令（见 _fight_retreat 文档）
            if self.fight_lost and self.state in ("gather", "creep", "home"):
                return self._fight_retreat(world, army, home)

        if self.state == "gather":
            current = next((u for u in world.enemies if u.addr == getattr(self.target, "addr", None)), None)
            if current is not None:
                self.target = current
                ev = self._assess(world, army, _Pt(cx, cy))
            if current is not None and ev.lhs <= ev.rhs / 0.9:
                return self._gather(world, army, mf, cx, cy, own)
            self._count("集结·目标变得不好")
            if current is not None:
                self.skip_targets[current.addr] = world.t + 30.0
            self.state, self.target = "home", None

        if self.state == "retreat":
            if self.creep_withdraw:
                old_foes = [c for c in world.creeps if c.addr in self.camp_ids and c.hp > 0]
                active_stranded = [u for u in army if u.hp > 0
                                   and any(dist(u, c) <= 700 for c in old_foes)]
                held_stranded = [u for u in self.held_units if u.hp > 0
                                 and any(dist(u, c) <= 700 for c in old_foes)]
                # 能由本层下令的人仍在旧营地，就继续把整队往安全点带。held_units 已由毫秒
                # 安全层接管，本层既不能命令它，又曾把它算进屏障，导致 1-207 连续一分钟
                # “等旧营地脱离”。给安全层一个短窗口；只剩 held 后到期立即重选附近任务。
                wait_held = held_stranded and world.t - self.retreat_at < CREEP_WITHDRAW_MAX_S
                if active_stranded or wait_held:
                    self._count("打野撤离·等旧营地脱离")
                    x, y = self.creep_withdraw_xy or self._local_creep_escape(world, army)
                    return [Intent("move", army, x, y, why="打野撤离，旧营地还有人，先到附近安全点收队")]
                if held_stranded:
                    self._note(f"旧营地仅剩 {len(held_stranded)} 名安全层接管伤员，撤离屏障到期，先让可用主力继续规划")
                # 已经脱离旧营地，就在当前位置重选附近机会；不再为了清状态横穿地图回主基地。
                self.creep_withdraw, self.camp_ids = False, set()
                self.creep_withdraw_xy = None
                self.state = "home"
            elif math.hypot(cx - home.x, cy - home.y) <= 1500:
                self.state = "home"
            else:
                return [Intent("move", army, home.x, home.y, why="回家路上", control="hold")]   # 撤到底：补攻击令别在路上拽回去

        if world.t < self.creep_rest_until:
            self.state = "creep" if self.camp_ids else "home"
            self._count("打野·伤兵多先休整")
            rx, ry = self.creep_rest_xy or (cx, cy)
            far = [u for u in army if math.hypot(u.x-rx, u.y-ry) > 700]
            why = ("打野后伤兵多，向下一营地安全点前移集结" if self.camp_ids
                   else "打野后伤兵多，在当前区域安全点整队补给")
            return ([Intent("move", far, rx, ry, why=why)]
                    if far else [])
        self.creep_rest_xy = None
        # home / creep：够强且到时机就出击（AMAI：英雄 1 经验太低或够高、二英雄、人口 > 40~60）
        heroes_ready = all(h.hp >= HERO_READY_HP * h.hp_max for h in world.my_heroes if h.hp_max > 0)
        recovering = [h for h in world.my_heroes if h.hp_max > 0 and h.hp < HERO_READY_HP * h.hp_max]
        if self.finishing and not heroes_ready:
            # 09-22 晚 收尾拆家（runtime._finishing：对面兵力 < 我方 FINISH_FOE_FRAC）：英雄不回家等血，跟着一起上
            self._count("收尾拆家·英雄不等回血")
            heroes_ready, recovering = True, []
        # ★ 09-21「残血不怕」：出击兵力原来把血 < 35% 的整个剔除，22 局每局 22.6 次判「有效兵力不足」。
        #   但 amai.unit_strength **本来就乘了血量比例** —— 再剔一次等于同一个惩罚收两遍。
        #   现在只剔掉低于绝对地板（KEEP_FLOOR_HP）的：那一档死亡率 84%~95%，确实不该带出门。
        ready = [u for u in army if u not in recovering
                 and (u.hp_max <= 0 or u.hp >= W.KEEP_FLOOR_HP * u.hp_max)]
        ready_str = amai.strength(ready, world.levels)
        enough = ready_str >= self.profile.get("min_attack_strength", 4)
        # 英雄缺席时至少凑出 8 强度的健康部队，不能靠恢复中的英雄充当出击兵力。
        enough = enough and (any(u.addr in self.hero_ids for u in ready) or ready_str >= 8)
        creep_fighting = self.creep_engaged and any(c.addr in self.camp_ids for c in world.creeps)
        if self.allow_attack and not enough:
            self._count("出击·有效兵力不足")
        elif self.allow_attack and creep_fighting:
            self._count("出击·先打完当前野怪")
        if self.allow_attack and enough and not creep_fighting and not self.camp_ids:
            if world.t - self.retreat_at < RETREAT_COOLDOWN_S:
                self._count("出击·撤回来冷却中")
            else:
                front = main_force(ready, world.me, world.levels)
                origin = _Pt(front.x, front.y) if front is not None else home
                # ★★ 09-23 全部单位待在家里长时间不行动（1-427 18 分钟起），多局反复出现。
                #   打主基地要 ATTACK_MARGIN 1.38 × ATTACK_MAIN_RESERVE 1.26 ≈ 对面整支部队的 1.73 倍；AMAI 一矿龟缩、全军守家，
                #   两边都顶满人口时永远到不了（1-427：全局 73 对 59，出击要 81.8 对 59），野怪打光、骚扰没目标、三矿只剩对面半边 ->
                #   只剩「前沿驻守」。兵满钱多（capped_rich）或者在家干站 STALE_S 以上、全局强度 >= 对面 STALE_RATIO 倍：
                #   门槛降到 CAPPED_MARGIN、不叠主基地余量（打输了按 AMAI 的 2 倍线才撤，离家远读回城）。
                #   ⚠ 09-22 那版「兵满钱多」写在 _assess 里，而发起出击走的是 choose_target —— 一次都没生效（计数 0）
                stale = (self.stale_since is not None and world.t - self.stale_since >= STALE_S
                         and world.own_strength >= STALE_RATIO * max(0.1, world.foe_strength))
                relaxed = capped_rich(world) or stale
                tgt, kind, ev = choose_target(world, origin, ready, self.skip_targets, ATTACK_LAUNCH_RESERVE, relaxed=relaxed)
                if tgt is not None and relaxed:
                    self.relaxed = True
                    self._count("出击·僵持破局" if stale else "出击·兵满钱多·门槛降到 1.0")
                if tgt is None and self.stale_since is None:
                    self.stale_since = world.t
                near_exp = tgt is not None and kind == "分矿" and dist(tgt, home) <= RAID_HOME_R and not self.camp_ids
                if ev is not None:
                    self.assessment = dict(ev.live(), targetAddr=getattr(tgt, "addr", None), kind=kind, t=world.t)
                if tgt is not None and (near_exp or self._timing_ok(world)):
                    if recovering:
                        self._count("出击·健康主力先走·英雄单独恢复")
                        if self.counter_window(world.t, world.foe_strength):
                            self._count("出击·刚打赢一仗·不等英雄回血")
                    self._count("出击·拆家门口的分矿" if near_exp else f"出击·目标好·{kind}")
                    self._note(f"{'拆家门口的分矿' if near_exp else '出击打' + kind}：有效兵力 {ev.rhs:.1f} / 所需 {ev.lhs:.1f}，当地 {ev.local:.1f}，援军 {ev.response:.1f}，塔 {ev.tower:.1f}")
                    self._set_target(tgt, kind, (origin.x, origin.y))
                    self.state, self.gather_at, self.camp_ids = "gather", world.t, set()
                    resting = [u for u in army if u not in ready]
                    recall = [Intent("move", resting, home.x, home.y, why="伤员单独恢复，健康主力出击")] if resting else []
                    return recall + self._gather(world, ready, front, origin.x, origin.y, ready_str)
                # 全局兵力让正规总攻评估不通过，并不等于敌方分矿安全。若分矿当地守军/塔
                # 明显弱于健康主力，就直接打一波；对方大部队接近后由局部战力状态机撤退。
                raid = raid_target(world, home, ready_str, ready, origin)
                if raid is not None:
                    rt, local = raid
                    self._count("出击·低损骚扰分矿")
                    self._note(f"低损骚扰分矿：健康战力 {ready_str:.1f} / 当地守军塔 {local:.1f}；敌方增援接近即撤")
                    self._set_target(rt, "骚扰分矿", (origin.x, origin.y))
                    self.state, self.start_strength, self.camp_ids = "attack", ready_str, set()
                    resting = [u for u in army if u not in ready]
                    recall = [Intent("move", resting, home.x, home.y, why="伤员留后恢复，健康主力骚扰分矿")] if resting else []
                    return recall + self._push(ready, origin.x, origin.y, rt.x, rt.y, rt, "低损骚扰分矿")
                if tgt is None:
                    self._count("出击·目标不好")
                else:
                    self._count("出击·开局时机未到")
        chain = False
        if not heroes_ready and not self.camp_ids and self.allow_creep and world.my_heroes:
            chain = self._easy_camp_near(world, army, mf, home)
            if chain:
                self._count("打野·英雄没满血·就近接着打")
        if not heroes_ready and not self.camp_ids and not chain:
            self._count("英雄没回血·先不出门")
            self.state = "home"
            # 只让未恢复英雄回基地；健康部队在当前主力位置成团，避免整军无意义往返。
            healthy = [u for u in army if u not in recovering]
            out = ([Intent("move", recovering, home.x, home.y, why="英雄没回血，单独回家恢复")]
                   if recovering else [])
            if healthy:
                anchor = self.creep_rest_xy or ((mf.x, mf.y) if mf is not None else centroid(healthy))
                self.creep_rest_xy = anchor
                far = [u for u in healthy if math.hypot(u.x-anchor[0], u.y-anchor[1]) > 900]
                if far:
                    out.append(Intent("move", far, *anchor, why="英雄恢复期间，健康部队留在前沿成团"))
            return out
        # 英雄恢复期间，健康部队若也没有可打的机会，保留原来的就近打野/恢复逻辑。
        if not heroes_ready and self.allow_attack:
            self._count("出击·英雄没回血")
        if self.escort is not None and not self.camp_ids:
            ex, ey = self.escort
            threats = [e for e in world.enemy_fighters if e.hp > 0 and math.hypot(e.x - ex, e.y - ey) <= ESCORT_THREAT_R]
            if threats:
                tx, ty = centroid(threats)
                d = math.hypot(tx - ex, ty - ey) or 1.0
                self.state = "home"
                self._count("护送开矿")
                return [Intent("attack_move", army, ex + (tx - ex) / d * ESCORT_STAND, ey + (ty - ey) / d * ESCORT_STAND,
                               why=f"护送开矿（分矿 {int(ESCORT_THREAT_R)} 内敌 {len(threats)} 个）")]
            self._count("护送开矿·没威胁接着打野")
        creep = self._creep(world, army, home, mf, own, only_ready=chain)
        if creep is not None:
            return creep
        self.state = "home"
        clear = self._clear_spot(army)
        if clear:
            return clear
        # 09-22 晚：空档安排（_idle_plan）。它给了去处就去（非空）；[] = 已经在前沿守着，不再叫回家集结（不然前沿 <-> 家来回走），
        #   下面照旧收掉队的。None（没有城）才走原来的回家集结。
        idle = self._idle_plan(world, army, mf)
        if idle:
            return idle
        # control=hold（租约 58 > 毫秒层补攻击令 55）：1-304 14:05 掉在东边的三个收到回家令的同一秒，被补攻击令派回去打 13 个人
        if idle is None and math.hypot(cx - home.x, cy - home.y) > 1500:
            return [Intent("move", army, home.x, home.y, why="集结", control="hold")]
        # ★ 09-21 几个残血小兵不回家、一直停在野外，敌军大部队到来后又上去接战阵亡。
        #   1-247 局 1311~1391 秒（**80 游戏秒**）一条令都没有：主力重心回到家里（< 1500），
        #   这一行就整个 army 都不下令，而 1304 秒「脱离接触，回队重整：6/6 个」里掉队的那 4 个
        #   还站在地图另一头，直到 1392 秒敌军过来。
        #   判据在量**重心**，而问题是**个体** —— 重心回家了不等于每个人都回家了。
        stray = [u for u in army if math.hypot(u.x - cx, u.y - cy) > STRAGGLER_R]
        if stray:
            self._count("掉队归队")
            return [Intent("move", stray, cx, cy, why=f"掉队的 {len(stray)} 个兵归队", control="hold")]
        return []

    def _idle_plan(self, world, army, mf) -> list[Intent] | None:
        """★★ 09-22 晚 打完野后长时间空闲（1-409 12 分）。空档期应提前部署后续行动，并随局势变化随时调整。
        22:44:41~22:45:15 大部队 12~13 个每拍都是「出击·目标不好」（对面强度 44~51 对我方 32~39）+「打野·没有可选营地」
        （我们这半边清完了，剩下两营一个挨着敌方分矿、一个在对面家后面）—— 走到这里原来就 return []，一条令都不下，直到对面打上门。
        空档按顺序找一件有用的事（每拍重判，局势变了下一拍就换）：
          ① 电脑队长的目标是我方某座城（runtime 给的 incoming，还没到必须回家）-> 先到那座城朝来路的前沿站好（塔、民兵都在旁边）；
          ② 打野筛掉的营地里，在我们这半边或中间的（IDLE_CAMP_SIDE / IDLE_CAMP_MAX_HOME）、我方不比对面弱（IDLE_CAMP_AHEAD）、
             看得见的敌方军团都离它 IDLE_CAMP_ARMY_CLEAR 以外、主力对它战力比 >= IDLE_CAMP_RATIO -> 去清（1-411 起不去对面那一侧）；
          ③ 都没有：到离敌人最近的那座我方大厅朝敌方方向的前沿驻守（有防御塔护着），不在后方干站。
        已经站在前沿点 IDLE_KEEP_R 内就不重下（返回 [] 表示「就在这儿守着」，计数照记）。
        出击和打野都关了（allow_attack / allow_creep）就不安排（和「向下一目标前沿集结」同一个开关口径）；抢营地要 allow_creep。"""
        if not army or not (self.allow_attack or self.allow_creep):
            return None
        self.idle_now = True
        ax, ay = (mf.x, mf.y) if mf is not None else centroid(army)
        for inc in sorted(self.incoming, key=lambda i: i["dist"]):
            town = inc["town"]
            spot = self._front_spot(world, town)
            self._count("空闲·电脑来袭·提前到前沿")
            name = getattr(getattr(town, "hall", None), "type", "我方城")
            return self._hold_at(army, ax, ay, spot, f"电脑队长要打 {name}：提前到它朝来路的前沿站好")
        hp_us, dps_us = power(army, world.levels)
        best = None
        foe_main = enemy_main_hall(world)
        skipped = [(sx, sy) for sx, sy, until in self.skip_camps if until > world.t]
        # ★★ 09-22 晚 1-411 15:30 大法师阵亡：23:05:09 这一条把整支部队（14 个）派去清对面家后面、离家 1 万多的营地（强度 17）——
        #   「看得见的敌方军团都在 3000 外」只是那一刻，两秒后撞上对面主力 16 个、敌强我弱后撤，大法师被睡眠定住、单独留下被围死。
        #   现在要：我方不比对面弱（own >= foe × IDLE_CAMP_AHEAD）；营地在我们这半边或中间（离我方最近大厅 <= 离对面最近大厅 × IDLE_CAMP_SIDE，
        #   且 <= IDLE_CAMP_MAX_HOME）。对面那一侧的留给出击 / 骚扰去处理。
        mine_halls = world.my_halls
        foe_halls = world.enemy_halls
        camps_ok = self.allow_creep and world.own_strength >= IDLE_CAMP_AHEAD * max(0.1, world.foe_strength)
        if self.allow_creep and not camps_ok:
            self._count("空闲·对面比我们强·不去抢营地")
        for camp in (creep_camps(world) if camps_ok else []):
            d_ours = min((math.hypot(h.x - camp["x"], h.y - camp["y"]) for h in mine_halls), default=1e9)
            d_theirs = min((math.hypot(h.x - camp["x"], h.y - camp["y"]) for h in foe_halls), default=1e9)
            if d_ours > IDLE_CAMP_MAX_HOME or d_ours > IDLE_CAMP_SIDE * d_theirs:
                continue
            if any(math.hypot(a.x - camp["x"], a.y - camp["y"]) <= IDLE_CAMP_ARMY_CLEAR for a in world.enemy_armies):
                continue
            if foe_main is not None and math.hypot(foe_main.x - camp["x"], foe_main.y - camp["y"]) <= ENEMY_BASE_AVOID + 500:
                continue                                  # 对面主基地门口的不去（分矿边上的可以）
            if any(math.hypot(sx - camp["x"], sy - camp["y"]) <= CREEP_CAMP_RADIUS for sx, sy in skipped):
                continue                                  # 打野那边放弃过的（凑不齐人 / 打不动）照样不去
            if any(math.hypot(t.x - camp["x"], t.y - camp["y"]) <= float(G.stat(t.type, "range", 700) or 700) + 400
                   for t in world.enemy_towers):
                continue
            hp_c, dps_c = power(camp["units"], world.levels)
            if (hp_us * dps_us) / max(1e-6, hp_c * dps_c) < IDLE_CAMP_RATIO:
                continue
            d = math.hypot(camp["x"] - ax, camp["y"] - ay)
            if best is None or d < best[1]:
                best = (camp, d)
        if best is not None:
            camp = best[0]
            core = creep_focus_target(camp["units"], world.levels, self.creep_focus_id)
            self._count("空闲·清中间剩下的营地")
            return [Intent("attack", army, core.x, core.y, core,
                           why=f"没营地可打、我方不弱、对面主力不在附近：去清这半边 / 中间剩下的营地（强度 {camp['strength']:.0f}）")]
        if not world.my_halls:
            return None
        # ★★ 09-23 空档期要清剩下的营地或攻打敌方其它矿点（1-438 对面 3 座矿，我们 14 个兵站在自家门口）。
        #   野点上面那一段已经在做；这里补「对面别的矿」：还是 raid_target 那一套（当地守军 + 赶得到的援军 + 塔），
        #   只是不再限定「离我家 RAID_HOME_R 以内」。打不过照旧不去。
        home_hall = min(world.my_halls, key=lambda h: math.hypot(h.x - ax, h.y - ay))
        # ⚠ 刚撤回来的冷却期内不许再冲出去（test_retreat_cooldown：加这条之前它当场把撤退冷却绕过去了）
        if self.allow_attack and not self.camp_ids and world.t - self.retreat_at >= RETREAT_COOLDOWN_S:
            raid = raid_target(world, home_hall, amai.strength(army, world.levels), army,
                               _Pt(ax, ay), max_home=IDLE_RAID_MAX_HOME)
            if raid is not None:
                rt, local = raid
                self._count("空闲·去拆对面的分矿")
                self._note(f"空档去拆对面的分矿：我方 {amai.strength(army, world.levels):.1f} / 当地守军塔 {local:.1f}"
                           f"（离我家 {dist(rt, home_hall):.0f}）")
                self._set_target(rt, "骚扰分矿", (ax, ay))
                self.state, self.start_strength, self.progress_at = "attack", amai.strength(army, world.levels), world.t
                return self._push(army, ax, ay, rt.x, rt.y, rt, "空档去拆对面的分矿")
        foe = enemy_main_hall(world)
        fx, fy = (foe.x, foe.y) if foe is not None else IDLE_FRONT_DEFAULT
        town = min(world.my_halls, key=lambda h: math.hypot(h.x - fx, h.y - fy))
        self._count("空闲·前沿驻守")
        return self._hold_at(army, ax, ay, self._front_spot(world, town), f"没仗可打：到 {town.type} 朝敌方的前沿驻守")

    def _front_spot(self, world, town) -> tuple[float, float]:
        """一座城朝敌方主基地（看不见就朝地图中心）方向 IDLE_FRONT_R 的地面点。"""
        foe = enemy_main_hall(world)
        fx, fy = (foe.x, foe.y) if foe is not None else IDLE_FRONT_DEFAULT
        d = math.hypot(fx - town.x, fy - town.y) or 1.0
        return self._ground(town.x + (fx - town.x) / d * IDLE_FRONT_R, town.y + (fy - town.y) / d * IDLE_FRONT_R,
                            (town.x, town.y))

    def _hold_at(self, army, ax, ay, spot, why) -> list[Intent]:
        if math.hypot(ax - spot[0], ay - spot[1]) <= IDLE_KEEP_R:
            self._count("空闲·已经在位")
            return []
        return [Intent("move", army, spot[0], spot[1], why=why, control="hold")]

    def _towers_first(self, world, army, cx, cy) -> list[Intent] | None:
        """★★ 09-21 交战中大量单位死于敌方瞭望塔等攻击型建筑。
        设计要求：附近有攻击型建筑时，等敌方有伤害的单位全部清完就全体集火它，
        被打的残血单位撤出它的攻击范围。

        实测（120 个战斗文件、368 场打玩家的仗）：塔在场的只有 19 场（5%），
        但**其中 9 场整场一下都没打过塔**（47%）—— 频次不高，缺口是真的：
        出击的目标只在大厅/分矿/建筑团里选，塔从来不是目标，部队就站在塔下打别的。

        两句话：
          * 附近**会动、会打人**的敌人清光了，而塔还在打我们 -> 全体集火最近那座塔；
          * 同时把血量掉到 SOFT_HP 以下的人挪出塔的射程（塔不会追，走出去就安全）。
        """
        towers = [t for t in world.enemy_towers if t.hp > 0
                  and math.hypot(t.x - cx, t.y - cy) <= TOWER_FOCUS_R]
        if not towers:
            return None
        live = [f for f in world.enemy_fighters if f.hp > 0
                and math.hypot(f.x - cx, f.y - cy) <= TOWER_FOCUS_R]
        if live:
            self._count("塔·还有活的敌兵·先打兵")
            return None
        tgt = min(towers, key=lambda t: math.hypot(t.x - cx, t.y - cy))
        reach = float(G.stat(tgt.type, "range", 800) or 800) + TOWER_CLEAR_PAD
        hurt = [u for u in army if u.addr not in self.hero_ids and u.hp_max > 0
                and u.hp < W.SOFT_HP * u.hp_max and dist(u, tgt) <= reach]
        # ★ 09-21 攻打敌方建筑时，箭塔一直打英雄，英雄残血仍顶着不撤；而英雄打塔伤害很低，
        #   不应让英雄当坦克承受塔的伤害。
        #   实机（5 号 21:15 那局）山丘在塔下 20 秒 649 -> 1/850：毫秒层拉了 5 次，这里又 5 次把它派回去打塔 ——
        #   上面那行挑「撤出射程」的人时把英雄排除了，英雄于是永远在「集火塔」那一组。
        #   英雄打塔本来就不划算（英雄攻击打城甲只有一半），塔交给兵，英雄一律站到射程外。
        heroes_in = [u for u in army if u.addr in self.hero_ids and dist(u, tgt) <= reach]
        rest = [u for u in army if u not in hurt and u not in heroes_in and u.addr not in self.hero_ids]
        out = []
        if heroes_in:
            self._count("塔·英雄不打塔·退出射程")
        if hurt or heroes_in:
            if hurt:
                self._count(f"塔·残血撤出射程·{len(hurt)}个")
            for u in hurt + heroes_in:
                d = math.hypot(u.x - tgt.x, u.y - tgt.y) or 1.0
                out.append(Intent("move", [u], tgt.x + (u.x - tgt.x) / d * reach,
                                  tgt.y + (u.y - tgt.y) / d * reach,
                                  why="英雄不打塔，退出塔的射程" if u.addr in self.hero_ids else "残血退出塔的射程"))
        if not rest:
            return out
        self._count("塔·集火")
        self._note(f"敌兵已清空，全体集火 {tgt.type}（{len(towers)} 座塔，撤出 {len(hurt)} 个残血）")
        return out + [Intent("attack", rest, tgt.x, tgt.y, tgt, why="敌兵清空，集火攻击型建筑")]

    def _clear_spot(self, army) -> list[Intent]:
        """闲着的人站在还没出地基的分矿落点 SPOT_CLEAR_R 内 -> 往外让到 SPOT_CLEAR_TO（地基放不下「无法在这里建造」）。"""
        if self.build_spot is None:
            return []
        bx, by = self.build_spot
        on = [u for u in army if math.hypot(u.x - bx, u.y - by) <= SPOT_CLEAR_R]
        if not on:
            return []
        cx, cy = centroid(on)
        d = math.hypot(cx - bx, cy - by)
        ux, uy = ((cx - bx) / d, (cy - by) / d) if d >= 1.0 else (0.0, -1.0)
        self._count("让开分矿落点")
        return [Intent("move", on, bx + ux * SPOT_CLEAR_TO, by + uy * SPOT_CLEAR_TO, why="让开分矿落点")]

    def _easy_camp_near(self, world, army, mf, home) -> bool:
        """有没有 CHAIN_TRAVEL_S 游戏秒走得到、不用等兵（按当前血量 R >= CREEP_POWER_MIN）的营地。"""
        origin = (mf.x, mf.y) if mf is not None else centroid(army)
        return any(p["wait"] == 0 and p["travel"] <= CHAIN_TRAVEL_S
                   for p in creep_plans(world, army, origin, home, self.skip_camps, 0.0))

    def _creep(self, world, army, home, mf, own, only_ready: bool = False) -> list[Intent] | None:
        # ★ 09-18 5-031 局：打下面最近的营地时只有两个小兵在协助英雄，其余全部回了家。
        #   14:28:30 毫秒层打野整队撤退把英雄也拉走 -> 英雄进了 hurt.json、大脑这边 world.my_heroes 暂时是空的 ->
        #   这里清掉营地、其余的兵没事干被「集结」派回家；英雄回来又重新选营地（选了上面那个）。英雄只是被拉着，不是没了：营地留着。
        if (not world.my_heroes and not self.hero_ids and not self.held_heroes) or not self.allow_creep:
            if self.camp_ids and self.camp and self.creep_engaged:
                return self._leave_camp(world, army, "打野任务终止，先收回旧营地部队")
            self.camp_ids = set()
            return None
        if self.camp_ids:
            alive = [c for c in world.creeps if c.addr in self.camp_ids and c.hp > 0]
            # ★ 09-21 3-007 13 分钟左右两个英雄卡在左下角，在移动和停止之间来回切换。
            #   实机 713~914 秒（**201 游戏秒**）反复下同一条「打野开打…：N/8 个 -> (-6667,-3705)」，
            #   同期让路日志「Hmkg 卡了 42 游戏秒没挪窝（离目标 658）」—— 目标够不着（地形/悬崖挡着），
            #   而营地血一点没掉。
            #   为什么原来不会放弃：唯一的放弃闸 CREEP_ABORT_POWER 按**战力比**判，而那一刻战力比是 99.0
            #   （上限），看起来碾压 —— 它回答的是「打不打得过」，这里的问题是「够不够得着」。
            #   判据必须换成**进展**：血有没有在掉（「能用条件就别用时长」这条原则的正面用法）。
            if self.creep_engaged and alive:
                hp_now = sum(c.hp for c in alive)
                seen = self.camp_hp_seen
                if seen is None or hp_now < seen[1] - CREEP_PROGRESS_HP:
                    self.camp_hp_seen = (world.t, hp_now)
                elif world.t - seen[0] >= CREEP_NO_PROGRESS_S:
                    self._count("打野·够不着·放弃营地")
                    self._note(f"打野放弃：{world.t - seen[0]:.0f} 游戏秒里营地总血一点没掉"
                               f"（还剩 {hp_now:.0f}，我方 {len(army)} 个）—— 多半够不着，换一个营地")
                    self.camp_hp_seen = None
                    return self._leave_camp(world, army, "打野打不到（血一直没掉），换一个营地")
            # 受挤碰/追击/临时绕路影响仍可能误入第二个野点。
            # 把第二营地也纳入“必须脱离”名单，立即整队撤退，不再把两场战斗分别评估。
            # ★ 09-21 6-191 6 分钟：状态良好时几个小兵和英雄却撤到一边旁观、不参与打野。
            #   实机：正在碾压营地（战力比 8.6），一个刚出兵、从家往这边赶的步兵路过另一个营地 681 以内
            #   （离正在打的营地 2737；那 7 只野怪满血、夜里在睡）-> 「误入第二营地，整队脱离」，全队去安全点收队，
            #   水元素和几个兵还在营地边打，其余的站在旁边看。只算在这场仗里的人。
            if self.camp:
                fighting = [u for u in army
                            if math.hypot(u.x - self.camp["x"], u.y - self.camp["y"]) <= CREEP_SECOND_SCOPE]
            else:
                fighting = army
            other_near = [c for c in world.creeps if c.hp > 0 and c.addr not in self.camp_ids
                          and any(dist(c, u) <= CREEP_SECOND_CONTACT for u in fighting)]
            if other_near:
                touched = {c.addr for c in other_near}
                joined = [camp for camp in creep_camps(world)
                          if any(c.addr in touched for c in camp["units"])]
                extra = {c.addr for camp in joined for c in camp["units"] if c.hp > 0}
                self.camp_ids.update(extra)
                # 记住真正撞上的危险点；_leave_camp 还会记原目标。换目标后，路线也不能再穿过这里。
                self.skip_camps += [(camp["x"], camp["y"], world.t+CREEP_SKIP_S) for camp in joined]
                self._count("打野·误入第二营地撤离")
                self._note(f"打野撤离：目标营地之外又接触 {len(extra)} 只野怪，避免两营夹击")
                return self._leave_camp(world, army, "误入第二营地，整队脱离")
            if not alive:
                self._count("打野·营地清空")
                self._note(f"营地清空（强度 {self.camp['strength']:.0f}）" if self.camp else "营地清空")
                self.camp_ids = set()
                self.creep_focus_id = None
                injured = recovery_injuries(army)
                # 捡装备由 mod_scavenge 单独租用一个英雄完成；主力同一拍继续选下一营地，
                # 不再为了地上掉落把整队最多按住 15 游戏秒。
                if self.camp and self.ground:
                    self._count("打野·掉落交给捡拾模块")
                if injured:
                    missing = sum(u.hp_max-u.hp for u in injured)
                    self.creep_rest_until = world.t + POST_CREEP_REST_S
                    stage = self._prepare_next_creep_stage(world, army, home, mf)
                    if stage is None:
                        self.state = "home"
                        self.creep_rest_xy = ((mf.x, mf.y) if mf is not None else centroid(army))
                        why = "打野后伤兵多，在当前区域安全点整队补给"
                    else:
                        self.state = "creep"
                        self.creep_rest_xy = stage
                        why = "打野后伤兵多，向下一营地安全点前移集结"
                    self._count("打野·清营后伤兵补给")
                    self._note(f"打野后转场补给：{len(injured)} 名伤兵缺血 {missing:.0f}，移动途中给整队恢复留窗口")
                    rx, ry = self.creep_rest_xy
                    return [Intent("move", army, rx, ry, why=why)]
            else:
                left = creep_strength(alive)
                r_main = power_ratio(mf.units if mf is not None else army, alive, world.levels)
                if self.creep_engaged and r_main < CREEP_ABORT_POWER and (self.held or self.held_heroes):
                    self._count("打野·英雄被拉着·先不判打不动")       # 英雄几秒就回来，算战力别把它漏了
                elif self.creep_engaged and r_main < CREEP_ABORT_POWER:
                    self._count("打野·打不动回家")
                    self._note(f"打野回家：主力对营地剩下的战力比 {r_main:.2f} < {CREEP_ABORT_POWER}（营地还剩强度 {left:.0f}）")
                    return self._leave_camp(world, army, "打野打不动，整队脱离")
                self.state = "creep"
                return self._creep_engage(world, army, mf, alive, left)
        # 走到这里时手上没有营地：要么本来就没有，要么上面刚清空一个（camp_ids 已置空、没伤兵）。
        # ★ 09-21 AMAI 的 attack 线程先看 needs_exp（分矿守卫打得过就先清它），然后才是普通打野。
        #   原来这段写在 `if self.camp_ids:` 前面，只有「进来时手上没营地」才会看一眼 —— 清空一个营地的那一拍
        #   直接往下走普通选营地。4-030：清掉分矿路上的挡路营（219 秒）之后接着去打强度 16/17/9 的营地，
        #   分矿守卫（强度 11）一直没人管，「建造跳过·分矿旁边有野怪」114 次，分矿拖到 9 分 37 秒。
        #   放在这里，两种情况走同一个口子。
        pick = self._expansion_guard_pick(world, army, mf)
        if pick is not None:
            return self._go_expansion_guard(world, army, mf, *pick)
        origin = (mf.x, mf.y) if mf is not None else centroid(army)
        group = [u for u in army if math.hypot(u.x - origin[0], u.y - origin[1]) <= CREEP_GROUP_R] or army
        dropped: dict = {}
        plans = creep_plans(world, group, origin, home, self.skip_camps, self.own_rate(world), why=dropped)
        if not plans:
            # 一个营地都没得选：按营地数记是哪一道筛掉的（每个营地记一次；多次出现 = 一直是它在挡）
            self._count("打野·没有可选营地")
            for reason, n in dropped.items():
                k = "打野·营地被筛·" + reason
                self.counts[k] = self.counts.get(k, 0) + n
        near_ok = [p for p in plans if p["wait"] == 0 and p["travel"] <= CHAIN_TRAVEL_S]
        if only_ready:                                   # 英雄没满血时只接近处、不用等兵的（见 CHAIN_TRAVEL_S）
            plans = near_ok
        elif near_ok and plans and plans[0] not in near_ok:
            # ★ 09-18 5-031 局：下面的营地明显可以先打，部队却绕去打上面的，浪费了不少时间。
            #   第 17 拍：下面强度 5 的营地走 6 秒、不用等兵，上面强度 9 的要走 14 秒 + 等兵 18 秒，按「等级和 / 总时间」挑了上面。
            #   等兵的估计偏乐观（兵是一个个出来的），近处现在就打得过的先打，打完再按当时的血量战力挑下一个。
            self._count("打野·先打近处能打的")
            plans = near_ok
        if not plans:
            return None
        p = plans[0]
        camp = p["camp"]
        self._count("打野·选营地·等兵" if p["wait"] > 0 else "打野·选营地")
        alt = (f"；次优 强度 {plans[1]['camp']['strength']:.0f} 走 {plans[1]['travel']:.0f} 等 {plans[1]['wait']:.0f} 秒"
               if len(plans) > 1 else "")
        self._note(f"打野选营地：强度 {camp['strength']:.0f}，战力比 {p['r']:.1f}，走 {p['travel']:.0f} + 等兵 {p['wait']:.0f} + 打 {p['fight']:.0f} 秒{alt}")
        self.state, self.camp = "creep", camp
        self.camp_ids = {c.addr for c in camp["units"]}
        self.creep_since, self.creep_engaged, self.creep_focus_id = world.t, False, None
        self.camp_hp_seen = None          # 换营地一定要清，否则上一个营地的进展记录会被继承
        self.creep_pull_at, self.creep_pull_id = -1e9, None
        self.creep_summon_tank_at = -1e9
        self.creep_ready_at = world.t + max(p["travel"], p["wait"]) + 10.0
        self.creep_regroup_at = -1.0
        self._count("打野")
        return self._creep_engage(world, army, mf, list(camp["units"]), camp["strength"])

    def _expansion_guard_pick(self, world, army, mf):
        """分矿守卫（clear_target，runtime 开矿检查时设）这一次该打哪一群。
        返回 (guard, via, s, r)：guard 是守卫本身，或它路上第一个打得过的挡路营（via=True）；
        None：没有清分矿任务 / 兵力还不够打守卫 / 路上的挡路营打不过 —— 照常打野攒经验（AMAI 也是这样）。
        ⚠ 挡路营打不过原来是「留在家里等兵」：v30 实机 1/3 号「清分矿·路线有强营地挡路」28~36 拍，
          整队在家站着（即已知的「在家发呆」问题）。普通选营地（creep_plans）本来就不穿越别的营地，落过去是安全的。"""
        ct = self.clear_target
        if not ct:
            return None
        guard = [c for c in world.creeps if c.addr in ct["ids"] and c.hp > 0]
        if not guard:
            self.clear_target = None
            return None
        # 分矿目标以前绕过了 creep_plans，因此 1-015 会反复直冲被第二营地挡住的矿点：
        # “误入第二营地撤离”生效，但下一拍 clear_target 又选回原路，成了撤退循环。
        # 先把路上能打的挡路营地当成子任务；挡路营太强则留在家里等兵，不再穿越。
        target_camp = dict(units=guard, x=centroid(guard)[0], y=centroid(guard)[1], strength=creep_strength(guard))
        origin = (mf.x, mf.y) if mf is not None else centroid(army)
        blockers = sorted(creep_route_blockers(target_camp, creep_camps(world), origin),
                          key=lambda c: math.hypot(c["x"]-origin[0], c["y"]-origin[1]))
        if blockers:
            passable = next((c for c in blockers if power_ratio(army, c["units"], world.levels) >= CLEAR_POWER), None)
            if passable is None:
                self._count("清分矿·路线有强营地挡路")
                return None
            guard = [c for c in passable["units"] if c.hp > 0]
        s = creep_strength(guard)
        r = power_ratio(army, guard, world.levels)
        if r < CLEAR_POWER:
            self._count("清分矿·兵力不够")
            return None
        return guard, bool(blockers), s, r

    def _go_expansion_guard(self, world, army, mf, guard, via, s, r) -> list[Intent]:
        gx, gy = centroid(guard)
        self.camp = dict(x=gx, y=gy, strength=s, units=guard)
        self.camp_ids = {c.addr for c in guard}
        self.state = "creep"
        self.creep_since, self.creep_engaged, self.creep_focus_id = world.t, False, None
        self.camp_hp_seen = None      # 换营地一定要清，否则上一个营地的进展记录会被继承
        self.creep_pull_at, self.creep_pull_id = -1e9, None
        self.creep_summon_tank_at = -1e9
        self.creep_ready_at, self.creep_regroup_at = world.t, -1.0
        self._count("打野·清分矿路线挡路营地" if via else "打野·清分矿野怪")
        self._note(f"{'清分矿路线先清挡路野怪' if via else '清分矿野怪'}：强度 {s:.0f}，战力比 {r:.1f}")
        tag = f"{'清分矿路线挡路野怪' if via else '清分矿野怪'}（强度 {s:.0f}）"
        return self._creep_engage(world, army, mf, guard, s, tag=tag)

    def _creep_engage(self, world, army, mf, alive, left, tag: str | None = None) -> list[Intent]:
        """撑不到把营地打光的兵不进打野的进攻令，退到集合点等营地打完；其余的照 _creep_engage_core。
        ★ 09-18 晚打野阵亡分析：33% 的打野阵亡进场时血已经不到 50%，24.3% 在血 < 30% 时还收到过大脑的进攻令
          （例：10 号 17:05:20 一个 8% 血的步兵，14 秒里被派了 6 次）。英雄的血线另有 HERO_RETREAT_HP / 毫秒层管。
        ★★ 09-21 改判据：原来是「血 < CREEP_UNIT_HP(40%) 就摘」，22 局实测每局摘 **100.5 次**，
          而同期每局只清空 6.2 个营地 —— 摘人的次数是清营次数的十六倍，等于一直在添油。
          现在按**打光时间**判：撑得到我们把这个营地打光就留下（轻松营地里 30% 血的步兵照样能打完），
          撑不到才摘。血量地板 KEEP_FLOOR_HP 仍在（低于 20% 死亡率 84%~95%）。
          一次摘走超过 PEEL_MAX 就整队退 —— 别把一支队伍一个一个喂进去。"""
        low, whole = W.hurt_to_peel(army, alive, world.levels, self.hero_ids, kind="creep")
        if low and whole:
            self._count("打野·伤员过半·整队退")
            t_us, t_them = W.kill_times(army, alive, world.levels)
            self._note(f"打野整队退：{len(low)}/{len(army)} 人撑不住"
                       f"（我们打光营地要 {t_us:.0f} 秒，营地打光我们要 {t_them:.0f} 秒）")
            sx, sy = self.home_xy or self._camp_stage(*(centroid(army)))
            return [Intent("move", army, sx, sy, why="打野·伤员过半，整队退到集合点")]
        if not low:
            return self._creep_engage_core(world, army, mf, alive, left, tag)
        ids = {u.addr for u in low}
        rest = [u for u in army if u.addr not in ids]
        out = self._creep_engage_core(world, rest, mf, alive, left, tag) if rest else []
        # 伤员恢复位置独立于下一个营地，不能让 20% 血步兵跟着新任务穿越野怪区。
        sx, sy = self.home_xy or self._camp_stage(*(centroid(army)))
        self._count("打野·残血兵退到集合点")
        return out + [Intent("move", low, sx, sy, why=f"打野·残血兵回家恢复（{len(low)} 个）")]

    def _camp_stage(self, mx, my):
        key = tuple(sorted(self.camp_ids))
        if self.creep_stage_xy is None or key != self.creep_stage_key:
            self.creep_stage_xy = self._stage_for(self.camp, (mx, my))
            self.creep_stage_key = key
        return self.creep_stage_xy

    def _ground(self, x, y, anchor) -> tuple[float, float]:
        """打野的站位点（后排展开、两翼包围）落在树里/水里/树林对面时，挪到 anchor（目标野怪）那一侧最近的空地。
        ★ 09-22 对局画面：集结打野时队伍经常被树隔开 —— 展开线横着铺十来个人、一千多宽，垂直于营地方向，
          一头插进树林；点在树里的人走到树林自己这一侧就停下，和队伍隔着树。没有地形表原样返回。"""
        if self.terrain is None:
            return x, y
        p = self.terrain.snap(x, y, anchor)
        if p is None:
            return x, y
        if abs(p[0] - x) > 1 or abs(p[1] - y) > 1:
            self._count("打野·站位点挪到营地这一侧的空地")
        return p

    def _stage_for(self, camp, origin) -> tuple[float, float]:
        """营地的集合点（等人、英雄拉怪出来、残血兵退到这里）。
        ★ 09-21 有地形表（brain/terrain.py）就挑营地周围最空旷、身后有退路的一侧，而且从部队现在的位置走过去不擦着营地；
          原来一律「朝家的方向退 CREEP_STAGE」—— 左下角鱼人营地那个点夹在水和树林之间（对局画面可见），
          还有两个营地（2824,-725 / 6014,-4621）的旧点根本不能走。没有地形表时照旧。
        ★★ 09-22 同一个营地又被树隔开（对局画面复现）：地形表里树只记了一半，点挑进了树林带中间 —— 见 terrain.py 文件头。"""
        cx, cy = camp["x"], camp["y"]
        hx, hy = self.home_xy or origin
        if self.terrain is not None:
            p = self.terrain.stage_point(cx, cy, CREEP_STAGE, (hx, hy), origin, CREEP_WAKE_R)
            if p is not None:
                self._count("打野·集合点按地形挑")
                return p[0], p[1]
        d = math.hypot(hx - cx, hy - cy)
        return ((cx + (hx-cx)/d*CREEP_STAGE, cy + (hy-cy)/d*CREEP_STAGE) if d >= 1 else (cx, cy-CREEP_STAGE))

    def _prepare_next_creep_stage(self, world, army, home, mf):
        """清完营地立即锁定下一处可打野点，把补给窗口用在转场路上。"""
        origin = (mf.x, mf.y) if mf is not None else centroid(army)
        usable = [u for u in army if u.hp > 0 and (u.hp_max <= 0 or u.hp >= 0.35*u.hp_max)] or army
        group = [u for u in usable if math.hypot(u.x-origin[0], u.y-origin[1]) <= CREEP_GROUP_R] or usable
        # 下一站同样先看分矿守卫（AMAI needs_exp，理由见 _creep 里调 _expansion_guard_pick 的那段）
        pick = self._expansion_guard_pick(world, group, mf)
        if pick is not None:
            guard, via, s, _r = pick
            gx, gy = centroid(guard)
            p = dict(camp=dict(x=gx, y=gy, strength=s, units=guard),
                     travel=math.hypot(gx - origin[0], gy - origin[1]) * CREEP_PATH / group_speed(group), wait=0.0)
            self._count("打野·清营后前往分矿守卫")
        else:
            plans = creep_plans(world, group, origin, home, self.skip_camps, self.own_rate(world))
            if not plans:
                return None
            # 优先最快能形成战斗队形的营地。只追“等级/总时间”高分会越过身边野点再走远路。
            p = min(plans, key=lambda q: (max(q["travel"], q["wait"]), q["travel"], -q["camp"]["strength"]))
        # ★ 09-21 4-036 8:35：打野途中英雄撤走，只留下小兵，最后小兵全部阵亡。
        #   实机：清完营地 4 个兵带伤（缺 895 血），往下一个营地（强度 17：鱼人×4 + 巨魔）的集合点前移；
        #   从当前位置到集合点的直线离那个营地约 580 —— 带伤的队伍路过把它惊醒了，然后英雄被毫秒层判孤军拉走、
        #   大脑判「未开打营地凑不齐」收队，被追上的步兵 #5 #16 #13 #10 一个个死在撤退路上。
        #   带伤前移的路线（和集合点本身）离任何营地 CREEP_WAKE_R 以内就不前移，原地补给。
        sx, sy = self._stage_for(p["camp"], origin)          # 和 _camp_stage 同一个算法（地形表在就按地形挑）
        # 起点旁边的（t <= 0.05）不算：已经站在那里没惊醒，往外走只会更远（和 creep_route_blockers 同一个口径）
        near = [segment_distance(c.x, c.y, origin[0], origin[1], sx, sy) for c in world.creeps if c.hp > 0]
        wake = min((d for d, t in near if t > 0.05), default=1e9)
        if wake < CREEP_WAKE_R:
            self._count("打野·带伤前移会惊醒营地·原地补给")
            self._note(f"带伤不前移：去下一营地集合点的路离野怪只有 {wake:.0f}（< {CREEP_WAKE_R:.0f}），原地补给")
            return None
        self.camp = p["camp"]
        self.camp_ids = {c.addr for c in self.camp["units"]}
        self.creep_since, self.creep_engaged, self.creep_focus_id = world.t, False, None
        self.camp_hp_seen = None          # 换营地一定要清，否则上一个营地的进展记录会被继承
        self.creep_pull_at, self.creep_pull_id = -1e9, None
        self.creep_summon_tank_at = -1e9
        self.creep_ready_at = world.t + max(p["travel"], p["wait"]) + 10.0
        self.creep_regroup_at = -1.0
        sx, sy = self._camp_stage(*origin)
        self._count("打野·清营后前往下一营地")
        self._note(f"清营后直接前移：下一营地强度 {self.camp['strength']:.0f}，走 {p['travel']:.0f}、等兵 {p['wait']:.0f} 游戏秒")
        return sx, sy

    def _leave_camp(self, world, army, why):
        cx, cy = self.camp["x"], self.camp["y"]
        self.skip_camps = [s for s in self.skip_camps if s[2] > world.t] + [(cx, cy, world.t+CREEP_SKIP_S)]
        self.state, self.retreat_at, self.creep_withdraw = "retreat", world.t, True
        self.creep_regroup_at = -1.0
        self.creep_focus_id = None
        x, y = self._local_creep_escape(world, army)
        self.creep_withdraw_xy = (x, y)
        return [Intent("move", army, x, y, why=why)]

    def _local_creep_escape(self, world, army):
        """从当前接触点后撤一小段；只为脱离野怪，不把一次换营地变成全图回城。"""
        ax, ay = centroid(army)
        threats = [c for c in world.creeps if c.hp > 0 and math.hypot(c.x-ax, c.y-ay) <= CREEP_GATHER_R]
        if threats:
            tx, ty = centroid(threats)
            dx, dy = ax-tx, ay-ty
        else:
            hx, hy = self.home_xy or (ax, ay-CREEP_STAGE)
            dx, dy = hx-ax, hy-ay
        d = math.hypot(dx, dy)
        if d < 1.0:
            dx, dy, d = 0.0, -1.0, 1.0
        return ax + dx/d*CREEP_STAGE, ay + dy/d*CREEP_STAGE

    def _creep_engage_core(self, world, army, mf, alive, left, tag: str | None = None) -> list[Intent]:
        """营地 CREEP_GATHER_R 内实际到场兵力够了才点名开打；走得近不能替代兵力检查。
        开打以后到场的人战力比掉到 CREEP_EASY_ENGAGE 以下，也不再点名开打、不让后面的兵一个个添油（见 ready）。"""
        mx, my = (mf.x, mf.y) if mf is not None else centroid(army)
        cx, cy = self.camp["x"], self.camp["y"]
        label = f"营地强度 {self.camp['strength']:.0f}"
        head = tag or f"打野（{label}）"
        gathered = [u for u in army if math.hypot(u.x - cx, u.y - cy) <= CREEP_GATHER_R]
        g_str = power_ratio(gathered, alive, world.levels) if gathered else 0.0      # 到场的人对营地剩下的战力比
        waited = world.t - self.creep_since
        ratio = CREEP_ENGAGE_POWER if waited < CREEP_LOWER_S else CREEP_ENGAGE_POWER_LATE
        r_all = power_ratio(army, alive, world.levels)
        easy = r_all >= CREEP_DIRECT_POWER
        coming = [u for u in army if math.hypot(u.x - cx, u.y - cy) <= CREEP_GROUP_R]
        r_coming = power_ratio(coming, alive, world.levels) if coming else 0.0
        gathered_ids = {u.addr for u in gathered}
        arrived_front = [u for u in gathered if u.addr not in self.hero_ids]
        summon_contact_pre = any(is_summon(u.type) and u.hp > .2*max(1,u.hp_max)
                                 and any(math.hypot(u.x-c.x,u.y-c.y) <= CREEP_TANK_CONTACT for c in alive)
                                 for u in gathered)
        # ★★ 09-21 3-018 局 6 分钟打二矿营地：水元素和一个小兵已经在打，其余兵和英雄
        #   停在远处不上前协助。
        #   根因：下面那两条放行口都要 `self.creep_engaged`，而它只在**大脑自己下过开打令**时才为真。
        #   召唤物/跑得快的兵自己冲上去拉了仇恨时它还是 False，于是「已经在打了」这件事看不见，
        #   整队就一直停在「未成团不开怪」。——又是一个量同时表示两件事：「没开打」和「不是我开的」。
        #   改成看**现场**：我方有人贴着活着的野怪 = 这仗已经开了，站着看只会让那一两个人白死。
        engaged_now = any(u.hp > 0 and any(math.hypot(u.x-c.x, u.y-c.y) <= CREEP_TANK_CONTACT for c in alive)
                          for u in gathered)
        reinforced = (g_str >= CREEP_REINFORCE_ENGAGE
                      and len(arrived_front) >= CREEP_REINFORCE_MIN
                      and r_coming >= ratio
                      and any(u.addr not in gathered_ids for u in coming))
        # ★ 09-18 晚：原来「开打过一次」就一直算够了（self.creep_engaged or ...），打野阵亡 16.7% 是孤军添油 ——
        #   「打野开打（到场战力比 0.2 / 0.0）」「打野·后面的跟上：1/1 个」。开打过也要到场的人不吃亏（>= CREEP_EASY_ENGAGE）
        already = self.creep_engaged or engaged_now
        ready = (g_str >= (CREEP_EASY_ENGAGE if easy else ratio) or reinforced
                 or (already and g_str >= CREEP_EASY_ENGAGE)
                 # 226 局：水元素已在顶怪，大法师被保命层暂时拉走；旧逻辑又把步兵撤到集合点，
                 # 只剩召唤物单打。召唤物仍接触且至少一名永久兵已到场时，允许继续输出。
                 or (already and summon_contact_pre and len(gathered) >= 2)
                 # 3-018：没人下过开打令，但自己人已经和野怪贴上了 —— 别站着看（见 engaged_now）
                 or (engaged_now and len(gathered) >= 2))
        if engaged_now and not self.creep_engaged:
            self._count("打野·已经打起来了·全队跟上")
        if easy and not gathered:
            # 1-011：全军战力比 4.2，但队伍还在后面。攻击移动使跑得快的水元素先开怪，
            # 下一拍又因到场战力不足改回集合，最终大法师被留在四只野怪中。
            sx, sy = self._camp_stage(mx, my)
            self._count("打野·轻松营地也先到安全点")
            return [Intent("move", army, sx, sy, why=f"{head}·全军很强但尚未到场，先去安全集结点")]
        if ready and gathered:
            self.creep_regroup_at = -1.0
            # 夜里野怪睡着，攻击移动走到跟前也不会主动打；明确点名同一只高输出核心。
            opening = not self.creep_engaged
            self.creep_engaged = True
            if reinforced and g_str < (CREEP_EASY_ENGAGE if easy else ratio):
                self._count("打野·增援在路上提前开打")
            tgt = creep_focus_target(alive, world.levels, self.creep_focus_id)
            self.creep_focus_id = tgt.addr
            core_dps = creep_dps(tgt, world.levels)
            self._count("打野·点名开打")
            self._count("打野·集火最高输出核心")
            why = (f"打野开打（{label}，到场战力比 {min(g_str, 99):.1f}"
                   f"{'、增援在路上' if reinforced else ''}；核心伤害 {core_dps:.1f}/秒）")
            # ★ 09-18 5-035 局 8 分钟：大法师直接冲上去给野怪当坦克，被打到残血。
            #   原来到场的人一起点名打同一只野怪：大法师移速 320 比步兵快、又是远程，常常第一个出手，仇恨就在它身上。
            #   肉盾（近战兵、召唤物、近战英雄）先打；远程（大法师这类施法英雄、火枪手、牧师……）先站在离目标 CREEP_BACK_STAND
            #   的我方一侧，等肉盾贴上野怪（CREEP_TANK_CONTACT 内）再打。没有肉盾就一起打。
            front = [u for u in gathered if not is_backline(u.type)]
            back = [u for u in gathered if is_backline(u.type)]
            tanked = any(math.hypot(u.x - c.x, u.y - c.y) <= CREEP_TANK_CONTACT for u in front for c in alive)
            summons = [u for u in front if is_summon(u.type) and u.hp > .2 * max(1, u.hp_max)]
            summon_tanked = any(math.hypot(u.x - c.x, u.y - c.y) <= CREEP_TANK_CONTACT
                                for u in summons for c in alive)
            if summon_tanked and self.creep_summon_tank_at < self.creep_since:
                self.creep_summon_tank_at = world.t
            elif not summons:
                self.creep_summon_tank_at = -1e9
            # 在外面等肉盾 / 召唤物接火最多 CREEP_WAIT_MAX_S（从本营地第一次等起算）
            if not tanked and not summon_tanked and self.creep_wait_at < self.creep_since:
                self.creep_wait_at = world.t
            waited_out = self.creep_wait_at >= self.creep_since and world.t - self.creep_wait_at >= CREEP_WAIT_MAX_S
            if waited_out and not tanked and not summon_tanked:
                self._count("打野·等接火超时·全队上")

            def backline_wait(waiters, reason="打野·后排展开等肉盾接火"):
                """在核心的我方一侧横向展开；不要让大法师、火枪和近战英雄叠在一个点上。"""
                if not waiters:
                    return []
                d = math.hypot(mx - tgt.x, my - tgt.y) or 1.0
                ux, uy = (mx - tgt.x) / d, (my - tgt.y) / d
                lx, ly = -uy, ux
                base_x, base_y = tgt.x + ux * CREEP_BACK_STAND, tgt.y + uy * CREEP_BACK_STAND
                ordered = sorted(waiters, key=lambda u: (not G.is_hero(u.type), u.addr))
                return [Intent("move", [u], *self._ground(base_x + lx * (i-(len(ordered)-1)/2) * 130.0,
                                                          base_y + ly * (i-(len(ordered)-1)/2) * 130.0, (tgt.x, tgt.y)),
                               why=reason) for i, u in enumerate(ordered)]

            # 首次开怪只让一两个非英雄前排拉住仇恨，其余前排绕两翼/背面。下一策略拍它们已在
            # 不同方向，全队再点同一核心时就会从四周贴上，不会在营地入口排成一条线互相卡住。
            screen = [u for u in front if not G.is_hero(u.type)]
            puller = next((u for u in back if G.is_hero(u.type) and u.hp >= .8*max(1, u.hp_max)), None)
            pull_ready = (opening and not tanked and puller is not None and len(screen) >= 3
                          and len(gathered) >= max(4, math.ceil(.75*len(army))))
            pulling = next((u for u in gathered if u.addr == self.creep_pull_id), None)
            pull_elapsed = world.t-self.creep_pull_at
            if pull_ready:
                # INFI：只有全队已到场、至少三名非英雄肉盾就位时，才让远程英雄单点一下把怪
                # 拉出出生点；其余人停在 500 外展开。下一策略拍英雄后撤、肉盾接火。
                self.creep_pull_at, self.creep_pull_id = world.t, puller.addr
                self._count("打野·英雄先手拉怪")
                out = [Intent("attack", [puller], tgt.x, tgt.y, tgt, why=why + "·英雄先手拉出营地")]
                out.extend(backline_wait([u for u in gathered if u.addr != puller.addr], "打野·英雄先手时全队在外展开"))
            elif pulling is not None and 0 < pull_elapsed <= CREEP_HERO_PULL_S and screen:
                # 英雄完成第一下后立刻回拉。只要有召唤物，就只让召唤物接第一轮仇恨；
                # 民兵和永久近战都留在外圈，不能因为攻击距离短就自动被当成肉盾。
                anchor_n = 2 if len(screen) >= 5 else 1
                anchors = (summons if summons else sorted(
                    screen, key=lambda u: (0 if u.type == "hmil" else 1,
                                           math.hypot(u.x-tgt.x, u.y-tgt.y), u.addr))[:anchor_n])
                anchor_ids = {u.addr for u in anchors}
                flankers = [] if summons else [u for u in screen if u.addr not in anchor_ids]
                out = backline_wait([pulling], "打野·英雄先手后回拉到肉盾后方")
                out.append(Intent("attack", anchors, tgt.x, tgt.y, tgt,
                                  why=why + ("·召唤物接住拉出的野怪" if summons else "·肉盾接住拉出的野怪")))
                out.extend(Intent("move", [u], *self._ground(x, y, (tgt.x, tgt.y)), why="打野·前排两翼包围拉出的核心")
                           for u, x, y in creep_surround_slots(flankers, tgt, (mx, my)))
                waiting = [u for u in gathered if u.addr != pulling.addr and u.addr not in anchor_ids]
                out.extend(backline_wait(waiting, "打野·召唤物接火时永久兵在外圈等待" if summons else "打野·远程展开等肉盾接火"))
                self._count("打野·英雄回拉肉盾接火")
                if summons:
                    self._count("打野·召唤物优先承伤")
            elif summons and not summon_tanked and not tanked and not waited_out:
                # 还没人贴上：召唤物先去接仇恨，永久兵在外圈等 —— 最多等 CREEP_WAIT_MAX_S（水元素走不过去 / 半路没了，全队不能一直干站）。
                # ★ 09-22 原来「无论普通近战是否已经误入接触范围」都把永久兵拉回外圈：已经在打、没人打它的兵也被拉走。
                #   现在已经有人贴上（tanked）就不拉回，全队打；谁被盯上血低了归毫秒层拉（见 CREEP_WAIT_MAX_S 上面那段）。
                summon_ids = {u.addr for u in summons}
                out = [Intent("attack", summons, tgt.x, tgt.y, tgt, why=why + "·召唤物先接仇恨")]
                out.extend(backline_wait([u for u in gathered if u.addr not in summon_ids],
                                         "打野·有召唤物时永久兵不抢承伤"))
                self._count("打野·召唤物优先承伤")
            elif summons and summon_tanked and world.t - self.creep_summon_tank_at < CREEP_SUMMON_SETTLE_S:
                # 已贴上但刚接触：远程可以开始输出，永久近战再等很短一拍，避免同拍冲上去抢仇恨。
                summon_ids = {u.addr for u in summons}
                ranged = [u for u in back if u.addr not in summon_ids]
                permanent_melee = [u for u in front if u.addr not in summon_ids]
                out = [Intent("attack", summons + ranged, tgt.x, tgt.y, tgt, why=why + "·召唤物稳住仇恨")]
                out.extend(backline_wait(permanent_melee, "打野·召唤物稳仇恨后近战再进场"))
                self._count("打野·召唤物稳住仇恨")
            elif opening and not tanked and len(screen) >= 3:
                anchor_n = 2 if len(screen) >= 5 else 1
                anchors = sorted(screen, key=lambda u: (0 if u.type == "hmil" else 1,
                                                        math.hypot(u.x-tgt.x, u.y-tgt.y), u.addr))[:anchor_n]
                anchor_ids = {u.addr for u in anchors}
                flankers = [u for u in screen if u.addr not in anchor_ids]
                out = [Intent("attack", anchors, tgt.x, tgt.y, tgt, why=why + "·坦克先接火")]
                out.extend(Intent("move", [u], *self._ground(x, y, (tgt.x, tgt.y)), why="打野·前排两翼包围核心")
                           for u, x, y in creep_surround_slots(flankers, tgt, (mx, my)))
                out.extend(backline_wait(back))
                self._count("打野·前排包围核心")
            elif front and back and not tanked and not waited_out:
                self._count("打野·远程等肉盾接火")
                out = [Intent("attack", front, tgt.x, tgt.y, tgt, why=why + "·肉盾先上")]
                out.extend(backline_wait(back, "打野·远程等肉盾接火"))
            else:
                # INFI 的民兵始终是独立小队：同一目标但单独下令，方便毫秒层把残血民兵
                # 拉后 300 再送回，而不打断正规军和英雄的持续输出。
                militia = [u for u in gathered if u.type == "hmil" and u.addr in self.militia_ids]
                regular = [u for u in gathered if u not in militia]
                out = ([Intent("attack", regular, tgt.x, tgt.y, tgt, why=why)] if regular else [])
                if militia:
                    out.append(Intent("attack", militia, tgt.x, tgt.y, tgt, why=why + "·民兵独立承伤组"))
            if not pull_ready and self.creep_pull_id is not None and pull_elapsed > CREEP_HERO_PULL_S:
                self.creep_pull_at, self.creep_pull_id = -1e9, None
            ids = {u.addr for u in gathered}
            rest = [u for u in army if u.addr not in ids]
            if rest:
                self._count("打野·后面的跟上")
                out.append(Intent("attack_move", rest, cx, cy, why="打野·后面的跟上"))
            return out
        if self.creep_engaged:
            # AMAI 接战任务持续到完成或明确撤离；轮换/短暂保命不是重新选营地。
            if self.creep_regroup_at < 0:
                self.creep_regroup_at = world.t
            if (g_str < CREEP_ABORT_POWER and not (self.held or self.held_heroes)) or world.t - self.creep_regroup_at >= 45:
                self._note("打野重整失败，先让旧营地全员脱离再换目标")
                return self._leave_camp(world, army, "打野重整失败，整队脱离")
            sx, sy = self._camp_stage(mx, my)
            self._count("打野·原营地重整不换目标")
            return [Intent("move", army, sx, sy, why="打野原营地重整，等轮换和英雄归队")]
        if waited >= CREEP_GIVEUP_S or (waited >= CREEP_REPICK_S and world.t >= self.creep_ready_at and r_coming < ratio):
            self._count("打野·凑不齐人放弃" if waited >= CREEP_GIVEUP_S else "打野·等不到人换营地")
            self._note(f"打野放弃：选定 {int(waited)} 游戏秒还凑不齐（到场战力比 {g_str:.1f}，{int(CREEP_GROUP_R)} 内能赶到的 {r_coming:.1f} < {ratio}）")
            return self._leave_camp(world, army, "放弃未开打营地，先收队")
        sx, sy = self._camp_stage(mx, my)
        self._count("打野·等人到齐")
        # 1-011：等兵时把召唤物排除在集合令外，毫秒层水元素护卫又会主动攻击英雄旁的野怪，
        # 于是水元素单独开怪。现在未满足开打门槛时，召唤物也跟全队纯移动到安全点。
        close = [u for u in army if math.hypot(u.x - cx, u.y - cy) <= CREEP_STAGE_NEAR]
        far = [u for u in army if math.hypot(u.x - cx, u.y - cy) > CREEP_STAGE_NEAR]
        out = []
        if close:
            out.append(Intent("move", close, sx, sy, why=f"打野·先到的退到集合点等人（{label}，到场战力比 {g_str:.1f}）"))
        if far:
            out.append(Intent("move", far, sx, sy, why=f"{head}·去集合点（未成团不开怪）"))
        return out

    def own_rate(self, world) -> float:
        """我方强度最近 GROWTH_WINDOW 游戏秒的涨速（每游戏秒，不涨按 0）—— 估「等兵要多久」。"""
        old = [p for p in self.own_hist if world.t - p[0] >= GROWTH_WINDOW * 0.5]
        if not old:
            return 0.0
        t0, s0 = min(old, key=lambda p: abs(world.t - p[0] - GROWTH_WINDOW))
        return max(0.0, (world.own_strength - s0) / max(1.0, world.t - t0))

    def _take_signal(self, army, cx, cy) -> dict | None:
        """毫秒层叫撤（fight_retreat.json，runtime 读进 retreat_req）：在场的我方人数 >= 全军 SIGNAL_MAIN_SHARE（主力在输）才算数，
        局部一两个人打不过的归毫秒层自己拉人（大脑这边看的是全局，局部输了可能正是该支援的时候）。取走即清。"""
        sig, self.retreat_req = self.retreat_req, None
        if sig is None:
            return None
        if sig.get("n_me", 0) >= max(3, SIGNAL_MAIN_SHARE * len(army)):
            return sig
        self._count("毫秒层叫撤·局部（不整队撤）")
        return None

    def _signal_retreat(self, world, army, home, sig) -> list[Intent]:
        self._count(f"毫秒层叫撤·整队回家·{self.state}")
        self._note(f"毫秒层叫撤（{sig.get('why')}：在场我方 {sig.get('n_me')} vs 敌 {sig.get('n_foe')}），整队回家（原来在 {self.state}）")
        if self.camp_ids and self.camp:
            return self._leave_camp(world, army, "毫秒层判这一仗输了，整队回家")
        self.state, self.target, self.retreat_at, self.camp_ids = "retreat", None, world.t, set()
        return [Intent("move", army, home.x, home.y, why="毫秒层判这一仗输了，整队回家")]

    def _gather(self, world, army, mf, cx, cy, own) -> list[Intent]:
        """80% 到齐或等待到期后，用实际到场兵力复核；超时不能覆盖安全判断。"""
        current = [u for u in army if math.hypot(u.x - cx, u.y - cy) <= GATHER_RADIUS]
        current_str = amai.strength(current, world.levels)
        # 主力本来已经成团就直接出发，不能为了“前沿集结”反而增加一道绕路。
        # 只有部队确实分散时，才使用朝下一目标前推并锁定的集结点。
        cohesive = own > 0 and current_str >= GATHER_READY * own
        gx, gy = (cx, cy) if cohesive else (self.gather_xy or (cx, cy))
        near = current if cohesive else [u for u in army if math.hypot(u.x - gx, u.y - gy) <= GATHER_RADIUS]
        near_str = amai.strength(near, world.levels)
        waited = world.t - self.gather_at
        if own <= 0 or near_str >= GATHER_READY * own or waited >= GATHER_MAX_S:
            # 指令可能因碰撞、交战或控制权竞争而没把主力送到前沿点。超时后若原主力仍完整成团，
            # 用这支真实存在的队伍复核并直接出发，不能因集结点为空就原地取消；远方援军仍不计入。
            commit = (current if waited >= GATHER_MAX_S and current_str > near_str else near)
            commit_x, commit_y = ((cx, cy) if commit is current else (gx, gy))
            ev = self._assess(world, commit, _Pt(commit_x, commit_y))
            commit_reserve = (GATHER_MAIN_COMMIT_RESERVE if self.target_kind == "主基地"
                              else GATHER_EXPANSION_COMMIT_RESERVE)
            # ★★ 09-23 1-432：出击停滞问题修过后再次出现，部队一直不主动骚扰和进攻 ——
            #   僵持破局/兵满钱多按 CAPPED_MARGIN 1.0 倍发起，40 游戏秒后的集结复核却还要 1.205 倍，
            #   实局两次（有效兵力 48.1 / 所需 45.6 = 1.055 倍、55.0 / 49.6 = 1.109 倍）都在这一步被取消：
            #   目标冷却 45 秒 -> 回家 -> 再发起，屏幕上就是「全军在家站着」。放宽出的门，复核走同一条线。
            #   ⚠ 又一次「只改了一处」（同一条规则写在多处，只改一处等于没改）：放宽门槛时要把这条路上每一道闸都过一遍。
            if self.relaxed and commit_reserve > CAPPED_MARGIN:
                commit_reserve = CAPPED_MARGIN
                self._count("集结复核·按放宽门槛")
            if ev.good and ev.rhs >= ev.lhs * commit_reserve:
                self._count("集结·出发" if waited < GATHER_MAX_S else "集结·等满出发")
                self.state, self.start_strength, self.progress_at = "attack", near_str, world.t
                return self._push(army, cx, cy, self.target.x, self.target.y, self.target, f"出击打{self.target_kind}")
            if waited >= GATHER_MAX_S:
                self._count("集结·到场兵力不足放弃")
                self.skip_targets[self.target.addr] = world.t + 45.0
                self.state, self.target, self.retreat_at = "retreat", None, world.t
                return [Intent("move", army, *self.home_xy, why="集结超时且到场兵力不足，重整")]
        self._count("集结·等人")
        far = [u for u in army if math.hypot(u.x - gx, u.y - gy) > GATHER_RADIUS]
        return [Intent("move", far, gx, gy, why="向下一目标前沿集结")]

    def _combat_progress(self, world, cx, cy) -> None:
        """出击中「在杀东西」也是进展：主力身边有敌方作战单位在交战、敌方总强度降了、主力附近的敌方建筑少了 -> 刷新 progress_at。
        _push 原来只认「离目标近了 200」和「目标掉血」（见 STALL_FIGHT_R 上面的 1-288）。"""
        foe = world.foe_strength
        blds = sum(1 for b in world.enemy_buildings if b.hp > 0 and math.hypot(b.x - cx, b.y - cy) <= STALL_NEAR_R)
        fighting = any(e.hp > 0 and math.hypot(e.x - cx, e.y - cy) <= STALL_FIGHT_R for e in world.enemy_fighters)
        first = self.progress_foe == float("inf")          # 换目标后第一拍只登记（拿无穷大比，任何数都像「减员」）
        why = ("交战中" if fighting else None if first
               else "敌方减员" if foe < self.progress_foe - STALL_FOE_DROP
               else "拆掉建筑" if blds < self.progress_blds else None)
        if why is not None:
            self.progress_at = world.t
            self._count("出击·进展·" + why)
        self.progress_foe, self.progress_blds = foe, blds

    def _route_camp(self, world, army, cx, cy, target) -> list[Intent] | None:
        """★★ 09-22 晚 占优出击时，顺路把中间能清的野怪也清掉，尽量扩大优势。
        占优（own >= foe × ROUTE_CREEP_AHEAD）出击时，离「主力 -> 目标」连线 ROUTE_CREEP_R 内、在前方（t 在 ROUTE_CREEP_T）、
        我方主力对它的战力比（血 × 伤害，平方律）>= ROUTE_CREEP_RATIO 的营地先清掉再走：拿经验 / 装备，也不留给对面。
        原来打野那套是绕开路上的营地（creep_route_blockers，怕带伤的队伍惊醒第二营），出击时没人管它们。"""
        if world.own_strength < ROUTE_CREEP_AHEAD * max(0.1, world.foe_strength):
            return None
        hp_us, dps_us = power(army, world.levels)
        best = None
        for camp in creep_camps(world):
            d, t = segment_distance(camp["x"], camp["y"], cx, cy, target.x, target.y)
            if d > ROUTE_CREEP_R or not (ROUTE_CREEP_T[0] <= t <= ROUTE_CREEP_T[1]):
                continue
            hp_c, dps_c = power(camp["units"], world.levels)
            ratio = (hp_us * dps_us) / max(1e-6, hp_c * dps_c)
            if ratio < ROUTE_CREEP_RATIO:
                self._count("出击·顺路营地打不动·不清")
                continue
            if best is None or t < best[1]:
                best = (camp, t, ratio)
        if best is None:
            return None
        camp, _, ratio = best
        core = creep_focus_target(camp["units"], world.levels, self.creep_focus_id)
        self._count("出击·顺路清野")
        return [Intent("attack", army, core.x, core.y, core,
                       why=f"出击路上顺路清野（营地强度 {camp['strength']:.0f}，战力比 {min(ratio, 99):.1f}）")]

    def _push(self, army, cx, cy, x, y, target, why) -> list[Intent]:
        """主力攻击移动到目标；离主力 2000 外的援兵先移动到主力那里（不单独冲进对面基地送）。对面兵力打没了就都直接去。"""
        remaining = math.hypot(cx - x, cy - y)
        hp = getattr(target, "hp", 0)
        if remaining < self.progress_dist - 200 or hp < self.progress_hp - 1:
            self.progress_at, self.progress_dist, self.progress_hp = self.now, remaining, hp
        elif self.now - self.progress_at >= 60 and self.home_xy is not None:
            self._count("出击·任务停滞重整")
            if self.target is not None:
                self.skip_targets[self.target.addr] = self.now + 60
            self.state, self.target, self.retreat_at = "retreat", None, self.now
            return [Intent("move", army, *self.home_xy, why="进攻任务无进展，回家重整")]
        if self.safe:
            return [Intent("attack_move", army, x, y, target, why)]
        wait = self._wait_heroes(army, cx, cy, x, y, target, why)
        if wait is not None:
            return wait
        near = [u for u in army if math.hypot(u.x - cx, u.y - cy) <= REINFORCE_FAR]
        far = [u for u in army if math.hypot(u.x - cx, u.y - cy) > REINFORCE_FAR]
        close = [u for u in far if math.hypot(u.x - cx, u.y - cy) <= REINFORCE_JOIN_MAX]
        remote = [u for u in far if math.hypot(u.x - cx, u.y - cy) > REINFORCE_JOIN_MAX]
        if len(remote) >= REINFORCE_MIN:
            close, remote = close + remote, []
        # ★ 09-21 1-280 17:50：#40（水元素）和 #41（骑士）两个单位冲进敌方二矿，明显打不过仍留在那里，最终阵亡。
        #   实机 1066 秒「出击打主基地：3/3 个」—— 评估按全军算（有效兵力 22.6 / 所需 8.2），冲进去的只有前面 3 个；
        #   main_force 在几团强度差不多时取「离目标最近」的那团当主力（防来回换），前面那一小团就一直是主力、一直往前冲，
        #   后面的人和英雄「去跟主力汇合」永远追不上。前面的人少于会来汇合的 PUSH_MIN_SHARE：停下等，到齐再一起打。
        joining = near + close + [u for u in remote if u.addr in self.hero_ids]
        if far and not self.safe and len(near) < PUSH_MIN_SHARE * len(joining):
            self._count("出击·前面人太少先停下等汇合")
            out = [Intent("move", near, cx, cy, why="出击·前面人太少，停下等后面的人汇合再打")]
        else:
            out = [Intent("attack_move", near, x, y, target, why)]
        if far:
            if close:
                self._count("出击·援兵先汇合")
                out.append(Intent("move", close, cx, cy, why="援兵先去跟主力汇合"))
            hs = [u for u in remote if u.addr in self.hero_ids]
            if hs:
                remote = [u for u in remote if u.addr not in self.hero_ids]
                self._count("出击·英雄赶去跟主力")
                out.append(Intent("attack_move", hs, cx, cy, why="英雄赶去跟主力汇合"))
            if remote:
                # 小股新兵在锁定的前沿点汇合；主力已经推进时也不再让它们先走回基地再二次出发。
                rx, ry = self.gather_xy or (cx, cy)
                self._count("出击·援兵去前沿集结")
                out.append(Intent("move", remote, rx, ry, why="援兵太少太远，去前沿集结点攒人"))
        return out

    @property
    def cull_ids(self) -> set:
        return {self.cull["victim"]} if self.cull else set()

    def _hostile_near(self, world, u, r: float) -> bool:
        return any(e.hp > 0 and math.hypot(e.x - u.x, e.y - u.y) <= r
                   for e in list(world.enemy_fighters) + list(world.creeps) + list(world.enemy_towers))

    def _cull(self, world, army) -> tuple[list, set]:
        """强制攻击自己的低级兵腾人口（CULL_*）。返回 (意图, 这一拍被占用的单位地址)。一次只打一个，打死或放手才挑下一个。"""
        t = world.t
        # 按全部单位判死活：它掉到五成血以下会被划进养伤、从出击层这份 world.my_army 里拿掉，那不是死了
        alive = {u.addr: u for u in world.units if u.owner == world.me and u.hp > 0}
        if self.cull:
            v = alive.get(self.cull["victim"])
            if v is None:
                self._count("强制A低级兵·腾出人口")
                self._note(f"强制攻击自己的 {self.cull['type']} 完成：腾出 {self.cull['food']} 人口给 {self.cull['want']}"
                           f"（用时 {t - self.cull['t']:.0f} 秒，人口 {world.food_used}/{world.food_cap}）")
                self.cull, self.cull_done_at = None, t
            elif t - self.cull["t"] > CULL_TIMEOUT_S or self._hostile_near(world, v, CULL_SAFE_R):
                self._count("强制A低级兵·放手·" + ("超时" if t - self.cull["t"] > CULL_TIMEOUT_S else "有敌人"))
                self.cull, self.cull_done_at = None, t
        if self.cull is None and not self._cull_start(world, army):
            return [], set()
        v = alive[self.cull["victim"]]
        killers = [u for u in army if u.addr in self.cull["killers"] and u.addr != v.addr]
        if not killers:                                   # 动手的人被别的事叫走了（守家/养伤/骚扰）：从身边再挑
            killers = self._cull_killers(world, army, v)
            self.cull["killers"] = {u.addr for u in killers}
        if not killers:
            self._count("强制A低级兵·没人动手")
            return [], set()
        kx, ky = centroid(killers)
        why = f"强制攻击自己的 {v.type} 腾人口（{world.food_used}/{G.FOOD_SOFT_CAP}，给 {self.cull['want']}）"
        return ([Intent("force_attack", killers, v.x, v.y, v, why=why),
                 Intent("move", [v], kx, ky, why="站过去等自己人动手（腾人口）")],
                {v.addr} | {u.addr for u in killers})

    def _cull_killers(self, world, army, v) -> list:
        pool = [u for u in army if u.addr != v.addr and u.addr not in self.hero_ids and u.addr not in self.sacrifice
                and u.type not in AIR_ONLY and u.type not in G.SIEGE and u.hp > 0
                and math.hypot(u.x - v.x, u.y - v.y) <= CULL_KILL_R]
        return sorted(pool, key=lambda u: math.hypot(u.x - v.x, u.y - v.y))[:CULL_KILLERS]

    def _cull_start(self, world, army) -> bool:
        if not CULL_ENABLED:
            return False
        if world.t - self.cull_done_at < CULL_GAP_S or self.state not in ("home", "creep"):
            return False
        if world.tier < CULL_MIN_TIER or world.food_used < CULL_AT:
            return False
        want = next((c for c in self.food_blocked if c not in CULL_TYPES), None)
        if want is None:
            return False
        g, l = G.cost(want)
        # 英雄 / 复活被挡不看钱（复活价随等级变，这里算不准；英雄总值得腾地方，建造规划会攒钱）
        if not G.is_hero(want) and want != "hero" and (world.gold < g or world.lumber < l):
            self._count("强制A低级兵·钱不够换")         # 腾出来也买不起，不如留着它打仗
            return False
        cands = [u for u in army if u.type in CULL_TYPES and u.addr not in self.hero_ids and u.addr not in self.sacrifice
                 and not self._hostile_near(world, u, CULL_SAFE_R)]
        if not cands:
            self._count("强制A低级兵·没有能换的")
            return False
        v = min(cands, key=lambda u: (u.hp / max(u.hp_max, 1.0), u.addr))     # 先挑已经掉了血的
        killers = self._cull_killers(world, army, v)
        if not killers:
            self._count("强制A低级兵·身边没人动手")
            return False
        self.cull = dict(victim=v.addr, type=v.type, food=max(1, G.food(v.type)), want=want, t=world.t,
                         killers={u.addr for u in killers})
        self._count("强制A低级兵·开始")
        self._note(f"人口 {world.food_used}/{world.food_cap}（上限 {G.FOOD_SOFT_CAP}），{want} 被人口挡着、钱够："
                   f"让 {len(killers)} 个自己人强制攻击一个 {v.type}（血 {int(100 * v.hp / max(v.hp_max, 1.0))}%）腾人口")
        return True

    def _critter_hunt(self, world, army) -> list[Intent]:
        """要开的分矿附近的小动物（CRITTER_*）：派最近的英雄用攻击令打掉；附近有英雄但它正忙就等，附近一个英雄都没有才派最近的兵。"""
        live = [c for c in self.critters if c.hp > 0]
        if not live or self.state not in ("home", "creep"):
            self.critter_hunter = None
            return []

        def reach(u):
            return min(math.hypot(c.x - u.x, c.y - u.y) for c in live)

        heroes = [u for u in army if u.addr in self.hero_ids and reach(u) <= CRITTER_HUNT_R]
        free = [u for u in heroes if u.hp >= CRITTER_HERO_HP * max(u.hp_max, 1.0) and not self._hostile_near(world, u, CRITTER_SAFE_R)]
        if heroes and not free:
            self._count("小动物·英雄在忙，等它")
            return []
        if not heroes:
            free = [u for u in army if u.addr not in self.hero_ids and u.addr not in self.sacrifice
                    and u.type not in AIR_ONLY and u.type not in G.SIEGE and reach(u) <= CRITTER_HUNT_R
                    and not self._hostile_near(world, u, CRITTER_SAFE_R)]
        if not free:
            self._count("小动物·附近没人")
            return []
        hunter = next((u for u in free if u.addr == self.critter_hunter), None) or min(free, key=reach)
        c = min(live, key=lambda c: math.hypot(c.x - hunter.x, c.y - hunter.y))
        if hunter.addr != self.critter_hunter:
            self._count("小动物·派" + ("英雄" if hunter.addr in self.hero_ids else "兵"))
            self._note(f"分矿落点附近有小动物 {c.type} ({int(c.x)},{int(c.y)})，派 {hunter.type} 用攻击令打掉（它挡着大厅地基）")
        self.critter_hunter = hunter.addr
        return [Intent("force_attack", [hunter], c.x, c.y, c, why=f"清掉分矿落点上的小动物（{c.type}）")]

    def _recycle(self, world, home) -> list:
        """低级兵喂野怪腾人口（RECYCLE_* 那段）。名单一旦定下，送到死为止；营地没了换一个，找不到就放回来。"""
        alive = {u.addr for u in world.my_army}
        self.sacrifice &= alive
        if not self.sacrifice:
            self.sac_camp = None
            if (self.state in ("attack", "gather") or world.tier < RECYCLE_MIN_TIER
                    or world.food_used < RECYCLE_FOOD or world.gold < RECYCLE_GOLD):
                return []
            low = sorted((u for u in world.my_army if u.type in RECYCLE_TYPES and u.addr not in self.cull_ids),
                         key=lambda u: u.hp)
            want = []
            freed = 0
            for u in low:
                if world.food_used - freed <= RECYCLE_TO:
                    break
                want.append(u)
                freed += max(1, G.food(u.type))
            if not want:
                return []
            # 先找最近的、至少打得死 min(3, 要送的数) 个的营地，再按它打得死多少个来送（分批喂）
            camp = self._sac_camp(world, want[:min(3, len(want))])
            if camp is None:
                self._count("喂野怪·没有打得死它们的营地")
                return []
            pick, lv = [], 0
            for u in want:
                l = int(G.stat(u.type, "level", 1))
                if (lv + l) * RECYCLE_CAMP_MULT > camp["strength"]:
                    break
                pick.append(u)
                lv += l
            freed = sum(max(1, G.food(u.type)) for u in pick)
            self.sacrifice, self.sac_camp = {u.addr for u in pick}, camp
            self._count("喂野怪·派出")
            self._note(f"低级兵喂野怪腾人口：{len(pick)} 个（{freed} 人口），人口 {world.food_used}，营地强度 {camp['strength']:.0f}")
        units = [u for u in world.my_army if u.addr in self.sacrifice]
        cx, cy = self.sac_camp["x"], self.sac_camp["y"]
        if not any(c.hp > 0 and math.hypot(c.x - cx, c.y - cy) <= CREEP_CAMP_RADIUS for c in world.creeps):
            camp = self._sac_camp(world, units)
            if camp is None:
                self._count("喂野怪·营地没了、放回来")
                self.sacrifice, self.sac_camp = set(), None
                return []
            self.sac_camp = camp
            cx, cy = camp["x"], camp["y"]
        return [Intent("attack_move", units, cx, cy, why=f"低级兵喂野怪腾人口（{len(units)} 个）")]

    def _sac_camp(self, world, units) -> dict | None:
        """打得死这批兵的营地：强度 >= 它们等级和 × RECYCLE_CAMP_MULT、离敌方建筑 ENEMY_BASE_AVOID 外、不是正在打的那个，取离它们最近的。"""
        need = RECYCLE_CAMP_MULT * sum(int(G.stat(u.type, "level", 1)) for u in units)
        cx, cy = centroid(units)
        best, best_d = None, float("inf")
        for camp in creep_camps(world):
            if camp["strength"] < need or any(c.addr in self.camp_ids for c in camp["units"]):
                continue
            if any(math.hypot(b.x - camp["x"], b.y - camp["y"]) < ENEMY_BASE_AVOID for b in world.enemy_buildings):
                continue
            d = math.hypot(camp["x"] - cx, camp["y"] - cy)
            if d < best_d:
                best, best_d = camp, d
        return best

    def _fight(self, world, army):
        """哪里在交战：离我方任何一个人 FIGHT_R 内的敌方作战单位按 1500 聚团，取强度最大的那团。
        -> (x, y, 强度, 那团敌人) 或 None（没有、或最大那团强度 < FIGHT_MIN）。野怪不算（world.creeps 另算）。"""
        foes = [f for f in world.enemy_fighters
                if f.hp > 0 and any(math.hypot(f.x - u.x, f.y - u.y) <= FIGHT_R for u in army)]
        best = None
        while foes:
            seed = foes.pop(0)
            grp = [seed] + [f for f in foes if math.hypot(f.x - seed.x, f.y - seed.y) <= amai.ARMY_RADIUS]
            foes = [f for f in foes if f not in grp]
            s = amai.strength(grp, world.levels)
            if best is None or s > best[2]:
                fx, fy = centroid(grp)
                best = (fx, fy, s, grp)
        if best is None or best[2] < FIGHT_MIN:
            return None
        return best

    def _support(self, world, army, home) -> list | None:
        """有交战就让 SUPPORT_R 内的人（英雄 HERO_SUPPORT_R 内）都攻击移动过去；合起来也打不过（AMAI 撤退判据）就不调人去送，
        交给毫秒层拉人 / 进攻状态的整队撤退。返回 None = 没有要支援的交战。"""
        self.fight_lost = False
        fight = self._fight(world, army)
        if fight is None and world.t - self.support_seen <= 8:
            lingering = [f for f in world.enemy_fighters if f.addr in self.support_ids and f.hp > 0
                         and any(dist(f,u) <= 1200 for u in army)]
            if lingering:
                fx, fy = centroid(lingering)
                fight = fx, fy, amai.strength(lingering, world.levels), lingering
        elif fight is not None:
            self.support_seen = world.t
            self.support_ids = {f.addr for f in fight[3]}
        if fight is None:
            return None
        fx, fy, fs, grp = fight
        # 没在出击（home）时，交战点在对方地盘上（离对方建筑比离我方近、又在 DEFENSE_LEASH_R 外）= 有人追出去了：
        #   接触着的收回来，不派援兵过去（1-317 16:50 骑士、狮鹫追到对方家里被围死，见 DEFENSE_LEASH_R 上面那段）
        if self.state == "home" and world.my_buildings:
            d_mine = min(math.hypot(b.x - fx, b.y - fy) for b in world.my_buildings)
            d_theirs = min((math.hypot(b.x - fx, b.y - fy) for b in world.enemy_buildings), default=1e9)
            if d_mine > DEFENSE_LEASH_R and d_theirs < d_mine:
                front = [u for u in army if any(math.hypot(u.x - f.x, u.y - f.y) <= FIGHT_R for f in grp)]
                self._count("交战·追到对方地盘·收回来")
                return ([Intent("move", front, home.x, home.y, why="追到对方地盘了，不打，收回来", control="skirmish")]
                        if front else None)
        regular = [u for u in army if u.addr not in self.hero_ids
                   and math.hypot(u.x - fx, u.y - fy) <= SUPPORT_R]
        heroes = [u for u in army if u.addr in self.hero_ids
                  and math.hypot(u.x - fx, u.y - fy) <= HERO_SUPPORT_R]
        safe_heroes, isolated_heroes = [], []
        for hero in heroes:
            escorts = [u for u in regular if not is_summon(u.type)
                       and u.hp > .35 * max(1, u.hp_max)
                       and dist(u, hero) <= HERO_SUPPORT_ESCORT_R]
            if len(escorts) >= HERO_SUPPORT_ESCORT_MIN:
                safe_heroes.append(hero)
            elif math.hypot(hero.x - fx, hero.y - fy) <= HERO_SUPPORT_ESCORT_R:
                isolated_heroes.append(hero)
        sup = regular + safe_heroes
        # 1-215 20:01：主力在 4500~5000 外的基地，两英雄在中场；旧 HERO_SUPPORT_R=8000
        # 让英雄单独组成 sup，账面 10 > 敌先头 6 后还主动攻击移动进 19 人主力。英雄没有两名
        # 非英雄护卫时只往真正主力靠拢，绝不以“支援”为名开一场英雄单打。
        if isolated_heroes and not sup:
            body = [u for u in army if u.addr not in self.hero_ids and not is_summon(u.type)]
            force = main_force(body, world.me, world.levels)
            rx, ry = ((force.x, force.y) if force is not None else (home.x, home.y))
            self._count("交战·英雄无护卫先回主力")
            return [Intent("move", isolated_heroes, rx, ry,
                           why="英雄附近出现敌军但护卫未到，先退向主力", control="skirmish")]
        mine = amai.strength(sup, world.levels)
        # ★ 09-21 3-037 29:45：一个我方单位留在敌方二矿，顶着敌军打塔直到阵亡，没有任何层把它收回来。
        #   实机：1762 秒毫秒层叫撤、主力回家；1766 秒「支援交战（敌 1 个，强度 5，我方 2）·增援接战」把留在敌方分矿边的
        #   一个兵派回去打。_persistent_loss 要「持续输 6 秒」才算输 —— 那是给**已经在打**的仗防摇摆的，
        #   一场还没开始的支援套用它，等于先把人送进去、6 秒后再说打不过（而且判输之后没人再管它）。
        #   开始一场支援必须当下就够线；已经在打的照旧按持续判输。
        # ★ 09-22 打不打只问 fightcall（校准胜率三档，和回防同一份，见 brain/fightcall.py）：
        #   新的一场：压上才去；观望 = 接触着的人先退回主力那边、没人接触就不去；放弃 = 整队撤。
        #   已经在打的：持续跌破放弃线（_persistent_loss，防摇摆）或毫秒层在这片战场叫过撤 -> 整队撤。
        #   原来毫秒层的叫撤在这里只有「在场人数 >= 全军一半」才认（_take_signal），局部输了照样派援兵 —— 撤了又被派回去。
        call = FC.judge(sup, grp, world.levels, where="field", lost=FC.recently_lost(self.lost_at, world.t, fx, fy))
        self._count(call.key)
        fresh = world.t - self.support_sent_at > SUPPORT_FRESH_S
        if sup and call.lost:
            self._count("交战·毫秒层叫过撤·不再派")
            self._note(f"交战：毫秒层刚在这里叫过撤（{call.why}），不再派人，整队撤")
            self.fight_lost = True
            return None
        if sup and fresh and call.band == "giveup":
            self._count("交战·开打前就打不过·不派")
            self.fight_lost = True
            return None
        if not sup or self._persistent_loss("support", world.t, mine, fs, SUPPORT_MIN_RATIO):
            self._count("交战·合起来也打不过")
            self.fight_lost = True
            return None
        if fresh and call.band == "hold":
            held = self._support_hold(world, sup, grp, fs, call)
            if held is None:
                self.fight_lost = True            # 该来的都在这儿了，还是不够：不在劣势里硬打，整队撤
            return held
        split = self._support_split(world, sup, grp, fs)
        if split is not None:
            return split
        self.support_sent_at = world.t
        self._count("交战·全员支援")
        out = battle_intents(self.engagement, sup, grp, world.t, fx, fy,
                             f"支援交战（敌 {len(grp)} 个，强度 {fs:.0f}，我方 {mine:.0f}）", world.levels)
        if isolated_heroes:
            force = main_force(regular, world.me, world.levels)
            rx, ry = ((force.x, force.y) if force is not None else (home.x, home.y))
            out.append(Intent("move", isolated_heroes, rx, ry,
                              why="支援交战·英雄先与护卫会合", control="skirmish"))
            self._count("交战·英雄无护卫先回主力")
        return out

    @staticmethod
    def _eta(u, grp) -> float:
        """游戏秒：走到打得到那团敌人里最近一个的地方要多久（按兵种射程、移速）。"""
        d = min(math.hypot(u.x - f.x, u.y - f.y) for f in grp)
        reach = float(G.stat(u.type, "range", 100) or 100)
        spd = float(G.stat(u.type, "spd", 270) or 270)
        return max(0.0, d - reach) / max(spd, 100.0)

    def _support_split(self, world, sup, grp, fs) -> list | None:
        """合起来打得过，但 SUPPORT_ARRIVE_S 内赶得到的那些人撑不住 -> 前面的先退回去跟援兵汇合、援兵在汇合点接应（见 SUPPORT_ARRIVE_S）。
        返回 None = 现在就在打的人够了（或者大家都还没到、一起走过去），照原来的全员支援。"""
        now_team = [u for u in sup if self._eta(u, grp) <= SUPPORT_ARRIVE_S]
        late = [u for u in sup if u not in now_team]
        if not now_team or not late:
            self.support_regroup_at = -1e9
            return None
        regrouping = world.t - self.support_regroup_at <= SUPPORT_FRESH_S
        # 前面这几个人自己够不够「压上」（fightcall 同一条线）；已经在汇合了要够线 × SUPPORT_REGROUP_HYST 才改回就地打
        now_call = FC.judge(now_team, grp, world.levels, where="field")
        if now_call.band == "commit" and (not regrouping or now_call.ratio >= FC.R_EVEN * SUPPORT_REGROUP_HYST):
            self.support_regroup_at = -1e9
            return None
        return self._regroup(world, now_team, late, grp, fs, now_call.ours, regrouping, "前面的人撑不到援兵")

    def _support_hold(self, world, sup, grp, fs, call) -> list | None:
        """一场新的交战判「观望」（合起来也到不了压上那条线）：不压上去。接触着的人先退回主力那边；
        该来的都已经在这儿了（没有在路上的援兵）返回 None —— 调用方整队撤，不在劣势里硬打。"""
        now_team = [u for u in sup if self._eta(u, grp) <= SUPPORT_ARRIVE_S]
        late = [u for u in sup if u not in now_team]
        if not late:
            self._count("交战·观望·没有援兵·整队撤")
            self._note(f"交战：敌 {len(grp)} 个，{call.why}，援兵也没有 —— 不在劣势里硬打，整队撤")
            return None
        if not now_team:
            return []
        regrouping = world.t - self.support_regroup_at <= SUPPORT_FRESH_S
        return self._regroup(world, now_team, late, grp, fs, amai.strength(now_team, world.levels), regrouping,
                             f"合起来也不够压上（{call.why}）")

    def _regroup(self, world, now_team, late, grp, fs, now_s, regrouping, reason) -> list:
        """接触着的人退到援兵主力往前 SUPPORT_MEET_FRAC 的汇合点，援兵在汇合点接应。"""
        body = main_force(late, world.me, world.levels)
        lx, ly = (body.x, body.y) if body is not None else centroid(late)
        nx, ny = centroid(now_team)
        mx, my = lx + (nx - lx) * SUPPORT_MEET_FRAC, ly + (ny - ly) * SUPPORT_MEET_FRAC
        if not regrouping:
            self._note(f"交战：敌 {len(grp)} 个（强度 {fs:.0f}），{reason}：{SUPPORT_ARRIVE_S:.0f} 秒内赶得到的我方 {len(now_team)} 个（强度 {now_s:.0f}），"
                       f"援兵 {len(late)} 个还在路上 —— 前面的先退回去汇合（{int(mx)},{int(my)}），到齐了一起打")
        self.support_regroup_at = world.t
        self._count(f"交战·{reason.split('（')[0]}·先退回去汇合")
        why = f"支援交战（敌 {len(grp)} 个，强度 {fs:.0f}；{SUPPORT_ARRIVE_S:.0f} 秒内赶得到的我方只有 {now_s:.0f}）"
        # control=skirmish：交战优先级（76）高过毫秒层补攻击令（55）—— 1-304 就是撤退令被补攻击令盖回去的
        return [Intent("move", now_team, mx, my, why=why + "·前面的人先退回去跟援兵汇合", control="skirmish"),
                Intent("attack_move", late, mx, my, why=why + "·援兵在汇合点接应", control="skirmish")]

    def _fight_retreat(self, world, army, home) -> list:
        """交战合起来也打不过：**所有人**回家（09-22 设计要求：判定打不过时全部单位一起撤，不留任何单位在场白白阵亡）。
        ★ 09-22：撤退令走交战优先级（control=skirmish，76）。1-304 14:05 掉在东边的火枪手和两个水元素收到回家令（「集结」「落单归队」，
          普通令 50）的同一秒，毫秒层「补攻击令」（55）又把它们派去打 13 个人，三个全死 —— 那两条令另外改成了 control=hold（58），见 runtime._do；
          这里是真判了打不过的撤退，给得更高。
          打野时撞上对面的兵原来走 _leave_camp —— 那是「从野怪身边后撤 1000」，对面的兵不是野怪，照样追着打；现在也回家，营地记进跳过名单。
          出击状态 home 原来判了打不过也没人撤（_step 只在 gather/creep 调这里）—— 读代码发现的口子，1-304 没走到这一步（整局 0 次判打不过）。"""
        self._count(f"交战·打不过撤·{self.state}")
        self._note(f"交战打不过，整队回家（原来在 {self.state}，{len(army)} 个）")
        if self.camp_ids and self.camp:
            cx, cy = self.camp["x"], self.camp["y"]
            self.skip_camps = [s for s in self.skip_camps if s[2] > world.t] + [(cx, cy, world.t + CREEP_SKIP_S)]
            self.creep_focus_id = None
        self.state, self.target, self.retreat_at, self.camp_ids = "retreat", None, world.t, set()
        return [Intent("move", army, home.x, home.y, why="交战打不过，整队回家", control="skirmish")]

    def _wait_heroes(self, army, cx, cy, x, y, target, why) -> list | None:
        """行军时（主力离目标 > COHESION_MIN）英雄比主力离目标远 HERO_LAG_R 以上：英雄照走，其余的攻击移动到
        「英雄往目标方向 HERO_LEAD」的点等着。英雄离主力 > HERO_WAIT_MAX（刚复活还在家）不等。"""
        if math.hypot(cx - x, cy - y) <= COHESION_MIN:
            self.hero_wait_at = -1.0
            return None
        hs = [u for u in army if u.addr in self.hero_ids and math.hypot(u.x - cx, u.y - cy) <= HERO_WAIT_MAX]
        if not hs:
            self.hero_wait_at = -1.0
            return None
        hx, hy = centroid(hs)
        if math.hypot(hx - x, hy - y) - math.hypot(cx - x, cy - y) <= HERO_LAG_R:
            self.hero_wait_at = -1.0
            return None
        if self.hero_wait_at < 0:
            self.hero_wait_at = self.now
        if self.now - self.hero_wait_at >= 12.0:
            self._count("行军·等英雄超时·主力继续")
            return None
        d = math.hypot(x - hx, y - hy) or 1.0
        wx, wy = hx + (x - hx) / d * HERO_LEAD, hy + (y - hy) / d * HERO_LEAD
        rest = [u for u in army if u not in hs]
        self._count("行军·等英雄跟上")
        out = [Intent("attack_move", hs, x, y, target, why)]
        if rest:
            out.append(Intent("attack_move", rest, wx, wy, None, f"{why}·等英雄跟上"))
        return out

    @staticmethod
    def _alive(world, target) -> bool:
        return target is not None and any(u is target or getattr(u, "addr", None) == getattr(target, "addr", -1) for u in world.enemies)

    def _timing_ok(self, world) -> bool:
        return world.food_used >= 40 or len(world.my_heroes) >= 2 or any(world.levels.get(h.addr, 1) >= 3 for h in world.my_heroes)

    def _set_target(self, tgt, kind: str, origin: tuple[float, float] | None = None) -> None:
        self.target, self.target_kind = tgt, kind
        self.last_x, self.last_y = tgt.x, tgt.y
        if origin is not None:
            ox, oy = origin
            dx, dy = tgt.x-ox, tgt.y-oy
            d = math.hypot(dx, dy)
            advance = min(GATHER_FORWARD, max(0.0, d-GATHER_STANDOFF))
            self.gather_xy = ((ox + dx/d*advance, oy + dy/d*advance) if d >= 1.0 else (ox, oy))
        else:
            self.gather_xy = None
        self.search_left = SEARCH_TRIES
        self.progress_at, self.progress_dist, self.progress_hp = self.now, float("inf"), float("inf")
        self.progress_foe, self.progress_blds = float("inf"), 1 << 30
        self.hero_wait_at = -1.0


@dataclass
class _Pt:
    x: float
    y: float


@dataclass
class Defense:
    """守家（ARMY_TRACK town_threatened）：每次 step 看最受威胁的我方城镇。"""
    preserve_since: float = -1
    preserve_town: int | None = None
    preserve_point: tuple[float, float] | None = None
    decisions: int = 0
    counts: dict = field(default_factory=dict)
    engagement: Engagement = field(default_factory=Engagement)
    lost: tuple | None = None        # 运行时设：毫秒层最近一次叫撤 (游戏秒, x, y)，见 DEFENSE_LOST_HOLD_S

    def _reset_regroup(self) -> None:
        self.preserve_since = -1
        self.preserve_town = None
        self.preserve_point = None

    @staticmethod
    def _fallback(town, fx: float, fy: float) -> tuple[float, float]:
        """大厅背向来敌的一侧；敌我重合时用固定向下方向，保证仍有一个确定集结点。"""
        dx, dy = town.x-fx, town.y-fy
        n = math.hypot(dx, dy)
        if n < 1.0:
            dx, dy, n = 0.0, -1.0, 1.0
        return town.x + dx/n*DEFENSE_FALLBACK_OFFSET, town.y + dy/n*DEFENSE_FALLBACK_OFFSET

    def step(self, world, main_hall) -> tuple[str, list[Intent]]:
        self.decisions += 1
        mine = [tw for tw in world.towns if tw.owner == world.me]
        if not mine:
            self._reset_regroup()
            return "none", []
        acc = amai.accepted_threat(world.t)
        # 一次集结期间锁住同一座基地。069/071/075 局威胁中心在主矿、分矿之间切换，
        # 旧代码每拍重算退点，命令目标最大跳了 6,000+，部队一直重新寻路。
        locked = next((tw for tw in mine if getattr(tw.hall, "addr", None) == self.preserve_town), None)
        town = locked if locked is not None and locked.threat > acc else max(mine, key=lambda tw: tw.threat)
        if town.threat <= 0:
            self._reset_regroup()
            return "none", []
        defenders = world.my_army + world.my_heroes + world.my_militia
        own = amai.strength(defenders, world.levels)
        hurt_heroes = {id(h) for h in world.my_heroes if h.hp_max > 0 and h.hp < HERO_RETREAT_HP * h.hp_max}
        mode = amai.defend_mode(town.threat, acc, amai.player_threat(own, world.aggression))
        self.counts[mode] = self.counts.get(mode, 0) + 1
        fx, fy = (town.threat_army.x, town.threat_army.y) if town.threat_army else (town.x, town.y)
        if mode == "minor":
            fighting = bool(self.engagement.assignments) and any(dist(u,f) <= 900 for u in defenders for f in world.enemy_fighters)
            if not fighting:
                self._reset_regroup()
                return mode, []
        fighters = [u for u in defenders if id(u) not in hurt_heroes]
        foes = [f for f in world.enemy_fighters if f.hp > 0 and math.hypot(f.x-fx,f.y-fy) <= amai.ARMY_RADIUS]
        # 只打我方建筑 DEFENSE_LEASH_R 以内的敌人（见 DEFENSE_LEASH_R 上面那段）：还在路上的不出去迎，退走的不追
        home_foes = [f for f in foes if any(math.hypot(b.x - f.x, b.y - f.y) <= DEFENSE_LEASH_R for b in world.my_buildings)]
        if not home_foes:
            self._reset_regroup()
            if mode == "minor":
                return mode, []
            # 威胁还在（大军在路上 / 刚退出去）：在家等，追出去的收回来；返回 defend 让出击层这一拍别再派人出门
            self.counts["回防·敌人不在家门口·在家等"] = self.counts.get("回防·敌人不在家门口·在家等", 0) + 1
            out_there = [u for u in fighters if math.hypot(u.x - town.x, u.y - town.y) > DEFENSE_LEASH_R]
            return "defend", ([Intent("move", out_there, town.x, town.y, why="回防：敌人不在家门口，不出去迎、不追，回来等",
                                      control="hold")] if out_there else [])
        foes = home_foes
        fx, fy = centroid(foes)
        # 打不过就不送：只问 fightcall（校准过的胜率分三档，见 brain/fightcall.py 文件头）。
        #   AMAI 的威胁模式只留「小骚扰不理」（minor）这一步；守得住 / 守不住改由 fightcall 判 ——
        #   原来的 player_threat（我方强度 × 进攻性倍数）没校准过，1-317 21 分我方 51.5 对 71~75 还判「守得住」；
        #   毫秒层叫过撤也算「放弃」（原来回防不看它，拉人的保护期 8 墙钟秒一过又把人派回去）。
        #   分矿：到不了「压上」就放弃，保兵回主基地；主基地：放弃档走下面「退到大厅背后集结、到齐 / 敌人拆建筑才反打」那条路。
        main = world.main_town()
        is_main = (main is None or town.hall is None or main.hall is None
                   or getattr(main.hall, "addr", None) == getattr(town.hall, "addr", None))
        # ★★ 09-23 1-439：敌方拆我方分矿时部队不敢上前回防 —— 我方的塔在这场仗里开着火，判据却一点不算它们
        #   （实测那一刻 21% 差 3 个百分点没到压上线，而分矿旁两座箭塔还活着：算上就是 27%）。
        my_towers = [b for b in world.my_buildings if b.hp > 0 and b.type in ARMED_BUILDINGS
                     and math.hypot(b.x - fx, b.y - fy) <= DEFENSE_TOWER_R]
        call = FC.judge(fighters, foes, world.levels, where="main" if is_main else "expansion",
                        lost=FC.recently_lost(self.lost, world.t, fx, fy), support=my_towers)
        if my_towers:
            self.counts["回防·算上我方塔"] = self.counts.get("回防·算上我方塔", 0) + 1
        self.counts[call.key] = self.counts.get(call.key, 0) + 1
        if not is_main and call.band != "commit" and fighters:
            self._reset_regroup()
            self.counts["回防·分矿守不住·保兵回主基地"] = self.counts.get("回防·分矿守不住·保兵回主基地", 0) + 1
            # ★ 09-22 1-383 败局复盘：9 分钟分矿被 14 个兵压上，「分矿守不住，保兵回主基地」—— 分矿到主基地横穿半张地图，
            #   几个火枪手一路被追着打死在路上（死时身边敌 8 我 1、敌 6 我 0）。1 号实例这条令 371 次单位·令对上战斗帧：
            #     下令时 800 内已经有敌方兵（贴上了）  128 次，25 秒内死 96（75%）
            #     下令时还没贴上                       243 次，25 秒内死 39（16%）
            #   已经贴上的往回跑就是被追着砍（和野外交战的撤退线同一个结论，见 skirmish.WITHDRAW_RATIO）。
            #   现在只让还没贴上的回主基地；贴上的就地打（旁边有大厅 / 塔），毫秒层的保命照旧能把快死的拉走（它的租约比这里高）。
            engaged = {u.addr for u in fighters
                       if any(math.hypot(u.x - f.x, u.y - f.y) <= EXPANSION_ENGAGED_R for f in foes)}
            leaving = [u for u in fighters if u.addr not in engaged]
            staying = [u for u in fighters if u.addr in engaged]
            out = ([Intent("move", leaving, main.x, main.y, control="skirmish",
                           why=f"分矿守不住（{call.why}），保兵回主基地")] if leaving else [])
            if staying:
                self.counts["回防·分矿守不住·已贴上的就地打"] = self.counts.get("回防·分矿守不住·已贴上的就地打", 0) + 1
                out += [replace(it, control="skirmish") for it in battle_intents(
                    self.engagement, staying, foes, world.t, fx, fy, f"分矿守不住·已经贴上的就地打（{call.why}）", world.levels)]
            return "toostrong", out
        # 主基地：压上 = 直接打；观望 / 放弃 = 先退到大厅背后集结，到齐 / 敌人拆建筑才反打（027 局「逐个接战 12 秒死 10 个」）
        if call.band != "commit" and mode != "toostrong":
            mode = "toostrong"
            self.counts["回防·主基地打不过·先集结"] = self.counts.get("回防·主基地打不过·先集结", 0) + 1
        elif call.band == "commit" and mode == "toostrong":
            mode = "defend"                          # AMAI 说守不住、校准胜率说守得住：听校准的
            self.counts["回防·校准胜率守得住"] = self.counts.get("回防·校准胜率守得住", 0) + 1
        trail = {u.addr for u in world.my_heroes}
        # 027 局证明压倒性劣势不能逐个接战；069~081 又证明“只要仍然 toostrong 就一直走”会变成让家。
        # 因此退点在一次防守内固定，只等待短窗口；70% 战力到齐、敌人压到点上或超时，任一成立就反打。
        hall_frac = (town.hall.hp / town.hall.hp_max
                     if town.hall is not None and town.hall.hp_max > 0 else 0.0)
        if mode == "toostrong" and hall_frac > DEFENSE_LAST_STAND_HP:
            town_id = getattr(town.hall, "addr", None)
            if self.preserve_since < 0 or self.preserve_town != town_id or self.preserve_point is None:
                self.preserve_since = world.t
                self.preserve_town = town_id
                self.preserve_point = self._fallback(town, fx, fy)
            ax, ay = self.preserve_point
            total = amai.strength(fighters, world.levels)
            ready = [u for u in fighters if math.hypot(u.x-ax, u.y-ay) <= DEFENSE_REGROUP_R]
            ready_power = amai.strength(ready, world.levels)
            ready_share = ready_power / max(0.01, total)
            counter_core = (len(ready) >= DEFENSE_COUNTER_MIN_UNITS
                            and ready_power >= DEFENSE_COUNTER_MIN_STRENGTH)
            formed = counter_core and ready_share >= DEFENSE_REGROUP_READY
            # 前排已经接触正是需要先退一步的触发条件；只有敌军追到锁定集结点才结束等待。
            breached = any(math.hypot(f.x-ax, f.y-ay) <= DEFENSE_BREACH_R for f in foes)
            elapsed = world.t - self.preserve_since
            timeout_ready = elapsed >= DEFENSE_REGROUP_S and counter_core
            # 敌人已贴到我方建筑就是在拆家：无论是否凑够反打核心都立即接战，不能让兵活着看家被拆。
            base_under_fire = any(math.hypot(b.x-town.x, b.y-town.y) <= 1800
                                  and any(math.hypot(f.x-b.x, f.y-b.y) <= 700 for f in foes)
                                  for b in world.my_buildings)
            breach_ready = (breached and counter_core) or base_under_fire
            if not (formed or timeout_ready or breach_ready):
                if elapsed >= DEFENSE_REGROUP_S:
                    # 第一次反打后的残兵/新兵不能继承旧超时。重新给它们一段集结时间；大厅到残血线仍会最后决战。
                    self.preserve_since = world.t
                    self.counts["回防·兵力过少继续集结"] = self.counts.get("回防·兵力过少继续集结", 0) + 1
                self.counts["回防·压倒劣势借建筑集结"] = self.counts.get("回防·压倒劣势借建筑集结", 0) + 1
                return "toostrong", ([Intent("move", fighters, ax, ay, why="回防·压倒劣势先到建筑后集结")]
                                     if fighters else [])
            reason = ("敌军正在拆建筑" if base_under_fire else "敌军压到集结点" if breach_ready else
                      "主力到齐" if formed else "集结限时到")
            self.counts[f"回防·{reason}反打"] = self.counts.get(f"回防·{reason}反打", 0) + 1
            # 反打是一次新的承诺。若这一轮没解决威胁，下一批增援必须重新完成集结，不能永久继承第一次的超时。
            self.preserve_since = world.t
            return "defend", heroes_trail(battle_intents(self.engagement, fighters, foes, world.t, fx, fy,
                                                         f"回防·{reason}反打", world.levels), trail, fx, fy)
        if mode == "toostrong":
            self.counts["回防·大厅残血最后决战"] = self.counts.get("回防·大厅残血最后决战", 0) + 1
        self._reset_regroup()
        return "defend", heroes_trail(battle_intents(self.engagement, fighters, foes, world.t, fx, fy,
                                                     f"回防（威胁 {town.threat:.0f}）", world.levels), trail, fx, fy)


def heroes_trail(intents, hero_addrs, fx, fy):
    """回防行军（「增援接战」攻击移动）：非英雄前排离交战点还有 DEFENSE_HERO_TRAIL_R 以上时，英雄攻击移动到前排后面 BACKLINE_OFFSET，
    不当第一个进场的（1-317 21 分圣骑士走在最前面，到场就被晕、被集火）。已经被点名打目标的（在交战范围里）不动。"""
    out = []
    for it in intents:
        heroes = [u for u in it.units if u.addr in hero_addrs]
        front = [u for u in it.units if u.addr not in hero_addrs]
        if it.kind != "attack_move" or it.target is not None or not heroes or len(front) < 2:
            out.append(it)
            continue
        cx, cy = centroid(front)
        d = math.hypot(cx - fx, cy - fy)
        if d <= DEFENSE_HERO_TRAIL_R:
            out.append(it)
            continue
        bx, by = cx + (cx - fx) / d * BACKLINE_OFFSET, cy + (cy - fy) / d * BACKLINE_OFFSET
        out.append(Intent(it.kind, front, it.x, it.y, None, it.why, it.control))
        out.append(Intent("attack_move", heroes, bx, by, None, it.why + "·英雄跟在前排后面", it.control))
    return out


def armed_reach(b) -> float:
    """能打人的敌方建筑的危险半径：射程 + TOWER_CLEAR_PAD（塔不追，出了这个圈就安全）。"""
    return float(G.stat(b.type, "range", 800) or 800) + TOWER_CLEAR_PAD


def tower_exit(b, x, y, armed) -> tuple[float, float]:
    """从建筑 b 往 (x, y) 那一侧走到它射程外 100；落点又在另一座的射程里就再往外推（最多三次）。"""
    ex, ey = x, y
    for _ in range(3):
        d = math.hypot(ex - b.x, ey - b.y)
        if d < 1:
            ex, ey, d = b.x + 1.0, b.y, 1.0
        r = armed_reach(b) + 100.0
        if d < r:
            ex, ey = b.x + (ex - b.x) / d * r, b.y + (ey - b.y) / d * r
        nxt = next((o for o in armed if o is not b and math.hypot(o.x - ex, o.y - ey) <= armed_reach(o)), None)
        if nxt is None:
            break
        b = nxt
    return ex, ey


def hero_tower_guard(world, intents, counts=None) -> list:
    """英雄不进能打人的敌方建筑的射程（见 ARMED_BUILDINGS 下面那段）。只改英雄：
      * 打的目标 / 要去的点在射程里 -> 改成走到射程外（在英雄这一侧），不打；
      * 自己已经在射程里、这条令还是打 / 攻击移动 -> 同样先走出来（攻击移动会让引擎自己去打那座塔）；
      * 本来就是往射程外走的移动令 -> 不动。
    control 沿用原令（skirmish 的照样 76，别被补攻击令拽回去）；原来没有的给 hold（58 > 补攻击令 55）。"""
    armed = [b for b in world.enemy_buildings if b.hp > 0 and b.type in ARMED_BUILDINGS]
    if not armed or not intents:
        return intents

    def inside(x, y):
        return next((b for b in armed if math.hypot(b.x - x, b.y - y) <= armed_reach(b)), None)

    out = []
    for it in intents:
        heroes = [u for u in it.units if G.is_hero(u.type)] if it.kind in ("attack", "attack_move", "move") else []
        if not heroes:
            out.append(it)
            continue
        dest = (it.target.x, it.target.y) if it.target is not None else (it.x, it.y)
        dest_b = inside(*dest)
        moved = []
        for h in heroes:
            here_b = inside(h.x, h.y)
            if dest_b is None and (here_b is None or it.kind == "move"):
                continue
            b = dest_b if dest_b is not None else here_b
            moved.append((h, *tower_exit(b, h.x, h.y, armed), b))
        if not moved:
            out.append(it)
            continue
        ids = {h.addr for h, *_ in moved}
        rest = [u for u in it.units if u.addr not in ids]
        if rest:
            out.append(replace(it, units=rest))
        for h, ex, ey, b in moved:
            if counts is not None:
                k = "英雄不进塔的射程·" + ("目标在塔下" if dest_b is not None else "自己在塔下")
                counts[k] = counts.get(k, 0) + 1
            out.append(Intent("move", [h], ex, ey, None,
                              f"英雄不进{b.type}的射程（原令：{it.why}）", it.control or "hold"))
    return out


HEALER_FOLLOW_R = 600.0     # 交战里牧师离前排重心这么远以外才让它跟上（engagement.OWN_HEALERS）


def battle_intents(engagement, fighters, foes, now, x, y, why, levels=None):
    # ★ 09-22：牧师原来和别人一样被点名攻击，毫秒层又下治疗令，交战里「支援交战 <-> 牧师治疗」来回 132 次（6 局）。
    #   牧师不进 Engagement：加血归毫秒层（mod_combat 定向治疗 + 自动治疗），这里只在它掉队时叫它跟上前排。
    healers = [u for u in fighters if u.type in ENG.OWN_HEALERS]
    if healers and len(healers) < len(fighters):
        fighters = [u for u in fighters if u.type not in ENG.OWN_HEALERS]
    else:
        healers = []
    picked, marching = engagement.plan(fighters, foes, now, levels)
    groups = {}
    for u, target in picked:
        groups.setdefault(target.addr, (target, []))[1].append(u)
    out = [Intent("attack", units, t.x, t.y, t,
                  why + "·" + engagement.target_reasons.get(t.addr, "就近接战"))
           for t, units in groups.values()]
    if marching:
        # 增援去已接战的前沿，不把整支军队反复推向移动中的敌军重心。
        if picked:
            mx, my = centroid(marching)
            target = min((t for _,t in picked),key=lambda t:math.hypot(t.x-mx,t.y-my))
            x, y = target.x, target.y
        # 前沿点跟着目标跑会一两秒换一次（09-22：增援接战 -> 增援接战 换点 137 次），离上一个不到 MARCH_KEEP_R 就沿用
        mp = engagement.march_xy
        if mp is not None and math.hypot(mp[0] - x, mp[1] - y) <= ENG.MARCH_KEEP_R:
            x, y = mp
        else:
            engagement.march_xy = (x, y)
        out.append(Intent("attack_move", marching, x, y, why=why + "·增援接战"))
    if healers:
        cx, cy = centroid(fighters)
        far = [h for h in healers if math.hypot(h.x - cx, h.y - cy) > HEALER_FOLLOW_R]
        if far:
            out.append(Intent("move", far, cx, cy, why=why + "·牧师跟上前排（加血归毫秒层）"))
    return out

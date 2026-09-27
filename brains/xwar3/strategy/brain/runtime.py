# -*- coding: utf-8 -*-
"""新大脑的一局：感知 -> 世界状态 -> 任务调度（建造 / 英雄 / 伐木 / 集结点 / 守家 / 出击 / 骚扰 / 记录 / 局末）。

    brain = Brain(cmd, claims, inst_dir, profile=human.PROFILE)
    brain.play(session, clock, recorder)        # 一直跑到局末，返回 outcome

任务频率都是**游戏秒**（旧 PowerShell 策略层的拍数阈值隐含 7 游戏秒一拍，这里全部换算成游戏秒）。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from . import act as A
from . import amai
from . import offense
from . import config as CFG
from . import live
from . import races as R
from . import gamedata as G
from . import opening as OP
from .build import Planner, Want
from .creepsplit import SplitCreep
from .harass import HarassManager
from . import coach as CH
from . import harvest as HV
from . import unitids as UID
from . import terrain as TR
from . import towerrush as TRUSH
from .jobs import Scheduler
from .perception import Perception, incoming
from .tables import amai_saturated, human, lumber_crew, wood_short
from . import tactics as T
from . import battlepower as BP
from .tactics import AttackSequence, Defense, Intent, creep_strength
from .recovery import RECOVERY_SAFE_R, SHOP_MAX, SHOP_MAX_IDLE, ArmyRecovery
from .powerbuild import HUMAN_BUILD, PowerBuild, constructing, site_full
from .repair import BuildingRepair
from .steptime import NullTimer
from .unstick import StuckWatch
from .world import build_world

PRODUCERS = {"hpea": ("htow", "hkee", "hcas"), "hfoo": ("hbar",), "hrif": ("hbar",), "hkni": ("hbar",),
             "hmpr": ("hars",), "hsor": ("hars",), "hspt": ("hars",), "hmtm": ("harm",), "hgyr": ("harm",),
             "hgry": ("hgra",), "hdhw": ("hgra",)}
RESEARCHERS = {"Rhme": "hbla", "Rhar": "hbla", "Rhra": "hbla", "Rhla": "hbla", "Rhde": "hbar", "Rhlh": "hlum", "Rhac": "hlum"}
HALL_UP = {"hkee": "htow", "hcas": "hkee"}
ALTAR = "halt"
OK_VERDICTS = (0, 220)
EXP_GUARD_W = 150.0         # 挑分矿时野怪强度折成多少距离（见 _expansion_mine）
# ★★ 09-22 晚 开矿前先攒够自家战力（1-414）：敌人频繁骚扰时兵力不足守不住新矿（_expand_hold / _expansion_mine）
EXP_AHEAD_2 = 0.6           # 开第二座矿：我方作战强度（world.own_strength，含英雄）至少是对面的这么多倍
#   ★ 09-23 1.0 -> 0.6（1-418：分矿大厅周围没有障碍却一直造不下去）：
#     1.0 把自家二矿从第 29 拍拖到第 83 拍（5 -> 14 分钟），挡它的是「11 对 12」「22 对 27」这种差不多的局面。
#     这条要挡的是「对面门口开三矿」（1-414），二矿在自家这半边，只在明显打不过时缓一缓
EXP_AHEAD_3 = 1.3           # 第三座及以后（1-414 14 分钟：我方 9 兵 vs 对面 16 还去开，5 分钟后三矿丢光）
EXP_CALM_S = 60.0           # 家里被打（守家民兵那条判据成立）之后这么多游戏秒内不开矿
EXP_FOE_SIDE_AHEAD = 2.0    # 对面那半边的矿：我方强度至少是对面的这么多倍（对面主力基本打没了）才去占
DEATH_NEAR = 900.0          # 阵亡归类（_unit_losses）：死前这么近有野怪 = 打野
DEATH_FOE_NEAR = 1200.0     #   这么近有敌方作战单位/塔 = 打玩家（优先）
REVIVE_PRIO = 94            # 复活英雄：祭坛(96)/首英雄(95) 之后、兵营(90) 之前 —— 钱不够就停下攒，不让出兵把钱花光
DEAD_READ_EVERY = 30.0      # 游戏秒：见过的英雄都在场上时，死亡英雄表多久兜底读一次（中途接管、没见过它活着的英雄）
REST_HOME_R = 1500.0        # 养伤的兵（毫秒层 resting.json）离主基地这么远以外、这一拍又没人管 -> 先回家（找不到空地时）
REST_SPOT_R = 350.0         # 养伤空地（_rest_spot）：离它这么远以外的养伤兵走过去
REST_SPOT_EVERY = 30.0      # 养伤空地多少游戏秒重算一次
RALLY_HALL_R = 250.0        # 整队点令的目的地离主基地城堡这么近 = 「回城堡」，改去门前空地（_home_rally）
HOME_RECOVERY_R = 2000.0    # 家里补给：主基地这么近的单位算「家里这一群」（_home_recovery_step）
HOME_CARRIER_MIN = (0.10, 0.10)   # 家里补给的买家血量下限（英雄, 小兵）：在家安全，去藏宝室买卷轴不用血
# ★ 09-22 上面那片野开打后英雄被调走（1-332 9 分）。打野时必须有英雄在场吃经验、帮打、捡装备和书。
#   8:33 打上面那片野（离主基地约 1850，在 HOME_RECOVERY_R 圈里）刚开打，家里补给把圈里**所有人**当成「家里这一群」
#   从出击层拿走、挑大法师去藏宝室买卷轴 —— 出击层这一拍看不到英雄，「打野任务终止，先收回旧营地部队」。
#   正在打的营地 HOME_RECOVERY_MISSION_R 内、身边 RECOVERY_SAFE_R 内有野怪/敌人的，都不算「家里这一群」。
HOME_RECOVERY_MISSION_R = 1500.0
# ★ 09-22 （1-303）分矿落点上的小动物挡着大厅地基（证据见 tactics.CRITTER_*）。引擎不许把地基压在不归己方控制的单位身上，
#   下令当场退单，而退过的落点会被永久拉黑（bad_spots）—— 1-303 那座矿最好的历史落点就是这样没的。
CRITTER_R = 1200.0          # 要开的分矿这么近以内的小动物交给战术层去打（attack.critters）
CRITTER_BLOCK = 352.0       # 小动物离落点（切比雪夫）这么近 = 压着大厅地基：大厅占地 512 的一半 256 + 它一秒能走的 96
CRITTER_WAIT_S = 40.0       # 首选落点被压着时最多等这么久（等英雄去打掉）；过了就先用没被压着的点


def critter_blocked(spots, critters, r: float = CRITTER_BLOCK) -> set:
    """被小动物压着的落点（切比雪夫距离 < r：地基是方的）。"""
    return {p for p in spots if any(max(abs(c.x - p[0]), abs(c.y - p[1])) < r for c in critters)}


def revive_cost(code: str, level: int) -> tuple[int, int]:
    """复活价（估）：MiscGame.txt ReviveBaseFactor 0.4 + ReviveLevelFactor 0.1 ×（等级 - 1），HeroMaxReviveCostGold 700 封顶，木头 0。
    09-18 010409 局实测：两次复活令 3 秒内先后接下，金 400 -> 230，差 170 = 425 × 0.4。估高只会多攒一会儿，估低会被引擎退（记「复活·被退」）。"""
    base = G.cost(code)[0] if code and code != "hero" else 425
    return int(min(700, (base or 425) * (0.4 + 0.1 * max(0, level - 1)))), 0
PENDING_UNIT_S = 25.0          # 训练下令后多久内还算"在路上"（训练时间约 15~40 游戏秒；超时当没成）
PENDING_BUILD_S = 45.0
PENDING_HALL_UP_S = 150.0
PENDING_EXPAND_S = 90.0
PENDING_RESUME_S = 30.0
MILITIA_EXPAND_RESERVE_S = 180.0  # 224 局实机：清矿后因攒 450 金，85 游戏秒才下大厅；60 秒会把两名原民兵提前送回经济。
MILITIA_ARMY_NEAR = 1800.0        # 主力（英雄/兵）离分矿守卫这么近才号召民兵（2-038：没兵时民兵自己去打，45 秒打不完）
MILITIA_WINDOW_S = 40.0           # 民兵 45 游戏秒自动变回：走过去 + 主力和民兵合起来打完守卫要在这么久以内
MILITIA_REVERT_WAIT_S = 50.0      # 预留建矿的民兵不下「回到工作」、原地等到点变回（Amil 持续 45 秒，从变身起算）；超过这么久还是民兵就放弃预留
FIELD_HEAL_R = 3500.0             # 09-22 晚 野外就地治疗（_field_heal_spot）：带卷轴的英雄这么近的养伤兵去它身后，不回家
FIELD_HEAL_KEEP_R = 300.0         #   已经在集合点这么近就不重下
FIELD_HEAL_BACK = 300.0           #   集合点 = 英雄往家方向退这么远（卷轴 600 范围，毫秒层到齐判 500）
FIELD_HEAL_MIN_HOME = 2500.0      #   英雄离家这么近就不算「野外」（回家附近空地一样）
FIELD_HEAL_SAFE_R = 1500.0        #   英雄这么近有敌方作战单位 / 野怪就不在这儿治（先打完或先撤）
FIELD_HEAL_HERO_HP = 0.45         #   英雄自己血低于这个不当集合点（它自己也在撤）
LATE_FOOD_HOLD_S = 60.0           # 09-22 晚 收尾结束后人口上限 100 再留这么多游戏秒（food_soft_cap）
LATE_FOOD_BANK = 2000             # 09-23 存款到这么多也放开到 100（AMAI Upkeep.txt HIGH 的 resistance；1-416/418 金子堆到 4000~6000）
FINISH_OWN_MIN = 20.0             # 09-22 晚 收尾拆家（_finishing）：我方强度至少这么多
FINISH_FOE_FRAC = 0.15            #   对面作战单位强度不到我方这么多 = 没什么兵了，拆家不等治疗
HOME_MIL_R = 1600.0               # 09-22 晚 守家民兵（_home_militia_step）：敌方作战单位离我方大厅这么近 = 家被打
HOME_MIL_MIN_FOES = 3             #   至少这么多个（一两个侦察兵不值得停工）
HOME_MIL_RATIO = 1.5              #   守军对这批敌人的战力比低于这个就叫（battlepower.estimate）
HOME_MIL_WORKER_R = 1400.0        #   叫这座大厅这么近的农民（Amil 要跑进大厅变身）
HOME_MIL_RECALL_S = 50.0          #   民兵 45 游戏秒到点变回，敌人还在就再叫；同一个农民至少隔这么久
HOME_MIL_CLEAR_S = 5.0            #   敌人离开满这么久全部解除
MILITIA_STAY = 5                  # 清完分矿后留下几个（原来 2 个；09-22 起全留：1 个主建 + 其余协建，见 _militia_assist）
POWER_BUILD_COST = 0.15           # 人族协建：每多一个助手多花这座建筑造价的这么多（AbilityData.slk Ahrp DataC；DataD 协建速率 0.6）
SITE_STALL_S = 8.0             # 工地血量这么多游戏秒没涨 = 没人在盖
FAR_MINE_FOE_R = 2000.0        # 远矿兜底：矿离敌方大厅/敌军这么近就不派农民去（_far_mines）
FAR_MINE_CREEP_R = 900.0       # 矿边这么近有活着的野怪也不去 —— 农民过去就是送
# ★★ 09-21 二矿上的农民停止采集，采矿人数长期不足不补，木头紧缺时也不增派伐木。
#   根因：`job_gold` 和 `job_lumber` **都只从 `_idle_workers` 里拿人**。农民一旦全都在采集
#   （一部分金、一部分木），两边就再也不会互相调配 —— 矿上死了人、二矿刚建好、木头见底，
#   都只能等新农民出生或者有人自己变闲。28 局实测「采金·派出」只有 25.9 次/局，
#   而「矿上缺人却没人可派」这件事**一个计数都没有**，所以从来没人看见过。
REBALANCE_EVERY = 4.0          # 游戏秒：每这么久最多调一个人。
#   ⚠ 不能一拍把缺口全调过去：采集名单是滞后的（工人进矿后快照里看不见、采集技能目标走到矿边才设），
#     按滞后的数连续下令会自激 —— 策略层 v262 就是这么把采金调到 2~4 人的。
AMAI_HARVEST_RACES = frozenset({"human", "orc"})   # 采金/伐木走 AMAI 派工（brain/harvest.py）的种族；
#   暗夜（小精灵站着采、缠绕金矿、建筑会吃掉小精灵）和亡灵（伐木的是食尸鬼）机制不同，照旧走 job_gold / job_lumber
GOLD_KEEP = 2                  # 再缺木头也至少留这么多人采金（零收入是不可逆的）
LUMBER_KEEP = 1                # 反过来也留一个砍树的，否则一有金矿空位木头就彻底断供
MAP_ID_ECHO = "1002"
LEARNED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "learned.json")
# 高手录像的主基地落点（tools/make_pro_spots.py 生成）：按大厅坐标认出生点，见 _pro_spots
PRO_SPOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pro_spots.json")
PRO_HALL_TOL = 300.0


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def _morph_source(code: str) -> str:
    """建筑升级的源建筑（hgtw <- hwtw、uzg1 <- uzig）。查的是 AMAI StandardUnits 的 needed1 + needed3=UPGRADED；
    只有 AMAI 战略引擎那条路会用到，所以按需导入。"""
    try:
        from .amai_strategy import data as AD
        return AD.is_upgraded_from(code) or ""
    except Exception:  # noqa: BLE001
        return ""


# ★★ 09-22 分矿塔位置不对：至少要两座能攻击的塔，而原来唯一一座塔造在分矿大厅背后，起不到保护作用。
#   原来分矿塔的扇区朝**金矿**（大厅 -> 金矿 ±0.35~1.05 弧度）：1-399 分矿大厅 (-4352,-2624)、金矿在它西南，塔盖在正南 (-4352,-3104)，
#   而敌人从东北（地图中心 / 对面基地）过来 —— 塔在大厅背后。最近 16 局每局分矿只有 1 座哨塔、一次都没升级（哨塔不能攻击）。
#   现在：扇区中心取「金矿方向」和「来路方向」（最近看到的敌方大厅，没看到就朝地图中心）的角平分线 ——
#   两个方向夹角超过 TOWER_BISECT_MAX（金矿在大厅背后）就直接朝来路。塔的数量 / 升防御塔见 tables/human.py。
TOWER_RADII = (450.0, 600.0)
TOWER_OFFS = (0.0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05)
TOWER_BISECT_MAX = math.radians(120.0)
TOWER_FRONT_DEFAULT = (0.0, 0.0)      # 没看到敌方大厅：朝地图中心（terrain.open_ground front 同一个口径）


def expansion_tower_sector(hall, mine, foe_xy) -> list:
    """分矿塔的候选落点（按好坏排好）：围着「金矿方向和来路方向的角平分线」（夹角太大直接朝来路），半径 TOWER_RADII。"""
    am = math.atan2(mine.y - hall.y, mine.x - hall.x)
    ae = math.atan2(foe_xy[1] - hall.y, foe_xy[0] - hall.x)
    diff = (ae - am + math.pi) % (2 * math.pi) - math.pi
    base = am + diff / 2 if abs(diff) <= TOWER_BISECT_MAX else ae
    return [(A.snap32(hall.x + r * math.cos(base + off)), A.snap32(hall.y + r * math.sin(base + off)))
            for off in TOWER_OFFS for r in TOWER_RADII]


TOWN_NEAR_R = 1200.0                    # 「这座大厅旁边有几座」的计数圈（have_near_town）
FRONT_RADII = (1100.0, 950.0, 1050.0)   # 09-23 主基地门口的塔离大厅多远（AMAI front_base_distance 1100）；★ 必须全在 TOWN_NEAR_R 以内
FRONT_OFFS = (0.0, 0.25, -0.25, 0.5, -0.5)


def main_front_sector(hall, foe_xy) -> list:
    """主基地门口塔的候选落点（按好坏排好）：朝来路（最近的敌方大厅，没看到就朝地图中心），离大厅 FRONT_RADII。

    ⚠ 半径超出 TOWN_NEAR_R 的点不能给：盖成了也不计进「门口有几座」，愿望永远不满足，
      门口的好位置又被占完 -> 回落到通用候选，塔就摆到基地背后去了（1-429 东北角那两座）。"""
    ae = math.atan2(foe_xy[1] - hall.y, foe_xy[0] - hall.x)
    return [(A.snap32(hall.x + r * math.cos(ae + off)), A.snap32(hall.y + r * math.sin(ae + off)))
            for off in FRONT_OFFS for r in FRONT_RADII]


def completed(u) -> bool:
    """在建建筑血量没满：「见过满血就不是工地」第一版简化成血量 >= 97%（旧 PowerShell AI 踩过的坑：挨打的成品也会误判）。"""
    return u.hp_max > 0 and u.hp >= 0.97 * u.hp_max


@dataclass
class Brain:
    cmd: object
    claims: object
    inst_dir: str
    profile: dict = field(default_factory=lambda: dict(human.PROFILE))
    act: A.Actuator = None
    planner: Planner = field(default_factory=Planner)
    attack: AttackSequence = None
    defense: Defense = field(default_factory=Defense)
    recovery: ArmyRecovery = field(default_factory=ArmyRecovery)
    home_recovery: ArmyRecovery = field(default_factory=ArmyRecovery)   # 09-21 主力在外面忙时，家里的伤兵自己补给（_home_recovery_step）
    coach: CH.Coach = field(default_factory=CH.Coach)     # 运营顾问（本地大模型），见 brain/coach.py
    powerbuild: PowerBuild = field(default_factory=PowerBuild)
    repairs: BuildingRepair = field(default_factory=BuildingRepair)
    mine_guards: dict = field(default_factory=dict)
    harass: HarassManager = field(default_factory=HarassManager)
    splitcreep: SplitCreep = field(default_factory=SplitCreep)   # 第二支打野小队（brain/creepsplit.py，09-23 分两路练级）
    stuck: StuckWatch = field(default_factory=StuckWatch)   # 英雄被自己人堵住 -> 强制让路（brain/unstick.py）
    percept: Perception = None
    jobs: Scheduler = field(default_factory=Scheduler)
    world: object = None
    pending: list = field(default_factory=list)          # dict(kind, code, t, addr, worker)
    bad_spots: dict = field(default_factory=dict)        # code -> set((x,y))
    pro_data: dict = field(default_factory=dict)         # brain/data/pro_spots.json：高手录像的主基地落点（__post_init__ 读）
    pro_last: frozenset = frozenset()                    # 最近一次 _building_spots 里来自高手录像的点（_build_at 记数用）
    opening_id: str = OP.NONE                            # 在线配置 opening 上一次看到的值（变了才换开局谱）
    opening_run: object = None                           # OP.OpeningRun：这一局的开局谱进度（brain/opening.py）
    lumber_crew: dict = field(default_factory=dict)      # worker addr -> tree addr
    gold_crew: dict = field(default_factory=dict)        # worker addr -> mine addr
    sent_at: dict = field(default_factory=dict)          # worker addr -> 派活的游戏秒（快照要一会儿才刷出新订单）
    rebalance_at: float = -1e9                           # 上一次在采金/伐木之间调人的游戏秒（见 _rebalance）
    mines_now: list = field(default_factory=list)       # job_gold 这一拍认的矿（job_lumber 分角色用）
    harvest_plan: dict = field(default_factory=dict)    # AMAI 派工这一轮的表：该几个 / 现有几个（发布到远见）
    trees: list = field(default_factory=list)
    trees_at: float = -1e9
    skill_idx: dict = field(default_factory=dict)
    heroes_ordered: int = 0
    hero_seen: dict = field(default_factory=dict)      # 英雄 addr -> (类型, 最近读到的等级)：死了以后按这个认出是谁、估复活价
    dead_waiting: list = field(default_factory=list)   # 死亡英雄表里还没开始复活的：[{ptr, code, level}]
    last_revive: float = -1e9
    dead_read_at: float = -1e9                         # 上次读死亡英雄表的游戏秒（_refresh_dead 按需读）
    hero_slot: dict = field(default_factory=dict)      # 英雄 addr -> 第几个出的（1/2/3）
    idle_note_at: dict = field(default_factory=dict)   # 英雄看戏埋点：每个英雄上次记原话的游戏秒
    last_hero_order: float = -1e9
    rally_at: dict = field(default_factory=dict)
    seen_full: set = field(default_factory=set)          # 见过满血的建筑地址（不再当工地）
    site_hp: dict = field(default_factory=dict)          # 工地地址 -> (血量, 上次涨血的游戏秒)
    mine_full_until: dict = field(default_factory=dict)  # 矿地址 -> 被拒后到这个游戏秒前不再派
    expand_target: tuple | None = None                   # (游戏秒, 矿 x, 矿 y)：开矿检查走到「守矿野怪清完」的那座矿（小动物找它）
    critter_wait: dict = field(default_factory=dict)     # (矿 x, 矿 y) -> 首选落点第一次被小动物压着的游戏秒
    gold_attempts: dict = field(default_factory=dict)    # 工人地址 -> (矿身份, 下令时间)；识别已经采空却仍在快照里的矿
    gold_failures: dict = field(default_factory=dict)    # 矿身份 -> 拒绝/原地空闲过的工人身份集合
    depleted_mines: set = field(default_factory=set)     # 两名不同工人都无法开始采集后，本局永久排除
    _ground: list = field(default_factory=list)
    _ground_mtime: float = 0.0
    tech_cache: dict = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    tick_deaths: Counter = field(default_factory=Counter)   # 这一拍（10 游戏秒）的阵亡 打野/打玩家/别处，run_brain 每拍取走清零
    notes: list = field(default_factory=list)
    defense_mode: str = "none"
    config: CFG.LiveConfig = None
    last_plan: dict = field(default_factory=dict)       # 最近一轮建造规划（控制台 AI 页）
    last_intents: list = field(default_factory=list)    # 最近一轮战术意图
    recent_notes: deque = field(default_factory=lambda: deque(maxlen=120))
    peak: dict = field(default_factory=lambda: dict(army=0, enemy=0, gold=0, workers=0, heroes=0))
    race: str = None
    spec: dict = field(default_factory=lambda: R.HUMAN)
    strategy: object = None            # AMAI 战略引擎（config strategy_engine == "amai" 时才建）
    timer: object = field(default_factory=NullTimer)   # brain/steptime.py：一轮慢在哪（run_brain 换成 StepTimer）
    militia_creep_ids: set = field(default_factory=set) # INFI：清分矿的临时民兵，和正规军分组下令
    militia_called_at: float = -1e9
    militia_seen_hmil: bool = False                     # 这一次号召**看到过**变身（见 _militia_creep_step）
    militia_camp_key: tuple | None = None
    militia_failed_camp_key: tuple | None = None          # 真实拒绝过的同一营地不循环刷号召
    militia_expand_xy: tuple | None = None              # 本次民兵所清分矿；清完保留两人原地建大厅
    militia_expand_builders: set = field(default_factory=set)
    militia_assist: dict = field(default_factory=dict)    # 原民兵协建分矿大厅：农民地址 -> 大厅地址（_militia_assist）
    home_militia_ids: set = field(default_factory=set)    # 09-22 晚 守家民兵（_home_militia_step）：号召过的农民地址
    home_militia_called: dict = field(default_factory=dict)  #   农民地址 -> 上次号召的游戏秒（到点变回后再叫要隔 HOME_MIL_RECALL_S）
    home_militia_seen: float = -1e9                       #   最近一次看到家被打的游戏秒（离开满 HOME_MIL_CLEAR_S 解除）
    late_food_at: float = -1e9                            # 09-22 晚 最近一次处在收尾拆家的游戏秒（food_soft_cap：人口放到 100）
    expand_hold_why: object = None                        # 09-22 晚 开矿闸（_expand_hold）上一次的理由：变了才写日志
    tower_rush: TRUSH.TowerRushWatch = field(default_factory=TRUSH.TowerRushWatch)   # 09-22 晚 防塔攻（brain/towerrush.py）
    militia_expand_until: float = -1e9
    militia_demobilize_at: float = -1e9                 # 已下解除令；等预留者真正以 hpea 回到快照再落大厅
    unit_ids: object = None                             # 09-21 血条编号（brain/unitids.py）
    labeler: object = None                              #   w3speak.Speaker（第一次标编号时才连）
    label_off: str = ""                                 #   标不了的原因（旧 DLL / 对话气泡占着），给远见看
    declog: object = None                               # 09-21 战斗决策明细（_declog）；False = 模块读不到
    _step_t0: float | None = None                       #   这一拍拿到快照的时刻（monotonic）
    _snap_age: float | None = None                      #   快照读出来时已经旧了多久（毫秒）
    _rest_cache: tuple | None = None                    # 养伤空地 (算的时刻, x, y, 是不是空地, 家 x, 家 y)（_rest_spot）
    _rally_cache: tuple | None = None                   # 门前空地，同上（_army_rally）
    _step_gap: float | None = None                      #   和上一拍隔了多久（毫秒）：事件落在两拍之间最多再等这么久
    _terrain_live: bool = False                         # 09-23：地形表换成这一局运行时算的（W3P 地图块）了没有
    _terrain_try: float = -1e9

    def __post_init__(self):
        if self.unit_ids is None:
            self.unit_ids = UID.UnitIds()
        if self.act is None:
            self.act = A.Actuator(self.cmd, self.claims)
        if self.attack is None:
            self.attack = AttackSequence(self.profile)
        if self.attack.terrain is None:
            self.attack.terrain = TR.load()        # 09-21 Echo Isles 地形表（打野集合点挑空旷、有退路的一侧；读不到 = None 照旧）
        if self.percept is None:
            self.percept = Perception(self.cmd)
        if self.config is None:
            self.config = CFG.LiveConfig(os.path.join(self.inst_dir, "brain_config.json"))
        self.config.refresh()
        self.home_recovery.inv_cache = self.recovery.inv_cache   # 两份恢复读同一批人的背包：一张缓存（recovery.INV_BAG_TTL）
        self.recovery.terrain = self.home_recovery.terrain = self.attack.terrain   # 用卷轴去空地集合（ArmyRecovery.meet_at）
        CFG.apply(self.config.values, self.profile)
        self.known_spots = _load_json(os.path.join(self.inst_dir, "known_spots.json"), {}).get(MAP_ID_ECHO, {})
        self.known_halls = _load_json(os.path.join(self.inst_dir, "known_halls.json"), {}).get(MAP_ID_ECHO, {})
        if not self.pro_data:
            self.pro_data = _load_json(PRO_SPOTS, {})
        self.skills = _load_json(LEARNED, {}).get("skills", {})
        j = self.jobs
        j.add("build", 3.0, lambda w, b: self.job_build(w))
        j.add("gold", 2.0, lambda w, b: self.job_gold(w))
        j.add("heroes", 5.0, lambda w, b: self.job_heroes(w), start_at=2.0)
        j.add("lumber", 4.0, lambda w, b: self.job_lumber(w), start_at=6.0)
        j.add("rally", 20.0, lambda w, b: self.job_rally(w), start_at=10.0)
        j.add("tactics", 2.0, lambda w, b: self.job_tactics(w), start_at=4.0)
        j.add("construction", 4.0, lambda w, b: self.job_construction(w), start_at=20.0)
        j.add("entangle", 5.0, lambda w, b: self.job_entangle(w), start_at=30.0)
        j.add("live", 1.5, lambda w, b: live.publish(self, os.path.join(self.inst_dir, "brain_live.json")), start_at=1.0)
        # ★★ 09-21 运营层很难用规则写好，交给 AI 做运营和战略层面的分析与输出。
        #   本地大模型当运营顾问，**异步**（实测一次 1.08 秒，而这一拍只有 1 秒）。
        #   它只改目标和优先级，具体下令仍归规则层 —— 见 brain/coach.py 的四条硬约束。
        j.add("coach", 5.0, lambda w, b: self.job_coach(w), start_at=25.0)
        j.add("unit_ids", UID.LABEL_EVERY, lambda w, b: self.job_unit_ids(w), start_at=1.0)

    def note(self, t: float, text: str) -> None:
        text = self.unit_ids.annotate(text)          # 「hfoo@14D4E0E4」-> 「#23 hfoo@14D4E0E4」（brain/unitids.py）
        self.notes.append((t, text))
        self.recent_notes.append((round(t, 1), text))

    # ── 数量：场上 + 在路上（下过令还没出现的）──
    def _prune_pending(self, w) -> None:
        keep = []
        for p in self.pending:
            ttl = p.get("ttl") or {"unit": PENDING_UNIT_S, "hero": PENDING_UNIT_S, "building": PENDING_BUILD_S, "upgrade": PENDING_UNIT_S * 3,
                                   "upgrade_hall": PENDING_HALL_UP_S, "expand": PENDING_EXPAND_S, "resume": PENDING_RESUME_S}.get(p["kind"], 30.0)
            if w.t - p["t"] > ttl:
                self.counts[f"在路上过期·{p['kind']}"] += 1
                continue
            if p["kind"] in ("building", "expand", "morph"):
                # 工人订单已经不是这座建筑、而场上这种建筑的数量涨了 -> 兑现了（morph = 建筑升级，只有 AMAI 那条路会有）
                if w.counts.get(p["code"], 0) > p.get("had", 0):
                    if self.spec.get('needs_builder'):         # 人族 / 兽族：工人要守着地基（09-22 晚 1-413，原来只登记人族）
                        self.powerbuild.observe(w, p)
                    continue
            if p["kind"] in ("unit", "hero", "revive") and w.counts.get(p["code"], 0) >= p.get("expect", p.get("had", 0) + 1):
                continue
            if p["kind"] == "upgrade_hall" and w.counts.get(p["code"], 0) > 0:
                continue
            keep.append(p)
        self.pending = keep

    def _pending_n(self, kind: str, code: str) -> int:
        return sum(1 for p in self.pending if p["kind"] == kind and p["code"] == code)

    def queued_food(self) -> int:
        """已经下了训练令、还没出现在场上的兵/英雄的人口。游戏里兵**开始训练**才占人口，排在队列后面的还没占 ——
        09-22 f58f9a3 之后 5 局 4 局冲过 80（309 一拍 80->85、311 73->82）就是几座兵营的队列一起开练。
        正在训练的那个已经算在当前人口里、这里又算一次：宁可少造一个，也别过 80（到 79 之前就要开始控制人口）。"""
        return sum(G.food(p["code"]) for p in self.pending if p["kind"] in ("unit", "hero") and p.get("code"))

    def soft_food_free(self, w) -> int:
        """软上限（food_soft_cap：平时 G.FOOD_SOFT_CAP，后期 FOOD_SOFT_CAP_LATE）下还能用的人口（扣掉已下单没出来的）。"""
        return min(w.food_cap, self.food_soft_cap(w)) - w.food_used - self.queued_food()

    def food_soft_cap(self, w) -> int:
        """★ 09-22 晚（1-411）人口上限分两档：只有收尾阶段（敌军基本被消灭）才突破 80 人口、加快补拆塔单位；
        其余时间尽量控制在 80 以内，避免吃到 60% 的维护费。
        游戏里 51~80 人口收入 -30%、81~100 -60%。原来「三本 + 金子 >= 600」就放到 100 —— 太早；现在只在收尾（_finishing：
        对面作战单位 <= 我方 × FINISH_FOE_FRAC）时放到 FOOD_SOFT_CAP_LATE，收尾结束后再留 LATE_FOOD_HOLD_S（别在门槛上来回抖）。"""
        #   ★★ 09-23 1-416 / 1-418：人口卡在 80、金子从 1800 涨到 6356 / 从 700 涨到 4289，兵 16 个一直在家（对面 AMAI 兽族一矿龟缩、
        #   25 个兵 + 10 座塔 / 地洞）—— 维护费省下来的钱根本花不出去。AMAI 自己的规则（third_party/AMAI/TFT/Upkeep.txt HIGH 那行）：
        #   存款 > 2000（resistance）或者两座矿就允许越过 80。这里取存款那一半：钱攒到 LATE_FOOD_BANK 也放开。
        bank = w.gold >= LATE_FOOD_BANK
        if self._finishing(w) or bank:
            if w.t - self.late_food_at > LATE_FOOD_HOLD_S:
                self.counts["收尾拆家·人口放到 100" if not bank else "存款过 2000·人口放到 100"] += 1
            self.late_food_at = w.t
        return G.FOOD_SOFT_CAP_LATE if w.t - self.late_food_at <= LATE_FOOD_HOLD_S else G.FOOD_SOFT_CAP

    def tech_level(self, code: str, any_unit, t: float) -> int:
        c = self.tech_cache.get(code)
        if c and t - c[1] < 10.0:
            return c[0]
        lvl = c[0] if c else 0
        try:
            player = self.act.player(any_unit.addr)              # 玩家对象整局不变，Actuator 缓存（09-19 steptime）
            many = getattr(self.cmd, "tech_many", None)
            if player and many is not None:
                # 09-23 体检：1.1 万次 × 7 ms —— 一个码过期时，把所有已经过期的一起刷新（一批只等一次）
                codes = [code] + [k for k, (_l, t0) in self.tech_cache.items() if k != code and t - t0 >= 10.0]
                got = many(player, codes)
                for k in codes[1:]:
                    if got.get(k) is not None:
                        self.tech_cache[k] = (int(got[k]), t)
                v = got.get(code)
            else:
                v = self.cmd.tech_count(player, code) if player else None
            if v is not None:
                lvl = int(v)
        except Exception:  # noqa: BLE001
            pass
        self.tech_cache[code] = (lvl, t)
        return lvl

    # ── AMAI 战略引擎（strategy_engine == "amai"）──
    def _ensure_strategy(self):
        """种族认出来之后建一次。Echo Isles 是 2 人图、镜头可视面积 < 240 千单位² -> AMAI 的 mapSize <= 2，
        所以"小地图给一本战略 +20"这条成立（spec §1.3）。"""
        if self.strategy is None and self.race is not None:
            from .amai_strategy import AmaiStrategy
            self.strategy = AmaiStrategy(self.race, map_size=2, players=2)
            self.strategy.b_profile = self.profile
            self.counts["AMAI 战略引擎·已启用"] += 1
        return self.strategy

    def have(self, w, want: Want) -> int:
        k, c = want.kind, want.code
        if k == "hero":
            # 死了等复活的、正在祭坛里复活的都算有 —— 否则规划器去「再训练一个同名英雄」（引擎不让，「没有能出 Obla 的建筑」×206）
            return (w.counts.get(c, 0) + self._pending_n(k, c) + self._pending_n("revive", c)
                    + sum(1 for d in self.dead_waiting if d["code"] == c))
        if k == "revive":
            return self._pending_n("revive", c) if c != "hero" else 0
        if k == "building" and want.where.startswith("town:"):
            codes = ("hwtw", "hgtw", "hatw", "hctw") if c == "hwtw" else (c,)
            return self.have_near_town(w, want, codes)
        if k in ("unit", "building"):
            # 09-22 晚：变成民兵的农民照样算农民（守家民兵 45 秒就变回），别在守家时掏钱补农民
            militia = w.counts.get("hmil", 0) if c == "hpea" else 0
            return w.counts.get(c, 0) + militia + self._pending_n(k, c)
        if k == "morph":
            # 建筑升级（哨塔 -> 箭塔/炮塔、地穴 -> 幽魂之塔）：只有 AMAI 战略引擎会要
            return w.counts.get(c, 0) + self._pending_n(k, c)
        if k == "upgrade":
            return (self.tech_level(c, w.mine[0], w.t) if w.mine else 0) + self._pending_n(k, c)
        if k == "upgrade_hall":
            halls = self.spec["halls"]
            chain = halls[halls.index(c):] if c in halls else (c,)
            return sum(w.counts.get(x, 0) for x in chain) + self._pending_n(k, c)
        if k == "expand":
            codes = ("ugol",) if self.spec["expand_on_mine"] else self.spec["halls"]
            return sum(w.counts.get(x, 0) for x in codes) + self._pending_n(k, c)
        return 0

    def have_near_town(self, w, want: Want, codes) -> int:
        """按指定大厅局部计建筑数；含已下令未出现的地基。"""
        try:
            addr = int(want.where.split(":", 1)[1])
        except (ValueError, IndexError):
            return 0
        hall = next((h for h in w.my_halls if h.addr == addr), None)
        if hall is None:
            return 0
        codes = set(codes)
        live = sum(b.type in codes and math.hypot(b.x-hall.x, b.y-hall.y) <= TOWN_NEAR_R for b in w.my_buildings)
        pending = sum(p.get("code") in codes and "x" in p and math.hypot(p["x"]-hall.x, p["y"]-hall.y) <= TOWN_NEAR_R
                      for p in self.pending if p.get("kind") == "building")
        return live + pending

    # ── 能不能下（CANNOT_BUILD 的原因）──
    FEAS_PREFETCH_MAX = 48          # 一拍最多预取这么多对（快车道一批 16 条 = 3 次等待；以前平均每拍逐个等 4.6 次）

    def _feasible_pairs(self, w, wants) -> list:
        """这一拍规划里 check() 可能会问的 (生产建筑地址, 四字码)：和 check() 里 _producer 的建筑类型取法一致（同一条规则写在两处，两边互相点名，改一处要连另一处一起改）。
        按优先级从高到低，最多 FEAS_PREFETCH_MAX 对；多出来的照旧在 check() 里单问。"""
        done = [b for b in w.my_buildings if completed(b)]
        out = []
        for x in sorted(wants, key=lambda x: -x.prio):
            k, c = x.kind, x.code
            if k in ("unit", "hero"):
                types = self.spec["producers"].get(c, (self.spec["altar"],) if k == "hero" else ())
            elif k == "upgrade":
                r = self.spec["researchers"].get(c, "")
                types = tuple(r) if isinstance(r, (tuple, list, set)) else (r,)
            elif k == "upgrade_hall":
                types = (self.spec["hall_up"].get(c, ""),)
            elif k == "morph":
                types = (_morph_source(c),)
            else:
                continue
            try:
                if self.have(w, x) >= x.qty:                         # 已经够了的规划不会去问
                    continue
            except Exception:  # noqa: BLE001
                pass
            out += [(b.addr, c) for b in done if b.type in types]
            if len(out) >= self.FEAS_PREFETCH_MAX:
                break
        return out[:self.FEAS_PREFETCH_MAX]

    def _producer(self, w, types, code: str):
        best = None
        blds = w.my_buildings
        main = w.main_town() if code == "hatw" else None
        if main is not None:
            # 09-23 奥术塔升门口那座：离主基地最近的哨塔（分矿的哨塔留着升防御塔；tables/human FRONT_TOWERS）
            blds = sorted(blds, key=lambda b: math.hypot(b.x - main.hall.x, b.y - main.hall.y))
        for b in blds:
            if b.type in types and completed(b):
                v = self.act.feasible(b.addr, code)
                if v in OK_VERDICTS:
                    return b, None
                best = best or v
        return None, (f"没有能出 {code} 的建筑" if best is None else f"引擎裁决 {best}")

    def check(self, w, want: Want) -> str | None:
        k, c = want.kind, want.code
        if k == "item":
            return "物品还没接（买东西这条路没做）"          # 只有 AMAI 战略引擎会要（BuildItem）
        if k == "morph":
            src = _morph_source(c)
            if not src:
                return f"{c} 不是升级来的建筑"
            b, why = self._producer(w, (src,), c)
            if b is None and why and why.endswith(("8", "9")):
                return None
            return why
        if k == "revive":
            if not any(b.type == self.spec["altar"] and completed(b) for b in w.my_buildings):
                return "没有祭坛复活不了"
            if w.t - self.last_revive < 3.0:
                return "复活刚下过单"
            if c != "hero" and G.food(c) > w.food_free:
                return "人口不够"
            if c != "hero" and G.food(c) > self.soft_food_free(w):
                return "人口软上限"                   # 复活也不许过 80：战术层据此强制攻击低级兵腾地方
            return None
        if k in ("unit", "hero"):
            types = self.spec["producers"].get(c, (self.spec["altar"],) if k == "hero" else ())
            if k == "hero" and w.t - self.last_hero_order < 60.0:
                return "英雄刚下过单"
            n_prod = sum(1 for b in w.my_buildings if b.type in types and completed(b))
            in_flight = sum(1 for p in self.pending if p["kind"] in ("unit", "hero") and p.get("addr") in
                            {b.addr for b in w.my_buildings if b.type in types})
            if n_prod and in_flight >= 2 * n_prod:
                return "生产队列满了"                              # 训练下单就扣钱，别把钱全压在队列里
            b, why = self._producer(w, types, c)
            if b is None and why and why.endswith(("8", "9")):
                return None                                        # 缺金缺木交给钱的判断（这样才会停下攒钱）
            if why and why.endswith(" 32"):
                # ★ 09-18 实测（234606 之后那局，兵营对 hfoo/hrif/hkni 全是 32、同时别的建筑缺钱是 8）：
                #   兵营训练队列容量 7、7 格已排满（hrif×5 hfoo×2，每个都已扣钱）—— 32 = 训练队列满
                return "训练队列满了（引擎裁决 32）"
            if why and why.startswith("引擎裁决") and why.split()[-1] not in ("3", "183", "185", "221"):
                self.counts[f"未知裁决·{why.split()[-1]}·{c}·在路上{in_flight}·建筑{n_prod}"] += 1
            return why
        if k == "upgrade":
            researcher = self.spec["researchers"].get(c, "")
            types = tuple(researcher) if isinstance(researcher, (tuple, list, set)) else (researcher,)
            b, why = self._producer(w, types, c)
            if b is None and why and why.endswith(("8", "9")):
                return None
            return why
        if k == "upgrade_hall":
            if not any(x.type == self.spec["barracks"] for x in w.my_buildings):
                return "没有兵营不升本"
            b, why = self._producer(w, (self.spec["hall_up"].get(c, ""),), c)
            if b is None and why and why.endswith(("8", "9")):
                return None
            return why
        if k in ("building", "expand"):
            if sum(1 for p in self.pending if p["kind"] in ("building", "expand")) >= self.builder_cap(w):
                return "建造工满了"
            if not self._builder(w, expansion=(k == "expand")):
                return "没有能用的工人"
            if k == "expand":
                hold = self._expand_hold(w)
                if hold:
                    return hold
                skipped = self.counts.get("分矿候选排除·在对面那半边（兵力没压倒）", 0)
                mine = self._expansion_mine(w)
                if mine is None:
                    if self.counts.get("分矿候选排除·在对面那半边（兵力没压倒）", 0) > skipped:
                        return "开矿·剩下的矿都在对面那半边（兵力压倒对面才去）"
                    return "没有能开的矿"
                guard = self._expansion_guards(w, mine)
                if guard:
                    s = creep_strength(guard)
                    self.attack.clear_target = dict(ids={c.addr for c in guard}, x=mine.x, y=mine.y, strength=s)
                    return f"分矿旁边有野怪（强度 {int(s)}），先清"
                self.expand_target = (w.t, mine.x, mine.y)     # 守矿野怪清完了：它附近的小动物交给战术层打（attack.critters）
                # Call to Arms 途中农民会进入大厅并暂时从快照消失。必须等它们重新出现为民兵、
                # 清完后解除成两名预留农民，不能让普通农民在这条链完成前抢先下大厅地基。
                if (self.race == "human" and self.militia_creep_ids and self.militia_expand_xy
                        and math.hypot(mine.x-self.militia_expand_xy[0], mine.y-self.militia_expand_xy[1]) <= 1400):
                    return "等待分矿民兵解除后双敲"
                # militiaoff 订单接下后，本拍快照里单位仍可能是 hmil；若立即允许开矿，建造器
                # 看不到预留者，就会抓一个普通农民从主矿跑来，重现 226 局的错误。最多等 15 秒
                # 取得 hpea 回执；失踪/死亡者超时后从预留中释放，由普通农民兜底。
                # ★ 09-21 1-286：预留的两个不再下「回到工作」（那条令让民兵跑回老家），而是原地等到点变回 ——
                #   还是民兵的那几个要等到它变回来（最多 MILITIA_REVERT_WAIT_S），不能按 15 秒回执超时移出预留：
                #   1-286 第二个预留者就是这样被移出、在老家被派去盖农场的。已经有一个变回来了就先让它下地基。
                visible_workers = {u.addr for u in w.my_workers}
                ready = self.militia_expand_builders & visible_workers
                missing = self.militia_expand_builders - visible_workers
                still_militia = missing & {u.addr for u in (getattr(w, "my_militia", []) or [])}
                gone = missing - still_militia
                since = w.t-self.militia_demobilize_at
                if not ready and still_militia and since < MILITIA_REVERT_WAIT_S:
                    self.counts["民兵清分矿·等预留者原地变回"] += 1
                    return "等待预留民兵到点原地变回农民后双敲"
                if not ready and gone and since < 15.0:
                    return "等待原民兵变回农民后双敲"
                stale = gone if since >= 15.0 else set()
                if since >= MILITIA_REVERT_WAIT_S:
                    stale |= still_militia
                if stale:
                    self.militia_expand_builders.difference_update(stale)
                    self.counts["民兵清分矿·解除回执超时"] += len(stale)
                # ★ 09-21 AMAI QueueExpansionPeonsIfNeeded（common.eai:9931）：现有矿的农民**造满了**才开下一座。
                #   清守卫（上面的 clear_target）照样先触发 —— AMAI 的 take_exp 也在这道闸之前，等农民的同时主力先去清野；
                #   民兵那几道等待也在前面（民兵正在为这座矿忙，该报的是「等民兵」）。
                #   两个原民兵已经站在分矿边上时不挡 —— 这时正好留两个就地造分矿大厅。
                if self.race in AMAI_HARVEST_RACES and not self.militia_expand_builders:
                    mines_done = sum(1 for h in w.my_halls if completed(h))
                    peons_done = len(w.my_workers) + len(getattr(w, "my_militia", []) or [])
                    if not amai_saturated(w, self.race, mines_done, peons_done):
                        return "现有矿农民没造满（AMAI：造满再开下一座）"
            return None
        return "不认识的种类"

    def cost(self, w, want: Want) -> tuple[int, int]:
        if want.kind == "morph":
            src = _morph_source(want.code)
            g, l = G.cost(want.code)
            if src:                       # units.json 里升级形态是累计价（和 hkee/hcas 同一个坑）
                sg, sl = G.cost(src)
                return max(0, g - sg), max(0, l - sl)
            return g, l
        if want.kind == "revive":
            lvl = max([d["level"] for d in self.dead_waiting if d["code"] == want.code] or [1])
            return revive_cost(want.code, lvl)
        if want.kind == "upgrade":
            lvl = self.tech_level(want.code, w.mine[0], w.t) if w.mine else 0
            return G.upgrade_cost(want.code, lvl + 1)
        if want.kind == "upgrade_hall":
            return G.hall_upgrade_cost(want.code)
        if want.kind == "hero" and not w.my_heroes and self.heroes_ordered == 0:
            return 0, 0                                            # 首英雄不收钱（旧 PowerShell AI 的规矩，v253）
        return G.cost(want.code)

    # ── 建造任务 ──
    def committed(self) -> tuple[int, int]:
        """已下令、还没开工的建筑的钱（魔兽里建筑在工人到工地放下地基时才扣钱，不扣掉就会同一笔钱花两遍）。"""
        g = l = 0
        for p in self.pending:
            if p["kind"] in ("building", "expand"):
                cg, cl = G.cost(p["code"])
                g, l = g + cg, l + cl
        # ★ 09-22 （1-297）一批残血单位一直等不到恢复卷轴：根因是造兵持续占用经济，卷轴钱攒不出来。
        #   整队恢复要买卷轴时把卷轴钱算成「已承诺」：需求表 / AMAI / 协建 / 修理都先扣掉它（recovery.reserve）。
        #   两份恢复（主力 / 家里）同一时间只有一份在跑，取大的那份，不重复扣。
        g += max(self.recovery.reserve, self.home_recovery.reserve)
        return g, l

    def builder_cap(self, w) -> int:
        if not self.spec["needs_builder"]:
            return 3        # ★ 09-18 亡灵：侍僧召唤完建筑马上回去采金，按工人数限 1 个名额，开矿「建造工满了」被挡 84 次
        return max(1, min(3, 1 + len(w.my_workers) // 10))

    def job_build(self, w) -> None:
        tm = self.timer                     # run_due 已经开了「任务·build」这一层，下面按步 lap（steptime）
        tm.lap("build·整理在路上/分矿野怪")
        self._prune_pending(w)
        for mine in w.mines:
            self._expansion_guards(w, mine)
        if not self.config["enable_build"]:
            return
        if self.config["strategy_engine"] == "amai":
            tm.lap("build·AMAI")
            self.job_build_amai(w)
            return
        tm.lap("build·需求表")
        # 09-22 晚：后期人口放到 100 —— 房子上限、后期多要的兵交给愿望表（world.late_food / farm_cap，四族表都认）
        w.late_food = self.food_soft_cap(w) > G.FOOD_SOFT_CAP
        w.farm_cap = G.FOOD_FARM_CAP_LATE if w.late_food else G.FOOD_FARM_CAP
        wants = self._opening_wants(w, list(self.spec["table"].wants(w, self.profile)))
        wants = self._militia_expansion_wants(w, wants)
        wants = self._coach_wants(w, wants)
        tm.lap("build·死亡英雄表")
        self._refresh_dead(w)
        tm.lap("build·规划")
        wants += [Want("revive", d["code"] or "hero", 1, REVIVE_PRIO, note="复活英雄") for d in self.dead_waiting]
        cg, cl = self.committed()
        keep = self._item_reserve()
        if keep:
            cg += keep
            self.counts["攒钱·残血英雄买血瓶"] += 1
        haves = {}

        def have(x):
            haves[id(x)] = self.have(w, x)
            return haves[id(x)]
        self.act.prefetch_feasible(self._feasible_pairs(w, wants))    # 09-23 体检：一批问完，规划里不再逐个等
        try:
            res = self.planner.plan(wants, have, max(0, w.gold - cg), max(0, w.lumber - cl), w.food_free,
                                    lambda x: self.check(w, x), lambda x: self.cost(w, x),
                                    unit_food_free=self.soft_food_free(w))
        finally:
            self.act.clear_feasible()
        # 被人口软上限挡下的兵 / 英雄 / 复活（不算农民）：战术层据此强制攻击自己的低级兵腾人口（AttackSequence._cull）
        self.attack.food_blocked = [x.code for x, r in res.skipped
                                    if r == "人口软上限" and x.kind in ("unit", "hero", "revive") and not G.is_worker(x.code)]
        if self.opening_run is not None and self.opening_run.active:
            self.opening_run.observe(res)
        # 引擎裁决的跳过带上是哪一项（09-17 实机「引擎裁决 221」×63，看不出是谁被挡）
        for why in {r if not r.startswith("引擎裁决") else f"{r}·{x.code}" for x, r in res.skipped}:
            self.counts[f"建造跳过·{why}"] += 1
        if res.stopped_at is not None:
            self.counts[f"攒钱·{res.stopped_at.code}"] += 1
            # 09-21：攒钱期间放过去了什么（不需要所缺资源的）。0 = 这条没起作用，1-262 那种死锁还在
            for x in res.through:
                self.counts[f"攒{'木' if res.short == 'lumber' else '金' if res.short == 'gold' else '钱'}中照样出·{x.code}"] += 1
        skipped = {id(x): r for x, r in res.skipped}
        chosen = {id(d.want) for d in res.decisions}
        self.last_plan = {"t": round(w.t, 1), "gold": w.gold, "lumber": w.lumber, "committed": [cg, cl],
                          "stoppedAt": res.stopped_at.code if res.stopped_at else None,
                          "wants": [{"kind": x.kind, "code": x.code, "qty": x.qty, "prio": x.prio, "note": x.note, "have": haves.get(id(x)),
                                     "status": "下单" if id(x) in chosen else ("攒钱" if x is res.stopped_at else
                                               (skipped[id(x)] if id(x) in skipped else ("已满足" if id(x) in haves and haves[id(x)] >= x.qty else "没轮到")))}
                                    for x in sorted(wants, key=lambda x: -x.prio)]}
        tm.lap("build·下令")
        for d in res.decisions:
            self._execute(w, d.want)

    def _militia_assist(self, w) -> None:
        """清完分矿留下的原民兵（militia_expand_builders，主建的那个已经转成 pending 里的建造工）一起敲分矿大厅。
        ★ 09-22 清完二矿的野后农民跑回主矿，没有留在二矿就地建大厅。
          人族协建（Ahrp）：每个助手多花造价的 POWER_BUILD_COST（大厅 385/205 -> 约 58 金 31 木），速度多 60%。
          5 个一起盖约 3.4 倍速，而且盖好时人就在矿边 —— 不用跑回老家、以后再从老家调人过来（单程约 5600）。
          钱够几个上几个（扣掉已承诺的建造费）；大厅盖好（或地基没了）就放回经济，就近采矿。
          powerbuild 不再往这座工地另派远处的助手（1-287 00:04:30 从 2135 外调了一个，1 秒后就停了）。"""
        if self.race != "human" or not self.militia_expand_builders or self.militia_expand_xy is None:
            self.militia_assist.clear()
            return
        ex, ey = self.militia_expand_xy
        halls = [b for b in w.my_buildings if b.type in G.HALLS and math.hypot(b.x - ex, b.y - ey) <= 1400]
        site = next((b for b in halls if b.hp < .97 * b.hp_max), None)
        if site is None:
            done = next((b for b in halls if b.hp >= .97 * b.hp_max), None)
            if done is not None:
                n = len(self.militia_expand_builders)
                self.militia_expand_builders.clear()
                self.militia_assist.clear()
                self.counts["民兵清分矿·大厅完工·放回就地采矿"] += n
                self.note(w.t, f"民兵清分矿：分矿大厅完工，{n} 名原民兵放回经济就近采矿")
            return                                   # 地基还没下：主建造工那条路在走（check / _build_at）
        workers = {u.addr: u for u in w.my_workers}
        for a in [a for a, s in self.militia_assist.items() if a not in workers or s != site.addr]:
            self.militia_assist.pop(a, None)
        cg, cl = self.committed()
        gold, wood = w.gold - cg, w.lumber - cl
        pg, pl = (POWER_BUILD_COST * v for v in G.cost(site.type))
        builder = {p.get("worker") for p in self.pending if p["kind"] == "expand"}
        for a in sorted(self.militia_expand_builders):
            u = workers.get(a)
            if u is None or a in builder:
                continue
            repairing = (u.order or 0) in (A.ORDER_REPAIR, HUMAN_BUILD)
            if a in self.militia_assist and (repairing or w.t - self.sent_at.get(a, -1e9) < 4.0):
                continue
            if gold < pg or wood < pl:
                self.counts["民兵清分矿·协建·钱不够先等"] += 1
                break
            if self.act.gather(u, site, order=A.ORDER_REPAIR):
                self.militia_assist[a] = site.addr
                self.sent_at[a] = w.t
                gold -= pg
                wood -= pl
                self.counts["民兵清分矿·协建"] += 1
            else:
                self.counts["民兵清分矿·协建·没接下"] += 1

    def _militia_expansion_wants(self, w, wants: list[Want]) -> list[Want]:
        """清完分矿后先攒大厅钱，避免两个原民兵在矿边等着、资源却继续被出兵花掉。"""
        if self.race != "human" or not self.militia_expand_builders or len(w.my_halls) >= 2:
            return wants
        out, raised = [], False
        for want in wants:
            if want.kind == "expand" and want.code == "htow" and want.qty == 2:
                if raised:
                    continue
                out.append(replace(want, prio=max(93, want.prio),
                                   note="民兵清矿完成：原民兵就地一起盖二矿"))
                raised = True
            else:
                out.append(want)
        if not raised:
            out.append(Want("expand", "htow", 2, 93, "mine", "民兵清矿完成：原民兵就地一起盖二矿"))
        return out

    def _opening_wants(self, w, wants: list) -> list:
        """开局谱（brain/opening.py）：在线配置 opening 选了某份、种族对得上，开局这段就由它定建造顺序（农民、农场照建造表补）；
        走完或到时间交回建造表。09-18 按 Yumiko 的顺序加了速开矿版本，两种版本可随时切换；格式留好扩展口，方便后续加更多参考谱。
        切换：开局途中切到 none 立刻停；切到一份开局谱时已经过了它的 until_s 就这局不用（下一局从头走）。"""
        if self.race is None:
            return wants                                    # 种族还没认出来：先不认这次切换，下一轮再看
        oid = self.config["opening"] if self.config else OP.NONE
        if oid != self.opening_id:
            old, self.opening_id, self.opening_run = self.opening_run, oid, None
            if old is not None and old.active:
                self.note(w.t, f"开局谱 {old.op.name} 中途切走（停在第 {old.idx + 1} 步），交回建造表")
            op = OP.load_all().get(oid) if oid != OP.NONE else None
            if oid != OP.NONE and op is None:
                self.counts["开局谱·找不到这份文件"] += 1
            elif op is not None and op.race and op.race != self.race:
                self.counts["开局谱·种族不符"] += 1
                self.note(w.t, f"开局谱 {op.name} 是给 {op.race} 的，这局是 {self.race}，不用")
            elif op is not None and w.t > op.until_s:
                self.counts["开局谱·切过来已经太晚"] += 1
                self.note(w.t, f"开局谱 {op.name} 切过来时已经 {int(w.t)} 秒（超过 {int(op.until_s)} 秒），这局不用，下一局从头走")
            elif op is not None:
                self.opening_run = OP.OpeningRun(op, w.t)
                self.counts[f"开局谱·启用·{op.id}"] += 1
                self.note(w.t, f"开局谱：{op.name}（{len(op.steps)} 步，到 {int(op.until_s)} 秒为止）")
        run = self.opening_run
        if run is None or not run.active:
            return wants
        notes: list = []
        out = run.wants(self, w, wants, notes)
        for s in notes:
            self.note(w.t, s)
        if not run.active:
            self.counts[f"开局谱·{'走完' if run.why_done == '走完' else '到时间'}"] += 1
            self.counts["开局谱·跳过的步"] += len(run.skipped)
        return out

    def job_build_amai(self, w) -> None:
        """AMAI 战略引擎那条路：需求来自建造序列解释器 + AMAI 队列，执行和展示跟手写表那条完全一样。"""
        st = self._ensure_strategy()
        if st is None:
            self.counts["AMAI·还不知道自己是什么族"] += 1
            return
        self._refresh_dead(w)          # 死亡英雄表：AMAI 的 HeroReviver 要按它插复活请求（和手写表那条路同一个口）
        res = st.step(self, w)
        if res is None:
            return                                   # 这一拍只跑了序列，还没到建造循环（5~6 游戏秒一轮）
        for why in {r if not r.startswith("引擎裁决") else f"{r}·{x.code}" for x, r in res.skipped}:
            self.counts[f"建造跳过·{why}"] += 1
        if res.stopped_at is not None:
            self.counts[f"攒钱·{res.stopped_at.code}"] += 1
            for x in res.through:
                self.counts[f"攒{'木' if res.short == 'lumber' else '金' if res.short == 'gold' else '钱'}中照样出·{x.code}"] += 1
        rows = []
        listed = set()

        def add_row(x: Want, status: str, n: int = 1) -> None:
            key = (x.kind, x.code, x.qty, x.where)
            if key in listed:
                return
            listed.add(key)
            rows.append({"kind": x.kind, "code": x.code, "qty": x.qty, "prio": x.prio, "note": x.note,
                         "have": self.have(w, x), "status": status if n <= 1 else f"{status} ×{n}"})

        picked = {}
        for d in res.decisions:
            picked[(d.want.kind, d.want.code, d.want.qty, d.want.where)] = picked.get(
                (d.want.kind, d.want.code, d.want.qty, d.want.where), 0) + 1
        for d in res.decisions:
            add_row(d.want, "下单", picked[(d.want.kind, d.want.code, d.want.qty, d.want.where)])
        if res.stopped_at is not None:
            add_row(res.stopped_at, "攒钱")
        for x, why in res.skipped:
            add_row(x, why)
        for e in st.queue.entries:
            add_row(e.want(st.data), "没轮到")
        cg, cl = self.committed()
        self.last_plan = {"t": round(w.t, 1), "gold": w.gold, "lumber": w.lumber, "committed": [cg, cl],
                          "engine": "amai", "strategy": st.strategy,
                          "stoppedAt": res.stopped_at.code if res.stopped_at else None,
                          "wants": sorted(rows, key=lambda r: -r["prio"])[:60]}
        for d in res.decisions:
            self._execute(w, d.want)

    def _execute(self, w, want: Want) -> None:
        k, c = want.kind, want.code
        if k == "revive":
            altar = next((b for b in w.my_buildings if b.type == self.spec["altar"] and completed(b)), None)
            dead = next((d for d in self.dead_waiting if d["code"] == c or c == "hero"), None)
            if altar is None or dead is None:
                self.counts["下令前没找到建筑·revive"] += 1
                return
            self.last_revive = w.t
            if self.act.revive(altar.addr, dead["ptr"]):
                self.dead_waiting.remove(dead)
                # 复活最长 150 游戏秒（MiscGame HeroMaxReviveTime）；英雄重新出现在场上就兑现
                self.pending.append(dict(kind="revive", code=c, t=w.t, addr=altar.addr, expect=w.counts.get(c, 0) + 1, ttl=170.0))
                self.note(w.t, f"复活 {c}（{dead['level']} 级，估价 {revive_cost(c, dead['level'])[0]} 金）：接下")
            else:
                self.note(w.t, f"复活 {c}：被退（金 {w.gold}）")
            return
        if k in ("unit", "hero", "upgrade", "upgrade_hall", "morph"):
            sp = self.spec
            researcher = sp["researchers"].get(c, "")
            researcher_types = tuple(researcher) if isinstance(researcher, (tuple, list, set)) else (researcher,)
            types = {"unit": sp["producers"].get(c, ()), "hero": (sp["altar"],), "upgrade": researcher_types,
                     "upgrade_hall": (sp["hall_up"].get(c, ""),), "morph": (_morph_source(c),)}[k]
            b, why = self._producer(w, types, c)
            if b is None:
                self.counts[f"下令前没找到建筑·{k}"] += 1
                return
            if self.act.train(b.addr, c):
                entry = dict(kind=k, code=c, t=w.t, addr=b.addr, had=w.counts.get(c, 0))
                if k in ("unit", "hero"):
                    # ★ 09-18（23:46 那局「在路上过期·unit」×41、「引擎裁决 32·hrif」×152）：原来 25 秒一律过期、
                    #   而且同一种兵出来一个就把在路上的全部当兑现 —— 骑士要 45 秒，队列里还没出来就被当成没下，接着再下，队列越排越长。
                    #   现在每条令记「兑现时应该有几个」，过期按造兵时间 × (前面排着的 + 1)
                    ahead = self._pending_n(k, c)
                    entry["expect"] = w.counts.get(c, 0) + ahead + 1
                    entry["ttl"] = float(G.stat(c, "bldtm", 20)) * (ahead + 1) * 1.3 + 10.0
                self.pending.append(entry)
                if k == "hero":
                    self.heroes_ordered += 1
                    self.last_hero_order = w.t
                self.note(w.t, f"train {c}（{k}，优先级 {want.prio}）")
            return
        if k in ("building", "expand") and sum(1 for p in self.pending if p["kind"] in ("building", "expand")) >= self.builder_cap(w):
            self.counts["下令前建造工已满"] += 1                    # 同一轮规划里前面刚派出去一个
            return
        if k == "building":
            with self.timer.section("build·落点"):            # 通路检查是纯 Python 的格子灌水，单独计时
                spots = self._building_spots(w, c, want.where)
            self._build_at(w, c, spots, pro=self.pro_last)
        elif k == "expand":
            mine = self._expansion_mine(w)
            if mine is not None:
                known = self.known_halls.get(f"{int(mine.x)},{int(mine.y)}", [])
                spots = [(mine.x, mine.y)] if self.spec["expand_on_mine"] else A.expansion_candidates(mine, w.my_halls + w.enemy_halls, known)
                bad = self.bad_spots.get(c, set())
                spots = [p for p in spots if p not in bad]        # ★ 09-18：被引擎退过的点要拉黑（普通建筑早就这么做了，开矿这条路漏了
                #   —— 实机一局「建造·被拒」301 次，同一个落点被无限重试，整局卡在开分矿上）
                # 被小动物压着的点这一轮不试：一试就被退、被拉黑（1-303 最好的落点就是这么没的）。首选点被压着先等战术层派人打掉
                key = (int(mine.x), int(mine.y))
                blocked = critter_blocked(spots, [x for x in w.critters if math.hypot(x.x - mine.x, x.y - mine.y) <= CRITTER_R + 600])
                if blocked:
                    self.counts["开分矿·小动物压着落点"] += 1
                    since = self.critter_wait.setdefault(key, w.t)
                    if spots[0] in blocked and w.t - since <= CRITTER_WAIT_S:
                        self.counts["开分矿·小动物压着首选落点·等清掉"] += 1
                        return
                    spots = [p for p in spots if p not in blocked]
                else:
                    self.critter_wait.pop(key, None)
                self._build_at(w, c, spots, kind="expand", note=f"开分矿 {c} @ ({int(mine.x)},{int(mine.y)})")

    def _builder(self, w, expansion: bool = False):
        if w.t >= self.militia_expand_until:
            self.militia_expand_builders.clear()
        busy = {p.get("worker") for p in self.pending if p["kind"] in ("building", "expand", "resume")}
        busy |= self.powerbuild.busy_addrs
        busy |= self.repairs.busy_addrs
        cands = [u for u in w.my_workers if u.addr not in busy and u.addr not in self.harass.busy_addrs
                 and u.addr not in self.militia_creep_ids
                 and (expansion or u.addr not in self.militia_expand_builders)
                 and not constructing(u)]         # 09-22 晚 1-413：正在盖房的不许挑走（兵营停在 99.4%）
        return cands

    def _building_spots(self, w, code: str, where: str = "", exits: bool = True):
        """where = "town:<大厅地址>" 时把落点算在那座分矿旁边（AMAI 的 DefendTowns* 就是给分矿造塔）；
        其余值（base/front/mine）沿用主基地，行为和以前一样。exits=False 只要排序（测试用），不做通路检查。"""
        main = w.main_town()
        if main is None:
            return []
        hall = main.hall
        if where and where.startswith("town:"):
            try:
                addr = int(where.split(":", 1)[1])
            except ValueError:
                addr = 0
            hall = next((h for h in w.my_halls if h.addr == addr), hall)
        known = []
        for key, pts in self.known_spots.items():
            try:
                hk, bc = key.split("|")
                hx, hy = (float(v) for v in hk.split(","))
            except ValueError:
                continue
            if bc == code and abs(hx - hall.x) < 300 and abs(hy - hall.y) < 300:
                known.extend(pts)
        bad = self.bad_spots.get(code, set())
        # 在路上的建筑（下了令、地基还没出现）当作已经占了地
        ghosts = [_Ghost(p["code"], p["x"], p["y"]) for p in self.pending if p["kind"] in ("building", "expand") and "x" in p]
        # ★ 09-18 高手落点排最前（只在主基地；分矿旁的塔不套主基地的布局）。有高手点的建筑，老 AI 的记忆点挪到环形候选之后 ——
        #   记忆点是 09-16 老 AI 自己打出来的散点（农场 960~1580 一路往东南散），排在前面等于照着以前的乱摆
        main_base = hall is main.hall
        pro = self._pro_spots(hall, code) if main_base else []
        self.pro_last = frozenset((A.snap32(x), A.snap32(y)) for x, y in pro)
        blds = w.my_buildings + w.enemy_buildings + ghosts
        # INFI 的分矿塔只放在大厅朝金矿/守矿野怪/来路的一侧。先给这个扇区候选，
        # 再回落到通用环；避免把第一座塔盖在大厅后方，对矿线和入口毫无保护。
        sector = []
        front = []
        if not main_base and code in {"hwtw", "hgtw", "hatw", "hctw"}:
            mine = min(w.mines, key=lambda m: math.hypot(m.x-hall.x, m.y-hall.y), default=None)
            if mine is not None:
                foe = min(w.enemy_halls, key=lambda h: math.hypot(h.x-hall.x, h.y-hall.y), default=None)
                sector = expansion_tower_sector(hall, mine, (foe.x, foe.y) if foe is not None else TOWER_FRONT_DEFAULT)
        elif main_base and where.startswith("town:") and code in {"hwtw", "hgtw", "hatw", "hctw"}:
            # 09-23 主基地门口的塔（tables/human FRONT_TOWERS，AMAI BuildFront）：朝最近的敌方大厅、离大厅 FRONT_RADII，排在高手点前面
            foe = min(w.enemy_halls, key=lambda h: math.hypot(h.x-hall.x, h.y-hall.y), default=None)
            front = main_front_sector(hall, (foe.x, foe.y) if foe is not None else TOWER_FRONT_DEFAULT)
        # ★ 预留：别的建筑还没用上的高手落点当作占着，只挡回落候选（环 / 记忆点），不挡本建筑自己的高手点。
        #   12 局实测：伐木场的高手点 (-5792,2592) 开局被树挡住（Yumiko 8 分钟树砍开了才盖），回落到环上的 (-4960,2432)
        #   正好压着宝物库的高手点 (-4928,2496)，宝物库又被挤到 (-4544,2688) —— 一个点被退，后面一串跟着乱。
        held = self._pro_reserved(hall, code) if main_base else []
        # ★ 09-22 伐木场尽量贴近树木 —— 贴着树林边的点排在最前面（证据见 act.tree_edge_spots 上面）。
        #   走 known 那条路过滤（离别的建筑留缝、不压矿线），高手点（开局被树挡着）和大厅一圈候选接在后面兜底。
        edge = []
        if main_base and code in G.LUMBER_DROPS:
            if w.t - self.trees_at > 60.0 or not self.trees:
                self.trees = self.act.scan_trees(hall.x, hall.y)
                self.trees_at = w.t
            # 可以占农场的预留点：西侧出生点离大厅 700~850、贴着 10~17 棵树的那几个点全被高手布局留给了农场/奥秘圣殿，
            #   只避其它建筑时伐木场被挤到东北 1011 外。农场多、放哪都行，别的建筑的预留照旧
            not_farms = [g for g in held if g.type != self.spec.get("supply")]
            edge = A.spot_candidates(code, hall, blds + not_farms, w.mines, A.tree_edge_spots(code, hall, self.trees), radii=())
            self.counts[f"落点·伐木场贴树林·{'有' if edge else '没有'}候选"] += 1
        cands = list(edge)
        if front:
            # ★ 09-23 对局画面：本该守家的防御塔被造到基地最顶上的角落，背离来路、打不到来犯的敌人 ——
            #   门口的塔只认门口那个扇区；这些点全被占了就这一拍不盖（记「落点·门口没位置」），
            #   绝不回落到「大厅一圈」的通用候选 —— 1-429 东北角那两座就是这么来的。
            cands = [p for p in A.spot_candidates(code, hall, blds, w.mines, pro=front, radii=()) if p not in bad]
            self.counts[f"落点·门口{'有位置' if cands else '没位置'}·{code}"] += 1
            return self._keep_exits(w, code, cands, ghosts) if exits else cands
        cands += [p for p in A.spot_candidates(code, hall, blds, w.mines, pro=front + pro + sector, radii=()) if p not in cands]
        cands += [p for p in A.spot_candidates(code, hall, blds + held, w.mines, [] if pro else known) if p not in cands]
        if pro:
            cands += [p for p in A.spot_candidates(code, hall, blds + held, w.mines, known, radii=()) if p not in cands]
        cands = [p for p in cands if p not in bad]
        return self._keep_exits(w, code, cands, ghosts) if main_base and exits else cands

    EXIT_WANT = 5            # 通路检查：凑够这么多个不堵门的点就停（_build_at 一轮最多试几个）
    EXIT_MAX_CHECK = 30      # 最多检查这么多个候选；都堵门就这一轮不盖（记「落点·会堵门」），好过把英雄关在家里

    def _keep_exits(self, w, code: str, cands: list, ghosts: list) -> list:
        """去掉会把出兵建筑（祭坛/兵营/大厅/车间…）出口封死的候选：建筑 + 在路上的建筑 + 树 + 金矿 一起算（act.seals_exit）。
        ★ 09-18 大法师多次出生后被旧策略造的建筑堵在家里 —— 只看建筑查不出来（最近 6 局没有被建筑单独围死），
        是祭坛挨着西边的树、再被农场/铁匠铺贴上去。本来就被地形/树堵着的不算这个点的账（只拦「原来出得去、放下它就出不去」）。"""
        main = w.main_town()
        if main is None or not cands:
            return cands
        hx, hy = main.hall.x, main.hall.y
        near = lambda o, r=A.EXIT_R + 400: abs(o.x - hx) < r and abs(o.y - hy) < r   # noqa: E731
        if w.t - self.trees_at > 60.0 or not self.trees:
            self.trees = self.act.scan_trees(hx, hy)
            self.trees_at = w.t
        items = [(A.footprint(b.type), b.x, b.y) for b in list(w.my_buildings) + list(ghosts) if near(b)]
        items += [(A.TREE_FOOT, t["x"], t["y"]) for t in self.trees
                  if t.get("hp", 1) > 0 and abs(t["x"] - hx) < A.EXIT_R + 400 and abs(t["y"] - hy) < A.EXIT_R + 400]
        items += [(A.footprint(m.type), m.x, m.y) for m in w.mines if near(m)]
        prod = {self.spec["altar"], *self.spec["halls"]} | {t for v in self.spec["producers"].values() for t in v}
        doors = [(f"{b.type}@{int(b.x)},{int(b.y)}", A.footprint(b.type), b.x, b.y) for b in w.my_buildings if b.type in prod and near(b)]
        reach0 = A.reachable_from_outside(A.blocked_cells(items), hx, hy)
        before = {d[0] for d in doors if any(c in reach0 for c in A.door_cells(*d[1:]))}
        out = []
        for k, (x, y) in enumerate(cands):
            if len(out) >= self.EXIT_WANT or k >= self.EXIT_MAX_CHECK:
                break
            sealed = A.seals_exit(items, doors, (A.footprint(code), x, y), hx, hy, before, reach0)
            if sealed:
                pocket = any(s.startswith("隔出死角") for s in sealed)
                self.counts[f"落点·{'隔出死角' if pocket else '会堵门'}·{code}"] += 1
                continue
            out.append((x, y))
        if not out:
            self.counts[f"落点·全都堵门·{code}"] += 1
        return out

    def _pro_split(self, hall, code: str) -> tuple[list, list]:
        """(同侧出生点的高手落点, 另一侧按地图镜像过来的)。出生点按大厅坐标认（PRO_HALL_TOL 内）。"""
        direct, mirrored = [], []
        data = self.pro_data or {}
        for hk, sp in (data.get("spawns") or {}).items():
            try:
                hx, hy = (float(v) for v in hk.split(","))
            except ValueError:
                continue
            pts = [(float(x), float(y)) for x, y in ((sp.get("spots") or {}).get(code) or [])]
            if not pts:
                continue
            if abs(hx - hall.x) < PRO_HALL_TOL and abs(hy - hall.y) < PRO_HALL_TOL:
                direct += pts
                continue
            mir = (data.get("mirror") or {}).get(sp.get("map")) or {}
            if mir.get("axis") == "x":
                c = float(mir.get("c", 0))
                if abs((c - hx) - hall.x) < PRO_HALL_TOL and abs(hy - hall.y) < PRO_HALL_TOL:
                    mirrored += [(c - x, y) for x, y in pts]
        return direct, [p for p in mirrored if p not in direct]

    def _pro_spots(self, hall, code: str) -> list[tuple[float, float]]:
        """高手录像落点：出生点对得上的那份在前；另一侧出生点的按地图镜像（Echo Isles x' = -512 - x）接在后面
        —— 东侧那局 Yumiko 把铁匠铺、宝物库盖在了分矿，东侧就靠西侧镜像补。镜像点可能被地形挡住（v276 实测西侧镜像兵营地基不出现），
        所以排在同侧点之后，被引擎退了就拉黑。"""
        direct, mirrored = self._pro_split(hall, code)
        # ★ 09-18 第 17/18 局：大法师被堵在家里。西侧 6 个农场高手点用完后接着用了东侧镜像过来的 (-5056,2624)，
        #   正好塞进 Yumiko 在祭坛-农场-宝物库之间留的 96 宽通道（她自己的布局里那条缝是路）。镜像点和同侧的布局不是一套，
        #   混着用就不是「照录像摆」—— 同侧有就只用同侧，同侧一个都没有（东侧的铁匠铺/宝物库）才用镜像
        return direct if direct else mirrored

    def _pro_reserved(self, hall, code: str) -> list:
        """别的建筑的高手落点（当作「在路上的建筑」占地）：同侧的；某建筑同侧没有才用镜像的（镜像点可能是地形挡住的，别拿它白占地方）。
        那个建筑被引擎退过的点不预留（反正用不上）。只预留同一个种族的建筑（建筑码首字母：人族 h / 兽族 o / 亡灵 u / 暗夜 e）——
        同一个出生点也存了对手暗夜玩家的布局，拿它来占人族的地方就错了。"""
        codes = set()
        for sp in ((self.pro_data or {}).get("spawns") or {}).values():
            codes.update((sp.get("spots") or {}).keys())
        out = []
        for c2 in sorted(c for c in codes - {code} if c[:1] == code[:1]):
            direct, mirrored = self._pro_split(hall, c2)
            bad2 = self.bad_spots.get(c2, set())
            out += [_Ghost(c2, x, y) for x, y in (direct or mirrored) if (A.snap32(x), A.snap32(y)) not in bad2]
        return out

    def _expansion_critters(self, w) -> list:
        """要开的分矿附近的小动物（给战术层 attack.critters）：开矿检查 20 游戏秒内走到过「守矿野怪清完」的那座矿 CRITTER_R 内，
        或者开矿令已经下了、地基还没出来的落点旁边（工人走过去的路上它又晃过来了）。"""
        if not w.critters:
            return []
        pts = []
        et = self.expand_target
        if et is not None and w.t - et[0] <= 20.0:
            pts.append((et[1], et[2], CRITTER_R))
        bs = self.attack.build_spot
        if bs is not None:
            pts.append((bs[0], bs[1], CRITTER_BLOCK + 300.0))
        return [c for c in w.critters if any(math.hypot(c.x - x, c.y - y) <= r for x, y, r in pts)]

    def _expansion_guards(self, w, mine):
        """守矿野怪被拉离矿区仍是守卫；按实体身份记忆，不能把离开半径误认成清完。"""
        key = lambda u: (u.addr, getattr(u,"handle_lo",0), getattr(u,"handle_hi",0))
        known = self.mine_guards.setdefault(key(mine), set())
        known.update(key(c) for c in w.creeps if math.hypot(c.x-mine.x,c.y-mine.y) <= T.EXPAND_GUARD_R)
        return [c for c in w.creeps if c.hp > 0 and key(c) in known]

    def _expand_hold(self, w) -> str | None:
        """开矿先要有兵：我方强度不到对面的 EXP_AHEAD_2 / EXP_AHEAD_3 倍、或家里 EXP_CALM_S 内被打过，就先不开（钱留给出兵）。
        1-414（对 AMAI 兽族）：5 分钟 6 兵开二矿、14 分钟 9 兵 vs 16 开三矿；民兵号召 9 次、守军战力比常年 0~1，26 分钟认输。
        返回的理由不带数字（建造跳过·<理由> 按它计数）；数字在状态变化时写一行建造日志。"""
        n = len(w.my_halls)
        need = EXP_AHEAD_2 if n < 2 else EXP_AHEAD_3
        own, foe = w.own_strength, w.foe_strength
        why = None
        if w.t - self.home_militia_seen < EXP_CALM_S:
            why = "开矿·家里刚被打，先守家"
        elif own < need * foe:
            why = "开矿·兵力没养起来"
        if why and why != self.expand_hold_why:
            self.note(w.t, f"先不开第 {n + 1} 座矿：{why.split('·', 1)[1]}（我方强度 {own:.0f} / 对面 {foe:.0f}，要 {need} 倍；"
                           f"家里上次被打 {w.t - self.home_militia_seen:.0f} 秒前）")
        elif not why and self.expand_hold_why:
            self.note(w.t, f"可以开第 {n + 1} 座矿了（我方强度 {own:.0f} / 对面 {foe:.0f}）")
        self.expand_hold_why = why
        return why

    def _expansion_mine(self, w):
        """★ 09-17 实机 5 号：第 39 拍开过一座分矿后「没有能开的矿」×262 —— Echo Isles 只有 4 座金矿，剩下两座是对面的；
        对面分矿大厅早被拆了，可旧规则「2500 内有任何对面建筑就不开」「离主基地 9000 外不开」两条都把它挡住。
        现在：离任何大厅 1000 内不开；1600 内有对面塔、1500 内有 3 座以上对面建筑、2000 内有对面作战单位不开；距离按离我方最近的大厅算。"""
        if w.main_town() is None:
            return None
        halls = w.my_halls + w.enemy_halls
        # ★★ 09-22 晚（1-414）不在敌方基地附近随意开三矿，先攒够自家战力；敌人频繁骚扰时兵力不足守不住。
        #   1-414 14 分钟开在对面二矿位 (3776,-2688)：我方 9 兵 vs 对面 16，5 分钟后三矿、两座哨塔全丢，兵也折在那边。
        #   对面那半边（离对面大厅比离我方大厅近）的矿，只有对面主力基本打没了（我方 >= EXP_FOE_SIDE_AHEAD 倍）才开 ——
        #   09-17 那条「对面分矿大厅拆了就去占」保留在这个前提下。
        own, foe = w.own_strength, w.foe_strength
        foe_side_ok = own >= EXP_FOE_SIDE_AHEAD * foe
        best, bd = None, 1e18
        for m in w.mines:
            if m.type != "ngol":
                continue
            if any(math.hypot(h.x - m.x, h.y - m.y) < 1000 for h in halls):
                continue
            d = min(math.hypot(m.x - h.x, m.y - h.y) for h in w.my_halls) if w.my_halls else 1e18
            if not 900 < d < 12000:
                continue
            d_foe = min((math.hypot(m.x - h.x, m.y - h.y) for h in w.enemy_halls), default=1e18)
            if d_foe < d and not foe_side_ok:
                self.counts["分矿候选排除·在对面那半边（兵力没压倒）"] += 1
                continue
            # 闹鬼金矿 / 缠绕金矿盖在矿上，中立矿单位还在同一个坐标（09-18 暗夜实测两个单位同坐标）—— 谁家的都算开过了
            if any(b.type in ("ugol", "egol") and math.hypot(b.x - m.x, b.y - m.y) < 300
                   for b in w.my_buildings + w.enemy_buildings):
                self.counts["分矿候选排除·已被占"] += 1
                continue
            if any(math.hypot(t.x - m.x, t.y - m.y) < 1600 for t in w.enemy_towers):
                self.counts["分矿候选排除·敌塔1600内"] += 1
                continue
            if sum(1 for b in w.enemy_buildings if math.hypot(b.x - m.x, b.y - m.y) < 1500) >= 3:
                self.counts["分矿候选排除·敌建筑1500内"] += 1
                continue
            if any(math.hypot(u.x - m.x, u.y - m.y) < 2000 for u in w.enemy_fighters):
                self.counts["分矿候选排除·敌兵2000内"] += 1
                continue
            # ★ 09-18 AMAI 引擎测试局：清掉最近那座分矿的野怪之后一次开矿都没试，引擎一直喊「分矿有野怪先清」、
            #   战术层回「清分矿·兵力不够」×46 —— 原来只挑最近的，最近那座一被临时排除（敌兵路过）就换去打一座强得多的矿。
            #   现在按「距离 + 野怪强度 × EXP_GUARD_W」挑：同样远近先挑野怪弱的，13 强度的营地约等于多走 2000。
            guard = self._expansion_guards(w, m)
            score = d + EXP_GUARD_W * creep_strength(guard)
            if score < bd:
                best, bd = m, score
        return best

    def _ground_ok(self, code: str, x: float, y: float) -> bool:
        """地图块（W3P，运行时开局后算的 IsTerrainPathable 建造）说这块地能不能盖：占地中心 + 四角所在的格子都不能是「不能盖」。
        ★ 09-23 体检：造农场 1127 次被当场拒 363 次（32%），每次都等一帧、还把点拉黑。当场拒只发生在明显被占的点上，
          候选点已经避开了建筑 —— 剩下的是地形（悬崖 / 坡道 / 水边）。地图块还没算好 / 假对象：不挡。"""
        world = getattr(self.cmd, "world", None)
        try:
            mp = world.map() if world is not None else None
        except Exception:  # noqa: BLE001
            mp = None
        if mp is None:
            return True
        h = max(0.0, A.footprint(code) / 2.0 - 16.0)
        for dx, dy in ((0.0, 0.0), (-h, -h), (h, -h), (-h, h), (h, h)):
            v = mp.at(x + dx, y + dy)
            if v is None or v & 2:
                return False
        return True

    def _build_at(self, w, code: str, spots, kind: str = "building", note: str = "", pro: frozenset = frozenset()) -> None:
        if kind != "expand" and spots:
            kept = [p for p in spots if self._ground_ok(code, p[0], p[1])]
            if len(kept) != len(spots):
                self.counts[f"落点·地形不能盖·跳过·{code}"] += len(spots) - len(kept)
            spots = kept
        workers = self._builder(w, expansion=(kind == "expand"))
        if not workers or not spots:
            self.counts[f"建造·{'没工人' if not workers else '没落点'}·{code}"] += 1
            return
        tx, ty = spots[0]
        preferred = self.militia_expand_builders if kind == "expand" else set()
        worker = min(workers, key=lambda u: (u.addr not in preferred,
                                             u.order not in A.HARVEST_ORDERS and u.order != 0,
                                             math.hypot(u.x - tx, u.y - ty)))
        deadline = time.monotonic() + 1.2
        tried = 0
        for (x, y) in spots:
            if time.monotonic() > deadline and tried >= 2:
                break
            tried += 1
            r = self.act.build(worker.addr, code, x, y, prev_order=worker.order if getattr(worker, "order_known", False) else None)
            if r == "接下":
                self.pending.append(dict(kind=kind, code=code, t=w.t, worker=worker.addr, had=w.counts.get(code, 0), x=x, y=y))
                if kind == "expand" and worker.addr in self.militia_expand_builders:
                    self.militia_expand_builders.discard(worker.addr)
                    self.counts["民兵清分矿·转主建造工"] += 1
                # 用上高手落点没有：计数（0 = 又没生效）+ 建造日志里标出来，复盘/直播页都看得见
                src = "高手落点" if (x, y) in pro else ("高手点已占满或被退" if pro else "")
                if code in G.LUMBER_DROPS and self.trees:          # 伐木场离树多远（贴树林那条改动管不管用，看这个数）
                    near_tree = min((math.hypot(t["x"] - x, t["y"] - y) for t in self.trees if t.get("hp", 1) > 0), default=-1)
                    src = (src + "，" if src else "") + f"离最近的树 {int(near_tree)}"
                if pro:
                    self.counts[f"落点·{'高手' if (x, y) in pro else '没用高手'}·{code}"] += 1
                self.note(w.t, f"{note or 'build'} {code} @ ({int(x)},{int(y)}) 工人 0x{worker.addr:08X}（试了 {tried} 个点"
                               f"{'，' + src if src else ''}）")
                self._reserve(w)
                return
            if r == "被拒":
                self.bad_spots.setdefault(code, set()).add((x, y))
                # 埋点：被引擎退掉的落点离最近的金矿多远 —— 「不能建在离金矿过近的地方」到底是多少，先量出来再定 A.MINE_CLEAR
                if w.mines:
                    dm = min(math.hypot(m.x - x, m.y - y) for m in w.mines)
                    self.counts[f"落点被拒·{code}·离矿{int(dm // 128 * 128)}"] += 1
            if r == "抢不到":
                break
        self.counts[f"建造·没接下·{code}"] += 1

    def _reserve(self, w) -> None:
        """建造工写 reserved.json（mod_worker 不碰），40 秒，带句柄对。"""
        units = {}
        now_ms = int(time.time() * 1000)
        by = {u.addr: u for u in w.mine}
        for p in self.pending + [dict(worker=a) for a in self.powerbuild.busy_addrs | self.repairs.busy_addrs]:
            u = by.get(p.get("worker"))
            if u is None:
                continue
            units[str(u.addr)] = {"expiresAtMs": now_ms + 40000, "handleLo": getattr(u, "handle_lo", 0), "handleHi": getattr(u, "handle_hi", 0)}
        data = {"schema": 1, "gamePid": getattr(self.cmd, "pid", 0), "epoch": str(getattr(w, "epoch", "")), "writtenAtMs": now_ms, "units": units}
        path = os.path.join(self.inst_dir, "reserved.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except OSError:
            self.counts["reserved 写失败"] += 1

    # ── 续建（人族工地没工人就停工，旧 PowerShell AI ai:7637-7654 派最近的工人对工地下 smart）──
    def job_construction(self, w) -> None:
        self.repairs.step(self, w)
        self._militia_assist(w)             # 先于 powerbuild：原民兵协建的工地它不再另派远处的助手
        self.powerbuild.step(self, w)
        if not self.config["enable_construction"] or not self.spec["needs_builder"]:
            return
        """★ 09-17 实机 5 号：一座农场停在 54/500，旁边没人，人口卡 42/42 两分钟、金子攒到 948。
        工地判据 = 没见过满血 且 血量 SITE_STALL_S 游戏秒没涨（「见过满血就不是工地」，挨打的成品不算）。"""
        for b in w.my_buildings:
            if b.hp_max <= 0:
                continue
            # ★★ 09-22 晚 1-413：原来 >= 97% 就算「见过满血」—— 兵营停在 1491/1500 整局没人管（见 powerbuild.SITE_FULL_EPS）
            if site_full(b):
                self.seen_full.add(b.addr)
                self.site_hp.pop(b.addr, None)
                continue
            if b.addr in self.seen_full:
                continue
            prev = self.site_hp.get(b.addr)
            if prev is None or b.hp > prev[0] + 1.0:
                self.site_hp[b.addr] = (b.hp, w.t)
                continue
            if w.t - prev[1] < SITE_STALL_S:
                continue
            if any(p["kind"] == "resume" and p.get("site") == b.addr for p in self.pending):
                continue
            cands = [u for u in self._builder(w) if u.addr not in self.gold_crew] or self._builder(w)
            if not cands:
                self.counts["续建·没工人"] += 1
                continue
            u = min(cands, key=lambda u: math.hypot(u.x - b.x, u.y - b.y))
            if self.act.gather(u, b, order=A.ORDER_SMART):
                self.pending.append(dict(kind="resume", code=b.type, t=w.t, worker=u.addr, site=b.addr))
                if b.addr in self.powerbuild.sites:
                    self.powerbuild.sites[b.addr]['worker'] = u.addr
                    self.powerbuild.sites[b.addr]['worker_identity'] = (u.addr, getattr(u,'handle_lo',0), getattr(u,'handle_hi',0))
                self.gold_crew.pop(u.addr, None)
                self.lumber_crew.pop(u.addr, None)
                self.sent_at[u.addr] = w.t
                self.site_hp[b.addr] = (b.hp, w.t)
                self.counts[f"续建·派出·{b.type}"] += 1
                self.note(w.t, f"续建 {b.type} @ ({int(b.x)},{int(b.y)}) 血 {int(b.hp)}/{int(b.hp_max)}：工人 0x{u.addr:08X}")
                self._reserve(w)
            else:
                self.counts["续建·没接下"] += 1

    # ── 暗夜开矿：矿边的生命之树建成后缠绕金矿（AMAI ANCIENT_EXPANSION：先种树再 entangle）──
    def job_entangle(self, w) -> None:
        if self.spec.get("name") != "nightelf":
            return
        entangled = [b for b in w.my_buildings if b.type == "egol"]
        for tree in w.my_buildings:
            if tree.type not in self.spec["halls"] or not completed(tree):
                continue
            mines = [m for m in w.mines if m.type == "ngol" and math.hypot(m.x - tree.x, m.y - tree.y) < 1100
                     and not any(math.hypot(e.x - m.x, e.y - m.y) < 300 for e in entangled)]
            if not mines:
                continue
            mine = min(mines, key=lambda m: math.hypot(m.x - tree.x, m.y - tree.y))
            if w.t < self.mine_full_until.get(("entangle", mine.addr), -1e9):
                continue
            self.mine_full_until[("entangle", mine.addr)] = w.t + 30.0
            if self.act._claim(tree.addr, 3.0, "strategy_eco"):
                try:
                    ok = bool(self.cmd.cast_target(tree.addr, A.ORDER_ENTANGLE, mine.addr, mine.x, mine.y))
                except Exception:  # noqa: BLE001
                    ok = False
                self.counts[f"缠绕金矿·{'下了' if ok else '没下成'}"] += 1
                self.note(w.t, f"缠绕金矿 @ ({int(mine.x)},{int(mine.y)})：{'下了' if ok else '没下成'}")

    # ── 英雄：学技能 + 复活 ──
    def job_heroes(self, w) -> None:
        if not self.config["enable_heroes"]:
            return
        for h in w.my_heroes:
            try:
                lvl = self.cmd.hero_level(h.addr)
            except Exception:  # noqa: BLE001
                lvl = None
            self.hero_seen[h.addr] = (h.type, lvl or self.hero_seen.get(h.addr, (h.type, 1))[1])
            if h.addr not in self.hero_slot:
                self.hero_slot[h.addr] = len(self.hero_slot) + 1              # 第几个出的英雄（AMAI 加点表按它分行）
            if self.config["strategy_engine"] == "amai" and self.strategy is not None and self.strategy.started:
                # AMAI 自己选的英雄位次和加点线（主/次/稀有是开局掷出来的，spec §5.5）
                st = self.strategy
                st.heroes_seen.add(h.type)
                order = (R.SKILL_OVERRIDE.get(h.type)
                         or R.amai_skill_order(self.race, h.type, min(3, st.hero_slot(h.type)), st.skill_line(h.type))
                         or self.skills.get(h.type) or self.spec["skills"].get(h.type, []))
            else:
                order = (R.SKILL_OVERRIDE.get(h.type) or self.skills.get(h.type)
                         or R.amai_skill_order(self.race, h.type, min(3, self.hero_slot[h.addr]))
                         or self.spec["skills"].get(h.type, []))
            i = self.skill_idx.get(h.addr, 0)
            pts = self.act.hero_points(h.addr)
            while pts and i < len(order):
                # ★ 09-23 体检：学技能 404 次失败 321 次 —— 英雄等级不够就去学（大招 AHmt/AHpx 要 6 级、技能第 3 级要 5 级），
                #   失败了下面又无条件前进一格，这一格就**永远跳过**了（大招可能整局学不上）。先按等级要求判：不够就停在这一格等升级。
                need = G.skill_level_needed(order[i], (self._ability_rank(h.addr, order[i]) or 0) + 1)
                if need == 99:                                 # 已经学满了（加点表写多了）：跳过这一格，不去试
                    self.counts["学技能·已满级跳过"] += 1
                    i += 1
                    continue
                if need is not None and lvl is not None and lvl < need:
                    self.counts["学技能·等级不够先等"] += 1
                    break
                ok = self.act.learn(h.addr, order[i])
                i += 1                                         # 无条件前进（写错的码不能卡住后面的，旧 PowerShell AI 的规矩）
                self.note(w.t, f"{h.type} 学 {order[i - 1]}：{'学会' if ok else '没学上'}")
                # ★ 09-23 第二批体检：学会之后立刻重读技能点，读到的是还没更新的世界块（最多旧 50 ms）—— 以为还有点，
                #   接着学下一个被拒（没点了），上面又无条件前进一格 = 这一格被跳过。学会了就本地减一，不重读。
                pts = (pts - 1) if ok else self.act.hero_points(h.addr)
            self.skill_idx[h.addr] = i

    def _ability_rank(self, hero_addr: int, code: str) -> int | None:
        """英雄这个技能现在几级（没学 0）；读不到 None（不按等级挡，照旧试）。"""
        fn = getattr(self.cmd, "ability_level", None)
        try:
            return fn(hero_addr, code) if fn is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _refresh_dead(self, w) -> None:
        """读死亡英雄表（7 次调用，跟规划同频 3 秒）。认不出是谁的（大脑中途接管、没见过它活着）按 code="hero"、1 级估价。"""
        for h in w.my_heroes:
            self.hero_seen.setdefault(h.addr, (h.type, 1))
        if not self.config["enable_heroes"] or not w.mine:
            self.dead_waiting = []
            return
        # ★ 09-19 steptime 实测：每次 build（3 游戏秒）都读一遍 = 8 次通道请求，占大脑墙钟 17%，而大部分时间一个英雄都没死。
        #   只在「见过的英雄有人不在场上」（死了/在复活）或上次读到过死亡英雄时读；都在场上就 DEAD_READ_EVERY 兜底读一次
        alive = {h.addr for h in w.my_heroes}
        if (all(a in alive for a in self.hero_seen) and not self.dead_waiting
                and w.t - self.dead_read_at < DEAD_READ_EVERY):
            self.counts["死亡英雄表·英雄都在不读"] += 1
            return
        self.dead_read_at = w.t
        self.counts["死亡英雄表·读"] += 1
        ptrs = self.act.dead_heroes(w.mine[0].addr)
        if ptrs is None:
            self.counts["死亡英雄表读不到"] += 1
            return
        self.dead_waiting = [dict(ptr=p, code=self.hero_seen.get(p, (None, 1))[0], level=self.hero_seen.get(p, (None, 1))[1]) for p in ptrs]

    # ── 采金：大脑自己派（09-17 实机：mod_worker 走的动作 28 采金令不生效，新大脑实例不拉它）──
    def _idle_workers(self, w) -> list:
        if w.t >= self.militia_expand_until:
            self.militia_expand_builders.clear()
        reserved = {p.get("worker") for p in self.pending}
        reserved |= self.powerbuild.busy_addrs
        reserved |= self.repairs.busy_addrs
        return [u for u in w.my_workers if u.addr not in reserved and u.addr not in self.harass.busy_addrs
                and u.addr not in self.militia_expand_builders
                and u.addr not in self.militia_creep_ids
                and getattr(u, "order_known", True) and (u.order or 0) == 0 and getattr(u, "has_task", None) is False
                and w.t - self.sent_at.get(u.addr, -1e9) > 4.0]

    def job_gold(self, w) -> None:
        if not self.config["enable_gold"]:
            return
        visible = {u.addr: u for u in w.my_workers}
        mines_all = {m.addr: m for m in w.mines if m.type in ("ngol", "ugol", "egol")}
        live_mine_ids = {(m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)) for m in mines_all.values()}
        self.depleted_mines.intersection_update(live_mine_ids)
        self.gold_failures = {k: v for k, v in self.gold_failures.items() if k in live_mine_ids}
        # 普通金矿采空后单位壳仍存在。若采集令已接下，但工人 12 游戏秒后仍在矿边且明确无
        # 订单/任务，说明这座矿不能再产金。用两名不同工人的回执确认，避免把一次拥堵误判为空矿。
        for addr, attempt in list(self.gold_attempts.items()):
            mine_id, sent = attempt
            u, m = visible.get(addr), mines_all.get(mine_id[0])
            if m is None or mine_id not in live_mine_ids or self.gold_crew.get(addr) != m.addr:
                self.gold_attempts.pop(addr, None)
                continue
            if u is None:
                self.gold_attempts.pop(addr, None)  # 已进入矿井，采集回执成立
                continue
            if (u.order or 0) in A.HARVEST_ORDERS and getattr(u, "has_task", None) is not False:
                self.gold_attempts.pop(addr, None)
                continue
            if w.t - sent < 12.0:
                continue
            if (getattr(u, "order_known", False) and (u.order or 0) == 0
                    and getattr(u, "has_task", None) is False and math.hypot(u.x-m.x, u.y-m.y) <= 700):
                worker_id = (u.addr, getattr(u, "handle_lo", 0), getattr(u, "handle_hi", 0))
                failed = self.gold_failures.setdefault(mine_id, set())
                failed.add(worker_id)
                self.gold_crew.pop(addr, None)
                self.gold_attempts.pop(addr, None)
                self.mine_full_until[m.addr] = w.t + 15.0
                self.counts["采金·空矿回执"] += 1
                if len(failed) >= 2:
                    self.depleted_mines.add(mine_id)
                    self.note(w.t, f"采金：两名农民都无法进入 ({int(m.x)},{int(m.y)})，判定矿已采空；后续转二矿或伐木")
            elif w.t - sent >= 45.0:
                self.gold_attempts.pop(addr, None)
        # 进矿的工人快照里看不见：看不见的保留；看得见但订单不是采集一族的，从名单里去掉
        for a in list(self.gold_crew):
            u = visible.get(a)
            if u is not None and (u.order or 0) not in A.HARVEST_ORDERS and w.t - self.sent_at.get(a, -1e9) > 6.0:
                self.gold_crew.pop(a)
        halls = [h for h in w.my_halls if completed(h)]
        if self.spec["expand_on_mine"]:     # 亡灵：自己建成的闹鬼金矿就是矿，不看旁边有没有大厅
            mines = [m for m in w.my_buildings if m.type == "ugol" and completed(m)]
        elif self.spec.get("mine_types"):   # 暗夜：只认自己缠绕过的金矿（旁边那座中立矿下不了令）
            mines = [m for m in w.my_buildings if m.type in self.spec["mine_types"]]
        else:
            mines = [m for m in w.mines if m.type in ("ngol", "ugol", "egol") and any(math.hypot(m.x - h.x, m.y - h.y) < 1000 for h in halls)]
        mines = [m for m in mines
                 if (m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)) not in self.depleted_mines]
        if not mines:
            # ★ 09-21 4-009：金矿被打爆后没钱再造大厅，农民也不去另一座矿采集，整局卡死。
            #   实机 1922~2401 游戏秒金子**恒定 24**、20 个农民站着不动：主矿和分矿都采空之后，
            #   上面那条「离我方大厅 1000 以内」的筛选让 mines 为空，这里直接 return，农民彻底没活干。
            #   可 War3 里农民能去**任何**金矿采，把金子运回最近的大厅 —— 远只是慢，不是不能采，
            #   而这时候「慢的收入」和「零收入」的差别，就是这局还有没有翻盘的可能。
            mines = self._far_mines(w, halls)
            if not mines:
                self.counts["采金·全图都没有能采的矿"] += 1
                if self.race in AMAI_HARVEST_RACES:
                    self._amai_harvest(w, [])      # 没矿可挖：全员砍木（job_lumber 对这两族已经让出派工）
                return
            self.counts["采金·附近没矿了·去远矿"] += 1
        crew_n = Counter(self.gold_crew.values())
        # ★ 09-18 暗夜中途接管：前一个进程派上矿的小精灵不在名单里，大脑以为矿没满，往满矿塞闲人被拒 188 次、闲人也没去伐木。
        #   人数按「名单」和「矿边 320 内正在采集的」取大；被拒一次这座矿 20 游戏秒内不再塞
        near = Counter()
        for u in w.my_workers:
            if (u.order or 0) in A.HARVEST_ORDERS:
                for m in mines:
                    if math.hypot(u.x - m.x, u.y - m.y) < 320:
                        near[m.addr] += 1
        self.mines_now = mines          # job_lumber 按现场分「挖金 / 砍树」要用同一份矿表
        if self.race in AMAI_HARVEST_RACES:
            # ★ 09-21 改为参考 AMAI 的派工方式：人族 / 兽族的采金 + 伐木派工整个换成
            #   AMAI universal_peon_assignment（brain/harvest.py）。下面那一套（只派闲人 + 打补丁）留给暗夜 / 亡灵。
            self._amai_harvest(w, mines)
            return
        # ★ 09-21 1-262：金 3000、木 30 整整 7 分钟，闲下来的农民照样先塞进金矿。
        #   金多木少（和建造表同一个判据 tables.wood_short）且砍树的不够时，闲人留给 job_lumber。
        idle_to_wood = False
        if wood_short(w):
            _bm, _gu, wood_units = self.census(w, mines)
            idle_to_wood = len(wood_units) < self.lumber_target(w)
        for u in self._idle_workers(w):
            if idle_to_wood:
                self.counts["采金·闲人让给伐木（金多木少）"] += 1
                break
            room = [m for m in mines if max(crew_n[m.addr], near[m.addr]) < 5 and w.t >= self.mine_full_until.get(m.addr, -1e9)]
            if not room:
                break
            m = min(room, key=lambda m: math.hypot(m.x - u.x, m.y - u.y))
            if self.act.gather(u, m):
                self.gold_crew[u.addr] = m.addr
                self.gold_attempts[u.addr] = ((m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)), w.t)
                self.lumber_crew.pop(u.addr, None)
                self.sent_at[u.addr] = w.t
                crew_n[m.addr] += 1
                self.counts["采金·派出"] += 1
            else:
                self.counts["采金·没接下"] += 1
                self.mine_full_until[m.addr] = w.t + 20.0
        self._rebalance(w, mines, crew_n, near)

    def _harvest_pool(self, w) -> int:
        """参与采集分配的农民总数：看得见、没被派去盖房/修理/骚扰/当民兵/预留建分矿的，
        加上进了矿井看不见、名单里挂着的。AMAI 的 SelectByPeons 也只数「准备好采集」的农民。"""
        busy = ({p.get("worker") for p in self.pending} | self.powerbuild.busy_addrs | self.repairs.busy_addrs
                | self.harass.busy_addrs | self.militia_creep_ids | self.militia_expand_builders)
        visible = {u.addr for u in w.my_workers}
        n = sum(1 for u in w.my_workers if u.addr not in busy)
        return n + sum(1 for a in self.gold_crew if a not in visible and a not in busy)

    def _amai_harvest(self, w, mines) -> None:
        """AMAI universal_peon_assignment（brain/harvest.py）：按一张优先级表算出每座矿几个、伐木几个，
        闲人马上按表派；在采的人每 REBALANCE_EVERY 秒最多挪一个（从「多出来的」挪到「最缺的」）。

        ⚠ 只有两种挪法，而且都以**同一张表**为准 —— 09-21 1-262 那种「两个方向各有各的判据、互相抢」
          在结构上不可能再发生：多出来和缺人是同一张表算出来的。"""
        main = w.main_town()
        ordered = sorted(mines, key=lambda m: math.hypot(m.x - main.x, m.y - main.y)) if main else list(mines)
        ordered = [m for m in ordered if w.t >= self.mine_full_until.get(m.addr, -1e9)] or ordered
        by_addr = {m.addr: m for m in ordered}
        table = HV.slots(w.gold, w.lumber, [m.addr for m in ordered], heroes=len(w.my_heroes))
        # ⚠ 先取闲人：_idle_workers 顺手清掉**过期**的分矿预留。反过来的话，同一拍里人数按「预留还在」算、
        #   闲人却按「预留已清」算，两边口径不一致（测试抓到：4 个闲人只派出去 2 个）。
        idle = self._idle_workers(w)
        n = self._harvest_pool(w)
        by_mine, gold_units, wood_units = self.census(w, ordered)
        want_gold, want_wood = HV.desired(table, n)
        self.harvest_plan = dict(n=n, gold={a: want_gold[a] for a in want_gold}, wood=want_wood,
                                 haveGold=dict(by_mine), haveWood=len(wood_units))

        # 1. 闲人：按表挨个派到最缺的位
        need = HV.deficits(table, n, by_mine, len(wood_units))
        for u in idle:
            if not need:
                break
            kind, a = need.pop(0)
            if kind == "gold":
                m = by_addr[a]
                if self.act.gather(u, m):
                    self.gold_crew[u.addr] = m.addr
                    self.gold_attempts[u.addr] = ((m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)), w.t)
                    self.lumber_crew.pop(u.addr, None)
                    self.sent_at[u.addr] = w.t
                    by_mine[a] += 1
                    self.counts["采金·派出（AMAI）"] += 1
                else:
                    self.counts["采金·没接下"] += 1
                    self.mine_full_until[m.addr] = w.t + 20.0
            elif self._send_chop(w, u):
                self.counts["伐木·派出（AMAI）"] += 1
        if not need:
            return

        # 2. 没闲人了还有缺口：从「多出来的」挪一个到「最缺的」，每 REBALANCE_EVERY 秒最多一个
        if w.t - self.rebalance_at < REBALANCE_EVERY:
            return
        extra_gold, extra_wood = HV.surplus(table, n, by_mine, len(wood_units))
        kind, a = need[0]
        recent = lambda u: w.t - self.sent_at.get(u.addr, -1e9) < 3 * REBALANCE_EVERY
        if kind == "wood":
            cand = [u for u in gold_units if extra_gold.get(self._nearest_mine(u, ordered), 0) > 0 and not recent(u)]
            if not cand:
                self.counts["伐木·缺人·没有多余的采金工（等新农民）"] += 1
                return
            u = min(cand, key=lambda u: min((math.hypot(u.x - t["x"], u.y - t["y"]) for t in self.trees), default=0))
            if self._send_chop(w, u):
                self.rebalance_at = w.t
                self.counts["伐木·从采金调人（AMAI）"] += 1
            return
        m = by_addr[a]
        cand = [u for u in wood_units if not recent(u)] if extra_wood > 0 else []
        cand += [u for u in gold_units if self._nearest_mine(u, ordered) != a
                 and extra_gold.get(self._nearest_mine(u, ordered), 0) > 0 and not recent(u)]
        if not cand:
            self.counts["采金·缺人·没有多余的人（等新农民）"] += 1
            return
        u = min(cand, key=lambda u: math.hypot(u.x - m.x, u.y - m.y))
        if self.act.gather(u, m):
            self.lumber_crew.pop(u.addr, None)
            self.gold_crew[u.addr] = m.addr
            self.gold_attempts[u.addr] = ((m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)), w.t)
            self.sent_at[u.addr] = w.t
            self.rebalance_at = w.t
            self.counts["采金·调人补矿（AMAI）"] += 1
        else:
            self.counts["采金·调人补矿·被拒"] += 1

    @staticmethod
    def _nearest_mine(u, mines):
        return min(mines, key=lambda m: math.hypot(u.x - m.x, u.y - m.y)).addr if mines else None

    def job_unit_ids(self, w) -> None:
        """血条编号（brain/unitids.py）：新出现的我方英雄 / 作战单位分号、写进对局记录、发布给毫秒层、在血条上方标出来。
        标不上（旧 DLL、这个实例开着对话气泡、没接上游戏）只影响显示，编号和日志照常。"""
        ids = self.unit_ids
        for n, addr, typ in ids.assign(w.my_heroes + w.my_army):
            self.note(w.t, f"编号 #{n} {typ}@{addr:X}")
        pid = int(getattr(self.cmd, "pid", 0) or 0)
        ids.publish(os.path.join(self.inst_dir, "unit_ids.json"), pid)
        if not self.config["enable_unit_labels"] or not pid or self.labeler is False:
            if self.labeler and ids.labeled and not self.config["enable_unit_labels"]:
                try:
                    self.labeler.say(ids.bubbles([], []))        # 关掉开关：把已经标上的撤掉
                except Exception:  # noqa: BLE001
                    pass
            return
        inst = os.path.basename(os.path.normpath(self.inst_dir)).replace("inst", "")
        if inst.isdigit() and UID.speech_on(int(inst), os.path.join(A.w3paths.SPEECH, "speak_api.json")):
            self.label_off = "这个实例开着对话气泡，编号让开（同一个单位头上只能有一个气泡）"
            ids.counts["让给对话气泡"] += 1
            return
        try:
            if self.labeler is None:
                import sys
                mod = A.MODULES
                if mod not in sys.path:
                    sys.path.append(mod)
                import w3speak
                self.labeler = w3speak.Speaker(pid)
            foes = [u for u in w.enemy_fighters if u.hp > 0] + [c for c in w.creeps if c.hp > 0]
            self.labeler.say(ids.bubbles(w.my_heroes + w.my_army, foes))
            self.label_off = ""
        except Exception as e:  # noqa: BLE001 - 显示层出任何问题都不能影响这一局
            self.label_off = f"{type(e).__name__}：{str(e)[:80]}"
            self.counts[f"编号·标不上·{type(e).__name__}"] += 1
            if type(e).__name__ == "BubbleUnsupported":
                self.labeler = False                              # 旧 DLL：这一局不再试

    def job_coach(self, w) -> None:
        """问一次运营顾问（本地大模型）。**永远不阻塞这一拍** —— 发请求在后台线程里。"""
        if not self.config["enable_coach"]:
            self.coach.enabled = False      # 远见上要显示「关着」，不能停在上一次的状态
            return
        self.coach.enabled = True
        self.coach.race = self.race or "human"
        try:
            self.coach.tick(w.t, CH.snapshot(w, self))
        except Exception as e:  # noqa: BLE001 - 顾问出任何问题都不能影响这一局
            self.counts[f"顾问·取快照出错·{type(e).__name__}"] += 1

    def coach_plan(self, w):
        """当前有效的顾问建议；没有就返回 None（行为完全等于没有这一层）。"""
        return self.coach.plan(w.t) if self.config["enable_coach"] else None

    COACH_PRIO_BUMP = 12       # 顾问点名的东西优先级加多少：够把同类往前挪一两位，不够翻盘整张表

    def _coach_wants(self, w, wants: list) -> list:
        """顾问点名「优先出什么」-> 给**已经在需求表里**的那几项加优先级。

        ⚠ 只**重排**，不凭空新增：模型可能说出这一族根本造不了的东西，或者科技没到的东西。
          需求表本身是有前置条件的（有没有兵营、够不够本），绕过它等于把那些前置全丢掉。
          所以点名了但表里没有的，记一笔 `顾问·优先项·表里没有`，下一轮看是提示词该改还是表该改。"""
        p = self.coach_plan(w)
        if p is None or not p.priority:
            return wants
        have = {x.code for x in wants}
        for rank, code in enumerate(p.priority):
            if code not in have:
                self.counts[f"顾问·优先项·表里没有·{code}"] += 1
                continue
            for x in wants:
                if x.code == code:
                    x.prio = int(x.prio) + self.COACH_PRIO_BUMP - rank
            self.counts["顾问·优先项·采纳"] += 1
        return wants

    def lumber_target(self, w) -> int:
        """该有几个人砍树。顾问给了数就用顾问的，没有就用建造表那份规则。

        ★ 09-21：这是顾问接进执行层的**第一个**口子，对应的是
        「木头紧缺也不增派伐木」那个问题。顾问给的数已经在 coach.clamp 里钳过
        （LUMBER_MIN~LUMBER_MAX），这里再钳一次「不能超过农民总数减去采金地板」——
        两道钳位不是重复：一道防模型乱说，一道防局面变化（农民刚死了一半）。"""
        base = lumber_crew(w, dict(self.profile, peons_lumber=self.profile.get("peons_lumber", 8)))
        p = self.coach_plan(w)
        if p is None or p.lumber is None:
            return base
        # ★★ 09-21 1-262：金 3000、木 30，规则算出来要 10 个人砍树（缺木头 +5），
        #   顾问一律说 3~4 个（6 个实例给的数几乎一样，是在照抄提示词里的示例，不是在读局面），
        #   而上一版是「顾问给了就用顾问的」—— **在木头最缺的时候把伐木队砍掉一半多**。
        #   规则里的「缺木头多派」是有证据的（09-19 第 39 局木头整局 0~150、金子堆到 6000），
        #   顾问还没证明自己比它准之前，**只许往上加、不许往下压**。
        room = max(LUMBER_KEEP, len(w.my_workers) - GOLD_KEEP)
        if p.lumber <= base:
            self.counts["顾问·伐木人数·不高于规则·按规则" if p.lumber == base else "顾问·伐木人数·比规则少·不采纳"] += 1
            return base
        n = max(LUMBER_KEEP, min(p.lumber, room))
        self.counts["顾问·伐木人数·比规则多·采纳" if n == p.lumber else "顾问·伐木人数·被局面钳住"] += 1
        return max(n, base)

    def _role(self, u, mines) -> str:
        """一个正在采集的农民现在是在挖金还是砍树 —— 按**现场**判，名单只当旁证。

        ★★ 09-21 1-262 局：名单（gold_crew / lumber_crew）大部分时间是**空的**
        （「伐木·缺人·采金名单0人」累计 169 次），而农民都在采：开局自动采矿的不是大脑派的、
        旧版反向调人又只删名单不下令。名单 = 「我派过谁」，不是「谁在采」。
        运货途中订单仍是 harvest/resumeharvesting（实测 252 个订单里 returnresources 0 次），
        所以按「订单是采集 + 在哪儿」分：
          矿边 320 内          金（进出矿井的那一段，不会错）
          名单里有             按名单
          离已知的树 250 内    木
          离矿 900 内          金（在矿和大厅之间运货）
          其余                 木"""
        dm = min((math.hypot(u.x - m.x, u.y - m.y) for m in mines), default=1e9)
        if dm <= 320:
            return "gold"
        if u.addr in self.lumber_crew:
            return "wood"
        if u.addr in self.gold_crew:
            return "gold"
        dt = min((math.hypot(u.x - t["x"], u.y - t["y"]) for t in self.trees), default=1e9)
        if dt <= 250:
            return "wood"
        return "gold" if dm <= 900 else "wood"

    def census(self, w, mines):
        """(每座矿几个人, 挖金的看得见的农民, 砍树的农民)。进了矿井看不见的，名单里有的算在那座矿上。"""
        from .act import HARVEST_ORDERS
        visible = {u.addr for u in w.my_workers}
        by_mine, gold_units, wood_units = Counter(), [], []
        for u in w.my_workers:
            if (u.order or 0) not in HARVEST_ORDERS:
                continue
            if self._role(u, mines) == "gold" and mines:
                m = min(mines, key=lambda m: math.hypot(u.x - m.x, u.y - m.y))
                by_mine[m.addr] += 1
                gold_units.append(u)
            else:
                wood_units.append(u)
        mine_ids = {m.addr for m in mines}
        for a, mid in self.gold_crew.items():
            if a not in visible and mid in mine_ids:
                by_mine[mid] += 1
        return by_mine, gold_units, wood_units

    def _chop_anchor(self, w, main):
        """挑哪片树按这个点：有盖好的伐木场（主基地 2000 内）就按伐木场 —— 它就盖在树林边上（_building_spots 的贴树候选），
        砍它旁边的树、就近交木头；没有就按主基地（原来的规则）。"""
        mills = [b for b in getattr(w, "my_buildings", ()) if b.type in G.LUMBER_DROPS and completed(b)
                 and math.hypot(b.x - main.x, b.y - main.y) <= 2000]
        return min(mills, key=lambda b: math.hypot(b.x - main.x, b.y - main.y)) if mills else main

    def _pick_tree(self, anchor, u):
        return min(self.trees, key=lambda t: abs(t["x"] - anchor.x) + abs(t["y"] - anchor.y) + (abs(t["x"] - u.x) + abs(t["y"] - u.y)) / 3)

    def _send_chop(self, w, u) -> bool:
        """派一个农民去砍最近的树（和 job_lumber 同一份挑树规则 _pick_tree）。**真的下令**，名单跟着令走。"""
        main = w.main_town()
        if main is None:
            return False
        if w.t - self.trees_at > 60.0 or not self.trees:
            self.trees = self.act.scan_trees(main.x, main.y)
            self.trees_at = w.t
        if not self.trees:
            self.counts["伐木·找不到树"] += 1
            return False
        tree = self._pick_tree(self._chop_anchor(w, main), u)
        if not self.act.gather(u, _Obj(tree["addr"], tree["x"], tree["y"])):
            return False
        self.lumber_crew[u.addr] = tree["addr"]
        self.gold_crew.pop(u.addr, None)
        self.gold_attempts.pop(u.addr, None)
        self.sent_at[u.addr] = w.t
        return True

    def _rebalance(self, w, mines, crew_n=None, near=None) -> None:
        """没有闲人时，按**伐木目标**在采金和伐木之间调一个人。

        ★★ 09-21 1-262 局：木头已经不够用，却不补农民，只有一个农民在伐木、另一个闲着；
        运营层的决策反而不如旧设计。根因主要是**上一版这里写坏了**：
          * 反方向（「放 1 个采金的去砍树」）**只删名单、没下令** —— 农民照样挖金，名单被一次次删空，
            这一局刷了 20 多次，木头一点没涨；
          * 名单空了矿就「看起来缺人」，正方向又去伐木队里抢人，伐木被抢到只剩 LUMBER_KEEP=1 个。
          两条互相打架 —— 又是「拿带滞后的量连续驱动动作」会自激，上一版的注释里其实已经写过这条教训。

        现在：
          * 人数按现场数（census），不按名单；
          * 调人一定**真的下令**，名单跟着令走；
          * **伐木目标是唯一的仲裁**：砍树的少于目标才从金矿调、多于目标才往金矿放 ——
            同一个目标两边看，不可能再互相抢；
          * 金多木少（tables.wood_short，和建造表同一个判据）时木头优先：金矿没满也调；
          * 每 REBALANCE_EVERY 秒最多一个；两边各留地板；调不动也记数（原因分开记）。
        """
        if w.t - self.rebalance_at < REBALANCE_EVERY or not mines:
            return
        cap = int(self.profile.get("peons_per_mine", 5))
        by_mine, gold_units, wood_units = self.census(w, mines)
        gold_n, wood_n = sum(by_mine.values()), len(wood_units)
        target = self.lumber_target(w)
        room = [m for m in mines if by_mine[m.addr] < cap and w.t >= self.mine_full_until.get(m.addr, -1e9)]
        short = wood_short(w)
        recent = lambda u: w.t - self.sent_at.get(u.addr, -1e9) < 3 * REBALANCE_EVERY   # 刚调过的别马上再调

        if wood_n < target and gold_n > GOLD_KEEP and (short or not room):
            # 木头不够：挑离树最近的那个挖金的（通常是刚运完货往回走的）
            cand = [u for u in gold_units if not recent(u)]
            if not cand:
                self.counts["伐木·缺人·挖金的都刚调过"] += 1
                return
            u = min(cand, key=lambda u: min((math.hypot(u.x - t["x"], u.y - t["y"]) for t in self.trees), default=0))
            if self._send_chop(w, u):
                self.rebalance_at = w.t
                self.counts["伐木·从采金调人" + ("·缺木头优先" if short and room else "")] += 1
                self.note(w.t, f"木头不够：砍树 {wood_n}/{target} 人，采金 {gold_n} 人"
                               f"{'（金多木少，木头优先）' if short else '（金矿已满）'}：调 1 人去砍树")
            else:
                self.counts["伐木·从采金调人·被拒"] += 1
            return
        if room and wood_n > target:
            # 金矿有空位、砍树的**多于目标**：多出来的那几个才能调（绝不把伐木压到目标以下）
            cand = [u for u in wood_units if not recent(u)]
            if not cand:
                self.counts["采金·缺人·砍树的都刚调过"] += 1
                return
            m = room[0]
            u = min(cand, key=lambda u: math.hypot(u.x - m.x, u.y - m.y))
            if self.act.gather(u, m):
                self.lumber_crew.pop(u.addr, None)
                self.gold_crew[u.addr] = m.addr
                self.gold_attempts[u.addr] = ((m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)), w.t)
                self.sent_at[u.addr] = w.t
                self.rebalance_at = w.t
                self.counts["采金·从伐木调人"] += 1
                self.note(w.t, f"矿上缺人（{gold_n}/{cap * len(mines)}），砍树 {wood_n} 人多于目标 {target}：调 1 人回矿")
            else:
                self.counts["采金·从伐木调人·被拒"] += 1
            return
        # 两边都缺 = 农民不够，那是建造表的事（农民目标 = 每矿 5 + 伐木人数）。记下来，不硬调。
        if room and wood_n < target:
            self.counts["采金伐木都缺·等新农民"] += 1
        elif room:
            self.counts["采金·缺人·砍树的不多余"] += 1

    # ── 伐木（矿满了的人去砍树）──
    def _far_mines(self, w, halls) -> list:
        """附近的矿都采空/被毁之后的兜底：全图找还能采的金矿。

        只在 job_gold 找不到近矿时才调用（平时不该把农民撒满全图）。
        排除：已判采空的、敌方老家门口的、旁边有敌军或野怪的 —— 派农民过去等于送人头。
        按离我方大厅的距离排序，只取最近的两座：金子要运回大厅，太远的来回就没收益了。"""
        if not halls:
            return []
        out = []
        for m in w.mines:
            if m.type not in ("ngol", "ugol", "egol") or m.hp <= 0:
                continue
            if (m.addr, getattr(m, "handle_lo", 0), getattr(m, "handle_hi", 0)) in self.depleted_mines:
                continue
            if any(math.hypot(m.x - h.x, m.y - h.y) < FAR_MINE_FOE_R for h in w.enemy_halls):
                continue
            if any(math.hypot(m.x - f.x, m.y - f.y) < FAR_MINE_FOE_R for f in w.enemy_fighters):
                continue
            if any(math.hypot(m.x - c.x, m.y - c.y) < FAR_MINE_CREEP_R for c in w.creeps if c.hp > 0):
                continue
            out.append(m)
        out.sort(key=lambda m: min(math.hypot(m.x - h.x, m.y - h.y) for h in halls))
        return out[:2]

    def job_lumber(self, w) -> None:
        if not self.config["enable_lumber"]:
            return
        main = w.main_town()
        if main is None:
            return
        ghoul = self.spec["lumber_unit"] != self.spec["worker"]
        pool = [u for u in w.my_army if u.type == self.spec["lumber_unit"]] if ghoul else w.my_workers
        alive = {u.addr: u for u in pool}
        self.lumber_crew = {a: t for a, t in self.lumber_crew.items() if a in alive}
        # 看得见但订单已经不是采集一族的（树砍光了、被打断了）从名单里去掉，下一轮当闲人重派
        for a in list(self.lumber_crew):
            if (alive[a].order or 0) not in A.HARVEST_ORDERS and w.t - self.sent_at.get(a, -1e9) > 6.0:
                self.lumber_crew.pop(a)
        # 伐木人数和建造表的农民数同一份规则（tables.lumber_crew：木头多了减半、金多木少多派）；
        # 09-21 起顾问给了数就听顾问的（brain/coach.py），见 lumber_target
        crew = self.lumber_target(w)
        target = 3 if (w.counts.get(self.spec["barracks"], 0) == 0 and not ghoul) else crew
        target = min(target, crew)
        if ghoul and w.lumber >= 2 * self.profile.get("lumber_high", 800):
            target = min(target, 1)                                         # 和 undead.wants 同一个门槛：木头远多于金子只留 1 个
        if ghoul and len(self.lumber_crew) > target:
            # ★ 09-18 亡灵实机木头 1100、金子 205：木头多了伐木食尸鬼要放回军队（人族农民不放：放了也是闲着）
            for a in list(self.lumber_crew)[target:]:
                self.lumber_crew.pop(a)
                self.counts["伐木·食尸鬼回军队"] += 1
        # ★ 09-21 1-262：够不够按**现场**砍树的人数判，不按名单 —— 名单这局大部分时间是空的，
        #   而名单偏大的时候（砍树的被打断、被抓去盖房）又会以为人够了。人族/兽族/暗夜的伐木工是工人，
        #   用 census；亡灵是食尸鬼，没有「在矿边」这回事，照旧按名单。
        if self.race in AMAI_HARVEST_RACES and not ghoul:
            return          # 人族 / 兽族的伐木由 job_gold 里的 AMAI 派工一起管（同一张表，不能两处各派一份）
        have = len(self.lumber_crew) if ghoul else len(self.census(w, self.mines_now)[2])
        if have >= target:
            return
        if w.t - self.trees_at > 60.0 or not self.trees:
            self.trees = self.act.scan_trees(main.x, main.y)
            self.trees_at = w.t
        if not self.trees:
            self.counts["伐木·找不到树"] += 1
            return
        if ghoul:   # 亡灵：伐木的是食尸鬼（作战单位），挑闲着的、离家 2500 内的
            idle = [u for u in pool if u.addr not in self.lumber_crew and u.addr not in self.harass.busy_addrs and (u.order or 0) == 0
                    and getattr(u, "has_task", None) is False and math.hypot(u.x - main.x, u.y - main.y) < 2500
                    and w.t - self.sent_at.get(u.addr, -1e9) > 4.0]
        else:
            # 闲着就是闲着：名单里还挂在金矿上的也算（名单是「派过谁」，不是「谁在挖」）
            idle = [u for u in self._idle_workers(w) if u.addr not in self.lumber_crew]
        anchor = self._chop_anchor(w, main)
        for u in idle[: target - have]:
            tree = self._pick_tree(anchor, u)
            if self.act.gather(u, _Obj(tree["addr"], tree["x"], tree["y"])):
                self.lumber_crew[u.addr] = tree["addr"]
                self.gold_crew.pop(u.addr, None)            # 名单跟着令走（见 _rebalance 的注释）
                self.sent_at[u.addr] = w.t
                self.counts["伐木·派出"] += 1
            else:
                self.counts["伐木·没接下"] += 1

    # ── 集结点：主基地朝地图中心 700（离家 > 1500 退回家），变化 > 400 才重设 ──
    def job_rally(self, w) -> None:
        main = w.main_town()
        if main is None:
            return
        d = math.hypot(main.x, main.y) or 1.0
        rx, ry = main.x - main.x / d * 700, main.y - main.y / d * 700
        for b in w.my_buildings:
            if b.type not in self.spec["rally"] or not completed(b):
                continue
            last = self.rally_at.get(b.addr)
            if last and math.hypot(last[0] - rx, last[1] - ry) < 400:
                continue
            if self.act.rally(b.addr, rx, ry):
                self.rally_at[b.addr] = (rx, ry)

    # ── 战术：守家 > 出击循环；骚扰小队单独 ──
    def _home_militia_step(self, w) -> None:
        """★★ 09-22 晚（1-405）敌军打进家时，农民要变民兵一起守家。
        1-405 6.9 游戏分钟：对面 8 步兵 4 火枪 + 山丘打进主基地，我方 7 个兵、16 个农民一直在采矿 —— 60 秒兵打光（战损 10:1）、认输。
        规则：敌方作战单位 >= HOME_MIL_MIN_FOES 个进了我方某座大厅 HOME_MIL_R 内，守军（兵 + 英雄 + 已有民兵）对它们的战力比
        < HOME_MIL_RATIO -> 这座大厅 HOME_MIL_WORKER_R 内没在盖 / 修的农民全部号召（Amil：跑进大厅、出来是民兵，守家时 Defense
        本来就把民兵算进守军）；民兵 45 游戏秒到点自己变回，敌人还在就再叫（每人隔 HOME_MIL_RECALL_S）；
        敌人离开满 HOME_MIL_CLEAR_S -> 全部解除回去干活。只有人族有民兵。"""
        if self.spec.get("worker") != "hpea":
            return
        foes = [f for f in w.enemy_fighters if f.hp > 0]
        best = None
        for h in w.my_halls:
            near = [f for f in foes if math.hypot(f.x - h.x, f.y - h.y) <= HOME_MIL_R]
            if len(near) >= HOME_MIL_MIN_FOES and (best is None or len(near) > len(best[1])):
                best = (h, near)
        alive = {u.addr: u for u in w.mine}
        self.home_militia_ids = {a for a in self.home_militia_ids if a in alive}
        if best is None:
            if self.home_militia_ids and w.t - self.home_militia_seen >= HOME_MIL_CLEAR_S:
                released = 0
                for a in list(self.home_militia_ids):
                    u = alive[a]
                    if u.type == "hmil" and self.act.militia(u, False):
                        released += 1
                    self.home_militia_ids.discard(a)
                self.counts["守家民兵·敌人走了解除"] += released
                if released:
                    self.note(w.t, f"守家民兵：敌人离开了，{released} 个民兵解除、回去干活")
            return
        self.home_militia_seen = w.t
        hall, attackers = best
        defenders = [u for u in w.my_army + w.my_heroes + w.my_militia
                     if u.hp > 0 and math.hypot(u.x - hall.x, u.y - hall.y) <= HOME_MIL_R + 800]
        ratio = BP.estimate(defenders, attackers, w.levels)["ratio"] if defenders else 0.0
        if ratio >= HOME_MIL_RATIO:
            self.counts["守家民兵·守得住不叫"] += 1
            return
        self._call_militia(w, hall, f"{len(attackers)} 个敌人打到 {hall.type} 边上，守军战力比 {ratio:.2f} < {HOME_MIL_RATIO}")

    def _call_militia(self, w, hall, why: str) -> int:
        """这座大厅 HOME_MIL_WORKER_R 内没在盖 / 修 / 骚扰的农民全部号召成民兵（守家民兵和防塔攻共用；解除在 _home_militia_step）。"""
        if self.spec.get("worker") != "hpea" or hall is None:
            return 0
        called_at = self.home_militia_called
        busy = ({p.get("worker") for p in self.pending} | self.powerbuild.busy_addrs | self.repairs.busy_addrs
                | self.harass.busy_addrs | self.militia_creep_ids)
        workers = [u for u in w.my_workers if u.type == "hpea" and u.addr not in busy and u.hp > 0
                   and math.hypot(u.x - hall.x, u.y - hall.y) <= HOME_MIL_WORKER_R
                   and w.t - called_at.get(u.addr, -1e9) >= HOME_MIL_RECALL_S]
        called = 0
        for u in workers:
            if self.act.militia(u, True):
                called += 1
                called_at[u.addr] = w.t
                self.home_militia_ids.add(u.addr)
                self.lumber_crew.pop(u.addr, None)
                self.gold_crew.pop(u.addr, None)
        if called:
            self.counts["守家民兵·号召"] += called
            self.note(w.t, f"守家民兵：{why}，号召 {called} 个农民变民兵一起守")
        return called

    def _militia_creep_step(self, w, busy_def: bool) -> None:
        """INFI/AMAI 的分矿民兵链：号召 -> 随主力清守军 -> 就地解除，供建矿/采集复用。"""
        alive = {u.addr: u for u in w.mine}
        ct = self.attack.clear_target
        active = [alive[a] for a in self.militia_creep_ids if a in alive]
        transformed = [u for u in active if u.type == "hmil"]
        if transformed:
            self.militia_seen_hmil = True

        # 农民奔向/进入大厅转换时会暂时不在快照里，不能 intersection_update 后误判失败。
        # 只有 15 游戏秒后所有地址都重新可见却仍是农民，或 44 秒仍没出现民兵，才认定拒绝。
        # ★★ 09-21 1-262：353 秒号召 5 个，361~401 秒状态里一直是 hmil=5 —— **变身成功了**；
        #   营地还没打完，民兵到 45 秒自动变回农民，403 秒这里看到「一个民兵都没有」就判成
        #   「号召令未观察到变身」，把营地标成失败、5 个人全放回主矿（现象：一队农民变民兵帮打完
        #   直接跑回主矿，本可以留两个就地造分矿大厅）。
        #   「现在没有民兵」同时表示「从没变过」和「变过、时间到了又变回来」——
        #   看到过一次就不是失败。
        unseen = self.militia_creep_ids - set(alive)
        elapsed = w.t-self.militia_called_at
        transform_failed = (self.militia_creep_ids and not transformed and not self.militia_seen_hmil
                            and ((elapsed >= 15.0 and not unseen) or elapsed >= 44.0))
        if transform_failed:
            self.note(w.t, "民兵清分矿：号召令未观察到变身，释放农民，本营地不再重试")
            self.counts["民兵清分矿·变身无回执"] += len(self.militia_creep_ids)
            self.militia_failed_camp_key = self.militia_camp_key
            self.militia_creep_ids.clear()
            self.militia_camp_key = None
            self.militia_expand_xy = None
            self.militia_seen_hmil = False
            return

        # ★ 09-21 2-038：原来要求当前守卫名单和号召时**完全一样**才算同一个营地 —— 守卫被打死一只名单就变了，
        #   民兵在打到一半时被判「任务完成」解散（324 秒号召、336 秒「民兵到点、营地还没清完」、338 秒主力清空）。
        #   当前守卫是原来那批里剩下的（有交集）就还是同一趟任务。
        mission_done = ct is None or not (set(ct.get("ids", ())) & set(self.militia_camp_key or ()))
        timed_out = self.militia_creep_ids and w.t-self.militia_called_at >= 44.0
        if active and (mission_done or timed_out) and not busy_def:
            self.militia_creep_ids.difference_update(self.militia_creep_ids - set(alive))
            threats = w.enemy_fighters + w.creeps
            safe = [u for u in transformed if not any(math.hypot(u.x-f.x, u.y-f.y) <= 900 for f in threats)]
            # ★ 09-21：民兵 45 秒到点会**自己变回农民**。原来这一支只处理还是民兵的（transformed），
            #   自己变回来的那几个既不进预留、也一直挂在 militia_creep_ids 里（被 _idle_workers 排除 ->
            #   站着不动），整条链卡住；1-262 那次是先被上面的「未观察到变身」误判清掉的。
            reverted = [u for u in active if u.type != "hmil"]
            # 真正清掉分矿后，就地保留离矿最近的两名幸存民兵作为大厅主建造工+协建工。
            # 它们先解除成农民；建造调度在下一张快照看到 hpea 后优先使用，不再送回主矿采集。
            # ★★ 09-21 留两个就地造分矿大厅，其余回去继续采集 ——
            #   超时（民兵到点变回来 / 仗还没打完）也留：原来只在「清完 + 没超时」时才留，超时一个都不留，
            #   人全跑回主矿，等营地清完又得从主矿重新派人走一趟。
            reserve = set()
            camp_clear = (self.militia_expand_xy is not None
                          and not any(math.hypot(c.x-self.militia_expand_xy[0], c.y-self.militia_expand_xy[1]) <= 1400
                                      for c in w.creeps if c.hp > 0))
            if self.militia_expand_xy and (mission_done or timed_out):
                ex, ey = self.militia_expand_xy
                # ★ 09-22 （1-287 4:40）清完二矿的野后农民又跑回主矿 —— 原来只留 2 个，另外 3 个下「回到工作」跑回老家。
                #   现在全留下（MILITIA_STAY）：1 个主建、其余协建（_militia_assist），大厅盖好就地挖 2 矿。
                slots = max(0, MILITIA_STAY-len(self.militia_expand_builders))
                pool = safe + [u for u in reverted if not any(math.hypot(u.x-f.x, u.y-f.y) <= 900 for f in w.enemy_fighters)]
                reserve = {u.addr for u in sorted(pool, key=lambda u: math.hypot(u.x-ex, u.y-ey))[:slots]}
            for u in safe:
                if u.addr in reserve:
                    # ★ 09-21 （1-286 5 分钟）清完野后，过来帮打的民兵跑回老家，没有留在二矿位置等着建大厅。
                    #   「回到工作」（militiaoff）的原文是「让民兵跑到最近的一个城镇大厅从而让其重新变回农民」——
                    #   分矿还没大厅，最近的就是老家：23:55:37 对 5 个民兵（含预留的 2 个）一起下了这条令，
                    #   预留那个 13 秒后才被叫回来盖大厅，地基晚了约 28 游戏秒；另一个超了 15 秒回执被移出预留，在老家盖了农场。
                    #   预留的不解除：民兵到点（45 游戏秒）会**原地**变回农民，就在矿边，变回来立刻一个盖一个协建。
                    self.militia_creep_ids.discard(u.addr)
                    self.counts["民兵清分矿·预留者原地等到点变回"] += 1
                    continue
                if self.act.militia(u, False):
                    self.militia_creep_ids.discard(u.addr)
                    self.counts["民兵清分矿·解除"] += 1
            for u in reverted:                          # 已经自己变回来了，不用再下解除令
                self.militia_creep_ids.discard(u.addr)
                self.counts["民兵清分矿·到点自己变回"] += 1
            kept = {a for a in reserve if a not in self.militia_creep_ids}
            for a in kept:
                self.militia_expand_builders.add(a)
                self.gold_crew.pop(a, None)
                self.lumber_crew.pop(a, None)
            if kept:
                self.militia_expand_until = w.t + MILITIA_EXPAND_RESERVE_S
                self.militia_demobilize_at = w.t
                self.counts["民兵清分矿·预留建矿" + ("" if camp_clear else "·营地未清先退开")] += len(kept)
                if not camp_clear:
                    # 营地还没清完：两个建造工先退到分矿朝主基地一侧 CREEP_STAGE 外等着，清完就地开工，
                    # 不跑回主矿（跑回去再跑回来要一两分钟），也不站在野怪脸上。
                    main = w.main_town()
                    hx, hy = (main.x, main.y) if main else (ex, ey)
                    d = math.hypot(hx-ex, hy-ey) or 1.0
                    sx, sy = ex + (hx-ex)/d*T.CREEP_STAGE, ey + (hy-ey)/d*T.CREEP_STAGE
                    self.act.move([alive[a] for a in kept if a in alive], sx, sy)
                self.note(w.t, f"民兵清分矿：{'守军已清' if camp_clear else '民兵到点、营地还没清完'}，"
                               f"预留 {len(kept)} 名{'原地' if camp_clear else '在分矿外安全点'}等着一起盖分矿大厅"
                               + ("，其余回去采集" if self.militia_creep_ids or len(kept) < len(active) else ""))
            if not self.militia_creep_ids:
                if timed_out and not camp_clear:
                    # ⚠ 09-21 3-028：修掉「未观察到变身」的误判之后，那条误判顺带当着的刹车也没了 ——
                    #   80 秒民兵到点、84 秒又号召 5 个、132 秒再 5 个，前三分钟 5 个农民一直在当民兵。
                    #   民兵 45 秒没打下来，马上再叫一批也是一样的结果：这个营地留给主力去清，不再号召民兵。
                    #   （和「变身失败」分开记：那是没变成，这是变成了但时间不够。）
                    self.militia_failed_camp_key = self.militia_camp_key
                    self.counts["民兵清分矿·到点没清完·本营地不再号召"] += 1
                self.militia_camp_key = None
                self.militia_seen_hmil = False
                if not self.militia_expand_builders:
                    self.militia_expand_xy = None
            return

        if (self.militia_creep_ids or busy_def or self.race != "human" or ct is None
                or not self.config["enable_creep"] or not self.config["enable_militia_creep"]):
            return
        main = w.main_town()
        strength = float(ct.get("strength", 0))
        camp_key = tuple(sorted(ct.get("ids", ())))
        if camp_key == self.militia_failed_camp_key:
            return
        desired = 6 if strength >= 14 else (5 if strength >= 8 else 0)
        if main is None or desired == 0 or strength > 18 or len(w.my_workers) < 7:
            return
        travel = math.hypot(float(ct["x"])-main.x, float(ct["y"])-main.y)
        if travel > 6200 or travel/270.0 + 22.0 > 45.0:
            self.counts["民兵清分矿·时限赶不上"] += 1
            return
        # ★ 09-21 （2-038 2 分钟）两个农民被单独派到还没清野的二矿，白跑一趟；应当由主力带农民过去变民兵一起打，
        #   打完就地建矿。实机 32 秒（还没有一个兵）就号召 5 个民兵打强度 11 的守卫，45 秒时限到了没打完，
        #   两个农民在矿外干等 180 秒后回去；主力 338 秒才清完，380 秒另派一个农民从主矿走过去一个人盖。
        #   民兵只在**主力已经在这个守卫边上**、并且主力 + 民兵能在时限里打完时才叫。
        gx, gy = float(ct["x"]), float(ct["y"])
        guard = [c for c in w.creeps if c.addr in set(ct.get("ids", ())) and c.hp > 0]
        army = [u for u in w.my_army + w.my_heroes
                if u.hp > 0 and math.hypot(u.x - gx, u.y - gy) <= MILITIA_ARMY_NEAR]
        if not army or not guard:
            self.counts["民兵清分矿·主力不在守卫边上"] += 1
            return
        hmil = SimpleNamespace(type="hmil", hp=float(G.stat("hmil", "hp", 220) or 220),
                               hp_max=float(G.stat("hmil", "hp", 220) or 220), addr=0, x=gx, y=gy)
        _hp_us, dps_us = T.power(army + [hmil] * desired, w.levels)
        hp_them, _dps_them = T.power(guard, w.levels)
        kill_s = hp_them / max(1e-6, dps_us)
        if travel / 270.0 + kill_s > MILITIA_WINDOW_S:
            self.counts["民兵清分矿·合起来时限内打不完"] += 1
            return
        busy = {p.get("worker") for p in self.pending} | self.powerbuild.busy_addrs | self.repairs.busy_addrs | self.harass.busy_addrs
        candidates = [u for u in w.my_workers if u.addr not in busy and math.hypot(u.x-main.x, u.y-main.y) <= 1100]
        candidates.sort(key=lambda u: (u.addr not in self.lumber_crew, u.addr in self.gold_crew,
                                       math.hypot(u.x-main.x, u.y-main.y), u.addr))
        if len(candidates) < desired:
            return
        called = []
        for u in candidates[:desired]:
            if self.act.militia(u, True):
                called.append(u)
                self.militia_creep_ids.add(u.addr)
                self.lumber_crew.pop(u.addr, None)
                self.gold_crew.pop(u.addr, None)
                self.sent_at[u.addr] = w.t
        if called:
            self.militia_called_at = w.t
            self.militia_seen_hmil = False          # 新的一次号召，重新看有没有变身
            self.militia_camp_key = camp_key
            self.militia_expand_xy = (float(ct["x"]), float(ct["y"]))
            self.counts["民兵清分矿·号召"] += len(called)
            self.note(w.t, f"民兵清分矿：营地强度 {strength:.0f}，号召 {len(called)}/{desired} 名（优先伐木农民），清完就地解除")

    def job_tactics(self, w) -> None:
        tm = self.timer                     # run_due 已经开了「任务·tactics」这一层，下面按步 lap（steptime）
        tm.lap("tactics·准备")
        if not self.config["enable_harass"]:
            for gid in list(self.harass.active):
                self.harass.end(gid, "骚扰开关关闭")
        main = w.main_town()
        home = main.hall if main else None
        # 骚扰小队和第二支打野小队的人不进主队的视野（它们各自有自己的状态机；本拍新组的队在下面另行摘掉）
        squad = (self.harass.busy_addrs | self.splitcreep.busy_addrs
                 | (set(self.lumber_crew) if self.spec["lumber_unit"] != self.spec["worker"] else set()))
        # ★ 09-18 毫秒层正在救的人（hurt.json）大脑不派：原来新大脑不读它，租约一到期每秒重下的打野/出击令又把人派回去送
        #   （124037 局 12:47~12:49 同一个营地「在场 1 人 + 赶来 0 人」被拉十几次）。和老 AI 的 W3-IsHurt 同一个文件
        hurt = self._read_hurt()
        self.attack.note_foe(w.t, w.foe_strength)          # 反推窗口（tactics.counter_window）：守家时也要记
        mission_militia = {u.addr for u in w.my_militia if u.addr in self.militia_creep_ids}
        self.attack.held = hurt & ({u.addr for u in w.my_army} | {u.addr for u in w.my_heroes} | mission_militia)
        self.attack.held_heroes = sum(1 for h in w.my_heroes if h.addr in hurt)   # 英雄被拉着 ≠ 没英雄（tactics._creep）
        self.attack.held_units = [u for u in w.my_army + w.my_heroes + w.my_militia if u.addr in self.attack.held]
        for addr in hurt:
            self.act.last_point.pop(addr, None)  # 微操已换过命令，归队不能继续沿用大脑的旧攻击去重缓存。
        self.counts["毫秒层在救·不派"] += len(self.attack.held)
        # ★ 09-21 养伤名单（毫秒层 unitmind.Minds，resting.json）：被拉出来的兵血没回到五成之前，出击/打野/守家都不派。
        #   原来只有 hurt.json（8 秒租约）：18:00 之后 262 个战死是「拉出来又被派回去」，死时血中位 10%、残血窗口中位 61 秒。
        #   整队恢复照样能带它们（下面 rest_view）；恢复没在跑时先回家（见下令前那段）。
        resting = self._read_resting() - hurt
        # ★★ 09-22 晚 后期敌军基本没兵、只剩拆家时，不再等治疗，直接上去快速拆完。
        #   收尾（_finishing）：养伤的也派（不回家等五成血）、整队恢复不开（下面 block_why）、英雄不等回血（tactics finishing）。
        #   毫秒层按「身边有敌人 / 在塔射程里」拉残血那条照旧 —— 拆家时真挨打的还是会被拉。
        self.attack.finishing = self._finishing(w)
        if self.attack.finishing and resting:
            self.counts["收尾拆家·养伤的也上"] += sum(1 for u in w.my_army if u.addr in resting)
            resting = set()
        self.counts["养伤·不派"] += sum(1 for u in w.my_army if u.addr in resting)
        squad = squad | hurt | resting
        sig = self._read_retreat_signal()
        if sig is not None and sig.get("x") is not None:
            # 回防、支援交战都认（fightcall.recently_lost：这片战场 LOST_HOLD_S 内判「放弃」）
            self.defense.lost = self.attack.lost_at = (w.t, float(sig["x"]), float(sig["y"]))
        self.attack.retreat_req = sig or self.attack.retreat_req
        view = replace(w, my_army=[u for u in w.my_army if u.addr not in squad],
                       my_heroes=[u for u in w.my_heroes if u.addr not in squad],
                       my_militia=[u for u in w.my_militia if u.addr not in squad])
        unavailable = hurt | resting | (set(self.lumber_crew) if self.spec["lumber_unit"] != self.spec["worker"] else set())
        defensive_view = replace(w, my_army=[u for u in w.my_army if u.addr not in unavailable],
                                 my_heroes=[u for u in w.my_heroes if u.addr not in unavailable],
                                 my_militia=[u for u in w.my_militia if u.addr not in unavailable])
        tm.lap("tactics·守家")
        if self.config["enable_defense"]:
            mode, intents = self.defense.step(defensive_view, home)
        else:
            mode, intents = "off", []
        self.defense_mode = mode
        busy_def = mode in ("defend", "toostrong", "last_stand")
        tm.lap("tactics·塔攻")
        # ★★ 09-22 晚 新增防塔攻（TR）策略（1-415 AMAI 兽族 10 座箭塔从门外推到大厅边，我们一座没拆）。
        #   对面在我方基地边上下塔（brain/towerrush.py）：守家没在打的时候，全军 + 民兵先杀苦工、再拆地基；
        #   打野 / 出击 / 骚扰都让路（busy_def），开矿闸按 home_militia_seen 一起挡住。
        tr_crew = defensive_view.my_army + defensive_view.my_heroes + defensive_view.my_militia
        tr_intents = self.tower_rush.step(w, tr_crew) if self.config["enable_defense"] else None
        for k, v in self.tower_rush.counts.items():
            self.counts[k] += v
        self.tower_rush.counts.clear()
        for n in self.tower_rush.notes:
            self.note(w.t, n)
        self.tower_rush.notes.clear()
        if tr_intents is not None:
            self.home_militia_seen = w.t                       # 民兵不解除、开矿先停（_expand_hold）
            if not busy_def:
                intents = tr_intents
                busy_def = True
            if self.tower_rush.wants_militia(w):
                self._call_militia(w, self.tower_rush.hall, "塔攻：对面在基地边上下塔、附近我方兵不够")
        self._militia_creep_step(w, busy_def)
        self._home_militia_step(w)
        self.attack.militia_ids = set(self.militia_creep_ids)
        self.attack.escort = self._escort_point(w)
        self.attack.build_spot = next(((p["x"], p["y"]) for p in self.pending if p["kind"] == "expand" and "x" in p), None)
        self.attack.critters = self._expansion_critters(w)
        self.attack.ground = self._ground_items()
        tm.lap("tactics·读队长")
        caps = self.percept.read_captains(w.enemy_armies, w.t)
        inc = incoming(w, caps)
        self.attack.incoming = inc              # 09-22 晚：空档时提前到来袭那座城的前沿（AttackSequence._idle_plan）
        if inc and not busy_def and self.attack.state != "attack":
            army = defensive_view.my_army + defensive_view.my_heroes
            due = [i for i in inc if offense.return_due(i, army)]
            if due:
                town = min(due, key=lambda i: i["dist"])["town"]
                self.counts["电脑队长来袭·提前回家"] += 1
                intents = [Intent("move", army, town.x, town.y, why="电脑队长的目标点是这座城，提前回家")]
                busy_def = True
            else:
                self.counts["电脑队长意图·尚有行动窗口"] += 1
        tm.lap("tactics·整队恢复")
        # ★ 09-21：原来「选了打野营地（camp_ids 非空）」或「有人被毫秒层拉着（held 非空）」就整体挡掉补给。
        #   毫秒层每局接管 144.6 次、主力几乎一直挂着营地 —— 这个闸几乎恒真，
        #   整队补给最近 60 局一条动作都没跑过。改成只挡「真的在打」：
        #   正规进攻/集结、守家、局部交战、打野**已经接战**（creep_engaged）。
        #   清完一个营地走向下一个的路上正是补给的好时机，held 里那几个人也不该阻塞全队。
        # ⚠ 09-21：这四个条件原来 or 成一个 blocked 传下去，而下面只记一个
        #   「整队恢复·没开始·上层挡着」——28 局实测 **259.4 次/局**，是所有闸门里最高的，
        #   但它回答不了「是谁挡的」（一个数同时表示四件事）。拆开各记各的。
        block_why = ("守家" if busy_def else
                     "收尾拆家" if self.attack.finishing else
                     "出击/集结" if self.attack.state in ("attack", "gather") else
                     "打野已接战" if self.attack.creep_engaged else
                     "局部交战" if self.attack.skirmish.phase != 'idle' else None)
        # 整队恢复要看得见养伤的人（它们正是最缺血的）；出击/打野/守家那几份 view 里没有它们
        rest_view = replace(view, my_army=view.my_army + [u for u in w.my_army if u.addr in resting]) if resting else view
        # 正在被强制攻击腾人口的那个兵不算伤兵（09-22 实战发现：一边反补自家小兵，牧师还一边给它加血 —— 卷轴同理）
        if self.attack.cull_ids:
            rest_view = replace(rest_view, my_army=[u for u in rest_view.my_army if u.addr not in self.attack.cull_ids])
        # 部队闲着（出击状态 home）时英雄可以跑远一点去藏宝室买卷轴（recovery.SHOP_MAX_IDLE，1-291）
        recovery_intents = self.recovery.step(rest_view, self.cmd, self.claims, blocked=block_why,
                                              shop_max=SHOP_MAX_IDLE if self.attack.state == "home" else SHOP_MAX)
        recovering = recovery_intents is not None
        home_intents, home_crew = self._home_recovery_step(w, rest_view, main, block_why, busy_def)
        if home_crew:
            view = replace(view, my_army=[u for u in view.my_army if u.addr not in home_crew],
                           my_heroes=[u for u in view.my_heroes if u.addr not in home_crew])
        tm.lap("tactics·分队打野")
        # ★★ 09-23 单位多、有两个英雄时分两路练级，各带一支小分队打野；
        #   评估打得过就给英雄配合适的单位去打（brain/creepsplit.py）。
        #   打得过的判据和主队同一条线；主队要用人（守家 / 出击 / 整队恢复 / 收尾）就收队。
        split_intents = []
        if self.config["enable_creep"]:
            recall_split = (busy_def or recovering or self.attack.finishing
                            or self.attack.state in ("gather", "attack", "retreat"))
            # 世界给 w（第二队自己的人已经从 view 里摘掉了，_run 要按地址找得到它们）；可挑的人从 view 里来
            split_intents = self.splitcreep.step(
                w, home,
                [u for u in view.my_army if u.addr not in unavailable],
                [u for u in view.my_heroes if u.addr not in unavailable],
                main_camp_ids=self.attack.camp_ids, recall=recall_split)
            for k, v in self.splitcreep.counts.items():
                self.counts[k] += v
            self.splitcreep.counts.clear()
            for n in self.splitcreep.notes:
                self.note(w.t, n)
            self.splitcreep.notes.clear()
            second = self.splitcreep.busy_addrs
            if second:
                view = replace(view, my_army=[u for u in view.my_army if u.addr not in second],
                               my_heroes=[u for u in view.my_heroes if u.addr not in second])
        tm.lap("tactics·出击")
        if recovering:
            intents += recovery_intents
        elif not busy_def:
            self.attack.allow_attack = self.config["enable_attack"]
            self.attack.allow_creep = self.config["enable_creep"]
            intents += self.attack.step(view, home)
        else:
            self.attack.interrupt()
        if split_intents:
            # 第二队的人不受别的层指挥（守家那一份意图是在拆队之前算的）
            second = self.splitcreep.busy_addrs | {u.addr for it in split_intents for u in it.units}
            intents = [it2 for it2 in (replace(it, units=[u for u in it.units if u.addr not in second])
                                       for it in intents) if it2.units]
            intents += split_intents
        tm.lap("tactics·骚扰")
        self._publish_sacrifice()
        if self.config["enable_harass"]:
            dual_target = (self.attack.target if self.spec.get("name") == "human"
                           and self.attack.state in ("gather", "attack")
                           and self.attack.skirmish.phase == "idle" and not busy_def and not recovering else None)
            recall_harass = (recovering or self.attack.state == "retreat"
                             or self.attack.skirmish.phase == "withdraw")
            # 打野只有真接战了才算「主力在打仗」（09-22：原来整个 creep 状态都算，1-304~308 每局骚扰只派出过 1 次，见 harass.HARASS_AHEAD 上面）
            harass_busy = (busy_def or recovering or self.attack.skirmish.phase != 'idle'
                           or self.attack.state in ("attack", "gather", "retreat")
                           or (self.attack.state == "creep" and self.attack.creep_engaged))
            harass_intents = self.harass.step(w, home, busy=harass_busy,
                                              town_threatened=busy_def, excluded=unavailable | self.attack.sacrifice | self.attack.cull_ids | self.splitcreep.busy_addrs | ({u.addr for u in view.my_army+view.my_heroes} if recovering else set()),
                                              dual_target=dual_target, recall=recall_harass,
                                              idle=self.attack.idle_now)
            if not busy_def:
                # 本拍新派出的骚扰队也不能再收到主力命令；伤员离队的回家令同样独占。
                reserved = self.harass.busy_addrs | {u.addr for it in harass_intents for u in it.units}
                intents = [replace(it, units=[u for u in it.units if u.addr not in reserved]) for it in intents]
                intents = [it for it in intents if it.units]
            intents += harass_intents
        if home_intents:
            intents = [replace(it, units=[u for u in it.units if u.addr not in home_crew]) for it in intents]
            intents = [it for it in intents if it.units] + home_intents
        tm.lap("tactics·英雄让路")
        # ★ 09-21 大法师被一个农民卡在家里出不去；英雄是这个游戏里优先级最高的单位，必须优先让路。
        #   必须放在下令之前：让路意图要和本拍其它意图一起发，而 forget 要在重下之前清掉去重缓存（见 unstick.py 文件头）。
        protected = (self.repairs.busy_addrs | self.powerbuild.busy_addrs | set(self.militia_expand_builders)
                     | {p["worker"] for p in self.pending if p.get("worker")})
        clear_intents, forget = self.stuck.step(w, intents, protected=protected, t=w.t)
        for addr in forget:
            self.act.last_point.pop(addr, None)   # 卡住的英雄不再享受「同令不重下」，让引擎重新寻路
        if clear_intents:
            reserved = {u.addr for it in clear_intents for u in it.units}
            intents = [replace(it, units=[u for u in it.units if u.addr not in reserved]) for it in intents]
            intents = [it for it in intents if it.units] + clear_intents
        # 养伤的人这一拍没被整队恢复带走、又不在家：先回家（离主基地 REST_HOME_R 以外的）。
        #   不能留在原地：毫秒层把它拉开 800 就放手了，主力一走它就一个人站在野外。
        # ★ 09-22 残血单位不在家里建筑中间集结，改到周围空地集结 —— 原来回的是城堡坐标，
        #   一群伤兵堆在建筑中间，英雄反复「卡太久，错身重新寻路」（1-284 11:49 那一串「给英雄让路」全是养伤的兵）。
        #   现在去家附近的一块空地（_rest_spot）；找不到空地才退回城堡、沿用 REST_HOME_R。
        if home is not None and resting:
            taken = {u.addr for it in intents for u in it.units}
            # ★★ 09-22 晚 后期出门时让背包有空格的单位带上恢复卷轴，残血单位退到附近安全区域就地治疗，不必回家。
            #   外面有带着卷轴、周围安全的英雄（_field_heal_spot）：它 FIELD_HEAL_R 内养伤的兵退到它身后，凑齐了毫秒层就地用卷轴
            #   （mod_combat 物品段 itemuse.regen_formation_ready：3 个伤兵到齐、总缺血够本、900 内没敌人）；远的照旧回家。
            field_spot = self._field_heal_spot(w, home)
            if field_spot is not None:
                fx, fy, holder = field_spot
                near_holder = [u for u in w.my_army if u.addr in resting and u.addr not in taken
                               and math.hypot(u.x - holder.x, u.y - holder.y) <= FIELD_HEAL_R
                               and math.hypot(u.x - fx, u.y - fy) > FIELD_HEAL_KEEP_R]
                if near_holder:
                    self.counts["养伤·去英雄身边用卷轴"] += len(near_holder)
                    intents.append(Intent("move", near_holder, fx, fy,
                                          why=f"养伤：{holder.type} 带着恢复卷轴，退到它身后安全处就地治疗（不回家）"))
                    taken |= {u.addr for u in near_holder}
                taken |= {u.addr for u in w.my_army if u.addr in resting
                          and math.hypot(u.x - fx, u.y - fy) <= FIELD_HEAL_KEEP_R}
            rx, ry, open_spot = self._rest_spot(w, home)
            keep_r = REST_SPOT_R if open_spot else REST_HOME_R
            far = [u for u in w.my_army if u.addr in resting and u.addr not in taken
                   and math.hypot(u.x - rx, u.y - ry) > keep_r]
            if far:
                self.counts["养伤·回家" + ("附近空地" if open_spot else "")] += len(far)
                intents.append(Intent("move", far, rx, ry,
                                      why="养伤：血没回到五成，先回家" + ("附近空地" if open_spot else "") + "（毫秒层 unitmind）"))
        # 最后一道兜底：这一拍谁都没管、又离大部队很远的兵，收回来（09-21 两局里残血兵在野外无人指挥、原地不动）。
        # 放在让路之后 —— 让路已经给英雄和挡路的人派了活，它们就不算"没人管"了。
        intents += self.stuck.strays(w, intents, squad | hurt, w.t)
        intents = self._home_rally(w, home, intents)
        intents = T.hero_tower_guard(w, intents, self.counts)     # 英雄不进塔/地洞的射程（tactics.ARMED_BUILDINGS 下面那段）
        intents = [self._on_ground(it) for it in intents]
        tm.lap("tactics·下令")
        self.last_intents = [{"kind": it.kind, "n": len(it.units), "x": round(it.x), "y": round(it.y), "why": it.why,
                              "target": getattr(it.target, "type", None)} for it in intents]
        for it in intents:
            self._do(it, w.t)
        tm.lap("tactics·看戏埋点")
        self._hero_idle_probe(w, intents)
        # ⚠ 09-21：coach.notes 原来没接进来 —— 复盘 1-262 时顾问那局到底说了什么，对局文件里一条都查不到
        for src in (self.attack.notes, self.attack.heroes.notes, self.harass.notes,
                    self.recovery.notes, self.home_recovery.notes, self.stuck.notes, self.coach.notes):
            for n in src:
                self.note(w.t, n)
            src.clear()

    def _read_hurt(self) -> set:
        """微操的残血及孤军撤离保护名单（hurt/solo），只读同一游戏 pid 的有效租约。"""
        try:
            import sys
            mod = A.MODULES
            if mod not in sys.path:
                sys.path.append(mod)
            import w3lease
            pid = int(getattr(self.cmd, "pid", 0) or 0)
            return set(w3lease.read(os.path.join(self.inst_dir, "hurt.json"), pid)) | set(w3lease.read(os.path.join(self.inst_dir, "solo.json"), pid))
        except Exception:  # noqa: BLE001
            return set()

    def _home_recovery_step(self, w, rest_view, main, block_why, busy_def):
        """主力在外面打野/出击时，家里的伤兵单独补给：家里的单位去藏宝室买恢复卷轴，把家里的伤兵集合起来当场用。
        ★ 09-21 反复出现的老问题（第四次复现）：一批残血单位在家里等恢复，没有任何英雄去给它们补给。
          近 24 局整队恢复只放出卷轴 8 次；头号拦路是「上层挡着」—— 主力在打野（4439 次）/ 出击（1884）/ 守家（982）时
          整个恢复流程不启动，不管伤兵是不是早就回家了。买家还要求血 > 40~45%，家里那群正好都低于这条线。
        只在整队恢复没在跑、主力被别的事占着、家里没在打仗时跑；用到的人从主力的命令里剔掉。-> (意图, 家里这群人的地址)"""
        hr = self.home_recovery
        if main is None or busy_def or not block_why or self.recovery.phase != "idle":
            if hr.phase != "idle":
                hr.cancel(w.t, "主力回来了 / 家里在打仗，交回整队恢复")
            return [], set()
        # 买家去藏宝室的路上会走出这一圈（1-284 藏宝室在分矿，离主基地 5600）—— 它还得算「家里这一群」，否则流程找不到它就作废
        camp = self.attack.camp if self.attack.state == "creep" and self.attack.camp else None
        hostiles = [f for f in w.enemy_fighters + w.creeps if f.hp > 0]

        def on_mission(u):
            """在打的营地边上、或者身边有野怪 / 敌人：那是在干活，不是在家闲着（见 HOME_RECOVERY_MISSION_R 上面那段）。"""
            if camp is not None and math.hypot(u.x - camp["x"], u.y - camp["y"]) <= HOME_RECOVERY_MISSION_R:
                return True
            return any(math.hypot(u.x - f.x, u.y - f.y) <= RECOVERY_SAFE_R for f in hostiles)

        def near(u):
            if hr.holder_id is not None and u.addr == hr.holder_id[0]:
                return True
            return math.hypot(u.x - main.x, u.y - main.y) <= HOME_RECOVERY_R and not on_mission(u)
        hv = replace(rest_view, my_army=[u for u in rest_view.my_army if near(u)],
                     my_heroes=[u for u in rest_view.my_heroes if near(u)])
        out = hr.step(hv, self.cmd, self.claims, blocked=None, carrier_min=HOME_CARRIER_MIN)
        if out is None:
            return [], set()
        self.counts["家里补给·在跑"] += 1
        return out, {u.addr for u in hv.my_army + hv.my_heroes}

    def _rest_spot(self, w, home):
        """养伤的兵在家附近的哪块空地等（terrain.open_ground，从主基地出发）-> (x, y, 是不是空地)。
        REST_SPOT_EVERY 游戏秒重算一次（建筑会越盖越多）；附近来了敌人 / 野怪也重算。地形表读不到或没有合格点 -> 城堡坐标。"""
        hostiles = w.enemy_fighters + w.creeps
        c = self._rest_cache
        if (c is not None and w.t - c[0] < REST_SPOT_EVERY and abs(c[4] - home.x) < 1 and abs(c[5] - home.y) < 1
                and not any(math.hypot(f.x - c[1], f.y - c[2]) < 1200 for f in hostiles)):
            return c[1], c[2], c[3]
        tr = self.attack.terrain
        p = tr.open_ground(home.x, home.y, buildings=list(w.my_buildings) + list(w.enemy_buildings),
                           mines=w.mines, avoid=hostiles) if tr is not None else None
        spot = (p[0], p[1], True) if p else (home.x, home.y, False)
        self._rest_cache = (w.t, spot[0], spot[1], spot[2], home.x, home.y)
        return spot

    def _army_rally(self, w, home):
        """整队回家 / 集结去的门前空地（terrain.open_ground front=True，朝地图中心那一侧、RALLY_RINGS 内）-> (x, y, 是不是空地)。
        和 _rest_spot（养伤，基地后面）一样 REST_SPOT_EVERY 游戏秒重算、附近来了敌人 / 野怪也重算；没有地形表或没有合格点 -> 城堡坐标。"""
        hostiles = w.enemy_fighters + w.creeps
        c = self._rally_cache
        if (c is not None and w.t - c[0] < REST_SPOT_EVERY and abs(c[4] - home.x) < 1 and abs(c[5] - home.y) < 1
                and not any(math.hypot(f.x - c[1], f.y - c[2]) < 1200 for f in hostiles)):
            return c[1], c[2], c[3]
        tr = self.attack.terrain
        p = tr.open_ground(home.x, home.y, buildings=list(w.my_buildings) + list(w.enemy_buildings),
                           mines=w.mines, avoid=hostiles, rings=TR.RALLY_RINGS, front=True) if tr is not None else None
        spot = (p[0], p[1], True) if p else (home.x, home.y, False)
        self._rally_cache = (w.t, spot[0], spot[1], spot[2], home.x, home.y)
        return spot

    def _home_rally(self, w, home, intents):
        """★ 09-22 部队不再回基地建筑堆里集结，统一改到基地外的空地集结。
        1 号实例 8 局：「集结」48 条令全部落在城堡坐标上（离最近的建筑 0），整队回家 / 收队 / 打不过撤 / 追到对方地盘收回 / 骚扰收队
        同样 —— 出击层、守家、骚扰二十来处都写的 home.x, home.y。这里在下令前统一改：
        对点的移动 / 攻击移动、目的地离城堡 RALLY_HALL_R 以内、里面没有农民民兵 -> 全员残血（< 五成）的去养伤空地（_rest_spot，基地后面），
        其余去门前空地（_army_rally）。出击层「到家」按主力重心离城堡 1500 判，门前空地最远 1300，不影响状态切换。"""
        if home is None or not intents:
            return intents
        out = []
        for it in intents:
            if (it.kind in ("move", "attack_move") and it.target is None
                    and math.hypot(it.x - home.x, it.y - home.y) <= RALLY_HALL_R
                    and it.units and not any(G.is_worker(u.type) or u.type == "hmil" for u in it.units)):
                wounded = all(u.hp_max > 0 and u.hp < 0.5 * u.hp_max for u in it.units)
                x, y, ok = (self._rest_spot if wounded else self._army_rally)(w, home)
                if ok:
                    self.counts["回城堡改去" + ("养伤空地" if wounded else "门前空地")] += 1
                    it = replace(it, x=x, y=y)
                else:
                    self.counts["回城堡·没找到空地"] += 1
            out.append(it)
        return out

    def _read_resting(self) -> set:
        """毫秒层的养伤名单 resting.json（war3-modules/unitmind.py）：血没回到 RETURN_FRAC 之前别派去打。同一游戏 pid 的有效租约。"""
        try:
            import sys
            mod = A.MODULES
            if mod not in sys.path:
                sys.path.append(mod)
            import w3lease
            pid = int(getattr(self.cmd, "pid", 0) or 0)
            return set(w3lease.read(os.path.join(self.inst_dir, "resting.json"), pid))
        except Exception:  # noqa: BLE001
            return set()

    def _field_heal_spot(self, w, home):
        """野外就地治疗的集合点：离家 FIELD_HEAL_MIN_HOME 以外、血 >= FIELD_HEAL_HERO_HP、身上有恢复卷轴（背包按 recovery 的缓存读）、
        FIELD_HEAL_SAFE_R 内没有敌方作战单位 / 野怪的英雄 -> (x, y, 英雄)：英雄往家方向退 FIELD_HEAL_BACK 的地面点。没有返回 None。"""
        hostiles = w.enemy_fighters + w.creeps
        for h in w.my_heroes:
            if h.hp <= 0 or h.hp < FIELD_HEAL_HERO_HP * max(1, h.hp_max):
                continue
            if math.hypot(h.x - home.x, h.y - home.y) < FIELD_HEAL_MIN_HOME:
                continue                                   # 离家近就回家附近空地，一样
            if any(math.hypot(f.x - h.x, f.y - h.y) <= FIELD_HEAL_SAFE_R for f in hostiles if f.hp > 0):
                continue
            bag = self.recovery.bag(self.cmd, h, w.t)
            if not bag or "sreg" not in bag:
                continue
            d = math.hypot(home.x - h.x, home.y - h.y) or 1.0
            return h.x + (home.x - h.x) / d * FIELD_HEAL_BACK, h.y + (home.y - h.y) / d * FIELD_HEAL_BACK, h
        return None

    def _finishing(self, w) -> bool:
        """收尾拆家：我方强度 >= FINISH_OWN_MIN、对面作战单位强度 <= 我方 × FINISH_FOE_FRAC、还看得见对面建筑。
        快照不按迷雾过滤（全图单位都在），foe_strength 就是对面真实兵力。1-403 / 1-407 拆家那几分钟对面 0~7 对我方 45~54。"""
        return (w.own_strength >= FINISH_OWN_MIN and w.foe_strength <= FINISH_FOE_FRAC * w.own_strength
                and bool(w.enemy_buildings))

    def _item_reserve(self) -> int:
        """★★ 09-22 英雄残血时要留钱买血瓶补血。毫秒层 mod_scavenge 看到残血英雄缺血瓶就写 item_reserve.json
        （war3-modules/itemuse.py HURT_POTION_FRAC 上面那段）：下单前从可用金子里留出这笔，别在英雄走到藏宝室之前全花在兵上。
        同一局（pid）、RESERVE_TTL（10 秒）内写的才算 —— 模块停了 / 英雄买上了，留钱自己过期。"""
        try:
            with open(os.path.join(self.inst_dir, "item_reserve.json"), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return 0
        pid = int(getattr(self.cmd, "pid", 0) or 0)
        if int(d.get("pid", -1)) != pid or time.time() - float(d.get("wall", 0)) > 10.0:
            return 0
        return max(0, int(d.get("gold", 0) or 0))

    def _read_retreat_signal(self) -> dict | None:
        """mod_combat / fightodds 的叫撤信号 fight_retreat.json：同一局（pid）、序号比上次新、20 秒内写的才算。"""
        try:
            with open(os.path.join(self.inst_dir, "fight_retreat.json"), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return None
        pid = int(getattr(self.cmd, "pid", 0) or 0)
        seq = int(d.get("seq", -1))
        key = (int(d.get("pid", -1)), seq)
        if getattr(self, "_retreat_key", None) == key:
            return None
        self._retreat_key = key
        if key[0] != pid or time.time() - float(d.get("wall", 0)) > 20.0:
            return None
        return d

    def _publish_sacrifice(self) -> None:
        """喂野怪名单写成 sacrifice.json 给毫秒层（mod_combat.load_sacrifice：这些兵不拉、不轮换）。变了就写，否则 10 秒刷新一次时刻
        （毫秒层 60 秒没更新就当大脑不在了）。"""
        now = time.time()
        cur = sorted(self.attack.sacrifice | self.attack.cull_ids)     # 强制攻击腾人口的那个也算：毫秒层看它掉血别去救
        if cur == getattr(self, "_sac_pub", None) and now - getattr(self, "_sac_pub_t", 0.0) < 10.0:
            return
        try:
            p = os.path.join(self.inst_dir, "sacrifice.json")
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({"units": cur, "wall": now}, fh)
            os.replace(p + ".tmp", p)
            self._sac_pub, self._sac_pub_t = cur, now
        except OSError:
            pass

    def _hero_idle_probe(self, w, intents) -> None:
        """埋点，只记不改（09-18 对局里常见两个英雄在交战边上空站、不参战；011447 战斗帧里剑圣交战时没订单没任务 9%，查不实原因）：
        英雄血 >= 50%、1000 内自己的兵 >= 2 个在交战（兵 500 内有敌人或野怪）、引擎说英雄没订单也没任务
        -> 计数「英雄看戏·<谁锁着它>·大脑没给令」；每个英雄 10 游戏秒最多记一条原话（带血量、离交战的兵多远、最近的敌人多远）。
        这里的单位状态来自下令前的快照：如果本拍意图已经包含英雄，它在快照里仍显示空闲是正常的，不能再记成“看戏”。"""
        foes = w.enemy_fighters + w.creeps
        if not foes or not w.my_heroes:
            return
        fighting = [a for a in w.my_army if any(math.hypot(a.x - f.x, a.y - f.y) <= 500 for f in foes)]
        if len(fighting) < 2:
            return
        try:
            import sys
            if A.MODULES not in sys.path:
                sys.path.append(A.MODULES)
            import w3claim
            names = {v: k for k, v in w3claim.MODULES.items()}
        except Exception:  # noqa: BLE001
            names = {}
        for h in w.my_heroes:
            if h.hp_max <= 0 or h.hp < 0.5 * h.hp_max or not getattr(h, "order_known", True):
                continue
            if (h.order or 0) != 0 or getattr(h, "has_task", None) is not False:
                continue
            near = [a for a in fighting if math.hypot(a.x - h.x, a.y - h.y) <= 1000]
            if len(near) < 2:
                continue
            assigned = next((it.why for it in intents if any(u.addr == h.addr for u in it.units)), None)
            if assigned is not None:
                continue
            holder = "没人锁"
            try:
                lease = self.claims.lease_of(h.addr) if self.claims is not None else None
                if lease and lease[0] != getattr(self.claims, "me", None):
                    holder = f"{names.get(lease[0], lease[0])}锁着"
            except Exception:  # noqa: BLE001
                holder = "租约读不到"
            why = "大脑没给令"
            self.counts[f"英雄看戏·{holder}·{why}"] += 1
            if w.t - self.idle_note_at.get(h.addr, -1e9) >= 10.0:
                self.idle_note_at[h.addr] = w.t
                d_ally = min(math.hypot(a.x - h.x, a.y - h.y) for a in near)
                d_foe = min(math.hypot(f.x - h.x, f.y - h.y) for f in foes)
                self.note(w.t, f"英雄看戏：{h.type} {int(h.hp)}/{int(h.hp_max)} 没订单没任务，1000 内 {len(near)} 个自己人在打"
                               f"（最近的 {int(d_ally)}、最近的敌人 {int(d_foe)}）｜{holder}｜{why}")

    def _ground_items(self) -> list:
        """mod_scavenge 发布的 ground_items.json（10 秒内写的、同一个游戏进程）-> [(x, y), ...]。"""
        path = os.path.join(self.inst_dir, "ground_items.json")
        try:
            m = os.path.getmtime(path)
        except OSError:
            return []
        if m != self._ground_mtime:
            self._ground_mtime = m
            data = _load_json(path, {}) or {}
            ok = int(data.get("gamePid") or 0) == int(getattr(self.cmd, "pid", 0) or 0) or not getattr(self.cmd, "pid", 0)
            self._ground = [(it[2], it[3]) for it in data.get("items", [])] if ok else []
        return self._ground if time.time() - m < 10.0 else []

    def _escort_point(self, w):
        """正在开的分矿：下了令还没出地基的，或者离主基地 1500 外、血没满过的大厅/闹鬼金矿（工地）。"""
        for p in self.pending:
            if p["kind"] == "expand" and "x" in p:
                return (p["x"], p["y"])
        main = w.main_town()
        if main is None:
            return None
        kinds = set(self.spec["halls"]) | {"ugol"}
        for b in w.my_buildings:
            if b.type in kinds and b.hp_max > 0 and b.hp < 0.97 * b.hp_max and b.addr not in self.seen_full \
                    and math.hypot(b.x - main.hall.x, b.y - main.hall.y) > 1500:
                return (b.x, b.y)
        return None

    def _building_losses(self, prev, w, t: float) -> None:
        """上一拍还在、这一拍没了的我方建筑：记下血量、是不是工地、900 内有谁（09-18：分矿地基十几秒就没了，一直不知道谁打的）。"""
        if prev is None or len(w.units) < 0.7 * len(prev.units):
            return                                   # 快照缩水（游戏在结束/切换）不算
        now = {u.addr for u in w.my_buildings}
        for b in prev.my_buildings:
            if b.addr in now:
                continue
            near = Counter(e.type for e in prev.enemies + prev.creeps if math.hypot(e.x - b.x, e.y - b.y) <= 900)
            site = b.hp_max > 0 and b.hp < 0.97 * b.hp_max and b.addr not in self.seen_full
            self.counts[f"建筑没了·{b.type}{'·工地' if site else ''}"] += 1
            who = "、".join(f"{k}×{v}" for k, v in near.most_common(6)) or "没有敌人/野怪"
            self.note(t, f"建筑没了：{b.type} @ ({int(b.x)},{int(b.y)}) 血 {int(b.hp)}/{int(b.hp_max)}{'（工地）' if site else ''}；900 内 {who}")

    def _unit_losses(self, prev, w, t: float) -> None:
        """上一拍还在、这一拍没了的我方兵和英雄（召唤物到期不算）：按死时附近是谁归类计数 —— 打野 / 打玩家 / 别处。
        ★ 09-18 复盘：前 7.5 游戏分钟训练约 13 个兵、死约 5 个，战斗账本（mod_fight battles.jsonl）只记打玩家的仗，
          约 3 个死在打野里却从没进账 —— 「打野为什么死」答不上来。每拍的数写进对局记录 state.dead（run_brain），整局在 counts「阵亡·…」。"""
        if prev is None or len(w.units) < 0.7 * len(prev.units):
            return                                   # 快照缩水（游戏在结束/切换）不算
        now = {u.addr for u in w.my_army} | {u.addr for u in w.my_heroes}
        for u in prev.my_army + prev.my_heroes:
            if u.addr in now or T.is_summon(u.type):
                continue
            creep = any(math.hypot(c.x - u.x, c.y - u.y) <= DEATH_NEAR for c in prev.creeps if c.hp > 0)
            foe = any(math.hypot(e.x - u.x, e.y - u.y) <= DEATH_FOE_NEAR for e in prev.enemy_fighters + prev.enemy_towers)
            kind = "打玩家" if foe else ("打野" if creep else "别处")
            self.counts[f"阵亡·{kind}"] += 1
            self.counts[f"阵亡·{kind}·{u.type}"] += 1
            self.tick_deaths[kind] += 1
            if kind == "打野":
                near = Counter(c.type for c in prev.creeps if c.hp > 0 and math.hypot(c.x - u.x, c.y - u.y) <= DEATH_NEAR)
                mine = sum(1 for a in prev.my_army + prev.my_heroes if math.hypot(a.x - u.x, a.y - u.y) <= DEATH_NEAR)
                self.note(t, f"打野阵亡：{u.type}@{u.addr:X} 上一拍血 {int(u.hp)}/{int(u.hp_max)} @ ({int(u.x)},{int(u.y)})；"
                             f"{int(DEATH_NEAR)} 内野怪 {'、'.join(f'{k}×{v}' for k, v in near.most_common(6))}，我方 {mine} 个；"
                             f"出击层 {self.attack.state}")

    def _declog(self):
        """战斗决策明细（war3-modules/declog.py）：和毫秒层写同一个 decisions.jsonl，远见「战斗决策」页读。读不到模块 = 不写。"""
        if self.declog is None:
            try:
                import sys
                mod = A.MODULES
                if mod not in sys.path:
                    sys.path.append(mod)
                import declog
                self.declog = declog.DecisionLog(os.path.join(self.inst_dir, "decisions.jsonl"), "大脑",
                                                 ids_fn=lambda: dict(self.unit_ids.ids))
            except Exception:  # noqa: BLE001
                self.declog = False
        return self.declog or None

    def _on_ground(self, it: Intent) -> Intent:
        """★ 09-22 集结打怪时部队经常被树隔开，从根上解决集结点落在障碍里的问题 ——
        命令点落在树里/水里（或贴着树缝站不下一群人）时，每个人走到障碍物**自己这一侧**最近的地方就停下，
        一队人被树隔成两拨。下令前统一挪到最近一块站得下的空地（terrain.snap）。
        只管对点的命令；对单位的攻击、毫秒层借走的（control）不动。没有地形表原样返回。"""
        tr = self.attack.terrain
        if tr is None or (it.control and it.control != "hold") or it.target is not None or tr.clear(it.x, it.y):
            return it
        p = tr.snap(it.x, it.y)
        if p is None:
            self.counts["意图·目标点站不下·附近没有空地"] += 1
            return it
        self.counts["意图·目标点挪到空地"] += 1
        return replace(it, x=p[0], y=p[1])

    def _do(self, it: Intent, t: float) -> None:
        if not it.units:
            return
        tc = time.monotonic()
        if it.control == 'skirmish':
            n = self.act.skirmish(it)
        elif it.kind == "attack_move":
            n = self.act.attack_move(it.units, it.x, it.y)
        elif it.kind == "attack" and it.target is not None:
            n = self.act.attack(it.units, it.target)
        elif it.kind == "force_attack" and it.target is not None:
            n = self.act.attack(it.units, it.target, order=A.ORDER_ATTACK)     # 自己的兵 / 小动物：右键只会跟着走
        elif it.kind == "recover":
            n = self.act.recover(it.units, it.x, it.y)
        else:
            # hold = 租约 strategy_hold（58），毫秒层补攻击令（55）抢不走：回家/归队的兵别在路上被派回去打（1-304 14:05）
            n = self.act.move(it.units, it.x, it.y, hold=(it.why or "").startswith("威胁") or it.control == "hold")
        cmd_s = time.monotonic() - tc
        self.counts[f"意图·{it.kind}"] += 1
        if it.why and (n or 0) > 0:
            self.note(t, f"{it.why}：{n}/{len(it.units)} 个 -> ({int(it.x)},{int(it.y)})")
            dl = self._declog()
            if dl is not None:
                w, a = self.world, self.attack
                act = re.split(r"[（(·：:，,]", it.why, maxsplit=1)[0] or it.why
                inputs = {"命令": {"attack": "攻击", "attack_move": "攻击移动", "move": "移动", "recover": "整队恢复",
                                   "force_attack": "强制攻击"}.get(it.kind, it.kind),
                          "下给几个": len(it.units), "接了几个": n, "出击状态": a.state, "目标类型": a.target_kind or None}
                if w is not None:
                    inputs.update({"我方强度": round(w.own_strength, 1), "敌方强度": round(w.foe_strength, 1),
                                   "进攻性": round(w.aggression, 1)})
                ev = getattr(a, "assessment", None) or {}
                if ev and a.state in ("attack", "gather"):
                    inputs["出击评估"] = {k: ev.get(k) for k in ("good", "lhs", "rhs", "local", "response") if k in ev}
                if a.camp and a.state == "creep":
                    inputs["营地强度"] = a.camp.get("strength")
                dl.log(act, list(it.units), it.why, target=it.target,
                       point=None if it.target is not None else (it.x, it.y), inputs=inputs,
                       t0=self._step_t0, cmd_s=cmd_s, age_ms=self._snap_age, g=t, gap_ms=self._step_gap)

    # ── 一拍 ──
    def _refresh_terrain(self, t: float) -> None:
        """09-23 P3：运行时开局后几秒算好地图块（W3P，不限地图）—— 算好了就把离线的 Echo Isles 表换掉，一局换一次。
        没有 world（单测的假 cmd）/ 还没算好：照旧用离线表，5 游戏秒后再看。"""
        if self._terrain_live or t - self._terrain_try < 5.0:
            return
        self._terrain_try = t
        world = getattr(self.cmd, "world", None)
        tr = TR.from_world(world) if world is not None else None
        if tr is None:
            return
        self.attack.terrain = self.recovery.terrain = self.home_recovery.terrain = tr
        self._terrain_live = True
        self.counts["地形表·运行时"] = self.counts.get("地形表·运行时", 0) + 1
        self.note(t, f"地形表换成这一局运行时算的（W3P 地图块 {tr.n}×{tr.n}@{tr.step:.0f}）")

    def step(self, snap, t: float) -> None:
        t_now = time.monotonic()
        self._step_gap = None if self._step_t0 is None or t_now - self._step_t0 > 10.0 else (t_now - self._step_t0) * 1000
        self._step_t0 = t_now               # 战斗决策明细的耗时从这里算（这一拍拿到快照的时刻）
        age = None
        fn = getattr(self.cmd, "snapshot_age_ms", None)           # 09-23：真实的快照年龄（W3P 世界块发布时刻），老快照的 age_ms 恒 0
        if fn is not None:
            try:
                age = fn()
            except Exception:  # noqa: BLE001
                age = None
        self._snap_age = age if age is not None else getattr(snap, "age_ms", None)
        self._refresh_terrain(t)
        tm = self.timer                     # 这里只用 section（lap 会关掉 run_brain 那层的「大脑」段，见 steptime 文件头）
        with tm.section("感知·英雄等级"):
            if self.race is None:
                r = R.detect(snap.units, snap.me)
                if r is not None:
                    self.set_race(r, t)
            levels = self.percept.hero_levels([u for u in snap.units if u.owner < 12 and u.hp > 0 and u.is_hero], t)
        with tm.section("世界模型"):
            w = build_world(snap.units, snap.me, t, snap.gold or 0, snap.lumber or 0, snap.food_used or 0, snap.food_cap or 0,
                            levels=levels, previous=self.world)
            if self.config.refresh():
                CFG.apply(self.config.values, self.profile)
                self.note(t, f"配置更新（第 {self.config.version} 版）：{self.config.overrides}")
            if self.config["strategy_engine"] == "amai" and self.strategy is not None and self.strategy.started:
                # §6.1：进攻性的基数和种族表换成本局实际的（人格第 3 列 + RaceAggression.txt + 战略第 45~56 列）
                w.aggression = self.strategy.aggression(w)
            w.aggression = max(w.aggression + self.config["aggression_bias"], self.config["aggression_floor"])
            self._building_losses(self.world, w, t)
            self._unit_losses(self.world, w, t)
        self.world = w
        self.peak["army"] = max(self.peak["army"], len(w.my_army))
        self.peak["enemy"] = max(self.peak["enemy"], len(w.enemy_fighters))
        self.peak["gold"] = max(self.peak["gold"], w.gold)
        self.peak["workers"] = max(self.peak["workers"], len(w.my_workers))
        self.peak["heroes"] = max(self.peak["heroes"], len(w.my_heroes))
        self.jobs.run_due(w, self)

    def set_race(self, race: str, t: float = 0.0) -> None:
        self.race = race
        self.spec = R.spec(race)
        if race != "human":
            keep = {k: v for k, v in self.profile.items() if k in self.config.overrides}
            self.profile.clear()
            self.profile.update(dict(self.spec["profile"]), **keep)
            self.attack.profile = self.profile
            from .harass import GROUPS
            self.harass.groups_cfg = GROUPS.get(race, ())
        self.note(t, f"种族：{race}（建造表 {self.spec['table'].__name__.split('.')[-1]}）")

    def lesson(self) -> dict:
        """⚠ `counts` 等字段是**给人看的**一行串（"名称xN 名称xN"）。跨局分析一律用 `countsJson`：
        09-20 的架构复盘为了比较 259 局的胜败差异，不得不现写一个中文串正则解析器
        —— 计数器被序列化成字符串就从分析工具退化成了日志（09-20 架构评审发现的根因 C）。"""
        def fmt(c):
            return " ".join(f"{k}x{v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1]))
        groups = {"counts": Counter(self.counts) + Counter(self.stuck.counts),
                  "act": Counter(self.act.counts),
                  "attack": (Counter(self.attack.counts) + Counter(self.attack.heroes.counts)
                             + Counter(self.attack.engagement.counts)),
                  "defense": Counter(self.defense.counts) + Counter(self.defense.engagement.counts),
                  "harass": Counter(self.harass.counts),
                  "recovery": Counter(self.recovery.counts),   # 09-21 补：原来这一组根本没进 lesson
                  "home_recovery": Counter(self.home_recovery.counts),   # 家里补给（主力在外面时）
                  "coach": Counter(self.coach.counts),         # 顾问：问了几次、采纳几次、被钳几次
                  "percept": Counter(self.percept.reads)}
        out = {"brain": "war3-brain", "peakArmy": self.peak["army"], "peakEnemyArmy": self.peak["enemy"],
               "peakGold": self.peak["gold"],
               "jobs": json.dumps(self.jobs.stats(), ensure_ascii=False)}
        out.update({k: fmt(v) for k, v in groups.items()})
        out["countsJson"] = {k: dict(v) for k, v in groups.items()}
        return out


@dataclass
class _Obj:
    addr: int
    x: float
    y: float


@dataclass
class _Ghost:
    type: str
    x: float
    y: float

"""mod_combat.py —— 独立模块：保命。

    python mod_combat.py

只做一件事，而且是最不能等的那件：**别让单位白死**。

为什么必须独立出来
------------------
战略拍一圈约 3.2 秒；而野怪营地打出 79 血/秒，650 血的英雄被集火
**8.2 秒就死** —— 事件比采样周期还短，再好的阈值也来不及。

原来它塞在 PowerShell 单体里，靠"在每次底层调用的缝里插一脚"跑到 250ms。
那是在没有共享内存时能做到的最好，但它和建造、经济共用一条执行线：
建造去试落点花 1.2 秒，保命就跟着停 1.2 秒。

现在自己一个进程：
    读快照 0.087ms（共享内存，不占通道）
    下命令 2.5ms（直连通道，不起进程）
所以可以真正跑到 100ms，而且别的模块卡住完全不影响它。

判据：会不会死，不是血多不多
----------------------------
    这一拍掉了多少血 / 间隔 = 正在承受的每秒伤害
    再挨 2.5 秒还剩多少 = hp - dps * 2.5
2.5 秒 = 下命令 + 单位真的跑起来所需要的时间。撑不过就现在走。

**血少不等于危险**：一个 94/700 的猎头者站在射程外输出，和满血一样安全。
危险来自**正在被打**，所以判据用掉血速度，不用血量比例。

和别的模块不打架
----------------
撤退和进攻抢同一个单位，是这个项目里最贵的一类 bug
（同一个 ogru 被连拉六次，血 242 掉到 94，最后死了）。
所以撤下来的单位要**拿住租约**：这段时间里谁也别想再把它派回去。
租约到期自动释放 —— 模块崩了不会把单位永久锁死。
"""

from __future__ import annotations

from collections import Counter
import json
import math
import os
import random
import sys
import time
from collections import deque
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()

from w3claim import Claims, PRIO                 # noqa: E402
import itemuse                                   # noqa: E402  09-17 物品用法判定（生命药水/恢复卷轴/回城卷轴）
import unitspells                                # noqa: E402  09-22 兵种技能（女巫变形术/减速、龙鹰空中锁镣）
import fightodds                                 # noqa: E402  09-17 v291 接触后 5/10 游戏秒按世界模型胜率叫撤
import unitmind as UM                            # noqa: E402  09-21 每个单位的处境：谁在打它 / 还能撑几秒 / 养伤状态
import declog                                    # noqa: E402  09-21 战斗决策明细（给人看的，远见「战斗决策」页）
from w3state import Session                      # noqa: E402
import modlog  # noqa: E402
from w3lease import identity, write_deadlines     # noqa: E402

TICK = 0.10           # 100ms 一轮
LETHAL_SECS = 2.5     # 下命令到真的跑起来的时间
FLOOR = 0.15          # 预测剩这么点就撤
HERO_LETHAL_SECS = 4.0   # 英雄：按 4 秒算（一拍 300+ 血的仗里 2.5 秒跑不掉，09-07 三局实测）
HERO_FLOOR = 0.35        # 英雄：预测剩 35% 就拉（一条命 300 金 + 15 拍复活）
LOW_FRAC = 0.25          # ★ 09-18 打野/交战时丝血的兵和英雄很少主动后撤，硬顶到阵亡。
LOW_FRAC_HERO = 0.55     #   英雄安全最高优先：过半血且仍在掉血就允许退出，不等到 45% 才开始走
                         #   外推完还在 floor 之上，于是一直顶到死。血本来就只剩这么点的，不等外推直接进危险名单
                         #   （打野时 pull_picks 不限名额；打玩家仍受 PULL_SHARE / HERO_PULL_MIN 那几道闸）。
HERO_SWARM_FRAC = 0.90   # 090 及 #94 新样本：山丘被大群贴脸后两三秒可从健康到阵亡；第一轮受伤就脱离包围。
HERO_SWARM_MIN = 6       # 只防整队集火/包围；少量敌军仍由普通血量、护卫和真实目标规则处理。
HERO_REPOSITION_LEASE = 2.5  # 健康英雄遇大群贴脸只退到后排，不能按孤军握住 10 秒停止输出
HERO_REPOSITION_COOLDOWN = 4.0 # 给前排接火和英雄重新输出的窗口，避免每 2.5 秒再次拉走
DPS_WINDOW = 1.5         # 掉血速率的观察窗口（秒，墙钟）
MIN_WIN = 0.8            # 窗口不足时，把一次掉血摊到这么长（约一个攻击间隔）——
                         # 否则 0.1 秒里的一次 70 点攻击会被算成 700/秒（09-07 感知审计）
PULL_BACK = 800.0     # 脱离接触的距离
LEASE = 8.0           # 撤下来之后握住指挥权多久
SOLO_LEASE = 10.0     # 明确撤离时设置到期；重复发布文件不续租。
MAX_PULL = 3          # 一轮最多拉几个 —— 撤退要有人顶着才成立
SAFE_RELEASE_R = 900.0   # ★ 09-18：拉出来的人跑到这么远没有敌人就提前放开指挥权（原来一律握满 LEASE 8 秒）
PULL_THREAT_R = 1400.0   # ★ 09-22：按掉血拉人，这么近得有敌人（迫击炮 / 投石车射程 1150 也罩住）；没有就不拉（1-399 骑士在家里被拉 32 次）
SAFE_RELEASE_HOLD = 1.5  #   至少先握这么久，免得刚下完撤退令就被策略层拽回去


def pull_held_long_enough(seen: dict, addr: int, until: float, now: float) -> bool:
    """这个撤退期限握够 SAFE_RELEASE_HOLD 没有。seen：{地址: (期限, 第一次看到它的时刻)}，原地更新；期限一变（重新拉 / 续租）重新计时。
    ★ 09-22 原来倒推 `until - now > (SOLO_LEASE 或 LEASE) - SAFE_RELEASE_HOLD`，被集火撤 / 预警撤是 pulled 8 秒、solo 10 秒，
      倒推出「已握 2 秒」，下一轮就能放手（17:20:28 血法师被集火撤，17:20:29 就放了烈焰风暴）。"""
    ps = seen.get(addr)
    if ps is None or ps[0] != until:
        seen[addr] = ps = (until, now)
    return now - ps[1] >= SAFE_RELEASE_HOLD
HOME_HOLD = 2200.0    # 09-15：交战中心离自家基地这么近就不整队撤（策略层在主基地是无条件守）
HOME_COLLAPSE_RATIO = 2.4  # 027 局家门口敌方击杀速度超过我方 2.4 倍仍不撤，12 秒死 10 个；仅这种压倒性劣势打破 HOME_HOLD
HOME_FALLBACK = 900.0      # 压倒性劣势退到最近大厅背敌一侧，用建筑挡追兵并重新成团
HOME_BREAK_COOLDOWN = 12.0 # 069~081：策略层准备反打时，毫秒层每 6 秒再拉一次会重新打散；一次撤离后给集结/反打窗口
PULL_SHARE = 0.20     # 09-16：打玩家时，同一时刻撤下来的人不超过附近参战的这么多（见 pull_picks）
CRIT_FRAC = 0.15      # 09-18：血只剩这么多的兵（非英雄）不受 PULL_SHARE 限制 —— 个别小兵快没血了也不撤；
                      #   PULL_SHARE 防的是「大半个部队 69% 血也被拉走、输出塌一半」，丝血的兵再打一两下就死，拉走不损输出
ISO_FOE_R = 700.0     # 09-18 孤兵：这么近的敌方作战单位
ISO_FOES = 3          #   至少这么多个
ISO_FRIEND_R = 900.0  #   而这么近的自己人（不算自己）
ISO_FRIENDS = 1       #   不超过这么多 —— 避免留下一两个单位被一群敌军围攻
ISO_JOIN_R = 3000.0   # 孤兵往这么远以内、至少 3 个自己人扎堆（且那边敌人比自己人少）的地方退；没有就往家退
ISO_REJOIN = 2        # 09-21 孤兵脱离状态（unitmind kind="iso"）：身边 ISO_FRIEND_R 内又有这么多自己人 = 回到队伍里
ISO_POWER = 0.5       # ★ 09-18 二：身边自己人不止 1 个，但局部战力比（900 内我方 血×伤害 / 700 内敌方）< 这个，也算孤兵
                      #   实战发现两个小兵对三四个敌方单位也不撤。战斗帧 020 局：步兵 234/420 身边 700 内敌 5 个、900 内自己人 2 个
                      #   （原来「自己人 <= 1」才算孤兵），一直打到 63 血
PULL_NEAR = 1200.0    #        "附近参战"的半径；已撤下来的人按 PULL_NEAR + PULL_BACK 数
HERO_PULL_MIN = 0.70  #        正在持续掉血的英雄低于七成即可拉；健康英雄是否拉由集火/包围两条更精确规则决定
HOME_BRAWL = 6        # 09-16：离家 HOME_HOLD 内、身边 PULL_NEAR 内敌方 >= 这么多 = 家门口大仗，不拉残血（见 home_brawl）
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}

# ★★ 建筑绝对不能"拉出来"。
#
# 实测抓到的（对局画面上还没显现，日志先暴露了）：
#     eaom 32/1000 正在挨 212 血/秒 —— 拉出来
#     etoe 1052/2000 正在挨 884 血/秒 —— 拉出来
# eaom 是战争古树、etoe 是**永恒之树（主基地）**。
# 我的过滤只排了工人，没排建筑。
#
# 而且这不只是浪费命令：**暗夜的远古树真的能拔起来走** ——
# 那等于在被打的时候把生产建筑连根拔了，比不管还糟。
#
# 判据用 units.json 里的**移动速度**：建筑是 0，单位都大于 0。
# 这是游戏自己的数据，不是我维护的表 —— 和这个项目其它地方一个路子。
# ⚠ 第一版我用"移动速度 > 0"来判，**对暗夜不成立**：
#       etoe 永恒之树 spd=40    eaom 战争古树 spd=40
#   远古树真的有速度（它能拔起来走），所以速度分不出建筑和单位。
#
#   干净的判据是**人口**：每个能训练的单位都占人口，建筑一律占 0。
#       ogru=3  hfoo=2  Hamg=5  hpea=1   |   etoe=0 eaom=0 htow=0 hhou=0
#   （这是游戏自己的数据，从 MPQ 的 SLK 表抽出来的，不是我维护的表。）
def _load_units() -> tuple[dict, dict, dict, dict, set]:
    import json
    p = os.environ.get(
        "WAR3_UNITS_JSON",
        w3paths.UNITS_JSON)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        def dps(v):
            cool = float(v.get("cool") or 0)
            return (float(v.get("dmgAvg") or 0) / cool) if cool > 0 else 0.0
        # 召唤物：不占人口、不占地、不花钱、但会打人（水元素 hwat/hwt2/hwt3、狼、凤凰、骷髅……）
        summons = {k for k, v in d.items() if isinstance(v, dict)
                   and not (v.get("food") or 0) and not (v.get("foot") or 0)
                   and not (v.get("gold") or 0) and dps(v) > 0}
        return ({k: float(v.get("food") or 0) for k, v in d.items()},
                {k: int(v.get("level") or 0) for k, v in d.items()},
                {k: float(v.get("range") or 100) for k, v in d.items()},
                {k: dps(v) for k, v in d.items()},
                summons)
    except Exception:                        # noqa: BLE001
        return {}, {}, {}, {}, set()


FOOD, LEVEL, RANGE, DPS, SUMMONS = _load_units()


def _load_speed() -> dict:
    """单位移速（units.json 的 spd，游戏单位 / 游戏秒）。读不到返回空表（按 270 算）。"""
    import json
    p = os.environ.get("WAR3_UNITS_JSON", w3paths.UNITS_JSON)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return {k: float(v.get("spd") or 0) for k, v in d.items() if isinstance(v, dict)}
    except Exception:                        # noqa: BLE001
        return {}


SPEED = _load_speed()


def _load_armor() -> dict:
    import json
    p = os.environ.get("WAR3_UNITS_JSON", w3paths.UNITS_JSON)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return {k: float(v.get("armor") or 0) for k, v in d.items() if isinstance(v, dict)}
    except Exception:                        # noqa: BLE001
        return {}


ARMOR = _load_armor()
MELEE_RANGE = 150.0


def target_score(u, f) -> float:
    """★★★ v279 补攻击令挑谁：**远程集火治疗、近战先杀脆皮。**

    09-16 设计要求：遇敌时远程优先集火治疗单位，近战优先击杀脆皮单位。
    原来所有人一套权重（治疗 ×6、施法 ×2.5、英雄 ×3，除以剩余血量），近战闲兵也被派去追后排牧师。
    ⚠ 和 war3ai.ps1 的 W3-TargetScoreFor（W3-TargetScore / W3-MeleeScore）是同一条规则，改一边要改另一边。
      远程（射程 > 150）：w × (1 + 2000/曼哈顿距离(下限 200)) × 1000/剩余血
      近战：w × (1 + 800/距离(下限 150)) × 1000/有效血，有效血 = 剩余血 × (1 + 0.06×护甲)；英雄 ×0.6，治疗/施法 ×1.3
    """
    if f.type in SUMMONS:
        return SUMMON_W * _score_raw(u, f)
    return _score_raw(u, f)


# ★ 09-18 部分单位一直在打召唤物，收益很低；攻击应有优先目标和集火目标。
#   原来的分数里「1000/剩余血」权重最大，骷髅（uske 180 血）、水元素这类召唤物血少，**反而是全场得分最高的** ——
#   补攻击令专挑它们。召唤物是限时单位，打死它不减对面一分钱、一点人口；只在身边没有正经目标时才打。
SUMMON_W = 0.2


def _score_raw(u, f) -> float:
    if RANGE.get(u.type, 100.0) > MELEE_RANGE:
        w = 1.0
        if f.type in HEALERS:
            w = 6.0
        elif f.type in CASTERS:
            w = 2.5
        elif f.type and "A" <= f.type[0] <= "Z":
            w = 3.0
        d = max(200.0, abs(f.x - u.x) + abs(f.y - u.y))
        return w * (1.0 + 2000.0 / d) * 1000.0 / max(1.0, f.hp)
    eff = max(1.0, f.hp) * (1.0 + 0.06 * max(0.0, ARMOR.get(f.type, 0.0)))
    d = max(150.0, math.hypot(f.x - u.x, f.y - u.y))
    w = 1.0
    if f.type and "A" <= f.type[0] <= "Z":
        w = 0.6
    elif f.type in HEALERS or f.type in CASTERS:
        w = 1.3
    return w * (1.0 + 800.0 / d) * 1000.0 / eff


def foe_fighter_type(typ: str) -> bool:
    """敌方这个类型算不算"这片战场上的作战单位"。

    ★ 09-15：原来只认占人口的（和 can_move 同一个判据），**召唤物全被漏掉**。
      交战帧里敌方 Archmage 每场召 1~3 个 2/3 级水元素（hwt2 675 血、hwt3 900 血，30 秒伤/个），
      211239 决定性那场同时 3 个 hwt3 贴着我们打 —— 撤不撤的推演里它们是 0。
    """
    return FOOD.get(typ, 0.0) > 0.0 or typ in SUMMONS


# ★★ 躲暴风雪（09-15）
#
#   v257 三局决定性的防守战，敌方 Archmage 接触帧里 27%~37% 挂着暴风雪（0x000D0079），
#   我方掉血里「同一帧 250 内 >=3 个一起掉」占 44%/46%；对手是圣骑士、没有暴风雪的那局只有 18%。
#   而我方单位站在里面不动：兵力 27:19、21:15 的仗，战损 29:10、28:12。
#
#   施法点读不到，用**掉血簇**估：敌方有英雄正在引导暴风雪、我方同一轮 >=3 个人彼此 250 内一起掉血，
#   簇重心就是落点。落点半径 BLIZ_R 内的自己人沿"背离施法者"方向挪 BLIZ_STEP ——
#   施法者站在敌阵后面，背离它 = 往自己这边退，不往敌阵里钻。
#   只挪一步、握 BLIZ_HOLD 秒，到期交还（补攻击令和策略层 DEFEND 自然把人带回去）。
BLIZZARD = 0x000D0079     # order-ids.txt
BLIZ_CLUSTER = 250.0      # 和交战帧分析同一个"成片"口径
BLIZ_MIN_HIT = 3
BLIZ_R = 320.0            # 暴风雪半径 200；落点是估的，多留 120
BLIZ_STEP = 420.0
BLIZ_HOLD = 1.2           # 墙钟秒；3 倍速约 3.6 游戏秒
BLIZ_CAST_R = 1600.0      # 施法者离掉血簇这么近才认（射程 800 + 估计误差）—— 远处的成片掉血是迫击炮之类


# ★★ 09-23 打得过就正面打，打不过就边拉扯边输出。AMAI Jobs/FOCUSFIRE_CONTROL.eai GroupMicroSystem：
#   带伤的远程被敌方近战贴脸（200 内、面朝它、移速不比它快）-> 背离那个近战走 800，1.8 游戏秒后回来接着打（远程拉扯近战）。
#   我们读不到朝向，用「KITE_R 内的敌方近战」代替；只拉带伤的（< KITE_HP），同一个单位 KITE_CD 内不再拉；
#   近战跑得比它快的不拉（拉开也会被追上，站着打更划算）。英雄另有保命那套，不进这条。
KITE_R = 200.0
KITE_STEP = 600.0        # AMAI 800；KITE_HOLD 内走不完 800，按走得到的给
KITE_HOLD = 0.6          # 墙钟秒（3 倍速约 1.8 游戏秒 = AMAI RESET_GUARD_POSITION_ONLY 1.8）
KITE_HP = 0.9
KITE_CD = 2.0


def kite_moves(units, foes, now: float, last: dict) -> list:
    """-> [(我方远程, tx, ty, 贴着它的敌方近战)]。units 已经去掉了被拉 / 轮换 / 躲技能的。"""
    melee = [f for f in foes if f.hp > 0 and RANGE.get(f.type, 100.0) <= MELEE_RANGE and foe_fighter_type(f.type)]
    out = []
    if not melee:
        return out
    for u in units:
        if (u.hp <= 0 or getattr(u, "is_hero", False) or RANGE.get(u.type, 100.0) <= MELEE_RANGE
                or u.hp >= KITE_HP * max(1.0, u.hp_max) or now - last.get(u.addr, -1e9) < KITE_CD):
            continue
        near = [f for f in melee if math.hypot(f.x - u.x, f.y - u.y) <= KITE_R]
        if not near:
            continue
        m = min(near, key=lambda f: math.hypot(f.x - u.x, f.y - u.y))
        if SPEED.get(m.type, 270.0) > SPEED.get(u.type, 270.0):
            continue
        dx, dy = u.x - m.x, u.y - m.y
        n = math.hypot(dx, dy) or 1.0
        out.append((u, u.x + dx / n * KITE_STEP, u.y + dy / n * KITE_STEP, m))
    return out


def blizzard_dodge(foes, hits, fighters):
    """返回 (要挪的 [(单位, tx, ty)], 估出来的落点 或 None, 有没有敌方英雄正在放暴风雪)。

    foes: 敌方玩家的活单位；hits: 我方这一轮掉了血的单位；fighters: 这一轮可以指挥的自己人。
    """
    casters = [f for f in foes if f.is_hero and f.order == BLIZZARD]
    if not casters:
        return [], None, False
    best = []
    for h in hits:
        grp = [g for g in hits if math.hypot(g.x - h.x, g.y - h.y) <= BLIZ_CLUSTER]
        if len(grp) > len(best):
            best = grp
    if len(best) < BLIZ_MIN_HIT:
        return [], None, True
    cx = sum(g.x for g in best) / len(best)
    cy = sum(g.y for g in best) / len(best)
    caster = min(casters, key=lambda c: math.hypot(c.x - cx, c.y - cy))
    if math.hypot(caster.x - cx, caster.y - cy) > BLIZ_CAST_R:
        return [], None, True
    dx, dy = cx - caster.x, cy - caster.y
    n = math.hypot(dx, dy)
    if n < 1.0:
        dx, dy, n = 1.0, 0.0, 1.0
    moves = [(u, u.x + dx / n * BLIZ_STEP, u.y + dy / n * BLIZ_STEP)
             for u in fighters if math.hypot(u.x - cx, u.y - cy) <= BLIZ_R]
    return moves, (cx, cy), True


# ★★★ 英雄在合适的时机、合适的点位放合适的技能（09-15）
#
#   问题：英雄几乎不放技能；要让英雄在最合适的时机和点位放最合适的技能。
#   现场：最近三局大法师每局只放 8~9 次、全是水元素；山丘之王一局 3 次风暴之锤；暴风雪从来没学过。
#   策略层 W3-CastSpells 每个英雄 4 拍（约 12 游戏秒）才试一次、而且只对着"当前攻击目标"放 ——
#   技能的时机是秒级的（敌人扎堆的那一两秒、英雄快死的那一下），所以挪到 100ms 这一层。
#
#   每个英雄按优先级从上往下试，一轮只放一个：
#     保命  神圣护甲（血 <35% 且 600 内有敌人）/ 天神下凡（血 <50% 且 600 内 >=4 个敌人）
#     救人  圣光术：800 内血 <=60% 且缺 >=250 的友军单位（不是建筑），挑血比例最低的
#     控制  风暴之锤：700 内的敌方英雄（血比例最低）> 施法者/治疗
#     召唤  水元素：900 内有敌人
#     范围  暴风雪：800 内半径 250 有 >=4 个敌人、圈里自己人 <=1 —— 落点是这团的重心
#           烈焰风暴：半径 200 有 >=3 个
#     近身  雷霆一击：300 内 >=3 个地面敌人
#   冷却按墙钟算（3 倍速：游戏秒 / 3）；引擎退令（没学/没蓝）按 REJECT_BACKOFF 退避，别每 100ms 重试。
#
# ★★★ 09-18 兽族 / 亡灵 / 暗夜 + 常用中立英雄 —— **一张技能表（SPELLS），一套施法框架**。
#   原来只会放人族英雄技能，兽族、亡灵、暗夜精灵英雄的主动技能都要补上。
#   原来人族 8 个技能写成 _spell_plan 里按技能名分支，订单号 / 冷却 / 耗蓝 / 技能代码四张表各手写一份
#   （SPELL_ABIL 还在 600 行外）。再加 50 来个技能照抄分支，同一条规则就写在了多处，只改一处等于没改。
#   现在：
#     SPELLS[技能名] = 技能代码 + 订单号 + 施法方式 + 出手规则（_RULES 里的一个函数名）+ 规则参数
#     SPELL_ORDERS / SPELL_ABIL / SPELL_MANA / SPELL_CD 全部从这张表**派生**，不再手写
#   人族原来的判据原样搬进通用规则（summon / aoe_point / nuke / self_aoe / self_save / heal_ally），
#   参数照原分支抄；tests/test_hero_casts.py 等人族测试一条没改、全绿 —— 那是"搬家没丢例外"的证据。
#   冷却 / 耗蓝 / 射程 / 半径 / 伤害一律按**当前等级**取 skills.json；表里的 cd= / mana= 只是人族原来的常数（读不到数据时兜底）。
#
#   施法方式（how）：instant 无目标 / point 点 / target 单位 / autocast 开自动施法（学了开一次）/ toggle 开关（献祭）
#   订单号：全部对过 w3data/replay_dict.json（游戏自己 *AbilityFunc.txt 的 Order= / Orderon= / Orderoff=）+ order-ids.txt。
#     ⚠ 对出来和印象不一样的：剑刃风暴是 AOww / whirlwind（不是 AObf）；恐惧魔王地狱火是 inferno 0x000D0108
#       （dreadlordinferno 0x000D0100 是战役版）；毒蛇守卫是 ward；风暴之锤 thunderbolt、圣光术 holybolt。
#     没收进来的：炎魔（ANlm 的订单名 slimemonster 不在订单号表里）、娜迦霜冻之箭（ANfa 查不到 Orderon）、
#       自然之力（要读树的位置，快照里没有）、各英雄的传送/侦察类、黑暗游侠吸取/符咒、修补匠机器人。
#
#   规则参数（opts，没写 = 默认）：
#     cd / mana         人族原常数（兜底）      cd_from=(字段, 上限)  冷却取 max(Cool, min(字段, 上限))（疾风步按隐身时长，别刷新）
#     rng / pad         射程兜底 / 射程外多走几步  area / area_default  半径覆盖 / 兜底
#     air               能不能打空军            players              只数敌方玩家的单位（大招别砸野怪）
#     organic / ground / living   目标过滤：非机械 / 非空军 / 非亡灵（死亡缠绕打活人）；魔免单位一律不选（MAGIC_IMMUNE）
#     need / hero_need  至少罩住几个 / 有敌方英雄时至少几个      per=(字段, 倍数, 兜底) + value + need_min  按伤害估人数（aoe_need）
#     friendly_fire     砸自己人（暴风雪类）：落点半径 +150 内一个自己人都不许有
#     channel=(字段, 倍数, 兜底) / precast   引导时长 / 前摇 -> 施法保护期（cast_hold_seconds）
#     while_pulled      撤退中也放（逃生技能）；then=（后续动作，见主循环 followups）
def _sp(abil, order, how, rule, **opts):
    return {"abil": abil, "order": order, "how": how, "rule": rule, "opts": opts}


SPELLS = {
    # ── 人族（09-15/09-16 的规则，参数照原分支抄）─────────────────────────────
    "waterelemental": _sp("AHwe", 0x000D0081, "instant", "summon", cd=7.0, mana=125.0),
    "blizzard": _sp("AHbz", 0x000D0079, "point", "aoe_point", cd=3.0, mana=75.0, air=True, friendly_fire=True,
                    per=("DataB", 3.0, 30.0), value=360.0, channel=("DataA", 1.0, 6.0)),
    "stormbolt": _sp("AHtb", 0x000D007F, "target", "nuke", cd=3.5, mana=75.0, rng=600.0, organic=True),
    "thunderclap": _sp("AHtc", 0x000D0080, "instant", "self_aoe", cd=2.5, mana=90.0, area_default=250.0, need=2, hero_need=1),
    "avatar": _sp("AHav", 0x000D0076, "instant", "self_save", cd=60.0, mana=150.0, frac=0.5, r=600.0, n=4),
    "holylight": _sp("AHhb", 0x000D007C, "target", "heal_ally", cd=2.0, mana=65.0, organic=True),
    "divineshield": _sp("AHds", 0x000D007A, "instant", "self_save", cd=12.0, mana=25.0, frac=0.42, r=600.0, n=1,
                        focus_frac=0.65, focus_n=2, hit_frac=0.55, pulled_frac=0.60, while_pulled=True),
    # ★ 09-21 实战看到身边全是自家单位时也放出了烈焰风暴，当时按游戏说明（HumanAbilityStrings.txt [AHfs]：
    #   「每秒对**敌方的**地面单位造成 45 点的伤害」）判它不伤自己人、去掉了圈里不许有自己人的限制。
    # ★★ 09-22 实测推翻：**烧自己人**（火落地后落点 200 内我方多掉血平均 +82，敌方 +54，见 STILL_WINDOW 上面那段）——
    #   游戏说明和 targs 一样回答不了「伤不伤友军」，要看实战掉血。现在不是「圈里有自己人就不放」（混战里放不出来），
    #   而是 friendly_fire="dodge"：放了之后让圈里的自己人躲开；still / no_walk：只瞄站桩的敌人、不走路直接放。
    "flamestrike": _sp("AHfs", 0x000D0208, "point", "aoe_point", cd=3.5, mana=135.0, air=False,
                       per=("DataF", 1.0, 90.0), value=270.0, precast=1.33,
                       friendly_fire="dodge", still=True, no_walk=True),
    # ★ 09-21 血魔法师在团战/打野时技能放得太少，缺蓝时也没有补蓝手段。近一个半小时六个实例心跳：
    #   烈焰风暴下令 152 次（缺蓝没放只 25 人·轮），法力虹吸 / 凤凰 0 次 —— 没接。订单号 drain 0x000D0207、summonphoenix 0x000D0209
    #   （order-ids.txt；同一张表里 flamestrike 0x000D0208 和上面这行对得上）。
    #   法力虹吸：10 蓝、射程 600、6 秒每秒吸 15 起 —— 蓝不够放烈焰风暴时从敌方英雄/施法者身上吸（_rule_siphon）
    "siphonmana": _sp("AHdr", 0x000D0207, "target", "siphon", cd=3.0, mana=10.0, organic=True, below=135.0,
                      channel=("Dur", 1.0, 6.0)),
    "phoenix": _sp("AHpx", 0x000D0209, "instant", "summon", cd=60.0, mana=175.0),
    # ── 兽族 ──────────────────────────────────────────────────────────────
    # 剑圣：撤退时疾风步隐身跑 / 追残血英雄；残血被围放镜像；人堆里剑刃风暴
    "windwalk": _sp("AOwk", 0x000D00A1, "instant", "escape", while_pulled=True, cd_from=("Dur", 20.0), opener=True),
    "mirrorimage": _sp("AOmi", 0x000D009B, "instant", "self_save", frac=0.4, r=500.0, n=2),
    "bladestorm": _sp("AOww", 0x000D00A0, "instant", "self_aoe", area_default=200.0, pad=50.0, need=3, hero_need=2,
                      min_frac=0.25, players=True, channel=("Dur", 1.0, 7.0)),
    # 先知：闪电链打扎堆 / 英雄；野狼先召；地震砸建筑群或大团地面兵
    "spiritwolf": _sp("AOsf", 0x000D009E, "instant", "summon"),
    "earthquake": _sp("AOeq", 0x000D0099, "point", "aoe_point", rng=1000.0, area_default=250.0, air=False, need=6,
                      friendly_fire=True, players=True, buildings=2, bld_r=400.0, channel=("Dur", 1.0, 25.0)),
    "chainlightning": _sp("AOcl", 0x000D0097, "target", "nuke", rng=700.0, organic=True, splash_r=500.0, splash_need=3,
                          lone_caster=False),
    # 牛头人酋长：战争践踏（身边人多 / 有英雄）；震荡波沿人最多的方向
    "stomp": _sp("AOws", 0x000D009F, "instant", "self_aoe", area_default=250.0, need=3, hero_need=2),
    "shockwave": _sp("AOsh", 0x000D009D, "point", "line", rng=700.0, length=("DataC", 800.0), half=("Area", 1.0, 125.0),
                     per=("DataA", 1.0, 75.0), value=300.0),
    # 暗影猎手：巫毒（全队大残血）> 治疗波（含自己）> 妖术（英雄 > 施法者 > 大块头）> 毒蛇守卫（插在敌人方向）
    "voodoo": _sp("AOvd", 0x000D0217, "instant", "team_save", own_n=4, foe_n=5, avg_frac=0.6, hero_frac=0.35, players=True),
    "healingwave": _sp("AOhw", 0x000D0215, "target", "heal_ally", self_ok=True, organic=True, rng=700.0),
    "hex": _sp("AOhx", 0x000D0216, "target", "disable", rng=800.0, organic=True, min_hp=700.0),
    "serpentward": _sp("AOsw", 0x000D0218, "point", "summon", reach=800.0, early=False, ahead=300.0),
    # ── 亡灵 ──────────────────────────────────────────────────────────────
    # 死亡骑士：残血吃自己的召唤物/快死的兵回血；尸体多开操纵死尸；死亡缠绕先奶自己的亡灵、再打活的敌方英雄
    "deathpact": _sp("AUdp", 0x000D00FF, "target", "sacrifice", mode="hp", frac=0.45, missing=250.0),
    "animatedead": _sp("AUan", 0x000D00F9, "instant", "corpses", need=4, foe_n=4, players=True),
    "deathcoil": _sp("AUdc", 0x000D00FE, "target", "heal_or_nuke", rng=800.0, undead_only=True, heal_default=200.0,
                     dmg_mult=0.5, dmg_default=200.0, living=True, organic=True, lone_caster=False),
    # 巫妖：霜冻护甲开自动；缺蓝献祭；死亡凋零砸大团；霜冻新星（打死英雄 > 英雄 > 扎堆 > 能打死的）
    "frostarmoron": _sp("AUfu", 0x000D01EA, "autocast", "autocast", cd=60.0),
    "darkritual": _sp("AUdr", 0x000D00FB, "target", "sacrifice", mode="mana", mana_below=125.0),
    "deathanddecay": _sp("AUdd", 0x000D00FD, "point", "aoe_point", rng=1000.0, area_default=300.0, air=True, need=6,
                         friendly_fire=True, players=True, buildings=3, bld_r=450.0, channel=("Dur", 1.0, 35.0)),
    "frostnova": _sp("AUfn", 0x000D0102, "target", "nuke", rng=800.0, dmg_default=50.0, dmg_add=(("DataB", 1.0),),
                     organic=True, splash_r=200.0, splash_need=3),
    # 恐惧魔王：地狱火砸扎堆（大招，只砸玩家）；睡眠（没人在打的英雄/施法者，挨打就醒）；腐臭蜂群沿直线
    "inferno": _sp("AUin", 0x000D0108, "point", "aoe_point", rng=900.0, area_default=250.0, air=False, need=4, players=True),
    "sleep": _sp("AUsl", 0x000D0103, "target", "disable", rng=800.0, organic=True, unengaged=True, min_hp=700.0),
    "carrionswarm": _sp("AUcs", 0x000D00FA, "point", "line", rng=700.0, length=("DataC", 800.0), half=("DataD", 0.5, 300.0),
                        air=True, per=("DataA", 1.0, 75.0), value=300.0),
    # 地穴领主：腐尸甲虫开自动；蝗虫群（大团）；穿刺沿直线（地面）
    "carrionscarabson": _sp("AUcb", 0x000D0248, "autocast", "autocast", cd=60.0),
    "locustswarm": _sp("AUls", 0x000D024C, "instant", "self_aoe", area=800.0, need=5, air=True, players=True),
    "impale": _sp("AUim", 0x000D024B, "point", "line", rng=700.0, length=("Rng", 700.0), half=("Area", 0.5, 250.0),
                  per=("DataC", 1.0, 50.0), value=200.0),
    # ── 暗夜精灵 ──────────────────────────────────────────────────────────
    # 恶魔猎手：变身（大仗 / 残血被围）；法力燃烧（智力英雄 > 英雄 > 施法者）；献祭贴身开、周围没人关
    "metamorphosis": _sp("AEme", 0x000D00D4, "instant", "self_save", frac=0.5, r=600.0, n=3, or_n=6, or_r=900.0, players=True),
    "manaburn": _sp("AEmb", 0x000D00D3, "target", "disable", rng=300.0, pad=200.0, prefer_int=True),
    "immolation": _sp("AEim", 0x000D00D1, "toggle", "toggle", off=0x000D00D2, area_default=160.0, pad=90.0, need=2,
                      min_mana=60.0, off_r=900.0, cd=2.0),
    # 丛林守护者：宁静（一片自己人残血、身边没敌人）；纠缠根须（地面：英雄 > 施法者 > 大块头）
    "tranquility": _sp("AEtq", 0x000D00D8, "instant", "team_heal", need=4, missing=1000.0, safe_r=450.0,
                       channel=("Dur", 1.0, 30.0)),
    "entanglingroots": _sp("AEer", 0x000D00CB, "target", "disable", rng=800.0, organic=True, ground=True, min_hp=700.0),
    # 月之女祭司：灼热之箭开自动；群星坠落（周围大团、贴身的少、血过半）
    "flamingarrows": _sp("AHfa", 0x000D00CE, "autocast", "autocast", cd=60.0),
    "starfall": _sp("AEsf", 0x000D00D7, "instant", "self_aoe", area=800.0, need=5, air=True, players=True, max_close=1,
                    close_r=300.0, min_frac=0.5, channel=("Dur", 1.0, 45.0)),
    # 守望者：闪烁（撤退时往远离敌人的方向闪 / 追残血英雄）；复仇天神（大仗）；刀扇（身边）；暗影突袭
    "blink": _sp("AEbl", 0x000D022D, "point", "escape", while_pulled=True),
    "spiritofvengeance": _sp("AEsv", 0x000D0230, "instant", "self_save", or_n=5, or_r=900.0, players=True),
    "fanofknives": _sp("AEfk", 0x000D022E, "instant", "self_aoe", area_default=400.0, air=True,
                       per=("DataA", 1.0, 75.0), value=270.0),
    "shadowstrike": _sp("AEsh", 0x000D022F, "target", "nuke", rng=300.0, pad=200.0, dmg="DataE", dmg_default=75.0,
                        dmg_add=(("DataA", 5.0),), organic=True),
    # ── 中立（酒馆英雄，可选；规则全是上面的通用规则）──────────────────────────
    "rainoffire": _sp("ANrf", 0x000D010E, "point", "aoe_point", rng=800.0, area_default=200.0, air=True, friendly_fire=True,
                      per=("DataB", 3.0, 25.0), value=360.0, channel=("DataA", 1.0, 6.0)),
    "howlofterror": _sp("ANht", 0x000D026C, "instant", "self_aoe", area_default=500.0, need=4, air=True),
    "blackarrowon": _sp("ANba", 0x000D0262, "autocast", "autocast", cd=60.0),
    "silence": _sp("ANsi", 0x000D0270, "point", "aoe_point", rng=900.0, area_default=200.0, air=True, need=3,
                   must="caster_or_hero"),
    "summongrizzly": _sp("ANsg", 0x000D0272, "instant", "summon"),
    "summonquillbeast": _sp("ANsq", 0x000D0273, "instant", "summon"),
    "summonwareagle": _sp("ANsw", 0x000D0274, "instant", "summon"),
    "drunkenhaze": _sp("ANdh", 0x000D0269, "target", "disable", rng=550.0, organic=True, min_hp=700.0),
    "breathoffire": _sp("ANbf", 0x000D0264, "point", "line", rng=375.0, length=("DataC", 375.0), half=("DataD", 0.5, 300.0),
                        air=True, per=("DataA", 1.0, 65.0), value=250.0),
    "manashieldon": _sp("ANms", 0x000D026D, "instant", "self_save", frac=0.5, r=700.0, n=1, while_pulled=True),
    "forkedlightning": _sp("ANfl", 0x000D026B, "target", "nuke", rng=600.0, organic=True, splash_r=300.0, splash_need=2,
                           lone_caster=False),
    "summonfactory": _sp("ANsy", 0x000D02B2, "point", "summon", ahead=200.0),
    "clusterrockets": _sp("ANcs", 0x000D02AC, "point", "aoe_point", rng=800.0, area_default=200.0, air=False,
                          per=("DataD", 1.0, 105.0), value=270.0),
    "chemicalrage": _sp("ANcr", 0x000D02B7, "instant", "self_save", or_n=3, or_r=600.0),
    "acidbomb": _sp("ANab", 0x000D02B6, "target", "disable", rng=700.0, organic=True, min_hp=700.0),
    "soulburn": _sp("ANso", 0x000D02BC, "target", "disable", rng=700.0, organic=True, prefer_int=True),
}
# 09-21 战斗决策明细里的技能中文名（没列的照英文名显示）
SPELL_CN = {
    "waterelemental": "召唤水元素", "blizzard": "暴风雪", "brilliance": "辉煌光环", "stormbolt": "风暴之锤", "thunderclap": "雷霆一击",
    "avatar": "天神下凡", "holylight": "圣光术", "divineshield": "神圣护甲", "flamestrike": "烈焰风暴", "siphonmana": "法力虹吸",
    "phoenix": "召唤凤凰", "windwalk": "疾风步", "mirrorimage": "镜像", "bladestorm": "剑刃风暴", "spiritwolf": "召唤野狼",
    "chainlightning": "闪电链", "earthquake": "地震", "stomp": "战争践踏", "shockwave": "震荡波", "voodoo": "巫毒", "hex": "妖术",
    "healingwave": "治疗波", "serpentward": "毒蛇守卫", "deathcoil": "死亡缠绕", "deathpact": "死亡契约", "animatedead": "操纵死尸",
    "frostnova": "霜冻新星", "frostarmoron": "霜冻护甲", "darkritual": "黑暗仪式", "deathanddecay": "死亡凋零", "impale": "穿刺",
    "carrionswarm": "腐尸甲虫群", "sleep": "睡眠", "inferno": "地狱火", "manaburn": "法力燃烧", "immolation": "献祭",
    "metamorphosis": "变身", "entanglingroots": "纠缠根须", "starfall": "群星坠落", "tranquility": "宁静", "blink": "闪烁",
    "fanofknives": "刀阵旋风", "shadowstrike": "暗影突袭", "spiritofvengeance": "复仇之魂",
}

HERO_SPELLS = {                       # 每个英雄按优先级从高到低试，一轮只放一个
    "Hamg": ("waterelemental", "blizzard"),
    "Hmkg": ("avatar", "stormbolt", "thunderclap"),
    "Hpal": ("divineshield", "holylight"),
    "Hblm": ("phoenix", "flamestrike", "siphonmana"),   # 蓝不够放前两个时往下试：虹吸只要 10 蓝
    "Obla": ("windwalk", "mirrorimage", "bladestorm"),
    "Ofar": ("spiritwolf", "earthquake", "chainlightning"),
    "Otch": ("stomp", "shockwave"),
    "Oshd": ("voodoo", "healingwave", "hex", "serpentward"),
    # 09-18 第一批（01:08）只接了人族 + 兽族（5 号实例正在打兽族）；亡灵 / 暗夜 / 中立是第二批，tests/test_ud_ne_casts.py 守着
    "Udea": ("deathpact", "animatedead", "deathcoil"),
    "Ulic": ("frostarmoron", "darkritual", "deathanddecay", "frostnova"),
    "Udre": ("inferno", "sleep", "carrionswarm"),
    "Ucrl": ("carrionscarabson", "locustswarm", "impale"),
    "Edem": ("metamorphosis", "manaburn", "immolation"),
    "Ekee": ("tranquility", "entanglingroots"),
    "Emoo": ("flamingarrows", "starfall"),
    "Ewar": ("blink", "spiritofvengeance", "fanofknives", "shadowstrike"),
    "Nplh": ("rainoffire", "howlofterror"),
    "Nbrn": ("blackarrowon", "silence"),
    "Nbst": ("summongrizzly", "summonquillbeast", "summonwareagle"),
    "Npbm": ("drunkenhaze", "breathoffire"),
    "Nngs": ("manashieldon", "forkedlightning"),
    "Ntin": ("summonfactory", "clusterrockets"),
    "Nalc": ("chemicalrage", "acidbomb"),
    "Nfir": ("soulburn",),
}
SPELL_ORDERS = {n: s["order"] for n, s in SPELLS.items()}     # 派生（见上），别再手写
SPELL_ABIL = {n: s["abil"] for n, s in SPELLS.items()}        # 技能名 -> 引擎技能代码（tests/test_hero_mana.py 的 ALIAS 对人族那几条）
MANA_TTL = 0.5                        # 英雄蓝量多久读一次（墙钟秒）；读一次 1~50ms
# ★★ 09-17 水元素先接敌 + 护卫大法师（大法师应优先让水元素承受伤害）
#   inst1 三局交战帧：敌方任务目标落在我方英雄 16~21%、水元素只有 3~6%；两者都在场的接触帧里英雄反而比水元素离敌人更近 17~49%，
#   水元素 70~90% 的帧**没有订单** —— 本模块的 fighters 按占人口筛（can_move），召唤物不占人口，补攻击令/撤退从来不管它。
WATER_TYPES = {"hwat", "hwt2", "hwt3"}
WATER_EARLY_FOE_R = 1200.0            # 敌方玩家作战单位这么近就先召（原来 900 才召）
WATER_EARLY_CREEP_R = 1400.0          # 野怪这么近、且英雄身边 1200 内有 >= 2 个自己的兵（在去打野的路上/集合点）就先召
GUARD_R = 900.0                       # 离大法师这么近的敌人（玩家作战单位或野怪）算威胁
GUARD_LEASH = 1500.0                  # 水元素离大法师这么远以内才叫来护卫
GUARD_CREEP_ESCORT_R = 1000.0         # 水元素只有在英雄身边已经有足够非召唤战斗单位时才允许主动碰野
GUARD_CREEP_ESCORT_MIN = 2            # 防止 1-011 的“大法师 + 水元素”绕过策略层集合门槛开怪
GUARD_SAME = 6.0                      # 同一个水元素打同一个目标，这么久内不重下（重复下令会打断攻击）
GUARD_SWITCH = 2.0                    # 换目标至少隔这么久（威胁在几个人之间跳时别来回拽）
GUARD_STICK = 250.0                   # 上一个目标比「离大法师最近的」远不到这么多（而且还在 GUARD_R 内）就不换（guard_picks）
# ★ 09-22 原来护卫令下完当场还租约，大脑下一拍（整队交战 76）就把水元素改走、6 秒后护卫再拽回来：
#   交战中「水元素护卫大法师 <-> 支援交战 / 先打兵」6 局约 140 次来回。现在下令成功后握住 GUARD_HOLD 秒，
#   按施法那一级（80，高过大脑整队交战 76、低于保命 90）；目标死了 / 离开大法师 GUARD_R 就提前还（guard_release）。
#   ⚠ 租约按**模块**记：本模块别的功能再 take 同一个单位会把优先级和期限改写掉、give_back 会整个放掉 ——
#     上线第一局（361）水元素照样 1~2 秒在两层之间切一次，就是补攻击令（55 级 1.5 秒、补完就还）把护卫的握住冲掉了。
#     所以两条补攻击令都跳过 guard_held 里的水元素。以后再加「拿了就还」的功能，也要跳过它。
GUARD_HOLD = GUARD_SAME
GUARD_PRIO = "cast"


def guard_release(held: dict, last: dict, hero_by_water: dict, threats, now: float) -> list:
    """护卫握着的水元素里，该提前还给大脑的 -> [水元素地址]。held：{水元素: 握到几时}；last：guard_last；
    hero_by_water：{水元素: 它护的那个大法师}（没了 = 大法师死了）；threats：这一轮的敌人。"""
    alive = {t.addr: t for t in threats if t.hp > 0}
    out = []
    for w, until in held.items():
        if until <= now:
            out.append(w)
            continue
        tgt = alive.get((last.get(w) or (None,))[0])
        hero = hero_by_water.get(w)
        if tgt is None or hero is None or hero.hp <= 0 or _dist(tgt, hero) > GUARD_R:
            out.append(w)
    return out
HERO_DANGER_R = 1300.0                # 09-17 英雄接触前预警（见 hero_danger）：看英雄身边这么远
HERO_DANGER_MIN = 5                   #   敌方玩家作战单位至少这么多
HERO_DANGER_RATIO = 2.0               #   对面打光这片我方的时间不到我方打光对面的一半（比遭遇战撤退的 1.6 更严：没接触就拉，只拉明显打不过的）
HERO_DANGER_EVERY = 0.3               #   每个英雄多久判一次（墙钟秒）。★ 09-22 1.0 -> 0.3：3 倍速下 1 秒墙钟 = 3 游戏秒，
#                                       1-299 英雄每游戏秒掉 60~100 血，一次判晚就是两三百血（判据本身只算快照，便宜）
HERO_RESERVE_R = 3000.0               # 1-215：无护卫英雄看更外一圈正在逼近的敌方主力，不能等 9 个敌人进 1300 才跑
HERO_RESERVE_MIN = 5
HERO_RESERVE_RATIO = 1.20
HERO_ESCORT_R = 1600.0
HERO_ESCORT_MIN = 2
HERO_SCREEN_R = 900.0     # 09-22 外圈预警：自己人离这批敌人这么近（且比英雄近）= 挡在英雄前面（见 precontact_hero_retreat）
HERO_SCREEN_MIN = 3
GAME_SPEED = 3.0                      # 倍速：引擎冷却是游戏秒，换墙钟除以它（SPELL_CD 也是按游戏秒/3 写的）
LEARN_TTL = 2.0                       # 英雄已学技能多久读一次（墙钟秒）；09-23 起读的是世界块本地快照（原来走一遍技能列表约 10 次调用，10 秒一读）
REJECT_BACKOFF = 10.0
AIR_TYPES = {"hgry", "hgyr", "hdhw", "owyv", "obat", "otbr", "ugar", "ufro", "ubsp", "ehip", "nzep"}


# ★★ 09-16 施法决策要把英雄技能的实际效果（等级、射程、半径、伤害）考虑进去。
#   原来的出手条件是估的常数、不看等级：暴风雪按半径 250 找扎堆、圈里允许 1 个自己人（暴风雪 targs「_」自己人照砸），
#   风暴之锤射程写 700（数据 600），雷霆一击固定 300（数据 250/300/350 随等级），圣光术缺 250 才奶（治疗 200/400/600）。
#   现在按 war3-analytics/w3data/skills.json（make_skills.py 从 AbilityData.slk 生成、自检）取**当前等级**的数据；
#   等级来自已学技能列表（learned，10 秒一读），读不到按 1 级（最保守）。表读不到就退回原来的常数。
def _load_skills() -> dict:
    p = os.path.join(w3paths.ROOT, "data", "game", "skills.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


SKILLS = _load_skills()
AOE_OWN_MARGIN = 150.0     # 砸人的范围技能：落点半径外再留这么多 —— 单位会走动，贴着边的自己人下一跳就进圈了

# ★★ 09-22 血法师的烈焰风暴会伤到自己人：施放后圈里的自己人要主动躲开；同时落点要更准，尽量多罩敌人。
#   09-21 按游戏说明（「对敌方的地面单位造成伤害」）判它不伤自己人 —— **错了**。1 号实例 09-21~22 我方 313 次烈焰风暴，
#   战斗帧里以血法师施法订单 0x000D0208 结束（火落地）为 0 点，落点 200 内的单位落地后 1.2 墙钟秒比落地前 1.2 秒多掉的血：
#     我方 239 个·次 平均 +82（中位 +79）   敌方 301 个·次 平均 +54（中位 +28）   —— 烧自己人比烧敌人还多
#   另外两件：下令到火落地中位 1.7 墙钟秒（射程 + 200 内都算、英雄先走过去 + 1.33 游戏秒前摇）；
#   火落地时落点 200 内一个敌人都没有的 178/313（57%）—— 瞄的是「最大一团的重心」，1.7 秒后人早走了。
#   同一批实战走位回放（一次性分析脚本 flamestrike_aim.py）比挑点方法，火落地时 200 内敌人数：
#     现在（最大一团重心，可以先走 200）  1.18 个，0 个的 51%，我方 0.93 个
#     只瞄「过去 0.5 秒几乎没动」的敌人、射程内不走路   2.35 个，0 个的 7%，我方 1.10 个
#     按速度外推                                  1.92 个，0 个的 21%
#   所以：still=True 只数站桩的敌人（Motion），no_walk=True 落点必须在射程内；friendly_fire="dodge"：不因为圈里有自己人不放，
#   而是下完令让圈里（+FF_DODGE_PAD）的自己人往外躲 FF_DODGE_OUT、握到前摇 + 满伤害那几秒（技能数据 Cast + HeroDur，
#   躲技能 85 级，补攻击令 / 策略层拽不回来）。
STILL_WINDOW = 0.5         # 墙钟秒（3 倍速约 1.5 游戏秒）：看多久以前的位置
STILL_FRAC = 0.25          # 这段时间挪的距离 < 移速 × 时长 × 这个 = 站桩（在打人 / 被缠住）
STILL_AREA_SHRINK = 50.0   # 数站桩敌人时半径缩 50（落点 200 -> 150）：他们还会挪一点，罩在中间的才稳
FF_DODGE_PAD = 120.0       # 落点半径外再多躲这么远的自己人（会走进来的）
FF_DODGE_OUT = 180.0       # 躲到落点半径外这么远
FF_DODGE_OWN_W = 0.3       # 挑落点时每个会被烧的自己人扣 0.3 个敌人的分（要躲开的越少越好，但不为此放弃好落点）


class Motion:
    """敌方单位的位置历史（墙钟秒）：谁在过去 STILL_WINDOW 秒里几乎没动（站桩）。地址被回收（换了类型）重来。"""

    def __init__(self):
        self.hist = {}

    def update(self, units, now: float) -> None:
        seen = set()
        for u in units:
            d = self.hist.get(u.addr)
            if d is None or (d and d[-1][3] != u.type):
                d = self.hist[u.addr] = deque()
            d.append((now, u.x, u.y, u.type))
            while d and now - d[0][0] > 2.0 * STILL_WINDOW:
                d.popleft()
            seen.add(u.addr)
        for a in [a for a in self.hist if a not in seen]:
            del self.hist[a]

    def still(self, u, now: float) -> bool:
        d = self.hist.get(u.addr)
        if not d:
            return False
        old = next((p for p in d if now - p[0] <= STILL_WINDOW + 0.15), None)
        if old is None or now - old[0] < 0.6 * STILL_WINDOW:
            return False                      # 历史不够长，不知道
        age = now - old[0]
        spd = SPEED.get(u.type) or 270.0
        return math.hypot(u.x - old[1], u.y - old[2]) < STILL_FRAC * spd * GAME_SPEED * age


# ★★ 09-22 晚 血法师技能区域在持续期间应整体避开：已在圈里的撤出，移动路线也不能穿过，直到技能完全结束。
#   原来只在放出去那一下让圈里的人躲开、握到「前摇 + 满伤害 HeroDur 2.67」（约 1.6 墙钟秒）—— 可烈焰风暴一共烧 Dur 9 游戏秒，
#   后 6 秒照样掉血；握一放开，补攻击令 / 大脑就把人派回火里，路过的也没人管。
#   现在每一团火记成一个火圈（ff_zone：落点、半径、前摇 + 整个 Dur 之后才灭），火灭之前每一轮都看：
#   圈边（半径 + FF_DODGE_PAD）有自己人（施法者自己也算）就往圈外推、握到火灭（ff_fence_moves）——
#   放出去那一下在圈里的是「躲开」，之后走进来的是「路过绕开」（等在圈外，不从火里穿过去）。
FF_ZONE_TAIL = 0.2       # 火灭之后再多握这么久（墙钟秒）
FF_ZONE_FRESH = 0.4      # 火圈放出去这么久以内推出去的算「圈里躲开」，之后的算「路过绕开」


def ff_zone(cx: float, cy: float, sk: dict, caster, now: float) -> dict:
    """自己放的烈焰风暴 -> 火圈：落点、半径（Area）、灭的时刻（前摇 Cast + 整个持续 Dur，不只是满伤害那 HeroDur）、施法者当时的位置。"""
    until = now + ((sk.get("Cast") or 1.33) + (sk.get("Dur") or 9.0)) / GAME_SPEED + FF_ZONE_TAIL
    return dict(x=cx, y=cy, r=sk.get("Area") or 200.0, until=until, t0=now, fx=caster.x, fy=caster.y)


def ff_fence_moves(zone: dict, units, held: dict) -> list:
    """火圈还没灭：圈边（r + FF_DODGE_PAD）内、还没为这个圈推过的自己人（held[地址] < 灭的时刻）-> [(单位, tx, ty)]：
    沿「落点 -> 单位」往外推到半径外 FF_DODGE_OUT；正站在落点上的背离施法者走。施法者自己也推（烈焰风暴烧自己）。"""
    out = []
    for u in units:
        if u.hp <= 0 or held.get(u.addr, 0.0) >= zone["until"]:
            continue
        d = math.hypot(u.x - zone["x"], u.y - zone["y"])
        if d > zone["r"] + FF_DODGE_PAD:
            continue
        if d < 1.0:
            dx, dy = zone["x"] - zone["fx"], zone["y"] - zone["fy"]
            n = math.hypot(dx, dy) or 1.0
            ux, uy = dx / n, dy / n
        else:
            ux, uy = (u.x - zone["x"]) / d, (u.y - zone["y"]) / d
        r = zone["r"] + FF_DODGE_OUT
        out.append((u, zone["x"] + ux * r, zone["y"] + uy * r))
    return out
CHANNEL_HOLD_MAX = 16.0    # 09-18 引导技能的施法保护期上限（墙钟秒）：群星坠落 45 游戏秒 = 15 墙钟秒，再长就不像"保护"了


def _load_hero_primary() -> dict:
    """英雄类型 -> 主属性（STR/AGI/INT），w3data/heroes.json（make_items.py 生成）。法力燃烧/灵魂燃烧先挑智力英雄。"""
    p = os.path.join(w3paths.ROOT, "data", "game", "heroes.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return {k: v.get("primary") for k, v in json.load(fh).items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


HERO_PRIMARY = _load_hero_primary()


def skill(name: str, level: int | None) -> dict:
    """技能 name 在 level 级的数据行（Rng/Area/Cool/Cost/Dur/HeroDur/Cast/DataA..F）；没有返回 {}。"""
    levels = SKILLS.get(SPELL_ABIL.get(name, ""), {}).get("levels") or []
    if not levels:
        return {}
    return levels[max(1, min(len(levels), int(level or 1))) - 1]


def _opts(name: str) -> dict:
    return SPELLS.get(name, {}).get("opts", {})


def aoe_need(name: str, level: int | None) -> int:
    """范围技能至少要罩住几个敌人。参数在 SPELLS：need 固定人数；per=(字段, 倍数, 兜底) + value：估的每人伤害 × 人数 >= value。
    暴风雪每人按 3 波算（单位挨两三波就走出圈了）：1 级 30×3=90 -> 4 人，2 级 120 -> 3 人，3 级 150 -> 3 人；
    烈焰风暴按总伤上限 DataF：1 级 90 -> 3 人，2 级 160、3 级 220 -> 2 人。
    （09-18 从按技能名分支改成表参数；暴风雪原来是 DataB × min(3, DataA)，DataA 波数 6/8/10 恒 >= 3，所以就是 × 3。）"""
    o = _opts(name)
    if o.get("need"):
        return int(o["need"])
    if not o.get("per"):
        return 3
    fld, mult, default = o["per"]
    per = (skill(name, level).get(fld) or default) * mult
    return max(int(o.get("need_min", 2)), int(math.ceil(o.get("value", 300.0) / max(1.0, per))))


def cast_hold_seconds(name: str, level: int | None) -> float:
    """下完施法令后，补攻击令多久不碰这个英雄（墙钟秒）。
    暴风雪是**引导**：波数 DataA（6/8/10，约每游戏秒一波）内被补一条攻击令就断了 —— 原来一律 1.5 秒，1 级也要 2 秒。
    烈焰风暴有 1.33 游戏秒前摇。其它按原来的 CAST_HOLD。
    09-18：引导类（SPELLS 的 channel=(字段, 倍数, 兜底)：死亡凋零/群星坠落/宁静/地震/火焰雨/剑刃风暴）同一条算法，上限 CHANNEL_HOLD_MAX。"""
    sk = skill(name, level)
    o = _opts(name)
    if o.get("channel"):
        fld, mult, default = o["channel"]
        return min(CHANNEL_HOLD_MAX, max(CAST_HOLD, (sk.get(fld) or default) * mult / GAME_SPEED + 0.3))
    if o.get("precast"):
        return max(CAST_HOLD, (sk.get("Cast") or o["precast"]) / GAME_SPEED + 0.3)
    return CAST_HOLD


def spell_mana(name: str, level: int | None = None) -> float:
    """这一级要多少蓝：skills.json 的 Cost（睡眠 100/75/50、闪烁 50/10/10 随等级变）；读不到数据用表里的 mana=，再没有按 0（不挡）。"""
    sk = skill(name, level)
    if "Cost" in sk:
        return float(sk["Cost"])
    return float(_opts(name).get("mana", 0.0))


def spell_cd(name: str, level: int | None = None) -> float:
    """本模块自己的节流（墙钟秒）；真冷却下令前还会问引擎（技能的冷却标志，flags & 0x200）。
    人族沿用原常数 cd=；其余按数据 Cool / GAME_SPEED（至少 1 秒）；cd_from=(字段, 上限) 取 max(Cool, min(字段, 上限))。"""
    o = _opts(name)
    if "cd" in o:
        return float(o["cd"])
    sk = skill(name, level)
    cool = sk.get("Cool") or 0.0
    if o.get("cd_from"):
        fld, cap = o["cd_from"]
        cool = max(cool, min(sk.get(fld) or 0.0, cap))
    return max(1.0, cool / GAME_SPEED) if cool else 5.0


SPELL_MANA = {n: spell_mana(n, 1) for n in SPELLS}   # 派生：1 级耗蓝（人族三级同价，tests/test_hero_mana.py 对着 slk 核）
SPELL_CD = {n: spell_cd(n, 1) for n in SPELLS}       # 派生：1 级节流


class SpellCooldowns:
    """每个英雄每个技能下一次能放的时刻（墙钟）。"""

    def __init__(self):
        self.next = {}

    def ready(self, hero: int, name: str, now: float) -> bool:
        return self.next.get((hero, name), 0.0) <= now

    def cast(self, hero: int, name: str, now: float, level: int | None = None) -> None:
        self.next[(hero, name)] = now + spell_cd(name, level)

    def rejected(self, hero: int, name: str, now: float) -> None:
        self.next[(hero, name)] = now + REJECT_BACKOFF

    def hold(self, hero: int, name: str, until: float) -> None:
        """引擎说还在冷却：按引擎给的剩余时间顺延（只往后推，不提前）。"""
        self.next[(hero, name)] = max(self.next.get((hero, name), 0.0), until)


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _clump(hero, foes, allies, reach: float, radius: float, need: int, own_r: float | None = None, own_max: int = 1,
           must=None, own_w: float = 0.0):
    """reach 内找一团 >=need 个敌人（半径 radius），重心 own_r（默认 radius）内自己人 <=own_max。返回 (个数, 重心x, 重心y) 或 None。
    must：这一团里至少要有一个满足它的（沉默要罩住施法者/英雄）。
    own_w：挑哪一团时每个圈里的自己人扣这么多个敌人的分（烈焰风暴会让他们躲开，越少越好；0 = 原来的只比人数）。
    09-22：落点要**在射程内**（reach 已经扣过）—— 重心落到射程外就退回这一团里离英雄最近的那个敌人所在的位置（no_walk 的技能不走过去）。"""
    best = None
    own_r = radius if own_r is None else own_r
    for f in foes:
        if _dist(f, hero) > reach:
            continue
        grp = [g for g in foes if _dist(g, f) <= radius]
        if len(grp) < need:
            continue
        if must is not None and not any(must(g) for g in grp):
            continue
        cx = sum(g.x for g in grp) / len(grp)
        cy = sum(g.y for g in grp) / len(grp)
        if own_w and math.hypot(cx - hero.x, cy - hero.y) > reach:
            cx, cy = f.x, f.y
        own = sum(1 for a in allies
                  if a.addr != hero.addr and math.hypot(a.x - cx, a.y - cy) <= own_r)
        if own > own_max:
            continue
        score = len(grp) - own_w * own
        if best is None or score > best[3]:
            best = (len(grp), cx, cy, score)
    return best[:3] if best else None


def _is_unit(u) -> bool:
    """会动的单位（不是建筑）：占人口的、或召唤物、或英雄。范围技能和风暴之锤的目标只数这些。"""
    return u.is_hero or FOOD.get(u.type, 1.0) > 0.0 or u.type in SUMMONS


# ── 09-18 出手规则（SPELLS 的 rule 字段指向这里）─────────────────────────────
#   每条规则：(技能名, 表项, 英雄, 敌人, 自己人, 当前等级数据行, 等级, ctx) -> plan 或 None（不看冷却、不看蓝、不看学没学）。
#   plan = (技能名, 订单号, instant/point/target, 目标地址或 None, x, y[, meta])；
#   meta（可选 dict）：then=后续动作、then_delay、autocast、toggle=True/False、no_mana（不按蓝挡）。
#   ctx（主循环给）：pulled 本模块正在拉它撤 / mana / home / foe_buildings 敌方建筑 / corpses [(x,y)] /
#                    autocast {技能名: 已开的等级} / toggles 认为开着的开关 / toggle_stale 开着但太久没确认的。
# 目标过滤用的三张小表（游戏数据里没有直接的"机械/魔免"字段，按兵种手记；漏了只是被引擎退一次 + 10 秒退避）
MAGIC_IMMUNE = {"edry", "ubsp", "hspt"}          # 树妖、毁灭者、破法者：英雄技能点不上
MECHANICAL = {"hgyr", "hmtt", "hrtt", "ocat", "umtw", "ebal", "nzep", "nfac", "ncg1", "ncg2", "ncg3", "ncgb"}
NOT_SACRIFICE = {"uloc"}                          # 蝗虫无敌，点不上
ENGAGED_R = 250.0                                 # 睡眠：自己人离目标这么近 = 正在打它（挨打就醒），不睡
ESCAPE_ON = 400.0                                 # 闪烁逃生之后沿同一方向再走这么远
CHASE_CROWD = 2                                   # 追残血英雄：它身边 600 内敌方玩家作战单位多于这么多就不追（别闪进人堆）


def _frac(u) -> float:
    return u.hp / max(1.0, float(u.hp_max or u.hp))


def _is_undead(u) -> bool:
    return bool(u.type) and u.type[0] in "uU"


def _target_ok(f, o) -> bool:
    """单体技能能不能点它：魔免一律不行；organic 不点机械；ground 不点空军；living 不点亡灵（死亡缠绕会把它奶满）。"""
    if f.type in MAGIC_IMMUNE:
        return False
    if o.get("organic") and f.type in MECHANICAL:
        return False
    if o.get("ground") and f.type in AIR_TYPES:
        return False
    if o.get("living") and _is_undead(f):
        return False
    return True


def _players(foes, o):
    """players=True：只数敌方玩家的单位 —— 大招（冷却 90~180 游戏秒）别砸在野怪身上。"""
    return [f for f in foes if f.owner != CREEP_OWNER] if o.get("players") else foes


_MUST = {"caster_or_hero": lambda f: f.is_hero or f.type in HEALERS or f.type in CASTERS}


def _instant(name, sp, hero, **meta):
    p = (name, sp["order"], "instant", None, hero.x, hero.y)
    return p + (meta,) if meta else p


def _point(name, sp, x, y, **meta):
    p = (name, sp["order"], "point", None, x, y)
    return p + (meta,) if meta else p


def _target(name, sp, t, **meta):
    p = (name, sp["order"], "target", t.addr, t.x, t.y)
    return p + (meta,) if meta else p


def _rule_summon(name, sp, hero, foes, allies, sk, level, ctx):
    """召唤：水元素 / 野狼 / 熊 / 豪猪 / 战鹰（瞬发），毒蛇守卫 / 口袋工厂（点：插在英雄和最近敌人之间 ahead 远）。
    09-17 大法师应优先让水元素承受伤害 —— 先召出来、让它先接敌（护卫见 guard_picks）。
      原来只在敌人进 900 才召：打野时英雄在营地外 900 压阵，水元素要等英雄自己靠上去才出来（inst1 两局开打后 +4~+8 秒才召）。
    reach（默认 900）内有敌人就召；early（默认开）：敌方玩家单位 1200 内、或野怪 1400 内且身边 1200 内有 >=2 个自己的兵也召。"""
    o = sp["opts"]
    near = lambda r: [f for f in foes if _dist(f, hero) <= r]  # noqa: E731
    ok = bool(near(o.get("reach", 900.0)))
    if not ok and o.get("early", True):
        early_foe = [f for f in near(WATER_EARLY_FOE_R) if f.owner != CREEP_OWNER]
        creep_close = [f for f in near(WATER_EARLY_CREEP_R) if f.owner == CREEP_OWNER]
        escort = [a for a in allies if a.addr != hero.addr and a.hp > 0 and not a.is_hero and a.type not in WORKERS
                  and FOOD.get(a.type, 0.0) > 0.0 and _dist(a, hero) <= 1200.0]
        ok = bool(early_foe or (creep_close and len(escort) >= 2))
    if not ok:
        return None
    if sp["how"] != "point":
        return _instant(name, sp, hero)
    f = min(foes, key=lambda g: _dist(g, hero))
    d = _dist(f, hero)
    if d < 1.0:
        return _point(name, sp, hero.x, hero.y)
    step = min(o.get("ahead", 300.0), max(0.0, d - 150.0))
    return _point(name, sp, hero.x + (f.x - hero.x) / d * step, hero.y + (f.y - hero.y) / d * step)


def _rule_aoe_point(name, sp, hero, foes, allies, sk, level, ctx):
    """点范围：暴风雪 / 烈焰风暴 / 地震 / 死亡凋零 / 地狱火 / 火焰雨 / 沉默 / 火箭群。落点 = 射程内最大那一团的重心。
    09-16 晚：暴风雪砸自己人 —— 落点半径 + 150 内**一个自己人都不许有**（⚠ 当时写的「烈焰风暴也砸」是错的，09-21 按游戏说明更正，见 SPELLS 那行）
      （原来允许 1 个）。半径按数据 200（原来暴风雪 250）；要罩住几个按等级算（aoe_need）；烈焰风暴只打地面。
    09-18：friendly_fire 的才查自己人（targs 里没写 enemy 不等于砸自己人 —— 雷霆一击 targs 也没写 enemy）；
      buildings=N：打不成兵团时退一步找 >=N 座敌方建筑的一片（地震、死亡凋零拆家）。"""
    o = sp["opts"]
    area = sk.get("Area") or o.get("area_default", 200.0)
    # no_walk：落点必须在射程内（不先走过去 —— 走的那一两秒敌人就散了，见 STILL_WINDOW 上面那段）
    reach = (sk.get("Rng") or o.get("rng", 800.0)) + (-50.0 if o.get("no_walk") else o.get("pad", 200.0))
    dodge = o.get("friendly_fire") == "dodge"
    own_r, own_max = ((area + AOE_OWN_MARGIN, 0) if o.get("friendly_fire") and not dodge else
                      (area + FF_DODGE_PAD, 1 << 30))
    pool = [f for f in _players(foes, o) if _is_unit(f) and (o.get("air", True) or f.type not in AIR_TYPES)]
    radius = area
    still = ctx.get("still")
    if o.get("still") and still is not None:
        pool = [f for f in pool if f.addr in still]       # 只瞄站桩的（在打人 / 被缠住），1.5 秒后还在圈里
        radius = area - STILL_AREA_SHRINK
    c = _clump(hero, pool, allies, reach, radius, aoe_need(name, level), own_r=own_r, own_max=own_max,
               must=_MUST.get(o.get("must")), own_w=FF_DODGE_OWN_W if dodge else 0.0)
    if c is None and o.get("buildings"):
        c = _clump(hero, ctx.get("foe_buildings") or [], allies, reach, o.get("bld_r", area), int(o["buildings"]),
                   own_r=own_r, own_max=own_max)
    return _point(name, sp, c[1], c[2]) if c else None


def _rule_self_aoe(name, sp, hero, foes, allies, sk, level, ctx):
    """以自己为圆心：雷霆一击 / 战争践踏 / 刀扇 / 恐怖嚎叫 / 剑刃风暴 / 蝗虫群 / 群星坠落。
    09-16 晚（雷霆一击）：半径随等级 250/300/350（原来固定 300），只打地面；罩住 2 个地面单位、或者 1 个敌方英雄就放（减速 50%）。
    09-18：need/per 定人数（aoe_need），hero_need：圈里有敌方英雄时这么多就够；min_frac 自己血太少不放（剑刃风暴、群星坠落）；
      max_close + close_r：贴身的敌人多于这么多不放（群星坠落要引导 15 墙钟秒，被砍就断）。"""
    o = sp["opts"]
    if o.get("min_frac") and _frac(hero) < o["min_frac"]:
        return None
    area = o["area"] if "area" in o else (sk.get("Area") or o.get("area_default", 250.0))
    area += o.get("pad", 0.0)
    pool = [f for f in _players(foes, o) if _dist(f, hero) <= area and _is_unit(f)
            and (o.get("air") or f.type not in AIR_TYPES)]
    if "max_close" in o and sum(1 for f in pool if _dist(f, hero) <= o.get("close_r", 300.0)) > o["max_close"]:
        return None
    hn = o.get("hero_need")
    if len(pool) >= aoe_need(name, level) or (hn and len(pool) >= hn and any(f.is_hero for f in pool)):
        return _instant(name, sp, hero)
    return None


def _rule_self_save(name, sp, hero, foes, allies, sk, level, ctx):
    """自保 / 大仗开大：神圣护甲（血 <35% 且 600 内有敌人）/ 天神下凡（血 <50% 且 600 内 >=4 个）/ 镜像 / 魔法护盾 / 变身 /
    复仇天神 / 化学狂暴。frac + r + n：血比例低于 frac 且 r 内敌人 >= n；or_n + or_r：不看血，or_r 内敌人 >= or_n（大仗）。"""
    o = sp["opts"]
    pool = _players(foes, o)
    count = lambda r: sum(1 for f in pool if _dist(f, hero) <= r)  # noqa: E731
    close = count(o.get("r", 600.0))
    focused = int(ctx.get("focus_counts", {}).get(hero.addr, 0))
    recent_hit = hero.addr in ctx.get("recent_hurt", ())
    due = (_frac(hero) < o.get("frac", 0.0)
           or (focused >= o.get("focus_n", 999) and _frac(hero) < o.get("focus_frac", 0.0))
           or (recent_hit and _frac(hero) < o.get("hit_frac", 0.0))
           or (ctx.get("pulled") and _frac(hero) < o.get("pulled_frac", 0.0)))
    if due and close >= o.get("n", 1):
        return _instant(name, sp, hero)
    if o.get("or_n") and count(o.get("or_r", 900.0)) >= o["or_n"]:
        return _instant(name, sp, hero)
    return None


def _away(hero, foes, home):
    """背离这些敌人的重心的单位向量；重心贴着自己（< 50）就朝家；都不行 None。"""
    if foes:
        cx = sum(f.x for f in foes) / len(foes)
        cy = sum(f.y for f in foes) / len(foes)
        dx, dy = hero.x - cx, hero.y - cy
        n = math.hypot(dx, dy)
        if n >= 50.0:
            return dx / n, dy / n
    if home is not None:
        dx, dy = home[0] - hero.x, home[1] - hero.y
        n = math.hypot(dx, dy)
        if n >= 1.0:
            return dx / n, dy / n
    return None


def _rule_escape(name, sp, hero, foes, allies, sk, level, ctx):
    """逃生 / 追击：疾风步（瞬发，不打断正在走的路）、闪烁（点）。
    09-18 与大脑层约定：剑圣疾风步用于残血逃跑（英雄血 <35% 且附近有敌人就放，和大脑的回家令同向）。
      逃：本模块正在拉它撤（ctx pulled）且 700 内有敌人，或血 <35% 且 600 内有敌人。
          疾风步直接放（隐身后原来的回家令照走）；闪烁往背离敌人的方向闪 min(最远-50, 900)，then 接着走 ESCAPE_ON。
      追（没在撤、自己血 >= 50%）：敌方英雄血 <= 30%、离 300~900（闪烁 450~最远-50），它身边敌兵不多于 CHASE_CROWD
          -> 疾风步 / 闪烁到它身边，then 攻击它。"""
    o = sp["opts"]
    point = sp["how"] == "point"
    reach = (sk.get("DataA") or 1000.0) - 50.0
    near = [f for f in foes if f.hp > 0 and _is_unit(f)]
    close = [f for f in near if _dist(f, hero) <= (700.0 if ctx.get("pulled") else 600.0)]
    if close and (ctx.get("pulled") or _frac(hero) < o.get("frac", 0.35)):
        if not point:
            return _instant(name, sp, hero)
        u = _away(hero, close, ctx.get("home"))
        if u is None:
            return None
        d = min(reach, 900.0)
        bx, by = hero.x + u[0] * d, hero.y + u[1] * d
        return _point(name, sp, bx, by, then=("move", bx + u[0] * ESCAPE_ON, by + u[1] * ESCAPE_ON), then_delay=0.5)
    if ctx.get("pulled") or _frac(hero) < 0.5:
        return None
    lo, hi = (450.0, reach) if point else (300.0, 900.0)
    prey = [f for f in near if f.is_hero and f.owner != CREEP_OWNER and _frac(f) <= 0.3 and lo <= _dist(f, hero) <= hi
            and sum(1 for g in near if g.owner != CREEP_OWNER and not g.is_hero and _dist(g, f) <= 600.0) <= CHASE_CROWD]
    if not prey:
        return _opener(name, sp, hero, near, allies, sk, ctx) if o.get("opener") and not point else None
    t = min(prey, key=_frac)
    then = ("attack", t.addr)
    if not point:
        return _instant(name, sp, hero, then=then, then_delay=0.2)
    d = _dist(t, hero)
    return _point(name, sp, t.x - (t.x - hero.x) / d * 100.0, t.y - (t.y - hero.y) / d * 100.0, then=then, then_delay=0.5)


OPENER_R = (250.0, 800.0)                         # 疾风步开场：目标离英雄这个范围（贴身了就不隐身，直接砍）
OPENER_HP = 0.6                                   # 自己血 >= 60% 才拿它开场
OPENER_ALLY_R = 700.0                             # 打野怪要自己的兵已经在目标 700 内（仗开了），打玩家单位不要求


def _opener(name, sp, hero, near, allies, sk, ctx):
    """疾风步开场（09-18 实战发现剑圣几乎不放技能）：原来只在残血逃跑 / 追残血英雄时放，满血打野打架一次不放，
    而剑圣早期只学了疾风步 + 致命一击（被动）。破隐那一刀多打 DataC（40/70/100）。
    蓝 >= 2 次耗蓝才放 —— 留一次逃跑的蓝；蓝读不到（ctx mana None）不放。
    目标：敌方英雄（血比例最低）> 施法者/治疗 > 血上限最高的；只打地面；野怪要自己的兵已经贴上去（别一个人隐身冲营地）。"""
    mana = ctx.get("mana")
    cost = sk.get("Cost") or 75.0
    if mana is None or mana < 2.0 * cost or _frac(hero) < OPENER_HP:
        return None
    lo, hi = OPENER_R
    army = [a for a in allies if a.addr != hero.addr and a.hp > 0 and not a.is_hero and a.type not in WORKERS]
    pool = [f for f in near if lo <= _dist(f, hero) <= hi and f.type not in AIR_TYPES
            and (f.owner != CREEP_OWNER or any(_dist(a, f) <= OPENER_ALLY_R for a in army))]
    if not pool or any(_dist(f, hero) < lo for f in near):
        return None
    heroes = [f for f in pool if f.is_hero]
    casters = [f for f in pool if f.type in HEALERS or f.type in CASTERS]
    t = min(heroes, key=_frac) if heroes else (casters[0] if casters else max(pool, key=lambda f: f.hp_max))
    return _instant(name, sp, hero, then=("attack", t.addr), then_delay=0.2)


def _rule_nuke(name, sp, hero, foes, allies, sk, level, ctx):
    """单体伤害/眩晕：风暴之锤 / 闪电链 / 霜冻新星 / 暗影突袭 / 叉状闪电 / 死亡缠绕（打的那一半）。
    09-16 晚（风暴之锤）：射程按数据 600（+100 走两步；原来写 700），伤害 100/225/350 —— 一下能打死的优先：
      能打死的英雄 > 能打死的施法者/治疗 > 敌方英雄（血比例最低，眩晕 3 秒）> 能打死的其它单位（挑血上限高的）> 施法者/治疗
    09-18 参数：dmg 字段（默认 DataA）× dmg_mult + dmg_add；splash_r/splash_need：在"敌方英雄"之后插一档
      "身边 splash_r 内敌人最多的那个（>= splash_need）"（闪电链弹跳、霜冻新星溅射）；lone_caster=False 不为一个施法者浪费。"""
    o = sp["opts"]
    dmg = (sk.get(o.get("dmg", "DataA")) or o.get("dmg_default", 100.0)) * o.get("dmg_mult", 1.0)
    for fld, k in o.get("dmg_add", ()):
        dmg += (sk.get(fld) or 0.0) * k
    rng = (sk.get("Rng") or o.get("rng", 600.0)) + o.get("pad", 100.0)
    inr = [f for f in foes if _dist(f, hero) <= rng and f.hp > 0 and _is_unit(f) and _target_ok(f, o)]
    kill = [f for f in inr if f.hp <= dmg]
    caster = lambda f: f.type in HEALERS or f.type in CASTERS  # noqa: E731
    lowest = lambda f: f.hp / max(1, f.hp_max)  # noqa: E731
    pools = [([f for f in kill if f.is_hero], None), ([f for f in kill if caster(f)], None),
             ([f for f in inr if f.is_hero], lowest)]
    if o.get("splash_r"):
        live = [g for g in foes if g.hp > 0 and _is_unit(g)]
        dense = [(sum(1 for g in live if _dist(g, f) <= o["splash_r"]), f) for f in inr]
        dense = [(n, f) for n, f in dense if n >= o.get("splash_need", 3)]
        if dense:
            top = max(n for n, _f in dense)
            pools.append(([f for n, f in dense if n == top], lambda f: -f.hp_max))
    pools.append((kill, lambda f: -f.hp_max))
    if o.get("lone_caster", True):
        pools.append(([f for f in inr if caster(f)], lowest))
    for pool, key in pools:
        if pool:
            return _target(name, sp, min(pool, key=key) if key else pool[0])
    return None


def _rule_siphon(name, sp, hero, foes, allies, sk, level, ctx):
    """法力虹吸（血法师）：自己的蓝低于 below（放不了烈焰风暴）时，从射程内有蓝的敌人身上吸。
    目标：敌方英雄（智力英雄先，蓝多）> 施法者/治疗（萨满、牧师、女巫……）；野怪和没蓝的兵不吸。
    读不到自己的蓝（mana 为 None）就不放 —— 不知道缺不缺，别拿 6 秒引导去换。"""
    o = sp["opts"]
    mana = ctx.get("mana")
    if mana is None or mana >= o.get("below", 135.0):
        return None
    rng = (sk.get("Rng") or o.get("rng", 600.0)) + o.get("pad", 100.0)
    inr = [f for f in foes if _dist(f, hero) <= rng and f.hp > 0 and f.owner != CREEP_OWNER
           and _is_unit(f) and _target_ok(f, o)]
    heroes = [f for f in inr if f.is_hero]
    if heroes:
        return _target(name, sp, min(heroes, key=lambda f: (0 if HERO_PRIMARY.get(f.type) == "INT" else 1, _dist(f, hero))))
    casters = [f for f in inr if f.type in HEALERS or f.type in CASTERS]
    return _target(name, sp, min(casters, key=lambda f: _dist(f, hero))) if casters else None


def _rule_disable(name, sp, hero, foes, allies, sk, level, ctx):
    """控制：妖术 / 睡眠 / 纠缠根须 / 醉酒云雾 / 灵魂燃烧 / 法力燃烧 / 酸性炸弹。
    敌方英雄（prefer_int 先挑智力英雄，再挑血比例最低的）> 施法者/治疗 > 血上限 >= min_hp 的大块头（挑最大的）。
    unengaged（睡眠）：自己人离目标 ENGAGED_R 内 = 正在打它，一挨打就醒，不睡。"""
    o = sp["opts"]
    rng = (sk.get("Rng") or o.get("rng", 800.0)) + o.get("pad", 100.0)
    inr = [f for f in foes if _dist(f, hero) <= rng and f.hp > 0 and _is_unit(f) and _target_ok(f, o)]
    if o.get("unengaged"):
        inr = [f for f in inr if not any(a.addr != hero.addr and a.hp > 0 and _is_unit(a) and _dist(a, f) <= ENGAGED_R
                                         for a in allies)]
    heroes = [f for f in inr if f.is_hero]
    if heroes:
        pick = min(heroes, key=lambda f: (0 if o.get("prefer_int") and HERO_PRIMARY.get(f.type) == "INT" else 1, _frac(f)))
        return _target(name, sp, pick)
    casters = [f for f in inr if f.type in HEALERS or f.type in CASTERS]
    if casters:
        return _target(name, sp, min(casters, key=_frac))
    big = [f for f in inr if o.get("min_hp") and f.hp_max >= o["min_hp"]]
    return _target(name, sp, max(big, key=lambda f: f.hp_max)) if big else None


def _rule_heal_ally(name, sp, hero, foes, allies, sk, level, ctx):
    """奶一个自己人：圣光术 / 治疗波 / 死亡缠绕（奶的那一半，只奶亡灵）。
    09-16 053644：圣光术不能对自己放 —— 施法日志 11 次里 10 次「Hpal holylight -> 自己」，
      上下文路被引擎拒、退到动作 28「接了」却没进冷却，2 秒后又对自己放（一分钟 27 次）；治疗波可以（self_ok）。
    09-16 晚：按治疗量挑 —— 缺的血 >= 治疗量 60%（别奶一个只缺 50 的，也别让 3 级 600 的奶去补 250 的口子），
      射程 + 200（走两步）；血 <= 75% 的里面挑血比例最低的。只奶占人口的单位（不奶建筑、不奶召唤物）。"""
    o = sp["opts"]
    heal = sk.get(o.get("heal", "DataA")) or o.get("heal_default", 200.0)
    reach = (sk.get("Rng") or o.get("rng", 800.0)) + 200.0
    focus = ctx.get("focus_counts", {})
    recent = ctx.get("recent_hurt", ())
    hurt = [a for a in allies
            if (a.addr != hero.addr or o.get("self_ok")) and a.hp > 0 and FOOD.get(a.type, 0.0) > 0.0
            and _dist(a, hero) <= reach
            and ((a.hp <= 0.75 * max(1, a.hp_max) and (a.hp_max - a.hp) >= 0.6 * heal)
                 or ((focus.get(a.addr, 0) > 0 or a.addr in recent)
                     and a.hp <= 0.85 * max(1, a.hp_max) and (a.hp_max - a.hp) >= 0.35 * heal))
            and not (o.get("organic") and a.type in MECHANICAL) and (not o.get("undead_only") or _is_undead(a))]
    if hurt:
        # 正在被集火/刚掉血的英雄优先，其次才按血量比例；圣光落在持续受击者身上才来得及。
        return _target(name, sp, min(hurt, key=lambda a: (-focus.get(a.addr, 0),
                                                           0 if a.addr in recent else 1,
                                                           0 if a.is_hero else 1,
                                                           a.hp / max(1, a.hp_max))))
    return None


def _rule_heal_or_nuke(name, sp, hero, foes, allies, sk, level, ctx):
    """死亡缠绕：先奶自己的亡灵（不能对自己），没人要奶再打活的敌人（伤害 = 治疗量一半，打不了亡灵和机械）。"""
    return _rule_heal_ally(name, sp, hero, foes, allies, sk, level, ctx) or _rule_nuke(name, sp, hero, foes, allies, sk,
                                                                                       level, ctx)


def _rule_team_save(name, sp, hero, foes, allies, sk, level, ctx):
    """巫毒（全体无敌）：半径内自己的作战单位 >= own_n、敌方玩家单位 900 内 >= foe_n，
    且（平均血比例 <= avg_frac 或 有英雄 <= hero_frac）。"""
    o = sp["opts"]
    area = sk.get("Area") or o.get("area_default", 800.0)
    own = [a for a in allies if a.hp > 0 and FOOD.get(a.type, 0.0) > 0.0 and a.type not in WORKERS and _dist(a, hero) <= area]
    if len(own) < o.get("own_n", 4) or sum(1 for f in _players(foes, o) if _dist(f, hero) <= 900.0) < o.get("foe_n", 5):
        return None
    if (sum(_frac(a) for a in own) / len(own) <= o.get("avg_frac", 0.6)
            or any(a.is_hero and _frac(a) <= o.get("hero_frac", 0.35) for a in own)):
        return _instant(name, sp, hero)
    return None


def _rule_team_heal(name, sp, hero, foes, allies, sk, level, ctx):
    """宁静（引导群奶）：半径内血 <= 70% 的自己人 >= need、合计缺血 >= missing，而且 safe_r 内没有敌人（引导会被打断）。"""
    o = sp["opts"]
    area = sk.get("Area") or 900.0
    hurt = [a for a in allies if a.hp > 0 and FOOD.get(a.type, 0.0) > 0.0 and a.type not in WORKERS
            and _dist(a, hero) <= area and _frac(a) <= 0.7]
    if len(hurt) < o.get("need", 4) or sum(a.hp_max - a.hp for a in hurt) < o.get("missing", 1000.0):
        return None
    if any(_dist(f, hero) <= o.get("safe_r", 450.0) for f in foes):
        return None
    return _instant(name, sp, hero)


def _rule_corpses(name, sp, hero, foes, allies, sk, level, ctx):
    """操纵死尸：半径内新鲜尸体（主循环 corpse_events 记的）>= need，且 1000 内敌人 >= foe_n（仗还在打）。"""
    o = sp["opts"]
    area = sk.get("Area") or 900.0
    n = sum(1 for x, y in ctx.get("corpses") or () if math.hypot(x - hero.x, y - hero.y) <= area)
    if n >= o.get("need", 4) and sum(1 for f in _players(foes, o) if _dist(f, hero) <= 1000.0) >= o.get("foe_n", 4):
        return _instant(name, sp, hero)
    return None


def _rule_sacrifice(name, sp, hero, foes, allies, sk, level, ctx):
    """献祭自己人：死亡契约（mode=hp：英雄血 <= frac 且缺 >= missing）/ 黑暗仪式（mode=mana：蓝 < mana_below、1400 内有敌人）。
    只吃召唤物（骷髅/甲虫）或快死的（血 <= 30%）非英雄非工人单位；召唤物优先、血少的优先。读不到蓝不献祭。"""
    o = sp["opts"]
    if o.get("mode") == "mana":
        m = ctx.get("mana")
        if m is None or m >= o.get("mana_below", 125.0) or not any(_dist(f, hero) <= 1400.0 for f in foes):
            return None
    elif _frac(hero) > o.get("frac", 0.45) or (hero.hp_max - hero.hp) < o.get("missing", 250.0):
        return None
    reach = (sk.get("Rng") or 800.0) + 100.0
    cand = [a for a in allies if a.addr != hero.addr and a.hp > 0 and not a.is_hero and a.type not in WORKERS
            and a.type not in NOT_SACRIFICE and _dist(a, hero) <= reach
            and (a.type in SUMMONS or (FOOD.get(a.type, 0.0) > 0.0 and _frac(a) <= o.get("fodder_frac", 0.3)))]
    if not cand:
        return None
    return _target(name, sp, min(cand, key=lambda a: (0 if a.type in SUMMONS else 1, a.hp)))


def _rule_autocast(name, sp, hero, foes, allies, sk, level, ctx):
    """开自动施法（霜冻护甲 / 灼热之箭 / 腐尸甲虫 / 黑暗之箭）：学了就开，升级后再开一次；开令本身不耗蓝。
    谁身上放、什么时候放交给引擎自己（它挑正在挨打的自己人 / 每一箭都带），比我们 100ms 猜得准。
    只在读得到已学等级时才开（level 为 None 不开：没学时下令只会被退）。"""
    if not level:
        return None
    done = (ctx.get("autocast") or {}).get(name)
    if done is not None and done >= level:
        return None
    return _instant(name, sp, hero, autocast=True, no_mana=True)


def _rule_toggle(name, sp, hero, foes, allies, sk, level, ctx):
    """开关（献祭）：贴身（半径 + pad）地面敌人 >= need 或有敌方英雄 -> 开（蓝 >= min_mana）；off_r 内没有敌人 -> 关（省蓝）。
    开着的状态是本模块下令成功记下的（ctx toggles）；引擎没蓝会自己关，所以开着但超过 TOGGLE_TRUST 没确认的（toggle_stale）
    在仗里再下一次开令（开令对已开的不起作用）。"""
    o = sp["opts"]
    on = name in (ctx.get("toggles") or ())
    if on and not any(_dist(f, hero) <= o.get("off_r", 900.0) for f in foes):
        return (name, o["off"], "instant", None, hero.x, hero.y, {"toggle": False, "no_mana": True})
    if on and name not in (ctx.get("toggle_stale") or ()):
        return None
    area = (sk.get("Area") or o.get("area_default", 160.0)) + o.get("pad", 90.0)
    close = [f for f in foes if _dist(f, hero) <= area and _is_unit(f) and f.type not in AIR_TYPES]
    if len(close) >= o.get("need", 2) or any(f.is_hero for f in close):
        return _instant(name, sp, hero, toggle=True)
    return None


def _rule_line(name, sp, hero, foes, allies, sk, level, ctx):
    """直线：震荡波 / 腐臭蜂群 / 穿刺 / 火焰呼吸。以每个射程内的敌人为方向，数这条线段（长 length、半宽 half）上有几个敌人，
    敌方英雄算两个；取最多的方向，够 aoe_need 就朝那个敌人放（落点不超过射程）。按"敌我分得开"处理（这几个 targs 都没写 friend）。"""
    o = sp["opts"]
    rng = sk.get("Rng") or o.get("rng", 700.0)
    lf, ld = o.get("length", ("Rng", 700.0))
    length = sk.get(lf) or ld
    hf, hm, hd = o.get("half", ("Area", 1.0, 125.0))
    half = (sk.get(hf) or hd) * hm
    pool = [f for f in foes if f.hp > 0 and _is_unit(f) and f.type not in MAGIC_IMMUNE
            and (o.get("air") or f.type not in AIR_TYPES)]
    need = aoe_need(name, level)
    best = None
    for f in pool:
        d = _dist(f, hero)
        if d < 1.0 or d > rng + o.get("pad", 100.0):
            continue
        ux, uy = (f.x - hero.x) / d, (f.y - hero.y) / d
        score = 0
        for g in pool:
            px, py = g.x - hero.x, g.y - hero.y
            t = px * ux + py * uy
            if 0.0 <= t <= length and abs(px * uy - py * ux) <= half:
                score += 2 if g.is_hero else 1
        if score >= need and (best is None or score > best[0]):
            best = (score, ux, uy, min(d, rng))
    if best is None:
        return None
    _s, ux, uy, d = best
    return _point(name, sp, hero.x + ux * d, hero.y + uy * d)


_RULES = {
    "summon": _rule_summon, "aoe_point": _rule_aoe_point, "self_aoe": _rule_self_aoe, "self_save": _rule_self_save,
    "escape": _rule_escape, "nuke": _rule_nuke, "disable": _rule_disable, "heal_ally": _rule_heal_ally,
    "heal_or_nuke": _rule_heal_or_nuke, "team_save": _rule_team_save, "team_heal": _rule_team_heal,
    "corpses": _rule_corpses, "sacrifice": _rule_sacrifice, "autocast": _rule_autocast, "toggle": _rule_toggle,
    "line": _rule_line, "siphon": _rule_siphon,
}


def _spell_plan(name, hero, foes, allies, level=None, ctx=None):
    """单个技能此刻该不该放、放在哪；返回 plan 元组或 None（不看冷却、不看蓝）。level：这个技能当前等级（None = 按 1 级）。
    09-18：按 SPELLS[name]["rule"] 分派到上面的规则函数（原来是按技能名的一长串 elif）。"""
    sp = SPELLS.get(name)
    rule = _RULES.get(sp["rule"]) if sp else None
    if rule is None:
        return None
    return rule(name, sp, hero, foes, allies, skill(name, level), level, ctx or {})


HERO_SAFETY_VERSION = "creep-output-retreat-v14"


def creep_output_safe(hero, foes, ours, targets):
    """已确认野怪正在打别人时，健康英雄继续输出；距离覆盖不等于正在被集火。"""
    if hero.hp < .6*max(1,hero.hp_max) or not foes or not targets:
        return False
    tanks = {_pair(u):u for u in ours if u.hp>0 and u.addr!=hero.addr}
    return all(f.owner==CREEP_OWNER and targets.get(f.addr) in tanks
               and _dist(f,tanks[targets[f.addr]])<=RANGE.get(f.type,100)+250 for f in foes)


TEAM_MIN_FRAC = 0.20     # 带伤但还在打的自己人（血高于这么多）算「英雄不是一个人」（hero_exposure，4-036）
CREEP_HOLD_FRAC = 0.60   # 打野时英雄血高于这么多、自己人正赶过来，就先顶住，不按孤军撤（hero_exposure，1-284）
CREEP_COMING_R = 1200.0  # 「正赶过来」：离这批野怪这么近的自己人（步兵 3 秒路程左右）
CREEP_TANK_FRAC = 0.55   # 09-23 打野时英雄血高于这么多就顶着打，不按孤军 / 大群贴脸 / 护卫不足拉（hero_exposure，1-418）


# ★★ 09-22 1-388 17 分钟敌军打家：英雄全躲在远处不输出，而敌军并没有在打英雄。应在保命前提下尽量多输出：
#   挨打了再撤，没挨打就继续上。
#   逐秒回放（战斗帧 + mod_fight 目标采样，一次性分析脚本 hero_defense_track.py）：
#     20:38:07 血法 313/600「实际护卫不足」脱离战场 10 秒 —— 那几秒没有一个敌人以它为目标；
#     20:39:55~20:40:19 对面 22~24 人打进家，大法师 825/825 满血、全程没人打它，却每 7 秒一次「英雄预警撤退」，
#       离最近的敌人 2000~4400、离家 3000~4000，一直到认输。
#   改成「打了再撤、不打就回去」：
#     ① 暴露类理由（大群贴脸 / 孤军 / 护卫不足）只在英雄确实在挨打（under_fire）时才拉；残血那条、被集火那条照旧；野怪不动
#     ② 撤出来之后没在挨打，血 >= HERO_RETURN_CALM_FRAC 就放回去（原来一律等回到 HERO_RETURN_FRAC）
#     ③ 守家（home_fight）血 >= HOME_HERO_HOLD_FRAC 不做接触前预警撤退
HIT_WIN = 1.2            # 墙钟秒（3 倍速约 3.6 游戏秒）：看这么久以内掉没掉血
HIT_LOSS_FRAC = 0.03     # 掉了最大血量的这么多 = 在挨打（光环 / 自然回血的抖动远小于它）
HIT_FOCUS_AGE = 1.0      # 这么久以内读到有敌人以它为目标（focus_last）也算
HERO_RETURN_FRAC = 0.75
HERO_RETURN_CALM_FRAC = 0.55   # 比残血线 0.45 高一截：回去挨两下不至于立刻又被拉
HOME_HERO_HOLD_FRAC = 0.5
CALM_HOLD_MAX_FOES = 5     # 09-22 晚：身边敌人这么多就不等挨打（calm_hold 那段，1-411 / 1-393 两次大法师阵亡）


def under_fire(hist, hp_now, hp_max, now, focus_k=0, focus_age=1e9) -> bool:
    """英雄是不是正在挨打：HIT_WIN 秒内血从最高点掉了 >= 最大血量 × HIT_LOSS_FRAC，或 HIT_FOCUS_AGE 秒内读到有敌人以它为目标。
    hist：[(时刻, 血量)]（主循环的 hp_win）。"""
    if focus_k and focus_age <= HIT_FOCUS_AGE:
        return True
    peak = max((hp for t, hp in (hist or ()) if now - t <= HIT_WIN), default=hp_now)
    return peak - hp_now >= HIT_LOSS_FRAC * max(1.0, hp_max)


def calm_hold(exposure, fire) -> bool:
    """这条暴露理由要不要因为「没在挨打」先不拉（见 HIT_WIN 上面那段）。fire=None（不知道）照旧拉。
    ★★ 09-22 晚 1-411 大法师在明显打不过的一堆敌军里硬顶到阵亡、白送经验，属于必须杜绝的严重缺陷。
      23:05:2x 部队撤走、大法师被睡眠定住后单独留在 8~13 个敌人中间，这条挡了 3 次「孤军且敌方占优」（那一刻没人打它），
      醒来 6 秒被 5~8 个围死；1-393 大法师阵亡那一分钟也挡过 5 次「大群贴脸」（身边 8~14 个）。
      「打了再撤」只适合小规模接触：身边敌人 >= CALM_HOLD_MAX_FOES 就不等挨打，照旧先撤（保命在前）。"""
    if exposure is None or fire is not False:
        return False
    reason, near, _ = exposure
    if len(near) >= CALM_HOLD_MAX_FOES:
        return False
    return reason != "低血量脱离接触" and any(f.owner != CREEP_OWNER for f in near)


def home_fight(hero, foes, home, halls=None) -> bool:
    """守家：英雄在家门口，或它身边 HERO_RESERVE_R 内的敌方作战单位一半以上在我方基地（near_home）。"""
    if near_home(hero.x, hero.y, home, halls):
        return True
    fz = [f for f in foes if f.hp > 0 and _dist(f, hero) <= HERO_RESERVE_R]
    return bool(fz) and 2 * sum(1 for f in fz if near_home(f.x, f.y, home, halls)) >= len(fz)


def hero_exposure(hero, foes, ours, unavailable=(), creep_targets=None):
    """英雄按真正能接战的护卫自保；已撤退/轮换/低血量的人不能当作护卫。"""
    near = [f for f in foes if f.hp > 0 and _dist(f, hero) <= max(900.0, RANGE.get(f.type, 100) + 200)]
    if not near:
        return None
    contact = any(_dist(f, hero) <= RANGE.get(f.type, 100) + 200 for f in near)
    # 野怪还够不着时允许远程输出/等肉盾，不能把安全的开打队形判作孤军。
    if all(f.owner == CREEP_OWNER for f in near) and not contact:
        return None
    if creep_output_safe(hero,near,ours,creep_targets):
        return None
    escort_reach = 1500 if all(f.owner == CREEP_OWNER for f in near) else 700
    friends = [u for u in ours if u.addr != hero.addr and u.addr not in unavailable
               and u.hp > 0.35 * max(1, u.hp_max) and _dist(u, hero) <= escort_reach
               and any(_dist(u, f) <= RANGE.get(u.type, 100) + 700
                       and _dist(u, f) <= _dist(hero, f) + 150 for f in near)]
    # ★ 09-21 4-036 8:35 交战中英雄单独撤走、把小兵留在原地；要么一起打，要么全部撤。
    #   实机 22:25:34「英雄局部自保：Hamg 625/625 孤军且敌方占优，敌 5、有效护卫 0」—— 护卫只算血 > 35% 的，
    #   而身边 4 个带伤步兵正和同一批野怪打着；英雄一走，它们一个个死在撤退路上。
    #   和同一批敌人交着手的自己人（血 > TEAM_MIN_FRAC、不在撤退/轮换里、不是召唤物）在，就不算孤军。
    #   英雄自己低血量、被大群贴脸、真的护卫不足这三条照旧。
    # ★ 09-21 1-284 3:20 大法师召出水元素后总要往家跑一段，其它单位已经开打、英雄却离场；召出来应立即参战。
    #   实机 23:33:24「英雄自保·脱离战场 大魔法师#1 450/450 孤军且敌方占优：身边敌 3 个、护卫 0 个，锁 10 秒」——
    #   豺狼人先咬上英雄；旁边 525 血的水元素离野怪 500~670（比英雄远，不算 friends），又因为是召唤物不算 team；
    #   步兵还在路上。英雄一撤、solo 一发布，大脑就「打野原营地重整，等英雄归队」，整队停下。
    #   只对野怪放宽（对玩家的兵照旧）：① 在和同一批野怪交手的召唤物算自己人 —— 大脑本来就是「召唤物先接仇恨」；
    #   ② 英雄血 >= CREEP_HOLD_FRAC 时，正赶过来的自己人（离野怪 CREEP_COMING_R 内）也算 —— 5-117：
    #   打弱小野怪时英雄能扛就先扛，不让小兵承伤。真危险交给 TTD（几秒内会死）和下面两条血量规则。
    creeps_only = all(f.owner == CREEP_OWNER for f in near)
    team = [u for u in ours if u.addr != hero.addr and u.addr not in unavailable
            and (u.type not in SUMMONS or creeps_only)
            and u.hp > TEAM_MIN_FRAC * max(1, u.hp_max) and _dist(u, hero) <= escort_reach
            and any(_dist(u, f) <= RANGE.get(u.type, 100) + 300 for f in near)]
    frac = hero.hp / max(1.0, hero.hp_max)
    coming = []
    if creeps_only and frac >= CREEP_HOLD_FRAC:
        coming = [u for u in ours if u.addr != hero.addr and u.addr not in unavailable
                  and u.hp > 0.35 * max(1, u.hp_max) and _dist(u, hero) <= escort_reach
                  and any(_dist(u, f) <= CREEP_COMING_R for f in near)]
    reason = None
    # ★★ 09-23 打野时英雄频繁跑远、长时间不攻击。开打后英雄应持续输出：英雄血厚，能扛就扛一会，
    #   等小兵回血时英雄自己也在回血。1-418 23:41:46~55（清营地强度 13）：大法师 498/525 被「孤军且敌方占优，敌 6、有效护卫 0」
    #   连拉 3 次各 10 秒，部队只能「打野原营地重整，等英雄归队」。
    #   只对野怪：英雄血 >= CREEP_TANK_FRAC 时「孤军」「大群贴脸」「护卫不足」都不拉 —— 低血量（45%）和几秒内会死（TTD）那两条照旧。
    tank = creeps_only and frac >= CREEP_TANK_FRAC
    if contact and frac <= 0.45:
        reason = "低血量脱离接触"
    elif tank:
        return None
    elif (contact and frac <= HERO_SWARM_FRAC and len(near) >= HERO_SWARM_MIN
          and len(near) >= len(friends)):
        # 1-090 23:04:32：山丘 80% 血以下时身边已是 11 敌 / 11 个有效护卫，
        # 之后约 1.5 秒才落到旧 45% 线，再下令时已经被围死。这里只在大群敌人已经
        # 接触、且人数不低于真正能接战的护卫时提前退到后排；普通前排接触不会触发。
        reason = "大群贴脸提前脱离"
    elif not friends and not team and not coming and len(near) >= 2 and local_power([hero]) < (1.25 if RANGE.get(hero.type, 100) > MELEE_RANGE else 0.8) * local_power(near):
        reason = "孤军且敌方占优"
    elif contact and frac <= 0.65 and local_power([hero] + friends) < 0.65 * local_power(near):
        reason = "实际护卫不足"
    return (reason, near, friends) if reason else None


def brain_controls_battle(claims, army):
    """优先级 76 只用于新脑局部进退；不覆盖个人 safety/dodge/cast。"""
    if not army or not hasattr(claims, 'lease_of'):
        return False
    owned = sum(1 for u in army if (lease := claims.lease_of(u.addr)) and lease[0] == 1 and lease[1] == 76)
    return owned >= max(1, math.ceil(len(army)*.5))


def hero_return_ready(hero, foes, ours, unavailable=(), creep_targets=None, fire=None):
    """恢复健康且局部位置安全就归队，不因已经结束的孤军状态罚站满租约。
    ★ 09-22 fire=False（确定没在挨打）：血 >= HERO_RETURN_CALM_FRAC 就回去，暴露理由按 calm_hold 放行（见 HIT_WIN 上面那段）。"""
    need = HERO_RETURN_CALM_FRAC if fire is False else HERO_RETURN_FRAC
    if hero.hp < need * max(1, hero.hp_max):
        return False
    ex = hero_exposure(hero, foes, ours, unavailable, creep_targets)
    return ex is None or calm_hold(ex, fire)


def healed_hero_rejoin_ready(hero, gain, foes, ours, unavailable=()):
    """刚吃到大额治疗的英雄，若局部明显占优就提前解除撤退租约并回到输出位。"""
    if gain < max(100.0, 0.12 * max(1, hero.hp_max)) or _frac(hero) < 0.60:
        return False
    near = [f for f in foes if f.hp > 0 and _dist(f, hero) <= 1200.0]
    if not near:
        return True
    escorts = [u for u in ours if u.addr != hero.addr and u.addr not in unavailable
               and u.type not in WORKERS and can_move(u)
               and u.hp > .35 * max(1, u.hp_max) and _dist(u, hero) <= 1100.0]
    return len(escorts) >= 2 and local_power([hero] + escorts) >= 1.15 * local_power(near)


RW_HERO_S = 0.4   # 英雄撤退令发了没挪窝，多久就判（墙钟秒；3 倍速约 1.2 游戏秒）。原来和小兵一样 0.8（1-299：英雄每游戏秒掉 60~100 血）


class RetreatWatch:
    """观察位移与危险距离，不把持有 hurt 租约当作已救下；墙钟限频且不续租。"""
    def __init__(self):
        self.samples = {}
        self.goals = {}

    def check(self, units, foes, pulled, now, excluded=()):
        active = {u.addr for u in units if u.hp > 0 and pulled.get(u.addr, 0) > now and u.addr not in excluded}
        self.samples = {a: s for a, s in self.samples.items() if a in active}
        self.goals = {a:g for a,g in self.goals.items() if a in active}
        retry = []
        for u in units:
            if u.addr not in active:
                continue
            near = [f for f in foes if f.hp > 0 and _dist(u, f) <= RANGE.get(f.type, 100) + 250]
            nearest = min((_dist(u, f) for f in near), default=1e9)
            old = self.samples.get(u.addr)
            if old is None or old[0] != identity(u):
                self.samples[u.addr] = (identity(u), now, u.x, u.y, u.hp, nearest, 0)
                continue
            if now - old[1] < (RW_HERO_S if u.is_hero else 0.8):
                continue
            moved = math.hypot(u.x - old[2], u.y - old[3])
            # 被追着打但在移动不等于卡住；重下寻路会把本来能走出的路线打断。
            stuck = moved < 64 or (u.order or 0)==0x000D000F
            tries = old[6]
            if near and stuck and tries < 4 and len(retry) < MAX_PULL:
                tries += 1
                retry.append((u, near, tries))
            self.samples[u.addr] = (identity(u), now, u.x, u.y, u.hp, nearest, tries)
        return retry


def retreat_retry_point(unit, threats, allies, home, attempt, previous=None, distance=PULL_BACK):
    """比较撤离路线，优先回己方且沿途少受攻击；同一威胁下保持稳定方向。"""
    x, y = focus_escape_point(unit, threats, allies, home)
    base=math.atan2(home[1]-unit.y,home[0]-unit.x)
    angles=[base+i*math.pi/6 for i in range(12)]+[math.atan2(y-unit.y,x-unit.x)]
    candidates=[]
    for angle in angles:
        tx,ty=unit.x+distance*math.cos(angle),unit.y+distance*math.sin(angle)
        risk=0
        for part in (.2,.4,.7,1):
            px,py=unit.x+(tx-unit.x)*part,unit.y+(ty-unit.y)*part
            for f in threats:
                gap=math.hypot(px-f.x,py-f.y)
                risk+=max(0,RANGE.get(f.type,100)+200-gap)*_unit_dps(f.type)/10
                if gap<128:risk+=3*(128-gap)
        progress=math.hypot(unit.x-home[0],unit.y-home[1])-math.hypot(tx-home[0],ty-home[1])
        separation=min((math.hypot(tx-f.x,ty-f.y) for f in threats),default=0)
        # v5 曾对“上一个落点”强行罚 600；单位被包围时每 0.8 秒就换一个方向，
        # 1-011 里撤退点在数个方向间跳，寻路每次都从头开始。威胁没变时让评分自然选同一条路；
        # 真有追兵换位，risk 会自然改变。previous 保留在签名里便于旧调用方兼容。
        candidates.append((progress+.3*separation-risk,tx,ty))
    _,tx,ty=max(candidates)
    return tx,ty


def hero_danger(hero, foes, ours, r: float = None):
    """★★ 09-17 英雄接触前预警（山丘之王被一堆敌人围死，死前没有任何撤退动作）。

    inst1 212133 21:29：策略层猎杀「对面 1 个」带队走到离家 7338，旁边是对面 22 个兵的主力；21:29:18 山丘还在人堆里放风暴之锤，
    21:29:20 遭遇战撤退（900 内接触、t_them > 1.6 t_us）才叫撤，山丘（近战、冲在最前）已经被围住，5 秒内阵亡。
    接触才判就晚了：英雄身边 HERO_DANGER_R 内敌方玩家作战单位 >= HERO_DANGER_MIN、且按同一套算术明显打不过（t_them > HERO_DANGER_RATIO × t_us）就先拉英雄。
    foes：敌方玩家作战单位（不含野怪）；ours：我方能打的（含召唤物）。-> (t_them, t_us, 敌数, 我数) 或 None"""
    r = HERO_DANGER_R if r is None else r
    fz = [f for f in foes if f.hp > 0 and math.hypot(f.x - hero.x, f.y - hero.y) <= r]
    if len(fz) < HERO_DANGER_MIN:
        return None
    mz = [u for u in ours if u.hp > 0 and math.hypot(u.x - hero.x, u.y - hero.y) <= r]
    f_hp = sum(f.hp for f in fz)
    f_dps = sum(_unit_dps(f.type) for f in fz)
    m_hp = sum(u.hp for u in mz)
    m_dps = sum(_unit_dps(u.type) for u in mz)
    if m_dps <= 0 or f_dps <= 0:
        return None
    t_them, t_us = f_hp / m_dps, m_hp / f_dps
    return (t_them, t_us, len(fz), len(mz)) if t_them > HERO_DANGER_RATIO * t_us else None


def precontact_hero_retreat(hero, foes, ours):
    """接触前的英雄撤退判据。

    ``hero_danger`` 已同时要求敌军不少于五个、局部清场时间差超过两倍；一旦成立就是
    压倒性劣势。不能再因为英雄当前血量健康或身边暂时有四个护卫而放行，否则英雄会
    先走进包围圈，等接触后的掉血规则触发时已经来不及。
    """
    close = hero_danger(hero, foes, ours)
    if close is not None:
        return close
    # 1-215：两英雄在中场、永久兵全部在 4200 以外。敌方六人集团已经进入 3000，
    # 旧预警仍因 1300 内为零而不动作；下一秒大脑又把两英雄攻击移动进敌群，看到 9 人时已来不及。
    # 只有真正脱离非英雄护卫时才看这层外圈，正常成团行军/正面团战仍沿用原 1300 判据。
    escorts = [u for u in ours if u.addr != hero.addr and not u.is_hero and u.type not in SUMMONS
               and not is_building(u) and u.hp > .35 * max(1, u.hp_max)
               and _dist(u, hero) <= HERO_ESCORT_R]
    if len(escorts) >= HERO_ESCORT_MIN:
        return None
    fz = [f for f in foes if f.hp > 0 and _dist(f, hero) <= HERO_RESERVE_R]
    if len(fz) < HERO_RESERVE_MIN:
        return None
    # ★★ 09-22 1-394 23:30 部队已经开战，英雄仍停在地图中间不去支援。
    #   21:12:14 两个满血英雄被这条拉回 1000：「3000 内敌 6~7 个、我方 2~3 个」—— 那 6~7 个正和我方 13 个兵在 2700 外交火，
    #   英雄只是行军跟在后面（大脑 _backline）才「身边没护卫」。前面有 HERO_SCREEN_MIN 个自己人贴着这批敌人、而且比英雄离它们近
    #   = 英雄在自己部队后面，不是孤军；1-215 那种英雄自己撞上敌人（自己人都在 4200 外）照旧拉。
    d_hero = min(_dist(f, hero) for f in fz)
    screen = [u for u in ours if u.addr != hero.addr and not u.is_hero and u.type not in SUMMONS and not is_building(u)
              and u.hp > .35 * max(1, u.hp_max)
              and min(_dist(u, f) for f in fz) <= min(HERO_SCREEN_R, d_hero)]
    if len(screen) >= HERO_SCREEN_MIN:
        return None
    mz = [u for u in ours if u.hp > 0 and _dist(u, hero) <= HERO_ESCORT_R]
    f_hp, f_dps = sum(f.hp for f in fz), sum(_unit_dps(f.type) for f in fz)
    m_hp, m_dps = sum(u.hp for u in mz), sum(_unit_dps(u.type) for u in mz)
    if m_dps <= 0 or f_dps <= 0:
        return None
    t_them, t_us = f_hp / m_dps, m_hp / f_dps
    return (t_them, t_us, len(fz), len(mz)) if t_them > HERO_RESERVE_RATIO * t_us else None


def guard_picks(hero, waters, threats, last: dict, now: float, allies=(), creep_targets=None):
    """大法师的水元素护卫：离英雄 GUARD_R 内最近的那个敌人就是威胁，GUARD_LEASH 内的水元素去打它。

    last：{水元素地址: (上次目标地址, 时刻)}（调用方下令成功后写）。同一个目标 GUARD_SAME 秒内不重下、换目标至少隔 GUARD_SWITCH 秒。
    creep_targets：{野怪地址: 它在打的单位句柄对}（主循环 aggro 读的）。给了就只护「已经在打我们的人」的野怪 ——
      ★ 09-22 1-301 4:30 准备清分矿野怪时，水元素连续几局跑去另一堆野怪；英雄已就位，水元素却没跟上。
        水元素的行动要和整体决策统一。命令记录：01:26:24/29 大脑让水元素跟主力去分矿集合点，
        01:26:26/32/34/36 本规则让它去打「离大法师最近的野怪」—— 路过的另一个营地。睡着的、没跟我们交手的野怪什么时候开，
        归大脑的打野决策管（召唤物先接仇恨那一套）；敌方玩家的兵照旧见了就护主。
    -> [(水元素, 目标)]"""
    near = [t for t in threats if t.hp > 0 and _dist(t, hero) <= GUARD_R]
    if creep_targets is not None:
        ours = {_pair(u) for u in list(allies) + list(waters) + [hero]}
        near = [t for t in near if t.owner < 12 or creep_targets.get(t.addr) in ours]
    if not near:
        return []
    threat = min(near, key=lambda t: _dist(t, hero))
    # 玩家敌军是途中遭遇，水元素应立即护主；中立野怪必须服从策略层的开怪门槛。
    # 1-011 中策略层正把全军纯移动到集合点，毫秒层却让水元素打了英雄旁的野怪，
    # 形成只有英雄和召唤物接敌的旁路。这里至少要求两名真正战斗单位已经到英雄身边。
    if threat.owner >= 12:
        escorts = [u for u in allies if u.hp > 0 and u.addr != hero.addr
                   and u.type not in WATER_TYPES and _dist(u, hero) <= GUARD_CREEP_ESCORT_R]
        if len(escorts) < GUARD_CREEP_ESCORT_MIN:
            return []
    out = []
    for w in waters:
        if w.hp <= 0 or _dist(w, hero) > GUARD_LEASH:
            continue
        prev = last.get(w.addr)
        t_w = threat
        if prev is not None:
            # 09-22：上一个目标还在大法师身边、没比最近的远出 GUARD_STICK，就接着打它 ——
            #   「离大法师最近的敌人」一两秒换一个（368 局一只水元素 20 秒里换了 6 个），每换一次都要重新走位
            keep = next((t for t in near if t.addr == prev[0]), None)
            if keep is not None and _dist(keep, hero) <= _dist(threat, hero) + GUARD_STICK:
                t_w = keep
            same = prev[0] == t_w.addr
            if (same and now - prev[1] < GUARD_SAME) or (not same and now - prev[1] < GUARD_SWITCH):
                continue
        out.append((w, t_w))
    return out


# ★ 09-22 1-301 15:50 敌军来打分矿，敌人扎堆正是放技能的好时机，血法师却几乎没放技能。
#   心跳：那一分钟烈焰风暴「下令 0、被退 0、没学 0、缺蓝 0」—— 根本没进候选。01:30:10、01:30:15 两次「大群贴脸提前脱离」
#   把她拉到后排（每次 2.5 秒），中间还有「撤离未脱险·改道」，整场她大多数时候都在「撤退中」，而撤退中只放逃生技能。
#   自保把英雄往后拉、施法就不放大 —— 两条规则抢同一个英雄。站在后排放烈焰风暴正是血魔法师该干的事：
#   撤退中、血 >= PULLED_CAST_FRAC 的英雄也可以放下面这些**瞬发、不引导**的技能，但目标必须**就在施法距离以内**（不许往前走一步）。
#   放完英雄站住了，RetreatWatch 0.4 秒内看到没挪窝，会接着把它往后拉。暴风雪/地震/死亡凋零这种要站着引导的不放。
BACKLINE_CASTS = {"flamestrike", "stormbolt", "chainlightning", "shockwave", "frostnova", "carrionswarm", "deathcoil",
                  "holylight", "healingwave", "forkedlightning", "clusterrockets", "breathoffire", "shadowstrike",
                  "manaburn", "hex", "sleep", "entanglingroots", "impale", "drunkenhaze", "silence"}
PULLED_CAST_FRAC = 0.5


def plan_hero_cast(hero, foes, allies, ready, mana=None, low=None, learned=None, unlearned=None, ctx=None):
    """这个英雄现在该放什么。返回 (技能, 订单号, 方式, 目标地址或 None, x, y[, meta]) 或 None。

    foes：活着的敌方单位（敌方玩家的作战单位 + 野怪）；allies：我方活单位（含英雄本人）；
    ready(技能名) -> 冷却好了没。方式：instant 无目标 / point 点 / target 单位。
    mana：当前蓝量（Cmd.mana 读的），None = 读不到、不按蓝挡。
    low：dict，时机到了但蓝不够的技能计数（技能 -> 次）—— 然后**往下试便宜的**，
         不在一个放不出来的技能上干等（09-16：70 蓝的大法师该放 75 蓝的暴风雪，不是等水元素）。
    ctx：见 _rule_* 上面那段。ctx["pulled"]（本模块正拉它撤）时只试 while_pulled 的逃生技能。
    09-18：耗蓝按当前等级（spell_mana）；meta no_mana 的不挡（开自动施法、关献祭）；min_mana 给持续耗蓝的开关留余量。
    """
    ctx = dict(ctx or {})
    ctx["mana"] = mana
    for name in HERO_SPELLS.get(hero.type, ()):
        sp = SPELLS.get(name)
        if sp is None:
            continue
        backline = ctx.get("pulled") and not sp["opts"].get("while_pulled")
        if backline and not (name in BACKLINE_CASTS and _frac(hero) >= PULLED_CAST_FRAC):
            continue
        if not ready(name):
            continue
        level = learned.get(sp["abil"], 0) if learned else None
        plan = _spell_plan(name, hero, foes, allies, level or None, ctx)
        if not plan:
            continue
        if backline:
            # 撤退中放：目标点必须就在施法距离内（规则里的 reach 带了往前走的 pad，这里不许走）
            rng = (skill(name, level or None).get("Rng")
                   or sp["opts"].get("rng", 800.0 if sp["rule"] == "aoe_point" else 600.0))   # 兜底和 _rule_aoe_point 一致
            if math.hypot(plan[4] - hero.x, plan[5] - hero.y) > rng:
                continue
        # learned：{技能代码: 等级}（w3cmd.abilities 读的），None = 读不到、不按学没学挡
        if learned is not None and not learned.get(sp["abil"], 0):
            if unlearned is not None:
                unlearned[name] = unlearned.get(name, 0) + 1
            continue
        meta = plan[6] if len(plan) > 6 else {}
        if (mana is not None and not meta.get("no_mana")
                and mana < max(spell_mana(name, level or None), float(sp["opts"].get("min_mana", 0.0)))):
            if low is not None:
                low[name] = low.get(name, 0) + 1
            continue
        return plan
    return None


def can_move(u) -> bool:
    """它是不是一个能走路的单位（不是建筑）。

    查不到的按**不能动**处理 —— 宁可少救一个兵，
    也不要去拔自己的主基地（暗夜的远古树真的会被拔起来走）。
    """
    return FOOD.get(u.type, 0.0) > 0.0

LOGF = os.environ.get(
    "WAR3_MOD_LOG",
    os.path.join(w3paths.BIN, "ai_run.log"),
)


def note(msg: str) -> None:
    msg = _with_ids(msg)
    try:
        with open(LOGF, "a", encoding="utf-8") as fh:
            fh.write(f"  [保命] {msg}\n")
    except OSError:
        pass
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


HURTF = os.environ.get(
    "WAR3_HURT_FILE",
    os.path.join(w3paths.BIN, "hurt.json"),
)

# ★ 09-21 血条编号：大脑（war3-brain/brain/unitids.py）给每个我方英雄/作战单位分本局编号、发布 unit_ids.json。
#   这里的日志「hfoo@14D4E0E4」补成「#23 hfoo@14D4E0E4」—— 排查「#23 为什么撤了」这类问题时，两层的日志对得上同一个单位。
#   补编号只有一份实现（brain.unitids.annotate），这里只读文件；读不到（没有大脑 / 副本目录里跑测试）就原样打印。
IDSF = os.path.join(os.path.dirname(HURTF), "unit_ids.json")
_ids_cache = {"at": -1e9, "ids": {}, "pid": None}
try:
    sys.path.insert(0, w3paths.STRATEGY)
    from brain.unitids import annotate as _annotate_ids, load as _load_ids   # noqa: E402
except Exception:                        # noqa: BLE001
    _annotate_ids = _load_ids = None


def _with_ids(msg: str) -> str:
    if _annotate_ids is None:
        return msg
    try:
        now = time.monotonic()
        if now - _ids_cache["at"] > 2.0:
            _ids_cache["at"] = now
            _ids_cache["ids"] = _load_ids(IDSF, _ids_cache["pid"])
        return _annotate_ids(msg, _ids_cache["ids"])
    except Exception:                    # noqa: BLE001 - 日志补编号出任何问题都照原样打
        return msg


# ★★ "一个人扛一堆怪"要**单开一个通道**，不能混进 hurt.json。
#
# 对局画面：一个英雄被一堆野怪围着，还在打，不撤。
# 查日志：毫秒层**判断是对的**，而且连报三次
#     17:04:01 英雄独自扛野怪：Obla 487/600 被 2 只咬着 —— 拉出来
#     17:04:28 英雄独自扛野怪：Obla 559/650 被 3 只咬着 —— 拉出来
#     17:04:36 英雄独自扛野怪：Obla 515/650 被 8 只咬着 —— 拉出来
# 拉了三次，怪从 2 只变成 8 只，人还在那。
#
# 原因在 PowerShell 那边的 W3-IsHurt：伤员锁在 **hp >= 60% 时立刻解除**。
# 而这个英雄是 515/650 = 79% —— **锁从来没生效过**，
# 毫秒层刚把它拉走，下一拍策略层就照原样派回去。
#
# 根因是**两件事共用了一把锁**：
#     "残血了"     —— 血回来就该归队，按血量解锁是对的
#     "孤军对一群" —— 和血量无关，血满照样被围死
# 这个项目写过"两份数据混用一个通道"会出事（hurt.json vs worker_busy），
# 这次是同一个错的另一面：**通道分开了，语义没分开**。
SOLOF = os.environ.get(
    "WAR3_SOLO_FILE",
    os.path.join(os.path.dirname(HURTF), "solo.json"),
)

# 09-17 v291：按交战胜率叫撤之后告诉策略层「这一波别追了」（fightodds.write_signal；策略层 W3-FightRetreatSignal 读）。
# 和 hurt.json 分开：hurt.json 是「这几个人别派」（按人、几秒），这个是「这一仗输了、结束追击/反推」（按仗）。
RETREATF = os.path.join(os.path.dirname(HURTF), "fight_retreat.json")
SACF = os.path.join(os.path.dirname(HURTF), "sacrifice.json")      # 09-18 大脑发布的喂野怪名单（load_sacrifice）
# 09-21 养伤名单（unitmind.Minds）：和 hurt.json 分开 —— hurt.json 是「本模块正拉着它、指挥权在我这」（几秒），
#   这份是「血没回到 RETURN_FRAC 之前别派它去打」（按条件）。大脑出击/打野/守家不派，整队恢复照样能带它去回血。
RESTINGF = os.path.join(os.path.dirname(HURTF), "resting.json")
# 09-21 战斗决策明细（declog.py）：每个战斗决策一条，远见「战斗决策」页读
DECF = os.path.join(os.path.dirname(HURTF), "decisions.jsonl")


def publish_solo(solo: dict[int, float], now: float, game_pid: int,
                 epoch=None, units=None) -> None:
    """孤军名单：这些人**不管血多满都别派回去**，直到旁边有人了。

    和 hurt.json 一样写绝对墙钟截止、PID、对局和单位句柄。
    文件即使停止更新，读者也会按原截止释放；读取不续租。
    """
    alive = {a: t for a, t in solo.items() if t > now}
    solo.clear()
    solo.update(alive)
    write_deadlines(SOLOF, alive, now, game_pid, epoch, units)


def publish_hurt(pulled: dict[int, float], now: float, game_pid: int,
                 epoch=None, units=None) -> None:
    """把"我刚把谁拉出来了"告诉 PowerShell 那边。

    为什么需要：策略层算"这一波打不打得过"时，**必须把撤下来的人排除掉**——
    否则账面上算五个人能打，实际上场的只有一个，而那一个是被这个错误结论
    送上去的（今天两个英雄就是这么死的）。

    为什么用文件而不是共享内存：这是**一份很小、一秒最多写几次、
    而且可以丢**的数据。文件足够，而且 PowerShell 读它不需要 Add-Type ——
    那个东西在这个项目里挡过一次启动，能不用就不用。
    写的是外部进程的 Unix 毫秒绝对到期时刻，不是游戏倍速钟或剩余秒数。
    """
    alive = {a: t for a, t in pulled.items() if t > now}
    pulled.clear()
    pulled.update(alive)
    write_deadlines(HURTF, alive, now, game_pid, epoch, units)


# ═══════════════════════════════════════════════════════════════════════
# 打野轮换（creep aggro manipulation / 拉扯）
#
# 设计要求：小队一起上；掉血多的单位先撤出一小段摆脱野怪，其余继续输出，
#        野怪不追了再回去打 —— 让野怪的伤害分散在多个单位上，
#        一个单位都打不死就被全灭。
#
# 这是高手打野的标准做法。查了一下机制（warcraft-gym 的 creep mechanics
# 总结、暴雪官方 basics/creeping），确认两件事：
#
#   1. **普通野怪打的是"正在打它的那个"。** 把被集火的单位拉出射程，
#      仇恨就转到别人身上；再送回去，它又能安全输出。
#      伤害于是摊在整队人头上，而不是把一个人的血条打空。
#
#   2. ★★ **7 级以上的野怪不守这条规矩：它们永远打射程内血最少的那个。**
#      对这种营地做"拉出去再送回来"是**反的** —— 那个残血单位一回去
#      就又被集火。我们日志里确实有"最弱的一队 5 只、最高 7 级"这种。
#      所以高等级营地要换一条规则：**血最少的那个先别回去**，
#      等它不再是全队最低了再上。
#
# 为什么放在这个 100 毫秒的模块里：轮换要在"被打了"和"转火了"之间反应，
# 那是零点几秒的事。战略拍 3.2 秒一圈，等它反应过来人已经死了。
#
# 和"保命撤退"的区别（这两件事很容易混成一件，混了就两头不讨好）：
#     保命撤退  = 这个人要死了 -> 拉回家 800，锁住 8 秒，告诉策略层别用它
#     打野轮换  = 这个人在挨打 -> 拉出射程 400，一两秒后**送回去继续打**
# 轮换**不写 hurt.json** —— 那份文件是给策略层"这人别用了"的信号，
# 而轮换恰恰要求它马上回来。写了会让策略层以为在场人数不够，
# 进而整队撤退，把一次正常轮换变成一次溃退。
CREEP_OWNER = 12
ROT_OUT = 420.0        # 拉出去多远：够脱离接触，不是回家
ROT_HOLD = 1.4         # 普通营地：在外面最多待这么久
ROT_HOLD_HI = 3.0      # 7 级以上：等它不再是全队血最少的，最多等这么久
HI_LEVEL = 7           # 从这一级开始，野怪只打血最少的


def creep_camp_near(snap, mine_engaged):
    """正在和我们交手的那一队野怪，以及它的最高等级。

    ★★★★ 09-09 这里量出过一个致命的判据错误。

    现象：打野时经常有丝血单位还在硬扛，
          旁边明明有满血或血更多的单位可以接替。

    加了跳过理由计数之后，一分钟的心跳就给出了答案：

        没换的理由 没在打野×525、找不到正在挨打的×53、它已经在外面×5

    **「没在打野」占 78~100%** —— 轮换整条逻辑从来没启动过。

    根因在下面这两行原来的写法：

        cx = sum(u.x for u in mine_engaged) / len(mine_engaged)   # 全军重心
        camp = [野怪 if 距这个重心 < 900]

    而 mine_engaged 是**我方所有能动的非工人单位**，包括待在家里的那些。
    6 个人在 5000 外打野、10 个人在家，重心就落在中间的空地上，
    那里 900 内一只野怪都没有 —— 于是判成"没在打野"。

    **重心不是战斗发生的地方。** 这是「量之前先问这个数是不是在量你以为的东西」
    这条经验在这个模块里的又一次。

    改成按**每个单位**判：只要有任何一个自己人贴着某只野怪，那只就算参战。
    ⚠ 代价是 O(野怪×我方)，实测 ~20×30=600 次距离计算 / 100ms，可以接受；
      而且只在 snap 里有野怪时才进这个循环。
    """
    if not mine_engaged:
        return [], 0
    creeps = [u for u in snap.units if u.owner == CREEP_OWNER and u.hp > 0]
    if not creeps:
        return [], 0
    camp = [c for c in creeps
            if any(math.hypot(c.x - u.x, c.y - u.y) < 900 for u in mine_engaged)]
    lvl = max((LEVEL.get(u.type, 0) for u in camp), default=0)
    return camp, lvl


def in_reach(u, camp) -> bool:
    """这个单位在不在野怪的打击范围里。用**野怪自己的射程**，不是一个固定数。"""
    for c in camp:
        if math.hypot(u.x - c.x, u.y - c.y) <= RANGE.get(c.type, 100) + 180:
            return True
    return False


# ★★ 09-18 按「野怪真实在打谁」轮换：读出单位是否被敌方（含野怪）列为攻击目标，被盯上的低血单位先拉回，
#   不再被当目标后再上去打，打野时尽量做到零阵亡。
#
#   原来的轮换按**掉血速度外推**猜「谁在挨打」（danger 列表）：只有真掉了血、窗口攒够了才知道，
#   而且分不清是谁打的、打它的有几个。今天实测（打野中 32 次读）：野怪当前任务里那对目标句柄
#   就是它在打的那个单位的句柄对，27 次对上我方具体单位（另 5 次是还没醒的野怪），一次读约 21ms。
#   于是能**提前**知道：谁被几只野怪盯着、它们每秒打多少、按现在的血还能撑几秒。
#
#   规则：被盯着、且 血比例 < AGGRO_PULL_FRAC 或 撑不过 AGGRO_PULL_TTD 秒 -> 拉到盯着它的野怪射程外；
#         **没有野怪再盯着它**满 AGGRO_CLEAR 秒 -> 送回去集火营地里的最高输出核心；回去又被盯、血还低 -> 再拉。
#         血低到 AGGRO_STAY_FRAC 以下的别回去了，等营地打完。召唤物（水元素）不拉 —— 它就是拿来扛的。
#   读法同 mod_fight 的 TargetCache：野怪的任务句柄变了才重读（快照里就有），没变的 AGGRO_REFRESH 秒兜底；一轮最多 AGGRO_BUDGET 秒。
AGGRO_REFRESH = 1.0
AGGRO_BUDGET = 0.07        # 一轮最多花这么多秒读任务（一次约 21ms，约 3 只）
AGGRO_FRESH = 2.5          # 读数这么久以内才算数
AGGRO_PULL_FRAC = 0.55     # 027 局 7 个步兵打野阵亡、死前仅 18~75/420；被盯后更早轮换，其他单位继续输出
AGGRO_PULL_FRAC_HERO = 0.55
AGGRO_PULL_TTD = 5.0       # 或者按盯着它的野怪算，这么多**游戏秒**内会被打死（DPS 表按游戏秒；3 倍速下反应要 0.2~0.5 墙钟秒 = 0.6~1.5 游戏秒）
AGGRO_PULL_TTD_HERO = 7.0
AGGRO_CLEAR = 0.6          # 没有野怪盯着它满这么久才送回去
AGGRO_MIN_OUT = 0.5        # 拉出去至少这么久
AGGRO_STAY_FRAC = 0.40     # 血比例低于这个就别回去了，等营地打完。027 局证明 30% 仍来不及承受换目标后的第一轮伤害
                           #   （回去时血中位 25%），心跳「送回后又被盯」占送回的 44%。设计原则是「不再被盯着就立刻回去打」，
                           #   这条只挡血太低、再被盯一次就撑不到拉出来的。和大脑 tactics.CREEP_UNIT_HP 同一条线，改一边要改另一边
AGGRO_OUT_PAD = 250.0      # 拉到离盯着它的野怪「射程 + 这么远」
AGGRO_REMOVE_EVERY = 0.8   # 拉出去还被追着：这么久再往外挪一次
AGGRO_LEASE = 2.0          # 拉出去的人每轮续这么长的租约（送回去就还）


class CreepAggro:
    """野怪 -> 它在打谁（目标句柄对）。按任务句柄变化触发重读。"""

    def __init__(self, refresh: float = AGGRO_REFRESH):
        self.refresh = refresh
        self.ent: dict = {}       # 野怪地址 -> {"ident", "task", "target", "at"}
        self.reads = 0
        self.read_s = 0.0

    @staticmethod
    def _ident(u):
        return (getattr(u, "handle_lo", 0), getattr(u, "handle_hi", 0))

    @staticmethod
    def _task(u):
        return (getattr(u, "task_handle_lo", None), getattr(u, "task_handle_hi", None))

    def due(self, creeps, now: float) -> list:
        changed, stale = [], []
        for c in creeps:
            e = self.ent.get(c.addr)
            if e is None or e["ident"] != self._ident(c) or e["task"] != self._task(c):
                changed.append(c)
            elif now - e["at"] >= self.refresh:
                stale.append((e["at"], c))
        return changed + [c for _at, c in sorted(stale, key=lambda t: t[0])]

    def put(self, c, target, now: float) -> None:
        self.ent[c.addr] = {"ident": self._ident(c), "task": self._task(c),
                            "target": tuple(target) if target else None, "at": now}

    def by_target(self, creeps, now: float) -> dict:
        """-> {我方单位句柄对: [在打它的野怪, ...]}，只算读数新鲜、身份没变的。"""
        out: dict = {}
        for c in creeps:
            e = self.ent.get(c.addr)
            if (e is None or e["target"] is None or now - e["at"] > AGGRO_FRESH
                    or e["ident"] != self._ident(c)):
                continue
            out.setdefault(e["target"], []).append(c)
        return out

    def known(self, creeps, now: float) -> bool:
        return any((e := self.ent.get(c.addr)) is not None and now - e["at"] <= AGGRO_FRESH for c in creeps)

    def targets(self, creeps, now):
        return {c.addr:e['target'] for c in creeps if (e:=self.ent.get(c.addr)) is not None
                and e['target'] is not None and now-e['at']<=AGGRO_FRESH
                and e['ident']==self._ident(c) and e['task']==self._task(c)}

    def prune(self, alive: set) -> None:
        for a in [a for a in self.ent if a not in alive]:
            self.ent.pop(a, None)


def _pair(u):
    return (getattr(u, "handle_lo", 0), getattr(u, "handle_hi", 0))


def aggro_pulls(engaged, by_target, skip, room: int) -> list:
    """被野怪盯着、血低或撑不了几秒的 -> [(单位, 盯着它的野怪, 撑几秒)]，最危险的在前，最多 room 个。召唤物不拉。
    ★ 09-18 5-035 局 8 分钟：大法师冲在最前面给野怪当肉盾，被打到残血。
      远程英雄（射程 > MELEE_RANGE：大法师、血法师、先知、巫妖……）被野怪盯上、而营地边有我方肉盾（近战兵或召唤物、血 > 30%）
      就马上拉开，不等掉到 AGGRO_PULL_FRAC_HERO —— 野怪会转去打肉盾。和大脑 tactics.is_backline（打野时远程等肉盾接火）同一个口径。
    09-20：只要场内还有健康召唤物，任何被野怪点名的永久单位都立即轮出；近战只是攻击距离，不能因此被当成肉盾。"""
    summon_tanks = [t for t in engaged if t.type in SUMMONS and t.addr not in skip
                    and t.hp > .3 * max(1.0, t.hp_max)]
    tanks = [t for t in engaged if t.hp > 0.3 * max(1.0, t.hp_max) and t.addr not in skip and not t.is_hero
             and (t.type in SUMMONS or RANGE.get(t.type, 100.0) <= MELEE_RANGE)]
    out = []
    for u in engaged:
        if u.addr in skip or u.type in SUMMONS or u.hp <= 0:
            continue
        att = by_target.get(_pair(u))
        if not att:
            continue
        dps = sum(DPS.get(c.type, 0.0) or 10.0 for c in att)
        ttd = u.hp / max(1.0, dps)
        frac = u.hp / max(1.0, u.hp_max)
        hero = bool(u.is_hero)
        if summon_tanks:
            # 召唤物仍能承伤时，永久兵被点名本身就是轮换理由；英雄排在最前。
            out.append((u, att, -2.0 if hero else -1.0))
        elif hero and RANGE.get(u.type, 100.0) > MELEE_RANGE and tanks:
            out.append((u, att, -1.0))                   # 远程英雄排最前（撑几秒记 -1：不是快死了，是不该它扛）
        elif frac < (AGGRO_PULL_FRAC_HERO if hero else AGGRO_PULL_FRAC) or ttd < (AGGRO_PULL_TTD_HERO if hero else AGGRO_PULL_TTD):
            out.append((u, att, ttd))
    out.sort(key=lambda t: t[2])
    return out[:max(0, room)]


def aggro_out_point(u, att) -> tuple[float, float]:
    """从盯着它的野怪那边往外拉：到离最近那只「射程 + AGGRO_OUT_PAD」以外，至少 ROT_OUT。"""
    cx = sum(c.x for c in att) / len(att)
    cy = sum(c.y for c in att) / len(att)
    dx, dy = u.x - cx, u.y - cy
    n = math.hypot(dx, dy) or 1.0
    reach = max(RANGE.get(c.type, 100.0) for c in att) + AGGRO_OUT_PAD
    d_now = min(math.hypot(u.x - c.x, u.y - c.y) for c in att)
    step = max(ROT_OUT, reach - d_now + 100.0)
    return u.x + dx / n * step, u.y + dy / n * step


def aggro_ready(u, targeted: bool, st: dict, now: float) -> str:
    """拉出去的人现在该怎样：'back' 送回去 / 'stay' 接着在外面 / 'chased' 还被盯着（再往外挪）。
    st：{"out_at", "clear_since"}（clear_since 在这里维护）。"""
    if targeted:
        st["clear_since"] = None
        return "chased"
    if st.get("clear_since") is None:
        st["clear_since"] = now
    if u.hp / max(1.0, u.hp_max) < AGGRO_STAY_FRAC:
        return "stay"
    if now - st["out_at"] >= AGGRO_MIN_OUT and now - st["clear_since"] >= AGGRO_CLEAR:
        return "back"
    return "stay"


# 我方大厅（主基地和分矿）。⚠ 和 battles.HALLS、war3ai.ps1 的 $script:W3HallTypes 是同一张表的三份拷贝，改一处要三处一起改。
HALL_TYPES = {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt",
              "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}
HALL_MEMORY_S = 15.0   # 093：分矿大厅被秒拆后，正在它门口打的仗不能瞬间从“守家”变成“野外拉扯”。


def is_building(u) -> bool:
    """建筑 = 不占人口、不是召唤物、不是英雄（units.json 的数据，见上面 _load_units 那段）。"""
    return bool(FOOD) and FOOD.get(u.type, 1.0) == 0 and u.type not in SUMMONS and not u.is_hero


TOWER_SAFE_PAD = 150.0   # 塔射程外再留这么多才算安全（和大脑 tactics.TOWER_CLEAR_PAD 同一个数，改一处要改两处）


def enemy_towers(units, me) -> list:
    """会打人的敌方建筑（箭塔、防御塔、地洞……）：不占人口、表里有伤害、不是召唤物/英雄。"""
    return [f for f in units if f.owner != me and f.owner < 12 and f.hp > 0
            and FOOD.get(f.type, 1.0) == 0 and DPS.get(f.type, 0.0) > 0 and f.type not in SUMMONS and not f.is_hero]


def in_tower_range(u, towers) -> bool:
    return any(math.hypot(t.x - u.x, t.y - u.y) <= RANGE.get(t.type, 800.0) + TOWER_SAFE_PAD for t in towers)


# ★ 09-22 1-333 22 分 大法师再次死在敌方防御塔射程里。那局大法师死了两次，都在兽族地洞（otrb）射程里：
#   9 分那次是**本模块自己**把它拉进去的 —— 12:35:07「英雄被集火撤：拉到 (3896,2645)」，背离打它的兽族步兵，
#   而那个方向 630 外就是地洞（离地洞 256），之后四次「撤离未脱险·改道」都没走出来，站着被射死。
#   撤退点只看了会动的敌人（打它的人 / threats），塔和地洞不在里面。现在每个撤退令下之前过一遍 tower_safe_point。
TOWER_SAFE_STEP = 100.0   # 撤出塔射程时再往外多走这么多


def _tower_reach(t) -> float:
    return RANGE.get(t.type, 800.0) + TOWER_SAFE_PAD


def _path_hits_tower(x0, y0, x1, y1, towers, skip=()) -> bool:
    """从 (x0,y0) 直线走到 (x1,y1) 会不会穿过塔的射程（起点本来就在里面的那几座不算，见 skip）。"""
    return any(t not in skip and _seg_dist(t.x, t.y, x0, y0, x1, y1) <= _tower_reach(t) for t in towers)


def tower_safe_point(u, tx, ty, towers):
    """撤退目的地过一遍塔：-> (x, y, 改没改)。
      * 目的地在塔射程里 -> 改成从那座塔往 u 这一侧、射程外 TOWER_SAFE_STEP（再落进别的塔就再往外推）；
      * 目的地在外面、但直线路过塔的射程（起点已经在里面的那几座不算）-> 同样的距离，左右各转 30°/60°/90°/120° 找第一条不穿塔的；
        都穿就用第一条。
    没有塔、或者目的地和路线都干净 -> 原样返回。"""
    if not towers:
        return tx, ty, False
    inside = [t for t in towers if math.hypot(t.x - tx, t.y - ty) <= _tower_reach(t)]
    if inside:
        b = inside[0]
        ex, ey = u.x, u.y
        for _ in range(3):
            d = math.hypot(ex - b.x, ey - b.y)
            if d < 1:
                ex, ey, d = b.x + 1.0, b.y, 1.0
            r = _tower_reach(b) + TOWER_SAFE_STEP
            if d < r:
                ex, ey = b.x + (ex - b.x) / d * r, b.y + (ey - b.y) / d * r
            b = next((o for o in towers if o is not b and math.hypot(o.x - ex, o.y - ey) <= _tower_reach(o)), None)
            if b is None:
                break
        return ex, ey, True
    here = [t for t in towers if math.hypot(t.x - u.x, t.y - u.y) <= _tower_reach(t)]
    if not _path_hits_tower(u.x, u.y, tx, ty, towers, here):
        return tx, ty, False
    dist = math.hypot(tx - u.x, ty - u.y) or 1.0
    base = math.atan2(ty - u.y, tx - u.x)
    first = None
    for deg in (30, -30, 60, -60, 90, -90, 120, -120):
        a = base + math.radians(deg)
        cx, cy = u.x + dist * math.cos(a), u.y + dist * math.sin(a)
        if any(math.hypot(t.x - cx, t.y - cy) <= _tower_reach(t) for t in towers):
            continue
        first = first or (cx, cy)
        if not _path_hits_tower(u.x, u.y, cx, cy, towers, here):
            return cx, cy, True
    return (first[0], first[1], True) if first else (tx, ty, False)


def halls_of(snap) -> list:
    return [(u.x, u.y) for u in snap.mine if u.type in HALL_TYPES]


def recent_halls(memory: dict, snap, now: float, ttl: float = HALL_MEMORY_S) -> list:
    """记住刚被拆的大厅位置，直到这一轮守家战有时间打完。

    memory 的 key 用单位身份而不是坐标；大厅升级或地址复用不会把另一局的点带进来，
    因为主循环在 epoch/PID 变化时会清空它。返回值按坐标去重，兼容大厅升级时新旧身份重叠。
    """
    for u in snap.mine:
        if u.type in HALL_TYPES and u.hp > 0:
            memory[identity(u)] = (u.x, u.y, now + ttl)
    for key in [key for key, row in memory.items() if row[2] <= now]:
        memory.pop(key, None)
    return list(dict.fromkeys((row[0], row[1]) for row in memory.values()))


def near_home(x, y, home, halls=None) -> bool:
    """★ 09-16：「家门口」= 离**任何一座**我方大厅（主基地、分矿）HOME_HOLD 以内；拿不到大厅才退回 home 那一个点。

    原来只看 home_of 的一个点 —— 而分矿被抄是最常见的决定性守家战：210459 局 21:10:38 那场
    （交战账本判「我方基地」= 离分矿 1800 内）打在 (-4248,-80) 附近，离主基地 3100，
    home_brawl 不认它是家门口，10 秒拉出 15 个、活 0 个，战损 15:1。"""
    pts = list(halls or [])
    if not pts and home is not None:
        pts = [home]
    return any(math.hypot(x - hx, y - hy) < HOME_HOLD for hx, hy in pts)


def home_fallback_point(enemy_x: float, enemy_y: float, home, halls=None) -> tuple[float, float]:
    """最近大厅背向敌军的一侧。家门口压倒性劣势时不能继续朝“家中心”挤，那常常正是敌军所在位置。"""
    pts = list(halls or [])
    if not pts and home is not None:
        pts = [home]
    if not pts:
        return enemy_x, enemy_y - HOME_FALLBACK
    hx, hy = min(pts, key=lambda p: math.hypot(enemy_x-p[0], enemy_y-p[1]))
    dx, dy = hx-enemy_x, hy-enemy_y
    n = math.hypot(dx, dy)
    if n < 1.0:
        dx, dy, n = 0.0, -1.0, 1.0
    return hx + dx/n*HOME_FALLBACK, hy + dy/n*HOME_FALLBACK


def squad_retreat_ready(now: float, last_squad: float, catastrophic_home: bool,
                        last_home_break: float) -> bool:
    """普通野外撤退沿用 6 秒；建筑后集结只允许较慢重触发，避免刚反打又被毫秒层拉散。"""
    return (now - last_home_break > HOME_BREAK_COOLDOWN
            if catastrophic_home else now - last_squad > 6.0)


def home_brawl(u, home, foes, halls=None, focus_n: int = 0) -> bool:
    """★★ 09-16 **家门口的大仗不拉残血** —— 和"家门口不整队撤"（09-15）同一个理由，那条只管了整队撤。

    策略层 W3-Defend 在主基地无条件守；这里"朝家的方向退 800"在家里就是往建筑堆里挤，
    敌人已经在基地里，退到哪都还在交火圈。三场决定性守家战（170914 / 172402 / 173118，
    我方 25/11/13 人对 26/25/20）拉出来 16 / 9 / 11 个，**录像段末尾活着 0 / 0 / 0 个**，
    被拉时血量最高 69%。被拉的人走开就不输出，敌方转火下一个、下一个又被拉 ——
    帧里我方输出从开战第 5 秒起塌掉一半（170914：每秒打出 850 -> 350，挨打一直 700~1100）。
    pull_picks 的 20% 上限只是让"同时背对敌人在走的"少一些，救下的照样是 0。

    判据：单位离家 < HOME_HOLD，且它身边 PULL_NEAR 内的敌方 >= HOME_BRAWL。
    家门口两三个骚扰的（< HOME_BRAWL）照旧拉；野外照旧拉；打野不走这里。

    ★ 09-16 晚更正：**英雄照拉**。原来"英雄一样不拉"的依据是三场 3 个英雄被拉、3 个都死了（n=3）。
      四个实例 18:00 之后的大仗（同样掉到 35% 以下、正在接触，比被拉和没被拉的活到仗结束）：
        英雄 家门口 被拉 5 活 2（40%）｜没拉 89 活 7（8%）；野外 被拉 2 活 1｜没拉 21 活 5（24%）
        兵   家门口 被拉 60 活 3（5%）｜没拉 834 活 21（3%）—— 兵拉不拉差不多，这条对兵照旧
      合起来英雄被拉 10 活 3、没拉 110 活 12。样本仍小，但英雄死一次要复活钱、丢经验，心跳「家门口大仗英雄照拉」接着看。

    ★ 09-21 实战发现：基地快被拆光、其它单位都在守家时，英雄却一直躲在地图角落不参战。
      要求：没被敌人集火时英雄应上前输出；只有多个单位同时打它且掉血很快时才暂避，
      有自己人顶上后立即回去输出。
      上面 09-16 那条「英雄照拉」优化的是**英雄存活率**，而守家真正要的是**家保不保得住** ——
      英雄活着、家被拆了，这局照样输。所以家门口的英雄改成：**没被集火就留下打**，
      被集火（focus_n >= FOCUS_PULL_N）仍然由 focus 那条路拉走，09-16 的结论在那条路上照旧成立。
      focus_n 来自 focus_last（focus_stats 的读数，09-21 已修好「读不到 ≠ 没在打」）。
    """
    if not near_home(u.x, u.y, home, halls):
        return False
    if u.is_hero:
        return focus_n < FOCUS_PULL_N
    near = 0
    for f in foes:
        if math.hypot(f.x - u.x, f.y - u.y) <= PULL_NEAR:
            near += 1
            if near >= HOME_BRAWL:
                return True
    return False


PULL_STATS: dict = {"丝血不受上限": 0}      # pull_picks 的附加计数，主循环心跳里报完清零


def isolated(u, fighters, foes) -> bool:
    """★ 09-18 孤兵：不是英雄、ISO_FOE_R 内敌方作战单位 >= ISO_FOES、ISO_FRIEND_R 内自己人（不算自己）<= ISO_FRIENDS。
    英雄有自己的一套（hero_danger 接触前预警 / 英雄被集火）。"""
    if u.is_hero or u.hp <= 0:
        return False
    nf = 0
    for f in foes:
        if f.hp > 0 and math.hypot(f.x - u.x, f.y - u.y) <= ISO_FOE_R:
            nf += 1
    if nf < ISO_FOES:
        return False
    friends = [m for m in fighters if m.addr != u.addr and m.hp > 0 and math.hypot(m.x - u.x, m.y - u.y) <= ISO_FRIEND_R]
    if len(friends) <= ISO_FRIENDS:
        return True
    near_f = [f for f in foes if f.hp > 0 and math.hypot(f.x - u.x, f.y - u.y) <= ISO_FOE_R]
    return local_power([u] + friends) < ISO_POWER * local_power(near_f)


def _unit_dps(t: str) -> float:
    """每秒伤害（表里没有按 8）；英雄表里只有基础攻击，按 25 算（和大脑 tactics.HERO_DPS_* 同一个量级）。
    ★ 09-18 所有「打光他们要几秒 / 他们打光我们要几秒」都走这里（打野整队撤退、遭遇战撤退、英雄预警 hero_danger）。
      原来那几处直接查表（大法师 2.3、山丘 3.2），5-031 局 14:28:30 打野「打光他们要 42 秒、他们打光我们要 46 秒」
      把英雄连同兵整队拉走（大脑按真实伤害算这一仗战力比 2.2~3.6），大脑那边就「没英雄」了 —— 同一条规则写了两份，只改了一份。"""
    if t and "A" <= t[0] <= "Z":
        return 25.0
    return DPS.get(t, 0.0) or 8.0


def local_power(units) -> float:
    """兰彻斯特平方律战力 = 总血 × 总每秒伤害（和大脑 tactics.power_ratio 同一套算术）。"""
    alive = [x for x in units if x.hp > 0]
    return sum(x.hp for x in alive) * sum(_unit_dps(x.type) for x in alive)


def rally_point(u, fighters, foes, home, pulled: dict, now: float):
    """孤兵往哪退：ISO_JOIN_R 内至少 3 个自己人（没在撤的）扎堆、且那里 700 内敌人比自己人少的地方，取最近的；没有返回 home。"""
    best = None
    for m in fighters:
        if m.addr == u.addr or m.hp <= 0 or pulled.get(m.addr, 0.0) > now:
            continue
        d = math.hypot(m.x - u.x, m.y - u.y)
        if d <= ISO_FRIEND_R or d > ISO_JOIN_R or (best is not None and d >= best[0]):
            continue
        grp = sum(1 for k in fighters if k.hp > 0 and k.addr != u.addr and pulled.get(k.addr, 0.0) <= now
                  and math.hypot(k.x - m.x, k.y - m.y) <= 600)
        if grp < 3:
            continue
        nf = sum(1 for f in foes if f.hp > 0 and math.hypot(f.x - m.x, f.y - m.y) <= 700)
        if nf >= grp:
            continue
        best = (d, m.x, m.y)
    return (best[1], best[2]) if best else home


def load_sacrifice(path: str, now_wall: float, max_age: float = 60.0) -> set:
    """大脑发布的「喂野怪腾人口」名单（brain/tactics.py 的 recycle）：这些兵是故意送去给野怪打死的，保命这边不拉、不轮换。
    文件 {"units": [地址...], "wall": 写入时刻}；超过 max_age 秒没更新 = 大脑不在了，当空。"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if now_wall - float(d.get("wall", 0)) > max_age:
            return set()
        return {int(a) for a in d.get("units", [])}
    except Exception:                        # noqa: BLE001
        return set()


def pull_picks(danger, fighters, pulled: dict, now: float, vs_creeps: bool, lethal=frozenset()):
    """这一轮从 danger 里挑谁拉出来；返回 (要拉的, 被同时撤下人数挡下的, 英雄血过半没拉的)。

    lethal（09-21，unitmind）：按「在打它的人」算，TTD_CRIT 游戏秒内就会死的地址 —— 和丝血（CRIT_FRAC）一样不受 PULL_SHARE 上限。

    ★★ 09-16 两局决定性的仗，输法一模一样：
        000421 家门口  9 秒拉走 16 人（我 16 : 敌 26）-> 战损 18 : 0
        001312 家门口 23 秒拉走 20 人次（我 12 人）  -> 战损 21 : 3，
               Hmkg 604/775、Hamg 515/625 也被拉 —— 英雄 78%/82% 血在决战里被拉走
    MAX_PULL 管的是"一轮（100ms）最多几个"，不是"同一时刻最多几个在撤"；
    每人锁 LEASE 8 秒，于是一场大仗里大半个部队背对敌人在走，
    对面近战追着砍，被拉的人最后也全死了 —— 救下 0 个，输出少一半。

    打野不受限：野怪不追人，轮换拉扯是有效的（设计上要的打法）。
    打玩家：附近参战的 PULL_SHARE 以内（至少 1 个）；英雄持续受伤到七成以下即可拉。
    """
    urgent_hero = lambda t: t[0].is_hero and (t[0].hp <= t[0].hp_max * HERO_FLOOR or t[2] <= 0)
    order = sorted(danger, key=lambda t: (not urgent_hero(t), t[2]))
    if vs_creeps:
        return order[:MAX_PULL], 0, 0
    sel, capped, hero_kept = [], 0, 0
    for t in order:
        u = t[0]
        if pulled.get(u.addr, 0.0) > now:
            continue
        if u.is_hero and u.hp_max > 0 and u.hp >= u.hp_max * HERO_PULL_MIN and not urgent_hero(t):
            hero_kept += 1
            continue
        near = [f for f in fighters if f.hp > 0
                and math.hypot(f.x - u.x, f.y - u.y) <= PULL_NEAR]
        out = sum(1 for f in fighters if f.hp > 0 and pulled.get(f.addr, 0.0) > now
                  and math.hypot(f.x - u.x, f.y - u.y) <= PULL_NEAR + PULL_BACK)
        out += sum(1 for s in sel
                   if math.hypot(s[0].x - u.x, s[0].y - u.y) <= PULL_NEAR + PULL_BACK)
        crit = (urgent_hero(t) or ((not u.is_hero) and u.hp_max > 0 and u.hp <= u.hp_max * CRIT_FRAC)
                or u.addr in lethal)
        if len(sel) >= MAX_PULL or (not crit and out >= max(1, int(len(near) * PULL_SHARE))):
            capped += 1
            continue
        if crit and out >= max(1, int(len(near) * PULL_SHARE)):
            PULL_STATS["丝血不受上限"] += 1
        sel.append(t)
    return sel, capped, hero_kept


ATK_RCPT_DELAY = 0.3   # 09-16：补攻击令之后多久抽查当前订单（墙钟秒）


# ★★ 09-17 英雄被集火：按「谁在打谁」拉，不按掉血速度。
#   问题：拉残血的命令不够及时；常见一两个英雄孤军作战、不撤退，一直扛到阵亡。
#   v287 四个实例 61 次英雄阵亡全是打敌军（hero_deaths.py，战斗帧 + mod_fight 目标采样 + cmdlog）：
#     死前 3 秒血量中位 67%，从 >=60% 血到死中位 3.2 秒（p25 2.1）；死时 900 内敌军中位 15；
#     死前 2 秒典型读数「13/16、18/18、14/15、20/22 个敌人的攻击目标是它」—— 简单电脑整队集火英雄；
#     第一次 >=3 个敌人打它的时刻中位在死前 1.9 秒。mod_combat 其实挪过 58/61，最后一次中位在死前 3.1 秒 —— 晚、而且直线回家。
#   掉血外推要先看到掉血，而 15 个人一起打，第一秒掉的血还不多、第二秒已经来不及；
#   旧 HERO_PULL_MIN（打玩家英雄血过半不拉）更把 67% 血的英雄整个挡在外面。
#   所以英雄单独一条：身边 FOCUS_R 内有敌方作战单位时，每 FOCUS_EVERY 秒读最近 FOCUS_MAX 个的任务目标
#   （和 mod_fight.sample_targets / act-attack 同一条路：任务里的目标句柄对快照 handle_lo/hi），
#   >= FOCUS_PULL_N 个在打它就立刻拉，不看血量；拉的方向背离打它的人，身后有自己人就退到自己人身后（focus_escape_point）。
#   打野不走这条（打野时拉出再派回是正常轮换，和敌军交战才需要考虑是否回家）—— 只数敌方玩家的单位。
FOCUS_R = 900.0         # 英雄身边这么近的敌方作战单位才去读它在打谁
FOCUS_EVERY = 0.35      # 每个英雄多久读一轮（墙钟秒；3 倍速约 1 游戏秒）
FOCUS_MAX = 6           # 一轮最多读几个（离英雄最近的；一次读约 12ms。集火时最近 6 个里 >=3 个在打它）
FOCUS_BUDGET = 0.08     # 一轮（100ms 循环一圈）所有英雄合计最多花多少秒，读不完下一圈接着来 —— 别把拉残血的节拍拖慢
FOCUS_PULL_N = 3        # 这么多个敌人的攻击目标是它 -> 立刻拉
# 下面两个用于「读不到目标」的外推（见 focus_stats / hero_focus_danger）：
# 约六成敌方单位读不到任务目标，只数明确读到的会系统性低估集火人数。
FOCUS_EXTRAPOLATE_NEAR = 4    # 身边至少这么多敌人才外推（一两个敌人时样本太小，外推等于瞎猜）
FOCUS_EXTRAPOLATE_RATIO = 0.5 # 读到目标的那些里，至少这个比例在打英雄
FOCUS_BACK = 900.0      # 拉多远
FOCUS_BEHIND = 1500.0   # 这么远以内、在它身后的自己人算「部队」，退到部队身后

# ★★ 09-22 兵种技能（判据见 unitspells.py 开头那段）
USP_EVERY = 0.3         # 多久排一次（墙钟秒）
USP_MANA_TTL = 1.5      # 施法兵的蓝多久读一次（放完那只当场作废重读）
USP_TECH_TTL = 10.0     # 研究等级多久问一次引擎（变形术要女巫训练 2 级）
USP_HOLD = 1.2          # 下令后握多久指挥权（施法前摇，别被补攻击令 / 大脑改掉）
USP_LEASH_HOLD = 2.0    # 锁镣要一直引导：每拍续这么久，锁镣结束就还


# ★ 09-17 拉出来往牧师身边拉，让后排牧师接着加血。
#   交战帧（v287 四个实例近 16 局）：我方牧师 25% 的帧挂着 heal 0x000D005F（敌方 30%）—— 自动治疗是开着的，
#   可拉残血/集火/打野轮换都是沿直线退（朝家或背离野怪），退到哪儿有没有牧师全凭运气。
#   现在沿撤退方向、在身后 reach 以内找最近的己方治疗单位（HEALERS），站到它身后 HEALER_STAND；没有就照旧。
HEALER_STAND = 150.0

# ★ 09-21 牧师治疗目标要有优先级：先保主力英雄和战斗力最强的单位，不随意加血。
#   牧师原来全靠引擎自动治疗（heal 0x000D005F），它就近随便挑一个掉血的。AbilityData Ahea：每次 25 血、射程 250、5 蓝、冷却 1 秒、只奶有机单位。
#   射程很短，只在 PRIEST_R 以内挑（最多往前走 150），不为了奶人把牧师送进前排。
# ★ 09-22 会战中英雄阵亡过快（1-299 13 分），毫秒层本应及时响应，排查底层响应链路。
#   战斗帧 + 命令记录：两个英雄都是毫秒层 300ms 内下了撤退令、引擎也接了，可人没走 —— 血魔法师撤了 0.2 秒就原地变回攻击，
#   3 游戏秒后才又能动（= 缠绕根须对英雄 HeroDur1 3 秒，AbilityData AEer）；大法师被守望者盯上后 2 秒纹丝不动，
#   「撤离未脱险·改道」连下三次移动令全白费，站着被 6 个人打死。**被控住的英雄下移动令没用**。
#   能让英雄走不了的状态（buffs.json：码 -> 技能 -> 效果），能被驱散魔法解掉的另列。
DISABLE_BUFFS = (("BEer", "缠绕"), ("Bena", "诱捕"), ("Beng", "诱捕"), ("BUsl", "睡眠"), ("BUsp", "睡眠"),
                 ("Bcyc", "旋风"), ("Bcy2", "旋风"), ("BPSE", "眩晕"), ("BUim", "穿刺"))
DISPELLABLE = {"BEer", "Bena", "Beng", "BUsl", "BUsp", "Bcyc", "Bcy2"}
ORDER_DISPEL = 0x000D0059     # dispel（war3-game-analysis/order-ids.txt，同表 heal 0x000D005F 已实机验证）
DISPEL_MANA = 75.0            # Adis Cost1
DISPEL_REACH = 500.0 + 400.0  # Adis 施法距离 500，再给牧师走过去 400 的余量


def disabled_by(cmd, hero):
    """英雄身上有没有让它走不了的状态 -> (状态码, 中文) 或 None。一种状态查一次技能等级（= GetUnitAbilityLevel）。
    只在「撤退令发了却没挪窝」时才读（RetreatWatch 判出来的），不是每轮都读。"""
    for code, name in DISABLE_BUFFS:
        try:
            if (cmd.ability_level(hero.addr, code) or 0) > 0:
                return code, name
        except Exception:                    # noqa: BLE001
            return None
    return None


def dispel_pick(hero, units, mana_of=None):
    """离被控英雄最近、够得着（DISPEL_REACH）、蓝够（读不到蓝就先试）的牧师。"""
    cands = [u for u in units if u.type in PRIESTS and u.hp > 0 and math.hypot(u.x - hero.x, u.y - hero.y) <= DISPEL_REACH]
    if mana_of is not None:
        cands = [u for u in cands if (mana_of(u) if mana_of(u) is not None else DISPEL_MANA) >= DISPEL_MANA]
    return min(cands, key=lambda u: math.hypot(u.x - hero.x, u.y - hero.y), default=None)


PRIESTS = {"hmpr"}
ORDER_HEAL = 0x000D005F
# ★ 09-22 缺陷：强制攻击自己小兵（反补腾人口）时，牧师同时在给这个小兵加血。
#   决策明细：18 次「强制攻击自己的步兵腾人口」，大多数被打的那个前后 30 秒被牧师奶了 2~10 次
#   （1-316 03:12:31 那个：03:12:38 奶到 157、03:12:44 掉到 15 又被奶回 117）。两层：定向治疗没排除送死名单；
#   牧师的治疗开着自动施法，引擎自己也会奶掉血的友军。所以被打的兵附近 PRIEST_SAC_R 内的牧师关掉自动治疗，名单空了 / 走远了再开。
ORDER_HEAL_ON = 0x000D0060    # healon（war3-game-analysis/order-ids.txt）
ORDER_HEAL_OFF = 0x000D0061   # healoff
PRIEST_SAC_R = 900.0          # 治疗施法距离 250 + 牧师自动施法找目标会走过去的余量
PRIEST_R = 400.0
PRIEST_EVERY = 0.5        # 每个牧师多久挑一次（墙钟秒）
PRIEST_REPEAT = 3.0       # 同一个目标隔这么久才重下（中间自动治疗会接着奶它）
PRIEST_HERO_W = 4.0       # 受伤英雄的分数再乘这么多 —— 英雄先保
PRIEST_MIN_MISSING = 40.0 # 缺血少于这么多的不奶（一次 25 血，满血边上浪费）
PRIEST_HEAL_MANA = 5.0    # 治疗一发的蓝（AbilityData Ahea Cost1 = 5）


def priest_pick(priest, allies, skip=frozenset()):
    """这个牧师该奶谁：PRIEST_R 内、有机、非召唤物、缺血 >= PRIEST_MIN_MISSING 的自己人里，
    分数 = 战斗力（血上限 × 每秒伤害，英雄再 × PRIEST_HERO_W）× 缺血比例，取最高。没有返回 None。
    skip = 大脑的送死名单（sacrifice.json：强制攻击腾人口 / 喂野怪的兵），不奶。"""
    best = None
    for a in allies:
        if a.hp <= 0 or a.type in MECHANICAL or a.type in SUMMONS or a.type in WORKERS or a.addr in skip:
            continue
        missing = a.hp_max - a.hp
        if missing < PRIEST_MIN_MISSING or math.hypot(a.x - priest.x, a.y - priest.y) > PRIEST_R:
            continue
        power = a.hp_max * max(1.0, _unit_dps(a.type)) * (PRIEST_HERO_W if a.is_hero else 1.0)
        score = power * missing / max(1.0, a.hp_max)
        if best is None or score > best[0]:
            best = (score, a)
    return best[1] if best else None


def toward_healer(u, dx, dy, allies, reach):
    """(dx, dy) 是单位向量的撤退方向。身后（投影 > 0）reach 以内最近的己方治疗单位 -> 它身后 HEALER_STAND 的点；没有 -> None。"""
    best = None
    for a in allies:
        if a.addr == u.addr or a.hp <= 0 or a.type not in HEALERS:
            continue
        d = math.hypot(a.x - u.x, a.y - u.y)
        if d > reach or (a.x - u.x) * dx + (a.y - u.y) * dy <= 0.0:
            continue
        if best is None or d < best[0]:
            best = (d, a)
    if best is None:
        return None
    return best[1].x + dx * HEALER_STAND, best[1].y + dy * HEALER_STAND


def focus_count(hero, rows) -> int:
    """rows：[(敌方地址, (tlo, thi) 或 None)] -> 攻击目标是这个英雄的个数。"""
    me = (hero.handle_lo, hero.handle_hi)
    return sum(1 for _a, tg in rows if tg is not None and tuple(tg) == me)


def focus_stats(hero, rows) -> tuple[int, int, int]:
    """(在打这个英雄的, 读到目标的, 读不到目标的)。

    ★ 09-21：**读不到目标 ≠ 没在打它**，而 focus_count 把两者算成同一个 0。
    实测（inst1 1983 分钟心跳）：英雄身边 900 内有敌人的 20927 轮里，
    「读到 0 个在打它」占 **77.6%**、>=3 的只有 2.0%，而同期英雄阵亡 1.9 次/局 ——
    集火撤离一共只触发 123 次，和死亡次数同一个量级。
    实测也记过：敌方单位「没目标 2811 / 有目标但不是我方 1488 / 对上我方 97」，
    约六成读不到目标。在这样一个数据源上要求「>=3 个明确在打它」，等于几乎不可能成立。
    问题在于 0 同时表示「没人打它」和「读不到谁在打它」，两件事要分开记。"""
    me = (hero.handle_lo, hero.handle_hi)
    focused = known = unknown = 0
    for _a, tg in rows:
        if tg is None:
            unknown += 1
            continue
        known += 1
        if tuple(tg) == me:
            focused += 1
    return focused, known, unknown


def hero_focus_danger(focused: int, known: int | None = None, unknown: int = 0, near: int = 0) -> bool:
    """真实攻击目标达到阈值就撤；英雄当前血量不能再次抬高这个阈值。

    只传 focused 时是老行为（明确读到 FOCUS_PULL_N 个在打它）。
    传了 known/unknown/near 时补一条**按比例外推**：读到目标的那些敌人里过半在打英雄、
    而且身边敌人够多，就用这个比例估算读不到的那批 —— 因为读不到的和读到的没有理由系统性不同。
    这条只放宽「读不到」造成的漏判，不放宽「确实在打别人」的情况（比例不够就不外推）。"""
    if focused >= FOCUS_PULL_N:
        return True
    if not known or near < FOCUS_EXTRAPOLATE_NEAR:
        return False
    ratio = focused / known
    if ratio < FOCUS_EXTRAPOLATE_RATIO:
        return False
    return focused + unknown * ratio >= FOCUS_PULL_N


HARBOR_GROUP_R = 1200.0   # 大部队：这么近以内扎堆的自己人
HARBOR_MIN = 4            # 至少这么多个非英雄作战单位才算「大部队」
HARBOR_ROUTE_CLEAR = 500.0  # 英雄走过去的路离每个敌人至少这么远
HARBOR_SAFE_R = 900.0     # 大部队自己离敌人这么近就不算安全港


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    q = 0.0 if l2 <= 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + q * dx), py - (ay + q * dy))


def friendly_harbor(hero, threats, ours):
    """英雄接触前预警该往哪撤：自己的大部队（扎堆最多的那群非英雄作战单位的重心）。
    ★ 09-22 1-298 24 分 大法师在地图最北边的水域长时间闲置，而主力已经在南边开打。
      01:10:55「英雄预警撤退：Hamg 725/725 身边 3000 内敌 6 个、我方 2 个」—— 它落在主力后面，身后没有自己人，
      focus_escape_point 就直直背离敌人退 1600，退到了地图最北边的水边；主力在西边回防，它一直没回来。
      路过敌人 HARBOR_ROUTE_CLEAR 以内、或者大部队自己就在敌人 HARBOR_SAFE_R 内（正在挨打）-> None，照旧背离敌人。"""
    grp = [u for u in ours if u.hp > 0 and not u.is_hero and u.addr != hero.addr and u.type not in WORKERS]
    if len(grp) < HARBOR_MIN:
        return None
    best = max(grp, key=lambda u: sum(1 for v in grp if math.hypot(u.x - v.x, u.y - v.y) <= HARBOR_GROUP_R))
    pack = [v for v in grp if math.hypot(best.x - v.x, best.y - v.y) <= HARBOR_GROUP_R]
    if len(pack) < HARBOR_MIN:
        return None
    cx, cy = sum(v.x for v in pack) / len(pack), sum(v.y for v in pack) / len(pack)
    if any(math.hypot(f.x - cx, f.y - cy) <= HARBOR_SAFE_R for f in threats):
        return None
    if any(_seg_dist(f.x, f.y, hero.x, hero.y, cx, cy) < HARBOR_ROUTE_CLEAR for f in threats):
        return None
    return cx, cy


def focus_escape_point(hero, attackers, allies, home, back=FOCUS_BACK):
    """被集火的英雄往哪拉：背离打它的人的重心；身后 FOCUS_BEHIND 内有牧师就到牧师身后（toward_healer），
    否则身后有自己人就退到他们的重心再往后 300。

    打它的人围在身边（重心离它不到 150）方向不可靠，退回朝家的方向。返回 (x, y)。
    """
    hx, hy = hero.x, hero.y
    dx = dy = 0.0
    if attackers:
        ax = sum(a.x for a in attackers) / len(attackers)
        ay = sum(a.y for a in attackers) / len(attackers)
        dx, dy = hx - ax, hy - ay
    n = math.hypot(dx, dy)
    if n < 150.0:
        dx, dy = home[0] - hx, home[1] - hy
        n = math.hypot(dx, dy)
    if n < 1e-6:
        return hx, hy
    dx, dy = dx / n, dy / n
    healer = toward_healer(hero, dx, dy, allies, FOCUS_BEHIND)
    if healer is not None:
        return healer
    behind = [a for a in allies
              if a.addr != hero.addr and math.hypot(a.x - hx, a.y - hy) <= FOCUS_BEHIND
              and (a.x - hx) * dx + (a.y - hy) * dy > 0.0]
    if behind:
        cx = sum(a.x for a in behind) / len(behind) + dx * 300.0
        cy = sum(a.y for a in behind) / len(behind) + dy * 300.0
        if math.hypot(cx - hx, cy - hy) >= back * 0.5:
            return cx, cy
    return hx + dx * back, hy + dy * back


# SPELL_ABIL（技能名 -> 引擎技能代码）09-18 起从 SPELLS 派生，定义在 SPELLS 旁边 —— 这里原来有一份手写的。


def reject_reason(ability, mana, cost) -> str:
    """施法被引擎退回时，当场读到的状态 -> 原因（心跳「施法被退原因」计数）。

    ★ 09-16 以前下令失败只返回 0、不说原因，没学 / 没蓝 / 冷却分不开，只能统一退避 10 秒。
      现在蓝、技能和等级、冷却标志都读得到。
    ability：w3cmd.find_ability 的结果（None = 读不到，{"level": 0} = 没有这个技能）。
    """
    if ability is None:
        return "读不到技能"
    if not ability.get("level"):
        return "没学"
    if ability.get("flags", 0) & 0x200:
        return "冷却中"
    if mana is not None and mana < cost:
        return "蓝不够"
    return "别的原因"


CAST_RCPT_DELAY = 1.2   # 09-16：施法令发出后多久看技能进没进冷却（墙钟秒，3 倍速约 3.6 游戏秒）


CAST_HOLD = 1.5   # 09-16：英雄刚下施法令后，补攻击令多久不碰它（墙钟秒）
POINT_CAST_NEAR = 800.0   # 09-16：暴风雪/烈焰风暴施法距离；回执按下令时英雄离落点远近分开记


def point_path_label(dist: float, path: str = "动作41") -> str:
    """点施法回执的路径标签：走的哪条路 + 下令时英雄离落点在施法距离内（近）还是要走过去（远）。

    ★ 09-16 暴风雪走动作 41：此前 16 次都没进冷却，031651 局第一次进冷却 1 次 ——
      不是全不通，猜是射程内才成、要走过去就丢。先把远近分开记，数据说话再改。
    ★ 09-16 查明根因：动作 41 这条路对行为类订单基本不执行；
      新路「上下文点」换了下令方式（w3cmd.cast_point_ctx）。路径从 last_cast_path 来。
    """
    return f"{path}·近" if dist <= POINT_CAST_NEAR else f"{path}·远"


def refill_candidates(units, now, rotating, dodged, pulled, cast_hold):
    """补攻击令挑谁：引擎说没任务、没在轮换/躲暴风雪/撤退、**也没在施法保护期里**的。

    ★ 09-16 施法回执：暴风雪 4 次、风暴之锤 1 次令接了却没进冷却，1.2 秒后英雄的订单是攻击令 0x000D000F
      或槽空，**一次也没挂着施法令** —— 施法令发出后英雄一时没订单没任务，像个闲兵，
      100ms 后被这条补攻击令改成去打人（0x000D000F 只有这里发；策略层防守发 smart）。
      水元素这类瞬发技能来得及放完，所以 5/5 进冷却。
    返回 (候选, 因为施法保护被挡下的个数)。
    """
    out, held = [], 0
    for u in units:
        if not (u.task_known and u.has_task is False):
            continue
        if u.addr in rotating or u.addr in dodged or pulled.get(u.addr, 0.0) > now:
            continue
        if cast_hold.get(u.addr, 0.0) > now:
            held += 1
            continue
        out.append(u)
    return out, held


CREEP_REFILL_PAD = 300.0
CREEP_REFILL_MAX = 6
CREEP_CORE_DPS_BAND = 0.85


def creep_focus_target(creeps):
    """毫秒补令和轮换归队统一打高输出核心，避免底层把策略层的集火重新打散。"""
    alive = [c for c in creeps if c.hp > 0]
    if not alive:
        return None
    peak = max((DPS.get(c.type, 0.0) for c in alive), default=0.0)
    if peak <= 0:
        return min(alive, key=lambda c: (c.hp, c.addr))
    core = [c for c in alive if DPS.get(c.type, 0.0) >= CREEP_CORE_DPS_BAND * peak]
    return min(core, key=lambda c: (c.hp * (1.0 + 0.06 * max(0.0, ARMOR.get(c.type, 0.0))),
                                    -DPS.get(c.type, 0.0), c.addr))


def creep_refill_picks(units, camp, now, rotating, dodged, pulled, cast_hold):
    """野怪醒来后，给射程边缘空闲的兵立即补攻击令。

    策略层一拍之间有数个游戏秒；018~025 局反复出现肉盾已在打、远程英雄上一只目标死亡后槽空，
    下一拍才重新收到点名攻击。只在野怪已掉血/已有订单或任务时生效，避免抢在肉盾前唤醒营地。
    """
    awake = [c for c in camp if c.hp > 0 and
             (c.hp < c.hp_max or (getattr(c, "order_known", False) and bool(c.order))
              or (getattr(c, "task_known", False) and c.has_task is True))]
    if not awake:
        return [], 0
    idle, held = refill_candidates(units, now, rotating, dodged, pulled, cast_hold)
    out = []
    for u in idle:
        if u.hp_max > 0 and u.hp <= AGGRO_STAY_FRAC * u.hp_max:
            continue
        reach = RANGE.get(u.type, 100.0) + CREEP_REFILL_PAD
        targets = [c for c in awake if _dist(u, c) <= reach]
        if targets:
            out.append((u, creep_focus_target(targets)))
    return out, held


# ★★ 09-16 补攻击令**跟打**。
#   「谁在打谁」采样（mod_fight targets-*.jsonl）+ 按实例的 cmdlog 归因（单位 10 秒内收到过谁的令），2246 个我方单位·轮：
#     敌方平均 4.58 人打同一目标（中位 3）；我方 没有模块令（策略层/引擎自找）3.43、**本模块补攻击令 3.07**（占 30%）、
#     被本模块拉出/撤退/轮换的 2.45（单打独斗 46%）。
#   ⚠ 更正：第一版这里写「补攻击令 1.9 人、单打独斗 72%，敌方 7.0」—— 那是按**当前订单槽**归的类：
#     补攻击令发出后订单槽很快变空（攻击挂到任务上），而槽里挂 0x000D000F 的 405 个单位·轮只有 40 个 10 秒内收到过补攻击令。
#     订单槽 ≠ 下令的来源（经验：量之前先问这个数是不是在量你以为的东西）。
#   原因：每个闲兵各自按 target_score 挑，而分数里距离项很重（远程 1+2000/d、近战 1+800/d），
#   于是各挑各身边那个；也没有"加入自己人正在打的目标"这一项。
#   改法：最近 FOCUS_TTL 秒内已经有 k 个自己人被补令去打这个目标，分数 ×(1 + FOCUS_JOIN×k)；
#   同一目标最多 focus_cap 个（剩余血 / 60，至少 3）防过量伤害 —— 满了就挑别的。同一批里前面挑的后面就看得见。
#   ⚠ 只看得见本模块自己补的令；策略层 smart 和引擎自己找的目标看不见（要解任务对象，一个 10ms）。
FOCUS_JOIN = 0.6
FOCUS_TTL = 3.0


def focus_cap(f) -> int:
    return max(3, int(math.ceil(max(0, f.hp) / 60.0)))


FOCUS_HERO_PAD = 300.0   # v292 远程集火英雄：射程 + 这么远以内的敌方英雄才算够得着
FOCUS_HERO_SAME = 8.0    #   同一个单位打同一个英雄，这么久内不重下（重复下令打断攻击）
FOCUS_HERO_SWITCH = 2.0  #   换目标至少隔这么久


def focus_hero(u, foes):
    """★★ v292 团战远程集火敌方英雄（09-17 设计要求：团战时所有远程集火同一个敌方英雄；有两个时先打弱的、再打强的）。

    远程（射程 > MELEE_RANGE）够得着（射程 + FOCUS_HERO_PAD）的敌方英雄里挑有效血最少的（剩余血 ×(1+0.06×护甲)）；近战或一个都够不着返回 None。
    ⚠ 和 war3ai.ps1 的 W3-WeakestHero（W3-DefendAssign 远程先走这条）是同一条规则，改一边要改另一边。"""
    rng = RANGE.get(u.type, 100.0)
    if rng <= MELEE_RANGE:
        return None
    reach = rng + FOCUS_HERO_PAD
    best, best_e = None, float("inf")
    for f in foes:
        if f.hp <= 0 or not f.is_hero or math.hypot(f.x - u.x, f.y - u.y) > reach:
            continue
        e = f.hp * (1.0 + 0.06 * max(0.0, ARMOR.get(f.type, 0.0)))
        if e < best_e:
            best, best_e = f, e
    return best


# ★ 09-18「别打召唤物」：补攻击令只管引擎说没任务的兵；已经被引擎自动索敌拉去打骷髅/水元素的兵没人管。
#   身边最近的敌人是召唤物、而射程 + SUMMON_LEAVE_R 内有正经目标的兵，读它的任务目标（一个约 10ms），
#   真在打召唤物就改打正经目标里 target_score 最高的。
SUMMON_LEAVE_R = 350.0     # 射程 + 这么远内有非召唤物的敌人才换（近战射程 100 -> 450 内）
SUMMON_CHECK_EVERY = 1.5   # 同一个兵这么久查一次


SUMMON_BUDGET = 0.06       # 一轮读任务最多花这么多秒（一次约 15~20ms）


def summon_candidates(units, foes, skip) -> list:
    """可能在打召唤物的兵：射程 + 200 内**有**敌方召唤物（不要求是最近的那个），且射程 + SUMMON_LEAVE_R 内有正经目标。
    ★ 09-18 二：原来要求「最近的敌人是召唤物」—— 兵被引擎自动锁上水元素之后、最近的敌人换成了别人，就再也不查了。
      战斗帧 × 谁在打谁（020 / 022 局）：几十次我方兵在打敌方 2 级水元素，其中大半射程 + 350 内就有正经目标。
    skip(u) 为真的（被拉、轮换、躲暴风雪、施法保护期）不查；召唤物自己不查。离召唤物近的在前。"""
    out = []
    for u in units:
        if u.hp <= 0 or u.type in SUMMONS or skip(u):
            continue
        rng = RANGE.get(u.type, 100.0)
        near = [(math.hypot(f.x - u.x, f.y - u.y), f) for f in foes if f.hp > 0]
        ds = [d for d, f in near if f.type in SUMMONS and d <= rng + 200.0]
        if not ds:
            continue
        if not any(f.type not in SUMMONS and d <= rng + SUMMON_LEAVE_R for d, f in near):
            continue
        out.append((min(ds), u))
    return [u for _d, u in sorted(out, key=lambda t: t[0])]


def summon_switch_target(u, foes):
    """在打召唤物的 u 改打谁：射程 + SUMMON_LEAVE_R 内非召唤物里 target_score 最高的（远程够得着英雄先打最脆的英雄）；没有返回 None。"""
    rng = RANGE.get(u.type, 100.0)
    cand = [f for f in foes if f.hp > 0 and f.type not in SUMMONS
            and math.hypot(f.x - u.x, f.y - u.y) <= rng + SUMMON_LEAVE_R]
    if not cand:
        return None
    h = focus_hero(u, cand)
    return h if h is not None else max(cand, key=lambda f: target_score(u, f))


def pick_refill(idle, foes, recent: dict, now: float, towers=()):
    """-> [(单位, 目标, 是不是跟打)]。

    recent：{目标地址: {单位地址: 到期时刻}}，原地清掉过期的、写入这一批挑的（下令失败由调用方撤掉）。
    towers：敌方能打人的建筑（enemy_towers）。★ 09-22（1-333）：**英雄**不补打站在塔射程里的目标 ——
      大法师被点名去追残血先知、追进地洞射程站了 7 秒；本模块的补攻击令同样会把闲着的英雄派到塔下。塔外没有目标的英雄这一轮不补。
    """
    for t in list(recent):
        live = {a: until for a, until in recent[t].items() if until > now}
        if live:
            recent[t] = live
        else:
            del recent[t]
    alive_all = [f for f in foes if f.hp > 0]
    alive_safe = [f for f in alive_all if not in_tower_range(f, towers)] if towers else alive_all
    out = []
    for u in idle:
        alive = alive_safe if u.is_hero else alive_all
        best, bs, joined = None, -1.0, False
        hero_t = focus_hero(u, alive)          # v292：远程够得着敌方英雄就打最脆的那个，不受每个目标人数上限约束
        if hero_t is not None:
            best, joined = hero_t, any(a != u.addr for a in recent.get(hero_t.addr, {}))
        for f in ([] if best is not None else alive):
            k = sum(1 for a in recent.get(f.addr, {}) if a != u.addr)
            if k >= focus_cap(f):
                continue
            sc = target_score(u, f) * (1.0 + FOCUS_JOIN * k)
            if sc > bs:
                best, bs, joined = f, sc, k > 0
        if best is None and alive:          # 全都满了：退回各挑各的
            best, joined = max(alive, key=lambda f: target_score(u, f)), False
        if best is None:
            continue
        for t in [t for t, who in recent.items() if u.addr in who and t != best.addr]:   # 换了目标：旧的那条不再算它
            recent[t].pop(u.addr, None)
            if not recent[t]:
                del recent[t]
        recent.setdefault(best.addr, {})[u.addr] = now + FOCUS_TTL
        out.append((u, best, joined))
    return out


def cast_receipt(ability, order=None, spell_order=None) -> str:
    """施法令发出 CAST_RCPT_DELAY 秒后，这个技能的状态 -> 回执类别（心跳「施法回执」计数）。

    ★ 09-16 施法令返回 1 只说明引擎接了令（点施法/目标施法以前「效果只有弱证据」）。
      技能真的放出去就会进冷却：技能的冷却标志（flags & 0x200；实测水元素放完 18.0 秒按倍速递减，到 0 清位）。
    ability：w3cmd.find_ability 的结果（None = 读不到，{"level": 0} = 没有这个技能）。
    order / spell_order：没进冷却时英雄的当前订单（w3cmd.current_order）和这个技能的订单号 ——
      ★ 012306 局首批：水元素/雷霆一击 5/5 进冷却，风暴之锤、暴风雪 4/4 没进冷却；
        分不清「还在走进射程」和「令没执行」，所以没进冷却时再看英雄是不是还挂着这条施法令。
    """
    if ability is None:
        return "读不到"
    if not ability.get("level"):
        return "没学"
    if ability.get("flags", 0) & 0x200:
        return "进冷却"
    if order is None:
        return "没进冷却·槽空"
    if spell_order is not None and order.get("order") == spell_order:
        return "没进冷却·还挂着施法令"
    return f"没进冷却·订单{order.get('order', 0):08X}"


CORPSE_KEEP = 25.0     # 09-18 尸体记多久（墙钟秒，3 倍速约 75 游戏秒；尸体约 88 游戏秒后腐烂）
TOGGLE_TRUST = 20.0    # 09-18 开关（献祭）开令成功后这么久内信它还开着；再久在仗里补一次开令（引擎没蓝会自己关）


def corpse_events(prev: dict, units, me: int):
    """这一轮新死的、会留尸体的单位 -> (这一轮活着的 {地址: 单位}, [(x, y)])。操纵死尸（_rule_corpses）数尸体用。

    上一轮活着、这一轮血 <= 0 或不见了：自己的单位不见了就算死（工人进矿不算，已排除）；
    别人的单位不见了可能只是进了迷雾，所以要最后一眼血 <= 30% 才算。
    英雄、建筑、召唤物、机械不留尸体（召唤物按 SUMMONS 排，里面有野怪以外的少量误差，只影响计数）。"""
    alive = {u.addr: u for u in units if u.hp > 0}
    dead = {u.addr for u in units if u.hp <= 0}
    out = []
    for a, p in prev.items():
        if a in alive:
            continue
        if p.is_hero or p.type in SUMMONS or p.type in MECHANICAL or p.type in WORKERS or not _is_unit(p):
            continue
        if a in dead or p.owner == me or p.hp <= 0.3 * max(1, p.hp_max):
            out.append((p.x, p.y))
    return alive, out


def run_followups(pending, now: float, cmd, claims, fighters, units, pulled):
    """施法后续（_rule_escape 的 then）：到期的执行，没到期的留着 -> (留下的, Counter{技能·动作·结果})。

    ("move", x, y)：闪烁逃生之后接着跑 —— 只在它还被本模块拉着撤时下（保命租约本模块握着，不再 take）。
    ("attack", 目标地址)：疾风步 / 闪烁追残血英雄之后打它 —— 撤退中不打（听保命的），目标没了不打，抢 cast 级租约。"""
    keep, done = [], Counter()
    for item in pending:
        due, addr, then, name = item
        if due > now:
            keep.append(item)
            continue
        hero = next((x for x in fighters if x.addr == addr and x.hp > 0), None)
        held = pulled.get(addr, 0.0) > now
        kind = then[0]
        if hero is None:
            done[f"{name}·{kind}·英雄没了"] += 1
            continue
        if kind == "move":
            if not held:
                done[f"{name}·move·不在撤了"] += 1
                continue
            ok = cmd.move_to(addr, then[1], then[2], ORDER_MOVE)
        else:
            tgt = next((f for f in units if f.addr == then[1] and f.hp > 0), None)
            if held or tgt is None:
                done[f"{name}·attack·{'撤退中' if held else '目标没了'}"] += 1
                continue
            if not claims.take(addr, 1.5, prio=PRIO["cast"]):
                done[f"{name}·attack·抢不到指挥权"] += 1
                continue
            try:
                ok = cmd.target_order(addr, tgt.addr, ORDER_ATTACK, tgt.x, tgt.y)
            finally:
                claims.give_back(addr)
        done[f"{name}·{kind}·{'ok' if ok else '被拒'}"] += 1
    return keep, done


def skill_stats(casts, landed, rejects, unlearned, mana_low) -> str:
    """心跳按技能合一行（09-18 约定：心跳按技能分计数，含下令/进冷却/被退原因）：
    「技能 下令/进冷却/被退/没学/缺蓝：名 a/b/c/d/e、…」，按总数从多到少。进冷却来自施法回执抽查（最多同时挂 4 个），是样本；
    被退原因看同一行前面的「施法被退原因（技能·原因×n）」。⚠ 不写「被退 数字」—— match_review 用 r"被退 (\\d+)" 取总数。"""
    names = set(casts) | set(landed) | set(rejects) | set(unlearned) | set(mana_low)
    if not names:
        return "技能 下令/进冷却/被退/没学/缺蓝：无"
    row = lambda n: (casts.get(n, 0), landed.get(n, 0), rejects.get(n, 0), unlearned.get(n, 0), mana_low.get(n, 0))  # noqa: E731
    order = sorted(names, key=lambda n: (-sum(row(n)), n))
    return "技能 下令/进冷却/被退/没学/缺蓝：" + "、".join(f"{n} " + "/".join(str(v) for v in row(n)) for n in order)


def attack_receipt(order, target_pair, has_task=None, task=None) -> str:
    """补攻击令之后抽查到的当前订单 / 任务 -> 类别（心跳按类别计数）。

    ★ 09-16 攻击令 0x000D000F **从没在实机验证过**，1311 次「补攻击令」只证明令发出去了。
      w3cmd.current_order 能读订单号和目标句柄之后，就能被动验证：下令后这个兵的当前订单
      是不是攻击令、目标是不是我们点的那个。
    ★ 005754 首批 15 次：0.3 秒后**订单槽全是空的**（有任务 13、闲着 1）—— 攻击挂在任务上。
      所以槽空又有任务时再读任务（w3cmd.current_task）：任务的目标是不是我们点的那个。
    order：current_order 的结果（None = 订单槽空/读不到）；target_pair：下令时目标的快照句柄对；
    task：current_task 的结果（只在槽空且有任务时才读）。
    """
    want = tuple(target_pair)
    if order is None:
        if has_task is None:
            return "单位没了"
        if not has_task:
            return "槽空·闲着"
        if task is None:
            return "槽空·有任务"
        if task.get("target") is not None:
            return "任务·目标对上" if tuple(task["target"]) == want else "任务·别的目标"
        return "任务·对点" if task.get("cls") == "task_point" else f"任务·{task.get('cls')}"
    if order["order"] == ORDER_ATTACK:
        return "攻击令·目标对上" if order["target"] == tuple(target_pair) else "攻击令·换了目标"
    return f"订单{order['order']:08X}"


def home_of(snap) -> tuple[float, float] | None:
    """家在哪：用**建筑**的重心。

    建筑不会动，所以"连续几拍坐标不变"这种启发式都不需要 ——
    直接按血量上限挑出建筑就行（战斗单位没有 600 血还不动的）。
    观察比维护一张 rawcode 表可靠。
    """
    # ⚠ 09-16 更正：原来是「血量上限 >= 400 且不是工人」—— 步兵 420、火枪 535、骑士、英雄全都满足，
    #   这个「家」其实是建筑 + 整支军队的重心，部队走到哪家就漂到哪（整队撤朝家退、家门口判据全跟着漂）。
    #   改成真的只用建筑（is_building）；读不到 units.json 时退回旧判据，总比没有家强。
    b = [u for u in snap.mine if is_building(u)]
    if not b:
        b = [u for u in snap.mine if u.hp_max >= 400 and u.type not in WORKERS]
    if not b:
        return None
    return sum(u.x for u in b) / len(b), sum(u.y for u in b) / len(b)


# 攻击的订单号：**从数据认出来的**，不是从表里查的 ——
# order-ids.txt 里没有 attack 这一条，而 mod_fight 录的两场仗里
# 敌方 58%/89% 的帧都是这个号，我方只有 6%/21%。
ORDER_ATTACK = 0x000D000F
ORDER_MOVE = 0x000D0012

# ★ 09-22 撤退令回执。1 号实例 6 局：本模块的撤退移动令有 48% 在下令后 2 秒内没让单位离目的地近 150
#   （英雄 55%，引擎都回了「接了」）；1-333 22 分大法师收到三次撤退令，原地站了 7 秒被地洞射死。
#   战斗帧的订单字段对移动令几乎全是 0（移动中的帧 15173 次是 0、12 次是 0x000D0012），分不清三种可能：
#     被控住（buff 表不全）/ 撤退令被别的令顶掉 / 令还挂着但寻路没出来（RetreatWatch 0.4 秒没挪就重下，会把寻路清零）。
#   所以撤退令发出 RT_DELAY 后看一眼：走近了 = 在走；没走近就读订单槽、任务、（英雄）身上的 buff，按 retreat_receipt 分类计数。
RT_DELAY = 0.6          # 墙钟秒（3 倍速约 2 游戏秒，英雄能走 600）
RT_PROGRESS = 60.0      # 离目的地近了这么多 = 在走
RT_MAX_PENDING = 6      # 最多同时等几个回执（一轮最多查 1 个，一次读订单/任务约 12~16ms）
RT_BUFF_EVERY = 1.5     # 英雄没走动时读一次技能列表（找 B 开头的 buff）至少隔这么久（一次读 10 个左右，约 120ms）
# ★ 09-22 第一轮回执（16:30~16:40）：英雄撤退没走的 4 次全是「订单被换成 000D000F」、身上只有 BHab/Bhea（没被控）；
#   16:36:17 本模块给大法师补了一条攻击令，之后 3 秒连下 6 次撤退令（动作 41），0.6 秒后订单槽里仍是那条攻击令 ——
#   动作 41 的「接了」只说明调用走完了（看的是 DLL 自己写的 issued），不说明引擎换了令。
#   A/B：英雄的撤退令 RT_KERNEL_SHARE 走 JASS IssuePointOrder 那条路（标签「内核」，演练台放暴风雪 3/3），
#   其余照旧动作 41；回执的路径名带「内核 / 动作41」，同一局里比。兵照旧动作 41。
#   ★ A/B 结果（16:47~17:11，同一批局）：英雄撤退走内核 13 次里 12 次在走；走动作 41 的 8 次里 6 次，没走的 2 次
#     「下令当下」订单槽里仍是原来的攻击令（动作 41 当场就没顶掉它）；兵全走动作 41，拉残血/改道约三成没走、多是被攻击令占着。
#     -> 本模块所有撤退 / 脱离 / 躲技能的移动令都走 retreat_move，retreat_move 全部走内核（留着比例开关，要再做对照时调它）。
RT_KERNEL_SHARE = 1.0


def retreat_receipt(progress, order, task, target_xy) -> str:
    """撤退令回执分类（心跳「撤退令回执」）。progress = 下令时离目的地 - 现在离目的地；
    order = w3cmd.current_order（None = 订单槽空）；task = w3cmd.current_task（None = 没任务）；target_xy = 撤退目的地。"""
    if progress >= RT_PROGRESS:
        return "在走"
    if order is not None:
        oid = int(order.get("order") or 0)
        if oid == ORDER_MOVE:
            ox, oy = order.get("x"), order.get("y")
            same = (target_xy is None or ox is None or oy is None
                    or math.hypot(ox - target_xy[0], oy - target_xy[1]) <= 64.0)
            return "挂着撤退令没走" if same else "挂着别处的移动令没走"
        return f"订单被换成{oid:08X}"
    if task is not None:
        cls = task.get("cls") or ""
        if cls == "task_target":
            return "撤退令没了·任务在打人"
        if cls == "task_point":
            return "撤退令没了·任务是点"
        return "撤退令没了·任务" + (cls or "读不出类名")
    return "撤退令没了·闲着"
# 和策略层 W3-TargetScore 同一套（治疗 ×6、施法 ×2.5）
HEALERS = {"hmpr", "ohwd", "uobs", "edoc"}
CASTERS = {"oshm", "hsor", "edry", "unec", "otbk"}


def main() -> int:
    modlog.tee_stdout("mod_combat")
    note(f"模块启动（独立进程，100ms 一轮）｜{HERO_SAFETY_VERSION}")
    retreat_watch = RetreatWatch()
    hall_memory = {}             # 大厅刚被拆后仍把原位置当作短期守家战场（见 recent_halls）
    from battlecommit import BattleCommit
    battle_commit, model_commit = BattleCommit(), BattleCommit()
    # Session 自己管重连 —— 游戏重启之后接回去。
    # 不这么做的话，模块会连着一个已经死掉的进程继续空转，
    # 而且日志里一行错误都没有（实测发生过，白跑了二十分钟）。
    ses = Session()
    last_match = None
    generations = {}
    claims = Claims("combat")
    last_engage = 0.0
    hp_prev: dict[int, tuple[float, float]] = {}     # 地址 -> (血量, 时刻)
    hp_win: dict[int, deque] = {}                    # 地址 -> [(时刻, 血量)]，掉血速率按窗口算
    saved = 0
    polls = 0
    pulled: dict[int, float] = {}   # 地址 -> 到期时刻，发布给策略层
    pull_seen: dict[int, tuple[float, float]] = {}   # 09-22 地址 -> (撤退期限, 第一次看到这个期限的时刻)：提前放手按它算握了多久
    solo: dict[int, float] = {}     # 孤军：血满也别派回去（见 publish_solo）
    rotating: dict[int, tuple[float, int]] = {}   # 轮换出去的：地址 -> (时刻, 营地等级)
    rot_skip: 'Counter[str]' = Counter()   # 这一分钟轮换没发生的理由（0 必须能解释）
    rot_out = 0
    rot_back = 0
    squad_ret = 0
    home_hold = 0         # 09-15：明显要输、但在家门口所以没撤的次数
    home_break = 0        # 027：压倒性劣势打破家门口死守，整队退到大厅背敌一侧
    pull_capped = 0       # 09-16：打玩家时因为同时撤下来的人到顶而没拉的（人·轮）
    pull_hero_kept = 0    # 09-16：打玩家时英雄血过半没拉的（人·轮）
    focus_at = {}         # 09-17：英雄地址 -> 上次读集火的时刻（见 FOCUS_R 上面那段）
    focus_reads = focus_hot = focus_pulls = focus_noclaim = 0
    focus_unread = focus_rows = focus_extrap = 0   # 读不到目标的敌人数 / 读过的总数 / 靠外推救下的次数
    to_healer = 0         # 09-17：拉残血/打野轮换拉出时退到了牧师身后的次数（见 toward_healer）
    tower_fix = 0         # 09-22：撤退目的地在塔射程里 / 直线穿过塔射程、改了落点的次数（tower_safe_point；0 = 没遇到或没生效）
    focus_last = {}       # 09-17：英雄地址 -> 最近一轮读到几个敌人在打它（喝药判据用）
    bag_cache = {}        # 09-17：英雄地址 -> (读背包的时刻, 背包)；读一次约 150ms，只在判据成立时读、3 秒内复用
    item_cool = {}        # 09-17：(英雄地址, 物品码) -> 这之前不再试（用过 / 背包里没有）
    item_used = Counter() # 09-17：物品·结果 计数（心跳）
    last_item = 0.0
    danger_home_hold = 0  # 09-22：守家、英雄血够，不做接触前预警撤退的次数（home_fight；0 = 没遇到或没生效）
    calm_note_at = {}     # 09-22：英雄地址 -> 上次记「没挨打不拉」的时刻（3 秒一行）

    def hero_fire(u, t_now):
        """09-22 英雄是不是正在挨打（under_fire：掉血窗口 hp_win + 集火读数 focus_last）。"""
        return under_fire(hp_win.get(u.addr), u.hp, u.hp_max, t_now,
                          focus_last.get(u.addr, 0), t_now - focus_at.get(u.addr, -1e9))

    focus_peak = Counter()   # 每轮读到的「在打它的人数」分布（0 / 1~2 / 3~5 / 6+）
    pull_home_kept = 0    # 09-16：家门口大仗没拉的（人·轮，见 home_brawl）
    pull_no_threat = 0    # 09-22：按掉血该拉、但身边 PULL_THREAT_R 内没敌人（持续伤害 / 回血抖动）没拉的（人·轮）
    home_hero_pull = 0    # 09-16 晚：家门口挨打的英雄进了拉残血候选（人·轮）
    home_hero_kept = 0    # 09-21：家门口没被集火、留下继续打的英雄（人·轮）——0 就是这条新路没生效
    mana_seen = {}        # 09-16：英雄地址 -> (读的时刻, 蓝量或 None)
    mana_low = {}         # 09-16：时机到了但蓝不够、没下令的（技能 -> 人·轮）
    mana_fail = 0         # 09-16：读蓝失败次数（0 以外要查通道）
    atk_pending = []      # 09-16：攻击令回执待抽查 (单位地址, 目标句柄对, 下令时刻)，最多 3 个
    atk_rcpt = {}         # 09-16：抽查结果 类别 -> 次（attack_receipt）
    rt_pending = []       # 09-22：撤退令回执待看 [单位地址, 目的地 x, y, 下令时刻, 下令时离目的地, 哪条路下的]
    rt_rcpt = Counter()   # 09-22：回执 (路径, 类别) -> 次（retreat_receipt）
    rt_buffs = Counter()  # 09-22：英雄撤退令没走动时身上挂的 buff 码 -> 次
    rt_buff_at = [0.0]    # 上一次读英雄技能列表的时刻（RT_BUFF_EVERY）

    def retreat_move(u, tx, ty):
        """撤退移动令 -> (下成没有, 哪条路)。09-23 P3：W3P point 在运行时里走的就是 JASS IssuePointOrder 那条路（RT_KERNEL_SHARE 那段
        A/B 比出来的好路：英雄撤退 13 次 12 次走了），动作 41 那条不再有 —— 一条路，标签照旧叫「内核」，回执统计和以前可比。"""
        return bool(ses.cmd.move_to(u.addr, tx, ty, ORDER_MOVE)), "内核"

    def rt_track(u, tx, ty, how):
        """撤退令下成功后登记回执（同一个单位只留最新那条）。
        ★ 09-22 第一轮回执：英雄没走的 4 次全是「订单被换成 000D000F」，而且那条攻击令是撤退令**之前**大脑下的攻击移动 ——
          分不清「撤退令根本没替换掉它」和「替换了、随后又变回攻击」。英雄下令当下再读一次订单槽（约 12ms，英雄撤退一分钟几次）。"""
        rt_pending[:] = [p for p in rt_pending if p[0] != u.addr]
        if len(rt_pending) < RT_MAX_PENDING:
            slot_now = None
            if u.is_hero:
                try:
                    o = ses.cmd.current_order(u.addr)
                    slot_now = ("槽空" if o is None else
                                f"{int(o.get('order') or 0):08X}->({o.get('x') or 0:.0f},{o.get('y') or 0:.0f})")
                except Exception:                    # noqa: BLE001
                    slot_now = "读不到"
            rt_pending.append([u.addr, tx, ty, time.monotonic(), math.hypot(tx - u.x, ty - u.y), how, slot_now])
    focus_recent = {}     # 09-16 跟打：目标地址 -> {单位地址: 到期时刻}（pick_refill）
    focus_join = {True: 0, False: 0}   # 09-16 补攻击令 跟打 / 新目标 各几条（心跳）
    creep_refill_sent = creep_refill_held = creep_refill_noclaim = 0
    reject_why = {}       # 09-16：施法被退原因 -> 次（reject_reason）
    cast_pending = []     # 09-16：施法回执待查 (英雄地址, 技能名, 下令时刻, 第几次看)，最多 4 个
    learned_seen = {}     # 09-16：英雄地址 -> (读的时刻, {技能代码: 等级} 或 None)
    unlearned = {}        # 09-16：时机到了但技能没学、没下令的（技能 -> 人·轮）
    cast_hold = {}        # 09-16：英雄地址 -> 施法保护到期时刻（这之前补攻击令不碰它）
    cast_hold_blocked = 0  # 09-16：补攻击令因为施法保护没碰的（人·轮）
    engine_cd = {}        # 09-16：下令前引擎说还在冷却、没下令的（技能 -> 次）
    cast_rcpt = {}        # 09-16：施法回执 技能·类别 -> 次（cast_receipt）
    dodged: dict[int, float] = {}   # 09-15 躲暴风雪：地址 -> 握到几点（这段时间补攻击令别碰它）
    bliz_waves = bliz_moved = bliz_nocluster = bliz_noclaim = 0
    motion = Motion()               # 09-22 敌方单位位置历史：烈焰风暴只瞄站桩的（STILL_WINDOW 上面那段）
    ff_casts = ff_moved = ff_noclaim = 0    # 09-22 放烈焰风暴后让圈里自己人躲开：几次 / 躲了几个 / 抢不到指挥权几个
    ff_fenced = 0                           # 09-22 晚 火圈没灭时走进圈边、被推回圈外的（路过绕开）
    ff_zones = []                           #   还没灭的火圈（ff_zone）
    ff_held = {}                            #   单位地址 -> 为哪个火圈握到几点（同一个圈不重复推）
    last_bliz_note = 0.0
    cds = SpellCooldowns()          # 09-15 英雄施法：每个英雄每个技能的冷却
    casts: 'Counter[str]' = Counter()
    cast_rejects: 'Counter[str]' = Counter()
    last_cast_note = 0.0
    last_squad = 0.0
    retreat_tp = None               # 09-23 整队撤退之后 (到期, x, y)：离家远的英雄读回城整队传走（itemuse.retreat_portal_due）
    tp_retreats: 'Counter[str]' = Counter()
    kite_last: dict = {}            # 09-23 远程拉扯（kite_moves）：单位地址 -> 上次拉开的时刻
    kite_tally: 'Counter[str]' = Counter()
    last_home_break = 0.0
    last_report = time.monotonic()
    last_publish = 0.0
    odds = fightodds.FightOdds()   # 09-17 v291：接触后 5/10 游戏秒按世界模型胜率叫撤（门槛和依据见 fightodds.py）
    clock = None                   # fightodds.GameClock（游戏时钟；换游戏进程就重建）
    clock_pid = None
    odds_ret = odds_home = odds_noclock = 0   # 胜率叫撤 撤了几次 / 家门口没撤 / 读不到游戏时钟的轮数（心跳，0 要能解释）
    odds_seq = 0
    guard_last = {}                # 09-17 水元素护卫：水元素地址 -> (上次目标地址, 时刻)
    guard_sent = guard_noclaim = 0
    guard_held = {}                # 09-22 护卫握着的水元素 -> 握到几时（GUARD_HOLD；guard_release 提前还）
    guard_of = {}                  # 09-22 水元素 -> 它护的大法师地址
    danger_at = {}                 # 09-17 英雄接触前预警：英雄地址 -> 上次判的时刻
    danger_pulls = danger_home = danger_noclaim = danger_harbor = 0
    hero_ctl = Counter()           # 09-22 1-299：英雄被控住（缠绕/眩晕…）、驱散下了/被拒、改道往大部队
    low_pulls = [0]                # 09-18：按"血只剩一点"（不是外推）进危险名单的人·轮，心跳里报
    early_release = 0              # 09-18：跑到安全处提前放开指挥权的次数
    hero_reposition_at = {}        # 健康英雄退到后排的冷却；这不是脱战，不发布 solo
    hero_focus_last = {}           # v292 远程集火英雄：单位地址 -> (目标英雄地址, 时刻)
    hero_focus_sent = 0
    board = UM.FocusBoard()        # 09-21 谁在打谁（全军共用一张表，按敌方单位读任务目标；见 unitmind.py）
    minds = UM.Minds()             #   每个单位的「打 / 养伤」状态
    mind_ttd: 'Counter[str]' = Counter()   #   心跳：按「打它的人」判危险（进名单 / 致命 / 团队闸没挡住的致命）人·轮
    mind_why = {}                  #   地址 -> (在打它的人数, 预计还能撑几游戏秒)：拉人那一行日志用
    cover_sent = cover_noclaim = 0 #   掩护撤退：叫队友去打追它的人
    last_rest_publish = 0.0
    # 09-21 战斗决策明细：每条带中文名 #编号、依据的数据、耗时（快照旧了多久 / 决定用了多久 / 下令用了多久）
    DLOG = declog.DecisionLog(DECF, "毫秒层", ids_fn=lambda: _ids_cache["ids"], clock=lambda: time.monotonic())
    loop_ref = {"t0": None, "age": None, "gap": None}   # 这一轮拿到快照的时刻、快照旧了多久、和上一轮隔了多久
    g_ref = {"g": None, "at": 0.0, "speed": 2.0}   # 游戏时钟：每 2 秒读一次，中间按实测倍速外推

    def game_now(t):
        if clock is None:
            return None
        if g_ref["g"] is None or t - g_ref["at"] >= 2.0:
            try:
                ms = clock.read()
            except Exception:                        # noqa: BLE001
                ms = None
            if ms is not None:
                gs = float(ms)                       # GameClock.read() 已经是秒（09-21 第一版又除了 1000：520 秒记成 0.5）
                if g_ref["g"] is not None and t > g_ref["at"]:
                    sp = (gs - g_ref["g"]) / (t - g_ref["at"])
                    if 0.5 < sp < 6.0:
                        g_ref["speed"] = sp
                g_ref["g"], g_ref["at"] = gs, t
        return None if g_ref["g"] is None else g_ref["g"] + (t - g_ref["at"]) * g_ref["speed"]

    def dlog(act, units, why, *, target=None, point=None, inputs=None, cmd_s=0.0):
        t0 = loop_ref["t0"]
        DLOG.log(act, units, why, target=target, point=point, inputs=inputs, t0=t0, cmd_s=cmd_s,
                 age_ms=loop_ref["age"], g=game_now(t0) if t0 is not None else None, gap_ms=loop_ref["gap"])

    # ★★ 09-22 兵种技能（unitspells.py 开头那段）：对敌方高伤害单位分散放、效果结束再补
    usp = unitspells.UnitSpells(DPS, AIR_TYPES, SUMMONS, itemuse.MECHANICAL, GAME_SPEED)
    usp_mana = {}                  #   施法兵地址 -> (读的时刻, 蓝)
    usp_tech = {}                  #   研究码 -> (读的时刻, 等级或 None)
    last_usp = 0.0
    last_usp_note = 0.0
    last_priest = 0.0              # 09-21 牧师定向治疗（priest_pick）
    priest_last = {}               #   牧师地址 -> (上次指定的目标, 时刻)
    priest_tally: 'Counter[str]' = Counter()
    priest_heal_off = set()        # 09-22 因为身边有送死名单里的兵而关掉了自动治疗的牧师（离开后再开）
    own_tg = CreepAggro(refresh=SUMMON_CHECK_EVERY)   # 09-18 别打召唤物：我方单位 -> 它在打谁（同一个按任务句柄变化触发的缓存）
    aggro = CreepAggro()           # 09-18 按野怪真实仇恨轮换
    aggro_out = {}                 #   拉出去的：地址 -> {"out_at", "clear_since", "moved_at"}
    aggro_last_back = {}           #   送回去的时刻（又被盯上算「又被盯」）
    ag = Counter()                 #   心跳计数：读/拉出/送回/又被盯/被追再挪/血太低不回/到顶/抢不到
    creep_seen = {}                # 09-18 打野阵亡：在营地边交手的我方单位 地址 -> (类型, 是英雄, 最后看见的时刻)
    creep_dead = Counter()         #   阵亡 类型 -> 个（心跳）
    iso_seen = iso_pulls = iso_noclaim = 0   # 09-18 孤兵回撤：判成孤兵的人·轮 / 拉出 / 抢不到指挥权
    sac, sac_read = set(), -1e9            # 09-18 喂野怪名单（每秒读一次）
    summon_reads = summon_hit = summon_switched = summon_none = summon_noclaim = 0
    autocast_on = {}               # 09-18 英雄地址 -> {技能名: 开自动施法时的等级}（_rule_autocast）
    toggles_on = {}                # 09-18 英雄地址 -> {技能名: 最近一次开令成功的时刻}（_rule_toggle）
    followups = []                 # 09-18 施法后续 (到期时刻, 英雄地址, ("move", x, y) / ("attack", 目标地址), 技能名)
    follow_done: 'Counter[str]' = Counter()   # 施法后续 技能·动作·结果 -> 次（心跳）
    corpse_prev = {}               # 09-18 上一轮活着的单位（corpse_events）
    corpses = []                   # 09-18 新鲜尸体 [(x, y, 时刻)]，CORPSE_KEEP 秒后丢
    cast_landed: 'Counter[str]' = Counter()   # 09-18 施法回执里「进冷却」按技能计（心跳「技能 …」那一行）

    while True:
        try:
            snap = ses.read()
            if snap is None:
                # 一次快照缺失不是换局；停止下令，已发布锁按原截止自然过期。
                time.sleep(TICK)
                continue
            match = (ses.pid, getattr(snap, "epoch", None)) if snap is not None and snap.in_game else None
            if match != last_match:
                # 包括同一进程内的新对局；绝不能把旧地址的锁带给新单位。
                for addr in set(pulled) | set(solo) | set(rotating):
                    claims.give_back(addr)
                hp_prev.clear()
                pulled.clear()
                solo.clear()
                rotating.clear()
                dodged.clear()
                cds = SpellCooldowns()
                generations.clear()
                last_squad = 0.0
                retreat_tp = None
                kite_last.clear()
                last_home_break = 0.0
                odds.reset()
                guard_last.clear()
                danger_at.clear()
                hero_focus_last.clear()
                hero_reposition_at.clear()
                board = UM.FocusBoard()
                minds = UM.Minds()
                mind_why.clear()
                priest_last.clear()
                aggro = CreepAggro()
                own_tg = CreepAggro(refresh=SUMMON_CHECK_EVERY)
                aggro_out.clear()
                aggro_last_back.clear()
                creep_seen.clear()
                autocast_on.clear()
                toggles_on.clear()
                followups = []
                corpse_prev = {}
                corpses = []
                hall_memory.clear()
                last_match = match
                retreat_watch = RetreatWatch()
                battle_commit, model_commit = BattleCommit(), BattleCommit()
                publish_hurt(pulled, time.monotonic(), ses.pid, getattr(snap, "epoch", None))
                publish_solo(solo, time.monotonic(), ses.pid, getattr(snap, "epoch", None))
                _ids_cache.update(pid=ses.pid, at=-1e9, ids={})     # 血条编号按这一局的 pid 读（上一局的编号作废）
                if match is not None:
                    note(f"接上 War3 pid={ses.pid} epoch={match[1]}")
            if snap is None or not snap.in_game:
                time.sleep(TICK)
                continue
            home = home_of(snap)
            if home is None:
                time.sleep(TICK)
                continue
            now = time.monotonic()
            loop_ref["gap"] = None if loop_ref["t0"] is None or now - loop_ref["t0"] > 5.0 else (now - loop_ref["t0"]) * 1000
            # 09-23：真实的快照年龄（W3P 世界块发布时刻，见 w3cmd.snapshot_age_ms）；老快照的 age_ms 恒 0
            _age = None
            try:
                _age = ses.cmd.snapshot_age_ms() if hasattr(ses.cmd, "snapshot_age_ms") else None
            except Exception:  # noqa: BLE001
                _age = None
            loop_ref["t0"], loop_ref["age"] = now, (_age if _age is not None else getattr(snap, "age_ms", None))
            halls = recent_halls(hall_memory, snap, now)
            if clock is None or clock_pid != ses.pid:      # 09-17：游戏时钟（叫撤信号要带游戏毫秒）；只建对象，读时才走通道
                clock = fightodds.GameClock(ses.cmd) if ses.cmd is not None else None
                clock_pid = ses.pid
                odds.reset()

            current = {u.addr: identity(u) for u in snap.mine}
            changed = {addr for addr, pair in generations.items()
                       if current.get(addr) != pair}
            for addr in changed:
                hp_prev.pop(addr, None)
                pulled.pop(addr, None)
                solo.pop(addr, None)
                rotating.pop(addr, None)
                aggro_out.pop(addr, None)
                dodged.pop(addr, None)
                minds.out.pop(addr, None)      # 地址被新单位复用：旧单位的养伤状态不能带过去
                claims.give_back(addr)
            if changed:
                publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
            generations = current
            # ★★ 09-18 「英雄看戏」埋点抓到满血英雄在自己人交战时闲着、被本模块的租约锁着。
            #   拉残血/整队撤的人原来一律按住 LEASE 8 秒，跑出危险区之后那几秒就是干站着。
            #   现在：握够 SAFE_RELEASE_HOLD 秒、身边 SAFE_RELEASE_R 内没有敌人 -> 提前还给策略层。
            if pulled:
                mine_by_addr = {u.addr: u for u in snap.mine if u.hp > 0}
                foes_near = [f for f in snap.units
                             if f.owner != snap.me and f.owner <= 12 and f.hp > 0
                             and (f.owner == CREEP_OWNER or foe_fighter_type(f.type))]
                # ★ 09-21：塔不在 foes_near 里（不是作战单位），拉出来的人还在塔射程里就被判「安全」提前放手，
                #   大脑马上又派回去 —— 5 号 21:15 那局山丘在塔下 20 秒拉了 5 次、派回 5 次，649 -> 1。
                towers_near = enemy_towers(snap.units, snap.me)
                for a in [a for a in pull_seen if a not in pulled]:
                    pull_seen.pop(a, None)
                for addr, until in list(pulled.items()):
                    u = mine_by_addr.get(addr)
                    # ★ 09-22 原来按 `until - now > duration - SAFE_RELEASE_HOLD` 倒推握了多久，duration 取 SOLO_LEASE（有孤军标记）或 LEASE。
                    #   可英雄被集火撤 / 预警撤是 pulled = LEASE（8）、solo = SOLO_LEASE（10）：倒推出来「已经握了 2 秒」，
                    #   刚下完撤退令的下一轮就能放手 —— 17:20:28 血法师被集火撤，17:20:29 就被放开、本模块接着放了烈焰风暴，没撤成。
                    #   现在按「这个期限第一次被看到的时刻」算（pull_seen；期限一变就重新计时）。
                    if u is None or until <= now or not pull_held_long_enough(pull_seen, addr, until, now):
                        continue
                    unavailable = {a for a, deadline in pulled.items() if deadline > now} | set(rotating) | set(aggro_out)
                    prev_hp = hp_prev.get(addr)
                    healed_gain = (u.hp - prev_hp[0]) if prev_hp else 0.0
                    healed_ready = (u.is_hero and healed_hero_rejoin_ready(
                        u, healed_gain, foes_near, list(mine_by_addr.values()), unavailable))
                    if u.is_hero and solo.get(addr, 0.0) > now:
                        escorts = [a for a in snap.mine if a.type not in WORKERS and can_move(a)]
                        if (not healed_ready
                                and not hero_return_ready(u, foes_near, escorts, unavailable,aggro.targets(foes_near,now),
                                                          fire=hero_fire(u, now))):
                            continue
                        solo.pop(addr, None)
                    elif not healed_ready and (any(math.hypot(f.x - u.x, f.y - u.y) <= SAFE_RELEASE_R for f in foes_near)
                                               or in_tower_range(u, towers_near)):
                        continue
                    pulled.pop(addr, None)
                    claims.give_back(addr)
                    early_release += 1
                    if healed_ready:
                        note(f"英雄治疗后归队：{u.type}@{u.addr:X} 本轮回血 {healed_gain:.0f}，局部战力占优，提前恢复输出")
            if (pulled or solo) and now - last_publish >= 0.5:
                # 先清掉复用地址，再补发原截止；发布失败不会变成永久丢失的锁。
                publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
                last_publish = now
            # 09-21 养伤：血回到 RETURN_FRAC 以上才归队（条件，不是 8 秒租约）；名单每 0.5 秒发一次，大脑按它不派
            def _rejoined(u):
                """孤兵什么时候算回到队伍里：回到家门口，或身边 ISO_FRIEND_R 内又有 ISO_REJOIN 个没在脱离的自己人。"""
                if near_home(u.x, u.y, home, halls):
                    return True
                return sum(1 for m in snap.mine if m.addr != u.addr and m.hp > 0 and m.type not in WORKERS and can_move(m)
                           and m.addr not in minds.out and math.hypot(m.x - u.x, m.y - u.y) <= ISO_FRIEND_R) >= ISO_REJOIN
            for u in minds.update({u.addr: u for u in snap.mine}, now, _rejoined):
                note(f"脱离结束归队：{u.type}@{u.addr:X} {int(u.hp)}/{int(u.hp_max)}（血回到 {UM.RETURN_FRAC:.0%} 以上 / 孤兵回到了队伍里）")
            if (minds.out or last_rest_publish > 0) and now - last_rest_publish >= 0.5:
                write_deadlines(RESTINGF, minds.deadlines(now), now, ses.pid, snap.epoch, snap.mine)
                last_rest_publish = now if minds.out else 0.0     # 名单空了写一次空表，之后不再写
            polls += 1
            # 只管**能走的战斗单位**：排掉工人，也排掉建筑（见 can_move 的说明）。
            fighters = [u for u in snap.mine
                        if u.type not in WORKERS and u.hp_max > 0 and can_move(u)]
            seen = set()
            danger = []
            hits = []          # 这一轮掉了血的自己人（躲暴风雪用掉血簇估落点）
            for u in fighters:
                seen.add(u.addr)
                prev = hp_prev.get(u.addr)
                hp_prev[u.addr] = (u.hp, now)
                if not prev:
                    continue
                drop = prev[0] - u.hp
                dt = max(0.05, now - prev[1])
                if drop <= 0:
                    q = hp_win.setdefault(u.addr, deque())
                    q.append((now, u.hp))
                    while len(q) > 2 and q[0][0] < now - DPS_WINDOW:
                        q.popleft()               # 09-22 没掉血也修剪（原来只在掉血那支修剪，不挨打的英雄一直往里堆）
                    continue
                if drop >= 8:
                    hits.append(u)
                # ★★ 09-07 傍晚：**瞬间掉血不是每秒伤害。**
                #   09-07 感知审计指出：
                #   一次 70 点的攻击落在 0.1 秒的采样间隔里，drop/dt 会算出 700/秒，
                #   然后我们再把它外推 2.5~4 秒 —— 于是满血英雄也会被判"必死"拉走。
                #   现场就是这样：`Obla 679/750 正在挨 705 血/秒`（90% 血被拉），
                #   而我今天把英雄窗口从 2.5 放大到 4 秒，等于把这个错误又放大了 1.6 倍。
                #   改成**按窗口算**：至少 MIN_WIN 秒的实际掉血除以实际时长。
                #   一次性爆发因此被摊平到一个攻击间隔上，而持续挨打的照样很快到线。
                q = hp_win.setdefault(u.addr, deque())
                q.append((now, u.hp))
                while len(q) > 2 and q[0][0] < now - DPS_WINDOW:
                    q.popleft()
                span = now - q[0][0]
                if span >= MIN_WIN:
                    loss = max(0.0, q[0][1] - u.hp)
                    dps = loss / span
                    basis = span
                else:
                    # 窗口还没攒够：把这一下摊到一个攻击间隔上，别按 0.1 秒算
                    dps = drop / max(dt, MIN_WIN)
                    basis = max(dt, MIN_WIN)
                # ★ 09-07：英雄按更宽的窗口算。三局里英雄都是"残血撤回"那一拍的下一拍就死：
                #   剑圣 242/650 上拍掉 317、287/650 掉 363、209/650 掉 331 —— 每拍 300+ 血，
                #   2.5 秒 / 15% 的窗口对 650 血的英雄只剩 97 血的余量，跑不掉。
                #   英雄一条命 = 300 金 + 15 拍复活 + 进攻闸门整段"英雄才 0 级"，比一个兵贵十倍，
                #   拉早一点的代价只是少打几秒。
                secs = HERO_LETHAL_SECS if u.is_hero else LETHAL_SECS
                floor = HERO_FLOOR if u.is_hero else FLOOR
                left = u.hp - dps * secs
                low_frac = LOW_FRAC_HERO if u.is_hero else LOW_FRAC
                if left <= u.hp_max * floor:
                    danger.append((u, dps, left, secs, basis))
                elif u.hp_max > 0 and u.hp <= u.hp_max * low_frac:
                    danger.append((u, dps, u.hp, secs, basis))   # 血只剩 low_frac 以下：不等外推
                    low_pulls[0] += 1
            for a in list(hp_prev):
                if a not in seen:
                    hp_prev.pop(a, None)          # 死了/看不见了，别留旧血量
                    hp_win.pop(a, None)

            # ── 09-21 每个单位的处境（unitmind.py）：谁在打它、按打它的人算还能撑几游戏秒 ──────────
            #   上面那段按「掉了多少血」外推，要先看到血掉下来；被 8 个人同时砍的步兵从 65% 到死只要一两秒，
            #   外推来不及（1079 个战死里 36% 是这样死的，其中 79% 死前读得到 >=3 个敌人在打它）。
            #   这里按敌方单位读任务目标（一张表全军共用），打它的人的伤害加起来 / 它的血 = 还能撑几秒，
            #   不到 TTD_OUT 就进危险名单；不到 TTD_CRIT 记进 lethal —— 下面的团队闸（同时撤下人数上限、家门口大仗）不挡它。
            #   英雄不走这里（有自己的集火撤离）；喂野怪名单里的、已经在撤的不算。
            foe_fight = [f for f in snap.units if f.owner != snap.me and f.owner < 12 and f.hp > 0
                         and foe_fighter_type(f.type)]
            lethal = set()
            if foe_fight and ses.cmd is not None:
                t_read = time.monotonic()
                for f in board.due(foe_fight, fighters, now):
                    if time.monotonic() - t_read > UM.BOARD_BUDGET:
                        break
                    try:
                        task = ses.cmd.current_task(f.addr)
                    except Exception:                        # noqa: BLE001
                        task = None
                    board.put(f.addr, task.get("target") if task else None, now)
                board.read_s += time.monotonic() - t_read
                board.prune({f.addr for f in foe_fight})
                in_danger = {t[0].addr for t in danger}
                for u in fighters:
                    if u.is_hero or u.addr in sac or pulled.get(u.addr, 0.0) > now:
                        continue
                    att = board.attackers(u, foe_fight, now)
                    if not att:
                        continue
                    inc = UM.incoming_dps(u, att, _unit_dps, lambda t: ARMOR.get(t, 0.0))
                    left_s = UM.ttd(u, inc)
                    if left_s >= UM.TTD_OUT:
                        continue
                    mind_ttd["进名单"] += 1
                    mind_why[u.addr] = (len(att), left_s)
                    if left_s < UM.TTD_CRIT:
                        lethal.add(u.addr)
                        mind_ttd["致命"] += 1
                    if u.addr not in in_danger:
                        # 元组和上面同一个形状 (单位, 每秒伤害, 外推剩血, 外推秒数, 窗口)；剩血按「打它的人」算
                        danger.append((u, inc, u.hp - inc * UM.TTD_OUT, UM.TTD_OUT, 0.0))
                        mind_ttd["只有按打它的人才进名单"] += 1

            # ── 躲暴风雪（判据和数据见 blizzard_dodge 上面那段）────────────
            #   撤退中的、轮换出去的、刚躲过还没到期的不再挪 —— 同一个人反复重下移动令会把寻路清零。
            for a in [a for a, until in dodged.items() if until <= now]:
                dodged.pop(a, None)
            foes_live = [f for f in snap.units if f.owner != snap.me and f.owner < 12 and f.hp > 0]
            # 比补攻击令、轮换送回和施法更早执行；独立于全军撤退，不让英雄成为最后一个留下的人。
            hostile = [f for f in foes_live if foe_fighter_type(f.type)] + [c for c in snap.units if c.owner == CREEP_OWNER and c.hp > 0]
            for u, threats, attempt in retreat_watch.check(fighters, hostile, pulled, now, sac):
                if u.is_hero:
                    dis = disabled_by(ses.cmd, u)
                    if dis is not None:
                        # 被控住：移动令白下（1-299）。能驱散的叫最近的牧师放驱散魔法，不能驱散的（眩晕/穿刺）等它结束。
                        code, cn = dis
                        hero_ctl[cn] += 1
                        priest = dispel_pick(u, snap.mine, mana_of=lambda p: (mana_seen.get(p.addr) or (0, None))[1])
                        how = "不能驱散，等它结束"
                        if code in DISPELLABLE:
                            if priest is None:
                                how = "能驱散但附近没有牧师"
                            elif claims.take(priest.addr, 1.5, prio=PRIO["cast"]):
                                tc = time.monotonic()
                                ok_d = ses.cmd.cast_point_ctx(priest.addr, ORDER_DISPEL, u.x, u.y)
                                if not ok_d:
                                    claims.give_back(priest.addr)
                                hero_ctl["驱散·" + ("下了" if ok_d else "被拒")] += 1
                                how = f"牧师 #{priest.addr:X} 驱散" + ("" if ok_d else "被拒（没学/没蓝/冷却）")
                                dlog("牧师驱散被控英雄", priest, f"{cn}：{u.type} 走不了", target=u,
                                     cmd_s=time.monotonic() - tc, inputs={"状态": code, "英雄血": f"{int(u.hp)}/{int(u.hp_max)}",
                                                                          "结果": "下了" if ok_d else "被拒"})
                            else:
                                how = "牧师被别的命令占着"
                        dlog("英雄被控住", u, f"{cn}（{code}），撤退令走不了：{how}", point=(u.x, u.y),
                             inputs={"状态": code, "第几次没走掉": attempt, "血": f"{int(u.hp)}/{int(u.hp_max)}",
                                     "身边敌人": len(threats)})
                        note(f"英雄被控住：{u.type}@{u.addr:X} {int(u.hp)}/{int(u.hp_max)} {cn}（{code}）—— {how}")
                        continue
                if not claims.take(u.addr, pulled[u.addr] - now, prio=PRIO["safety"]):
                    continue
                available = [a for a in fighters if pulled.get(a.addr, 0) <= now and a.addr not in rotating]
                if u.is_hero:
                    # 英雄改道先往大部队撤（1-299：部队往西撤、英雄往东南跑，追兵只追它一个）
                    harbor = friendly_harbor(u, threats, fighters)
                    if harbor is not None:
                        hero_ctl["改道往大部队"] += 1
                tx, ty = (harbor if u.is_hero and harbor is not None
                          else retreat_retry_point(u, threats, available, home, attempt, retreat_watch.goals.get(u.addr)))
                tx, ty, tw_fix = tower_safe_point(u, tx, ty, enemy_towers(snap.units, snap.me))
                tower_fix += tw_fix
                ok_r, r_path = retreat_move(u, tx, ty)
                if ok_r:
                    retreat_watch.goals[u.addr]=(tx,ty)
                    rt_track(u, tx, ty, "改道·" + r_path)
                    note(f"撤离未脱险·改道 {u.type}@{u.addr:X} {int(u.hp)}/{int(u.hp_max)} 第 {attempt} 次 -> ({tx:.0f},{ty:.0f})")
            # 在英雄自保前读取近处野怪目标，避免上一轮缓存/距离推测导致满血英雄来回撤。
            nearby_creeps=[c for c in hostile if c.owner==CREEP_OWNER and any(
                h.is_hero and _dist(h,c)<1000 for h in fighters)]
            t_read=time.monotonic()
            for c in aggro.due(nearby_creeps,now):
                if time.monotonic()-t_read>AGGRO_BUDGET:break
                try:tk=ses.cmd.current_task(c.addr)
                except Exception:tk=None
                aggro.put(c,tk.get('target') if tk else None,now)
                ag['读']+=1
            aggro.read_s+=time.monotonic()-t_read
            creep_targets=aggro.targets(nearby_creeps,now)
            unavailable = {a for a, until in pulled.items() if until > now} | set(rotating) | set(aggro_out) | sac
            for h in [u for u in fighters if u.is_hero and u.hp > 0]:
                if pulled.get(h.addr, 0.0) > now:
                    continue
                exposure = hero_exposure(h, hostile, fighters, unavailable,creep_targets)
                if exposure is None:
                    continue
                if calm_hold(exposure, hero_fire(h, now)):
                    hero_ctl["没挨打不拉·" + exposure[0]] += 1     # 09-22 打了再撤（HIT_WIN 上面那段）
                    # 1-393 大法师 21:01:24 637/800 身边 7 敌、21:01:28 掉到 126 阵亡：心跳只有次数，分不出挡的是谁 —— 每个英雄 3 秒记一行
                    if now - calm_note_at.get(h.addr, -1e9) >= 3.0:
                        calm_note_at[h.addr] = now
                        note(f"没挨打不拉：{h.type}@{h.addr:X} {int(h.hp)}/{int(h.hp_max)} {exposure[0]}，"
                             f"身边敌 {len(exposure[1])}、有效护卫 {len(exposure[2])}")
                    continue
                reason, threats, escorts = exposure
                reposition = reason == "大群贴脸提前脱离"
                if reposition and now - hero_reposition_at.get(h.addr, -1e9) < HERO_REPOSITION_COOLDOWN:
                    continue
                lease = HERO_REPOSITION_LEASE if reposition else SOLO_LEASE
                if not claims.take(h.addr, lease, prio=PRIO["safety"]):
                    continue
                # 被大群贴脸但仍健康：短退 800 到可输出的后排。真正低血/孤军：首令直接
                # 拉开 1600，避免 1-014 那种每次只退 800、被同速追兵一直磨死。
                escape = PULL_BACK if reposition else PULL_BACK * 2.0
                tx, ty = retreat_retry_point(h, threats, [u for u in fighters if u.addr not in unavailable and u.hp > 0.35 * max(1, u.hp_max)], home,0,distance=escape)
                tx, ty, tw_fix = tower_safe_point(h, tx, ty, enemy_towers(snap.units, snap.me))
                tower_fix += tw_fix
                tc = time.monotonic()
                ok_r, r_path = retreat_move(h, tx, ty)
                if not ok_r:
                    claims.give_back(h.addr)
                    continue
                rt_track(h, tx, ty, "英雄自保·" + r_path)
                dlog("英雄自保" + ("·短退后排" if reposition else "·脱离战场"), h,
                     f"{reason}：身边敌 {len(threats)} 个、真正能接战的护卫 {len(escorts)} 个", point=(tx, ty),
                     cmd_s=time.monotonic() - tc,
                     inputs={"血": f"{int(h.hp)}/{int(h.hp_max)}", "判据": reason, "身边敌人": len(threats),
                             "有效护卫": len(escorts), "锁多久秒": lease})
                pulled[h.addr] = now + lease
                if reposition:
                    solo.pop(h.addr, None)
                    hero_reposition_at[h.addr] = now
                else:
                    solo[h.addr] = now + lease
                retreat_watch.goals[h.addr]=(tx,ty)
                unavailable.add(h.addr)
                rotating.pop(h.addr, None)
                aggro_out.pop(h.addr, None)
                cast_hold.pop(h.addr, None)
                publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
                saved += 1
                mode = "短退后排" if reposition else "脱离战场"
                note(f"英雄局部自保：{h.type}@{h.addr:X} {int(h.hp)}/{int(h.hp_max)} {reason}，敌 {len(threats)}、有效护卫 {len(escorts)}，{mode} {lease:.1f}s -> ({tx:.0f},{ty:.0f})")
            free = [u for u in fighters if u.hp > 0 and u.addr not in rotating
                    and pulled.get(u.addr, 0.0) <= now and u.addr not in dodged]
            bmoves, bcenter, bcasting = blizzard_dodge(foes_live, hits, free)
            if bcasting and bcenter is None:
                bliz_nocluster += 1
            if bmoves:
                bliz_waves += 1
                moved = 0
                for u, tx, ty in bmoves:
                    if not claims.take(u.addr, BLIZ_HOLD, prio=PRIO["dodge"]):
                        bliz_noclaim += 1
                        continue
                    if retreat_move(u, tx, ty)[0]:
                        dodged[u.addr] = now + BLIZ_HOLD
                        moved += 1
                    else:
                        claims.give_back(u.addr)
                bliz_moved += moved
                if moved and now - last_bliz_note > 3.0:
                    last_bliz_note = now
                    note(f"躲暴风雪：敌方英雄在引导暴风雪，我方 {len(hits)} 人这一轮掉血、"
                         f"落点约 ({bcenter[0]:.0f},{bcenter[1]:.0f}) —— 挪开 {moved}/{len(bmoves)} 人 {BLIZ_STEP:.0f}")

            # ── 09-23 远程拉扯近战（AMAI GroupMicroSystem，判据见 kite_moves 上面那段）────────────
            kfree = [u for u in fighters if u.hp > 0 and u.addr not in rotating and pulled.get(u.addr, 0.0) <= now
                     and u.addr not in dodged and u.addr not in sac]
            for u, tx, ty, m in kite_moves(kfree, foes_live, now, kite_last):
                if not claims.take(u.addr, KITE_HOLD, prio=PRIO["dodge"]):
                    kite_tally["抢不到指挥权"] += 1
                    continue
                if retreat_move(u, tx, ty)[0]:
                    dodged[u.addr] = now + KITE_HOLD
                    kite_last[u.addr] = now
                    kite_tally["拉开"] += 1
                else:
                    claims.give_back(u.addr)
                    kite_tally["没发出去"] += 1

            # ── 打野轮换：先看这是不是一场打野 ──────────────────
            # 只有和**野怪**交手时才轮换。打敌军是另一回事：
            # 对面会追、会集火、会绕后，拉出去 400 只是把人送到半路挨打。
            # 设计原则：打野和与敌军交战是两套独立逻辑。
            engaged = [u for u in fighters if u.hp > 0 and u.addr not in sac and pulled.get(u.addr, 0.0) <= now]
            camp, lvl = creep_camp_near(snap, engaged)
            _ = low_pulls
            if camp:
                engaged = [u for u in engaged if in_reach(u, camp)]
            rotated_now = 0
            # ★★★★ 09-09 打野时丝血单位仍在硬扛，旁边明明有满血或血更多的单位；
            #     应让它后撤一段、换别的单位顶上。
            #
            #   查日志：轮换**在跑**，但 314 次心跳"拉出 0"、只有约 70 次 >=1 ——
            #   **82% 的心跳一个人都没换出去**。而心跳只说 0，不说为什么是 0。
            #   这正是"一排零同时表示没发生和没轮到"：一个数表示好几件事，要拆开各记各的。
            #
            #   轮换要连过五道，任何一道都能静默产生 0：
            #     没在打野 / 交手的人不足 2 / 找不到"正在挨打"的那个 /
            #     已经换出去的人数到顶 / 抢不到指挥权
            #   所以按这个项目验证过有效的办法（跳过理由计数）把它们分开记，
            #   下一轮它自己说话。
            # ── 打野阵亡（0 死亡的判据）：在营地边交手过、1 秒内还看见的非召唤物，这一轮没了 = 打野阵亡 ──
            alive_now = {u.addr for u in snap.mine if u.hp > 0}
            for a, (typ, hero, seen_at) in list(creep_seen.items()):
                if a not in alive_now:
                    if now - seen_at <= 1.0:
                        creep_dead[("英雄 " if hero else "") + typ] += 1
                    creep_seen.pop(a, None)
                elif now - seen_at > 3.0:
                    creep_seen.pop(a, None)
            if camp:
                for u in engaged:
                    if u.type not in SUMMONS:
                        creep_seen[u.addr] = (u.type, bool(u.is_hero), now)

            # ── 按野怪真实在打谁轮换（判据见 CreepAggro 上面那段）──
            aggro_used = False
            if camp and ses.cmd is not None:
                awake = [c for c in camp if c.hp > 0]
                t_read = time.monotonic()
                for c in aggro.due(awake, now):
                    if time.monotonic() - t_read > AGGRO_BUDGET:
                        break
                    try:
                        tk = ses.cmd.current_task(c.addr)
                    except Exception:                        # noqa: BLE001
                        tk = None
                    aggro.put(c, tk.get("target") if tk else None, now)
                    ag["读"] += 1
                aggro.read_s += time.monotonic() - t_read
                aggro.prune({c.addr for c in camp})
                if aggro.known(awake, now):
                    aggro_used = True
                    bt = aggro.by_target(awake, now)
                    fighters_in = [u for u in engaged if u.type not in SUMMONS]
                    room = max(1, len(fighters_in) // 2) - len(aggro_out)       # 总得有人顶着：最多一半在外面
                    room = max(0, room) if len(fighters_in) >= 2 else 0
                    # ★ 09-18：召唤物/近战兵在场时总有人顶着 —— 开局「大法师 + 水元素」只有一个会被拉的，原来永远 room=0、大法师一直扛
                    if room == 0 and not aggro_out and any(
                            not t.is_hero and t.hp > 0 and (t.type in SUMMONS or RANGE.get(t.type, 100.0) <= MELEE_RANGE) for t in engaged):
                        room = 1
                    for u, att, ttd in aggro_pulls(engaged, bt, set(rotating) | set(aggro_out), room):
                        if not claims.take(u.addr, AGGRO_LEASE, prio=PRIO["rotate"]):
                            ag["抢不到"] += 1
                            continue
                        tx, ty = aggro_out_point(u, att)
                        hp_at = toward_healer(u, (tx - u.x) / (math.hypot(tx - u.x, ty - u.y) or 1.0),
                                              (ty - u.y) / (math.hypot(tx - u.x, ty - u.y) or 1.0), fighters, ROT_OUT * 2)
                        if hp_at is not None:
                            tx, ty = hp_at
                            to_healer += 1
                        if retreat_move(u, tx, ty)[0]:
                            aggro_out[u.addr] = {"out_at": now, "clear_since": None, "moved_at": now}
                            rotating[u.addr] = (now, lvl)
                            ag["拉出"] += 1
                            if now - aggro_last_back.get(u.addr, -1e9) < 6.0:
                                ag["送回后又被盯"] += 1
                        else:
                            claims.give_back(u.addr)
                    by_addr = {u.addr: u for u in snap.mine if u.hp > 0}
                    for a, st in list(aggro_out.items()):
                        u = by_addr.get(a)
                        if u is None:
                            aggro_out.pop(a, None)
                            rotating.pop(a, None)
                            claims.give_back(a)
                            continue
                        att = bt.get(_pair(u), [])
                        what = aggro_ready(u, bool(att), st, now)
                        # ★ 09-18 晚：曾经加过「保命拉出后 8 秒内不送回」，当天撤掉 —— 野怪锁定一个目标后，除非打死否则不会再换；
                        #   所以只要它的攻击对象已不是这个残血小兵，小兵就可以立刻回去继续输出。不再被盯着（aggro_ready 的
                        #   AGGRO_CLEAR）就送回；只留血量底线 AGGRO_STAY_FRAC（送回的兵 44% 又被盯上：野怪原来的目标死了/被拉走就近换人，
                        #   我们从挨打到拉出约 1.5 游戏秒，血太低的撑不到）
                        if what == "back":
                            tgt = creep_focus_target(awake)
                            if ses.cmd.target_order(u.addr, tgt.addr, 0x000D0003, tgt.x, tgt.y):
                                ag["送回"] += 1
                                aggro_last_back[a] = now
                            aggro_out.pop(a, None)
                            rotating.pop(a, None)
                            claims.give_back(a)
                            continue
                        claims.take(a, AGGRO_LEASE, prio=PRIO["rotate"])      # 续租，别让策略层把它又派回去
                        if what == "chased" and now - st["moved_at"] >= AGGRO_REMOVE_EVERY:
                            tx, ty = aggro_out_point(u, att)
                            if retreat_move(u, tx, ty)[0]:
                                st["moved_at"] = now
                                ag["被追再挪"] += 1
                        elif what == "stay" and u.hp / max(1.0, u.hp_max) < AGGRO_STAY_FRAC:
                            ag["血太低不回"] += 1
                            # ★★ 09-22 残血单位直接撤回家，不再留在战场边。原来血低于 AGGRO_STAY_FRAC 的就在营地边站着等营地打完
                            #   （1-397 一分钟 47 人·轮）。现在进养伤（unitmind，resting.json）：放开指挥权，大脑按养伤名单送回家附近空地，
                            #   血回到 RETURN_FRAC 才归队。英雄 / 召唤物 / 民兵不进养伤（can_rest），照旧在外面等。
                            if UM.can_rest(u, SUMMONS):
                                minds.mark_out(u, now, "打野残血")
                                aggro_out.pop(a, None)
                                rotating.pop(a, None)
                                if pulled.get(a, 0.0) <= now:
                                    claims.give_back(a)
                                if home is not None:
                                    retreat_move(u, home[0], home[1])
                                ag["残血回家养伤"] += 1
            if not camp and aggro_out:
                for a in list(aggro_out):                    # 营地打完/脱战：放回给策略层
                    aggro_out.pop(a, None)
                    rotating.pop(a, None)
                    if pulled.get(a, 0.0) <= now:            # 保命租约还在的别一起还掉（09-18 晚：还掉 = 策略层马上又派回去）
                        claims.give_back(a)

            if not camp:
                rot_skip['没在打野'] += 1
            elif aggro_used:
                rot_skip['按真实仇恨轮换'] += 1
            elif len(engaged) < 2:
                rot_skip['交手不足2人'] += 1
            if camp and len(engaged) >= 2 and not aggro_used:
                # 谁该退出去
                if lvl >= HI_LEVEL:
                    # 高等级营地只认血量：血最少的那个反正会被集火，
                    # 让它先出去，等它不再垫底再回来。
                    pick = min(engaged, key=lambda u: u.hp / max(1.0, u.hp_max))
                else:
                    # 普通营地认"谁正在挨打"——那才是仇恨所在。
                    ins = {x.addr for x in engaged}
                    hot = [(dps, u) for u, dps, _, _s, _b in danger if u.addr in ins]
                    # ⚠ 不能写 max(hot)：dps 相等时 Python 会去比第二个元素，
                    #   而 Unit 之间没有大小可言 —— 那是一个只在平局时才炸的崩溃。
                    pick = max(hot, key=lambda t: t[0])[1] if hot else None
                cap = max(1, len(engaged) // 3)     # 总得有人顶着
                if not pick:
                    # 普通营地这一支：danger 里没有"正在挨打"的人。
                    # 这是最可疑的一支 —— 对局里观察到的现象正是：有人在挨打却没被换下来。
                    rot_skip['找不到正在挨打的'] += 1
                elif pick.addr in rotating:
                    rot_skip['它已经在外面'] += 1
                elif len(rotating) >= cap:
                    rot_skip[f'换出去的到顶({cap})'] += 1
                if pick and pick.addr not in rotating and len(rotating) < cap:
                    # ⚠ take() 有副作用（抢到就占住了），**只能调一次** ——
                    #   第一版写成 if not take(): 记一笔; if take(): 干活，
                    #   于是抢到的那次会被再抢一遍。这类"判据本身有副作用"的坑
                    #   一旦写下就很难看出来，因为两次都返回 True 时行为看着是对的。
                    got = claims.take(pick.addr, ROT_HOLD_HI + 1.0, prio=PRIO["rotate"])
                    if not got:
                        rot_skip['抢不到指挥权'] += 1
                    if got:
                        cx = sum(c.x for c in camp) / len(camp)
                        cy = sum(c.y for c in camp) / len(camp)
                        dx, dy = pick.x - cx, pick.y - cy
                        n = math.hypot(dx, dy) or 1.0
                        tx, ty = pick.x + dx / n * ROT_OUT, pick.y + dy / n * ROT_OUT
                        # 打野轮换：身后 ROT_OUT×2 以内有牧师就站到牧师身后（奶好了再送回去），别跑远
                        hp_at = toward_healer(pick, dx / n, dy / n, fighters, ROT_OUT * 2)
                        if hp_at is not None:
                            tx, ty = hp_at
                            to_healer += 1
                        if retreat_move(pick, tx, ty)[0]:
                            rotating[pick.addr] = (now, lvl)
                            rotated_now += 1
                            rot_out += 1
                        else:
                            claims.give_back(pick.addr)
                # 谁该回去打
                for a, (out_at, out_lvl) in list(rotating.items()):
                    u = next((x for x in snap.mine if x.addr == a and x.hp > 0), None)
                    if u is None:
                        rotating.pop(a, None)
                        claims.give_back(a)
                        continue
                    waited = now - out_at
                    if out_lvl >= HI_LEVEL:
                        # 不再是全队血最少的 = 仇恨不会优先落在它身上了
                        mine_frac = u.hp / max(1.0, u.hp_max)
                        lowest = min((x.hp / max(1.0, x.hp_max) for x in engaged
                                      if x.addr != a), default=1.0)
                        ready = (mine_frac > lowest) or waited > ROT_HOLD_HI
                    else:
                        # 别人开始挨打了 = 仇恨已经转走
                        moved = any(d[0].addr != a for d in danger)
                        ready = moved or waited > ROT_HOLD
                    if ready:
                        tgt = creep_focus_target(camp)
                        if ses.cmd.target_order(u.addr, tgt.addr, 0x000D0003, tgt.x, tgt.y):
                            rot_back += 1
                        rotating.pop(a, None)
                        claims.give_back(a)
            elif rotating:
                # 野怪没了/脱战了：把还在外面的人放回去，别让租约把他们锁着
                for a in list(rotating):
                    rotating.pop(a, None)
                    claims.give_back(a)

            # ★★ 整队撤退判定 —— 从 3.2 秒的战略拍搬进 100 毫秒。
            #
            # 问题：战斗层已是毫秒级响应，整队撤退的判定却还跟着 3.2 秒的战略拍。
            # 一直含混的正是这里：**反射**（个体保命/轮换/护卫）早就在
            # 100ms，但"这一仗整体打不打得过、该不该全撤"这个**判定**
            # 还在战略拍里 —— 实测一拍 3.09 秒，而野怪一秒能打掉一个
            # 单位一半的血。几次"整队慢慢被磨死"就死在这条缝上。
            #
            # 判据和战略层同一套算术（打光他们要几秒 vs 他们打光我们要几秒），
            # 但每 100ms 算一次。保守边界，防和战略层打架、防抖：
            #   营地血 > 250 才考虑（快打完的仗撤了反而亏——赢面就在眼前）
            #   要输了才撤（t_杀光他们 > 0.9 × t_杀光我们），不是"打得难看"就撤
            #   6 秒内不重复触发；撤的人写 hurt.json，战略层那边自然把他们
            #   从可用名单里排掉，8 秒内不会再派回来 —— 两个大脑不抢人
            if camp and engaged:
                # ★★ 行军哨兵 —— v29 在战略层修过的 bug，我在这层原样重犯了。
                #
                # 实测（上线 20 分钟就在对局里复现）：
                #     战略层：打野行军中：到场 1/8 人 —— 不做战力判断，继续往上带
                #     这里：  整队撤退(100ms)：在场 1 人 vs 2 只 —— 拉回 1 人
                # 队伍分批到场，第一个到的被单独拿去推演，结论必然是"打不过"；
                # 拉走 + 写 hurt.json 之后，战略层把它排除，后到的两三个就成了
                # 孤军 —— 对局里「英雄往回跑、只剩两三个在打」正是这两层打架的结果。
                #
                # **还有人在赶来，就不许下整体结论。** 赶来的人比在场的多 =
                # 行军中，闭嘴等。判定成立要撤的时候，撤**所有**在附近的人
                # （在场的 + 赶来的）—— 只撤在场的等于把队伍拦腰剪断，
                # 和战略层整队撤退的老注释一个道理："撤的是所有人"。
                ccx = sum(c.x for c in camp) / len(camp)
                ccy = sum(c.y for c in camp) / len(camp)
                inids = {u.addr for u in engaged}
                coming = [u for u in fighters
                          if u.addr not in inids and u.hp > 0
                          and math.hypot(u.x - ccx, u.y - ccy) < 1600]
                marching = len(coming) > len(engaged)
                foe_hp = sum(c.hp for c in camp)
                foe_dps = sum(_unit_dps(c.type) for c in camp)
                # ★★ 09-07 演练台（drill.py）第一分钟抓到的：同等兵力 9 打 9，
                #   开战 1 秒这里判"在场 6 人 4050 血 vs 9 只 4040 血，打光他们 59s、
                #   他们打光我们 47s"—— 整队撤退、拉回 11 人；实际 18~20 秒打光对面 9 个，
                #   我方只死 1 个（两次重复都是）。
                #   错在"在场"只数**已经进了野怪射程**的人：近战还差两步没贴上，就不算数，
                #   于是 9 个人的仗按 6 个人推演，赢面直接推成输面。
                #   1600 内正在赶来的人**一两秒内就到**，推演要把他们算进去；
                #   行军哨兵（赶来的比在场的多就不下结论）照旧管"大部队还在路上"的情形。
                whole = engaged + coming
                our_hp2 = sum(u.hp for u in whole)
                our_dps2 = sum(_unit_dps(u.type) for u in whole)
                if not marching and foe_hp > 250 and our_dps2 > 0 and foe_dps > 0:
                    t_them = foe_hp / our_dps2
                    t_us = our_hp2 / foe_dps
                    if t_them > t_us * 0.9 and now - last_squad > 6.0:
                        last_squad = now
                        n_out = 0
                        again2 = stolen2 = no_claim2 = 0
                        for u in (engaged + coming):
                            # 和遭遇战撤退同一条规矩（09-07 晚）：还在这片仗里就重下，
                            # 别人占着就抢过来 —— 撤退不能因为"它在捡装备"而漏下一个人。
                            if pulled.get(u.addr, 0.0) > now:
                                again2 += 1
                            if not claims.take(u.addr, LEASE, prio=PRIO["safety"]):
                                if not claims.take(u.addr, LEASE, steal=True):
                                    no_claim2 += 1
                                    continue
                                stolen2 += 1
                            dx, dy = home[0] - u.x, home[1] - u.y
                            nn = math.hypot(dx, dy) or 1.0
                            if retreat_move(u, u.x + dx / nn * PULL_BACK, u.y + dy / nn * PULL_BACK)[0]:
                                pulled[u.addr] = now + LEASE
                                rotating.pop(u.addr, None)
                                n_out += 1
                            else:
                                claims.give_back(u.addr)
                        if n_out:
                            publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                            squad_ret += 1
                            dlog("打野整队撤退", [x for x in engaged + coming if pulled.get(x.addr, 0.0) > now],
                                 f"我们打光营地要 {t_them:.0f} 秒、营地打光我们要 {t_us:.0f} 秒 —— 打不动",
                                 inputs={"在场": len(engaged), "赶来": len(coming), "我方总血": int(our_hp2),
                                         "野怪": len(camp), "野怪总血": int(foe_hp), "拉回": n_out})
                            note(f"整队撤退(100ms)：打光他们要 {t_them:.0f}s、"
                                 f"他们打光我们要 {t_us:.0f}s（在场 {len(engaged)} 人 + 赶来 {len(coming)} 人，共 "
                                 f"{int(our_hp2)} 血 vs {len(camp)} 只 {int(foe_hp)} 血）"
                                 f"—— 拉回 {n_out}/{len(engaged) + len(coming)} 人"
                                 f"（重下 {again2}、抢租约 {stolen2}、抢不到 {no_claim2}）")

            # 野怪已经醒着时，100ms 层负责把刚打完目标而槽空的远程/英雄立刻接回输出。
            # 这条在整体撤退判定之后执行；刚被判撤的人已在 pulled，候选函数会排除，不会和保命令打架。
            # 护卫握着的水元素也不补（同一个模块再 take 会把 80 级的握住改写成 55 级、give_back 直接放掉，见 GUARD_HOLD）
            refill, held = creep_refill_picks([u for u in fighters if u.addr not in minds.out   # 养伤的不派回营地
                                               and guard_held.get(u.addr, 0.0) <= now],
                                              camp, now, rotating, dodged, pulled, cast_hold)
            creep_refill_held += held
            for u, tgt in refill[:CREEP_REFILL_MAX]:
                if not claims.take(u.addr, 1.5, prio=PRIO["refill"]):
                    creep_refill_noclaim += 1
                    continue
                try:
                    if ses.cmd.target_order(u.addr, tgt.addr, ORDER_ATTACK, tgt.x, tgt.y):
                        creep_refill_sent += 1
                finally:
                    claims.give_back(u.addr)

            # ★★ **和敌军打的时候也要实时算，不只是打野。**
            #
            # 设计要求：实时评估当前这一小片交火区域的敌我战力，
            #        据此决定撤还是继续打，避免硬顶到被团灭。
            #
            # 上面那段只认 owner==12（野怪）。打敌军时这一层**完全不判断**，
            # 全靠 3.2 秒一圈的战略拍 —— 而一场遭遇战几秒就分胜负，
            # 等战略拍转过来，人已经没了。
            #
            # 判据和打野那段是同一套（打光他们要几秒 vs 他们打光我们要几秒），
            # 只是对手换成敌方单位，而且**只看这一小片战场**（1000 以内）——
            # 「这一小片交火区域」正是这个意思：地图另一头的敌军不该
            # 影响这里的去留决定。
            #
            # 比打野更保守两点，因为敌军会追、会集火、会有英雄技能：
            #   * 门槛 1.0 而不是 0.9（打野是 0.9）—— 势均力敌就该走，
            #     因为对面还有我们算不到的东西（技能、增援、地形）
            #   * 撤退距离用 PULL_BACK 的 1.5 倍 —— 野怪不追人，敌军追
            # ⚠ 09-15：fz / mz 只在下面"附近有敌人"那一支里赋值，而 725 行附近的补攻击令
            #   `if fz and mz:` 在 `if not camp:` 块外面 —— 开局到第一次看见敌人之前每轮都抛
            #   UnboundLocalError（异常被吞，本轮后面的拉残血、英雄孤军全不跑），
            #   之后只要附近有野怪（camp 为真）就拿**上一轮的旧名单**下令。每轮先置空。
            fz, mz = [], []
            if not camp:
                foes_near = [u for u in snap.units
                             if u.owner not in (snap.me, CREEP_OWNER)
                             and u.owner < 12 and u.hp > 0
                             and foe_fighter_type(u.type)]    # 09-15：水元素这类召唤物也算（见 foe_fighter_type）
                mine_hot = [u for u in fighters if u.hp > 0]
                if foes_near and mine_hot:
                    # ★★ 09-07 晚，实战发现：总有一两个到两三个单位一直顶着一大群敌军，直到阵亡。
                    #
                    # 拉通全天 125 次遭遇战撤退：**只有 34% 是真的全撤**，平均有 3 个人留在原地。
                    # 根因在这两行原来的写法：战场 = 以**我方全军重心**为圆心、1000 为半径的圈。
                    #   全军人数中位 13，圈里中位只有 6 —— 中位 3 个（平均 5 个）人根本不在圈里，
                    #   29% 的撤退里圈子装不下一半军队。
                    # 而**冲得最深的那几个离重心最远**，正好被排除 —— 于是"撤退"从来不包括
                    # 最该撤的人。它们不在名单里，不是没拉动。
                    #
                    # 战场应该按**接触**定义，不是按我方重心：
                    #   先找"贴着我方任何一个人"的敌人，再找"贴着这些敌人"的我方单位。
                    # 这样队伍拉长、有人突前时，突前的那个仍然在名单里。
                    CONTACT = 900.0
                    fz = [f for f in foes_near
                          if any(math.hypot(f.x - u.x, f.y - u.y) < CONTACT for u in mine_hot)]
                    mz = [u for u in mine_hot
                          if any(math.hypot(u.x - f.x, u.y - f.y) < 1500 for f in fz)]
                    if len(fz) >= 2 and mz:
                        f_hp = sum(f.hp for f in fz)
                        f_dps = sum(_unit_dps(f.type) for f in fz)
                        m_hp = sum(u.hp for u in mz)
                        m_dps = sum(_unit_dps(u.type) for u in mz)
                        if m_dps > 0 and f_dps > 0 and f_hp > 200:
                            t_them = f_hp / m_dps
                            t_us = m_hp / f_dps
                            # ★★ 09-07 晚：拉通全天 111 次"遭遇战撤退"，看撤退后 30 秒兵力：
                            #     全部：      平均掉 8.7 个，79% 掉 ≥3，英雄 47% 在 30 秒内死
                            #     势均力敌（t_them ≤ 1.15 t_us）：平均掉 13.5 个，96% 掉 ≥3
                            #     明显要输（t_them > 1.6 t_us）：平均掉 6.8 个
                            #   势均力敌时叫撤，比明显要输时撤还惨一倍 —— 撤退中的军队被追着打、
                            #   没人还手，快的先跑、慢的被围。演练台的对照：同等兵力不撤，18 秒完胜。
                            #   （对野怪的"整队撤退"没这个问题：撤后 30 秒平均只掉 0.5 —— 野怪不追。）
                            #   门槛从 1.0 抬到 1.6：只在明显要输时才撤。势均力敌就打。
                            # ★★ 09-15：**家门口不整队撤。**
                            #   策略层 W3-Defend 在主基地是"无条件守"，这里一撤、1~2 秒后又被 DEFEND 派回来；
                            #   而"往家的方向拉"在家里就是往建筑堆里挤。192433 决定性那场撤了 4 次
                            #   （19/19、13/13、6/6、1/1），hsor 被拉向家、离敌人反而从 2386 缩到 805，4 秒后死。
                            #   两层规则方向相反 = 两边都不成立。在家门口交给策略层守，这里只记数。
                            fcx = sum(f.x for f in fz) / len(fz)
                            fcy = sum(f.y for f in fz) / len(fz)
                            at_home = near_home(fcx, fcy, home, halls)   # 09-16：分矿门口也算家门口
                            catastrophic_home = at_home and t_them > HOME_COLLAPSE_RATIO * t_us
                            if at_home and t_them > 1.6 * t_us and not catastrophic_home:
                                home_hold += 1
                            confirmed_loss = battle_commit.observe(now, mz, fz, t_them, t_us)
                            retreat_ready = squad_retreat_ready(now, last_squad, catastrophic_home, last_home_break)
                            if (confirmed_loss and (not at_home or catastrophic_home) and retreat_ready
                                    and (catastrophic_home or not brain_controls_battle(claims, mz))):
                                last_squad = now
                                if catastrophic_home:
                                    last_home_break = now
                                n_out = 0
                                again = stolen = no_claim = no_move = 0
                                fallback = home_fallback_point(fcx, fcy, home, halls) if catastrophic_home else None
                                for u in mz:
                                    # ⚠ 原来这里对"已经拉过的人" continue —— 可它**还在交火圈里**，
                                    #   说明上一条撤退命令没生效（或者它走回来了），却要等 8 秒租约
                                    #   到期才有人再管它。那正是被丢下送死的那一两个。还在圈里就重下。
                                    if pulled.get(u.addr, 0.0) > now:
                                        if (u.order or 0) in (0x000D0012,0x000D0003):
                                            continue  # 撤退监视器负责卡住重试，整队规则不覆盖正在走的路线。
                                        again += 1
                                    if not claims.take(u.addr, LEASE, prio=PRIO["safety"]):
                                        # 撤退比捡装备、打野轮换都要紧 —— 抢过来。
                                        if not claims.take(u.addr, LEASE, steal=True):
                                            no_claim += 1
                                            continue
                                        stolen += 1
                                    if fallback is not None:
                                        tx, ty = fallback
                                    else:
                                        dx, dy = home[0] - u.x, home[1] - u.y
                                        nn = math.hypot(dx, dy) or 1.0
                                        tx, ty = u.x + dx / nn * PULL_BACK * 1.5, u.y + dy / nn * PULL_BACK * 1.5
                                    if retreat_move(u, tx, ty)[0]:
                                        pulled[u.addr] = now + LEASE
                                        n_out += 1
                                    else:
                                        no_move += 1
                                        claims.give_back(u.addr)
                                if n_out:
                                    if catastrophic_home:
                                        home_break += 1
                                    publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                                    squad_ret += 1
                                    # 09-17：也告诉策略层这一波别追/别猎杀（原来只有胜率叫撤写信号；212133 猎杀在遭遇战撤退之后照样派人回去）
                                    odds_seq += 1
                                    fightodds.write_signal(RETREATF, ses.pid, snap.epoch, clock.read() if clock else None, {},
                                                           fcx, fcy, len(mz), len(fz), odds_seq, why="血量输出")
                                    retreat_tp = (now + itemuse.TP_RETREAT_WINDOW, fcx, fcy)   # 09-23 离家远就读回城（物品段）
                                    note(f"{'基地压倒性劣势退到建筑后' if catastrophic_home else '遭遇战撤退'}(100ms)：打光他们要 {t_them:.0f}s、"
                                         f"他们打光我们要 {t_us:.0f}s"
                                         f"（这片战场 我方 {len(mz)} 人 {int(m_hp)} 血 "
                                         f"vs 敌 {len(fz)} 个 {int(f_hp)} 血）"
                                         f"—— 拉回 {n_out}/{len(mz)} 人"
                                         f"（重下 {again}、抢租约 {stolen}、"
                                         f"抢不到 {no_claim}、命令没发出 {no_move}）")

            # ★★ 09-17 v291 **打上几秒之后按胜率叫撤**（fightodds.py）。
            #   打不过时要考虑整体回撤，不能硬顶到全军覆没。
            #   上面那条（t_them > 1.6 t_us）是瞬时的血量/输出比，不管这一仗已经打了多久；
            #   世界模型 M1：接触那一刻谁都判不准（推演 AUC 0.61），打上 5 秒 AUC 0.77、10 秒 0.81。
            #   两条是「或」：任一条成立就撤；家门口一律不撤（同上，策略层在家无条件守）。
            #   撤的是**这片战场 1500 内所有自己人**（和"撤的是所有人"同一个理由），并写 fight_retreat.json 让策略层结束追击。
            f_contact = len(fz) >= 2 and bool(mz)
            if f_contact or odds.start is not None:
                if clock is None or clock_pid != ses.pid:
                    clock = fightodds.GameClock(ses.cmd) if ses.cmd is not None else None
                    clock_pid = ses.pid
                    odds.reset()
                gs = clock.read() if clock is not None else None
                if gs is None:
                    odds_noclock += 1
                dec = odds.update(gs, mine_hot if f_contact else [], foes_near if f_contact else [], f_contact)
                if dec:
                    fcx, fcy = fightodds.centroid(fz)
                    if near_home(fcx, fcy, home, halls):
                        odds_home += 1
                    elif (now - last_squad > 6.0 and not brain_controls_battle(claims,mz) and model_commit.observe(
                            now, mz, fz,
                            sum(f.hp for f in fz) / max(.1, sum(_unit_dps(u.type) for u in mz)),
                            sum(u.hp for u in mz) / max(.1, sum(_unit_dps(f.type) for f in fz)), model_bad=True)):
                        last_squad = now
                        crew = fightodds.near(mine_hot, fcx, fcy, 1500.0)
                        n_out = again = stolen = no_claim = no_move = 0
                        for u in crew:
                            if pulled.get(u.addr, 0.0) > now:
                                if (u.order or 0) in (0x000D0012,0x000D0003):
                                    continue
                                again += 1
                            if not claims.take(u.addr, LEASE, prio=PRIO["safety"]):
                                if not claims.take(u.addr, LEASE, steal=True):
                                    no_claim += 1
                                    continue
                                stolen += 1
                            dx, dy = home[0] - u.x, home[1] - u.y
                            nn = math.hypot(dx, dy) or 1.0
                            sq_x, sq_y = u.x + dx / nn * PULL_BACK * 1.5, u.y + dy / nn * PULL_BACK * 1.5
                            sq_ok, sq_path = retreat_move(u, sq_x, sq_y)
                            if sq_ok:
                                rt_track(u, sq_x, sq_y, "整队撤退·" + sq_path)
                                pulled[u.addr] = now + LEASE
                                rotating.pop(u.addr, None)
                                n_out += 1
                            else:
                                no_move += 1
                                claims.give_back(u.addr)
                        if n_out:
                            publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                            odds_ret += 1
                            odds_seq += 1
                            fightodds.write_signal(RETREATF, ses.pid, snap.epoch, gs, dec, fcx, fcy, len(crew), len(fz), odds_seq)
                            retreat_tp = (now + itemuse.TP_RETREAT_WINDOW, fcx, fcy)   # 09-23 离家远就读回城（物品段）
                        note(f"胜率叫撤(世界模型)：接触后 {dec['k']:.0f} 游戏秒 胜率 {dec['p']:.0%}（门槛 {dec['thr']:.0%}），"
                             f"近处 我方 {dec['n_near'][0] if dec['n_near'] else '?'} vs 敌 {dec['n_near'][1] if dec['n_near'] else '?'}"
                             f" —— 拉回 {n_out}/{len(crew)} 人（重下 {again}、抢租约 {stolen}、抢不到 {no_claim}、命令没发出 {no_move}）")

            # ★★★ 交战期间**持续下攻击令** —— mod_fight 录下来的差距在这里。
            #
            #   现象：我方实际攻击次数远少于敌方，单位要么在移动，
            #          要么各打各的，缺少高手那样的整体战斗节奏。
            #   录了两场仗（547 帧 / 405 帧），数据比看上去更严重：
            #       我方 攻击令  6% / 21%    无订单 92% / 72%
            #       敌方 攻击令 58% / 89%    无订单 14% /  2%
            #   **敌方下攻击令的频率是我们的四到十倍。**
            #   根因是节奏：我方的攻击令来自策略层，**一拍 3 秒下一次**，
            #   而这是 100 毫秒尺度的仗 —— 两次下令之间二十几帧没人管，
            #   单位就靠引擎的自动索敌各打各的。
            #
            #   ⚠ 只填空档：**只对引擎说"没有任务"的单位下令**。
            #     正在撤退的有移动任务、已经在打的有攻击任务，都不碰 ——
            #     否则这条会和撤退、拉残血、轮换互相打架，
            #     那是今天反复栽过的"两条规则抢同一个单位"。
            #   ⚠ 目标选择和策略层的 W3-TargetScore 同一套口径：
            #     治疗 ×6、施法 ×2.5、英雄 ×3，再乘 1000/血 和距离项 ——
            #     口径不一致会让两层互相拉扯。
            # 施法回执：施法令发出 CAST_RCPT_DELAY 秒后看这个技能进没进冷却（一轮最多查 1 个）
            if cast_pending and now - cast_pending[0][2] >= CAST_RCPT_DELAY:
                c_addr, c_name, _c_at, c_stage, c_path = cast_pending.pop(0)
                if any(x.addr == c_addr and x.hp > 0 for x in fighters):
                    c_ab = ses.cmd.find_ability(c_addr, SPELL_ABIL.get(c_name, ""))
                    c_ord = None
                    if c_ab and c_ab.get("level") and not (c_ab.get("flags", 0) & 0x200):
                        c_ord = ses.cmd.current_order(c_addr)
                    ck = cast_receipt(c_ab, c_ord, SPELL_ORDERS.get(c_name))
                    if ck == "没进冷却·还挂着施法令" and c_stage == 0:
                        cast_pending.append((c_addr, c_name, now, 1, c_path))    # 还在走进射程：再等一轮
                        ck = None
                    elif c_stage == 1:
                        ck = f"{ck}（第二次看）"
                else:
                    ck = "英雄没了"
                if ck:
                    key = f"{c_name}·{c_path}·{ck}"
                    cast_rcpt[key] = cast_rcpt.get(key, 0) + 1
                    if ck.startswith("进冷却"):
                        cast_landed[c_name] += 1

            # 攻击令回执：下令 ATK_RCPT_DELAY 秒后读这个兵的当前订单（一轮最多查 1 个，约 16ms）
            if atk_pending and now - atk_pending[0][2] >= ATK_RCPT_DELAY:
                a_addr, a_tgt, _a_at = atk_pending.pop(0)
                a_unit = next((x for x in fighters if x.addr == a_addr and x.hp > 0), None)
                a_ord = ses.cmd.current_order(a_addr) if a_unit else None
                a_task = (ses.cmd.current_task(a_addr)
                          if (a_unit and a_ord is None and a_unit.has_task) else None)
                k = attack_receipt(a_ord, a_tgt, a_unit.has_task if a_unit else None, a_task)
                atk_rcpt[k] = atk_rcpt.get(k, 0) + 1

            # 撤退令回执（见 RT_DELAY 上面那段）：一轮最多看 1 个；走近了不读，没走近才读订单槽 / 任务 / 英雄的 buff
            if rt_pending and now - rt_pending[0][3] >= RT_DELAY:
                r_addr, r_tx, r_ty, _r_at, r_d0, r_how, r_slot0 = rt_pending.pop(0)
                r_u = next((x for x in snap.mine if x.addr == r_addr and x.hp > 0), None)
                if r_u is None:
                    rt_rcpt[(r_how, "死了")] += 1
                else:
                    prog = r_d0 - math.hypot(r_tx - r_u.x, r_ty - r_u.y)
                    r_ord = r_task = None
                    try:
                        if prog < RT_PROGRESS:
                            r_ord = ses.cmd.current_order(r_addr)
                            if r_ord is None and r_u.has_task:
                                r_task = ses.cmd.current_task(r_addr)
                    except Exception:                        # noqa: BLE001
                        pass
                    k = retreat_receipt(prog, r_ord, r_task, (r_tx, r_ty))
                    rt_rcpt[(r_how + ("·英雄" if r_u.is_hero else ""), k)] += 1
                    if r_u.is_hero and r_slot0 is not None:
                        # 下令当下订单槽里是不是撤退令（0x000D0012）：是 = 替换成功，之后又被改掉；不是 = 撤退令根本没顶掉旧令
                        rt_rcpt[("英雄·下令当下", "挂上撤退令" if r_slot0.startswith("000D0012") else "还是旧令/" + r_slot0[:8])] += 1
                    if k != "在走" and r_u.is_hero and now - rt_buff_at[0] >= RT_BUFF_EVERY:
                        rt_buff_at[0] = now
                        try:
                            bs = sorted({a["code"] for a in ses.cmd.abilities(r_addr) if str(a.get("code", "")).startswith("B")})
                        except Exception:                    # noqa: BLE001
                            bs = ["读不到"]
                        for b in bs or ["没有buff"]:
                            rt_buffs[b] += 1
                        o_tgt = ""
                        if r_ord and r_ord.get("target"):
                            hit = next((x for x in snap.units if (x.handle_lo, x.handle_hi) == tuple(r_ord["target"])), None)
                            o_tgt = f" 目标 {hit.type}/{hit.owner}" if hit else " 目标不在快照里"
                        o_txt = (f"{int(r_ord.get('order') or 0):08X}->({r_ord.get('x') or 0:.0f},{r_ord.get('y') or 0:.0f}){o_tgt}"
                                 if r_ord else "槽空") + f"（下令当下 {r_slot0}）"
                        t_txt = ((r_task.get("cls") or "?") if r_task else "无")
                        note(f"撤退令回执·英雄没走：{r_u.type}@{r_addr:X} {int(r_u.hp)}/{int(r_u.hp_max)} [{r_how}] {k}："
                             f"{RT_DELAY:.1f} 秒走近 {prog:.0f}/{r_d0:.0f}，订单 {o_txt}，任务 {t_txt}，身上 {bs or '没有 buff'}")

            # 09-21 养伤中的人（unitmind.Minds）不补攻击令、不转火、不改打 —— 本模块自己也不能把它派回去
            mz_act = [u for u in mz if u.addr not in minds.out
                      and guard_held.get(u.addr, 0.0) <= now]      # 护卫握着的水元素不补（见 GUARD_HOLD）
            if fz and mz_act:
                # 刚躲出暴风雪的握满 BLIZ_HOLD、刚下施法令的握满 CAST_HOLD 再说（refill_candidates）
                idle_now, n_held = refill_candidates(mz_act, now, rotating, dodged, pulled, cast_hold)
                cast_hold_blocked += n_held
                sent = 0
                # 一拍最多补 6 个，别把通道打满；挑目标 = target_score（v279 远程集火治疗、近战先杀脆皮）× 跟打加权
                for u, best, joined in pick_refill(idle_now[:6], fz, focus_recent, now, enemy_towers(snap.units, snap.me)):
                    if not claims.take(u.addr, 1.5, prio=PRIO["refill"]):
                        focus_recent.get(best.addr, {}).pop(u.addr, None)
                        continue
                    try:
                        tc = time.monotonic()
                        if ses.cmd.target_order(u.addr, best.addr, ORDER_ATTACK,
                                                best.x, best.y):
                            dlog("补攻击令", u, ("跟着队友打同一个（集火）" if joined else "闲着 —— 挑最该打的那个")
                                 + f"：{declog.unit_name(best.type)} {int(best.hp)}/{int(best.hp_max)}",
                                 target=best, cmd_s=time.monotonic() - tc,
                                 inputs={"跟打": bool(joined), "这片战场我方": len(mz), "敌方": len(fz),
                                         "目标离它": int(math.hypot(best.x - u.x, best.y - u.y))})
                            sent += 1
                            focus_join[joined] += 1
                            if len(atk_pending) < 3:
                                atk_pending.append((u.addr, (best.handle_lo, best.handle_hi), now))
                        else:
                            focus_recent.get(best.addr, {}).pop(u.addr, None)
                    finally:
                        claims.give_back(u.addr)
                if sent and now - last_engage > 5.0:
                    last_engage = now
                    note(f"补攻击令：这片战场我方 {len(mz)} 人，其中 {len(idle_now)} 个"
                         f"引擎说没任务 —— 补了 {sent} 个（敌 {len(fz)} 个）")

                # ★ 09-18 别打召唤物（summon_candidates / summon_switch_target）
                if any(f.type in SUMMONS for f in fz):
                    def _busy(x):
                        return (x.addr in rotating or dodged.get(x.addr, 0.0) > now or pulled.get(x.addr, 0.0) > now
                                or cast_hold.get(x.addr, 0.0) > now)
                    cands = summon_candidates(mz_act, fz, _busy)
                    t_read = time.monotonic()
                    for u in own_tg.due(cands, now):
                        if time.monotonic() - t_read > SUMMON_BUDGET:
                            break
                        try:
                            st = ses.cmd.current_task(u.addr)
                        except Exception:                        # noqa: BLE001
                            st = None
                        own_tg.put(u, st.get("target") if st else None, now)
                        summon_reads += 1
                    summon_h = {(f.handle_lo, f.handle_hi) for f in fz if f.type in SUMMONS}
                    for u in cands:
                        e = own_tg.ent.get(u.addr)
                        if (e is None or e["target"] not in summon_h or now - e["at"] > AGGRO_FRESH
                                or e["ident"] != CreepAggro._ident(u) or e["task"] != CreepAggro._task(u)):
                            continue
                        summon_hit += 1
                        nt = summon_switch_target(u, fz)
                        if nt is None:
                            summon_none += 1
                            continue
                        if not claims.take(u.addr, 1.5, prio=PRIO["refill"]):
                            summon_noclaim += 1
                            continue
                        try:
                            if ses.cmd.target_order(u.addr, nt.addr, ORDER_ATTACK, nt.x, nt.y):
                                summon_switched += 1
                                own_tg.put(u, (nt.handle_lo, nt.handle_hi), now)     # 下一轮任务句柄一变会重读确认
                        finally:
                            claims.give_back(u.addr)

                # ★★ v292 正在打别人的远程也转去集火最脆的敌方英雄（focus_hero）—— 补攻击令只管引擎说没任务的，已经在打小兵的远程不会自己换
                hero_fz = [f for f in fz if f.hp > 0 and f.is_hero]
                if hero_fz:
                    n_hf = 0
                    for u in mz_act:
                        if n_hf >= 6:
                            break
                        if (u.addr in rotating or dodged.get(u.addr, 0.0) > now or pulled.get(u.addr, 0.0) > now
                                or cast_hold.get(u.addr, 0.0) > now):
                            continue
                        ht = focus_hero(u, hero_fz)
                        if ht is None:
                            continue
                        prev_hf = hero_focus_last.get(u.addr)
                        if prev_hf is not None and ((prev_hf[0] == ht.addr and now - prev_hf[1] < FOCUS_HERO_SAME)
                                                    or now - prev_hf[1] < FOCUS_HERO_SWITCH):
                            continue
                        if not claims.take(u.addr, 1.5, prio=PRIO["refill"]):
                            continue
                        try:
                            ok_hf = ses.cmd.target_order(u.addr, ht.addr, ORDER_ATTACK, ht.x, ht.y)
                        finally:
                            claims.give_back(u.addr)
                        if ok_hf:
                            hero_focus_last[u.addr] = (ht.addr, now)
                            hero_focus_sent += 1
                            n_hf += 1

            # ── 英雄被敌军集火：立刻拉（判据见 FOCUS_R 上面那段）────────────
            foe_army = [f for f in foes_live if foe_fighter_type(f.type)]
            # ★★ 09-17 英雄接触前预警（判据见 hero_danger）：家门口不拉（同遭遇战撤退）；拉得比拉残血远（PULL_BACK×2），并告诉策略层别追
            if foe_army:
                ours_d = [u for u in fighters if u.hp > 0] + [w for w in snap.mine if w.hp > 0 and w.type in WATER_TYPES]
                for h in [x for x in fighters if x.is_hero and x.hp > 0]:
                    if pulled.get(h.addr, 0.0) > now or now - danger_at.get(h.addr, 0.0) < HERO_DANGER_EVERY:
                        continue
                    danger_at[h.addr] = now
                    dz = precontact_hero_retreat(h, foe_army, ours_d)
                    if dz is None:
                        continue
                    # ★★ 09-22 守家、血够就不先跑（HIT_WIN 上面那段 ③）：1-388 大法师满血在家门口被这条每 7 秒拉一次，
                    #   离家 3000~4000 看着 22 个敌人拆家、没人打过它。真被打了有被集火 / 掉血 / 残血那几条。
                    if h.hp >= HOME_HERO_HOLD_FRAC * max(1, h.hp_max) and home_fight(h, foe_army, home, halls):
                        danger_home_hold += 1
                        continue
                    if near_home(h.x, h.y, home, halls):
                        # 09-17 设计要求英雄存活为最高优先级；守家时血低于 HOME_HERO_HOLD_FRAC 的照旧先拉。
                        danger_home += 1
                    if not claims.take(h.addr, LEASE, prio=PRIO["safety"]) and not claims.take(h.addr, LEASE, steal=True):
                        danger_noclaim += 1
                        continue
                    close_threats = [f for f in foe_army if _dist(f, h) <= HERO_DANGER_R]
                    threat_r = HERO_DANGER_R if len(close_threats) >= HERO_DANGER_MIN else HERO_RESERVE_R
                    threats = close_threats or [f for f in foe_army if _dist(f, h) <= threat_r]
                    allies = [u for u in ours_d if u.addr != h.addr and u.hp > 0]
                    harbor = friendly_harbor(h, foe_army, ours_d)   # 先往大部队撤（1-298：直直背离敌人退进了北边水里）
                    tx, ty = harbor or focus_escape_point(h, threats, allies, home, back=PULL_BACK * 2)
                    tx, ty, tw_fix = tower_safe_point(h, tx, ty, enemy_towers(snap.units, snap.me))
                    tower_fix += tw_fix
                    tc = time.monotonic()
                    ok_r, r_path = retreat_move(h, tx, ty)
                    if ok_r:
                        rt_track(h, tx, ty, "英雄预警·" + r_path)
                        pulled[h.addr] = now + LEASE
                        solo[h.addr] = now + SOLO_LEASE
                        rotating.pop(h.addr, None)
                        danger_pulls += 1
                        danger_harbor += 1 if harbor else 0
                        dlog("英雄预警撤退·" + ("往大部队撤" if harbor else "背离敌人"), h,
                             f"身边 {int(threat_r)} 内敌 {dz[2]} 个、我方 {dz[3]} 个，打光他们要 {dz[0]:.0f}s、"
                             f"他们打光我们要 {dz[1]:.0f}s", point=(tx, ty), cmd_s=time.monotonic() - tc,
                             inputs={"血": f"{int(h.hp)}/{int(h.hp_max)}", "身边敌人": dz[2], "身边我方": dz[3],
                                     "去向": "大部队" if harbor else "背离敌人"})
                        publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                        publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
                        # 个人英雄预警不能升级成全军撤退；整队去留由持续交战证据决定。
                        note(f"英雄预警撤退：{h.type}@{h.addr:X} {h.hp}/{h.hp_max} 身边 {int(threat_r)} 内敌 {dz[2]} 个、我方 {dz[3]} 个，"
                             f"打光他们要 {dz[0]:.0f}s、他们打光我们要 {dz[1]:.0f}s —— 没接触就先拉（别等被围）")
                    else:
                        claims.give_back(h.addr)
            if foe_army:
                spent = 0.0
                for h in [x for x in fighters if x.is_hero and x.hp > 0]:
                    if pulled.get(h.addr, 0.0) > now or now - focus_at.get(h.addr, 0.0) < FOCUS_EVERY:
                        continue
                    near_f = sorted((f for f in foe_army if math.hypot(f.x - h.x, f.y - h.y) <= FOCUS_R),
                                    key=lambda f: math.hypot(f.x - h.x, f.y - h.y))[:FOCUS_MAX]
                    if not near_f or spent >= FOCUS_BUDGET:
                        continue
                    focus_at[h.addr] = now
                    t_read = time.monotonic()
                    rows, by_addr = [], {}
                    for f in near_f:
                        if time.monotonic() - t_read + spent > FOCUS_BUDGET:
                            break
                        try:
                            task = ses.cmd.current_task(f.addr)
                        except Exception:                        # noqa: BLE001
                            task = None
                        tg = task.get("target") if task else None
                        rows.append((f.addr, (int(tg[0]), int(tg[1])) if tg else None))
                        by_addr[f.addr] = f
                    spent += time.monotonic() - t_read
                    focus_reads += 1
                    k, k_known, k_unknown = focus_stats(h, rows)
                    focus_last[h.addr] = k
                    focus_peak["0" if k == 0 else "1~2" if k < 3 else "3~5" if k < 6 else "6+"] += 1
                    focus_unread += k_unknown
                    focus_rows += len(rows)
                    if not hero_focus_danger(k, k_known, k_unknown, len(near_f)):
                        continue
                    if k < FOCUS_PULL_N:
                        focus_extrap += 1      # 明确读到的不够 3 个，靠外推救下的（0 = 这条新路没生效）
                    focus_hot += 1
                    me_hl = (h.handle_lo, h.handle_hi)
                    attackers = [by_addr[a] for a, tg in rows if tg is not None and tuple(tg) == me_hl]
                    if not claims.take(h.addr, LEASE, prio=PRIO["safety"]):
                        focus_noclaim += 1
                        continue
                    # 先往大部队撤（1-299：大法师被集火往东南拉，部队在往西撤，追兵只追它一个）
                    harbor = friendly_harbor(h, foe_army, fighters)
                    tx, ty = harbor or focus_escape_point(h, attackers, [u for u in fighters if u.hp > 0], home)
                    tx, ty, tw_fix = tower_safe_point(h, tx, ty, enemy_towers(snap.units, snap.me))
                    tower_fix += tw_fix
                    if harbor is not None:
                        hero_ctl["集火撤往大部队"] += 1
                    tc = time.monotonic()
                    ok_r, r_path = retreat_move(h, tx, ty)
                    if ok_r:
                        rt_track(h, tx, ty, "英雄被集火·" + r_path)
                        dlog("英雄被集火撤" + ("·往大部队" if harbor else ""), h, f"身边读了 {len(rows)} 个敌人，{k} 个的攻击目标是它", point=(tx, ty),
                             cmd_s=time.monotonic() - tc,
                             inputs={"血": f"{int(h.hp)}/{int(h.hp_max)}", "在打它的": k, "读到目标的": k_known,
                                     "读不到目标的": k_unknown, "打它的人": [declog.unit_name(a.type) for a in attackers]})
                        pulled[h.addr] = now + LEASE
                        solo[h.addr] = now + SOLO_LEASE
                        rotating.pop(h.addr, None)
                        cast_hold.pop(h.addr, None)
                        publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                        publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
                        focus_pulls += 1
                        saved += 1
                        note(f"英雄被集火：{h.type}@{h.addr:X} {int(h.hp)}/{int(h.hp_max)}，"
                             f"身边 {len(rows)} 个敌人里 {k} 个在打它 —— 拉到 ({tx:.0f},{ty:.0f})"
                             f"（{int((time.monotonic() - now) * 1000)}ms 内响应）")
                    else:
                        claims.give_back(h.addr)

            # ── 09-17 物品（itemuse）：生命药水自救 > 回城卷轴回防 > 恢复卷轴打完回血 ──────────
            #   判据先用快照算（便宜），成立了才读背包（约 150ms，3 秒内复用）；背包里没有这件的 10 秒内不再读。
            if now - last_item >= 0.3:
                last_item = now
                army_foes = [f for f in foes_live if foe_fighter_type(f.type)]
                hostile = army_foes + [c for c in snap.units if c.owner == CREEP_OWNER and c.hp > 0]
                halls_u = [u for u in snap.mine if u.type in HALL_TYPES]
                hall_u = min(halls_u, key=lambda u: math.hypot(u.x - home[0], u.y - home[1])) if halls_u else None
                own_hall_xy = [(u.x, u.y) for u in halls_u]      # 09-23 守自家大厅 / 分矿时不读回城（itemuse.TP_OWN_BASE_R）
                home_foes = sum(1 for f in army_foes if hall_u is not None
                                and math.hypot(f.x - hall_u.x, f.y - hall_u.y) <= itemuse.TP_HOME_R)
                for h in [x for x in fighters if x.is_hero and x.hp > 0]:
                    near_n = sum(1 for f in army_foes if math.hypot(f.x - h.x, f.y - h.y) <= itemuse.POTION_FOE_R)
                    self_n = sum(1 for f in hostile if math.hypot(f.x - h.x, f.y - h.y) <= itemuse.TP_SELF_R)
                    want = None
                    tp_retreat = False
                    seen_mp = mana_seen.get(h.addr)
                    mp_now = seen_mp[1] if seen_mp and now - seen_mp[0] < 2.0 else None
                    army_near = sum(1 for a in fighters if a.addr != h.addr and a.hp > 0 and not a.is_hero
                                    and itemuse.dist(a, h) <= itemuse.TP_AREA)
                    if itemuse.potion_due(h, near_n, focus_last.get(h.addr, 0)):
                        want = "phea"
                    elif (retreat_tp is not None and retreat_tp[0] > now and hall_u is not None
                          and itemuse.retreat_portal_due(h, retreat_tp[1:], (hall_u.x, hall_u.y), army_near, own_hall_xy)):
                        want, tp_retreat = "stwp", True     # 09-23 打输了整队撤、离家远：读回城整队传回家（AMAI TeleportHome）
                    elif itemuse.town_portal_due(h, hall_u, home_foes, self_n, own_hall_xy):
                        want = "stwp"
                    elif itemuse.mana_potion_due(h, mp_now, near_n):
                        want = "pman"            # 09-21 施法英雄打仗时没蓝：喝魔法药水（itemuse.MANA_HEROES）
                    elif itemuse.regen_formation_ready(h, [a for a in fighters if itemuse.dist(a, h) <= itemuse.REGEN_GATHER_R],
                                                       hostile):
                        # 09-22 晚：只数英雄 REGEN_GATHER_R 内的伤兵（原来传全图：家里有一个伤兵，野外就永远「没到齐」）
                        want = "sreg"
                    if want is None or item_cool.get((h.addr, want), 0.0) > now:
                        continue
                    if want == "sreg":
                        lease = claims.lease_of(h.addr) if hasattr(claims, "lease_of") else None
                        if lease and lease[0] == 1 and lease[1] == 75:
                            continue  # 大脑整队恢复负责集合、使用和回执，避免双方消费同一卷轴。
                    cached = bag_cache.get(h.addr)
                    if cached is None or now - cached[0] > 3.0:
                        cached = (now, ses.cmd.inventory(h.addr))
                        bag_cache[h.addr] = cached
                    bag = cached[1] or []
                    if want not in bag:
                        item_cool[(h.addr, want)] = now + 10.0
                        item_used[f"{want}·背包里没有"] += 1
                        continue
                    lease_s, prio = (6.0, PRIO["safety"]) if want == "stwp" else (1.0, PRIO["cast"])
                    if not claims.take(h.addr, lease_s, prio=prio):
                        item_used[f"{want}·抢不到指挥权"] += 1
                        continue
                    slot = bag.index(want)
                    ok = ses.cmd.use_item(h.addr, ses.cmd.USESLOT1 + slot,
                                          target=hall_u.addr if want == "stwp" else 0)
                    item_used[f"{want}·{'用了' if ok else '被拒'}"] += 1
                    if ok:
                        dlog("用物品", h, {"phea": "血低，喝生命药水", "pman": "打仗时没蓝，喝魔法药水", "sreg": "伤兵到齐，用恢复卷轴",
                                           "stwp": "打输了整队撤、离家远，读回城整队传回家" if tp_retreat
                                           else "家被打、英雄离家远，读回城卷轴"}.get(want, want),
                             inputs={"物品": want, "血": f"{int(h.hp)}/{int(h.hp_max)}", "身边敌军": near_n,
                                     "在打它的": focus_last.get(h.addr, 0), "家里敌军": home_foes})
                        if want == "stwp":
                            # ★★ 09-22 回城带兵（itemuse.TP_AREA 上面那段）：引导时间内走得到的战斗单位叫到英雄身边、锁到引导结束
                            tp_hold = itemuse.TP_CHANNEL / GAME_SPEED + 1.0
                            # 撤退回城：刚被整队撤退拉着的人（pulled）正是要一起传走的，照样叫过来
                            crew = [u for u in snap.mine if u.hp > 0 and u.type not in WORKERS and not is_building(u)
                                    and (can_move(u) or u.type in SUMMONS)
                                    and u.addr not in rotating and (tp_retreat or pulled.get(u.addr, 0.0) <= now)
                                    and dodged.get(u.addr, 0.0) <= now]
                            if tp_retreat:
                                retreat_tp = None                   # 一次撤退只读一张
                                tp_retreats["读回城整队撤"] += 1
                            tp_moves, tp_far = itemuse.tp_gather(h, crew, SPEED.get)
                            tp_called = 0
                            for u, tx, ty in tp_moves:
                                if not claims.take(u.addr, tp_hold, prio=PRIO["cast"]):
                                    item_used["stwp·靠拢抢不到指挥权"] += 1
                                    continue
                                if retreat_move(u, tx, ty)[0]:
                                    dodged[u.addr] = now + tp_hold
                                    tp_called += 1
                                else:
                                    claims.give_back(u.addr)
                            item_used["stwp·叫来靠拢"] += tp_called
                            item_used["stwp·够不着"] += tp_far
                            note(f"回城带兵：{h.type}@{h.addr:X} 引导 {itemuse.TP_CHANNEL:.0f} 游戏秒内走得到的 {len(tp_moves)} 个，"
                                 f"叫来靠拢 {tp_called} 个（够不着 {tp_far} 个）")
                    item_cool[(h.addr, want)] = now + (6.0 if ok else 10.0)
                    bag_cache.pop(h.addr, None)
                    if want != "stwp":
                        claims.give_back(h.addr)
                    note(f"用物品：{h.type}@{h.addr:X} {int(h.hp)}/{int(h.hp_max)} 第{slot + 1}格 {want}"
                         f"（身边敌军 {near_n}、在打它 {focus_last.get(h.addr, 0)}、家里敌军 {home_foes}）-> {'ok' if ok else '被拒'}")

            # ── 09-21 牧师定向治疗（priest_pick）：受伤的英雄 > 战斗力 × 缺血比例最高的，引擎自动治疗只是就近随便奶 ──
            if now - last_priest >= PRIEST_EVERY:
                last_priest = now
                # 送死名单里的兵附近的牧师关自动治疗，离开 / 名单空了再开（见 ORDER_HEAL_OFF 上面那段）
                victims = [u for u in fighters if u.addr in sac and u.hp > 0]
                for pr in [x for x in fighters if x.type in PRIESTS and x.hp > 0]:
                    near_v = any(math.hypot(v.x - pr.x, v.y - pr.y) <= PRIEST_SAC_R for v in victims)
                    if near_v and pr.addr not in priest_heal_off:
                        if ses.cmd.immediate_order(pr.addr, ORDER_HEAL_OFF):
                            priest_heal_off.add(pr.addr)
                            priest_tally["送死名单边上·关自动治疗"] += 1
                    elif not near_v and pr.addr in priest_heal_off:
                        if ses.cmd.immediate_order(pr.addr, ORDER_HEAL_ON):
                            priest_heal_off.discard(pr.addr)
                            priest_tally["送死名单走了·开自动治疗"] += 1
                priest_heal_off &= {x.addr for x in fighters if x.type in PRIESTS and x.hp > 0}
                for pr in [x for x in fighters if x.type in PRIESTS and x.hp > 0 and x.addr not in minds.out
                           and pulled.get(x.addr, 0.0) <= now and x.addr not in rotating]:
                    tgt = priest_pick(pr, fighters, skip=sac)
                    if tgt is None:
                        continue
                    if priest_last.get(pr.addr, (None, 0.0))[0] == tgt.addr and now - priest_last[pr.addr][1] < PRIEST_REPEAT:
                        continue                          # 还是同一个人：自动治疗会接着奶，别每 0.5 秒重下
                    # ★ 09-23 体检：治疗令 1752 次被拒 817 次（47%，引擎只回 0 不说原因）。牧师开着自动治疗，蓝一直在被引擎花掉 ——
                    #   蓝不够一发的先不下（世界块本地读，不花请求），计数「没蓝不下」；剩下的被拒再看还有多少
                    pm = ses.cmd.mana(pr.addr)
                    if pm is not None and pm < PRIEST_HEAL_MANA:
                        priest_tally["没蓝不下"] += 1
                        priest_last[pr.addr] = (tgt.addr, now)
                        continue
                    if not claims.take(pr.addr, 1.0, prio=PRIO["cast"]):
                        priest_tally["抢不到"] += 1
                        continue
                    tc = time.monotonic()
                    # ★ 09-22 对局画面（1-307 13:32~14:37）：在家等牧师加血太慢，浪费时间 —— 两个牧师每 3 秒一条治疗令、引擎回 ok，
                    #   65 游戏秒里目标步兵只涨了 182 -> 198（= 自然回血 0.25/秒），牧师离它 291/356（治疗射程 250）一步没动，
                    #   那 20 秒命令记录里除了这条治疗令没有任何别的令打断。原来走 target_order（DLL 动作 28）——
                    #   那是给右键/捡装备造的订单，目标施法走它 30 次只进冷却 1 次（w3cmd.cast_target 文档）。英雄的目标技能早改走 cast_target 那条路，这里漏了
                    if ses.cmd.cast_target(pr.addr, ORDER_HEAL, tgt.addr, tgt.x, tgt.y):
                        dlog("牧师治疗", pr, ("受伤的英雄先保" if tgt.is_hero else "身边战斗力 × 缺血最高的")
                             + f"：{declog.unit_name(tgt.type)} {int(tgt.hp)}/{int(tgt.hp_max)}",
                             target=tgt, cmd_s=time.monotonic() - tc,
                             inputs={"距离": int(math.hypot(tgt.x - pr.x, tgt.y - pr.y)), "缺血": int(tgt.hp_max - tgt.hp)})
                        priest_last[pr.addr] = (tgt.addr, now)
                        priest_tally["英雄" if tgt.is_hero else "按战斗力"] += 1
                    else:
                        priest_last[pr.addr] = (tgt.addr, now)       # 被拒（没蓝/够不着）也等 PRIEST_REPEAT，别每 0.5 秒重试
                        priest_tally["被拒"] += 1
                    claims.give_back(pr.addr)

            # ── 英雄放技能（判据见 plan_hero_cast / SPELLS 上面那段）──────────────────
            #   轮换出去 / 刚躲暴风雪的英雄不放 —— 一条施法令会取消它正在走的路。
            #   ★ 09-18 撤退中（本模块拉的）只放逃生技能（while_pulled：疾风步 / 闪烁 / 神圣护甲 / 魔法护盾）；
            #     保命租约是本模块握着的，所以不再 take / give_back —— give_back 会把 8 秒的保命租约一起还掉，策略层当场就能把它派回去。
            corpse_prev, died = corpse_events(corpse_prev, snap.units, snap.me)
            corpses = [c for c in corpses if now - c[2] <= CORPSE_KEEP] + [(x, y, now) for x, y in died]
            if followups:
                followups, done = run_followups(followups, now, ses.cmd, claims, fighters, snap.units, pulled)
                follow_done.update(done)
            foes_cast = [f for f in snap.units if f.hp > 0 and f.owner != snap.me
                         and (f.owner == CREEP_OWNER or (f.owner < 12 and foe_fighter_type(f.type)))]
            motion.update(foes_cast, now)
            if foes_cast:
                allies_cast = [a for a in snap.mine if a.hp > 0]
                foe_bld = [f for f in snap.units if f.hp > 0 and f.owner != snap.me and f.owner < 12 and is_building(f)]
                corpse_xy = [(c[0], c[1]) for c in corpses]
                still_now = {f.addr for f in foes_cast if motion.still(f, now)}
                for h in [x for x in fighters if x.is_hero and x.hp > 0]:
                    if h.addr in rotating or h.addr in dodged:
                        continue
                    h_pulled = pulled.get(h.addr, 0.0) > now
                    if cast_hold.get(h.addr, 0.0) > now and not h_pulled:
                        continue
                    # 09-16：先读蓝（0.5 秒缓存、附近有敌人才读）—— 蓝不够的技能不下令，
                    #   以前全靠引擎退令 + 10 秒退避，一局被退 9~13 次。
                    # ★ 09-23 体检：召唤凤凰 145 次全被拒（没学）。蓝和已学技能原来**只在 1400 内有敌人时才读**（老通道一次读要十几次请求），
                    #   远处有敌人时 lv 保持 None，plan_hero_cast 按「读不到、不按学没学挡」处理，于是去放没学的召唤。
                    #   现在两样都来自世界块的本地快照（不再往游戏里发请求），每次都读；读不到就这一轮先不放，也不缓存「读不到」。
                    seen_mp = mana_seen.get(h.addr)
                    if seen_mp and now - seen_mp[0] < MANA_TTL:
                        mp = seen_mp[1]
                    else:
                        mp = ses.cmd.mana(h.addr)
                        if mp is None:
                            mana_fail += 1
                        mana_seen[h.addr] = (now, mp)
                    # 09-16：已学技能 10 秒读一次；012306 局山丘 1 级去放没学的雷霆一击被退
                    seen_lv = learned_seen.get(h.addr)
                    if seen_lv and now - seen_lv[0] < LEARN_TTL:
                        lv = seen_lv[1]
                    else:
                        got = ses.cmd.abilities(h.addr)
                        lv = {a["code"]: a["level"] for a in got} if got else None
                        if lv is not None:
                            learned_seen[h.addr] = (now, lv)
                    if lv is None:
                        unlearned["技能表读不到·先不放"] = unlearned.get("技能表读不到·先不放", 0) + 1
                        continue
                    tg_on = toggles_on.get(h.addr, {})
                    focus_ctx = {a: n for a, n in focus_last.items()
                                 if now - focus_at.get(a, -1e9) <= 2.0 * FOCUS_EVERY}
                    cast_ctx = {"pulled": h_pulled, "home": home, "foe_buildings": foe_bld, "corpses": corpse_xy,
                                "focus_counts": focus_ctx, "recent_hurt": {u.addr for u in hits},
                                "autocast": autocast_on.get(h.addr, {}), "toggles": set(tg_on),
                                "toggle_stale": {n for n, t in tg_on.items() if now - t > TOGGLE_TRUST},
                                "still": still_now}
                    plan = plan_hero_cast(h, foes_cast, allies_cast,
                                          lambda n, _h=h.addr: cds.ready(_h, n, now),
                                          mana=mp, low=mana_low, learned=lv, unlearned=unlearned, ctx=cast_ctx)
                    if not plan:
                        continue
                    name, order, how, tgt, px, py = plan[:6]
                    meta = plan[6] if len(plan) > 6 else {}
                    lvl_now = (lv or {}).get(SPELL_ABIL.get(name, "")) or None
                    # 09-16：下令前问引擎这个技能是不是还在冷却（技能的冷却标志）——
                    #   SPELL_CD 常数表比实际短，024229 局水元素被引擎以「冷却中」退了 2 次。
                    ab_now = ses.cmd.find_ability(h.addr, SPELL_ABIL.get(name, ""))
                    if ab_now and ab_now.get("level") and ab_now.get("flags", 0) & 0x200:
                        left = ses.cmd.cooldown_left(ab_now["addr"])
                        cds.hold(h.addr, name, now + (left / GAME_SPEED if left else 1.0))
                        engine_cd[name] = engine_cd.get(name, 0) + 1
                        continue
                    if h_pulled and not claims.mine(h.addr):
                        continue                         # 保命租约被更高的（人手动）抢走了：不碰
                    if not h_pulled and not claims.take(h.addr, 1.0, prio=PRIO["cast"]):
                        continue
                    ok = False
                    tc = time.monotonic()
                    try:
                        if how == "instant":
                            ok = ses.cmd.immediate_order(h.addr, order)
                        elif how == "point":
                            # 09-16：JASS IssuePointOrder 那条路（演练台 3/3 进冷却；动作 41 0/3）
                            ok = ses.cmd.cast_point_ctx(h.addr, order, px, py)
                        else:
                            ok = ses.cmd.cast_target(h.addr, order, tgt, px, py)
                    finally:
                        cast_s = time.monotonic() - tc
                        if not h_pulled:
                            if ok and not meta.get("autocast") and "toggle" not in meta:
                                claims.take(h.addr, cast_hold_seconds(name, lvl_now), prio=PRIO["cast"])
                            else:
                                claims.give_back(h.addr)
                    if ok:
                        cds.cast(h.addr, name, now, lvl_now)
                        casts[name] += 1
                        if how == "point" and _opts(name).get("friendly_fire") == "dodge":
                            # 09-22 自己的烈焰风暴烧自己人：记成火圈，火灭之前谁都不进（ff_zone 上面那段；推人在下面「火圈」那一段）
                            ff_zones.append(ff_zone(px, py, skill(name, lvl_now), h, now))
                            ff_casts += 1
                        tgt_u = next((x for x in snap.units if x.addr == tgt), None) if tgt else None
                        dlog("放技能", h, f"{SPELL_CN.get(name, name)}" + ("（撤退中）" if h_pulled else ""),
                             target=tgt_u, point=None if tgt_u else (px, py), cmd_s=cast_s,
                             inputs={"技能": name, "等级": lvl_now, "蓝": None if mp is None else int(mp),
                                     "方式": how, "身边敌人": sum(1 for f in foes_cast if math.hypot(f.x - h.x, f.y - h.y) <= 1200)})
                        if mp is not None and not meta.get("no_mana"):      # 缓存里先扣掉，别在 0.5 秒内拿旧蓝量再下一个
                            mana_seen[h.addr] = (now, mp - spell_mana(name, lvl_now))
                        if meta.get("autocast"):
                            autocast_on.setdefault(h.addr, {})[name] = lvl_now or 1
                        elif "toggle" in meta:
                            if meta["toggle"]:
                                toggles_on.setdefault(h.addr, {})[name] = now
                            else:
                                toggles_on.get(h.addr, {}).pop(name, None)
                        else:
                            # 开自动施法 / 开关不进冷却，回执分不出成败，不抽查
                            if len(cast_pending) < 4:
                                if how == "point":
                                    c_path0 = point_path_label(math.hypot(h.x - px, h.y - py),
                                                               getattr(ses.cmd, "last_cast_path", "动作41"))
                                else:
                                    c_path0 = {"instant": "瞬发"}.get(how, getattr(ses.cmd, "last_cast_path", "目标"))
                                cast_pending.append((h.addr, name, now, 0, c_path0))
                            # 补攻击令先别碰它（refill_candidates）；暴风雪这类引导按时长给（cast_hold_seconds）
                            cast_hold[h.addr] = now + cast_hold_seconds(name, lvl_now)
                        if meta.get("then"):
                            followups.append((now + meta.get("then_delay", 0.3), h.addr, meta["then"], name))
                        if now - last_cast_note > 5.0:
                            last_cast_note = now
                            where = f"-> {tgt:X}" if tgt else f"@({px:.0f},{py:.0f})"
                            note(f"英雄施法：{h.type}@{h.addr:X} {name} {where}{'（撤退中）' if h_pulled else ''}")
                    else:
                        cds.rejected(h.addr, name, now)
                        cast_rejects[name] += 1
                        if meta.get("toggle") is False:
                            toggles_on.get(h.addr, {}).pop(name, None)   # 关令被退：多半引擎没蓝已经自己关了
                        why = reject_reason(ses.cmd.find_ability(h.addr, SPELL_ABIL.get(name, "")),
                                            ses.cmd.mana(h.addr), spell_mana(name, lvl_now))
                        reject_why[f"{name}·{why}"] = reject_why.get(f"{name}·{why}", 0) + 1

            # ★★ 09-22 晚 火圈（ff_zone 上面那段）：自己的烈焰风暴烧完之前，圈边的自己人推出去、握到火灭；路过的等在圈外
            if ff_zones:
                ff_zones = [z for z in ff_zones if z["until"] > now]
                for a in [a for a, t in ff_held.items() if t <= now]:
                    ff_held.pop(a, None)
                ff_units = [u for u in snap.mine if u.hp > 0 and not is_building(u) and pulled.get(u.addr, 0.0) <= now]
                for z in ff_zones:
                    for u, tx, ty in ff_fence_moves(z, ff_units, ff_held):
                        if not claims.take(u.addr, z["until"] - now, prio=PRIO["dodge"]):
                            ff_noclaim += 1
                            continue
                        if retreat_move(u, tx, ty)[0]:
                            ff_held[u.addr] = z["until"]
                            dodged[u.addr] = max(dodged.get(u.addr, 0.0), z["until"])
                            if now - z["t0"] <= FF_ZONE_FRESH:
                                ff_moved += 1
                            else:
                                ff_fenced += 1
                        else:
                            claims.give_back(u.addr)

            # ★★ 09-22 兵种技能（unitspells.py 开头那段）：女巫变形术 / 减速、龙鹰空中锁镣 —— 挑高伤害的敌人、分散放、恢复了再补
            if now - last_usp >= USP_EVERY:
                last_usp = now
                usp_units = {u.addr: u for u in snap.units}
                for c_addr in usp.refresh(usp_units, now):
                    if pulled.get(c_addr, 0.0) <= now:
                        claims.give_back(c_addr)
                    cast_hold.pop(c_addr, None)

                def usp_mana_of(c, fresh=False):
                    got = usp_mana.get(c.addr)
                    if fresh or got is None or now - got[0] > USP_MANA_TTL:
                        try:
                            m = ses.cmd.mana(c.addr)
                        except Exception:                        # noqa: BLE001
                            m = None
                        usp_mana[c.addr] = got = (now, m)
                    return got[1]

                if usp.pending:
                    usp.receipts(now, lambda c: usp_mana_of(c, fresh=True), usp_units)
                for c_addr in list(usp.leash):          # 锁镣在引导：续租，别被补攻击令 / 大脑改掉
                    if pulled.get(c_addr, 0.0) <= now and claims.take(c_addr, USP_LEASH_HOLD, prio=PRIO["cast"]):
                        cast_hold[c_addr] = now + USP_LEASH_HOLD
                usp_casters = [u for u in snap.mine if u.type in unitspells.CASTER_TYPES and u.hp > 0
                               and u.addr not in rotating and pulled.get(u.addr, 0.0) <= now
                               and dodged.get(u.addr, 0.0) <= now]
                usp_foes = [f for f in snap.units if f.owner != snap.me and f.owner < 12 and f.hp > 0
                            and not is_building(f) and f.type not in WORKERS] if usp_casters else []

                def usp_tech_of(code):
                    got = usp_tech.get(code)
                    if got is None or now - got[0] > USP_TECH_TTL:
                        lv = None
                        try:
                            lv = ses.cmd.tech_count(ses.cmd.player_of(usp_casters[0].addr), code)
                        except Exception:                        # noqa: BLE001
                            lv = None
                        usp_tech[code] = got = (now, lv)
                    return got[1]

                if usp_foes and any(_dist(c, f) <= 800.0 for c in usp_casters for f in usp_foes):
                    for uc, usp_s, uf, um0 in usp.plan(usp_casters, usp_foes, now, usp_mana_of, usp_tech_of):
                        usp_hold = USP_LEASH_HOLD if usp_s.channel else USP_HOLD
                        if not claims.take(uc.addr, usp_hold, prio=PRIO["cast"]):
                            usp.tally[f"{usp_s.cn}·抢不到指挥权"] += 1
                            continue
                        t_usp = time.monotonic()
                        ok_u = False
                        try:
                            ok_u = bool(ses.cmd.cast_target(uc.addr, usp_s.order, uf.addr, uf.x, uf.y))
                        except Exception:                        # noqa: BLE001
                            ok_u = False
                        usp.issued(uc, usp_s, uf, now, um0, ok_u)
                        if not ok_u:
                            claims.give_back(uc.addr)
                            continue
                        cast_hold[uc.addr] = now + usp_hold
                        usp_mana.pop(uc.addr, None)
                        dlog("兵种技能", uc, f"{usp_s.cn} -> {declog.unit_name(uf.type)}（每秒伤害 {DPS.get(uf.type, 0):.0f}）",
                             target=uf, cmd_s=time.monotonic() - t_usp,
                             inputs={"技能": usp_s.cn, "蓝": int(um0), "目标血": f"{int(uf.hp)}/{int(uf.hp_max)}",
                                     "距离": int(_dist(uc, uf))})
                        if now - last_usp_note > 5.0:
                            last_usp_note = now
                            note(f"兵种技能：{uc.type}@{uc.addr:X} {usp_s.cn} -> {uf.type}@{uf.addr:X}"
                                 f"（{int(uf.hp)}/{int(uf.hp_max)}，距离 {int(_dist(uc, uf))}，蓝 {int(um0)}）")

            # ★★ 09-17 水元素护卫大法师（判据见 guard_picks / WATER_TYPES 上面那段）：水元素去打离大法师最近的敌人，把伤害接过去
            if guard_held:
                mages = {x.addr: x for x in fighters if x.type == "Hamg"}
                for w_addr in guard_release(guard_held, guard_last, {w: mages.get(h) for w, h in guard_of.items()},
                                            foes_cast, now):
                    guard_held.pop(w_addr, None)
                    guard_of.pop(w_addr, None)
                    claims.give_back(w_addr)
            if foes_cast:
                waters = [w for w in snap.mine if w.hp > 0 and w.type in WATER_TYPES]
                if waters:
                    guard_used = set()
                    for h in [x for x in fighters if x.type == "Hamg" and x.hp > 0]:
                        for w, t in guard_picks(h, [w for w in waters if w.addr not in guard_used], foes_cast,
                                                guard_last, now, fighters, creep_targets=creep_targets):
                            guard_used.add(w.addr)
                            if not claims.take(w.addr, GUARD_HOLD, prio=PRIO[GUARD_PRIO]):
                                guard_noclaim += 1
                                continue
                            tc = time.monotonic()
                            ok_g = False
                            try:
                                ok_g = ses.cmd.target_order(w.addr, t.addr, ORDER_ATTACK, t.x, t.y)
                            finally:
                                if ok_g:
                                    guard_held[w.addr] = now + GUARD_HOLD
                                    guard_of[w.addr] = h.addr
                                else:
                                    claims.give_back(w.addr)
                            if ok_g:
                                guard_last[w.addr] = (t.addr, now)
                                guard_sent += 1
                                dlog("水元素护卫大法师", w, ("野怪正在打我们的人" if t.owner >= 12 else "敌军到了大法师身边")
                                     + f"：{declog.unit_name(t.type)} 离大法师 {int(_dist(t, h))}", target=t,
                                     cmd_s=time.monotonic() - tc, inputs={"大法师血": f"{int(h.hp)}/{int(h.hp_max)}"})

            # ★★ 英雄独自扛一群野怪 —— 不等到快死才拉。
            #
            # 实战多次出现英雄独自一人对着一群野怪硬打。
            # 进场是有护卫判据的（小兵接火英雄才上），但**退场没有**：
            # 打起来之后小兵死光/被伤员锁撤走/被轮换拉出，英雄就成了唯一的
            # 集火目标。而把英雄丢在营地边的路不止一条（轮换送回、捡装备、
            # 集结路过被仇恨咬住），战略拍 3.2 秒一圈管不过来 ——
            # 所以做成这里的一条**硬规则**：身边 700 内没有任何小兵、
            # 又有 2+ 野怪够得着、而且已经在掉血，就立刻退出接触。
            # 满血不拉：满血英雄单刷小营地是职业选手都在用的正常打法。
            for h in [u for u in fighters if u.is_hero and u.hp > 0]:
                if h.addr in rotating or pulled.get(h.addr, 0.0) > now:
                    continue
                if h.hp >= h.hp_max * 0.92:
                    continue
                creeps_on = [c for c in snap.units
                             if c.owner == CREEP_OWNER and c.hp > 0
                             and math.hypot(c.x - h.x, c.y - h.y)
                                 <= RANGE.get(c.type, 100) + 200]
                if len(creeps_on) < 2:
                    continue
                if creep_output_safe(h,creeps_on,fighters,creep_targets):
                    continue
                escorts = [u for u in fighters
                           if not u.is_hero and u.hp > 0 and u.addr != h.addr
                           and pulled.get(u.addr, 0.0) <= now and u.addr not in rotating
                           and math.hypot(u.x - h.x, u.y - h.y) < 700]
                if escorts:
                    continue
                if not claims.take(h.addr, LEASE, prio=PRIO["safety"]):
                    continue
                dx, dy = home[0] - h.x, home[1] - h.y
                n2 = math.hypot(dx, dy) or 1.0
                if retreat_move(h, h.x + dx / n2 * PULL_BACK, h.y + dy / n2 * PULL_BACK)[0]:
                    pulled[h.addr] = now + LEASE
                    publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                    # 孤军名单单独写一份 —— hurt.json 那把锁按血量解，
                    # 而这个英雄血是满的，混在一起等于没上锁。
                    solo[h.addr] = now + SOLO_LEASE
                    publish_solo(solo, now, ses.pid, snap.epoch, snap.mine)
                    saved += 1
                    note(f"英雄独自扛野怪：{h.type}@{h.addr:X} "
                         f"{int(h.hp)}/{int(h.hp_max)} 被 {len(creeps_on)} 只咬着、"
                         f"700 内一个小兵都没有 —— 拉出来")
                else:
                    claims.give_back(h.addr)

            # 正在轮换的人不进"保命撤退"那条路 —— 那条会把他们拉回家 800、
            # 锁 8 秒、还写 hurt.json，等于把一次轮换变成一次退场。
            danger = [t for t in danger if t[0].addr not in rotating]
            if now - sac_read >= 1.0:
                sac_read = now
                sac = load_sacrifice(SACF, datetime.now().timestamp())
            if sac:
                danger = [t for t in danger if t[0].addr not in sac]      # 故意喂野怪的：不拉
            # ★★ 09-22 1-399 22 分 #26 骑士一直在家里来回徘徊，没有明确行动。
            #   骑士 26% 血回到家，身上挂着每墙钟秒掉约 2 血的持续伤害；「血低于 LOW_FRAC 就拉」不看有没有敌人 ——
            #   拉出来 -> 1.5 秒后「900 内没敌人」提前放手 -> 又掉 2 血 -> 再拉、再换一个撤退点：一分钟 32 次，在家里来回走。
            #   按掉血拉人，身边 PULL_THREAT_R 内得有敌人（玩家作战单位 / 野怪）或在敌方塔射程里；没有 = 没人可躲，交给养伤。
            if danger:
                threats_pull = [f for f in snap.units if f.hp > 0 and f.owner != snap.me and f.owner <= CREEP_OWNER
                                and (f.owner == CREEP_OWNER or foe_fighter_type(f.type))]
                towers_pull = enemy_towers(snap.units, snap.me)
                calm_pull = [t for t in danger
                             if not any(_dist(t[0], f) <= PULL_THREAT_R for f in threats_pull)
                             and not in_tower_range(t[0], towers_pull)]
                if calm_pull:
                    pull_no_threat += len(calm_pull)
                    danger = [t for t in danger if t not in calm_pull]
            if danger and not camp:
                # 09-21：预计 TTD_CRIT 游戏秒内就死的（lethal）不留 —— 家门口大仗不拉是怕拉走 69% 血的人、输出塌一半，
                #   留一个马上要死的人下来只多打一两下（「没人管」那 51 个战死里 48 个在家门口）
                kept_home = [t for t in danger
                             if t[0].addr not in lethal
                             and home_brawl(t[0], home, foes_live, halls, focus_last.get(t[0].addr, 0))]
                kept_heroes = sum(1 for t in kept_home if t[0].is_hero)
                if kept_heroes:
                    home_hero_kept += kept_heroes      # 0 = 家门口留英雄这条新路没生效
                if foes_live and any(t[0].is_hero and near_home(t[0].x, t[0].y, home, halls) for t in danger):
                    home_hero_pull += sum(1 for t in danger if t[0].is_hero and near_home(t[0].x, t[0].y, home, halls))
                if kept_home:
                    pull_home_kept += len(kept_home)
                    danger = [t for t in danger if t not in kept_home]
            if danger:
                # 快死的排前面，一轮最多拉 MAX_PULL 个 ——
                # 全撤等于没人顶着，反而死得更快。
                # 打玩家时还要看同一时刻已经撤下来几个（见 pull_picks）。
                pull_sel, pull_cap_n, pull_hero_n = pull_picks(danger, fighters, pulled, now, bool(camp), lethal)
                pull_capped += pull_cap_n
                pull_hero_kept += pull_hero_n
                for u, dps, left, secs_used, basis in pull_sel:
                    # ★★ **已经拉过的，在租约到期前不许再拉。**
                    #
                    # 这个文件开头就写着要防的那个 bug，实测又发生了，
                    # 而且规模大得多：一次防守战里 60 秒拉了 159 次，
                    # 同一个 Obla 被连着拉，血从 212 一路掉到 121：
                    #     22:50:16 Obla 212/550 正在挨 69 血/秒 —— 拉出来
                    #     22:50:18 Obla 177/550 正在挨 289 血/秒 —— 拉出来
                    #     22:50:19 Obla 121/550 正在挨 109 血/秒 —— 拉出来
                    # **每次重下命令都把上一次的寻路清零，它永远跑不掉**，
                    # 于是一边"正在被救"一边被打死。
                    #
                    # 租约本来是用来防这个的，可它防不住**我们自己**：
                    # claims.take 对一个已经属于自己的单位会直接成功
                    # （那是设计如此 —— 它区分的是模块，不是次数）。
                    # 所以要另有一个"这人已经在撤了"的判据，
                    # 而它现成就有：pulled 里记着每个人的租约到期时刻。
                    #
                    # 教训和"落点游标"那次一样：**防重复要按对象记，
                    # 而不是指望另一套机制顺便帮你挡住。**
                    if pulled.get(u.addr, 0.0) > now:
                        continue
                    if not claims.take(u.addr, LEASE, prio=PRIO["safety"]):
                        continue                   # 同级或更高优先级的正指挥它，不抢
                    # 背对威胁退：朝家的方向，退到脱离接触为止；身后有牧师就退到牧师身后（toward_healer）。
                    dx, dy = home[0] - u.x, home[1] - u.y
                    n = math.hypot(dx, dy) or 1.0
                    tx = u.x + dx / n * PULL_BACK
                    ty = u.y + dy / n * PULL_BACK
                    hp_at = toward_healer(u, dx / n, dy / n, fighters, PULL_BACK * 1.5)
                    if hp_at is not None:
                        tx, ty = hp_at
                        to_healer += 1
                    tx, ty, tw_fix = tower_safe_point(u, tx, ty, enemy_towers(snap.units, snap.me))
                    tower_fix += tw_fix
                    tc = time.monotonic()
                    ok_mv, r_path = retreat_move(u, tx, ty)
                    cmd_s = time.monotonic() - tc
                    if ok_mv:
                        rt_track(u, tx, ty, "拉残血·" + r_path)
                        saved += 1
                        pulled[u.addr] = now + LEASE
                        publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                        mw_d = mind_why.get(u.addr)
                        dlog("拉出撤退", u,
                             (f"{mw_d[0]} 个敌人在打它，按它们的伤害还能撑 {mw_d[1]:.1f} 游戏秒" if mw_d else
                              f"掉血 {int(dps)}/秒，{secs_used:.1f} 秒后只剩 {int(left)} 血") + ("（打野）" if camp else "（打玩家）"),
                             point=(tx, ty), cmd_s=cmd_s,
                             inputs={"血": f"{int(u.hp)}/{int(u.hp_max)}", "掉血每秒": int(dps), "外推秒数": round(secs_used, 1),
                                     "外推剩血": int(left), "在打它的敌人": mw_d[0] if mw_d else None,
                                     "按打它的人还能撑游戏秒": round(mw_d[1], 1) if mw_d else None,
                                     "致命·不受团队上限": u.addr in lethal, "场合": "打野" if camp else "打玩家",
                                     "退到牧师身后": hp_at is not None})
                        # ⚠ 打**地址**，不只是类型。
                        #   验"同一个单位有没有被反复拉"时我按类型去重，
                        #   于是六个不同的 ogru 被算成"同一个被拉六次"，
                        #   差点让我以为修复没生效（实际是 278 -> 12）。
                        #   一队里同型单位好几个，类型根本不是身份。
                        note(f"{u.type}@{u.addr:X} {int(u.hp)}/{int(u.hp_max)} 正在挨 "
                             f"{int(dps)} 血/秒（{'按打它的人算、游戏秒' if basis == 0.0 else f'{basis:.1f} 秒窗口'}），"
                             f"{secs_used:.1f} 秒后只剩 {int(left)} —— 拉出来（{'打野' if camp else '打玩家'}）"
                             f"（{int((time.monotonic()-now)*1000)}ms 内响应）")
                        # 09-21 退 = 进养伤状态：血回到 RETURN_FRAC 以上才归队（原来 8 秒租约一到，大脑就把 10% 血的人派回去）
                        if UM.can_rest(u, SUMMONS):
                            minds.mark_out(u, now, "打野" if camp else "打玩家")
                        mw = mind_why.pop(u.addr, None)
                        if mw is not None:
                            mind_ttd["按打它的人拉出"] += 1
                            if u.addr in lethal:
                                mind_ttd["致命·拉出"] += 1
                            note(f"  └ 处境：{mw[0]} 个敌人在打它，按它们的伤害还能撑 {mw[1]:.1f} 游戏秒"
                                 f"{'（致命：同时撤下人数上限 / 家门口大仗都不挡）' if u.addr in lethal else ''}")
                        # 09-21 团队规则·掩护：打它的人就是追它的人 —— 附近健康的队友去打他们（远程优先）
                        att = board.attackers(u, foe_fight, now) if not camp else []
                        if att:
                            busy = ({a for a, t in pulled.items() if t > now} | set(rotating) | set(dodged)
                                    | set(minds.out) | {a for a, t in cast_hold.items() if t > now} | sac)
                            for a, tgt in UM.cover_picks(u, att, fighters, busy, lambda t: RANGE.get(t, 100.0)):
                                if not claims.take(a.addr, 2.0, prio=PRIO["refill"]):
                                    cover_noclaim += 1
                                    continue
                                tc = time.monotonic()
                                ok_cv = ses.cmd.target_order(a.addr, tgt.addr, ORDER_ATTACK, tgt.x, tgt.y)
                                if ok_cv:
                                    cover_sent += 1
                                    dlog("掩护撤退", a, f"队友 {declog.unit_name(u.type)}#{_ids_cache['ids'].get(u.addr, '?')} 被拉出来，"
                                                      f"去打追它的人", target=tgt, cmd_s=time.monotonic() - tc,
                                         inputs={"被掩护的": f"{declog.unit_name(u.type)} {int(u.hp)}/{int(u.hp_max)}",
                                                 "在打它的敌人": len(att), "掩护者射程": RANGE.get(a.type, 100.0)})
                                else:
                                    claims.give_back(a.addr)
                    else:
                        claims.give_back(u.addr)

            # ★ 09-18 孤兵回撤（isolated / rally_point）：一两个兵被一群人围着打 -> 往自己人扎堆的地方退，没有就回家。
            #   实战发现只剩一两个小兵被一群敌军围攻时也不及时回家。不看血量（满血的孤兵一样被围死），不分家门口。
            if not camp and foes_live:
                foe_f = [f for f in foes_live if foe_fighter_type(f.type)]
                n_iso = 0
                for u in fighters:
                    if n_iso >= MAX_PULL:
                        break
                    if u.addr in sac or u.addr in rotating or pulled.get(u.addr, 0.0) > now:
                        continue
                    if not u.is_hero and u.hp >= 0.35 * u.hp_max and near_home(u.x, u.y, home, halls):
                        continue  # 健康守军不能再被孤兵规则拆走；英雄自保和残血拉扯独立运行。
                    if u.hp > .65*u.hp_max and any(a.addr != u.addr and a.hp > .5*a.hp_max
                            and pulled.get(a.addr,0) <= now and _dist(a,u) <= 1300 for a in fighters):
                        continue  # 健康先锋身后还有援军时，不按瞬时贴脸人数把它先拉走。
                    if not isolated(u, fighters, foe_f):
                        continue
                    iso_seen += 1
                    if not claims.take(u.addr, LEASE, prio=PRIO["safety"]):
                        iso_noclaim += 1
                        continue
                    rx, ry = rally_point(u, fighters, foe_f, home, pulled, now)
                    tc = time.monotonic()
                    ok_iso = retreat_move(u, rx, ry)[0]
                    if ok_iso:
                        n_foe = sum(1 for f in foe_f if f.hp > 0 and math.hypot(f.x - u.x, f.y - u.y) <= ISO_FOE_R)
                        n_own = sum(1 for m in fighters if m.addr != u.addr and m.hp > 0
                                    and math.hypot(m.x - u.x, m.y - u.y) <= ISO_FRIEND_R)
                        dlog("孤兵回撤", u, f"身边 {int(ISO_FOE_R)} 内 {n_foe} 个敌人、{int(ISO_FRIEND_R)} 内只有 {n_own} 个自己人，"
                                           f"退到自己人扎堆处（没有就回家），回到队伍里之前不派它去打",
                             point=(rx, ry), cmd_s=time.monotonic() - tc,
                             inputs={"血": f"{int(u.hp)}/{int(u.hp_max)}", "身边敌人": n_foe, "身边自己人": n_own,
                                     "判孤兵的局部战力比阈值": ISO_POWER})
                        n_iso += 1
                        iso_pulls += 1
                        pulled[u.addr] = now + LEASE
                        publish_hurt(pulled, now, ses.pid, snap.epoch, snap.mine)
                        # ★ 09-21 5-115 20:20 再次出现单个单位独自对抗多个敌军、残血也不撤退，属于反复出现的问题。
                        #   #49 骑士 22:46:27 孤兵回撤（766/835），5 秒后租约提前放手 -> 本模块补攻击令、大脑两次派回去 ->
                        #   22:46:36 再判孤兵 …… 22:46:51 死。锁只有 8 秒、提前放手按「900 内没敌人」—— 和残血那条同一个形状。
                        #   孤兵也进「脱离」状态：身边重新有自己人 / 回到家门口才归队（unitmind.Minds kind="iso"），大脑和补攻击令都不派。
                        if UM.can_rest(u, SUMMONS):
                            minds.mark_out(u, now, "孤兵", kind="iso")
                        note(f"孤兵回撤：{u.type}@{u.addr:X} {int(u.hp)}/{int(u.hp_max)} 身边 {int(ISO_FOE_R)} 内敌 >= {ISO_FOES}、"
                             f"{int(ISO_FRIEND_R)} 内自己人 <= {ISO_FRIENDS} —— 退到 ({int(rx)},{int(ry)})")
                    else:
                        claims.give_back(u.addr)

            if now - last_report > 60:
                last_report = now
                note(f"心跳：采样 {polls} 次，救下 {saved} 个，"
                     f"打野轮换 拉出 {rot_out}／送回 {rot_back}"
                     f"{('｜没换的理由 ' + '、'.join(f'{k}×{v}' for k, v in rot_skip.most_common(4))) if rot_skip else ''}，"
                     f"整队撤退 {squad_ret} 次，家门口不撤 {home_hold} 次、压倒劣势退到建筑后 {home_break} 次，"
                     f"胜率叫撤 {odds_ret} 次（家门口没撤 {odds_home}、算胜率 {odds.checks} 次、算失败 {odds.fails}、"
                     f"读不到游戏时钟 {odds_noclock} 轮{('、模型没加载：' + odds.error) if odds.error else ''}），"
                     f"水元素护卫 下令 {guard_sent}（抢不到指挥权 {guard_noclaim}），"
                     f"血量触发拉人 {low_pulls[0]} 人·轮，安全后提前放手 {early_release} 次，"
                     f"英雄预警撤退 {danger_pulls} 次（其中往大部队撤 {danger_harbor}、家门口也拉 {danger_home}、抢不到 {danger_noclaim}、"
                     f"守家血够不先跑 {danger_home_hold}），"
                     f"英雄被控 {dict(hero_ctl) or '无'}，兵种技能 {usp.heartbeat()}，"
                     f"远程转火敌方英雄 {hero_focus_sent} 次，"
                     f"打野仇恨 读 {ag['读']}（均 {1000 * aggro.read_s / max(1, ag['读']):.0f}ms）拉出 {ag['拉出']} 送回 {ag['送回']} "
                     f"送回后又被盯 {ag['送回后又被盯']} 被追再挪 {ag['被追再挪']} 血太低不回 {ag['血太低不回']}"
                     f"（其中回家养伤 {ag['残血回家养伤']}） 抢不到 {ag['抢不到']}，"
                     f"打野阵亡 {sum(creep_dead.values())}{('（' + '、'.join(f'{k}×{v}' for k, v in creep_dead.most_common()) + '）') if creep_dead else ''}，"
                     f"别打召唤物 读 {summon_reads} 在打召唤物 {summon_hit} 改打 {summon_switched}（没别的目标 {summon_none}、抢不到 {summon_noclaim}），"
                     f"孤兵回撤 {iso_pulls} 次（孤兵 {iso_seen} 人·轮、抢不到 {iso_noclaim}），丝血不受上限 {PULL_STATS['丝血不受上限']} 次，"
                     f"喂野怪名单 {len(sac)} 个，"
                     f"打玩家限拉 {pull_capped} 人·轮、英雄血过半不拉 {pull_hero_kept} 人·轮、"
                     f"身边没敌人不拉 {pull_no_threat} 人·轮、"
                     f"家门口大仗不拉 {pull_home_kept} 人·轮（英雄照拉候选 {home_hero_pull}、"
                     f"没被集火留下打 {home_hero_kept}），"
                     f"蓝不够没放 {sum(mana_low.values())} 人·轮"
                     f"{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(mana_low.items(), key=lambda kv: -kv[1])) + '）') if mana_low else ''}"
                     f"、读蓝失败 {mana_fail} 次，"
                     f"攻击令回执 抽查 {sum(atk_rcpt.values())}"
                     f"{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(atk_rcpt.items(), key=lambda kv: -kv[1])) + '）') if atk_rcpt else ''}，"
                     f"施法被退原因{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(reject_why.items(), key=lambda kv: -kv[1])) + '）') if reject_why else ' 无'}，"
                     f"没学没放 {sum(unlearned.values())} 人·轮{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(unlearned.items(), key=lambda kv: -kv[1])) + '）') if unlearned else ''}，"
                     f"施法保护挡下补攻击令 {cast_hold_blocked} 人·轮，"
                     f"拉出来退到牧师身后 {to_healer} 次，撤退点避开塔 {tower_fix} 次，"
                     f"撤退令回执 {sum(rt_rcpt.values())}"
                     f"{('（' + '、'.join(f'{a}·{b}×{v}' for (a, b), v in rt_rcpt.most_common()) + '）') if rt_rcpt else ''}"
                     f"{('，英雄没走时身上 ' + '、'.join(f'{k}×{v}' for k, v in rt_buffs.most_common())) if rt_buffs else ''}，"
                     f"单位处境 读敌 {board.reads} 次（没目标 {board.unread}、均 {(1000 * board.read_s / board.reads) if board.reads else 0:.0f}ms）"
                     f" 按打它的人判危险 {mind_ttd['进名单']} 人·轮（只靠这条进名单 {mind_ttd['只有按打它的人才进名单']}、"
                     f"致命 {mind_ttd['致命']}）拉出 {mind_ttd['按打它的人拉出']}（致命 {mind_ttd['致命·拉出']}），"
                     f"养伤 在册 {len(minds.out)}（进 {minds.counts['进养伤']}、回血归队 {minds.counts['回血归队']}、"
                     f"养伤中死亡或消失 {minds.counts['养伤中死亡或消失']}、孤兵归队 {minds.counts['孤兵归队']}），"
                     f"掩护撤退 {cover_sent}（抢不到 {cover_noclaim}），"
                     f"牧师定向治疗 {dict(priest_tally) if priest_tally else '无'}，"
                     f"物品 {dict(item_used) if item_used else '无'}，"
                     f"撤退回城 {dict(tp_retreats) if tp_retreats else '无'}，"
                     f"远程拉扯 {dict(kite_tally) if kite_tally else '无'}，"
                     # ⚠ 条件表达式不能写在隐式拼接的相邻串之间：`f"a" f"b" if c else "d"` 会把
                     #   前面所有串一起纳入条件（和「逗号比乘号结合更紧」是同一类优先级坑）。
                     #   要判空就判在 {} 里面。
                     f"英雄集火 读 {focus_reads} 轮（在打它的人数 {dict(focus_peak)}）>={FOCUS_PULL_N} 的 {focus_hot} 轮、拉 {focus_pulls}、"
                     f"读不到目标 {focus_unread}/{focus_rows}"
                     f"（{(100.0 * focus_unread / focus_rows) if focus_rows else 0.0:.0f}%）、"
                     f"外推救下 {focus_extrap}、抢不到指挥权 {focus_noclaim}，"
                     f"打野补攻击令 {creep_refill_sent}（施法保护 {creep_refill_held}、抢不到 {creep_refill_noclaim}），"
                     f"补攻击令 跟打 {focus_join[True]}／新目标 {focus_join[False]}，"
                     f"引擎冷却中没下令 {sum(engine_cd.values())} 次"
                     f"{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(engine_cd.items(), key=lambda kv: -kv[1])) + '）') if engine_cd else ''}，"
                     f"施法回执{('（' + '、'.join(f'{k}×{v}' for k, v in sorted(cast_rcpt.items(), key=lambda kv: -kv[1])) + '）') if cast_rcpt else ' 无'}，"
                     f"躲暴风雪 {bliz_waves} 波 {bliz_moved} 人（在放但没成片 {bliz_nocluster} 轮、抢不到指挥权 {bliz_noclaim}），"
                     f"自己放烈焰风暴 {ff_casts} 次、让圈里自己人躲开 {ff_moved} 人、路过火圈绕开 {ff_fenced} 人（抢不到指挥权 {ff_noclaim}），"
                     f"英雄施法 {sum(casts.values())} 次"
                     f"{('（' + '、'.join(f'{k}×{v}' for k, v in casts.most_common()) + '）') if casts else ''}"
                     f" 被退 {sum(cast_rejects.values())}"
                     f"{('（' + '、'.join(f'{k}×{v}' for k, v in cast_rejects.most_common()) + '）') if cast_rejects else ''}"
                     f"（快照延迟 {snap.age_ms}ms）"
                     f"｜{skill_stats(casts, cast_landed, cast_rejects, unlearned, mana_low)}"
                     f"｜施法后续 {('、'.join(f'{k}×{v}' for k, v in follow_done.most_common())) if follow_done else '无'}")
                rot_out = rot_back = squad_ret = home_hold = home_break = pull_capped = pull_hero_kept = pull_home_kept = home_hero_pull = home_hero_kept = 0
                pull_no_threat = 0
                odds_ret = odds_home = odds_noclock = 0
                odds.checks = odds.fails = 0
                guard_sent = guard_noclaim = 0
                danger_pulls = danger_home = danger_noclaim = danger_harbor = danger_home_hold = 0
                hero_ctl.clear()
                usp.reset_counts()
                low_pulls[0] = 0
                early_release = 0
                hero_focus_sent = 0
                summon_reads = summon_hit = summon_switched = summon_none = summon_noclaim = 0
                ag.clear()
                aggro.read_s = 0.0
                creep_dead.clear()
                iso_seen = iso_pulls = iso_noclaim = 0
                PULL_STATS["丝血不受上限"] = 0
                board.reads = board.unread = 0
                board.read_s = 0.0
                mind_ttd.clear()
                minds.counts = {k: 0 for k in minds.counts}
                cover_sent = cover_noclaim = 0
                priest_tally.clear()
                focus_reads = focus_hot = focus_pulls = focus_noclaim = to_healer = tower_fix = 0
                focus_unread = focus_rows = focus_extrap = 0
                creep_refill_sent = creep_refill_held = creep_refill_noclaim = 0
                item_used.clear()
                tp_retreats.clear()
                kite_tally.clear()
                focus_peak.clear()
                mana_low.clear()
                mana_fail = 0
                atk_rcpt.clear()
                rt_rcpt.clear()
                rt_buffs.clear()
                focus_join[True] = focus_join[False] = 0
                reject_why.clear()
                cast_rcpt.clear()
                unlearned.clear()
                cast_hold_blocked = 0
                engine_cd.clear()
                for a in [a for a, until in cast_hold.items() if until <= now]:
                    cast_hold.pop(a, None)
                bliz_waves = bliz_moved = bliz_nocluster = bliz_noclaim = 0
                ff_casts = ff_moved = ff_noclaim = ff_fenced = 0
                casts.clear()
                cast_rejects.clear()
                cast_landed.clear()
                follow_done.clear()
                rot_skip.clear()   # 理由也按分钟统计，和上面几个数同一个口径
                polls = 0
            time.sleep(TICK)
        except KeyboardInterrupt:
            publish_hurt({}, time.monotonic(), ses.pid)
            publish_solo({}, time.monotonic(), ses.pid)
            note("模块退出")
            return 0
        except Exception as exc:                   # noqa: BLE001
            # 独立进程死了没人知道，所以这个循环绝不能死。
            note(f"出错（继续跑）：{exc!r}")
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())

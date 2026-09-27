# -*- coding: utf-8 -*-
"""校准过的战力判断：把「战力比」换算成**实测对得上**的胜率和预计损失份额。

## 为什么有这一层

出击/撤退/打野/守家至少 12 个阈值原来是手调的比值（`R >= 2.5`、`× 1.25`、`× 0.55`……），
没有一个对照过实战结果。`tools/calibrate_power.py` 用 964 个战斗帧文件、约 6000 场仗
拟合了两条曲线，于是那些数现在是**从三条能解释的线推出来的**：

    P_COMMIT    发起一场新仗要几成把握
    P_GIVEUP    跌破这条就撤
    LOSS_CHEAP  「必须便宜」的活（打野、清分矿）预计我方损失份额的上限

## 曲线长什么样

    p_win(R) = lo + (hi - lo) · sigmoid(a · lnR + b)
    loss(R)  = 同形（a 为负，战力比越高损失越低）

`lo`/`hi` 是**地板和天花板**，不是摆设 —— 实测出来的两条硬事实：

  * 打玩家的仗，胜率在约 **44%** 封顶。战力比从 5 涨到 30，胜率、损失份额都不再改善。
    （把英雄整个剔掉重算，曲线形状不变 —— 不是「英雄按 4 倍钱算」造成的假象。）
  * 同样的仗，我方损失份额在约 **57%** 触底。也就是说**单位交换永远是亏的**：
    964 个文件里按战力比分十档，每一档的「敌方损失金 - 我方损失金」都是负的，
    最好的一档（战力比 7~12）也只是持平。
    -> 出击的价值只能来自拆掉对面的建筑和经济，不能指望换兵换赢。这条要记住。
  * 打野正相反：**每一档都赚钱**（净 +106 ~ +885 金），胜率地板就有 75%。
    所以打野的门槛该按「损失份额」定，不该按「打不打得过」定。

一条没有天花板的 logistic 拟合不出饱和，只能靠压平斜率，会外推出
「五成把握 = 战力比 15」这种假结论（第一版就是这么错的）。

## 两个估计器

大脑里其实有两套战力口径，各管一半阈值，**都**校准了：

  * `power` = `tactics.power_ratio`（总血 × 总每秒伤害）—— 打野那一组阈值比的是它
  * `amai`  = `amai.strength` 之比（(人口+等级)/2 × 血量比例）—— 出击/撤退/支援/拆分矿比的是它

检验集 AUC 两者几乎一样（0.710 / 0.706），也就是说它们携带的信息量相同，
差别只在刻度。谁在哪里用就校准谁，不要拿一个的系数去套另一个。

## 重新拟合

    python tools/calibrate_power.py --write     # 重新拟合并写回 brain/data/powercal.json
    python tools/calibrate_power.py --derive    # 打印三条线推出来的那些阈值

⚠ 系数只在**导入时**读一次。改了 json 要等下一局（和大脑其它代码一样）。
⚠ 下面这些 `R_*` 是给 tactics / config 用的唯一来源。不要在别处另写一份数
   —— 同一条规则写在两处、只改一处等于没改。
"""
from __future__ import annotations

import json
import math
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "powercal.json")

# ---------------------------------------------------------------- 三条线

P_COMMIT = 0.41      # 发起一场新仗（打对面主基地）：校准后的胜率至少这么多。
                     #   天花板 44%，所以 0.41 已经贴着「能要到的最高把握」；再往上等于永远不出击，
                     #   而出击的价值本来也不在换兵（见下），在拆掉对面的建筑和经济。
                     #   这个数是**反推**出来的：它让门槛落回 086/088/089 那批失败局定下的那条线
                     #   （守军强度 × 1.73），于是这次改动只换说法、不换行为 —— 真正按证据放宽的是打野那一组。
P_EXPANSION = 0.365  # 打分矿放宽这么点：守军少、失败也跑得掉，而分矿直接关系到对面的经济。
P_GIVEUP = 0.11      # 跌破这条就撤。地板是 9%，11% 等于「已经基本没戏」。
LOSS_CHEAP = 0.27    # 打野 / 清分矿野怪：预计我方损失份额不超过这个才开打。
LOSS_HOLD = 0.30     # 已经开打了放宽到这个（迟滞下沿，防止在门槛上来回抖）。
CEIL_MARGIN = 0.02   # 「撤」这条线贴着损失份额的**天花板**定：那一段曲线是平的，
                     #   再往上没有分辨率，写死一个 0.33 会解出一个荒唐的比值（实测 0.19）。
                     #   打野每一档都赚钱（净 +106 ~ +885 金），所以撤退线本来就该很低，
                     #   真正管「打不动」的是 v22 那条按**进展**判的闸，不是比值。

# ---------------------------------------------------------------- 曲线

# 兜底系数：`brain/data/powercal.json` 读不到时用这一份（964 个文件 / 5981 场拟合出来的同一组数）。
# 有兜底是因为**读不到数据不能让大脑起不来**；正常路径永远走 json。
# ⚠ 改了 json 就要把这一份抄过来（`--write` 会把该贴的块打出来）。
#   `tests/test_winprob.py` 守着这件事 —— 兜底不是第二份真相，它是同一份数的副本。
FALLBACK = {
    "foe.power": {"win": [2.14461, -0.34225, 0.08676, 0.44695],
                  "loss": [-4.74599, -0.17781, 0.57475, 0.79873]},
    "foe.amai": {"win": [5.03503, -0.25398, 0.08587, 0.43637],
                 "loss": [-8.27801, -0.16522, 0.57863, 0.79752]},
    "creep.power": {"win": [2.68969, -0.30196, 0.74904, 0.91917],
                    "loss": [-2.35873, 1.86895, 0.17307, 0.33047]},
    "creep.amai": {"win": [6.36636, -1.03246, 0.76479, 0.93215],
                   "loss": [-2.94321, 0.99148, 0.16422, 0.33915]},
}

R_LO, R_HI = 0.02, 50.0     # 和校准时的钳位一致；超出这个范围的比值没有样本支撑
_FITS: dict = {}
_META: dict = {}


def reload() -> dict:
    """重读系数。导入时自动跑一次；`tools/calibrate_power.py --write` 之后手动调它才看得到新值。"""
    global _FITS, _META
    fits = {}
    meta = {}
    try:
        with open(DATA, encoding="utf-8") as fh:
            raw = json.load(fh)
        meta = {k: v for k, v in raw.items() if k != "fits"}
        for key, f in (raw.get("fits") or {}).items():
            if isinstance(f.get("win"), list) and len(f["win"]) == 4:
                fits[key] = {"win": [float(v) for v in f["win"]],
                             "loss": [float(v) for v in f["loss"]],
                             "n": f.get("n"), "auc": f.get("auc_test")}
    except (OSError, ValueError, KeyError, TypeError):
        fits = {}
    for key, f in FALLBACK.items():
        fits.setdefault(key, dict(f))
    _FITS, _META = fits, meta
    return fits


def meta() -> dict:
    """拟合用了多少文件 / 多少场，写进对局记录用。"""
    return dict(_META)


def _fit(kind: str, est: str) -> dict:
    return _FITS.get("%s.%s" % (kind, est)) or FALLBACK["%s.%s" % (kind, est)]


def _curve(theta, ratio: float) -> float:
    a, b, lo, hi = theta
    x = math.log(max(R_LO, min(R_HI, max(1e-9, ratio))))
    z = a * x + b
    z = 30.0 if z > 30 else (-30.0 if z < -30 else z)
    return lo + (hi - lo) / (1.0 + math.exp(-z))


def win_prob(ratio: float, kind: str = "foe", est: str = "amai") -> float:
    """这个战力比对应的**校准过的**胜率。「赢」= 这一仗我方阵亡的人头份额低于对面。"""
    return _curve(_fit(kind, est)["win"], ratio)


def loss_share(ratio: float, kind: str = "foe", est: str = "amai") -> float:
    """预计我方会死掉参战部队的多大份额。"""
    return _curve(_fit(kind, est)["loss"], ratio)


def _invert(theta, target: float):
    """要达到 target，比值至少要多少。达不到返回 None，任何比值都满足返回 R_LO。

    对胜率（a > 0）是「至少要这么大」；对损失份额（a < 0）是「至少要这么大才降到这条线以下」——
    两种情况解出来是同一个式子，只有「够不到 / 白给」这两个边界要按 a 的符号翻过来。"""
    a, b, lo, hi = theta
    if hi <= lo or a == 0:
        return None
    frac = (target - lo) / (hi - lo)
    unreachable = (frac >= 1.0) if a > 0 else (frac <= 0.0)
    trivial = (frac <= 0.0) if a > 0 else (frac >= 1.0)
    if unreachable:
        return None
    if trivial:
        return R_LO
    return max(R_LO, min(R_HI, math.exp((math.log(frac / (1.0 - frac)) - b) / a)))


def ratio_for_win(p: float, kind: str = "foe", est: str = "amai", fallback: float = R_HI) -> float:
    """要有 p 的把握，战力比至少要多少。天花板够不到时返回 fallback（默认 = 实际上永不成立）。"""
    r = _invert(_fit(kind, est)["win"], p)
    return fallback if r is None else r


def ratio_for_loss(share: float, kind: str = "creep", est: str = "power", fallback: float = R_HI) -> float:
    """要把预计损失份额压到 share 以下，战力比至少要多少。"""
    r = _invert(_fit(kind, est)["loss"], share)
    return fallback if r is None else r


def loss_ceiling(kind: str = "creep", est: str = "power") -> float:
    """损失份额最差能到多少（曲线的天花板）。比这个大的目标解不出有意义的比值。"""
    a, _b, lo, hi = _fit(kind, est)["loss"]
    return hi if a < 0 else lo


def loss_floor(kind: str = "creep", est: str = "power") -> float:
    """损失份额最好能到多少（地板）。打玩家的仗这个数是 0.57 —— 交换永远是亏的。"""
    a, _b, lo, hi = _fit(kind, est)["loss"]
    return lo if a < 0 else hi


def win_ceiling(kind: str = "foe", est: str = "amai") -> float:
    """胜率的天花板。打玩家的仗是 0.44：战力比再大也没用，别设一条到不了的线。"""
    a, _b, lo, hi = _fit(kind, est)["win"]
    return hi if a > 0 else lo


def win_floor(kind: str = "foe", est: str = "amai") -> float:
    """胜率的地板。打玩家的仗是 0.09：比这更低的门槛没有分辨率，撤退线只能定在它上面一点。"""
    a, _b, lo, hi = _fit(kind, est)["win"]
    return lo if a > 0 else hi


def kill_times(ours, theirs, levels: dict | None = None) -> tuple[float, float]:
    """(我们打光他们要几秒, 他们打光我们要几秒)。

    设计上要的判据：「对面打光我们要几秒 vs 我们打光他们要几秒」。
    这两个数就是 `tactics.power_ratio` 的分子分母拆开 —— 比值相同、但**绝对秒数**多一层信息：
    同样 2 倍优势，30 秒打完和 6 秒打完，能不能等到援军是两回事。
    ⚠ 放在这里而不是 tactics 里，是为了不和 power_ratio 分叉：两边都调 tactics.power()。"""
    from . import tactics as T
    ho, do = T.power(ours, levels)
    hc, dc = T.power(theirs, levels)
    return (hc / do if do > 0 else float("inf")), (ho / dc if dc > 0 else float("inf"))


# ---------------------------------------------------------------- 残血怕不怕
#
# 实测（140 个战斗帧文件、13852 个「单位 × 场」，按**单个单位**算，不是按整队）：
#
#   进场血比      死亡率   战力比 <1   战力比 >3
#   0.90~1.00      57%       62%        47%
#   0.35~0.50      62%       73%        44%
#   0.20~0.35      75%       87%        66%
#   0.00~0.20      84%       95%         -
#
# **一个 40% 血的兵在打得过的仗里（44%）比一个满血的兵在打不过的仗里（62%）还安全。**
# 从满血掉到 40% 只加 5 个点风险，而这一仗打不打得过能拉开 15~29 个点。
# 低于 35% 才开始陡增（+18 / +27 个点）——所以下面既要「按仗判」，也要留一条绝对地板。
#
# ⚠ 不要把这条读成「残血的人一样安全」。整队血量完整度对**胜负**没有预测力（单变量 AUC 0.517），
#   那说的是「这一仗赢不赢」；单个兵会不会死是另一件事，上表就是专门为它量的。

SOFT_HP = 0.35         # 这条线以上：残血只比满血多 5 个点风险（62% vs 57%）—— 一律留下
KEEP_FLOOR_HP = 0.20   # 这条线以下：死亡率 84%~95%，而且回血很慢 —— 一律摘
                       # 中间那一档（20%~35%）按**这一仗打不打得过**：打得过 66%、打不过 87%
PEEL_MAX = 0.34        # 一次最多摘走这么多人。超过说明不是「个别人残血」，是整队该撤了 ——
                       # 一个个摘走正是添油的来源：账上 10 个人在打，真正在打的只有 7 个


def unit_survival_s(u, ours, theirs, levels: dict | None = None) -> float:
    """这个单位大约还能撑几秒 = 它的血 ÷ (敌方总输出平摊到我方还活着的人)。

    只用来写日志和给人看（「对面打光我们要 N 秒」里的每个人那一份），**不当闸门**：
    ⚠ 它和「我们打光他们要几秒」一比就退化成「血量要高于全军均值 ÷ 战力比」，
      势均力敌时会摘掉一半人 —— 第一版就是这么写的，把「残血不怕」做成了「怕得更厉害」
      （test_flee_local 当场抓到：8 个满血步兵对 14 个食尸鬼，全队被判该撤）。
      闸门用 should_keep，那是直接照实测表分档的。"""
    from . import tactics as T
    alive = [x for x in ours if x.hp > 0]
    _hf, df = T.power(theirs, levels)
    if df <= 0 or not alive:
        return float("inf")
    per = df / len(alive)
    return u.hp / per if per > 0 else float("inf")


def should_keep(u, ratio: float, kind: str = "foe") -> bool:
    """这个兵该不该留在这一仗里。三档，每一档都能在上面那张表里指出来：

      血 >= 35%   留。比满血只多 5 个点风险，摘走它反而是添油。
      血 <  20%   摘。84%~95% 会死，留下等于送。
      中间        看**这一仗打不打得过** —— 同样 20~35% 血，打得过的仗 66%、打不过的 87%。
                  用 P_GIVEUP 当线：低于它整队本来就该撤（FLEE_LOCAL_RATIO 用的是同一条线），
                  不另立第四个数。"""
    frac = (u.hp / u.hp_max) if getattr(u, "hp_max", 0) > 0 else 1.0
    if frac >= SOFT_HP:
        return True
    if frac < KEEP_FLOOR_HP:
        return False
    return win_prob(ratio, kind, "power") >= P_GIVEUP


def hurt_to_peel(army, theirs, levels: dict | None = None, hero_ids=frozenset(), kind: str = "foe"):
    """(该摘出去的人, 是不是该整队撤)。

    规则两句话：
      * 按 should_keep 摘（照实测的死亡率表，不是拍一个血线）；
      * 但一次摘走超过 PEEL_MAX 的人，就不是「个别人残血」了 —— 整队撤，别一个个添油上去。
    召唤物和英雄不走这条路（英雄有 heroplan，召唤物本来就是拿来扛的）。
    没有敌人在场时**只查绝对地板**：不打仗的时候血量确实不是问题，但 10% 血的兵也不该跟着出门。"""
    from . import tactics as T
    pool = [u for u in army if u.addr not in hero_ids and not T.is_summon(u.type) and u.hp > 0]
    if not pool:
        return [], False
    if not theirs:
        low = [u for u in pool if u.hp_max > 0 and u.hp < KEEP_FLOOR_HP * u.hp_max]
        return low, False
    ratio = T.power_ratio(army, theirs, levels)
    low = [u for u in pool if not should_keep(u, ratio, kind)]
    # ★ 09-21 （5-109 7 分钟）野怪只剩一丝血时全队撤退，大法师几乎满血也跟着跑了。
    #   原句「打野整队退：2/7 人撑不住（我们打光营地要 4 秒，营地打光我们要 25 秒）」，之后一路到「要 0 秒」还在退，
    #   死了两个步兵、营地没清。整队撤只数了残血的人头，没问**这一仗打不打得过** ——
    #   打得过就只把残血的摘下去、其余人收掉营地。线用校准过的同一条曲线：
    #   打野 = 「已经在打、别撤」R_CREEP_HOLD；打玩家 = 「当初会不会下决心打」P_COMMIT（打玩家那条曲线很悲观，约 3.2，
    #   所以打玩家的整队撤几乎不变，只挡掉碾压还撤这种）。
    hold = R_CREEP_HOLD if kind == "creep" else ratio_for_win(P_COMMIT, "foe", "power")
    return low, len(low) > PEEL_MAX * len(pool) and ratio < hold


# ---------------------------------------------------------------- 推出来的阈值
#
# 下面这些是 tactics.py 和 config.py **唯一**的来源。两边都按名字引用，
# 不再各写一个字面量 —— 那正是 v20 查出来的「四个阈值从来没生效过」那个形状。

reload()

R_ATTACK_MAIN = ratio_for_win(P_COMMIT, "foe", "amai")          # 打主基地要的强度比
R_ATTACK_EXPANSION = ratio_for_win(P_EXPANSION, "foe", "amai")  # 打分矿要的强度比
R_GIVEUP = ratio_for_win(P_GIVEUP, "foe", "amai")               # 跌破这条撤
R_CREEP = ratio_for_loss(LOSS_CHEAP, "creep", "power")          # 打野开打
R_CREEP_HOLD = ratio_for_loss(LOSS_HOLD, "creep", "power")      # 已经在打，别撤
# 「轻松营地」= 预计损失份额落在**地板和普通门槛的正中间**。不是又一个手调数：
# 地板是这条曲线能给出的最好结果，正中间就是「明显比普通营地便宜」的自然定义。
LOSS_EASY = loss_floor("creep", "power") + (LOSS_CHEAP - loss_floor("creep", "power")) / 2
R_CREEP_EASY = ratio_for_loss(LOSS_EASY, "creep", "power")
LOSS_ABORT = loss_ceiling("creep", "power") - CEIL_MARGIN       # 贴着天花板，见 CEIL_MARGIN
R_CREEP_ABORT = ratio_for_loss(LOSS_ABORT, "creep", "power")    # 真的该走了

# 出击那条路上，`offense.assess` 先乘一个基础 margin，调用方再乘一个 target_reserve：
#   实际要求 ≈ defenders × ATTACK_MARGIN × target_reserve
# 所以基础 margin 取「分矿」那一档，主基地那一档写成两者的**商**，两边乘起来正好是推出来的值。
ATTACK_MAIN_MULT = R_ATTACK_MAIN / R_ATTACK_EXPANSION
# 集结复核的迟滞写成**乘性**的，不写成「放宽几个点把握」：胜率在 44% 封顶，曲线在门槛那一带已经很平，
# 差 3 个点的把握 = 差 20% 的兵力比，而 086/088/089 的证据要求复核只允许**小幅**波动（旧值 1.30 对 1.35）。
# 这是「能用条件就别用时长」的同一类判断：在曲线平的地方，用概率表达差异是病态的。
GATHER_HYST = 0.96
# ⚠ 复核乘数不许低于 1：`assess` 里的 `ev.good`（own >= need）已经是一道等价于基础 margin 的闸，
#   commit_reserve 再小也压不过它。算出来 < 1 只说明「分矿这一档的迟滞被 ev.good 吃掉了」，
#   写 0.96 会让人以为放宽了，其实一点没放宽。
GATHER_MAIN_MULT = max(1.0, ATTACK_MAIN_MULT * GATHER_HYST)
GATHER_EXPANSION_MULT = max(1.0, GATHER_HYST)


def derived_thresholds() -> list[tuple[str, float, str]]:
    """(名字, 值, 它是从哪条线来的)。`tools/calibrate_power.py --derive` 打的就是这张表，
    `tests/test_winprob.py` 拿它和 tactics 里的实际值对账。"""
    return [
        ("tactics.ATTACK_MARGIN", R_ATTACK_EXPANSION, "打分矿 %.0f%% 把握" % (100 * P_EXPANSION)),
        ("tactics.ATTACK_MAIN_RESERVE", ATTACK_MAIN_MULT, "叠到 %.0f%% 把握（打主基地）" % (100 * P_COMMIT)),
        ("tactics.ATTACK_EXPANSION_RESERVE", 1.0, "分矿不再叠，基础 margin 已经是它"),
        ("tactics.ATTACK_LAUNCH_RESERVE", 1.0, "发起时不额外加，目标那一档说了算"),
        ("tactics.GATHER_MAIN_COMMIT_RESERVE", GATHER_MAIN_MULT,
         "主基地复核，比发起时松 %.0f%%" % (100 * (1 - GATHER_HYST))),
        ("tactics.GATHER_EXPANSION_COMMIT_RESERVE", GATHER_EXPANSION_MULT,
         "分矿复核：迟滞被 assess 的 ev.good 吃掉，实际就是基础 margin"),
        ("tactics.RAID_MARGIN", R_ATTACK_MAIN, "拆家门口分矿 = 打主基地那一档（当地守军算得准，不打折）"),
        ("tactics.FLEE_LOCAL_RATIO", R_GIVEUP, "跌破 %.0f%% 把握撤" % (100 * P_GIVEUP)),
        ("tactics.SUPPORT_MIN_RATIO", R_GIVEUP, "同上：合起来也没到 %.0f%% 就不支援" % (100 * P_GIVEUP)),
        ("tactics.CREEP_POWER_MIN", R_CREEP, "打野预计损失 <= %.0f%%" % (100 * LOSS_CHEAP)),
        ("tactics.CREEP_ENGAGE_POWER", R_CREEP, "同上（到场开打）"),
        ("tactics.CLEAR_POWER", R_CREEP, "同上（清分矿野怪）"),
        ("tactics.CREEP_ENGAGE_POWER_LATE", R_CREEP_HOLD, "等久了放宽到损失 <= %.0f%%" % (100 * LOSS_HOLD)),
        ("tactics.CREEP_EASY_ENGAGE", R_CREEP_HOLD, "同上（先到的人不吃亏）"),
        ("tactics.CREEP_DIRECT_POWER", R_CREEP_EASY,
         "轻松营地：损失 <= %.0f%%（地板 %.0f%% 和门槛的正中间）" % (100 * LOSS_EASY, 100 * loss_floor())),
        ("tactics.CREEP_REINFORCE_ENGAGE", R_CREEP_ABORT, "到场前排至少不到该撤那条线"),
        ("tactics.CREEP_ABORT_POWER", R_CREEP_ABORT,
         "打野中撤：预计损失超过 %.0f%%（贴着天花板 %.0f%%）" % (100 * LOSS_ABORT, 100 * loss_ceiling())),
    ]

"""battles.py —— 交战账本：把 mod_fight 录的原始帧切成一场一场的仗。

    python battles.py                 # 最近 3 局
    python battles.py --n 6
    python battles.py --match match-20260915-183429

09-15 排查：对局日志是否不够全面，以致无法据此了解战局、做优化迭代。

查下来不是记得少，是**记偏了**：经济、生产、闸门记得很细，而决定胜负的那场仗
只有"被抄家：阵亡 12 个"这种一行聚合数 —— 没有兵种、没有敌方战损、不知道推演说对没说对。
v251 三局形状一模一样：整局没出过门，敌方第 96~100 拍打进来，3~4 拍把我们打光，第 104 拍认输。

而 150ms 一帧、敌我全部单位的原始帧早就在 build_intel4/fights/ 里（mod_fight 在录），
**除了 mod_fight --report 没有任何工具读它**，而那个报表不分场次、不算按兵种的战损、
也对不上对局的拍号。这个脚本补的就是这一段：纯离线，不碰正在跑的游戏。

口径（写死在这里，不靠记得）：
  * 作战单位 = 不是工人、不是建筑（units.json foot>0，和 mod_fight 同一张表）
  * 敌方 = 敌方**玩家**（0 <= owner < 12 且不是我）；野怪另算，不进这张账
  * 接触 = 我方作战单位离某个敌方作战单位 <= 900（和 mod_fight / mod_combat 同一个距离）
  * 一场 = 连续的接触帧；中断超过 GAP 秒算下一场
  * 战损 = 这一场的参与者，在"结束后 TAIL 秒"那一帧里血量 <= 0 或已经不在帧里
    ⚠ 帧里是**全图**的作战单位，所以"不在了"基本等于死了；进了建筑/运输的会被误算（人族极少见）
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()

from mod_fight import BUILDINGS, CONTACT, OUTDIR, UNITS_JSON, WORKERS  # noqa: E402

MATCHES = os.environ.get(
    "WAR3_MATCH_DIR", os.path.join(w3paths.BIN, "matches"))
ATTACK = 0x000D000F
# ⚠ 09-15：第一版把 0x000D0004 也算"移动"，而 order-ids.txt 里它是 **stop**。只留 smart（右键）。
SMART = 0x000D0003
EMPTY = (-1, 0xFFFFFFFF)   # 任务句柄对为空
GAP = 8.0            # 秒：接触中断超过这么久算下一场
TAIL = 5.0           # 秒：一场结束后再看这么久才结算战损（死亡动画、追击）
BASE_R = 1800.0      # 离大厅这么近算"在基地里"
HALLS = {"htow", "hkee", "hcas", "ogre", "ostr", "ofrt",
         "unpl", "unp1", "unp2", "etol", "etoa", "etoe"}

# 帧里每个单位：[addr, type, owner, x, y, hp, order, tlo, thi]
A, TYP, OWN, X, Y, HP, ORD = range(7)


# units.json 里 foot=0 却不打仗的（飞艇、治疗守卫、凤凰蛋）。
#   ⚠ 09-15 更正：第一版把 hwt2/hwt3 当成"升级后的塔"放在这里 —— units.json 里它们是
#     **2 级、3 级水元素**（waterelemental2/3，675/900 血）；塔是 hgtw/hatw/hctw/hwtw，foot=128，本来就进 BUILDINGS。
#     于是敌方 Archmage 召出来的水元素整场不进交战账本（211239 局的帧里 hwt2 533 次、hwt3 399 次）。
#   hwat/hwt2/hwt3（水元素）、hphx（凤凰）foot=0 且不占人口，但它们是作战单位。
NONFIGHTERS = {"nzep", "ohwd", "hpxe"}


def _summons():
    """召唤物：units.json 里不占人口、不花钱、不占地、不是英雄的（水元素、狼、骷髅、树人……）。

    09-16 账本行「接触峰值 我 14:敌 15 战损 19:9」里的战损 19 对不上峰值，查下来：
    战损数的是整场进过接触的所有人（那场 20 个，分批到场、边到边死），其中 2 个是大法师召的水元素 ——
    账本行里原来既没有参战人数、也分不出召唤物，只有一个"同一时刻最多几个人"的峰值摆在旁边。
    """
    try:
        with open(UNITS_JSON, encoding="utf-8-sig") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return set()
    return {k for k, v in d.items() if isinstance(v, dict) and not is_hero(k)
            and not (v.get("food") or 0) and not (v.get("gold") or 0) and not (v.get("foot") or 0)}


def is_fighter(r) -> bool:
    return (r[HP] > 0 and r[TYP] not in WORKERS and r[TYP] not in BUILDINGS
            and r[TYP] not in NONFIGHTERS)


def is_hero(typ: str) -> bool:
    return bool(typ) and typ[0].isupper()


SUMMONS = None   # 第一次用到时再读表（is_hero 定义在上面，模块加载顺序无关）


def summons():
    global SUMMONS
    if SUMMONS is None:
        SUMMONS = _summons()
    return SUMMONS


CREEP_OWNER = 12    # 中立敌对 = 野怪（实测帧里 owner 12 是 nmrr/nkob…，15 是商店/金矿/小动物）


def foe_player(r, me) -> bool:
    """默认的「算不算敌方」：别的**玩家**（0~11），不含野怪（12）和中立被动（15）。"""
    return 0 <= r[OWN] < 12 and r[OWN] != me


def foe_creep(r, me) -> bool:
    """野怪（中立敌对 12）。打野的仗要用它才切得出来 —— 默认那一份把 12 排除在外。"""
    return r[OWN] == CREEP_OWNER


def contact_sets(units, me, foe=None):
    """这一帧里谁在接触：返回 (我方接触地址集, 敌方接触地址集)。

    foe: 可选谓词 (行, me) -> bool，决定谁算敌方。默认 foe_player（只算敌方玩家，和账本历来的口径一致）。
    传 foe_creep 就能把**打野**的仗按同一套逻辑切出来 —— 打野占了 tactics 里一半的战力阈值，
    而它们从来没进过这条账本。"""
    foe = foe or foe_player
    mine = [r for r in units if r[OWN] == me and is_fighter(r)]
    foes = [r for r in units if foe(r, me) and is_fighter(r)]
    cm, cf = set(), set()
    for a in mine:
        for b in foes:
            if abs(a[X] - b[X]) + abs(a[Y] - b[Y]) > CONTACT * 2:
                continue
            if math.hypot(a[X] - b[X], a[Y] - b[Y]) <= CONTACT:
                cm.add(a[A])
                cf.add(b[A])
    return cm, cf


def segment(frames, me, foe=None):
    """把帧切成场次。frames: [(秒, units)]，按时间排好。返回 [(起始下标, 结束下标)]（含）。"""
    out = []
    start = last = None
    for i, (t, units) in enumerate(frames):
        cm, _ = contact_sets(units, me, foe)
        if not cm:
            continue
        if start is None:
            start = last = i
        elif t - frames[last][0] > GAP:
            out.append((start, last))
            start = i
        last = i
    if start is not None:
        out.append((start, last))
    return out


def summarize(frames, lo, hi, me, foe=None):
    """一场仗的账。"""
    t0, t1 = frames[lo][0], frames[hi][0]
    part_m, part_f = set(), set()
    peak_m = peak_f = 0
    orders = collections.Counter()
    # ⚠ 类型按**第一次进入接触时**记，不按最后一次看到的记：单位地址会被回收，
    #   一个步兵死了、同一个地址马上变成新盖的农场 —— 第一版因此把 hhou/hgra 算进了"我方投入"。
    typ = {}
    cx = cy = cn = 0
    for t, units in frames[lo:hi + 1]:
        cm, cf = contact_sets(units, me, foe)
        part_m |= cm
        part_f |= cf
        peak_m = max(peak_m, len(cm))
        peak_f = max(peak_f, len(cf))
        for r in units:
            if r[A] in cm or r[A] in cf:
                typ.setdefault(r[A], r[TYP])
            if r[A] in cm:
                # ⚠ 09-15：订单号 0 要按任务句柄拆开 —— 交战调查里"订单 0"占 53%，
                #   其中 49 个百分点**任务对非空**（在逼近/在射程里），真正空闲只有 3.7%。
                #   混成一列"没订单"会把人引向"兵在发呆"的错误结论。
                o = r[ORD]
                if o == ATTACK:
                    k = "攻击"
                elif o == SMART:
                    k = "smart"
                elif o in (0, -1):
                    k = "真空闲" if (r[7] in EMPTY and r[8] in EMPTY) else "有任务无订单"
                else:
                    k = "其他"
                orders[k] += 1
            if r[A] in cm or r[A] in cf:
                cx += r[X]
                cy += r[Y]
                cn += 1
    # 结算帧：结束后 TAIL 秒内最后一帧
    j = hi
    while j + 1 < len(frames) and frames[j + 1][0] <= t1 + TAIL:
        j += 1
    # 地址还在但类型变了 = 原来那个单位死了、地址被回收（算阵亡）
    alive = {r[A] for r in frames[j][1] if r[HP] > 0 and typ.get(r[A], r[TYP]) == r[TYP]}
    lost_m = collections.Counter(typ[a] for a in part_m if a not in alive)
    lost_f = collections.Counter(typ[a] for a in part_f if a not in alive)
    comp_m = collections.Counter(typ[a] for a in part_m)
    comp_f = collections.Counter(typ[a] for a in part_f)
    # 地点：接触单位的重心离谁的大厅近
    where = "野外"
    if cn:
        gx, gy = cx / cn, cy / cn
        halls = [(r[OWN], r[X], r[Y]) for r in frames[lo][1] if r[TYP] in HALLS and r[HP] > 0]
        best = None
        for own, hx, hy in halls:
            d = math.hypot(hx - gx, hy - gy)
            if d <= BASE_R and (best is None or d < best[0]):
                best = (d, own)
        if best:
            where = "我方基地" if best[1] == me else "敌方基地"
    heroes_m = sorted(typ[a] for a in part_m if is_hero(typ[a]))
    heroes_dead = sorted(typ[a] for a in part_m if is_hero(typ[a]) and a not in alive)
    return dict(t0=t0, t1=t1, where=where, peak_m=peak_m, peak_f=peak_f,
                comp_m=comp_m, comp_f=comp_f, lost_m=lost_m, lost_f=lost_f,
                heroes=heroes_m, heroes_dead=heroes_dead, orders=orders)


# ---------------------------------------------------------------- 逐秒曲线（09-16）

# ★★ 09-16 敌方抱团进攻时我方单位几乎没有战斗力，很快被团灭。为此交战中实时统计所有参战单位的各项数据，
#        战斗结束后按这些记录做一次复盘。
#   summarize 只回答"死了几个"，回答不了"为什么死"。三场决定性守家战是手工逐秒拆帧才看出来的：
#     * 170914（兵力 25:26，战损 26:9）开战前 4 秒双方每秒打出差不多，第 8 秒起我方输出塌到一半以下，人还剩八成
#     * 被 mod_combat 拉出来的 16/9/11 个，录像段末尾活着 0/0/0
#   ⚠ 手工拆帧时曾拿交战中段几秒算过"敌方单兵每秒 36~40、我方 17~20"，**按全场接触人·秒算是 36:30 和 30:30** ——
#     173118 不是单兵打不过，是敌方接触人·秒是我方的 2.3 倍（人少、还有 9 个在 7000 外打野）。
#     挑几秒算比例会挑出想看的结论，所以账本里单兵输出一律按全场人·秒，两边的人·秒也一起记。
#   这些量现在是账本里的固定字段，每场仗都算（mod_fight 实时、match_review 每局）。
#   口径：
#     打出 / 挨打 = 敌方 / 我方作战单位**掉的血**（这一帧比上一帧少的；消失或地址换了类型 = 按上一帧血量全算）。
#                   回血不扣（牧师在奶的话，"打出"会大于敌方净掉血 —— 那正是要看的）。
#     单兵输出    = 打出 / 我方接触单位·秒（接触 = 900 内有敌方作战单位，和 contact_sets 同一个口径）。
#     背离        = 我方接触单位这一帧离最近敌人比上一帧远 8 以上（边打边走的远程也会算进去，看的是比例变化）。
#     输出塌陷    = 开战第 1~4 秒平均每秒打出记为基线；第 5 秒起连续 3 秒平均低于基线一半、
#                   而我方接触人数还有峰值的 6 成以上 —— 人还在、输出没了。
MOVE_EPS = 8.0
LIVE_SECS = 5.0


def _fighter_maps(units, me):
    mine = {r[A]: r for r in units if r[OWN] == me and is_fighter(r)}
    foes = {r[A]: r for r in units if 0 <= r[OWN] < 12 and r[OWN] != me and is_fighter(r)}
    return mine, foes


def _hp_lost(prev, now):
    lost = 0.0
    for a, r in prev.items():
        n = now.get(a)
        if n is None or n[TYP] != r[TYP]:
            lost += max(0, r[HP])
        elif n[HP] < r[HP]:
            lost += r[HP] - n[HP]
    return lost


def curve(frames, lo, hi, me, tail=TAIL):
    """一场仗（frames[lo..hi] 是接触段）逐秒拆。返回可以直接写进 JSON 的 dict。"""
    t0, t1 = frames[lo][0], frames[hi][0]
    j = hi
    while tail and j + 1 < len(frames) and frames[j + 1][0] <= t1 + tail:
        j += 1
    bins = {}
    dealt = taken = 0.0
    us_m = us_f = 0.0
    toward = away = still = 0
    first = {}
    prev = None
    for k in range(lo, j + 1):
        t, units = frames[k]
        mine, foes = _fighter_maps(units, me)
        cm, cf = contact_sets(units, me)
        sec = int(max(0.0, t - t0))
        b = bins.setdefault(sec, [sec, 0, 0, 0.0, 0.0])
        b[1] = max(b[1], len(cm))
        b[2] = max(b[2], len(cf))
        for a in cm:
            first.setdefault(a, t - t0)
        if prev is not None:
            pt, pm, pf, pcm, pcf = prev
            dt = max(0.0, t - pt)
            d_f = _hp_lost(pf, foes)
            d_m = _hp_lost(pm, mine)
            dealt += d_f
            taken += d_m
            b[3] += d_f
            b[4] += d_m
            us_m += len(pcm) * dt
            us_f += len(pcf) * dt
            fx = [(r[X], r[Y]) for r in pf.values()]
            if fx:
                for a in pcm:
                    r0, r1 = pm.get(a), mine.get(a)
                    if r0 is None or r1 is None or r1[TYP] != r0[TYP]:
                        continue
                    nx, ny = min(fx, key=lambda p: (r0[X] - p[0]) ** 2 + (r0[Y] - p[1]) ** 2)
                    d0 = math.hypot(r0[X] - nx, r0[Y] - ny)
                    d1 = math.hypot(r1[X] - nx, r1[Y] - ny)
                    if d1 < d0 - MOVE_EPS:
                        toward += 1
                    elif d1 > d0 + MOVE_EPS:
                        away += 1
                    else:
                        still += 1
        prev = (t, mine, foes, cm, cf)
    rows = [bins[s] for s in sorted(bins)]
    series = {r[0]: r[3] for r in rows}
    peak_m = max((r[1] for r in rows), default=0)
    early = [series.get(s, 0.0) for s in range(1, 5)]
    base = sum(early) / 4.0
    collapse = None
    if base >= 50.0:
        last = max(series) if series else 0
        for s in range(5, last - 1):
            win = [series.get(s + i, 0.0) for i in range(3)]
            if sum(win) / 3.0 < base * 0.5 and bins.get(s, [0, 0])[1] >= 0.6 * peak_m:
                collapse = s
                break
    moves = toward + away + still
    arrive = sorted(first.values())
    return dict(
        dur=round(t1 - t0, 1), dealt=round(dealt), taken=round(taken),
        rateM=round(dealt / us_m, 1) if us_m > 0 else None,
        rateF=round(taken / us_f, 1) if us_f > 0 else None,
        unitSecsM=round(us_m, 1), unitSecsF=round(us_f, 1),
        awayShare=round(away / moves, 3) if moves else None,
        collapseAt=collapse, baseDealt=round(base),
        arriveP90=round(arrive[int(0.9 * (len(arrive) - 1))], 1) if arrive else None,
        bins=[[r[0], r[1], r[2], round(r[3]), round(r[4])] for r in rows])


PULL_RE = re.compile(r"(\w{4})@([0-9A-Fa-f]+) (\d+)/(\d+) 正在挨 .*拉出来")


def pulls_survival(frames, lo, hi, me, wall0, pull_rows, tail=TAIL):
    """这场仗前后被 mod_combat 拉出来的人，录像段末尾还活着几个。
    pull_rows: [(datetime, 日志文本)]（modlog.read("mod_combat") 的格式）。wall0 = frames 的 t=0 对应的墙钟。"""
    if not pull_rows:
        return None
    w_start = wall0 + timedelta(seconds=frames[lo][0] - 2.0)
    w_end = wall0 + timedelta(seconds=frames[hi][0] + tail)
    pulled = {}
    for at, text in pull_rows:
        if not (w_start <= at <= w_end):
            continue
        m = PULL_RE.search(text)
        if m:
            pulled[int(m.group(2), 16)] = (m.group(1), int(m.group(3)), int(m.group(4)))
    if not pulled:
        return dict(n=0, alive=0, hpShareMax=None)
    last = frames[-1][1]
    alive = {r[A]: r for r in last if r[HP] > 0}
    surv = sum(1 for a, v in pulled.items() if a in alive and alive[a][TYP] == v[0])
    share = max((v[1] / v[2]) for v in pulled.values() if v[2] > 0)
    return dict(n=len(pulled), alive=surv, hpShareMax=round(share, 2))


def record(frames, lo, hi, me, wall0, pull_rows=None):
    """一场仗的账本条目（summarize + curve + 被拉存活），可以直接 json.dumps。"""
    s = summarize(frames, lo, hi, me)
    c = curve(frames, lo, hi, me)
    return dict(
        start=(wall0 + timedelta(seconds=s["t0"])).isoformat(timespec="seconds"),
        end=(wall0 + timedelta(seconds=s["t1"])).isoformat(timespec="seconds"),
        where=s["where"], peak=[s["peak_m"], s["peak_f"]],
        lostM=dict(s["lost_m"]), lostF=dict(s["lost_f"]),
        compM=dict(s["comp_m"]), compF=dict(s["comp_f"]),
        heroes=s["heroes"], heroesDead=s["heroes_dead"], orders=dict(s["orders"]),
        pulls=pulls_survival(frames, lo, hi, me, wall0, pull_rows), **c)


def line(rec) -> str:
    """账本条目 -> 一行中文（mod_fight 日志和 match_review 共用）。"""
    lm, lf = sum(rec["lostM"].values()), sum(rec["lostF"].values())
    tot = sum(rec["orders"].values()) or 1
    atk = 100 * rec["orders"].get("攻击", 0) // tot
    rm, rf = rec.get("rateM"), rec.get("rateF")
    unit = f"单兵每秒 {rm:.0f}:{rf:.0f}" if rm is not None and rf is not None else "单兵每秒 -"
    p = rec.get("pulls")
    pull = "" if not p else f"｜被拉出来 {p['n']} 活 {p['alive']}"
    away = "-" if rec.get("awayShare") is None else f"{100 * rec['awayShare']:.0f}%"
    col = "" if rec.get("collapseAt") is None else f"｜★输出塌陷@{rec['collapseAt']}s（基线每秒 {rec['baseDealt']}）"
    dead = f"｜英雄阵亡 {' '.join(rec['heroesDead'])}" if rec.get("heroesDead") else ""
    # 参战 = 整场进过接触的人（战损从这里面算）；接触峰值 = 同一时刻最多几个人。两个都写，别让人拿峰值去对战损。
    pm, pf = sum((rec.get("compM") or {}).values()), sum((rec.get("compF") or {}).values())
    joined = f"参战 我 {pm}:敌 {pf} " if (pm or pf) else ""
    sm = sum(v for k, v in rec["lostM"].items() if k in summons())
    summ = f"（我方含召唤物 {sm}）" if sm else ""
    return (f"{rec['start'][11:19]} {rec['where']} {rec['dur']:.0f}s {joined}接触峰值 我 {rec['peak'][0]}:敌 {rec['peak'][1]}"
            f" 战损 {lm}:{lf}{summ}｜打出 {rec['dealt']} 挨打 {rec['taken']}（{unit}，接触人·秒 {rec.get('unitSecsM', 0):.0f}:{rec.get('unitSecsF', 0):.0f}）"
            f"｜接触兵 攻击令 {atk}% 背离 {away}{col}{pull}{dead}")


# ---------------------------------------------------------------- 对局对齐

def _match_start(path):
    m = re.search(r"match-(\d{8})-(\d{6})", os.path.basename(path))
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def read_match(path):
    """对局文件：每条记录的墙钟时刻 -> 拍号，外加防守推演原句和结局。"""
    base = _match_start(path)
    day = base.replace(hour=0, minute=0, second=0, microsecond=0)
    prev = None
    ticks, defend, outcome, end = [], [], None, None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = d.get("t")
        if not ts:
            continue
        hh, mm, rest = ts.split(":")
        at = day + timedelta(hours=int(hh), minutes=int(mm), seconds=float(rest))
        if prev is not None and at < prev - timedelta(hours=12):
            day += timedelta(days=1)
            at += timedelta(days=1)
        prev = at
        end = at
        tick = d.get("tick") or 0
        if d.get("kind") == "state":
            ticks.append((at, tick))
        elif d.get("kind") == "action":
            text = (d.get("data") or {}).get("text", "")
            if text.startswith("DEFEND"):
                defend.append((at, tick, text))
        elif d.get("kind") == "lesson":
            outcome = (d.get("data") or {}).get("outcome")
    return dict(start=base, end=end, ticks=ticks, defend=defend, outcome=outcome)


def tick_at(ticks, when):
    if not ticks:
        return None
    i = bisect.bisect_right([t for t, _ in ticks], when) - 1
    return ticks[max(0, i)][1]


def fights_dir():
    """战斗帧目录。★ 09-16 多开：对局目录是 inst<N>/matches 时，帧在同一个实例的 fights/ ——
    原来固定读 OUTDIR（没设 WAR3_FIGHT_DIR 时是单实例的 build_intel4/fights），
    按实例复盘时一场仗都找不到，「决定性一仗」显示「-」、看着像是这局没打仗。设了 WAR3_FIGHT_DIR 就听它的（模块进程）。"""
    if os.environ.get("WAR3_FIGHT_DIR"):
        return OUTDIR
    parent = os.path.dirname(os.path.abspath(MATCHES))
    name = os.path.basename(parent).lower()
    if name.startswith("inst") and name[4:].isdigit():
        return os.path.join(parent, "fights")
    return OUTDIR


def fight_files(start, end):
    out = []
    for f in sorted(glob.glob(os.path.join(fights_dir(), "fight-*.jsonl"))):
        m = re.search(r"fight-(\d{8})-(\d{6})", os.path.basename(f))
        if not m:
            continue
        fs = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        if start - timedelta(seconds=30) <= fs <= end:
            out.append((fs, f))
    return out


def load_frames(path):
    frames = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("u"):
            frames.append((float(d.get("t", 0.0)), d["u"]))
    return frames


def fmt(c: collections.Counter) -> str:
    return " ".join(f"{k}×{v}" for k, v in c.most_common()) or "—"


def report_match(path):
    mt = read_match(path)
    print(f"== {os.path.basename(path)}  结局 {mt['outcome']}  "
          f"最后一拍 {mt['ticks'][-1][1] if mt['ticks'] else '?'}")
    rows = []
    for fs, f in fight_files(mt["start"], mt["end"]):
        frames = load_frames(f)
        if not frames:
            continue
        me = frames[0][1][0][OWN]
        for lo, hi in segment(frames, me):
            s = summarize(frames, lo, hi, me)
            s["w0"] = fs + timedelta(seconds=s["t0"])
            s["w1"] = fs + timedelta(seconds=s["t1"])
            rows.append(s)
    if not rows:
        print("   没有和敌方玩家的接触（或这局没有录到帧）")
        return []
    worst = max(rows, key=lambda s: sum(s["lost_m"].values()))
    for s in rows:
        k0, k1 = tick_at(mt["ticks"], s["w0"]), tick_at(mt["ticks"], s["w1"])
        lm, lf = sum(s["lost_m"].values()), sum(s["lost_f"].values())
        star = "★" if s is worst and lm > 0 else " "
        dur = s["t1"] - s["t0"]
        tot = sum(s["orders"].values()) or 1
        od = "/".join(f"{int(100 * s['orders'][k] / tot)}" for k in ("攻击", "smart", "有任务无订单", "真空闲"))
        print(f" {star} 拍 {k0}~{k1}（{dur:.0f} 秒）{s['where']}  接触峰值 我 {s['peak_m']} : 敌 {s['peak_f']}  "
              f"战损 我 {lm} : 敌 {lf}  我方订单 攻击/smart/有任务无订单/真空闲 {od}%")
        print(f"     我方投入 {fmt(s['comp_m'])}  -> 死 {fmt(s['lost_m'])}")
        print(f"     敌方投入 {fmt(s['comp_f'])}  -> 死 {fmt(s['lost_f'])}")
        if s["heroes"]:
            print(f"     我方英雄 {' '.join(s['heroes'])}"
                  f"{'｜阵亡 ' + ' '.join(s['heroes_dead']) if s['heroes_dead'] else ''}")
        said = [d for d in mt["defend"]
                if s["w0"] - timedelta(seconds=10) <= d[0] <= s["w1"]]
        if said:
            first = said[0]
            m = re.search(r"推演[^｜]*", first[2])
            print(f"     推演（拍 {first[1]}）：{m.group(0) if m else first[2][:60]}"
                  f"｜实际 我方剩 {max(0, s['peak_m'] - lm)}/{s['peak_m']}")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--match", default=None)
    args = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(MATCHES, "match-*.jsonl")))
    files = [f for f in files if '"kind":"finalized"' in open(f, encoding="utf-8", errors="replace").read()[-4000:]]
    if args.match:
        files = [f for f in files if os.path.basename(f).startswith(args.match)]
    for f in files[-args.n:]:
        report_match(f)
        print()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

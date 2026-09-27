"""mod_scavenge.py —— 独立模块：捡地上的书和装备。

    python mod_scavenge.py

第一个从单体里拆出来的模块。挑它打头阵是因为三点：
  1. **最独立** —— 只碰英雄，而且只在没打架的时候碰
  2. **出错代价最小** —— 最坏是少捡一件装备，不会送掉部队
  3. **原来就是坏的** —— 经验书/属性书是永久加成，一直没捡到

原来它在 3.2 秒的战略拍上，还叠了两层节流（4 拍才扫一次地面 + 3 拍才准捡
一次），东西掉在英雄脚边要十几秒才"发现"。现在自己一个进程，200ms 一轮。

★ 怎么保证不和别的模块打架
    不靠"检查它有没有任务"（那要走通道，130ms 一次，而且会和别人抢）。
    靠 w3claim 的**租约**：拿到这个英雄 6 秒的指挥权才下命令，
    这 6 秒里别人碰不了它，我也不重复下令。

    这一条同时修掉了老版本最要命的 bug：每秒重下一次 getitem，
    每次都把上一次的寻路清零 —— 英雄在装备旁边空转 30 秒一步没走成。
    **租约天然就是"别再下一次"**，不需要额外的去重逻辑。

★ 读和写的成本差 1500 倍，所以判断全用读
    读快照 0.087ms（共享内存），下命令约 130ms（要起一个进程）。
    所以：每 200ms 读一次快照做判断，只有真要捡的时候才付那 130ms。
"""

from __future__ import annotations

import collections
import json
import math
import os
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()

from w3cmd import ORDER_SMART       # noqa: E402
import itemvalue                    # noqa: E402  09-16：东西给谁（按物品效果 × 英雄主属性，见 itemvalue.py）
import itemuse                      # noqa: E402  09-17：商店补货（买什么、什么时候买，见 itemuse.py）
from w3claim import Claims, PRIO    # noqa: E402
from w3state import Session                   # noqa: E402
import modlog  # noqa: E402
from w3lease import identity, read as read_leases, write_deadlines  # noqa: E402

TICK = 0.2            # 主循环周期
ITEM_SCAN = 1.5       # 扫地面的周期（这一步要走通道，约 190ms，别太频）
# ★ 只捡**脚边**的，不专程跑。
#
# 原来是 1400 —— 那不是"顺路"，那是让英雄**独自穿过半张地图**去拿一件装备。
# 对局里反复出现「一个英雄独自冲上去」，这是其中一条路径：
# 掉落往往在刚打完的野怪堆旁边，而那附近通常还有别的野怪。
#
# 暴雪那边是 SetHeroesTakeItems(true) —— 引擎让英雄**路过时**捡起来，
# 不会为了一件装备专门派它出门。600 大约就是"就在旁边"。
REACH = 600.0
LEASE = 6.0           # 捡**脚边**东西给的指挥权时长；走远路的按距离加时（walk_lease）
# ★ 09-08 实测：Hamg 去捡 tdex（小兵掩护×5，距离 1040）—— 6 秒走近了 860，
#   **它真的在走**，只是租约到期被判"没捡到"，然后这件东西就被放掉了。
#   定长租约配不定长路程，等于"走得越远越注定失败"，而掩护那条路开出来的
#   恰恰全是远的。实测步速约 150/秒（要绕路，比标称慢一半）。
WALK_SPEED = 150.0
SHOP_FOOD = itemuse.load_food()   # 09-17：数英雄身边自己的作战单位用（itemuse.army_with；建筑 food 0 不算）
SHOP_ESCORTED_SHORT = 800.0       # 英雄在家且商店只差几步时，身边有部队也允许快速补货
# 买东西被拒后多久再试（墙钟秒，按回执里的引擎原因码）：88 刚买空 / 89 还没到货 = 等补货；17 / 64 = 店边没有能接货的买家，人走近了就好
SHOP_REFUSE_BACKOFF = {88: 40.0, 89: 40.0, 17: 8.0, 64: 8.0, 8: 15.0, 221: 300.0}
LEASE_MAX = 20.0      # 再远也不能让英雄整局挂在捡拾租约上（那就整局不打野）


def drop_owned(items, owned):
    """扫描结果里剔掉**已经有持有者**的物品（背包里的），返回 (剩下的, 剔掉几件)。
    ★★ 09-15 动作 46 枚举的是物品注册表，背包里的东西也在里面、坐标冻在进包那一刻。
      这里原来把它们全当成"地上的"：捡进包里的永远不从扫描里消失 -> 永远判"没捡到"，
      出生自带的 stwp 每局被"去捡（距离 0）"，捡拾租约挂着、英雄被划进撤退档、打野停摆。
      剔掉之后，捡到的东西下一次扫描就不在 live 里 -> 自然判"捡到"（下面那段一个字不用改）。
    读不到持有者（None）的**保留**：宁可多试一次，也别把地上的漏掉。
    """
    keep = []
    dropped = 0
    for it in items:
        if owned(it[0]):
            dropped += 1
            continue
        keep.append(it)
    return keep, dropped


def walk_lease(d: float) -> float:
    """走 d 距离该给多长的指挥权。脚边的仍是 6 秒，远的按步速加时。"""
    return min(LEASE_MAX, max(LEASE, LEASE + d / WALK_SPEED))
# ★★★★ 09-09 英雄捡地上的东西很慢：常常一个人在野外待很久，
#     看上去像在犹豫要不要捡。
#
#   实测（最近 4000 行日志）：派出去捡 **648 次、累计占用英雄 6014 秒**，
#   真捡到 **27 件** —— 成功率 4%。其中 **226 次出发时距离就 <200**，
#   也就是英雄**站在东西上面**还是捡不起来，日志原文：
#       去捡 stwp（距离 **0**，给 6 秒）-> 没捡到（走近了 0）-> 再试 -> 再试 -> 放弃
#   三次 18 秒，全程站着不动。
#
#   根因是两条规则都是**按件**算的（连续 3 次没走近，或累计 6 次），
#   而**没有任何一条处理"这个英雄什么都捡不了"**（背包满）——
#   地上 22 件时它会一件一件试过去，英雄就一直待在野外。
#
#   ⚠ 背包满不满读不到：物品从注册表枚举，包里的东西坐标就在英雄脚下，
#     和地上的分不出来（要新的引擎接口）。所以用**行为**判：
#     NEAR_FAIL_MAX  —— 近距离失败几次就拉黑这件（走路不是问题，重试没意义）
#     FULL_STREAK    —— 连着几件**不同**的东西都在近距离失败 = 这个英雄装不下了
#     FULL_COOLDOWN  —— 判定装不下之后，多久之内不再派它去捡
NEAR_DIST = 200.0     # 多近算"已经站在上面"
NEAR_FAIL_MAX = 1     # 近距离失败 1 次就够 —— 本来就不用走路，重试改变不了任何输入
FULL_STREAK = 3       # 连着 3 件不同的东西近距离失败 = 背包满
FULL_COOLDOWN = 90.0  # 判定满了之后，这个英雄 90 秒内不再去捡

HP_FLOOR = 0.45       # 残血英雄不去捡东西，先保命
# ★ 09-18 打野掉在地上的装备英雄不捡就走了。实机 inst5 兽族局心跳：先知「被租约锁×79、血量<45%×51」、剑圣「被租约锁×38」——
#   打完野英雄常被保命/孤军锁住或残血，原来一律不派。周围 SAFE_R 内没有敌人和野怪时，这种英雄也可以捡 SAFE_PICK_R 以内的东西。
SAFE_R = 900.0
SAFE_PICK_R = 500.0

# ★★ 09-08 装备掉在自家小兵附近时也要派英雄去捡；
#          去捡时要考虑路上会不会被偷袭。
#
# 上面那条 600 是对的，但它把**两件事**一起关掉了：
#   1. 英雄独自穿过半张地图去拿装备（要关 —— 对局里看到的送死路径之一）
#   2. 装备掉在**我们自己部队脚下**，英雄在 600 外（不该关）
# 而 2 恰恰是打野掉落的常态：怪死在营地，英雄要么在 900 外压阵、
# 要么残血回了家，等它自己走近，东西早被下一波仗盖过去了。
# 实测 117 次"去捡"的距离：中位 488、**最大 598** —— 上限咬得死死的，
# 也就是说 600 外的掉落一次都没去过，而且**连一行日志都没有**。
#
# 所以不是把 600 调大（那就退回 1400 的老毛病），而是加一条**另一种资格**：
# 东西旁边站着我们自己的部队 = 这块地是我们占着的，英雄不是一个人去。
ESCORT_R = 500.0      # 离掉落多近算"小兵在旁边"
ESCORT_MIN = 3        # 至少几个战斗单位在旁边才算掩护得住
ESCORT_REACH = 2000.0 # 有掩护也不是随便跑：超过这个距离照样不去
CLEAR_REACH = 1500.0  # 09-21 路上干净（没埋伏、东西周围 SAFE_R 内没敌人/野怪）时，这么远以内不用小兵陪也去捡
# 路上偷袭：从英雄到装备连一条直线，线**两侧** AMBUSH_R 内有敌人/野怪
# 就算路上有埋伏。已经被我们部队缠住的那些不算 —— 它们没空来堵路。
AMBUSH_R = 700.0
# ★ 09-16 补令回执（见 reassert_step）
REASSERT_AFTER = 1.5    # 租约期内这么久没走近物品，就当被别的指令覆盖了
REASSERT_GAIN = 40.0    # 走近不到这么多不算"有进展"（站着原地抖动不算）
REASSERT_MAX = 3        # 每件东西最多补几次 —— 再多就是在和别的控制层拉锯了
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}

LOGF = os.environ.get(
    "WAR3_MOD_LOG",
    os.path.join(w3paths.BIN, "ai_run.log"),
)


PICKF = os.path.join(os.path.dirname(LOGF), "pickup.json")


def publish_ground(items, now: float, game_pid: int, epoch=None) -> None:
    """地上物品发布给大脑用于复盘；捡拾模块自行租用英雄，战略主力不会原地等待。"""
    path = os.path.join(os.path.dirname(PICKF), "ground_items.json")
    data = {"schema": 1, "gamePid": game_pid, "epoch": str(epoch) if epoch is not None else None,
            "writtenAtMs": int(time.time() * 1000), "items": [[int(a), t, round(x), round(y)] for a, t, x, y in items]}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def safe_around(hero, foes, radius: float = SAFE_R) -> bool:
    """英雄周围 radius 内没有活着的玩家单位（0~11）和野怪（12）。"""
    return not any(f.hp > 0 and f.owner <= 12 and math.hypot(f.x - hero.x, f.y - hero.y) <= radius for f in foes)


def publish_pickup(busy: dict, now: float, game_pid: int,
                   epoch=None, units=None) -> None:
    """发布固定截止、PID、对局和generation；重复发布不会延长截止。

    战略拍读它，这几秒不给这个英雄下打野/集结/收回命令
    （war3ai.ps1 的 W3-IsPickingUp）。现场：模块 13 秒内三次"去捡 rde1"，
    同一秒战略拍下了打野令、9 秒后又收回集结点。
    空了也发布空units；进程停更时读者按绝对截止自行释放。
    """
    deadlines = {addr: at + lease for addr, (_item, at, lease) in busy.items()}
    write_deadlines(PICKF, deadlines, now, game_pid, epoch, units)


RESERVEF = os.path.join(os.path.dirname(PICKF), "item_reserve.json")


def write_item_reserve(gold: int, why: str, game_pid: int, path: str = RESERVEF) -> None:
    """09-22 残血英雄缺血瓶：让大脑下单前留出这么多金（war3-brain runtime._item_reserve 读；itemuse.RESERVE_TTL 内有效）。
    gold=0 也写（清掉上一次的留钱）。写临时文件再替换，别让大脑读到半截。"""
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"pid": int(game_pid or 0), "wall": time.time(), "gold": int(gold), "why": why}, fh, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def note(msg: str) -> None:
    """写进和主 AI 同一份日志 —— 直播页照着它显示，观众不用关心是哪个进程干的。"""
    line = f"  [捡拾] {msg}\n"
    try:
        with open(LOGF, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def reassert_step(prog: list, now: float, dnow: float, done: int,
                  after: float = REASSERT_AFTER, gain: float = REASSERT_GAIN,
                  cap: int = REASSERT_MAX) -> bool:
    """租约期内要不要补一次捡取令。prog = [上次有进展的时刻, 迄今最近距离]，原地更新。

    ★★ 09-16 仍有不捡东西的情况（对局画面：英雄站在书旁边）。
      修好物品扫描后装备 5 件都 1~2 秒到手，**书（tint）三次都没捡到**：
        16:42:54 没捡到：tint 10 秒后还在地上（这 10 秒走近了 495）
        16:45:30 没捡到：tint 13 秒后还在地上（这 13 秒走近了 -439） <- 反而走远了
      cmdlog 里那 13 秒没有任何模块碰过这个英雄；对局日志 16:45:17 同一秒策略层下了
      「打野……英雄压阵」。策略层是认租约的（16:42:45「不打野：英雄去捡装备」），
      输在**时序**：它这一拍读 pickup.json 时本模块还没写出来，于是把英雄当可用的下了令，
      后下的令覆盖了捡取令；下一拍它让路了，可英雄已经在去压阵点的路上，而本模块从不补令。
      装备离得近、1.6 秒就到手，赶在覆盖之前；书远，就中招。
      补一次就够（策略层下一拍就看得见租约），封顶 REASSERT_MAX 次防止拉锯。"""
    if dnow < prog[1] - gain:
        prog[0], prog[1] = now, dnow
        return False
    return (now - prog[0]) >= after and done < cap


def dist(a, b) -> float:
    return math.hypot(a.x - b[2], a.y - b[3])


def seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """点到**线段** ab 的距离。

    ⚠ 必须是线段不是直线：英雄在 A、装备在 B，一个站在 A 背后很远的野怪
      到**直线** AB 的垂距可能很小，但它根本不在路上。用线段就自然排除了。
    """
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def escort_at(item, mine) -> int:
    """掉落旁边站着几个我们自己的战斗单位（工人不算 —— 它们不掩护任何人）。"""
    n = 0
    for u in mine:
        if u.type in WORKERS or u.hp <= 0:
            continue
        if math.hypot(u.x - item[2], u.y - item[3]) <= ESCORT_R:
            n += 1
    return n


def _load_dps() -> dict:
    """类型 -> 每秒伤害（units.json，和 mod_combat._load_units 同一份数据）。读不到 = 空表（全都当会打人）。"""
    p = os.environ.get("WAR3_UNITS_JSON", w3paths.UNITS_JSON)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return {k: (float(v.get("dmgAvg") or 0) / float(v["cool"])) if float(v.get("cool") or 0) > 0 else 0.0
                for k, v in d.items() if isinstance(v, dict)}
    except Exception:                        # noqa: BLE001
        return {}


DPS = _load_dps()


def can_attack(u) -> bool:
    """会不会打人。★ 09-22 1-287：「路上有埋伏」把**两栖鱼人小屋**（nmh1，中立建筑、攻击 0）当成拦路的野怪，
    力量之书在地上躺了 2 分多钟（00:03:43「不去捡 tstr：路上 nmh1 离路线 428」，00:05:54 才被捡走）。
    小屋、金矿、哨塔这类攻击为 0 的不算威胁；数据表里没有的类型照旧当会打人（保守）。"""
    return DPS.get(u.type, 1.0) > 0


def ambush_on_route(hero, item, foes, mine):
    """路上有没有埋伏。返回 None = 安全，否则返回堵路的那一个（拿来写日志）。

    「已经被我们部队缠住的敌人不算」—— 判据是它身边 ESCORT_R 内有我们
    ESCORT_MIN 个人。这一条很重要：打野刚打完，营地边上常常还剩一两只
    残怪被小兵围着，那不构成偷袭，可它离路线一定很近。少了这一条，
    **打野掉落永远捡不到**，而那正是这次要修的场景。
    """
    for f in foes:
        if f.hp <= 0 or not can_attack(f):
            continue
        d = seg_dist(f.x, f.y, hero.x, hero.y, item[2], item[3])
        if d > AMBUSH_R:
            continue
        held = 0
        for u in mine:
            if u.type in WORKERS or u.hp <= 0:
                continue
            if math.hypot(u.x - f.x, u.y - f.y) <= ESCORT_R:
                held += 1
                if held >= ESCORT_MIN:
                    break
        if held < ESCORT_MIN:
            return (f, d, held)
    return None


def main() -> int:
    modlog.tee_stdout("mod_scavenge")
    note("模块启动（独立进程，200ms 一轮）")
    # Session 自己管重连 —— 游戏重启之后接回去。
    # 不这么做的话，模块会连着一个已经死掉的进程继续空转，
    # 而且日志里一行错误都没有（实测发生过，白跑了二十分钟）。
    ses = Session()
    last_match = None
    generations = {}
    claims = Claims("scavenge")
    items: list[tuple[int, str, float, float]] = []
    last_scan = 0.0
    bag_last = 0                      # 09-15：最近一次扫描剔掉了几件背包里的（见 drop_owned）
    dead_last = 0                     # 09-16：最近一次扫描剔掉了几件用掉的书（血量 0 的死物品，见 w3cmd.items_on_ground）
    taken: dict[int, float] = {}      # 物品地址 -> 我们下令去捡的时刻
    fails: dict[int, int] = {}        # 物品地址 -> 连续失败次数
    # ★ 英雄 -> (它正在去拿的那件东西, 下令时刻)
    #
    # 只有 taken 是不够的：两件东西的 taken 会在不同时刻过期，于是同一个英雄
    # 被在它们之间来回改派 —— 实测日志：
    #     Hmkg 去捡 tst2（距离 1382）
    #     Hmkg 去捡 fgsk（距离 666）     <- 被改派
    #     Hmkg 去捡 tst2（距离 996）     <- 又改回去
    # 而**每次改派都会把上一次的寻路清零**，它永远走不到。
    # 这就是那个"英雄在装备旁边空转 30 秒"的 bug 换了个地方重演。
    #
    # 所以锁的对象要从"东西"改成"人"：一个英雄同时只认一个目标，
    # 到手或超时之前不换。
    busy: dict[int, tuple[int, float]] = {}
    ignore: set[int] = set()      # 认定拿不到的（多半已在背包里），本局不再试
    near_fail: dict[int, int] = {}   # 物品 -> 在"脚边"失败了几次
    full_seen: dict[int, set[int]] = {}  # 英雄 -> 它在脚边捡不起来的那些物品
    full_until: dict[int, float] = {}    # 英雄 -> 在这个时刻之前别再派它去捡
    names: dict[int, str] = {}    # 物品地址 -> 名字（成功/失败日志要说得出是哪件）
    orders: dict[int, str] = {}   # 物品地址 -> 这次用的命令（getitem / smart）
    attempts: dict[int, int] = {} # 物品地址 -> 下过几次令（两种命令轮着用）
    pick_pub = False              # pickup.json 里有没有东西；空了再写一次 {} 就停
    # ★ 跳过理由计数 + 心跳。
    #   经验：一个只在成功时说话、失败时沉默的日志，会把人引向错误的结论。
    #   600 那条上限就是这么活了一整天的 —— 它每次拒绝都是**静默**的，
    #   于是"捡不到"和"没东西可捡"在日志上长得一模一样。
    #   去和不去都记在同一个计数器里：只有并排摆着，比例才有意义。
    tally: collections.Counter = collections.Counter()
    state: dict[int, str] = {}        # 物品地址 -> 这一分钟它为什么没人去（按件记，不按 tick 记）
    sent_at: dict[int, float] = {}    # 物品地址 -> 下令那一刻英雄离它多远
    stuck: dict[int, int] = {}        # 物品地址 -> 连续几次"下了令但一步没走近"
    progress: dict = {}               # (英雄, 物品) -> [上次有进展的时刻, 最近距离]（补令回执）
    reasserts: dict[int, int] = {}    # 物品地址 -> 租约期内补过几次令
    last_amb: dict[int, float] = {}
    last_hb = 0.0
    # ★ 09-16：没派人时的心跳（见主循环里 `if not heroes` 那条出口的注释）
    last_idle_hb = 0.0
    last_shop = 0.0
    shop_tally: collections.Counter = collections.Counter()   # 09-17 商店：买成/被拒/没进背包/跳过理由（心跳里报）
    shop_pending: dict = {}           # 持有者地址 -> (物品码, 买之前这件的个数, 下单时刻)：下一轮读背包核对进没进
    shop_walk_next: dict = {}         # 英雄地址 -> 这之前不再判「要不要走去藏宝室」（读背包约 150ms，20 秒一次）
    shop_refused: dict = {}           # (商店地址, 物品码) -> 这之前不再买（09-23：被拒按原因码退避，见 SHOP_REFUSE_BACKOFF）
    hurt_bags: dict = {}              # 09-22 残血英雄地址 -> (读的时刻, 背包)：判要不要让大脑留钱买血瓶（8 秒一读）
    idle_why: collections.Counter = collections.Counter()
    # ★★ 09-16 看门狗 + 循环顶部的**无条件**心跳。
    #   上一刀只给"没可用英雄"那条出口加了心跳，实测（16:26 那局）英雄一出来模块又沉默：
    #   下面那个"心跳"只在 `if parts:` 时才写（这一分钟没统计到东西就一个字不写 ——
    #   它是条件日志不是心跳，应该先无条件记数、再决定打不打印），紧接着 `if not items: continue` 也是静默的。
    #   而另开进程按同样步骤能扫到地上 7 件（tst2 力量之书、tint 药…），英雄满血。
    #   于是"卡死在某个调用上"和"空转但眼里没东西"在日志上一模一样。
    #   两件事一起补：① 心跳挪到循环最顶上、每 60 秒必写，带上每条出口走了几次；
    #   ② 看门狗线程：主循环超过 10 秒没转完一轮，就把卡在哪一步写出来。
    probe = {"loop_ts": time.monotonic(), "stage": "启动", "stage_ts": time.monotonic()}
    exits: collections.Counter = collections.Counter()
    loops = 0
    last_beat = 0.0
    slow = {"ms": 0.0, "where": ""}

    def _stage(name: str) -> None:
        probe["stage"] = name
        probe["stage_ts"] = time.monotonic()

    def _watchdog() -> None:
        warned = 0.0
        while True:
            time.sleep(2.0)
            now_w = time.monotonic()
            stuck_for = now_w - probe["loop_ts"]
            if stuck_for > 10.0 and now_w - warned > 30.0:
                warned = now_w
                note(f"看门狗：主循环 {stuck_for:.0f} 秒没转完一轮，卡在「{probe['stage']}」"
                     f"（进这一步已 {now_w - probe['stage_ts']:.0f} 秒）")

    threading.Thread(target=_watchdog, name="scavenge-watchdog", daemon=True).start()
    while True:
        try:
            probe["loop_ts"] = time.monotonic()
            loops += 1
            if probe["loop_ts"] - last_beat >= 60.0:
                last_beat = probe["loop_ts"]
                note(f"心跳：{loops} 轮｜出口 "
                     + ("、".join(f"{k}×{v}" for k, v in exits.most_common()) or "无")
                     + "｜商店 " + ("、".join(f"{k}×{v}" for k, v in shop_tally.most_common()) or "无")
                     + f"｜地上物品 {len(items)} 件（用掉的书 {dead_last} 件已剔除）｜派人中 {len(busy)}"
                     + f"｜最慢通道调用 {slow['ms']:.0f}ms（{slow['where'] or '-'}）"
                     + (f"｜★扫描异常：{ses.cmd.items_scan_error}"
                        if getattr(getattr(ses, "cmd", None), "items_scan_error", None) else ""))
                loops = 0
                exits.clear()
                shop_tally.clear()
                slow["ms"] = 0.0
                slow["where"] = ""
            _stage("读快照")
            snap = ses.read()
            if snap is None:
                exits["没快照"] += 1
                time.sleep(TICK)
                continue
            match = (ses.pid, snap.epoch) if snap.in_game else None
            if match != last_match:
                for addr in busy:
                    claims.give_back(addr)
                busy.clear()
                taken.clear()
                fails.clear()
                ignore.clear()
                names.clear()
                orders.clear()
                attempts.clear()
                generations.clear()
                tally.clear()
                state.clear()
                last_amb.clear()
                sent_at.clear()
                stuck.clear()
                progress.clear()
                reasserts.clear()
                shop_pending.clear()
                shop_walk_next.clear()
                items = []
                last_scan = 0.0
                pick_pub = False
                last_match = match
                publish_pickup({}, time.monotonic(), ses.pid, snap.epoch)
                if match is not None:
                    note(f"接上 War3 pid={ses.pid} epoch={snap.epoch}")
            if snap is None or not snap.in_game:
                exits["不在对局中"] += 1
                time.sleep(TICK)
                continue
            current = {u.addr: identity(u) for u in snap.mine}
            for addr, pair in generations.items():
                if current.get(addr) != pair:
                    busy.pop(addr, None)
                    claims.give_back(addr)
            generations = current
            # ── 09-17 商店补货（itemuse）：英雄在自家神秘藏宝室边上、附近没敌人、金子扣完还有 BUY_GOLD_FLOOR ──
            #   买东西不给英雄下令（令发给商店），不抢租约；一轮最多买一件，下一轮读背包核对进没进（回执）。
            _now_shop = time.monotonic()
            if _now_shop - last_shop >= 4.0:
                last_shop = _now_shop
                _stage("商店")
                vaults = [u for u in snap.mine if u.type in itemuse.VAULTS]
                shop_tier = 3 if any(u.type == "hcas" for u in snap.mine) else 2 if any(u.type == "hkee" for u in snap.mine) else 1
                # ★ 09-22 只给英雄买。原来二本 Backpack 之后把藏宝室边上的地面兵也当买家（伤兵够多时给它买卷轴），
                #   可兵的背包是 Aihn（AbilityData.slk：DataA 格数 2、**DataC 能使用物品 = 0**）—— 能拿、不能用。
                #   全部实例历史：兵拿着卷轴 165 次使用全被引擎拒，英雄 80 次全成。
                #   更糟的是兵背包里那张用不了的卷轴还算进「团队已有恢复卷轴」，把英雄的采购也挡掉了。
                carrier_bags = {}
                carriers = list(snap.heroes)
                # ★★ 09-22 残血英雄缺血瓶：让大脑留钱攒着买（itemuse.HURT_POTION_FRAC 上面那段）
                #   09-22 晚 恢复卷轴随身常备两张、商店有货就买：藏宝室附近的英雄卷轴不到两张，也留一张的钱（itemuse.item_reserve）
                reserve_bags = {}
                if vaults:
                    for h in snap.heroes:
                        hurt_h = shop_tier >= 2 and h.hp < itemuse.HURT_POTION_FRAC * max(1, h.hp_max)
                        near_v = min(itemuse.dist(v, h) for v in vaults) <= itemuse.SHOP_WALK_R
                        if not (hurt_h or near_v):
                            continue
                        hb = hurt_bags.get(h.addr)
                        if hb is None or _now_shop - hb[0] > 8.0:
                            hb = hurt_bags[h.addr] = (_now_shop, ses.cmd.inventory(h.addr))
                        reserve_bags[h.addr] = hb[1]
                reserve_gold, reserve_why, reserve_kind = itemuse.item_reserve(snap.heroes, reserve_bags, vaults, shop_tier)
                write_item_reserve(reserve_gold, reserve_why, ses.pid)
                if reserve_why:
                    shop_tally[f"{reserve_kind}·让大脑留钱"] += 1
                # ⚠ 09-17「附近有敌人」只算玩家（0~11）和野怪（12）：snap.foes 里还有中立被动（13~15 号，绵羊/小动物/商店）——
                #   3 号 020200 英雄走到藏宝室 177 码站了一整局没下单，1012 码外有一只绵羊 nshe（15 号）。
                by_addr = {u.addr: u for u in snap.mine}
                for addr, pend in list(shop_pending.items()):
                    if pend and _now_shop - pend[2] >= 1.0:
                        holder = by_addr.get(addr)
                        bag_now = ses.cmd.inventory(addr) if holder is not None else None
                        got = bag_now is not None and bag_now.count(pend[0]) > pend[1]
                        shop_tally[f"{pend[0]}·{'进背包' if got else '没进背包'}"] += 1
                        shop_pending.pop(addr, None)
                if not vaults:
                    shop_tally["没有藏宝室"] += 1
                elif snap.gold is None:
                    shop_tally["金子读不到"] += 1
                else:
                    team_has_sreg = any('sreg' in bag for bag in carrier_bags.values())
                    for h in carriers:
                        if h.addr in shop_pending:
                            continue
                        is_hero = h in snap.heroes
                        lease = claims.lease_of(h.addr) if hasattr(claims, "lease_of") else None
                        if lease and lease[0] == 1 and lease[1] == 75:
                            shop_tally["整队恢复·由大脑采购"] += 1
                            continue
                        v = min(vaults, key=lambda u: itemuse.dist(u, h))
                        d_v = itemuse.dist(v, h)
                        # 09-22 残血英雄急买血瓶（itemuse.HURT_POTION_FRAC 上面那段）：走得更远、带着部队也走、按急救线的钱
                        hurt = is_hero and h.hp < itemuse.HURT_POTION_FRAC * max(1, h.hp_max) and shop_tier >= 2
                        walk_r = itemuse.SHOP_HURT_WALK_R if hurt else itemuse.SHOP_WALK_R
                        # 09-22 晚 后期带卷轴出门（itemuse.REGEN_GATHER_R 上面那段）：三本以后英雄在自家大厅边上，带着部队也去补货、卷轴备 2 张
                        late = shop_tier >= 3
                        # 09-22 晚：卷轴任何时候都备两张（itemuse.SREG_CARRY）—— 在自家大厅边上带着部队也去补，不再只限三本以后
                        at_home = any(u.type in ("htow", "hkee", "hcas") and itemuse.dist(u, h) <= itemuse.LATE_SHOP_HOME_R
                                      for u in snap.mine)
                        gold_min = itemuse.PRICE["phea"] + itemuse.REGEN_BUY_FLOOR if hurt else itemuse.WALK_GOLD_MIN
                        if d_v > itemuse.SHOP_REACH:
                            if not is_hero:
                                continue                    # 地面兵只顺路在店边代购，不从队伍里远距离抽走
                            shop_tally["英雄不在藏宝室边上"] += 1
                            # 09-17：在家附近、没活、没敌人、钱够、缺药 -> 走过去（到了由上面这段下单）
                            #   014259 首局这条一次没触发、只有总计数「不在边上」—— 分不清卡在哪，先按出口分开数
                            army_w = itemuse.army_with(h, snap.mine, SHOP_FOOD)
                            army_ok = hurt or at_home or army_w < itemuse.WALK_ARMY_MAX or d_v <= SHOP_ESCORTED_SHORT
                            if d_v > walk_r:
                                shop_tally["不走·离藏宝室太远"] += 1
                            elif h.addr in busy:
                                shop_tally["不走·在捡东西"] += 1
                            elif not army_ok:
                                shop_tally["不走·正带着部队"] += 1
                            elif snap.gold < gold_min:
                                shop_tally["不走·钱不够" + ("（残血急买）" if hurt else "")] += 1
                            elif any(f.hp > 0 and f.owner <= 12 and itemuse.dist(f, h) <= itemuse.ENEMY_NEAR for f in snap.foes):
                                shop_tally["不走·附近有敌人"] += 1
                            if (d_v <= walk_r and h.addr not in busy and army_ok
                                    and _now_shop >= shop_walk_next.get(h.addr, 0.0)
                                    and snap.gold >= gold_min     # 09-21 原 BUY_GOLD_FLOOR + 100（550）
                                    and not any(f.hp > 0 and f.owner <= 12 and itemuse.dist(f, h) <= itemuse.ENEMY_NEAR for f in snap.foes)):
                                shop_walk_next[h.addr] = _now_shop + (8.0 if hurt else 20.0)
                                bag_w = ses.cmd.inventory(h.addr)
                                want_w = itemuse.shop_wish(bag_w, snap.gold, want=itemuse.shop_want(h.type, late), tier=shop_tier,
                                                           hurt=hurt)
                                if hurt and want_w == "phea":
                                    shop_tally["残血急买血瓶·走过去"] += 1
                                if not want_w:
                                    shop_tally["在家但不缺"] += 1
                                elif not claims.take(h.addr, walk_lease(d_v), prio=PRIO["pickup"]):
                                    shop_tally["走去藏宝室·抢不到指挥权"] += 1
                                else:
                                    ok_w = ses.cmd.move_to(h.addr, v.x, v.y)
                                    shop_tally[f"走去藏宝室·{'ok' if ok_w else '失败'}"] += 1
                                    note(f"商店：{h.type} 离藏宝室 {int(d_v)}、缺 {want_w}（金 {snap.gold}）-> 走过去 {'ok' if ok_w else '失败'}")
                            continue
                        # 09-17：走到藏宝室边上了 —— 走路那把租约（walk_lease，最长 20 秒）当场还掉，买东西本来就不用它。
                        #   原来挂满才过期，英雄站在藏宝室边上时策略层的令全被让出（形状同上面「捡到了不还指挥权」）。
                        if h.addr not in busy:
                            claims.give_back(h.addr)
                        if any(f.hp > 0 and f.owner <= 12 and itemuse.dist(f, h) <= itemuse.ENEMY_NEAR for f in snap.foes):
                            shop_tally["附近有敌人"] += 1
                            continue
                        bag = carrier_bags.get(h.addr) if not is_hero else ses.cmd.inventory(h.addr)
                        team_has_sreg = team_has_sreg or bool(bag and 'sreg' in bag)
                        injured = [u for u in snap.mine if SHOP_FOOD.get(u.type, 0) > 0 and u.type not in itemuse.WORKER_TYPES]
                        recovery_due = itemuse.regen_worth(h, injured, [f for f in snap.foes if f.owner <= 12])[0]
                        if not is_hero and not recovery_due:
                            shop_tally["背包兵·伤兵未到卷轴门槛"] += 1
                            continue
                        want = itemuse.shop_want(h.type, late) if is_hero else (("sreg", 1),)   # 09-21 施法英雄多备魔法药水
                        code = itemuse.shop_wish(bag, snap.gold, want=want, tier=shop_tier, recovery=recovery_due, hurt=hurt)
                        if code == "sreg" and team_has_sreg and not is_hero:  # 英雄各带 SREG_CARRY 张；只有背包兵看团队有没有
                            shop_tally["团队已有恢复卷轴"] += 1
                            continue
                        if not code:
                            # 09-22 背包满时考虑扔一件到地上腾位置 —— 先把「背包满了」单独数出来，看它多常见
                            full = itemuse.bag_full_missing(bag, want)
                            shop_tally[f"背包满了·缺{full}" if full else "不缺/钱不够"] += 1
                            continue
                        if _now_shop < shop_refused.get((v.addr, code), 0.0):
                            shop_tally[f"{code}·被拒退避中"] += 1
                            continue
                        ok = ses.cmd.buy_item(v.addr, itemuse.rawcode(code))
                        shop_tally[f"{code}·{'下单' if ok else '被拒'}"] += 1
                        if not ok:
                            # ★ 09-23 体检：买恢复卷轴 159 次被拒 81 次（原因码 17 / 88），每几秒又试 —— 按回执里的原因码退避
                            rc = getattr(getattr(ses.cmd, "last_receipt", None), "verdict", None)
                            shop_refused[(v.addr, code)] = _now_shop + SHOP_REFUSE_BACKOFF.get(rc, 20.0)
                            shop_tally[f"{code}·被拒·原因码{rc}"] += 1
                        if ok:
                            shop_pending[h.addr] = (code, bag.count(code), _now_shop)
                            team_has_sreg = team_has_sreg or code == "sreg"
                        note(f"商店：给 {h.type} 买 {code}（离藏宝室 {int(d_v)}，金 {snap.gold}，背包 {bag}）-> {'下单' if ok else '被拒（前置/库存/钱）'}")
                        break
            # 文件桥与claims同时遵守保命/孤军锁，避免solo比claims多出的2秒被打断。
            protected = {}
            for filename in ("hurt.json", "solo.json"):
                protected.update(read_leases(os.path.join(os.path.dirname(PICKF), filename),
                                             ses.pid, snap.epoch, snap.mine))
            heroes = [h for h in snap.heroes
                      if h.addr not in protected
                      and (h.hp_max <= 0 or h.hp >= h.hp_max * HP_FLOOR)]
            # 残血/被保命锁住、但周围安全的英雄：只许捡脚边 SAFE_PICK_R 以内的（见 SAFE_R 上面的注释）
            near_only = {h.addr for h in snap.heroes if h not in heroes and safe_around(h, snap.foes)}
            heroes = heroes + [h for h in snap.heroes if h.addr in near_only]
            if not heroes:
                # ★ 09-16 这里原来是**静默** continue，而心跳在下面很远、这条出口之后 ——
                #   英雄一直不可用时本模块一行都不写。161115 那局大法师从拍 16 活到 121、
                #   打野清空 5 次，本模块接上 War3 之后 0 行输出、0 次捡东西；
                #   同一份快照的 mod_combat 却写了 72 行、清清楚楚看到 Hmkg。
                #   英雄在对局里被打死，本模块却查不到任何日志 —— 确实没有。
                #   分不清"没英雄 / 被 hurt·solo 租约锁 / 血量不到 HP_FLOOR"，先把原因数出来。
                if not snap.heroes:
                    idle_why["没有英雄"] += 1
                else:
                    for h in snap.heroes:
                        if h.addr in protected:
                            idle_why[f"{h.type}被租约锁"] += 1
                        elif h.hp_max > 0 and h.hp < h.hp_max * HP_FLOOR:
                            idle_why[f"{h.type}血量<{int(HP_FLOOR * 100)}%"] += 1
                _now = time.monotonic()
                if _now - last_idle_hb >= 60.0:
                    last_idle_hb = _now
                    note("心跳（没派人去捡）："
                         + ("、".join(f"{k}×{v}" for k, v in idle_why.most_common()) or "无")
                         + f"｜上次扫到地上物品 {len(items)} 件")
                    idle_why.clear()
                exits["没有可用英雄"] += 1
                time.sleep(TICK)
                continue

            now = time.monotonic()
            if now - last_scan >= ITEM_SCAN:
                last_scan = now
                _t0 = time.monotonic()
                _stage("扫描地上物品")
                scanned = ses.cmd.items_on_ground()
                if "不完整" in (getattr(ses.cmd, "items_scan_error", None) or ""):
                    # ★ 09-16 读漏的物品会被下面当成"从地上消失 = 捡到了"。扫描不完整就沿用上一轮的物品表，
                    #   这一轮不推断谁被捡走。（当初引的「17:12:15 捡到 tst2 是误报」判断错了 ——
                    #   那次是真捡到，是用掉的书留下的死对象又被扫出来；现在 w3cmd 按血量剔掉了。）
                    exits["扫描不完整·沿用上一轮"] += 1
                else:
                    # 09-15：扫描里混着背包里的东西 —— 剔掉有持有者的（见 drop_owned）
                    _stage("逐件查持有者")
                    items, bag_last = drop_owned(scanned, ses.cmd.item_owned)
                    dead_last = getattr(ses.cmd, "items_dead", 0) or 0
                    publish_ground(items, now, ses.pid, snap.epoch)
                _ms = (time.monotonic() - _t0) * 1000.0
                if _ms > slow["ms"]:
                    slow["ms"] = _ms
                    slow["where"] = "扫描物品+查持有者"
                _stage("派人")

            # 已经捡到的东西会从地面消失；下过令但还没到手的，给它 LEASE 秒
            # ⚠ 这一段要在 `if not items: continue` **之前**：最后一件被捡走后
            #   items 就空了，原来直接 continue，busy 永远解不开，也永远记不到"捡到"。
            live = {it[0] for it in items}
            for a in list(taken):
                if a not in live or now - taken[a] > LEASE_MAX:
                    taken.pop(a, None)
            # 英雄的任务：东西没了（捡到了）或者超时了才解开
            for haddr, (iaddr, at, lease) in list(busy.items()):
                if iaddr not in live:
                    busy.pop(haddr, None)
                    fails.pop(iaddr, None)
                    # ★★ 09-17 捡到了就**立刻还指挥权**。原来只清 busy/pickup.json，w3claim 的租约挂满 walk_lease（6~20 秒）才自己过期，
                    #   策略层这段时间下给英雄的令全被「让出」—— 表现为英雄打完野后原地停顿约 5~10 秒。
                    #   inst1 211228：21:14:07 去捡 tdex、21:14:08 捡到，策略层 21:14:12/16 让出、21:14:20 才下得进去；
                    #   21:14:26 去捡 gcel、21:14:27 捡到，21:14:31/34 让出、21:14:38 才下得进去（lesson.claimYield scavenge70x8）。
                    claims.give_back(haddr)
                    # ★ 成功也要记一笔。以前只记"去捡"和"捡不起来"，
                    #   "捡到了"和"没捡到"从日志上分不开 —— 捡装备有没有用是不可答的。
                    note(f"捡到：{names.get(iaddr, '?')}（用 {orders.pop(iaddr, '?')}，"
                         f"下令后 {now - at:.1f} 秒）")
                elif now - at > lease:
                    busy.pop(haddr, None)
                    # ★★ 09-08 这里原来只写一行日志就完了 —— **不计失败、不放弃**。
                    #
                    #   现场（16:31 那一分钟，一件 stwp）：
                    #       没捡到：stwp 6 秒后还在地上
                    #       Hamg 去捡 stwp（距离 508）
                    #       没捡到：stwp 6 秒后还在地上
                    #       Hamg 去捡 stwp（距离 508）      <- 距离一模一样，人没动
                    #   ……连着十几轮，一整局。最近 12 局：去捡 64.4 次，
                    #   **捡到只有 3.3、没捡到 60.6**。
                    #
                    #   而这个死循环的代价远不止少一件装备：捡拾租约一直挂着，
                    #   策略层的 W3-IsPickingUp 就一直为真，英雄被划进 flee ——
                    #   **整局不打野、每 3.2 秒被下一次回家令**。
                    #   16:30:31 和 16:31:31 的"不打野：英雄残血/撤退中"
                    #   和这个循环在同一分钟。对局里看到的"英雄一直待在家里"
                    #   根因就在这儿：不是英雄不想去，是它被一件永远捡不到的
                    #   东西占着。
                    #
                    #   判据用**有没有走近**，不用次数或时长（原则：
                    #   能用条件就别用时长）。真在赶路的允许一直试；
                    #   原地不动的试三次就本局拉黑。
                    hero = next((u for u in snap.mine if u.addr == haddr), None)
                    here = next((x for x in items if x[0] == iaddr), None)
                    moved = None
                    if hero is not None and here is not None:
                        moved = sent_at.get(iaddr, 1e9) - dist(hero, here)
                    if moved is not None and moved < 100.0:
                        stuck[iaddr] = stuck.get(iaddr, 0) + 1
                    else:
                        stuck.pop(iaddr, None)
                    note(f"没捡到：{names.get(iaddr, '?')} {lease:.0f} 秒后还在地上"
                         f"（用 {orders.get(iaddr, '?')}，这 {lease:.0f} 秒走近了 "
                         f"{'?' if moved is None else int(moved)}）")
                    # ★★ 09-08 第二种死循环：**走近了也会白走**。
                    #   上面那条"连三次一步没走近"只抓得住原地不动的。
                    #   实测 31 次没捡到里 7 次（22%）带着"走近了≥100" ——
                    #   而那会把连败计数清零，于是 texp 被下了 8 次令、
                    #   一次没成，却永远攒不满三次连败。
                    #   （现场还见过"这 6 秒走近了 -2742"：英雄被别人拽走了，
                    #     下一轮又派回来，来回拉锯。）
                    #   所以再加一条**总次数**的闸：走近不走近都算一次。
                    #   6 次配上按路程加长的租约≈两分钟，真要走的路早够了。
                    # ★★★★ 近距离失败**一次就够**：出发时就站在上面，
                    #   "没走近"是必然的，再试两次只是把 18 秒烧掉。
                    #   现场：stwp 距离 0 试三次全失败，每次 6 秒。
                    if (sent_at.get(iaddr, 1e9) <= NEAR_DIST
                            and iaddr not in ignore):
                        near_fail[iaddr] = near_fail.get(iaddr, 0) + 1
                        if near_fail[iaddr] >= NEAR_FAIL_MAX:
                            ignore.add(iaddr)
                            stuck.pop(iaddr, None)
                            taken.pop(iaddr, None)
                            # ★★ 跨物品的连败才是"背包满"的信号 ——
                            #   一件捡不起来可能是那件的事，连着好几件都捡不起来
                            #   就是这个英雄的事。
                            if iaddr not in full_seen.get(haddr, set()):
                                full_seen.setdefault(haddr, set()).add(iaddr)
                            note(f"放弃 {names.get(iaddr, '?')}：出发时距离 "
                                 f"{int(sent_at.get(iaddr, -1))} 就在脚边还是没捡到 —— "
                                 f"走路不是问题，重试也不会变（本局不再碰它）")
                            if len(full_seen.get(haddr, set())) >= FULL_STREAK:
                                full_until[haddr] = now + FULL_COOLDOWN
                                full_seen[haddr] = set()
                                note(f"{names.get(iaddr,'?')} 之后：这个英雄连着 "
                                     f"{FULL_STREAK} 件在脚边都捡不起来 —— **多半背包满了**，"
                                     f"{FULL_COOLDOWN:.0f} 秒内不再派它去捡，先去打野")
                    elif attempts.get(iaddr, 0) >= 6 and iaddr not in ignore:
                        ignore.add(iaddr)
                        stuck.pop(iaddr, None)
                        taken.pop(iaddr, None)
                        note(f"放弃 {names.get(iaddr, '?')}：下了 {attempts[iaddr]} 次令都没到手"
                             f"（中间走近过，所以连败计数一直被清零）——**本局不再碰它**")
                    elif stuck.get(iaddr, 0) >= 3:
                        ignore.add(iaddr)
                        stuck.pop(iaddr, None)
                        taken.pop(iaddr, None)
                        note(f"放弃 {names.get(iaddr, '?')}：连试三次英雄一步没走近"
                             f"（多半在商店里或走不过去）——**本局不再碰它**，"
                             f"再占着捡拾租约英雄就整局不打野了")
                else:
                    # ★ 09-16 租约期内的回执：英雄被别的指令拽走就补令（见 reassert_step）
                    hero = next((u for u in snap.mine if u.addr == haddr), None)
                    here = next((x for x in items if x[0] == iaddr), None)
                    if hero is not None and here is not None:
                        dnow = math.hypot(hero.x - here[2], hero.y - here[3])
                        prog = progress.setdefault((haddr, iaddr), [now, dnow])
                        if reassert_step(prog, now, dnow, reasserts.get(iaddr, 0)):
                            _stage("补令")
                            if ses.cmd.get_item(haddr, iaddr, here[2], here[3]):
                                reasserts[iaddr] = reasserts.get(iaddr, 0) + 1
                                prog[0], prog[1] = now, dnow
                                tally["补令"] += 1
                                note(f"补令：{names.get(iaddr, '?')} —— {hero.type} "
                                     f"{REASSERT_AFTER:.1f} 秒没走近（离它 {int(dnow)}），"
                                     f"多半被别的指令覆盖；第 {reasserts[iaddr]} 次补")
            if busy or pick_pub:
                publish_pickup(busy, now, ses.pid, snap.epoch, snap.mine)
                pick_pub = bool(busy)

            if now - last_hb >= 60.0:
                last_hb = now
                # ⚠ 拒绝理由**按东西记，不按 tick 累加**。
                #   第一版是每 200ms 给每件地上的东西记一笔，于是打出
                #   "远且无人掩护 3076" —— 那个数是 tick 频率乘地上件数，
                #   和"有多少东西够不着"毫无关系（地上只有 15 件）。
                #   经验：量之前先问这个数是不是在量你以为的东西。
                live_state = {a: r for a, r in state.items()
                              if a in {x[0] for x in items}}
                parts = [f"{k} {v}" for k, v in tally.most_common()]
                parts += [f"{k} {v} 件" for k, v in
                          collections.Counter(live_state.values()).most_common()]
                if parts:
                    note("这一分钟：" + "，".join(parts) +
                         f"（地上 {len(items)} 件，背包里 {bag_last} 件、用掉的书 {dead_last} 件已剔除，"
                         f"能用的英雄 {len(heroes)} 个）")
                tally.clear()
                state.clear()

            if not items:
                exits["地上没东西"] += 1
                time.sleep(TICK)
                continue

            for it in items:
                if it[0] in ignore:
                    continue
                if it[0] in taken:
                    continue                      # 已经派人去了，别重复下令
                # 只挑**手上没活**的英雄。正在去拿别的东西的不许改派 ——
                # 改派会把它已经走过的那段路作废。
                # ★★ 判过"背包满"的英雄，冷却期内不派它去捡 ——
                #   这正是对局里看到的"一个人在野外待很久"：地上 22 件，
                #   它会一件一件试过去，而每一件都捡不起来。
                free = [h for h in heroes
                        if h.addr not in busy and now >= full_until.get(h.addr, 0.0)
                        and (h.addr not in near_only or dist(h, it) <= SAFE_PICK_R)]
                if not free:
                    break
                # ★ 09-16 捡属性书要看是否对口：法系英雄吃智力书、力量型英雄吃力量书。
                #   原来一律派离得最近的 —— 智力书常被山丘之王吃掉。现在按 itemvalue.pick：物品效果 × 主属性/近战/等级，
                #   扣掉走路成本（多走 800 = 少 1 分，主属性对口的书值得多走约 800，不值得横穿地图）。
                #   等级只在经验书/能量之书/重生这几类才问引擎（取英雄等级函数，约 10ms）。
                #   ⚠ 先筛够得着的（顺路 REACH 内，或有掩护 ESCORT_REACH 内）再挑：演练台 5 号实例，力量书离山丘 611、
                #   离大法师 383 —— 按价值挑中山丘，山丘过不了下面「远且无人掩护」那道闸，而原来不会换人，书就一直没人捡。
                esc_here = escort_at(it, snap.mine)
                reachable = [u for u in free if dist(u, it) <= REACH
                             or (esc_here >= ESCORT_MIN and dist(u, it) <= ESCORT_REACH)
                             or dist(u, it) <= CLEAR_REACH] or free
                h, _pick_score, pick_why = itemvalue.pick(
                    reachable, it[1], lambda u: dist(u, it), level_of=lambda u: ses.cmd.hero_level(u.addr))
                nearest = min(reachable, key=lambda u: dist(u, it))
                if h is not nearest:
                    tally["不派最近的"] += 1
                d = dist(h, it)
                # ★ 两种资格，任一成立就去：
                #   「顺路」—— 就在英雄脚边（原来唯一的一条）
                #   「有掩护」—— 东西旁边站着我们自己的部队，而且路上没埋伏
                why = "顺路"
                if d > REACH:
                    esc = escort_at(it, snap.mine)
                    # ★ 09-21（3-042 7 分钟）大法师身边掉了一本书一直没捡，拖到 10 分 30 秒路过才捡；捡东西的优先级应当更高。
                    #   日志：「远且无人掩护」—— 书在 REACH 外、旁边凑不齐 3 个小兵就不去；23:09:57 凑够 4 个小兵掩护才去，1.6 秒捡到。
                    #   路上没埋伏、东西周围 SAFE_R 内没有敌人/野怪时，不需要人陪（第三种资格「路上干净」）。
                    clear = (d <= CLEAR_REACH and ambush_on_route(h, it, snap.foes, snap.mine) is None
                             and not any(f.hp > 0 and f.owner <= 12 and can_attack(f)
                                         and math.hypot(f.x - it[2], f.y - it[3]) <= SAFE_R
                                         for f in snap.foes))
                    if esc < ESCORT_MIN and not clear:
                        state[it[0]] = "远且无人掩护"
                        continue
                    if esc < ESCORT_MIN:
                        why = "路上干净"
                        tally["路上干净"] += 1
                    elif d > ESCORT_REACH:
                        state[it[0]] = "有掩护但太远"
                        continue
                    amb = None if clear else ambush_on_route(h, it, snap.foes, snap.mine)
                    if amb:
                        state[it[0]] = "路上有埋伏"
                        if now - last_amb.get(it[0], 0.0) > 10.0:
                            last_amb[it[0]] = now
                            note(f"不去捡 {it[1]}：路上 {amb[0].type} 离路线 "
                                 f"{int(amb[1])}，我方只有 {amb[2]} 人缠着它"
                                 f"（距离 {int(d)}，旁边有 {esc} 个自己人）")
                        continue
                    if esc >= ESCORT_MIN:
                        why = f"小兵掩护×{esc}"
                # ★ 抢到指挥权才下令。抢不到说明战斗模块正在用它 ——
                #   那就让它打完，装备不会跑。
                lease = walk_lease(d)
                if not claims.take(h.addr, lease, prio=PRIO["pickup"]):
                    continue
                # ★ 埋数据：getitem（精确命令 0x000D000D）对英雄多半是**静默失败** ——
                #   老接口 context 对战斗单位返回 0，攻击那条路当年就因此
                #   "发出去了但一动不动"。历史 53 次"去捡"里 30 次
                #   在 12 秒内对同一件东西重下（英雄没动），距离 ≤200 也一样。
                #   两种命令轮着用：第一次 getitem，同一件东西再来时用 smart（右键），
                #   成功/失败都记下用的是哪种 —— 下一轮数据自己说话。
                # 09-07 晚定论：捡装备只有 smart 生效，getitem 不生效（受控实验，
                # 用 IsItemOwned 问引擎要真结果）。不再两种轮着试。
                n_try = attempts.get(it[0], 0)
                use_smart = True
                ok = ses.cmd.get_item(h.addr, it[0], it[2], it[3])
                if ok:
                    attempts[it[0]] = n_try + 1
                    taken[it[0]] = now
                    busy[h.addr] = (it[0], now, lease)   # 这个英雄认准这一件，不再改派
                    sent_at[it[0]] = d             # 下令那一刻的距离，用来判"有没有走近"
                    names[it[0]] = it[1]
                    orders[it[0]] = "smart" if use_smart else "getitem"
                    pick_pub = True
                    publish_pickup(busy, now, ses.pid, snap.epoch, snap.mine)      # 战略拍这几秒不碰它
                    tally[why] += 1
                    note(f"{h.type} 去捡 {it[1]}（{why}，距离 {int(d)}，给 {lease:.0f} 秒，"
                         f"用 {orders[it[0]]}，快照延迟 {snap.age_ms}ms）｜给它：{pick_why}"
                         + ("" if h is nearest else f"（最近的是 {nearest.type}）"))
                else:
                    # ⚠ 失败必须留痕。
                    #
                    # 这个模块原来只在**成功**时写日志，于是"没东西可捡"和
                    # "想捡但捡不了"长得一模一样 —— 而这两者要改的地方
                    # 完全不同。实测抓到过一次：英雄脚下距离 0 有一件 stwp，
                    # 模块一声不吭什么也没发生（多半那件其实已经在背包里，
                    # 动作 46 会把身上的也列出来）。
                    # 这是这个项目的第 35 条坑，今天第三次踩。
                    claims.give_back(h.addr)
                    fails[it[0]] = fails.get(it[0], 0) + 1
                    if fails[it[0]] in (1, 5, 20):
                        note(f"捡不起来：{it[1]} 距 {h.type} {int(d)}"
                             f"（第 {fails[it[0]]} 次；多半已在背包里，或者背包满了）")
                    if fails[it[0]] >= 3:
                        taken[it[0]] = now         # 连试三次不成就先搁置，别每轮都撞
                    if fails[it[0]] >= 5 and d < 150:
                        # ★ 就在英雄身上、又反复捡不起来 —— 它**已经在背包里**了。
                        #   动作 46 会把背包里的东西也一起列出来，
                        #   而"在地上"和"在背包里"从坐标上分不出来（都在英雄脚下）。
                        #   分不出来就用**行为**来分：捡不动的那个就是已经拿到的。
                        ignore.add(it[0])
                        note(f"{it[1]} 应该已经在 {h.type} 背包里了，不再尝试")
                break                              # 一轮只派一次，别一次全指出去
            time.sleep(TICK)
        except KeyboardInterrupt:
            publish_pickup({}, time.monotonic(), ses.pid)
            note("模块退出")
            return 0
        except Exception as exc:                   # noqa: BLE001
            # ⚠ 这个循环**绝不能死**：它是独立进程，死了没人知道。
            #   出错就记一笔、歇一下、继续 —— 挂机几小时的前提是它扛得住意外。
            note(f"出错（继续跑）：{exc!r}")
            exits["出错"] += 1
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())

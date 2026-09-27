"""mod_fight.py —— 交战期间把**每个单位每一帧在干什么**录下来。

    python mod_fight.py             # 跟着跑，交战时录
    python mod_fight.py --report    # 复盘已经录下来的

对局里看到：和敌方正面交战时我方打得很乱 —— 真正出手的攻击次数远少于敌方，
单位不是在移动就是各打各的，没有高手那样的整体战斗策略。而每个单位的攻击次数、
是否在移动、攻击目标是谁，正是复盘分析最需要的数据。

此前**没有任何统计保存**。查下来是这样：

* 快照（layout 2）里每个单位本来就带 **order（当前订单号）、
  task_handle（任务指向谁）、x/y、hp**，100 毫秒一帧，敌我都有。
* 而这些数据**没有任何地方在存**。mod_combat 读它做保命决策，读完就扔；
  对局记录里只有"兵力几个、总血量多少"这种聚合数。
* 于是"我们每秒真正打出去几次攻击""在移动的比例是多少""是不是各打各的"
  这三个问题，**在今天之前是不可回答的**。

这个模块只做一件事：**交战期间把原始帧存下来**，不下任何命令。
（只读 —— 战斗回路在 mod_combat，那里出问题代价是输一局；
  这里出问题最多是少一份日志。）

## 为什么不直接判"在攻击"

攻击的订单号我**没有**从 order-ids.txt 里查到（那张表里没有一条叫 attack 的）。
所以这一版**不猜**：把原始订单号原样录下来，用数据反推 ——
交战期间敌方单位出现最多的那个订单号，配合"对面血在掉"，就是攻击。
今天已经有四次是"照着看起来像的东西归类"然后被推翻的
（迷雾/地形、采空/被打掉、前置/地形、累计价/升级价）。

## 存哪

`build_intel4/fights/fight-<epoch>.jsonl`，一帧一行：

    {"t":12.34,"n":18,"u":[[addr,type,owner,x,y,hp,order,tlo,thi,hpMax,hlo,hhi], ...]}
    （09-16 起多三列：最大血量、单位句柄 lo/hi —— 句柄对得上任务里的目标句柄，以后按它归因"谁在打谁"）
每场仗结束（GAP 秒没接触、再等 TAIL 秒结算）往 `fights/battles.jsonl` 记一条账本（battles.record），
日志一行 battles.line；交战中每 LIVE_SECS 秒一行实时统计（live_line）。

一场 60 秒的仗、40 个单位、150ms 一帧 = 400 行 × 40 条，几 MB，
按对局分文件、只在交战期间录，不会把磁盘写爆。
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()

from w3state import Session
import modlog  # noqa: E402

UNITS_JSON = os.environ.get(
    "WAR3_UNITS_JSON",
    w3paths.UNITS_JSON)


def _footprints():
    """哪些类型是建筑 —— 用游戏数据表里的 foot（占地），不用「动没动过」。

    ⚠ 位移判据在长录像上不可靠：**单位地址会被回收**。一个地址先是移动过的
      步兵、后来变成农场，整场的位移一算就把农场判成了机动单位 ——
      实测最激烈那一帧的「动过的」里有 hhou×3。
      foot 是静态数据、和种族无关，整表 837 条零误判（09-08 核过）。
    """
    try:
        with open(UNITS_JSON, encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return {k for k, v in d.items() if isinstance(v, dict) and (v.get("foot") or 0) > 0}
    except OSError:
        return set()


BUILDINGS = _footprints()

TICK = 0.15
CONTACT = 900.0          # 和 mod_combat 判"接触"用同一个距离，口径要一致
COOL_FRAMES = 20         # 脱离接触后再录 3 秒，好看清追击和撤退
PRE_S = 10.0             # 09-18 接触前预录：没交战时保留最近这么多秒的帧（worldmodel/REQUESTS_TO_AI_SESSION.md #2）
PRE_EVERY = 0.5          #   每这么多秒存一帧（交战中是 TICK 一帧）
# ★ 09-16「谁在打谁」采样。帧里的 tlo/thi 是**任务对象**的句柄（每个单位各一个），不是目标 ——
#   拿它去数"几个人打同一个目标"，53 场大仗双方集火度全是 1.0，量的不是集火。
#   目标要把任务对象解出来（w3cmd.current_task：任务里的目标单位句柄 lo/hi，和帧里第 11/12 列同一种）。
#   一次解析是一次引擎调用（约 10ms），所以不每帧做：交战中每秒一轮，双方接触单位交替取、最多 TARGET_MAX 个、
#   一轮最多花 TARGET_BUDGET 秒。写进 fights/targets-<和帧文件同名>.jsonl（不进帧文件：帧文件的读者都按 "u" 取）。
TARGET_EVERY = 1.0
TARGET_MAX = 30
TARGET_BUDGET = 0.35
# ★ 09-17 改成按任务句柄变化触发（TargetCache），给世界模型要更密的「谁在打谁」（采样频率按性能开销取最优方案）。
#   依据（10 局 targets×fight 帧对照，相邻两次采样 3 秒内）：任务句柄变了 5027 次；**句柄没变的 5107 次里目标也没变 95%**，
#   变了的 260 次几乎全在 1 秒间隔上（同一任务里自动换目标 / 采样和帧没对齐）。
#   所以：每帧（150ms）只重读「任务句柄变了 / 没读过」的接触单位，句柄没变的 TARGET_REFRESH 秒兜底重读；
#   一帧最多花 TARGET_FRAME_BUDGET 秒（一次读约 12ms，走游戏 UI 线程 —— 读多了拖画面）。
#   原来每秒一轮最多 30 个：目标最坏晚 1 秒、世界模型导出只有 12~26% 的单位·帧有目标。
TARGET_FRAME_BUDGET = 0.05
TARGET_REFRESH = 2.0
TARGET_WRITE_EVERY = 0.5
OUTDIR = os.environ.get(
    "WAR3_FIGHT_DIR",
    os.path.join(w3paths.BIN, "fights"))
LOGF = os.environ.get(
    "WAR3_MOD_LOG",
    os.path.join(w3paths.BIN, "ai_run.log"))

# 工人和建筑不参与交战统计：它们的订单常年是采集/建造，会把比例冲淡。
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}


def note(msg: str) -> None:
    try:
        with open(LOGF, "a", encoding="utf-8") as fh:
            fh.write("  [战斗记录] %s\n" % msg)
    except OSError:
        pass
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def fighters(units, owner_is_mine, me):
    """会打仗的单位：不是工人、有血、不是建筑（建筑血上限高但不动）。"""
    out = []
    for u in units:
        if u.hp <= 0 or u.type in WORKERS:
            continue
        if (u.owner == me) != owner_is_mine:
            continue
        # 建筑的 hp_max 通常很高而且没有订单，这里用"能不能动"近似不了，
        # 所以只按类型首字母大写（英雄）或已知战斗单位放行 —— 宁可多录。
        out.append(u)
    return out


def in_contact(mine, foes):
    for a in mine:
        for b in foes:
            if abs(a.x - b.x) + abs(a.y - b.y) <= CONTACT * 2:
                if math.hypot(a.x - b.x, a.y - b.y) <= CONTACT:
                    return True
    return False


def contact_units(mine, foes):
    """双方各自「身边 CONTACT 内有对方单位」的单位（敌方只算玩家，不算野怪）。"""
    players = [b for b in foes if b.owner < 12]
    cm, cf = [], []
    for a in mine:
        if any(math.hypot(a.x - b.x, a.y - b.y) <= CONTACT for b in players):
            cm.append(a)
    for b in players:
        if any(math.hypot(a.x - b.x, a.y - b.y) <= CONTACT for a in mine):
            cf.append(b)
    return cm, cf


def frame_rows(units) -> list:
    """一帧的单位行（交战帧和预录帧同一个格式）。"""
    return [[u.addr, u.type, u.owner, int(u.x), int(u.y), int(u.hp),
             (u.order if u.order is not None else -1),
             (u.task_handle_lo if u.task_handle_lo is not None else -1),
             (u.task_handle_hi if u.task_handle_hi is not None else -1),
             int(u.hp_max or 0), int(u.handle_lo or 0), int(u.handle_hi or 0)] for u in units]


class PreBuffer:
    """★ 09-18 接触前预录（世界模型数据需求 #2，「胜率模型」路线的数据前提）：
    M1 胜率模型在接触那一刻 AUC 只有 0.61（没比基率好），而出击/支援/撤退都是接触**之前**做的决定 ——
    帧从第一次接触才开始写，接触前谁在赶来、离多远，数据里根本没有。
    没交战时每 PRE_EVERY 秒存一帧、只留最近 PRE_S 秒；一接触就先把这些帧写进帧文件（带 "pre":1，t 相对同一个 t0，可以是负数）。
    battles.segment 只认有接触的帧，这些帧不会被当成打仗。"""

    def __init__(self, keep: float = PRE_S, every: float = PRE_EVERY):
        self.keep, self.every = keep, every
        self.q = collections.deque()
        self.last = -1e9

    def add(self, now: float, rows_fn) -> bool:
        if now - self.last < self.every:
            return False
        self.last = now
        self.q.append((now, rows_fn()))
        while self.q and now - self.q[0][0] > self.keep:
            self.q.popleft()
        return True

    def drain(self, t0: float) -> list[dict]:
        out = [{"t": round(tm - t0, 2), "n": len(rows), "u": rows, "pre": 1} for tm, rows in self.q]
        self.q.clear()
        return out


def interleave(a, b):
    out = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            out.append(a[i])
        if i < len(b):
            out.append(b[i])
    return out


def sample_targets(cmd, mine, foes, limit=TARGET_MAX, budget=TARGET_BUDGET, clock=time.monotonic):
    """-> ([[addr, owner, kind, tlo, thi], ...], 解析了几个, 花了几秒)。

    kind：'T' 对着一个单位（tlo/thi = 目标句柄）/ 'P' 对着一个点 / '-' 没任务或读不到。
    双方交替取，预算用完就停 —— 不让一方把名额吃光。
    """
    cm, cf = contact_units(mine, foes)
    t0 = clock()
    rows, n = [], 0
    for u in interleave(cm, cf)[:limit]:
        if clock() - t0 > budget:
            break
        n += 1
        try:
            task = cmd.current_task(u.addr)
        except Exception:                        # noqa: BLE001
            task = None
        if task and task.get("target"):
            rows.append([u.addr, u.owner, "T", int(task["target"][0]), int(task["target"][1])])
        elif task and task.get("x") is not None:
            rows.append([u.addr, u.owner, "P", -1, -1])
        else:
            rows.append([u.addr, u.owner, "-", -1, -1])
    return rows, n, clock() - t0


class TargetCache:
    """接触单位的攻击目标缓存。键 = 单位地址 + 句柄对（地址会复用）。"""

    def __init__(self, refresh=TARGET_REFRESH):
        self.refresh = refresh
        self.ent = {}          # addr -> {"ident", "task", "row", "at"}
        self.reads = 0
        self.changed_reads = 0

    @staticmethod
    def _task(u):
        return (u.task_handle_lo, u.task_handle_hi)

    @staticmethod
    def _ident(u):
        return (getattr(u, "handle_lo", 0), getattr(u, "handle_hi", 0))

    def due(self, units, now):
        """要重读的单位，任务句柄变了/没读过的在前，其余按上次读的时刻从早到晚（到了 refresh 才算）。"""
        changed, stale = [], []
        for u in units:
            e = self.ent.get(u.addr)
            if e is None or e["ident"] != self._ident(u) or e["task"] != self._task(u):
                changed.append(u)
            elif now - e["at"] >= self.refresh:
                stale.append((e["at"], u))
        stale.sort(key=lambda p: p[0])
        return changed, [u for _, u in stale]

    def step(self, cmd, units, now, budget=TARGET_FRAME_BUDGET, clock=time.monotonic):
        """读到期的那些，预算用完就停。-> 这一帧读了几个。"""
        changed, stale = self.due(units, now)
        t0 = clock()
        n = 0
        for u in changed + stale:
            if n and clock() - t0 > budget:
                break
            try:
                task = cmd.current_task(u.addr)
            except Exception:                        # noqa: BLE001
                task = None
            if task and task.get("target"):
                row = [u.addr, u.owner, "T", int(task["target"][0]), int(task["target"][1])]
            elif task and task.get("x") is not None:
                row = [u.addr, u.owner, "P", -1, -1]
            else:
                row = [u.addr, u.owner, "-", -1, -1]
            if u in changed:
                self.changed_reads += 1
            self.ent[u.addr] = {"ident": self._ident(u), "task": self._task(u), "row": row, "at": now}
            n += 1
        self.reads += n
        return n

    def rows(self, units, now, max_age=None):
        """这些单位当前缓存的目标行（没读过 / 太旧的不给）。"""
        max_age = self.refresh * 2 if max_age is None else max_age
        out = []
        for u in units:
            e = self.ent.get(u.addr)
            if e is not None and e["ident"] == self._ident(u) and now - e["at"] <= max_age:
                out.append(e["row"])
        return out


class FightBook:
    """★★ 09-16 实时战斗统计 + 每场仗结束记账（口径全在 battles.curve / battles.record，这里只管什么时候算）。

    要求：交战中实时统计所有参战单位的各项信息，战斗结束后按这些记录做一次复盘。
    —— 之前只录帧、不算：交战中没有任何一行统计，打完也没有任何一条账本；
    决定性那场仗为什么输，是 09-16 手工逐秒拆帧才看出来的（第 8 秒起输出塌掉、被拉的全死、接触人·秒差 2.3 倍）。
    这里把同一套口径搬进模块：交战中每 LIVE_SECS 秒一行，一场结束（GAP 秒没接触 + TAIL 秒结算）记一条。
    只读，不下任何命令。
    """

    # ★ 09-16 上线第一局就暴露：录帧的"接触"含野怪，而账本只算打玩家 —— 打野时刷出
    #   「交战中（开打 282s）：接触 我 0:敌 0｜挨打 1536」（挨打是被野怪咬的），缓冲也从第一次打野起一直攒着不结算。
    #   所以这里的时机全按**和敌方玩家的接触**（battles.contact_sets，和账本同一个口径）；
    #   还没碰到玩家时缓冲只留 PREROLL 秒。
    PREROLL = 2.0

    def __init__(self, outdir):
        self.outdir = outdir
        self.frames = []          # [(t, rows)]，只留这一场的
        self.first_hot = None     # 这一场第一次和敌方玩家接触
        self.last_hot = None      # 最近一次和敌方玩家接触
        self.last_live = None
        self.wall0 = None
        self.written = 0

    def feed(self, t, rows, hot, me):
        import battles as B
        self.frames.append((t, rows))
        cm, _ = B.contact_sets(rows, me)
        if cm:
            self.last_hot = t
            if self.first_hot is None:
                self.first_hot = self.last_live = t
        elif self.last_hot is None:
            while self.frames and self.frames[0][0] < t - self.PREROLL:
                self.frames.pop(0)

    def live_line(self, t, me):
        """交战中（和敌方玩家）每 LIVE_SECS 秒：这几秒的接触人数、打出/挨打、单兵输出。"""
        import battles as B
        if self.last_live is None or self.last_hot is None or t - self.last_live < B.LIVE_SECS:
            return None
        if t - self.last_hot > 1.0:
            return None
        lo = next((i for i, (ft, _) in enumerate(self.frames) if ft >= t - B.LIVE_SECS), 0)
        hi = len(self.frames) - 1
        if hi - lo < 2:
            return None
        self.last_live = t
        c = B.curve(self.frames, lo, hi, me, tail=0)
        cm = max((b[1] for b in c["bins"]), default=0)
        cf = max((b[2] for b in c["bins"]), default=0)
        rm = "-" if c["rateM"] is None else f"{c['rateM']:.0f}"
        rf = "-" if c["rateF"] is None else f"{c['rateF']:.0f}"
        away = "-" if c["awayShare"] is None else f"{100 * c['awayShare']:.0f}%"
        return (f"交战中（开打 {t - self.first_hot:.0f}s）：近 {B.LIVE_SECS:.0f} 秒 接触 我 {cm}:敌 {cf}"
                f"｜打出 {c['dealt']} 挨打 {c['taken']}（单兵每秒 {rm}:{rf}）｜背离 {away}")

    def maybe_close(self, t, me):
        import battles as B
        if not self.frames or self.last_hot is None or t - self.last_hot <= B.GAP + B.TAIL:
            return []
        return self.close(me)

    def close(self, me):
        """结算缓冲里的仗（按 battles.segment 切，只记打玩家的），清空。返回要写日志的行。"""
        import battles as B
        out = []
        frames, self.frames = self.frames, []
        self.first_hot = self.last_hot = self.last_live = None
        if not frames or self.wall0 is None:
            return out
        try:
            pull_rows = None
            try:
                pull_rows = [(at, tx) for at, tx in modlog.read("mod_combat") if "拉出来" in tx]
            except Exception:                                   # noqa: BLE001
                pull_rows = None
            for lo, hi in B.segment(frames, me):
                rec = B.record(frames, lo, hi, me, self.wall0, pull_rows)
                with open(os.path.join(self.outdir, "battles.jsonl"), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.written += 1
                out.append("战斗账本：" + B.line(rec))
        except Exception as exc:                                # noqa: BLE001
            out.append("战斗账本出错（帧照录）：%r" % (exc,))
        return out


def main() -> int:
    modlog.tee_stdout("mod_fight")
    os.makedirs(OUTDIR, exist_ok=True)
    note("模块启动（只读；交战期间按 %.0fms 录原始帧）" % (TICK * 1000))
    ses = Session()
    context = None
    fh = None
    path = None
    t0 = None
    cool = 0
    frames = 0
    tgh = None                 # targets-*.jsonl
    tg_at = 0.0
    tg_rounds = tg_units = 0
    tg_spent = 0.0
    tcache = TargetCache()
    book = FightBook(OUTDIR)
    pre = PreBuffer()
    pre_frames = 0
    while True:
        try:
            snap = ses.read()
            if snap is None or not snap.in_game:
                time.sleep(TICK)
                continue
            cur = (ses.pid, snap.epoch)
            if cur != context:
                if fh:
                    fh.close()
                    note("这一局录了 %d 帧（另有接触前预录 %d 帧）-> %s｜谁在打谁：写 %d 行、读目标 %d 次（任务句柄变了 %d）、读目标共 %.1f 秒（每帧 %.0fms）"
                         % (frames, pre_frames, os.path.basename(path), tg_rounds, tg_units, tcache.changed_reads,
                            tg_spent, 1000.0 * tg_spent / max(1, frames)))
                pre_frames = 0
                if tgh:
                    tgh.close()
                tgh, tg_rounds, tg_units, tg_spent = None, 0, 0, 0.0
                tcache = TargetCache()
                for msg in book.close(snap.me if snap else 0):
                    note(msg)
                context, fh, path, t0, frames, cool = cur, None, None, None, 0, 0
                book = FightBook(OUTDIR)
                pre = PreBuffer()
            mine = fighters(snap.units, True, snap.me)
            foes = fighters(snap.units, False, snap.me)
            # ⚠ 一开始把野怪滤掉了（只留 owner<12 的玩家），
            #   而要查的恰恰是**打野时英雄在不在场** —— 那种仗一帧都没录到。
            #   现在两种都录，靠 owner 区分：<12 是玩家，>=12 是野怪/中立。
            foes = [u for u in foes if u.owner != snap.me]
            hot = bool(mine) and bool(foes) and in_contact(mine, foes)
            if hot:
                cool = COOL_FRAMES
            elif cool > 0:
                cool -= 1
            if not hot and cool <= 0:
                if t0 is not None:
                    for msg in book.maybe_close(round(time.monotonic() - t0, 2), snap.me):
                        note(msg)
                if mine:
                    pre.add(time.monotonic(), lambda: frame_rows(mine + foes))
                time.sleep(TICK)
                continue
            if fh is None:
                path = os.path.join(
                    OUTDIR, "fight-%s-%s.jsonl" % (
                        datetime.now().strftime("%Y%m%d-%H%M%S"), snap.epoch))
                fh = open(path, "w", encoding="utf-8")
                if tgh:
                    tgh.close()
                tgh = open(os.path.join(OUTDIR, "targets-" + os.path.basename(path)[len("fight-"):]),
                           "w", encoding="utf-8")
                t0 = time.monotonic()
                book.wall0 = datetime.now()
                creeps = sum(1 for u in foes if u.owner >= 12)
                note("接触 —— 开始录（我方 %d｜敌方玩家 %d 野怪 %d）"
                     % (len(mine), len(foes) - creeps, creeps))
            for d in pre.drain(t0):                       # 接触前预录的帧先写（t <= 现在）
                fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
                pre_frames += 1
            rows = frame_rows(mine + foes)
            tnow = round(time.monotonic() - t0, 2)
            fh.write(json.dumps({"t": tnow, "n": len(rows), "u": rows},
                                ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            frames += 1
            if hot and ses.cmd is not None and tgh is not None:
                # 09-17：每帧按任务句柄变化读（TargetCache），每 TARGET_WRITE_EVERY 秒把接触单位的当前目标整行写一次（格式不变）
                cm_t, cf_t = contact_units(mine, foes)
                contact = interleave(cm_t, cf_t)
                w0 = time.monotonic()
                tg_n = tcache.step(ses.cmd, contact, w0)
                tg_spent += time.monotonic() - w0
                tg_units += tg_n
                if time.monotonic() - tg_at >= TARGET_WRITE_EVERY:      # 不管这一帧读没读，缓存里的当前目标定时整行写（下游按 1 秒沿用）
                    tg_at = time.monotonic()
                    tg_rows = tcache.rows(contact, w0)
                    if tg_rows:
                        tgh.write(json.dumps({"t": tnow, "tg": tg_rows}, separators=(",", ":")) + "\n")
                        tgh.flush()
                    tg_rounds += 1
            book.feed(tnow, rows, hot, snap.me)
            msg = book.live_line(tnow, snap.me)
            if msg:
                note(msg)
            for msg in book.maybe_close(tnow, snap.me):     # 玩家那场打完了、还在打野也要结算
                note(msg)
            time.sleep(TICK)
        except KeyboardInterrupt:
            if fh:
                fh.close()
            note("模块退出")
            return 0
        except Exception as exc:                      # noqa: BLE001
            note("出错（继续跑）：%r" % (exc,))
            time.sleep(1.0)


def report(limit: int = 5) -> int:
    """把录下来的帧翻成能回答模块开头那三个问题的数。

    ⚠ **先把建筑滤掉**。第一版没滤，我方"无订单 96%"里有一大半是农舍和兵营 ——
      建筑永远没有订单。判据用数据本身定：整场**移动过 >128** 的才算作战单位
      （建筑一动不动）。不预设类型表，换种族也成立。

    ⚠ 攻击的订单号**不是猜的**：order-ids.txt 里没有 attack 这一条，
      是从数据认出来的 —— 交战期间敌方 58% 的帧都是 0x000D000F，
      而我方只有 6%，且它出现时对面在掉血。
    """
    files = sorted(glob.glob(os.path.join(OUTDIR, "fight-*.jsonl")))[-limit:]
    if not files:
        print("还没有录到过交战（%s 是空的）" % OUTDIR)
        return 0
    ATTACK = 0x000D000F
    MOVE = (0x000D0003, 0x000D0012)   # 09-16：原来含 0x000D0004，那是 stop（order-ids.txt:27）；move 实测 0x000D0012
    for f in files:
        frames = []
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                frames.append(json.loads(line))
            except Exception:
                continue
        if not frames or not frames[0]["u"]:
            continue
        me = frames[0]["u"][0][2]
        pos = collections.defaultdict(list)
        for d in frames:
            for addr, typ, owner, x, y, hp, order, tlo, thi, *_ in d["u"]:
                pos[addr].append((x, y))
        # 建筑用 foot 判（见 _footprints）；读不到表就退回旧的位移判据
        bt = {}
        for d0 in frames:
            for a, typ, ow, x, y, hp, o, tl, th, *_ in d0["u"]:
                bt[a] = typ
        if BUILDINGS:
            mobile = {a for a in pos if bt.get(a) not in BUILDINGS}
        else:
            mobile = {a for a, ps in pos.items()
                      if max(abs(px - ps[0][0]) + abs(py - ps[0][1]) for px, py in ps) > 128}
        creepN = 0
        stat = {True: collections.Counter(), False: collections.Counter()}
        tgt = {True: [], False: []}
        cnt = {True: [], False: []}
        # ★★★★ 09-08 深夜 **「我方均 18.4 个」不是「到场 18 个」。**
        #
        #   我拿这个数得出过"我们带一半的兵去打人家的全部"，**读错了** ——
        #   它是"这段录像里我方活着的机动单位均值"，和"谁站在战团里"无关。
        #   按"身边 900 内有没有敌人"重算，两边**同时在接触的都只有 2 个人**：
        #       我方 机动 16.1~18.4，在打的 1.7~2.0（9~12%）
        #       敌方 机动 31.9~40.1，在打的 2.0~2.1（5~6%）
        #   也就是说这些录像大半是**零星摩擦**（打野、小接触），不是主力会战 ——
        #   接触判据只要一对单位靠近 900 就开录。
        #
        #   拿摩擦的数去谈会战，等于在回答一个没人问的问题。
        #   所以先把两者分开：同时接触 >= 6 个才算一场会战。
        contact = []
        for d in frames:
            # ⚠ 「敌方」必须只算**敌方玩家**。owner >= 12 是中立/野怪，
            #   而快照里那一片 ngol（金矿）、nftb/ngna/nfsp（野怪）全在这个区间 ——
            #   实测最激烈那一帧的"敌方 123 个"里，真正的敌方玩家兵只有 18。
            #   今天已经因为同一个形状修过一次（v172 的 peakEnemyArmy）。
            fo = [r for r in d["u"] if 0 <= r[2] < 12 and r[2] != me and r[5] > 0 and r[0] in mobile]
            mi = [r for r in d["u"] if r[2] == me and r[5] > 0 and r[0] in mobile]
            n = 0
            for r in mi:
                if any(math.hypot(r[3] - q[3], r[4] - q[4]) < 900 for q in fo):
                    n += 1
            contact.append(n)
        peak = max(contact) if contact else 0
        hot = sum(1 for n in contact if n >= 6)
        kind = "会战" if peak >= 6 else "零星摩擦"
        # ★★ 下面这些比例原来按**全部帧**平均，而 87% 的帧根本没在打 ——
        #   安静的帧把"攻击令占比""无订单占比"全稀释了。
        #   我已经因此连着读错两次（"带一半的兵"、"只有 2 个人在接触"）。
        #   会战文件只按**会战帧**（同时接触 >= 6 人）算，摩擦文件照旧全算。
        for fi, d in enumerate(frames):
            if peak >= 6 and contact[fi] < 6:
                continue
            per = {True: set(), False: set()}
            n = {True: 0, False: 0}
            for addr, typ, owner, x, y, hp, order, tlo, thi, *_ in d["u"]:
                if addr not in mobile or hp <= 0:
                    continue
                if owner != me and not (0 <= owner < 12):
                    creepN += 1
                    continue          # 中立/野怪不进敌我统计
                k = (owner == me)
                n[k] += 1
                stat[k]["帧"] += 1
                if order == ATTACK:
                    stat[k]["攻击令"] += 1
                elif order == 0:
                    stat[k]["无订单"] += 1
                elif order in MOVE:
                    stat[k]["在移动"] += 1
                if tlo not in (-1, 0, 0xFFFFFFFF):
                    stat[k]["有目标"] += 1
                    per[k].add((tlo, thi))
            for k in (True, False):
                if n[k]:
                    cnt[k].append(n[k])
                    tgt[k].append(len(per[k]))
        print("\n== %s  %d 帧（作战单位 %d 个，建筑已滤掉）"
              % (os.path.basename(f), len(frames), len(mobile)))
        print("   %s：我方同时在接触的峰值 %d 人，>=6 人的帧 %d 个（%d%%）"
              % (kind, peak, hot, 100 * hot // max(1, len(contact))))
        note = "（只算会战帧）" if peak >= 6 else "（全部帧）"
        if creepN:
            print("   （另有 %d 个中立/野怪单位·帧，不算进敌我）" % creepN)
        for k, name in ((True, "我方" + note), (False, "敌方" + note)):
            st = stat[k]
            t = st["帧"] or 1
            u = sum(cnt[k]) / max(1, len(cnt[k]))
            tt = sum(tgt[k]) / max(1, len(tgt[k]))
            print("   %s 均 %4.1f 个｜攻击令 %2d%%｜移动 %2d%%｜无订单 %2d%%｜"
                  "有目标 %2d%%｜同时打 %.1f 个目标"
                  % (name, u, 100 * st["攻击令"] // t, 100 * st["在移动"] // t,
                     100 * st["无订单"] // t, 100 * st["有目标"] // t, tt))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    raise SystemExit(report(a.n) if a.report else main())

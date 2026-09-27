"""mod_spy.py —— 盯着电脑对手，把它的每一步记下来。

    python mod_spy.py                 # 跟着跑，每局写一份
    python mod_spy.py --report        # 看已经攒下来的对比

对手电脑就在同一局里：它怎么打野、怎么造第 2 个基地、怎么采矿，全都看得到，
应当拿来分析。

**这是我们独有的优势。** 锁步模型下客户端持有所有玩家的
权威状态，我们的共享内存快照里本来就有对方的每一个单位 ——
可此前只拿它数了个"敌方兵力"。

一个跑得比我们好的 AI，就在同一张地图上、同样的规则下、每 50 毫秒
可观测。**这是最便宜的老师。**

## 记什么

只记**时间点**和**位置**，不记瞬时值 —— 因为要回答的是"它什么时候做什么"：

    第一个工人 / 第 10 个工人 / 第 15 个工人   什么时候
    第一座兵营 / 第一个英雄 / 二本            什么时候
    ★ 第二座大厅                             什么时候、在哪、离家多远
    ★ 军队第一次离家 > 3000                  什么时候（出门打野）
    ★ 军队第一次靠近我方基地 < 4000          什么时候（真正来打我们）
    工人在金矿附近 vs 在别处                  比例随时间

## 为什么不写进 war3.db

这是**观察笔记**，不是对局结果。写成 jsonl 一局一行，
和已有的复盘分开 —— 混在一起会让"我们的成绩"和"它的打法"互相污染。
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
from w3state import Session          # noqa: E402
import modlog  # noqa: E402

OUT = os.environ.get(
    "WAR3_SPY_FILE",
    os.path.join(w3paths.BIN, "spy.jsonl"),
)
TICK = 0.5                            # 观察不用很快，0.5 秒足够定位"什么时候"
WORKERS = {"hpea", "opeo", "uaco", "ewsp"}
HALLS = {"htow", "hkee", "hcas",      # 人族 城镇大厅/主城/城堡
         "ogre", "ostr", "ofrt",      # 兽族 大厅/要塞/堡垒
         "unpl", "unp1", "unp2",      # 亡灵 大墓地
         "etol", "etoa", "etoe"}      # 暗夜 生命之树/家园之树/永恒之树


class _ProcessWatch:
    """Retain the observed process identity; a missing snapshot is not an exit."""

    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = None
        self.known_exited = False

    def open(self, pid):
        self.close()
        # SYNCHRONIZE only: no memory access, process mutation or WMI polling.
        self.handle = self.api.OpenProcess(0x00100000, False, pid)
        self.known_exited = not self.handle and ctypes.get_last_error() == 87

    def exited(self):
        if self.handle:
            status = self.api.WaitForSingleObject(self.handle, 0)
            if status == 0:       # WAIT_OBJECT_0: this exact process has exited.
                return True
            if status == 258:     # WAIT_TIMEOUT: it is still alive.
                return False
        # Access denied / failed wait cannot be turned into an exit signal.
        return True if self.known_exited else None

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
        self.handle = None
        self.known_exited = False


def dist(a, b) -> float:
    return math.hypot(a.x - b[0], a.y - b[1])


def centroid(us):
    if not us:
        return None
    return (sum(u.x for u in us) / len(us), sum(u.y for u in us) / len(us))


class Match:
    """一局的观察笔记。里程碑只记**第一次**发生的时刻。"""

    def __init__(self, t0):
        self.t0 = t0
        self.mark = {}
        self.preexisting = set()   # 接进来之前就已经成立的，不计时
        self.foe_home = None
        self.my_home = None
        self.samples = 0
        self.foe_worker_max = 0
        self.foe_army_max = 0

    def at(self, now):
        return round(now - self.t0, 1)

    def once(self, key, now, **extra):
        # ⚠ **第一次采样时就已经成立的里程碑不算数。**
        #
        # 我们经常是从半局中间接进去的（游戏重启、模块重启、AI 重连），
        # 那时候工人早就 10 个了、大厅早就 2 座了 —— 如果照记，
        # 就会得出"我们第 0 秒就开出了第二座大厅"这种假数据。
        # 而假数据比没数据糟：它会让对比表看起来我们赢了。
        #
        # 判据：头 3 秒内成立的一律当成"接进来之前就有了"，只登记不计时。
        if key in self.mark or key in self.preexisting:
            return False
        if self.at(now) < 3.0:
            self.preexisting.add(key)
            return False
        self.mark[key] = {"t": self.at(now), **extra}
        return True


def observe(ses, m: Match, snap, now) -> None:
    me = snap.me
    foes = [u for u in snap.units
            if u.hp > 0 and u.owner != me and u.owner < 12]
    if not foes:
        return
    m.samples += 1

    fw = [u for u in foes if u.type in WORKERS]
    fh = [u for u in foes if u.type in HALLS]
    fa = [u for u in foes if u.type not in WORKERS and u.hp_max < 900
          and u.type not in HALLS]

    if m.foe_home is None and fh:
        m.foe_home = (fh[0].x, fh[0].y)
    if m.my_home is None:
        mh = [u for u in snap.mine if u.type in HALLS]
        if mh:
            m.my_home = (mh[0].x, mh[0].y)

    m.foe_worker_max = max(m.foe_worker_max, len(fw))
    m.foe_army_max = max(m.foe_army_max, len(fa))

    # 工人里程碑 —— 回答"它什么时候把工人堆到 N 个"
    for n in (6, 10, 12, 15, 18):
        if len(fw) >= n:
            m.once(f"foe_workers_{n}", now, n=len(fw))

    # ★ 第二座大厅：什么时候、在哪、离它老家多远
    if len(fh) >= 2 and m.foe_home:
        far = max(fh, key=lambda h: dist(h, m.foe_home))
        m.once("foe_second_hall", now,
               x=int(far.x), y=int(far.y),
               from_home=int(dist(far, m.foe_home)),
               type=far.type)
    if len(fh) >= 3:
        m.once("foe_third_hall", now, n=len(fh))

    # 英雄（类型码首字母大写）
    fhero = [u for u in foes if u.is_hero]
    if fhero:
        m.once("foe_first_hero", now, type=fhero[0].type)
    if len(fhero) >= 2:
        m.once("foe_second_hero", now)

    # 军队第一次离家远行 = 出门打野
    c = centroid(fa)
    if c and m.foe_home and len(fa) >= 3:
        d = math.hypot(c[0] - m.foe_home[0], c[1] - m.foe_home[1])
        if d > 3000:
            m.once("foe_army_out", now, dist=int(d), n=len(fa))
    # 军队第一次逼近我们 = 真的来打了
    if c and m.my_home and len(fa) >= 3:
        d2 = math.hypot(c[0] - m.my_home[0], c[1] - m.my_home[1])
        if d2 < 4000:
            m.once("foe_attacks_us", now, dist=int(d2), n=len(fa))

    # 我方对照：同样的里程碑，用同一把尺子量
    mw = [u for u in snap.mine if u.type in WORKERS]
    mh2 = [u for u in snap.mine if u.type in HALLS]
    for n in (6, 10, 12, 15, 18):
        if len(mw) >= n:
            m.once(f"my_workers_{n}", now, n=len(mw))
    if len(mh2) >= 2:
        m.once("my_second_hall", now)
    if [u for u in snap.mine if u.is_hero]:
        m.once("my_first_hero", now)


def flush(m: Match) -> None:
    if m.samples < 20 or "foe_workers_6" not in m.mark:
        return                               # 太短，不值得记
    rec = {"samples": m.samples,
           "foe_worker_max": m.foe_worker_max,
           "foe_army_max": m.foe_army_max,
           "marks": m.mark}
    try:
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def report() -> int:
    try:
        rows = [json.loads(l) for l in open(OUT, encoding="utf-8") if l.strip()]
    except OSError:
        print("还没有观察记录，先让 mod_spy.py 跟着跑几局")
        return 1
    if not rows:
        print("还没有观察记录")
        return 1
    agg = defaultdict(list)
    for r in rows:
        for k, v in r["marks"].items():
            agg[k].append(v["t"])
    print(f"共 {len(rows)} 局观察记录（时间单位：秒，从进入对局算起）\n")
    # ⚠ **两列不能各算各的平均。**
    #
    # 上一版就是那样，于是打印出这种东西：
    #     工人到 10 个   电脑 98s (13局)
    #     工人到 12 个   电脑 54s (10局)
    # 电脑到 12 个工人比到 10 个还早 —— 物理上不可能。
    # 原因是两行的分母是**不同的对局子集**：13 局都到了 10 个，
    # 只有 10 局到了 12 个，而那 10 局恰好是发育顺的局。
    # 同一个道理也污染了"电脑 vs 我们"那一列差值：拿 A 组的均值
    # 减 B 组的均值，得到的数不代表任何一局里真实发生的差距。
    #
    # 只在**同一局里两边都做到了**的那些局上配对求差。做不到的
    # 单独报"我们 N 局里只做到 M 局"，那本身就是结论 ——
    # 把"从没做到"混进平均值里会让它看起来只是慢一点。
    pairs = [
        ("工人到 6 个", "foe_workers_6", "my_workers_6"),
        ("工人到 10 个", "foe_workers_10", "my_workers_10"),
        ("工人到 12 个", "foe_workers_12", "my_workers_12"),
        ("工人到 15 个", "foe_workers_15", "my_workers_15"),
        ("第一个英雄", "foe_first_hero", "my_first_hero"),
        ("第二座大厅", "foe_second_hall", "my_second_hall"),
    ]
    print(f"{'里程碑':<14}{'电脑':>10}{'我们':>10}{'配对差':>12}   两边都做到")
    print("-" * 62)
    for label, fk, mk in pairs:
        # 配对：只要这一局两边都有这个里程碑
        both = [(r["marks"][fk]["t"], r["marks"][mk]["t"])
                for r in rows if fk in r["marks"] and mk in r["marks"]]
        nf = sum(1 for r in rows if fk in r["marks"])
        nm = sum(1 for r in rows if mk in r["marks"])
        if both:
            fa = sum(a for a, _ in both) / len(both)
            ma = sum(b for _, b in both) / len(both)
            d = ma - fa
            gap = f"慢 {d:.0f}s" if d > 0 else f"快 {-d:.0f}s"
            print(f"{label:<14}{fa:>9.0f}s{ma:>9.0f}s{gap:>12}   {len(both)}/{len(rows)} 局")
        else:
            # 一边从没做到 —— 这不是"慢"，是另一回事，要分开说
            note = f"电脑 {nf}/{len(rows)} 局做到，我们 {nm}/{len(rows)} 局"
            print(f"{label:<14}{'—':>10}{'—':>10}{'无法配对':>12}   {note}")
    # 它的第二座大厅建在哪
    hall = [r["marks"]["foe_second_hall"] for r in rows
            if "foe_second_hall" in r["marks"]]
    if hall:
        avg = sum(h.get("from_home", 0) for h in hall) / len(hall)
        print(f"\n电脑的第二座大厅：平均离它老家 {avg:.0f}（{len(hall)} 局）")
    out = [r["marks"]["foe_army_out"] for r in rows if "foe_army_out" in r["marks"]]
    if out:
        print(f"电脑军队第一次远行：平均第 {sum(o['t'] for o in out)/len(out):.0f} 秒，"
              f"带 {sum(o.get('n',0) for o in out)/len(out):.1f} 个兵")
    atk = [r["marks"]["foe_attacks_us"] for r in rows if "foe_attacks_us" in r["marks"]]
    if atk:
        print(f"电脑第一次打到我们家门口：平均第 "
              f"{sum(a['t'] for a in atk)/len(atk):.0f} 秒，"
              f"带 {sum(a.get('n',0) for a in atk)/len(atk):.1f} 个兵")
    print(f"\n电脑峰值：工人 {max(r['foe_worker_max'] for r in rows)}、"
          f"兵 {max(r['foe_army_max'] for r in rows)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        return report()
    modlog.tee_stdout("mod_spy")

    print("盯梢模块启动（0.5 秒一次）", flush=True)
    ses = Session()
    process = _ProcessWatch()
    m = None
    last_match = None
    while True:
        try:
            snap = ses.read()
            now = time.monotonic()
            if snap is None:
                # Includes seqlock retries and layout-2 epoch priming. Keep the
                # current observations unless this exact process has exited.
                if m is not None and process.exited() is True:
                    flush(m)
                    m = None
                    last_match = None
                    process.close()
                time.sleep(1.0)
                continue
            if not snap.in_game:
                if m is not None:
                    flush(m)
                    m = None
                last_match = None
                process.close()
                time.sleep(1.0)
                continue
            current_match = (ses.pid, snap.epoch)
            if current_match != last_match:
                if m is not None:
                    flush(m)
                m = None
                last_match = current_match
                process.open(ses.pid)
            if m is None:
                m = Match(now)
                print(f"新的一局，开始观察（pid={ses.pid}, epoch={snap.epoch}）", flush=True)
            observe(ses, m, snap, now)
            time.sleep(TICK)
        except KeyboardInterrupt:
            if m:
                flush(m)
            process.close()
            return 0
        except Exception as exc:              # noqa: BLE001
            print(f"出错（继续）：{exc!r}", flush=True)
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())

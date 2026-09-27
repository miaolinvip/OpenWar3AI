# -*- coding: utf-8 -*-
"""把战斗帧抽成「一场仗一行」的样本集，带磁盘缓存。

**为什么单独一层**：inst1~6 一共 950+ 个战斗帧文件、19 GB，每次校准都重新解析要一个小时。
抽取结果按「文件路径 + 大小 + 修改时间」缓存成小 JSON（一个文件几 KB），
第二次跑只读缓存。样本从 47 场扩到几百场靠的就是这个。

**每一行是什么**：一场仗开打那一刻的**预测输入**（双方参战名单 + 真实血量 -> 生产代码的战力比），
配上这场仗打完的**实际结果**（双方各死了几个 / 损失多少钱）。
有了这张表才能问「说七成把握的时候，实测是不是七成」。

⚠ 两条口径，改之前先读：
  * 参战名单取**开打后 OPEN_SECS 秒内**进入接触的并集，不是开打那一帧的接触集
    —— 刚碰上时往往只有 1 个人接触，按那一帧过滤会把绝大多数仗丢掉（第一版 10 个文件只剩 1 场）。
    位置和血量仍用开打帧的：预测要回答的是「现在该不该打」。
  * 野怪（owner 12）的仗要传 foe=battles.foe_creep 才切得出来。默认那一份只算敌方玩家，
    于是 tactics 里一半的战力阈值（打野那一组）从来没有对照过实战。

用法：
    python tools/fightset.py --files 200            # 抽取并缓存，打印统计
    python tools/fightset.py --files 200 --refresh  # 忽略缓存重抽
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import glob
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRAIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "war3-modules"), BRAIN):
    if p not in sys.path:
        sys.path.insert(0, p)

import battles as B                  # noqa: E402
from brain import amai as AM         # noqa: E402
from brain import gamedata as G      # noqa: E402
from brain import tactics as T       # noqa: E402

CACHE = os.path.join(BRAIN, "tuning", "fightset")
VERSION = 4            # ⚠ 抽取口径改了就 +1：缓存键带着它，否则旧缓存会**静默**喂回缺列的老样本
OPEN_SECS = 3.0        # 开打后这么久内进入接触的都算这一仗的参战名单
MIN_SIDE = 2           # 双方各至少这么多人才算一场（一对一的遭遇噪声太大）
MIN_FRAMES = 4         # 太短的接触不算一场

# 三种「预测时看得见的兵力」口径，每场都算一遍，回头比哪一种更贴近实战结果：
#   c    开打 3 秒内进入接触的人          —— 最保守，但和生产代码喂给 power_ratio 的东西不是一回事
#   l15  战场重心 1500 内的全部作战单位   —— 和 tactics 的聚团半径 / FIGHT_R 同一量级，最接近生产口径
#   l25  重心 2500 内                     —— 加上还在赶路的那一批（SUPPORT_R 那一档）
# ⚠ 经验：校准的输入分布必须和生产调用时的输入分布一致，
#   否则校出来的系数是给另一个问题用的。
VARIANTS = (("c", None), ("l15", 1500.0), ("l25", 2500.0))


class Row:
    """战斗帧的一行包装成战力层认的单位对象。帧格式 [addr,type,owner,x,y,hp,order,tlo,thi,hpMax,hlo,hhi]。"""
    __slots__ = ("addr", "type", "owner", "x", "y", "hp", "hp_max", "order")

    def __init__(self, r):
        self.addr, self.type, self.owner = r[B.A], r[B.TYP], r[B.OWN]
        self.x, self.y, self.hp = float(r[B.X]), float(r[B.Y]), float(r[B.HP])
        self.hp_max = float(r[9]) if len(r) > 9 and r[9] else max(1.0, self.hp)
        self.order = r[B.ORD] if len(r) > B.ORD else 0


def value_of(t: str) -> float:
    """一个单位值多少钱。英雄按 4 倍算（复活要钱、要时间，还掉一级经验），召唤物 0。"""
    gold = float(G.stat(t, "gold", 0) or 0)
    if G.is_hero(t):
        return 4 * max(gold, 425.0)
    if gold <= 0:                      # 召唤物 / 野怪：没有价格，按人头给一个小的正值
        return 60.0
    return gold


def _counter_value(c) -> tuple[float, float]:
    """(按人头, 按钱) 两种口径的总量。"""
    n = v = 0.0
    for t, k in (c or {}).items():
        n += k
        v += k * value_of(t)
    return n, v


def kill_times(mine, foes, levels=None):
    """(我们打光他们要几秒, 他们打光我们要几秒)。这就是 power_ratio 的分子分母拆开。

    设计上要的判据「对面打光我们要几秒 vs 我们打光他们要几秒」直接读这两个数。"""
    hm, dm = T.power(mine, levels)
    hf, df = T.power(foes, levels)
    t_us = hf / dm if dm > 0 else float("inf")      # 我们打光他们
    t_them = hm / df if df > 0 else float("inf")    # 他们打光我们
    return t_us, t_them


def _side_stats(mine, foes, tag, out):
    """一种兵力口径下的预测量，平铺进 out（键带 tag 后缀）。

    **两个估计器都算**：大脑里其实有两套战力口径，各管一半阈值 ——
      ratio_*  = tactics.power_ratio（总血 × 总每秒伤害），打野那一组阈值比的是它；
      sratio_* = amai.strength 之比（(人口+等级)/2 × 血量比例），出击/撤退/支援/拆分矿比的是它。
    只校准其中一个，另一半阈值照样是手调的。"""
    r = T.power_ratio(mine, foes)
    t_us, t_them = kill_times(mine, foes)
    sm, sf = AM.strength(mine), AM.strength(foes)
    out["str_" + tag] = round(sm, 2)
    out["strf_" + tag] = round(sf, 2)
    out["sratio_" + tag] = None if sf <= 0 else round(sm / sf, 4)
    out["ratio_" + tag] = None if not math.isfinite(r) else round(r, 4)
    out["tus_" + tag] = None if not math.isfinite(t_us) else round(t_us, 2)
    out["tthem_" + tag] = None if not math.isfinite(t_them) else round(t_them, 2)
    out["m_" + tag] = len(mine)
    out["f_" + tag] = len(foes)
    out["hp_" + tag] = round(sum(u.hp for u in mine), 1)
    out["hpf_" + tag] = round(sum(u.hp for u in foes), 1)
    out["frac_" + tag] = round(sum(u.hp for u in mine) / max(1.0, sum(u.hp_max for u in mine)), 3)


def _one_fight(frames, lo, hi, me, foe, kind):
    """一场仗 -> 一行样本；不够格返回 None。"""
    t0, units = frames[lo]
    cm, cf = set(), set()
    j = lo
    while j <= hi and frames[j][0] - t0 <= OPEN_SECS:
        a_m, a_f = B.contact_sets(frames[j][1], me, foe)
        cm |= a_m
        cf |= a_f
        j += 1
    if len(cm) < MIN_SIDE or len(cf) < MIN_SIDE:
        return None
    idx = {r[B.A]: r for r in units}
    contact_m = [Row(idx[x]) for x in cm if x in idx]
    contact_f = [Row(idx[x]) for x in cf if x in idx]
    if len(contact_m) < MIN_SIDE or len(contact_f) < MIN_SIDE:
        return None
    gx = sum(u.x for u in contact_m + contact_f) / len(contact_m + contact_f)
    gy = sum(u.y for u in contact_m + contact_f) / len(contact_m + contact_f)

    s = B.summarize(frames, lo, hi, me, foe)
    n_m, v_m = _counter_value(s["comp_m"])
    n_f, v_f = _counter_value(s["comp_f"])
    lost_n_m, lost_v_m = _counter_value(s["lost_m"])
    lost_n_f, lost_v_f = _counter_value(s["lost_f"])
    if n_m <= 0 or n_f <= 0:
        return None
    row = dict(
        kind=kind, t0=round(t0, 1), dur=round(frames[hi][0] - t0, 1), where=s["where"],
        heroes_m=len(s["heroes"]), heroes_dead_m=len(s["heroes_dead"]),
        n_m=n_m, n_f=n_f, v_m=round(v_m), v_f=round(v_f),
        lost_n_m=lost_n_m, lost_n_f=lost_n_f, lost_v_m=round(lost_v_m), lost_v_f=round(lost_v_f),
    )
    for tag, radius in VARIANTS:
        if radius is None:
            mine, foes = contact_m, contact_f
        else:
            mine = [Row(r) for r in units
                    if r[B.OWN] == me and B.is_fighter(r) and math.hypot(r[B.X] - gx, r[B.Y] - gy) <= radius]
            foes = [Row(r) for r in units
                    if foe(r, me) and B.is_fighter(r) and math.hypot(r[B.X] - gx, r[B.Y] - gy) <= radius]
        if len(mine) < MIN_SIDE or len(foes) < MIN_SIDE:
            continue
        _side_stats(mine, foes, tag, row)
    return row if "ratio_c" in row else None


def extract(path, me=0):
    """一个战斗帧文件 -> 样本列表（打玩家的 + 打野的）。"""
    frames = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if "u" in o:
                frames.append((float(o.get("t", 0.0)), o["u"]))
    if len(frames) < 10:
        return []
    out = []
    for kind, foe in (("foe", B.foe_player), ("creep", B.foe_creep)):
        for lo, hi in B.segment(frames, me, foe):
            if hi - lo < MIN_FRAMES:
                continue
            row = _one_fight(frames, lo, hi, me, foe, kind)
            if row:
                out.append(row)
    return out


# ---------------------------------------------------------------- 缓存

def cache_key(path):
    st = os.stat(path)
    return "v%d-%d-%d" % (VERSION, st.st_size, int(st.st_mtime))


def cache_file(path):
    inst = "x"
    parts = os.path.abspath(path).replace("\\", "/").split("/")
    for p in parts:
        if p.startswith("inst"):
            inst = p
    return os.path.join(CACHE, "%s_%s.json" % (inst, os.path.splitext(os.path.basename(path))[0]))


def cached_extract(path, me=0, refresh=False):
    """抽取一个文件，带缓存。返回 (样本列表, 是否命中缓存)。"""
    cf, key = cache_file(path), cache_key(path)
    if not refresh:
        try:
            with open(cf, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("key") == key:
                for s in d["rows"]:
                    s["file"] = os.path.basename(path)
                return d["rows"], True
        except (OSError, ValueError, KeyError):
            pass
    rows = extract(path, me)
    os.makedirs(CACHE, exist_ok=True)
    tmp = cf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dict(key=key, rows=rows), fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, cf)
    for s in rows:
        s["file"] = os.path.basename(path)
    return rows, False


def files(insts="1,2,3,4,5,6", limit=None):
    paths = []
    for n in [x.strip() for x in str(insts).split(",") if x.strip()]:
        paths += glob.glob(os.path.join(ROOT, "war3-button-hook-lab", "build_intel4",
                                        "inst%s" % n, "fights", "fight-*.jsonl"))
    paths = sorted(paths, key=lambda p: os.path.basename(p))
    return paths[-limit:] if limit else paths


def load(insts="1,2,3,4,5,6", limit=None, refresh=False, jobs=6, me=0, progress=None):
    """抽取（或读缓存）一批文件的全部样本。jobs>1 时多进程解析。

    ⚠ Windows 用 spawn：调用方的脚本**必须**有 `if __name__ == "__main__":` 保护，
    否则子进程重新导入主模块时又会走到这里，整池当场炸掉（实测报
    "A process in the process pool was terminated abruptly"，而且样本数是 0 不是报错）。
    下面兜底：整池失败就退回单进程，不让一个调用姿势毁掉一次分析。"""
    paths = files(insts, limit)
    if jobs and jobs > 1 and len(paths) > 1:
        try:
            return _load_pool(paths, refresh, jobs, me, progress)
        except (futures.process.BrokenProcessPool, OSError) as exc:
            print("  ! 多进程池不可用（%s），退回单进程" % exc)
    return _load_serial(paths, refresh, me, progress)


def _load_pool(paths, refresh, jobs, me, progress):
    rows, hits, broken = [], 0, 0
    with futures.ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(cached_extract, p, me, refresh): p for p in paths}
        for k, fu in enumerate(futures.as_completed(futs), 1):
            try:
                got, hit = fu.result()
            except futures.process.BrokenProcessPool:
                broken += 1
                continue
            except Exception as exc:  # noqa: BLE001 - 单个坏文件不该拖垮整批
                print("  ! %s: %s" % (os.path.basename(futs[fu]), exc))
                continue
            rows += got
            hits += hit
            if progress and k % progress == 0:
                print("  ... %d/%d 文件，%d 场" % (k, len(paths), len(rows)))
    if broken:
        raise futures.process.BrokenProcessPool("%d/%d 个文件没跑成" % (broken, len(paths)))
    return rows, len(paths), hits


def _load_serial(paths, refresh, me, progress):
    rows, hits = [], 0
    for k, p in enumerate(paths, 1):
        got, hit = cached_extract(p, me, refresh)
        rows += got
        hits += hit
        if progress and k % progress == 0:
            print("  ... %d/%d 文件，%d 场" % (k, len(paths), len(rows)))
    return rows, len(paths), hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=60)
    ap.add_argument("--inst", default="1,2,3,4,5,6")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    rows, n, hits = load(a.inst, a.files, a.refresh, a.jobs, progress=20)
    print("文件 %d 个（缓存命中 %d），样本 %d 场" % (n, hits, len(rows)))
    for kind in ("foe", "creep"):
        g = [r for r in rows if r["kind"] == kind]
        if not g:
            continue
        print("  %-6s %4d 场  我方投入中位 %.0f 人  对面 %.0f 人  时长中位 %.0f 秒"
              % (kind, len(g), _median([r["n_m"] for r in g]), _median([r["n_f"] for r in g]),
                 _median([r["dur"] for r in g])))
        for tag, _r in VARIANTS:
            have = [r for r in g if r.get("ratio_" + tag) is not None]
            if have:
                print("      口径 %-4s %4d 场  我方 %.0f 人 vs %.0f 人  战力比中位 %.2f"
                      % (tag, len(have), _median([r["m_" + tag] for r in have]),
                         _median([r["f_" + tag] for r in have]),
                         _median([r["ratio_" + tag] for r in have])))


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""用历史战斗校准战力估计器，拟合出生产代码要用的修正系数。

**为什么**（09-20 架构评审发现的根因 B）：
出击/撤退/打野/守家至少 12 个阈值全部建在 `tactics.power_ratio` 上，
而这个估计器**从来没有对照实战结果校准过**。老的 PowerShell 推演校准过一次，
结论是「推演判赢 39 场，实战只赢 12 场（31%）」；新大脑换了兰彻斯特口径，准不准没人量过。
每次打输就去调那 12 个阈值中的一个，等于在一个未知偏差上贴补丁。

**做法**：
  1. `tools/fightset.py` 把战斗帧抽成「一场仗一行」（开打那一刻的战力比 + 打完的实际损失），带缓存。
  2. 这里拟合两条曲线，输入都是 `ln(战力比)`：
       打赢的概率      p_win = pmax · sigmoid(a·lnR + b)
       我方损失的份额  loss  = lmin + (1-lmin) · sigmoid(c·lnR + d)
     `pmax` 和 `lmin` 是**天花板和地板**，不是摆设：实测打玩家的仗胜率在约五成封顶、
     我方损失份额在约五成触底，战力比从 5 涨到 30 完全没有改善。
     一条普通 logistic 没有天花板，硬拟合会外推出「五成把握 = 战力比 15」这种假结论（第一版就是）。
  3. 按时间切前 70% 拟合、后 30% 检验，只拿**检验集**的可靠性曲线下结论。
  4. `--write` 把系数写进 `brain/data/powercal.json`，生产代码 `brain/winprob.py` 读它。

于是上层阈值从「R >= 2.5」变成「校准过的胜率 / 预计损失份额」—— 能解释、能验证、
而且 12 个手调数收敛成三条线（承诺 / 坚持 / 要便宜）。

⚠ 打野（creep）和打玩家（foe）分开拟合：野怪不撤退、不补血、不复活，是两个不同的分布。
⚠ 不要在这里另写一个战力公式。`fightset` 调的是生产代码那一份 `tactics.power_ratio`；
   校准的是它，不是我的复刻（自己抄一遍的时候就已经分叉了）。

用法：
    python tools/calibrate_power.py                    # 用缓存里的全部样本
    python tools/calibrate_power.py --files 200        # 只看最近 200 个战斗文件
    python tools/calibrate_power.py --write            # 拟合结果写进 brain/data/powercal.json
    python tools/calibrate_power.py --derive           # 打印三条线推出来的那 12 个阈值
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.path.dirname(HERE)
for p in (BRAIN, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fightset as FS     # noqa: E402

OUT = os.path.join(BRAIN, "brain", "data", "powercal.json")
TAG = "l15"                  # 兵力口径：战场重心 1500 内的作战单位，最接近生产代码喂给 power_ratio 的东西
R_LO, R_HI = 0.02, 50.0      # 战力比钳位；ln 之后就是 [-3.9, 3.9]
TIE = 1e-9


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _clip01(v):
    return min(max(float(v), 0.0), 1.0)


def fit_capped(x, y):
    """拟合 y = lo + (hi-lo)·sigmoid(a·x + b)，四个参数一起最大似然（二项偏差）。

    lo/hi 就是地板和天花板。给它们留自由度是这条曲线和普通 logistic 的**唯一**区别，
    也是第一版外推出「五成把握 = 战力比 15」的原因：没有天花板的曲线只能靠把斜率压平来拟合饱和。

    ⚠ 第二版在这里栽过一次：函数末尾有一行 `hi = min(max(hi, hi_bound[0]), hi_bound[1])`，
      默认 hi_bound=(1.0, 1.0)，于是**拟合出来的天花板被无条件改回 1.0**，
      检验集立刻出现「预测 0.95 实测 0.52」。钳位只在 [0,1] 上做，不要再引入第二套边界。"""
    def nll(theta):
        a, b, lo, hi = theta
        lo, hi = _clip01(lo), _clip01(hi)
        p = np.clip(lo + (hi - lo) * sigmoid(a * x + b), 1e-6, 1 - 1e-6)
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    best, best_v = None, float("inf")
    for a0 in (0.3, 0.8, 1.5):
        for lo0, hi0 in ((0.0, 1.0), (0.05, 0.6), (0.3, 0.95)):
            res = minimize(nll, np.array([a0, 0.0, lo0, hi0]), method="Nelder-Mead",
                           options=dict(maxiter=6000, maxfev=6000, xatol=1e-7, fatol=1e-10))
            if res.fun < best_v:
                best_v, best = res.fun, res.x
    a, b, lo, hi = best
    lo, hi = _clip01(lo), _clip01(hi)
    if hi < lo:                      # 方向反了就翻过来，保持 lo 是低战力比那一端
        a, b, lo, hi = -a, -b, hi, lo
    return float(a), float(b), lo, hi


def curve(theta, x):
    a, b, lo, hi = theta
    return lo + (hi - lo) * sigmoid(a * np.asarray(x, dtype=float) + b)


def invert(theta, target):
    """曲线的反函数：要达到这个 y，战力比至少要多少。够不到返回 None。"""
    a, b, lo, hi = theta
    if hi <= lo:
        return None
    frac = (target - lo) / (hi - lo)
    if frac <= 0:
        return R_LO
    if frac >= 1 or a == 0:
        return None                     # 天花板就在那里，再大的战力比也到不了
    return float(math.exp((math.log(frac / (1 - frac)) - b) / a))


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p, y):
    """0/1 标签的 AUC（按秩，并列取平均秩）。全同一类时返回 nan。"""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    pos, neg = y > 0.5, y <= 0.5
    if not pos.any() or not neg.any():
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = np.mean(ranks[order[i:j + 1]])
        i = j + 1
    n1, n0 = pos.sum(), neg.sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def prepare(rows, kind, est="power"):
    """样本 -> (ln比值, 打赢没有, 我方损失份额)。

    est: "power" = tactics.power_ratio（打野那一组阈值比的是它）；
         "amai"  = amai.strength 之比（出击/撤退/支援/拆分矿比的是它）。

    「赢」= 我方损失的**人头份额**低于对面。按人头不按钱：英雄按 4 倍钱算时结论一样
    （实测把英雄整个剔掉，曲线形状不变），人头这个口径解释起来更直接。
    双方一个人都没死的仗剔掉 —— 那是擦肩而过，没有胜负信息。"""
    key = ("sratio_" if est == "amai" else "ratio_") + TAG
    x, yw, yl = [], [], []
    for r in rows:
        if r["kind"] != kind or r.get(key) is None:
            continue
        ratio = max(R_LO, min(R_HI, r[key]))
        share_m = r["lost_n_m"] / max(1.0, r["n_m"])
        share_f = r["lost_n_f"] / max(1.0, r["n_f"])
        if share_m <= TIE and share_f <= TIE:
            continue
        x.append(math.log(ratio))
        yw.append(1.0 if share_f > share_m else 0.0)
        yl.append(min(1.0, share_m))
    return np.array(x), np.array(yw), np.array(yl)


def reliability(p, y, edges, label):
    lines = ["%-14s %6s %10s %10s" % (label, "场次", "预测均值", "实测均值")]
    for lo, hi in zip(edges[:-1], edges[1:]):
        g = (p >= lo) & (p < hi)
        if not g.any():
            continue
        lines.append("%-14s %6d %9.2f %9.2f"
                     % ("[%.2f, %.2f)" % (lo, hi), int(g.sum()), float(p[g].mean()), float(y[g].mean())))
    return lines


EST_NAME = {"power": "power_ratio（总血×每秒伤害）", "amai": "amai.strength 之比"}


def fit_kind(rows, kind, est="power", split=0.70, verbose=True):
    x, yw, yl = prepare(rows, kind, est)
    if len(x) < 100:
        if verbose:
            print("\n=== %s/%s：样本只有 %d 场，不够拟合 ===" % (kind, est, len(x)))
        return None
    cut = int(len(x) * split)
    tr, te = slice(0, cut), slice(cut, len(x))

    win = fit_capped(x[tr], yw[tr])
    loss = fit_capped(x[tr], yl[tr])
    p_te, l_te = curve(win, x[te]), curve(loss, x[te])
    base = float(yw[tr].mean())

    if verbose:
        print("\n" + "=" * 74)
        print("=== %s ｜ %s：%d 场（拟合 %d / 检验 %d），实测赢 %.0f%% ==="
              % (kind, EST_NAME[est], len(x), cut, len(x) - cut, 100 * yw.mean()))
        print("修正系数  p_win = %.3f + %.3f·sigmoid(%.3f·lnR %+.3f)"
              % (win[2], win[3] - win[2], win[0], win[1]))
        print("          loss  = %.3f + %.3f·sigmoid(%.3f·lnR %+.3f)"
              % (loss[2], loss[3] - loss[2], loss[0], loss[1]))
        print("          胜率天花板 %.0f%%  地板 %.0f%%  ｜ 损失份额地板 %.0f%% 天花板 %.0f%%"
              % (100 * win[3], 100 * win[2], 100 * min(loss[2], loss[3]), 100 * max(loss[2], loss[3])))
        print("  要几成把握 -> 战力比要多少：", end="")
        for q in (0.3, 0.4, 0.5, 0.6, 0.7):
            r = invert(win, q)
            print("  %.0f成 %s" % (10 * q, "到不了" if r is None else "%.2f" % r), end="")
        print()
        print("  预计损失不超过 -> 战力比要多少：", end="")
        for q in (0.15, 0.25, 0.35, 0.5):
            r = invert(loss, q)
            print("  %.0f%% %s" % (100 * q, "到不了" if r is None else "%.2f" % r), end="")
        print()

        print("\n-- 胜率可靠性（拟合集，用来区分「优化器没拟合上」和「新旧两批仗本来就不一样」）--")
        for line in reliability(curve(win, x[tr]), yw[tr], (0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.01), "预测胜率"):
            print("  " + line)
        print("\n-- 胜率可靠性（检验集 %d 场，没参与拟合）--" % (len(x) - cut))
        for line in reliability(p_te, yw[te], (0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.01), "预测胜率"):
            print("  " + line)
        print("  Brier %.3f（只猜基准率 %.3f）  logloss %.3f  AUC %.3f"
              % (brier(p_te, yw[te]), brier(np.full(len(p_te), base), yw[te]),
                 logloss(p_te, yw[te]), auc(p_te, yw[te])))
        print("\n-- 损失份额可靠性（检验集）--")
        for line in reliability(l_te, yl[te], (0.0, 0.2, 0.35, 0.5, 0.65, 1.01), "预测损失份额"):
            print("  " + line)
        print("  Brier %.3f" % brier(l_te, yl[te]))
        print("\n-- 同一批仗，改之前那个隐含预测 --")
        r = np.exp(x[te])
        old_left = np.sqrt(np.clip(1.0 - 1.0 / np.maximum(r, 1e-9) ** 2, 0.0, 1.0))
        judged = r > 1.0
        print("  「R > 1 就判赢」判赢 %d 场，其中实际赢 %d 场（%.0f%%）"
              % (int(judged.sum()), int((judged & (yw[te] > 0.5)).sum()),
                 100 * float(yw[te][judged].mean()) if judged.any() else 0.0))
        print("  「预测剩 sqrt(1-1/R²)」预测均值 %.2f，实测我方存活份额 %.2f（乐观 %.1f 倍）"
              % (old_left.mean(), 1 - yl[te].mean(), old_left.mean() / max(1e-6, 1 - yl[te].mean())))

    return dict(kind=kind, est=est, n=int(len(x)), n_fit=int(cut), tag=TAG,
                win=[round(v, 5) for v in win], loss=[round(v, 5) for v in loss],
                base_rate=round(float(yw.mean()), 4),
                brier_test=round(brier(p_te, yw[te]), 4),
                brier_base=round(brier(np.full(len(p_te), base), yw[te]), 4),
                auc_test=round(auc(p_te, yw[te]), 4),
                loss_brier_test=round(brier(l_te, yl[te]), 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=0, help="只看最近这么多个战斗文件；0 = 全部")
    ap.add_argument("--inst", default="1,2,3,4,5,6")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--write", action="store_true", help="把系数写进 brain/data/powercal.json")
    ap.add_argument("--derive", action="store_true", help="打印三条线推出来的阈值（不需要样本，读 powercal.json）")
    a = ap.parse_args()

    if a.derive:
        _print_derived()
        if not a.write and not a.files:
            return

    rows, nfiles, hits = FS.load(a.inst, a.files or None, a.refresh, a.jobs, progress=200)
    rows.sort(key=lambda r: (r.get("file", ""), r["t0"]))
    print("战斗文件 %d 个（缓存命中 %d），样本 %d 场" % (nfiles, hits, len(rows)))

    fits = [f for f in (fit_kind(rows, k, e) for k in ("foe", "creep") for e in ("power", "amai")) if f]
    if not fits:
        print("没有够用的样本。")
        return

    if a.write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        payload = dict(
            note="tools/calibrate_power.py 拟合。p_win = lo+(hi-lo)·sigmoid(a·ln比值+b)，loss 同形。"
                 "参数顺序 [a, b, lo, hi]。键是「谁的仗/哪个估计器」：foe|creep × power|amai。",
            files=nfiles, samples=len(rows), tag=TAG,
            fits={"%s.%s" % (f["kind"], f["est"]): f for f in fits})
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("\n已写入 %s" % OUT)
        print("\n把下面这块贴回 brain/winprob.py 的 FALLBACK（tests/test_winprob.py 守着两边一致）：")
        for f in fits:
            key = "%s.%s" % (f["kind"], f["est"])
            print('    "%s": {"win": [%s],' % (key, ", ".join("%.5f" % v for v in f["win"])))
            print('    %s"loss": [%s]},' % (" " * (len(key) + 7), ", ".join("%.5f" % v for v in f["loss"])))
        _print_derived(reload=True)


def _print_derived(reload=False):
    from brain import winprob as W
    if reload:
        W.reload()
        print("⚠ 系数是导入时读的：这里打印的是**重读之后**的值，"
              "但 R_* 那些常量在导入时就算好了，要拿到新阈值得重新起一个进程。")
    print("\n" + "=" * 74)
    print("三条线（P_COMMIT %.2f / P_GIVEUP %.2f / LOSS_CHEAP %.2f）推出来的阈值："
          % (W.P_COMMIT, W.P_GIVEUP, W.LOSS_CHEAP))
    for name, value, why in W.derived_thresholds():
        print("  %-38s %7.2f   %s" % (name, value, why))


if __name__ == "__main__":
    main()

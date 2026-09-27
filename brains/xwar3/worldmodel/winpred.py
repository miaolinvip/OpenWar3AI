"""
M1 v0：「这仗打不打得赢」—— 学出来的胜负预测器 vs W3-Simulate，在**同一批交战、同一时刻、同一份输入**上比。

    python -m worldmodel.winpred                 # 抽样本（缓存）-> 训练 -> 报告 worldmodel/reports/m1_v0.md
    python -m worldmodel.winpred --rebuild       # 重新抽样本

口径（写死，别让比较变成挑口径）：
  * 预测时刻 = 接触开始后 k 游戏秒（k ∈ KS）。接触开始 = 第一步「双方存活单位里有一对距离 <= CONTACT」（和 battles.py 同一个 900）。
  * **输入只用那一刻「近处」的单位**：接触中的单位（900 内有敌人）的重心 NEAR_R 以内、活着的双方单位。
    ⚠ 真实对局导出只收了「后来参战」的单位 —— 远处还没到的单位之所以出现在数据里，是因为它后来来了；
      拿它当特征就是偷看未来。演练场景里全体单位本来就在场，同一口径处理保持一致。
  * W3-Simulate 的输入 = 同一批近处单位、**当时实际血量**（simulate.py，和 PowerShell 原版逐场一致）。
    另报一份「旧校准口径」（整场累计名单满血，偷看了未来）只为复现 09-16 的数字，不参与比较。
  * 标签（labels.py）：主 win_value；另报 win_wipe（推演 win 的语义）。
  * 划分按 splits.json（真实对局按局 + 冻结时刻；演练按种子块）。评估的置信区间按**组**（局 / 种子块）重采样。

模型：
  base      训练集基率
  sim       推演判赢 -> 1，否则 0（夹到 [0.02, 0.98] 算对数损失）
  sim_cal   推演的 left / 反向 left / 秒数喂逻辑回归（= 给推演做校准，看它的「排序」本身有没有用）
  lr        手工特征逻辑回归（numpy，L2）
  gbdt      同一批特征的 LightGBM（装了才跑）
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from .simulate import roster, simulate
from .vocab import TYPE_CODES, UNIT_STATS

INDEX = Path("worldmodel/data_index/index.jsonl")
CACHE = Path("worldmodel/data_m1/samples_v0.jsonl")
REPORT = Path("worldmodel/reports/m1_v0.md")
KS = (0.0, 5.0, 10.0)
START = -1.0                                          # 演练的「开场、接触之前」时刻
CONTACT = 900.0
NEAR_R = 1500.0
LABELS = ("win_value", "win_wipe")


# ---------------------------------------------------------------- 从 npz 抽一个时刻的特征

def _decode(z):
    u = z["units"]
    return dict(x=u[..., 0] * 4000, y=u[..., 1] * 4000, hp=u[..., 2] * 1000, ratio=u[..., 3], armor=u[..., 4] * 10,
                dmg=u[..., 5] * 50, rng=u[..., 6] * 1000, cd=u[..., 7] * 3, hero=u[..., 10] > 0.5, alive=u[..., 11] > 0.5,
                mp=u[..., 12] * 1000, food=u[..., 13] * 10, level=u[..., 14] * 10,
                owner=z["owner"], tid=z["type_id"], mask=z["mask"], t=np.concatenate([[0.0], np.cumsum(z["dt"])]))


def _live(d, s):
    return np.nonzero(d["mask"][s] & d["alive"][s] & np.isin(d["owner"][s], (0, 1)))[0]


def contact_step(d) -> int | None:
    for s in range(len(d["t"])):
        idx = _live(d, s)
        a = idx[d["owner"][s, idx] == 0]
        b = idx[d["owner"][s, idx] == 1]
        if len(a) and len(b):
            dx = d["x"][s, a][:, None] - d["x"][s, b][None, :]
            dy = d["y"][s, a][:, None] - d["y"][s, b][None, :]
            if (dx * dx + dy * dy).min() <= CONTACT * CONTACT:
                return s
    return None


def near_set(d, s) -> tuple[np.ndarray, np.ndarray]:
    idx = _live(d, s)
    a = idx[d["owner"][s, idx] == 0]
    b = idx[d["owner"][s, idx] == 1]
    if not len(a) or not len(b):
        return a, b
    dx = d["x"][s, a][:, None] - d["x"][s, b][None, :]
    dy = d["y"][s, a][:, None] - d["y"][s, b][None, :]
    close = (dx * dx + dy * dy) <= CONTACT * CONTACT
    eng = np.concatenate([a[close.any(axis=1)], b[close.any(axis=0)]])
    pts = eng if len(eng) else idx
    cx, cy = d["x"][s, pts].mean(), d["y"][s, pts].mean()
    keep = lambda arr: arr[np.hypot(d["x"][s, arr] - cx, d["y"][s, arr] - cy) <= NEAR_R]
    return keep(a), keep(b)


def _side_feats(d, s, idx, other_idx) -> tuple[dict, list]:
    types = [TYPE_CODES[int(t)] for t in d["tid"][s, idx]]
    hp = d["hp"][s, idx]
    ros = [(t, float(h)) for t, h in zip(types, hp)]
    oth = [(TYPE_CODES[int(t)], float(h)) for t, h in zip(d["tid"][s, other_idx], d["hp"][s, other_idx])]
    r = roster(ros, oth, UNIT_STATS)
    ehp = sum(x["hp"] for x in r)
    dps = sum(x["dps"] for x in r)
    hp_max = sum(float(h) / max(float(q), 1e-3) for h, q in zip(hp, d["ratio"][s, idx]))
    f = dict(n=len(idx), hp=float(hp.sum()), hp_frac=float(hp.sum() / hp_max) if hp_max > 0 else 0.0, ehp=ehp, dps=dps,
             heroes=int(d["hero"][s, idx].sum()), hero_lvl=float(d["level"][s, idx][d["hero"][s, idx]].sum()),
             food=float(d["food"][s, idx].sum()), ranged=int((d["rng"][s, idx] >= 300).sum()),
             mp=float(d["mp"][s, idx][d["hero"][s, idx]].sum()), value=sum(float((UNIT_STATS.get(t) or {}).get("gold") or 0)
                                                                          + float((UNIT_STATS.get(t) or {}).get("lumber") or 0) for t in types))
    return f, ros


def features_at(d, s) -> dict | None:
    a, b = near_set(d, s)
    if not len(a) or not len(b):
        return None
    fa, ra = _side_feats(d, s, a, b)
    fb, rb = _side_feats(d, s, b, a)
    sim, rev = simulate(ra, rb, UNIT_STATS), simulate(rb, ra, UNIT_STATS)
    out = {f"m_{k}": v for k, v in fa.items()}
    out.update({f"f_{k}": v for k, v in fb.items()})
    lr = lambda p, q: math.log((p + 1.0) / (q + 1.0))
    out.update(r_n=lr(fa["n"], fb["n"]), r_ehp=lr(fa["ehp"], fb["ehp"]), r_dps=lr(fa["dps"], fb["dps"]),
               r_power=lr(fa["ehp"] * fa["dps"], fb["ehp"] * fb["dps"]), r_value=lr(fa["value"], fb["value"]),
               r_food=lr(fa["food"], fb["food"]), d_heroes=fa["heroes"] - fb["heroes"], d_hero_lvl=fa["hero_lvl"] - fb["hero_lvl"],
               sim_win=float(sim["win"]), sim_left=sim["left"], sim_secs=sim["secs"], sim_rev_win=float(rev["win"]),
               sim_rev_left=rev["left"], sim_aliveB=sim["aliveB"], sim_rev_aliveB=rev["aliveB"])
    return out


def build_samples(rows: list[dict], ks=KS, drill_generator: str | None = None) -> list[dict]:
    """drill_generator：只要这个版本的演练场景（按场景名前缀 v2_ / v3_；v3 = 带迟到援兵）。"""
    out = []
    for r in rows:
        if drill_generator and r["source"] == "drill" and not str(r.get("scenario") or "").startswith(drill_generator + "_"):
            continue
        if r.get("left_arena"):                      # 演练里有单位跑出场地的场次：胜负标签不干净（data_index.left_arena）
            continue
        try:
            with np.load(r["path"]) as z:
                d = _decode(z)
        except Exception:  # noqa: BLE001
            continue
        s0 = contact_step(d)
        if s0 is None:
            continue
        # 演练多一个时刻 START：开场第 0 步，双方还没接上（推演在策略层里真正被调用的就是这种时刻）。
        # 真实对局的帧从接触才开始录，没有这个时刻（REQUESTS_TO_AI_SESSION.md 第 2 条）。
        moments = [(k, int(np.searchsorted(d["t"], d["t"][s0] + k))) for k in ks]
        if r["source"] == "drill" and s0 > 0:
            moments.append((START, 0))
        for k, s in moments:
            if s >= len(d["t"]):
                continue
            f = features_at(d, s)
            if f is None:
                continue
            group = r["group"] if r["source"] == "match" else f"drill_ep:{r['name']}"      # 演练场之间互相独立：按场分组
            out.append(dict(name=r["name"], source=r["source"], split=r["split"], group=group, k=k, big=bool(r.get("big")),
                            where=r.get("where"), feats=f, **{lab: bool(r[lab]) for lab in LABELS},
                            margin_value=r.get("margin_value"),
                            old_sim_win=_old_calib_win(r) if r["source"] == "match" else None))
    return out


def _old_calib_win(r) -> bool | None:
    """旧校准口径：账本 compM/compF 整场累计名单、满血（偷看未来，只为复现数字）。"""
    from .simulate import roster_from_counts
    if not r.get("compM") or not r.get("compF"):
        return None
    return bool(simulate(roster_from_counts(r["compM"], UNIT_STATS), roster_from_counts(r["compF"], UNIT_STATS), UNIT_STATS)["win"])


# ---------------------------------------------------------------- 模型与指标



def matrix(samples, names):
    return np.array([[float(s["feats"][n]) for n in names] for s in samples], dtype=np.float64)


class LogReg:
    def __init__(self, l2: float = 1.0, iters: int = 50):
        self.l2, self.iters = l2, iters

    def fit(self, X, y):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Xs = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        w = np.zeros(Xs.shape[1])
        reg = np.eye(Xs.shape[1]) * self.l2
        reg[0, 0] = 0.0
        for _ in range(self.iters):                                     # IRLS / 牛顿法
            p = 1 / (1 + np.exp(-np.clip(Xs @ w, -30, 30)))
            g = Xs.T @ (p - y) + reg @ w
            H = (Xs * (p * (1 - p))[:, None]).T @ Xs + reg
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        self.w = w
        return self

    def predict(self, X):
        Xs = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        return 1 / (1 + np.exp(-np.clip(Xs @ self.w, -30, 30)))


def auc(y, p):
    y = np.asarray(y, bool)
    pos, neg = p[y], p[~y]
    if not len(pos) or not len(neg):
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allp = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(allp):                                               # 并列取平均秩
        j = i
        while j + 1 < len(allp) and allp[j + 1] == allp[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def metrics(y, p) -> dict:
    y = np.asarray(y, float)
    pc = np.clip(p, 0.02, 0.98)
    bins = np.minimum((p * 10).astype(int), 9)
    ece = sum(abs(y[bins == b].mean() - p[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any())
    return dict(n=len(y), base=float(y.mean()), acc=float(((p >= 0.5) == (y > 0.5)).mean()), auc=auc(y, p),
                brier=float(((p - y) ** 2).mean()), logloss=float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()), ece=float(ece))


def bootstrap(samples, p, label, stat="brier", n=400, seed=0):
    groups = defaultdict(list)
    for i, s in enumerate(samples):
        groups[s["group"]].append(i)
    keys = list(groups)
    rng = random.Random(seed)
    vals = []
    y = np.array([s[label] for s in samples], float)
    for _ in range(n):
        idx = [i for g in (rng.choice(keys) for _ in keys) for i in groups[g]]
        m = metrics(y[idx], p[idx])
        if not math.isnan(m[stat]):
            vals.append(m[stat])
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]) if vals else (float("nan"), float("nan"))


def calibration_table(y, p, bins=5) -> list[tuple]:
    y = np.asarray(y, float)
    out = []
    edges = np.linspace(0, 1, bins + 1)
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= 1))
        if m.any():
            out.append((f"{edges[i]:.1f}~{edges[i + 1]:.1f}", int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return out


# ---------------------------------------------------------------- 实验

def feature_names(samples):
    return sorted(samples[0]["feats"])


SIM_CAL = ("sim_left", "sim_rev_left", "sim_win", "sim_rev_win", "sim_secs")
MODEL_NAMES = ("base", "sim", "sim_cal", "lr", "gbdt")


def fit_models(tr: list[dict], label: str) -> dict:
    """在 tr 上拟合全部模型，返回 {名字: 预测函数}。"""
    names = feature_names(tr)
    y = np.array([s[label] for s in tr], float)
    base = float(y.mean())
    models = {"base": lambda S: np.full(len(S), base),
              "sim": lambda S: np.array([0.98 if s["feats"]["sim_win"] > 0.5 else 0.02 for s in S])}
    if len(set(y)) < 2:
        return models
    cal = LogReg(l2=1.0).fit(matrix(tr, SIM_CAL), y)
    models["sim_cal"] = lambda S, m=cal: m.predict(matrix(S, SIM_CAL))
    lr = LogReg(l2=3.0).fit(matrix(tr, names), y)
    models["lr"] = lambda S, m=lr: m.predict(matrix(S, names))
    try:
        import lightgbm as lgb
        gb = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=20,
                                subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1)
        gb.fit(matrix(tr, names), y.astype(int))
        models["gbdt"] = lambda S, m=gb: m.predict_proba(matrix(S, names))[:, 1]
    except Exception:  # noqa: BLE001
        pass
    return models


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 5:
        return float("nan")
    ra = np.argsort(np.argsort(a[ok]))
    rb = np.argsort(np.argsort(b[ok]))
    return float(np.corrcoef(ra, rb)[0, 1])


def grouped_folds(samples, n_folds=5, seed=0):
    groups = sorted({s["group"] for s in samples})
    rng = random.Random(seed)
    rng.shuffle(groups)
    fold_of = {g: i % n_folds for i, g in enumerate(groups)}
    return [fold_of[s["group"]] for s in samples]


def cv_predict(samples, label, n_folds=5) -> dict[str, np.ndarray]:
    folds = grouped_folds(samples, n_folds)
    out = {m: np.full(len(samples), np.nan) for m in MODEL_NAMES}
    for f in range(n_folds):
        tr = [s for s, k in zip(samples, folds) if k != f]
        te_idx = [i for i, k in enumerate(folds) if k == f]
        if not te_idx:
            continue
        models = fit_models(tr, label)
        te = [samples[i] for i in te_idx]
        for m, fn in models.items():
            out[m][te_idx] = fn(te)
    return {m: p for m, p in out.items() if not np.isnan(p).any()}


def cv_predict_mixed(real: list[dict], drill: list[dict], label: str, n_folds: int = 5) -> dict[str, np.ndarray]:
    """真实数据按局分折；每一折训练 = 其余真实折 + 全部演练（特征里有 is_drill 标记），只评真实折外。
    回答「演练数据能不能帮真实对局」—— 只用演练训练的迁移在 09-17 失败了（AUC 0.61，校准很差）。"""
    folds = grouped_folds(real, n_folds)
    out = {m: np.full(len(real), np.nan) for m in MODEL_NAMES}
    for f in range(n_folds):
        tr = [s for s, k in zip(real, folds) if k != f] + drill
        te_idx = [i for i, k in enumerate(folds) if k == f]
        models = fit_models(tr, label)
        te = [real[i] for i in te_idx]
        for m, fn in models.items():
            out[m][te_idx] = fn(te)
    return {m: p for m, p in out.items() if not np.isnan(p).any()}


def export_models(real: list[dict], path: Path = Path(__file__).with_name("models") / "winpred_lr_v0.json") -> Path:
    """每个 (标签, k) 用全部真实大仗拟合一个逻辑回归，存 mu/sd/w/特征名。几 KB，进 git（可复现、winpred_infer 直接读）。"""
    from datetime import datetime
    entries = []
    for label in LABELS:
        for k in KS:
            S = [s for s in real if s["k"] == k]
            if len(S) < 50:
                continue
            names = feature_names(S)
            y = np.array([s[label] for s in S], float)
            m = LogReg(l2=3.0).fit(matrix(S, names), y)
            entries.append(dict(label=label, k=k, n=len(S), positives=int(y.sum()), features=names,
                                mu=m.mu.tolist(), sd=m.sd.tolist(), w=m.w.tolist()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(version="v0", fitted_at=datetime.now().isoformat(timespec="seconds"),
                                    note="真实大仗（双方各>=6）、按 winpred.features_at 的近处单位特征、LogReg l2=3；k = 接触后秒数",
                                    models=entries), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"导出 {len(entries)} 个模型 -> {path}")
    return path


def table(lines, title, S, label, preds: dict):
    y = np.array([s[label] for s in S], float)
    margin = [s.get("margin_value") if s.get("margin_value") is not None else float("nan") for s in S]
    lines += [f"**{title}**：n={len(S)}、正例 {int(y.sum())}、组 {len({s['group'] for s in S})}", "",
              "| 模型 | 准确率 | AUC [95%CI] | Brier [95%CI] | 对数损失 | ECE | 秩相关(分数, 价值战损差) |", "|---|---|---|---|---|---|---|"]
    for m, p in preds.items():
        met = metrics(y, p)
        blo, bhi = bootstrap(S, p, label, "brier")
        alo, ahi = bootstrap(S, p, label, "auc")
        lines.append(f"| {m} | {met['acc']:.3f} | {met['auc']:.3f} [{alo:.2f}, {ahi:.2f}] | {met['brier']:.3f} [{blo:.3f}, {bhi:.3f}] | "
                     f"{met['logloss']:.3f} | {met['ece']:.3f} | {spearman(p, margin):.3f} |")
    lines.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--out", default=str(REPORT))
    ap.add_argument("--drill-generator", default=None, help="只用某个版本的演练场景（v2 / v3），缓存和报告文件名带上版本")
    ap.add_argument("--export", action="store_true", help="用全部真实大仗拟合逻辑回归，写 models/winpred_lr_v0.json（winpred_infer 读）")
    args = ap.parse_args()
    rows = [json.loads(l) for l in INDEX.read_text(encoding="utf-8").splitlines()]
    cache = CACHE if not args.drill_generator else CACHE.with_name(f"samples_v0_{args.drill_generator}.jsonl")
    if args.drill_generator and args.out == str(REPORT):
        args.out = str(REPORT.with_name(f"m1_v0_{args.drill_generator}.md"))
    if args.rebuild or not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        samples = build_samples(rows, drill_generator=args.drill_generator)
        with open(cache, "w", encoding="utf-8") as fh:
            for s in samples:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    samples = [json.loads(l) for l in cache.read_text(encoding="utf-8").splitlines()]
    for s in samples:
        s["feats"]["is_drill"] = float(s["source"] == "drill")
    real = [s for s in samples if s["source"] == "match" and s["big"]]
    drill = [s for s in samples if s["source"] == "drill"]
    if args.export:
        export_models(real)
        return
    lines = ["# M1 v0：胜负预测 vs W3-Simulate", "",
             f"样本 {len(samples)}：真实大仗 {len(real)}（{len({s['group'] for s in real})} 局）、演练 {len(drill)}。"
             f"时刻 k = 接触后 {', '.join(f'{k:g}' for k in KS)} 游戏秒（演练另有开场）；输入 = 那一刻接触重心 {NEAR_R:g} 内的双方单位（不偷看后来的援兵）。", ""]
    old = [r for r in rows if r["source"] == "match" and r.get("big")]
    oldw = [r for r in old if _old_calib_win(r)]
    lines += ["## 旧校准口径复现（整场累计名单满血，偷看未来，只作对照）", "",
              f"大仗 {len(old)} 场，推演判赢 {len(oldw)} 场，其中实战 win_value {sum(r['win_value'] for r in oldw)}、"
              f"win_wipe {sum(r['win_wipe'] for r in oldw)}（09-16 旧校准 94 场：判赢 39、实战赢 12）", ""]
    for label in LABELS:
        lines += [f"## 标签 {label}", ""]
        for k in (START,) + KS:
            lines += ["### 开场（接触之前，只有演练有）" if k == START else f"### 接触后 {k:g} 秒", ""]
            rk = [s for s in real if s["k"] == k and s["split"] != "future"]
            if len(rk) >= 50:
                table(lines, "真实大仗 · 按局 5 折交叉验证（train+val+test，折外预测合并）", rk, label, cv_predict(rk, label))
            fut = [s for s in real if s["k"] == k and s["split"] == "future"]
            if len(fut) >= 10 and len(rk) >= 50:
                models = fit_models(rk, label)
                table(lines, "真实大仗 · 时间外推（用冻结前全部真实数据训练，评 future）", fut, label, {m: fn(fut) for m, fn in models.items()})
            dk = [s for s in drill if s["k"] == k]
            if len(dk) >= 50:
                table(lines, "演练 · 按场 5 折交叉验证", dk, label, cv_predict(dk, label))
                if len(rk) >= 50:
                    models = fit_models([s for s in dk], label)
                    table(lines, "迁移：只用演练训练，评真实大仗（train+val+test）", rk, label, {m: fn(rk) for m, fn in models.items()})
                    table(lines, "混合：真实按局 5 折，每折训练加上全部演练（带 is_drill 标记），评真实折外", rk, label,
                          cv_predict_mixed(rk, dk, label))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

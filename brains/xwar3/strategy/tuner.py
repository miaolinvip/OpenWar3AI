# -*- coding: utf-8 -*-
"""自动调参：多个跑批实例上用高斯过程贝叶斯优化（GP-BO）试大脑的战术参数，对手是疯狂电脑。

09-18 定下的学习路线「胜率模型 + 自动调参」的后一半。大脑里出击/打野/支援/撤退的门槛都是凭经验拍的
（出击 1.25 倍、打野 0.6 倍、支援 0.8 倍……），这里让跑批实例自己试：

  每个实例打完一局 -> 按对局文件打分 -> 高斯过程拟合「参数 -> 得分」-> 挑下一组（UCB）-> 写进该实例的 brain_config.json
  大脑开局读 brain_config.json（brain/config.py），并把这一局用的覆盖项记进对局文件的 experiment 记录 ——
  **得分按对局文件里记的那组参数认**，不按「我以为那个实例在跑哪组」认（写晚了、局中改了都分得清）。

得分（score_match）：赢 = 1，否则 0；再加 0.5 × 整局平均「兵力占比」own/(own+foe)（每 10 游戏秒一个 state 记录）。
  只看输赢太稀疏（一小时几十局、大多是输），兵力占比是和输赢同向的稠密信号：输得体面的参数比一边倒被推平的好。
  crash / ended-stuck / ended-by-operator 不算（结局不可信）；局中改过配置（有「配置更新」记录）的不算。

优化器（BO）：参数归一到 [0,1]^d；前 n_init 个点用 Sobol 撒（第 0 个 = 默认参数）；之后 GP（Matern 2.5 + 白噪声）+ UCB，
  同时在跑的点用「常数谎言」占位（别让几个实例试同一个地方）；每 REEVAL_EVERY 次挑一次「后验均值最高的已试点」重测
  （单局噪声大，冠军要多测几局才算数），默认参数也定期重测到 BASELINE_N 局，报告里才有得比。

数据：war3-brain/tuning/<study>/trials.jsonl（只追加）+ report.md。重启服务从 trials.jsonl 接着来。

用法：
  python tuner.py --study s1 --insts 6-11            # 常驻（Start-Process 起成独立进程）
  python tuner.py --study s1 --report                # 打印/写报告
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time

import warnings

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
BIN = os.environ.get("WAR3_BIN", w3paths.BIN)
TUNING = os.path.join(HERE, "tuning")

# (brain_config 的键, 下限, 上限, 类型)。范围在 config.SCHEMA 的合法范围之内、围着默认值放开。
# ★ 09-18 晚换成经济参数：s10（343 局）试了 9 个战术门槛（出击倍数/打野战力比/支援/撤退/英雄血线/进攻性下限……），
#   GP 收敛回默认附近、胜率 3.8%，门槛不是瓶颈；复盘看到的差距在经济和节奏（工人卡 13、木头堆 900、二本 10 分钟），
#   外加打野开打门槛（前 15 分钟 31% 的拍在集合点「等人到齐」）。战术那几个留在 brain_config 默认值上。
SPACE = [
    ("peons_lumber", 3, 8, "int"),               # 默认 5（人族表，09-18 从 8 降）
    ("lumber_high", 200, 1000, "int"),           # 400（木头到这么多伐木减半）
    ("tier2_army", 0, 10, "int"),                # 4（兵够这么多升二本排到出兵前面）
    ("expand_army", 0, 10, "int"),               # 4（兵够这么多第二矿插到出兵前面）
    ("creep_engage_power", 1.0, 3.0, "float"),   # 2.0（营地边到场的人战力比够这个才开打）
]
WIN_OUTCOMES = {"victory"}
LOSS_OUTCOMES = {"defeated", "conceded-weak", "timeout"}
SHARE_W = 0.5
REEVAL_EVERY = 5
BASELINE_EVERY = 8
BASELINE_N = 6
KAPPA = 1.5
PENDING_TIMEOUT_S = 45 * 60


# ---------------------------------------------------------------- 打分

def score_match(path: str) -> dict | None:
    """-> None（还没打完）或 {match, outcome, valid, score, share, minutes, config, why}。"""
    cfg, states, outcome, minutes, finalized, changed = None, [], None, None, False, False
    try:
        with open(path, "rb") as fh:                   # 先看尾巴：没有 finalized 就是还在打，不整份读（每 3 秒轮一遍所有实例）
            fh.seek(max(0, os.path.getsize(path) - 4096))
            if b'"finalized"' not in fh.read():
                return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                k, d = r.get("kind"), r.get("data") or {}
                if k == "experiment":
                    cfg = d.get("config")
                elif k == "state":
                    states.append((float(d.get("ownStr") or 0.0), float(d.get("foeStr") or 0.0)))
                elif k == "result":
                    outcome, minutes = d.get("outcome"), d.get("minutes")
                elif k == "action" and str(d.get("text", "")).startswith("配置更新"):
                    changed = True
                elif k == "finalized":
                    finalized = True
    except OSError:
        return None
    if not finalized:
        return None
    sh = [o / (o + f) for o, f in states if o + f > 0]
    share = sum(sh) / len(sh) if sh else 0.0
    out = dict(match=os.path.basename(path), outcome=outcome, share=round(share, 4), minutes=minutes, config=cfg, valid=True, why="")
    if cfg is None:
        out.update(valid=False, why="对局文件没有 config（旧版大脑）")
    elif changed:
        out.update(valid=False, why="局中改过配置")
    elif outcome in WIN_OUTCOMES:
        out["score"] = round(1.0 + SHARE_W * share, 4)
    elif outcome in LOSS_OUTCOMES:
        out["score"] = round(SHARE_W * share, 4)
    else:
        out.update(valid=False, why=f"结局不可信：{outcome}")
    return out


# ---------------------------------------------------------------- 参数空间

def defaults() -> dict:
    sys.path.insert(0, HERE)
    from brain import config as CFG
    return {k: CFG.DEFAULTS[k] for k, *_ in SPACE}


def to_unit(cfg: dict, base: dict | None = None) -> np.ndarray:
    base = base or defaults()
    x = []
    for k, lo, hi, _t in SPACE:
        v = float(cfg.get(k, base[k]))
        x.append(min(1.0, max(0.0, (v - lo) / (hi - lo))))
    return np.array(x)


def from_unit(x) -> dict:
    out = {}
    for (k, lo, hi, t), u in zip(SPACE, x):
        v = lo + float(u) * (hi - lo)
        out[k] = int(round(v)) if t == "int" else round(v, 3)
    return out


def same_config(a: dict, b: dict, tol: float = 1e-3) -> bool:
    """两组覆盖项在调参空间上是不是同一组（对局文件里记的是 coerce 过的值，写进去的是 round 过的，差一点点）。"""
    base = defaults()
    return bool(np.all(np.abs(to_unit(a or {}, base) - to_unit(b or {}, base)) <= tol))


# ---------------------------------------------------------------- 优化器

class BO:
    def __init__(self, d: int, seed: int = 0, n_init: int | None = None, kappa: float = KAPPA):
        self.d = d
        self.rng = np.random.default_rng(seed)
        self.n_init = n_init if n_init is not None else max(8, 2 * d)
        self.kappa = kappa
        from scipy.stats import qmc
        self.sobol = qmc.Sobol(d, scramble=True, seed=seed).random(64)

    def _gp(self, X, y, kernel=None):
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.gaussian_process import GaussianProcessRegressor
        warnings.filterwarnings("ignore", category=ConvergenceWarning)   # 噪声/长度尺度顶到边界很常见，不刷屏
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        if kernel is None:
            kernel = (ConstantKernel(0.1, (1e-3, 10.0)) * Matern(length_scale=[0.4] * self.d, length_scale_bounds=(0.05, 5.0), nu=2.5)
                      + WhiteKernel(0.05, (1e-4, 1.0)))
            gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=2, random_state=0)
        else:
            gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, optimizer=None)
        gp.fit(X, y)
        return gp

    def best_mean(self, X, y):
        """已试过的点里后验均值最高的那个 -> (x, 均值)。"""
        if len(X) < 3:
            i = int(np.argmax(y))
            return X[i], float(y[i])
        gp = self._gp(np.asarray(X), np.asarray(y))
        mu = gp.predict(np.asarray(X))
        i = int(np.argmax(mu))
        return np.asarray(X)[i], float(mu[i])

    def suggest(self, X, y, pending, n_asked: int) -> np.ndarray:
        """X/y：已完成的点和得分；pending：正在跑的点；n_asked：一共问过几次（决定走撒点还是 GP）。"""
        if n_asked < self.n_init or len(X) < 4:
            return self.sobol[n_asked % len(self.sobol)]
        X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        gp = self._gp(X, y)
        if pending:
            P = np.asarray(pending, dtype=float)
            gp = self._gp(np.vstack([X, P]), np.concatenate([y, np.full(len(P), float(np.mean(y)))]), kernel=gp.kernel_)
        top = X[np.argsort(-y)[:5]]
        cand = np.vstack([self.rng.random((3000, self.d)),
                          np.clip(np.repeat(top, 200, axis=0) + self.rng.normal(0, 0.08, (len(top) * 200, self.d)), 0, 1)])
        mu, sd = gp.predict(cand, return_std=True)
        return cand[int(np.argmax(mu + self.kappa * sd))]


# ---------------------------------------------------------------- 研究（持久化 + 调度）

class Study:
    def __init__(self, name: str, seed: int = 0):
        self.name = name
        self.dir = os.path.join(TUNING, name)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "trials.jsonl")
        self.rows: list[dict] = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.rows = [json.loads(ln) for ln in fh if ln.strip()]
        self.base = defaults()
        self.bo = BO(len(SPACE), seed=seed)

    def _append(self, row: dict) -> None:
        self.rows.append(row)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 视图
    def results(self) -> list[dict]:
        return [r for r in self.rows if r["kind"] == "result" and r.get("valid")]

    def pending(self) -> dict:
        """实例 -> 最近一次派给它、还没结果的 trial 行。"""
        out = {}
        done = {r["trial"] for r in self.rows if r["kind"] in ("result", "expired") and r.get("trial") is not None}
        for r in self.rows:
            if r["kind"] == "ask" and r["trial"] not in done:
                out[r["inst"]] = r
        return out

    def seen_matches(self) -> set:
        return {r["match"] for r in self.rows if r["kind"] in ("result", "skip")}

    def n_asked(self) -> int:
        return sum(1 for r in self.rows if r["kind"] == "ask")

    def xy(self):
        res = self.results()
        return [to_unit(r["config"], self.base) for r in res], [r["score"] for r in res]

    # 下一组
    def ask(self, inst: int) -> dict:
        X, y = self.xy()
        n = self.n_asked()
        tag = "bo"
        base_n = sum(1 for r in self.results() if same_config(r["config"], {}))
        if n == 0 or (n % BASELINE_EVERY == 0 and base_n < BASELINE_N):
            x, tag = to_unit({}, self.base), "默认参数"
        elif n >= self.bo.n_init and n % REEVAL_EVERY == 0 and len(X) >= 4:
            x, _mu = self.bo.best_mean(X, y)
            tag = "冠军重测"
        else:
            pend = [to_unit(p["config"], self.base) for p in self.pending().values()]
            x = self.bo.suggest(X, y, pend, n)
            tag = "撒点" if n < self.bo.n_init else "GP-UCB"
        cfg = from_unit(x)
        trial = n
        row = dict(kind="ask", trial=trial, inst=inst, config=cfg, tag=tag, t=time.time())
        self._append(row)
        return row

    def tell(self, inst: int, sc: dict, trial: int | None) -> None:
        self._append(dict(kind="result" if sc.get("valid") else "skip", trial=trial, inst=inst, t=time.time(), **sc))

    def expire(self, row: dict) -> None:
        self._append(dict(kind="expired", trial=row["trial"], inst=row["inst"], t=time.time()))


KEEP_KEYS = ("opening",)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(inst: int, cfg: dict, meta: dict | None = None) -> None:
    """写该实例的 brain_config.json；meta（{study, trial, tag}）同时写进 tune_meta.json ——
    大脑开局把它记进对局文件 experiment.tuning（run_brain.tune_meta），Farsight 对局页显示「调参 s12 · 第 N 组」。
    ★ 09-18 s12 这类标记和代码版本容易混淆，远见后台也看不出来 —— 原来对局记录里只有参数，没有是哪批实验、第几组。"""
    d = os.path.join(BIN, f"inst{inst}")
    os.makedirs(d, exist_ok=True)
    # 人手选的项（开局谱）不是调参维度：原样留着，不被这组参数整份覆盖掉（tune_meta 只记调参的那几项，run_brain.tune_meta 核对时也不看它们）
    keep = {k: v for k, v in _read_json(os.path.join(d, "brain_config.json")).items() if k in KEEP_KEYS and k not in cfg}
    for name, data in (("brain_config.json", dict(cfg, **keep)), ("tune_meta.json", dict(meta or {}, config=cfg, wall=time.time()))):
        if name == "tune_meta.json" and meta is None:
            continue
        p = os.path.join(d, name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)


AFTER = ""        # --after：只认文件名时刻（match-YYYYMMDD-HHMMSS）>= 这个的对局 —— 大脑代码中途换了，旧代码打的局不混进来


def finished_matches(inst: int, since: float) -> list[str]:
    ps = glob.glob(os.path.join(BIN, f"inst{inst}", "matches", "match-*.jsonl"))
    return sorted(p for p in ps if os.path.getmtime(p) >= since and os.path.basename(p)[6:21] >= AFTER)


def step(study: Study, insts: list[int], since: float, log=print) -> None:
    seen = study.seen_matches()
    pend = study.pending()
    for inst in insts:
        for p in finished_matches(inst, since):
            name = os.path.basename(p)
            if name in seen:
                continue
            sc = score_match(p)
            if sc is None:
                continue                                 # 还在打
            row = pend.get(inst)
            trial = row["trial"] if (row and sc.get("config") is not None and same_config(sc["config"], row["config"])) else None
            study.tell(inst, sc, trial)
            seen.add(name)
            log(f"inst{inst} {name}：{sc['outcome']} 得分 {sc.get('score')} 兵力占比 {sc['share']}"
                f"{'' if sc['valid'] else '（不算：' + sc['why'] + '）'}｜trial {trial}")
            if trial is not None:
                pend.pop(inst, None)
        row = pend.get(inst)
        if row is not None and time.time() - row["t"] > PENDING_TIMEOUT_S:
            log(f"inst{inst} trial {row['trial']} 等了 {PENDING_TIMEOUT_S // 60} 分钟没结果，作废重派")
            study.expire(row)
            row = None
        if row is None:
            row = study.ask(inst)
            write_config(inst, row["config"], dict(study=study.name, trial=row["trial"], tag=row["tag"]))
            log(f"inst{inst} 派 trial {row['trial']}（{row['tag']}）：{row['config']}")


# ---------------------------------------------------------------- 报告

def report(study: Study) -> str:
    res = study.results()
    lines = [f"# 自动调参 {os.path.basename(study.dir)}", "",
             f"有效局 {len(res)}（不算的 {sum(1 for r in study.rows if r['kind'] == 'skip')}），派过 {study.n_asked()} 组。", ""]
    if not res:
        return "\n".join(lines + ["还没有结果。"])
    groups: dict = {}
    for r in res:
        key = json.dumps(from_unit(to_unit(r["config"], study.base)), sort_keys=True)
        groups.setdefault(key, []).append(r)
    base_key = json.dumps(from_unit(to_unit({}, study.base)), sort_keys=True)
    rows = []
    for k, rs in groups.items():
        rows.append((sum(x["score"] for x in rs) / len(rs), len(rs), sum(1 for x in rs if x["outcome"] in WIN_OUTCOMES), k))
    rows.sort(key=lambda t: -t[0])
    b = next((t for t in rows if t[3] == base_key), None)
    wins = sum(1 for r in res if r["outcome"] in WIN_OUTCOMES)
    lines.append(f"总胜率 {wins}/{len(res)}；默认参数 " + (f"{b[2]}/{b[1]} 胜、平均得分 {b[0]:.3f}" if b else "还没测到"))
    X, y = study.xy()
    if len(X) >= 4:
        x, mu = study.bo.best_mean(X, y)
        lines += ["", f"GP 认为最好的已试组（后验均值 {mu:.3f}）：`{from_unit(x)}`"]
    lines += ["", "| 平均得分 | 局数 | 胜 | 参数 |", "|---|---|---|---|"]
    for m, n, w, k in rows[:15]:
        lines.append(f"| {m:.3f} | {n} | {w} | `{k}`{' ← 默认' if k == base_key else ''} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", required=True)
    ap.add_argument("--insts", default="6-11")
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--after", default="", help="只认这个时刻之后开局的对局，格式 20260918-124000")
    a = ap.parse_args(argv)
    global AFTER
    AFTER = a.after
    study = Study(a.study, seed=a.seed)
    if a.report:
        txt = report(study)
        with open(os.path.join(study.dir, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
        print(txt)
        return 0
    lo, _, hi = a.insts.partition("-")
    insts = list(range(int(lo), int(hi or lo) + 1))
    since = time.time() - 60                       # 只认服务起来之后打完的局（前 1 分钟内结束的也算）
    end = time.time() + a.hours * 3600

    def log(s):
        print(time.strftime("%H:%M:%S"), s, flush=True)

    log(f"调参 {a.study} 实例 {insts}，参数 {[k for k, *_ in SPACE]}，已有 {len(study.rows)} 行"
        f"{'，只认 ' + AFTER + ' 之后开局的' if AFTER else ''}")
    last_report = 0.0
    while time.time() < end:
        try:
            step(study, insts, since, log)
            if time.time() - last_report > 600:
                last_report = time.time()
                with open(os.path.join(study.dir, "report.md"), "w", encoding="utf-8") as fh:
                    fh.write(report(study) + "\n")
        except Exception as exc:  # noqa: BLE001
            import traceback
            log("出错：" + traceback.format_exc())
        time.sleep(3.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())

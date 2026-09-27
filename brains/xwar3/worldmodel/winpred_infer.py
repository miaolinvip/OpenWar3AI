"""
M1 推理接口：给一组单位（接触那一刻或之后 k 秒的局面），返回「这仗打不打得赢」的概率 + 同一输入下 W3-Simulate 的结论。

    from worldmodel.winpred_infer import predict
    predict([dict(type="hfoo", owner=0, x=0, y=0, hp=420, hp_max=420), ...], k=5.0)

    python -m worldmodel.winpred --export        # 用全部真实大仗重新拟合，写 worldmodel/models/winpred_lr_v0.json

输入单位字典：type（四字符码）、owner（0 = 我方 / 1 = 敌方；其他忽略）、x、y、hp、hp_max；英雄可带 level、mp。
特征计算**直接复用 winpred.features_at**（把单位列表拼成一帧），不另写一份 —— 训练和推理同一口径。
近处单位的筛法同训练：接触中单位的重心 1500 以内。

输出：
  p_win_value / p_win_wipe   逻辑回归概率（按 k 选最近的那个模型：接触后 0 / 5 / 10 秒）
  sim_win / sim_left          同一批近处单位、当时血量的 W3-Simulate 结论（simulate.py，和 PowerShell 原版一致）
  n_near                      参与计算的近处单位数（我方, 敌方）
⚠ 09-17 M1 结论：接触那一刻（k=0）模型也没显著好过基率；接触后 5~10 秒才可靠（AUC 0.77~0.83）。用于「打起来之后撤不撤」，不是「出不出门」。
⚠ 真实对局的胜负基率在漂（val 26% -> test/future 6~9%），接进策略层要定期用最近的对局重新 --export。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .vocab import UNIT_STATS, type_index
from .winpred import LogReg, features_at, near_set

MODEL_PATH = Path(__file__).with_name("models") / "winpred_lr_v0.json"
_CACHE: dict = {}


def units_to_frame(units: list[dict]) -> dict:
    """单位字典列表 -> winpred 的一帧（形状 [1, N]），喂 features_at(d, 0)。"""
    us = [u for u in units if u.get("owner") in (0, 1) and float(u.get("hp", 0)) > 0]
    n = len(us)

    def st(t, key, default=0.0):
        v = (UNIT_STATS.get(t) or {}).get(key)
        return float(v) if v not in (None, "") else float(default)
    arr = lambda vals, dtype=np.float32: np.asarray(vals, dtype=dtype).reshape(1, n)
    return dict(
        x=arr([float(u["x"]) for u in us]), y=arr([float(u["y"]) for u in us]),
        hp=arr([float(u["hp"]) for u in us]),
        ratio=arr([float(u["hp"]) / max(1.0, float(u.get("hp_max") or st(u["type"], "hp", u["hp"]))) for u in us]),
        armor=arr([st(u["type"], "armor") for u in us]), dmg=arr([st(u["type"], "dmgAvg") for u in us]),
        rng=arr([st(u["type"], "range", 100) for u in us]), cd=arr([st(u["type"], "cool", 1.5) for u in us]),
        hero=arr([bool(u["type"][:1].isupper()) for u in us], bool), alive=np.ones((1, n), bool),
        mp=arr([float(u.get("mp") or 0.0) for u in us]),
        food=arr([st(u["type"], "food") for u in us]),
        level=arr([float(u.get("level") or st(u["type"], "level")) for u in us]),
        owner=np.asarray([[int(u["owner"]) for u in us]], np.int8), tid=np.asarray([[type_index(u["type"]) for u in us]], np.int32),
        mask=np.ones((1, n), bool), t=np.zeros(1))


def load_model(path: Path = MODEL_PATH) -> dict:
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = json.loads(Path(path).read_text(encoding="utf-8"))
    return _CACHE[key]


def _lr_predict(entry: dict, feats: dict) -> float:
    m = LogReg()
    m.mu, m.sd, m.w = np.asarray(entry["mu"]), np.asarray(entry["sd"]), np.asarray(entry["w"])
    x = np.asarray([[float(feats.get(n, 0.0)) for n in entry["features"]]])
    return float(m.predict(x)[0])


def predict(units: list[dict], k: float = 5.0, model: dict | None = None) -> dict:
    model = model or load_model()
    d = units_to_frame(units)
    a, b = near_set(d, 0)
    feats = features_at(d, 0)
    if feats is None:
        return dict(p_win_value=None, p_win_wipe=None, sim_win=None, sim_left=None, n_near=(len(a), len(b)))
    feats["is_drill"] = 0.0
    ks = sorted({e["k"] for e in model["models"]})
    kk = min(ks, key=lambda v: abs(v - k))
    out = dict(k_model=kk, n_near=(len(a), len(b)), sim_win=bool(feats["sim_win"] > 0.5), sim_left=feats["sim_left"])
    for e in model["models"]:
        if e["k"] == kk:
            out[f"p_{e['label']}"] = _lr_predict(e, feats)
    return out

# -*- coding: utf-8 -*-
"""自动调参（tuner.py）：打分、按对局文件认参数、优化器能往好的方向走。"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tuner as TU  # noqa: E402


def write_match(path, outcome="defeated", config=None, states=((10, 10), (30, 10)), changed=False, finalized=True):
    rows = [dict(kind="start", tick=0, data={})]
    if config is not None:
        rows.append(dict(kind="experiment", tick=0, data={"config": config}))
    for i, (o, f) in enumerate(states):
        rows.append(dict(kind="state", tick=i, data={"ownStr": o, "foeStr": f}))
    if changed:
        rows.append(dict(kind="action", tick=1, data={"text": "配置更新（第 2 版）：{}"}))
    rows.append(dict(kind="result", tick=2, data={"outcome": outcome, "minutes": 12.0}))
    if finalized:
        rows.append(dict(kind="finalized", tick=2, data={}))
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


class Score(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "match-x.jsonl")

    def test_loss_scores_share(self):
        write_match(self.p, "defeated", {})
        sc = TU.score_match(self.p)
        self.assertTrue(sc["valid"])
        self.assertAlmostEqual(sc["share"], (0.5 + 0.75) / 2)
        self.assertAlmostEqual(sc["score"], 0.5 * 0.625)

    def test_win_adds_one(self):
        write_match(self.p, "victory", {"attack_margin": 1.1})
        self.assertAlmostEqual(TU.score_match(self.p)["score"], 1.0 + 0.5 * 0.625)

    def test_not_finished(self):
        write_match(self.p, "defeated", {}, finalized=False)
        self.assertIsNone(TU.score_match(self.p))

    def test_invalid_cases(self):
        for kw, why in ((dict(outcome="crash", config={}), "结局不可信"), (dict(config=None), "config"),
                        (dict(config={}, changed=True), "局中改过配置")):
            write_match(self.p, **kw)
            sc = TU.score_match(self.p)
            self.assertFalse(sc["valid"], kw)
            self.assertIn(why, sc["why"])


class Space(unittest.TestCase):
    def test_default_roundtrip(self):
        x = TU.to_unit({})
        self.assertTrue(TU.same_config(TU.from_unit(x), {}))
        self.assertEqual(TU.from_unit(x)["peons_lumber"], 5)          # 09-18 晚调参空间换成经济参数

    def test_written_config_matches_recorded(self):
        cfg = TU.from_unit(np.full(len(TU.SPACE), 0.37))
        sys.path.insert(0, TU.HERE)
        from brain import config as CFG
        recorded = {k: CFG.coerce(k, v) for k, v in cfg.items()}      # 大脑读回来是 coerce 过的
        self.assertTrue(TU.same_config(recorded, cfg))
        self.assertFalse(TU.same_config({}, cfg))


class Optimizer(unittest.TestCase):
    def test_finds_better_region_than_sobol_start(self):
        rng = np.random.default_rng(1)
        opt = np.array([0.8, 0.2, 0.6])

        def f(x):
            return float(1.0 - np.sum((x - opt) ** 2) + rng.normal(0, 0.05))

        bo = TU.BO(3, seed=0, n_init=6)
        X, y = [], []
        for n in range(30):
            x = bo.suggest(X, y, [], n)
            X.append(x)
            y.append(f(x))
        best, _mu = bo.best_mean(X, y)
        self.assertLess(float(np.linalg.norm(best - opt)), 0.35, best)
        self.assertGreater(np.mean(y[-10:]), np.mean(y[:6]))


class Flow(unittest.TestCase):
    def test_ask_write_tell(self):
        with tempfile.TemporaryDirectory() as d:
            TU.TUNING, TU.BIN = os.path.join(d, "tuning"), os.path.join(d, "bin")
            st = TU.Study("t")
            logs = []
            TU.step(st, [6], 0.0, logs.append)
            cfg_path = os.path.join(TU.BIN, "inst6", "brain_config.json")
            written = json.load(open(cfg_path, encoding="utf-8"))
            self.assertTrue(TU.same_config(written, {}))                  # 第 0 组 = 默认参数
            os.makedirs(os.path.join(TU.BIN, "inst6", "matches"))
            write_match(os.path.join(TU.BIN, "inst6", "matches", "match-a.jsonl"), "victory", written)
            TU.step(st, [6], 0.0, logs.append)
            res = st.results()
            self.assertEqual([(r["trial"], r["outcome"]) for r in res], [(0, "victory")])
            self.assertEqual(sorted(st.pending()), [6])                   # 马上派了下一组
            self.assertEqual(st.pending()[6]["trial"], 1)
            # 下一局开局早于写配置：对局里记的还是旧参数 -> 结果照记（按记下的参数认），trial 1 不算完成
            write_match(os.path.join(TU.BIN, "inst6", "matches", "match-b.jsonl"), "defeated", written)
            TU.step(st, [6], 0.0, logs.append)
            self.assertEqual([r["trial"] for r in st.results()], [0, None])
            self.assertEqual(st.pending()[6]["trial"], 1)
            self.assertIn("默认参数", TU.report(st))

    def test_after_filters_old_code_matches(self):
        with tempfile.TemporaryDirectory() as d:
            TU.BIN = os.path.join(d, "bin")
            m = os.path.join(TU.BIN, "inst6", "matches")
            os.makedirs(m)
            for name in ("match-20260918-123438-001-a.jsonl", "match-20260918-124512-002-b.jsonl"):
                write_match(os.path.join(m, name), "defeated", {})
            try:
                TU.AFTER = "20260918-124000"
                self.assertEqual([os.path.basename(p) for p in TU.finished_matches(6, 0.0)], ["match-20260918-124512-002-b.jsonl"])
            finally:
                TU.AFTER = ""


if __name__ == "__main__":
    unittest.main()

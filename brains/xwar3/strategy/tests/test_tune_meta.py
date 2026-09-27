# -*- coding: utf-8 -*-
"""对局记录里带上「哪批调参实验、第几组」（09-18 起：s12 这类标记的含义要说清，远见网页后台也要能取到它）。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util  # noqa: E402

# ⚠ 按文件路径加载 war3-brain/tuner.py：war3-modules 里也有一个老的 tuner.py，run_brain 导入时把 war3-modules 加进 sys.path，
#   按名字 import 会拿到哪个取决于测试顺序（单独跑这个文件时拿到的是老的那个）
_spec = importlib.util.spec_from_file_location(
    "brain_tuner", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tuner.py"))
TU = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TU)
import run_brain as RB  # noqa: E402


class TuneMeta(unittest.TestCase):
    def test_tuner_writes_meta_and_brain_reads_it(self):
        with tempfile.TemporaryDirectory() as d:
            old = TU.BIN
            try:
                TU.BIN = d
                cfg = {"peons_lumber": 4, "lumber_high": 350, "tier2_army": 3, "expand_army": 5, "creep_engage_power": 1.7}
                TU.write_config(7, cfg, dict(study="s12", trial=17, tag="GP-UCB"))
            finally:
                TU.BIN = old
            inst = os.path.join(d, "inst7")
            self.assertEqual(json.load(open(os.path.join(inst, "brain_config.json"), encoding="utf-8")), cfg)
            recorded = dict(cfg, creep_engage_power=1.7000001)                  # 大脑读回来是 coerce 过的，差一点点
            self.assertEqual(RB.tune_meta(inst, recorded), {"study": "s12", "trial": 17, "tag": "GP-UCB"})

    def test_mismatch_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            old = TU.BIN
            try:
                TU.BIN = d
                TU.write_config(7, {"peons_lumber": 4}, dict(study="s12", trial=18, tag="撒点"))
            finally:
                TU.BIN = old
            inst = os.path.join(d, "inst7")
            self.assertEqual(RB.tune_meta(inst, {"peons_lumber": 6}), {})         # 开局读到的是上一组：不认
            self.assertEqual(RB.tune_meta(os.path.join(d, "none"), {}), {})       # 没调参的实例


if __name__ == "__main__":
    unittest.main()

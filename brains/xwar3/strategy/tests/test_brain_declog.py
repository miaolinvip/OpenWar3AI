# -*- coding: utf-8 -*-
"""大脑这一层的战斗决策明细（runtime._do -> war3-modules/declog.py -> 实例目录 decisions.jsonl）。
09-21 决策内容在后台展示：带 # 后面的单位 ID，列出每个决策从信息获取到执行各环节的耗时。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "war3-modules"))
from brain import tactics as T          # noqa: E402
from brain.runtime import Brain         # noqa: E402
from test_tactics import U, world       # noqa: E402
from test_runtime_smoke import FakeCmd  # noqa: E402
import declog                           # noqa: E402


class BrainDeclog(unittest.TestCase):
    def test_a_creep_order_is_written_with_names_reason_inputs_and_timing(self):
        home = U(1, "htow")
        feet = [U(10 + i, "hfoo", x=100 + 30 * i) for i in range(3)]
        w = world([home] + feet + [U(100, "htow", 1, 9000)], t=600)
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.config.values["enable_harass"] = False
            b._read_hurt = lambda: set()
            b._read_resting = lambda: set()
            b._hero_idle_probe = lambda *a: None
            b.unit_ids.assign(feet)
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 3000, 0, why="打野开打（营地强度 8，到场战力比 6.6）")]
            b._step_t0, b._snap_age = __import__("time").monotonic(), 7.0
            b.job_tactics(w)
            recs = [r for r in declog.tail(os.path.join(d, "decisions.jsonl")) if r["act"] == "打野开打"]
            self.assertTrue(recs, "大脑下的打野令要进决策明细")
            r = recs[0]
            self.assertEqual(r["src"], "大脑")
            self.assertEqual({u["id"] for u in r["units"]}, {1, 2, 3})
            self.assertEqual(r["units"][0]["name"], declog.unit_name("hfoo"))
            self.assertEqual(r["inputs"]["命令"], "攻击移动")
            self.assertEqual(r["timing"]["snapAge"], 7.0)
            self.assertIn("decide", r["timing"])
            self.assertEqual(r["g"], 600.0)


if __name__ == "__main__":
    unittest.main()

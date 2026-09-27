# -*- coding: utf-8 -*-
"""全机进程表缓存（09-24）：1.5 秒内共用一次扫描（远见每个接口原来都要等 1.6~5 秒）；但起了 / 杀了进程之后必须重扫 ——
加缓存的第一版切方案时读到缓存里的旧运行器，判成「已经有方案运行器在跑」拒绝启动。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import instances as I  # noqa: E402


class ProcsCacheTest(unittest.TestCase):
    def setUp(self):
        self.saved = I._scan_procs
        self.scans = 0

        def fake():
            self.scans += 1
            return [{"pid": self.scans}]
        I._scan_procs = fake
        I.invalidate_procs()

    def tearDown(self):
        I._scan_procs = self.saved
        I.invalidate_procs()

    def test_shared_within_ttl(self):
        I._procs()
        I._procs()
        self.assertEqual(self.scans, 1)

    def test_kill_and_spawn_force_rescan(self):
        I._procs()
        I.kill_tree(987654321)                      # 不存在的 pid：什么都不杀，但缓存照样作废
        I._procs()
        self.assertEqual(self.scans, 2)
        I.invalidate_procs()
        self.assertEqual(I._procs(), [{"pid": 3}])


if __name__ == "__main__":
    unittest.main()

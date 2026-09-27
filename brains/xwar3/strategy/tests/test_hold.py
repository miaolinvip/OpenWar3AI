# -*- coding: utf-8 -*-
"""停止牌 inst<N>\\hold.json（Farsight 网页「停止实例 / 全部停止」挂的）：监督器看见比自己晚挂的牌就退出、不局间重启；
比自己早的牌不算（挂完牌又手动起监督器 = 要跑）。09-19 起停止后不再自动启动、开始新一局。"""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_brain as RB  # noqa: E402

INST = 97     # 不会和真实例撞的号（main 会建 Global\War3BrainSupervisor.inst<N> 互斥体）


class HoldBase(unittest.TestCase):
    inst = INST

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_bin = RB.BIN
        RB.BIN = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, f"inst{self.inst}"))
        self.hold = os.path.join(self.tmp.name, f"inst{self.inst}", RB.HOLD_FILE)

    def tearDown(self):
        RB.BIN = self.old_bin
        self.tmp.cleanup()

    def put_hold(self, mtime=None):
        with open(self.hold, "w", encoding="utf-8") as fh:
            fh.write('{"why": "test"}')
        if mtime is not None:
            os.utime(self.hold, (mtime, mtime))


class TestHeldSince(HoldBase):
    def test_no_file(self):
        self.assertFalse(RB.held_since(INST, 0.0))

    def test_newer_counts_older_does_not(self):
        self.put_hold(mtime=1000.0)
        self.assertTrue(RB.held_since(INST, 999.0))
        self.assertTrue(RB.held_since(INST, 1000.0))
        self.assertFalse(RB.held_since(INST, 1001.0))


class TestMainLoop(HoldBase):
    # main() 结束只 ReleaseMutex 不关句柄（进程退出时系统关），同一个测试进程里再用同一个号会被当成「已经有监督器」—— 每个用例一个号
    def run_main(self, hours, hold_after=None):
        box = {}

        def go():
            t = time.monotonic()
            box["rc"] = RB.main(["--inst", str(self.inst), "--hours", str(hours)])
            box["took"] = time.monotonic() - t
        th = threading.Thread(target=go)
        th.start()
        if hold_after is not None:
            time.sleep(hold_after)
            self.put_hold()
        th.join(60)
        self.assertFalse(th.is_alive(), "main 没退出")
        return box

    def test_hold_written_while_running_stops_it(self):
        box = self.run_main(hours=0.02, hold_after=0.5)       # 本来要跑 72 秒
        self.assertEqual(box["rc"], 0)
        # 主循环 1 秒一轮，挂牌后下一轮就退（平时 1.5 秒）；判据只要分得开「跑满 72 秒」——
        # 09-22 全套测试并发跑、机器忙时超过过 5 秒，所以留到 20
        self.assertLess(box["took"], 20.0)


class TestMainLoopStale(TestMainLoop):
    inst = 96

    def test_hold_written_while_running_stops_it(self):        # 父类那条用 97 跑过了，这里不重复
        pass

    def test_stale_hold_is_ignored(self):
        self.put_hold(mtime=time.time() - 60)                  # 一分钟前挂的牌，之后手动起的监督器
        box = self.run_main(hours=0.001)                       # 3.6 秒到点自己退
        self.assertEqual(box["rc"], 0)
        self.assertGreaterEqual(box["took"], 3.0)


class TestRespawn(HoldBase):
    def test_held_means_no_successor(self):
        old = RB.newest_source
        RB.newest_source = lambda: 0.0                         # 源码早就静了，不用等 20 秒
        spawned = []
        old_popen = RB.subprocess.Popen
        RB.subprocess.Popen = lambda *a, **k: spawned.append(a)
        try:
            self.put_hold()
            ok = RB.respawn(None, type("A", (), {"inst": INST, "max_minutes": 40.0})(), time.monotonic() + 60, 3, 1234,
                            since=time.time() - 10)
        finally:
            RB.newest_source = old
            RB.subprocess.Popen = old_popen
        self.assertFalse(ok)
        self.assertEqual(spawned, [])


if __name__ == "__main__":
    unittest.main()

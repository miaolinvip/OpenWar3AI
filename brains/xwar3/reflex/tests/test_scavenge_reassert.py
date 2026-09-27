# -*- coding: utf-8 -*-
"""mod_scavenge.reassert_step：捡取租约期内被别的指令覆盖时补令。

09-16 现场：书（tint）三次没捡到，其中一次"13 秒走近了 -439" —— 同一秒策略层在租约写出来之前
读了 pickup.json，把英雄派去打野压阵，后下的令覆盖了捡取令；本模块从不补令。
驱动几拍测（只在连续几拍里出现的问题，要写"驱动几拍"的测试，不能只喂一张静态快照）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_scavenge as M  # noqa: E402


def drive(dists, dt=0.2, done0=0):
    """按 dt 秒一轮喂距离序列，返回在哪几轮（下标）触发补令；触发后模拟"补上了"。"""
    prog = [0.0, dists[0]]
    done, fired = done0, []
    for i, d in enumerate(dists):
        now = i * dt
        if M.reassert_step(prog, now, d, done):
            fired.append(i)
            done += 1
            prog[0], prog[1] = now, d
    return fired


class ReassertStep(unittest.TestCase):
    def test_walking_closer_never_reasserts(self):
        # 每 0.2 秒走近 190（英雄 3 倍速约 960/秒）
        self.assertEqual(drive([674 - 190 * i for i in range(4)] + [0] * 3), [])

    def test_yanked_away_reasserts_after_1_5s(self):
        # 起步走近一点，然后被拽走、越走越远
        dists = [674, 600] + [600 + 60 * i for i in range(1, 20)]
        fired = drive(dists)
        self.assertTrue(fired, '被拽走必须补令')
        self.assertEqual(fired[0], 9)      # 最后一次进展在第 1 轮(0.2s)，1.5 秒后 = 第 9 轮(1.8s)

    def test_capped(self):
        # 一直不动：最多补 REASSERT_MAX 次，之后不再拉锯
        fired = drive([500] * 200)
        self.assertEqual(len(fired), M.REASSERT_MAX)

    def test_jitter_is_not_progress(self):
        # 原地抖动（每轮 ±20，小于 REASSERT_GAIN）不算走近，照样补
        dists = [500 + (20 if i % 2 else -20) for i in range(20)]
        self.assertTrue(drive(dists))

    def test_progress_resets_clock(self):
        # 停 1.2 秒（不到阈值）后又走近一大步：时钟重置，不补
        dists = [500] * 7 + [300] + [300] * 6
        self.assertEqual(drive(dists), [])


if __name__ == '__main__':
    unittest.main()

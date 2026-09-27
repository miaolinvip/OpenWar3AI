# -*- coding: utf-8 -*-
"""09-23 体检之后的优化（20 局报告：大脑一轮 48 ms，一半在串行地等游戏线程）：
  * 战术下令整队一批发（act._point / attack 走 cmd.move_many / target_many）
  * 建造规划的可行性检查一批预取（act.prefetch_feasible），规划里命中缓存不再逐个问
  * 游戏内按钮一次刷新的几条命令合成一批（hud.update）
  * 学技能按「第 N 级要英雄几级」先判，不够就停在这一格等升级（原来失败了照样前进一格 = 大招永远跳过）
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain import hud as H  # noqa: E402

import w3fast as F  # noqa: E402


def unit(addr, x=0.0, order=0):
    return SimpleNamespace(addr=addr, type="hfoo", x=x, y=0.0, order=order, has_task=False)


class BatchCmd:
    def __init__(self):
        self.batches = []
        self.single = 0

    def move_many(self, items):
        self.batches.append(("move", list(items)))
        return [True] * len(items)

    def target_many(self, items):
        self.batches.append(("target", list(items)))
        return [True] * len(items)

    def move_to(self, u, x, y, order):
        self.single += 1
        return True

    def feasible_many(self, pairs):
        self.batches.append(("feas", list(pairs)))
        return [0 if code == A.rawcode("hfoo") else 8 for _a, code in pairs]

    def feasible(self, u, code):
        self.single += 1
        return 221


class ArmyOrders(unittest.TestCase):
    def test_group_move_is_one_batch(self):
        cmd = BatchCmd()
        act = A.Actuator(cmd)
        n = act.move([unit(1), unit(2, 40.0), unit(3, 80.0)], 2000.0, 0.0)
        self.assertEqual(n, 3)
        self.assertEqual([k for k, _ in cmd.batches], ["move"])
        self.assertEqual(len(cmd.batches[0][1]), 3)
        self.assertEqual(cmd.single, 0)
        self.assertEqual(act.counts.get("点命令·W3P批"), 3)

    def test_single_unit_still_goes_alone(self):
        cmd = BatchCmd()
        A.Actuator(cmd).move([unit(1)], 2000.0, 0.0)
        self.assertEqual((cmd.batches, cmd.single), ([], 1))

    def test_group_attack_is_one_batch(self):
        cmd = BatchCmd()
        tgt = SimpleNamespace(addr=99, x=5.0, y=6.0)
        n = A.Actuator(cmd).attack([unit(1), unit(2)], tgt)
        self.assertEqual(n, 2)
        self.assertEqual(cmd.batches, [("target", [(1, 99, A.ORDER_SMART), (2, 99, A.ORDER_SMART)])])


class FeasiblePrefetch(unittest.TestCase):
    def test_prefetched_verdicts_used_then_cleared(self):
        cmd = BatchCmd()
        act = A.Actuator(cmd)
        act.prefetch_feasible([(10, "hfoo"), (10, "hrif"), (10, "hfoo")])     # 重复的去掉
        self.assertEqual(cmd.batches, [("feas", [(10, A.rawcode("hfoo")), (10, A.rawcode("hrif"))])])
        self.assertEqual((act.feasible(10, "hfoo"), act.feasible(10, "hrif")), (0, 8))
        self.assertEqual(cmd.single, 0)
        self.assertEqual(act.counts.get("可行性·用预取"), 2)
        act.clear_feasible()
        self.assertEqual(act.feasible(10, "hfoo"), 221)                        # 规划完就不用了：单问
        self.assertEqual(cmd.single, 1)


class SkillLevels(unittest.TestCase):
    def test_level_needed_from_ability_data(self):
        self.assertEqual([G.skill_level_needed("AHbz", r) for r in (1, 2, 3, 4)], [1, 3, 5, 99])
        self.assertEqual(G.skill_level_needed("AHpx", 1), 6)
        self.assertIsNone(G.skill_level_needed("XXXX", 1))

    def test_hero_waits_for_level_instead_of_skipping(self):
        """3 级大法师、加点表下一格是大招：原来学失败照样前进一格（大招整局学不上），现在停在这一格等 6 级。"""
        import tempfile
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd, U

        class Cmd(FakeCmd):
            def __init__(self):
                super().__init__()
                self.learned = []

            def hero_level(self, u):
                return 3

            def skill_points(self, u):
                return 1

            def ability_level(self, u, code):
                return 0

            def learn(self, u, code):
                self.learned.append(code)
                return True

        from unittest import mock
        from brain import runtime as RT
        cmd = Cmd()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(RT.R, "SKILL_OVERRIDE", {}):
            b = Brain(cmd, None, d)
            hero = U(0x500, "Hamg", 0, 0)
            b.skills = {"Hamg": ["AHmt", "AHbz"]}                              # 加点表第一格就是大招（覆盖表清空，用这一份）
            b.job_heroes(SimpleNamespace(my_heroes=[hero], t=10.0))
            self.assertEqual(cmd.learned, [])
            self.assertEqual(b.skill_idx.get(hero.addr, 0), 0)                 # 停在大招这一格
            self.assertEqual(b.counts["学技能·等级不够先等"], 1)


class LearnReadYourWrites(unittest.TestCase):
    def test_stale_skill_points_do_not_skip_the_next_skill(self):
        """学会一个后世界块还没更新（技能点读回来还是 1）：原来接着学下一个、被拒、前进一格 = 跳过。现在本地减一就停。"""
        import tempfile
        from unittest import mock
        from brain import runtime as RT
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd, U

        class Cmd(FakeCmd):
            def __init__(self):
                super().__init__()
                self.learned = []

            def hero_level(self, u):
                return 5

            def skill_points(self, u):
                return 1                                   # 世界块还没更新：一直读到 1

            def ability_level(self, u, code):
                return 0

            def learn(self, u, code):
                self.learned.append(code)
                return len(self.learned) == 1              # 真实只有 1 点：第二次会被拒

        cmd = Cmd()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(RT.R, "SKILL_OVERRIDE", {}):
            b = Brain(cmd, None, d)
            hero = U(0x500, "Hamg", 0, 0)
            b.skills = {"Hamg": ["AHwe", "AHbz", "AHab"]}
            b.job_heroes(SimpleNamespace(my_heroes=[hero], t=10.0))
            self.assertEqual(len(cmd.learned), 1)
            self.assertEqual(b.skill_idx[hero.addr], 1)                    # 下一格 AHbz 留着，没被跳过


class HudBatch(unittest.TestCase):
    def test_one_update_is_one_commands_call(self):
        class Lane:
            def __init__(self):
                self.calls = 0
                self.sent = []

            def commands(self, calls):
                self.calls += 1
                self.sent += calls
                return [F.Receipt(c.op, 0, 1, None, 0, 0, (0,) * 8) for c in calls]

        lane, t = Lane(), [100.0]
        hud = H.Hud(lane, clock=lambda: t[0])
        hud.update("War3AI-5-001", 61.0, [("log", "对局日志", "01:00 打野"), ("status", "当前状态", "强度 1")])
        self.assertEqual(lane.calls, 1)                                        # 按钮 ×2 + 任务 ×2 + 刷新 + 查面板 一批
        self.assertGreaterEqual(len(lane.sent), 8)
        self.assertEqual(hud.shown, {"quests": "War3AI-5-001", "allies": "01:01"})

    def test_failed_group_is_undone(self):
        class Lane:
            def commands(self, calls):
                return [F.Receipt(c.op, 1, 0, None, 0, 0, (0,) * 8) for c in calls]   # 还没进局：全拒

        hud = H.Hud(Lane(), clock=lambda: 100.0)
        hud.update("L", 1.0, [("log", "对局日志", "x")])
        self.assertEqual((hud.shown, hud.quests, hud.quest_text), ({}, {}, {}))  # 乐观记下的都撤回
        self.assertGreater(hud.errors, 0)


if __name__ == "__main__":
    unittest.main()

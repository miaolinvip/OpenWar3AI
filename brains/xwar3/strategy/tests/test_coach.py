# -*- coding: utf-8 -*-
"""运营顾问（本地大模型）这一层的**边界**：钳位、白名单、过期、关掉之后完全等于没有。

09-21 设计：运营层用规则代码不好写，改为按阵营准备一套提示词，由大模型做运营和战略层面的分析与输出，
替代大量 if/else 分支。

这条测试不测模型说得对不对（那要实机看），只测**模型说错话的时候这一层不会把局面搞坏** ——
LLM 进决策回路，能守住的只有边界。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))        # test_runtime_smoke.U
from brain import coach as CH  # noqa: E402


class ParseIsForgivingAboutWrappingNotAboutContent(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(CH.parse_json('{"a":1}'), {"a": 1})

    def test_code_fence_and_think_block(self):
        self.assertEqual(CH.parse_json('<think>\n想一想\n</think>\n```json\n{"a":1}\n```'), {"a": 1})

    def test_chatter_around_it(self):
        self.assertEqual(CH.parse_json('好的，我的建议是：{"a":1} 希望有帮助'), {"a": 1})

    def test_garbage_is_none_not_a_guess(self):
        for bad in ("", "不知道", "{不是 json}", "[1,2,3]", None):
            self.assertIsNone(CH.parse_json(bad), bad)


class ClampKeepsTheModelInsideTheRules(unittest.TestCase):
    def setUp(self):
        self.c = CH.Coach(race="human")

    def test_worker_counts_are_clamped_and_counted(self):
        p = self.c.clamp({"workers": {"gold": 999, "lumber": -5}}, 100.0)
        self.assertEqual((p.gold, p.lumber), (CH.GOLD_MAX, CH.LUMBER_MIN))
        self.assertEqual(self.c.counts.get("顾问·钳位·采金人数越界"), 1)
        self.assertEqual(self.c.counts.get("顾问·钳位·伐木人数越界"), 1)

    def test_non_numeric_workers_are_dropped_not_guessed(self):
        p = self.c.clamp({"workers": {"gold": "很多", "lumber": None}}, 100.0)
        self.assertIsNone(p.gold)
        self.assertIsNone(p.lumber)
        self.assertEqual(self.c.counts.get("顾问·钳位·采金人数不是数字"), 1)

    def test_unknown_posture_is_refused_and_counted(self):
        """白名单之外的姿态必须**记数后丢弃**，不能静默忽略 ——
        静默忽略的闸门看起来完全正常，这个仓库栽过很多次。"""
        p = self.c.clamp({"posture": "全军突击"}, 100.0)
        self.assertEqual(p.posture, "")
        self.assertTrue(any("姿态不认识" in k for k in self.c.counts))

    def test_known_posture_passes(self):
        for post in CH.POSTURES:
            self.assertEqual(CH.Coach().clamp({"posture": post}, 1.0).posture, post)

    def test_priority_is_capped_and_filtered(self):
        p = self.c.clamp({"priority": ["hkni", "hhou", "Rhme", "hgry", "hfoo", "造一堆兵"]}, 100.0)
        self.assertEqual(len(p.priority), CH.PRIORITY_MAX)
        self.assertNotIn("造一堆兵", p.priority)
        self.assertEqual(self.c.counts.get("顾问·钳位·优先项不是代码"), 1)

    def test_diagnosis_and_avoid_are_length_capped(self):
        p = self.c.clamp({"diagnosis": "啊" * 500, "avoid": ["x" * 500] * 9}, 100.0)
        self.assertLessEqual(len(p.diagnosis), 60)
        self.assertLessEqual(len(p.avoid), 2)


class DisabledOrStaleMeansNoLayerAtAll(unittest.TestCase):
    def test_disabled_returns_none(self):
        c = CH.Coach(enabled=False)
        c._plan = c.clamp({"workers": {"gold": 8}}, 100.0)
        self.assertIsNone(c.plan(100.0))

    def test_stale_plan_is_not_used(self):
        """模型挂掉之后旧建议会一直留在内存里。局面早就变了，必须过期 ——
        否则「模型没起来」会表现成「按 10 分钟前的局面运营」，比没有这一层还糟。"""
        c = CH.Coach(enabled=True)
        c._plan = c.clamp({"workers": {"gold": 8}}, 100.0)
        self.assertIsNotNone(c.plan(100.0 + CH.COACH_EVERY))
        self.assertIsNone(c.plan(100.0 + 3 * CH.COACH_EVERY + 1))

    def test_tick_never_blocks_when_disabled(self):
        c = CH.Coach(enabled=False)
        c.tick(100.0, {})
        self.assertFalse(c.counts)


class LiveStateTellsTheFourCasesApart(unittest.TestCase):
    """★ 09-21 排查大模型运营层是否在运行时发现：LM Studio 根本没起、而大脑侧看不出来
    —— 行为和「没有这一层」一模一样（设计如此），心跳又只打前 10 个计数。
    发布给远见的 state 必须把四种情况分开，不能都显示成「没动静」。"""

    def test_off(self):
        self.assertEqual(CH.Coach(enabled=False).live(100.0)["state"], "off")

    def test_waiting_before_the_first_try(self):
        self.assertEqual(CH.Coach(enabled=True).live(100.0)["state"], "waiting")

    def test_down_when_the_last_try_failed(self):
        c = CH.Coach(enabled=True)
        c.last_try, c.last_error = 1000.0, "URLError：连接被拒"
        d = c.live(100.0)
        self.assertEqual(d["state"], "down")
        self.assertIn("连接被拒", d["lastError"])

    def test_ok_after_a_success_even_if_an_older_try_failed(self):
        c = CH.Coach(enabled=True)
        c.last_try, c.last_ok, c.last_error = 1000.0, 1000.5, ""
        c._plan = c.clamp({"workers": {"gold": 8, "lumber": 4}, "posture": "creep"}, 100.0)
        d = c.live(100.0)
        self.assertEqual(d["state"], "ok")
        self.assertEqual((d["plan"]["gold"], d["plan"]["posture"]), (8, "creep"))
        self.assertTrue(d["planFresh"])

    def test_a_real_failed_request_is_recorded_not_swallowed(self):
        """端到端：指向一个没人听的端口，_run 必须留下 lastError 和计数（不能只是悄悄什么都不发生）。"""
        c = CH.Coach(enabled=True, base_url="http://127.0.0.1:9/v1")
        c._run(100.0, {})
        d = c.live(100.0)
        self.assertEqual(d["state"], "down")
        self.assertTrue(d["lastError"])
        self.assertTrue(any(k.startswith("顾问·出错") or k == "顾问·超时" for k in c.counts), dict(c.counts))


class SnapshotCountsWhoIsActuallyMining(unittest.TestCase):
    """★ 09-21 远见一上线就照出来的：快照里「采金人数」原来用的是大脑的**派工名单**，
    开局那 5 个农民是游戏自动派去采矿的、不在名单里 —— 于是 6 个实例一律「采金 0」，
    4 个顾问**全都诊断「金矿无人采集 / 农民分配严重失衡」**，对着一个假问题出主意。
    名单 = 「我派过谁」，不是「谁在采」（量之前先问这个数在量什么）。"""

    def test_auto_mining_workers_count_as_on_gold(self):
        from types import SimpleNamespace
        from brain.act import HARVEST_ORDERS
        from brain.world import build_world
        from test_runtime_smoke import U
        harvest = HARVEST_ORDERS[0]
        units = [U(1, "htow", 0, 0), U(700, "ngol", 300, 0, owner=15)]
        units += [U(10 + i, "hpea", 280 + 10 * i, 40, order=harvest) for i in range(5)]    # 开局自动采金
        units += [U(20, "hpea", 2000, 2000, order=harvest)]                                 # 远处在砍树
        units += [U(30, "hpea", 100, 100)]                                                  # 没订单
        w = build_world(units, 0, 30.0, 500, 150, 6, 12)
        brain = SimpleNamespace(race="human", profile={"peons_per_mine": 5}, gold_crew={}, lumber_crew={})
        s = CH.snapshot(w, brain)
        self.assertEqual(s["workers"]["onGold"], 5, s["workers"])
        self.assertEqual(s["workers"]["onLumber"], 1)
        self.assertEqual(s["workers"]["idle"], 1)
        self.assertEqual(s["mines"][0]["workers"], 5)

    def test_workers_hidden_inside_the_mine_still_count(self):
        """进了矿井的农民快照里看不见；名单里有、画面上没有的，算在他那座矿上。"""
        from types import SimpleNamespace
        from brain.world import build_world
        from test_runtime_smoke import U
        units = [U(1, "htow", 0, 0), U(700, "ngol", 300, 0, owner=15)]
        w = build_world(units, 0, 300.0, 500, 150, 6, 12)
        brain = SimpleNamespace(race="human", profile={"peons_per_mine": 5}, gold_crew={91: 700, 92: 700}, lumber_crew={})
        s = CH.snapshot(w, brain)
        self.assertEqual(s["workers"]["onGold"], 2)
        self.assertEqual(s["workers"]["total"], 2)


class EveryRaceHasItsOwnPrompt(unittest.TestCase):
    def test_four_races_and_they_differ(self):
        self.assertEqual(set(CH.RACE_PROMPTS), {"human", "orc", "undead", "nightelf"})
        self.assertEqual(len(set(CH.RACE_PROMPTS.values())), 4)

    def test_the_schema_lives_in_one_place(self):
        """字段名只在 COMMON 里出现一次；四份种族提示词不许各写一份格式说明。"""
        for text in CH.RACE_PROMPTS.values():
            self.assertNotIn("diagnosis", text)
            self.assertNotIn("posture", text)

    def test_common_prompt_lists_exactly_the_whitelisted_postures(self):
        """提示词里写给模型的姿态，必须和代码白名单是同一份 —— 两边分叉的话，
        模型会一直给出一个被我们默默丢掉的值，而计数里只看到「姿态不认识」。"""
        for post in CH.POSTURES:
            self.assertIn(post, CH.COMMON)


if __name__ == "__main__":
    unittest.main()

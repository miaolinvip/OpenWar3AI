# -*- coding: utf-8 -*-
"""家被打、守军打不过：大厅边上的农民变民兵一起守；敌人走了解除（runtime._home_militia_step）。

09-22 晚 敌军攻进家时，农民变民兵一起守家（1-405）。
1-405 6.9 游戏分钟：对面 8 步兵 4 火枪 + 山丘打进主基地，我方 7 个兵、16 个农民一直在采矿 —— 60 秒兵打光、10.9 分钟认输。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import runtime as RT  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

PEAS = [0x300 + i for i in range(10)]


def world(t=400.0, foes=6, army=2, knights=0, militia=(), far_peasant=False):
    units = [U(1, "htow", 0, 0), U(2, "hbar", 500, 0)]
    units += [U(a, "hpea", 200 + 30 * i, 150) for i, a in enumerate(PEAS) if a not in militia]
    units += [U(a, "hmil", 250, 200) for a in militia]
    if far_peasant:
        units.append(U(0x3F0, "hpea", 4000, 4000))
    units += [U(0x500 + i, "hfoo", 300, 300 + 40 * i) for i in range(army)]
    units += [U(0x520 + i, "hkni", 350, 300 + 40 * i) for i in range(knights)]
    units += [U(0x800 + i, "hfoo", 900, 60 * i, owner=1) for i in range(foes)]
    return build_world(units, 0, t, 300, 300, 30, 60)


def brain(d):
    b = Brain(FakeCmd(), None, d)
    b.set_race("human")
    return b


def calls(b, order):
    return [u for u, o in b.cmd.trains if o == order]


class HomeMilitia(unittest.TestCase):
    def test_1405_outnumbered_at_home_calls_the_peasants(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b._home_militia_step(world(far_peasant=True))
            self.assertEqual(sorted(calls(b, A.ORDER_MILITIA)), PEAS, "大厅边上的 10 个全叫，4000 外那个不叫")
            self.assertEqual(b.counts.get("守家民兵·号召"), 10)

    def test_strong_enough_or_a_scout_does_not_stop_the_economy(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b._home_militia_step(world(knights=12))
            self.assertEqual(calls(b, A.ORDER_MILITIA), [])
            self.assertEqual(b.counts.get("守家民兵·守得住不叫"), 1)
            b._home_militia_step(world(foes=2))
            self.assertEqual(calls(b, A.ORDER_MILITIA), [], "两个敌人不叫")

    def test_builders_keep_building(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b.pending.append(dict(kind="building", code="hhou", t=399.0, worker=PEAS[0], x=0.0, y=600.0))
            b._home_militia_step(world())
            self.assertNotIn(PEAS[0], calls(b, A.ORDER_MILITIA))

    def test_released_after_the_enemy_leaves(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b._home_militia_step(world())
            gone = world(t=402.0, foes=0, militia=PEAS)
            b._home_militia_step(gone)
            self.assertEqual(calls(b, A.ORDER_MILITIA_OFF), [], "刚走 2 秒：先别解除")
            b._home_militia_step(world(t=400.0 + RT.HOME_MIL_CLEAR_S + 0.5, foes=0, militia=PEAS))
            self.assertEqual(sorted(calls(b, A.ORDER_MILITIA_OFF)), PEAS)
            self.assertEqual(b.counts.get("守家民兵·敌人走了解除"), 10)

    def test_militia_still_count_as_peasants_for_the_build_plan(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            from brain.build import Want
            w = world(militia=PEAS[:6])
            self.assertEqual(b.have(w, Want("unit", "hpea", 20, 84)), 10, "6 个民兵 + 4 个农民 = 10 个农民，不补")

    def test_recalled_after_the_45_seconds_if_still_attacked(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b._home_militia_step(world())
            b._home_militia_step(world(t=420.0))                      # 20 秒后还在打：同一批不重叫
            self.assertEqual(len(calls(b, A.ORDER_MILITIA)), 10)
            b._home_militia_step(world(t=400.0 + RT.HOME_MIL_RECALL_S + 1))   # 到点变回了、敌人还在：再叫
            self.assertEqual(len(calls(b, A.ORDER_MILITIA)), 20)


if __name__ == "__main__":
    unittest.main()

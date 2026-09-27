# -*- coding: utf-8 -*-
"""地基一定要盖完（runtime.job_construction / _builder / powerbuild.PowerBuild / act.build）。

09-22 晚 1-413（对 AMAI 兽族）6.8 分钟一个兵都没有、认输：兵营 t=19 下令，t=83 盖到 1491/1500（99.4%）时，
工人被「续建」拉去盖隔壁一座没人盖的农场 —— powerbuild 只登记农场/大厅，兵营的工人一出地基就不算忙，
续建按「离停工工地最近」挑中了它。之后兵营整局停在 99.4%：续建器 >= 97% 就当「见过满血、不是工地」，
维修器要 < 98.5% 才修，两条判据中间这条缝谁也不管。「引擎裁决 221·hfoo」×66（221 = 建筑还在建）。
交战帧拉通 150 局：从没满血、停在最后一点整局没盖完的地基 7 次 —— 兵营 2 次（1-283 峰值兵力 3、1-413 峰值兵力 1），
第一座农场 5 次（09-21 晚，97.6~98.8%，整局少 6 人口）。
那座没人盖的农场本身来自 act.build 的误判：工人就在旁边，0.25 秒里已经放下地基、订单变成施工令 0x000D001B，
被当成「被拒」、换点重下，把刚开工的工人拽走。
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import powerbuild as PB  # noqa: E402
from brain import runtime as RT  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HALL = (-5184.0, 2944.0)
BAR = (-4608.0, 3136.0)
FARM = (-4864.0, 3136.0)


class Site(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cmd = FakeCmd()
        self.b = Brain(self.cmd, None, self.tmp.name)
        self.hall = U(1, "htow", *HALL)
        self.bar = U(2, "hbar", *BAR)
        self.bar.hp = 150.0
        self.builder = U(3, "hpea", BAR[0], BAR[1] - 150, order=PB.HUMAN_BUILD, has_task=True)
        self.far = U(4, "hpea", HALL[0], HALL[1] - 350)               # 离那座农场 620：比兵营工人远一倍
        self.units = [self.hall, self.bar, self.builder, self.far]

    def world(self, t):
        return build_world(self.units, 0, t, 400, 200, 12, 30)

    def register_barracks(self):
        self.b.pending = [dict(kind="building", code="hbar", worker=self.builder.addr, t=19.0, had=0, x=BAR[0], y=BAR[1])]
        self.b._prune_pending(self.world(22.0))

    def resumes(self):
        return [(u, t) for (u, t, o) in self.cmd.gathers if o == A.ORDER_SMART]

    def test_1413_barracks_builder_is_not_pulled_to_an_orphan_farm(self):
        self.register_barracks()
        self.assertIn(self.builder.addr, self.b.powerbuild.busy_addrs, "兵营地基也要登记施工的工人")
        farm = U(5, "hhou", *FARM)
        farm.hp = 55.0
        self.units.append(farm)
        for t in (24.0, 28.0, 33.0):
            self.bar.hp += 300.0
            self.b.job_construction(self.world(t))
        self.assertEqual(self.resumes(), [(self.far.addr, farm.addr)], "续建要派别人去盖农场，不能拽走正在盖兵营的")

    def test_a_worker_on_a_build_order_is_never_a_candidate(self):
        """大脑重启之后没有账：订单是施工令 / 续建令 / 建造令（rawcode）的农民也不许被挑走。"""
        for o in (PB.HUMAN_BUILD, PB.RESUME_BUILD, A.rawcode("hbar")):
            self.builder.order = o
            self.assertNotIn(self.builder, self.b._builder(self.world(30.0)), hex(o))
        self.builder.order = A.ORDER_HARVEST
        self.assertIn(self.builder, self.b._builder(self.world(30.0)))

    def test_1413_site_stuck_at_99_percent_gets_resumed(self):
        self.bar.hp = 1491.0                                          # 差 9 点血
        self.builder.order, self.builder.has_task = 0, False
        self.builder.x, self.builder.y = HALL[0] + 3000, HALL[1]      # 工人早走了
        self.b.job_construction(self.world(100.0))
        self.b.job_construction(self.world(104.0))
        self.b.job_construction(self.world(110.0))
        self.assertEqual([t for (_, t) in self.resumes()], [self.bar.addr], "停在 99.4% 也是工地")

    def test_finished_building_that_took_a_hit_is_not_a_site(self):
        self.bar.hp = 1499.6                                          # 快照里的成品常差零点几（交战帧 499/500）
        self.b.job_construction(self.world(100.0))
        self.bar.hp = 1480.0
        for t in (104.0, 110.0, 120.0):
            self.b.job_construction(self.world(t))
        self.assertEqual(self.resumes(), [])

    def test_builder_is_released_once_its_site_stops_growing(self):
        """工人被保命拉走、或者死了一个又换了人：地基 8 游戏秒不涨就不再替它占着工人（续建会重新派）。"""
        self.register_barracks()
        self.b.job_construction(self.world(24.0))
        self.assertIn(self.builder.addr, self.b.powerbuild.busy_addrs)
        self.b.job_construction(self.world(24.0 + PB.SITE_BUSY_STALL_S + 1))
        self.assertNotIn(self.builder.addr, self.b.powerbuild.busy_addrs)
        self.assertIn(self.b.powerbuild.sites and next(iter(self.b.powerbuild.sites)), {self.bar.addr})

    def test_site_kept_until_really_full(self):
        self.register_barracks()
        self.bar.hp = 1470.0                                          # 98%：原来 97% 就把工地删了、工人放掉
        self.b.job_construction(self.world(24.0))
        self.assertIn(self.bar.addr, self.b.powerbuild.sites)
        self.assertIn(self.builder.addr, self.b.powerbuild.busy_addrs)
        self.bar.hp = 1500.0
        self.b.job_construction(self.world(28.0))
        self.assertNotIn(self.bar.addr, self.b.powerbuild.sites)

    def test_stall_windows_agree(self):
        self.assertEqual(PB.SITE_BUSY_STALL_S, RT.SITE_STALL_S, "放掉工人和判停工是同一件事，两边的数要一致")


class BuildAccepted(unittest.TestCase):
    """act.build：工人就在旁边时同一帧就放下地基、订单变成施工令 —— 这也是「接下」。
    09-23 P3 起「接没接」由运行时判（W3P build 回执：订单 == 建筑码，或下令前不在施工、下令后在施工），这里只看回执怎么翻成结果。"""

    def actuator(self, status, before=0, after=0):
        r = SimpleNamespace(status=status, order_before=before, order_after=after)
        cmd = SimpleNamespace(build=lambda *a: status == 0, last_receipt=r)
        return A.Actuator(cmd, None, sleep_fn=lambda s: None)

    def test_receipt_maps_to_result(self):
        act = self.actuator(0, A.ORDER_HARVEST, PB.HUMAN_BUILD)
        self.assertEqual(act.build(3, "hhou", 0, 0), "接下")
        self.assertEqual(act.counts.get("建造·接下·已开工"), 1)
        self.assertEqual(self.actuator(0, 0, A.rawcode("hhou")).build(3, "hhou", 0, 0), "接下")
        self.assertEqual(self.actuator(1, A.ORDER_HARVEST, A.ORDER_HARVEST).build(3, "hhou", 0, 0), "被拒")

    def test_not_an_engine_refusal_is_not_sent(self):
        self.assertEqual(self.actuator(2).build(3, "hhou", 0, 0), "没发出去")      # 单位没了（bad_unit）
        self.assertEqual(self.actuator(98).build(3, "hhou", 0, 0), "没发出去")     # 被更高优先级的层握着


if __name__ == "__main__":
    unittest.main()

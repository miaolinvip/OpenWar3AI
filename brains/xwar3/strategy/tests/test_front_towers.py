# -*- coding: utf-8 -*-
"""主基地门口的塔（tables/human FRONT_TOWERS + runtime.main_front_sector + _producer 挑门口那座升奥术塔）。

09-23 我方主基地从没造过能攻击的塔，而 AMAI 家门口有两座塔（一座奥术塔、一座攻击塔），
我方冲过去的单位大多没能回来（1-422）。1-422 对面（AMAI 人族）家门口 hatw (3648,2496) + hwtw (3584,2752)；
AMAI NormalHuman 表 BuildFront(1, uARCANE_TOWER, 45 / 57)，front_base_distance 1100。
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import runtime as RT  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables import human  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

MAIN, FOE = (-5184, 2944), (4672, 2944)


def world(hall="hkee", extra=(), t=500.0):
    units = [U(1, hall, *MAIN), U(2, "hbar", MAIN[0] + 400, MAIN[1] + 500), U(3, "hlum", MAIN[0] - 500, MAIN[1] + 400),
             U(0x800, "hkee", *FOE, owner=1)] + list(extra)
    return build_world(units, 0, t, 400, 300, 30, 60)


def front_wants(w):
    return [x for x in human.wants(w, human.PROFILE) if x.kind == "building" and x.code == "hwtw" and "门口" in x.note]


class Front(unittest.TestCase):
    def test_1422_main_base_gets_two_towers_toward_the_enemy(self):
        w = world()
        f = front_wants(w)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].qty, f[0].prio, f[0].where), (human.FRONT_TOWERS, 57, "town:1"))
        self.assertEqual(front_wants(world(hall="htow"))[0].prio, 45, "一本 45（AMAI 同）")

    def test_spots_face_the_enemy_about_1100_out(self):
        pts = RT.main_front_sector(U(1, "hkee", *MAIN), FOE)
        x, y = pts[0]
        self.assertAlmostEqual(math.hypot(x - MAIN[0], y - MAIN[1]), 1100, delta=40)
        self.assertGreater(x, MAIN[0], "朝东（对面在东边）")
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            spots = b._building_spots(world(), "hwtw", "town:1", exits=False)
            self.assertTrue(spots)
            sx, sy = spots[0]
            self.assertGreater(sx, MAIN[0] + 600, f"第一个候选在门口（朝对面）而不是大厅背后：{spots[:3]}")

    def test_one_scout_becomes_arcane_and_it_is_the_one_at_the_door(self):
        scouts = [U(10, "hwtw", MAIN[0] + 1100, MAIN[1]), U(11, "hwtw", -4352, -2400)]    # 门口一座、二矿一座
        w = world(extra=scouts)
        morphs = {x.code: x for x in human.wants(w, human.PROFILE) if x.kind == "morph"}
        self.assertEqual(morphs["hatw"].qty, 1)
        self.assertGreater(morphs["hatw"].prio, morphs["hgtw"].prio, "先定奥术塔，剩下的升防御塔")
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.act.feasible = lambda addr, code: 0
            tower, why = b._producer(w, ("hwtw",), "hatw")
            self.assertEqual(tower.addr, 10, "离主基地最近的那座（门口）升奥术塔")
        done = world(extra=scouts + [U(12, "hatw", MAIN[0] + 1000, MAIN[1] + 200)])
        self.assertNotIn("hatw", {x.code for x in human.wants(done, human.PROFILE) if x.kind == "morph"}, "有一座就不再要")


class OnlyAtTheDoor(unittest.TestCase):
    """09-23 对局画面（1-429）：防御塔该守家门口，却被造到了地图最上边，打不到任何来犯的敌人。

    实局：主基地 (-5184,2944)、对面在正东（0°），塔却落在 (-4544,3520) 42° 和 (-4352,3712) 43°、离大厅 861/1132。
    两层原因：① FRONT_RADII 里有 1250，而 have_near_town 只数 1200 以内 —— 门口盖成了也不算数，愿望永远不满足；
    ② 门口的点占满之后回落到「大厅一圈」的通用候选，于是接着往基地背后摆。"""

    def test_every_front_spot_is_counted_by_have_near_town(self):
        pts = RT.main_front_sector(U(1, "hkee", *MAIN), FOE)
        far = [(x, y) for x, y in pts if math.hypot(x - MAIN[0], y - MAIN[1]) > RT.TOWN_NEAR_R]
        self.assertFalse(far, f"这些点盖成了也不会被 have_near_town（{RT.TOWN_NEAR_R}）数进去：{far}")

    def test_every_front_spot_faces_the_enemy(self):
        pts = RT.main_front_sector(U(1, "hkee", *MAIN), FOE)
        for x, y in pts:
            ang = abs(math.degrees(math.atan2(y - MAIN[1], x - MAIN[0])))       # 对面在正东 = 0°
            self.assertLessEqual(ang, 35, f"({x},{y}) 偏了 {ang:.0f}°；1-429 那两座是 42°/43°")

    def test_no_fallback_to_the_back_of_the_base(self):
        w = world()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.bad_spots["hwtw"] = set(RT.main_front_sector(w.my_halls[0], FOE))   # 门口的点全被退过
            spots = b._building_spots(w, "hwtw", "town:1", exits=False)
            self.assertEqual(spots, [], "门口没位置就这一拍不盖，不许退回大厅一圈（那会摆到东北角）")
            self.assertEqual(b.counts.get("落点·门口没位置·hwtw"), 1)


if __name__ == "__main__":
    unittest.main()

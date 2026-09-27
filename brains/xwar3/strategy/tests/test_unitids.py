# -*- coding: utf-8 -*-
"""血条编号（brain/unitids.py）。

09-21 给战场上每个单位标一个 ID，显示在血条上方，方便指认是哪个单位出了异常。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "war3-modules"))
from brain import unitids as UID  # noqa: E402


def U(addr, typ, x=0.0, y=0.0, hp=100.0):
    return SimpleNamespace(addr=addr, type=typ, x=x, y=y, hp=hp, hp_max=100.0)


class Assign(unittest.TestCase):
    def test_numbers_are_stable_and_never_reused(self):
        ids = UID.UnitIds()
        new = ids.assign([U(0x100, "Hamg"), U(0x200, "hfoo"), U(0x300, "hpea")])
        self.assertEqual(new, [(1, 0x100, "Hamg"), (2, 0x200, "hfoo")])        # 工人不编号
        self.assertEqual(ids.assign([U(0x200, "hfoo"), U(0x100, "Hamg")]), [])  # 已经有号的不再给
        # 0x200 死了、地址被一个火枪手复用：当新单位，给新号，旧号不回收
        self.assertEqual(ids.assign([U(0x200, "hrif")]), [(3, 0x200, "hrif")])


class Annotate(unittest.TestCase):
    def test_both_log_formats_get_the_number_once(self):
        ids = {0x14D4E0E4: 23}
        self.assertEqual(UID.annotate("打野阵亡：hfoo@14D4E0E4 上一拍血 46/420", ids),
                         "打野阵亡：#23 hfoo@14D4E0E4 上一拍血 46/420")
        once = UID.annotate("拉出来 hfoo@14D4E0E4", ids)
        self.assertEqual(UID.annotate(once, ids), once)                          # 不重复加
        self.assertEqual(UID.annotate("编号 #23 hfoo@14D4E0E4", ids), "编号 #23 hfoo@14D4E0E4")
        self.assertEqual(UID.annotate("没见过 hfoo@12345678", ids), "没见过 hfoo@12345678")

    def test_publish_and_load_round_trip(self):
        ids = UID.UnitIds()
        ids.assign([U(0x14D4E0E4, "hfoo")])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "unit_ids.json")
            ids.publish(p, 4242)
            self.assertEqual(UID.load(p, 4242), {0x14D4E0E4: 1})
            self.assertEqual(UID.load(p, 999), {})                                # 上一局的不认


class Labels(unittest.TestCase):
    def test_heroes_first_then_nearest_to_the_enemy_capped(self):
        ids = UID.UnitIds()
        army = [U(0x10 + i, "hfoo", x=100.0 * i) for i in range(UID.LABEL_MAX + 5)]
        hero = U(0x900, "Hamg", x=9000.0)                                         # 离敌人最远，也先标
        ids.assign([hero] + army)
        foe = [U(0x5000, "ogru", x=0.0)]
        chosen = ids.pick([hero] + army, foe)
        self.assertEqual(len(chosen), UID.LABEL_MAX)
        self.assertEqual(chosen[0].addr, 0x900)
        self.assertEqual([u.addr for u in chosen[1:4]], [0x10, 0x11, 0x12])

    def test_bubbles_show_the_number_and_remove_dropped_ones(self):
        ids = UID.UnitIds()
        a, b = U(0x10, "hfoo"), U(0x11, "hfoo")
        ids.assign([a, b])
        first = ids.bubbles([a, b], [])
        self.assertEqual(sorted(x.text for x in first), ["#1", "#2"])
        self.assertTrue(all(not x.style.tail and x.style.type_ms == 0 and not x.name for x in first))
        second = ids.bubbles([a], [])                                              # b 这一轮不标了
        self.assertEqual([(x.unit, x.remove) for x in second], [(0x10, False), (0x11, True)])

    def test_speech_instances_are_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "speak_api.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"autostart": {"banter": [5], "camtalk": [], "events": []}}, fh)
            self.assertTrue(UID.speech_on(5, p))
            self.assertFalse(UID.speech_on(3, p))
            self.assertFalse(UID.speech_on(3, os.path.join(d, "没有这个文件.json")))


class InTheBrain(unittest.TestCase):
    def test_brain_notes_carry_the_number(self):
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w = SimpleNamespace(t=10.0, my_heroes=[U(0x100, "Hamg")], my_army=[U(0x14D4E0E4, "hfoo")],
                                enemy_fighters=[], creeps=[])
            b.job_unit_ids(w)
            texts = [t for _t, t in b.notes]
            self.assertIn("编号 #2 hfoo@14D4E0E4", texts)
            b.note(11.0, "打野阵亡：hfoo@14D4E0E4")
            self.assertEqual(b.notes[-1][1], "打野阵亡：#2 hfoo@14D4E0E4")
            self.assertTrue(os.path.exists(os.path.join(d, "unit_ids.json")))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""加点顺序：高手录像 learned.json 优先，其次 AMAI 加点表主线（按第几个出的英雄分行），最后才是 races.py 里的手写表。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import races as R  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


class TestAmaiSkillOrder(unittest.TestCase):
    def test_archmage_water_and_aura_override_replay_and_amai(self):
        expected=['AHwe','AHab','AHwe','AHab','AHwe','AHab']
        self.assertEqual(R.SKILL_OVERRIDE['Hamg'][:6],expected)
        with tempfile.TemporaryDirectory() as d:
            b=Brain(FakeCmd(),None,d);b.race='human'
            b.skills={'Hamg':['AHbz']*10}
            got=[];pts=[6]
            b.act.hero_points=lambda _:pts[0]
            def learn(addr,code):got.append(code);pts[0]-=1;return True
            b.act.learn=learn
            b.job_heroes(build_world([U(1,'htow',0,0),U(2,'Hamg',0,0)],0,300,100,100,10,20))
            self.assertEqual(got,expected)

    def test_far_seer_major_line(self):
        self.assertEqual(R.amai_skill_order("orc", "Ofar")[:7], ["AOsf", "AOcl", "AOsf", "AOcl", "AOcl", "AOeq", "AOsf"])
        self.assertNotIn("AOfs", R.amai_skill_order("orc", "Ofar")[:7])      # 远见术 8 级以后
        self.assertEqual(R.amai_skill_order("human", "Hamg")[:2], ["AHwe", "AHab"])
        self.assertEqual(R.amai_skill_order("orc", "Hamg"), [])
        self.assertEqual(R.amai_skill_order(None, "Ofar"), [])

    def test_brain_uses_amai_when_no_replay(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.race = "orc"
            b.skills = {}
            learned = []
            pts = [2]
            b.act.hero_points = lambda addr: pts[0]

            def learn(addr, code):
                learned.append(code)
                pts[0] -= 1
                return True
            b.act.learn = learn
            hero = U(0x200, "Ofar", 0, 0)
            b.job_heroes(build_world([U(0x100, "ogre", -5184, 2944), hero], 0, 300.0, 100, 100, 10, 20))
            self.assertEqual(learned, ["AOsf", "AOcl"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""交战集火照 AMAI 放宽人数上限（engagement.FOCUS_CAP_*）。

09-23 对照 AMAI 交战时怎么决定集火。AMAI Jobs/FOCUSFIRE_CONTROL.eai：每 2 秒给一支部队定一个目标
（能秒掉的英雄 > 被定住的 > 全场血最少的），射程内所有人都打它，没有人数上限。我们原来普通目标最多 3 人，
1-420 12 分钟那一仗打出去的伤害反而更多（8280 对 5812）却只杀了 5 个，交战采样对面 4.6 人打一个目标、我们 3.1~3.4。
"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import engagement as ENG  # noqa: E402
from brain.engagement import Engagement  # noqa: E402
from test_tactics import U  # noqa: E402


class Focus(unittest.TestCase):
    def test_six_riflemen_shoot_the_same_grunt(self):
        rifles = [U(1 + i, "hrif", 0, i * 40) for i in range(6)]
        grunts = [U(100 + i, "ogru", 1, 450, i * 60) for i in range(3)]
        hits, _ = Engagement().plan(rifles, grunts, 0)
        loads = Counter(t.addr for _, t in hits)
        self.assertEqual(max(loads.values()), 6, f"原来最多 3 个打一个：{dict(loads)}")

    def test_caps_are_amai_scale(self):
        self.assertGreaterEqual(ENG.FOCUS_CAP_NORMAL, 6)
        self.assertGreater(ENG.FOCUS_CAP_CORE, ENG.FOCUS_CAP_NORMAL)
        self.assertGreater(ENG.FOCUS_CAP_FINISH_CORE, ENG.FOCUS_CAP_CORE)


if __name__ == "__main__":
    unittest.main()

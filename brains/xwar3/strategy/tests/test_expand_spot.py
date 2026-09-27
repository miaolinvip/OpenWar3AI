# -*- coding: utf-8 -*-
"""开分矿的落点：离金矿不能太近（引擎会退「不能建在离金矿过近的地方」），被退过的点要拉黑。

09-18 实机：一局「建造·被拒」301 次、「建造·没接下·htow」66 次，整局卡在开分矿上不发展 ——
普通建筑早就有拉黑，开矿这条路漏了，同一个落点被无限重试。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402


def world():
    hall = U(0x100, "htow", -5184, 2944)
    mine_home = U(0x600, "ngol", -5632, 3584, owner=15)
    mine_far = U(0x601, "ngol", -4864, -3200, owner=15)
    peas = [U(0x300 + i, "hpea", -5000, 2800) for i in range(6)]
    return build_world([hall, mine_home, mine_far] + peas, 0, 400.0, 900, 900, 20, 40), mine_far


class TestExpandSpot(unittest.TestCase):
    def test_candidates_keep_clear_of_the_mine(self):
        mine = SimpleNamespace(x=0.0, y=0.0)
        pts = A.expansion_candidates(mine, [])
        self.assertTrue(pts)
        self.assertGreaterEqual(min(max(abs(x), abs(y)) for x, y in pts), A.MINE_CLEAR)
        self.assertLessEqual(max((x ** 2 + y ** 2) ** 0.5 for x, y in pts), 900.0)

    def test_rejected_spot_is_not_tried_again(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w, mine = world()
            first = A.expansion_candidates(mine, w.my_halls)[0]
            b.bad_spots["htow"] = {first}                      # 上一轮被引擎退过
            b._execute(w, Want("expand", "htow", 2, 66, "mine"))
            tried = [(x, y) for _u, _o, x, y in b.cmd.builds]
            self.assertTrue(tried, "一个点都没试")
            self.assertNotIn(first, tried)                     # 拉黑的那个点不再试


if __name__ == "__main__":
    unittest.main()

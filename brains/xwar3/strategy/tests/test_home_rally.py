# -*- coding: utf-8 -*-
"""整队回城堡改去门前空地（runtime._home_rally / _army_rally，terrain.open_ground front=True）；
英雄避塔在 runtime 下令前生效（tactics.hero_tower_guard）。

09-22 不再让所有单位回基地附近集结，改在基地外的空地上集结。
1 号实例 8 局：「集结」48 条令全部落在城堡坐标上；整队回家 / 收队 / 打不过撤 / 骚扰收队同样 —— 出击层、守家、骚扰二十来处写的 home.x, home.y。
"""
import math
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "war3-modules"))
from brain import tactics as T              # noqa: E402
from brain import terrain as TR             # noqa: E402
from brain.runtime import Brain             # noqa: E402
from test_open_ground import doc            # noqa: E402
from test_runtime_smoke import FakeCmd      # noqa: E402
from test_tactics import U, world           # noqa: E402

FRONT = (900.0, 300.0, 1.0)
BACK = (-900.0, 700.0, 1.0)


class TwoSpots:
    """front=True（门前空地）和默认（养伤空地）各给一个固定点。"""
    def __init__(self):
        self.calls = []

    def open_ground(self, *a, **k):
        self.calls.append(k.get("front", False))
        return FRONT if k.get("front") else BACK

    def clear(self, x, y):
        return True


def run(intents_fn, units, t=600):
    w = world(units, t=t)
    sent = []
    with tempfile.TemporaryDirectory() as d:
        b = Brain(FakeCmd(), None, d)
        b.config.values["enable_harass"] = False
        b.attack.terrain = TwoSpots()
        b.attack.step = lambda v, h: intents_fn(v, h)
        b.recovery.step = lambda *a, **k: None
        b._do = lambda it, t: sent.append(it)
        b._hero_idle_probe = lambda *args: None
        b._read_hurt = lambda: set()
        b._read_resting = lambda: set()
        b.job_tactics(w)
    return b, sent


class HomeRally(unittest.TestCase):
    def test_gather_at_the_castle_goes_to_the_open_ground_in_front(self):
        feet = [U(10 + i, "hfoo", 0, 3000 + 30 * i, 0) for i in range(4)]
        b, sent = run(lambda v, h: [T.Intent("move", v.my_army, h.x, h.y, why="集结", control="hold")],
                      [U(1, "htow")] + feet)
        moves = [it for it in sent if it.why == "集结"]
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0].x, moves[0].y), FRONT[:2])
        self.assertEqual(moves[0].control, "hold", "别的都不变")
        self.assertEqual(b.counts["回城堡改去门前空地"], 1)

    def test_all_wounded_go_to_the_rest_spot_behind(self):
        feet = [U(10 + i, "hfoo", 0, 3000 + 30 * i, 0, hp=100, hp_max=420) for i in range(3)]
        b, sent = run(lambda v, h: [T.Intent("move", v.my_army, h.x, h.y, why="打不过，整队回家")],
                      [U(1, "htow")] + feet)
        mv = next(it for it in sent if it.why == "打不过，整队回家")
        self.assertEqual((mv.x, mv.y), BACK[:2])
        self.assertEqual(b.counts["回城堡改去养伤空地"], 1)

    def test_moves_elsewhere_and_worker_moves_are_untouched(self):
        feet = [U(10 + i, "hfoo", 0, 3000 + 30 * i, 0) for i in range(3)]
        peon = U(30, "hpea", 0, 2000, 0)
        b, sent = run(lambda v, h: [T.Intent("move", v.my_army, 2500, 2500, why="打野"),
                                    T.Intent("move", [peon] + v.my_army[:1], h.x, h.y, why="民兵回家")],
                      [U(1, "htow"), peon] + feet)
        self.assertEqual((sent[0].x, sent[0].y), (2500, 2500))
        mil = next(it for it in sent if it.why == "民兵回家")
        self.assertEqual((mil.x, mil.y), (0, 0))

    def test_hero_guard_runs_before_orders(self):
        hero = U(2, "Hamg", 0, 2800, 0)
        seer = U(101, "Ofar", 1, 3500, 0, hp=200, hp_max=800)
        b, sent = run(lambda v, h: [T.Intent("attack", [hero], seer.x, seer.y, seer, why="支援交战·集火残血核心")],
                      [U(1, "htow"), hero, U(100, "otrb", 1, 4000, 0), seer])
        mv = next(it for it in sent if hero in it.units)
        self.assertEqual(mv.kind, "move")
        self.assertGreater(math.hypot(mv.x - 4000, mv.y), 700 + T.TOWER_CLEAR_PAD)
        self.assertEqual(b.counts["英雄不进塔的射程·目标在塔下"], 1)


class FrontOpenGround(unittest.TestCase):
    def test_front_spot_faces_the_map_centre(self):
        """家在 (1500, 1500)，地图中心 (0, 0)：门前空地比城堡离中心近，默认（养伤）那个不会比门前的更靠中心。"""
        t = TR.Terrain(doc(west_forest=False))
        hx, hy = 1500.0, 1500.0
        blds = [SimpleNamespace(x=hx, y=hy)] + [SimpleNamespace(x=hx + 300 * math.cos(a), y=hy + 300 * math.sin(a))
                                                for a in (0.0, 1.6, 3.2, 4.7)]
        fx, fy, _ = t.open_ground(hx, hy, buildings=blds, rings=TR.RALLY_RINGS, front=True)
        bx, by, _ = t.open_ground(hx, hy, buildings=blds, rings=TR.RALLY_RINGS)
        self.assertLess(math.hypot(fx, fy), math.hypot(hx, hy) - 500)
        self.assertLessEqual(math.hypot(fx, fy), math.hypot(bx, by))
        self.assertLessEqual(math.hypot(fx - hx, fy - hy), max(TR.RALLY_RINGS) + 1)


if __name__ == "__main__":
    unittest.main()

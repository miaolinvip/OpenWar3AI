# -*- coding: utf-8 -*-
"""回血集合去空地（terrain.open_ground、ArmyRecovery.meet_at、runtime._rest_spot）。

09-22 用恢复卷轴不在家里用：家里空间本来就小，少血单位不在家里集结，改到周围空地上；
英雄也找一块空地，所有需要恢复的单位集结到带着恢复卷轴的英雄身边。
原来：用卷轴的集合点 = 伤兵重心（伤兵都在家里就落在建筑堆里）；养伤的兵回城堡坐标。1-284 11:49 那一串
「给英雄让路」「卡太久，英雄自己错身重新寻路」里挡路的全是养伤的兵。
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
from brain.recovery import ArmyRecovery     # noqa: E402
from brain.runtime import Brain             # noqa: E402
from test_runtime_smoke import FakeCmd      # noqa: E402
from test_tactics import U, world           # noqa: E402

STEP = 128.0
N = 48


def doc(west_forest=True):
    """家在 (0,0)。西边 x < -700 是树林（能走不了），其余到 ±3000 都是空地。"""
    x0 = y0 = -(N / 2) * STEP + STEP / 2
    rows = []
    for j in range(N):
        y = y0 + j * STEP
        row = ""
        for i in range(N):
            x = x0 + i * STEP
            row += "1" if (west_forest and x < -700) or abs(x) > 3000 or abs(y) > 3000 else "0"
        rows.append(row)
    return {"x0": x0, "y0": y0, "step": STEP, "n": N, "blocked_rows": rows, "trees": []}


def base():
    """城堡 + 一圈建筑 + 金矿在东北。"""
    blds = [SimpleNamespace(x=0.0, y=0.0)] + [SimpleNamespace(x=300.0 * math.cos(a), y=300.0 * math.sin(a))
                                              for a in (0.0, 1.6, 3.2, 4.7)]
    mine = [SimpleNamespace(x=600.0, y=600.0)]
    return blds, mine


class OpenGround(unittest.TestCase):
    def test_spot_is_clear_of_buildings_mines_and_creeps_and_avoids_the_forest(self):
        t = TR.Terrain(doc())
        blds, mine = base()
        creep = [SimpleNamespace(x=0.0, y=-1400.0)]
        x, y, opn = t.open_ground(0.0, 0.0, buildings=blds, mines=mine, avoid=creep)
        self.assertGreaterEqual(min(math.hypot(x - b.x, y - b.y) for b in blds), TR.CLEAR_BUILD_R)
        self.assertGreaterEqual(math.hypot(x - 600, y - 600), TR.CLEAR_MINE_R)
        self.assertGreaterEqual(math.hypot(x, y + 1400), 1200.0)
        self.assertGreater(x, -700 + TR.OPEN_R * 0.5, "不贴着西边的树林")
        self.assertGreaterEqual(opn, 0.95)

    def test_nothing_walkable_gives_none(self):
        d = doc()
        d["blocked_rows"] = ["1" * N for _ in range(N)]
        self.assertIsNone(TR.Terrain(d).open_ground(0.0, 0.0))


class FixedTerrain:
    """open_ground 返回固定点，记下被问了几次。"""
    def __init__(self, p):
        self.p, self.calls = p, 0

    def open_ground(self, *a, **k):
        self.calls += 1
        return self.p

    def clear(self, x, y):
        return True                      # 下令前的站不站得下（runtime._on_ground）：这里哪里都站得下


class Bag:
    USESLOT1 = 0xD0028

    def __init__(self, bag):
        self.bag = bag

    def inventory(self, a):
        return list(self.bag) if a == 3 else None

    def buy_item(self, *a):
        return True

    def use_item(self, *a):
        return True


class ScrollMeetsOnOpenGround(unittest.TestCase):
    def test_holder_and_wounded_gather_on_the_open_spot_and_it_does_not_drift(self):
        # 伤兵散在基地另一头（离英雄 > RECOVERY_READY_R）才需要集合；本来就围在英雄身边时就地放（见下一条）
        wounded = [U(10 + i, "hfoo", x=900 + 30 * i, y=700, hp=150, hp_max=420) for i in range(5)]
        w = world([U(1, "htow"), U(2, "hvlt", x=300), U(3, "Hamg", x=200, y=-100)] + wounded, t=300)
        w.gold = 1000
        r = ArmyRecovery()
        r.terrain = FixedTerrain((1200.0, -900.0, 1.0))
        out = r.step(w, Bag(["sreg"] + [None] * 5), None)       # 英雄手上已有卷轴 -> 直接集合
        self.assertEqual(r.phase, "gather")
        center = out[0]
        self.assertEqual((center.x, center.y), (1200.0, -900.0))
        self.assertIn(3, [u.addr for u in center.units], "英雄也去空地")
        self.assertTrue({u.addr for u in wounded} <= {u.addr for u in center.units})
        for u in wounded:
            u.x += 300                                          # 走了几步，重心变了
        w.t += 2
        out = r.step(w, Bag(["sreg"] + [None] * 5), None)
        self.assertEqual((out[0].x, out[0].y), (1200.0, -900.0))
        self.assertEqual(r.terrain.calls, 1, "定下来就不再重找")
        self.assertEqual(r.counts["整队恢复·集合点·空地"], 1)

    def test_already_gathered_around_the_hero_uses_it_on_the_spot(self):
        """人已经站在英雄 500 以内：不为了「去空地」再挪一次，就地放最快（09-21 起：节省路程和时间）。"""
        wounded = [U(10 + i, "hfoo", x=100 + 30 * i, y=100, hp=150, hp_max=420) for i in range(5)]
        w = world([U(1, "htow"), U(2, "hvlt", x=300), U(3, "Hamg", x=200, y=-100)] + wounded, t=300)
        w.gold = 1000
        r = ArmyRecovery()
        r.terrain = FixedTerrain((1200.0, -900.0, 1.0))
        r.step(w, Bag(["sreg"] + [None] * 5), None)
        self.assertEqual(r.phase, "used")

    def test_without_terrain_falls_back_to_the_wounded_centre(self):
        wounded = [U(10 + i, "hfoo", x=900 + 30 * i, y=700, hp=150, hp_max=420) for i in range(5)]
        w = world([U(1, "htow"), U(2, "hvlt", x=300), U(3, "Hamg", x=200, y=-100)] + wounded, t=300)
        w.gold = 1000
        r = ArmyRecovery()
        out = r.step(w, Bag(["sreg"] + [None] * 5), None)
        cx = sum(u.x for u in wounded) / len(wounded)
        self.assertAlmostEqual(out[0].x, cx)


class RestingGoToOpenGround(unittest.TestCase):
    def test_resting_units_near_home_also_move_out_to_the_open_spot(self):
        home = U(1, "htow")
        feet = [U(10 + i, "hfoo", x=100 + i * 20) for i in range(4)]
        far = U(20, "hfoo", x=0, y=3000)
        w = world([home] + feet + [far, U(100, "htow", 1, 4000)], t=600)
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            b.config.values["enable_harass"] = False
            b.attack.terrain = FixedTerrain((-900.0, 700.0, 1.0))
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 1000, 0, why="打野")]
            b.recovery.step = lambda *a, **k: None
            sent = []
            b._do = lambda it, t: sent.append(it)
            b._hero_idle_probe = lambda *args: None
            b._read_hurt = lambda: set()
            b._read_resting = lambda: {10, 20}
            b.job_tactics(w)
        moves = [it for it in sent if it.kind == "move" and "养伤" in it.why]
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0].x, moves[0].y), (-900.0, 700.0))
        self.assertEqual(sorted(u.addr for u in moves[0].units), [10, 20], "离城堡 100 的也去空地，不在家里堆着")
        self.assertIn("空地", moves[0].why)


if __name__ == "__main__":
    unittest.main()

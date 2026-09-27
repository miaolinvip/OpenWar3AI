# -*- coding: utf-8 -*-
"""分矿落点上的小动物（G.is_critter、tactics.CRITTER_*、runtime.critter_blocked / CRITTER_WAIT_S）。

09-22 分矿落点上有一只小动物，导致分矿一直建不起来（1-303 7 分）；英雄清完野后要优先清掉它。
战斗帧：浣熊 nrac（15 血）整局在分矿 (-4864,-3200) 东南转悠；第一次下开矿令时它离这座矿的历史落点 (-4352,-2624) 只有 120，
引擎当场退单、这个点被永久拉黑，之后换点被退 77 次，12 分才在离矿 779 的地方盖起来。
"""
import os
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain import runtime as R  # noqa: E402
from brain import tactics as T  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

MINE = (-4864, -3200)


def world(hero_at=(-4500, -2600), critter_at=(-4300, -2700), creep_at=None, extra=(), t=500.0):
    units = [U(0x100, "htow", -5184, 2944), U(0x600, "ngol", -5632, 3584, owner=15), U(0x601, "ngol", *MINE, owner=15)]
    units += [U(0x300 + i, "hpea", -5000, 2800) for i in range(6)]
    units += [U(0x10 + i, "hfoo", -5000, 2600 - 60 * i) for i in range(3)]
    if hero_at is not None:
        units.append(U(0x2, "Hamg", *hero_at))
    if critter_at is not None:
        units.append(U(0x700, "nrac", *critter_at, owner=15))
    if creep_at is not None:
        units.append(U(0x800, "nmrl", *creep_at, owner=12))
    units += list(extra)
    return build_world(units, 0, t, 900, 900, 20, 40, levels={0x2: 3})


class CritterIsNotAShop(unittest.TestCase):
    def test_classification(self):
        w = world(extra=[U(0x900, "ngme", -4000, -3000, owner=15)])
        self.assertEqual([c.type for c in w.critters], ["nrac"])           # 金矿、地精商店不算；野怪（owner 12）不算


class Hunt(unittest.TestCase):
    def seq(self, w):
        s = T.AttackSequence(dict(PROFILE))
        s.allow_attack = False
        s.critters = list(w.critters)
        return s

    def hunt(self, out):
        return [i for i in out if i.kind == "force_attack"]

    def test_free_hero_attacks_it_with_the_attack_order(self):
        w = world()
        s = self.seq(w)
        h = self.hunt(s.step(w, w.my_halls[0]))
        self.assertEqual(len(h), 1)
        self.assertEqual([u.type for u in h[0].units], ["Hamg"])
        self.assertEqual(h[0].target.type, "nrac")
        self.assertEqual(s.counts.get("小动物·派英雄"), 1)

    def test_hero_still_fighting_waits(self):
        w = world(creep_at=(-4450, -2550))                                # 英雄 900 内还有野怪：打完再去
        s = self.seq(w)
        self.assertFalse(self.hunt(s.step(w, w.my_halls[0])))
        self.assertEqual(s.counts.get("小动物·英雄在忙，等它"), 1)

    def test_no_hero_nearby_sends_the_nearest_soldier(self):
        extra = [U(0x50, "hgyr", -4350, -2700), U(0x51, "hrif", -4100, -2300)]   # 飞行器更近但只打空中
        w = world(hero_at=(-5184, 2400), extra=extra)                      # 英雄在家，5000 外
        s = self.seq(w)
        h = self.hunt(s.step(w, w.my_halls[0]))
        self.assertEqual([u.addr for u in h[0].units], [0x51])
        self.assertEqual(s.counts.get("小动物·派兵"), 1)

    def test_nothing_when_no_critter(self):
        w = world(critter_at=None)
        s = self.seq(w)
        self.assertFalse(self.hunt(s.step(w, w.my_halls[0])))


class ExpandSpots(unittest.TestCase):
    def test_blocked_is_square(self):
        c = U(1, "nrac", 0, 0, owner=15)
        self.assertEqual(R.critter_blocked([(300, 300), (400, 0), (0, 351)], [c]), {(300, 300), (0, 351)})

    def brain_on(self, w, known):
        d = tempfile.mkdtemp()
        b = Brain(FakeCmd(), None, d)
        b.known_halls = {f"{MINE[0]},{MINE[1]}": [known]}
        return b

    def test_waits_while_the_best_spot_is_covered_and_does_not_blacklist_it(self):
        best = (-4352, -2624)                                              # 1-303 那座矿的历史落点
        w = world(critter_at=(best[0] + 70, best[1] - 90))
        b = self.brain_on(w, best)
        b._execute(w, Want("expand", "htow", 2, 66, "mine"))
        self.assertEqual(b.cmd.builds, [])                                 # 一个点都不试（一试就被退、被拉黑）
        self.assertEqual(b.counts.get("开分矿·小动物压着首选落点·等清掉"), 1)
        self.assertNotIn(best, b.bad_spots.get("htow", set()))

    def test_after_the_wait_uses_other_spots_but_still_not_the_covered_one(self):
        best = (-4352, -2624)
        w = world(critter_at=(best[0] + 70, best[1] - 90))
        b = self.brain_on(w, best)
        b._execute(w, Want("expand", "htow", 2, 66, "mine"))
        w2 = replace(w, t=w.t + R.CRITTER_WAIT_S + 1.0)
        b._execute(w2, Want("expand", "htow", 2, 66, "mine"))
        tried = [(x, y) for _u, _o, x, y in b.cmd.builds]
        self.assertTrue(tried)
        self.assertNotIn(best, tried)

    def test_best_spot_used_once_it_is_clear(self):
        best = (-4352, -2624)
        w = world(critter_at=(-4300, -3900))                               # 在矿另一边，压不着
        b = self.brain_on(w, best)
        b._execute(w, Want("expand", "htow", 2, 66, "mine"))
        self.assertEqual([(x, y) for _u, _o, x, y in b.cmd.builds][:1], [best])

    def test_critters_handed_to_tactics_only_after_guards_are_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            w = world()
            self.assertEqual(b._expansion_critters(w), [])                 # 开矿检查还没走到这座矿
            b.expand_target = (w.t, *MINE)
            self.assertEqual([c.type for c in b._expansion_critters(w)], ["nrac"])
            self.assertEqual(b._expansion_critters(replace(w, t=w.t + 30.0)), [])   # 20 秒没刷新 = 不开这座了


if __name__ == "__main__":
    unittest.main()

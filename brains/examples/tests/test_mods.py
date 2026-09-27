# -*- coding: utf-8 -*-
"""玩法模组示例（09-25）：方案库认得出来、Mod 的定时器按游戏时间走、finish 让运行器停、英雄肉鸽的三选一流程（假 Game，不需要游戏）。"""
import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openwar3  # noqa: E402
from openwar3 import Mod  # noqa: E402
from openwar3 import schemes as S  # noqa: E402
from mod_endless_defense import EndlessDefense  # noqa: E402
from mod_hero_roguelike import HeroRoguelike  # noqa: E402


class SchemeTest(unittest.TestCase):
    def test_builtin_mods_listed_and_loadable(self):
        for ref, cls in (("builtin/hero-roguelike", HeroRoguelike), ("builtin/endless-defense", EndlessDefense)):
            s = S.get(ref)
            self.assertEqual((s["kind"], s["fair"], s["judge"]), ("mod", False, False))
            loaded = S.load_bot_class(s)
            self.assertEqual(loaded.__name__, cls.__name__)
            self.assertTrue(issubclass(loaded, Mod))

    def test_validate_kind_mod_defaults(self):
        m = S.validate({"id": "my-mod", "kind": "mod", "entry": "m.py", "fair": True, "judge": True})
        self.assertEqual((m["kind"], m["fair"], m["judge"]), ("mod", False, False))   # 模组不能公平、胜负自己报
        with self.assertRaises(S.SchemeError):
            S.validate({"id": "x1", "kind": "brain", "entry": "m.py"})


class FakeG:
    def __init__(self):
        self.t = 10.0

    def clock(self):
        return self.t


class TimerTest(unittest.TestCase):
    def test_every_after_and_finish(self):
        m = Mod()
        m._clock_hint = 10.0
        fired = []
        m.every(5, lambda g: fired.append(("every", g.t)))
        m.after(2, lambda g: fired.append(("after", g.t)))
        g = FakeG()
        for t in (11.0, 12.5, 15.0, 17.0, 20.1):
            g.t = t
            m.on_tick(g)
        self.assertEqual(fired, [("after", 12.5), ("every", 15.0), ("every", 20.1)])
        self.assertIsNone(m._finish)
        m.finish("win", "守住了")
        self.assertEqual(m._finish, ("win", "守住了"))
        self.assertTrue(Mod.mod_rules)


class _UI:
    def __init__(self):
        self.choices, self.toasts = [], []

    def choice(self, title, options, key, pause, on_pick):
        ch = SimpleNamespace(title=title, options=options, pause=pause, on_pick=on_pick, closed=False)
        self.choices.append(ch)
        return ch

    def toast(self, text, seconds=3.0):
        self.toasts.append(text)


class RoguelikeTest(unittest.TestCase):
    def test_two_levels_two_picks(self):
        m = HeroRoguelike()
        m.hero = SimpleNamespace(handle=(5, 6), addr=500)
        m.hero_h = 77
        applied = []
        g = SimpleNamespace(ui=_UI())
        import mod_hero_roguelike as R
        orig = R.REWARDS
        R.REWARDS = [(f"r{i}", "d", (lambda i: lambda g_, h: applied.append((i, h)))(i)) for i in range(5)]
        try:
            ev = SimpleNamespace(kind="hero.levelup", handle=(5, 6), a=3, b=1)
            m.on_event(g, ev)
            self.assertEqual(m.picks, 2)
            self.assertEqual(len(g.ui.choices), 1)
            first = g.ui.choices[0]
            self.assertTrue(first.pause)
            self.assertEqual(len(first.options), 3)
            first.closed = True
            first.on_pick(g, 0)                                   # 选了 -> 奖励给英雄，接着弹第二次
            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0][1], 77)
            self.assertEqual(len(g.ui.choices), 2)
            g.ui.choices[1].closed = True
            g.ui.choices[1].on_pick(g, 2)
            self.assertEqual((m.picks, len(g.ui.choices)), (0, 2))   # 选完不再弹
        finally:
            R.REWARDS = orig

    def test_hero_death_is_loss(self):
        m = HeroRoguelike()
        m.hero = SimpleNamespace(handle=(5, 6), addr=500)
        m.on_event(SimpleNamespace(), SimpleNamespace(kind="unit.died", handle=(5, 6)))
        self.assertEqual(m._finish[0], "loss")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""公平模式补全（09-24 A3）：地上物品只留视野内、生产表只留看得见的建筑、事件只留自己的/看得见的；
last_seen 记住最后一次看见的敌人（看见它死了就忘掉，换局清空）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openwar3  # noqa: E402
import w3world as W  # noqa: E402


def unit(addr, typ, owner, x, y, vis=0, alive=True):
    return W.Unit(index=0, addr=addr, handle_lo=addr, handle_hi=addr, type=typ, owner=owner,
                  flags=W.ALIVE if alive else 0, x=x, y=y, hp=100.0 if alive else 0.0, hp_max=100.0, mana=0.0,
                  mana_max=0.0, order=None, order_target=None, order_x=0.0, order_y=0.0, task_target=None, task_x=None,
                  task_y=None, level=0, xp=0, skill_points=0, first_seen=0, visible_mask=vis)


def state(units, items=(), prods=(), clock=10.0, epoch=1, tod=12.0):
    s = W.WorldState(1, 0, clock, epoch, 0, True, 200, 50, 0, 0, 0, [], list(units), list(items))
    s.by_handle = {u.handle: u for u in units}
    s.time_of_day = tod
    s.production = list(prods)
    return s


class World:
    def __init__(self):
        self.s, self.evs = None, []

    def read(self):
        return self.s

    def events(self):
        e, self.evs = self.evs, []
        return e


class Stub(openwar3.Game):
    def __init__(self, fair=True):
        self.world, self.fair, self.player = World(), fair, None
        self._snap, self._snap_at, self._recent_orders = None, 0.0, {}

    def feed(self, s):
        self.world.s = s
        return self.snapshot(max_age=0)


ME, FOE = 0, 1


class FairView(unittest.TestCase):
    def setUp(self):
        self.mine = unit(1, "hfoo", ME, 0, 0, vis=1)
        self.seen = unit(2, "ogru", FOE, 500, 0, vis=0b11)        # 我也看得见
        self.hidden = unit(3, "ogru", FOE, 9000, 0, vis=0b10)     # 只有对方自己看得见
        self.hidden_bld = unit(4, "obar", FOE, 9000, 500, vis=0b10)

    def test_units_items_production_filtered(self):
        items = [W.Item(10, 1, 1, "phea", 300.0, 0.0, 50.0), W.Item(11, 2, 2, "phea", 9000.0, 0.0, 50.0)]
        prods = [W.Production(self.hidden_bld.handle, "queue", ["ogru"], 20.0, 5.0, 1.0, False, True),
                 W.Production(self.mine.handle, "queue", ["hfoo"], 20.0, 5.0, 1.0, False, True)]
        g = Stub()
        s = g.feed(state([self.mine, self.seen, self.hidden, self.hidden_bld], items, prods))
        self.assertEqual({u.addr for u in s.units}, {1, 2})
        self.assertEqual([i.addr for i in s.items], [10])                   # 9000 外那瓶看不见
        self.assertEqual([p.building for p in s.production], [self.mine.handle])
        full = Stub(fair=False).feed(state([self.mine, self.seen, self.hidden, self.hidden_bld], items, prods))
        self.assertEqual((len(full.units), len(full.items), len(full.production)), (4, 2, 2))

    def test_last_seen_memory(self):
        g = Stub()
        g.feed(state([self.mine, self.seen], clock=10.0))
        moved_away = unit(2, "ogru", FOE, 3000, 0, vis=0b10)               # 走出视野
        g.feed(state([self.mine, moved_away], clock=15.0))
        ls = g.last_seen()
        self.assertEqual(len(ls), 1)
        u, t, age = ls[0]
        self.assertEqual(((u.x, u.y), t, age), ((500, 0), 10.0, 5.0))      # 记的是最后看见时的位置
        self.assertEqual(g.last_seen(max_age=3.0), [])
        dead = unit(2, "ogru", FOE, 500, 0, vis=0b11, alive=False)
        g.feed(state([self.mine, dead], clock=16.0))
        self.assertEqual(g.last_seen(), [])                                  # 看见它死了：忘掉
        g.feed(state([self.mine, self.seen], clock=17.0))
        g.feed(state([self.mine], clock=1.0, epoch=2))
        self.assertEqual(g.last_seen(), [])                                  # 新的一局清空

    def test_events_filtered(self):
        g = Stub()
        g.feed(state([self.mine, self.seen, self.hidden]))

        def ev(kind, handle, owner, a=0):
            return W.Event(1, kind, 10.0, handle[0], handle, "x", owner, a, 0, 0.0, 0.0, 1.0)
        g.world.evs = [ev("order.changed", self.hidden.handle, FOE), ev("order.changed", self.seen.handle, FOE),
                       ev("unit.died", self.mine.handle, ME), ev("damage", self.hidden.handle, FOE, a=self.mine.addr),
                       ev("damage", self.hidden.handle, FOE, a=999)]
        kept = g.events()
        self.assertEqual([(e.kind, e.handle[0]) for e in kept],
                         [("order.changed", 2), ("unit.died", 1), ("damage", 3)])   # 我打它的那一下留着


if __name__ == "__main__":
    unittest.main()

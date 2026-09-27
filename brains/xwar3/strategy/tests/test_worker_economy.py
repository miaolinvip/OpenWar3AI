# -*- coding: utf-8 -*-
"""采空主矿后的农民必须转二矿/伐木，不能被旧金矿壳循环吸回去。"""
import tempfile
import unittest

from brain.runtime import Brain
from brain.world import build_world
from test_runtime_smoke import FakeCmd, U, A


class WorkerReassignment(unittest.TestCase):
    def test_two_idle_receipts_blacklist_depleted_mine_and_use_expansion(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d); b.set_race("human")
            main = U(1, "hkee", 0, 0)
            expo = U(2, "htow", 4000, 0)
            old = U(10, "ngol", 0, 600, owner=15)
            fresh = U(11, "ngol", 4000, 600, owner=15)
            peas = [U(20+i, "hpea", 40+i*30, 500) for i in range(2)]
            w = build_world([main, expo, old, fresh] + peas, 0, 100, 500, 200, 20, 40)
            old_id = (old.addr, old.handle_lo, old.handle_hi)
            for p in peas:
                b.gold_crew[p.addr] = old.addr
                b.gold_attempts[p.addr] = (old_id, 80.0)
                b.sent_at[p.addr] = 80.0

            b.job_gold(w)

            self.assertIn(old_id, b.depleted_mines)
            self.assertTrue(cmd.gathers)
            self.assertEqual({target for _worker, target, order in cmd.gathers if order == A.ORDER_HARVEST}, {fresh.addr})
            self.assertEqual(set(b.gold_crew.values()), {fresh.addr})

    def test_depleted_only_mine_releases_idle_workers_to_lumber(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d); b.set_race("human")
            b.act.scan_trees = lambda _x, _y: [dict(addr=0x9000, x=900, y=0)]
            main = U(1, "hkee", 0, 0)
            old = U(10, "ngol", 0, 600, owner=15)
            peas = [U(20+i, "hpea", 40+i*30, 500) for i in range(2)]
            w = build_world([main, old] + peas, 0, 100, 500, 0, 10, 20)
            old_id = (old.addr, old.handle_lo, old.handle_hi)
            for p in peas:
                b.gold_crew[p.addr] = old.addr
                b.gold_attempts[p.addr] = (old_id, 80.0)
                b.sent_at[p.addr] = 80.0

            b.job_gold(w)
            b.job_lumber(w)

            self.assertIn(old_id, b.depleted_mines)
            self.assertFalse(b.gold_crew)
            self.assertEqual(set(b.lumber_crew), {p.addr for p in peas})
            self.assertEqual({target for _worker, target, order in cmd.gathers if order == A.ORDER_HARVEST}, {0x9000})

    def test_reserved_former_militia_are_not_stolen_by_normal_building(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d); b.set_race("human")
            reserved = U(20, "hpea", 4000, 0)
            ordinary = U(21, "hpea", 200, 0)
            hall = U(1, "hkee", 0, 0)
            w = build_world([hall, reserved, ordinary], 0, 100, 500, 300, 5, 20)
            b.militia_expand_builders = {reserved.addr}; b.militia_expand_until = 250
            self.assertEqual([u.addr for u in b._builder(w)], [ordinary.addr])
            self.assertEqual({u.addr for u in b._builder(w, expansion=True)}, {reserved.addr, ordinary.addr})


if __name__ == "__main__":
    unittest.main()

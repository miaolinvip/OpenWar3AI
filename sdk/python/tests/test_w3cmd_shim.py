# -*- coding: utf-8 -*-
"""w3cmd 兼容层（P3）：老方法名 -> W3P 语义命令 / 世界块字段。假车道记下每条命令，假世界给一份固定快照。"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3cmd  # noqa: E402
import w3fast as F  # noqa: E402
import w3world as W  # noqa: E402

HERO, FOOT, ENEMY, ITEM, TREE = 0x14000010, 0x14000020, 0x14000030, 0x14100040, 0x14200050


def fbits(v):
    return struct.unpack("<I", struct.pack("<f", v))[0]


class FakeLane:
    def __init__(self, status=0, values=(0,) * 8, verdict=None):
        self.sent = []
        self.status, self.values, self.verdict = status, values, verdict

    def commands(self, calls):
        self.sent += calls
        return [F.Receipt(c.op, self.status, 1, self.verdict, 0, 0, tuple(self.values)) for c in calls]


def unit(addr, typ, owner=0, flags=W.ALIVE, **kw):
    base = dict(index=0, addr=addr, handle_lo=addr & 0xFFFF, handle_hi=7, type=typ, owner=owner, flags=flags,
                x=100.0, y=200.0, hp=400.0, hp_max=500.0, mana=50.0, mana_max=200.0, order=None,
                order_target=None, order_x=0.0, order_y=0.0, task_target=None, task_x=None, task_y=None,
                level=0, xp=0, skill_points=0, first_seen=0)
    base.update(kw)
    return W.Unit(**base)


class FakeWorld:
    def __init__(self):
        hero = unit(HERO, "Hamg", flags=W.ALIVE | W.HERO | W.DETAIL | W.ORDER_KNOWN | W.TASK_KNOWN | W.HAS_TASK,
                    level=3, xp=560, order=0x000D0079, order_x=5.0, order_y=6.0, task_x=7.0, task_y=8.0,
                    abilities=[W.Ability("AHbz", 2, 4.5, 0x200), W.Ability("AInv", 1, 0.0, 0)],
                    buffs=["BHab"], items=["stwp", None, None, None, None, None], has_detail=True)
        foot = unit(FOOT, "hfoo", flags=W.ALIVE | W.TASK_KNOWN | W.HAS_TASK, task_target=(9, 9))
        enemy = unit(ENEMY, "hkni", owner=1, flags=W.ALIVE | W.ORDER_KNOWN, order=0x000D0012, order_target=(3, 4))
        self.state = W.WorldState(1, 0, 10.0, 1, 0, True, 300, 50, 0, 0, 0, [], [hero, foot, enemy],
                                  [W.Item(ITEM, 11, 12, "pman", 1.0, 2.0, 75.0), W.Item(0x14100099, 1, 1, "tkno", 0, 0, 0.0)])
        self.reads = 0

    def read(self):
        self.reads += 1
        return self.state

    def trees(self):
        return [W.Tree(TREE, 5, 5, "LTlt", 0.0, 0.0, 50.0)]


class Orders(unittest.TestCase):
    def setUp(self):
        self.lane, self.world = FakeLane(), FakeWorld()
        self.c = w3cmd.Cmd.with_backends(self.lane, self.world)

    def last(self):
        return self.lane.sent[-1]

    def test_move_carries_handle_and_point(self):
        self.assertTrue(self.c.move_to(FOOT, 10.0, 20.0))
        c = self.last()
        self.assertEqual((c.op, c.args[0], c.args[1], c.args[3]), (F.OP["point"], FOOT, FOOT & 0xFFFF, w3cmd.ORDER_SMART))
        self.assertEqual((c.args[7], c.args[8]), (fbits(10.0), fbits(20.0)))

    def test_target_resolves_unit_item_tree(self):
        self.c.target_order(FOOT, ENEMY, w3cmd.ORDER_SMART)
        self.assertEqual(self.last().args[4:7], [ENEMY, ENEMY & 0xFFFF, 7])
        self.c.get_item(HERO, ITEM)
        self.assertEqual(self.last().args[4:7], [ITEM, 11, 12])
        self.c.target_order(FOOT, TREE, w3cmd.ORDER_HARVEST)
        self.assertEqual(self.last().args[4:7], [TREE, 5, 5])
        self.c.target_order(FOOT, 0x15555555, w3cmd.ORDER_SMART)          # 世界里没有：裸地址
        self.assertEqual(self.last().args[4:7], [0x15555555, 0, 0])

    def test_immediate_and_casts(self):
        self.c.immediate_order(HERO, 0x000D0084)
        self.assertEqual((self.last().op, self.last().args[3]), (F.OP["immediate"], 0x000D0084))
        self.c.stop(FOOT)
        self.assertEqual(self.last().args[3], w3cmd.ORDER_STOP)
        self.c.cast_point_ctx(HERO, 0x000D0079, 1.0, 2.0)
        self.assertEqual((self.last().op, self.c.last_cast_path), (F.OP["point"], "W3P点"))
        self.c.cast_target(HERO, 0x000D007C, FOOT)
        self.assertEqual((self.last().op, self.last().args[4]), (F.OP["target"], FOOT))

    def test_use_item_modes(self):
        self.c.use_item(HERO, w3cmd.Cmd.USESLOT1 + 2)
        self.assertEqual((self.last().args[9], self.last().args[10]), (2, 0))
        self.c.use_item(HERO, w3cmd.Cmd.USESLOT1, x=1.0, y=2.0)
        self.assertEqual(self.last().args[10], 1)
        self.c.use_item(HERO, w3cmd.Cmd.USESLOT1, target=FOOT)
        self.assertEqual((self.last().args[10], self.last().args[4]), (2, FOOT))
        self.assertFalse(self.c.use_item(HERO, 0x000D0012))              # 不是 useslot
        self.c.buy_item(0x14300000, w3cmd.rawcode("stel"))
        self.assertEqual((self.last().op, self.last().args[3]), (F.OP["buy"], w3cmd.rawcode("stel")))

    def test_rejection_is_false_with_receipt(self):
        self.lane.status, self.lane.verdict = 1, 1001
        self.assertFalse(self.c.cast_target(HERO, 0x000D007C, ENEMY))
        self.assertEqual(self.c.last_receipt.verdict, 1001)

    def test_fogged_target_falls_back_to_attack_move(self):
        """老通道右键带坐标、目标无效时回落到点；W3P 严格拒 1001 —— 兼容层替大脑回落成对它位置的攻击移动。"""
        class FogLane(FakeLane):
            def commands(self, calls):
                self.sent += calls
                return [F.Receipt(c.op, 1 if c.op == F.OP["target"] else 0, 0,
                                  1001 if c.op == F.OP["target"] else None, 0, 0, (0,) * 8) for c in calls]
        lane = FogLane()
        c = w3cmd.Cmd.with_backends(lane, self.world)
        self.assertTrue(c.target_order(FOOT, ENEMY, w3cmd.ORDER_SMART))
        p = lane.sent[-1]
        self.assertEqual((p.op, p.args[3], p.args[7], p.args[8]), (F.OP["point"], w3cmd.ORDER_ATTACK, fbits(100.0), fbits(200.0)))
        self.assertEqual(c.fog_fallbacks, 1)
        n = len(lane.sent)
        self.assertFalse(c.target_order(FOOT, ENEMY, w3cmd.ORDER_HARVEST))     # 别的令不回落
        self.assertEqual(len(lane.sent), n + 1)
        self.assertFalse(c.cast_target(HERO, 0x000D007C, ENEMY))               # 施法也不回落

    def test_command_invalidates_snapshot(self):
        self.c.current_order(HERO)
        n = self.world.reads
        self.c.current_order(HERO)
        self.assertEqual(self.world.reads, n)          # 缓存里
        self.c.move_to(FOOT, 0.0, 0.0)
        self.c.current_order(HERO)
        self.assertGreater(self.world.reads, n)        # 下过令，重读


class Batches(unittest.TestCase):
    """09-23 体检：一批命令只调一次 lane.commands（只等游戏线程一次）；仲裁、日志逐条照旧。"""

    class CountingLane(FakeLane):
        def __init__(self, reject=()):
            super().__init__()
            self.batches = 0
            self.reject = reject

        def commands(self, calls):
            self.batches += 1
            self.sent += calls
            out = []
            for c in calls:
                bad = c.op == F.OP["target"] and c.args[4] in self.reject
                out.append(F.Receipt(c.op, 1 if bad else 0, 1, 1001 if bad else None, 0, 0, (7,) * 8))
            return out

    def setUp(self):
        self.lines = []
        self.orig = w3cmd._cmdlog_write
        w3cmd._cmdlog_write = self.lines.append
        import modlog
        self.orig_name = modlog._current
        modlog._current = "brain"

    def tearDown(self):
        import modlog
        w3cmd._cmdlog_write = self.orig
        modlog._current = self.orig_name

    def test_move_many_one_batch_logged_each(self):
        lane = self.CountingLane()
        c = w3cmd.Cmd.with_backends(lane, FakeWorld())
        oks = c.move_many([(FOOT, 1.0, 2.0, 0x000D0012), (HERO, 3.0, 4.0, 0x000D0012)])
        self.assertEqual((oks, lane.batches, len(lane.sent)), ([True, True], 1, 2))
        self.assertEqual([l.split()[3] for l in self.lines], ["move_to", "move_to"])
        self.assertTrue(all(" ok" in l for l in self.lines))

    def test_target_many_fog_fallback_second_batch(self):
        lane = self.CountingLane(reject={ENEMY})
        c = w3cmd.Cmd.with_backends(lane, FakeWorld())
        oks = c.target_many([(FOOT, ENEMY, w3cmd.ORDER_SMART), (HERO, FOOT, w3cmd.ORDER_SMART)])
        self.assertEqual(oks, [True, True])
        self.assertEqual(lane.batches, 2)                                   # 一批目标令 + 一批回落
        self.assertEqual((lane.sent[-1].op, lane.sent[-1].args[3]), (F.OP["point"], w3cmd.ORDER_ATTACK))
        self.assertEqual(c.fog_fallbacks, 1)

    def test_feasible_and_tech_many(self):
        lane = self.CountingLane()
        c = w3cmd.Cmd.with_backends(lane, FakeWorld())
        self.assertEqual(c.feasible_many([(HERO, 1), (FOOT, 2), (HERO, 3)]), [None, None, None])   # 假回执没有 verdict
        self.assertEqual(c.tech_many(0, ["Rhme", "Rhar"]), {"Rhme": 7, "Rhar": 7})
        self.assertEqual(lane.batches, 2)

    def test_snapshot_age_uses_real_clock(self):
        import ctypes
        w = FakeWorld()
        w.state.real_ms = (ctypes.windll.kernel32.GetTickCount() - 40) & 0xFFFFFFFF
        age = w3cmd.Cmd.with_backends(FakeLane(), w).snapshot_age_ms()
        self.assertTrue(35 <= age <= 1000, age)
        w.state.real_ms = 0
        self.assertIsNone(w3cmd.Cmd.with_backends(FakeLane(), w).snapshot_age_ms())


class Reads(unittest.TestCase):
    def setUp(self):
        self.lane, self.world = FakeLane(values=(3, 0, 0, 0, 0, 0, 0, 0)), FakeWorld()
        self.c = w3cmd.Cmd.with_backends(self.lane, self.world)

    def test_order_and_task(self):
        o = self.c.current_order(ENEMY)
        self.assertEqual((o["order"], o["target"], o["unit"]), (0x000D0012, (3, 4), ENEMY))
        self.assertIsNone(self.c.current_order(FOOT))
        t = self.c.current_task(FOOT)
        self.assertEqual((t["cls"], t["target"]), (w3cmd.TASK_TARGET, (9, 9)))
        t = self.c.current_task(HERO)
        self.assertEqual((t["cls"], t["x"], t["y"]), (w3cmd.TASK_POINT, 7.0, 8.0))
        self.assertIsNone(self.c.current_task(ENEMY))
        self.assertTrue(self.c.has_task(FOOT))

    def test_hero_fields(self):
        self.assertEqual((self.c.hero_level(HERO), self.c.hero_xp(HERO)), (3, 560))
        self.assertIsNone(self.c.hero_level(FOOT))
        self.assertEqual((self.c.mana(HERO), self.c.unit_state(HERO, 1)), (50.0, 500.0))
        self.assertEqual(self.c.inventory(HERO), ["stwp", None, None, None, None, None])
        self.assertIsNone(self.c.inventory(FOOT))

    def test_abilities_and_cooldown(self):
        ab = self.c.find_ability(HERO, "AHbz")
        self.assertEqual((ab["level"], ab["flags"] & 0x200), (2, 0x200))
        self.assertEqual(self.c.cooldown_left(ab["addr"]), 4.5)
        self.assertEqual(self.c.find_ability(HERO, "AHwe"), {"level": 0})
        self.assertIsNone(self.c.find_ability(FOOT, "AHwe"))               # 没细节 = 不知道
        self.assertEqual(self.c.ability_level(HERO, "AHbz"), 2)
        codes = [a["code"] for a in self.c.abilities(HERO)]
        self.assertEqual(codes, ["AHbz", "AInv", "BHab"])

    def test_player_ref_is_truthy_and_tech(self):
        p = self.c.player_of(HERO)
        self.assertEqual(int(p), 0)
        self.assertTrue(p)                                                    # 0 号玩家也是"读到了"
        self.assertEqual((p or 99), 0)
        self.assertEqual(self.c.tech_count(p, "Rhme"), 3)
        c = self.lane.sent[-1]
        self.assertEqual((c.op, c.args[3], c.args[9]), (F.OP["q_tech"], w3cmd.rawcode("Rhme"), 0))

    def test_items_on_ground_skips_dead(self):
        self.assertEqual(self.c.items_on_ground(), [(ITEM, "pman", 1.0, 2.0)])
        self.assertEqual(self.c.items_dead, 1)
        self.assertFalse(self.c.item_owned(ITEM))
        self.assertIsNone(self.c.item_owned(0x14100777))

    def test_captain(self):
        vals = tuple(fbits(v) for v in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)) + (0, 0)
        self.lane.values = vals
        cap = self.c.captain(self.c.current_order(ENEMY))
        self.assertEqual((cap["goal_x"], cap["home_y"], cap["handle"]), (3.0, 6.0, (3, 4)))
        self.assertEqual(self.lane.sent[-1].op, F.OP["q_captain"])

    def test_context_compat(self):
        self.assertEqual(self.c.context(FOOT), 0)
        self.assertIsNone(self.c.context(0x15555555))


if __name__ == "__main__":
    unittest.main()

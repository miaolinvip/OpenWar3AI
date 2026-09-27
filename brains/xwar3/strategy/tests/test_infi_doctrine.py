# -*- coding: utf-8 -*-
"""INFI Echo Isles 复盘落地：分矿塔、民兵清矿、英雄先手拉怪。"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import act as A
from brain import powerbuild as PB
from brain import tactics as T
from brain.amai_strategy.engine import WorldEnv
from brain.build import Want
from brain.runtime import Brain
from brain.tables import human
from brain.world import build_world
from test_amai_engine import make_brain, world_of
from test_runtime_smoke import FakeCmd, U


def expansion_world(extra=(), t=180.0):
    main = U(0x100, "hkee", 0, 0)
    natural = U(0x101, "htow", 4800, 0)
    mines = [U(0x200, "ngol", 0, 700, owner=15), U(0x201, "ngol", 4800, -700, owner=15)]
    peasants = [U(0x300+i, "hpea", 100+i*30, 100) for i in range(8)]
    return build_world([main, natural] + mines + peasants + list(extra), 0, t, 1000, 800, 20, 40)


class ExpansionArchitecture(unittest.TestCase):
    def test_base_tower_does_not_satisfy_natural_tower(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d); b.set_race("human")
            base_tower = U(0x400, "hgtw", 300, 0)
            w = expansion_world([base_tower])
            want = next(x for x in human.wants(w, b.profile) if x.code == "hwtw" and x.where.startswith("town:"))
            self.assertEqual(b.have(w, want), 0)
            natural_tower = U(0x401, "hatw", 4450, -250)
            self.assertEqual(b.have(expansion_world([base_tower, natural_tower]), want), 1)

    def test_first_natural_tower_is_biased_toward_gold_side(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d); b.set_race("human")
            w = expansion_world()
            natural = next(h for h in w.my_halls if h.addr == 0x101)
            mine = min(w.mines, key=lambda m: math.hypot(m.x-natural.x, m.y-natural.y))
            spots = b._building_spots(w, "hwtw", f"town:{natural.addr}", exits=False)
            self.assertTrue(spots)
            sx, sy = spots[0]
            # 大厅->塔 与 大厅->金矿同半平面，不会盖到分矿后方。
            self.assertGreater((sx-natural.x)*(mine.x-natural.x) + (sy-natural.y)*(mine.y-natural.y), 0)

    def test_amai_queue_also_pairs_the_natural_with_a_tower(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d, engine="amai"); b.set_race("human")
            w = expansion_world()
            st = b._ensure_strategy()
            st.paired_expansion_tower(WorldEnv(b, w, st))
            towers = [e for e in st.queue.entries if e.code == "hwtw"]
            self.assertTrue(any(e.town == 0x101 and e.prio == 410 for e in towers))

    def test_amai_queue_researches_human_backpack_at_tier_two(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d, engine="amai"); b.set_race("human")
            w = expansion_world([U(0x450, "hvlt", 300, 0)])
            st = b._ensure_strategy()
            st.human_backpack_builder(WorldEnv(b, w, st))
            self.assertTrue(any(e.kind == "upgrade" and e.code == "Rhpm" for e in st.queue.entries))


def guard_and_army():
    """分矿守卫（3 只食人魔）+ 已经站在守卫边上的主力（09-21 起民兵只在主力在场时号召，见 runtime.MILITIA_ARMY_NEAR）。"""
    creeps = [U(0x700 + i, "nogr", 4800, 60 * i, owner=12) for i in range(3)]
    army = [U(0x400, "Hamg", 4300, 0)] + [U(0x410 + i, "hfoo", 4350, 60 * i) for i in range(4)]
    return creeps + army


class MilitiaExpansion(unittest.TestCase):
    def test_no_militia_before_the_army_is_at_the_guard(self):
        """2-038：32 秒还没有一个兵就号召 5 个民兵打强度 11 的守卫，45 秒打不完，两个农民在矿外干等 180 秒。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            b.attack.clear_target = dict(ids={0x700, 0x701, 0x702}, x=4800, y=0, strength=11)
            creeps = [U(0x700 + i, "nogr", 4800, 60 * i, owner=12) for i in range(3)]
            b._militia_creep_step(expansion_world(creeps, t=32), False)
            self.assertFalse([o for _u, o in cmd.trains if o == A.ORDER_MILITIA])
            self.assertEqual(b.counts.get("民兵清分矿·主力不在守卫边上"), 1)

    def test_a_dead_guard_creep_does_not_end_the_mission(self):
        """2-038 324 秒号召、336 秒就「民兵到点、营地还没清完」：守卫死了一只，名单变了，被当成换了营地。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            b.attack.clear_target = dict(ids={0x700, 0x701, 0x702}, x=4800, y=0, strength=11)
            b._militia_creep_step(expansion_world(guard_and_army(), t=100), False)
            called = set(b.militia_creep_ids)
            self.assertEqual(len(called), 5)
            # 民兵在守卫 900 外（正往回拉 / 刚赶到）：旧代码会把它们当成「任务完成、安全」直接解散
            mil = [U(a, "hmil", 3700 + i * 20, 0) for i, a in enumerate(sorted(called))]
            b.attack.clear_target = dict(ids={0x701, 0x702}, x=4800, y=30, strength=7)   # 0x700 被打死了
            left = [c for c in guard_and_army() if c.addr != 0x700]
            b._militia_creep_step(expansion_world(mil + left, t=112), False)
            self.assertEqual(set(b.militia_creep_ids), called, "还是同一个营地：民兵继续打")
            self.assertFalse([o for _u, o in cmd.trains if o == A.ORDER_MILITIA_OFF])

    def test_calls_five_workers_and_releases_them_after_camp(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            # Echo 分矿口等级和 11：INFI 用 5 民兵。
            b.attack.clear_target = dict(ids={0x700, 0x701, 0x702}, x=4800, y=0, strength=11)
            w = expansion_world(guard_and_army(), t=100)
            b.lumber_crew = {0x300+i: 0x9000+i for i in range(5)}
            b._militia_creep_step(w, False)
            calls = [(u, o) for u, o in cmd.trains if o == A.ORDER_MILITIA]
            self.assertEqual(len(calls), 5)
            self.assertEqual(set(b.militia_creep_ids), {u for u, _o in calls})
            self.assertFalse(set(b.militia_creep_ids) & set(b.lumber_crew))

            militia = [U(a, "hmil", 4800+i*20, 0) for i, a in enumerate(sorted(b.militia_creep_ids))]
            done = expansion_world(militia, t=130)
            b.attack.clear_target = None
            b._militia_creep_step(done, False)
            # ★ 09-21 1-286：留下的不下「回到工作」（那条令让民兵跑回最近的大厅 = 老家）；
            # ★ 09-22 1-287：全留下（MILITIA_STAY）—— 清完二矿上的野后农民又跑回去了
            self.assertEqual(sum(o == A.ORDER_MILITIA_OFF for _u, o in cmd.trains), 0)
            self.assertFalse(b.militia_creep_ids)
            self.assertEqual(b.militia_expand_builders, {u.addr for u in militia})

            # 224 局清矿后 85 游戏秒才攒够大厅钱；原 60 秒会把两人提前送回经济。
            former = [U(u.addr, "hpea", u.x, u.y) for u in militia]
            waiting = expansion_world(former, t=215)
            self.assertFalse(b.militia_expand_builders & {u.addr for u in b._idle_workers(waiting)})
            self.assertEqual(b.militia_expand_until, 310)

    def test_militia_that_timed_out_keep_two_builders_instead_of_all_running_home(self):
        """★★ 09-21 1-262：353 秒号召 5 个，361~401 秒一直是 hmil=5（**变身成功**），
        营地还没打完，民兵 45 秒到点自己变回农民；403 秒代码看到「一个民兵都没有」判成
        「号召令未观察到变身」，营地标失败、5 个人全跑回主矿。
        一队农民变民兵帮忙打完就直接回去了；应当留两个在分矿造基地，其余回去继续采集。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            b.attack.clear_target = dict(ids={0x700, 0x701, 0x702}, x=4800, y=0, strength=11)
            w = expansion_world(guard_and_army(), t=100)
            b._militia_creep_step(w, False)
            called = set(b.militia_creep_ids)
            self.assertEqual(len(called), 5)

            # 变身成功：看到过民兵
            mil = [U(a, "hmil", 4600+i*20, 0) for i, a in enumerate(sorted(called))]
            b._militia_creep_step(expansion_world(mil, t=110), False)
            self.assertTrue(b.militia_seen_hmil)

            # 50 秒后：到点自己变回农民，营地（野怪还在）没清完，任务还挂着
            creeps = [U(0x700 + i, "nogr", 4800, 60*i, owner=12) for i in range(3)]
            back = [U(a, "hpea", 4600+i*20, 0) for i, a in enumerate(sorted(called))]
            b._militia_creep_step(expansion_world(back + creeps, t=150), False)

            self.assertIsNone(b.counts.get("民兵清分矿·变身无回执"), "变身成功过，不能判成「号召令未观察到变身」")
            self.assertEqual(b.counts.get("民兵清分矿·到点没清完·本营地不再号召"), 1)
            self.assertFalse(b.militia_creep_ids, "任务结束：全部从民兵名单里拿掉")
            # ★ 09-22 1-287：全留下（MILITIA_STAY）一起盖分矿（清完二矿上的野后农民又跑回去了）
            self.assertEqual(b.militia_expand_builders, called, "全留在分矿盖大厅")
            self.assertEqual(b.counts.get("民兵清分矿·到点自己变回"), 5)
            idle = {u.addr for u in b._idle_workers(expansion_world(back, t=160))}
            self.assertFalse(called & idle, "留下的不被当成闲人派回老家")
            # 营地没清完：建造工先退到分矿外的安全点，不站在野怪脸上
            self.assertTrue(cmd.moves, "营地没清完时要把建造工挪到安全点")

            # ⚠ 3-028 实机抓到的回归：修掉误判之后刹车也没了，80 秒到点、84 秒又号召 5 个、132 秒再 5 个。
            #   45 秒没打下来，马上再叫一批也是一样的结果 —— 同一个营地不再号召。
            calls = sum(o == A.ORDER_MILITIA for _u, o in cmd.trains)
            b._militia_creep_step(expansion_world(creeps, t=154), False)
            self.assertEqual(sum(o == A.ORDER_MILITIA for _u, o in cmd.trains), calls, "同一个营地不许再号召")
            self.assertFalse(b.militia_creep_ids)

    def test_1286_reserved_militia_wait_at_the_mine_instead_of_running_home(self):
        """1-286 5 分钟：清完野，过来帮忙的民兵又回了老家，没有留在二矿位置等着建基地。
        「回到工作」= 让民兵跑到最近的城镇大厅变回农民（HumanAbilityStrings [Amil] Unubertip），分矿还没大厅就是跑回老家。
        实机：23:55:37 五个一起解除，预留那个 13 秒后才被叫回来盖（地基晚约 28 游戏秒）；
        另一个超了 15 秒回执被移出预留，在老家盖了农场。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            b.attack.clear_target = dict(ids={0x700, 0x701, 0x702}, x=4800, y=-700, strength=11)
            b._militia_creep_step(expansion_world(guard_and_army(), t=100), False)
            called = set(b.militia_creep_ids)
            militia = [U(a, "hmil", 4800 + i * 20, -600) for i, a in enumerate(sorted(called))]
            b.attack.clear_target = None
            b._militia_creep_step(expansion_world(militia, t=130), False)      # 守军清完
            kept = set(b.militia_expand_builders)
            self.assertEqual(kept, called, "09-22 起全留下")
            off = {u for u, o in cmd.trains if o == A.ORDER_MILITIA_OFF}
            self.assertEqual(off, set(), "一个都不下「回到工作」")
            self.assertEqual(b.counts.get("民兵清分矿·预留者原地等到点变回"), 5)

            want = Want("expand", "htow", 2, 93, "mine")
            one_hall = lambda extra, t: build_world(                          # noqa: E731
                [U(0x100, "hkee", 0, 0), U(0x200, "ngol", 0, 700, owner=15), U(0x201, "ngol", 4800, -700, owner=15)]
                + [U(0x310 + i, "hpea", 100 + i * 30, 100) for i in range(6)] + list(extra), 0, t, 1000, 800, 20, 40)
            still = [u for u in militia if u.addr in kept]
            # 清完 20 秒：两个还是民兵（原来 15 秒就判回执超时、把人移出预留）-> 继续等，名单不动
            self.assertEqual(b.check(one_hall(still, 150), want), "等待预留民兵到点原地变回农民后双敲")
            self.assertEqual(b.militia_expand_builders, kept)
            # 一个到点原地变回农民：马上让它下地基；其余还是民兵，留在预留里等着协建
            first, second = sorted(kept)[:2]
            now = [U(first, "hpea", 4800, -600)] + [U(a, "hmil", 4820, -600) for a in sorted(kept)[1:]]
            w = one_hall(now, 158)
            self.assertIsNone(b.check(w, want))
            self.assertEqual(b.militia_expand_builders, kept)
            pick = min(b._builder(w, expansion=True), key=lambda u: (u.addr not in b.militia_expand_builders,
                                                                     math.hypot(u.x - 4800, u.y + 700)))
            self.assertEqual(pick.addr, first)
            # 超过 MILITIA_REVERT_WAIT_S 还是民兵（异常）：放弃预留，别让开矿一直等
            b.check(one_hall([U(second, "hmil", 4820, -600)], 181), want)
            self.assertNotIn(second, b.militia_expand_builders)

    def test_1287_former_militia_power_build_the_natural_then_mine_there(self):
        """1-287 4:40：清完二矿上的野，农民又跑回去了。留下的原民兵：1 个主建，其余协建（Ahrp DataC 0.15：
        每个助手多花大厅造价 15% ≈ 58 金 31 木，速度多 60%）；钱够几个上几个；大厅盖好放回经济就近采矿。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            main = U(0x100, "hkee", 0, 0)
            mines = [U(0x200, "ngol", 0, 700, owner=15), U(0x201, "ngol", 4800, -700, owner=15)]
            crew = [U(0x300 + i, "hpea", 4700 + 40 * i, -500) for i in range(5)]
            crew[0].order = PB.HUMAN_BUILD                      # 主建的正在盖
            hall = U(0x800, "htow", 4800, -300)
            hall.hp = 0.3 * hall.hp_max                         # 地基在盖
            b.militia_expand_xy = (4800, -700)
            b.militia_expand_builders = {u.addr for u in crew[1:]}   # crew[0] 是主建（已转成 pending 的建造工）
            b.militia_expand_until = 999
            b.pending = [dict(kind="expand", code="htow", t=100, worker=crew[0].addr, had=1, x=4800, y=-300)]
            pg, pl = (0.15 * v for v in (385, 205))
            # 钱只够 2 个助手（已承诺的建造费里有这座大厅的 385/205 —— pending 还在）
            gold, wood = 385 + 2 * pg + 10, 205 + 2 * pl + 10
            w = build_world([main, hall] + mines + crew, 0, 130, int(gold), int(wood), 20, 40)
            b._militia_assist(w)
            helpers = [a for a, t, o in cmd.gathers if t == hall.addr and o == A.ORDER_REPAIR]
            self.assertEqual(len(helpers), 2, cmd.gathers)
            self.assertNotIn(crew[0].addr, helpers, "主建的不当助手")
            self.assertEqual(b.counts.get("民兵清分矿·协建·钱不够先等"), 1)
            self.assertFalse(b.militia_expand_builders & {u.addr for u in b._idle_workers(w)}, "等着的也不被派回老家")
            # powerbuild 不往这座工地另派远处的助手
            b.powerbuild.sites[hall.addr] = dict(identity=PB.identity(hall), worker=crew[0].addr, since=120,
                                                 worker_identity=PB.identity(crew[0]),
                                                 hp=hall.hp, progress=129, advanced=True, code="htow")
            far = U(0x390, "hpea", 3600, -300)                 # 1200 外一个闲农民：原来会被 powerbuild 抓来当助手
            b.powerbuild.step(b, build_world([main, hall, far] + mines + crew, 0, 131, 2000, 2000, 20, 40))
            self.assertIsNone(b.powerbuild.helper)
            # 大厅完工：全部放回经济
            hall.hp = hall.hp_max
            b.pending = []
            b._militia_assist(build_world([main, hall] + mines + crew, 0, 200, 2000, 2000, 20, 40))
            self.assertFalse(b.militia_expand_builders)
            self.assertEqual(b.counts.get("民兵清分矿·大厅完工·放回就地采矿"), 4)

    def test_workers_hidden_inside_hall_stay_in_conversion_mission(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = FakeCmd(); b = Brain(cmd, None, d); b.set_race("human")
            b.attack.clear_target = dict(ids={0x700}, x=4800, y=-700, strength=11)
            start = expansion_world(t=100)
            b._militia_creep_step(start, False)
            selected = set(b.militia_creep_ids)
            calls = len([o for _u, o in cmd.trains if o == A.ORDER_MILITIA])

            # Call to Arms 会让选中的农民短暂进入大厅，从快照消失；不能当成死亡/拒绝。
            hidden_units = [u for u in start.units if u.addr not in selected]
            hidden = build_world(hidden_units, 0, 108, 1000, 800, 20, 40)
            b._militia_creep_step(hidden, False)
            self.assertEqual(b.militia_creep_ids, selected)
            self.assertEqual(len([o for _u, o in cmd.trains if o == A.ORDER_MILITIA]), calls)

            returned = expansion_world([U(a, "hmil", 100, 100) for a in selected], t=114)
            b._militia_creep_step(returned, False)
            self.assertEqual(b.militia_creep_ids, selected)
            self.assertIsNone(b.militia_failed_camp_key)

    def test_expansion_waits_for_matching_militia_to_demobilize(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d); b.set_race("human")
            one_hall = build_world([U(0x100, "hkee", 0, 0), U(0x200, "ngol", 0, 700, owner=15),
                                    U(0x201, "ngol", 4800, -700, owner=15)] +
                                   [U(0x300+i, "hpea", 100+i*30, 100) for i in range(8)],
                                   0, 400, 1000, 800, 20, 40)
            b.militia_creep_ids = {0x300, 0x301}
            b.militia_expand_xy = (4800, -700)
            why = b.check(one_hall, Want("expand", "htow", 2, 93, "mine"))
            self.assertEqual(why, "等待分矿民兵解除后双敲")

    def test_expansion_waits_for_reserved_militia_to_reappear_as_peasants(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d); b.set_race("human")
            main = U(0x100, "hkee", 0, 0)
            mines = [U(0x200, "ngol", 0, 700, owner=15), U(0x201, "ngol", 4800, -700, owner=15)]
            ordinary = [U(0x310+i, "hpea", 100+i*30, 100) for i in range(6)]
            hidden = build_world([main] + mines + ordinary, 0, 132, 1000, 800, 20, 40)
            b.militia_expand_builders = {0x300, 0x301}
            b.militia_expand_xy = (4800, -700)
            b.militia_expand_until = 310
            b.militia_demobilize_at = 130
            want = Want("expand", "htow", 2, 93, "mine")
            self.assertEqual(b.check(hidden, want), "等待原民兵变回农民后双敲")

            returned = [U(0x300+i, "hpea", 4800+i*20, -700) for i in range(2)]
            ready = build_world([main] + mines + ordinary + returned, 0, 134, 1000, 800, 20, 40)
            self.assertIsNone(b.check(ready, want))
            self.assertEqual(min(b._builder(ready, expansion=True),
                                 key=lambda u: (u.addr not in b.militia_expand_builders,
                                                math.hypot(u.x-4800, u.y+700))).addr, 0x300)

    def test_table_followthrough_moves_expansion_ahead_of_units(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d); b.set_race("human")
            b.militia_expand_builders = {0x300, 0x301}
            one_hall = build_world([U(0x100, "hkee", 0, 0), U(0x200, "ngol", 0, 700, owner=15),
                                    U(0x201, "ngol", 4800, -700, owner=15)] +
                                   [U(0x300+i, "hpea", 100+i*30, 100) for i in range(8)],
                                   0, 400, 200, 100, 20, 40)
            wants = b._militia_expansion_wants(one_hall, human.wants(one_hall, b.profile))
            expand = [x for x in wants if x.kind == "expand" and x.qty == 2]
            self.assertEqual(len(expand), 1)
            self.assertEqual(expand[0].prio, 93)

    def test_amai_followthrough_inserts_urgent_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_brain(d, engine="amai"); b.set_race("human")
            b.militia_expand_builders = {0x300, 0x301}
            one_hall = build_world([U(0x100, "hkee", 0, 0), U(0x200, "ngol", 0, 700, owner=15),
                                    U(0x201, "ngol", 4800, -700, owner=15)] +
                                   [U(0x300+i, "hpea", 100+i*30, 100) for i in range(8)],
                                   0, 400, 1000, 800, 20, 40)
            st = b._ensure_strategy()
            st.militia_expansion_followthrough(WorldEnv(b, one_hall, st))
            self.assertTrue(any(e.kind == "expand" and e.prio >= 520 and
                                e.note == "MilitiaExpansionFollowthrough" for e in st.queue.entries))


class HeroPullCreep(unittest.TestCase):
    def scene(self, t=600.0):
        hero = U(2, "Hamg", 3500, 0); hero.hp = hero.hp_max = 650
        front = [U(10+i, "hfoo", 3500, i*60) for i in range(4)]
        creep = U(700, "nwlg", 4000, 0, owner=12)
        w = build_world([U(1, "htow", 0, 0), hero] + front + [creep], 0, t, 500, 300, 20, 40)
        seq = T.AttackSequence(dict(human.PROFILE), state="creep", camp_ids={700}, creep_since=t)
        seq.camp = dict(x=4000.0, y=0.0, strength=4.0, units=[creep])
        seq.hero_ids = {hero.addr}
        return seq, w, hero, front, creep

    def test_full_formation_hero_tags_then_pulls_behind_tanks(self):
        seq, w, hero, front, creep = self.scene()
        out = seq._creep_engage_core(w, [hero]+front, None, [creep], 4)
        hits = [i for i in out if i.kind == "attack"]
        self.assertEqual([[u.addr for u in i.units] for i in hits], [[hero.addr]])
        self.assertEqual(seq.counts.get("打野·英雄先手拉怪"), 1)

        w.t += 2
        out = seq._creep_engage_core(w, [hero]+front, None, [creep], 4)
        self.assertTrue(any(i.kind == "move" and hero in i.units and "回拉" in i.why for i in out))
        self.assertTrue(any(i.kind == "attack" and all(u in front for u in i.units) for i in out))

    def test_hero_never_opens_before_three_tanks_and_team_arrive(self):
        seq, w, hero, front, creep = self.scene()
        out = seq._creep_engage_core(w, [hero]+front[:2], None, [creep], 4)
        self.assertFalse(any(i.kind == "attack" and i.units == [hero] for i in out))
        self.assertIsNone(seq.creep_pull_id)

    def test_summon_is_the_only_first_tank_and_permanent_melee_waits(self):
        seq, w, hero, front, creep = self.scene()
        water = U(30, "hwat", 3500, -80); water.hp = water.hp_max = 525
        w = build_world([U(1, "htow", 0, 0), hero, water] + front + [creep], 0, 600, 500, 300, 20, 40)
        army = [hero, water] + front

        # 第一拍仍由英雄按 INFI 方式点一下拉怪。
        seq._creep_engage_core(w, army, None, [creep], 4)
        w.t += 2
        out = seq._creep_engage_core(w, army, None, [creep], 4)
        attackers = [u for i in out if i.kind == "attack" for u in i.units]
        self.assertEqual({u.addr for u in attackers}, {water.addr})
        self.assertFalse(any(u in front for u in attackers))
        self.assertTrue(all(any(i.kind == "move" and u in i.units for i in out) for u in front))
        self.assertEqual(seq.counts.get("打野·召唤物优先承伤"), 1)

    def test_permanent_army_joins_only_after_summon_has_settled_contact(self):
        seq, _w, hero, front, creep = self.scene(t=700)
        water = U(30, "hwat", 3780, 0); water.hp = water.hp_max = 525  # 已经贴到 4000 的野怪
        army = [hero, water] + front
        w = build_world([U(1, "htow", 0, 0)] + army + [creep], 0, 700, 500, 300, 20, 40)
        seq.creep_engaged = True

        settling = seq._creep_engage_core(w, army, None, [creep], 4)
        settling_attackers = [u for i in settling if i.kind == "attack" for u in i.units]
        self.assertIn(water, settling_attackers)
        self.assertIn(hero, settling_attackers)  # 远程安全输出
        self.assertFalse(any(u in front for u in settling_attackers))

        w.t += T.CREEP_SUMMON_SETTLE_S + .1
        joined = seq._creep_engage_core(w, army, None, [creep], 4)
        joined_attackers = [u for i in joined if i.kind == "attack" for u in i.units]
        self.assertTrue(all(u in joined_attackers for u in front))


if __name__ == "__main__":
    unittest.main()

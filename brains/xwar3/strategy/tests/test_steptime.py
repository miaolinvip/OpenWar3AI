# -*- coding: utf-8 -*-
"""brain/steptime.py：一轮慢在哪（09-19，一轮 5~9 秒墙钟）。

守着几条底线：
  * 各段只记自己那部分，加起来 = 这一轮（嵌套段从外层扣掉；lap 换段；section 退出时关掉里面没关的 lap，任务抛异常也不串层）
  * 通道包一层之后返回值 / 异常原样；锁、游戏处理分开记到当时的段和调用方；_submit 返回 False 记失败
  * 每分钟一行摘要（心跳）+ steptime.jsonl（≥2 秒的一轮各一条、每分钟一条），摘要之后清零
  * 真大脑跑几拍：各任务、build/tactics 的细分段都出现，act.build 的 sleep 记成「等待」
期望值都是按假时钟**算**出来的，不是照代码现状写的。
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import steptime as S  # noqa: E402
from brain.jobs import Scheduler  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Busy(RuntimeError):
    pass


class FakeHeld:
    def __init__(self, ch):
        self.ch = ch

    def __enter__(self):
        self.ch.clock.now += self.ch.lock_s
        if self.ch.busy:
            raise Busy("通道忙")
        return self

    def __exit__(self, *a):
        return False


class FakeCh:
    """w3chan.Channel 的最小形状：call = 持锁 + _submit。"""

    def __init__(self, clock, lock_s=0.5, game_s=0.2):
        self.clock, self.lock_s, self.game_s = clock, lock_s, game_s
        self.ok = True
        self.busy = False

    def _hold(self):
        return FakeHeld(self)

    def _submit(self, action, command, timeout=5.0):
        self.clock.now += self.game_s
        return self.ok

    def call(self):
        with self._hold():
            return self._submit(40, 0, 5.0)


class FakeClaims:
    def __init__(self, clock):
        self.clock = clock

    class _L:
        def __init__(self, c): self.c = c
        def __enter__(self): self.c.now += 0.1
        def __exit__(self, *a): return False

    def _lock(self):
        return FakeClaims._L(self.clock)

    def take(self):
        with self._lock():
            return True


class TestSections(unittest.TestCase):
    def test_exclusive_times_add_up(self):
        c = Clock()
        tm = S.StepTimer(clock=c)
        tm.begin()                                    # 0
        c.now = 1; tm.lap("读快照")
        c.now = 2; tm.lap("大脑")
        c.now = 3
        with tm.section("任务·build"):
            c.now = 4; tm.lap("build·规划")
            c.now = 6; tm.lap("build·下令")
            c.now = 9
        c.now = 10; tm.lap("记录")
        c.now = 12
        loop = tm.end(t=30.0)
        sec = {k: v["s"] for k, v in loop["sec"].items()}
        # 大脑 2->10 = 8，扣掉 任务·build 3->9 = 6 -> 2；任务·build 6 扣掉 规划 2 + 下令 3 -> 1
        self.assertEqual(sec, {S.ROOT_NAME: 1, "读快照": 1, "大脑": 2, "任务·build": 1, "build·规划": 2, "build·下令": 3, "记录": 2})
        self.assertEqual(loop["wall"], 12)
        self.assertAlmostEqual(sum(sec.values()), loop["wall"])
        self.assertEqual(tm.heaviest(loop), "build·下令")

    def test_section_closes_laps_when_job_raises(self):
        c = Clock()
        tm = S.StepTimer(clock=c)

        def boom(w, b):
            tm.lap("x·第一步")
            c.now += 2
            raise ValueError("任务里抛了")

        s = Scheduler()
        s.add("x", 1.0, boom)
        brain = SimpleNamespace(timer=tm)
        tm.begin()
        tm.lap("大脑")
        c.now = 1
        s.run_due(SimpleNamespace(t=0.0), brain)
        self.assertEqual(s.jobs["x"].errors, 1)
        self.assertEqual(len(tm._stack), 2)           # 根 + 「大脑」lap：任务那层和它里面的 lap 都关掉了
        c.now = 4
        tm.lap("记录")                                 # 关的是「大脑」，不是别的
        c.now = 5
        loop = tm.end()
        sec = {k: v["s"] for k, v in loop["sec"].items()}
        self.assertEqual(sec, {S.ROOT_NAME: 0, "大脑": 2, "任务·x": 0, "x·第一步": 2, "记录": 1})
        self.assertEqual(s.stats()["x"]["errors"], 1)
        self.assertIn("max", s.stats()["x"])

    def test_no_loop_is_harmless(self):
        tm = S.StepTimer(clock=Clock())
        with tm.section("a"):
            tm.lap("b")
        tm.chan("game", 0.1)
        self.assertIsNone(tm.end())
        self.assertEqual(tm.minute["chan"]["calls"], 1)


class TestChannel(unittest.TestCase):
    def test_instrument_keeps_behavior_and_attributes(self):
        c = Clock()
        tm = S.StepTimer(clock=c)
        ch = FakeCh(c)
        S.instrument(tm, ch=ch)
        S.instrument(tm, ch=ch)                       # 包两次 = 包一次
        tm.begin()
        with tm.section("任务·build"):
            self.assertIs(ch.call(), True)
            ch.ok = False
            self.assertIs(ch.call(), False)           # 5 秒超时那种：返回值原样，记一次失败
            ch.busy = True
            with self.assertRaises(Busy):             # 抢不到锁的异常原样往外抛
                ch.call()
        loop = tm.end()
        st = loop["sec"]["任务·build"]
        self.assertEqual(st["calls"], 2)
        self.assertAlmostEqual(st["lock"], 1.5)       # 三次各等 0.5（第三次抢不到）
        self.assertAlmostEqual(st["game"], 0.4)
        self.assertEqual(st["fail"], 2)               # 一次超时 + 一次抢不到锁
        self.assertAlmostEqual(st["s"], 1.9)
        (who, v), = [(k[1], v) for k, v in loop["who"].items()]
        self.assertEqual(who, "FakeCh.call")
        self.assertEqual(v["n"], 2)
        self.assertAlmostEqual(v["s"], 1.9)

    def test_claims_lock_and_sleep(self):
        c = Clock()
        tm = S.StepTimer(clock=c)
        cl = FakeClaims(c)
        act = SimpleNamespace(sleep_fn=None)
        S.instrument(tm, claims=cl, act=act)
        self.assertEqual(act.sleep_fn, tm.sleep)
        tm.begin()
        with tm.section("任务·gold"):
            self.assertTrue(cl.take())
            act.sleep_fn(0)
        loop = tm.end()
        st = loop["sec"]["任务·gold"]
        self.assertAlmostEqual(st["claim"], 0.1)
        self.assertEqual(st["calls"], 0)


class TestSummary(unittest.TestCase):
    def test_minute_line_jsonl_and_reset(self):
        c = Clock()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "steptime.jsonl")
            tm = S.StepTimer(clock=c, path=path, label="match-x")
            ch = FakeCh(c, lock_s=0.0, game_s=3.0)
            S.instrument(tm, ch=ch)
            # 一轮快的 0.5 秒
            tm.begin(); tm.lap("读快照"); c.now += 0.5; tm.end(t=10.0)
            # 一轮慢的：任务·build 里一次通道请求游戏处理了 3 秒，再加 3 秒 Python
            tm.begin(); tm.lap("大脑")
            with tm.section("任务·build"):
                ch.call()
                c.now += 3.0
            tm.end(t=20.0)
            self.assertEqual(tm.take_tick()[1], "任务·build")
            self.assertEqual(tm.take_tick(), (0.0, None))
            line = tm.minute_line()
            self.assertIn("循环 2 轮", line)
            self.assertIn("≥2秒 1 ≥5秒 1", line)
            self.assertIn("最慢一轮 6.00 秒@游戏20秒", line)
            self.assertIn("任务·build 6.00（通道 1 次 游戏 3.00）", line)
            self.assertIn("谁在调（通道/锁/等待）：FakeCh.call×1 3.00秒", line)
            self.assertNotIn("循环·其它 0.00", line)                # 0.00 秒的段不占位置
            self.assertIn("单次最慢 游戏 3.00秒 FakeCh.call（任务·build）", line)
            self.assertIn("循环 0 轮", tm.minute_line())      # 摘要之后清零
            with open(path, encoding="utf-8") as fh:
                recs = [json.loads(x) for x in fh]
            self.assertEqual([r["kind"] for r in recs], ["slow", "minute", "minute"])
            self.assertEqual(recs[0]["match"], "match-x")
            self.assertEqual(recs[0]["sec"]["任务·build"]["game"], 3.0)
            self.assertEqual(recs[1]["max"]["wall"], 6.0)
            self.assertEqual(recs[1]["who"], [["FakeCh.call", 1, 3.0]])                     # 这一分钟按调用方合计
            self.assertEqual(recs[1]["secChan"], {"任务·build": {"calls": 1, "game": 3.0}})  # 锁 0 秒不列
            les = tm.lesson()                           # 整局的不跟着分钟清零
            self.assertEqual((les["loops"], les["slow2"], les["slow5"]), (2, 1, 1))
            self.assertEqual(les["max"]["wall"], 6.0)
            self.assertEqual(les["calls"], {"任务·build": 1})
            self.assertEqual(les["who"], [["FakeCh.call", 1, 3.0]])
            json.dumps(les, ensure_ascii=False)


class TestDeadHeroTable(unittest.TestCase):
    """09-19 steptime 实测死亡英雄表占大脑墙钟 17%（每次 build 8 次请求）。现在按需读：驱动多拍，数它真读了几次。"""

    def test_read_only_when_someone_missing(self):
        from brain.runtime import Brain, DEAD_READ_EVERY
        from test_runtime_smoke import FakeCmd, U

        class Cmd(FakeCmd):
            def __init__(self):
                super().__init__()
                self.table = []                        # 死亡英雄表里还没开始复活的
                self.reads = 0

            def dead_heroes(self, player):             # W3P q_dead_heroes：一次读整张表
                self.reads += 1
                return list(self.table)

        cmd = Cmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            hero, mine = U(0x500, "Hamg", 0, 0), U(0x200, "ngol", 0, 0, owner=15)

            def at(t, heroes):
                b._refresh_dead(SimpleNamespace(t=t, my_heroes=heroes, mine=[mine]))

            at(0.0, [hero])                            # 第一次：兜底读（中途接管时没见过的英雄靠它）
            for t in (3.0, 6.0, 9.0, 27.0):
                at(t, [hero])                          # 都在场上、没到 30 秒：不读
            self.assertEqual(cmd.reads, 1)
            at(DEAD_READ_EVERY, [hero])                # 到 30 秒：兜底再读一次
            self.assertEqual(cmd.reads, 2)
            cmd.table = [0x500]                     # 英雄死了：快照里没它、表里有它
            at(33.0, [])
            self.assertEqual(cmd.reads, 3)
            self.assertEqual([x["ptr"] for x in b.dead_waiting], [0x500])
            self.assertEqual(b.dead_waiting[0]["code"], "Hamg")          # 认得出是谁（见过它活着）
            cmd.table = []                          # 复活令接下：槽清掉，英雄在祭坛里（快照仍然没它）
            at(36.0, [])
            at(39.0, [])
            self.assertEqual(cmd.reads, 5)          # 有人缺席：每拍都读
            at(42.0, [hero])                           # 复活回来了：回到兜底频率
            at(45.0, [hero])
            self.assertEqual(cmd.reads, 5)
            self.assertEqual(b.counts["死亡英雄表·英雄都在不读"], 6)


class TestPlayerCached(unittest.TestCase):
    def test_player_of_once_per_match(self):
        """09-19：我方玩家对象整局不变 —— 研究等级、死亡英雄表共用 Actuator 缓存的那一份，player_of 一局只调一次。"""
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd, U

        class Cmd(FakeCmd):
            n = 0

            def player_of(self, u):
                Cmd.n += 1
                return 0x5000

        cmd = Cmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            me = U(0x100, "htow", 0, 0)
            b.tech_level("Rhme", me, 0.0)
            b.tech_level("Rhar", me, 0.0)
            b.tech_level("Rhme", me, 20.0)                # 过了 10 秒缓存，重读等级
            self.assertEqual(b.act.dead_heroes(me.addr), [])
            self.assertEqual(Cmd.n, 1)
            self.assertEqual(b.act.player(me.addr), 0x5000)


class TestBrainIntegration(unittest.TestCase):
    def test_real_brain_steps(self):
        from brain.runtime import Brain
        from test_runtime_smoke import FakeCmd, U
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            tm = S.StepTimer(path=os.path.join(d, "steptime.jsonl"))
            b.timer = tm
            S.instrument(tm, act=b.act)
            b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=4000 + i * 64, y=-3500, hp=50.0) for i in range(10)]
            hall = U(0x100, "htow", 4352, -2688)
            mine = U(0x200, "ngol", 4352, -3200, owner=15)
            peas = [U(0x300 + i, "hpea", 4300 + i * 40, -2900, order=0x000D0032, has_task=True) for i in range(3)] + \
                   [U(0x310 + i, "hpea", 4400, -2500) for i in range(2)]
            enemy = [U(0x800, "hcas", -4352, 2944, owner=1)]
            seen = set()
            for t in range(2, 30, 2):
                snap = SimpleNamespace(units=[hall, mine] + peas + enemy, me=0, gold=500, lumber=150, food_used=5, food_cap=12)
                tm.begin()
                tm.lap("大脑")
                b.step(snap, float(t))
                loop = tm.end(float(t))
                self.assertAlmostEqual(sum(v["s"] for v in loop["sec"].values()), loop["wall"], places=6)
                seen |= set(loop["sec"])
            self.assertEqual({n: s for n, s in b.jobs.stats().items() if s["errors"]}, {})
            for name in ("感知·英雄等级", "世界模型", "任务·build", "build·规划", "build·下令", "build·落点",
                         "任务·tactics", "tactics·守家", "tactics·下令", "任务·gold"):
                self.assertIn(name, seen)
            self.assertTrue(cmd.builds)
            # 09-23 P3：W3P build 同一帧回执，act.build 不再 sleep 0.25 秒回头看 —— 下建造令这段不该有「等待」
            waits = tm.match["sec"]["build·下令"]["wait"]
            self.assertEqual(waits, 0.0)
            self.assertIn("循环", tm.minute_line())


if __name__ == "__main__":
    unittest.main()

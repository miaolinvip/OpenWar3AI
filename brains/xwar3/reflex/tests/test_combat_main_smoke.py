# -*- coding: utf-8 -*-
"""mod_combat.main() 整轮冒烟：假会话 / 假通道 / 假时钟 / 假租约表，跑 220 轮，**一轮都不许进「出错（继续跑）」**。

为什么要它（09-18）：规则函数都有单测，但主循环没有 —— 而主循环里施法那一段排在"英雄独自扛野怪 / 拉残血"前面，
那里抛一次异常，这一轮后面的保命全不跑（09-16 004301 就是半截文件 NameError，整局保命、施法全停，复盘只显示「施法 0」）。
顺带核对几条真的发出去了：剑圣残血疾风步、巫妖开霜冻护甲自动、恶魔猎手献祭开了又关、死亡骑士奶食尸鬼、心跳那一行的新字段。
"""
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

LOOPS = 220
STEP = 0.35          # 每轮假时钟走这么多墙钟秒：220 轮 = 77 秒，心跳（60 秒）会打一次


def unit(addr, typ, owner, x, y, hp, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp,
                hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


class _Stop(BaseException):
    """跑够轮数就停。不用 KeyboardInterrupt：主循环 except Exception 里的 time.sleep(1.0) 抛出来会漏出 unittest，把整个测试批次打断。
    BaseException 子类不会被 main 的 except Exception / except KeyboardInterrupt 吞掉。"""


class FakeTime:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = 0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps += 1
        self.t += STEP
        if self.sleeps >= LOOPS:
            raise _Stop


class FakeCmd:
    USESLOT1 = 0x000D0028

    def __init__(self, types):
        self.types = types
        self.calls = []
        self.ch = object()
        self.last_cast_path = "内核点"

    def _rec(self, *a):
        self.calls.append(a)
        return True

    def move_to(self, u, x, y, order=0):
        return self._rec("move", u)

    def target_order(self, u, t, order, x=0.0, y=0.0):
        return self._rec("target", u, order, t)

    def immediate_order(self, u, order):
        return self._rec("instant", u, order)

    def cast_point_ctx(self, u, order, x, y):
        return self._rec("point", u, order)

    def cast_target(self, u, order, t, x=0.0, y=0.0):
        return self._rec("cast_target", u, order, t)

    def use_item(self, *a, **k):
        return True

    def find_ability(self, u, code):
        return {"level": 1, "flags": 0, "addr": 0x5000, "code": code}

    def cooldown_left(self, a):
        return 0.0

    def mana(self, u):
        return 500.0

    def abilities(self, u):
        names = C.HERO_SPELLS.get(self.types.get(u, ""), ())
        return [{"code": C.SPELL_ABIL[n], "level": 1} for n in names]

    def current_order(self, u):
        return None

    def current_task(self, u):
        return None

    def inventory(self, u):
        return []


class FakeClaims:
    def __init__(self, *a, **k):
        self.yielded = 0

    def take(self, *a, **k):
        return True

    def give_back(self, a):
        pass

    def mine(self, a):
        return True


class FakeOdds:
    def __init__(self):
        self.start, self.checks, self.fails, self.error = None, 0, 0, None

    def reset(self):
        self.start = None

    def update(self, *a, **k):
        return None


def scenario(i):
    """第 i 轮的快照。0 号玩家是我们、1 号敌人、12 号野怪。"""
    mine = [
        unit(1, "Obla", 0, 0, 0, 200, 700),              # 残血剑圣，400 外有敌方步兵 -> 疾风步
        unit(2, "Ulic", 0, 3000, 0, 500),                # 野怪在 500 外 -> 读到已学技能 -> 开霜冻护甲自动
        unit(3, "Edem", 0, 6000, 0, 600),                # 贴身两个步兵 -> 开献祭；第 100 轮起人走了 -> 关
        unit(4, "Udea", 0, 9000, 0, 600),
        unit(40, "ugho", 0, 9200, 0, 100, 340),          # 残血食尸鬼 -> 死亡缠绕奶它
        unit(90, "unpl", 0, -2000, -2000, 1500),         # 大厅（家）
    ]
    far = 2500 if i >= 100 else 0
    foes = [
        unit(20, "hfoo", 1, 400, 0, 420),
        unit(21, "hfoo", 1, 6150 + far, 0, 420),
        unit(22, "hfoo", 1, 6200 + far, 50, 420),
        unit(30, "nogr", 12, 3500, 0, 400),
    ]
    if i < 10:
        foes.append(unit(23, "hfoo", 1, 9400, 300, 50, 420))   # 第 10 轮消失（血 12%）-> 记一具尸体
    return SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + foes, age_ms=5)


class MainLoopSmoke(unittest.TestCase):
    def test_loop_runs_and_casts(self):
        notes = []
        lease_events = []
        class ObservedClaims(FakeClaims):
            def take(self, addr, duration, **kwargs):
                lease_events.append(("take", addr, duration, kwargs.get("prio")))
                return True
            def give_back(self, addr):
                lease_events.append(("release", addr))
        clock = FakeTime()
        types = {u.addr: u.type for u in scenario(0).mine}
        cmd = FakeCmd(types)
        state = {"i": 0}

        def read():
            snap = scenario(state["i"])
            state["i"] += 1
            return snap

        ses = SimpleNamespace(pid=4242, cmd=cmd, read=read)
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", ObservedClaims), \
                mock.patch.object(C, "time", clock), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            C.main()

        errors = [n for n in notes if "出错" in n]
        self.assertEqual(errors, [], errors[:3])
        self.assertEqual(clock.sleeps, LOOPS)
        instant = {(c[1], c[2]) for c in cmd.calls if c[0] == "instant"}
        self.assertIn((1, 0x000D00A1), instant)          # 疾风步
        self.assertIn((2, 0x000D01EA), instant)          # 霜冻护甲开自动
        self.assertIn((3, 0x000D00D1), instant)          # 献祭开
        self.assertIn((3, 0x000D00D2), instant)          # 献祭关（第 100 轮后 900 内没人）
        self.assertIn(("cast_target", 4, 0x000D00FE, 40), cmd.calls)   # 死亡缠绕奶食尸鬼
        # 普通成功施法的保护必须留在跨模块租约表，不能只保护微操自己的补令。
        hold = ("take", 4, C.cast_hold_seconds("deathcoil", 1), C.PRIO["cast"])
        self.assertIn(hold, lease_events)
        for i, event in enumerate(lease_events[:-1]):
            if event == hold:
                self.assertNotEqual(lease_events[i + 1], ("release", 4))
        autocasts = [c for c in cmd.calls if c[0] == "instant" and c[2] == 0x000D01EA]
        self.assertEqual(len(autocasts), 1)              # 开自动施法一次就够，不刷
        beats = [n for n in notes if n.startswith("心跳")]
        self.assertTrue(beats)
        self.assertIn("技能 下令/进冷却/被退/没学/缺蓝：", beats[0])
        self.assertIn("windwalk ", beats[0])
        self.assertIn("｜施法后续 ", beats[0])

    def test_decision_records_carry_game_seconds_and_loop_gap(self):
        """09-21 战斗决策明细第一版：GameClock.read() 已经是秒，game_now 又除了 1000 —— 实机 520 秒记成 0.5。
        这里让游戏时钟从 500 秒起、按 2 倍速走，记录里的 g 必须落在 500 起的那一段；gap = 两轮之间的假时钟步长。"""
        clock = FakeTime()
        cmd = FakeCmd({u.addr: u.type for u in scenario(0).mine})
        state = {"i": 0}

        def read():
            snap = scenario(state["i"])
            state["i"] += 1
            return snap

        ses = SimpleNamespace(pid=4242, cmd=cmd, read=read)
        import tempfile
        import declog
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        decf = os.path.join(tmp.name, "decisions.jsonl")
        game = SimpleNamespace(read=lambda: 500.0 + (clock.t - 1000.0) * 2.0)
        with mock.patch.object(C, "DECF", decf), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", clock), \
                mock.patch.object(C, "note", lambda *a: None), \
                mock.patch.object(C, "write_deadlines", lambda *a, **k: None), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: game), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            C.main()
        recs = declog.tail(decf, limit=5000)
        casts = [r for r in recs if r["act"] == "放技能"]
        self.assertTrue(casts, "冒烟场景里会放技能，要有记录")
        end = 500.0 + LOOPS * STEP * 2.0
        for r in recs:
            self.assertGreaterEqual(r["g"], 500.0, r)
            self.assertLessEqual(r["g"], end, r)
        gaps = {r["timing"].get("gap") for r in recs if r["timing"].get("gap") is not None}
        self.assertEqual(gaps, {STEP * 1000}, gaps)
        self.assertTrue(all("cmd" in r["timing"] for r in casts))


if __name__ == "__main__":
    unittest.main()

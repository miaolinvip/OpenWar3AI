# -*- coding: utf-8 -*-
"""mod_combat.main() 驱动 unitmind 那条路：有人在打它 -> 按打它的人算还能撑几秒 -> 拉出 -> 养伤 -> 掩护 -> 回血归队。

test_combat_main_smoke 的假命令 current_task 永远返回空，新路径一次都走不到 —— 这里单独驱动。
场景：我方步兵 A 200/420 被 6 个兽人步兵同时砍（任务目标全是 A），旁边一个满血火枪手、一个满血步兵；家在 3000 外。
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_combat as C  # noqa: E402
import unitmind as UM  # noqa: E402
from w3state import Unit  # noqa: E402
from test_combat_main_smoke import FakeClaims, FakeCmd, FakeOdds, _Stop  # noqa: E402

LOOPS = 200          # 200 × 0.35 = 70 秒：心跳 60 秒一次，至少打一次
STEP = 0.35
HEAL_AT = 80          # 第 80 轮起：兽人走远、A 回到 300/420（71%）


def unit(addr, typ, owner, x, y, hp, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y), hp=hp,
                hp_max=hp if hp_max is None else hp_max, order=0, order_known=True,
                handle_lo=addr, handle_hi=7, task_known=True, has_task=True)


GRUNTS = list(range(100, 106))


def scenario(i):
    healed = i >= HEAL_AT
    mine = [unit(10, "hfoo", 0, 0, 0, 300 if healed else 200, 420),
            unit(11, "hrif", 0, 600, 0, 535),
            unit(12, "hfoo", 0, 200, 200, 420),
            unit(90, "htow", 0, -3000, 0, 1500)]
    foes = [unit(a, "ogru", 1, (6000 if healed else 80) + 20 * k, 30 * k, 700) for k, a in enumerate(GRUNTS)]
    return SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine + foes, age_ms=5)


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, 0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps += 1
        self.t += STEP
        if self.sleeps >= LOOPS:
            raise _Stop


class Cmd(FakeCmd):
    def current_task(self, u):
        return {"target": (10, 7)} if u in GRUNTS else None     # 6 个兽人都在打 A


class UnitMindLoop(unittest.TestCase):
    def test_pull_rest_cover_and_return(self):
        notes, rest_writes = [], []
        cmd = Cmd({u.addr: u.type for u in scenario(0).mine})
        state = {"i": 0}

        def read():
            snap = scenario(state["i"])
            state["i"] += 1
            return snap

        def deadlines(path, d, *a, **k):
            if path == C.RESTINGF:
                rest_writes.append((state["i"], set(d)))

        ses = SimpleNamespace(pid=4242, cmd=cmd, read=read)
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        decf = os.path.join(tmp.name, "decisions.jsonl")
        with mock.patch.object(C, "DECF", decf), \
                mock.patch.object(C, "Session", lambda: ses), \
                mock.patch.object(C, "Claims", FakeClaims), \
                mock.patch.object(C, "time", Clock()), \
                mock.patch.object(C, "note", notes.append), \
                mock.patch.object(C, "write_deadlines", deadlines), \
                mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                self.assertRaises(_Stop):
            C.main()

        self.assertEqual([n for n in notes if "出错" in n], [])
        # 先确认场景前提：按表算 A 在 TTD_OUT 以内（期望按公式算，不是凭感觉）
        inc = UM.incoming_dps(scenario(0).mine[0], scenario(0).units[4:], C._unit_dps, lambda t: C.ARMOR.get(t, 0.0))
        self.assertLess(200 / inc, UM.TTD_OUT)
        # 1) A 被拉出（纯移动），日志里说得出「几个人在打它、还能撑几秒」
        self.assertIn(("move", 10), [c[:2] for c in cmd.calls])
        self.assertTrue(any("6 个敌人在打它" in n for n in notes), [n for n in notes if "处境" in n][:3])
        # 2) 掩护：火枪手和另一个步兵被叫去打兽人（攻击令），远程在前
        cover = [c for c in cmd.calls if c[0] == "target" and c[2] == C.ORDER_ATTACK and c[3] in GRUNTS]
        self.assertEqual([c[1] for c in cover[:2]], [11, 12])
        # 3) 养伤期间本模块从不给 A 派攻击
        self.assertFalse([c for c in cmd.calls if c[0] == "target" and c[1] == 10])
        # 4) 养伤名单：受伤那段一直有 A；回血后发一次空表
        during = [w for i, w in rest_writes if i < HEAL_AT]
        self.assertTrue(during and all(10 in w for w in during))
        self.assertTrue(any(i > HEAL_AT and w == set() for i, w in rest_writes))
        self.assertTrue(any(n.startswith("脱离结束归队：hfoo@A") for n in notes))
        # 5) 心跳里有这一段
        beat = next(n for n in notes if n.startswith("心跳"))
        self.assertIn("单位处境 读敌", beat)
        self.assertIn("养伤 在册", beat)
        # 6) 战斗决策明细（declog）：中文名、理由、依据的数据、耗时都在（远见「战斗决策」页读这个文件）
        import declog
        recs = declog.tail(decf)
        pull = next(r for r in recs if r["act"] == "拉出撤退" and r["units"][0]["addr"] == "A")
        self.assertEqual(pull["units"][0]["name"], declog.unit_name("hfoo"))
        self.assertIn("个敌人在打它", pull["why"])
        self.assertIn("在打它的敌人", pull["inputs"])
        self.assertTrue({"snapAge", "decide", "cmd", "total"} <= set(pull["timing"]))
        self.assertTrue(any(r["act"] == "掩护撤退" for r in recs))


if __name__ == "__main__":
    unittest.main()

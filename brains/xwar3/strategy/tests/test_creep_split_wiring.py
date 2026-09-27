# -*- coding: utf-8 -*-
"""第二支打野小队真的被大脑用起来了（runtime.job_tactics -> brain/creepsplit.py）。

09-23 单位多、有两个英雄时分两路练级，各带一支小分队打野。
⚠ 模块自己好用不等于接上了（要看计数从 0 变成非 0）—— 这里驱动整个 Brain.step。
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import gamedata as G  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

HOME = (4352, -2688)
CAMP_A = (1200, -2600)        # 主队那个营地（3 只）
CAMP_B = (4200, 600)          # 第二队那个（2 只，小队打得过）


def creep(addr, x, y, code="nfor"):
    return U(addr, code, x, y, owner=G.CREEP_OWNER)


def snapshot(t):
    hall = U(0x100, "htow", *HOME)
    peas = [U(0x300 + i, "hpea", HOME[0] + 40 * i, HOME[1] + 60) for i in range(5)]
    foot = [U(0x400 + i, "hfoo", HOME[0] - 200 + 40 * i, HOME[1] + 200) for i in range(12)]
    heroes = [U(0x500, "Hamg", HOME[0], HOME[1] + 300), U(0x501, "Hmkg", HOME[0] + 60, HOME[1] + 300)]
    # 小队（4 个步兵 + 1 级大法师）对 2 只野怪只有 1.55 < CREEP_POWER_MIN 1.808，对 1 只才打得过（一次性分析脚本 wiring_probe.py 实算）
    wild = [creep(0x900 + i, CAMP_A[0] + 80 * i, CAMP_A[1]) for i in range(3)] + [creep(0x920, *CAMP_B)]
    # 对面要有一整个基地：只放一座建筑的话大脑会判「收尾拆家」（finishing），第二队每拍都被召回
    enemy = [U(0x800, "hcas", -4352, 2944, owner=1)] + \
            [U(0x810 + i, "hhou", -4352 + 200 * i, 3200, owner=1) for i in range(4)] + \
            [U(0x820 + i, "hbar", -4600 + 200 * i, 2600, owner=1) for i in range(2)] + \
            [U(0x830 + i, "hfoo", -4300 + 60 * i, 2800, owner=1) for i in range(6)]
    return SimpleNamespace(units=[hall] + peas + foot + heroes + wild + enemy,
                           me=0, gold=400, lumber=300, food_used=25, food_cap=50), t


class Wiring(unittest.TestCase):
    def test_brain_sends_a_second_party_and_nobody_else_commands_them(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.act.scan_trees = lambda x, y: []
            b.config.values["enable_attack"] = False   # 只测打野分队这条线：主队一出击第二队本来就该被召回
            sent = []                                  # 这一拍真的发下去的意图（last_intents 只留了条数，看不到是谁）
            orig_do = b._do
            b._do = lambda it, t, _o=orig_do, _s=sent: (_s.append(it), _o(it, t))[1]
            leads, clashes, went = set(), [], 0
            for t in range(2, 60, 2):
                snap, _ = snapshot(t)
                sent.clear()
                b.step(snap, float(t))
                if b.splitcreep.party is not None:
                    leads.add(b.splitcreep.party.hero)
                    second = b.splitcreep.busy_addrs
                    # 「给英雄让路 / 卡太久错身」是防堵路那一层（brain/unstick.py），对谁都生效、只推一拍，不算抢指挥
                    mine = {u.addr for it in sent for u in it.units
                            if "第二队" not in it.why and "让路" not in it.why and "错身" not in it.why}
                    clashes += sorted(mine & second)
                    went += sum(1 for it in sent if "第二队" in it.why)
            self.assertEqual({n: s for n, s in b.jobs.stats().items() if s["errors"]}, {})
            self.assertGreaterEqual(b.counts.get("分队打野·派出", 0), 1, dict(b.counts))
            self.assertGreaterEqual(b.counts.get("分队打野·走过去", 0), 1, "派出去之后每拍还在往营地走")
            self.assertEqual(leads, {0x500}, "等级低的那个带队；两个都 1 级时按地址稳定挑（不来回换人）")
            self.assertGreater(went, 0, "第二队自己的意图要真的发出去")
            self.assertFalse(clashes, f"第二队的人被主队同一拍又指挥了：{sorted(set(clashes))}")


if __name__ == "__main__":
    unittest.main()

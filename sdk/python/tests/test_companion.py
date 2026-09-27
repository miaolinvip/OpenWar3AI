# -*- coding: utf-8 -*-
"""RPG 辅助 / 玩伴（09-24）：JASS 通道的参数打包和多人局保护、地图物体数据解析（带不带等级两种格式、保护图的 TXT）、
名字优先级、台词冷却和大模型退回、玩伴认「那个槽最好的单位」、右键才算叫它。都不连游戏。"""
import os
import struct
import sys
import time
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402

w3paths.setup()
import w3fast  # noqa: E402
from openwar3 import jass as J  # noqa: E402
import w3mpq as M  # noqa: E402
from openwar3.companion import Companion  # noqa: E402
from openwar3.mapdata import MapData  # noqa: E402
from openwar3.orders import SMART  # noqa: E402
from openwar3.talk import Talk  # noqa: E402


class FakeGame:
    """记下 JASS 通道发出去的 Call；返回值按名字配。"""

    def __init__(self, humans=1, fair=False, player=None, returns=None):
        self.pid, self.fair, self.player = 1, fair, player
        self._w3fast = w3fast
        self.calls = []
        self.humans = humans
        self.returns = returns or {}

    def _one(self, call):
        self.calls.append(call)
        if call.op == w3fast.OP["jass_handle_of"]:
            return NS(ok=True, values=(0x100123,) + (0,) * 7, reason="accepted")
        name = call.scratch[J.TEXT_OFF:].split(b"\0")[0].decode()
        if name == "GetPlayerController":
            pid = call.args[0] - 0x1000
            v = 0 if pid < self.humans or pid == 0 else 5
        elif name == "GetPlayerSlotState":
            pid = call.args[0] - 0x1000
            v = 1 if pid < self.humans or pid == 0 else 0
        elif name == "Player":
            v = 0x1000 + call.args[0]
        else:
            v = self.returns.get(name, 0)
        return NS(ok=True, values=(v,) + (0,) * 7, reason="accepted")


class JassTest(unittest.TestCase):
    def test_signature(self):
        self.assertEqual(J.parse_signature("(Hplayer;IRRR)Hunit;"), (("Hplayer", "I", "R", "R", "R"), "Hunit"))
        self.assertEqual(J.parse_signature("()V"), ((), "V"))
        self.assertEqual(J.parse_signature(J.signatures()["DisplayTimedTextToPlayer"]), (("Hplayer", "R", "R", "R", "S"), "V"))
        self.assertGreater(len(J.signatures()), 1200)

    def test_marshal(self):
        g = FakeGame(returns={"CreateUnit": 0x100777})
        j = J.Jass(g)
        h = j.CreateUnit(0x1001, "Hpal", 1.5, -2.0, 270.0)
        self.assertEqual(h, 0x100777)
        c = g.calls[-1]
        self.assertEqual(c.op, w3fast.OP["jass"])
        self.assertEqual(c.args[0], 0x1001)
        self.assertEqual(c.args[1], int.from_bytes(b"Hpal", "big"))                      # 四字码自动转
        self.assertEqual(c.args[2], struct.unpack("<I", struct.pack("<f", 1.5))[0])      # 实数 -> float 位
        self.assertEqual(c.scratch[J.TEXT_OFF:], b"CreateUnit\0")

    def test_strings_units_and_real_return(self):
        g = FakeGame(returns={"GetUnitX": struct.unpack("<I", struct.pack("<f", 123.5))[0]})
        j = J.Jass(g)
        j.DisplayTimedTextToPlayer(0x1000, 0.0, 0.0, 5.0, "你好")
        c = g.calls[-1]
        self.assertEqual(c.args[4], 1)                                                    # 第 1 个字符串
        self.assertEqual(c.scratch[J.TEXT_OFF:], b"DisplayTimedTextToPlayer\0" + "你好".encode() + b"\0")
        unit = NS(addr=0x1234, handle_lo=5, handle_hi=6, type="hfoo")
        self.assertEqual(j.GetUnitX(unit), 123.5)                                        # 单位对象 -> 句柄；R 返回 -> float
        self.assertEqual(g.calls[-1].args[0], 0x100123)

    def test_arity_and_code_params(self):
        j = J.Jass(FakeGame())
        with self.assertRaises(J.JassError):
            j.CreateUnit(1, "Hpal")
        with self.assertRaises(J.JassError):
            j.call("TimerStart", 1, 1.0, False, lambda: None)                           # code 参数给不出函数
        j.call("TimerStart", 1, 1.0, False, None)                                        # null 可以（倒计时窗口要它）
        with self.assertRaises(AttributeError):
            j.NoSuchNative()

    def test_multiplayer_guard(self):
        g = FakeGame(humans=2)
        j = J.Jass(g)
        self.assertEqual(j.GetPlayerId(0x1000), 0)                                       # 只读的照常
        with self.assertRaises(J.JassError):
            j.CreateUnit(0x1000, "hfoo", 0.0, 0.0, 0.0)                                  # 改世界的拒
        self.assertFalse(J.readonly("IssueImmediateOrder"))                              # Issue* 不是只读
        self.assertTrue(J.readonly("IsUnitEnemy"))
        J.Jass(FakeGame(humans=1)).CreateUnit(0x1000, "hfoo", 0.0, 0.0, 0.0)             # 单人局可以

    def test_lane_guard(self):
        with self.assertRaises(J.JassError):
            J.Jass(FakeGame(fair=True)).GetLocalPlayer()
        with self.assertRaises(J.JassError):
            J.Jass(FakeGame(player=1)).GetLocalPlayer()


def obj_table(entries, leveled=False):
    """造一份物体数据：entries = [(原码, 新码或 None, [(字段, 类型, 值, 等级)])]，按 custom 分两张表。"""
    def one(orig, new, mods):
        b = orig.encode() + (new.encode() if new else b"\0\0\0\0") + struct.pack("<i", len(mods))
        for fid, t, v, lvl in mods:
            b += fid.encode() + struct.pack("<i", t)
            if leveled:
                b += struct.pack("<ii", lvl, 0)
            b += struct.pack("<i", v) if t == 0 else struct.pack("<f", v) if t in (1, 2) else v.encode() + b"\0"
            b += b"\0\0\0\0"
        return b
    orig = [e for e in entries if not e[1]]
    cust = [e for e in entries if e[1]]
    return (struct.pack("<ii", 2, len(orig)) + b"".join(one(*e) for e in orig)
            + struct.pack("<i", len(cust)) + b"".join(one(*e) for e in cust))


class MapDataTest(unittest.TestCase):
    def test_objects_plain_and_leveled(self):
        wts = {12: "小圣骑"}
        u = M.parse_objects(obj_table([("hfoo", None, [("unam", 3, "改过的步兵", 0), ("uhpm", 0, 999, 0)]),
                                       ("Hpal", "H000", [("unam", 3, "TRIGSTR_012", 0), ("uhab", 3, "AHhb,AHds", 0)])]), wts)
        self.assertEqual(u["hfoo"]["fields"]["uhpm"], 999)
        self.assertEqual((u["H000"]["base"], u["H000"]["custom"], u["H000"]["fields"]["unam"]), ("Hpal", True, "小圣骑"))
        a = M.parse_objects(obj_table([("AHhb", "A000", [("anam", 3, "大圣光", 0), ("Cool", 1, 5.0, 2)])], leveled=True),
                            {}, leveled=True)
        self.assertEqual(a["A000"]["fields"]["anam"], "大圣光")
        self.assertAlmostEqual(a["A000"]["fields"]["Cool@2"], 5.0)                     # 带等级的字段按 字段@等级 存

    def test_profile(self):
        p = M.parse_profile("﻿[H001]\r\nName=全能骑士\r\nPropernames=普里斯特,雷霆\r\nTip=\"带引号\"\r\n// 注释\r\n".encode())
        self.assertEqual(p["H001"], {"Name": "全能骑士", "Propernames": "普里斯特,雷霆", "Tip": "带引号"})

    def test_name_priority(self):
        md = MapData.__new__(MapData)
        md.wts = {}
        md.units = {"H000": {"base": "Hpal", "custom": True, "fields": {"unam": "小圣骑", "uhab": "AHhb,AHds"}},
                    "H001": {"base": "Hpal", "custom": True, "fields": {}}}
        md.items, md.abilities, md.buffs = {}, {}, {}
        md.profile = {"H002": {"Name": "全能骑士", "Propernames": "普里斯特"}}
        self.assertEqual(md.name_of("H000"), "小圣骑")                                  # 地图改过的名字
        self.assertEqual(md.name_of("H002"), "全能骑士")                                # 保护图的 TXT
        self.assertEqual(md.name_of("H001"), "圣骑士")                                  # 新建的没改名：沿用原型的名字
        self.assertEqual(md.name_of("hfoo"), "步兵")                                    # 内置
        self.assertEqual(md.name_of("zzzz"), "zzzz")
        self.assertEqual(md.hero_skills("H000"), ["AHhb", "AHds"])
        self.assertEqual(md.hero_names("H002"), ["普里斯特"])


class TalkTest(unittest.TestCase):
    def test_cooldown_and_format(self):
        t = Talk(lines={"kill": ["干掉{enemy}！{nothing}"]}, min_gap=5)
        self.assertEqual(t.line("kill", now=100.0, enemy="狗头人"), "干掉狗头人！")        # 缺的变量留空
        self.assertIsNone(t.line("kill", now=101.0, enemy="x"))                           # 冷却中
        self.assertIsNone(t.line("idle", now=102.0))                                      # 全局间隔内（不急的话）
        self.assertIsNotNone(t.line("poke", now=102.0))                                   # 急的话不等全局间隔

    def test_llm_fallback(self):
        class Broken:
            def reply(self, *a):
                raise OSError("模型没开")
        t = Talk(lines={"hello": ["你好"]}, llm=Broken())
        self.assertIsNone(t.line("hello", now=1.0))                                       # 先去问模型
        for _ in range(100):
            got = t.take()
            if got:
                break
            time.sleep(0.01)
        self.assertEqual(got, ["你好"])                                                   # 模型失败退回固定台词


def unit(addr, owner, hero=True, level=1, hp=100.0, handle=None, type_="Hpal"):
    return NS(addr=addr, owner=owner, is_hero=hero, level=level, hp=hp, hp_max=100.0, alive=True, x=0.0, y=0.0,
              type=type_, handle=handle or (addr, addr))


class FakeCompanionGame:
    def __init__(self, units, orders=None):
        self._units, self.orders, self.pid, self.map_data = units, orders or {}, 1, None

    def units(self, owner="all", types=None, alive=True):
        return [u for u in self._units if (owner == "all" or u.owner == owner) and (u.alive or not alive)]

    def snapshot(self, max_age=0.05):
        return NS(units=self._units, me=0)

    def is_building(self, u):
        return False

    def current_order(self, u):
        return self.orders.get(u.addr)

    def hero_info(self, u):
        return {"skill_points": 0}


class CompanionTest(unittest.TestCase):
    def make(self, g):
        c = Companion()
        c.g, c.mode, c.slot, c.buddy_addr, c.dead_at, c.last_poke = g, "ally", 1, 0x10, None, None
        c.match_level, c.cg, c.master_addr = False, g, None
        return c

    def test_buddy_follows_slot_not_address(self):
        # 地图把造出来的圣骑士（0x10）换成了一个地图英雄（0x20）：玩伴应当认新英雄，而不是以为玩伴死了
        g = FakeCompanionGame([unit(0x20, 1, type_="OC10"), unit(0x30, 0)])
        c = self.make(g)
        self.assertEqual(c.buddy_unit(g).addr, 0x20)
        self.assertEqual(c.buddy_addr, 0x20)

    def test_poke_needs_smart_on_buddy(self):
        b, m = unit(0x20, 1), unit(0x30, 0)
        g = FakeCompanionGame([b, m], orders={0x30: {"order": 0x000D000F, "target": b.handle}})   # 攻击令指着玩伴：不算
        c = self.make(g)
        poked = []
        c.on_poke = lambda g_: poked.append(1)
        c._poke(g, m, b)
        self.assertEqual(poked, [])
        g.orders[0x30] = {"order": SMART, "target": b.handle}
        c.last_poke = False
        c._poke(g, m, b)
        c._poke(g, m, b)                                                                    # 按住不放只算一次
        self.assertEqual(poked, [1])


if __name__ == "__main__":
    unittest.main()

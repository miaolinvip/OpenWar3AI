# -*- coding: utf-8 -*-
"""JASS 对外接口（09-24）：目录分类、小脚本（JASS 写法翻译、变量、嵌套、算术、单位属性、报错带行号）、
visual 封装（发出去的 native 顺序和参数、事件计数器只报新增）。JASS 通道换成假的，只记调用。"""
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3paths  # noqa: E402

w3paths.setup()
from openwar3 import jass as J  # noqa: E402
from openwar3.jassrun import Script, ScriptError, translate  # noqa: E402
from openwar3.visual import Visual  # noqa: E402


class FakeJass:
    """按名字记调用；返回值：Create*/Add*/Dialog* 给递增句柄，GetTriggerExecCount 按脚本给。"""

    def __init__(self):
        self.calls, self.next, self.exec_counts = [], 100, {}

    def call(self, name, *args):
        J.parse_signature(J.signatures()[name])          # 名字必须在表里
        self.calls.append((name, args))
        if name == "GetTriggerExecCount":
            return self.exec_counts.get(args[0], 0)
        if name in ("GetLocalPlayer", "Player"):
            return 7
        if name == "GetUnitX":
            return float(args[0].x) if hasattr(args[0], "x") else 0.0
        if name.startswith(("Create", "Add", "Dialog", "Convert", "MultiboardGetItem")):
            self.next += 1
            return self.next
        return None

    def __getattr__(self, name):
        return lambda *a: self.call(name, *a)

    def names(self):
        return [c[0] for c in self.calls]


def fake_game(units=()):
    j = FakeJass()
    snap = NS(units=list(units), me=0)
    g = NS(jass=j, snapshot=lambda max_age=0.05: snap, is_building=lambda u: False)
    return g, j


def unit(addr, owner=0, hero=True, x=100.0, y=200.0, type_="Hpal"):
    return NS(addr=addr, owner=owner, is_hero=hero, alive=True, x=x, y=y, hp=500.0, hp_max=500.0, level=3, type=type_)


class CatalogTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(J.category_of("CreateTextTag"), "visual")
        self.assertEqual(J.category_of("CreateMultiboard"), "ui")
        self.assertEqual(J.category_of("IssuePointOrder"), "order")
        self.assertEqual(J.category_of("GetTriggerUnit"), "event_response")
        self.assertEqual(J.category_of("CaptainAttack"), "ai")
        self.assertEqual(J.category_of("ConvertPlayerColor"), "convert")
        rows = J.catalog("TextTag")
        self.assertTrue(rows and all("TextTag" in r["name"] for r in rows))
        self.assertTrue(any(r["verified"] for r in rows))
        self.assertTrue(set(J.VERIFIED) <= set(J.signatures()))           # 实机调过的名字都在表里
        self.assertEqual(J.category_of("GetTriggerExecCount"), "trigger")  # 计数，不是事件回应（09-25 从 event_response 挪出来）
        self.assertEqual(set(J.CATEGORY_NAMES), set(J.CATEGORY_NAMES_EN))


class DocsTest(unittest.TestCase):
    """09-25 native 查询要带中文注释：每一条都有对应的中文说明 —— 英文界面用 en。"""
    def test_every_native_has_docs(self):
        import re
        han = re.compile(r"[一-鿿]")
        sigs, docs = J.signatures(), J.docs()
        self.assertEqual(set(sigs), set(docs), "jass_docs.json 和签名表对不上")
        for name, sig in sigs.items():
            d = docs[name]
            self.assertTrue(han.search(d["zh"]), f"{name} 没有中文说明")
            self.assertTrue(d["en"] and not han.search(d["en"]), f"{name} 英文说明为空或夹了中文")
            self.assertEqual(len(d["params"]), len(J.parse_signature(sig)[0]), f"{name} 参数名个数和签名不一致")

    def test_search_by_description_and_lang(self):
        rows = J.catalog("漂浮文字")
        self.assertIn("CreateTextTag", [r["name"] for r in rows])
        self.assertTrue(all("漂浮文字" in r["zh"] or "漂浮文字" in r["name"] for r in rows))
        en = J.catalog("camera shake", lang="en")                      # 多个词 = 每个都要出现
        self.assertTrue(en and all(r["desc"] == r["en"] for r in en))
        self.assertTrue(all("camera" in (r["name"] + r["en"]).lower() and "shake" in (r["name"] + r["en"]).lower() for r in en))
        first = J.catalog("TextTag")[0]                                 # 名字命中的排在说明命中的前面
        self.assertIn("texttag", first["name"].lower())
        self.assertEqual(J.catalog("CreateUnit", lang="en")[0]["categoryName"], J.CATEGORY_NAMES_EN["unit"])

    def test_pretty_signature_and_doc(self):
        self.assertEqual(J.pretty_signature("CreateUnit"), "CreateUnit(player id, integer unitid, real x, real y, real face) -> unit")
        self.assertEqual(J.pretty_signature("GetLocalPlayer"), "GetLocalPlayer() -> player")
        self.assertIn(J.describe("CreateUnit"), J.Jass.doc("CreateUnit"))
        self.assertIn(J.describe("CreateUnit", "en"), J.Jass.doc("CreateUnit", "en"))


class ScriptTest(unittest.TestCase):
    def test_translate_jass(self):
        code = 'local texttag t = CreateTextTag() // 注释\ncall SetTextTagText(t, "a//b", 0.02)\nset x = 1\nlocal integer n\n'
        self.assertEqual([s for _, s in translate(code)],
                         ["t = CreateTextTag()", 'SetTextTagText(t, "a//b", 0.02)', "x = 1"])      # 字符串里的 // 不是注释

    def test_vars_nested_math_and_units(self):
        h = unit(0x10, x=300.0, y=-50.0)
        g, j = fake_game([h])
        sc = Script(g)
        rows = sc.run("set h = hero()\nset t = CreateTextTag()\ncall SetTextTagPos(t, h.x + 100, h.y * 2, 60)\nGetUnitX(h)")
        self.assertEqual(rows[-1]["value"], 300.0)
        self.assertIn(("SetTextTagPos", (sc.vars["t"], 400.0, -100.0, 60)), j.calls)
        sc.run("call PingMinimapEx(h.x, h.y, 3, 255, 0, 0, false)")          # 变量跨 run 保留
        self.assertEqual(j.calls[-1], ("PingMinimapEx", (300.0, -50.0, 3, 255, 0, 0, False)))
        sc.run("set p = Player(1)\ncall SetUnitOwner(h, p, true)")          # 嵌套、单位对象直接当参数
        self.assertEqual(j.calls[-1][1][0].addr, 0x10)

    def test_errors_carry_line(self):
        g, j = fake_game([unit(1)])
        sc = Script(g)
        with self.assertRaises(ScriptError) as e:
            sc.run("call GetLocalPlayer()\n\nNoSuchNative(1)")
        self.assertEqual(e.exception.line, 3)
        for bad in ("import os", "x.__class__", "open('a')", "if true:"):
            with self.assertRaises(ScriptError):
                sc.run(bad)


class VisualTest(unittest.TestCase):
    def test_float_text_and_board(self):
        g, j = fake_game()
        v = Visual(g)
        v.float_text("你好", (10.0, 20.0), color=(255, 204, 0), seconds=3)
        self.assertEqual(j.names()[1:5], ["CreateTextTag", "SetTextTagText", "SetTextTagPos", "SetTextTagColor"])
        b = v.board("面板", [("心情", "开心"), ("击杀", "3")], icon="BTNHeroPaladin.blp")
        self.assertIn(("MultiboardSetTitleText", (b.handle, "面板")), j.calls)
        icon = [c for c in j.calls if c[0] == "MultiboardSetItemIcon"][0]
        self.assertTrue(icon[1][1].startswith("ReplaceableTextures\\CommandButtons\\"))       # 只给文件名自动补目录
        v.clear()
        self.assertIn("DestroyMultiboard", j.names())
        self.assertEqual(v._items, [])

    def test_counters_report_only_new(self):
        g, j = fake_game()
        v = Visual(g)
        cmds = v.chat_commands(["-follow", "-stay"])
        trig_follow = cmds.counters["-follow"].trigger
        reg = [c for c in j.calls if c[0] == "TriggerRegisterPlayerChatEvent"]
        self.assertEqual([r[1][2:] for r in reg], [("-follow", True), ("-stay", True)])
        self.assertEqual(cmds.poll(), [])
        j.exec_counts[trig_follow] = 2
        self.assertEqual(cmds.poll(), ["-follow", "-follow"])                       # 打了两次就报两次
        self.assertEqual(cmds.poll(), [])                                           # 只报新增
        menu = v.menu("做什么？", ["跟着我", "原地守着"])
        self.assertIsNone(menu.clicked())
        j.exec_counts[menu.counters[1].trigger] = 1
        self.assertEqual(menu.clicked(), 1)


if __name__ == "__main__":
    unittest.main()

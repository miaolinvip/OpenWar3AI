# -*- coding: utf-8 -*-
"""W3P 世界块 / 树 / 事件 / 语义命令：Python 侧的常量、布局和 C 头文件逐项对上（不需要游戏）。"""
import os
import re
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3fast as F  # noqa: E402
import w3world as W  # noqa: E402

HEADER_H = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "native", "src", "ui_control_protocol.h")
HAS_HEADER = os.path.exists(HEADER_H)       # 发布包可能不带 native/：对头文件的核对跳过，其余照跑
SRC = open(HEADER_H, encoding="utf-8").read() if HAS_HEADER else ""


def const(name):
    m = re.search(rf"\b{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", SRC)
    assert m, name
    return int(m.group(1), 0)


def fields(struct_name):
    body = re.search(rf"struct {struct_name} \{{(.*?)\n\}};", SRC, re.S).group(1)
    body = re.sub(r"//[^\n]*", "", body)
    out = []
    for decl in re.findall(r"(?:volatile\s+)?(\w+)\s+([^;]+);", body):
        typ, names = decl
        for n in names.split(","):
            n = n.strip()
            m = re.match(r"(\w+)(?:\[(\w+)\])?", n)
            out.append((typ, m.group(1), m.group(2)))
    return out


@unittest.skipUnless(HAS_HEADER, "没有 native/ 的头文件")
class ConstantsTest(unittest.TestCase):
    def test_world_constants(self):
        self.assertEqual(const("kWar3WorldMagic"), W.WORLD_MAGIC)
        self.assertEqual(const("kWar3TreesMagic"), W.TREES_MAGIC)
        self.assertEqual(const("kWar3EventsMagic"), W.EVENTS_MAGIC)
        self.assertEqual(const("kWar3WorldLayout"), W.LAYOUT)
        self.assertEqual((const("kWar3WorldUnits"), const("kWar3WorldDetails"), const("kWar3WorldItems"),
                          const("kWar3WorldPlayers"), const("kWar3WorldAbilities"), const("kWar3WorldBuffs"),
                          const("kWar3WorldTrees"), const("kWar3WorldEvents")),
                         (W.MAX_UNITS, W.MAX_DETAILS, W.MAX_ITEMS, W.PLAYERS, W.N_ABIL, W.N_BUFF, W.MAX_TREES, W.MAX_EVENTS))

    def test_unit_flags(self):
        for i, name in enumerate(("kWUAlive", "kWUHero", "kWUOrderKnown", "kWUTaskKnown", "kWUHasTask",
                                  "kWUTaskTarget", "kWUTaskPoint", "kWUOrderTarget", "kWUDetail")):
            self.assertIn(f"{name} = 1u << {i}", SRC)
        self.assertEqual((W.ALIVE, W.HERO, W.DETAIL), (1, 2, 256))

    def test_event_kinds(self):
        for kind, name in ((1, "kEvUnitAppeared"), (2, "kEvUnitDied"), (4, "kEvUnitDamaged"), (5, "kEvOrderChanged"),
                           (8, "kEvItemAppeared"), (9, "kEvItemRemoved"), (10, "kEvDamage"), (11, "kEvKilled"),
                           (100, "kEvGameStarted")):
            self.assertEqual(const(name), kind)
            self.assertIn(kind, W.EVENT_KINDS)

    def test_command_ops_and_status(self):
        pairs = (("point", "kCmdPointOrder"),("target", "kCmdTargetOrder"), ("immediate", "kCmdImmediateOrder"),
                      ("build", "kCmdBuild"), ("learn", "kCmdLearn"), ("use_item", "kCmdUseItem"), ("revive", "kCmdRevive"),
                      ("rally", "kCmdRally"), ("buy", "kCmdBuy"), ("item_drop", "kCmdItemDrop"),
                      ("q_tech", "kQueryTech"), ("q_feasible", "kQueryFeasible"),
                      ("q_visible", "kQueryVisible"), ("q_mine_gold", "kQueryMineGold"), ("q_captain", "kQueryCaptain"),
                      ("pause", "kCtlPause"), ("q_dead_heroes", "kQueryDeadHeroes"), ("cam_state", "kCamState"),
                      ("cam_field", "kCamSetField"), ("cam_look", "kCamLookAt"), ("cam_follow", "kCamFollow"),
                      ("cam_reset", "kCamReset"), ("cam_rotate", "kCamRotate"), ("cam_bounds", "kCamBounds"),
                      ("cam_smoothing", "kCamSmoothing"), ("cam_show_ui", "kCamShowUI"),
                      ("cam_clean_view", "kCamCleanView"), ("cam_fog", "kCamFogScale"),
                      ("hud_button", "kHudButton"), ("hud_quest", "kHudQuest"),
                      ("hud_quest_refresh", "kHudQuestRefresh"), ("hud_user_dialog", "kHudUserDialog"),
                      ("jass", "kJassNative"), ("jass_handle_of", "kJassHandleOf"), ("jass_unit_of", "kJassUnitOf"),
                      ("canvas_enable", "kCanvasEnable"), ("input_enable", "kInputEnable"))
        for py, c in pairs:
            self.assertEqual(F.OP[py], const(c), py)
        # 新加操作码时两边都要对上：Python 这边每一个都得在上面核对过
        self.assertEqual(set(F.OP) - {p for p, _ in pairs}, set())
        self.assertEqual((const("kLaneRoleDev"), const("kLaneRolePlayer"), const("kLaneRoleObserver")),
                         (F.ROLE_DEV, F.ROLE_PLAYER, F.ROLE_OBSERVER))
        for code, c in ((0, "kCmdAccepted"), (1, "kCmdRejected"), (2, "kCmdBadUnit"), (3, "kCmdNotOwner"),
                        (4, "kCmdFault"), (7, "kCmdBadTarget"), (8, "kCmdForbidden")):
            self.assertEqual(const(c), code)
        self.assertEqual(const("kWar3FastSemantic"), F.SEMANTIC)
        self.assertEqual(const("kVerdictTargetNotVisible"), 1001)
        self.assertIn(1001, F.VERDICT)


@unittest.skipUnless(HAS_HEADER, "没有 native/ 的头文件")
class LayoutTest(unittest.TestCase):
    """按头文件的字段顺序算大小，和 Python 的 struct 格式对上（C 那边有 static_assert 钉着同样的数）。"""
    SIZES = {"DWORD": 4, "LONG": 4, "float": 4}

    def size_of(self, name, counts=None):
        counts = counts or {}
        total = 0
        for typ, _n, arr in fields(name):
            if typ in self.SIZES:
                k = 1 if arr is None else (int(arr) if arr.isdigit() else counts[arr])
                total += self.SIZES[typ] * k
            else:
                k = 1 if arr is None else counts[arr]
                total += self.size_of(typ, counts) * k
        return total

    def test_sizes(self):
        counts = {"kWar3WorldAbilities": W.N_ABIL, "kWar3WorldBuffs": W.N_BUFF, "kWar3WorldPlayers": W.PLAYERS,
                  "kWar3WorldUnits": W.MAX_UNITS, "kWar3WorldDetails": W.MAX_DETAILS, "kWar3WorldItems": W.MAX_ITEMS,
                  "kWar3WorldTrees": W.MAX_TREES, "kWar3WorldEvents": W.MAX_EVENTS}
        self.assertEqual(self.size_of("War3WorldUnit", counts), W.UNIT_SIZE)
        self.assertEqual(W._UNIT.size, W.UNIT_SIZE)
        self.assertEqual(self.size_of("War3WorldDetail", counts), W.DETAIL_SIZE)
        self.assertEqual(self.size_of("War3WorldItem", counts), W.ITEM_SIZE)
        self.assertEqual(W._ITEM.size, W.ITEM_SIZE)
        self.assertEqual(self.size_of("War3WorldPlayer", counts), W.PLAYER_SIZE)
        self.assertEqual(self.size_of("War3WorldTree", counts), W.TREE_SIZE)
        self.assertEqual(self.size_of("War3WorldEvent", counts), W.EV_SIZE)
        self.assertEqual(W._EVENT.size, W.EV_SIZE)
        self.assertEqual(self.size_of("War3CmdReceipt", counts), F.RECEIPT_SIZE)
        self.assertEqual(W._HEADER.size, W.HDR)
        self.assertEqual(self.size_of("War3WorldExtra", counts), W.EXT_SIZE)
        self.assertEqual(self.size_of("War3WorldProd", counts), W.PROD_SIZE)
        self.assertEqual(W._PROD.size, W.PROD_SIZE)
        counts["kWar3WorldProds"] = W.MAX_PRODS
        self.assertEqual(const("kWar3WorldProds"), W.MAX_PRODS)
        self.assertEqual(self.size_of("War3WorldBlock", counts),                                   # 块尾：扩展区 + 生产表
                         W.WORLD_SIZE + W.EXT_SIZE + W.PROD_SIZE * W.MAX_PRODS)
        self.assertEqual(const("kWar3WorldExtraMagic"), W.EXT_MAGIC)
        self.assertEqual(const("kEvProdDone"), 12)
        self.assertEqual(W.EVENT_KINDS[12], "production.done")
        self.assertEqual(self.size_of("War3TreeBlock", counts), W.TREES_SIZE)
        self.assertEqual(self.size_of("War3EventRing", counts), W.EVENTS_SIZE)

    def test_unit_field_order(self):
        names = [n for _t, n, _a in fields("War3WorldUnit")]
        self.assertEqual(names, ["addr", "handleLo", "handleHi", "typeId", "owner", "flags", "x", "y", "hp", "hpMax",
                                 "mana", "manaMax", "order", "orderTargetLo", "orderTargetHi", "orderX", "orderY",
                                 "taskTargetLo", "taskTargetHi", "taskX", "taskY", "level", "xp", "skillPoints",
                                 "detail", "firstSeen", "visibleTo", "reserved"])

    def test_event_field_order(self):
        names = [n for _t, n, _a in fields("War3WorldEvent")]
        self.assertEqual(names, ["seq", "kind", "clockMs", "addr", "handleLo", "handleHi", "typeId", "owner",
                                 "a", "b", "x", "y", "value", "extra", "reserved"])

    def test_cam_state(self):
        self.assertEqual(self.size_of("War3CamState"), F.CAMSTATE_SIZE)
        names = [n for _t, n, _a in fields("War3CamState")]
        self.assertEqual(names, ["valid", "locked", "uiEpoch", "target", "eye", "fields", "bounds", "live",
                                 "fogColor", "fogStyle", "fogDensity", "fogStart", "fogEnd", "fogK"])
        raw = bytes(F.CAMSTATE_OFF) + F._CAMSTATE.pack(1, 0, 3, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0,
                                                      1650.0, 5000.0, -0.5, 1.2, 0.0, 1.57, 0.0,
                                                      -100.0, 100.0, -200.0, 200.0, 7.0, 8.0,
                                                      0xFF000000, 0, 1.0, 3000.0, 5000.0, 2.0)
        s = F.parse_cam_state(raw)
        self.assertEqual((s["ui_epoch"], s["target"], s["fields"]["distance"], s["bounds"][3], s["fog"]["k"]),
                         (3, (1.0, 2.0, 3.0), 1650.0, 200.0, 2.0))
        self.assertIsNone(F.parse_cam_state(bytes(F.CAMSTATE_OFF + F.CAMSTATE_SIZE)))

    def test_map_block(self):
        self.assertEqual(const("kWar3MapMagic"), W.MAP_MAGIC)
        names = [n for _t, n, _a in fields("War3MapBlock")]
        self.assertEqual(names[:16], ["magic", "seq", "epoch", "state", "cellSize", "cols", "rows", "done",
                                      "minX", "minY", "boundMinX", "boundMaxX", "boundMinY", "boundMaxY",
                                      "startCount", "starts"])
        self.assertIn("offsetof(War3MapBlock, cells) == 160", SRC)
        self.assertEqual(W.MAP_HDR, 160)

    def test_map_info_lookup(self):
        cells = bytes([0, 1, 2, 3])            # 2x2：(0,0) 可走可盖，(1,0) 不能走，(0,1) 不能盖
        m = W.MapInfo(1, True, 128, 2, 2, -128.0, -128.0, (0, 0, 0, 0), [], cells)
        self.assertTrue(m.walkable(-64, -64) and m.buildable(-64, -64))
        self.assertFalse(m.walkable(64, -64))
        self.assertTrue(m.walkable(-64, 64) and not m.buildable(-64, 64))
        self.assertIsNone(m.at(1000, 0))


class ParseTest(unittest.TestCase):
    def test_parse_unit_with_detail(self):
        u = W._UNIT.pack(0x1000, 7, 8, W.rawcode("Hpal"), 0,
                         W.ALIVE | W.HERO | W.ORDER_KNOWN | W.ORDER_TARGET | W.DETAIL | W.TASK_KNOWN,
                         1.0, 2.0, 500.0, 650.0, 100.0, 255.0, 0x000D007C, 1, 1, 0.0, 0.0, 0xFFFFFFFF, 0xFFFFFFFF,
                         0.0, 0.0, 3, 200, 1, 0, 5, 0b101, 0)
        d = bytearray(W.DETAIL_SIZE)
        struct.pack_into("<4I", d, 0, 0, 2, 1, 1)
        struct.pack_into("<3If", d, 16, W.rawcode("AHhb"), 2, 0x200, 3.5)
        struct.pack_into("<3If", d, 32, W.rawcode("AInv"), 1, 0, 0.0)
        struct.pack_into("<I", d, 16 + W.ABIL_SIZE * W.N_ABIL, W.rawcode("BHds"))
        struct.pack_into("<I", d, 16 + W.ABIL_SIZE * W.N_ABIL + 4 * W.N_BUFF, W.rawcode("phea"))
        (unit,) = W.parse_units(u, bytes(d), 1, 1)
        self.assertEqual((unit.type, unit.level, unit.is_hero, unit.alive), ("Hpal", 3, True, True))
        self.assertEqual(unit.order, 0x000D007C)
        self.assertEqual(unit.order_target, (1, 1))
        self.assertIsNone(unit.task_target)
        self.assertFalse(unit.has_task)
        self.assertEqual(unit.ability("AHhb").cooldown, 3.5)
        self.assertEqual(unit.buffs, ["BHds"])
        self.assertEqual(unit.items[0], "phea")
        self.assertEqual((unit.visible_to(0), unit.visible_to(1), unit.visible_to(2)), (True, False, True))

    def test_damage_event_fields(self):
        # 09-23 实测：火枪手打人 b = 伤害类型 4（normal）| 攻击类型 2（pierce）<< 8
        e = W.Event(1, "damage", 1.0, 0x100, (1, 1), "hfoo", 1, 0x200, 4 | (2 << 8), 0.0, 0.0, 9.0,
                    struct.unpack("<I", struct.pack("<f", 12.5))[0])
        self.assertEqual((e.source_addr, e.raw_damage, e.attack_type, e.damage_type), (0x200, 12.5, "pierce", "normal"))


if __name__ == "__main__":
    unittest.main()

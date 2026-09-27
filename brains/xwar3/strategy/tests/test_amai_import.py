# -*- coding: utf-8 -*-
"""AMAI 数据导入：tools/amai_import.py -> brain/amai_data/*.json。

期望值都是对着 AMAI 提交 ea95cfd8（SMUnlimited/AMAI）的源码数出来的，不是凭印象写的：
  战略数       tail -n +2 TFT/<Race>/Strategy.txt | grep -c .                      13 / 16 / 14 / 15
  call 数      去掉 // 注释后 grep -cE '^\\s*call\\b' TFT/<Race>/BuildSequence.ai   439 / 499 / 490 / 477
  set 数       同上 '^\\s*set\\b'                                                   12 / 13 / 13 / 15
  if/elseif 数 同上 '^\\s*(if|elseif)\\b'                                           48 / 57 / 49 / 46
  NormalHuman 二本分支       TFT/Human/BuildSequence.ai 第 412~420 行原文
  4 字码                     grep '^FOOTMAN\\t' TFT/StandardUnits.txt 这类逐个查的
  key_unit 为 0 的战略       awk -F'\\t' '{print $1, $8}' Strategy.txt：人族 BarrackMix、亡灵 NormalUndead

不依赖 AMAI 源码的测试读仓库里的 JSON（任何机器都能跑）。
有 AMAI 源码时（--amai 同款查找：AMAI_DIR / strategy/third_party/AMAI / 仓库根 third_party/AMAI）另外跑：
  独立重数一遍上面的数、重复运行导入两次比字节、和仓库里的 JSON/报告比字节（不同步就红）；
  有 perl 时用 AMAI 自带的 ejass.pl 预处理 races.eai，对照 STRAT_ 编号 / allskills 下标 / set_skills 的英雄行号。
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import amai_import as AI  # noqa: E402

DATA = ROOT / "brain" / "amai_data"
REPORT = ROOT / "docs" / "amai_data_report.md"
RACE_KEYS = ("human", "orc", "undead", "nightelf")
FOLDER = {"human": "Human", "orc": "Orc", "undead": "Undead", "nightelf": "Elf"}
AMAI_RACE = {"human": "HUMAN", "orc": "ORC", "undead": "UNDEAD", "nightelf": "ELF"}

EXPECTED_STRATEGIES = {"human": 13, "orc": 16, "undead": 14, "nightelf": 15}
EXPECTED_CALLS = {"human": 439, "orc": 499, "undead": 490, "nightelf": 477}
EXPECTED_SETS = {"human": 12, "orc": 13, "undead": 13, "nightelf": 15}
EXPECTED_CONDS = {"human": 48, "orc": 57, "undead": 49, "nightelf": 46}
# Heroes.txt 第 9~12 行（前 8 行是中立英雄）
EXPECTED_RACE_HEROES = {
    "human": ["ARCHMAGE", "MTN_KING", "PALADIN", "BLOOD_MAGE"],
    "orc": ["BLADE_MASTER", "FAR_SEER", "TAUREN_CHIEF", "SHADOW_HUNTER"],
    "undead": ["DEATH_KNIGHT", "DREAD_LORD", "LICH", "CRYPT_LORD"],
    "nightelf": ["DEMON_HUNTER", "KEEPER", "MOON_PRIESTESS", "WARDEN"],
}

AMAI_DIR = AI.find_amai_dir()
PERL = shutil.which("perl")

_cache = {}


def load(name):
    if name not in _cache:
        with open(DATA / f"{name}.json", encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def strip_comments(text):
    return [AI.strip_comment(line.rstrip("\r")) for line in text.split("\n")]


class TestCounts(unittest.TestCase):
    def test_strategy_counts(self):
        for race, n in EXPECTED_STRATEGIES.items():
            d = load(race)
            self.assertEqual(len(d["strategies"]), n, race)
            self.assertEqual(d["stats"]["strategies"], n, race)
            names = {s["Strategy Function Name"] for s in d["strategies"]}
            self.assertEqual(set(d["build"]) - {"global_init", "global_build"}, names, race)
            for name in names:
                self.assertIsNotNone(d["build"][name]["init"], f"{race} {name} init")
                self.assertEqual(sorted(d["build"][name]["tiers"]), ["1", "2", "3"], f"{race} {name}")

    def test_statement_counts(self):
        for race in RACE_KEYS:
            s = load(race)["stats"]
            self.assertEqual(s["calls"]["total"], EXPECTED_CALLS[race], race)
            self.assertEqual(s["sets"]["total"], EXPECTED_SETS[race], race)
            self.assertEqual(s["conditions"]["total"], EXPECTED_CONDS[race], race)

    def test_every_call_lands_somewhere(self):
        """JSON 里（init 列表 + global_build.calls + 各战略三层展开）出现过的 call 行号去重后 = 源码 call 数：展开没丢语句。"""
        for race in RACE_KEYS:
            b = load(race)["build"]
            lines = set()
            stmts = list(b["global_init"]) + list(b["global_build"]["calls"])
            for name, v in b.items():
                if name in ("global_init", "global_build"):
                    continue
                stmts += v["init"]
                for lst in v["tiers"].values():
                    stmts += lst
            for st in stmts:
                if st["fn"] != "set":
                    lines.add(st["line"])
            self.assertEqual(len(lines), EXPECTED_CALLS[race], race)

    def test_everything_translated(self):
        for race in RACE_KEYS:
            s = load(race)["stats"]
            self.assertEqual(s["calls"]["failed"], 0, race)
            self.assertEqual(s["sets"]["failed"], 0, race)
            self.assertEqual(s["conditions"]["unsupported"], 0, race)
            self.assertEqual(s["parse_problems"], [], race)
            self.assertEqual(s["warnings"], [], race)
        self.assertEqual(load("common")["unresolved_names"], {})


class TestNormalHuman(unittest.TestCase):
    def test_tier2_branch(self):
        """TFT/Human/BuildSequence.ai 412~420 行（elseif tier == 2 分支）。"""
        t2 = load("human")["build"]["NormalHuman"]["tiers"]["2"]
        got = [(s["fn"], s.get("qty"), s.get("code", s.get("hero")), s.get("prio"), s["cond"]) for s in t2]
        self.assertEqual(got, [
            ("BuildUnit", 1, 1, 80, None),          # hero[1]
            ("BuildUnit", 8, "hfoo", 65, None),     # FOOTMAN
            ("BuildUnit", 4, "hrif", 55, None),     # RIFLEMAN
            ("BuildUnit", 1, "hvlt", 57, None),     # ARCANE_VAULT
            ("BuildFront", 1, "hatw", 57, None),    # ARCANE_TOWER
            ("BuildUnit", 1, 2, 50, None),          # hero[2]
            ("BuildUnit", 2, "hmtm", 45, None),     # MORTAR
            ("BuildUnit", 1, "hcas", 45, None),     # CASTLE
        ])
        foot = t2[1]
        self.assertEqual(foot["name"], "FOOTMAN")
        self.assertEqual(foot["args"], {"qty": 8, "unitid": {"unit": "FOOTMAN", "code": "hfoo"}, "prio": 65})
        self.assertEqual(foot["raw"], "call BuildUnit(8, uFOOTMAN, 65)")
        self.assertEqual(foot["cond_raw"], ["not (tier == 3)", "tier == 2"])
        self.assertEqual(t2[0]["hero"], 1)
        self.assertNotIn("code", t2[0])
        # 所有 BuildUnit 都给了 4 字码或 hero
        for s in t2:
            if s["fn"] == "BuildUnit":
                self.assertTrue("hero" in s or re.fullmatch(r"\w{4}", s["code"]), s)

    def test_tier1_expansion_condition_arg(self):
        t1 = load("human")["build"]["NormalHuman"]["tiers"]["1"]
        exp = [s for s in t1 if s["fn"] == "BasicExpansionAM"]
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["line"], 456)
        self.assertEqual(exp[0]["args"]["build_it"], {"op": "lt", "lhs": {"fn": "GetMinesOwned"}, "rhs": 2})
        self.assertEqual((exp[0]["code"], exp[0]["prio"]), ("htow", 53))


class TestUndeadGlobalBuild(unittest.TestCase):
    GHOUL_COND = {"op": "and", "args": [
        {"op": "eq", "lhs": {"fn": "TownCount", "code": "ugho", "name": "GHOUL"}, "rhs": 0},
        {"op": "lt", "lhs": {"fn": "GetWood"}, "rhs": 500},
    ]}

    def test_ghoul_condition(self):
        """`if TownCount(uGHOUL) == 0 and GetWood() < 500 then call BuildUnit(1, uGHOUL, ghoul_prio)`。"""
        gb = load("undead")["build"]["global_build"]
        flat = [s for s in gb["calls"] if s["raw"] == "call BuildUnit(1, uGHOUL, ghoul_prio)"]
        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["cond"], self.GHOUL_COND)
        self.assertEqual(flat[0]["cond_raw"], ["TownCount(uGHOUL) == 0 and GetWood() < 500"])
        self.assertEqual(flat[0]["prio"], {"var": "ghoul_prio"})
        for t in ("1", "2", "3"):
            hit = [s for s in gb["tiers"][t] if s["raw"] == "call BuildUnit(1, uGHOUL, ghoul_prio)"]
            self.assertEqual(len(hit), 1, t)
            self.assertEqual(hit[0]["cond"], self.GHOUL_COND, t)

    def test_elseif_branch_negates_previous(self):
        gb = load("undead")["build"]["global_build"]
        hit = [s for s in gb["calls"] if s["line"] == 69]     # elseif GetRushFactor() < 10000 下的 BuildAtMine
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["cond"], {"op": "and", "args": [
            {"op": "not", "arg": {"op": "lt", "lhs": {"fn": "GetRushFactor"}, "rhs": 5000}},
            {"op": "lt", "lhs": {"fn": "GetRushFactor"}, "rhs": 10000},
        ]})

    def test_tier_folding(self):
        """`BuildItem(1, uM_SCROLL_OF_HEALING, 50 + (tier - 1) * 15)`：三层分别折成 50 / 65 / 80。"""
        gb = load("undead")["build"]["global_build"]
        raw = "call BuildItem(1, uM_SCROLL_OF_HEALING, 50 + (tier - 1) * 15)"
        got = {t: [s["prio"] for s in gb["tiers"][t] if s["raw"] == raw] for t in ("1", "2", "3")}
        self.assertEqual(got, {"1": [50], "2": [65], "3": [80]})
        flat = [s for s in gb["calls"] if s["raw"] == raw][0]
        self.assertEqual(flat["code"], "shea")
        self.assertEqual(flat["prio"]["op"], "add")
        # `if tier == 1 then call BuildItem(1, uM_TOWN_PORTAL, 100)` 只在一本出现
        tp = {t: sum(1 for s in gb["tiers"][t] if "uM_TOWN_PORTAL" in s["raw"]) for t in ("1", "2", "3")}
        self.assertEqual(tp, {"1": 1, "2": 0, "3": 0})


class TestHumanGlobalTierGates(unittest.TestCase):
    """TFT/Human/BuildSequence.ai 的 global_build_sequence 里散落的 tier 闸门（数字是源码行号，grep -n 查的）。"""

    @staticmethod
    def tiers_of(line):
        gb = load("human")["build"]["global_build"]
        return [t for t in ("1", "2", "3") if any(s["line"] == line for s in gb["tiers"][t])]

    def test_gates(self):
        self.assertEqual(self.tiers_of(125), ["1"])            # if tier == 1 then BuildItem(uM_TOWN_PORTAL)
        self.assertEqual(self.tiers_of(128), ["2", "3"])       # if tier > 1 then BuildItem(uHEALING_POTION)
        self.assertEqual(self.tiers_of(132), ["3"])            # if tier >= 3 then BuildItem(uSTAFF_OF_SANCTUARY)
        self.assertEqual(self.tiers_of(240), ["2", "3"])       # if tier >= 2 then BuildUnit(Max(...), uPRIEST)
        self.assertEqual(self.tiers_of(140), ["1"])            # if tier == 1 分支里的 AddUnitToAntiair(uRIFLEMAN,29)
        self.assertEqual(self.tiers_of(187), ["3"])            # else 分支里的 AddUnitToAntiair(uROCKET_TANK, ...)
        self.assertEqual(self.tiers_of(108), ["2", "3"])       # 嵌在 HasExpanded / GetGold > 2000 里的 tier >= 2 and ...

    def test_nested_residual_condition(self):
        gb = load("human")["build"]["global_build"]
        flat = [x for x in gb["calls"] if x["line"] == 108][0]
        self.assertEqual(flat["cond_raw"], ["HasExpanded()", "GetGold() > 2000",
                                            "tier >= 2 and TownCount(uGUARD_TOWER) >= 4 and TownCount(uARCANE_VAULT) < 2"])
        t2 = [x for x in gb["tiers"]["2"] if x["line"] == 108][0]
        self.assertEqual(t2["cond"], {"op": "and", "args": [
            {"fn": "HasExpanded"},
            {"op": "gt", "lhs": {"fn": "GetGold"}, "rhs": 2000},
            {"op": "ge", "lhs": {"fn": "TownCount", "code": "hgtw", "name": "GUARD_TOWER"}, "rhs": 4},
            {"op": "lt", "lhs": {"fn": "TownCount", "code": "hvlt", "name": "ARCANE_VAULT"}, "rhs": 2},
        ]})
        priest = [x for x in gb["tiers"]["2"] if x["line"] == 240][0]
        self.assertEqual((priest["code"], priest["prio"], priest["cond"]), ("hmpr", 40, None))   # awk '$1=="PRIEST"' StandardUnits.txt
        self.assertEqual(priest["qty"], {"fn": "Max", "args": [
            {"op": "sub", "lhs": 2, "rhs": {"fn": "TownCountDone", "args": [
                {"special": "merc_healer", "kind": "merc", "merc_type": "healer"}]}},
            0]})


class TestStrategyTable(unittest.TestCase):
    def test_key_unit_codes(self):
        zero = set()
        for race in RACE_KEYS:
            units = load("common")["standard_units"]["rows"]
            code_of = {r["Name in AI"]: r["4-letter-code"] for r in units}
            for s in load(race)["strategies"]:
                if s["key_unit"] in (0, "0"):
                    zero.add((race, s["Strategy Function Name"]))
                    self.assertIsNone(s["codes"]["key_unit"])
                    continue
                self.assertRegex(s["codes"]["key_unit"], r"^\w{4}$", (race, s["Strategy Function Name"]))
                self.assertEqual(s["codes"]["key_unit"], code_of[s["key_unit"]], (race, s["Strategy Function Name"]))
        self.assertEqual(zero, {("human", "BarrackMix"), ("undead", "NormalUndead")})

    def test_numbers_and_bools(self):
        rifle = [s for s in load("human")["strategies"] if s["Strategy Function Name"] == "Rifle"][0]
        self.assertIs(rifle["beginning_strat"], True)
        self.assertEqual(rifle["base rp"], 27)
        self.assertEqual(rifle["strat_minimum_time (in seconds/6)"], 40)
        self.assertEqual(rifle["codes"]["first hero bonus"], "Hamg")
        self.assertEqual(sorted(s["strat_id"] for s in load("human")["strategies"]), list(range(13)))
        # 人族 STRAT_<名字> 常量：09-18 用 perl ejass.pl races.eai TFT VER:TFT HUMAN RACE:HUMAN 预处理产物里抄的（没有 perl 时也钉住）
        self.assertEqual({s["Strategy Function Name"]: s["strat_id"] for s in load("human")["strategies"]}, {
            "Rifle": 0, "NormalHuman": 1, "Copters": 2, "HAntiTower": 3, "MassSB": 4, "SanctumMix": 5, "BarrackMix": 6,
            "Mortars": 7, "Dragonhawk": 8, "MassSp": 9, "VersusOrc": 10, "Gryph": 11, "Knight": 12})


class TestHeroSkills(unittest.TestCase):
    def test_rows_map_to_heroes(self):
        for race in RACE_KEYS:
            d = load(race)
            rows = d["hero_skills"]["rows"]
            self.assertEqual(len(rows), 36, race)
            heroes = [h["Hero"] for h in d["heroes"]["rows"]]
            self.assertEqual(heroes[8:], EXPECTED_RACE_HEROES[race], race)
            for i, r in enumerate(rows):
                self.assertEqual(r["hero"], EXPECTED_RACE_HEROES[race][i // 9], (race, r["row"]))
                self.assertEqual(r["hero_row"], 9 + i // 9, (race, r["row"]))
                self.assertEqual(r["hero_slot"], i % 9 // 3 + 1, (race, r["row"]))
                self.assertEqual(r["line"], ("major", "minor", "rare")[i % 3], (race, r["row"]))
                self.assertEqual(len(r["codes"]), 10)
                self.assertTrue(all(re.fullmatch(r"\w{4}", c) for c in r["codes"]), (race, r["row"]))
            self.assertTrue(all(c["ok"] for c in d["hero_skills"]["self_check"].values()), race)

    def test_archmage_first_row(self):
        r = load("human")["hero_skills"]["rows"][0]
        self.assertEqual(r["spells"][:2], ["WATER_ELEMENTAL", "BRILLIANCE_AURA"])
        self.assertEqual(r["codes"][:2], ["AHwe", "AHab"])
        self.assertEqual(r["hero_code"], "Hamg")

    def test_neutral(self):
        c = load("common")["neutral_hero_skills"]
        self.assertEqual(len(c["rows"]), 72)
        self.assertEqual(c["hero_order"], ["NAGA_SORCERESS", "BEAST_MASTER", "DARK_RANGER", "NEUTRAL_PIT_LORD",
                                           "BREW_MASTER", "GOBLIN_TINKER", "FIRELORD", "ALCHEMIST"])
        self.assertTrue(all(x["ok"] for x in c["self_check"].values()))


class TestParser(unittest.TestCase):
    def conv(self, text):
        names = AI.Names()
        names.units["GHOUL"] = {"code": "ugho", "u_index": 1, "race": "UNDEAD", "buy_type": "UNIT", "kind": "unit"}
        ctx = AI.Ctx("test", AI.new_vocab())
        node = AI.Converter(names, {}, ["range"], ["Small Dragon"]).expr(text, ctx)
        return node, ctx

    def test_ambiguous_precedence_is_unsupported(self):
        for text in ("a and b or c", "a or b and c", "not a == b"):
            node, ctx = self.conv(text)
            self.assertTrue(node.get("unsupported"), text)
            self.assertEqual(ctx.issues[0]["kind"], "unsupported")
        node, ctx = self.conv("not (a == b) and (c or d)")
        self.assertFalse(ctx.issues)
        self.assertEqual(node["op"], "and")

    def test_special_refs(self):
        self.assertEqual(self.conv("hero[2]")[0], {"hero": 2})
        self.assertEqual(self.conv("merc_range")[0], {"special": "merc_range", "kind": "merc", "merc_type": "range"})
        self.assertEqual(self.conv("dragons[1]")[0]["dragon_type"], "Small Dragon")
        self.assertEqual(self.conv("GetUnitCountDone(old_id[uGHOUL])")[0],
                         {"fn": "GetUnitCountDone", "code": "ugho", "name": "GHOUL", "id_kind": "raw"})
        node, ctx = self.conv("TownCount(uNO_SUCH_UNIT)")
        self.assertTrue(node["unresolved"])
        self.assertEqual(ctx.issues[0]["kind"], "unresolved")

    def test_subst(self):
        node, _ = self.conv("tier >= 2 and TownCount(uGHOUL) > 1")
        self.assertIs(AI.subst(node, "tier", 1), False)
        self.assertEqual(AI.subst(node, "tier", 3), {"op": "gt", "lhs": {"fn": "TownCount", "code": "ugho", "name": "GHOUL"}, "rhs": 1})
        self.assertEqual(AI.subst(self.conv("(7 - tier) / 2")[0], "tier", 2), 2)   # JASS 整数除法向零截断


@unittest.skipUnless(AMAI_DIR, "没有 AMAI 源码（设 AMAI_DIR 指向 clone）")
class TestFromSource(unittest.TestCase):
    def test_recount_from_source(self):
        for race in RACE_KEYS:
            base = AMAI_DIR / "TFT" / FOLDER[race]
            strat = (base / "Strategy.txt").read_text(encoding="utf-8").replace("\r", "").split("\n")[1:]
            self.assertEqual(sum(1 for x in strat if x.strip()), EXPECTED_STRATEGIES[race], race)
            lines = strip_comments((base / "BuildSequence.ai").read_text(encoding="utf-8"))
            self.assertEqual(sum(1 for x in lines if re.match(r"^\s*call\b", x)), EXPECTED_CALLS[race], race)
            self.assertEqual(sum(1 for x in lines if re.match(r"^\s*set\b", x)), EXPECTED_SETS[race], race)
            self.assertEqual(sum(1 for x in lines if re.match(r"^\s*(if|elseif)\b", x)), EXPECTED_CONDS[race], race)

    def test_repeatable_and_in_sync(self):
        with tempfile.TemporaryDirectory() as d:
            for i in (1, 2):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = AI.main(["--amai", str(AMAI_DIR), "--out", f"{d}/out{i}", "--report", f"{d}/out{i}/report.md"])
                self.assertEqual(rc, 0)
            for name in ["common.json"] + [f"{r}.json" for r in RACE_KEYS]:
                a = Path(d, "out1", name).read_bytes()
                self.assertEqual(a, Path(d, "out2", name).read_bytes(), f"{name} 两次运行不一致")
                self.assertEqual(a, (DATA / name).read_bytes(), f"仓库里的 {name} 和源码不同步：重跑 tools/amai_import.py")
            rep = Path(d, "out1", "report.md").read_bytes()
            self.assertEqual(rep, Path(d, "out2", "report.md").read_bytes())
            self.assertEqual(rep, REPORT.read_bytes(), "docs/amai_data_report.md 和源码不同步")


@unittest.skipUnless(AMAI_DIR and PERL, "没有 AMAI 源码或 perl")
class TestAgainstEjass(unittest.TestCase):
    """用 AMAI 自带的预处理器生成 <race>.ai，对照我们复算的 STRAT_ 编号、allskills 下标、英雄行号。"""

    def ejass(self, race):
        p = subprocess.run([PERL, "ejass.pl", "races.eai", "TFT", "VER:TFT", AMAI_RACE[race], f"RACE:{AMAI_RACE[race]}"],
                           cwd=str(AMAI_DIR), capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace")[:500])
        return p.stdout.decode("utf-8", "replace")

    def test_races_eai(self):
        for race in RACE_KEYS:
            text = self.ejass(race)
            d = load(race)
            strat = {m.group(1): int(m.group(2)) for m in re.finditer(r"constant integer STRAT_(\w+) = (\d+)", text)}
            self.assertEqual(strat, {s["Strategy Function Name"]: s["strat_id"] for s in d["strategies"]}, race)
            alls = {int(m.group(1)): m.group(2) for m in re.finditer(r"set allskills\[(\d+)\] = (\w+)", text)}
            self.assertEqual(len(alls), 360, race)
            for row in d["hero_skills"]["rows"]:
                for x, spell in enumerate(row["spells"], 1):
                    self.assertEqual(alls.get(row["block"] * 10 + x), spell, (race, row["row"], x))
            hn = {m.group(1): int(m.group(2))
                  for m in re.finditer(r"if hero\[1\]== u(\S+)\s+then\s+call LoadSkills\(1,(\d+),1\)", text)}
            self.assertEqual(hn, {h["Hero"]: h["row"] for h in d["heroes"]["rows"]}, race)


if __name__ == "__main__":
    unittest.main()

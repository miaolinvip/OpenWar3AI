# -*- coding: utf-8 -*-
"""开局谱（brain/opening.py + openings/*.json）：可在线切换、一份 JSON 一个开局。

09-18 按 Yumiko 的顺序加一版速开矿，与原开局并存、可随时切换；后续还会加入更多参考开局，结构要方便扩展。"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from brain import config as CFG  # noqa: E402
from brain import opening as OP  # noqa: E402
from brain.build import Want  # noqa: E402
from brain.runtime import Brain  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import FakeCmd, U  # noqa: E402

FE = "human_yumiko_fe"


def world(t=5.0, extra=(), gold=900):
    hall = U(0x100, "htow", -5184, 2944)
    mine = U(0x600, "ngol", -5632, 3584, owner=15)
    peas = [U(0x300 + i, "hpea", -5000, 2800) for i in range(6)]
    return build_world([hall, mine] + peas + list(extra), 0, t, gold, 900, 5, 12)     # 开局人口 5/12：还不到补农场


def built(b):
    """FakeCmd.builds 记的是整数 rawcode -> 四字码。"""
    return [bytes(((c >> 24) & 255, (c >> 16) & 255, (c >> 8) & 255, c & 255)).decode("latin-1") for _u, c, _x, _y in b.cmd.builds]


def brain(d, opening=FE):
    with open(os.path.join(d, "brain_config.json"), "w", encoding="utf-8") as fh:
        json.dump({"opening": opening} if opening else {}, fh)
    b = Brain(FakeCmd(), None, d)
    b.set_race("human", 0.0)
    return b


class Files(unittest.TestCase):
    def test_yumiko_fe_is_an_option(self):
        ops = OP.load_all()
        self.assertIn(FE, ops)
        self.assertEqual(ops[FE].race, "human")
        self.assertEqual([s.code for s in ops[FE].steps[:3]], ["hbar", "halt", "Hamg"])       # 兵营先于祭坛（两局录像都这样）
        self.assertIn(FE, OP.choices())
        self.assertEqual(OP.choices()[0], OP.NONE)
        self.assertEqual(CFG.coerce("opening", FE), FE)
        with self.assertRaises(ValueError):
            CFG.coerce("opening", "没有这份")
        row = next(r for r in CFG.schema() if r["key"] == "opening")
        self.assertEqual(row["labels"][FE], "Yumiko 速开矿")
        self.assertEqual(CFG.DEFAULTS["opening"], OP.NONE)                                     # 默认不用：行为和以前一样

    def test_new_file_becomes_an_option(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "orc_x.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": "orc_x", "name": "兽族试验", "race": "orc", "steps": [{"kind": "building", "code": "obar"}]}, fh)
            with open(os.path.join(d, "bad.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": "bad", "steps": [{"kind": "dance", "code": "x"}]}, fh)
            got = OP.load_all(d)
            self.assertEqual(list(got), ["orc_x"])                                              # 坏文件跳过、不拖垮别的
            self.assertIn("bad.json", OP._cache["errors"])
        OP.load_all()                                                                           # 缓存换回正式目录


class Run(unittest.TestCase):
    def op(self, **kw):
        d = dict(id="t", name="测试", race="human", until_s=600, keep={"hpea": 86, "hhou": None},
                 steps=[{"kind": "building", "code": "hbar"}, {"kind": "building", "code": "halt"},
                        {"kind": "unit", "code": "hfoo", "qty": 2, "max_wait_s": 30}])
        d.update(kw)
        return OP.parse(d)

    def test_table_yields_except_keep_and_order_is_kept(self):
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op())
            table = [Want("building", "halt", 1, 96, "base"), Want("unit", "hpea", 12, 85), Want("unit", "hpea", 15, 60),
                     Want("building", "hhou", 2, 92, "base"), Want("unit", "hfoo", 8, 65)]
            out = run.wants(b, world(), table, [])
            # 祭坛（建筑）让位；出兵留着但排在开局谱这一步（84）后面
            self.assertEqual([(x.code, x.prio) for x in out], [("hpea", 86), ("hpea", 86), ("hhou", 92), ("hfoo", 65), ("hbar", 84)])

    def test_army_fills_only_when_the_step_is_blocked_not_when_saving(self):
        # 第 21 局：开分矿那一步被「分矿有野怪先清」挡着，整个建造停住，9 分钟金子 1935、兵 8 个
        from brain.build import Planner
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op())
            wants = run.wants(b, world(), [Want("unit", "hfoo", 8, 65)], [])
            have = lambda x: 0                                                                    # noqa: E731
            blocked = Planner().plan(wants, have, 1000, 1000, 10, lambda x: "分矿旁边有野怪，先清" if x.code == "hbar" else None)
            self.assertEqual([d.want.code for d in blocked.decisions], ["hfoo"])                 # 被条件卡住：照常出兵
            saving = Planner().plan(wants, have, 100, 1000, 10, lambda x: None)
            self.assertEqual((saving.stopped_at.code, saving.decisions), ("hbar", []))            # 为这一步攒钱：兵不花钱

    def test_advances_skips_and_hands_back(self):
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op())
            notes = []
            w = world(t=5.0, extra=[U(0x700, "hbar", -4608, 3136), U(0x701, "halt", -5408, 2400)])
            out = run.wants(b, w, [], notes)
            self.assertEqual([x.code for x in out], ["hfoo"])                                   # 兵营、祭坛有了 -> 第 3 步
            self.assertEqual(notes[:2], ["开局谱第 1/3 步：建造 兵营", "开局谱第 2/3 步：建造 国王祭坛"])
            run.last_reason[2] = "攒钱"
            notes = []
            run.wants(b, world(t=40.0, extra=[U(0x700, "hbar", -4608, 3136), U(0x701, "halt", -5408, 2400)]), [], notes)
            self.assertFalse(run.active)                                                        # 等了 35 秒 > 30：跳过，走完
            self.assertIn("开局谱跳过第 3 步 训练 步兵 ×2：等了 35 秒（攒钱）", notes)
            table = [Want("unit", "hfoo", 8, 65)]
            self.assertIs(run.wants(b, world(t=41.0), table, []), table)                        # 交回建造表，原样

    def test_until_s_ends_it(self):
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op(until_s=100))
            notes = []
            run.wants(b, world(t=101.0), [], notes)
            self.assertFalse(run.active)
            self.assertTrue(notes[-1].startswith("开局谱 测试 到 100 秒还没走完（停在第 1 步）"))

    def test_towers_count_upgraded_ones(self):
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op(steps=[{"kind": "building", "code": "hwtw", "qty": 2, "count": ["hwtw", "hatw"]},
                                               {"kind": "building", "code": "hbar"}]))
            out = run.wants(b, world(extra=[U(0x700, "hatw", -4800, 2400), U(0x701, "hwtw", -5504, 3008)]), [], [])
            self.assertEqual([x.code for x in out], ["hbar"])                                   # 一座升成神秘之塔也算塔

    def test_expansion_tower_waits_for_the_hall(self):
        with tempfile.TemporaryDirectory() as dd:
            b = brain(dd, None)
            run = OP.OpeningRun(self.op(steps=[{"kind": "building", "code": "hwtw", "where": "expansion"}]))
            self.assertEqual(run.wants(b, world(), [], []), [])                                 # 分矿大厅还没出现：不出需求
            nat = U(0x702, "htow", -4800, -2432)
            out = run.wants(b, world(extra=[nat]), [], [])
            self.assertEqual(out[0].where, f"town:{nat.addr}")


class Runtime(unittest.TestCase):
    def test_pulled_mine_guards_are_not_mistaken_for_a_clear_camp(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d, None)
            mine = U(0x800, "ngol", -4864, -3200, owner=15)
            guard = U(0x801, "nomg", -4800, -3000, owner=12)
            w = world(extra=[mine, guard])
            self.assertEqual(b._expansion_guards(w, mine), [guard])
            guard.y = 0  # 追兵离开 1400 范围，仍然活着。
            self.assertEqual(b._expansion_guards(world(extra=[mine, guard]), mine), [guard])
            self.assertEqual(b._expansion_guards(world(extra=[mine]), mine), [])
            guard.handle_lo += 1  # 复用地址的新实体不继承守矿身份。
            self.assertEqual(b._expansion_guards(world(extra=[mine, guard]), mine), [])

    def test_expansion_requires_foundation_and_rebuilds_after_its_loss(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d, None)
            run = OP.OpeningRun(OP.parse(dict(id="t", steps=[
                dict(kind="expand", code="htow", qty=2),
                dict(kind="building", code="hwtw", where="expansion")], keep={})))
            b.pending = [dict(kind="expand", code="htow")]
            run.wants(b, world(t=210), [], [])
            self.assertEqual(run.idx, 0)  # 发令还不算建出来。
            b.pending = []
            foundation = U(0x702,"htow",-4352,-2624)
            foundation.hp = 155
            run.wants(b,world(t=240,extra=[foundation]),[],[])
            self.assertEqual(run.idx,1)
            out=run.wants(b,world(t=244),[],[])
            self.assertEqual([(x.kind,x.qty) for x in out],[("expand",2)])

    def test_fe_builds_barracks_before_altar(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b.job_build(world())
            self.assertEqual(built(b)[:1], ["hbar"])            # 建造表是祭坛（96）先
            self.assertEqual(b.opening_run.op.id, FE)
            self.assertTrue(any("开局谱：Yumiko 速开矿" in n for _t, n in b.notes))

    def test_default_is_the_table(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d, None)
            b.job_build(world())
            self.assertIsNone(b.opening_run)
            self.assertEqual(built(b)[:1], ["halt"])

    def test_switch_off_mid_game(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b.job_build(world())
            with open(os.path.join(d, "brain_config.json"), "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            b.config.checked = -1e9
            b.config.mtime = None
            b.config.refresh()
            b.job_build(world(t=10.0))
            self.assertIsNone(b.opening_run)
            self.assertTrue(any("中途切走" in n for _t, n in b.notes))

    def test_wrong_race_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            b = brain(d)
            b.set_race("orc", 0.0)
            b._opening_wants(world(), [])
            self.assertIsNone(b.opening_run)
            self.assertEqual(b.counts["开局谱·种族不符"], 1)


class Tuner(unittest.TestCase):
    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_tuner_keeps_the_opening_and_tune_meta_ignores_it(self):
        root = os.path.dirname(HERE)
        tuner = self.load("tuner_for_test", os.path.join(root, "tuner.py"))
        rb = self.load("run_brain_for_test", os.path.join(root, "run_brain.py"))
        with tempfile.TemporaryDirectory() as d:
            tuner.BIN = d
            os.makedirs(os.path.join(d, "inst9"))
            with open(os.path.join(d, "inst9", "brain_config.json"), "w", encoding="utf-8") as fh:
                json.dump({"opening": FE, "tier2_army": 3}, fh)
            tuner.write_config(9, {"tier2_army": 6}, {"study": "s99", "trial": 1, "tag": "撒点"})
            with open(os.path.join(d, "inst9", "brain_config.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"tier2_army": 6, "opening": FE})
            meta = rb.tune_meta(os.path.join(d, "inst9"), {"tier2_army": 6, "opening": FE})
            self.assertEqual(meta.get("study"), "s99")


if __name__ == "__main__":
    unittest.main()

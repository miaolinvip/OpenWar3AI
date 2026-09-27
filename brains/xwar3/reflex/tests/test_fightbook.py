# -*- coding: utf-8 -*-
"""战斗账本口径（battles.curve / record / line）和 mod_fight.FightBook 的实时/结算时机 —— 合成帧钉住。"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import battles  # noqa: E402
import mod_fight  # noqa: E402

ME, FOE = 0, 1


def u(addr, typ, owner, x, y, hp=400):
    return [addr, typ, owner, x, y, hp, 0, -1, -1, 400, 0, 0]


def fight(n_sec=12, step=0.5, foe_dps_each=40.0, my_dps_each=20.0, walk_away_from=None):
    """我方 4 个步兵在 (0,0) 一排，敌方 4 个步兵在 (0,300)。每人每秒按给定速度掉血（平摊到每个单位）。"""
    frames = []
    hp_m = {10 + i: 400.0 for i in range(4)}
    hp_f = {20 + i: 400.0 for i in range(4)}
    t = 0.0
    while t <= n_sec + 1e-9:
        units = [u(1, "htow", ME, -3000, 0, 1500)]
        for i, a in enumerate(sorted(hp_m)):
            y = 0.0
            if walk_away_from is not None and t >= walk_away_from and i < 2:
                y = -60.0 * (t - walk_away_from)
            if hp_m[a] > 0:
                units.append(u(a, "hfoo", ME, i * 40, y, int(hp_m[a])))
        for i, a in enumerate(sorted(hp_f)):
            if hp_f[a] > 0:
                units.append(u(a, "hfoo", FOE, i * 40, 300, int(hp_f[a])))
        frames.append((round(t, 2), units))
        for a in hp_m:
            hp_m[a] = max(0.0, hp_m[a] - foe_dps_each * step)
        for a in hp_f:
            hp_f[a] = max(0.0, hp_f[a] - my_dps_each * step)
        t += step
    return frames


class Curve(unittest.TestCase):
    def test_per_unit_rates(self):
        fr = fight(n_sec=6)
        lo, hi = battles.segment(fr, ME)[0]
        c = battles.curve(fr, lo, hi, ME, tail=0)
        # 敌方每人每秒掉 20 -> 我方 4 人在接触，单兵每秒打出 20；我方每人每秒掉 40 -> 敌方单兵 40
        self.assertAlmostEqual(c["rateM"], 20.0, delta=0.5)
        self.assertAlmostEqual(c["rateF"], 40.0, delta=0.5)
        self.assertEqual(c["dealt"], 4 * 20 * 6)
        self.assertEqual(c["taken"], 4 * 40 * 6)
        self.assertEqual((c["unitSecsM"], c["unitSecsF"]), (24.0, 24.0))   # 4 人 × 6 秒

    def test_dead_unit_counts_its_last_hp(self):
        # 我方每人每秒掉 100：4 秒死光（最后一帧消失，按上一帧血量算进挨打）
        fr = fight(n_sec=6, foe_dps_each=100.0)
        lo, hi = battles.segment(fr, ME)[0]
        c = battles.curve(fr, lo, len(fr) - 1, ME, tail=0)
        self.assertEqual(c["taken"], 4 * 400)

    def test_walking_away_is_counted(self):
        fr = fight(n_sec=6, walk_away_from=2.0)
        lo, hi = battles.segment(fr, ME)[0]
        c = battles.curve(fr, lo, hi, ME, tail=0)
        # 2 秒之后 2/4 个人每帧往外走 30：背离帧 = 2 人 × 8 个间隔（2.5~6 秒）/ 总 4 人 × 12 个间隔
        self.assertAlmostEqual(c["awayShare"], (2 * 8) / (4 * 12), places=3)

    def test_collapse_detected(self):
        # 前 4 秒敌方每秒掉 4×50=200（基线 200），之后降到每秒 40 而我方人还在 -> 第 5 秒塌陷
        frames = []
        hp_f = 5000.0
        for k in range(0, 25):
            t = k * 0.5
            units = [u(1, "htow", ME, -3000, 0, 1500)]
            units += [u(10 + i, "hfoo", ME, i * 40, 0, 400) for i in range(4)]
            units.append(u(20, "hkni", FOE, 0, 300, int(hp_f)))
            frames.append((t, units))
            hp_f -= (200.0 if t < 4.5 else 40.0) * 0.5
        lo, hi = battles.segment(frames, ME)[0]
        c = battles.curve(frames, lo, hi, ME, tail=0)
        self.assertEqual(c["collapseAt"], 5)

    def test_record_line_and_pull_survival(self):
        fr = fight(n_sec=6, foe_dps_each=100.0)
        lo, hi = battles.segment(fr, ME)[0]
        wall0 = datetime(2026, 9, 16, 17, 16, 0)
        rows = [(datetime(2026, 9, 16, 17, 16, 2), "hfoo@A 200/420 正在挨 100 血/秒（1.5 秒窗口），2.5 秒后只剩 -50 —— 拉出来")]
        rec = battles.record(fr, lo, hi, ME, wall0, rows)
        json.dumps(rec, ensure_ascii=False)
        self.assertEqual(rec["pulls"], {"n": 1, "alive": 0, "hpShareMax": 0.48})
        text = battles.line(rec)
        self.assertIn("单兵每秒", text)
        self.assertIn("被拉出来 1 活 0", text)

    def test_line_shows_participants_and_summons(self):
        """09-16 接触峰值 14:15、战损却是 19:9，战损 19 的来源对不上 —— 真实记录（inst1 19:13:31 那场）钉住。
        compM 合计 2+5+4+1+2+2+2+1+1 = 20，compF 2+8+1+4+2 = 17；lostM 19 里 hwt2（水元素）2 个。"""
        rec = {"start": "2026-09-16T19:13:31", "end": "2026-09-16T19:13:57", "where": "野外", "peak": [14, 15],
               "lostM": {"hgry": 2, "hrif": 5, "hfoo": 4, "hmpr": 2, "hsor": 2, "hwt2": 2, "hkni": 1, "Hmkg": 1},
               "lostF": {"hfoo": 5, "hrif": 3, "hkni": 1},
               "compM": {"hgry": 2, "hrif": 5, "hfoo": 4, "Hamg": 1, "hmpr": 2, "hsor": 2, "hwt2": 2, "hkni": 1, "Hmkg": 1},
               "compF": {"hmtm": 2, "hfoo": 8, "Hmkg": 1, "hrif": 4, "hkni": 2},
               "heroes": ["Hamg", "Hmkg"], "heroesDead": ["Hmkg"],
               "orders": {"有任务无订单": 665, "真空闲": 18, "攻击": 421, "其他": 99, "smart": 188},
               "pulls": {"n": 1, "alive": 0, "hpShareMax": 0.26}, "dur": 25.4, "dealt": 4237, "taken": 9739,
               "rateM": 20.2, "rateF": 39.7, "unitSecsM": 210.0, "unitSecsF": 245.5, "awayShare": 0.168,
               "collapseAt": None, "baseDealt": 102}
        text = battles.line(rec)
        self.assertIn("19:13:31 野外 25s 参战 我 20:敌 17 接触峰值 我 14:敌 15 战损 19:9（我方含召唤物 2）｜", text)
        self.assertIn("hwt2", battles.summons())
        self.assertNotIn("hfoo", battles.summons())
        self.assertNotIn("Hamg", battles.summons())
        # 老记录没有 compM：不写参战，格式照旧
        old = dict(rec, compM={}, compF={}, lostM={"hfoo": 1})
        self.assertIn("25s 接触峰值 我 14:敌 15 战损 1:9｜", battles.line(old))


class FightBookTiming(unittest.TestCase):
    def test_live_line_every_live_secs_and_ledger_after_gap(self):
        d = tempfile.mkdtemp()
        book = mod_fight.FightBook(d)
        book.wall0 = datetime(2026, 9, 16, 17, 0, 0)
        fr = fight(n_sec=12, step=0.15)
        lives = []
        for t, rows in fr:
            book.feed(t, rows, True, ME)
            m = book.live_line(t, ME)
            if m:
                lives.append((t, m))
        self.assertEqual(len(lives), 2)                       # 第 5 秒、第 10 秒
        self.assertIn("单兵每秒", lives[0][1])
        self.assertEqual(book.maybe_close(12.0 + battles.GAP, ME), [])     # 还没过 GAP + TAIL
        msgs = book.maybe_close(12.0 + battles.GAP + battles.TAIL + 0.2, ME)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("战斗账本："))
        with open(os.path.join(d, "battles.jsonl"), encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["peak"], [4, 4])
        self.assertEqual(book.frames, [])

    def test_creep_only_fight_writes_nothing(self):
        d = tempfile.mkdtemp()
        book = mod_fight.FightBook(d)
        book.wall0 = datetime(2026, 9, 16, 17, 0, 0)
        rows = [u(10, "hfoo", ME, 0, 0), u(30, "nfsh", 12, 0, 300)]
        for k in range(20):
            book.feed(k * 0.15, rows, True, ME)
        self.assertEqual(book.close(ME), [])
        self.assertFalse(os.path.exists(os.path.join(d, "battles.jsonl")))

    def test_creeping_after_player_fight_still_closes_and_stays_quiet(self):
        """09-16 上线第一局：打野时刷「接触 我 0:敌 0｜挨打 1536」、缓冲从开局攒着不结算。"""
        d = tempfile.mkdtemp()
        book = mod_fight.FightBook(d)
        book.wall0 = datetime(2026, 9, 16, 17, 0, 0)
        fr = fight(n_sec=6, step=0.15)
        lives, closed = [], []
        for t, rows in fr:
            book.feed(t, rows, True, ME)
            m = book.live_line(t, ME)
            if m:
                lives.append((t, m))
        last_player = fr[-1][0]
        creep_rows = [u(10, "hfoo", ME, 0, 0), u(30, "nfsh", 12, 0, 300)]
        t = round(last_player + 0.15, 2)
        while t < last_player + battles.GAP + battles.TAIL + 3.0:
            book.feed(t, creep_rows, True, ME)          # 录帧的 hot 含野怪 —— 账本不该当成交战
            m = book.live_line(t, ME)
            if m:
                lives.append((t, m))
            closed += book.maybe_close(t, ME)
            t = round(t + 0.15, 2)
        # 玩家接触结束 1 秒之后（只剩打野）一行实时统计都不该有
        self.assertEqual([x for x in lives if x[0] > last_player + 1.0], [])
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0].startswith("战斗账本："))

    def test_creep_only_buffer_is_trimmed(self):
        d = tempfile.mkdtemp()
        book = mod_fight.FightBook(d)
        book.wall0 = datetime(2026, 9, 16, 17, 0, 0)
        creep_rows = [u(10, "hfoo", ME, 0, 0), u(30, "nfsh", 12, 0, 300)]
        for k in range(400):                             # 60 秒一直在打野
            book.feed(round(k * 0.15, 2), creep_rows, True, ME)
        self.assertLessEqual(len(book.frames), int(mod_fight.FightBook.PREROLL / 0.15) + 2)
        self.assertIsNone(book.live_line(59.85, ME))


if __name__ == "__main__":
    unittest.main()

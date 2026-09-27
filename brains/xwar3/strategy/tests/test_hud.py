# -*- coding: utf-8 -*-
"""游戏内按钮栏 + 任务面板日志（09-18 加的，方便标记对局问题、点开看日志）。09-23 起走 W3P 的 hud_* 命令。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import hud as H  # noqa: E402

import w3fast as F  # noqa: E402

OPNAME = {v: k for k, v in F.OP.items()}


class FakeLane:
    """记下每条 HUD 命令：(op, extra, mode, 文字)。in_game=False 时运行时找不到按钮栏 -> rejected。"""

    def __init__(self):
        self.sent = []
        self.in_game = True
        self.dialog = 0

    def commands(self, calls):
        out = []
        for c in calls:
            text = c.scratch[F.TEXT_OFF:].split(b"\0", 1)[0].decode("utf-8") if len(c.scratch) > F.TEXT_OFF else None
            op = OPNAME[c.op]
            self.sent.append((op, c.args[9], c.args[10], text))
            vals = (self.dialog, 0, 0, 0, 0, 0, 0, 0) if op == "hud_user_dialog" else (0,) * 8
            out.append(F.Receipt(c.op, 0 if self.in_game else 1, 1, None, 0, 0, vals))
        return out

    def buttons(self, which):
        return [t for op, extra, _m, t in self.sent if op == "hud_button" and extra == H.BUTTON[which]]

    def enables(self):
        return [(extra, m) for op, extra, m, _t in self.sent if op == "hud_button" and m & 1]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Buttons(unittest.TestCase):
    def test_clock_text(self):
        self.assertEqual([H.clock_text(s) for s in (0, 59.9, 61, 3600, -3)], ["00:00", "00:59", "01:01", "60:00", "00:00"])

    def test_time_every_second_label_every_three(self):
        # 09-19：任务按钮强制重写 + 盟友按钮启用 3 秒一次（LABEL_EVERY）；时间照旧每秒变（CLOCK_EVERY）
        lane, clk = FakeLane(), Clock()
        hud = H.Hud(lane, clock=clk)
        hud.update("War3AI-5-021", 61.2)                # t=100：第一次，全写
        clk.t += 0.3
        hud.update("War3AI-5-021", 61.8)                # 同一秒、不到 1 秒：都不重写
        clk.t += 0.8
        hud.update("War3AI-5-021", 62.1)                # 时间变了 -> 只写时间；标签没到 3 秒不重写
        clk.t += 2.1
        hud.update("War3AI-5-021", 62.9)                # 过了 3 秒 -> 标签重写、时间按钮也强制写一遍
        self.assertEqual(lane.buttons("quests"), ["War3AI-5-021"] * 2)
        self.assertEqual(lane.buttons("allies"), ["01:01", "01:02", "01:02"])
        self.assertEqual(lane.enables(), [(1, 1), (1, 1)])            # 盟友按钮 3 秒启用一次（可点样式）

    def test_not_in_game_not_marked_shown(self):
        lane = FakeLane()
        lane.in_game = False                        # 菜单里 / 还没进局
        hud = H.Hud(lane)
        self.assertFalse(hud.set("quests", "x"))
        self.assertNotIn("quests", hud.shown)
        self.assertEqual(hud.errors, 1)


class Quests(unittest.TestCase):
    def test_created_once_and_desc_only_when_changed(self):
        lane, clk = FakeLane(), Clock()
        hud = H.Hud(lane, clock=clk)
        panels = [("log", "对局日志", "01:00 打野选营地"), ("status", "当前状态", "强度 10 对 12")]
        hud.update("L", 60, panels)
        q = [s for s in lane.sent if s[0] == "hud_quest"]
        self.assertEqual([(e, m) for _o, e, m, _t in q], [(0, 0), (0, 1), (0, 3), (1, 0), (1, 1), (1, 3)])
        self.assertEqual([t for _o, _e, m, t in q if m == 0], ["对局日志", "当前状态"])
        self.assertEqual(sum(1 for s in lane.sent if s[0] == "hud_quest_refresh"), 1)
        # 刷新任务后要重写任务按钮（引擎改回「任务 (F9)」）；在刷新之后、同一轮只写一次
        up = next(i for i, s in enumerate(lane.sent) if s[0] == "hud_quest_refresh")
        self.assertEqual([s[3] for s in lane.sent[up:] if s[0] == "hud_button" and s[1] == 0], ["L"])
        self.assertEqual(lane.buttons("quests"), ["L"])
        n = len(lane.sent)
        clk.t += 5
        hud.update("L", 65, panels)                          # 内容没变：不重写、不刷新
        self.assertFalse([s for s in lane.sent[n:] if s[0] in ("hud_quest", "hud_quest_refresh")])
        clk.t += 5
        hud.update("L", 70, [("log", "对局日志", "01:00 打野选营地\n01:10 营地清空"), panels[1]])
        writes = [s for s in lane.sent[n:] if s[0] == "hud_quest"]
        self.assertEqual({s[1] for s in writes}, {0})       # 只重写变了的那一项
        self.assertEqual(writes[0][3], "01:00 打野选营地\n01:10 营地清空")

    def test_long_description_goes_in_chunks(self):
        lane = FakeLane()
        hud = H.Hud(lane)
        text = "\n".join(f"{i:03d} 第 {i} 条决策" for i in range(120))
        hud.quest("log", "对局日志", text)
        parts = [s for s in lane.sent if s[0] == "hud_quest" and s[2] in (1, 2)]
        self.assertGreater(len(parts), 1)
        self.assertEqual([s[2] for s in parts], [1] + [2] * (len(parts) - 1))
        self.assertEqual("".join(s[3] for s in parts), H.clip_utf8(text))
        self.assertEqual(lane.sent[-1][:3], ("hud_quest", 0, 3))

    def test_clip_keeps_latest_whole_lines(self):
        text = "\n".join(f"{i:03d} 第 {i} 条" for i in range(400))
        out = H.clip_utf8(text, 200)
        self.assertLessEqual(len(out.encode("utf-8")), 200)
        self.assertTrue(out.endswith("399 第 399 条"))
        self.assertTrue(out.split("\n")[0][:3].isdigit())      # 从一整行开始

    def test_paused_by_user(self):
        lane, clk = FakeLane(), Clock()
        hud = H.Hud(lane, clock=clk)
        self.assertFalse(hud.paused_by_user())
        lane.dialog = 1                                       # 人打开了任务面板（运行时判：任务/盟友/记录面板）
        self.assertTrue(hud.paused_by_user())
        hud.update("L", 1)
        self.assertTrue(hud.user_paused)


class Content(unittest.TestCase):
    def test_log_keeps_decisions_drops_execution_noise(self):
        keep = ["打野选营地：强度 5，战力比 3.2，走 12 + 等兵 0 + 打 9 秒", "营地清空（强度 11）",
                "建筑没了：hvlt @ (-4096,-2112) 血 18/485", "复活 Hamg（3 级，估价 255 金）：接下"]
        drop = ["打野开打（营地强度 5，到场战力比 2.2）：3/3 个 -> (-5662,783)", "train hpea（unit，优先级 85）",
                "build hhou @ (-4416,2368) 工人 0x14692924", "英雄看戏：Hamg 450/450 没订单没任务", ""]
        self.assertEqual([t for t in keep + drop if H.log_worthy(t)], keep)

    def test_join_lines_empty(self):
        self.assertEqual(H.join_lines([]), "（还没有记录）")
        self.assertEqual(H.join_lines(["a", "b"]), "a\nb")

    def test_status_text(self):
        from types import SimpleNamespace as S
        w = S(own_strength=19.4, foe_strength=26.8, aggression=3.0, gold=407, lumber=638, food_used=33, food_cap=48,
              my_workers=[1] * 14, my_army=[1] * 9, my_heroes=[1, 2], my_halls=[1])
        txt = H.status_text(w, "creep", "none", 349.0, {"出击·目标不好": 4, "打野·选营地": 1})
        self.assertIn("游戏时间 05:49", txt)
        self.assertIn("强度 我方 19.4 / 敌方 26.8", txt)
        self.assertIn("出击·目标不好×4", txt)


if __name__ == "__main__":
    unittest.main()

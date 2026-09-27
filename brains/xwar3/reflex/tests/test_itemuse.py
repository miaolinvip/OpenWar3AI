# -*- coding: utf-8 -*-
"""itemuse：买什么 / 喝药 / 恢复卷轴 / 回城卷轴的判定。期望按函数手算，不照代码现状写。"""
import os
import sys
import unittest
from types import SimpleNamespace as U

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import itemuse as I  # noqa: E402


def unit(x, y, hp=400, hp_max=400, typ="hfoo", owner=0):
    return U(x=float(x), y=float(y), hp=hp, hp_max=hp_max, type=typ, owner=owner)


class Shop(unittest.TestCase):
    def test_tier_one_and_army_recovery_prioritize_available_scroll(self):
        self.assertEqual(I.shop_wish([None]*6,1000,tier=1),"sreg")
        self.assertEqual(I.shop_wish([None]*6,1000,tier=2,recovery=True),"sreg")
        self.assertEqual(I.shop_wish([None]*6,1000,tier=2),"phea")

    def test_potion_first_then_regen(self):
        self.assertEqual(I.shop_wish([None] * 6, 1000), "phea")
        self.assertEqual(I.shop_wish(["phea"] + [None] * 5, 1000), "sreg")
        self.assertEqual(I.shop_wish(["phea", "sreg", None, None, None, None], 5000), "sreg")   # 09-22 晚：卷轴备两张
        self.assertIsNone(I.shop_wish(["phea", "sreg", "sreg", None, None, None], 5000))

    def test_floor_counts_after_price(self):
        # 药的门槛 450：600 - 150 = 450 >= 450 买药；599 - 150 = 449 不买药，买卷轴。
        # ★ 09-21 卷轴走急救线 REGEN_BUY_FLOOR 150（卷轴便宜，不卡在常备补货线上）：250 - 100 = 150 买；249 不买
        self.assertEqual((I.BUY_GOLD_FLOOR, I.REGEN_BUY_FLOOR), (450, 150))
        self.assertEqual(I.shop_wish([None] * 6, 600), "phea")
        self.assertEqual(I.shop_wish([None] * 6, 599), "sreg")
        self.assertEqual(I.shop_wish([None] * 6, 250), "sreg")
        self.assertIsNone(I.shop_wish([None] * 6, 249))
        self.assertEqual(I.shop_wish(["sreg"] + [None] * 5, 549), "sreg")  # 09-22 晚：一张不够，再备第二张（药还买不起）
        self.assertIsNone(I.shop_wish(["sreg", "sreg"] + [None] * 4, 549))  # 两张齐了、药还买不起

    def test_caster_heroes_also_stock_a_mana_potion_from_tier_two(self):
        """09-21 施法英雄蓝不够时直接买魔法药水补。AMAI 数据：pman 种族商店、要二本、200 金。"""
        full = ["phea", "sreg", "sreg", None, None, None]                  # 卷轴两张齐了（09-22 晚 SREG_CARRY）
        self.assertEqual(I.shop_wish(full, 1000, want=I.shop_want("Hblm"), tier=2), "pman")
        self.assertEqual(I.shop_wish(full, 1000, want=I.shop_want("Hamg"), tier=2), "pman")
        self.assertIsNone(I.shop_wish(full, 1000, want=I.shop_want("Hblm"), tier=1))       # 一本不卖
        self.assertEqual(I.shop_wish(full, 1000, want=I.shop_want("Hmkg"), tier=2), "stwp")  # 山丘不备魔法药水，只补回城（09-23）
        self.assertIsNone(I.shop_wish(full[:3] + ["stwp", None, None], 1000, want=I.shop_want("Hmkg"), tier=2))
        self.assertIsNone(I.shop_wish(full, 649, want=I.shop_want("Hblm"), tier=2))        # 649 - 200 < 450

    def test_mana_potion_only_in_a_fight_when_low(self):
        bm = unit(0, 0, typ="Hblm")
        self.assertTrue(I.mana_potion_due(bm, 80.0, foes_near=2))
        self.assertFalse(I.mana_potion_due(bm, 80.0, foes_near=0))       # 没打仗不喝，回家自己回
        self.assertFalse(I.mana_potion_due(bm, 300.0, foes_near=2))
        self.assertFalse(I.mana_potion_due(bm, None, foes_near=2))       # 读不到蓝不喝
        self.assertFalse(I.mana_potion_due(unit(0, 0, typ="Hmkg"), 10.0, foes_near=2))

    def test_full_or_unknown_bag(self):
        self.assertIsNone(I.shop_wish(["stwp", "rde1", "cnob", "tint", "bspd", "dust"], 5000))
        self.assertIsNone(I.shop_wish(None, 5000))
        self.assertIsNone(I.shop_wish([None] * 6, None))


class Potion(unittest.TestCase):
    def test_low_hp_with_enemy(self):
        self.assertTrue(I.potion_due(unit(0, 0, 300, 700), foes_near=2))        # 42.9%
        self.assertFalse(I.potion_due(unit(0, 0, 320, 700), foes_near=2))       # 45.7%
        self.assertFalse(I.potion_due(unit(0, 0, 100, 700), foes_near=0))       # 没敌人不喝

    def test_focused_relaxes_to_70(self):
        h = unit(0, 0, 480, 700)                                               # 68.6%
        self.assertTrue(I.potion_due(h, foes_near=5, focused=3))
        self.assertFalse(I.potion_due(h, foes_near=5, focused=2))


class Regen(unittest.TestCase):
    def test_worth_when_many_hurt_and_no_enemy(self):
        holder = unit(0, 0, typ="Hamg")
        allies = [unit(75 * i, 0, 100, 420) for i in range(1, 7)]            # 6 人各缺 320 = 1920
        ok, hurt, miss = I.regen_worth(holder, allies, foes=[])
        self.assertEqual((ok, hurt, miss), (True, 6, 1920.0))

    def test_not_with_enemy_close_or_mechanical_or_far(self):
        """敌人在附近不放；机械单位和范围外的人不算进伤兵数。

        ⚠ 09-21：伤兵门槛从 6 降到 3（见 itemuse.REGEN_MIN_HURT 的算账），所以这里
        **不能**再断言「5 个不够」。要守的是「谁算数」，那就只断言算出来的人数。"""
        holder = unit(0, 0, typ="Hamg")
        allies = [unit(75 * i, 0, 100, 420) for i in range(1, 7)]
        self.assertFalse(I.regen_worth(holder, allies, foes=[unit(800, 0, owner=1)])[0])
        mech = allies[:5] + [unit(100, 50, 100, 500, typ="hgyr")]             # 飞行器不算
        self.assertEqual(I.regen_worth(holder, mech, foes=[])[1], 5)
        far = allies[:5] + [unit(700, 0, 100, 420)]                            # 700 在 600 外
        self.assertEqual(I.regen_worth(holder, far, foes=[])[1], 5)

    def test_the_wounded_count_gate_sits_exactly_at_the_constant(self):
        """门槛就在 REGEN_MIN_HURT 上：差一个不放、刚好够就放。

        ⚠ 场景按常量**算**出来，不写死 —— 09-21 门槛从 6 改到 3 时，原来那条
        「5 个 + 1 个机械不够」的测试整个失去意义（期望值要按公式算：测试挂的是期望，不是代码现状）。"""
        holder = unit(0, 0, typ="Hamg")
        def scene(n):
            # 每人缺 320 血，凑够 REGEN_MIN_MISSING 不成问题；只让人数成为唯一变量
            return [unit(75 * i, 0, 100, 420) for i in range(1, n + 1)]
        n = I.REGEN_MIN_HURT
        self.assertFalse(I.regen_worth(holder, scene(n - 1) + [unit(200, 0, 100, 500, typ="hgyr")], [])[0],
                         "差一个（机械的不算）不该放")
        self.assertTrue(I.regen_worth(holder, scene(n), [])[0], "刚好够就该放")

    def test_small_scratches_not_counted(self):
        holder = unit(0, 0, typ="Hamg")
        allies = [unit(100, 0, 380, 420)] * 10                                # 各缺 40 < 60
        self.assertEqual(I.regen_worth(holder, allies, foes=[])[:2], (False, 0))

    def test_formation_waits_for_all_injured_healthy_in_the_circle_do_not_block(self):
        holder = unit(0, 0, typ="Hamg")
        hurt = [unit(75 * i, 0, 100, 420) for i in range(1, 7)]
        self.assertTrue(I.regen_formation_ready(holder, hurt, []))
        hurt[-1].x = 550
        self.assertFalse(I.regen_formation_ready(holder, hurt, []))
        hurt[-1].x = 300
        # ★ 09-22（1-301）：原来满血的站在圈里就不放。卷轴没有人数上限，满血的不挡任何人。
        healthy = unit(200, 100, 420, 420)
        self.assertTrue(I.regen_formation_ready(holder, hurt + [healthy], []))


class ArmyWith(unittest.TestCase):
    """09-17：英雄正带着部队时不专门走回藏宝室买药（mod_scavenge 走去藏宝室那段）。"""
    food = {"hfoo": 2.0, "hpea": 1.0, "hbar": 0.0, "Hamg": 5.0, "hwat": 0.0}

    def test_counts_only_army_within_r(self):
        hero = unit(0, 0, typ="Hamg")
        mine = [hero, unit(100, 0), unit(0, 900), unit(1199, 0), unit(1300, 0),          # 3 个步兵在 1200 内，1 个在外
                unit(50, 0, typ="hpea"), unit(60, 0, typ="hbar"), unit(70, 0, typ="Hmkg"),  # 工人 / 建筑 / 另一个英雄不算
                unit(80, 0, hp=0)]                                                         # 死的不算
        self.assertEqual(I.army_with(hero, mine, self.food), 3)
        self.assertGreaterEqual(I.army_with(hero, mine, self.food), I.WALK_ARMY_MAX)

    def test_unknown_types_and_empty_table(self):
        hero = unit(0, 0, typ="Hamg")
        self.assertEqual(I.army_with(hero, [hero, unit(10, 0, typ="hwat")], self.food), 0)   # 召唤物 food 0：不算
        self.assertEqual(I.army_with(hero, [hero, unit(10, 0)], {}), 0)                     # 表读不到：数 0（照旧能去买）

    def test_load_food_real_table(self):
        food = I.load_food()
        self.assertGreater(food.get("hfoo", 0), 0)
        self.assertEqual(food.get("hbar", -1), 0)
        self.assertEqual(I.load_food(r"Z:\no\such\units.json"), {})


class TownPortal(unittest.TestCase):
    hall = unit(-5184, 2944, 1500, 1500, typ="htow")

    def test_far_and_home_attacked(self):
        self.assertTrue(I.town_portal_due(unit(0, -3000), self.hall, home_foes=6, self_foes=0))

    def test_not_when_close_or_fighting_or_few(self):
        self.assertFalse(I.town_portal_due(unit(-2000, 1000), self.hall, 6, 0))   # 离家 3740 < 4500
        self.assertFalse(I.town_portal_due(unit(0, -3000), self.hall, 6, 2))       # 自己身边有敌人
        self.assertFalse(I.town_portal_due(unit(0, -3000), self.hall, 4, 0))       # 家里只有 4 个
        self.assertFalse(I.town_portal_due(unit(0, -3000), None, 6, 0))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""整队恢复 / 家里补给：挑谁去买、买家走远了还认不认、背包读多少次（09-21 1-284）。

一堆单位等恢复，大脑已下了恢复要求，血魔法师和大法师却都没执行，卡了很久；
直到 15 分左右那些残血单位仍停在家里，没有任何动作。
查下来三件事叠在一起：
  ① 买家随机挑 —— 抽中家里一个步兵走 5600 去分矿的藏宝室，两个英雄就在藏宝室 2000 外却没被选；
  ② 买家一走出「家里 2000」这一圈，家里补给就找不到它，当场「物品持有者不可用」作废（两次都是）；
  ③ 空闲时每一拍把十几个人的背包重读一遍，一拍 1.0~2.6 秒，大脑的每条战斗令跟着晚 1.2~2.2 秒。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "war3-modules"))
from brain import tactics as T              # noqa: E402
from brain.recovery import ArmyRecovery, HOLDER_MISSING_S, INV_NONE_TTL  # noqa: E402
from brain.runtime import Brain             # noqa: E402
from test_tactics import U, world           # noqa: E402
from test_runtime_smoke import FakeCmd      # noqa: E402


class Bags:
    """背包表：{地址: 六格}；不在表里 = 没背包（inventory 返回 None）。记每个地址被读了几次。"""
    USESLOT1 = 0xD0028

    def __init__(self, bags):
        self.bags = {a: list(b) for a, b in bags.items()}
        self.reads = []
        self.buys = []
        self.active = None

    def inventory(self, a):
        self.reads.append(a)
        self.active = a
        return list(self.bags[a]) if a in self.bags else None

    def buy_item(self, shop, code):
        self.buys.append(code)
        bag = self.bags[self.active]
        bag[bag.index(None)] = "sreg"
        return True

    def use_item(self, a, order):
        return True


def field(hero_x, foot_x):
    """藏宝室在 0；英雄 Hblm 在 hero_x；有背包的步兵 #20 在 foot_x；6 个伤兵在 600 外。"""
    wounded = [U(10 + i, "hfoo", x=600 + 30 * i, y=300, hp=150, hp_max=420) for i in range(6)]
    w = world([U(1, "htow", x=-800), U(2, "hvlt", x=0), U(3, "Hblm", x=hero_x, y=0),
               U(20, "hfoo", x=foot_x, y=-200)] + wounded, t=300)
    w.gold = 1000
    return w


class Buyer(unittest.TestCase):
    def test_only_heroes_buy_and_the_nearest_hero_goes(self):
        """★ 09-22：兵的背包 Aihn「能使用物品 = 0」—— 有背包的步兵就站在店门口也不当买家。两个英雄里离店近的去。"""
        r = ArmyRecovery()
        c = Bags({3: [None] * 6, 20: [None] * 6})
        r.step(field(2200, 200), c, None)
        self.assertEqual(r.holder_id[0], 3)
        self.assertNotIn(20, c.reads, "兵的背包不用读")
        w = field(2200, 200)
        w2 = world(list(w.units) + [U(4, "Hamg", x=900, y=0)], t=300)
        w2.gold = 1000
        r = ArmyRecovery()
        r.step(w2, Bags({3: [None] * 6, 4: [None] * 6}), None)
        self.assertEqual(r.holder_id[0], 4)

    def test_same_scene_same_buyer(self):
        """不再随机：同一个局面挑同一个人（复盘时才对得上）。"""
        picks = set()
        for _ in range(20):
            r = ArmyRecovery()
            r.step(field(1800, 1700), Bags({3: [None] * 6, 20: [None] * 6}), None)
            picks.add(r.holder_id[0])
        self.assertEqual(picks, {3})


class ScrollMoneyIsReserved(unittest.TestCase):
    """1-297 14 分：一堆少血单位等恢复卷轴，却一直没有购买和使用，疑似造兵一直占用经济。
    这局金子 86/330/63/152：原来要求拍首 >= 150 + 100 才买、不够冷却 15 秒，钱一进来就被出兵花掉。"""

    def test_short_of_money_reserves_the_price_and_retries_soon(self):
        import itemuse as I
        w = field(150, 5000)                                # 英雄就在藏宝室边（< itemuse.SHOP_REACH 250）
        w.gold = 60
        r = ArmyRecovery()
        self.assertIsNone(r.step(w, Bags({3: [None] * 6}), None))
        self.assertEqual(r.reserve, I.PRICE["sreg"])
        self.assertEqual(r.counts["整队恢复·攒卷轴钱（生产让路）"], 1)
        self.assertLessEqual(r.next_try - w.t, 3)
        # 攒够卷轴价就买（不再要求买完还剩 150）；走去藏宝室的路上继续占着这笔钱
        w.t += 3
        w.gold = I.PRICE["sreg"] + 5
        c = Bags({3: [None] * 6})
        r.step(w, c, None)
        self.assertEqual(r.phase, "buying", "英雄就在店边（150）：到了就买")
        self.assertEqual(r.reserve, I.PRICE["sreg"])
        w.t += 3
        r.step(w, c, None)                                  # 卷轴进背包
        self.assertEqual(r.reserve, 0)

    def test_nothing_to_buy_reserves_nothing(self):
        w = field(300, 5000)
        w.gold = 60
        for u in w.my_army:
            u.hp = u.hp_max                                 # 没伤兵
        r = ArmyRecovery()
        r.step(w, Bags({3: [None] * 6}), None)
        self.assertEqual(r.reserve, 0)

    def test_brain_budget_subtracts_the_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            b = Brain(FakeCmd(), None, d)
            g0, _ = b.committed()
            b.recovery.reserve = 100
            b.home_recovery.reserve = 100
            self.assertEqual(b.committed()[0], g0 + 100, "两份恢复同时要也只扣一份")


class BuyOnlyWhenReallyAtTheVault(unittest.TestCase):
    def test_hero_300_away_walks_closer_before_buying(self):
        """藏宝室交互距离 Aall Rng1 = 350；捡装备模块 <= 450 就买，卷轴一半被拒（530/562），大脑先走到 +80 再买 83% 成。
        itemuse.SHOP_REACH 250：300 外先走过去（持有者先采购），不下单。"""
        import itemuse as I
        self.assertLess(I.SHOP_REACH, 350)
        w = field(300, 5000)
        c = Bags({3: [None] * 6})
        r = ArmyRecovery()
        out = r.step(w, c, None)
        self.assertEqual(r.phase, "shopping")
        self.assertEqual(c.buys, [])
        self.assertTrue(any(it.why == "整队恢复·持有者先采购" for it in out))


class ShopDistance(unittest.TestCase):
    def test_idle_army_lets_the_hero_walk_further_to_the_vault(self):
        """1-291 14 分：部队在 2 矿闲着，藏宝室在主基地（5600）—— 5000 的上限让英雄不去买，伤兵只能等牧师。"""
        from brain.recovery import SHOP_MAX, SHOP_MAX_IDLE
        wounded = [U(10 + i, "hfoo", x=5600 + 30 * i, y=300, hp=150, hp_max=420) for i in range(4)]
        w = world([U(1, "htow"), U(2, "hvlt", x=0), U(3, "Hblm", x=5600, y=0)] + wounded, t=300)
        w.gold = 1000
        r = ArmyRecovery()
        self.assertIsNone(r.step(w, Bags({3: [None] * 6}), None, shop_max=SHOP_MAX))
        self.assertEqual(r.phase, "idle")
        self.assertEqual(r.counts.get(f"整队恢复·没开始·{int(SHOP_MAX)}内没有能买的人或商店"), 1)
        r = ArmyRecovery()
        self.assertIsNotNone(r.step(w, Bags({3: [None] * 6}), None, shop_max=SHOP_MAX_IDLE))
        self.assertEqual((r.phase, r.holder_id[0]), ("shopping", 3))


class InventoryCache(unittest.TestCase):
    def test_idle_scans_reuse_the_hero_bag_and_never_ask_troops(self):
        """1-284：空闲时一拍读 85~138 次通道。现在只读英雄的背包，而且 INV_BAG_TTL 内复用。"""
        w = field(1800, 500)
        c = Bags({3: ["stwp"] * 6, 20: [None] * 6})         # 英雄背包满 -> 冷却 15 秒
        r = ArmyRecovery()
        self.assertIsNone(r.step(w, c, None))
        self.assertEqual(set(c.reads), {3})
        r.next_try = 0                                       # 跳过冷却，只看读背包
        w.t += 1
        c.reads.clear()
        r.step(w, c, None)
        self.assertEqual(c.reads, [], "1 秒内同一个英雄的背包不重读")
        w.t += 5
        r.next_try = 0
        r.step(w, c, None)
        self.assertEqual(set(c.reads), {3})

    def test_units_without_backpack_stay_cached(self):
        r = ArmyRecovery()
        c = Bags({})
        u = U(9, "Hamg")
        self.assertIsNone(r.bag(c, u, 100))
        self.assertIsNone(r.bag(c, u, 100 + INV_NONE_TTL - 1))
        self.assertEqual(c.reads, [9])
        r.bag(c, u, 100 + INV_NONE_TTL)
        self.assertEqual(c.reads, [9, 9])


class HolderMissing(unittest.TestCase):
    def test_holder_briefly_out_of_view_does_not_cancel(self):
        w = field(1800, 5000)
        c = Bags({3: [None] * 6})
        r = ArmyRecovery()
        r.step(w, c, None)
        self.assertEqual((r.phase, r.holder_id[0]), ("shopping", 3))
        hero = next(u for u in w.my_heroes if u.addr == 3)
        w.my_heroes.remove(hero)                             # 被毫秒层拉走：这一拍不在这份视野里
        w.t += 3
        self.assertIsNone(r.step(w, c, None))
        self.assertEqual(r.phase, "shopping")
        w.my_heroes.append(hero)
        w.t += 3
        self.assertIsNotNone(r.step(w, c, None))
        self.assertEqual(r.phase, "shopping")

    def test_holder_gone_for_good_is_cancelled_after_the_grace(self):
        w = field(1800, 5000)
        r = ArmyRecovery()
        c = Bags({3: [None] * 6})
        r.step(w, c, None)
        w.my_heroes.clear()
        w.t += 1
        r.step(w, c, None)
        w.t += HOLDER_MISSING_S
        r.step(w, c, None)
        self.assertEqual(r.phase, "idle")


class HomeBuyerLeavesHome(unittest.TestCase):
    def test_home_buyer_walking_to_a_far_vault_is_still_tracked(self):
        """1-284：藏宝室在分矿，家里补给的买家一走出主基地 2000 就被判「不可用」作废。"""
        home = U(1, "htow")
        vault = U(2, "hvlt", x=4500)
        hurt = [U(10 + i, "hfoo", x=400 + 40 * i, y=200, hp=126, hp_max=420) for i in range(4)]
        buyer = U(5, "Hblm", x=300, y=-100)                 # 在家的英雄（买家只能是英雄）
        away = [U(30 + i, "hfoo", x=7000, y=100 * i) for i in range(5)]
        w = world([home, vault, buyer] + hurt + away, t=600)
        w.gold = 800

        class Cmd(FakeCmd):
            USESLOT1 = 0xD0028

            def __init__(self):
                super().__init__()
                self.bags = {5: [None] * 6}

            def inventory(self, a):
                return list(self.bags[a]) if a in self.bags else None

        with tempfile.TemporaryDirectory() as d:
            b = Brain(Cmd(), None, d)
            b.config.values["enable_harass"] = False
            b._read_hurt = lambda: set()
            b._read_resting = lambda: set()
            b._hero_idle_probe = lambda *a: None
            b.attack.creep_engaged = True                    # 主力在打野：整队恢复被挡，家里补给接手
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 7000, 0, why="打野")]
            b._do = lambda it, t: None
            b.job_tactics(w)
            self.assertEqual((b.home_recovery.phase, b.home_recovery.holder_id[0]), ("shopping", 5))
            buyer.x = 2600                                   # 走在去藏宝室的路上，离主基地 2600 > HOME_RECOVERY_R
            w.t += 3
            b.job_tactics(w)
            self.assertEqual(b.home_recovery.phase, "shopping", b.home_recovery.notes)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""整队补给的启动闸（09-21 一堆少血的兵在等，英雄却不用恢复道具；要求多用恢复道具给自家少血单位补血）。

真实失败形状：最近 **60 局**对局记录里「整队恢复」一条动作都没有，
同期毫秒层报了 **5878 次 `sreg·背包里没有`**（对比 `sreg·用了` 只有 155 次）——
不是判据不肯用，是根本没人去买。

两个几乎恒真的闸把整条流程挡死了，而且都看起来完全合理：
  * `unsafe = any(distance(u,f) < 1200 for u in army for f in hostiles)`
    —— 「**全军**任何一个人离**任何一个**敌人或野怪 1200 内」，Echo Isles 上野怪成片，几乎恒真；
  * 上层 `blocked` 里的 `bool(camp_ids) or bool(held)`
    —— 主力几乎一直挂着打野营地，毫秒层每局接管 144.6 次。

这个模块当时**一个计数都没有**，所以「有没有生效」是个不可回答的问题，
直到在对局画面里看出问题。现在每条不走的路都记数。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "war3-modules"))
from brain.recovery import ArmyRecovery, RECOVERY_SAFE_R  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

HOME = (-5184.0, 2944.0)


class FakeCmd:
    """背包：addr -> 六格列表。None 表示空格。"""

    USESLOT1 = 0x000D0028

    def __init__(self, bags=None):
        self.bags = bags or {}
        self.reads = 0
        self.used = []

    def use_item(self, addr, order):
        self.used.append((addr, order))
        return True

    def inventory(self, addr):
        self.reads += 1
        return self.bags.get(addr, [None] * 6)

    def buy_item(self, shop_addr, rawcode):
        self.bought = getattr(self, "bought", 0) + 1
        return True


# 伤兵横向铺开的宽度：算「离野怪多远」时要按最远的那个伤兵算，不是按第一个
SPREAD = 30.0
FRONT = 250.0


def world(injured=7, creep_at=None, gold=2000, healthy=0):
    """一支带伤的部队 + 一座完好的藏宝室；creep_at = 野怪坐标；healthy = 额外几个满血地面兵。"""
    units = [U(1, "htow", *HOME), U(0x50, "hvlt", HOME[0] + 300, HOME[1]), U(900, "unpl", 9000, 9000, owner=1)]
    units += [U(2, "Hamg", HOME[0] + 200, HOME[1] + 100)]
    for i in range(injured):
        u = U(10 + i, "hfoo", HOME[0] + FRONT + SPREAD * i, HOME[1] + 150)
        # ⚠ 要「明显受伤」（< 70% 且缺血 >= 100），但**不能低到 40%** ——
        #   carriers 要求 `u.hp > .40*u.hp_max`（严格大于），正好 0.40 会被排除在买家之外。
        u.hp = u.hp_max * 0.5
        units.append(u)
    for i in range(healthy):
        units.append(U(60 + i, "hrif", HOME[0] + 200, HOME[1] - 100 - 40 * i))
    if creep_at:
        units.append(U(700, "nogm", creep_at[0], creep_at[1], owner=12))
    return build_world(units, 0, 600.0, gold, 500, 40, 60)


class GateIsNotAlwaysOn(unittest.TestCase):
    def test_distant_creeps_do_not_block_recovery(self):
        """地图另一头的野怪不该阻止在家门口补血 —— 这就是原来那个恒真闸。"""
        w = world(creep_at=(9000.0, -9000.0))
        r = ArmyRecovery()
        out = r.step(w, FakeCmd(), None, blocked=False)
        self.assertNotEqual(r.counts.get("整队恢复·没开始·周围有敌人", 0), 1,
                            f"远处野怪不该算威胁：{dict(r.counts)}")
        self.assertNotEqual(r.phase, "idle", f"应该已经开始补给流程：{dict(r.counts)}")

    def test_creep_next_to_the_wounded_does_block(self):
        """伤兵群旁边真的有野怪时仍然不补 —— 放宽不等于取消。"""
        w = world(creep_at=(HOME[0] + 300.0, HOME[1] + 150.0))
        r = ArmyRecovery()
        r.step(w, FakeCmd(), None, blocked=False)
        self.assertEqual(r.counts.get("整队恢复·没开始·周围有敌人"), 1, dict(r.counts))
        self.assertEqual(r.phase, "idle")

    def test_safe_radius_boundary_is_computed(self):
        """边界按常量算，不靠感觉。
        7 个伤兵从 HOME+250 起每 30 一个，最远的在 HOME+250+30*6 = HOME+430；
        野怪要放在 HOME+430+RECOVERY_SAFE_R 之外才算安全。"""
        far = FRONT + SPREAD * 6 + RECOVERY_SAFE_R + 50.0
        w = world(creep_at=(HOME[0] + far, HOME[1] + 150.0))
        r = ArmyRecovery()
        r.step(w, FakeCmd(), None, blocked=False)
        self.assertIsNone(r.counts.get("整队恢复·没开始·周围有敌人"), dict(r.counts))


class EveryRefusalIsCounted(unittest.TestCase):
    """每条「不走」的路都要留下计数 —— 否则「一条动作都没有」和「没轮到」分不开。"""

    def test_blocked_by_upper_layer_says_which_one(self):
        """★ 09-21：blocked 从 bool 换成「谁挡的」那句话。

        28 局实测「整队恢复·没开始·上层挡着」**259.4 次/局**，是所有闸门里最高的 ——
        而它是四个条件 or 出来的，回答不了到底是守家、出击、打野接战还是局部交战挡的
        （一个数同时表示四件事）。现在各记各的。"""
        r = ArmyRecovery()
        r.step(world(), FakeCmd(), None, blocked="打野已接战")
        self.assertEqual(r.counts.get("整队恢复·没开始·上层挡着·打野已接战"), 1, dict(r.counts))
        r.step(world(), FakeCmd(), None, blocked="守家")
        self.assertEqual(r.counts.get("整队恢复·没开始·上层挡着·守家"), 1, dict(r.counts))

    def test_not_enough_wounded(self):
        r = ArmyRecovery()
        r.step(world(injured=2), FakeCmd(), None, blocked=False)
        self.assertEqual(r.counts.get("整队恢复·没开始·伤兵只有2个"), 1, dict(r.counts))

    def test_all_bags_full_is_distinguished_from_no_gold(self):
        """背包满了的情况：候选全满和钱不够要分开记，否则查不出是哪种。"""
        w = world()
        bags = {u.addr: ["stwp", "phea", "ratf", "ratc", "rat9", "rde1"] for u in w.my_army + w.my_heroes}
        r = ArmyRecovery()
        r.step(w, FakeCmd(bags), None, blocked=False)
        self.assertEqual(r.counts.get("整队恢复·没开始·候选背包都满了"), 1, dict(r.counts))

        poor = ArmyRecovery()
        poor.step(world(gold=10), FakeCmd(), None, blocked=False)
        # ★ 09-22：钱不够不再只是记一笔就冷却 15 秒 —— 预留卷轴价让生产让路（1-297）
        self.assertEqual(poor.counts.get("整队恢复·攒卷轴钱（生产让路）"), 1, dict(poor.counts))
        self.assertGreater(poor.reserve, 0)

    def test_picks_a_carrier_with_a_free_slot(self):
        """英雄背包满 -> 换**另一个英雄**去买（背包满的英雄换给背包有空位的英雄来用恢复道具）。
        ★ 09-22：只能换英雄 —— 兵的背包 Aihn「能使用物品 = 0」，历史 165 次兵拿卷轴全被引擎拒。"""
        w = world(healthy=2)
        hero = w.my_heroes[0]
        full = ["stwp", "phea", "ratf", "ratc", "rat9", "rde1"]
        r, cmd = ArmyRecovery(), FakeCmd({hero.addr: full})
        r.step(w, cmd, None, blocked=False)                            # 只有一个英雄、背包满：兵有空格也不买
        self.assertEqual(r.phase, "idle", dict(r.counts))
        self.assertFalse(getattr(cmd, "bought", 0))
        self.assertEqual(r.counts.get("整队恢复·没开始·候选背包都满了"), 1, dict(r.counts))

        second = U(3, "Hblm", HOME[0] + 250, HOME[1] + 60)
        w2 = build_world(list(w.units) + [second], 0, 600.0, 2000, 500, 40, 60)
        r = ArmyRecovery()
        r.step(w2, FakeCmd({hero.addr: full}), None, blocked=False)
        self.assertNotEqual(r.phase, "idle", dict(r.counts))
        self.assertEqual(r.holder_id[0], second.addr)


class ScrollInHandNeedsNoShop(unittest.TestCase):
    """09-21 对局画面（4-009 25~26 分钟）：一堆残血兵停在家里，山丘身上带着一个恢复道具，
    却没有让这些残血单位去山丘身边用掉它，一直卡住。

    两个缺陷叠成死锁：
      ① `carriers` 要求英雄 > 45% 血、小兵 > 40% 血才被检查背包 ——
         而最该放卷轴的时候恰恰大家都残血，拿着卷轴的残血英雄永远轮不到；
      ② 即使选上了，推进到「使用」的那一步卡在 `distance(h, shop) > SHOP_REACH: return`
         —— 持有者必须站在商店 450 内才重读背包，可卷轴已经在手时根本不用回商店。
         于是每拍都在排恢复队形，却永远放不出来。
    """

    def test_wounded_hero_holding_a_scroll_is_picked(self):
        w = world()
        hero = w.my_heroes[0]
        hero.hp = hero.hp_max * 0.3            # 残血：旧的 carriers 门槛（45%）会把它整个排除
        bags = {hero.addr: ["sreg", None, None, None, None, None]}
        r = ArmyRecovery()
        r.step(w, FakeCmd(bags), None, blocked=False)
        self.assertEqual(r.holder_id[0], hero.addr, dict(r.counts))
        self.assertEqual(r.counts.get("整队恢复·用手上已有的卷轴"), 1, dict(r.counts))

    def test_scroll_in_hand_does_not_require_a_shop(self):
        """卷轴在手时不配商店：shop_id 为空，集合点就地取，不把全队拖回商店门口。"""
        w = world()
        hero = w.my_heroes[0]
        bags = {hero.addr: ["sreg", None, None, None, None, None]}
        r = ArmyRecovery()
        r.step(w, FakeCmd(bags), None, blocked=False)
        self.assertIsNone(r.shop_id)
        # 伤兵本来就在持有者身边，所以同一拍就从 gather 推进到 used —— 就地放，不用先跑一趟商店。
        self.assertIn(r.phase, ("gather", "used"), dict(r.counts))
        self.assertNotIn(r.phase, ("idle", "shopping", "buying"))

    def test_holder_far_from_shop_still_advances(self):
        """卷轴在手、持有者离商店很远时，流程必须能推进到使用 —— 这就是那个死锁。"""
        w = world()
        hero = w.my_heroes[0]
        shop = next(u for u in w.my_buildings if u.type == "hvlt")
        hero.x, hero.y = shop.x + 6000.0, shop.y + 6000.0     # 离商店远得不能再远
        for u in w.my_army:                                   # 伤兵都在持有者身边（集合已完成）
            u.x, u.y = hero.x + 100.0, hero.y + 100.0
        bags = {hero.addr: ["sreg", None, None, None, None, None]}
        cmd = FakeCmd(bags)
        used = cmd.used
        r = ArmyRecovery()
        for i in range(4):                                    # 驱动几拍：集合 -> 使用
            w2 = world()
            for u in w2.my_heroes:
                u.x, u.y, u.addr = hero.x, hero.y, hero.addr
            for u in w2.my_army:
                u.x, u.y = hero.x + 100.0, hero.y + 100.0
            w2.t = 600.0 + i * 2.0
            r.step(w2, cmd, None, blocked=False)
        self.assertTrue(used, f"卷轴在手却一直没放出来：phase={r.phase} {dict(r.counts)}")
        self.assertEqual(used[0][1], cmd.USESLOT1 + 0, "应该用第 0 格")


if __name__ == "__main__":
    unittest.main()

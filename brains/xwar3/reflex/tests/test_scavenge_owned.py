# -*- coding: utf-8 -*-
"""捡装备：扫描出来的"地上物品"里混着背包里的东西。

09-15 英雄打完野后常在原地长时间不动，有时地上的书也不捡。

查下来两件事同一个根因：
  * 动作 46 枚举物品注册表，只按坐标范围过滤，**背包里的物品也在里面**，坐标冻在进包那一刻
  * mod_scavenge 判"捡到"是看地址从扫描里消失 —— 捡进包里的永远不消失，
    于是租约到期判"没捡到"、再派一次；英雄出生自带的 stwp 每局都被"去捡（距离 0）"
  * 捡拾租约挂着时 W3-HeroBands 把英雄划进撤退档，打野整段停摆

判据（IsItemOwned：物品的持有者句柄对全 FF = 在地上）09-23 起在运行时里判：W3P 世界块的 items 只放地上的物品，
所以 w3cmd.item_owned 对"在地上的"答 False、世界块里没有的答 None（不知道）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_scavenge  # noqa: E402
import w3world as W  # noqa: E402
from w3cmd import Cmd  # noqa: E402


class _World:
    def read(self):
        return W.WorldState(1, 0, 1.0, 1, 0, True, 300, 50, 0, 0, 0, [], [],
                            [W.Item(0x1000, 1, 2, "tstr", 0.0, 0.0, 75.0)])


class ItemOwned(unittest.TestCase):
    def test_ground_item_is_not_owned(self):
        c = Cmd.with_backends(None, _World())
        self.assertIs(c.item_owned(0x1000), False)

    def test_not_in_world_is_unknown(self):
        self.assertIsNone(Cmd.with_backends(None, _World()).item_owned(0x3000))


class DropOwned(unittest.TestCase):
    def test_owned_items_are_not_on_the_ground(self):
        items = [(0xA, "stwp", 464.0, -6672.0),   # 英雄出生自带、在包里
                 (0xB, "tstr", 900.0, 100.0),     # 地上的书
                 (0xC, "rde1", 950.0, 120.0)]     # 读不到持有者
        owned = {0xA: True, 0xB: False, 0xC: None}
        keep, dropped = mod_scavenge.drop_owned(items, owned.get)
        self.assertEqual([it[0] for it in keep], [0xB, 0xC])
        self.assertEqual(dropped, 1)

    def test_picked_up_item_leaves_live_set(self):
        # 捡进包之后下一次扫描：它还在注册表里，但已有持有者 -> 不在 live 里 -> 判"捡到"
        items = [(0xB, "tstr", 900.0, 100.0)]
        keep, _ = mod_scavenge.drop_owned(items, lambda a: True)
        self.assertNotIn(0xB, {it[0] for it in keep})


if __name__ == "__main__":
    unittest.main()

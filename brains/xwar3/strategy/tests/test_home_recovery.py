# -*- coding: utf-8 -*-
"""家里补给（runtime._home_recovery_step）：主力在外面打野 / 出击时，家里的伤兵自己去藏宝室买恢复卷轴、集合、使用。

09-21 第四次复现：一堆残血单位在家里等恢复，没有一个英雄去给它们补血，属于长期遗留问题。
近 24 局整队恢复只放出卷轴 8 次；头号拦路是「上层挡着」—— 主力在打野（4439 次）/ 出击（1884）/ 守家（982）时整个恢复不启动，
而买家要求血 > 40~45%，家里那群正好都低于这条线。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T          # noqa: E402
from brain.runtime import Brain         # noqa: E402
from test_tactics import U, world       # noqa: E402
from test_runtime_smoke import FakeCmd  # noqa: E402


class BagCmd(FakeCmd):
    """研究过背包的步兵有六格背包；买东西直接进买家背包。"""
    USESLOT1 = 0xD0028

    def __init__(self, backpack):
        super().__init__()
        self.bags = {a: [None] * 6 for a in backpack}
        self.buys = []
        self.active = None

    def inventory(self, a):
        self.active = a
        return list(self.bags[a]) if a in self.bags else None

    def buy_item(self, shop, code):
        self.buys.append(code)
        bag = self.bags[self.active]
        bag[bag.index(None)] = "sreg"
        return True

    def use_item(self, a, order):
        return True


def scene():
    home = U(1, "htow")
    vault = U(2, "hvlt", x=300)
    hurt = [U(10 + i, "hfoo", x=400 + 40 * i, y=200) for i in range(4)]
    for u in hurt:
        u.hp = 0.3 * u.hp_max                                      # 30%：低于原来买家要求的 40%
    away = [U(30 + i, "hfoo", x=7000, y=100 * i) for i in range(5)]   # 主力在 7000 外打野
    # ★ 09-22：买家只能是英雄（兵的背包 Aihn 能拿不能用，见 recovery 文件头）。家里这个英雄 30% 血、在养伤。
    hero = U(5, "Hblm", x=380, y=60)
    hero.hp = 0.3 * hero.hp_max
    w = world([home, vault, hero] + hurt + away, t=600)
    w.gold = 800
    return w, hurt, away


class HomeRecovery(unittest.TestCase):
    def brain(self, d, cmd):
        b = Brain(cmd, None, d)
        b.config.values["enable_harass"] = False
        b._read_hurt = lambda: set()
        b._read_resting = lambda: set()
        b._hero_idle_probe = lambda *a: None
        return b

    def test_home_wounded_shop_while_the_army_creeps(self):
        w, hurt, away = scene()
        with tempfile.TemporaryDirectory() as d:
            cmd = BagCmd({5, 10})                                    # 英雄 + 一个研究了背包的步兵（步兵不当买家）
            b = self.brain(d, cmd)
            b.attack.creep_engaged = True                            # 主力「打野已接战」：整队恢复被挡
            b.attack.step = lambda v, h: [T.Intent("attack_move", v.my_army, 7000, 0, why="打野")]
            sent = []
            b._do = lambda it, t: sent.append(it)
            b.job_tactics(w)
            self.assertEqual(b.recovery.phase, "idle")               # 前提：整队恢复确实没跑
            self.assertNotEqual(b.home_recovery.phase, "idle")
            # 英雄 #5 离藏宝室约 90（< 购买距离 450），30% 血照样当买家：第一拍就下单
            self.assertEqual(b.home_recovery.holder_id[0], 5)
            self.assertEqual(cmd.buys, [int.from_bytes(b"sreg", "big")])
            self.assertEqual(b.counts["家里补给·在跑"], 1)
            creep = [u.addr for it in sent if it.why == "打野" for u in it.units]
            self.assertTrue(set(creep) >= {u.addr for u in away}, "主力照常打野")
            self.assertFalse(set(creep) & {u.addr for u in hurt}, "家里的伤兵不被拉去打野")

    def test_creeping_a_camp_near_home_is_not_home_crew(self):
        """09-22 1-332 9 分：上面那片野离主基地约 1850（在家里补给的 2000 圈里），刚开打，家里补给把圈里所有人当成「家里这一群」
        从出击层拿走、挑大法师去买卷轴 —— 出击层看不到英雄，「打野任务终止」。在打的营地边上的人不算家里的人。"""
        w, hurt, away = scene()
        camp_units = [U(40 + i, "hfoo", x=1300 + 40 * i, y=1300) for i in range(4)]
        am = U(6, "Hamg", x=1250, y=1250)
        creeps = [U(700 + i, "nfsp", x=1500 + 60 * i, y=1400, owner=12) for i in range(3)]
        w = world(list(w.units) + camp_units + [am] + creeps, t=600)
        w.gold = 800
        with tempfile.TemporaryDirectory() as d:
            cmd = BagCmd({5, 6, 10})
            b = self.brain(d, cmd)
            b.attack.state = "creep"
            b.attack.camp = dict(x=1550.0, y=1400.0, strength=9.0, units=creeps)
            b.attack.creep_engaged = True
            seen = {}

            def step(v, h):
                seen["heroes"] = {u.addr for u in v.my_heroes}
                seen["army"] = {u.addr for u in v.my_army}
                return []
            b.attack.step = step
            b._do = lambda it, t: None
            b.job_tactics(w)
            self.assertIn(6, seen["heroes"], "在打野的大法师留在出击层")
            self.assertTrue({40, 41, 42, 43} <= seen["army"], "在打野的兵留在出击层")
            self.assertNotEqual(b.home_recovery.holder_id and b.home_recovery.holder_id[0], 6)

    def test_army_level_recovery_owns_it_when_the_army_is_free(self):
        w, hurt, away = scene()
        with tempfile.TemporaryDirectory() as d:
            cmd = BagCmd({5, 10})
            b = self.brain(d, cmd)
            b.attack.step = lambda v, h: []
            b._do = lambda it, t: None
            b.job_tactics(w)
            self.assertEqual(b.home_recovery.phase, "idle")
            self.assertIsNone(b.counts.get("家里补给·在跑"))


if __name__ == "__main__":
    unittest.main()

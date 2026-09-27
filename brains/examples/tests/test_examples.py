# -*- coding: utf-8 -*-
"""示例 bot 离线驱动：假 Game 记下每条命令，驱动多拍看 bot 的行为对不对（不需要游戏）。

假 Game 只实现示例用到的门面接口，签名和 openwar3.Game 一致 —— 另有一条测试核对这一点，
免得真 Game 改了签名、假的没跟上，测试照绿（同一条规则写在两处，只改一处等于没改）。
"""
import inspect
import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import openwar3  # noqa: E402
from hello_bot import HelloBot  # noqa: E402
from rush_bot import ARMY_WAVE, RushBot  # noqa: E402


def U(addr, t, owner, x, y, hp=100, order=0, has_task=False):
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=hp, hp_max=hp, order=order,
                           has_task=has_task, is_hero=t[0].isupper())


class FakeGame:
    BUILDINGS = {"htow", "hkee", "hhou", "hbar", "halt", "hbla", "hlum", "ngol", "ogre"}

    def __init__(self, units, gold=500, food=(5, 12), t=10.0):
        self.all = units
        self.gold, self.food, self.t = gold, list(food), t
        self.log = []
        self.queues = {}

    # 观察
    def resources(self, player=None): return {"gold": self.gold, "lumber": 200, "food_used": self.food[0], "food_cap": self.food[1]}
    def units(self, owner="all", types=None, alive=True):
        out = [u for u in self.all if (owner == "all" or (owner == "me" and u.owner == 0) or
                                       (owner == "enemy" and 0 < u.owner < 12))]
        return [u for u in out if types is None or u.type in types]
    def is_building(self, u): return u.type in self.BUILDINGS
    def my_buildings(self, types=None): return [u for u in self.units("me", types) if self.is_building(u)]
    def my_workers(self): return self.units("me", {"hpea"})
    def idle_workers(self): return [u for u in self.my_workers() if not u.order and not u.has_task]
    def my_heroes(self): return [u for u in self.units("me") if u.is_hero]
    def my_army(self): return [u for u in self.units("me") if u.type not in {"hpea"} and not self.is_building(u)]
    def gold_mines(self): return [u for u in self.all if u.type == "ngol"]
    def enemies(self, fighters_only=False): return self.units("enemy")
    def trees(self, x=None, y=None, limit=60): return [U(5000 + i, "LTlt", 254, 600 + i * 64, 400) for i in range(3)]
    def build_near(self, worker, code, x, y, min_r=450, max_r=1500, max_tries=24):
        self.log.append(("build", code)); return (x + 600, y)
    def order_of(self, u): return u.order
    is_constructing = openwar3.Game.is_constructing
    under_construction = openwar3.Game.under_construction
    def repair(self, workers, building): self.log.append(("repair", workers.addr, building.addr)); workers.order = 0x000D0038; return True
    def clock(self): return self.t
    def hero_info(self, hero): return {"level": 1, "xp": 0, "skill_points": 1}
    dist = staticmethod(openwar3.Game.dist)
    def nearest(self, candidates, to): return openwar3.Game.nearest(self, candidates, to)
    # macro/micro 用到的（09-24）
    night = False
    verdicts = {}                        # 四字码 -> 可行性裁决（缺省 0 = 能做）
    prods = []                           # [(建筑, Production)]
    def queue(self, building): return list(self.queues.get(building.addr, []))
    def production(self, building): return None
    def can_do(self, u, code): return self.verdicts.get(code, 0)
    def upgrade(self, building, code): self.log.append(("upgrade", building.type, code)); return True
    def all_production(self, owner="me"): return list(self.prods)
    def can_afford(self, code): return self.gold >= 100
    def can_do_many(self, pairs): return [self.verdicts.get(c, 0) for _u, c in pairs]
    def research(self, building, code): self.log.append(("research", building.type, code)); return True
    def creeps(self): return [u for u in self.all if u.owner == 12]
    def last_seen(self, owner="enemy", max_age=None): return []
    def map(self): return None
    def path_distance(self, a, b): return self.dist(a, b)
    def walk_path(self, a, b): return [(b.x, b.y) if hasattr(b, "x") else tuple(b)]
    def path(self, units, points, attack=False):
        self.log.append(("path", len(units) if isinstance(units, list) else 1, attack))
        return [True]
    def is_night(self): return self.night
    def batch(self):
        import contextlib
        return contextlib.nullcontext()
    # 命令：记下来，都算接下
    def gather(self, workers, target, queue=None): self.log.append(("gather", workers.addr, target.addr, queue)); workers.has_task = True; return True
    def train(self, building, code):
        self.log.append(("train", building.type, code))
        self.queues.setdefault(building.addr, []).append(code)
        return True
    def revive(self, altar, hero=None): self.log.append(("revive", altar.type)); return self.dead_hero
    dead_hero = False
    def build(self, worker, code, x, y): self.log.append(("build", code)); return True
    def learn(self, hero, ability): self.log.append(("learn", ability)); return True
    def attack_move(self, units, x, y, queue=None):
        for u in (units if isinstance(units, list) else [units]):
            self.log.append(("amove", u.addr))
            u.order = 0x000D000F
        return True
    def move(self, units, x, y): self.log.append(("move", units.addr)); return True
    def message(self, text): return True


def base(workers=6):
    us = [U(1, "htow", 0, 0, 0, 1500), U(2, "ngol", 15, 800, 0, 12500), U(3, "ngol", 15, 9000, 9000, 12500),
          U(90, "htow", 1, 9500, 9500, 1500)]
    us += [U(10 + i, "hpea", 0, 100, 0) for i in range(workers)]
    return us


class SignatureTest(unittest.TestCase):
    def test_fake_matches_real_signatures(self):
        for name in ("resources", "units", "my_buildings", "idle_workers", "gold_mines", "gather", "train",
                     "build", "learn", "attack_move", "move", "hero_info", "nearest", "enemies",
                     "repair", "trees", "build_near", "clock", "revive"):
            real = list(inspect.signature(getattr(openwar3.Game, name)).parameters)[1:]
            fake = list(inspect.signature(getattr(FakeGame, name)).parameters)[1:]
            self.assertEqual([p for p in real if p in fake], fake, name)


class HelloTest(unittest.TestCase):
    def test_mine_cap_holds_and_overflow_chops(self):
        g = FakeGame(base(workers=8))
        b = HelloBot()
        b.on_start(g)
        for _ in range(5):
            b.on_tick(g)
        gathers = [e for e in g.log if e[0] == "gather"]
        # 近矿 5 个人封顶（远矿离家 >2500 不派），多出来的 3 个去砍树；每人只派一次（has_task 之后不再重派）
        self.assertEqual(len([e for e in gathers if e[2] == 2]), 5)
        self.assertEqual(len([e for e in gathers if e[2] >= 5000]), 3)
        self.assertEqual(len(gathers), 8)

    def test_foundation_not_abandoned(self):
        """09-23 实机：同一个工人每 20 秒被拽去开新地基，3 座农场全停工。"""
        us = base(workers=3)
        us[4].order = 0x68686F75                     # 第一个工人正在盖农场（订单 = 'hhou'）
        us.append(U(7, "hhou", 0, 600, 0, 200))       # 一座没盖完的地基（200/500 血）
        us[-1].hp_max = 500
        us.append(U(8, "hhou", 0, -600, 0, 150))      # 另一座停工的
        us[-1].hp_max = 500
        g = FakeGame(us, food=(11, 12), t=100.0)
        b = HelloBot()
        b.on_start(g)
        b.on_tick(g)
        self.assertEqual([e for e in g.log if e[0] == "build"], [], "有地基没盖完不该开新的")
        rep = [e for e in g.log if e[0] == "repair"]
        self.assertEqual(len(rep), 1, "两座地基只有一个人在盖 -> 派一个去续建")
        self.assertNotEqual(rep[0][1], us[4].addr, "不能把正在盖的那个人拽走")

    def test_builds_farm_when_food_nearly_capped(self):
        g = FakeGame(base(), food=(10, 12))
        b = HelloBot()
        b.on_start(g)
        b.on_tick(g)
        b.on_tick(g)                     # 同一游戏秒不重复盖（20 秒一次）
        self.assertEqual([e for e in g.log if e[0] == "build"], [("build", "hhou")])


class RushTest(unittest.TestCase):
    def test_attacks_when_wave_ready_then_regroups(self):
        us = base() + [U(200 + i, "hfoo", 0, 0, 0) for i in range(ARMY_WAVE)] + [U(5, "hbar", 0, -700, -300, 1500)]
        g = FakeGame(us)
        b = RushBot()
        b.on_start(g)
        b.on_tick(g)
        self.assertEqual(len([e for e in g.log if e[0] == "amove"]), ARMY_WAVE)
        g.log.clear()
        b.on_tick(g)                     # 已经在攻击的不重下（重下会打断战斗）
        self.assertEqual([e for e in g.log if e[0] == "amove"], [])
        g.all = [u for u in g.all if not (u.type == "hfoo" and u.addr >= 203)]   # 打残
        b.on_tick(g)
        self.assertFalse(b.attacking)
        self.assertEqual(len([e for e in g.log if e[0] == "move"]), 3)

    def test_trains_hero_and_learns(self):
        us = base() + [U(6, "halt", 0, -700, 300, 1000)]
        g = FakeGame(us)
        b = RushBot()
        b.on_start(g)
        b.on_tick(g)
        self.assertIn(("train", "halt", "Hpal"), g.log)
        g.all.append(U(300, "Hpal", 0, 0, 0))
        b.on_tick(g)
        self.assertIn(("learn", "AHhb"), g.log)

    def test_dead_hero_is_revived_not_retrained(self):
        """09-23 实机：圣骑士阵亡后 rush_bot 每拍训练 Hpal 都回 221，兵营也跟着停了。"""
        us = base() + [U(6, "halt", 0, -700, 300, 1000)]
        g = FakeGame(us)
        g.dead_hero = True
        b = RushBot()
        b.on_start(g)
        b.on_tick(g)
        self.assertIn(("revive", "halt"), g.log)
        self.assertNotIn(("train", "halt", "Hpal"), g.log)


class QueueOneTest(unittest.TestCase):
    def test_hall_queue_holds_one_worker(self):
        """09-24 实机：每拍都下训练令，大厅排了 4 个农民、300 金锁在队列里。"""
        g = FakeGame(base(workers=5))
        b = HelloBot()
        b.on_start(g)
        for _ in range(4):
            b.on_tick(g)
        self.assertEqual(len([e for e in g.log if e[:2] == ("train", "htow")]), 1)


class MacroTest(unittest.TestCase):
    def _bot(self, g):
        from macro_bot import MacroBot
        b = MacroBot()
        b.on_start(g)
        return b

    def test_build_order_then_back_to_mine(self):
        g = FakeGame(base(), gold=500, food=(5, 30), t=50.0)
        self._bot(g).on_tick(g)
        self.assertIn(("build", "halt"), g.log)                           # 表里第一座
        back = [e for e in g.log if e[0] == "gather" and e[3] == "after"]
        self.assertEqual(len(back), 1, "盖完回矿：build 之后 gather(queue='after')")

    def test_blocked_production_builds_farm_now(self):
        from w3world import Production
        g = FakeGame(base(), gold=500, food=(10, 30), t=50.0)            # 人口看着还够（不会触发 hello 的"快满了"）
        hall = g.all[0]
        g.prods = [(hall, Production((1, 1), "queue", ["hpea"], 0.0, 0.0, 1.0, True, True))]
        self._bot(g).on_tick(g)
        self.assertIn(("build", "hhou"), g.log)

    def test_barracks_trains_first_allowed_soldier_one_at_a_time(self):
        us = base() + [U(5, "hbar", 0, -700, -300, 1500)]
        g = FakeGame(us, gold=500, food=(5, 30), t=50.0)
        g.verdicts = {"hrif": 183}                                         # 火枪缺铁匠铺
        b = self._bot(g)
        b.on_tick(g)
        b.on_tick(g)
        self.assertEqual([e for e in g.log if e[:2] == ("train", "hbar")], [("train", "hbar", "hfoo")])

    def test_wave_goes_by_path_then_reinforces(self):
        from macro_bot import WAVE_FOOD
        n = int(WAVE_FOOD * 1.5) // 2                                         # 没侦察到对面：门槛 ×1.5；步兵 2 人口
        us = base() + [U(200 + i, "hfoo", 0, 0, 0) for i in range(n)]
        g = FakeGame(us, gold=0, food=(n * 2 + 5, 90), t=50.0)
        b = self._bot(g)
        b.on_tick(g)
        self.assertTrue(b.attacking)
        self.assertIn(("path", n, True), g.log)
        for u in g.all:
            if u.type == "hfoo":
                u.order = 0x000D000F                                          # 都在路上
        g.all.append(U(400, "hfoo", 0, 0, 0))                                 # 新出的一个兵
        g.log.clear()
        b.on_tick(g)
        self.assertEqual([e for e in g.log if e[0] == "amove"], [("amove", 400)])   # 只派新来的

    def test_scouts_once_then_back_to_mine(self):
        g = FakeGame(base(workers=8), gold=0, food=(8, 20), t=100.0)
        g.map = lambda: SimpleNamespace(starts=[(0.0, 0.0), (9500.0, 9500.0)])
        b = self._bot(g)
        b.on_tick(g)
        b.on_tick(g)
        self.assertEqual(len([e for e in g.log if e[0] == "path"]), 1)       # 只派一次
        self.assertTrue(any(e[0] == "gather" and e[3] == "after" for e in g.log))   # 看完回矿

    def test_saves_for_tier_instead_of_training(self):
        """09-24 实机：不存钱时两座兵营每拍先把钱花光，16 分钟没升上本。"""
        us = base(workers=12) + [U(5 + i, t, 0, -700, 300 * i, 1000) for i, t in enumerate(("halt", "hbar", "hbla", "hlum"))]
        us += [U(200 + i, "hfoo", 0, 0, 0) for i in range(6)]              # 家里有 12 人口的兵：可以存钱
        g = FakeGame(us, gold=200, food=(30, 60), t=50.0)
        g.verdicts = {"hkee": 8}                                            # 升本缺金
        b = self._bot(g)
        b.on_tick(g)
        self.assertEqual([e for e in g.log if e[0] in ("train", "research") and e[1] in ("hbar", "hbla")], [])

    def test_tiers_up_when_order_done_and_hall_idle(self):
        us = base(workers=12) + [U(5 + i, t, 0, -700, 300 * i, 1000) for i, t in enumerate(("halt", "hbar", "hbla", "hlum"))]
        g = FakeGame(us, gold=500, food=(10, 30), t=50.0)                  # 工人够了：大厅不造农民、队列空
        b = self._bot(g)
        b.on_tick(g)
        self.assertIn(("upgrade", "htow", "hkee"), g.log)


if __name__ == "__main__":
    unittest.main()

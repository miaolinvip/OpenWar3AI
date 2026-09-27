# -*- coding: utf-8 -*-
"""冒烟：假通道 + 假快照驱动新大脑开局几十游戏秒 —— 任务不抛异常、下出预期的命令（造祭坛/兵营、训练农民、派伐木、首英雄不收钱）。"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import act as A  # noqa: E402
from brain import gamedata as G  # noqa: E402
from brain.runtime import Brain  # noqa: E402

class FakeCmd:
    """w3cmd.Cmd 的假对象（09-23 P3 起大脑只用它的语义方法：build / rally / learn / revive / feasible ……，没有 .ch 了）。"""
    pid = 4242

    def __init__(self):
        self.builds, self.trains, self.gathers, self.moves, self.rallies = [], [], [], [], []
        self.orders = {}
        self.last_receipt = None

    def context(self, u):
        return 0

    def immediate_order(self, u, order):
        self.trains.append((u, order))
        return True

    def move_to(self, u, x, y, order=A.ORDER_SMART):
        self.moves.append((u, x, y, order))
        return True

    def target_order(self, u, t, order, x=0.0, y=0.0):
        self.gathers.append((u, t, order))
        return True

    def build(self, u, order, x, y):
        self.builds.append((u, order, x, y))
        self.orders[u] = order                     # 接下：工人订单变成建筑码
        return True

    def rally(self, u, x=None, y=None, target=0):
        self.rallies.append((u, x, y))
        return True

    def feasible(self, u, code):                  # 可行性：建筑存在就放行
        return 0

    def skill_points(self, u):
        return 0

    def learn(self, u, code):
        return False

    def dead_heroes(self, player):
        return []

    def revive(self, altar, hero=0):
        return False

    def action(self, a, c=0, timeout=5.0):
        return True

    def current_order(self, u):
        return {"order": self.orders.get(u, 0)}

    def player_of(self, u):
        return 0x5000

    def tech_count(self, p, code):
        return 0

    def hero_level(self, u):
        return 1

    def captain(self, order):
        return None

    def cast_target(self, *a):
        return True


def U(addr, t, x, y, owner=0, order=0, has_task=False):
    m = float(G.stat(t, "hp", 100))
    return SimpleNamespace(addr=addr, type=t, owner=owner, x=x, y=y, hp=m, hp_max=m, order=order, order_known=True,
                           has_task=has_task, handle_lo=addr, handle_hi=1, is_hero=t[:1].isupper())


class TestSmoke(unittest.TestCase):
    def test_opening(self):
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.act.scan_trees = lambda x, y: [dict(addr=0x9000 + i, type="LTlt", x=4000 + i * 64, y=-3500, hp=50.0) for i in range(10)]
            hall = U(0x100, "htow", 4352, -2688)
            mine = U(0x200, "ngol", 4352, -3200, owner=15)
            peas = [U(0x300 + i, "hpea", 4300 + i * 40, -2900, order=A.ORDER_HARVEST, has_task=True) for i in range(3)] + \
                   [U(0x310 + i, "hpea", 4400, -2500) for i in range(2)]                  # 两个闲着的
            enemy = [U(0x800, "hcas", -4352, 2944, owner=1)]
            gold, lumber = 500, 150
            for t in range(2, 40, 2):
                snap = SimpleNamespace(units=[hall, mine] + peas + enemy, me=0, gold=gold, lumber=lumber, food_used=5, food_cap=12)
                b.step(snap, float(t))
            errs = {n: s for n, s in b.jobs.stats().items() if s["errors"]}
            self.assertEqual(errs, {})
            built = [A.rawcode(c) for c in ("halt", "hbar")]
            self.assertTrue(any(o in built for _, o, _, _ in cmd.builds), cmd.builds)
            self.assertIn(A.rawcode("hpea"), [o for _, o in cmd.trains])
            self.assertTrue(any(o == A.ORDER_HARVEST and t == 0x200 for _, t, o in cmd.gathers), cmd.gathers)   # 闲人先去金矿
            self.assertTrue(os.path.exists(os.path.join(d, "reserved.json")))
            self.assertIn("counts", b.lesson())
            # 控制台读的实时状态：写出来了、能解析、带规划表和意图
            import json
            with open(os.path.join(d, "brain_live.json"), encoding="utf-8") as fh:
                live = json.load(fh)
            self.assertEqual(live["attack"]["state"], b.attack.state)
            # 最后一份发布时那一轮不一定有新下单（钱前面花完了）—— 只断言每一条都有状态、首英雄已经不在「没轮到」
            self.assertTrue(live["plan"]["wants"] and all(x["status"] for x in live["plan"]["wants"]))
            self.assertIn(live["plan"]["wants"][0]["code"], ("halt", "Hamg"))

    def test_config_switch_off_build(self):
        import json
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "brain_config.json"), "w", encoding="utf-8") as fh:
                json.dump({"enable_build": False, "creep_power_min": 99, "nope": 1}, fh)
            b = Brain(cmd, None, d)
            from brain import tactics as T
            self.assertEqual((b.config["enable_build"], T.CREEP_POWER_MIN, "nope" in b.config.overrides), (False, 6.0, False))  # 夹到上限 6.0、不认识的键丢掉
            hall = U(0x100, "htow", 4352, -2688)
            peas = [U(0x310 + i, "hpea", 4400, -2500) for i in range(2)]
            for t in range(2, 20, 2):
                b.step(SimpleNamespace(units=[hall] + peas, me=0, gold=500, lumber=150, food_used=2, food_cap=12), float(t))
            self.assertEqual((cmd.builds, cmd.trains), ([], []))
            T.CREEP_POWER_MIN = 2.5                           # 模块级常量，别漏到别的测试

    def test_resume_stalled_site(self):
        from brain.world import build_world
        cmd = FakeCmd()
        with tempfile.TemporaryDirectory() as d:
            b = Brain(cmd, None, d)
            b.config.values['enable_repair'] = False  # 本例只验证续建；成品受损维修有独立测试。
            hall = U(0x100, "htow", 0, 0)
            farm = U(0x400, "hhou", 600, 0)
            farm.hp = 54.0                                     # 09-17 实机那座：54/500，旁边没人
            barracks = U(0x500, "hbar", -600, 0)
            pea = U(0x300, "hpea", 300, 300)

            def w(t, farm_hp, bar_hp):
                farm.hp, barracks.hp = farm_hp, bar_hp
                return build_world([hall, farm, barracks, pea], 0, t, 500, 100, 5, 12)

            b.job_construction(w(100.0, 54.0, 1500.0))      # 第一次看见工地；兵营满血 -> 见过满血
            b.job_construction(w(104.0, 54.0, 700.0))       # 兵营挨打掉血，不是工地
            self.assertEqual(cmd.gathers, [])
            b.job_construction(w(110.0, 54.0, 700.0))       # 工地 10 游戏秒没涨血 -> 派人 smart 过去
            self.assertEqual(cmd.gathers, [(0x300, 0x400, A.ORDER_SMART)])
            b.job_construction(w(114.0, 54.0, 700.0))       # 已经派过（在路上），不重派
            self.assertEqual(len(cmd.gathers), 1)
            self.assertEqual(b.counts.get("续建·派出·hhou"), 1)

    def test_expansion_mine_enemy_natural(self):
        # Echo Isles 实测坐标：我方主基地 (-5184,2944)、分矿 (-4352,-2624)；对面分矿矿点 (4352,-3200)，大厅早拆了
        from brain.world import build_world
        b = Brain(FakeCmd(), None, tempfile.gettempdir())
        mines = [U(0x600 + i, "ngol", x, y, owner=15) for i, (x, y) in enumerate(((-5632, 3584), (-4864, -3200), (5120, 3584), (4352, -3200)))]
        mine_side = [U(0x100, "hcas", -5184, 2944), U(0x101, "htow", -4352, -2624)]
        foe = [U(0x800, "hcas", 4672, 2944, owner=1), U(0x801, "hbar", 4096, 2752, owner=1)]

        def pick(extra):
            w = build_world(mine_side + mines + foe + extra, 0, 1500.0, 3000, 500, 50, 60)
            m = b._expansion_mine(w)
            return None if m is None else (int(m.x), int(m.y))

        # 离最近的我方大厅（分矿）8723 < 12000；旁边 2 座对面农场不挡
        self.assertEqual(pick([U(0x900, "hhou", 4000, -2800, owner=1), U(0x901, "hhou", 4600, -2800, owner=1)]), (4352, -3200))
        self.assertIsNone(pick([U(0x902, "hgtw", 4000, -2400, owner=1)]))                      # 1600 内有对面塔
        self.assertIsNone(pick([U(0x903, "hfoo", 4352, -1400, owner=1)]))                      # 2000 内有对面兵


if __name__ == "__main__":
    unittest.main()

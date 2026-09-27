"""英雄不能落在小兵撤退规则与英雄预警规则之间。"""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C
from w3state import Unit


def unit(a, typ, x=0, y=0, hp=500, maximum=500, owner=0):
    return Unit(index=a, addr=a, type=typ, owner=owner, x=x, y=y, hp=hp, hp_max=maximum, order=0, order_known=True)


class HeroLocalSafety(unittest.TestCase):
    def setUp(self):
        self.h = unit(1, "Hamg")
        self.foes = [unit(100+i, "ugho", 220, i*30, 340, 340, 1) for i in range(3)]

    def test_full_health_lone_hero_flees_three_enemies(self):
        self.assertIsNotNone(C.hero_exposure(self.h, self.foes, [self.h]))

    def test_creeps_use_same_local_safety(self):
        """09-23 起满血英雄对野怪顶着打（test_creep_hero_tank，对局 1-418）；血掉到 CREEP_TANK_FRAC 以下照旧按孤军撤。"""
        foes = [unit(100+i, "nogm", 200, i*30, 800, 800, 12) for i in range(2)]
        self.assertIsNone(C.hero_exposure(self.h, foes, [self.h]))
        half = unit(1, "Hamg", hp=250)
        self.assertIsNotNone(C.hero_exposure(half, foes, [half]))

    def test_1284_summon_fighting_the_same_creeps_is_not_isolation(self):
        """1-284 3:20：豺狼人先咬上满血大法师，525 血的水元素在旁边接战（离野怪 500~670，比英雄远）——
        旧版判「孤军且敌方占优」退 1600、锁 10 秒，大脑跟着整队重整。召唤物和同一批野怪交手就算自己人。"""
        h = unit(1, "Hamg", hp=450, maximum=450)
        gnolls = [unit(100, "ngnb", 150, 0, 330, 330, 12), unit(101, "ngnb", 120, 90, 330, 330, 12),
                  unit(102, "ngna", 200, -80, 240, 240, 12)]
        water = unit(4, "hwat", -350, 300, 525, 525)
        self.assertIsNone(C.hero_exposure(h, gnolls, [h, water]))
        # 对玩家的兵照旧不算召唤物（4-036 那条的边界没动）
        ghouls = [unit(200 + i, "ugho", 150, i * 40, 340, 340, 1) for i in range(3)]
        self.assertEqual(C.hero_exposure(h, ghouls, [h, water])[0], "孤军且敌方占优")

    def test_healthy_hero_holds_small_creeps_while_troops_arrive(self):
        """5-117：面对较弱的野怪，英雄能顶伤害就先顶住，不让小兵去扛。步兵离野怪约 1000、正在赶来：
        血 >= CREEP_HOLD_FRAC 先顶；半血就照旧撤；一个自己人都没有也照旧撤（test_creeps_use_same_local_safety）。"""
        gnolls = [unit(100, "ngnb", 150, 0, 330, 330, 12), unit(101, "ngnb", 120, 90, 330, 330, 12),
                  unit(102, "ngna", 200, -80, 240, 240, 12)]
        feet = [unit(10 + i, "hfoo", -900, i * 40, 420, 420) for i in range(3)]
        full = unit(1, "Hamg", hp=450, maximum=450)
        self.assertIsNone(C.hero_exposure(full, gnolls, [full] + feet))
        half = unit(1, "Hamg", hp=225, maximum=450)
        self.assertIsNotNone(C.hero_exposure(half, gnolls, [half] + feet))
        far = [unit(10 + i, "hfoo", -1600, i * 40, 420, 420) for i in range(3)]    # 离野怪 1700：不算「正赶过来」
        self.assertIsNone(C.hero_exposure(full, gnolls, [full] + far), "09-23 起满血没人来也先顶着（CREEP_TANK_FRAC）")
        self.assertIsNotNone(C.hero_exposure(half, gnolls, [half] + far))

    def test_pulled_troops_do_not_count_as_escorts(self):
        friends = [unit(10+i, "hkni", 100, i*30, 1000, 1000) for i in range(4)]
        self.assertIsNone(C.hero_exposure(self.h, self.foes, [self.h]+friends))
        self.assertIsNotNone(C.hero_exposure(self.h, self.foes, [self.h]+friends, {u.addr for u in friends}))

    def test_distant_and_low_health_troops_do_not_count(self):
        friends = [unit(10+i, "hkni", -1500, i*30, 1000, 1000) for i in range(4)]
        friends += [unit(20+i, "hfoo", 50, i*30, 10, 420) for i in range(4)]
        self.assertIsNotNone(C.hero_exposure(self.h, self.foes, [self.h]+friends))

    def test_one_weak_enemy_does_not_make_healthy_hero_flee(self):
        self.assertIsNone(C.hero_exposure(self.h, [unit(100, "hfoo", 200, 0, 40, 420, 1)], [self.h]))

    def test_low_hero_exits_even_with_escorts(self):
        h = unit(1, "Hamg", hp=150)
        friends = [unit(10+i, "hkni", 100, i*30, 1000, 1000) for i in range(5)]
        self.assertEqual(C.hero_exposure(h, self.foes, [h]+friends)[0], "低血量脱离接触")

    def test_surrounded_hero_exits_before_burst_reaches_old_low_health_line(self):
        """1-090：11 个敌人贴脸时，英雄从 75% 到阵亡只剩约 1.5 秒。"""
        h = unit(1, "Hmkg", hp=600, maximum=750)
        foes = [unit(100+i, "ogru", 220, i*30, 700, 700, 1) for i in range(6)]
        friends = [unit(10+i, "hfoo", -120, i*30, 420, 420) for i in range(6)]
        self.assertEqual(C.hero_exposure(h, foes, [h]+friends)[0], "大群贴脸提前脱离")
        h.hp = 650                         # 86.7%：新样本里两三秒足以阵亡，第一轮受伤就拉出包围
        self.assertEqual(C.hero_exposure(h, foes, [h]+friends)[0], "大群贴脸提前脱离")
        h.hp = 710                         # 94.7%：尚未明显受伤，真实集火检测仍会单独保护
        self.assertIsNone(C.hero_exposure(h, foes, [h]+friends))

    def test_safe_hero_not_pulled_due_to_low_health_alone(self):
        self.assertIsNone(C.hero_exposure(unit(1, "Hamg", hp=100), [], [self.h]))

    def test_large_heal_releases_hero_when_escorted_and_locally_ahead(self):
        hero = unit(1, "Hmkg", hp=600, maximum=850)
        friends = [unit(10+i, "hkni", 80, i*40, 900, 900) for i in range(3)]
        foe = [unit(100, "hfoo", 300, 0, 200, 420, 1)]
        self.assertTrue(C.healed_hero_rejoin_ready(hero, 200, foe, [hero]+friends))
        self.assertFalse(C.healed_hero_rejoin_ready(hero, 30, foe, [hero]+friends))

    def test_dying_hero_bypasses_full_pull_quota(self):
        h = unit(1, "Hamg", hp=100)
        friends = [unit(10+i, "hfoo", -600, i*30, 100, 420) for i in range(3)]
        selected, _, _ = C.pull_picks([(h, 50, -100, 4, 1.5)], [h]+friends, {u.addr:10 for u in friends}, 1, False)
        self.assertEqual([t[0].addr for t in selected], [1])

    def test_hero_predicted_dead_not_blocked_by_half_hp_rule(self):
        h = unit(1, "Hamg", hp=400)
        selected, _, kept = C.pull_picks([(h, 150, -200, 4, 1.5)], [h], {}, 1, False)
        self.assertEqual(([t[0].addr for t in selected], kept), ([1], 0))

    def test_main_loop_does_not_overwrite_hero_escape(self):
        from test_combat_main_smoke import FakeTime, FakeCmd, FakeClaims, FakeOdds, _Stop

        for owner in (1, 12):
            with self.subTest(enemy_owner=owner):
                notes, leases = [], []
                clock = FakeTime()
                cmd = FakeCmd({1: "Hamg"})
                # 野怪那一轮用 40% 血的英雄：09-23 起满血英雄对野怪顶着打（CREEP_TANK_FRAC），这里验的是「撤退不被覆盖」
                mine = [self.h if owner != 12 else unit(1, "Hamg", hp=200), unit(90, "htow", -2000, -2000, 1500, 1500)]
                foes = [unit(100+i, "nogm" if owner == 12 else "ugho", 200, i*30, 800, 800, owner)
                        for i in range(3)]
                snap = SimpleNamespace(in_game=True, epoch=7, me=0, mine=mine, units=mine+foes, age_ms=5)
                ses = SimpleNamespace(pid=4242, cmd=cmd, read=lambda: snap)
                import os
                import tempfile
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                with mock.patch.object(C, "DECF", os.path.join(tmp.name, "decisions.jsonl")), \
                        mock.patch.object(C, "Session", lambda: ses), \
                        mock.patch.object(C, "Claims", FakeClaims), \
                        mock.patch.object(C, "time", clock), \
                        mock.patch.object(C, "note", notes.append), \
                        mock.patch.object(C, "write_deadlines", lambda *a, **k: leases.append(a)), \
                        mock.patch.object(C.modlog, "tee_stdout", lambda *a, **k: None), \
                        mock.patch.object(C.fightodds, "FightOdds", FakeOdds), \
                        mock.patch.object(C.fightodds, "GameClock", lambda ch: SimpleNamespace(read=lambda: None)), \
                        mock.patch.object(C.fightodds, "write_signal", lambda *a, **k: None), \
                        mock.patch.object(C, "under_fire", lambda *a, **k: True), \
                        self.assertRaises(_Stop):
                    # 09-22 起没在挨打的英雄不因「孤军」拉（mod_combat HIT_WIN 上面那段）；这里验撤退不被覆盖，场景定成在挨打

                    C.main()
                self.assertFalse([n for n in notes if "出错" in n])
                self.assertTrue(any("英雄局部自保" in n for n in notes))
                hero_orders = [c for c in cmd.calls if c[1] == self.h.addr]
                self.assertTrue(hero_orders)
                self.assertTrue(all(c[0] == "move" for c in hero_orders), hero_orders[:10])
                self.assertTrue(leases)


if __name__ == "__main__":
    unittest.main()

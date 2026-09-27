"""英雄在合适的时机、合适的点位放合适的技能 —— mod_combat.plan_hero_cast 的离线语义。

09-15 英雄主动技能几乎从未释放；研究让英雄在最合适的时机、最合适的点位放合适的技能。
现场：最近三局大法师每局只放 8~9 次、全是水元素；山丘之王一局 3 次风暴之锤；暴风雪一次都没学过。
策略层 W3-CastSpells 每个英雄 4 拍（约 12 游戏秒）才试一次、只对着"当前攻击目标"放 —— 挪到 100ms 的毫秒层。
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C
from w3state import Unit

ME, FOE, CREEP = 0, 1, 12


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def always_ready(_name):
    return True


class Archmage(unittest.TestCase):
    def test_water_elemental_when_enemies_near(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        foes = [u(10, "hfoo", FOE, 700, 0)]
        plan = C.plan_hero_cast(hero, foes, [hero], always_ready)
        self.assertEqual(plan[0], "waterelemental")
        self.assertEqual(plan[2], "instant")

    def test_no_cast_when_nobody_near(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 3000, 0)], [hero], always_ready))

    def test_water_elemental_early_before_contact(self):
        # 09-17：敌方玩家作战单位 1200 内就先召（原来 900）；1300 不召
        hero = u(1, "Hamg", ME, 0, 0, 800)
        self.assertEqual(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 1100, 0)], [hero], always_ready)[0], "waterelemental")
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 1300, 0)], [hero], always_ready))

    def test_water_elemental_before_creeping_with_escort(self):
        # 野怪 1300（900 外、1400 内）：身边 1200 内有 2 个自己的兵才召；工人/建筑不算兵；只有 1 个兵不召
        hero = u(1, "Hamg", ME, 0, 0, 800)
        creeps = [u(10, "nfor", CREEP, 1300, 0)]
        troops = [u(20, "hfoo", ME, 200, 0), u(21, "hrif", ME, -200, 0)]
        self.assertEqual(C.plan_hero_cast(hero, creeps, [hero] + troops, always_ready)[0], "waterelemental")
        self.assertIsNone(C.plan_hero_cast(hero, creeps, [hero, troops[0], u(22, "hpea", ME, 100, 0), u(23, "hbar", ME, 50, 50)], always_ready))
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "nfor", CREEP, 1500, 0)], [hero] + troops, always_ready))


class WaterGuard(unittest.TestCase):
    """09-17 水元素护卫：去打离大法师最近的敌人（900 内），水元素离大法师 1500 内才叫。"""

    def test_attacks_threat_nearest_to_hero(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        water = u(2, "hwat", ME, 300, 300, 525)
        threats = [u(10, "hfoo", FOE, 800, 0), u(11, "ogru", FOE, 400, 0), u(12, "nfor", CREEP, 1200, 0)]
        picks = C.guard_picks(hero, [water], threats, {}, 100.0)
        self.assertEqual([(w.addr, t.addr) for w, t in picks], [(2, 11)])

    def test_nothing_near_or_water_too_far(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        self.assertEqual(C.guard_picks(hero, [u(2, "hwat", ME, 100, 0)], [u(10, "hfoo", FOE, 950, 0)], {}, 0.0), [])
        self.assertEqual(C.guard_picks(hero, [u(2, "hwat", ME, 1600, 0)], [u(10, "hfoo", FOE, 500, 0)], {}, 0.0), [])

    def test_throttle_same_and_switch(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        water = u(2, "hwat", ME, 100, 0)
        threat = [u(10, "hfoo", FOE, 500, 0)]
        self.assertEqual(C.guard_picks(hero, [water], threat, {2: (10, 100.0)}, 105.9), [])          # 同一个目标 6 秒内不重下
        self.assertEqual(len(C.guard_picks(hero, [water], threat, {2: (10, 100.0)}, 106.0)), 1)
        self.assertEqual(C.guard_picks(hero, [water], threat, {2: (99, 100.0)}, 101.9), [])          # 换目标至少隔 2 秒
        self.assertEqual(len(C.guard_picks(hero, [water], threat, {2: (99, 100.0)}, 102.0)), 1)

    def test_creep_guard_waits_for_real_escort(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        water = u(2, "hwat", ME, 100, 0)
        creep = [u(10, "nfor", CREEP, 500, 0)]
        footmen = [u(20, "hfoo", ME, 300, 0), u(21, "hfoo", ME, -300, 0)]
        self.assertEqual(C.guard_picks(hero, [water], creep, {}, 0.0, [hero]), [])
        self.assertEqual(C.guard_picks(hero, [water], creep, {}, 0.0, [hero, footmen[0]]), [])
        self.assertEqual(len(C.guard_picks(hero, [water], creep, {}, 0.0, [hero] + footmen)), 1)

    def test_player_threat_guard_does_not_wait_for_escort(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        water = u(2, "hwat", ME, 100, 0)
        self.assertEqual(len(C.guard_picks(hero, [water], [u(10, "ogru", FOE, 500, 0)], {}, 0.0, [hero])), 1)

    def test_blizzard_on_clump_away_from_own_troops(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        clump = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(4)]    # 600..780，半径 250 内 4 个
        ready = lambda n: n != "waterelemental"
        plan = C.plan_hero_cast(hero, clump, [hero], ready)
        self.assertEqual(plan[0], "blizzard")
        self.assertEqual(plan[2], "point")
        self.assertAlmostEqual(plan[4], 690.0, delta=1.0)     # 落点 = 这团的重心
        self.assertAlmostEqual(plan[5], 0.0, delta=1.0)

    def test_blizzard_not_on_own_troops(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        clump = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(4)]
        mine = [hero, u(20, "hfoo", ME, 650, 20), u(21, "hfoo", ME, 700, -20)]   # 圈里有 2 个自己人
        ready = lambda n: n != "waterelemental"
        self.assertIsNone(C.plan_hero_cast(hero, clump, mine, ready))

    def test_three_is_not_a_clump(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        clump = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(3)]
        ready = lambda n: n != "waterelemental"
        self.assertIsNone(C.plan_hero_cast(hero, clump, [hero], ready))


class MountainKing(unittest.TestCase):
    def test_storm_bolt_prefers_enemy_hero(self):
        hero = u(1, "Hmkg", ME, 0, 0, 900)
        foes = [u(10, "hfoo", FOE, 200, 0, 100, 420), u(11, "Hamg", FOE, 600, 0, 500, 800)]
        plan = C.plan_hero_cast(hero, foes, [hero], always_ready)
        self.assertEqual(plan[0], "stormbolt")
        self.assertEqual(plan[3], 11)

    def test_storm_bolt_on_caster_when_no_hero(self):
        hero = u(1, "Hmkg", ME, 0, 0, 900)
        foes = [u(10, "hfoo", FOE, 200, 0), u(12, "hmpr", FOE, 500, 0, 290, 290)]
        plan = C.plan_hero_cast(hero, foes, [hero], always_ready)
        self.assertEqual((plan[0], plan[3]), ("stormbolt", 12))

    def test_thunder_clap_when_surrounded(self):
        hero = u(1, "Hmkg", ME, 0, 0, 900)
        foes = [u(10 + i, "hfoo", FOE, 150, 50 * i) for i in range(3)]
        ready = lambda n: n != "stormbolt"
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[0], "thunderclap")

    def test_avatar_when_low_and_crowded(self):
        hero = u(1, "Hmkg", ME, 0, 0, 400, 900)
        foes = [u(10 + i, "hfoo", FOE, 300, 60 * i) for i in range(4)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], always_ready)[0], "avatar")


class Paladin(unittest.TestCase):
    def test_divine_shield_when_about_to_die(self):
        hero = u(1, "Hpal", ME, 0, 0, 250, 900)
        foes = [u(10, "hfoo", FOE, 200, 0)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], always_ready)[0], "divineshield")

    def test_divine_shield_early_when_focused(self):
        hero = u(1, "Hpal", ME, 0, 0, 540, 900)       # 60%，旧 35% 线会来不及
        foes = [u(10+i, "hfoo", FOE, 200, i*30) for i in range(3)]
        ctx = {"focus_counts": {hero.addr: 3}}
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], always_ready, ctx=ctx)[0], "divineshield")

    def test_holy_light_on_most_hurt_ally(self):
        hero = u(1, "Hpal", ME, 0, 0, 900)
        a = u(20, "hfoo", ME, 300, 0, 200, 420)     # 缺 220、48%：1 级（治疗 200）也够格，但比例没 b 低
        b = u(21, "hkni", ME, 500, 0, 300, 835)     # 缺 535、36% -> 这个
        foes = [u(10, "hfoo", FOE, 2500, 0)]
        plan = C.plan_hero_cast(hero, foes, [hero, a, b], always_ready)
        self.assertEqual((plan[0], plan[3]), ("holylight", 21))

    def test_holy_light_not_on_buildings(self):
        hero = u(1, "Hpal", ME, 0, 0, 900)
        barracks = u(30, "hbar", ME, 300, 0, 300, 1500)
        self.assertIsNone(C.plan_hero_cast(hero, [], [hero, barracks], always_ready))

    def test_holy_light_prioritizes_ally_currently_under_fire(self):
        hero = u(1, "Hpal", ME, 0, 0, 900)
        low = u(20, "hfoo", ME, 300, 0, 220, 420)
        focused = u(21, "Hmkg", ME, 500, 0, 680, 850)  # 80%，但正在被三人集火
        foes = [u(10+i, "hfoo", FOE, 700, i*30) for i in range(3)]
        ctx = {"focus_counts": {focused.addr: 3}, "recent_hurt": {focused.addr}}
        plan = C.plan_hero_cast(hero, foes, [hero, low, focused], always_ready, ctx=ctx)
        self.assertEqual((plan[0], plan[3]), ("holylight", focused.addr))


class BloodMage(unittest.TestCase):
    def test_flame_strike_on_clump(self):
        hero = u(1, "Hblm", ME, 0, 0, 800)
        clump = [u(10 + i, "hrif", FOE, 500 + 50 * i, 0) for i in range(3)]
        # 09-21 血法师先试凤凰（6 级大招）：这里只学了烈焰风暴
        plan = C.plan_hero_cast(hero, clump, [hero], always_ready, learned={"AHfs": 1})
        self.assertEqual(plan[0], "flamestrike")
        self.assertEqual(plan[2], "point")


class SkillData(unittest.TestCase):
    """09-16 晚：出手条件按当前等级的真实数据（skills.json）。期望里的数字按表算，不手写猜。"""

    def test_table_loaded(self):
        self.assertEqual(C.skill("blizzard", 1)["Area"], 200.0)
        self.assertEqual(C.skill("stormbolt", 2)["DataA"], 225.0)
        self.assertEqual(C.skill("thunderclap", 3)["Area"], 350.0)

    def test_blizzard_need_by_level(self):
        self.assertEqual(C.aoe_need("blizzard", 1), 4)     # 30 x 3 = 90 -> ceil(360/90)
        self.assertEqual(C.aoe_need("blizzard", 2), 3)     # 40 x 3 = 120
        self.assertEqual(C.aoe_need("flamestrike", 2), 2)  # 上限 160 -> ceil(270/160) = 2
        hero = u(1, "Hamg", ME, 0, 0, 800)
        three = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(3)]
        ready = lambda n: n != "waterelemental"
        self.assertIsNone(C.plan_hero_cast(hero, three, [hero], ready, learned={"AHbz": 1}))
        self.assertEqual(C.plan_hero_cast(hero, three, [hero], ready, learned={"AHbz": 2})[0], "blizzard")

    def test_blizzard_zero_own_units_near_the_circle(self):
        hero = u(1, "Hamg", ME, 0, 0, 800)
        clump = [u(10 + i, "hfoo", FOE, 600 + 60 * i, 0) for i in range(4)]
        ready = lambda n: n != "waterelemental"
        one = [hero, u(20, "hfoo", ME, 690, 320)]        # 离重心 320：半径 200 外、但在 200+150 内
        self.assertIsNone(C.plan_hero_cast(hero, clump, one, ready))
        far = [hero, u(20, "hfoo", ME, 690, 400)]        # 400 > 350
        self.assertEqual(C.plan_hero_cast(hero, clump, far, ready)[0], "blizzard")

    def test_storm_bolt_range_600_and_kill_first(self):
        hero = u(1, "Hmkg", ME, 0, 0, 900)
        ready = lambda n: n == "stormbolt"
        self.assertIsNone(C.plan_hero_cast(hero, [u(11, "Hamg", FOE, 750, 0, 500, 800)], [hero], ready))   # 750 > 600+100
        foes = [u(12, "hmpr", FOE, 500, 0, 290, 290), u(13, "hfoo", FOE, 300, 0, 200, 420)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready, learned={"AHtb": 2})[3], 13)    # 225 打得死步兵
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready, learned={"AHtb": 1})[3], 12)    # 100 打不死 -> 施法者

    def test_thunder_clap_area_grows(self):
        hero = u(1, "Hmkg", ME, 0, 0, 900)
        foes = [u(10, "hfoo", FOE, 320, 0), u(11, "hfoo", FOE, 0, 320)]
        ready = lambda n: n == "thunderclap"
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero], ready, learned={"AHtc": 1}))            # 半径 250
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready, learned={"AHtc": 3})[0], "thunderclap")   # 半径 350

    def test_channel_hold(self):
        self.assertAlmostEqual(C.cast_hold_seconds("blizzard", 1), 6.0 / C.GAME_SPEED + 0.3)
        self.assertAlmostEqual(C.cast_hold_seconds("blizzard", 3), 10.0 / C.GAME_SPEED + 0.3)
        self.assertEqual(C.cast_hold_seconds("stormbolt", 1), C.CAST_HOLD)


class Cooldowns(unittest.TestCase):
    def test_cooldown_state(self):
        cds = C.SpellCooldowns()
        self.assertTrue(cds.ready(1, "stormbolt", 10.0))
        cds.cast(1, "stormbolt", 10.0)
        self.assertFalse(cds.ready(1, "stormbolt", 10.0 + C.SPELL_CD["stormbolt"] - 0.1))
        self.assertTrue(cds.ready(1, "stormbolt", 10.0 + C.SPELL_CD["stormbolt"] + 0.1))
        self.assertTrue(cds.ready(2, "stormbolt", 10.0))            # 按英雄分开记

    def test_rejected_order_backs_off(self):
        cds = C.SpellCooldowns()
        cds.rejected(1, "blizzard", 5.0)
        self.assertFalse(cds.ready(1, "blizzard", 5.0 + C.REJECT_BACKOFF - 0.1))
        self.assertTrue(cds.ready(1, "blizzard", 5.0 + C.REJECT_BACKOFF + 0.1))


if __name__ == "__main__":
    unittest.main()

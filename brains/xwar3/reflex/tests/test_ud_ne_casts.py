# -*- coding: utf-8 -*-
"""亡灵 / 暗夜 / 中立英雄的施法规则 —— mod_combat.SPELLS / plan_hero_cast（09-18 第二批）。

此前只会放人族英雄技能，这里补上兽族、亡灵、暗夜精灵英雄的主动技能释放。
第一批（兽族）见 test_orc_casts.py。期望值按规则 + w3data/skills.json **算出来**，算式写在注释里。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE, CREEP = 0, 1, 12


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def ready(_):
    return True


def only(name):
    return lambda n: n == name


class Registered(unittest.TestCase):
    def test_all_melee_heroes_registered(self):
        for hero in ("Udea", "Ulic", "Udre", "Ucrl", "Edem", "Ekee", "Emoo", "Ewar",
                     "Nplh", "Nbrn", "Nbst", "Npbm", "Nngs", "Ntin", "Nalc", "Nfir"):
            self.assertTrue(C.HERO_SPELLS.get(hero), hero)

    def test_level_dependent_data(self):
        self.assertEqual((C.spell_mana("sleep", 1), C.spell_mana("sleep", 3)), (100.0, 50.0))     # AUsl Cost 100/75/50
        self.assertEqual((C.spell_mana("blink", 1), C.spell_mana("blink", 2)), (50.0, 10.0))      # AEbl Cost 50/10/10
        self.assertAlmostEqual(C.cast_hold_seconds("starfall", 1), 45.0 / C.GAME_SPEED + 0.3)       # 15.3 < 上限 16
        self.assertAlmostEqual(C.cast_hold_seconds("deathanddecay", 1), 35.0 / C.GAME_SPEED + 0.3)
        self.assertAlmostEqual(C.cast_hold_seconds("tranquility", 1), 30.0 / C.GAME_SPEED + 0.3)
        self.assertEqual(C.cast_hold_seconds("frostnova", 1), C.CAST_HOLD)
        self.assertEqual(C.aoe_need("fanofknives", 1), 4)       # ceil(270/75)
        self.assertEqual(C.aoe_need("fanofknives", 3), 2)       # ceil(270/190)
        self.assertEqual(C.aoe_need("impale", 2), 3)            # ceil(200/80)
        self.assertEqual(C.aoe_need("rainoffire", 1), 5)        # 25×3=75 -> ceil(360/75)


class DeathKnight(unittest.TestCase):
    def test_coil_heals_undead_ally(self):
        hero = u(1, "Udea", ME, 0, 0, 600)
        ghoul = u(30, "ugho", ME, 300, 0, 100, 340)          # 缺 240 >= 0.6×200
        self.assertEqual(C.plan_hero_cast(hero, [], [hero, ghoul], ready)[:4], ("deathcoil", 0x000D00FE, "target", 30))

    def test_coil_hits_living_hero_not_undead_or_living_ally(self):
        hero = u(1, "Udea", ME, 0, 0, 600)
        grunt = u(31, "ogru", ME, 300, 0, 100, 700)           # 活的自己人：死亡缠绕只奶亡灵
        paladin = u(40, "Hpal", FOE, 500, 0, 500, 700)
        self.assertEqual(C.plan_hero_cast(hero, [paladin], [hero, grunt], ready)[3], 40)
        self.assertIsNone(C.plan_hero_cast(hero, [u(41, "Ulic", FOE, 500, 0, 300, 500)], [hero], ready))   # 亡灵会被奶

    def test_coil_never_on_self(self):
        hero = u(1, "Udea", ME, 0, 0, 100, 600)
        self.assertIsNone(C.plan_hero_cast(hero, [], [hero], ready))

    def test_death_pact_eats_summon_then_dying_unit(self):
        hero = u(1, "Udea", ME, 0, 0, 200, 600)               # 33% <= 45%，缺 400 >= 250
        skel = u(50, "uske", ME, 200, 0, 180)
        ghoul = u(51, "ugho", ME, 100, 0, 300, 340)           # 88%：不吃
        self.assertEqual(C.plan_hero_cast(hero, [], [hero, skel, ghoul], ready)[:2], ("deathpact", 0x000D00FF))
        self.assertEqual(C.plan_hero_cast(hero, [], [hero, skel, ghoul], ready)[3], 50)
        dying = u(51, "ugho", ME, 100, 0, 80, 340)            # 23.5% <= 30%
        acolyte = u(52, "uaco", ME, 50, 0, 20, 220)           # 工人不吃
        self.assertEqual(C.plan_hero_cast(hero, [], [hero, dying, acolyte], ready)[3], 51)

    def test_animate_dead_needs_corpses_and_a_fight(self):
        hero = u(1, "Udea", ME, 0, 0, 600)
        foes = [u(10 + i, "hfoo", FOE, 500, 50 * i) for i in range(4)]
        plan = C.plan_hero_cast(hero, foes, [hero], ready, ctx={"corpses": [(300.0, 0.0)] * 4})
        self.assertEqual(plan[0], "animatedead")
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero], ready, ctx={"corpses": [(300.0, 0.0)] * 3}))


class Lich(unittest.TestCase):
    def test_frost_armor_autocast_once_per_level(self):
        hero = u(1, "Ulic", ME, 0, 0, 500)
        plan = C.plan_hero_cast(hero, [], [hero], ready, learned={"AUfu": 1})
        self.assertEqual((plan[0], plan[1], plan[6]), ("frostarmoron", 0x000D01EA, {"autocast": True, "no_mana": True}))
        self.assertIsNone(C.plan_hero_cast(hero, [], [hero], ready, learned={"AUfu": 1}, ctx={"autocast": {"frostarmoron": 1}}))
        self.assertEqual(C.plan_hero_cast(hero, [], [hero], ready, learned={"AUfu": 2},
                                          ctx={"autocast": {"frostarmoron": 1}})[0], "frostarmoron")
        self.assertIsNone(C.plan_hero_cast(hero, [], [hero], ready, learned=None))    # 读不到等级不开

    def test_frost_nova_kill_hero_then_clump(self):
        hero = u(1, "Ulic", ME, 0, 0, 500)
        clump = [u(10, "hfoo", FOE, 500, 0), u(11, "hfoo", FOE, 550, 50), u(12, "hfoo", FOE, 600, -50)]
        self.assertEqual(C.plan_hero_cast(hero, clump, [hero], ready)[:4], ("frostnova", 0x000D0102, "target", 10))
        weak = u(20, "Hamg", FOE, 700, 0, 140, 600)            # 50 + 100 = 150 >= 140：一下打死
        self.assertEqual(C.plan_hero_cast(hero, clump + [weak], [hero], ready)[3], 20)

    def test_dark_ritual_when_low_mana(self):
        hero = u(1, "Ulic", ME, 0, 0, 500)
        skel = u(50, "uske", ME, 300, 0, 180)
        foe = [u(10, "hfoo", FOE, 1000, 0)]
        self.assertEqual(C.plan_hero_cast(hero, foe, [hero, skel], ready, mana=60.0)[:4],
                         ("darkritual", 0x000D00FB, "target", 50))
        self.assertIsNone(C.plan_hero_cast(hero, foe, [hero, skel], ready, mana=200.0))   # 蓝够；新星 1000 > 900 够不着
        self.assertIsNone(C.plan_hero_cast(hero, foe, [hero, skel], ready, mana=None))    # 读不到蓝不献祭

    def test_death_and_decay_big_clump_no_own_units(self):
        hero = u(1, "Ulic", ME, 0, 0, 500)
        pts = [(800, 0), (850, 50), (900, 0), (850, -50), (820, 20), (880, -20)]
        foes = [u(10 + i, "hfoo", FOE, x, y) for i, (x, y) in enumerate(pts)]
        plan = C.plan_hero_cast(hero, foes, [hero], only("deathanddecay"))
        self.assertEqual((plan[0], plan[2]), ("deathanddecay", "point"))
        self.assertAlmostEqual(plan[4], 850.0)                # 重心 (5100/6, 0)
        self.assertAlmostEqual(plan[5], 0.0)
        ghoul = u(30, "ugho", ME, 850, 300, 340)             # 离重心 300 <= 300 + 150：砸自己人，不放
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero, ghoul], only("deathanddecay")))
        creeps = [u(10 + i, "nogr", CREEP, x, y) for i, (x, y) in enumerate(pts)]
        self.assertIsNone(C.plan_hero_cast(hero, creeps, [hero], only("deathanddecay")))   # 大招不砸野怪


class Dreadlord(unittest.TestCase):
    def test_sleep_unengaged_hero(self):
        hero = u(1, "Udre", ME, 0, 0, 600)
        mage = u(20, "Hamg", FOE, 600, 0, 500, 600)
        self.assertEqual(C.plan_hero_cast(hero, [mage], [hero], ready)[:4], ("sleep", 0x000D0103, "target", 20))
        ghoul = u(30, "ugho", ME, 700, 0, 340)               # 正在打它（100 <= 250）：睡了也马上被打醒
        self.assertIsNone(C.plan_hero_cast(hero, [mage], [hero, ghoul], only("sleep")))

    def test_inferno_on_players_clump(self):
        hero = u(1, "Udre", ME, 0, 0, 600)
        pts = [(700, 0), (750, 50), (750, -50), (800, 0)]
        plan = C.plan_hero_cast(hero, [u(10 + i, "hfoo", FOE, x, y) for i, (x, y) in enumerate(pts)], [hero], ready)
        self.assertEqual((plan[0], plan[1], plan[2]), ("inferno", 0x000D0108, "point"))
        self.assertAlmostEqual(plan[4], 750.0)
        creeps = [u(10 + i, "nogr", CREEP, x, y) for i, (x, y) in enumerate(pts)]
        self.assertIsNone(C.plan_hero_cast(hero, creeps, [hero], only("inferno")))

    def test_carrion_swarm_line(self):
        hero = u(1, "Udre", ME, 0, 0, 600)
        line = [u(10 + i, "hfoo", FOE, 300 + 150 * i, 0) for i in range(4)]   # 1 级 75 -> 要 4 个
        plan = C.plan_hero_cast(hero, line, [hero], only("carrionswarm"))
        self.assertEqual((plan[0], plan[2], plan[4], plan[5]), ("carrionswarm", "point", 300.0, 0.0))
        self.assertIsNone(C.plan_hero_cast(hero, line[:3], [hero], only("carrionswarm")))


class CryptLord(unittest.TestCase):
    def test_impale_ground_line_by_level(self):
        hero = u(1, "Ucrl", ME, 0, 0, 700)
        ground = [u(10, "hfoo", FOE, 300, 0), u(11, "hfoo", FOE, 400, 0)]
        air = [u(12, "hgry", FOE, 500, 0), u(13, "hgry", FOE, 600, 0)]
        self.assertIsNone(C.plan_hero_cast(hero, ground + air, [hero], only("impale")))    # 1 级要 4 个地面，空军不算
        four = ground + [u(14, "hfoo", FOE, 500, 0), u(15, "hfoo", FOE, 600, 0)]
        self.assertEqual(C.plan_hero_cast(hero, four, [hero], only("impale"))[:3], ("impale", 0x000D024B, "point"))
        plan = C.plan_hero_cast(hero, ground + air, [hero], only("impale"), learned={"AUim": 3})   # 3 级 110 -> 2 个
        self.assertEqual(plan[0], "impale")

    def test_locust_swarm_and_scarab_autocast(self):
        hero = u(1, "Ucrl", ME, 0, 0, 700)
        foes = [u(10 + i, "hfoo", FOE, 600, 60 * i) for i in range(5)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[0], "locustswarm")
        self.assertEqual(C.plan_hero_cast(hero, [], [hero], ready, learned={"AUcb": 1})[:2],
                         ("carrionscarabson", 0x000D0248))


class DemonHunter(unittest.TestCase):
    def test_mana_burn_prefers_int_hero(self):
        hero = u(1, "Edem", ME, 0, 0, 600)
        mk = u(20, "Hmkg", FOE, 400, 0, 300, 700)            # 力量英雄、血更少
        am = u(21, "Hamg", FOE, 450, 0, 500, 550)            # 智力英雄
        self.assertEqual(C.plan_hero_cast(hero, [mk, am], [hero], ready)[:4], ("manaburn", 0x000D00D3, "target", 21))
        self.assertIsNone(C.plan_hero_cast(hero, [u(22, "Hamg", FOE, 600, 0, 500)], [hero], only("manaburn")))  # 600 > 300+200

    def test_immolation_on_off(self):
        hero = u(1, "Edem", ME, 0, 0, 600)
        close = [u(10, "hfoo", FOE, 150, 0), u(11, "hfoo", FOE, 200, 0)]     # 160 + 90 = 250 内 2 个
        plan = C.plan_hero_cast(hero, close, [hero], ready)
        self.assertEqual((plan[0], plan[1], plan[6]["toggle"]), ("immolation", 0x000D00D1, True))
        low = {}
        self.assertIsNone(C.plan_hero_cast(hero, close, [hero], ready, mana=50.0, low=low))   # 持续耗蓝，留 60
        self.assertEqual(low, {"immolation": 1})
        on = {"toggles": {"immolation"}}
        self.assertIsNone(C.plan_hero_cast(hero, close, [hero], ready, ctx=on))               # 开着、还在打：不动
        quiet = [u(10, "hfoo", FOE, 1000, 0)]
        off = C.plan_hero_cast(hero, quiet, [hero], ready, mana=0.0, ctx=on)                  # 900 内没人：关，不看蓝
        self.assertEqual((off[0], off[1], off[6]["toggle"]), ("immolation", 0x000D00D2, False))
        stale = {"toggles": {"immolation"}, "toggle_stale": {"immolation"}}
        self.assertEqual(C.plan_hero_cast(hero, close, [hero], ready, ctx=stale)[1], 0x000D00D1)   # 太久没确认：再开一次

    def test_metamorphosis_big_fight_players_only(self):
        hero = u(1, "Edem", ME, 0, 0, 600)
        foes = [u(10 + i, "hfoo", FOE, 800, 50 * i) for i in range(6)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[0], "metamorphosis")
        creeps = [u(10 + i, "nogr", CREEP, 800, 50 * i) for i in range(6)]
        self.assertIsNone(C.plan_hero_cast(hero, creeps, [hero], ready))


class Keeper(unittest.TestCase):
    def test_roots_hero_then_big_ground_unit(self):
        hero = u(1, "Ekee", ME, 0, 0, 600)
        self.assertEqual(C.plan_hero_cast(hero, [u(20, "Hamg", FOE, 700, 0, 500)], [hero], ready)[:4],
                         ("entanglingroots", 0x000D00CB, "target", 20))
        foes = [u(30, "hgry", FOE, 400, 0, 825), u(31, "hkni", FOE, 500, 0, 835)]    # 狮鹫是空军，缠不住
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[3], 31)

    def test_tranquility_when_team_hurt_and_safe(self):
        hero = u(1, "Ekee", ME, 0, 0, 600)
        hurt = [u(30 + i, "hfoo", ME, 200, 100 * i, 100, 420) for i in range(4)]    # 缺 320×4 = 1280 >= 1000
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 1000, 0)], [hero] + hurt, ready)
        self.assertEqual(plan[:2], ("tranquility", 0x000D00D8))
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 300, 0)], [hero] + hurt, ready))   # 450 内有敌人


class Priestess(unittest.TestCase):
    def test_searing_arrows_autocast(self):
        hero = u(1, "Emoo", ME, 0, 0, 600)
        self.assertEqual(C.plan_hero_cast(hero, [], [hero], ready, learned={"AHfa": 1})[:2], ("flamingarrows", 0x000D00CE))

    def test_starfall_conditions(self):
        hero = u(1, "Emoo", ME, 0, 0, 700)
        far = [u(10 + i, "hfoo", FOE, 600, 60 * i) for i in range(5)]
        self.assertEqual(C.plan_hero_cast(hero, far, [hero], ready)[:2], ("starfall", 0x000D00D7))
        crowded = [u(10, "hfoo", FOE, 250, 0), u(11, "hfoo", FOE, 0, 250)] + far[:3]    # 300 内 2 个 > 1
        self.assertIsNone(C.plan_hero_cast(hero, crowded, [hero], ready))
        weak = u(1, "Emoo", ME, 0, 0, 300, 700)                                          # 43% < 50%
        self.assertIsNone(C.plan_hero_cast(weak, far, [weak], ready))


class Warden(unittest.TestCase):
    def test_blink_away_when_pulled_then_keep_running(self):
        hero = u(1, "Ewar", ME, 0, 0, 500)
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 300, 0)], [hero], ready,
                                ctx={"pulled": True, "home": (-3000.0, 0.0)})
        # 背离敌人 (-1, 0)；1 级最远 1000 - 50 = 950，取 min(950, 900)
        self.assertEqual(plan[:6], ("blink", 0x000D022D, "point", None, -900.0, 0.0))
        self.assertEqual(plan[6], {"then": ("move", -1300.0, 0.0), "then_delay": 0.5})

    def test_blink_low_hp_and_toward_home_when_surrounded(self):
        hero = u(1, "Ewar", ME, 0, 0, 150, 500)                                          # 30% < 35%
        self.assertEqual(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 300, 0)], [hero], ready)[4:6], (-900.0, 0.0))
        around = [u(10, "hfoo", FOE, 0, 300), u(11, "hfoo", FOE, 0, -280)]               # 重心 (0, 10) 贴着自己 -> 朝家
        plan = C.plan_hero_cast(hero, around, [hero], ready, ctx={"home": (0.0, 5000.0)})
        self.assertEqual(plan[4:6], (0.0, 900.0))

    def test_blink_chase(self):
        hero = u(1, "Ewar", ME, 0, 0, 500)
        prey = u(20, "Hpal", FOE, 800, 0, 140, 700)                                      # 20%，800 在 450~950
        plan = C.plan_hero_cast(hero, [prey], [hero], ready)
        self.assertEqual((plan[0], plan[4], plan[5], plan[6]["then"]), ("blink", 700.0, 0.0, ("attack", 20)))

    def test_vengeance_fan_and_shadow_strike(self):
        hero = u(1, "Ewar", ME, 0, 0, 500)
        five = [u(10 + i, "hfoo", FOE, 700, 60 * i) for i in range(5)]
        self.assertEqual(C.plan_hero_cast(hero, five, [hero], ready)[0], "spiritofvengeance")
        four = [u(10 + i, "hfoo", FOE, 200, 60 * i) for i in range(4)]                   # 400 内 4 个（1 级要 4）
        self.assertEqual(C.plan_hero_cast(hero, four, [hero], ready)[0], "fanofknives")
        self.assertIsNone(C.plan_hero_cast(hero, four[:3], [hero], ready))
        weak = u(20, "Hamg", FOE, 400, 0, 120, 600)          # 75 + 10×5 = 125 >= 120；400 < 450 不闪
        self.assertEqual(C.plan_hero_cast(hero, [weak], [hero], ready)[:4], ("shadowstrike", 0x000D022F, "target", 20))


class Neutral(unittest.TestCase):
    def test_pit_lord(self):
        hero = u(1, "Nplh", ME, 0, 0, 800)
        pts = [(600, 0), (650, 50), (650, -50), (700, 0), (650, 0)]
        foes = [u(10 + i, "hfoo", FOE, x, y) for i, (x, y) in enumerate(pts)]
        plan = C.plan_hero_cast(hero, foes, [hero], only("rainoffire"))
        self.assertEqual((plan[0], plan[1], plan[4], plan[5]), ("rainoffire", 0x000D010E, 650.0, 0.0))
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero, u(30, "hfoo", ME, 650, 200)], only("rainoffire")))
        howl = [u(10 + i, "hfoo", FOE, 400, 60 * i) for i in range(4)]
        self.assertEqual(C.plan_hero_cast(hero, howl, [hero], only("howlofterror"))[0], "howlofterror")

    def test_dark_ranger_silence_needs_caster(self):
        hero = u(1, "Nbrn", ME, 0, 0, 600)
        three = [u(10, "hfoo", FOE, 700, 0), u(11, "hfoo", FOE, 750, 50), u(12, "hfoo", FOE, 750, -50)]
        self.assertIsNone(C.plan_hero_cast(hero, three, [hero], ready))
        with_priest = three[:2] + [u(12, "hmpr", FOE, 750, -50, 290)]
        self.assertEqual(C.plan_hero_cast(hero, with_priest, [hero], ready)[:3], ("silence", 0x000D0270, "point"))
        self.assertEqual(C.plan_hero_cast(hero, [], [hero], ready, learned={"ANba": 1})[0], "blackarrowon")

    def test_beastmaster_naga_tinker_alchemist_firelord(self):
        foe = [u(10, "hfoo", FOE, 800, 0)]
        bm = u(1, "Nbst", ME, 0, 0, 700)
        self.assertEqual(C.plan_hero_cast(bm, foe, [bm], ready)[:3], ("summongrizzly", 0x000D0272, "instant"))
        naga = u(1, "Nngs", ME, 0, 0, 300, 700)
        self.assertEqual(C.plan_hero_cast(naga, [u(10, "hfoo", FOE, 500, 0)], [naga], ready, ctx={"pulled": True})[0],
                         "manashieldon")
        pair = [u(10, "hfoo", FOE, 400, 0), u(11, "hfoo", FOE, 450, 80)]
        self.assertEqual(C.plan_hero_cast(u(1, "Nngs", ME, 0, 0, 700), pair, [], ready)[:4],
                         ("forkedlightning", 0x000D026B, "target", 10))
        tink = u(1, "Ntin", ME, 0, 0, 600)
        self.assertEqual(C.plan_hero_cast(tink, foe, [tink], ready)[:6], ("summonfactory", 0x000D02B2, "point", None, 200.0, 0.0))
        alc = u(1, "Nalc", ME, 0, 0, 700)
        self.assertEqual(C.plan_hero_cast(alc, [u(10 + i, "nogr", CREEP, 300, 60 * i) for i in range(3)], [alc], ready)[0],
                         "chemicalrage")
        fl = u(1, "Nfir", ME, 0, 0, 600)
        self.assertEqual(C.plan_hero_cast(fl, [u(20, "Hmkg", FOE, 500, 0, 300, 700), u(21, "Hamg", FOE, 600, 0, 500)],
                                          [fl], ready)[3], 21)


if __name__ == "__main__":
    unittest.main()

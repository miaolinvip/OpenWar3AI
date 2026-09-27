# -*- coding: utf-8 -*-
"""兽族四个英雄的施法规则 + 技能表自检 —— mod_combat.SPELLS / plan_hero_cast（09-18）。

09-18 发现：5 号实例这局和下局都是兽族；剑圣学了疾风步×2，先知学了闪电链×2，
而 mod_combat 心跳每分钟都是「英雄施法 0 次」—— HERO_SPELLS 里只有人族四个英雄。
期望值按规则 + w3data/skills.json 的数据**算出来**（注释里写了算式），不是凭印象写的。
"""
import json
import pathlib
import re
import sys
import unittest
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE, CREEP = 0, 1, 12
ROOT = pathlib.Path(__file__).resolve().parents[2]
REPLAY_DICT = ROOT / "war3-analytics" / "w3data" / "replay_dict.json"
ORDER_IDS = ROOT / "war3-game-analysis" / "order-ids.txt"


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def ready(_):
    return True


class Table(unittest.TestCase):
    def test_every_hero_spell_is_in_table_and_rule_exists(self):
        for hero, names in C.HERO_SPELLS.items():
            for n in names:
                self.assertIn(n, C.SPELLS, f"{hero} {n}")
        for n, sp in C.SPELLS.items():
            self.assertIn(sp["rule"], C._RULES, n)
            self.assertIn(sp["how"], ("instant", "point", "target", "autocast", "toggle"), n)
            self.assertIn(sp["abil"], C.SKILLS, n)          # 技能代码在 skills.json 里（拼错会一直按 1 级兜底）

    def test_orc_heroes_registered(self):
        for hero in ("Obla", "Ofar", "Otch", "Oshd"):
            self.assertTrue(C.HERO_SPELLS.get(hero), hero)

    @unittest.skipUnless(REPLAY_DICT.exists() and ORDER_IDS.exists(), "没有 replay_dict.json / order-ids.txt")
    def test_order_ids_match_game_order_names(self):
        # 技能代码 -> 游戏 *AbilityFunc.txt 里的 Order/Orderon/Orderoff 名 -> order-ids.txt 的号；表里的号必须是其中之一
        d = json.loads(REPLAY_DICT.read_text(encoding="utf-8"))
        by_abil = {}
        for oname, codes in d["orders"].items():
            for c in codes:
                by_abil.setdefault(c, set()).add(oname)
        ids = {}
        for line in ORDER_IDS.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\w+)\s+0x([0-9A-Fa-f]{8})", line)
            if m:
                ids[m.group(1)] = int(m.group(2), 16)
        # 录像字典里没有的技能（没人在录像里放过）：只核对号码等于 order-ids.txt 里那个订单名，实机靠施法回执确认。
        #   09-21 法力虹吸 AHdr：订单名 drain（HumanAbilityFunc [AHdr] Order=drain；黑暗游侠吸血 ANdr 同名）
        no_replay = {"siphonmana": "drain"}
        for n, sp in C.SPELLS.items():
            if n in no_replay:
                self.assertEqual(sp["order"], ids[no_replay[n]], n)
                continue
            valid = {ids[o] for o in by_abil.get(sp["abil"], ()) if o in ids}
            self.assertIn(sp["order"], valid, f"{n} {sp['abil']} {sp['order']:08X} 不在 {sorted(hex(v) for v in valid)}")
            if "off" in sp["opts"]:
                self.assertIn(sp["opts"]["off"], valid, n)

    def test_derived_views_cover_table(self):
        self.assertEqual(set(C.SPELL_ORDERS), set(C.SPELLS))
        self.assertEqual(set(C.SPELL_ABIL), set(C.SPELLS))
        self.assertEqual(set(C.SPELL_MANA), set(C.SPELLS))
        self.assertEqual(set(C.SPELL_CD), set(C.SPELLS))

    def test_mana_and_cooldown_from_data(self):
        self.assertEqual(C.spell_mana("windwalk", 2), 75.0)          # AOwk Cost 75
        self.assertEqual(C.spell_mana("chainlightning", 1), 120.0)   # AOcl Cost 120
        # 疾风步 Cool 5，按隐身时长 min(Dur 20/35/50, 20) = 20 游戏秒节流 -> 20 / 3 墙钟秒
        self.assertAlmostEqual(C.spell_cd("windwalk", 2), 20.0 / C.GAME_SPEED)
        self.assertAlmostEqual(C.spell_cd("chainlightning", 1), 9.0 / C.GAME_SPEED)   # AOcl Cool 9
        self.assertEqual(C.spell_cd("stormbolt", 3), C.SPELL_CD["stormbolt"])          # 人族照旧用原常数


class Blademaster(unittest.TestCase):
    def test_windwalk_when_low_and_enemy_near(self):
        hero = u(1, "Obla", ME, 0, 0, 200, 700)          # 28.6% < 35%，600 内有敌人
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 400, 0)], [hero], ready)
        self.assertEqual((plan[0], plan[2], len(plan)), ("windwalk", "instant", 6))   # 逃跑：不带后续，原来的回家令照走

    def test_no_windwalk_when_healthy(self):
        hero = u(1, "Obla", ME, 0, 0, 700)
        self.assertIsNone(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 400, 0)], [hero], ready))

    def test_pulled_hero_only_escape_spells(self):
        hero = u(1, "Obla", ME, 0, 0, 200, 700)
        foes = [u(10, "hfoo", FOE, 300, 0), u(11, "hfoo", FOE, 300, 60)]
        no_ww = lambda n: n != "windwalk"  # noqa: E731
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], no_ww)[0], "mirrorimage")   # 40% 以下、500 内 2 个
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero], no_ww, ctx={"pulled": True}))   # 撤退中不放镜像（会停下）
        hero_ok = u(1, "Obla", ME, 0, 0, 600, 700)                  # 撤退中：不看血，700 内有敌人就隐身
        self.assertEqual(C.plan_hero_cast(hero_ok, [u(10, "hfoo", FOE, 650, 0)], [hero_ok], ready,
                                          ctx={"pulled": True})[0], "windwalk")

    def test_windwalk_opener(self):
        hero = u(1, "Obla", ME, 0, 0, 700)
        foot = u(10, "hfoo", FOE, 500, 0)
        plan = C.plan_hero_cast(hero, [foot], [hero], ready, mana=150.0)
        self.assertEqual((plan[0], plan[6]["then"]), ("windwalk", ("attack", 10)))     # 满血、蓝够两次：隐身开场砍它
        self.assertIsNone(C.plan_hero_cast(hero, [foot], [hero], ready, mana=149.0))   # 只够一次：留着逃跑
        self.assertIsNone(C.plan_hero_cast(hero, [foot], [hero], ready))                # 蓝读不到不放
        hurt = u(1, "Obla", ME, 0, 0, 400, 700)                                          # 57% < 60%
        self.assertIsNone(C.plan_hero_cast(hurt, [foot], [hurt], lambda n: n == "windwalk", mana=300.0))
        self.assertIsNone(C.plan_hero_cast(hero, [foot, u(11, "hfoo", FOE, 150, 0)], [hero], ready, mana=300.0))   # 已经贴身

    def test_windwalk_opener_targets(self):
        hero = u(1, "Obla", ME, 0, 0, 700)
        creep = u(10, "nogr", 12, 600, 0, 600, 600)
        self.assertIsNone(C.plan_hero_cast(hero, [creep], [hero], ready, mana=300.0))   # 野怪：自己的兵还没贴上去
        grunt = u(2, "ogru", ME, 650, 50)
        plan = C.plan_hero_cast(hero, [creep], [hero, grunt], ready, mana=300.0)
        self.assertEqual(plan[6]["then"], ("attack", 10))
        foes = [u(20, "hfoo", FOE, 400, 0), u(21, "hmpr", FOE, 700, 0), u(22, "Hpal", FOE, 750, 0, 650, 650)]
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready, mana=300.0)[6]["then"], ("attack", 22))   # 英雄优先
        self.assertEqual(C.plan_hero_cast(hero, foes[:2], [hero], ready, mana=300.0)[6]["then"], ("attack", 21))   # 再施法者
        air = [u(30, "hgyr", FOE, 500, 0)]
        self.assertIsNone(C.plan_hero_cast(hero, air, [hero], ready, mana=300.0))       # 近战打不到空军

    def test_windwalk_chase_low_enemy_hero(self):
        hero = u(1, "Obla", ME, 0, 0, 700)
        prey = u(20, "Hpal", FOE, 600, 0, 200, 700)      # 28.6% <= 30%，离 600（300~900）
        plan = C.plan_hero_cast(hero, [prey], [hero], ready)
        self.assertEqual((plan[0], plan[6]["then"]), ("windwalk", ("attack", 20)))
        crowd = [u(30 + i, "hfoo", FOE, 650, 40 * i) for i in range(3)]   # 它身边 3 个兵 > CHASE_CROWD
        self.assertIsNone(C.plan_hero_cast(hero, [prey] + crowd, [hero], lambda n: n == "windwalk"))

    def test_bladestorm_players_only(self):
        hero = u(1, "Obla", ME, 0, 0, 700)
        foes = [u(10 + i, "hfoo", FOE, 150, 40 * i) for i in range(3)]   # 250（Area 200 + 50）内 3 个地面兵
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[0], "bladestorm")
        creeps = [u(10 + i, "nogr", CREEP, 150, 40 * i) for i in range(3)]
        self.assertIsNone(C.plan_hero_cast(hero, creeps, [hero], ready))


class FarSeer(unittest.TestCase):
    def test_spirit_wolf_on_contact(self):
        hero = u(1, "Ofar", ME, 0, 0, 600)
        self.assertEqual(C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 800, 0)], [hero], ready)[0], "spiritwolf")

    def test_chain_lightning_on_clump(self):
        hero = u(1, "Ofar", ME, 0, 0, 600)
        clump = [u(10, "hfoo", FOE, 600, 0, 420), u(11, "hfoo", FOE, 650, 50, 420), u(12, "hfoo", FOE, 700, -50, 420)]
        no_wolf = lambda n: n != "spiritwolf"  # noqa: E731
        plan = C.plan_hero_cast(hero, clump, [hero], no_wolf)
        self.assertEqual((plan[0], plan[2], plan[3]), ("chainlightning", "target", 10))   # 500 内 3 个一样多 -> 血上限一样 -> 第一个
        self.assertIsNone(C.plan_hero_cast(hero, clump[:1], [hero], no_wolf))   # 单个步兵、打不死：不浪费 120 蓝

    def test_chain_lightning_prefers_enemy_hero(self):
        hero = u(1, "Ofar", ME, 0, 0, 600)
        foes = [u(10, "hfoo", FOE, 300, 0), u(20, "Hamg", FOE, 750, 0, 450, 600)]   # 750 <= 700 + 100
        plan = C.plan_hero_cast(hero, foes, [hero], lambda n: n == "chainlightning")
        self.assertEqual(plan[3], 20)

    def test_earthquake_on_buildings(self):
        hero = u(1, "Ofar", ME, 0, 0, 600)
        blds = [u(40, "hbar", FOE, 900, 0, 1500), u(41, "hhou", FOE, 1100, 100, 500)]   # 两座相距 224 <= 400
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 1000, 0)], [hero], lambda n: n == "earthquake",
                                ctx={"foe_buildings": blds})
        self.assertEqual((plan[0], plan[2]), ("earthquake", "point"))
        self.assertAlmostEqual(plan[4], 1000.0)
        self.assertAlmostEqual(plan[5], 50.0)


class TaurenChieftain(unittest.TestCase):
    def test_war_stomp_surrounded(self):
        hero = u(1, "Otch", ME, 0, 0, 900)
        foes = [u(10 + i, "hfoo", FOE, 100 + 50 * i, 0) for i in range(3)]   # 250 内 3 个
        self.assertEqual(C.plan_hero_cast(hero, foes, [hero], ready)[0], "stomp")
        self.assertIsNone(C.plan_hero_cast(hero, foes[:2], [hero], ready))       # 2 个不够，震荡波 1 级要 4 个
        with_hero = [foes[0], u(20, "Hpal", FOE, 150, 80, 700)]
        self.assertEqual(C.plan_hero_cast(hero, with_hero, [hero], ready)[0], "stomp")   # 有英雄 2 个就够

    def test_shockwave_along_the_line(self):
        hero = u(1, "Otch", ME, 0, 0, 900)
        line = [u(10 + i, "hfoo", FOE, 300 + 150 * i, 0) for i in range(4)]   # 300..750，都在 800 长、125 半宽里
        plan = C.plan_hero_cast(hero, line, [hero], ready)
        # 1 级伤害 75 -> ceil(300/75) = 4 个；第一个方向（朝 300 那个）就 4 个，落点 = 它的位置
        self.assertEqual((plan[0], plan[2]), ("shockwave", "point"))
        self.assertAlmostEqual(plan[4], 300.0)
        self.assertAlmostEqual(plan[5], 0.0)
        self.assertIsNone(C.plan_hero_cast(hero, line[:3], [hero], ready, learned={"AOsh": 1, "AOws": 1}))
        self.assertEqual(C.plan_hero_cast(hero, line[:2], [hero], ready, learned={"AOsh": 3, "AOws": 1})[0],
                         "shockwave")    # 3 级 200 -> ceil(300/200) = 2


class ShadowHunter(unittest.TestCase):
    def test_healing_wave_on_hurt_ally_or_self(self):
        hero = u(1, "Oshd", ME, 0, 0, 500)
        grunt = u(20, "ogru", ME, 300, 0, 200, 700)       # 缺 500 >= 0.6×130；28.6%
        plan = C.plan_hero_cast(hero, [], [hero, grunt], ready)
        self.assertEqual((plan[0], plan[3]), ("healingwave", 20))
        hurt_self = u(1, "Oshd", ME, 0, 0, 200, 500)       # 治疗波可以奶自己
        self.assertEqual(C.plan_hero_cast(hurt_self, [], [hurt_self], ready)[3], 1)

    def test_hex_enemy_hero_then_big_unit_not_immune(self):
        hero = u(1, "Oshd", ME, 0, 0, 500)
        foes = [u(10, "hfoo", FOE, 300, 0), u(20, "Hamg", FOE, 850, 0, 500, 600)]   # 850 <= 800 + 100
        plan = C.plan_hero_cast(hero, foes, [hero], ready)
        self.assertEqual((plan[0], plan[3]), ("hex", 20))
        big = [u(30, "hspt", FOE, 300, 0, 600, 900), u(31, "hkni", FOE, 400, 0, 835)]   # 破法者魔免（血上限故意比骑士高，不过滤就会选它）
        self.assertEqual(C.plan_hero_cast(hero, big, [hero], lambda n: n == "hex")[3], 31)

    def test_serpent_ward_toward_nearest_enemy(self):
        hero = u(1, "Oshd", ME, 0, 0, 500)
        plan = C.plan_hero_cast(hero, [u(10, "hfoo", FOE, 600, 0)], [hero], ready)
        self.assertEqual((plan[0], plan[2]), ("serpentward", "point"))    # 妖术没目标（步兵 400 血 < 700）
        self.assertAlmostEqual(plan[4], 300.0)                            # min(300, 600 - 150)

    def test_voodoo_when_team_is_dying(self):
        hero = u(1, "Oshd", ME, 0, 0, 500)
        mine = [hero] + [u(20 + i, "ogru", ME, 100 * i, 100, 150, 700) for i in range(4)]
        foes = [u(40 + i, "hfoo", FOE, 400, 60 * i) for i in range(5)]
        self.assertEqual(C.plan_hero_cast(hero, foes, mine, ready)[0], "voodoo")


class Bookkeeping(unittest.TestCase):
    def test_skill_stats_line(self):
        line = C.skill_stats(Counter(windwalk=3, chainlightning=1), Counter(windwalk=2), Counter(chainlightning=1),
                             {"stomp": 4}, {"chainlightning": 2})
        self.assertEqual(line, "技能 下令/进冷却/被退/没学/缺蓝：windwalk 3/2/0/0/0、chainlightning 1/0/1/0/2、stomp 0/0/0/4/0")
        self.assertIsNone(re.search(r"被退 (\d+)", line))     # match_review 用它取总数，别被这一行抢先匹配

    def test_followups(self):
        class Cmd:
            def __init__(self):
                self.calls = []

            def move_to(self, a, x, y, order=None):
                self.calls.append(("move", a, x, y, order))
                return True

            def target_order(self, a, t, order, x, y):
                self.calls.append(("attack", a, t, order))
                return True

        class Claims:
            def take(self, *a, **k):
                return True

            def give_back(self, a):
                pass

        hero = u(1, "Ewar", ME, 0, 0, 500)
        prey = u(20, "Hpal", FOE, 300, 0, 100, 700)
        cmd = Cmd()
        pending = [(10.0, 1, ("attack", 20), "windwalk"), (10.0, 1, ("move", 50.0, 60.0), "blink"),
                   (99.0, 1, ("attack", 20), "windwalk")]
        keep, done = C.run_followups(pending, 10.5, cmd, Claims(), [hero], [hero, prey], {})
        self.assertEqual(keep, [pending[2]])                          # 没到期的留着
        self.assertEqual(cmd.calls, [("attack", 1, 20, C.ORDER_ATTACK)])   # 没在撤：追击照打，闪烁后的"接着跑"不下
        self.assertEqual(done, Counter({"windwalk·attack·ok": 1, "blink·move·不在撤了": 1}))
        cmd2 = Cmd()
        _keep, done2 = C.run_followups(pending[:2], 10.5, cmd2, Claims(), [hero], [hero, prey], {1: 20.0})
        self.assertEqual(cmd2.calls, [("move", 1, 50.0, 60.0, C.ORDER_MOVE)])  # 在撤：用明确 move 接着跑，不去打
        self.assertEqual(done2, Counter({"windwalk·attack·撤退中": 1, "blink·move·ok": 1}))

    def test_corpse_events(self):
        prev = {2: u(2, "ogru", ME, 10, 10, 300, 700), 3: u(3, "hfoo", FOE, 20, 20, 400, 420),
                4: u(4, "hfoo", FOE, 30, 30, 100, 420), 5: u(5, "Hpal", FOE, 40, 40, 50, 700),
                6: u(6, "ogru", ME, 50, 50, 700)}
        now_units = [u(6, "ogru", ME, 50, 50, 700), u(4, "hfoo", FOE, 30, 30, 0, 420)]
        alive, died = C.corpse_events(prev, now_units, ME)
        # 2 自己的不见了 -> 算；3 别人的满血不见了 -> 可能进迷雾，不算；4 血 0 -> 算；5 英雄不留尸体
        self.assertEqual(sorted(died), [(10.0, 10.0), (30.0, 30.0)])
        self.assertEqual(set(alive), {6})


if __name__ == "__main__":
    unittest.main()

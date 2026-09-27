# -*- coding: utf-8 -*-
"""战况触发 eventtalk.py：一秒一秒喂假快照，看认不认得出事、挑谁说、冷却/间隔/只在镜头里（不连游戏）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eventtalk as E  # noqa: E402

CAM = {'x': 0.0, 'y': 0.0, 'dist': 2600.0, 'pitch': 304.0, 'fov': 70.0, 'yaw': 90.0}


def unit(addr, type_, x, y, owner=0, hp=400, hp_max=400):
    return SimpleNamespace(addr=addr, type=type_, x=x, y=y, owner=owner, hp=hp, hp_max=hp_max, order=None)


class Rig:
    def __init__(self, cfg=None, cam=CAM):
        self.t = 0.0
        self.units = []
        self.food = 20
        self.epoch = 1
        self.sent, self.prompts = [], []
        self.e = E.EventTalk(5, snapshot=self.snap, camera=lambda: cam, llm=self.llm, send=self.sent.extend,
                             config=cfg or E.EventTalkConfig(), sleep=lambda s: None, clock=lambda: self.t)

    def snap(self):
        return SimpleNamespace(in_game=True, me=0, epoch=self.epoch, gold=300, lumber=100, food_used=self.food,
                               food_cap=80, units=list(self.units))

    def llm(self, messages, max_tokens, temperature):
        self.prompts.append(messages)
        return '台词%d' % len(self.prompts)

    def tick(self, dt=1.0):
        self.t += dt
        self.e.tick()

    def results(self, kind):
        return [x['result'] for x in self.e.log if x['kind'] == kind]


def base():
    return [unit(0x1, 'htow', -300, -200, hp=1500, hp_max=1500), unit(0x10, 'Hamg', 0, 0),
            unit(0x20, 'hfoo', 60, 0), unit(0x21, 'hfoo', 120, 0), unit(0x22, 'hrif', 0, 60),
            unit(0x30, 'hpea', -250, -100)]


def foes(dx=5000):
    return [unit(0x90 + i, 'ogru', dx + 50 * i, 0, owner=1) for i in range(3)] + \
           [unit(0x99, 'Obla', dx, 80, owner=1)]


def field_army():
    """部队拉到 x+800 去打（离大厅 1400 以上，不触发「家被打」；投影 u 约 0.8~0.92，还在镜头里）。"""
    return [u if u.type in ('htow', 'hpea') else unit(u.addr, u.type, u.x + 800, u.y) for u in base()]


class FightTest(unittest.TestCase):
    def test_fight_start_then_end_with_losses(self):
        r = Rig()
        r.units = field_army() + foes()
        r.tick()                                            # 第一秒只记底
        self.assertEqual(r.sent, [])
        r.units = field_army() + foes(dx=1100)              # 撞上了
        r.tick()
        self.assertEqual(r.results('fight_start'), ['说了'])
        self.assertEqual(r.sent[0].unit, 0x10)              # 我方英雄喊
        self.assertEqual(r.sent[0].name, '')
        self.assertIn('开战了：我方', r.prompts[0][1]['content'])
        self.assertEqual(len(r.sent), 2)                    # replies=1：一个兵接一句
        # 打完：对面倒下 2 个步兵、我方倒下 1 个火枪手，其余脱离接触 6 秒以上
        r.units = [u for u in field_army() if u.addr != 0x22] + [unit(0x92, 'ogru', 5000, 0, owner=1),
                                                                  unit(0x99, 'Obla', 5000, 80, owner=1)]
        for _ in range(8):
            r.tick()
        self.assertEqual(r.results('fight_end'), ['说了'])
        facts = [x['facts'] for x in r.e.log if x['kind'] == 'fight_end'][0]
        self.assertIn('我方倒下 1 个（矮人火枪手）', facts)
        self.assertIn('对面倒下 2 个（兽族步兵×2）', facts)
        self.assertIn('我方占了便宜', facts)

    def test_cooldown_and_foe_taunt(self):
        cfg = E.EventTalkConfig(include_foes=True)
        r = Rig(cfg)
        r.units = field_army() + foes()
        r.tick()
        r.units = field_army() + foes(dx=1100)
        r.tick()
        roles = [x['role'] for x in r.e.transcript]
        self.assertEqual(roles[:2], ['foe', 'lead'])        # 敌方英雄先放狠话，我方英雄回
        self.assertEqual(r.sent[0].unit, 0x99)
        # 脱离 -> 打完 -> 马上又撞上：开战还在冷却（25 秒）
        r.units = field_army() + foes()
        for _ in range(8):
            r.tick()
        r.units = field_army() + foes(dx=1100)
        r.tick()
        self.assertEqual(r.results('fight_start'), ['说了', '冷却中'])


class PriorityTest(unittest.TestCase):
    def test_fight_at_home_base_first_then_fight(self):
        # 在家门口打起来：「家被打」（优先级 2）先说，「开战」（3）排队，过了最小间隔（4 秒）再说
        r = Rig()
        r.units = base() + foes()
        r.tick()
        r.units = base() + foes(dx=300)
        r.tick()
        self.assertEqual(r.results('base_attacked'), ['说了'])
        self.assertEqual(r.results('fight_start'), [])
        r.tick(4)
        self.assertEqual(r.results('fight_start'), ['说了'])


class HeroTest(unittest.TestCase):
    def test_dead_then_back_and_low(self):
        r = Rig()
        r.units = base()
        r.tick()
        r.units = [u for u in base() if u.addr != 0x10]      # 英雄不见了
        r.tick()
        self.assertEqual(r.results('hero_dead'), [])        # 只不见 1 秒不算
        r.tick()
        self.assertEqual(r.results('hero_dead'), ['说了'])
        self.assertIn(r.sent[-1].unit, (0x20, 0x21, 0x22))  # 旁边的兵来说
        self.assertIn('首领大魔法师刚刚阵亡了', r.prompts[-1][1]['content'])
        r.units = base()
        r.tick(5)                                           # 过了最小间隔
        self.assertEqual(r.results('hero_back'), ['说了'])
        r.units = [u if u.addr != 0x10 else unit(0x10, 'Hamg', 0, 0, hp=100) for u in base()]
        r.tick(5)
        self.assertEqual(r.results('hero_low'), ['说了'])
        self.assertIn('血只剩 25%', [x['facts'] for x in r.e.log if x['kind'] == 'hero_low'][0])


class EconomyTest(unittest.TestCase):
    def test_tier_expand_upkeep(self):
        r = Rig()
        r.units = base()
        r.tick()
        r.units = [u if u.addr != 0x1 else unit(0x1, 'hkee', -300, -200, hp=1500, hp_max=1500) for u in base()]
        r.tick()
        self.assertEqual(r.results('tier_up'), ['说了'])
        self.assertEqual(r.sent[0].unit, 0x30)              # 大厅边上的农民先说
        r.units = r.units + [unit(0x2, 'htow', 400, 300, hp=200, hp_max=1500), unit(0x31, 'hpea', 380, 250)]
        r.tick(5)
        self.assertEqual(r.results('expand'), ['说了'])
        r.food = 52
        r.tick(5)
        self.assertEqual(r.results('upkeep'), ['说了'])
        self.assertIn('过了 50 的坎', [x['facts'] for x in r.e.log if x['kind'] == 'upkeep'][0])

    def test_min_gap_queues_second_event(self):
        r = Rig()
        r.units = base()
        r.tick()
        r.units = [u if u.addr != 0x1 else unit(0x1, 'hkee', -300, -200, hp=1500, hp_max=1500) for u in base()]
        r.food = 52                                         # 同一秒：升本 + 人口过坎
        r.tick()
        self.assertEqual(r.results('tier_up'), ['说了'])     # 升本优先级高，先说
        self.assertEqual(r.results('upkeep'), [])
        r.tick(1)                                           # 还没过最小间隔（4 秒）
        self.assertEqual(r.results('upkeep'), [])
        r.tick(4)
        self.assertEqual(r.results('upkeep'), ['说了'])


class FilterTest(unittest.TestCase):
    def test_out_of_view_is_skipped(self):
        r = Rig(cam=dict(CAM, x=20000.0))                   # 镜头在别处，那边也没有自己人
        r.units = base()
        r.tick()
        r.units = [u if u.addr != 0x1 else unit(0x1, 'hkee', -300, -200, hp=1500, hp_max=1500) for u in base()]
        r.tick()
        self.assertEqual(r.results('tier_up'), ['相关的人不在镜头里'])
        self.assertEqual(r.sent, [])

    def test_bystander_in_view_reacts_to_offscreen_news(self):
        # 英雄在镜头外阵亡，镜头里另有一队兵：他们当旁观者说
        r = Rig(cam=dict(CAM, x=3000.0))
        squad = [unit(0x60, 'hfoo', 3000, 0), unit(0x61, 'hrif', 3050, 40)]
        r.units = base() + squad
        r.tick()
        r.units = [u for u in base() if u.addr != 0x10] + squad
        r.tick()
        r.tick()
        self.assertEqual(r.results('hero_dead'), ['说了'])
        self.assertEqual([b.unit for b in r.sent], [0x60, 0x61])
        self.assertIn('你不在现场，但刚听说了这件事：我方英雄大魔法师阵亡了', r.prompts[0][1]['content'])
        # 关掉旁观者就还是跳过
        r2 = Rig(E.EventTalkConfig(bystander=False), cam=dict(CAM, x=3000.0))
        r2.units = base() + squad
        r2.tick()
        r2.units = [u for u in base() if u.addr != 0x10] + squad
        r2.tick()
        r2.tick()
        self.assertEqual(r2.results('hero_dead'), ['相关的人不在镜头里'])

    def test_disabled_event(self):
        cfg = E.EventTalkConfig()
        cfg.update({'enabled_events': {'tier_up': False}, 'cooldowns': {'fight_start': 99}})
        self.assertEqual(cfg.cooldowns['fight_start'], 99)
        self.assertEqual(cfg.cooldowns['tier_up'], 30)      # 没改的保留默认
        r = Rig(cfg)
        r.units = base()
        r.tick()
        r.units = [u if u.addr != 0x1 else unit(0x1, 'hkee', -300, -200, hp=1500, hp_max=1500) for u in base()]
        r.tick()
        self.assertEqual(r.results('tier_up'), ['这类事关着'])

    def test_new_match_resets(self):
        r = Rig()
        r.units = base()
        r.tick()
        r.epoch = 2
        r.units = [u for u in base() if u.addr != 0x10]      # 新一局英雄还没出来 —— 不能算阵亡
        r.tick()
        r.tick()
        self.assertEqual(r.results('hero_dead'), [])


if __name__ == '__main__':
    unittest.main()

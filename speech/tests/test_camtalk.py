# -*- coding: utf-8 -*-
"""镜头对白 camtalk.py：谁在画面里、谁先说、口吻、气泡不带名字（假快照 + 假镜头 + 假模型，不连游戏）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camtalk as C  # noqa: E402

CAM = {'x': 0.0, 'y': 0.0, 'dist': 2600.0, 'pitch': 304.0, 'fov': 70.0, 'yaw': 90.0}


def unit(addr, type_, x, y, owner=0, hp=400, hp_max=400):
    return SimpleNamespace(addr=addr, type=type_, x=x, y=y, owner=owner, hp=hp, hp_max=hp_max, order=None)


def snap(units):
    return SimpleNamespace(in_game=True, me=0, epoch=1, gold=300, lumber=100, food_used=20, food_cap=40, units=units)


class ViewTest(unittest.TestCase):
    def test_in_view_uses_projection(self):
        # 期望是按 camframe.project 算出来的（一次性分析脚本 probe_view.py）：
        # (1000,0)->u 0.85 在；(1500,0)->u 1.03 出右边；(0,1200)->v 0.04 出上边（气泡会被顶栏切）；
        # (0,-600)->v 0.675 在；(0,-900)->v 0.84 压在操作台后面
        for (x, y), want in [((0, 0), True), ((1000, 0), True), ((1500, 0), False), ((0, 1200), False),
                             ((0, -600), True), ((0, -900), False)]:
            self.assertEqual(C.in_view(unit(1, 'hfoo', x, y), CAM), want, (x, y))

    def test_rotated_camera_falls_back_to_radius(self):
        cam = dict(CAM, yaw=0.0)
        self.assertTrue(C.in_view(unit(1, 'hfoo', 800, 0), cam))
        self.assertFalse(C.in_view(unit(1, 'hfoo', 1500, 0), cam))

    def test_kinds(self):
        self.assertTrue(C.is_hero('Hamg'))
        self.assertFalse(C.is_hero('hfoo'))
        self.assertTrue(C.is_building('htow'))
        self.assertTrue(C.is_building('hbar'))            # units.json：兵营 defT = fort
        self.assertFalse(C.is_building('hfoo'))

    def test_clean_line(self):
        self.assertEqual(C.clean_line('大魔法师：「稳住阵型（抬手）！」', 24), '稳住阵型！')
        self.assertEqual(C.clean_line('遵命，长官！', 24), '遵命，长官！')


class RoundTest(unittest.TestCase):
    def make(self, units, cfg=None, cam=CAM):
        sent, prompts = [], []

        def llm(messages, max_tokens, temperature):
            prompts.append(messages)
            return '台词%d' % len(prompts)

        c = C.CamTalk(5, snapshot=lambda: snap(units), camera=lambda: cam, llm=llm,
                      send=lambda bubbles: sent.extend(bubbles), config=cfg or C.CamTalkConfig(), sleep=lambda s: None)
        return c, sent, prompts

    def army(self):
        return [unit(0x10, 'Hamg', 0, 0), unit(0x20, 'hfoo', 100, 0), unit(0x21, 'hfoo', 200, 50),
                unit(0x30, 'hpea', 50, 50),                   # 工人不参与（归茶话会）
                unit(0x40, 'hbar', -200, 100, hp=1500, hp_max=1500),   # 建筑不参与
                unit(0x50, 'hfoo', 5000, 0)]                  # 画面外

    def test_hero_leads_then_follower_replies_without_names(self):
        c, sent, prompts = self.make(self.army())
        c._round()
        self.assertEqual([b.unit for b in sent][:1], [0x10])            # 英雄先说
        self.assertEqual(len(sent), 2)                                   # replies=1：随从接一句
        self.assertIn(sent[1].unit, (0x20, 0x21))                        # 画面里的兵，不是工人/建筑/画面外的
        for b in sent:
            self.assertEqual(b.name, '')                                 # 不显示名字
        self.assertEqual(sent[0].style.border_color, '#E0B040')          # 首领金边
        lead_user = prompts[0][1]['content']
        self.assertIn('你是大魔法师（英雄', lead_user)
        self.assertIn('首领', lead_user)
        follow_user = prompts[1][1]['content']
        self.assertIn('大魔法师手下的随从', follow_user)
        self.assertIn('大魔法师：台词1', follow_user)                     # 随从看得到首领刚说的话
        self.assertIn('领导者的口吻', prompts[0][0]['content'])
        self.assertIn('随从的口吻', prompts[0][0]['content'])
        names = {v['name'] for v in c.view}
        self.assertNotIn('农民', names)
        self.assertEqual(len(c.view), 3)

    def test_no_hero_soldiers_talk(self):
        units = [unit(0x20, 'hfoo', 100, 0), unit(0x21, 'hrif', 200, 50)]
        c, sent, prompts = self.make(units)
        c._round()
        self.assertEqual(len(sent), 2)
        self.assertIn('普通士兵', prompts[0][1]['content'])

    def test_nobody_in_view_says_nothing(self):
        c, sent, _ = self.make([unit(0x50, 'hfoo', 5000, 0), unit(0x30, 'hpea', 0, 0)])
        c._round()
        self.assertEqual(sent, [])
        self.assertEqual(c.scene, '画面里没有我方的人')

    def test_scene_facts(self):
        units = self.army() + [unit(0x90, 'ogru', 300, 0, owner=1), unit(0x91, 'nfsp', -300, 0, owner=12)]
        c, sent, prompts = self.make(units)
        c._round()
        user = prompts[0][1]['content']
        self.assertIn('我方首领：大魔法师（血 100%）', user)
        self.assertIn('我方随从：步兵×2', user)
        self.assertIn('敌方就在眼前：兽族步兵', user)
        self.assertIn('野怪：树魔影子牧师', user)

    def test_fogged_foes_and_creeps_do_not_count(self):
        units = self.army() + [unit(0x90, 'ogru', 300, 0, owner=1), unit(0x91, 'nfsp', -300, 0, owner=12),
                               unit(0x92, 'nfsp', 400, 100, owner=12)]
        c, sent, prompts = self.make(units)
        c._fog = lambda: (lambda x, y: x < 0)       # 右半边在迷雾里
        c._round()
        user = prompts[0][1]['content']
        self.assertNotIn('敌方就在眼前', user)
        self.assertIn('野怪：树魔影子牧师 ——', user)  # 只剩左边那一个（不是 ×2）
        self.assertEqual({v['name'] for v in c.view if v['side'] != '我方'}, {'树魔影子牧师'})

    def test_follower_tones_rotate(self):
        c, sent, prompts = self.make(self.army(), C.CamTalkConfig(replies=1))
        for _ in range(3):
            c._round()
        tones = [p[1]['content'].split('这次的态度：')[1].split('。')[0] for p in prompts if '这次的态度：' in p[1]['content']]
        self.assertEqual(len(set(tones)), 3)          # 三轮三种态度

    def test_settings(self):
        cfg = C.CamTalkConfig(replies=3, duration_s=7, font_px=28, extra='整体搞笑一点')
        units = self.army() + [unit(0x22, 'hrif', 150, -100), unit(0x23, 'hkni', -100, -50)]
        c, sent, prompts = self.make(units, cfg)
        c._round()
        self.assertEqual(len(sent), 4)                                   # 英雄 1 + 随从 3
        self.assertTrue(all(b.duration_ms == 7000 for b in sent))
        self.assertTrue(all(b.style.font_px == 28 for b in sent))
        self.assertIn('额外要求：整体搞笑一点', prompts[0][0]['content'])

    def test_foe_hero_taunts_when_enabled(self):
        import random
        random.seed(3)
        units = self.army() + [unit(0x99, 'Obla', 400, 0, owner=1)]
        c, sent, prompts = self.make(units, C.CamTalkConfig(include_foes=True))
        roles = []
        for _ in range(12):
            c._round()
        roles = [ln.role for ln in c.transcript]
        self.assertIn('foe', roles)
        i = roles.index('foe')
        self.assertEqual(roles[i + 1], 'lead')                           # 敌方放狠话后我方英雄回
        foe_bubble = [b for b in sent if b.unit == 0x99][0]
        self.assertEqual(foe_bubble.style.border_color, '#D04040')


if __name__ == '__main__':
    unittest.main()

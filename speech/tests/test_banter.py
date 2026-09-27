# -*- coding: utf-8 -*-
"""农民茶话会 banter.py：台词清洗、战绩摘要、报幕/接话流程（假快照 + 假模型 + 假发送，不连游戏）。"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import banter as B  # noqa: E402


def unit(addr, type_, x, y, owner=0, hp=220, order=None):
    return SimpleNamespace(addr=addr, type=type_, x=x, y=y, owner=owner, hp=hp, order=order)


def snap(units, epoch=7, gold=500, lumber=150):
    return SimpleNamespace(in_game=True, me=0, epoch=epoch, gold=gold, lumber=lumber, food_used=5, food_cap=12,
                           units=units)


LAST = {'name': 'match-20260918-173544-077-x.jsonl', 'date': '20260918', 'finalized': True, 'live': False,
        'race': 'human', 'foeRace': 'human', 'difficulty': 3, 'map': '(2)EchoIsles_AMAI.w3x',
        'peakFoeArmy': 22, 'tier': 2, 'maxHalls': 2,
        'result': {'outcome': 'conceded-weak', 'minutes': 13.31, 'peakArmy': 12, 'peakWorkers': 16}}


class FakeStats:
    def __init__(self, rows):
        self.rows = rows

    def get(self, inst):
        return B.MatchStats._summarize(self.rows, today='20260918')


class CleanTest(unittest.TestCase):
    def test_strips_name_brackets_quotes_emoji(self):
        names = ['老王', '小李']
        self.assertEqual(B.clean_line('老王：「好家伙（叹气）又开工了😂」', names, 32), '好家伙又开工了')
        self.assertEqual(B.clean_line('小李说: 摸了摸了\n第二行不要', names, 32), '摸了摸了')
        self.assertEqual(B.clean_line('<think>想想</think>绷不住了', names, 32), '绷不住了')
        self.assertEqual(B.clean_line('', names, 32), '')

    def test_truncates_at_punctuation(self):
        s = B.clean_line('第一句话说完了，第二句话特别特别特别长一直说个不停根本停不下来', [], 16)
        self.assertEqual(s, '第一句话说完了')          # 逗号在第 8 个字（>= 16 x 0.4），截在那里、去掉逗号
        self.assertEqual(B.clean_line('好！第二句话特别特别特别长一直说', [], 10), '好！第二句话特别特别')   # 标点太靠前 -> 硬截
        self.assertLessEqual(len(B.clean_line('一' * 50, [], 20)), 20)


class StatsTest(unittest.TestCase):
    def test_summarize_counts(self):
        live = {'name': 'match-20260918-180000-078-y.jsonl', 'date': '20260918', 'finalized': False, 'live': True,
                'result': None}
        old = dict(LAST, name='match-20260917-100000-001-z.jsonl', date='20260917',
                   result={'outcome': 'victory', 'minutes': 20})
        s = B.MatchStats._summarize([live, LAST, old], today='20260918')
        self.assertEqual((s['current'], s['today'], s['wins'], s['finished']), (3, 2, 1, 2))
        self.assertIs(s['last'], LAST)
        # 最新一局已经打完（没有在打）：报的是「下一局」
        s2 = B.MatchStats._summarize([LAST, old], today='20260918')
        self.assertEqual((s2['current'], s2['today']), (3, 2))

    def test_describe_last(self):
        d = B.describe_last(LAST)
        for part in ('人族打人族AMAI 电脑（普通）', '13 分钟', '自己打了 GG', '我方兵力最多 12、对面最多 22', '升到了二本',
                     '最多 2 座大厅'):
            self.assertIn(part, d)
        self.assertEqual(B.describe_last(None), '上一局没有记录。')


class FlowTest(unittest.TestCase):
    def make(self, units, replies):
        sent, prompts = [], []

        def llm(messages, max_tokens, temperature):
            prompts.append(messages)
            return replies.pop(0) if replies else '随便说一句'

        state = {'snap': snap(units)}
        b = B.Banter(1234, 16, snapshot=lambda: state['snap'], llm=llm, send=lambda bubbles: sent.extend(bubbles),
                     stats=FakeStats([LAST]), config=B.BanterConfig(interval_s=0.01))
        return b, sent, prompts, state

    def home(self):
        return [unit(0x100, 'htow', 0, 0)] + [unit(0x200 + i, 'hpea', 100 * i, 50) for i in range(5)] + \
               [unit(0x900, 'hpea', 9000, 9000)]          # 离家太远，不参与

    def test_first_line_is_announce_by_reporter(self):
        b, sent, prompts, _ = self.make(self.home(), ['小美：第 78 局开工！上一局 13 分钟打出 GG，绷不住了'])
        b._step()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].name, '小美')
        self.assertIn(sent[0].unit, {0x200 + i for i in range(5)})
        self.assertEqual(sent[0].text, '第 78 局开工！上一局 13 分钟打出 GG，绷不住了')
        self.assertTrue(3000 <= sent[0].duration_ms <= 5000)
        user = prompts[0][1]['content']
        self.assertIn('第 2 局（今天第 2 局）；前面打完 1 局，0 胜 1 负', user)   # 只有一局记录且已打完 -> 这是第 2 局
        self.assertIn('上一局：人族打人族', user)
        self.assertIn('报幕', user)
        self.assertEqual(b.transcript[-1].mode, 'announce')
        self.assertEqual(len(b.assign), 5)                  # 家里 5 个都分到人设，远处那个没有

    def test_next_lines_rotate_speakers_and_mention_pros(self):
        b, sent, prompts, _ = self.make(self.home(), [])
        for _ in range(7):
            b._step()
        self.assertEqual(len(sent), 7)
        for a, c in zip(sent, sent[1:]):
            self.assertNotEqual(a.name, c.name)             # 不会同一个人连着说
        mentions = [p[1]['content'] for p in prompts if '这句顺带提到' in p[1]['content']]
        self.assertGreaterEqual(len(mentions), 2)           # pro_every=3：7 句里至少 2 句带高手
        for text in mentions:                               # 带高手时一定带上他那个种族的兵种表
            name = text.split('这句顺带提到 ')[1].split('（')[0]
            race = next(p[1] for p in B.PROS if p[0] == name)
            self.assertIn(B.RACE_UNITS[race], text)

    def test_worn_slang_is_banned_next(self):
        b, sent, prompts, _ = self.make(self.home(), ['小美：好家伙，又开工了', '老王：牛马就是我'])
        b._step()
        b._step()
        b._step()
        self.assertIn('这句别再用：好家伙', prompts[1][1]['content'])
        self.assertIn('这句别再用：好家伙、牛马', prompts[2][1]['content'])

    def test_new_epoch_triggers_announce(self):
        b, sent, prompts, state = self.make(self.home(), [])
        b._step()
        b._step()
        self.assertNotEqual(b.transcript[-1].mode, 'announce')
        state['snap'] = snap([unit(0x5100, 'htow', 0, 0)] + [unit(0x5200 + i, 'hpea', 100 * i, 50) for i in range(5)],
                             epoch=8)
        b._step()
        self.assertEqual(b.transcript[-1].mode, 'announce')
        self.assertIn(sent[-1].unit, {0x5200 + i for i in range(5)})

    def test_topic_and_no_workers(self):
        b, sent, prompts, state = self.make(self.home(), [])
        b._step()
        b.poke('你们为什么不去打野？')
        b._step()
        self.assertEqual(b.transcript[-1].mode, 'topic')
        self.assertIn('你们为什么不去打野？', prompts[-1][1]['content'])
        state['snap'] = snap([unit(0x100, 'htow', 0, 0)])
        n = len(sent)
        b._step()
        self.assertEqual(len(sent), n)                      # 没有工人就不说

    def test_duration_and_font_settings(self):
        b, sent, prompts, _ = self.make(self.home(), ['小美：第二局开工', '老王：一句话'])
        b._step()
        self.assertTrue(3000 <= sent[0].duration_ms <= 5000)        # 默认：自动 3~5 秒
        self.assertEqual((sent[0].style.font_px, sent[0].style.name_font_px), (0, 0))
        b.config.update({'duration_s': 8.5, 'font_px': 30, 'name_font_px': 20})
        b._step()
        self.assertEqual(sent[1].duration_ms, 8500)
        self.assertEqual((sent[1].style.font_px, sent[1].style.name_font_px), (30, 20))
        b.config.update({'duration_s': 99})
        b._step()
        self.assertEqual(sent[2].duration_ms, 30000)               # 上限 30 秒

    def test_activity_and_stock_wording(self):
        units = [unit(0x100, 'htow', 0, 0), unit(0x50, 'ngol', 1000, 0, owner=15, hp=10000),
                 unit(0x201, 'hpea', 900, 0, order=0x000D0032), unit(0x202, 'hpea', 1100, 50, order=0x000D0032),
                 unit(0x203, 'hpea', 0, 300, order=0), unit(0x204, 'hpea', -500, 0, order=0x000D0032)]
        b, sent, prompts, _ = self.make(units, [])
        b._step()
        user = prompts[0][1]['content']
        self.assertIn('家里 4 个农民：2 个在挖金，1 个在砍树或者盖房，1 个闲着没活干', user)
        self.assertIn('手上库存金子 500、木头 150（是存款，不是金矿剩多少）', user)

    def test_enemy_at_home_alarm(self):
        units = self.home() + [unit(0x7000 + i, 'hfoo', 300, 300, owner=1) for i in range(3)]
        b, sent, prompts, _ = self.make(units, [])
        b._step()                                           # 第一句还是报幕
        b._step()
        self.assertEqual(b.transcript[-1].mode, 'alarm')
        self.assertIn('家门口来了 3 个敌方单位', prompts[-1][1]['content'])


if __name__ == '__main__':
    unittest.main()

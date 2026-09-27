# -*- coding: utf-8 -*-
"""离线生成 w3data/skills.json：英雄技能每一级的射程/半径/冷却/耗蓝/持续/施法前摇/DataA..F + 目标类型。

    python make_skills.py

09-16 英雄施法要按技能的实际作用来判。mod_combat 的施法规则原来是估的常数、不看等级
（暴风雪按半径 250 找扎堆、风暴之锤射程写 700、雷霆一击固定 300），这张表让它按**当前等级的真实数据**判。
字段原样取 AbilityData.slk（Rng/Area/Cool/Cost/Dur/HeroDur/Cast/DataA..F/targs），含义由使用方按技能解释，
常用的几条（核对过数据）：
  AHbz 暴风雪   A 波数 6/8/10、B 每波伤害 30/40/50、F 每波上限；Area 200；targs「_」= 不挑目标，自己人也挨砸
  AHfs 烈焰风暴 A 每跳伤害、F 总伤上限；Area 200；targs 含 friend/self（烧自己人）；Cast 1.33 前摇
  AHtb 风暴之锤 A 伤害 100/225/350；Rng 600；HeroDur 3 眩晕
  AHtc 雷霆一击 A 伤害 60/100/140；Area 250/300/350；只打 ground
  AHhb 圣光术   A 治疗 200/400/600；Rng 800；notself
生成时自检这几条，对不上不写。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_items import parse_slk, num  # noqa: E402

FIELDS = ('Rng', 'Area', 'Cool', 'Cost', 'Dur', 'HeroDur', 'Cast', 'DataA', 'DataB', 'DataC', 'DataD', 'DataE', 'DataF')


def build():
    out = {}
    for r in parse_slk(os.path.join(os.path.dirname(HERE), 'game', 'AbilityData.slk')):
        code = r.get('alias')
        if not code or r.get('hero') != '1':
            continue
        n = int(num(r.get('levels')) or 1)
        levels = []
        last = {}
        for lv in range(1, max(1, n) + 1):
            row = {}
            for f in FIELDS:
                v = r.get('%s%d' % (f, lv))
                row[f] = num(v) if v not in (None, '-', '_', '') else last.get(f, 0.0)   # 高等级留空 = 沿用低等级
            levels.append(row)
            last = row
        out[code] = {'name': r.get('comments', ''), 'targs': (r.get('targs1') or '').split(','),
                     'reqLevel': int(num(r.get('reqLevel'))), 'levels': levels}
    return out


def selfcheck(s):
    want = [('AHbz', 1, 'Area', 200), ('AHbz', 1, 'DataA', 6), ('AHbz', 3, 'DataB', 50), ('AHtb', 2, 'DataA', 225),
            ('AHtb', 1, 'Rng', 600), ('AHtc', 3, 'Area', 350), ('AHhb', 3, 'DataA', 600), ('AHfs', 1, 'Cast', 1.33)]
    return [(c, lv, f, v, s.get(c, {}).get('levels', [{}] * 3)[lv - 1].get(f)) for c, lv, f, v in want
            if abs(s.get(c, {}).get('levels', [{}] * 3)[lv - 1].get(f, -1) - v) > 1e-6]


def main():
    s = build()
    bad = selfcheck(s)
    if bad:
        print('自检没过，不写：', bad)
        return 1
    with io.open(os.path.join(os.path.dirname(HERE), 'game', 'skills.json'), 'w', encoding='utf-8') as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print('写出 skills.json：%d 个英雄技能' % len(s))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

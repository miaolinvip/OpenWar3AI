# -*- coding: utf-8 -*-
"""离线生成 w3data/items.json（物品 -> 效果）和 w3data/heroes.json（英雄 -> 主属性/基础三围）。

    python make_items.py

09-16 捡属性书要看是不是适合自己用：法师吃智力书，战士吃力量书。
效果**查表，不猜**（和 make_buffs.py 同一个做法）：
  ItemData.slk   每件物品的 class / Level / abilList / powerup（捡起即用）
  AbilityData.slk 物品技能行：按**基础技能码**（code 列）决定 DataA..D 的含义 —— 同一基础码的各档物品含义一致：
      AIsm/AIam/AIim/AIxm 永久属性书、AIab 装备加属性：DataA=敏捷 DataB=智力 DataC=力量
        （核对：AIsm 力量书 C=1、AIam 敏捷书 A=1、AIim 智力书 B=1、AInm 力量书+2 C=2）
      AIem 经验 A、AIlm 升级 A、AImi 永久加血 A、AIml 加血上限 A、AImm 加蓝上限 A、AIat 攻击 A、AIde 护甲 A、
      AIas 攻速 A、AIms 移速 A、AIrm 回蓝 A、Arel 回血 A、AIhe 治疗 A、AIma 回蓝药 A
  UnitBalance.slk 英雄 Primary（STR/AGI/INT）和 STR/INT/AGI 基础值
  ItemStrings.txt 中文名（War3Patch 里的是中文版）
生成时拿已知物品自检（tint 智力 +1、tstr 力量 +1、manh 永久加血 50、rin1 智力 +3、ratc 攻击 +12），对不上就不写。
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
W3 = os.path.join(os.path.dirname(HERE), 'game')

ATTR3 = {'AIsm', 'AIam', 'AIim', 'AIxm'}        # 永久属性书
ATTR_EQ = {'AIab'}                              # 戴在身上的加属性
SIMPLE = {
    'AIem': 'xp', 'AIlm': 'level', 'AImi': 'hp_perm', 'AIml': 'hp', 'AImm': 'mana', 'AIat': 'attack',
    'AIde': 'armor', 'AIas': 'attack_speed', 'AIms': 'move', 'AIrm': 'mana_regen', 'Arel': 'life_regen',
    'AIhe': 'heal', 'AIma': 'mana_restore', 'AIrg': 'regen_scroll', 'AImr': 'mana_restore_aoe',
    'AIrc': 'reincarnation', 'AEar': 'aura_trueshot', 'AUav': 'aura_vampiric', 'AIcf': 'cloak_flames',
    'AIsa': 'speed_aoe', 'AHmt': 'teleport',
}


def parse_slk(path):
    cells, maxx, maxy, y = {}, 0, 0, 1
    for line in io.open(path, encoding='latin-1'):
        if not line.startswith('C;'):
            continue
        x = val = None
        for part in line.rstrip('\r\n').split(';')[1:]:
            if not part:
                continue
            t, rest = part[0], part[1:]
            if t == 'X':
                x = int(rest)
            elif t == 'Y':
                y = int(rest)
            elif t == 'K':
                val = rest
        if x is None or val is None:
            continue
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        cells[(x, y)] = val
        maxx, maxy = max(maxx, x), max(maxy, y)
    hdr = {x: cells[(x, 1)] for x in range(1, maxx + 1) if (x, 1) in cells}
    return [{h: cells[(x, yy)] for x, h in hdr.items() if (x, yy) in cells} for yy in range(2, maxy + 1)]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def names():
    out, cur = {}, None
    for line in io.open(os.path.join(W3, 'ItemStrings.txt'), encoding='utf-8-sig', errors='replace'):
        m = re.match(r'\[(\w{4})\]', line.strip())
        if m:
            cur = m.group(1)
        elif cur and line.startswith('Name='):
            out.setdefault(cur, line[5:].strip().strip('"'))
    return out


def build():
    ab = {r['alias']: r for r in parse_slk(os.path.join(W3, 'AbilityData.slk')) if r.get('alias')}
    nm = names()
    items = {}
    for r in parse_slk(os.path.join(W3, 'ItemData.slk')):
        code = r.get('itemID')
        if not code:
            continue
        eff, unknown = {}, []
        for a in [s for s in (r.get('abilList') or '').split(',') if s and s != '_']:
            row = ab.get(a)
            base = row.get('code') if row else None
            if base in ATTR3 or base in ATTR_EQ:
                pre = 'tome_' if base in ATTR3 else ''
                for key, col in (('agi', 'DataA1'), ('int', 'DataB1'), ('str', 'DataC1')):
                    v = num(row.get(col))
                    if v:
                        eff[pre + key] = eff.get(pre + key, 0.0) + v
            elif base in SIMPLE:
                eff[SIMPLE[base]] = eff.get(SIMPLE[base], 0.0) + (num(row.get('DataA1')) or 1.0)
            else:
                unknown.append(a)
        items[code] = {'name': nm.get(code, ''), 'class': r.get('class'), 'level': int(num(r.get('Level'))),
                       'powerup': r.get('powerup') == '1', 'effects': eff, 'other': unknown}
    heroes = {}
    for r in parse_slk(os.path.join(W3, 'UnitBalance.slk')):
        c = r.get('unitBalanceID')
        if c and c[:1].isupper() and r.get('Primary') in ('STR', 'AGI', 'INT'):
            heroes[c] = {'primary': r['Primary'], 'str': num(r.get('STR')), 'int': num(r.get('INT')), 'agi': num(r.get('AGI'))}
    return items, heroes


def selfcheck(items, heroes):
    want = [('tint', 'tome_int', 1), ('tstr', 'tome_str', 1), ('tdex', 'tome_agi', 1), ('tst2', 'tome_str', 2),
            ('manh', 'hp_perm', 50), ('rin1', 'int', 3), ('ratc', 'attack', 12), ('texp', 'xp', 100)]
    bad = [(c, k, v, items.get(c, {}).get('effects')) for c, k, v in want if items.get(c, {}).get('effects', {}).get(k) != v]
    bad += [(c, 'primary', p, heroes.get(c)) for c, p in (('Hamg', 'INT'), ('Hmkg', 'STR'), ('Obla', 'AGI'))
            if heroes.get(c, {}).get('primary') != p]
    return bad


def main():
    items, heroes = build()
    bad = selfcheck(items, heroes)
    if bad:
        print('自检没过，不写：', bad)
        return 1
    for fn, obj in (('items.json', items), ('heroes.json', heroes)):
        with io.open(os.path.join(W3, fn), 'w', encoding='utf-8') as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print('写出 items.json %d 件、heroes.json %d 个英雄' % (len(items), len(heroes)))
    for c in ('tint', 'tstr', 'tpow', 'tkno', 'manh', 'rde1', 'pman'):
        print('  %s %s %s' % (c, items[c]['name'], items[c]['effects']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

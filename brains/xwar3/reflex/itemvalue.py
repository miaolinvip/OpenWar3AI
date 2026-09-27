# -*- coding: utf-8 -*-
"""itemvalue.py —— 地上这件东西该给哪个英雄：按物品效果 × 英雄主属性/近战远程/等级打分，再扣走路成本。

09-16 捡属性书要看是否对口：法系英雄吃智力书、力量型英雄吃力量书，其它物品同理按细节分配。
原来 mod_scavenge 一律派**离得最近**的空闲英雄 —— 智力书被山丘之王吃掉、力量书被大法师吃掉都是常事。

数据查表不猜：war3-analytics/w3data/items.json / heroes.json（make_items.py 从 ItemData / AbilityData / UnitBalance 生成并自检）。
权重是**写死的常数**，每一项的理由写在旁边；它们不是量出来的最优值，改之前先想清楚要改的是哪条理由。

    value(item, hero_type, level, melee)  -> (分, 理由)
    pick(heroes, item, dist)              -> (英雄, 分, 理由)；dist(hero) 给走过去的距离
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
W3DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'data', 'game')

WALK_COST = 800.0      # 多走 800 距离 = 少 1 分：主属性对口的书（2 分）值得多走约 800，不值得横穿地图
PRIMARY_MUL = 2.0      # 主属性每点还加攻击力（1.27：主属性 +1 = 攻击 +1），同样一点属性对主属性英雄值两倍
MELEE_TANK_MUL = 1.5   # 血、护甲给近战（站前排挨打的）更值
BASELINE = 0.5         # 认不出效果的东西（任务物品、奇怪的道具）：谁近谁捡


def _load(name):
    try:
        with open(os.path.join(W3DATA, name), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


ITEMS = _load('items.json')
HEROES = _load('heroes.json')


def low_level_factor(level: int) -> float:
    """经验/升级书给低等级英雄更值：1 级 1.0，10 级 0.2（10 级封顶，经验书对满级英雄没用）。"""
    return max(0.2, (10 - max(1, min(10, level))) / 9.0) if level < 10 else 0.0


def value(item: str, hero: str, level: int = 1, melee: bool | None = None) -> tuple[float, str]:
    info = ITEMS.get(item)
    if not info or not info.get('effects'):
        return BASELINE, '效果未知，谁近谁捡'
    h = HEROES.get(hero, {})
    primary = h.get('primary')
    if melee is None:
        melee = primary in ('STR', 'AGI')      # 读不到射程时的近似：人族/兽族/亡灵/暗夜的力量、敏捷英雄都是近战
    eff = info['effects']
    score, why = 0.0, []
    for stat, key in (('str', 'STR'), ('agi', 'AGI'), ('int', 'INT')):
        v = eff.get('tome_' + stat, 0.0) + eff.get(stat, 0.0)
        if v:
            mul = PRIMARY_MUL if primary == key else 1.0
            score += v * mul
            why.append('%s+%g%s' % (key, v, '（主属性×2）' if mul > 1 else ''))
    lf = low_level_factor(level)
    if eff.get('xp'):
        score += 1.5 * eff['xp'] / 100.0 * lf
        why.append('经验+%g（%d 级×%.2f）' % (eff['xp'], level, lf))
    if eff.get('level'):
        score += 4.0 * eff['level'] * lf
        why.append('升 %g 级（%d 级×%.2f）' % (eff['level'], level, lf))
    tank = MELEE_TANK_MUL if melee else 1.0
    hp = eff.get('hp_perm', 0.0) + eff.get('hp', 0.0)
    if hp:
        score += hp / 50.0 * tank
        why.append('血+%g%s' % (hp, '（近战×1.5）' if melee else ''))
    if eff.get('armor'):
        score += eff['armor'] * (tank if melee else 0.8)
        why.append('甲+%g%s' % (eff['armor'], '（近战×1.5）' if melee else '（远程×0.8）'))
    if eff.get('attack'):
        score += eff['attack'] / 6.0 * (1.3 if primary in ('STR', 'AGI') else 1.0)
        why.append('攻击+%g' % eff['attack'])
    if eff.get('attack_speed'):
        score += eff['attack_speed'] * 10.0 * (1.3 if primary in ('STR', 'AGI') else 1.0)
        why.append('攻速+%g' % eff['attack_speed'])
    caster = 2.0 if primary == 'INT' else 0.6   # 智力英雄靠技能打输出，蓝就是输出；力量/敏捷英雄也放技能，但少
    if eff.get('mana'):
        score += eff['mana'] / 100.0 * caster
        why.append('蓝上限+%g%s' % (eff['mana'], '（法师×2）' if primary == 'INT' else ''))
    if eff.get('mana_regen'):
        score += eff['mana_regen'] * 4.0 * caster
        why.append('回蓝%s' % ('（法师×2）' if primary == 'INT' else ''))
    if eff.get('mana_restore'):
        score += eff['mana_restore'] / 150.0 * (1.5 if primary == 'INT' else 0.5)
        why.append('回蓝药')
    if eff.get('life_regen'):
        score += eff['life_regen'] * 0.5 * tank
        why.append('回血')
    if eff.get('heal'):
        score += eff['heal'] / 250.0
        why.append('治疗药')
    if eff.get('move'):
        score += eff['move'] / 60.0
        why.append('移速')
    if eff.get('reincarnation'):
        score += 3.0 + level * 0.3       # 重生十字章给等级最高的（死了最亏的那个）
        why.append('重生（%d 级）' % level)
    rest = [k for k in eff if k.startswith('aura') or k in ('cloak_flames', 'regen_scroll', 'mana_restore_aoe', 'speed_aoe', 'teleport')]
    if rest:
        score += 1.0 * len(rest)
        why.append('/'.join(rest))
    if score <= 0:
        return BASELINE, '效果对这个英雄没用处，谁近谁捡'
    return score, '、'.join(why)


def pick(heroes, item: str, dist, level_of=None, melee_of=None):
    """-> (英雄, 分, 理由)；heroes 为空返回 (None, 0, '')。

    分 = value - 距离/WALK_COST。只有经验/升级/重生这几类才问等级（level_of 要走一次引擎调用）。
    """
    info = ITEMS.get(item, {})
    needs_level = any(k in info.get('effects', {}) for k in ('xp', 'level', 'reincarnation'))
    best = (None, float('-inf'), '')
    for h in heroes:
        lv = level_of(h) if (needs_level and level_of) else 1
        v, why = value(item, h.type, lv or 1, melee_of(h) if melee_of else None)
        d = dist(h)
        s = v - d / WALK_COST
        if s > best[1]:
            best = (h, s, '%s：%s（值 %.1f，距离 %d）' % (info.get('name') or item, why, v, d))
    return best if best[0] is not None else (None, 0.0, '')

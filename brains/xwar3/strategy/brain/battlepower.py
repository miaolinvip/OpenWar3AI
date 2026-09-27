"""局部战力估算。静态兵种/武器来自游戏 SLK，动态血量与位置来自本拍快照。

分数是 sqrt(有效血量 × 对当前敌方构成的有效 DPS)，不是胜率。
显式折扣赶路时间，避免把全图存活单位当作眼前援军；不假装知道技能冷却或科技等级。
"""
from functools import lru_cache
import math
import os
import sys
from . import gamedata as G

if G.ROOT not in sys.path:
    sys.path.insert(0, G.ROOT)
from worldmodel.simulate import atk_mul


def number(row, key, default=0):
    try:
        return float(row.get(key, default))
    except (TypeError, ValueError):
        return float(default)


@lru_cache(maxsize=1)
def tables():
    folder = G.w3paths.GAME_DATA
    return tuple({r[key]: r for r in G.read_slk(os.path.join(folder, name)) if key in r}
                 for name, key in [('UnitBalance.slk', 'unitBalanceID'), ('UnitWeapons.slk', 'unitWeapID'), ('UnitData.slk', 'unitID')])


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def attributes(u, levels):
    balance, weapons, data = tables()
    b, w, d = balance.get(u.type, {}), weapons.get(u.type, {}), data.get(u.type, {})
    hero = G.is_hero(u.type)
    level = max(1, levels.get(u.addr, 1))
    agi = number(b, 'AGI') + (level-1)*number(b, 'AGIplus') if hero else 0
    primary = b.get('Primary', '')
    stat = number(b, primary) + (level-1)*number(b, primary+'plus') if hero else 0
    armor = number(b, 'realdef', G.stat(u.type, 'armor', 0)) + (level-1)*number(b, 'AGIplus')*.3 if hero else number(b, 'def', G.stat(u.type, 'armor', 0))
    modes = []
    for i in (1, 2):
        if not int(number(w, 'weapsOn', 1)) & i:
            continue
        cool = number(w, f'cool{i}', G.stat(u.type, 'cool', 0))
        if cool <= 0:
            continue
        damage = number(w, f'avgdmg{i}', G.stat(u.type, 'dmgAvg', 0)) + stat
        modes.append((max(0, damage/cool)*(1+agi*.02),
                      set(w.get(f'targs{i}', 'ground').split(',')),
                      w.get(f'atkType{i}', G.stat(u.type, 'atk', 'normal')),
                      number(w, f'rangeN{i}', G.stat(u.type, 'range', 100))))
    return dict(armor=armor, defence=G.stat(u.type, 'defT', 'normal'),
                air='air' in d.get('targType', '').split(','), modes=modes,
                speed=0 if G.is_building(u.type) else number(b, 'spd', G.stat(u.type, 'spd', 270)),
                known=bool(b and w and d))


def estimate(ours, foes, levels=None, horizon=6.0):
    """双方使用相同到场折扣；horizon=10 用于反打前的援军保守检查。"""
    levels = levels or {}
    ours, foes = [u for u in ours if u.hp > 0], [u for u in foes if u.hp > 0]
    attrs = {u.addr: attributes(u, levels) for u in ours+foes}
    def arrivals(us, them):
        weights = {}
        for u in us:
            a = attrs[u.addr]
            rng = max((x[3] for x in a['modes']), default=100)
            gap = max(0, min((distance(u, f) for f in them), default=1e9)-rng)
            arrival = gap/max(1, a['speed']) if a['speed'] else (0 if gap <= 0 else 1e9)
            weights[u.addr] = max(0.0, 1.0-arrival/horizon)
        return weights
    weights = {**arrivals(ours,foes), **arrivals(foes,ours)}
    def side(us, them):
        hp = dps = 0.0
        for u in us:
            a, weight = attrs[u.addr], weights[u.addr]
            armor_factor = 1+.06*a['armor'] if a['armor'] >= 0 else 1/(2-.94**(-a['armor']))
            hp += u.hp*armor_factor*weight
            target_hp = sum(f.hp*weights[f.addr] for f in them) or 1
            output = 0.0
            for f in them:
                fa = attrs[f.addr]
                mode = max((raw*atk_mul(atk, fa['defence']) for raw, targets, atk, _ in a['modes']
                            if ('air' if fa['air'] else 'ground') in targets), default=0)
                output += mode*f.hp*weights[f.addr]/target_hp
            # 重伤部队虽仍能攻击，实战中会撤离；不给残血队伍按满编持续输出估值。
            uptime = .55+.45*min(1, u.hp/max(1, u.hp_max))
            dps += output*weight*uptime
        return dict(hp=hp, dps=dps, power=math.sqrt(max(0,hp*dps)))
    a, b = side(ours,foes), side(foes,ours)
    ratio = a['power']/max(1,b['power'])
    return dict(ours=a, enemy=b, ratio=ratio, horizon=horizon,
                known=all(a['known'] for a in attrs.values()))

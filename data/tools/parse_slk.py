# -*- coding: utf-8 -*-
"""解析 SLK 表，导出每个单位的价格 / 攻击力 / 等级 / 血量。

SLK 是 1980 年代的电子表格交换格式，行是这样的：
    ID;PWXL;N;E
    B;X31;Y1478      <- 表格尺寸
    C;X1;Y1;K"unitBalanceID"   <- 第 1 列第 1 行，值是字符串
    C;X2;K135                  <- 同一行第 2 列，值是数字（Y 省略表示沿用上一个）
解析规则只有三条：C 行是单元格、X/Y 是列行号、**Y 省略时沿用上一行号**。
最后一条是所有 SLK 解析器最常踩的地方，漏掉它会得到一张乱位的表。
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'game')
OUT = os.path.join(SRC, 'units.json')


def parse_slk(path):
    """-> (headers, rows) ，rows 是 dict 列表。"""
    cells = {}
    maxx = maxy = 0
    y = 1
    with io.open(path, encoding='latin-1') as fh:
        for line in fh:
            if not line.startswith('C;'):
                continue
            x = None
            val = None
            for part in line.rstrip('\r\n').split(';')[1:]:
                if not part:
                    continue
                t, rest = part[0], part[1:]
                if t == 'X':
                    x = int(rest)
                elif t == 'Y':
                    y = int(rest)          # Y 省略时沿用上一个，这是 SLK 的规矩
                elif t == 'K':
                    val = rest
            if x is None or val is None:
                continue
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            cells[(x, y)] = val
            maxx = max(maxx, x)
            maxy = max(maxy, y)
    headers = {}
    for x in range(1, maxx + 1):
        h = cells.get((x, 1))
        if h:
            headers[x] = h
    rows = []
    for yy in range(2, maxy + 1):
        row = {}
        for x, h in headers.items():
            v = cells.get((x, yy))
            if v is not None:
                row[h] = v
        if row:
            rows.append(row)
    return list(headers.values()), rows


def num(v, d=0):
    if v is None:
        return d
    try:
        return float(v) if '.' in str(v) else int(v)
    except ValueError:
        return d


tables = {}
for f in ('UnitBalance', 'UnitWeapons', 'UnitData', 'UnitUI'):
    p = os.path.join(SRC, f + '.slk')
    if not os.path.exists(p):
        continue
    hdr, rows = parse_slk(p)
    tables[f] = rows
    print('%-14s %5d 行  列名前 14 个：%s' % (f, len(rows), hdr[:14]))

# 每张表的第一列都是单位 id，但列名不一样（unitBalanceID / unitWeapID / ...）
def key_of(row):
    for k in ('unitBalanceID', 'unitWeapID', 'unitID', 'unitUIID'):
        if k in row:
            return row[k]
    return list(row.values())[0] if row else None


merged = {}
for name, rows in tables.items():
    for r in rows:
        k = key_of(r)
        if not k or k in ('_', ''):
            continue
        merged.setdefault(k, {}).update(r)

print()
print('合并后单位数：', len(merged))

# 占地：`PathTextures\4x4SimpleSolid.tga` -> 4 格 -> 4*32 = 128 世界单位。
# 认不出来就返回 0，让上层退回它自己的估计 —— **不要瞎猜一个数字冒充真值**，
# 那会让"数据表里有没有"这个判据失去意义。
def blank(v):
    """SLK 的空格子写作 `_`。当成字符串直接用会得到一个非空的假值。"""
    s = (v or '').strip().lower()
    return '' if s in ('_', '-', 'none') else s


def foot_of(tex):
    if not tex:
        return 0
    m = re.search(r'(\d+)x(\d+)', str(tex))
    if not m:
        return 0
    return max(int(m.group(1)), int(m.group(2))) * 32


# 挑出真正有用的字段
def pick(r):
    dice = num(r.get('dice1'))
    sides = num(r.get('sides1'))
    base = num(r.get('dmgplus1'))
    # 魔兽的伤害 = dmgplus + dice 个 sides 面骰，平均值 = dmgplus + dice*(sides+1)/2
    avg = base + (dice * (sides + 1) / 2.0 if dice and sides else 0)
    return {
        'gold':   num(r.get('goldcost')),
        'lumber': num(r.get('lumbercost')),
        'food':   num(r.get('fused')),        # 列名是 fused，不是 fooduse
        'hp':     num(r.get('HP')),
        'level':  num(r.get('level'), num(r.get('Level'))),
        'armor':  num(r.get('def')),
        'dmgBase': base, 'dice': dice, 'sides': sides,
        'dmgAvg': round(avg, 1),
        'cool':   num(r.get('cool1')),
        'range':  num(r.get('rangeN1')),
        # ★ 攻击类型 vs 护甲类型 —— 魔兽里最大的那个乘数。
        # 穿刺打中甲 0.75、打轻甲 2.0；魔法打重甲 2.0、打要塞 0.35。
        # 这个系数比护甲值本身影响大得多，而我一开始整个漏掉了。
        'atk':    (r.get('atkType1') or 'normal').strip().lower(),
        'defT':   (r.get('defType') or 'normal').strip().lower(),
        # 回血速度：野怪磨不死的原因就在这（食尸鬼 2.0/秒）。
        # 之前只能事后发现"血量涨回去了"，现在可以事前算进去。
        'regen':  num(r.get('regenHP')),
        'spd':    num(r.get('spd')),          # 移速：决定谁能跑掉、谁追得上
        'bldtm':  num(r.get('bldtm')),        # 建造/训练时间
        'bounty': num(r.get('bountyplus')),   # 击杀赏金
        # ★★ 占地和落点要求 —— 这两个字段以前**从来没写出来过**。
        #
        # war3ai.ps1 的 W3-Footprint / W3-NeedsBlight 都是这么写的：
        #     $d = $script:W3Units[$Code]
        #     if ($d -and $d.foot -gt 0) { return $d.foot }   # 数据表里的真值
        #     ... 否则退回一张猜的表
        # 注释还特意写着"占地用**游戏数据表里的真值**，不再靠猜"，
        # 并列出 hhou=128 / htow=512 / halt=320。
        # 可 units.json 里**根本没有 foot 这个键** —— 于是那段代码
        # 一次都没走进去过，四个种族全程用的是那张猜的表，
        # 而且不会报任何错。W3-NeedsBlight 同理：恒为假，
        # 亡灵的腐地半径收缩逻辑等于不存在。
        #
        # 这是这个项目最典型的一类故障：**改进写好了、注释写足了、
        # 数据没接上，于是它安静地什么也没做。**
        #
        # 占地来自 pathTex（`PathTextures\10x10Simple.tga`），一格 32 单位。
        # 拿三个已知值验过：hhou 4x4->128、htow 16x16->512、halt 10x10->320，
        # 和注释里手写的那三个数完全对得上 —— 说明换算是对的。
        'foot':   foot_of(r.get('pathTex')),
        # SLK 用 `_` 表示空格子。不归一化的话 836 个单位的 reqPlace 都是 "_"，
        # 一个看起来"有值"的空值 —— 正是最容易被下游当成真数据的那种。
        'reqPlace': blank(r.get('requirePlace')),
        'name':   r.get('name', '') or r.get('Name', ''),
    }

data = {k: pick(v) for k, v in merged.items()}
with io.open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(data, fh, ensure_ascii=False, indent=0, sort_keys=True)
print('已写出', OUT)

print()
print('=== 抽查（这些价格我知道答案，用来验证解析对不对）===')
print('%-6s %-16s %6s %6s %5s %6s %5s %7s %6s' %
      ('code', 'name', 'gold', 'lumber', 'food', 'hp', 'lvl', 'dmgAvg', 'armor'))
for c in ('hfoo', 'ogru', 'earc', 'hpea', 'opeo', 'hkni', 'otau', 'ugho', 'nfor', 'ndrf', 'ntkw'):
    d = data.get(c)
    if d:
        print('%-6s %-16s %6s %6s %5s %6s %5s %7s %6s' %
              (c, d['name'][:16], d['gold'], d['lumber'], d['food'],
               d['hp'], d['level'], d['dmgAvg'], d['armor']))
    else:
        print('%-6s (表里没有)' % c)

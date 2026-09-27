# -*- coding: utf-8 -*-
"""从 War3 的 MPQ 里找单位数据表。

为什么走这条路：单位的金币价格、攻击力、等级本来就在游戏自己的数据表里
（SLK 格式，字段名就叫 goldcost / Level / dice1 / sides1 / dmgplus1）。
从运行中的游戏里读是**因为不知道有更直接的来源**才做的事 ——
数据表是权威、精确、一次拿全，而且不依赖游戏正在运行。

补丁包优先：War3Patch.mpq 会覆盖基础包里的同名文件，
所以要按 Patch -> War3x -> war3 的顺序找，**先找到的那份才是生效的那份**。
"""
import io
import os
import sys

W3 = os.environ.get('WAR3_GAME_DIR') or __import__('json').load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'openwar3.json'), encoding='utf-8-sig')).get('game_dir')
ORDER = ['War3Patch.mpq', 'War3x.mpq', 'war3.mpq', 'War3xLocal.mpq']
WANT = [
    'Units\\UnitBalance.slk',
    'Units\\UnitWeapons.slk',
    'Units\\UnitData.slk',
    'Units\\UnitUI.slk',
    'Units\\UnitAbilities.slk',
]

try:
    import mpyq
except ImportError:
    print('没有 mpyq'); sys.exit(1)

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'game')
os.makedirs(out, exist_ok=True)

for arc in ORDER:
    p = os.path.join(W3, arc)
    if not os.path.exists(p):
        continue
    print('--- %s ---' % arc)
    try:
        a = mpyq.MPQArchive(p)
    except Exception as e:
        print('   打不开: %s: %s' % (type(e).__name__, e))
        continue
    # 先看看有没有 listfile（有的话能直接看到所有路径）
    names = None
    try:
        lf = a.read_file('(listfile)')
        if lf:
            names = [n.strip() for n in lf.decode('latin-1').replace('\r', '\n').split('\n') if n.strip()]
            print('   listfile: %d 条' % len(names))
            hits = [n for n in names if n.lower().endswith('.slk')]
            print('   其中 .slk: %d 条，前几条：%s' % (len(hits), hits[:6]))
    except Exception as e:
        print('   listfile 读不到: %s' % type(e).__name__)

    for w in WANT:
        dst = os.path.join(out, os.path.basename(w))
        if os.path.exists(dst):
            continue          # 补丁包里已经拿到了，不要被基础包覆盖
        try:
            data = a.read_file(w)
        except Exception:
            data = None
        if data:
            with open(dst, 'wb') as fh:
                fh.write(data)
            print('   ✔ %s -> %d 字节' % (w, len(data)))

print()
print('已取出：', os.listdir(out) or '（空）')

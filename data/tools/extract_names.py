# -*- coding: utf-8 -*-
"""把游戏里的**中文名字**和 rawcode 对起来。

为什么需要：游戏的提示是用中文说的 ——「已完成： 兽族地洞」。
拿到这句话只解决了一半，另一半是"兽族地洞是哪个 rawcode"。
没有这一半，完成事件就只是一行给人看的字，进不了任何判断。

名字表在本地化包里（War3xLocal.mpq / war3.mpq），是 INI 格式：

    [otrb]
    Name=兽族地洞

顺序仍然是补丁包优先 —— 先找到的那份才是生效的那份，和 SLK 一样。

⚠ 编码：这些文件在简体中文版里是 GBK(936)，不是 UTF-8。
  而游戏在运行期把提示按 UTF-8 发给我们。两头编码不同，
  所以这里读的时候要按 GBK 解，写出去统一成 UTF-8 ——
  **在边界上把编码定死，中间就不用再猜。**
"""
import io
import json
import os
import sys

W3 = os.environ.get('WAR3_GAME_DIR') or __import__('json').load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'openwar3.json'), encoding='utf-8-sig')).get('game_dir')
ORDER = ['War3Patch.mpq', 'War3xLocal.mpq', 'War3x.mpq', 'war3.mpq']

# 单位、建筑、技能、科技的名字各在各的文件里。
WANT = [
    'Units\\HumanUnitStrings.txt',
    'Units\\OrcUnitStrings.txt',
    'Units\\NightElfUnitStrings.txt',
    'Units\\UndeadUnitStrings.txt',
    'Units\\NeutralUnitStrings.txt',
    'Units\\CampaignUnitStrings.txt',
    'Units\\ItemStrings.txt',
    'Units\\HumanUpgradeStrings.txt',
    'Units\\OrcUpgradeStrings.txt',
    'Units\\NightElfUpgradeStrings.txt',
    'Units\\UndeadUpgradeStrings.txt',
    'Units\\NeutralUpgradeStrings.txt',
    'Units\\CampaignUpgradeStrings.txt',
    # 技能名。现场出现过一条对不上的提示「低维修费用」——
    # 它不在单位表也不在升级表里，只能是技能。
    # **认不出的那条正是要补的那条**，这就是把 other 留着原文的用处。
    # 加进来的风险很小：重名是留全不猜的，最坏是"认不出"而不是认错。
    'Units\\HumanAbilityStrings.txt',
    'Units\\OrcAbilityStrings.txt',
    'Units\\NightElfAbilityStrings.txt',
    'Units\\UndeadAbilityStrings.txt',
    'Units\\NeutralAbilityStrings.txt',
    'Units\\CommonAbilityStrings.txt',
    'Units\\ItemAbilityStrings.txt',
    'Units\\CampaignAbilityStrings.txt',
]

try:
    import mpyq
except ImportError:
    print('没有 mpyq'); sys.exit(1)

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, 'w3data')
os.makedirs(out, exist_ok=True)

blobs = {}
for arc in ORDER:
    p = os.path.join(W3, arc)
    if not os.path.exists(p):
        continue
    try:
        a = mpyq.MPQArchive(p)
    except Exception as e:
        print('打不开 %s: %s' % (arc, e))
        continue
    for w in WANT:
        if w in blobs:
            continue            # 前面的包里已经有了，别被后面的覆盖
        try:
            data = a.read_file(w)
        except Exception:
            data = None
        if data:
            blobs[w] = data
            print('  %-42s <- %s (%d 字节)' % (w, arc, len(data)))

if not blobs:
    print('一个名字表都没找到'); sys.exit(1)


def decode(data):
    """简体中文版是 GBK。UTF-8 先试一下，失败再退 GBK ——
    **不要反过来**：GBK 几乎什么字节都"解得出来"，先试它会把
    UTF-8 文件静默解成乱码（这个坑今天在 DLL 那边刚踩过一次）。"""
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', 'replace')


names = {}          # rawcode -> 中文名（多级升级取第一级）
levels = {}         # 中文名 -> (rawcode, 第几级)
for w, data in blobs.items():
    text = decode(data)
    code = None
    for line in text.replace('\r', '\n').split('\n'):
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            code = line[1:-1].strip()
            continue
        if not code or '=' not in line:
            continue
        k, _, v = line.partition('=')
        if k.strip().lower() != 'name':
            continue
        v = v.strip().strip('"')
        if not v:
            continue
        # ⚠ 升级是**多级**的，一个 rawcode 一行逗号分隔的名字：
        #     [Rome] Name=钢铁近战武器,金属近战武器,终极近战武器
        # 只取整行的话，游戏说「已完成： 钢铁近战武器」就永远对不上 ——
        # 现场就是这样：三条完成事件里两条是"认不出这个名字"。
        # 每一级都要单独进表；level 从 1 开始，因为提示里不带级数，
        # 我们要能反过来知道刚完成的是第几级。
        parts = [p.strip() for p in v.split(',') if p.strip()]
        names.setdefault(code, parts[0])
        for lvl, nm in enumerate(parts, 1):
            # ⚠ 这里**不能** setdefault —— 同一个中文名确实会属于多个 rawcode
            #   （「兵营」人族兽族都叫这个）。用 setdefault 的话先读到的那族赢，
            #   而输出里只会显示"重名 0 条"，看上去像"没有歧义"，
            #   实际是歧义被悄悄解决错了。**"没有冲突"和"冲突被吞了"长得一样。**
            slot = levels.setdefault(nm, [])
            if not any(c == code for c, _ in slot):
                slot.append((code, lvl))

# 反向表：中文名 -> rawcode。**一对多要留全**，
# 因为同名的东西确实存在（各种族的"祭坛"在中文里不同名，但道具里有重名），
# 而"取第一个"会在最需要精确的时候悄悄给错答案。
rev = {nm: [c for c, _ in slot] for nm, slot in levels.items()}

dst = os.path.join(here, '..', 'game', 'names.json')
dst = os.path.abspath(dst)
with io.open(dst, 'w', encoding='utf-8') as fh:
    json.dump({'byCode': names,
               'byName': rev,
               'level': {nm: slot[0][1] for nm, slot in levels.items()}},
              fh, ensure_ascii=False, indent=0)

print()
print('%d 个 rawcode 有中文名，%d 个名字可反查 -> %s' % (len(names), len(rev), dst))
for probe in ('otrb', 'ogre', 'hhou', 'ohme', 'egol', 'uzig', 'Rome', 'Rotr'):
    print('  %s = %s' % (probe, names.get(probe, '(没有)')))
dupes = {k: v for k, v in rev.items() if len(v) > 1}
print('重名 %d 条（各族同名的兵营/祭坛之类，留全不猜）' % len(dupes))

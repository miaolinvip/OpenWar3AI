# -*- coding: utf-8 -*-
"""从大脑的实时发布里读出「接下来要发生什么」，转成导播看得懂的候选点。

导播原来只有一个数据源：逐拍的血量差分。那意味着**只能追认** —— 打起来了才知道
该看哪儿，而 3 倍速下一拍 0.6 秒墙钟 ≈ 2 游戏秒，镜头赶到时第一波已经打完。

而大脑每 1.5 游戏秒就把自己的打算发布在 `inst<N>/brain_live.json` 里：
它要出击、要集结、要打哪个营地、哪座城镇正在被威胁、敌方哪一团在往哪走。
这些都是**先于交火**的信息。读一次 0.13 毫秒（14.7 KB），比问引擎一次（17 毫秒）
便宜两个数量级。

⚠ 这里只读，不改大脑的任何东西。大脑那边还在迭代，字段可能变 ——
  所以每个字段都当成"可能没有"来取，缺了就少一个候选点，不抛异常。

⚠ 外推用的是大脑自己那套：`brain/world.py` 的 ARMY_FUTURE_MULT = 3.0
  （AMAI 的 army_future_mult，预测位置 = 现在 + 最近一次位移 × 3）。
  不另发明一套 —— 两处各算一份迟早会分叉。
"""
import json
import math
import os
import re
import time

ARMY_FUTURE_MULT = 3.0     # 和 war3-brain/brain/world.py 同名常量保持一致
STALE_S = 12.0             # 发布超过这么久就当没有（多半是上一局或者大脑卡住了）
MEET_DIST = 1800.0         # 两团外推后离这么近，算"要碰上了"
# 预判相遇时我方至少几个人。09-18 那局 13 条"相遇"里 9 条是"敌方 29 人正朝我方 2 人
# 过来" —— 两个人多半是落单的援兵或者护送的，切过去是看一次送死，不是一场仗。
MEET_MIN_OURS = 3
CACHE_S = 0.4              # 重读间隔（大脑 1.5 游戏秒发一次，3 倍速约 0.5 秒墙钟）
KEY_CELL = 1500.0          # "同一个意图"在空间上的格子
# 一个意图消失超过这么久（墙钟秒）再出现，就算"新冒出来的"。导播选镜和复盘
# 认"冒出来"用的是这同一个数 —— 两边各写一个的话，复盘量的就不是导播做的事。
ONSET_FORGET_S = 4.0


def beat_key(kind, why, x, y):
    """"这是不是同一个意图"的唯一定义 —— 导播和复盘共用（同一个定义别写两份，只改一处等于没改）。

    为什么要认"同一个"：大脑每 1.5 游戏秒重发一遍它的打算，"护送开矿"在同一个点
    挂 200 多游戏秒（09-18 实测）。导播要的是**它刚冒出来的那一下**，不是它一直在。
    ⚠ 句子里的数字要抹掉：「威胁 72 超过主力能扛的」下一拍就变成「威胁 80 …」，
      不抹的话每拍都是一个"新意图"，淡出机制形同虚设（实测那局有 6 条这样的）。
    """
    head = re.sub(r'[\d.]+', '#', (why or '').split('（')[0])
    return (kind, head, int(x // KEY_CELL), int(y // KEY_CELL))


class Intents(object):
    def __init__(self):
        self.path = None
        self.at = 0.0
        self.data = None
        self.mtime = None          # (修改时间纳秒, 文件大小) —— 见 read()
        self.error = None
        self.reads = 0

    def bind(self, state_file):
        """跟着 director.STATE_FILE 走，自动分实例（和 dirledger 同一个规矩）。"""
        self.path = os.path.join(os.path.dirname(os.path.abspath(state_file)),
                                 'brain_live.json')
        return self.path

    def read(self):
        """当前这份发布。读不到 / 太旧 / 格式不对都返回 None。"""
        if not self.path:
            return None
        now = time.time()
        if now - self.at < CACHE_S:
            return self.data
        self.at = now
        try:
            # ★ 键是（修改时间纳秒, 大小），不是浮点的修改时间（09-20）：两次发布落在同一个时间戳里的时候
            #   （写得快、或者时间戳精度不够），只比 mtime 会把**新的一份当成没变**，一直用旧的。
            #   test_dirintent 偶发失败就是它 —— 偶发的测试失败往往是真问题，别当噪声。
            st = os.stat(self.path)
            mtime = (st.st_mtime_ns, st.st_size)
            if mtime != self.mtime:
                with open(self.path, encoding='utf-8-sig') as fh:
                    self.data = json.load(fh)
                self.mtime = mtime
                self.reads += 1
        except (OSError, ValueError) as exc:
            self.error = '%s: %s' % (type(exc).__name__, exc)
            self.data = None
            return None
        d = self.data
        if not isinstance(d, dict):
            self.data = None
            return None
        wall = d.get('wall')
        if not isinstance(wall, (int, float)) or now - wall > STALE_S:
            return None            # 不清空 data：文件还在，只是这一刻不新鲜
        return d

    # -- 候选点 ------------------------------------------------------------
    def beats(self, own_owner=0):
        """把这份发布转成候选点。**只读，不决定任何事** —— 用不用是导播的事。

        每个点都带一句人话（why），因为这些点最后会出现在切镜账本和网页上，
        而"大脑打算干什么"正是观众最想知道、光看画面又看不出来的东西。
        """
        d = self.read()
        if not d:
            return []
        out = []
        w = d.get('world') or {}
        atk = d.get('attack') or {}

        # 一、自家城镇被威胁 —— 大脑已经算好了 threat，不自己再算一遍
        for t in (w.get('towns') or []):
            if t.get('owner') != own_owner or not t.get('threat'):
                continue
            out.append(_beat('threat', t.get('x'), t.get('y'),
                             15.0 + min(30.0, float(t['threat']) / 4.0),
                             '自家%s被威胁（%.0f）' % (t.get('hall') or '基地', t['threat'])))

        # 二、大脑这一刻的意图（出击 / 回防 / 移动），它自己就写了一句人话
        for it in (d.get('intents') or []):
            n = int(it.get('n') or 1)
            b = _beat('intent', it.get('x'), it.get('y'), 16.0 + min(12.0, n * 0.6),
                      '%s（%d 人）' % (it.get('why') or it.get('kind') or '行动', n))
            if b:
                b['n'] = n              # 导播按人数过滤（1~2 个人走动不是一场戏）
                out.append(b)

        # 三、出击流程里的几个关键点
        state = atk.get('state')
        if state == 'gather' and atk.get('gatherAt'):
            g = atk['gatherAt']
            out.append(_beat('gather', _at(g, 0), _at(g, 1), 17.0, '部队在集结，准备出击'))
        if state == 'creep' and (atk.get('camp') or {}).get('x') is not None:
            c = atk['camp']
            out.append(_beat('creep', c.get('x'), c.get('y'), 13.0,
                             '打野目标（营地强度 %s，还剩 %s 只）'
                             % (c.get('strength'), c.get('alive'))))
        if state == 'retreat' and atk.get('retreatAt'):
            r = atk['retreatAt']
            out.append(_beat('retreat', _at(r, 0), _at(r, 1), 14.0, '部队在撤退'))

        # 四、预判：敌我两团按各自的移动方向外推，会不会撞上
        #     这是唯一"自己算"的一条 —— 因为大脑没有发布这个结论，
        #     但它发布了算这个结论需要的东西（dx/dy）。
        mine = [a for a in (w.get('myArmies') or []) if (a.get('n') or 0) >= MEET_MIN_OURS]
        for e in (w.get('enemyArmies') or []):
            if (e.get('n') or 0) < 2:
                continue
            ex = _num(e.get('x')) + _num(e.get('dx')) * ARMY_FUTURE_MULT
            ey = _num(e.get('y')) + _num(e.get('dy')) * ARMY_FUTURE_MULT
            for m in mine:
                mx, my = _num(m.get('x')), _num(m.get('y'))
                if math.hypot(ex - mx, ey - my) > MEET_DIST:
                    continue
                b = _beat('meet', (ex + mx) / 2.0, (ey + my) / 2.0, 24.0,
                          '敌方 %d 人正朝我方 %d 人过来（强度 %s 对 %s）'
                          % (e.get('n'), m.get('n'), e.get('strength'), m.get('strength')))
                # 锚点：我方那一团。两团的中点可能还在迷雾里，而我方部队所在的地方
                # 一定看得见 —— 导播问引擎发现中点是雾，就退到这里
                if b:
                    b['ax'], b['ay'] = mx, my
                    b['en'] = int(e.get('n') or 0)      # 导播按敌人数过滤（两三个人过来不值得切）
                    out.append(b)
                break                     # 一团敌人只报一次
        return [b for b in out if b]

    def armies(self):
        """大脑发布的我方部队分团（不含农民和建筑）：[{'x','y','n','strength'}]。读不到返回 []。"""
        d = self.read()
        if not d:
            return []
        out = []
        for a in ((d.get('world') or {}).get('myArmies') or []):
            x, y = a.get('x'), a.get('y')
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                out.append({'x': float(x), 'y': float(y), 'n': int(a.get('n') or 0),
                            'strength': a.get('strength') or 0})
        return out

    def snapshot(self):
        """给账本/网页看的一小段摘要（大脑这一刻在想什么）。"""
        d = self.read()
        if not d:
            return None
        w = d.get('world') or {}
        atk = d.get('attack') or {}
        return {'t': d.get('t'), 'state': atk.get('state'),
                'army': w.get('army'), 'foe': w.get('enemyFighters'),
                'strength': w.get('ownStrength'), 'foeStrength': w.get('foeStrength'),
                'intents': [i.get('why') for i in (d.get('intents') or [])][:3]}


def _num(v):
    return float(v) if isinstance(v, (int, float)) else 0.0


def _at(v, i):
    """坐标可能是 [x, y]，也可能是 {'x':, 'y':} —— 两种都收。"""
    if isinstance(v, (list, tuple)) and len(v) > i:
        return _num(v[i])
    if isinstance(v, dict):
        return _num(v.get('x' if i == 0 else 'y'))
    return None


def _beat(kind, x, y, w, why):
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {'kind': kind, 'x': float(x), 'y': float(y), 'w': float(w),
            'why': why, 'src': 'brain'}


INTENTS = Intents()

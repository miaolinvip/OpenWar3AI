# -*- coding: utf-8 -*-
"""导播账本：把「每一拍看见了什么」和「每一次切镜为什么」写下来。

为什么要有这一层（09-18）：
改导播之前先翻了历史数据，发现**一次切镜都没有被记录过** —— director-stats.jsonl
是一局一行的汇总（26 局 26 行），而 director 的 `note` 是一句下一拍就被覆盖的字符串。
于是「为什么切这里」在过去无从回答，「改好了没有」只能靠肉眼。

每局写两份文件，放在 <实例目录>/director/ 下：

    shots-<开局时刻>.jsonl   每次切镜一行：落点、景别、分数、人话原因、**落选的候选**
    trace-<开局时刻>.jsonl   每拍一行：当时场上**全部**事件团 + 镜头在哪 + 看不看得见

⚠ 落选候选一定要一起记。只记切中的那个，等于拿被筛过的集合
  去估分布 —— 分不出「没有别的候选」和「有，但没轮到」。

⚠ trace 记的是**全部**事件团，不是导播选中的那几个，也不做 own_only 过滤。
  离线复盘要能独立重认「这一刻最该看的是哪里」；trace 里只有导播看中的东西的话，
  就是拿它自己的答案给它自己判卷。

⚠ 写账本**不许把导播搞挂**：所有 IO 都吞异常，坏了最多是这一局没有账本。
"""
import json
import os
import time

KEEP_MATCHES = 40          # 每种文件保留最近这么多局
BRAIN_CACHE_S = 2.0        # brain_live.json 重读间隔
STALE_S = 8.0              # 大脑的发布旧过这么久就不拿来对时


class Ledger(object):
    def __init__(self):
        self.dir = None
        self.stamp = None
        self._shots = None
        self._trace = None
        self._brain = {'at': 0.0, 't': None, 'wall': 0.0, 'path': None}
        self.shots_n = 0
        self.ticks_n = 0
        self.error = None

    # -- 绑定与开关局 ------------------------------------------------------
    def bind(self, state_file):
        """按 director.STATE_FILE 落地：它在实例目录里，账本就在实例目录的 director/ 下。

        每实例导播（director_inst.py）会把 STATE_FILE 指到 inst<N>/，所以这里
        跟着它走就自动分实例了 —— 不需要自己再认一次 WAR3_INST（同一条规则写两处，
        只改一处等于没改）。
        """
        base = os.path.join(os.path.dirname(os.path.abspath(state_file)), 'director')
        self.dir = base
        self._brain['path'] = os.path.join(os.path.dirname(os.path.abspath(state_file)),
                                           'brain_live.json')
        return base

    def open_match(self, stamp=None):
        """新的一局。返回这一局的时间戳（也是文件名的一部分）。"""
        self.close_match()
        self.stamp = stamp or time.strftime('%Y%m%d-%H%M%S')
        self.shots_n = self.ticks_n = 0
        if not self.dir:
            return self.stamp
        try:
            os.makedirs(self.dir, exist_ok=True)
            # 同一秒里开两局，文件名会撞 —— 而打开方式是追加，撞了就是**两局混进
            # 一个文件**，正是这套账本要避免的事。现实中很少见（两局隔着几十秒），
            # 但导播重启、或者一局刚开就结束时是可能的。
            # ⚠ 后缀原来只试到 -19，超过就**追加进已有的文件** —— 对照工具一两秒内开
            #   32 份账本，于是两次运行混进一个文件，凭空多出"镜头换了地方却没记账"
            #   （不变量检查报了 31 次，单独重跑同一个世界 0 次，才查到这里）。
            base = self.stamp
            k = 2
            while os.path.exists(self._path('trace')) and k < 100000:
                self.stamp = '%s-%d' % (base, k)
                k += 1
            self._shots = open(self._path('shots'), 'a', encoding='utf-8')
            self._trace = open(self._path('trace'), 'a', encoding='utf-8')
            self._rotate()
        except OSError as exc:
            self.error = '%s: %s' % (type(exc).__name__, exc)
            self._shots = self._trace = None
        return self.stamp

    def close_match(self, report=None):
        if report is not None:
            self.write('trace', dict(report, ev='match_end'))
        for fh in (self._shots, self._trace):
            try:
                if fh:
                    fh.close()
            except OSError:
                pass
        self._shots = self._trace = None

    def _path(self, kind):
        return os.path.join(self.dir, '%s-%s.jsonl' % (kind, self.stamp))

    def _rotate(self):
        """只留最近 KEEP_MATCHES 局。一局的 trace 约 200 KB，留 40 局够复盘用。"""
        try:
            for kind in ('shots', 'trace'):
                files = sorted(f for f in os.listdir(self.dir)
                               if f.startswith(kind + '-') and f.endswith('.jsonl'))
                for f in files[:-KEEP_MATCHES]:
                    try:
                        os.remove(os.path.join(self.dir, f))
                    except OSError:
                        pass
        except OSError:
            pass

    # -- 写 ----------------------------------------------------------------
    def write(self, kind, row):
        fh = self._shots if kind == 'shots' else self._trace
        if not fh:
            return False
        try:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'),
                                default=_plain) + '\n')
            fh.flush()
        except (OSError, ValueError, TypeError) as exc:
            self.error = '%s: %s' % (type(exc).__name__, exc)
            return False
        if kind == 'shots':
            self.shots_n += 1
        else:
            self.ticks_n += 1
        return True

    def shot(self, row):
        return self.write('shots', row)

    def tick(self, row):
        return self.write('trace', row)

    # -- 游戏时间 ----------------------------------------------------------
    def game_time(self):
        """当前游戏秒。读大脑发布的 brain_live.json，**只为了对齐时间**。

        为什么不用墙钟：3 倍速下墙钟和游戏时间差 3 倍，而复盘要和对局记录
        （gameElapsedSec）对得上。大脑每 1.5 游戏秒发布一次，两次之间按墙钟差
        乘倍速外推；读不到就返回 None —— 没有游戏时间不影响导播干活。
        """
        now = time.time()
        c = self._brain
        if not c['path']:
            return None
        if now - c['at'] >= BRAIN_CACHE_S:
            c['at'] = now
            try:
                with open(c['path'], encoding='utf-8-sig') as fh:
                    d = json.load(fh)
                t, wall = d.get('t'), d.get('wall')
                if isinstance(t, (int, float)) and isinstance(wall, (int, float)):
                    c['t'], c['wall'] = float(t), float(wall)
                else:
                    c['t'] = None
            except (OSError, ValueError, AttributeError):
                c['t'] = None
        if c['t'] is None:
            return None
        age = now - c['wall']
        # 太旧多半是上一局留下的，宁可不给。原来放宽到 60 秒 —— 新局开头那一分钟
        # 读到的全是上一局的时钟（09-18 一份 trace 的游戏时钟从 1386 起跳，
        # 过了一阵才落回 6.3）。大脑 3 倍速下每 0.5 秒墙钟发一次，8 秒足够宽。
        if age > STALE_S:
            return None
        return round(c['t'] + age * _speed(), 1)


def _speed():
    try:
        import camctl
        return camctl._speed_pct() / 100.0
    except Exception:                                   # noqa: BLE001
        return 1.0


def _plain(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


LEDGER = Ledger()

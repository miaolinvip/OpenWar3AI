# -*- coding: utf-8 -*-
"""农民茶话会：让基地里的工人用本地大模型互相吐槽，头顶气泡说出来（给 B 站直播间看个乐）。

09-18 设计要求：家里的农民由大模型扮演、互相聊天，贴近真实游戏场景；每个单位有自己的性格和情绪。
人设是「为了 AI 训练每天不停地挖」的打工人，互相吐槽、给观众看个乐，带出一局接一局重复打的真实感。
每次开局报一下当前是第几局、上一局怎么样。口吻戏谑、爱吐槽、有网感：观众以 B 站 20~50 岁的魔兽争霸老玩家为主，
日常对话里穿插一些 war3 对战高手的名字，增加代入感。

由 speak_api.py 挂上（POST /api/banter 开关），每个游戏实例一个 Banter 线程：
    读快照 -> 挑家里（主基地附近）的工人 -> 每个工人分一个固定人设（性格 + 心情）
    -> 轮流让一个人说一句（本地模型现编，带上战况、在场的人、最近的对话）-> 头顶气泡
    新一局开始（快照 epoch 变了 / 战绩来源出现新对局文件）-> 先「报幕」：第几局、今天第几局、上一局怎么没的
战绩来自 Farsight（127.0.0.1:8866 /api/i/<n>/matches）；它没开就直接读 build_intel4/inst<n>/matches/*.jsonl。

⚠ 工人进金矿时从快照里消失（不是死了）—— 所以人设按「最后一次见到」保留 90 秒，不播「某某没了」。
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

FARSIGHT = os.environ.get('FARSIGHT_URL', 'http://127.0.0.1:8866')
MATCH_ROOT = Path(__file__).resolve().parent.parent / 'bin'

WORKERS = {'hpea': '农民', 'hmil': '民兵', 'opeo': '苦工', 'uaco': '侍僧', 'ewsp': '小精灵'}
HALLS = {'htow', 'hkee', 'hcas', 'ogre', 'ostr', 'ofrt', 'unpl', 'unp1', 'unp2', 'etol', 'etoa', 'etoe'}
RACE_OF_WORKER = {'hpea': '人族', 'hmil': '人族', 'opeo': '兽族', 'uaco': '不死族', 'ewsp': '暗夜精灵'}
RACE_CN = {'human': '人族', 'orc': '兽族', 'undead': '不死族', 'nightelf': '暗夜精灵', 'elf': '暗夜精灵'}
DIFFICULTY_CN = {2: '简单', 3: '普通', 4: '疯狂'}      # 开局设置里的界面序号
OUTCOME_CN = {
    'victory': '居然赢了',
    'defeated': '被对面推平了',
    'timeout': '打到超时也没分出胜负',
    'conceded-weak': '打不过、自己打了 GG',
    'ended-stuck': '卡住了被强制结束',
    'ended-by-operator': '被主播手动掐了',
    'crash': '游戏直接崩了',
}
TIER_CN = {1: '一本都没升上去', 2: '升到了二本', 3: '升到了三本'}
MOODS = ['暴躁', '烦', '丧', '一般', '还行', '亢奋']
ANGLES = [
    '今天的战绩和场次',
    '挖金砍树的日常（一趟一趟来回跑）',
    'AI 指挥官的某个迷惑操作习惯',
    '当年网吧通宵、浩方、VS 平台的回忆',
    '打工人日常：没工资、没下班、KPI、摸鱼',
    '对面那个电脑',
    '幻想下班以后干什么、想转行当什么兵种',
    '直播间的观众在看他们打工',
    '怼刚才说话的那个人',
    '自己在这个基地里的地位和委屈',
]

# 高手：只给名字、国家、种族这种不会错的信息；提示词里要求不编具体比赛和比分。
# 09-18 实测：只给「韩国暗夜精灵传奇」时模型会说出「Moon 的蜘蛛」（蜘蛛是亡灵的）—— 老玩家一眼看穿，
# 所以提到谁就把他那个种族的英雄和兵种一起给过去，要玩梗只许扯这些。
PROS = [
    ('Sky', '人族', '中国人族，外号人皇，拿过两次 WCG 世界冠军'),
    ('Moon', '暗夜精灵', '韩国暗夜精灵传奇'),
    ('Grubby', '兽族', '荷兰兽族传奇'),
    ('TH000', '人族', '中国人族高手'),
    ('Infi', '人族', '中国人族高手'),
    ('Fly100%', '兽族', '中国兽族高手'),
    ('Lyn', '兽族', '韩国兽族高手'),
    ('Happy', '不死族', '俄罗斯亡灵高手'),
    ('120', '不死族', '中国亡灵高手'),
    ('Ted', '不死族', '中国亡灵选手，后来做了直播'),
    ('ToD', '人族', '法国人族老将'),
    ('Remind', '暗夜精灵', '韩国暗夜精灵高手'),
    ('Lawliet', '暗夜精灵', '韩国暗夜精灵高手'),
    ('FoCuS', '兽族', '韩国兽族高手'),
]
RACE_UNITS = {
    '人族': '大法师、山丘之王、圣骑士、步兵、火枪手、骑士、狮鹫、女巫、牧师、飞行器、迫击炮、哨塔',
    '兽族': '剑圣、先知、牛头人酋长、苦工、兽人步兵、猎头者、狼骑、科多兽、双足飞龙、牛头人、萨满',
    '不死族': '死亡骑士、巫妖、恐惧魔王、侍僧、食尸鬼、蜘蛛（地穴恶魔）、石像鬼、冰龙、绞肉车、毁灭者',
    '暗夜精灵': '恶魔猎手、守望者、丛林守护者、小精灵、弓箭手、女猎手、树妖、熊德、山岭巨人、奇美拉、角鹰兽',
}
# 网络梗：最近 6 句里用过的，下一句点名别再用（09-18 实测「永动牛马」「好家伙」一轮出现三四次）
SLANG = ['好家伙', '牛马', '永动', '下次一定', '绷不住', '破防', '离谱', '摸鱼', 'KPI', '打工人', '内卷', '卷',
         '爷青回', 'DNA', '福报', '白嫖', '纯纯', '属实', '体验卡', '工伤', '下饭', '毁灭吧', '随便吧', '老铁']


@dataclass
class Persona:
    name: str
    traits: str          # 性格
    habit: str           # 说话习惯 / 口头禅方向
    color: str           # 名字颜色
    mood: int = 3        # MOODS 下标
    talk: float = 1.0    # 话多不多（被挑中的权重）
    reporter: bool = False   # 开局报幕优先挑他
    lines: int = 0


def default_cast() -> list[Persona]:
    return [
        Persona('小美', '数据党、报幕担当，记得每一局的场次和战绩，报数特别精确然后顺手补刀', '爱报数字、算胜率',
                '#FF9ED2', 3, 0.6, reporter=True),     # 报幕固定是她，平时少说点（09-18 实测 14 句里占 6 句）
        Persona('老王', '打工十年的老油条，看透一切，能摸鱼绝不多挖一镐', '动不动「想当年」，爱拿 Sky 说事', '#FFD060', 2, 1.3),
        Persona('小李', '00 后新来的，满嘴网络热梗，嫌这活没前途，天天嚷嚷跳槽去当民兵', '梗多、爱阴阳怪气', '#7FD8FF', 3, 1.3),
        Persona('铁柱', '卷王，每局抢着第一个进矿，KPI 狂魔，看不起摸鱼的', '张口效率闭口运营，崇拜 TH000', '#FF8A5C', 4, 1.0),
        Persona('翠花', '毒舌担当，专怼 AI 指挥官和老王，一针见血', '说话短、狠', '#E080FF', 2, 1.2),
        Persona('老张', '怀旧派老玩家，张口浩方、VS 平台、网吧通宵', 'Moon 粉，爱说爷青回', '#9AE07A', 3, 1.0),
        Persona('阿强', '阴谋论玄学派，坚信大家就是被拿来喂模型的训练数据', '「我们就是训练集」', '#80B0FF', 1, 0.9),
        Persona('二狗', '憨憨乐天派，有金子挖就开心，经常 get 不到别人的梗', '傻乐、容易被带偏', '#FFF080', 5, 0.9),
        Persona('大壮', '身在人族心在兽族，嘴上说人族好，心里崇拜 Grubby 和 Fly100%', '老说苦工待遇比我们好', '#FF6060', 3, 0.9),
        Persona('老刘', '佛系，已经放弃思考，觉得输赢跟自己没关系，只想下班', '「随便吧」「都行」', '#C8C8C8', 2, 0.8),
        Persona('阿飞', '自称当年网吧一霸，吹牛说自己 TR 过 Sky，没人信', '爱吹牛，被拆穿就嘴硬', '#FFB040', 4, 0.9),
        Persona('秀才', '文绉绉，爱拽古诗吐槽，偶尔冒出一句解说腔', '半文半白', '#B0E0D0', 3, 0.8),
    ]


SYSTEM = """你在给 B 站直播间写魔兽争霸3（冰封王座 1.27）的「{worker}小剧场」台词。
直播画面：一个 AI 正在自己操控{race}跟电脑打对战，一局接一局，打了很多局基本全输。
基地里的{worker}们是被这个 AI 指挥的打工人：天天被拉起来开局、挖金砍树、盖房子，给 AI 当训练数据，打完一局马上下一局，没有下班，没有周末。
他们互相吐槽、摸鱼、抱怨 AI 指挥官菜、回忆当年的 war3，像一个真实的打工人群聊。

观众：B 站 20~50 岁的老 war3 玩家，熟悉浩方、VS 平台、网吧通宵、冰封王座、TR、一波流、速矿、升本，
也熟悉 Sky、Moon、Grubby、TH000、Infi、Fly100%、Lyn、Happy、120、Ted 这些名字。

写作要求：
- 只写指定那个人的一句台词，口语化，吐槽口吻，有网感（打工人、摸鱼、内卷、破防、绷不住了、好家伙、离谱、下次一定、爷青回、DNA 动了 这类词自然地用，别堆砌）；
- 不超过 {max_chars} 个字，一到两句话；
- 可以接话、怼人、自嘲、翻旧账，要符合这个人的性格和此刻的心情；
- 提到高手时只拿他们的名气和种族玩梗，只能扯他那个种族的英雄和兵种，不要编造具体比赛、比分、年份、打法；
- 报战绩、说上一局只能用【战况】里写的数字和事实，不要编具体战斗过程（比如「那波一波流」「被偷塔」）；
- 不要旁白、不要动作描写、不要括号、不要引号、不要写说话人名字、不要表情符号；
- 别重复刚才对话里已经说过的梗和句式；
- 不碰现实政治、色情、地域攻击。"""


# ── 战绩 ──────────────────────────────────────────────────────
def _tail_result(path: Path) -> dict | None:
    """对局文件末尾的 result 行（没有 = 还没打完）。只读最后 16 KB。"""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            tail = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        if '"kind": "result"' in line or '"kind":"result"' in line:
            try:
                return json.loads(line).get('data')
            except ValueError:
                return None
    return None


class MatchStats:
    """某个实例的战绩：总局数、今天第几局、赢了几局、上一局的情况。20 秒缓存。"""

    def __init__(self):
        self._cache: dict[int, tuple[float, dict]] = {}
        self._file_cache: dict[str, tuple[float, int, dict | None]] = {}

    def get(self, inst: int | None) -> dict:
        if not inst:
            return {}
        hit = self._cache.get(inst)
        if hit and time.monotonic() - hit[0] < 20:
            return hit[1]
        rows = self._from_farsight(inst)
        if rows is None:
            rows = self._from_files(inst)
        out = self._summarize(rows)
        self._cache[inst] = (time.monotonic(), out)
        return out

    @staticmethod
    def _from_farsight(inst: int) -> list[dict] | None:
        try:
            with urllib.request.urlopen(f'{FARSIGHT}/api/i/{inst}/matches?limit=100000', timeout=3) as r:
                rows = json.loads(r.read())
        except Exception:  # noqa: BLE001
            return None
        return [x for x in rows if x.get('brain')]

    def _from_files(self, inst: int) -> list[dict]:
        folder = MATCH_ROOT / f'inst{inst}' / 'matches'
        rows = []
        for p in sorted(folder.glob('match-*.jsonl'), reverse=True):
            st = p.stat()
            hit = self._file_cache.get(p.name)
            if not hit or hit[0] != st.st_mtime or hit[1] != st.st_size:
                hit = (st.st_mtime, st.st_size, _tail_result(p))
                self._file_cache[p.name] = hit
            parts = p.stem.split('-')        # match-YYYYMMDD-HHMMSS-NNN-episode
            rows.append({'name': p.name, 'date': parts[1] if len(parts) > 1 else '', 'result': hit[2],
                         'finalized': hit[2] is not None, 'live': hit[2] is None and time.time() - st.st_mtime < 90})
        return rows

    @staticmethod
    def _summarize(rows: list[dict], today: str | None = None) -> dict:
        """today（'YYYYMMDD'）不给 = 本机今天；测试给固定值（以前写死当天，隔天测试就挂）。"""
        if not rows:
            return {}
        today = today or time.strftime('%Y%m%d')
        newest = rows[0]
        live = bool(newest.get('live'))
        total = len(rows) + (0 if live else 1)                 # 正在打的 / 下一局 是第几局
        today_n = sum(1 for r in rows if r.get('date') == today) + (0 if live else 1)
        wins = sum(1 for r in rows if (r.get('result') or {}).get('outcome') == 'victory')
        last = next((r for r in rows if r.get('finalized')), None)      # 正在打的那局没有 finalized
        return {'current': total, 'today': today_n, 'wins': wins, 'finished': sum(1 for r in rows if r.get('finalized')),
                'newest': newest.get('name'), 'live': live, 'last': last}


def describe_last(last: dict | None) -> str:
    if not last:
        return '上一局没有记录。'
    res = last.get('result') or {}
    race = RACE_CN.get(last.get('race') or '', '')
    foe = RACE_CN.get(last.get('foeRace') or '', '')
    diff = DIFFICULTY_CN.get(last.get('difficulty'), '')
    enemy = ('AMAI 电脑' if 'AMAI' in (last.get('map') or '') else '电脑') + (f'（{diff}）' if diff else '')
    bits = []
    head = f'{race}打{foe}{enemy}' if race and foe else f'打{enemy}'
    minutes = res.get('minutes')
    outcome = OUTCOME_CN.get(res.get('outcome') or '', res.get('outcome') or '结果不明')
    bits.append(f'{head}，{minutes:.0f} 分钟{outcome}' if minutes else f'{head}，{outcome}')
    if res.get('peakArmy') is not None and last.get('peakFoeArmy') is not None:
        bits.append(f'我方兵力最多 {res["peakArmy"]}、对面最多 {last["peakFoeArmy"]}')
    elif res.get('peakArmy') is not None:
        bits.append(f'我方兵力最多 {res["peakArmy"]}')
    if last.get('tier') in TIER_CN:
        bits.append(TIER_CN[last['tier']])
    if last.get('maxHalls'):
        bits.append(f'最多 {last["maxHalls"]} 座大厅')
    if res.get('peakWorkers'):
        bits.append(f'工人最多 {res["peakWorkers"]} 个')
    return '；'.join(bits) + '。'


# ── 台词清洗 ──────────────────────────────────────────────────
_EMOJI = re.compile('[\U0001F000-\U0001FFFF☀-➿️]')
_BRACKETS = re.compile(r'[（(【\[*][^）)】\]*]{0,40}[）)】\]*]')


def clean_line(text: str, names: list[str], max_chars: int) -> str:
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    lines = [s.strip() for s in text.strip().splitlines() if s.strip()]
    if not lines:
        return ''
    s = lines[0]
    for n in names:                                     # 「老王：」「老王说：」
        s = re.sub(r'^' + re.escape(n) + r'\s*(说)?\s*[:：]\s*', '', s)
    s = _BRACKETS.sub('', s)
    s = _EMOJI.sub('', s)
    s = s.strip().strip('"“”「」\'‘’').strip()
    if len(s) > max_chars:                              # 在标点处截断（至少留 40%），找不到就硬截
        cut = max((s.rfind(p, 0, max_chars) for p in '。！？!?，,…~'), default=-1)
        s = s[:cut + 1] if cut + 1 >= max_chars * 0.4 else s[:max_chars]
        s = s.rstrip('，,')                              # 截在逗号上就把逗号去掉
    return s.strip()


# ── 引擎 ──────────────────────────────────────────────────────
@dataclass
class BanterConfig:
    interval_s: float = 7.0          # 两句之间大概隔多久（会随机 ±30%，并且至少等上一句显示完）
    max_chars: int = 32
    stats_inst: int | None = None    # 战绩从哪个实例读；None = 游戏本身的实例号
    home_radius: float = 1800.0      # 离主基地多远以内算「家里」
    pro_every: int = 3               # 每几句带一次高手名字
    duration_s: float = 0.0          # 每句显示几秒；0 = 自动（按字数 3~5 秒）
    font_px: int = 0                 # 正文字号；0 = 自动（屏幕高 x 0.021，1080p 约 22）
    name_font_px: int = 0            # 名字字号；0 = 正文 x 0.8
    temperature: float = 0.95

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if k in self.__dataclass_fields__ and v is not None:
                setattr(self, k, v if k != 'stats_inst' else (int(v) or None))


@dataclass
class Line:
    t: float
    name: str
    text: str
    mode: str
    ms: int = 0


class Banter:
    """一个游戏实例的茶话会。snapshot() -> w3state.Snapshot | None；llm(messages, max_tokens, temperature) -> str；
    send(bubbles: list[w3speak.Bubble]) -> 回执。"""

    def __init__(self, pid: int, inst: int, snapshot, llm, send, stats: MatchStats, config: BanterConfig | None = None):
        self.pid, self.inst = pid, inst
        self._snapshot, self._llm, self._send, self._stats = snapshot, llm, send, stats
        self.config = config or BanterConfig()
        self.cast = default_cast()
        self.assign: dict[int, int] = {}              # 单位地址 -> cast 下标
        self.last_seen: dict[int, float] = {}
        self.transcript: deque[Line] = deque(maxlen=40)
        self.topics: deque[str] = deque(maxlen=5)
        self.last_error = ''
        self.running = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_at = 0.0
        self._pending_announce = True                 # 一打开先报一次幕
        self._match_key: tuple | None = None
        self._pro_used: dict[str, int] = {}
        self._recent_angles: deque[str] = deque(maxlen=4)
        self._since_pro = 0
        self._last_alarm = 0.0
        self._last_facts = ''
        self._lock = threading.Lock()

    # 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, name=f'banter-{self.pid}', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False

    def poke(self, topic: str) -> None:
        with self._lock:
            self.topics.append(topic.strip()[:120])
            self._next_at = 0.0

    def announce_now(self) -> None:
        with self._lock:
            self._pending_announce = True
            self._next_at = 0.0

    def status(self) -> dict:
        present = {i: a for a, i in self.assign.items()}
        return {
            'pid': self.pid, 'inst': self.inst, 'running': self.running and bool(self._thread and self._thread.is_alive()),
            'config': self.config.__dict__, 'last_error': self.last_error, 'facts': self._last_facts,
            'cast': [{'name': p.name, 'traits': p.traits, 'mood': MOODS[p.mood], 'color': p.color, 'lines': p.lines,
                      'unit': ('0x%08X' % present[i]) if i in present else None} for i, p in enumerate(self.cast)],
            'transcript': [{'t': round(ln.t), 'name': ln.name, 'text': ln.text, 'mode': ln.mode, 'ms': ln.ms}
                           for ln in list(self.transcript)[-30:]],
        }

    def _run(self) -> None:
        while not self._stop.wait(0.3):
            if time.monotonic() < self._next_at:
                continue
            try:
                self._step()
            except Exception as e:  # noqa: BLE001
                self.last_error = f'{time.strftime("%H:%M:%S")} {type(e).__name__}: {e}'
                self._next_at = time.monotonic() + 5.0

    # 一步：读快照、认人、挑一个人说一句
    def _step(self) -> None:
        now = time.monotonic()
        snap = self._snapshot()
        if not snap or not snap.in_game:
            self._next_at = now + 3.0
            return
        mine = [u for u in snap.units if u.owner == snap.me and u.hp > 0]
        halls = [u for u in mine if u.type in HALLS]
        workers = [u for u in mine if u.type in WORKERS]
        if not workers:
            self._next_at = now + 3.0
            return
        home = max(halls, key=lambda h: sum(1 for w in workers if (w.x - h.x) ** 2 + (w.y - h.y) ** 2 < 1.0e6)) \
            if halls else None
        hx = home.x if home else sum(w.x for w in workers) / len(workers)
        hy = home.y if home else sum(w.y for w in workers) / len(workers)
        r2 = self.config.home_radius ** 2
        at_home = [w for w in workers if (w.x - hx) ** 2 + (w.y - hy) ** 2 <= r2]

        # 新一局：快照 epoch（游戏界面对象的代数，每局新建一个）/ 战绩来源的最新对局文件，任何一个变了
        stats_inst = self.config.stats_inst or self.inst
        stats = self._stats.get(stats_inst)
        # 主基地地址不能进 key：开了分矿以后「家」会换到人多的那座，被当成新一局
        key = (snap.epoch, stats.get('newest') if stats_inst == self.inst else None)
        if self._match_key is not None and key != self._match_key:
            self._pending_announce = True
            self.assign.clear()
            for p in self.cast:                      # 又开一局：大家都更丧一点
                p.mood = max(0, p.mood - (1 if random.random() < 0.6 else 0))
        self._match_key = key

        self._assign(at_home, now)
        speakable = [(w, self.assign[w.addr]) for w in at_home if w.addr in self.assign]
        if not speakable:
            self._next_at = now + 3.0
            return

        foes_home = [u for u in snap.units if u.hp > 0 and u.owner != snap.me and u.owner < 12
                     and (u.x - hx) ** 2 + (u.y - hy) ** 2 < 1400 ** 2]
        worker_word = WORKERS.get(workers[0].type, '农民')
        race = RACE_OF_WORKER.get(workers[0].type, '人族')
        facts = self._facts(snap, stats, at_home, worker_word, len(foes_home))
        self._last_facts = facts

        with self._lock:
            topic = self.topics.popleft() if self.topics else None
            announce = self._pending_announce
            self._pending_announce = False
        mode = 'announce' if announce else 'topic' if topic else \
            'alarm' if len(foes_home) >= 2 and now - self._last_alarm > 40 else \
            'react' if self.transcript and time.time() - self.transcript[-1].t < 20 and random.random() < 0.7 else 'chat'
        if mode == 'alarm':
            self._last_alarm = now

        unit, idx = self._pick(speakable, mode)
        persona = self.cast[idx]
        pro = self._pick_pro() if mode in ('chat', 'react', 'topic') else None
        max_chars = self.config.max_chars + (16 if mode == 'announce' else 0)
        messages = self._messages(persona, mode, facts, worker_word, race, stats, topic, pro, max_chars)
        t0 = time.perf_counter()
        raw = self._llm(messages, 120, self.config.temperature)
        ms = int((time.perf_counter() - t0) * 1000)
        text = clean_line(raw, [p.name for p in self.cast], max_chars)
        if not text:
            self.last_error = f'{time.strftime("%H:%M:%S")} 模型回了空话：{raw[:60]!r}'
            self._next_at = time.monotonic() + 2.0
            return
        if self.config.duration_s and self.config.duration_s > 0:        # 网页上设了就照设的（1~30 秒）
            duration = int(min(30.0, max(1.0, float(self.config.duration_s))) * 1000)
        else:
            duration = int(min(5000, max(3000, 1800 + 110 * len(text))))
        self._say(unit, persona, text, duration)
        persona.lines += 1
        self.transcript.append(Line(time.time(), persona.name, text, mode, ms))
        if pro:
            self._pro_used[pro[0]] = self._pro_used.get(pro[0], 0) + 1
            self._since_pro = 0
        else:
            self._since_pro += 1
        if random.random() < 0.3:                       # 心情随便飘一飘
            persona.mood = min(len(MOODS) - 1, max(0, persona.mood + random.choice((-1, 1))))
        self.last_error = ''
        gap = duration / 1000 + 0.4
        if mode in ('announce', 'topic', 'alarm'):     # 报幕 / 话题 / 敌袭之后紧跟一句接话
            self._next_at = time.monotonic() + gap
        else:
            self._next_at = time.monotonic() + max(gap, self.config.interval_s * random.uniform(0.7, 1.3))

    def _assign(self, at_home, now: float) -> None:
        for w in at_home:
            self.last_seen[w.addr] = now
        # 90 秒没见到的放回去（进金矿会从快照里消失一阵子，别急着收回）
        for addr in [a for a, t in self.last_seen.items() if now - t > 90]:
            self.last_seen.pop(addr, None)
            self.assign.pop(addr, None)
        used = set(self.assign.values())
        # 报幕担当先上岗（开局报幕靠她），其余随机
        free = sorted((i for i in range(len(self.cast)) if i not in used),
                      key=lambda i: (not self.cast[i].reporter, random.random()))
        for w in sorted(at_home, key=lambda u: u.addr):
            if w.addr not in self.assign and free:
                self.assign[w.addr] = free.pop(0)

    def _pick(self, speakable, mode):
        last = self.transcript[-1].name if self.transcript else ''
        if mode == 'announce':
            rep = [s for s in speakable if self.cast[s[1]].reporter]
            if rep:
                return rep[0]
        pool = [s for s in speakable if self.cast[s[1]].name != last] or speakable
        weights = [self.cast[i].talk / (1 + 0.15 * self.cast[i].lines) for _, i in pool]
        return random.choices(pool, weights=weights)[0]

    def _pick_angle(self) -> str:
        # 09-18 实测：只给「抱怨挖矿、吐槽 AI」时几句话全绕着库存和人口打转 —— 轮换话头，最近用过的不再挑
        fresh = [a for a in ANGLES if a not in self._recent_angles] or ANGLES
        angle = random.choice(fresh)
        self._recent_angles.append(angle)
        return angle

    def _pick_pro(self):
        if self._since_pro + 1 < self.config.pro_every:
            return None
        least = min(self._pro_used.get(p[0], 0) for p in PROS)
        return random.choice([p for p in PROS if self._pro_used.get(p[0], 0) == least])

    def _facts(self, snap, stats: dict, at_home: list, worker_word: str, n_foes: int) -> str:
        rows = []
        if stats:
            # 输的局数直接给：模型自己拿「第几局 - 赢几局」去减，会把没打完的局也算成输（09-18：16 胜 107 负，实为 103）
            rows.append(f'- 这是 AI 的第 {stats["current"]} 局（今天第 {stats["today"]} 局）；'
                        f'前面打完 {stats["finished"]} 局，{stats["wins"]} 胜 {stats["finished"] - stats["wins"]} 负。')
            rows.append('- 上一局：' + describe_last(stats.get('last')))
        else:
            rows.append('- 场次没有记录（不知道打到第几局了）。')
        # 09-18 实测：只写「金 500」模型会读成「金矿就剩 500」—— 写明是库存
        now_bits = []
        if snap.gold is not None:
            now_bits.append(f'手上库存金子 {snap.gold}、木头 {snap.lumber}（是存款，不是金矿剩多少）')
        if snap.food_used is not None:
            now_bits.append(f'人口 {snap.food_used}/{snap.food_cap}')
        rows.append('- 现在：' + '，'.join(now_bits) + '。')
        rows.append(f'- 家里 {len(at_home)} 个{worker_word}：' + self._activity(snap, at_home) + '。')
        if n_foes >= 2:
            rows.append(f'- 家门口来了 {n_foes} 个敌方单位！')
        return '\n'.join(rows)

    @staticmethod
    def _activity(snap, at_home: list) -> str:
        """金矿边上 = 在挖金；没有订单 = 闲着；其余算在砍树/盖房/路上。"""
        mines = [u for u in snap.units if u.type == 'ngol']
        gold = idle = other = 0
        for w in at_home:
            if any((w.x - m.x) ** 2 + (w.y - m.y) ** 2 < 400 ** 2 for m in mines):
                gold += 1
            elif getattr(w, 'order', None) == 0:
                idle += 1
            else:
                other += 1
        bits = [f'{gold} 个在挖金'] if gold else []
        if other:
            bits.append(f'{other} 个在砍树或者盖房')
        if idle:
            bits.append(f'{idle} 个闲着没活干')
        return '，'.join(bits) or '看不清在干嘛'

    def _messages(self, persona: Persona, mode: str, facts: str, worker: str, race: str, stats: dict,
                  topic: str | None, pro, max_chars: int) -> list[dict]:
        system = SYSTEM.format(worker=worker, race=race, max_chars=max_chars)
        present = sorted(set(self.assign.values()))
        roster = '\n'.join(f'- {self.cast[i].name}：{self.cast[i].traits}；{self.cast[i].habit}；心情：{MOODS[self.cast[i].mood]}'
                           for i in present)
        recent = list(self.transcript)[-10:]
        convo = '\n'.join(f'{ln.name}：{ln.text}' for ln in recent) or '（还没人说话）'
        me = f'{persona.name}（{persona.traits}；心情：{MOODS[persona.mood]}）'
        if mode == 'announce':
            if stats:
                task = (f'新的一局刚开始。由{me}报幕：先说清楚这是第 {stats["current"]} 局、今天第 {stats["today"]} 局，'
                        f'再拿【战况】里「上一局」那一行写的事实（结局、几分钟、兵力对比、升本、大厅）损一句，'
                        f'那一行没写的一概别说。')
            else:
                task = f'新的一局刚开始。由{me}吐槽一句「又开工了」。'
        elif mode == 'topic':
            task = f'直播间有观众说：「{topic}」。轮到{me}接这句话。'
        elif mode == 'alarm':
            task = f'敌人打到家门口了！轮到{me}喊一句（慌、骂、甩锅都行）。'
        elif mode == 'react':
            task = (f'轮到{me}说话：接着上一句（{recent[-1].name}说的）吐槽、怼回去或者自嘲。'
                    f'换个角度接，别复读他的原话和数字。')
        else:
            task = f'轮到{me}说话：自己起个话头，这次聊「{self._pick_angle()}」。'
        if pro:
            name, prace, desc = pro
            task += (f'这句顺带提到 {name}（{desc}）—— 他是{prace}选手，要扯兵种英雄只能扯{prace}的'
                     f'（{RACE_UNITS[prace]}），别把别的种族的东西安到他头上。')
        worn = [w for w in SLANG if any(w in ln.text for ln in recent[-6:])]
        if worn:
            task += f'这些词最近用过了，这句别再用：{"、".join(worn)}。'
        user = (f'【战况】\n{facts}\n\n【在场的{worker}】\n{roster}\n\n【刚才的对话】\n{convo}\n\n【任务】\n{task}\n'
                f'只输出{persona.name}的这一句台词，不超过 {max_chars} 个字。')
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

    def _say(self, unit, persona: Persona, text: str, duration: int) -> None:
        import w3speak
        style = w3speak.Style(name_color=persona.color, type_ms=40, font_px=int(self.config.font_px or 0),
                              name_font_px=int(self.config.name_font_px or 0))
        self._send([w3speak.Bubble(unit=unit.addr, name=persona.name, text=text, duration_ms=duration, style=style)])

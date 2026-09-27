# -*- coding: utf-8 -*-
"""运营 / 战略顾问：把「该攒什么、该往哪投人、这一分钟该打还是该缓」交给本地大模型，
规则层只负责**执行和否决**。

## 为什么有这一层

09-21 复盘发现：运营层缺少持续的信息收集、判断和策略执行。运营决策很难用规则写好，
改为按阵营设计提示词，让 AI 做运营和战略层面的分析与输出，替代越堆越多的 if/else。

这个仓库有现成的证据：运营层的规则是一层一层贴上去的
（伐木人数一条、每矿 5 人一条、木头多了减半一条、金多木少多派一条……），
每条单独看都对，合起来会出现「矿上缺人而所有农民都在砍树、两边谁也调不动谁」这种
**没有任何一条规则负责**的局面 —— 09-21 那个采集调配 bug 正是如此。
这类「看全局、排优先级」的判断本来就不适合写成 if/else。

## 分层（改完之后）

    本地大模型（顾问）      每 COACH_EVERY 游戏秒一次，**异步**，1.1 秒出结果
        输入：一页局面快照（资源、人口、农民分布、矿、兵种、科技、英雄、敌情、最近发生的事）
        输出：严格 JSON —— 一句诊断 + 工人配比 + 优先出什么 + 这一分钟的姿态 + 不要做的事
            ↓   白名单 + 上下限钳位 + 否决（本文件）
    规则层（brain 每拍）    把它翻译成**已有能力的偏置**：工人配比、建造/训练优先级、进攻姿态
            ↓
    执行层（act / 毫秒层）  下令、读回执、微操

## 四条硬约束（都是这个仓库已经付过学费的）

1. **顾问永远不直接下单位令。** 它看不到 150ms 的现场，也会幻觉。它只改目标和优先级，
   具体谁去哪、打谁，仍由规则层和毫秒层决定 —— 指挥权只能有一个主人。
2. **异步。** 大脑一拍约 1 秒，顾问一次 1.1 秒（实测中位）。后台线程跑，最新结果生效，
   **永远不阻塞一拍**；模型没起来 / 超时 / 乱答，就当没有这一层，行为回到纯规则。
3. **白名单 + 钳位。** 每个字段都要能映射到已有能力，数值硬钳在合理区间；
   不认识的**记数后丢弃**，不是静默忽略（静默恒真的闸门看起来完全正常，不记数就发现不了）。
4. **全程计数。** 问过几次、成功几次、超时几次、被钳位几次、各字段被采纳几次。
   否则「这一层到底有没有用」是个不可回答的问题 —— 这个仓库栽过太多次了。

## 实测（tools/bench_coach.py，Qwen3.6-35B-A3B / LM Studio）

    中位 1.09 秒，最慢 1.45 秒，5/5 输出可直接 json.loads。

⚠ 必须走 `/v1/completions` 自己拼 ChatML 并**预填空的 `<think></think>`**：
  LM Studio 不理 `/no_think`，走 `/chat/completions` 时 400 个 token 会全被思考段吃掉，
  实测 5/5 一个 JSON 都出不来。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, field

try:                      # openwar3.json 的 llm_base / llm_model（和气泡、玩伴同一处配置）
    import w3paths
    BASE_URL, MODEL = w3paths.llm_base(), w3paths.llm_model()
except Exception:  # noqa: BLE001 - 单独拿出来跑（没有 sdk 在路径上）时用老默认值
    BASE_URL = "http://127.0.0.1:1234/v1"
    MODEL = "qwen3-8b"
COACH_EVERY = 20.0        # 游戏秒：问一次的间隔。运营决定的时间尺度是分钟，不需要每拍问
TIMEOUT = 8.0             # 秒：超过就当这一次没有（实测中位 1.1 秒，给 7 倍余量）
MAX_TOKENS = 260

# 工人配比的硬上下限：模型给的数只在这个区间里生效
GOLD_MIN, GOLD_MAX = 2, 25
LUMBER_MIN, LUMBER_MAX = 1, 20
PRIORITY_MAX = 4          # 最多接受几条「优先出什么」
POSTURES = ("attack", "defend", "creep", "expand", "recover", "hold")

# 不同阵营一套提示词（按阵营分别设计）。
# 只写**这一族特有的**取舍；通用规则在 COMMON 里，不要在四份里各抄一遍。
COMMON = """你是《魔兽争霸3》的运营教练。只管**运营和战略**（资源怎么分、先造什么、这一分钟是打还是缓），
不管微操和具体谁打谁 —— 那由执行层负责。

**只输出 JSON，不要解释、不要 markdown 代码块。字段固定，不要增删：**
{"diagnosis": <一句话，说局面里最大的问题，必须能在「局面」数据里找到依据>,
 "workers": {"gold": <整数>, "lumber": <整数>},
 "priority": [<代码>, ...],
 "posture": <下面六个之一>,
 "avoid": [<这一分钟不要做的事>, ...]}

- workers：农民去采金、砍树的**目标人数**。采金 = 在采的金矿数 × 5（每座矿最多 5 个，多了没用）；
  其余的人砍树。**金子远多于木头（比如金 > 4 × 木）时木头是瓶颈，砍树的人要多**；木头 >= 400 时砍树的可以少。
  两者加起来不要超过「局面」里的农民总数。
- priority：接下来优先出的单位/建筑/科技代码，最多 4 个，只能用「可选代码」里出现过的；
  **缺木头时别把要木头的东西排前面**；农民不够（采金 + 砍树的目标 > 农民总数）时把农民排第一。
- posture 只能是：attack（出击）defend（守家）creep（打野）expand（开矿）recover（回血整队）hold（攒兵）。
- avoid：最多 2 条。
- 只根据「局面」里的数据说话。数据里没有的东西（比如没列出来的兵种、没写的状态）不要编。
"""

RACE_PROMPTS = {
    "human": """你打人族。要点：
- 农民能协建，关键建筑（分矿大厅、卡人口的农场）可以两人一起敲。
- 民兵（Call to Arms）能临时清分矿野怪，清完记得解除。
- 圣骑士的圣光、恢复卷轴（神秘藏宝室 100 金）是人族续航的关键，残血多就该用，别硬扛。
- 狮鹫要三本 + 狮鹫笼；骑士要二本 + 兽栏升级。步兵后期不要堆太多。
可选代码：hfoo 步兵 hrif 火枪 hkni 骑士 hmtm 迫击炮 hmpr 牧师 hsor 女巫 hgry 狮鹫 hpea 农民
  hhou 农场 hbar 兵营 hlum 伐木场 hbla 铁匠铺 hars 神秘圣地 harm 车间 hgra 狮鹫笼 hvlt 藏宝室
  htow 城镇大厅 hkee 主城 hcas 城堡 Rhme 铁质武器 Rhar 铁甲 Rhlh 伐木改良 Rhpm 背包""",
    "orc": """你打兽族。要点：
- 苦工能进地洞躲避并射击，被骚扰时把苦工收进地洞。
- 大 G（步兵）和狼骑（诱捕）是主力；萨满/巫医是后排，别顶在前面。
- 兽族地洞既是人口也是防御，缺人口时优先补。
可选代码：ogru 步兵 ohun 猎头者 orai 狼骑 okod 科多兽 oshm 萨养 ocat 投石车 opeo 苦工
  otrb 地洞 obar 兵营 owtw 箭塔 ofor 铁匠铺 ogre 大厅 ostr 兵营升级 ofrt 要塞""",
    "undead": """你打亡灵。要点：
- 侍僧下地基就走，不占工；伐木靠食尸鬼，木头够了要把食尸鬼放回军队打仗。
- 闹鬼金矿直接盖在金矿上，开矿比别族快。
- 通灵僧/女妖是后排；凋零术和霜甲是核心科技。
可选代码：ugho 食尸鬼 ucry 蜘蛛 uabo 憎恶 unec 通灵僧 uban 女妖 uaco 侍僧
  uzig 萃取魔法 usep 地穴 utod 神殿 uslh 屠宰场 unpl 大墓地 ugol 闹鬼金矿""",
    "nightelf": """你打暗夜。要点：
- 小精灵缠绕金矿才能采；月井是人口也是回血，缺血时把部队拉到月井边。
- 战争古树/远古守护者可以拔起来移动。
- 女猎手前期，奇美拉/角鹰兽后期；树妖驱散。
可选代码：earc 弓箭手 esen 女猎手 edry 树妖 edoc 鸟德 edot 熊德 ewsp 小精灵
  emow 月井 eaom 战争古树 etol 生命之树 etoa 长者之树 etoe 永恒之树""",
}


def chatml(messages, prefill="{"):
    """⚠ LM Studio **不理** `/no_think` 和 enable_thinking=false（speak_api 的注释记过同一件事）。
    要自己拼 ChatML 并预填空的 `<think></think>` 把思考段关掉，再预填一个 `{` 逼它直接进正文。
    走 `/chat/completions` 实测 5/5 都把 400 个 token 用在思考段上、一个 JSON 都没出来。"""
    parts = [f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages]
    return "".join(parts) + "<|im_start|>assistant\n<think>\n\n</think>\n\n" + prefill


@dataclass
class Plan:
    """钳位之后的建议。规则层只读这里，不读模型原文。"""
    at: float = -1e9
    diagnosis: str = ""
    gold: int | None = None
    lumber: int | None = None
    priority: tuple = ()
    posture: str = ""
    avoid: tuple = ()
    latency: float = 0.0

    def fresh(self, now: float, ttl: float = 3 * COACH_EVERY) -> bool:
        """太旧的建议不要用：局面早就变了。模型挂掉时这条保证行为退回纯规则。"""
        return now - self.at <= ttl


@dataclass
class Coach:
    race: str = "human"
    enabled: bool = False
    base_url: str = BASE_URL
    model: str = MODEL
    every: float = COACH_EVERY
    counts: Counter = field(default_factory=Counter)
    notes: list = field(default_factory=list)
    _plan: Plan = field(default_factory=Plan)
    _asked_at: float = -1e9
    _busy: bool = False
    _lock: object = field(default_factory=threading.Lock)
    # ★ 09-21 大模型运营层是否在运行无从确认，需要能在远见后台直接看到。
    #   当时 LM Studio 根本没起来，而大脑这边**看不出来** —— 心跳只打印前 10 个计数，
    #   「顾问·出错·URLError」排不进去；行为又和「没有这一层」完全一样（设计如此）。
    #   能安全降级的东西，最容易悄悄降级一整天没人知道。所以最近一次的结果单独记、发布出去。
    last_try: float = 0.0          # 墙钟：最近一次发出请求
    last_ok: float = 0.0           # 墙钟：最近一次拿到可用的建议
    last_error: str = ""           # 最近一次失败的原因（成功后清空）
    latencies: deque = field(default_factory=lambda: deque(maxlen=20))
    history: deque = field(default_factory=lambda: deque(maxlen=6))   # 最近几条建议（给远见看的）
    # 最后一次发给模型的局面。没有它就回答不了「它为什么这么说」——
    # 09-21 第一版的快照把开局自动采矿的农民数成 0，4 个顾问一起诊断「金矿无人采集」，
    # 是远见上把输入和输出摆在一起才一眼看出来的。
    last_input: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- 对外

    def plan(self, now: float) -> Plan | None:
        """当前有效的建议；没有 / 过期 / 关掉了都返回 None（= 行为和没有这一层完全一样）。"""
        if not self.enabled:
            return None
        with self._lock:
            p = self._plan
        return p if p.at > -1e9 and p.fresh(now) else None

    def tick(self, now: float, snapshot: dict) -> None:
        """非阻塞：到点了就起一个后台请求。**任何情况下都不会让这一拍变慢。**"""
        if not self.enabled or self._busy or now - self._asked_at < self.every:
            return
        self._asked_at = now
        self._busy = True
        threading.Thread(target=self._run, args=(now, snapshot), daemon=True).start()

    # ---------------------------------------------------------------- 内部

    def _run(self, now, snapshot):
        self.last_try = time.time()
        self.last_input = snapshot
        try:
            t0 = time.perf_counter()
            text = self._ask(snapshot)
            dt = time.perf_counter() - t0
            obj = parse_json(text)
            if obj is None:
                self.counts["顾问·答复不是 JSON"] += 1
                self.last_error = "答复不是 JSON：" + (text or "")[:60]
                return
            plan = self.clamp(obj, now, dt)
            with self._lock:
                self._plan = plan
            self.counts["顾问·采纳"] += 1
            self.last_ok, self.last_error = time.time(), ""
            self.latencies.append(dt)
            self.history.append(dict(t=round(now, 1), wall=self.last_ok, **plan_dict(plan)))
            self.notes.append(f"顾问（{dt:.1f} 秒）：{plan.diagnosis}｜"
                              f"采金 {plan.gold} 伐木 {plan.lumber}｜姿态 {plan.posture}｜"
                              f"优先 {'/'.join(plan.priority) or '—'}")
        except TimeoutError:
            self.counts["顾问·超时"] += 1
            self.last_error = f"超时（>{TIMEOUT:.0f} 秒）"
        except Exception as e:  # noqa: BLE001 - 顾问出任何问题都不能影响这一局
            self.counts[f"顾问·出错·{type(e).__name__}"] += 1
            self.last_error = f"{type(e).__name__}：{str(e)[:80]}"
        finally:
            self._busy = False

    def live(self, now: float) -> dict:
        """发布给远见（brain_live.json 的 coach 段）。

        state 四选一，**每一种都要能从外面分得清**（一个「没动静」不能同时表示四件事）：
          off        开关关着
          waiting    开着，但还没问过（开局前 25 游戏秒 job 还没到点）
          down       问过、最近一次失败（模型没起来 / 超时 / 答非所问）
          ok         最近一次拿到了可用的建议
        """
        if not self.enabled:
            state = "off"
        elif not self.last_try:
            state = "waiting"
        elif self.last_error and self.last_try >= self.last_ok:
            state = "down"
        else:
            state = "ok"
        with self._lock:
            p = self._plan
        lat = sorted(self.latencies)
        return {
            "state": state, "enabled": self.enabled, "model": self.model, "baseUrl": self.base_url,
            "busy": self._busy, "every": self.every,
            "lastTry": self.last_try or None, "lastOk": self.last_ok or None, "lastError": self.last_error,
            "latencyMedian": round(lat[len(lat) // 2], 2) if lat else None,
            "latencyMax": round(lat[-1], 2) if lat else None,
            "plan": plan_dict(p) if p.at > -1e9 else None,
            "planAge": round(now - p.at, 1) if p.at > -1e9 else None,
            "planFresh": p.at > -1e9 and p.fresh(now),
            "history": list(self.history),
            "input": self.last_input,
            "counts": dict(self.counts),
        }

    def _ask(self, snapshot) -> str:
        system = COMMON + "\n" + RACE_PROMPTS.get(self.race, RACE_PROMPTS["human"])
        user = "局面：\n" + json.dumps(snapshot, ensure_ascii=False)
        body = {"model": self.model, "temperature": 0.3, "max_tokens": MAX_TOKENS, "top_p": 0.92,
                "prompt": chatml([{"role": "system", "content": system},
                                  {"role": "user", "content": user}]),
                "stop": ["<|im_end|>", "<|im_start|>"]}
        req = urllib.request.Request(self.base_url.rstrip("/") + "/completions",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        self.counts["顾问·问"] += 1
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = json.loads(r.read())
        return "{" + raw["choices"][0]["text"]

    def clamp(self, obj: dict, now: float, latency: float = 0.0) -> Plan:
        """白名单 + 上下限。**每一次钳位都记数** —— 模型老在某一项上越界，是提示词该改的信号。"""
        p = Plan(at=now, latency=latency)
        p.diagnosis = str(obj.get("diagnosis") or "")[:60]
        w = obj.get("workers") or {}
        if isinstance(w, dict):
            p.gold = _clamp_int(w.get("gold"), GOLD_MIN, GOLD_MAX, self.counts, "采金人数")
            p.lumber = _clamp_int(w.get("lumber"), LUMBER_MIN, LUMBER_MAX, self.counts, "伐木人数")
        pr = obj.get("priority")
        if isinstance(pr, list):
            # ⚠ rawcode 一定是 4 个 ASCII 字母数字（hkni / Rhme）。只按长度过滤会放过
            #   「造一堆兵」这种**正好四个汉字**的自然语言（测试抓到的）—— 它进不了需求表，
            #   只会变成一条「表里没有」的噪声计数。
            ok = [str(c) for c in pr
                  if isinstance(c, str) and 3 <= len(c) <= 4 and c.isascii() and c.isalnum()]
            if len(ok) != len(pr):
                self.counts["顾问·钳位·优先项不是代码"] += 1
            p.priority = tuple(ok[:PRIORITY_MAX])
        post = obj.get("posture")
        if isinstance(post, str) and post in POSTURES:
            p.posture = post
        elif post:
            self.counts[f"顾问·钳位·姿态不认识·{str(post)[:12]}"] += 1
        av = obj.get("avoid")
        if isinstance(av, list):
            p.avoid = tuple(str(x)[:40] for x in av[:2])
        return p


def plan_dict(p: Plan) -> dict:
    return {"diagnosis": p.diagnosis, "gold": p.gold, "lumber": p.lumber, "priority": list(p.priority),
            "posture": p.posture, "avoid": list(p.avoid), "latency": round(p.latency, 2), "at": round(p.at, 1)}


def _clamp_int(v, lo, hi, counts, label):
    try:
        n = int(v)
    except (TypeError, ValueError):
        if v is not None:
            counts[f"顾问·钳位·{label}不是数字"] += 1
        return None
    if n < lo or n > hi:
        counts[f"顾问·钳位·{label}越界"] += 1
    return max(lo, min(hi, n))


def parse_json(text: str):
    """模型吐的东西 -> dict。允许它裹代码块或多说两句，但不猜内容。"""
    s = (text or "").strip()
    if "</think>" in s:
        s = s.split("</think>", 1)[1].strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def snapshot(w, brain) -> dict:
    """一页局面。给模型看的东西要**少而准**：它读不完也记不住一百个字段，
    而且每多一个字段就多一条它可能拿来幻觉的线索。这里只放运营决定真正需要的。"""
    from collections import Counter as C
    from .act import HARVEST_ORDERS
    army = C(u.type for u in w.my_army)
    # ⚠⚠ 09-21 第一版这里用的是大脑自己的**派工名单**（brain.gold_crew / lumber_crew）。
    #   开局那 5 个农民是游戏自动派去采矿的、不是大脑派的，名单里是 0 ——
    #   于是远见上 6 个实例一律显示「农民 10（采金 0 / 伐木 1）」，而 4 个顾问
    #   **全都诊断「金矿无人采集 / 农民分配严重失衡」**：模型在对着一个快照造出来的假问题出主意。
    #   量之前先问这个数在量什么：名单 = 「我派过谁」，不是「谁在采」。
    #   现在按**现场**数：采集订单 + 离哪座矿 400 内算采金，其余在采集的算伐木；
    #   进了矿井快照里看不见的，名单里有、画面上没有的那几个算采金（和 runtime.job_gold 同一个口径）。
    mine_list = [m for m in getattr(w, "mines", []) if m.type in ("ngol", "ugol", "egol")]
    visible = {u.addr for u in w.my_workers}
    on_gold = C()
    lumber_n = idle_n = 0
    for u in w.my_workers:
        if (u.order or 0) in HARVEST_ORDERS:
            near = min(mine_list, key=lambda m: abs(m.x - u.x) + abs(m.y - u.y), default=None)
            if near is not None and ((near.x - u.x) ** 2 + (near.y - u.y) ** 2) ** .5 <= 400:
                on_gold[near.addr] += 1
            else:
                lumber_n += 1
        elif (u.order or 0) == 0:
            idle_n += 1
    for a, mid in brain.gold_crew.items():
        if a not in visible:
            on_gold[mid] += 1
    cap = int(brain.profile.get("peons_per_mine", 5))
    mines = [{"workers": on_gold[m.addr], "cap": cap} for m in mine_list
             if on_gold[m.addr] or any(abs(m.x - h.x) + abs(m.y - h.y) < 1600 for h in w.my_halls)]
    hurt = [u for u in w.my_army if u.hp_max > 0 and u.hp < 0.5 * u.hp_max]
    return {
        "race": brain.race, "enemyRace": getattr(w, "foe_race", None) or "?",
        "gameMinute": round(w.t / 60.0, 1),
        "gold": int(w.gold), "lumber": int(w.lumber),
        "food": f"{int(w.food_used)}/{int(w.food_cap)}",
        "workers": {"total": len(w.my_workers) + sum(1 for a in brain.gold_crew if a not in visible),
                    "onGold": sum(on_gold.values()), "onLumber": lumber_n, "idle": idle_n},
        "mines": mines, "halls": len(w.my_halls),
        # 建筑也要给：不给的话它只能猜 —— 09-21 用 1-262 的局面问，三次里有两次说「无法建造兵营」，
        # 而那局兵营早就有了（快照里有 7 个步兵，却没有建筑这一栏）。
        "buildings": dict(C(u.type for u in w.my_buildings)),
        "army": dict(army), "hurtUnits": len(hurt),
        "heroes": [{"type": h.type, "hpPct": int(100 * h.hp / max(1.0, h.hp_max))} for h in w.my_heroes],
        "enemyArmy": len(w.enemy_fighters), "enemyHalls": len(w.enemy_halls),
        "ownStrength": round(w.own_strength, 1), "enemyStrength": round(w.foe_strength, 1),
    }

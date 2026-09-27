# -*- coding: utf-8 -*-
"""AMAI 战略引擎：开局选战略/英雄 -> 每 3 游戏秒跑一遍建造序列 -> 每 5~6 秒走一遍建造队列。

runtime.job_build 在 `strategy_engine == "amai"` 时把需求来源换成这里（默认 "table" 还是手写表，一行都不变）。
时序照 AMAI（docs/amai_strategy_spec.md 附录 B）：
  build_sequence_all（3×slm 秒）：换战略检查 -> 换反制 -> 换本清队列 -> build_sequence() -> 抽反制兵种
  BuildLoopAM（5~6×slm 秒）：农场/农民/开矿/复活 -> 按优先级走队列（升本闸、维护费闸、钱不够就停）
  UPDATE_STRENGTH（第一次 50~65 秒，之后每 15 秒）：enemy_* / ally_*
"""
from __future__ import annotations

import math
import random

from .. import amai as AMAI
from ..build import Want
from . import choose as C
from . import data as D
from . import strength as S
from .expr import Evaluator
from .queue import BuildQueue, Entry
from .seq import Sequencer

TOWN_RADIUS = 1200.0        # 算"这座分矿有几座塔"的半径（AMAI 是引擎的城镇概念，我们按离大厅的距离）
MINE_NEAR = 1000.0          # 大厅离金矿这么近算"这座矿在采"（和 runtime._expansion_mine 同一个量级）
EXPANSION_RADIUS = 1250.0   # expansion_radius（GlobalSettings）
BUILD_PERIOD = 5.0          # 建造循环周期（游戏秒）：AMAI 是 5~6×slm
SEQ_PERIOD = 3.0            # 建造序列周期，等于 runtime 的 job_build 频率


def _done(u) -> bool:
    """建成没有（和 runtime.completed 同一条判据：见过 97% 血就算成品；两处互相点名，别只改一处）。"""
    return getattr(u, "hp_max", 0) > 0 and u.hp >= 0.97 * u.hp_max


class WorldEnv:
    """把 brain/world 的世界状态翻成 AMAI 脚本要的那些取值函数。"""

    def __init__(self, brain, world, strat: "AmaiStrategy"):
        self.b = brain
        self.w = world
        self.s = strat
        self.data = strat.data
        self._committed = None

    # ── 时间 / 资源 ──
    def ai_time(self) -> float:
        return self.w.t / 5.0                      # common.eai:12235 TimeKeeper

    def elapsed(self) -> float:
        return self.w.t

    def gold(self) -> int:
        return int(self.w.gold)

    def wood(self) -> int:
        return int(self.w.lumber)

    def _commit(self):
        if self._committed is None:
            self._committed = self.b.committed()   # 已下令、还没扣钱的建筑
        return self._committed

    def gold_available(self) -> int:
        return max(0, self.gold() - self._commit()[0])

    def wood_available(self) -> int:
        return max(0, self.wood() - self._commit()[1])

    def food_used(self) -> int:
        return int(self.w.food_used)

    def food_cap(self) -> int:
        return int(self.w.food_cap)

    def food_limit(self) -> int:
        """AMAI 用 ver_food_limit（100）当上限、靠引擎挡人口；我们用真实人口上限，免得下一堆会被引擎退的令。"""
        return int(self.w.food_cap)

    def tier(self) -> int:
        return int(self.w.tier)

    # ── 数量 ──
    def _pending(self, code: str) -> int:
        return sum(1 for p in self.b.pending if p.get("code") == code)

    def town_count(self, code: str | None, only_done: bool = False, town: int = -1) -> int:
        """TownCountEx（common.eai:2690）：本体 + "一起数的"（主城算上二本三本、农民算上民兵、哨塔算上各种塔）。"""
        if not code:
            return 0
        total = 0
        for c in [code] + D.count_alts(code):
            total += self.unit_count(c, only_done=only_done, town=town)
        return total

    def unit_count(self, code: str | None, only_done: bool = False, town: int = -1) -> int:
        """GetUnitCountEx：只数这一个码本身。含在建的（only_done=False 时）和已下令还没出现的。"""
        if not code:
            return 0
        if town != -1:
            hall = next((h for h in self.w.my_halls if h.addr == town), None)
            if hall is None:
                return 0
            return sum(1 for u in self.w.mine if u.type == code and (not only_done or _done(u))
                       and math.hypot(u.x - hall.x, u.y - hall.y) <= TOWN_RADIUS)
        if only_done:
            return sum(1 for u in self.w.mine if u.type == code and _done(u))
        return int(self.w.counts.get(code, 0)) + self._pending(code)

    def upgrade_level(self, code: str | None) -> int:
        if not code:
            return 0
        try:
            return int(self.b.have(self.w, Want("upgrade", code, 1, 0)))
        except Exception:                          # noqa: BLE001
            return 0

    def item_count(self, code: str | None) -> int:
        return 0                                   # 物品还没接（mod_scavenge 只捡地上的）

    def neutral_available(self, code: str | None) -> bool:
        return False                               # 酒馆英雄 / 雇佣兵 / 龙：执行层买不了

    def build_free(self) -> dict:
        """SetBuildFree：每轮开头，每种生产建筑"空闲的座数" = 已建成的座数。"""
        free = {}
        for b in self.w.my_buildings:
            if _done(b):
                free[b.type] = free.get(b.type, 0) + 1
        return free

    def halls_completed(self, code: str) -> bool:
        return self.unit_count(code, only_done=False) == self.unit_count(code, only_done=True)

    def hall_upgrading(self) -> bool:
        return any(p.get("kind") == "upgrade_hall" for p in self.b.pending)

    def can_build(self, code: str) -> bool:
        """CanBuildUnit 的近似：前置建筑都建成了。"""
        n1, n2, n3 = D.needed(code)
        for n in (n1, n2, n3):
            if n and n != D.UPGRADED and not D.is_upgraded_from(code) and self.town_count(n, only_done=True) < 1:
                return False
        return True

    # ── 钱 / 能不能下（走 runtime 的那两个口）──
    def cost(self, want: Want) -> tuple:
        try:
            return self.b.cost(self.w, want)
        except Exception:                          # noqa: BLE001
            return D.cost(want.code)

    def check(self, want: Want) -> str | None:
        try:
            return self.b.check(self.w, want)
        except Exception as exc:                   # noqa: BLE001
            return f"检查出错 {type(exc).__name__}"

    def town_threatened(self) -> bool:
        return getattr(self.b, "defense_mode", "none") in ("defend", "toostrong", "last_stand")

    def town_alive(self, addr: int) -> bool:
        return any(h.addr == addr for h in self.w.my_halls)

    def towns(self, include_main: bool = False) -> list:
        """有主城 + 旁边有矿的城镇；main=主基地。DefendTowns* 默认不算主基地（common.eai:10843）。"""
        main = self.w.main_town()
        out = []
        for h in self.w.my_halls:
            if not _done(h):
                continue
            near_mine = any(math.hypot(h.x - m.x, h.y - m.y) <= MINE_NEAR for m in self.w.mines)
            is_main = bool(main and main.hall is not None and h.addr == main.hall.addr)
            if not near_mine:
                continue
            if is_main and not include_main:
                continue
            dist = math.hypot(h.x - main.x, h.y - main.y) if main else 0.0
            out.append({"id": h.addr, "main": is_main, "dist": dist, "x": h.x, "y": h.y})
        return out

    # ── 矿 / 开矿 ──
    def mines_harvested(self) -> int:
        """GetMinesHarvested：亡灵数闹鬼金矿、暗夜数缠绕金矿，其余数"有主城又有矿"的城镇。"""
        race = self.data.race
        if race == "undead":
            return self.unit_count("ugol", only_done=True)
        if race == "nightelf":
            return self.unit_count("egol", only_done=True)
        n = 0
        for h in self.w.my_halls:
            if _done(h) and any(math.hypot(h.x - m.x, h.y - m.y) <= MINE_NEAR for m in self.w.mines):
                n += 1
        return n

    def mines_owned(self) -> int:
        return self.mines_harvested()

    def has_expanded(self) -> bool:
        return self.mines_harvested() > 1 or self.unit_count(self.data.racial("racial_expansion"), only_done=True) > 1

    def expand_count(self) -> int:
        try:
            return int(self.b.have(self.w, Want("expand", self.data.racial("racial_expansion"), 1, 0)))
        except Exception:                          # noqa: BLE001
            return self.mines_harvested()

    def expansion_mine(self):
        try:
            return self.b._expansion_mine(self.w)
        except Exception:                          # noqa: BLE001
            return None

    def expansion_available(self) -> bool:
        """first_expansion_chosen：地图上还有能开的矿。"""
        return self.expansion_mine() is not None

    def expansion_strength(self) -> float:
        m = self.expansion_mine()
        if m is None:
            return 0.0
        return S.creep_strength_sum(self.w.creeps, m.x, m.y, EXPANSION_RADIUS)

    def own_strength(self) -> float:
        return float(self.w.own_strength)

    def mark_clear_expansion(self, mine) -> bool:
        """分矿旁边有野怪：告诉战术层先去清（AMAI 的 take_exp —— 进攻线程优先清分矿野怪，排在打人之前）。
        ★ 09-18 实机（LT、Echo Isles）：这些图的分矿全都有野怪，原来这里只记一句「还没到时候」就返回，
        结果 25 游戏分钟还是一座大厅、地图上的矿全被对手占走。和 table 模式 runtime.check 走的是同一条路。"""
        from .. import tactics as T
        import math
        guard = [c for c in self.w.creeps if math.hypot(c.x - mine.x, c.y - mine.y) <= T.EXPAND_GUARD_R]
        if not guard:
            return False
        self.b.attack.clear_target = dict(ids={c.addr for c in guard}, x=mine.x, y=mine.y,
                                          strength=T.creep_strength(guard))
        return True

    def peons_saturated(self) -> bool:
        """QueueExpansionPeonsIfNeeded：现有矿的工人没到目标数就先不开新矿，同时提前多要工人。"""
        mines = self.mines_harvested()
        if mines <= 0:
            return True
        peon = self.data.racial("racial_peon")
        done = self.unit_count(peon, only_done=True)
        cur = self.s.peon_target(mines, self.wood())
        nxt = self.s.peon_target(mines + 1, self.wood())
        if done < nxt:
            self.s.pending_expansion_peons = max(self.s.pending_expansion_peons, nxt)
        return done >= cur

    def rush_factor(self) -> int:
        """GetRushFactor：到最近敌方主基地的距离 - 1000×(敌人数 -(盟友数+1))。"""
        main = self.w.main_town()
        if main is None or not self.w.enemy_halls:
            return 10000
        d = min(math.hypot(h.x - main.x, h.y - main.y) for h in self.w.enemy_halls)
        return int(d) - 1000 * (self.c_enemy_total() - (self.c_ally_total() + 1))

    # ── 收入估算（生产建筑要几座）──
    def gold_income(self) -> float:
        inc = self.mines_harvested() * self.s.income_per_mine
        upkeep = 1.0
        for row in self.s.queue.upkeep.rows:
            if self.food_used() >= row["border"]:
                upkeep = row["gold_change"]
        return inc * upkeep + self.gold() * self.gold_unit_percentage()

    def gold_unit_percentage(self) -> float:
        return float(C.profile_value(self.s.profile or {}, "influences number of factories built", 0.9))

    def difficulty(self) -> int:
        return self.s.difficulty

    # ── 局面 ──
    def c_enemy_total(self) -> int:
        return len({u.owner for u in self.w.enemies}) or 1

    def c_ally_total(self) -> int:
        return 0

    def c_enemy(self, race: str) -> int:
        return 1 if self.w.foe_race == race else 0

    def c_ally(self, race: str) -> int:
        return 0

    def c_enemy_random(self) -> int:
        return 0                                   # 大厅里选"随机"的敌人我们看不到（看到的已经是实际种族）

    def is_1on1(self) -> bool:
        return self.c_enemy_total() == 1 and self.c_ally_total() == 0

    def is_ffa(self) -> bool:
        return False

    def enemy(self, cat: str) -> float:
        return self.s.strengths.enemy.get(cat, 0.0)

    def ally(self, cat: str) -> float:
        return self.s.strengths.ally.get(cat, 0.0)

    def heroes(self) -> list:
        return [h for h in self.s.heroes if h]

    def hero_built(self, slot: int) -> bool:
        code = self.s.heroes[slot - 1] if 0 < slot <= len(self.s.heroes) else None
        if not code:
            return False
        return self.w.counts.get(code, 0) > 0 or code in self.s.heroes_seen

    # ── 表达式里的变量 / 常量 ──
    def var(self, name: str):
        if name == "tier":
            return self.tier()
        if name.startswith("enemy_"):
            return self.enemy(name[6:])
        if name.startswith("ally_"):
            return self.ally(name[5:])
        if name == "c_enemy_total":
            return self.c_enemy_total()
        if name == "c_ally_total":
            return self.c_ally_total()
        if name == "nearest_enemy":
            return "nearest_enemy"
        if name in ("towerrush", "militia_expanding", "AatpAbility"):
            return False                            # 塔攻 / 民兵开矿 / 1.30 的前向兼容技能：都没接
        if name in ("militia_state", "urgent_healing_count"):
            return 0
        v = self.data.setting(name, None)           # ghoul_prio 这类就是 Settings 里的值
        if v is None:
            self.miss(f"变量·{name}")
            return 0
        return v

    def var_index(self, name: str, index):
        if name == "c_enemy":
            race = D.RACES[int(index) - 1] if 1 <= int(index) <= len(D.RACES) else None
            return self.c_enemy(race) if race else 0
        if name == "c_ally":
            return 0
        if name == "hero_skills":
            i = int(index) - 1
            lines = C.SKILL_LINES
            line = self.s.skill_lines[i] if 0 <= i < len(self.s.skill_lines) else "major"
            return lines.index(line)
        self.miss(f"数组·{name}")
        return 0

    def const(self, name: str):
        if name in ("RACE_UNDEAD", "RACE_NIGHTELF", "RACE_HUMAN", "RACE_ORC"):
            return {"RACE_UNDEAD": "undead", "RACE_NIGHTELF": "nightelf",
                    "RACE_HUMAN": "human", "RACE_ORC": "orc"}[name]
        if name == "HARASS_TARGET_PEONS":
            return 0
        if name in ("HERO_SKILL_MAJOR", "HERO_SKILL_MINOR", "HERO_SKILL_RARE"):
            return {"HERO_SKILL_MAJOR": 0, "HERO_SKILL_MINOR": 1, "HERO_SKILL_RARE": 2}[name]
        if name == "MAP_RANDOM_HERO":
            return name
        self.miss(f"常量·{name}")
        return 0

    def map_flag(self, name: str | None) -> bool:
        return False                                # 地图没开"随机英雄"

    def player_race(self, node) -> str:
        return self.w.foe_race or ""

    def player_unit_count(self, player, code: str | None) -> int:
        return 0                                    # 只在强度预测规则里用，那边走专门的求值环境

    def random_int(self, a: int, b: int) -> int:
        return self.s.rng.randint(int(a), int(b))

    def hero(self, n: int) -> str | None:
        return self.s.heroes[n - 1] if 0 < n <= len(self.s.heroes) else None

    def miss(self, key: str) -> None:
        self.s.count(f"取不到·{key}")


class _PredEnv:
    """强度预测规则（StrengthPredictionRules）专用的求值环境：只用到 GetPlayerUnitTypeCount(p, 建筑)。"""

    def __init__(self, counts: dict):
        self.counts = counts

    def player_unit_count(self, player, code):
        return int(self.counts.get(code, 0)) if code else 0

    def var(self, name):
        return name

    def var_index(self, name, index):
        return 0

    def const(self, name):
        return name

    def map_flag(self, name):
        return False

    def player_race(self, node):
        return ""

    def random_int(self, a, b):
        return a

    def hero(self, n):
        return None

    def town_count(self, code, only_done=False, town=-1):
        return 0

    def unit_count(self, code, only_done=False, town=-1):
        return 0

    def upgrade_level(self, code):
        return 0

    def gold(self):
        return 0

    def wood(self):
        return 0

    def mines_owned(self):
        return 0

    def rush_factor(self):
        return 0

    def has_expanded(self):
        return False

    def miss(self, key):
        pass


class AmaiStrategy:
    """一局一个。runtime 在 strategy_engine == "amai" 时持有它。"""

    def __init__(self, race: str, rng=None, difficulty: int = 2, players: int = 2, map_size: int = 2,
                 allow_neutral_heroes: bool = False, build_period: float = BUILD_PERIOD):
        self.data = D.AmaiData(race)
        self.race = race
        self.rng = rng if rng is not None else random.Random()
        self.difficulty = int(difficulty)
        self.players = int(players)
        self.map_size = int(map_size)
        self.allow_neutral_heroes = bool(allow_neutral_heroes)
        self.build_period = float(build_period)
        self.income_per_mine = 10 * max(self.difficulty - 1, 1)     # common.eai:9268
        self.queue = BuildQueue(self.data, self.rng)
        self.seq = Sequencer(self.data, self.queue, self.rng)
        self.strengths = S.StrengthTracker(self.data, 5, self.rng)
        self.profile: dict | None = None
        self.strategy: str | None = None
        self.last_strategy: str | None = None
        self.heroes: list = []
        self.heroes_seen: set = set()
        self.skill_lines: list = ["major", "major", "major"]
        self.chosen_counter: int | None = None
        self.no_counter = True
        self.set_counter = True
        self.reset_counter = False
        self.counter_timer = -60
        self.x = -120
        self.tier = 1
        self.exp_first_time = 0.0
        self.exp_second_time = 0.0
        self.exp_time_count = 0.0
        self.fast_expa_checked = False
        self.pending_expansion_peons = 0
        self.race_aggression: dict = {}
        self.started = False
        self.next_build_at = 0.0
        self.counts: dict = {}
        self.notes: list = []
        self.history: list = []          # 换战略的流水：(游戏秒, 旧, 新, 原因)
        self.last_change_t = 0.0
        self.last_change_why = "开局"
        self.last_round = None

    # ── 小工具 ──
    def count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    def note(self, t: float, text: str) -> None:
        self.notes.append((round(t, 1), text))
        if len(self.notes) > 40:
            del self.notes[:-40]

    def setting_int(self, name: str, default: int = 0) -> int:
        return self.data.setting_int(name, default)

    def peon_target(self, mines: int, wood: float) -> int:
        """GetPeonTargetForMines（common.eai:9931）—— 实现在 data.peon_target，手写建造表也用那一份。"""
        return D.peon_target(self.data, mines, wood)

    # ── 开局 ──
    def start(self, env: WorldEnv) -> None:
        self.profile = C.choose_profile(self.data, self.rng)
        self.strengths.area_uncertainty = int(C.profile_value(self.profile, "Uncertainty", 10)) // 2
        rp = C.strategy_rp(self.data, env, self.profile, mid_game=False)
        rp = C.small_map_add(self.data, rp, self.players, self.map_size)
        rp = C.remove_non_beginning(self.data, rp)
        self.strategy = C.choose_strategy(self.data, rp, self.rng) or self.data.strategy_names[0]
        self.last_strategy = self.strategy
        # 开局这一次 enemy_* 全是 0 -> 随便挑一类（races.eai:999）
        self.chosen_counter, self.no_counter, _t = C.determine_counter(
            self.data, self.strengths.enemy, self.strengths.ally, None, self.strat_persistence(), self.rng)
        self.heroes = C.pick_heroes(self.data, env, self.profile, self.strategy, self.rng, self.allow_neutral_heroes)
        self.skill_lines = [C.roll_skill_line(self.data, self.rng) for _ in range(3)]
        self.tier = env.tier()
        self.init_strategy(env)
        smt = int((self.data.strategy(self.strategy) or {}).get("strat_minimum_time (in seconds/6)") or 30)
        self.x = C.calc_strat_timer(smt, self.rng)
        self.counter_timer = int(min(C.calc_strat_timer(smt, self.rng) / 2, -60))
        self.set_counter = True
        self.started = True
        self.apply_profile(env)
        self.note(env.elapsed(), f"开局：人格 {self.profile.get('Profile Name')}、战略 {self.strategy}、"
                                 f"英雄 {'/'.join(h or '-' for h in self.heroes)}、加点线 {'/'.join(self.skill_lines)}、"
                                 f"反制 {D.CATEGORIES[self.chosen_counter]}（没信息随便挑的）")

    def strat_persistence(self) -> int:
        return int(C.profile_value(self.profile or {}, "strategy persistence", 1000))

    def init_strategy(self, env: WorldEnv) -> None:
        """init_strategy（races.eai:527）：清 Block、跑 global_init_strategy + init_strategy_<战略>、
        算开矿时间、算种族进攻性。"""
        self.queue.init_blocks(env.tier())
        self.seq.harass_groups = {}
        self.seq.harass_calls = []
        self.seq.run(self.data.global_init(), env)
        s = self.data.strategy(self.strategy) or {}
        self.exp_first_time = C.profile_value(self.profile or {}, "first expansion time", 0) + int(s.get("expansion time") or 0)
        self.exp_second_time = C.profile_value(self.profile or {}, "second expansion time", 0) + int(s.get("second expansion time") or 0)
        base = self.data.race_aggression()
        add = self.data.strategy_race_aggression(self.strategy)
        self.race_aggression = {r: tuple(base.get(r, (0, 0, 0))[i] + add.get(r, (0, 0, 0))[i] for i in range(3))
                                for r in D.RACES}
        self.seq.run(self.data.strategy_init(self.strategy), env)

    def apply_profile(self, env: WorldEnv) -> None:
        """把人格 / 种族进攻性写进 brain.profile，让 tactics 那一侧读得到（§6.1）。
        量级和 brain/amai.py 完全一致（都是 AMAI 的"强度点"）：
          base_aggression      = 全局 attacking_aggression(2) + 人格第 3 列(-15..5)   <- 原来写死 amai.BASE_AGGRESSION=2
          race_aggression[族]  = RaceAggression.txt 那一行 + 当前战略第 45~56 列       <- 原来写死人族那张表
          min_attack_strength  = 人格第 5 列（2~6）                                    <- 原来 profile 里的 4
        """
        p = self.b_profile
        if p is None:
            return
        p["base_aggression"] = AMAI.BASE_AGGRESSION + int(C.profile_value(self.profile or {}, "Aggression", 0))
        p["race_aggression"] = dict(self.race_aggression)
        if "min_attack_strength" not in getattr(self, "_config_overrides", ()):  # 控制台调过就不覆盖
            p["min_attack_strength"] = int(C.profile_value(self.profile or {}, "Minimum Attack Strength", 4))
        p["amai_profile"] = self.profile.get("Profile Name") if self.profile else None

    b_profile = None            # runtime 在建引擎时塞进来（brain.profile 的引用）

    # ── 每 3 游戏秒一次（runtime.job_build 调）──
    def step(self, brain, world) -> object | None:
        env = WorldEnv(brain, world, self)
        self._config_overrides = tuple(getattr(getattr(brain, "config", None), "overrides", {}) or ())
        if not self.started:
            if world.foe_race is None and world.t < 15.0:
                self.count("等对手种族")
                return None
            self.b_profile = brain.profile
            self.start(env)
        if self.strengths.due(world.t):
            self.update_strength(env)
            self.strengths.advance(world.t)
        self.loop_step(env)
        # build_sequence()：全局段 + 当前战略段（都已按 tier 展开）
        tier = env.tier()
        self.seq.run(self.data.global_build(tier), env)
        self.seq.run(self.data.strategy_build(self.strategy, tier), env)
        if self.set_counter:
            code = self.seq.select_counter_unit(self.chosen_counter or 0)
            self.set_counter = False
            if code:
                self.note(world.t, f"反制 {D.CATEGORIES[self.chosen_counter or 0]} -> 出 {code}")
        if world.t < self.next_build_at:
            return None
        self.next_build_at = world.t + self.build_period
        self.pre_round(env)
        res = self.queue.plan(env, extra=self.revive_entries(brain, world))
        self.pending_expansion_peons = 0
        self.queue.needed_peons = 0
        if self.queue.rounds % int(self.data.setting_int("build_array_reset_time", 100)) == 0:
            self.queue.clear("每 100 轮")
        self.last_round = res
        return res

    # ── build_sequence_all 的那一段（races.eai:843）──
    def loop_step(self, env: WorldEnv) -> None:
        smt = int((self.data.strategy(self.strategy) or {}).get("strat_minimum_time (in seconds/6)") or 30)
        if self.x > 12:
            self.x = 0
            self.strategy_changer(env)
            self.x = min(max(int(C.calc_strat_timer(smt, self.rng) / 2), -120), -60)
            self.counter_timer = 2
            self.reset_counter = True
        if self.last_strategy != self.strategy:
            self.last_strategy = self.strategy
            self.x = C.calc_strat_timer(smt, self.rng)
            self.counter_timer = int(min(self.x / 2, -80))
            self.reset_counter = True
            self.queue.clear("换战略")
            self.init_strategy(env)
            self.apply_profile(env)
        if (self.x > self.counter_timer and self.counter_timer < 0) or self.reset_counter:
            before = self.chosen_counter
            self.chosen_counter, self.no_counter, scores = C.determine_counter(
                self.data, self.strengths.enemy, self.strengths.ally, self.chosen_counter,
                self.strat_persistence(), self.rng)
            self.queue.clear("换反制")
            self.set_counter = True
            if self.reset_counter:
                self.reset_counter = False
            else:
                self.counter_timer = 2
            if before != self.chosen_counter:
                self.count(f"换反制类别·{D.CATEGORIES[self.chosen_counter]}")
        new_tier = env.tier()
        if self.tier != new_tier:
            self.tier = new_tier
            self.queue.clear("换本")
            self.note(env.elapsed(), f"升到第 {new_tier} 本，清空建造队列")
        self.x += int(SEQ_PERIOD)

    def strategy_changer(self, env: WorldEnv) -> bool:
        """StrategyChanger（races.eai:275）。人口 >= 85 或者所有敌人里最强的也不到 25 就不换。"""
        if env.food_used() >= 85:
            self.count("不换战略·人口 >= 85")
            return False
        if self.max_enemy_strength(env) < self.data.setting_int("ver_strat_change_enemy_strength_min", 25):
            self.count("不换战略·敌人还不够强")
            return False
        scores = C.strategy_change_scores(self.data, env, self.profile or {}, self.strategy, self.strat_persistence())
        pick = C.pick_changed_strategy(self.data, scores, self.rng)
        self.count("检查换战略")
        if pick and pick != self.strategy:
            top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
            why = "打分：" + "、".join(f"{k} {v}" for k, v in top)
            self.history.append((round(env.elapsed(), 1), self.strategy, pick, why))
            self.last_change_t = env.elapsed()
            self.last_change_why = why
            self.note(env.elapsed(), f"换战略 {self.strategy} -> {pick}（{why}）")
            self.strategy = pick
            self.count("换了战略")
            return True
        return False

    def max_enemy_strength(self, env: WorldEnv) -> float:
        best = 0.0
        w = env.w
        for owner in {u.owner for u in w.enemies}:
            units = [u for u in w.enemies if u.owner == owner]
            best = max(best, S.player_strength(units, w.levels))
        return best

    def update_strength(self, env: WorldEnv) -> None:
        w = env.w
        counts_by_player = {}
        enemies = set()
        for owner in {u.owner for u in w.enemies}:
            counts_by_player[owner] = S.unit_counts([u for u in w.enemies if u.owner == owner])
            enemies.add(owner)
        counts_by_player[w.me] = S.unit_counts(w.mine)

        def pred(cond, counts):
            return Evaluator(_PredEnv(counts)).truth(cond)

        self.strengths.update(counts_by_player, enemies, {w.me}, env.ai_time(), pred)
        self.count("更新敌方强度")

    # ── 建造循环开头那几个"建造器"（common.eai:12272 起）──
    def pre_round(self, env: WorldEnv) -> None:
        self.farm_builder(env)
        self.peon_builder(env)
        self.militia_expansion_followthrough(env)
        self.human_backpack_builder(env)
        self.human_support_builder(env)
        self.paired_expansion_tower(env)
        self.expansion_builder(env)

    def militia_expansion_followthrough(self, env: WorldEnv) -> None:
        """分矿守军已由民兵清掉时，先攒大厅资源并接住留在矿边的两名原民兵。"""
        if self.race != "human" or not env.b.militia_expand_builders or env.expand_count() >= 2:
            return
        expansion = self.data.racial("racial_expansion")
        self.queue.add("expand", 2, expansion, 520, env, note="MilitiaExpansionFollowthrough")

    def paired_expansion_tower(self, env: WorldEnv) -> None:
        """融合 INFI：人族每座分矿大厅一出现，就把第一座哨塔插到普通开矿/出兵之前。"""
        if self.race != "human":
            return
        main = env.w.main_town()
        if main is None:
            return
        for hall in env.w.my_halls:
            if hall.addr != main.hall.addr:
                self.queue.add("unit", 1, "hwtw", 410, env, town=hall.addr, note="InfiExpansionTowerPair")

    def human_backpack_builder(self, env: WorldEnv) -> None:
        """二本且藏宝室完工后研究背包，让非英雄地面部队也能携带团队恢复卷轴。"""
        if self.race != "human" or env.tier() < 2 or env.town_count("hvlt", only_done=True) < 1:
            return
        self.queue.add("upgrade", 1, "Rhpm", 395, env, note="HumanBackpackRecovery")

    def human_support_builder(self, env: WorldEnv) -> None:
        """AMAI 融合下限：人族进入二本中期后至少带两名牧师。

        原版战略表允许部分火枪/迫击炮路线把神秘圣地和牧师拖得很晚。1-090 在
        219 秒已有铁匠铺，却到 884 秒才下神秘圣地、959 秒才出第一名牧师；两场
        决定性团战都没有治疗和驱散。仍走 AMAI BuildQueue，让前置、资源预留、
        人口和生产队列使用同一套规则，不从执行层强塞订单。
        """
        if self.race != "human" or env.tier() < 2 or env.food_used() < 35:
            return
        self.queue.add("unit", 2, "hmpr", 70, env, note="HumanSupportFloor")

    def farm_builder(self, env: WorldEnv) -> None:
        d = self.data
        farm = d.racial("racial_farm")
        if not farm or env.food_cap() >= 100:
            return
        space = env.food_cap() - env.food_used()
        farm_food = int(C.profile_value(self.profile or {}, "Build Farm at left food", 6))
        if space <= d.setting_int("farm_food2", 0):
            self.build_farms(2, env)
        elif space <= farm_food:
            self.build_farms(1, env)

    def build_farms(self, n: int, env: WorldEnv) -> None:
        d = self.data
        farm = d.racial("racial_farm")
        target = env.unit_count(farm, only_done=True) + n
        prio = d.setting_int("farm_prio", 400)
        if env.town_count(farm, only_done=False) < d.setting_int("race_farms_at_mine", 0):
            self.queue.add("unit", target, farm, prio, env, loc="mine", note="FarmBuilder")
        elif d.setting("race_build_farms_front") and 1 <= env.unit_count(farm, only_done=True) <= d.setting_int("race_farms_front_limit", 4) + 1:
            self.queue.add("unit", target, farm, prio, env, loc="front", note="FarmBuilder")
        else:
            self.queue.add("unit", target, farm, prio, env, note="FarmBuilder")

    def peon_builder(self, env: WorldEnv) -> None:
        d = self.data
        peon = d.racial("racial_peon")
        target = max(self.peon_target(env.mines_harvested(), env.wood()), self.pending_expansion_peons)
        self.queue.add("unit", target, peon, d.setting_int("peon_prio", 280), env, note="PeonBuilder")
        if d.setting("race_has_ghouls"):
            ghoul = d.racial("racial_ghoul")
            n = max(int(C.lerp(d.setting_int("maximum_peon_wood", 200), d.setting_int("minimum_peon_wood", 2000),
                               d.setting_int("race_max_ghouls", 5), d.setting_int("race_min_ghouls", 2), env.wood())), 2)
            self.queue.add("unit", n, ghoul, d.setting_int("ghoul_prio", 250), env, note="BuildGhouls")

    def expansion_builder(self, env: WorldEnv) -> None:
        """ExpansionBuilder（common.eai:11998）：到点了、现有矿的工人满了就把开矿放进队列。
        没接的：水矿/飞艇、可重建的旧矿（prefer_rebuild）、民兵开矿、古树开矿、兽族物品开矿。
        矿里还剩多少金子读不到，所以"金快挖完了就紧急开矿"这条按"一座矿都没有"处理。"""
        d = self.data
        if not env.expansion_available():
            self.count("开矿·地图上没有能开的矿")
            return
        if not self.fast_expa_checked:
            self.fast_expa_checked = True
            self.check_fast_expansion(env)
        mines = env.mines_harvested()
        ai_time = env.ai_time()
        aggression = AMAI.BASE_AGGRESSION + int(C.profile_value(self.profile or {}, "Aggression", 0))
        ready = (mines < 2 and ai_time - self.exp_time_count > self.exp_first_time) \
            or (mines < 3 and ai_time - self.exp_time_count > self.exp_second_time) \
            or (mines < 4 and aggression > 0 and self.rng.randint(0, 100) < 42
                and env.gold() > 1000 and env.wood() > 700)
        guarded = env.expansion_strength() > 0
        upkeepboost = any(mines < row["mines"] and env.food_used() + 10 > row["border"]
                          for row in self.queue.upkeep.rows)
        if not (mines < 1 or ready):
            self.count("开矿·还没到时候")
            return
        if guarded:
            # 到点了但矿旁边有野怪：派兵去清（AMAI 的 take_exp），清完下一轮自然就开
            mine = env.expansion_mine()
            if mine is not None and env.mark_clear_expansion(mine):
                self.count("开矿·分矿旁边有野怪，先清")
            else:
                self.count("开矿·分矿旁边有野怪（认不出是哪几只）")
            return
        if not env.peons_saturated():
            self.count("开矿·现有矿的工人还没满")
            return
        expansion = d.racial("racial_expansion")
        prio = d.setting_int("expa_emergency_prio", 500) if mines < 1 else (
            d.setting_int("expa_rebuild_prio", 400) if upkeepboost else d.setting_int("expa_prio", 280))
        self.queue.add("expand", env.unit_count(expansion, only_done=True) + 1, expansion, prio, env, note="ExpansionBuilder")

    def check_fast_expansion(self, env: WorldEnv) -> None:
        """CheckFastExpansion（common.eai:7405）。⚠ A7：源码里那个 fastexpa 是局部变量、每轮都跑；
        这里按作者意图只跑一次（人族恒满足条件，等于 50% 概率跳过"第一次开矿时间"）。"""
        d = self.data
        if self.rng.randint(1, 2) != 1:
            return
        if d.setting("race_militia_expansion_available") or self.players > 2:
            self.exp_time_count = -self.exp_first_time
            self.count("开矿·跳过第一次开矿时间")

    def revive_entries(self, brain, world) -> list:
        """HeroReviver（common.eai:12211）：英雄造过但现在没有 -> 按 hero<N>_revive_prio 重新请求。
        我们的执行层用的是"复活"这条路（祭坛 + 死亡英雄表），所以只在这一轮插队，不进队列。"""
        out = []
        for d in getattr(brain, "dead_waiting", []) or []:
            code = d.get("code") or "hero"
            slot = 1
            for i, h in enumerate(self.heroes):
                if h == code:
                    slot = i + 1
                    break
            prio = self.data.setting_int(f"hero{min(slot, 3)}_revive_prio", 200)
            out.append(Entry("revive", code, 1, prio, note="HeroReviver"))
        return out

    # ── 给 runtime 用的东西 ──
    def skill_line(self, hero_code: str) -> str:
        i = self.heroes.index(hero_code) if hero_code in self.heroes else 0
        return self.skill_lines[i] if i < len(self.skill_lines) else "major"

    def hero_slot(self, hero_code: str) -> int:
        return (self.heroes.index(hero_code) + 1) if hero_code in self.heroes else 1

    def aggression(self, world) -> float:
        """§6.1 的叠加，和 brain/amai.py 的 aggression 同一个公式，只是基数和种族表换成本局实际的。"""
        return AMAI.aggression(world.food_used, len(world.my_heroes), world.foe_race, world.tier,
                               base=AMAI.BASE_AGGRESSION + int(C.profile_value(self.profile or {}, "Aggression", 0)),
                               race_table=self.race_aggression or None)

    def live(self) -> dict:
        """写进 brain_live.json 的 strategy 段（键名见 docs 报告）。"""
        counter = D.CATEGORIES[self.chosen_counter] if self.chosen_counter is not None else None
        return {
            "engine": "amai",
            "race": self.race,
            "profile": (self.profile or {}).get("Profile Name"),
            "strategy": self.strategy,
            "strategyTier": int((self.data.strategy(self.strategy) or {}).get("main_strategy_tier") or 0) if self.strategy else 0,
            "lastChangeT": round(self.last_change_t, 1),
            "lastChangeWhy": self.last_change_why,
            "changes": [{"t": t, "from": a, "to": b, "why": why} for t, a, b, why in self.history[-6:]],
            "counter": counter,
            "counterUnit": self.seq.chosen_counter_unit,
            "counterBlind": bool(self.no_counter),
            "heroes": list(self.heroes),
            "skillLines": list(self.skill_lines),
            "enemy": {k: round(v, 1) for k, v in self.strengths.enemy.items()},
            "ally": {k: round(v, 1) for k, v in self.strengths.ally.items()},
            "queue": [{"kind": e.kind, "code": e.code, "qty": e.qty, "prio": e.prio, "note": e.note}
                      for e in self.queue.entries[:40]],
            "queueLen": len(self.queue.entries),
            "rounds": self.queue.rounds,
            "nextCheckIn": round(12 - self.x, 1),
            "expTimes": [round(self.exp_first_time, 1), round(self.exp_second_time, 1)],
            "notes": list(self.notes)[-12:],
            "counts": dict(sorted(self.counts.items(), key=lambda kv: -kv[1])[:30]),
            "seqCounts": dict(sorted(self.seq.counts.items(), key=lambda kv: -kv[1])[:20]),
            "queueCounts": dict(sorted(self.queue.counts.items(), key=lambda kv: -kv[1])[:20]),
        }

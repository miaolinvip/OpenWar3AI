# -*- coding: utf-8 -*-
"""地形：哪里能走、哪里空旷。数据是 tools/terrain_map.py 离线生成的 war3-analytics/w3data/terrain_EchoIsles.json
（IsTerrainPathable 按 128 一格问全图 + 开局的树）。

★ 09-21 对局画面（左下角鱼人营地，我方挤在水和树林之间）：打野时部队钻进角落，没有微操空间，残血单位无路可撤。
  设计要求：不管打哪个营地，都要给自己周围留足可移动的空间；为此需要分析所在位置周围的地形。
  原来确实没有：打野集合点一律「营地朝家的方向退 1000」（tactics._camp_stage），不看那边是水、树林还是悬崖。
  那个营地（-6454,-3565）画出来：西边是水，旧集合点正好落在水和树林之间一条两三格宽的缝里；营地东边、东南是一大片空地。

stage_point：在营地周围 16 个方向、距离 dist 的点里挑 ——
  点本身能走（地形 + 树）、周围 OPEN_R 内空地比例高（有地方拉扯）、身后（背离营地再走 BACK_R）也能走（有退路），
  再按「朝家的方向」加一点分（回家近、援兵从那边来）。表读不到时返回 None，调用方照旧算。

★★ 09-22 对局画面（同一个海龟鱼人营地）：打野集结时部队经常被树隔成两半、站不到一起，需要彻底解决集结点问题。
  两处量错了：
  1 **树只记了一半**。树（LTlt）的通行占地是 128×128（DestructableData pathTex = PathTextures\\4x4Default），
    地图上的树按 64 错位摆，大多压在两个 128 格中间 —— 原来按四舍五入只记一格，营地东边那条树林带在表里成了
    「一行树一行空地」的筛子，集合点（-5530,-3182）就挑在树林带正中间（空地 69%）。命令点在树里，
    各人走到树林自己这一侧最近的地方停下 —— 一半在水边、一半在草地，中间隔着树。
    现在内部按 64 一格（地形表是 128，一格拆四格；树都在 64 的整数倍上，占地正好 2×2 格，不多不少）。
  2 **空地比例把树林对面的空地也算进来了**。现在只数「从这个点走得过去」的（圆里连通），一墙之隔的不算；
    集合点还要和营地同一侧（走到营地的路程不超过直线 STAGE_DETOUR 倍），不然开打时又要绕树林。
  snap：命令点落在树里/水里/贴着树缝时挪到最近一块能站人的地方（runtime 下令前统一过一遍，打野展开/包围的点按营地那一侧挪）。
"""
from __future__ import annotations

import heapq
import json
import math
import os

PATH = __import__("w3paths").game_data("terrain_EchoIsles.json")
OPEN_R = 500.0          # 集合点周围这么远以内的空地比例 = 有没有拉扯的空间
BACK_R = 600.0          # 背离营地再走这么远还能走 = 有退路
DIRS = 16
W_OPEN = 1.0
W_BACK = 0.5
W_HOME = 0.35           # 朝家的方向：cos(夹角) × 这个
# open_ground（09-22 恢复卷轴不在家里用：家里建筑密集没空间，改到周围空地集结，
#   英雄也站到空地上，需要恢复的单位都聚到拿卷轴的英雄身边再放）
CLEAR_BUILD_R = 420.0   # 离任何建筑中心至少这么远（城堡占地半径约 256，再留一条路）
CLEAR_MINE_R = 700.0    # 离金矿这么远：矿线上全是农民
CROWD_R = 800.0         # 这么远以内每多一座建筑，分数扣 CROWD_W
CROWD_W = 0.05
GROUND_RINGS = (0.0, 400.0, 700.0, 1000.0, 1300.0, 1600.0)
W_GROUND_OPEN = 2.0     # 空旷度的权重（满分 1 × 这个）—— 这件事就是为了「有地方站开」
W_NEAR = 1.0 / 3000.0   # 离出发点每远 3000 扣 1 分
W_BEHIND = 0.05         # 背离地图中心那一侧（基地后面）只当平手时的参考：Echo Isles 主基地背后是树林，
#                         0.15 时 1-286 选到了西边贴着树林的点（空旷度 0.69），东南边是整片空地（1.0）
# ★ 09-22 所有单位不再到基地附近集结，改在外面空地集结 —— 整队回家 / 集结 / 收队原来都走到城堡坐标。
#   open_ground(front=True) 挑门前（朝地图中心那一侧）的空地：出门就是它，也是敌人来的方向，不堵在建筑和矿线中间。
RALLY_RINGS = (700.0, 1000.0, 1300.0)   # 门前空地离城堡这么远（出击层按主力重心离城堡 1500 内算「到家」，最外圈要留余量）
W_FRONT = 0.5           # 朝地图中心那一侧：cos(夹角) × 这个（空旷度满分 2，正面和背面差 1 分 = 空旷度差五成）
TREE_HALF = 64.0        # 树的通行占地半边长（128×128，见文件头 ★★ 09-22）
SUB = 2                 # 地形表一格拆成 SUB×SUB 格（128 -> 64）
STAGE_RINGS = (1.0, 0.8, 1.2)   # 集合点候选离营地 dist × 这些（1000 那一圈全是树/水时往里往外各找一圈）
W_RING = 0.5            # 偏离 dist 每 1 倍扣这么多分（0.2 倍 = 0.1 分，比空旷度差一成还轻）
STAGE_DETOUR = 1.35     # 集合点走到营地的路程 <= 直线 × 这个；超了 = 中间隔着树林/水，开打时要绕过去
SNAP_MAX = 640.0        # snap 最多把命令点挪这么远


class Terrain:
    def __init__(self, doc: dict):
        step0 = float(doc["step"])
        self.step = step0 / SUB
        # 大格中心 x0 + i*step0、占 ±step0/2；小格中心从大格左边缘往里半个小格
        self.x0 = float(doc["x0"]) - step0 / 2 + self.step / 2
        self.y0 = float(doc["y0"]) - step0 / 2 + self.step / 2
        self.n = int(doc["n"]) * SUB
        self.free = [[c == "0" for c in row for _ in range(SUB)] for row in doc["blocked_rows"] for _ in range(SUB)]
        reach = TREE_HALF + self.step / 2 - 1e-6          # 小格和树的占地有重叠（只挨着边不算）
        for _t, tx, ty in doc.get("trees", ()):
            i0, i1 = math.ceil((tx - reach - self.x0) / self.step), math.floor((tx + reach - self.x0) / self.step)
            j0, j1 = math.ceil((ty - reach - self.y0) / self.step), math.floor((ty + reach - self.y0) / self.step)
            for j in range(max(0, j0), min(self.n - 1, j1) + 1):
                for i in range(max(0, i0), min(self.n - 1, i1) + 1):
                    self.free[j][i] = False

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return int(round((x - self.x0) / self.step)), int(round((y - self.y0) / self.step))

    def _xy(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + i * self.step, self.y0 + j * self.step

    def _ok(self, i: int, j: int) -> bool:
        return 0 <= i < self.n and 0 <= j < self.n and self.free[j][i]

    def walkable(self, x: float, y: float) -> bool:
        return self._ok(*self._cell(x, y))

    def clear(self, x: float, y: float) -> bool:
        """(x, y) 这一格和四周 8 格都能走 —— 一群人能在这里站下，不是贴着树缝的一条边。"""
        ci, cj = self._cell(x, y)
        return all(self._ok(ci + di, cj + dj) for dj in (-1, 0, 1) for di in (-1, 0, 1))

    def _disk(self, x: float, y: float, r: float) -> set:
        k = int(r // self.step)
        ci, cj = self._cell(x, y)
        return {(ci + di, cj + dj) for dj in range(-k, k + 1) for di in range(-k, k + 1) if di * di + dj * dj <= k * k}

    def _steps(self, i: int, j: int):
        """8 邻格；斜着走要两个直角边都能走（两棵斜对着的树中间钻不过去）。"""
        for di, dj, c in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                          (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)):
            ni, nj = i + di, j + dj
            if not self._ok(ni, nj):
                continue
            if di and dj and not (self._ok(i + di, j) and self._ok(i, j + dj)):
                continue
            yield ni, nj, c

    def _flood(self, start: tuple, allowed: set) -> set:
        """从 start 出发、只在 allowed 里走，走得到的格子。start 本身不能走 = 空集。"""
        if start not in allowed or not self._ok(*start):
            return set()
        seen, todo = {start}, [start]
        while todo:
            i, j = todo.pop()
            for ni, nj, _c in self._steps(i, j):
                if (ni, nj) in allowed and (ni, nj) not in seen:
                    seen.add((ni, nj))
                    todo.append((ni, nj))
        return seen

    def path_dist(self, sx: float, sy: float, limit: float) -> dict:
        """从 (sx, sy)（不能走就从最近的能走格）出发的路程表 {格: 路程}，只算到 limit（按 512 往上取整，好复用）。"""
        s = self._nearest_ok(sx, sy)
        if s is None:
            return {}
        limit = math.ceil(limit / 512.0) * 512.0
        cache = self.__dict__.setdefault("_pd_cache", {})
        key = (s, limit)
        if key in cache:
            return cache[key]
        if len(cache) >= 32:
            cache.pop(next(iter(cache)))
        cache[key] = got = self._path_dist(s, limit)
        return got

    def _path_dist(self, s: tuple, limit: float) -> dict:
        k = limit / self.step
        dist, heap = {s: 0.0}, [(0.0, s)]
        while heap:
            d, (i, j) = heapq.heappop(heap)
            if d > dist.get((i, j), 1e18):
                continue
            for ni, nj, c in self._steps(i, j):
                nd = d + c
                if nd <= k and nd < dist.get((ni, nj), 1e18):
                    dist[(ni, nj)] = nd
                    heapq.heappush(heap, (nd, (ni, nj)))
        return {c: d * self.step for c, d in dist.items()}

    def _nearest_ok(self, x: float, y: float, max_r: float = SNAP_MAX, need_clear: bool = False, among=None):
        ci, cj = self._cell(x, y)
        k = int(max_r // self.step)
        best = None
        for dj in range(-k, k + 1):
            for di in range(-k, k + 1):
                d2 = di * di + dj * dj
                if d2 > k * k or (best is not None and d2 >= best[0]):
                    continue
                c = (ci + di, cj + dj)
                if among is not None and c not in among:
                    continue
                if not self._ok(*c) or (need_clear and not self.clear(*self._xy(*c))):
                    continue
                best = (d2, c)
        return None if best is None else best[1]

    def snap(self, x: float, y: float, anchor: tuple | None = None, max_r: float = SNAP_MAX):
        """命令点 (x, y) 站不下人（树里、水里、贴着树缝）-> 最近一块站得下的地方 (x, y)；本来就站得下原样返回；找不到 None。
        anchor：只挑从 anchor 走得到、不用绕远的格子（树林带两边都是空地时，挑营地/目标那一侧）。"""
        if self.clear(x, y) and anchor is None:
            return x, y
        among = None
        if anchor is not None:
            reach = math.hypot(x - anchor[0], y - anchor[1]) + max_r
            among = self.path_dist(anchor[0], anchor[1], reach * STAGE_DETOUR)
            ci, cj = self._cell(x, y)
            if (ci, cj) in among and self.clear(x, y):
                return x, y
        c = self._nearest_ok(x, y, max_r, True, among) or self._nearest_ok(x, y, max_r, False, among)
        return None if c is None else self._xy(*c)

    def openness(self, x: float, y: float, r: float = OPEN_R, src: tuple | None = None) -> float:
        """(x, y) 周围 r 以内、**走得到**的能走格子比例。
        走得到 = 从 src（默认 (x, y) 自己）出发、只在「这个圆 + src 周围到这里这么远」里连通 —— 树林/水对面的空地不算。"""
        disk = self._disk(x, y, r)
        if not disk:
            return 0.0
        if src is None:
            got = self._flood(self._cell(x, y), disk)
        else:
            allowed = disk | self._disk(src[0], src[1], math.hypot(x - src[0], y - src[1]))
            got = self._flood(self._cell(*src), allowed)
        return len(got & disk) / len(disk)

    def open_ground(self, cx: float, cy: float, buildings=(), mines=(), avoid=(), avoid_r: float = 1200.0,
                    rings=GROUND_RINGS, center: tuple = (0.0, 0.0), front: bool = False):
        """(cx, cy) 附近一块能摆开一群人的空地 -> (x, y, 空旷度) 或 None。
        候选：rings × 16 个方向。硬条件：地形能走、离建筑 CLEAR_BUILD_R 外、离金矿 CLEAR_MINE_R 外、离 avoid（敌人/野怪）avoid_r 外。
        分数：地形空旷度（OPEN_R 内能走的比例）- 附近建筑数 × CROWD_W - 离出发点的距离 × W_NEAR + 背离地图中心 × W_BEHIND。
        front=True（整队集结，见 RALLY_RINGS）：换成**朝**地图中心 × W_FRONT。
        地形表只有地形和开局的树，建筑是调用方给的（buildings 里放双方建筑）。"""
        bx = [(b.x, b.y) for b in buildings]
        mx = [(m.x, m.y) for m in mines]
        ax = [(a.x, a.y) for a in avoid]
        ox, oy = cx - center[0], cy - center[1]
        on = math.hypot(ox, oy) or 1.0
        best = None
        for r in rings:
            for k in range(1 if r == 0 else DIRS):
                a = 2 * math.pi * k / DIRS
                x, y = cx + r * math.cos(a), cy + r * math.sin(a)
                if not self.clear(x, y):
                    continue
                if any((x - px) ** 2 + (y - py) ** 2 < CLEAR_BUILD_R ** 2 for px, py in bx):
                    continue
                if any((x - px) ** 2 + (y - py) ** 2 < CLEAR_MINE_R ** 2 for px, py in mx):
                    continue
                if any((x - px) ** 2 + (y - py) ** 2 < avoid_r ** 2 for px, py in ax):
                    continue
                crowd = sum(1 for px, py in bx if (x - px) ** 2 + (y - py) ** 2 < CROWD_R ** 2)
                opn = self.openness(x, y)
                behind = ((x - cx) * ox + (y - cy) * oy) / (on * r) if r > 0 else 0.0
                side = -behind * W_FRONT if front else behind * W_BEHIND
                score = opn * W_GROUND_OPEN - crowd * CROWD_W - r * W_NEAR + side
                if best is None or score > best[0]:
                    best = (score, x, y, opn)
        return None if best is None else (best[1], best[2], best[3])

    def stage_point(self, cx: float, cy: float, dist: float, home: tuple | None = None,
                    origin: tuple | None = None, wake_r: float = 0.0):
        """营地 (cx, cy) 周围 dist 远、最空旷且有退路的点 -> (x, y, 说明)；一个能走的都没有返回 None。
        origin + wake_r：从部队现在的位置走过去的直线离营地中心不到 wake_r 的候选不要（半路就把野怪惊醒了）；
        全被这条挡掉时退回不看路线；再挡光才放宽「站得下一群人、和营地同一侧」（strict）。"""
        for strict in (True, False):
            for check_route in ((True, False) if origin and wake_r > 0 else (False,)):
                p = self._stage_best(cx, cy, dist, home, origin if check_route else None, wake_r, strict)
                if p is not None:
                    return p
        return None

    def _stage_best(self, cx, cy, dist, home, origin, wake_r, strict=True):
        hx, hy = home if home else (cx, cy)
        hd = math.hypot(hx - cx, hy - cy)
        # 营地那一侧：从营地走过去的路程表（09-22：集合点和营地之间隔着树林，开打时又要绕）
        camp = self.path_dist(cx, cy, dist * max(STAGE_RINGS) * STAGE_DETOUR)
        best = None
        for ring in STAGE_RINGS:
            d0 = dist * ring
            for k in range(DIRS):
                a = 2 * math.pi * k / DIRS
                ux, uy = math.cos(a), math.sin(a)
                x, y = cx + ux * d0, cy + uy * d0
                if not (self.clear(x, y) if strict else self.walkable(x, y)):
                    continue
                pd = camp.get(self._cell(x, y))
                if strict and (pd is None or pd > d0 * STAGE_DETOUR):
                    continue
                pd = d0 * 9.99 if pd is None else pd
                if origin is not None and _seg_dist(cx, cy, origin[0], origin[1], x, y) < wake_r:
                    continue
                opn = self.openness(x, y)
                back = self.openness(x + ux * BACK_R, y + uy * BACK_R, OPEN_R * 0.6, src=(x, y))
                toward = ((hx - cx) * ux + (hy - cy) * uy) / hd if hd >= 1 else 0.0
                score = W_OPEN * opn + W_BACK * back + W_HOME * toward - W_RING * abs(ring - 1.0)
                if best is None or score > best[0]:
                    best = (score, x, y, opn, back, toward, pd / d0)
        if best is None:
            return None
        _s, x, y, opn, back, toward, det = best
        return x, y, f"空地 {opn:.0%}、退路 {back:.0%}、朝家 {toward:+.2f}、到营地绕 {det:.2f} 倍"


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    """点到线段的距离 —— 和 tactics.segment_distance 同一个算式（这里不能 import tactics：tactics 要 import 本模块）。"""
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den <= 1e-6 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


_CACHE: dict = {}


def doc_from_map(mp, trees) -> dict:
    """W3P 地图块（w3world.MapInfo：cell 一格、bit0 = 不能走）+ 树块 -> 和离线 terrain_*.json 同一种格式。
    离线表是正方形（n×n），地图块是 cols×rows —— 补成正方形，多出来的格子算不能走（在地图外）。
    采样点一样：格子中心（离线表 x0 + i*step 也是格子中心），运行时同样问 IsTerrainPathable 行走。"""
    n = max(mp.cols, mp.rows)
    rows = []
    for r in range(n):
        if r < mp.rows:
            cells = mp.cells[r * mp.cols:(r + 1) * mp.cols]
            rows.append("".join("1" if c & 1 else "0" for c in cells) + "1" * (n - mp.cols))
        else:
            rows.append("1" * n)
    return {"map": "live", "x0": mp.min_x + mp.cell / 2.0, "y0": mp.min_y + mp.cell / 2.0, "step": float(mp.cell),
            "n": n, "blocked_rows": rows,
            "trees": [[t.type, round(t.x), round(t.y)] for t in trees if getattr(t, "alive", t.life > 0)]}


def from_world(world) -> Terrain | None:
    """这一局运行时算好的地形（W3P 地图块 + 开局的树），不限地图；地图块还没算好（开局几秒）返回 None。按 epoch 缓存。
    09-23 P3：原来只有 Echo Isles 一张离线表（tools/terrain_map.py 在开放层里自己调 IsTerrainPathable 扫出来的）。"""
    try:
        mp = world.map()
    except Exception:  # noqa: BLE001
        return None
    if mp is None:
        return None
    key = ("live", getattr(world, "pid", 0), mp.epoch)
    if key not in _CACHE:
        try:
            trees = world.trees()
        except Exception:  # noqa: BLE001
            trees = []
        _CACHE[key] = Terrain(doc_from_map(mp, trees))
    return _CACHE[key]


def load(path: str = PATH) -> Terrain | None:
    if path in _CACHE:
        return _CACHE[path]
    t = None
    try:
        with open(path, encoding="utf-8") as fh:
            t = Terrain(json.load(fh))
    except Exception:  # noqa: BLE001 - 没有表就按老办法算，不影响打游戏
        t = None
    _CACHE[path] = t
    return t

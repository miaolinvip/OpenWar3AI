# -*- coding: utf-8 -*-
"""地面寻路（SDK 侧，纯计算）：在运行时推送的地图块（128 一格、地形能不能走）上叠加树和建筑，用 A* 算
"走不走得到"和"要走多远"。

    grid = pathing.Grid.from_game(g)             # 或 Game 的 g.path_distance / g.reachable / g.walk_path
    grid.distance((x1, y1), (x2, y2))           # 走路距离（地图单位）；走不到 None
    grid.path(a, b)                              # 路径点（格子中心，已拉直）

精度：一格 128（一个人族农场宽 128）。窄于一格的缝隙（两棵树之间刚好过一个单位）会判成走不通 —— 用来做决策
（远近、通不通、绕多远），别当成单位真实走的线。飞行单位不需要它。
"""
from __future__ import annotations

import heapq
import math

SQRT2 = math.sqrt(2.0)
ALL_TREES = 64 * 1024        # 树块最多 4096 棵，给足
MAX_NODES = 200 * 1000       # A* 展开上限（最大 256x256 格 = 65536，给足）


class Grid:
    def __init__(self, cols: int, rows: int, cell: float, min_x: float, min_y: float, blocked: bytearray):
        self.cols, self.rows, self.cell, self.min_x, self.min_y = cols, rows, cell, min_x, min_y
        self.blocked = blocked                     # 1 = 走不了

    # ── 构造 ─────────────────────────────────────────────
    @classmethod
    def from_map(cls, mp, trees=(), buildings=(), building_radius=None) -> "Grid":
        """mp = MapInfo（g.map()）。trees：有 x,y 的树（活的才挡路）；buildings：有 x,y,type 的建筑（按占地半径挡）。"""
        blocked = bytearray(1 if (c & 1) else 0 for c in mp.cells)
        g = cls(mp.cols, mp.rows, float(mp.cell), mp.min_x, mp.min_y, blocked)
        for t in trees:
            if getattr(t, "life", 1) > 0:
                g._block_disc(t.x, t.y, 0.0)       # 只挡树所在的那一格（树按 64/128 对齐，一棵树约一格）
        for b in buildings:
            r = building_radius(b) if building_radius else 96.0
            if r:
                g._block_disc(b.x, b.y, r)
        return g

    @classmethod
    def from_game(cls, game, with_buildings: bool = True) -> "Grid | None":
        mp = game.map()
        if mp is None:
            return None
        blds = []
        if with_buildings:
            blds = [u for u in game.units("all") if game.is_building(u) and u.type not in ("ngol", "egol", "ugol")]
        return cls.from_map(mp, game.trees(limit=ALL_TREES), blds, building_radius=_footprint)

    def copy(self) -> "Grid":
        """复制一份（改它不影响 g.grid() 的缓存）。"""
        return Grid(self.cols, self.rows, self.cell, self.min_x, self.min_y, bytearray(self.blocked))

    def block_area(self, x: float, y: float, half_width: float) -> None:
        """把 (x,y) 周围半宽 half_width 的方块标成不能走（绕开塔、绕开敌方主力：先 copy() 再标）。"""
        self._block_disc(x, y, half_width)

    def _block_disc(self, x: float, y: float, r: float) -> None:
        """挡住以 (x,y) 为中心、半宽 r 的方块压到的格（格子中心离中心 < r）；r <= 0 只挡 (x,y) 所在那一格。"""
        if r <= 0:
            c, rr = self.cell_of(x, y)
            if self.inside(c, rr):
                self.blocked[rr * self.cols + c] = 1
            return
        c0, r0 = self.cell_of(x - r, y - r)
        c1, r1 = self.cell_of(x + r, y + r)
        for rr in range(max(r0, 0), min(r1, self.rows - 1) + 1):
            for cc in range(max(c0, 0), min(c1, self.cols - 1) + 1):
                cx, cy = self.center(cc, rr)
                if abs(cx - x) < r and abs(cy - y) < r:
                    self.blocked[rr * self.cols + cc] = 1

    # ── 坐标 ─────────────────────────────────────────────
    def cell_of(self, x: float, y: float) -> tuple:
        return int((x - self.min_x) // self.cell), int((y - self.min_y) // self.cell)

    def center(self, c: int, r: int) -> tuple:
        return self.min_x + (c + 0.5) * self.cell, self.min_y + (r + 0.5) * self.cell

    def inside(self, c: int, r: int) -> bool:
        return 0 <= c < self.cols and 0 <= r < self.rows

    def walkable_cell(self, c: int, r: int) -> bool:
        return self.inside(c, r) and not self.blocked[r * self.cols + c]

    def nearest_open(self, x: float, y: float, max_ring: int = 4):
        """(x,y) 所在格走不了时（站在建筑/树边上），找最近的一个能走的格。"""
        c, r = self.cell_of(x, y)
        if self.walkable_cell(c, r):
            return c, r
        for ring in range(1, max_ring + 1):
            best = None
            for dr in range(-ring, ring + 1):
                for dc in (-ring, ring) if abs(dr) != ring else range(-ring, ring + 1):
                    if self.walkable_cell(c + dc, r + dr):
                        d = dc * dc + dr * dr
                        if best is None or d < best[0]:
                            best = (d, c + dc, r + dr)
            if best:
                return best[1], best[2]
        return None

    # ── A* ───────────────────────────────────────────────
    def _astar(self, a, b, max_nodes: int = MAX_NODES):
        s, t = self.nearest_open(*a), self.nearest_open(*b)
        if s is None or t is None:
            return None, None
        cols = self.cols
        blocked = self.blocked
        start, goal = s[1] * cols + s[0], t[1] * cols + t[0]
        tc, tr = t

        def h(i):
            dc, dr = abs(i % cols - tc), abs(i // cols - tr)
            return (dc + dr) + (SQRT2 - 2) * min(dc, dr)

        g = {start: 0.0}
        came = {}
        heap = [(h(start), start)]
        seen = 0
        while heap:
            _f, cur = heapq.heappop(heap)
            if cur == goal:
                break
            seen += 1
            if seen > max_nodes:
                return None, None
            cc, cr = cur % cols, cur // cols
            gc = g[cur]
            for dc, dr, cost in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                                 (1, 1, SQRT2), (1, -1, SQRT2), (-1, 1, SQRT2), (-1, -1, SQRT2)):
                nc, nr = cc + dc, cr + dr
                if not (0 <= nc < cols and 0 <= nr < self.rows):
                    continue
                n = nr * cols + nc
                if blocked[n]:
                    continue
                if dc and dr and (blocked[cr * cols + nc] or blocked[nr * cols + cc]):
                    continue                       # 不许斜着穿过两个障碍的夹角
                ng = gc + cost
                if ng < g.get(n, 1e18):
                    g[n] = ng
                    came[n] = cur
                    heapq.heappush(heap, (ng + h(n), n))
        if goal not in g:
            return None, None
        cells = [goal]
        while cells[-1] != start:
            cells.append(came[cells[-1]])
        cells.reverse()
        return cells, g[goal] * self.cell

    def reachable(self, a, b) -> bool:
        """地面单位能不能从 a 走到 b。"""
        return self._astar(a, b)[0] is not None

    def distance(self, a, b) -> float | None:
        """走路距离（地图单位，按格子中心算；两端到格子中心的零头不计）。走不到 None。"""
        cells, d = self._astar(a, b)
        return d if cells is not None else None

    def path(self, a, b) -> list | None:
        """路径点 [(x,y)...]（去掉共线的中间格，只留拐点；最后一点换成 b 本身）。走不到 None。"""
        cells, _d = self._astar(a, b)
        if cells is None:
            return None
        cols = self.cols
        pts, last_dir = [], None
        for i in range(1, len(cells)):
            d = (cells[i] % cols - cells[i - 1] % cols, cells[i] // cols - cells[i - 1] // cols)
            if d != last_dir and i > 1:
                pts.append(self.center(cells[i - 1] % cols, cells[i - 1] // cols))
            last_dir = d
        pts.append((float(b[0]), float(b[1])))
        return pts


def _footprint(u) -> float:
    """建筑挡路的半宽：单位表的占地宽度 foot 的一半（城镇大厅 512 -> 256，农场 128 -> 64；缺了按 96）。"""
    try:
        from .game import unit_table
        f = (unit_table().get(u.type) or {}).get("foot") or 0
        return float(f) * 0.5 if f else 96.0
    except Exception:  # noqa: BLE001
        return 96.0

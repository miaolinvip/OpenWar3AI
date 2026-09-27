# -*- coding: utf-8 -*-
"""SLK 表（暴雪的数据表格式）-> 行字典列表。

⚠ 和 brains/xwar3/strategy/brain/gamedata.py 的 read_slk 是同一个读法（两份）：P3 参考大脑迁移时它改用这一份，
  那之前改一处要改两处。规则只有三条：C 行是单元格、X/Y 是列行号、**Y 省略时沿用上一行号**。
"""
from __future__ import annotations


def read_slk(path: str) -> list[dict]:
    cells, maxx, maxy, y = {}, 0, 0, 1
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            if not line.startswith("C;"):
                continue
            x = val = None
            for part in line.rstrip("\r\n").split(";")[1:]:
                if not part:
                    continue
                k, rest = part[0], part[1:]
                if k == "X":
                    x = int(rest)
                elif k == "Y":
                    y = int(rest)
                elif k == "K":
                    val = rest
            if x is None or val is None:
                continue
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            cells[(x, y)] = val
            maxx, maxy = max(maxx, x), max(maxy, y)
    headers = {x: cells[(x, 1)] for x in range(1, maxx + 1) if (x, 1) in cells}
    rows = []
    for yy in range(2, maxy + 1):
        row = {h: cells[(x, yy)] for x, h in headers.items() if (x, yy) in cells}
        if row:
            rows.append(row)
    return rows

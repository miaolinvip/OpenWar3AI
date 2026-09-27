# -*- coding: utf-8 -*-
"""从游戏自己的 AbilityData.slk 生成 buff 码对照表 war3-analytics/w3data/buffs.json。

为什么要它：快照读不到"被眩晕/被减速"，但**单位的技能链表里会挂上 B 开头的 buff 码**
（09-16 只读采样：敌方山丘身上 Bslo、Bprg）。码本身不说明是什么，而游戏的数据表里说了 ——
每个技能行的 BuffID1..4 就是它挂出来的 buff 码，同一行的 comments 是效果名，
Dur1 / HeroDur1 是持续时间，targs1 是作用对象。

于是"哪个码 = 哪种控制"不用猜：查表就行。这个脚本只读 SLK、只写 JSON，不碰游戏。

    python make_buffs.py          # 生成 w3data/buffs.json 并自检

输出形如：
    {"Bslo": [{"ability": "Aslo", "code": "Aslo", "effect": "Slow",
               "dur": "60", "heroDur": "10", "targets": "air,ground,enemy", "race": "human"}], ...}
一个码可能对应多行（普通版 / 野怪版 / 物品版），全部保留 —— 效果名相同，时长不同。
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SLK = HERE.parent / "game" / "AbilityData.slk"
OUT = HERE.parent / "game" / "buffs.json"
BUFF_COLS = ("BuffID1", "BuffID2", "BuffID3", "BuffID4")
KEEP = {"alias": "ability", "code": "code", "comments": "effect",
        "Dur1": "dur", "HeroDur1": "heroDur", "targs1": "targets", "race": "race"}


def read_slk(path: pathlib.Path) -> tuple[dict, dict]:
    """SLK 是列式的：C;X列;Y行;K值，而且行列号会沿用上一条。返回 (单元格, 表头)。"""
    cells: dict[tuple[int, int], str] = {}
    row = col = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("C;"):
            continue
        my = re.search(r";Y(\d+)", line)
        mx = re.search(r";X(\d+)", line)
        mk = re.search(r";K(.*)$", line)
        if my:
            row = int(my.group(1))
        if mx:
            col = int(mx.group(1))
        if mk and row and col:
            value = mk.group(1).strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            cells[(row, col)] = value
    header = {c: v for (r, c), v in cells.items() if r == 1}
    return cells, header


def build(cells: dict, header: dict) -> dict:
    by_col = {name: c for c, name in header.items()}
    rows: dict[int, dict] = {}
    for (r, c), v in cells.items():
        if r == 1 or v in ("", "-", "_"):
            continue
        rows.setdefault(r, {})[header.get(c, f"col{c}")] = v
    out: dict[str, list] = {}
    for r, row in rows.items():
        for col in BUFF_COLS:
            code = row.get(col)
            if not code or not code[0] in "Bb":
                continue
            entry = {dst: row.get(src, "") for src, dst in KEEP.items()}
            if entry not in out.setdefault(code, []):
                out[code].append(entry)
    return {k: out[k] for k in sorted(out)}


def main() -> int:
    if not SLK.exists():
        print("没有", SLK, "—— 先跑 dump_tables.py 从 MPQ 取表")
        return 1
    cells, header = read_slk(SLK)
    table = build(cells, header)
    # 自检：拿 09-16 实测到的三个码对一遍（期望值来自表里的 comments，不是我编的）
    checks = {"Bslo": "Slow", "Bprg": "Purge", "BHad": "Paladin - Devotion Aura"}
    bad = []
    for code, want in checks.items():
        got = [e["effect"] for e in table.get(code, [])]
        if want not in got:
            bad.append(f"{code}: 期望里有 {want!r}，实际 {got}")
    if bad:
        print("自检不过：", *bad, sep="\n  ")
        return 1
    OUT.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 {OUT}：{len(table)} 个 buff 码")
    for code in ("Bslo", "Bprg", "BHad"):
        first = table[code][0]
        print(f"   {code} <- {first['ability']} {first['effect']}（{first['targets']}，"
              f"持续 {first['dur']}，英雄 {first['heroDur']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""订单号表：名字 <-> 0x000D00xx。数据来自 data/order-ids.txt（281 条）。

    ORDER("blizzard") -> 0x000D0079
    ORDER_NAME(0x000D0012) -> "move"
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import w3paths

# 实测过的几条（见 act.py），不依赖表文件也能用
SMART = 0x000D0003          # 右键：对敌人=攻击，对自己人=跟随，对矿=采集，对建筑=集结点
STOP = 0x000D0004           # ⚠ 不是移动（09-16 更正）
GETITEM = 0x000D000D        # ⚠ 实测不动（5%），捡东西用 SMART
ATTACK = 0x000D000F         # A 键：攻击 / 攻击移动
MOVE = 0x000D0012           # 纯移动（撤退要用它走 W3P point；老的动作 41 顶不掉攻击令）
HOLD = 0x000D0019
BUILD_STARTED = (0x000D001B, 0x000D001C)
USESLOT1 = 0x000D0028       # 用第 1 格物品；第 n 格 = USESLOT1 + n - 1
HARVEST = 0x000D0032
RETURN_RESOURCES = 0x000D0034
REPAIR = 0x000D0038
REVIVE = 0x000D0047
MILITIA_ON, MILITIA_OFF = 0x000D0068, 0x000D0069
TOWNBELL_ON, TOWNBELL_OFF = 0x000D0072, 0x000D0073
ENTANGLE = 0x000D00B3


@lru_cache(maxsize=1)
def table() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open(w3paths.ORDER_IDS, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                m = re.match(r"^(\w+)\s+0x([0-9A-Fa-f]{8})", ln)
                if m:
                    out.setdefault(m.group(1).lower(), int(m.group(2), 16))
    except OSError:
        pass
    return out


# 大家习惯的叫法 -> 引擎订单名（引擎把风暴之锤叫 thunderbolt、圣光术叫 holybolt；
# 参考大脑的毫秒层和远见网页用的也是左边这套，console/server/stream.py SPELL_ALIAS 同一张表）
ALIASES = {"stormbolt": "thunderbolt", "holylight": "holybolt"}


def ORDER(name_or_id) -> int:
    """'blizzard' / 'thunderbolt'（别名 'stormbolt' 也认）/ 0x000D0079 -> 订单号。四字码（'hfoo'）不是订单名，用 rawcode()。"""
    if isinstance(name_or_id, int):
        return name_or_id
    key = str(name_or_id).lower()
    v = table().get(ALIASES.get(key, key))
    if v is None:
        raise KeyError(f"订单表里没有 {name_or_id!r}（看 data/order-ids.txt）")
    return v


def ORDER_NAME(order_id: int) -> str | None:
    for k, v in table().items():
        if v == order_id:
            return k
    return None


def rawcode(code: str) -> int:
    """'hfoo' -> 0x68666F6F（单位/建筑/科技/技能/物品的四字码）。"""
    b = code.encode("latin-1")
    if len(b) != 4:
        raise ValueError(f"四字码要 4 个字符：{code!r}")
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def code_of(raw: int) -> str:
    return bytes(((raw >> 24) & 255, (raw >> 16) & 255, (raw >> 8) & 255, raw & 255)).decode("latin-1", "replace")

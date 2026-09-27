"""
固定词表与单位基础数值（schema v1）。

type_vocab.json 是从 units.json（1.27 MPQ 解析）**冻结**下来的：<pad>=0、<unk>=1，其余 837 个四字符类型码按字母序。
⚠ 原来 env.TYPE_VOCAB 是「运行时见到一个编一个」—— 不同进程 / 不同文件里同一兵种编号不同，混在一起训练会错乱。
   词表只追加、不重排；要改就升 schema 版本。
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA = json.loads((Path(__file__).with_name("type_vocab.json")).read_text(encoding="utf-8"))
SCHEMA_VERSION = 2
# schema v2（09-17）= v1 + 动作的技能 a_ability [T, N] int16（ABILITY_ORDERS 下标，0 = 不是施法 / 不知道）。
#   只加字段：v1 文件和对局导出没有 a_ability，读的时候当全 0。
# 技能订单名表：冻结、只追加（和 order-ids.txt 同名；War3Bridge.issue 按名字查订单号）。
ABILITY_ORDERS: tuple[str, ...] = (
    "<none>",
    "blizzard", "waterelemental", "massteleport",           # Hamg
    "thunderbolt", "thunderclap", "avatar",                 # Hmkg
    "holybolt", "divineshield", "resurrection",             # Hpal
    "flamestrike", "banish", "siphonmana", "summonphoenix",  # Hblm
    "heal", "innerfire", "dispel",                          # hmpr
    "slow", "invisibility", "polymorph",                    # hsor
)
ABILITY_INDEX: dict[str, int] = {a: i for i, a in enumerate(ABILITY_ORDERS)}
TYPE_CODES: list[str] = _DATA["codes"]
TYPE_VOCAB: dict[str, int] = {c: i for i, c in enumerate(TYPE_CODES)}
UNIT_STATS: dict[str, dict] = _DATA["stats"]
PAD, UNK = 0, 1


def type_index(type_id: str) -> int:
    return TYPE_VOCAB.get(type_id, UNK)


def stat(type_id: str, key: str, default: float = 0.0) -> float:
    v = UNIT_STATS.get(type_id, {}).get(key)
    return float(v) if v not in (None, "") else float(default)

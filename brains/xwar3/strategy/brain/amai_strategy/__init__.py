# -*- coding: utf-8 -*-
"""AMAI 战略层（移植自 SMUnlimited/AMAI ea95cfd 的 TFT 脚本，规格见 docs/amai_strategy_spec.md）。

    brain/config.py 的 strategy_engine = "table"（默认，手写表，行为和以前完全一样）/ "amai"（这一层）

    data.py      读 brain/amai_data/*.json：单位表、战略表、英雄表、设置、种族进攻性
    expr.py      BuildSequence.ai 里条件/数量/优先级表达式的求值（JASS 整数除法那一套）
    strength.py  enemy_<类> / ally_<类>、某个玩家有多强
    choose.py    选人格 / 开局战略 / 换战略 / 反制类别与兵种 / 英雄与加点线
    queue.py     AMAI 的建造请求队列 + 建造循环（数量拆条、老化、升本闸、维护费闸、钱不够就停）
    seq.py       建造序列解释器（把 call 变成队列里的请求）
    engine.py    把上面这些按 AMAI 的时序串起来（AmaiStrategy）
"""
from .engine import AmaiStrategy  # noqa: F401

__all__ = ["AmaiStrategy"]

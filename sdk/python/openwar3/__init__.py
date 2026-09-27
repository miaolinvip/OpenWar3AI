# -*- coding: utf-8 -*-
"""OpenWar3 SDK —— 用 Python 写魔兽争霸 III（1.27）的 AI。

    from openwar3 import Game, Bot, run, ORDER, rawcode

入门看 sdk/README_ZH.md；全部接口（带实测状态和底层机制）看 docs/API_ZH.md，
或者 `python -m openwar3.catalog --json`（给大模型读的机器格式）。
"""
import os
import sys

# 让 `import openwar3` 在任何地方都能用：把 sdk/python 所在的路径中心接上
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _here not in sys.path:
    sys.path.insert(0, _here)
import w3paths  # noqa: E402

w3paths.setup()

from .bot import Bot, run  # noqa: E402
from .game import Game, unit_table  # noqa: E402
from .mod import Mod  # noqa: E402
from .orders import ORDER, ORDER_NAME, code_of, rawcode  # noqa: E402

__all__ = ["Game", "Bot", "Mod", "run", "ORDER", "ORDER_NAME", "rawcode", "code_of", "unit_table"]
__version__ = "0.1.0"

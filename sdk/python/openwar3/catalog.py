# -*- coding: utf-8 -*-
"""接口目录：Game 的每个公开方法都用 @api 登记（分类、实测状态、底层机制）。

    python -m openwar3.catalog            # 打印 Markdown（docs/API_ZH.md 就是它生成的）
    python -m openwar3.catalog --json     # 机器可读（给大模型写 bot 前读）

为什么要登记而不是只写文档：文档和代码分两处写，改一处忘一处（这个项目栽过很多次「同一条规则写在两处」）。
这里的目录就是代码本身的元数据，生成出来的文档不会过期。
"""
from __future__ import annotations

import inspect
import json
import sys

REGISTRY: list[dict] = []

CATEGORIES = {
    "observe": "观察：读状态（不改变游戏）",
    "command": "命令：让单位做事",
    "control": "游戏控制：速度、暂停、界面、气泡",
    "sandbox": "沙盒：JASS 通道（造单位、设盟友、给物品…… RPG 辅助 / 玩伴用；只在单人局、本机工具里能改世界）",
    "meta": "连接与工具",
}
STATUS = {
    "verified": "实机验证过",
    "inferred": "推断/未完整实测",
    "experimental": "新接口，待实机验证",
}


def api(category: str, status: str, mechanism: str, latency: str = ""):
    """登记一个接口。mechanism 写底层走的是什么（W3P 命令 / DLL 动作号 / 共享内存），出问题时照着查。"""
    assert category in CATEGORIES and status in STATUS

    def deco(fn):
        REGISTRY.append({"name": fn.__name__, "category": category, "status": status,
                         "mechanism": mechanism, "latency": latency, "fn": fn})
        return fn
    return deco


def entries() -> list[dict]:
    import openwar3.game  # noqa: F401 - 触发登记
    out = []
    for e in REGISTRY:
        fn = e["fn"]
        sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
        doc = inspect.getdoc(fn) or ""
        out.append({k: v for k, v in e.items() if k != "fn"} | {"signature": f"{e['name']}{sig}", "doc": doc})
    return out


def markdown() -> str:
    lines = ["# OpenWar3 Python 接口目录", "",
             "由 `python -m openwar3.catalog` 从代码生成，别手改。每个接口都标了**实测状态**和**底层机制**。", "",
             "⚠ 「实机验证过」指**底层那条路**（运行时动作号 / 语义命令 / 参数形状）在真实对局里验证过（实测记录见参考大脑 act.py 等处的注释）；"
             "SDK 门面和快车道是 OpenWar3 新写的：09-23 在演练实例上跑过快车道实机对拍（30/30 一致）和两个示例 bot（造农民、续建、伐木、兵营、英雄学技能），"
             "但还没覆盖每一个接口（施法、复活、物品、商店只验证过底层路）。", "",
             "延迟说明：`快照` = 读共享内存，不等游戏（约 0.05 ms）；`快车道` = 约 1 帧（游戏线程成批执行）；"
             "`控制通道` = 20~40 ms（旧路，快车道接不上时自动退回）。", ""]
    by = {}
    for e in entries():
        by.setdefault(e["category"], []).append(e)
    for cat, title in CATEGORIES.items():
        if cat not in by:
            continue
        lines += [f"## {title}", "", "| 接口 | 状态 | 延迟 | 底层 | 说明 |", "|---|---|---|---|---|"]
        for e in by[cat]:
            first = (e["doc"].splitlines() or [""])[0].replace("|", "\\|")
            lines.append(f"| `{e['signature']}` | {STATUS[e['status']]} | {e['latency']} | {e['mechanism']} | {first} |")
        lines.append("")
    return "\n".join(lines)


def write_docs() -> list[str]:
    """重新生成 docs/API_ZH.md 和 docs/api.json（改了 Game 的接口之后跑一次）。"""
    import w3paths
    import os
    d = os.path.join(w3paths.ROOT, "docs")
    os.makedirs(d, exist_ok=True)
    md, js = os.path.join(d, "API_ZH.md"), os.path.join(d, "api.json")
    with open(md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(markdown() + "\n")
    with open(js, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(entries(), fh, ensure_ascii=False, indent=1)
    return [md, js]


if __name__ == "__main__":
    # ⚠ python -m openwar3.catalog 会把本文件当 __main__ 再执行一遍：game.py 登记进的是包里那份 REGISTRY，这份是空的 ——
    #   09-26 --write 就这样写出了空目录。一律转交给包里那份（等价于 python -m openwar3 catalog）。
    from openwar3 import catalog as _pkg
    if "--write" in sys.argv:
        print("\n".join(_pkg.write_docs()))
    elif "--json" in sys.argv:
        print(json.dumps(_pkg.entries(), ensure_ascii=False, indent=1))
    else:
        print(_pkg.markdown())

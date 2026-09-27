# -*- coding: utf-8 -*-
"""命令行：

    python -m openwar3 run brains/examples/hello_bot.py --inst 5 [--hz 5] [--minutes 30]
    python -m openwar3 status --inst 5          # 连一下，打印快车道/快照状态
    python -m openwar3 catalog [--json]         # 接口目录
    python -m openwar3 jass --inst 20           # JASS 控制台：一行一句，直接粘 JASS（call / set / local）
    python -m openwar3 jass --inst 20 -e 'call PingMinimapEx(hero().x, hero().y, 3, 255, 0, 0, false)'
    python -m openwar3 jass --inst 20 my_script.j
    python -m openwar3 jass --list TextTag      # 查 native：名字 / 参数 / 中文说明 / 分类 / 实机调过没有（--cat visual 按分类，--lang en 英文说明）
    python -m openwar3 jass --list 漂浮文字      # 也能按说明搜
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_bot(path: str):
    path = os.path.abspath(path)
    sys.path.insert(0, os.path.dirname(path))        # bot 可以 import 同目录的别的文件（rush_bot 继承 hello_bot）
    spec = importlib.util.spec_from_file_location("user_bot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from openwar3 import Bot
    bots = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, Bot) and v is not Bot]
    own = [b for b in bots if b.__module__ == "user_bot"]   # 优先这个文件里自己定义的，而不是 import 进来的父类
    bots = own or bots
    if not bots:
        raise SystemExit(f"{path} 里没有 Bot 的子类")
    return bots[-1]


def main() -> int:
    ap = argparse.ArgumentParser(prog="openwar3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("bot")
    r.add_argument("--inst", type=int)
    r.add_argument("--pid", type=int)
    r.add_argument("--hz", type=float, default=5.0)
    r.add_argument("--minutes", type=float, default=60.0)
    r.add_argument("--no-fast", action="store_true")
    r.add_argument("--fair", action="store_true", help="公平模式：只看得见视野里的东西")
    r.add_argument("--player", type=int, help="以几号玩家的身份指挥（AI 对 AI）")
    st = sub.add_parser("status")
    st.add_argument("--inst", type=int)
    st.add_argument("--pid", type=int)
    c = sub.add_parser("catalog")
    c.add_argument("--json", action="store_true")
    c.add_argument("--write", action="store_true", help="重新生成 docs/API_ZH.md 和 docs/api.json")
    js = sub.add_parser("jass", help="JASS 控制台 / 跑 JASS 小脚本 / 查 native")
    js.add_argument("script", nargs="?", help="脚本文件（.j / .txt）；不给就进交互控制台")
    js.add_argument("--inst", type=int)
    js.add_argument("--pid", type=int)
    js.add_argument("-e", "--exec", dest="code", help="直接跑这一段（多句用换行或分号 ;）")
    js.add_argument("--list", nargs="?", const="", help="查 native（按名字模糊搜）")
    js.add_argument("--cat", default="", help="--list 时按分类筛：visual ui camera sound fog order item hero unit …")
    js.add_argument("--lang", default="zh", choices=["zh", "en"], help="--list / ?名字 显示哪种语言的说明")
    a = ap.parse_args()

    if a.cmd == "jass":
        return jass_main(a)
    import openwar3
    if a.cmd == "run":
        print(openwar3.run(load_bot(a.bot), inst=a.inst, pid=a.pid, hz=a.hz, max_minutes=a.minutes, fast=not a.no_fast,
                           fair=a.fair, player=a.player))
    elif a.cmd == "status":
        g = openwar3.Game(inst=a.inst, pid=a.pid)
        s = g.snapshot()
        print(json.dumps(g.status() | {"inGame": bool(s and s.in_game), "units": len(s.units) if s else 0,
                                       "me": s.me if s else None, "clock": g.clock()}, ensure_ascii=False, indent=1))
    else:
        from openwar3 import catalog
        if a.write:
            print("\n".join(catalog.write_docs()))
            return 0
        print(json.dumps(catalog.entries(), ensure_ascii=False, indent=1) if a.json else catalog.markdown())
    return 0


def jass_main(a) -> int:
    from openwar3 import jass as J
    if a.list is not None:
        rows = J.catalog(a.list, a.cat, lang=a.lang)
        for e in rows:
            flag = ("✔" if e["verified"] else " ") + ("读" if e["readonly"] else "  ")
            print(f"{flag} {J.pretty_signature(e['name'])}  [{e['categoryName']}]\n      {e['desc']}")
        print(f"共 {len(rows)} 个（✔ = 09-24 实机调过；读 = 只读，多人局也能调；名字和中英文说明都能搜）。分类：",
              " ".join(f"{k}={J.category_name(k, a.lang)}" for k in J.CATEGORY_NAMES))
        return 0
    import openwar3
    from openwar3.jassrun import Script, ScriptError
    g = openwar3.Game(inst=a.inst, pid=a.pid)
    sc = Script(g, echo=print)

    def run(code: str) -> bool:
        try:
            for r in sc.run(code):
                if r["value"] is not None:
                    print(f"  = {r['value']}")
            return True
        except (ScriptError, J.JassError) as e:
            print(f"  ✘ {e}")
            return False

    if a.code is not None:
        return 0 if run(a.code.replace(";", "\n")) else 1
    if a.script:
        with open(a.script, encoding="utf-8-sig") as fh:
            return 0 if run(fh.read()) else 1
    print(f"JASS 控制台（pid {g.pid}）：一行一句，直接粘 JASS（call / set / local）；变量会一直记着。空行退出，?名字 查签名。")
    while True:
        try:
            line = input("jass> ").strip()
        except EOFError:
            break
        if not line:
            break
        if line.startswith("?"):
            for e in J.catalog(line[1:].strip(), lang=a.lang)[:20]:
                print(f"  {J.pretty_signature(e['name'])}  [{e['categoryName']}]\n      {e['desc']}")
            continue
        run(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

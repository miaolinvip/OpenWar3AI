# -*- coding: utf-8 -*-
"""远见（console/web）界面翻译检查：源码里每个 tx("中文") 键，在各语言字典里有没有译文。

    python tools/i18n_check.py                 # 各语言缺几条、占位符对不对
    python tools/i18n_check.py --todo en --out todo_en.json
                                               # 把 en 缺的键导出成 {中文: ""}，翻完合进 src/i18n/en.json

键的规则见 console/web/src/lib/i18n.ts：键 = 中文原文，占位符 {0} {1}… 按参数顺序。
键有两处来源：前端源码里的字面量 tx("...")，和 backend_keys() 收集的后端固定标签（配置 schema、日志源名……）。
别的 tx(变量)（如问题分类、决策来源）要自己往字典里加。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_SRC = os.path.join(ROOT, "console", "web", "src")
DICT_DIR = os.path.join(WEB_SRC, "i18n")
KEY_RX = re.compile(r'\btx\(\s*"((?:[^"\\]|\\.)*)"')
PH_RX = re.compile(r"\{\d+\}")
HAN = re.compile(r"[一-鿿]")


def source_keys(src: str = WEB_SRC) -> dict[str, list[str]]:
    """{键: [文件:行]}"""
    out: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(src, "**", "*.ts*"), recursive=True)):
        if path.endswith(".d.ts"):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        rel = os.path.relpath(path, src).replace("\\", "/")
        for m in KEY_RX.finditer(text):
            key = json.loads('"' + m.group(1) + '"')
            line = text.count("\n", 0, m.start()) + 1
            out.setdefault(key, []).append(f"{rel}:{line}")
    return out


def backend_keys() -> dict[str, list[str]]:
    """后端发给远见、远见用 tx(值) 显示的固定中文标签：{键: [来源]}。
    AI 配置 / 运行开关的 schema（组名、名字、说明、选项名）、日志源名、APM 来源名、导播预设和运镜序列名。
    ⚠ 大脑写的决策理由、日志正文、导播切镜理由是**内容**不是界面，不翻（见 docs/I18N_ZH.md）。"""
    out: dict[str, list[str]] = {}

    def add(s, where):
        if isinstance(s, str) and HAN.search(s):
            out.setdefault(s, []).append(where)

    console = os.path.join(ROOT, "console")
    if console not in sys.path:
        sys.path.insert(0, console)
    from server import common as C  # noqa: E402  它把 sdk/python 加进 sys.path
    if C.BRAIN not in sys.path:
        sys.path.insert(0, C.BRAIN)
    from brain import config as BCFG  # noqa: E402
    from brain import ops as BOPS  # noqa: E402
    from server import apm  # noqa: E402
    for name, schema in (("brain/config", BCFG.schema()), ("ops", BOPS.schema())):
        for c in schema:
            for f in ("group", "label", "desc"):
                add(c.get(f), f"{name}:{c.get('key')}.{f}")
            for v in (c.get("labels") or {}).values():
                add(v, f"{name}:{c.get('key')}.labels")
    for v in apm.SOURCE_LABEL.values():
        add(v, "apm.SOURCE_LABEL")
    with open(os.path.join(console, "server", "logs.py"), encoding="utf-8") as fh:
        for m in re.finditer(r'\("\w+", "([^"]+)", os\.path', fh.read()):
            add(m.group(1), "logs.sources")
    with open(os.path.join(ROOT, "director", "camctl.py"), encoding="utf-8") as fh:
        cam = fh.read()
    for m in re.finditer(r"label='([^']+)'", cam):
        add(m.group(1), "camctl.PRESETS")
    for m in re.finditer(r"^    '\w+': \('([^']+)', \[", cam, re.M):
        add(m.group(1), "camctl.SEQUENCES")
    # 战况触发的事件名（远见「头顶气泡 → 战况触发」的事件表和日志里 tx(名字) 显示）
    with open(os.path.join(ROOT, "speech", "eventtalk.py"), encoding="utf-8") as fh:
        for m in re.finditer(r"^    '\w+': \('([^']+)', (?:True|False),", fh.read(), re.M):
            add(m.group(1), "eventtalk.EVENTS")
    return out


def all_keys() -> dict[str, list[str]]:
    keys = source_keys()
    for k, v in backend_keys().items():
        keys.setdefault(k, []).extend(v)
    return keys


def dictionaries() -> dict[str, dict[str, str]]:
    out = {}
    for p in sorted(glob.glob(os.path.join(DICT_DIR, "*.json"))):
        with open(p, encoding="utf-8") as fh:
            out[os.path.splitext(os.path.basename(p))[0]] = json.load(fh)
    return out


def problems(keys: dict[str, list[str]], table: dict[str, str]) -> dict[str, list]:
    """缺译文的 / 占位符对不上的 / 译文里还夹着中文的"""
    missing = [k for k in keys if not table.get(k)]
    bad_ph = [k for k in keys if table.get(k) and sorted(PH_RX.findall(k)) != sorted(PH_RX.findall(table[k]))]
    has_han = [k for k in keys if table.get(k) and HAN.search(table[k])]
    return {"missing": missing, "placeholders": bad_ph, "han": has_han}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", help="导出这个语言缺的键")
    ap.add_argument("--out", help="--todo 写到这个文件（默认打印）")
    a = ap.parse_args()
    keys = all_keys()
    dicts = dictionaries()
    if a.todo:
        table = dicts.get(a.todo, {})
        todo = {k: "" for k in keys if not table.get(k)}
        text = json.dumps(todo, ensure_ascii=False, indent=1)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"{len(todo)} 条写到 {a.out}")
        else:
            print(text)
        return 0
    rc = 0
    print(f"源码里 {len(keys)} 个键")
    for lang, table in dicts.items():
        p = problems(keys, table)
        print(f"  {lang}: 缺 {len(p['missing'])}，占位符不对 {len(p['placeholders'])}，译文夹中文 {len(p['han'])}")
        for k in (p["missing"] + p["placeholders"])[:10]:
            print(f"     {k!r}  {keys[k][0]}")
        rc |= bool(p["missing"] or p["placeholders"])
    return rc


if __name__ == "__main__":
    sys.exit(main())

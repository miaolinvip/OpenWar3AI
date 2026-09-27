# -*- coding: utf-8 -*-
"""从**你自己的**魔兽争霸 III 1.27 安装目录提取项目要用的游戏数据，写进 data/game/。

    python data/tools/extract_game_data.py              # 提取 + 生成 units.json / names.json / skills.json ...
    python data/tools/extract_game_data.py --out D:\\tmp  # 提取到别处（核对用）

为什么不直接放进仓库：这些 .slk/.txt 是暴雪的游戏文件，不能随开源代码分发。每个人从自己的游戏里取。
补丁包优先（War3Patch -> War3xLocal -> War3x -> war3）：先找到的那份才是游戏实际生效的。
需要：pip install mpyq
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ORDER = ["War3Patch.mpq", "War3xLocal.mpq", "War3x.mpq", "war3.mpq"]

# 目标文件名 -> MPQ 里的路径（MiscData.txt 在两个目录各有一份，UI 那份存成 UI_MiscData.txt）
WANT = {
    "UnitBalance.slk": "Units\\UnitBalance.slk",
    "UnitWeapons.slk": "Units\\UnitWeapons.slk",
    "UnitData.slk": "Units\\UnitData.slk",
    "UnitUI.slk": "Units\\UnitUI.slk",
    "UnitAbilities.slk": "Units\\UnitAbilities.slk",
    "AbilityData.slk": "Units\\AbilityData.slk",
    "UpgradeData.slk": "Units\\UpgradeData.slk",
    "ItemData.slk": "Units\\ItemData.slk",
    "DestructableData.slk": "Units\\DestructableData.slk",
    "ItemFunc.txt": "Units\\ItemFunc.txt",
    "ItemStrings.txt": "Units\\ItemStrings.txt",
    "ItemAbilityFunc.txt": "Units\\ItemAbilityFunc.txt",
    "ItemAbilityStrings.txt": "Units\\ItemAbilityStrings.txt",
    "HumanAbilityStrings.txt": "Units\\HumanAbilityStrings.txt",
    "MiscData.txt": "Units\\MiscData.txt",
    "MiscGame.txt": "Units\\MiscGame.txt",          # 09-24：伤害克制表 DamageBonus*、护甲减伤 DefenseArmor、英雄三围换算
    "MiscUI.txt": "UI\\MiscUI.txt",
    "UI_MiscData.txt": "UI\\MiscData.txt",
}
# 提取之后按顺序跑的生成器（都读写 data/game）
GENERATORS = ["parse_slk.py", "extract_names.py", "make_skills.py", "make_items.py", "make_buffs.py"]


def game_dir() -> str:
    if os.environ.get("WAR3_GAME_DIR"):
        return os.environ["WAR3_GAME_DIR"]
    for f in ("openwar3.json", "openwar3.example.json"):
        try:
            with open(os.path.join(ROOT, f), encoding="utf-8-sig") as fh:
                d = json.load(fh).get("game_dir")
                if d:
                    return d
        except (OSError, ValueError):
            pass
    raise SystemExit("不知道游戏装在哪：在远见首页「控制中心」设置魔兽争霸 III 的目录（存在 openwar3.json 的 game_dir）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "game"))
    ap.add_argument("--no-generate", action="store_true")
    a = ap.parse_args()
    try:
        import mpyq
    except ImportError:
        print("缺 mpyq：pip install mpyq")
        return 1
    gd = game_dir()
    os.makedirs(a.out, exist_ok=True)
    got: dict[str, str] = {}
    for arc in ORDER:
        p = os.path.join(gd, arc)
        if not os.path.exists(p):
            continue
        try:
            mpq = mpyq.MPQArchive(p)
        except Exception as e:  # noqa: BLE001
            print(f"  {arc} 打不开：{type(e).__name__}: {e}")
            continue
        for name, inner in WANT.items():
            if name in got:
                continue            # 补丁包里已经拿到了，基础包不能覆盖
            try:
                data = mpq.read_file(inner)
            except Exception:  # noqa: BLE001
                data = None
            if data:
                with open(os.path.join(a.out, name), "wb") as fh:
                    fh.write(data)
                got[name] = arc
    missing = [n for n in WANT if n not in got]
    for n, arc in sorted(got.items()):
        print(f"  ✔ {n:26s} <- {arc}")
    if missing:
        print(f"  ✘ 没找到：{', '.join(missing)}（游戏目录对吗？{gd}）")
    if a.no_generate:
        return 0 if not missing else 2
    env = dict(os.environ, WAR3_GAME_DIR=gd, PYTHONIOENCODING="utf-8")
    for g in GENERATORS:
        r = subprocess.run([sys.executable, os.path.join(HERE, g)], cwd=HERE, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        print(f"  {'✔' if r.returncode == 0 else '✘'} {g}" + ("" if r.returncode == 0 else f"\n{r.stderr[-600:]}"))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())

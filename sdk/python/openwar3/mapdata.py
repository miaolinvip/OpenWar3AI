# -*- coding: utf-8 -*-
"""正在打的这张地图的数据（09-24，RPG 辅助用）：自定义单位 / 物品 / 技能的名字。

RPG 地图的单位、物品、英雄大多是地图作者在物体编辑器里新建的（四字码像 HC07、I00A），内置的单位表和名字表里没有 ——
不读地图，玩伴连"那是什么怪""你捡的是什么"都说不出来。名字就在地图文件里：war3map.w3u/w3t/w3a + war3map.wts。

    md = g.map_data                    # 当前这局的地图（按游戏进程找启动器的 --map；找不到是 None）
    md.name_of("HC07")                 # '剑圣' 之类：地图改过的名字优先，再退回内置名字表，都没有就是四字码
    md.hero_names("HC07")              # 英雄的称号列表（upro）
    MapData(r"<游戏目录>\\Maps\\Scenario\\(4)WarChasers.w3m")   # 也可以直接给路径
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import w3paths

from w3mpq import PROFILE_FILES, Mpq, parse_objects, parse_profile, parse_w3i_name, parse_wts, trigstr

NAME_FIELDS = ("unam", "anam", "fnam", "bnam", "gnam")     # 单位/物品、技能、魔法效果、可破坏物、升级的名字字段


@lru_cache(maxsize=1)
def builtin_names() -> dict:
    """内置名字表：四字码 -> 中文名（data/game/names.json 的 byCode，2000 多条）。"""
    try:
        with open(w3paths.NAMES_JSON, encoding="utf-8") as fh:
            return json.load(fh).get("byCode", {})
    except (OSError, ValueError):
        return {}


def running_map_path(pid: int) -> str | None:
    """这个游戏进程正在跑哪张地图：游戏是提权跑的、读不到它的命令行，但启动器 war3_launch.exe 不提权、它的子进程就是游戏，
    命令行里有 --map。不是启动器起的（手动开的游戏）返回 None。"""
    try:
        import psutil
    except ImportError:
        return None
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() != "war3_launch.exe":
            continue
        try:
            if pid not in {c.pid for c in p.children()}:
                continue
            cmd = p.cmdline()
        except psutil.Error:
            continue
        if "--map" in cmd:
            i = cmd.index("--map")
            if i + 1 < len(cmd):
                return cmd[i + 1]
    return None


class MapData:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            m = Mpq(fh.read())
        self.wts = parse_wts(m.read("war3map.wts"))
        self.units = parse_objects(m.read("war3map.w3u"), self.wts)
        self.items = parse_objects(m.read("war3map.w3t"), self.wts)
        self.abilities = parse_objects(m.read("war3map.w3a"), self.wts, leveled=True)
        self.buffs = parse_objects(m.read("war3map.w3h"), self.wts)
        # 保护过的地图不带上面那些，名字在图里的 TXT（mpq.PROFILE_FILES）；普通地图一般没有这些文件
        self.profile: dict = {}
        for f in PROFILE_FILES:
            try:
                parse_profile(m.read(f), self.profile)
            except Exception:  # noqa: BLE001 - 一个文件坏了不影响别的
                continue
        self.name = os.path.splitext(os.path.basename(path))[0]
        try:
            self.name = parse_w3i_name(m.read("war3map.w3i"), self.wts) or self.name
        except Exception:  # noqa: BLE001 - 名字只是展示用
            pass

    def _obj(self, code: str) -> dict | None:
        for table in (self.units, self.items, self.abilities, self.buffs):
            if code in table:
                return table[code]
        return None

    def name_of(self, code: str) -> str:
        """四字码 -> 名字：地图改过/新建的优先，再退回内置名字表（自定义的沿用它的原型的名字），都没有就是四字码本身。"""
        o = self._obj(code)
        if o:
            for f in NAME_FIELDS:
                if o["fields"].get(f):
                    return trigstr(str(o["fields"][f]), self.wts)
        p = self.profile.get(code, {})
        if p.get("Name"):
            return trigstr(p["Name"], self.wts)
        if o:
            base = builtin_names().get(o["base"])
            if base:
                return base
        return builtin_names().get(code, code)

    def hero_names(self, code: str) -> list[str]:
        """英雄的称号（物体编辑器 upro / TXT 的 Propernames，逗号分隔的一串，游戏随机挑一个）。"""
        o = self.units.get(code)
        v = (o["fields"].get("upro") if o else None) or self.profile.get(code, {}).get("Propernames")
        return [s.strip() for s in trigstr(str(v), self.wts).split(",") if s.strip()] if v else []

    def hero_skills(self, code: str) -> list[str]:
        """英雄能学的技能（物体编辑器 uhab，地图改过英雄技能时才有；内置英雄没改过就是空列表）。"""
        o = self.units.get(code)
        v = o["fields"].get("uhab") if o else None
        return [s.strip() for s in str(v).split(",") if s.strip()] if v else []

    def tooltip(self, code: str) -> str:
        """说明文字（单位/物品 utub 或 utip，技能 aub1/atp1，TXT 的 Ubertip/Tip），地图没改就是空串。"""
        o = self._obj(code)
        if o:
            for f in ("utub", "utip", "aub1", "atp1", "aub1@1", "atp1@1"):
                if o["fields"].get(f):
                    return trigstr(str(o["fields"][f]), self.wts)
        p = self.profile.get(code, {})
        return trigstr(p.get("Ubertip") or p.get("Tip") or "", self.wts)

    def summary(self) -> dict:
        return {"path": self.path, "name": self.name, "units": len(self.units), "items": len(self.items),
                "abilities": len(self.abilities), "customUnits": sum(1 for o in self.units.values() if o["custom"]),
                "profileEntries": len(self.profile), "strings": len(self.wts)}


@lru_cache(maxsize=16)
def _load(path: str, mtime: float) -> MapData:
    return MapData(path)


def for_pid(pid: int) -> MapData | None:
    """这个游戏进程正在跑的地图的数据（同一个文件只解析一次）。"""
    path = running_map_path(pid)
    if not path or not os.path.isfile(path):
        return None
    return _load(path, os.path.getmtime(path))

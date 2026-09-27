# -*- coding: utf-8 -*-
"""按种族查的规则：谁能出什么、谁研究什么、祭坛/人口建筑/出兵建筑是哪个、谁伐木、要不要工人盖房、开矿怎么开。

★ 09-18 试用亡灵族时发现：原来 runtime 里 PRODUCERS/RESEARCHERS/ALTAR/"hbar" 全写死人族，
  亡灵开局大脑去造人族祭坛、派侍僧砍树；更要命的是 is_building 靠 units.json 的 foot，亡灵大厅 foot=0，
  大脑以为自己没有大厅，10 秒判输、动作 22 把游戏结束掉。
"""
from __future__ import annotations

from .tables import human, nightelf, orc, undead

HUMAN = dict(
    name="human", table=human, profile=human.PROFILE,
    worker="hpea", lumber_unit="hpea", supply="hhou", supply_food=6, altar="halt", barracks="hbar",
    halls=("htow", "hkee", "hcas"), hall_up={"hkee": "htow", "hcas": "hkee"},
    producers={"hpea": ("htow", "hkee", "hcas"), "hfoo": ("hbar",), "hrif": ("hbar",), "hkni": ("hbar",),
               "hmpr": ("hars",), "hsor": ("hars",), "hspt": ("hars",), "hmtm": ("harm",), "hgyr": ("harm",),
               "hgry": ("hgra",), "hdhw": ("hgra",)},
    researchers={"Rhme": "hbla", "Rhar": "hbla", "Rhra": "hbla", "Rhla": "hbla", "Rhde": "hbar", "Rhlh": "hlum", "Rhac": "hlum",
                 "Rhpm": ("hkee", "hcas"), "Rhst": "hars"},
    rally=("hbar", "hars", "harm", "hgra", "halt"),
    needs_builder=True,            # 人族工地没人就停工（续建任务）
    expand_code="htow", expand_on_mine=False,
    # ★★ 09-21 山丘表现很差，而血魔法师从未出场；技能用得好的血魔法师远比技能没用起来的山丘有价值，
    #   二英雄改为血魔法师。
    #   换二英雄之前必须先有加点表：**AMAI 的 hero_skills 里只有大法师和山丘两行**
    #   （brain/amai_data/human.json 实测），血法师取不到会返回 []，那就成了「出了不加点」。
    #   顺序按技能作用定：
    #     AHfs 烈焰风暴  —— 唯一接进毫秒层的（mod_combat SPELLS["flamestrike"]），主输出，先满
    #     AHdr 法力虹吸  —— 吸蓝续航（血法师法力池是它的命）
    #     AHbn 放逐      —— 单体控制；毫秒层还没接，先少点
    #     AHpx 凤凰      —— 大招，6 级
    #   ⚠ 没接毫秒层的三个只会按引擎自动行为用（基本等于不用），这是已知缺口。
    skills={"Hblm": ["AHfs", "AHdr", "AHfs", "AHdr", "AHfs", "AHpx", "AHdr", "AHbn", "AHbn", "AHbn"]},
)

UNDEAD = dict(
    name="undead", table=undead, profile=undead.PROFILE,
    worker="uaco", lumber_unit="ugho", supply="uzig", supply_food=10, altar="uaod", barracks="usep",
    halls=("unpl", "unp1", "unp2"), hall_up={"unp1": "unpl", "unp2": "unp1"},
    producers={"uaco": ("unpl", "unp1", "unp2"), "ugho": ("usep",), "ucry": ("usep",), "ugar": ("usep",),
               "unec": ("utod",), "uban": ("utod",), "uabo": ("uslh",), "umtw": ("uslh",), "uobs": ("uslh",),
               "ufro": ("ubon",)},
    researchers={"Rume": "ugrv", "Ruar": "ugrv", "Rucr": "ugrv", "Rura": "ugrv",
                 "Ruac": "usep", "Rugf": "usep", "Ruwb": "usep", "Rubu": "usep", "Rusf": "usep",
                 "Rune": "utod", "Ruba": "utod", "Rusl": "utod", "Rusm": "utod", "Rupc": "uslh", "Ruex": "uslh", "Rufb": "ubon"},
    rally=("usep", "utod", "uslh", "ubon", "uaod"),
    needs_builder=False,           # 亡灵召唤建筑，侍僧放下就走，地基自己长
    expand_code="ugol", expand_on_mine=True,   # 闹鬼金矿直接盖在金矿上
    # learned.json（高手录像）里没有亡灵英雄，先用 AMAI TFT/Undead/HeroSkills.txt 第一行的顺序
    skills={"Udea": ["AUdc", "AUau", "AUdc", "AUau", "AUdc", "AUan", "AUau", "AUdp", "AUdp", "AUdp"],
            "Ulic": ["AUfn", "AUfu", "AUfn", "AUdr", "AUfn", "AUdd", "AUdr", "AUfu", "AUfu", "AUdr"],
            "Udre": ["AUcs", "AUav", "AUcs", "AUsl", "AUcs", "AUin", "AUav", "AUav", "AUsl", "AUsl"],
            "Ucrl": ["AUim", "AUts", "AUim", "AUcb", "AUim", "AUls", "AUts", "AUts", "AUcb", "AUcb"]},
)

NIGHTELF = dict(
    name="nightelf", table=nightelf, profile=nightelf.PROFILE,
    worker="ewsp", lumber_unit="ewsp", supply="emow", supply_food=10, altar="eate", barracks="eaom",
    halls=("etol", "etoa", "etoe"), hall_up={"etoa": "etol", "etoe": "etoa"},
    producers={"ewsp": ("etol", "etoa", "etoe"), "earc": ("eaom",), "esen": ("eaom",), "ebal": ("eaom",),
               "edry": ("eaoe",), "edoc": ("eaoe",), "emtg": ("eaoe",), "edot": ("eaow",), "ehip": ("eaow",), "efdr": ("eaow",),
               "echm": ("edos",)},
    researchers={"Resm": "edob", "Resw": "edob", "Rema": "edob", "Rerh": "edob", "Reuv": "edob", "Rews": "edob", "Repm": "edob",
                 "Reib": "eaom", "Remk": "eaom", "Resc": "eaom", "Remg": "eaom", "Repb": "eaom",
                 "Resi": "eaoe", "Redc": "eaoe", "Rers": "eaoe", "Rehs": "eaoe", "Reeb": "eaoe",
                 "Redt": "eaow", "Reht": "eaow", "Reec": "eaow", "Recb": "edos"},
    rally=("eaom", "eaoe", "eaow", "edos", "eate"),
    needs_builder=False,           # 小精灵并进建筑，建筑自己长
    expand_code="etol", expand_on_mine=False,
    mine_types=("egol",),          # 只认自己缠绕过的金矿
    # AMAI TFT/Elf/HeroSkills.txt 第一行（恶魔猎手 learned.json 里有，优先用那份）
    skills={"Ekee": ["AEer", "AEah", "AEer", "AEfn", "AEer", "AEtq", "AEah", "AEah", "AEfn", "AEfn"],
            "Emoo": ["AHfa", "AEar", "AHfa", "AEar", "AHfa", "AEsf", "AEar", "AEst", "AEst", "AEst"],
            "Ewar": ["AEfk", "AEbl", "AEfk", "AEbl", "AEfk", "AEsv", "AEbl", "AEsh", "AEsh", "AEsh"]},
)

ORC = dict(
    name="orc", table=orc, profile=orc.PROFILE,
    worker="opeo", lumber_unit="opeo", supply="otrb", supply_food=10, altar="oalt", barracks="obar",
    halls=("ogre", "ostr", "ofrt"), hall_up={"ostr": "ogre", "ofrt": "ostr"},
    producers={"opeo": ("ogre", "ostr", "ofrt"), "ogru": ("obar",), "ohun": ("obar",), "ocat": ("obar",),
               "orai": ("obea",), "okod": ("obea",), "owyv": ("obea",), "otbr": ("obea",),
               "oshm": ("osld",), "odoc": ("osld",), "ospw": ("otto",), "otau": ("otto",)},
    researchers={"Rome": "ofor", "Rora": "ofor", "Roar": "ofor", "Rwdm": "ofor", "Ropg": "ofor", "Rosp": "ofor", "Rorb": "ofor",
                 "Robs": "obar", "Rotr": "obar", "Robk": "obar", "Robf": "obar",
                 "Roen": "obea", "Rovs": "obea", "Rolf": "obea",
                 "Rost": "osld", "Rowd": "osld", "Rowt": "otto", "Rows": "otto"},
    rally=("obar", "obea", "osld", "otto", "oalt"),
    needs_builder=True,            # 兽族苦工和人族农民一样要守着工地
    expand_code="ogre", expand_on_mine=False,
    # AMAI TFT/Orc/HeroSkills.txt 第一行（剑圣、暗影猎手 learned.json 里有，优先用那份）
    skills={"Ofar": ["AOcl", "AOfs", "AOcl", "AOsf", "AOcl", "AOeq", "AOsf", "AOsf", "AOfs", "AOfs"],
            "Otch": ["AOsh", "AOws", "AOsh", "AOae", "AOsh", "AOre", "AOws", "AOws", "AOae", "AOae"]},
)

SPECS = {"human": HUMAN, "undead": UNDEAD, "nightelf": NIGHTELF, "orc": ORC}
_HALL_RACE = {h: s["name"] for s in SPECS.values() for h in s["halls"]}
_WORKER_RACE = {s["worker"]: s["name"] for s in SPECS.values()}


def detect(units, me: int) -> str | None:
    """看自己的大厅/工人是哪个种族的；都看不见返回 None（调用方沿用上次或人族）。"""
    for u in units:
        if u.owner == me and u.hp > 0 and u.type in _HALL_RACE:
            return _HALL_RACE[u.type]
    for u in units:
        if u.owner == me and u.hp > 0 and u.type in _WORKER_RACE:
            return _WORKER_RACE[u.type]
    return None


_AMAI_ROWS: dict = {}


# 09-19 加点方案：水元素主升、辉煌光环回蓝；前六级先完成两条主技能。
# 比录像和 AMAI 随机加点线优先。暴风雪留到后期，不在缺蓝时分散早期技能点。
SKILL_OVERRIDE = {
    "Hamg": ["AHwe", "AHab", "AHwe", "AHab", "AHwe", "AHab", "AHmt", "AHbz", "AHbz", "AHbz"],
}


def amai_skill_order(race: str | None, hero_code: str, slot: int = 1, line: str = "major") -> list:
    """AMAI 加点表（brain/amai_data/<种族>.json 的 hero_skills.rows；行号已按 Heroes.txt 换算到 英雄 × 第几个出 × 主/次/稀有线）。
    ★ 09-18：上面 skills={...} 那几份标着「HeroSkills.txt 第一行」，其实是没换算行号取错了英雄的行 ——
      先知第 2 点学远见术（AMAI 主线是 野狼、闪电链、野狼、闪电链、闪电链、地震、野狼，远见术 8 级以后才学），
      011447 局心跳「spiritwolf 没学 96 轮」。取不到返回 []（调用方退回 skills 表）。"""
    if race not in SPECS:
        return []
    if race not in _AMAI_ROWS:
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amai_data", f"{race}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _AMAI_ROWS[race] = json.load(fh)["hero_skills"]["rows"]
        except (OSError, ValueError, KeyError):
            _AMAI_ROWS[race] = []
    rows = [r for r in _AMAI_ROWS[race] if r.get("hero_code") == hero_code and r.get("line") == line]
    row = next((r for r in rows if r.get("hero_slot") == slot), rows[0] if rows else None)
    return list(row["codes"]) if row else []


def spec(race: str | None) -> dict:
    return SPECS.get(race or "human", HUMAN)

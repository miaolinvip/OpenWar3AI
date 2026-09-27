# -*- coding: utf-8 -*-
"""AI 方案库：玩家之间分享、下载、切换 AI 的单位。

一个**方案** = 一个文件夹：`scheme.json`（说明书）+ 代码。打成 zip 就能分享，导入到别人那里互不影响。

    {
      "format": 1,
      "id": "fast-rush",                    # 小写字母/数字/-/_，2~41 个字符
      "name": "人族速推",
      "version": "1.2.0",
      "author": "zhangsan",
      "description": "两兵营步兵火枪，5 分钟出门",
      "kind": "bot",                         # bot = openwar3.Bot 的子类（SDK 写的）；mod = openwar3.Mod 的子类（玩法模组，09-25）
      "entry": "rush.py",                    # 入口文件（相对方案目录）
      "class": "RushBot",                    # 可选：入口里有多个 Bot 子类时指定哪一个
      "fair": true,                          # 可选：按公平模式跑（只看得见视野里的东西，推荐 true）
      "hz": 5,                               # 可选：每秒几拍
      "races": ["human"],                    # 可选：支持的种族（给人看的）
      "license": "MIT", "homepage": "",       # 可选
      "i18n": {"en": {"name": "Human fast rush", "description": "Two barracks, footmen + riflemen, out at 5 min"}}
                                             # 可选：别的语言的名字/简介（远见按界面语言挑，没有就用上面的）
    }

三个来源，互不干扰（ref = "来源/id"）：
  builtin/…    内置：参考大脑（kind=brain，自带监督器）和 brains/examples 的示例，只读
  mine/…       我自己的（schemes/mine/<id>），可以改
  installed/…  从别人那导入的（schemes/installed/<id>），只读；要改先「复制到我的」（fork）

⚠ 方案是代码，运行时和你本人的权限一样。导入的方案默认**不信任**，第一次运行前要 trust(ref)（远见里点一次「信任」）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile

import w3paths

SCHEME_FILE = "scheme.json"
TRUST_FILE = ".trusted"
FORMAT = 1
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
SOURCES = ("builtin", "mine", "installed")
MAX_ZIP_BYTES = 50 * 1024 * 1024        # 解压后总大小上限
MAX_FILES = 2000
RESULTS_FILE = "scheme_matches.jsonl"   # 每个实例目录里：每局一行（哪个方案、结果、用时）
DEFAULT_REF = "builtin/xwar3"           # 没选过方案的实例 = 参考大脑（和以前的行为一样）

# 内置方案：参考大脑 + 示例。示例之间互相 import（micro <- macro <- hello），所以它们的"目录"就是整个 examples
_EX = [
    ("hello", "经济入门（hello_bot）", "hello_bot.py", "HelloBot",
     "只管经济：闲农民去采矿（一矿 5 人）、造农民、盖人口建筑。读代码学接口的第一站。",
     {"i18n": {"en": {"name": "Economy basics (hello_bot)",
                      "description": "Economy only: idle workers go mine (5 per mine), train workers, build farms. The first stop for learning the API by reading code."}}}),
    ("rush", "出兵示例（rush_bot）", "rush_bot.py", "RushBot",
     "在经济上加兵营、英雄（死了复活）、攒够一波攻击移动。",
     {"i18n": {"en": {"name": "Army example (rush_bot)",
                      "description": "Adds barracks and a hero (revived on death) on top of the economy, then attack-moves once a wave is ready."}}}),
    ("macro", "运营示例（macro_bot）", "macro_bot.py", "MacroBot",
     "建造顺序 + 盖完回矿、人口卡住立刻补、队列 1 个、攻防、升本、侦察、按路程出击。",
     {"i18n": {"en": {"name": "Macro example (macro_bot)",
                      "description": "Build order, builders return to mining, instant supply fixes, 1-deep queues, upgrades, tier-ups, scouting, distance-aware attacks."}}}),
    ("micro", "微操示例（micro_bot）", "micro_bot.py", "MicroBot",
     "在运营上加集火、拉残血、英雄保命、夜里打野、回防。⚠ 目前打不过简单电脑（约 20 分钟被推平）。",
     {"i18n": {"en": {"name": "Micro example (micro_bot)",
                      "description": "Adds focus fire, pulling back wounded units, hero survival, night creeping and defending home. ⚠ Currently loses to the Easy computer (overrun in about 20 minutes)."}}}),
    # 玩伴（09-24）：RPG / 自定义地图里陪玩家打的伙伴。要 JASS 通道（造单位、设盟友）所以不是公平模式；不按对战规则判胜负
    ("buddy", "玩伴示例（buddy）", "buddy.py", "Buddy",
     "RPG 地图里给你配一个圣骑士伙伴：跟着你、帮你打怪、残血给你加血、陪你说话。单人局用；你自己玩，它来帮忙。",
     {"fair": False, "judge": False, "races": ["any"], "maps": "rpg",
      "i18n": {"en": {"name": "Companion example (buddy)",
                      "description": "Gives you a Paladin buddy on RPG maps: follows you, fights with you, heals you when you're low and chats with you. Single-player only; you play, it helps."}}}),
    # 玩法模组（09-25，kind=mod）：不是 AI，是一套规则。你自己玩，模组出题
    ("hero-roguelike", "英雄肉鸽（模组示例）", "mod_hero_roguelike.py", "HeroRoguelike",
     "你只有一个圣骑士，一波波怪从四面围上来；每升一级在屏幕中间三选一强化（选的时候游戏暂停）。撑过 10 波算赢，英雄死了算输。单人局。",
     {"kind": "mod", "fair": False, "judge": False, "hz": 8, "races": ["any"],
      "i18n": {"en": {"name": "Hero roguelike (mod example)",
                      "description": "You only have a Paladin; waves of monsters close in from all sides. Each level-up offers three upgrades on screen (the game pauses while you pick). Survive 10 waves to win; lose if the hero dies. Single-player."}}}),
    ("endless-defense", "无尽守城（模组示例）", "mod_endless_defense.py", "EndlessDefense",
     "守住主城：怪一波比一波多，从对面出生点冲过来。每守住一波给金子；点屏幕上的按钮（或 F7）提前叫下一波多拿奖励，"
     "F8 在地上点一下放一座免费的箭塔。主城倒了算输。单人局。",
     {"kind": "mod", "fair": False, "judge": False, "hz": 8, "races": ["any"],
      "i18n": {"en": {"name": "Endless defense (mod example)",
                      "description": "Hold your town hall: waves grow bigger each time and charge in from the opposite start location. Each wave held pays gold; click the on-screen button (or F7) to call the next wave early for a bonus; F8 then click the ground to drop a free guard tower. Lose when the hall falls. Single-player."}}}),
]
KINDS = ("bot", "mod")                  # 玩家方案能是的类型（brain 只有内置的参考大脑）


class SchemeError(ValueError):
    pass


class SchemeExists(SchemeError):
    """导入时同一个 id 已经装过（远见据此问「要不要替换」，不去匹配报错文字）。"""


def _builtins() -> list[dict]:
    out = [{
        "format": FORMAT, "id": "xwar3", "name": "参考大脑（xwar3）", "version": "brain",
        "author": "OpenWar3", "kind": "brain", "fair": False,
        "description": "完整的参考 AI：开矿、打野、出击、英雄施法、毫秒层保命。对简单电脑约 7~8 成胜率。自带监督器（run_brain.py）。",
        "races": ["human", "orc", "undead", "nightelf"],
        "i18n": {"en": {"name": "Reference brain (xwar3)",
                        "description": "The full reference AI: expanding, creeping, attacking, hero spells, reflex-layer survival. "
                                       "Wins about 70-80% against the Easy computer. Comes with its own supervisor (run_brain.py)."}},
    }]
    for sid, name, entry, cls, desc, *extra in _EX:
        out.append({"format": FORMAT, "id": sid, "name": name, "version": "example", "author": "OpenWar3", "kind": "bot",
                    "entry": entry, "class": cls, "fair": True, "judge": True, "hz": 5, "description": desc,
                    "races": ["human", "orc"], **(extra[0] if extra else {})})
    return out


def _root(source: str) -> str:
    return {"mine": w3paths.SCHEMES_MINE, "installed": w3paths.SCHEMES_INSTALLED}[source]


def parse_ref(ref: str) -> tuple[str, str]:
    src, _, sid = (ref or "").partition("/")
    if src not in SOURCES or not ID_RE.match(sid):
        raise SchemeError(f"方案引用应当是 来源/id（来源：{'/'.join(SOURCES)}），不是 {ref!r}")
    return src, sid


def validate(m: dict) -> dict:
    """检查说明书、补默认值。坏了抛 SchemeError（带人能看懂的原因）。"""
    if not isinstance(m, dict):
        raise SchemeError("scheme.json 不是一个 JSON 对象")
    if int(m.get("format", FORMAT)) > FORMAT:
        raise SchemeError(f"方案格式 {m.get('format')} 比这个版本的 OpenWar3 新（支持到 {FORMAT}），请更新 OpenWar3")
    sid = str(m.get("id", ""))
    if not ID_RE.match(sid):
        raise SchemeError(f"id {sid!r} 不合法：只能用小写字母、数字、-、_，2~41 个字符")
    kind = m.get("kind", "bot")
    if kind not in KINDS:
        raise SchemeError(f"kind {kind!r} 还不支持（玩家方案只能是 bot：openwar3.Bot 的子类，或 mod：openwar3.Mod 的子类）")
    entry = str(m.get("entry", ""))
    if not entry.endswith(".py") or entry.startswith(("/", "\\")) or ".." in entry.replace("\\", "/").split("/"):
        raise SchemeError(f"entry {entry!r} 应当是方案目录里的一个 .py 文件")
    out = dict(m)
    mod = kind == "mod"                  # 模组：改世界（不能公平模式）、胜负自己报（不按对战规则判）
    out.update(format=FORMAT, id=sid, kind=kind, entry=entry.replace("\\", "/"),
               name=str(m.get("name") or sid), version=str(m.get("version") or "0.0.0"),
               author=str(m.get("author") or "未署名"), description=str(m.get("description") or ""),
               fair=False if mod else bool(m.get("fair", True)), hz=float(m.get("hz", 5) or 5),
               judge=False if mod else bool(m.get("judge", True)))
    return out


def _load_dir(path: str) -> dict:
    p = os.path.join(path, SCHEME_FILE)
    try:
        with open(p, encoding="utf-8-sig") as fh:
            m = json.load(fh)
    except OSError:
        raise SchemeError(f"没有 {SCHEME_FILE}") from None
    except ValueError as exc:
        raise SchemeError(f"{SCHEME_FILE} 不是合法 JSON：{exc}") from None
    m = validate(m)
    if not os.path.isfile(os.path.join(path, m["entry"])):
        raise SchemeError(f"入口文件 {m['entry']} 不存在")
    return m


def list_schemes() -> list[dict]:
    """全部方案：内置 + 我的 + 已安装。每项带 ref / source / dir / trusted；坏掉的方案也列出来（带 error），方便人去修。"""
    out = []
    for m in _builtins():
        out.append(dict(m, ref=f"builtin/{m['id']}", source="builtin", trusted=True,
                        dir=w3paths.STRATEGY if m["kind"] == "brain" else w3paths.EXAMPLES))
    for src in ("mine", "installed"):
        root = _root(src)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d) or not ID_RE.match(name):
                continue
            item = {"ref": f"{src}/{name}", "source": src, "dir": d, "id": name,
                    "trusted": src == "mine" or os.path.exists(os.path.join(d, TRUST_FILE))}
            try:
                item.update(_load_dir(d))
                item["id"] = name                      # 目录名为准（说明书里的 id 和目录名不同时，以目录为准）
            except SchemeError as exc:
                item.update(name=name, error=str(exc), kind=item.get("kind", "bot"))
            out.append(item)
    return out


def get(ref: str) -> dict:
    parse_ref(ref)
    for s in list_schemes():
        if s["ref"] == ref:
            if s.get("error"):
                raise SchemeError(f"方案 {ref} 坏了：{s['error']}")
            return s
    raise SchemeError(f"没有方案 {ref}")


def trust(ref: str) -> None:
    src, sid = parse_ref(ref)
    if src != "installed":
        return
    d = os.path.join(_root(src), sid)
    if not os.path.isdir(d):
        raise SchemeError(f"没有方案 {ref}")
    with open(os.path.join(d, TRUST_FILE), "w", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S"))


# ── 导入 / 导出 / 复制 / 删除 ────────────────────────────────────
def _safe_members(z: zipfile.ZipFile) -> tuple[str, list]:
    """zip 里的文件：挡住绝对路径、..（zip slip）、太大太多；scheme.json 在根上或唯一的顶层目录里。返回 (前缀, 成员)。"""
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise SchemeError(f"文件太多（{len(infos)} > {MAX_FILES}）")
    if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
        raise SchemeError(f"解压后超过 {MAX_ZIP_BYTES // 1024 // 1024} MB")
    for i in infos:
        n = i.filename.replace("\\", "/")
        if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in n.split("/"):
            raise SchemeError(f"zip 里有不安全的路径：{i.filename}")
    names = [i.filename.replace("\\", "/") for i in infos]
    if SCHEME_FILE in names:
        return "", infos
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and f"{next(iter(tops))}/{SCHEME_FILE}" in names:
        return next(iter(tops)) + "/", infos
    raise SchemeError(f"zip 里找不到 {SCHEME_FILE}（要在根上，或者在唯一的顶层文件夹里）")


def install_zip(data: bytes, replace: bool = False) -> dict:
    """导入别人的方案（zip 字节）到 schemes/installed/<id>/。同 id 已存在：replace=False 报错，True 覆盖（之前的信任作废）。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SchemeError("不是 zip 文件") from None
    with z:
        prefix, infos = _safe_members(z)
        try:
            m = validate(json.loads(z.read(prefix + SCHEME_FILE).decode("utf-8-sig")))
        except ValueError as exc:
            raise SchemeError(f"{SCHEME_FILE} 不对：{exc}") from None
        dest = os.path.join(w3paths.SCHEMES_INSTALLED, m["id"])
        if os.path.exists(dest) and not replace:
            raise SchemeExists(f"已经装过 {m['id']}（{get_version(dest)}）；要覆盖请选「替换」")
        tmp = dest + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for i in infos:
            rel = i.filename.replace("\\", "/")[len(prefix):]
            if not rel or rel == TRUST_FILE:
                continue                                    # 包里自带的"已信任"标记不算数
            out = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(z.read(i))
    if not os.path.isfile(os.path.join(tmp, m["entry"])):
        shutil.rmtree(tmp, ignore_errors=True)
        raise SchemeError(f"入口文件 {m['entry']} 不在包里")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return get(f"installed/{m['id']}")


def get_version(d: str) -> str:
    try:
        with open(os.path.join(d, SCHEME_FILE), encoding="utf-8-sig") as fh:
            return str(json.load(fh).get("version", "?"))
    except (OSError, ValueError):
        return "?"


def _files_of(s: dict) -> list[tuple[str, str]]:
    """方案要打包的文件：[(包内相对路径, 本机路径)]。内置示例 = examples 下全部 .py（它们互相 import）。"""
    if s["source"] == "builtin":
        if s["kind"] == "brain":
            raise SchemeError("参考大脑是整个仓库的一部分，不能单独导出（直接分享 OpenWar3 就行）")
        return [(f, os.path.join(w3paths.EXAMPLES, f)) for f in sorted(os.listdir(w3paths.EXAMPLES)) if f.endswith(".py")]
    out = []
    for dp, dns, fns in os.walk(s["dir"]):
        dns[:] = [d for d in dns if d not in ("__pycache__", ".git")]
        for f in fns:
            if f == TRUST_FILE or f.endswith(".pyc"):
                continue
            full = os.path.join(dp, f)
            out.append((os.path.relpath(full, s["dir"]).replace("\\", "/"), full))
    return sorted(out)


def _manifest_for_export(s: dict) -> dict:
    keys = ("format", "id", "name", "version", "author", "description", "kind", "entry", "class", "fair", "judge", "hz",
            "races", "license", "homepage", "forked_from", "i18n")
    return {k: s[k] for k in keys if k in s}


def export_zip(ref: str) -> tuple[str, bytes]:
    """打包成 zip（分享 / 以后上传方案网站用同一个包）。返回 (文件名, 字节)。"""
    s = get(ref)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, full in _files_of(s):
            if rel == SCHEME_FILE:
                continue
            z.write(full, f"{s['id']}/{rel}")
        z.writestr(f"{s['id']}/{SCHEME_FILE}", json.dumps(_manifest_for_export(s), ensure_ascii=False, indent=1))
    return f"{s['id']}-{s['version']}.zip", buf.getvalue()


def fork(ref: str, new_id: str, name: str | None = None) -> dict:
    """复制一份到「我的方案」（改别人的 / 从示例起步都走这里）。原方案不动。"""
    if not ID_RE.match(new_id or ""):
        raise SchemeError(f"新 id {new_id!r} 不合法：只能用小写字母、数字、-、_，2~41 个字符")
    s = get(ref)
    dest = os.path.join(w3paths.SCHEMES_MINE, new_id)
    if os.path.exists(dest):
        raise SchemeError(f"「我的方案」里已经有 {new_id}")
    files = _files_of(s)
    os.makedirs(dest)
    for rel, full in files:
        if rel == SCHEME_FILE:
            continue
        out = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(full, out)
    m = _manifest_for_export(s)
    m.update(id=new_id, name=name or f"{s['name']}（我的副本）", author="我", version="0.1.0", forked_from=f"{ref}@{s['version']}")
    # 别的语言的名字跟着改：起了新名字就不留旧译名（远见退回显示新名字），没起就加「副本」
    m["i18n"] = {lang: {k: v for k, v in tr.items() if not (k == "name" and name)} | ({} if name or "name" not in tr else {"name": f"{tr['name']} (my copy)"})
                 for lang, tr in (m.get("i18n") or {}).items() if isinstance(tr, dict)}
    if not m["i18n"]:
        m.pop("i18n")
    with open(os.path.join(dest, SCHEME_FILE), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    return get(f"mine/{new_id}")


def delete(ref: str) -> None:
    src, sid = parse_ref(ref)
    if src == "builtin":
        raise SchemeError("内置方案不能删")
    d = os.path.join(_root(src), sid)
    if not os.path.isdir(d):
        raise SchemeError(f"没有方案 {ref}")
    shutil.rmtree(d)


# ── 运行 ──────────────────────────────────────────────────────
def load_bot_class(s: dict):
    """把方案的入口文件当模块载入，返回里面的 Bot 子类（说明书 class 指定的，或文件里自己定义的最后一个）。
    方案目录放在 sys.path 最前面：方案里的文件可以互相 import。每个方案在自己的进程里跑，不会和别的方案撞名。"""
    from .bot import Bot
    from .mod import Mod
    if s.get("kind") not in KINDS:
        raise SchemeError(f"{s['ref']} 不是 bot / mod 方案")
    d = s["dir"]
    if d not in sys.path:
        sys.path.insert(0, d)
    path = os.path.join(d, s["entry"])
    mod_name = f"ow3_scheme_{s['source']}_{s['id']}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    if s.get("class"):
        cls = getattr(mod, s["class"], None)
        if not (isinstance(cls, type) and issubclass(cls, Bot)):
            raise SchemeError(f"{s['entry']} 里没有 Bot 子类 {s['class']}")
        return cls
    own = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, Bot) and v not in (Bot, Mod)
           and v.__module__ == mod_name]
    if not own:
        raise SchemeError(f"{s['entry']} 里没有定义 Bot / Mod 的子类")
    return own[-1]


# ── 战绩 ──────────────────────────────────────────────────────
def record_result(inst_dir: str, row: dict) -> None:
    os.makedirs(inst_dir, exist_ok=True)
    with open(os.path.join(inst_dir, RESULTS_FILE), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def results(inst_dirs: list[str]) -> list[dict]:
    out = []
    for d in inst_dirs:
        try:
            with open(os.path.join(d, RESULTS_FILE), encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out


def stats(rows: list[dict]) -> dict:
    """{ref: {games, wins, losses, unknown, midGame, avgMinutes, last}}
    midGame = 半路接手的局（远见「切换（立刻生效）」时这局已经在打）：不进胜负，单独数。"""
    out: dict = {}
    for r in rows:
        s = out.setdefault(r.get("scheme", "?"), {"games": 0, "wins": 0, "losses": 0, "unknown": 0, "midGame": 0,
                                                  "minutes": 0.0, "last": 0})
        s["games"] += 1
        res = r.get("result")
        if r.get("midGame"):
            s["midGame"] += 1
        else:
            s["wins" if res == "win" else "losses" if res == "loss" else "unknown"] += 1
        s["minutes"] += float(r.get("gameMinutes") or 0)
        s["last"] = max(s["last"], float(r.get("end") or 0))
    for s in out.values():
        s["avgMinutes"] = round(s.pop("minutes") / s["games"], 1) if s["games"] else 0
    return out

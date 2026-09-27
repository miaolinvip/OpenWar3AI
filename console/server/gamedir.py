# -*- coding: utf-8 -*-
"""魔兽争霸 III 游戏目录（09-26）：由玩家自己在远见里设（首页「控制中心」的引导卡片 / 「设置」页），不再由 start.bat 猜。

    GET  /api/gamedir                 现在设的目录、版本对不对、游戏数据提取到哪了
    GET  /api/gamedir/detect          帮着找：注册表 + 各盘常见目录里的候选（带版本），玩家点一个再保存
    POST /api/gamedir/browse          弹系统的「选择文件夹」窗口（在这台电脑的桌面上），返回选的路径（不保存）
    POST /api/gamedir {path}          保存：检查 War3.exe / Game.dll、读 Game.dll 版本，写 openwar3.json 的 game_dir；
                                      游戏数据还没提取就在后台提取（data/tools/extract_game_data.py）
    POST /api/gamedir/extract         重新提取游戏数据

所有游戏相关的相对路径（Maps、default_map、下一局的地图）都相对这里设的目录（w3paths.maps_dir）。
运行时只支持 Game.dll 1.27.0.52240（1.27a，w3paths.SUPPORTED_GAME_VERSIONS）：版本不对照样能存（看得到地图），
但开局会被拦下（instances.start_farm），网页上给温馨提示：下载这个版本，更多版本即将支持，关注 War3AI.com。
"""
from __future__ import annotations

import json
import os
import string
import subprocess
import threading
import time

from . import common as C

W = C.w3paths
NOWIN = 0x08000000
_job: dict = {"running": False, "ok": None, "log": [], "startedAt": None, "endedAt": None}
_lock = threading.Lock()
on_changed: list = []            # 目录换了 / 数据提取完之后要做的事（app.py 注册：清名字表缓存、重扫地图）


def file_version(path: str) -> str | None:
    from . import services as S                    # 读文件版本号的 ctypes 实现在 services 里
    try:
        return S.file_version(path)
    except OSError:
        return None


def check(path: str | None) -> dict:
    """看一个目录能不能用：War3.exe / Game.dll 在不在、Game.dll 版本、是不是运行时支持的版本。"""
    path = (path or "").strip().strip('"').rstrip("\\/")
    out = {"path": path, "exists": False, "hasExe": False, "hasDll": False, "version": None, "supported": False,
           "ok": False, "maps": False}
    if not path:
        return out
    if os.path.isfile(path) and path.lower().endswith(".exe"):   # 选成了 War3.exe / Frozen Throne.exe 本身
        path = os.path.dirname(path)
        out["path"] = path
    out["exists"] = os.path.isdir(path)
    out["hasExe"] = any(os.path.isfile(os.path.join(path, n)) for n in ("War3.exe", "war3.exe"))
    dll = os.path.join(path, "Game.dll")
    out["hasDll"] = os.path.isfile(dll)
    out["maps"] = os.path.isdir(os.path.join(path, "Maps"))
    if out["hasDll"]:
        out["version"] = file_version(dll)
    out["ok"] = out["exists"] and out["hasExe"] and out["hasDll"]
    out["supported"] = out["ok"] and out["version"] in W.SUPPORTED_GAME_VERSIONS
    return out


def status() -> dict:
    gd = W.game_dir()
    chk = check(gd)
    units = W.UNITS_JSON
    return {
        "configured": bool(gd),
        **chk,
        "want": list(W.SUPPORTED_GAME_VERSIONS),
        "wantLabel": W.GAME_VERSION_LABEL,
        "website": W.WEBSITE,
        "mapsDir": W.maps_dir(),
        "dataReady": os.path.isfile(units),
        "dataAt": os.path.getmtime(units) if os.path.isfile(units) else None,
        "job": {k: (v[-60:] if k == "log" else v) for k, v in _job.items()},
    }


# ── 帮着找 ────────────────────────────────────────────────────────────
_RX = ("war3", "warcraft", "魔兽")


def _registry_dirs() -> list[str]:
    import winreg
    out = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Blizzard Entertainment\Warcraft III"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Warcraft III"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\Warcraft III")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("InstallPath", "InstallPathX", "Program", "ProgramX"):
                    try:
                        v, _ = winreg.QueryValueEx(k, name)
                    except OSError:
                        continue
                    if v:
                        out.append(os.path.dirname(v) if str(v).lower().endswith(".exe") else str(v))
        except OSError:
            continue
    return out


def _scan_drives(deadline: float) -> list[str]:
    """各个盘的根目录下、Games / Program Files / 游戏 这类目录下，名字带 war3 / warcraft / 魔兽 的文件夹（两层以内）。"""
    out = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if time.monotonic() > deadline or not os.path.isdir(root):
            continue
        try:
            level1 = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in level1:
            name = e.name.lower()
            if any(r in name for r in _RX):
                out.append(e.path)
            if any(r in name for r in _RX) or name in ("games", "game", "program files", "program files (x86)") or "游戏" in name:
                try:
                    out += [s.path for s in os.scandir(e.path) if s.is_dir(follow_symlinks=False) and any(r in s.name.lower() for r in _RX)]
                except OSError:
                    continue
    return out


def detect() -> dict:
    seen, rows = set(), []
    for p in _registry_dirs() + _scan_drives(time.monotonic() + 8.0):
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        c = check(p)
        if c["ok"]:
            rows.append(c)
    rows.sort(key=lambda r: (not r["supported"], r["path"].lower()))
    return {"candidates": rows}


def browse() -> dict:
    """在这台电脑的桌面上弹「选择文件夹」（远见后台就在这台电脑上跑）；窗口置顶，免得藏在浏览器后面。"""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; "
          "$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false}; "
          "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
          "$d.Description = '选择魔兽争霸 III 的安装目录（里面有 War3.exe 和 Game.dll） / Pick your Warcraft III folder'; "
          "$d.ShowNewFolderButton = $false; "
          "if ($d.ShowDialog($o) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", ps], capture_output=True, timeout=600,
                           creationflags=NOWIN)
    except (OSError, subprocess.SubprocessError) as e:
        return {"path": None, "error": str(e)}
    path = r.stdout.decode("utf-8", "replace").strip().splitlines()
    return {"path": path[-1].strip() if path else None}


# ── 保存 ──────────────────────────────────────────────────────────────
def write_config(patch: dict) -> None:
    """改 openwar3.json 的几项（其余原样保留），原子替换；改完清 w3paths 的配置缓存，远见里马上按新目录走。"""
    cur = {}
    try:
        with open(W.CONFIG, encoding="utf-8-sig") as fh:
            cur = json.load(fh)
    except FileNotFoundError:
        pass
    cur.update(patch)
    tmp = W.CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, W.CONFIG)
    W.config.cache_clear()


def _changed() -> None:
    for fn in [C.names, C.names_en, C.units, C.ability_names]:
        fn.cache_clear()
    for cb in on_changed:
        try:
            cb()
        except Exception:  # noqa: BLE001
            pass


def save(path: str) -> dict:
    c = check(path)
    if not c["exists"]:
        return {"ok": False, "code": "not-found", "check": c}
    if not c["ok"]:
        return {"ok": False, "code": "not-war3", "check": c}
    old = W.game_dir()
    write_config({"game_dir": c["path"]})
    _changed()
    started = False
    if not os.path.isfile(W.UNITS_JSON):             # 第一次设：顺手把游戏数据提取了（1~2 分钟）
        started = extract()["started"]
    return {"ok": True, "check": c, "changed": os.path.normcase(old or "") != os.path.normcase(c["path"]),
            "extracting": started, "status": status()}


# ── 提取游戏数据（后台）───────────────────────────────────────────────
def extract() -> dict:
    with _lock:
        if _job["running"]:
            return {"started": False, "note": "already"}
        if not check(W.game_dir())["ok"]:
            return {"started": False, "error": "游戏目录还没设好"}
        _job.update(running=True, ok=None, log=[], startedAt=time.time(), endedAt=None)

    def run():
        script = os.path.join(C.ROOT, "data", "tools", "extract_game_data.py")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", WAR3_GAME_DIR=W.game_dir())
        ok = False
        try:
            p = subprocess.Popen([C.PYTHON, "-u", script], cwd=C.ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, creationflags=NOWIN)
            for raw in p.stdout:
                _job["log"].append(raw.decode("utf-8", "replace").rstrip())
                del _job["log"][:-400]
            ok = p.wait() == 0 and os.path.isfile(W.UNITS_JSON)
        except Exception as e:  # noqa: BLE001
            _job["log"].append(f"出错：{type(e).__name__}: {e}")
        finally:
            _job.update(running=False, ok=ok, endedAt=time.time())
            if ok:
                _changed()

    threading.Thread(target=run, name="extract-game-data", daemon=True).start()
    return {"started": True}


def version_problem() -> str | None:
    """开局前的检查（instances.start_farm 调）：目录没设 / 不对 / 版本不支持就回一句话（网页 toast 出来），好的回 None。"""
    c = check(W.game_dir())
    if not c["path"]:
        return "还没设置魔兽争霸 III 的目录：到远见首页「控制中心」设置"
    if not c["ok"]:
        return f"游戏目录不对（{c['path']} 里没有 War3.exe / Game.dll）：到远见「控制中心」重新设置"
    if not c["supported"]:
        return (f"游戏版本是 {c['version']}，现在只支持 {W.GAME_VERSION_LABEL}：请下载这个版本的魔兽争霸 III（冰封王座）。"
                f"我们即将支持更多版本，请关注 War3AI.com")
    return None

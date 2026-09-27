# -*- coding: utf-8 -*-
"""OpenWar3 的唯一路径中心。

旧仓库里同一个路径（build_intel4、war3-modules、D:\\Games\\war3……）散在 80 多处，
换一台机器要改 80 多处。这里是唯一一份：代码只问这个模块，本机差异只写在
仓库根的 ``openwar3.json``（从 ``openwar3.example.json`` 复制一份改）。

入口脚本的开头统一三行（找到仓库根 -> 把 sdk/python 放上 sys.path -> setup）：

    import os, sys
    _r = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isfile(os.path.join(_r, "openwar3.root")): _r = os.path.dirname(_r)
    sys.path.insert(0, os.path.join(_r, "sdk", "python")); import w3paths; w3paths.setup()

库模块（被入口 import 的）直接 ``import w3paths`` 即可。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from functools import lru_cache


def _find_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    while True:
        if os.path.isfile(os.path.join(cur, "openwar3.root")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            raise RuntimeError(f"找不到 openwar3.root（从 {here} 往上找）")
        cur = parent


ROOT = _find_root()

# ── 代码目录 ───────────────────────────────────────────────────────
SDK = os.path.join(ROOT, "sdk", "python")                     # 传输层 + 游戏接口 + 仲裁
BRAINS = os.path.join(ROOT, "brains")
XWAR3 = os.path.join(BRAINS, "xwar3")                          # 参考大脑（worldmodel 包的父目录）
STRATEGY = os.path.join(XWAR3, "strategy")                     # 策略层（brain 包 + run_brain.py）
REFLEX = os.path.join(XWAR3, "reflex")                         # 毫秒层（mod_combat 等）
CONSOLE = os.path.join(ROOT, "console")                        # Farsight 网页指挥台
DIRECTOR = os.path.join(ROOT, "director")                      # 自动运镜 / 血条
SPEECH = os.path.join(ROOT, "speech")                          # 头顶气泡 + 本地大模型
RUNTIME = os.path.join(ROOT, "runtime")                        # farm.py（多实例编排）、实例种子
NATIVE = os.path.join(ROOT, "native")
EXAMPLES = os.path.join(BRAINS, "examples")                    # 示例 bot（内置方案）
# AI 方案库（09-24）：mine = 我自己的（可改）、installed = 从别人那导入的（只读，要改先「复制到我的」）。都不进 git
SCHEMES = os.path.join(ROOT, "schemes")
SCHEMES_MINE = os.path.join(SCHEMES, "mine")
SCHEMES_INSTALLED = os.path.join(SCHEMES, "installed")

# ── 产物与运行期目录（.gitignore 排除）────────────────────────────
# BIN 相当于旧仓库的 war3-button-hook-lab\build_intel4：DLL/exe 和 render_hook.log 在这里，
# 每个实例的数据在 BIN\inst<N>\，farm 的输出在 BIN\farm\。DLL 把日志写在自己旁边，所以三者不拆开。
BIN = os.path.join(ROOT, "bin")
FARM_DIR = os.path.join(BIN, "farm")
HOOK_LOG = os.path.join(BIN, "render_hook.log")
CLI_EXE = os.path.join(BIN, "war3_ui_command.exe")
LAUNCH_EXE = os.path.join(BIN, "war3_launch.exe")
SEED = os.path.join(RUNTIME, "seed")
# start.bat / setup.ps1 在没装过环境的机器上自己下的私有运行环境（Python；改了网页源码 / 起官网预览时才有 Node.js），
# 不写注册表、不改 PATH；删掉 bin\ 以后再双击 start.bat 会重新下。
ENV_DIR = os.path.join(BIN, "env")
# 随仓库发的注入 DLL / 启动器二进制（运行时源码 native/ 不开源时，setup.ps1 从这里同步到 BIN）
PREBUILT = os.path.join(ROOT, "prebuilt")

# ── 数据 ──────────────────────────────────────────────────────────
DATA = os.path.join(ROOT, "data")
GAME_DATA = os.path.join(DATA, "game")        # 从本机游戏 MPQ 提取的暴雪数据（不随开源发布）
UNITS_JSON = os.path.join(GAME_DATA, "units.json")
NAMES_JSON = os.path.join(GAME_DATA, "names.json")
ORDER_IDS = os.path.join(DATA, "order-ids.txt")


def inst_dir(n: int | str) -> str:
    return os.path.join(BIN, f"inst{n}")


def game_data(name: str) -> str:
    return os.path.join(GAME_DATA, name)


# ── 本机配置 ──────────────────────────────────────────────────────
CONFIG = os.path.join(ROOT, "openwar3.json")
CONFIG_EXAMPLE = os.path.join(ROOT, "openwar3.example.json")


@lru_cache(maxsize=1)
def config() -> dict:
    """openwar3.json 覆盖 openwar3.example.json（只需写改过的项）。"""
    out: dict = {}
    for p in (CONFIG_EXAMPLE, CONFIG):
        try:
            with open(p, encoding="utf-8-sig") as fh:
                out.update({k: v for k, v in json.load(fh).items() if not k.startswith("_")})
        except FileNotFoundError:
            pass
    return out


def game_dir() -> str:
    """玩家设置的魔兽争霸 III 目录（远见「控制中心 / 设置」里设，存 openwar3.json 的 game_dir）；没设 = 空字符串。
    其余游戏相关的相对路径（Maps、default_map）都相对它。"""
    return config().get("game_dir") or ""


# 运行时（注入 DLL）支持的游戏版本：Game.dll 的文件版本。现在只有 1.27a；以后支持更多版本在这里加（War3AI.com 公告）
SUPPORTED_GAME_VERSIONS = ("1.27.0.52240",)
GAME_VERSION_LABEL = "1.27a（1.27.0.52240）"
WEBSITE = "https://war3ai.com"


def maps_dir() -> str:
    """<游戏目录>\\Maps（openwar3.json 另写了 maps_dir 才用它）；游戏目录还没设 = 空字符串。"""
    if config().get("maps_dir"):
        return config()["maps_dir"]
    gd = game_dir()
    return os.path.join(gd, "Maps") if gd else ""


def default_map() -> str:
    m = config().get("default_map", r"FrozenThrone\(2)EchoIsles.w3x")
    return m if os.path.isabs(m) else os.path.join(maps_dir(), m)


def python_exe() -> str:
    return config().get("python") or sys.executable


def powershell_exe() -> str:
    """Windows 自带的 PowerShell 5.1（每台 Windows 10 / 11 都有，不用装）：后台起进程（w3spawn）、选文件夹对话框用它。
    09-26 起整个系统不再需要 PowerShell 7（farm 改成了 Python）。"""
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(p):
        return p
    return shutil.which("powershell") or shutil.which("pwsh") or p


def node_exe() -> str | None:
    """Node.js（只有编网页、起官网预览时要）：openwar3.json 的 node -> PATH -> setup.ps1 下的私有那份；都没有 = None。"""
    p = config().get("node")
    if p and os.path.isfile(p):
        return p
    private = os.path.join(ENV_DIR, "node", "node.exe")
    return shutil.which("node") or (private if os.path.isfile(private) else None)


def port(name: str, default: int) -> int:
    return int(config().get("ports", {}).get(name, default))


def llm_base() -> str:
    return config().get("llm_base") or "http://127.0.0.1:1234/v1"


# 气泡 / 玩伴的本地大模型默认用哪个（openwar3.json 的 llm_model；speech/speak_api.json 里网页改过的优先）。
# 默认值只是个常见的小模型名：各人在 LM Studio 里下了什么就在远见「头顶气泡」页 / openwar3.json 里改成什么
DEFAULT_LLM_MODEL = "qwen3-8b"


def llm_model() -> str:
    return config().get("llm_model") or DEFAULT_LLM_MODEL


# ── sys.path ──────────────────────────────────────────────────────
# 旧代码用扁平模块名互相 import（import w3state / from brain import ... / from worldmodel import ...），
# 这里一次把它们所在的目录都放上去，代码本身不用改成包。
PY_PATHS = (SDK, REFLEX, STRATEGY, XWAR3, DIRECTOR, SPEECH)


def setup() -> None:
    for p in PY_PATHS:
        if p not in sys.path:
            sys.path.append(p)
    # 子进程（模块、worker、导播）会通过 PYTHONPATH 继承同一组路径
    cur = os.environ.get("PYTHONPATH", "")
    parts = [p for p in cur.split(os.pathsep) if p]
    for p in PY_PATHS:
        if p not in parts:
            parts.append(p)
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)
    os.environ.setdefault("OPENWAR3_ROOT", ROOT)


if __name__ == "__main__":
    print(json.dumps({"ROOT": ROOT, "BIN": BIN, "GAME_DATA": GAME_DATA, "game_dir": game_dir(),
                      "default_map": default_map(), "python": python_exe(), "powershell": powershell_exe(),
                      "config": config()}, ensure_ascii=False, indent=2))

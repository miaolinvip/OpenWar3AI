"""原生全屏启动器副本。仅支持经过核对的发布版，不改动发布文件或游戏文件。"""
from __future__ import annotations

import hashlib
from pathlib import Path
import os

# native 源码未随仓库发布；此版本的两个 CreateProcess 命令模板固定带 -window。
# 仅改等长的 UTF-16 字符串，保持 PE 布局、注入逻辑和守护进程行为不变。
LAUNCH_SHA256 = "c86eb9239355a097a53a1da5b21243f64d956d338f68aeb639b54485369a716b"
COMMANDS = ('"%s" -window -loadfile "%s"', '"%s" -window')


def fullscreen_image(original: bytes) -> bytes:
    if hashlib.sha256(original).hexdigest() != LAUNCH_SHA256:
        raise RuntimeError("原生全屏不支持此启动器版本；需要适配新版 war3_launch.exe 的全屏入口")
    result = original
    for command in COMMANDS:
        old = (command + "\0").encode("utf-16le")
        new = (command.replace("-window", "       ") + "\0").encode("utf-16le")
        if result.count(old) != 1:
            raise RuntimeError("原生全屏启动器命令模板校验失败")
        result = result.replace(old, new)
    return result


def _install(path: Path, data: bytes) -> None:
    if path.is_file() and path.read_bytes() == data:
        return                       # 另一局可能正在使用同一副本，不覆盖运行中的 exe/DLL
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fullscreen_launcher(source: str) -> str:
    source = Path(source)
    image = fullscreen_image(source.read_bytes())  # 先校验，再生成任何文件
    target = source.parent / "human-fullscreen" / "war3_launch.exe"
    # 启动器按自己的目录找 Runtime DLL，副本保持原 DLL 的全部字节。
    _install(target.parent / "render_hook.dll", (source.parent / "render_hook.dll").read_bytes())
    _install(target, image)
    return str(target)

# -*- coding: utf-8 -*-
"""远见网页的源码指纹（09-26）：判断 console/web/dist（编好的网页）是不是这份源码编出来的。

编好的 dist/ 随仓库发：普通用户不用装 Node.js、不用下 90 MB 的 node_modules。只有改了网页源码才要重编 ——
start.bat（setup.ps1）用这里的 --check 判断：对得上就直接用，对不上才去找 / 下 Node.js 编。

指纹 = 源码文件（src/、public/ 和几个顶层配置）逐个「相对路径 + 内容的 SHA-256」再整体 SHA-256；
内容先把 CRLF 换成 LF（git 在 Windows 上检出可能把换行改成 CRLF，不能因为这个就当成源码改过）。
编完由 console/web/scripts/stamp.mjs 写进 dist/.source-hash —— ⚠ 两边是同一个算法，改一处要改两处
（console/tests/test_webstamp.py 在有 Node.js 的机器上核对两边算得一样）。

    python console/server/webstamp.py --check     对得上退出码 0；否则 1，并打印原因
    python console/server/webstamp.py --print     打印当前源码的指纹
    python console/server/webstamp.py --write     把当前源码的指纹写进 dist/.source-hash（只在确认 dist 是这份源码编的时用）
只用标准库：setup.ps1 在装 Python 包之前也能跑它。
"""
from __future__ import annotations

import hashlib
import os
import sys

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
TOP_FILES = ("index.html", "live.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")
SRC_DIRS = ("src", "public")
STAMP = os.path.join("dist", ".source-hash")


def source_files(web: str = WEB) -> list[str]:
    """参与指纹的文件，相对 web 的路径（正斜杠），排好序。"""
    rels = [f for f in TOP_FILES if os.path.isfile(os.path.join(web, f))]
    for d in SRC_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(web, d)):
            dirnames.sort()
            for fn in filenames:
                rels.append(os.path.relpath(os.path.join(dirpath, fn), web).replace(os.sep, "/"))
    return sorted(rels)


def fingerprint(web: str = WEB) -> str:
    h = hashlib.sha256()
    for rel in source_files(web):
        with open(os.path.join(web, rel), "rb") as fh:
            body = fh.read().replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(body).hexdigest().encode("ascii") + b"\n")
    return h.hexdigest()


def stamped(web: str = WEB) -> str | None:
    try:
        with open(os.path.join(web, STAMP), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def check(web: str = WEB) -> dict:
    """state：ok = dist 就是这份源码编的；stale = 源码改过；missing = 没有 dist；unstamped = 有 dist 但没有指纹（老版本编的）。"""
    if not os.path.isfile(os.path.join(web, "dist", "index.html")):
        return {"state": "missing", "source": None, "built": None}
    src, built = fingerprint(web), stamped(web)
    state = "unstamped" if built is None else "ok" if built == src else "stale"
    return {"state": state, "source": src, "built": built}


def write(web: str = WEB) -> str:
    fp = fingerprint(web)
    with open(os.path.join(web, STAMP), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(fp + "\n")
    return fp


def main(argv: list[str]) -> int:
    if "--print" in argv:
        print(fingerprint())
        return 0
    if "--write" in argv:
        print(write())
        return 0
    st = check()
    reason = {"ok": "网页已编译，和源码一致", "stale": "网页源码改过，要重编", "missing": "还没有编好的网页",
              "unstamped": "编好的网页没有源码指纹（老版本编的），要重编"}[st["state"]]
    print(reason)
    return 0 if st["state"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

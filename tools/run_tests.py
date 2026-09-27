# -*- coding: utf-8 -*-
"""OpenWar3 全部 Python 测试的统一入口。

    python tools/run_tests.py            # 全部
    python tools/run_tests.py reflex     # 只跑名字里带 reflex 的那几套

每一套在**独立子进程**里跑（各套测试都往 sys.path / sys.modules 里塞东西，放一个进程里会互相污染），
子进程开头先 w3paths.setup()，所以单独拿出来跑也是同一个环境。
退出码：任何一套失败就非 0 —— 别用 `| tail && git commit`，失败也会提交。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = [
    # (名字, 在哪个目录跑 discover, tests 目录)
    ("sdk", "sdk/python", "tests"),
    ("strategy", "brains/xwar3/strategy", "tests"),
    ("reflex", "brains/xwar3/reflex", "tests"),
    ("console", "console", "tests"),
    ("speech", "speech", "tests"),
    ("examples", "brains/examples", "tests"),
    ("gateway", "gateway", "tests"),
    ("server", "server/feedback", "tests"),      # war3ai.com 的反馈服务（签名、限流、重放、格式）
]

BOOT = (
    "import sys, os; sys.path.insert(0, os.path.join({root!r}, 'sdk', 'python')); "
    "import w3paths; w3paths.setup(); sys.path.insert(0, os.getcwd()); "
    "import unittest; "
    "r = unittest.main(module=None, argv=['x', 'discover', '-s', {tests!r}], exit=False); "
    "sys.exit(0 if r.result.wasSuccessful() else 1)"
)


def main() -> int:
    want = sys.argv[1:]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    bad = []
    ran = 0
    for name, cwd, tests in SUITES:
        if want and not any(w in name for w in want):
            continue
        d = os.path.join(ROOT, cwd)
        if not os.path.isdir(os.path.join(d, tests)):
            continue
        ran += 1
        t0 = time.time()
        p = subprocess.run([sys.executable, "-c", BOOT.format(root=ROOT, tests=tests)], cwd=d, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = [ln for ln in p.stderr.splitlines() if ln.startswith(("Ran ", "OK", "FAILED"))]
        print(f"[{'✔' if p.returncode == 0 else '✘'}] {name:9s} {' '.join(tail)}  ({time.time() - t0:.0f}s)", flush=True)
        if p.returncode != 0:
            bad.append(name)
            fails = [ln for ln in p.stderr.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
            for ln in fails[:30]:
                print("      " + ln)
    if not ran:
        # 名字打错时别一声不吭地「通过」
        print(f"没有匹配的测试套：{' '.join(want)}（可选：{', '.join(s[0] for s in SUITES)}）")
        return 2
    print("全部通过" if not bad else f"失败：{', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

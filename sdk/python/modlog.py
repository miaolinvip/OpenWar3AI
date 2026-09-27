# -*- coding: utf-8 -*-
"""毫秒层模块的永久日志：<模块名>.log，每行 "YYYY-MM-DD HH:MM:SS 文本"。

09-16 查实的两件事（底座账本 log-line-loss / log-module-hist）：

* 模块 note() 往 ai_run.log 追加 —— 可对局进行中 run-ai.ps1 的 Tee-Object 独占着它，
  open("a") 当场 Permission denied、被 except OSError 吞掉。只有局间那几秒写得进去：
  ai_run.log 里「[保命]」415 行，mod_combat.hist 30310 行。
* .out 是 Start-Process 的重定向，每次拉起模块从零覆盖；run-ai 把旧 .out 拼进 .hist 时
  日期取**文件修改时间**，跨午夜的那份 .out 前半夜的行会被标成第二天。

这里的做法：模块 main() 开头 tee_stdout(名字)，stdout 照常写（.out、status.py 不变），
每一整行同时加上**写入那一刻的日期**追加进自己的 .log —— 一个模块一份，不和任何人抢锁，
从不覆盖。读的一侧只用 read()：先 .log；.hist/.out 只取 .log 开始之前的行（旧数据，不重复）。

⚠ run-ai.ps1 的模块重载依赖名单（w3state/w3cmd/w3claim/w3lease）里没有本文件：
  只改这里不会触发局间重载，要么同时碰一下模块源文件，要么 restart.py。
"""
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


class DayTee:
    """包一层 stdout：原样写出去，整行再加日期追加进 path。半行先攒着等换行。"""

    def __init__(self, inner, path, clock=datetime.now):
        self.inner = inner
        self.path = path
        self.clock = clock
        self.buf = ""

    def write(self, s):
        n = self.inner.write(s)
        self.buf += s
        if "\n" in self.buf:
            *lines, self.buf = self.buf.split("\n")
            rows = [ln.rstrip("\r") for ln in lines if ln.strip()]
            if rows:
                day = self.clock().strftime("%Y-%m-%d")
                try:
                    with open(self.path, "a", encoding="utf-8") as fh:
                        fh.write("".join(f"{day} {ln}\n" for ln in rows))
                except OSError:
                    pass
        return n

    def flush(self):
        self.inner.flush()

    def __getattr__(self, key):
        return getattr(self.inner, key)


def default_base():
    """★ 09-16 多开：模块日志写**自己实例的目录**（WAR3_INST_DIR），没设才写 war3-modules/。

    多开之前全机只有一个实例，<模块>.log 放在 war3-modules/ 没问题；四个实例一起跑之后，
    四份 mod_combat 的心跳/拉残血、四份 cmdlog 的下令记录全追加进**同一个文件、不带实例号**，
    match_review 按墙钟对齐时把四个实例的数加在一起（21:30 起 inst2 复盘「施法 79、矿超员采样 118」就是这么来的）。
    实例目录里原本就有 run-ai 按实例重定向的 .out/.hist（21:30 之前 inst1.hist 心跳 83 条 = 共用 .log 83 条），
    所以读的一侧 base 指向实例目录就能拿到干净的全程：.log 开始之前由同目录的 .hist/.out 补上。
    """
    d = os.environ.get("WAR3_INST_DIR", "").strip()
    return d if d and os.path.isdir(d) else HERE


def base_for(match_dir):
    """对局目录（…\\inst2\\matches）-> 这个实例的模块日志目录；老的单实例目录（build_intel4\\matches）-> war3-modules/。

    判据是上一级目录名 inst<N>，不看里面有没有文件 —— 刚起的实例还没写日志时也不该退回共用目录（那里是别人的数）。
    """
    parent = os.path.dirname(os.path.abspath(match_dir))
    name = os.path.basename(parent).lower()
    return parent if name.startswith("inst") and name[4:].isdigit() else HERE


def tee_stdout(name, base=None):
    """模块 main() 开头调一次；重复调不会套两层。顺手记住模块名（见 current_name）。"""
    global _current
    _current = name
    base = base or default_base()
    if not isinstance(sys.stdout, DayTee):
        sys.stdout = DayTee(sys.stdout, os.path.join(base, name + ".log"))
    return sys.stdout


def _dated(ln):
    try:
        return datetime.strptime(ln[:19], "%Y-%m-%d %H:%M:%S"), ln[20:].rstrip("\r\n")
    except ValueError:
        return None


def read(name, ref=None, base=None):
    """[(datetime, 文本)]，按文件先后：旧的 .hist、.out，再 .log。

    .out 只有时分秒：给了 ref（对局开始时刻）就按 ref 那天算、比 ref 早 12 小时以上的挪到第二天
    （match_review 原来的做法）；没给就按 .out 的修改日期（kpi 原来的做法）。
    base 不给 = default_base()（本进程的实例目录）；复盘别的实例要显式传 base_for(对局目录)。
    """
    base = base or default_base()
    rows, since = [], None
    logp = os.path.join(base, name + ".log")
    if os.path.exists(logp):
        with open(logp, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                p = _dated(ln)
                if p:
                    rows.append(p)
                    since = p[0] if since is None else since
    legacy = []
    histp = os.path.join(base, name + ".hist")
    if os.path.exists(histp):
        with open(histp, encoding="utf-8", errors="replace") as fh:
            legacy.extend(p for p in map(_dated, fh) if p)
    outp = os.path.join(base, name + ".out")
    if os.path.exists(outp):
        anchor = ref or datetime.fromtimestamp(os.path.getmtime(outp))
        with open(outp, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    hh = datetime.strptime(ln[:8], "%H:%M:%S")
                except ValueError:
                    continue
                at = anchor.replace(hour=hh.hour, minute=hh.minute, second=hh.second, microsecond=0)
                if ref is not None and at < ref - timedelta(hours=12):
                    at += timedelta(days=1)
                legacy.append((at, ln[9:].rstrip("\r\n")))
    if since is not None:
        legacy = [r for r in legacy if r[0] < since]
    return legacy + rows


# ── 「这个进程是哪个模块」──────────────────────────────────────────
# 命令来源日志要在每条令上标出是谁下的，而下令的口子在 w3cmd.Cmd 里，
# 它是 w3state.Session 构造的、根本不知道模块名。模块名唯一确定的地方就是
# main() 开头那句 tee_stdout("mod_worker")，所以让它记下来、下令出口来问 ——
# 这样**一个调用点都不用改**（三个模块各几十处下令）。
_current = None


def current_name():
    """tee_stdout 登记过的模块名；没登记（比如单测、drill）返回 None。"""
    return _current


def append(name, text, base=None, clock=datetime.now):
    """追加一行到 <name>.log，格式和 DayTee 一致（日期 + 文本）。写不进去返回 False。

    给**不经过 stdout** 的记录用（命令来源日志 cmdlog）：它每秒几十行，
    混进模块自己的 .log 会把心跳冲没，所以单独一个文件。
    ⚠ 和 DayTee 一样只追加、不抢锁 —— ai_run.log 被 Tee-Object 独占那次就是这么丢行的。
    """
    try:
        with open(os.path.join(base or default_base(), name + ".log"), "a", encoding="utf-8") as fh:
            fh.write(f"{clock().strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        return True
    except OSError:
        return False

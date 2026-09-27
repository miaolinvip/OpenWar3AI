# -*- coding: utf-8 -*-
"""每个实例一个导播（无网页，runtime/farm.py 或远见按实例拉起）。

    python director_inst.py --inst 2

09-16 发现：多开时只有一个实例有导播，其余实例的镜头一直不切换。每个游戏进程都要有自己的导播控制镜头。

原来导播只有一份，挂在 serve.py 里（网页镜头台调它），camctl 没设 WAR3_PID 时固定跟 1 号实例 ——
2 号以后的实例镜头从开局停在出生点，一整局不动。

这里做的事：
* WAR3_INST=N：camctl 每 5 秒按进程名 War3-<N>.exe 重新找 pid（跑批每局都重起游戏，pid 不能钉死）；
* WAR3_INST_DIR：倍速读这个实例自己的 speed.txt；
* 导播存盘和每局报告写进实例目录（inst<N>/director-state.json、director-stats.jsonl），
  第一次从全局 director-state.json 抄参数 —— 不去改 1 号实例在网页上调好的那份；
* 头顶血条的看门线程也起一份（开关活在每个游戏进程的 DLL 里，每个实例都要各自下发）；
* 同一个实例只允许一个导播（锁文件记 pid，旧进程还活着就退出），两份导播抢一个镜头会一秒一跳。

⚠ 1 号实例的导播仍然是 serve.py 里那一份（网页镜头台直接调它），farm 不给 1 号起这个。
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_r = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_r, "openwar3.root")):
    _r = os.path.dirname(_r) if os.path.dirname(_r) != _r else sys.exit("找不到 openwar3.root")
sys.path.insert(0, os.path.join(_r, "sdk", "python"))
import w3paths  # noqa: E402  OpenWar3 唯一路径中心
w3paths.setup()
BUILD = w3paths.BIN


def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    except Exception:                                   # noqa: BLE001
        return False


def take_lock(path, me=None):
    """锁文件里是别的活进程 -> False；否则写自己的 pid -> True。"""
    me = os.getpid() if me is None else me
    try:
        with open(path, encoding='utf-8') as fh:
            other = int((fh.read() or '0').strip() or 0)
    except (OSError, ValueError):
        other = 0
    if other and other != me and pid_alive(other):
        return False
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(str(me))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inst', type=int, required=True)
    args = ap.parse_args()
    inst_dir = os.path.join(BUILD, 'inst%d' % args.inst)
    os.makedirs(inst_dir, exist_ok=True)
    os.environ['WAR3_INST'] = str(args.inst)
    os.environ['WAR3_INST_DIR'] = inst_dir
    os.environ.pop('WAR3_PID', None)          # 钉实例号，不钉 pid（见文件头）

    if not take_lock(os.path.join(inst_dir, 'director.lock')):
        print('inst%d 已经有导播在跑 —— 本进程退出' % args.inst, flush=True)
        return 0

    sys.path.insert(0, HERE)
    import camctl                              # noqa: E402
    import director                            # noqa: E402

    state = os.path.join(inst_dir, 'director-state.json')
    if not os.path.exists(state) and os.path.exists(director.STATE_FILE):
        shutil.copyfile(director.STATE_FILE, state)
    director.STATE_FILE = state
    d = director.DIRECTOR
    d.load()
    d.start()
    try:
        camctl.bars_autostart()
    except Exception as exc:                   # noqa: BLE001
        print('头顶血条起不来：%s' % exc, flush=True)
    print('inst%d 导播已待命（pid %d）' % (args.inst, os.getpid()), flush=True)

    last = ''
    ops_path = os.path.join(inst_dir, 'ops.json')
    enabled = True
    next_print = time.monotonic() + 30
    while True:
        time.sleep(3)
        # 09-18 Farsight 设置页「对局运行 → 自动导播」开关（war3-brain/brain/ops.py 的 director_enabled，默认开）
        want = True
        try:
            with open(ops_path, encoding='utf-8-sig') as fh:
                want = bool(json.load(fh).get('director_enabled', True))
        except (OSError, ValueError, AttributeError):
            want = True
        if want != enabled:
            enabled = want
            if enabled:
                d.start()
            else:
                d.stop()
            print(time.strftime('%H:%M:%S ') + ('导播开关打开，恢复运镜' if enabled else '导播开关关了，停止运镜（进程留着待命）'), flush=True)
        if enabled and not d.running():        # 线程意外退出：拉起来
            d.start()
        # 每轮把运行状态写出去（Farsight 的导播台读它 —— 控制台里那个 DIRECTOR 对象不是这个进程的，
        # 页面原来显示「已停止」其实是在看控制台自己那份，09-18 发现游戏里的导播明明一直在运镜）
        try:
            st = d.status()
            live = {"pid": os.getpid(), "wall": time.time(), "running": d.running(), "enabled": enabled,
                    "mode": st.get("mode"), "note": st.get("note"), "shot": st.get("shot"), "heldFor": st.get("heldFor"),
                    "orbiting": st.get("orbiting"), "stats": st.get("stats"), "camPid": camctl.CTL.pid,
                    # 09-18：参数、"哪些是人调的"、计数器、账本进度也发出去 ——
                    # 网页上看不见的东西就调不动，也没法判断某条规则到底有没有生效
                    "config": st.get("config"), "pinned": st.get("pinned"),
                    "counts": st.get("counts"), "why": st.get("why"),
                    "ledger": st.get("ledger"), "live": st.get("live"),
                    "lastTickS": st.get("lastTickS"),
                    "size": st.get("size"), "sizeWhy": st.get("sizeWhy"),
                    "curDistance": st.get("curDistance")}
            tmp = os.path.join(inst_dir, 'director-live.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(live, fh, ensure_ascii=False)
            os.replace(tmp, os.path.join(inst_dir, 'director-live.json'))
        except Exception as exc:                # noqa: BLE001
            print('写导播状态出错：%s' % exc, flush=True)
        if time.monotonic() < next_print:
            continue
        next_print = time.monotonic() + 30
        s = d.status()
        line = 'pid=%s %s 切%d 滑%d%s' % (camctl.CTL.pid, s['note'], s['stats']['cuts'], s['stats']['glides'], '' if enabled else '（开关关着）')
        if line != last:
            print(time.strftime('%H:%M:%S ') + line, flush=True)
            last = line


if __name__ == '__main__':
    sys.exit(main() or 0)

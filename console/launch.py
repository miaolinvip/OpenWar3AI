# -*- coding: utf-8 -*-
"""一键启动远见（Farsight）—— 仓库根目录的 start.bat 部署检查完之后调的就是它。

    python console/launch.py              起网页后台（已经在跑就不重起），打开浏览器（首页「控制中心」）
    python console/launch.py 5            顺带开始 5 号实例的测试（游戏 + 新大脑，打完按下一局设置自动重开）
    python console/launch.py 5 6 7        开始好几个，依次起、间隔 4 秒
    python console/launch.py --restart    后台已经在跑也先停掉再起（改了 console/server 的代码之后用）
    python console/launch.py --stop       彻底停止：游戏实例、AI、网关、气泡、官网预览、本系统用的模型，最后远见后台（start.bat stop / stop.bat）
    python console/launch.py 5 6 --director 5   5 号带导演台（自动运镜 + 头顶血条），开关记住，以后开始测试 5 号都带
    python console/launch.py --no-browser --hours 12

之后什么时候开始/停止测试、停哪个实例、全部停止，都在网页「实例与开局」页点；网关、气泡、本地大模型、官网预览在首页「控制中心」起停。
端口上已经有程序在听时先问它 /api/version：是这份仓库的远见才算「已经在跑」——旧仓库（xwar3）的远见也默认 8866，
以前直接当成在跑，打开的是旧版。
起测试走网页后台同一个接口（POST /api/tests/start），和网页上点「开始测试」是同一条路。

后台用 WMI 起（instances.spawn_detached）：父进程不是这个窗口，关掉 bat 的黑框后台照跑；
也不挂在任何终端的进程树 / Job 里（长期进程别从后台任务管理器拉起，那边结束任务会连整棵进程树一起杀）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from server import common as C  # noqa: E402
from server import instances as I  # noqa: E402

PORT = C.w3paths.port("console", 8866)
URL = f"http://127.0.0.1:{PORT}"


def set_port(port: int) -> None:
    global PORT, URL
    PORT, URL = port, f"http://127.0.0.1:{port}"


def say(msg: str) -> None:
    print(msg, flush=True)


def http(method: str, path: str, body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def server_up() -> bool:
    """端口在监听就算起来了。⚠ 不按某个接口多快回来判：刚启动那半分钟后台在预热（地图预览、对局列表），
    请求时快时慢，09-19 按 3 秒超时探 HTTP，连着 60 秒判「没起来」，其实早就在跑了。"""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except OSError:
        return False


def port_owner() -> str:
    """端口上在听的是谁：ours（这份仓库的远见）/ other-repo（别的仓库的远见，带着它的路径）/ foreign（别的程序，或旧版远见没有这个接口）。
    刚起来那半分钟后台在预热，接口可能慢，多等几次。"""
    for _ in range(6):
        try:
            v = http("GET", "/api/version", timeout=5)
        except urllib.error.HTTPError:
            return "foreign"                     # 404：旧版远见 / 别的网页程序
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(1.0)
            continue
        if not isinstance(v, dict) or v.get("app") != "openwar3-farsight":
            return "foreign"
        same = os.path.normcase(os.path.normpath(str(v.get("root") or ""))) == os.path.normcase(os.path.normpath(C.ROOT))
        return "ours" if same else f"other-repo:{v.get('root')}"
    return "foreign"


def server_procs() -> list[dict]:
    """正在跑的远见后台：farsight/run.py 和它的实例 worker（farsight/server/worker.py）。"""
    run_py = os.path.join(HERE, "run.py").lower()
    worker_py = os.path.join(HERE, "server", "worker.py").lower()
    return [p for p in I._procs() if p["name"].lower().startswith("python")
            and any(a.lower() in (run_py, worker_py) for a in p["args"])]


def stop_server() -> int:
    procs = server_procs()
    for p in procs:
        I.kill_tree(p["pid"])
    return len(procs)


def log_path() -> str:
    """默认端口写 console/cache/server.out；别的端口各写各的 server-<端口>.out ——
    ⚠ 09-26 实测：8866 的后台开着 server.out，8876 的再往同一个文件重定向，cmd 打不开文件，python 根本没起来。"""
    default = C.w3paths.port("console", 8866)
    return os.path.join(HERE, "cache", "server.out" if PORT == default else f"server-{PORT}.out")


def start_server() -> dict:
    out = log_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = (f'cmd /c "set PYTHONIOENCODING=utf-8&& "{C.PYTHON}" -u "{os.path.join(HERE, "run.py")}" --port {PORT} '
           f'> "{out}" 2>&1"')
    return I.spawn_detached(cmd, C.ROOT)


def stop_everything(keep_llm: bool) -> int:
    """彻底停止（start.bat stop / stop.bat）：实例 → 网关 / 气泡 / 官网预览 → 漏网组件 → 本系统用的模型 → 远见后台。
    在这个进程里直接做（不经过网页后台的接口）：后台卡死、端口被占时也停得掉。"""
    from server import services as SVC
    say("彻底停止 OpenWar3 …")
    r = SVC.stop_everything(llm=not keep_llm)
    inst = r["instances"]["stopped"]
    say(f"  ✔ 游戏实例（游戏、farm、AI、导播）：{'停了 ' + '、'.join(f'{n} 号' for n in inst) if inst else '没有在跑的'}")
    names = {"gateway": "网关", "speech": "头顶气泡", "website": "官网预览"}
    ran = [names[k] for k, v in r["services"].items() if v]
    say(f"  ✔ 服务：{'停了 ' + '、'.join(ran) if ran else '没有在跑的'}")
    if r["leftovers"]:
        say(f"  ✔ 另外收掉 {len(r['leftovers'])} 个漏网的组件进程")
    llm = r.get("llm")
    if keep_llm:
        say("  · 本地大模型：保留（--keep-llm）")
    elif llm and llm.get("unloaded"):
        say(f"  ✔ 本地大模型：已卸载 {', '.join(llm['unloaded'])}（LM Studio 程序本身没关）")
    elif llm and not llm.get("ok"):
        say(f"  ✘ 本地大模型没卸成：{llm.get('error')}")
    n = stop_server()
    say(f"  ✔ 远见后台：{'停了（%d 个进程）' % n if n else '本来就没在跑'}")
    if r["mcp"]:
        say(f"  · 还有 {r['mcp']} 个 MCP 服务器连着 Claude 等客户端：它们归客户端管，关掉客户端就会退")
    say("已全部停止。要再用，双击 start.bat。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一键启动远见网页后台，可选顺带开始几个实例的测试")
    ap.add_argument("insts", nargs="*", type=int, help="要开始测试的实例号（可以好几个），不给就只起网页")
    ap.add_argument("--hours", type=float, default=24.0, help="测试最多跑多久（小时），到点 farm 不再重开；随时可以在网页上停")
    ap.add_argument("--director", type=int, nargs="+", default=[], metavar="N",
                    help="这几个实例带导演台（自动运镜 + 头顶血条），开关会记住，以后每次开始测试都带；网页实例卡片上可以关")
    ap.add_argument("--restart", action="store_true", help="后台已经在跑也先停掉再起")
    ap.add_argument("--stop", action="store_true",
                    help="彻底停止：游戏实例、AI、网关、气泡、官网预览、本系统用的本地模型，最后停远见后台")
    ap.add_argument("--keep-llm", action="store_true", help="--stop 时不卸载本地大模型")
    ap.add_argument("--port", type=int, default=0, help="端口（默认 openwar3.json 的 ports.console）")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    if args.port:
        set_port(args.port)
    if any(not 1 <= n <= 30 for n in args.insts):
        say("实例号要在 1~30")
        return 2
    if args.stop:
        return stop_everything(args.keep_llm)
    if not os.path.exists(os.path.join(C.WEB_DIST, "index.html")):
        say(f"⚠ 网页没编译：{C.WEB_DIST} 里没有 index.html —— 双击 start.bat（会自动编），或 cd console\\web && npm run build")

    if args.restart and server_procs():
        say(f"停掉正在跑的远见后台（{stop_server()} 个进程）…")
        time.sleep(1.0)
    if server_up():
        owner = port_owner()
        if owner != "ours":
            where = owner.split(":", 1)[1] if owner.startswith("other-repo:") else "别的程序（或旧版远见）"
            say(f"✘ {PORT} 端口被占着，在听的不是这份 OpenWar3 的远见：{where}")
            say("   关掉它再双击 start.bat；或者在 openwar3.json 里给远见换个端口，例如 \"ports\": {\"console\": 8867}")
            return 1
        say(f"远见后台已经在跑：{URL}")
    else:
        r = start_server()
        if r.get("rc") != 0:
            say(f"✘ 起不来远见后台：{r}")
            return 1
        say(f"远见后台启动中（pid {r['pid']}）…")
        t_end = time.monotonic() + 90
        while time.monotonic() < t_end and not server_up():
            time.sleep(0.5)
        if not server_up():
            say(f"✘ 90 秒内没起来，看 {log_path()}")
            return 1
        say(f"✔ 远见后台已启动：{URL}")

    if not args.no_browser:
        webbrowser.open(URL + "/#/home")

    for n in args.director:
        try:
            r = http("POST", f"/api/instances/{n}/director", {"on": True}, timeout=60)
            say(f"  {'✔' if r.get('ok') else '✘'} 实例 {n} 带导演台：{r.get('note') or r.get('error') or ('导播 pid %s' % r.get('pid') if r.get('pid') else '已开')}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            say(f"  ✘ 实例 {n} 导演台开关没设上：{exc}")

    if args.insts:
        say(f"开始测试：实例 {' '.join(map(str, args.insts))}（游戏 + 新大脑，最多 {args.hours:g} 小时；依次起，每个间隔 4 秒）…")
        try:
            res = http("POST", "/api/tests/start", {"insts": args.insts, "hours": args.hours}, timeout=60 + 15 * len(args.insts))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            say(f"✘ 调用失败：{exc}")
            return 1
        for r in res.get("results", []):
            parts = [f"{k}: {v.get('note') or ('已起 pid %s' % v.get('pid') if v.get('ok') else v.get('error') or v)}"
                     for k, v in (("游戏 farm", r["farm"]), ("新大脑", r["brain"]), ("导演台", r.get("directorStart")))
                     if v is not None]
            say(f"  {'✔' if r['ok'] else '✘'} 实例 {r['inst']}：" + "；".join(parts))
        if not res.get("ok"):
            return 1
    say("网关、头顶气泡、本地大模型、官网预览在首页「控制中心」起停；开局 / 停局在「实例与开局」页。")
    say("")
    say("这个窗口几秒后会自己关掉：远见在后台继续运行，关掉浏览器也不会停（再双击 start.bat 就是重新打开网页）。")
    say("彻底停止（游戏、AI、所有服务、远见后台）：双击 stop.bat，或者在控制中心右上角点「全部停止」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""一键启动「游戏里的聊天气泡」的命令行版（09-26 起平时在远见首页「控制中心」点：本地大模型、头顶聊天气泡两张卡片；
原来的 start-speech.bat / 启动气泡.bat 已并进去。下面的「启动气泡.bat」换成 python speech\\speak_launch.py 用法一样）。
远见的「本地大模型 → 启动」走的就是这里的 ensure_llm。

09-19 加的：一键启动游戏里的聊天气泡服务。

双击做三件事，每件都是**先看在不在，不在才起**（重复双击没有副作用）：
  1. 本地大模型：LM Studio 的本地服务（1234 端口）+ speak_api.json 里配的那个模型载进显存，再空跑一句预热；
     09-19 实测：LM Studio 被关掉以后什么都不报，茶话会只是悄悄不说话了
  2. 气泡接口 speak_api.py（8872，带调试页）—— 用 start_speak_api.ps1 经 WMI 拉起，关掉这个窗口它也照跑
  3. 打开调试页
然后打印一张状态表：模型、接口、各功能的自动开启实例号、哪些游戏在跑、每个实例上三个功能开着没有。

自动开启实例号（茶话会 / 镜头对白 / 战况触发）存在 speak_api.json，接口起来后 5 秒内自动开到那些实例上。
带实例号就顺手改这张表：
    启动气泡.bat 5                      5 号开全部三个功能（不在表里的功能保持原样）
    启动气泡.bat 1 5 --features banter  只把茶话会的自动开启改成 1 号和 5 号
    启动气泡.bat --restart              改了代码后重起接口
    启动气泡.bat --stop                 停接口 + 把模型从显存卸掉（约 21 GB）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'sdk' / 'python'))
import w3paths  # noqa: E402

CONFIG = HERE / 'speak_api.json'
PORT = w3paths.port('speech', 8872)          # openwar3.json 的 ports.speech（以前写死 8872，改了配置就对不上）
API = f'http://127.0.0.1:{PORT}'
FEATURES = {'banter': '农民茶话会', 'camtalk': '镜头对白', 'events': '战况触发'}
NOWIN = 0x08000000                      # CREATE_NO_WINDOW


def say(msg: str) -> None:
    print(msg, flush=True)


def llm_config() -> dict:
    cfg = {'base_url': w3paths.llm_base(), 'model': w3paths.llm_model(), 'mode': 'qwen-nothink'}
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding='utf-8')).get('llm', {}))
    except (OSError, ValueError):
        pass
    return cfg


def http_json(url: str, body=None, timeout: float = 5.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def lms_exe() -> str | None:
    found = shutil.which('lms')
    if found:
        return found
    guess = Path.home() / '.lmstudio' / 'bin' / 'lms.exe'
    return str(guess) if guess.exists() else None


def lms(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
    exe = lms_exe()
    if not exe:
        return 127, '找不到 lms 命令（LM Studio 没装，或没把 ~/.lmstudio/bin 加进 PATH）'
    p = subprocess.run([exe] + args, capture_output=True, timeout=timeout, creationflags=NOWIN)
    return p.returncode, (p.stdout + p.stderr).decode('utf-8', 'replace')


def loaded_models() -> list[str]:
    code, out = lms(['ps', '--json'])
    if code != 0:
        return []
    start = out.find('[')
    try:
        rows = json.loads(out[start:]) if start >= 0 else []
    except ValueError:
        return []
    return [str(r.get('identifier') or r.get('modelKey') or r.get('path') or '') for r in rows]


def ensure_llm(skip: bool) -> bool:
    cfg = llm_config()
    model = cfg['model']
    if skip:
        say(f'[模型] 跳过（--no-llm）')
        return True
    say('[模型] 检查 LM Studio 本地服务……')
    code, out = lms(['server', 'start', '--port', '1234'], timeout=90)
    if code != 0:
        say(f'[模型] ✘ 本地服务起不来：{out.strip()[:200]}')
        return False
    if model in loaded_models():
        say(f'[模型] ✔ {model} 已在显存里')
    else:
        say(f'[模型] 载入 {model}（约 21 GB，第一次要几十秒）……')
        code, out = lms(['load', model, '--gpu', 'max', '-c', '4096', '-y'], timeout=600)
        if code != 0 or model not in loaded_models():
            say(f'[模型] ✘ 载入失败：{out.strip()[-300:]}')
            return False
        say(f'[模型] ✔ 载入完成')
    # 预热一句：第一次推理要建缓存，免得游戏里第一句等好几秒
    t0 = time.perf_counter()
    try:
        if cfg.get('mode') == 'qwen-nothink':
            body = {'model': model, 'prompt': '<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n',
                    'max_tokens': 4, 'temperature': 0}
            http_json(cfg['base_url'].rstrip('/') + '/completions', body, timeout=120)
        else:
            body = {'model': model, 'messages': [{'role': 'user', 'content': '你好'}], 'max_tokens': 4}
            http_json(cfg['base_url'].rstrip('/') + '/chat/completions', body, timeout=120)
        say(f'[模型] ✔ 预热完成（{(time.perf_counter() - t0) * 1000:.0f} ms）')
    except Exception as e:  # noqa: BLE001
        say(f'[模型] ⚠ 预热没成功：{e}（接口照起，第一句可能慢一点）')
    return True


def api_up() -> bool:
    try:
        http_json(API + '/api/autostart', timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def start_api() -> bool:
    say(f'[接口] 起 speak_api（{PORT}）……')
    p = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(HERE / 'start_speak_api.ps1'), '-Port', str(PORT)], capture_output=True, timeout=60,
                       creationflags=NOWIN)
    out = (p.stdout + p.stderr).decode('utf-8', 'replace').strip()
    if api_up():
        say('[接口] ✔ 起来了')
        return True
    say(f'[接口] ✘ 没起来：{out[-300:]}（日志 {HERE / "speak_api.out"}）')
    return False


def stop_all() -> int:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*speak_api.py*' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    p = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=30, creationflags=NOWIN)
    pids = p.stdout.decode('utf-8', 'replace').split()
    say(f'[接口] 停了 {len(pids)} 个进程' if pids else '[接口] 本来就没在跑')
    code, out = lms(['unload', '--all'], timeout=60)
    say('[模型] 已从显存卸掉' if code == 0 else f'[模型] 卸载没成功：{out.strip()[:200]}')
    return 0


def set_autostart(insts: list[int], features: list[str]) -> None:
    body = {f: ','.join(str(i) for i in insts) for f in features}
    try:
        http_json(API + '/api/autostart', body)
        say(f'[自动开启] {"、".join(FEATURES[f] for f in features)} -> {", ".join(map(str, insts))} 号（5 秒内开起来）')
    except Exception as e:  # noqa: BLE001
        say(f'[自动开启] ✘ 没改成：{e}')


def report() -> None:
    try:
        auto = http_json(API + '/api/autostart')['autostart']
        running = http_json(API + '/api/instances')
    except Exception as e:  # noqa: BLE001
        say(f'[状态] 读不到：{e}')
        return
    say('')
    say('自动开启实例号（网页各栏可改，会存盘）：')
    for f, name in FEATURES.items():
        say(f'  {name}：{", ".join(map(str, auto.get(f) or [])) or "无"}')
    say('在跑的游戏：' + ('、'.join(f'{r["inst"]} 号（pid {r["pid"]}）' if r['inst'] else f'原名（pid {r["pid"]}）'
                                   for r in running) or '无'))
    if running:
        time.sleep(6)                   # 看门线程 5 秒一轮，等它把表里的实例开起来再报
        for r in running:
            q = f'inst={r["inst"]}' if r['inst'] else f'pid={r["pid"]}'
            bits = []
            for f, name in FEATURES.items():
                try:
                    on = http_json(f'{API}/api/{f}?{q}').get('running')
                except Exception:  # noqa: BLE001
                    on = None
                bits.append(f'{name} {"开" if on else "关" if on is False else "?"}')
            say(f'  {r["inst"] or "原名"} 号：' + '，'.join(bits))
    say('')
    say(f'界面：远见的「头顶气泡」页（{API}/ 会跳过去）')


def main() -> int:
    ap = argparse.ArgumentParser(description='一键启动游戏里的聊天气泡（本地模型 + 气泡接口 + 调试页）')
    ap.add_argument('instances', nargs='*', type=int, help='把这些实例号设成自动开启（默认三个功能都设）')
    ap.add_argument('--features', default='banter,camtalk,events',
                    help='带实例号时改哪几个功能的自动开启：banter,camtalk,events（逗号分隔）')
    ap.add_argument('--restart', action='store_true', help='接口在跑也重起（改了代码以后用）')
    ap.add_argument('--stop', action='store_true', help='停接口 + 卸载模型')
    ap.add_argument('--no-llm', action='store_true', help='不管本地模型（模型在别处跑时用）')
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    if args.stop:
        return stop_all()
    features = [f.strip() for f in args.features.split(',') if f.strip()]
    bad = [f for f in features if f not in FEATURES]
    if bad:
        say(f'不认识的功能：{bad}（只有 {", ".join(FEATURES)}）')
        return 2

    llm_ok = ensure_llm(args.no_llm)
    if api_up() and not args.restart:
        say('[接口] ✔ 已经在跑')
    elif not start_api():
        return 1
    if args.instances:
        set_autostart(args.instances, features)
    report()
    if not llm_ok:
        say('⚠ 本地模型没准备好：气泡接口能用，但茶话会/镜头对白/战况触发说不出话。先开 LM Studio 再双击一次。')
    if not args.no_browser:
        webbrowser.open(API + '/')
    return 0 if llm_ok else 1


if __name__ == '__main__':
    sys.exit(main())

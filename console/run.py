# -*- coding: utf-8 -*-
"""Farsight（远见）—— War3 AI 指挥台。

    python console/run.py              # http://127.0.0.1:8866（端口默认读 openwar3.json 的 ports.console）
    python console/run.py --port 9000

只监听本机。前端：cd console/web && npm run build（开发时 npm run dev，Vite 把 /api 和 /ws 代理到这里）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _default_port() -> int:
    """openwar3.json 的 ports.console（start.bat / launch.py 按它探测；以前这里写死 8866，改了配置就对不上）。"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sdk", "python"))
        import w3paths
        return int(w3paths.port("console", 8866))
    except Exception:  # noqa: BLE001
        return 8866


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=_default_port())
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    from server.app import app
    print(f"Farsight 远见：http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", ws_ping_interval=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

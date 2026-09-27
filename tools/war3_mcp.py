# -*- coding: utf-8 -*-
"""给大模型的工具接口（MCP，stdio）的启动脚本：不用管 PYTHONPATH，任何目录下都能起。

    python tools/war3_mcp.py --inst 9                        # dev 角色
    python tools/war3_mcp.py --inst 9 --role player --player 0

挂到 Claude Code：  claude mcp add war3 -- python <这个文件的完整路径> --inst 9
挂到别的 MCP 客户端（Claude Desktop 等）：command = python，args = [这个文件的完整路径, "--inst", "9"]。
工具说明见 sdk/python/openwar3/mcp.py 和 docs/MCP_ZH.md。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk", "python"))
from openwar3.mcp import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

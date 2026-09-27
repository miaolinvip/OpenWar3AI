# -*- coding: utf-8 -*-
"""后台起常驻进程：WMI Win32_Process.Create。

起出来的进程父进程是 WmiPrvSE，不挂在调用方的进程树 / 作业（Job）里：远见后台重启、调用它的脚本被连树结束，
farm、大脑、游戏照跑（09-24 实测：演练实例挂在调用方的进程树上，调用方一退游戏跟着死）。
CIM 调用走 Windows 自带的 PowerShell 5.1（w3paths.powershell_exe，每台 Windows 10 / 11 都有，不用另装）。
"""
from __future__ import annotations

import os
import re
import subprocess

import w3paths

CREATE_NO_WINDOW = 0x08000000
# ★ 09-18 后台起进程时弹出一堆 cmd 窗口 —— ProcessStartupInformation.ShowWindow = 0（SW_HIDE）：
#   对照实测 带 0 个可见窗口 / 不带 1 个；输出本来就重定向进日志文件。命令行和目录走环境变量，不用操心引号转义。
_PS = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }; "
       "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $env:FS_CMD; "
       "CurrentDirectory = $env:FS_CWD; ProcessStartupInformation = $si }; Write-Output ('{0} {1}' -f $r.ProcessId, $r.ReturnValue)")


def spawn_detached(cmdline: str, cwd: str, timeout: float = 30) -> dict:
    """起 cmdline（整条命令行字符串），工作目录 cwd。返回 {pid, rc}；rc = 0 成功（9 = 找不到程序：WMI 不走 PATH，要全路径）。
    ⚠ WMI 起的进程拿的是用户的默认环境变量，不继承调用方的 —— 要传环境变量就在命令行里 `cmd /c "set X=1&& ..."`。"""
    env = dict(os.environ, FS_CMD=cmdline, FS_CWD=cwd)
    env.pop("PSModulePath", None)        # 从 PowerShell 7 终端里起的远见带着 7 的模块路径：别让 5.1 继承（start.bat 踩过）
    try:
        r = subprocess.run([w3paths.powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", _PS],
                           capture_output=True, text=True, env=env, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"pid": 0, "rc": -1, "error": str(exc)}
    m = re.search(r"(\d+)\s+(\d+)", r.stdout or "")
    if not m:
        return {"pid": 0, "rc": -1, "error": (r.stderr or r.stdout or "").strip()[-400:]}
    return {"pid": int(m.group(1)), "rc": int(m.group(2))}

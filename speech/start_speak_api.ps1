# 起头顶气泡接口服务（127.0.0.1:8872），用 WMI 拉起 —— 不挂在调用它的会话/终端上，关掉终端它也照跑。
# 停：Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*speak_api.py*' | % { Stop-Process -Id $_.ProcessId }
param([int]$Port = 8872)
$here = $PSScriptRoot
$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*speak_api.py*' }
foreach ($p in $old) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
$cfg = @{}
$cfgFile = Join-Path (Split-Path -Parent $here) 'openwar3.json'
if (Test-Path $cfgFile) { $cfg = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json }
$py = if ($cfg.python) { $cfg.python } else { (Get-Command python).Source }
$log = Join-Path $here 'speak_api.out'
$cmd = "cmd.exe /c `"set PYTHONIOENCODING=utf-8 && `"$py`" `"$here\speak_api.py`" --port $Port > `"$log`" 2>&1`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $here }
"create rc=$($r.ReturnValue)"
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try { $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/instances" -TimeoutSec 2; "up: http://127.0.0.1:$Port/"; exit 0 } catch { }
}
"not up yet; see $log"
exit 1

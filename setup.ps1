# OpenWar3 一键部署 —— start.bat 调它；在一台什么都没装过的 Windows 10/11 上从零装好，之后每次双击只做几秒的快速检查。
#
#     start.bat                      部署检查（缺什么补什么，已经有的直接过）-> 起远见 -> 打开浏览器「控制中心」
#     start.bat setup                完整检查：重装 Python 包、重试 AMAI，然后起远见（游戏目录在远见「控制中心」设）
#     start.bat stop                 彻底停止全部（= stop.bat）：游戏实例、AI、各服务、本地模型、远见后台
#     start.bat restart              只重启远见后台
#     powershell -ExecutionPolicy Bypass -File setup.ps1            只部署，不起远见
#     powershell -ExecutionPolicy Bypass -File setup.ps1 -Rebuild   强制重编网页、重新提取游戏数据（有 native/ 源码时也重编 DLL）
#
# 装什么、装在哪 —— 都不要管理员权限，不写注册表、不改系统 PATH，全在本仓库的 bin\env\ 里：
#   Python 3.13     **唯一要装的环境**。openwar3.json 的 python 能用就用它；否则下官方 NuGet 便携包（完整 CPython，自带 pip，约 14 MB）到 bin\env\python
#   PowerShell      用 Windows 自带的 5.1（本脚本就跑在它上面；远见在后台起进程也用它）。09-26 起不再装 PowerShell 7：farm 改成了 Python
#   远见网页        编好的 console\web\dist\ 随仓库发，源码指纹对得上就直接用 —— 不用 Node.js
#   Node.js 22      只有改了网页源码要重编、或者 start.bat node（官网预览）时才找 / 下：系统里的能用就用，不行就下官方 zip 到 bin\env\node
#   注入 DLL        有 native\ 源码 = 本机编（要 VS Build Tools）；没有 = 从 prebuilt\ 同步到 bin\（核对 SHA256）
#   游戏数据        从你自己的魔兽争霸 III 1.27a 里提取（暴雪的文件不随仓库分发）
#   AMAI 数据       参考大脑的战略引擎要用；git clone 或下载 zip 后生成（失败不影响别的，只是参考大脑跑不了）
# 国内网络：每个下载先试官方源，失败换镜像（npmmirror / 清华 / 华为云）并核对 SHA256；pip、npm 同理。
# 选出来的 python / node 路径写进 openwar3.json（远见和 farm 都按它找），部署记录写 bin\env\state.json，日志 bin\env\setup.log。
#
# ⚠ 这个文件必须存成 **UTF-8 带 BOM**：新电脑上只有系统自带的 Windows PowerShell 5.1，不带 BOM 它按 GBK 读，中文全乱、直接语法错误。
#   也只能写 5.1 认的语法：没有 ?: ?? && ||，Join-Path 只接两个参数，调原生程序别用 2>&1（5.1 会把 stderr 当成异常）。
param(
    [switch]$Launch,      # 部署完起远见（start.bat 传）；start.bat 的参数在环境变量 OW3_ARGS 里
    [switch]$Full,        # 完整检查（= start.bat setup）
    [switch]$Rebuild,
    [switch]$SkipAmai,
    [switch]$Test,        # 部署完跑一遍 tools\run_tests.py
    [switch]$WithNode     # 顺带装好 Node.js（= start.bat node；官网预览要它）。⚠ 别叫 $Node：PowerShell 变量名不分大小写，会和下面的 $node 撞
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # 5.1 的 Invoke-WebRequest 进度条能把下载拖慢几十倍
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'                   # 接住 Python 的输出时按 UTF-8 解（上面那行），中文才不乱
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$root = $PSScriptRoot
$bin = Join-Path $root 'bin'
$envDir = Join-Path $bin 'env'
$dlDir = Join-Path $envDir 'downloads'
$cfgFile = Join-Path $root 'openwar3.json'
$stateFile = Join-Path $envDir 'state.json'
New-Item -ItemType Directory -Force -Path $envDir, $dlDir | Out-Null
try { Start-Transcript -Path (Join-Path $envDir 'setup.log') -Force | Out-Null } catch { }

# 固定版本 + SHA256（镜像下下来的也要和官方一致）
$PY_VER = '3.13.7'
$PY_NUPKG_SHA = 'E74272A824E23702DFB5F3E11C3660CEABAC7487E3366D4551391DB5CD762853'
$PY_EXE_SHA = 'B12E2E82461AC8E51FC43289050BC8EB937A32D84CE4D242E2C88258C37CF2BB'
$NODE_VER = '22.19.0'
$NODE_SHA = 'EA3FAD0E67A991D8477D8C01344B56E69C676CCB733F065B22436994B1253F86'
$GAME_VERS = @('1.27.0.52240')                    # 运行时支持的 Game.dll 版本（和 sdk/python/w3paths.py 的 SUPPORTED_GAME_VERSIONS 一致）
$GAME_LABEL = '1.27a（1.27.0.52240）'
$PYPI_MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$NPM_MIRROR = 'https://registry.npmmirror.com'

# ── 输出 ─────────────────────────────────────────────────────────────
$summary = New-Object System.Collections.ArrayList
function Step($m) { Write-Host ''; Write-Host "== $m ==" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  [X]  $m" -ForegroundColor Red }
function Note($m) { Write-Host "       $m" -ForegroundColor DarkGray }
function Sum($status, $name, $detail) { [void]$summary.Add([pscustomobject]@{ s = $status; n = $name; d = $detail }) }
function Fail($m) {
    Bad $m
    Write-Host ''
    Write-Host '部署没完成。按上面的提示处理后再双击 start.bat；日志在 bin\env\setup.log。' -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

# ── 小工具 ───────────────────────────────────────────────────────────
function Read-Json([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}
function Write-Json([string]$path, $obj) {
    $json = $obj | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding $false))
}
function Set-Cfg([string]$key, $value) {
    $script:cfg | Add-Member -NotePropertyName $key -NotePropertyValue $value -Force
    Write-Json $cfgFile $script:cfg
}
function Test-Exe([string]$p) { return ($p -and (Test-Path -LiteralPath $p -PathType Leaf)) }

# SHA256（大写十六进制）。⚠ 不用 Get-FileHash：从 PowerShell 7 终端里跑 start.bat 时 5.1 会继承 7 的模块路径，
#   Get-FileHash 这类写在 Utility 模块脚本里的命令直接「找不到」（09-26 实测）；.NET 的不受影响
function Get-Sha256([string]$path) {
    $fs = [IO.File]::OpenRead($path)
    try { return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($fs)) -replace '-', '') }
    finally { $fs.Dispose() }
}

# 调原生程序拿第一行 stdout（stderr 不重定向：5.1 在 Stop 模式下会把它当异常）
function Get-FirstLine([string]$exe, [string[]]$argList) {
    $ErrorActionPreference = 'Continue'
    try { $out = & $exe @argList } catch { return $null }
    if ($LASTEXITCODE -ne 0) { return $null }
    return (@($out) | Where-Object { $_ } | Select-Object -First 1)
}

# 依次试几个地址下载，核对大小和 SHA256；成功返回 $true
function Get-Download([string[]]$urls, [string]$out, [string]$sha = '', [long]$minBytes = 1MB) {
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    foreach ($u in $urls) {
        Note "下载 $u"
        $tmp = "$out.part"
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        $got = $false
        if (Test-Path -LiteralPath $curl) {
            $ErrorActionPreference = 'Continue'
            & $curl -L --fail --retry 2 --connect-timeout 20 -# -o $tmp $u | Out-Host
            $got = ($LASTEXITCODE -eq 0)
            $ErrorActionPreference = 'Stop'
        } else {
            try { (New-Object Net.WebClient).DownloadFile($u, $tmp); $got = $true } catch { Note $_.Exception.Message }
        }
        if (-not $got -or -not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -lt $minBytes) {
            Warn '这个源下不下来，换下一个'
            continue
        }
        if ($sha) {
            $h = Get-Sha256 $tmp
            if ($h -ne $sha.ToUpper()) { Warn "SHA256 对不上（$h），换下一个"; continue }
        }
        Move-Item -LiteralPath $tmp -Destination $out -Force
        return $true
    }
    return $false
}

# 解 zip 到 $dest（先解到临时目录再挪，失败不留半截）；$sub 是 zip 里要取的子目录（NuGet 包里的 tools\）
function Expand-To([string]$zip, [string]$dest, [string]$sub = '') {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $tmp = "$dest.unzip"
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
    $from = $tmp
    if ($sub) { $from = Join-Path $tmp $sub }
    else {
        $kids = @(Get-ChildItem -LiteralPath $tmp)
        if ($kids.Count -eq 1 -and $kids[0].PSIsContainer) { $from = $kids[0].FullName }   # node-v22…-win-x64\ 这种包一层的
    }
    Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $from -Destination $dest
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 启动参数（start.bat 放在 OW3_ARGS 里：setup / stop / restart / help，其余原样给 console\launch.py）──
$launchArgs = @()
if ($env:OW3_ARGS) { $launchArgs = @($env:OW3_ARGS.Trim() -split '\s+' | Where-Object { $_ }) }
$verb = ''
if ($launchArgs.Count -gt 0) { $verb = $launchArgs[0].ToLower() }
switch ($verb) {
    { $_ -in 'setup', 'repair', 'install' } { $Full = $true; $launchArgs = @($launchArgs | Select-Object -Skip 1) }
    'node' { $WithNode = $true; $launchArgs = @($launchArgs | Select-Object -Skip 1) }
    'restart' { $launchArgs = @('--restart') + @($launchArgs | Select-Object -Skip 1) }
    { $_ -in 'help', '-h', '--help', '/?' } {
        Write-Host @'
用法：
  start.bat                 部署检查（缺什么补什么）-> 起远见 -> 打开浏览器「控制中心」
  start.bat setup           完整检查：重装 Python 包、重试 AMAI（游戏目录在远见「控制中心」设）
  start.bat stop            彻底停止：游戏实例、AI、网关、气泡、官网预览、本地模型，最后远见后台（= 双击 stop.bat）
  start.bat stop --keep-llm 同上，但本地大模型留在显存里
  start.bat restart         只重启远见后台（游戏和各服务不受影响）
  start.bat node            顺带装一份 Node.js（约 33 MB，官网预览要它；平时用不着）
  start.bat 5 6             顺带开始 5、6 号实例的测试（游戏 + 参考 AI）
  其它参数：--director 5  --hours 12  --no-browser  --port 8867
网关、头顶气泡、本地大模型、官网预览都在远见首页「控制中心」起停。
'@
        exit 0
    }
}

$cfg = Read-Json $cfgFile
if ($null -eq $cfg) {
    if (Test-Path -LiteralPath $cfgFile) { Fail "openwar3.json 不是合法的 JSON：修好它，或者删掉让它重新生成（$cfgFile）" }
    $cfg = [pscustomobject]@{}
}

if ($verb -eq 'stop') {
    # 彻底停止：游戏实例、AI、网关、气泡、官网预览、本系统用的本地模型，最后远见后台（console\launch.py stop_everything）
    $rest = @($launchArgs | Select-Object -Skip 1)
    if (Test-Exe $cfg.python) { & $cfg.python (Join-Path $root 'console\launch.py') --stop @rest; exit $LASTEXITCODE }
    Write-Host '还没部署过，没有要停的。'
    exit 0
}

$state = Read-Json $stateFile
if ($null -eq $state) { $state = [pscustomobject]@{} }
$sourceMode = Test-Path -LiteralPath (Join-Path $root 'native\build.ps1')
$mode = '快速检查'
if ($Full) { $mode = '完整检查' }
Write-Host "OpenWar3 部署（$mode）：$root" -ForegroundColor Cyan

# ── 0. 系统 ──────────────────────────────────────────────────────────
if (-not [Environment]::Is64BitOperatingSystem) { Fail '需要 64 位 Windows 10 / 11。' }
if ([Environment]::OSVersion.Version.Major -lt 10) { Fail '需要 Windows 10 / 11（这台是更老的系统）。' }

# ── 1. Python ────────────────────────────────────────────────────────
Step 'Python'
function Test-Python([string]$exe) {
    if (-not (Test-Exe $exe)) { return $null }
    $line = Get-FirstLine $exe @('-c', "import sys,struct;print(*sys.version_info[:3],struct.calcsize('P')*8)")
    if (-not $line) { return $null }
    $p = "$line".Split(' ')
    if ($p.Count -lt 4) { return $null }
    if ([int]$p[3] -ne 64 -or [int]$p[0] -ne 3 -or [int]$p[1] -lt 11) { return $null }
    return "$($p[0]).$($p[1]).$($p[2])"
}
$pyPrivate = Join-Path $envDir 'python\python.exe'
$py = $null
$pyVer = Test-Python $cfg.python
if ($pyVer) { $py = $cfg.python; Ok "Python $pyVer（openwar3.json）" }
else {
    if ($cfg.python) { Warn "openwar3.json 里的 python 用不了（要 3.11+ 的 64 位）：$($cfg.python)" }
    $pyVer = Test-Python $pyPrivate
    if (-not $pyVer) {
        Note "下载 Python $PY_VER（官方 NuGet 便携包，约 14 MB，装在 bin\env\python，不动系统）"
        $pkg = Join-Path $dlDir "python.$PY_VER.nupkg"
        $got = (Test-Path -LiteralPath $pkg) -and ((Get-Sha256 $pkg) -eq $PY_NUPKG_SHA)
        if (-not $got) {
            $got = Get-Download @("https://api.nuget.org/v3-flatcontainer/python/$PY_VER/python.$PY_VER.nupkg",
                                  "https://www.nuget.org/api/v2/package/python/$PY_VER") $pkg $PY_NUPKG_SHA
        }
        if ($got) { Expand-To $pkg (Join-Path $envDir 'python') 'tools' }
        else {
            # NuGet 连不上：退回官方安装包，装到同一个私有目录（按用户装、不加 PATH、不关联文件）
            $exe = Join-Path $dlDir "python-$PY_VER-amd64.exe"
            $got = Get-Download @("https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe",
                                  "https://registry.npmmirror.com/-/binary/python/$PY_VER/python-$PY_VER-amd64.exe",
                                  "https://mirrors.huaweicloud.com/python/$PY_VER/python-$PY_VER-amd64.exe") $exe $PY_EXE_SHA
            if (-not $got) { Fail '下不到 Python：检查网络，或者自己装 Python 3.11+（64 位），把 python.exe 的路径写进 openwar3.json 的 "python"。' }
            $target = Join-Path $envDir 'python'
            $p = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList @('/quiet', 'InstallAllUsers=0', "TargetDir=`"$target`"",
                'PrependPath=0', 'Include_launcher=0', 'InstallLauncherAllUsers=0', 'Shortcuts=0', 'AssociateFiles=0',
                'Include_test=0', 'Include_doc=0', 'Include_tcltk=0', 'Include_pip=1')
            if ($p.ExitCode -ne 0) { Fail "Python 安装程序退出码 $($p.ExitCode)（1638 = 这台机器已经装了同版本：把它的 python.exe 路径写进 openwar3.json 的 `"python`"）" }
        }
        $pyVer = Test-Python $pyPrivate
        if (-not $pyVer) { Fail "Python 解出来了但跑不起来：$pyPrivate" }
    }
    $py = $pyPrivate
    Set-Cfg 'python' $py
    Ok "Python $pyVer（$py）"
}
Sum 'ok' 'Python' $pyVer

# ── 2. Python 包 ─────────────────────────────────────────────────────
Step 'Python 包'
$req = Join-Path $root 'requirements.txt'
$reqHash = Get-Sha256 $req
$needPip = $Full -or ($state.pipHash -ne $reqHash) -or ($state.python -ne $py)
if (-not $needPip) {
    $chk = Get-FirstLine $py @('-c', 'import numpy,psutil,fastapi,uvicorn,pydantic,websockets,mpyq;print(1)')
    $needPip = ($chk -ne '1')
}
if (-not $needPip) { Ok 'requirements.txt 没变，已装好' }
else {
    $ErrorActionPreference = 'Continue'
    & $py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade }
    $pipArgs = @('-m', 'pip', 'install', '-r', $req, '--disable-pip-version-check', '--no-warn-script-location', '--timeout', '30')
    & $py @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Warn "官方源装不上，换清华镜像再试（$PYPI_MIRROR）"
        & $py @pipArgs -i $PYPI_MIRROR
    }
    $ErrorActionPreference = 'Stop'
    $chk = Get-FirstLine $py @('-c', 'import numpy,psutil,fastapi,uvicorn,pydantic,websockets,mpyq;print(1)')
    if ($chk -ne '1') { Fail 'Python 包没装全（看上面 pip 的报错；多半是网络）。' }
    $state | Add-Member -NotePropertyName pipHash -NotePropertyValue $reqHash -Force
    Ok 'requirements.txt'
}
Sum 'ok' 'Python 包' 'requirements.txt'

# 09-26 起不再需要 PowerShell 7（起游戏实例的 farm 改成了 Python，后台起进程用 Windows 自带的 5.1）。
# 以前部署时下的私有那份（bin\env\pwsh，约 105 MB）和安装包删掉；openwar3.json 里指向它的 pwsh 一起去掉。
$oldPwsh = Join-Path $envDir 'pwsh'
if (Test-Path -LiteralPath $oldPwsh) {
    try { Remove-Item -LiteralPath $oldPwsh -Recurse -Force; Note '删掉了以前下的 PowerShell 7（bin\env\pwsh）：已经用不着' }
    catch { Warn "bin\env\pwsh 删不掉（$($_.Exception.Message)）：已经用不着，可以手动删" }
}
Get-ChildItem -LiteralPath $dlDir -Filter 'PowerShell-*-win-x64.zip' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
if ("$($cfg.pwsh)" -like "$oldPwsh*") { $cfg.PSObject.Properties.Remove('pwsh'); Write-Json $cfgFile $cfg }

# ── 3. 游戏目录 ──────────────────────────────────────────────────────
Step '魔兽争霸 III 1.27a'
function Get-GameVersion([string]$d) {
    try {
        $vi = (Get-Item -LiteralPath (Join-Path $d 'Game.dll')).VersionInfo
        return ('{0}.{1}.{2}.{3}' -f $vi.FileMajorPart, $vi.FileMinorPart, $vi.FileBuildPart, $vi.FilePrivatePart)
    } catch { return $null }
}
function Test-GameDir([string]$d) {
    return ($d -and (Test-Path -LiteralPath (Join-Path $d 'War3.exe')) -and (Test-Path -LiteralPath (Join-Path $d 'Game.dll')))
}
# 09-26 起游戏目录由玩家在远见里自己设（首页「控制中心」的设置卡片，远见帮着找候选、检查版本、自动提取游戏数据），
# 这里不再替玩家猜 / 弹窗，只核对已经设了的：目录对不对、版本是不是运行时支持的。
$gameDir = "$($cfg.game_dir)"
$gameValid = Test-GameDir $gameDir
$gameVer = $null
if ($gameValid) { $gameVer = Get-GameVersion $gameDir }
if (-not $gameDir) {
    Note '还没设置魔兽争霸 III 的目录：远见打开后在首页「控制中心」最上面设置（远见会检查版本、自动提取游戏数据）。'
    Sum 'warn' '魔兽争霸 III' '未设置（打开远见后设置）'
} elseif (-not $gameValid) {
    Warn "设置的游戏目录不对：$gameDir（里面没有 War3.exe / Game.dll）。在远见「控制中心」重新设置。"
    Sum 'error' '魔兽争霸 III' '目录不对（在远见里重设）'
} elseif ($GAME_VERS -notcontains $gameVer) {
    Warn "游戏版本是 $gameVer，目前只支持 $GAME_LABEL。"
    Note "温馨提示：请下载 $GAME_LABEL 版本的魔兽争霸 III（冰封王座）。我们即将支持更多版本，请关注 War3AI.com。"
    Sum 'warn' '魔兽争霸 III' "版本 $gameVer（暂不支持）"
} else {
    Ok "$gameDir（$gameVer）"
    Sum 'ok' '魔兽争霸 III' $gameDir
    $maps = $cfg.maps_dir
    if (-not $maps) { $maps = Join-Path $gameDir 'Maps' }
    $map = $cfg.default_map
    if (-not $map) { $map = 'FrozenThrone\(2)EchoIsles.w3x' }
    if (-not [IO.Path]::IsPathRooted($map)) { $map = Join-Path $maps $map }
    if (-not (Test-Path -LiteralPath $map)) { Warn "默认地图不在：$map（开局前在远见「实例与开局」选一张图）" }
}

# ── 4. 注入运行时（DLL / 启动器 / 命令行）────────────────────────────
Step '注入运行时'
$natFiles = 'render_hook.dll', 'war3_launch.exe', 'war3_ui_command.exe'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
if ($sourceMode) {
    $missing = @($natFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $bin $_)) })
    if ($missing.Count -gt 0 -or $Rebuild) {
        Note '有 native\ 源码：本机编译（要 Visual Studio Build Tools 的「使用 C++ 的桌面开发」）'
        # 编译脚本优先用开发机上装的 PowerShell 7，没有就用自带的这份（native\ 不开源，只有开发机上有）
        $psExe = Get-Command pwsh -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
        if (-not $psExe) { $psExe = Join-Path $PSHOME 'powershell.exe' }
        $ErrorActionPreference = 'Continue'
        & $psExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'native\build.ps1')
        $rc = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($rc -ne 0) { Fail '编译失败：装 Visual Studio Build Tools（勾「使用 C++ 的桌面开发」）后重试；或者删掉 native\ 改用 prebuilt\ 里发布的二进制。' }
    }
    Ok '本机编译的（有 native\ 源码）'
    Sum 'ok' '注入运行时' '本机编译'
} else {
    $pre = Join-Path $root 'prebuilt'
    $man = Read-Json (Join-Path $pre 'manifest.json')
    if ($null -eq $man) { Fail '缺运行时二进制：prebuilt\manifest.json 不在。重新下载完整的仓库 / 发布包。' }
    $synced = 0
    foreach ($f in $natFiles) {
        $src = Join-Path $pre $f
        $dst = Join-Path $bin $f
        $want = $man.files.$f.sha256
        if (-not (Test-Path -LiteralPath $src)) { Fail "prebuilt\ 里少了 $f" }
        if ((Get-Sha256 $src) -ne "$want".ToUpper()) { Fail "prebuilt\$f 和 manifest.json 对不上（下载不完整？）" }
        if ((Test-Path -LiteralPath $dst) -and ((Get-Sha256 $dst) -eq "$want".ToUpper())) { continue }
        try { Copy-Item -LiteralPath $src -Destination $dst -Force; $synced++ }
        catch { Fail "更新 bin\$f 失败（$($_.Exception.Message)）：多半是游戏还开着、DLL 被占用。关掉所有魔兽窗口再双击 start.bat。" }
    }
    Start-Sleep -Milliseconds 300
    $gone = @($natFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $bin $_)) })
    if ($gone.Count -gt 0) {
        Fail ("bin\ 里的 {0} 刚复制完就不见了：多半被杀毒软件隔离了（注入 DLL 常被误报）。在杀毒软件里把本仓库目录加入排除 / 信任，再双击 start.bat。" -f ($gone -join ', '))
    }
    if ($synced) { Ok "从 prebuilt\ 更新了 $synced 个文件（版本 $($man.version)）" } else { Ok "已是发布版本 $($man.version)" }
    Sum 'ok' '注入运行时' "prebuilt $($man.version)"
}
# 从网上下的压缩包解出来的文件带「来自网络」标记：去掉，免得被拦
foreach ($d in $bin, (Join-Path $root 'prebuilt'), (Join-Path $root 'runtime')) {
    Get-ChildItem -LiteralPath $d -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
}

# ── 5. 游戏数据 ──────────────────────────────────────────────────────
Step '游戏数据（从你的游戏提取，不随仓库分发）'
$units = Join-Path $root 'data\game\units.json'
if ((Test-Path -LiteralPath $units) -and -not $Rebuild) { Ok '已有 data\game\'; Sum 'ok' '游戏数据' 'data\game' }
elseif (-not $gameValid) { Note '先跳过：在远见里设好游戏目录后会自动提取'; Sum 'warn' '游戏数据' '等设置游戏目录' }
else {
    $ErrorActionPreference = 'Continue'
    & $py (Join-Path $root 'data\tools\extract_game_data.py')
    $rc = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $units)) { Fail '游戏数据提取不完整（看上面哪一项出错）。' }
    Ok 'data\game\'
    Sum 'ok' '游戏数据' '已提取'
}

# ── 6. AMAI 战略数据（参考大脑要用；失败不挡别的）─────────────────────
Step 'AMAI 战略数据'
$amaiOut = Join-Path $root 'brains\xwar3\strategy\brain\amai_data\human.json'
if (Test-Path -LiteralPath $amaiOut) { Ok '已有 amai_data\'; Sum 'ok' 'AMAI 数据' '已生成' }
elseif ($SkipAmai) { Warn '跳过（-SkipAmai）：参考大脑跑不起来，示例 Bot 和别的方案不受影响'; Sum 'warn' 'AMAI 数据' '跳过' }
elseif ($state.amaiFailed -and -not $Full) {
    Warn '上次没弄成，这次先跳过（参考大脑跑不起来，别的不受影响）。要重试：start.bat setup'
    Sum 'warn' 'AMAI 数据' '上次失败，start.bat setup 重试'
} else {
    $amaiDir = Join-Path $root 'third_party\AMAI'
    $std = Join-Path $amaiDir 'TFT\StandardUnits.txt'
    if (-not (Test-Path -LiteralPath $std)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'third_party') | Out-Null
        Remove-Item -LiteralPath $amaiDir -Recurse -Force -ErrorAction SilentlyContinue
        $git = Get-Command git -ErrorAction SilentlyContinue
        if ($git) {
            $ErrorActionPreference = 'Continue'
            & $git.Source clone --depth 1 https://github.com/SMUnlimited/AMAI.git $amaiDir
            $ErrorActionPreference = 'Stop'
        }
        if (-not (Test-Path -LiteralPath $std)) {
            Remove-Item -LiteralPath $amaiDir -Recurse -Force -ErrorAction SilentlyContinue
            $zip = Join-Path $dlDir 'AMAI-master.zip'
            if (Get-Download @('https://codeload.github.com/SMUnlimited/AMAI/zip/refs/heads/master') $zip '' 100KB) {
                try { Expand-To $zip $amaiDir } catch { Warn "解压 AMAI 失败：$($_.Exception.Message)" }
            }
        }
    }
    $okAmai = $false
    if (Test-Path -LiteralPath $std) {
        $ErrorActionPreference = 'Continue'
        & $py (Join-Path $root 'brains\xwar3\strategy\tools\amai_import.py') --amai $amaiDir
        $okAmai = ($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $amaiOut)
        $ErrorActionPreference = 'Stop'
    }
    if ($okAmai) {
        Ok 'amai_data\（AMAI 是自定义许可，生成物不进 git）'
        Sum 'ok' 'AMAI 数据' '已生成'
        $state | Add-Member -NotePropertyName amaiFailed -NotePropertyValue $false -Force
    } else {
        Warn '没弄成（GitHub 连不上？）：参考大脑跑不起来，示例 Bot 和别的方案不受影响。网络好了运行 start.bat setup 重试。'
        Sum 'warn' 'AMAI 数据' '没弄成'
        $state | Add-Member -NotePropertyName amaiFailed -NotePropertyValue $true -Force
    }
}

# ── 7. 远见网页（console\web\dist，随仓库发）─────────────────────────
# 编好的 dist\ 随仓库发：源码指纹（console\server\webstamp.py）对得上就直接用 —— 普通用户不用装 Node.js、不用下 90 MB 的依赖。
# 只有改了网页源码（或者 -Rebuild）才找 / 下 Node.js 重编；编完 npm run build 自己写新指纹。
Step '远见网页'
$web = Join-Path $root 'console\web'
$dist = Join-Path $web 'dist\index.html'
$ErrorActionPreference = 'Continue'
$webWhy = "$(& $py (Join-Path $root 'console\server\webstamp.py') --check)".Trim()
$stale = $Rebuild -or ($LASTEXITCODE -ne 0)
$ErrorActionPreference = 'Stop'
function Test-Node([string]$exe) {
    if (-not (Test-Exe $exe)) { return $null }
    $v = Get-FirstLine $exe @('--version')
    if ($v -match '^v(\d+)\.(\d+)') { if ([int]$Matches[1] -ge 20) { return "$v" } }
    return $null
}
function Get-PrivateNode {
    $exe = Join-Path $envDir 'node\node.exe'
    if (Test-Node $exe) { return $exe }
    Note "下载 Node.js $NODE_VER（官方 zip，约 33 MB，装在 bin\env\node）"
    $zip = Join-Path $dlDir "node-v$NODE_VER-win-x64.zip"
    $got = Get-Download @("https://nodejs.org/dist/v$NODE_VER/node-v$NODE_VER-win-x64.zip",
                          "https://registry.npmmirror.com/-/binary/node/v$NODE_VER/node-v$NODE_VER-win-x64.zip") $zip $NODE_SHA
    if (-not $got) { return $null }
    Expand-To $zip (Join-Path $envDir 'node')
    if (Test-Node $exe) { return $exe }
    return $null
}
function Build-Web([string]$node) {
    $ndir = Split-Path -Parent $node
    $npm = Join-Path $ndir 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npm)) { Warn "$ndir 里没有 npm.cmd"; return $false }
    $oldPath = $env:Path
    $env:Path = "$ndir;$env:Path"
    Push-Location $web
    $ErrorActionPreference = 'Continue'
    try {
        $lock = Join-Path $web 'package-lock.json'
        $mark = Join-Path $web 'node_modules\.package-lock.json'
        # ⚠ 函数里原生程序的输出不接住就会变成函数的返回值（npm 的输出被吞、返回值永远为真）：一律 | Out-Host
        if (-not (Test-Path -LiteralPath $mark) -or ((Get-Item -LiteralPath $lock).LastWriteTimeUtc -gt (Get-Item -LiteralPath $mark).LastWriteTimeUtc)) {
            & $npm ci --no-audit --no-fund | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Warn "官方源装不上，换镜像再试（$NPM_MIRROR）"
                & $npm ci --no-audit --no-fund "--registry=$NPM_MIRROR" | Out-Host
            }
            if ($LASTEXITCODE -ne 0) { return $false }
        }
        & $npm run build | Out-Host
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = 'Stop'
        Pop-Location
        $env:Path = $oldPath
    }
}
function Find-Node {
    foreach ($c in @($cfg.node, (Get-Command node -ErrorAction SilentlyContinue | ForEach-Object Source), (Join-Path $envDir 'node\node.exe'))) {
        if (Test-Node $c) { return $c }
    }
    return $null
}
if (-not $stale) { Ok '随仓库发的编译结果，和源码一致（不用 Node.js）'; Sum 'ok' '远见网页' '已编译' }
else {
    if (-not $Rebuild) { Note "$webWhy（用 Node.js 编）" }
    $node = Find-Node
    if (-not $node) { $node = Get-PrivateNode }
    $built = $false
    if ($node) {
        Note "编译网页（Node $(Test-Node $node)：$node）"
        $built = Build-Web $node
        if (-not $built -and $node -ne (Join-Path $envDir 'node\node.exe')) {
            Warn '用系统里的 Node 没编成，换一份 Node.js 22 再试'
            $n2 = Get-PrivateNode
            if ($n2) { $node = $n2; $built = Build-Web $node }
        }
    }
    if ($built) {
        if ($cfg.node -ne $node) { Set-Cfg 'node' $node }
        Ok 'console\web\dist\'
        Sum 'ok' '远见网页' '已编译'
    } elseif (Test-Path -LiteralPath $dist) {
        Warn '网页没编成（看上面的报错），先用上一次编好的'
        Sum 'warn' '远见网页' '用旧的编译结果'
    } else {
        Fail '网页编不出来，又没有旧的编译结果：看上面 npm 的报错（多半是网络），网络好了再双击 start.bat。'
    }
}
if ($WithNode) {
    # start.bat node：官网预览（远见控制中心里起）要 Node.js
    $n = Find-Node
    if (-not $n) { $n = Get-PrivateNode }
    if ($n) {
        if ($cfg.node -ne $n) { Set-Cfg 'node' $n }
        Ok "Node.js $(Test-Node $n)（$n）"
        Sum 'ok' 'Node.js' "$(Test-Node $n)"
    } else {
        Warn '下不到 Node.js：检查网络，或者自己装 Node.js 22（nodejs.org）'
        Sum 'warn' 'Node.js' '没装上'
    }
}

# ── 收尾 ─────────────────────────────────────────────────────────────
if ($state.PSObject.Properties['pwsh']) { $state.PSObject.Properties.Remove('pwsh') }
foreach ($kv in @(@('at', (Get-Date).ToString('o')), @('python', $py), @('mode', $mode), @('version', 1))) {
    $state | Add-Member -NotePropertyName $kv[0] -NotePropertyValue $kv[1] -Force
}
if ($cfg.node) { $state | Add-Member -NotePropertyName node -NotePropertyValue $cfg.node -Force }
Write-Json $stateFile $state

Write-Host ''
Write-Host '部署结果' -ForegroundColor Cyan
foreach ($r in $summary) {
    $color = 'Green'; $tag = '[OK]'
    if ($r.s -eq 'warn') { $color = 'Yellow'; $tag = '[!] ' }
    if ($r.s -eq 'error') { $color = 'Red'; $tag = '[X] ' }
    Write-Host ('  {0} {1,-14} {2}' -f $tag, $r.n, $r.d) -ForegroundColor $color
}

if ($Test) {
    Step '测试'
    & $py (Join-Path $root 'tools\run_tests.py')
}
try { Stop-Transcript | Out-Null } catch { }

if ($Launch) {
    Write-Host ''
    & $py (Join-Path $root 'console\launch.py') @launchArgs
    exit $LASTEXITCODE
}
Write-Host ''
Write-Host '部署完成。双击 start.bat 打开远见（首页「控制中心」管理所有服务）。' -ForegroundColor Cyan
exit 0

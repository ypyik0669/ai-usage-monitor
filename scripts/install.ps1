param(
    [switch]$SkipPlugin,
    [switch]$SkipAutoStart,
    [int]$Port = 8797
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==> [1/3] 检查 Python 环境..." -ForegroundColor Cyan
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    throw "未找到 python，请先安装 Python 3.10+（例如：winget install Python.Python.3.12）。"
}
$pyExe = $pyCmd.Source
$pywExe = $pyExe -replace "python\.exe$", "pythonw.exe"
if (-not (Test-Path $pywExe)) { $pywExe = $pyExe }

if (-not $SkipPlugin) {
    Write-Host "==> [2/3] 安装 Antigravity 全局模型锁插件 (carbon-high-lock)..." -ForegroundColor Cyan
    $PluginDst = Join-Path $HOME ".gemini\config\plugins\carbon-high-lock"
    New-Item -ItemType Directory -Force -Path $PluginDst | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot "plugins\carbon-high-lock\*") -Destination $PluginDst -Recurse -Force
    Write-Host "    已部署到: $PluginDst（实时生效，无需重启 Antigravity）" -ForegroundColor Green
} else {
    Write-Host "==> [2/3] 已跳过安装 carbon-high-lock 插件。" -ForegroundColor Yellow
}

Write-Host "==> [3/3] 生成初始统计数据并启动本地实时监控服务..." -ForegroundColor Cyan
& $pyExe -X utf8 (Join-Path $RepoRoot "agg.py") --no-git

Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*serve.py*' } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 400

$env:USAGE_DASHBOARD_PORT = "$Port"
$servePath = Join-Path $RepoRoot "serve.py"
$cmdLine = "`"$pywExe`" -X utf8 `"$servePath`""
$null = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmdLine
    CurrentDirectory = $RepoRoot
}

if (-not $SkipAutoStart) {
    $StartupDir = [Environment]::GetFolderPath("Startup")
    $VbsPath = Join-Path $StartupDir "UsageDashboardLocal.vbs"
    $VbsTemplate = 'Set s = CreateObject("WScript.Shell")' + "`r`n" + 's.Run """{0}"" -X utf8 ""{1}""", 0, False' + "`r`n"
    $VbsContent = $VbsTemplate -f $pywExe, $servePath
    Set-Content -Path $VbsPath -Value $VbsContent -Encoding Default
    Write-Host "    已写入开机自启脚本: $VbsPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "✅ 部署完成！请打开浏览器访问: http://127.0.0.1:$Port/" -ForegroundColor Green

# 投资看板：设置开机（登录 Windows 时）自动启动，并在桌面放一个打开看板的快捷方式。
# 用法（在程序文件夹里运行）：
#   powershell -ExecutionPolicy Bypass -File dashboard\install-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File dashboard\install-autostart.ps1 -Uninstall   # 取消自动启动
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "moomoo-dashboard"
$Root = Split-Path -Parent $PSScriptRoot
$Startup = [Environment]::GetFolderPath("Startup")
$StartupLink = Join-Path $Startup "moomoo-dashboard.lnk"
$Desktop = [Environment]::GetFolderPath("Desktop")
$DesktopLink = Join-Path $Desktop "投资看板.url"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $StartupLink -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*-m dashboard*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "已取消看板的自动启动，并关掉了正在运行的看板。"
    exit 0
}

$Py = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Py)) { throw "没有找到 $Py ，请先按 README 建好 .venv" }

$Port = 8080
$cfg = Join-Path $Root "config.yaml"
if (Test-Path $cfg) {
    $m = Select-String -Path $cfg -Pattern '^\s+port:\s*(\d+)' | Select-Object -First 1
    if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
}

$ok = $false
try {
    $action = New-ScheduledTaskAction -Execute $Py -Argument "-m dashboard" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "投资看板 http://localhost:$Port （只读）" -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Remove-Item $StartupLink -ErrorAction SilentlyContinue
    $ok = $true
    Write-Host "已建立计划任务 $TaskName ：登录 Windows 时自动启动看板。"
} catch {
    Write-Host "建计划任务失败（$($_.Exception.Message)），改用“启动”文件夹。"
}

if (-not $ok) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($StartupLink)
    $lnk.TargetPath = $Py
    $lnk.Arguments = "-m dashboard"
    $lnk.WorkingDirectory = $Root
    $lnk.Save()
    Start-Process -FilePath $Py -ArgumentList "-m dashboard" -WorkingDirectory $Root -WindowStyle Hidden
    Write-Host "已在“启动”文件夹放了快捷方式：登录 Windows 时自动启动看板。"
}

Set-Content -Path $DesktopLink -Value "[InternetShortcut]`r`nURL=http://localhost:$Port/" -Encoding ASCII
Write-Host "桌面上放了“投资看板”快捷方式。"

$up = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch {}
}
if ($up) {
    Write-Host "看板已经在运行：http://localhost:$Port"
    Start-Process "http://localhost:$Port/"
} else {
    Write-Host "看板没有在 20 秒内启动，请看 logs\dashboard.log"
    exit 1
}

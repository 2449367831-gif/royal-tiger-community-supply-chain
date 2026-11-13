[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TaskName = "RoyalTigerInventoryWeb8791"
$Launcher = Join-Path $Root "scripts\run_inventory_server_hidden.ps1"
$VbsLauncher = Join-Path $Root "scripts\start_inventory_web_hidden.vbs"

$PowerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"{0}`"" -f $Launcher) -WorkingDirectory $Root
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$TaskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
try {
  # The Doris credentials are user-scoped environment values. Run the web
  # process in the interactive user's session so the query child inherits them.
  Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $TaskPrincipal -Description "Royal Tiger inventory web service on TCP 8791" -Force | Out-Null
  $RunMode = "current user at logon"
} catch {
  $RunMode = "current user Startup folder (task registration unavailable)"
}

# Keep a second logon entry even when task registration succeeds: a registered
# task may fail at runtime. The supervisor mutex makes both entries idempotent.
$startup = [Environment]::GetFolderPath("Startup")
New-Item -ItemType Directory -Force -Path $startup | Out-Null
$shortcutPath = Join-Path $startup "RoyalTigerInventoryWeb8791.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$shortcut.Arguments = "`"$VbsLauncher`""
$shortcut.WorkingDirectory = $Root
$shortcut.WindowStyle = 7
$shortcut.Description = "Royal Tiger inventory web service on TCP 8791"
$shortcut.Save()

try {
  if (-not (Get-NetFirewallRule -DisplayName "皇家小虎库存变动内网访问 8791" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "皇家小虎库存变动内网访问 8791" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8791 -RemoteAddress LocalSubnet -Profile Any | Out-Null
  }
} catch {
  Write-Warning "Firewall rule was not changed: $($_.Exception.Message)"
}

if ($RunMode -eq "current user at logon") {
  try { Start-ScheduledTask -TaskName $TaskName } catch {
    Write-Warning "Task start failed; using the hidden launcher."
  }
}
& cscript.exe //nologo $VbsLauncher | Out-Null
Write-Host "Installed and started task: $TaskName ($RunMode)"
Write-Host "Logs: $Root\tmp\pickup-web-8791-supervisor.log"

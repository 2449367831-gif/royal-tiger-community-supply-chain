[CmdletBinding()]
param(
  [int]$Port = 8791
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

$Mutex = New-Object System.Threading.Mutex($false, "Local\RoyalTigerInventoryWeb8791Supervisor")
$MutexAcquired = $false
try {
  try {
    $MutexAcquired = $Mutex.WaitOne(0, $false)
  } catch [System.Threading.AbandonedMutexException] {
    $MutexAcquired = $true
  }
  if (-not $MutexAcquired) {
    exit 0
  }

$LogDir = Join-Path $Root "tmp"
$SupervisorLog = Join-Path $LogDir "pickup-web-8791-supervisor.log"
$StdoutLog = Join-Path $LogDir "pickup-web-8791.log"
$StderrLog = Join-Path $LogDir "pickup-web-8791.err.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-SupervisorLog {
  param([string]$Message)
  "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message |
    Out-File -LiteralPath $SupervisorLog -Encoding UTF8 -Append
}

$env:PICKUP_WEB_HOST = "0.0.0.0"
$env:PICKUP_WEB_PORT = $Port.ToString()
# Tasks launched by an older Explorer/session may inherit stale environment
# values. Fill missing database settings from this user's persisted environment.
# Credentials stay in Windows; never write them to launch arguments or logs.
foreach ($Scope in @("User", "Machine")) {
  $Settings = [Environment]::GetEnvironmentVariables($Scope)
  foreach ($Key in $Settings.Keys) {
    if ($Key -match '^(DORIS_|PICKUP_DB_)' -and -not [Environment]::GetEnvironmentVariable($Key, "Process")) {
      [Environment]::SetEnvironmentVariable($Key, [string]$Settings[$Key], "Process")
    }
  }
}
$PythonCandidates = @(
  "C:\Users\24493\AppData\Local\Programs\Python\Python313\python.exe",
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
)
$PythonCandidate = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($PythonCandidate -and -not $env:INVENTORY_PYTHON) {
  $env:INVENTORY_PYTHON = $PythonCandidate
}

$NodeExe = "C:\Program Files\nodejs\node.exe"
if (-not (Test-Path -LiteralPath $NodeExe)) {
  $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
  if ($NodeCommand) {
    $NodeExe = $NodeCommand.Source
  }
}
if (-not (Test-Path -LiteralPath $NodeExe)) {
  Write-SupervisorLog "Node.js executable was not found."
  exit 1
}

function Test-InventoryPort {
  # CIM can omit an active listener; probe the local socket directly instead.
  $Probe = New-Object System.Net.Sockets.TcpClient
  try {
    $Connection = $Probe.ConnectAsync("127.0.0.1", $Port)
    return ($Connection.Wait(1000) -and $Probe.Connected)
  } catch {
    return $false
  } finally {
    $Probe.Dispose()
  }
}

Write-SupervisorLog "Inventory web supervisor started; port=$Port; node=$NodeExe"
while ($true) {
  if (Test-InventoryPort) {
    Start-Sleep -Seconds 15
    continue
  }

  Write-SupervisorLog "Port $Port is not listening; starting server.js."
  try {
    $ServerPath = Join-Path $Root "server.js"
    $Child = Start-Process -FilePath $NodeExe -ArgumentList @($ServerPath) -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog -PassThru
  } catch {
    Write-SupervisorLog "Server start failed: $($_.Exception.Message)"
    Start-Sleep -Seconds 15
    continue
  }

  Write-SupervisorLog "Started node PID $($Child.Id)."
  $PortReady = $false
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-InventoryPort) {
      $PortReady = $true
      break
    }
    if ($Child.HasExited) {
      break
    }
    Start-Sleep -Milliseconds 250
  }
  if ($PortReady) {
    while (-not $Child.HasExited) {
      Start-Sleep -Seconds 5
    }
  }
  Write-SupervisorLog "Node PID $($Child.Id) exited or failed to bind; retrying in 5 seconds."
  Start-Sleep -Seconds 5
}
} finally {
  if ($MutexAcquired) {
    $Mutex.ReleaseMutex()
  }
  $Mutex.Dispose()
}

param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$StateRoot
)
$ErrorActionPreference = 'Stop'
$taskName = 'ProtoKboM0Forward-v1'
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
$StateRoot = (Resolve-Path -LiteralPath $StateRoot).Path
$entryPoint = Join-Path $RuntimeRoot 'research/kbo_forward/scheduled_run.ps1'
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) { throw 'Pinned scheduler entry point is missing' }
$description = 'KBO paired M0 research only; direct sources; no wagering or production changes; PC awake and user logged in required.'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { throw 'Collector task already exists; preserve it and inspect its pinned revision before any explicit update' }
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $entryPoint + '" -PythonExe "' + $PythonExe + '" -RuntimeRoot "' + $RuntimeRoot + '" -StateRoot "' + $StateRoot + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $RuntimeRoot
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,Description

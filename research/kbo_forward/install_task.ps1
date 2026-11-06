param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$StateRoot,
    [string]$M1StateRoot = ''
)
$ErrorActionPreference = 'Stop'
$taskName = 'ProtoKboM0Forward-v1'
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
$StateRoot = (Resolve-Path -LiteralPath $StateRoot).Path
$entryPoint = Join-Path $RuntimeRoot 'research/kbo_forward/scheduled_run.py'
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) { throw 'Pinned scheduler entry point is missing' }
$PythonwExe = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $PythonwExe -PathType Leaf)) { throw 'The selected Python installation is missing pythonw.exe' }
$description = 'KBO paired M0 research only; direct sources; no wagering or production changes; PC awake and user logged in required.'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { throw 'Collector task already exists; preserve it and inspect its pinned revision before any explicit update' }
$arguments = '-B -X utf8 "' + $entryPoint + '" --python "' + $PythonExe + '" --runtime "' + $RuntimeRoot + '" --state "' + $StateRoot + '"'
if ($M1StateRoot) {
    $M1StateRoot = [System.IO.Path]::GetFullPath($M1StateRoot)
    $arguments += ' --m1-state "' + $M1StateRoot + '"'
}
$action = New-ScheduledTaskAction -Execute $PythonwExe -Argument $arguments -WorkingDirectory $RuntimeRoot
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,Description

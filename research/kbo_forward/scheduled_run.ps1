param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$StateRoot,
    [string]$M1StateRoot = ''
)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $RuntimeRoot 'research/kbo_forward/runner.py'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw 'Pinned collector runtime is missing' }
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
# Task Scheduler owns repetition and rejects concurrent instances; Python also
# holds an OS lock. Only the last process log is replaced, never the ledger.
$collectorArguments = @('-B', '-X', 'utf8', $scriptPath, '--state', $StateRoot)
if ($M1StateRoot) { $collectorArguments += @('--m1-state', $M1StateRoot) }
& $PythonExe @collectorArguments *> (Join-Path $StateRoot 'last-process.log')
exit $LASTEXITCODE

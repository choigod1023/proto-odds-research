param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$StateRoot
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
& $PythonExe -B -X utf8 -m scripts.nba_forward_run `
    --db (Join-Path $StateRoot 'nba-forward.sqlite3') `
    --model-config (Join-Path $StateRoot 'runtime/model.json') `
    --report-file (Join-Path $StateRoot 'comparison.json') `
    *> (Join-Path $StateRoot 'last-process.log')
exit $LASTEXITCODE

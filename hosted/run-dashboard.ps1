$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $ProjectRoot
$LogDirectory = Join-Path $env:LOCALAPPDATA 'Courtside'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -u -B -m dashboard.server --port 8765 --host 127.0.0.1 *> (Join-Path $LogDirectory 'dashboard.log')
exit $LASTEXITCODE

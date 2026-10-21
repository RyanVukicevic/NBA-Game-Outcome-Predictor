$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $ProjectRoot
$env:ODDS_API_KEY = [Environment]::GetEnvironmentVariable('ODDS_API_KEY', 'User')
$CredentialPath = Join-Path $env:LOCALAPPDATA 'Courtside\odds-api-key.dpapi'
if (-not $env:ODDS_API_KEY -and (Test-Path -LiteralPath $CredentialPath)) {
    $SecureKey = Get-Content -LiteralPath $CredentialPath -Raw | ConvertTo-SecureString
    $Credential = New-Object System.Management.Automation.PSCredential('odds-api', $SecureKey)
    $env:ODDS_API_KEY = $Credential.GetNetworkCredential().Password
}
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -B -m hosted.local_worker
exit $LASTEXITCODE

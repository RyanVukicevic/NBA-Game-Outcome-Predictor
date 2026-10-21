$ErrorActionPreference = 'Stop'
$SecureKey = Read-Host 'Paste your current Odds API key (hidden)' -AsSecureString
if ($SecureKey.Length -eq 0) { throw 'No key supplied.' }
$CredentialDirectory = Join-Path $env:LOCALAPPDATA 'Courtside'
New-Item -ItemType Directory -Path $CredentialDirectory -Force | Out-Null
$SecureKey | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $CredentialDirectory 'odds-api-key.dpapi')
Write-Host 'Key saved encrypted for your Windows account. The next Courtside task will use it.'

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-east1",
    [Parameter(Mandatory = $true)][string]$CloudflareAccountId,
    [string]$R2Bucket = "courtside-state",
    [string]$PagesProject = "courtside-nba"
)

$ErrorActionPreference = "Stop"
$Service = "courtside-worker"
$Repository = "courtside"
$RuntimeAccount = "courtside-runtime@$ProjectId.iam.gserviceaccount.com"
$SchedulerAccount = "courtside-scheduler@$ProjectId.iam.gserviceaccount.com"
$Image = "$Region-docker.pkg.dev/$ProjectId/$Repository/worker:latest"
$Secrets = @(
    "courtside-odds-api-key",
    "courtside-r2-access-key-id",
    "courtside-r2-secret-access-key",
    "courtside-cf-api-token"
)

$GcloudCommand = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
$Gcloud = if ($GcloudCommand) {
    $GcloudCommand.Source
} else {
    Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
}
if (-not (Test-Path -LiteralPath $Gcloud)) {
    throw "Install and authenticate the Google Cloud CLI first."
}

function Invoke-RequiredGcloud {
    & $script:Gcloud @args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud command failed: gcloud $($args -join ' ')"
    }
}

$ActiveAccount = & $Gcloud auth list --filter "status:ACTIVE" --format "value(account)"
if ($LASTEXITCODE -ne 0 -or -not $ActiveAccount) {
    throw "Authenticate first with: & `"$Gcloud`" auth login"
}

Invoke-RequiredGcloud config set project $ProjectId --quiet
Invoke-RequiredGcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com --quiet

& $Gcloud artifacts repositories describe $Repository --location $Region 2>$null
if ($LASTEXITCODE -ne 0) {
    Invoke-RequiredGcloud artifacts repositories create $Repository --repository-format docker --location $Region --quiet
}

foreach ($name in @("courtside-runtime", "courtside-scheduler")) {
    & $Gcloud iam service-accounts describe "$name@$ProjectId.iam.gserviceaccount.com" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Invoke-RequiredGcloud iam service-accounts create $name --display-name $name --quiet
    }
}

foreach ($secret in $Secrets) {
    & $Gcloud secrets describe $secret 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Create Secret Manager secret '$secret' before deploying." }
    Invoke-RequiredGcloud secrets add-iam-policy-binding $secret --member "serviceAccount:$RuntimeAccount" --role roles/secretmanager.secretAccessor --quiet
}

Invoke-RequiredGcloud builds submit --config hosted/cloudbuild.yaml --substitutions "_IMAGE=$Image" . --quiet
Invoke-RequiredGcloud run deploy $Service --image $Image --region $Region --service-account $RuntimeAccount `
    --no-allow-unauthenticated --concurrency 1 --max-instances 1 --timeout 600 --memory 1Gi `
    --set-env-vars "R2_ACCOUNT_ID=$CloudflareAccountId,R2_BUCKET=$R2Bucket,CLOUDFLARE_ACCOUNT_ID=$CloudflareAccountId,CLOUDFLARE_PAGES_PROJECT=$PagesProject,COURTSIDE_URL=https://$PagesProject.pages.dev" `
    --set-secrets "ODDS_API_KEY=courtside-odds-api-key:latest,R2_ACCESS_KEY_ID=courtside-r2-access-key-id:latest,R2_SECRET_ACCESS_KEY=courtside-r2-secret-access-key:latest,CLOUDFLARE_API_TOKEN=courtside-cf-api-token:latest" --quiet

$Url = Invoke-RequiredGcloud run services describe $Service --region $Region --format "value(status.url)"
Invoke-RequiredGcloud run services add-iam-policy-binding $Service --region $Region --member "serviceAccount:$SchedulerAccount" --role roles/run.invoker --quiet

& $Gcloud scheduler jobs describe courtside-five-minute --location $Region 2>$null
if ($LASTEXITCODE -eq 0) {
    Invoke-RequiredGcloud scheduler jobs update http courtside-five-minute --location $Region --schedule "*/5 * * * *" --time-zone UTC --http-method POST --uri "$Url/run" --oidc-service-account-email $SchedulerAccount --oidc-token-audience $Url --max-retry-attempts 0 --attempt-deadline 600s --quiet
} else {
    Invoke-RequiredGcloud scheduler jobs create http courtside-five-minute --location $Region --schedule "*/5 * * * *" --time-zone UTC --http-method POST --uri "$Url/run" --oidc-service-account-email $SchedulerAccount --oidc-token-audience $Url --max-retry-attempts 0 --attempt-deadline 600s --quiet
}

Write-Host "Courtside worker deployed at $Url (IAM protected)."

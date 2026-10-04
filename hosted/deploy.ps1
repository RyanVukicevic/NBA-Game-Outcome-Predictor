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

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "Install and authenticate the Google Cloud CLI first."
}

& gcloud config set project $ProjectId
& gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com

& gcloud artifacts repositories describe $Repository --location $Region 2>$null
if ($LASTEXITCODE -ne 0) {
    & gcloud artifacts repositories create $Repository --repository-format docker --location $Region
}

foreach ($name in @("courtside-runtime", "courtside-scheduler")) {
    & gcloud iam service-accounts describe "$name@$ProjectId.iam.gserviceaccount.com" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & gcloud iam service-accounts create $name --display-name $name
    }
}

foreach ($secret in $Secrets) {
    & gcloud secrets describe $secret 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Create Secret Manager secret '$secret' before deploying." }
    & gcloud secrets add-iam-policy-binding $secret --member "serviceAccount:$RuntimeAccount" --role roles/secretmanager.secretAccessor
}

& gcloud builds submit --config hosted/cloudbuild.yaml --substitutions "_IMAGE=$Image" .
& gcloud run deploy $Service --image $Image --region $Region --service-account $RuntimeAccount `
    --no-allow-unauthenticated --concurrency 1 --max-instances 1 --timeout 600 --memory 1Gi `
    --set-env-vars "R2_ACCOUNT_ID=$CloudflareAccountId,R2_BUCKET=$R2Bucket,CLOUDFLARE_ACCOUNT_ID=$CloudflareAccountId,CLOUDFLARE_PAGES_PROJECT=$PagesProject,COURTSIDE_URL=https://$PagesProject.pages.dev" `
    --set-secrets "ODDS_API_KEY=courtside-odds-api-key:latest,R2_ACCESS_KEY_ID=courtside-r2-access-key-id:latest,R2_SECRET_ACCESS_KEY=courtside-r2-secret-access-key:latest,CLOUDFLARE_API_TOKEN=courtside-cf-api-token:latest"

$Url = & gcloud run services describe $Service --region $Region --format "value(status.url)"
& gcloud run services add-iam-policy-binding $Service --region $Region --member "serviceAccount:$SchedulerAccount" --role roles/run.invoker

& gcloud scheduler jobs describe courtside-five-minute --location $Region 2>$null
if ($LASTEXITCODE -eq 0) {
    & gcloud scheduler jobs update http courtside-five-minute --location $Region --schedule "*/5 * * * *" --time-zone UTC --http-method POST --uri "$Url/run" --oidc-service-account-email $SchedulerAccount --oidc-token-audience $Url --max-retry-attempts 0 --attempt-deadline 600s
} else {
    & gcloud scheduler jobs create http courtside-five-minute --location $Region --schedule "*/5 * * * *" --time-zone UTC --http-method POST --uri "$Url/run" --oidc-service-account-email $SchedulerAccount --oidc-token-audience $Url --max-retry-attempts 0 --attempt-deadline 600s
}

Write-Host "Courtside worker deployed at $Url (IAM protected)."

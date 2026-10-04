# Free-Tier Deployment Runbook

This design keeps `https://courtside-nba.pages.dev` and does not require a paid
domain. Cloudflare Pages hosts the read-only PWA, Cloudflare R2 stores the private
compressed state, and an authenticated Google Cloud Run service is invoked by
Cloud Scheduler. Free tiers and provider terms can change; enable billing alerts
and verify each dashboard before relying on a $0 monthly total.

## 1. Local Prerequisites

- Install the Google Cloud CLI and authenticate. On Windows, use the `.cmd`
  launcher so a restrictive PowerShell execution policy does not block setup:

```powershell
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" auth login
```
- In Cloudflare, create an R2 bucket named `courtside-state`.
- Create an R2 API token restricted to object read/write for that bucket.
- Create a Cloudflare API token with Pages edit access for `courtside-nba`.
- Keep using the existing The Odds API key. Never paste any value into Git.

Install the complete environment:

```powershell
\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Set temporary local environment variables, then seed the current compact ledger,
pinned production artifact, manifest, and cached NBA history:

```powershell
$env:R2_ACCOUNT_ID = "YOUR_CLOUDFLARE_ACCOUNT_ID"
$env:R2_ACCESS_KEY_ID = "YOUR_R2_ACCESS_KEY_ID"
$env:R2_SECRET_ACCESS_KEY = "YOUR_R2_SECRET"
$env:R2_BUCKET = "courtside-state"
\.venv\Scripts\python.exe -B -m hosted.seed_state
```

The command uploads two private objects below `courtside/state/`. It does not
upload `.env`, the Odds API key, email credentials, or the uncompressed ledger.

## 2. Google Secrets

Create these Secret Manager secrets in the chosen Google project:

| Secret | Value |
|---|---|
| `courtside-odds-api-key` | The Odds API key |
| `courtside-r2-access-key-id` | R2 access key ID |
| `courtside-r2-secret-access-key` | R2 secret access key |
| `courtside-cf-api-token` | Cloudflare Pages API token |

Optional free Gmail SMTP alerts use an app password, not the normal account
password. Add `courtside-smtp-user`, `courtside-smtp-password`, and
`courtside-alert-email`; use the same verified Gmail address for `SMTP_FROM`.
Resend is also supported, but general recipients require a verified sending
domain, so Gmail SMTP is the no-domain path.

## 3. Build And Deploy

Run the checked-in helper from PowerShell after the secrets exist:

```powershell
.\hosted\deploy.ps1 `
  -ProjectId "YOUR_GCP_PROJECT" `
  -CloudflareAccountId "YOUR_CLOUDFLARE_ACCOUNT_ID" `
  -R2Bucket "courtside-state"
```

The helper enables the required APIs, creates Artifact Registry and least-
privilege service accounts, builds `hosted/Dockerfile`, deploys an authenticated
Cloud Run service with concurrency/max instances set to one, and creates one
five-minute Cloud Scheduler job with OIDC. It does not create secrets or reveal
their values.

The worker runs the existing scheduler policy. It checkpoints the ledger to R2
before Pages publication and republishes only when evidence changes or the site
is six hours old. Each run appears in **System** with status and next wake time.

## 4. Email Alerts

For Gmail SMTP, add these Cloud Run environment variables/secrets after the base
deployment:

```text
ALERT_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_FROM=<same Gmail address>
SMTP_USER=<Secret Manager reference>
SMTP_PASSWORD=<Secret Manager reference>
ALERT_EMAIL=<Secret Manager reference>
```

Only an official locked `model_value` selection generates an alert. Refreshes,
previews, favorites, and generic promotions do not. The deterministic delivery
key prevents a successful decision alert from sending twice.

## 5. Private Access Without A Domain

Cloudflare supports Access on the production `pages.dev` hostname. In **Workers
& Pages > courtside-nba > Settings**, enable an Access policy. In Zero Trust,
limit the application to the desired email address and use a one-time PIN or an
enabled identity provider. Cloudflare's current Pages instructions require the
production hostname application to omit the preview wildcard.

This gives the existing site a login gate; it is not a Courtside password
database. Theme, timezone, bookmaker, and layout preferences remain local to the
browser. The collector stays private behind Google IAM independently.

## 6. Verification

1. Run the Cloud Scheduler job manually once.
2. Confirm Cloud Run returns `status: success` and R2 object timestamps advance.
3. Open **System** and verify a successful heartbeat and published timestamp.
4. Confirm The Odds API usage did not change unless a registered slot was due.
5. Download the R2 ledger to a temporary path and run `PRAGMA integrity_check`.
6. Keep a monthly R2 object copy and test a restore before the season starts.

Configure a Google Cloud budget alert and a log-based alert for HTTP 5xx or a
missing successful run. Cloud Scheduler should use no automatic retry: the next
five-minute invocation is safer around an externally billed odds request.

## 7. Updating Code

CI runs on every push. After cloud identity federation is configured, the manual
`Deploy hosted worker` workflow builds and deploys the new revision. Re-seed the
runtime bundle only after intentionally rebuilding/promoting a production model;
ordinary dashboard, ledger, or scheduler changes do not change model identity.

Official references: [Cloud Run scheduled services](https://cloud.google.com/run/docs/triggering/using-scheduler),
[Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/), and
[Cloudflare Pages Access](https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain).

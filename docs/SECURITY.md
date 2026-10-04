# Security

## Supported Scope

Report a suspected credential exposure, unsafe archive behavior, unauthorized
write path, or data-integrity issue privately to the repository owner. Do not
include real API keys, email passwords, or sportsbook account information in an
issue, commit, screenshot, notebook, or database export.

## Controls

- Secrets are environment variables or Google Secret Manager versions.
- The Cloud Run `/run` endpoint requires IAM; Cloud Scheduler uses OIDC.
- The service runs with maximum one instance and concurrency one.
- R2 credentials are bucket-scoped read/write keys.
- Pages receives bounded JSON, never SQLite, model artifacts, `.env`, or secrets.
- Static responses set a restrictive content security policy and disable browser
  access to camera, microphone, and geolocation.
- Email delivery stores only a recipient hash and provider receipt in SQLite.
- Cloudflare Access can restrict the existing `pages.dev` hostname by email.
- Runtime archives are hashed and checked for path traversal before extraction.

## Deliberate Exclusions

There is no sportsbook login, payment processing, automated betting, public write
API, or browser-side secret. Do not add one without a separate threat model,
authorized provider contract, jurisdiction review, limits, and incident plan.

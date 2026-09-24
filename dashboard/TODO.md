# Dashboard Implementation Backlog

## Hosting And Operations

- Preferred hosted shape: Cloudflare Pages for the always-available static/PWA
  shell, Supabase Postgres/Auth for durable user and prediction data, and a
  scheduled Python container such as Google Cloud Run invoked by Cloud Scheduler.
  The worker must be idempotent and record a heartbeat. Confirm quotas, billing
  alerts, secrets, backups and restore behavior before calling it production.
- Deploy the Python dashboard and scheduled collector on an always-on free tier
  only after confirming scheduled jobs, persistent storage, and outbound API
  requests are supported. A static host alone is insufficient.
- Move SQLite to durable hosted storage or a managed database before relying on
  the service. Add backups, migrations, scheduler heartbeat, alerting, secrets,
  authentication, HTTPS, and recovery checks.
- Keep the Odds API quota guard and one account/project. Do not evade provider
  limits with extra accounts.
- Make the hosted dashboard installable as a Progressive Web App so it can live
  on the phone home screen with a mobile layout, HTTPS, cached shell, and clear
  offline/stale-data indicators. Native iOS/Android packaging is optional later.
- Add authenticated read-only remote access and notification preferences for
  locked decisions once hosting and user security are in place.
- Add transactional email notifications through a provider such as Resend for
  newly eligible model opportunities, official locked decisions, material line
  movement, injury-status changes and stale/missed collectors. Store per-user
  thresholds, quiet hours, bookmaker preferences and an auditable send log.
- Never describe generic sportsbook promotions as model betting value. Promotion
  ingestion requires a permitted source, exact eligibility/expiry terms and a
  separate alert type; do not scrape authenticated sportsbook pages.

## Prospective Betting Research

- Run the locked DraftKings T-60 policy for the entire season. Pre-register any
  additional timing cohorts, such as T-6h, rather than choosing one after seeing
  which was most profitable.
- Keep flat $10 paper stakes as the primary strategy comparison. Report sample
  counts and uncertainty; do not declare a winning strategy from a short streak.
- Research capped fractional Kelly only after enough prospective calibration data
  exists. Version bankroll assumptions, cap per-bet and daily exposure, and add
  stop rules. Keep it paper-only until separately reviewed.
- Add manual bet-receipt entry and reconciliation if real personal wagers need to
  be compared with paper prices and actual fills.

## Data And Modeling

- Add a Calibration view with raw-versus-candidate reliability curves, Brier and
  log-loss trends, ECE with sample counts, calibration slope/intercept, confidence
  coverage, season/phase slices and prospective drift alerts. Keep the September
  24 beta calibrator as a frozen challenger until future games justify promotion.
- Continue the separate player-availability research: roster identity, injuries,
  expected minutes, lineup strength, missing-player impact, and source licensing.
- When player data is validated, show each matchup's timestamped status, projected
  rotation, expected minutes with uncertainty, player-strength contribution,
  replacement allocation, net lineup-strength difference and model probability
  change. Unknown must remain distinct from available; assumptions belong in a
  labeled scenario tool and never overwrite the official forecast.
- Monitor calibration drift, bookmaker coverage, team aliases, postponed games,
  odds outliers, missing snapshots, and schedule changes.
- Add confidence intervals and minimum-sample warnings to strategy performance.

## Sportsbook Integration Boundary

- Do not scrape sportsbook account pages, automate login, or place bets with a
  browser bot. DraftKings terms restrict automated scripts and automated betting.
- Consider execution only if a sportsbook supplies an official authorized API,
  grants written access for this use, and all jurisdiction, identity, geolocation,
  responsible-gaming, security, and audit requirements are satisfied.
- Until then, keep the product as decision support and paper tracking with a human
  making every real-money decision.
- Kalshi and Polymarket US expose official trading APIs for their event-contract
  exchanges. Treat any future connector as a separate, opt-in execution service
  with contract matching, jurisdiction checks, limits, kill switches, idempotency,
  reconciliation and paper mode first. Never reuse sportsbook credentials.

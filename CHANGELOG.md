# Changelog

## Unreleased

### Added

- Courtside responsive dashboard, dark default, card/table matchup views, Elo
  comparisons, forecast explanations, prospective performance, and system health.
- Cloudflare Pages static export, PWA manifest/icons, offline shell, and shareable
  matchup routes.
- SQLite schema v4 worker heartbeats and idempotent notification delivery audit.
- Cloud Run worker scaffold with verified R2 state, bounded Pages publication,
  quota-aware scheduling, and optional SMTP/Resend locked-decision alerts.
- Explicit prediction-source identity, architecture/methodology/data/security
  documentation, and automated core test workflow.

### Changed

- Eligibility history uses deduplicated blocker sets, cutting the audited ledger
  from about 127 MB to 67 MB without changing dashboard output.
- Hosted snapshots preserve scheduler health instead of labeling every deployment
  as a local-only dashboard.
- Dashboard reads are bounded to the 90-day product window; indexed eligibility
  lookups and cached direct Elo replay keep local and static exports responsive.

### Boundaries

- Cloud resources and secrets still require one-time owner configuration.
- Player availability and alternative calibration remain research challengers.
- No wager execution is implemented.

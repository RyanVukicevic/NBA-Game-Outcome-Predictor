# Implementation Progress

## Milestone 1: Forecast Foundation

Implemented:

- Separate historical evaluation and full-history deployment estimators.
- Whole-date holdout boundary and purged date boundary for temporal CV.
- Explicit daily data cutoff, paired final-result validation, and rejection of
  ambiguous duplicate/same-day team histories.
- Post-result rolling prediction inputs, actual date/schedule-derived rest,
  labeled sparse-history fallback, and new-season Elo regression.
- Elo processes the full completed-game sequence before rolling warmup filtering.
- Content-addressed processed caches; source/settings/runtime/model manifests.
- Prediction history updates without refitting model weights.
- Saved forecast input vectors, model archives and data snapshot artifacts.
- CLI cutoff/game-date/output options; demo provenance inspection.

Verification covers cutoff exclusion, latest-game inclusion, full-data deployment
fit, independent evaluation counts, save/load reproducibility, cache invalidation,
snapshot refresh without weight changes, Elo warmup/season rollover, and schedule
boundary/status handling. See tests/test_forecast_foundation.py and
tests/test_production_schedule.py.

Verified September 13, 2026: all 15 unit tests pass; all 13 demo code cells
execute without errors. The live run saved 333 forecasts, and every probability
was reproduced from its archived deployment estimator and recorded input vector.
The demo's evaluation estimator trained on 3,106 games; deployment used 3,884.

Current boundaries:

- Daily source logs have no first-available timestamps. Same-day results are
  excluded; historical replay does not establish intraday availability.
- Download refresh is explicit. Scheduled ingestion belongs to Milestones 2-3.
- Long-range rolling inputs remain a snapshot; rest can use intervening scheduled
  games but their outcomes are not invented. Sparse new-season history is labeled.
- Preseason/all-star/play-in IDs are excluded unless supported by the configured
  training scope. Legacy schedules without IDs still use label fallback.
- No odds provider subscription, SQL ledger, automatic wagering, or website is
  implemented in this milestone. Original settings remain the reference.
- Existing experiment scores predate these corrections and must be rerun before
  comparing against corrected models.

Next: Milestone 2, persistent games/model/prediction/result records with immutable
forecast snapshots, idempotent writes, and fixed-horizon evaluation selection.

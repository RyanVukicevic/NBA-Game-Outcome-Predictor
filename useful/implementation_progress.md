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

Follow-on work is recorded in Milestone 2 below.

## Milestone 2: Pregame Ledger And Paper Comparisons

Implemented:

- Versioned SQLite schema for stable NBA games, observed schedule revisions,
  model metadata, immutable forecasts, bookmaker quotes, final-result revisions,
  policy-specific decisions, and separate actual wager/settlement receipts.
- Production forecasts automatically enter the ledger; historic archive imports
  retain present-day receipt times and cannot invent historical availability.
- Read-only The Odds API v4 NBA moneyline adapter with secret-safe errors, quota
  reporting, exact event matching, and unavailable-market observations.
- Pinned-model/bookmaker policies with T-60m, $10 stake, 10-minute odds freshness,
  24-hour prediction freshness, and a configurable unvalidated 3% EV threshold.
- Favorite/home/model-winner/model-value comparisons on a shared eligible cohort,
  explicit skips, pending results, conservative schedule-change voids, and no
  repeat entries for the same policy/game.
- Paper profit/ROI/drawdown/season/month reports, model and no-vig market
  calibration/Brier/log-loss, and game-day bootstrap ROI intervals.
- CLI receipt import, odds collection, decision selection, result reconciliation,
  schedule-status updates, and report exports. No order execution.
- Demo ledger inspection and isolated synthetic 30-game accounting walkthrough.

Verification: 36 unit tests passed, including prior forecast foundation tests.
All 15 notebook code cells executed successfully. Two production runs retained
666 forecast snapshots across 333 games; re-importing the latest artifact left
the row count unchanged. All 333 latest probabilities reproduced from their
archived model and inputs. CLI reporting and the synthetic chart were verified;
`pip check` found no broken requirements.
Initial odds testing used mocked responses. Subsequent live verification used
two read-only API calls (498 credits remained) and revealed provider expected
start times commonly 10 minutes after NBA scheduled starts. The adapter now
requires a unique team-pair match within 15 minutes, retains both timestamps,
and keeps the NBA cutoff. It rejects quotes after either start time. Replaying
the saved response matched 36 games and 41 markets (36 DraftKings, 5 FanDuel).
Five Christmas events were outside the current 90-day forecast window.
All 40 tests pass, including offset/ambiguity/pregame-boundary checks. No key was
saved to source, no subscription purchased, and no actual wager attempted.

Boundaries and next work:

- Collection is command-driven; managed scheduling and deployment remain next.
- Automated postponement/cancellation detection is not implemented. Explicit
  status updates and forecast-observed tipoff changes are supported.
- This is prospective paper research, not a historical-odds backfill or proof of
  profitability. Quoted prices are not guaranteed accepted wagers.
- No NFL/UFC model, live betting, staking optimizer, or automatic execution.
- The original ideas.txt user edits remain untouched. Operational details and
  agreed pregame/favorite benchmark scope are in useful/tracking_guide.md.

## Milestone 3: Eligibility And Free-Plan Scheduling

Implemented:

- Non-destructive SQLite v2 migration; version-2 paper policies enforce both
  teams' readiness using schedule/results/forecast data available at cutoff.
- Full in-scope schedule import includes past and ongoing games, not just future
  forecasts. Explicit canceled/postponed source labels are recorded.
- Feature snapshot membership and scores, per-team incorporated game references,
  blockers, and first observed eligibility per model/schedule version.
- American-odds conversion in notebook displays and paper CSV exports, without
  another API call. Moneylines remain the only collected betting market.
- Shared manual/scheduler quota reservations: 12 credits per Eastern day, 450
  per calendar month, 50-credit reserve. User confirmed first-of-month 00:00 UTC
  resets; automatic rollover preserves the same-Eastern-day cap.
- Batched T-65m/T-365m collection for T-60m/T-6h paper decisions; qualifying quote
  reuse, primary-slot priority, one automatic attempt per slot, and missed-window
  records. No post-cutoff backfilling.
- CLI dry-run, one-pass execution, and optional minute-check watch loop; shared
  worker lease prevents overlap. Missing key/quota configuration prevents startup.
- Daily/near-window NBA data refresh, pinned weights/model identity, current-season
  history rollover, raw snapshot archives, and no automatic model replacement.

Verification uses offline collector fixtures rather than paid calls. The real
database quota was initialized to the last known two used credits; provider
headers reconcile external account use on subsequent requests. No Odds API
credits, subscription purchases, or wagers were used during this phase.

Known data limitation surfaced by the new guard: five neutral/ambiguous-home
2025-26 games are excluded by the existing paired home/away pipeline, blocking
six opening matchups. These are visibly awaiting data, not silently approved.
IDs and details are in the tracking guide. Neutral-site feature/Elo handling
needs a separate model/data correction. The scheduler is not an installed OS
service and has not been started with a stored API credential.

Final verification: 69 tests passed; all 15 demo code cells executed with no
errors and current source/model identities matched. The real notebook exported
41 American/decimal quote rows and showed 8 eligible, 6 awaiting-data, and 1,186
waiting future games. Schema v2 migration preserved existing records. CLI
scheduler preview returned no work due; the request journal remained empty.
`pip check` passed. The free-plan guard is configured for 2026-10-01 00:00 UTC
with two previously used credits as its initial baseline.
